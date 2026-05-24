"""Overlay builder — persist, prepare/apply/validate (ablation boundary B3)."""

from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ._config import GENERIC_TAG_STOPWORDS, IGNORE_DIRS_LOWER, anchor_of, norm_path
from .read import read_leaf, sanitize_display

PKG = Path(__file__).resolve().parent
OVERLAYS = PKG / "overlays"
FEEDBACK_MAX = 200
DEFAULT_ACTIVE_BUDGET = 40


@dataclass
class OverlayFlags:
    enable_leaf_tags: bool = True
    enable_compression: bool = True
    enable_aggregation: bool = True
    enable_active_cold: bool = True
    enable_feedback: bool = True


def _ok(**kw: Any) -> dict[str, Any]:
    return {"ok": True, "error": None, **kw}


def _err(code: str, **kw: Any) -> dict[str, Any]:
    return {"ok": False, "error": code, **kw}


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def normalize_label(text: str) -> str:
    s = re.sub(r"[^\w\u4e00-\u9fff ]+", "", re.sub(r"[\s_\-]+", " ", (text or "").strip().lower()))
    return s.strip()


def overlay_path(scope: str) -> Path:
    n = norm_path(scope)
    h = hashlib.sha1(n.encode()).hexdigest()[:8]
    tail = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", os.path.basename(n) or "root")[:40]
    OVERLAYS.mkdir(parents=True, exist_ok=True)
    return OVERLAYS / f"{tail}_{h}.json"


def empty_overlay(scope: str) -> dict[str, Any]:
    return {
        "meta": {"scope": norm_path(scope), "updated_at": now_iso(), "active_budget": DEFAULT_ACTIVE_BUDGET},
        "leaves": {}, "nodes": {}, "feedback": [],
    }


def load(scope: str) -> dict[str, Any]:
    p = overlay_path(scope)
    if not p.is_file():
        return empty_overlay(scope)
    with p.open(encoding="utf-8") as f:
        data = json.load(f)
    for k, d in (("meta", {}), ("leaves", {}), ("nodes", {}), ("feedback", [])):
        data.setdefault(k, d)
    data["meta"]["scope"] = norm_path(scope)
    data["meta"].setdefault("active_budget", DEFAULT_ACTIVE_BUDGET)
    return data


def save(data: dict[str, Any]) -> None:
    data["meta"]["updated_at"] = now_iso()
    p = overlay_path(data["meta"]["scope"])
    tmp = p.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    tmp.replace(p)


def leaf_id_for_path(path: str) -> str:
    return f"leaf_{hashlib.sha1(norm_path(path).encode()).hexdigest()[:16]}"


def add_leaf(data: dict[str, Any], path: str, read_result: dict[str, Any]) -> str:
    p, lid = norm_path(path), leaf_id_for_path(path)
    leaf = data["leaves"].get(lid, {})
    leaf.update({
        "path": p, "anchor": anchor_of(p),
        "mtime": read_result.get("mtime", leaf.get("mtime", 0)),
        "size": read_result.get("size", leaf.get("size", 0)),
        "read_status": read_result.get("read_status", leaf.get("read_status")),
        "evidence_type": read_result.get("evidence_type", leaf.get("evidence_type")),
        "semantic_tags": leaf.get("semantic_tags") or [],
    })
    if read_result.get("text_head"):
        leaf["text_head"] = read_result["text_head"]
    data["leaves"][lid] = leaf
    return lid


def validate_node(node: dict[str, Any], leaves: dict[str, Any]) -> str | None:
    if not (node.get("label") or "").strip():
        return "empty_label"
    if node.get("source") not in ("compressed", "aggregated"):
        return "bad_source"
    sids = node.get("supporting_leaf_ids") or []
    if not sids or any(s not in leaves for s in sids):
        return "missing_supporting_leaves"
    if node.get("status") not in ("active", "cold"):
        return "bad_status"
    if not (node.get("brief") or "").strip():
        return "empty_brief"
    if node.get("source") == "aggregated" and not (node.get("derived_from_ids") or []):
        return "aggregated_missing_lineage"
    return None


