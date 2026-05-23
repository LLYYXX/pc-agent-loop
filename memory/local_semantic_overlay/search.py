"""Search adapter for LSO substrate.

Mechanical only: locate es/Everything, subprocess, encoding, timeout, path rows.
Must not generate tags, nodes, ranking, or overlay state.
"""

from __future__ import annotations

import codecs
import os
import re
import shutil
import subprocess
import sys
import time
from typing import Any

from ._config import DEFAULT_TIMEOUT, ES_ENV_KEYS, GLOBAL_MEM_PATH, PROBE_TIMEOUT, norm_path

_ES: str | None = None


def _set_es(p: str) -> str:
    global _ES
    _ES = norm_path(p)
    return _ES


def _enc() -> str:
    default = "mbcs" if sys.platform == "win32" else "utf-8"
    enc = (os.environ.get("ES_STDOUT_ENCODING") or "").strip() or default
    try:
        codecs.lookup(enc)
    except LookupError:
        return default
    return enc


def _parse_tools() -> dict[str, str]:
    try:
        text = GLOBAL_MEM_PATH.read_text(encoding="utf-8", errors="replace")
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
            tools[k.strip().lower()] = v.strip().strip('"')
    return tools


def _write_tool(key: str, val: str) -> bool:
    try:
        text = GLOBAL_MEM_PATH.read_text(encoding="utf-8", errors="replace")
    except OSError:
        text = ""
    lines = text.splitlines(keepends=True) or []
    out: list[str] = []
    in_sec = found_sec = wrote = False
    entry = f"{key}={val}\n"
    for line in lines:
        m = re.match(r"^##\s*\[([^\]]+)\]\s*$", line.strip(), re.I)
        if m:
            if in_sec and not wrote:
                out.append(entry)
                wrote = True
            in_sec = m.group(1).strip().upper() == "LOCAL_TOOLS"
            if in_sec:
                found_sec = True
            out.append(line)
            continue
        if in_sec and line.strip().lower().startswith(f"{key.lower()}="):
            out.append(entry)
            wrote = True
            continue
        out.append(line)
    if in_sec and not wrote:
        out.append(entry)
    if not found_sec:
        out.append(f"\n## [LOCAL_TOOLS]\n{entry}")
    try:
        GLOBAL_MEM_PATH.parent.mkdir(parents=True, exist_ok=True)
        GLOBAL_MEM_PATH.write_text("".join(out), encoding="utf-8", newline="\n")
    except OSError:
        return False
    return True


def _ev_for_es(es: str) -> str | None:
    d = os.path.dirname(os.path.abspath(es))
    for name in ("Everything.exe", "Everything64.exe", "Everything32.exe"):
        c = os.path.join(d, name)
        if os.path.isfile(c):
            return c
    return None


def _probe(es: str) -> bool:
    try:
        kw: dict[str, Any] = {"capture_output": True, "timeout": PROBE_TIMEOUT}
        if sys.platform == "win32":
            kw["creationflags"] = 0x08000000
        r = subprocess.run([es, "-n", "1", "*"], **kw)
        return r.returncode == 0 or bool((r.stdout or b"").strip())
    except (OSError, subprocess.TimeoutExpired):
        return False


def _start_everything(es: str) -> bool:
    ev = _ev_for_es(es)
    if not ev:
        return False
    try:
        kw: dict[str, Any] = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
        if sys.platform == "win32":
            kw["creationflags"] = 0x08000000 | 0x00000200
        subprocess.Popen([ev, "-startup", "-minimized"], **kw)
    except OSError:
        return False
    for _ in range(12):
        time.sleep(0.8)
        if _probe(es):
            return True
    return False


