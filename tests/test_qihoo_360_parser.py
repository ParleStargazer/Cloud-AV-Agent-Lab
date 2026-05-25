from __future__ import annotations

import inspect
import sqlite3
import tempfile
import unittest
from pathlib import Path

from cloud_av_agent_lab.guest_agent_server.collectors.qihoo_360 import (
    Qihoo360SQLiteError,
    parse_qihoo360_fc_blob,
    read_qihoo360_summary_database,
    validate_qihoo360_summary_header,
)
from cloud_av_agent_lab.guest_agent_server.collectors.qihoo_360 import (
    parser as qihoo_parser_module,
)
from cloud_av_agent_lab.guest_agent_server.collectors.qihoo_360 import (
    sqlite_reader as qihoo_sqlite_reader_module,
)


class Qihoo360ParserTests(unittest.TestCase):
    def test_fc_blob_parser_extracts_known_fields(self) -> None:
        blob = _container(
            "@100",
            _text_field("@200", "newspy_killer"),
            _text_field("@201", "360safe"),
            _text_field("@203", "木马:Generic/Trojan.Generic.HoAASOQA"),
            _text_field("@204", "木马"),
            _text_field("@205", "恢复文件"),
            _text_field("@206", "134241831817950000"),
            _text_field("@208", r"C:\Users\AvTester\Desktop\eicar.com"),
            _text_field("@209", r"C:\Users\AvTester\Desktop\eicar.com"),
            _container(
                "@101",
                _text_field("@500", r"C:\Users\AvTester\Desktop\eicar.com"),
                _text_field("@501", "68"),
                _text_field("@502", r"C:\$360Section\360.example.q3q"),
                _text_field("@510", "44D88612FEA8A8F36DE82E1278ABB02F"),
                _text_field("@512", "3395856CE81F2B7382DEE72602F798B642F14140"),
                _text_field(
                    "@513",
                    "275A021BBFB6489E54D471899F7DB9D1663FC695EC2FE2A2C4538AABF651FD0F",
                ),
                _text_field("@514", "other"),
                _text_field("@999", "unknown text"),
            ),
        )

        parsed = parse_qihoo360_fc_blob(blob, source_row_id=7)

        self.assertEqual(parsed.source_row_id, 7)
        self.assertEqual(parsed.event_source, "newspy_killer")
        self.assertEqual(parsed.raw_product, "360safe")
        self.assertEqual(parsed.threat_name, "木马:Generic/Trojan.Generic.HoAASOQA")
        self.assertEqual(parsed.threat_category, "木马")
        self.assertEqual(parsed.raw_action_text, "恢复文件")
        self.assertEqual(parsed.event_time_raw, "134241831817950000")
        self.assertTrue(parsed.observed_at_utc.endswith("Z"))
        self.assertEqual(parsed.time_confidence, "low")
        self.assertEqual(parsed.file_path, r"C:\Users\AvTester\Desktop\eicar.com")
        self.assertEqual(parsed.file_size, 68)
        self.assertEqual(parsed.quarantine_path, r"C:\$360Section\360.example.q3q")
        self.assertEqual(parsed.md5, "44d88612fea8a8f36de82e1278abb02f")
        self.assertEqual(
            parsed.sha1,
            "3395856ce81f2b7382dee72602f798b642f14140",
        )
        self.assertEqual(
            parsed.sha256,
            "275a021bbfb6489e54d471899f7db9d1663fc695ec2fe2a2c4538aabf651fd0f",
        )
        self.assertIn("@999", parsed.unknown_fields)
        self.assertIn("unknown text", parsed.unknown_fields["@999"]["decoded_preview"])
        self.assertNotIn("raw_blob", parsed.to_dict())

    def test_duplicate_fields_are_preserved_as_lists(self) -> None:
        blob = _container(
            "@100",
            _text_field("@208", r"C:\one.txt"),
            _text_field("@208", r"C:\two.txt"),
        )

        parsed = parse_qihoo360_fc_blob(blob)

        self.assertEqual(parsed.related_paths, (r"C:\one.txt", r"C:\two.txt"))
        self.assertEqual(parsed.raw_fields["@208"], [r"C:\one.txt", r"C:\two.txt"])

    def test_malformed_tlv_does_not_crash(self) -> None:
        parsed = parse_qihoo360_fc_blob(b"prefix@203\x00\xff\xff\xff\x7fshort")

        self.assertEqual(parsed.threat_name, "")
        self.assertIn("field_203_length_exceeds_blob", parsed.parse_warnings)


