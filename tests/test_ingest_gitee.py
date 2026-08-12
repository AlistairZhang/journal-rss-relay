#!/usr/bin/env python3
"""Offline tests for signed Gitee status outcomes."""

from __future__ import annotations

import base64
import gzip
import hashlib
import hmac
import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import ingest_gitee as ingest


SECRET = "offline-test-signing-secret-000000000000"


def make_event(inner: dict) -> dict:
    raw = json.dumps(
        inner, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    digest = hashlib.sha256(raw).hexdigest()
    compressed = gzip.compress(raw, compresslevel=9, mtime=0)
    collected_at = (
        datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    )
    signature = hmac.new(
        SECRET.encode("utf-8"),
        ingest._signing_message(
            ingest.SCHEMA, digest[:24], collected_at, digest, compressed
        ),
        hashlib.sha256,
    ).hexdigest()
    envelope = json.dumps(
        {
            "schema": ingest.SCHEMA,
            "encoding": "gzip+base64",
            "batch_id": digest[:24],
            "collected_at": collected_at,
            "sha256": digest,
            "data": base64.b64encode(compressed).decode("ascii"),
            "signature": f"sha256={signature}",
        },
        separators=(",", ":"),
    )
    return {
        "repository": {"full_name": ingest.EXPECTED_REPOSITORY},
        "sender": {"login": ingest.EXPECTED_SENDER},
        "inputs": {"envelope": envelope},
    }


class GiteeStatusProtocolTests(unittest.TestCase):
    def run_main(self, inner: dict) -> tuple[dict, str]:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            event_path = root / "event.json"
            result_path = root / "result.json"
            output_path = root / "output.txt"
            event_path.write_text(json.dumps(make_event(inner)), encoding="utf-8")
            with mock.patch.dict(
                os.environ,
                {
                    "GITHUB_EVENT_PATH": str(event_path),
                    "GITHUB_EVENT_NAME": "workflow_dispatch",
                    "GITEE_RELAY_HMAC_SECRET": SECRET,
                    "STATUS_RESULT_PATH": str(result_path),
                    "GITHUB_OUTPUT": str(output_path),
                },
                clear=True,
            ):
                ingest.main()
            return (
                json.loads(result_path.read_text(encoding="utf-8")),
                output_path.read_text(encoding="utf-8"),
            )

    def test_two_sanitized_failures_create_a_failure_result(self) -> None:
        result, outputs = self.run_main(
            {
                "schema_version": 2,
                "journals": [],
                "failures": [
                    {
                        "slug": "sljjjsjjyj",
                        "stage": "fetch",
                        "code": "source_unreachable",
                    },
                    {
                        "slug": "glsj",
                        "stage": "parse",
                        "code": "invalid_source",
                    },
                ],
            }
        )
        self.assertEqual(result["outcome"], "failure")
        self.assertEqual(result["feeds"], [])
        self.assertEqual(len(result["failures"]), 2)
        self.assertIn("changed=false", outputs)

    def test_v2_requires_an_outcome_for_both_journals(self) -> None:
        inner = {
            "schema_version": 2,
            "journals": [],
            "failures": [
                {
                    "slug": "glsj",
                    "stage": "fetch",
                    "code": "source_unreachable",
                }
            ],
        }
        with self.assertRaisesRegex(RuntimeError, "do not cover all allowed journals"):
            self.run_main(inner)

    def test_failure_fields_cannot_contain_exception_text(self) -> None:
        inner = {
            "schema_version": 2,
            "journals": [],
            "failures": [
                {
                    "slug": "sljjjsjjyj",
                    "stage": "fetch",
                    "code": "source_unreachable",
                    "message": "secret response body",
                },
                {
                    "slug": "glsj",
                    "stage": "fetch",
                    "code": "source_unreachable",
                },
            ],
        }
        with self.assertRaisesRegex(RuntimeError, "failure fields are invalid"):
            self.run_main(inner)

    def test_legacy_payload_cannot_silently_omit_a_journal(self) -> None:
        inner = {"schema_version": 1, "journals": []}
        event = make_event(inner)
        with tempfile.TemporaryDirectory() as directory:
            event_path = Path(directory) / "event.json"
            event_path.write_text(json.dumps(event), encoding="utf-8")
            with mock.patch.dict(
                os.environ,
                {
                    "GITHUB_EVENT_PATH": str(event_path),
                    "GITHUB_EVENT_NAME": "workflow_dispatch",
                    "GITEE_RELAY_HMAC_SECRET": SECRET,
                },
                clear=True,
            ):
                with self.assertRaisesRegex(RuntimeError, "journal count is invalid"):
                    ingest.read_verified_payload()


if __name__ == "__main__":
    unittest.main()
