"""Evidence-first directory macro compression. No directory-name semantics."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any

from . import store
from .config import KEY_EVIDENCE_EVIDENCE_TYPES
from .evidence_title import sanitize_display_text
from .leaf_seed import _detect_org_signals, is_key_evidence_file

KEY_EVIDENCE_COMPRESSION_ROLE = "key_evidence_macro"


def is_key_evidence_leaf(leaf: dict[str, Any]) -> bool:
    if leaf.get("seed_source") == "key_evidence":
        return True
    if leaf.get("evidence_type") in KEY_EVIDENCE_EVIDENCE_TYPES:
        return True
    if int(leaf.get("confirm_count") or 0) > 0:
        return True
    return is_key_evidence_file(Path(leaf["path"]))


def _organization_signals_from_leaf(leaf: dict[str, Any]) -> list[str]:
    signals: list[str] = []
    et = leaf.get("evidence_type")
    if et:
        signals.append(f"evidence_type:{et}")
    if leaf.get("text_head"):
        signals.append("has_text_head")
    if leaf.get("seed_source") == "user_confirmed" or int(leaf.get("confirm_count") or 0) > 0:
        signals.append("user_confirmed_evidence")
    parent = Path(leaf.get("parent_directory_path") or Path(leaf["path"]).parent)
    signals.extend(_detect_org_signals(parent))
    return list(dict.fromkeys(signals))


def _evidence_brief(leaves: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for leaf in leaves[:4]:
        if leaf.get("readable_status") in ("extract_failed", "binary"):
            continue
        head = sanitize_display_text((leaf.get("text_head") or ""))
        if head:
            parts.append(head[:160])
    return " | ".join(parts)[:500]


def compress_directories_from_key_evidence(session_id: str | None = None) -> dict[str, Any]:
    """
    key evidence leaf -> org_signals compression role (traceable).
    Does not create semantic tags or path-derived semantics.
    """
    leaves = store.list_leaves(limit=10000)
    if session_id:
        leaves = [l for l in leaves if l.get("session_id") == session_id]

    key_leaves = [l for l in leaves if is_key_evidence_leaf(l) and l.get("readable_status") == "readable"]
    by_dir: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for leaf in key_leaves:
        parent = leaf.get("parent_directory_path") or str(Path(leaf["path"]).parent)
        by_dir[parent].append(leaf)

    macros = 0
    for dir_path, evidence_leaves in by_dir.items():
        if not evidence_leaves:
            continue
        signals: list[str] = []
        for leaf in evidence_leaves:
            signals.extend(_organization_signals_from_leaf(leaf))
        key_ids = [l["leaf_id"] for l in evidence_leaves]
        key_paths = [l["path"] for l in evidence_leaves]
        existing = store.get_directory_node_by_path(dir_path)
        prev_org = existing.get("org_signals") if existing and isinstance(existing.get("org_signals"), dict) else {}
        org_payload = {
            "compression_role": KEY_EVIDENCE_COMPRESSION_ROLE,
            "macro_source": "key_evidence",
            "key_evidence_leaf_ids": key_ids,
            "key_evidence_paths": key_paths,
            "organization_signals": list(dict.fromkeys(signals)),
            "compression_brief": _evidence_brief(evidence_leaves),
        }
        for key in ("bundle_compression_basis", "bundle_coverage_boundary"):
            if prev_org.get(key) is not None:
                org_payload[key] = prev_org[key]
        node_type = existing.get("node_type") if existing else "directory_macro"
        if node_type not in ("semantic_node", "directory_macro", "organizational"):
            node_type = "directory_macro"
        if node_type == "semantic_node":
            pass  # keep semantic_node when tagging already promoted this directory
        else:
            node_type = "directory_macro"
        store.upsert_directory_node(
            dir_path,
            node_type=node_type,
            org_signals=org_payload,
            compression_weight=float(len(key_ids) * 2),
            activation_weight=max(float(len(key_ids) * 1.5), float(existing.get("activation_weight") or 0) if existing else 0),
            readable_leaf_count=len(evidence_leaves),
        )
        macros += 1

    return {
        "ok": True,
        "directory_macros": macros,
        "key_evidence_compression_nodes": macros,
        "key_evidence_leaves": len(key_leaves),
    }


_UNEXPANDED_SCOPE = (
    "Files under this directory are not individually tagged unless indexed as leaf seeds."
)


def node_has_key_evidence_compression(node: dict[str, Any]) -> bool:
    org = node.get("org_signals")
    return (
        isinstance(org, dict)
        and org.get("compression_role") == KEY_EVIDENCE_COMPRESSION_ROLE
        and bool(org.get("key_evidence_leaf_ids"))
    )


def coverage_boundary_for_node(node: dict[str, Any]) -> str:
    nt = node.get("node_type") or "container"
    if nt == "directory_macro":
        return "key_evidence_only"
    if nt == "semantic_node":
        return "key_evidence_plus_tagged_leaf_aggregation"
    if nt == "organizational":
        return "key_evidence_supported_organizational_node"
    return "key_evidence_supported"


def build_macro_node_reports(limit: int = 50) -> list[dict[str, Any]]:
    """Nodes with key-evidence compression role in org_signals; preserves actual node_type."""
    from .evidence_title import build_evidence_title

    reports: list[dict[str, Any]] = []
    for node in store.list_directory_nodes(limit=5000):
        org = node.get("org_signals")
        if not isinstance(org, dict) or not node_has_key_evidence_compression(node):
            continue
        key_ids = list(org.get("key_evidence_leaf_ids") or [])
        key_paths = list(org.get("key_evidence_paths") or [])[:5]
        brief = sanitize_display_text((org.get("compression_brief") or ""))
        if not brief:
            titles = []
            for lid in key_ids[:3]:
                leaf = store.get_leaf(lid)
                if leaf:
                    titles.append(build_evidence_title(leaf))
            brief = "; ".join(titles)[:500]
        node_type = node.get("node_type") or "container"
        reports.append({
            "path": node["path"],
            "node_type": node_type,
            "compression_role": org.get("compression_role"),
            "key_evidence_count": len(key_ids),
            "key_evidence_paths": key_paths,
            "compression_basis": brief[:500] if brief else "key_evidence",
            "coverage_boundary": coverage_boundary_for_node(node),
            "unexpanded_internal_scope": _UNEXPANDED_SCOPE,
        })
    reports.sort(key=lambda r: (-r["key_evidence_count"], r["path"]))
    return reports[:limit]
