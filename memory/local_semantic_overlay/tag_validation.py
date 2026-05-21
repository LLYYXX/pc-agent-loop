"""Minimal validation for leaf tags and evidence_note."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .config import MIN_EVIDENCE_NOTE_CHARS

_TEMPLATE_NOTE_PATTERNS = (
    "based on path",
    "derived from filename",
    "derived from path",
    "file extension indicates",
    "evidence_title indicates",
)

_PATH_ONLY_RE = re.compile(r"^[a-z]?[:\\/]|[\\/]{2,}", re.IGNORECASE)


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.strip().lower())


def note_is_template(note: str) -> bool:
    n = _norm(note)
    return any(pat in n for pat in _TEMPLATE_NOTE_PATTERNS)


def _looks_path_only(tag: str) -> bool:
    t = tag.strip()
    if _PATH_ONLY_RE.search(t):
        return True
    parts = re.split(r"[-_/\\]", t)
    if len(parts) >= 4 and all(len(p) <= 3 for p in parts):
        return True
    return False


def tag_derived_from_path(leaf: dict[str, Any], tag: str) -> bool:
    t = _norm(tag)
    if len(t) < 2:
        return True
    path = Path(leaf.get("path") or "")
    stem = _norm(path.stem)
    if t == stem or t == _norm(path.name):
        return True
    suf = path.suffix.lower().lstrip(".")
    return bool(suf and t == suf)


def validate_evidence_note(leaf: dict[str, Any], note: str) -> str | None:
    note = (note or "").strip()
    if len(note) < MIN_EVIDENCE_NOTE_CHARS:
        return "evidence_note too short"
    if note_is_template(note):
        return "template evidence_note"
    return None


def validate_tag(leaf: dict[str, Any], tag: str) -> str | None:
    if _looks_path_only(tag):
        return "path-only tag"
    if tag_derived_from_path(leaf, tag):
        return "tag derived from path/filename/extension"
    return None
