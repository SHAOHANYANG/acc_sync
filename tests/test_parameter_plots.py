"""Dual plot semantics using entirely invented values and labels."""

from pathlib import Path
import itertools
import tempfile
import unittest

from sync_eval.core import valid_coverage, window_average
from sync_eval.exploration import detect_dual
from sync_eval.parameter_plots import dual_heatmap_data, dual_heatmap_figure, save_dual_heatmap


def invented_grid(values):
    frames = [value for value in values for _ in range(4)]
    times = [i * .5 for i in range(len(frames))]
    rows = []
    for width in (2, 4):
        windows = window_average(times, frames, width)
        events = detect_dual(windows, 2, 6, 4, 8)
        valid = sum(b - a for a, b in valid_coverage(windows))
        total = sum(e["duration_s"] for e in events)
        rows.append({"method": "dual", "width_s": width, "lower_threshold": 2,
            "upper_threshold": 6, "lower_quantile": .5, "upper_quantile": .9,
            "min_duration_s": 4, "long_duration_s": 8,
            "effective_min_duration_s": 4, "effective_long_duration_s": 8,
            "n_events": len(events), "total_candidate_s": total, "valid_s": valid,
            "candidate_fraction_valid": total / valid if valid else None})
    return rows


class DualHeatmapTests(unittest.TestCase):
    def test_each_cell_retains_both_thresholds_and_both_durations(self):
        grid = invented_grid([1, 8, 8, 1, 3, 3, 3, 3, 1])
        data = dual_heatmap_data(grid)
        self.assertEqual(data["pairs"], [(2, 6, .5, .9)])
        self.assertEqual(data["durations"], [(4, 8)])
        self.assertEqual(data["panels"][2][0][0]["candidate_fraction_valid"], 12 / 18)
        for width in (2, 4):
            self.assertEqual(data["panels"][width][0][0], grid[0 if width == 2 else 1])

    def test_overlapping_branches_are_counted_once(self):
        grid = invented_grid([3, 3, 8, 8, 3, 3])
        cells = dual_heatmap_data(grid)["panels"]
        for width in (2, 4):
            self.assertEqual(cells[width][0][0]["candidate_fraction_valid"], 1)
            self.assertEqual(cells[width][0][0]["total_candidate_s"], 12)

    def test_missing_cell_and_zero_selection_remain_different(self):
        grid = invented_grid([1] * 8)
        grid.append(dict(grid[0], min_duration_s=2, effective_min_duration_s=2))
        data = dual_heatmap_data(grid)
        self.assertEqual(data["panels"][2][0][0]["candidate_fraction_valid"], 0)
        self.assertIsNone(data["panels"][4][0][0])
        self.assertEqual(data["panels"][4][0][1]["candidate_fraction_valid"], 0)

    def test_duplicate_cells_are_not_silently_averaged(self):
        grid = invented_grid([1] * 8)
        with self.assertRaises(ValueError):
            dual_heatmap_data(grid + [dict(grid[0])])

    def test_quantile_and_manual_threshold_labels_are_distinct(self):
        grid = invented_grid([1] * 8)
        grid += [dict(r, lower_quantile=None, upper_quantile=None) for r in grid]
        data = dual_heatmap_data(grid)
        self.assertEqual(len(data["pairs"]), 2)
        figure = dual_heatmap_figure(grid, fake=True)
        labels = list(figure.layout.yaxis.ticktext)
        self.assertIn("q50%", labels[1])
        self.assertNotIn("q", labels[0])

    def test_plot_uses_separate_widths_and_one_shared_fraction_scale(self):
        grid = invented_grid([3, 3, 8, 8, 3, 3])
        figure = dual_heatmap_figure(grid, fake=True)
        self.assertEqual(len(figure.data), 2)
        self.assertEqual(figure.layout.coloraxis.cmin, 0)
        self.assertEqual(figure.layout.coloraxis.cmax, 1)
        for trace in figure.data:
            self.assertEqual(trace.coloraxis, "coloraxis")
            self.assertEqual(trace.z[0][0], 1)
            self.assertEqual(trace.customdata[0][0][:4], [2, 6, 4, 8])
            self.assertIn("effective", trace.hovertemplate)
        self.assertIn("2s", figure.data[0].hovertemplate)
        self.assertIn("4s", figure.data[1].hovertemplate)

    def test_full_quantile_scan_has_six_pairs_and_four_duration_pairs(self):
        base = invented_grid([1] * 8)[0]
        quantiles = (.5, 2 / 3, .8, .9)
        grid = []
        for pair_index, (low, high) in enumerate(itertools.combinations(range(4), 2)):
            for duration_index, (short, long) in enumerate(itertools.product((2, 4), (8, 12))):
                value = (4 * pair_index + duration_index + 1) / 25
                for width in (2, 4):
                    fraction = value if width == 2 else 1 - value
                    grid.append(dict(base, width_s=width, lower_threshold=low + 1, upper_threshold=high + 1,
                        lower_quantile=quantiles[low], upper_quantile=quantiles[high], min_duration_s=short,
                        long_duration_s=long, effective_min_duration_s=max(short, width),
                        effective_long_duration_s=long, valid_s=600, total_candidate_s=600 * fraction,
                        candidate_fraction_valid=fraction))
        data = dual_heatmap_data(grid)
        self.assertEqual((len(data["pairs"]), len(data["durations"])), (6, 4))
        figure = dual_heatmap_figure(grid, fake=True)
        self.assertEqual((len(figure.data[0].z), len(figure.data[0].z[0])), (6, 4))
        self.assertAlmostEqual(figure.data[0].z[0][0], .04)
        self.assertAlmostEqual(figure.data[1].z[0][0], .96)
        # Same requested 2s condition, explicitly different effective durations.
        self.assertEqual(figure.data[0].customdata[0][0][4], 2)
        self.assertEqual(figure.data[1].customdata[0][0][4], 4)

    def test_no_valid_dual_pairs_has_an_explicit_message(self):
        figure = dual_heatmap_figure([], fake=True)
        self.assertEqual(len(figure.data), 0)
        self.assertTrue(any("No ordered" in a.text for a in figure.layout.annotations))

    def test_standalone_fake_html_has_reading_guide_and_embedded_js(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "synthetic_dual.html"
            save_dual_heatmap(path, invented_grid([1] * 8), fake=True)
            html = path.read_text(encoding="utf-8")
            # Plotly escapes slashes in JSON; test labels without dumping megabytes
            # of embedded JS if a check fails.
            for marker in ("双门槛参数热力图", "SYNTHETIC", "NOT RESEARCH RESULTS", "2s windows",
                           "4s windows", "Overlapping branches count once", "plotly.js"):
                self.assertTrue(marker in html, f"Missing public label: {marker}")
            self.assertFalse('<script src="http' in html)
            with self.assertRaises(FileExistsError):
                save_dual_heatmap(path, invented_grid([1] * 8), fake=True)


if __name__ == "__main__":
    unittest.main()
