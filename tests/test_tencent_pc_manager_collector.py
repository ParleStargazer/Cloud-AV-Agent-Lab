from __future__ import annotations

import inspect
import json
import os
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path
from unittest import TestCase

from cloud_av_agent_lab.evidence import build_evidence_bundle
from cloud_av_agent_lab.guest_agent_server.collectors import (
    CollectionWindow,
    get_product_log_collector,
)
from cloud_av_agent_lab.guest_agent_server.collectors.tencent_pc_manager import (
    PRODUCT_ID,
    TencentPcManagerLogCollector,
)

SAMPLE_MD5 = "bd3f9e29ec9ecafc9b8b2475afb3a9a2"
SAMPLE_SHA256 = "1" * 64


class TencentPcManagerCollectorTests(TestCase):
    def test_missing_quarantine_returns_not_collected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "QQPCMgr"
            workspace = Path(tmp) / "case"
            root.mkdir()
            workspace.mkdir()

            result = TencentPcManagerLogCollector(log_dir=root).collect(
                workspace,
                _case_context(workspace),
                _window(),
            )

        self.assertEqual(result.collection_state, "not_collected")
        self.assertEqual(result.verdict, "unknown")
        self.assertEqual(result.reason, "product_quarantine_dir_not_found")
        self.assertEqual(result.evidence_count, 0)

    def test_missing_md5_is_structured_unknown_without_reading_sample(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, workspace = _make_dirs(Path(tmp))
            (root / "TAVWfsDB" / "TAVCacheFullEx.db").write_bytes(b"cache")
            sample = workspace / "sample" / "sample.bin"
            sample.parent.mkdir()
            sample.write_bytes(b"do-not-read")

            context = _case_context(workspace)
            context["sample_md5"] = ""
            result = TencentPcManagerLogCollector(log_dir=root).collect(
                workspace,
                context,
                _window(),
            )
            sample_still_present = sample.is_file()

        self.assertEqual(result.collection_state, "collected")
        self.assertEqual(result.verdict, "unknown")
        self.assertEqual(
            result.reason,
            "sample_md5_missing_for_tav_quarantine_collection",
        )
        self.assertEqual(result.evidence_count, 0)
        self.assertTrue(sample_still_present)

    def test_quarantine_container_match_is_intercepted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, workspace = _make_dirs(Path(tmp))
            tav_cache = root / "TAVWfsDB" / "TAVCacheFullEx.db"
            tav_cache.write_bytes(b"cache")
            container = root / "Quarantine" / SAMPLE_MD5
            icon = root / "Quarantine" / f"{SAMPLE_MD5}.ico"
            container.write_bytes(b"x" * 1016)
            icon.write_bytes(b"icon")
            _set_mtime(container, "2026-05-30T00:00:06Z")
            _set_mtime(icon, "2026-05-30T00:00:06Z")
            _write_readiness_baseline(workspace, root, container_present=False)

            result = TencentPcManagerLogCollector(log_dir=root).collect(
                workspace,
                _case_context(workspace),
                _window(),
            )

        self.assertEqual(result.collection_state, "collected")
        self.assertEqual(result.verdict, "intercepted")
        self.assertTrue(result.intercepted)
        self.assertEqual(result.reason, "tav_quarantine_container_matched_case_md5")
        self.assertEqual(result.events[0].event_type, "av_quarantined")
        self.assertEqual(result.events[0].confidence, "high")
        attribution = result.events[0].evidence["attribution"]
        self.assertEqual(attribution["level"], "strong")
        self.assertIn("md5_quarantine_filename", attribution["matched_on"])
        self.assertIn("time_window", attribution["matched_on"])
        self.assertIn("size_delta", attribution["matched_on"])
        self.assertEqual(
            result.events[0].evidence["quarantine_ref"],
            f"<tav_quarantine>/{SAMPLE_MD5}",
        )

    def test_icon_sidecar_without_container_is_weak_unknown(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, workspace = _make_dirs(Path(tmp))
            (root / "TAVWfsDB" / "TAVCacheFullEx.db").write_bytes(b"cache")
            icon = root / "Quarantine" / f"{SAMPLE_MD5}.ico"
            icon.write_bytes(b"icon")
            _set_mtime(icon, "2026-05-30T00:00:06Z")
            _write_readiness_baseline(workspace, root, container_present=False)

            result = TencentPcManagerLogCollector(log_dir=root).collect(
                workspace,
                _case_context(workspace),
                _window(),
            )

        self.assertEqual(result.verdict, "unknown")
        self.assertIsNone(result.intercepted)
        self.assertEqual(
            result.events[0].event_type, "quarantine_icon_sidecar_observed"
        )
        self.assertEqual(result.events[0].confidence, "low")

    def test_pre_existing_unchanged_container_is_unattributed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, workspace = _make_dirs(Path(tmp))
            (root / "TAVWfsDB" / "TAVCacheFullEx.db").write_bytes(b"cache")
            container = root / "Quarantine" / SAMPLE_MD5
            container.write_bytes(b"x" * 1016)
            _set_mtime(container, "2026-05-30T00:00:06Z")
            _write_readiness_baseline(
                workspace,
                root,
                container_present=True,
                container_size=1016,
                container_mtime="2026-05-30T00:00:06Z",
            )

            result = TencentPcManagerLogCollector(log_dir=root).collect(
                workspace,
                _case_context(workspace),
                _window(),
            )

        self.assertEqual(result.verdict, "unknown")
        self.assertEqual(
            result.events[0].evidence["attribution"]["level"],
            "unattributed",
        )
        self.assertIn(
            "quarantine_container_pre_existing_unchanged",
            result.events[0].evidence["attribution"]["warnings"],
        )

    def test_pre_existing_modified_container_is_intercepted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, workspace = _make_dirs(Path(tmp))
            (root / "TAVWfsDB" / "TAVCacheFullEx.db").write_bytes(b"cache")
            container = root / "Quarantine" / SAMPLE_MD5
            container.write_bytes(b"x" * 1016)
            _set_mtime(container, "2026-05-30T00:00:06Z")
            _write_readiness_baseline(
                workspace,
                root,
                container_present=True,
                container_size=900,
                container_mtime="2026-05-30T00:00:00Z",
            )

            result = TencentPcManagerLogCollector(log_dir=root).collect(
                workspace,
                _case_context(workspace),
                _window(),
            )

        self.assertEqual(result.verdict, "intercepted")
        self.assertTrue(result.intercepted)
        self.assertIn(
            "baseline_changed",
            result.events[0].evidence["attribution"]["matched_on"],
        )

    def test_tav_cache_changed_without_container_is_activity_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, workspace = _make_dirs(Path(tmp))
            tav_cache = root / "TAVWfsDB" / "TAVCacheFullEx.db"
            tav_cache.write_bytes(b"cache-new")
            _set_mtime(tav_cache, "2026-05-30T00:00:06Z")
            _write_readiness_baseline(
                workspace,
                root,
                container_present=False,
                tav_cache_size=1,
                tav_cache_mtime="2026-05-30T00:00:00Z",
            )

            result = TencentPcManagerLogCollector(log_dir=root).collect(
                workspace,
                _case_context(workspace),
                _window(),
            )

        self.assertEqual(result.collection_state, "collected")
        self.assertEqual(result.verdict, "unknown")
        self.assertEqual(result.events[0].event_type, "product_log_activity_observed")
        self.assertFalse(result.events[0].evidence["case_relevant"])
        self.assertEqual(
            result.events[0].evidence["attribution"]["verdict_signal"],
            "activity_observed",
        )

    def test_near_window_mtime_records_warning_and_medium(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, workspace = _make_dirs(Path(tmp))
            (root / "TAVWfsDB" / "TAVCacheFullEx.db").write_bytes(b"cache")
            container = root / "Quarantine" / SAMPLE_MD5
            container.write_bytes(b"x" * 1016)
            _set_mtime(container, "2026-05-29T23:59:57Z")
            _write_readiness_baseline(workspace, root, container_present=False)

            result = TencentPcManagerLogCollector(log_dir=root).collect(
                workspace,
                _case_context(workspace),
                _window(),
            )

        self.assertEqual(result.verdict, "intercepted")
        self.assertEqual(result.events[0].confidence, "medium")
        self.assertIn(
            "mtime_near_case_window",
            result.events[0].evidence["attribution"]["warnings"],
        )

    def test_registry_can_create_tencent_pc_manager_collector(self) -> None:
        collector = get_product_log_collector("tencent-pc-manager")

        self.assertIsInstance(collector, TencentPcManagerLogCollector)

    def test_evidence_bundle_excludes_raw_refs_and_redacts_metadata_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, workspace = _make_dirs(Path(tmp))
            tav_cache = root / "TAVWfsDB" / "TAVCacheFullEx.db"
            tav_cache.write_bytes(b"cache")
            container = root / "Quarantine" / SAMPLE_MD5
            container.write_bytes(b"x" * 1016)
            _set_mtime(container, "2026-05-30T00:00:06Z")
            _write_readiness_baseline(workspace, root, container_present=False)
            result = TencentPcManagerLogCollector(log_dir=root).collect(
                workspace,
                _case_context(workspace),
                _window(),
            )
            _write_minimal_workspace(workspace, result)
            raw_ref_dir = workspace / "collection" / PRODUCT_ID / "raw-ref"
            raw_ref_dir.mkdir(parents=True, exist_ok=True)
            (raw_ref_dir / "quarantine_container").write_bytes(b"raw container")
            (raw_ref_dir / "icon_sidecar").write_bytes(b"raw icon")
            (raw_ref_dir / "tav_cache").write_bytes(b"raw cache")

            output = workspace / "evidence.zip"
            build_evidence_bundle(workspace, output)

            with zipfile.ZipFile(output) as bundle:
                names = set(bundle.namelist())
                observation = bundle.read(
                    "collection/tencent-pc-manager/metadata/quarantine_observation.json"
                ).decode("utf-8")
                manifest = json.loads(bundle.read("manifest.json").decode("utf-8"))

        self.assertIn(
            "collection/tencent-pc-manager/metadata/quarantine_observation.json",
            names,
        )
        self.assertIn("collector/normalized_evidence.json", names)
        self.assertNotIn(
            "collection/tencent-pc-manager/raw-ref/quarantine_container",
            names,
        )
        self.assertNotIn("collection/tencent-pc-manager/raw-ref/icon_sidecar", names)
        self.assertNotIn("collection/tencent-pc-manager/raw-ref/tav_cache", names)
        self.assertIn("<collector_path>", observation)
        self.assertNotIn(str(root), observation)
        excluded = {
            item["path"]: item["reason"] for item in manifest["excluded_path_details"]
        }
        self.assertEqual(
            excluded["collection/tencent-pc-manager/raw-ref/quarantine_container"],
            "raw_blocked",
        )
        self.assertEqual(
            excluded["collection/tencent-pc-manager/raw-ref/tav_cache"],
            "raw_binary_redaction_not_supported",
        )

    def test_collector_source_has_no_shell_or_command_runner(self) -> None:
        from cloud_av_agent_lab.guest_agent_server.collectors.tencent_pc_manager import (
            collector as collector_module,
        )

        source = inspect.getsource(collector_module)
        self.assertNotIn("subprocess", source)
        self.assertNotIn("PowerShell", source)
        self.assertNotIn("cmd.exe", source)
        self.assertNotIn("shell=True", source)
        self.assertNotIn("os.system", source)


def _make_dirs(root: Path) -> tuple[Path, Path]:
    product_root = root / "QQPCMgr"
    (product_root / "Quarantine").mkdir(parents=True)
    (product_root / "TAVWfsDB").mkdir()
    workspace = root / "case"
    workspace.mkdir()
    return product_root, workspace


def _case_context(workspace: Path) -> dict[str, object]:
    return {
        "case_id": "eicar__tencent-pc-manager",
        "sample_id": "eicar",
        "sample_sha256": SAMPLE_SHA256,
        "sample_md5": SAMPLE_MD5,
        "sample_size": 1000,
        "sample_dir": str(workspace / "sample"),
        "stored_filename": "eicar.txt",
        "original_filename": "eicar.txt",
        "case_workspace": str(workspace),
    }


def _window() -> CollectionWindow:
    return CollectionWindow(
        start_utc="2026-05-29T23:59:00Z",
        end_utc="2026-05-30T00:00:10Z",
        uploaded_at_utc="2026-05-30T00:00:00Z",
        collection_started_at_utc="2026-05-30T00:00:10Z",
        collection_finished_at_utc="2026-05-30T00:00:10Z",
    )


def _write_readiness_baseline(
    workspace: Path,
    root: Path,
    container_present: bool,
    container_size: int | None = None,
    container_mtime: str = "",
    tav_cache_size: int | None = None,
    tav_cache_mtime: str = "",
) -> None:
    workspace.mkdir(parents=True, exist_ok=True)
    payload = {
        "case_id": "eicar__tencent-pc-manager",
        "product_id": PRODUCT_ID,
        "state": "ready",
        "checks": [
            {
                "name": "tav_quarantine_baseline_recorded",
                "status": "ok",
                "data": {
                    "schema_version": "tav-quarantine-readiness-baseline.v1",
                    "product_id": PRODUCT_ID,
                    "sample_md5": SAMPLE_MD5,
                    "quarantine_dir": str(root / "Quarantine"),
                    "tav_cache_path": str(root / "TAVWfsDB" / "TAVCacheFullEx.db"),
                    "raw_artifacts_copied": False,
                    "quarantine_container": {
                        "kind": "quarantine_container_baseline",
                        "path": str(root / "Quarantine" / SAMPLE_MD5),
                        "name": SAMPLE_MD5,
                        "present": container_present,
                        "size": container_size,
                        "mtime_utc": container_mtime,
                    },
                    "icon_sidecar": {},
                    "tav_cache": {
                        "kind": "tav_cache_baseline",
                        "path": str(root / "TAVWfsDB" / "TAVCacheFullEx.db"),
                        "name": "TAVCacheFullEx.db",
                        "present": True,
                        "size": tav_cache_size,
                        "mtime_utc": tav_cache_mtime,
                    },
                },
            }
        ],
    }
    (workspace / "case_security_product_readiness.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _write_minimal_workspace(workspace: Path, result: object) -> None:
    _write_json(
        workspace / "case.json",
        {
            "case": {"id": "eicar__tencent-pc-manager"},
            "sample": {"id": "eicar", "md5": SAMPLE_MD5, "sha256": SAMPLE_SHA256},
            "product": {"id": PRODUCT_ID},
        },
    )
    _write_json(
        workspace / "case_state.json",
        {
            "case_id": "eicar__tencent-pc-manager",
            "sample_id": "eicar",
            "product_id": PRODUCT_ID,
            "sample": {"md5": SAMPLE_MD5, "sha256": SAMPLE_SHA256, "size": 1000},
        },
    )
    _write_json(
        workspace / "sample" / "sample.json",
        {
            "case_id": "eicar__tencent-pc-manager",
            "sample_id": "eicar",
            "md5": SAMPLE_MD5,
            "sha256": SAMPLE_SHA256,
            "size": 1000,
        },
    )
    _write_json(
        workspace / "case_report.json",
        {
            "case_id": "eicar__tencent-pc-manager",
            "sample_id": "eicar",
            "product_id": PRODUCT_ID,
        },
    )
    _write_json(
        workspace / "case_summary.json",
        {
            "case_id": "eicar__tencent-pc-manager",
            "sample_id": "eicar",
            "product_id": PRODUCT_ID,
        },
    )
    _write_json(
        workspace / "case_collection.json",
        {
            "case_id": "eicar__tencent-pc-manager",
            "sample_id": "eicar",
            **result.to_dict(),
        },
    )
    (workspace / "events.jsonl").write_text(
        json.dumps({"event_type": "case_prepared"}) + "\n",
        encoding="utf-8",
    )


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _set_mtime(path: Path, value: str) -> None:
    timestamp = datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    os.utime(path, (timestamp, timestamp))
