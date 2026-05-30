# Raw Sample Drop-In Directory

This directory is a placeholder for the cloud platform host only.

`multi-run --sample-dir runs\raw_sample --platform-sample-dir ...` may scan this
directory on the isolated platform host and create a separate indexed mirror
inside the selected batch directory. The original files in `raw_sample` are not
modified by the indexer.

Safety notes:

- Do not place real malware in this repository checkout on a development host.
- Do not commit any file placed in this directory.
- Directory contents are ignored by Git; only this README and `.gitkeep` are
  tracked.
- Development hosts should consume a prebuilt `sample_manifest.jsonl` with
  `--manifest` instead of scanning a local sample directory.
