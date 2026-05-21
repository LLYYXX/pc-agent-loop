"""Build: seeds → bundle → tags → entries → overview."""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

from . import store
from .read import read_leaf, sanitize_display
from .store import (
    MARKERS, add_leaf, load, new_id,
    pending_leaves, rebuild_pending_queue, save, update_build_stats,
)

GENERIC_TAGS = frozenset({
    "project", "document", "file", "folder", "data", "misc", "general", "code", "archive",
    "pdf", "doc", "ppt", "文件", "目录", "项目", "文档",
})
IGNORE = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build", "target"}
RECENT_DAYS = 14
LONG_DAYS = 30
APPLY_CAP = 12
KEY_EXT = {".pdf", ".docx", ".doc", ".pptx", ".xlsx", ".xls", ".md", ".json", ".toml"}


def _ok(data: dict[str, Any] | None = None, **kw: Any) -> dict[str, Any]:
    out = {"ok": True, "error": None, "message": "", "partial": True}
    if data is not None:
        out["data"] = data
    out.update(kw)
    return out


def _err(code: str, msg: str, **kw: Any) -> dict[str, Any]:
    return {"ok": False, "error": code, "message": msg, "partial": True, **kw}


def _is_key_evidence(path: Path) -> bool:
    n = path.name
    return n in MARKERS or n.lower().startswith("readme") or path.suffix.lower() in KEY_EXT


def begin_build(scope: str, *, seed_max: int = 200, bundle_max: int = 6,
                annotate_max: int = 30, reset: bool = False) -> dict[str, Any]:
    scope = store.norm_path(scope)
    data = store.empty_overlay(scope) if reset else load(scope)
    if reset or not data.get("meta", {}).get("built_at"):
        data["leaves"] = {}
        data["entries"] = []
    data["meta"].update({
        "scope": scope,
        "status": "partial",
        "built_at": data["meta"].get("built_at") or store.now_iso(),
        "budgets": {"seed_max": seed_max, "bundle_max": bundle_max, "annotate_max": annotate_max},
        "usage": {"seeds": 0, "bundles_done": 0, "annotated": 0, "deferred": 0},
        "build": {"phase": "seeding", "pending_queue": []},
    })
    save(data)
    return _ok(data=data)


def _walk_seeds(scope: str, data: dict[str, Any], seed_max: int) -> int:
    root = Path(scope)
    now = time.time()
    count = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in IGNORE and not d.startswith(".")]
        rel = os.path.relpath(dirpath, scope)
        depth = 0 if rel == "." else rel.count(os.sep) + 1
        if depth > 2:
            dirnames.clear()
            continue
        for fn in filenames:
            if count >= seed_max:
                return count
            p = Path(dirpath) / fn
            try:
                st = p.stat()
            except OSError:
                continue
            source = "recent"
            if _is_key_evidence(p):
                source = "key_evidence"
            elif (st.st_mtime - st.st_ctime) / 86400 >= LONG_DAYS:
                source = "long_maintained"
            elif now - st.st_mtime > RECENT_DAYS * 86400:
                continue
            info = read_leaf(str(p))
            if info.get("read_status") != "readable":
                continue
            add_leaf(data, str(p), source=source, status="seed", read_status="readable",
                     evidence_type=info.get("evidence_type"), text_head=info.get("text_head"),
                     mtime=info.get("mtime"), ctime=info.get("ctime"), size=info.get("size"))
            count += 1
    return count


def discover_seeds(data: dict[str, Any]) -> dict[str, Any]:
    scope = data["meta"]["scope"]
    seed_max = int(data["meta"]["budgets"].get("seed_max", 200))
    n = _walk_seeds(scope, data, seed_max)
    rebuild_pending_queue(data)
    data["meta"]["build"]["phase"] = "tagging"
    update_build_stats(data)
    save(data)
    return _ok(data=data, seeds_added=n)


def _existing_tags_at_anchor(data: dict[str, Any], anchor: str) -> list[str]:
    tags = []
    for _, leaf in store.leaves_by_anchor(data, anchor):
        for t in leaf.get("tags") or []:
            tags.append(t.get("tag", ""))
    return list(dict.fromkeys(tags))[:10]


