from __future__ import annotations
import hashlib, json, os, re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
ROLES = ("selector", "compressor", "tagger", "aggregator", "auditor")
PKG = Path(__file__).resolve().parent
OVERLAY_DIR = PKG / "overlays"
def _ok(**kw: Any) -> dict[str, Any]: return {"ok": True, "error": None, **kw}
def _err(code: str, **kw: Any) -> dict[str, Any]: return {"ok": False, "error": code, **kw}
def _now() -> str: return datetime.now(timezone.utc).replace(microsecond=0).isoformat()
def _norm(path: str | os.PathLike[str]) -> str: return os.path.normpath(os.path.abspath(str(path)))
def _clean(x: Any) -> str: return re.sub(r"\s+", " ", str(x or "")).strip()
def _seq(x: Any) -> list[Any]: return [] if x is None else x if isinstance(x, list) else [x]
def _id(prefix: str, *parts: Any) -> str:
    text = json.dumps(parts, ensure_ascii=False, sort_keys=True)
    return f"{prefix}_{hashlib.sha1(text.encode()).hexdigest()[:14]}"
def overlay_path(scope: str) -> Path:
    s = _norm(scope)
    tail = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", os.path.basename(s) or "root")[:40]
    OVERLAY_DIR.mkdir(parents=True, exist_ok=True)
    return OVERLAY_DIR / f"{tail}_{hashlib.sha1(s.encode()).hexdigest()[:8]}.json"
def load(scope: str) -> dict[str, Any]:
    path = overlay_path(scope)
    data = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    data.setdefault("scope", _norm(scope)); data.setdefault("leaves", {})
    data.setdefault("nodes", {}); data.setdefault("events", [])
    return data
def save(scope: str, data: dict[str, Any]) -> None:
    data["scope"] = _norm(scope)
    tmp = overlay_path(scope).with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(overlay_path(scope))
def _event(data: dict[str, Any], role: str, action: str, ref: str, aid: str | None) -> None:
    data["events"].append({"at": _now(), "role": role, "action": action, "ref": ref, "artifact_id": aid})
    data["events"] = data["events"][-200:]
def _items(a: dict[str, Any]) -> list[dict[str, Any]]:
    raw = []
    for k in ("retained", "claims", "targets", "facet_nodes", "semantic_nodes"):
        raw += _seq(a.get(k))
    return [x for x in raw if isinstance(x, dict)]
def _leaf_id(path: str) -> str: return _id("leaf", _norm(path))
def _ref(data: dict[str, Any], item: dict[str, Any]) -> tuple[str, str | None, dict[str, Any] | None]:
    rid = item.get("leaf_id") or item.get("node_id") or item.get("target_id")
    if rid in data["leaves"]: return "leaf", rid, data["leaves"][rid]
    if rid in data["nodes"]: return "node", rid, data["nodes"][rid]
    if item.get("path"):
        lid = _leaf_id(item["path"])
        if lid in data["leaves"]: return "leaf", lid, data["leaves"][lid]
    return "", None, None
