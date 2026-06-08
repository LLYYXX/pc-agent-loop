"""ES/Everything substrate. Selection config lives in select.py."""

from __future__ import annotations

import codecs, os, re, shutil, subprocess, sys, time
from pathlib import Path
from typing import Any

PKG = Path(__file__).resolve().parent


def norm_path(path: str | os.PathLike[str]) -> str:
    return os.path.normpath(os.path.abspath(str(path).strip().strip('"').strip("'")))


GLOBAL_MEM_PATH = (PKG / "../global_mem.txt").resolve()
DEFAULT_TIMEOUT = 30.0
PROBE_TIMEOUT = 8.0
ES_ENV_KEYS = ("EVERYTHING_ES_EXE", "GA_ES_EXE", "FILE_INDEX_ES_EXE")
_ES: str | None = None
_START_ATTEMPTED = False


def _enc() -> str:
    default = "mbcs" if sys.platform == "win32" else "utf-8"
    enc = os.environ.get("ES_STDOUT_ENCODING") or default
    try:
        codecs.lookup(enc)
        return enc
    except LookupError:
        return default


def _tool_cfg() -> dict[str, str]:
    try:
        text = GLOBAL_MEM_PATH.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    out, in_sec = {}, False
    for line in text.splitlines():
        m = re.match(r"^##\s*\[([^\]]+)\]\s*$", line.strip(), re.I)
        if m:
            in_sec = m.group(1).strip().upper() == "LOCAL_TOOLS"
        elif in_sec and "=" in line:
            k, v = line.split("=", 1); out[k.strip().lower()] = v.strip().strip('"')
    return out


def _remember(key: str, val: str) -> None:
    try:
        old = GLOBAL_MEM_PATH.read_text(encoding="utf-8", errors="replace") if GLOBAL_MEM_PATH.is_file() else ""
        entry = f"{key}={val}"
        current = re.compile(rf"(?im)^{re.escape(key)}\s*=.*$")
        section = re.search(r"(?im)^##\s*\[LOCAL_TOOLS\]\s*$", old)
        if current.search(old): body = current.sub(lambda _: entry, old)
        elif section: body = old[:section.end()] + "\n" + entry + old[section.end():]
        else: body = old.rstrip() + f"\n\n## [LOCAL_TOOLS]\n{entry}\n"
        if body != old:
            GLOBAL_MEM_PATH.parent.mkdir(parents=True, exist_ok=True)
            GLOBAL_MEM_PATH.write_text(body, encoding="utf-8", newline="\n")
    except OSError:
        pass


def _probe(es: str) -> bool:
    try:
        kw: dict[str, Any] = {"capture_output": True, "timeout": PROBE_TIMEOUT}
        if sys.platform == "win32":
            kw["creationflags"] = 0x08000000
        r = subprocess.run([es, "-n", "1", "*"], **kw)
        return r.returncode == 0 or bool((r.stdout or b"").strip())
    except (OSError, subprocess.TimeoutExpired):
        return False


def _start(es: str) -> bool:
    global _START_ATTEMPTED
    if _START_ATTEMPTED:
        return False
    _START_ATTEMPTED = True
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
    for _ in range(10):
        time.sleep(0.5)
        if _probe(es):
            return True
    return False


def _candidates() -> list[str]:
    out = [os.environ.get(k, "") for k in ES_ENV_KEYS]
    out += [_tool_cfg().get("es.exe", ""), shutil.which("es.exe") or ""]
    if sys.platform == "win32":
        try:
            import winreg
            for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
                for sub in (r"SOFTWARE\voidtools\Everything", r"SOFTWARE\WOW6432Node\voidtools\Everything"):
                    try:
                        with winreg.OpenKey(hive, sub) as h: base, _ = winreg.QueryValueEx(h, "InstallLocation")
                        out.append(os.path.join(str(base), "es.exe"))
                    except OSError: pass
        except ImportError: pass
        roots = [os.environ.get("ProgramFiles", ""), os.environ.get("ProgramFiles(x86)", ""), os.environ.get("LocalAppData", "")]
        out += [os.path.join(r, s, "es.exe") for r in roots for s in ("Everything", os.path.join("voidtools", "Everything")) if r]
    return [norm_path(x) for x in out if x]