def prepare_bundle(data: dict[str, Any]) -> dict[str, Any] | None:
    budgets = data["meta"]["budgets"]
    if int(data["meta"]["usage"].get("bundles_done", 0)) >= int(budgets.get("bundle_max", 6)):
        return None
    pending = pending_leaves(data)
    if not pending:
        return None
    pid, primary = pending[0]
    anchor = primary.get("anchor", "")
    candidates, key_ev = [], []
    pending_ids = {x[0] for x in pending}
    for lid, leaf in pending[1:]:
        if leaf.get("anchor") == anchor and lid != pid:
            if _is_key_evidence(Path(leaf.get("path", ""))):
                candidates.append(lid)
            else:
                candidates.append(lid)
    for lid, leaf in data["leaves"].items():
        if lid in pending_ids or leaf.get("anchor") != anchor:
            continue
        if leaf.get("tags") and _is_key_evidence(Path(leaf.get("path", ""))):
            key_ev.append({
                "leaf_id": lid, "path": leaf.get("path"),
                "display_title": leaf.get("display_title") or Path(leaf.get("path", "")).name,
                "text_head": (leaf.get("text_head") or "")[:1000],
            })
    cand_items = []
    for lid in candidates[:8]:
        leaf = data["leaves"][lid]
        cand_items.append({
            "leaf_id": lid, "path": leaf.get("path"),
            "display_title": Path(leaf.get("path", "")).name,
            "text_head": (leaf.get("text_head") or "")[:1000],
        })
    return {
        "bundle_id": new_id("bdl"),
        "anchor": anchor,
        "primary": {
            "leaf_id": pid, "path": primary.get("path"),
            "display_title": Path(primary.get("path", "")).name,
            "text_head": (primary.get("text_head") or "")[:1000],
        },
        "candidates": cand_items,
        "key_evidence": key_ev[:4],
        "tags_in_anchor": _existing_tags_at_anchor(data, anchor),
    }


def bundle_prompt(bundle: dict) -> str:
    import json
    lines = [
        "Tag leaves: 1-3 orthogonal, evidence-grounded tags per leaf.",
        "primary MUST appear in leaf_annotations or defer_leaf_ids.",
        "candidates optional. Reuse tags_in_anchor when similar.",
        "Output JSON only:",
        '{"leaf_annotations":[{"leaf_id":"...","tags":[{"tag":"...","evidence_note":"..."}]}],"defer_leaf_ids":[]}',
        "",
        json.dumps(bundle, ensure_ascii=False, indent=2),
    ]
    return "\n".join(lines)


def _bad_tag(leaf: dict, tag: str, note: str) -> str | None:
    t = tag.strip()
    if len(t) < 2 or t.lower() in GENERIC_TAGS:
        return "generic_tag"
    path = leaf.get("path", "")
    stem = Path(path).stem.lower()
    if t.lower() in (stem, Path(path).name.lower(), Path(path).suffix.lstrip(".").lower()):
        return "path_tag"
    if len((note or "").strip()) < 12:
        return "note_short"
    return None


