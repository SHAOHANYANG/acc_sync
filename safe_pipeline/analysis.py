"""Trusted analysis used only by run_real's silent child process.

Research figures are written, never opened. Detailed rows stay under results.
"""

import csv
from datetime import datetime
import json
from pathlib import Path
import statistics
import uuid

from sync_eval.core import Event, evaluate_events, valid_coverage, window_average
from sync_eval.exploration import (ReferenceInterval, _plot_experiment, compare_reference,
                                   explore, intersections, point_biserial)
from .boundary import safe_output_directory


def _read_rows(path):
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def _reference(session):
    return sorted([ReferenceInterval(a, b, label) for label in ("synchrony", "non_synchrony", "uncertain")
                   for a, b in getattr(session, label)], key=lambda r: r.start)


def _evaluate(predictions, session, coverage, config):
    if not config.get("labels_independent") or not config.get("alignment_confirmed"):
        return None
    if "synchrony" not in session.present and not config.get("annotations_complete"):
        return None
    # Complete negatives require explicit coded coverage, not absence of notes.
    coded = sorted(session.synchrony + session.non_synchrony)
    observed = sum(b - a for a, b in coverage)
    covered = sum(b - a for a, b in intersections(coverage, coded))
    complete = bool(config.get("annotations_complete") and observed > 0 and covered >= observed - 1e-8)
    result = evaluate_events(predictions, [Event(a, b) for a, b in session.synchrony],
                            min_iou=config.get("min_iou", .5), coverage=coverage,
                            annotations_complete=complete)
    result["complete_coverage_confirmed"] = complete
    return result


def _groups(coded, width):
    included = [r for r in coded if r["status"] == "included"]
    return [(int(r["label"] == "synchrony"), r["mean_energy"]) for r in included]


def _render(path, session, windows, grid, event_map, comparisons, labelled, metadata):
    _plot_experiment(path, session.times, session.energies, windows, grid, event_map,
                     comparisons, labelled, metadata)


