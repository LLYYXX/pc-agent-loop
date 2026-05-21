"""Validate and write semantic_tags + leaf_tag_edges only. No directory logic."""

from __future__ import annotations

import re
from typing import Any

from . import store
from .config import BULK_TAGGING_LEAF_THRESHOLD, GENERIC_TAGS, SCHEDULING_SEED_SOURCE_ORDER, TAG_TYPES
from .evidence_title import build_evidence_title
from .tag_validation import validate_evidence_note, validate_tag

_PATH_ONLY_RE = re.compile(r"^[a-z]?[:\\/]|[\\/]{2,}", re.IGNORECASE)

SEED_PRIORITY = {src: i for i, src in enumerate(SCHEDULING_SEED_SOURCE_ORDER)}

# Internal tag writes allowed without apply_session_leaf_tags(session_id, ...).
_INGESTION_TAG_SOURCES = frozenset({"bundle", "session_packet"})


def is_pending_tag_target(leaf: dict[str, Any] | None) -> bool:
    if not leaf:
        return False
    if leaf.get("readable_status") != "readable" or leaf.get("semantic_status") != "seed":
        return False
    return len(store.list_leaf_tag_edges(leaf_id=leaf["leaf_id"])) == 0


def seed_sort_key(leaf: dict[str, Any]) -> tuple:
    return (
        SEED_PRIORITY.get(leaf.get("seed_source") or "", 9),
        str(leaf.get("updated_at") or ""),
        str(leaf.get("path") or ""),
    )


def _tagged_leaf_ids() -> set[str]:
    return {e["leaf_id"] for e in store.list_leaf_tag_edges()}


