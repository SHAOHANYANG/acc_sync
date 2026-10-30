"""Invented data only. No arguments accept research input/output paths."""

from sync_eval.benchmark import run_benchmark


if __name__ == "__main__":
    destination, summary = run_benchmark()
    print(f"SYNTHETIC_ONLY sessions={summary['sessions']} rule_runs={summary['rule_runs']} matching_runs={summary['matching_runs']}")
    print(f"SYNTHETIC_REPORT {destination}")
