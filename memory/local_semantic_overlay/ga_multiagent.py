from __future__ import annotations
import json, os, re, signal, subprocess, sys, time
from pathlib import Path
from typing import Any
from . import overlay
ROLE_GUIDE = {
    "selector": "Recall-preserving noise rejector. Retain unless there is explicit noise evidence. Do not rank, choose best files, or discard low/uncertain value. No tags or nodes.",
    "compressor": "Compress only mature project/tool/service directories: entry file, project structure, and a concrete project/tool/service name. Shared directory/topic/extension is not enough; otherwise mark leaves standalone.",
    "tagger": "Tag only IDs in tag_targets. For each target, produce 2-5 evidence-backed tags across distinct semantic axes when supported; single-tag targets require truly narrow evidence. No aggregate nodes.",
    "aggregator": "Use current-run plus existing claims only. Return ordered explicit-id nodes; semantic nodes may repeatedly derive from earlier nodes. Do not read raw files.",
    "auditor": "Independently judge role semantics and final-map usefulness, not just schema. Non-project compression, systematic single-tag targets, or shallow aggregation require rework unless justified. Never writes overlay.",
}
def _brief_overlay(scope: str) -> dict[str, Any]:
    data = overlay.load(scope)
    leaves = [{"leaf_id": k, "path": v.get("path"), "tags": v.get("tags", []), "evidence_kind": v.get("evidence_kind"),
               "signals": v.get("selector_signals", []), "claims": v.get("claims", [])} for k, v in data["leaves"].items()]
    nodes = [{"node_id": k, "label": v.get("label"), "tags": v.get("tags", []),
              "layer": v.get("layer"), "node_type": v.get("node_type"),
              "brief": v.get("brief"), "boundary": v.get("boundary"), "claims": v.get("claims", []),
              "supporting_leaf_ids": v.get("supporting_leaf_ids", []), "derived_from_ids": v.get("derived_from_ids", [])} for k, v in data["nodes"].items()]
    return {"scope": data["scope"], "leaves": leaves, "nodes": nodes}
def role_tasks(scope: str, *, paths: list[str] | None = None, question: str = "") -> dict[str, dict[str, Any]]:
    view = _brief_overlay(scope)
    candidates = [{"path": p, "signals": ["seed"]} for p in paths or []]
    aggregate = {"node_id": "str", "label": "str", "tags": ["str"], "derived_from_ids": ["leaf/node id"], "layer": "facet|semantic"}
    selector = {"role": "selector", "scope": view["scope"], "question": question,
                "candidates": candidates,
                "rule": "classify every candidate as retained or discarded; retain means downstream review, not high-value choice; when uncertain, retain",
                "output_schema": {"role": "selector", "retained": [{"path": "str", "reason": "str"}],
                                  "discarded": [{"path": "str", "noise_evidence": "str", "reason": "str"}]}}
    base = {"scope": view["scope"], "question": question, "overlay": view}
    return {
        "selector": selector,
        "compressor": {**base, "role": "compressor",
                       "rule": "compress only mature project/tool/service dirs with entry file, project structure, and concrete name; same directory/topic/extension or one weak marker is insufficient; otherwise standalone",
                       "optional_search": {"module": str(Path(__file__).resolve().with_name("search.py")), "api": "search_rows(...); ES only; never filesystem walk"},
                       "output_schema": {"role": "compressor", "targets": [{"target_id": "str", "target_type": "directory|file_group", "label": "str", "brief": "str", "boundary": "str", "supporting_leaf_ids": ["str"]}],
                                         "standalone_leaf_ids": ["str"]}},
        "tagger": {**base, "role": "tagger", "rule": "tag only explicit tag_targets using actual evidence; cover multiple semantic facets per target when supported: topic, artifact type, workflow, domain, project/service, action; avoid single generic tags; support leaves are evidence-only; metadata_only is not file content; optional document_extract may read PDF/Office/text targets; if dependencies are missing, create a temp reader env/tool yourself instead of inventing content",
                   "optional_document_extract": {"module": str(Path(__file__).resolve().with_name("document_extract.py")), "api": "extract_text(path, max_chars=4000) -> {ok,text,error}; on dependency miss, SubAgent may use temp/ tools or env and cite exact method"},
                   "output_schema": {"role": "tagger", "claims": [{"leaf_id": "str or omit for node", "node_id": "str or omit for leaf", "tag": "str", "evidence": "str", "source": "str"}]}},
        "aggregator": {**base, "role": "aggregator", "rule": "aggregate existing claims only; ordered semantic nodes may derive from earlier aggregate nodes",
                       "output_schema": {"role": "aggregator", "facet_nodes": [aggregate], "semantic_nodes": [aggregate]}},
        "auditor": {**base, "role": "auditor", "audit_packet": overlay.audit_packet(scope)["packet"],
                    "output_schema": {"role": "auditor", "verdict": "PASS|FAIL|UNCERTAIN", "evidence": "str", "rework_role": "optional role"}},
    }
