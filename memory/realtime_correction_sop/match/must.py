from __future__ import annotations

import time

from .events import Event
from . import session_log
from .rules_util import audio_id, iter_must_rules
from .text_match import any_kw_in, match_window

PRODUCT_WORDS = ["配置", "亮点", "卖点", "外观", "内饰", "续航", "动力", "参数", "这款车"]
SWAP_HAND_TRIGGERS = ["您来开", "换您开", "你来试试", "换手", "找个地方停", "换您试试", "换你开", "我来开", "让我开"]


def _arm_drive_phase_musts(session, rules: dict):
    stages = rules["stages"]
    if session.stage_idx < 0:
        return
    if stages[session.stage_idx]["id"] not in ("drive_route", "adjust_seat"):
        return
    if session.drive_active_since == 0:
        session.drive_active_since = time.time()
    drive_stage = next(s for s in stages if s["id"] == "adjust_seat")
    for must in drive_stage.get("must", []):
        if must.get("activate", {}).get("mode") == "after_drive_enter":
            _arm(session, must)


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
            if not any_kw_in(text, keys):
                continue
            # 场景词只在 must 所属阶段生效，避免迎接阶段「到了」等误 arm 试驾后规则
            if stage is not None:
                if session.stage_idx < 0:
                    continue
                if rules["stages"][session.stage_idx]["id"] != stage["id"]:
                    continue
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

    if text and any_kw_in(text, SWAP_HAND_TRIGGERS):
        _arm_drive_phase_musts(session, rules)


def apply_evidence(
    session,
    rules: dict,
    text: str | None,
    *,
    skip_after_enter_stage_idx: int | None = None,
) -> list[Event]:
    events = []
    if not text:
        return events
    idx = session.stage_idx
    stage_name = rules["stages"][idx]["name"] if idx >= 0 else None
    skip_stage_id = (
        rules["stages"][skip_after_enter_stage_idx]["id"]
        if skip_after_enter_stage_idx is not None
        else None
    )

    for stage, must in iter_must_rules(rules):
        rid = must["id"]
        st = session.rule(rid)
        if st.status not in ("armed", "alerted"):
            continue
        if skip_stage_id and stage is not None and stage.get("id") == skip_stage_id:
            mode = must.get("activate", {}).get("mode", "after_stage_enter")
            if mode == "after_stage_enter":
                continue
        keywords = must.get("keywords", [])
        # 留资阶段进入词与完成词相同，滑窗会把上一句开口词误算完成
        use_window = stage is None or stage.get("id") != "get_contact"
        if not any_kw_in(text, keywords) and not (
            use_window and match_window(session.full_text, keywords, 120)
        ):
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
