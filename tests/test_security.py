"""Security boundary checks against a real loopback HTTP server.

Every test gets a fresh temporary SQLite database; the repository data directory
and any running user service are never opened or modified.
"""
import http.client
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import uvicorn
from backend.app import create_app


class SecurityHTTPCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="gongxi-security-")
        self.previous_management_token = os.environ.get("GONGXI_MANAGEMENT_TOKEN")
        self.management_token = "security-test-management-secret"
        os.environ["GONGXI_MANAGEMENT_TOKEN"] = self.management_token
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            self.port = sock.getsockname()[1]
        self.host = f"127.0.0.1:{self.port}"
        self.app = create_app(Path(self.temp.name) / "data")
        self.server = uvicorn.Server(uvicorn.Config(
            self.app, host="127.0.0.1", port=self.port, log_level="error",
            access_log=False,
        ))
        self.thread = threading.Thread(target=self.server.run, daemon=True)
        self.thread.start()
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            if self.server.started:
                break
            if not self.thread.is_alive():
                self.fail("uvicorn stopped before accepting requests")
            time.sleep(0.02)
        self.assertTrue(self.server.started, "uvicorn did not start")

    def tearDown(self):
        self.server.should_exit = True
        self.thread.join(timeout=8)
        self.temp.cleanup()
        if self.previous_management_token is None:
            os.environ.pop("GONGXI_MANAGEMENT_TOKEN", None)
        else:
            os.environ["GONGXI_MANAGEMENT_TOKEN"] = self.previous_management_token
        self.assertFalse(self.thread.is_alive(), "uvicorn did not stop cleanly")

    def request(self, method, path, body=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=3)
        payload = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        request_headers = {"Accept": "application/json"}
        if payload is not None:
            request_headers["Content-Type"] = "application/json"
        if headers:
            request_headers.update(headers)
        connection.request(method, path, body=payload, headers=request_headers)
        response = connection.getresponse()
        raw = response.read()
        result = {
            "status": response.status,
            "headers": {key.lower(): value for key, value in response.getheaders()},
            "raw": raw,
        }
        try:
            result["json"] = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            result["json"] = None
        connection.close()
        return result

    def setup_workspace(self, headers=None):
        return self.request("POST", "/api/setup", {
            "name": "安全测试组织者", "workspace_name": "隔离测试空间",
        }, headers=headers)

    def test_application_pages_limit_scripts_connections_and_framing(self):
        for path in ('/', '/join'):
            result = self.request('GET', path)
            self.assertEqual(result['status'], 200)
            policy = result['headers'].get('content-security-policy', '')
            self.assertIn("script-src 'self'", policy)
            self.assertIn("connect-src 'self'", policy)
            self.assertIn("frame-ancestors 'none'", policy)
            self.assertNotIn("script-src 'unsafe-inline'", policy)
            self.assertEqual(result['headers'].get('x-frame-options'), 'DENY')
            self.assertEqual(result['headers'].get('referrer-policy'), 'no-referrer')

    def test_cross_origin_and_dns_rebinding_style_setup_are_rejected(self):
        evil = self.setup_workspace({"Origin": "http://evil.example", "Host": self.host})
        self.assertEqual(evil["status"], 403)
        self.assertEqual(evil["headers"].get("x-content-type-options"), "nosniff")
        self.assertEqual(evil["headers"].get("cache-control"), "no-store")

        rebinding = self.setup_workspace({
            "Origin": f"http://attacker.example:{self.port}",
            "Host": f"attacker.example:{self.port}",
        })
        self.assertEqual(rebinding["status"], 403)

        malformed = self.setup_workspace({"Origin": "http://[::1"})
        self.assertEqual(malformed["status"], 403)
        self.assertEqual(self.request("GET", "/api/status")["json"], {"initialized": False})

    def test_fetch_metadata_blocks_cross_site_even_without_origin(self):
        result = self.setup_workspace({"Sec-Fetch-Site": "cross-site"})
        self.assertEqual(result["status"], 403)
        self.assertEqual(self.request("GET", "/api/status")["json"], {"initialized": False})

    def test_same_origin_browser_setup_and_originless_cli_setup_work(self):
        same_origin = self.setup_workspace({"Origin": f"http://{self.host}"})
        self.assertEqual(same_origin["status"], 200, same_origin["json"])
        self.assertTrue(same_origin["json"]["token"])

        second_app = create_app(Path(self.temp.name) / "cli-data")
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            second_port = sock.getsockname()[1]
        second_server = uvicorn.Server(uvicorn.Config(
            second_app, host="127.0.0.1", port=second_port, log_level="error", access_log=False,
        ))
        # Use a separately bound real server to preserve the CLI's no-Origin contract.
        second_thread = threading.Thread(target=second_server.run, daemon=True)
        second_thread.start()
        try:
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline and not second_server.started:
                time.sleep(0.02)
            self.assertTrue(second_server.started)
            response = http.client.HTTPConnection("127.0.0.1", second_port, timeout=3)
            payload = json.dumps({"name": "CLI", "workspace_name": "CLI"}).encode()
            response.request("POST", "/api/setup", payload, {"Content-Type": "application/json"})
            result = response.getresponse()
            self.assertEqual(result.status, 200)
            self.assertTrue(json.loads(result.read())["token"])
            response.close()
        finally:
            second_server.should_exit = True
            second_thread.join(timeout=8)
            self.assertFalse(second_thread.is_alive())

    def test_unauthenticated_and_cookie_only_requests_cannot_read_workspace(self):
        created = self.setup_workspace()
        self.assertEqual(created["status"], 200)
        no_auth = self.request("GET", "/api/state")
        self.assertEqual(no_auth["status"], 401)
        cookie_only = self.request("GET", "/api/state", headers={
            "Cookie": "gongxi_token=" + created["json"]["token"],
        })
        self.assertEqual(cookie_only["status"], 401)

        member = self.request("GET", "/api/me", headers={
            "Authorization": "Bearer " + created["json"]["token"],
        })
        self.assertEqual(member["status"], 200)
        self.assertNotIn("token", member["json"]["member"])
        self.assertNotIn("token_hash", json.dumps(member["json"]))

    def test_non_owner_cannot_mint_invites_and_message_is_json_data(self):
        owner = self.setup_workspace()["json"]["token"]
        invite = self.request("POST", "/api/invites", {"kind": "ai"}, {
            "Authorization": "Bearer " + owner,
        })
        self.assertEqual(invite["status"], 200)
        join = self.request("POST", "/api/join", {
            "code": invite["json"]["code"], "kind": "ai", "name": "参与者",
        })
        self.assertEqual(join["status"], 200)
        participant = join["json"]["token"]

        forbidden = self.request("POST", "/api/invites", {"kind": "human"}, {
            "Authorization": "Bearer " + participant,
        })
        self.assertEqual(forbidden["status"], 403)

        topic = self.request("POST", "/api/topics", {"title": "XSS 检查"}, {
            "Authorization": "Bearer " + participant,
        })["json"]
        payload = '<img src=x onerror="alert(1)"> </script>'
        message = self.request("POST", "/api/messages", {
            "topic_id": topic["id"], "body": payload,
        }, {"Authorization": "Bearer " + participant})
        self.assertEqual(message["status"], 200)
        self.assertTrue(message["headers"].get("content-type", "").startswith("application/json"))
        self.assertEqual(message["headers"].get("x-content-type-options"), "nosniff")
        self.assertEqual(message["json"]["body"], payload)

    def test_public_health_contains_no_credentials_or_raw_local_path(self):
        health = self.request("GET", "/api/health")
        self.assertEqual(health["status"], 200)
        serialized = health["raw"].decode("utf-8")
        self.assertNotIn(self.management_token, serialized)
        self.assertNotIn("management_token", serialized)
        self.assertNotIn(self.temp.name, serialized)
        self.assertNotIn("token_hash", serialized)


if __name__ == "__main__":
    unittest.main()