def expand_supporting_leaves(data: dict[str, Any], ids: list[str]) -> list[str]:
    out, seen = [], set()
    for i in ids:
        if i in data["leaves"] and i not in seen:
            out.append(i); seen.add(i)
        elif i in data["nodes"]:
            for sid in data["nodes"][i].get("supporting_leaf_ids") or []:
                if sid not in seen:
                    out.append(sid); seen.add(sid)
    return out


def mechanical_dedup_node(data: dict[str, Any], node: dict[str, Any]) -> str | None:
    nl = normalize_label(node.get("label", ""))
    src, sids = node.get("source"), tuple(sorted(node.get("supporting_leaf_ids") or []))
    for nid, ex in data["nodes"].items():
        if ex.get("source") == src and normalize_label(ex.get("label", "")) == nl:
            if tuple(sorted(ex.get("supporting_leaf_ids") or [])) == sids:
                return nid
    return None


def cleanup_lineage(data: dict[str, Any]) -> list[str]:
    removed, changed = [], True
    while changed:
        changed = False
        for nid in list(data["nodes"]):
            node = data["nodes"][nid]
            if node.get("source") != "aggregated":
                continue
            valid = [s for s in (node.get("supporting_leaf_ids") or []) if s in data["leaves"]]
            if not valid:
                del data["nodes"][nid]; removed.append(nid); changed = True
                continue
            if valid != node.get("supporting_leaf_ids"):
                node["supporting_leaf_ids"] = valid
            derived = [d for d in (node.get("derived_from_ids") or []) if d in data["nodes"] or d in data["leaves"]]
            if derived != (node.get("derived_from_ids") or []):
                node["derived_from_ids"] = derived
    return removed


def delete_node(data: dict[str, Any], node_id: str) -> bool:
    if node_id not in data["nodes"]:
        return False
    del data["nodes"][node_id]
    cleanup_lineage(data)
    return True


def _path_tokens(path: str) -> set[str]:
    base = os.path.basename(norm_path(path)).lower()
    stem = Path(base).stem.lower()
    return {p for p in set(re.split(r"[\W_\-]+", base)) | set(re.split(r"[\W_\-]+", stem)) if len(p) >= 2}


def _filter_tags(tags: list[str], path: str) -> list[str]:
    ptoks = _path_tokens(path)
    out, seen = [], set()
    ext = Path(path).suffix.lower().lstrip(".")
    for t in tags or []:
        s, low = (t or "").strip(), (t or "").strip().lower()
        if not s or low in GENERIC_TAG_STOPWORDS or low in ptoks or normalize_label(s) in ptoks or low == ext:
            continue
        nl = normalize_label(s)
        if nl and nl not in seen:
            seen.add(nl); out.append(s)
    return out


def _validate_brief(brief: str, leaf_ids: list[str], leaves: dict[str, Any]) -> str | None:
    b = sanitize_display(brief or "")
    if not b:
        return "empty_brief"
    blob = b.lower()
    for lid in leaf_ids:
        head = sanitize_display((leaves.get(lid) or {}).get("text_head") or "")
        if head:
            sn = head[:120].lower()
            if sn in blob or blob in sn:
                return None
            if any(tok in blob for tok in re.findall(r"[a-z0-9\u4e00-\u9fff]{4,}", sn)):
                return None
        path = ((leaves.get(lid) or {}).get("path") or "").lower()
        if path and os.path.basename(path) in blob:
            return None
    return "brief_not_grounded"


def _fresh_node(label: str, tags: list[str], source: str, leaf_ids: list[str], brief: str,
                *, anchor: str | None = None, derived: list[str] | None = None) -> dict[str, Any]:
    return {
        "label": label, "semantic_tags": tags, "source": source,
        "supporting_leaf_ids": leaf_ids, "brief": brief, "status": "active",
        "anchor": norm_path(anchor) if anchor else None,
        "derived_from_ids": list(derived or []),
        "last_hit_at": None, "hit_count": 0,
    }


