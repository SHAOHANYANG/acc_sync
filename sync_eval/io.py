"""Local-only allowlist extraction. Never execute HTML or include source metadata in exports."""

import base64
import csv
from datetime import datetime
import json
import math
from pathlib import Path
import re
import struct

from .core import ACTIVITY_TYPES, Event, validate_curve


def check_session_id(session_id):
    if not re.fullmatch(r"session_[0-9]{2,}", session_id):
        raise ValueError("Use a numbered session ID.")


def private_directory(path):
    path = Path(path).resolve()
    repository = Path(__file__).resolve().parent.parent
    if path.is_relative_to(repository):
        parts = path.relative_to(repository).parts
        if not parts or parts[0] not in {"local-data", "local-results", "private"}:
            raise ValueError("Use an ignored local directory or a location outside this repository.")
    path.mkdir(parents=True, exist_ok=True)
    return path


def write_csv(path, rows, fields):
    with Path(path).open("x", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="raise")
        writer.writeheader()
        writer.writerows(rows)


def seconds(value):
    """Accept numeric seconds, MM:SS, or HH:MM:SS, without dates."""
    if isinstance(value, bool):
        raise ValueError("Invalid time.")
    text = str(value).strip()
    if ":" not in text:
        result = float(text)
    else:
        parts = text.split(":")
        if len(parts) not in {2, 3}:
            raise ValueError("Invalid clock format.")
        values = [float(p) for p in parts]
        if any(v < 0 for v in values) or any(v >= 60 for v in values[1:]):
            raise ValueError("Invalid clock value.")
        result = sum(v * 60 ** i for i, v in enumerate(reversed(values)))
    if not math.isfinite(result):
        raise ValueError("Invalid time.")
    return result


def _number(value):
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, bool):
        raise ValueError("Invalid energy.")
    result = float(value)
    if math.isnan(result):
        return None
    if not math.isfinite(result):
        raise ValueError("Invalid energy.")
    return result


def _array(value):
    if isinstance(value, list):
        return value
    if not isinstance(value, dict) or "bdata" not in value:
        raise ValueError("Unsupported array representation.")
    formats = {"f8": "d", "f4": "f", "i4": "i", "i2": "h", "i1": "b",
               "u4": "I", "u2": "H", "u1": "B"}
    dtype = value.get("dtype")
    if dtype not in formats or "," in str(value.get("shape", "")):
        raise ValueError("Only one-dimensional numeric arrays are accepted.")
    raw = base64.b64decode(value["bdata"], validate=True)
    return [item[0] for item in struct.iter_unpack("<" + formats[dtype], raw)]


def plotly_energy(path, trace_name="mean cross energy"):
    """Decode data arguments of Plotly.newPlot; no browser, eval, or JS execution."""
    text = Path(path).read_text(encoding="utf-8")
    decoder, found = json.JSONDecoder(), []
    for candidate in re.finditer(r"Plotly\.newPlot\s*\(\s*", text):
        try:
            _, position = decoder.raw_decode(text, candidate.end())
            position = re.compile(r"\s*,\s*").match(text, position).end()
            traces, _ = decoder.raw_decode(text, position)
        except (ValueError, AttributeError):
            continue
        if not isinstance(traces, list):
            continue
        for trace in traces:
            if isinstance(trace, dict) and trace.get("name") == trace_name:
                found.append(trace)
    if len(found) != 1:
        raise ValueError("Exactly one selected energy trace is required.")
    return _array(found[0].get("x")), _array(found[0].get("y"))


