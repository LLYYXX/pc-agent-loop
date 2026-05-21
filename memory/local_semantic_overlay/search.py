"""Everything/es thin wrapper."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

_MEM = Path(__file__).resolve().parent.parent / "global_mem.txt"
_ES: str | None = None


def _ok(**kw: Any) -> dict[str, Any]:
    return {"ok": True, "error": None, "message": "", **kw}


def _err(code: str, msg: str, **kw: Any) -> dict[str, Any]:
    return {"ok": False, "error": code, "message": msg, **kw}


def _norm(p: str) -> str:
    return os.path.normpath(os.path.abspath(p.strip().strip('"')))


def _parse_tools() -> dict[str, str]:
    try:
        text = _MEM.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    tools: dict[str, str] = {}
    in_sec = False
    for line in text.splitlines():
        m = re.match(r"^##\s*\[([^\]]+)\]\s*$", line.strip(), re.I)
        if m:
            in_sec = m.group(1).strip().upper() == "LOCAL_TOOLS"
            continue
        if in_sec and "=" in line:
            k, v = line.split("=", 1)
            tools[k.strip()] = v.strip().strip('"')
    return tools


def _find_es() -> str | None:
    global _ES
    if _ES and os.path.isfile(_ES):
        return _ES
    for key in ("EVERYTHING_ES_EXE", "GA_ES_EXE"):
        v = (os.environ.get(key) or "").strip()
        if v and os.path.isfile(_norm(v)):
            _ES = _norm(v)
            return _ES
    cfg = _parse_tools().get("es.exe")
    if cfg and os.path.isfile(_norm(cfg)):
        _ES = _norm(cfg)
        return _ES
    found = shutil.which("es.exe")
    if found:
        _ES = _norm(found)
        return _ES
    return None


def ensure_search_ready() -> dict[str, Any]:
    if sys.platform != "win32":
        return _err("unsupported_platform", "Everything/es requires Windows", ready=False)
    es = _find_es()
    if not es:
        return _err("es_not_found", "es.exe not found", ready=False)
    return _ok(ready=True, es_path=es)


def _run_es(query: str, scope: str | None, limit: int) -> list[dict[str, Any]]:
    es = _find_es()
    if not es:
        return []
    args = [es, "-n", str(limit)]
    if scope:
        args += ["-path", _norm(scope)]
    args.append(query)
    try:
        r = subprocess.run(args, capture_output=True, timeout=30, creationflags=0x08000000 if os.name == "nt" else 0)
    except (OSError, subprocess.TimeoutExpired):
        return []
    enc = "gbk" if os.name == "nt" else "utf-8"
    out = (r.stdout or b"").decode(enc, errors="replace")
    rows = []
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split("|", 2)
        path = parts[0].strip() if parts else line
        if path:
            rows.append({"path": _norm(path), "name": os.path.basename(path)})
        if len(rows) >= limit:
            break
    return rows


def search_rows(query: str, scope: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    return _run_es(query, scope, limit)


def search_paths(query: str, scope: str | None = None, limit: int = 50) -> list[str]:
    return [r["path"] for r in search_rows(query, scope, limit)]
