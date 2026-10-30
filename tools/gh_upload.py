"""用 GitHub API 上传仓库（绕开被拦的 git 协议）。

思路：本地每个 commit 的 tree / blob 的 SHA 与 GitHub 算法一致，
所以按历史顺序逐个把 blob → tree → commit 重建上去，重建出来的 commit SHA
和本地完全一样，之后正常 git push 不会冲突。

用法：
    python tools/gh_upload.py <owner/repo> [分支名]
"""

from __future__ import annotations

import base64
import json
import subprocess
import sys


def sh(*args: str, binary: bool = False):
    out = subprocess.run(args, capture_output=True, check=True)
    return out.stdout if binary else out.stdout.decode("utf-8", "replace")


def gh(path: str, method: str = "GET", payload: dict | None = None):
    cmd = ["gh", "api", "-X", method, path]
    if payload is not None:
        cmd += ["--input", "-"]
    proc = subprocess.run(
        cmd, input=json.dumps(payload).encode() if payload else None, capture_output=True
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.decode("utf-8", "replace")[:400])
    txt = proc.stdout.decode("utf-8", "replace").strip()
    return json.loads(txt) if txt else {}


def commits() -> list[str]:
    return sh("git", "rev-list", "--reverse", "HEAD").split()


def parse_commit(sha: str) -> dict:
    raw = sh("git", "cat-file", "-p", sha)
    head, _, message = raw.partition("\n\n")
    info = {"tree": None, "parents": [], "author": None, "committer": None, "message": message}
    for line in head.splitlines():
        if line.startswith("tree "):
            info["tree"] = line[5:]
        elif line.startswith("parent "):
            info["parents"].append(line[7:])
        elif line.startswith("author "):
            info["author"] = line[7:]
        elif line.startswith("committer "):
            info["committer"] = line[10:]
    return info


def split_ident(s: str) -> dict:
    # 形如：Erius <a@b.c> 1761800000 +0800
    import datetime as dt

    name_email, _, ts = s.rpartition("> ")
    name, _, email = name_email.partition(" <")
    seconds, tz = ts.split()
    sign = -1 if tz.startswith("-") else 1
    offset = dt.timedelta(hours=int(tz[1:3]), minutes=int(tz[3:5])) * sign
    stamp = dt.datetime.fromtimestamp(int(seconds), dt.timezone(offset))
    return {
        "name": name,
        "email": email.rstrip(">"),
        "date": stamp.strftime("%Y-%m-%dT%H:%M:%S%z")[:-2] + ":" + stamp.strftime("%z")[-2:],
    }


def ensure_initialized(repo: str) -> None:
    """空仓库上 Git Data API 会 409，先用 Contents API 造一个引导提交。"""
    try:
        gh(f"/repos/{repo}/commits?per_page=1")
        return
    except RuntimeError as e:
        if "409" not in str(e):
            raise
    gh(
        f"/repos/{repo}/contents/.gitkeep",
        "PUT",
        {"message": "chore: init", "content": base64.b64encode(b"").decode()},
    )
    print("空仓库已初始化")


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    repo = sys.argv[1]
    branch = sys.argv[2] if len(sys.argv) > 2 else "master"

    ensure_initialized(repo)

    uploaded: set[str] = set()
    hist = commits()
    last_new = hist[-1]
    print(f"{len(hist)} 个提交待上传")

    for idx, sha in enumerate(hist, 1):
        info = parse_commit(sha)

        entries = []
        for line in sh("git", "ls-tree", "-r", sha).splitlines():
            meta, path = line.split("\t", 1)
            mode, typ, blob = meta.split()
            entries.append({"path": path, "mode": mode, "type": typ, "sha": blob})

        for e in entries:
            if e["sha"] in uploaded:
                continue
            content = sh("git", "cat-file", "blob", e["sha"], binary=True)
            gh(
                f"/repos/{repo}/git/blobs",
                "POST",
                {"content": base64.b64encode(content).decode(), "encoding": "base64"},
            )
            uploaded.add(e["sha"])

        tree = gh(f"/repos/{repo}/git/trees", "POST", {"tree": entries})
        if tree["sha"] != info["tree"]:
            raise RuntimeError(f"tree 重建不一致：{tree['sha']} != {info['tree']}")

        payload = {
            "message": info["message"],
            "tree": tree["sha"],
            "parents": info["parents"],
            "author": split_ident(info["author"]),
            "committer": split_ident(info["committer"]),
        }
        new = gh(f"/repos/{repo}/git/commits", "POST", payload)
        last_new = new["sha"]
        print(f"[{idx}/{len(hist)}] {new['sha'][:8]} {info['message'].splitlines()[0][:40]}")

    try:
        gh(
            f"/repos/{repo}/git/refs",
            "POST",
            {"ref": f"refs/heads/{branch}", "sha": last_new},
        )
    except RuntimeError:
        # 分支已存在（引导提交建的），强制指到我们的历史
        gh(
            f"/repos/{repo}/git/refs/heads/{branch}",
            "PATCH",
            {"sha": last_new, "force": True},
        )
    print(f"已创建分支 {branch} @ {last_new[:8]}")
    print(f"https://github.com/{repo}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
