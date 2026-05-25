from __future__ import annotations

import inspect
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cloud_av_agent_lab.guest_agent_server.collectors.qihoo_360 import (
    Qihoo360SQLiteError,
    SUMMARY_DATABASE_NAME,
    UNION_METADATA_NAME,
)
from cloud_av_agent_lab.guest_agent_server.security_product_readiness import (
    SecurityProductReadinessContext,
    run_security_product_readiness_probe,
)
from cloud_av_agent_lab.guest_agent_server.security_product_readiness import (
    registry as readiness_registry,
)
from cloud_av_agent_lab.guest_agent_server.security_product_readiness.qihoo_360 import (
    Qihoo360SecurityProductReadinessProbe,
)


class Qihoo360ReadinessTests(unittest.TestCase):
    def test_non_windows_returns_unsupported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = _check(Path(tmp), platform_name="Linux")

        self.assertEqual(result.state, "unsupported")
        self.assertEqual(result.scope, "log_observability")
        self.assertEqual(result.protection_state, "unknown")
        self.assertIn("non_windows_platform", result.reason_codes)

    def test_missing_summary_dat_returns_not_ready(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp) / "360Quarant"
            log_dir.mkdir()

            result = _check(log_dir)

        self.assertEqual(result.state, "not_ready")
        self.assertIn("summary_dat_not_found", result.reason_codes)
        self.assertIn("Summary.dat", " ".join(result.errors))

    def test_readable_summary_with_schema_returns_ready(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp) / "360Quarant"
            log_dir.mkdir()
            _write_summary_db(log_dir / SUMMARY_DATABASE_NAME, fq_rows=((1, b""),))
            (log_dir / UNION_METADATA_NAME).write_bytes(b"union-placeholder")

            result = _check(log_dir)
            payload = result.to_dict()
            schema_check = _check_payload(
                payload,
                "qihoo360_summary_dat_schema_verified",
            )

        self.assertEqual(result.state, "ready")
        self.assertEqual(result.confidence, "medium")
        self.assertEqual(schema_check["data"]["fq_record_count"], 1)
        self.assertNotIn("summary_records_empty", result.warnings)
        self.assertNotIn("union_metadata_missing", result.warnings)

    def test_empty_fq_returns_ready_with_summary_records_empty_warning(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp) / "360Quarant"
            log_dir.mkdir()
            _write_summary_db(log_dir / SUMMARY_DATABASE_NAME, fq_rows=())
            (log_dir / UNION_METADATA_NAME).write_bytes(b"union-placeholder")

            result = _check(log_dir)

        self.assertEqual(result.state, "ready")
        self.assertIn("summary_records_empty", result.warnings)
        self.assertIn("summary_records_empty", result.reason_codes)

    def test_missing_union_metadata_returns_ready_with_warning(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp) / "360Quarant"
            log_dir.mkdir()
            _write_summary_db(log_dir / SUMMARY_DATABASE_NAME, fq_rows=((1, b""),))

            result = _check(log_dir)

        self.assertEqual(result.state, "ready")
        self.assertIn("union_metadata_missing", result.warnings)
        self.assertIn("union_metadata_missing", result.reason_codes)

    def test_invalid_sqlite_header_returns_unknown(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp) / "360Quarant"
            log_dir.mkdir()
            (log_dir / SUMMARY_DATABASE_NAME).write_bytes(b"not sqlite")

            result = _check(log_dir)

        self.assertEqual(result.state, "unknown")
        self.assertIn("summary_dat_query_failed", result.reason_codes)

    def test_schema_exception_returns_unknown(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp) / "360Quarant"
            log_dir.mkdir()
            _write_missing_schema_db(log_dir / SUMMARY_DATABASE_NAME)

            result = _check(log_dir)

        self.assertEqual(result.state, "unknown")
        self.assertIn("summary_dat_query_failed", result.reason_codes)

    def test_query_exception_returns_unknown(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp) / "360Quarant"
            log_dir.mkdir()
            _write_summary_db(log_dir / SUMMARY_DATABASE_NAME, fq_rows=((1, b""),))

            with patch(
                "cloud_av_agent_lab.guest_agent_server."
                "security_product_readiness.qihoo_360."
                "read_qihoo360_summary_database",
                side_effect=Qihoo360SQLiteError("query failed"),
            ):
                result = _check(log_dir)

        self.assertEqual(result.state, "unknown")
        self.assertIn("summary_dat_query_failed", result.reason_codes)
        self.assertIn("query failed", " ".join(result.errors))

    def test_registry_can_call_qihoo_360_probe(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp) / "360Quarant"
            log_dir.mkdir()
            _write_summary_db(log_dir / SUMMARY_DATABASE_NAME, fq_rows=((1, b""),))

            result = run_security_product_readiness_probe(
                SecurityProductReadinessContext(
                    product_id="qihoo-360",
                    workspace=Path(tmp) / "case",
                    log_dir=log_dir,
                ),
                "qihoo-360",
            )

        self.assertEqual(result.product_id, "qihoo-360")
        self.assertEqual(result.state, "ready")
        self.assertIn(
            "qihoo-360",
            readiness_registry.supported_security_product_readiness_probes(),
        )

    def test_readiness_does_not_create_raw_artifact(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            log_dir = root / "360Quarant"
            workspace = root / "case"
            log_dir.mkdir()
            workspace.mkdir()
            _write_summary_db(log_dir / SUMMARY_DATABASE_NAME, fq_rows=((1, b""),))

            result = Qihoo360SecurityProductReadinessProbe(
                platform_provider=lambda: "Windows"
            ).check(
                SecurityProductReadinessContext(
                    product_id="qihoo-360",
                    workspace=workspace,
                    log_dir=log_dir,
                )
            )

        self.assertEqual(result.state, "ready")
        self.assertFalse((workspace / "security-product-readiness").exists())

    def test_probe_source_has_no_shell_or_command_runner(self) -> None:
        from cloud_av_agent_lab.guest_agent_server.security_product_readiness import (
            qihoo_360 as readiness_module,
        )

        source = inspect.getsource(readiness_module)
        self.assertNotIn("subprocess", source)
        self.assertNotIn("PowerShell", source)
        self.assertNotIn("cmd.exe", source)
        self.assertNotIn("shell=True", source)
        self.assertNotIn("os.system", source)


def _check(log_dir: Path, platform_name: str = "Windows"):
    return Qihoo360SecurityProductReadinessProbe(
        platform_provider=lambda: platform_name,
    ).check(
        SecurityProductReadinessContext(
            product_id="qihoo-360",
            workspace=log_dir.parent / "case",
            log_dir=log_dir,
        )
    )


def _check_payload(payload: dict[str, object], name: str) -> dict[str, object]:
    checks = payload["checks"]
    if not isinstance(checks, list):
        raise AssertionError("checks payload must be a list")
    for item in checks:
        if isinstance(item, dict) and item.get("name") == name:
            return item
    raise AssertionError(f"check not found: {name}")


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


def _write_missing_schema_db(path: Path) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.execute('CREATE TABLE "FI" ("ID" INTEGER)')
        connection.commit()
    finally:
        connection.close()
