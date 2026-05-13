# Local Semantic Overlay SOP

Local Semantic Overlay (LSO) is a semantic overlay on top of the local
filesystem and Everything/es. It is not a file-level search index, not a
cold-start batch builder, and not an Agent hook.

Use LSO when a local file task would otherwise require broad `es` search,
directory enumeration, or repeated file reads. LSO should return compact
semantic route cards first, then evidence only when a route is selected.

## Import

```python
from local_semantic_overlay import *
```

Do not import internal modules unless debugging the implementation.

## Default Runtime Flow

```python
res = run_file_query(query, scope=optional_scope, limit=20)
hits = res["all_hits"]
route_hits = res["route_hits"]

# If a route looks useful, inspect it before reading broad filesystem data.
detail = expand_route(route_id, query=query, budget="normal")

# If routes are insufficient, fall back to raw terrain search. These return
# plain lists, not envelope dicts.
rows = search_files_rows(query, scope=None, limit=50)
paths = search_files_paths(query, scope=None, limit=50)

finish_local_file_task(
    query=query,
    used=[...],
    found=[...],
    rejected=[...],
    selected_routes=[...],
)
```

## Resolution Model

- Filesystem / Everything: raw terrain. It is factual and detailed, but not
  semantically compressed.
- Evidence paths: proof and expansion material for semantic routes. They are
  not the default recall unit.
- Semantic routes: the default LSO recall unit. A route is an actionable
  middle-layer entry point such as a workspace, material collection, project
  area, or stable task route.
- Overview: a coarse low-token map for broad takeover tasks.

## Route Rules

- `recall_routes()` must return route cards, not raw leaf hits.
- Prefer `recall_hits()` or `run_file_query()` for task code so you do not
  need to remember result field names.
- `expand_route()` is the normal way to see anchors, evidence paths, scoped
  search hints, and cautions.
- A fallback `es` hit is a raw-terrain candidate, not semantic knowledge.
- `finish_local_file_task()` records which routes and paths were actually
  used. Recall alone does not warm a route.
- External `used` or `found` paths create draft update plans. They do not
  become routes until an update plan is applied.
- Rejected paths and user corrections are negative evidence and must be
  recorded.
- User corrections can be recorded with `record_correction(...)`; route label
  fixes go through `update_route_tags(route_id, add=[...], remove=[...],
  evidence_note="...")`.

## Search Substrate

`search_files_detailed`, `search_files_rows`, and `search_files_paths` are thin
Everything/es wrappers:

- no semantic query expansion
- no rerank
- no route tag inference
- no path or case-specific rules
- diagnostics include argv, scope, encoding, stderr, and hit_count

Return contracts:

```python
search_files_rows(query, scope=None, limit=50)  # -> list[dict]
search_files_paths(query, scope=None, limit=50) # -> list[str]
search_files_detailed(query, scope=None, limit=50) # -> dict with rows/hits/paths plus argv/encoding/stderr/hit_count
last_search_diagnostic() # -> last detailed diagnostics
```

`ensure_search_ready(start=True, patch=True)` may locate and start Everything
or es using local environment facts such as `memory/global_mem.txt`. It must not
write semantic overlay content into global memory.

## Maintenance

Tier applies to semantic routes only:

- `active`: recently truly used route
- `warm`: stable semantic route
- `cold`: low-use or low-confidence route

`maintenance_tick()` opportunistically rebalances route tiers. Ordinary evidence
paths are never promoted into default recall units merely because they were
found.

## Seed-Map Cold Start

Cold start is a seed-map workflow, not a batch indexing workflow. Its goal is to
make an empty LSO useful on first recall by creating predicted semantic routes
with evidence, task affordances, and scoped search hints.

Use it like this:

```python
session = begin_seed_map(scope="F:\\", route_budget=40)
survey = survey_scope(session["session"]["session_id"])
cluster_result = list_terrain_clusters(session["session"]["session_id"], limit=20)
clusters = cluster_result["clusters"]

packet = expand_cluster_evidence(session_id, cluster_id, budget="normal")

commit_seed_route(
    session_id,
    cluster_id,
    title="short semantic label",
    brief="what this route lets the Agent do, based on evidence",
    task_affordances=[...],
    search_hints=[{"scope": "...", "query": "..."}],
    evidence_refs=["ev_..."],
    uncertainty_note="what is still not verified",
)

report = finish_seed_map(session_id)
```

For a conservative automatic pass:

```python
result = seed_lso_routes(scope="F:\\", route_budget=40)
print(result["status"], result["routes_created"], result["routes_reused"])
print(result["coverage_report"]["coverage_by_weight"])
```

Seed-map rules:

- `route_budget` is an upper bound, never a target.
- Success is measured by whether the scope is well summarized, not by route
  count.
- A predicted route may have strong evidence, but it still has no observed
  usage.
- Predicted routes use `usage_verification="predicted"`, `usage_score=0`, and
  `tier="cold"`.
- `route_terms` are evidence-derived recall cues. They are not route tags.
- `route_tags` should be empty unless the Agent has strong evidence and a
  specific route purpose.
- Evidence-insufficient clusters are `deferred` or `unknown`, not skipped.
- `finish_local_file_task()` converts a selected predicted route to observed
  only after real task use.

Coverage report statuses:

- `complete`: major high-signal clusters are mapped, route cards are task-ready,
  and unknown/deferred areas are explicit.
- `usable_partial`: seed routes should improve first recall, but important
  clusters remain unknown or deferred.
- `incomplete`: clusters exist, but the map is too weak to act as an overview.
- `protocol_unhealthy`: quantity-driven, file-type/tag-driven, or template-like
  seed routes dominate.

## Audit

Use `audit_lso()` when behavior looks wrong. It should flag:

- route cards that look like single leaf files
- missing anchors or evidence
- generic or file-type route tags
- unresolved update plans
- stale active routes
- recall results that are never consumed
- fallback results that were not finalized into update plans

Use `audit_runtime()` when the problem is workflow drift: recalled routes not
expanded/consumed, fallback hits not finalized, or corrections not recorded.

## Out of Scope

LSO does not implement batch indexing, watchers, file-read hooks, or changes to
the Agent main loop.
