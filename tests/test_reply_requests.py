"""HTTP acceptance for explicit reply requests (reminders, v0.3 semantics)."""
import json
import socket
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

import uvicorn

from backend.app import create_app


class ReplyRequestAcceptance(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='gongxi-reply-requests-')
        self.addCleanup(self.temp.cleanup)
        listener = socket.socket()
        listener.bind(('127.0.0.1', 0))
        listener.listen(128)
        self.base = 'http://127.0.0.1:' + str(listener.getsockname()[1])
        self.server = uvicorn.Server(uvicorn.Config(
            create_app(Path(self.temp.name)), log_level='error', access_log=False))
        self.thread = threading.Thread(target=self.server.run,
                                       kwargs={'sockets': [listener]}, daemon=True)
        self.thread.start()
        deadline = time.monotonic() + 8
        while not self.server.started and self.thread.is_alive() and time.monotonic() < deadline:
            time.sleep(.02)
        self.addCleanup(setattr, self.server, 'should_exit', True)
        owner = self.request('POST', '/api/setup', {'name': 'Owner', 'workspace_name': 'Reply requests'})[1]
        self.owner_token = owner['token']
        self.owner_id = owner['member']['id']
        self.topic = self.request('POST', '/api/topics', {'title': 'Main'}, self.owner_token)[1]['id']
        self.reader = self.join('Reader')
        self.bystander = self.join('Bystander')
        self.message = self.request('POST', '/api/messages', {
            'topic_id': self.topic, 'body': '请回应一下'}, self.owner_token)[1]

    def join(self, name):
        invite = self.request('POST', '/api/invites', {'kind': 'ai'}, self.owner_token)[1]
        joined = self.request('POST', '/api/join', {'code': invite['code'], 'name': name, 'kind': 'ai'})[1]
        return {'token': joined['token'], 'id': joined['member']['id']}

    def request(self, method, path, body=None, token=None, headers=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method)
        req.add_header('Content-Type', 'application/json')
        if token:
            req.add_header('Authorization', 'Bearer ' + token)
        for key, value in (headers or {}).items():
            req.add_header(key, value)
        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                return response.status, json.loads(response.read().decode())
        except urllib.error.HTTPError as error:
            payload = error.read().decode()
            return error.code, json.loads(payload) if payload else {}

    def create(self, targets, message_id=None, key='k1', due=None, token=None):
        body = {'message_id': message_id or self.message['id'], 'member_ids': targets}
        if due:
            body['due_at'] = due
        return self.request('POST', '/api/reply-requests', body,
                            token or self.owner_token, {'Idempotency-Key': key})

    def test_author_creates_request_and_targets_see_it(self):
        status, created = self.create([self.reader['id']])
        self.assertEqual(status, 200, created)
        request_id = created['requests'][0]['id']
        reader_state = self.request('GET', '/api/state', None, self.reader['token'])[1]
        self.assertEqual([r['id'] for r in reader_state['reply_requests']], [request_id])
        bystander_state = self.request('GET', '/api/state', None, self.bystander['token'])[1]
        self.assertEqual(bystander_state['reply_requests'], [])
        owner_state = self.request('GET', '/api/state', None, self.owner_token)[1]
        self.assertEqual([r['id'] for r in owner_state['reply_requests']], [request_id])

    def test_only_author_can_create(self):
        status, _ = self.create([self.bystander['id']], token=self.reader['token'])
        self.assertEqual(status, 403)

    def test_idempotent_create_returns_same_rows(self):
        first = self.create([self.reader['id']], key='same')
        second = self.create([self.reader['id']], key='same')
        self.assertEqual(first[0], 200)
        self.assertEqual([r['id'] for r in first[1]['requests']], [r['id'] for r in second[1]['requests']])
        changed = self.create([self.bystander['id']], key='same')
        self.assertEqual(changed[0], 409)

    def test_duplicate_pending_and_self_revoked_targets_rejected(self):
        self.create([self.reader['id']])
        self.assertEqual(self.create([self.reader['id']], key='k2')[0], 409)
        self.assertEqual(self.create([self.owner_id], key='k3')[0], 422)
        self.request('POST', '/api/members/' + self.bystander['id'] + '/revoke', {}, self.owner_token)
        self.assertEqual(self.create([self.bystander['id']], key='k4')[0], 422)

    def test_due_at_must_be_future(self):
        self.assertEqual(self.create([self.reader['id']], due='2000-01-01T00:00:00+00:00')[0], 422)
        status, created = self.create([self.reader['id']], key='k5', due='2999-01-01T00:00:00+00:00')
        self.assertEqual(status, 200, created)
        self.assertTrue(created['requests'][0]['due_at'].startswith('2999-01-01'))

    def test_reply_to_marks_only_target_responded(self):
        created = self.create([self.reader['id']])[1]
        request_id = created['requests'][0]['id']
        # Another member replying must not close it.
        self.request('POST', '/api/messages', {
            'topic_id': self.topic, 'body': '别人插话', 'reply_to': self.message['id']}, self.bystander['token'])
        state = self.request('GET', '/api/state', None, self.owner_token)[1]
        row = [r for r in state['reply_requests'] if r['id'] == request_id][0]
        self.assertEqual(row['status'], 'pending')
        reply = self.request('POST', '/api/messages', {
            'topic_id': self.topic, 'body': '我来回应', 'reply_to': self.message['id']}, self.reader['token'])[1]
        state = self.request('GET', '/api/state', None, self.owner_token)[1]
        row = [r for r in state['reply_requests'] if r['id'] == request_id][0]
        self.assertEqual(row['status'], 'responded')
        self.assertEqual(row['response_message_id'], reply['id'])

    def test_visibility_of_responded_at_and_withdrawal(self):
        created = self.create([self.reader['id']])[1]
        request_id = created['requests'][0]['id']
        self.request('POST', '/api/messages', {
            'topic_id': self.topic, 'body': '回应', 'reply_to': self.message['id']}, self.reader['token'])
        owner_row = [r for r in self.request('GET', '/api/state', None, self.owner_token)[1]['reply_requests'] if r['id'] == request_id][0]
        target_row = [r for r in self.request('GET', '/api/state', None, self.reader['token'])[1]['reply_requests'] if r['id'] == request_id][0]
        self.assertIn('responded_at', owner_row)
        self.assertIn('responded_at', target_row)
        # A second request that gets withdrawn: requester sees reason, target does not.
        second = self.create([self.bystander['id']], key='k9')[1]['requests'][0]
        status, _ = self.request('PATCH', '/api/reply-requests/' + second['id'],
                                 {'status': 'withdrawn'}, self.owner_token)
        self.assertEqual(status, 200)
        target_state = self.request('GET', '/api/state', None, self.bystander['token'])[1]
        row = [r for r in target_state['reply_requests'] if r['id'] == second['id']][0]
        self.assertEqual(row['status'], 'withdrawn')
        self.assertNotIn('withdrawal_reason', row)
        self.assertNotIn('withdrawn_at', row)

    def test_only_requester_can_withdraw_and_only_pending(self):
        created = self.create([self.reader['id']])[1]
        request_id = created['requests'][0]['id']
        self.assertEqual(self.request('PATCH', '/api/reply-requests/' + request_id,
                                      {'status': 'withdrawn'}, self.reader['token'])[0], 403)
        self.assertEqual(self.request('PATCH', '/api/reply-requests/' + request_id,
                                      {'status': 'withdrawn'}, self.owner_token)[0], 200)
        self.assertEqual(self.request('PATCH', '/api/reply-requests/' + request_id,
                                      {'status': 'withdrawn'}, self.owner_token)[0], 409)

    def test_events_are_filtered_without_stalling_cursor(self):
        created = self.create([self.reader['id']])[1]
        request_id = created['requests'][0]['id']
        self.request('POST', '/api/messages', {
            'topic_id': self.topic, 'body': '回应', 'reply_to': self.message['id']}, self.reader['token'])
        owner_events = self.request('GET', '/api/events?after=0', None, self.owner_token)[1]
        target_events = self.request('GET', '/api/events?after=0', None, self.reader['token'])[1]
        bystander_events = self.request('GET', '/api/events?after=0', None, self.bystander['token'])[1]
        owner_types = [e['type'] for e in owner_events['events'] if e['type'].startswith('reply_request.')]
        target_types = [e['type'] for e in target_events['events'] if e['type'].startswith('reply_request.')]
        bystander_types = [e['type'] for e in bystander_events['events'] if e['type'].startswith('reply_request.')]
        self.assertIn('reply_request.created', owner_types)
        self.assertIn('reply_request.responded', owner_types)
        self.assertIn('reply_request.created', target_types)
        self.assertEqual(bystander_types, [])
        self.assertGreater(bystander_events['cursor'], 0)


if __name__ == '__main__':
    unittest.main()
