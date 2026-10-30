"""Window-origin sensitivity on in-memory energy; no input files or printing.

These are algorithm stability diagnostics, never accuracy estimates. Thresholds
stay fixed while only the bin origin changes. No preferred origin is selected.
"""

import csv
import math

from .core import valid_coverage, window_average
from .exploration import detect_dual, detect_runs, intersections


PHASE_FRACTIONS = (0, .25, .5, .75)


def apply_rule(windows, rule):
    if rule["method"] == "single":
        return detect_runs(windows, rule["lower_threshold"], rule["min_duration_s"])
    if rule["method"] == "dual":
        return detect_dual(windows, rule["lower_threshold"], rule["upper_threshold"],
                           rule["min_duration_s"], rule["long_duration_s"])
    raise ValueError("Unsupported rule.")


def agreement(events_a, events_b, coverage_a, coverage_b):
    common = intersections(coverage_a, coverage_b)
    a = intersections([(e["start"], e["end"]) for e in events_a], common)
    b = intersections([(e["start"], e["end"]) for e in events_b], common)
    length = lambda spans: sum(end - start for start, end in spans)
    overlap = length(intersections(a, b))
    union = length(a) + length(b) - overlap
    return {"common_valid_seconds": length(common), "baseline_candidate_seconds_common": length(a),
            "shifted_candidate_seconds_common": length(b),
            "time_jaccard_common": overlap / union if union > 1e-9 else None}


def phase_scan(times, energies, grid, *, origin=0, min_coverage=.8):
    """Quarter-bin shifts, including baseline; each comparison uses shared coverage.

    Equality in the empty/empty case is undefined (None), not perfect detection.
    Missing/partial bins remain invalid. The origin is a bin-grid parameter,
    not a change in the timestamps or external time alignment.
    """
    rows = []
    for width in (2, 4):
        windows = {fraction: window_average(times, energies, width,
                    origin=origin + fraction * width, min_coverage=min_coverage)
                   for fraction in PHASE_FRACTIONS}
        for rule in (r for r in grid if r["width_s"] == width):
            baseline = apply_rule(windows[0], rule)
            for fraction, shifted in windows.items():
                events = apply_rule(shifted, rule)
                rows.append({"rule_id": rule["rule_id"], "rule_key": rule["rule_key"],
                    "method": rule["method"], "width_s": width, "phase_fraction": fraction,
                    "window_origin_s": origin + fraction * width,
                    "requested_min_duration_s": rule["min_duration_s"],
                    "effective_min_duration_s": width * max(1, math.ceil(rule["min_duration_s"] / width - 1e-9)),
                    "candidate_events": len(events), "candidate_seconds": sum(e["duration_s"] for e in events),
                    "valid_seconds": sum(b - a for a, b in valid_coverage(shifted)),
                    **agreement(baseline, events, valid_coverage(windows[0]), valid_coverage(shifted))})
    return rows


def save_phase_tables(directory, session_id, rows):
    path = directory / f"{session_id}_origin_sensitivity.csv"
    with path.open("x", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    (directory / f"{session_id}_origin_readme.md").write_text(
        "# Window-origin sensitivity — exploratory only\n\n"
        "Each rule is evaluated at offsets 0, 1/4, 1/2 and 3/4 of its bin width. "
        "Thresholds and requested durations stay fixed. Origin 0 here means the configured baseline. "
        "This does not shift timestamps or establish alignment with annotation/video.\n\n"
        "Time Jaccard compares candidate intervals only within time valid under both grids. "
        "No candidates in either grid gives NA, not 100% accuracy. Candidate counts/durations "
        "refer to each full valid grid and can have different edge coverage. "
        "Small Jaccard indicates sensitivity to arbitrary bin placement, not poor clinical accuracy.\n\n"
        "Requested 2 seconds becomes at least 4 seconds in the 4-second grid. "
        "Use requested durations of 4/8/12 seconds for comparisons with equal effective duration. "
        "Do not select an origin or threshold using unlabelled energy alone.\n",
        encoding="utf-8")


def plot_phase(path, rows, *, fake):
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots

    figure = make_subplots(rows=1, cols=2, subplot_titles=["2s bin grid", "4s bin grid"])
    for column, width in enumerate((2, 4), 1):
        subset = [r for r in rows if r["width_s"] == width]
        keys = list(dict.fromkeys(r["rule_key"] for r in subset))
        lookup = {(r["rule_key"], r["phase_fraction"]): r for r in subset}
        figure.add_trace(go.Heatmap(x=[f"+{f * width:g}s" for f in PHASE_FRACTIONS], y=keys,
            z=[[lookup[key, fraction]["time_jaccard_common"] for fraction in PHASE_FRACTIONS] for key in keys],
            coloraxis="coloraxis", hoverongaps=False,
            hovertemplate="%{y}<br>Grid shift %{x}<br>Candidate overlap %{z:.3f}<extra></extra>"), row=1, col=column)
    tag = "SYNTHETIC — NOT RESEARCH RESULTS" if fake else "EXPLORATORY CANDIDATES — NOT VALIDATED SYNCHRONY"
    figure.update_layout(title=f"{tag}<br>Window-origin sensitivity; common-valid-time Jaccard",
        height=max(500, 23 * len(keys) + 220), coloraxis={"cmin": 0, "cmax": 1, "colorscale": "Viridis"})
    figure.update_xaxes(title_text="Shift from configured bin origin")
    html = figure.to_html(full_html=True, include_plotlyjs=True)
    html = html.replace("<body>", "<body><p>Blank = no candidate union; not perfect detection. "
        "Thresholds stay fixed. This changes bin placement, not the annotation clock. "
        "Do not choose the best origin from this chart.</p>", 1)
    with path.open("x", encoding="utf-8") as handle:
        handle.write(html)
