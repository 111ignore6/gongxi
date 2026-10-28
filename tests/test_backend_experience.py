"""Real HTTP checks for optional history paging and task conflict detection."""
import json
from pathlib import Path
import socket
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request

import uvicorn

from backend.app import create_app


class BackendExperience(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='gongxi-experience-')
        self.addCleanup(self.temp.cleanup)
        self.listener = socket.socket()
        self.listener.bind(('127.0.0.1', 0))
        self.listener.listen(128)
        self.addCleanup(self.listener.close)
        self.base = 'http://127.0.0.1:' + str(self.listener.getsockname()[1])
        self.server = uvicorn.Server(uvicorn.Config(
            create_app(Path(self.temp.name)), log_level='error', access_log=False))
        self.thread = threading.Thread(target=self.server.run,
                                       kwargs={'sockets': [self.listener]}, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)
        deadline = time.monotonic() + 8
        while not self.server.started and self.thread.is_alive() and time.monotonic() < deadline:
            time.sleep(.02)
        self.assertTrue(self.server.started)
        self.token = None
        self.owner = self.request('POST', '/api/setup',
                                  {'name': 'Example owner', 'workspace_name': 'Paging QA'})[1]
        self.token = self.owner['token']
        self.topic = self.request('POST', '/api/topics', {'title': 'Main'})[1]['id']

    def stop(self):
        self.server.should_exit = True
        self.thread.join(8)
        self.assertFalse(self.thread.is_alive())

    def request(self, method, path, body=None):
        headers = {'Content-Type': 'application/json'}
        if self.token:
            headers['Authorization'] = 'Bearer ' + self.token
        req = urllib.request.Request(self.base + path,
                                     data=None if body is None else json.dumps(body).encode(),
                                     method=method, headers=headers)
        try:
            response = urllib.request.urlopen(req, timeout=5)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            return response.status, json.load(response)

    def post(self, body, topic=None):
        return self.request('POST', '/api/messages',
                            {'topic_id': topic or self.topic, 'body': body})[1]

    def test_history_pages_are_stable_across_appends_and_isolated_by_topic(self):
        other = self.request('POST', '/api/topics', {'title': 'Other'})[1]['id']
        expected = []
        for i in range(7):
            expected.append(self.post(str(i))['id'])
            self.post('unrelated', other)
        path = f'/api/topics/{self.topic}/messages?limit=3'
        status, page = self.request('GET', path)
        self.assertEqual(status, 200)
        self.assertEqual([m['id'] for m in page['messages']], expected[-3:])
        self.assertTrue(page['has_more'])
        # Appends after the first page must not move the older-page boundary.
        self.post('new arrival')
        collected = [m['id'] for m in page['messages']]
        while page['has_more']:
            _, page = self.request('GET', path + '&before=' + str(page['next_before']))
            collected = [m['id'] for m in page['messages']] + collected
        self.assertIsNone(page['next_before'])
        self.assertEqual(collected, expected)

    def test_light_state_is_explicit_and_default_state_stays_complete(self):
        message = self.post('Long discussion')
        full = self.request('GET', '/api/state')[1]
        light = self.request('GET', '/api/state?include_messages=false')[1]
        self.assertEqual([m['id'] for m in full['messages']], [message['id']])
        self.assertNotIn('messages_included', full)
        self.assertEqual(light.pop('messages_included'), False)
        self.assertEqual(light.pop('messages'), [])
        full.pop('messages')
        # Each authenticated request updates last_seen_at independently.
        full.pop('members'); light.pop('members')
        self.assertEqual(full, light)

    def test_paging_auth_validation_missing_topic_and_empty_history(self):
        path = f'/api/topics/{self.topic}/messages'
        self.assertEqual(self.request('GET', path)[1],
                         {'messages': [], 'has_more': False, 'next_before': None})
        for query in ('?before=0', '?before=-1', '?before=9223372036854775808', '?limit=0', '?limit=101'):
            self.assertEqual(self.request('GET', path + query)[0], 422)
        self.assertEqual(self.request('GET', '/api/events?after=9223372036854775808')[0], 422)
        self.assertEqual(self.request('GET', '/api/topics/missing/messages')[0], 404)
        self.token = None
        self.assertEqual(self.request('GET', path)[0], 401)
        self.assertEqual(self.request('GET', '/api/state?include_messages=false')[0], 401)

    def test_stale_task_writes_are_rejected_without_event_or_content_changes(self):
        task = self.request('POST', '/api/tasks', {'title': 'Concurrent task'})[1]
        path = '/api/tasks/' + task['id']
        status, updated = self.request('PATCH', path, {
            'assignee_id': self.owner['member']['id'], 'status': 'in_progress',
            'expected_updated_at': task['updated_at']})
        self.assertEqual(status, 200)
        self.assertNotIn('expected_updated_at', updated)
        cursor = self.request('GET', '/api/state')[1]['cursor']
        self.assertEqual(self.request('PATCH', path, {
            'status': 'done', 'expected_updated_at': task['updated_at']})[0], 409)
        state = self.request('GET', '/api/state')[1]
        self.assertEqual(state['cursor'], cursor)
        self.assertEqual(state['tasks'][0], updated)
        self.assertEqual(self.request('PATCH', path, {
            'status': 'done', 'expected_updated_at': updated['updated_at']})[0], 200)
        # The original API remains available to clients without preconditions.
        self.assertEqual(self.request('PATCH', path, {'status': 'in_progress'})[0], 200)
        self.assertEqual(self.request('PATCH', path, {
            'expected_updated_at': updated['updated_at']})[0], 422)


if __name__ == '__main__':
    unittest.main()
