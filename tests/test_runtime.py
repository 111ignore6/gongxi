import json
from http.server import BaseHTTPRequestHandler, HTTPServer
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from unittest.mock import patch

from scripts import runtime as runtime_manager

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "scripts" / "runtime.py"


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def run_runtime(command, port, data_dir, *extra):
    return subprocess.run(
        [sys.executable, str(RUNTIME), command, "--port", str(port), "--data-dir", str(data_dir), "--json", *extra],
        cwd=ROOT,
        text=True,
        encoding="utf-8",
        capture_output=True,
        timeout=35,
        check=False,
    )


def get_json(url):
    with urllib.request.urlopen(url, timeout=2) as response:
        return json.loads(response.read().decode("utf-8"))


class RuntimeAcceptanceTests(unittest.TestCase):
    @unittest.skipIf(os.name == "nt", "POSIX TIME_WAIT reuse semantics")
    def test_port_probe_accepts_time_wait_but_rejects_live_listener(self):
        with socket.socket() as listener:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            port = listener.getsockname()[1]
            self.assertFalse(runtime_manager.port_is_available(port))
            with socket.create_connection(("127.0.0.1", port)) as client:
                accepted, _ = listener.accept()
                accepted.close()
                self.assertEqual(client.recv(1), b"")
        self.assertTrue(runtime_manager.port_is_available(port))

    def test_runtime_prefers_exact_lockfile_when_available(self):
        expected = "requirements-lock.txt" if (ROOT / "requirements-lock.txt").is_file() else "requirements.txt"
        self.assertEqual(runtime_manager.requirements_file().name, expected)

    def test_start_reuses_stops_and_restores_persistent_data(self):
        port = free_port()
        with tempfile.TemporaryDirectory(prefix="gongxi-runtime-") as temp:
            data_dir = Path(temp) / "workspace-data"
            first = run_runtime("start", port, data_dir, "--no-browser")
            self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
            first_value = json.loads(first.stdout)
            self.assertTrue(first_value["managed"])
            self.assertEqual(first_value["port"], port)
            self.assertEqual(first_value["url"], f"http://127.0.0.1:{port}")
            self.assertNotIn("management_token", first.stdout)
            self.assertNotIn(str(data_dir), first.stdout)

            for _ in range(20):
                try:
                    health = get_json(f"http://127.0.0.1:{port}/api/health")
                    break
                except OSError:
                    time.sleep(0.1)
            else:
                self.fail("health endpoint did not become ready")
            self.assertEqual(health["service"], "gongxi")
            self.assertEqual(health["project_id"], first_value["project_id"])
            self.assertEqual(health["data_dir_id"], first_value["data_dir_id"])
            self.assertEqual(health["pid"], first_value["pid"])
            self.assertNotIn(str(data_dir), json.dumps(health))

            request = urllib.request.Request(
                f"http://127.0.0.1:{port}/api/setup",
                data=json.dumps({"name": "测试者", "workspace_name": "持久化验证"}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=5) as response:
                self.assertEqual(response.status, 200)

            repeated = run_runtime("start", port, data_dir, "--no-browser")
            self.assertEqual(repeated.returncode, 0, repeated.stdout + repeated.stderr)
            repeated_value = json.loads(repeated.stdout)
            self.assertTrue(repeated_value["reused"])
            self.assertEqual(repeated_value["instance_id"], first_value["instance_id"])

            doctor = run_runtime("doctor", port, data_dir, "--no-browser")
            self.assertEqual(doctor.returncode, 0, doctor.stdout + doctor.stderr)
            doctor_value = json.loads(doctor.stdout)
            self.assertTrue(doctor_value["ok"])
            self.assertTrue(doctor_value["running"])
            self.assertTrue(doctor_value["reusable"])
            self.assertFalse(doctor_value["port_available"])
            self.assertEqual(doctor_value["url"], f"http://127.0.0.1:{port}")
            self.assertEqual(doctor_value["instance_id"], first_value["instance_id"])
            self.assertFalse(any("选择空闲端口" in suggestion for suggestion in doctor_value["suggestions"]))

            status = run_runtime("status", port, data_dir)
            status_value = json.loads(status.stdout)
            self.assertTrue(status_value["managed"])
            self.assertEqual(status_value["url"], f"http://127.0.0.1:{port}")
            stopped = run_runtime("stop", port, data_dir)
            self.assertEqual(stopped.returncode, 0, stopped.stdout + stopped.stderr)
            self.assertTrue(json.loads(stopped.stdout)["stopped"])

            restarted = run_runtime("start", port, data_dir, "--no-browser")
            self.assertEqual(restarted.returncode, 0, restarted.stdout + restarted.stderr)
            self.assertTrue(get_json(f"http://127.0.0.1:{port}/api/status")["initialized"])
            stopped_again = run_runtime("stop", port, data_dir)
            self.assertEqual(stopped_again.returncode, 0, stopped_again.stdout + stopped_again.stderr)

    def test_start_refuses_foreign_port_without_touching_service(self):
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                payload = b'{"ok":true}'
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *_args):
                pass

        server = HTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory(prefix="gongxi-foreign-") as temp:
                result = run_runtime("start", server.server_port, Path(temp) / "data", "--no-browser")
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(json.loads(result.stdout)["ok"])
                self.assertIn("端口", json.loads(result.stdout)["error"])
                doctor = run_runtime("doctor", server.server_port, Path(temp) / "data", "--no-browser")
                doctor_value = json.loads(doctor.stdout)
                self.assertFalse(doctor_value["ok"])
                self.assertFalse(doctor_value["running"])
                self.assertFalse(doctor_value["reusable"])
                self.assertTrue(doctor_value["foreign"])
                self.assertIn("端口", " ".join(doctor_value["suggestions"]))
                self.assertIn("未触碰", " ".join(doctor_value["suggestions"]))
                self.assertEqual(get_json(f"http://127.0.0.1:{server.server_port}/api/health"), {"ok": True})
        finally:
            server.shutdown()
            server.server_close()

    def test_direct_uvicorn_has_no_runtime_stop_control(self):
        port = free_port()
        with tempfile.TemporaryDirectory(prefix="gongxi-unmanaged-") as temp:
            env = os.environ.copy()
            env["CS_DATA_DIR"] = str(Path(temp) / "data")
            process = subprocess.Popen(
                [sys.executable, "-m", "uvicorn", "backend.app:app", "--host", "127.0.0.1", "--port", str(port), "--no-access-log"],
                cwd=ROOT,
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            try:
                for _ in range(80):
                    try:
                        get_json(f"http://127.0.0.1:{port}/api/health")
                        break
                    except OSError:
                        time.sleep(0.1)
                else:
                    self.fail("direct uvicorn did not become ready")
                request = urllib.request.Request(
                    f"http://127.0.0.1:{port}/api/runtime/stop",
                    data=b'{"instance_id":"x","project_id":"x","data_dir_id":"x"}',
                    headers={"Content-Type": "application/json", "X-Gongxi-Management-Token": "anything"},
                    method="POST",
                )
                with self.assertRaises(urllib.error.HTTPError) as raised:
                    urllib.request.urlopen(request, timeout=3)
                self.assertEqual(raised.exception.code, 409)
            finally:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)

    def test_start_reaper_preserves_control_until_child_exit_is_confirmed(self):
        class StuckProcess:
            pid = 12345

            def __init__(self):
                self.terminated = False

            def poll(self):
                return None

            def terminate(self):
                self.terminated = True

            def wait(self, timeout):
                raise subprocess.TimeoutExpired("test-child", timeout)

        process = StuckProcess()
        with patch.object(runtime_manager, "request_health", return_value=None):
            stopped = runtime_manager.reap_failed_start(process, 1234, "instance", "project", "data", "secret")
        self.assertFalse(stopped)
        self.assertTrue(process.terminated)

    def test_start_reaper_stops_by_instance_even_when_venv_launcher_pid_differs(self):
        class LauncherProcess:
            pid = 111

            def poll(self):
                return None

            def wait(self, timeout):
                return 0

        health = {"ok": True, "service": "gongxi", "project_id": "project", "data_dir_id": "data", "instance_id": "instance", "pid": 222}
        with patch.object(runtime_manager, "request_health", side_effect=[health, None]), patch.object(runtime_manager, "request_managed_stop", return_value=True) as stop:
            stopped = runtime_manager.reap_failed_start(LauncherProcess(), 1234, "instance", "project", "data", "secret")
        self.assertTrue(stopped)
        stop.assert_called_once_with(1234, health, "project", "data", "secret")

    def test_runtime_control_file_uses_private_atomic_writer(self):
        with tempfile.TemporaryDirectory(prefix="gongxi-private-control-") as temp:
            path = Path(temp) / "control.json"
            runtime_manager.write_private(path, {"management_token": "first"})
            runtime_manager.write_private(path, {"management_token": "second"})
            self.assertEqual(runtime_manager.read_control(path), {"management_token": "second"})
            self.assertEqual(list(Path(temp).iterdir()), [path])
            if os.name == "nt":
                acl = subprocess.check_output(["icacls", str(path)])
                self.assertIn(b":(F)", acl)
                self.assertNotIn(b"(I)", acl)


if __name__ == "__main__":
    unittest.main()
