#!/usr/bin/env python3
"""验证并接收 Gitee 采集的国内期刊题录，生成可信 RSS。"""

from __future__ import annotations

import base64
import binascii
import gzip
import hashlib
import hmac
import io
import json
import os
import re
import sys
import tempfile
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from update_feeds import (
    ATOM_NS,
    DC_NS,
    ROOT,
    feed_issue_key,
    feed_title,
    is_non_article,
)


EVENT_TYPE = "gitee-journal-ingest-v1"
SCHEMA = "journal-rss-relay/gitee-ingest@1"
EXPECTED_REPOSITORY = "AlistairZhang/journal-rss-relay"
EXPECTED_SENDER = "AlistairZhang"
ALLOWED_SLUGS = {"sljjjsjjyj", "glsj"}
SIGNING_PREFIX = b"journal-rss-relay:gitee-ingest:v1\0"

MAX_COMPRESSED_BYTES = 48 * 1024
MAX_DECOMPRESSED_BYTES = 512 * 1024
MAX_ITEMS_PER_JOURNAL = 100
MAX_TITLE_CHARS = 600
MAX_ABSTRACT_CHARS = 20_000
MAX_AUTHOR_CHARS = 200
MAX_MISC_CHARS = 500

OUTER_FIELDS = {
    "schema",
    "encoding",
    "batch_id",
    "collected_at",
    "sha256",
    "data",
    "signature",
}
JOURNAL_FIELDS = {"slug", "source_url", "issue", "items"}
ISSUE_FIELDS = {"year", "number"}
ITEM_FIELDS = {
    "source_id",
    "title",
    "authors",
    "published",
    "abstract",
    "pages",
    "section",
    "official_url",
}

ET.register_namespace("atom", ATOM_NS)
ET.register_namespace("dc", DC_NS)


def _fail(message: str) -> RuntimeError:
    return RuntimeError(f"Gitee ingest rejected: {message}")


def _require_exact_fields(value: dict[str, Any], expected: set[str], label: str) -> None:
    actual = set(value)
    if actual != expected:
        raise _fail(f"{label} fields are invalid")


def _require_string(
    value: Any,
    label: str,
    *,
    max_chars: int,
    allow_empty: bool = False,
) -> str:
    if not isinstance(value, str):
        raise _fail(f"{label} must be a string")
    normalized = re.sub(r"[ \t\r\f\v]+", " ", value).strip()
    if not allow_empty and not normalized:
        raise _fail(f"{label} is empty")
    if len(normalized) > max_chars:
        raise _fail(f"{label} is too long")
    for character in normalized:
        codepoint = ord(character)
        if codepoint < 32 and character not in "\n\t\r":
            raise _fail(f"{label} contains an invalid control character")
    return normalized


def _parse_iso_datetime(value: str) -> datetime:
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise _fail("collected_at is invalid") from exc
    if parsed.tzinfo is None:
        raise _fail("collected_at must include a timezone")
    return parsed.astimezone(timezone.utc)


def _signing_message(
    schema: str,
    batch_id: str,
    collected_at: str,
    digest: str,
    compressed: bytes,
) -> bytes:
    metadata = "\0".join((schema, batch_id, collected_at, digest)).encode("utf-8")
    return SIGNING_PREFIX + metadata + b"\0" + compressed


def _decompress_limited(compressed: bytes) -> bytes:
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(compressed), mode="rb") as handle:
            output = handle.read(MAX_DECOMPRESSED_BYTES + 1)
    except (OSError, EOFError) as exc:
        raise _fail("compressed payload is invalid") from exc
    if len(output) > MAX_DECOMPRESSED_BYTES:
        raise _fail("decompressed payload is too large")
    return output


