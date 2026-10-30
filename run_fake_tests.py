"""Run all invented-data tests and issue a source/config-bound verification receipt."""

import json
from pathlib import Path
import sys
import tempfile
import unittest

from generate_fake_data import generate
from safe_pipeline.boundary import REPO, fingerprint, load_config


def main():
    temporary_root = REPO / "fake_results/test-temporary"
    temporary_root.mkdir(parents=True, exist_ok=True)
    tempfile.tempdir = str(temporary_root)
    generate()
    load_config(REPO / "real_config.json")
    before = fingerprint()
    suite = unittest.defaultTestLoader.discover(str(REPO / "tests"))
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    if not result.wasSuccessful():
        print("FAKE_TESTS_FAILED")
        return 2
    if before != fingerprint():
        print("FAKE_TESTS_FAILED_SOURCE_CHANGED")
        return 2
    control = REPO / "results/_control"
    control.mkdir(parents=True, exist_ok=True)
    (control / "verification.json").write_text(json.dumps({"fingerprint": before,
        "tests_passed": result.testsRun}), encoding="utf-8")
    print(f"FAKE_TESTS_PASSED tests={result.testsRun}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
