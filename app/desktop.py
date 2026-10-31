"""桌面壳：本地服务 + 原生窗口 + 托盘常驻。

进程结构（这里是最容易写错的地方）：

    主线程   ── webview.start()，Win32 窗口消息循环，必须占主线程，直到窗口被 destroy
    服务线程 ── waitress 跑 Flask，daemon
    托盘线程 ── pystray 通知区图标，daemon

三条线程互不阻塞。点窗口 X 默认只 hide() 不 destroy，服务照常活着，
所以 Edge 扩展在这期间仍能录用题目。

退出只有一条路：托盘菜单「退出」→ 停托盘 + destroy 窗口 + os._exit。
os._exit 是必要的兜底：waitress 的 serve() 没有优雅停机接口，
webview.start() 返回后主线程也可能被残留线程吊住。
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
import urllib.request
import webbrowser
from datetime import datetime
from pathlib import Path

from . import host, paths

APP_MARK = "刷题调度"
GUI_BACKEND = "edgechromium"  # 明确指定，避免 pywebview 在 Windows 上回落到 mshtml 老内核

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
AUTOSTART_NAME = "刷题调度"

PROTOCOL_NAME = "scj"  # 扩展里对应的地址是 scj://open

LOG_MAX_BYTES = 1_000_000

# 环回请求绕开系统代理，理由同 main.py：开着 Clash 时 http_proxy 会把
# 127.0.0.1 也转出去，读设置直接读成 502
_LOCAL_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


# ---------------------------------------------------------------- 日志
class ShellLog:
    """无控制台时唯一能看到错误的地方，所以必须落盘。

    `console=False` 打包后 sys.stdout 是 None，裸 print 会抛 AttributeError ——
    这也是为什么全项目不允许直接 print。
    """

    def __init__(self, root: Path) -> None:
        self.path = Path(root) / "desktop.log"

    def write(self, msg: str) -> None:
        line = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"
        try:
            print(line, flush=True)
        except Exception:
            pass
        try:
            if self.path.is_file() and self.path.stat().st_size > LOG_MAX_BYTES:
                self.path.replace(self.path.with_suffix(".log.1"))
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except Exception:
            pass


# ---------------------------------------------------------------- 开机自启
def _launch_command() -> str:
    """开机自启和 scj:// 协议都用这一条命令：一律 --tray，只进托盘不弹窗。"""
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" --tray'
    py = Path(sys.executable)
    pyw = py.with_name("pythonw.exe")
    entry = paths.base_dir() / "run.py"
    return f'"{pyw if pyw.is_file() else py}" "{entry}" --tray'


def autostart_command() -> str:
    return _launch_command()


# ---------------------------------------------------------------- 自定义协议
def register_protocol() -> bool:
    """把 scj:// 挂到本程序，扩展靠它把没开的程序拉起来。

    写 HKCU 不需要管理员权限，CreateKey 幂等，所以每次启动都刷一遍最省事
    —— 用户搬了目录或换了 exe 位置，下一次启动就自动修好。
    """
    import winreg

    base = rf"Software\Classes\{PROTOCOL_NAME}"
    icon_exe = (
        sys.executable
        if getattr(sys, "frozen", False)
        else str(Path(sys.executable).with_name("pythonw.exe"))
    )
    try:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, base) as key:
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, "URL:刷题调度")
            winreg.SetValueEx(key, "URL Protocol", 0, winreg.REG_SZ, "")
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, base + r"\DefaultIcon") as key:
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, f"{icon_exe},0")
        with winreg.CreateKey(
            winreg.HKEY_CURRENT_USER, base + r"\shell\open\command"
        ) as key:
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, _launch_command())
        return True
    except OSError:
        return False


