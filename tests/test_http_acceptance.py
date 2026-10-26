"""Black-box acceptance against a real isolated Uvicorn process.

Run: python -m unittest discover -s tests -v
These tests create scripted client identities, not actual model participants.
"""
import concurrent.futures
import hashlib
import http.server
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
import uuid

from scripts.agent_client import ClientError, resolve_base_url, validate_base_url

ROOT = Path(__file__).resolve().parents[1]


class HttpAcceptance(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='gongxi-http-')
        self._servers = []
        self._logs = []
        self.addCleanup(self.cleanup_temp)
        self.log = open(Path(self.temp.name) / 'server.log', 'a', encoding='utf-8')
        self._logs.append(self.log)
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            self.port = sock.getsockname()[1]
        self.base = f'http://127.0.0.1:{self.port}'
        self.process = None
        self.addCleanup(self.close_logs)
        self.addCleanup(self.stop_server)
        self.start_server()

    def start_server(self):
        self.process = self.start_managed_server(self.temp.name, self.port, self.log, 'primary')
        self.base = self.process['base']

    def start_managed_server(self, data_dir, port, log, label):
        resolved_data_dir = Path(data_dir).resolve()
        base = f'http://127.0.0.1:{port}'
        instance_id = str(uuid.uuid4())
        management_token = secrets.token_urlsafe(32)
        identity = {
            'instance_id': instance_id,
            'project_id': hashlib.sha256(str(ROOT.resolve()).encode('utf-8')).hexdigest()[:24],
            'data_dir_id': hashlib.sha256(str(resolved_data_dir).encode('utf-8')).hexdigest()[:24],
        }
        env = os.environ.copy()
        env['CS_DATA_DIR'] = str(resolved_data_dir)
        env['GONGXI_INSTANCE_ID'] = instance_id
        env['GONGXI_MANAGEMENT_TOKEN'] = management_token
        env['PYTHONIOENCODING'] = 'utf-8'
        handle = {
            'base': base, 'port': port, 'data_dir': resolved_data_dir,
            'identity': identity, 'management_token': management_token,
            'label': label, 'log': log, 'stopped': False,
        }
        process = subprocess.Popen(
            [sys.executable, str(ROOT / 'launch.py'), '--port', str(port), '--data-dir', str(resolved_data_dir), '--no-browser'],
            cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
        handle['process'] = process
        self._servers.append(handle)
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if process.poll() is not None:
                self.fail(f'{label} service exited during startup. ' + self.log_text(log))
            try:
                health = self.read_health(base)
                if health.get('ok'):
                    self.assert_managed_identity(handle, health)
                    return handle
            except (OSError, urllib.error.URLError, json.JSONDecodeError):
                pass
            time.sleep(.05)
        self.fail(f'{label} service startup timeout. ' + self.log_text(log))

    def start_secondary_server(self):
        """Start a second isolated Uvicorn instance to test CLI target selection."""
        data_dir = Path(self.temp.name) / 'secondary-data'
        data_dir.mkdir()
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        base = f'http://127.0.0.1:{port}'
        log = open(Path(self.temp.name) / 'secondary-server.log', 'a', encoding='utf-8')
        self._logs.append(log)
        handle = self.start_managed_server(data_dir, port, log, 'secondary')
        return handle['base']

    @staticmethod
    def read_health(base):
        with urllib.request.urlopen(base + '/api/health', timeout=1) as response:
            return json.load(response)

    def assert_managed_identity(self, handle, health):
        for key, expected in handle['identity'].items():
            self.assertEqual(health.get(key), expected, f"{handle['label']} health {key} mismatch")
        self.assertTrue(health.get('ok'))

    def server_log(self):
        self.log.flush()
        return (Path(self.temp.name) / 'server.log').read_text(encoding='utf-8', errors='replace')[-5000:]

    @staticmethod
    def log_text(log):
        log.flush()
        return Path(log.name).read_text(encoding='utf-8', errors='replace')[-5000:]

    def stop_server(self):
        errors = []
        for handle in reversed(self._servers):
            if handle['stopped']:
                continue
            try:
                self.stop_managed_server(handle)
            except Exception as error:
                errors.append(f"{handle['label']}: {error}")
        if errors:
            raise RuntimeError('Could not confirm all real services stopped; temporary data is retained. ' + ' | '.join(errors))
        self.process = None

    def stop_managed_server(self, handle):
        base = handle['base']
        try:
            health = self.read_health(base)
        except (OSError, urllib.error.URLError):
            health = None
        if health is not None:
            self.assert_managed_identity(handle, health)
            payload = json.dumps(handle['identity']).encode('utf-8')
            request = urllib.request.Request(
                base + '/api/runtime/stop', data=payload, method='POST',
                headers={
                    'Accept': 'application/json',
                    'Content-Type': 'application/json',
                    'X-Gongxi-Management-Token': handle['management_token'],
                })
            try:
                with urllib.request.urlopen(request, timeout=5) as response:
                    result = json.load(response)
                if not result.get('ok'):
                    raise RuntimeError('runtime/stop did not confirm shutdown')
            except urllib.error.HTTPError as error:
                raise RuntimeError(f'runtime/stop rejected the managed shutdown: HTTP {error.code}') from error
            except urllib.error.URLError:
                # Shutdown can close the connection after accepting the request;
                # process and listener checks below determine whether it worked.
                pass
        process = handle['process']
        if process.poll() is None:
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                # Fallback is bound to this Popen handle, never a PID from health/logs.
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)

        deadline = time.monotonic() + 8
        while self.port_is_listening(handle['port']) and time.monotonic() < deadline:
            try:
                health = self.read_health(base)
            except (OSError, urllib.error.URLError):
                health = None
            if health is not None:
                self.assert_managed_identity(handle, health)
            time.sleep(.1)
        if self.port_is_listening(handle['port']):
            raise RuntimeError('listener remains active after managed shutdown and Popen fallback; ' + self.log_text(handle['log']))
        if process.poll() is None:
            raise RuntimeError('Popen wrapper did not exit after managed shutdown')
        handle['stopped'] = True

    @staticmethod
    def port_is_listening(port):
        with socket.socket() as sock:
            sock.settimeout(.25)
            return sock.connect_ex(('127.0.0.1', port)) == 0

    def close_logs(self):
        for log in self._logs:
            if not log.closed:
                log.close()

    def cleanup_temp(self):
        active = [handle['label'] for handle in self._servers if not handle['stopped']]
        if active:
            raise RuntimeError(f"Retaining temporary data at {self.temp.name}; service stop was not confirmed for: {', '.join(active)}")
        self.temp.cleanup()

    def request(self, method, path, data=None, token=None, expect=None, headers=None):
        hdr = {'Accept': 'application/json'}
        if token: hdr['Authorization'] = 'Bearer ' + token
        if headers: hdr.update(headers)
        body = None
        if data is not None:
            body = json.dumps(data, ensure_ascii=False).encode('utf-8')
            hdr['Content-Type'] = 'application/json'
        req = urllib.request.Request(self.base + path, data=body, headers=hdr, method=method)
        try:
            response = urllib.request.urlopen(req, timeout=5)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            status = response.status
            raw = response.read()
            try:
                parsed = json.loads(raw)
            except ValueError:
                self.fail(f'{method} {path} returned HTTP {status} non-JSON: {raw[:300]!r}. Server log: {self.server_log()}')
        if expect is not None:
            accepted = (expect,) if isinstance(expect, int) else expect
            self.assertIn(status, accepted, f'{method} {path} status={status}, body={parsed}')
        if status >= 400:
            self.assertIsInstance(parsed.get('detail'), str, 'Errors must have a string detail without traceback.')
            self.assertNotIn('Traceback', json.dumps(parsed))
        return status, parsed

    def ok(self, method, path, data=None, token=None):
        return self.request(method, path, data, token, expect=(200, 201))[1]

    def initialize(self):
        self.assertEqual(self.ok('GET', '/api/status'), {'initialized': False})
        result = self.ok('POST', '/api/setup', {'name': '组织者', 'workspace_name': '跨产品协作验收'})
        self.owner = result['token']
        self.owner_id = result['member']['id']
        self.assertEqual(result['member']['kind'], 'human')
        self.assertEqual(result['member']['role'], 'owner')
        self.assertGreaterEqual(len(self.owner), 32)
        self.assertEqual(self.ok('GET', '/api/status'), {'initialized': True})
        self.request('POST', '/api/setup', {'name': 'other', 'workspace_name': 'other'}, expect=409)
        return result

    def join(self, name, kind='ai'):
        invite = self.ok('POST', '/api/invites', {'kind': kind, 'expires_hours': 1}, self.owner)
        member = self.ok('POST', '/api/join', {'code': invite['code'], 'name': name, 'kind': kind, 'description': 'HTTP scripted test identity'})
        return invite, member

    def test_complete_collaboration_restart_and_revoke(self):
        self.initialize()
        invite_a, a = self.join('客户端 A')
        invite_b, b = self.join('客户端 B')
        at, bt = a['token'], b['token']
        aid, bid = a['member']['id'], b['member']['id']
        self.assertNotEqual(at, bt)
        self.request('POST', '/api/join', {'code': invite_a['code'], 'name': '重复使用', 'kind': 'ai'}, expect=(400, 403, 409))
        self.request('GET', '/api/state', expect=401)
        self.request('GET', '/api/state', token='invalid-token', expect=401)
        self.request('POST', '/api/invites', {'kind': 'ai'}, at, expect=403)
        initial = self.ok('GET', '/api/state', token=at)
        cursor = initial['cursor']
        self.assertEqual(initial['direction']['revision'], 0)
        self.assertEqual(initial['acknowledgements'], [])
        topic = self.ok('POST', '/api/topics', {'title': ' 接入协议讨论 ', 'description': '通用 HTTP'}, at)
        self.assertEqual(topic['title'], '接入协议讨论')
        payload = {'topic_id': topic['id'], 'body': '建议使用通用 HTTP 协作协议', 'idempotency_key': 'proposal-1'}
        m1 = self.ok('POST', '/api/messages', payload, at)
        after_post = self.ok('GET', '/api/state', token=at)['cursor']
        self.assertEqual(self.ok('POST', '/api/messages', payload, at)['id'], m1['id'])
        self.assertEqual(self.ok('GET', '/api/state', token=at)['cursor'], after_post, 'Idempotent retry must not emit an event.')
        self.request('POST', '/api/messages', dict(payload, body='different'), at, expect=409)
        m2 = self.ok('POST', '/api/messages', {'topic_id': topic['id'], 'body': '同意先验证跨环境恢复', 'reply_to': m1['id']}, bt)
        self.assertEqual(m2['reply_to'], m1['id'])
        self.assertEqual(m2['author_id'], bid)
        other = self.ok('POST', '/api/topics', {'title': '另一个议题'}, self.owner)
        self.request('POST', '/api/messages', {'topic_id': other['id'], 'body': 'wrong reply', 'reply_to': m1['id']}, bt, expect=(400, 409, 422))
        # Paginated incremental events preserve order and deliver every success.
        events = []
        while True:
            page = self.ok('GET', f'/api/events?after={cursor}&limit=1', token=bt)
            events.extend(page['events'])
            if page['events']:
                self.assertGreater(page['cursor'], cursor)
                cursor = page['cursor']
            if not page['has_more']: break
        ids = [event['id'] for event in events]
        self.assertEqual(ids, sorted(set(ids)))
        self.assertTrue({m1['id'], m2['id'], topic['id']}.issubset({event['entity_id'] for event in events}))
        self.assertEqual(self.ok('GET', f'/api/events?after={cursor}', token=at)['events'], [])
        self.request('POST', '/api/direction', {'body': 'unauthorized', 'expected_revision': 0}, at, expect=403)
        d1 = self.ok('POST', '/api/direction', {'body': '先验证接入，再推进实现', 'expected_revision': 0}, self.owner)
        self.assertEqual(d1['revision'], 1)
        self.assertEqual(self.ok('GET', '/api/state', token=at)['acknowledgements'], [], 'Publishing must never acknowledge on behalf of participants.')
        ack = self.ok('POST', '/api/direction/ack', {'revision': 1}, at)
        self.assertEqual(ack['member_id'], aid)
        self.assertEqual(self.ok('POST', '/api/direction/ack', {'revision': 1}, at), ack)
        self.request('POST', '/api/direction', {'body': 'stale change', 'expected_revision': 0}, self.owner, expect=409)
        self.ok('POST', '/api/direction', {'body': '接入完成后需要人类复核', 'expected_revision': 1}, self.owner)
        self.request('POST', '/api/direction/ack', {'revision': 1}, bt, expect=409)
        current = self.ok('GET', '/api/state', token=bt)
        self.assertFalse(any(item['revision'] == 2 for item in current['acknowledgements']))
        self.ok('POST', '/api/direction/ack', {'revision': 2}, bt)
        task = self.ok('POST', '/api/tasks', {'title': '验证通用 HTTP', 'topic_id': topic['id']}, at)
        self.assertEqual(task['status'], 'open')
        self.request('PATCH', '/api/tasks/' + task['id'], {'status': 'done'}, at, expect=(400, 403, 409, 422))
        # Real concurrent HTTP requests compete for the same unassigned task.
        def claim(identity):
            return self.request('PATCH', '/api/tasks/' + task['id'], {'assignee_id': identity['member']['id'], 'status': 'in_progress'}, identity['token'])
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(claim, [a, b]))
        self.assertEqual(sum(status in (200, 201) for status, _ in outcomes), 1, outcomes)
        winner = next(body['assignee_id'] for status, body in outcomes if status in (200, 201))
        winning_token = at if winner == aid else bt
        losing_token = bt if winner == aid else at
        losing_id = bid if winner == aid else aid
        self.request('PATCH', '/api/tasks/' + task['id'], {'assignee_id': losing_id}, losing_token, expect=(403, 409))
        completed = self.ok('PATCH', '/api/tasks/' + task['id'], {'status': 'done'}, winning_token)
        self.assertEqual(completed['status'], 'done')
        self.request('POST', '/api/decisions', {'title': '不应成功', 'body': '普通成员不能记录正式决定'}, at, expect=403)
        decision = self.ok('POST', '/api/decisions', {'title': '采用通用 HTTP', 'body': '用真实服务验收协议', 'topic_id': topic['id']}, self.owner)
        self.assertEqual(decision['direction_revision'], 2)
        search = self.ok('GET', '/api/search?' + urllib.parse.urlencode({'q': '通用 HTTP'}), token=bt)
        self.assertTrue(any(item['id'] == m1['id'] for item in search['messages']))
        self.assertTrue(any(item['id'] == task['id'] for item in search['tasks']))
        self.assertTrue(any(item['id'] == decision['id'] for item in search['decisions']))
        self.request('GET', '/api/export', token=at, expect=403)
        export = self.ok('GET', '/api/export', token=self.owner)
        secretless = json.dumps(export)
        for secret in (self.owner, at, bt, invite_a['code'], invite_b['code']):
            self.assertNotIn(secret, secretless)
        before_restart = self.ok('GET', '/api/state', token=at)
        self.stop_server()
        self.start_server()
        restored = self.ok('GET', '/api/state', token=at)
        for key in ('workspace', 'topics', 'messages', 'tasks', 'decisions', 'direction', 'acknowledgements', 'cursor'):
            self.assertEqual(restored[key], before_restart[key], key + ' must survive restart')
        self.ok('POST', '/api/members/' + aid + '/revoke', {}, self.owner)
        self.request('GET', '/api/state', token=at, expect=(401, 403))
        self.request('POST', '/api/messages', {'topic_id': topic['id'], 'body': 'revoked'}, at, expect=(401, 403))
        final = self.ok('GET', '/api/state', token=bt)
        self.assertTrue(any(item['id'] == m1['id'] for item in final['messages']))
        self.assertTrue(next(item for item in final['members'] if item['id'] == aid)['revoked_at'])
        self.request('POST', '/api/members/' + self.owner_id + '/revoke', {}, self.owner, expect=(400, 403, 409))
        self.request('POST', '/api/members/' + bid + '/revoke', {}, bt, expect=403)

    def test_invalid_inputs_auth_and_failed_transactions(self):
        self.request('POST', '/api/setup', {'name': ' ', 'workspace_name': 'X'}, expect=(400, 422))
        self.assertEqual(self.ok('GET', '/api/status'), {'initialized': False})
        self.initialize()
        _, ai = self.join('客户端')
        token = ai['token']
        invite = self.ok('POST', '/api/invites', {'kind': 'ai'}, self.owner)
        self.request('POST', '/api/join', {'code': invite['code'], 'name': 'mismatch', 'kind': 'human'}, expect=(400, 403, 409, 422))
        self.ok('POST', '/api/join', {'code': invite['code'], 'name': 'right-kind', 'kind': 'ai'})
        state = self.ok('GET', '/api/state', token=token)
        cursor = state['cursor']
        cases = [
            ('POST', '/api/topics', {'title': ' '}),
            ('POST', '/api/topics', {'title': 'x' * 201}),
            ('POST', '/api/topics', {'title': 'ok', 'description': 'x' * 5001}),
            ('POST', '/api/messages', {'topic_id': 'missing', 'body': 'hello'}),
            ('POST', '/api/tasks', {'title': 'x', 'topic_id': 'missing'}),
            ('PATCH', '/api/tasks/missing', {'status': 'unknown'}),
            ('POST', '/api/direction/ack', {'revision': -1}),
            ('GET', '/api/events?after=-1', None),
            ('GET', '/api/events?after=oops', None),
            ('GET', '/api/events?limit=0', None),
        ]
        for method, path, body in cases:
            with self.subTest(path=path, body_size=len(str(body))):
                self.request(method, path, body, token, expect=(400, 404, 409, 422))
        self.assertEqual(self.ok('GET', '/api/state', token=token)['cursor'], cursor, 'Failed operations must not publish events.')
        topic = self.ok('POST', '/api/topics', {'title': 'input validation'}, token)
        for body in (' ', 'x' * 20001):
            self.request('POST', '/api/messages', {'topic_id': topic['id'], 'body': body}, token, expect=(400, 422))
        self.request('POST', '/api/topics', {'title': 'evil origin'}, token, headers={'Origin': 'https://evil.example'}, expect=403)
        self.ok('POST', '/api/topics', {'title': 'real origin'}, token)
        self.request('POST', '/api/topics', {'title': 'local same origin'}, token, headers={'Origin': self.base}, expect=(200, 201))
        for query in ("' OR 1=1 --", '*', '"', '%_'):
            result = self.ok('GET', '/api/search?' + urllib.parse.urlencode({'q': query}), token=token)
            self.assertEqual(set(result), {'topics', 'messages', 'tasks', 'decisions'})
        for path in ('/api/me', '/api/events', '/api/search?q=a', '/api/export'):
            self.request('GET', path, expect=401)

    def test_invite_consumption_is_atomic_and_kind_bound(self):
        self.initialize()
        invite = self.ok('POST', '/api/invites', {'kind': 'ai', 'expires_hours': 1}, self.owner)
        def consume(name):
            return self.request('POST', '/api/join', {'code': invite['code'], 'name': name, 'kind': 'ai'})
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(consume, ['并发加入 A', '并发加入 B']))
        self.assertEqual(sum(status in (200, 201) for status, _ in outcomes), 1, outcomes)
        self.assertTrue(all(status in (200, 201, 400, 403, 409) for status, _ in outcomes))
        state = self.ok('GET', '/api/state', token=self.owner)
        self.assertEqual(len(state['members']), 2, 'Only one member may consume a one-time invite.')
        human_invite = self.ok('POST', '/api/invites', {'kind': 'human'}, self.owner)
        self.request('POST', '/api/join', {'code': human_invite['code'], 'name': 'wrong kind', 'kind': 'ai'}, expect=(400, 403, 409, 422))
        human = self.ok('POST', '/api/join', {'code': human_invite['code'], 'name': '参与人类', 'kind': 'human'})
        self.assertEqual(human['member']['kind'], 'human')
        self.assertNotEqual(human['member']['role'], 'owner')
        expired = self.ok('POST', '/api/invites', {'kind': 'ai', 'expires_hours': 0.000001}, self.owner)
        time.sleep(.02)
        self.request('POST', '/api/join', {'code': expired['code'], 'name': 'too late', 'kind': 'ai'}, expect=(400, 403, 409))
        for value in (-1, 0):
            self.request('POST', '/api/invites', {'kind': 'ai', 'expires_hours': value}, self.owner, expect=(400, 422))
        self.request('POST', '/api/join', {'code': 'unknown', 'name': 'X', 'kind': 'ai'}, expect=(400, 403, 404, 409))

    def test_cli_join_read_post_claim_and_private_credentials(self):
        self.initialize()
        invite = self.ok('POST', '/api/invites', {'kind': 'ai'}, self.owner)
        path = Path(self.temp.name) / 'client.json'
        env = os.environ.copy()
        env.pop('CS_TOKEN', None)
        env['CS_INVITE_CODE'] = invite['code']
        env['PYTHONIOENCODING'] = 'utf-8'
        def cli(*args, success=True):
            completed = subprocess.run([sys.executable, str(ROOT / 'scripts' / 'agent_client.py'), '--base', self.base, *args], env=env, capture_output=True, text=True, encoding='utf-8', timeout=15)
            if success:
                self.assertEqual(completed.returncode, 0, completed.stderr)
                return json.loads(completed.stdout)
            self.assertNotEqual(completed.returncode, 0)
            return completed
        joined = cli('join', '--name', 'CLI 脚本身份', '--save-token', str(path))
        self.assertTrue(path.is_file())
        credentials = json.loads(path.read_text(encoding='utf-8'))
        self.assertNotIn(credentials['token'], json.dumps(joined))
        self.assertEqual(cli('--token-file', str(path), 'me')['member']['name'], 'CLI 脚本身份')
        topic = cli('--token-file', str(path), 'topic', '--title', 'CLI 议题')
        msg = cli('--token-file', str(path), 'post', '--topic', topic['id'], '--body', 'CLI 通用客户端发言', '--key', 'cli-1')
        self.assertEqual(cli('--token-file', str(path), 'post', '--topic', topic['id'], '--body', 'CLI 通用客户端发言', '--key', 'cli-1')['id'], msg['id'])
        task = cli('--token-file', str(path), 'task-create', '--title', 'CLI 验收')
        self.assertEqual(cli('--token-file', str(path), 'task', task['id'], '--claim', '--status', 'in_progress')['assignee_id'], credentials['member']['id'])
        self.assertEqual(cli('--token-file', str(path), 'task', task['id'], '--status', 'done')['status'], 'done')
        cli('--token-file', str(path), 'state')
        cli('--token-file', str(path), 'events', '--after', '0', '--limit', '1')
        cli('--token-file', str(path), 'search', 'CLI')
        # Refuse overwrite before consuming a new invitation.
        next_invite = self.ok('POST', '/api/invites', {'kind': 'ai'}, self.owner)
        env['CS_INVITE_CODE'] = next_invite['code']
        cli('join', '--name', 'should fail', '--save-token', str(path), success=False)
        self.ok('POST', '/api/join', {'code': next_invite['code'], 'kind': 'ai', 'name': 'still usable'})
        if os.name != 'nt':
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_cli_uses_bound_credential_target_before_environment(self):
        self.initialize()
        other_base = self.start_secondary_server()
        env = os.environ.copy()
        env['CS_BASE_URL'] = other_base
        env.pop('CS_TOKEN', None)
        env['PYTHONIOENCODING'] = 'utf-8'
        cli_path = str(ROOT / 'scripts' / 'agent_client.py')

        for target_key in ('base_url', 'origin'):
            with self.subTest(target_key=target_key):
                path = Path(self.temp.name) / f'{target_key}.json'
                path.write_text(json.dumps({target_key: self.base, 'token': self.owner}), encoding='utf-8')
                completed = subprocess.run(
                    [sys.executable, cli_path, '--token-file', str(path), 'state'],
                    env=env, capture_output=True, text=True, encoding='utf-8', timeout=15)
                self.assertEqual(completed.returncode, 0, completed.stderr)
                state = json.loads(completed.stdout)
                self.assertEqual(state['workspace']['name'], '跨产品协作验收')

        # An explicit target remains an intentional override of the saved binding.
        path = Path(self.temp.name) / 'explicit.json'
        path.write_text(json.dumps({'base_url': self.base, 'token': self.owner}), encoding='utf-8')
        explicit = subprocess.run(
            [sys.executable, cli_path, '--base', other_base, '--token-file', str(path), 'state'],
            env=env, capture_output=True, text=True, encoding='utf-8', timeout=15)
        self.assertNotEqual(explicit.returncode, 0)
        self.assertIn('HTTP 401', explicit.stderr)

        # Without a credential binding, the environment remains the next fallback.
        env_path = Path(self.temp.name) / 'environment.json'
        setup = urllib.request.Request(
            other_base + '/api/setup',
            data=json.dumps({'name': '环境变量身份', 'workspace_name': '环境变量目标'}).encode('utf-8'),
            headers={'Content-Type': 'application/json'}, method='POST')
        with urllib.request.urlopen(setup, timeout=5) as response:
            secondary = json.load(response)
        env_path.write_text(json.dumps({'token': secondary['token']}), encoding='utf-8')
        environment = subprocess.run(
            [sys.executable, cli_path, '--token-file', str(env_path), 'state'],
            env=env, capture_output=True, text=True, encoding='utf-8', timeout=15)
        self.assertEqual(environment.returncode, 0, environment.stderr)
        self.assertEqual(json.loads(environment.stdout)['workspace']['name'], '环境变量目标')

    def start_drop_proxy(self, drop_mode):
        """HTTP proxy that drops one join response before or after forwarding."""
        owner = self
        state = {'drop_mode': drop_mode}

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, format, *args):
                return

            def dispatch(self):
                body = self.rfile.read(int(self.headers.get('Content-Length', '0'))) if self.command == 'POST' else None
                is_join = self.command == 'POST' and self.path == '/api/join'
                mode = state['drop_mode'] if is_join else None
                if mode == 'before':
                    state['drop_mode'] = None
                    self.close_connection = True
                    self.connection.shutdown(socket.SHUT_RDWR)
                    self.connection.close()
                    return
                headers = {key: value for key, value in self.headers.items()
                           if key.lower() in ('authorization', 'content-type', 'accept')}
                request = urllib.request.Request(owner.base + self.path, data=body, method=self.command, headers=headers)
                try:
                    with urllib.request.urlopen(request, timeout=5) as response:
                        status, response_body = response.status, response.read()
                except urllib.error.HTTPError as error:
                    status, response_body = error.code, error.read()
                if mode == 'after':
                    state['drop_mode'] = None
                    self.close_connection = True
                    self.connection.shutdown(socket.SHUT_RDWR)
                    self.connection.close()
                    return
                self.send_response(status)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(response_body)))
                self.end_headers()
                self.wfile.write(response_body)

            do_GET = dispatch
            do_POST = dispatch

        proxy = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=proxy.serve_forever, daemon=True)
        thread.start()
        def stop_proxy():
            proxy.shutdown()
            proxy.server_close()
            thread.join(5)
        self.addCleanup(stop_proxy)
        return f'http://127.0.0.1:{proxy.server_port}'

    def test_client_token_binding_and_duplicate_conflict(self):
        token = 'A' * 43
        setup = self.ok('POST', '/api/setup', {
            'name': '凭证绑定组织者', 'workspace_name': 'token绑定', 'client_token': token,
        })
        self.assertEqual(setup['token'], token)
        serialized = json.dumps(setup)
        self.assertNotIn(hashlib.sha256(token.encode()).hexdigest(), serialized)

        invite = self.ok('POST', '/api/invites', {'kind': 'ai'}, token)
        duplicate = self.request('POST', '/api/join', {
            'code': invite['code'], 'name': '重复凭证', 'kind': 'ai', 'client_token': token,
        }, expect=409)
        self.assertEqual(duplicate[0], 409, duplicate)
        # The failed insert and invite consumption are in the same transaction.
        joined = self.ok('POST', '/api/join', {
            'code': invite['code'], 'name': '新凭证', 'kind': 'ai', 'client_token': 'B' * 43,
        })
        self.assertEqual(joined['token'], 'B' * 43)
        self.assertNotIn(hashlib.sha256(('B' * 43).encode()).hexdigest(), json.dumps(joined))

    def test_base_url_transport_and_authority_restrictions(self):
        for value in (
            'ftp://127.0.0.1:4284',
            'http://example.com:4284',
            'http://user:password@127.0.0.1:4284',
            'http://127.0.0.1:4284?next=/api',
            'http://127.0.0.1:4284#fragment',
        ):
            with self.subTest(value=value), self.assertRaises(ClientError):
                validate_base_url(value)
        self.assertEqual(validate_base_url('http://[::1]:4284/'), 'http://[::1]:4284')
        self.assertEqual(validate_base_url('https://api.example.com/v1'), 'https://api.example.com/v1')
        credentials = {'base_url': 'https://bound.example/api'}
        env = {'CS_BASE_URL': 'https://environment.example/api'}
        self.assertEqual(resolve_base_url('https://explicit.example/api', credentials, env), 'https://explicit.example/api')
        self.assertEqual(resolve_base_url(None, credentials, env), 'https://bound.example/api')
        self.assertEqual(resolve_base_url(None, None, env), 'https://environment.example/api')
        self.assertEqual(resolve_base_url(None, None, {}), 'http://127.0.0.1:4284')

    def test_cli_recovers_join_after_committed_response_drop(self):
        self.initialize()
        invite = self.ok('POST', '/api/invites', {'kind': 'ai'}, self.owner)
        proxy_base = self.start_drop_proxy('after')
        path = Path(self.temp.name) / 'recovered.json'
        env = os.environ.copy()
        env['CS_INVITE_CODE'] = invite['code']
        env.pop('CS_TOKEN', None)
        env['PYTHONIOENCODING'] = 'utf-8'
        completed = subprocess.run(
            [sys.executable, str(ROOT / 'scripts' / 'agent_client.py'), '--base', proxy_base,
             'join', '--name', '断响应后恢复', '--save-token', str(path)],
            env=env, capture_output=True, text=True, encoding='utf-8', timeout=15)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        output = json.loads(completed.stdout)
        saved = json.loads(path.read_text(encoding='utf-8'))
        self.assertEqual(output['member']['name'], '断响应后恢复')
        self.assertTrue(output['recovered'])
        self.assertNotIn('pending', saved)
        self.assertEqual(self.ok('GET', '/api/me', token=saved['token'])['member']['name'], '断响应后恢复')

    def test_cli_resume_retries_uncommitted_join_with_saved_token_and_invite(self):
        self.initialize()
        invite = self.ok('POST', '/api/invites', {'kind': 'ai'}, self.owner)
        proxy_base = self.start_drop_proxy('before')
        path = Path(self.temp.name) / 'pending.json'
        env = os.environ.copy()
        env['CS_INVITE_CODE'] = invite['code']
        env.pop('CS_TOKEN', None)
        env['PYTHONIOENCODING'] = 'utf-8'
        cli_path = str(ROOT / 'scripts' / 'agent_client.py')
        first = subprocess.run(
            [sys.executable, cli_path, '--base', proxy_base, 'join', '--name', '恢复加入', '--save-token', str(path)],
            env=env, capture_output=True, text=True, encoding='utf-8', timeout=15)
        self.assertNotEqual(first.returncode, 0)
        self.assertIn('resume', first.stderr)
        pending = json.loads(path.read_text(encoding='utf-8'))
        self.assertEqual(pending['pending']['command'], 'join')
        self.assertEqual(pending['pending']['payload']['client_token'], pending['token'])
        self.assertEqual(pending['pending']['payload']['code'], invite['code'])
        self.assertNotIn(pending['token'], first.stderr)
        self.assertNotIn(invite['code'], first.stderr)

        resumed = subprocess.run(
            [sys.executable, cli_path, '--token-file', str(path), 'resume'],
            env=env, capture_output=True, text=True, encoding='utf-8', timeout=15)
        self.assertEqual(resumed.returncode, 0, resumed.stderr)
        output = json.loads(resumed.stdout)
        saved = json.loads(path.read_text(encoding='utf-8'))
        self.assertEqual(output['member']['name'], '恢复加入')
        self.assertNotIn('pending', saved)
        self.assertEqual(self.ok('GET', '/api/me', token=saved['token'])['member']['name'], '恢复加入')


if __name__ == '__main__':
    unittest.main(verbosity=2)
