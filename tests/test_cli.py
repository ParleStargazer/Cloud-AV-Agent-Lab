from __future__ import annotations

import sys
import tempfile
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
from io import StringIO
from pathlib import Path
from unittest import TestCase
from unittest.mock import call, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cloud_av_agent_lab.cli import (
    _restore_execution_guard,
    _write_execution_guard,
    main,
)
from cloud_av_agent_lab.config import load_config
from cloud_av_agent_lab.adapters.guest_agent_client import (
    GuestAgentError,
    GuestAgentResponse,
)


class CloudLifecycleCliGuardTests(TestCase):
    def test_write_guard_requires_real_mode_dry_run_false_and_confirm_match(
        self,
    ) -> None:
        config = load_config(ROOT / "configs" / "lab.example.toml")
        config = replace(
            config,
            cloud=replace(config.cloud, mode="real", dry_run=False),
        )

        allowed, reasons = _write_execution_guard(
            config,
            resolved_instance_id="lhins-example",
            confirm_instance="lhins-example",
        )

        self.assertTrue(allowed)
        self.assertEqual(reasons, [])

    def test_write_guard_blocks_missing_or_wrong_confirmation(self) -> None:
        config = load_config(ROOT / "configs" / "lab.example.toml")
        config = replace(
            config,
            cloud=replace(config.cloud, mode="real", dry_run=False),
        )

        missing_allowed, missing_reasons = _write_execution_guard(
            config,
            resolved_instance_id="lhins-example",
            confirm_instance="",
        )
        wrong_allowed, wrong_reasons = _write_execution_guard(
            config,
            resolved_instance_id="lhins-example",
            confirm_instance="lhins-other",
        )

        self.assertFalse(missing_allowed)
        self.assertIn("--confirm-instance was not provided", missing_reasons)
        self.assertFalse(wrong_allowed)
        self.assertIn(
            "--confirm-instance does not match resolved instance id",
            wrong_reasons,
        )

    def test_write_guard_blocks_dry_run_config(self) -> None:
        config = load_config(ROOT / "configs" / "lab.example.toml")
        config = replace(
            config,
            cloud=replace(config.cloud, mode="real", dry_run=True),
        )

        allowed, reasons = _write_execution_guard(
            config,
            resolved_instance_id="lhins-example",
            confirm_instance="lhins-example",
        )

        self.assertFalse(allowed)
        self.assertIn("cloud dry_run is true", reasons)

    def test_restore_guard_requires_snapshot_confirmation(self) -> None:
        config = load_config(ROOT / "configs" / "lab.example.toml")
        config = replace(
            config,
            cloud=replace(config.cloud, mode="real", dry_run=False),
        )

        allowed, reasons = _restore_execution_guard(
            config,
            resolved_instance_id="lhins-example",
            confirm_instance="lhins-example",
            baseline_snapshot="snap-example",
            confirm_snapshot="snap-example",
        )

        self.assertTrue(allowed)
        self.assertEqual(reasons, [])

    def test_restore_guard_blocks_wrong_snapshot_confirmation(self) -> None:
        config = load_config(ROOT / "configs" / "lab.example.toml")
        config = replace(
            config,
            cloud=replace(config.cloud, mode="real", dry_run=False),
        )

        allowed, reasons = _restore_execution_guard(
            config,
            resolved_instance_id="lhins-example",
            confirm_instance="lhins-example",
            baseline_snapshot="snap-example",
            confirm_snapshot="snap-other",
        )

        self.assertFalse(allowed)
        self.assertIn(
            "--confirm-snapshot does not match configured baseline_snapshot",
            reasons,
        )

    def test_guest_health_exits_clearly_when_guest_agent_disabled(self) -> None:
        stderr = StringIO()

        with redirect_stderr(stderr), self.assertRaises(SystemExit) as exit_error:
            main(
                [
                    "guest-health",
                    "--config",
                    str(ROOT / "configs" / "lab.example.toml"),
                    "--vm-id",
                    "win10-tencent-manager",
                ]
            )

        self.assertEqual(exit_error.exception.code, 2)
        self.assertIn("[Local Check]", stderr.getvalue())

    def test_guest_upload_exits_clearly_when_guest_agent_disabled(self) -> None:
        stderr = StringIO()

        with redirect_stderr(stderr), self.assertRaises(SystemExit) as exit_error:
            main(
                [
                    "guest-upload-sample",
                    "--config",
                    str(ROOT / "configs" / "lab.example.toml"),
                    "--vm-id",
                    "win10-tencent-manager",
                    "--sample-id",
                    "case-001",
                    "--case-id",
                    "case-001__tencent-pc-manager",
                    "--file",
                    str(ROOT / "state" / "tests" / "eicar.txt"),
                ]
            )

        self.assertEqual(exit_error.exception.code, 2)
        self.assertIn("[Local Check]", stderr.getvalue())

    def test_guest_case_status_exits_clearly_when_guest_agent_disabled(self) -> None:
        stderr = StringIO()

        with redirect_stderr(stderr), self.assertRaises(SystemExit) as exit_error:
            main(
                [
                    "guest-case-status",
                    "--config",
                    str(ROOT / "configs" / "lab.example.toml"),
                    "--vm-id",
                    "win10-tencent-manager",
                    "--case-id",
                    "case-001__tencent-pc-manager",
                ]
            )

        self.assertEqual(exit_error.exception.code, 2)
        self.assertIn("[Local Check]", stderr.getvalue())

    def test_guest_case_report_exits_clearly_when_guest_agent_disabled(self) -> None:
        stderr = StringIO()

        with redirect_stderr(stderr), self.assertRaises(SystemExit) as exit_error:
            main(
                [
                    "guest-case-report",
                    "--config",
                    str(ROOT / "configs" / "lab.example.toml"),
                    "--vm-id",
                    "win10-tencent-manager",
                    "--case-id",
                    "case-001__tencent-pc-manager",
                ]
            )

        self.assertEqual(exit_error.exception.code, 2)
        self.assertIn("[Local Check]", stderr.getvalue())

    def test_guest_execution_status_exits_clearly_when_guest_agent_disabled(
        self,
    ) -> None:
        stderr = StringIO()

        with redirect_stderr(stderr), self.assertRaises(SystemExit) as exit_error:
            main(
                [
                    "guest-execution-status",
                    "--config",
                    str(ROOT / "configs" / "lab.example.toml"),
                    "--vm-id",
                    "win10-tencent-manager",
                    "--case-id",
                    "case-001__tencent-pc-manager",
                ]
            )

        self.assertEqual(exit_error.exception.code, 2)
        self.assertIn("[Local Check]", stderr.getvalue())

    def test_guest_execute_sample_exits_clearly_when_guest_agent_disabled(
        self,
    ) -> None:
        stderr = StringIO()

        with redirect_stderr(stderr), self.assertRaises(SystemExit) as exit_error:
            main(
                [
                    "guest-execute-sample",
                    "--config",
                    str(ROOT / "configs" / "lab.example.toml"),
                    "--vm-id",
                    "win10-tencent-manager",
                    "--sample-id",
                    "case-001",
                    "--case-id",
                    "case-001__tencent-pc-manager",
                ]
            )

        self.assertEqual(exit_error.exception.code, 2)
        self.assertIn("[Local Check]", stderr.getvalue())

    def test_guest_upload_command_only_calls_upload_method(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "lab.guest-agent-enabled.toml"
            config_path.write_text(_guest_agent_enabled_config(), encoding="utf-8")
            upload_path = Path(tmp) / "eicar.txt"
            upload_path.write_bytes(b"EICAR harmless placeholder")
            fake_client = _FakeGuestAgentClient()
            stdout = StringIO()

            with (
                patch(
                    "cloud_av_agent_lab.cli._create_guest_agent_client",
                    return_value=fake_client,
                ),
                patch("cloud_av_agent_lab.cli.time.sleep") as sleep_mock,
                redirect_stdout(stdout),
            ):
                exit_code = main(
                    [
                        "guest-upload-sample",
                        "--config",
                        str(config_path),
                        "--vm-id",
                        "win10-tencent-manager",
                        "--sample-id",
                        "case-001",
                        "--case-id",
                        "case-001__tencent-pc-manager",
                        "--file",
                        str(upload_path),
                        "--sha256",
                        "0" * 64,
                    ]
                )

        self.assertEqual(exit_code, 0)
        self.assertEqual(fake_client.upload_calls, 1)
        self.assertGreater(fake_client.case_status_calls, 1)
        self.assertEqual(sleep_mock.call_args_list[0], call(10.0))
        self.assertIn(call(2.0), sleep_mock.call_args_list)
        self.assertFalse(fake_client.execute_called)
        output = stdout.getvalue()
        self.assertIn('"message": "sample uploaded"', output)
        self.assertIn('"message": "case status loaded"', output)
        self.assertIn("[Polling] 检查样本状态", output)
        self.assertIn("样本存活", output)

    def test_guest_case_status_404_suggests_prepare_case(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "lab.guest-agent-enabled.toml"
            config_path.write_text(_guest_agent_enabled_config(), encoding="utf-8")
            fake_client = _FailingGuestAgentClient(
                GuestAgentError(
                    "Guest Agent cases/missing-case/status returned HTTP 404 "
                    "Not Found: case workspace does not exist; run "
                    "guest-prepare-case first",
                    status_code=404,
                )
            )
            stderr = StringIO()

            with (
                patch(
                    "cloud_av_agent_lab.cli._create_guest_agent_client",
                    return_value=fake_client,
                ),
                redirect_stderr(stderr),
                self.assertRaises(SystemExit) as exit_error,
            ):
                main(
                    [
                        "guest-case-status",
                        "--config",
                        str(config_path),
                        "--vm-id",
                        "win10-tencent-manager",
                        "--case-id",
                        "missing-case",
                    ]
                )

        self.assertEqual(exit_error.exception.code, 2)
        output = stderr.getvalue()
        self.assertIn("HTTP 404 Not Found", output)
        self.assertIn("guest-prepare-case", output)
        self.assertIn("请确认 case_id 是否正确", output)

    def test_guest_case_status_network_error_is_attributed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "lab.guest-agent-enabled.toml"
            config_path.write_text(_guest_agent_enabled_config(), encoding="utf-8")
            fake_client = _FailingGuestAgentClient(
                GuestAgentError(
                    "无法连接到 Guest Agent，请确认云端服务已启动、IP/端口/防火墙/代理配置正确。",
                    source="network",
                )
            )
            stderr = StringIO()

            with (
                patch(
                    "cloud_av_agent_lab.cli._create_guest_agent_client",
                    return_value=fake_client,
                ),
                redirect_stderr(stderr),
                self.assertRaises(SystemExit) as exit_error,
            ):
                main(
                    [
                        "guest-case-status",
                        "--config",
                        str(config_path),
                        "--vm-id",
                        "win10-tencent-manager",
                        "--case-id",
                        "case-001__tencent-pc-manager",
                    ]
                )

        self.assertEqual(exit_error.exception.code, 2)
        output = stderr.getvalue()
        self.assertIn("[Network]", output)
        self.assertIn("无法连接到 Guest Agent", output)

    def test_guest_case_report_404_suggests_prepare_case(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "lab.guest-agent-enabled.toml"
            config_path.write_text(_guest_agent_enabled_config(), encoding="utf-8")
            fake_client = _FailingGuestAgentClient(
                GuestAgentError(
                    "Guest Agent cases/missing-case/report returned HTTP 404 "
                    "Not Found: case workspace does not exist; run "
                    "guest-prepare-case first",
                    status_code=404,
                )
            )
            stderr = StringIO()

            with (
                patch(
                    "cloud_av_agent_lab.cli._create_guest_agent_client",
                    return_value=fake_client,
                ),
                redirect_stderr(stderr),
                self.assertRaises(SystemExit) as exit_error,
            ):
                main(
                    [
                        "guest-case-report",
                        "--config",
                        str(config_path),
                        "--vm-id",
                        "win10-tencent-manager",
                        "--case-id",
                        "missing-case",
                    ]
                )

        self.assertEqual(exit_error.exception.code, 2)
        output = stderr.getvalue()
        self.assertIn("HTTP 404 Not Found", output)
        self.assertIn("guest-prepare-case", output)
        self.assertIn("请确认 case_id 是否正确", output)

    def test_guest_execute_sample_404_suggests_prepare_case(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "lab.guest-agent-enabled.toml"
            config_path.write_text(_guest_agent_enabled_config(), encoding="utf-8")
            fake_client = _FailingGuestAgentClient(
                GuestAgentError(
                    "Guest Agent cases/missing-case/actions returned HTTP 404 "
                    "Not Found: case workspace does not exist; run "
                    "guest-prepare-case first",
                    status_code=404,
                )
            )
            stderr = StringIO()

            with (
                patch(
                    "cloud_av_agent_lab.cli._create_guest_agent_client",
                    return_value=fake_client,
                ),
                redirect_stderr(stderr),
                self.assertRaises(SystemExit) as exit_error,
            ):
                main(
                    [
                        "guest-execute-sample",
                        "--config",
                        str(config_path),
                        "--vm-id",
                        "win10-tencent-manager",
                        "--sample-id",
                        "case-001",
                        "--case-id",
                        "missing-case",
                    ]
                )

        self.assertEqual(exit_error.exception.code, 2)
        output = stderr.getvalue()
        self.assertIn("HTTP 404 Not Found", output)
        self.assertIn("guest-prepare-case", output)
        self.assertIn("请确认 case_id 是否正确", output)

    def test_guest_execute_sample_defaults_to_dry_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "lab.guest-agent-enabled.toml"
            config_path.write_text(_guest_agent_enabled_config(), encoding="utf-8")
            fake_client = _FakeGuestAgentClient()
            stdout = StringIO()

            with (
                patch(
                    "cloud_av_agent_lab.cli._create_guest_agent_client",
                    return_value=fake_client,
                ),
                redirect_stdout(stdout),
            ):
                exit_code = main(
                    [
                        "guest-execute-sample",
                        "--config",
                        str(config_path),
                        "--vm-id",
                        "win10-tencent-manager",
                        "--sample-id",
                        "case-001",
                        "--case-id",
                        "case-001__tencent-pc-manager",
                    ]
                )

        self.assertEqual(exit_code, 0)
        self.assertEqual(len(fake_client.execute_calls), 1)
        call_info = fake_client.execute_calls[0]
        self.assertTrue(call_info["dry_run"])
        self.assertEqual(call_info["expected_sha256"], "0" * 63 + "1")
        self.assertFalse(fake_client.execute_called)
        output = stdout.getvalue()
        self.assertIn('"execution_state": "execution_dry_run_checked"', output)
        self.assertIn("没有启动样本进程", output)

    def test_guest_execute_sample_real_action_requires_execution_enabled(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "lab.guest-agent-enabled.toml"
            config_path.write_text(_guest_agent_enabled_config(), encoding="utf-8")
            fake_client = _FakeGuestAgentClient()
            stderr = StringIO()

            with (
                patch(
                    "cloud_av_agent_lab.cli._create_guest_agent_client",
                    return_value=fake_client,
                ),
                redirect_stderr(stderr),
                self.assertRaises(SystemExit) as exit_error,
            ):
                main(
                    [
                        "guest-execute-sample",
                        "--config",
                        str(config_path),
                        "--vm-id",
                        "win10-tencent-manager",
                        "--sample-id",
                        "case-001",
                        "--case-id",
                        "case-001__tencent-pc-manager",
                        "--real-action",
                    ]
                )

        self.assertEqual(exit_error.exception.code, 2)
        self.assertEqual(fake_client.execute_calls, [])
        self.assertIn("[Local Check]", stderr.getvalue())

    def test_guest_execute_sample_real_action_reports_remote_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "lab.guest-agent-enabled.toml"
            config_path.write_text(
                _guest_agent_execution_enabled_config(),
                encoding="utf-8",
            )
            fake_client = _FakeGuestAgentClient()
            stderr = StringIO()

            with (
                patch(
                    "cloud_av_agent_lab.cli._create_guest_agent_client",
                    return_value=fake_client,
                ),
                redirect_stderr(stderr),
                self.assertRaises(SystemExit) as exit_error,
            ):
                main(
                    [
                        "guest-execute-sample",
                        "--config",
                        str(config_path),
                        "--vm-id",
                        "win10-tencent-manager",
                        "--sample-id",
                        "case-001",
                        "--case-id",
                        "case-001__tencent-pc-manager",
                        "--expected-sha256",
                        "custom-sha",
                        "--real-action",
                    ]
                )

        self.assertEqual(exit_error.exception.code, 2)
        self.assertEqual(len(fake_client.execute_calls), 1)
        call_info = fake_client.execute_calls[0]
        self.assertFalse(call_info["dry_run"])
        self.assertEqual(call_info["expected_sha256"], "custom-sha")
        self.assertTrue(fake_client.execute_called)
        output = stderr.getvalue()
        self.assertIn("[Remote Agent]", output)
        self.assertIn("云端拒绝了执行请求", output)

    def test_guest_execute_sample_real_action_polls_execution_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "lab.guest-agent-execution-enabled.toml"
            config_path.write_text(
                _guest_agent_execution_enabled_config(),
                encoding="utf-8",
            )
            fake_client = _FakeGuestAgentClient(
                real_execution_state="running",
                execution_status_states=["running", "exited_cleanly"],
            )
            stdout = StringIO()

            with (
                patch(
                    "cloud_av_agent_lab.cli._create_guest_agent_client",
                    return_value=fake_client,
                ),
                patch("cloud_av_agent_lab.cli.time.sleep"),
                redirect_stdout(stdout),
            ):
                exit_code = main(
                    [
                        "guest-execute-sample",
                        "--config",
                        str(config_path),
                        "--vm-id",
                        "win10-tencent-manager",
                        "--sample-id",
                        "case-001",
                        "--case-id",
                        "case-001__tencent-pc-manager",
                        "--real-action",
                        "--poll-interval-seconds",
                        "2",
                        "--poll-timeout-seconds",
                        "6",
                    ]
                )

        self.assertEqual(exit_code, 0)
        self.assertEqual(len(fake_client.execute_calls), 1)
        self.assertFalse(fake_client.execute_calls[0]["dry_run"])
        self.assertEqual(fake_client.execution_status_calls, 2)
        output = stdout.getvalue()
        self.assertIn("[Execution Polling] 检查执行状态", output)
        self.assertIn("state=running", output)
        self.assertIn("state=exited_cleanly", output)
        self.assertIn("root 进程正常退出", output)
        self.assertNotIn("execution_proof", output)

    def test_guest_upload_404_suggests_prepare_case(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "lab.guest-agent-enabled.toml"
            config_path.write_text(_guest_agent_enabled_config(), encoding="utf-8")
            upload_path = Path(tmp) / "eicar.txt"
            upload_path.write_bytes(b"EICAR harmless placeholder")
            fake_client = _FailingGuestAgentClient(
                GuestAgentError(
                    "Guest Agent upload-sample returned HTTP 404 Not Found: "
                    "case workspace does not exist; run guest-prepare-case first",
                    status_code=404,
                )
            )
            stderr = StringIO()

            with (
                patch(
                    "cloud_av_agent_lab.cli._create_guest_agent_client",
                    return_value=fake_client,
                ),
                redirect_stderr(stderr),
                self.assertRaises(SystemExit) as exit_error,
            ):
                main(
                    [
                        "guest-upload-sample",
                        "--config",
                        str(config_path),
                        "--vm-id",
                        "win10-tencent-manager",
                        "--sample-id",
                        "case-001",
                        "--case-id",
                        "missing-case",
                        "--file",
                        str(upload_path),
                    ]
                )

        self.assertEqual(exit_error.exception.code, 2)
        output = stderr.getvalue()
        self.assertIn("HTTP 404 Not Found", output)
        self.assertIn("guest-prepare-case", output)
        self.assertIn("请确认 case_id 是否正确", output)

    def test_guest_upload_removed_after_save_warns_without_failing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "lab.guest-agent-enabled.toml"
            config_path.write_text(_guest_agent_enabled_config(), encoding="utf-8")
            upload_path = Path(tmp) / "eicar.txt"
            upload_path.write_bytes(b"EICAR harmless placeholder")
            fake_client = _FakeGuestAgentClient(
                status_upload_states=["stable", "stable", "removed_after_save"]
            )
            stdout = StringIO()

            with (
                patch(
                    "cloud_av_agent_lab.cli._create_guest_agent_client",
                    return_value=fake_client,
                ),
                patch("cloud_av_agent_lab.cli.time.sleep"),
                redirect_stdout(stdout),
            ):
                exit_code = main(
                    [
                        "guest-upload-sample",
                        "--config",
                        str(config_path),
                        "--vm-id",
                        "win10-tencent-manager",
                        "--sample-id",
                        "case-001",
                        "--case-id",
                        "case-001__tencent-pc-manager",
                        "--file",
                        str(upload_path),
                    ]
                )

        self.assertEqual(exit_code, 0)
        self.assertEqual(fake_client.case_status_calls, 3)
        output = stdout.getvalue()
        self.assertIn('"upload_state": "removed_after_save"', output)
        self.assertIn("拦截成功", output)
        self.assertIn("[Polling] 检查样本状态", output)

    def test_guest_upload_locked_or_busy_warns_without_failing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "lab.guest-agent-enabled.toml"
            config_path.write_text(_guest_agent_enabled_config(), encoding="utf-8")
            upload_path = Path(tmp) / "eicar.txt"
            upload_path.write_bytes(b"EICAR harmless placeholder")
            fake_client = _FakeGuestAgentClient(status_upload_state="locked_or_busy")
            stdout = StringIO()

            with (
                patch(
                    "cloud_av_agent_lab.cli._create_guest_agent_client",
                    return_value=fake_client,
                ),
                patch("cloud_av_agent_lab.cli.time.sleep"),
                redirect_stdout(stdout),
            ):
                exit_code = main(
                    [
                        "guest-upload-sample",
                        "--config",
                        str(config_path),
                        "--vm-id",
                        "win10-tencent-manager",
                        "--sample-id",
                        "case-001",
                        "--case-id",
                        "case-001__tencent-pc-manager",
                        "--file",
                        str(upload_path),
                    ]
                )

        self.assertEqual(exit_code, 0)
        output = stdout.getvalue()
        self.assertIn('"upload_state": "locked_or_busy"', output)
        self.assertIn("warning:", output)
        self.assertIn("guest-case-status", output)


class _FakeGuestAgentClient:
    def __init__(
        self,
        status_upload_state: str = "stable",
        status_upload_states: list[str] | None = None,
        real_execution_state: str = "execution_disabled",
        execution_status_states: list[str] | None = None,
    ) -> None:
        self.status_upload_state = status_upload_state
        self.status_upload_states = list(status_upload_states or [])
        self.real_execution_state = real_execution_state
        self.execution_status_states = list(execution_status_states or [])
        self.upload_calls = 0
        self.case_status_calls = 0
        self.execution_status_calls = 0
        self.execute_calls: list[dict[str, object]] = []
        self.execute_called = False

    def upload_sample(
        self,
        case_id: str,
        sample_id: str,
        file_path: str,
        sha256: str = "",
    ) -> GuestAgentResponse:
        self.upload_calls += 1
        return GuestAgentResponse(
            status="ok",
            message="sample uploaded",
            data={
                "case_id": case_id,
                "sample_id": sample_id,
                "sha256": sha256,
                "upload_state": "uploaded",
                "workspace": "C:\\CloudAvAgentLab\\cases\\case-001",
            },
        )

    def case_status(self, case_id: str) -> GuestAgentResponse:
        self.case_status_calls += 1
        if self.status_upload_states:
            state = self.status_upload_states.pop(0)
        else:
            state = self.status_upload_state
        return GuestAgentResponse(
            status="ok",
            message="case status loaded",
            data={
                "case_id": case_id,
                "state": {"upload_state": state},
            },
        )

    def case_report(self, case_id: str) -> GuestAgentResponse:
        return GuestAgentResponse(
            status="ok",
            message="case report loaded",
            data={
                "case_id": case_id,
                "upload_state": self.status_upload_state,
            },
        )

    def execution_status(
        self,
        case_id: str,
        mark_timeout: bool = False,
        timeout_seconds: float | None = None,
    ) -> GuestAgentResponse:
        self.execution_status_calls += 1
        if mark_timeout:
            state = "timeout_still_running"
        elif self.execution_status_states:
            state = self.execution_status_states.pop(0)
        else:
            state = self.real_execution_state
        return GuestAgentResponse(
            status="ok",
            message="execution status observed",
            data={
                "case_id": case_id,
                "execution_state": state,
                "root_pid": 4321,
                "exit_code": 0 if state == "exited_cleanly" else None,
                "children": [],
                "execution": {
                    "state": state,
                    "root_pid": 4321,
                    "exit_code": 0 if state == "exited_cleanly" else None,
                    "children": [],
                },
            },
        )

    def execute_uploaded_sample(
        self,
        case_id: str,
        sample_id: str,
        expected_sha256: str = "",
        dry_run: bool = True,
    ) -> GuestAgentResponse:
        self.execute_calls.append(
            {
                "case_id": case_id,
                "sample_id": sample_id,
                "expected_sha256": expected_sha256,
                "dry_run": dry_run,
            }
        )
        self.execute_called = not dry_run
        if dry_run:
            return GuestAgentResponse(
                status="ok",
                message=(
                    "dry-run checked uploaded sample metadata; no sample was executed"
                ),
                data={
                    "case_id": case_id,
                    "sample_id": sample_id,
                    "execution_state": "execution_dry_run_checked",
                },
            )
        return GuestAgentResponse(
            status="ok",
            message=(
                "uploaded sample process started"
                if self.real_execution_state != "execution_disabled"
                else "execution is disabled; no sample was executed"
            ),
            data={
                "case_id": case_id,
                "sample_id": sample_id,
                "execution_state": self.real_execution_state,
                "root_pid": 4321
                if self.real_execution_state != "execution_disabled"
                else None,
            },
        )


class _FailingGuestAgentClient:
    def __init__(self, error: GuestAgentError) -> None:
        self.error = error
        self.execute_called = False

    def upload_sample(
        self,
        case_id: str,
        sample_id: str,
        file_path: str,
        sha256: str = "",
    ) -> GuestAgentResponse:
        raise self.error

    def case_status(self, case_id: str) -> GuestAgentResponse:
        raise self.error

    def case_report(self, case_id: str) -> GuestAgentResponse:
        raise self.error

    def execution_status(
        self,
        case_id: str,
        mark_timeout: bool = False,
        timeout_seconds: float | None = None,
    ) -> GuestAgentResponse:
        raise self.error

    def execute_uploaded_sample(
        self,
        case_id: str,
        sample_id: str,
        expected_sha256: str = "",
        dry_run: bool = True,
    ) -> GuestAgentResponse:
        raise self.error


def _guest_agent_enabled_config() -> str:
    config_text = (ROOT / "configs" / "lab.example.toml").read_text(encoding="utf-8")
    marker = "[guest_agent]"
    before, after = config_text.split(marker, maxsplit=1)
    return before + marker + after.replace("enabled = false", "enabled = true", 1)


def _guest_agent_execution_enabled_config() -> str:
    return _guest_agent_enabled_config().replace(
        "[guest_agent.execution]\n"
        "# 受控触发能力默认关闭。本地配置和云端 Guest Agent 都显式开启，并提供\n"
        "# execution token 后，才允许触发当前 case 已登记上传文件。\n"
        "# 不接受任意命令、任意路径或 shell 参数；本地控制面仍不执行样本。\n"
        "enabled = false",
        "[guest_agent.execution]\n"
        "# 受控触发能力默认关闭。本地配置和云端 Guest Agent 都显式开启，并提供\n"
        "# execution token 后，才允许触发当前 case 已登记上传文件。\n"
        "# 不接受任意命令、任意路径或 shell 参数；本地控制面仍不执行样本。\n"
        "enabled = true",
    )
