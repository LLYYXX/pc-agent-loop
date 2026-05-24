# LSO Agent SOP

**触发**：任务根目录固定，需**反复**在该范围内找本地文件，并沉淀「哪些文件/概念相关」。  
**不触发**：1–2 步单文件读写；与目录导航无关的任务。

可选能力（compression / aggregation / recheck / 返回值全集）：`file_read memory/local_semantic_overlay_reference.md`

---

## 禁止 ⚠

1. **runtime 不写覆盖图**（只有 Build 里 apply）  
2. **fallback 路径**只进下轮 `fallback_seeds`，不自动入库  
3. **negative / not_selected** 必须显式 `record_feedback`；query 未命中 ≠ 负面  
4. **brief** 必须能在 `text_head` 里找到依据  
5. **tags** 勿抄路径片段、扩展名、泛词  
6. **aggregated 节点**不能出现在 `derived_from_ids` 里  

---

## import

```python
import json, os, sys
ROOT = "/abs/path/to/pc-agent-loop"  # 写入 working memory
sys.path.insert(0, os.path.join(ROOT, "memory"))
from local_semantic_overlay import (
    ensure_search_ready, search_rows, select_for_read, read_leaf,
    ensure_leaf, prepare_leaf_tag_task, apply_leaf_tags,
    query, record_hit, record_feedback, enforce_active_budget,
)
SCOPE = "/abs/path/to/task/root"
```

---

## Build

```python
ensure_search_ready()
paths = [r["path"] for r in search_rows("关键词", scope=SCOPE, limit=30)]
for row in select_for_read(paths, seeds=[], fallback_seeds=[], limit=15):
    rr = read_leaf(row["path"])
    if rr["read_status"] != "readable":
        continue
    _, leaf_id = ensure_leaf(SCOPE, row["path"])
    prep = prepare_leaf_tag_task(SCOPE, leaf_id)
    if not prep["ok"]:
        continue
    tags = ["..."]  # 读 prep["task"]["text_head"]，2–5 个短词
    apply_leaf_tags(SCOPE, leaf_id, tags)
enforce_active_budget(SCOPE)
```

`read_status` 非 `readable` → 跳过。apply 失败见 reference。

---

## Runtime

```python
res = query(SCOPE, "检索词", limit=20)
fallback_seeds = []
for h in res["hits"]:
    if h["hit_type"] == "semantic_node":
        rh = record_hit(SCOPE, h["node_id"])
        if rh.get("action") == "needs_recheck":
            pass  # reference: recheck_cold_node / apply_recheck
    elif h["hit_type"] == "leaf_tag":
        pass  # file_read h["path"]
    elif h["hit_type"] == "fallback" and h["source"] == "fallback":
        fallback_seeds.append(h["path"])
# 下轮 Build 把 fallback_seeds 传入 select_for_read
```

| hit_type | 动作 |
|----------|------|
| `semantic_node` | 读 `brief`；cold 默认不出现在 query |
| `leaf_tag` | `file_read` 该路径 |
| `path` | 路径词面命中 |
| `fallback` | 仅候选，下轮 `fallback_seeds` |

设计背景（人类）：[`local_semantic_overlay_overview.md`](local_semantic_overlay_overview.md)
