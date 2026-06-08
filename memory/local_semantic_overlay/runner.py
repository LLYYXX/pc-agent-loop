"""LSO control plane and mechanical build runner."""
from __future__ import annotations
import json, os, time
from pathlib import Path
from typing import Any
from . import ga_multiagent as agent, overlay
from .select import CONFIG, MECHANICAL_LIMIT, discover_candidates, select_for_read
ORDER = ("selector", "compressor", "tagger", "aggregator", "auditor")
KEYS = {"selector": {"role", "artifact_id", "retained", "discarded"},
        "compressor": {"role", "artifact_id", "targets", "standalone_leaf_ids"},
        "tagger": {"role", "artifact_id", "claims"},
        "aggregator": {"role", "artifact_id", "facet_nodes", "semantic_nodes"},
        "auditor": {"role", "artifact_id", "verdict", "evidence", "rework_role"}}
REQ = {"selector": ("retained", "discarded"), "compressor": ("targets", "standalone_leaf_ids"),
       "tagger": ("claims",), "aggregator": ("facet_nodes", "semantic_nodes"), "auditor": ("verdict",)}
EXTS = {"doc": {".md", ".txt", ".rst", ".pdf", ".doc", ".docx", ".ppt", ".pptx", ".xls", ".xlsx"},
        "code": {".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".go", ".rs", ".xml"},
        "data": {".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".csv"},
        "archive": {".zip", ".rar", ".7z", ".tar", ".gz"}, "media": {".png", ".jpg", ".jpeg", ".mp3", ".mp4", ".wav"}}
ROOT = Path(__file__).resolve().parents[2]
def _seq(x: Any) -> list[Any]: return [] if x is None else x if isinstance(x, list) else [x]
def _norm(x: Any) -> str: return os.path.normcase(os.path.abspath(str(x)))
def _dir(scope: str) -> Path:
    d = overlay.overlay_path(scope).with_suffix(""); d.mkdir(parents=True, exist_ok=True); return d
def _rw(scope: str, name: str, data: Any | None = None) -> Any:
    p = _dir(scope) / name
    if data is None: return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {}
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"); return data
def _paths(items: Any) -> list[str]:
    return [_norm(x.get("path") if isinstance(x, dict) else x) for x in _seq(items)
            if (isinstance(x, dict) and x.get("path")) or (not isinstance(x, dict) and x)]
def _path_view(scope: str, rows: list[dict[str, Any]]) -> str:
    root, dirs = _norm(scope), {}
    for r in rows:
        try: rel = os.path.relpath(os.path.dirname(_norm(r["path"])), root)
        except ValueError: continue
        if rel.startswith(".."): continue
        parts = [] if rel == "." else rel.split(os.sep)
        for i in range(len(parts) + 1):
            key = "." if i == 0 else os.path.join(*parts[:i]); dirs[key] = dirs.get(key, 0) + 1
    return "\n".join(f"{'  ' * (0 if k == '.' else len(k.split(os.sep)))}{'.' if k == '.' else k.split(os.sep)[-1] + os.sep}\t{v}" for k, v in sorted(dirs.items()))
def _summary(rows: list[dict[str, Any]], scope: str) -> dict[str, Any]:
    top, assets = {}, {}
    for r in rows:
        try: rel = os.path.relpath(r["path"], scope)
        except ValueError: rel = ".."
        key = "." if rel.startswith("..") else rel.split(os.sep, 1)[0]
        ext = Path(r["path"]).suffix.lower(); asset = next((n for n, vals in EXTS.items() if ext in vals), "other")
        top[key] = top.get(key, 0) + 1; assets[asset] = assets.get(asset, 0) + 1
    return {"top": top, "assets": assets}
def _selector_task(scope: str, st: dict[str, Any]) -> dict[str, Any]:
    pool = _rw(scope, "candidate_pool.json"); size = int(CONFIG.get("selector_batch_size", 200))
    off = int(st.get("selector_offset", 0)); batch = pool.get("items", [])[off:off + size]
    return {"role": "selector", "scope": st["scope"], "question": st.get("question", ""),
            "batch": {"offset": off, "size": len(batch), "total": pool.get("count", 0)}, "candidates": batch,
            "path_view": _path_view(st["scope"], batch),
            "rule": "recall-preserving noise rejection: retain unless explicit noise evidence; never rank, choose best files, or discard low/uncertain value; no tags or nodes",
            "output_schema": {"role": "selector", "retained": [{"path": "str", "reason": "str"}],
                              "discarded": [{"path": "str", "noise_evidence": "str", "reason": "str"}]}}
def _role_task(scope: str, role: str, st: dict[str, Any]) -> dict[str, Any]:
    out = agent.role_tasks(scope, question=st.get("question", ""))[role]
    names = ("selector_ledger.json", "compressor_artifact.json", "tagger_artifact.json", "aggregator_artifact.json")
    out["current_run_artifacts"] = {n: str(_dir(scope) / n) for n in names if (_dir(scope) / n).is_file()}; out["build_state"] = st
    if role == "tagger":
        c = _rw(scope, "compressor_artifact.json")
        out["tag_targets"] = {"leaf_ids": c.get("standalone_leaf_ids", []),
                              "node_ids": [x.get("target_id") for x in c.get("targets", []) if x.get("target_id")]}
    if role == "auditor":
        out["build_artifacts"] = {n: str(_dir(scope) / n) for n in ("candidate_pool.json", *names, "coverage_report.json") if (_dir(scope) / n).is_file()}
        out["coverage_facts"] = coverage_audit(scope)
    return out
def prepare(scope: str, *, question: str = "", seeds: list[Any] | None = None, limit: int = MECHANICAL_LIMIT, reset: bool = False) -> dict[str, Any]:
    active = _rw(scope, "build_state.json")
    if not reset and active.get("stage") not in (None, "complete", "audit_failed"):
        return {"ok": False, "error": "build_in_progress", "state": active, "task": task(scope)}
    if reset:
        if overlay.overlay_path(scope).is_file(): overlay.overlay_path(scope).unlink()
        for p in _dir(scope).glob("*.json"): p.unlink()
    _rw(scope, "base_overlay.json", overlay.load(scope))
    seed_paths = [x.get("path") if isinstance(x, dict) else x for x in seeds or []]
    seed_paths = [str(x) for x in seed_paths if x]; root = _norm(scope).rstrip(os.sep) + os.sep
    seed_paths = [x for x in seed_paths if _norm(x) == _norm(scope) or _norm(x).startswith(root)]
    rows = select_for_read(seed_paths, seeds=seed_paths, limit=None) if seed_paths and not reset else discover_candidates(scope, query=question, seeds=seed_paths, limit=limit)
    if not reset:
        existing = {_norm(x["path"]) for x in overlay.load(scope)["leaves"].values()}
        rows = [x for x in rows if _norm(x["path"]) not in existing]
    pool = {"scope": os.path.abspath(scope), "count": len(rows), "items": rows, "summary": _summary(rows, scope)}
    state = {"scope": os.path.abspath(scope), "stage": "selector" if rows else "auditor", "mode": "cold_start" if reset else "incremental",
             "question": question, "seeds": seed_paths, "roles": ORDER, "selector_offset": 0}
    _rw(scope, "candidate_pool.json", pool); _rw(scope, "selector_ledger.json", {"batches": []}); _rw(scope, "build_state.json", state)
    facts = coverage_audit(scope)
    return {"ok": True, "state": state, "candidate_pool": pool, "coverage_issues": facts["issues"], "task": task(scope)}
def task(scope: str) -> dict[str, Any]:
    st = _rw(scope, "build_state.json"); role = st.get("stage", "selector")
    return _selector_task(scope, st) if role == "selector" else _role_task(scope, role, st)
def validate_artifact(a: dict[str, Any]) -> dict[str, Any]:
    role = a.get("role")
    if role not in KEYS: return {"ok": False, "error": "unknown_role", "role": role}
    extra = sorted(set(a) - KEYS[role])
    if extra: return {"ok": False, "error": "unknown_fields", "fields": extra}
    if not any(k in a for k in REQ[role]): return {"ok": False, "error": "missing_payload", "role": role}
    return {"ok": True}
def _apply_selector(scope: str, a: dict[str, Any], st: dict[str, Any]) -> dict[str, Any]:
    t = _selector_task(scope, st); expected = {_norm(x["path"]): x for x in t["candidates"]}
    retained, discarded = _seq(a.get("retained")), _seq(a.get("discarded")); got = _paths(retained) + _paths(discarded)
    dup = sorted({p for p in got if got.count(p) > 1}); outside = sorted(set(got) - set(expected)); missing = sorted(set(expected) - set(got))
    if dup or outside or missing: return {"ok": False, "error": "candidate_batch_not_consumed", "duplicates": dup, "outside": outside, "missing": missing}
    no_evidence = [x for x in discarded if isinstance(x, dict) and not str(x.get("noise_evidence") or "").strip()]
    if no_evidence: return {"ok": False, "error": "discard_missing_noise_evidence", "items": no_evidence}
    def enrich(items: list[Any]) -> list[dict[str, Any]]: return [{**expected[_norm(x["path"])], **x} for x in items]
    retained, discarded = enrich(retained), enrich(discarded)
    res = overlay.apply_artifact(scope, {"role": "selector", "artifact_id": a.get("artifact_id"), "retained": retained, "discarded": discarded})
    ledger = _rw(scope, "selector_ledger.json"); ledger.setdefault("batches", []).append({"offset": t["batch"]["offset"], "retained": retained, "discarded": discarded})
    _rw(scope, "selector_ledger.json", ledger); st["selector_offset"] = int(st.get("selector_offset", 0)) + len(expected)
    st["stage"] = "selector" if st["selector_offset"] < t["batch"]["total"] else "compressor" if any(x.get("retained") for x in ledger["batches"]) else "auditor"
    _rw(scope, "build_state.json", st); return res
def _compressor_artifact(scope: str, a: dict[str, Any]) -> dict[str, Any]:
    data = overlay.load(scope); by_path = {_norm(v["path"]): k for k, v in data["leaves"].items()}; targets = []
    for raw in _seq(a.get("targets")):
        x = dict(raw); ids = _seq(x.get("supporting_leaf_ids"))
        ids += [by_path[p] for p in _paths(x.pop("supporting_paths", [])) if p in by_path]
        x["supporting_leaf_ids"] = list(dict.fromkeys(ids)); targets.append(x)
    known = set(data["leaves"]); used = {x for t in targets for x in _seq(t.get("supporting_leaf_ids"))}; standalone = set(_seq(a.get("standalone_leaf_ids")))
    ledger = _rw(scope, "selector_ledger.json"); current = {by_path[p] for b in ledger.get("batches", []) for p in _paths(b.get("retained")) if p in by_path}
    if (used | standalone) - known or current - used - standalone:
        return {"ok": False, "error": "compression_coverage_invalid", "unknown": sorted((used | standalone) - known), "missing": sorted(current - used - standalone)}
    return {"role": "compressor", "artifact_id": a.get("artifact_id"), "targets": targets, "standalone_leaf_ids": sorted(standalone)}
def _rewind(scope: str, role: str) -> None:
    overlay.save(scope, _rw(scope, "base_overlay.json") or overlay.load(scope))
    if role != "selector":
        ledger = _rw(scope, "selector_ledger.json"); retained = [x for b in ledger.get("batches", []) for x in b.get("retained", [])]
        overlay.apply_artifact(scope, {"role": "selector", "retained": retained})
        for r in ORDER[1:ORDER.index(role)]:
            a = _rw(scope, f"{r}_artifact.json")
            if a: overlay.apply_artifact(scope, a)
    for r in ORDER[ORDER.index(role):-1]:
        p = _dir(scope) / f"{r}_artifact.json"
        if p.is_file(): p.unlink()
def apply_stage(scope: str, artifact: dict[str, Any], *, probes: list[str] | None = None) -> dict[str, Any]:
    st = _rw(scope, "build_state.json") or {"stage": artifact.get("role"), "roles": ORDER}; role = artifact.get("role")
    v = validate_artifact(artifact)
    if not v["ok"]: return v
    if role != st.get("stage"): return {"ok": False, "error": "wrong_stage", "expected": st.get("stage"), "got": role}
    if role == "auditor":
        facts = coverage_audit(scope, probes=probes); passed = artifact.get("verdict") == "PASS"
        rework = artifact.get("rework_role"); stage = "complete" if passed else rework if rework in ORDER[:-1] else "audit_failed"
        if stage in ORDER[:-1]: _rewind(scope, stage)
        if stage == "selector": st["selector_offset"] = 0; _rw(scope, "selector_ledger.json", {"batches": []})
        st.update({"stage": stage, "coverage_audit": facts}); _rw(scope, "build_state.json", st)
        nxt = task(scope) if stage not in ("complete", "audit_failed") else None
        if nxt is not None: nxt["audit_evidence"] = artifact.get("evidence")
        return {"ok": passed, "role": role, "coverage_audit": facts, "next_task": nxt}
    if role == "selector":
        res = _apply_selector(scope, artifact, st)
        return res if not res.get("ok") else {"ok": True, "role": role, "apply_result": res, "next_task": task(scope)}
    if role == "tagger":
        targets = _role_task(scope, role, st)["tag_targets"]; allowed = set(targets["leaf_ids"] + targets["node_ids"])
        invalid = [x for x in _seq(artifact.get("claims")) if (x.get("leaf_id") or x.get("node_id")) not in allowed]
        if invalid: return {"ok": False, "error": "tag_target_invalid", "invalid": invalid}
    normalized = _compressor_artifact(scope, artifact) if role == "compressor" else artifact
    if normalized.get("ok") is False: return normalized
    res = overlay.apply_artifact(scope, normalized)
    if res.get("rejected"): return {"ok": False, "error": "rejected_items", "apply_result": res}
    if role not in ("compressor", "aggregator") and not res.get("accepted"): return {"ok": False, "error": "accepted_zero", "apply_result": res}
    _rw(scope, f"{role}_artifact.json", normalized); st["stage"] = ORDER[ORDER.index(role) + 1]; _rw(scope, "build_state.json", st)
    return {"ok": True, "role": role, "apply_result": res, "next_task": task(scope)}
def apply_task_artifact(scope: str, task_dir: str | Path, *, probes: list[str] | None = None) -> dict[str, Any]:
    root, reply = Path(task_dir), Path(task_dir) / "reply.txt"
    try: res = apply_stage(scope, json.loads((root / "artifact.json").read_text(encoding="utf-8")), probes=probes)
    except Exception as e: res = {"ok": False, "error": "invalid_artifact_json", "detail": str(e)}
    if not res.get("ok"):
        reply.write_text("artifact.json was rejected by LSO. Rewrite artifact.json yourself; the main Agent must not edit role artifacts.\n" + json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
        return {**res, "correction_required": True, "reply": str(reply)}
    if reply.is_file(): reply.unlink()
    return res
def coverage_audit(scope: str, *, probes: list[str] | None = None) -> dict[str, Any]:
    pool, ledger, data, st = _rw(scope, "candidate_pool.json"), _rw(scope, "selector_ledger.json"), overlay.load(scope), _rw(scope, "build_state.json")
    decided = {_norm(x["path"]) for b in ledger.get("batches", []) for k in ("retained", "discarded") for x in b.get(k, [])}
    retained = {_norm(x["path"]) for b in ledger.get("batches", []) for x in b.get("retained", [])}; leaves = {_norm(x["path"]) for x in data["leaves"].values()}
    issues: list[dict[str, Any]] = []
    if not pool.get("count"): issues.append({"type": "candidate_pool_empty"})
    if pool.get("count", 0) > len(decided): issues.append({"type": "unclassified_candidates", "count": pool["count"] - len(decided)})
    target = int(CONFIG.get("cold_start_retained_target", 100))
    if st.get("mode") == "cold_start" and len(decided) == pool.get("count") and len(retained) < target:
        issues.append({"type": "retained_below_target", "retained": len(retained), "target": target, "discarded": len(decided - retained)})
    if retained - leaves: issues.append({"type": "retained_missing_overlay", "count": len(retained - leaves)})
    compressed = [x for x in data["nodes"].values() if x.get("source") == "compressed"]
    compressed_leaves = {lid for x in compressed for lid in x.get("supporting_leaf_ids", [])}
    untagged = [lid for lid, x in data["leaves"].items() if lid not in compressed_leaves and not x.get("tags")]
    untagged += [x["node_id"] for x in compressed if not x.get("tags")]
    if untagged: issues.append({"type": "untagged_targets", "target_ids": untagged})
    unsupported = overlay.audit_packet(scope)["packet"]["unsupported_nodes"]
    if unsupported: issues.append({"type": "unsupported_nodes", "node_ids": unsupported})
    for q in probes or []:
        if not overlay.query(scope, q).get("hits"): issues.append({"type": "probe_miss", "query": q})
    report = {"issues": issues, "candidate_count": pool.get("count", 0), "decided_count": len(decided),
              "retained_count": len(retained), "leaf_count": len(leaves)}
    _rw(scope, "coverage_report.json", report); return report
def _write(path: Path | None, data: Any) -> None:
    if path: path.parent.mkdir(parents=True, exist_ok=True); path.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
def _line(log: Path | None, msg: str) -> None:
    if not log: return
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a", encoding="utf-8") as f: f.write(f"[{time.strftime('%H:%M:%S')}] {msg}\n")
def run_build(scope: str, *, task_name: str = "lso_build", question: str = "", seeds: list[Any] | None = None,
              reset: bool = False, prepare_first: bool = True, timeout_sec: int = 1800, correction_limit: int = 6,
              progress_path: str | Path | None = None, timings_path: str | Path | None = None,
              log_path: str | Path | None = None, worker_args: list[str] | None = None) -> dict[str, Any]:
    """Run the serial LSO role loop; never edits role artifacts."""
    progress = Path(progress_path) if progress_path else ROOT / "temp" / f"{task_name}_progress.json"
    timings = Path(timings_path) if timings_path else ROOT / "temp" / f"{task_name}_timings.json"
    log = Path(log_path) if log_path else ROOT / "temp" / f"{task_name}.log"
    state: dict[str, Any] = {"started": time.time(), "roles_done": [], "status": "running"}; phases: list[dict[str, Any]] = []
    def mark(status: str, **extra: Any) -> dict[str, Any]:
        state.update({"status": status, **extra}); _write(progress, state)
        _write(timings, {"scope": scope, "reset": reset, "phases": phases, "total_sec": round(time.time() - state["started"], 2), "status": status})
        return state
    try:
        if prepare_first:
            t0 = time.time(); prep = prepare(scope, question=question, seeds=seeds, reset=reset); t1 = time.time()
            phases.append({"phase": "prepare", "duration_sec": round(t1 - t0, 2), "ok": prep.get("ok"), "error": prep.get("error")})
            if not prep.get("ok") and prep.get("error") != "build_in_progress": return mark("prepare_failed", result=prep)
            current = prep.get("task") or task(scope)
        else: current = task(scope)
        _write(progress, state); _write(timings, {"scope": scope, "reset": reset, "phases": phases})
        while current:
            role, batch = current.get("role"), current.get("batch")
            state["current"] = {"role": role, "batch": batch}; _write(progress, state); _line(log, f"ROLE {role} {batch or ''}")
            t0 = time.time(); info = agent.write_task_dir(task_name, role, current); task_dir = Path(info["task_dir"])
            pid = agent.launch_task(task_name, worker_args); last, corrections = 0.0, 0
            while True:
                mtime = agent.wait_artifact(task_dir, last, timeout_sec)
                if mtime is None:
                    try: agent.close_task(pid)
                    finally: phases.append({"phase": role, "duration_sec": round(time.time() - t0, 2), "result": "timeout"})
                    return mark("timeout")
                last = mtime; result = apply_task_artifact(scope, task_dir)
                if not result.get("correction_required"): break
                corrections += 1; _line(log, f"correction {corrections}: {result.get('error')}")
                if corrections > correction_limit:
                    try: agent.close_task(pid)
                    finally: phases.append({"phase": role, "duration_sec": round(time.time() - t0, 2), "result": "stuck", "corrections": corrections})
                    return mark("stuck_corrections", last_result=result)
            agent.close_task(pid); phases.append({"phase": role, "duration_sec": round(time.time() - t0, 2), "result": "accepted", "corrections": corrections})
            state["roles_done"].append({"role": role, "batch": batch}); _write(progress, state); current = result.get("next_task")
            if current is None: return mark("complete" if result.get("ok") else "ended", final={k: v for k, v in result.items() if k != "next_task"})
        return mark("complete")
    except Exception as e:
        return mark("fatal", error=repr(e))
