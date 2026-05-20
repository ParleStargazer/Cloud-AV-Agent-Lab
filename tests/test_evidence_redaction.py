from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest import TestCase

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cloud_av_agent_lab.evidence.redaction import (
    RedactionContext,
    RedactionError,
    redact_text_artifact,
)


class EvidenceRedactionTests(TestCase):
    def test_json_redacts_paths_and_secrets_but_preserves_hashes(self) -> None:
        payload = {
            "case_id": "eicar-001__huorong",
            "sample_id": "eicar-001",
            "run_id": "run-001",
            "sha256": "a" * 64,
            "expected_sha256": "b" * 64,
            "password_protected": False,
            "raw_binary_included": False,
            "token": "super-secret-token",
            "api_key": "super-secret-key",
            "recname": "TEST/AVEngTestFile!EICAR",
            "workspace": r"C:\CloudAvAgentLab\cases\eicar-001__huorong",
            "user_file": r"C:\Users\Alice\Downloads\eicar.txt",
        }
        result = redact_text_artifact(
            "case_collection.json",
            json.dumps(payload).encode("utf-8"),
            context=RedactionContext(
                case_workspace=r"C:\CloudAvAgentLab\cases\eicar-001__huorong"
            ),
        )

        decoded = json.loads(result.content.decode("utf-8"))
        self.assertEqual(decoded["case_id"], "eicar-001__huorong")
        self.assertEqual(decoded["sample_id"], "eicar-001")
        self.assertEqual(decoded["run_id"], "run-001")
        self.assertEqual(decoded["sha256"], "a" * 64)
        self.assertEqual(decoded["expected_sha256"], "b" * 64)
        self.assertEqual(decoded["recname"], "TEST/AVEngTestFile!EICAR")
        self.assertFalse(decoded["password_protected"])
        self.assertFalse(decoded["raw_binary_included"])
        self.assertEqual(decoded["token"], "<redacted>")
        self.assertEqual(decoded["api_key"], "<redacted>")
        self.assertEqual(decoded["workspace"], "<case_workspace>")
        self.assertIn("<user_home>", decoded["user_file"])

    def test_text_redacts_authorization_cookie_and_assignments(self) -> None:
        text = (
            "Authorization: Bearer abc.def\n"
            "Cookie: a=b; c=d\n"
            "api_key=secret-value\n"
            "sha256=" + "0" * 64 + "\n"
        )

        result = redact_text_artifact("notes.txt", text.encode("utf-8"))
        redacted = result.content.decode("utf-8")

        self.assertIn("Authorization: Bearer <redacted>", redacted)
        self.assertIn("Cookie: <redacted>", redacted)
        self.assertIn("api_key=<redacted>", redacted)
        self.assertIn("sha256=" + "0" * 64, redacted)

    def test_jsonl_bad_line_falls_back_to_text_redaction(self) -> None:
        text = (
            json.dumps({"event_type": "ok", "token": "secret"}) + "\n"
            "{bad json Authorization: Bearer abc}\n"
        )

        result = redact_text_artifact("events.jsonl", text.encode("utf-8"))
        lines = result.content.decode("utf-8").splitlines()

        self.assertEqual(len(lines), 2)
        self.assertEqual(json.loads(lines[0])["token"], "<redacted>")
        self.assertIn("Authorization: Bearer <redacted>", lines[1])
        self.assertIn(
            "jsonl_line_parse_failed_fallback_text_redaction", result.warnings
        )

    def test_utf16le_text_with_nul_is_not_binary(self) -> None:
        content = "Authorization: Bearer abc".encode("utf-16le")

        result = redact_text_artifact("notes.txt", content)

        self.assertEqual(result.encoding, "utf-16le")
        self.assertIn(
            "Authorization: Bearer <redacted>",
            result.content.decode("utf-8"),
        )

    def test_binary_like_text_candidate_fails_closed(self) -> None:
        with self.assertRaises(RedactionError):
            redact_text_artifact("notes.txt", b"\x00\x01\x02\x03")
