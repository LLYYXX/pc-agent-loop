from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
MEMORY_ROOT = REPO_ROOT / "memory"
if str(MEMORY_ROOT) not in sys.path:
    sys.path.insert(0, str(MEMORY_ROOT))

from local_semantic_overlay import overlay as ov
from local_semantic_overlay.navigate import NavigateFlags, query, record_hit


class LsoNavigateTests(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.scope = self._td.name
        self._patch = mock.patch.object(ov, "OVERLAYS", Path(self._td.name) / "overlays")
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        self._td.cleanup()

    def test_empty_query_no_hits(self):
        self.assertEqual(query(self.scope, "")["hits"], [])

    def test_leaf_tag_hit(self):
        p = Path(self.scope) / "a.md"
        p.write_text("Navigate test evidence about sensors and routing.\n", encoding="utf-8")
        _, lid = ov.ensure_leaf(self.scope, str(p))
        ov.apply_leaf_tags(self.scope, lid, ["sensors"])
        hits = query(self.scope, "sensors")["hits"]
        self.assertTrue(any(h["hit_type"] == "leaf_tag" for h in hits))

    def test_fallback_source_labeled(self):
        with mock.patch("local_semantic_overlay.navigate.lso_search.search_rows") as sr:
            sr.return_value = [{"path": "/tmp/x.txt", "name": "x.txt", "mtime": 1, "size": 2}]
            hits = query(self.scope, "xtxt", flags=NavigateFlags(enable_semantic=False, enable_leaf_tags=False, enable_path=False))["hits"]
        self.assertEqual(hits[0]["source"], "fallback")

    def test_semantic_disabled(self):
        p = Path(self.scope) / "a.md"
        p.write_text("Semantic off test with unique token foobarzz.\n", encoding="utf-8")
        _, lid = ov.ensure_leaf(self.scope, str(p))
        ov.apply_leaf_tags(self.scope, lid, ["foobarzz"])
        off = NavigateFlags(enable_semantic=False)
        hits = query(self.scope, "foobarzz", flags=off)["hits"]
        self.assertFalse(any(h["hit_type"] == "semantic_node" for h in hits))

    def test_cold_node_excluded_by_default(self):
        p = Path(self.scope) / "cold.md"
        p.write_text("Cold node evidence about thermal control systems.\n", encoding="utf-8")
        _, lid = ov.ensure_leaf(self.scope, str(p))
        ov.apply_leaf_tags(self.scope, lid, ["thermal control"])
        node = {
            "label": "Thermal", "semantic_tags": ["thermal control"], "source": "compressed",
            "supporting_leaf_ids": [lid], "brief": "Cold node evidence about thermal control",
            "status": "active", "anchor": self.scope, "derived_from_ids": [],
        }
        ov.apply_node(self.scope, node)
        data = ov.load(self.scope)
        data["nodes"][list(data["nodes"].keys())[0]]["status"] = "cold"
        ov.save(data)
        hits = query(self.scope, "thermal")["hits"]
        self.assertFalse(any(h["hit_type"] == "semantic_node" for h in hits))

    def test_record_hit_needs_recheck_when_files_changed(self):
        p = Path(self.scope) / "chg.md"
        p.write_text("Original thermal control evidence text.\n", encoding="utf-8")
        _, lid = ov.ensure_leaf(self.scope, str(p))
        res = ov.apply_compression(self.scope, str(p.parent), {
            "decision": "compress", "label": "T", "tags": ["thermal"],
            "brief": "Original thermal control evidence text.",
        })
        nid = res["node_id"]
        data = ov.load(self.scope)
        data["nodes"][nid]["status"] = "cold"
        ov.save(data)
        p.write_text("Changed thermal control evidence text.\n", encoding="utf-8")
        hit = record_hit(self.scope, nid)
        self.assertEqual(hit["action"], "needs_recheck")


if __name__ == "__main__":
    unittest.main()
