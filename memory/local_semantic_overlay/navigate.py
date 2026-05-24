"""Runtime navigation — query with hit source labeling (ablation boundary B4)."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any

from ._config import GENERIC_TAG_STOPWORDS
from . import overlay as ov
from . import search as lso_search

_HIT_ORDER = {"semantic_node": 0, "leaf_tag": 1, "path": 2, "fallback": 3}


@dataclass
class NavigateFlags:
    enable_semantic: bool = True
    enable_leaf_tags: bool = True
    enable_path: bool = True
    enable_fallback: bool = True
    include_cold: bool = False


def _err(code: str, msg: str = "") -> dict[str, Any]:
    return {"ok": False, "error": code, "message": msg}


def _tokens(q: str) -> list[str]:
    low = (q or "").lower().replace("\u3000", " ")
    toks = re.findall(r"[a-z0-9][a-z0-9_\-]{1,}", low) + re.findall(r"[\u4e00-\u9fff]{2,}", low)
    out, seen = [], set()
    for t in toks:
        if t not in GENERIC_TAG_STOPWORDS and t not in seen:
            seen.add(t); out.append(t)
    return out


def _match(text: str, toks: list[str]) -> bool:
    return bool(toks) and any(t in (text or "").lower() for t in toks)


def _rank(hit: dict[str, Any]) -> tuple[int, int, int]:
    return (0 if hit.get("status") == "active" else 1,
            _HIT_ORDER.get(hit.get("hit_type"), 9),
            0 if hit.get("direct_match") else 1)


def query(scope: str, text: str, *, limit: int = 20, flags: NavigateFlags | None = None) -> dict[str, Any]:
    fl, data, toks = flags or NavigateFlags(), ov.load(scope), _tokens(text)
    hits: list[dict[str, Any]] = []
    if not toks:
        return {"ok": True, "query_tokens": toks, "hits": hits}

    if fl.enable_semantic:
        for nid, node in data["nodes"].items():
            if not fl.include_cold and node.get("status") == "cold":
                continue
            blob = " ".join([node.get("label") or "", " ".join(node.get("semantic_tags") or []),
                             node.get("brief") or "", node.get("anchor") or ""])
            if _match(blob, toks):
                hits.append({"hit_type": "semantic_node", "source": "overlay", "node_id": nid,
                             "label": node.get("label"), "tags": node.get("semantic_tags") or [],
                             "brief": node.get("brief"), "status": node.get("status"), "direct_match": True})

    if fl.enable_leaf_tags:
        for lid, leaf in data["leaves"].items():
            tags = leaf.get("semantic_tags") or []
            if tags and _match(" ".join(tags + [leaf.get("path") or "", os.path.basename(leaf.get("path") or "")]), toks):
                hits.append({"hit_type": "leaf_tag", "source": "overlay", "leaf_id": lid,
                             "path": leaf.get("path"), "tags": tags, "status": "active", "direct_match": True})

    if fl.enable_path:
        for lid, leaf in data["leaves"].items():
            path = leaf.get("path") or ""
            if not _match(" ".join([path, os.path.basename(path), leaf.get("anchor") or ""]), toks):
                continue
            if any(h.get("leaf_id") == lid and h.get("hit_type") == "leaf_tag" for h in hits):
                continue
            hits.append({"hit_type": "path", "source": "overlay", "leaf_id": lid,
                         "path": path, "status": "active", "direct_match": True})

    if fl.enable_fallback:
        for row in lso_search.search_rows(text, scope=scope, limit=limit):
            p = row.get("path") or ""
            hits.append({"hit_type": "fallback", "source": "fallback", "path": p,
                         "name": row.get("name"), "mtime": row.get("mtime"), "size": row.get("size"),
                         "direct_match": _match(p, toks), "status": "active"})

    hits.sort(key=_rank)
    return {"ok": True, "query_tokens": toks, "hits": hits[:limit] if limit > 0 else hits}


def record_hit(scope: str, node_id: str) -> dict[str, Any]:
    data = ov.load(scope)
    node = data["nodes"].get(node_id)
    if not node:
        return _err("missing_node", node_id)
    node["last_hit_at"] = ov.now_iso()
    node["hit_count"] = int(node.get("hit_count") or 0) + 1
    action = "hit_recorded"
    if node.get("status") == "cold":
        if ov.supporting_files_changed(node, data["leaves"]):
            action = "needs_recheck"
        else:
            node["status"] = "active"
            action = "restored_active"
    ov.save(data)
    return {"ok": True, "node_id": node_id, "status": node.get("status"), "action": action}


def recheck_cold_node(scope: str, node_id: str) -> dict[str, Any]:
    data = ov.load(scope)
    node = data["nodes"].get(node_id)
    if not node:
        return _err("missing_node", node_id)
    if node.get("status") != "cold":
        return {"ok": True, "action": "not_cold", "node_id": node_id}
    if not ov.supporting_files_changed(node, data["leaves"]):
        node["status"] = "active"
        ov.save(data)
        return {"ok": True, "action": "restored_active", "node_id": node_id}
    return ov.prepare_recheck_task(scope, node_id)
