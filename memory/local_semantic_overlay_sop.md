# LSO Agent SOP

LSO 是本地文件任务的轻量语义覆盖工具：在明确 `scope` 内沉淀可复用、可审计、可导航的文件证据层。它不是一次性找文件工具，也不是 coverage planner；目录观察和采样规划由 Agent 用普通文件系统能力完成。

## 0. 核心纪律

只在以下场景使用 LSO：用户给出固定 scope，且任务需要在该范围内反复找文件、读证据、复用判断。不用于单文件读写、一次性路径搜索、或不依赖文件证据复用的任务。

scope 已明确且可访问时，不因 scope 大而停止；改为 bounded sample 并声明边界。scope 不存在、权限不可读、目标含糊时，先澄清或报告阻塞。

Agent 使用 LSO 时必须区分三层结果：

| 状态 | 含义 | 不得越级表述 |
| --- | --- | --- |
| `leaf_sample_built` | 只完成文件级证据沉淀 | 不得说完整覆盖或 node coverage |
| `node_coverage_built` | 本轮新增/更新 node，或复用与当前任务相关的已有 node | 不得说 runtime 有效 |
| `runtime_validated` | 已用代表性查询验证 overlay 对任务有帮助 | 仍须说明验证边界 |

禁止把 `session.finalize()`、`node_count > 0`、metadata-only、filename/path/fallback 命中说成“覆盖完成”。

## 1. Coverage Task 流程

一次 coverage task 按以下阶段执行。API 只是执行这些阶段的工具。

| 阶段 | 必做事项 | 产物 |
| --- | --- | --- |
| A. Scope & Boundary | 记录 `scope`、`scope_type`、`build_mode`、未覆盖范围 | 本轮边界 |
| B. Candidate Policy | 综合用户关键词、目录/anchor 观察、高价值文件类型、已有 overlay、fallback seeds；不得只用自编关键词 | candidate paths |
| C. Leaf Evidence Build | select/read/ensure/proposal/apply | `BuildSession.finalize()` |
| D. Node Eligibility Check | 用 `top_anchors`、本轮 selected/readable paths、`proposal_log` 判断是否尝试 compression/aggregation | node check result |
| E. Runtime Validation | 仅当要声明 `runtime_validated` 时，跑 2-5 个代表性 query | hit_type 分布 |
| F. Bounded Report | 用固定模板报告本轮状态和边界 | 可审计总结 |

目录规划和采样策略由 Agent 在调用 LSO 前完成。LSO 只处理路径候选、证据读取、写入审计、overlay 查询和命中来源标注。

## 2. Candidate Policy

候选来源至少考虑：

- 用户任务关键词或路径线索。
- scope 下顶级目录、主要 anchors、近期目录。
- 高价值文件类型：README、Office、PDF、文本、代码、manifest。
- 已有 overlay 的 top anchors、top tags，以及可通过公开接口获得的 active/cold 信息。
- fallback seeds。

对于 large root，例如 `F:\`，先观察顶级目录或主要 anchors，再决定 bounded sample。不得用一组通用关键词代表整盘覆盖。

## 3. Leaf Evidence Build

Leaf build 只产生 leaf-level coverage。真实任务中的 `paths` 必须来自 Stage B。

`candidate_paths_from_stage_b` 是路径字符串列表；若候选来自 `search_rows`，需要先取 `r["path"]`。

```python
import local_semantic_overlay as lso

session = lso.BuildSession(SCOPE)
paths = candidate_paths_from_stage_b
session.add_candidates(len(paths))

for row in lso.select_for_read(paths, seeds=[], fallback_seeds=[], limit=15):
    session.try_read(row["path"])
    leaf_id = session.ensure_leaf(row["path"])
    prep = lso.prepare_leaf_tag_task(SCOPE, leaf_id)
    if not prep["ok"]:
        continue
    task = prep["task"]  # text_head is semantic evidence; filename_hint is only a hint.
    proposals = [{"tag": "...", "evidence_phrase": "...",
                  "evidence_source": "text_head", "tag_role": "content_semantic"}]
    session.propose_tags(leaf_id, proposals)

