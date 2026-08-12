#!/usr/bin/env python3
"""Update configured journals and publish normalized RSS 2.0 feeds."""

from __future__ import annotations

import json
import hashlib
import copy
import html
import os
import re
import sys
import time
import urllib.error
import urllib.request
import urllib.parse
import unicodedata
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import format_datetime, parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path


ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "journals.json"
OUTPUT_DIR = ROOT / "docs"
TRANSLATION_CACHE_PATH = ROOT / "translation_cache.json"
REPOSITORY_URL = "https://github.com/AlistairZhang/journal-rss-relay"
USER_AGENT = f"Journal-RSS-Relay/2.0 (+{REPOSITORY_URL})"
BROWSER_USER_AGENT = (
    f"Mozilla/5.0 (compatible; JournalRSSRelay/2.0; +{REPOSITORY_URL})"
)
ATOM_NS = "http://www.w3.org/2005/Atom"
DC_NS = "http://purl.org/dc/elements/1.1/"
ET.register_namespace("atom", ATOM_NS)
ET.register_namespace("dc", DC_NS)


def output_filename(settings: dict) -> str:
    """Return a safe, readable XML filename configured for one feed."""
    filename = str(settings.get("output_file") or f"{settings['slug']}.xml").strip()
    if Path(filename).name != filename or not filename.endswith(".xml"):
        raise RuntimeError(f"invalid output filename: {filename}")
    return filename


def public_feed_url(base_url: str, settings: dict) -> str:
    return f"{base_url.rstrip('/')}/{output_filename(settings)}"


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].split(":")[-1]


def first_text(element: ET.Element, *names: str) -> str:
    wanted = set(names)
    for child in list(element):
        if local_name(child.tag) in wanted:
            text = "".join(child.itertext()).strip()
            if text:
                return text
    return ""


def fetch(
    url: str,
    attempts: int = 3,
    *,
    accept: str = "application/rss+xml, application/atom+xml, application/xml, text/xml, */*",
    user_agent: str = USER_AGENT,
    referer: str = "",
) -> bytes:
    last_error = None
    for attempt in range(1, attempts + 1):
        try:
            headers = {"User-Agent": user_agent, "Accept": accept}
            if referer:
                headers["Referer"] = referer
            request = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(request, timeout=45) as response:
                return response.read()
        except Exception as exc:  # keep the previous feed when a source is temporarily unavailable
            last_error = exc
            if attempt < attempts:
                time.sleep(attempt * 3)
    raise RuntimeError(f"fetch failed after {attempts} attempts: {last_error}")


def fetch_json(
    url: str,
    *,
    payload: dict | None = None,
    attempts: int = 3,
    referer: str = "",
    origin: str = "",
) -> dict:
    last_error = None
    for attempt in range(1, attempts + 1):
        try:
            headers = {
                "User-Agent": BROWSER_USER_AGENT,
                "Accept": "application/json, text/plain, */*",
            }
            if referer:
                headers["Referer"] = referer
            if origin:
                headers["Origin"] = origin
            body = None
            if payload is not None:
                body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                headers["Content-Type"] = "application/json"
            request = urllib.request.Request(url, data=body, headers=headers)
            with urllib.request.urlopen(request, timeout=45) as response:
                result = json.loads(response.read().decode("utf-8"))
            if result.get("code") not in (None, 200):
                raise RuntimeError(f"API returned code {result.get('code')}")
            return result
        except Exception as exc:
            last_error = exc
            if attempt < attempts:
                time.sleep(attempt * 3)
    raise RuntimeError(f"JSON fetch failed after {attempts} attempts: {last_error}")


def normalize_date(value: str) -> str:
    if not value:
        return ""
    try:
        dt = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            dt = None
            for pattern in ("%Y/%m/%d %H:%M:%S", "%Y/%m/%d"):
                try:
                    dt = datetime.strptime(value, pattern)
                    break
                except ValueError:
                    continue
            if dt is None:
                return value
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return format_datetime(dt)


def source_items(source_root: ET.Element) -> list[ET.Element]:
    return [element for element in source_root.iter() if local_name(element.tag) in {"item", "entry"}]


def item_link(item: ET.Element) -> str:
    direct = first_text(item, "link")
    if direct:
        return direct
    for child in list(item):
        if local_name(child.tag) == "link":
            href = child.attrib.get("href", "").strip()
            if href:
                return href
    return ""


def clean_html_text(parts: list[str]) -> str:
    return " ".join("".join(parts).split())


class PlainTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def html_to_text(value: str) -> str:
    parser = PlainTextParser()
    parser.feed(value or "")
    parser.close()
    return clean_html_text(parser.parts)


def normalize_doi(value: str) -> str:
    text = html_to_text(value or "").strip()
    match = re.search(r"10\.\d{4,9}/[^\s<>\"']+", text, flags=re.IGNORECASE)
    return match.group(0).rstrip(".,;") if match else ""


def normalize_title_key(value: str) -> str:
    """Normalize harmless typography differences before comparing article titles."""
    text = unicodedata.normalize("NFKC", html_to_text(value or "")).lower()
    return "".join(character for character in text if character.isalnum())


def load_translation_cache() -> dict:
    """Load public, DOI-keyed translations without making the feed depend on the API."""
    if not TRANSLATION_CACHE_PATH.exists():
        return {"version": 1, "entries": {}}
    try:
        cache = json.loads(TRANSLATION_CACHE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"translation cache is invalid: {exc}") from exc
    if cache.get("version") != 1 or not isinstance(cache.get("entries"), dict):
        raise RuntimeError("translation cache has an unsupported format")
    return cache


def translation_source_hash(title: str, abstract: str) -> str:
    return hashlib.sha256(f"{title}\0{abstract}".encode("utf-8")).hexdigest()


class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Do not forward the private API key to a redirected host."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


def translate_text(
    text: str,
    *,
    api_key: str,
    api_url: str,
    model: str,
    field_name: str,
    attempts: int = 3,
) -> str:
    """Translate one English field through an OpenAI-compatible HTTPS API."""
    if not text.strip():
        return ""
    parsed_url = urllib.parse.urlparse(api_url)
    if parsed_url.scheme != "https" or not parsed_url.netloc:
        raise RuntimeError("TRANSLATION_API_URL must be a complete HTTPS URL")
    if parsed_url.username or parsed_url.password:
        raise RuntimeError("TRANSLATION_API_URL must not contain credentials")
    field_label = "学术摘要" if field_name == "abstract" else "论文标题"
    prompt = (
        f"请把下面的英文学术{field_label}准确翻译成简体中文。"
        "保留数字、公式、缩写和专有名词；只输出译文，不要解释。\n\n"
        + text.strip()
    )
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.1,
        "max_tokens": 4096 if field_name == "abstract" else 512,
        "stream": False,
    }
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    last_error = "unknown error"
    opener = urllib.request.build_opener(NoRedirectHandler())
    for attempt in range(1, attempts + 1):
        try:
            request = urllib.request.Request(
                api_url,
                data=body,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                    "User-Agent": USER_AGENT,
                },
            )
            with opener.open(request, timeout=90) as response:
                result = json.loads(response.read().decode("utf-8"))
            choices = result.get("choices") or []
            finish_reason = choices[0].get("finish_reason") if choices else ""
            if finish_reason in {"length", "content_filter"}:
                raise RuntimeError(f"model did not finish normally ({finish_reason or 'missing'})")
            translated = str(
                ((choices[0].get("message") or {}).get("content") if choices else "")
                or ""
            ).strip()
            translated = re.sub(
                r"^(?:翻译|译文|中文翻译)\s*[：:]\s*",
                "",
                translated,
            ).strip()
            cjk_count = len(re.findall(r"[\u3400-\u9fff]", translated))
            if (
                not translated
                or translated.casefold() == text.strip().casefold()
                or cjk_count < (4 if field_name == "abstract" else 1)
            ):
                raise RuntimeError("model returned no usable Chinese translation")
            return translated
        except urllib.error.HTTPError as exc:
            last_error = f"HTTP {exc.code}"
            if 300 <= exc.code < 400 or exc.code in {400, 401, 403}:
                break
            if attempt < attempts:
                time.sleep(attempt * 3)
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            if attempt < attempts:
                time.sleep(attempt * 3)
    raise RuntimeError(
        f"translation API failed after {attempts} attempts ({last_error})"
    )


