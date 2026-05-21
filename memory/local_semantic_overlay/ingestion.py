"""Orchestration only. No core tagging, aggregation, overview, or runtime ranking logic."""

from __future__ import annotations

from typing import Any

from . import store
from .config import (
    DEFAULT_ANNOTATION_BUDGET,
    DEFAULT_BUNDLE_BUDGET,
    DEFAULT_CANDIDATE_LEAF_BUDGET,
    DEFAULT_LEAF_BUDGET,
)
from .diagnostics import build_ingestion_diagnostics, format_agent_report, format_diagnostics_summary
from .directory_agg import aggregate_directory_tags
from .directory_macro import compress_directories_from_key_evidence
from .evidence_bundle import preview_next_evidence_bundle
from .leaf_tagging import list_pending_tagging_leaves
from .leaf_seed import discover_leaf_seeds
from .overview_build import build_overview


def begin_ingestion(
    scope: str,
    leaf_budget: int | None = None,
    *,
    candidate_leaf_budget: int | None = None,
    annotation_budget: int = DEFAULT_ANNOTATION_BUDGET,
    bundle_budget: int = DEFAULT_BUNDLE_BUDGET,
) -> dict[str, Any]:
    cand = candidate_leaf_budget if candidate_leaf_budget is not None else (leaf_budget if leaf_budget is not None else DEFAULT_CANDIDATE_LEAF_BUDGET)
    store.init_db(reset=True)
    session = store.create_ingestion_session(
        scope,
        cand,
        candidate_leaf_budget=cand,
        annotation_budget=annotation_budget,
        bundle_budget=bundle_budget,
    )
    return {
        "ok": True,
        "session_id": session["session_id"],
        "session": session,
        "candidate_leaf_budget": cand,
        "annotation_budget": annotation_budget,
        "bundle_budget": bundle_budget,
    }


def finish_ingestion(session_id: str) -> dict[str, Any]:
    session = store.get_ingestion_session(session_id)
    if not session:
        return {"ok": False, "status": "failed", "success": False, "error": "session not found"}

    counts = store.lso_counts()
    tagged = counts.get("tagged_leaves", 0)
    readable = counts.get("readable_leaves", 0)
    semantic_nodes = counts.get("semantic_nodes", 0)
    overview_n = counts.get("overview_entries", 0)

    failures: list[str] = []
    if tagged > 0 and semantic_nodes == 0:
        failures.append("tagged leaves exist but no semantic_node")
    if semantic_nodes > 0 and overview_n == 0:
        failures.append("semantic nodes exist but no overview entries")

    tag_coverage = tagged / max(1, readable) if readable else 0.0
    overview_coverage = overview_n / max(1, semantic_nodes) if semantic_nodes else 0.0

    if failures:
        status = "failed"
    elif readable == 0 and tagged == 0:
        status = "failed"
    else:
        status = "completed"

    diagnostics = build_ingestion_diagnostics(session_id)
    metrics = {
        "readable_leaves": readable,
        "tagged_leaves": tagged,
        "tag_coverage": round(tag_coverage, 3),
        "semantic_nodes": semantic_nodes,
        "overview_entries": overview_n,
        "overview_coverage": round(overview_coverage, 3),
    }
    if diagnostics.get("cost_metrics"):
        metrics.update(diagnostics["cost_metrics"])

    diagnostics_summary = format_diagnostics_summary(
        diagnostics, status=status, metrics=metrics, failures=failures,
    )
    agent_report = format_agent_report(
        diagnostics, status=status, metrics=metrics, failures=failures,
    )

    report = {
        "status": status,
        "success": status == "completed",
        "metrics": metrics,
        "failures": failures,
        "diagnostics": diagnostics,
        "agent_report": agent_report,
        "diagnostics_summary": diagnostics_summary,
        "cost_metrics": diagnostics.get("cost_metrics", {}),
    }
    store.update_ingestion_session(session_id, status=status, report=report)
    return report


