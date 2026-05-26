# realtime_correction_sop

智能体在「实时销售话术纠偏」场景下的操作说明。

## 何时用

用户要**现场演示**、**调整提醒内容**，或提到销售话术遗漏实时播报时。

## 启停演示

从仓库根目录：

```python
import pathlib, subprocess, webbrowser

cwd = pathlib.Path("memory/realtime_correction_sop").resolve()
proc = subprocess.Popen(["python", "web_server.py"], cwd=cwd)
print("pid =", proc.pid)
webbrowser.open("http://localhost:5000")
```

结束（`<pid>` 换为打印值）：

```powershell
taskkill /F /PID <pid>
```

- 浏览器看当前阶段、识别文本、提醒记录。
- 现场戴耳机，避免播报被麦克风拾取。
- 需麦克风；无麦克风无法演示。

## 改规则与音频

| 项 | 路径 / 命令 |
|----|-------------|
| 规则表 | `memory/realtime_correction_sop/sop_rules.py` |
| 生成提醒音 | 在该目录执行 `python generate_alerts.py` |

## 日志

`temp/realtime_correction_sop/logs/`

## 操作清单

| 用户诉求 | 动作 |
|----------|------|
| 开始演示 | 启动 `web_server.py`，打开 `http://localhost:5000` |
| 结束演示 | `taskkill` 结束进程 |
| 改提醒文案或触发 | 改 `sop_rules.py` → `generate_alerts.py` |
| 演示现象不对 | 先看日志与规则表；仅当用户明确要求再改 `memory/realtime_correction_sop/` 下其它代码 |
