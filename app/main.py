"""启动入口：找端口 → 起本地服务 → 开窗口（或浏览器）。

三种形态：
  默认        桌面壳：原生窗口 + 托盘常驻（打包产物走这条，无黑窗）
  --headless  只起服务 + 系统浏览器，前台阻塞（开发调试用，Ctrl+C 结束）
  --tray      只进托盘不弹窗口（开机自启写的就是这条）
"""

from __future__ import annotations

import json
import socket
import sys
import threading
import time
import urllib.request
import webbrowser

from .api import create_app

APP_MARK = "刷题调度"
PORT_START, PORT_END = 8765, 8785

HEALTH_TIMEOUT = 8.0


def _log(msg: str) -> None:
    """console=False 打包后 sys.stdout 是 None，裸 print 会抛 AttributeError。"""
    try:
        print(msg, flush=True)
    except Exception:
        pass


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


def wake_instance(port: int) -> bool:
    """让已在跑的实例把窗口显出来。老版本没有这个接口，返回 False 交给浏览器兜底。"""
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/ui/show", method="POST", data=b""
    )
    try:
        with urllib.request.urlopen(req, timeout=1.5) as resp:
            return 200 <= resp.status < 300
    except Exception:
        return False


def wait_health(port: int, timeout: float = HEALTH_TIMEOUT) -> bool:
    """等本地服务真正能应答再开窗，否则窗口会先吃到 502 白屏。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/api/health", timeout=0.5
            ) as resp:
                if json.loads(resp.read().decode("utf-8")).get("app") == APP_MARK:
                    return True
        except Exception:
            time.sleep(0.15)
    return False


def serve_blocking(app, port: int) -> None:
    try:
        from waitress import serve

        serve(app, host="127.0.0.1", port=port, threads=4)
    except ImportError:
        app.run(host="127.0.0.1", port=port, debug=False)


def _open(url: str) -> None:
    try:
        webbrowser.open(url)
    except Exception:
        pass


def main() -> int:
    args = sys.argv[1:]
    headless = any(a in args for a in ("--headless", "-H"))
    silent = any(a in args for a in ("--silent", "-s"))
    # --silent 在桌面形态下等于「只进托盘」：旧版开机启动脚本写的就是它
    tray_only = any(a in args for a in ("--tray", "--minimized", "--silent", "-s"))

    # 单实例：第二次双击只负责把已有窗口叫到前面
    running = existing_instance()
    if running:
        url = f"http://127.0.0.1:{running}/"
        _log(f"已有实例在跑：{url}")
        if not silent:
            if not wake_instance(running):
                _open(url)  # 旧版本没有 /api/ui/show，退回开浏览器
        return 0

    app = create_app()
    port = free_port()
    url = f"http://127.0.0.1:{port}/"

    if headless:
        _log(f"刷题调度 已启动：{url}")
        _log(f"数据目录：{app.config['ROOT']}")
        _log("关闭这个窗口即退出。")
        if not silent:
            threading.Timer(0.8, lambda: _open(url)).start()
        serve_blocking(app, port)
        return 0

    # 桌面形态：服务放 daemon 线程，主线程留给 GUI 消息循环
    threading.Thread(
        target=serve_blocking, args=(app, port), name="server", daemon=True
    ).start()
    if not wait_health(port):
        _log("服务没有在预期时间内就绪，退出")
        return 1

    from .desktop import DesktopShell

    DesktopShell(app, port).run(minimized=tray_only)
    return 0


if __name__ == "__main__":
    sys.exit(main())
