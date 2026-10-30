"""Strict CSV input contract. Invoke for research data ONLY through run_real.py.

No archive/parquet/HTML/JSON input, names, note columns, linkage maps or symlinks.
The only public failure metadata is a 1-based file and column ordinal (0=schema).
"""

import csv
from dataclasses import dataclass, field
import math
from pathlib import Path
import re


KINDS = {
    ("energy",): "energy",
    ("annotations", "synchrony"): "synchrony",
    ("annotations", "non_synchrony"): "non_synchrony",
    ("annotations", "uncertain"): "uncertain",
    ("predictions",): "predictions",
}
HEADERS = {"energy": ("time_s", "energy"), "interval": ("start", "end")}
SESSION = re.compile(r"session_[0-9]{2,}\.csv\Z", re.ASCII)


class InvalidData(Exception):
    def __init__(self, file_index, column_index):
        super().__init__()
        self.file_index = int(file_index)
        self.column_index = int(column_index)


@dataclass
class Session:
    session_id: str
    energy_path: Path | None = None
    times: list = field(default_factory=list)
    energies: list = field(default_factory=list)
    synchrony: list = field(default_factory=list)
    non_synchrony: list = field(default_factory=list)
    uncertain: list = field(default_factory=list)
    predictions: list = field(default_factory=list)
    present: set = field(default_factory=set)


def _timestamp(value):
    # Only relative seconds or duration clocks. No date/identity strings.
    text = value.strip()
    if ":" not in text:
        result = float(text)
    else:
        parts = text.split(":")
        if len(parts) not in (2, 3):
            raise ValueError()
        numbers = [float(x) for x in parts]
        if any(x < 0 for x in numbers) or any(x >= 60 for x in numbers[1:]):
            raise ValueError()
        result = sum(v * 60 ** i for i, v in enumerate(reversed(numbers)))
    if not math.isfinite(result) or result < 0:
        raise ValueError()
    return result


def _is_link(path):
    return path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction())


def _inventory(root):
    """Never traverse an unknown directory or follow a link; never open non-CSV."""
    if not root.is_dir() or _is_link(root):
        raise InvalidData(0, 0)
    found = []

    def walk(directory, relative=()):
        for path in sorted(directory.iterdir(), key=lambda p: p.name):
            index = len(found) + 1
            if _is_link(path):
                raise InvalidData(index, 0)
            if path.is_dir():
                next_parts = relative + (path.name,)
                if not any(parts[:len(next_parts)] == next_parts for parts in KINDS):
                    raise InvalidData(index, 0)
                walk(path, next_parts)
            elif path.is_file():
                if relative not in KINDS or not SESSION.fullmatch(path.name):
                    raise InvalidData(index, 0)
                found.append((path, KINDS[relative]))
            else:
                raise InvalidData(index, 0)
    walk(root)
    if not found:
        raise InvalidData(0, 0)
    return found


def check_data(root):
    files = _inventory(Path(root))
    sessions = {}
    for index, (path, kind) in enumerate(files, 1):
        session = sessions.setdefault(path.stem, Session(path.stem))
        session.present.add(kind)
        header = HEADERS["energy" if kind == "energy" else "interval"]
        parsed = []
        try:
            with path.open(newline="", encoding="utf-8-sig") as handle:
                reader = csv.reader(handle, strict=True)
                actual = next(reader, [])
                if len(actual) != len(header):
                    raise InvalidData(index, min(len(actual), len(header)) + 1)
                for col, (a, b) in enumerate(zip(actual, header), 1):
                    if a != b:
                        raise InvalidData(index, col)
                for row in reader:
                    if len(row) != len(header):
                        raise InvalidData(index, min(len(row), len(header)) + 1)
                    converted = []
                    for col, value in enumerate(row, 1):
                        try:
                            if kind == "energy" and col == 2:
                                number = None if not value.strip() or value.strip().lower() == "nan" else float(value)
                                if number is not None and (not math.isfinite(number) or number < 0):
                                    raise ValueError()
                            else:
                                number = _timestamp(value)
                        except (ValueError, OverflowError):
                            raise InvalidData(index, col) from None
                        converted.append(number)
                    if kind == "energy" and parsed and converted[0] <= parsed[-1][0]:
                        raise InvalidData(index, 1)
                    if kind != "energy" and converted[1] <= converted[0]:
                        raise InvalidData(index, 2)
                    parsed.append(tuple(converted))
        except InvalidData:
            raise
        except (UnicodeError, csv.Error, OSError):
            raise InvalidData(index, 0) from None
        if kind == "energy":
            if len(parsed) < 2:
                raise InvalidData(index, 0)
            session.energy_path = path
            session.times = [r[0] for r in parsed]
            session.energies = [r[1] for r in parsed]
        else:
            setattr(session, kind, sorted(parsed))
    for index, (path, kind) in enumerate(files, 1):
        session = sessions[path.stem]
        if session.energy_path is None:
            raise InvalidData(index, 0)
        intervals = sorted((a, b) for label in ("synchrony", "non_synchrony", "uncertain")
                           for a, b in getattr(session, label))
        if any(b[0] < a[1] - 1e-8 for a, b in zip(intervals, intervals[1:])):
            raise InvalidData(index, 1)
    return list(sessions.values()), len(files)


if __name__ == "__main__":
    # Do not offer a second command that could open research inputs.
    print("Use run_real.py.")
    raise SystemExit(2)
