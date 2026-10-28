"""Local Energy + Duration exploration. No networking or console data output.

Only existing numerical energy is analysed; this module never imports acc_sync
or recomputes STFT. Research inputs are opened only by the researcher's command.
"""

import csv
from dataclasses import dataclass
import hashlib
import itertools
import json
import math
from pathlib import Path
import random
import statistics

from .core import quantile, valid_coverage, window_average
from .io import check_session_id, load_curve, private_directory, seconds, write_csv


WINDOW_FIELDS = ["start", "end", "mean_energy", "n_valid", "n_expected",
                 "coverage", "partial", "level"]
EVENT_FIELDS = ["start", "end", "duration_s", "n_windows", "mean_window_energy",
                "min_window_energy", "max_window_energy", "rule_branch"]


def _finite_values(values, *, positive=False):
    values = tuple(float(v) for v in values)
    if not values or len(set(values)) != len(values) or any(
            not math.isfinite(v) or v < 0 or (positive and v == 0) for v in values):
        raise ValueError("Use distinct finite nonnegative parameters.")
    return tuple(sorted(values))


def _event(windows, branch):
    values = [w["mean_energy"] for w in windows]
    return {"start": windows[0]["start"], "end": windows[-1]["end"],
            "duration_s": windows[-1]["end"] - windows[0]["start"],
            "n_windows": len(windows), "mean_window_energy": statistics.fmean(values),
            "min_window_energy": min(values), "max_window_energy": max(values),
            "rule_branch": branch}


def detect_runs(windows, threshold, min_duration, branch="single"):
    """Consecutive valid windows >= threshold; duration filter AFTER joining.

    No gap bridging, interpolation, outlier removal, or energy integration.
    Duration is the span of complete qualifying bins, not a clinical duration.
    """
    if not math.isfinite(threshold) or threshold < 0 or not math.isfinite(min_duration) or min_duration < 0:
        raise ValueError("Invalid energy or duration rule.")
    events, current = [], []

    def finish():
        if current and current[-1]["end"] - current[0]["start"] + 1e-9 >= min_duration:
            events.append(_event(current, branch))
        current.clear()

    for w in windows:
        eligible = w["level"] != "unknown" and w["mean_energy"] is not None and w["mean_energy"] >= threshold
        if not eligible:
            finish()
            continue
        if current and not math.isclose(current[-1]["end"], w["start"], abs_tol=1e-8):
            finish()
        current.append(w)
    finish()
    return events


def detect_dual(windows, low_threshold, high_threshold, short_duration, long_duration):
    """(high AND short) OR (lower AND long), with duration filtering per branch."""
    if high_threshold <= low_threshold or long_duration <= short_duration:
        raise ValueError("Dual rule needs ordered thresholds and durations.")
    branches = detect_runs(windows, high_threshold, short_duration, "high_short")
    branches += detect_runs(windows, low_threshold, long_duration, "lower_long")
    merged = []
    for e in sorted(branches, key=lambda r: (r["start"], r["end"])):
        if merged and e["start"] <= merged[-1]["end"] + 1e-8:
            merged[-1]["end"] = max(merged[-1]["end"], e["end"])
            merged[-1]["branches"].add(e["rule_branch"])
        else:
            merged.append({"start": e["start"], "end": e["end"], "branches": {e["rule_branch"]}})
    return [_event([w for w in windows if w["start"] >= e["start"] - 1e-8
                    and w["end"] <= e["end"] + 1e-8], "+".join(sorted(e["branches"]))) for e in merged]


def intersections(a, b):
    """Intersection of two sorted, nonoverlapping interval lists."""
    out, i, j = [], 0, 0
    while i < len(a) and j < len(b):
        start, end = max(a[i][0], b[j][0]), min(a[i][1], b[j][1])
        if end > start + 1e-9:
            out.append((start, end))
        if a[i][1] < b[j][1]:
            i += 1
        else:
            j += 1
    return out


