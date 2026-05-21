"""Select readable high-value leaf seeds only. No tags or aggregation."""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

from . import store
from .config import (
    ALLOWED_SEED_SOURCES,
    SCHEDULING_SEED_SOURCE_ORDER,
    DEFAULT_CANDIDATE_LEAF_BUDGET,
    DEFAULT_LEAF_BUDGET,
    DEFAULT_LONG_MAINTAINED_MIN_SPAN_DAYS,
    DEFAULT_RECENT_DAYS,
    KEY_EVIDENCE_EVIDENCE_TYPES,
    MARKER_NAMES,
    OFFICE_EXTENSIONS,
    ORG_SUBDIR_NAMES,
    SCAN_IGNORE_DIRS,
    SCAN_IGNORE_DIRS_LOWER,
    TEXT_EXTENSIONS,
)
from .leaf_read import classify_and_extract


def _dir_is_noise(name: str) -> bool:
    return name in SCAN_IGNORE_DIRS or name.lower() in SCAN_IGNORE_DIRS_LOWER


def _safe_scandir(path: Path) -> list[os.DirEntry[str]]:
    try:
        with os.scandir(path) as it:
            return list(it)
    except OSError:
        return []


def _span_days(mtime: float, ctime: float) -> float:
    """mtime - ctime in days (last_modified - creation)."""
    return max(0.0, (mtime - ctime) / 86400.0)


def is_key_evidence_file(path: Path) -> bool:
    """Marker/config/manifest or representative office readable — not directory names."""
    name = path.name
    if name in MARKER_NAMES or name.lower().startswith("readme") or name == "index.md":
        return True
    if path.suffix.lower() in OFFICE_EXTENSIONS:
        return True
    return False


def _register_seed(
    session_id: str,
    path: Path,
    seed_source: str,
    budget: list[dict[str, Any]],
    *,
    force_source: bool = False,
) -> bool:
    if seed_source not in ALLOWED_SEED_SOURCES:
        return False
    if len(budget) >= DEFAULT_LEAF_BUDGET * 10:
        return False
    info = classify_and_extract(path)
    existing = store.get_leaf_by_path(path)
    effective_source = seed_source
    if existing and existing.get("seed_source") in ALLOWED_SEED_SOURCES and not force_source:
        # Prefer higher-signal sources when re-registering the same path.
        priority = SCHEDULING_SEED_SOURCE_ORDER
        old = existing["seed_source"]
        if priority.index(old) <= priority.index(seed_source):
            effective_source = old
        else:
            effective_source = seed_source
    leaf = store.upsert_leaf(
        session_id,
        path,
        readable_status=info["readable_status"],
        evidence_type=info.get("evidence_type"),
        semantic_status="seed" if info["readable_status"] == "readable" else "deferred",
        text_head=info.get("text_head"),
        extract_error=info.get("extract_error"),
        mtime=info.get("mtime"),
        ctime=info.get("ctime"),
        size=info.get("size"),
        seed_source=effective_source,
    )
    budget.append(leaf)
    return True


def _detect_org_signals(dir_path: Path) -> list[str]:
    """Structural signals only (child layout), never directory name semantics."""
    signals: list[str] = []
    entries = _safe_scandir(dir_path)[:200]
    names = {e.name for e in entries}
    if names & MARKER_NAMES or any(n.lower().startswith("readme") for n in names):
        signals.append("has_readme_or_manifest")
    child_dirs = {e.name.lower() for e in entries if e.is_dir(follow_symlinks=False)}
    if child_dirs & ORG_SUBDIR_NAMES:
        signals.append("has_org_subdirs")
    if len([e for e in entries if e.is_file()]) >= 5:
        signals.append("file_dense")
    return signals


