from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class RunState:
    def __init__(
        self,
        path: Path,
        *,
        run_id: str,
        case_id: str,
        instance_id: str,
        snapshot_id: str,
        region: str,
        product_id: str,
        sample_name: str,
        sample_path: str,
    ) -> None:
        self.path = path
        self.data: dict[str, Any] = {
            "schema_version": "single-run-state.v1",
            "run_id": run_id,
            "case_id": case_id,
            "instance_id": instance_id,
            "snapshot_id": snapshot_id,
            "region": region,
            "product_id": product_id,
            "sample": {
                "name": sample_name,
                "path": sample_path,
                "sha256": "",
                "size": None,
            },
            "steps": [],
            "status": "pending",
            "test_verdict": "",
            "stages": {
                "environment": {},
                "delivery": {},
                "security_product_readiness": {"status": "pending"},
                "execution": {},
                "collection": {},
                "summary": {},
                "evidence": {},
                "cleanup": {},
            },
            "case_started": False,
            "evidence_export_status": "skipped",
            "evidence_bundle_path": "",
            "agent_dead": False,
            "cleanup_status": "skipped",
            "emergency_poweroff_status": "not_needed",
            "final_status": "pending",
            "warnings": [],
            "fatal_errors": [],
            "artifacts": {},
            "errors": [],
            "created_at_utc": utc_now(),
            "updated_at_utc": utc_now(),
        }
        self.write()

    def set_sample_hash(self, sha256: str, size: int) -> None:
        sample = self.data.setdefault("sample", {})
        if isinstance(sample, dict):
            sample["sha256"] = sha256
            sample["size"] = size
        self.write()

    def mark(self, key: str, value: Any) -> None:
        self.data[key] = value
        self.write()

    def mark_stage(self, stage: str, key: str, value: Any) -> None:
        stages = self.data.setdefault("stages", {})
        if not isinstance(stages, dict):
            stages = {}
            self.data["stages"] = stages
        stage_payload = stages.setdefault(stage, {})
        if not isinstance(stage_payload, dict):
            stage_payload = {}
            stages[stage] = stage_payload
        stage_payload[key] = value
        self.write()

    def mark_artifact(self, name: str, value: Any) -> None:
        artifacts = self.data.setdefault("artifacts", {})
        if not isinstance(artifacts, dict):
            artifacts = {}
            self.data["artifacts"] = artifacts
        artifacts[name] = value
        self.write()

    def add_warning(self, stage: str, message: str) -> None:
        warnings = self.data.setdefault("warnings", [])
        if isinstance(warnings, list):
            warnings.append(
                {
                    "stage": stage,
                    "message": message,
                    "recorded_at_utc": utc_now(),
                }
            )
        self.write()

    def add_fatal_error(self, stage: str, error: BaseException | str) -> None:
        fatal_errors = self.data.setdefault("fatal_errors", [])
        if isinstance(fatal_errors, list):
            fatal_errors.append(
                {
                    "stage": stage,
                    "message": str(error),
                    "recorded_at_utc": utc_now(),
                }
            )
        self.write()

    def add_error(self, stage: str, error: BaseException | str) -> None:
        self.data.setdefault("errors", []).append(
            {
                "stage": stage,
                "message": str(error),
                "recorded_at_utc": utc_now(),
            }
        )
        self.write()

    @contextmanager
    def step(self, name: str) -> Iterator[dict[str, Any]]:
        entry: dict[str, Any] = {
            "name": name,
            "status": "running",
            "started_at_utc": utc_now(),
            "finished_at_utc": "",
            "message": "",
        }
        steps = self.data.setdefault("steps", [])
        if isinstance(steps, list):
            steps.append(entry)
        self.write()
        try:
            yield entry
        except Exception as exc:
            entry["status"] = "failed"
            entry["finished_at_utc"] = utc_now()
            entry["message"] = str(exc)
            self.write()
            raise
        else:
            entry["status"] = "ok"
            entry["finished_at_utc"] = utc_now()
            self.write()

    def write(self) -> None:
        self.data["updated_at_utc"] = utc_now()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.path.with_suffix(self.path.suffix + ".tmp")
        temp_path.write_text(
            json.dumps(self.data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temp_path.replace(self.path)
