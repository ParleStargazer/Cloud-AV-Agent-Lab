from __future__ import annotations

import inspect
import json
import shutil
import sqlite3
import tempfile
import zipfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from cloud_av_agent_lab.evidence import build_evidence_bundle
from cloud_av_agent_lab.guest_agent_server.collectors import (
    CollectionWindow,
    get_product_log_collector,
)
from cloud_av_agent_lab.guest_agent_server.collectors.qihoo_360 import (
    EICAR_SHA256,
    SUMMARY_DATABASE_NAME,
    UNION_METADATA_NAME,
    Qihoo360LogCollector,
)


class Qihoo360CollectorTests(TestCase):
    def test_missing_summary_returns_not_collected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            log_dir = root / "360Quarant"
            workspace = root / "case"
            log_dir.mkdir()
            workspace.mkdir()

            result = Qihoo360LogCollector(log_dir=log_dir).collect(
                workspace,
                _case_context(workspace),
                _window(),
            )

        self.assertEqual(result.collection_state, "not_collected")
        self.assertEqual(result.verdict, "unknown")
        self.assertEqual(result.reason, "product_log_not_found")
        self.assertEqual(result.evidence_count, 0)

    def test_bad_summary_returns_failed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            log_dir = root / "360Quarant"
            workspace = root / "case"
            log_dir.mkdir()
            workspace.mkdir()
            (log_dir / SUMMARY_DATABASE_NAME).write_bytes(b"not sqlite")

            result = Qihoo360LogCollector(log_dir=log_dir).collect(
                workspace,
                _case_context(workspace),
                _window(),
            )

        self.assertEqual(result.collection_state, "failed")
        self.assertEqual(result.verdict, "failed")
        self.assertEqual(result.reason, "summary_dat_parse_failed")
        self.assertTrue(result.errors)

    def test_readable_summary_without_current_case_evidence_is_unknown(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            log_dir = root / "360Quarant"
            workspace = root / "case"
            log_dir.mkdir()
            workspace.mkdir()
            _write_summary_db(
                log_dir / SUMMARY_DATABASE_NAME,
                fq_rows=((1, _event_blob(threat_name="Generic/Trojan")),),
            )

            result = Qihoo360LogCollector(log_dir=log_dir).collect(
                workspace,
                _case_context(workspace),
                _window(),
            )

        self.assertEqual(result.collection_state, "collected")
        self.assertEqual(result.verdict, "unknown")
        self.assertIsNone(result.intercepted)
        self.assertEqual(result.reason, "no_relevant_qihoo360_events_for_case")
        self.assertEqual(result.events[0].evidence["attribution"], "unattributed")

    def test_case_sample_path_match_is_strong(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            log_dir, workspace = _make_dirs(root)
            context = _case_context(workspace)
            _write_summary_db(
                log_dir / SUMMARY_DATABASE_NAME,
                fq_rows=(
                    (
                        1,
                        _event_blob(
                            threat_name="Generic/Trojan",
                            file_path=str(workspace / "sample" / "eicar.txt"),
                            sha256=EICAR_SHA256,
                        ),
                    ),
                ),
            )

            result = Qihoo360LogCollector(log_dir=log_dir).collect(
                workspace,
                context,
                _window(),
            )

        self.assertEqual(result.events[0].evidence["attribution"], "strong")
        self.assertIn("case_sample_path", result.events[0].evidence["matched_on"])
        self.assertEqual(result.events[0].confidence, "high")

    def test_eicar_hash_only_is_medium_with_warning(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            log_dir, workspace = _make_dirs(root)
            _write_summary_db(
                log_dir / SUMMARY_DATABASE_NAME,
                fq_rows=(
                    (
                        1,
                        _event_blob(
                            threat_name="EICAR-Test-File",
                            file_path=r"C:\Users\Other\Desktop\eicar.txt",
                            sha256=EICAR_SHA256,
                        ),
                    ),
                ),
            )

            result = Qihoo360LogCollector(log_dir=log_dir).collect(
                workspace,
                _case_context(workspace),
                _window(),
            )

        event = result.events[0]
        self.assertEqual(event.evidence["attribution"], "medium")
        self.assertEqual(event.confidence, "medium")
        self.assertIn("sha256", event.evidence["matched_on"])
        self.assertIn(
            "eicar_hash_is_reused_across_cases",
            event.evidence["attribution_warnings"],
        )

    def test_threat_only_is_weak_and_not_confident_verdict(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            log_dir, workspace = _make_dirs(root)
            _write_summary_db(
                log_dir / SUMMARY_DATABASE_NAME,
                fq_rows=(
                    (
                        1,
                        _event_blob(
                            threat_name="EICAR-Test-File",
                            file_path="",
                            sha256="",
                            observed_at="1779667203",
                        ),
                    ),
                ),
            )

            result = Qihoo360LogCollector(log_dir=log_dir).collect(
                workspace,
                _case_context(workspace),
                _window(),
            )

        self.assertEqual(result.verdict, "unknown")
        self.assertEqual(result.events[0].evidence["attribution"], "weak")

    def test_quarantine_path_with_strong_attribution_is_intercepted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            log_dir, workspace = _make_dirs(root)
            _write_summary_db(
                log_dir / SUMMARY_DATABASE_NAME,
                fq_rows=(
                    (
                        1,
                        _event_blob(
                            threat_name="Generic/Trojan",
                            file_path=str(workspace / "sample" / "eicar.txt"),
                            quarantine_path=r"C:\$360Section\360.example.q3q",
                            sha256=EICAR_SHA256,
                        ),
                    ),
                ),
            )

            result = Qihoo360LogCollector(log_dir=log_dir).collect(
                workspace,
                _case_context(workspace),
                _window(),
            )

        self.assertEqual(result.verdict, "intercepted")
        self.assertTrue(result.intercepted)
        self.assertEqual(result.events[0].event_type, "av_quarantined")

    def test_detection_only_with_confident_attribution_is_detected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            log_dir, workspace = _make_dirs(root)
            _write_summary_db(
                log_dir / SUMMARY_DATABASE_NAME,
                fq_rows=(
                    (
                        1,
                        _event_blob(
                            threat_name="Generic/Trojan",
                            file_path=str(workspace / "sample" / "eicar.txt"),
                            quarantine_path="",
                            sha256=EICAR_SHA256,
                            raw_action_text="恢复文件",
                        ),
                    ),
                ),
            )

            result = Qihoo360LogCollector(log_dir=log_dir).collect(
                workspace,
                _case_context(workspace),
                _window(),
            )

        self.assertEqual(result.verdict, "detected")
        self.assertFalse(result.intercepted)
        event = result.events[0]
        self.assertEqual(event.event_type, "av_detected")
        self.assertEqual(event.evidence["raw_action_text"], "恢复文件")
        self.assertEqual(
            event.evidence["action_semantics"],
            "detected_without_confirmed_action",
        )

    def test_snapshot_may_be_changing_is_warning_not_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            log_dir, workspace = _make_dirs(root)
            source = log_dir / SUMMARY_DATABASE_NAME
            _write_summary_db(
                source,
                fq_rows=(
                    (
                        1,
                        _event_blob(
                            threat_name="Generic/Trojan",
                            file_path=str(workspace / "sample" / "eicar.txt"),
                        ),
                    ),
                ),
            )
            original_copy2 = shutil.copy2

            def copy_and_mutate(source_path: Path, destination: Path) -> Path:
                result = original_copy2(source_path, destination)
                Path(source_path).write_bytes(Path(source_path).read_bytes() + b"\0")
                return result

            with patch(
                "cloud_av_agent_lab.guest_agent_server.collectors."
                "qihoo_360.collector.shutil.copy2",
                side_effect=copy_and_mutate,
            ):
                result = Qihoo360LogCollector(log_dir=log_dir).collect(
                    workspace,
                    _case_context(workspace),
                    _window(),
                )

        self.assertEqual(result.collection_state, "collected")
        self.assertIn("snapshot_may_be_changing", result.artifacts["warnings"])
        self.assertIn(
            "snapshot_may_be_changing",
            result.artifacts["summary_snapshot"]["warnings"],
        )

    def test_registry_can_create_qihoo_360_collector(self) -> None:
        collector = get_product_log_collector("qihoo-360")

        self.assertIsInstance(collector, Qihoo360LogCollector)

    def test_artifacts_mark_raw_sqlite_and_union_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            log_dir, workspace = _make_dirs(root)
            _write_summary_db(log_dir / SUMMARY_DATABASE_NAME, fq_rows=((1, b""),))
            (log_dir / UNION_METADATA_NAME).write_bytes(b"union metadata")

            result = Qihoo360LogCollector(log_dir=log_dir).collect(
                workspace,
                _case_context(workspace),
                _window(),
            )

        artifacts = result.to_dict()["artifacts"]
        by_path = {item["path"]: item for item in artifacts["items"]}
        self.assertFalse(
            by_path[f"collection/qihoo-360/raw/{SUMMARY_DATABASE_NAME}"][
                "include_in_evidence"
            ]
        )
        self.assertEqual(
            by_path[f"collection/qihoo-360/raw/{SUMMARY_DATABASE_NAME}"][
                "redaction_state"
            ],
            "raw_blocked",
        )
        self.assertFalse(
            by_path[f"collection/qihoo-360/raw/{UNION_METADATA_NAME}"][
                "include_in_evidence"
            ]
        )

    def test_evidence_bundle_excludes_raw_artifacts_and_redacts_normalized_paths(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            log_dir, workspace = _make_dirs(root)
            sample_dir = workspace / "sample"
            sample_dir.mkdir()
            _write_json(sample_dir / "sample.json", {"sample_id": "eicar"})
            _write_summary_db(
                log_dir / SUMMARY_DATABASE_NAME,
                fq_rows=(
                    (
                        1,
                        _event_blob(
                            threat_name="Generic/Trojan",
                            file_path=str(sample_dir / "eicar.txt"),
                            quarantine_path=r"C:\$360Section\360.example.q3q",
                            sha256=EICAR_SHA256,
                        ),
                    ),
                ),
            )
            (log_dir / UNION_METADATA_NAME).write_bytes(b"union metadata")
            result = Qihoo360LogCollector(log_dir=log_dir).collect(
                workspace,
                _case_context(workspace),
                _window(),
            )
            _write_minimal_workspace(workspace, result)
            (workspace / "collection" / "qihoo-360" / "raw" / "sample.q3q").write_bytes(
                b"quarantine placeholder"
            )

            output = workspace / "evidence.zip"
            build_evidence_bundle(workspace, output)

            with zipfile.ZipFile(output) as bundle:
                names = set(bundle.namelist())
                normalized = bundle.read("collector/normalized_evidence.json").decode(
                    "utf-8"
                )
                manifest = json.loads(bundle.read("manifest.json").decode("utf-8"))

        self.assertNotIn(f"collection/qihoo-360/raw/{SUMMARY_DATABASE_NAME}", names)
        self.assertNotIn(f"collection/qihoo-360/raw/{UNION_METADATA_NAME}", names)
        self.assertNotIn("collection/qihoo-360/raw/sample.q3q", names)
        self.assertIn("<case_workspace>", normalized)
        self.assertNotIn(str(workspace), normalized)
        excluded = {
            item["path"]: item["reason"] for item in manifest["excluded_path_details"]
        }
        self.assertEqual(
            excluded[f"collection/qihoo-360/raw/{SUMMARY_DATABASE_NAME}"],
            "raw_binary_redaction_not_supported",
        )
        self.assertEqual(
            excluded[f"collection/qihoo-360/raw/{UNION_METADATA_NAME}"],
            "raw_binary_redaction_not_supported",
        )

    def test_collector_source_has_no_shell_or_command_runner(self) -> None:
        from cloud_av_agent_lab.guest_agent_server.collectors.qihoo_360 import (
            collector as collector_module,
        )

        source = inspect.getsource(collector_module)
        self.assertNotIn("subprocess", source)
        self.assertNotIn("PowerShell", source)
        self.assertNotIn("cmd.exe", source)
        self.assertNotIn("shell=True", source)
        self.assertNotIn("os.system", source)


def _make_dirs(root: Path) -> tuple[Path, Path]:
    log_dir = root / "360Quarant"
    workspace = root / "case"
    log_dir.mkdir()
    workspace.mkdir()
    return log_dir, workspace


def _case_context(workspace: Path) -> dict[str, object]:
    return {
        "case_id": "eicar__qihoo-360",
        "sample_id": "eicar",
        "sample_sha256": EICAR_SHA256,
        "sample_dir": str(workspace / "sample"),
        "stored_filename": "eicar.txt",
        "original_filename": "eicar.txt",
        "case_workspace": str(workspace),
    }


def _window() -> CollectionWindow:
    return CollectionWindow(
        start_utc="2026-05-25T00:00:00Z",
        end_utc="2026-05-25T00:00:10Z",
        uploaded_at_utc="2026-05-25T00:00:01Z",
        collection_started_at_utc="2026-05-25T00:00:10Z",
        collection_finished_at_utc="2026-05-25T00:00:10Z",
    )


def _write_minimal_workspace(workspace: Path, result: object) -> None:
    _write_json(workspace / "case.json", {"case": {"id": "eicar__qihoo-360"}})
    _write_json(
        workspace / "case_state.json",
        {"case_id": "eicar__qihoo-360", "product_id": "qihoo-360"},
    )
    _write_json(
        workspace / "case_report.json",
        {
            "case_id": "eicar__qihoo-360",
            "sample_id": "eicar",
            "product_id": "qihoo-360",
        },
    )
    _write_json(
        workspace / "case_summary.json",
        {"case_id": "eicar__qihoo-360", "product_id": "qihoo-360"},
    )
    _write_json(
        workspace / "case_collection.json",
        {
            "case_id": "eicar__qihoo-360",
            "sample_id": "eicar",
            **result.to_dict(),
        },
    )
    (workspace / "events.jsonl").write_text(
        json.dumps({"event_type": "case_prepared"}) + "\n",
        encoding="utf-8",
    )


def _write_summary_db(path: Path, fq_rows: tuple[tuple[int, bytes], ...]) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.execute('CREATE TABLE "CF" ("K" BLOB, "V" BLOB, "VR" INTEGER)')
        connection.execute(
            'CREATE TABLE "FI" ('
            '"ID" INTEGER, "FO" BLOB, "FQ" BLOB, "M5" BLOB, "S1" BLOB, '
            '"S6" BLOB, "SZ" INTEGER, "VR" INTEGER)'
        )
        connection.execute(
            'CREATE TABLE "FQ" ('
            '"ID" INTEGER, "FC" BLOB, "VE" INTEGER, "CS" INTEGER, "VR" INTEGER)'
        )
        connection.execute('INSERT INTO "CF" VALUES (?, ?, ?)', (b"V", b"0", 102))
        for record_id, blob in fq_rows:
            connection.execute(
                'INSERT INTO "FQ" VALUES (?, ?, ?, ?, ?)',
                (record_id, sqlite3.Binary(blob), 1, 1, 1),
            )
        connection.commit()
    finally:
        connection.close()


def _event_blob(
    threat_name: str = "",
    file_path: str = r"C:\Users\Other\Desktop\eicar.txt",
    quarantine_path: str = "",
    sha256: str = "",
    observed_at: str = "",
    raw_action_text: str = "",
) -> bytes:
    children = []
    if threat_name:
        children.append(_text_field("@203", threat_name))
    if raw_action_text:
        children.append(_text_field("@205", raw_action_text))
    if observed_at:
        children.append(_text_field("@206", observed_at))
    if file_path:
        children.append(_text_field("@500", file_path))
    if quarantine_path:
        children.append(_text_field("@502", quarantine_path))
    if sha256:
        children.append(_text_field("@513", sha256))
    return _container("@100", *children)


def _container(code: str, *children: bytes) -> bytes:
    body = b"".join(children)
    return code.encode("ascii") + b"\x01" + len(body).to_bytes(4, "little") + body


def _text_field(code: str, value: str) -> bytes:
    raw_value = value.encode("utf-16le") + b"\x00\x00"
    body = (
        (0x08).to_bytes(2, "little") + len(raw_value).to_bytes(4, "little") + raw_value
    )
    return code.encode("ascii") + b"\x00" + len(body).to_bytes(4, "little") + body


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
