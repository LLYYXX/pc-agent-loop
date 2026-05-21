"""Evidence bundle scheduling for Phase A — ingestion-time only, not runtime assets."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import store
from .config import (
    BULK_TAGGING_LEAF_THRESHOLD,
    DEFAULT_BUNDLE_CANDIDATE_CAP,
    DEFAULT_BUNDLE_KEY_EVIDENCE_CAP,
    DEFAULT_BUNDLE_TEXT_HEAD_CHARS,
)
from .directory_macro import is_key_evidence_leaf, node_has_key_evidence_compression
from .evidence_title import build_evidence_title
from .leaf_tagging import (
    apply_leaf_tags,
    is_pending_tag_target,
    leaf_tag_schema,
    list_pending_tagging_leaves,
)
from .session_pending import prune_stale_pending_bundles


def bundle_annotation_schema() -> dict[str, Any]:
    base = leaf_tag_schema()
    return {
        "fields": {
            "leaf_annotations": "list[{leaf_id, tags:[{tag, evidence_note, tag_type?}]}]",
            "defer_leaf_ids": "list[str] — mark readable seeds deferred, no tags",
            "compression_basis": "optional str — org_signals supplement only",
            "coverage_boundary": "optional str — org_signals supplement only",
        },
        "rules": base.get("rules", [])
        + [
            "anchor_directory is positioning only — not semantic evidence",
            "leaf ids must belong to this bundle's exposed primary/key/candidate set",
            "not all bundle leaves must be tagged; use defer_leaf_ids for uncertainty",
            "primary_leaf_id must be tagged (non-empty tags) or listed in defer_leaf_ids",
            "compression_basis does not replace leaf_tag_edges",
            f"max {BULK_TAGGING_LEAF_THRESHOLD} leaf_annotations per apply (bulk gate)",
        ],
    }


def _leaf_item(leaf: dict[str, Any]) -> dict[str, Any]:
    text_head = leaf.get("text_head")
    if isinstance(text_head, str) and len(text_head) > DEFAULT_BUNDLE_TEXT_HEAD_CHARS:
        text_head = text_head[:DEFAULT_BUNDLE_TEXT_HEAD_CHARS]
    return {
        "leaf_id": leaf["leaf_id"],
        "path": leaf["path"],
        "text_head": text_head,
        "seed_source": leaf.get("seed_source"),
        "evidence_type": leaf.get("evidence_type"),
        "evidence_title": build_evidence_title(leaf),
    }


def _anchor_path(leaf: dict[str, Any]) -> str:
    return leaf.get("parent_directory_path") or str(Path(leaf["path"]).parent)


def _existing_context_for_anchor(anchor: str) -> dict[str, Any]:
    ctx: dict[str, Any] = {"tags_in_anchor": [], "org_signals": None, "macro_compression": False}
    node = store.get_directory_node_by_path(anchor)
    if node:
        org = node.get("org_signals")
        if isinstance(org, dict):
            ctx["org_signals"] = {
                k: org.get(k)
                for k in (
                    "compression_role", "compression_brief", "key_evidence_paths",
                    "bundle_compression_basis", "bundle_coverage_boundary",
                )
                if org.get(k) is not None
            }
            ctx["macro_compression"] = node_has_key_evidence_compression(node)
    tagged_ids = {e["leaf_id"] for e in store.list_leaf_tag_edges()}
    for leaf in store.list_leaves(limit=5000):
        if leaf.get("parent_directory_path") == anchor and leaf["leaf_id"] in tagged_ids:
            for edge in store.list_leaf_tag_edges(leaf_id=leaf["leaf_id"])[:3]:
                tag = store.get_tag(edge["tag_id"])
                if tag:
                    ctx["tags_in_anchor"].append(tag["tag"])
    ctx["tags_in_anchor"] = list(dict.fromkeys(ctx["tags_in_anchor"]))[:10]
    return ctx


def _key_evidence_leaves_for_anchor(session_id: str, anchor: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for leaf in store.list_leaves(session_id=session_id, readable_status="readable", limit=50000):
        if _anchor_path(leaf) == anchor and is_key_evidence_leaf(leaf):
            out.append(leaf)
    out.sort(key=lambda leaf: str(leaf.get("path") or ""))
    return out[:DEFAULT_BUNDLE_KEY_EVIDENCE_CAP]


def _pending_bundle_for_primary(state: dict[str, Any], primary_leaf_id: str) -> tuple[str | None, dict[str, Any] | None]:
    for bid, meta in (state.get("pending_bundles") or {}).items():
        if meta.get("primary_leaf_id") == primary_leaf_id:
            return bid, meta
    return None, None


def _leaf_annotation_has_tags(raw: dict[str, Any]) -> bool:
    return bool(raw.get("tags"))


def _compose_bundle(
    session_id: str,
    session: dict[str, Any],
    state: dict[str, Any],
    *,
    bundle_id: str,
    register: bool,
) -> dict[str, Any] | None:
    bundle_budget = int(session.get("bundle_budget") or 0)
    if state.get("bundles_processed", 0) >= bundle_budget:
        return None

    pending = list_pending_tagging_leaves(session_id=session_id)
    if not pending:
        return None

    primary = pending[0]
    anchor = _anchor_path(primary)
    primary_id = primary["leaf_id"]

    candidates = [
        leaf for leaf in pending
        if _anchor_path(leaf) == anchor and leaf["leaf_id"] != primary_id
    ][:DEFAULT_BUNDLE_CANDIDATE_CAP]
    candidate_ids = {l["leaf_id"] for l in candidates}
    pending_ids = {l["leaf_id"] for l in pending}

    key_leaves = [
        l for l in _key_evidence_leaves_for_anchor(session_id, anchor)
        if l["leaf_id"] not in pending_ids
    ]
    key_ids = {l["leaf_id"] for l in key_leaves}

    allowed_leaf_ids = {primary_id} | key_ids | candidate_ids

    return {
        "bundle_id": bundle_id,
        "session_id": session_id,
        "anchor_directory": anchor,
        "primary_leaf_id": primary_id,
        "primary_leaf_item": _leaf_item(primary),
        "key_evidence_items": [_leaf_item(l) for l in key_leaves],
        "candidate_leaf_items": [_leaf_item(l) for l in candidates],
        "allowed_leaf_ids": sorted(allowed_leaf_ids),
        "existing_context": _existing_context_for_anchor(anchor),
        "preview": not register,
        "constraints": [
            "anchor_directory is positioning only — not tag or evidence_note content",
            "primary_leaf is the global pending-queue head for this bundle",
            "primary must be tagged (non-empty tags) or listed in defer_leaf_ids on apply",
            "only annotate/defer leaf ids exposed in this bundle",
            "each tag requires leaf-specific evidence_note from text_head / evidence_title",
            "defer uncertain leaves via defer_leaf_ids",
        ],
        "budget_info": _budget_info(session, state),
        "payload_limits": {
            "max_key_evidence_items": DEFAULT_BUNDLE_KEY_EVIDENCE_CAP,
            "max_candidate_items": DEFAULT_BUNDLE_CANDIDATE_CAP,
            "max_text_head_chars": DEFAULT_BUNDLE_TEXT_HEAD_CHARS,
        },
    }


def next_evidence_bundle(
    session_id: str,
    *,
    register: bool = True,
    hygiene: bool = True,
) -> dict[str, Any]:
    """Seed-first bundle.

    register=False: compose only, no new pending_bundles entry.
    hygiene=False: no prune_stale_pending_bundles / session state patch (true dry-run).
    """
    session = store.get_ingestion_session(session_id)
    if not session:
        return {"ok": False, "error": "session not found", "bundle": None}

    state = store.get_session_state(session_id)
    bundle_budget = int(session.get("bundle_budget") or 0)
    if state.get("bundles_processed", 0) >= bundle_budget:
        return {
            "ok": True,
            "bundle": None,
            "reason": "bundle_budget_exhausted",
            "budget_info": _budget_info(session, state),
            "schema": bundle_annotation_schema(),
        }

    pending_queue = list_pending_tagging_leaves(session_id=session_id)
    head_id = pending_queue[0]["leaf_id"] if pending_queue else None
    if hygiene:
        state = store.patch_session_state(
            session_id,
            {"pending_bundles": prune_stale_pending_bundles(session_id, queue_head_leaf_id=head_id)},
        )
    else:
        state = store.get_session_state(session_id)
    if not pending_queue:
        return {
            "ok": True,
            "bundle": None,
            "reason": "no_pending_leaves",
            "remaining_bundles": 0,
            "budget_info": _budget_info(session, state),
            "schema": bundle_annotation_schema(),
        }

    primary_id = pending_queue[0]["leaf_id"]
    existing_id, _ = _pending_bundle_for_primary(state, primary_id)
    bundle_id = existing_id if (register and existing_id) else store.new_id("bdl")

    bundle = _compose_bundle(session_id, session, state, bundle_id=bundle_id, register=register)
    if not bundle:
        return {
            "ok": True,
            "bundle": None,
            "reason": "no_pending_leaves",
            "remaining_bundles": 0,
            "budget_info": _budget_info(session, state),
            "schema": bundle_annotation_schema(),
        }

    if register and not existing_id:
        pending_map = dict(state.get("pending_bundles") or {})
        pending_map[bundle_id] = {
            "anchor_directory": bundle["anchor_directory"],
            "primary_leaf_id": bundle["primary_leaf_id"],
            "allowed_leaf_ids": bundle["allowed_leaf_ids"],
            "session_id": session_id,
            "created": store.now_iso(),
        }
        store.patch_session_state(session_id, {"pending_bundles": pending_map})

    remaining = max(0, bundle_budget - int(state.get("bundles_processed") or 0))
    return {
        "ok": True,
        "bundle": bundle,
        "remaining_bundles": remaining,
        "schema": bundle_annotation_schema(),
    }


def preview_next_evidence_bundle(session_id: str) -> dict[str, Any]:
    return next_evidence_bundle(session_id, register=False, hygiene=False)


def _budget_info(session: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    ann_budget = int(session.get("annotation_budget") or 0)
    ann_used = int(state.get("annotations_applied") or 0)
    return {
        "candidate_leaf_budget": session.get("candidate_leaf_budget"),
        "annotation_budget": ann_budget,
        "annotation_remaining": max(0, ann_budget - ann_used),
        "bundle_budget": int(session.get("bundle_budget") or 0),
        "bundles_processed": int(state.get("bundles_processed") or 0),
        "bundle_remaining": max(0, int(session.get("bundle_budget") or 0) - int(state.get("bundles_processed") or 0)),
    }


def _validate_bundle_leaf_refs(
    *,
    session_id: str,
    anchor: str,
    allowed_leaf_ids: set[str],
    leaf_annotations: list[dict[str, Any]],
    defer_ids: list[str],
) -> dict[str, Any] | None:
    """Return error dict if any leaf ref is out of bundle scope."""
    bad: list[dict[str, Any]] = []

    def check_id(lid: str, role: str, *, require_pending: bool) -> None:
        if lid not in allowed_leaf_ids:
            bad.append({"leaf_id": lid, "role": role, "error": "leaf_not_in_bundle"})
            return
        leaf = store.get_leaf(lid)
        if not leaf:
            bad.append({"leaf_id": lid, "role": role, "error": "leaf_not_found"})
            return
        if leaf.get("session_id") != session_id:
            bad.append({"leaf_id": lid, "role": role, "error": "wrong_session"})
            return
        if _anchor_path(leaf) != anchor:
            bad.append({"leaf_id": lid, "role": role, "error": "wrong_anchor"})
            return
        if require_pending and not is_pending_tag_target(leaf):
            bad.append({"leaf_id": lid, "role": role, "error": "not_pending_tag_target"})

    for raw in leaf_annotations:
        lid = raw.get("leaf_id")
        if lid:
            check_id(str(lid), "leaf_annotation", require_pending=True)

    for lid in defer_ids:
        check_id(str(lid), "defer", require_pending=True)

    if bad:
        return {"ok": False, "error": "bundle_leaf_scope_violation", "violations": bad}
    return None


def _rollback_bundle_tag_apply(tag_result: dict[str, Any]) -> None:
    touched: set[str] = set()
    touched_tags: set[str] = set()
    for row in tag_result.get("results") or []:
        lid = row.get("leaf_id")
        if lid:
            touched.add(str(lid))
        for item in row.get("tags") or []:
            edge = item.get("edge") if isinstance(item, dict) else None
            if isinstance(edge, dict) and edge.get("edge_id"):
                if edge.get("tag_id"):
                    touched_tags.add(str(edge["tag_id"]))
                store.delete_leaf_tag_edge(edge["edge_id"])
    for lid in touched:
        if not store.list_leaf_tag_edges(leaf_id=lid):
            store.update_leaf_semantic_status(lid, "seed")
    for tid in touched_tags:
        store.delete_semantic_tag_if_unused(tid)


def _patch_bundle_org_signals(anchor: str, compression_basis: str, coverage_boundary: str) -> None:
    node = store.get_directory_node_by_path(anchor)
    if not node:
        return
    org = node.get("org_signals")
    org = dict(org) if isinstance(org, dict) else {}
    if compression_basis:
        org["bundle_compression_basis"] = compression_basis[:500]
    if coverage_boundary:
        org["bundle_coverage_boundary"] = coverage_boundary[:200]
    store.upsert_directory_node(anchor, org_signals=org)


def apply_bundle_annotations(
    bundle_id: str,
    annotations: dict[str, Any],
    *,
    session_id: str | None = None,
) -> dict[str, Any]:
    session_id = session_id or store.get_latest_open_session_id()
    if not session_id:
        return {"ok": False, "error": "no session"}

    session = store.get_ingestion_session(session_id)
    if not session:
        return {"ok": False, "error": "session not found"}

    state = store.get_session_state(session_id)
    pending = state.get("pending_bundles") or {}
    if bundle_id not in pending:
        return {"ok": False, "error": "unknown or expired bundle_id", "bundle_id": bundle_id}

    meta = pending[bundle_id]
    anchor = meta.get("anchor_directory") or annotations.get("anchor_directory")
    allowed = set(meta.get("allowed_leaf_ids") or [])
    leaf_annotations = annotations.get("leaf_annotations") or []
    defer_ids = list(annotations.get("defer_leaf_ids") or [])

    scope_err = _validate_bundle_leaf_refs(
        session_id=session_id,
        anchor=anchor or "",
        allowed_leaf_ids=allowed,
        leaf_annotations=leaf_annotations,
        defer_ids=defer_ids,
    )
    if scope_err:
        return {**scope_err, "bundle_id": bundle_id}

    for raw in leaf_annotations:
        lid = raw.get("leaf_id")
        if lid and not _leaf_annotation_has_tags(raw):
            return {
                "ok": False,
                "error": "empty_leaf_tags",
                "bundle_id": bundle_id,
                "leaf_id": lid,
            }

    primary_id = meta.get("primary_leaf_id")
    tagged_ann_ids = {
        str(raw["leaf_id"]) for raw in leaf_annotations
        if raw.get("leaf_id") and _leaf_annotation_has_tags(raw)
    }
    defer_set = {str(lid) for lid in defer_ids}
    if primary_id and str(primary_id) not in tagged_ann_ids and str(primary_id) not in defer_set:
        return {
            "ok": False,
            "error": "primary_not_resolved",
            "bundle_id": bundle_id,
            "primary_leaf_id": primary_id,
        }

    ann_budget = int(session.get("annotation_budget") or 0)
    ann_used = int(state.get("annotations_applied") or 0)
    new_ann = len(tagged_ann_ids)
    if new_ann > BULK_TAGGING_LEAF_THRESHOLD:
        return {
            "ok": False,
            "error": "bulk_tagging_detected",
            "message": f"max {BULK_TAGGING_LEAF_THRESHOLD} leaf_annotations per apply_bundle_annotations",
            "batch_size": new_ann,
        }
    if ann_used + new_ann > ann_budget:
        return {
            "ok": False,
            "error": "annotation_budget_exceeded",
            "annotation_budget": ann_budget,
            "annotation_budget_used": ann_used,
            "requested": new_ann,
        }

    for raw in leaf_annotations:
        raw["source"] = "bundle"
    tag_result = apply_leaf_tags(leaf_annotations)

    if leaf_annotations and (tag_result.get("errors") or not tag_result.get("ok")):
        _rollback_bundle_tag_apply(tag_result)
        return {
            "ok": False,
            "error": "tag_validation_failed",
            "bundle_id": bundle_id,
            "tag_apply": tag_result,
        }

    tagged_in_batch = {str(raw.get("leaf_id")) for raw in leaf_annotations if raw.get("leaf_id")}
    defer_ids = [lid for lid in defer_ids if lid not in tagged_in_batch]

    deferred = 0
    for lid in defer_ids:
        leaf = store.get_leaf(lid)
        if not leaf:
            continue
        if store.list_leaf_tag_edges(leaf_id=lid):
            continue
        store.update_leaf_semantic_status(lid, "deferred")
        deferred += 1

    compression_basis = (annotations.get("compression_basis") or "").strip()
    coverage_boundary = (annotations.get("coverage_boundary") or "").strip()
    if anchor and (compression_basis or coverage_boundary):
        _patch_bundle_org_signals(anchor, compression_basis, coverage_boundary)

    pending = dict(pending)
    pending.pop(bundle_id, None)

    new_state = store.patch_session_state(session_id, {
        "pending_bundles": pending,
        "bundles_processed": int(state.get("bundles_processed") or 0) + 1,
        "annotations_applied": ann_used + int(tag_result.get("applied") or 0),
    })

    return {
        "ok": tag_result.get("ok", False) and len(tag_result.get("errors", [])) == 0,
        "bundle_id": bundle_id,
        "anchor_directory": anchor,
        "tag_apply": tag_result,
        "deferred_count": deferred,
        "budget_usage": _budget_info(session, new_state),
    }
