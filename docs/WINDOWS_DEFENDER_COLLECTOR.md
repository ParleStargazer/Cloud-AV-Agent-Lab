# Windows Defender Collector Model

This document tracks the Windows Defender / Microsoft Defender Antivirus
onboarding work. The current implementation includes XML parser support,
readiness scaffolding with injected fake-reader tests, and a stage 3 collector
registered as `product_id = "windows-defender"`.

The collector uses a Windows Event Log reader abstraction. The default real
reader is optional pywin32-based code loaded only when collection runs on a
Windows Guest Agent with the `guest-agent` extra installed. Tests use fake
readers and fixture XML; they do not read the live Windows Event Log.

## Product ID

Use the explicit product id:

```text
windows-defender
```

This avoids ambiguity with Defender Firewall, Defender for Endpoint,
SmartScreen, or the Microsoft Defender app.

## Event Source

The target product log source is the Defender Operational channel:

```text
Applications and Services Logs
  Microsoft
    Windows
      Windows Defender
        Operational
```

The canonical channel string used by the parser schema is:

```text
Microsoft-Windows-Windows Defender/Operational
```

The parser supports exported event XML fixtures and XML returned by the Windows
Event Log reader.

## Readiness

The readiness probe defines the Windows Event Log reader protocol and can be
constructed with an injected reader and platform provider. Current tests use
fake readers only. Readiness remains a log observability check; it is not proof
that Defender real-time protection is currently enabled.

```python
class WindowsEventLogReader(Protocol):
    def query(
        self,
        channel: str,
        event_ids: Sequence[int],
        start_time_utc: datetime | None = None,
        end_time_utc: datetime | None = None,
        limit: int = 50,
    ) -> Sequence[WindowsEventRecord]:
        ...
```

The collector's default real reader uses pywin32 when available. There is still
no PowerShell, `wevtutil`, `cmd`, shell runner, or ctypes reader in this stage.

Readiness state semantics:

- `ready`: Windows platform, Defender Operational channel is queryable, and the
  reader returns normally. An empty result still means the channel is
  observable; it is reported as `ready` with
  `reason_codes=["no_recent_activity"]`, not as an exception or `not_ready`.
- `partial`: the channel is queryable, but the returned records do not include
  core Defender AV event IDs.
- `not_ready`: Windows platform, but the Defender Operational channel is
  explicitly missing.
- `unknown`: access denied, query failed, reader failure, unexpected exception,
  or invalid reader result.
- `unsupported`: non-Windows platform.

Readiness remains scoped to `log_observability`, and `protection_state` remains
`unknown`. This signal must not be treated as proof that Defender real-time
protection is enabled.

## Collector Stage 3

The `windows-defender` collector is registered in the product log collector
registry, and the readiness probe is registered in the security product
readiness registry. CLI flows can select it explicitly with
`--product windows-defender`; `guest-prepare-case` stores that product in
`case_state.json`, and subsequent readiness/collection calls must match the
prepared case product. The collector queries the Defender Operational channel
through `WindowsEventLogReader`, parses the returned XML, and emits normalized
evidence. If pywin32 is missing on the Windows Guest Agent, collection returns a
structured `failed` result rather than a 500 response.

Collection event IDs:

```text
1006  malware detected
1007  malware action taken
1116  malware detected
1117  malware action taken
1118  malware action failed
1119  malware action critically failed
```

Attribution is intentionally conservative:

- `strong`: event timestamp is inside the case window and the event path matches
  the current case sample/workspace path.
- `medium`: event timestamp is inside the case window and process id or process
  name matches the current case execution metadata.
- `weak`: event timestamp is inside the case window and only the threat name
  suggests EICAR or Defender evidence.
- `unattributed`: the event cannot be tied to the current case.

Only `strong` and `medium` attribution can affect the collector verdict.
`weak` and `unattributed` events remain visible evidence but do not create a
confident verdict.

Verdict mapping:

- Remediation actions such as quarantine, remove, delete, block, clean, or
  disinfect with `strong` / `medium` attribution produce `intercepted`.
- Detection events with `strong` / `medium` attribution produce `detected`.
- `Allow`, `No action`, or `None` actions produce `detected_only`, not blocked.
- `1118` / `1119` action failure events produce `detected_with_action_failed`
  or an inconclusive result, not blocked.