def _ev_for_es(es: str) -> str | None:
    folder = os.path.dirname(os.path.abspath(es))
    return next((os.path.join(folder, n) for n in ("Everything.exe", "Everything64.exe", "Everything32.exe")
                 if os.path.isfile(os.path.join(folder, n))), None)


def _find_es() -> str | None:
    global _ES
    if _ES and os.path.isfile(_ES):
        return _ES
    for p in _candidates():
        if os.path.isfile(p):
            _ES = p; return p
    return None


def ensure_search_ready(*, persist: bool = True) -> dict[str, Any]:
    if sys.platform != "win32":
        return {"ok": False, "error": "unsupported_platform", "ready": False}
    es = _find_es()
    if not es:
        return {"ok": False, "error": "es_not_found", "ready": False}
    ready = _probe(es) or _start(es)
    if ready and persist:
        _remember("es.exe", es)
        ev = _ev_for_es(es)
        if ev: _remember("Everything.exe", ev)
    return {"ok": bool(ready), "error": None if ready else "everything_not_running", "ready": bool(ready), "es_path": es}


def _info(path: str) -> dict[str, Any]:
    p = norm_path(path); row: dict[str, Any] = {"path": p, "name": os.path.basename(p), "mtime": None, "size": None}
    try:
        st = os.stat(p); row.update({"mtime": st.st_mtime, "size": st.st_size})
    except OSError:
        pass
    return row


def resolve_lnk(paths: list[str]) -> list[str]:
    if not paths: return []
    script = "$w=New-Object -ComObject WScript.Shell; $input|%{try{$s=$w.CreateShortcut($_);if($s.TargetPath){$s.TargetPath}}catch{}}"
    try:
        kw: dict[str, Any] = {"input": "\n".join(paths), "capture_output": True, "timeout": DEFAULT_TIMEOUT,
                              "text": True, "encoding": _enc(), "errors": "replace"}
        if sys.platform == "win32":
            kw["creationflags"] = 0x08000000
        r = subprocess.run(["powershell", "-NoProfile", "-Command", script], **kw)
    except (OSError, subprocess.TimeoutExpired, LookupError):
        return []
    return [norm_path(x) for x in (r.stdout or "").splitlines() if x.strip()]


def iter_column_rows(query: str, scope: str | None = None, columns: list[str] | None = None, *,
                     limit: int | None = 50, sort: str | None = None, timeout: float = DEFAULT_TIMEOUT, files_only: bool = True):
    es = _ready_es()
    if not es: return
    cols = columns or []
    args = _base_args(es, scope, False, files_only)
    if limit is not None: args += ["-n", str(max(0, int(limit)))]
    if sort: args += ["-sort", sort]
    args += ["-tsv", "-no-header", "-full-path-and-name", *cols, str(query)]
    try:
        kw: dict[str, Any] = {"stdout": subprocess.PIPE, "stderr": subprocess.DEVNULL, "text": True, "encoding": _enc(), "errors": "replace"}
        if sys.platform == "win32": kw["creationflags"] = 0x08000000
        p = subprocess.Popen(args, **kw)
    except (OSError, LookupError): return
    try:
        for line in p.stdout or []:
            parts = line.rstrip("\r\n").split("\t")
            if len(parts) < 1 + len(cols): continue
            vals = parts[-len(cols):] if cols else []
            yield {"path": norm_path("\t".join(parts[:len(parts) - len(cols)])), **dict(zip(cols, vals))}
    finally:
        if p.poll() is None: p.terminate()
        try: p.wait(timeout=min(timeout, 2))
        except subprocess.TimeoutExpired: p.kill()


