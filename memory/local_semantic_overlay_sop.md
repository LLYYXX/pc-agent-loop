# LSO Agent SOP

**触发**：任务需在一个**固定目录范围**内反复找本地文件、沉淀「哪些文件/概念相关」，且希望下次查询更快。  
**不触发**：1–2 步单文件读写、与目录导航无关的任务。

**读本文后**：用 `code_run` 执行下方模板；不要改 GA 主循环。

---

## 0. 一次性 import（每段 code_run 开头复制）

```python
import json, os, sys
# 把 ROOT 换成 pc-agent-loop 仓库绝对路径（写入 working memory，勿猜）
ROOT = "/home/lyx/workspace/A3Lab/pc-agent-loop"
sys.path.insert(0, os.path.join(ROOT, "memory"))
from local_semantic_overlay import (
    ensure_search_ready, search_rows, select_for_read, read_leaf,
    ensure_leaf, prepare_leaf_tag_task, apply_leaf_tags,
    prepare_compression_task, apply_compression,
    prepare_aggregation_task, apply_aggregation,
    prepare_recheck_task, apply_recheck,
    query, record_hit, recheck_cold_node, record_feedback, enforce_active_budget,
    OverlayFlags, NavigateFlags, EvidenceFlags,
)
SCOPE = "/absolute/path/to/task/root"  # 作用域 = 任务根目录
```

---

## 1. 概念（最少必要）

| 词 | 含义 |
|----|------|
| `SCOPE` | 任务根目录；覆盖图按 scope 存 `memory/local_semantic_overlay/overlays/*.json` |
| prepare | 核心返回「待你填语义的任务包」；**你**读 `task` 后做判断 |
| apply | 你把判断结果交回核心；核心校验、持久化 |
| `read_status` | `readable` 才能打标签；`skipped_noise`/`binary`/`extract_failed` 跳过 |
| `hit_type` | `semantic_node` / `leaf_tag` / `path` / `fallback` |
| `source` | `overlay`（覆盖图）或 `fallback`（底层搜索） |

---

## 2. Build 循环（写覆盖图）

按序执行；语义内容（标签、label、brief、decision）**全部由 Agent 填**。

```python
# --- B1 搜索（可选；无 es 时跳过，paths 改用手动列表）---
ensure_search_ready()  # 默认不写 global_mem；仅首次定位 es 时用 persist=True
rows = search_rows("关键词", scope=SCOPE, limit=30)
paths = [r["path"] for r in rows]

# --- B2 证据：机械筛候选 → 读文件 ---
selected = select_for_read(
    paths,
    seeds=[],              # 你已确认要读的路径
    fallback_seeds=[],     # 上轮 query 的 fallback 路径，见 §3
    limit=15,
)
for row in selected:
    rr = read_leaf(row["path"])
    print(row["path"], row["reason"], rr["read_status"])
    if rr["read_status"] != "readable":
        continue
    data, leaf_id = ensure_leaf(SCOPE, row["path"])

    # --- B3 文件叶打标签 ---
    prep = prepare_leaf_tag_task(SCOPE, leaf_id)
    if not prep["ok"]:
        print("leaf_tag prep:", prep)
        continue
    # ↓ Agent 根据 prep["task"]["text_head"] 决定 tags（2–5 个短词，勿抄路径/扩展名）
    tags = ["your", "tags"]
    print(apply_leaf_tags(SCOPE, leaf_id, tags))

# --- B3 可选：目录压缩为 1 个语义节点（decision=compress 才写入）---
# prep = prepare_compression_task(SCOPE, SCOPE)
# Agent 读 prep["task"]["sample_evidence"] → 填 result
# result = {"decision": "compress", "label": "...", "tags": ["..."], "brief": "..."}
# print(apply_compression(SCOPE, SCOPE, result))
# decision=expand|defer → 不写入节点

# --- B3 可选：多标签/节点归纳（decision=aggregate 才写入）---
# prep = prepare_aggregation_task(SCOPE)
# result = {"decision": "aggregate", "label": "...", "tags": ["..."],
#           "derived_from_ids": ["leaf_xxx", "node_yyy"], "brief": "..."}
# print(apply_aggregation(SCOPE, result))

# --- B3 热预算（active 节点超限则 demote 为 cold）---
print(enforce_active_budget(SCOPE))
```

**apply 失败常见原因**：`brief_not_grounded`（brief 与证据摘要无关）、`incomplete`、`recursive_aggregation`。  
处理：改 brief/tags 重试，或 `decision=defer/skip` 跳过。

---

## 3. Runtime 循环（读覆盖图 + 查）

