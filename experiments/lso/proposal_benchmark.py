"""Proposal-mode benchmark scaffold — outside semantic core (boundary B5).

This file exists to **reserve the contract surface** for measuring the
``propose_leaf_tags`` evidence overlay. It does NOT yet drive an LLM agent; it provides:

1. ``run(scope, fixture_proposals)`` — a thin harness that uses
   ``BuildSession + propose_leaf_tags`` and returns the canonical metric
   schema (semantic_apply_ok / metadata_apply_ok / reason-freq /
   evidence_source breakdown). External LLM/Agent layers fill in
   ``fixture_proposals`` to supply real evidence.
2. ``METRICS_SCHEMA`` — the canonical field list, frozen here so future
   benchmark versions cannot silently drop or rename fields.
3. ``__main__`` smoke — a tiny self-contained run against a tempdir fixture
   so wiring (`__init__.py` exports, BuildSession counters, defense_filter,
   full-replacement contract) keeps working end-to-end.

Attribution: results from this harness measure the TagProposal evidence
contract and should be reported separately from A/B/C search-flow metrics.
"""

from __future__ import annotations

import json
import sys
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
MEMORY = REPO / "memory"
if str(MEMORY) not in sys.path:
    sys.path.insert(0, str(MEMORY))

from local_semantic_overlay import BuildSession, prepare_leaf_tag_task  # noqa: E402
from local_semantic_overlay import overlay as ov  # noqa: E402

METRICS_SCHEMA = (
    "candidate_path_count", "selected_count", "readable_count", "skipped_count",
    "proposal_count", "proposal_accepted", "proposal_rejected",
    "semantic_applied_count", "metadata_applied_count",
    "semantic_apply_ok", "metadata_apply_ok",
    "evidence_source_text_head",
    "apply_ok", "apply_fail",
    "unique_leaf_before", "unique_leaf_after", "unique_leaf_delta",
    "rejection_reasons",
)


def run(scope: str, fixture_proposals: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    """Run BuildSession against ``fixture_proposals`` (leaf path → proposals).

    Returns a metric dict whose top-level keys are exactly ``METRICS_SCHEMA``.
    ``rejection_reasons`` is a Counter-derived dict for downstream ablation;
    everything else mirrors ``BuildSession.finalize()['process']``.
    """
    session = BuildSession(scope)
    session.add_candidates(len(fixture_proposals))
    for path, proposals in fixture_proposals.items():
        rr = session.try_read(path)  # readable / skipped both tracked
        lid = session.ensure_leaf(path)
        if not prepare_leaf_tag_task(scope, lid)["ok"]:
            continue
        session.propose_tags(lid, proposals)
    audit = session.finalize()
    proc = audit["process"]
    reasons: Counter[str] = Counter()
    for entry in proc.get("proposal_log") or []:
        for rej in entry.get("rejected") or []:
            reasons[rej.get("reason") or "unknown"] += 1
    metrics = {k: proc.get(k) for k in METRICS_SCHEMA if k != "rejection_reasons"}
    metrics["rejection_reasons"] = dict(reasons)
    return metrics


def _smoke() -> dict[str, Any]:
    """Self-contained wiring smoke: tempdir fixture, no external LLM, no I/O leak."""
    with tempfile.TemporaryDirectory() as td:
        scope = td
        original = ov.OVERLAYS
        ov.OVERLAYS = Path(td) / "overlays"
        try:
            readable = Path(td) / "rdoc.txt"
            readable.write_text("深度学习模型训练与推理优化框架完整设计", encoding="utf-8")
            binary = Path(td) / "项目结题报告.bin"
            binary.write_bytes(b"\x00\x01\x02")
            # Pre-register both leaves so the harness sees them as candidates.
            ov.ensure_leaf(scope, str(readable))
            ov.ensure_leaf(scope, str(binary))
            fixtures = {
                str(readable): [
                    {"tag": "深度学习", "evidence_phrase": "深度学习模型训练",
                     "evidence_source": "text_head", "tag_role": "content_semantic"},
                    {"tag": "txt", "evidence_phrase": "深度学习模型训练",
                     "evidence_source": "text_head", "tag_role": "content_semantic"},
                ],
                str(binary): [
                    {"tag": "项目结题报告", "evidence_phrase": "项目结题报告",
                     "evidence_source": "filename", "tag_role": "content_semantic"},
                ],
            }
            metrics = run(scope, fixtures)
        finally:
            ov.OVERLAYS = original
    return metrics


if __name__ == "__main__":
    out = _smoke()
    print(json.dumps(out, ensure_ascii=False, indent=2))
    # wiring assertions — fail loudly if contract drifted
    assert out["semantic_apply_ok"] == 1, out
    assert out["evidence_source_text_head"] == 1, out
    assert out["rejection_reasons"].get("tag_is_extension") == 1, out
    assert out["rejection_reasons"].get("invalid_evidence_source") == 1, out