def _startup_lnk() -> Path | None:
    """旧版 tools/install_autostart.py 走的是启动文件夹快捷方式。

    两处都要认：只认注册表的话，用户以前用脚本装过的快捷方式会残留，
    在设置里关掉自启后开机照样启动，看起来像开关坏了。
    """
    appdata = os.environ.get("APPDATA")
    if not appdata:
        return None
    return (
        Path(appdata)
        / "Microsoft"
        / "Windows"
        / "Start Menu"
        / "Programs"
        / "Startup"
        / "刷题调度.lnk"
    )


def autostart_enabled() -> bool:
    lnk = _startup_lnk()
    if lnk is not None and lnk.is_file():
        return True
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.QueryValueEx(key, AUTOSTART_NAME)
            return True
    except FileNotFoundError:
        return False
    except OSError:
        return False


def set_autostart(enabled: bool) -> bool:
    """返回写入后的真实状态，不假设成功。"""
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE
        ) as key:
            if enabled:
                winreg.SetValueEx(
                    key, AUTOSTART_NAME, 0, winreg.REG_SZ, autostart_command()
                )
            else:
                try:
                    winreg.DeleteValue(key, AUTOSTART_NAME)
                except FileNotFoundError:
                    pass
    except OSError:
        pass

    if not enabled:
        lnk = _startup_lnk()
        if lnk is not None:
            try:
                lnk.unlink(missing_ok=True)
            except OSError:
                pass
    return autostart_enabled()


