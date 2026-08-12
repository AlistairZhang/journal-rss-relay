#!/usr/bin/env python3
"""Tests for permanent, redacted RSS status reports."""

from __future__ import annotations

import json
import tempfile
import unittest
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

import status_report


DC_NS = "http://purl.org/dc/elements/1.1/"


def write_feed(path: Path, *, title: str, chinese: bool) -> None:
    rss = ET.Element("rss", {"version": "2.0"})
    channel = ET.SubElement(rss, "channel")
    ET.SubElement(channel, "title").text = title
    item = ET.SubElement(channel, "item")
    ET.SubElement(item, "title").text = "Private article title"
    ET.SubElement(item, "link").text = "https://example.com/article"
    ET.SubElement(item, f"{{{DC_NS}}}creator").text = "Private Author"
    ET.SubElement(item, "pubDate").text = "Sat, 01 Aug 2026 00:00:00 +0000"
    ET.SubElement(item, "description").text = "Private abstract text"
    ET.SubElement(item, "category").text = (
        "2026年第8期，1-20页" if chinese else "Vol. 116, No. 8, pp. 1–20"
    )
    ET.SubElement(item, f"{{{DC_NS}}}identifier").text = "https://doi.org/10.test/private"
    ET.SubElement(item, "guid").text = "private-guid"
    path.parent.mkdir(parents=True, exist_ok=True)
    ET.indent(rss, space="  ")
    path.write_bytes(ET.tostring(rss, encoding="utf-8", xml_declaration=True))


class StatusReportTests(unittest.TestCase):
    def make_root(self) -> tuple[tempfile.TemporaryDirectory, Path]:
        temporary = tempfile.TemporaryDirectory()
        root = Path(temporary.name)
        config = {
            "journals": [
                {
                    "name": "管理世界",
                    "language": "zh-cn",
                    "output_file": "guanli.xml",
                },
                {
                    "name": "American Economic Review",
                    "language": "en",
                    "output_file": "aer.xml",
                    "translation": {
                        "title": "American Economic Review中文版",
                        "output_file": "aer-zh.xml",
                    },
                },
            ]
        }
        (root / "journals.json").write_text(json.dumps(config), encoding="utf-8")
        write_feed(root / "docs" / "guanli.xml", title="管理世界", chinese=True)
        write_feed(root / "docs" / "aer.xml", title="AER", chinese=False)
        write_feed(root / "docs" / "aer-zh.xml", title="AER中文版", chinese=False)
        return temporary, root

    def test_report_is_independent_indexed_and_does_not_copy_articles(self) -> None:
        temporary, root = self.make_root()
        self.addCleanup(temporary.cleanup)
        path = status_report.write_report(
            root,
            kind="update-rss",
            outcome="success",
            reason="none",
            publish_state="unchanged",
            run_id="12345",
            run_attempt="1",
            generated_at=datetime(2026, 8, 13, 1, 2, 3, tzinfo=timezone.utc),
            result_path="",
        )
        report = path.read_text(encoding="utf-8")
        index = (root / "status-reports" / "index.md").read_text(encoding="utf-8")
        self.assertIn("成功", report)
        self.assertIn(path.name, index)
        self.assertNotIn("Private article title", report)
        self.assertNotIn("Private Author", report)
        self.assertNotIn("Private abstract text", report)
        self.assertNotIn("10.test/private", report)

    def test_chinese_table_has_no_doi_column_but_english_table_does(self) -> None:
        temporary, root = self.make_root()
        self.addCleanup(temporary.cleanup)
        path = status_report.write_report(
            root,
            kind="update-rss",
            outcome="success",
            reason="none",
            publish_state="published",
            run_id="222",
            run_attempt="3",
            generated_at=datetime(2026, 8, 13, tzinfo=timezone.utc),
            result_path="",
        )
        report = path.read_text(encoding="utf-8")
        chinese = report.split("### 中文期刊", 1)[1].split("### 英文期刊及中文版", 1)[0]
        english = report.split("### 英文期刊及中文版", 1)[1]
        self.assertNotIn("DOI", chinese)
        self.assertIn("DOI", english)

    def test_safe_failure_is_translated_without_raw_error_text(self) -> None:
        temporary, root = self.make_root()
        self.addCleanup(temporary.cleanup)
        result_path = root / "result.json"
        result_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "kind": "gitee-receive",
                    "batch_id": "a" * 24,
                    "collected_at": "2026-08-13T01:00:00Z",
                    "outcome": "partial",
                    "feeds": [
                        {
                            "slug": "sljjjsjjyj",
                            "name": "数量经济技术经济研究",
                            "issue": "2026年第7期",
                            "items": 10,
                            "status": "unchanged",
                        }
                    ],
                    "failures": [
                        {
                            "slug": "glsj",
                            "stage": "fetch",
                            "code": "source_unreachable",
                        }
                    ],
                    "changed_files": [],
                }
            ),
            encoding="utf-8",
        )
        path = status_report.write_report(
            root,
            kind="gitee-receive",
            outcome="success",
            reason="none",
            publish_state="unchanged",
            run_id="333",
            run_attempt="1",
            generated_at=datetime(2026, 8, 13, tzinfo=timezone.utc),
            result_path=str(result_path),
        )
        report = path.read_text(encoding="utf-8")
        self.assertIn("部分成功", report)
        self.assertIn("无法连接期刊官网", report)

    def test_invalid_result_is_ignored_and_never_echoed(self) -> None:
        temporary, root = self.make_root()
        self.addCleanup(temporary.cleanup)
        result_path = root / "result.json"
        result_path.write_text(
            '{"secret":"redacted-test-token","exception":"private response body"}',
            encoding="utf-8",
        )
        path = status_report.write_report(
            root,
            kind="gitee-receive",
            outcome="failure",
            reason="receiver_failed",
            publish_state="not-published",
            run_id="444",
            run_attempt="1",
            generated_at=datetime(2026, 8, 13, tzinfo=timezone.utc),
            result_path=str(result_path),
        )
        report = path.read_text(encoding="utf-8")
        self.assertIn("国内采集数据包未通过接收校验", report)
        self.assertNotIn("redacted-test-token", report)
        self.assertNotIn("private response body", report)

    def test_update_progress_identifies_where_collection_stopped(self) -> None:
        temporary, root = self.make_root()
        self.addCleanup(temporary.cleanup)
        result_path = root / "update-result.json"
        result_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "kind": "update-rss",
                    "outcome": "running",
                    "current_slug": "jjdl",
                    "feeds": [
                        {"slug": "jjyj", "name": "经济研究", "items": 11, "status": "fetched"},
                        {
                            "slug": "sljjjsjjyj",
                            "name": "数量经济技术经济研究",
                            "items": 10,
                            "status": "preserved",
                        },
                    ],
                }
            ),
            encoding="utf-8",
        )
        path = status_report.write_report(
            root,
            kind="update-rss",
            outcome="failure",
            reason="update_failed",
            publish_state="not-published",
            run_id="555",
            run_attempt="1",
            generated_at=datetime(2026, 8, 13, tzinfo=timezone.utc),
            result_path=str(result_path),
        )
        report = path.read_text(encoding="utf-8")
        self.assertIn("经济研究 | 11 | 已获取", report)
        self.assertIn("经济地理 | — | 处理到该期刊时中断", report)
        self.assertIn("获取或解析未完成", report)


if __name__ == "__main__":
    unittest.main()
