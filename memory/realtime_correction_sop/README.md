# 实时销售 SOP 纠偏 Demo 对接手册

本目录是上汽 MG 0514 门店接待 SOP 的实时纠偏 demo 运行时。它的目标不是做完整质检系统，而是在接待现场用本地 ASR、轻量规则和预生成音频，发现少数关键 must 缺失或高风险表达，并让销售当场补一句。

```text
销售说话
-> 本地 ASR 输出文本
-> detector 按阶段、触发条件、超时和命中证据判断
-> monitor 播放预设音频并推送事件
-> Web 页面展示文本、阶段、提醒和命中日志
```

## 当前交付状态

当前版本已经接入 `shangqi-SOP-0514.xlsx` 的可监测主 SOP 项，运行真相在 `sop_rules.py`。Excel 是业务来源和核对依据，运行时不读取 Excel。

已交付内容：

| 内容 | 状态 |
|------|------|
| 0514 主 SOP must 规则 | 已配置到 `sop_rules.py`，包含迎接、需求、产品、留资、试驾前、试驾中、试驾后 |
| 场景跳转 | 支持顺序推进、受控跨阶段、回退和 skipped 记录 |
| 缺失提醒 | 支持规则 armed 后超时提醒 |
| 补救命中 | 支持超时提醒后继续命中并记录“命中” |
| 预生成音频 | `alerts_audio/` 中按 `audio_id` 提供 wav |
| Web 展示 | Flask + Socket.IO 展示文本、阶段、日志 |
| 测试脚本（非现场交付） | `test_0514_sop_full_triggers.py`：present/missing、clean flow、补救、跳阶、变体、负例与 gap 追踪 |

当前仍未覆盖的已知 gap：

```text
1. “您是之前电话联系过的吗”这类预约确认自然表达
2. “咱们这段路大概开十来分钟”这类路线说明自然表达
3. “试驾确认扫一下”这类协议确认自然表达
4. “手机导航同步到车机”这类手机互联自然表达
5. extra 产品讲解素材的 evidence layer
```

`shangqi-SOP-extra.xlsx` 当前只作为后续产品 evidence 词库和语义层素材，不作为实时纠偏主规则全量启用。

## 交付产物

建议按下面范围交付。`memory/realtime_correction_sop/` 是主产物；`temp/` 下的模型、讲稿和真实 SOP 来源按交付场景选择是否一并提供。

### 主文档

| 路径 | 说明 |
|------|------|
| `memory/realtime_correction_sop.md` | 给 GA / 智能体看的操作 SOP：何时使用、如何启动、如何改规则和音频 |
| `memory/realtime_correction_sop/README.md` | 本对接手册：交付状态、启动、接口、规则、语义层接入 |

### 运行时目录

| 路径 | 说明 |
|------|------|
| `memory/realtime_correction_sop/asr_engine.py` | 本地麦克风采集和 sherpa-onnx ASR |
| `memory/realtime_correction_sop/monitor.py` | 对外 `feed()` 入口；驱动 detector；分发事件；调用播放器 |
| `memory/realtime_correction_sop/detector.py` | 薄 facade；持有会话状态；调用 `match.engine` |
| `memory/realtime_correction_sop/match/` | 规则匹配内核：阶段、must、forbidden、事件、会话状态 |
| `memory/realtime_correction_sop/sop_rules.py` | 0514 SOP 运行规则，当前唯一运行真相 |
| `memory/realtime_correction_sop/alerts_audio/` | 预生成提醒音频，运行时按 `audio_id` 播放 |
| `memory/realtime_correction_sop/generate_alerts.py` | 根据 `sop_rules.py` 检查/生成提醒音频 |
| `memory/realtime_correction_sop/player.py` | wav 播放，使用当前默认输出设备 |
| `memory/realtime_correction_sop/io_devices.py` | 输入/输出设备选择与热插拔重试 |
| `memory/realtime_correction_sop/web_server.py` | 当前 demo 后端入口 |
| `memory/realtime_correction_sop/templates/index.html` | 当前 demo 前端页面，已接入 REST 重置和 Socket.IO 推送 |
| `memory/realtime_correction_sop/tests/test_0514_sop_full_triggers.py` | 开发/验收自测脚本，非现场交付物 |

### 可选交付材料

