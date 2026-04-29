# AV Test Analysis Skill

Use this skill when analyzing structured AV test results produced by Cloud AV Agent Lab.

## Scope

- Compare detection outcomes across products.
- Identify cases where competitor products detected or blocked a behavior while the target product did not.
- Map evidence to high-level behavior categories such as persistence, privilege escalation, injection, or outbound network activity.
- Produce concise report sections with evidence-backed conclusions.

## Boundaries

- Do not request, create, modify, download, or execute malware samples.
- Do not provide evasion or bypass guidance.
- Treat missing evidence as `unknown`.
- Prefer cloud artifact references over local paths.

## Inputs

- Case metadata: sample id, hash, category, expected behaviors.
- Product metadata: product id, display name, vendor.
- Signals: log, UI, behavior, process, filesystem, registry, network.
- Baseline comparison: optional manual result for the same sample and product.

## Output Checklist

- Detection matrix by product.
- Evidence summary for each detected case.
- Unknown or inconclusive cases.
- Competitor-only detection cases.
- Suggested defensive follow-up, phrased as detection engineering or telemetry improvement.

