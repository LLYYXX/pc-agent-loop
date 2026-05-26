"""Mechanical evidence candidate selection (core, ablation boundary B2)."""

from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ._config import (
    BINARY_EXT, CODE_EXT, IGNORE_DIRS_LOWER, MARKER_NAMES_LOWER,
    OFFICE_EXT, RECENT_DAYS, SIZE_CAP_BYTES, TEXT_EXT, norm_path,
)

@dataclass
class EvidenceFlags:
    enable_selection: bool = True

def _filename_signal(pp: Path) -> bool:
    stem = pp.stem.strip()
    compact = re.sub(r"[\W_]+", "", stem, flags=re.UNICODE)
    if len(compact) < 4:
        return False
    return bool(
        re.search(r"[\u4e00-\u9fff]{4,}", stem)
        or re.search(r"[A-Za-z][A-Za-z0-9_\-]{3,}", stem)
    )


def _candidate_kind(p: str, size_cap: int) -> str | None:
    pp = Path(p)
    if not os.path.isfile(p) or any(x.lower() in IGNORE_DIRS_LOWER for x in pp.parts):
        return None
    try:
        if pp.stat().st_size > size_cap:
            return None
    except OSError:
        return None
    if pp.suffix.lower() in BINARY_EXT:
        return "filename_only" if _filename_signal(pp) else None
    return "content"

def _classify(pp: Path, *, user: bool, fb: bool, recent_cut: float, kind: str) -> tuple[int, str]:
    if kind == "filename_only":
        return (0 if user or fb else 6), "filename_only"
    if user:
        return 0, "user_confirmed"
    if fb:
        return 0, "fallback_found"
    name = pp.name.lower()
    if name in MARKER_NAMES_LOWER or name.startswith("readme"):
        return 1, "readme" if name.startswith("readme") else "manifest"
    suf = pp.suffix.lower()
    pri, reason = 5, "search_hit"
    if suf in OFFICE_EXT:
        pri, reason = 2, "pdf" if suf == ".pdf" else "office"
    elif suf in CODE_EXT:
        pri, reason = 3, "code_like"
    elif suf in TEXT_EXT:
        pri = 4
    try:
        if pp.stat().st_mtime >= recent_cut and pri > 2:
            return 2, "recent"
    except OSError:
        pass
    return pri, reason

def select_for_read(
    paths: list[str],
    *,
    flags: EvidenceFlags | None = None,
    recent_days: int = RECENT_DAYS,
    size_cap: int = SIZE_CAP_BYTES,
    limit: int = 20,
    seeds: list[str] | None = None,
    fallback_seeds: list[str] | None = None,
) -> list[dict[str, Any]]:
    fl = flags or EvidenceFlags()
    normed = [norm_path(p) for p in paths if p]
    seeds_n = {norm_path(p) for p in (seeds or [])}
    fb_n = {norm_path(p) for p in (fallback_seeds or [])}

    if not fl.enable_selection:
        out, seen = [], set()
        for p in [*(seeds or []), *(fallback_seeds or []), *normed]:
            np = norm_path(p)
            if np and np not in seen and os.path.isfile(np):
                seen.add(np)
                out.append({"path": np, "reason": "passthrough", "priority": 0})
                if len(out) >= limit:
                    break
        return out

    recent_cut = time.time() - recent_days * 86400
    rows: list[tuple[int, str, str]] = []
    for p in list(seeds_n) + list(fb_n - seeds_n) + [x for x in normed if x not in seeds_n and x not in fb_n]:
        kind = _candidate_kind(p, size_cap)
        if not kind:
            continue
        pri, reason = _classify(Path(p), user=p in seeds_n, fb=p in fb_n, recent_cut=recent_cut, kind=kind)
        rows.append((pri, p, reason))

    rows.sort(key=lambda x: (x[0], -os.path.getmtime(x[1]) if os.path.isfile(x[1]) else 0))
    out, seen = [], set()
    for pri, p, reason in rows:
        if p in seen:
            continue
        seen.add(p)
        out.append({"path": p, "reason": reason, "priority": pri})
        if len(out) >= limit:
            break
    return out