def _select(data: dict[str, Any], artifact: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    out, bad = [], []
    for item in _seq(artifact.get("discarded") or artifact.get("rejected")):
        if isinstance(item, dict):
            bad.append({"path": item.get("path"), "reason": _clean(item.get("why") or item.get("reason")),
                        "signals": _seq(item.get("signals"))})
    for item in _items(artifact):
        if not item.get("path"): continue
        path, lid = _norm(item["path"]), _leaf_id(item["path"])
        leaf = data["leaves"].setdefault(lid, {"leaf_id": lid, "path": path, "tags": [], "claims": []})
        try:
            st = os.stat(path); leaf["file_state"] = {"exists": True, "mtime": st.st_mtime, "size": st.st_size}
        except OSError:
            leaf["file_state"] = {"exists": False}
        leaf["why_retained"] = _clean(item.get("why") or item.get("reason"))
        leaf["selector_signals"] = _seq(item.get("signals") or item.get("reason"))
        leaf["evidence_kind"] = _clean(item.get("evidence_kind"))
        _event(data, "selector", "leaf", lid, artifact.get("artifact_id"))
        out.append({"leaf_id": lid, "path": path})
    return out, bad
def _tag(data: dict[str, Any], artifact: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    ok, bad = [], []
    for item in _items(artifact):
        kind, rid, obj = _ref(data, item)
        tag = _clean(item.get("tag")); evidence = _clean(item.get("evidence") or item.get("evidence_phrase"))
        source = _clean(item.get("source") or item.get("evidence_source"))
        if not (rid and obj and tag and evidence and source):
            bad.append({"item": item, "reason": "missing_target_tag_evidence_or_source"}); continue
        claim = {"tag": tag, "evidence": evidence, "source": source, "artifact_id": artifact.get("artifact_id")}
        obj.setdefault("tags", []); obj.setdefault("claims", [])
        if tag not in obj["tags"]: obj["tags"].append(tag)
        if claim not in obj["claims"]: obj["claims"].append(claim)
        _event(data, artifact.get("role") or "tagger", "tag", rid, artifact.get("artifact_id"))
        ok.append({f"{kind}_id": rid, "tag": tag})
    return ok, bad
def _support(data: dict[str, Any], item: dict[str, Any]) -> list[str]:
    ids: list[str] = []
    for k in ("supporting_leaf_ids", "leaf_ids"):
        ids += _seq(item.get(k))
    for did in _seq(item.get("derived_from_ids") or item.get("target_ids")):
        if did in data["leaves"]: ids.append(did)
        if did in data["nodes"]: ids.extend(data["nodes"][did].get("supporting_leaf_ids") or [])
    return [x for x in dict.fromkeys(ids) if x in data["leaves"]]
def _node(data: dict[str, Any], artifact: dict[str, Any], source: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    ok, bad = [], []
    for item in _items(artifact):
        derived = _seq(item.get("derived_from_ids"))
        if source == "aggregated" and (not derived or any(x not in data["leaves"] and x not in data["nodes"] for x in derived)):
            bad.append({"item": item, "reason": "missing_or_unknown_derivation"}); continue
        label = _clean(item.get("label") or item.get("boundary") or item.get("path"))
        tags = [_clean(t) for t in _seq(item.get("tags")) if _clean(t)]
        leaves = _support(data, {"derived_from_ids": derived} if source == "aggregated" else item)
        if not (label and leaves):
            bad.append({"item": item, "reason": "missing_label_or_support"}); continue
        layer = _clean(item.get("layer") or ("target" if source == "compressed" else source))
        ntype = _clean(item.get("node_type") or item.get("target_type") or source)
        nid = item.get("node_id") or item.get("target_id") or _id("node", source, label, sorted(leaves), layer)
        data["nodes"][nid] = {"node_id": nid, "source": source, "label": label, "tags": tags,
                              "layer": layer, "node_type": ntype,
                              "brief": _clean(item.get("brief") or item.get("why")),
                              "boundary": _clean(item.get("boundary")), "supporting_leaf_ids": leaves,
                              "derived_from_ids": derived if source == "aggregated" else [],
                              "artifact_id": artifact.get("artifact_id")}
        _event(data, artifact.get("role") or source, "node", nid, artifact.get("artifact_id"))
        ok.append({"node_id": nid, "supporting_leaf_ids": leaves})
    return ok, bad
def apply_artifact(scope: str, artifact: dict[str, Any]) -> dict[str, Any]:
    role = artifact.get("role")
    if role not in ROLES: return _err("unknown_role", role=role)
    if role == "auditor": return _ok(role=role, verdict=artifact.get("verdict"), applied=False)
    data = load(scope); accepted: list[dict[str, Any]] = []; rejected: list[dict[str, Any]] = []
    if role == "selector": accepted, rejected = _select(data, artifact)
    elif role == "compressor": accepted, rejected = _node(data, artifact, "compressed")
    elif role == "tagger": accepted, rejected = _tag(data, artifact)
    else: accepted, rejected = _node(data, artifact, "aggregated")
    applied = bool(accepted) if role == "selector" else bool(accepted) and not rejected
    if applied: save(scope, data)
    return _ok(role=role, accepted=accepted, rejected=rejected, applied=applied)
def _tokens(text: str) -> list[str]:
    return list(dict.fromkeys(re.findall(r"[a-z0-9][a-z0-9_\-]{1,}|[\u4e00-\u9fff]{2,}", (text or "").lower())))
def _match(values: list[Any], toks: list[str]) -> bool:
    blob = " ".join(str(v or "").lower() for v in values)
    return bool(toks) and any(t in blob for t in toks)
def query(scope: str, text: str, *, limit: int = 20) -> dict[str, Any]:
    data, toks, hits = load(scope), _tokens(text), []
    for nid, n in data["nodes"].items():
        if _match([n.get("label"), n.get("brief"), *n.get("tags", [])], toks):
            leaves = [data["leaves"][i] for i in n.get("supporting_leaf_ids", []) if i in data["leaves"]]
            hits.append({"hit_type": "node", "node_id": nid, "label": n["label"], "tags": n["tags"],
                         "layer": n.get("layer"), "node_type": n.get("node_type"),
                         "derived_from_ids": n.get("derived_from_ids", []),
                         "leaf_ids": [l["leaf_id"] for l in leaves], "files": [l["path"] for l in leaves]})
    for lid, leaf in data["leaves"].items():
        if _match([leaf.get("path"), leaf.get("why_retained"), *leaf.get("tags", [])], toks):
            hits.append({"hit_type": "leaf", "leaf_id": lid, "tags": leaf["tags"], "file": leaf["path"]})
    return _ok(query_tokens=toks, hits=hits[:limit])
def audit_packet(scope: str, *, artifacts: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    data = load(scope); leaves, nodes = data["leaves"], data["nodes"]
    unsupported = [nid for nid, n in nodes.items() if not n.get("supporting_leaf_ids") or any(x not in leaves for x in n["supporting_leaf_ids"])]
    return _ok(packet={"scope": data["scope"], "leaf_count": len(leaves),
                       "tagged_leaf_count": sum(1 for l in leaves.values() if l.get("tags")),
                       "node_count": len(nodes), "unsupported_nodes": unsupported,
                       "node_layers": {k: sum(1 for n in nodes.values() if n.get("layer") == k)
                                       for k in sorted({n.get("layer") for n in nodes.values() if n.get("layer")})},
                       "recent_events": data["events"][-20:],
                       "artifact_roles": [a.get("role") for a in (artifacts or [])],
                       "contract": ["selector semantically filters mechanically recalled candidate files",
                                    "compressor proposes project/tool/service nodes only",
                                    "tagger records multi-facet evidence-backed tags", "aggregator keeps facet/semantic layers",
                                    "nodes keep supporting_leaf_ids"]})