def apply_tags(data: dict[str, Any], bundle: dict, response: dict) -> dict[str, Any]:
    primary_id = bundle["primary"]["leaf_id"]
    defer = set(response.get("defer_leaf_ids") or [])
    anns = response.get("leaf_annotations") or []
    ann_ids = {a.get("leaf_id") for a in anns}
    if primary_id not in defer and primary_id not in ann_ids:
        return _err("primary_not_resolved", "primary must be tagged or deferred")
    if len(anns) > APPLY_CAP:
        return _err("apply_cap", f"max {APPLY_CAP} annotations per apply")
    annotated = int(data["meta"]["usage"].get("annotated", 0))
    cap = int(data["meta"]["budgets"].get("annotate_max", 30))
    queue = data["meta"]["build"]["pending_queue"]
    resolved = set(defer)
    for raw in anns:
        lid = raw.get("leaf_id")
        if not lid or lid not in data["leaves"]:
            continue
        leaf = data["leaves"][lid]
        tags = []
        for t in (raw.get("tags") or [])[:3]:
            err = _bad_tag(leaf, t.get("tag", ""), t.get("evidence_note", ""))
            if err:
                return _err(err, f"invalid tag on {lid}")
            tags.append({"tag": t["tag"].strip(), "evidence_note": t["evidence_note"].strip()})
        if tags:
            leaf["tags"] = tags
            leaf["status"] = "tagged"
            annotated += 1
            resolved.add(lid)
        elif lid in defer:
            leaf["status"] = "deferred"
            resolved.add(lid)
    if primary_id in defer:
        data["leaves"][primary_id]["status"] = "deferred"
        resolved.add(primary_id)
    if annotated > cap:
        return _err("annotate_max", "session annotate_max exceeded")
    data["meta"]["build"]["pending_queue"] = [x for x in queue if x not in resolved]
    data["meta"]["usage"]["bundles_done"] = int(data["meta"]["usage"].get("bundles_done", 0)) + 1
    data["meta"]["usage"]["annotated"] = sum(1 for l in data["leaves"].values() if l.get("tags"))
    data["meta"]["usage"]["deferred"] = sum(1 for l in data["leaves"].values() if l.get("status") == "deferred")
    update_build_stats(data)
    save(data)
    return _ok(data=data)


def aggregate_entries(data: dict[str, Any]) -> list[dict[str, Any]]:
    by_anchor: dict[str, list[tuple[str, dict]]] = {}
    for lid, leaf in data["leaves"].items():
        if not leaf.get("tags"):
            continue
        by_anchor.setdefault(leaf.get("anchor", ""), []).append((lid, leaf))
    entries = []
    for anchor, group in by_anchor.items():
        tag_map: dict[str, list[str]] = {}
        for lid, leaf in group:
            for t in leaf.get("tags") or []:
                tag_map.setdefault(t["tag"], []).append(lid)
        for tag, lids in tag_map.items():
            if len(lids) >= 2:
                note = ""
                for lid in lids:
                    for t in data["leaves"][lid].get("tags") or []:
                        if t["tag"] == tag:
                            note = sanitize_display(t.get("evidence_note", ""))[:200]
                            break
                    if note:
                        break
                entries.append({
                    "entry_id": new_id("ent"),
                    "label": tag,
                    "anchor": anchor,
                    "tags": [tag],
                    "leaf_ids": lids,
                    "brief": note,
                })
        solo = [x for x in group if len(x[1].get("tags") or []) and x[1].get("source") == "key_evidence"]
        for lid, leaf in solo:
            if any(lid in e.get("leaf_ids", []) for e in entries if e["anchor"] == anchor):
                continue
            t0 = leaf["tags"][0]
            entries.append({
                "entry_id": new_id("ent"),
                "label": t0["tag"],
                "anchor": anchor,
                "tags": [t0["tag"]],
                "leaf_ids": [lid],
                "brief": sanitize_display(t0.get("evidence_note", ""))[:200],
            })
    data["entries"] = entries
    return entries


def build_overview(data: dict[str, Any], *, max_entries: int = 40) -> dict[str, Any]:
    aggregate_entries(data)
    data["meta"]["status"] = "partial"
    update_build_stats(data)
    save(data)
    return _ok(data=data, entry_count=len(data.get("entries", [])))


def finish_build(data: dict[str, Any]) -> dict[str, Any]:
    data["meta"]["build"]["phase"] = "done"
    update_build_stats(data)
    save(data)
    leaves = data["leaves"]
    return _ok(
        partial_report={
            "status": "partial",
            "seed_count": sum(1 for l in leaves.values() if l.get("status") == "seed"),
            "readable_count": sum(1 for l in leaves.values() if l.get("read_status") == "readable"),
            "tagged_count": sum(1 for l in leaves.values() if l.get("tags")),
            "deferred_count": sum(1 for l in leaves.values() if l.get("status") == "deferred"),
            "entry_count": len(data.get("entries", [])),
            "pending_remaining": len(data["meta"]["build"].get("pending_queue", [])),
            "skipped_noise": 0,
        },
    )
