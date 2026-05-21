"""Readability classification and text extraction only. No tags."""

from __future__ import annotations

import os
import re
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from typing import Any

from .config import (
    BINARY_EXTENSIONS,
    DEFAULT_TEXT_HEAD_CHARS,
    MARKER_NAMES,
    OFFICE_EXTENSIONS,
    PATH_IGNORE_DIRS,
    PATH_IGNORE_DIRS_LOWER,
    SCAN_IGNORE_DIRS,
    SCAN_IGNORE_DIRS_LOWER,
    TEXT_EXTENSIONS,
)


def _dir_is_noise(name: str) -> bool:
    return name in SCAN_IGNORE_DIRS or name.lower() in SCAN_IGNORE_DIRS_LOWER


def _read_text_head(path: Path, max_chars: int = DEFAULT_TEXT_HEAD_CHARS) -> str:
    try:
        data = path.read_bytes()[: max_chars * 4]
    except OSError:
        return ""
    for enc in ("utf-8-sig", "utf-8", "gbk", "cp936", "utf-16le"):
        try:
            return data.decode(enc)[:max_chars]
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")[:max_chars]


def _extract_docx(path: Path) -> str | None:
    try:
        with zipfile.ZipFile(path, "r") as zf:
            if "word/document.xml" not in zf.namelist():
                return None
            xml_bytes = zf.read("word/document.xml")
        root = ET.fromstring(xml_bytes)
        ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
        texts = [node.text for node in root.iter(f"{{{ns['w']}}}t") if node.text]
        content = " ".join(texts).strip()
        return content[:DEFAULT_TEXT_HEAD_CHARS] if len(content) > 30 else None
    except (zipfile.BadZipFile, ET.ParseError, KeyError, OSError):
        return None


def _extract_ooxml(path: Path) -> str | None:
    try:
        with zipfile.ZipFile(path, "r") as zf:
            texts: list[str] = []
            for name in zf.namelist():
                if name.endswith(".xml") and len(texts) < 500:
                    try:
                        root = ET.fromstring(zf.read(name))
                        for elem in root.iter():
                            if elem.text and elem.text.strip():
                                texts.append(elem.text.strip())
                    except ET.ParseError:
                        continue
        content = " ".join(texts).strip()
        return content[:DEFAULT_TEXT_HEAD_CHARS] if len(content) > 30 else None
    except (zipfile.BadZipFile, OSError):
        return None


def _extract_pdf(path: Path) -> str | None:
    try:
        import pdfplumber  # type: ignore
        with pdfplumber.open(path) as pdf:
            texts = [p.extract_text() for p in pdf.pages[:5] if p.extract_text()]
            content = "\n".join(texts).strip()
            return content[:DEFAULT_TEXT_HEAD_CHARS] if len(content) > 30 else None
    except Exception:
        pass
    try:
        data = path.read_bytes()[:32000]
        strings = re.findall(rb"[\x20-\x7e\xc0-\xff]{8,}", data)
        if strings:
            text = b" ".join(strings[:200]).decode("utf-8", errors="replace")
            return text[:DEFAULT_TEXT_HEAD_CHARS] if len(text) > 50 else None
    except OSError:
        pass
    return None


def _extract_legacy_office(path: Path) -> str | None:
    try:
        data = path.read_bytes()[:32000]
        strings = re.findall(rb"[\x20-\x7e]{10,}", data)
        unicode_strings = re.findall(rb"(?:[\x20-\x7e]\x00){5,}", data)
        texts = [s.decode("ascii", errors="replace") for s in strings[:100]]
        texts.extend(s.decode("utf-16-le", errors="replace") for s in unicode_strings[:50])
        content = " ".join(texts).strip()
        return content[:DEFAULT_TEXT_HEAD_CHARS] if len(content) > 50 else None
    except OSError:
        return None


def extract_text_head(path: Path) -> tuple[str | None, str | None]:
    """Returns (text_head, extract_error)."""
    suffix = path.suffix.lower()
    try:
        if suffix == ".docx":
            head = _extract_docx(path)
        elif suffix in (".pptx", ".xlsx"):
            head = _extract_ooxml(path)
        elif suffix == ".pdf":
            head = _extract_pdf(path)
        elif suffix in (".doc", ".ppt", ".xls"):
            head = _extract_legacy_office(path)
        else:
            head = _read_text_head(path)
        if head and len(head.strip()) >= 10:
            return head, None
        return None, "extract_failed"
    except OSError:
        return None, "extract_failed"


def file_stat(path: Path) -> dict[str, Any]:
    try:
        st = path.stat()
        return {"mtime": st.st_mtime, "ctime": st.st_ctime, "size": st.st_size}
    except OSError:
        return {"mtime": 0.0, "ctime": 0.0, "size": 0}


def classify_and_extract(path: str | Path) -> dict[str, Any]:
    """Classify readability and extract text_head when possible."""
    p = Path(path)
    if any(part.lower() in PATH_IGNORE_DIRS_LOWER or part in PATH_IGNORE_DIRS for part in p.parts):
        return {
            "readable_status": "skipped_noise",
            "evidence_type": None,
            "text_head": None,
            "extract_error": "hard_ignore",
            **file_stat(p),
        }

    suffix = p.suffix.lower()
    name = p.name

    if suffix in BINARY_EXTENSIONS:
        return {
            "readable_status": "binary",
            "evidence_type": None,
            "text_head": None,
            "extract_error": "binary",
            **file_stat(p),
        }

    if name in MARKER_NAMES or name.lower().startswith("readme"):
        evidence_type = "readme" if name.lower().startswith("readme") or name == "index.md" else "manifest"
        if suffix in TEXT_EXTENSIONS or name in MARKER_NAMES:
            text_head, err = extract_text_head(p) if suffix in TEXT_EXTENSIONS | OFFICE_EXTENSIONS else (_read_text_head(p), None)
            if text_head:
                return {"readable_status": "readable", "evidence_type": evidence_type, "text_head": text_head, "extract_error": None, **file_stat(p)}
            return {"readable_status": "extract_failed", "evidence_type": evidence_type, "text_head": None, "extract_error": err or "extract_failed", **file_stat(p)}

    if suffix in TEXT_EXTENSIONS:
        text_head, err = extract_text_head(p)
        if text_head:
            et = "code_head" if suffix in {".py", ".js", ".ts", ".java", ".go", ".rs", ".cpp", ".c", ".cs"} else "text_head"
            return {"readable_status": "readable", "evidence_type": et, "text_head": text_head, "extract_error": None, **file_stat(p)}
        return {"readable_status": "extract_failed", "evidence_type": "text_head", "text_head": None, "extract_error": err, **file_stat(p)}

    if suffix in OFFICE_EXTENSIONS:
        text_head, err = extract_text_head(p)
        if text_head:
            et = {".pdf": "pdf_head", ".docx": "office_head", ".pptx": "office_head", ".xlsx": "office_head"}.get(suffix, "office_head")
            return {"readable_status": "readable", "evidence_type": et, "text_head": text_head, "extract_error": None, **file_stat(p)}
        return {"readable_status": "extract_failed", "evidence_type": "office_head", "text_head": None, "extract_error": err, **file_stat(p)}

    return {
        "readable_status": "binary",
        "evidence_type": None,
        "text_head": None,
        "extract_error": "unknown_type",
        **file_stat(p),
    }
