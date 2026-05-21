# Local Semantic Overlay SOP (v3 — Readable-Evidence-Bounded)

LSO is a **semantic map layer** over the filesystem and Everything/es. It is not a search engine, not a cold-start builder, and not a route generator.

## Runtime (code_run)

`code_run` prepends `memory/` to `sys.path` via `assets/code_run_header.py`. Use **only**:

```python
from local_semantic_overlay import (...)
```

Do **not** use `from memory.local_semantic_overlay import ...`, do **not** probe `sys.path` or `memory/__init__.py`.

*(Local pytest from repo root may use `memory.local_semantic_overlay` — that path is for tests only.)*

## Import (explicit)

```python
from local_semantic_overlay import (
    begin_ingestion,
    discover_leaf_seeds,
    compress_directories_from_key_evidence,
    mark_compress_done,
    next_evidence_bundle,
    apply_bundle_annotations,
    apply_session_leaf_tags,
    aggregate_directory_tags,
    build_overview,
    finish_ingestion,
    audit_phase_a,
    run_file_query,
    finish_file_query,
    system_overview,
)
```

## Phase A workflow (evidence bundle)

Default budgets: `candidate_leaf_budget=200`, `annotation_budget=30`, `bundle_budget=6`.
Partial coverage is expected when budgets are finite — see `agent_report` / `cost_metrics`, not `finish_ingestion` status alone.

```python
beg = begin_ingestion(
    scope=r"F:\YourScope",
    candidate_leaf_budget=200,
    annotation_budget=30,
    bundle_budget=6,
)
sid = beg["session_id"]

discover_leaf_seeds(sid)
compress_directories_from_key_evidence(sid)
mark_compress_done(sid)

while True:
    out = next_evidence_bundle(sid)
    b = out.get("bundle")
    if not b:
        break
    # LLM returns structured JSON per bundle_annotation_schema()
    apply_bundle_annotations(b["bundle_id"], {...}, session_id=sid)

aggregate_directory_tags(sid)
build_overview(sid)

report = finish_ingestion(sid)

# REQUIRED — status is flow-only; coverage is in diagnostics
print(report["agent_report"])
print(report["diagnostics_summary"])
print(report.get("cost_metrics"))

audit = audit_phase_a()  # structural only
```

**Evidence bundle rules:**

- Bundle is **ingestion-time scheduling only** — not a runtime semantic asset.
- **Seed-first**: same global pending pool and sort as `next_tagging_packet` (`readable` + `seed` + no `leaf_tag_edges`, session-scoped); `primary_leaf` = queue head; `anchor_directory` = its parent; key evidence + same-anchor pending candidates add LLM context only.
- `anchor_directory` is for **positioning only** — never tag/evidence from directory names.
- Each tag needs **leaf-specific `evidence_note`** from that leaf's `text_head` / `evidence_title`.
- Use `defer_leaf_ids` for uncertainty; **primary must be tagged (non-empty tags) or deferred** each `apply_bundle_annotations` call.
- `compression_basis` supplements directory `org_signals` — does **not** replace `leaf_tag_edges`.
- No bulk heuristic tagger; `apply_bundle_annotations` respects `annotation_budget` and bulk gate (12).
- While an ingestion session is **open**, `apply_leaf_tags()` without `session_id` is rejected for any leaf in that session unless `source` is `bundle` (internal). Use `apply_bundle_annotations` or `apply_session_leaf_tags(session_id, ...)`.
- Bundle payload caps: `DEFAULT_BUNDLE_KEY_EVIDENCE_CAP`, `DEFAULT_BUNDLE_CANDIDATE_CAP`, `DEFAULT_BUNDLE_TEXT_HEAD_CHARS`; `apply_bundle_annotations` rejects leaf ids outside the registered bundle set.
- Default standard flow stops when `bundle_budget` is exhausted: aggregate, build overview, finish, and report blind spots. Do not spend remaining `annotation_budget` on tail packets unless the user explicitly asks for deep/tail expansion.

