# LSO Agent SOP

LSO 是本地文件任务的轻量语义覆盖工具。Agent 只在需要反复发现、复用、组织某个目录范围内的文件证据时使用它。

## 什么时候用

使用：

- 用户给出固定目录或明确 scope。
- 任务需要在该范围内反复找文件、读证据、复用已有判断。
- 任务需要沉淀“哪些文件和哪些概念相关”。

不使用：

- 只读写一个明确文件。
- 只做一次路径搜索。
- 任务不依赖目录内文件证据复用。

## 基本约束

- Runtime 只查询 overlay，不写 overlay。
- 只有 Build 阶段能写 leaf tags、metadata、nodes。
- fallback 搜索结果只作为下一轮候选，不自动入库。
- `semantic_tags` 不是 Agent 输入，而是 core 审计通过后的输出。
- Agent 只能提交 TagProposal，不能直接写 `semantic_tags`。
- `content_semantic` proposal 必须有 `evidence_phrase` 和 `evidence_source`。
- `content_semantic.evidence_source` 只能是 `text_head`。
- filename 只作为 `filename_hint` 返回和匹配，不能作为 semantic evidence。
- path、目录、来源渠道只能作为 `location` / `source_channel` metadata，不能进入 `content_semantic`。
- metadata-only 不算 semantic coverage。
- leaf-only 样本不能表述成完整语义覆盖。
- nodes=0 时不得暗示已经形成 compression / aggregation 节点。
- 结果表述禁止使用“完成”“所有”“完整”，除非有穷尽性证据。

## Build

```python
import json
import os
import sys

ROOT = "/abs/path/to/pc-agent-loop"
sys.path.insert(0, os.path.join(ROOT, "memory"))

import local_semantic_overlay as lso

SCOPE = "/abs/path/to/task/root"

lso.ensure_search_ready()

session = lso.BuildSession(SCOPE)
rows = lso.search_rows("关键词", scope=SCOPE, limit=30)
paths = [r["path"] for r in rows]
session.add_candidates(len(paths))

for row in lso.select_for_read(paths, seeds=[], fallback_seeds=[], limit=15):
    session.try_read(row["path"])
    leaf_id = session.ensure_leaf(row["path"])

    prep = lso.prepare_leaf_tag_task(SCOPE, leaf_id)
    if not prep["ok"]:
        continue

    task = prep["task"]
    # readable leaf: task["allowed_evidence_sources"] == ["text_head"]
    # non-readable leaf: task["allowed_evidence_sources"] == []
    # Agent reads task["text_head"] for semantic proposals; task["filename_hint"] is a hint only.
    proposals = [
        {
            "tag": "跨境贸易支付",
            "evidence_phrase": "高性能可信跨境贸易支付监管关键技术研究",
            "evidence_source": "text_head",
            "tag_role": "content_semantic",
        }
    ]
    session.propose_tags(leaf_id, proposals)

lso.enforce_active_budget(SCOPE)
print(json.dumps(session.finalize(), ensure_ascii=False, indent=2))
```

Evidence channels:

| leaf 状态 | semantic evidence | filename channel |
| --- | --- | --- |
| `read_status == "readable"` | `text_head` | `filename_hint` |
| `read_status != "readable"` | 无 | `filename_hint` |

`filename_hint` 可以帮助召回和展示，但不产生 `semantic_tags`。

## TagProposal

```python
proposals = [
    {
        "tag": "跨境贸易支付",
        "evidence_phrase": "高性能可信跨境贸易支付监管关键技术研究",
        "evidence_source": "text_head",
        "tag_role": "content_semantic",       # content_semantic | location | source_channel
    }
]

result = lso.propose_leaf_tags(SCOPE, leaf_id, proposals)
```

`tag_role` 路由：

| tag_role | 写入字段 | evidence |
| --- | --- | --- |
| `content_semantic` | `semantic_tags` | 必须 |
| `location` | `location_tags` | 不需要 |
| `source_channel` | `source_channel` | 不需要，单值 |

`propose_leaf_tags` 成功路径是 full replacement：本次通过审计的结果会完整替换 leaf 上的 `semantic_tags`、`location_tags`、`source_channel`。如果要保留旧 tag，必须在本次 proposals 中重新提交。

错误路径不改 leaf，例如所有 proposals 被拒绝时返回 `no_tags_accepted`。

返回值要点：

```python
{
    "ok": bool,
    "error": None | str,
    "accepted": [{"tag": str, "evidence_phrase": str, "evidence_source": str, "tag_role": str}],
    "rejected": [{"tag": str, "evidence_phrase": str, "evidence_source": str, "tag_role": str, "reason": str}],
    "semantic_tags": list[str],
    "metadata": {"location_tags": list[str], "source_channel": str | None},
    "semantic_applied": bool,
    "metadata_applied": bool,
}
```

