"""桌面壳 ↔ HTTP 层之间的桥。

为什么要有这层：`api.py` 是纯 Web 层，headless 模式（开发调试、pytest）下也要能跑；
但「显示窗口」「开关机自启」这些动作只有桌面壳做得到。
如果让 api.py 直接 import webview / pystray，headless 就会因为缺 GUI 依赖而崩。

所以：desktop.py 启动时把能力注册进这张表，api.py 只按名字调用，
查不到就是「当前形态不支持」，返回明确的降级结果，不抛。
"""

from __future__ import annotations

from typing import Any, Callable

_handlers: dict[str, Callable[..., Any]] = {}


class HostUnavailable(RuntimeError):
    """当前没跑在桌面壳里，能力不存在。"""


def register(name: str, fn: Callable[..., Any]) -> None:
    _handlers[name] = fn


def unregister(name: str) -> None:
    _handlers.pop(name, None)


def available(name: str) -> bool:
    return name in _handlers


def call(name: str, *args: Any, **kwargs: Any) -> Any:
    fn = _handlers.get(name)
    if fn is None:
        raise HostUnavailable(name)
    return fn(*args, **kwargs)
