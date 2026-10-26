"""Manage the local 共席 server without treating PID files as proof of life."""
import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser

try:
    from .agent_client import restrict_private_file as secure_private_file, write_private as secure_write_private
except ImportError:
    from agent_client import restrict_private_file as secure_private_file, write_private as secure_write_private

ROOT = Path(__file__).resolve().parents[1]
SERVICE_VERSION = "1.0.0"
LOCK_WAIT_SECONDS = 20


class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


NO_REDIRECT_OPENER = urllib.request.build_opener(NoRedirectHandler())


def identity_hash(path):
    return hashlib.sha256(str(path.resolve()).encode("utf-8")).hexdigest()[:24]


def get_paths(data_dir):
    data = Path(data_dir or os.environ.get("CS_DATA_DIR") or ROOT / "data").expanduser().resolve()
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local")) / "gongxi" / "runtime"
    elif os.environ.get("XDG_RUNTIME_DIR"):
        base = Path(os.environ["XDG_RUNTIME_DIR"]) / "gongxi"
    else:
        base = Path.home() / ".cache" / "gongxi" / "runtime"
    private = base / identity_hash(ROOT) / identity_hash(data)
    return data, private, private / "control.json", data / "logs" / "gongxi.log"


def read_control(control_file):
    try:
        return json.loads(control_file.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None


def request_health(port):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=1.5) as response:
            if response.status != 200:
                return None
            value = json.loads(response.read().decode("utf-8"))
            return value if isinstance(value, dict) else None
    except (OSError, ValueError, urllib.error.URLError):
        return None


def health_matches(health, project_id, data_id):
    return bool(
        health
        and health.get("ok") is True
        and health.get("service") == "gongxi"
        and health.get("project_id") == project_id
        and health.get("data_dir_id") == data_id
    )


def port_is_available(port):
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            # Match uvicorn's POSIX bind semantics: a recently stopped listener
            # can leave TIME_WAIT connections without another live service.
            # Windows SO_REUSEADDR permits overlapping listeners, so keep its
            # exclusive default instead.
            if os.name != "nt":
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            probe.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False


def selected_python():
    if os.name == "nt":
        candidate = ROOT / ".venv" / "Scripts" / "python.exe"
    else:
        candidate = ROOT / ".venv" / "bin" / "python"
    return str(candidate) if candidate.is_file() else sys.executable


def requirements_file():
    locked = ROOT / "requirements-lock.txt"
    return locked if locked.is_file() else ROOT / "requirements.txt"


@contextmanager
def start_lock(private_dir):
    private_dir.mkdir(parents=True, exist_ok=True)
    lock = private_dir / "start.lock"
    deadline = time.monotonic() + LOCK_WAIT_SECONDS
    descriptor = None
    while descriptor is None:
        try:
            descriptor = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.write(descriptor, str(time.time()).encode("ascii"))
        except FileExistsError:
            try:
                if time.time() - lock.stat().st_mtime > 120:
                    lock.unlink()
                    continue
            except OSError:
                continue
            if time.monotonic() >= deadline:
                raise RuntimeError("另一个 start 操作仍持有锁；请稍后重试")
            time.sleep(0.1)
    try:
        yield
    finally:
        os.close(descriptor)
        try:
            lock.unlink()
        except OSError:
            pass


def write_private(path, value):
    secure_write_private(path, value)


def runtime_url(port):
    return f"http://127.0.0.1:{port}"


def request_managed_stop(port, health, project_id, data_id, token):
    payload = json.dumps({
        "instance_id": health["instance_id"],
        "project_id": project_id,
        "data_dir_id": data_id,
    }).encode("utf-8")
    request = urllib.request.Request(
        runtime_url(port) + "/api/runtime/stop",
        data=payload,
        headers={"Content-Type": "application/json", "X-Gongxi-Management-Token": token},
        method="POST",
    )
    try:
        with NO_REDIRECT_OPENER.open(request, timeout=3) as response:
            return response.status == 200
    except (OSError, urllib.error.URLError):
        return False


