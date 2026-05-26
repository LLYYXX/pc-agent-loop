"""纠偏监控器 - 接收文本，驱动 detector，按 Event 播报"""
import threading

import player
from detector import StageDetector
from match.events import Event

detector = StageDetector()
_on_alert = None
_on_info = None
_reset_timer = None
_lock = threading.RLock()


def stage_name():
    with _lock:
        return detector.stage_name()


def feed(new_part):
    with _lock:
        if new_part is None:
            events = detector.force_check_timeout()
        else:
            events = detector.tick(new_part) + detector.force_check_timeout()
        _dispatch(events)
    return events


def reset_session(msg="[新会话开始]"):
    """立即清空会话（取消待执行的自动重置）。"""
    global _reset_timer
    timer = _reset_timer
    if timer and timer.is_alive():
        timer.cancel()
    _reset_timer = None
    with _lock:
        detector.reset()
    if _on_info:
        _on_info(msg)


def _dispatch(events: list[Event]):
    for ev in events:
        if _on_alert:
            _on_alert(ev)
        if ev.type == "session_end":
            _schedule_reset()
            continue
        if ev.type in ("stage_enter", "must_ok"):
            threading.Thread(target=player.play, args=("ding",), daemon=True).start()
        elif ev.is_audio_alert():
            threading.Thread(target=player.play, args=(ev.audio_id,), daemon=True).start()


def _schedule_reset():
    global _reset_timer
    if _reset_timer and _reset_timer.is_alive():
        return
    _reset_timer = threading.Timer(60.0, reset_session)
    _reset_timer.daemon = True
    _reset_timer.start()
    if _on_info:
        _on_info("[送客] 60s后自动开启新会话")


def init(on_alert=None, on_info=None):
    global _on_alert, _on_info
    _on_alert = on_alert
    _on_info = on_info
