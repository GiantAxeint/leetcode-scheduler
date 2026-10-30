"""启动入口：找端口 → 起本地服务 → 开浏览器。"""

from __future__ import annotations

import json
import os
import socket
import sys
import threading
import urllib.request
import webbrowser

from .api import create_app

APP_MARK = "刷题调度"
PORT_START, PORT_END = 8765, 8785


def existing_instance() -> int | None:
    """已经有一个实例在跑就复用它，别起第二个去抢同一个库。"""
    for port in range(PORT_START, PORT_END + 1):
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/api/health", timeout=0.4
            ) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            if data.get("app") == APP_MARK:
                return port
        except Exception:
            continue
    return None


def free_port() -> int:
    """注意：Windows 上不能带 SO_REUSEADDR，否则占用中的端口也能 bind 成功。"""
    for port in range(PORT_START, PORT_END + 1):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise RuntimeError("没有可用端口")


def _open(url: str) -> None:
    """SCJ_NO_BROWSER=1 时不弹浏览器（调试/自动测试用）。"""
    if os.environ.get("SCJ_NO_BROWSER"):
        return
    try:
        webbrowser.open(url)
    except Exception:
        pass


def main() -> int:
    silent = any(a in sys.argv[1:] for a in ("--silent", "-s"))

    running = existing_instance()
    if running:
        url = f"http://127.0.0.1:{running}/"
        print(f"已经有一个实例在跑：{url}")
        if not silent:
            threading.Timer(0.5, lambda: _open(url)).start()
            threading.Timer(1.2, lambda: os._exit(0)).start()
            threading.Event().wait(3)
        return 0

    app = create_app()
    port = free_port()
    url = f"http://127.0.0.1:{port}/"

    print(f"刷题调度 已启动：{url}")
    print(f"数据目录：{app.config['ROOT']}")
    print("关闭这个窗口即退出。")

    if not silent:
        threading.Timer(0.8, lambda: _open(url)).start()

    try:
        from waitress import serve

        serve(app, host="127.0.0.1", port=port, threads=4)
    except ImportError:
        app.run(host="127.0.0.1", port=port, debug=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
