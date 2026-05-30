from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from cloud_av_agent_lab.orchestration.multi_run import (
    CaseState,
    MultiRunState,
    MultiRunStateError,
    append_next_multi_run_event,
    read_multi_run_events,
    read_multi_run_state_payload,
    write_multi_run_state,
)


class MultiRunStateWriterTests(unittest.TestCase):
    def test_state_write_is_json_and_leaves_no_tmp_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "multi_run_state.json"
            state = MultiRunState(
                batch_id="batch-001",
                batch_state="created",
                product_id="huorong",
                instance_id="lhins-test",
                snapshot_id="lhsnap-test",
                region="ap-singapore",
                sample_manifest_path="sample_manifest.jsonl",
                manifest_sha256="a" * 64,
                batch_plan_sha256="b" * 64,
                selected_indexes=(1,),
                cases=(
                    CaseState(
                        sample_index=1,
                        sample_id="sample-001",
                        case_id="0001_sample-001__huorong",
                    ),
                ),
            )

            write_multi_run_state(state_path, state)

            payload = read_multi_run_state_payload(state_path)
            self.assertEqual(payload["schema_version"], "multi-run-state.v1")
            self.assertEqual(payload["sample_manifest_path"], "sample_manifest.jsonl")
            self.assertEqual(payload["cases"][0]["sample_index"], 1)
            self.assertFalse((Path(tmp) / ".multi_run_state.json.tmp").exists())

    def test_state_corruption_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "multi_run_state.json"
            state_path.write_text("{not-json", encoding="utf-8")

            with self.assertRaises(MultiRunStateError) as error:
                read_multi_run_state_payload(state_path)

            self.assertIn("invalid multi_run_state.json", str(error.exception))

    def test_event_log_appends_incrementing_seq(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            event_path = Path(tmp) / "multi_run_events.jsonl"

            first = append_next_multi_run_event(
                event_path,
                batch_id="batch-001",
                event_type="batch_created",
            )
            second = append_next_multi_run_event(
                event_path,
                batch_id="batch-001",
                event_type="plan_created",
                data={"selected_indexes": (1, 2)},
            )

            self.assertEqual(first.seq, 1)
            self.assertEqual(second.seq, 2)
            events = read_multi_run_events(event_path)
            self.assertEqual([event["seq"] for event in events], [1, 2])
            self.assertEqual(events[1]["data"]["selected_indexes"], [1, 2])
            json.dumps(events)

    def test_event_log_corruption_is_reported_with_line_number(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            event_path = Path(tmp) / "multi_run_events.jsonl"
            event_path.write_text(
                '{"schema_version":"multi-run-event.v1","seq":1}\n{bad-json\n',
                encoding="utf-8",
            )

            with self.assertRaises(MultiRunStateError) as error:
                read_multi_run_events(event_path)

            self.assertIn("line 2", str(error.exception))


if __name__ == "__main__":
    unittest.main()