| 路径 | 说明 |
|------|------|
| `temp/realtime_correction_sop/基于测试脚本的全流程讲稿.md` | 与测试脚本对应的自然语言演示讲稿，可用于现场演示 |
| `temp/realtime_correction_sop/source/shangqi-SOP-0514.xlsx` | 真实主 SOP 来源；涉及业务资料，按权限决定是否交付 |
| `temp/realtime_correction_sop/source/shangqi-SOP-extra.xlsx` | 产品讲解素材来源；按权限决定是否交付 |
| `temp/realtime_correction_sop/logs/` | 本机运行日志，不建议作为正式交付内容 |
| `temp/realtime_asr_sherpa_v2.py` | 历史 ASR 基础脚本，通常不作为运行入口；需要追溯 ASR 采集方案时可附带 |

### ASR 模型

当前 ASR 使用 sherpa-onnx SenseVoice int8 模型，代码默认读取：

```text
temp/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17/
```

该目录至少需要：

```text
model.int8.onnx
tokens.txt
```

当前本机还保留了模型压缩包：

```text
temp/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17.tar.bz2
```

目标机部署时可以直接复制解压后的模型目录，也可以下载同名模型包：

```text
https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17.tar.bz2
```

如果目标机模型放在其他位置，需要同步修改 `asr_engine.py` 中的 `MODEL_DIR`，或在后续版本把模型路径改成配置项。

## 目录与职责

统计范围：`memory/realtime_correction_sop/`。智能体启停说明在上一级 `memory/realtime_correction_sop.md`（约 50 行），不计入下表。

### 文件树

```text
memory/realtime_correction_sop/
├── asr_engine.py              # 麦克风 + SenseVoice 滑窗 ASR
├── io_devices.py              # 系统默认 in/out；热插拔/异常时重开流
├── monitor.py                 # feed、播报、新接待、送客 60s 自动重置
├── detector.py                # Session + match.engine.tick 薄封装
├── player.py                  # 预生成 wav 播报
├── web_server.py              # Flask + Socket.IO demo 入口
├── sop_rules.py               # 0514 规则表（运行真相）
├── generate_alerts.py         # 按 audio_id 生成/检查 alerts_audio
├── README.md                  # 本对接手册
├── templates/index.html       # 监控页（含「新接待」）
├── tests/
│   └── test_0514_sop_full_triggers.py   # 开发/验收自测，非现场交付
└── match/
    ├── engine.py              # tick 编排（唯一匹配入口）
    ├── stage.py               # 阶段、受控跳阶、回退、skipped、hint_next
    ├── must.py                # arm、证据、超时、alerted 后补救命中
    ├── forbidden.py
    ├── events.py
    ├── session.py
    ├── session_log.py
    ├── rules_util.py
    └── text_match.py
```

运行期还会写入 `temp/realtime_correction_sop/logs/`；播报依赖 `alerts_audio/{audio_id}.wav`（由 `generate_alerts.py` 生成）。

### 代码量

| 分类 | 文件数 | 行数 | 是否现场交付 |
|------|--------|------|----------------|
| 核心运行时（`match/` + 管道 6 个 `.py`） | 17 | 1062 | 是 |
| 规则配置 `sop_rules.py` | 1 | 323 | 是（业务配置） |
| Demo UI（`web_server.py` + `templates/index.html`） | 2 | 138 | 是（当前 demo） |
| 音频生成 `generate_alerts.py` | 1 | 84 | 运维脚本，非接待时运行 |
| 自测 `tests/test_0514_sop_full_triggers.py` | 1 | 311 | 否 |
| 文档 `README.md` | 1 | 350 | 否（开发/对接用） |
| **模块内合计** | **21** | **1897** | — |

核心运行时拆分（1062 行）：

| 文件 | 行数 | 职责 |
|------|------|------|
| `match/must.py` | 149 | 激活、命中、超时、补救 |
| `match/stage.py` | 112 | 阶段推进、skipped、hint_next |
| `asr_engine.py` | 103 | 采集与识别回调 |
| `monitor.py` | 56 | Event 分发与播报 |
| `match/session.py` | 52 | 会话与规则状态 |
| `web_server.py` | 48 | HTTP + Socket.IO |
| `io_devices.py` | 49 | 设备选择 |
| `match/forbidden.py` | 36 | 违规监听 |
| `match/engine.py` | 37 | tick 顺序 |
| `player.py` | 36 | 播放 |
| 其余 `match/*`、`detector.py` | ~82 | Event、日志、工具函数 |

