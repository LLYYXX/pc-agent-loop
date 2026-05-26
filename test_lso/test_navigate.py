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

    def _tag(self, leaf_id: str, tag: str, evidence: str) -> None:
        res = ov.propose_leaf_tags(self.scope, leaf_id, [{
            "tag": tag,
            "evidence_phrase": evidence,
            "evidence_source": "text_head",
            "tag_role": "content_semantic",
        }])
        self.assertTrue(res["ok"])

    def test_leaf_tag_hit(self):
        p = Path(self.scope) / "a.md"
        p.write_text("Navigate test evidence about sensors and routing.\n", encoding="utf-8")
        _, lid = ov.ensure_leaf(self.scope, str(p))
        self._tag(lid, "sensors", "sensors")
        hits = query(self.scope, "sensors")["hits"]
        self.assertTrue(any(h["hit_type"] == "leaf_tag" for h in hits))
        self.assertEqual(hits[0]["match_reasons"][0]["channel"], "semantic_tags")

    def test_filename_hint_hit_is_not_leaf_tag(self):
        p = Path(self.scope) / "project-review.bin"
        p.write_bytes(b"\x00\x01\x02")
        ov.ensure_leaf(self.scope, str(p))
        hits = query(self.scope, "project-review")["hits"]
        self.assertEqual(hits[0]["hit_type"], "filename_hint")
        self.assertEqual(hits[0]["match_reasons"][0]["channel"], "filename_hint")
        self.assertEqual(hits[0]["semantic_tags"], [])

    def test_metadata_hit_reports_channel(self):
        p = Path(self.scope) / "note.md"
        p.write_text("Readable evidence about payment review.\n", encoding="utf-8")
        _, lid = ov.ensure_leaf(self.scope, str(p))
        ov.propose_leaf_tags(self.scope, lid, [{"tag": "source-chat", "tag_role": "source_channel"}])
        hits = query(self.scope, "source-chat")["hits"]
        self.assertEqual(hits[0]["hit_type"], "metadata")
        self.assertEqual(hits[0]["match_reasons"][0]["channel"], "source_channel")

    def test_fallback_source_labeled(self):
        with mock.patch("local_semantic_overlay.navigate.lso_search.search_rows") as sr:
            sr.return_value = [{"path": "/tmp/x.txt", "name": "x.txt", "mtime": 1, "size": 2}]
            hits = query(self.scope, "xtxt", flags=NavigateFlags(enable_semantic=False, enable_leaf_tags=False, enable_path=False))["hits"]
        self.assertEqual(hits[0]["source"], "fallback")

    def test_semantic_disabled(self):
        p = Path(self.scope) / "a.md"
        p.write_text("Semantic off test with unique token foobarzz.\n", encoding="utf-8")
        _, lid = ov.ensure_leaf(self.scope, str(p))
        self._tag(lid, "foobarzz", "foobarzz")
        off = NavigateFlags(enable_semantic=False)
        hits = query(self.scope, "foobarzz", flags=off)["hits"]
        self.assertFalse(any(h["hit_type"] == "semantic_node" for h in hits))

    def test_cold_node_excluded_by_default(self):
        p = Path(self.scope) / "cold.md"
        p.write_text("Cold node evidence about thermal control systems.\n", encoding="utf-8")
        _, lid = ov.ensure_leaf(self.scope, str(p))
        self._tag(lid, "thermal control", "thermal control")
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
