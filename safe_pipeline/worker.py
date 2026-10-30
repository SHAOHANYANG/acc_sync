"""Silent child, launched only by run_real.py. Never writes a raw console log."""

import hmac
import json
import logging
import os
from pathlib import Path
import sys

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from safe_pipeline.boundary import error_record, validate_summary, verified


def _deny_external(*args, **kwargs):
    raise PermissionError()


def _audit(event, args):
    if event in {"socket.connect", "socket.bind", "socket.getaddrinfo"}:
        raise PermissionError()


def _fault(name, result):
    # Deliberately unsafe behaviour is permitted ONLY on the invented fixtures.
    text = "ZZTEST_Qx7 7777.7777\ntime_s,energy\n0,7777.7777"
    if name == "print_table":
        text += "\n" + (REPO / "fake_data/valid/energy/session_90.csv").read_text(encoding="utf-8")
        print(text)
        sys.__stdout__.write(text)
        sys.stderr.write(text)
    elif name == "fd_write":
        os.write(1, text.encode()); os.write(2, text.encode())
    elif name == "exception":
        raise ValueError(text)
    elif name == "custom_exception":
        kind = type("ZZTEST_Qx7", (Exception,), {})
        raise kind(text)
    elif name == "text_summary":
        result["rules"][0]["candidate_events"] = text
    elif name == "bad_number":
        result["rules"][0]["candidate_seconds"] = float("nan")
    elif name == "logging":
        logging.error(text)
        handler = logging.FileHandler(REPO / "SHOULD_NOT_EXIST.log")
        handler.handle(logging.LogRecord("test", logging.ERROR, "", 0, text, (), None))
    elif name == "subprocess":
        import subprocess
        subprocess.run([sys.executable, "-c", "import os; os.write(1,b'ZZTEST_Qx7 7777.7777')"], check=True)
    elif name == "warnings":
        import warnings
        warnings.warn(text)
    elif name == "exit":
        os._exit(17)
    elif name == "open_figure":
        import webbrowser
        webbrowser.open("fake_results/SHOULD_NOT_OPEN.html")
    elif name == "network":
        import socket
        with socket.socket() as connection:
            connection.connect(("127.0.0.1", 1))


def _run(request):
    from check_data import InvalidData, check_data
    from sync_eval import io as permitted_io
    from safe_pipeline.analysis import analyse
    fake = request["fake"]
    if not fake and not verified():
        return error_record(RuntimeError(), category="VerificationRequired")
    permitted_io._RUN_REAL_ACTIVE = True
    root = REPO / ("fake_data/valid" if fake else "data")
    try:
        sessions, count = check_data(root)
    except InvalidData as exception:
        return {"status": "check", "file_index": exception.file_index, "column_index": exception.column_index}
    if request["session"] is not None:
        sessions = [s for s in sessions if s.session_id == request["session"]]
        if not sessions:
            raise ValueError()
    if request["check_only"]:
        return {"status": "ok", "totals": {"sessions": len(sessions), "files": count, "rule_runs": 0,
            "comparison_sessions": 0, "evaluation_sessions": 0}, "rules": [], "comparisons": [], "evaluations": []}
    output = REPO / ("fake_results" if fake else "results")
    result = analyse(sessions, count, output, request["config"], fake=fake, figures=request["figures"])
    if fake and request["fault"]:
        _fault(request["fault"], result)
    return validate_summary(result)


def main():
    try:
        request = json.loads(sys.stdin.read(65536))
        if not hmac.compare_digest(str(request.pop("token", "")), os.environ.pop("SYNC_RUN_GATE", "invalid")):
            return 2
        reply = Path(sys.argv[1]).resolve()
        output = REPO / ("fake_results" if request["fake"] else "results")
        if not reply.is_relative_to(output / "_control") or reply.name != "reply.json":
            return 2
        # No raw file logging. Existing analysis has no persistent log output.
        class DiscardFileLog(logging.NullHandler):
            def __init__(self, *args, **kwargs):
                super().__init__()
        logging.FileHandler = DiscardFileLog
        import webbrowser
        webbrowser.open = webbrowser.open_new = webbrowser.open_new_tab = webbrowser.get = _deny_external
        if hasattr(os, "startfile"):
            os.startfile = _deny_external
        sys.addaudithook(_audit)
        os.environ["PYTHONBREAKPOINT"] = "0"
        os.environ["MPLCONFIGDIR"] = str(output / "_control/matplotlib-cache")
        try:
            payload = _run(request)
        except BaseException as exception:
            payload = error_record(exception)
        try:
            validate_summary(payload)
        except (ValueError, TypeError):
            payload = error_record(RuntimeError(), category="ProtocolError")
        reply.write_text(json.dumps(payload, allow_nan=False), encoding="utf-8")
        return 0
    except BaseException:
        # An invalid bootstrap never prints its request, file paths, or traceback.
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
