"""壁纸：本地图片/视频，以及从 Wallpaper Engine 取当前壁纸。

一个绕不过去的事实：WE 的壁纸是渲染在桌面层（WorkerW 窗口）的，
第三方程序**截不到画面流**。能拿到的是「壁纸项目本身」——
读 WE 配置找到当前项目，video 类直接播它的视频文件，picture 类轮播图片。
scene（粒子/骨骼）和 web 类要靠 WE 自己的引擎，做不到，直接告诉用户。
"""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path

VIDEO_EXT = {".mp4", ".webm", ".mov", ".mkv"}
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}

MAX_PICTURE = 20          # 轮播最多取这么多张，再多没意义还费内存


def _steam_path() -> Path | None:
    """Steam 安装路径，从注册表拿。"""
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as k:
            raw = winreg.QueryValueEx(k, "SteamPath")[0]
    except OSError:
        return None
    p = Path(raw)
    return p if p.is_dir() else None


def we_dir() -> Path | None:
    steam = _steam_path()
    if steam is None:
        return None
    p = steam / "steamapps" / "common" / "wallpaper_engine"
    return p if p.is_dir() else None


def _current_we_entry(we: Path) -> tuple[str | None, str | None]:
    """从 WE 的 config.json 读当前壁纸，返回 (workshop_id, 文件路径)。

    这个文件有两种结构：老版写在 `steamuser` 下，新版按 Steam 用户 id 平铺
    （实测本机是 `{"28903": {...}, "?installdirectory": "..."}`）。
    选中值也有两种：`"id"` 或 `{"file": "D:/.../431960/<id>/scene.pkg"}`。
    """
    cfg = we / "config.json"
    if not cfg.is_file():
        return None, None
    try:
        data = json.loads(cfg.read_text(encoding="utf-8", errors="ignore"))
    except (OSError, json.JSONDecodeError):
        return None, None

    nodes = []
    if isinstance(data.get("steamuser"), dict):
        nodes.append(data["steamuser"])
    for key, val in data.items():
        if key.startswith("?") or not isinstance(val, dict):
            continue
        nodes.append(val)

    for node in nodes:
        sel = (
            node.get("general", {})
            .get("wallpaperconfig", {})
            .get("selectedwallpapers", {})
        )
        if not isinstance(sel, dict) or not sel:
            continue
        # 主屏优先，其次是任意一个屏
        val = sel.get("monitor0") or sel.get("Monitor1") or next(iter(sel.values()))
        path = val.get("file") if isinstance(val, dict) else val
        if not path:
            continue
        parts = Path(str(path)).parts
        wid = None
        for i, p in enumerate(parts):
            if p == "431960" and i + 1 < len(parts):   # WE 的工坊 appid
                wid = parts[i + 1]
                break
        return wid, str(path)
    return None, None


def _project_dir(we: Path, wid: str) -> Path | None:
    """壁纸项目的实际目录。工坊订阅和本地自制都试一遍。"""
    cands = [
        we / "projects" / "workshop" / "content" / "431960" / wid,
        we / "projects" / "defaultprojects" / wid,
        we / "projects" / "myprojects" / wid,
    ]
    steam = _steam_path()
    if steam is not None:
        # 工坊订阅实际落在这里，不在 wallpaper_engine 目录下
        cands.insert(0, steam / "steamapps" / "workshop" / "content" / "431960" / wid)
    for cand in cands:
        if (cand / "project.json").is_file():
            return cand
    return None


def _media_in(folder: Path, want: str) -> list[Path]:
    exts = VIDEO_EXT if want == "video" else IMAGE_EXT
    files = [p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in exts]
    return sorted(files, key=lambda p: p.name)


def workshop_dir() -> Path | None:
    steam = _steam_path()
    if steam is None:
        return None
    p = steam / "steamapps" / "workshop" / "content" / "431960"
    return p if p.is_dir() else None


def preview_file(folder: Path) -> Path | None:
    """壁纸项目的预览图。WE 自己就靠 preview.* 在库里显示缩略图。"""
    for name in ("preview.jpg", "preview.png", "preview.gif", "preview.webp", "preview.jpeg"):
        p = folder / name
        if p.is_file():
            return p
    imgs = _media_in(folder, "image")   # 没有 preview 就退而用项目里的第一张图
    return imgs[0] if imgs else None


def _folder_media(folder: Path, meta: dict, kind: str) -> list[str]:
    """从一个壁纸项目里取出可播放的文件。视频取主的那个，图片按名称排。"""
    if kind == "video":
        name = meta.get("file")
        target = folder / name if name else None
        if target and target.is_file():
            return [str(target)]
        return [str(p) for p in _media_in(folder, "video")[:1]]
    return [str(p) for p in _media_in(folder, "image")[:MAX_PICTURE]]


