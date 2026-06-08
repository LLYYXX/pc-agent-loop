from __future__ import annotations
import json, os, re
from pathlib import Path
from typing import Any
from .search import flat_parents_too_large, iter_column_rows, resolve_lnk, search_rows
PKG = Path(__file__).resolve().parent
CONFIG = json.loads((PKG / "config.json").read_text(encoding="utf-8"))
IGNORE_DIRS_LOWER = frozenset(x.lower() for x in CONFIG.get("ignore_dirs", ()))
TEXT_EXT = frozenset(CONFIG.get("text_ext", ()))
DOCUMENT_EXT = frozenset(CONFIG.get("document_ext", ()))
BINARY_EXT = frozenset(CONFIG.get("binary_ext", ()))
MARKER_NAMES_LOWER = frozenset(x.lower() for x in CONFIG.get("marker_names", ()))
NOISE_RE = re.compile("|".join(CONFIG.get("noise_patterns") or [r"$^"]), re.I)
DISCOVERY_BUCKETS = tuple(CONFIG.get("discovery_buckets", ()))
MECHANICAL_LIMIT = int(CONFIG.get("mechanical_candidate_limit", 1000))
SIZE_CAP_BYTES = int(CONFIG.get("size_cap_bytes", 5_000_000))
FLAT_DIR_FILE_LIMIT = int(CONFIG.get("flat_dir_file_limit", 1000))
VALUE_SIG = {"windows_recent", "recent_modified", "recent_created", "long_maintained"}
def norm_path(path: str | os.PathLike[str]) -> str:
    return os.path.normpath(os.path.abspath(str(path).strip().strip('"').strip("'")))
def _kind(path: str, size_cap: int) -> str | None:
    p = Path(path)
    try:
        if not p.is_file() or any(x.lower() in IGNORE_DIRS_LOWER for x in p.parts) or NOISE_RE.search(p.name): return None
        large = p.stat().st_size > size_cap
    except (PermissionError, OSError):
        return None
    if p.suffix.lower() in DOCUMENT_EXT: return "metadata_only" if large else "document"
    if large or p.suffix.lower() in BINARY_EXT: return "filename_only"
    return "content"
def _priority(path: str, signals: list[str], kind: str) -> tuple[int, str]:
    p, name = Path(path), Path(path).name.lower()
    if "seed" in signals: return 0, "seed"
    if name in MARKER_NAMES_LOWER or name.startswith("readme") or "project_marker" in signals: return 1, "project_marker"
    if "docs" in signals: return 2, "docs"
    if any(s in VALUE_SIG for s in signals): return 3, ",".join(s for s in signals if s not in ("recent", "maintained"))
    if any(s.startswith("recent_") for s in signals): return 4, ",".join(s for s in signals if s.startswith("recent_"))
    if p.suffix.lower() in TEXT_EXT | DOCUMENT_EXT: return 5, "readable_candidate"
    return (6 if kind == "filename_only" else 5), kind
def _merge(rows: dict[str, set[str]], paths: list[str], *signals: str) -> None:
    for path in paths:
        if path: rows.setdefault(norm_path(path), set()).update(x for x in signals if x)
def _spec(x: Any, bucket: str) -> tuple[str, str]:
    return (str(x.get("signal") or bucket), str(x.get("query") or "")) if isinstance(x, dict) else (bucket, str(x))
def _under(path: str, scope: str | None) -> bool:
    p, root = norm_path(path), norm_path(scope) if scope else ""
    return not scope or p == root or p.startswith(root.rstrip(os.sep) + os.sep)
def _windows_recent(scope: str | None, limit: int) -> list[str]:
    base = os.environ.get("APPDATA")
    recent = os.path.join(base, "Microsoft", "Windows", "Recent") if base else ""
    links = search_rows("ext:lnk", scope=recent, limit=limit, with_info=False) if recent else []
    return [p for p in resolve_lnk([r["path"] for r in links]) if _under(p, scope)]
