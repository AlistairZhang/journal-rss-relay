#!/usr/bin/env python3
"""在提交和发布前校验全部 RSS 文件的格式、字段及中英文一致性。"""

from __future__ import annotations

import json
import sys
import xml.etree.ElementTree as ET
import urllib.parse
from pathlib import Path

from update_feeds import is_non_article


ROOT = Path(__file__).resolve().parent
DOCS = ROOT / "docs"
ATOM_NS = "http://www.w3.org/2005/Atom"
DC_NS = "http://purl.org/dc/elements/1.1/"


def text(parent: ET.Element, tag: str) -> str:
    element = parent.find(tag)
    return "" if element is None else "".join(element.itertext()).strip()


def configured_feeds(config: dict) -> list[tuple[str, dict]]:
    feeds: list[tuple[str, dict]] = []
    for journal in config["journals"]:
        feeds.append((journal["name"], journal))
        translation = journal.get("translation")
        if translation:
            feeds.append(
                (
                    translation["title"],
                    {
                        **translation,
                        "official_link_hosts": journal.get("official_link_hosts", []),
                    },
                )
            )
    return feeds


def validate_one(name: str, settings: dict, base_url: str) -> ET.Element:
    filename = settings.get("output_file") or f"{settings['slug']}.xml"
    path = DOCS / filename
    if path.name != filename or path.suffix != ".xml":
        raise ValueError(f"{name}: 输出文件名不安全")
    raw = path.read_bytes()
    if b"<![CDATA[" in raw:
        raise ValueError(f"{name}: 仍含旧版逐字 CDATA 规避内容")
    root = ET.fromstring(raw)
    if root.tag != "rss" or root.get("version") != "2.0":
        raise ValueError(f"{name}: 不是 RSS 2.0")
    channel = root.find("channel")
    if channel is None:
        raise ValueError(f"{name}: 缺少 channel")
    expected_title = str(
        settings.get("feed_title") or settings.get("title") or ""
    ).strip()
    if expected_title and text(channel, "title") != expected_title:
        raise ValueError(f"{name}: RSS 名称与配置不一致")
    self_link = channel.find(f"{{{ATOM_NS}}}link")
    expected_url = f"{base_url.rstrip('/')}/{filename}"
    if self_link is None or self_link.get("href") != expected_url:
        raise ValueError(f"{name}: atom:self 不是公开订阅网址")
    items = channel.findall("item")
    if not items:
        raise ValueError(f"{name}: 没有文献条目")
    guids: set[str] = set()
    for index, item in enumerate(items, 1):
        for field in ("title", "link", "description", "pubDate", "guid"):
            if not text(item, field):
                raise ValueError(f"{name}: 第 {index} 条缺少 {field}")
        guid = text(item, "guid")
        if guid in guids:
            raise ValueError(f"{name}: GUID 重复：{guid}")
        guids.add(guid)
        creators = [text(element, ".") for element in item.findall(f"{{{DC_NS}}}creator")]
        creators = [creator for creator in creators if creator]
        if not creators:
            raise ValueError(f"{name}: 第 {index} 条没有作者")
        if len(creators) != len(set(creators)):
            raise ValueError(f"{name}: 第 {index} 条作者重复")
        title = text(item, "title")
        if is_non_article(title):
            raise ValueError(f"{name}: 第 {index} 条仍是书评或读后感：{title}")
        allowed_hosts = {
            host.casefold()
            for host in settings.get("official_link_hosts", [])
            if str(host).strip()
        }
        if allowed_hosts:
            link_host = (urllib.parse.urlparse(text(item, "link")).hostname or "").casefold()
            if link_host not in allowed_hosts:
                raise ValueError(
                    f"{name}: 第 {index} 条没有链接到期刊官网：{link_host or '无主机名'}"
                )
    return channel


def comparable_item(item: ET.Element) -> tuple:
    creators = tuple(text(element, ".") for element in item.findall(f"{{{DC_NS}}}creator"))
    identifiers = tuple(text(element, ".") for element in item.findall(f"{{{DC_NS}}}identifier"))
    return (
        text(item, "link"),
        text(item, "pubDate"),
        text(item, "category"),
        creators,
        identifiers,
    )


def validate_translation_pair(source: ET.Element, translated: ET.Element, name: str) -> None:
    source_items = source.findall("item")
    translated_items = translated.findall("item")
    if len(source_items) != len(translated_items):
        raise ValueError(f"{name}: 原文与译文条数不一致")
    for index, (original, translation) in enumerate(zip(source_items, translated_items), 1):
        if comparable_item(original) != comparable_item(translation):
            raise ValueError(f"{name}: 第 {index} 条原文与译文元数据不一致")
        if text(translation, "guid") != f"zh-cn:{text(original, 'guid')}":
            raise ValueError(f"{name}: 第 {index} 条译文 GUID 不稳定")


def main() -> int:
    config = json.loads((ROOT / "journals.json").read_text(encoding="utf-8"))
    feeds = configured_feeds(config)
    outputs = [
        settings.get("output_file") or f"{settings['slug']}.xml"
        for _, settings in feeds
    ]
    if len(outputs) != len(set(outputs)):
        raise ValueError("配置中存在重复输出文件名")
    commit_labels = [
        str(settings.get("commit_label") or "").strip()
        for _, settings in feeds
    ]
    if any(not label or "\t" in label or "\n" in label for label in commit_labels):
        raise ValueError("每个 RSS 必须配置单行 commit_label")
    if len(commit_labels) != len(set(commit_labels)):
        raise ValueError("配置中存在重复 commit_label")

    channels: dict[str, ET.Element] = {}
    for name, settings in feeds:
        channels[settings["slug"]] = validate_one(name, settings, config["public_base_url"])

    for journal in config["journals"]:
        if journal.get("translation"):
            validate_translation_pair(
                channels[journal["slug"]],
                channels[journal["translation"]["slug"]],
                journal["translation"]["title"],
            )
    print(f"Validated {len(outputs)} RSS feeds.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"VALIDATION ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
