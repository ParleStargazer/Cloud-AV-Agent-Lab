# Windows Defender Collector Model

This document tracks the Windows Defender / Microsoft Defender Antivirus
onboarding work. The current implementation includes stage 1 XML parser support
and stage 2 readiness scaffolding with an injected fake reader in tests. It does
not read the live Windows Event Log, does not connect to Guest Agent endpoints,
and is not registered as a product collector yet.

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

Stage 1 parses exported event XML fixtures only. Later stages may add a Windows
Event Log reader abstraction and a real collector.

## Readiness Stage 2

Stage 2 defines a reader protocol and a Windows Defender readiness probe, but it
does not provide a real reader implementation.

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

The probe is constructed with an injected reader and platform provider. Tests
use fake readers only. There is intentionally no pywin32, ctypes, PowerShell,
`wevtutil`, `cmd`, or shell reader in this stage.

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

The Windows Defender stage 1 parser:

- does not read live Windows Event Logs;
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

Stage 3 should register the `windows-defender` collector, query the Operational
channel through the reader, normalize product evidence, and then run a real
EICAR smoke test on an isolated cloud Windows host.