def reap_failed_start(process, port, instance_id, project_id, data_id, token):
    """Request stop by token; report success only after the instance disappears."""
    health = request_health(port)
    same_instance = bool(
        health_matches(health, project_id, data_id)
        and health.get("instance_id") == instance_id
    )
    if same_instance:
        request_managed_stop(port, health, project_id, data_id, token)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            current = request_health(port)
            if not (health_matches(current, project_id, data_id) and current.get("instance_id") == instance_id):
                try:
                    process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    pass
                return True
            time.sleep(0.1)

    # On Windows, a venv's python.exe may be a redirector whose process handle
    # is not the Python process serving HTTP. Terminating it is only cleanup;
    # it never proves that the child service has stopped.
    if process.poll() is None:
        try:
            process.terminate()
        except OSError:
            pass
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass

    # If health had identified this exact process, wait for its endpoint to go
    # away. Otherwise a missing response is not enough to discard its token.
    if same_instance and health.get("pid") == process.pid and process.poll() is not None:
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            current = request_health(port)
            if not (health_matches(current, project_id, data_id) and current.get("instance_id") == instance_id):
                return True
            time.sleep(0.1)
    return False


def verify_runtime_python(python):
    result = subprocess.run(
        [python, "-c", "import sys, fastapi, uvicorn, pydantic; raise SystemExit(sys.version_info < (3, 11))"],
        cwd=ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=15,
        check=False,
    )
    return result.returncode == 0


def run_start(args):
    data, private, control_file, log_file = get_paths(args.data_dir)
    project_id = identity_hash(ROOT)
    data_id = identity_hash(data)
    python = selected_python()
    if not verify_runtime_python(python):
        requirements = requirements_file().name
        return start_fail(f"{python} 缺少运行依赖。执行 setup.cmd，或运行：\n  \"{python}\" -m pip install -r {requirements}", args)
    try:
        with start_lock(private):
            existing = request_health(args.port)
            if existing:
                if health_matches(existing, project_id, data_id):
                    result = {"ok": True, "running": True, "reused": True, "managed": False, "port": args.port, "url": runtime_url(args.port), "instance_id": existing.get("instance_id"), "project_id": project_id, "data_dir_id": data_id}
                    control = read_control(control_file)
                    if control and control.get("instance_id") == existing.get("instance_id"):
                        result["managed"] = True
                    if not args.no_browser:
                        webbrowser.open(runtime_url(args.port))
                    return output(result, args.json)
                return start_fail(f"端口 {args.port} 已有服务，但不是当前项目和数据目录的共席实例；未触碰该服务。", args, 2)
            if not port_is_available(args.port):
                return start_fail(f"端口 {args.port} 已被其他服务占用；未触碰该服务。", args, 2)

            token = secrets.token_urlsafe(48)
            instance_id = secrets.token_hex(16)
            control = {
                "instance_id": instance_id,
                "project_id": project_id,
                "data_dir_id": data_id,
                "management_token": token,
            }
            try:
                write_private(control_file, control)
            except Exception:
                return start_fail("无法安全保存本机管理凭证，启动已取消。", args, 4)
            log_file.parent.mkdir(parents=True, exist_ok=True)
            if log_file.is_symlink():
                return start_fail("服务日志路径是符号链接，启动已取消。", args, 4)
            try:
                # Create/tighten the log before the child can write startup
                # diagnostics, which may include local configuration details.
                with log_file.open("ab"):
                    pass
                secure_private_file(log_file)
            except Exception:
                return start_fail("无法安全限制服务日志权限，启动已取消。", args, 4)
            env = os.environ.copy()
            env.update({
                "CS_DATA_DIR": str(data),
                "GONGXI_INSTANCE_ID": instance_id,
                "GONGXI_MANAGEMENT_TOKEN": token,
                "GONGXI_PORT": str(args.port),
            })
            creationflags = 0
            popen_options = {"start_new_session": True} if os.name != "nt" else {}
            if os.name == "nt":
                creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
            try:
                with log_file.open("ab") as log:
                    process = subprocess.Popen(
                        [python, str(ROOT / "launch.py"), "--port", str(args.port), "--no-browser"],
                        cwd=ROOT,
                        env=env,
                        stdin=subprocess.DEVNULL,
                        stdout=log,
                        stderr=subprocess.STDOUT,
                        close_fds=True,
                        creationflags=creationflags,
                        **popen_options,
                    )
            except OSError:
                current = read_control(control_file)
                if current and current.get("instance_id") == instance_id:
                    control_file.unlink(missing_ok=True)
                return start_fail("无法创建服务子进程；没有启动实例。", args, 4)
            deadline = time.monotonic() + 20
            health = None
            while time.monotonic() < deadline:
                health = request_health(args.port)
                if health and health_matches(health, project_id, data_id) and health.get("instance_id") == instance_id:
                    break
                if health and not health_matches(health, project_id, data_id):
                    break
                time.sleep(0.2)
            if not health or not health_matches(health, project_id, data_id) or health.get("instance_id") != instance_id:
                if not health:
                    error = f"服务未在 20 秒内就绪；日志：{log_file}"
                else:
                    error = f"端口 {args.port} 返回了其他实例的健康信息；未停止或覆盖该服务。"
                current = read_control(control_file)
                stopped = reap_failed_start(process, args.port, instance_id, project_id, data_id, token)
                if stopped and current and current.get("instance_id") == instance_id:
                    try:
                        control_file.unlink()
                    except OSError:
                        pass
                elif not stopped:
                    error += " 启动子进程尚未确认退出，管理凭证已保留，可用 status/stop 恢复管理。"
                return start_fail(error, args, 3)
            control.update({"pid": health.get("pid"), "launcher_pid": process.pid})
            try:
                write_private(control_file, control)
            except Exception:
                # The original atomic control record still holds the valid token.
                pass
            if not args.no_browser:
                webbrowser.open(runtime_url(args.port))
            return output({"ok": True, "running": True, "reused": False, "managed": True, "port": args.port, "url": runtime_url(args.port), "instance_id": instance_id, "pid": health.get("pid"), "project_id": project_id, "data_dir_id": data_id}, args.json)
    except RuntimeError as exc:
        return start_fail(str(exc), args, 4)


