from __future__ import annotations

from cloud_av_agent_lab.adapters.cloud import CloudVmAdapter
from cloud_av_agent_lab.adapters.factory import create_cloud_adapter
from cloud_av_agent_lab.adapters.guest import GuestAutomationAdapter
from cloud_av_agent_lab.adapters.null_guest import PlannedGuestAutomationAdapter
from cloud_av_agent_lab.core.contracts import (
    CaseResult,
    CaseStatus,
    LabConfig,
    TestCase,
)
from cloud_av_agent_lab.core.safety import assert_safe_config
from cloud_av_agent_lab.detectors.behavior_rules import parse_behavior_signals
from cloud_av_agent_lab.detectors.log_rules import parse_log_signals


class PipelineExecutionError(RuntimeError):
    """Raised when execution is requested without executable adapters."""


class TestPipeline:
    def __init__(
        self,
        config: LabConfig,
        cloud: CloudVmAdapter | None = None,
        guest: GuestAutomationAdapter | None = None,
    ) -> None:
        self.config = config
        self.cloud = cloud or create_cloud_adapter(config)
        self.guest = guest or PlannedGuestAutomationAdapter()

    def build_plan(self) -> list[TestCase]:
        cases: list[TestCase] = []
        for sample in self.config.samples.values():
            for vm in self.config.vms.values():
                product = self.config.products[vm.product_id]
                cases.append(
                    TestCase(
                        id=f"{sample.id}__{product.id}",
                        sample=sample,
                        vm=vm,
                        product=product,
                    )
                )
        return cases

    def dry_run(self) -> list[str]:
        assert_safe_config(self.config)
        events: list[str] = []
        for case in self.build_plan():
            events.extend(
                [
                    str(self.cloud.restore_snapshot(case.vm)),
                    str(self.cloud.start_vm(case.vm)),
                    self.guest.prepare_case(case),
                    self.guest.stage_sample_from_cloud(case.sample),
                    self.guest.execute_sample(
                        case.sample,
                        self.config.policy.max_case_seconds,
                    ),
                    f"plan: collect logs for {case.product.id}",
                    f"plan: collect behavior observations for {case.id}",
                    self.cloud.capture_screenshot(case),
                    str(self.cloud.restore_snapshot(case.vm)),
                ]
            )
        return events

    def run(self) -> list[CaseResult]:
        assert_safe_config(self.config)
        if not self.cloud.supports_execution or not self.guest.supports_execution:
            raise PipelineExecutionError(
                "real execution requires cloud and guest adapters with supports_execution=true"
            )

        results: list[CaseResult] = []
        for case in self.build_plan():
            result = CaseResult(case=case, status=CaseStatus.RUNNING)
            try:
                self.cloud.restore_snapshot(case.vm)
                self.cloud.start_vm(case.vm)
                self.guest.prepare_case(case)
                self.guest.stage_sample_from_cloud(case.sample)
                self.guest.execute_sample(
                    case.sample,
                    self.config.policy.max_case_seconds,
                )
                logs = self.guest.collect_logs(case.product)
                for source, text in logs.items():
                    result.signals.extend(parse_log_signals(case.product, source, text))
                observations = self.guest.collect_behavior_observations(case)
                result.signals.extend(
                    parse_behavior_signals(case.product, observations)
                )
                result.artifacts["screenshot"] = self.cloud.capture_screenshot(case)
                result.status = (
                    CaseStatus.DETECTED if result.detected else CaseStatus.MISSED
                )
            except Exception as exc:  # pragma: no cover - adapter-dependent
                result.status = CaseStatus.ERROR
                result.errors.append(str(exc))
            finally:
                self.cloud.restore_snapshot(case.vm)
                results.append(result)
        return results

    def planned_results(self) -> list[CaseResult]:
        return [
            CaseResult(case=case, status=CaseStatus.PLANNED)
            for case in self.build_plan()
        ]
