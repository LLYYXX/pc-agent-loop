# LSO Design

LSO is a removable semantic overlay for commonly used, high-value files:

```text
tag -> semantic node -> leaf/node -> file
```

It is not a directory index or full-disk coverage system. Files outside the
overlay remain in ES and can enter the same role pipeline after a query miss.

## Boundaries

- `search.py`: ES/Everything substrate and optional neighborhood lookup
- `config.json`: mechanical recall and hygiene data
- `select.py`: value-signal discovery and mechanical prefilter
- `runner.py`: candidate batches, role order, artifact validation, audit facts,
  and the serial build driver
- `overlay.py`: materialize accepted leaves/tags/nodes, save, and query
- `ga_multiagent.py`: adapter to GA's CLI file-IO SubAgent protocol

Search, config, and Core contain no semantic judgment. GA, Conductor, and
SubAgent runtime code are unchanged.

## Build Flow

1. ES recalls files through recent, maintained, mainstream project-marker, docs,
   task-query, and seed signals. Each bucket has its own budget; the
   deduplicated union is the candidate pool. Maintained means the configured
   creation-to-modification span, independent of recent activity. Cold-start
   discovery skips direct files under a flat parent directory whose direct file
   count exceeds the configured threshold.
2. Independent Selector SubAgents consume candidate-file batches and perform
   recall-preserving noise rejection. Each candidate is retained or discarded
   exactly once. Discard requires explicit noise evidence; uncertain or merely
   ordinary files are retained for downstream review.
3. An independent Compressor proposes only mature project/tool/service directory
   nodes: entry file, project structure, and a concrete name. Shared directory,
   topic, extension, loose document piles, or one weak marker are not enough.
4. Tagger creates multiple evidence-backed, facet-diverse tags on standalone
   leaves and compressed nodes; supporting leaves are evidence-only.
5. Aggregator creates lightweight, potentially multi-level facet and semantic
   nodes from existing claims without re-reading source files.
6. Auditor independently reviews the role products and map. Core only validates,
   materializes, saves, and queries. A rework verdict mechanically rewinds that
   role and its downstream products before re-dispatch.

After an LSO query miss, ES hits are passed back through
`prepare(scope, question=query, seeds=hits, reset=False)`. Existing leaves are
removed from the incremental candidate pool; only the explicit hits follow the
same independent roles. An incremental call without seeds performs a broader
value-signal refresh.

The control plane persists `candidate_pool.json`, `selector_ledger.json`,
accepted current-run role artifacts, `base_overlay.json`, `build_state.json`,
and `coverage_report.json`. Rework restores the stable base and replays only
accepted upstream artifacts. It never turns physical directories into required
coverage buckets.

## Data Model

- Leaf: retained real file
- Compressed node: cohesive directory or file group supported by leaves
- Aggregate node: semantic grouping derived from existing leaves/nodes/tags
- Tag claim: target, tag, evidence, and evidence source

A leaf may support several nodes. Nodes retain `supporting_leaf_ids`; aggregate
lineage uses `derived_from_ids`. No ignored node/tag or complex edge model exists.

## Lightweight Constraint

Final code is `__init__.py + overlay.py + select.py + runner.py +
ga_multiagent.py < 600` physical lines, excluding `search.py`,
`document_extract.py`, config, tests, and the verifier. `runner.py < 300`.
