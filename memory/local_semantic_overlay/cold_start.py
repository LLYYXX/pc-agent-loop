"""Area-aware annotation-first cold start for Local Semantic Overlay v2.

Flow: area census -> representative evidence sampling -> LLM file-level
annotation -> annotation as runtime asset -> route lift from annotations
-> hard finish gate.

This module does NOT build a file-level index, does NOT use global BFS + top-N
candidates, and does NOT auto-commit routes. Routes are only created from
evidence-backed annotations applied by the calling LLM.
"""

from __future__ import annotations

import os
import re
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from typing import Any

from . import store
from .config import (
    ANNOTATION_DECISIONS,
    DEFAULT_EVIDENCE_BUDGET,
    DEFAULT_PACKET_SIZE,
    DEFAULT_SEED_ROUTE_BUDGET,
    DEFAULT_SURVEY_MAX_DEPTH,
    DEFAULT_TEXT_HEAD_CHARS,
    ENTRYPOINT_NAMES,
    EVIDENCE_BUCKETS,
    GENERIC_TAGS,
    HARD_IGNORE_DIRS,
    HARD_IGNORE_DIRS_LOWER,
    MARKER_FILE_NAMES,
    OFFICE_PDF_EXTENSIONS,
    TEXT_EVIDENCE_EXTENSIONS,
)

MAX_ENTRIES_PER_DIR = 500
MAX_EVIDENCE_PER_AREA = 12
DEEP_REPR_MAX_EXTRA_DEPTH = 2
DEEP_REPR_MAX_FILES = 4

_PATH_ONLY_TAG_RE = re.compile(
    r"^[a-z]?[:\\/]|[\\/]{2,}|^[a-z]-[a-z]+-[a-z]+-[a-z]+$",
    re.IGNORECASE,
)


def _is_hard_ignored(path: Path) -> bool:
    return any(part.lower() in HARD_IGNORE_DIRS_LOWER or part in HARD_IGNORE_DIRS for part in path.parts)


def _dir_is_noise(name: str) -> bool:
    return name in HARD_IGNORE_DIRS or name.lower() in HARD_IGNORE_DIRS_LOWER


def _safe_scandir(path: Path) -> list[os.DirEntry[str]]:
    try:
        with os.scandir(path) as entries:
            return list(entries)
    except OSError:
        return []


def _is_text(path: Path) -> bool:
    return path.suffix.lower() in TEXT_EVIDENCE_EXTENSIONS or path.name in MARKER_FILE_NAMES


def _is_office_pdf(path: Path) -> bool:
    return path.suffix.lower() in OFFICE_PDF_EXTENSIONS


def _read_head(path: Path, max_chars: int = DEFAULT_TEXT_HEAD_CHARS) -> str:
    try:
        data = path.read_bytes()[:max_chars * 4]
    except OSError:
        return ""
    for enc in ("utf-8-sig", "utf-8", "gbk", "cp936", "utf-16le"):
        try:
            return data.decode(enc)[:max_chars]
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")[:max_chars]


def _stat_mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _looks_path_only(tag: str) -> bool:
    """Reject tags that are just path fragments or drive-letter derivatives."""
    t = tag.strip()
    if _PATH_ONLY_TAG_RE.search(t):
        return True
    parts = re.split(r"[-_/\\]", t)
    if len(parts) >= 4 and all(len(p) <= 3 for p in parts):
        return True
    return False


# ---------------------------------------------------------------------------
# Phase 2: Area Census
# ---------------------------------------------------------------------------

def begin_seed_map(scope: str, route_budget: int = DEFAULT_SEED_ROUTE_BUDGET) -> dict[str, Any]:
    session = store.create_seed_session(scope, route_budget)
    return {"ok": True, "session": session, "session_id": session["session_id"]}


def survey_scope(session_id: str, max_depth: int = DEFAULT_SURVEY_MAX_DEPTH) -> dict[str, Any]:
    session = store.get_seed_session(session_id)
    if not session:
        return {"ok": False, "session_id": session_id, "error": "session not found"}

    scope = Path(session["scope"])
    if not scope.exists():
        store.update_seed_session(session_id, status="failed", report={"reason": "scope_not_found"})
        return {"ok": False, "session_id": session_id, "error": "scope not found"}

    areas_created: list[dict[str, Any]] = []
    queue: list[tuple[Path, int, str | None]] = [(scope, 0, None)]

    while queue:
        current, depth, parent_id = queue.pop(0)
        if depth > 0 and _dir_is_noise(current.name):
            store.upsert_area(session_id, current, parent_area_id=parent_id,
                              depth=depth, status="ignored_noise", signals=["hard_ignore"])
            areas_created.append({"path": str(current), "status": "ignored_noise"})
            continue

        entries = _safe_scandir(current)[:MAX_ENTRIES_PER_DIR]
        files = [Path(e.path) for e in entries if e.is_file(follow_symlinks=False)]
        dirs = [Path(e.path) for e in entries if e.is_dir(follow_symlinks=False)]

        signals = _compute_signals(current, files, dirs, depth)
        status = "profiled"

        area = store.upsert_area(
            session_id, current,
            parent_area_id=parent_id,
            depth=depth,
            status=status,
            file_count=len(files),
            dir_count=len(dirs),
            signals=signals,
            profile={
                "file_extensions": _extension_summary(files),
                "has_readme": any(f.name.lower().startswith("readme") for f in files),
                "has_manifest": any(f.name in MARKER_FILE_NAMES for f in files),
                "has_entrypoint": any(f.name in ENTRYPOINT_NAMES for f in files),
                "has_office_pdf": any(_is_office_pdf(f) for f in files),
            },
        )
        areas_created.append(area)

        if depth < max_depth:
            for d in sorted(dirs, key=lambda p: p.name.lower()):
                queue.append((d, depth + 1, area["area_id"]))

    return {
        "ok": True,
        "session_id": session_id,
        "scope": str(scope),
        "area_count": len(areas_created),
        "areas": areas_created[:50],
    }


