"""纠偏监控器 - 接收文本，驱动detector检测+player播报"""
import threading

import player
from detector import StageDetector

detector = StageDetector()
_on_alert = None
_on_info = None
_reset_timer = None

def stage_name():
    return "等待开始" if detector.current_stage_idx < 0 else detector.stages[detector.current_stage_idx]["name"]

def feed(new_part):
    alerts = detector.force_check_timeout() if new_part is None else detector.update(new_part) + detector.force_check_timeout()
    _dispatch(alerts)
    return alerts

def _dispatch(alerts):
    for alert in alerts:
        atype, text = alert[0], alert[1]
        aid = alert[2] if len(alert) > 2 else None
        just_entered = alert[3] if len(alert) > 3 else False
        if _on_alert:
            _on_alert(atype, text, aid, just_entered)
        if atype == "stage_enter" or (atype == "must_ok" and not just_entered):
            player.play("ding")
        elif atype not in ("must_ok", "skipped", "stage_reenter"):
            player.play(text)
        if atype == "must_ok" and aid == "send_off":
            detector.reset()
        elif text == "送客到门口":
            _schedule_reset()

def _schedule_reset():
    global _reset_timer
    if _reset_timer and _reset_timer.is_alive():
        return
    _reset_timer = threading.Timer(60.0, detector.reset)
    _reset_timer.daemon = True
    _reset_timer.start()
    if _on_info:
        _on_info("[送客] 60s后自动开启新会话")

def init(on_alert=None, on_info=None):
    global _on_alert, _on_info
    _on_alert = on_alert
    _on_info = on_info
