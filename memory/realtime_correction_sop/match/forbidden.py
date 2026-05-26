from __future__ import annotations

import time

from .events import Event
from . import session_log
from .rules_util import audio_id
from .text_match import any_kw_in, match_window


def apply_tick(session, rules: dict, text: str | None) -> list[Event]:
    if not text:
        return []
    events = []
    now = time.time()
    idx = session.stage_idx
    stage_name = rules["stages"][idx]["name"] if idx >= 0 else None

    for stage in rules["stages"]:
        for fb in stage.get("forbidden", []):
            if not any_kw_in(text, fb.get("keywords", [])):
                continue
            fid = fb["id"]
            if now - session.last_forbidden.get(fid, 0) < fb.get("cooldown_sec", 10):
                continue
            neg = fb.get("negative_context_patterns") or fb.get("negative", [])
            if neg and match_window(session.full_text, neg, 80):
                session_log.write(f'[抑制] {fb.get("alert", fid)}')
                continue
            session.last_forbidden[fid] = now
            session_log.write(f'[违规] {stage["name"]} - {fb.get("alert", fid)}')
            events.append(
                Event(
                    "forbidden",
                    fb.get("alert", fid),
                    rule_id=fid,
                    audio_id=audio_id(fb),
                    stage=stage_name,
                )
            )
    return events