def run_status(args):
    data, _, control_file, _ = get_paths(args.data_dir)
    project_id = identity_hash(ROOT)
    data_id = identity_hash(data)
    health = request_health(args.port)
    same = health_matches(health, project_id, data_id)
    control = read_control(control_file)
    managed = bool(same and control and control.get("instance_id") == health.get("instance_id") and control.get("project_id") == project_id and control.get("data_dir_id") == data_id)
    result = {
        "ok": True,
        "running": same,
        "foreign": bool(health and not same) or (not health and not port_is_available(args.port)),
        "managed": managed,
        "port": args.port,
        "url": runtime_url(args.port),
        "project_id": project_id,
        "data_dir_id": data_id,
    }
    if same:
        result.update({"instance_id": health.get("instance_id"), "pid": health.get("pid"), "version": health.get("version")})
    return output(result, args.json)


def run_stop(args):
    data, private, control_file, _ = get_paths(args.data_dir)
    project_id = identity_hash(ROOT)
    data_id = identity_hash(data)
    try:
        with start_lock(private):
            health = request_health(args.port)
            if not health:
                if not port_is_available(args.port):
                    return fail("端口仍被占用，但没有可验证的共席健康信息；未触碰该服务。", args.json, 2)
                control = read_control(control_file)
                if control:
                    return output({"ok": True, "running": False, "stopped": False, "control_retained": True, "port": args.port, "url": runtime_url(args.port)}, args.json)
                return output({"ok": True, "running": False, "stopped": False, "port": args.port, "url": runtime_url(args.port)}, args.json)
            if not health_matches(health, project_id, data_id):
                return fail("该端口服务身份不匹配；未触碰该服务。", args.json, 2)
            control = read_control(control_file)
            if not control or control.get("instance_id") != health.get("instance_id") or control.get("project_id") != project_id or control.get("data_dir_id") != data_id or not control.get("management_token"):
                return fail("此实例没有本项目保存的管理凭证，拒绝停止。", args.json, 2)
            request = urllib.request.Request(
                f"http://127.0.0.1:{args.port}/api/runtime/stop",
                data=json.dumps({"instance_id": health["instance_id"], "project_id": project_id, "data_dir_id": data_id}).encode("utf-8"),
                headers={"Content-Type": "application/json", "X-Gongxi-Management-Token": control["management_token"]},
                method="POST",
            )
            try:
                with NO_REDIRECT_OPENER.open(request, timeout=3) as response:
                    if response.status != 200:
                        return fail(f"服务拒绝优雅停止（HTTP {response.status}）。", args.json, 3)
            except urllib.error.HTTPError as exc:
                return fail(f"服务拒绝优雅停止（HTTP {exc.code}）。", args.json, 3)
            deadline = time.monotonic() + 12
            while time.monotonic() < deadline and request_health(args.port):
                time.sleep(0.2)
            if request_health(args.port):
                return fail("停止请求已发送，但服务仍在响应；未按 PID 强制终止。", args.json, 3)
            try:
                control_file.unlink()
            except OSError:
                pass
            return output({"ok": True, "running": False, "stopped": True, "port": args.port, "instance_id": health["instance_id"]}, args.json)
    except RuntimeError as exc:
        return fail(str(exc), args.json, 4)


