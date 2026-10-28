"""HTTP acceptance for persistent read receipts and actionable message state."""
import concurrent.futures
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request

import uvicorn

from backend.app import create_app


class ReadTrackingAcceptance(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='gongxi-read-state-')
        self.addCleanup(self.temp.cleanup)
        self.listeners = []
        self.start_server()
        self.addCleanup(self.cleanup)
        self.token = None
        owner = self.request('POST', '/api/setup', {
            'name': 'Owner', 'workspace_name': 'Read state acceptance'})[1]
        self.owner_token = owner['token']
        self.token = self.owner_token
        self.owner_id = owner['member']['id']
        self.topic = self.request('POST', '/api/topics', {'title': 'Main'})[1]['id']
        self.other_topic = self.request('POST', '/api/topics', {'title': 'Other'})[1]['id']
        invite = self.request('POST', '/api/invites', {'kind': 'ai'})[1]
        joined = self.request('POST', '/api/join', {
            'code': invite['code'], 'name': 'DSH', 'kind': 'ai'})[1]
        self.dsh_token = joined['token']
        self.dsh_id = joined['member']['id']

    def start_server(self):
        listener = socket.socket()
        listener.bind(('127.0.0.1', 0))
        listener.listen(128)
        self.listeners.append(listener)
        self.base = 'http://127.0.0.1:' + str(listener.getsockname()[1])
        self.server = uvicorn.Server(uvicorn.Config(
            create_app(Path(self.temp.name)), log_level='error', access_log=False))
        self.thread = threading.Thread(target=self.server.run,
                                       kwargs={'sockets': [listener]}, daemon=True)
        self.thread.start()
        deadline = time.monotonic() + 8
        while not self.server.started and self.thread.is_alive() and time.monotonic() < deadline:
            time.sleep(.02)
        self.assertTrue(self.server.started)

    def stop_server(self):
        if getattr(self, 'thread', None) and self.thread.is_alive():
            self.server.should_exit = True
            self.thread.join(8)
            self.assertFalse(self.thread.is_alive())

    def cleanup(self):
        self.stop_server()
        for listener in self.listeners:
            try:
                listener.close()
            except OSError:
                pass

    def request(self, method, path, body=None, token=None):
        if token is None:
            token = self.token
        headers = {'Content-Type': 'application/json'}
        if token:
            headers['Authorization'] = 'Bearer ' + token
        req = urllib.request.Request(
            self.base + path,
            data=None if body is None else json.dumps(body, ensure_ascii=False).encode(),
            method=method, headers=headers)
        try:
            response = urllib.request.urlopen(req, timeout=5)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            return response.status, json.load(response)

    def state(self, token=None):
        return self.request('GET', '/api/state', token=token)[1]

    def post_message(self, text, topic=None, **extra):
        body = {'topic_id': topic or self.topic, 'body': text, **extra}
        return self.request('POST', '/api/messages', body)[1]

    def test_read_watermark_is_member_scoped_monotonic_idempotent_and_persistent(self):
        first = self.post_message('first')
        second = self.post_message('second')
        self.assertLess(first['event_id'], second['event_id'])
        initial = self.state()
        self.assertIn('start_event_id', initial['read_tracking'])
        self.assertIn('started_at', initial['read_tracking'])
        self.assertEqual(initial['read_states'], [])

        status, marked = self.request('POST', '/api/read-state', {
            'topic_id': self.topic, 'message_id': first['id']})
        self.assertEqual(status, 200)
        self.assertEqual(marked['member_id'], self.owner_id)
        self.assertEqual(marked['last_read_message_id'], first['id'])
        self.assertEqual(marked['last_read_event_id'], first['event_id'])
        after_first = self.state()['cursor']

        status, duplicate = self.request('POST', '/api/read-state', {
            'topic_id': self.topic, 'message_id': first['id']})
        self.assertEqual(status, 200)
        self.assertEqual(duplicate, marked)
        self.assertEqual(self.state()['cursor'], after_first, 'duplicate read state must not emit an event')

        status, advanced = self.request('POST', '/api/read-state', {
            'topic_id': self.topic, 'message_id': second['id']})
        self.assertEqual(status, 200)
        self.assertEqual(advanced['last_read_message_id'], second['id'])
        after_advance = self.state()['cursor']
        status, stale = self.request('POST', '/api/read-state', {
            'topic_id': self.topic, 'message_id': first['id']})
        self.assertEqual(status, 200)
        self.assertEqual(stale, advanced, 'stale updates must never move the watermark backward')
        self.assertEqual(self.state()['cursor'], after_advance)

        dsh_state = self.state(token=self.dsh_token)
        self.assertEqual(len(dsh_state['read_states']), 1)
        self.assertEqual(dsh_state['read_states'][0]['member_id'], self.owner_id)
        self.assertEqual(dsh_state['read_states'][0]['last_read_message_id'], second['id'])
        self.assertEqual(self.request('POST', '/api/read-state', {
            'topic_id': self.other_topic, 'message_id': first['id']})[0], 409)
        self.assertEqual(self.request('POST', '/api/read-state', {
            'topic_id': 'missing', 'message_id': first['id']})[0], 404)

        self.stop_server()
        self.start_server()
        restored = self.state()
        self.assertEqual(restored['read_states'][0]['last_read_message_id'], second['id'])
        self.assertEqual(restored['messages'][0]['event_id'], first['event_id'])

    def test_explicit_action_requests_are_separate_from_read_and_target_only(self):
        ordinary = self.post_message('FYI only')
        requested = self.post_message('Please review this', action_for=[self.dsh_id], idempotency_key='review-1')
        self.assertEqual(requested['action_for'], [self.dsh_id])
        self.assertIn('event_id', requested)
        current = self.state()
        self.assertEqual(current['messages'][0]['action_for'], [])
        self.assertEqual(current['action_states'], [{
            'message_id': requested['id'], 'member_id': self.dsh_id,
            'status': 'pending', 'updated_at': current['action_states'][0]['updated_at']}])

        cursor = current['cursor']
        retry = self.post_message('Please review this', action_for=[self.dsh_id], idempotency_key='review-1')
        self.assertEqual(retry['id'], requested['id'])
        self.assertEqual(retry['event_id'], requested['event_id'])
        self.assertEqual(self.state()['cursor'], cursor, 'idempotent action request retry must not duplicate events')
        self.assertNotEqual(ordinary['id'], requested['id'])

        self.assertEqual(self.request('PATCH', f'/api/messages/{requested["id"]}/action-state',
                                      {'status': 'handled'})[0], 403)
        status, handled = self.request('PATCH', f'/api/messages/{requested["id"]}/action-state',
                                       {'status': 'handled'}, token=self.dsh_token)
        self.assertEqual(status, 200)
        self.assertEqual(handled['status'], 'handled')
        cursor = self.state()['cursor']
        status, duplicate = self.request('PATCH', f'/api/messages/{requested["id"]}/action-state',
                                         {'status': 'handled'}, token=self.dsh_token)
        self.assertEqual(status, 200)
        self.assertEqual(duplicate, handled)
        self.assertEqual(self.state()['cursor'], cursor, 'same action status must be idempotent')
        self.assertEqual(self.state(token=self.dsh_token)['action_states'][0]['status'], 'handled')

    def test_action_targets_must_be_unique_and_active_and_revocation_blocks_updates(self):
        self.assertEqual(self.request('POST', '/api/messages', {
            'topic_id': self.topic, 'body': 'duplicate',
            'action_for': [self.dsh_id, self.dsh_id]})[0], 422)
        self.assertEqual(self.request('POST', '/api/messages', {
            'topic_id': self.topic, 'body': 'unknown', 'action_for': ['missing']})[0], 422)
        message = self.post_message('Assigned', action_for=[self.dsh_id])
        self.assertEqual(self.request('POST', f'/api/members/{self.dsh_id}/revoke', {}, token=self.owner_token)[0], 200)
        self.assertEqual(self.request('PATCH', f'/api/messages/{message["id"]}/action-state',
                                      {'status': 'handled'}, token=self.dsh_token)[0], 401)
        self.assertEqual(self.state()['action_states'][0]['status'], 'pending')

    def test_read_updates_racing_out_of_order_cannot_regress(self):
        first = self.post_message('first')
        latest = self.post_message('latest')
        gate = threading.Barrier(2)

        def mark(message):
            gate.wait(timeout=3)
            return self.request('POST', '/api/read-state', {
                'topic_id': self.topic, 'message_id': message['id']})

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(mark, [first, latest]))
        self.assertEqual([status for status, _ in results], [200, 200])
        state = self.state()
        receipt = next(row for row in state['read_states'] if row['member_id'] == self.owner_id)
        self.assertEqual(receipt['last_read_message_id'], latest['id'])
        self.assertEqual(receipt['last_read_event_id'], latest['event_id'])

    def test_cli_read_and_action_state_commands(self):
        message = self.post_message('CLI follow-up', action_for=[self.dsh_id])
        cli_path = Path(__file__).resolve().parents[1] / 'scripts' / 'agent_client.py'

        def cli(token, *args):
            env = os.environ.copy()
            env['CS_TOKEN'] = token
            env['PYTHONIOENCODING'] = 'utf-8'
            result = subprocess.run(
                [sys.executable, str(cli_path), '--base', self.base, *args],
                env=env, capture_output=True, text=True, encoding='utf-8', timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)
            return json.loads(result.stdout)

        receipt = cli(self.owner_token, 'read-state', '--topic', self.topic,
                      '--message', message['id'])
        self.assertEqual(receipt['last_read_message_id'], message['id'])
        status = cli(self.dsh_token, 'action-state', message['id'],
                     '--status', 'handled')
        self.assertEqual(status['member_id'], self.dsh_id)
        self.assertEqual(status['status'], 'handled')


if __name__ == '__main__':
    unittest.main()
