from __future__ import annotations

import base64
import json
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest import mock

import sync_xml_to_gitee as sync


class SyncXmlToGiteeTests(unittest.TestCase):
    def test_configured_files_contains_all_original_and_translated_outputs(self) -> None:
        config = json.loads((sync.ROOT / "journals.json").read_text(encoding="utf-8"))
        outputs = sync.configured_xml_files(config)
        self.assertEqual(len(outputs), 11)
        self.assertIn(("American-Economic-Review-zh.xml", "AER中文版"), outputs)

    def test_rejects_unsafe_output_filename(self) -> None:
        config = {
            "journals": [
                {"output_file": "../bad.xml", "commit_label": "bad"},
            ]
        }
        with self.assertRaises(sync.SyncError):
            sync.configured_xml_files(config)

    def test_validated_xml_requires_items(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            docs = Path(directory)
            (docs / "empty.xml").write_text(
                '<rss version="2.0"><channel><title>x</title></channel></rss>',
                encoding="utf-8",
            )
            with mock.patch.object(sync, "DOCS", docs):
                with self.assertRaises(sync.SyncError):
                    sync.validated_local_xml("empty.xml")

    def test_remote_content_is_decoded_exactly(self) -> None:
        raw = b'<rss version="2.0" />\n'
        payload = {"content": base64.b64encode(raw).decode("ascii")}
        self.assertEqual(sync.decoded_remote_content("test.xml", payload), raw)

    def test_gitee_empty_list_means_remote_file_does_not_exist(self) -> None:
        with mock.patch.object(sync, "request_json", return_value=(200, [])):
            self.assertIsNone(sync.remote_file("owner", "repo", "master", "new.xml", "token"))

    def test_publish_encoding_preserves_xml_without_raw_non_ascii(self) -> None:
        raw = (
            '<?xml version="1.0" encoding="utf-8"?>'
            '<rss version="2.0"><channel><title>经济研究 - 祥仔</title>'
            '<item><title>Rüdiger 的文章</title></item></channel></rss>'
        ).encode("utf-8")
        published = sync.gitee_publishable_xml("test.xml", raw)
        self.assertTrue(published.isascii())
        self.assertNotIn("经济研究".encode("utf-8"), published)
        self.assertEqual(
            ET.tostring(ET.fromstring(published), encoding="utf-8"),
            ET.tostring(ET.fromstring(raw), encoding="utf-8"),
        )

    def test_main_skips_identical_and_updates_changed_files(self) -> None:
        config = {
            "journals": [
                {"output_file": "one.xml", "commit_label": "One"},
                {"output_file": "two.xml", "commit_label": "Two"},
            ]
        }
        rss = b'<rss version="2.0"><channel><item><title>x</title></item></channel></rss>'
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "journals.json").write_text(json.dumps(config), encoding="utf-8")
            with (
                mock.patch.object(sync, "ROOT", root),
                mock.patch.object(sync, "validated_local_xml", return_value=rss),
                mock.patch.object(sync, "gitee_publishable_xml", side_effect=lambda _name, raw: raw),
                mock.patch.object(
                    sync,
                    "remote_file",
                    side_effect=[
                        {"sha": "one", "content": base64.b64encode(rss).decode("ascii")},
                        {"sha": "two", "content": base64.b64encode(b"old").decode("ascii")},
                    ],
                ),
                mock.patch.object(sync, "put_file") as put_file,
                mock.patch.dict(
                    "os.environ",
                    {"GITEE_XML_SYNC_TOKEN": "redacted-test-token"},
                    clear=True,
                ),
            ):
                self.assertEqual(sync.main(), 0)
            put_file.assert_called_once()
            self.assertEqual(put_file.call_args.args[3], "two.xml")


if __name__ == "__main__":
    unittest.main()