def _find_es() -> str | None:
    if _ES and os.path.isfile(_ES):
        return _ES
    for key in ES_ENV_KEYS:
        v = (os.environ.get(key) or "").strip()
        if v and os.path.isfile(norm_path(v)):
            return _set_es(v)
    cfg = _parse_tools().get("es.exe")
    if cfg and os.path.isfile(norm_path(cfg)):
        return _set_es(cfg)
    found = shutil.which("es.exe")
    if found:
        return _set_es(found)
    if sys.platform == "win32":
        try:
            import winreg
            for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
                for sub in (r"SOFTWARE\voidtools\Everything", r"SOFTWARE\WOW6432Node\voidtools\Everything"):
                    try:
                        with winreg.OpenKey(hive, sub) as h:
                            base, _ = winreg.QueryValueEx(h, "InstallLocation")
                        if isinstance(base, str) and base.strip():
                            es = os.path.join(base.strip(), "es.exe")
                            if os.path.isfile(es):
                                return _set_es(es)
                    except OSError:
                        continue
        except Exception:
            pass
        for root in (
            os.environ.get("ProgramFiles", ""),
            os.environ.get("ProgramFiles(x86)", ""),
            os.environ.get("LocalAppData", ""),
        ):
            if not root:
                continue
            for sub in ("", "Everything", os.path.join("voidtools", "Everything")):
                es = os.path.join(root, sub, "es.exe") if sub else os.path.join(root, "es.exe")
                if os.path.isfile(es):
                    return _set_es(es)
    return None


def _file_info(path: str) -> dict[str, Any]:
    normed = norm_path(path)
    row: dict[str, Any] = {"path": normed, "name": os.path.basename(normed), "mtime": None, "size": None}
    try:
        st = os.stat(normed)
        row["mtime"] = st.st_mtime
        row["size"] = st.st_size
    except OSError:
        pass
    return row


def ensure_search_ready(*, persist: bool = False) -> dict[str, Any]:
    if sys.platform != "win32":
        return {"ok": False, "error": "unsupported_platform", "message": "Everything/es requires Windows", "ready": False}
    es = _find_es()
    if not es:
        return {
            "ok": False,
            "error": "es_not_found",
            "message": "es.exe not found; set EVERYTHING_ES_EXE or install Everything",
            "ready": False,
        }
    if not (_probe(es) or _start_everything(es)):
        return {
            "ok": False,
            "error": "everything_not_running",
            "message": "Everything not running and auto-start failed",
            "ready": False,
            "es_path": es,
        }
    if persist:
        _write_tool("es.exe", es)
        ev = _ev_for_es(es)
        if ev:
            _write_tool("Everything.exe", ev)
    return {"ok": True, "error": None, "message": "", "ready": True, "es_path": es}


def search_rows(query: str, scope: str | None = None, limit: int = 50, *, timeout: float = DEFAULT_TIMEOUT) -> list[dict[str, Any]]:
    try:
        n = int(limit)
    except (TypeError, ValueError):
        n = 50
    n = min(max(n, 0), 50000)
    if n <= 0:
        return []
    es = _find_es()
    if not es:
        return []
    args = [es, "-n", str(n)]
    if scope:
        s = norm_path(scope)
        if len(s) == 2 and s[1] == ":":
            s += os.sep
        args += ["-path", s]
    args.append(str(query))
    try:
        kw: dict[str, Any] = {"capture_output": True, "timeout": timeout,
                               "text": True, "encoding": _enc(), "errors": "replace"}
        if sys.platform == "win32":
            kw["creationflags"] = 0x08000000
        r = subprocess.run(args, **kw)
    except (LookupError, OSError, subprocess.TimeoutExpired):
        return []
    rows: list[dict[str, Any]] = []
    for line in (r.stdout or "").splitlines():
        p = line.strip()
        if p and (r.returncode == 0 or os.path.isabs(p)):
            rows.append(_file_info(p))
        if len(rows) >= n:
            break
    return rows


def search_paths(query: str, scope: str | None = None, limit: int = 50, **kw: Any) -> list[str]:
    return [r["path"] for r in search_rows(query, scope, limit, **kw)]
