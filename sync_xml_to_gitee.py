#!/usr/bin/env python3
"""把配置中的最终 RSS XML 按内容差异同步到 Gitee。"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DOCS = ROOT / "docs"
API_ROOT = "https://gitee.com/api/v5"
USER_AGENT = "journal-rss-relay-gitee-xml-sync/1.0"
MAX_XML_BYTES = 2 * 1024 * 1024


class SyncError(RuntimeError):
    """同步操作无法安全完成。"""


def configured_xml_files(config: dict) -> list[tuple[str, str]]:
    """返回 (文件名, 提交说明)，只接受根目录下的 XML 文件名。"""
    outputs: list[tuple[str, str]] = []
    for journal in config["journals"]:
        outputs.append((journal["output_file"], journal["commit_label"]))
        translation = journal.get("translation")
        if translation:
            outputs.append((translation["output_file"], translation["commit_label"]))
    filenames = [filename for filename, _ in outputs]
    if len(filenames) != len(set(filenames)):
        raise SyncError("配置中存在重复 XML 文件名")
    for filename, label in outputs:
        if Path(filename).name != filename or Path(filename).suffix.casefold() != ".xml":
            raise SyncError("配置中存在不安全的 XML 文件名")
        if not str(label).strip() or "\n" in str(label) or "\r" in str(label):
            raise SyncError("XML 提交说明不合法")
    return outputs


def validated_local_xml(filename: str) -> bytes:
    path = DOCS / filename
    raw = path.read_bytes()
    if not raw or len(raw) > MAX_XML_BYTES:
        raise SyncError(f"{filename}: 文件为空或过大")
    root = ET.fromstring(raw)
    if root.tag != "rss" or root.get("version") != "2.0":
        raise SyncError(f"{filename}: 不是 RSS 2.0")
    channel = root.find("channel")
    if channel is None or not channel.findall("item"):
        raise SyncError(f"{filename}: 没有文献条目")
    return raw


def request_json(
    method: str,
    url: str,
    token: str,
    form: dict[str, str] | None = None,
    expected: tuple[int, ...] = (200,),
) -> tuple[int, dict]:
    body = None
    headers = {
        "Accept": "application/json",
        "Authorization": f"token {token}",
        "User-Agent": USER_AGENT,
    }
    if form is not None:
        body = urllib.parse.urlencode(form).encode("utf-8")
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            status = response.status
            payload = response.read(4 * 1024 * 1024)
    except urllib.error.HTTPError as exc:
        status = exc.code
        if status == 404 and 404 in expected:
            return status, {}
        raise SyncError(f"Gitee API 返回 HTTP {status}") from None
    except (OSError, urllib.error.URLError) as exc:
        raise SyncError(f"无法连接 Gitee API：{type(exc).__name__}") from None
    if status not in expected:
        raise SyncError(f"Gitee API 返回意外状态 {status}")
    if not payload:
        return status, {}
    try:
        return status, json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise SyncError("Gitee API 返回了无效 JSON") from None


def remote_file(owner: str, repo: str, branch: str, filename: str, token: str) -> dict | None:
    path = urllib.parse.quote(filename, safe="")
    query = urllib.parse.urlencode({"ref": branch})
    url = f"{API_ROOT}/repos/{owner}/{repo}/contents/{path}?{query}"
    status, payload = request_json("GET", url, token, expected=(200, 404))
    return None if status == 404 else payload


def decoded_remote_content(filename: str, payload: dict) -> bytes:
    try:
        encoded = str(payload["content"]).replace("\n", "")
        return base64.b64decode(encoded, validate=True)
    except (KeyError, ValueError):
        raise SyncError(f"{filename}: Gitee 现有文件内容无法解析") from None


def put_file(
    owner: str,
    repo: str,
    branch: str,
    filename: str,
    label: str,
    raw: bytes,
    token: str,
    current: dict | None,
) -> None:
    path = urllib.parse.quote(filename, safe="")
    url = f"{API_ROOT}/repos/{owner}/{repo}/contents/{path}"
    form = {
        "access_token": token,
        "branch": branch,
        "content": base64.b64encode(raw).decode("ascii"),
        "message": label,
    }
    method = "POST"
    expected = (201,)
    if current is not None:
        method = "PUT"
        expected = (200,)
        form["sha"] = str(current.get("sha") or "")
        if not form["sha"]:
            raise SyncError(f"{filename}: Gitee 未返回现有文件版本")
    request_json(method, url, token, form=form, expected=expected)


def main() -> int:
    token = os.environ.get("GITEE_XML_SYNC_TOKEN", "").strip()
    if not token:
        raise SyncError("缺少 GITEE_XML_SYNC_TOKEN")
    owner = os.environ.get("GITEE_OWNER", "alistairzhang").strip()
    repo = os.environ.get("GITEE_REPO", "journal-rss").strip()
    branch = os.environ.get("GITEE_BRANCH", "master").strip()
    if not owner or not repo or not branch:
        raise SyncError("Gitee 仓库配置不完整")

    config = json.loads((ROOT / "journals.json").read_text(encoding="utf-8"))
    outputs = configured_xml_files(config)
    local = {filename: validated_local_xml(filename) for filename, _ in outputs}

    changed = 0
    unchanged = 0
    for filename, label in outputs:
        current = remote_file(owner, repo, branch, filename, token)
        raw = local[filename]
        if current is not None and decoded_remote_content(filename, current) == raw:
            unchanged += 1
            print(f"UNCHANGED {filename}")
            continue
        put_file(owner, repo, branch, filename, label, raw, token, current)
        changed += 1
        print(f"SYNCED {filename} {hashlib.sha256(raw).hexdigest()[:12]}")
    print(f"Gitee XML sync complete: {changed} changed, {unchanged} unchanged.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"GITEE XML SYNC ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
