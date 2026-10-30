"""启动入口：找端口 → 起本地服务 → 开浏览器。"""

from __future__ import annotations

import os
import socket
import sys
import threading
import webbrowser

from .api import create_app


def free_port(start: int = 8765, end: int = 8785) -> int:
    for p in range(start, end + 1):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(("127.0.0.1", p))
                return p
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
    app = create_app()
    port = free_port()
    url = f"http://127.0.0.1:{port}/"

    print(f"刷题调度 已启动：{url}")
    print(f"数据目录：{app.config['ROOT']}")
    print("关闭这个窗口即退出。")

    threading.Timer(0.8, lambda: _open(url)).start()

    try:
        from waitress import serve

        serve(app, host="127.0.0.1", port=port, threads=4)
    except ImportError:
        app.run(host="127.0.0.1", port=port, debug=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
