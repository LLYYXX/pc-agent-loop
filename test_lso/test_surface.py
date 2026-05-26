from __future__ import annotations

import inspect
import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
MEMORY_ROOT = REPO_ROOT / "memory"
if str(MEMORY_ROOT) not in sys.path:
    sys.path.insert(0, str(MEMORY_ROOT))

import local_semantic_overlay as lso
from local_semantic_overlay import overlay as ov
from experiments.lso.proposal_benchmark import METRICS_SCHEMA, run


class LsoSurfaceTests(unittest.TestCase):
    def test_primary_surface_excludes_unchecked_writers(self):
        self.assertTrue(hasattr(lso, "propose_leaf_tags"))
        self.assertTrue(hasattr(lso, "BuildSession"))
        self.assertTrue(hasattr(lso, "prepare_leaf_tag_task"))
        self.assertFalse(hasattr(lso, "ensure_leaf"))
        self.assertFalse(hasattr(lso, "load"))
        self.assertFalse(hasattr(lso, "read_leaf"))

    def test_full_replacement_contract_is_visible_in_writer(self):
        src = inspect.getsource(ov.propose_leaf_tags)
        self.assertIn('leaf["semantic_tags"] = semantic', src)
        self.assertIn('leaf["location_tags"] = location', src)
        self.assertIn('leaf["source_channel"] = channel', src)

    def test_prepare_keeps_filename_as_hint_not_evidence(self):
        src = inspect.getsource(ov.prepare_leaf_tag_task)
        self.assertIn("allowed_evidence_sources", src)
        self.assertIn("filename_hint", src)
        self.assertNotIn('"filename"]', src)
        self.assertNotIn('return _err("not_readable")', src)

    def test_proposal_benchmark_has_metrics_schema(self):
        self.assertTrue(callable(run))
        self.assertIn("rejection_reasons", METRICS_SCHEMA)
        self.assertIn("evidence_source_text_head", METRICS_SCHEMA)
        self.assertNotIn("evidence_source_filename", METRICS_SCHEMA)


if __name__ == "__main__":
    unittest.main()

