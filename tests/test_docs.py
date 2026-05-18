from __future__ import annotations

import sys
from pathlib import Path
from unittest import TestCase

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


class DocsTests(TestCase):
    def test_execution_model_exists_and_forbids_arbitrary_commands(self) -> None:
        path = ROOT / "docs" / "EXECUTION_MODEL.md"

        self.assertTrue(path.is_file())
        text = path.read_text(encoding="utf-8").casefold()
        self.assertIn("must not expose arbitrary command execution", text)
        self.assertIn("must not accept or run shell", text)
        self.assertIn("execute_uploaded_sample", text)
        self.assertIn("execution_disabled", text)

    def test_collection_model_exists_and_documents_conservative_verdicts(self) -> None:
        path = ROOT / "docs" / "COLLECTION_MODEL.md"

        self.assertTrue(path.is_file())
        text = path.read_text(encoding="utf-8").casefold()
        self.assertIn("collector plugin model", text)
        self.assertIn("unified event timeline", text)
        self.assertIn("time window", text)
        self.assertIn("conservative verdict", text)
        self.assertIn("removed_after_save", text)
        self.assertIn("evaluator", text)
        self.assertIn("exporter", text)
        self.assertIn("uploaded sample body", text)

    def test_desktop_worker_model_documents_loopback_and_no_arbitrary_exec(
        self,
    ) -> None:
        path = ROOT / "docs" / "DESKTOP_WORKER.md"

        self.assertTrue(path.is_file())
        text = path.read_text(encoding="utf-8").casefold()
        self.assertIn("127.0.0.1", text)
        self.assertIn("must never expose arbitrary command execution", text)
        self.assertIn("execution lease", text)
        self.assertIn("session 0", text)
