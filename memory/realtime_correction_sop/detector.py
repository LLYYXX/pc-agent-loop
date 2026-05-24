"""阶段检测引擎 - 匹配当前阶段、检测must遗漏和forbidden违规"""
import time
from datetime import datetime
from pathlib import Path

from sop_rules import SOP_RULES

LOG_DIR = Path(__file__).resolve().parents[2] / "temp" / "realtime_correction_sop" / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

def _new_log_path():
    return LOG_DIR / f"{datetime.now():%Y%m%d_%H%M%S}.txt"

_log_path = _new_log_path()

def _log(msg):
    with _log_path.open('a', encoding='utf-8') as f:
        f.write(f"{datetime.now():%H:%M:%S} {msg}\n")

def _rotate_log():
    global _log_path
    _log_path = _new_log_path()

class StageDetector:
    def __init__(self):
        self.stages = SOP_RULES["stages"]
        self.current_stage_idx = -1
        self.stage_enter_time = 0
        self.must_done = {}
        self.must_alerted = {}
        self.last_triggered = {}
        self.full_text = ""
        self.must_all_done_time = 0

    def reset(self):
        _rotate_log()
        self.current_stage_idx = -1
        self.stage_enter_time = 0
        self.must_done.clear()
        self.must_alerted.clear()
        self.last_triggered.clear()
        self.full_text = ""
        self.must_all_done_time = 0
        print("\n[新会话开始]", flush=True)

    def update(self, new_text):
        self.full_text += new_text
        alerts = []
        ev = self._check_stage_enter(new_text)
        just_entered = ev is not None
        if ev:
            alerts.extend(ev[3] if len(ev) > 3 else [])
            alerts.append(ev[:3])

        if self.current_stage_idx < 0:
            return alerts

        stage = self.stages[self.current_stage_idx]
        done = self.must_done.setdefault(self.current_stage_idx, set())
        must_items = stage["must"]
        all_ids = {m["id"] for m in must_items}
        for must in must_items:
            if must["id"] in done or not any(kw in new_text for kw in must["keywords"]):
                continue
            done.add(must["id"])
            alerts.append(("must_ok", must["alert"], must["id"], just_entered))
            if not just_entered:
                _log(f'[命中] {stage["name"]} - {must["desc"]}')
        if self.must_all_done_time == 0 and stage.get("hint_next") and all_ids and done >= all_ids:
            self.must_all_done_time = time.time()

        now = time.time()
        for fb in stage.get("forbidden", []):
            if not any(kw in new_text for kw in fb["keywords"]):
                continue
            if now - self.last_triggered.get(fb["id"], 0) < fb.get("cooldown_sec", 10):
                continue
            neg_patterns = fb.get("negative_context_patterns") or fb.get("negative", [])
            if neg_patterns and any(p in self.full_text[-50:] for p in neg_patterns):
                print(f"  [抑制] {fb['alert']} (negative_context命中)", flush=True)
                continue
            alerts.append(("forbidden", fb["alert"], fb["id"]))
            self.last_triggered[fb["id"]] = now
            _log(f'[违规] {stage["name"]} - {fb["alert"]}')

        return alerts

    def _check_stage_enter(self, text):
        # 后退
        if self.current_stage_idx > 0:
            for i in range(self.current_stage_idx - 1, -1, -1):
                stage = self.stages[i]
                if any(kw in text for kw in stage.get("reenter_keywords", [])):
                    flush_alerts = self._flush_prev_stage()
                    self._enter_stage(i, "后退")
                    return ("stage_reenter", stage["name"], stage["id"], flush_alerts)

        # 前进（未进入任何阶段时只允许匹配第一个阶段）
        end = 1 if self.current_stage_idx < 0 else len(self.stages)
        for i in range(self.current_stage_idx + 1, end):
            stage = self.stages[i]
            if any(kw in text for kw in stage["enter_keywords"]):
                flush_alerts = self._flush_prev_stage()
                for j in range(self.current_stage_idx + 1, i):
                    skipped = self.stages[j]
                    descs = "、".join(m["desc"] for m in skipped["must"])
                    if descs:
                        _log(f'[跳过] {skipped["name"]} 未完成: {descs}')
                        flush_alerts.append(("skipped", f'{skipped["name"]} 未完成: {descs}', None))
                    else:
                        _log(f'[跳过] {skipped["name"]} - 未进入')
                self._enter_stage(i, "进入")
                return ("stage_enter", stage["name"], stage["id"], flush_alerts)
        return None

    def _enter_stage(self, idx, reason):
        stage = self.stages[idx]
        self.current_stage_idx = idx
        self.stage_enter_time = time.time()
        self.last_triggered.clear()
        done = self.must_done.get(idx, set())
        all_done = {m["id"] for m in stage["must"]} <= done
        self.must_all_done_time = time.time() if stage.get("hint_next") and done and all_done else 0
        _log(f'[{reason}] {stage["name"]}')

    def _flush_prev_stage(self):
        if self.current_stage_idx < 0:
            return []
        stage = self.stages[self.current_stage_idx]
        done = self.must_done.get(self.current_stage_idx, set())
        missed = [m for m in stage["must"] if m["id"] not in done]
        if not missed:
            return []
        descs = "、".join(m["desc"] for m in missed)
        _log(f'[跳过] {stage["name"]} 未完成: {descs}')
        return [("skipped", f'{stage["name"]} 未完成: {descs}', None)]

    def force_check_timeout(self):
        if self.current_stage_idx < 0:
            return []
        alerts = []
        stage = self.stages[self.current_stage_idx]
        idx = self.current_stage_idx
        done = self.must_done.get(idx, set())
        alerted = self.must_alerted.setdefault(idx, set())
        elapsed = time.time() - self.stage_enter_time
        for must in (m for m in stage["must"] if m["id"] not in done and m["id"] not in alerted and elapsed > m["timeout_sec"]):
            alerts.append(("must_timeout", must["alert"], must["id"]))
            alerted.add(must["id"])
            _log(f'[超时] {stage["name"]} - {must["desc"]}')
        # must全命中10s后提醒下一阶段
        if self.must_all_done_time > 0 and time.time() - self.must_all_done_time >= 10:
            nxt = self.current_stage_idx + 1
            if nxt < len(self.stages):
                next_name = self.stages[nxt]["name"]
                alerts.append(("hint_next", f"该{next_name}了", None))
            self.must_all_done_time = -1  # 只提醒一次
        return alerts