def _read_meta(folder: Path) -> dict:
    pj = folder / "project.json"
    if not pj.is_file():
        return {}
    try:
        meta = json.loads(pj.read_text(encoding="utf-8", errors="ignore"))
        return meta if isinstance(meta, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def list_we_wallpapers() -> list[dict]:
    """扫工坊，列出能直接播的壁纸（video / picture）。

    scene 和 web 靠 WE 自己的引擎，取过来也放不了，直接不列，
    免得用户点了个用不了的再回来问。
    """
    steam = _steam_path()
    if steam is None:
        return []
    base = workshop_dir()
    if base is None:
        return []

    out: list[dict] = []
    for folder in sorted(base.iterdir()):
        if not folder.is_dir():
            continue
        meta = _read_meta(folder)
        kind = str(meta.get("type") or "").lower()
        if kind not in ("video", "picture"):
            continue
        files = _folder_media(folder, meta, kind)
        if not files:
            continue
        out.append(
            {
                "id": folder.name,
                "title": str(meta.get("title") or folder.name),
                "kind": kind,
                "files": files,
            }
        )
    return out


def probe_we(project_id: str | None = None) -> dict:
    """探测 Wallpaper Engine 壁纸，返回可直接用的结果。

    不传 project_id 时取「当前正在用的那张」；传了就取指定工坊项目。
    返回 {ok, kind, files, title, reason}：
      kind = video | picture | unsupported
    """
    we = we_dir()
    if we is None:
        return {"ok": False, "reason": "没找到 Wallpaper Engine 安装目录"}

    if project_id:
        folder = _project_dir(we, project_id)
        if folder is None:
            return {"ok": False, "reason": f"找不到这个壁纸项目（id={project_id}）"}
        meta = _read_meta(folder)
        kind = str(meta.get("type") or "").lower()
        title = str(meta.get("title") or folder.name)
        if kind not in ("video", "picture"):
            return {"ok": False, "reason": f"这张是 {kind or '未知'} 类型，放不了"}
        files = _folder_media(folder, meta, kind)
        if not files:
            return {"ok": False, "reason": "项目里没找到可播放的文件"}
        return {"ok": True, "kind": kind, "files": files, "title": title}

    wid, cur_file = _current_we_entry(we)
    if not wid and not cur_file:
        return {"ok": False, "reason": "WE 配置里没有当前壁纸记录（可能没运行过）"}

    # 选中值直接给了路径就用它的父目录，比按 id 拼路径可靠
    folder = None
    if cur_file:
        parent = Path(cur_file).parent
        if (parent / "project.json").is_file():
            folder = parent
    if folder is None and wid:
        folder = _project_dir(we, wid)
    if folder is None:
        return {"ok": False, "reason": f"找不到壁纸项目目录（id={wid or '?'}）"}

    meta = _read_meta(folder)
    kind = str(meta.get("type") or "").lower()
    title = str(meta.get("title") or folder.name)

    if kind in ("video", "picture"):
        files = _folder_media(folder, meta, kind)
        if not files:
            return {"ok": False, "reason": "项目里没找到可播放的文件"}
        return {"ok": True, "kind": kind, "files": files, "title": title}

    if kind in ("scene", "web"):
        return {
            "ok": False,
            "reason": f"当前是 {kind} 壁纸，需要 WE 自己的渲染引擎，这里放不了。"
                      "可以从工坊里另选一个 video / picture 类型的。",
            "kind": kind,
        }

    return {"ok": False, "reason": f"未知的壁纸类型：{kind or '空'}"}


def store_upload(root: Path, filename: str, data: bytes) -> Path:
    """把上传的壁纸落到数据目录，清掉上一张，避免越攒越多。"""
    folder = Path(root) / "wallpaper"
    folder.mkdir(parents=True, exist_ok=True)
    for old in folder.iterdir():
        if old.is_file():
            try:
                old.unlink()
            except OSError:
                pass
    ext = Path(filename).suffix.lower() or ".bin"
    target = folder / ("wall" + ext)
    target.write_bytes(data)
    return target


def data_url(path: Path, limit: int = 8_000_000) -> str | None:
    """小图直接内联成 data URL，省一次请求；视频一律走流式接口。"""
    try:
        if path.suffix.lower() in VIDEO_EXT or path.stat().st_size > limit:
            return None
        ext = path.suffix.lower().lstrip(".")
        mime = {"jpg": "jpeg", "jpeg": "jpeg", "png": "png", "webp": "webp",
                "gif": "gif", "bmp": "bmp"}.get(ext, "png")
        return f"data:image/{mime};base64," + base64.b64encode(path.read_bytes()).decode()
    except OSError:
        return None


def is_video(p: Path) -> bool:
    return p.suffix.lower() in VIDEO_EXT


def resolve_files(raw: str) -> list[Path]:
    """settings 里存的是 JSON 数组，反序列化后只保留还存在的文件。"""
    try:
        items = json.loads(raw or "[]")
    except json.JSONDecodeError:
        return []
    if not isinstance(items, list):
        return []
    out: list[Path] = []
    for s in items:
        p = Path(str(s))
        if p.is_file() and os.access(p, os.R_OK):
            out.append(p)
    return out
