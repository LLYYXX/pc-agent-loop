"""text_head extraction, raw dump gate, display sanitizer."""

from __future__ import annotations

import os
import re
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from typing import Any

from .store import MARKERS, norm_path

TEXT_EXT = {".md", ".txt", ".rst", ".json", ".yaml", ".yml", ".toml", ".py", ".js", ".ts", ".csv", ".xml", ".ini"}
OFFICE_EXT = {".pdf", ".doc", ".docx", ".ppt", ".pptx", ".xls", ".xlsx"}
BINARY_EXT = {".png", ".jpg", ".jpeg", ".gif", ".zip", ".exe", ".dll", ".so", ".mp4", ".mp3", ".bin"}
IGNORE_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build", "target", ".cache"}
RAW_RE = re.compile(
    r"PK\x03\x04|\[Content_Types\]\.xml|_rels/|xl/workbook\.xml|theme/theme|"
    r"Root Entry|SummaryInformation|WordDocument|%PDF-1\.",
    re.I,
)
HEAD_MAX = 1600


def looks_like_raw_dump(text: str) -> bool:
    t = (text or "").strip()
    if not t or RAW_RE.search(t[:500]):
        return True
    if t.startswith("<?xml") or "<w:" in t[:80]:
        return True
    if len(t) > 120 and t.count('"') > 8:
        return True
    return False


def sanitize_display(text: str) -> str:
    t = (text or "").strip()
    return "" if not t or looks_like_raw_dump(t) else t


def _read_bytes(path: Path) -> str:
    try:
        data = path.read_bytes()[: HEAD_MAX * 4]
    except OSError:
        return ""
    for enc in ("utf-8-sig", "utf-8", "gbk", "cp936"):
        try:
            return data.decode(enc)[:HEAD_MAX]
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")[:HEAD_MAX]


def _ooxml(path: Path) -> str | None:
    try:
        with zipfile.ZipFile(path) as zf:
            if path.suffix.lower() == ".docx" and "word/document.xml" in zf.namelist():
                root = ET.fromstring(zf.read("word/document.xml"))
                ns = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
                texts = [n.text for n in root.iter(f"{{{ns}}}t") if n.text]
                s = " ".join(texts).strip()
                return s[:HEAD_MAX] if len(s) > 30 else None
            parts = []
            for name in zf.namelist():
                if name.endswith(".xml") and len(parts) < 400:
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


def _pdf(path: Path) -> str | None:
    try:
        data = path.read_bytes()[:32000]
        strings = re.findall(rb"[\x20-\x7e\xc0-\xff]{8,}", data)
        if strings:
            s = b" ".join(strings[:120]).decode("utf-8", errors="replace")
            return s[:HEAD_MAX] if len(s) > 50 else None
    except OSError:
        pass
    return None


def _legacy(path: Path) -> str | None:
    try:
        data = path.read_bytes()[:32000]
        strings = re.findall(rb"[\x20-\x7e]{10,}", data)
        s = " ".join(x.decode("ascii", errors="replace") for x in strings[:80]).strip()
        return s[:HEAD_MAX] if len(s) > 50 else None
    except OSError:
        return None


def _extract(path: Path) -> tuple[str | None, str | None]:
    suf = path.suffix.lower()
    if suf == ".docx":
        h = _ooxml(path)
    elif suf in (".pptx", ".xlsx"):
        h = _ooxml(path)
    elif suf == ".pdf":
        h = _pdf(path)
    elif suf in (".doc", ".ppt", ".xls"):
        h = _legacy(path)
    else:
        h = _read_bytes(path)
    if h and len(h.strip()) >= 10 and not looks_like_raw_dump(h):
        return h.strip()[:HEAD_MAX], None
    return None, "extract_failed"


def display_title(path: str, evidence_type: str | None) -> str:
    name = Path(path).name
    if name in MARKERS or name.lower().startswith("readme"):
        return "README" if "readme" in name.lower() else "Manifest"
    et = (evidence_type or "").lower()
    return {"pdf_head": "PDF", "office_head": "Office", "code_head": "Code", "text_head": "Text"}.get(et, "File")


def read_leaf(path: str) -> dict[str, Any]:
    p = Path(norm_path(path))
    if any(part in IGNORE_DIRS or part.lower() in IGNORE_DIRS for part in p.parts):
        return {"path": str(p), "ok": False, "read_status": "skipped_noise", "text_head": None}
    suf = p.suffix.lower()
    st = {"mtime": 0.0, "ctime": 0.0, "size": 0}
    try:
        s = p.stat()
        st = {"mtime": s.st_mtime, "ctime": s.st_ctime, "size": s.st_size}
    except OSError:
        pass
    if suf in BINARY_EXT:
        return {"path": str(p), "ok": True, "read_status": "binary", "text_head": None, "evidence_type": None, **st}
    name = p.name
    if name in MARKERS or name.lower().startswith("readme"):
        et = "readme" if "readme" in name.lower() else "manifest"
    elif suf in TEXT_EXT:
        et = "code_head" if suf in {".py", ".js", ".ts"} else "text_head"
    elif suf in OFFICE_EXT:
        et = "pdf_head" if suf == ".pdf" else "office_head"
    else:
        return {"path": str(p), "ok": True, "read_status": "binary", "text_head": None, "evidence_type": None, **st}
    head, err = _extract(p)
    if head:
        return {
            "path": str(p), "ok": True, "text_head": head, "read_status": "readable",
            "evidence_type": et, **st, "display_title": display_title(str(p), et),
        }
    rs = "extract_failed" if err else "binary"
    return {"path": str(p), "ok": True, "read_status": rs, "text_head": None, "evidence_type": et, **st,
            "display_title": display_title(str(p), et)}