`ok=True` 不等于 `semantic_applied=True`。metadata-only 写入应报告为 `semantic_applied=False, metadata_applied=True`。

## Build Audit

`session.finalize()["process"]` 是构建总结的唯一统计来源。关键字段：

```text
candidate_path_count
selected_count
readable_count
skipped_count
proposal_count
proposal_accepted
proposal_rejected
semantic_applied_count
metadata_applied_count
semantic_apply_ok
metadata_apply_ok
evidence_source_text_head
apply_ok
apply_fail
unique_leaf_before
unique_leaf_after
unique_leaf_delta
proposal_log
```

构建总结必须引用 `semantic_apply_ok` 作为 semantic coverage 入口，不能用 metadata-only 或 apply attempt 冒充语义覆盖。

## Runtime

```python
res = lso.query(SCOPE, "检索词", limit=20)
fallback_seeds = []

for h in res["hits"]:
    if h["hit_type"] == "semantic_node":
        rh = lso.record_hit(SCOPE, h["node_id"])
        if rh.get("action") == "needs_recheck":
            task = lso.recheck_cold_node(SCOPE, h["node_id"])
            # Read task["task"], then call apply_recheck with Agent decision.
            # lso.apply_recheck(SCOPE, h["node_id"], {"decision": "keep|delete|update", ...})
    elif h["hit_type"] == "leaf_tag":
        pass  # read h["path"]
    elif h["hit_type"] in ("filename_hint", "metadata", "path"):
        pass  # inspect h["match_reasons"] before reading
    elif h["hit_type"] == "fallback":
        fallback_seeds.append(h["path"])
```

| hit_type | 含义 |
| --- | --- |
| `semantic_node` | 来自 compressed / aggregated node |
| `leaf_tag` | 来自已审计 leaf semantic tags |
| `filename_hint` | 文件名主体命中，不等于内容语义 |
| `metadata` | `location_tags` / `source_channel` 命中 |
| `path` | 路径字面命中 |
| `fallback` | 搜索适配器召回，只能作为下一轮候选 |

每个 hit 都应查看 `match_reasons`。它说明命中来自 `semantic_tags`、`filename_hint`、`source_channel`、`location_tags`、`path` 还是 `fallback`，避免把来源/文件名命中误读成内容语义。

## Feedback

只有显式反馈才能写入 feedback：

```python
lso.record_feedback(SCOPE, result_id="q1", kind="selected", node_id="node_xxx")
lso.record_feedback(SCOPE, result_id="q1", kind="not_selected", node_id="node_xxx")
lso.record_feedback(SCOPE, result_id="q1", kind="negative", node_id="node_xxx")
```

不要把“query 没命中”推断成负反馈。

## Optional Build

Compression:

```python
prep = lso.prepare_compression_task(SCOPE, SCOPE)
result = {"decision": "compress", "label": "...", "tags": ["..."], "brief": "..."}
lso.apply_compression(SCOPE, SCOPE, result)
```

Aggregation:

```python
prep = lso.prepare_aggregation_task(SCOPE)
result = {
    "decision": "aggregate",
    "label": "...",
    "tags": ["..."],
    "derived_from_ids": ["leaf_xxx", "node_yyy"],
    "brief": "...",
}
lso.apply_aggregation(SCOPE, result)
```

Node 的 evidence 约束体现在 `supporting_leaf_ids` 和 grounded `brief`，不是每个 node tag 单独 evidence phrase。

## 常见拒绝原因

| reason | 含义 |
| --- | --- |
| `missing_evidence` | content semantic 没有 evidence phrase |
| `weak_evidence` | evidence phrase 太短 |
| `invalid_tag_role` | tag_role 非法 |
| `invalid_evidence_source` | content semantic evidence_source 非 `text_head` |
| `no_evidence_source` | 请求的 evidence source 在该 leaf 不可用 |
| `evidence_not_grounded` | evidence phrase 不能贴回对应证据 |
| `duplicate_tag` | 同批 proposal 中 tag 规范化后重复 |
| `multiple_source_channel` | 同批 proposal 出现多个 source_channel |
| `tag_is_extension` | content semantic tag 等于扩展名 |
| `tag_is_dir_token` | content semantic tag 等于父目录 token |
| `no_tags_accepted` | 没有任何 proposal 通过 |
| `brief_not_grounded` | node brief 不能贴回 supporting leaves |
| `recursive_aggregation` | aggregation 递归引用 aggregated node |
