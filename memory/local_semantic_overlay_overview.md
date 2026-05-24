# 本地语义覆盖层（LSO）— 设计说明

> 面向人类读者：解释 LSO 是什么、解决什么问题、边界在哪。  
> Agent 如何调用见 [`local_semantic_overlay_sop.md`](local_semantic_overlay_sop.md)（按需 [`reference`](local_semantic_overlay_reference.md)）。

---

## 一句话

LSO 是挂在 GenericAgent **外面**的一层**轻量语义地图**：用少量可信文件内容，帮 Agent 在本地文件海里**更快找到该读什么**，而不是替 Agent 做任务决策。

它不侵入 GA 主循环；Agent 在需要时通过 `code_run` 主动调用。

---

## 解决什么问题

本地任务里，Agent 常卡在两类摩擦：

1. **找文件**：全盘搜、路径乱、同名多、无关目录噪音大。  
2. **读文件**：二进制 dump、Office 乱码、manifest 被当正文——读到了也不能当证据。

LSO 把「找 → 筛 → 读 → 记语义 → 再查」收成一条**可持久、可解释、可关掉某一步做对比实验**的管线。  
目标不是给每个文件打标签，而是在**有限预算**内维护一张**局部语义覆盖图**，够用就好，随时可回退到底层搜索。

---

## 数据怎么流动

```text
真实文件
  → 可读证据（文件头摘要，或明确「不可读」）
  → 文件级语义标签（Agent 根据证据填写）
  → 语义节点（把多份证据压缩/归纳成虚拟概念，仍必须挂回真实文件）
  → 运行时查询（标签、节点、路径、底层搜索，来源分开标注）
```

**不维护**：任务入口、目录树、embedding 向量库、自动后台索引。

**核心对象（设计语言）**

| 对象 | 含义 |
|------|------|
| **作用域** | 一次任务关注的根目录；该目录下的覆盖图单独存盘。 |
| **文件叶** | 某个真实路径及其可读摘要、mtime 等机械元数据。 |
| **语义标签** | Agent 给文件叶写的短标签；必须来自已读证据，系统会滤掉路径词和泛词。 |
| **语义节点** | 虚拟概念（压缩或归纳而来）；必须列出支撑哪些文件叶，brief 必须能在证据里找到依据。 |
| **热/冷** | 节点太多时，久未命中的降为冷存储；冷节点默认不参与查询，命中后可复核。 |
| **显式反馈** | 只有 Agent 明确说「选中 / 未选中 / 负面」才记；不能从「查询没命中」推断。 |

---

## 谁做什么

| 角色 | 职责 |
|------|------|
| **Agent** | 决定读哪些、标签/节点写什么、是否压缩/归纳、如何处理查询结果与反馈。 |
| **LSO 核心** | 持久化、校验不变量、机械过滤、查询与来源标注；**不调 LLM**。 |
| **底层搜索** | Everything/`es` 等；只返回路径行，不产生语义。 |

**不变量（违反则写入被拒绝）**

- 每个语义节点必须挂至少一个真实文件叶。  
- 节点的 brief 必须能在支撑文件的内容摘要里找到依据（grounded）。  
- 归纳节点必须记录「从哪些标签/节点而来」；不能用归纳节点再归纳。  
- 运行时只读覆盖图 + 搜索；**不在查询时写标签或节点**。  
- fallback 搜到的路径只作为下轮候选种子，**不自动写入覆盖图**。

---

## 五条可拆卸边界

实现按「能单独关掉做消融」划分，不是按论文章节拆文件。

| 边界 | 做什么 | 关掉后意味着什么 |
|------|--------|------------------|
| **搜索适配** | 定位 es、统一编码/超时、返回路径行 | 回到 Agent 直接调 es（A 基线） |
| **证据管线** | 机械筛候选 + 读文件门控（拒 raw dump） | 候选全透传或 Agent 手选路径 |
| **覆盖图构建** | 存盘、打标签、压缩/归纳、热冷、反馈 | 无持久语义层 |
| **运行时导航** | 查覆盖图 + 标注命中来源 + fallback | 无语义查询，只剩路径/搜索 |
| **实验编排** | A/B/C 模式与指标 JSON | 不影响 Agent 日常使用 |

默认**全开** = 设计稿里的最终能力；有效性靠「关掉某边界」的对比实验证明，不是分期交付半套产品。

---

## 与 GenericAgent 的关系

- **不改** tool schema、主循环、`file_read` hook。  
- **不跑** watcher、定时索引、隐式记忆写入。  
- Agent 任务涉及「在一个目录范围内反复找文件、沉淀哪些文件重要」时，读 SOP 并按 Build / Runtime 两循环调用即可。  
- 删掉整个 `local_semantic_overlay` 包，GA 行为不变。

---

## 当前状态

```text
消融边界版实现成立；
Slim Core 规模约束已收敛（减码后）。
```

当前实现已完成消融边界版重写，**30 项结构测试全部通过**。  
按对齐稿 core 口径统计（`read` + `select` + `overlay` + `navigate` + public API），减码后 **897 行**，低于 1000 行约束并保留约 **100 行缓冲**（供 dedup 测试、lineage cleanup、ablation benchmark 指标、Windows/es smoke 等后续补全）。  
**可消融 ≠ 不计行数**——证据管线可关开关做实验，但 `read.py` 仍计入 core，不能当 substrate 从预算里拿掉。

下一步：**不新增能力**，先跑 A/B/C 消融实验；再据结果决定是否加能力。

---

## 规模与范围

**行数口径（与 §2.2 对齐）**：计入 Slim core 的是所有生成或维护 tag / semantic node / overlay state、以及 evidence extraction / raw dump gate 的代码；**不计入**的只有搜索适配（`search.py`）和实验 harness（`experiments/lso/ablation_benchmark.py`）。

| 模块 | 行数 | 计入 core |
|------|------|-----------|
| `read.py`（证据提取 / raw dump gate） | 166 | 是 |
| `select.py`（机械候选筛选） | 101 | 是 |
| `overlay.py`（构建 / 持久化 / 热冷 / 反馈） | 494 | 是 |
| `navigate.py`（运行时导航） | 124 | 是 |
| `__init__.py`（public API） | 12 | 是 |
| **core 合计** | **897** | **≤1000，缓冲 ~103 行** |
| `search.py` | 251 | 否（substrate） |
| `_config.py` | 86 | 否（机械配置，与 search 共享） |
| `experiments/lso/ablation_benchmark.py` | 114 | 否（harness） |

当前是 **Slim** 版：词面匹配查询，无 embedding、无任务级推理。

---

## 文档分工

| 文件 | 读者 |
|------|------|
| 本文 | 人：设计意图、对象、边界、约束 |
| [`local_semantic_overlay_sop.md`](local_semantic_overlay_sop.md) | Agent 日常：Build / Runtime、禁止项 |
| [`local_semantic_overlay_reference.md`](local_semantic_overlay_reference.md) | Agent 按需：可选能力、返回值、完整 import |
| [`local_semantic_overlay_ablation.md`](local_semantic_overlay_ablation.md) | 人/实验：A/B/C 消融（**不给日常 Agent**） |
| `memory/local_semantic_overlay/` | 实现源码 |
