from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
MEMORY_ROOT = REPO_ROOT / "memory"
if str(MEMORY_ROOT) not in sys.path:
    sys.path.insert(0, str(MEMORY_ROOT))

from local_semantic_overlay.select import EvidenceFlags, select_for_read


class LsoSelectTests(unittest.TestCase):
    def test_mechanical_reason_for_readme(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "README.md"
            p.write_text("Project overview with setup instructions.\n", encoding="utf-8")
            rows = select_for_read([str(p)])
        self.assertTrue(rows)
        self.assertEqual(rows[0]["reason"], "readme")

    def test_ignore_dirs_skipped(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "node_modules" / "note.md"
            p.parent.mkdir()
            p.write_text("Should be skipped by ignore gate.\n", encoding="utf-8")
            rows = select_for_read([str(p)])
        self.assertEqual(rows, [])

    def test_user_confirmed_seed(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "picked.txt"
            p.write_text("User picked file content here.\n", encoding="utf-8")
            rows = select_for_read([], seeds=[str(p)])
        self.assertEqual(rows[0]["reason"], "user_confirmed")

    def test_binary_with_filename_signal_selected_as_filename_only(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "项目结题报告.bin"
            p.write_bytes(b"\x00\x01\x02")
            rows = select_for_read([str(p)])
        self.assertTrue(rows)
        self.assertEqual(rows[0]["reason"], "filename_only")

    def test_binary_without_filename_signal_skipped(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "x.bin"
            p.write_bytes(b"\x00\x01\x02")
            rows = select_for_read([str(p)])
        self.assertEqual(rows, [])

    def test_selection_disabled_passthrough(self):
        with tempfile.TemporaryDirectory() as td:
            a = Path(td) / "a.txt"
            b = Path(td) / "b.txt"
            a.write_text("aaa\n", encoding="utf-8")
            b.write_text("bbb\n", encoding="utf-8")
            rows = select_for_read(
                [str(b), str(a)],
                flags=EvidenceFlags(enable_selection=False),
                limit=10,
            )
        self.assertEqual([r["path"] for r in rows], [str(b), str(a)])
        self.assertEqual(rows[0]["reason"], "passthrough")


if __name__ == "__main__":
    unittest.main()