def audit_phase_a() -> dict[str, Any]:
    """Thin checks for Phase A acceptance criteria."""
    checks: list[dict[str, Any]] = []

    counts = store.lso_counts()
    checks.append({"name": "db_has_leaves", "ok": counts.get("leaves", 0) > 0})
    checks.append({"name": "has_tags", "ok": counts.get("tags", 0) > 0})
    checks.append({"name": "has_leaf_edges", "ok": counts.get("leaf_tag_edges", 0) > 0})

    entries = store.list_overview_entries(limit=10)
    trace_ok = True
    for e in entries[:3]:
        if not e.get("supporting_leaf_ids") or not e.get("evidence_refs"):
            trace_ok = False
    checks.append({"name": "overview_traceable", "ok": trace_ok or len(entries) == 0})

    failed = [c for c in checks if not c["ok"]]
    return {"ok": len(failed) == 0, "checks": checks, "failed": failed, "counts": counts}


def run_ingestion_step(session_id: str) -> dict[str, Any]:
    """Suggest next manual step only — does not execute pipeline or call LLM."""
    session = store.get_ingestion_session(session_id)
    if not session:
        return {"ok": False, "error": "session not found"}

    state = store.get_session_state(session_id)
    leaves = store.list_leaves(session_id=session_id, limit=1)

    if not leaves:
        return {
            "ok": True,
            "suggested_next_action": "discover_leaf_seeds",
            "hint": "Call discover_leaf_seeds(session_id) explicitly.",
        }

    if not state.get("compress_done"):
        return {
            "ok": True,
            "suggested_next_action": "compress_directories_from_key_evidence",
            "hint": "Call compress_directories_from_key_evidence(session_id) explicitly.",
        }

    bundle_budget = int(session.get("bundle_budget") or 0)
    if state.get("bundles_processed", 0) < bundle_budget and list_pending_tagging_leaves(session_id=session_id):
        prev = preview_next_evidence_bundle(session_id)
        return {
            "ok": True,
            "suggested_next_action": "annotate_bundle",
            "hint": (
                "Call next_evidence_bundle(session_id) once to register bundle_id, "
                "then apply_bundle_annotations(bundle_id, ...). "
                "Do not call run_ingestion_step in a loop for bundles."
            ),
            "preview": prev.get("bundle"),
            "schema": prev.get("schema"),
        }

    counts = store.lso_counts()
    if counts.get("tagged_leaves", 0) > 0 and counts.get("overview_entries", 0) == 0:
        return {
            "ok": True,
            "suggested_next_action": "aggregate_directory_tags",
            "hint": "Then build_overview(session_id); both explicit in SOP.",
        }

    return {
        "ok": True,
        "suggested_next_action": "finish_ingestion",
        "hint": "Call finish_ingestion; print agent_report and diagnostics_summary.",
    }


def mark_compress_done(session_id: str) -> None:
    store.patch_session_state(session_id, {"compress_done": True})


def run_ingestion_pipeline(session_id: str) -> dict[str, Any]:
    """Skeleton only: discover/compress/agg/overview/finish — does NOT run bundle loop or LLM tagging.

    Prefer explicit SOP: discover → compress → next_evidence_bundle loop → aggregate → overview → finish.
    """
    seed_result = discover_leaf_seeds(session_id)
    compress_result = compress_directories_from_key_evidence(session_id)
    mark_compress_done(session_id)
    agg_result = aggregate_directory_tags(session_id)
    ov_result = build_overview(session_id)
    finish = finish_ingestion(session_id)
    audit = audit_phase_a()
    return {
        "ok": seed_result.get("ok") and agg_result.get("ok") and ov_result.get("ok"),
        "seed": seed_result,
        "compress": compress_result,
        "aggregate": agg_result,
        "overview": ov_result,
        "finish": finish,
        "agent_report": finish.get("agent_report", ""),
        "diagnostics_summary": finish.get("diagnostics_summary", ""),
        "audit": audit,
    }
