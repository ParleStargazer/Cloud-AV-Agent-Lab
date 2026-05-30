from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from cloud_av_agent_lab.orchestration.multi_run import (
    MultiRunManifestError,
    build_sample_manifest_from_directory,
    load_sample_manifest,
    unique_sha_prefixes,
)


class MultiRunSampleDirectoryIndexerTests(unittest.TestCase):
    def test_indexes_regular_files_and_preserves_raw_samples(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            raw_dir = tmp_path / "raw_sample"
            raw_dir.mkdir()
            first = raw_dir / "first.bin"
            duplicate_dir = raw_dir / "nested"
            duplicate_dir.mkdir()
            duplicate = duplicate_dir / "first-copy.bin"
            second = raw_dir / "second.exe"
            first.write_bytes(b"alpha")
            duplicate.write_bytes(b"alpha")
            second.write_bytes(b"bravo")

            artifacts = build_sample_manifest_from_directory(
                raw_dir,
                tmp_path / "batch" / "sample_index",
                created_at_utc="2026-05-30T00:00:00Z",
            )
            manifest = load_sample_manifest(artifacts.manifest_path)

            self.assertEqual(len(manifest.entries), 2)
            self.assertEqual(
                [entry.sha256 for entry in manifest.entries],
                sorted(
                    [
                        hashlib.sha256(b"alpha").hexdigest(),
                        hashlib.sha256(b"bravo").hexdigest(),
                    ]
                ),
            )
            alpha_entry = next(
                entry
                for entry in manifest.entries
                if entry.sha256 == hashlib.sha256(b"alpha").hexdigest()
            )
            self.assertEqual(
                alpha_entry.duplicate_group_id, f"sha256:{alpha_entry.sha256}"
            )
            self.assertEqual(
                sorted(alpha_entry.aliases), ["first.bin", "nested/first-copy.bin"]
            )
            self.assertIn(alpha_entry.original_filename, alpha_entry.aliases)
            self.assertIn("/indexed/", alpha_entry.sample_ref)
            self.assertNotIn("/raw_sample/", alpha_entry.sample_ref)
            self.assertTrue(
                (artifacts.indexed_dir / alpha_entry.renamed_filename).is_file()
            )
            self.assertEqual(first.read_bytes(), b"alpha")
            self.assertEqual(duplicate.read_bytes(), b"alpha")
            self.assertEqual(second.read_bytes(), b"bravo")
            self.assertIn(
                "nested/first-copy.bin",
                artifacts.sample_name_map_path.read_text(encoding="utf-8"),
            )

    def test_sha16_collision_expands_prefix(self) -> None:
        prefixes = unique_sha_prefixes(
            [
                "a" * 16 + "0" + "b" * 47,
                "a" * 16 + "1" + "b" * 47,
            ]
        )

        self.assertEqual(set(len(prefix) for prefix in prefixes.values()), {17})

    def test_indexer_rejects_existing_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            raw_dir = tmp_path / "raw_sample"
            output_dir = tmp_path / "sample_index"
            raw_dir.mkdir()
            output_dir.mkdir()
            (raw_dir / "sample.txt").write_text("hello", encoding="utf-8")
            (output_dir / "sample_manifest.jsonl").write_text("", encoding="utf-8")

            with self.assertRaisesRegex(MultiRunManifestError, "output already exists"):
                build_sample_manifest_from_directory(raw_dir, output_dir)

    def test_indexer_rejects_existing_indexed_mirror_target(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            raw_dir = tmp_path / "raw_sample"
            output_dir = tmp_path / "sample_index"
            indexed_dir = output_dir / "indexed"
            raw_dir.mkdir()
            indexed_dir.mkdir(parents=True)
            (raw_dir / "sample.txt").write_text("hello", encoding="utf-8")
            (indexed_dir / "0001_existing.txt").write_text("old", encoding="utf-8")

            with self.assertRaisesRegex(MultiRunManifestError, "output already exists"):
                build_sample_manifest_from_directory(raw_dir, output_dir)

    def test_manifest_jsonl_loads_after_indexing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            raw_dir = tmp_path / "raw_sample"
            raw_dir.mkdir()
            (raw_dir / "sample.txt").write_text("hello", encoding="utf-8")

            artifacts = build_sample_manifest_from_directory(
                raw_dir, tmp_path / "index"
            )
            payloads = [
                json.loads(line)
                for line in artifacts.manifest_path.read_text(
                    encoding="utf-8"
                ).splitlines()
                if line
            ]

            self.assertEqual(payloads[0]["sample_index"], 1)
            self.assertEqual(payloads[0]["entry_status"], "ready")
            self.assertNotIn("sample_bytes", payloads[0])


if __name__ == "__main__":
    unittest.main()
