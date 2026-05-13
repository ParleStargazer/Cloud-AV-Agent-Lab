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
        self.assertIn("guest_agent is disabled", stderr.getvalue())

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
        self.assertIn("guest_agent is disabled", stderr.getvalue())

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
        self.assertIn("guest_agent is disabled", stderr.getvalue())

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
    ) -> None:
        self.status_upload_state = status_upload_state
        self.status_upload_states = list(status_upload_states or [])
        self.upload_calls = 0
        self.case_status_calls = 0
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


def _guest_agent_enabled_config() -> str:
    config_text = (ROOT / "configs" / "lab.example.toml").read_text(encoding="utf-8")
    marker = "[guest_agent]"
    before, after = config_text.split(marker, maxsplit=1)
    return before + marker + after.replace("enabled = false", "enabled = true", 1)
