# Local Semantic Overlay SOP

LSO 用来发现用户常用、高价值文件，并沉淀成可移除的本地语义导航层。它不是全盘索引器，也不声称完整覆盖某个目录或磁盘。冷文件仍然留在 ES 里；当查询 miss 后，可以通过增量流程进入 LSO。

## 调度

使用 `memory/subagent.md` 里的 GA CLI 文件 IO SubAgent 协议：

```text
prepare -> write_task_dir -> agentmain.py --task (保留打印出的 PID)
        -> artifact.json -> apply_task_artifact -> close_task(PID) -> next_task
```

单个 role 可按下面的最小协议手动推进；完整 cold/incremental build 应优先交给标准 driver 循环执行。

```python
info = lso.write_task_dir(task_name, task["role"], task)
# 在仓库根目录运行 info["command"]，观察 output*.txt。
result = lso.apply_task_artifact(scope, info["task_dir"])
while result.get("correction_required"):
    # 保持同一个 SubAgent 存活；它必须根据 reply.txt 自己重写 artifact.json。
    result = lso.apply_task_artifact(scope, info["task_dir"])
lso.close_task(pid)
task = result.get("next_task")
```

主 Agent 只负责派发、观察和推进状态。它不得扮演任何 role，不得写 `artifact.json`，不得编辑 `artifact.json`，不得裁剪字段，不得删除 claims，也不得用临时脚本替代某个 role。

如果 `apply_task_artifact()` 拒绝产物，它会写入 `reply.txt`。此时必须让同一个 SubAgent 根据 `reply.txt` 重写 `artifact.json`，然后再次调用 `apply_task_artifact()`。产物被接受或任务放弃后，必须关闭该 SubAgent 的 PID。LSO 串行执行：关闭当前 SubAgent 之前，不派发下一个 role。

## 标准 Driver

Driver 是 LSO build 的机械调度器，不是新角色，也不做任何语义判断。正式实现是 `memory/local_semantic_overlay/runner.py::run_build`，并通过 `local_semantic_overlay.run_build(...)` 导出。

完整 build 的 driver 必须执行：

```text
prepare(scope, reset/question/seeds)
-> task(scope)
-> write_task_dir(task_name, role, task)
-> launch agentmain.py --task <task_name> --verbose
-> parse and retain worker PID
-> wait for fresh artifact.json and latest output containing [ROUND END]
-> apply_task_artifact(scope, task_dir)
-> if correction_required: keep same worker alive and wait for rewritten artifact.json
-> cap correction retries, record timeout/stuck state, always close_task(PID)
-> on accepted artifact: close_task(PID), persist progress/timings, continue next_task
-> stop when next_task is None
```

Driver 可以记录 `progress.json`、`timings.json` 和普通日志；可以把 `prepare(reset=True)` 放在后台长跑；可以用 correction cap 防止无限返工。Driver 不得读取 stdout 当 artifact，不得修改 `artifact.json`，不得删除 invalid claim 继续 apply，不得同时启动多个 role worker，不得在关闭当前 PID 前进入下一 role。

常用入口：

```python
import local_semantic_overlay as lso

result = lso.run_build(
    scope,
    task_name="lso_build_name",
    question="optional user task",
    seeds=None,
    reset=False,
)
```

需要长跑或轮询时，显式传入 `progress_path`、`timings_path`、`log_path`；需要从已有 `build_state.json` 继续时传 `prepare_first=False`。这些都是机械参数，不改变任何 role 的语义职责。

## 冷启动

`prepare(scope, question, seeds, reset)` 通过 ES 获取可配置的价值信号：

```text
recent access/create/modify, long-maintained files, mainstream project markers, docs, task seeds
```

每类信号有自己的机械预算；多类结果去重后持久化，不再做第二次全局截断。

- `long_maintained` 只表示“修改时间 - 创建时间”达到配置时长；近期活跃不属于这个定义。
- 如果一个目录下直接平铺文件数超过配置阈值，该目录下的直接文件不进入冷启动候选；显式 ES miss seeds 仍可进入增量维护。
- `candidate_pool.json` 记录机械召回的候选文件和 signals。
- `selector_ledger.json` 记录每个 Selector batch 的判断。
- `base_overlay.json` 记录本次 cold/incremental run 之前的稳定 overlay。
- `build_state.json` 记录当前 role 和 selector batch offset。
- `coverage_report.json` 记录给独立 Auditor 使用的机械事实。

机械事实本身不决定通过或失败。最终 verdict 只由独立 Auditor 给出。

没有任何物理区域必须出现在 LSO 里。未进入 LSO 的路径不需要、也不应该获得 ignored tag。

## 角色

每个当前任务启动一个独立 CLI SubAgent：