- Missing or weak evidence remains `unknown` or `not_intercepted` depending on
  whether any product events were found.

The collector records normalized evidence and metadata only. It does not include
raw EVTX or binary Event Log snapshots in the default evidence bundle.

## Smoke Test Sign-Off

2026-05-24: Windows Defender EICAR smoke test passed in an isolated
Lighthouse Windows environment. This was a real `single-run` smoke test, not a
read-only collection-only check.

Run:

```text
runs/20260524-183317_eicar__windows-defender
```

Key results:

- `product_id = "windows-defender"` was selected and persisted through the run.
- Security product readiness returned `ready` for `log_observability`;
  `protection_state` remained `unknown`.
- Upload observation ended in `removed_after_save`.
- Execution was skipped as `skipped_removed_after_save`, because the uploaded
  file was already gone before controlled execution.
- Collection finished with `collection_state = "collected"`.
- Normalized evidence contained Defender Operational events:
  - `1116` detected `Virus:DOS/EICAR_Test_File`;
  - `1117` action taken / quarantine for `Virus:DOS/EICAR_Test_File`.
- Both evidence records had `attribution = "strong"` with
  `matched_on = ["time_window", "path"]`.
- Case summary verdict was `detected_or_blocked` with `high` confidence.
- Cleanup finished with `cleanup_status = "restored"`.

Evidence bundle checks:

- raw EVTX was not included;
- uploaded sample bytes were not included;
- token values, cloud credentials, and `configs/real.toml` were not included;
- only `sample/sample.json` metadata was included under `sample/`;
- `case_security_product_readiness.json` was included as redacted readiness
  metadata.

The run root `case_summary.json` previously exposed the cloud-side case
workspace path in product-log evidence. The summary generation path now applies
the shared text redaction model so externally copied summary JSON uses
`<case_workspace>` consistently. This does not change the evidence bundle
allowlist or raw artifact policy.

## Core Event IDs

The first parser MVP classifies these event IDs:

```text
1006  malware detected
1007  malware action taken
1116  malware detected
1117  malware action taken
1118  malware action failed
1119  malware action critically failed
5000  real-time protection enabled
5001  real-time protection disabled
5011  antivirus scanning enabled
5012  antivirus scanning disabled
```

Classification is intentionally narrow:

- `1006`, `1116` -> `detected`
- `1007`, `1117` -> `action_taken`
- `1118`, `1119` -> `action_failed`
- `5000`, `5001`, `5011`, `5012` -> `protection_state_changed`
- anything else -> `unknown`

Protection state events are observability metadata. They do not prove the
current real-time protection state for a specific case and do not create a
collector verdict.

## Parser Contract

The parser entry point is:

```python
parse_windows_defender_event_xml(xml_text: str) -> WindowsDefenderParsedEvent
```

It reads structured XML fields instead of localized Event Viewer prose:

```text
System/EventID
System/Provider/@Name
System/Channel
System/Computer
System/EventRecordID
System/TimeCreated/@SystemTime
EventData/Data[@Name=...]
```

The output schema is `WindowsDefenderParsedEvent`. Important fields include:

```text
event_id
event_kind
observed_at_utc
provider
channel
computer
record_id
threat_name
threat_id
severity
category
path
process_name
user
action
action_status
error_code
error_description
raw_event_data
raw_event_data_items
```

Missing fields are represented as `None`. Unknown structured fields remain in
`raw_event_data`. Duplicate structured fields are preserved through
`raw_event_data_items` and numbered keys in `raw_event_data`, such as `Path#2`.

## Safety Boundary

The Windows Defender collector:

- reads only Defender Operational event metadata through the reader abstraction;
- does not call PowerShell, `cmd`, `wevtutil`, or shell commands;
- does not read uploaded sample bytes;
- does not execute samples;
- does not modify Defender configuration;
- does not add exclusions;
- does not start, stop, repair, disable, or weaken Defender;
- does not write raw EVTX files into evidence bundles;
- does not touch `configs/real.toml`;
- does not trigger Tencent Cloud APIs.

## Next Stages

The Windows Defender EICAR smoke test is now complete. Future work can focus on
repeatability improvements, additional attribution fields, or onboarding a
third product through the existing product profile / readiness probe /
collector / docs / tests path. Local tests remain fake-reader based and do not
read this machine's Event Log.
