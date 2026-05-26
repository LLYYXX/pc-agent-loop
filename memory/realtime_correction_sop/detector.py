"""薄 facade：Session + match.engine.tick"""
from match import session_log
from match.engine import tick
from match.session import Session
from sop_rules import SOP_RULES


class StageDetector:
    def __init__(self):
        self.stages = SOP_RULES["stages"]
        self._session = Session()

    def reset(self):
        session_log.rotate()
        self._session.reset()
        print("\n[新会话开始]", flush=True)

    def tick(self, new_text=None):
        return tick(new_text, self._session, SOP_RULES)

    def force_check_timeout(self):
        return self.tick(None)

    def stage_name(self):
        return self._session.stage_name(self.stages)
