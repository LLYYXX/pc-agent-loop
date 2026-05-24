# 实时销售纠偏系统

轻量 demo-first 实现。入口 SOP 见 `../realtime_correction_sop.md`。

## 运行链路

```text
asr_engine -> monitor -> detector -> player
                 |
              sop_rules.py
```

## 手动运行

从仓库根目录启动 Web 监控页：

```powershell
cd memory\realtime_correction_sop
python web_server.py
```

然后打开：

```text
http://localhost:5000
```

停止时在终端按 `Ctrl+C`。

## 模块职责

```text
asr_engine.py   本地麦克风采集、滑窗 ASR、输出增量文本
monitor.py      接收文本或 tick，调用 detector，再分发提示和播放
detector.py     阶段切换、must 命中、must 超时、forbidden、冷却、日志
player.py       按音频名播放 alerts_audio/ 下的预生成音频
web_server.py   Web UI 与 Socket 推送入口
sop_rules.py    示例销售 SOP 规则
generate_alerts.py 生成 alerts_audio/ 下的 ding 与 alert 音频
```

## 维护边界

- 真实 SOP 适配优先改 `sop_rules.py`。
- 新增 alert 文本后运行 `generate_alerts.py`；生成出的 wav 不需要上传。
- 运行日志位置由 `detector.py` 决定；当前写到 `temp/realtime_correction_sop/logs/`。
- `detector.py` 是判断核心，除非规则语义确实变复杂，否则不要拆成平台。

## 当前限制

- 关键词规则可能被 ASR 分段切断。
- 当前只支持麦克风实机测试。
- 播报声可能被麦克风拾取，现场优先戴耳机。
- 规则是汽车销售 demo，需要替换成真实 SOP 并重新适配。
- 未加入语义分析相关功能，目前只有关键词匹配。
