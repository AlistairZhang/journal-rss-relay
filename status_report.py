#!/usr/bin/env python3
"""生成不含题录正文和原始错误信息的永久 RSS 运行状态报告。"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parent
REPORTS = ROOT / "status-reports"
DC_NS = "http://purl.org/dc/elements/1.1/"

KINDS = {
    "update-rss": "全部期刊定时更新",
    "gitee-receive": "Gitee 国内期刊接收",
}
OUTCOMES = {"success": "成功", "partial": "部分成功", "failure": "失败"}
PUBLISH_STATES = {
    "published": "已提交并安排发布 RSS",
    "unchanged": "题录没有变化，无需重新发布",
    "not-published": "未发布 RSS，继续保留上一次有效版本",
}
REASONS = {
    "none": "无",
    "update_failed": "一个或多个期刊的获取或解析未完成",
    "validation_failed": "新生成的 RSS 未通过完整性校验",
    "receiver_failed": "国内采集数据包未通过接收校验，或接收程序异常",
    "publish_failed": "RSS 已生成，但保存到仓库的步骤未完成",
    "workflow_failed": "自动运行未完成",
    "source_failure": "国内采集节点报告至少一个期刊采集失败",
    "translation_failed": "英文原版已更新，但至少一个中文版翻译未完成并沿用上次结果",
}
FEED_STATUSES = {
    "updated": "已获取并更新",
    "unchanged": "已获取，内容无变化",
    "ignored_stale": "收到旧期次，已忽略",
    "ignored_older": "收到同一期较早数据，已忽略",
}
FAILURE_STAGES = {
    "fetch": "访问官网",
    "parse": "识别网页结构",
    "validate": "检查题录完整性",
    "internal": "采集器内部处理",
}
FAILURE_CODES = {
    "source_unreachable": "无法连接期刊官网",
    "source_too_large": "官网响应超过安全大小限制",
    "invalid_source": "官网返回的页面结构无法可靠识别",
    "incomplete_metadata": "文章必要题录字段不完整",
    "duplicate_items": "官网目录出现重复条目",
    "inconsistent_issue": "官网目录中的期次信息不一致",
    "unexpected_error": "采集器发生未分类错误",
}
ALLOWED_RESULT_KINDS = set(KINDS)
ALLOWED_SLUGS = {"sljjjsjjyj", "glsj"}
ALLOWED_FEED_FIELDS = {"slug", "name", "issue", "items", "status"}
ALLOWED_FAILURE_FIELDS = {"slug", "stage", "code"}
MISSING_ABSTRACTS = {"官网未提供摘要。", "摘要暂缺。"}
UPDATE_FEEDS = {
    "jjyj": "经济研究",
    "sljjjsjjyj": "数量经济技术经济研究",
    "jjdl": "经济地理",
    "glsj": "管理世界",
    "zggyjj": "中国工业经济",
    "econometrica": "Econometrica",
    "jpe": "Journal of Political Economy",
    "aer": "American Economic Review",
    "sjjj": "世界经济",
}
UPDATE_FEED_STATUSES = {
    "fetched": "已获取",
    "preserved": "由 Gitee 独立更新，本轮保留",
    "fetched_translated": "英文题录已获取，中文版已由缓存或翻译接口生成",
    "fetched_translation_preserved": "英文题录已获取；本次翻译未完成，中文版沿用上次结果",
}


@dataclass(frozen=True)
class FeedSnapshot:
    name: str
    output_file: str
    language: str
    issue: str
    items: int
    titles: int
    authors: int
    abstracts: int
    dates: int
    pages: int
    links: int
    dois: int


def _text(parent: ET.Element, tag: str) -> str:
    element = parent.find(tag)
    return "" if element is None else "".join(element.itertext()).strip()


def _has_abstract(item: ET.Element) -> bool:
    value = _text(item, "description")
    if not value or value in MISSING_ABSTRACTS:
        return False
    compact = re.sub(r"\s+", "", value)
    if re.fullmatch(r"\d{4}年第?\d+期(?:，?页码?[：:]?[0-9–—~\-]+)?", compact):
        return False
    if re.fullmatch(r"Vol\.\d+,?No\.\d+(?:,?pp?\.[0-9ivxlcdm–—\-]+)?", compact, re.I):
        return False
    return True


def _count(items: list[ET.Element], predicate: Any) -> int:
    return sum(1 for item in items if predicate(item))


def _issue_label(channel: ET.Element) -> str:
    items = channel.findall("item")
    if not items:
        return "未识别"
    for category in items[0].findall("category"):
        value = "".join(category.itertext()).strip()
        match = re.search(r"\d{4}年第?\d+(?:卷第\d+)?期", value)
        if match:
            return match.group(0)
        if re.search(r"\bVol\.\s*\d+.*\bNo\.\s*\d+", value, re.I):
            return re.sub(r",?\s*pp?\..*$", "", value).strip()
    pub_date = _text(items[0], "pubDate")
    try:
        parsed = parsedate_to_datetime(pub_date)
    except (TypeError, ValueError):
        return "未识别"
    return parsed.strftime("%Y-%m-%d")


def _has_pages(item: ET.Element) -> bool:
    categories = ["".join(element.itertext()).strip() for element in item.findall("category")]
    return any(
        re.search(r"(?:页码[：:]|\bpp?\.\s*)\s*[0-9ivxlcdm]+", value, re.I)
        or re.search(r"第?\s*\d+\s*[-–—~]\s*\d+\s*页", value)
        or re.search(r"期[，,]\s*\d+\s*[-–—~]\s*\d+\s*页", value)
        for value in categories
    )


def configured_feeds(config: dict[str, Any]) -> list[dict[str, str]]:
    feeds: list[dict[str, str]] = []
    for journal in config["journals"]:
        feeds.append(
            {
                "name": str(journal["name"]),
                "output_file": str(journal["output_file"]),
                "language": str(journal.get("language", "")),
            }
        )
        translation = journal.get("translation")
        if translation:
            feeds.append(
                {
                    "name": str(translation["title"]),
                    "output_file": str(translation["output_file"]),
                    "language": "translated-en",
                }
            )
    return feeds


def snapshot_feed(root: Path, settings: dict[str, str]) -> FeedSnapshot:
    path = root / "docs" / settings["output_file"]
    channel = ET.parse(path).getroot().find("channel")
    if channel is None:
        raise ValueError("RSS channel is missing")
    items = channel.findall("item")
    return FeedSnapshot(
        name=settings["name"],
        output_file=settings["output_file"],
        language=settings["language"],
        issue=_issue_label(channel),
        items=len(items),
        titles=_count(items, lambda item: bool(_text(item, "title"))),
        authors=_count(
            items,
            lambda item: any(
                "".join(element.itertext()).strip()
                for element in item.findall(f"{{{DC_NS}}}creator")
            ),
        ),
        abstracts=_count(
            items,
            _has_abstract,
        ),
        dates=_count(items, lambda item: bool(_text(item, "pubDate"))),
        pages=_count(items, _has_pages),
        links=_count(items, lambda item: bool(_text(item, "link"))),
        dois=(
            _count(
                items,
                lambda item: any(
                    "".join(element.itertext()).strip()
                    for element in item.findall(f"{{{DC_NS}}}identifier")
                ),
            )
            if not settings["language"].lower().startswith("zh")
            else 0
        ),
    )


def collect_snapshots(root: Path) -> list[FeedSnapshot]:
    config = json.loads((root / "journals.json").read_text(encoding="utf-8"))
    snapshots: list[FeedSnapshot] = []
    for settings in configured_feeds(config):
        try:
            snapshots.append(snapshot_feed(root, settings))
        except (OSError, ET.ParseError, ValueError):
            snapshots.append(
                FeedSnapshot(
                    name=settings["name"],
                    output_file=settings["output_file"],
                    language=settings["language"],
                    issue="现有 RSS 无法读取",
                    items=0,
                    titles=0,
                    authors=0,
                    abstracts=0,
                    dates=0,
                    pages=0,
                    links=0,
                    dois=0,
                )
            )
    return snapshots


def load_safe_result(path: str, expected_kind: str) -> dict[str, Any] | None:
    if not path:
        return None
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError):
        return None
    if expected_kind == "update-rss":
        if not isinstance(value, dict) or set(value) != {
            "schema_version",
            "kind",
            "outcome",
            "current_slug",
            "feeds",
        }:
            return None
        if (
            value["schema_version"] != 1
            or value["kind"] != "update-rss"
            or value["outcome"] not in {"running", "success"}
            or value["current_slug"] not in {"", *UPDATE_FEEDS}
            or not isinstance(value["feeds"], list)
        ):
            return None
        safe_feeds: list[dict[str, Any]] = []
        seen: set[str] = set()
        for feed in value["feeds"]:
            if not isinstance(feed, dict) or set(feed) != {
                "slug",
                "name",
                "items",
                "status",
            }:
                return None
            slug = feed["slug"]
            if slug not in UPDATE_FEEDS or slug in seen or feed["name"] != UPDATE_FEEDS[slug]:
                return None
            if not isinstance(feed["items"], int) or not 1 <= feed["items"] <= 100:
                return None
            if feed["status"] not in UPDATE_FEED_STATUSES:
                return None
            safe_feeds.append(feed)
            seen.add(slug)
        if value["current_slug"] and value["current_slug"] in seen:
            return None
        return {**value, "feeds": safe_feeds}

    if not isinstance(value, dict) or set(value) != {
        "schema_version",
        "kind",
        "batch_id",
        "collected_at",
        "outcome",
        "feeds",
        "failures",
        "changed_files",
    }:
        return None
    if value["schema_version"] != 1 or value["kind"] != expected_kind:
        return None
    if value["kind"] not in ALLOWED_RESULT_KINDS or value["outcome"] not in OUTCOMES:
        return None
    if not isinstance(value["batch_id"], str) or not re.fullmatch(
        r"[0-9a-f]{24}", value["batch_id"]
    ):
        return None
    if not isinstance(value["collected_at"], str) or len(value["collected_at"]) > 50:
        return None
    try:
        datetime.fromisoformat(value["collected_at"].replace("Z", "+00:00"))
    except ValueError:
        return None
    if not isinstance(value["feeds"], list) or not isinstance(value["failures"], list):
        return None
    safe_feeds: list[dict[str, Any]] = []
    seen_slugs: set[str] = set()
    for feed in value["feeds"]:
        if not isinstance(feed, dict) or set(feed) != ALLOWED_FEED_FIELDS:
            return None
        if feed["slug"] not in ALLOWED_SLUGS or feed["slug"] in seen_slugs:
            return None
        if not isinstance(feed["name"], str) or feed["name"] not in {
            "数量经济技术经济研究",
            "管理世界",
        }:
            return None
        if not isinstance(feed["issue"], str) or not re.fullmatch(
            r"20\d{2}年第(?:[1-9]|1[0-2])期", feed["issue"]
        ):
            return None
        if not isinstance(feed["items"], int) or not 1 <= feed["items"] <= 30:
            return None
        if feed["status"] not in FEED_STATUSES:
            return None
        seen_slugs.add(feed["slug"])
        safe_feeds.append(feed)
    safe_failures: list[dict[str, str]] = []
    for failure in value["failures"]:
        if not isinstance(failure, dict) or set(failure) != ALLOWED_FAILURE_FIELDS:
            return None
        if failure["slug"] not in ALLOWED_SLUGS or failure["slug"] in seen_slugs:
            return None
        if failure["stage"] not in FAILURE_STAGES or failure["code"] not in FAILURE_CODES:
            return None
        seen_slugs.add(failure["slug"])
        safe_failures.append(failure)
    if expected_kind == "gitee-receive" and seen_slugs != ALLOWED_SLUGS:
        return None
    if not isinstance(value["changed_files"], list) or any(
        path not in {
            "docs/shuliang-jingji-jishu-jingji-yanjiu.xml",
            "docs/guanli-shijie.xml",
        }
        for path in value["changed_files"]
    ):
        return None
    return {**value, "feeds": safe_feeds, "failures": safe_failures}


def _coverage(found: int, total: int) -> str:
    return f"{found}/{total}"


def _snapshot_table(snapshots: list[FeedSnapshot], *, chinese: bool) -> list[str]:
    selected = [
        snapshot
        for snapshot in snapshots
        if (snapshot.language.lower().startswith("zh")) == chinese
    ]
    if chinese:
        lines = [
            "| 期刊 | 当前期次或日期 | 条目 | 标题 | 作者 | 摘要 | 日期 | 页码 | 官网链接 |",
            "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    else:
        lines = [
            "| 期刊 | 当前期次或日期 | 条目 | 标题 | 作者 | 摘要 | 日期 | 页码 | DOI | 官网链接 |",
            "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    for snapshot in selected:
        values = [
            snapshot.name,
            snapshot.issue,
            str(snapshot.items),
            _coverage(snapshot.titles, snapshot.items),
            _coverage(snapshot.authors, snapshot.items),
            _coverage(snapshot.abstracts, snapshot.items),
            _coverage(snapshot.dates, snapshot.items),
            _coverage(snapshot.pages, snapshot.items),
        ]
        if not chinese:
            values.append(_coverage(snapshot.dois, snapshot.items))
        values.append(_coverage(snapshot.links, snapshot.items))
        lines.append("| " + " | ".join(values) + " |")
    return lines


def render_report(
    *,
    snapshots: list[FeedSnapshot],
    kind: str,
    outcome: str,
    reason: str,
    publish_state: str,
    publish_detail: str,
    run_id: str,
    run_attempt: str,
    generated_at: datetime,
    safe_result: dict[str, Any] | None,
) -> str:
    utc = generated_at.astimezone(timezone.utc)
    beijing = utc.astimezone(ZoneInfo("Asia/Shanghai"))
    run_url = f"https://github.com/AlistairZhang/journal-rss-relay/actions/runs/{run_id}"
    lines = [
        "# RSS 运行状态报告",
        "",
        f"- **结果：** {OUTCOMES[outcome]}",
        f"- **任务：** {KINDS[kind]}",
        f"- **检查时间：** {beijing.strftime('%Y-%m-%d %H:%M:%S')}（北京时间）",
        f"- **UTC 时间：** {utc.strftime('%Y-%m-%d %H:%M:%S')} UTC",
        f"- **运行记录：** [GitHub Actions #{run_id}（第 {run_attempt} 次尝试）]({run_url})",
        f"- **RSS 发布：** {PUBLISH_STATES[publish_state]}",
        f"- **失败原因：** {REASONS[reason]}",
        "",
        "> 本报告只保存状态、数量和字段覆盖率，不保存网页正文、文章题录、原始错误响应或密钥。",
    ]
    if publish_detail:
        lines.insert(7, f"- **发布说明：** {publish_detail}")
    if safe_result is not None and kind == "gitee-receive":
        lines += ["", "## 本次国内采集结果", ""]
        if safe_result["feeds"]:
            lines += [
                "| 期刊 | 期次 | 条目 | 处理结果 |",
                "| --- | --- | ---: | --- |",
            ]
            for feed in safe_result["feeds"]:
                lines.append(
                    f"| {feed['name']} | {feed['issue']} | {feed['items']} | "
                    f"{FEED_STATUSES[feed['status']]} |"
                )
        if safe_result["failures"]:
            names = {"sljjjsjjyj": "数量经济技术经济研究", "glsj": "管理世界"}
            lines += [
                "",
                "| 失败期刊 | 失败阶段 | 归类原因 |",
                "| --- | --- | --- |",
            ]
            for failure in safe_result["failures"]:
                lines.append(
                    f"| {names[failure['slug']]} | {FAILURE_STAGES[failure['stage']]} | "
                    f"{FAILURE_CODES[failure['code']]} |"
                )
    if safe_result is not None and kind == "update-rss":
        lines += [
            "",
            "## 本次期刊检查进度",
            "",
            "| 期刊 | 已取得条目 | 本轮处理 |",
            "| --- | ---: | --- |",
        ]
        for feed in safe_result["feeds"]:
            lines.append(
                f"| {feed['name']} | {feed['items']} | "
                f"{UPDATE_FEED_STATUSES[feed['status']]} |"
            )
        if safe_result["current_slug"]:
            lines.append(
                f"| {UPDATE_FEEDS[safe_result['current_slug']]} | — | "
                "处理到该期刊时中断 |"
            )
    lines += ["", "## 当前 RSS 字段状态", "", "### 中文期刊", ""]
    lines += _snapshot_table(snapshots, chinese=True)
    lines += ["", "### 英文期刊及中文版", ""]
    lines += _snapshot_table(snapshots, chinese=False)
    lines += [
        "",
        "“0/条目数”表示当前 RSS 未识别到该字段；中文期刊表不检查、也不报告 DOI。",
        "",
    ]
    return "\n".join(lines)


def update_index(reports_root: Path) -> None:
    reports = sorted(
        (
            path
            for path in reports_root.glob("*/*/*.md")
            if path.name != "index.md"
        ),
        reverse=True,
    )
    lines = [
        "# RSS 状态报告索引",
        "",
        "每次自动检查或国内采集接收都会生成一份独立报告，按时间倒序排列。",
        "",
    ]
    for path in reports:
        relative = path.relative_to(reports_root).as_posix()
        label = path.stem.replace("T", " ").replace("Z-", " UTC · ")
        lines.append(f"- [{label}]({relative})")
    (reports_root / "index.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_report(
    root: Path,
    *,
    kind: str,
    outcome: str,
    reason: str,
    publish_state: str,
    publish_detail: str = "",
    run_id: str,
    run_attempt: str,
    generated_at: datetime,
    result_path: str,
) -> Path:
    if not re.fullmatch(r"\d+", run_id) or not re.fullmatch(r"\d+", run_attempt):
        raise ValueError("run id and attempt must be numeric")
    safe_result = load_safe_result(result_path, kind)
    if safe_result is not None and reason in {"none", "source_failure", "translation_failed"}:
        result_outcome = safe_result["outcome"]
        if result_outcome in OUTCOMES:
            outcome = result_outcome
        if safe_result.get("failures") and reason == "none":
            reason = "source_failure"
    snapshots = collect_snapshots(root)
    utc = generated_at.astimezone(timezone.utc)
    directory = root / "status-reports" / utc.strftime("%Y") / utc.strftime("%m")
    directory.mkdir(parents=True, exist_ok=True)
    filename = (
        f"{utc.strftime('%Y-%m-%dT%H-%M-%SZ')}-{kind}-run-{run_id}"
        f"-attempt-{run_attempt}.md"
    )
    report_path = directory / filename
    report_path.write_text(
        render_report(
            snapshots=snapshots,
            kind=kind,
            outcome=outcome,
            reason=reason,
            publish_state=publish_state,
            publish_detail=publish_detail,
            run_id=run_id,
            run_attempt=run_attempt,
            generated_at=generated_at,
            safe_result=safe_result,
        ),
        encoding="utf-8",
    )
    update_index(root / "status-reports")
    return report_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--kind", choices=sorted(KINDS), required=True)
    parser.add_argument("--outcome", choices=sorted(OUTCOMES), required=True)
    parser.add_argument("--reason", choices=sorted(REASONS), required=True)
    parser.add_argument("--publish-state", choices=sorted(PUBLISH_STATES), required=True)
    parser.add_argument("--publish-detail", choices=("", "rss_saved_pages_pending"), default="")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--run-attempt", required=True)
    parser.add_argument("--result-json", default="")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report_path = write_report(
        ROOT,
        kind=args.kind,
        outcome=args.outcome,
        reason=args.reason,
        publish_state=args.publish_state,
        publish_detail=(
            "RSS 已保存到 GitHub，Pages 将在本报告保存成功后发布。"
            if args.publish_detail == "rss_saved_pages_pending"
            else ""
        ),
        run_id=args.run_id,
        run_attempt=args.run_attempt,
        generated_at=datetime.now(timezone.utc),
        result_path=args.result_json,
    )
    relative = report_path.relative_to(ROOT).as_posix()
    output_path = os.environ.get("GITHUB_OUTPUT", "")
    if output_path:
        with open(output_path, "a", encoding="utf-8") as handle:
            handle.write(f"report_path={relative}\n")
    print(f"Created status report: {relative}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"STATUS REPORT ERROR: {type(error).__name__}", file=sys.stderr)
        raise SystemExit(1)