```python
q = "用户问题或检索词"
res = query(SCOPE, q, limit=20)
print(json.dumps(res, ensure_ascii=False, indent=2))

fallback_seeds = []  # 下轮 build 传入 select_for_read
for h in res["hits"]:
    t, src = h["hit_type"], h["source"]
    if t == "semantic_node":
        print("语义节点", h["node_id"], h["label"], h.get("status"))
        rh = record_hit(SCOPE, h["node_id"])
        print("record_hit", rh)
        if rh.get("action") == "needs_recheck":
            task = recheck_cold_node(SCOPE, h["node_id"])  # 或 prepare_recheck_task
            # Agent 读 task → apply_recheck(SCOPE, node_id, {"decision": "keep|delete|update", ...})
    elif t == "leaf_tag":
        print("文件标签", h["path"], h["tags"])
    elif t == "path":
        print("路径命中", h["path"])
    elif t == "fallback" and src == "fallback":
        print("fallback", h["path"])
        fallback_seeds.append(h["path"])

# 显式反馈（optional；禁止从「未出现在 hits」推断 negative）
# record_feedback(SCOPE, result_id="q1", kind="selected", node_id="node_xxx")
# record_feedback(SCOPE, result_id="q1", kind="negative", node_id="node_xxx")  # → 节点变 cold
```

**读 hits 规则**

| hit_type | 怎么用 |
|----------|--------|
| `semantic_node` | 优先读 `brief` + 查 `supporting_leaf_ids` 对应文件；`status=cold` 时默认不出现在 query（除非 `include_cold=True`） |
| `leaf_tag` | 直接 `file_read` 该 `path` |
| `path` | 路径词面匹配，无标签 |
| `fallback` | 仅候选；**下轮**放进 `fallback_seeds`，runtime **不写** overlay |

---

## 4. 禁止项 ⚠

1. **runtime 不写覆盖图**（不打标签、不建节点；只有 Build 循环 apply）  
2. **fallback 路径不自动入库**  
3. **negative / not_selected 必须显式** `record_feedback`；query 未命中 ≠ 负面反馈  
4. **brief 必须 grounded** 于 `text_head`，禁止空泛描述  
5. **tags 禁止** 路径片段、扩展名、泛词（核心会机械过滤）  
6. **aggregated 节点** 不能出现在 `derived_from_ids` 里  

---

## 5. 返回值速查

**prepare_***：`{"ok": true, "task": {...}}` 或 `{"ok": false, "error": "...", "message": "..."}`

**apply_leaf_tags**：`{"ok": true, "leaf_id", "semantic_tags"}`

**apply_compression / apply_aggregation**：成功 `node_id`；跳过 `node_id: null`；dedup 时 `deduped: true`

**query**：`{"ok": true, "query_tokens": [...], "hits": [...]}`；**空 query → hits=[]**

**record_hit action**：`hit_recorded` | `restored_active` | `needs_recheck`

**enforce_active_budget**：`{"demoted": ["node_..."], "skipped": true}`（关 active_cold 时 skipped）

---

## 6. 能力开关（默认全开；仅消融实验改）

```python
EvidenceFlags(enable_selection=False)   # 候选不机械排序，passthrough
OverlayFlags(enable_leaf_tags=False, enable_compression=False,
             enable_aggregation=False, enable_active_cold=False, enable_feedback=False)
NavigateFlags(enable_semantic=False, enable_fallback=False,
              enable_path=True, enable_leaf_tags=False, include_cold=False)
```

日常任务**不要关**；做对比实验见 §7。

---

## 7. 消融 / 实验（B5，可选）

```bash
cd /path/to/pc-agent-loop
python experiments/lso/runner.py --mode C --scope /path/to/fixture
python experiments/lso/runner.py --mode B --ablate no_compression --ablate no_fallback
```

| mode | 含义 |
|------|------|
| A | 无 LSO（直调 es） |
| B | + 搜索适配 + 证据管线 |
| C | + 覆盖图 + 运行时导航 |

`--ablate`：`no_selection` `no_leaf_tags` `no_compression` `no_aggregation` `no_active_cold` `no_feedback` `no_semantic` `no_fallback` `path_only`

输出 JSON 字段：`search_calls` `file_reads` `overlay_writes` `query_hits` `fallback_hits` `semantic_hits`

---

## 8. 最小闭环示例（单文件）

```python
import json, os, sys
ROOT = "/home/lyx/workspace/A3Lab/pc-agent-loop"
sys.path.insert(0, os.path.join(ROOT, "memory"))
from local_semantic_overlay import ensure_leaf, prepare_leaf_tag_task, apply_leaf_tags, query

SCOPE = "/path/to/project"
path = "/path/to/project/README.md"
_, leaf_id = ensure_leaf(SCOPE, path)
prep = prepare_leaf_tag_task(SCOPE, leaf_id)
apply_leaf_tags(SCOPE, leaf_id, ["setup", "routing"])
print(json.dumps(query(SCOPE, "routing"), ensure_ascii=False))
```

---

设计背景（人类可读）：[`local_semantic_overlay_overview.md`](local_semantic_overlay_overview.md)