def _collect_key_evidence_seeds(
    session_id: str,
    scope: Path,
    collected: list[dict[str, Any]],
    seen: set[str],
    budget: int,
) -> int:
    """Register marker/config/office key files as key_evidence seeds (limited walk)."""
    added = 0
    queue: list[Path] = [scope]
    walked = 0
    while queue and walked < 4000:
        current = queue.pop(0)
        walked += 1
        if _dir_is_noise(current.name) and current != scope:
            continue
        for entry in _safe_scandir(current)[:250]:
            p = Path(entry.path)
            if entry.is_dir(follow_symlinks=False) and not _dir_is_noise(p.name):
                if len(queue) < 400:
                    queue.append(p)
            elif entry.is_file(follow_symlinks=False) and is_key_evidence_file(p):
                if str(p) in seen:
                    continue
                readable_count = len([c for c in collected if c.get("readable_status") == "readable"])
                if readable_count >= budget:
                    return added
                if _register_seed(session_id, p, "key_evidence", collected, force_source=True):
                    seen.add(str(p))
                    added += 1
    return added


def discover_leaf_seeds(session_id: str, budget: int | None = None) -> dict[str, Any]:
    session = store.get_ingestion_session(session_id)
    if not session:
        return {"ok": False, "error": "session not found"}

    if budget is None:
        budget = int(
            session.get("candidate_leaf_budget")
            or session.get("leaf_budget")
            or DEFAULT_CANDIDATE_LEAF_BUDGET
        )

    scope = Path(session["scope"])
    if not scope.exists():
        return {"ok": False, "error": "scope not found"}

    collected: list[dict[str, Any]] = []
    seen: set[str] = set()
    now = time.time()
    recent_cutoff = now - DEFAULT_RECENT_DAYS * 86400

    def add_path(p: Path, source: str) -> None:
        if str(p) in seen or not p.is_file():
            return
        seen.add(str(p))
        if len([c for c in collected if c.get("readable_status") == "readable"]) >= budget:
            return
        if is_key_evidence_file(p):
            _register_seed(session_id, p, "key_evidence", collected, force_source=True)
        else:
            _register_seed(session_id, p, source, collected)

    # 1. user-confirmed (prior sessions)
    for leaf in store.list_leaves(limit=5000):
        if int(leaf.get("confirm_count") or 0) > 0:
            add_path(Path(leaf["path"]), "user_confirmed")

    # 2. recent + long-maintained via scoped walk (not a seed source itself)
    queue: list[Path] = [scope]
    walked = 0
    while queue and walked < 8000:
        current = queue.pop(0)
        walked += 1
        if _dir_is_noise(current.name) and current != scope:
            continue
        for entry in _safe_scandir(current)[:300]:
            p = Path(entry.path)
            if entry.is_dir(follow_symlinks=False) and not _dir_is_noise(p.name):
                if len(queue) < 500:
                    queue.append(p)
            elif entry.is_file(follow_symlinks=False):
                try:
                    st = entry.stat(follow_symlinks=False)
                    mtime, ctime = st.st_mtime, st.st_ctime
                except OSError:
                    continue
                if mtime >= recent_cutoff:
                    add_path(p, "recent")
                if _span_days(mtime, ctime) >= DEFAULT_LONG_MAINTAINED_MIN_SPAN_DAYS:
                    add_path(p, "long_maintained")

    # 3. explicit key_evidence pass (markers, configs, office heads)
    key_added = _collect_key_evidence_seeds(session_id, scope, collected, seen, budget)

    readable = [c for c in collected if c.get("readable_status") == "readable"]
    by_source: dict[str, int] = {}
    for c in collected:
        src = c.get("seed_source") or "unknown"
        by_source[src] = by_source.get(src, 0) + 1

    return {
        "ok": True,
        "session_id": session_id,
        "seed_count": len(collected),
        "readable_seed_count": len(readable),
        "key_evidence_added": key_added,
        "by_seed_source": by_source,
        "seeds": collected[:100],
    }


def register_fallback_seed(session_id: str | None, path: str | Path) -> dict[str, Any]:
    """Register a path found via fallback or query feedback."""
    if session_id is None:
        session_id = store.get_latest_open_session_id()
    info = classify_and_extract(path)
    leaf = store.upsert_leaf(
        session_id,
        path,
        readable_status=info["readable_status"],
        evidence_type=info.get("evidence_type") or "fallback_found",
        semantic_status="seed" if info["readable_status"] == "readable" else "deferred",
        text_head=info.get("text_head"),
        extract_error=info.get("extract_error"),
        mtime=info.get("mtime"),
        ctime=info.get("ctime"),
        size=info.get("size"),
        seed_source="fallback_found",
    )
    return {"ok": True, "leaf": leaf}
