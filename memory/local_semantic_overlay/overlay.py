"""Overlay builder — persist/validate/proposals (ablation boundary B3).

``propose_leaf_tags`` is the ONLY writer of ``semantic_tags`` (full-replacement
contract). ``_defense_filter`` is a wordlist-free source boundary guard. See
SOP/Reference docs.
"""

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

from ._config import IGNORE_DIRS_LOWER, anchor_of, norm_path, normalize_label
from .read import read_leaf, sanitize_display

PKG = Path(__file__).resolve().parent
OVERLAYS = PKG / "overlays"
FEEDBACK_MAX = 200
DEFAULT_ACTIVE_BUDGET = 40
_VALID_TAG_ROLES = frozenset({"content_semantic", "location", "source_channel"})
_VALID_EVIDENCE_SOURCES = frozenset({"text_head"})

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


def leaf_id_for_path(path: str) -> str:
    return f"leaf_{hashlib.sha1(norm_path(path).encode()).hexdigest()[:16]}"

def overlay_path(scope: str) -> Path:
    n = norm_path(scope)
    h = hashlib.sha1(n.encode()).hexdigest()[:8]
    tail = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", os.path.basename(n) or "root")[:40]
    OVERLAYS.mkdir(parents=True, exist_ok=True)
    return OVERLAYS / f"{tail}_{h}.json"

def empty_overlay(scope: str) -> dict[str, Any]:
    return {"meta": {"scope": norm_path(scope), "updated_at": now_iso(),
                     "active_budget": DEFAULT_ACTIVE_BUDGET},
            "leaves": {}, "nodes": {}, "feedback": []}

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

def add_leaf(data: dict[str, Any], path: str, read_result: dict[str, Any]) -> str:
    p, lid = norm_path(path), leaf_id_for_path(path)
    pp = Path(p)
    leaf = data["leaves"].get(lid, {})
    leaf.update({
        "path": p, "anchor": anchor_of(p),
        "filename": pp.name, "filename_hint": pp.stem,
        "mtime": read_result.get("mtime", leaf.get("mtime", 0)),
        "size": read_result.get("size", leaf.get("size", 0)),
        "read_status": read_result.get("read_status", leaf.get("read_status")),
        "evidence_type": read_result.get("evidence_type", leaf.get("evidence_type")),
        "semantic_tags": leaf.get("semantic_tags") or [],
        "location_tags": leaf.get("location_tags") or [],
        "source_channel": leaf.get("source_channel"),
    })
    if read_result.get("text_head"):
        leaf["text_head"] = read_result["text_head"]
    data["leaves"][lid] = leaf
    return lid

def ensure_leaf(scope: str, path: str) -> tuple[dict[str, Any], str]:
    data = load(scope)
    lid = add_leaf(data, path, read_leaf(path))
    save(data)
    return data, lid


def ensure_leaf_read(scope: str, path: str, read_result: dict[str, Any]) -> tuple[dict[str, Any], str]:
    data = load(scope)
    lid = add_leaf(data, path, read_result)
    save(data)
    return data, lid

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
    return {"label": label, "semantic_tags": tags, "source": source,
            "supporting_leaf_ids": leaf_ids, "brief": brief, "status": "active",
            "anchor": norm_path(anchor) if anchor else None,
            "derived_from_ids": list(derived or []),
            "last_hit_at": None, "hit_count": 0}

def _anchor_leaves(data: dict[str, Any], anchor: str) -> tuple[list[str], str]:
    ids, sample = [], anchor
    for f in shallow_preview(anchor)["files"]:
        ids.append(add_leaf(data, f["path"], read_leaf(f["path"])))
        sample = f["path"]
    return ids, sample

def prepare_leaf_tag_task(scope: str, leaf_id: str, *, flags: OverlayFlags | None = None) -> dict[str, Any]:
    """Build the leaf-tag task; filename is a hint, not semantic evidence."""
    fl = flags or OverlayFlags()
    if not fl.enable_leaf_tags:
        return _err("disabled")
    leaf = load(scope)["leaves"].get(leaf_id)
    if not leaf:
        return _err("missing_leaf")
    readable = leaf.get("read_status") == "readable"
    allowed = ["text_head"] if readable else []
    return _ok(task={"task": "leaf_tag", "leaf_id": leaf_id, "path": leaf["path"],
                     "read_status": leaf.get("read_status"),
                     "text_head": leaf.get("text_head") if readable else None,
                     "filename_hint": leaf.get("filename_hint") or Path(leaf.get("path") or "").stem,
                     "allowed_evidence_sources": allowed,
                     "output_schema": {"proposals": [{
                         "tag": "str", "evidence_phrase": "str",
                         "evidence_source": "text_head",
                         "tag_role": "content_semantic|location|source_channel"}]}})

def _evidence_text(leaf: dict[str, Any], source: str) -> str:
    if source == "text_head":
        return sanitize_display(leaf.get("text_head") or "") if leaf.get("read_status") == "readable" else ""
    return ""

def _norm_ev(text: str) -> str:
    return normalize_label(text).replace(" ", "")