def _anchor_leaves(data: dict[str, Any], anchor: str) -> tuple[list[str], str]:
    ids, sample = [], anchor
    for f in shallow_preview(anchor)["files"]:
        ids.append(add_leaf(data, f["path"], read_leaf(f["path"])))
        sample = f["path"]
    return ids, sample


def ensure_leaf(scope: str, path: str) -> tuple[dict[str, Any], str]:
    data = load(scope)
    lid = add_leaf(data, path, read_leaf(path))
    save(data)
    return data, lid


def prepare_leaf_tag_task(scope: str, leaf_id: str, *, flags: OverlayFlags | None = None) -> dict[str, Any]:
    fl = flags or OverlayFlags()
    if not fl.enable_leaf_tags:
        return _err("disabled")
    leaf = load(scope)["leaves"].get(leaf_id)
    if not leaf:
        return _err("missing_leaf")
    if leaf.get("read_status") != "readable":
        return _err("not_readable")
    return _ok(task={"task": "leaf_tag", "leaf_id": leaf_id, "path": leaf["path"],
                     "text_head": leaf.get("text_head"), "output_schema": {"tags": ["string"]}})


def apply_leaf_tags(scope: str, leaf_id: str, tags: list[str], *, flags: OverlayFlags | None = None) -> dict[str, Any]:
    fl = flags or OverlayFlags()
    if not fl.enable_leaf_tags:
        return _err("disabled")
    data = load(scope)
    leaf = data["leaves"].get(leaf_id)
    if not leaf:
        return _err("missing_leaf")
    leaf["semantic_tags"] = _filter_tags(tags, leaf["path"])
    save(data)
    return _ok(leaf_id=leaf_id, semantic_tags=leaf["semantic_tags"])


def shallow_preview(anchor: str, *, file_cap: int = 40) -> dict[str, Any]:
    root = Path(norm_path(anchor))
    files, subdirs = [], {}
    if not root.is_dir():
        return {"anchor": str(root), "files": files, "subdir_summary": []}
    try:
        for entry in os.scandir(root):
            if entry.is_dir(follow_symlinks=False):
                subdirs[entry.name] = subdirs.get(entry.name, 0) + 1
                continue
            if not entry.is_file(follow_symlinks=False) or any(
                p.lower() in IGNORE_DIRS_LOWER for p in Path(entry.path).parts):
                continue
            try:
                st = entry.stat(follow_symlinks=False)
            except OSError:
                continue
            files.append({"path": norm_path(entry.path), "name": entry.name,
                          "mtime": st.st_mtime, "size": st.st_size})
            if len(files) >= file_cap:
                break
    except OSError:
        pass
    return {"anchor": str(root), "files": files,
            "subdir_summary": [{"name": k, "count": v} for k, v in sorted(subdirs.items())[:20]]}


def apply_node(scope: str, node: dict[str, Any], *, flags: OverlayFlags | None = None) -> dict[str, Any]:
    fl = flags or OverlayFlags()
    src = node.get("source")
    if (src == "compressed" and not fl.enable_compression) or (src == "aggregated" and not fl.enable_aggregation):
        return _err("disabled")
    data = load(scope)
    if (verr := validate_node(node, data["leaves"])):
        return _err(verr)
    if (berr := _validate_brief(node.get("brief") or "", node.get("supporting_leaf_ids") or [], data["leaves"])):
        return _err(berr)
    if dup := mechanical_dedup_node(data, node):
        return _ok(node_id=dup, deduped=True)
    nid = new_id("node")
    data["nodes"][nid] = node
    save(data)
    return _ok(node_id=nid)


