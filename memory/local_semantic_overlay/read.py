"""Reader / evidence gate (core): readable evidence or mechanical read_status."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from typing import Any

from ._config import (
    BINARY_EXT, CODE_EXT, HEAD_MAX, IGNORE_DIRS_LOWER, MARKER_NAMES_LOWER,
    MIN_READABLE_CHARS, OFFICE_EXT, RAW_ARTIFACT_RE, TEXT_EXT, norm_path,
)

_CJK_RE = re.compile(r"[\u4e00-\u9fff]{2,}")
_WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9_\-]{2,}")
_TABULAR_RE = re.compile(r"[,|\t].*[,|\t]|^\s*[\w\u4e00-\u9fff]+\s*[:：]\s*\S", re.M)
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")

def looks_like_raw_dump(text: str) -> bool:
    t = (text or "").strip()
    head = t[:800]
    return bool(
        not t or RAW_ARTIFACT_RE.search(head) or "<w:" in t[:120]
        or _CONTROL_RE.search(head) or head.count("\ufffd") > max(8, len(head) // 20)
    )

def sanitize_display(text: str) -> str:
    t = (text or "").strip()
    if not t or looks_like_raw_dump(t):
        return ""
    lines = [
        line.strip() for line in t.splitlines()
        if line.strip() and not RAW_ARTIFACT_RE.search(line)
        and not (line.strip().startswith("<?xml") or "<w:" in line[:40])
    ]
    out = "\n".join(lines).strip()
    return "" if not out or looks_like_raw_dump(out) else out

def _readable(text: str) -> bool:
    t = text.strip()
    return len(t) >= MIN_READABLE_CHARS and bool(
        _CJK_RE.search(t) or len(_WORD_RE.findall(t)) >= 2 or _TABULAR_RE.search(t[:1200])
    )

def _read_bytes(path: Path) -> str:
    try:
        data = path.read_bytes()[: HEAD_MAX * 4]
    except OSError:
        return ""
    if data.startswith((b"\xff\xfe", b"\xfe\xff")) or data[:200].count(b"\x00") > 20:
        for enc in ("utf-16", "utf-16le", "utf-16be"):
            try:
                decoded = data.decode(enc)
            except (UnicodeDecodeError, UnicodeError):
                continue
            if not _CONTROL_RE.search(decoded[:800]):
                return decoded[:HEAD_MAX]
    for enc in ("utf-8-sig", "utf-8", "gbk", "cp936", "utf-16le"):
        try:
            return data.decode(enc)[:HEAD_MAX]
        except (UnicodeDecodeError, UnicodeError):
            continue
    return data.decode("utf-8", errors="replace")[:HEAD_MAX]

def _ooxml(path: Path) -> str | None:
    try:
        with zipfile.ZipFile(path) as zf:
            if path.suffix.lower() == ".docx" and "word/document.xml" in zf.namelist():
                root = ET.fromstring(zf.read("word/document.xml"))
                ns = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
                s = " ".join(n.text for n in root.iter(f"{{{ns}}}t") if n.text).strip()
                return s[:HEAD_MAX] if len(s) > 30 else None
            parts: list[str] = []
            for name in zf.namelist():
                if not name.endswith(".xml") or len(parts) >= 400:
                    continue
                try:
                    root = ET.fromstring(zf.read(name))
                    for e in root.iter():
                        if e.text and e.text.strip():
                            parts.append(e.text.strip())
                except ET.ParseError:
                    pass
            s = " ".join(parts).strip()
            return s[:HEAD_MAX] if len(s) > 30 else None
    except (OSError, zipfile.BadZipFile, ET.ParseError):
        return None

def _binary_strings(path: Path) -> str | None:
    try:
        data = path.read_bytes()[:32000]
        pat = rb"[\x20-\x7e\xc0-\xff]{8,}" if path.suffix.lower() == ".pdf" else rb"[\x20-\x7e]{10,}"
        strings = re.findall(pat, data)
        if not strings:
            return None
        dec = "utf-8" if path.suffix.lower() == ".pdf" else "ascii"
        s = b" ".join(strings[:120 if path.suffix.lower() == ".pdf" else 80]).decode(dec, errors="replace")
        return s[:HEAD_MAX] if len(s) > (50 if path.suffix.lower() == ".pdf" else 50) else None
    except OSError:
        return None

def _extract_raw(path: Path) -> str | None:
    suf = path.suffix.lower()
    if suf in (".docx", ".pptx", ".xlsx"):
        return _ooxml(path)
    if suf == ".pdf" or suf in (".doc", ".ppt", ".xls"):
        return _binary_strings(path)
    return _read_bytes(path)

def _evidence_type(path: Path) -> str | None:
    name = path.name.lower()
    if name in MARKER_NAMES_LOWER or name.startswith("readme"):
        return "readme" if name.startswith("readme") else "manifest"
    suf = path.suffix.lower()
    if suf in TEXT_EXT:
        return "code_head" if suf in CODE_EXT else "text_head"
    if suf in OFFICE_EXT:
        return "pdf_head" if suf == ".pdf" else "office_head"
    return None

def _stat(path: Path) -> dict[str, float | int]:
    try:
        st = path.stat()
        return {"mtime": st.st_mtime, "size": st.st_size}
    except OSError:
        return {"mtime": 0.0, "size": 0}

def _gate(base: dict[str, Any], status: str, et: str | None = None) -> dict[str, Any]:
    return {**base, "ok": True, "read_status": status, "text_head": None, "evidence_type": et}

def read_leaf(path: str) -> dict[str, Any]:
    p = Path(norm_path(path))
    base: dict[str, Any] = {"path": str(p), **_stat(p)}
    if any(part.lower() in IGNORE_DIRS_LOWER for part in p.parts):
        return _gate(base, "skipped_noise")
    suf = p.suffix.lower()
    if suf in BINARY_EXT:
        return _gate(base, "binary")
    et = _evidence_type(p)
    if et is None and suf not in TEXT_EXT and suf not in OFFICE_EXT:
        return _gate(base, "binary")
    raw = _extract_raw(p)
    if not raw:
        return _gate(base, "extract_failed", et)
    if looks_like_raw_dump(raw):
        return _gate(base, "skipped_noise", et)
    text_head = sanitize_display(raw)[:HEAD_MAX]
    if not text_head or not _readable(text_head):
        return _gate(base, "skipped_noise", et)
    return {**base, "ok": True, "read_status": "readable", "text_head": text_head, "evidence_type": et}