def _length(intervals):
    return sum(b - a for a, b in intervals)


@dataclass(frozen=True)
class ReferenceInterval:
    start: float
    end: float
    label: str

    def __post_init__(self):
        if not all(math.isfinite(v) for v in (self.start, self.end)) or self.start < 0 or self.end <= self.start:
            raise ValueError("Invalid reference interval.")
        if self.label not in {"synchrony", "non_synchrony", "uncertain"}:
            raise ValueError("Use controlled reference labels.")


def load_references(path):
    with Path(path).open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != ["start", "end", "label"]:
            raise ValueError("Use start,end,label without notes or identifying columns.")
        rows = sorted((ReferenceInterval(seconds(r["start"]), seconds(r["end"]), r["label"])
                       for r in reader), key=lambda r: r.start)
    if any(b.start < a.end - 1e-8 for a, b in zip(rows, rows[1:])):
        raise ValueError("Overlapping reference intervals require human resolution.")
    return rows


def point_biserial(labels, energies):
    """Pearson(binary independent label, continuous energy), descriptive only."""
    if len(labels) != len(energies) or not labels or set(labels) != {0, 1}:
        return None
    if any(not math.isfinite(e) for e in energies):
        raise ValueError("Nonfinite comparison energy.")
    xbar, ybar = statistics.fmean(labels), statistics.fmean(energies)
    xx = sum((x - xbar) ** 2 for x in labels)
    yy = sum((y - ybar) ** 2 for y in energies)
    if not yy:
        return None
    xy = sum((x - xbar) * (y - ybar) for x, y in zip(labels, energies))
    return max(-1.0, min(1.0, xy / math.sqrt(xx * yy)))


def compare_reference(windows, references, *, labels_independent, alignment_confirmed,
                      time_reference, annotation_time_reference):
    if not labels_independent or not alignment_confirmed or time_reference not in {"curve", "video"} \
            or annotation_time_reference != time_reference:
        raise ValueError("Independent labels and a confirmed matching time reference are required.")
    coded = []
    for w in windows:
        coverage = {k: 0.0 for k in ("synchrony", "non_synchrony", "uncertain")}
        for r in references:
            coverage[r.label] += max(0.0, min(w["end"], r.end) - max(w["start"], r.start))
        width = w["end"] - w["start"]
        label, status = "", "unlabelled_or_partial"
        if w["level"] == "unknown":
            status = "invalid_energy_window"
        elif coverage["uncertain"] > 1e-8:
            status = "uncertain"
        elif coverage["synchrony"] > 1e-8 and coverage["non_synchrony"] > 1e-8:
            status = "mixed_boundary"
        else:
            for candidate in ("synchrony", "non_synchrony"):
                if coverage[candidate] >= width - 1e-8:
                    label, status = candidate, "included"
        coded.append({"start": w["start"], "end": w["end"], "mean_energy": w["mean_energy"],
                      "label": label, "status": status})
    groups = []
    for label in ("non_synchrony", "synchrony"):
        values = [r["mean_energy"] for r in coded if r["label"] == label]
        groups.append({"label": label, "n_windows": len(values),
                       "mean_energy": statistics.fmean(values) if values else None,
                       "median_energy": statistics.median(values) if values else None,
                       "sd_energy": statistics.stdev(values) if len(values) > 1 else None,
                       "q25_energy": quantile(values, .25) if values else None,
                       "q75_energy": quantile(values, .75) if values else None})
    included = [r for r in coded if r["status"] == "included"]
    r_pb = point_biserial([int(r["label"] == "synchrony") for r in included],
                         [r["mean_energy"] for r in included])
    return {"status": "descriptive_only", "groups": groups, "point_biserial_r": r_pb,
            "n_excluded": len(coded) - len(included), "p_value": None,
            "p_value_reason": "Serially dependent windows and overlapping upstream frames; no IID test performed.",
            "labelling_rule": "100% of each valid window must have one explicit independent label; absence is not negative."}, coded


