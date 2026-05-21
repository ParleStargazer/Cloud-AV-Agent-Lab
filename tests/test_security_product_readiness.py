from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cloud_av_agent_lab.guest_agent_server.security_product_readiness import (
    SecurityProductReadinessContext,
    run_security_product_readiness_probe,
)


class SecurityProductReadinessProbeTests(TestCase):
    def test_huorong_log_dir_and_db_present_returns_ready(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            log_dir = root / "huorong" / "sysdiag"
            log_dir.mkdir(parents=True)
            (log_dir / "log.db").write_bytes(b"sqlite-placeholder")
            workspace = root / "case"
            workspace.mkdir()

            result = run_security_product_readiness_probe(
                SecurityProductReadinessContext(
                    product_id="huorong",
                    workspace=workspace,
                    log_dir=log_dir,
                ),
                "huorong",
            )

            self.assertEqual(result.state, "ready")
            self.assertEqual(result.confidence, "medium")
            self.assertEqual(result.scope, "log_observability")
            self.assertEqual(result.protection_state, "unknown")
            self.assertTrue(
                (
                    workspace / "security-product-readiness" / "huorong" / "log.db"
                ).is_file()
            )

    def test_huorong_missing_log_db_returns_not_ready(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            log_dir = root / "huorong" / "sysdiag"
            log_dir.mkdir(parents=True)
            workspace = root / "case"
            workspace.mkdir()

            result = run_security_product_readiness_probe(
                SecurityProductReadinessContext(
                    product_id="huorong",
                    workspace=workspace,
                    log_dir=log_dir,
                ),
                "huorong",
            )

            self.assertEqual(result.state, "not_ready")
            self.assertIn("log.db", " ".join(result.errors))

    def test_huorong_core_snapshot_copy_failure_returns_unknown(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            log_dir = root / "huorong" / "sysdiag"
            log_dir.mkdir(parents=True)
            (log_dir / "log.db").write_bytes(b"sqlite-placeholder")
            workspace = root / "case"
            workspace.mkdir()

            with patch(
                "cloud_av_agent_lab.guest_agent_server."
                "security_product_readiness.huorong.shutil.copy2",
                side_effect=PermissionError("locked"),
            ):
                result = run_security_product_readiness_probe(
                    SecurityProductReadinessContext(
                        product_id="huorong",
                        workspace=workspace,
                        log_dir=log_dir,
                    ),
                    "huorong",
                )

            self.assertEqual(result.state, "unknown")
            self.assertTrue(result.errors)

    def test_huorong_auxiliary_copy_failure_returns_partial(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            log_dir = root / "huorong" / "sysdiag"
            log_dir.mkdir(parents=True)
            (log_dir / "log.db").write_bytes(b"sqlite-placeholder")
            (log_dir / "log.db-wal").write_bytes(b"wal-placeholder")
            workspace = root / "case"
            workspace.mkdir()

            original_copy2 = __import__("shutil").copy2

            def fail_wal(source: Path, destination: Path) -> object:
                if Path(source).name == "log.db-wal":
                    raise PermissionError("locked")
                return original_copy2(source, destination)

            with patch(
                "cloud_av_agent_lab.guest_agent_server."
                "security_product_readiness.huorong.shutil.copy2",
                side_effect=fail_wal,
            ):
                result = run_security_product_readiness_probe(
                    SecurityProductReadinessContext(
                        product_id="huorong",
                        workspace=workspace,
                        log_dir=log_dir,
                    ),
                    "huorong",
                )

            self.assertEqual(result.state, "partial")
            self.assertIn("WAL/SHM", " ".join(result.warnings))

    def test_unsupported_product_returns_unsupported_without_env_leak(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "case"
            workspace.mkdir()

            result = run_security_product_readiness_probe(
                SecurityProductReadinessContext(
                    product_id="unsupported",
                    workspace=workspace,
                ),
                "unsupported",
            )

            payload_text = str(result.to_dict())
            self.assertEqual(result.state, "unsupported")
            self.assertNotIn("CLOUD_AV_GUEST_AGENT_TOKEN", payload_text)
            self.assertNotIn("TENCENTCLOUD_SECRET", payload_text)
