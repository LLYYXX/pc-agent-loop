from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Event:
    type: str
    message: str = ""
    rule_id: str | None = None
    audio_id: str | None = None
    stage: str | None = None
    meta: dict = field(default_factory=dict)

    def is_audio_alert(self) -> bool:
        return self.type in ("must_timeout", "forbidden", "hint_next") and bool(self.audio_id)