audit = session.finalize()
```

`read_status == "readable"` 时，semantic evidence 只能来自 `text_head`。不可读 leaf 可以保留 `filename_hint`，但不产生 `semantic_tags`。

## 4. 语义通道约束

| 通道 | 含义 | 是否算 semantic coverage |
| --- | --- | --- |
| `semantic_tags` | 文件内容在讲什么，只能来自 `text_head` evidence | 是 |
| `filename_hint` | 文件名主体线索，用于召回和展示 | 否 |
| `location_tags` | 目录或位置线索 | 否 |
| `source_channel` | 来源渠道，例如 chat/mail/download | 否 |
| `path` | 路径字面线索 | 否 |

约束：

- Runtime 只查询 overlay，不写 overlay。
- 只有 Build 阶段能写 leaf tags、metadata、nodes。
- `semantic_tags` 不是 Agent 输入，而是 core 审计通过后的输出。
- Agent 只能提交 TagProposal，不能直接写 `semantic_tags`。
- `content_semantic.evidence_source` 只能是 `text_head`。
- filename/path/source/location/fallback 不得进入 `content_semantic`。
- metadata-only 不算 semantic coverage。

## 5. TagProposal 契约

```python
result = lso.propose_leaf_tags(SCOPE, leaf_id, [
    {"tag": "跨境贸易支付",
     "evidence_phrase": "高性能可信跨境贸易支付监管关键技术研究",
     "evidence_source": "text_head",
     "tag_role": "content_semantic"}  # content_semantic | location | source_channel
])
```

路由：

| `tag_role` | 写入字段 | evidence |
| --- | --- | --- |
| `content_semantic` | `semantic_tags` | 必须，且贴回 `text_head` |
| `location` | `location_tags` | 不需要 |
| `source_channel` | `source_channel` | 不需要，单值 |

成功路径是 full replacement：本次通过审计的结果会完整替换 leaf 上的 `semantic_tags`、`location_tags`、`source_channel`。错误路径不改 leaf。`ok=True` 不等于 `semantic_applied=True`。

返回值要点：`accepted`、`rejected`、`semantic_tags`、`metadata`、`semantic_applied`、`metadata_applied`。

## 6. Build Audit

`session.finalize()` 返回 overlay 快照和 `process` 统计。`process` 是本轮 leaf build 的唯一统计来源，但不能单独证明 coverage task 已完成。

必须关注：

```text
process:
candidate_path_count selected_count readable_count skipped_count
proposal_count proposal_accepted proposal_rejected
semantic_apply_ok metadata_apply_ok
semantic_applied_count metadata_applied_count
unique_leaf_before unique_leaf_after unique_leaf_delta
apply_ok apply_fail proposal_log

snapshot:
unique_leaf_count tagged_leaf_count untagged_leaf_count
node_count node_source_dist read_status_dist top_tags top_anchors
```

构建总结必须引用 `semantic_apply_ok` 作为 semantic coverage 入口，不能用 metadata-only、proposal accepted 或 apply attempt 冒充语义覆盖。

## 7. Node Eligibility 与 Node Build

Leaf build 后必须检查 node eligibility，不能直接用“停留 leaf sample”跳过。

检查：

- 用 `top_anchors`、本轮 selected/readable paths、`proposal_log` 判断是否有主要 anchor。
- 判断是否有重复主题、重复 tags、任务关键主题簇。

执行：

- 高价值 anchor：尝试 `prepare_compression_task` / `apply_compression`，或记录 `defer | expand | insufficient_evidence`。
- 跨 anchor 主题簇：尝试 `prepare_aggregation_task` / `apply_aggregation`，或记录 `defer | insufficient_evidence`。
- 无候选：记录 `no_node_candidate`。

`node_count` 是 overlay 快照，不等于本轮 node build 成果。不得仅因 snapshot 中 `node_count > 0` 判定本轮达到 `node_coverage_built`。`node_coverage_built` 只能来自本轮新增/更新 node，或复用已有 node 且通过 runtime query 证明与当前任务相关。

受控结果值：

```text
compressed | aggregated | reused_existing_node | defer | expand | insufficient_evidence | no_node_candidate | not_run
```

## 8. Runtime Query 与 Validation

若要声明 `runtime_validated`，必须跑 2-5 个代表性 query，并报告 hit_type 分布。未验证时只能停留在 `leaf_sample_built` 或 `node_coverage_built`。

```python
res = lso.query(SCOPE, "检索词", limit=20)
```

每个 hit 必须看 `hit_type` 和 `match_reasons`：

| hit_type | 含义 |
| --- | --- |
| `semantic_node` | 来自 compressed / aggregated node |
| `leaf_tag` | 来自已审计 leaf semantic tags |
| `filename_hint` | 文件名主体命中，不等于内容语义 |
| `metadata` | `location_tags` / `source_channel` 命中 |
| `path` | 路径字面命中 |
| `fallback` | 搜索适配器召回，只能作为下一轮候选 |

如果结果主要来自 `filename_hint` / `metadata` / `path` / `fallback`，只能说明 overlay 有文件线索价值，不能说明 semantic coverage 已经有效。

## 9. Bounded Report 模板

最终报告必须绑定本轮 run state，不得越级。

```text
本轮状态：<leaf_sample_built | node_coverage_built | runtime_validated>
scope: ...
scope_type: ...
build_mode: ...

候选与读取：candidate=..., selected=..., readable=..., skipped=...
语义写入：semantic_apply_ok=..., metadata_apply_ok=..., unique_leaf_delta=...
节点状态：node_count=..., compression_checked=<yes/no>, aggregation_checked=<yes/no>, result=...
验证状态：runtime_validation=<not_run | run>, hit_type_dist=...

边界：本轮主要覆盖 ...；未覆盖 ...
结论：本轮只能表述为 ...
```

除非有穷尽性证据，不得使用“完整覆盖”“覆盖完成”“所有文件”“本机全部”“F 盘语义覆盖完成”。

## 10. Feedback 与拒绝原因

只有显式反馈才能写入 feedback；不要把“query 没命中”推断成负反馈。

```python
lso.record_feedback(SCOPE, result_id="q1", kind="selected|not_selected|negative", node_id="node_xxx")
```

常见拒绝原因：`missing_evidence`、`weak_evidence`、`invalid_tag_role`、`invalid_evidence_source`、`no_evidence_source`、`evidence_not_grounded`、`duplicate_tag`、`multiple_source_channel`、`tag_is_extension`、`tag_is_dir_token`、`no_tags_accepted`、`brief_not_grounded`、`recursive_aggregation`。
