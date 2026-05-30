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
from cloud_av_agent_lab.orchestration import SingleRunResult


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

    def test_guest_worker_status_exits_when_desktop_worker_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "lab.guest-agent-enabled.toml"
            config_path.write_text(_guest_agent_enabled_config(), encoding="utf-8")
            stderr = StringIO()

            with redirect_stderr(stderr), self.assertRaises(SystemExit) as exit_error:
                main(
                    [
                        "guest-worker-status",
                        "--config",
                        str(config_path),
                        "--vm-id",
                        "win10-huorong",
                    ]
                )

        self.assertEqual(exit_error.exception.code, 2)
        self.assertIn("Desktop Worker", stderr.getvalue())

    def test_guest_worker_status_prints_status_when_enabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "lab.desktop-worker-enabled.toml"
            config_path.write_text(
                _guest_agent_desktop_worker_enabled_config(),
                encoding="utf-8",
            )
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
                        "guest-worker-status",
                        "--config",
                        str(config_path),
                        "--vm-id",
                        "win10-huorong",
                    ]
                )

        self.assertEqual(exit_code, 0)
        self.assertIn('"desktop_worker_ready": true', stdout.getvalue())

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

    def test_guest_case_summary_exits_clearly_when_guest_agent_disabled(self) -> None:
        stderr = StringIO()

        with redirect_stderr(stderr), self.assertRaises(SystemExit) as exit_error:
            main(
                [
                    "guest-case-summary",
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

    def test_guest_export_evidence_exits_clearly_when_guest_agent_disabled(
        self,
    ) -> None:
        stderr = StringIO()

        with tempfile.TemporaryDirectory() as tmp:
            with redirect_stderr(stderr), self.assertRaises(SystemExit) as exit_error:
                main(
                    [
                        "guest-export-evidence",
                        "--config",
                        str(ROOT / "configs" / "lab.example.toml"),
                        "--vm-id",
                        "win10-tencent-manager",
                        "--case-id",
                        "case-001__tencent-pc-manager",
                        "--output",
                        str(Path(tmp) / "bundle.zip"),
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

    def test_guest_collect_logs_exits_clearly_when_guest_agent_disabled(self) -> None:
        stderr = StringIO()

        with redirect_stderr(stderr), self.assertRaises(SystemExit) as exit_error:
            main(
                [
                    "guest-collect-logs",
                    "--config",
                    str(ROOT / "configs" / "lab.example.toml"),
                    "--vm-id",
                    "win10-huorong",
                    "--case-id",
                    "case-001__huorong",
                    "--product",
                    "huorong",
                ]
            )

        self.assertEqual(exit_error.exception.code, 2)
        self.assertIn("[Local Check]", stderr.getvalue())

    def test_guest_check_security_product_readiness_disabled_is_clear(self) -> None:
        stderr = StringIO()

        with redirect_stderr(stderr), self.assertRaises(SystemExit) as exit_error:
            main(
                [
                    "guest-check-security-product-readiness",
                    "--config",
                    str(ROOT / "configs" / "lab.example.toml"),
                    "--vm-id",
                    "win10-huorong",
                    "--case-id",
                    "case-001__huorong",
                    "--product",
                    "huorong",
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

    def test_guest_collect_logs_calls_collect_method(self) -> None:
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
                        "guest-collect-logs",
                        "--config",
                        str(config_path),
                        "--vm-id",
                        "win10-huorong",
                        "--case-id",
                        "case-001__huorong",
                        "--product",
                        "huorong",
                    ]
                )

        self.assertEqual(exit_code, 0)
        self.assertEqual(fake_client.collect_calls, 1)
        self.assertEqual(fake_client.collect_products, ["huorong"])
        self.assertIn('"message": "collection completed"', stdout.getvalue())
        self.assertIn('"verdict": "intercepted"', stdout.getvalue())

    def test_guest_prepare_case_resolves_windows_defender_product(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "lab.guest-agent-enabled.toml"
            config_path.write_text(_guest_agent_enabled_config(), encoding="utf-8")
            fake_client = _FakeGuestAgentClient()

            with (
                patch(
                    "cloud_av_agent_lab.cli._create_guest_agent_client",
                    return_value=fake_client,
                ),
                redirect_stdout(StringIO()),
            ):
                exit_code = main(
                    [
                        "guest-prepare-case",
                        "--config",
                        str(config_path),
                        "--vm-id",
                        "win10-windows-defender",
                        "--sample-id",
                        "case-001",
                        "--product",
                        "windows-defender",
                    ]
                )

        self.assertEqual(exit_code, 0)
        self.assertEqual(fake_client.prepare_products, ["windows-defender"])

    def test_guest_prepare_case_resolves_qihoo_360_product(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "lab.guest-agent-enabled.toml"
            config_path.write_text(_guest_agent_enabled_config(), encoding="utf-8")
            fake_client = _FakeGuestAgentClient()

            with (
                patch(
                    "cloud_av_agent_lab.cli._create_guest_agent_client",
                    return_value=fake_client,
                ),
                redirect_stdout(StringIO()),
            ):
                exit_code = main(
                    [
                        "guest-prepare-case",
                        "--config",
                        str(config_path),
                        "--vm-id",
                        "win10-qihoo-360",
                        "--sample-id",
                        "case-001",
                        "--product",
                        "qihoo-360",
                    ]
                )

        self.assertEqual(exit_code, 0)
        self.assertEqual(fake_client.prepare_products, ["qihoo-360"])

    def test_guest_collect_logs_resolves_windows_defender_product(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "lab.guest-agent-enabled.toml"
            config_path.write_text(_guest_agent_enabled_config(), encoding="utf-8")
            fake_client = _FakeGuestAgentClient()

            with (
                patch(
                    "cloud_av_agent_lab.cli._create_guest_agent_client",
                    return_value=fake_client,
                ),
                redirect_stdout(StringIO()),
            ):
                exit_code = main(
                    [
                        "guest-collect-logs",
                        "--config",
                        str(config_path),
                        "--vm-id",
                        "win10-windows-defender",
                        "--case-id",
                        "case-001__windows-defender",
                        "--product",
                        "windows-defender",
                    ]
                )

        self.assertEqual(exit_code, 0)
        self.assertEqual(fake_client.collect_products, ["windows-defender"])

    def test_guest_collect_logs_resolves_qihoo_360_product(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "lab.guest-agent-enabled.toml"
            config_path.write_text(_guest_agent_enabled_config(), encoding="utf-8")
            fake_client = _FakeGuestAgentClient()

            with (
                patch(
                    "cloud_av_agent_lab.cli._create_guest_agent_client",
                    return_value=fake_client,
                ),
                redirect_stdout(StringIO()),
            ):
                exit_code = main(
                    [
                        "guest-collect-logs",
                        "--config",
                        str(config_path),
                        "--vm-id",
                        "win10-qihoo-360",
                        "--case-id",
                        "case-001__qihoo-360",
                        "--product",
                        "qihoo-360",
                    ]
                )

        self.assertEqual(exit_code, 0)
        self.assertEqual(fake_client.collect_products, ["qihoo-360"])

    def test_guest_collect_logs_requires_explicit_product(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "lab.guest-agent-enabled.toml"
            config_path.write_text(_guest_agent_enabled_config(), encoding="utf-8")
            stderr = StringIO()

            with redirect_stderr(stderr), self.assertRaises(SystemExit) as exit_error:
                main(
                    [
                        "guest-collect-logs",
                        "--config",
                        str(config_path),
                        "--vm-id",
                        "win10-huorong",
                        "--case-id",
                        "case-001__huorong",
                    ]
                )

        self.assertEqual(exit_error.exception.code, 2)
        self.assertIn("--product", stderr.getvalue())

    def test_guest_collect_logs_rejects_conflicting_explicit_product(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "lab.guest-agent-enabled.toml"
            config_path.write_text(_guest_agent_enabled_config(), encoding="utf-8")
            stderr = StringIO()

            with redirect_stderr(stderr), self.assertRaises(SystemExit) as exit_error:
                main(
                    [
                        "guest-collect-logs",
                        "--config",
                        str(config_path),
                        "--vm-id",
                        "win10-huorong",
                        "--case-id",
                        "case-001__huorong",
                        "--product",
                        "windows-defender",
                    ]
                )

        self.assertEqual(exit_error.exception.code, 2)
        self.assertIn("does not match", stderr.getvalue())

    def test_guest_collect_logs_rejects_disabled_product(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "lab.disabled-product.toml"
            config_path.write_text(
                _guest_agent_enabled_config().replace(
                    'id = "windows-defender"\nenabled = true',
                    'id = "windows-defender"\nenabled = false',
                ),
                encoding="utf-8",
            )
            stderr = StringIO()

            with redirect_stderr(stderr), self.assertRaises(SystemExit) as exit_error:
                main(
                    [
                        "guest-collect-logs",
                        "--config",
                        str(config_path),
                        "--vm-id",
                        "win10-windows-defender",
                        "--case-id",
                        "case-001__windows-defender",
                        "--product",
                        "windows-defender",
                    ]
                )

        self.assertEqual(exit_error.exception.code, 2)
        self.assertIn("disabled", stderr.getvalue())

    def test_single_run_rejects_unknown_product_locally(self) -> None:
        stderr = StringIO()

        with redirect_stderr(stderr), self.assertRaises(SystemExit) as exit_error:
            main(["single-run", "--product", "unknown-product", "--dry-run"])

        self.assertEqual(exit_error.exception.code, 2)
        self.assertIn("invalid choice", stderr.getvalue())

    def test_guest_check_security_product_readiness_prints_concise_status(
        self,
    ) -> None:
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
                        "guest-check-security-product-readiness",
                        "--config",
                        str(config_path),
                        "--vm-id",
                        "win10-huorong",
                        "--case-id",
                        "case-001__huorong",
                        "--product",
                        "huorong",
                    ]
                )

        self.assertEqual(exit_code, 0)
        self.assertEqual(fake_client.readiness_calls, 1)
        self.assertEqual(fake_client.readiness_products, ["huorong"])
        output = stdout.getvalue()
        self.assertIn("Security product readiness:", output)
        self.assertIn("state: ready", output)
        self.assertIn("[ok] huorong_log_db_exists", output)

    def test_guest_check_readiness_resolves_windows_defender_product(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "lab.guest-agent-enabled.toml"
            config_path.write_text(_guest_agent_enabled_config(), encoding="utf-8")
            fake_client = _FakeGuestAgentClient()

            with (
                patch(
                    "cloud_av_agent_lab.cli._create_guest_agent_client",
                    return_value=fake_client,
                ),
                redirect_stdout(StringIO()),
            ):
                exit_code = main(
                    [
                        "guest-check-security-product-readiness",
                        "--config",
                        str(config_path),
                        "--vm-id",
                        "win10-windows-defender",
                        "--case-id",
                        "case-001__windows-defender",
                        "--product",
                        "windows-defender",
                    ]
                )

        self.assertEqual(exit_code, 0)
        self.assertEqual(fake_client.readiness_products, ["windows-defender"])

    def test_guest_check_readiness_resolves_qihoo_360_product(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "lab.guest-agent-enabled.toml"
            config_path.write_text(_guest_agent_enabled_config(), encoding="utf-8")
            fake_client = _FakeGuestAgentClient()

            with (
                patch(
                    "cloud_av_agent_lab.cli._create_guest_agent_client",
                    return_value=fake_client,
                ),
                redirect_stdout(StringIO()),
            ):
                exit_code = main(
                    [
                        "guest-check-security-product-readiness",
                        "--config",
                        str(config_path),
                        "--vm-id",
                        "win10-qihoo-360",
                        "--case-id",
                        "case-001__qihoo-360",
                        "--product",
                        "qihoo-360",
                    ]
                )

        self.assertEqual(exit_code, 0)
        self.assertEqual(fake_client.readiness_products, ["qihoo-360"])

    def test_guest_readiness_status_passes_explicit_product(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "lab.guest-agent-enabled.toml"
            config_path.write_text(_guest_agent_enabled_config(), encoding="utf-8")
            fake_client = _FakeGuestAgentClient()

            with (
                patch(
                    "cloud_av_agent_lab.cli._create_guest_agent_client",
                    return_value=fake_client,
                ),
                redirect_stdout(StringIO()),
            ):
                exit_code = main(
                    [
                        "guest-security-product-readiness-status",
                        "--config",
                        str(config_path),
                        "--vm-id",
                        "win10-windows-defender",
                        "--case-id",
                        "case-001__windows-defender",
                        "--product",
                        "windows-defender",
                    ]
                )

        self.assertEqual(exit_code, 0)
        self.assertEqual(fake_client.readiness_status_products, ["windows-defender"])

    def test_guest_readiness_status_resolves_qihoo_360_product(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "lab.guest-agent-enabled.toml"
            config_path.write_text(_guest_agent_enabled_config(), encoding="utf-8")
            fake_client = _FakeGuestAgentClient()

            with (
                patch(
                    "cloud_av_agent_lab.cli._create_guest_agent_client",
                    return_value=fake_client,
                ),
                redirect_stdout(StringIO()),
            ):
                exit_code = main(
                    [
                        "guest-security-product-readiness-status",
                        "--config",
                        str(config_path),
                        "--vm-id",
                        "win10-qihoo-360",
                        "--case-id",
                        "case-001__qihoo-360",
                        "--product",
                        "qihoo-360",
                    ]
                )

        self.assertEqual(exit_code, 0)
        self.assertEqual(fake_client.readiness_status_products, ["qihoo-360"])

    def test_guest_case_summary_outputs_verdict_and_summary(self) -> None:
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
                        "guest-case-summary",
                        "--config",
                        str(config_path),
                        "--vm-id",
                        "win10-huorong",
                        "--case-id",
                        "case-001__huorong",
                    ]
                )

        self.assertEqual(exit_code, 0)
        output = stdout.getvalue()
        self.assertIn("Verdict: detected_or_blocked", output)
        self.assertIn("Huorong collector", output)
        self.assertNotIn("Timeline:", output)
        self.assertNotIn('"status": "ok"', output)

    def test_guest_case_summary_json_outputs_full_response(self) -> None:
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
                        "guest-case-summary",
                        "--config",
                        str(config_path),
                        "--vm-id",
                        "win10-huorong",
                        "--case-id",
                        "case-001__huorong",
                        "--json",
                    ]
                )

        self.assertEqual(exit_code, 0)
        output = stdout.getvalue()
        self.assertIn('"status": "ok"', output)
        self.assertIn('"timeline"', output)
        self.assertNotIn("Key Reasons:", output)

    def test_guest_export_evidence_saves_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "lab.guest-agent-enabled.toml"
            config_path.write_text(_guest_agent_enabled_config(), encoding="utf-8")
            output_path = Path(tmp) / "case_evidence.zip"
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
                        "guest-export-evidence",
                        "--config",
                        str(config_path),
                        "--vm-id",
                        "win10-huorong",
                        "--case-id",
                        "case-001__huorong",
                        "--output",
                        str(output_path),
                    ]
                )

            self.assertEqual(exit_code, 0)
            self.assertTrue(output_path.is_file())
            self.assertEqual(fake_client.export_calls, 1)
            output = stdout.getvalue()
            self.assertIn("Evidence bundle saved to:", output)
            self.assertIn("Exported redacted guest-reported evidence bundle:", output)
            self.assertIn("raw_binary_included: False", output)
            self.assertIn("trust_model: dirty_instance_untrusted", output)

    def test_single_run_invokes_orchestration_and_prints_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sample_path = root / "eicar.txt"
            sample_path.write_text("harmless placeholder", encoding="utf-8")
            result = SingleRunResult(
                run_id="run-001",
                case_id="eicar__huorong__run-001",
                run_dir=root / "runs" / "run-001",
                run_state_path=root / "runs" / "run-001" / "run_state.json",
                generated_config_path=root / "runs" / "run-001" / "lab.generated.toml",
                summary_path=root / "runs" / "run-001" / "case_summary.json",
                evidence_bundle_path=root / "runs" / "run-001" / "case_evidence.zip",
                verdict="detected_or_blocked",
                confidence="high",
                final_status="completed",
                cleanup_status="dry_run",
                emergency_poweroff_status="not_needed",
            )
            stdout = StringIO()

            with (
                patch(
                    "cloud_av_agent_lab.cli.run_single_case", return_value=result
                ) as run_single,
                patch(
                    "cloud_av_agent_lab.cli._confirm_single_run_real_operation"
                ) as confirm_real,
                redirect_stdout(stdout),
            ):
                exit_code = main(
                    [
                        "single-run",
                        "--instance-id",
                        "lhins-example",
                        "--snapshot-id",
                        "lhsnap-example",
                        "--region",
                        "ap-singapore",
                        "--sample-name",
                        "eicar",
                        "--sample-path",
                        str(sample_path),
                        "--product",
                        "huorong",
                        "--guest-agent-url",
                        "http://127.0.0.1:8080",
                        "--runs-dir",
                        str(root / "runs"),
                    ]
                )

        self.assertEqual(exit_code, 0)
        run_single.assert_called_once()
        confirm_real.assert_called_once()
        options = run_single.call_args.args[0]
        self.assertEqual(options.instance_id, "lhins-example")
        self.assertFalse(options.dry_run)
        output = stdout.getvalue()
        self.assertIn("Single-run finished: completed", output)
        self.assertIn("Verdict: detected_or_blocked", output)

    def test_single_run_dry_run_skips_real_operation_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sample_path = root / "eicar.txt"
            sample_path.write_text("harmless placeholder", encoding="utf-8")
            result = SingleRunResult(
                run_id="run-001",
                case_id="eicar__huorong__run-001",
                run_dir=root / "runs" / "run-001",
                run_state_path=root / "runs" / "run-001" / "run_state.json",
                generated_config_path=root / "runs" / "run-001" / "lab.generated.toml",
                summary_path=root / "runs" / "run-001" / "case_summary.json",
                evidence_bundle_path=root / "runs" / "run-001" / "case_evidence.zip",
                verdict="",
                confidence="",
                final_status="completed",
                cleanup_status="dry_run",
                emergency_poweroff_status="not_needed",
            )

            with (
                patch(
                    "cloud_av_agent_lab.cli.run_single_case", return_value=result
                ) as run_single,
                patch(
                    "cloud_av_agent_lab.cli._confirm_single_run_real_operation"
                ) as confirm_real,
                redirect_stdout(StringIO()),
            ):
                exit_code = main(
                    [
                        "single-run",
                        "--dry-run",
                        "--instance-id",
                        "lhins-example",
                        "--snapshot-id",
                        "lhsnap-example",
                        "--region",
                        "ap-singapore",
                        "--sample-name",
                        "eicar",
                        "--sample-path",
                        str(sample_path),
                        "--product",
                        "huorong",
                        "--guest-agent-url",
                        "http://127.0.0.1:8080",
                        "--runs-dir",
                        str(root / "runs"),
                    ]
                )

        self.assertEqual(exit_code, 0)
        confirm_real.assert_not_called()
        self.assertTrue(run_single.call_args.args[0].dry_run)

    def test_single_run_accepts_qihoo_360_product(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sample_path = root / "eicar.txt"
            sample_path.write_text("harmless placeholder", encoding="utf-8")
            result = SingleRunResult(
                run_id="run-001",
                case_id="eicar__qihoo-360__run-001",
                run_dir=root / "runs" / "run-001",
                run_state_path=root / "runs" / "run-001" / "run_state.json",
                generated_config_path=root / "runs" / "run-001" / "lab.generated.toml",
                summary_path=root / "runs" / "run-001" / "case_summary.json",
                evidence_bundle_path=root / "runs" / "run-001" / "case_evidence.zip",
                verdict="",
                confidence="",
                final_status="completed",
                cleanup_status="dry_run",
                emergency_poweroff_status="not_needed",
            )

            with (
                patch(
                    "cloud_av_agent_lab.cli.run_single_case", return_value=result
                ) as run_single,
                patch(
                    "cloud_av_agent_lab.cli._confirm_single_run_real_operation"
                ) as confirm_real,
                redirect_stdout(StringIO()),
            ):
                exit_code = main(
                    [
                        "single-run",
                        "--dry-run",
                        "--instance-id",
                        "lhins-example",
                        "--snapshot-id",
                        "lhsnap-example",
                        "--region",
                        "ap-singapore",
                        "--sample-name",
                        "eicar",
                        "--sample-path",
                        str(sample_path),
                        "--product",
                        "qihoo-360",
                        "--guest-agent-url",
                        "http://127.0.0.1:8080",
                        "--runs-dir",
                        str(root / "runs"),
                    ]
                )

        self.assertEqual(exit_code, 0)
        confirm_real.assert_not_called()
        self.assertEqual(run_single.call_args.args[0].product_id, "qihoo-360")

    def test_single_run_prompts_product_before_generated_config_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sample_path = root / "eicar.txt"
            sample_path.write_text("harmless placeholder", encoding="utf-8")
            result = SingleRunResult(
                run_id="run-001",
                case_id="eicar__windows-defender__run-001",
                run_dir=root / "runs" / "run-001",
                run_state_path=root / "runs" / "run-001" / "run_state.json",
                generated_config_path=root / "runs" / "run-001" / "lab.generated.toml",
                summary_path=root / "runs" / "run-001" / "case_summary.json",
                evidence_bundle_path=root / "runs" / "run-001" / "case_evidence.zip",
                verdict="",
                confidence="",
                final_status="completed",
                cleanup_status="dry_run",
                emergency_poweroff_status="not_needed",
            )

            with (
                patch(
                    "cloud_av_agent_lab.cli.run_single_case", return_value=result
                ) as run_single,
                patch.object(sys.stdin, "isatty", return_value=True),
                patch(
                    "builtins.input",
                    side_effect=[
                        "windows-defender",
                        "lhins-example",
                        "lhsnap-example",
                        "ap-singapore",
                        "eicar",
                        str(sample_path),
                        "http://127.0.0.1:8080",
                    ],
                ) as input_mock,
                redirect_stdout(StringIO()),
            ):
                exit_code = main(
                    [
                        "single-run",
                        "--dry-run",
                        "--runs-dir",
                        str(root / "runs"),
                    ]
                )

        self.assertEqual(exit_code, 0)
        self.assertEqual(
            input_mock.call_args_list[0].args[0],
            "Security product id [huorong]: ",
        )
        self.assertEqual(run_single.call_args.args[0].product_id, "windows-defender")

    def test_single_run_missing_input_exits_clearly(self) -> None:
        stderr = StringIO()
        with redirect_stderr(stderr), self.assertRaises(SystemExit) as exit_error:
            main(["single-run"])

        self.assertEqual(exit_error.exception.code, 2)
        self.assertIn("[Local Check]", stderr.getvalue())

    def test_guest_collect_logs_404_suggests_prepare_case(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "lab.guest-agent-enabled.toml"
            config_path.write_text(_guest_agent_enabled_config(), encoding="utf-8")
            fake_client = _FailingGuestAgentClient(
                GuestAgentError(
                    "Guest Agent cases/missing-case/collection/huorong returned "
                    "HTTP 404 Not Found: case workspace does not exist; run "
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
                        "guest-collect-logs",
                        "--config",
                        str(config_path),
                        "--vm-id",
                        "win10-huorong",
                        "--case-id",
                        "missing-case",
                        "--product",
                        "huorong",
                    ]
                )

        self.assertEqual(exit_error.exception.code, 2)
        output = stderr.getvalue()
        self.assertIn("HTTP 404 Not Found", output)
        self.assertIn("guest-prepare-case", output)
        self.assertIn("请确认 case_id 是否正确", output)

    def test_guest_case_summary_404_suggests_prepare_case(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "lab.guest-agent-enabled.toml"
            config_path.write_text(_guest_agent_enabled_config(), encoding="utf-8")
            fake_client = _FailingGuestAgentClient(
                GuestAgentError(
                    "Guest Agent cases/missing-case/summary returned HTTP 404 "
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
                        "guest-case-summary",
                        "--config",
                        str(config_path),
                        "--vm-id",
                        "win10-huorong",
                        "--case-id",
                        "missing-case",
                    ]
                )

        self.assertEqual(exit_error.exception.code, 2)
        output = stderr.getvalue()
        self.assertIn("HTTP 404 Not Found", output)
        self.assertIn("guest-prepare-case", output)
        self.assertIn("请确认 case_id 是否正确", output)

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
                        "--md5",
                        "1" * 32,
                    ]
                )

        self.assertEqual(exit_code, 0)
        self.assertEqual(fake_client.upload_calls, 1)
        self.assertEqual(fake_client.upload_md5s, ["1" * 32])
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
        self.prepare_products: list[str] = []
        self.case_status_calls = 0
        self.collect_calls = 0
        self.collect_products: list[str] = []
        self.summary_calls = 0
        self.export_calls = 0
        self.execution_status_calls = 0
        self.readiness_calls = 0
        self.readiness_products: list[str] = []
        self.readiness_status_calls = 0
        self.readiness_status_products: list[str] = []
        self.execute_calls: list[dict[str, object]] = []
        self.execute_called = False
        self.upload_md5s: list[str] = []

    def upload_sample(
        self,
        case_id: str,
        sample_id: str,
        file_path: str,
        sha256: str = "",
        md5: str = "",
    ) -> GuestAgentResponse:
        self.upload_calls += 1
        self.upload_md5s.append(md5)
        return GuestAgentResponse(
            status="ok",
            message="sample uploaded",
            data={
                "case_id": case_id,
                "sample_id": sample_id,
                "sha256": sha256,
                "md5": md5,
                "upload_state": "uploaded",
                "workspace": "C:\\CloudAvAgentLab\\cases\\case-001",
            },
        )

    def prepare_case(self, case: object) -> GuestAgentResponse:
        self.prepare_products.append(case.product.id)
        return GuestAgentResponse(
            status="ok",
            message="case workspace prepared",
            data={"case_id": case.id},
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

    def case_summary(self, case_id: str) -> GuestAgentResponse:
        self.summary_calls += 1
        return GuestAgentResponse(
            status="ok",
            message="case summary loaded",
            data={
                "case_id": case_id,
                "sample_id": "case-001",
                "product_id": "huorong",
                "verdict": "detected_or_blocked",
                "confidence": "high",
                "summary": "Huorong collector 找到了明确拦截证据。",
                "reasons": [
                    "collection evidence_count>0",
                    "product log evidence indicates detection or blocking",
                ],
                "timeline": [
                    {
                        "timestamp_utc": "2026-05-16T00:00:00Z",
                        "source": "product_log",
                        "event_type": "av_quarantined",
                        "message": "Huorong collector matched this case",
                    }
                ],
            },
        )

    def export_evidence_bundle(
        self,
        case_id: str,
        output_path: str | Path,
    ) -> GuestAgentResponse:
        self.export_calls += 1
        destination = Path(output_path)
        destination.write_bytes(b"fake-zip")
        return GuestAgentResponse(
            status="ok",
            message="evidence bundle saved",
            data={
                "case_id": case_id,
                "output_path": str(destination),
                "size": len(b"fake-zip"),
                "sha256": "0" * 64,
                "trust_model": "dirty_instance_untrusted",
                "forensic_grade": False,
                "raw_binary_included": False,
            },
        )

    def collect_logs(self, case_id: str, product_id: str) -> GuestAgentResponse:
        self.collect_calls += 1
        self.collect_products.append(product_id)
        return GuestAgentResponse(
            status="ok",
            message="collection completed",
            data={
                "case_id": case_id,
                "product_id": product_id,
                "collection_state": "collected",
                "verdict": "intercepted",
                "intercepted": True,
                "evidence_count": 1,
            },
        )

    def collection_status(self, case_id: str) -> GuestAgentResponse:
        return GuestAgentResponse(
            status="ok",
            message="collection status loaded",
            data={
                "case_id": case_id,
                "collection_state": "collected",
            },
        )

    def check_security_product_readiness(
        self,
        case_id: str,
        product_id: str,
    ) -> GuestAgentResponse:
        self.readiness_calls += 1
        self.readiness_products.append(product_id)
        return GuestAgentResponse(
            status="ok",
            message="security product readiness checked",
            data={
                "case_id": case_id,
                "product_id": product_id,
                "state": "ready",
                "confidence": "medium",
                "scope": "log_observability",
                "protection_state": "unknown",
                "checks": [
                    {
                        "name": "huorong_log_db_exists",
                        "status": "ok",
                        "message": "Huorong log database exists",
                        "data": {"filename": "log.db"},
                    }
                ],
                "warnings": [],
                "errors": [],
            },
        )

    def security_product_readiness_status(
        self,
        case_id: str,
        product_id: str = "",
    ) -> GuestAgentResponse:
        self.readiness_status_calls += 1
        self.readiness_status_products.append(product_id)
        return GuestAgentResponse(
            status="ok",
            message="security product readiness status loaded",
            data={
                "case_id": case_id,
                "product_id": "huorong",
                "state": "ready",
                "confidence": "medium",
                "checks": [],
                "warnings": [],
                "errors": [],
            },
        )

    def worker_status(self) -> GuestAgentResponse:
        return GuestAgentResponse(
            status="ok",
            message="desktop worker status loaded",
            data={
                "desktop_worker_ready": True,
                "desktop_session_ready": True,
                "worker_session_id": 1,
                "desktop_session_state": "active",
                "username": "AvTester-Admin",
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
        run_id: str = "",
    ) -> GuestAgentResponse:
        self.execute_calls.append(
            {
                "case_id": case_id,
                "sample_id": sample_id,
                "expected_sha256": expected_sha256,
                "dry_run": dry_run,
                "run_id": run_id,
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
        md5: str = "",
    ) -> GuestAgentResponse:
        raise self.error

    def case_status(self, case_id: str) -> GuestAgentResponse:
        raise self.error

    def case_report(self, case_id: str) -> GuestAgentResponse:
        raise self.error

    def case_summary(self, case_id: str) -> GuestAgentResponse:
        raise self.error

    def export_evidence_bundle(
        self,
        case_id: str,
        output_path: str | Path,
    ) -> GuestAgentResponse:
        raise self.error

    def collect_logs(self, case_id: str, product_id: str) -> GuestAgentResponse:
        raise self.error

    def collection_status(self, case_id: str) -> GuestAgentResponse:
        raise self.error

    def check_security_product_readiness(
        self,
        case_id: str,
        product_id: str,
    ) -> GuestAgentResponse:
        raise self.error

    def security_product_readiness_status(
        self,
        case_id: str,
        product_id: str = "",
    ) -> GuestAgentResponse:
        raise self.error

    def worker_status(self) -> GuestAgentResponse:
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
        run_id: str = "",
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


def _guest_agent_desktop_worker_enabled_config() -> str:
    return _guest_agent_enabled_config().replace(
        "[guest_agent.desktop_worker]\n"
        "# Desktop Worker 运行在云端 Windows 交互式桌面 session 中，只监听\n"
        "# 127.0.0.1。Control Agent 通过本机 HTTP 查询 Worker ready 状态；后续真实\n"
        "# 执行会下放给 Worker，避免 Session 0 直接启动样本。默认关闭，直到云端\n"
        "# baseline snapshot 已固化自动登录和 Worker 自启动。\n"
        "enabled = false",
        "[guest_agent.desktop_worker]\n"
        "# Desktop Worker 运行在云端 Windows 交互式桌面 session 中，只监听\n"
        "# 127.0.0.1。Control Agent 通过本机 HTTP 查询 Worker ready 状态；后续真实\n"
        "# 执行会下放给 Worker，避免 Session 0 直接启动样本。默认关闭，直到云端\n"
        "# baseline snapshot 已固化自动登录和 Worker 自启动。\n"
        "enabled = true",
    )
