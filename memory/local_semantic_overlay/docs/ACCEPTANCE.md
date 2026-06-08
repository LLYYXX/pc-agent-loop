# LSO Acceptance Contract

## Product

1. Product shape is `tag -> semantic node -> leaf/node -> file`.
2. LSO covers commonly used, high-value files; it does not require physical
   region or full-disk coverage.
3. Cold files remain in ES. Absence from LSO creates no ignored tag.
4. One file may support multiple semantic nodes; aggregate nodes may form more
   than one semantic level.
5. Nodes keep `supporting_leaf_ids`; `derived_from_ids` is lineage only.
6. Core validates, materializes, saves, and queries. It makes no semantic
   judgments.

## Build

7. Discovery uses ES/Everything only and configurable per-signal recall budgets.
   Long-maintained means `modified_time - created_time >= configured days`; it
   is not redefined as recent activity.
8. The deduplicated bucket union is not globally truncated before semantic
   filtering.
   Direct files under a flat parent directory with more than the configured
   file-count threshold are skipped in cold-start discovery.
9. Selector is a recall-preserving noise rejector. It classifies every batch
   item exactly once as `retained` or `discarded`; uncertain and merely ordinary
   files are retained. Every discard requires explicit noise evidence. It
   creates no tags or nodes and receives no count target.
10. Compressor is an independent role. It may compress only mature
    project/tool/service directories with an entry file, project structure, and
    concrete name; shared directory/topic/extension or one weak marker is not
    enough. No complete-directory read is required or implied.
11. Compressor accounts for every retained leaf through a target or
    `standalone_leaf_ids`.
12. Tagger claims require an explicit Compressor tag target, tag, evidence, and
    source. Only standalone leaves and compressed nodes are tag targets;
    supporting leaves are evidence-only. Targets should receive multiple
    facet-diverse tags when evidence supports it; systematic single-tag targets
    require rework unless justified. Difficult formats may use explicit
    non-content evidence but cannot pretend it is content.
13. Aggregator uses existing artifacts only and creates support/derivation
    relationships, not a complex knowledge graph.
14. Auditor is independent, never writes the overlay, and returns evidence for
    `rework_role` when it does not pass. The control plane re-dispatches that
    role after mechanically rewinding that role and downstream materialization.
    It judges semantic usefulness, including systematic single-tag targets and
    shallow aggregation. Mechanical audit facts never become a code gate; the
    Auditor verdict decides.

## Integration

15. Cold start uses GA's existing CLI file-IO SubAgent protocol from
    `memory/subagent.md`; LSO modifies no GA, Conductor, or SubAgent body code.
16. Each role writes formal `artifact.json`; stdout is never parsed as an
    artifact.
17. The main Agent dispatches and observes only. It does not act as a role,
    generate role scripts, edit `artifact.json`, trim fields, delete invalid
    claims, or otherwise repair artifacts. Rejected artifacts are returned
    through `reply.txt` and rewritten by the same role SubAgent.
18. LSO dispatch is serial. It retains the CLI SubAgent's printed PID and closes
    the current process before dispatching the next role.
19. No case-specific path, domain, or user-sample rules exist in code, config,
    prompts, SOP, tests, or docs.

## Size And Checks

- Final code (`__init__.py + overlay.py + select.py + runner.py +
  ga_multiagent.py`) is under 600 physical lines.
- `runner.py` is under 300 physical lines.
- `search.py`, `document_extract.py`, config, verifier, and tests are excluded
  from final code.
- `search.py`, `config.json`, verifier, and tests are excluded from final code.

```text
python memory/local_semantic_overlay/verify_lines.py
python -m unittest test_lso.test_compact_lso -v
```
