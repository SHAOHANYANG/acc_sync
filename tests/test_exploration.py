"""Invented fixtures only: duration logic, missingness, labels, and local IO."""

import contextlib
import io
import json
import math
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from sync_eval.cli import analyze, main
from sync_eval.core import window_average
from sync_eval.exploration import (ReferenceInterval, compare_reference, detect_dual,
    detect_runs, explore, exploration_demo, intersections, load_references, point_biserial)
from sync_eval.io import write_csv


def fake_windows(values, width=2):
    # Each synthetic value occupies one complete output window, 0.5s lattice.
    n = int(width / .5)
    frames = [value for value in values for _ in range(n)]
    return window_average([i * .5 for i in range(len(frames))], frames, width)


class DurationTests(unittest.TestCase):
    def test_join_before_duration_filter_and_inclusive_threshold(self):
        rows = fake_windows([1, 6, 6, 1, 6])
        events = detect_runs(rows, 6, 4)
        self.assertEqual([(e["start"], e["end"]) for e in events], [(2, 6)])
        self.assertEqual(events[0]["n_windows"], 2)
        self.assertEqual(events[0]["mean_window_energy"], 6)

    def test_unknown_and_low_windows_split_events(self):
        for gap in (None, 1):
            rows = fake_windows([8, gap, 8])
            self.assertEqual(len(detect_runs(rows, 6, 2)), 2)
            self.assertEqual(detect_runs(rows, 6, 4), [])

    def test_dual_rule_retains_weaker_longer_and_stronger_shorter(self):
        rows = fake_windows([1, 8, 8, 1, 3, 3, 3, 3, 1, 3])
        single = detect_runs(rows, 6, 4)
        dual = detect_dual(rows, 2, 6, 4, 8)
        self.assertEqual([(e["start"], e["end"]) for e in single], [(2, 6)])
        self.assertEqual([(e["start"], e["end"]) for e in dual], [(2, 6), (8, 16)])
        self.assertEqual(dual[1]["rule_branch"], "lower_long")

    def test_dual_union_does_not_double_count_or_use_span_to_qualify(self):
        rows = fake_windows([3, 3, 8, 8, 3, 3])
        events = detect_dual(rows, 2, 6, 4, 8)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["duration_s"], 12)
        self.assertEqual(events[0]["rule_branch"], "high_short+lower_long")
        # Two sub-duration branches cannot become eligible by combining first.
        rows = fake_windows([3, 8, 3])
        self.assertEqual(detect_dual(rows, 2, 6, 4, 8), [])

    def test_width_quantization_can_make_duration_settings_identical(self):
        rows = fake_windows([1, 8, 1], 4)
        self.assertEqual(detect_runs(rows, 6, 2), detect_runs(rows, 6, 4))

    def test_interval_agreement_uses_common_valid_time(self):
        self.assertEqual(intersections([(0, 4), (8, 12)], [(2, 10)]), [(2, 4), (8, 10)])
        self.assertEqual(intersections([(0, 2)], [(2, 4)]), [])


class ReferenceTests(unittest.TestCase):
    def test_unlabelled_is_not_a_negative_and_mixed_boundaries_excluded(self):
        rows = fake_windows([1, 2, 3, 4, 5])
        refs = [ReferenceInterval(0, 2, "non_synchrony"), ReferenceInterval(2, 5, "synchrony"),
                ReferenceInterval(5, 6, "non_synchrony"), ReferenceInterval(6, 8, "uncertain")]
        result, coded = compare_reference(rows, refs, labels_independent=True,
            alignment_confirmed=True, time_reference="curve", annotation_time_reference="curve")
        self.assertEqual([r["status"] for r in coded],
                         ["included", "included", "mixed_boundary", "uncertain", "unlabelled_or_partial"])
        self.assertEqual(result["n_excluded"], 3)
        self.assertIsNone(result["p_value"])

    def test_independence_and_alignment_are_required(self):
        settings = dict(labels_independent=True, alignment_confirmed=True,
                        time_reference="curve", annotation_time_reference="curve")
        for override in ({"labels_independent": False}, {"alignment_confirmed": False},
                         {"annotation_time_reference": "video"}, {"time_reference": "unspecified"}):
            with self.assertRaises(ValueError):
                compare_reference(fake_windows([1, 9]), [], **(settings | override))

    def test_correlation_matches_known_binary_pearson_and_handles_constant_energy(self):
        self.assertAlmostEqual(point_biserial([0, 0, 1, 1], [1, 2, 3, 4]), 2 / math.sqrt(5))
        self.assertIsNone(point_biserial([0, 0], [1, 2]))
        self.assertIsNone(point_biserial([0, 1], [1, 1]))


class ExplorationIOTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.source = self.root / "session_01_energy.csv"
        write_csv(self.source, ({"time_s": i * .5, "energy": [1, 8, 3, 3][i // 8]}
                               for i in range(32)), ["time_s", "energy"])
        self.source.with_suffix(".timebase.json").write_text(json.dumps({"time_reference": "curve"}))

    def tearDown(self):
        self.temporary.cleanup()

    def test_cached_means_are_reused_and_tampering_rejected(self):
        analyze(self.source, session_id="session_01", output_dir=self.root / "prior", thresholds=(2, 6))
        cached = self.root / "prior" / "session_01"
        result = explore(self.source, session_id="session_01", output_dir=self.root / "new",
                         reuse_windows=cached, thresholds=(2, 6))
        self.assertEqual(result["window_source"], "checked_existing_exports")
        self.assertEqual(result["independent_energy_comparison"]["2s"]["status"], "not_run")
        path = cached / "session_01_2s_windows.csv"
        path.write_text(path.read_text().replace("1.0,4,4", "9.0,4,4"))
        with self.assertRaises(ValueError):
            explore(self.source, session_id="session_01", output_dir=self.root / "tampered",
                    reuse_windows=cached, thresholds=(2, 6))

    def test_no_overwrite_and_all_parameters_are_recorded(self):
        result = explore(self.source, session_id="session_01", output_dir=self.root / "new", thresholds=(2, 6))
        self.assertEqual(result["n_rule_runs"], 24)
        self.assertEqual(result["gap_bridge_seconds"], 0)
        self.assertIsNone(result["detection_accuracy"])
        self.assertEqual(result["energy_frame_period_seconds"], .5)
        with self.assertRaises(FileExistsError):
            explore(self.source, session_id="session_01", output_dir=self.root / "new", thresholds=(2, 6))

    def test_references_require_assertions_before_the_file_is_opened(self):
        with patch("sync_eval.exploration.load_references", side_effect=AssertionError("Must not open")):
            with self.assertRaises(ValueError):
                explore(self.source, session_id="session_01", output_dir=self.root / "new",
                        reference_labels="FICTIONAL_PRIVATE.csv", annotation_source="independent_human")

    def test_reference_schema_rejects_notes_and_overlaps(self):
        path = self.root / "fake_reference.csv"
        write_csv(path, [{"start": 0, "end": 2, "label": "synchrony", "notes": "FICTIONAL"}],
                  ["start", "end", "label", "notes"])
        with self.assertRaises(ValueError):
            load_references(path)
        other = self.root / "fake_overlapping.csv"
        write_csv(other, [{"start": 0, "end": 4, "label": "synchrony"},
                          {"start": 2, "end": 6, "label": "non_synchrony"}], ["start", "end", "label"])
        with self.assertRaises(ValueError):
            load_references(other)

    def test_synthetic_end_to_end_has_no_network_and_identifies_fake_labels(self):
        stream = io.StringIO()
        with patch("socket.socket", side_effect=AssertionError("No networking")), \
                contextlib.redirect_stdout(stream), contextlib.redirect_stderr(stream):
            status = main(["explore-demo", "--output-dir", str(self.root / "fake_demo")])
        self.assertEqual(status, 0)
        self.assertEqual(stream.getvalue(), "SUCCESS: files saved locally. No input values are printed.\n")
        summary = json.loads((self.root / "fake_demo/analysis/session_01/session_01_experiment.json").read_text())
        self.assertEqual(summary["provenance"], "synthetic")
        self.assertEqual(summary["n_rule_runs"], 80)
        self.assertEqual(summary["independent_energy_comparison"]["2s"]["annotation_source"], "synthetic_reference")

    def test_safe_experiment_failure_does_not_echo_paths_or_payloads(self):
        stream = io.StringIO()
        with patch("sync_eval.cli.explore", side_effect=ValueError("FICTIONAL_PRIVATE_VALUE")), \
                contextlib.redirect_stdout(stream), contextlib.redirect_stderr(stream):
            code = main(["explore", "--input", "FICTIONAL_PRIVATE_PATH", "--session", "session_01"])
        self.assertEqual(code, 2)
        self.assertIn("EXPLORE/VALUE_ERROR", stream.getvalue())
        self.assertNotIn("FICTIONAL_PRIVATE", stream.getvalue())


if __name__ == "__main__":
    unittest.main()
