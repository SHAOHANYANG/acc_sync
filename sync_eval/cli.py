"""Offline commands. Console output is fixed status text, never research values."""

import argparse
import json
from pathlib import Path

from .core import (Event, choose_thresholds, evaluate_events, merge_windows,
                   valid_coverage, window_average)
from .io import (check_session_id, load_annotations, load_curve, prepare_curve,
                 private_directory, write_csv)


class QuietParser(argparse.ArgumentParser):
    def error(self, message):
        # argparse's normal errors can echo an identifying argument or path.
        raise ValueError("Invalid arguments.")


def analyze(input_path, *, session_id, annotations=None, output_dir="local-results",
            widths=(2.0, 4.0), quantiles=(1 / 3, 2 / 3), thresholds=None,
            window_origin=0.0, sample_period=None, min_coverage=0.8,
            positive_levels=("high",), min_iou=0.5, annotations_complete=False,
            plot=False, time_reference="auto"):
    check_session_id(session_id)
    if not widths or len(set(widths)) != len(widths):
        raise ValueError("Use distinct window widths.")
    if not positive_levels or not set(positive_levels) <= {"low", "medium", "high"}:
        raise ValueError("Invalid positive levels.")
    times, energies = load_curve(input_path)
    if time_reference == "auto":
        sidecar = Path(input_path).with_suffix(".timebase.json")
        time_reference = json.loads(sidecar.read_text(encoding="utf-8")).get("time_reference") \
            if sidecar.exists() else "unspecified"
    if time_reference not in {"curve", "video", "unspecified"}:
        raise ValueError("Invalid time reference.")
    if annotations is not None and time_reference != "video":
        raise ValueError("Align the curve with video-based annotations before evaluation.")
    cutoffs = choose_thresholds(energies, quantiles, thresholds)
    gold = load_annotations(annotations) if annotations is not None else None
    summary = {"session_id": session_id, "analysis_energy_scale": "linear",
               "exploratory_only": True, "time_reference": time_reference,
               "window_origin_seconds": window_origin,
               "threshold_method": "manual" if thresholds else "input_curve_quantiles",
               "quantiles": list(quantiles) if thresholds is None else None,
               "low_cutoff": cutoffs[0], "high_cutoff": cutoffs[1],
               "positive_levels": list(positive_levels), "min_coverage": min_coverage,
               "windows": {}}
    computed = []
    for width in widths:
        windows = window_average(times, energies, width, origin=window_origin,
                                 sample_period=sample_period, min_coverage=min_coverage,
                                 thresholds=cutoffs)
        segments = merge_windows(windows)
        positive = merge_windows(windows, set(positive_levels))
        # Merge touching positive windows even if both medium and high are selected.
        predictions = []
        for row in positive:
            if predictions and abs(predictions[-1].end - row["start"]) < 1e-8:
                predictions[-1] = Event(predictions[-1].start, row["end"])
            else:
                predictions.append(Event(row["start"], row["end"]))
        evaluation = evaluate_events(
            predictions, gold, min_iou=min_iou, coverage=valid_coverage(windows),
            annotations_complete=annotations_complete
        ) if gold is not None else None
        label = f"{width:g}s"
        summary["windows"][label] = {"n_windows": len(windows),
                                      "n_unknown": sum(r["level"] == "unknown" for r in windows),
                                      "n_predictions": len(predictions), "evaluation": evaluation}
        computed.append((label, windows, segments, predictions))
    destination = private_directory(output_dir) / session_id
    destination.mkdir(exist_ok=False)
    for label, windows, segments, predictions in computed:
        prefix = f"{session_id}_{label}"
        write_csv(destination / f"{prefix}_windows.csv", windows,
                  ["start", "end", "mean_energy", "n_valid", "n_expected", "coverage", "partial", "level"])
        write_csv(destination / f"{prefix}_segments.csv", segments,
                  ["start", "end", "level", "n_windows"])
        write_csv(destination / f"{prefix}_predictions.csv",
                  ({"start": event.start, "end": event.end} for event in predictions), ["start", "end"])
    with (destination / f"{session_id}_summary.json").open("x", encoding="utf-8") as handle:
        json.dump(summary, handle, indent=2, allow_nan=False)
    if plot:
        _plot(destination / f"{session_id}_comparison.html", times, energies, computed, gold, cutoffs,
              time_reference)
    return summary