def write_task_dir(task_name: str, role: str, task: dict[str, Any]) -> dict[str, str]:
    if role not in ROLE_GUIDE: raise ValueError(f"unknown LSO role: {role}")
    name = Path(task_name)
    if name.is_absolute() or ".." in name.parts: raise ValueError("task_name must stay under temp/")
    repo = Path(__file__).resolve().parents[2]; root = repo / "temp" / name
    root.mkdir(parents=True, exist_ok=True)
    stale = ("artifact.json", "reply.txt", "_history.json", "_stop", "_keyinfo", "_intervene", "stdout.log", "stderr.log")
    for p in [*root.glob("output*.txt"), *(root / x for x in stale)]:
        if p.is_file(): p.unlink()
    artifact = root / "artifact.json"
    context = root / "context.json"
    context.write_text(json.dumps({"role": role, "task": task, "artifact_path": str(artifact)}, ensure_ascii=False, indent=2), encoding="utf-8")
    prompt = f"GA SubAgent LSO role: {role}\n{ROLE_GUIDE[role]}\nRead {context} first. Do only this role. Write formal JSON with top-level role={role} to {artifact}; stdout is summary only."
    (root / "input.txt").write_text(prompt, encoding="utf-8")
    return {"task_dir": str(root), "artifact": str(artifact), "command": f"python agentmain.py --task {task_name} --verbose"}
def read_artifact(task_dir: str | Path) -> dict[str, Any]:
    obj = json.loads((Path(task_dir) / "artifact.json").read_text(encoding="utf-8"))
    if not isinstance(obj, dict) or not obj.get("role"): raise ValueError("invalid artifact.json")
    return obj
def launch_task(task_name: str, worker_args: list[str] | None = None) -> int:
    repo = Path(__file__).resolve().parents[2]
    cmd = [sys.executable, "agentmain.py", "--task", task_name, *(worker_args or ["--verbose"])]
    r = subprocess.run(cmd, cwd=repo, capture_output=True, text=True)
    m = re.search(r"PID:\s*(\d+)", (r.stdout or "") + (r.stderr or ""))
    if not m: raise RuntimeError(f"no worker PID in launcher output: {r.stdout!r} {r.stderr!r}")
    return int(m.group(1))
def _round_done(task_dir: Path) -> bool:
    outs = list(task_dir.glob("output*.txt"))
    if not outs: return False
    latest = max(outs, key=lambda p: p.stat().st_mtime)
    return "[ROUND END]" in latest.read_text(encoding="utf-8", errors="replace")
def wait_artifact(task_dir: str | Path, after: float, timeout_sec: int) -> float | None:
    root = Path(task_dir); art = root / "artifact.json"; start = time.time()
    while time.time() - start < timeout_sec:
        if art.is_file() and art.stat().st_mtime > after and _round_done(root):
            time.sleep(1); return art.stat().st_mtime
        time.sleep(2)
    return None
def close_task(pid: int | str) -> None:
    """Terminate the owned CLI SubAgent after its final artifact."""
    try: os.kill(int(pid), signal.SIGTERM)
    except ProcessLookupError: pass
