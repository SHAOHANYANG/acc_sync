"""Closed output schemas: never stringify exceptions, input fields, or data rows."""

import hashlib
import json
import math
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
TOTAL_KEYS = {"sessions", "files", "rule_runs", "comparison_sessions", "evaluation_sessions"}
RULE_KEYS = {"rule_index", "width_s", "method", "low_quantile", "high_quantile", "low_threshold",
             "high_threshold", "min_duration_s", "long_duration_s", "sessions", "candidate_events", "candidate_seconds"}
GROUP_KEYS = {"width_s", "sessions", "synchrony_windows", "non_synchrony_windows", "mean_energy_difference", "r_pb"}
EVAL_KEYS = {"source", "rule_index", "width_s", "sessions", "hit", "miss", "unmatched_predictions",
             "false_positives", "precision", "recall"}
ERROR_TYPES = {"ValueError", "TypeError", "KeyError", "OSError", "FileNotFoundError", "PermissionError",
               "FileExistsError", "RuntimeError", "ChildProcessError", "TimeoutError", "Interrupted",
               "VerificationRequired", "ProtocolError"}
PUBLIC_FUNCTIONS = {
    "run_real.py": {"main", "execute", "emit", "_read_reply"},
    "check_data.py": {"check_data", "_inventory", "walk", "_timestamp", "_is_link"},
    "safe_pipeline/boundary.py": {"load_config", "validate_summary", "error_record", "fingerprint", "verified", "_numbers", "safe_output_directory"},
    "safe_pipeline/worker.py": {"main", "_fault", "_run", "_deny_external", "_audit"},
    "safe_pipeline/analysis.py": {"analyse", "_reference", "_read_rows", "_evaluate", "_render", "_groups"},
    "sync_eval/core.py": {"window_average", "validate_curve", "quantile", "evaluate_events", "interval_iou", "valid_coverage"},
    "sync_eval/io.py": {"load_curve", "write_csv", "private_directory", "check_session_id", "seconds", "_number", "protected_input"},
    "sync_eval/robustness.py": {"phase_scan", "apply_rule", "agreement", "save_phase_tables", "plot_phase"},
    "sync_eval/exploration.py": {"explore", "detect_runs", "detect_dual", "_plot_experiment", "_finite_values",
                                "_write_report", "point_biserial", "compare_reference", "_event", "_effective_duration"},
}


def error_record(exception, *, category=None):
    # Neither exception.__str__ nor repr, args, locals, source lines, or paths.
    kind = category or type(exception).__name__
    if kind not in ERROR_TYPES:
        kind = "RuntimeError"
    result = {"status": "error", "type": kind, "file": "run_real.py", "line": 0, "function": "main"}
    trace = exception.__traceback__
    while trace is not None:
        try:
            name = Path(trace.tb_frame.f_code.co_filename).resolve().relative_to(REPO).as_posix()
            function = trace.tb_frame.f_code.co_name
            if function in PUBLIC_FUNCTIONS.get(name, set()):
                result.update(file=name, line=trace.tb_lineno, function=function)
        except (ValueError, OSError):
            pass
        trace = trace.tb_next
    return result


def _numbers(row, fields):
    if not isinstance(row, dict) or set(row) != fields:
        raise ValueError()
    for value in row.values():
        if value is not None and (type(value) not in (int, float) or not math.isfinite(value)):
            raise ValueError()


def validate_summary(payload):
    if not isinstance(payload, dict):
        raise ValueError()
    status = payload.get("status")
    if status == "check":
        if set(payload) != {"status", "file_index", "column_index"} or any(
                type(payload[k]) is not int or not 0 <= payload[k] <= 1_000_000_000
                for k in ("file_index", "column_index")):
            raise ValueError()
    elif status == "error":
        if set(payload) != {"status", "type", "file", "line", "function"} \
                or payload["type"] not in ERROR_TYPES or type(payload["line"]) is not int or payload["line"] < 0 \
                or payload["function"] not in PUBLIC_FUNCTIONS.get(payload["file"], set()):
            raise ValueError()
    elif status == "ok":
        if set(payload) != {"status", "totals", "rules", "comparisons", "evaluations"}:
            raise ValueError()
        _numbers(payload["totals"], TOTAL_KEYS)
        for section, fields in (("rules", RULE_KEYS), ("comparisons", GROUP_KEYS), ("evaluations", EVAL_KEYS)):
            if not isinstance(payload[section], list) or len(payload[section]) > 8192:
                raise ValueError()
            for row in payload[section]:
                _numbers(row, fields)
    else:
        raise ValueError()
    return payload


def load_config(path):
    if path.parent.resolve() != REPO or path.is_symlink() or path.stat().st_size > 65536:
        raise ValueError()
    data = json.loads(path.read_text(encoding="utf-8"))
    allowed = {"thresholds", "quantiles", "durations", "short_durations", "long_durations", "window_origin",
               "min_coverage", "time_reference", "labels_independent", "alignment_confirmed", "annotations_complete", "min_iou"}
    if not isinstance(data, dict) or set(data) - allowed:
        raise ValueError()
    for key in ("thresholds", "quantiles", "durations", "short_durations", "long_durations"):
        if key in data and (not isinstance(data[key], list) or not data[key] or any(
                type(v) not in (int, float) or not math.isfinite(v) or v < 0 for v in data[key])):
            raise ValueError()
    for key in ("window_origin", "min_coverage", "min_iou"):
        if key in data and (type(data[key]) not in (int, float) or not math.isfinite(data[key])):
            raise ValueError()
    for key in ("labels_independent", "alignment_confirmed", "annotations_complete"):
        if type(data.get(key, False)) is not bool:
            raise ValueError()
    if data.get("time_reference", "curve") not in {"curve", "video"}:
        raise ValueError()
    return data


def fingerprint():
    # Only explicit public source/config/test paths. Never scan input/result trees.
    files = [REPO / name for name in ("AGENTS.md", ".gitignore", "run_real.py", "check_data.py",
        "run_fake_tests.py", "generate_fake_data.py", "run_synthetic_benchmark.py", "real_config.json", "fake_config.json")]
    for directory in ("safe_pipeline", "sync_eval", "tests"):
        files += sorted((REPO / directory).glob("*.py"))
    digest = hashlib.sha256()
    for path in files:
        digest.update(path.relative_to(REPO).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def verified():
    try:
        path = REPO / "results/_control/verification.json"
        for current in (path, *path.parents):
            if current == REPO:
                break
            if current.is_symlink() or (hasattr(current, "is_junction") and current.is_junction()):
                return False
        receipt = json.loads(path.read_text(encoding="utf-8"))
        return receipt.get("fingerprint") == fingerprint() and receipt.get("tests_passed", 0) > 0
    except (OSError, ValueError, TypeError):
        return False


def safe_output_directory(path):
    path = Path(path).absolute()
    if not any(path.is_relative_to(REPO / name) for name in ("results", "fake_results")):
        raise PermissionError()
    for current in (path, *path.parents):
        if current == REPO:
            break
        if current.is_symlink() or (hasattr(current, "is_junction") and current.is_junction()):
            raise PermissionError()
    path.mkdir(parents=True, exist_ok=True)
    return path