def analyse(sessions, file_count, output, config, *, fake=False, figures=True):
    run = "run_" + datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:8]
    safe_output_directory(output / "tables")
    safe_output_directory(output / "figures")
    tables = output / "tables" / run
    plots = output / "figures" / run
    tables.mkdir(parents=True)
    if figures:
        plots.mkdir(parents=True)
    parameters = {k: v for k, v in config.items() if k in {
        "thresholds", "quantiles", "durations", "short_durations", "long_durations",
        "window_origin", "min_coverage", "time_reference"}}
    rule_totals, group_pairs, group_sessions, evaluations = {}, {2: [], 4: []}, {2: 0, 4: 0}, {}
    comparison_sessions, evaluation_sessions = set(), set()
    for session in sessions:
        metadata = explore(session.energy_path, session_id=session.session_id, output_dir=tables,
                           provenance="synthetic" if fake else "local_research", plot=False, **parameters)
        directory = tables / session.session_id
        grid_strings = _read_rows(directory / f"{session.session_id}_rule_grid.csv")
        grid = []
        for row in grid_strings:
            grid.append({k: (v if k in {"rule_id", "rule_key", "method"} else float(v) if v else None)
                         for k, v in row.items()})
        event_map = {r["rule_id"]: [] for r in grid}
        for r in _read_rows(directory / f"{session.session_id}_candidate_events.csv"):
            event_map[r["rule_id"]].append({k: (v if k == "rule_branch" else float(v))
                for k, v in r.items() if k not in {"rule_id", "width_s"}})
        windows = {width: window_average(session.times, session.energies, width,
                    origin=config.get("window_origin", 0), min_coverage=config.get("min_coverage", .8)) for width in (2, 4)}
        references = _reference(session)
        comparisons, labelled, session_evaluation = {}, [], []
        for width in (2, 4):
            key = f"{width}s"
            if references and config.get("labels_independent") and config.get("alignment_confirmed"):
                comparison, coded = compare_reference(windows[width], references, labels_independent=True,
                    alignment_confirmed=True, time_reference=config.get("time_reference", "curve"),
                    annotation_time_reference=config.get("time_reference", "curve"))
                comparison["annotation_source"] = "synthetic_reference" if fake else "independent_timestamps"
                comparisons[key] = comparison
                group_pairs[width].extend(_groups(coded, width))
                group_sessions[width] += 1
                comparison_sessions.add(session.session_id)
                labelled += [{"width_s": width, **r} for r in coded]
            else:
                comparisons[key] = {"status": "not_run"}
        for r in grid:
            key = (r["width_s"], r["rule_key"])
            if key not in rule_totals:
                rule_totals[key] = dict(r, sessions=0, sum_low=0, sum_high=0, candidate_events=0, candidate_seconds=0)
            total = rule_totals[key]
            total["sessions"] += 1
            total["sum_low"] += r["lower_threshold"]
            total["sum_high"] += r["upper_threshold"] or 0
            total["candidate_events"] += int(r["n_events"])
            total["candidate_seconds"] += r["total_candidate_s"]
            candidates = [Event(e["start"], e["end"]) for e in event_map[r["rule_id"]]]
            result = _evaluate(candidates, session, valid_coverage(windows[r["width_s"]]), config)
            if result is not None:
                evaluation_sessions.add(session.session_id)
                session_evaluation.append({"source": "baseline_rule", "rule_key": r["rule_key"],
                                           "width_s": r["width_s"], "result": result})
                evaluations.setdefault((0, key), []).append(result)
        if "predictions" in session.present:
            # Emily intervals are already events; no energy-based labels invented.
            coverage = valid_coverage(windows[2])
            result = _evaluate([Event(a, b) for a, b in session.predictions], session, coverage, config)
            if result is not None:
                evaluation_sessions.add(session.session_id)
                session_evaluation.append({"source": "external_predictions", "result": result})
                evaluations.setdefault((1, None), []).append(result)
        metadata["independent_energy_comparison"] = comparisons
        (directory / f"{session.session_id}_controlled_analysis.json").write_text(json.dumps({
            "comparison": comparisons, "evaluation": session_evaluation,
            "labels_independent": config.get("labels_independent", False),
            "alignment_confirmed": config.get("alignment_confirmed", False)}, indent=2, allow_nan=False), encoding="utf-8")
        if labelled:
            with (directory / f"{session.session_id}_independent_windows.csv").open("x", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, ["width_s", "start", "end", "mean_energy", "label", "status"])
                writer.writeheader(); writer.writerows(labelled)
        if figures:
            _render(plots / f"{session.session_id}.html", session, windows, grid, event_map,
                    comparisons, labelled, metadata)
    rule_rows, key_numbers = [], {}
    for index, (key, r) in enumerate(sorted(rule_totals.items()), 1):
        key_numbers[key] = index
        rule_rows.append({"rule_index": index, "width_s": r["width_s"], "method": 1 if r["method"] == "single" else 2,
            "low_quantile": r["lower_quantile"], "high_quantile": r["upper_quantile"],
            "low_threshold": r["sum_low"] / r["sessions"],
            "high_threshold": r["sum_high"] / r["sessions"] if r["upper_threshold"] is not None else None,
            "min_duration_s": r["min_duration_s"], "long_duration_s": r["long_duration_s"],
            "sessions": r["sessions"], "candidate_events": r["candidate_events"], "candidate_seconds": r["candidate_seconds"]})
    group_rows = []
    for width, pairs in group_pairs.items():
        if not group_sessions[width]:
            continue
        yes, no = [v for k, v in pairs if k == 1], [v for k, v in pairs if k == 0]
        group_rows.append({"width_s": width, "sessions": group_sessions[width],
            "synchrony_windows": len(yes), "non_synchrony_windows": len(no),
            "mean_energy_difference": statistics.fmean(yes) - statistics.fmean(no) if yes and no else None,
            "r_pb": point_biserial([k for k, v in pairs], [v for k, v in pairs])})
    eval_rows = []
    for (source, key), records in evaluations.items():
        hit, miss = sum(r["hit"] for r in records), sum(r["miss"] for r in records)
        fp = sum(r["false_positives"] for r in records) if all(r["false_positives"] is not None for r in records) else None
        eval_rows.append({"source": source, "rule_index": key_numbers[key] if key is not None else 0,
            "width_s": key[0] if key is not None else 0, "sessions": len(records), "hit": hit, "miss": miss,
            "unmatched_predictions": sum(r["unmatched_predictions"] for r in records), "false_positives": fp,
            "recall": hit / (hit + miss) if hit + miss else None,
            "precision": hit / (hit + fp) if fp is not None and hit + fp else None})
    result = {"status": "ok", "totals": {"sessions": len(sessions), "files": file_count,
        "rule_runs": sum(r["sessions"] for r in rule_rows), "comparison_sessions": len(comparison_sessions),
        "evaluation_sessions": len(evaluation_sessions)}, "rules": rule_rows,
        "comparisons": group_rows, "evaluations": eval_rows}
    (tables / "run_summary.json").write_text(json.dumps(result, indent=2, allow_nan=False), encoding="utf-8")
    (tables / "config.json").write_text(json.dumps(config, indent=2, allow_nan=False), encoding="utf-8")
    return result
