"""Run the local Common Ground workspace in the foreground."""
import argparse
import os
from pathlib import Path
import sys
import threading
import time
import urllib.request
import webbrowser

ROOT = Path(__file__).resolve().parent


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="共席 · 本地协作空间")
    parser.add_argument("--port", type=int, default=int(os.environ.get("GONGXI_PORT", "4284")))
    parser.add_argument("--data-dir", default=os.environ.get("CS_DATA_DIR"))
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("端口必须在 1–65535 之间")

    os.chdir(ROOT)
    try:
        import uvicorn
        from backend.app import create_app
    except ImportError:
        print("缺少运行依赖。请在项目目录执行：", flush=True)
        print(f'"{sys.executable}" -m pip install -r requirements.txt', flush=True)
        return 1

    data_dir = Path(args.data_dir or ROOT / "data").expanduser().resolve()
    os.environ["CS_DATA_DIR"] = str(data_dir)
    os.environ["GONGXI_PORT"] = str(args.port)
    app = create_app(data_dir)
    config = uvicorn.Config(app, host="127.0.0.1", port=args.port, log_level="info", access_log=False)
    server = uvicorn.Server(config)
    app.state.runtime_shutdown = lambda: setattr(server, "should_exit", True)

    url = f"http://127.0.0.1:{args.port}"
    print(f"共席 · 纸间研究工作室\n{url}\n数据目录已配置。按 Ctrl+C 停止。", flush=True)

    def open_when_ready():
        for _ in range(80):
            try:
                with urllib.request.urlopen(url + "/api/health", timeout=1) as response:
                    if response.status == 200:
                        webbrowser.open(url)
                        return
            except OSError:
                time.sleep(0.25)

    if not args.no_browser:
        threading.Thread(target=open_when_ready, daemon=True).start()
    server.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
