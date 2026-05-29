# LSO 最高契约 (ACCEPTANCE)

> 本文件由人定、冻结、只读。master / worker 不得改写。
> 它只定义 WHAT（不可再降的根不变量），不定义 HOW。
> HOW（逻辑行怎么数、provenance 通道怎么存、采样状态字段、消融指标/阈值、
> audit 问题清单逐条措辞）由 gate 脚本实现，再用本契约反验 gate。

## 定位

LSO 是 GA 的本地文件语义覆盖索引：产出 `tag → node → leaf → file` 的可导航链路，
让文件任务用"查链路"替代"猜关键词盲搜 ES"。
语义、路径、目录、文件名、元信息及其 provenance 是链路的支撑数据，不是产品本体。

## 产品不变量

1. **导航链是本体**
   LSO 产出 tag→node→leaf→file 可导航链路。其它数据是支撑，不是本体。

2. **core 只物化、不判断**
   core 是导航链的物化与维护机器：给定已审核 proposal，负责校验、提交、存储、
   更新、查询和命中解释。语义单元的产生、筛选、压缩、打标签、聚合、覆盖规划
   一律在执行层，不在 core。

3. **可摘除**
   执行层整体摘除后，core 仍能基于 fixture / replay proposal 与已有 overlay state
   完成提交、维护、查询和导航；但 core 不能独立从原始文件生成任何语义判断
   （高价值判断、tag、node 聚合、语义解释）。

4. **行数是检验器，且不可被搬运规避**
   core（导航链物化、状态维护、查询与命中解释代码）< 200；
   kernel（除 search 薄封装 / 配置 / SOP 文档 / 实验测试脚本外的全部源码）< 500，按逻辑行计。
   达标只许靠剥离能力，不许压格式；不计入行数的 SOP / prompt / 配置不得承载
   case-specific 路径规则、业务分类或硬编码语义策略，否则视为未轻量。

5. **单写入**
   只有一条代码路径能写 overlay 主索引；建造期 subAgent 只交 proposal。

6. **多源信号、分立可溯、不伪 semantic**
   语义证据、路径、目录、文件名、元信息都是合法信号来源，都必须保留并标明 provenance 通道；
   命中可以是多通道组合，但 semantic 命中必须来自已存在的 tag/node/leaf 语义链路，
   不能仅由文件名 / 路径 / 元信息推导冒充。

7. **覆盖状态是链路结构的一部分，不自欺**
   覆盖 / 采样状态必须可在链路中表达并随查询和报告保留（覆盖边界、采样与否、
   fresh/stale、已知未覆盖区域 / 未确认区域）。基于采样产生的 node/leaf 可进入链路，但任何结论
   不得把采样说成完整覆盖；case-specific 调参默认拒绝。

8. **持续更新是维护，不是重规划**
   持续更新只维护已有链路状态（freshness/stale/warm-cold、文件存在性、变更探测）
   并应用 proposal-based 修订；任何重新筛选、重新打标签、重新聚合都必须作为
   执行层 proposal 重新进入 core，core 不得自行发起语义重规划。

9. **可归因（C vs D）**
   收益必须能区分来自 LSO 导航链 / overlay index（C：已构建链路是否比搜索更稳、
   可复用、可解释），还是来自 subAgent 构建与更新编排
   （D：多智能体建造是否产出更高质量的链路；D 主要是建造期收益，
   非每个运行期任务都开 subAgent）。

10. **真有用**
    未经调参的真实文件任务走运行期路径，比盲搜更稳、可复用、可解释。

## 过程不变量

11. **审核与判定分离，判官独立**
    master / 大脑只能整理 proposal、发起复核、准备准备写入请求 / 绿色基线提交请求；
    是否通过由独立 audit（用冻结的问题清单出 verdict，留痕可查）+ 机械门共同决定。 audit 必须基于 proposal、provenance、gate 输出和本契约判定，不得只看 master 的自然语言总结。
    "审核"不等于"判定通过"。

12. **commit 锁死，主索引与日志分离**
    Git commit 仅在"机械门绿 AND audit pass"时由 gate 执行，用于固化新的绿色 baseline；
    master 无手工 Git commit、无"有条件合入"核心交付物的权力。

    overlay 主索引只能通过单写入入口写入；master 不直接写 overlay。
    未通过的 proposal 不得进入 overlay 主索引，但可进入 run log / audit log
    以追踪失败原因。
