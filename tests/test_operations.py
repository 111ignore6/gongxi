"""Real-service acceptance for invite files, event watch, and complete backups."""
import json
import http.server
import hashlib
import io
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
import uuid
import zipfile
from unittest import mock

from scripts import backup as backup_module
from scripts.agent_client import Client, ClientError, NO_REDIRECT_OPENER

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / 'scripts' / 'agent_client.py'
BACKUP = ROOT / 'scripts' / 'backup.py'


class OperationsAcceptance(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='gongxi-operations-')
        self.addCleanup(self.temp.cleanup)
        self.temp_path = Path(self.temp.name)
        self.logs = []
        self.processes = []
        self.addCleanup(self.cleanup_temp)
        self.addCleanup(self.close_logs)
        self.addCleanup(self.stop_servers)
        self.data = self.temp_path / 'live-data'
        self.base = self.start_server(self.data)

    def start_server(self, data_dir):
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        log = open(self.temp_path / f'server-{port}.log', 'a', encoding='utf-8')
        self.logs.append(log)
        data_dir = Path(data_dir).resolve()
        instance_id = uuid.uuid4().hex
        management_token = secrets.token_urlsafe(32)
        identity = {
            'instance_id': instance_id,
            'project_id': hashlib.sha256(str(ROOT.resolve()).encode('utf-8')).hexdigest()[:24],
            'data_dir_id': hashlib.sha256(str(data_dir).encode('utf-8')).hexdigest()[:24],
        }
        env = os.environ.copy()
        env['CS_DATA_DIR'] = str(data_dir)
        env['GONGXI_INSTANCE_ID'] = instance_id
        env['GONGXI_MANAGEMENT_TOKEN'] = management_token
        env['PYTHONIOENCODING'] = 'utf-8'
        proc = subprocess.Popen(
            [sys.executable, str(ROOT / 'launch.py'), '--port', str(port), '--data-dir', str(data_dir), '--no-browser'],
            cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
        handle = {
            'base': f'http://127.0.0.1:{port}', 'port': port, 'data_dir': data_dir,
            'identity': identity, 'management_token': management_token,
            'process': proc, 'log': log, 'stopped': False,
        }
        self.processes.append(handle)
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                self.fail('Uvicorn exited: ' + self.log_text(log))
            try:
                health = self.request(handle['base'], 'GET', '/api/health')[1]
                if health.get('ok'):
                    self.assert_managed_identity(handle, health)
                    return handle['base']
            except (OSError, urllib.error.URLError):
                pass
            time.sleep(.05)
        self.fail('Uvicorn startup timeout: ' + self.log_text(log))

    def stop_servers(self):
        errors = []
        for handle in reversed(self.processes):
            if handle['stopped']:
                continue
            try:
                self.stop_managed_server(handle)
            except Exception as error:
                errors.append(str(error))
        if errors:
            raise RuntimeError('Could not confirm isolated services stopped; temporary data is retained. ' + ' | '.join(errors))

    def stop_managed_server(self, handle):
        try:
            health = self.request(handle['base'], 'GET', '/api/health')[1]
        except (OSError, urllib.error.URLError, ValueError):
            health = None
        if health is not None:
            self.assert_managed_identity(handle, health)
            request = urllib.request.Request(
                handle['base'] + '/api/runtime/stop',
                data=json.dumps(handle['identity']).encode('utf-8'),
                headers={
                    'Content-Type': 'application/json',
                    'X-Gongxi-Management-Token': handle['management_token'],
                },
                method='POST',
            )
            try:
                with NO_REDIRECT_OPENER.open(request, timeout=5) as response:
                    result = json.load(response)
                if not result.get('ok'):
                    raise RuntimeError('managed stop did not confirm shutdown')
            except urllib.error.HTTPError as error:
                raise RuntimeError(f'managed stop rejected: HTTP {error.code}') from error
            except urllib.error.URLError:
                pass

        proc = handle['process']
        if proc.poll() is None:
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=5)
        deadline = time.monotonic() + 8
        while self.port_is_listening(handle['port']) and time.monotonic() < deadline:
            try:
                current = self.request(handle['base'], 'GET', '/api/health')[1]
            except (OSError, urllib.error.URLError, ValueError):
                current = None
            if current is not None:
                self.assert_managed_identity(handle, current)
            time.sleep(.1)
        if self.port_is_listening(handle['port']):
            raise RuntimeError('listener remains active after managed shutdown and Popen fallback; ' + self.log_text(handle['log']))
        if proc.poll() is None:
            raise RuntimeError('Popen wrapper did not exit after managed shutdown')
        handle['stopped'] = True

    @staticmethod
    def assert_managed_identity(handle, health):
        for key, expected in handle['identity'].items():
            if health.get(key) != expected:
                raise RuntimeError(f"{handle['base']} health {key} did not match this test instance")
        if not health.get('ok'):
            raise RuntimeError('test service health did not report ready')

    @staticmethod
    def port_is_listening(port):
        with socket.socket() as sock:
            sock.settimeout(.25)
            return sock.connect_ex(('127.0.0.1', port)) == 0

    def close_logs(self):
        for log in self.logs:
            if not log.closed:
                log.close()

    def cleanup_temp(self):
        active = [handle['base'] for handle in self.processes if not handle['stopped']]
        if active:
            raise RuntimeError(f'Retaining temporary data at {self.temp.name}; service stop was not confirmed: {", ".join(active)}')
        self.temp.cleanup()

    @staticmethod
    def log_text(log):
        log.flush()
        return Path(log.name).read_text(encoding='utf-8', errors='replace')[-3000:]

    @staticmethod
    def request(base, method, path, data=None, token=None):
        headers = {'Accept': 'application/json'}
        if token:
            headers['Authorization'] = 'Bearer ' + token
        body = None
        if data is not None:
            headers['Content-Type'] = 'application/json'
            body = json.dumps(data).encode('utf-8')
        req = urllib.request.Request(base + path, data=body, headers=headers, method=method)
        try:
            response = urllib.request.urlopen(req, timeout=6)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            return response.status, json.loads(response.read())

    def cli(self, base, *args, env=None, check=True):
        child_env = os.environ.copy()
        child_env.pop('CS_TOKEN', None)
        child_env.pop('CS_INVITE_CODE', None)
        child_env['CS_BASE_URL'] = base
        child_env['PYTHONIOENCODING'] = 'utf-8'
        child_env.update(env or {})
        result = subprocess.run([sys.executable, str(CLI), *map(str, args)], cwd=ROOT, env=child_env, text=True, capture_output=True, encoding='utf-8', timeout=20)
        if check and result.returncode:
            self.fail(f'CLI failed: {result.stderr}\n{result.stdout}')
        return result

    def backup_cli(self, *args, check=True):
        env = os.environ.copy()
        env['PYTHONIOENCODING'] = 'utf-8'
        result = subprocess.run([sys.executable, str(BACKUP), *map(str, args)], cwd=ROOT, env=env, text=True, capture_output=True, encoding='utf-8', errors='replace', timeout=30)
        if check and result.returncode:
            self.fail(f'Backup CLI failed: {result.stderr}\n{result.stdout}')
        return result

    def test_client_refuses_redirect_without_contacting_second_loopback_server(self):
        captured = []
        redirect_auth = []

        class CaptureHandler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                captured.append(self.headers.get('Authorization'))
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{"ok":true}')

            def log_message(self, *_args):
                pass

        target = http.server.ThreadingHTTPServer(('127.0.0.1', 0), CaptureHandler)
        target_thread = threading.Thread(target=target.serve_forever, daemon=True)
        target_thread.start()

        class RedirectHandler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                redirect_auth.append(self.headers.get('Authorization'))
                self.send_response(302)
                self.send_header('Location', f'http://127.0.0.1:{target.server_port}/capture')
                self.end_headers()

            def log_message(self, *_args):
                pass

        redirector = http.server.ThreadingHTTPServer(('127.0.0.1', 0), RedirectHandler)
        redirect_thread = threading.Thread(target=redirector.serve_forever, daemon=True)
        redirect_thread.start()
        try:
            client = Client(f'http://127.0.0.1:{redirector.server_port}', 'secret-test-token')
            with self.assertRaises(ClientError) as error:
                client.request('GET', '/redirect')
            self.assertIn('HTTP', str(error.exception))
            self.assertEqual(redirect_auth, ['Bearer secret-test-token'])
            self.assertEqual(captured, [], 'redirect target must receive no request or Authorization header')
        finally:
            redirector.shutdown()
            target.shutdown()
            redirector.server_close()
            target.server_close()
            redirect_thread.join(timeout=2)
            target_thread.join(timeout=2)

    def test_backup_rejects_missing_required_column_even_with_matching_manifest(self):
        db_path = self.temp_path / 'malformed.sqlite3'
        backup_path = self.temp_path / 'self-consistent-invalid.zip'
        with sqlite3.connect(db_path) as db:
            for table, required in backup_module.REQUIRED_COLUMNS.items():
                columns = sorted(required - ({'token_hash'} if table == 'members' else set()))
                declaration = ', '.join('"' + name + '" TEXT' for name in columns)
                db.execute('CREATE TABLE "' + table + '" (' + declaration + ')')
            table_names = sorted(backup_module.REQUIRED_COLUMNS)
            column_map = {
                table: [row[1] for row in db.execute('PRAGMA table_info("' + table + '")')]
                for table in table_names
            }
        db.close()
        manifest = {
            'format': backup_module.FORMAT,
            'created_at': '2026-10-26T00:00:00+00:00',
            'schema': {'user_version': 0, 'tables': table_names, 'columns': column_map},
            'table_counts': {table: 0 for table in table_names},
            'database_sha256': hashlib.sha256(db_path.read_bytes()).hexdigest(),
        }
        with zipfile.ZipFile(backup_path, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            archive.write(db_path, backup_module.DB_MEMBER)
            archive.writestr(backup_module.MANIFEST_MEMBER, json.dumps(manifest))
        with self.assertRaises(backup_module.BackupError):
            backup_module.verify(backup_path)

    def test_backup_rejects_oversize_declared_entries_and_streams(self):
        archive_path = self.temp_path / 'oversize.zip'
        manifest = json.dumps({'format': backup_module.FORMAT, 'created_at': '2026-10-26T00:00:00+00:00'})
        with zipfile.ZipFile(archive_path, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(backup_module.DB_MEMBER, b'database')
            archive.writestr(backup_module.MANIFEST_MEMBER, manifest)
        with mock.patch.object(backup_module, 'MAX_DATABASE_BYTES', 1):
            with self.assertRaises(backup_module.BackupError):
                backup_module.verify(archive_path)
        with mock.patch.object(backup_module, 'MAX_MANIFEST_BYTES', 1):
            with self.assertRaises(backup_module.BackupError):
                backup_module.verify(archive_path)
        with self.assertRaises(backup_module.BackupError):
            backup_module.copy_member_limited(io.BytesIO(b'too large'), io.BytesIO(), 3)

    def test_invite_file_watch_and_complete_live_backup_restore(self):
        owner_file = self.temp_path / 'owner.json'
        setup = json.loads(self.cli(self.base, 'setup', '--name', 'owner', '--workspace-name', 'operations', '--save-token', owner_file).stdout)
        invite_file = self.temp_path / 'invite.json'
        invite_output = self.cli(self.base, '--token-file', owner_file, 'invite', '--kind', 'ai', '--save-invite', invite_file).stdout
        invitation = json.loads(invite_file.read_text(encoding='utf-8'))
        self.assertEqual(invitation['base_url'], self.base)
        self.assertEqual(invitation['kind'], 'ai')
        self.assertNotIn(invitation['code'], invite_output)

        wrong_kind = self.cli(self.base, 'join', '--name', 'wrong-kind', '--kind', 'human', '--invite-file', invite_file, '--save-token', self.temp_path / 'wrong-kind.json', check=False)
        self.assertNotEqual(wrong_kind.returncode, 0)

        # Environment points to another live server; the invite's binding wins.
        decoy = self.start_server(self.temp_path / 'decoy-data')
        member_file = self.temp_path / 'member.json'
        joined = self.cli(decoy, 'join', '--name', 'member', '--invite-file', invite_file, '--save-token', member_file)
        joined_value = json.loads(joined.stdout)
        self.assertEqual(joined_value['member']['name'], 'member')
        self.assertNotIn(invitation['code'], joined.stdout)
        reused = self.cli(self.base, 'join', '--name', '重复', '--invite-file', invite_file, '--save-token', self.temp_path / 'member-again.json', check=False)
        self.assertNotEqual(reused.returncode, 0)

        cursor = self.request(self.base, 'GET', '/api/events', token=json.loads(owner_file.read_text())['token'])[1]['cursor']
        watch = subprocess.Popen(
            [sys.executable, str(CLI), '--token-file', str(member_file), 'watch', '--after', str(cursor), '--timeout', '4', '--interval', '1'],
            cwd=ROOT, env={**os.environ, 'PYTHONIOENCODING': 'utf-8'}, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8')
        time.sleep(.25)
        topic = self.request(self.base, 'POST', '/api/topics', {'title': 'backup topic', 'description': 'history'}, token=json.loads(owner_file.read_text())['token'])[1]
        stdout, stderr = watch.communicate(timeout=10)
        self.assertEqual(watch.returncode, 0, stderr)
        watched = json.loads(stdout)
        self.assertFalse(watched['timed_out'])
        self.assertEqual(watched['events'][0]['type'], 'topic.created')
        self.assertGreater(watched['cursor'], cursor)

        timed_out = json.loads(self.cli(self.base, '--token-file', member_file, 'watch', '--after', '999999', '--timeout', '1', '--interval', '1').stdout)
        self.assertTrue(timed_out['timed_out'])
        self.assertEqual(timed_out['events'], [])
        self.assertEqual(timed_out['cursor'], 999999)

        owner_token = json.loads(owner_file.read_text())['token']
        member_token = json.loads(member_file.read_text())['token']
        message = self.request(self.base, 'POST', '/api/messages', {'topic_id': topic['id'], 'body': 'member message to preserve'}, token=member_token)[1]
        task = self.request(self.base, 'POST', '/api/tasks', {'topic_id': topic['id'], 'title': 'continue work', 'description': 'resume after restore'}, token=member_token)[1]
        direction = self.request(self.base, 'POST', '/api/direction', {'body': 'restore history and continue', 'expected_revision': 0}, token=owner_token)[1]

        archive = self.temp_path / 'full-backup.zip'
        created = self.backup_cli('create', '--data-dir', self.data, '--output', archive)
        self.assertEqual(json.loads(created.stdout)['table_counts']['members'], 2)
        self.assertEqual(self.request(self.base, 'GET', '/api/health')[0], 200, 'online backup must leave service available')
        verified = json.loads(self.backup_cli('verify', archive).stdout)
        self.assertTrue(verified['valid'])

        # v1 backups created before column manifests remain verifiable and restorable.
        legacy_archive = self.temp_path / 'legacy-v1.zip'
        with zipfile.ZipFile(archive, 'r') as source:
            database_bytes = source.read(backup_module.DB_MEMBER)
            legacy_manifest = json.loads(source.read(backup_module.MANIFEST_MEMBER))
        legacy_manifest['schema'].pop('columns')
        with zipfile.ZipFile(legacy_archive, 'w', compression=zipfile.ZIP_DEFLATED) as target:
            target.writestr(backup_module.DB_MEMBER, database_bytes)
            target.writestr(backup_module.MANIFEST_MEMBER, json.dumps(legacy_manifest, ensure_ascii=False))
        self.assertTrue(backup_module.verify(legacy_archive)['valid'])
        legacy_restored = self.temp_path / 'legacy-restored'
        backup_module.restore(legacy_archive, legacy_restored)
        self.assertTrue((legacy_restored / backup_module.DB_NAME).is_file())

        restored_dir = self.temp_path / 'restored-data'
        self.backup_cli('restore', archive, '--data-dir', restored_dir)
        restored_base = self.start_server(restored_dir)
        restored_state = self.request(restored_base, 'GET', '/api/state', token=member_token)[1]
        self.assertEqual(next(m for m in restored_state['messages'] if m['id'] == message['id']), message)
        self.assertEqual(next(t for t in restored_state['tasks'] if t['id'] == task['id']), task)
        self.assertEqual(restored_state['direction'], direction)
        self.assertEqual({m['id'] for m in restored_state['members']}, {setup['member']['id'], joined_value['member']['id']})

        nonempty = self.temp_path / 'nonempty'
        nonempty.mkdir()
        (nonempty / 'keep.txt').write_text('existing')
        refused = self.backup_cli('restore', archive, '--data-dir', nonempty, check=False)
        self.assertNotEqual(refused.returncode, 0)
        self.assertTrue((nonempty / 'keep.txt').exists())

        corrupt = self.temp_path / 'corrupt.zip'
        with zipfile.ZipFile(archive) as src, zipfile.ZipFile(corrupt, 'w') as dst:
            for item in src.infolist():
                content = src.read(item.filename)
                if item.filename == 'database.sqlite3':
                    content = content[:-1] + bytes([content[-1] ^ 1])
                dst.writestr(item.filename, content)
        broken = self.backup_cli('verify', corrupt, check=False)
        self.assertNotEqual(broken.returncode, 0)


if __name__ == '__main__':
    unittest.main()
