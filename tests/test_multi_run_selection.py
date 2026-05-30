from __future__ import annotations

import unittest

from cloud_av_agent_lab.orchestration.multi_run import (
    MultiRunSelectionError,
    parse_sample_selection,
)


class MultiRunSelectionParserTests(unittest.TestCase):
    def test_range_uses_closed_interval(self) -> None:
        selection = parse_sample_selection(
            range(1, 12),
            range_text="1-10",
        )

        self.assertEqual(selection.mode, "range")
        self.assertEqual(selection.selected_indexes, tuple(range(1, 11)))
        self.assertEqual(selection.range_text, "1-10")

    def test_indexes_are_sorted_and_deduplicated(self) -> None:
        selection = parse_sample_selection(
            range(1, 10),
            indexes_text="7,3,3,1",
        )

        self.assertEqual(selection.mode, "indexes")
        self.assertEqual(selection.selected_indexes, (1, 3, 7))

    def test_all_selects_available_indexes_only(self) -> None:
        selection = parse_sample_selection(
            (5, 1, 3),
            all_samples=True,
        )

        self.assertEqual(selection.mode, "all")
        self.assertEqual(selection.selected_indexes, (1, 3, 5))

    def test_from_to_uses_closed_interval(self) -> None:
        selection = parse_sample_selection(
            range(1, 6),
            from_index=2,
            to_index=4,
        )

        self.assertEqual(selection.mode, "from_to")
        self.assertEqual(selection.selected_indexes, (2, 3, 4))
        self.assertEqual(selection.from_index, 2)
        self.assertEqual(selection.to_index, 4)

    def test_max_cases_truncates_frozen_selection(self) -> None:
        selection = parse_sample_selection(
            range(1, 10),
            all_samples=True,
            max_cases=3,
        )

        self.assertEqual(selection.selected_indexes, (1, 2, 3))
        self.assertEqual(selection.max_cases, 3)

    def test_max_cases_zero_fails_as_planning_error(self) -> None:
        with self.assertRaisesRegex(
            MultiRunSelectionError,
            "--max-cases must be a positive integer",
        ) as error:
            parse_sample_selection(
                range(1, 10),
                all_samples=True,
                max_cases=0,
            )

        self.assertEqual(error.exception.failure_kind, "planning_or_policy_failure")

    def test_empty_available_indexes_fail_before_batch_plan(self) -> None:
        with self.assertRaisesRegex(MultiRunSelectionError, "no selectable"):
            parse_sample_selection((), all_samples=True)

    def test_range_out_of_bounds_is_planning_failure(self) -> None:
        with self.assertRaisesRegex(
            MultiRunSelectionError,
            "unavailable sample_index",
        ) as error:
            parse_sample_selection(
                (1, 2, 3),
                range_text="1-4",
            )

        self.assertEqual(error.exception.failure_kind, "planning_or_policy_failure")

    def test_missing_index_in_non_contiguous_manifest_fails(self) -> None:
        with self.assertRaisesRegex(MultiRunSelectionError, "sample_index values: 2"):
            parse_sample_selection(
                (1, 3),
                range_text="1-3",
            )

    def test_multiple_selection_modes_fail(self) -> None:
        with self.assertRaisesRegex(MultiRunSelectionError, "exactly one selection"):
            parse_sample_selection(
                range(1, 10),
                all_samples=True,
                range_text="1-3",
            )

    def test_from_to_must_be_paired(self) -> None:
        with self.assertRaisesRegex(MultiRunSelectionError, "--from and --to"):
            parse_sample_selection(range(1, 10), from_index=2)

    def test_indexes_reject_non_integer_input(self) -> None:
        with self.assertRaisesRegex(MultiRunSelectionError, "integer indexes"):
            parse_sample_selection(range(1, 10), indexes_text="1,two")


if __name__ == "__main__":
    unittest.main()
