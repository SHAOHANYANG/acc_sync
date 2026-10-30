"""Reproducible energy-level stress test. No study files are read.

The labels are defined by invented scenarios BEFORE energy generation. This is
not a model of raw acceleration, cross spectra, or Tom's 5.12s STFT smoothing.
Synthetic performance cannot estimate clinical accuracy or select a real rule.
"""

from collections import defaultdict
import csv
from datetime import datetime
import itertools
import json
from pathlib import Path
import random
import statistics
import uuid

from .core import Event, evaluate_events, valid_coverage, window_average
from .exploration import ReferenceInterval, compare_reference
from .robustness import PHASE_FRACTIONS, apply_rule, agreement


SCENARIOS = ("mixed_clean", "weak_near_threshold", "missing_inside_event",
             "sustained_nonsynchrony", "brief_strong", "constant_no_event")
CONTROLS = ("single_t6_d4", "single_t2_d8", "dual_t2_t6_s4_l8")


def invented_case(scenario, seed):
    if scenario not in SCENARIOS:
        raise ValueError("Unknown invented scenario.")
    rng = random.Random(20261029 + seed)
    shift = rng.uniform(-1.5, 1.5)
    weak = (20.8 + shift, 40.8 + shift)
    strong = (64.8 + shift, 68.8 + shift)
    if scenario in {"weak_near_threshold", "missing_inside_event"}:
        weak = (weak[0], weak[1] + 4)
    if scenario == "brief_strong":
        strong = (strong[0], strong[0] + 2.4)
    # Independent scenario labels, not labels produced by energy thresholding.
    reference = [weak, strong] if scenario in SCENARIOS[:3] else [strong] if scenario == "brief_strong" else []
    references = [Event(a, b) for a, b in reference]
    times, energies = [round(i * .48, 8) for i in range(200)], []
    for t in times:
        level = 1.0
        if scenario != "constant_no_event":
            if weak[0] <= t < weak[1] and scenario != "brief_strong":
                level = 2.15 if scenario == "weak_near_threshold" else 3.0
            if strong[0] <= t < strong[1]:
                level = 8.0
            # A brief low-energy nonsynchrony burst, irrespective of labels.
            if 12 + shift <= t < 14 + shift and scenario != "brief_strong":
                level = 3.0
        value = max(0, level + rng.gauss(0, .25 if scenario == "weak_near_threshold" else .12))
        energies.append(None if scenario == "missing_inside_event" and 28.8 + shift <= t < 32.8 + shift else value)
    coded, cursor = [], 0
    for event in references:
        if event.start > cursor:
            coded.append(ReferenceInterval(cursor, event.start, "non_synchrony"))
        coded.append(ReferenceInterval(event.start, event.end, "synchrony"))
        cursor = event.end
    if cursor < 96:
        coded.append(ReferenceInterval(cursor, 96, "non_synchrony"))
    return times, energies, references, coded


def benchmark_rules(width):
    rules = []
    for threshold, duration in itertools.product((2, 4, 6), (2, 4, 8, 12)):
        rules.append({"rule_key": f"single_t{threshold}_d{duration}", "method": "single",
            "lower_threshold": threshold, "upper_threshold": None,
            "min_duration_s": duration, "long_duration_s": None})
    for (low, high), short, long in itertools.product(itertools.combinations((2, 4, 6), 2), (2, 4), (8, 12)):
        rules.append({"rule_key": f"dual_t{low}_t{high}_s{short}_l{long}", "method": "dual",
            "lower_threshold": low, "upper_threshold": high,
            "min_duration_s": short, "long_duration_s": long})
    return [dict(rule, width_s=width, rule_id=f"{width}s_{rule['rule_key']}") for rule in rules]