def _plot(destination, times, energies, computed, gold, cutoffs, time_reference):
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots

    fig = make_subplots(rows=1 + len(computed), cols=1, shared_xaxes=True,
                        subplot_titles=["Existing energy curve"] + [f"{label} window means" for label, *_ in computed])
    fig.add_trace(go.Scatter(x=times, y=energies, name="Existing energy", mode="lines"), row=1, col=1)
    colors = {"low": "#bfc7d5", "medium": "#f1b44c", "high": "#309b8a", "unknown": "#d9d9d9"}
    for number, (label, windows, _, _) in enumerate(computed, 2):
        x, y = [], []
        for w in windows:
            x.extend([w["start"], w["end"], None])
            y.extend([w["mean_energy"], w["mean_energy"], None])
            fig.add_vrect(x0=w["start"], x1=w["end"], fillcolor=colors[w["level"]],
                          opacity=0.15, line_width=0, row=number, col=1)
        fig.add_trace(go.Scatter(x=x, y=y, name=f"{label} mean", mode="lines"), row=number, col=1)
    for row in range(1, len(computed) + 2):
        for cutoff in cutoffs:
            fig.add_hline(y=cutoff, line_dash="dot", row=row, col=1)
        for event in gold or []:
            fig.add_vrect(x0=event.start, x1=event.end, line_color="#6f42c1",
                          fillcolor="rgba(0,0,0,0)", line_width=2, row=row, col=1)
        fig.update_yaxes(title_text="Linear energy index", row=row, col=1)
    time_labels = {"video": "Video time (seconds; origin supplied by researcher)",
                   "curve": "Seconds from curve start (not aligned to video)",
                   "unspecified": "Time (seconds; reference unspecified)"}
    fig.update_xaxes(title_text=time_labels[time_reference], row=len(computed) + 1, col=1)
    fig.update_layout(title="Exploratory energy segmentation (unvalidated thresholds)<br>"
                      + time_labels[time_reference], height=850)
    # Embed JS so viewing the local chart does not contact a CDN.
    fig.write_html(destination, include_plotlyjs=True, auto_open=False)