def read_verified_payload() -> tuple[str, str, dict[str, Any]]:
    event_path = os.environ.get("GITHUB_EVENT_PATH")
    if not event_path:
        raise _fail("GITHUB_EVENT_PATH is missing")
    secret = os.environ.get("GITEE_RELAY_HMAC_SECRET", "")
    if len(secret) < 32:
        raise _fail("relay signing secret is missing or too short")

    try:
        event = json.loads(Path(event_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise _fail("event file cannot be read") from exc

    if event.get("action") != EVENT_TYPE:
        raise _fail("event type is not allowed")
    if (event.get("repository") or {}).get("full_name") != EXPECTED_REPOSITORY:
        raise _fail("repository is not allowed")
    if (event.get("sender") or {}).get("login") != EXPECTED_SENDER:
        raise _fail("sender is not allowed")

    payload = event.get("client_payload")
    if not isinstance(payload, dict):
        raise _fail("client_payload is missing")
    _require_exact_fields(payload, OUTER_FIELDS, "client_payload")

    schema = _require_string(payload["schema"], "schema", max_chars=100)
    encoding = _require_string(payload["encoding"], "encoding", max_chars=30)
    batch_id = _require_string(payload["batch_id"], "batch_id", max_chars=64)
    collected_at = _require_string(payload["collected_at"], "collected_at", max_chars=50)
    digest = _require_string(payload["sha256"], "sha256", max_chars=64)
    data = _require_string(payload["data"], "data", max_chars=80_000)
    signature = _require_string(payload["signature"], "signature", max_chars=80)

    if schema != SCHEMA or encoding != "gzip+base64":
        raise _fail("schema or encoding is unsupported")
    if not re.fullmatch(r"[0-9a-f]{24}", batch_id):
        raise _fail("batch_id is invalid")
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise _fail("sha256 is invalid")
    if not re.fullmatch(r"sha256=[0-9a-f]{64}", signature):
        raise _fail("signature format is invalid")

    collected_time = _parse_iso_datetime(collected_at)
    now = datetime.now(timezone.utc)
    if collected_time > now + timedelta(minutes=15):
        raise _fail("payload timestamp is in the future")
    if collected_time < now - timedelta(days=7):
        raise _fail("payload timestamp is too old")

    try:
        compressed = base64.b64decode(data, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise _fail("base64 payload is invalid") from exc
    if not compressed or len(compressed) > MAX_COMPRESSED_BYTES:
        raise _fail("compressed payload size is invalid")

    expected_signature = hmac.new(
        secret.encode("utf-8"),
        _signing_message(schema, batch_id, collected_at, digest, compressed),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(signature[7:], expected_signature):
        raise _fail("signature does not match")

    raw = _decompress_limited(compressed)
    actual_digest = hashlib.sha256(raw).hexdigest()
    if not hmac.compare_digest(digest, actual_digest):
        raise _fail("payload digest does not match")
    if batch_id != digest[:24]:
        raise _fail("batch_id does not match payload digest")

    try:
        inner = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _fail("inner payload is not valid UTF-8 JSON") from exc
    if not isinstance(inner, dict) or set(inner) != {"schema_version", "journals"}:
        raise _fail("inner payload fields are invalid")
    if inner["schema_version"] != 1:
        raise _fail("inner schema version is unsupported")
    if not isinstance(inner["journals"], list) or not 1 <= len(inner["journals"]) <= 2:
        raise _fail("journal count is invalid")
    return batch_id, collected_at, inner


def _parse_date(value: Any, label: str) -> str:
    text = _require_string(value, label, max_chars=10)
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError as exc:
        raise _fail(f"{label} is not YYYY-MM-DD") from exc


def _validate_issue(value: Any) -> tuple[int, int]:
    if not isinstance(value, dict):
        raise _fail("issue is not an object")
    _require_exact_fields(value, ISSUE_FIELDS, "issue")
    year = value["year"]
    number = value["number"]
    if not isinstance(year, int) or not 2000 <= year <= 2100:
        raise _fail("issue year is invalid")
    if not isinstance(number, int) or not 1 <= number <= 12:
        raise _fail("issue number is invalid")
    return year, number


def _quantity_link(source_id: str, year: int, issue: int) -> tuple[str, str]:
    if not re.fullmatch(r"\d{8}", source_id):
        raise _fail("quantity source_id is invalid")
    if int(source_id[:4]) != year or int(source_id[4:6]) != issue:
        raise _fail("quantity source_id does not match its issue")
    sequence = int(source_id[6:])
    if sequence < 1:
        raise _fail("quantity article sequence is invalid")
    query = urllib.parse.urlencode({"file_no": source_id, "flag": "1"})
    link = f"https://www.jqte.net/sljjjsjjyj/ch/reader/view_abstract.aspx?{query}"
    stable_id = f"SLJJJSJJYJ{year}{issue:03d}{sequence:03d}"
    return link, stable_id


def _management_link(source_id: str, supplied_url: str) -> tuple[str, str]:
    if not re.fullmatch(r"\d{1,12}", source_id):
        raise _fail("management source_id is invalid")
    parsed = urllib.parse.urlsplit(supplied_url)
    if (
        parsed.scheme != "http"
        or parsed.hostname != "www.mwm.net.cn"
        or parsed.port is not None
        or parsed.path != "/web/xq"
    ):
        raise _fail("management official_url is invalid")
    query = urllib.parse.parse_qs(parsed.query, keep_blank_values=False)
    if set(query) != {"leafId", "docId"}:
        raise _fail("management official_url query is invalid")
    leaf_id = query["leafId"]
    doc_id = query["docId"]
    if len(leaf_id) != 1 or len(doc_id) != 1:
        raise _fail("management official_url query is ambiguous")
    if not re.fullmatch(r"\d{1,12}", leaf_id[0]) or not re.fullmatch(r"\d{1,12}", doc_id[0]):
        raise _fail("management official_url identifiers are invalid")
    if doc_id[0] != source_id:
        raise _fail("management source_id does not match official_url")
    link = f"http://www.mwm.net.cn/web/xq?leafId={leaf_id[0]}&docId={doc_id[0]}"
    return link, link


def validate_journal_payload(raw: Any, configs: dict[str, dict[str, Any]]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise _fail("journal entry is not an object")
    _require_exact_fields(raw, JOURNAL_FIELDS, "journal")
    slug = _require_string(raw["slug"], "journal slug", max_chars=30)
    if slug not in ALLOWED_SLUGS or slug not in configs:
        raise _fail("journal slug is not allowed")
    journal = configs[slug]
    source_url = _require_string(raw["source_url"], "source_url", max_chars=500)
    if source_url != journal["source_url"]:
        raise _fail("source_url does not match configuration")
    year, issue = _validate_issue(raw["issue"])
    current_year = datetime.now(ZoneInfo("Asia/Shanghai")).year
    if year not in {current_year - 1, current_year}:
        raise _fail("issue year is outside the accepted publication window")

    raw_items = raw["items"]
    if not isinstance(raw_items, list) or not 1 <= len(raw_items) <= MAX_ITEMS_PER_JOURNAL:
        raise _fail("item count is invalid")

    seen_ids: set[str] = set()
    seen_titles: set[str] = set()
    seen_links: set[str] = set()
    items: list[dict[str, Any]] = []
    filtered = 0
    for index, raw_item in enumerate(raw_items, start=1):
        if not isinstance(raw_item, dict):
            raise _fail(f"item {index} is not an object")
        _require_exact_fields(raw_item, ITEM_FIELDS, f"item {index}")
        title = _require_string(raw_item["title"], f"item {index} title", max_chars=MAX_TITLE_CHARS)
        section = _require_string(
            raw_item["section"], f"item {index} section", max_chars=MAX_MISC_CHARS, allow_empty=True
        )
        if is_non_article(title, section):
            filtered += 1
            continue

        source_id = _require_string(raw_item["source_id"], f"item {index} source_id", max_chars=100)
        abstract = _require_string(
            raw_item["abstract"], f"item {index} abstract", max_chars=MAX_ABSTRACT_CHARS
        )
        published = _parse_date(raw_item["published"], f"item {index} published")
        published_date = date.fromisoformat(published)
        if published_date.year != year:
            raise _fail(f"item {index} publication year does not match its issue")
        if published_date > datetime.now(timezone.utc).date() + timedelta(days=31):
            raise _fail(f"item {index} publication date is implausibly far in the future")
        pages = _require_string(
            raw_item["pages"], f"item {index} pages", max_chars=MAX_MISC_CHARS, allow_empty=True
        )
        supplied_url = _require_string(
            raw_item["official_url"], f"item {index} official_url", max_chars=1_000
        )

        raw_authors = raw_item["authors"]
        if not isinstance(raw_authors, list) or not 1 <= len(raw_authors) <= 30:
            raise _fail(f"item {index} authors are invalid")
        authors: list[str] = []
        author_keys: set[str] = set()
        for raw_author in raw_authors:
            author = _require_string(
                raw_author, f"item {index} author", max_chars=MAX_AUTHOR_CHARS
            )
            key = re.sub(r"\s+", "", author).casefold()
            if key not in author_keys:
                authors.append(author)
                author_keys.add(key)
        if not authors:
            raise _fail(f"item {index} has no unique authors")

        if slug == "sljjjsjjyj":
            link, stable_id = _quantity_link(source_id, year, issue)
            if supplied_url != link:
                raise _fail(f"item {index} official_url does not match its source_id")
            guid = f"urn:ncpssd:{stable_id}"
        else:
            if not pages:
                raise _fail(f"item {index} management pages are empty")
            link, guid = _management_link(source_id, supplied_url)

        title_key = re.sub(r"\s+", "", title).casefold()
        if source_id in seen_ids or title_key in seen_titles or link in seen_links:
            raise _fail(f"item {index} is duplicated")
        seen_ids.add(source_id)
        seen_titles.add(title_key)
        seen_links.add(link)
        items.append(
            {
                "source_id": source_id,
                "title": title,
                "authors": authors,
                "published": published,
                "abstract": abstract,
                "pages": pages,
                "section": section,
                "link": link,
                "guid": guid,
            }
        )

    if not items:
        raise _fail(f"all items were filtered for {slug}")
    print(f"Validated {slug}: issue {year}-{issue:02d}, {len(items)} items, {filtered} filtered.")
    return {"slug": slug, "year": year, "issue": issue, "items": items, "journal": journal}


def build_rss(
    validated: dict[str, Any], base_url: str, suffix: str, collected_at: str
) -> tuple[bytes, int]:
    journal = validated["journal"]
    year = validated["year"]
    issue = validated["issue"]
    items = validated["items"]
    output_file = journal.get("output_file", f"{journal['slug']}.xml")

    rss = ET.Element("rss", {"version": "2.0"})
    channel = ET.SubElement(rss, "channel")
    ET.SubElement(channel, "title").text = feed_title(journal, suffix)
    ET.SubElement(channel, "link").text = journal["site_url"]
    ET.SubElement(channel, "description").text = f"{journal['name']}当期文章题录，由国内采集节点获取。"
    ET.SubElement(channel, "language").text = journal.get("language", "zh-cn")
    ET.SubElement(channel, "lastBuildDate").text = format_datetime(
        _parse_iso_datetime(collected_at)
    )
    ET.SubElement(
        channel,
        f"{{{ATOM_NS}}}link",
        {"href": f"{base_url}/{output_file}", "rel": "self", "type": "application/rss+xml"},
    )

    for article in items:
        item = ET.SubElement(channel, "item")
        ET.SubElement(item, "title").text = article["title"]
        ET.SubElement(item, "link").text = article["link"]
        for author in article["authors"]:
            ET.SubElement(item, f"{{{DC_NS}}}creator").text = author
        pub_date = datetime.combine(
            date.fromisoformat(article["published"]), datetime.min.time(), tzinfo=timezone.utc
        )
        ET.SubElement(item, "pubDate").text = format_datetime(pub_date)
        ET.SubElement(item, "description").text = article["abstract"]
        ET.SubElement(item, "category").text = f"{year}年第{issue}期"
        if article["pages"]:
            ET.SubElement(item, "category").text = f"页码：{article['pages']}"
        if article["section"]:
            ET.SubElement(item, "category").text = article["section"]
        guid = ET.SubElement(item, "guid", {"isPermaLink": "false"})
        guid.text = article["guid"]

    ET.indent(rss, space="  ")
    return ET.tostring(rss, encoding="utf-8", xml_declaration=True), len(items)


def _feed_signature(xml_bytes: bytes) -> bytes:
    root = ET.fromstring(xml_bytes)
    channel = root.find("channel")
    if channel is None:
        raise _fail("existing RSS has no channel")
    for element in list(channel):
        if element.tag == "lastBuildDate":
            channel.remove(element)
    ET.indent(root, space="  ")
    return ET.tostring(root, encoding="utf-8")


def _feed_build_time(xml_bytes: bytes) -> datetime | None:
    root = ET.fromstring(xml_bytes)
    value = root.findtext("channel/lastBuildDate", "").strip()
    if not value:
        return None
    try:
        from email.utils import parsedate_to_datetime

        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _write_outputs(changed: bool, files: list[str]) -> None:
    output_path = os.environ.get("GITHUB_OUTPUT")
    if not output_path:
        return
    with open(output_path, "a", encoding="utf-8") as handle:
        handle.write(f"changed={'true' if changed else 'false'}\n")
        handle.write(f"files={' '.join(files)}\n")


def main() -> None:
    batch_id, collected_at, inner = read_verified_payload()
    config = json.loads((ROOT / "journals.json").read_text(encoding="utf-8"))
    journals = config.get("journals", [])
    configs = {entry["slug"]: entry for entry in journals if entry.get("slug") in ALLOWED_SLUGS}
    if set(configs) != ALLOWED_SLUGS:
        raise _fail("repository configuration is missing an allowed journal")

    seen_slugs: set[str] = set()
    validated_batch: list[dict[str, Any]] = []
    for raw_journal in inner["journals"]:
        validated = validate_journal_payload(raw_journal, configs)
        if validated["slug"] in seen_slugs:
            raise _fail("a journal appears more than once")
        seen_slugs.add(validated["slug"])
        validated_batch.append(validated)

    base_url = config["public_base_url"].rstrip("/")
    suffix = config.get("title_suffix", "祥仔")
    prepared: list[tuple[Path, bytes, int]] = []
    for validated in validated_batch:
        journal = validated["journal"]
        output_file = journal.get("output_file", f"{journal['slug']}.xml")
        output_path = ROOT / "docs" / output_file
        candidate, count = build_rss(validated, base_url, suffix, collected_at)
        candidate_issue = (validated["year"], validated["issue"])

        if output_path.exists():
            existing = output_path.read_bytes()
            existing_issue = feed_issue_key(existing)
            if existing_issue is None:
                raise _fail(f"existing issue cannot be identified for {journal['slug']}")
            if candidate_issue < existing_issue:
                print(f"Ignored stale {journal['slug']} issue {candidate_issue[0]}-{candidate_issue[1]:02d}.")
                continue
            if candidate_issue == existing_issue:
                existing_build_time = _feed_build_time(existing)
                incoming_time = _parse_iso_datetime(collected_at)
                if existing_build_time is not None and incoming_time < existing_build_time:
                    print(
                        f"Ignored older same-issue collection for {journal['slug']}.",
                        flush=True,
                    )
                    continue
            candidate_ordinal = candidate_issue[0] * 12 + candidate_issue[1]
            existing_ordinal = existing_issue[0] * 12 + existing_issue[1]
            if candidate_ordinal > existing_ordinal + 2:
                raise _fail(f"issue number jumped unexpectedly for {journal['slug']}")
            existing_item_count = len(ET.fromstring(existing).find("channel").findall("item"))
            candidate_item_count = len(ET.fromstring(candidate).find("channel").findall("item"))
            if candidate_issue == existing_issue and candidate_item_count < existing_item_count:
                raise _fail(f"same-issue item count decreased for {journal['slug']}")
            if _feed_signature(candidate) == _feed_signature(existing):
                print(f"No semantic change for {journal['slug']}.")
                continue
        prepared.append((output_path, candidate, count))

    changed_files: list[str] = []
    for output_path, content, _count in prepared:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile("wb", dir=output_path.parent, delete=False) as handle:
            handle.write(content)
            temporary_path = Path(handle.name)
        os.replace(temporary_path, output_path)
        changed_files.append(f"docs/{output_path.name}")

    _write_outputs(bool(changed_files), changed_files)
    print(f"Accepted batch {batch_id}: {len(changed_files)} feed(s) changed.")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"ERROR: {error}", file=sys.stderr)
        raise SystemExit(1)