def _write(path, rows, fields=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fields or list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _metrics(hit, miss, fp):
    return {"precision": hit / (hit + fp) if hit + fp else None,
            "recall": hit / (hit + miss) if hit + miss else None,
            "f1": 2 * hit / (2 * hit + miss + fp) if 2 * hit + miss + fp else None}


def _summarise(rows):
    grouped = defaultdict(list)
    for row in rows:
        if row["rule_key"] in CONTROLS:
            grouped[row["scenario"], row["width_s"], row["rule_key"]].append(row)
    output = []
    for (scenario, width, rule), records in sorted(grouped.items()):
        hit, miss, fp = (sum(r[k] for r in records) for k in ("hit", "miss", "false_positives"))
        overlaps = [r["phase_jaccard"] for r in records if r["phase_fraction"] and r["phase_jaccard"] is not None]
        output.append({"scenario": scenario, "width_s": width, "rule_key": rule,
            "replicate_grid_runs": len(records), "trajectories": len({r["seed"] for r in records}),
            "hit": hit, "miss": miss, "false_positives": fp,
            "excluded_references": sum(r["excluded_references"] for r in records),
            **_metrics(hit, miss, fp), "minimum_phase_jaccard": min(overlaps) if overlaps else None,
            "mean_candidate_seconds": statistics.fmean(r["candidate_seconds"] for r in records)})
    return output


def run_benchmark(*, seeds=8, figures=True, repo=None):
    """Fixed synthetic-only roots, no input path option. Returns fake summaries."""
    repo = Path(repo or Path(__file__).resolve().parents[1]).resolve()
    if not 1 <= seeds <= 32:
        raise ValueError("Use 1 to 32 invented replicates.")
    run_id = "run_" + datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:8]
    # Reject redirects before generating or writing anything; never enter data/.
    for name in ("fake_data", "fake_results"):
        for folder in (repo, repo / name, repo / name / "benchmark"):
            if folder.is_symlink() or (hasattr(folder, "is_junction") and folder.is_junction()):
                raise PermissionError("Synthetic destination is linked.")
    source = repo / "fake_data/benchmark" / run_id
    destination = repo / "fake_results/benchmark" / run_id
    source.mkdir(parents=True, exist_ok=False)
    destination.mkdir(parents=True, exist_ok=False)
    rows, comparison_rows, match_rows = [], [], []
    session_number = 92
    for scenario, seed in itertools.product(SCENARIOS, range(seeds)):
        times, energies, reference, coded = invented_case(scenario, seed)
        session_id = f"session_{session_number}"
        session_number += 1
        _write(source / "energy" / f"{session_id}.csv", [{"time_s": t, "energy": e} for t, e in zip(times, energies)])
        for label in ("synchrony", "non_synchrony"):
            _write(source / "annotations" / label / f"{session_id}.csv",
                [{"start": r.start, "end": r.end} for r in coded if r.label == label], ["start", "end"])
        for width in (2, 4):
            grids = {fraction: window_average(times, energies, width, origin=fraction * width)
                     for fraction in PHASE_FRACTIONS}
            for fraction, windows in grids.items():
                comparison, labelled = compare_reference(windows, coded, labels_independent=True,
                    alignment_confirmed=True, time_reference="curve", annotation_time_reference="curve")
                group = {g["label"]: g for g in comparison["groups"]}
                yes, no = group["synchrony"], group["non_synchrony"]
                comparison_rows.append({"session": session_id, "scenario": scenario, "seed": seed,
                    "width_s": width, "phase_fraction": fraction, "n_synchrony": yes["n_windows"],
                    "n_non_synchrony": no["n_windows"], "excluded_windows": comparison["n_excluded"],
                    "mean_energy_difference": yes["mean_energy"] - no["mean_energy"] if yes["n_windows"] and no["n_windows"] else None,
                    "r_pb": comparison["point_biserial_r"]})
                for rule in benchmark_rules(width):
                    events = apply_rule(windows, rule)
                    result = evaluate_events([Event(e["start"], e["end"]) for e in events], reference,
                        coverage=valid_coverage(windows), annotations_complete=True)
                    baseline = apply_rule(grids[0], rule)
                    overlap = agreement(baseline, events, valid_coverage(grids[0]), valid_coverage(windows))
                    record = {"session": session_id, "scenario": scenario, "seed": seed,
                        "width_s": width, "phase_fraction": fraction, "rule_key": rule["rule_key"],
                        "candidate_events": len(events), "candidate_seconds": sum(e["duration_s"] for e in events),
                        "hit": result["hit"], "miss": result["miss"], "false_positives": result["false_positives"],
                        "excluded_references": result["excluded_reference_events"],
                        "phase_jaccard": overlap["time_jaccard_common"],
                        "effective_min_duration_s": width * max(1, -(-rule["min_duration_s"] // width)),
                        **_metrics(result["hit"], result["miss"], result["false_positives"])}
                    rows.append(record)
                    if rule["rule_key"] in CONTROLS:
                        for min_iou in (.25, .5, .75):
                            match = evaluate_events([Event(e["start"], e["end"]) for e in events], reference,
                                coverage=valid_coverage(windows), annotations_complete=True, min_iou=min_iou)
                            match_rows.append({k: record[k] for k in ("session", "scenario", "seed", "width_s", "phase_fraction", "rule_key")} |
                                {"min_iou": min_iou, "hit": match["hit"], "miss": match["miss"],
                                 "false_positives": match["false_positives"], "excluded_references": match["excluded_reference_events"]})
    summary = _summarise(rows)
    _write(destination / "all_parameter_runs.csv", rows)
    _write(destination / "prespecified_rules.csv", summary)
    _write(destination / "independent_energy_comparison.csv", comparison_rows)
    _write(destination / "matching_sensitivity.csv", match_rows)
    metadata = {"synthetic_only": True, "seed_base": 20261029, "replicates_per_scenario": seeds,
        "scenarios": list(SCENARIOS), "prespecified_rules": list(CONTROLS), "sessions": len(SCENARIOS) * seeds,
        "rule_runs": len(rows), "matching_runs": len(match_rows), "frame_period_s": .48,
        "origin_fractions": list(PHASE_FRACTIONS), "min_coverage": .8, "default_min_iou": .5,
        "thresholds": [2, 4, 6], "durations": [2, 4, 8, 12], "short_durations": [2, 4], "long_durations": [8, 12],
        "models_upstream_stft": False, "real_detection_accuracy": None, "summary": summary}
    (destination / "benchmark.json").write_text(json.dumps(metadata, indent=2, allow_nan=False), encoding="utf-8")
    _report(destination / "report.md", metadata)
    if figures:
        _plot(destination, summary, rows, match_rows)
    return destination, metadata


def _report(path, metadata):
    fmt = lambda value: "NA" if value is None else f"{value:.3g}"
    lines = ["# 假数据压力实验：不能作为临床准确率", "",
        f"共{metadata['sessions']}条虚构能量曲线、{metadata['rule_runs']}次规则运行。",
        "每条曲线96秒，点间隔0.48秒；2/4秒均值、4种窗口起点、24组规则。",
        "标签由情景预先设定，不是从能量阈值生成。阈值2/4/6是人为实验参数，不能迁移为真实标准。", "",
        "比较3个事先指定的规则，未按测试结果挑最好的一组：T6持续4秒、T2持续8秒、T6持续4秒或T2持续8秒。",
        f"表中按{metadata['replicates_per_scenario']}条曲线×4个起点汇总，同一曲线反复使用，不能当成独立受试者；不计算p值或置信区间。", "",
        "|情景|窗口|规则|Hit|Miss|额外预测FP|排除的参考事件|Recall|Precision|最小起点Jaccard|",
        "|---|---:|---|---:|---:|---:|---:|---:|---:|---:|"]
    for r in metadata["summary"]:
        lines.append("|" + "|".join(str(r[k]) if k in {"scenario", "rule_key"} else fmt(r[k]) for k in
            ("scenario", "width_s", "rule_key", "hit", "miss", "false_positives", "excluded_references", "recall", "precision", "minimum_phase_jaccard")) + "|")
    lines += ["", "需要如何解释", "",
        "- mixed_clean与sustained_nonsynchrony用同一随机种子生成完全相同的能量，但预设同步标签不同。仅靠能量和时长无法区分它们。",
        "- 缺失片段贯穿参考事件时，该事件标为不可评估，不静默删掉，也不记为正确或漏检；同时报告排除数量。",
        "- 起点变化只改变分窗，不改变原始时间或标注时间。Jaccard只在共同有效时间比较，空/空为NA。",
        "- IoU=0.25/0.5/0.75只展示事件匹配规则的敏感性，不用来寻找有利的评分标准。",
        "- 2秒最短时长在4秒窗下实际为4秒。主比较的4秒/8秒规则有效时长一致，完整扫描保留量化差异。",
        "- 这是能量层面的模型，未模拟Tom的5.12秒STFT或真实噪声分布。不能据此宣称真实2秒分辨率或临床检测准确率。", "",
        "向Anna确认：弱持续运动应如何独立标注？长时间非同步运动如何作负对照？缺失事件如何报告？允许多大边界误差？", ""]
    path.write_text("\n".join(lines), encoding="utf-8")


def _plot(destination, summary, rows, matching):
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots

    figure = make_subplots(rows=1, cols=2, subplot_titles=["2-second bins", "4-second bins"])
    for col, width in enumerate((2, 4), 1):
        for rule in CONTROLS:
            records = [r for r in summary if r["width_s"] == width and r["rule_key"] == rule]
            figure.add_trace(go.Bar(x=[r["scenario"] for r in records], y=[r["recall"] for r in records],
                name=rule, legendgroup=rule, showlegend=col == 1), row=1, col=col)
    figure.update_yaxes(range=[0, 1], title_text="Synthetic event recall; blank = no eligible events")
    figure.update_layout(title="SYNTHETIC ONLY — prespecified rules across four grid origins", barmode="group", height=640)
    fp = go.Figure()
    for rule in CONTROLS:
        records = [r for r in summary if r["rule_key"] == rule]
        fp.add_bar(x=[f"{r['scenario']} / {r['width_s']}s" for r in records],
            y=[r["false_positives"] / r["replicate_grid_runs"] for r in records], name=rule)
    fp.update_layout(title="SYNTHETIC ONLY — mean unmatched predictions per trajectory/grid",
                     yaxis_title="False positives with complete invented labels", barmode="group", height=620)
    jitter = go.Figure()
    for width in (2, 4):
        for rule in CONTROLS:
            records = [r for r in rows if r["rule_key"] == rule and r["width_s"] == width and r["phase_fraction"]]
            jitter.add_trace(go.Box(x=[r["scenario"] for r in records], y=[r["phase_jaccard"] for r in records],
                name=f"{width}s {rule}", boxpoints=False))
    jitter.update_layout(title="SYNTHETIC ONLY — bin-origin stability; empty candidate unions excluded",
                         yaxis_title="Common-valid-time Jaccard, not accuracy", boxmode="group", height=650)
    matching_figure = go.Figure()
    for width in (2, 4):
        for rule in CONTROLS:
            values = []
            for iou in (.25, .5, .75):
                selected = [r for r in matching if r["rule_key"] == rule and r["width_s"] == width and r["min_iou"] == iou]
                hit, miss = sum(r["hit"] for r in selected), sum(r["miss"] for r in selected)
                values.append(hit / (hit + miss) if hit + miss else None)
            matching_figure.add_scatter(x=[.25, .5, .75], y=values, mode="lines+markers", name=f"{width}s {rule}")
    matching_figure.update_layout(title="SYNTHETIC ONLY — sensitivity to event-matching IoU",
        xaxis_title="Minimum IoU", yaxis_title="Pooled synthetic recall; repeated grids are dependent", height=540)
    figures = (figure, fp, jitter, matching_figure)
    chunks = [f.to_html(full_html=False, include_plotlyjs=True if index == 0 else False) for index, f in enumerate(figures)]
    (destination / "benchmark.html").write_text('<!doctype html><html><head><meta charset="utf-8"></head><body>'
        '<h1>Synthetic stress test — no clinical data or clinical accuracy</h1><p>Energy-level model only. '
        'No upstream STFT simulation. Fixed rule examples; no best rule selected.</p>' + ''.join(chunks) + '</body></html>', encoding="utf-8")
    _static_plot(destination, summary, rows, matching)


def _static_plot(destination, summary, rows, matching):
    import os
    # Keep the purely synthetic font cache in its ignored output directory.
    os.environ["MPLCONFIGDIR"] = str(destination / "_matplotlib")
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    colors = {CONTROLS[0]: "#425e9c", CONTROLS[1]: "#c18124", CONTROLS[2]: "#178678"}
    names = {CONTROLS[0]: "High / short (6, 4s)", CONTROLS[1]: "Lower / long (2, 8s)", CONTROLS[2]: "Either branch"}
    fig, axes = plt.subplots(2, 2, figsize=(13, 9.5), constrained_layout=True)
    for index, rule in enumerate(CONTROLS):
        for j, scenario in enumerate(("mixed_clean", "weak_near_threshold")):
            for k, width in enumerate((2, 4)):
                r = next(r for r in summary if r["rule_key"] == rule and r["width_s"] == width and r["scenario"] == scenario)
                x = j * 2 + k + (index - 1) * .24
                axes[0, 0].bar(x, r["recall"], width=.22, color=colors[rule], label=names[rule] if j == k == 0 else None)
    axes[0, 0].set(xticks=range(4), xticklabels=["Clean\n2s", "Clean\n4s", "Near threshold\n2s", "Near threshold\n4s"],
                   ylim=(0, 1.08), ylabel="Synthetic event recall", title="A  Weak + strong invented events")
    axes[0, 0].legend(loc="upper left", fontsize=8)
    for index, rule in enumerate(CONTROLS):
        for k, width in enumerate((2, 4)):
            r = next(r for r in summary if r["rule_key"] == rule and r["width_s"] == width and r["scenario"] == "sustained_nonsynchrony")
            axes[0, 1].bar(k + (index - 1) * .24, r["false_positives"] / r["replicate_grid_runs"], width=.22, color=colors[rule])
    axes[0, 1].set(xticks=[0, 1], xticklabels=["2s bins", "4s bins"], ylabel="Mean false positives per trajectory / grid",
                   title="B  Same energy, no synchrony in scenario labels", ylim=(0, 2.25))
    for width, marker in ((2, "o"), (4, "s")):
        for rule in (CONTROLS[0], CONTROLS[2]):
            means = []
            for fraction in PHASE_FRACTIONS:
                values = [r["phase_jaccard"] for r in rows if r["scenario"] == "mixed_clean" and
                    r["width_s"] == width and r["rule_key"] == rule and r["phase_fraction"] == fraction and r["phase_jaccard"] is not None]
                means.append(statistics.fmean(values) if values else float("nan"))
            axes[1, 0].plot(PHASE_FRACTIONS, means, marker=marker, color=colors[rule], linestyle="-" if width == 2 else "--",
                            label=f"{width}s {names[rule]}")
    axes[1, 0].set(xlabel="Bin-origin shift / bin width", ylabel="Mean candidate time Jaccard vs baseline",
                   ylim=(-.03, 1.05), title="C  Origin changes, same energy and thresholds")
    axes[1, 0].legend(fontsize=8)
    for width, marker in ((2, "o"), (4, "s")):
        for rule in (CONTROLS[0], CONTROLS[2]):
            recalls = []
            for iou in (.25, .5, .75):
                selected = [r for r in matching if r["width_s"] == width and r["rule_key"] == rule and r["min_iou"] == iou]
                hit, miss = sum(r["hit"] for r in selected), sum(r["miss"] for r in selected)
                recalls.append(hit / (hit + miss) if hit + miss else float("nan"))
            axes[1, 1].plot([.25, .5, .75], recalls, marker=marker, color=colors[rule], linestyle="-" if width == 2 else "--",
                            label=f"{width}s {names[rule]}")
    axes[1, 1].set(xlabel="Minimum event-matching IoU", ylabel="Pooled synthetic event recall",
                   ylim=(-.03, 1.05), title="D  Matching tolerance changes the reported result")
    axes[1, 1].legend(fontsize=8)
    for ax in axes.flat:
        ax.spines[["top", "right"]].set_visible(False)
        ax.grid(axis="y", alpha=.17)
        ax.set_axisbelow(True)
    fig.suptitle("SYNTHETIC STRESS TEST — no clinical data or clinical accuracy\n"
        "48 invented trajectories; repeated origins are dependent; no rule selected as best", fontsize=14)
    fig.savefig(destination / "synthetic_findings.png", dpi=180, facecolor="white")
    fig.savefig(destination / "synthetic_findings.pdf", facecolor="white")
    plt.close(fig)
