"""The sole research-data command. Child stdout/stderr go to the OS null sink.

Only a schema-checked numeric reply or a sanitized error reaches the console.
Do not add print/debug/logging calls outside emit(). No real figure is opened.
"""

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import uuid

from safe_pipeline.boundary import (REPO, error_record, load_config, safe_output_directory, validate_summary, verified)


class QuietParser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError()


def emit(payload):
    validate_summary(payload)
    if payload["status"] == "check":
        print(f"CHECK_FAILED: 第{payload['file_index']}个文件，第{payload['column_index']}列不合格")
        return
    if payload["status"] == "error":
        print(f"ERROR type={payload['type']} file={payload['file']} line={payload['line']} function={payload['function']}")
        return

    def number(value):
        return "NA" if value is None else format(0 if value == 0 else value, ".3g")

    print("SUCCESS " + " ".join(f"{k}={number(v)}" for k, v in sorted(payload["totals"].items())))
    for section in ("rules", "comparisons", "evaluations"):
        for row in payload[section]:
            print(section.upper() + " " + " ".join(f"{k}={number(v)}" for k, v in sorted(row.items())))


def _read_reply(path):
    if path.is_symlink() or path.stat().st_size > 2_000_000:
        raise ValueError()
    return validate_summary(json.loads(path.read_text(encoding="utf-8")))


def execute(options):
    fake = options.fake
    output = REPO / ("fake_results" if fake else "results")
    safe_output_directory(output)
    control = safe_output_directory(output / "_control")
    if not fake and not verified():
        return error_record(RuntimeError(), category="VerificationRequired")
    if options.last_summary:
        return _read_reply(control / "last_summary.json")
    if options.test_fault and not fake:
        raise ValueError()
    config_name = options.config or ("fake_config.json" if fake else "real_config.json")
    if not re.fullmatch(r"[A-Za-z0-9_-]+\.json", config_name):
        raise ValueError()
    config = load_config(REPO / config_name)
    if options.session is not None and not re.fullmatch(r"session_[0-9]{2,}", options.session):
        raise ValueError()
    temporary = control / ("gate_" + uuid.uuid4().hex)
    temporary.mkdir()
    reply = temporary / "reply.json"
    request = {"fake": fake, "check_only": options.check, "figures": not options.no_figures,
               "session": options.session, "config": config, "fault": options.test_fault}
    token = uuid.uuid4().hex
    request["token"] = token
    environment = dict(os.environ, SYNC_RUN_GATE=token, PYTHONBREAKPOINT="0", PYTHONFAULTHANDLER="0")
    try:
        completed = subprocess.run([sys.executable, "-I", "-B", str(REPO / "safe_pipeline/worker.py"), str(reply)],
            input=json.dumps(request), encoding="utf-8", stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            cwd=REPO, env=environment, timeout=600, check=False)
        if completed.returncode != 0 or not reply.is_file():
            return error_record(RuntimeError(), category="ChildProcessError")
        try:
            result = _read_reply(reply)
        except (OSError, ValueError, TypeError):
            return error_record(RuntimeError(), category="ProtocolError")
        if result["status"] == "ok":
            # Store only the already checked numeric schema; never a raw stream.
            (control / "last_summary.json").write_text(json.dumps(result, allow_nan=False), encoding="utf-8")
        return result
    finally:
        # Only our known reply and empty nonce directory; no recursive deletion.
        reply.unlink(missing_ok=True)
        try:
            temporary.rmdir()
        except OSError:
            pass


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        parser = QuietParser(description="Controlled research entry. Never displays input rows or figures.")
        parser.add_argument("--fake", action="store_true", help="Only use fake_data/valid.")
        parser.add_argument("--check", action="store_true", help="Validate before analysis; no data preview.")
        parser.add_argument("--last-summary", action="store_true", help="Re-emit only the validated numeric summary.")
        parser.add_argument("--no-figures", action="store_true")
        parser.add_argument("--config", help="Public configuration basename in this repository.")
        parser.add_argument("--session", help="Optional numbered session; all inputs are still checked first.")
        parser.add_argument("--test-fault", choices=["print_table", "fd_write", "exception", "custom_exception",
            "text_summary", "bad_number", "logging", "subprocess", "exit", "warnings", "open_figure", "network"], help=argparse.SUPPRESS)
        result = execute(parser.parse_args(argv))
    except subprocess.TimeoutExpired as exception:
        result = error_record(exception, category="TimeoutError")
    except KeyboardInterrupt as exception:
        result = error_record(exception, category="Interrupted")
    except Exception as exception:
        result = error_record(exception)
    emit(result)
    return 0 if result["status"] == "ok" else 2


if __name__ == "__main__":
    raise SystemExit(main())
