from __future__ import annotations

from .events import Event
from .session import Session
from . import stage as stage_mod
from . import must as must_mod
from . import forbidden as forbidden_mod


def tick(text_or_none: str | None, session: Session, rules: dict) -> list[Event]:
    text = text_or_none if text_or_none else None
    if text:
        session.append_text(text)

    events: list[Event] = []
    stage_events = stage_mod.apply_tick(session, rules, text)
    events.extend(stage_events)

    entered_idx = None
    for ev in stage_events:
        if ev.type == "stage_enter":
            entered_idx = ev.meta.get("stage_idx")
    if entered_idx is not None:
        must_mod.arm_after_stage_enter(session, rules, entered_idx)

    must_mod.apply_arm_triggers(session, rules, text)
    events.extend(must_mod.apply_evidence(session, rules, text))
    events.extend(forbidden_mod.apply_tick(session, rules, text))

    extra: list[Event] = []
    extra.extend(must_mod.check_timeouts(session, rules))
    hint = stage_mod.check_hint_next(session, rules)
    if hint:
        extra.append(hint)

    out = events + extra
    stage_mod.update_must_all_done(session, rules)

    last = session.stage_idx
    if last == len(rules["stages"]) - 1 and last >= 0:
        must_ids = {m["id"] for m in rules["stages"][last].get("must", [])}
        if must_ids and all(session.rule(mid).status == "done" for mid in must_ids):
            if not session.flags.get("session_end"):
                session.flags["session_end"] = True
                out.append(Event("session_end", "会话结束", stage=rules["stages"][last]["name"]))

    return out
