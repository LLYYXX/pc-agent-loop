from __future__ import annotations

import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
MEMORY_ROOT = REPO_ROOT / "memory"
if str(MEMORY_ROOT) not in sys.path:
    sys.path.insert(0, str(MEMORY_ROOT))

from local_semantic_overlay import read_leaf
from local_semantic_overlay import search as lso_search
from local_semantic_overlay.read import looks_like_raw_dump, sanitize_display


class LsoReadSubstrateTests(unittest.TestCase):
    def test_json_manifest_is_readable_not_raw_dump(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "package.json"
            p.write_text(
                """{
  "name": "sample-project",
  "description": "Readable project manifest with useful script metadata",
  "scripts": {"test": "python -m unittest", "lint": "ruff check"},
  "dependencies": {"requests": "^2.0.0"}
}
""",
                encoding="utf-8",
            )

            text = p.read_text(encoding="utf-8")
            self.assertFalse(looks_like_raw_dump(text))
            result = read_leaf(str(p))

        self.assertTrue(result["ok"])
        self.assertEqual(result["read_status"], "readable")
        self.assertEqual(result["evidence_type"], "manifest")
        self.assertIn("sample-project", result["text_head"])

    def test_obvious_artifact_dump_is_suppressed(self):
        raw = "PK\x03\x04 [Content_Types].xml _rels/word/document.xml"

        self.assertTrue(looks_like_raw_dump(raw))
        self.assertEqual(sanitize_display(raw), "")

    def test_ignore_dirs_are_case_insensitive(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "System Volume Information" / "note.md"
            p.parent.mkdir()
            p.write_text("Readable text that must still be skipped by directory gate.", encoding="utf-8")

            result = read_leaf(str(p))

        self.assertTrue(result["ok"])
        self.assertEqual(result["read_status"], "skipped_noise")
        self.assertIsNone(result["text_head"])

    def test_xml_manifest_is_not_rejected_by_xml_declaration(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "pom.xml"
            p.write_text(
                """<?xml version="1.0" encoding="UTF-8"?>
<project>
  <name>Readable Maven manifest</name>
  <description>Build settings and dependencies for service module</description>
</project>
""",
                encoding="utf-8",
            )

            result = read_leaf(str(p))

        self.assertEqual(result["read_status"], "readable")
        self.assertEqual(result["evidence_type"], "manifest")
        self.assertIn("Readable Maven manifest", result["text_head"])

    def test_utf16le_without_bom_is_readable(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "note.txt"
            p.write_bytes("Readable UTF16 text with enough words for extraction.".encode("utf-16le"))

            result = read_leaf(str(p))

        self.assertEqual(result["read_status"], "readable")
        self.assertIn("Readable UTF16 text", result["text_head"])


class LsoSearchSubstrateTests(unittest.TestCase):
    def test_ensure_search_ready_does_not_persist_by_default(self):
        with (
            mock.patch.object(lso_search.sys, "platform", "win32"),
            mock.patch.object(lso_search, "_find_es", return_value=r"C:\Everything\es.exe"),
            mock.patch.object(lso_search, "_probe", return_value=True),
            mock.patch.object(lso_search, "_write_tool") as write_tool,
        ):
            result = lso_search.ensure_search_ready()

        self.assertTrue(result["ok"])
        self.assertTrue(result["ready"])
        write_tool.assert_not_called()

    def test_ensure_search_ready_persists_only_when_requested(self):
        with (
            mock.patch.object(lso_search.sys, "platform", "win32"),
            mock.patch.object(lso_search, "_find_es", return_value=r"C:\Everything\es.exe"),
            mock.patch.object(lso_search, "_probe", return_value=True),
            mock.patch.object(lso_search, "_ev_for_es", return_value=r"C:\Everything\Everything.exe"),
            mock.patch.object(lso_search, "_write_tool", return_value=True) as write_tool,
        ):
            result = lso_search.ensure_search_ready(persist=True)

        self.assertTrue(result["ok"])
        write_tool.assert_has_calls([
            mock.call("es.exe", r"C:\Everything\es.exe"),
            mock.call("Everything.exe", r"C:\Everything\Everything.exe"),
        ])

    def test_search_rows_zero_limit_has_no_side_effect(self):
        with mock.patch.object(lso_search, "_find_es") as find_es:
            result = lso_search.search_rows("*", limit=0)

        self.assertEqual(result, [])
        find_es.assert_not_called()

    def test_search_rows_returns_mechanical_file_rows(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "result.md"
            p.write_text("searchable file", encoding="utf-8")
            completed = types.SimpleNamespace(returncode=0, stdout=f"{p}\n")

            with (
                mock.patch.object(lso_search, "_find_es", return_value=r"C:\Everything\es.exe"),
                mock.patch.object(lso_search.subprocess, "run", return_value=completed) as run,
            ):
                rows = lso_search.search_rows("result", scope=td, limit=1)

        self.assertEqual(len(rows), 1)
        self.assertEqual(set(rows[0]), {"path", "name", "mtime", "size"})
        self.assertEqual(rows[0]["name"], "result.md")
        self.assertEqual(run.call_args.args[0][0], r"C:\Everything\es.exe")
        self.assertIn("-path", run.call_args.args[0])

    def test_search_rows_ignores_nonzero_es_output(self):
        completed = types.SimpleNamespace(returncode=2, stdout="Invalid switch\n")

        with (
            mock.patch.object(lso_search, "_find_es", return_value=r"C:\Everything\es.exe"),
            mock.patch.object(lso_search.subprocess, "run", return_value=completed),
        ):
            rows = lso_search.search_rows("-bad", limit=10)

        self.assertEqual(rows, [])

    def test_search_rows_keeps_absolute_paths_from_nonzero_es_output(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "result.md"
            p.write_text("searchable file", encoding="utf-8")
            completed = types.SimpleNamespace(returncode=2, stdout=f"{p}\n")

            with (
                mock.patch.object(lso_search, "_find_es", return_value=r"C:\Everything\es.exe"),
                mock.patch.object(lso_search.subprocess, "run", return_value=completed),
            ):
                rows = lso_search.search_rows("result", limit=10)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["name"], "result.md")


if __name__ == "__main__":
    unittest.main()