def _validate_proposal(p: dict[str, Any], leaf: dict[str, Any]) -> tuple[str, str | None]:
    tag = (p.get("tag") or "").strip()
    if not tag:
        return "", "empty_tag"
    role = (p.get("tag_role") or "").strip()
    if role not in _VALID_TAG_ROLES:
        return "", "invalid_tag_role"
    if role != "content_semantic":
        return role, None
    ev, src = (p.get("evidence_phrase") or "").strip(), (p.get("evidence_source") or "").strip()
    if not ev:
        return "", "missing_evidence"
    if src not in _VALID_EVIDENCE_SOURCES:
        return "", "invalid_evidence_source"
    if len(_norm_ev(ev)) < 4 and not re.fullmatch(r"[A-Z0-9]{2,}", ev):
        return "", "weak_evidence"
    ref = _evidence_text(leaf, src)
    if not ref:
        return "", "no_evidence_source"
    if _norm_ev(ev) not in _norm_ev(ref):
        return "", "evidence_not_grounded"
    return role, None

def _record(p: dict[str, Any], role: str) -> dict[str, Any]:
    return {"tag": (p.get("tag") or "").strip(),
            "evidence_phrase": (p.get("evidence_phrase") or "").strip(),
            "evidence_source": (p.get("evidence_source") or "").strip(),
            "tag_role": role or (p.get("tag_role") or "").strip()}

def _defense_filter(tag: str, path: str) -> str | None:
    """Source boundary guard, zero wordlists. Returns rejection reason or None.
    Rejects extension and parent-dir token; filename is separated by evidence source.
    """
    low, nl = (tag or "").strip().lower(), normalize_label(tag)
    if not low or not nl:
        return "empty_tag"
    p = Path(path)
    if low == p.suffix.lower().lstrip("."):
        return "tag_is_extension"
    dir_toks: set[str] = set()
    for part in p.parent.parts:
        dir_toks |= {t for t in re.split(r"[\W_\-]+", part.lower()) if len(t) >= 2}
    if low in dir_toks or nl in dir_toks:
        return "tag_is_dir_token"
    return None

def _clean_node_tags(tags: list[str], path: str) -> list[str]:
    """List-level defense for node (compression/aggregation) tags. No wordlists."""
    out, seen = [], set()
    for t in tags or []:
        s = (t or "").strip()
        if _defense_filter(s, path) is not None:
            continue
        nl = normalize_label(s)
        if nl and nl not in seen:
            seen.add(nl); out.append(s)
    return out

def propose_leaf_tags(scope: str, leaf_id: str, proposals: list[dict[str, Any]],
                      *, flags: OverlayFlags | None = None) -> dict[str, Any]:
    """Primary tag-write API; full-replacement contract on success (audit == leaf).
    err paths (e.g. ``no_tags_accepted``) do not touch the leaf. See Reference."""
    fl = flags or OverlayFlags()
    if not fl.enable_leaf_tags:
        return _err("disabled")
    data = load(scope)
    leaf = data["leaves"].get(leaf_id)
    if not leaf:
        return _err("missing_leaf")
    path, accepted, rejected, seen = leaf.get("path") or "", [], [], set()
    source_channel_seen = False
    for p in proposals or []:
        role, reason = _validate_proposal(p, leaf)
        rec = _record(p, role)
        if reason:
            rejected.append({**rec, "reason": reason}); continue
        if rec["tag_role"] == "source_channel":
            if source_channel_seen:
                rejected.append({**rec, "reason": "multiple_source_channel"}); continue
            source_channel_seen = True
        nl = normalize_label(rec["tag"])
        if nl in seen:
            rejected.append({**rec, "reason": "duplicate_tag"}); continue
        if rec["tag_role"] == "content_semantic":
            df = _defense_filter(rec["tag"], path)
            if df:
                rejected.append({**rec, "reason": df}); continue
        seen.add(nl); accepted.append(rec)
    semantic = [r["tag"] for r in accepted if r["tag_role"] == "content_semantic"]
    location = [r["tag"] for r in accepted if r["tag_role"] == "location"]
    chans = [r["tag"] for r in accepted if r["tag_role"] == "source_channel"]
    channel = chans[-1] if chans else None
    semantic_applied = bool(semantic)
    metadata_applied = bool(location) or channel is not None
    if not semantic_applied and not metadata_applied:
        return _err("no_tags_accepted", accepted=[], rejected=rejected,
                    semantic_applied=False, metadata_applied=False)
    leaf["semantic_tags"] = semantic
    leaf["location_tags"] = location
    leaf["source_channel"] = channel
    save(data)
    return _ok(leaf_id=leaf_id, accepted=accepted, rejected=rejected,
               semantic_tags=semantic,
               metadata={"location_tags": location, "source_channel": channel},
               semantic_applied=semantic_applied,
               metadata_applied=metadata_applied)

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
    tags = _clean_node_tags(result.get("tags") or [], sample_path)
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
    tags = _clean_node_tags(result.get("tags") or [], path)
    if not leaf_ids or not label or not tags or not brief:
        return _err("incomplete")
    if berr := _validate_brief(brief, leaf_ids, data["leaves"]):
        return _err(berr)
    return apply_node(scope, _fresh_node(label, tags, "aggregated", leaf_ids, brief, derived=derived), flags=flags or OverlayFlags())