def demo(output_dir="local-results/synthetic-demo", plot=False):
    """Generate wholly fictional values and complete reference labels."""
    root = private_directory(output_dir)
    fixture_dir = root / "synthetic-input"
    fixture_dir.mkdir(exist_ok=False)
    raw = fixture_dir / "session_01_source.csv"
    rows = []
    for i in range(96):
        time = i * 0.5
        energy = [1, 9, 2, 8, 1, 10][int(time // 8)]
        rows.append({"clock": 100 + time, "value": energy,
                     "name": "FICTIONAL_PERSON", "date": "2099-01-01",
                     "notes": "SYNTHETIC_NOTES_ONLY"})
    write_csv(raw, rows, ["clock", "value", "name", "date", "notes"])
    energy_path = prepare_curve(raw, kind="csv", session_id="session_01", time_origin="100",
                                time_column="clock", energy_column="value",
                                output_dir=root / "prepared", mapping_dir=root / "private")
    annotations = fixture_dir / "session_01_annotations.csv"
    write_csv(annotations, [{"start": 8, "end": 16, "activity_type": "instrument_playing"},
                            {"start": 18, "end": 20, "activity_type": "singing"},
                            {"start": 40, "end": 48, "activity_type": "body_movement"}],
              ["start", "end", "activity_type"])
    return analyze(energy_path, session_id="session_01", annotations=annotations,
                   output_dir=root / "analysis", thresholds=(3.0, 6.0),
                   annotations_complete=True, plot=plot)


def parser():
    root = QuietParser(description="Offline energy extraction and exploratory event evaluation.")
    commands = root.add_subparsers(dest="command", required=True, parser_class=QuietParser)
    prep = commands.add_parser("prepare", help="Locally extract a two-column curve; no data is printed.")
    prep.add_argument("--input", required=True)
    prep.add_argument("--kind", choices=["csv", "plotly"], required=True)
    prep.add_argument("--session", required=True)
    prep.add_argument("--time-origin", required=True,
                      help="Use 'first' for curve-relative time, or supply a confirmed video origin.")
    prep.add_argument("--time-format", choices=["seconds", "datetime"], default="seconds")
    prep.add_argument("--offset-seconds", type=float, default=0.0)
    prep.add_argument("--energy-scale", choices=["linear", "db"], required=True)
    prep.add_argument("--time-column", default="time_s")
    prep.add_argument("--energy-column", default="energy")
    prep.add_argument("--trace-name", default="mean cross energy")
    prep.add_argument("--output-dir", default="local-data")
    prep.add_argument("--mapping-dir", default="private")
    prep.add_argument("--keep-pre-video", action="store_true")
    run = commands.add_parser("analyze", help="Compute 2 s / 4 s windows from a prepared local curve.")
    run.add_argument("--input", required=True)
    run.add_argument("--session", required=True)
    run.add_argument("--annotations")
    run.add_argument("--output-dir", default="local-results")
    run.add_argument("--widths", type=float, nargs="+", default=[2.0, 4.0])
    cuts = run.add_mutually_exclusive_group()
    cuts.add_argument("--thresholds", type=float, nargs=2)
    cuts.add_argument("--quantiles", type=float, nargs=2, default=[1 / 3, 2 / 3])
    run.add_argument("--window-origin", type=float, default=0.0)
    run.add_argument("--sample-period", type=float)
    run.add_argument("--min-coverage", type=float, default=0.8)
    run.add_argument("--positive-levels", choices=["low", "medium", "high"], nargs="+", default=["high"])
    run.add_argument("--min-iou", type=float, default=0.5)
    run.add_argument("--annotations-complete", action="store_true")
    run.add_argument("--plot", action="store_true")
    run.add_argument("--time-reference", choices=["auto", "curve", "video"], default="auto")
    fake = commands.add_parser("demo", help="Generate and analyze synthetic data only.")
    fake.add_argument("--output-dir", default="local-results/synthetic-demo")
    fake.add_argument("--plot", action="store_true")
    return root


def _safe_failure(exception, stage):
    """Expose only a fixed category and public code location, never exception text."""
    categories = {"FileNotFoundError": "FILE_NOT_FOUND", "FileExistsError": "ALREADY_EXISTS",
                  "PermissionError": "PERMISSION", "ValueError": "VALUE_ERROR",
                  "KeyError": "COLUMN_MISSING", "TypeError": "TYPE_ERROR",
                  "ModuleNotFoundError": "DEPENDENCY_MISSING", "OverflowError": "NUMBER_RANGE",
                  "JSONDecodeError": "JSON_FORMAT", "UnicodeDecodeError": "TEXT_ENCODING",
                  "OSError": "FILE_IO_ERROR", "error": "BINARY_FORMAT"}
    category = categories.get(type(exception).__name__, "INTERNAL_ERROR")
    functions = {"main", "parser", "error", "analyze", "_plot", "demo", "check_session_id",
                 "private_directory", "write_csv", "seconds", "_number", "_array",
                 "plotly_energy", "prepare_curve", "load_curve", "load_annotations",
                 "validate_curve", "quantile", "choose_thresholds", "window_average",
                 "merge_windows", "valid_coverage", "interval_iou", "evaluate_events"}
    location = "entry"
    trace = exception.__traceback__
    while trace is not None:
        frame = trace.tb_frame
        module = frame.f_globals.get("__name__")
        function = frame.f_code.co_name
        if module in {"sync_eval.cli", "sync_eval.io", "sync_eval.core"} and function in functions:
            location = f"{module}.{function}:{trace.tb_lineno}"
        trace = trace.tb_next
    print(f"FAILED [{stage}/{category}/{location}]: No input details are printed.")


def main(argv=None):
    stage = "ARGUMENTS"
    try:
        options = vars(parser().parse_args(argv))
        command = options.pop("command")
        stage = {"prepare": "PREPARE", "analyze": "ANALYZE", "demo": "DEMO"}[command]
        if command == "prepare":
            options["session_id"] = options.pop("session")
            options["input_path"] = options.pop("input")
            prepare_curve(**options)
        elif command == "analyze":
            options["session_id"] = options.pop("session")
            options["input_path"] = options.pop("input")
            analyze(**options)
        else:
            demo(**options)
    except Exception as exception:
        _safe_failure(exception, stage)
        return 2
    print("SUCCESS: files saved locally. No input values are printed.")
    return 0
