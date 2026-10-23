"""Numerical functions independent of files, identifying metadata, and networking."""

from collections import deque
from dataclasses import dataclass
import math
import statistics


ACTIVITY_TYPES = frozenset({
    "instrument_playing", "singing", "body_movement", "narrative_play", "other"
})


@dataclass(frozen=True)
class Event:
    start: float
    end: float
    activity_type: str = "other"

    def __post_init__(self):
        if not all(math.isfinite(v) for v in (self.start, self.end)):
            raise ValueError("Event bounds must be finite.")
        if self.end <= self.start:
            raise ValueError("Event end must follow start.")
        if self.activity_type not in ACTIVITY_TYPES:
            raise ValueError("Use a controlled activity code.")


def validate_curve(times, energies):
    if len(times) != len(energies) or len(times) < 2:
        raise ValueError("A curve needs at least two paired samples.")
    if any(not math.isfinite(t) for t in times):
        raise ValueError("Times must be finite.")
    if any(b <= a for a, b in zip(times, times[1:])):
        raise ValueError("Times must increase strictly; duplicates are not merged.")
    if any(e is not None and (not math.isfinite(e) or e < 0) for e in energies):
        raise ValueError("Energy must be nonnegative linear values or missing.")


def quantile(values, q):
    if not 0 <= q <= 1 or not values:
        raise ValueError("Invalid quantile.")
    ordered = sorted(values)
    position = (len(ordered) - 1) * q
    lo, hi = math.floor(position), math.ceil(position)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (position - lo)


def choose_thresholds(energies, quantiles=(1 / 3, 2 / 3), manual=None):
    """Fit once to the input curve so 2 s and 4 s share the same cutoffs."""
    if manual is not None:
        low, high = manual
    else:
        q1, q2 = quantiles
        if not 0 < q1 < q2 < 1:
            raise ValueError("Quantiles must satisfy 0 < q1 < q2 < 1.")
        values = [e for e in energies if e is not None]
        low, high = (quantile(values, q) for q in (q1, q2))
    if not all(math.isfinite(v) for v in (low, high)) or low < 0 or high <= low:
        raise ValueError("Distinct nonnegative cutoffs are required; try manual cutoffs.")
    return low, high


def window_average(times, energies, width, *, origin=0.0, sample_period=None,
                   min_coverage=0.8, thresholds=None):
    """Arithmetic mean of available frame values in [start, end) video windows.

    Missing windows are retained. No interpolation is done. Leading/trailing
    partial windows and windows with insufficient valid frames get 'unknown'.
    The final frame is assumed to cover one nominal sample period.
    """
    validate_curve(times, energies)
    if not math.isfinite(width) or width <= 0 or not math.isfinite(origin):
        raise ValueError("Invalid window width or origin.")
    if not 0 < min_coverage <= 1:
        raise ValueError("Invalid minimum coverage.")
    step = sample_period if sample_period is not None else statistics.median(
        b - a for a, b in zip(times, times[1:])
    )
    if not math.isfinite(step) or step <= 0:
        raise ValueError("Invalid sample period.")
    stop = times[-1] + step
    first_bin = math.floor((times[0] - origin) / width + 1e-9)
    last_bin = math.ceil((stop - origin) / width - 1e-9)
    if last_bin - first_bin > 1_000_000:
        raise ValueError("Too many windows; check the time scale.")
    buckets = {}
    for t, e in zip(times, energies):
        index = math.floor((t - origin) / width + 1e-9)
        if e is not None:
            buckets.setdefault(index, []).append(e)
    result = []
    for index in range(first_bin, last_bin):
        start, end = origin + index * width, origin + (index + 1) * width
        # Count nominal positions on the input frame lattice, including gaps.
        expected = max(1, math.ceil((end - times[0]) / step - 1e-8)
                       - math.ceil((start - times[0]) / step - 1e-8))
        values = buckets.get(index, [])
        coverage = min(1.0, len(values) / expected)
        partial = start < times[0] - 1e-8 or end > stop + 1e-8
        mean = statistics.fmean(values) if values else None
        level = "unknown"
        if mean is not None and not partial and coverage + 1e-9 >= min_coverage:
            if thresholds is None:
                level = "valid"
            else:
                low, high = thresholds
                level = "low" if mean < low else "high" if mean >= high else "medium"
        result.append({"start": start, "end": end, "mean_energy": mean,
                       "n_valid": len(values), "n_expected": expected,
                       "coverage": coverage, "partial": partial, "level": level})
    return result