# ---------------------------------------------------------------- 桌面壳
class DesktopShell:
    def __init__(self, app, port: int) -> None:
        self.app = app
        self.port = port
        self.url = f"http://127.0.0.1:{port}/"
        self.root = Path(app.config["ROOT"])
        self.log = ShellLog(self.root)

        self.window = None
        self.tray = None
        self._quitting = False
        self._tray_ok = False
        self._hinted = False

    # ---------- 配置 ----------
    def close_action(self) -> str:
        """关窗行为存在 settings 表里（tray / exit）。

        走 HTTP 读而不是直接连库：api 层已经负责建表和迁移，
        这里再开一个连接会在老库首次启动时和它抢写锁（交接文档记过这个坑）。
        """
        try:
            with _LOCAL_OPENER.open(
                f"http://127.0.0.1:{self.port}/api/settings", timeout=1.0
            ) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            action = (data.get("settings") or {}).get("close_action") or "tray"
            return action if action in ("tray", "exit") else "tray"
        except Exception:
            return "tray"

    # ---------- 窗口 ----------
    def show_window(self, *_args) -> bool:
        """从托盘唤起。可能来自托盘线程，也可能来自 HTTP 线程。"""
        if self.window is None:
            return False
        try:
            self.window.show()
        except Exception as exc:
            self.log.write(f"显示窗口失败: {exc}")
            return False
        try:
            # 被最小化过就再恢复一次；没最小化时这步会抛，忽略即可
            self.window.restore()
        except Exception:
            pass
        return True

    def _on_closing(self, *_args):
        """返回 False 阻止关闭（pywebview 的约定）。"""
        if self._quitting:
            return True
        # 托盘没起来还 hide 等于把窗口弄丢，这种情况下必须放行退出
        if not self._tray_ok or self.close_action() == "exit":
            self._quitting = True
            self.log.write("关窗 → 退出")
            return True
        try:
            self.window.hide()
        except Exception as exc:
            self.log.write(f"隐藏窗口失败: {exc}")
            return True
        self._hint_once()
        return False

    def _hint_once(self) -> None:
        """第一次缩托盘给个气泡，否则用户会以为程序被关了。"""
        if self._hinted:
            return
        self._hinted = True
        try:
            self.tray.notify("已缩到托盘，双击图标可再打开", "刷题调度")
        except Exception:
            pass

    # ---------- 托盘 ----------
    def _tray_image(self):
        from PIL import Image, ImageDraw

        try:
            p = paths.icon_path()
            if p is not None:
                return Image.open(p).convert("RGBA").resize((64, 64))
        except Exception as exc:
            self.log.write(f"托盘图标加载失败: {exc}")
        # 兜底：画一个方块，保证托盘一定起得来（图标加载失败不该导致整个程序异常）
        img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        ImageDraw.Draw(img).rounded_rectangle((4, 4, 60, 60), 14, fill=(31, 111, 235, 255))
        return img

    def _open_root(self, *_args) -> None:
        try:
            os.startfile(str(self.root))  # type: ignore[attr-defined]
        except Exception as exc:
            self.log.write(f"打开数据目录失败: {exc}")

    def _open_browser(self, *_args) -> None:
        try:
            webbrowser.open(self.url)
        except Exception as exc:
            self.log.write(f"打开浏览器失败: {exc}")

    def _toggle_autostart(self, *_args) -> None:
        now = set_autostart(not autostart_enabled())
        self.log.write(f"开机自启 → {'开' if now else '关'}")
        try:
            self.tray.notify(
                "已开启开机自启" if now else "已关闭开机自启", "刷题调度"
            )
        except Exception:
            pass

    def quit(self, *_args) -> None:
        self._quitting = True
        self.log.write("托盘退出")
        try:
            if self.tray is not None:
                self.tray.stop()
        except Exception:
            pass
        try:
            if self.window is not None:
                self.window.destroy()
        except Exception:
            pass
        time.sleep(0.15)
        os._exit(0)

    def _build_tray(self) -> None:
        import pystray

        menu = pystray.Menu(
            pystray.MenuItem("显示主窗口", self.show_window, default=True),
            pystray.MenuItem("在浏览器中打开", self._open_browser),
            pystray.MenuItem("打开数据目录", self._open_root),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                "开机自启", self._toggle_autostart,
                checked=lambda _item: autostart_enabled(),
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("退出", self.quit),
        )
        self.tray = pystray.Icon("scj", self._tray_image(), "刷题调度", menu)

        def _run() -> None:
            try:
                self.tray.run()  # 阻塞，直到 stop()
            except Exception as exc:  # 通知区不可用（远程桌面/精简系统）
                self._tray_ok = False
                self.log.write(f"托盘启动失败: {exc}")

        threading.Thread(target=_run, name="tray", daemon=True).start()
        # run() 是异步的，给一个短暂窗口确认线程没立刻挂掉
        for _ in range(20):
            time.sleep(0.05)
            if self.tray is not None and getattr(self.tray, "visible", False):
                break
        self._tray_ok = bool(getattr(self.tray, "visible", False))
        self.log.write(f"托盘状态: {'ok' if self._tray_ok else '失败'}")

    # ---------- 主循环 ----------
    def run(self, minimized: bool = False) -> None:
        import webview

        # 把只有桌面形态才有的能力登记进桥，api.py 按名字调
        host.register("show_window", self.show_window)
        host.register("autostart_enabled", autostart_enabled)
        host.register("autostart_set", set_autostart)
        host.register("close_action", self.close_action)

        # 扩展靠这个协议把没开的程序拉起来；注册失败不影响本机使用
        if not register_protocol():
            self.log.write("scj:// 协议注册失败，扩展无法自动拉起程序")

        ico = paths.icon_path()
        self.window = webview.create_window(
            "刷题调度",
            self.url,
            width=1180,
            height=820,
            min_size=(880, 620),
            hidden=minimized,
            background_color="#F7F8FA",
        )
        self.window.events.closing += self._on_closing

        self._build_tray()
        self.log.write(f"窗口启动 {self.url}")

        try:
            webview.start(gui=GUI_BACKEND, icon=str(ico) if ico else None)
        except Exception as exc:
            self.log.write(f"窗口启动失败: {exc}")
            raise

        # start() 返回只有一种情况：窗口被 destroy 了，跟着收摊
        self._quitting = True
        try:
            if self.tray is not None:
                self.tray.stop()
        except Exception:
            pass
        os._exit(0)