### Explicit deep/tail leaf packet loop

Use only when the user explicitly asks to continue beyond the standard bundle budget. Same pending pool as bundle; apply through `apply_session_leaf_tags()` so tail work consumes the same `annotation_budget`.

```python
from local_semantic_overlay import next_tagging_packet, apply_session_leaf_tags

pkt = next_tagging_packet(limit=6, session_id=sid, mode="tail")
apply_session_leaf_tags(sid, [...])  # per leaf_tag_schema(); budget-accounted
```

### Optional: `run_ingestion_step(sid)`

Returns **suggested** `next_action` only — does not run discover/compress/aggregate/LLM.
Uses `preview_next_evidence_bundle` (no register, no session state write). **Register** (+ stale pending hygiene) only via `next_evidence_bundle(sid)`.

## Runtime query

```python
result = run_file_query("pytorch training", scope=r"F:\YourScope")

finish_file_query(
    result,
    used=[r"F:\YourScope\path\used.py"],
    found=[r"F:\YourScope\path\discovered.py"],
    rejected=[],
)

ov = system_overview(max_chars=2000)
print(ov["overview"])
print(ov["partial_map_warning"])
print(ov["macro_coverage_note"])
print(ov["coverage_basis"])
```

## Rules

- Only **readable** leaves get semantic tags (`text_head` + `evidence_note`).
- Overview titles use `evidence_title` rules — not directory names, not raw dumps.
- No generic tags (`document`, `file`, `code`, `pdf`, …).
- Seed sources: `recent`, `long_maintained`, `key_evidence`, `user_confirmed`, `fallback_found` only.
- **No bulk heuristic tagger scripts.**
- `apply_leaf_tags` / `apply_bundle_annotations` reject batches > 12 leaves per call.

### Pitfalls (verified)

- **All `allowed_leaf_ids` must be resolved**: every leaf in `allowed_leaf_ids` must appear in either `leaf_annotations` or `defer_leaf_ids`, otherwise `ok: false`.
- **Already-tagged leaves cannot be deferred**: if a leaf was tagged in a prior bundle, including it in `defer_leaf_ids` returns `not_pending_tag_target`. Exclude them.
- **Tags must not derive from filename/path/extension**: e.g. using the document filename as a tag triggers `tag derived from path/filename/extension` rejection. Use semantic/content-based tags.
- **`evidence_note` must be detailed**: short generic notes (< ~30 chars) fail with `evidence_note too short`. Write a sentence describing the leaf's actual content.

## Macro semantics

- **Key-evidence compression role** = `org_signals.compression_role == "key_evidence_macro"` + `key_evidence_leaf_ids`.
- `node_type` may be `semantic_node` while compression role is present — see `macro_nodes` with real `node_type`.
- Bundle vs macro: macro = rule-side directory compression; bundle = LLM input scheduling for leaf tags.

## Design Constraints

- Current overview rebuild is single-session/single-active-DB oriented: `build_overview(session_id)` filters which nodes it rebuilds, but overview storage is still globally cleared before writing. Multi-session or old-DB reuse requires changing overview write strategy first, either session-scoped incremental writes or scoped delete. Do not treat this as already supported behavior.

## Module boundaries

| Module | Role |
|--------|------|
| `evidence_bundle` | `next_evidence_bundle`, `apply_bundle_annotations` (ingestion-time only) |
| `evidence_title` | Rule-based titles |
| `diagnostics` | diagnostics, `cost_metrics`, `format_agent_report` |
| `leaf_seed` | seed discovery |
| `directory_macro` | key-evidence compression |
| `overview_build` | overview_entries |
| `ingestion` | orchestration, budgets, `run_ingestion_step` hint |
| `runtime` | query + `system_overview` |
| `leaf_tagging` | tags + validation; `next_tagging_packet` shares pending pool with bundle; `apply_session_leaf_tags` is the budget-accounted tail apply path |
