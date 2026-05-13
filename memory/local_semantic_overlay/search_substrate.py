"""Thin Everything/es search substrate for Local Semantic Overlay.

This module deliberately stays below semantics: it discovers es.exe, optionally
starts Everything, builds argv lists, handles Windows stdout encoding, and
returns structured diagnostics. It does not expand queries, rerank, or infer
tags from paths.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Sequence

from .config import GLOBAL_MEM_PATH

_SECTION = "LOCAL_TOOLS"
_SECTION_RE = re.compile(r"^##\s*\[([^\]]+)\]\s*$", re.I)
_RESOLVED_ES_CACHE: str | None = None
_LAST_SEARCH_DIAGNOSTIC: dict[str, Any] = {}


def _norm(path: str | os.PathLike[str]) -> str:
    return os.path.normpath(os.path.abspath(str(path).strip().strip('"').strip("'")))


def _read_global_mem() -> str:
    try:
        return GLOBAL_MEM_PATH.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _parse_local_tools(text: str | None = None) -> dict[str, str]:
    tools: dict[str, str] = {}
    raw = _read_global_mem() if text is None else text
    in_section = False
    for line in raw.splitlines():
        stripped = line.strip()
        header = _SECTION_RE.match(stripped)
        if header:
            in_section = header.group(1).strip().upper() == _SECTION
            continue
        if not in_section:
            continue
        if "=" in line:
            key, value = line.split("=", 1)
            tools[key.strip()] = value.strip().strip('"').strip("'")
    return tools


def _patch_local_tools(es_path: str, everything_path: str | None = None, raw: str | None = None) -> str:
    """Patch only ## [LOCAL_TOOLS] using line editing, not regex replacement."""

    text = _read_global_mem() if raw is None else raw
    lines = text.splitlines(keepends=True) or ["# Global memory L2\n"]
    out: list[str] = []
    in_section = False
    found_section = False
    wrote_es = False
    wrote_everything = False
    insert_at = -1

    es_line = f"es.exe={os.path.normpath(es_path)}\n"
    everything_line = f"Everything.exe={os.path.normpath(everything_path)}\n" if everything_path else None

    for line in lines:
        header = _SECTION_RE.match(line.strip())
        if header and header.group(1).strip().upper() == _SECTION:
            found_section = True
            in_section = True
            out.append(line)
            insert_at = len(out)
            continue
        if in_section and header and header.group(1).strip().upper() != _SECTION:
            if not wrote_es:
                out.insert(insert_at, es_line)
                insert_at += 1
            if everything_line and not wrote_everything:
                out.insert(insert_at, everything_line)
                insert_at += 1
            in_section = False
            out.append(line)
            continue
        if in_section:
            if re.match(r"^\s*es\.exe\s*=", line, re.I):
                out.append(es_line)
                wrote_es = True
            elif re.match(r"^\s*Everything\.exe\s*=", line, re.I):
                if everything_line:
                    out.append(everything_line)
                    wrote_everything = True
                else:
                    out.append(line)
            else:
                out.append(line)
        else:
            out.append(line)

    if in_section:
        if not wrote_es:
            out.insert(insert_at, es_line)
            insert_at += 1
        if everything_line and not wrote_everything:
            out.insert(insert_at, everything_line)
    if not found_section:
        body = "".join(out).rstrip()
        block = f"\n\n## [{_SECTION}]\n{es_line}"
        if everything_line:
            block += everything_line
        return (body + block).lstrip("\n")
    return "".join(out)


def _write_local_tools(es_path: str, everything_path: str | None = None) -> bool:
    before = _read_global_mem()
    after = _patch_local_tools(es_path, everything_path, before)
    if after == before:
        return False
    GLOBAL_MEM_PATH.parent.mkdir(parents=True, exist_ok=True)
    GLOBAL_MEM_PATH.write_text(after, encoding="utf-8", newline="\n")
    return True


def _everything_for_es(es_path: str | None) -> str | None:
    if not es_path:
        return None
    directory = os.path.dirname(os.path.abspath(es_path))
    for name in ("Everything.exe", "Everything64.exe", "Everything32.exe", "everything.exe"):
        candidate = os.path.join(directory, name)
        if os.path.isfile(candidate):
            return _norm(candidate)
    return None


