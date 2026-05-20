from __future__ import annotations

import json
import inspect
import sys
import tempfile
import zipfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cloud_av_agent_lab.evaluation import evaluate_case
from cloud_av_agent_lab.evidence import exporter
from cloud_av_agent_lab.evidence import build_evidence_bundle


class EvaluationTests(TestCase):
    def test_collection_evidence_yields_detected_or_blocked(self) -> None:
        summary = evaluate_case(
            case_report=_case_report(upload_state="stable", stable=True),
            case_collection={
                "product_id": "huorong",
                "collection_state": "collected",
                "verdict": "intercepted",
                "intercepted": True,
                "evidence_count": 1,
            },
        )

        self.assertEqual(summary.verdict, "detected_or_blocked")
        self.assertEqual(summary.confidence, "high")

    def test_removed_after_save_without_collection_is_inconclusive(self) -> None:
        summary = evaluate_case(
            case_report=_case_report(
                upload_state="removed_after_save",
                removed_after_save=True,
            ),
            case_collection={"collection_state": "not_collected"},
        )

        self.assertEqual(summary.verdict, "inconclusive")
        self.assertEqual(summary.confidence, "low")
        self.assertIn("collection_state=not_collected", summary.blocking_conditions)

    def test_removed_after_save_with_collection_success_is_suspicious(self) -> None:
        summary = evaluate_case(
            case_report=_case_report(
                upload_state="removed_after_save",
                removed_after_save=True,
            ),
            case_collection={
                "collection_state": "collected",
                "verdict": "not_intercepted",
                "intercepted": False,
                "evidence_count": 0,
            },
        )

        self.assertEqual(summary.verdict, "suspiciously_removed")
        self.assertEqual(summary.confidence, "medium")

    def test_stable_without_evidence_and_observed_execution_is_no_detection(
        self,
    ) -> None:
        report = _case_report(upload_state="stable", stable=True)
        report["execution"] = {
            "state": "exited_cleanly",
            "exit_code": 0,
            "started_at_utc": "2026-05-16T00:00:05Z",
        }

        summary = evaluate_case(
            case_report=report,
            case_collection={
                "collection_state": "collected",
                "verdict": "not_intercepted",
                "intercepted": False,
                "evidence_count": 0,
                "window": {
                    "execution_started_at_utc": "2026-05-16T00:00:05Z",
                    "end_utc": "2026-05-16T00:01:00Z",
                },
            },
        )

        self.assertEqual(summary.verdict, "no_detection_observed")

    def test_collection_failed_never_yields_no_detection(self) -> None:
        report = _case_report(upload_state="stable", stable=True)
        report["execution"] = {
            "state": "exited_cleanly",
            "exit_code": 0,
            "started_at_utc": "2026-05-16T00:00:05Z",
        }

        summary = evaluate_case(
            case_report=report,
            case_collection={
                "collection_state": "failed",
                "verdict": "unknown",
                "evidence_count": 0,
                "errors": ["collector failed"],
                "window": {
                    "execution_started_at_utc": "2026-05-16T00:00:05Z",
                    "end_utc": "2026-05-16T00:01:00Z",
                },
            },
        )

        self.assertEqual(summary.verdict, "inconclusive")
        self.assertIn("collection_state=failed", summary.blocking_conditions)

    def test_collection_partial_never_yields_no_detection(self) -> None:
        report = _case_report(upload_state="stable", stable=True)
        report["execution"] = {
            "state": "exited_cleanly",
            "exit_code": 0,
            "started_at_utc": "2026-05-16T00:00:05Z",
        }

        summary = evaluate_case(
            case_report=report,
            case_collection={
                "collection_state": "partial",
                "verdict": "not_intercepted",
                "evidence_count": 0,
                "errors": ["copied WAL failed"],
                "window": {
                    "execution_started_at_utc": "2026-05-16T00:00:05Z",
                    "end_utc": "2026-05-16T00:01:00Z",
                },
            },
        )

        self.assertEqual(summary.verdict, "inconclusive")
        self.assertIn("collection_partial_or_errors", summary.blocking_conditions)

    def test_stable_without_execution_is_not_overstated(self) -> None:
        summary = evaluate_case(
            case_report=_case_report(upload_state="stable", stable=True),
            case_collection={"collection_state": "not_collected", "evidence_count": 0},
        )

        self.assertEqual(summary.verdict, "execution_not_observed")

    def test_process_disappearance_alone_is_not_detection(self) -> None:
        report = _case_report(upload_state="uploaded")
        report["execution"] = {"state": "terminated_or_disappeared"}

        summary = evaluate_case(
            case_report=report,
            case_collection={"collection_state": "collected", "evidence_count": 0},
        )

        self.assertEqual(summary.verdict, "inconclusive")
        self.assertIn("process disappearance", " ".join(summary.reasons))

    def test_case_summary_schema_contains_expected_sections(self) -> None:
        summary = evaluate_case(
            case_report=_case_report(upload_state="stable", stable=True),
            case_collection={"collection_state": "not_collected"},
        ).to_dict()

        for key in (
            "case_id",
            "sample_id",
            "vm_id",
            "product_id",
            "verdict",
            "confidence",
            "summary",
            "reasons",
            "delivery",
            "execution",
            "collection",
            "decision_inputs",
            "blocking_conditions",
            "nonfatal_failures",
            "timeline",
            "generated_at_utc",
        ):
            self.assertIn(key, summary)

    def test_timeline_keeps_only_key_state_changes(self) -> None:
        summary = evaluate_case(
            case_report=_case_report(upload_state="stable", stable=True),
            case_collection={
                "collection_state": "collected",
                "timeline": [
                    _timeline_event("sample_post_upload_check", "status query"),
                    _timeline_event(
                        "sample_stable_after_upload",
                        "stable 1",
                        {"upload_state": "stable"},
                    ),
                    _timeline_event(
                        "sample_stable_after_upload",
                        "stable 2",
                        {"upload_state": "stable"},
                    ),
                    _timeline_event(
                        "execution_observed",
                        "running 1",
                        {"execution_state": "running", "children_count": 0},
                    ),
                    _timeline_event(
                        "execution_observed",
                        "running 2",
                        {"execution_state": "running", "children_count": 0},
                    ),
                    _timeline_event(
                        "execution_observed",
                        "exited",
                        {
                            "execution_state": "exited_cleanly",
                            "children_count": 0,
                            "exit_code": 0,
                        },
                    ),
                    _timeline_event(
                        "av_quarantined",
                        "product evidence",
                        source="product_log",
                    ),
                ],
            },
        ).to_dict()

        event_types = [event["event_type"] for event in summary["timeline"]]
        messages = [event["message"] for event in summary["timeline"]]
        self.assertNotIn("sample_post_upload_check", event_types)
        self.assertEqual(event_types.count("sample_stable_after_upload"), 1)
        self.assertEqual(event_types.count("execution_observed"), 2)
        self.assertNotIn("stable 2", messages)
        self.assertNotIn("running 2", messages)


