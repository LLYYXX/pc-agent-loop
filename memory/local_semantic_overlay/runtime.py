"""Runtime query, fallback, feedback."""

from __future__ import annotations

import re
from typing import Any

from . import search, store
from .read import read_leaf, sanitize_display
from .store import add_feedback, add_leaf, load, path_under, rebuild_pending_queue, save

GENERIC = frozenset({
    "file", "files", "folder", "document", "project", "data", "misc", "general",
    "文件", "目录", "项目", "文档",
})


def _ok(**kw: Any) -> dict[str, Any]:
    return {"ok": True, "error": None, "message": "", "partial": True, **kw}


def _err(code: str, msg: str, **kw: Any) -> dict[str, Any]:
    return {"ok": False, "error": code, "message": msg, "partial": True, **kw}


def _tokens(q: str) -> set[str]:
    low = q.lower()
    toks = set(re.findall(r"[a-z0-9][a-z0-9_-]{1,}", low))
    toks.update(re.findall(r"[\u4e00-\u9fff]{2,}", low))
    return {t for t in toks if t not in GENERIC}


def _score_tokens(text: str, toks: set[str]) -> float:
    if not toks:
        return 0.0
    low = text.lower()
    return sum(1 for t in toks if t in low) / len(toks)


def system_overview(scope: str, *, max_chars: int = 1500) -> dict[str, Any]:
    data = load(scope)
    lines = []
    for ent in data.get("entries", [])[:40]:
        brief = sanitize_display(ent.get("brief") or "")[:200]
        lines.append(f"- {ent.get('label', '')} @ {ent.get('anchor', '')}: {brief}")
    text = "\n".join(lines)[:max_chars]
    return _ok(scope=store.norm_path(scope), entries=data.get("entries", []), text=text)


def query_map(query: str, *, scope: str, limit: int = 10) -> dict[str, Any]:
    data = load(scope)
    toks = _tokens(query)
    semantic_hits, leaf_hits = [], []
    for ent in data.get("entries", []):
        blob = " ".join([ent.get("label", ""), ent.get("brief", ""), " ".join(ent.get("tags") or [])])
        sc = _score_tokens(blob, toks)
        if sc > 0:
            semantic_hits.append({"hit_type": "entry", "entry_id": ent.get("entry_id"), "label": ent.get("label"),
                                  "anchor": ent.get("anchor"), "score": sc, "source": "map"})
    for lid, leaf in data.get("leaves", {}).items():
        if not path_under(leaf.get("path", ""), scope):
            continue
        tags = [t.get("tag", "") for t in leaf.get("tags") or []]
        blob = " ".join(tags) + " " + sanitize_display(leaf.get("text_head") or "")[:300]
        sc = _score_tokens(blob, toks)
        if sc > 0:
            leaf_hits.append({"hit_type": "leaf", "leaf_id": lid, "path": leaf.get("path"), "score": sc, "source": "map"})
    semantic_hits.sort(key=lambda x: -x["score"])
    leaf_hits.sort(key=lambda x: -x["score"])
    return _ok(semantic_hits=semantic_hits[:limit], leaf_hits=leaf_hits[:limit], fallback_hits=[])


def run_file_query(query: str, *, scope: str, limit: int = 10) -> dict[str, Any]:
    base = query_map(query, scope=scope, limit=limit)
    if not base["ok"]:
        return base
    sem, leaf = base.get("semantic_hits") or [], base.get("leaf_hits") or []
    need = min(3, limit)
    fallback_hits, fallback_used = [], False
    if len(sem) + len(leaf) < need:
        fallback_used = True
        for row in search.search_rows(query, scope, limit):
            fallback_hits.append({"hit_type": "fallback", "path": row["path"], "source": "fallback", "score": 0.0})
    return _ok(
        semantic_hits=sem,
        leaf_hits=leaf,
        fallback_hits=fallback_hits[:limit],
        fallback_used=fallback_used,
    )


def finish_file_query(
    query: str,
    *,
    scope: str,
    found: list[str] | None = None,
    selected: list[str] | None = None,
    rejected: list[str] | None = None,
) -> dict[str, Any]:
    data = load(scope)
    add_feedback(data, {
        "query": query,
        "found": found or [],
        "selected": selected or [],
        "rejected": rejected or [],
        "at": store.now_iso(),
    })
    added = 0
    for p in found or []:
        if not path_under(p, scope):
            continue
        info = read_leaf(p)
        if info.get("read_status") != "readable":
            continue
        add_leaf(data, p, source="fallback_found", status="seed", read_status="readable",
                 evidence_type=info.get("evidence_type"), text_head=info.get("text_head"),
                 mtime=info.get("mtime"), ctime=info.get("ctime"), size=info.get("size"))
        added += 1
    rebuild_pending_queue(data)
    save(data)
    return _ok(added_seeds=added)
