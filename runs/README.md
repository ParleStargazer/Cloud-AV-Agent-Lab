# Runtime Workspace

This directory is the default local workspace for `single-run` and `multi-run`
runtime artifacts.

Tracked files in this directory are only safe placeholders and operator notes.
Generated batch directories, lock files, run logs, summaries, evidence bundles,
sample indexes, and indexed mirrors are intentionally ignored by Git.

Suggested layout:

```text
runs/
  plans/        # optional non-sensitive batch plan drafts
  raw_sample/   # cloud platform sample drop-in directory, contents ignored
  .locks/       # runtime instance locks, contents ignored
```

Safety notes:

- Do not commit sample files.
- Do not commit generated batch output.
- Do not commit tokens, cloud credentials, or `configs/real.toml`.
- Development hosts should normally use `--manifest`; `--sample-dir` is for the
  cloud platform host and requires `--platform-sample-dir`.
