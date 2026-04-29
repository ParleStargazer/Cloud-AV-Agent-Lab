from __future__ import annotations

from typing import Mapping

from cloud_av_agent_lab.core.contracts import ProductProfile, SampleReference, TestCase


class PlannedGuestAutomationAdapter:
    """Plan-only guest adapter used for dry runs."""

    supports_execution = False

    def prepare_case(self, case: TestCase) -> str:
        return f"plan: prepare guest workspace for {case.id}"

    def stage_sample_from_cloud(self, sample: SampleReference) -> str:
        return f"plan: guest fetches {sample.cloud_object_uri}"

    def execute_sample(self, sample: SampleReference, timeout_seconds: int) -> str:
        return f"plan: execute {sample.id} with timeout {timeout_seconds}s"

    def collect_logs(self, product: ProductProfile) -> Mapping[str, str]:
        return {path: "" for path in product.log_paths}

    def collect_behavior_observations(self, case: TestCase) -> Mapping[str, object]:
        return {"case_id": case.id, "planned": True}