def merge_windows(windows, levels=None):
    """Merge touching windows with the same level; never bridge missing bins."""
    segments = []
    for row in windows:
        level = row["level"]
        if level == "unknown" or (levels is not None and level not in levels):
            continue
        if segments and segments[-1]["level"] == level and math.isclose(
            segments[-1]["end"], row["start"], abs_tol=1e-8
        ):
            segments[-1]["end"] = row["end"]
            segments[-1]["n_windows"] += 1
        else:
            segments.append({"start": row["start"], "end": row["end"],
                             "level": level, "n_windows": 1})
    return segments


def valid_coverage(windows):
    intervals = []
    for row in windows:
        if row["level"] == "unknown":
            continue
        if intervals and math.isclose(intervals[-1][1], row["start"], abs_tol=1e-8):
            intervals[-1] = (intervals[-1][0], row["end"])
        else:
            intervals.append((row["start"], row["end"]))
    return intervals


def interval_iou(a, b):
    overlap = max(0.0, min(a.end, b.end) - max(a.start, b.start))
    return overlap / (max(a.end, b.end) - min(a.start, b.start)) if overlap else 0.0


def evaluate_events(predictions, references, *, min_iou=0.5,
                    coverage=None, annotations_complete=False):
    """Maximum-cardinality one-to-one IoU matching, not frame-level accuracy.

    References not fully inside valid coverage are excluded, not counted as
    misses. Predictions overlapping an excluded reference are also excluded.
    Unmatched predictions require review unless annotation completeness is known.
    """
    if not math.isfinite(min_iou) or not 0 < min_iou <= 1:
        raise ValueError("IoU must be in (0, 1].")
    coverage = sorted(coverage) if coverage is not None else None
    excluded_gold = [i for i, event in enumerate(references) if coverage is not None
                     and not any(start <= event.start + 1e-8
                                 and end >= event.end - 1e-8 for start, end in coverage)]
    eligible_gold = [i for i in range(len(references)) if i not in excluded_gold]
    excluded_pred = [i for i, event in enumerate(predictions)
                     if (coverage is not None and not any(
                         start <= event.start + 1e-8 and end >= event.end - 1e-8
                         for start, end in coverage))
                     or any(min(event.end, references[j].end)
                            > max(event.start, references[j].start) for j in excluded_gold)]
    eligible_pred = [i for i in range(len(predictions)) if i not in excluded_pred]
    edges = {g: sorted(
        [p for p in eligible_pred if interval_iou(references[g], predictions[p]) >= min_iou],
        key=lambda p: (-interval_iou(references[g], predictions[p]), p)
    ) for g in eligible_gold}
    gold_match, pred_match = {}, {}
    # Alternating-path matching avoids greedy undercounting and recursion limits.
    for initial in eligible_gold:
        queue, seen_gold, predecessor, free = deque([initial]), {initial}, {}, None
        while queue and free is None:
            g = queue.popleft()
            for p in edges[g]:
                if p in predecessor:
                    continue
                predecessor[p] = g
                if p not in pred_match:
                    free = p
                    break
                next_gold = pred_match[p]
                if next_gold not in seen_gold:
                    seen_gold.add(next_gold)
                    queue.append(next_gold)
        while free is not None:
            g = predecessor[free]
            old = gold_match.get(g)
            gold_match[g], pred_match[free] = free, g
            free = old
    hits = len(gold_match)
    by_activity = {}
    for g in eligible_gold:
        counts = by_activity.setdefault(references[g].activity_type, {"hit": 0, "miss": 0})
        counts["hit" if g in gold_match else "miss"] += 1
    matches = [{"reference_index": g, "prediction_index": p,
                "iou": interval_iou(references[g], predictions[p])}
               for g, p in sorted(gold_match.items())]
    return {"hit": hits, "miss": len(eligible_gold) - hits,
            "unmatched_predictions": len(eligible_pred) - hits,
            "false_positives": len(eligible_pred) - hits if annotations_complete else None,
            "recall": hits / len(eligible_gold) if eligible_gold else None,
            "precision": hits / len(eligible_pred)
            if annotations_complete and eligible_pred else None,
            "annotations_complete": annotations_complete, "min_iou": min_iou,
            "excluded_reference_events": len(excluded_gold),
            "excluded_predictions": len(excluded_pred),
            "by_activity": by_activity, "matches": matches,
            "miss_indices": [g for g in eligible_gold if g not in gold_match],
            "unmatched_prediction_indices": [p for p in eligible_pred if p not in pred_match]}
