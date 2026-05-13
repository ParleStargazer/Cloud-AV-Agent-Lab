# Safety Model

This repository is designed for local automation development without local malware handling.

## Allowed Locally

- Store source code, TOML configs, generated reports, and sanitized logs.
- Store cloud object references to already approved samples.
- Read a user-explicit EICAR or harmless test file path for the upload MVP.
- Analyze structured observations collected from isolated VMs.
- Compare AV products by detection signal, confidence, and evidence.

## Not Allowed Locally

- Store executable samples or archives containing samples.
- Read, receive, upload, download, save, open, extract, scan, parse, or execute
  real malware samples in the current development phase.
- Download samples from cloud storage to the developer machine.
- Execute samples on the developer machine.
- Add instructions whose purpose is to bypass, disable, or evade AV products.
- Mix multiple test cases in one dirty VM state.

## Required Cloud Controls

- Use a clean snapshot per AV product baseline.
- A single cloud instance may host multiple product baselines through separate snapshots, but those profiles must run serially and must not share a dirty VM state.
- Restore the snapshot before and after each case.
- Use an isolated network profile by default.
- Store artifacts in a cloud bucket with limited retention.
- Keep cloud credentials in environment variables or a secret manager, never in TOML.
- Keep every case bounded by timeout.

## Development Proxy

The optional `[network.proxy]` configuration is only for development-time control-plane connectivity to cloud APIs or a cloud Guest Agent. It must stay disabled by default and must never be used to route samples to the local machine.

When proxy support is no longer needed for delivery, disable it with `enabled = false` or remove the proxy config and `network/proxy.py` module. The sample boundary remains unchanged either way.

## Evidence Standards

Every detection claim should include at least one evidence source:

- AV log line or event ID;
- UI alert screenshot reference;
- quarantine or block event;
- process/file/registry/network observation;
- manual baseline comparison.

If evidence is missing, the result should be `unknown`, not `detected` or `missed`.
