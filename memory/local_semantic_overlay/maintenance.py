"""Overlay maintenance: feedback, active budget, and cold-node recheck."""

from __future__ import annotations

import os
from typing import Any

from . import overlay as ov
from .read import read_leaf, sanitize_display


def record_feedback(
    scope: str,
    *,
    result_id: str,
    kind: str,
    node_id: str | None = None,
    leaf_id: str | None = None,
    flags: ov.OverlayFlags | None = None,
) -> dict[str, Any]:
    fl = flags or ov.OverlayFlags()
    if not fl.enable_feedback or kind not in ("selected", "not_selected", "negative"):
        return ov._err("disabled" if not fl.enable_feedback else "bad_kind")
    if bool(node_id) == bool(leaf_id):
        return ov._err("bad_target")
    data = ov.load(scope)
    data["feedback"] = (data["feedback"] + [{
        "result_id": result_id, "node_id": node_id, "leaf_id": leaf_id,
        "kind": kind, "at": ov.now_iso(),
    }])[-ov.FEEDBACK_MAX:]
    if node_id and node_id in data["nodes"]:
        node = data["nodes"][node_id]
        key = {
            "selected": "selected_count",
            "not_selected": "not_selected_count",
            "negative": "negative_feedback_count",
        }[kind]
        node[key] = int(node.get(key) or 0) + 1
        if kind == "negative" and fl.enable_active_cold:
            node["status"] = "cold"
    ov.save(data)
    return ov._ok()


def enforce_active_budget(scope: str, *, flags: ov.OverlayFlags | None = None) -> dict[str, Any]:
    fl = flags or ov.OverlayFlags()
    if not fl.enable_active_cold:
        return ov._ok(demoted=[], skipped=True)
    data = ov.load(scope)
    budget = int(data["meta"].get("active_budget") or ov.DEFAULT_ACTIVE_BUDGET)
    active = [(nid, n) for nid, n in data["nodes"].items() if n.get("status") == "active"]
    if len(active) <= budget:
        return ov._ok(demoted=[])
    active.sort(key=lambda x: (x[1].get("last_hit_at") or "", -int(x[1].get("hit_count") or 0)))
    demoted = []
    while len(active) > budget:
        nid, node = active.pop(0)
        node["status"] = "cold"
        demoted.append(nid)
    ov.save(data)
    return ov._ok(demoted=demoted)


def supporting_files_changed(node: dict[str, Any], leaves: dict[str, Any]) -> bool:
    for lid in node.get("supporting_leaf_ids") or []:
        leaf = leaves.get(lid)
        if not leaf:
            return True
        path = leaf.get("path") or ""
        if not os.path.isfile(path):
            return True
        try:
            st = os.stat(path)
        except OSError:
            return True
        if st.st_mtime != leaf.get("mtime") or st.st_size != leaf.get("size"):
            return True
    return False


def prepare_recheck_task(scope: str, node_id: str) -> dict[str, Any]:
    data = ov.load(scope)
    node = data["nodes"].get(node_id)
    if not node:
        return ov._err("missing_node")
    samples = []
    for lid in node.get("supporting_leaf_ids") or []:
        leaf = data["leaves"].get(lid)
        if leaf:
            rr = read_leaf(leaf["path"])
            samples.append({"leaf_id": lid, "path": leaf["path"], "text_head": rr.get("text_head")})
    return ov._ok(task={
        "task": "recheck", "node_id": node_id, "label": node.get("label"),
        "supporting_evidence": samples,
        "output_schema": {"decision": "keep|delete|update"},
    })


def apply_recheck(scope: str, node_id: str, result: dict[str, Any]) -> dict[str, Any]:
    decision = (result.get("decision") or "keep").strip().lower()
    data = ov.load(scope)
    node = data["nodes"].get(node_id)
    if not node:
        return ov._err("missing_node")
    if decision == "delete":
        ov.delete_node(data, node_id)
        ov.save(data)
        return ov._ok(action="deleted", node_id=node_id)
    if decision == "update":
        lids = node.get("supporting_leaf_ids") or []
        if result.get("label"):
            node["label"] = result["label"].strip()
        if result.get("tags"):
            path = (data["leaves"].get(lids[0]) or {}).get("path") or ""
            node["semantic_tags"] = ov._clean_node_tags(result["tags"], path)
        if result.get("brief"):
            brief = sanitize_display(result["brief"])
            if berr := ov._validate_brief(brief, lids, data["leaves"]):
                return ov._err(berr)
            node["brief"] = brief
    node["status"] = "active"
    ov.save(data)
    return ov._ok(action=decision, node_id=node_id)
