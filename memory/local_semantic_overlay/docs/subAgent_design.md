# LSO Multi-Agent Design

LSO mounts on GA's existing CLI file-IO SubAgent protocol. LSO does not start or
modify GA, Conductor, or SubAgent runtime code.

## Protocol

For the current role task, the main Agent calls `write_task_dir()`, launches the
returned `python agentmain.py --task <name> --verbose`, observes `output*.txt`,
retains the printed child PID, submits `artifact.json` through
`apply_task_artifact()`, then calls `close_task(pid)` before dispatching the next
role.

The main Agent never performs role work or creates an artifact. Invalid output is
returned to the same SubAgent through `reply.txt`; the main Agent must not edit
or normalize `artifact.json`. LSO keeps at most one SubAgent alive; the current
process remains alive only while correction is pending.

## Roles And Contracts

| Role | Input | Output |
| --- | --- | --- |
| selector | current candidate-file batch and value signals | `retained`, `discarded` |
| compressor | retained leaves and path/organization evidence | `targets`, `standalone_leaf_ids` |
| tagger | standalone leaves and compressed nodes | evidence-backed `claims` |
| aggregator | existing tags, claims, leaves, and nodes | `facet_nodes`, `semantic_nodes` |
| auditor | map, audit packet, candidate/decision ledgers | independent verdict and evidence |

Selector batches are independent semantic-filtering contexts but advance
sequentially through one persisted candidate pool. Compressor is separate
because deciding whether evidence supports a directory/file-group boundary is
different from deciding whether a candidate file is valuable.

Compressor may use ES to inspect nearby structure on demand. Such lookup is a
role tool choice, not a global expansion state. A directory node is valid only
for mature project/tool/service boundaries with entry file, project structure,
and concrete name. Same-directory files, loose documents, common topic, common
extension, or one weak marker stay as standalone leaves.

Tagger may read source content, but may tag only the Compressor's explicit
standalone leaves and compressed nodes; supporting leaves are evidence-only.
For difficult formats it must state the actual evidence channel used. For each
target it should produce multiple evidence-backed tags across different semantic
facets when evidence supports it; repeated single generic tags are a rework
signal.
Aggregator does not read source content. Auditor never writes overlay state. A
failing Auditor names `rework_role`; the control plane rewinds that role and
downstream products, then returns its next task with the audit evidence.

## Control Plane

```text
candidate recall
-> selector batches
-> compressor
-> tagger
-> aggregator
-> auditor
```

`runner.py` enforces role order, schemas, complete Selector-batch consumption,
Compressor leaf coverage, materialization validity, and serial SubAgent
dispatch. Semantic relevance,
cohesion, tags, aggregation, and final quality remain Agent judgments.
Incremental runs retain a stable base overlay; rework restores that base and
mechanically replays accepted current-run artifacts before the named role.
