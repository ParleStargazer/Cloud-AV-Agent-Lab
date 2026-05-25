from __future__ import annotations

import inspect
import sqlite3
import tempfile
import unittest
from pathlib import Path

from cloud_av_agent_lab.guest_agent_server.collectors.base import CollectionWindow
from cloud_av_agent_lab.guest_agent_server.collectors import registry
from cloud_av_agent_lab.guest_agent_server.collectors.qihoo_360 import (
    attribution as qihoo_attribution_module,
)
from cloud_av_agent_lab.guest_agent_server.collectors.qihoo_360 import (
    EICAR_SHA256,
    Qihoo360ParsedEvent,
    Qihoo360SummaryBaseline,
    attribute_qihoo360_event,
    filter_qihoo360_delta_events,
    read_qihoo360_summary_baseline,
    read_qihoo360_summary_database,
)
from cloud_av_agent_lab.guest_agent_server.collectors.qihoo_360 import (
    baseline as qihoo_baseline_module,
)


class Qihoo360BaselineTests(unittest.TestCase):
    def test_baseline_reads_empty_fq(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "360safe.Summary.dat"
            _write_summary_db(db_path, fq_rows=())

            baseline = read_qihoo360_summary_baseline(db_path)

        self.assertEqual(baseline.product_id, "qihoo-360")
        self.assertGreater(baseline.summary_dat_size, 0)
        self.assertTrue(baseline.summary_dat_mtime_utc.endswith("Z"))
        self.assertIsNone(baseline.max_fq_id)
        self.assertEqual(baseline.known_fq_ids, ())

    def test_baseline_reads_max_fq_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "360safe.Summary.dat"
            _write_summary_db(
                db_path, fq_rows=((2, _event_blob("old")), (5, _event_blob("new")))
            )

            baseline = read_qihoo360_summary_baseline(db_path)

        self.assertEqual(baseline.max_fq_id, 5)
        self.assertEqual(baseline.known_fq_ids, (2, 5))

    def test_delta_filter_selects_only_new_fq_ids(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "360safe.Summary.dat"
            _write_summary_db(
                db_path,
                fq_rows=(
                    (1, _event_blob("old")),
                    (2, _event_blob("new-a")),
                    (3, _event_blob("new-b")),
                ),
            )
            summary = read_qihoo360_summary_database(db_path)
        baseline = Qihoo360SummaryBaseline(
            source_path="baseline",
            summary_dat_size=10,
            summary_dat_mtime_utc="2026-05-25T00:00:00Z",
            max_fq_id=1,
            known_fq_ids=(1,),
        )

        selected, delta = filter_qihoo360_delta_events(summary, baseline)

        self.assertEqual([event.source_row_id for event in selected], [2, 3])
        self.assertTrue(delta.baseline_delta_usable)
        self.assertEqual(delta.candidate_fq_ids, (2, 3))
        self.assertEqual(delta.warnings, ())

    def test_delta_filter_warns_when_summary_db_reset_or_rotated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "360safe.Summary.dat"
            _write_summary_db(db_path, fq_rows=((1, _event_blob("new-base")),))
            summary = read_qihoo360_summary_database(db_path)
        baseline = Qihoo360SummaryBaseline(
            source_path="baseline",
            summary_dat_size=10,
            summary_dat_mtime_utc="2026-05-25T00:00:00Z",
            max_fq_id=5,
            known_fq_ids=(1, 2, 3, 4, 5),
        )

        selected, delta = filter_qihoo360_delta_events(summary, baseline)

        self.assertEqual(selected, ())
        self.assertFalse(delta.baseline_delta_usable)
        self.assertEqual(delta.candidate_fq_ids, ())
        self.assertIn("summary_db_reset_or_rotated", delta.warnings)

    def test_phase_two_does_not_register_collector_or_add_shell_paths(self) -> None:
        self.assertIsNone(registry.get_product_log_collector("qihoo-360"))
        source = inspect.getsource(qihoo_baseline_module) + inspect.getsource(
            qihoo_attribution_module
        )
        self.assertNotIn("powershell", source.casefold())
        self.assertNotIn("wevtutil", source.casefold())
        self.assertNotIn("shell=True", source)
        self.assertNotIn("subprocess", source)


class Qihoo360AttributionTests(unittest.TestCase):
    def test_eicar_hash_only_attribution_is_not_above_medium(self) -> None:
        event = Qihoo360ParsedEvent(
            source_row_id=11,
            threat_name="EICAR-Test-File",
            sha256=EICAR_SHA256,
        )

        attribution = attribute_qihoo360_event(
            event,
            {
                "case_id": "eicar__qihoo-360",
                "sample_sha256": EICAR_SHA256,
                "sample_dir": r"C:\CloudAvAgentLab\cases\eicar__qihoo-360\sample",
                "stored_filename": "eicar.txt",
                "original_filename": "eicar.txt",
            },
        )

        self.assertEqual(attribution.level, "medium")
        self.assertIn("sha256", attribution.matched_on)
        self.assertFalse(attribution.path_matched)
        self.assertIn("eicar_hash_is_reused_across_cases", attribution.warnings)
        self.assertIn("case_path_not_matched", attribution.warnings)

    def test_case_sample_path_match_can_be_strong(self) -> None:
        event = Qihoo360ParsedEvent(
            source_row_id=12,
            threat_name="木马:Generic/Trojan.Generic.HoAASOQA",
            file_path=r"C:\CloudAvAgentLab\cases\eicar__qihoo-360\sample\eicar.txt",
            sha256=EICAR_SHA256,
            observed_at_utc="2026-05-25T00:00:03Z",
        )

        attribution = attribute_qihoo360_event(
            event,
            {
                "case_id": "eicar__qihoo-360",
                "sample_sha256": EICAR_SHA256,
                "sample_dir": r"C:\CloudAvAgentLab\cases\eicar__qihoo-360\sample",
                "stored_filename": "eicar.txt",
                "original_filename": "eicar.txt",
            },
            window=CollectionWindow(
                start_utc="2026-05-25T00:00:00Z",
                end_utc="2026-05-25T00:00:10Z",
            ),
            baseline_delta_ids=(12,),
        )

        self.assertEqual(attribution.level, "strong")
        self.assertTrue(attribution.path_matched)
        self.assertTrue(attribution.sha256_matched)
        self.assertTrue(attribution.time_window_matched)
        self.assertTrue(attribution.baseline_delta_matched)
        self.assertIn("case_sample_path", attribution.matched_on)
        self.assertIn("baseline_delta", attribution.matched_on)


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


def _event_blob(threat_name: str) -> bytes:
    return _container(
        "@100",
        _text_field("@203", threat_name),
        _text_field("@500", r"C:\Users\AvTester\Desktop\eicar.txt"),
        _text_field("@513", EICAR_SHA256),
    )


def _container(code: str, *children: bytes) -> bytes:
    body = b"".join(children)
    return code.encode("ascii") + b"\x01" + len(body).to_bytes(4, "little") + body


def _text_field(code: str, value: str) -> bytes:
    raw_value = value.encode("utf-16le") + b"\x00\x00"
    body = (
        (0x08).to_bytes(2, "little") + len(raw_value).to_bytes(4, "little") + raw_value
    )
    return code.encode("ascii") + b"\x00" + len(body).to_bytes(4, "little") + body