def build_translated_feed(
    journal: dict,
    base_url: str,
    source_feed_xml: bytes,
    cache: dict,
) -> tuple[bytes, int, bool]:
    """Create a Chinese companion feed and reuse cached translations by source hash."""
    translation = journal.get("translation") or {}
    translated_title = str(translation.get("title") or "").strip()
    if not translation.get("slug") or not translated_title:
        raise RuntimeError(f"translation settings are incomplete for {journal['name']}")

    source_root = ET.fromstring(source_feed_xml)
    source_channel = next(
        (child for child in list(source_root) if local_name(child.tag) == "channel"),
        None,
    )
    if source_channel is None:
        raise RuntimeError("source RSS does not contain a channel")
    source_feed_title = first_text(source_channel, "title") or journal["name"]
    source_feed_url = public_feed_url(base_url, journal)
    source_items_list = [
        child for child in list(source_channel) if local_name(child.tag) == "item"
    ]
    if not source_items_list:
        raise RuntimeError("source RSS does not contain items to translate")

    api_key = os.environ.get("TRANSLATION_API_KEY", "").strip()
    api_url = os.environ.get("TRANSLATION_API_URL", "").strip()
    model = os.environ.get("TRANSLATION_MODEL", "").strip()
    entries = cache.setdefault("entries", {})
    pending_entries: dict[str, dict[str, str]] = {}
    prepared_items: list[tuple[ET.Element, str, str]] = []
    for source_item in source_items_list:
        source_title = first_text(source_item, "title").strip()
        source_abstract = first_text(source_item, "description").strip()
        source_guid = first_text(source_item, "guid", "id").strip()
        doi = normalize_doi(first_text(source_item, "identifier", "doi"))
        stable_id = doi.lower() or source_guid
        if not all((source_title, source_abstract, stable_id)):
            raise RuntimeError("source RSS item lacks title, abstract, or stable identifier")
        cache_key = f"{journal['slug']}:{stable_id}"
        source_hash = translation_source_hash(source_title, source_abstract)
        cached = entries.get(cache_key) if isinstance(entries.get(cache_key), dict) else {}
        if (
            cached.get("source_hash") == source_hash
            and str(cached.get("title_zh") or "").strip()
            and str(cached.get("abstract_zh") or "").strip()
        ):
            title_zh = str(cached["title_zh"]).strip()
            abstract_zh = str(cached["abstract_zh"]).strip()
        else:
            if not all((api_key, api_url, model)):
                raise RuntimeError(
                    "current translations are missing; configure TRANSLATION_API_KEY, "
                    "TRANSLATION_API_URL, and TRANSLATION_MODEL"
                )
            title_zh = translate_text(
                source_title,
                api_key=api_key,
                api_url=api_url,
                model=model,
                field_name="title",
            )
            abstract_zh = translate_text(
                source_abstract,
                api_key=api_key,
                api_url=api_url,
                model=model,
                field_name="abstract",
            )
            pending_entries[cache_key] = {
                "source_hash": source_hash,
                "title_zh": title_zh,
                "abstract_zh": abstract_zh,
                "model": model,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        prepared_items.append((source_item, title_zh, abstract_zh))

    entries.update(pending_entries)
    cache_changed = bool(pending_entries)

    rss = ET.Element("rss", {"version": "2.0"})
    channel = ET.SubElement(rss, "channel")
    ET.SubElement(channel, "title").text = translated_title
    ET.SubElement(channel, "link").text = first_text(source_channel, "link")
    translated_source_name = str(
        journal.get("expected_journal_title") or journal["name"]
    ).strip()
    ET.SubElement(channel, "description").text = str(
        translation.get("description")
        or f"{translated_source_name} 最新一期中文译版；标题和摘要由 AI 翻译，仅供参考"
    )
    ET.SubElement(channel, "language").text = "zh-cn"
    ET.SubElement(channel, "lastBuildDate").text = format_datetime(datetime.now(timezone.utc))
    ET.SubElement(
        channel,
        f"{{{ATOM_NS}}}link",
        {
            "href": public_feed_url(base_url, translation),
            "rel": "self",
            "type": "application/rss+xml",
        },
    )

    for source_item, title_zh, abstract_zh in prepared_items:
        item = ET.SubElement(channel, "item")
        ET.SubElement(item, "title").text = title_zh
        link = item_link(source_item)
        if link:
            ET.SubElement(item, "link").text = link
        for child in list(source_item):
            name = local_name(child.tag)
            if name == "creator":
                ET.SubElement(item, f"{{{DC_NS}}}creator").text = "".join(child.itertext()).strip()
            elif name in {"pubDate", "category"}:
                ET.SubElement(item, name).text = "".join(child.itertext()).strip()
            elif name == "identifier":
                ET.SubElement(item, f"{{{DC_NS}}}identifier").text = "".join(child.itertext()).strip()
        ET.SubElement(item, "description").text = abstract_zh
        source_guid = first_text(source_item, "guid", "id").strip()
        ET.SubElement(item, "guid", {"isPermaLink": "false"}).text = (
            f"zh-cn:{source_guid}"
        )
        ET.SubElement(item, "source", {"url": source_feed_url}).text = source_feed_title

    ET.indent(rss, space="  ")
    return (
        ET.tostring(rss, encoding="utf-8", xml_declaration=True),
        len(prepared_items),
        cache_changed,
    )


def parse_chndoi_metadata(data: bytes) -> dict[str, str]:
    """Read the title and DOI from CNKI's public DOI registration result page."""
    text = html_to_text(data.decode("utf-8", errors="replace"))
    if "没有注册" in text:
        return {}
    title_match = re.search(
        r"题名\s*[：:]\s*(.+?)(?=\s*作者\s*[：:])",
        text,
    )
    doi_match = re.search(
        r"DOI码\s*[：:]\s*(10\.\d{4,9}/\S+)",
        text,
        flags=re.IGNORECASE,
    )
    if not title_match or not doi_match:
        return {}
    return {
        "title": title_match.group(1).strip(),
        "doi": normalize_doi(doi_match.group(1)),
    }


def doi_registered_by_cnki(doi: str) -> bool:
    """Confirm through DOI.org that the candidate is registered by CNKI."""
    url = f"https://doi.org/ra/{urllib.parse.quote(doi, safe='/')}"
    try:
        records = json.loads(
            fetch(url, attempts=1, accept="application/json, */*").decode("utf-8")
        )
    except Exception as exc:
        print(f"WARNING: DOI注册机构暂无法核验: {doi} ({exc})", file=sys.stderr)
        return False
    return any(
        normalize_doi(str(record.get("DOI", ""))).lower() == doi.lower()
        and str(record.get("RA", "")).upper() == "CNKI"
        for record in records
        if isinstance(record, dict)
    )


def supplement_cnki_dois(articles: list[dict[str, str]], journal: dict) -> int:
    """Add only CNKI-registered DOI records whose title matches exactly."""
    prefix = str(journal.get("doi_prefix", "")).strip().rstrip(".")
    if not prefix:
        return 0

    unresolved = {
        normalize_title_key(article["title"]): article
        for article in articles
        if not article.get("doi") and normalize_title_key(article["title"])
    }
    if not unresolved:
        return 0

    issue_groups = {
        (article["year"], int(article["issue_number"]))
        for article in unresolved.values()
    }
    sequence_max = int(journal.get("doi_sequence_max", 20))
    matched = 0
    for year, issue_number in sorted(issue_groups):
        for sequence in range(1, sequence_max + 1):
            doi = f"{prefix}.{year}.{issue_number:02d}.{sequence:03d}"
            url = (
                "https://www.chndoi.org/Resolution/Handler?"
                + urllib.parse.urlencode({"doi": doi})
            )
            try:
                metadata = parse_chndoi_metadata(
                    fetch(
                        url,
                        attempts=1,
                        accept="text/html,application/xhtml+xml,*/*;q=0.8",
                    )
                )
            except Exception as exc:
                print(f"WARNING: CHNDOI暂无法查询: {doi} ({exc})", file=sys.stderr)
                continue
            title_key = normalize_title_key(metadata.get("title", ""))
            registered_doi = normalize_doi(metadata.get("doi", ""))
            article = unresolved.get(title_key)
            if (
                not article
                or not registered_doi
                or registered_doi.lower() != doi.lower()
                or not doi_registered_by_cnki(registered_doi)
            ):
                continue
            article["doi"] = registered_doi
            matched += 1
            del unresolved[title_key]
            if not unresolved:
                return matched
    return matched


def split_creator_names(
    value: str,
    language: str = "",
    separator: str = "",
) -> list[str]:
    """Return individual creator names for Zotero-compatible RSS metadata."""
    text = html_to_text(value or "").strip()
    if not text:
        return []

    # The Chinese sources use commas, enumeration commas, or whitespace between
    # names. Keep English source strings intact because some publishers mix
    # author names and affiliations in a single dc:creator value.
    parts = [text]
    if separator:
        parts = [part.strip() for part in text.split(separator)]
    elif language.lower().startswith("zh"):
        parts = re.split(r"\s*[,，、;；]\s*", text)
        if len(parts) == 1 and " " in text:
            tokens = [token for token in text.split() if token]
            if tokens and all(
                re.fullmatch(r"[\u3400-\u9fff·]{1,12}|等", token)
                for token in tokens
            ):
                parts = tokens

    creators: list[str] = []
    for part in parts:
        creator = part.strip()
        if not creator or creator.lower() in {"等", "et al", "et al."}:
            continue
        if creator not in creators:
            creators.append(creator)
    return creators


def add_creators(
    item: ET.Element,
    value: str,
    language: str = "",
    separator: str = "",
) -> list[str]:
    creators = split_creator_names(value, language, separator)
    for creator in creators:
        ET.SubElement(item, f"{{{DC_NS}}}creator").text = creator
    return creators


class MagtechCurrentParser(HTMLParser):
    """Extract complete current-issue metadata from a Magtech article list."""

    VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}
    FIELD_CLASSES = {
        "j-title-1": "title",
        "j-author": "author",
        "j-volumn": "citation",
        "j-abstract": "abstract",
        "j-column": "section",
        "j-doi": "doi",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.items: list[dict[str, str]] = []
        self.page_text: list[str] = []
        self.current: dict[str, object] | None = None
        self.capture_field = ""
        self.capture_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        classes = set((attributes.get("class") or "").split())

        if tag == "li" and (attributes.get("id") or "").startswith("art"):
            self.current = {
                "article_id": (attributes.get("id") or "")[3:],
                "title": [],
                "author": [],
                "citation": [],
                "abstract": [],
                "section": [],
                "doi": [],
                "link": "",
            }

        if self.current is None:
            return

        if self.capture_field and tag not in self.VOID_TAGS:
            self.capture_depth += 1
        elif not self.capture_field:
            for class_name, field_name in self.FIELD_CLASSES.items():
                if class_name in classes:
                    self.capture_field = field_name
                    self.capture_depth = 1
                    break

        if self.capture_field == "title" and tag == "a" and attributes.get("href"):
            self.current["link"] = attributes["href"].strip()

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag not in self.VOID_TAGS:
            self.handle_endtag(tag)

    def handle_data(self, data: str) -> None:
        self.page_text.append(data)
        if self.current is not None and self.capture_field:
            parts = self.current[self.capture_field]
            if isinstance(parts, list):
                parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if self.current is not None and self.capture_field and tag not in self.VOID_TAGS:
            self.capture_depth -= 1
            if self.capture_depth <= 0:
                self.capture_field = ""
                self.capture_depth = 0

        if tag == "li" and self.current is not None:
            normalized = {
                key: clean_html_text(value) if isinstance(value, list) else str(value)
                for key, value in self.current.items()
            }
            if normalized["title"] and normalized["link"]:
                self.items.append(normalized)
            self.current = None
            self.capture_field = ""
            self.capture_depth = 0

    def publication_date(self) -> str:
        match = re.search(r"刊出日期\s*[：:]\s*(\d{4}-\d{2}-\d{2})", clean_html_text(self.page_text))
        return match.group(1) if match else ""


class AeaArticleParser(HTMLParser):
    """Extract AEA citation metadata, visible authors, and the full abstract."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.meta: dict[str, list[str]] = {}
        self.authors: list[str] = []
        self.abstract = ""
        self._author_depth = 0
        self._author_parts: list[str] = []
        self._abstract_depth = 0
        self._abstract_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = {name: value or "" for name, value in attrs}
        classes = set(attributes.get("class", "").split())
        if tag == "meta":
            name = attributes.get("name", "").lower()
            content = unicodedata.normalize(
                "NFC",
                html_to_text(attributes.get("content", "")),
            ).strip()
            if name and content:
                self.meta.setdefault(name, []).append(content)

        if tag == "li" and "author" in classes:
            self._author_depth = 1
            self._author_parts = []
        elif self._author_depth and tag == "li":
            self._author_depth += 1

        if tag == "section" and "abstract" in classes:
            self._abstract_depth = 1
            self._abstract_parts = []
        elif self._abstract_depth and tag == "section":
            self._abstract_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if self._author_depth and tag == "li":
            self._author_depth -= 1
            if self._author_depth == 0:
                author = unicodedata.normalize(
                    "NFC",
                    clean_html_text(self._author_parts),
                )
                if author and author not in self.authors:
                    self.authors.append(author)

        if self._abstract_depth and tag == "section":
            self._abstract_depth -= 1
            if self._abstract_depth == 0:
                abstract = unicodedata.normalize(
                    "NFC",
                    clean_html_text(self._abstract_parts),
                )
                self.abstract = re.sub(r"^Abstract\s*", "", abstract).strip()

    def handle_data(self, data: str) -> None:
        if self._author_depth:
            self._author_parts.append(data)
        if self._abstract_depth:
            self._abstract_parts.append(data)

    def first_meta(self, name: str) -> str:
        values = self.meta.get(name.lower(), [])
        return values[0] if values else ""


def build_magtech_feed(journal: dict, suffix: str, base_url: str, source_html: bytes) -> tuple[bytes, int]:
    parser = MagtechCurrentParser()
    parser.feed(source_html.decode("utf-8", errors="replace"))
    items = parser.items
    published = normalize_date(parser.publication_date())
    if not items:
        raise RuntimeError("Magtech current-issue page contains no articles")
    if not published:
        raise RuntimeError("Magtech current-issue page contains no publication date")

    rss = ET.Element("rss", {"version": "2.0"})
    channel = ET.SubElement(rss, "channel")
    ET.SubElement(channel, "title").text = f"{journal['name']} - {suffix}"
    ET.SubElement(channel, "link").text = journal.get("site_url", journal["source_url"])
    ET.SubElement(channel, "description").text = f"{journal['name']}当期题录信息，由 Journal RSS Relay 每 3 天更新"
    ET.SubElement(channel, "language").text = journal.get("language", "zh-cn")
    ET.SubElement(channel, "lastBuildDate").text = format_datetime(datetime.now(timezone.utc))
    ET.SubElement(
        channel,
        f"{{{ATOM_NS}}}link",
        {
            "href": public_feed_url(base_url, journal),
            "rel": "self",
            "type": "application/rss+xml",
        },
    )

    for article in items:
        item = ET.SubElement(channel, "item")
        ET.SubElement(item, "title").text = article["title"]
        ET.SubElement(item, "link").text = article["link"]
        add_creators(item, article["author"], journal.get("language", ""))
        ET.SubElement(item, "pubDate").text = published
        abstract = article["abstract"] or journal.get("missing_abstract_text", "")
        ET.SubElement(item, "description").text = abstract
        if article["section"]:
            ET.SubElement(item, "category").text = article["section"]
        doi = normalize_doi(article.get("doi", ""))
        if doi:
            ET.SubElement(item, f"{{{DC_NS}}}identifier").text = f"https://doi.org/{doi}"
        ET.SubElement(item, "guid", {"isPermaLink": "false"}).text = article["link"]

    ET.indent(rss, space="  ")
    return ET.tostring(rss, encoding="utf-8", xml_declaration=True), len(items)


def build_ncpssd_feed(
    journal: dict,
    suffix: str,
    base_url: str,
    source_html: bytes,
) -> tuple[bytes, int]:
    text = source_html.decode("utf-8", errors="replace")
    issue_match = re.search(
        r"<h2\s+class=['\"]catalog-vol['\"]>\s*(\d{4})年\s*第(\d+)期\s*</h2>",
        text,
        flags=re.IGNORECASE,
    )
    if not issue_match:
        raise RuntimeError("国家哲社文献中心页面未返回当期期次")
    expected_year, expected_issue = issue_match.groups()

    reference_pattern = re.compile(
        r"openDetail\('/Literature/articleinfo\?id=(?P<id>[^&']+)",
        flags=re.IGNORECASE,
    )
    references: list[dict[str, str]] = []
    seen_ids: set[str] = set()
    for match in reference_pattern.finditer(text):
        article_id = match.group("id").strip()
        if not article_id or article_id in seen_ids:
            continue
        seen_ids.add(article_id)
        article_query = urllib.parse.urlencode(
            {
                "id": article_id,
                "type": "journalArticle",
                "typename": "中文期刊文章",
                "nav": "1",
                "langType": "1",
            }
        )
        references.append(
            {
                "id": article_id,
                "link": f"https://m.ncpssd.cn/Literature/articleinfo?{article_query}",
            }
        )
    if not references:
        raise RuntimeError("国家哲社文献中心当期目录未返回文章")

    articles: list[dict[str, str]] = []
    for reference in references:
        response = fetch_json(
            journal["detail_api"],
            payload={
                "lngid": reference["id"],
                "type": "中文期刊文章",
                "pageType": 1,
            },
            attempts=2,
        )
        detail = response.get("data") or {}
        title = str(detail.get("titlec") or "").strip()
        year = str(detail.get("years") or "").strip()
        issue_number = str(detail.get("num") or "").strip()
        if (
            str(detail.get("lngid") or "") != reference["id"]
            or str(detail.get("mediac") or "").strip() != journal["name"]
            or year != expected_year
            or int(issue_number or 0) != int(expected_issue)
            or not title
        ):
            raise RuntimeError(f"国家哲社文献中心题录校验失败: {reference['id']}")

        author = re.sub(r"\[\d+(?:,\d+)*\]", "", str(detail.get("showwriter") or ""))
        start_page = str(detail.get("beginpage") or "").strip()
        end_page = str(detail.get("endpage") or "").strip()
        pages = "-".join(part for part in (start_page, end_page) if part)
        articles.append(
            {
                "id": reference["id"],
                "title": title,
                "author": author,
                "link": reference["link"],
                "year": year,
                "issue_number": issue_number,
                "pages": pages,
                "published": str(detail.get("publishdate") or "").strip(),
                "abstract": str(detail.get("remarkc") or "").strip(),
                "keywords": str(detail.get("keywordc") or "").strip(),
                "doi": normalize_doi(str(detail.get("doi") or "")),
            }
        )
        time.sleep(float(journal.get("request_delay_seconds", 0.3)))

    supplemented = supplement_cnki_dois(articles, journal)
    if supplemented:
        print(
            f"{journal['name']}从 CNKI DOI 登记库补全 {supplemented} 条 DOI",
            flush=True,
        )

    rss = ET.Element("rss", {"version": "2.0"})
    channel = ET.SubElement(rss, "channel")
    ET.SubElement(channel, "title").text = f"{journal['name']} - {suffix}"
    ET.SubElement(channel, "link").text = journal.get("site_url", journal["source_url"])
    ET.SubElement(channel, "description").text = (
        f"{journal['name']}当期题录信息，由 Journal RSS Relay 每 3 天更新"
    )
    ET.SubElement(channel, "language").text = journal.get("language", "zh-cn")
    ET.SubElement(channel, "lastBuildDate").text = format_datetime(datetime.now(timezone.utc))
    ET.SubElement(
        channel,
        f"{{{ATOM_NS}}}link",
        {
            "href": public_feed_url(base_url, journal),
            "rel": "self",
            "type": "application/rss+xml",
        },
    )

    for article in articles:
        item = ET.SubElement(channel, "item")
        ET.SubElement(item, "title").text = article["title"]
        ET.SubElement(item, "link").text = article["link"]
        add_creators(item, article["author"], journal.get("language", ""))
        published = normalize_date(article["published"])
        if published:
            ET.SubElement(item, "pubDate").text = published
        issue_text = (
            f"{article['year']}年第{article['issue_number']}期，"
            f"{article['pages']}页"
        )
        ET.SubElement(item, "description").text = article["abstract"] or issue_text
        ET.SubElement(item, "category").text = issue_text
        for keyword in re.split(r"\s*[；;]\s*", article["keywords"]):
            if keyword:
                ET.SubElement(item, "category").text = keyword
        if article["doi"]:
            ET.SubElement(item, f"{{{DC_NS}}}identifier").text = (
                f"https://doi.org/{article['doi']}"
            )
        ET.SubElement(item, "guid", {"isPermaLink": "false"}).text = (
            f"urn:ncpssd:{article['id']}"
        )

    ET.indent(rss, space="  ")
    return ET.tostring(rss, encoding="utf-8", xml_declaration=True), len(articles)


def build_aea_current_feed(
    journal: dict,
    suffix: str,
    base_url: str,
    source_html: bytes,
) -> tuple[bytes, int]:
    """Build an RSS feed from AEA's current-issue and article pages."""
    text = source_html.decode("utf-8", errors="replace")
    issue_match = re.search(
        r"<h1\s+class=['\"]issue['\"]>(.*?)</h1>",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if not issue_match:
        raise RuntimeError("AEA current-issue page did not return issue metadata")
    issue_label = html_to_text(issue_match.group(1))
    issue_parts = re.search(
        r"Vol\.\s*(\d+)\s*,\s*No\.\s*(\d+)\s*,\s*([A-Za-z]+\s+\d{4})",
        issue_label,
        flags=re.IGNORECASE,
    )
    if not issue_parts:
        raise RuntimeError(f"AEA issue label is not recognized: {issue_label}")
    expected_volume, expected_issue, month_year = issue_parts.groups()
    issue_date = datetime.strptime(month_year, "%B %Y").replace(tzinfo=timezone.utc)

    article_urls: list[str] = []
    seen_dois: set[str] = set()
    for href in re.findall(
        r"href=['\"](/articles\?id=10\.1257/aer\.[^'\"]+)['\"]",
        text,
        flags=re.IGNORECASE,
    ):
        link = urllib.parse.urljoin(journal["source_url"], href)
        query = urllib.parse.parse_qs(urllib.parse.urlparse(link).query)
        doi = normalize_doi((query.get("id") or [""])[0])
        if not doi or doi.lower() in seen_dois:
            continue
        seen_dois.add(doi.lower())
        article_urls.append(
            "https://www.aeaweb.org/articles?"
            + urllib.parse.urlencode({"id": doi})
        )
    if not article_urls:
        raise RuntimeError("AEA current issue did not return article links")

    articles: list[dict[str, object]] = []
    for article_url in article_urls:
        article_query = urllib.parse.parse_qs(urllib.parse.urlparse(article_url).query)
        expected_doi = normalize_doi((article_query.get("id") or [""])[0])
        detail_html = fetch(
            article_url,
            accept="text/html,application/xhtml+xml,*/*;q=0.8",
            user_agent=BROWSER_USER_AGENT,
            referer=journal["source_url"],
        )
        parser = AeaArticleParser()
        parser.feed(detail_html.decode("utf-8", errors="replace"))
        parser.close()

        title = parser.first_meta("citation_title").strip()
        doi = normalize_doi(parser.first_meta("citation_doi"))
        journal_title = parser.first_meta("citation_journal_title").strip()
        volume = parser.first_meta("citation_volume").strip()
        issue_number = parser.first_meta("citation_issue").strip()
        published = parser.first_meta("citation_publication_date").strip()
        if not parser.authors or not parser.abstract:
            front_matter_doi = f"10.1257/aer.{expected_volume}.{expected_issue}.i"
            if expected_doi.lower() == front_matter_doi.lower() and title == "Front Matter":
                print(f"Skipping AEA front matter: {title}", flush=True)
                continue
            raise RuntimeError(f"AEA research article lacks authors or abstract: {article_url}")
        if (
            not title
            or not doi
            or doi.lower() != expected_doi.lower()
            or journal_title != journal["expected_journal_title"]
            or volume != expected_volume
            or issue_number != expected_issue
            or published != issue_date.strftime("%Y/%m")
        ):
            raise RuntimeError(f"AEA article metadata validation failed: {article_url}")

        first_page = parser.first_meta("citation_firstpage").strip()
        last_page = parser.first_meta("citation_lastpage").strip()
        if (
            first_page.isdigit()
            and last_page.isdigit()
            and len(last_page) < len(first_page)
        ):
            suffix_size = 10 ** len(last_page)
            expanded_last_page = int(first_page[: -len(last_page)] + last_page)
            while expanded_last_page < int(first_page):
                expanded_last_page += suffix_size
            last_page = str(expanded_last_page)
        pages = "–".join(part for part in (first_page, last_page) if part)
        articles.append(
            {
                "title": title,
                "authors": parser.authors,
                "link": article_url,
                "doi": doi,
                "abstract": parser.abstract,
                "pages": pages,
            }
        )
        time.sleep(float(journal.get("request_delay_seconds", 0.3)))
    if not articles:
        raise RuntimeError("AEA current issue did not return research articles")

    rss = ET.Element("rss", {"version": "2.0"})
    channel = ET.SubElement(rss, "channel")
    ET.SubElement(channel, "title").text = f"{journal['name']} - {suffix}"
    ET.SubElement(channel, "link").text = journal.get("site_url", journal["source_url"])
    ET.SubElement(channel, "description").text = (
        f"{journal['expected_journal_title']} latest-issue metadata, updated every 3 days"
    )
    ET.SubElement(channel, "language").text = journal.get("language", "en")
    ET.SubElement(channel, "lastBuildDate").text = format_datetime(datetime.now(timezone.utc))
    ET.SubElement(
        channel,
        f"{{{ATOM_NS}}}link",
        {
            "href": public_feed_url(base_url, journal),
            "rel": "self",
            "type": "application/rss+xml",
        },
    )

    for article in articles:
        item = ET.SubElement(channel, "item")
        ET.SubElement(item, "title").text = str(article["title"])
        ET.SubElement(item, "link").text = str(article["link"])
        authors = article["authors"]
        if isinstance(authors, list):
            for author in authors:
                ET.SubElement(item, f"{{{DC_NS}}}creator").text = str(author)
        ET.SubElement(item, "pubDate").text = format_datetime(issue_date)
        ET.SubElement(item, "description").text = str(article["abstract"])
        issue_category = f"Vol. {expected_volume}, No. {expected_issue}"
        if article["pages"]:
            issue_category += f", pp. {article['pages']}"
        ET.SubElement(item, "category").text = issue_category
        ET.SubElement(item, f"{{{DC_NS}}}identifier").text = (
            f"https://doi.org/{article['doi']}"
        )
        ET.SubElement(item, "guid", {"isPermaLink": "false"}).text = (
            f"doi:{article['doi']}"
        )

    ET.indent(rss, space="  ")
    return ET.tostring(rss, encoding="utf-8", xml_declaration=True), len(articles)


def discover_ncpssd_current_url(journal: dict) -> str:
    """Resolve the newest issue URL instead of pinning the feed to one issue."""
    current_year = datetime.now().year
    year_lookback = int(journal.get("year_lookback", 2))
    api_url = journal["issue_list_api"]
    for year in range(current_year, current_year - year_lookback - 1, -1):
        query_url = api_url + "?" + urllib.parse.urlencode(
            {
                "op": "getnum",
                "gch": journal["journal_code"],
                "years": year,
                "langType": 1,
            }
        )
        response = fetch_json(
            query_url,
            attempts=2,
            referer=journal.get("source_url", "https://www.ncpssd.org/"),
        )
        issue_links = re.findall(
            r"href=['\"]([^'\"]+)['\"][^>]*>\s*(\d+)\s*</a>",
            str(response.get("data") or ""),
            flags=re.IGNORECASE,
        )
        if not issue_links:
            continue
        href, _ = max(issue_links, key=lambda entry: int(entry[1]))
        return urllib.parse.urljoin(
            "https://www.ncpssd.org/",
            href.replace("&amp;", "&"),
        )
    raise RuntimeError("国家哲社文献中心未返回可用的最新期目录")


def build_erj_official_feed(journal: dict, suffix: str, base_url: str) -> tuple[bytes, int]:
    api_base = journal["api_base"].rstrip("/")
    journal_id = journal["journal_id"]
    api_journal_id = int(journal_id) if str(journal_id).isdigit() else journal_id
    current_url = api_base + "/SiteWebApi/GetCurrentPeriod?" + urllib.parse.urlencode(
        {"JournalID": journal_id}
    )
    current = fetch_json(
        current_url,
        referer=journal["source_url"],
        origin="https://erj.ajcass.com",
    )
    issue_data = current.get("data") or {}
    summaries = issue_data.get("issueInfoList") or []
    if not summaries:
        raise RuntimeError("经济研究官网当前期接口未返回文章")

    articles: list[dict[str, str]] = []
    for summary in summaries:
        content_id = summary.get("contentId") or summary.get("id")
        if not content_id:
            raise RuntimeError("经济研究官网文章缺少 contentId")
        detail = fetch_json(
            api_base + "/SiteWebApi/GetContentInfo",
            payload={
                "JournalID": api_journal_id,
                "contentId": content_id,
                "channelId": 0,
                "dataShowType": 1,
                "dataSourceType": 3,
                "issue": 0,
                "year": 0,
            },
            referer=journal["source_url"],
            origin="https://erj.ajcass.com",
        )
        result = ((detail.get("data") or {}).get("issueContentInfoResult") or {})
        title = html_to_text(result.get("title") or summary.get("title") or "")
        author = html_to_text(result.get("authors") or summary.get("authors") or "")
        abstract = html_to_text(result.get("abstract") or "")
        published = normalize_date(result.get("editDate") or "")
        if not all((title, author, abstract, published)):
            raise RuntimeError(f"经济研究官网文章字段不完整: contentId={content_id}")
        article_link = "https://erj.ajcass.com/#/issue?" + urllib.parse.urlencode(
            {
                "id": content_id,
                "year": summary.get("year") or issue_data.get("year") or "",
                "issue": summary.get("issue") or issue_data.get("issue") or "",
                "title": "最新目录",
            }
        )
        articles.append(
            {
                "title": title,
                "author": author,
                "abstract": abstract,
                "published": published,
                "link": article_link,
                "issue": html_to_text(
                    result.get("yearVolumeIssue") or summary.get("yearVolumeIssue") or ""
                ),
                "doi": normalize_doi(result.get("doi") or summary.get("doi") or ""),
            }
        )

    rss = ET.Element("rss", {"version": "2.0"})
    channel = ET.SubElement(rss, "channel")
    ET.SubElement(channel, "title").text = f"{journal['name']} - {suffix}"
    ET.SubElement(channel, "link").text = journal["source_url"]
    ET.SubElement(channel, "description").text = (
        "《经济研究》官网当期题录信息，由 Journal RSS Relay 每 3 天更新"
    )
    ET.SubElement(channel, "language").text = journal.get("language", "zh-cn")
    ET.SubElement(channel, "lastBuildDate").text = format_datetime(datetime.now(timezone.utc))
    ET.SubElement(
        channel,
        f"{{{ATOM_NS}}}link",
        {
            "href": public_feed_url(base_url, journal),
            "rel": "self",
            "type": "application/rss+xml",
        },
    )

    for article in articles:
        item = ET.SubElement(channel, "item")
        ET.SubElement(item, "title").text = article["title"]
        ET.SubElement(item, "link").text = article["link"]
        add_creators(item, article["author"], journal.get("language", ""))
        ET.SubElement(item, "pubDate").text = article["published"]
        ET.SubElement(item, "description").text = article["abstract"]
        if article["issue"]:
            ET.SubElement(item, "category").text = article["issue"]
        if article["doi"]:
            ET.SubElement(item, f"{{{DC_NS}}}identifier").text = (
                f"https://doi.org/{article['doi']}"
            )
        ET.SubElement(item, "guid", {"isPermaLink": "false"}).text = article["link"]

    ET.indent(rss, space="  ")
    return ET.tostring(rss, encoding="utf-8", xml_declaration=True), len(articles)


def discover_jpe_current_issue(source_xml: bytes) -> tuple[str, str, str, set[str]]:
    """Read JPE's current volume, issue, cover date, and DOI set from its eTOC RSS."""
    source_root = ET.fromstring(source_xml)
    issue_groups: dict[tuple[str, str, str], set[str]] = {}
    for source_item in source_items(source_root):
        volume = first_text(source_item, "volume").strip()
        issue = first_text(source_item, "number").strip()
        cover_date = first_text(source_item, "coverDate").strip()
        doi = normalize_doi(first_text(source_item, "identifier", "doi"))
        author = first_text(source_item, "creator", "author").strip()
        if not all((volume, issue, cover_date, doi, author)):
            continue
        if not re.fullmatch(r"\d+", volume) or not re.fullmatch(r"[A-Za-z0-9]+", issue):
            continue
        try:
            parsed_cover_date = datetime.fromisoformat(cover_date.replace("Z", "+00:00"))
        except ValueError:
            continue
        normalized_cover_date = parsed_cover_date.astimezone(timezone.utc).isoformat()
        issue_groups.setdefault(
            (volume, issue, normalized_cover_date),
            set(),
        ).add(doi.lower())
    if not issue_groups:
        raise RuntimeError("JPE eTOC RSS did not return a published issue")

    def issue_sort_key(group: tuple[str, str, str]) -> tuple[datetime, int, int, str]:
        volume, issue, cover_date = group
        numeric_issue = int(issue) if issue.isdigit() else -1
        return (
            datetime.fromisoformat(cover_date),
            int(volume),
            numeric_issue,
            issue,
        )

    current_group = max(issue_groups, key=issue_sort_key)
    volume, issue, cover_date = current_group
    return volume, issue, cover_date, issue_groups[current_group]


def parse_redif_articles(source_data: bytes) -> list[dict[str, list[str]]]:
    """Parse the repeated and continued fields used by ReDIF article records."""
    text = source_data.decode("utf-8", errors="replace")
    records: list[dict[str, list[str]]] = []
    current: dict[str, list[str]] = {}
    last_key = ""

    def finish_record() -> None:
        nonlocal current, last_key
        if current:
            records.append(current)
        current = {}
        last_key = ""

    for raw_line in text.splitlines():
        if not raw_line.strip():
            finish_record()
            continue
        if raw_line[:1].isspace():
            if not last_key or not current.get(last_key):
                raise RuntimeError("JPE ReDIF contains an orphan continuation line")
            current[last_key][-1] = " ".join(
                (current[last_key][-1] + " " + raw_line.strip()).split()
            )
            continue
        if ":" not in raw_line:
            raise RuntimeError(f"JPE ReDIF field is malformed: {raw_line[:80]}")
        key, value = raw_line.split(":", 1)
        last_key = key.strip().lower()
        current.setdefault(last_key, []).append(" ".join(value.split()))
    finish_record()
    return records


def html_meta_values(source_html: bytes) -> dict[str, list[str]]:
    """Extract repeated HTML meta values without relying on attribute order."""
    values: dict[str, list[str]] = {}
    text = source_html.decode("utf-8", errors="replace")
    for tag in re.findall(r"<meta\b[^>]*>", text, flags=re.IGNORECASE):
        attributes = {
            key.lower(): html.unescape(value)
            for key, _, value in re.findall(
                r"([\w:-]+)\s*=\s*(['\"])(.*?)\2",
                tag,
                flags=re.IGNORECASE | re.DOTALL,
            )
        }
        name = (attributes.get("name") or attributes.get("property") or "").lower()
        content = attributes.get("content", "").strip()
        if name and content:
            values.setdefault(name, []).append(content)
    return values


def build_repec_series_current_feed(
    journal: dict,
    suffix: str,
    base_url: str,
    source_html: bytes,
) -> tuple[bytes, int]:
    """Build a current-issue feed from a publisher-supplied IDEAS/RePEc series."""
    text = source_html.decode("utf-8", errors="replace")
    issue_blocks = re.findall(
        r"<h3>([A-Za-z]+\s+\d{4}),\s*Volume\s*(\d+),\s*Issue\s*(\d+)</h3>"
        r"<div[^>]*>\s*<ul[^>]*>(.*?)</ul>",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if not issue_blocks:
        raise RuntimeError("RePEc series page did not return issue metadata")

    def issue_key(block: tuple[str, str, str, str]) -> tuple[datetime, int, int]:
        return datetime.strptime(block[0], "%B %Y"), int(block[1]), int(block[2])

    month_year, volume, issue_number, issue_html = max(issue_blocks, key=issue_key)
    issue_date = datetime.strptime(month_year, "%B %Y").replace(tzinfo=timezone.utc)
    article_paths = list(
        dict.fromkeys(
            html.unescape(path)
            for path in re.findall(
                r"href=['\"]([^'\"]*?/a/[^'\"]+\.html)['\"]",
                issue_html,
                flags=re.IGNORECASE,
            )
        )
    )
    if not article_paths:
        raise RuntimeError("RePEc current issue did not return article links")

    articles: list[dict[str, object]] = []
    seen_dois: set[str] = set()
    for article_path in article_paths:
        article_url = urllib.parse.urljoin(journal["source_url"], article_path)
        article_html = fetch(
            article_url,
            accept="text/html,application/xhtml+xml,*/*;q=0.8",
            referer=journal["source_url"],
        )
        meta = html_meta_values(article_html)
        title = (meta.get("citation_title") or [""])[0].strip()
        abstract = (meta.get("citation_abstract") or [""])[0].strip()
        journal_title = (meta.get("citation_journal_title") or [""])[0].strip()
        article_volume = (meta.get("citation_volume") or [""])[0].strip()
        article_issue = (meta.get("citation_issue") or [""])[0].strip()
        article_year = (meta.get("citation_year") or [""])[0].strip()
        authors = [
            author.strip()
            for author in re.split(
                r"\s*;\s*",
                (meta.get("citation_authors") or meta.get("author") or [""])[0],
            )
            if author.strip()
        ]
        first_page = (meta.get("citation_firstpage") or [""])[0].strip()
        last_page = (meta.get("citation_lastpage") or [""])[0].strip()
        doi = normalize_doi(article_html.decode("utf-8", errors="replace"))
        if (
            not all((title, abstract, authors, first_page, last_page, doi))
            or journal_title != journal["expected_journal_title"]
            or article_volume != volume
            or article_issue != issue_number
            or article_year != str(issue_date.year)
        ):
            raise RuntimeError(f"RePEc article metadata validation failed: {article_url}")
        if doi.lower() in seen_dois:
            raise RuntimeError(f"RePEc current issue contains duplicate DOI: {doi}")
        seen_dois.add(doi.lower())
        articles.append(
            {
                "title": title,
                "abstract": abstract,
                "authors": authors,
                "first_page": int(first_page),
                "pages": f"{first_page}\u2013{last_page}",
                "doi": doi,
                "link": f"https://doi.org/{doi}",
            }
        )
    articles.sort(key=lambda article: int(article["first_page"]))

    rss = ET.Element("rss", {"version": "2.0"})
    channel = ET.SubElement(rss, "channel")
    ET.SubElement(channel, "title").text = f"{journal['name']} - {suffix}"
    ET.SubElement(channel, "link").text = journal.get("site_url", journal["source_url"])
    ET.SubElement(channel, "description").text = (
        f"{journal['expected_journal_title']} latest-issue metadata, updated every 3 days"
    )
    ET.SubElement(channel, "language").text = journal.get("language", "en")
    ET.SubElement(channel, "lastBuildDate").text = format_datetime(datetime.now(timezone.utc))
    ET.SubElement(
        channel,
        f"{{{ATOM_NS}}}link",
        {
            "href": public_feed_url(base_url, journal),
            "rel": "self",
            "type": "application/rss+xml",
        },
    )
    published = format_datetime(issue_date)
    for article in articles:
        item = ET.SubElement(channel, "item")
        ET.SubElement(item, "title").text = str(article["title"])
        ET.SubElement(item, "link").text = str(article["link"])
        for author in article["authors"]:
            ET.SubElement(item, f"{{{DC_NS}}}creator").text = str(author)
        ET.SubElement(item, "pubDate").text = published
        ET.SubElement(item, "description").text = str(article["abstract"])
        ET.SubElement(item, "category").text = (
            f"Vol. {volume}, No. {issue_number}, pp. {article['pages']}"
        )
        ET.SubElement(item, f"{{{DC_NS}}}identifier").text = str(article["link"])
        ET.SubElement(item, "guid", {"isPermaLink": "false"}).text = (
            f"doi:{article['doi']}"
        )
    ET.indent(rss, space="  ")
    return ET.tostring(rss, encoding="utf-8", xml_declaration=True), len(articles)


def build_jpe_current_feed(
    journal: dict,
    suffix: str,
    base_url: str,
    source_xml: bytes,
    redif_data: bytes,
) -> tuple[bytes, int]:
    """Build a clean JPE current-issue feed from the publisher's RePEc metadata."""
    volume, issue_number, cover_date, issue_dois = discover_jpe_current_issue(source_xml)
    records = parse_redif_articles(redif_data)
    if not records:
        raise RuntimeError("JPE publisher ReDIF file contains no articles")

    expected_journal = str(
        journal.get("expected_journal_title") or "Journal of Political Economy"
    ).strip()
    source_articles: dict[str, dict[str, str]] = {}
    source_root = ET.fromstring(source_xml)
    for source_item in source_items(source_root):
        if (
            first_text(source_item, "volume").strip() != volume
            or first_text(source_item, "number").strip() != issue_number
            or not first_text(source_item, "creator", "author").strip()
        ):
            continue
        source_doi = normalize_doi(first_text(source_item, "identifier", "doi")).lower()
        if source_doi:
            source_articles[source_doi] = {
                "title": " ".join(first_text(source_item, "title").split()),
                "link": item_link(source_item),
            }
    if set(source_articles) != issue_dois:
        raise RuntimeError("JPE eTOC research-article DOI discovery is inconsistent")

    articles: list[dict[str, object]] = []
    seen_dois: set[str] = set()
    cover_year = str(datetime.fromisoformat(cover_date).year)
    for record in records:
        template_type = (record.get("template-type") or [""])[0]
        title = unicodedata.normalize(
            "NFC", " ".join((record.get("title") or [""])[0].split())
        )
        abstract = unicodedata.normalize(
            "NFC", " ".join((record.get("abstract") or [""])[0].split())
        )
        authors = [" ".join(value.split()) for value in record.get("author-name", [])]
        article_journal = (record.get("journal") or [""])[0].strip()
        article_volume = (record.get("volume") or [""])[0].strip()
        article_issue = (record.get("issue") or [""])[0].strip()
        article_year = (record.get("year") or [""])[0].strip()
        pages = (record.get("pages") or [""])[0].strip()
        handle = (record.get("handle") or [""])[0]
        doi = normalize_doi(handle)
        page_match = re.fullmatch(r"(\d+)\s*-\s*(\d+)", pages)
        if (
            not template_type.lower().startswith("redif-article")
            or not all((title, abstract, authors, doi, page_match))
            or article_journal != expected_journal
            or article_volume != volume
            or article_issue != issue_number
            or article_year != cover_year
            or doi.lower() not in issue_dois
            or normalize_title_key(source_articles.get(doi.lower(), {}).get("title", ""))
            != normalize_title_key(title)
        ):
            raise RuntimeError(f"JPE ReDIF article metadata validation failed: {handle or title}")
        if doi.lower() in seen_dois:
            raise RuntimeError(f"JPE ReDIF contains a duplicate DOI: {doi}")
        seen_dois.add(doi.lower())
        first_page, last_page = page_match.groups()
        if int(last_page) < int(first_page):
            raise RuntimeError(f"JPE ReDIF page range is invalid: {pages}")
        articles.append(
            {
                "title": title,
                "abstract": abstract,
                "authors": authors,
                "doi": doi,
                "link": source_articles[doi.lower()]["link"],
                "first_page": int(first_page),
                "pages": f"{first_page}\u2013{last_page}",
            }
        )
    if seen_dois != issue_dois:
        raise RuntimeError("JPE publisher ReDIF and eTOC DOI sets do not match")
    articles.sort(key=lambda article: int(article["first_page"]))

    rss = ET.Element("rss", {"version": "2.0"})
    channel = ET.SubElement(rss, "channel")
    ET.SubElement(channel, "title").text = f"{journal['name']} - {suffix}"
    ET.SubElement(channel, "link").text = journal.get("site_url", journal["source_url"])
    ET.SubElement(channel, "description").text = (
        f"{expected_journal} latest-issue metadata, updated every 3 days"
    )
    ET.SubElement(channel, "language").text = journal.get("language", "en")
    ET.SubElement(channel, "lastBuildDate").text = format_datetime(datetime.now(timezone.utc))
    ET.SubElement(
        channel,
        f"{{{ATOM_NS}}}link",
        {
            "href": public_feed_url(base_url, journal),
            "rel": "self",
            "type": "application/rss+xml",
        },
    )

    issue_date = format_datetime(datetime.fromisoformat(cover_date))
    for article in articles:
        item = ET.SubElement(channel, "item")
        ET.SubElement(item, "title").text = str(article["title"])
        article_link = str(article["link"]) or (
            "https://www.journals.uchicago.edu/doi/abs/"
            + urllib.parse.quote(str(article["doi"]), safe="/")
        )
        ET.SubElement(item, "link").text = article_link
        for author in article["authors"]:
            ET.SubElement(item, f"{{{DC_NS}}}creator").text = str(author)
        ET.SubElement(item, "pubDate").text = issue_date
        ET.SubElement(item, "description").text = str(article["abstract"])
        ET.SubElement(item, "category").text = (
            f"Vol. {volume}, No. {issue_number}, pp. {article['pages']}"
        )
        ET.SubElement(item, f"{{{DC_NS}}}identifier").text = (
            f"https://doi.org/{article['doi']}"
        )
        ET.SubElement(item, "guid", {"isPermaLink": "false"}).text = (
            f"doi:{article['doi']}"
        )

    ET.indent(rss, space="  ")
    return ET.tostring(rss, encoding="utf-8", xml_declaration=True), len(articles)


def build_feed(journal: dict, suffix: str, base_url: str, source_xml: bytes) -> tuple[bytes, int]:
    source_root = ET.fromstring(source_xml)
    excluded_phrases = journal.get("exclude_title_contains", [])
    items = [
        item
        for item in source_items(source_root)
        if not any(phrase in first_text(item, "title") for phrase in excluded_phrases)
    ]
    if not items:
        raise RuntimeError("source feed contains no items")

    rss = ET.Element("rss", {"version": "2.0"})
    channel = ET.SubElement(rss, "channel")
    title = f"{journal['name']} - {suffix}"
    ET.SubElement(channel, "title").text = title
    ET.SubElement(channel, "link").text = journal.get("site_url", journal["source_url"])
    ET.SubElement(channel, "description").text = f"{journal['name']}题录信息，由 Journal RSS Relay 每 3 天更新"
    ET.SubElement(channel, "language").text = journal.get("language", "zh-cn")
    ET.SubElement(channel, "lastBuildDate").text = format_datetime(datetime.now(timezone.utc))
    ET.SubElement(
        channel,
        f"{{{ATOM_NS}}}link",
        {
            "href": public_feed_url(base_url, journal),
            "rel": "self",
            "type": "application/rss+xml",
        },
    )

    for source_item in items:
        item = ET.SubElement(channel, "item")
        item_title = first_text(source_item, "title") or "（无标题）"
        link = item_link(source_item)
        for replacement in journal.get("link_replacements", []):
            old_prefix = replacement.get("from", "")
            if old_prefix and link.startswith(old_prefix):
                link = replacement.get("to", "") + link[len(old_prefix):]
                break
        if journal.get("link_mode") == "cnki_title_search":
            link = "https://kns.cnki.net/kns8s/defaultresult/index?kw=" + urllib.parse.quote(item_title)
        description = first_text(source_item, "description", "summary", "content", "encoded")
        published = normalize_date(first_text(source_item, "pubDate", "date", "published", "updated"))
        author = first_text(source_item, "creator", "author")
        doi = normalize_doi(first_text(source_item, "doi", "identifier"))
        source_guid = first_text(source_item, "guid", "id")
        guid = source_guid or "urn:sha256:" + hashlib.sha256(
            f"{item_title}|{link}".encode("utf-8")
        ).hexdigest()

        ET.SubElement(item, "title").text = item_title
        if link:
            ET.SubElement(item, "link").text = link
        if description and journal.get("include_description", True):
            ET.SubElement(item, "description").text = description
        if published:
            ET.SubElement(item, "pubDate").text = published
        if author:
            add_creators(
                item,
                author,
                journal.get("language", ""),
                journal.get("creator_separator", ""),
            )
        if doi:
            ET.SubElement(item, f"{{{DC_NS}}}identifier").text = f"https://doi.org/{doi}"
        ET.SubElement(item, "guid", {"isPermaLink": "false"}).text = guid

    ET.indent(rss, space="  ")
    return ET.tostring(rss, encoding="utf-8", xml_declaration=True), len(items)


def main() -> int:
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    suffix = config.get("title_suffix", "祥仔")
    base_url = config["public_base_url"]
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    prepared: list[tuple[Path, bytes, str, int]] = []
    translation_cache = load_translation_cache()
    translation_cache_changed = False

    for journal in config["journals"]:
        print(f"Fetching {journal['name']} ...", flush=True)
        if journal.get("source_type") == "erj_official_api":
            feed_xml, count = build_erj_official_feed(journal, suffix, base_url)
        elif journal.get("source_type") == "aea_current":
            source_data = fetch(
                journal["source_url"],
                accept="text/html,application/xhtml+xml,*/*;q=0.8",
                user_agent=BROWSER_USER_AGENT,
                referer=journal.get("site_url", journal["source_url"]),
            )
            feed_xml, count = build_aea_current_feed(
                journal,
                suffix,
                base_url,
                source_data,
            )
        elif journal.get("source_type") == "jpe_repec_current":
            source_data = fetch(journal["source_url"])
            volume, issue_number, _, _ = discover_jpe_current_issue(source_data)
            redif_url = (
                journal["repec_base_url"].rstrip("/")
                + f"/JPEv{volume}n{issue_number}.repec.redif"
            )
            redif_data = fetch(
                redif_url,
                accept="text/plain,text/x-redif,*/*;q=0.8",
                referer=journal.get("site_url", journal["source_url"]),
            )
            feed_xml, count = build_jpe_current_feed(
                journal,
                suffix,
                base_url,
                source_data,
                redif_data,
            )
        elif journal.get("source_type") == "repec_series_current":
            source_data = fetch(
                journal["source_url"],
                accept="text/html,application/xhtml+xml,*/*;q=0.8",
                referer=journal.get("site_url", journal["source_url"]),
            )
            feed_xml, count = build_repec_series_current_feed(
                journal,
                suffix,
                base_url,
                source_data,
            )
        elif journal.get("source_type") == "ncpssd_current":
            current_url = discover_ncpssd_current_url(journal)
            source_data = fetch(
                current_url,
                accept="text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                user_agent=BROWSER_USER_AGENT,
                referer=journal.get("source_url", "https://www.ncpssd.org/"),
            )
            resolved_journal = {**journal, "source_url": current_url}
            feed_xml, count = build_ncpssd_feed(
                resolved_journal,
                suffix,
                base_url,
                source_data,
            )
        elif journal.get("source_type") == "magtech_current":
            source_data = fetch(
                journal["source_url"],
                accept="text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                user_agent=BROWSER_USER_AGENT,
                referer=journal.get("referer", journal["source_url"]),
            )
            feed_xml, count = build_magtech_feed(journal, suffix, base_url, source_data)
        else:
            source_data = fetch(journal["source_url"])
            feed_xml, count = build_feed(journal, suffix, base_url, source_data)
        output_path = OUTPUT_DIR / output_filename(journal)
        prepared.append((output_path, feed_xml, journal["name"], count))

        if journal.get("translation"):
            translation_settings = journal["translation"]
            candidate_cache = copy.deepcopy(translation_cache)
            try:
                translated_xml, translated_count, cache_changed = build_translated_feed(
                    journal,
                    base_url,
                    feed_xml,
                    candidate_cache,
                )
            except Exception as exc:
                translated_output = OUTPUT_DIR / output_filename(translation_settings)
                if not translated_output.exists():
                    raise RuntimeError(
                        f"首次生成{journal['name']}译版失败: {exc}"
                    ) from exc
                print(
                    f"WARNING: 保留现有{journal['name']}译版，未覆盖: {exc}",
                    file=sys.stderr,
                    flush=True,
                )
            else:
                if cache_changed:
                    translation_cache = candidate_cache
                prepared.append(
                    (
                        OUTPUT_DIR / output_filename(translation_settings),
                        translated_xml,
                        str(translation_settings["title"]),
                        translated_count,
                    )
                )
                translation_cache_changed = translation_cache_changed or cache_changed

    for output_path, feed_xml, name, count in prepared:
        temp_path = output_path.with_suffix(".xml.tmp")
        temp_path.write_bytes(feed_xml)
        os.replace(temp_path, output_path)
        display_name = name if name.endswith(suffix) else f"{name} - {suffix}"
        print(f"Updated {output_path.name}: {count} items ({display_name})", flush=True)

    if translation_cache_changed:
        cache_temp_path = TRANSLATION_CACHE_PATH.with_suffix(".json.tmp")
        cache_temp_path.write_text(
            json.dumps(translation_cache, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(cache_temp_path, TRANSLATION_CACHE_PATH)
        print(f"Updated {TRANSLATION_CACHE_PATH.name}", flush=True)

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
