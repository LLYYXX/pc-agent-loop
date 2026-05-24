# realtime_correction_sop

实时销售话术纠偏工具。用于监听本地麦克风，经 ASR 输出增量文本，再按销售 SOP 规则触发预设提醒音频。

## 何时使用

用户要求“实时销售纠偏 / must 项提醒 / 销售 SOP 播报 / 话术违规提醒”时使用本 SOP。

不要把它当完整质检系统。当前只做：

```text
本地语音输入 -> ASR 文本 -> 阶段/must/forbidden 检测 -> 播放预设音频 -> 写日志
```

## 启动

从 repo 根目录运行。当前只保留 Web 监控入口：

```python
import pathlib, subprocess, webbrowser

cwd = pathlib.Path("memory/realtime_correction_sop").resolve()
proc = subprocess.Popen(["python", "web_server.py"], cwd=cwd)
print("pid =", proc.pid)
webbrowser.open("http://localhost:5000")
```

停止：

```powershell
taskkill /F /PID <pid>
```

## 测试

当前只支持麦克风实机测试。无麦克风环境下无法验证检测链路。
如未来需要无麦克风调试能力，届时按实际系统形态重新实现。

## 修改规则

只改 `memory/realtime_correction_sop/sop_rules.py`。

规则结构：

```text
stages[].enter_keywords    进入阶段触发词
stages[].reenter_keywords  回退到旧阶段触发词
stages[].hint_next         本阶段 must 全完成后是否提示进入下一阶段
stages[].must[]            必做项：id/desc/keywords/alert/timeout_sec
stages[].forbidden[]       禁止项：id/keywords/negative/alert/cooldown_sec
```

拿捏原则：

- 没有 must 的阶段通常不要 `hint_next`。
- 体验、谈判、等待客户决策类阶段通常不要催下一阶段。
- 新增或改名 `alert` 后，必须重新生成音频。音频文件是脚本产物，不作为必须上传内容。

生成音频：

```powershell
cd memory\realtime_correction_sop
python generate_alerts.py
```

## 输出位置

运行日志路径由代码决定，当前入口在：

```text
memory/realtime_correction_sop/detector.py
```

当前实现写到：

```text
temp/realtime_correction_sop/logs/
```

如果要核对或修改日志位置，读 `detector.py`，不要只改 SOP。

## 文件职责

```text
web_server.py   Web 入口，Socket 推送实时状态
asr_engine.py   麦克风采集和滑窗 ASR
monitor.py      detector 与 player 的协调层
detector.py     阶段、must、forbidden、超时、冷却
player.py       按音频名播放 alerts_audio/ 下的预生成音频
sop_rules.py    销售 SOP 示例规则
generate_alerts.py 生成 alerts_audio/ 下的 ding 与 alert 音频
```

## 硬约束

- SOP 只约束使用边界和改动入口；代码已经决定的行为，以代码为准，SOP 只指明去哪里读。
- 不让 GA 进入实时判断闭环，只负责启动、停止、看日志和改规则。
- 不动态生成播报话术；运行时只播放 `alerts_audio/` 里的预生成音频。
- 不把 ASR 原文直接播出来。
- 不把关键词规则扩成开放语义判断；需要语义能力时，应新增证据适配层，不改播报决策边界。
- 回声污染仍是已知风险，优先使用耳机输出。
