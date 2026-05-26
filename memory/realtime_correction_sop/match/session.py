import time
from dataclasses import dataclass, field


@dataclass
class RuleState:
    status: str = "idle"  # idle | armed | alerted(已提醒,可补救) | done
    armed_at: float = 0.0


@dataclass
class Session:
    stage_idx: int = -1
    stage_enter_at: float = 0.0
    full_text: str = ""
    last_text_at: float = 0.0
    text_ticks: int = 0
    rule_states: dict = field(default_factory=dict)
    flags: dict = field(default_factory=dict)
    must_all_done_at: float = 0.0
    hint_next_fired: bool = False
    last_forbidden: dict = field(default_factory=dict)
    invite_done: bool = False
    invite_rejected: bool = False
    product_pitch_since: float = 0.0
    drive_active_since: float = 0.0

    def rule(self, rule_id: str) -> RuleState:
        return self.rule_states.setdefault(rule_id, RuleState())

    def stage_name(self, stages: list) -> str:
        if self.stage_idx < 0:
            return "等待开始"
        return stages[self.stage_idx]["name"]

    def append_text(self, text: str):
        self.full_text += text
        if text.strip():
            self.text_ticks += 1
            self.last_text_at = time.time()

    def silence_seconds(self) -> float:
        if self.last_text_at == 0:
            return 0.0
        return time.time() - self.last_text_at

    def reset(self):
        self.stage_idx = -1
        self.stage_enter_at = 0.0
        self.full_text = ""
        self.last_text_at = 0.0
        self.text_ticks = 0
        self.rule_states.clear()
        self.flags.clear()
        self.must_all_done_at = 0.0
        self.hint_next_fired = False
        self.last_forbidden.clear()
        self.invite_done = False
        self.invite_rejected = False
        self.product_pitch_since = 0.0
        self.drive_active_since = 0.0
