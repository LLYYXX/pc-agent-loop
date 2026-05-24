from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
RUNNER = REPO / "experiments" / "lso" / "runner.py"


class LsoRunnerTests(unittest.TestCase):
    def test_runner_outputs_json_for_mode_c(self):
        with tempfile.TemporaryDirectory() as td:
            Path(td, "a.md").write_text("Experiment evidence about sensors and routing.\n", encoding="utf-8")
            proc = subprocess.run(
                [sys.executable, str(RUNNER), "--mode", "C", "--scope", td],
                capture_output=True, text=True, check=True,
            )
            report = json.loads(proc.stdout)
        self.assertEqual(report["mode"], "C")
        self.assertIn("file_reads", report)
        self.assertGreaterEqual(report["file_reads"], 1)

    def test_ablation_no_selection(self):
        with tempfile.TemporaryDirectory() as td:
            Path(td, "b.md").write_text("Ablation passthrough evidence content.\n", encoding="utf-8")
            proc = subprocess.run(
                [sys.executable, str(RUNNER), "--mode", "C", "--scope", td, "--ablate", "no_selection"],
                capture_output=True, text=True, check=True,
            )
            report = json.loads(proc.stdout)
        self.assertIn("no_selection", report["ablations"])


if __name__ == "__main__":
    unittest.main()
