from __future__ import annotations

import inspect
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cloud_av_agent_lab.guest_agent_server.security_product_readiness import (
    SecurityProductReadinessContext,
    run_security_product_readiness_probe,
)
from cloud_av_agent_lab.guest_agent_server.security_product_readiness import (
    registry as readiness_registry,
)
from cloud_av_agent_lab.guest_agent_server.security_product_readiness.tencent_pc_manager import (
    TencentPcManagerSecurityProductReadinessProbe,
)

SAMPLE_MD5 = "bd3f9e29ec9ecafc9b8b2475afb3a9a2"


class TencentPcManagerReadinessTests(unittest.TestCase):
    def test_non_windows_returns_unsupported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = _check(Path(tmp), platform_name="Linux")

        self.assertEqual(result.state, "unsupported")
        self.assertEqual(result.scope, "quarantine_metadata_observability")
        self.assertEqual(result.protection_state, "unknown")
        self.assertIn("non_windows_platform", result.reason_codes)

    def test_missing_quarantine_returns_not_ready(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "QQPCMgr"
            root.mkdir()
            workspace = Path(tmp) / "case"
            _write_case_metadata(workspace, SAMPLE_MD5)

            result = _check(root, workspace=workspace)

        self.assertEqual(result.state, "not_ready")
        self.assertIn("quarantine_dir_not_found", result.reason_codes)
        self.assertIn("quarantine directory", " ".join(result.errors))

    def test_quarantine_and_tav_cache_present_returns_ready_with_baseline(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "QQPCMgr"
            quarantine = root / "Quarantine"
            tav_cache = root / "TAVWfsDB" / "TAVCacheFullEx.db"
            quarantine.mkdir(parents=True)
            tav_cache.parent.mkdir(parents=True)
            tav_cache.write_bytes(b"tav-cache-placeholder")
            (quarantine / SAMPLE_MD5).write_bytes(b"x" * 1016)
            (quarantine / f"{SAMPLE_MD5}.ico").write_bytes(b"icon")
            workspace = Path(tmp) / "case"
            _write_case_metadata(workspace, SAMPLE_MD5)

            result = _check(quarantine, workspace=workspace)
            payload = result.to_dict()
            baseline = _check_payload(payload, "tav_quarantine_baseline_recorded")[
                "data"
            ]
            signals = _check_payload(
                payload,
                "tencent_pc_manager_product_presence_signals",
            )["data"]

        self.assertEqual(result.state, "ready")
        self.assertEqual(result.confidence, "medium")
        self.assertEqual(baseline["sample_md5"], SAMPLE_MD5)
        self.assertTrue(baseline["quarantine_container"]["present"])
        self.assertTrue(baseline["icon_sidecar"]["present"])
        self.assertTrue(baseline["tav_cache"]["present"])
        self.assertFalse(baseline["raw_artifacts_copied"])
        self.assertTrue(signals["qqpcmgr_root_exists"])
        self.assertTrue(signals["quarantine_dir_exists"])
        self.assertTrue(signals["tav_cache_exists"])
        self.assertFalse(signals["product_process_observed"])
        self.assertFalse(signals["product_service_observed"])

    def test_tav_cache_missing_returns_partial_warning(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "QQPCMgr"
            quarantine = root / "Quarantine"
            quarantine.mkdir(parents=True)
            workspace = Path(tmp) / "case"
            _write_case_metadata(workspace, SAMPLE_MD5)

            result = _check(quarantine, workspace=workspace)

        self.assertEqual(result.state, "partial")
        self.assertIn("tav_cache_missing", result.warnings)
        self.assertIn("tav_cache_missing", result.reason_codes)

    def test_path_query_exception_returns_unknown(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "QQPCMgr"
            quarantine = root / "Quarantine"
            quarantine.mkdir(parents=True)
            workspace = Path(tmp) / "case"
            _write_case_metadata(workspace, SAMPLE_MD5)

            with patch("pathlib.Path.is_dir", side_effect=OSError("access denied")):
                result = _check(quarantine, workspace=workspace)

        self.assertEqual(result.state, "unknown")
        self.assertIn("product_path_query_failed", result.reason_codes)

    def test_sample_md5_missing_warns_without_reading_uploaded_sample(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "QQPCMgr"
            quarantine = root / "Quarantine"
            tav_cache = root / "TAVWfsDB" / "TAVCacheFullEx.db"
            quarantine.mkdir(parents=True)
            tav_cache.parent.mkdir(parents=True)
            tav_cache.write_bytes(b"tav-cache-placeholder")
            workspace = Path(tmp) / "case"
            workspace.mkdir()
            (workspace / "case_state.json").write_text(
                json.dumps({"sample": {"sha256": "0" * 64}}),
                encoding="utf-8",
            )
            sample_dir = workspace / "sample"
            sample_dir.mkdir()
            sample_file = sample_dir / "sample.bin"
            sample_file.write_bytes(b"must-not-be-read")

            result = _check(quarantine, workspace=workspace)
            payload = result.to_dict()
            baseline = _check_payload(payload, "tav_quarantine_baseline_recorded")[
                "data"
            ]
            sample_still_present = sample_file.is_file()
            raw_artifact_dir_exists = (
                workspace / "security-product-readiness"
            ).exists()

        self.assertEqual(result.state, "ready")
        self.assertIn("sample_md5_missing", result.warnings)
        self.assertEqual(baseline["sample_md5"], "")
        self.assertEqual(baseline["quarantine_container"], {})
        self.assertTrue(sample_still_present)
        self.assertFalse(raw_artifact_dir_exists)

    def test_registry_can_call_tencent_pc_manager_probe(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "QQPCMgr"
            quarantine = root / "Quarantine"
            tav_cache = root / "TAVWfsDB" / "TAVCacheFullEx.db"
            quarantine.mkdir(parents=True)
            tav_cache.parent.mkdir(parents=True)
            tav_cache.write_bytes(b"tav-cache-placeholder")
            workspace = Path(tmp) / "case"
            _write_case_metadata(workspace, SAMPLE_MD5)

            result = run_security_product_readiness_probe(
                SecurityProductReadinessContext(
                    product_id="tencent-pc-manager",
                    workspace=workspace,
                    log_dir=quarantine,
                ),
                "tencent-pc-manager",
            )

        self.assertEqual(result.product_id, "tencent-pc-manager")
        self.assertEqual(result.state, "ready")
        self.assertIn(
            "tencent-pc-manager",
            readiness_registry.supported_security_product_readiness_probes(),
        )

    def test_probe_source_has_no_raw_artifact_copy_or_command_runner(self) -> None:
        from cloud_av_agent_lab.guest_agent_server.security_product_readiness import (
            tencent_pc_manager as readiness_module,
        )

        source = inspect.getsource(readiness_module)
        self.assertNotIn("shutil", source)
        self.assertNotIn("copy", source)
        self.assertNotIn("subprocess", source)
        self.assertNotIn("PowerShell", source)
        self.assertNotIn("cmd.exe", source)
        self.assertNotIn("shell=True", source)
        self.assertNotIn("os.system", source)


def _check(
    log_dir: Path,
    platform_name: str = "Windows",
    workspace: Path | None = None,
):
    workspace = workspace or (log_dir.parent / "case")
    return TencentPcManagerSecurityProductReadinessProbe(
        platform_provider=lambda: platform_name,
    ).check(
        SecurityProductReadinessContext(
            product_id="tencent-pc-manager",
            workspace=workspace,
            log_dir=log_dir,
        )
    )


def _write_case_metadata(workspace: Path, md5: str) -> None:
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "case_state.json").write_text(
        json.dumps({"sample": {"md5": md5, "sha256": "0" * 64}}),
        encoding="utf-8",
    )
    (workspace / "case.json").write_text(
        json.dumps({"sample": {"md5": md5, "sha256": "0" * 64}}),
        encoding="utf-8",
    )


def _check_payload(payload: dict[str, object], name: str) -> dict[str, object]:
    checks = payload["checks"]
    if not isinstance(checks, list):
        raise AssertionError("checks payload must be a list")
    for item in checks:
        if isinstance(item, dict) and item.get("name") == name:
            return item
    raise AssertionError(f"check not found: {name}")


if __name__ == "__main__":
    unittest.main()