def checked_cached_windows(directory, session_id, width, expected, metadata):
    """Reuse exported means only after checking them against the existing core.

    This cheap consistency check reuses window_average; no STFT is performed.
    Legacy labels are not used to set any new threshold or annotation label.
    """
    directory = Path(directory)
    old = json.loads((directory / f"{session_id}_summary.json").read_text(encoding="utf-8"))
    if old.get("session_id") != session_id or any(old.get(k) != metadata[k] for k in
            ("time_reference", "window_origin_seconds", "min_coverage")):
        raise ValueError("Cached window settings do not match.")
    with (directory / f"{session_id}_{width:g}s_windows.csv").open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != WINDOW_FIELDS:
            raise ValueError("Unexpected cached window schema.")
        cached = list(reader)
    if len(cached) != len(expected):
        raise ValueError("Cached window count mismatch.")
    output = []
    for a, b in zip(cached, expected):
        row = dict(b)
        for key in WINDOW_FIELDS:
            if key == "level":
                continue
            if key == "partial":
                if a[key] not in {"True", "False"} or (a[key] == "True") != b[key]:
                    raise ValueError("Cached boundary mismatch.")
            elif b[key] is None:
                if a[key] != "":
                    raise ValueError("Cached missing value mismatch.")
            elif not math.isclose(float(a[key]), b[key], rel_tol=1e-10, abs_tol=1e-10):
                raise ValueError("Cached numerical value mismatch.")
        row["mean_energy"] = float(a["mean_energy"]) if a["mean_energy"] else None
        output.append(row)
    return output


def _effective_duration(duration, width):
    return width * max(1, math.ceil(duration / width - 1e-9))


