# Security Product Onboarding

This document defines the checklist for adding a new security product to Cloud
AV Agent Lab. It keeps product-specific behavior inside readiness probes and
collectors, while the orchestration, evaluator, and evidence exporter continue
to consume stable schemas.

## Scope

Adding a product means implementing two optional but strongly recommended
components:

- a `security_product_readiness` probe for low-intrusion pre-delivery
  observability checks;
- a collector plugin that reads product logs after delivery and optional
  controlled execution, then emits normalized evidence.

Do not add product-specific logic to single-run orchestration, the evaluator,
or the evidence exporter unless a schema change is required and covered by
tests.

## Safety Boundary

Product integrations must keep the existing project boundary:

- no real malware samples;
- no local sample execution;
- no arbitrary shell, cmd, PowerShell, exec, or run-command behavior;
- no arbitrary guest path supplied by the client;
- no service start/stop/repair, registry modification, policy changes, or
  product configuration changes;
- no token, cloud key, environment variable, or `configs/real.toml` output;
- no raw binary product logs in the default redacted evidence bundle.

Readiness and collectors may inspect product metadata and product logs inside
the cloud guest. They must not read uploaded sample bytes.

## Product Metadata

Each product must choose a stable lowercase `product_id`, for example:

```text
huorong
windows-defender
tencent-pc-manager
```

Use the same `product_id` in:

- VM/product config;
- the single-run generated-config product profile;
- readiness probe registry;
- collector registry;
- collection artifacts;
- tests and fixtures.

Avoid aliases in the first implementation. If aliases are needed later, keep
them in the registry layer, not in every caller.

## Readiness Probe Contract

Readiness probes live under:

```text
src/cloud_av_agent_lab/guest_agent_server/security_product_readiness/
```

The shared contract is in `base.py`:

- `SecurityProductReadinessContext`
- `SecurityProductReadinessCheck`
- `SecurityProductReadinessResult`
- `SecurityProductReadinessProbe`

Register new probes in `registry.py`.

A readiness probe answers only whether the product's observation path appears
usable before delivery. It does not parse interception logs and does not own
final verdicts.

Required behavior:

- return one of `ready`, `partial`, `not_ready`, `unknown`, or `unsupported`;
- set `scope`, usually `log_observability` for the first version;
- keep `protection_state = "unknown"` unless a future low-risk, read-only,
  well-tested probe can actually prove protection state;
- write clear `checks`, `warnings`, and `errors`;
- copy live logs only when necessary, and treat copied files as guest-side raw
  snapshots, not default evidence-bundle content.

Readiness states are used by the evaluator only as a conservative gate for
`no_detection_observed`: only `ready` allows that verdict. Readiness never
creates `detected_or_blocked` and never overrides product-log evidence.

## Collector Contract

Collectors live under:

```text
src/cloud_av_agent_lab/guest_agent_server/collectors/<product_id>/
  __init__.py
  collector.py
  parser.py
  schema.py
```

The shared contract is in `collectors/base.py`:

- `CollectionWindow`
- `NormalizedSecurityEvent`
- `CollectorArtifact`
- `CollectorResult`
- `ProductLogCollector`

Register new collectors in `collectors/registry.py`. The workspace collection
layer must call the registry rather than importing product collectors directly.

Collectors own product-specific details:

- log locations;
- log copy strategy;
- schema discovery and parser fallback;
- timestamp conversion;
- product action mapping;
- artifact sensitivity and include/exclude recommendations;
- conservative case attribution.

Collectors must return `CollectorResult` and write normalized product evidence
only. They should prefer `unknown` over a confident false negative when logs
are missing, unreadable, incomplete, or cannot be safely attributed to the
current case.

## Evidence Attribution Policy

Different products expose different fields. Missing fields must degrade
confidence rather than crash the collector or force a verdict.

Use this policy when normalizing product events:

- `strong`: current sample hash matched, or current case sample path matched,
  and the event is inside or reasonably attributable to the collection window.
- `medium`: current root/child PID matched and the event is inside the time
  window, or another product-specific stable identifier strongly links the
  event to the case.
