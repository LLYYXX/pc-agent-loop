from __future__ import annotations

import time

from .events import Event
from . import session_log
from .text_match import any_kw_in


def _enter_stage(session, stages, idx: int, reason: str):
    stage = stages[idx]
    session.stage_idx = idx
    session.stage_enter_at = time.time()
    session.last_forbidden.clear()
    session.must_all_done_at = 0.0
    session.hint_next_fired = False
    session_log.write(f"[{reason}] {stage['name']}")


def apply_tick(session, rules: dict, text: str | None) -> list[Event]:
    if text is None:
        return []
    stages = rules["stages"]
    events = []
    idx = session.stage_idx

    if idx > 0:
        for i in range(idx - 1, -1, -1):
            stage = stages[i]
            if any_kw_in(text, stage.get("reenter_keywords", [])):
                skipped = _flush_skipped(session, stages)
                _enter_stage(session, stages, i, "后退")
                events.append(Event("stage_reenter", stage["name"], stage=stage["name"], meta={"stage_id": stage["id"]}))
                events.extend(skipped)
                return events

    target = _find_forward_stage(session, stages, text)
    if target is None:
        return events

    if target != idx:
        skipped = _flush_skipped(session, stages) if idx >= 0 else []
        for j in range(max(idx, -1) + 1, target):
            skipped_stage = stages[j]
            missed = [m["desc"] for m in skipped_stage.get("must", []) if session.rule(m["id"]).status != "done"]
            if missed:
                msg = f'{skipped_stage["name"]} 未完成: {"、".join(missed)}'
                session_log.write(f"[跳过] {msg}")
                skipped.append(Event("skipped", msg, stage=skipped_stage["name"], meta={"merged": True}))
            else:
                session_log.write(f'[跳过] {skipped_stage["name"]} - 未进入')
        _enter_stage(session, stages, target, "进入")
        stage = stages[target]
        events.append(Event("stage_enter", stage["name"], stage=stage["name"], meta={"stage_id": stage["id"], "stage_idx": target}))
        events.extend(skipped)

    return events


def _find_forward_stage(session, stages, text: str) -> int | None:
    idx = session.stage_idx
    if idx < 0:
        if any_kw_in(text, stages[0].get("enter_keywords", []) + stages[0].get("enter_strong", [])):
            return 0
        return None

    strong_hit = None
    for i in range(idx + 1, len(stages)):
        st = stages[i]
        if any_kw_in(text, st.get("enter_strong", [])):
            strong_hit = i
    if strong_hit is not None:
        return strong_hit

    nxt = idx + 1
    if nxt < len(stages) and any_kw_in(text, stages[nxt].get("enter_keywords", [])):
        return nxt
    return None


def _flush_skipped(session, stages) -> list[Event]:
    idx = session.stage_idx
    if idx < 0:
        return []
    stage = stages[idx]
    missed = [m for m in stage.get("must", []) if session.rule(m["id"]).status != "done"]
    if not missed:
        return []
    descs = "、".join(m["desc"] for m in missed)
    session_log.write(f"[跳过] {stage['name']} 未完成: {descs}")
    return [Event("skipped", f'{stage["name"]} 未完成: {descs}', stage=stage["name"])]


def check_hint_next(session, rules: dict) -> Event | None:
    idx = session.stage_idx
    if idx < 0:
        return None
    stage = rules["stages"][idx]
    if not stage.get("hint_next") or session.hint_next_fired:
        return None
    must_ids = {m["id"] for m in stage.get("must", [])}
    if must_ids and not all(session.rule(mid).status == "done" for mid in must_ids):
        return None
    if session.must_all_done_at <= 0:
        return None
    if time.time() - session.must_all_done_at < 10:
        return None
    nxt = idx + 1
    if nxt >= len(rules["stages"]):
        return None
    session.hint_next_fired = True
    next_stage = rules["stages"][nxt]
    audio_id = stage.get("hint_next_audio_id", f"hint_next_{next_stage['id']}")
    return Event(
        "hint_next",
        f"该{next_stage['name']}了",
        audio_id=audio_id,
        stage=stage["name"],
    )


def update_must_all_done(session, rules: dict):
    idx = session.stage_idx
    if idx < 0:
        return
    stage = rules["stages"][idx]
    must_ids = {m["id"] for m in stage.get("must", [])}
    if not must_ids:
        return
    if all(session.rule(mid).status == "done" for mid in must_ids):
        if session.must_all_done_at == 0:
            session.must_all_done_at = time.time()