class BuildSession:
    """Auto-counting Build wrapper; finalize() returns build_audit + process stats."""

    def __init__(self, scope: str):
        self.scope = scope
        self._leaf_before = len(load(scope)["leaves"])
        self._reads: dict[str, dict[str, Any]] = {}
        self._s = dict.fromkeys((
            "candidate_path_count", "selected_count", "readable_count", "skipped_count",
            "proposal_count", "proposal_accepted", "proposal_rejected",
            "semantic_applied_count", "metadata_applied_count",
            "semantic_apply_ok", "metadata_apply_ok",
            "evidence_source_text_head",
            "apply_ok", "apply_fail"), 0)
        self._errors: list[dict[str, Any]] = []
        self._log: list[dict[str, Any]] = []

    def add_candidates(self, n: int) -> None:
        self._s["candidate_path_count"] += n

    def try_read(self, path: str) -> dict[str, Any]:
        self._s["selected_count"] += 1
        rr = read_leaf(path)
        self._reads[norm_path(path)] = rr
        self._s["readable_count" if rr.get("read_status") == "readable" else "skipped_count"] += 1
        return rr

    def ensure_leaf(self, path: str) -> str:
        np = norm_path(path)
        rr = self._reads.get(np)
        if rr is None:
            rr = self.try_read(np)
        _, lid = ensure_leaf_read(self.scope, np, rr)
        return lid

    def propose_tags(self, leaf_id: str, proposals: list[dict[str, Any]]) -> dict[str, Any]:
        self._s["proposal_count"] += len(proposals or [])
        result = propose_leaf_tags(self.scope, leaf_id, proposals)
        acc, rej = result.get("accepted") or [], result.get("rejected") or []
        self._s["proposal_accepted"] += len(acc)
        self._s["proposal_rejected"] += len(rej)
        sem, meta = result.get("semantic_tags") or [], result.get("metadata") or {}
        loc, chan = meta.get("location_tags") or [], meta.get("source_channel")
        self._s["semantic_applied_count"] += len(sem)
        self._s["metadata_applied_count"] += len(loc) + (1 if chan else 0)
        if result.get("semantic_applied"): self._s["semantic_apply_ok"] += 1
        if result.get("metadata_applied"): self._s["metadata_apply_ok"] += 1
        for a in acc:
            if a.get("tag_role") == "content_semantic":
                es = a.get("evidence_source")
                if es == "text_head": self._s["evidence_source_text_head"] += 1
        if result.get("ok"):
            self._s["apply_ok"] += 1
        else:
            self._s["apply_fail"] += 1
            self._errors.append({"leaf_id": leaf_id, "error": result.get("error")})
        self._log.append({"leaf_id": leaf_id, "accepted": acc, "rejected": rej})
        return result

    def finalize(self) -> dict[str, Any]:
        leaf_after = len(load(self.scope)["leaves"])
        return build_audit(self.scope, process_stats={
            **self._s, "unique_leaf_before": self._leaf_before,
            "unique_leaf_after": leaf_after,
            "unique_leaf_delta": leaf_after - self._leaf_before,
            "errors": self._errors, "proposal_log": self._log,
        })

def build_audit(scope: str, *, process_stats: dict[str, Any] | None = None) -> dict[str, Any]:
    """Overlay snapshot + optional process stats. Pure read."""
    data = load(scope)
    leaves, nodes = data["leaves"], data["nodes"]
    status_dist: dict[str, int] = {}
    tag_freq: dict[str, int] = {}
    anchor_freq: dict[str, int] = {}
    tagged = 0
    for leaf in leaves.values():
        rs = leaf.get("read_status") or "unknown"
        status_dist[rs] = status_dist.get(rs, 0) + 1
        a = leaf.get("anchor") or ""
        anchor_freq[a] = anchor_freq.get(a, 0) + 1
        tags = leaf.get("semantic_tags") or []
        if tags:
            tagged += 1
        for t in tags:
            tag_freq[t] = tag_freq.get(t, 0) + 1
    node_src: dict[str, int] = {}
    for n in nodes.values():
        s = n.get("source") or "unknown"
        node_src[s] = node_src.get(s, 0) + 1
    result = {"scope": data["meta"]["scope"], "unique_leaf_count": len(leaves),
              "tagged_leaf_count": tagged, "untagged_leaf_count": len(leaves) - tagged,
              "node_count": len(nodes), "node_source_dist": node_src,
              "read_status_dist": status_dist,
              "top_tags": sorted(tag_freq.items(), key=lambda x: -x[1])[:20],
              "top_anchors": sorted(anchor_freq.items(), key=lambda x: -x[1])[:10]}
    if process_stats is not None:
        result["process"] = process_stats
    return result
