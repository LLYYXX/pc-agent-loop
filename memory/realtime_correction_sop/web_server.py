"""实时纠偏 - Web前端版入口"""
import threading
import time

from flask import Flask, render_template
from flask_socketio import SocketIO

import asr_engine
import monitor

app = Flask(__name__)
# 后台线程（ASR / 0.5s tick）推送：threading + app_context + emit（无 to 即广播）
sio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

ALERT_LOGS = {
    "stage_enter": ("stage", "进入: "),
    "stage_reenter": ("back", "回退: "),
    "hint_next": ("warn", ""),
    "skipped": ("warn", "跳过: "),
    "must_ok": ("ok", "命中: "),
    "must_timeout": ("warn", "超时: "),
    "forbidden": ("error", "违规: "),
    "session_end": ("info", ""),
}


def _emit(event: str, payload=None):
    """从 ASR / tick 等后台线程向前端推送（非 request 上下文；不传 to 即全体客户端）。"""
    with app.app_context():
        if payload is None:
            sio.emit(event)
        else:
            sio.emit(event, payload)


def on_alert(ev):
    if ev.type in ("stage_enter", "stage_reenter"):
        _emit("stage", ev.message)
    level, prefix = ALERT_LOGS.get(ev.type, ("info", ""))
    _emit("log", {"level": level, "text": f"{prefix}{ev.message}"})


def on_info(msg):
    _emit("log", {"level": "info", "text": msg})
    if "新会话" in msg:
        _emit("stage", "等待开始")
        _emit("reset")


def _feed(new_part):
    monitor.feed(new_part)
    if new_part:
        _emit("text", {"stage": monitor.stage_name(), "text": new_part})


monitor.init(on_alert=on_alert, on_info=on_info)


@app.route("/")
def index():
    return render_template("index.html")


@app.post("/reset")
def reset():
    monitor.reset_session()
    return {"ok": True}


if __name__ == "__main__":
    def _tick():
        while True:
            time.sleep(0.5)
            _feed(None)

    threading.Thread(target=_tick, daemon=True).start()
    asr_engine.start(on_text=_feed)
    sio.run(app, host="0.0.0.0", port=5000, allow_unsafe_werkzeug=True)
