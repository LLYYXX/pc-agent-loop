# Local Semantic Overlay SOP (v2 — Area-Aware Annotation-First)

Local Semantic Overlay (LSO) is a semantic overlay above the local filesystem
and Everything/es. It is not a file-level index, not a batch builder, and not an
Agent hook. Use it only through explicit calls.

## Import

```python
from local_semantic_overlay import *
```

Do not import internal modules unless debugging.

## Cold-Start Flow

Cold start builds an area-aware, annotation-first semantic overview.

```python
session = begin_seed_map(scope="F:\\", route_budget=40)
session_id = session["session_id"]

survey_scope(session_id)

collect_area_evidence(session_id)

packet = next_seed_packet(session_id, packet_size=6)

apply_file_annotations(session_id, annotations=[
    {
        "evidence_id": "ev_xxx",
        "decision": "annotate",
        "tags": ["machine-learning", "pytorch-training"],
        "value_reason": "Core ML training scripts",
        "evidence_summary": "Contains model training and evaluation code",
        "confidence": 0.8,
    },
    {
        "evidence_id": "ev_yyy",
        "decision": "defer",
    },
])

propose_route_nodes(session_id)

apply_route_cards(session_id, route_cards=[
    {
        "title": "Machine Learning Workspace",
        "brief": "PyTorch training and evaluation code",
        "use_when": "Tasks involving ML model training, evaluation, or data prep",
        "anchor_path": "F:\\Projects\\ml-workspace",
        "entrypoints": ["F:\\Projects\\ml-workspace\\train.py"],
        "supporting_annotation_ids": ["ann_xxx"],
        "tags": ["machine-learning", "pytorch-training"],
        "route_terms": ["pytorch", "训练", "模型"],
        "route_meta": {
            "positive_cues": ["pytorch", "training", "模型"],
            "negative_cues": ["unity", "游戏"],
            "boundary_note": "Only ML code, not data storage",
        },
    },
])

report = finish_seed_map(session_id)
overview = system_overview()
```

### Cold-Start Rules

- `survey_scope()` creates an area ledger; every scanned directory has a status.
- `collect_area_evidence()` samples per-area, not global top-N.
- `apply_file_annotations()` is how the LLM judges file-level value.
  Annotations are first-class runtime assets even without routes.
- Routes are lifted from annotations only, via `apply_route_cards()`.
- `finish_seed_map()` is a hard validator:
  - `status="incomplete"` means gaps exist — not a success.
  - `status="failed"` means no usable overlay was built.
  - `next_required_actions` lists what must be done.
- `route_budget` is an upper limit, not a target.

### Annotation Schema

`apply_file_annotations()` accepts aliases:
- `file_id` / `candidate_id` → `evidence_id`
- `action` → `decision`

Decisions: `annotate`, `needs_more_evidence`, `defer`, `ignore_noise`.

Tag rules: no path-only tags, no generic tags
(`project/document/research/code/file/folder/misc/general`).

## Runtime Flow

```python
res = run_file_query(query, scope=optional_scope, limit=20)

# Inspect layers separately:
# res["route_hits"]           — semantic routes
# res["file_annotation_hits"] — file annotations
# res["deferred_hits"]        — pending items
# res["search_hits"]          — Everything/es fallback

finish_file_query(
    res,
    used=[...],
    found=[...],
    rejected=[...],
    selected_routes=[...],
    selected_annotations=[...],
)
```

### Runtime Rules

- `finish_file_query()` or `finish_local_file_task()` is required for learning.
  Recall alone does not warm routes or create update plans.
- File annotations are queryable without routes.
- `search_files_rows` / `search_files_paths` are thin Everything/es wrappers:
  no semantic expansion, no rerank, no tag inference.
- Fallback does not masquerade as a route hit.
- `recommended_next_action` suggests: `use_route`, `inspect_annotation`,
  `fallback_search`, or `ask_user`.

## Recall Layers

1. **Route recall** — semantic routes with cue/tag/term matching.
2. **File annotation recall** — first-class annotation assets.
3. **Deferred evidence** — items awaiting annotation or lift.
4. **Everything/es fallback** — raw terrain, factual but uncompressed.

## Route Semantics

- Routes are only created from evidence-backed annotations.
- Active routes require: `supporting_annotation_ids`, `entrypoints`,
  `anchor_path`, `brief`, `use_when`, evidence-backed tags.
- Seeded routes default to `usage_verification="seeded"`, `tier="warm"`.
- Low-confidence routes go to `candidate` or `deferred`, not `active`.
- `route_budget` is always an upper limit.

## Search Substrate

```python
search_files_rows(query, scope=None, limit=50)   # -> list[dict]
search_files_paths(query, scope=None, limit=50)   # -> list[str]
search_files_detailed(query, scope=None, limit=50) # -> dict
last_search_diagnostic()                           # -> dict
```

`ensure_search_ready(start=True, patch=True)` may locate or start Everything/es.
It must not write semantic overlay content into global memory.

## Maintenance

Tiers: `active` → `warm` → `cold`. `maintenance_tick()` rebalances
opportunistically. Annotations are never demoted; routes are.

## Audits

- `audit_lso()` — route/annotation health.
- `audit_runtime()` — workflow drift (unfinalised recalls, unclosed feedback).

Important warnings:
- Route missing annotations or entrypoints
- Route anchor is tech noise
- Generic route tags
- Annotations not linked to routes
- Recall not finalized
- Fallback found paths without update plan

## Out Of Scope

LSO does not implement batch indexing, watchers, file-read hooks, or changes to
the Agent main loop.
