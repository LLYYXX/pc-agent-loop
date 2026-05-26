from datetime import datetime
from pathlib import Path

LOG_DIR = Path(__file__).resolve().parents[3] / "temp" / "realtime_correction_sop" / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
_log_path = LOG_DIR / f"{datetime.now():%Y%m%d_%H%M%S}.txt"


def rotate():
    global _log_path
    _log_path = LOG_DIR / f"{datetime.now():%Y%m%d_%H%M%S}.txt"


def write(msg: str):
    with _log_path.open("a", encoding="utf-8") as f:
        f.write(f"{datetime.now():%H:%M:%S} {msg}\n")
