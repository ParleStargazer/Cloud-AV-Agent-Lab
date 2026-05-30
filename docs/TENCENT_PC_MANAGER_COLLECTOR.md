# Tencent PC Manager Collector

This document describes the Tencent PC Manager MVP integration for Cloud AV
Agent Lab. The stable product id is:

```text
tencent-pc-manager
```

## Safety Boundary

The collector and readiness probe are metadata-only:

- no real malware handling in local control code;
- no local sample execution;
- no shell, cmd, PowerShell, `wevtutil`, subprocess, or run-command path;
- no arbitrary guest path supplied by the client;
- no product configuration changes;
- no decryption or parsing of Tencent TAV quarantine container bytes;
- no raw TAV artifact or uploaded sample bytes in the default redacted evidence
  bundle.

## Readiness

Tencent PC Manager readiness uses:

```text
scope = "quarantine_metadata_observability"
protection_state = "unknown"
```

The probe runs after `prepare-case` and before upload. It reads the current
case sample MD5 from case metadata, then checks the QQPCMgr/TAV observation
paths:

```text
C:\ProgramData\Tencent\QQPCMgr\Quarantine
C:\ProgramData\Tencent\QQPCMgr\TAVWfsDB\TAVCacheFullEx.db
```

It records product-presence signals such as whether the QQPCMgr root,
quarantine directory, and TAV cache file are visible. Process and service
signals are placeholders in this MVP and are not used as blocking gates.

Readiness does not prove that real-time protection is enabled. It only records
whether the quarantine metadata observation path appears usable.

## Collector Model

The collector observes metadata for the current case sample MD5:

- `Quarantine\<md5>`: encrypted quarantine container. Its presence is the main
  TAV interception signal.
- `Quarantine\<md5>.ico`: icon sidecar. This is auxiliary only and is never a
  strong signal without the container.
- `TAVWfsDB\TAVCacheFullEx.db`: product activity metadata. The collector
  compares readiness baseline metadata with collection-time metadata.

The collector writes text metadata under:

```text
collection/tencent-pc-manager/metadata/quarantine_observation.json
collection/tencent-pc-manager/metadata/normalized_events.jsonl
```

Raw artifact references are declared for auditability but are excluded from the
default evidence bundle:

```text
collection/tencent-pc-manager/raw-ref/quarantine_container
collection/tencent-pc-manager/raw-ref/icon_sidecar
collection/tencent-pc-manager/raw-ref/tav_cache
```

## Attribution

The collector uses conservative attribution:

- a current-case MD5 quarantine container created or modified in the case time
  window can produce `intercepted`;
- if the container already existed in readiness baseline and is unchanged, it
  remains unattributed;
- if the container existed before but size or mtime changed near the case
  window, attribution may be medium rather than discarded;
- `.ico` sidecar alone is weak/unknown;
- `TAVCacheFullEx.db` activity without a matching container is reported as
  `product_log_activity_observed` with collector verdict `unknown`.

A small time tolerance is used around the case window to absorb VM clock drift
and asynchronous product writes.

## CLI

Prepare, readiness, and collection use the same explicit product id:

```powershell
python -m cloud_av_agent_lab guest-prepare-case `
  --config configs/lab.local.toml `
  --vm-id win10-tencent-manager `
  --sample-id case-001 `
  --product tencent-pc-manager

python -m cloud_av_agent_lab guest-check-security-product-readiness `
  --config configs/lab.local.toml `
  --vm-id win10-tencent-manager `
  --case-id case-001__tencent-pc-manager `
  --product tencent-pc-manager

python -m cloud_av_agent_lab guest-collect-logs `
  --config configs/lab.local.toml `
  --vm-id win10-tencent-manager `
  --case-id case-001__tencent-pc-manager `
  --product tencent-pc-manager
```

`single-run --product tencent-pc-manager` generates a non-sensitive product
profile and threads the same product id through prepare, readiness, upload,
execution decision, collection, summary, and evidence export.

## Evidence Bundle

The redacted evidence bundle may include:

- `case_security_product_readiness.json`;
- `case_collection.json`;
- normalized Tencent PC Manager metadata JSON/JSONL;
- summary/report/state files.

It must not include:

- uploaded sample bytes;
- `Quarantine\<md5>` container bytes;
- `.ico` sidecar bytes;
- `TAVCacheFullEx.db` bytes;
- tokens, cloud secrets, environment variables, or real cloud config.

