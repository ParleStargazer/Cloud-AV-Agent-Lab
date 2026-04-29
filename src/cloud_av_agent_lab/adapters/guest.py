from __future__ import annotations

from typing import Mapping, Protocol

from cloud_av_agent_lab.core.contracts import ProductProfile, SampleReference, TestCase


class GuestAutomationAdapter(Protocol):
    supports_execution: bool

    def prepare_case(self, case: TestCase) -> str:
        """Create a guest-side workspace for one case."""

    def stage_sample_from_cloud(self, sample: SampleReference) -> str:
        """Fetch the sample from cloud object storage inside the guest."""

    def execute_sample(self, sample: SampleReference, timeout_seconds: int) -> str:
        """Run the approved case inside the guest under a strict timeout."""

    def collect_logs(self, product: ProductProfile) -> Mapping[str, str]:
        """Return product log contents keyed by artifact source."""

    def collect_behavior_observations(self, case: TestCase) -> Mapping[str, object]:
        """Return normalized behavior observations for the case."""
