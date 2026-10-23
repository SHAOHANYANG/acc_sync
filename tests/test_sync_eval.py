"""All fixtures are invented. Tests never open study files or clinical records."""

import base64
import contextlib
import csv
import io
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

from sync_eval.cli import analyze, demo, main
from sync_eval.core import (Event, choose_thresholds, evaluate_events, interval_iou,
                            merge_windows, valid_coverage, window_average)
from sync_eval.io import (load_annotations, load_curve, plotly_energy,
                          prepare_curve, private_directory, write_csv)


class WindowTests(unittest.TestCase):
    def test_two_and_four_seconds_and_boundaries(self):
        times = [i * 0.5 for i in range(16)]
        energies = [1] * 4 + [3] * 4 + [5] * 4 + [7] * 4
        two = window_average(times, energies, 2, thresholds=(2, 6))
        four = window_average(times, energies, 4, thresholds=(2, 6))
        self.assertEqual([r["mean_energy"] for r in two], [1, 3, 5, 7])
        self.assertEqual([r["mean_energy"] for r in four], [2, 6])
        self.assertEqual([r["level"] for r in two], ["low", "medium", "medium", "high"])
        self.assertTrue(all(r["coverage"] == 1 for r in two + four))

    def test_missing_bins_are_not_interpolated_or_merged(self):
        times = [0, 0.5, 1, 1.5, 4, 4.5, 5, 5.5]
        rows = window_average(times, [9] * len(times), 2, thresholds=(2, 6))
        self.assertEqual([r["level"] for r in rows], ["high", "unknown", "high"])
        self.assertEqual(len(merge_windows(rows, {"high"})), 2)
        self.assertEqual(valid_coverage(rows), [(0, 2), (4, 6)])

    def test_missing_values_and_edge_windows_are_unknown(self):
        rows = window_average([0, .5, 1, 1.5, 2, 2.5], [1, None, None, 1, 9, 9],
                              2, thresholds=(2, 6))
        self.assertEqual([r["level"] for r in rows], ["unknown", "unknown"])
        self.assertTrue(rows[-1]["partial"])
        self.assertEqual(rows[0]["mean_energy"], 1)

    def test_video_grid_and_offset(self):
        rows = window_average([10, 10.5, 11, 11.5, 12, 12.5, 13, 13.5],
                              [9] * 8, 4, origin=2, thresholds=(2, 6))
        self.assertEqual((rows[0]["start"], rows[0]["end"]), (10, 14))
        self.assertEqual(rows[0]["level"], "high")

    def test_invalid_or_ambiguous_inputs(self):
        for times, values in [([0, 0], [1, 1]), ([1, 0], [1, 1]),
                              ([0, 1], [1, -1]), ([0, 1], [1, float("inf")])]:
            with self.assertRaises(ValueError):
                window_average(times, values, 2)
        with self.assertRaises(ValueError):
            choose_thresholds([1, 1, 1])
        with self.assertRaises(ValueError):
            window_average([0, 1], [1, 1], 0)

    def test_common_thresholds_do_not_depend_on_window_size(self):
        self.assertEqual(choose_thresholds([0, 3, 6, 9]), (3, 6))


class MatchingTests(unittest.TestCase):
    def test_hit_miss_and_unmatched_are_distinct(self):
        predictions = [Event(0, 4), Event(10, 14)]
        gold = [Event(0, 4, "singing"), Event(5, 6, "body_movement")]
        result = evaluate_events(predictions, gold)
        self.assertEqual((result["hit"], result["miss"], result["unmatched_predictions"]), (1, 1, 1))
        self.assertIsNone(result["false_positives"])
        self.assertIsNone(result["precision"])
        complete = evaluate_events(predictions, gold, annotations_complete=True)
        self.assertEqual(complete["precision"], .5)

    def test_one_prediction_cannot_hit_two_annotations(self):
        result = evaluate_events([Event(0, 10)], [Event(0, 5), Event(5, 10)])
        self.assertEqual((result["hit"], result["miss"]), (1, 1))

    def test_augmenting_paths_avoid_greedy_undercounting(self):
        result = evaluate_events([Event(0, 14), Event(0, 5)], [Event(0, 10), Event(6, 16)], min_iou=.5)
        self.assertEqual(result["hit"], 2)

    def test_touching_events_do_not_match(self):
        self.assertEqual(interval_iou(Event(0, 2), Event(2, 4)), 0)
        self.assertEqual(evaluate_events([Event(0, 2)], [Event(2, 4)])["hit"], 0)

    def test_events_crossing_invalid_coverage_are_excluded(self):
        result = evaluate_events([Event(0, 2)], [Event(1, 5)], coverage=[(0, 2), (4, 6)],
                                 annotations_complete=True)
        self.assertEqual(result["excluded_reference_events"], 1)
        self.assertEqual(result["excluded_predictions"], 1)
        self.assertEqual(result["miss"], 0)

    def test_empty_evaluation_has_no_invented_accuracy(self):
        result = evaluate_events([], [])
        self.assertEqual(result["hit"], 0)
        self.assertIsNone(result["recall"])


class OfflineIOTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def test_whitelist_removes_metadata_and_preserves_video_alignment(self):
        source = self.root / "FICTIONAL_SOURCE.csv"
        write_csv(source, [{"time": "2099-01-01T10:00:10", "value": 1, "name": "FICTIONAL_PERSON"},
                           {"time": "2099-01-01T10:00:11", "value": 2, "name": "FICTIONAL_PERSON"}],
                  ["time", "value", "name"])
        prepared = prepare_curve(source, kind="csv", session_id="session_01",
                                  time_origin="2099-01-01T10:00:00", time_format="datetime",
                                  offset_seconds=-2, time_column="time", energy_column="value",
                                  output_dir=self.root / "prepared", mapping_dir=self.root / "private")
        self.assertEqual(load_curve(prepared), ([8.0, 9.0], [1.0, 2.0]))
        text = prepared.read_text()
        self.assertNotIn("2099", text)
        self.assertNotIn("FICTIONAL", text)
        self.assertEqual(text.splitlines()[0], "time_s,energy")
        mapping = (self.root / "private" / "session_map.csv").read_text()
        self.assertIn("FICTIONAL_SOURCE", mapping)
        self.assertIn("2099-01-01T10:00:00", mapping)
        with self.assertRaises(FileExistsError):
            prepare_curve(source, kind="csv", session_id="session_01",
                          time_origin="2099-01-01T10:00:00", time_format="datetime",
                          time_column="time", energy_column="value",
                          output_dir=self.root / "prepared", mapping_dir=self.root / "private")

    def test_plotly_typed_arrays_and_no_javascript_execution(self):
        source = self.root / "session_01_fake_report.html"
        values = {"dtype": "f8", "bdata": base64.b64encode(struct.pack("<dd", 2, 4)).decode()}
        traces = [{"name": "mean cross energy", "x": [100, 101], "y": values,
                   "customdata": ["FICTIONAL_PERSON", "SYNTHETIC_NOTE"]}]
        source.write_text('Plotly.newPlot("fake", ' + json.dumps(traces)
                          + ', {"title":"FICTIONAL_TITLE"}); throw new Error("DO_NOT_RUN");')
        self.assertEqual(plotly_energy(source), ([100, 101], [2.0, 4.0]))
        prepared = prepare_curve(source, kind="plotly", session_id="session_01", time_origin="100",
                                  output_dir=self.root / "prepared", mapping_dir=self.root / "private")
        self.assertEqual(load_curve(prepared), ([0, 1], [2, 4]))
        self.assertNotIn("FICTIONAL", prepared.read_text())

    def test_duplicate_plotly_trace_is_rejected(self):
        source = self.root / "session_01_fake_report.html"
        trace = {"name": "mean cross energy", "x": [0, 1], "y": [1, 2]}
        source.write_text('Plotly.newPlot("fake", ' + json.dumps([trace, trace]) + ', {});')
        with self.assertRaises(ValueError):
            plotly_energy(source)

    def test_db_conversion_and_missing_values(self):
        source = self.root / "session_01_db.csv"
        write_csv(source, [{"time_s": 0, "energy": 10}, {"time_s": 1, "energy": 20},
                           {"time_s": 2, "energy": None}], ["time_s", "energy"])
        prepared = prepare_curve(source, kind="csv", session_id="session_01", time_origin="0",
                                  energy_scale="db", output_dir=self.root / "prepared",
                                  mapping_dir=self.root / "private")
        self.assertEqual(load_curve(prepared), ([0, 1, 2], [10, 100, None]))

    def test_annotation_schema_and_clock_format(self):
        source = self.root / "session_01_annotations.csv"
        write_csv(source, [{"start": "01:02", "end": "00:01:04", "activity_type": "singing"}],
                  ["start", "end", "activity_type"])
        self.assertEqual(load_annotations(source), [Event(62, 64, "singing")])
        extra = self.root / "session_02_annotations.csv"
        write_csv(extra, [{"start": 1, "end": 2, "activity_type": "FICTIONAL_NAME"}],
                  ["start", "end", "activity_type"])
        with self.assertRaises(ValueError):
            load_annotations(extra)

    def test_console_never_echoes_private_arguments(self):
        stream = io.StringIO()
        with contextlib.redirect_stdout(stream), contextlib.redirect_stderr(stream):
            result = main(["prepare", "--input", "FICTIONAL_PRIVATE_PATH"])
        self.assertEqual(result, 2)
        self.assertNotIn("FICTIONAL_PRIVATE_PATH", stream.getvalue())
        self.assertNotIn("Traceback", stream.getvalue())

    def test_outputs_cannot_use_public_repository_directories(self):
        repository = Path(__file__).resolve().parents[1]
        with self.assertRaises(ValueError):
            private_directory(repository / "examples" / "not-private")

    def test_full_synthetic_demo(self):
        result = demo(self.root / "demo")
        self.assertEqual(set(result["windows"]), {"2s", "4s"})
        for width in result["windows"].values():
            counts = width["evaluation"]
            self.assertEqual((counts["hit"], counts["miss"], counts["unmatched_predictions"]), (2, 1, 1))
            self.assertAlmostEqual(counts["recall"], 2 / 3)
        prepared = self.root / "demo" / "prepared" / "session_01_energy.csv"
        with self.assertRaises(FileExistsError):
            analyze(prepared, session_id="session_01", output_dir=self.root / "demo" / "analysis",
                    thresholds=(3, 6))

    def test_prepare_and_analyze_cli_without_network_or_console_data(self):
        source = self.root / "session_01_fake.csv"
        write_csv(source, [{"time_s": i * .5, "energy": [1, 9][i // 8]}
                           for i in range(16)], ["time_s", "energy"])
        prepared, private, results = self.root / "prepared", self.root / "private", self.root / "results"
        output = io.StringIO()
        with patch("socket.socket", side_effect=AssertionError("No networking in core commands")), \
                contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            self.assertEqual(main(["prepare", "--kind", "csv", "--input", str(source),
                                   "--session", "session_01", "--time-origin", "0",
                                   "--energy-scale", "linear", "--output-dir", str(prepared),
                                   "--mapping-dir", str(private)]), 0)
            self.assertEqual(main(["analyze", "--input", str(prepared / "session_01_energy.csv"),
                                   "--session", "session_01", "--thresholds", "3", "6",
                                   "--output-dir", str(results)]), 0)
        self.assertEqual(output.getvalue().count("SUCCESS:"), 2)
        self.assertNotIn(str(source), output.getvalue())
        self.assertEqual(len(list((results / "session_01").glob("*.csv"))), 6)

    def test_mapping_failure_removes_only_the_new_export(self):
        source = self.root / "session_01_fake.csv"
        write_csv(source, [{"time_s": 0, "energy": 1}, {"time_s": 1, "energy": 2}],
                  ["time_s", "energy"])
        with patch.object(Path, "replace", side_effect=OSError("SYNTHETIC_FAILURE")):
            with self.assertRaises(OSError):
                prepare_curve(source, kind="csv", session_id="session_01", time_origin="0",
                              output_dir=self.root / "prepared", mapping_dir=self.root / "private")
        self.assertFalse((self.root / "prepared" / "session_01_energy.csv").exists())
        self.assertTrue(source.exists())


if __name__ == "__main__":
    unittest.main()