def prepare_curve(input_path, *, kind, session_id, time_origin,
                  time_format="seconds", offset_seconds=0.0, energy_scale="linear",
                  time_column="time_s", energy_column="energy",
                  trace_name="mean cross energy", output_dir="local-data",
                  mapping_dir="private", keep_pre_video=False):
    """Whitelist two numeric columns; keep source/clock linkage only in private map.

    output_time = source_time - time_origin + offset_seconds.
    'first' uses the first curve point without claiming video alignment.
    An explicit origin declares the source clock at video time zero.
    """
    check_session_id(session_id)
    input_path = Path(input_path).resolve()
    if kind == "plotly":
        raw_times, raw_energy = plotly_energy(input_path, trace_name)
    elif kind == "csv":
        with input_path.open(newline="", encoding="utf-8-sig") as handle:
            rows = list(csv.DictReader(handle))
        raw_times = [row[time_column] for row in rows]
        raw_energy = [row[energy_column] for row in rows]
    else:
        raise ValueError("Unsupported source format.")
    if len(raw_times) != len(raw_energy) or len(raw_times) < 2:
        raise ValueError("Unpaired data.")
    if not math.isfinite(offset_seconds):
        raise ValueError("Invalid time offset.")
    time_reference = "curve" if str(time_origin).lower() == "first" else "video"
    resolved_origin = raw_times[0] if time_reference == "curve" else time_origin
    if time_format == "datetime":
        origin = datetime.fromisoformat(str(resolved_origin).replace("Z", "+00:00"))
        converted = [(datetime.fromisoformat(str(t).replace("Z", "+00:00"))
                      - origin).total_seconds() + offset_seconds for t in raw_times]
    elif time_format == "seconds":
        converted = [seconds(t) - seconds(resolved_origin) + offset_seconds for t in raw_times]
    else:
        raise ValueError("Unsupported time format.")
    values = [_number(e) for e in raw_energy]
    if energy_scale == "db":
        values = [10 ** (e / 10) if e is not None else None for e in values]
    elif energy_scale != "linear":
        raise ValueError("Declare the source energy scale.")
    pairs = [(t, e) for t, e in zip(converted, values) if keep_pre_video or t >= 0]
    times, energies = [p[0] for p in pairs], [p[1] for p in pairs]
    validate_curve(times, energies)
    destination = private_directory(output_dir) / f"{session_id}_energy.csv"
    timebase_path = destination.with_suffix(".timebase.json")
    mapping_path = private_directory(mapping_dir) / "session_map.csv"
    if destination.exists() or timebase_path.exists():
        raise FileExistsError("Choose an unused session ID.")
    mapping_fields = ["session_id", "source_path", "source_kind", "time_format",
                      "time_origin", "offset_seconds", "source_energy_scale", "time_reference"]
    existing = []
    if mapping_path.exists():
        with mapping_path.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames not in [mapping_fields, mapping_fields[:-1]]:
                raise ValueError("Unexpected private mapping format.")
            existing = list(reader)
            for row in existing:
                row.setdefault("time_reference", "video")
        if any(row["session_id"] == session_id for row in existing):
            raise ValueError("Session ID is already in use.")
    mapping_row = dict(zip(mapping_fields, [session_id, str(input_path), kind, time_format,
                                          resolved_origin, offset_seconds, energy_scale, time_reference]))
    temporary_map = mapping_path.with_suffix(".csv.tmp")
    destination_created = False
    timebase_created = False
    try:
        write_csv(temporary_map, existing + [mapping_row], mapping_fields)
        write_csv(destination, ({"time_s": t, "energy": e} for t, e in pairs),
                  ["time_s", "energy"])
        destination_created = True
        with timebase_path.open("x", encoding="utf-8") as handle:
            timebase_created = True
            json.dump({"time_reference": time_reference,
                       "video_origin_supplied": time_reference == "video"}, handle)
        temporary_map.replace(mapping_path)
    except Exception:
        if timebase_created:
            timebase_path.unlink()
        if destination_created:
            destination.unlink()
        if temporary_map.exists():
            temporary_map.unlink()
        raise
    return destination


def load_curve(path):
    with Path(path).open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != ["time_s", "energy"]:
            raise ValueError("Use only the two-column prepared energy file.")
        rows = list(reader)
    times, energies = [seconds(r["time_s"]) for r in rows], [_number(r["energy"]) for r in rows]
    validate_curve(times, energies)
    return times, energies


def load_annotations(path):
    with Path(path).open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != ["start", "end", "activity_type"]:
            raise ValueError("Use start,end,activity_type without free-text columns.")
        rows = list(reader)
    events = [Event(seconds(r["start"]), seconds(r["end"]), r["activity_type"])
              for r in rows]
    if any(e.start < 0 for e in events):
        raise ValueError("Annotations use nonnegative video seconds.")
    return events
