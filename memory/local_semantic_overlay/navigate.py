"""Runtime navigation — query with hit source labeling (ablation boundary B4)."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any

from . import maintenance as mt
from . import overlay as ov
from . import search as lso_search

_HIT_ORDER = {"semantic_node": 0, "leaf_tag": 1, "filename_hint": 2, "metadata": 3, "path": 4, "fallback": 5}

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
    """Mechanical tokenization, no stopword list (explainability via hit_type/source)."""
    low = (q or "").lower().replace("\u3000", " ")
    toks = re.findall(r"[a-z0-9][a-z0-9_\-]{1,}", low) + re.findall(r"[\u4e00-\u9fff]{2,}", low)
    out, seen = [], set()
    for t in toks:
        if t not in seen:
            seen.add(t); out.append(t)
    return out

def _match(text: str, toks: list[str]) -> bool:
    return bool(toks) and any(t in (text or "").lower() for t in toks)

def _reason(channel: str, values: list[str], toks: list[str]) -> dict[str, str] | None:
    vals = [v for v in values if v]
    if not _match(" ".join(vals), toks):
        return None
    for v in vals:
        if _match(v, toks):
            return {"channel": channel, "value": v}
    return {"channel": channel, "value": vals[0] if vals else ""}

def _rank(hit: dict[str, Any]) -> tuple[int, int, int]:
    return (0 if hit.get("status") == "active" else 1,
            _HIT_ORDER.get(hit.get("hit_type"), 9),
            0 if hit.get("direct_match") else 1)

def _leaf_hit(lid: str, leaf: dict[str, Any], reasons: list[dict[str, str]]) -> dict[str, Any]:
    path = leaf.get("path") or ""
    ch = reasons[0]["channel"]
    ht = {"semantic_tags": "leaf_tag", "filename_hint": "filename_hint",
          "location_tags": "metadata", "source_channel": "metadata"}.get(ch, "path")
    return {"hit_type": ht, "source": "overlay", "leaf_id": lid, "path": path,
            "filename": os.path.basename(path), "anchor": leaf.get("anchor"),
            "filename_hint": leaf.get("filename_hint") or os.path.splitext(os.path.basename(path))[0],
            "semantic_tags": leaf.get("semantic_tags") or [], "tags": leaf.get("semantic_tags") or [],
            "location_tags": leaf.get("location_tags") or [], "source_channel": leaf.get("source_channel"),
            "match_reasons": reasons, "status": "active", "direct_match": True}

def query(scope: str, text: str, *, limit: int = 20, flags: NavigateFlags | None = None) -> dict[str, Any]:
    fl, data, toks = flags or NavigateFlags(), ov.load(scope), _tokens(text)
    hits: list[dict[str, Any]] = []
    if not toks:
        return {"ok": True, "query_tokens": toks, "hits": hits}

    if fl.enable_semantic:
        for nid, node in data["nodes"].items():
            if not fl.include_cold and node.get("status") == "cold":
                continue
            reasons = [r for r in [
                _reason("semantic_tags", node.get("semantic_tags") or [], toks),
                _reason("brief", [node.get("brief") or ""], toks),
                _reason("label", [node.get("label") or ""], toks),
            ] if r]
            if reasons:
                hits.append({"hit_type": "semantic_node", "source": "overlay", "node_id": nid,
                             "label": node.get("label"), "tags": node.get("semantic_tags") or [],
                             "brief": node.get("brief"), "status": node.get("status"),
                             "match_reasons": reasons, "direct_match": True})

    if fl.enable_leaf_tags or fl.enable_path:
        for lid, leaf in data["leaves"].items():
            path = leaf.get("path") or ""
            reasons: list[dict[str, str]] = []
            if fl.enable_leaf_tags:
                r = _reason("semantic_tags", leaf.get("semantic_tags") or [], toks)
                if r: reasons.append(r)
            if fl.enable_path:
                hint = leaf.get("filename_hint") or os.path.splitext(os.path.basename(path))[0]
                for r in (
                    _reason("filename_hint", [hint], toks),
                    _reason("source_channel", [leaf.get("source_channel") or ""], toks),
                    _reason("location_tags", leaf.get("location_tags") or [], toks),
                    _reason("path", [path, os.path.basename(path), leaf.get("anchor") or ""], toks),
                ):
                    if r: reasons.append(r)
            if reasons:
                hits.append(_leaf_hit(lid, leaf, reasons))

    if fl.enable_fallback:
        for row in lso_search.search_rows(text, scope=scope, limit=limit):
            p = row.get("path") or ""
            hits.append({"hit_type": "fallback", "source": "fallback", "path": p,
                         "name": row.get("name"), "mtime": row.get("mtime"), "size": row.get("size"),
                         "match_reasons": [_reason("fallback", [p, row.get("name") or ""], toks)] if _match(p, toks) else [],
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
        if mt.supporting_files_changed(node, data["leaves"]):
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
    if not mt.supporting_files_changed(node, data["leaves"]):
        node["status"] = "active"
        ov.save(data)
        return {"ok": True, "action": "restored_active", "node_id": node_id}
    return mt.prepare_recheck_task(scope, node_id)