class Qihoo360SQLiteReaderTests(unittest.TestCase):
    def test_sqlite_reader_validates_schema_reads_fi_and_fq(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "360safe.Summary.dat"
            _write_summary_db(
                db_path,
                fc_blob=_container(
                    "@100",
                    _text_field("@203", "木马:Generic/Trojan.Generic.HoAASOQA"),
                    _text_field("@204", "木马"),
                    _container(
                        "@101",
                        _text_field("@501", "68"),
                        _text_field("@502", r"C:\$360Section\360.example.q3q"),
                    ),
                ),
            )

            validate_qihoo360_summary_header(db_path)
            summary = read_qihoo360_summary_database(db_path)

        self.assertEqual(set(summary.table_names), {"CF", "FI", "FQ"})
        self.assertEqual(len(summary.file_index), 1)
        self.assertEqual(len(summary.events), 1)
        event = summary.events[0]
        self.assertEqual(event.source_row_id, 1)
        self.assertEqual(event.file_path, r"C:\Users\AvTester\Desktop\eicar.com")
        self.assertEqual(event.file_size, 68)
        self.assertEqual(event.quarantine_path, r"C:\$360Section\360.example.q3q")
        self.assertEqual(
            event.sha256,
            "275a021bbfb6489e54d471899f7db9d1663fc695ec2fe2a2c4538aabf651fd0f",
        )

    def test_invalid_header_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "360safe.Summary.dat"
            db_path.write_bytes(b"not sqlite")

            with self.assertRaisesRegex(Qihoo360SQLiteError, "not a SQLite"):
                validate_qihoo360_summary_header(db_path)

    def test_missing_required_tables_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "360safe.Summary.dat"
            connection = sqlite3.connect(db_path)
            try:
                connection.execute('CREATE TABLE "FI" ("ID" INTEGER)')
                connection.commit()
            finally:
                connection.close()

            with self.assertRaisesRegex(Qihoo360SQLiteError, "missing required tables"):
                read_qihoo360_summary_database(db_path)

    def test_parser_adds_no_shell_paths(self) -> None:
        source = inspect.getsource(qihoo_parser_module) + inspect.getsource(
            qihoo_sqlite_reader_module
        )
        self.assertNotIn("powershell", source.casefold())
        self.assertNotIn("wevtutil", source.casefold())
        self.assertNotIn("shell=True", source)
        self.assertNotIn("subprocess", source)


def _write_summary_db(path: Path, fc_blob: bytes) -> None:
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
        connection.execute(
            'INSERT INTO "FI" VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
            (
                1,
                r"C:\Users\AvTester\Desktop\eicar.com".encode("utf-8"),
                r"C:\$360Section\360.example.q3q".encode("utf-8"),
                b"44d88612fea8a8f36de82e1278abb02f",
                b"3395856ce81f2b7382dee72602f798b642f14140",
                b"275a021bbfb6489e54d471899f7db9d1663fc695ec2fe2a2c4538aabf651fd0f",
                68,
                1,
            ),
        )
        connection.execute(
            'INSERT INTO "FQ" VALUES (?, ?, ?, ?, ?)',
            (1, sqlite3.Binary(fc_blob), 1, 1, 1),
        )
        connection.commit()
    finally:
        connection.close()


def _container(code: str, *children: bytes) -> bytes:
    body = b"".join(children)
    return code.encode("ascii") + b"\x01" + len(body).to_bytes(4, "little") + body


def _text_field(code: str, value: str) -> bytes:
    raw_value = value.encode("utf-16le") + b"\x00\x00"
    body = (
        (0x08).to_bytes(2, "little") + len(raw_value).to_bytes(4, "little") + raw_value
    )
    return code.encode("ascii") + b"\x00" + len(body).to_bytes(4, "little") + body