def run_doctor(args):
    data, _, _, _ = get_paths(args.data_dir)
    python = selected_python()
    dependencies = verify_runtime_python(python)
    writable = False
    try:
        data.mkdir(parents=True, exist_ok=True)
        probe = data / f".gongxi-write-check-{secrets.token_hex(6)}"
        probe.write_text("ok", encoding="ascii")
        probe.unlink()
        writable = True
    except OSError:
        pass
    available = port_is_available(args.port)
    health = request_health(args.port)
    same_instance = health_matches(health, identity_hash(ROOT), identity_hash(data))
    result = {
        "ok": dependencies and writable and (available or same_instance),
        "running": same_instance,
        "reusable": same_instance,
        "python": python,
        "python_source": "project .venv" if str(ROOT / ".venv") in python else "PATH/current interpreter",
        "dependencies_ok": dependencies,
        "python_version": subprocess.run([python, "-c", "import platform; print(platform.python_version())"], cwd=ROOT, text=True, capture_output=True, timeout=10, check=False).stdout.strip(),
        "requirements_file": requirements_file().name,
        "port": args.port,
        "url": runtime_url(args.port),
        "port_available": available,
        "data_writable": writable,
        "project_id": identity_hash(ROOT),
        "data_dir_id": identity_hash(data),
        "suggestions": [],
    }
    if not dependencies:
        requirements = requirements_file().name
        result["suggestions"].append(f"需要 Python 3.11+ 和项目依赖。运行 setup.cmd 创建 .venv 并安装 {requirements}；或执行 \"{python}\" -m pip install -r {requirements}")
    if not available:
        if same_instance:
            result["instance_id"] = health.get("instance_id")
            result["pid"] = health.get("pid")
            result["suggestions"].append(f"本项目和数据目录的共席实例已在端口 {args.port} 运行，可直接复用：{runtime_url(args.port)}")
        else:
            result["foreign"] = True
            result["suggestions"].append(f"端口 {args.port} 已被无法验证为当前项目实例的服务占用；未触碰该服务。请检查后再选择空闲端口 --port。")
    if not writable:
        result["suggestions"].append(f"确认当前用户对数据目录有写入权限：{data}")
    return output(result, args.json, 0 if result["ok"] else 1)


def output(value, as_json=False, code=0):
    if as_json:
        print(json.dumps(value, ensure_ascii=False, separators=(",", ":")))
    else:
        for key, item in value.items():
            if key == "suggestions":
                for suggestion in item:
                    print(f"建议：{suggestion}")
            else:
                print(f"{key}: {item}")
    return code


def fail(message, as_json=False, code=1):
    return output({"ok": False, "error": message}, as_json, code)


def start_fail(message, args, code=1):
    return output({"ok": False, "error": message, "url": runtime_url(args.port)}, args.json, code)


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="共席本地服务运行管理")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("start", "status", "stop", "doctor"):
        sub = subparsers.add_parser(command)
        sub.add_argument("--port", type=int, default=4284)
        sub.add_argument("--data-dir", default=None)
        sub.add_argument("--no-browser", action="store_true", help="start 时不打开浏览器")
        sub.add_argument("--json", action="store_true", help="输出单行 JSON")
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        return fail("端口必须在 1–65535 之间", args.json)
    return {"start": run_start, "status": run_status, "stop": run_stop, "doctor": run_doctor}[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
