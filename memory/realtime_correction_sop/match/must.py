from __future__ import annotations

import time

from .events import Event
from . import session_log
from .rules_util import audio_id, iter_must_rules
from .text_match import any_kw_in, match_window

PRODUCT_WORDS = ["配置", "亮点", "卖点", "外观", "内饰", "续航", "动力", "参数", "这款车"]


def _arm(session, rule: dict):
    st = session.rule(rule["id"])
    if st.status in ("armed", "done", "alerted"):
        return
    st.status = "armed"
    st.armed_at = time.time()
    session_log.write(f'[激活] {rule.get("desc", rule["id"])}')


def arm_after_stage_enter(session, rules: dict, stage_idx: int):
    if stage_idx < 0:
        return
    stage = rules["stages"][stage_idx]
    if stage["id"] == "adjust_seat":
        session.drive_active_since = time.time()
    for must in stage.get("must", []):
        act = must.get("activate", {"mode": "after_stage_enter"})
        mode = act.get("mode", "after_stage_enter")
        if mode in ("after_stage_enter", "after_drive_enter"):
            _arm(session, must)


def apply_arm_triggers(session, rules: dict, text: str | None):
    if not text:
        return
    idx = session.stage_idx
    stages = rules["stages"]

    if idx >= 0 and any_kw_in(text, PRODUCT_WORDS):
        if session.product_pitch_since == 0:
            session.product_pitch_since = time.time()

    if idx >= 0 and stages[idx]["id"] in ("adjust_seat", "auto_park"):
        if session.drive_active_since == 0:
            session.drive_active_since = time.time()

    for stage, must in iter_must_rules(rules):
        if session.rule(must["id"]).status != "idle":
            continue
        act = must.get("activate", {"mode": "after_stage_enter"})
        mode = act.get("mode", "after_stage_enter")

        if mode == "on_trigger":
            triggers = must.get("trigger") or act.get("keywords", [])
            if any_kw_in(text, triggers):
                _arm(session, must)
                if must["id"] == "invite_test_drive":
                    session.invite_rejected = True

        elif mode == "on_scene_enter":
            keys = act.get("keywords") or must.get("trigger", [])
            if any_kw_in(text, keys):
                _arm(session, must)

        elif mode == "on_behavior":
            if session.product_pitch_since > 0:
                elapsed = time.time() - session.product_pitch_since
                if elapsed >= act.get("min_sec", 2):
                    _arm(session, must)

        elif mode == "after_drive_enter":
            if session.drive_active_since > 0 and session.stage_idx >= 0:
                if stages[session.stage_idx]["id"] == "adjust_seat":
                    _arm(session, must)

        elif mode == "on_end_view":
            if any_kw_in(text, act.get("keywords", ["去坐坐", "算个价格", "先回去", "回去考虑"])):
                if not session.invite_done:
                    _arm(session, must)

        elif mode == "on_leave_intent":
            if any_kw_in(text, act.get("keywords", ["先走了", "下次再来", "先回去", "慢走", "再见"])):
                _arm(session, must)

        elif mode == "on_second_invite":
            if session.invite_rejected and not session.invite_done:
                _arm(session, must)


def apply_evidence(session, rules: dict, text: str | None) -> list[Event]:
    events = []
    if not text:
        return events
    idx = session.stage_idx
    stage_name = rules["stages"][idx]["name"] if idx >= 0 else None

    for stage, must in iter_must_rules(rules):
        rid = must["id"]
        st = session.rule(rid)
        if st.status not in ("armed", "alerted"):
            continue
        keywords = must.get("keywords", [])
        if not any_kw_in(text, keywords) and not match_window(session.full_text, keywords, 120):
            continue
        st.status = "done"
        session_log.write(f'[命中] {must.get("desc", rid)}')
        if rid == "invite_test_drive":
            session.invite_done = True
        events.append(
            Event(
                "must_ok",
                must.get("alert", rid),
                rule_id=rid,
                audio_id="ding",
                stage=stage_name or must.get("scene"),
            )
        )
    return events


def check_timeouts(session, rules: dict) -> list[Event]:
    events = []
    now = time.time()
    idx = session.stage_idx
    stage_name = rules["stages"][idx]["name"] if idx >= 0 else None

    for stage, must in iter_must_rules(rules):
        rid = must["id"]
        st = session.rule(rid)
        if st.status != "armed":
            continue
        if st.armed_at <= 0:
            continue
        timeout = must.get("timeout_sec", 3)
        if now - st.armed_at <= timeout:
            continue
        st.status = "alerted"
        if rid == "invite_test_drive":
            session.invite_rejected = True
        session_log.write(f'[超时] {must.get("desc", rid)}')
        events.append(
            Event(
                "must_timeout",
                must.get("alert", rid),
                rule_id=rid,
                audio_id=audio_id(must),
                stage=stage_name or must.get("scene"),
            )
        )

    for stage, must in iter_must_rules(rules):
        rid = must["id"]
        act = must.get("activate", {})
        if act.get("mode") != "on_silence":
            continue
        st = session.rule(rid)
        if st.status != "armed":
            continue
        if session.silence_seconds() < act.get("silence_sec", 4):
            continue
        st.status = "alerted"
        session_log.write(f'[超时] {must.get("desc", rid)} (静默)')
        events.append(
            Event(
                "must_timeout",
                must.get("alert", rid),
                rule_id=rid,
                audio_id=audio_id(must),
                stage=stage_name,
                meta={"reason": "silence"},
            )
        )
    return events
