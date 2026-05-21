# Local Semantic Overlay (LSO Slim)

LSO 是 GA 外置的轻量本地语义覆盖层：Everything/es 负责召回；LSO 把少量高价值 evidence 变成正交 tag 与低 token overview，供 runtime 定位文件。

**不**改 GA 主循环；文件任务时由 Agent 在 `code_run` 中主动调用。

## Import

`code_run` 已将 `memory/` 加入 `sys.path`：

```python
from local_semantic_overlay import (
    begin_build, discover_seeds, prepare_bundle, bundle_prompt, apply_tags,
    build_overview, finish_build, system_overview, query_map, run_file_query,
    finish_file_query,
)
```

禁止 `from memory.local_semantic_overlay import ...`。

## 何时用

- 在固定 scope（项目目录、课题组文档树等）反复找文件
- 已 build 出 partial overview 后，先 `system_overview` / `query_map`，不足再 `run_file_query`（含 es fallback）
- 找到目标后 `finish_file_query(found=[...])` 回流 seed

## Build 循环

```python
scope = r"F:\YourScope"
data = begin_build(scope, reset=True)["data"]
discover_seeds(data)

while True:
    bundle = prepare_bundle(data)
    if not bundle:
        break
    prompt = bundle_prompt(bundle)
    # LLM → response JSON
    result = apply_tags(data, bundle, response)
    if not result["ok"]:
        print(result)  # 修正后重试
        continue

build_overview(data)
print(finish_build(data))
```

## Tag 规则（LLM）

- 1–3 个 tag，须 orthogonal / evidence-grounded / future-useful
- primary 必须出现在 `leaf_annotations` 或 `defer_leaf_ids`
- candidate 可选；不要为清 bundle 大量 defer
- 同 anchor 优先复用已有 tag

## Runtime

```python
system_overview(scope)          # partial overview，低 token
query_map("关键词", scope=scope)  # 仅 map，无 fallback
run_file_query("关键词", scope=scope)  # map 不足时 es fallback
finish_file_query("q", scope=scope, found=[r"F:\path\file.md"])
```

返回均含 `ok` / `error` / `message`；`partial: true` 表示未全盘覆盖。

## Partial 口径

LSO 不追求全盘索引；`status` 恒为 partial。overview 与 runtime 不展示 raw dump 文本。
