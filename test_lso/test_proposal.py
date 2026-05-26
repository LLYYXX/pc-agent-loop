"""Structural tests for the post-design TagProposal contract + BuildSession.

Covers the ten invariants that hold the lightweight / provable deadlines:

T1.  accepted proposals carry full evidence into the audit result
T2.  empty / weak / missing-evidence proposals are rejected with stable codes
T3.  evidence_phrase must be grounded in text_head for content semantics
T4.  duplicate tags within one call are de-duplicated, not silently merged
T5.  role routing keeps semantic vs metadata channels apart
T6.  metadata-only proposals report ``semantic_applied=False``
T7.  ``_defense_filter`` rejects tag==extension and tag==parent-dir token;
     filename is separated as hint rather than semantic evidence
T8.  filename_hint works on non-readable leaves; filename is not semantic evidence
T9.  BuildSession.finalize exposes semantic vs metadata counters separately
T10. default package surface exposes the proposal write contract
"""

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


class LsoProposalTests(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.scope = self._td.name
        self._patch = mock.patch.object(ov, "OVERLAYS", Path(self._td.name) / "overlays")
        self._patch.start()

    def tearDown(self) -> None:
        self._patch.stop()
        self._td.cleanup()

    def _write(self, rel: str, text: str) -> str:
        p = Path(self.scope) / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        return str(p)

    def _leaf(self, rel: str, text: str) -> str:
        path = self._write(rel, text)
        _, lid = ov.ensure_leaf(self.scope, path)
        return lid

    # T1
    def test_accepted_proposal_carries_full_evidence(self):
        lid = self._leaf("paper.txt", "高性能可信跨境贸易支付监管关键技术研究报告")
        res = lso.propose_leaf_tags(self.scope, lid, [
            {"tag": "跨境贸易支付", "evidence_phrase": "跨境贸易支付监管",
             "evidence_source": "text_head", "tag_role": "content_semantic"},
        ])
        self.assertTrue(res["ok"])
        self.assertTrue(res["semantic_applied"])
        self.assertFalse(res["metadata_applied"])
        self.assertEqual(res["semantic_tags"], ["跨境贸易支付"])
        self.assertEqual(res["metadata"], {"location_tags": [], "source_channel": None})
        acc = res["accepted"][0]
        self.assertEqual(
            (acc["tag"], acc["evidence_phrase"], acc["evidence_source"], acc["tag_role"]),
            ("跨境贸易支付", "跨境贸易支付监管", "text_head", "content_semantic"),
        )

    # T2
    def test_missing_and_weak_evidence_rejected(self):
        lid = self._leaf("a.txt", "深度学习模型训练与推理优化框架")
        res = lso.propose_leaf_tags(self.scope, lid, [
            {"tag": "深度学习", "evidence_phrase": "",
             "evidence_source": "text_head", "tag_role": "content_semantic"},
            {"tag": "高", "evidence_phrase": "高",
             "evidence_source": "text_head", "tag_role": "content_semantic"},
        ])
        self.assertFalse(res["ok"])
        reasons = {r["reason"] for r in res["rejected"]}
        self.assertEqual(reasons, {"missing_evidence", "weak_evidence"})

    # T3
    def test_evidence_not_grounded_rejected(self):
        lid = self._leaf("b.txt", "深度学习模型训练与推理优化框架")
        res = lso.propose_leaf_tags(self.scope, lid, [
            {"tag": "区块链", "evidence_phrase": "分布式账本网络",
             "evidence_source": "text_head", "tag_role": "content_semantic"},
        ])
        self.assertFalse(res["ok"])
        self.assertEqual(res["rejected"][0]["reason"], "evidence_not_grounded")

    # T4
    def test_duplicate_proposal_deduplicated(self):
        lid = self._leaf("d.txt", "评审意见整理稿与专利申报材料汇总报告")
        res = lso.propose_leaf_tags(self.scope, lid, [
            {"tag": "专利申报", "evidence_phrase": "专利申报材料",
             "evidence_source": "text_head", "tag_role": "content_semantic"},
            {"tag": "专利申报", "evidence_phrase": "专利申报材料",
             "evidence_source": "text_head", "tag_role": "content_semantic"},
        ])
        self.assertTrue(res["ok"])
        self.assertEqual(res["semantic_tags"], ["专利申报"])
        self.assertEqual(sum(1 for r in res["rejected"] if r["reason"] == "duplicate_tag"), 1)

    # T5
    def test_role_routing_separates_channels(self):
        lid = self._leaf("e.txt", "评审意见整理稿与专利申报材料汇总")
        res = lso.propose_leaf_tags(self.scope, lid, [
            {"tag": "course-lib", "tag_role": "location"},
            {"tag": "source-chat", "tag_role": "source_channel"},
            {"tag": "评审意见", "evidence_phrase": "评审意见整理稿",
             "evidence_source": "text_head", "tag_role": "content_semantic"},
        ])
        self.assertTrue(res["ok"])
        self.assertTrue(res["semantic_applied"])
        self.assertTrue(res["metadata_applied"])
        self.assertEqual(res["semantic_tags"], ["评审意见"])
        self.assertEqual(res["metadata"]["location_tags"], ["course-lib"])
        self.assertEqual(res["metadata"]["source_channel"], "source-chat")
        leaf = ov.load(self.scope)["leaves"][lid]
        self.assertEqual(leaf["semantic_tags"], ["评审意见"])
        self.assertEqual(leaf["location_tags"], ["course-lib"])
        self.assertEqual(leaf["source_channel"], "source-chat")

    def test_source_channel_is_single_value_contract(self):
        lid = self._leaf("f.txt", "评审意见整理稿与专利申报材料汇总")
        res = lso.propose_leaf_tags(self.scope, lid, [
            {"tag": "source-chat", "tag_role": "source_channel"},
            {"tag": "source-mail", "tag_role": "source_channel"},
        ])
        self.assertTrue(res["ok"])
        self.assertEqual(res["metadata"]["source_channel"], "source-chat")
        self.assertEqual(res["rejected"][0]["reason"], "multiple_source_channel")

    # T6
    def test_metadata_only_not_counted_as_semantic(self):
        lid = self._leaf("g.txt", "评审意见整理稿与专利申报材料汇总")
        res = lso.propose_leaf_tags(self.scope, lid, [
            {"tag": "course-lib", "tag_role": "location"},
        ])
        self.assertTrue(res["ok"])
        self.assertFalse(res["semantic_applied"])
        self.assertTrue(res["metadata_applied"])
        self.assertEqual(res["semantic_tags"], [])
        self.assertEqual(res["metadata"]["location_tags"], ["course-lib"])
        leaf = ov.load(self.scope)["leaves"][lid]
        self.assertEqual(leaf.get("semantic_tags") or [], [])
        self.assertEqual(leaf["location_tags"], ["course-lib"])

    # T7
    def test_defense_filter_rejects_extension_and_dir_token_not_stem(self):
        # tag == extension: text_head can support it, but extension tags still fail.
        lid_ext = self._leaf("report_txt_x.txt", "text txt evidence for extension guard")
        res_ext = lso.propose_leaf_tags(self.scope, lid_ext, [
            {"tag": "txt", "evidence_phrase": "text txt",
             "evidence_source": "text_head", "tag_role": "content_semantic"},
        ])
        self.assertFalse(res_ext["ok"])
        self.assertEqual(res_ext["rejected"][0]["reason"], "tag_is_extension")

        # tag == parent dir token: parent dir is "campus/courses"
        lid_dir = self._leaf("campus/courses/a.txt", "campus course review material")
        res_dir = lso.propose_leaf_tags(self.scope, lid_dir, [
            {"tag": "campus", "evidence_phrase": "campus course",
             "evidence_source": "text_head", "tag_role": "content_semantic"},
        ])
        self.assertFalse(res_dir["ok"])
        self.assertEqual(res_dir["rejected"][0]["reason"], "tag_is_dir_token")

        # filename is not evidence, but a text_head-supported tag may also appear in filename.
        lid_stem = self._leaf("sensors.txt", "sensors calibration evidence for filename overlap")
        res_stem = lso.propose_leaf_tags(self.scope, lid_stem, [
            {"tag": "sensors", "evidence_phrase": "sensors calibration",
             "evidence_source": "text_head", "tag_role": "content_semantic"},
        ])
        self.assertTrue(res_stem["ok"])
        self.assertEqual(res_stem["semantic_tags"], ["sensors"])

    # T8
    def test_filename_hint_on_non_readable_but_not_semantic_evidence(self):
        path_fn = self._write("project-report.bin", "\x00\x01\x02")
        _, lid_fn = ov.ensure_leaf(self.scope, path_fn)
        leaf = ov.load(self.scope)["leaves"][lid_fn]
        self.assertNotEqual(leaf.get("read_status"), "readable")
        self.assertEqual(leaf["filename_hint"], "project-report")
        res_fn = lso.propose_leaf_tags(self.scope, lid_fn, [
            {"tag": "project report", "evidence_phrase": "project-report",
             "evidence_source": "filename", "tag_role": "content_semantic"},
        ])
        self.assertFalse(res_fn["ok"])
        self.assertEqual(res_fn["rejected"][0]["reason"], "invalid_evidence_source")
        self.assertEqual(ov.load(self.scope)["leaves"][lid_fn]["semantic_tags"], [])

        path_th = self._write("noisy.bin", "\x00\x01\x02")
        _, lid_th = ov.ensure_leaf(self.scope, path_th)
        res_th = lso.propose_leaf_tags(self.scope, lid_th, [
            {"tag": "任何标签", "evidence_phrase": "任何内容",
             "evidence_source": "text_head", "tag_role": "content_semantic"},
        ])
        self.assertFalse(res_th["ok"])
        self.assertEqual(res_th["rejected"][0]["reason"], "no_evidence_source")

    # T9
    def test_build_session_finalize_separates_semantic_and_metadata(self):
        path = self._write("model.txt", "深度学习模型训练与推理优化框架完整设计")
        session = lso.BuildSession(self.scope)
        session.add_candidates(3)
        rr = session.try_read(path)
        self.assertEqual(rr["read_status"], "readable")
        with mock.patch.object(ov, "read_leaf", wraps=ov.read_leaf) as spy:
            lid = session.ensure_leaf(path)
        self.assertEqual(spy.call_count, 0)
        session.propose_tags(lid, [
            {"tag": "深度学习", "evidence_phrase": "深度学习模型训练",
             "evidence_source": "text_head", "tag_role": "content_semantic"},
            {"tag": "course-lib", "tag_role": "location"},
            {"tag": "无关", "evidence_phrase": "完全不存在",
             "evidence_source": "text_head", "tag_role": "content_semantic"},
        ])
        proc = session.finalize()["process"]
        self.assertEqual(proc["candidate_path_count"], 3)
        self.assertEqual(proc["selected_count"], 1)
        self.assertEqual(proc["readable_count"], 1)
        self.assertEqual(proc["proposal_count"], 3)
        self.assertEqual(proc["proposal_accepted"], 2)
        self.assertEqual(proc["proposal_rejected"], 1)
        self.assertEqual(proc["semantic_applied_count"], 1)
        self.assertEqual(proc["metadata_applied_count"], 1)
        self.assertEqual(proc["semantic_apply_ok"], 1)
        self.assertEqual(proc["metadata_apply_ok"], 1)
        self.assertEqual(proc["apply_ok"], 1)
        log = proc["proposal_log"][0]
        self.assertEqual(log["leaf_id"], lid)
        self.assertEqual(len(log["accepted"]), 2)
        self.assertEqual(log["rejected"][0]["reason"], "evidence_not_grounded")

    def test_proposal_benchmark_submits_bad_proposals_to_core(self):
        from experiments.lso.proposal_benchmark import run

        path = self._write("invalid_source.txt", "深度学习模型训练与推理优化框架完整设计")
        metrics = run(self.scope, {
            path: [
                {"tag": "深度学习", "evidence_phrase": "深度学习模型训练",
                 "evidence_source": "path", "tag_role": "content_semantic"},
            ],
        })
        self.assertEqual(metrics["proposal_count"], 1)
        self.assertEqual(metrics["proposal_rejected"], 1)
        self.assertEqual(metrics["rejection_reasons"], {"invalid_evidence_source": 1})

    # T10
    def test_default_api_exposes_proposal_contract(self):
        self.assertTrue(hasattr(lso, "propose_leaf_tags"))
        self.assertTrue(hasattr(lso, "BuildSession"))
        self.assertTrue(hasattr(lso, "prepare_leaf_tag_task"))

    # T11 — full-replacement contract: metadata-only call clears stale semantic
    def test_metadata_only_clears_stale_semantic_tags(self):
        lid = self._leaf("h.txt", "评审意见整理稿与专利申报材料汇总报告")
        first = lso.propose_leaf_tags(self.scope, lid, [
            {"tag": "评审意见", "evidence_phrase": "评审意见整理稿",
             "evidence_source": "text_head", "tag_role": "content_semantic"},
        ])
        self.assertTrue(first["semantic_applied"])
        self.assertEqual(ov.load(self.scope)["leaves"][lid]["semantic_tags"], ["评审意见"])
        second = lso.propose_leaf_tags(self.scope, lid, [
            {"tag": "course-lib", "tag_role": "location"},
        ])
        self.assertTrue(second["ok"])
        self.assertFalse(second["semantic_applied"])
        self.assertTrue(second["metadata_applied"])
        leaf = ov.load(self.scope)["leaves"][lid]
        self.assertEqual(leaf["semantic_tags"], [])
        self.assertEqual(leaf["location_tags"], ["course-lib"])

    # T12 — err path does not touch the leaf (all proposals rejected)
    def test_all_rejected_call_does_not_modify_leaf(self):
        lid = self._leaf("i.txt", "评审意见整理稿与专利申报材料汇总报告")
        ok = lso.propose_leaf_tags(self.scope, lid, [
            {"tag": "评审意见", "evidence_phrase": "评审意见整理稿",
             "evidence_source": "text_head", "tag_role": "content_semantic"},
        ])
        self.assertTrue(ok["ok"])
        snapshot = dict(ov.load(self.scope)["leaves"][lid])
        rej = lso.propose_leaf_tags(self.scope, lid, [
            {"tag": "区块链", "evidence_phrase": "分布式账本网络",
             "evidence_source": "text_head", "tag_role": "content_semantic"},
        ])
        self.assertFalse(rej["ok"])
        self.assertEqual(rej["error"], "no_tags_accepted")
        self.assertEqual(ov.load(self.scope)["leaves"][lid], snapshot)

    # T11b — prepare_leaf_tag_task keeps filename as hint, not semantic evidence
    def test_prepare_leaf_tag_task_non_readable_returns_filename_hint_only(self):
        path = self._write("binary.bin", "\x00\x01\x02")
        _, lid = ov.ensure_leaf(self.scope, path)
        prep_bad = lso.prepare_leaf_tag_task(self.scope, lid)
        self.assertTrue(prep_bad["ok"])
        task = prep_bad["task"]
        self.assertEqual(task["allowed_evidence_sources"], [])
        self.assertIsNone(task["text_head"])
        self.assertEqual(task["filename_hint"], "binary")

        readable_path = self._write("doc.txt", "深度学习模型训练与推理优化框架完整设计")
        _, lid_r = ov.ensure_leaf(self.scope, readable_path)
        prep_ok = lso.prepare_leaf_tag_task(self.scope, lid_r)
        self.assertTrue(prep_ok["ok"])
        self.assertEqual(
            prep_ok["task"]["allowed_evidence_sources"], ["text_head"]
        )
        self.assertTrue(prep_ok["task"]["text_head"])

    # T11c — BuildSession counts only text_head as semantic evidence
    def test_build_session_counts_evidence_source_breakdown(self):
        readable = self._write("rdoc.txt", "深度学习模型训练与推理优化框架完整设计")
        binary = self._write("项目结题报告.bin", "\x00\x01\x02")
        session = lso.BuildSession(self.scope)
        session.try_read(readable)
        session.try_read(binary)
        lid_r = session.ensure_leaf(readable)
        lid_b = session.ensure_leaf(binary)
        session.propose_tags(lid_r, [
            {"tag": "深度学习", "evidence_phrase": "深度学习模型训练",
             "evidence_source": "text_head", "tag_role": "content_semantic"},
        ])
        session.propose_tags(lid_b, [
            {"tag": "项目结题报告", "evidence_phrase": "项目结题报告",
             "evidence_source": "filename", "tag_role": "content_semantic"},
        ])
        proc = session.finalize()["process"]
        self.assertEqual(proc["evidence_source_text_head"], 1)
        self.assertNotIn("evidence_source_filename", proc)
        self.assertEqual(proc["semantic_apply_ok"], 1)
        self.assertEqual(proc["proposal_rejected"], 1)

    # T13 — full replacement also overwrites location_tags and source_channel
    def test_full_replacement_applies_to_metadata_too(self):
        lid = self._leaf("j.txt", "评审意见整理稿与专利申报材料汇总报告")
        lso.propose_leaf_tags(self.scope, lid, [
            {"tag": "评审意见", "evidence_phrase": "评审意见整理稿",
             "evidence_source": "text_head", "tag_role": "content_semantic"},
            {"tag": "course-lib", "tag_role": "location"},
            {"tag": "source-chat", "tag_role": "source_channel"},
        ])
        snapshot = ov.load(self.scope)["leaves"][lid]
        self.assertEqual(snapshot["location_tags"], ["course-lib"])
        self.assertEqual(snapshot["source_channel"], "source-chat")
        replaced = lso.propose_leaf_tags(self.scope, lid, [
            {"tag": "专利申报", "evidence_phrase": "专利申报材料",
             "evidence_source": "text_head", "tag_role": "content_semantic"},
        ])
        self.assertTrue(replaced["ok"])
        leaf = ov.load(self.scope)["leaves"][lid]
        self.assertEqual(leaf["semantic_tags"], ["专利申报"])
        self.assertEqual(leaf["location_tags"], [])
        self.assertIsNone(leaf["source_channel"])


if __name__ == "__main__":
    unittest.main()

