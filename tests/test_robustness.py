"""Algorithm stress checks on invented energy, never study inputs."""

import csv
from pathlib import Path
import tempfile
import unittest

from check_data import check_data
from sync_eval.benchmark import benchmark_rules, invented_case, run_benchmark
from sync_eval.core import Event, evaluate_events, valid_coverage, window_average
from sync_eval.robustness import agreement, apply_rule, phase_scan


class RobustnessTests(unittest.TestCase):
    def test_empty_candidate_agreement_is_undefined(self):
        result = agreement([], [], [(0, 10)], [(2, 12)])
        self.assertIsNone(result["time_jaccard_common"])
        self.assertEqual(result["common_valid_seconds"], 8)

    def test_arbitrary_bin_origin_can_hide_same_known_burst(self):
        times = [i * .5 for i in range(40)]
        energies = [8 if 6 <= t < 10 else 1 for t in times]
        rule = {"rule_id": "4s_example", "rule_key": "example", "method": "single",
                "width_s": 4, "lower_threshold": 6, "min_duration_s": 4}
        records = phase_scan(times, energies, [rule])
        by_fraction = {r["phase_fraction"]: r for r in records}
        self.assertEqual(by_fraction[0]["candidate_events"], 0)
        self.assertEqual(by_fraction[.5]["candidate_events"], 1)
        self.assertEqual(by_fraction[.5]["time_jaccard_common"], 0)

    def test_identical_energy_with_different_labels_is_unidentifiable(self):
        a = invented_case("mixed_clean", 3)
        b = invented_case("sustained_nonsynchrony", 3)
        self.assertEqual(a[:2], b[:2])
        self.assertEqual(len(a[2]), 2)
        self.assertEqual(b[2], [])
        windows = window_average(a[0], a[1], 2)
        rule = next(r for r in benchmark_rules(2) if r["rule_key"] == "dual_t2_t6_s4_l8")
        predicted = [Event(e["start"], e["end"]) for e in apply_rule(windows, rule)]
        yes = evaluate_events(predicted, a[2], annotations_complete=True)
        no = evaluate_events(predicted, b[2], annotations_complete=True)
        self.assertGreater(yes["hit"], 0)
        self.assertEqual(no["hit"], 0)
        self.assertEqual(no["false_positives"], len(predicted))

    def test_missing_reference_is_reported_as_excluded(self):
        times, energies, reference, _ = invented_case("missing_inside_event", 0)
        windows = window_average(times, energies, 2)
        result = evaluate_events([], reference, coverage=valid_coverage(windows), annotations_complete=True)
        self.assertEqual(result["excluded_reference_events"], 1)
        self.assertEqual(result["miss"], 1)
        self.assertEqual(len(reference), result["excluded_reference_events"] + result["miss"])

    def test_all_parameter_rules_include_prespecified_fair_duration_controls(self):
        rules = benchmark_rules(2)
        self.assertEqual(len(rules), 24)
        self.assertEqual(len({r["rule_key"] for r in rules}), 24)
        self.assertEqual(sum(r["method"] == "single" for r in rules), 12)
        self.assertEqual(sum(r["method"] == "dual" for r in rules), 12)

    def test_small_complete_benchmark_writes_only_fake_roots(self):
        with tempfile.TemporaryDirectory() as temporary:
            repo = Path(temporary)
            destination, metadata = run_benchmark(seeds=1, figures=False, repo=repo)
            self.assertEqual(metadata["sessions"], 6)
            self.assertEqual(metadata["rule_runs"], 1152)
            self.assertEqual(metadata["matching_runs"], 432)
            self.assertFalse((repo / "data").exists())
            self.assertFalse((repo / "results").exists())
            fixture = next((repo / "fake_data/benchmark").iterdir())
            sessions, count = check_data(fixture)
            self.assertEqual((len(sessions), count), (6, 18))
            self.assertTrue(all(int(s.session_id.split("_")[1]) >= 90 for s in sessions))
            with (destination / "all_parameter_runs.csv").open(newline="") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(len(rows), 1152)
            negative = [r for r in rows if r["scenario"] == "constant_no_event"]
            self.assertTrue(all(int(r["candidate_events"]) == 0 for r in negative))
            missing = [r for r in rows if r["scenario"] == "missing_inside_event"]
            self.assertTrue(all(int(r["excluded_references"]) == 1 for r in missing))


if __name__ == "__main__":
    unittest.main()