class EvidenceExportTests(TestCase):
    def test_evidence_bundle_contains_metadata_and_excludes_sample(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            (workspace / "sample").mkdir()
            (workspace / "sample" / "sample.bin").write_bytes(b"sample-content")
            (workspace / "sample" / "sample.json").write_text(
                json.dumps({"sample_id": "case-001", "sha256": "0" * 64}),
                encoding="utf-8",
            )
            (workspace / "collection" / "huorong").mkdir(parents=True)
            (workspace / "collection" / "huorong" / "log.db").write_bytes(
                b"raw huorong db copy"
            )
            (workspace / "worker-state").mkdir()
            (workspace / "worker-state" / "worker_execution_state.json").write_text(
                json.dumps({"state": "exited_cleanly"}),
                encoding="utf-8",
            )
            (workspace / ".env").write_text("TOKEN=secret", encoding="utf-8")
            (workspace / "evidence").mkdir()
            (workspace / "evidence" / "old.zip").write_bytes(b"old")
            (workspace / "case.json").write_text(
                json.dumps({"case": {"id": "case-001__huorong"}}),
                encoding="utf-8",
            )
            (workspace / "case_state.json").write_text(
                json.dumps({"case_id": "case-001__huorong"}),
                encoding="utf-8",
            )
            (workspace / "case_report.json").write_text(
                json.dumps(_case_report(product_id="huorong")),
                encoding="utf-8",
            )
            (workspace / "case_collection.json").write_text(
                json.dumps(
                    {
                        "case_id": "case-001__huorong",
                        "sample_id": "case-001",
                        "product_id": "huorong",
                        "evidence_count": 1,
                        "events": [{"event_type": "av_quarantined"}],
                    }
                ),
                encoding="utf-8",
            )
            (workspace / "case_summary.json").write_text(
                json.dumps({"case_id": "case-001__huorong", "product_id": "huorong"}),
                encoding="utf-8",
            )
            (workspace / "events.jsonl").write_text(
                json.dumps({"event_type": "case_prepared"}) + "\n",
                encoding="utf-8",
            )

            output = workspace / "evidence.zip"
            result = build_evidence_bundle(workspace, output)

            self.assertTrue(output.is_file())
            self.assertGreater(result["size"], 0)
            with zipfile.ZipFile(output) as bundle:
                names = set(bundle.namelist())
                self.assertIn("manifest.json", names)
                self.assertIn("case.json", names)
                self.assertIn("case_state.json", names)
                self.assertIn("case_report.json", names)
                self.assertIn("case_collection.json", names)
                self.assertIn("case_summary.json", names)
                self.assertIn("events.jsonl", names)
                self.assertIn("sample/sample.json", names)
                self.assertIn("worker-state/worker_execution_state.json", names)
                self.assertIn("collector/normalized_evidence.json", names)
                self.assertNotIn("collection/huorong/log.db", names)
                self.assertNotIn("sample/sample.bin", names)
                self.assertNotIn(".env", names)
                self.assertNotIn("evidence/old.zip", names)
                manifest = json.loads(bundle.read("manifest.json").decode("utf-8"))
                self.assertEqual(manifest["schema_version"], "evidence-bundle.v2")
                self.assertEqual(manifest["trust_model"], "dirty_instance_untrusted")
                self.assertFalse(manifest["forensic_grade"])
                self.assertFalse(manifest["raw_binary_included"])
                self.assertEqual(
                    manifest["redaction_policy"]["schema_version"],
                    "evidence-redaction.v1",
                )
                self.assertTrue(manifest["redaction_policy"]["enabled"])
                self.assertTrue(manifest["redaction_policy"]["text_files_redacted"])
                self.assertFalse(manifest["redaction_policy"]["binary_files_redacted"])
                self.assertTrue(manifest["redaction_policy"]["preserve_hashes"])
                self.assertEqual(manifest["redaction_policy"]["max_bundle_files"], 200)
                self.assertEqual(
                    manifest["redaction_policy"]["max_entry_bytes"],
                    10 * 1024 * 1024,
                )
                self.assertEqual(
                    manifest["redaction_policy"]["max_bundle_uncompressed_bytes"],
                    50 * 1024 * 1024,
                )
                self.assertEqual(
                    manifest["redaction_policy"]["max_text_redaction_bytes"],
                    5 * 1024 * 1024,
                )
                self.assertEqual(
                    manifest["redaction_policy"]["product_semantic_redaction_owner"],
                    "collector",
                )
                self.assertEqual(
                    manifest["redaction_policy"]["global_redaction_owner"],
                    "evidence_exporter",
                )
                self.assertIn("redacted_files", manifest)
                self.assertIn("sample/", manifest["excluded_paths"])
                self.assertIn("configs/real.toml", manifest["excluded_paths"])
                self.assertTrue(
                    any(
                        item["path"] == "sample/sample.bin"
                        and item["reason"] == "uploaded_sample_bytes"
                        for item in manifest["excluded_path_details"]
                    )
                )
                self.assertTrue(
                    any(
                        item["path"] == "collection/huorong/log.db"
                        and item["reason"] == "raw_binary_redaction_not_supported"
                        for item in manifest["excluded_path_details"]
                    )
                )
                for item in manifest["files"]:
                    content = bundle.read(item["path"])
                    self.assertEqual(len(content), item["size"])

    def test_manifest_does_not_include_legacy_collector_absolute_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            _write_minimal_evidence_workspace(workspace)
            raw_db = workspace / "collection" / "huorong" / "log.db"
            raw_db.parent.mkdir(parents=True, exist_ok=True)
            raw_db.write_bytes(b"raw huorong db copy")
            source_dir = r"C:\ProgramData\Huorong\sysdiag"
            source_path = source_dir + r"\log.db"
            artifact_path = str(raw_db)
            _write_json(
                workspace / "case_collection.json",
                {
                    "case_id": "case-001__huorong",
                    "sample_id": "case-001",
                    "product_id": "huorong",
                    "evidence_count": 0,
                    "events": [],
                    "artifacts": {
                        "schema_version": "collector-artifacts.v1",
                        "items": [
                            {
                                "path": "collection/huorong/log.db",
                                "category": "raw_product_log",
                                "include_in_evidence": False,
                                "redaction_state": "raw_blocked",
                            }
                        ],
                        "legacy": {
                            "source_log_dir": source_dir,
                            "artifact_dir": str(raw_db.parent),
                            "copied_files": [
                                {
                                    "source": source_path,
                                    "artifact": artifact_path,
                                }
                            ],
                        },
                    },
                },
            )

            output = workspace / "evidence.zip"
            build_evidence_bundle(workspace, output)

            with zipfile.ZipFile(output) as bundle:
                manifest_text = bundle.read("manifest.json").decode("utf-8")
                collection_text = bundle.read("case_collection.json").decode("utf-8")
            self.assertNotIn(source_dir, manifest_text)
            self.assertNotIn(source_path, manifest_text)
            self.assertNotIn(artifact_path, manifest_text)
            self.assertNotIn(source_dir, collection_text)
            self.assertNotIn(source_path, collection_text)
            self.assertNotIn(artifact_path, collection_text)

    def test_unsafe_collector_artifact_path_is_ignored_with_warning(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            _write_minimal_evidence_workspace(workspace)
            _write_json(
                workspace / "case_collection.json",
                {
                    "case_id": "case-001__huorong",
                    "sample_id": "case-001",
                    "product_id": "huorong",
                    "evidence_count": 0,
                    "events": [],
                    "artifacts": {
                        "schema_version": "collector-artifacts.v1",
                        "items": [
                            {
                                "path": "../escape/log.db",
                                "category": "raw_product_log",
                                "include_in_evidence": False,
                                "redaction_state": "raw_blocked",
                            }
                        ],
                    },
                },
            )

            output = workspace / "evidence.zip"
            build_evidence_bundle(workspace, output)

            with zipfile.ZipFile(output) as bundle:
                manifest = json.loads(bundle.read("manifest.json").decode("utf-8"))
            self.assertIn(
                "collector_artifact_unsafe_path_ignored",
                manifest["redaction_warnings"],
            )
            self.assertNotIn("../escape/log.db", json.dumps(manifest))

    def test_archive_path_conflict_is_excluded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            _write_minimal_evidence_workspace(workspace)
            one = workspace / "one.json"
            two = workspace / "two.json"
            one.write_text(json.dumps({"name": "one"}), encoding="utf-8")
            two.write_text(json.dumps({"name": "two"}), encoding="utf-8")

            output = workspace / "evidence.zip"
            with (
                patch.object(
                    exporter, "_iter_workspace_files", return_value=[one, two]
                ),
                patch.object(
                    exporter,
                    "_relative_name",
                    side_effect=[
                        "worker-state/status.json",
                        "worker-state/STATUS.json",
                    ],
                ),
            ):
                build_evidence_bundle(workspace, output)

            with zipfile.ZipFile(output) as bundle:
                names = set(bundle.namelist())
                manifest = json.loads(bundle.read("manifest.json").decode("utf-8"))
            self.assertIn("worker-state/status.json", names)
            self.assertNotIn("worker-state/STATUS.json", names)
            self.assertTrue(
                any(
                    item["path"] == "worker-state/STATUS.json"
                    and item["reason"] == "zip_entry_conflict"
                    for item in manifest["excluded_path_details"]
                )
            )

    def test_evidence_exporter_uses_python_zipfile_not_external_packers(self) -> None:
        source = inspect.getsource(exporter)

        self.assertIn("zipfile.ZipFile", source)
        self.assertNotIn("subprocess", source)
        self.assertNotIn("7z.exe", source)
        self.assertNotIn("zip.exe", source)


def _case_report(
    upload_state: str = "stable",
    stable: bool = False,
    removed_after_save: bool = False,
    product_id: str = "huorong",
) -> dict[str, object]:
    return {
        "case_id": "case-001__huorong",
        "sample_id": "case-001",
        "vm_id": "win10-huorong",
        "product_id": product_id,
        "upload_state": upload_state,
        "saved_once": True,
        "post_write_exists": stable,
        "removed_after_save": removed_after_save,
        "locked_or_busy": False,
        "stable": stable,
        "original_filename": "eicar.txt",
        "sha256": "0" * 64,
        "size": 68,
        "execution": {"state": "not_started"},
        "collection": {"state": "not_collected", "evidence_count": 0},
    }


def _write_minimal_evidence_workspace(workspace: Path) -> None:
    (workspace / "sample").mkdir()
    _write_json(workspace / "sample" / "sample.json", {"sample_id": "case-001"})
    _write_json(workspace / "case.json", {"case": {"id": "case-001__huorong"}})
    _write_json(workspace / "case_state.json", {"case_id": "case-001__huorong"})
    _write_json(workspace / "case_report.json", _case_report(product_id="huorong"))
    _write_json(
        workspace / "case_collection.json",
        {
            "case_id": "case-001__huorong",
            "sample_id": "case-001",
            "product_id": "huorong",
            "evidence_count": 0,
            "events": [],
        },
    )
    _write_json(
        workspace / "case_summary.json",
        {"case_id": "case-001__huorong", "product_id": "huorong"},
    )
    (workspace / "events.jsonl").write_text(
        json.dumps({"event_type": "case_prepared"}) + "\n",
        encoding="utf-8",
    )


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _timeline_event(
    event_type: str,
    message: str,
    evidence: dict[str, object] | None = None,
    source: str = "guest_agent",
) -> dict[str, object]:
    return {
        "timestamp_utc": "2026-05-16T00:00:00Z",
        "source": source,
        "event_type": event_type,
        "message": message,
        "evidence": evidence or {},
    }