def prepare_compression_task(scope: str, anchor: str, *, flags: OverlayFlags | None = None, sample_limit: int = 6) -> dict[str, Any]:
    fl = flags or OverlayFlags()
    if not fl.enable_compression:
        return _err("disabled")
    data, preview, samples = load(scope), shallow_preview(anchor), []
    for f in preview["files"][:sample_limit]:
        rr = read_leaf(f["path"])
        lid = add_leaf(data, f["path"], rr)
        if rr.get("read_status") == "readable" and rr.get("text_head"):
            samples.append({"leaf_id": lid, "path": f["path"], "text_head": rr["text_head"]})
    save(data)
    return _ok(task={"task": "compress", "anchor": preview["anchor"], "sample_evidence": samples,
                     "output_schema": {"decision": "compress|expand|defer", "label": "str", "tags": ["str"], "brief": "str"}})


def apply_compression(scope: str, anchor: str, result: dict[str, Any], *, flags: OverlayFlags | None = None) -> dict[str, Any]:
    if (result.get("decision") or "").strip().lower() != "compress":
        return _ok(decision=(result.get("decision") or "defer").strip().lower() or "defer", node_id=None)
    label, brief = (result.get("label") or "").strip(), sanitize_display(result.get("brief") or "")
    if not label or not brief:
        return _err("incomplete")
    data = load(scope)
    leaf_ids, sample_path = _anchor_leaves(data, anchor)
    if not leaf_ids:
        return _err("no_leaves")
    tags = _filter_tags(result.get("tags") or [], sample_path)
    if not tags:
        return _err("incomplete")
    if berr := _validate_brief(brief, leaf_ids, data["leaves"]):
        return _err(berr)
    save(data)
    return apply_node(scope, _fresh_node(label, tags, "compressed", leaf_ids, brief, anchor=anchor), flags=flags or OverlayFlags())


def gather_aggregation_candidates(scope: str) -> list[dict[str, Any]]:
    data = load(scope)
    out = []
    for lid, leaf in data["leaves"].items():
        if leaf.get("semantic_tags"):
            out.append({"kind": "leaf_tag", "id": lid, "tags": leaf["semantic_tags"],
                        "supporting_leaf_ids": [lid], "brief": sanitize_display(leaf.get("text_head") or "")[:200]})
    for nid, node in data["nodes"].items():
        if node.get("source") == "compressed":
            out.append({"kind": "compressed_node", "id": nid, "tags": node.get("semantic_tags") or [],
                        "supporting_leaf_ids": node.get("supporting_leaf_ids") or [], "brief": node.get("brief") or ""})
    return out


def prepare_aggregation_task(scope: str, *, flags: OverlayFlags | None = None, candidate_ids: list[str] | None = None) -> dict[str, Any]:
    fl = flags or OverlayFlags()
    if not fl.enable_aggregation:
        return _err("disabled")
    cands = gather_aggregation_candidates(scope)
    if candidate_ids:
        wanted = set(candidate_ids)
        cands = [c for c in cands if c["id"] in wanted]
    if not cands:
        return _err("no_candidates")
    return _ok(task={"task": "aggregate", "candidates": cands,
                     "output_schema": {"decision": "aggregate|skip", "label": "str", "tags": ["str"],
                                       "derived_from_ids": ["id"], "brief": "str"}})


def apply_aggregation(scope: str, result: dict[str, Any], *, flags: OverlayFlags | None = None) -> dict[str, Any]:
    if (result.get("decision") or "").strip().lower() != "aggregate":
        return _ok(decision="skip", node_id=None)
    data = load(scope)
    derived = list(result.get("derived_from_ids") or [])
    if not derived:
        return _err("missing_lineage")
    if any(did in data["nodes"] and data["nodes"][did].get("source") == "aggregated" for did in derived):
        return _err("recursive_aggregation")
    leaf_ids = expand_supporting_leaves(data, derived)
    label, brief = (result.get("label") or "").strip(), sanitize_display(result.get("brief") or "")
    path = (data["leaves"].get(leaf_ids[0]) or {}).get("path") or "" if leaf_ids else ""
    tags = _filter_tags(result.get("tags") or [], path)
    if not leaf_ids or not label or not tags or not brief:
        return _err("incomplete")
    if berr := _validate_brief(brief, leaf_ids, data["leaves"]):
        return _err(berr)
    return apply_node(scope, _fresh_node(label, tags, "aggregated", leaf_ids, brief, derived=derived), flags=flags or OverlayFlags())