def explore(input_path, *, session_id, output_dir="local-results/window-exploration", widths=(2, 4),
            quantiles=(.5, 2 / 3, .8, .9), thresholds=None, durations=(2, 4, 8, 12),
            short_durations=(2, 4), long_durations=(8, 12), window_origin=0.0,
            sample_period=None, min_coverage=.8, reuse_windows=None, time_reference="auto",
            reference_labels=None, labels_independent=False, alignment_confirmed=False,
            annotation_time_reference=None, annotation_source=None, plot=False,
            provenance="local_research"):
    check_session_id(session_id)
    widths = _finite_values(widths, positive=True)
    if widths != (2.0, 4.0):
        raise ValueError("This comparison requires 2 s and 4 s widths.")
    durations = _finite_values(durations)
    short_durations = _finite_values(short_durations)
    long_durations = _finite_values(long_durations, positive=True)
    times, energies = load_curve(input_path)
    if time_reference == "auto":
        sidecar = Path(input_path).with_suffix(".timebase.json")
        time_reference = json.loads(sidecar.read_text(encoding="utf-8")).get("time_reference") if sidecar.exists() else "unspecified"
    if time_reference not in {"curve", "video", "unspecified"} or provenance not in {"local_research", "synthetic"}:
        raise ValueError("Invalid provenance or time reference.")
    if thresholds is None:
        qs = _finite_values(quantiles, positive=True)
        if any(q >= 1 for q in qs):
            raise ValueError("Use interior quantiles.")
        observed = [e for e in energies if e is not None]
        specs = [{"id": f"q{q:.6f}", "value": quantile(observed, q), "quantile": q} for q in qs]
    else:
        specs = [{"id": f"t{i + 1}", "value": value, "quantile": None}
                 for i, value in enumerate(_finite_values(thresholds))]
    if len(specs) * len(durations) + len(specs) ** 2 * len(short_durations) * len(long_durations) > 2000:
        raise ValueError("Use a small interpretable parameter grid.")
    metadata = {"session_id": session_id, "provenance": provenance, "exploratory_only": True,
                "time_reference": time_reference, "window_origin_seconds": window_origin,
                "min_coverage": min_coverage, "energy_scale": "linear",
                "energy_frame_period_seconds": statistics.median(b - a for a, b in zip(times, times[1:])),
                "window_source": "checked_existing_exports" if reuse_windows else "existing_window_average_function",
                "threshold_basis": "manual_absolute" if thresholds is not None else "valid_input_curve_quantiles_shared_across_widths",
                "thresholds": specs, "minimum_durations_seconds": list(durations),
                "short_durations_seconds": list(short_durations), "long_durations_seconds": list(long_durations),
                "gap_bridge_seconds": 0, "upstream_signal_processing_recomputed": False,
                "duplicate_threshold_values": len(specs) - len({s["value"] for s in specs}),
                "input_sha256": hashlib.sha256(Path(input_path).read_bytes()).hexdigest()}
    windows = {}
    for width in widths:
        rows = window_average(times, energies, width, origin=window_origin,
                              sample_period=sample_period, min_coverage=min_coverage)
        windows[width] = checked_cached_windows(reuse_windows, session_id, width, rows, metadata) if reuse_windows else rows
    references = None
    if reference_labels is not None:
        if annotation_source not in {"independent_human", "synthetic_reference"} \
                or (annotation_source == "synthetic_reference" and provenance != "synthetic"):
            raise ValueError("Declare independent reference provenance.")
        # Check consent/provenance assertions BEFORE opening any reference file.
        if not labels_independent or not alignment_confirmed or time_reference not in {"curve", "video"} \
                or annotation_time_reference != time_reference:
            raise ValueError("Independent aligned references are required.")
        references = load_references(reference_labels)
    comparisons, labelled_rows, group_rows = {}, [], []
    for width, rows in windows.items():
        if references is None:
            comparisons[f"{width:g}s"] = {"status": "not_run", "reason": "No independent reference labels supplied."}
        else:
            comparison, coded = compare_reference(rows, references, labels_independent=labels_independent,
                alignment_confirmed=alignment_confirmed, time_reference=time_reference,
                annotation_time_reference=annotation_time_reference)
            comparison["annotation_source"] = annotation_source
            comparisons[f"{width:g}s"] = comparison
            labelled_rows += [{"width_s": width, **r} for r in coded]
            group_rows += [{"width_s": width, **r} for r in comparison["groups"]]
    grid, all_events, event_map = [], [], {}
    for width, rows in windows.items():
        valid_s = _length(valid_coverage(rows))
        rules = []
        for spec, duration in itertools.product(specs, durations):
            key = f"single_{spec['id']}_d{duration:g}"
            rules.append((key, "single", spec, None, duration, None,
                          detect_runs(rows, spec["value"], duration)))
        for low, high in itertools.combinations(specs, 2):
            if low["value"] >= high["value"]:
                continue
            for short, long in itertools.product(short_durations, long_durations):
                if long <= short:
                    continue
                key = f"dual_{low['id']}_{high['id']}_s{short:g}_l{long:g}"
                rules.append((key, "dual", low, high, short, long,
                              detect_dual(rows, low["value"], high["value"], short, long)))
        for key, method, low, high, short, long, events in rules:
            rule_id = f"{width:g}s_{key}"
            total = sum(e["duration_s"] for e in events)
            grid.append({"rule_id": rule_id, "rule_key": key, "width_s": width, "method": method,
                "lower_threshold": low["value"], "upper_threshold": high["value"] if high else None,
                "lower_quantile": low["quantile"], "upper_quantile": high["quantile"] if high else None,
                "min_duration_s": short, "long_duration_s": long,
                "effective_min_duration_s": _effective_duration(short, width),
                "effective_long_duration_s": _effective_duration(long, width) if long is not None else None,
                "n_events": len(events), "total_candidate_s": total, "valid_s": valid_s,
                "candidate_fraction_valid": total / valid_s if valid_s else None,
                "median_duration_s": statistics.median(e["duration_s"] for e in events) if events else None,
                "unknown_windows": sum(r["level"] == "unknown" for r in rows)})
            all_events += [{"rule_id": rule_id, "width_s": width, **e} for e in events]
            event_map[rule_id] = events
    common = intersections(valid_coverage(windows[2.0]), valid_coverage(windows[4.0]))
    width_comparison = []
    by_id = {r["rule_id"]: r for r in grid}
    for row in (r for r in grid if r["width_s"] == 2):
        other = by_id[f"4s_{row['rule_key']}"]
        a = intersections([(e["start"], e["end"]) for e in event_map[row["rule_id"]]], common)
        b = intersections([(e["start"], e["end"]) for e in event_map[other["rule_id"]]], common)
        overlap = _length(intersections(a, b))
        union = _length(a) + _length(b) - overlap
        width_comparison.append({"rule_key": row["rule_key"], "method": row["method"],
            "n_events_2s": row["n_events"], "n_events_4s": other["n_events"],
            "candidate_seconds_2s": row["total_candidate_s"], "candidate_seconds_4s": other["total_candidate_s"],
            "common_valid_seconds": _length(common), "candidate_seconds_2s_common": _length(a),
            "candidate_seconds_4s_common": _length(b),
            "time_jaccard_common": overlap / union if union else None})
    metadata.update({"n_rule_runs": len(grid), "n_rule_settings": len(width_comparison),
                     "independent_energy_comparison": comparisons,
                     "detection_accuracy": None, "detection_accuracy_reason": "No detection accuracy is estimated by this exploration.",
                     "notes": ["Energy and duration thresholds are exploratory, not validated synchrony definitions.",
                               "Duration is quantized by the output bin width; equal effective durations can give duplicate results.",
                               "No candidate crosses an unknown or below-threshold gap within either branch.",
                               "Window grid origin and upstream frame smoothing affect event boundaries.",
                               "Width agreement is not detection accuracy; no best rule is selected."]})
    destination = private_directory(output_dir) / session_id
    destination.mkdir(exist_ok=False)
    prefix = destination / session_id
    write_csv(f"{prefix}_rule_grid.csv", grid, list(grid[0]))
    write_csv(f"{prefix}_candidate_events.csv", all_events, ["rule_id", "width_s"] + EVENT_FIELDS)
    write_csv(f"{prefix}_width_comparison.csv", width_comparison, list(width_comparison[0]))
    write_csv(f"{prefix}_windows.csv", ({"width_s": width, **r} for width, rows in windows.items() for r in rows),
              ["width_s"] + WINDOW_FIELDS)
    if references is not None:
        write_csv(f"{prefix}_labelled_windows.csv", labelled_rows,
                  ["width_s", "start", "end", "mean_energy", "label", "status"])
        write_csv(f"{prefix}_energy_groups.csv", group_rows, ["width_s"] + list(comparisons["2s"]["groups"][0]))
    with Path(f"{prefix}_experiment.json").open("x", encoding="utf-8") as handle:
        json.dump(metadata, handle, indent=2, allow_nan=False)
    _write_report(Path(f"{prefix}_report.md"), metadata, grid, width_comparison)
    if plot:
        _plot_experiment(Path(f"{prefix}_exploration.html"), times, energies, windows, grid,
                         event_map, comparisons, labelled_rows, metadata)
    return metadata