def column_rows(query: str, scope: str | None = None, columns: list[str] | None = None, **kw: Any) -> list[dict[str, Any]]:
    return list(iter_column_rows(query, scope, columns, **kw))


def _base_args(es: str, scope: str | None, folders_only: bool, files_only: bool) -> list[str]:
    args = [es]
    if folders_only: args.append("/ad")
    if files_only: args.append("/a-d")
    if scope:
        s = norm_path(scope); args += ["-path", s + os.sep if len(s) == 2 and s[1] == ":" else s]
    return args


def _ready_es() -> str | None:
    ready = ensure_search_ready(); es = ready.get("es_path")
    return str(es) if ready.get("ok") and es else None


def search_rows(query: str, scope: str | None = None, limit: int | None = 50, *,
                timeout: float = DEFAULT_TIMEOUT, with_info: bool = True,
                folders_only: bool = False, files_only: bool = False, sort: str | None = None) -> list[dict[str, Any]]:
    n = None if limit is None else min(max(int(limit or 0), 0), 50000)
    es = _ready_es()
    if not es or (n is not None and n <= 0):
        return []
    args = _base_args(es, scope, folders_only, files_only)
    if n is not None:
        args += ["-n", str(n)]
    if sort: args += ["-sort", sort]
    args.append(str(query))
    try:
        kw: dict[str, Any] = {"capture_output": True, "timeout": timeout, "text": True, "encoding": _enc(), "errors": "replace"}
        if sys.platform == "win32":
            kw["creationflags"] = 0x08000000
        r = subprocess.run(args, **kw)
    except (OSError, subprocess.TimeoutExpired, LookupError):
        return []
    paths = [x.strip() for x in (r.stdout or "").splitlines() if x.strip() and (r.returncode == 0 or os.path.isabs(x.strip()))]
    rows = [_info(x) if with_info else {"path": norm_path(x), "name": os.path.basename(x)} for x in paths]
    return rows if n is None else rows[:n]


def search_paths(query: str, scope: str | None = None, limit: int | None = 50, **kw: Any) -> list[str]:
    return [r["path"] for r in search_rows(query, scope, limit, **kw)]

def _flat_parent_count(es: str, parent: str, threshold: int) -> bool | None:
    args = [es, "/a-d", "-parent", parent, "-get-result-count", "*"]
    try:
        kw: dict[str, Any] = {"capture_output": True, "timeout": DEFAULT_TIMEOUT, "text": True, "encoding": _enc(), "errors": "replace"}
        if sys.platform == "win32": kw["creationflags"] = 0x08000000
        r = subprocess.run(args, **kw)
        m = re.search(r"\d+", r.stdout or "")
        return int(m.group(0)) > threshold if m else None
    except (OSError, subprocess.TimeoutExpired, LookupError):
        return None

def flat_parents_too_large(parents: list[str], threshold: int, *, workers: int = 12) -> dict[str, bool]:
    uniq = list(dict.fromkeys(norm_path(p) for p in parents if p)); es = _ready_es()
    if es:
        roots = sorted({(os.path.splitdrive(p)[0] + os.sep) if os.path.splitdrive(p)[0] else os.path.abspath(os.sep) for p in uniq})
        large = {norm_path(r["path"]) for root in roots for r in search_rows(f"childfilecount:>{threshold}", scope=root, limit=50000, with_info=False, folders_only=True)}
        return {p: p in large for p in uniq}
    return {p: len(search_rows("*", scope=p, limit=threshold + 1, with_info=False, files_only=True)) > threshold for p in uniq}

def flat_parent_too_large(path: str, threshold: int) -> bool:
    parent = os.path.dirname(norm_path(path)); es = _ready_es()
    if es:
        val = _flat_parent_count(es, parent, threshold)
        if val is not None: return val
    return len(search_rows("*", scope=parent, limit=threshold + 1, with_info=False, files_only=True)) > threshold
