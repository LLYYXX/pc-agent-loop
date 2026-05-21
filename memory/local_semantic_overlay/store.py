"""Per-scope JSON overlay store."""

from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PKG = Path(__file__).resolve().parent
OVERLAYS = PKG / "overlays"
FEEDBACK_MAX = 200

SOURCE_PRIORITY = {
    "fallback_found": 0,
    "user_confirmed": 1,
    "key_evidence": 2,
    "long_maintained": 3,
    "recent": 4,
}

MARKERS = frozenset({
    "README", "README.md", "readme.md", "index.md", "package.json", "pyproject.toml",
    "requirements.txt", "Cargo.toml", "go.mod", "pom.xml", "Dockerfile",
})


def norm_path(path: str | os.PathLike[str]) -> str:
    return os.path.normpath(os.path.abspath(str(path).strip().strip('"').strip("'")))


def path_eq(a: str, b: str) -> bool:
    if os.name == "nt":
        return norm_path(a).lower() == norm_path(b).lower()
    return norm_path(a) == norm_path(b)


def path_under(path: str, scope: str) -> bool:
    p, s = norm_path(path), norm_path(scope)
    if os.name == "nt":
        p, s = p.lower(), s.lower()
    return p == s or p.startswith(s + os.sep)


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def anchor_of(path: str) -> str:
    return norm_path(os.path.dirname(path))


def safe_scope_tail(scope: str) -> str:
    name = os.path.basename(norm_path(scope)) or "root"
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name)[:40]


def overlay_path(scope: str) -> Path:
    n = norm_path(scope)
    h = hashlib.sha1(n.encode("utf-8")).hexdigest()[:8]
    OVERLAYS.mkdir(parents=True, exist_ok=True)
    return OVERLAYS / f"{safe_scope_tail(scope)}_{h}.json"


def empty_overlay(scope: str) -> dict[str, Any]:
    return {
        "meta": {
            "scope": norm_path(scope),
            "built_at": None,
            "updated_at": now_iso(),
            "status": "partial",
            "budgets": {"seed_max": 200, "bundle_max": 6, "annotate_max": 30},
            "usage": {"seeds": 0, "bundles_done": 0, "annotated": 0, "deferred": 0},
            "build": {"phase": "idle", "pending_queue": []},
        },
        "leaves": {},
        "entries": [],
        "feedback": [],
    }


def load(scope: str) -> dict[str, Any]:
    p = overlay_path(scope)
    if not p.is_file():
        return empty_overlay(scope)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return empty_overlay(scope)
    data.setdefault("meta", {})["scope"] = norm_path(data.get("meta", {}).get("scope") or scope)
    data.setdefault("leaves", {})
    data.setdefault("entries", [])
    data.setdefault("feedback", [])
    return data


def save(data: dict[str, Any]) -> None:
    scope = norm_path(data["meta"]["scope"])
    data["meta"]["scope"] = scope
    data["meta"]["updated_at"] = now_iso()
    p = overlay_path(scope)
    tmp = p.with_suffix(".tmp")
    payload = json.dumps(data, ensure_ascii=False, indent=2)
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(payload)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, p)


def get_leaf_by_path(data: dict[str, Any], path: str) -> dict[str, Any] | None:
    for leaf in data["leaves"].values():
        if path_eq(leaf.get("path", ""), path):
            return leaf
    return None


def add_leaf(data: dict[str, Any], path: str, **fields: Any) -> dict[str, Any]:
    path = norm_path(path)
    ex = get_leaf_by_path(data, path)
    if ex:
        for k, v in fields.items():
            if v is not None:
                ex[k] = v
        ex["anchor"] = anchor_of(path)
        return ex
    lid = new_id("leaf")
    leaf = {
        "path": path,
        "anchor": anchor_of(path),
        "source": fields.get("source", "recent"),
        "read_status": fields.get("read_status", "readable"),
        "evidence_type": fields.get("evidence_type"),
        "text_head": (fields.get("text_head") or "")[:1600] or None,
        "status": fields.get("status", "seed"),
        "tags": fields.get("tags") or [],
        "mtime": fields.get("mtime", 0.0),
        "ctime": fields.get("ctime", 0.0),
        "size": fields.get("size", 0),
    }
    data["leaves"][lid] = leaf
    return leaf


def leaf_id_by_path(data: dict[str, Any], path: str) -> str | None:
    for lid, leaf in data["leaves"].items():
        if path_eq(leaf.get("path", ""), path):
            return lid
    return None


def pending_leaves(data: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    q = set(data["meta"].get("build", {}).get("pending_queue") or [])
    out = [(lid, data["leaves"][lid]) for lid in q if lid in data["leaves"]]
    out.sort(key=lambda x: (
        SOURCE_PRIORITY.get(x[1].get("source", "recent"), 9),
        x[1].get("anchor", ""),
        -(x[1].get("mtime") or 0),
        x[1].get("path", ""),
    ))
    return out


def rebuild_pending_queue(data: dict[str, Any]) -> None:
    ids = [
        lid for lid, leaf in data["leaves"].items()
        if leaf.get("read_status") == "readable" and leaf.get("status") == "seed"
    ]
    ids.sort(key=lambda lid: (
        SOURCE_PRIORITY.get(data["leaves"][lid].get("source", "recent"), 9),
        data["leaves"][lid].get("anchor", ""),
        -(data["leaves"][lid].get("mtime") or 0),
        data["leaves"][lid].get("path", ""),
    ))
    data["meta"].setdefault("build", {})["pending_queue"] = ids


def tagged_leaves(data: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    return [(lid, leaf) for lid, leaf in data["leaves"].items() if leaf.get("tags")]


def leaves_by_anchor(data: dict[str, Any], anchor: str) -> list[tuple[str, dict[str, Any]]]:
    a = norm_path(anchor)
    if os.name == "nt":
        a = a.lower()
    out = []
    for lid, leaf in data["leaves"].items():
        la = leaf.get("anchor", "")
        if os.name == "nt":
            la = la.lower()
        if la == a:
            out.append((lid, leaf))
    return out


def add_feedback(data: dict[str, Any], row: dict[str, Any]) -> None:
    data.setdefault("feedback", []).append(row)
    if len(data["feedback"]) > FEEDBACK_MAX:
        data["feedback"] = data["feedback"][-FEEDBACK_MAX:]


def update_build_stats(data: dict[str, Any]) -> None:
    u = data["meta"].setdefault("usage", {})
    leaves = data["leaves"].values()
    u["seeds"] = sum(1 for l in leaves if l.get("status") == "seed")
    u["annotated"] = sum(1 for l in leaves if l.get("tags"))
    u["deferred"] = sum(1 for l in leaves if l.get("status") == "deferred")
    u["bundles_done"] = int(u.get("bundles_done") or 0)