def _write_report(path, metadata, grid, width_comparison):
    tag = "SYNTHETIC DEMONSTRATION - NOT RESEARCH RESULTS" if metadata["provenance"] == "synthetic" else "LOCAL EXPLORATORY RESULTS - NOT VALIDATED DETECTION"
    lines = [f"# {tag}", "", "## Methods", "",
             "Existing energy only; nonoverlapping [start,end) 2 s and 4 s arithmetic means.",
             "Single rule: mean energy >= T continuously for at least D seconds.",
             "Dual rule: (energy >= high for short duration) OR (energy >= lower for long duration).",
             "Each branch joins adjacent eligible bins before filtering by duration; accepted branches are unioned.",
             "Unknown bins and below-threshold gaps are not bridged. Missingness is retained.",
             f"Clock: {metadata['time_reference']}; valid coverage >= {metadata['min_coverage']:.0%}; grid origin {metadata['window_origin_seconds']:g} seconds.",
             f"Threshold basis: {metadata['threshold_basis']}. Same resolved energy values are used for both widths.",
             "", "## Results", "", f"{metadata['n_rule_settings']} rule settings x two widths = {metadata['n_rule_runs']} runs.",
             "Complete parameters and event intervals are in the accompanying local CSV files.",
             "No optimum is selected from unlabelled energy, and no accuracy is reported.", "",
             "| Width | Rule runs | Candidate event count range | Candidate duration range (s) |",
             "|---|---:|---:|---:|"]
    for width in (2, 4):
        subset = [r for r in grid if r["width_s"] == width]
        lines.append(f"| {width}s | {len(subset)} | {min(r['n_events'] for r in subset)}-{max(r['n_events'] for r in subset)} | "
                     f"{min(r['total_candidate_s'] for r in subset):g}-{max(r['total_candidate_s'] for r in subset):g} |")
    lines += ["", "## Independent Synchrony / Non-Synchrony Comparison", ""]
    for label, result in metadata["independent_energy_comparison"].items():
        lines.append(f"- {label}: {result['status']}.")
        if result["status"] == "not_run":
            lines.append("  Not run: no independent reference labels supplied. No negative labels inferred from silence.")
        else:
            lines.append(f"  Reference source: {result['annotation_source']}; descriptive r_pb = {result['point_biserial_r']}; excluded windows = {result['n_excluded']}.")
            lines.append("  No p-value: adjacent windows are dependent. Synthetic labels do not validate a real clinical signal.")
    lines += ["", "## Observations to check", "",
              "Compare event spans and coverage using common valid time. Width agreement is not accuracy.",
              "A lower threshold with a longer duration can retain sustained lower-energy candidates, but can also retain sustained noise.",
              "A longer duration condition filters short bursts; it cannot establish whether a burst was synchrony.",
              "", "## Limitations", ""] + ["- " + note for note in metadata["notes"]]
    lines += ["- The reference pipeline uses a 5.12 s STFT analysis window; subsequent averaging cannot recover lost short-time detail.",
              "- Full-window same-class reference coverage is required; mixed/uncertain/unlabelled windows are excluded and counted.",
              "- The constant Energy=4 is not an approved cutoff. Absolute energy units/normalization must be confirmed.",
              "- Raw data, identifying metadata, and clinical notes are not included in this report.",
              "", "## Questions for Anna", "",
              "- Which low-energy, sustained behaviours should be represented by the reference labels?",
              "- Which energy scale and candidate threshold range are appropriate?",
              "- Should a brief below-threshold dip split an event? Current setting: yes, no gap bridging.",
              "- Can we obtain independently coded positive AND explicit negative intervals with an agreed clock?",
              "- Should the upstream 5.12 s STFT window also be varied in a separate experiment?",
              "- Which Emily event format and matching rule should the later Hit/Miss evaluation use?", ""]
    with path.open("x", encoding="utf-8") as handle:
        handle.write("\n".join(lines))


