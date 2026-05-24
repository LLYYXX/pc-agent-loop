# LSO 消融实验说明

> **不给日常 Agent**。用于 A/B/C 对比与 `--ablate` 开关验证。  
> 设计背景见 [`local_semantic_overlay_overview.md`](local_semantic_overlay_overview.md)。

---

## 脚本

```bash
cd /path/to/pc-agent-loop
python experiments/lso/ablation_benchmark.py --mode C --scope /path/to/fixture
python experiments/lso/ablation_benchmark.py --mode B --ablate no_compression --ablate no_fallback
```

## A/B/C

| mode | 包含 |
|------|------|
| A | 无 LSO（直调 es 基线） |
| B | + 搜索适配 + 证据管线 |
| C | + 覆盖图 + 运行时导航 |

B→C 测 semantic overlay 收益；A→B 测 tool friction。

## `--ablate` 开关

`no_selection` `no_leaf_tags` `no_compression` `no_aggregation` `no_active_cold` `no_feedback` `no_semantic` `no_fallback` `path_only`

对应 core 中的 `EvidenceFlags` / `OverlayFlags` / `NavigateFlags`（见 reference）。

## 输出 JSON

`mode` `ablations` `search_calls` `file_reads` `overlay_writes` `query_hits` `fallback_hits` `semantic_hits`

---

**可消融 ≠ 不计 core 行数**（见 overview 规模表）。
