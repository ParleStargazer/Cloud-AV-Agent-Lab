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