def _maintained(scope: str | None, limit: int, query: str) -> list[str]:
    m = re.search(r"maintained:(\d+)d", query, re.I); days = int(m.group(1)) if m else 180; out = []
    for r in iter_column_rows("*", scope=scope, columns=[], limit=None, sort="date-modified-descending", files_only=True):
        try: st = os.stat(r["path"])
        except (KeyError, OSError): continue
        if st.st_mtime - st.st_ctime >= days * 86400: out.append(r["path"])
        if len(out) >= limit: break
    return out
def _discover(q: str, scope: str | None, per: int) -> list[str]:
    if q == "windows_recent:": return _windows_recent(scope, per)
    if q.startswith("maintained:"): return _maintained(scope, per, q)
    sort = "date-created-descending" if q.startswith("dc:") else "date-modified-descending"
    return [r["path"] for r in search_rows(q, scope=scope, limit=per, with_info=False, files_only=True, sort=sort)]
def discover_paths(scope: str | None = None, *, query: str = "", limit: int = MECHANICAL_LIMIT) -> list[dict[str, Any]]:
    rows: dict[str, set[str]] = {}
    if query: _merge(rows, [r["path"] for r in search_rows(query, scope=scope, limit=min(200, limit), with_info=False, files_only=True)], "task_query")
    for b in DISCOVERY_BUCKETS:
        name, cap = str(b.get("name") or "bucket"), int(b.get("limit", limit)); qs = tuple(b.get("queries") or ())
        per = max(1, min(cap, limit) // max(1, len(qs)))
        for raw in qs:
            signal, q = _spec(raw, name); _merge(rows, _discover(q, scope, per), name, signal)
    return [{"path": p, "signals": sorted(sig)} for p, sig in rows.items()]
def select_for_read(paths: list[str], *, size_cap: int = SIZE_CAP_BYTES, limit: int | None = MECHANICAL_LIMIT, seeds: list[str] | None = None, flat_degrade: bool = False,
                    source_signals: dict[str, list[str]] | None = None) -> list[dict[str, Any]]:
    signals: dict[str, set[str]] = {}; seen: set[str] = set(); cands: list[tuple[str, list[str], str]] = []; out: list[tuple[int, str, dict[str, Any]]] = []
    for path in seeds or []: signals.setdefault(norm_path(path), set()).add("seed")
    for path, ss in (source_signals or {}).items(): signals.setdefault(norm_path(path), set()).update(ss)
    for raw in [*(seeds or []), *paths]:
        path = norm_path(raw)
        if path in seen: continue
        sig = sorted(signals.get(path, set()))
        seen.add(path); kind = _kind(path, size_cap)
        if not kind: continue
        cands.append((path, sig, kind))
    flat = flat_parents_too_large([os.path.dirname(p) for p, sig, _ in cands if flat_degrade and "seed" not in sig], FLAT_DIR_FILE_LIMIT) if flat_degrade else {}
    for path, sig, kind in cands:
        if flat_degrade and "seed" not in sig:
            parent = os.path.dirname(path)
            if flat[parent]: continue
        pri, reason = _priority(path, sig, kind)
        out.append((pri, path, {"path": path, "name": os.path.basename(path), "reason": reason, "priority": pri, "signals": sig, "evidence_kind": kind}))
    out.sort(key=lambda x: (x[0], -os.path.getmtime(x[1]) if os.path.isfile(x[1]) else 0))
    return [x[2] for x in out] if limit is None else [x[2] for x in out[:limit]]
def discover_candidates(scope: str | None = None, *, query: str = "", seeds: list[str] | None = None, limit: int = MECHANICAL_LIMIT) -> list[dict[str, Any]]:
    found = discover_paths(scope, query=query, limit=limit)
    seeds = [p for p in seeds or [] if _under(p, scope)]
    return select_for_read([r["path"] for r in found], seeds=seeds, flat_degrade=True, source_signals={r["path"]: r["signals"] for r in found}, limit=None)