def _discover_es_and_everything() -> tuple[str | None, str | None, str]:
    tools = _parse_local_tools()
    for key in ("EVERYTHING_ES_EXE", "GA_ES_EXE", "FILE_INDEX_ES_EXE"):
        value = (os.environ.get(key) or "").strip()
        if value and os.path.isfile(_norm(value)):
            es = _norm(value)
            return es, _everything_for_es(es), f"env:{key}"

    configured = tools.get("es.exe")
    if configured and os.path.isfile(_norm(configured)):
        es = _norm(configured)
        everything = tools.get("Everything.exe")
        ev = _norm(everything) if everything and os.path.isfile(_norm(everything)) else _everything_for_es(es)
        return es, ev, "LOCAL_TOOLS:es.exe"

    found = shutil.which("es.exe")
    if found and os.path.isfile(found):
        es = _norm(found)
        return es, _everything_for_es(es), "PATH"

    if sys.platform == "win32":
        try:
            import winreg  # type: ignore

            for hive_name, root in (("HKLM", winreg.HKEY_LOCAL_MACHINE), ("HKCU", winreg.HKEY_CURRENT_USER)):
                for subkey in (r"SOFTWARE\voidtools\Everything", r"SOFTWARE\WOW6432Node\voidtools\Everything"):
                    try:
                        with winreg.OpenKey(root, subkey) as handle:
                            base, _ = winreg.QueryValueEx(handle, "InstallLocation")
                        if isinstance(base, str) and base.strip():
                            es = os.path.join(base, "es.exe")
                            ev = os.path.join(base, "Everything.exe")
                            if os.path.isfile(es):
                                return _norm(es), _norm(ev) if os.path.isfile(ev) else None, f"registry:{hive_name}:{subkey}"
                    except OSError:
                        continue
        except Exception:
            pass

    common_roots = [
        os.environ.get("ProgramFiles") or "",
        os.environ.get("ProgramFiles(x86)") or "",
        os.environ.get("LocalAppData") or "",
        "D:/Everything",
        "C:/Program Files/Everything",
        "C:/Program Files (x86)/Everything",
    ]
    for root in common_roots:
        if not root:
            continue
        for subdir in ("", "Everything", os.path.join("voidtools", "Everything")):
            base = os.path.join(root, subdir) if subdir else root
            es = os.path.join(base, "es.exe")
            ev = os.path.join(base, "Everything.exe")
            if os.path.isfile(es):
                return _norm(es), _norm(ev) if os.path.isfile(ev) else None, "common-path"
    return None, None, "not-found"


def _stdout_encoding() -> str:
    configured = (os.environ.get("FILE_INDEX_ES_STDOUT_ENCODING") or os.environ.get("ES_STDOUT_ENCODING") or "").strip()
    if configured:
        return configured
    return "mbcs" if sys.platform == "win32" else "utf-8"


def _run_es(es_exe: str, argv_without_exe: Sequence[str], *, timeout: float = 120.0) -> subprocess.CompletedProcess[str]:
    kwargs: dict[str, Any] = {
        "args": [es_exe] + list(argv_without_exe),
        "capture_output": True,
        "text": True,
        "encoding": _stdout_encoding(),
        "errors": "replace",
        "timeout": float(max(5.0, min(timeout, 7200.0))),
    }
    if sys.platform == "win32":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return subprocess.run(**kwargs)


def _run_probe(es_path: str, timeout: float = 8.0) -> tuple[bool, str]:
    try:
        result = _run_es(es_path, ["-n", "1", "*"], timeout=timeout)
        if result.returncode == 0 or (result.stdout or "").strip():
            return True, "es.exe query OK"
        return False, (result.stderr or "").strip() or f"exit {result.returncode}"
    except Exception as exc:
        return False, str(exc)