def record_feedback(scope: str, *, result_id: str, kind: str,
                    node_id: str | None = None, leaf_id: str | None = None,
                    flags: OverlayFlags | None = None) -> dict[str, Any]:
    fl = flags or OverlayFlags()
    if not fl.enable_feedback or kind not in ("selected", "not_selected", "negative"):
        return _err("disabled" if not fl.enable_feedback else "bad_kind")
    if bool(node_id) == bool(leaf_id):
        return _err("bad_target")
    data = load(scope)
    data["feedback"] = (data["feedback"] + [{"result_id": result_id, "node_id": node_id, "leaf_id": leaf_id,
                                             "kind": kind, "at": now_iso()}])[-FEEDBACK_MAX:]
    if node_id and node_id in data["nodes"]:
        n = data["nodes"][node_id]
        key = {"selected": "selected_count", "not_selected": "not_selected_count",
               "negative": "negative_feedback_count"}[kind]
        n[key] = int(n.get(key) or 0) + 1
        if kind == "negative" and fl.enable_active_cold:
            n["status"] = "cold"
    save(data)
    return _ok()


def enforce_active_budget(scope: str, *, flags: OverlayFlags | None = None) -> dict[str, Any]:
    fl = flags or OverlayFlags()
    if not fl.enable_active_cold:
        return _ok(demoted=[], skipped=True)
    data = load(scope)
    budget = int(data["meta"].get("active_budget") or DEFAULT_ACTIVE_BUDGET)
    active = [(nid, n) for nid, n in data["nodes"].items() if n.get("status") == "active"]
    if len(active) <= budget:
        return _ok(demoted=[])
    active.sort(key=lambda x: (x[1].get("last_hit_at") or "", -int(x[1].get("hit_count") or 0)))
    demoted = []
    while len(active) > budget:
        nid, node = active.pop(0)
        node["status"] = "cold"
        demoted.append(nid)
    save(data)
    return _ok(demoted=demoted)


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
    data = load(scope)
    node = data["nodes"].get(node_id)
    if not node:
        return _err("missing_node")
    samples = []
    for lid in node.get("supporting_leaf_ids") or []:
        leaf = data["leaves"].get(lid)
        if not leaf:
            continue
        rr = read_leaf(leaf["path"])
        samples.append({"leaf_id": lid, "path": leaf["path"], "text_head": rr.get("text_head")})
    return _ok(task={"task": "recheck", "node_id": node_id, "label": node.get("label"),
                     "supporting_evidence": samples, "output_schema": {"decision": "keep|delete|update"}})


def apply_recheck(scope: str, node_id: str, result: dict[str, Any]) -> dict[str, Any]:
    decision = (result.get("decision") or "keep").strip().lower()
    data = load(scope)
    node = data["nodes"].get(node_id)
    if not node:
        return _err("missing_node")
    if decision == "delete":
        delete_node(data, node_id); save(data)
        return _ok(action="deleted", node_id=node_id)
    if decision == "update":
        lids = node.get("supporting_leaf_ids") or []
        if result.get("label"):
            node["label"] = result["label"].strip()
        if result.get("tags"):
            node["semantic_tags"] = _filter_tags(result["tags"], (data["leaves"].get(lids[0]) or {}).get("path") or "")
        if result.get("brief"):
            brief = sanitize_display(result["brief"])
            if berr := _validate_brief(brief, lids, data["leaves"]):
                return _err(berr)
            node["brief"] = brief
    node["status"] = "active"
    save(data)
    return _ok(action=decision, node_id=node_id)