```text
selector batches -> compressor -> tagger -> aggregator -> auditor -> complete
```

1. `selector` 是 recall-preserving noise rejector，不是 high-value file selector。它接收 candidate paths、文件名和 source signals，只负责拒绝明确噪声，必须把当前 batch 的每个 candidate 明确归入 `retained` 或 `discarded`。不确定就 `retained`；看起来普通但可能有用也 `retained`。误留可以由下游恢复，误杀不可恢复。它不得排序、精选、判断“最重要文件”，也不得因为“低价值”“不中心”“不够像目标”而 discard。每个 `discarded` 都必须有明确 `noise_evidence`。它不创建 tag，也不创建 node。

2. `compressor` 只压缩成熟项目/工具/服务目录：必须同时看到入口文件、项目结构，并能命名为具体项目、工具或服务。同目录、同主题、同扩展名、同一批文档、或单个弱 marker 都不足以证明语义内聚；不满足时把 leaf 放进 `standalone_leaf_ids`。需要时可以通过 ES 查询附近结构。输出 `targets` 和 `standalone_leaf_ids`。

3. `tagger` 读取可用内容或明确的替代 evidence channel，只给 Compressor 显式给出的 `tag_targets` 产出有 evidence 支撑的 claims。`tag_targets` 包括 standalone leaves 和 compressed nodes；supporting leaves 只是证据，不是单独打标签目标。它可以调用 `memory/local_semantic_overlay/document_extract.py::extract_text` 读取 PDF/Office/text targets。如果高价值 target 只是因为本地 reader 依赖缺失而不可读，SubAgent 可以在 `temp/` 下临时创建 reader environment 或工具，并在 `source` 里说明具体方法。每个 target 应在证据支持时产出 2-5 个尽量不同方向的 tag，例如主题、材料类型、工作流、领域、项目/服务、动作；单个泛 tag 是异常，不是默认。filename-only evidence 不得伪装成 file-content evidence。

4. `aggregator` 只读取已有 leaves、nodes、tags 和 claims。它把相近 tags 聚合成 facet nodes 和 semantic nodes，也可以继续聚合这些 nodes。它只维护 support 和 derivation 关系，不添加其他关系类型。

5. `auditor` 独立审查 artifacts、ledgers 和最终 map。它判断语义有用性，不只看结构合法性：非成熟项目/工具/服务目录的压缩、系统性单 tag targets、浅层或无帮助的聚合，都需要 rework，除非有充分理由。它永远不写 overlay。失败时，它的 evidence 指明 `rework_role`；`apply_stage()` 会恢复稳定 base，重放已接受的上游 artifacts，并返回该 role 的 next task。主 Agent 永远不修 auditor artifact。

## Artifact 规则

- 正式输出只能是 `artifact.json`；不得从 stdout 解析 artifact。
- 被拒绝的 artifact 只能由对应 role SubAgent 根据 `reply.txt` 修正。
- artifact 的 `role` 必须等于当前 stage。
- 未知顶层字段直接失败。
- 每个 Selector batch path 必须被且仅被分类一次。
- 每个 retained leaf 必须被 Compressor target 覆盖，或列入 `standalone_leaf_ids`。
- Compressor nodes 必须有 supporting leaves，且只表达成熟项目/工具/服务边界；它们不意味着完整读取了整个目录。
- Tagger claims 必须引用显式 Compressor tag target，并包含 tag、evidence 和 source；系统性单 tag target 应由 Auditor 打回。
- Aggregator 不得新增 support 和 derivation 以外的关系类型。

## 运行时

```text
query LSO -> follow tag/node to files
miss -> query ES -> prepare(scope, question=query, seeds=hits, reset=False)
     -> selector/compressor/tagger/aggregator/auditor
```

带 seeds 的增量 run 只处理这些 ES hits。没有 seeds 的增量 `prepare()` 才会做更宽的价值信号刷新。运行时 query 本身保持词面匹配，不自动启动 SubAgents。

## 已验证的操作注意事项

- 大 scope，例如整盘和约 170 万文件的 census，会让 `prepare(reset=True)` 远超 300 秒工具超时。应作为后台进程运行并轮询输出文件；长时间 census 是正常现象，不等于卡死。
- 整个 dispatch loop 是机械流程，语义判断都在 SubAgents 里。完整 build 默认走标准 Driver；主 Agent 仍然不得编辑 artifact。
- Auditor 阶段结束后 build 是 `complete`；此时调用 `lso.task(scope)` 会抛 `KeyError: 'complete'`。这是预期行为，因为没有 pending task。验证结果应使用 `lso.load(scope)` 查看 leaves/nodes 数量，用 `lso.query(scope, q)` 查看查询效果。
