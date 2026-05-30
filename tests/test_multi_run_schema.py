from __future__ import annotations

import json
import unittest

from cloud_av_agent_lab.orchestration.multi_run import (
    BATCH_PLAN_SCHEMA_VERSION,
    MULTI_RUN_EVENT_SCHEMA_VERSION,
    MULTI_RUN_STATE_SCHEMA_VERSION,
    SAMPLE_MANIFEST_ENTRY_SCHEMA_VERSION,
    BatchExecutionPolicy,
    BatchPlan,
    BatchSelection,
    CaseState,
    MultiRunEvent,
    MultiRunState,
    SampleManifestEntry,
    VERDICTS,
)


class MultiRunSchemaTests(unittest.TestCase):
    def test_sample_manifest_entry_serializes_manifest_schema(self) -> None:
        entry = SampleManifestEntry(
            manifest_id="manifest-001",
            manifest_created_at_utc="2026-05-30T10:00:00Z",
            manifest_tool_version="0.1.0",
            sample_index=1,
            sample_id="a" * 64,
            case_name="aaaaaaaaaaaaaaaa",
            sha256="a" * 64,
            md5="b" * 32,
            size=68,
            original_filename="eicar.bat",
            original_suffix=".bat",
            normalized_suffix=".bat",
            renamed_filename="0001_aaaaaaaaaaaaaaaa.bat",
            sample_ref=r"C:\CloudAvSamples\indexed\0001_aaaaaaaaaaaaaaaa.bat",
            duplicate_group_id="sha256:" + ("a" * 64),
            aliases=("eicar.bat", "copy.bat"),
            created_at_utc="2026-05-30T10:01:00Z",
        )

        payload = entry.to_dict()

        self.assertEqual(
            payload["schema_version"], SAMPLE_MANIFEST_ENTRY_SCHEMA_VERSION
        )
        self.assertEqual(payload["sample_index"], 1)
        self.assertEqual(payload["sample_source_kind"], "local_platform_path")
        self.assertEqual(payload["entry_status"], "ready")
        self.assertEqual(payload["aliases"], ["eicar.bat", "copy.bat"])
        json.dumps(payload)

    def test_batch_plan_serializes_selection_and_execution_policy(self) -> None:
        plan = BatchPlan(
            batch_id="batch_20260530-210000_qihoo-360",
            created_at_utc="2026-05-30T13:00:00Z",
            product_id="qihoo-360",
            instance_id="lhins-test",
            snapshot_id="lhsnap-test",
            region="ap-singapore",
            sample_manifest_path="sample_manifest.jsonl",
            manifest_sha256="c" * 64,
            generated_config_sha256="d" * 64,
            selection=BatchSelection(
                mode="range",
                selected_indexes=(1, 2, 3),
                range_text="1-3",
                max_cases=3,
            ),
            execution=BatchExecutionPolicy(
                mode="serial",
                failure_policy="stop-on-case-failure",
                dry_run=True,
                plan_only=True,
                case_timeout_seconds=1800.0,
            ),
            single_run_runner_version="single-run.v1",
            multi_run_version="multi-run.v1",
            product_profile_version="product-profile.v1",
        )

        payload = plan.to_dict()

        self.assertEqual(payload["schema_version"], BATCH_PLAN_SCHEMA_VERSION)
        self.assertEqual(payload["product_id"], "qihoo-360")
        self.assertEqual(payload["selection"]["selected_indexes"], [1, 2, 3])
        self.assertEqual(payload["selection"]["range"], "1-3")
        self.assertTrue(payload["execution"]["dry_run"])
        self.assertTrue(payload["execution"]["plan_only"])
        self.assertEqual(payload["execution"]["mode"], "serial")
        json.dumps(payload)

    def test_multi_run_state_serializes_case_status_breakdown(self) -> None:
        case_state = CaseState(
            sample_index=7,
            sample_id="sample-007",
            case_id="sample-007__huorong",
            run_id="run-007",
            attempt=1,
            case_status="completed",
            single_run_status="completed",
            cleanup_status="restored",
            evidence_status="exported",
            summary_status="collected",
            readiness_status="warning",
            resume_eligible=True,
            verdict="detected_or_blocked",
            confidence="high",
            evidence_bundle_path="cases/0007/single_run/case_evidence.zip",
            run_state_path="cases/0007/single_run/run_state.json",
            case_summary_path="cases/0007/single_run/case_summary.json",
            warnings=("security product readiness warning",),
        )
        state = MultiRunState(
            batch_id="batch-001",
            batch_state="running",
            product_id="huorong",
            instance_id="lhins-test",
            snapshot_id="lhsnap-test",
            region="ap-singapore",
            sample_manifest_path="sample_manifest.jsonl",
            manifest_sha256="e" * 64,
            batch_plan_sha256="f" * 64,
            selected_indexes=(7,),
            cases=(case_state,),
            started_at_utc="2026-05-30T14:00:00Z",
        )

        payload = state.to_dict()

        self.assertEqual(payload["schema_version"], MULTI_RUN_STATE_SCHEMA_VERSION)
        self.assertEqual(payload["selected_indexes"], [7])
        self.assertFalse(payload["unsafe_to_continue"])
        self.assertEqual(payload["cases"][0]["cleanup_status"], "restored")
        self.assertEqual(payload["cases"][0]["evidence_status"], "exported")
        self.assertEqual(payload["cases"][0]["summary_status"], "collected")
        self.assertEqual(payload["cases"][0]["verdict"], "detected_or_blocked")
        json.dumps(payload)

    def test_multi_run_event_uses_append_only_jsonl_shape(self) -> None:
        event = MultiRunEvent(
            seq=5,
            event_type="cleanup_verified",
            at_utc="2026-05-30T15:00:00Z",
            batch_id="batch-001",
            sample_index=1,
            sample_id="sample-001",
            run_id="run-001",
            case_id="sample-001__huorong",
            case_status="completed",
            data={"selected_indexes": (1, 2)},
        )

        payload = event.to_dict()

        self.assertEqual(payload["schema_version"], MULTI_RUN_EVENT_SCHEMA_VERSION)
        self.assertEqual(payload["type"], "cleanup_verified")
        self.assertEqual(payload["data"]["selected_indexes"], [1, 2])
        json.dumps(payload)

    def test_fixed_verdict_values_include_multi_run_aggregate_set(self) -> None:
        self.assertIn("detected_or_blocked", VERDICTS)
        self.assertIn("allowed_executed", VERDICTS)
        self.assertIn("not_delivered", VERDICTS)
        self.assertIn("not_executed", VERDICTS)
        self.assertIn("inconclusive", VERDICTS)
        self.assertIn("not_evaluable", VERDICTS)
        self.assertIn("unknown", VERDICTS)


if __name__ == "__main__":
    unittest.main()
