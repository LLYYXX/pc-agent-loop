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

import local_semantic_overlay as lso
from local_semantic_overlay import overlay as ov
from local_semantic_overlay.overlay import OverlayFlags


class LsoOverlayTests(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.scope = self._td.name
        self._patch = mock.patch.object(ov, "OVERLAYS", Path(self._td.name) / "overlays")
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        self._td.cleanup()

    def _write(self, rel: str, text: str) -> str:
        p = Path(self.scope) / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        return str(p)

    def _tag(self, leaf_id: str, tag: str, evidence: str, flags: OverlayFlags | None = None) -> dict:
        return ov.propose_leaf_tags(self.scope, leaf_id, [{
            "tag": tag,
            "evidence_phrase": evidence,
            "evidence_source": "text_head",
            "tag_role": "content_semantic",
        }], flags=flags)

    def test_leaf_tag_and_apply_node(self):
        path = self._write("a.md", "Alpha evidence about routing tables and deployment.\n")
        _, lid = ov.ensure_leaf(self.scope, path)
        self.assertTrue(self._tag(lid, "routing tables", "routing tables")["ok"])
        self.assertEqual(ov.load(self.scope)["leaves"][lid]["semantic_tags"], ["routing tables"])

        node = {
            "label": "Routing notes", "semantic_tags": ["routing tables"], "source": "compressed",
            "supporting_leaf_ids": [lid], "brief": "Alpha evidence about routing tables",
            "status": "active", "anchor": str(Path(path).parent), "derived_from_ids": [],
        }
        res = ov.apply_node(self.scope, node)
        self.assertTrue(res["ok"])
        self.assertEqual(len(ov.load(self.scope)["nodes"]), 1)

    def test_ungrounded_brief_rejected(self):
        path = self._write("b.md", "Beta content about zebras in delta region.\n")
        _, lid = ov.ensure_leaf(self.scope, path)
        node = {
            "label": "Fake", "semantic_tags": ["fake"], "source": "compressed",
            "supporting_leaf_ids": [lid], "brief": "totally fabricated summary",
            "status": "active", "anchor": self.scope, "derived_from_ids": [],
        }
        self.assertFalse(ov.apply_node(self.scope, node)["ok"])

    def test_compression_single_node(self):
        anchor = Path(self.scope) / "bundle"
        anchor.mkdir()
        (anchor / "readme.md").write_text(
            "Material bundle with course readings and assignment briefs.\n", encoding="utf-8")
        prep = ov.prepare_compression_task(self.scope, str(anchor))
        self.assertTrue(prep["ok"])
        applied = ov.apply_compression(self.scope, str(anchor), {
            "decision": "compress", "label": "Course bundle",
            "tags": ["course readings"], "brief": "Material bundle with course readings",
        })
        self.assertTrue(applied["ok"])
        self.assertEqual(len(ov.load(self.scope)["nodes"]), 1)

    def test_aggregation_lineage_and_recursive_block(self):
        p1 = self._write("p1.md", "Policy gradients for control tasks in simulation.\n")
        p2 = self._write("p2.md", "Policy gradient methods for robotic benchmarks.\n")
        _, l1 = ov.ensure_leaf(self.scope, p1)
        _, l2 = ov.ensure_leaf(self.scope, p2)
        self.assertTrue(self._tag(l1, "policy gradients", "Policy gradients")["ok"])
        self.assertTrue(self._tag(l2, "policy gradients", "Policy gradient")["ok"])
        agg = ov.apply_aggregation(self.scope, {
            "decision": "aggregate", "label": "PG research", "tags": ["policy gradients"],
            "derived_from_ids": [l1, l2], "brief": "Policy gradients for control tasks",
        })
        self.assertTrue(agg["ok"])
        bad = ov.apply_aggregation(self.scope, {
            "decision": "aggregate", "label": "Broader", "tags": ["policy gradients"],
            "derived_from_ids": [agg["node_id"]], "brief": "Policy gradients for control tasks",
        })
        self.assertFalse(bad["ok"])

    def test_flags_disable_leaf_tags(self):
        path = self._write("c.md", "Gamma readable evidence for flag disable test.\n")
        _, lid = ov.ensure_leaf(self.scope, path)
        off = OverlayFlags(enable_leaf_tags=False)
        self.assertFalse(self._tag(lid, "gamma", "Gamma", flags=off)["ok"])

    def test_explicit_feedback_only(self):
        path = self._write("d.md", "Delta readable evidence for feedback recording.\n")
        _, lid = ov.ensure_leaf(self.scope, path)
        self.assertTrue(self._tag(lid, "delta evidence", "Delta readable evidence")["ok"])
        self.assertTrue(lso.record_feedback(self.scope, result_id="r1", kind="selected", leaf_id=lid)["ok"])
        self.assertEqual(len(ov.load(self.scope)["feedback"]), 1)

    def test_active_budget_demotion_respects_flag(self):
        anchor = Path(self.scope) / "many"
        anchor.mkdir()
        for i in range(3):
            sub = anchor / f"s{i}"
            sub.mkdir()
            (sub / "n.md").write_text(f"Cluster {i} readable evidence text here.\n", encoding="utf-8")
            ov.apply_compression(self.scope, str(sub), {
                "decision": "compress", "label": f"C{i}", "tags": [f"cluster {i}"],
                "brief": f"Cluster {i} readable evidence text here.",
            })
        data = ov.load(self.scope)
        data["meta"]["active_budget"] = 1
        ov.save(data)
        off = OverlayFlags(enable_active_cold=False)
        res = lso.enforce_active_budget(self.scope, flags=off)
        self.assertTrue(res.get("skipped"))
        active = [n for n in ov.load(self.scope)["nodes"].values() if n.get("status") == "active"]
        self.assertEqual(len(active), 3)


if __name__ == "__main__":
    unittest.main()