含规则表的业务实现体量约 **1385 行**（1062 运行时 + 323 `sop_rules.py`）。

运行产物和业务来源：

| 路径 | 用途 |
|------|------|
| `temp/realtime_correction_sop/source/shangqi-SOP-0514.xlsx` | 主 SOP 来源 |
| `temp/realtime_correction_sop/source/shangqi-SOP-extra.xlsx` | 产品讲解素材来源 |
| `temp/realtime_correction_sop/logs/` | 运行日志 |
| `memory/realtime_correction_sop/alerts_audio/` | 预生成提醒音频 |

## 使用方式

### 1. GA / 智能体接手

让 GA 或其他智能体接手前，先明确告诉它：

```text
本项目存在实时销售 SOP 纠偏 demo。
运行时位于 memory/realtime_correction_sop。
业务说明见 memory/realtime_correction_sop.md。
当前 0514 SOP 的运行真相是 memory/realtime_correction_sop/sop_rules.py。
不要重新从 Excel 推翻结构；Excel 只用于核对和补规则。
```

如果该智能体有全局记忆能力，建议把上述信息加入全局记忆，避免它把本项目当成普通 Web 项目或重新设计大平台。

### 2. 手动启动 demo

交付时不要依赖开发者本机的虚拟环境。目标机应自行准备 Python 环境，并安装运行依赖。

建议环境：

```text
Python 3.10 或 3.11
Windows + 可用麦克风/扬声器
```

核心 Python 依赖：

```text
numpy
pyaudio
sherpa-onnx
flask
flask-socketio
edge-tts      # 仅生成音频时需要
miniaudio     # 仅生成音频时需要
```

启动方式：

```powershell
cd <repo>\memory\realtime_correction_sop
python web_server.py
```

浏览器打开：

```text
http://localhost:5000
```

如果一台机器上有多个 Python 环境，先确认当前解释器：

```powershell
python -c "import sys; print(sys.executable)"
```

### 3. 音频检查和生成

```powershell
cd <repo>\memory\realtime_correction_sop
python generate_alerts.py --check
```

缺少音频时生成：

```powershell
python generate_alerts.py
```

运行时只引用 `alerts_audio/{audio_id}.wav`，不做实时 TTS。

### 4. 测试

```powershell
cd <repo>
python memory\realtime_correction_sop\tests\test_0514_sop_full_triggers.py
```

`test_0514_sop_full_triggers.py` 是当前最接近验收规格的脚本，覆盖：

```text
Excel/SOP ref -> rule_id 映射
每条 must present / missing
完整 clean flow
缺失 -> 提醒 -> 补救
跳阶段不阻断
部分真实话术变体
负例不误报
已知 gap 追踪
```

## 对接接口

如果只替换 Web 页面或接入其他后端，优先对接 `monitor.py`。如果不需要播放音频，只想拿规则事件，可以直接对接 `detector.py` 或 `match.engine`。

### 前端 / 后端接口

当前 demo 的前端后端契约由 REST + Socket.IO 组成。

HTTP / REST：

| 方法 | 路径 | 请求 | 响应 | 用途 |
|------|------|------|------|------|
| `GET` | `/` | 无 | HTML 页面 | 返回当前 demo 前端 |
| `POST` | `/reset` | 无 body | `{"ok": true}` | 开启新接待；`monitor.reset_session()`，并向前端推送 `stage` 和 `reset` |

Socket.IO：后端推送给前端。

| 事件 | payload | 用途 |
|------|---------|------|
| `text` | `{"stage": 当前阶段名, "text": 新增识别文本}` | 展示 ASR 增量文本 |
| `stage` | 阶段名字符串 | 更新页面阶段 badge |
| `log` | `{"level": 等级, "text": 日志文本}` | 展示提醒、命中、跳过、违规、信息 |
| `reset` | 无 | 清空当前页面文本和提醒记录 |

`log.level` 当前取值：

| level | 含义 |
|-------|------|
| `stage` | 进入阶段 |
| `back` | 回退阶段 |
| `warn` | must 超时、跳过阶段、下一阶段提示 |
| `error` | forbidden 违规 |
| `ok` | must 命中 |
| `info` | 普通系统信息 |

