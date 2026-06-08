"""Small document text extractor for LSO tag evidence."""
from __future__ import annotations

import csv, io, re, zipfile
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

TEXT_EXT = {".txt", ".md", ".rst", ".csv", ".tsv"}
OFFICE_EXT = {".docx", ".pptx", ".xlsx"}

def _cut(text: str, max_chars: int) -> tuple[str, bool]:
    text = re.sub(r"[ \t\r\f\v]+", " ", text or "")
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text[:max_chars], len(text) > max_chars

def _texts(xml: bytes) -> list[str]:
    try: root = ET.fromstring(xml)
    except ET.ParseError: return []
    return [x.text.strip() for x in root.iter() if x.text and x.text.strip()]

def _zip_members(path: Path, pattern: str) -> list[tuple[str, bytes]]:
    with zipfile.ZipFile(path) as z:
        names = sorted(n for n in z.namelist() if re.fullmatch(pattern, n))
        return [(n, z.read(n)) for n in names]

def _docx(path: Path) -> str:
    return "\n".join(" ".join(_texts(xml)) for _, xml in _zip_members(path, r"word/(document|header\d+|footer\d+)\.xml"))

def _pptx(path: Path) -> str:
    return "\n".join(" ".join(_texts(xml)) for _, xml in _zip_members(path, r"ppt/slides/slide\d+\.xml"))

def _xlsx(path: Path) -> str:
    shared: list[str] = []
    try:
        for _, xml in _zip_members(path, r"xl/sharedStrings\.xml"): shared = _texts(xml)
    except Exception:
        shared = []
    rows: list[str] = []
    for _, xml in _zip_members(path, r"xl/worksheets/sheet\d+\.xml"):
        try: root = ET.fromstring(xml)
        except ET.ParseError: continue
        vals: list[str] = []
        for c in root.iter():
            if not c.tag.endswith("}c") and c.tag != "c": continue
            typ = c.attrib.get("t"); raw = next((x.text for x in c if x.tag.endswith("}v") or x.tag == "v"), None)
            vals.append(shared[int(raw)] if typ == "s" and raw and raw.isdigit() and int(raw) < len(shared) else (raw or ""))
        if vals: rows.append(", ".join(x for x in vals if x))
    return "\n".join(rows or shared)

def _plain(path: Path, max_chars: int) -> str:
    raw = path.read_bytes()[: max_chars * 4]
    for enc in ("utf-8-sig", "utf-16", "gb18030", "latin-1"):
        try: text = raw.decode(enc); break
        except UnicodeDecodeError: pass
    else: text = raw.decode("utf-8", "replace")
    if path.suffix.lower() in (".csv", ".tsv"):
        delim = "\t" if path.suffix.lower() == ".tsv" else ","
        rows = list(csv.reader(io.StringIO(text), delimiter=delim))[:50]
        text = "\n".join(", ".join(x[:20]) for x in rows)
    return text

def _pdf(path: Path, max_chars: int) -> tuple[str, str]:
    for mod in ("pypdf", "PyPDF2"):
        try:
            reader = __import__(mod).PdfReader(str(path)); chunks = []
            for page in reader.pages:
                chunks.append(page.extract_text() or "")
                if sum(map(len, chunks)) >= max_chars: break
            return "\n".join(chunks), mod
        except ImportError:
            continue
    raise RuntimeError("pdf_library_missing")

def extract_text(path: str, max_chars: int = 4000) -> dict[str, Any]:
    p = Path(path); ext = p.suffix.lower()
    if not p.is_file(): return {"ok": False, "path": str(p), "kind": ext, "text": "", "method": None, "truncated": False, "error": "not_file"}
    try:
        if ext in TEXT_EXT: text, method = _plain(p, max_chars), "plain"
        elif ext == ".docx": text, method = _docx(p), "zip_xml_docx"
        elif ext == ".pptx": text, method = _pptx(p), "zip_xml_pptx"
        elif ext == ".xlsx": text, method = _xlsx(p), "zip_xml_xlsx"
        elif ext == ".pdf": text, method = _pdf(p, max_chars)
        elif ext in {".doc", ".ppt", ".xls"}: raise RuntimeError("legacy_office_unsupported")
        else: raise RuntimeError("unsupported_format")
        text, truncated = _cut(text, max_chars)
        return {"ok": bool(text), "path": str(p), "kind": ext, "text": text, "method": method, "truncated": truncated, "error": None if text else "empty_text"}
    except Exception as e:
        return {"ok": False, "path": str(p), "kind": ext, "text": "", "method": None, "truncated": False, "error": str(e)}
