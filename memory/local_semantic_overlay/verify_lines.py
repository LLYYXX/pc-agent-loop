"""Line-budget validator for LSO source.

Excluded from final: search substrate, document extraction substrate, config,
tests, and this checker.
"""
from __future__ import annotations
from pathlib import Path

FINAL_LIMIT, RUNNER_LIMIT = 600, 300
FINAL = ("__init__.py", "overlay.py", "select.py", "ga_multiagent.py", "runner.py")
EXCLUDED = ("search.py", "document_extract.py", "verify_lines.py")

def _count(path: Path) -> int: return len(path.read_text(encoding="utf-8").splitlines())

def measure() -> dict:
    pkg = Path(__file__).resolve().parent
    files = {p.name: _count(p) for p in pkg.glob("*.py")}
    final = sum(files.get(f, 0) for f in FINAL)
    return {"final": final, "runner": files.get("runner.py", 0),
            "all_py": sum(files.values()), "excluded": sum(files.get(f, 0) for f in EXCLUDED),
            "search": files.get("search.py", 0), "document_extract": files.get("document_extract.py", 0),
            "dev_checks": files.get("verify_lines.py", 0), "config_data": _count(pkg / "config.json"),
            "files": files}

def main() -> int:
    m = measure()
    print(f"final={m['final']} limit={FINAL_LIMIT}")
    print(f"runner={m['runner']} limit={RUNNER_LIMIT}")
    print(f"all_py={m['all_py']} excluded_py={m['excluded']} search={m['search']} document_extract={m['document_extract']} dev_checks={m['dev_checks']} config_data={m['config_data']}")
    for name, count in sorted(m["files"].items()): print(f"{name}: {count}")
    ok = m["final"] < FINAL_LIMIT and m["runner"] < RUNNER_LIMIT
    print("OK" if ok else "FAIL")
    return 0 if ok else 1

if __name__ == "__main__":
    raise SystemExit(main())