- `weak`: filename, threat name, product description, or nearby timing suggests
  relevance, but hash/path/PID attribution is missing.
- `unattributed`: product log exists, but it cannot be tied to the current case.

Only strong or medium attribution, paired with a clear product action such as
detected, blocked, quarantined, deleted, or denied, should produce product-log
evidence for `detected_or_blocked`.

Weak or unattributed events should remain in normalized evidence or warnings
when useful, but they must not by themselves create a confident detection
verdict.

Recommended event evidence shape:

```json
{
  "product_action": "blocked",
  "case_relevant": true,
  "attribution": {
    "level": "strong",
    "matched_on": ["sha256", "time_window"],
    "missing_fields": [],
    "limitations": []
  }
}
```

Examples of degradation:

- missing `sha256`: use path, PID, and time-window evidence if available;
- missing timestamp: do not rely on time-window attribution; require strong
  hash/path attribution or keep the event weak/inconclusive;
- missing action/result: record the event, but do not claim block/quarantine;
- missing path: require hash or PID attribution before treating it as relevant;
- missing PID: do not link the event to execution unless hash/path is enough.

## Artifact Policy

Every copied product artifact must be declared with `CollectorArtifact`.

Default rules:

- raw SQLite, EVTX, WAL, SHM, executable, DLL, and proprietary binary logs are
  `raw_product_log`, `include_in_evidence=false`,
  `redaction_state=raw_blocked`, and `sensitivity=high`;
- normalized JSON / JSONL evidence may be included only after redaction;
- product-specific raw snapshots must not be added to exporter allowlists;
- the evidence exporter makes the final safety decision and may still exclude a
  collector-suggested artifact.

Readiness snapshots under `security-product-readiness/` follow the same raw
artifact boundary. Only `case_security_product_readiness.json` may enter the
default redacted evidence bundle.

## Tests Required For A New Product

Add tests before considering a product supported:

- readiness ready / partial / not_ready / unknown behavior;
- unsupported product remains safe and clear;
- collector returns normalized events for at least one fixture;
- parser tolerates schema rotation or missing optional fields;
- missing critical fields degrade attribution and verdict conservatively;
- timestamp conversion and time-window filtering;
- artifact policy excludes raw logs from the default evidence bundle;
- token / secret / environment / real config strings do not leak;
- case path traversal is rejected by existing endpoint tests;
- evaluator does not produce `no_detection_observed` unless readiness is ready.

Fixtures should be minimal, redacted, and harmless. Do not store real malware or
live secrets in fixtures.

## Registration Checklist

For readiness:

1. Implement `<product_id>.py` with a probe class.
2. Add it to `SUPPORTED_SECURITY_PRODUCT_READINESS_PROBES` in
   `security_product_readiness/registry.py`.
3. Add tests and documentation notes.

For collection:

1. Add `collectors/<product_id>/`.
2. Implement a `ProductLogCollector`.
3. Add it to `SUPPORTED_COLLECTORS` in `collectors/registry.py`.
4. Add parser fixtures and artifact policy tests.

Do not add product-specific conditionals to `single_run.py`,
`evaluation/evaluator.py`, or `evidence/exporter.py` for ordinary onboarding.
The only ordinary `single_run.py` change should be adding a metadata entry to
`SINGLE_RUN_PRODUCT_PROFILES`; CLI choices are derived from that profile list.

## Review Questions

Before onboarding a product, answer:

- Where are logs stored?
- Are logs text, JSON, XML, EVTX, SQLite, or proprietary binary?
- Are companion files needed, such as WAL/SHM?
- Are logs locked while the product runs?
- What timestamp format and timezone does the product use?
- Which fields prove case attribution: hash, path, PID, process tree, event ID,
  or another stable identifier?
- Which product actions clearly mean detection, blocking, quarantine, deletion,
  or denial?
- Which actions are only scan records, UI prompts, or ambiguous events?
- What happens if the product lacks hash, path, PID, timestamp, or action
  fields?
- Which raw artifacts are copied, and why are they excluded from the default
  evidence bundle?
