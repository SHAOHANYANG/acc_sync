"""Generate invented fixtures only. Never opens or writes the research data/ tree."""

import csv
from pathlib import Path


def _write(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        csv.writer(handle).writerows(rows)


def generate(root=None):
    repo = Path(__file__).resolve().parent
    root = Path(root or repo / "fake_data").resolve()
    if root.is_relative_to(repo / "data") or root.name != "fake_data":
        raise ValueError()
    for number in (90, 91):
        name = f"session_{number}.csv"
        rows = [["time_s", "energy"]]
        for i in range(128):
            t = i * .5
            value = 1.0
            if number == 90 and 8 <= t < 16:
                value = 8.0
            elif number == 90 and 24 <= t < 44:
                value = 3.0
            elif number == 91 and 16 <= t < 32:
                value = 2.5
            if number == 90 and i == 0:
                value = 7777.7777
            if number == 91 and 40 <= t < 44:
                value = None
            rows.append([t, value])
        _write(root / "valid/energy" / name, rows)
        yes = [(8, 16), (24, 44)] if number == 90 else [(16, 32)]
        no = [(0, 8), (16, 24), (44, 64)] if number == 90 else [(0, 16), (32, 64)]
        predicted = [(8, 16), (24, 44), (48, 52)] if number == 90 else [(16, 28)]
        for kind, intervals in (("annotations/synchrony", yes), ("annotations/non_synchrony", no),
                                 ("predictions", predicted)):
            _write(root / "valid" / kind / name, [["start", "end"], *intervals])
    # Deliberately non-compliant fixtures, never part of the accepted dataset.
    cases = {
        "bad_value": [["time_s", "energy"], [0, 7777.7777], [1, "ZZTEST_Qx7"]],
        "bad_header": [["time_s", "ZZTEST_Qx7"], [0, 7777.7777], [1, 1]],
        "text_column": [["time_s", "energy", "notes"], [0, 7777.7777, "ZZTEST_Qx7"]],
        "bad_time": [["time_s", "energy"], ["ZZTEST_Qx7", 7777.7777]],
        "nonfinite": [["time_s", "energy"], [0, 1], [1, "inf"]],
        "extra_field": [["time_s", "energy"], [0, 1, "ZZTEST_Qx7"]],
        "negative": [["time_s", "energy"], [0, -1], [1, 1]],
        "duplicate_time": [["time_s", "energy"], [0, 1], [0, 7777.7777]],
    }
    for case, rows in cases.items():
        _write(root / "adversarial" / case / "energy/session_99.csv", rows)
    _write(root / "adversarial/bad_filename/energy/ZZTEST_Qx7.csv", [["time_s", "energy"], [0, 7777.7777]])
    _write(root / "adversarial/non_csv/energy/session_99.bin", [["ZZTEST_Qx7", 7777.7777]])
    return root


if __name__ == "__main__":
    generate()
    print("FAKE_DATA_READY")