def _start_everything(everything_path: str | None, es_path: str | None, wait_seconds: float = 10.0) -> tuple[bool, str]:
    executable = everything_path or _everything_for_es(es_path)
    if not executable or not os.path.isfile(executable):
        return False, "Everything.exe not found; configure it or start Everything manually"
    try:
        kwargs: dict[str, Any] = {"args": [executable, "-startup", "-minimized"], "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
        if sys.platform == "win32":
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        subprocess.Popen(**kwargs)
    except Exception as exc:
        return False, f"failed to start Everything: {exc}"
    if es_path:
        deadline = time.time() + wait_seconds
        last = ""
        while time.time() < deadline:
            ok, message = _run_probe(es_path, timeout=3.0)
            if ok:
                return True, "Everything started and es.exe IPC is ready"
            last = message
            time.sleep(0.7)
        return False, "Everything start attempted but es.exe IPC not ready: " + last
    return True, "Everything start attempted"


def resolve_es_exe() -> str | None:
    global _RESOLVED_ES_CACHE
    if _RESOLVED_ES_CACHE and os.path.isfile(_RESOLVED_ES_CACHE):
        return _RESOLVED_ES_CACHE
    es_path, _everything, _source = _discover_es_and_everything()
    if es_path:
        _RESOLVED_ES_CACHE = es_path
    return es_path


def ensure_search_ready(start: bool = True, patch: bool = True, dry_run: bool = False) -> dict[str, Any]:
    """Locate es/Everything and optionally start Everything."""

    es_path, everything_path, source = _discover_es_and_everything()
    if not es_path:
        return {
            "ok": False,
            "error": "could not locate es.exe",
            "source": source,
            "hint": "Install voidtools Everything or set EVERYTHING_ES_EXE.",
            "es_path": None,
            "es_exe": None,
            "everything_path": None,
            "everything_exe": None,
            "encoding": _stdout_encoding(),
            "global_mem": str(GLOBAL_MEM_PATH),
        }

    configured = _parse_local_tools().get("es.exe") == es_path
    would_write = bool(patch and not configured)
    wrote = False
    if would_write and not dry_run:
        wrote = _write_local_tools(es_path, everything_path)

    ok, probe = _run_probe(es_path, timeout=6.0)
    started = False
    start_message = ""
    if not ok and start:
        started, start_message = _start_everything(everything_path, es_path)
        ok, probe = _run_probe(es_path, timeout=6.0)

    return {
        "ok": bool(ok),
        "es_path": es_path,
        "es_exe": es_path,
        "everything_path": everything_path,
        "everything_exe": everything_path,
        "source": source,
        "configured": configured or wrote,
        "would_write": would_write,
        "wrote": wrote,
        "started": started,
        "start_message": start_message,
        "probe": probe,
        "encoding": _stdout_encoding(),
        "global_mem": str(GLOBAL_MEM_PATH),
        "error": None if ok else probe,
    }


def _scope_arg(scope: str) -> str:
    value = os.path.normpath(os.path.abspath(str(scope)))
    if len(value) == 2 and value[1] == ":":
        return value + "\\"
    return value


def _build_argv(query: str, *, limit: int = 50, scope: str | None = None, extra_args: Sequence[str] | None = None) -> list[str]:
    q = (query or "").strip()
    if not q:
        raise ValueError("Everything query is empty")
    capped = max(1, min(int(limit), 50000))
    argv = ["-n", str(capped)]
    if scope:
        argv.extend(["-path", _scope_arg(scope)])
    if extra_args:
        argv.extend(str(item) for item in extra_args if str(item).strip())
    argv.append(q)
    return argv


def _row_for_path(path_text: str) -> dict[str, Any]:
    normalized = os.path.normpath(path_text)
    path = Path(normalized)
    row: dict[str, Any] = {
        "path": normalized,
        "name": path.name,
        "basename": path.name,
        "parent": str(path.parent) if path.parent != path else "",
        "exists": path.exists(),
        "is_dir": path.is_dir(),
        "is_file": path.is_file(),
    }
    try:
        stat = path.stat()
        row["size"] = int(stat.st_size)
        row["mtime_ns"] = int(getattr(stat, "st_mtime_ns", int(stat.st_mtime * 1e9)))
    except OSError:
        pass
    return row


def last_search_diagnostic() -> dict[str, Any]:
    return dict(_LAST_SEARCH_DIAGNOSTIC)


def search_files_detailed(
    query: str,
    scope: str | None = None,
    limit: int = 50,
    *,
    timeout: float = 120.0,
    start: bool = True,
    patch: bool = True,
    extra_args: Sequence[str] | None = None,
    sort_mtime_desc: bool = False,
) -> dict[str, Any]:
    """Run es.exe as a raw-terrain search and return diagnostics."""

    global _LAST_SEARCH_DIAGNOSTIC
    ready = ensure_search_ready(start=start, patch=patch)
    es_path = ready.get("es_path") or ready.get("es_exe")
    if not es_path:
        result = {
            "ok": False,
            "query": query,
            "scope": scope,
            "argv": None,
            "es_exe": None,
            "encoding": ready.get("encoding"),
            "stderr": "",
            "returncode": None,
            "hit_count": 0,
            "paths": [],
            "rows": [],
            "hits": [],
            "ready": ready,
            "diagnostic": ready,
            "error": ready.get("error") or "es.exe not found",
        }
        _LAST_SEARCH_DIAGNOSTIC = {key: value for key, value in result.items() if key not in {"rows", "hits", "paths"}}
        return result

    try:
        argv_without_exe = _build_argv(query, limit=limit, scope=scope, extra_args=extra_args)
    except Exception as exc:
        result = {
            "ok": False,
            "query": query,
            "scope": scope,
            "argv": None,
            "es_exe": es_path,
            "encoding": ready.get("encoding"),
            "stderr": "",
            "returncode": None,
            "hit_count": 0,
            "paths": [],
            "rows": [],
            "hits": [],
            "ready": ready,
            "diagnostic": ready,
            "error": str(exc),
        }
        _LAST_SEARCH_DIAGNOSTIC = {key: value for key, value in result.items() if key not in {"rows", "hits", "paths"}}
        return result

    argv = [str(es_path)] + argv_without_exe
    try:
        completed = _run_es(str(es_path), argv_without_exe, timeout=timeout)
    except Exception as exc:
        result = {
            "ok": False,
            "query": query,
            "scope": scope,
            "argv": argv,
            "es_exe": es_path,
            "encoding": _stdout_encoding(),
            "stderr": str(exc),
            "returncode": None,
            "hit_count": 0,
            "paths": [],
            "rows": [],
            "hits": [],
            "ready": ready,
            "diagnostic": {"argv": argv, "stderr": str(exc), "encoding": _stdout_encoding()},
            "error": str(exc),
        }
        _LAST_SEARCH_DIAGNOSTIC = {key: value for key, value in result.items() if key not in {"rows", "hits", "paths"}}
        return result

    paths = [os.path.normpath(line.strip()) for line in (completed.stdout or "").splitlines() if line.strip()]
    rows = [_row_for_path(path) for path in paths]
    if sort_mtime_desc:
        rows.sort(key=lambda row: int(row.get("mtime_ns") or 0), reverse=True)
        paths = [row["path"] for row in rows]
    ok = completed.returncode == 0 or bool(rows)
    diagnostic = {
        "argv": argv,
        "scope": scope,
        "encoding": _stdout_encoding(),
        "stderr": completed.stderr or "",
        "returncode": completed.returncode,
        "es_exe": es_path,
        "ready": ready,
    }
    result = {
        "ok": ok,
        "query": query,
        "scope": scope,
        "argv": argv,
        "es_exe": es_path,
        "encoding": _stdout_encoding(),
        "stderr": completed.stderr or "",
        "returncode": completed.returncode,
        "hit_count": len(rows),
        "paths": paths,
        "rows": rows,
        "hits": rows,
        "ready": ready,
        "diagnostic": diagnostic,
        "error": None if ok else (completed.stderr or f"es.exe returned {completed.returncode}"),
    }
    _LAST_SEARCH_DIAGNOSTIC = diagnostic
    return result


def search_files_rows(query: str, scope: str | None = None, limit: int = 50, **kwargs: Any) -> list[dict[str, Any]]:
    """Everything thin wrapper with stable list[dict] return."""

    kwargs.pop("task_id", None)
    return list(search_files_detailed(query, scope=scope, limit=limit, **kwargs).get("rows") or [])


def search_files_paths(query: str, scope: str | None = None, limit: int = 50, **kwargs: Any) -> list[str]:
    """Everything thin wrapper with stable list[str] return."""

    kwargs.pop("task_id", None)
    return [str(row.get("path")) for row in search_files_rows(query, scope=scope, limit=limit, **kwargs) if row.get("path")]
