# LSO Agent Reference

日常流程见 [`local_semantic_overlay_sop.md`](local_semantic_overlay_sop.md)。本文：**可选 Build 路径、返回值、完整 import**。

---

## 完整 import

```python
from local_semantic_overlay import (
    ensure_search_ready, search_rows, select_for_read, read_leaf,
    ensure_leaf, prepare_leaf_tag_task, apply_leaf_tags,
    prepare_compression_task, apply_compression,
    prepare_aggregation_task, apply_aggregation,
    prepare_recheck_task, apply_recheck,
    query, record_hit, recheck_cold_node, record_feedback, enforce_active_budget,
    OverlayFlags, NavigateFlags, EvidenceFlags,
)
```

---

## 可选 Build：compression（一次 0/1 node）

```python
prep = prepare_compression_task(SCOPE, SCOPE)
# 读 prep["task"]["sample_evidence"]
result = {"decision": "compress", "label": "...", "tags": ["..."], "brief": "..."}
apply_compression(SCOPE, SCOPE, result)  # expand|defer → 不写入
```

## 可选 Build：aggregation

```python
prep = prepare_aggregation_task(SCOPE)
result = {
    "decision": "aggregate", "label": "...", "tags": ["..."],
    "derived_from_ids": ["leaf_xxx", "node_yyy"], "brief": "...",
}
apply_aggregation(SCOPE, result)  # skip → 不写入
```

## 可选：cold recheck

```python
task = recheck_cold_node(SCOPE, node_id)  # 或 prepare_recheck_task
apply_recheck(SCOPE, node_id, {"decision": "keep|delete|update", ...})
```

## 显式 feedback

```python
record_feedback(SCOPE, result_id="q1", kind="selected", node_id="node_xxx")
record_feedback(SCOPE, result_id="q1", kind="negative", node_id="node_xxx")  # → cold
```

---

## 返回值速查

| 调用 | 成功 / 要点 |
|------|-------------|
| prepare_* | `{"ok": true, "task": {...}}` |
| apply_leaf_tags | `semantic_tags` 已机械过滤 |
| apply_compression / apply_aggregation | 成功有 `node_id`；跳过 `node_id: null` |
| query | 空 query → `hits=[]` |
| record_hit | `action`: `hit_recorded` \| `restored_active` \| `needs_recheck` |
| enforce_active_budget | `demoted` 列表 |

## apply 常见 error

| error | 处理 |
|-------|------|
| `brief_not_grounded` | 改 brief 贴 evidence |
| `incomplete` | 补 label/tags/brief |
| `recursive_aggregation` | 换 derived_from_ids |
| `disabled` | 该能力被 Flag 关闭（日常不应关） |
