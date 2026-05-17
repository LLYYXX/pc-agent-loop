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
# Phase 3: Representative Evidence Sampling
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

    for area in non_noise:
        if len(total_collected) >= total_budget:
            break
        collected = _sample_area_evidence(session_id, area, min(per_area, MAX_EVIDENCE_PER_AREA))
        total_collected.extend(collected)

    return {
        "ok": True,
        "session_id": session_id,
        "evidence_count": len(total_collected),
        "areas_sampled": len(non_noise),
        "evidence_items": total_collected[:100],
    }


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

    text_files = [f for f in files if _is_text(f)]
    for f in text_files[:2]:
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


def _try_office_extract(path: Path) -> str | None:
    """Best-effort text extraction from Office/PDF. Returns None on failure."""
    try:
        data = path.read_bytes()[:8000]
        for enc in ("utf-8", "utf-8-sig", "gbk", "latin-1"):
            try:
                text = data.decode(enc)
                printable = "".join(ch for ch in text if ch.isprintable() or ch in "\n\r\t")
                if len(printable) > 50:
                    return printable[:DEFAULT_TEXT_HEAD_CHARS]
            except UnicodeDecodeError:
                continue
    except OSError:
        pass
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
# Phase 4: File-Level Annotation
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

        tags = raw.get("tags") or []
        if decision == "annotate":
            if not tags:
                errors.append({"error": "annotate requires tags", "evidence_id": evidence_id})
                continue
            bad_tags = [t for t in tags if t.lower() in GENERIC_TAGS]
            if bad_tags:
                errors.append({"error": f"generic tags not allowed: {bad_tags}", "evidence_id": evidence_id})
                continue
            if not raw.get("value_reason"):
                errors.append({"error": "annotate requires value_reason", "evidence_id": evidence_id})
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
            pass  # area status stays; individual evidence is marked
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
    decision = status  # alias
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
# Phase 5: Route Lift And Overview
# ---------------------------------------------------------------------------

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
    for area_id, anns in by_area.items():
        if len(proposals) >= budget:
            break
        area = store.get_area(area_id)
        if not area:
            continue

        prop = store.create_proposal(
            session_id=session_id,
            title=f"[proposal] {Path(area['path']).name}",
            brief=f"Route proposal from {len(anns)} annotations in {area['path']}",
            supporting_annotation_ids=[a["annotation_id"] for a in anns],
            anchor_path=area["path"],
            tags=_merge_tags(anns),
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
            "entrypoints": "list[str] - key file paths",
            "supporting_annotation_ids": "list[str] - annotation ids backing this route",
            "tags": "list[str] - evidence-backed semantic tags (no generic tags)",
        },
        "optional_fields": {
            "route_terms": "list[str] - search cue terms",
            "route_meta": "dict - positive_cues, negative_cues, boundary_note, etc.",
            "confidence": "float 0-1",
        },
        "rules": [
            "anchor must not be a tech noise directory",
            "tags must come from annotation evidence, not inferred from paths",
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

        bad_tags = [t for t in tags if t.lower() in GENERIC_TAGS]
        if bad_tags:
            errors.append({"error": f"generic tags not allowed: {bad_tags}", "title": title})
            continue

        if anchor and _dir_is_noise(Path(anchor).name):
            errors.append({"error": "anchor cannot be tech noise directory", "title": title, "anchor": anchor})
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

    ann_queryable = len(search_file_annotations("test", limit=1000)) if annotate_anns else 0
    runtime_coverage = min(1.0, ann_queryable / max(1, len(annotate_anns)))

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
    if annotate_anns and ann_queryable == 0:
        runtime_readiness_failures.append("annotations exist but none queryable at runtime")
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
    return finish_seed_map.__wrapped__(session_id) if hasattr(finish_seed_map, "__wrapped__") else _compute_report(session_id)


def _compute_report(session_id: str) -> dict[str, Any]:
    """Same logic as finish_seed_map but doesn't persist status."""
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