def list_pending_tagging_leaves(
    *,
    session_id: str | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """Global pending pool: readable + seed + no leaf_tag_edges (same as next_tagging_packet)."""
    tagged_ids = _tagged_leaf_ids()
    scan_limit = 50000 if session_id else 500
    if session_id:
        seeds = store.list_leaves(
            session_id=session_id,
            readable_status="readable",
            semantic_status="seed",
            limit=scan_limit,
        )
    else:
        seeds = store.list_leaves(
            readable_status="readable",
            semantic_status="seed",
            limit=scan_limit,
        )
    pending = [s for s in seeds if s["leaf_id"] not in tagged_ids]
    pending.sort(key=seed_sort_key)
    if limit is not None:
        return pending[:limit]
    return pending


def leaf_tag_schema() -> dict[str, Any]:
    return {
        "fields": {
            "leaf_id": "str, required",
            "tags": "list[{tag, tag_type?, evidence_note}] — non-obvious semantic only",
        },
        "rules": [
            "leaf must be readable_status=readable",
            "evidence_note required per tag (min 12 chars; cite text_head with semantic link)",
            "no generic tags (document, file, code, pdf, ...)",
            "no path-only tags or path-derived tags",
            "no template evidence_note (based on path, derived from filename, ...)",
            "no bulk apply_leaf_tags over all pending leaves — use next_tagging_packet batches",
            f"max {BULK_TAGGING_LEAF_THRESHOLD} annotations per apply_leaf_tags call",
        ],
    }


def _looks_path_only(tag: str) -> bool:
    t = tag.strip()
    if _PATH_ONLY_RE.search(t):
        return True
    parts = re.split(r"[-_/\\]", t)
    if len(parts) >= 4 and all(len(p) <= 3 for p in parts):
        return True
    return False


def apply_leaf_tags(annotations: list[dict[str, Any]], *, session_id: str | None = None) -> dict[str, Any]:
    """Apply leaf tags. During open ingestion, pass session_id or use apply_bundle_annotations."""
    if session_id is not None:
        return apply_session_leaf_tags(session_id, annotations)

    open_sid = store.get_latest_open_session_id()
    if open_sid:
        sess = store.get_ingestion_session(open_sid)
        if not sess or sess.get("status") != "open":
            open_sid = None
    if open_sid and annotations:
        bypass = all((raw.get("source") or "") in _INGESTION_TAG_SOURCES for raw in annotations)
        if not bypass:
            for raw in annotations:
                lid = raw.get("leaf_id")
                if not lid:
                    continue
                leaf = store.get_leaf(str(lid))
                if leaf and leaf.get("session_id") == open_sid:
                    return {
                        "ok": False,
                        "error": "session_id_required",
                        "message": (
                            "Open ingestion session — tag session leaves via "
                            "apply_bundle_annotations or apply_session_leaf_tags(session_id, ...)."
                        ),
                        "session_id": open_sid,
                    }

    batch_size = len(annotations)
    sources = {raw.get("source") or "llm" for raw in annotations}
    tag_source = sources.pop() if len(sources) == 1 else "mixed"

    if batch_size > BULK_TAGGING_LEAF_THRESHOLD:
        return {
            "ok": False,
            "applied": 0,
            "errors": [],
            "results": [],
            "warnings": [{
                "code": "bulk_tagging_detected",
                "batch_size": batch_size,
                "threshold": BULK_TAGGING_LEAF_THRESHOLD,
                "message": "Entire batch rejected — tag leaves via next_tagging_packet batches only.",
            }],
            "batch_size": batch_size,
            "tag_source": tag_source,
        }

    results: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []

    for raw in annotations:
        leaf_id = raw.get("leaf_id")
        tags = raw.get("tags") or []
        if not leaf_id:
            errors.append({"error": "missing leaf_id", "raw": raw})
            continue

        leaf = store.get_leaf(leaf_id)
        if not leaf:
            errors.append({"error": "leaf not found", "leaf_id": leaf_id})
            continue
        if leaf["readable_status"] != "readable":
            errors.append({"error": "leaf not readable", "leaf_id": leaf_id, "status": leaf["readable_status"]})
            continue

        applied_tags: list[dict[str, Any]] = []
        for item in tags:
            if isinstance(item, str):
                tag_str = item.strip()
                evidence_note = raw.get("evidence_note") or ""
                tag_type = "semantic"
            else:
                tag_str = (item.get("tag") or "").strip()
                evidence_note = (item.get("evidence_note") or "").strip()
                tag_type = item.get("tag_type") or "semantic"

            if not tag_str:
                errors.append({"error": "empty tag", "leaf_id": leaf_id})
                continue
            if tag_str.lower() in GENERIC_TAGS:
                errors.append({"error": f"generic tag: {tag_str}", "leaf_id": leaf_id})
                continue
            if _looks_path_only(tag_str):
                errors.append({"error": f"path-only tag: {tag_str}", "leaf_id": leaf_id})
                continue
            path_err = validate_tag(leaf, tag_str)
            if path_err:
                errors.append({"error": path_err, "leaf_id": leaf_id, "tag": tag_str})
                continue
            if not evidence_note:
                errors.append({"error": f"evidence_note required for tag {tag_str}", "leaf_id": leaf_id})
                continue
            note_err = validate_evidence_note(leaf, evidence_note)
            if note_err:
                errors.append({"error": note_err, "leaf_id": leaf_id, "tag": tag_str})
                continue
            if tag_type not in TAG_TYPES:
                tag_type = "semantic"

            tag_row = store.get_or_create_tag(tag_str, tag_type)
            edge = store.upsert_leaf_tag_edge(
                leaf_id, tag_row["tag_id"],
                weight=float(item.get("weight", 1.0)) if isinstance(item, dict) else 1.0,
                source=raw.get("source") or "llm",
                evidence_note=evidence_note,
            )
            applied_tags.append({"tag": tag_row["tag"], "edge": edge})

        if applied_tags:
            store.update_leaf_semantic_status(leaf_id, "tagged")
            results.append({"leaf_id": leaf_id, "tags": applied_tags})
            if (raw.get("source") or "") != "bundle":
                from .session_pending import invalidate_pending_after_leaf_tagged
                invalidate_pending_after_leaf_tagged(leaf_id)

    return {
        "ok": len(errors) == 0,
        "applied": len(results),
        "errors": errors,
        "results": results,
        "warnings": warnings,
        "batch_size": batch_size,
        "tag_source": tag_source,
    }


def _rollback_tag_apply(tag_result: dict[str, Any]) -> None:
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


def _validate_session_tag_targets(session_id: str, annotations: list[dict[str, Any]]) -> dict[str, Any] | None:
    violations: list[dict[str, Any]] = []
    for raw in annotations:
        lid = raw.get("leaf_id")
        if not lid:
            violations.append({"leaf_id": None, "error": "missing_leaf_id"})
            continue
        leaf = store.get_leaf(str(lid))
        if not leaf:
            violations.append({"leaf_id": lid, "error": "leaf_not_found"})
            continue
        if leaf.get("session_id") != session_id:
            violations.append({"leaf_id": lid, "error": "wrong_session"})
            continue
        if leaf.get("readable_status") != "readable":
            violations.append({"leaf_id": lid, "error": "leaf_not_readable"})
            continue
        if leaf.get("semantic_status") != "seed" or store.list_leaf_tag_edges(leaf_id=str(lid)):
            violations.append({"leaf_id": lid, "error": "not_pending_tag_target"})
    if violations:
        return {"ok": False, "error": "session_leaf_scope_violation", "violations": violations}
    return None


def apply_session_leaf_tags(session_id: str, annotations: list[dict[str, Any]]) -> dict[str, Any]:
    """Budget-accounted tail tagging for explicit deep/tail work."""
    session = store.get_ingestion_session(session_id)
    if not session:
        return {"ok": False, "error": "session not found"}

    batch_size = len(annotations)
    if batch_size > BULK_TAGGING_LEAF_THRESHOLD:
        return {
            "ok": False,
            "error": "bulk_tagging_detected",
            "message": f"max {BULK_TAGGING_LEAF_THRESHOLD} annotations per apply_session_leaf_tags",
            "batch_size": batch_size,
        }

    state = store.get_session_state(session_id)
    ann_budget = int(session.get("annotation_budget") or 0)
    ann_used = int(state.get("annotations_applied") or 0)
    if ann_used + batch_size > ann_budget:
        return {
            "ok": False,
            "error": "annotation_budget_exceeded",
            "annotation_budget": ann_budget,
            "annotation_budget_used": ann_used,
            "requested": batch_size,
        }

    scope_err = _validate_session_tag_targets(session_id, annotations)
    if scope_err:
        return scope_err

    prepared: list[dict[str, Any]] = []
    for raw in annotations:
        item = dict(raw)
        item["source"] = "session_packet"
        prepared.append(item)

    tag_result = apply_leaf_tags(prepared)
    if annotations and (tag_result.get("errors") or not tag_result.get("ok")):
        _rollback_tag_apply(tag_result)
        return {
            "ok": False,
            "error": "tag_validation_failed",
            "tag_apply": tag_result,
        }

    new_state = store.patch_session_state(
        session_id,
        {"annotations_applied": ann_used + int(tag_result.get("applied") or 0)},
    )
    return {
        "ok": tag_result.get("ok", False) and len(tag_result.get("errors", [])) == 0,
        "tag_apply": tag_result,
        "budget_usage": {
            "annotation_budget": ann_budget,
            "annotation_budget_used": int(new_state.get("annotations_applied") or 0),
            "annotation_remaining": max(0, ann_budget - int(new_state.get("annotations_applied") or 0)),
        },
    }


def next_tagging_packet(
    limit: int = 6,
    session_id: str | None = None,
    *,
    mode: str = "standard",
) -> dict[str, Any]:
    """Return next tagging packet.

  Standard Phase A uses evidence bundles only. Pass mode='tail' for explicit tail/deep packet work.
    """
    if session_id and mode != "tail":
        return {
            "ok": False,
            "error": "standard_mode_use_bundles",
            "message": (
                "Standard ingestion uses next_evidence_bundle / apply_bundle_annotations only. "
                "Call next_tagging_packet(..., mode='tail') only after bundle budget is exhausted "
                "and the user explicitly requests tail expansion."
            ),
        }

    pending = list_pending_tagging_leaves(session_id=session_id)
    batch = pending[:limit]
    packet = []
    for leaf in batch:
        packet.append({
            "leaf_id": leaf["leaf_id"],
            "path": leaf["path"],
            "text_head": leaf.get("text_head"),
            "seed_source": leaf.get("seed_source"),
            "evidence_title": build_evidence_title(leaf),
        })
    return {
        "ok": True,
        "packet": packet,
        "remaining": max(0, len(pending) - len(batch)),
        "schema": leaf_tag_schema(),
        "mode": mode,
    }