def _compute_signals(path: Path, files: list[Path], dirs: list[Path], depth: int) -> list[str]:
    signals: list[str] = []
    if depth == 0:
        signals.append("scope_root")
    elif depth <= 1:
        signals.append("top_level_area")
    names = {f.name for f in files}
    if names & MARKER_FILE_NAMES:
        signals.append("has_manifest")
    if any(n.lower().startswith("readme") for n in names):
        signals.append("has_readme")
    if names & ENTRYPOINT_NAMES:
        signals.append("has_entrypoint")
    if any(_is_office_pdf(f) for f in files):
        signals.append("has_office_pdf")
    if any(_is_text(f) for f in files):
        signals.append("has_text_evidence")
    if len(files) >= 8:
        signals.append("file_dense")
    if len(dirs) >= 3:
        signals.append("has_child_areas")
    if len(files) == 0 and len(dirs) == 0:
        signals.append("empty")
    if len(files) <= 3 and len(dirs) == 0:
        signals.append("sparse")
    return signals


def _extension_summary(files: list[Path]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for f in files:
        ext = f.suffix.lower() or "(no ext)"
        counts[ext] = counts.get(ext, 0) + 1
    return dict(sorted(counts.items(), key=lambda x: -x[1])[:20])


def list_areas(session_id: str, status: str | None = None, limit: int | None = None) -> dict[str, Any]:
    session = store.get_seed_session(session_id)
    if not session:
        return {"ok": False, "session_id": session_id, "error": "session not found"}
    areas = store.list_areas(session_id, status=status, limit=limit)
    return {"ok": True, "session_id": session_id, "areas": areas, "count": len(areas)}


def area_profile(session_id: str, area_id: str) -> dict[str, Any]:
    area = store.get_area(area_id)
    if not area or area["session_id"] != session_id:
        return {"ok": False, "error": "area not found in session"}
    evidence = store.list_evidence_items(area_id=area_id)
    annotations = store.list_annotations(area_id=area_id)
    return {
        "ok": True,
        "area": area,
        "evidence_items": evidence,
        "annotations": annotations,
        "evidence_count": len(evidence),
        "annotation_count": len(annotations),
    }


# ---------------------------------------------------------------------------
# Phase 3: Representative Evidence Sampling  (fix2 + fix3)
# ---------------------------------------------------------------------------

def collect_area_evidence(
    session_id: str,
    area_id: str | None = None,
    total_budget: int = DEFAULT_EVIDENCE_BUDGET,
) -> dict[str, Any]:
    session = store.get_seed_session(session_id)
    if not session:
        return {"ok": False, "session_id": session_id, "error": "session not found"}

    if area_id:
        areas = [store.get_area(area_id)]
        areas = [a for a in areas if a and a["session_id"] == session_id]
    else:
        areas = store.list_areas(session_id)

    non_noise = [a for a in areas if a["status"] not in ("ignored_noise", "out_of_scope")]
    if not non_noise:
        return {"ok": True, "session_id": session_id, "evidence_count": 0, "message": "no eligible areas"}

    per_area = max(3, total_budget // max(1, len(non_noise)))
    total_collected: list[dict[str, Any]] = []
    sampled_ids: set[str] = set()
    skipped: list[dict[str, Any]] = []

    for area in non_noise:
        if len(total_collected) >= total_budget:
            # fix2: explicitly mark skipped areas instead of silent break
            store.update_area_status(area["area_id"], "needs_more_evidence")
            skipped.append({"area_id": area["area_id"], "path": area["path"]})
            continue
        collected = _sample_area_evidence(session_id, area, min(per_area, MAX_EVIDENCE_PER_AREA))
        total_collected.extend(collected)
        sampled_ids.add(area["area_id"])

    return {
        "ok": True,
        "session_id": session_id,
        "evidence_count": len(total_collected),
        "areas_sampled": len(sampled_ids),
        "areas_skipped": len(skipped),
        "skipped_areas": skipped[:50],
        "evidence_items": total_collected[:100],
    }


def _collect_deep_files(
    base: Path,
    used: set[str],
    max_depth: int = DEEP_REPR_MAX_EXTRA_DEPTH,
    max_files: int = DEEP_REPR_MAX_FILES,
) -> list[Path]:
    """Recursively collect representative files from subdirectories."""
    result: list[Path] = []
    queue: list[tuple[Path, int]] = [(base, 0)]
    while queue and len(result) < max_files:
        current, depth = queue.pop(0)
        if depth > max_depth:
            continue
        for entry in _safe_scandir(current)[:200]:
            if len(result) >= max_files:
                break
            p = Path(entry.path)
            if entry.is_dir(follow_symlinks=False) and not _dir_is_noise(p.name):
                queue.append((p, depth + 1))
            elif entry.is_file(follow_symlinks=False) and str(p) not in used:
                if _is_text(p) or _is_office_pdf(p):
                    result.append(p)
    return result


def _sample_area_evidence(session_id: str, area: dict[str, Any], budget: int) -> list[dict[str, Any]]:
    existing = store.list_evidence_items(area_id=area["area_id"])
    if existing:
        return existing

    path = Path(area["path"])
    entries = _safe_scandir(path)[:MAX_ENTRIES_PER_DIR]
    files = [Path(e.path) for e in entries if e.is_file(follow_symlinks=False)]

    collected: list[dict[str, Any]] = []
    used_paths: set[str] = set()

    def _add(p: Path, bucket: str, weight: float = 1.0) -> bool:
        if str(p) in used_paths or len(collected) >= budget:
            return False
        used_paths.add(str(p))
        text_head = None
        extract_error = None
        if _is_text(p):
            text_head = _read_head(p)
        elif _is_office_pdf(p):
            extract_error = "lazy_extract_pending"
        collected.append(store.add_evidence_item(
            session_id, area["area_id"], p, bucket,
            text_head=text_head, extract_error=extract_error, weight=weight,
        ))
        return True

    manifests = [f for f in files if f.name in MARKER_FILE_NAMES]
    for f in manifests[:2]:
        _add(f, "manifest", 2.5)

    readmes = [f for f in files if f.name.lower().startswith("readme") or f.name.lower() == "index.md"]
    for f in readmes[:2]:
        _add(f, "readme_or_index", 2.0)

    office_pdfs = [f for f in files if _is_office_pdf(f)]
    for f in office_pdfs[:3]:
        _add(f, "office_pdf", 1.5)

    entrypoints = [f for f in files if f.name in ENTRYPOINT_NAMES]
    for f in entrypoints[:2]:
        _add(f, "entrypoint_like", 1.8)

    by_mtime = sorted([f for f in files if _is_text(f)], key=_stat_mtime, reverse=True)
    for f in by_mtime[:2]:
        _add(f, "recent", 1.2)

    # fix3: deep_representative actually recurses into subdirectories
    deep_files = _collect_deep_files(path, used_paths)
    for f in deep_files:
        _add(f, "deep_representative", 1.0)

    remaining = [f for f in files if str(f) not in used_paths]
    exts_seen: set[str] = {f.suffix.lower() for f in files if str(f) in used_paths}
    for f in remaining:
        if len(collected) >= budget:
            break
        ext = f.suffix.lower()
        if ext not in exts_seen:
            exts_seen.add(ext)
            _add(f, "diversity", 0.8)

    if collected:
        store.update_area_status(area["area_id"], "profiled")

    return collected


def expand_area_evidence(session_id: str, area_id: str, budget: str = "normal") -> dict[str, Any]:
    area = store.get_area(area_id)
    if not area or area["session_id"] != session_id:
        return {"ok": False, "error": "area not found in session"}

    evidence = store.list_evidence_items(area_id=area_id)

    for ev in evidence:
        if ev.get("extract_error") == "lazy_extract_pending" and _is_office_pdf(Path(ev["path"])):
            head = _try_office_extract(Path(ev["path"]))
            if head:
                with store.connect() as conn:
                    conn.execute("UPDATE evidence_items SET text_head=?, extract_error=NULL WHERE evidence_id=?",
                                 (head, ev["evidence_id"]))
                ev["text_head"] = head
                ev["extract_error"] = None
            else:
                with store.connect() as conn:
                    conn.execute("UPDATE evidence_items SET extract_error=? WHERE evidence_id=?",
                                 ("extract_failed", ev["evidence_id"]))
                ev["extract_error"] = "extract_failed"

    return {"ok": True, "area": area, "evidence_items": evidence, "evidence_count": len(evidence)}


# fix4: real Office/PDF extraction
def _try_office_extract(path: Path) -> str | None:
    """Extract text from Office/PDF files using real parsers, not raw byte decode."""
    suffix = path.suffix.lower()
    try:
        if suffix == ".docx":
            return _extract_docx(path)
        if suffix in (".pptx", ".xlsx"):
            return _extract_ooxml_shared_strings(path)
        if suffix == ".pdf":
            return _extract_pdf_best_effort(path)
        if suffix in (".doc", ".ppt", ".xls"):
            return _extract_legacy_office_strings(path)
    except Exception:
        pass
    return None


def _extract_docx(path: Path) -> str | None:
    """Extract text from docx by parsing word/document.xml inside the zip."""
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


def _extract_ooxml_shared_strings(path: Path) -> str | None:
    """Extract text from pptx/xlsx by scanning all xml parts for text nodes."""
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


def _extract_pdf_best_effort(path: Path) -> str | None:
    """Try pdfplumber, fall back to regex on raw bytes."""
    try:
        import pdfplumber  # type: ignore
        with pdfplumber.open(path) as pdf:
            texts = []
            for page in pdf.pages[:5]:
                t = page.extract_text()
                if t:
                    texts.append(t)
            content = "\n".join(texts).strip()
            return content[:DEFAULT_TEXT_HEAD_CHARS] if len(content) > 30 else None
    except Exception:
        pass
    # Fallback: extract printable ASCII/UTF-8 strings from raw bytes
    try:
        data = path.read_bytes()[:32000]
        strings = re.findall(rb"[\x20-\x7e\xc0-\xff]{8,}", data)
        if strings:
            text = b" ".join(strings[:200]).decode("utf-8", errors="replace")
            return text[:DEFAULT_TEXT_HEAD_CHARS] if len(text) > 50 else None
    except OSError:
        pass
    return None


def _extract_legacy_office_strings(path: Path) -> str | None:
    """Best-effort extraction from legacy .doc/.xls/.ppt via printable strings."""
    try:
        data = path.read_bytes()[:32000]
        # Extract runs of printable chars (legacy Office embeds text in binary)
        strings = re.findall(rb"[\x20-\x7e]{10,}", data)
        unicode_strings = re.findall(rb"(?:[\x20-\x7e]\x00){5,}", data)
        texts = [s.decode("ascii", errors="replace") for s in strings[:100]]
        texts.extend(s.decode("utf-16-le", errors="replace") for s in unicode_strings[:50])
        content = " ".join(texts).strip()
        return content[:DEFAULT_TEXT_HEAD_CHARS] if len(content) > 50 else None
    except OSError:
        return None


def next_seed_packet(session_id: str, packet_size: int = DEFAULT_PACKET_SIZE) -> dict[str, Any]:
    """Return next batch of evidence items for LLM annotation."""
    session = store.get_seed_session(session_id)
    if not session:
        return {"ok": False, "error": "session not found"}

    all_evidence = store.list_evidence_items(session_id=session_id, limit=5000)
    annotated_ev_ids = {a["evidence_id"] for a in store.list_annotations(session_id=session_id) if a.get("evidence_id")}

    unannotated = [ev for ev in all_evidence if ev["evidence_id"] not in annotated_ev_ids]
    unannotated.sort(key=lambda e: -e["weight"])
    batch = unannotated[:packet_size]

    if not batch:
        return {"ok": True, "session_id": session_id, "packet": [], "remaining": 0,
                "message": "all evidence annotated"}

    packet = []
    for ev in batch:
        area = store.get_area(ev["area_id"])
        packet.append({
            "evidence_id": ev["evidence_id"],
            "area_id": ev["area_id"],
            "area_path": area["path"] if area else None,
            "path": ev["path"],
            "bucket": ev["bucket"],
            "text_head": ev.get("text_head"),
            "extract_error": ev.get("extract_error"),
            "weight": ev["weight"],
        })

    return {
        "ok": True,
        "session_id": session_id,
        "packet": packet,
        "packet_size": len(packet),
        "remaining": len(unannotated) - len(batch),
        "annotation_schema": file_annotation_schema(),
    }


# ---------------------------------------------------------------------------
# Phase 4: File-Level Annotation  (fix5)
# ---------------------------------------------------------------------------

def file_annotation_schema() -> dict[str, Any]:
    return {
        "fields": {
            "evidence_id": "str, required - id from seed packet (aliases: file_id, candidate_id)",
            "decision": "str, required - one of: annotate, needs_more_evidence, defer, ignore_noise (alias: action)",
            "tags": "list[str], required if decision=annotate - semantic tags (no generic/path-only)",
            "value_reason": "str, required if decision=annotate - why this file matters",
            "evidence_summary": "str, required if decision=annotate - summary of what the evidence shows",
            "confidence": "float 0-1, required if decision=annotate",
        },
        "decisions": {
            "annotate": "evidence is sufficient, file has clear semantic value",
            "needs_more_evidence": "cannot judge; more context needed",
            "defer": "may have value but cannot annotate now",
            "ignore_noise": "confirmed tech noise / duplicate / auto-generated / out-of-scope",
        },
        "tag_rules": "no path-only tags, no generic tags (project/document/research/code/file/folder/misc/general)",
    }


def apply_file_annotations(session_id: str, annotations: list[dict[str, Any]]) -> dict[str, Any]:
    session = store.get_seed_session(session_id)
    if not session:
        return {"ok": False, "error": "session not found"}

    results: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []

    for raw in annotations:
        evidence_id = raw.get("evidence_id") or raw.get("file_id") or raw.get("candidate_id")
        decision = raw.get("decision") or raw.get("action")

        if not evidence_id:
            errors.append({"error": "missing evidence_id", "raw": raw})
            continue
        if decision not in ANNOTATION_DECISIONS:
            errors.append({"error": f"invalid decision: {decision}", "evidence_id": evidence_id})
            continue

        ev = store.get_evidence_item(evidence_id)
        if not ev:
            errors.append({"error": "evidence not found", "evidence_id": evidence_id})
            continue

        # fix5a: verify evidence belongs to current session
        if ev.get("session_id") != session_id:
            errors.append({"error": "evidence does not belong to this session", "evidence_id": evidence_id})
            continue

        tags = raw.get("tags") or []
        if decision == "annotate":
            if not tags:
                errors.append({"error": "annotate requires tags", "evidence_id": evidence_id})
                continue
            bad_generic = [t for t in tags if t.lower() in GENERIC_TAGS]
            if bad_generic:
                errors.append({"error": f"generic tags not allowed: {bad_generic}", "evidence_id": evidence_id})
                continue
            # fix5c: reject path-only tags
            bad_path = [t for t in tags if _looks_path_only(t)]
            if bad_path:
                errors.append({"error": f"path-only tags not allowed: {bad_path}", "evidence_id": evidence_id})
                continue
            if not raw.get("value_reason"):
                errors.append({"error": "annotate requires value_reason", "evidence_id": evidence_id})
                continue
            # fix5b: require evidence_summary
            if not raw.get("evidence_summary"):
                errors.append({"error": "annotate requires evidence_summary", "evidence_id": evidence_id})
                continue

        ann = store.create_annotation(
            session_id=session_id,
            evidence_id=evidence_id,
            area_id=ev["area_id"],
            path=ev["path"],
            decision=decision,
            tags=[t.strip() for t in tags if t.strip()] if decision == "annotate" else [],
            value_reason=raw.get("value_reason"),
            evidence_summary=raw.get("evidence_summary"),
            confidence=float(raw.get("confidence", 0.5)) if decision == "annotate" else 0.0,
        )
        results.append(ann)

        if decision == "needs_more_evidence":
            store.update_area_status(ev["area_id"], "needs_more_evidence")
        elif decision == "ignore_noise":
            pass
        elif decision == "annotate":
            area = store.get_area(ev["area_id"])
            if area and area["status"] not in ("covered",):
                store.update_area_status(ev["area_id"], "covered")

    return {
        "ok": len(errors) == 0,
        "session_id": session_id,
        "applied": len(results),
        "errors": errors,
        "annotations": results[:50],
    }


def list_file_annotations(
    session_id: str | None = None,
    status: str | None = None,
    area_id: str | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    decision = status
    anns = store.list_annotations(session_id=session_id, area_id=area_id, decision=decision, limit=limit)
    return {"ok": True, "annotations": anns, "count": len(anns)}


def search_file_annotations(query: str, scope: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
    """Search annotations by tag/value_reason/path matching. Runtime-queryable."""
    all_anns = store.list_annotations(decision="annotate", limit=5000)
    scored: list[tuple[float, dict[str, Any]]] = []

    query_tokens = _query_tokens(query)
    query_lc = query.lower()

    for ann in all_anns:
        if scope and not _path_under(ann["path"], scope):
            continue
        score = _score_annotation(ann, query_tokens, query_lc)
        if score > 0:
            scored.append((score, ann))

    scored.sort(key=lambda x: -x[0])
    return [item for _, item in scored[:limit]]


def _query_tokens(text: str) -> set[str]:
    lowered = text.lower()
    tokens = set(re.findall(r"[a-z0-9][a-z0-9_-]{1,}", lowered))
    tokens.update(re.findall(r"[\u4e00-\u9fff]{2,}", lowered))
    generic = {"file", "files", "folder", "folders", "project", "projects",
               "document", "documents", "misc", "general", "code", "data",
               "文件", "目录", "文件夹", "项目", "文档", "资料", "所有", "本机"}
    return tokens - generic


def _score_annotation(ann: dict[str, Any], query_tokens: set[str], query_lc: str) -> float:
    text_parts = [
        " ".join(ann.get("tags") or []),
        ann.get("value_reason") or "",
        ann.get("evidence_summary") or "",
        Path(ann["path"]).name,
    ]
    ann_text = " ".join(text_parts).lower()
    ann_tokens = set(re.findall(r"[a-z0-9][a-z0-9_-]{1,}", ann_text))
    ann_tokens.update(re.findall(r"[\u4e00-\u9fff]{2,}", ann_text))

    real_matches = 0
    score = 0.0

    overlap = query_tokens & ann_tokens
    if overlap:
        real_matches += len(overlap)
        score += len(overlap) * 3

    for tag in ann.get("tags") or []:
        tag_lc = tag.lower()
        if len(tag_lc) >= 2 and tag_lc in query_lc:
            real_matches += 1
            score += 4

    cn_cues = re.findall(r"[\u4e00-\u9fff]{2,}", query_lc)
    for cue in cn_cues:
        if len(cue) >= 2 and cue in ann_text:
            real_matches += 1
            score += 3

    if real_matches == 0:
        return 0.0

    score += float(ann.get("confidence") or 0) * 0.5
    score += min(float(ann.get("use_count") or 0) * 0.3, 2.0)

    return score


def _path_under(path: str, scope: str) -> bool:
    try:
        return Path(path).resolve().as_posix().lower().startswith(
            Path(scope).resolve().as_posix().lower()
        )
    except (OSError, ValueError):
        return path.lower().startswith(scope.lower())


# ---------------------------------------------------------------------------
# Phase 5: Route Lift And Overview  (fix6 + fix7)
# ---------------------------------------------------------------------------

def _tag_overlap(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _cluster_annotations_by_tags(anns: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Sub-cluster annotations within an area by tag similarity (Jaccard >= 0.2)."""
    if len(anns) <= 3:
        return [anns]

    clusters: list[list[dict[str, Any]]] = []
    assigned: set[str] = set()

    for ann in anns:
        if ann["annotation_id"] in assigned:
            continue
        cluster = [ann]
        assigned.add(ann["annotation_id"])
        ann_tags = {t.lower() for t in ann.get("tags") or []}
        for other in anns:
            if other["annotation_id"] in assigned:
                continue
            other_tags = {t.lower() for t in other.get("tags") or []}
            if _tag_overlap(ann_tags, other_tags) >= 0.2:
                cluster.append(other)
                assigned.add(other["annotation_id"])
        clusters.append(cluster)

    return clusters


def propose_route_nodes(session_id: str, limit: int | None = None) -> dict[str, Any]:
    """Generate route proposals from annotated evidence. Does NOT create routes."""
    session = store.get_seed_session(session_id)
    if not session:
        return {"ok": False, "error": "session not found"}

    budget = limit or session["route_budget"]
    annotations = store.list_annotations(session_id=session_id, decision="annotate")
    if not annotations:
        return {"ok": True, "session_id": session_id, "proposals": [],
                "message": "no annotations to propose routes from"}

    by_area: dict[str, list[dict[str, Any]]] = {}
    for ann in annotations:
        aid = ann.get("area_id") or "unknown"
        by_area.setdefault(aid, []).append(ann)

    proposals: list[dict[str, Any]] = []
    for area_id, area_anns in by_area.items():
        if len(proposals) >= budget:
            break
        area = store.get_area(area_id)
        if not area:
            continue

        # fix6: sub-cluster within area by tag similarity
        clusters = _cluster_annotations_by_tags(area_anns)
        for cluster in clusters:
            if len(proposals) >= budget:
                break
            merged = _merge_tags(cluster)
            suffix = f" ({merged[0]})" if merged else ""
            prop = store.create_proposal(
                session_id=session_id,
                title=f"[proposal] {Path(area['path']).name}{suffix}",
                brief=f"Route proposal from {len(cluster)} annotations in {area['path']}",
                supporting_annotation_ids=[a["annotation_id"] for a in cluster],
                anchor_path=area["path"],
                tags=merged,
            )
            proposals.append(prop)

    return {
        "ok": True,
        "session_id": session_id,
        "proposals": proposals,
        "proposal_count": len(proposals),
        "route_card_schema": route_card_schema(),
    }


def _merge_tags(annotations: list[dict[str, Any]]) -> list[str]:
    tags: dict[str, int] = {}
    for ann in annotations:
        for t in ann.get("tags") or []:
            t_clean = t.strip().lower()
            if t_clean and t_clean not in GENERIC_TAGS:
                tags[t_clean] = tags.get(t_clean, 0) + 1
    return sorted(tags, key=lambda t: -tags[t])[:10]


def route_card_schema() -> dict[str, Any]:
    return {
        "required_fields": {
            "title": "str - semantic label, NOT a path",
            "brief": "str - what this route lets the Agent do",
            "use_when": "str - when to use this route",
            "anchor_path": "str - root directory",
            "entrypoints": "list[str] - key file paths (must be under or near anchor_path)",
            "supporting_annotation_ids": "list[str] - annotation ids backing this route",
            "tags": "list[str] - must be subset of tags from supporting annotations",
        },
        "optional_fields": {
            "route_terms": "list[str] - search cue terms",
            "route_meta": "dict - positive_cues, negative_cues, boundary_note, etc.",
            "confidence": "float 0-1",
        },
        "rules": [
            "anchor must not be a tech noise directory",
            "tags must come from supporting annotation tags, not invented",
            "entrypoints must be under anchor_path or under evidence paths",
            "route_budget is an upper limit, not a target",
            "low-confidence routes go to candidate/deferred, not active",
        ],
    }


def apply_route_cards(session_id: str, route_cards: list[dict[str, Any]]) -> dict[str, Any]:
    session = store.get_seed_session(session_id)
    if not session:
        return {"ok": False, "error": "session not found"}

    budget = session["route_budget"]
    existing = store.list_routes(status="active")
    active_count = len(existing)

    results: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []

    for card in route_cards:
        if active_count + len(results) >= budget:
            errors.append({"error": "route_budget reached", "title": card.get("title")})
            continue

        title = (card.get("title") or "").strip()
        brief = (card.get("brief") or "").strip()
        anchor = card.get("anchor_path")
        ann_ids = card.get("supporting_annotation_ids") or []
        entrypoints = card.get("entrypoints") or []
        tags = card.get("tags") or []
        use_when = card.get("use_when") or ""

        if not title or not brief:
            errors.append({"error": "title and brief required", "card": card})
            continue
        if "\\" in title or "/" in title or ":" in title:
            errors.append({"error": "title must be semantic label, not path", "title": title})
            continue
        if not ann_ids:
            errors.append({"error": "supporting_annotation_ids required", "title": title})
            continue
        if not entrypoints:
            errors.append({"error": "entrypoints required", "title": title})
            continue

        valid_anns = [store.get_annotation(aid) for aid in ann_ids]
        valid_anns = [a for a in valid_anns if a and a.get("decision") == "annotate"]
        if not valid_anns:
            errors.append({"error": "no valid supporting annotations", "title": title})
            continue

        # fix7a: verify route tags are subset of annotation evidence tags
        ann_tag_pool = set()
        for a in valid_anns:
            ann_tag_pool.update(t.lower() for t in (a.get("tags") or []))
        unsupported_tags = [t for t in tags if t.lower() not in ann_tag_pool and t.lower() not in GENERIC_TAGS]
        if unsupported_tags:
            errors.append({"error": f"tags not found in supporting annotations: {unsupported_tags}", "title": title})
            continue

        bad_tags = [t for t in tags if t.lower() in GENERIC_TAGS]
        if bad_tags:
            errors.append({"error": f"generic tags not allowed: {bad_tags}", "title": title})
            continue

        if anchor and _dir_is_noise(Path(anchor).name):
            errors.append({"error": "anchor cannot be tech noise directory", "title": title, "anchor": anchor})
            continue

        # fix7b: verify entrypoints are under anchor or under evidence paths
        if anchor:
            ann_paths = {store.normalize_path(a["path"]) for a in valid_anns if a.get("path")}
            anchor_norm = store.normalize_path(anchor)
            bad_eps = []
            for ep in entrypoints:
                ep_norm = store.normalize_path(ep)
                under_anchor = _path_under(ep_norm, anchor_norm)
                near_evidence = any(_path_under(ep_norm, str(Path(p).parent)) for p in ann_paths)
                if not under_anchor and not near_evidence:
                    bad_eps.append(ep)
            if bad_eps:
                errors.append({"error": f"entrypoints not under anchor or evidence: {bad_eps}", "title": title})
                continue

        confidence = float(card.get("confidence", 0.6))
        tier = "warm"
        status = "active"
        if confidence < 0.4:
            status = "candidate"
        if confidence < 0.25:
            status = "deferred"

        route = store.create_route(
            title=title,
            brief=brief,
            use_when=use_when,
            anchor_path=anchor,
            entrypoints=[store.normalize_path(e) for e in entrypoints],
            supporting_annotation_ids=[a["annotation_id"] for a in valid_anns],
            tags=[t.strip() for t in tags if t.strip()],
            route_terms=card.get("route_terms") or [],
            route_meta=card.get("route_meta") or {},
            tier=tier,
            status=status,
            usage_verification="seeded",
            confidence=confidence,
            source=f"seed_map:{session_id}",
        )
        results.append(route)

        for prop in store.list_proposals(session_id=session_id, status="proposed"):
            prop_anchor = prop.get("anchor_path") or ""
            if anchor and prop_anchor and store.normalize_path(prop_anchor) == store.normalize_path(anchor):
                store.update_proposal_status(prop["proposal_id"], "accepted")

    return {
        "ok": len(errors) == 0,
        "session_id": session_id,
        "created": len(results),
        "routes": results,
        "errors": errors,
    }


def list_route_proposals(session_id: str, status: str | None = None) -> dict[str, Any]:
    proposals = store.list_proposals(session_id=session_id, status=status)
    return {"ok": True, "proposals": proposals, "count": len(proposals)}


# fix1: runtime coverage uses structural completeness, not search("test")
def _annotation_runtime_ready(ann: dict[str, Any]) -> bool:
    """An annotation is runtime-queryable if it has tags + value_reason + evidence_summary."""
    return bool(
        ann.get("decision") == "annotate"
        and ann.get("tags")
        and ann.get("value_reason")
        and ann.get("evidence_summary")
    )


def finish_seed_map(session_id: str) -> dict[str, Any]:
    """Hard validator. Returns success/incomplete/failed with detailed metrics."""
    session = store.get_seed_session(session_id)
    if not session:
        return {"ok": False, "status": "failed", "success": False, "error": "session not found"}

    areas = store.list_areas(session_id)
    annotations = store.list_annotations(session_id=session_id)
    evidence = store.list_evidence_items(session_id=session_id)
    routes = store.list_routes(status="active")
    candidate_routes = store.list_routes(status="candidate")
    deferred_routes = store.list_routes(status="deferred")

    annotate_anns = [a for a in annotations if a["decision"] == "annotate"]
    non_noise_areas = [a for a in areas if a["status"] not in ("ignored_noise", "out_of_scope")]
    covered_areas = [a for a in areas if a["status"] == "covered"]
    deferred_areas = [a for a in areas if a["status"] in ("deferred", "needs_more_evidence")]

    area_state_coverage = len(covered_areas) / max(1, len(non_noise_areas))
    high_value_ev = [e for e in evidence if e["weight"] >= 1.5]
    annotated_ev_ids = {a["evidence_id"] for a in annotate_anns if a.get("evidence_id")}
    covered_hv = [e for e in high_value_ev if e["evidence_id"] in annotated_ev_ids]
    hv_coverage = len(covered_hv) / max(1, len(high_value_ev))

    # fix1: structural completeness instead of search("test")
    queryable_anns = [a for a in annotate_anns if _annotation_runtime_ready(a)]
    runtime_coverage = len(queryable_anns) / max(1, len(annotate_anns))

    route_quality_failures: list[dict[str, Any]] = []
    for r in routes:
        fails: list[str] = []
        if not r.get("supporting_annotation_ids"):
            fails.append("missing supporting_annotation_ids")
        if not r.get("entrypoints"):
            fails.append("missing entrypoints")
        if not r.get("anchor_path"):
            fails.append("missing anchor_path")
        if r.get("anchor_path") and _dir_is_noise(Path(r["anchor_path"]).name):
            fails.append("anchor is tech noise")
        if fails:
            route_quality_failures.append({"route_id": r["route_id"], "title": r["title"], "failures": fails})

    active_route_quality = 1.0 - (len(route_quality_failures) / max(1, len(routes))) if routes else 0.0

    runtime_readiness_failures: list[str] = []
    non_queryable = len(annotate_anns) - len(queryable_anns)
    if non_queryable > 0:
        runtime_readiness_failures.append(f"{non_queryable} annotations missing tags/value_reason/evidence_summary")
    for r in routes:
        if not r.get("supporting_annotation_ids"):
            runtime_readiness_failures.append(f"route {r['route_id']} has no annotation backing")

    next_actions: list[str] = []
    uncovered_hv = [e for e in high_value_ev if e["evidence_id"] not in annotated_ev_ids]

    if area_state_coverage < 0.5:
        next_actions.append("annotate more areas — coverage below 50%")
    if hv_coverage < 0.5:
        next_actions.append(f"annotate {len(uncovered_hv)} uncovered high-value evidence items")
    if route_quality_failures:
        next_actions.append(f"fix {len(route_quality_failures)} route quality issues")
    if runtime_readiness_failures:
        next_actions.append("resolve runtime readiness failures")
    if deferred_areas:
        next_actions.append(f"revisit {len(deferred_areas)} deferred/needs_more_evidence areas")

    if route_quality_failures or runtime_readiness_failures:
        status = "incomplete"
    elif not routes and not annotate_anns:
        status = "failed"
    elif area_state_coverage >= 0.6 and hv_coverage >= 0.5 and active_route_quality >= 0.8:
        status = "success"
    else:
        status = "incomplete"

    report = {
        "status": status,
        "success": status == "success",
        "metrics": {
            "area_state_coverage": round(area_state_coverage, 3),
            "high_value_evidence_coverage": round(hv_coverage, 3),
            "runtime_asset_coverage": round(runtime_coverage, 3),
            "active_route_quality": round(active_route_quality, 3),
        },
        "covered_areas": [{"area_id": a["area_id"], "path": a["path"]} for a in covered_areas],
        "deferred_areas": [{"area_id": a["area_id"], "path": a["path"], "status": a["status"]} for a in deferred_areas],
        "uncovered_high_value_evidence": [{"evidence_id": e["evidence_id"], "path": e["path"]} for e in uncovered_hv[:20]],
        "active_routes": [{"route_id": r["route_id"], "title": r["title"]} for r in routes],
        "candidate_routes": [{"route_id": r["route_id"], "title": r["title"]} for r in candidate_routes],
        "route_quality_failures": route_quality_failures,
        "runtime_readiness_failures": runtime_readiness_failures,
        "next_required_actions": next_actions,
    }

    store.update_seed_session(session_id, status=status, report=report)
    return report


def seed_map_report(session_id: str) -> dict[str, Any]:
    """Read-only report without committing status."""
    session = store.get_seed_session(session_id)
    if not session:
        return {"ok": False, "error": "session not found"}
    return _compute_report(session_id)


def _compute_report(session_id: str) -> dict[str, Any]:
    areas = store.list_areas(session_id)
    annotations = store.list_annotations(session_id=session_id)
    evidence = store.list_evidence_items(session_id=session_id)
    routes = store.list_routes(status="active")

    annotate_anns = [a for a in annotations if a["decision"] == "annotate"]
    non_noise = [a for a in areas if a["status"] not in ("ignored_noise", "out_of_scope")]
    covered = [a for a in areas if a["status"] == "covered"]

    return {
        "ok": True,
        "session_id": session_id,
        "area_count": len(areas),
        "non_noise_areas": len(non_noise),
        "covered_areas": len(covered),
        "annotation_count": len(annotations),
        "annotated_count": len(annotate_anns),
        "evidence_count": len(evidence),
        "route_count": len(routes),
    }