当前前端不向后端发送 Socket.IO 事件；用户操作通过页头「新接待」调用 `POST /reset`。

### 后端内部事件回调

`monitor.init(on_alert, on_info)` 注册两个回调：

| 回调 | 参数 | 触发时机 |
|------|------|----------|
| `on_alert(event)` | `match.events.Event` | detector 产生阶段、命中、超时、跳过、违规等事件时 |
| `on_info(message)` | `str` | 系统信息，例如自动开启新会话 |

当前 `web_server.py` 把 `on_alert(event)` 转成 Socket.IO：

```text
stage_enter / stage_reenter -> emit("stage", event.message)
所有 event -> emit("log", {"level": ..., "text": ...})
```

把 `on_info(message)` 转成：

```text
emit("log", {"level": "info", "text": message})
必要时 emit("stage", "等待开始") 和 emit("reset")
```

其他 Web Server 可以复用这个映射，也可以直接按 `Event` 字段设计自己的协议。

### 方案 A：接入 monitor，保留播放器

适合继续使用本地音频播报，只替换 Web Server。

```python
import monitor


def on_alert(event):
    # event.type / event.message / event.rule_id / event.audio_id / event.stage / event.meta
    push_to_client(event)


def on_info(message):
    push_info(message)


monitor.init(on_alert=on_alert, on_info=on_info)

# ASR 有新文本时调用
events = monitor.feed("您好欢迎光临")

# 没有新文本时也要定时 tick，用于超时判断
events = monitor.feed(None)

# 当前阶段
stage = monitor.stage_name()
```

调用约定：

| 调用 | 说明 |
|------|------|
| `monitor.feed(text)` | 输入 ASR 新增文本或一句转写文本 |
| `monitor.feed(None)` | 定时 tick，用于触发超时，建议 0.5s 左右 |
| `monitor.init(on_alert, on_info)` | 注册事件回调 |
| `monitor.stage_name()` | 获取当前阶段名 |

`monitor.feed()` 会产生播放副作用：`must_timeout`、`forbidden`、`hint_next` 播放对应 `audio_id`；`stage_enter`、`must_ok` 播放 ding。

### 方案 B：只接 detector，不自动播放

适合其他后端自己决定如何展示、播放或上报事件。

```python
from detector import StageDetector

detector = StageDetector()

events = detector.tick("您好欢迎光临")
events += detector.force_check_timeout()

for event in events:
    handle_event(event)
```

这种方式不会自动调用 `player.play()`。外部系统可以按 `Event` 自己处理日志、音频、WebSocket 或数据库写入。

### 方案 C：直接接 match.engine 做纯规则测试

适合单测、批量回放和离线评估。

```python
from match.engine import tick
from match.session import Session
from sop_rules import SOP_RULES

session = Session()
events = tick("您好欢迎光临", session, SOP_RULES)
events += tick(None, session, SOP_RULES)
```

### Event 结构

`match.events.Event` 字段：

| 字段 | 含义 |
|------|------|
| `type` | 事件类型 |
| `message` | 页面/日志展示文案 |
| `rule_id` | 命中的规则 id，阶段事件可为空 |
| `audio_id` | 需要播放的音频 id |
| `stage` | 当前阶段名 |
| `meta` | 额外信息 |

当前事件类型：

| type | 含义 |
|------|------|
| `stage_enter` | 进入新阶段 |
| `stage_reenter` | 回退到前序阶段 |
| `skipped` | 跨阶段时记录被跳过环节 |
| `must_ok` | must 已命中，包括提醒后的补救命中 |
| `must_timeout` | must 超时提醒 |
| `forbidden` | 命中禁止表达 |
| `hint_next` | 当前阶段完成后提示进入下一环节 |
| `session_end` | 会话结束 |

Web 日志推荐映射：

```text
stage_enter   -> 进入: xxx
stage_reenter -> 回退: xxx
skipped       -> 跳过: xxx
must_timeout  -> 超时: xxx
must_ok       -> 命中: xxx
forbidden     -> 违规: xxx
hint_next     -> xxx
session_end   -> 会话结束
```

## 规则配置说明

`sop_rules.py` 是唯一运行真相。每个阶段包含：