def _plot_experiment(path, times, energies, windows, grid, event_map, comparisons, labelled_rows, metadata):
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots

    tag = "SYNTHETIC / NOT RESEARCH RESULTS" if metadata["provenance"] == "synthetic" else "LOCAL EXPLORATION / NOT VALIDATED SYNCHRONY"
    clock = "Seconds from curve start; not video-aligned" if metadata["time_reference"] == "curve" else f"Seconds; reference={metadata['time_reference']}"
    curve = make_subplots(rows=3, cols=1, shared_xaxes=True,
                          subplot_titles=["Existing numerical energy", "2-second window means", "4-second window means"])
    curve.add_trace(go.Scatter(x=times, y=energies, name="Existing energy", mode="lines"), row=1, col=1)
    for number, (width, rows) in enumerate(windows.items(), 2):
        x, y = [], []
        for w in rows:
            x.extend([w["start"], w["end"], None]); y.extend([w["mean_energy"], w["mean_energy"], None])
            if w["level"] == "unknown":
                curve.add_vrect(x0=w["start"], x1=w["end"], fillcolor="gray", opacity=.2, line_width=0, row=number, col=1)
        curve.add_trace(go.Scatter(x=x, y=y, name=f"{width:g}s means", mode="lines"), row=number, col=1)
    curve.update_yaxes(title_text="Linear energy index")
    curve.update_xaxes(title_text=clock, row=3, col=1)
    curve.update_layout(title=f"{tag}<br>Window comparison; gray = invalid/partial", height=760)
    # Fixed examples chosen by parameter position, never by detection outcome.
    single_key = next(r["rule_key"] for r in grid if r["width_s"] == 2 and r["method"] == "single"
                      and r["lower_threshold"] == metadata["thresholds"][min(1, len(metadata["thresholds"]) - 1)]["value"]
                      and r["min_duration_s"] == metadata["minimum_durations_seconds"][min(1, len(metadata["minimum_durations_seconds"]) - 1)])
    dual_keys = [r["rule_key"] for r in grid if r["width_s"] == 2 and r["method"] == "dual"]
    selected = [("single", single_key)] + ([("dual", dual_keys[len(dual_keys) // 2]) ] if dual_keys else [])
    timeline = make_subplots(rows=2 * len(selected), cols=1, shared_xaxes=True,
                            subplot_titles=[f"{method}, {width}s: {key}" for method, key in selected for width in (2, 4)])
    for number, (method, key, width) in enumerate(((method, key, width) for method, key in selected for width in (2, 4)), 1):
        es = event_map[f"{width}s_{key}"]
        timeline.add_trace(go.Bar(x=[e["duration_s"] for e in es], y=[.5] * len(es),
            base=[e["start"] for e in es], orientation="h", width=.65,
            marker_color="#147d92" if method == "single" else "#c58126", showlegend=False,
            customdata=[[e["start"], e["end"], e["rule_branch"]] for e in es],
            hovertemplate="%{customdata[0]}-%{customdata[1]} seconds<br>%{customdata[2]}<extra></extra>"), row=number, col=1)
        timeline.update_yaxes(range=[0, 1], showticklabels=False, row=number, col=1)
        timeline.update_xaxes(range=[times[0], times[-1] + metadata["energy_frame_period_seconds"]], row=number, col=1)
    timeline.update_xaxes(title_text=clock, row=2 * len(selected), col=1)
    timeline.update_layout(title=f"{tag}<br>Illustrative candidate events; fixed example rules, not best rules", height=200 + 150 * len(selected) * 2)
    heatmap = make_subplots(rows=1, cols=2, subplot_titles=["2s windows", "4s windows"])
    for col, width in enumerate((2, 4), 1):
        subset = [r for r in grid if r["method"] == "single" and r["width_s"] == width]
        ts = sorted(set(r["lower_threshold"] for r in subset))
        ds = metadata["minimum_durations_seconds"]
        z = [[next(r["candidate_fraction_valid"] for r in subset if r["lower_threshold"] == t and r["min_duration_s"] == d)
              for d in ds] for t in ts]
        heatmap.add_trace(go.Heatmap(x=ds, y=ts, z=z, zmin=0, zmax=1, coloraxis="coloraxis"), row=1, col=col)
        heatmap.update_xaxes(title_text="Requested minimum duration (s)", row=1, col=col)
        heatmap.update_yaxes(title_text="Energy threshold", row=1, col=col)
    heatmap.update_layout(title=f"{tag}<br>Fraction of valid time selected, not accuracy", height=430,
                          coloraxis={"colorscale": "Viridis", "cmin": 0, "cmax": 1, "colorbar": {"title": "Fraction"}})
    figures = [curve, timeline, heatmap]
    if labelled_rows:
        grouped = make_subplots(rows=1, cols=2, subplot_titles=["2s independent labels", "4s independent labels"])
        for col, width in enumerate((2, 4), 1):
            for label in ("non_synchrony", "synchrony"):
                values = [r["mean_energy"] for r in labelled_rows if r["width_s"] == width and r["label"] == label]
                grouped.add_trace(go.Box(y=values, name=label, boxpoints=False, showlegend=col == 1), row=1, col=col)
        grouped.update_layout(title=f"{tag}<br>Independent label groups; descriptive only, no IID p-value", height=440)
        figures.append(grouped)
    # Embed JS once. No CDN, external font, uploaded data, or external analytics.
    chunks = [f.to_html(full_html=False, include_plotlyjs=True if i == 0 else False) for i, f in enumerate(figures)]
    html = '<!doctype html><html><head><meta charset="utf-8"><title>Local energy-duration exploration</title></head>'
    html += '<body style="font-family:Arial,sans-serif;max-width:1400px;margin:auto"><h1>' + tag + '</h1>'
    html += '<p>Two rules: single threshold + minimum duration; higher/shorter OR lower/longer. '
    html += 'Candidate events are hypotheses. Energy=4 is not a validated cutoff. Gray windows are not classified. '
    html += 'A 4s window cannot represent a qualifying duration shorter than 4s. Common-valid-time agreement is not accuracy.</p>'
    html += ''.join(chunks) + '</body></html>'
    with path.open("x", encoding="utf-8") as handle:
        handle.write(html)


def exploration_demo(output_dir="local-results/window-exploration-synthetic", plot=False):
    """Invented energy and independently predeclared synthetic interval labels.

    This is NOT a simulation of Tom's cross-spectrum/STFT, and not clinical data.
    Labels describe the invented scenario, not thresholds applied to its energy.
    """
    root = private_directory(output_dir)
    prepared = root / "prepared"
    prepared.mkdir(exist_ok=False)
    times = [i * .48 for i in range(400)]
    rng = random.Random(20261027)
    # Intended synchrony, brief nonsynchrony bursts, and an explicit missing span.
    references = [ReferenceInterval(0, 21, "non_synchrony"), ReferenceInterval(21, 25, "synchrony"),
        ReferenceInterval(25, 60, "non_synchrony"), ReferenceInterval(60, 84, "synchrony"),
        ReferenceInterval(84, 156, "non_synchrony"), ReferenceInterval(156, 168, "synchrony"),
        ReferenceInterval(168, 192, "non_synchrony")]
    values = []
    for t in times:
        level = 1.1
        if 21 <= t < 25 or 110 <= t < 111.5:
            level = 8.2
        elif 60 <= t < 84 or 100 <= t < 102:
            level = 3.3
        elif 156 <= t < 168:
            level = 4.7
        value = max(0.0, level + .12 * math.sin(t / 3) + rng.uniform(-.15, .15))
        values.append(None if 92 <= t < 96 else value)
    source = prepared / "session_01_energy.csv"
    write_csv(source, ({"time_s": t, "energy": e} for t, e in zip(times, values)), ["time_s", "energy"])
    source.with_suffix(".timebase.json").write_text(json.dumps({"time_reference": "curve"}), encoding="utf-8")
    reference_path = prepared / "session_01_reference_labels.csv"
    write_csv(reference_path, (vars(r) for r in references), ["start", "end", "label"])
    result = explore(source, session_id="session_01", output_dir=root / "analysis", thresholds=(2, 3, 4, 6),
        reference_labels=reference_path, labels_independent=True, alignment_confirmed=True,
        annotation_time_reference="curve", annotation_source="synthetic_reference", provenance="synthetic", plot=plot)
    return result
