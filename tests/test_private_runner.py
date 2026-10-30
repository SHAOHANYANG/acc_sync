"""Only invented data, including isolated mock data/ trees. Never opens study files."""

import csv
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from check_data import InvalidData, check_data
from generate_fake_data import generate
from safe_pipeline import boundary


class PrivateRunnerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temporary.name)
        source = Path(__file__).resolve().parents[1]
        for name in ("run_real.py", "check_data.py", "generate_fake_data.py", "run_fake_tests.py", "run_synthetic_benchmark.py",
                     "real_config.json", "fake_config.json", "AGENTS.md", ".gitignore"):
            shutil.copyfile(source / name, cls.root / name)
        for directory in ("safe_pipeline", "sync_eval", "tests"):
            (cls.root / directory).mkdir()
            for path in (source / directory).glob("*.py"):
                shutil.copyfile(path, cls.root / directory / path.name)
        generate(cls.root / "fake_data")
        cls.raw_rows = (cls.root / "fake_data/valid/energy/session_90.csv").read_text().splitlines()

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def run_gate(self, *args):
        completed = subprocess.run([sys.executable, "-B", "run_real.py", *args], cwd=self.root,
                                   capture_output=True, text=True, encoding="utf-8", timeout=90)
        text = completed.stdout + completed.stderr
        self.assertNotIn("ZZTEST_Qx7", text)
        self.assertNotIn("7777.7777", text)
        self.assertNotIn("time_s,energy", text)
        self.assertNotIn("0.0,7777.7777", text)
        self.assertNotIn("Traceback", text)
        self.assertEqual(completed.stderr, "")
        # Exact accepted fake input rows must never appear on screen either.
        for line in self.raw_rows:
            self.assertNotIn(line, text)
        return completed

    def mark_verified(self):
        control = self.root / "results/_control"
        control.mkdir(parents=True, exist_ok=True)
        with patch.object(boundary, "REPO", self.root):
            stamp = boundary.fingerprint()
        (control / "verification.json").write_text(json.dumps({"fingerprint": stamp, "tests_passed": 1}))

    def test_accepted_fake_workflow_includes_both_comparison_and_hit_miss(self):
        result = self.run_gate("--fake", "--no-figures")
        self.assertEqual(result.returncode, 0)
        self.assertIn("SUCCESS", result.stdout)
        self.assertIn("sessions=2", result.stdout)
        self.assertIn("COMPARISONS", result.stdout)
        self.assertIn("EVALUATIONS", result.stdout)
        self.assertIn("source=1", result.stdout)

    def test_printing_whole_table_cannot_escape(self):
        self.assertEqual(self.run_gate("--fake", "--no-figures", "--test-fault", "print_table").returncode, 0)

    def test_native_fd_printing_cannot_escape(self):
        self.assertEqual(self.run_gate("--fake", "--no-figures", "--test-fault", "fd_write").returncode, 0)

    def test_subprocess_printing_cannot_escape(self):
        self.assertEqual(self.run_gate("--fake", "--no-figures", "--test-fault", "subprocess").returncode, 0)

    def test_logging_cannot_escape_or_create_a_raw_log(self):
        self.assertEqual(self.run_gate("--fake", "--no-figures", "--test-fault", "logging").returncode, 0)
        self.assertFalse((self.root / "SHOULD_NOT_EXIST.log").exists())

    def test_warnings_cannot_escape(self):
        self.assertEqual(self.run_gate("--fake", "--no-figures", "--test-fault", "warnings").returncode, 0)

    def test_accidental_browser_open_is_blocked(self):
        result = self.run_gate("--fake", "--no-figures", "--test-fault", "open_figure")
        self.assertEqual(result.returncode, 2)
        self.assertIn("PermissionError", result.stdout)

    def test_python_network_connect_is_blocked(self):
        result = self.run_gate("--fake", "--no-figures", "--test-fault", "network")
        self.assertEqual(result.returncode, 2)
        self.assertIn("PermissionError", result.stdout)

    def test_figure_destination_is_separate_from_tables(self):
        from safe_pipeline.analysis import analyse
        sessions, count = check_data(self.root / "fake_data/valid")
        output = self.root / "fake_results/figure_route_test"
        config = json.loads((self.root / "fake_config.json").read_text())
        with patch.object(boundary, "REPO", self.root), patch("safe_pipeline.analysis._render") as render, \
                patch("safe_pipeline.analysis.plot_phase") as phase_render:
            analyse(sessions, count, output, config, fake=True, figures=True)
            self.assertEqual(render.call_count, 2)
            for call in render.call_args_list:
                self.assertTrue(call.args[0].is_relative_to(output / "figures"))
            self.assertEqual(phase_render.call_count, 2)
            for call in phase_render.call_args_list:
                self.assertTrue(call.args[0].is_relative_to(output / "figures"))
        self.assertEqual(list((output / "tables").rglob("*.html")), [])

    def test_exception_message_is_never_printed(self):
        result = self.run_gate("--fake", "--no-figures", "--test-fault", "exception")
        self.assertEqual(result.returncode, 2)
        self.assertIn("type=ValueError", result.stdout)
        self.assertIn("function=_fault", result.stdout)

    def test_custom_exception_class_name_is_never_printed(self):
        result = self.run_gate("--fake", "--no-figures", "--test-fault", "custom_exception")
        self.assertEqual(result.returncode, 2)
        self.assertIn("type=RuntimeError", result.stdout)

    def test_text_in_an_allowed_numeric_output_field_is_rejected(self):
        self.assertEqual(self.run_gate("--fake", "--no-figures", "--test-fault", "text_summary").returncode, 2)

    def test_nonfinite_summary_is_rejected(self):
        self.assertEqual(self.run_gate("--fake", "--no-figures", "--test-fault", "bad_number").returncode, 2)

    def test_abrupt_child_exit_does_not_dump_anything(self):
        result = self.run_gate("--fake", "--no-figures", "--test-fault", "exit")
        self.assertEqual(result.returncode, 2)
        self.assertIn("ChildProcessError", result.stdout)

    def test_invalid_arguments_never_echo_them(self):
        self.assertEqual(self.run_gate("--ZZTEST_Qx7=7777.7777").returncode, 2)
        self.assertEqual(self.run_gate("--fake", "--config", "ZZTEST_Qx7/7777.7777.json").returncode, 2)

    def test_rechecking_fake_inputs_only_emits_counts(self):
        result = self.run_gate("--fake", "--check")
        self.assertEqual(result.returncode, 0)
        self.assertNotIn("session_90", result.stdout)

    def test_success_numbers_have_at_most_three_significant_digits(self):
        result = self.run_gate("--fake", "--no-figures")
        self.assertEqual(result.returncode, 0)
        for token in result.stdout.split():
            if "=" in token:
                value = token.split("=", 1)[1]
                if value != "NA":
                    self.assertEqual(float(value), float(format(float(value), ".3g")))

    def test_malformed_reference_and_energy_inputs_have_ordinal_errors_only(self):
        valid = self.root / "fake_data/valid"
        backup = self.root / "fake_data/valid_backup"
        # All paths are inside this newly created, invented test workspace.
        valid.rename(backup)
        try:
            for case in sorted((self.root / "fake_data/adversarial").iterdir()):
                with self.subTest(case=case.name):
                    shutil.copytree(case, valid)
                    try:
                        result = self.run_gate("--fake", "--check")
                        self.assertEqual(result.returncode, 2)
                        self.assertIn("CHECK_FAILED", result.stdout)
                        self.assertNotIn("session_99", result.stdout)
                    finally:
                        self.assertTrue(valid.resolve().is_relative_to(self.root.resolve()))
                        shutil.rmtree(valid)
        finally:
            backup.rename(valid)

    def test_unexpected_output_fields_and_exception_locations_are_rejected(self):
        for payload in ({"status": "ok", "totals": {"ZZTEST_Qx7": 7777.7777}},
                        {"status": "error", "type": "ValueError", "file": "ZZTEST_Qx7", "line": 1, "function": "main"}):
            with self.assertRaises(ValueError):
                boundary.validate_summary(payload)

    def test_actual_input_gate_requires_a_fresh_test_receipt(self):
        # This is a mock data/ under the temporary fixture root, NOT project data/.
        data = self.root / "data"
        if data.exists():
            self.assertTrue(data.resolve().is_relative_to(self.root.resolve()))
            shutil.rmtree(data)
        shutil.copytree(self.root / "fake_data/valid", data)
        receipt = self.root / "results/_control/verification.json"
        receipt.unlink(missing_ok=True)
        result = self.run_gate("--check")
        self.assertIn("VerificationRequired", result.stdout)
        self.mark_verified()
        self.assertEqual(self.run_gate("--check").returncode, 0)
        source = self.root / "real_config.json"
        original = source.read_text()
        try:
            source.write_text(original + "\n")
            self.assertIn("VerificationRequired", self.run_gate("--check").stdout)
        finally:
            source.write_text(original)
            receipt.unlink(missing_ok=True)

    def test_mock_real_mode_still_suppresses_feature_values(self):
        data = self.root / "data"
        if not data.exists():
            shutil.copytree(self.root / "fake_data/valid", data)
        self.mark_verified()
        result = self.run_gate("--no-figures")
        self.assertEqual(result.returncode, 0)
        self.assertNotIn("COMPARISONS", result.stdout)  # Defaults do not invent consent/alignment.
        self.assertNotIn("EVALUATIONS", result.stdout)
        receipt = self.root / "results/_control/verification.json"
        receipt.unlink(missing_ok=True)

    def test_legacy_cli_cannot_open_data_inputs(self):
        result = subprocess.run([sys.executable, "-B", "-m", "sync_eval", "explore", "--input",
            "data/energy/session_90.csv", "--session", "session_90"], cwd=self.root,
            capture_output=True, text=True, encoding="utf-8", timeout=20)
        self.assertEqual(result.returncode, 2)
        self.assertIn("PERMISSION", result.stdout)
        self.assertNotIn("7777.7777", result.stdout + result.stderr)

    def test_legacy_prepare_cannot_open_data_inputs(self):
        result = subprocess.run([sys.executable, "-B", "-m", "sync_eval", "prepare", "--kind", "csv", "--input",
            "data/energy/session_90.csv", "--session", "session_90", "--time-origin", "first", "--energy-scale", "linear"],
            cwd=self.root, capture_output=True, text=True, encoding="utf-8", timeout=20)
        self.assertEqual(result.returncode, 2)
        self.assertIn("PERMISSION", result.stdout)
        self.assertNotIn("7777.7777", result.stdout + result.stderr)

    def test_zero_energy_and_missing_data_remain_valid_inputs(self):
        root = self.root / "fake_data/numeric_edge_case"
        path = root / "energy/session_99.csv"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("time_s,energy\n0,0\n0.5,\n1,NaN\n1.5,1\n")
        sessions, count = check_data(root)
        self.assertEqual(sessions[0].energies, [0, None, None, 1])
        self.assertEqual(count, 1)

    def test_conflicting_independent_timestamp_labels_are_rejected(self):
        root = self.root / "fake_data/overlap_case"
        for kind, text in (("energy", "time_s,energy\n0,1\n0.5,1\n"),
                           ("annotations/synchrony", "start,end\n0,4\n"),
                           ("annotations/non_synchrony", "start,end\n2,6\n")):
            path = root / kind / "session_99.csv"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
        with self.assertRaises(InvalidData):
            check_data(root)

    def test_last_summary_is_schema_checked_again(self):
        self.run_gate("--fake", "--no-figures")
        self.assertEqual(self.run_gate("--fake", "--last-summary").returncode, 0)
        path = self.root / "fake_results/_control/last_summary.json"
        path.write_text('{"status":"ok","ZZTEST_Qx7":"7777.7777"}')
        self.assertEqual(self.run_gate("--fake", "--last-summary").returncode, 2)

    def test_links_and_overlap_are_rejected_without_following_them(self):
        invalid = self.root / "fake_data/adversarial/bad_value"
        with patch("check_data._is_link", return_value=True):
            with self.assertRaises(InvalidData):
                check_data(invalid)


if __name__ == "__main__":
    unittest.main()