```text
id / name
enter_keywords    弱进入，一般只推进到下一阶段
enter_strong      强进入，可以跨阶段，但必须是边界清晰的业务证据
reenter_keywords  回退证据
hint_next         本阶段 must 完成后是否提示下一环节
must[]            本阶段 must 项
forbidden[]       本阶段 forbidden 项
```

非线性规则放在：

```text
insert_rules[]
```

例如离店留资、二次邀约试驾，不强绑死单一线性阶段。

每条 must 常用字段：

| 字段 | 用途 |
|------|------|
| `id` | 稳定规则 id，也是默认音频 id |
| `desc` | 内部描述 |
| `scene` | 业务场景 |
| `keywords` | 完成证据 |
| `trigger` | 前置触发证据 |
| `activate` | 何时开始等待完成 |
| `timeout_sec` | 从 armed 开始到提醒的秒数 |
| `alert` | 提醒文案 |
| `audio_id` | 对应 `alerts_audio/{audio_id}.wav` |

`activate.mode` 当前支持：

| mode | 说明 |
|------|------|
| `after_stage_enter` | 进入阶段后开始等待 |
| `on_trigger` | 触发词出现后开始等待 |
| `on_scene_enter` | 命中局部场景词后开始等待 |
| `on_behavior` | 某类行为持续一段时间后开始等待 |
| `on_leave_intent` | 离店意图触发 |
| `on_end_view` | 结束看车、聊价等触发 |
| `on_second_invite` | 邀约被拒后触发二次邀约 |
| `after_drive_enter` | 进入试驾环节后触发 |

配置原则：

```text
全量 SOP 可以入库，但不能全局同时倒计时。
阶段跳过是必要能力，只要跳转证据业务上说得通。
跳过只记录 skipped，不阻断后续链路。
超时提醒不是终态，销售补救后仍应记录命中。
播报顺序优先靠 activate 和 timeout 配置解决，不靠运行时吞事件兜底。
```

## 接入语义层方案

当前系统已经适合接入 BERT、embedding 或轻量 NLU，但还没有正式的语义层接口。建议后续只加一个薄层，不改变 detector 决策边界。

目标结构：

```text
ASR 文本
-> semantic_tagger 产出 evidence tags
-> match 层消费 tags + 关键词
-> detector 根据阶段、armed 状态、超时和补救决定 Event
```

建议新增：

```text
semantic_tagger.py
  tag(text, session) -> list[EvidenceTag]
```

示例输出：

```python
[
    {"id": "confirm_appointment", "source": "bert", "confidence": 0.86},
    {"id": "phone_connect", "source": "embedding", "confidence": 0.81},
]
```

规则中可新增：

```python
"evidence_tags": ["confirm_appointment"],
"trigger_tags": ["customer_high_intent"],
```

`must.py` 只需要把判断扩展为：

```text
关键词命中 OR evidence tag 命中
trigger 关键词命中 OR trigger tag 命中
```

边界要求：

```text
BERT / embedding 只负责发现 evidence。
是否 armed、是否超时、是否提醒、是否补救完成，仍由 detector/match 本地状态逻辑决定。
提醒音频仍使用预生成 audio_id，不做实时生成。
extra Excel 优先进入 evidence 素材库，不直接变成全量实时 must。
```

优先接入的语义 gap：

```text
电话联系 -> confirm_appointment
这段路十来分钟 -> drive_route
试驾确认扫一下 -> sign_agreement
手机导航同步到车机 -> phone_connect
MG4/MG7 产品讲解点 -> product evidence tags
```

## 交付注意事项

- 当前 Web Server 是 demo 入口，不是唯一对接方式；其他后端按 `monitor.feed()` 或 `StageDetector.tick()` 接入即可。
- 当前前端通过页头「新接待」调用 `POST /reset`（内部 `monitor.reset_session()`），通过 Socket.IO 接收实时文本和日志。
- 修改 `sop_rules.py` 后必须检查音频：`generate_alerts.py --check`。
- 新增 `audio_id` 后必须生成对应 wav。
- 变更规则后先跑 `test_0514_sop_full_triggers.py`，再跑现场演示。
- 如果让其他智能体继续开发，先给它本 README、`../realtime_correction_sop.md` 和当前 `sop_rules.py`，避免重新设计架构。
