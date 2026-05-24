"""LSO A/B/C ablation benchmark — outside semantic core (boundary B5)."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
MEMORY = REPO / "memory"
if str(MEMORY) not in sys.path:
    sys.path.insert(0, str(MEMORY))

from local_semantic_overlay import overlay as ov
from local_semantic_overlay.navigate import NavigateFlags, query
from local_semantic_overlay.overlay import OverlayFlags
from local_semantic_overlay.read import read_leaf
from local_semantic_overlay.search import search_rows
from local_semantic_overlay.select import EvidenceFlags, select_for_read


@dataclass
class RunMetrics:
    mode: str
    ablations: list[str] = field(default_factory=list)
    search_calls: int = 0
    file_reads: int = 0
    overlay_writes: int = 0
    query_hits: int = 0
    fallback_hits: int = 0
    semantic_hits: int = 0


def _run_flow(scope: str, mode: str, *, ablations: list[str]) -> RunMetrics:
    m = RunMetrics(mode=mode, ablations=list(ablations))
    use_search = mode in ("B", "C")
    use_overlay = mode == "C"
    ev_flags = EvidenceFlags(enable_selection="no_selection" not in ablations)
    ov_flags = OverlayFlags(
        enable_leaf_tags="no_leaf_tags" not in ablations,
        enable_compression="no_compression" not in ablations,
        enable_aggregation="no_aggregation" not in ablations,
        enable_active_cold="no_active_cold" not in ablations,
        enable_feedback="no_feedback" not in ablations,
    )
    nav_flags = NavigateFlags(
        enable_semantic=use_overlay and "no_semantic" not in ablations,
        enable_leaf_tags=use_overlay and "no_leaf_tags" not in ablations,
        enable_path=use_overlay and "path_only" not in ablations,
        enable_fallback=use_search and "no_fallback" not in ablations,
    )

    fixture = Path(scope)
    paths = [str(p) for p in fixture.rglob("*.md") if p.is_file()]

    if use_search:
        m.search_calls += 1
        rows = search_rows("evidence", scope=scope, limit=20)
        paths = [r["path"] for r in rows] or paths

    selected = select_for_read(paths, flags=ev_flags, limit=10)
    for row in selected:
        m.file_reads += 1
        rr = read_leaf(row["path"])
        if use_overlay:
            data = ov.load(scope)
            ov.add_leaf(data, row["path"], rr)
            ov.save(data)
            m.overlay_writes += 1
            if ov_flags.enable_leaf_tags and rr.get("read_status") == "readable":
                lid = ov.leaf_id_for_path(row["path"])
                ov.apply_leaf_tags(scope, lid, ["evidence token"], flags=ov_flags)
                m.overlay_writes += 1

    if use_overlay:
        result = query(scope, "evidence", flags=nav_flags)
        m.query_hits = len(result.get("hits") or [])
        m.semantic_hits = sum(1 for h in result["hits"] if h.get("hit_type") == "semantic_node")
        m.fallback_hits = sum(1 for h in result["hits"] if h.get("source") == "fallback")
    return m


def run_experiment(mode: str, scope: str | None = None, *, ablations: list[str] | None = None) -> dict[str, Any]:
    ablations = ablations or []
    if scope is None:
        td = tempfile.TemporaryDirectory()
        scope = td.name
        Path(scope, "sample.md").write_text("Experiment fixture evidence about routing sensors.\n", encoding="utf-8")
    return asdict(_run_flow(scope, mode.upper(), ablations=ablations))


def main() -> None:
    ap = argparse.ArgumentParser(description="LSO A/B/C ablation benchmark")
    ap.add_argument("--mode", choices=["A", "B", "C"], default="C")
    ap.add_argument("--scope", default=None, help="fixture scope directory")
    ap.add_argument("--ablate", action="append", default=[], help="ablation flag, repeatable")
    ap.add_argument("--out", default="-", help="JSON output path or - for stdout")
    args = ap.parse_args()
    text = json.dumps(run_experiment(args.mode, args.scope, ablations=args.ablate), ensure_ascii=False, indent=2)
    if args.out == "-":
        print(text)
    else:
        Path(args.out).write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
