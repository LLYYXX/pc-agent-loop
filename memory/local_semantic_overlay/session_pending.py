"""Session-scoped pending bundle hygiene (ingestion-time only)."""

from __future__ import annotations

from typing import Any

from . import store
from .leaf_tagging import is_pending_tag_target


def prune_stale_pending_bundles(session_id: str, *, queue_head_leaf_id: str | None = None) -> dict[str, Any]:
    """Drop pending bundles whose primary is stale (wrong head, tagged, or no longer a tag target)."""
    state = store.get_session_state(session_id)
    pending = dict(state.get("pending_bundles") or {})
    if not pending:
        return pending

    tagged = {e["leaf_id"] for e in store.list_leaf_tag_edges()}
    removed: list[str] = []
    for bid, meta in list(pending.items()):
        primary = meta.get("primary_leaf_id")
        drop = False
        if primary and primary in tagged:
            drop = True
        elif primary and queue_head_leaf_id and primary != queue_head_leaf_id:
            drop = True
        elif primary:
            leaf = store.get_leaf(primary)
            if not is_pending_tag_target(leaf):
                drop = True
        if drop:
            pending.pop(bid, None)
            removed.append(bid)

    if removed:
        store.patch_session_state(session_id, {"pending_bundles": pending})
    return pending


def invalidate_pending_after_leaf_tagged(leaf_id: str) -> None:
    sid = store.get_latest_open_session_id()
    if not sid:
        return
    state = store.get_session_state(sid)
    pending = dict(state.get("pending_bundles") or {})
    changed = False
    for bid, meta in list(pending.items()):
        primary = meta.get("primary_leaf_id")
        allowed = set(meta.get("allowed_leaf_ids") or [])
        if leaf_id == primary or leaf_id in allowed:
            pending.pop(bid, None)
            changed = True
    if changed:
        store.patch_session_state(sid, {"pending_bundles": pending})
