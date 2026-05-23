# Local Semantic Overlay (LSO)

LSO 是 GA 外置的轻量本地语义覆盖图。当前仓库仅包含 **substrate** 模块；semantic core（build/store/runtime）待重写。

## Import

```python
from local_semantic_overlay import (
    ensure_search_ready, search_paths, search_rows,
    read_leaf, looks_like_raw_dump, sanitize_display,
)
```

禁止 `from memory.local_semantic_overlay import ...`。

## Search adapter（retrieval substrate）

纯机械：定位 es/Everything、subprocess、encoding、返回 path/name/mtime/size。不生成 tag/node。

```python
ready = ensure_search_ready()
rows = search_rows("*.md", scope=r"F:\YourScope", limit=50)
paths = search_paths("keyword", scope=r"F:\YourScope")
```

## Reader / evidence gate

纯机械：抽取 text_head、过滤 raw dump、返回 read_status。

```python
info = read_leaf(r"F:\path\file.docx")
# read_status: readable | skipped_noise | extract_failed | binary
# readable 才进入后续 leaf tagging / compression
```

## 边界

- 不侵入 GA；Agent 在 `code_run` 中主动调用
- substrate 不计入 LSO core 1000 行预算
- semantic core 将另行实现：compression、aggregation、semantic nodes、active/cold、runtime
