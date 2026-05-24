"""实时纠偏 - Web前端版入口"""
from flask import Flask, render_template
from flask_socketio import SocketIO
import asr_engine, monitor

app = Flask(__name__)
sio = SocketIO(app, cors_allowed_origins="*")

ALERT_LOGS = {
    "stage_enter": ("stage", "进入: "),
    "stage_reenter": ("back", "回退: "),
    "hint_next": ("warn", ""),
    "skipped": ("warn", "跳过: "),
    "must_ok": ("ok", "命中: "),
    "must_timeout": ("warn", "超时: "),
    "forbidden": ("error", "违规: "),
}

def on_alert(atype, text, aid, just_entered):
    if atype in ("stage_enter", "stage_reenter"):
        sio.emit('stage', text)
    level, prefix = ALERT_LOGS.get(atype, ("info", ""))
    sio.emit('log', {'level': level, 'text': f'{prefix}{text}'})

def on_info(msg):
    sio.emit('log', {'level': 'info', 'text': msg})
    if '新会话' in msg:
        sio.emit('stage', '等待开始')
        sio.emit('reset')

def _feed(new_part):
    monitor.feed(new_part)
    if new_part:
        sio.emit('text', {'stage': monitor.stage_name(), 'text': new_part})

monitor.init(on_alert=on_alert, on_info=on_info)

@app.route('/')
def index():
    return render_template('index.html')

if __name__ == '__main__':
    import threading, time
    def _tick():
        while True:
            time.sleep(0.5)
            _feed(None)
    threading.Thread(target=_tick, daemon=True).start()
    asr_engine.start(on_text=_feed)
    sio.run(app, host='0.0.0.0', port=5000, allow_unsafe_werkzeug=True)
