"""Ingestion diagnostics — minimal partial-map report."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any

from . import store
from .config import ALLOWED_SEED_SOURCES
from .directory_macro import build_macro_node_reports, is_key_evidence_leaf, node_has_key_evidence_compression


def _file_type_bucket(leaf: dict[str, Any]) -> str:
    et = leaf.get("evidence_type") or ""
    suffix = Path(leaf["path"]).suffix.lower()
    if leaf.get("readable_status") == "binary":
        return "binary"
    if leaf.get("readable_status") == "extract_failed":
        return "extract_failed"
    if et in ("readme", "manifest") or suffix in {".md", ".rst"}:
        return "readme_markdown"
    if et in ("pdf_head", "office_head") or suffix in {".pdf", ".docx", ".pptx", ".xlsx", ".doc", ".ppt", ".xls"}:
        return "office_pdf"
    if suffix in {".csv", ".xlsx", ".xls"}:
        return "spreadsheet"
    if et in ("code_head",) or suffix in {".py", ".js", ".ts", ".go", ".rs", ".java", ".cpp", ".c", ".cs"}:
        return "code_config"
    if suffix in {".json", ".yaml", ".yml", ".toml", ".ini", ".cfg"}:
        return "code_config"
    if leaf.get("readable_status") == "readable":
        return "code_config"
    return "other"


def build_cost_metrics(
    session_id: str,
    *,
    session: dict[str, Any] | None = None,
    state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    session = session or store.get_ingestion_session(session_id)
    if not session:
        return {}
    state = state if state is not None else store.get_session_state(session_id)
    leaves = store.list_leaves(session_id=session_id, limit=50000)
    session_leaf_ids = {l["leaf_id"] for l in leaves}
    tagged_ids = {e["leaf_id"] for e in store.list_leaf_tag_edges() if e["leaf_id"] in session_leaf_ids}
    readable = [l for l in leaves if l.get("readable_status") == "readable"]
    remaining_candidate = sum(
        1 for l in readable
        if l["leaf_id"] not in tagged_ids and l.get("semantic_status") == "seed"
    )
    ann_budget = int(session.get("annotation_budget") or 0)
    ann_used = int(state.get("annotations_applied") or 0)
    bnd_budget = int(session.get("bundle_budget") or 0)
    bnd_used = int(state.get("bundles_processed") or 0)
    return {
        "candidate_leaf_budget": int(session.get("candidate_leaf_budget") or session.get("leaf_budget") or 0),
        "annotation_budget": ann_budget,
        "annotation_budget_used": ann_used,
        "bundle_budget": bnd_budget,
        "bundle_budget_used": bnd_used,
        "annotated_leaf_count": len(tagged_ids),
        "remaining_candidate_count": remaining_candidate,
        "small_budget_partial_map": bool(
            remaining_candidate > 0
            or (ann_budget > 0 and ann_used >= ann_budget)
            or (bnd_budget > 0 and bnd_used >= bnd_budget)
        ),
    }


def _top_level(scope: str, path: str) -> str:
    try:
        rel = Path(path).resolve().relative_to(Path(scope).resolve())
        parts = rel.parts
        return parts[0] if parts else "(root)"
    except ValueError:
        return "(outside_scope)"


def build_ingestion_diagnostics(session_id: str) -> dict[str, Any]:
    session = store.get_ingestion_session(session_id)
    if not session:
        return {"ok": False, "error": "session not found"}
    scope = session["scope"]
    leaves = store.list_leaves(session_id=session_id, limit=50000)
    overview_entries = store.list_overview_entries(limit=5000)
    nodes = store.list_directory_nodes(limit=5000)

    by_top_level: dict[str, dict[str, int]] = defaultdict(lambda: {
        "leaves": 0, "readable": 0, "tagged": 0, "overview_entries": 0,
    })
    by_file_type: dict[str, int] = defaultdict(int)
    by_seed_source: dict[str, int] = defaultdict(int)

    leaf_ids = {l["leaf_id"] for l in leaves}
    tagged_ids = {e["leaf_id"] for e in store.list_leaf_tag_edges() if e["leaf_id"] in leaf_ids}

    for leaf in leaves:
        tl = _top_level(scope, leaf["path"])
        by_top_level[tl]["leaves"] += 1
        if leaf.get("readable_status") == "readable":
            by_top_level[tl]["readable"] += 1
        if leaf["leaf_id"] in tagged_ids:
            by_top_level[tl]["tagged"] += 1
        by_file_type[_file_type_bucket(leaf)] += 1
        src = leaf.get("seed_source") or "unknown"
        by_seed_source[src if src in ALLOWED_SEED_SOURCES else "unknown"] += 1

    for entry in overview_entries:
        node = store.get_directory_node(entry["node_id"])
        if node:
            by_top_level[_top_level(scope, node["path"])]["overview_entries"] += 1

    readable_untagged = [
        l["path"] for l in leaves
        if l.get("readable_status") == "readable" and l["leaf_id"] not in tagged_ids
    ][:30]

    doc_rich_unrepresented: list[str] = []
    for leaf in leaves:
        if _file_type_bucket(leaf) in ("office_pdf", "readme_markdown", "spreadsheet"):
            if leaf["leaf_id"] not in tagged_ids:
                parent = leaf.get("parent_directory_path") or ""
                node = store.get_directory_node_by_path(parent) if parent else None
                if not node or (
                    node.get("node_type") not in ("semantic_node", "directory_macro", "organizational")
                    and not node_has_key_evidence_compression(node)
                ):
                    doc_rich_unrepresented.append(leaf["path"])
    doc_rich_unrepresented = doc_rich_unrepresented[:30]

    org_evidence_not_compressed: list[str] = []
    for leaf in leaves:
        if is_key_evidence_leaf(leaf) and leaf.get("readable_status") == "readable":
            parent = leaf.get("parent_directory_path") or ""
            node = store.get_directory_node_by_path(parent) if parent else None
            if not node or not node_has_key_evidence_compression(node):
                org_evidence_not_compressed.append(parent or leaf["path"])
    org_evidence_not_compressed = list(dict.fromkeys(org_evidence_not_compressed))[:30]

    macro_nodes = build_macro_node_reports(limit=50)
    key_evidence_compression_count = sum(1 for n in nodes if node_has_key_evidence_compression(n))
    directory_macro_type_count = sum(1 for n in nodes if n.get("node_type") == "directory_macro")

    cost_metrics = build_cost_metrics(session_id, session=session, state=store.get_session_state(session_id))

    return {
        "ok": True,
        "session_id": session_id,
        "scope": scope,
        "by_top_level": dict(by_top_level),
        "by_file_type": dict(by_file_type),
        "by_seed_source": dict(by_seed_source),
        "macro_nodes": macro_nodes,
        "cost_metrics": cost_metrics,
        "blind_spots": {
            "readable_heavy_but_untagged": readable_untagged,
            "document_rich_but_unrepresented": doc_rich_unrepresented,
            "organization_evidence_found_but_not_compressed": org_evidence_not_compressed,
        },
        "summary": {
            "total_leaves": len(leaves),
            "readable_leaves": sum(1 for l in leaves if l.get("readable_status") == "readable"),
            "tagged_leaves": len(tagged_ids),
            "directory_macros": directory_macro_type_count,
            "key_evidence_compression_nodes": key_evidence_compression_count,
            "overview_entries": len(overview_entries),
            "partial_map_warning": (
                "This index is partial semantic coverage, not a complete filesystem map."
            ),
        },
    }


def format_diagnostics_summary(
    diagnostics: dict[str, Any],
    *,
    status: str,
    metrics: dict[str, Any] | None = None,
    failures: list[str] | None = None,
) -> str:
    if not diagnostics.get("ok"):
        return f"LSO ingestion diagnostics unavailable: {diagnostics.get('error', 'unknown')}"

    lines: list[str] = []
    s = diagnostics.get("summary") or {}
    m = metrics or {}
    readable = s.get("readable_leaves", m.get("readable_leaves", 0))
    tagged = s.get("tagged_leaves", m.get("tagged_leaves", 0))

    lines.append("=== LSO Ingestion Report (partial semantic index) ===")
    lines.append(f"Status: {status}")
    lines.append(
        f"Indexed: {s.get('total_leaves', 0)} leaves, {readable} readable, {tagged} tagged, "
        f"{s.get('overview_entries', 0)} overview entries, "
        f"{s.get('key_evidence_compression_nodes', 0)} key-evidence compression nodes"
    )
    lines.append(
        f"Coverage note: {tagged}/{readable} tagged is NOT full-disk coverage — "
        "use blind spots below before trusting the map."
    )
    cost = diagnostics.get("cost_metrics") or {}
    if cost:
        lines.append("")
        lines.append("Budget:")
        lines.append(
            f"  • annotations {cost.get('annotation_budget_used')}/{cost.get('annotation_budget')}, "
            f"bundles {cost.get('bundle_budget_used')}/{cost.get('bundle_budget')}, "
            f"remaining_candidates={cost.get('remaining_candidate_count')}"
        )
    if failures:
        lines.append(f"Pipeline gaps: {'; '.join(failures)}")

    lines.append("")
    lines.append("By top-level directory:")
    for tl, counts in sorted(diagnostics.get("by_top_level", {}).items()):
        lines.append(
            f"  • {tl}: {counts.get('leaves', 0)} leaves, "
            f"{counts.get('readable', 0)} readable, {counts.get('tagged', 0)} tagged, "
            f"{counts.get('overview_entries', 0)} overview"
        )

    lines.append("")
    lines.append("By file type:")
    for ft, n in sorted(diagnostics.get("by_file_type", {}).items(), key=lambda x: -x[1]):
        lines.append(f"  • {ft}: {n}")

    blind = diagnostics.get("blind_spots") or {}
    lines.append("")
    lines.append("Blind spots:")
    untagged = blind.get("readable_heavy_but_untagged") or []
    lines.append(f"  • Readable but untagged: {len(untagged)} shown (max 30)")
    for p in untagged[:5]:
        lines.append(f"      - {p}")
    if len(untagged) > 5:
        lines.append(f"      … and {len(untagged) - 5} more")

    doc_unrep = blind.get("document_rich_but_unrepresented") or []
    if doc_unrep:
        lines.append(f"  • Document-rich, weak directory representation: {len(doc_unrep)}")
        for p in doc_unrep[:3]:
            lines.append(f"      - {p}")

    org_gap = blind.get("organization_evidence_found_but_not_compressed") or []
    if org_gap:
        lines.append(f"  • Key evidence not compressed to macro: {len(org_gap)}")
        for p in org_gap[:3]:
            lines.append(f"      - {p}")

    lines.append("")
    lines.append(s.get("partial_map_warning", "Partial index only."))
    return "\n".join(lines)


def format_agent_report(
    diagnostics: dict[str, Any],
    *,
    status: str,
    metrics: dict[str, Any] | None = None,
    failures: list[str] | None = None,
) -> str:
    if not diagnostics.get("ok"):
        return f"LSO partial map: diagnostics unavailable ({diagnostics.get('error', 'unknown')})."

    m = metrics or {}
    s = diagnostics.get("summary") or {}
    readable = m.get("readable_leaves", s.get("readable_leaves", 0))
    tagged = m.get("tagged_leaves", s.get("tagged_leaves", 0))
    scope = diagnostics.get("scope", "")

    cost = diagnostics.get("cost_metrics") or {}
    lines = [
        "=== LSO Agent Report (partial semantic map) ===",
        f"Status: {status} (structural flow only — not coverage success)",
        "This is a SMALL-BUDGET PARTIAL map — not full scope coverage.",
        f"Indexed readable seeds tagged: {tagged}/{readable} (indexed seeds only, not entire disk).",
    ]
    if cost.get("small_budget_partial_map"):
        lines.append(
            f"Budget: annotations {cost.get('annotation_budget_used')}/{cost.get('annotation_budget')}, "
            f"bundles {cost.get('bundle_budget_used')}/{cost.get('bundle_budget')}, "
            f"remaining candidates {cost.get('remaining_candidate_count', 0)}."
        )
    if failures:
        lines.append(f"Pipeline gaps: {'; '.join(failures)}")

    by_tl = diagnostics.get("by_top_level") or {}
    if by_tl:
        ranked = sorted(
            by_tl.items(),
            key=lambda x: (x[1].get("tagged", 0), x[1].get("readable", 0)),
            reverse=True,
        )[:3]
        lines.append("Main covered areas (top-level):")
        for tl, c in ranked:
            lines.append(f"  • {tl}: {c.get('tagged', 0)}/{c.get('readable', 0)} tagged")

    blind = diagnostics.get("blind_spots") or {}
    weak = (blind.get("readable_heavy_but_untagged") or [])[:3]
    if weak:
        lines.append("Weak / untagged examples:")
        for p in weak:
            lines.append(f"  • {p}")

    lines.append("Do NOT claim full coverage because audit_phase_a passed or tagged ratio is 100%.")
    if scope:
        lines.append(f"Scope: {scope}")
    return "\n".join(lines)
