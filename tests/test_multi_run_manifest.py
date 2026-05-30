from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from cloud_av_agent_lab.orchestration.multi_run import (
    SAMPLE_MANIFEST_ENTRY_SCHEMA_VERSION,
    MultiRunManifestError,
    compute_manifest_sha256,
    load_sample_manifest,
)


class MultiRunManifestLoaderTests(unittest.TestCase):
    def test_valid_manifest_loads_entries_and_digest(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest_path = Path(tmpdir) / "sample_manifest.jsonl"
            entries = [_manifest_entry(1), _manifest_entry(3, sha_char="b")]
            _write_manifest(manifest_path, entries)

            manifest = load_sample_manifest(manifest_path)

            self.assertEqual(manifest.indexes, (1, 3))
            self.assertEqual(manifest.by_index()[1].sample_id, "a" * 64)
            self.assertEqual(manifest.by_index()[3].sha256, "b" * 64)
            self.assertEqual(manifest.sha256, compute_manifest_sha256(manifest_path))

    def test_duplicate_sample_index_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest_path = Path(tmpdir) / "sample_manifest.jsonl"
            _write_manifest(
                manifest_path,
                [_manifest_entry(1), _manifest_entry(1, sha_char="b")],
            )

            with self.assertRaisesRegex(
                MultiRunManifestError,
                "duplicate sample_index 1",
            ):
                load_sample_manifest(manifest_path)

    def test_missing_sha256_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest_path = Path(tmpdir) / "sample_manifest.jsonl"
            entry = _manifest_entry(1)
            del entry["sha256"]
            _write_manifest(manifest_path, [entry])

            with self.assertRaisesRegex(
                MultiRunManifestError,
                r"sample_manifest\.jsonl: line 1: .*sha256",
            ):
                load_sample_manifest(manifest_path)

    def test_sample_ref_is_not_checked_for_file_existence(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest_path = Path(tmpdir) / "sample_manifest.jsonl"
            entry = _manifest_entry(1)
            entry["sample_ref"] = r"Z:\does\not\exist\sample.exe"
            _write_manifest(manifest_path, [entry])

            manifest = load_sample_manifest(manifest_path)

            self.assertEqual(manifest.entries[0].sample_ref, entry["sample_ref"])

    def test_invalid_sample_source_kind_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest_path = Path(tmpdir) / "sample_manifest.jsonl"
            entry = _manifest_entry(1)
            entry["sample_source_kind"] = "remote_shell_path"
            _write_manifest(manifest_path, [entry])

            with self.assertRaisesRegex(MultiRunManifestError, "sample_source_kind"):
                load_sample_manifest(manifest_path)

    def test_manifest_digest_is_stable_for_same_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest_path = Path(tmpdir) / "sample_manifest.jsonl"
            _write_manifest(
                manifest_path,
                [_manifest_entry(1), _manifest_entry(2, sha_char="b")],
            )

            self.assertEqual(
                compute_manifest_sha256(manifest_path),
                compute_manifest_sha256(manifest_path),
            )

    def test_manifest_line_order_changes_digest(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            first_path = Path(tmpdir) / "first.jsonl"
            second_path = Path(tmpdir) / "second.jsonl"
            entries = [_manifest_entry(1), _manifest_entry(2, sha_char="b")]
            _write_manifest(first_path, entries)
            _write_manifest(second_path, list(reversed(entries)))

            self.assertNotEqual(
                compute_manifest_sha256(first_path),
                compute_manifest_sha256(second_path),
            )


def _manifest_entry(index: int, *, sha_char: str = "a") -> dict[str, object]:
    sha256 = sha_char * 64
    md5 = sha_char * 32
    return {
        "schema_version": SAMPLE_MANIFEST_ENTRY_SCHEMA_VERSION,
        "manifest_id": "manifest-001",
        "manifest_created_at_utc": "2026-05-30T10:00:00Z",
        "manifest_tool_version": "0.1.0",
        "sample_index": index,
        "sample_id": sha256,
        "case_name": sha256[:16],
        "sha256": sha256,
        "md5": md5,
        "size": 68 + index,
        "original_filename": f"sample-{index}.bat",
        "original_suffix": ".bat",
        "normalized_suffix": ".bat",
        "renamed_filename": f"{index:04d}_{sha256[:16]}.bat",
        "sample_source_kind": "local_platform_path",
        "sample_ref": f"C:\\CloudAvSamples\\indexed\\{index:04d}_{sha256[:16]}.bat",
        "duplicate_group_id": f"sha256:{sha256}",
        "duplicate_of_sample_index": None,
        "aliases": [f"sample-{index}.bat"],
        "entry_status": "ready",
        "skip_reason": None,
        "created_at_utc": "2026-05-30T10:01:00Z",
    }


def _write_manifest(path: Path, entries: list[dict[str, object]]) -> None:
    path.write_text(
        "".join(json.dumps(entry, sort_keys=True) + "\n" for entry in entries),
        encoding="utf-8",
    )


if __name__ == "__main__":
    unittest.main()
