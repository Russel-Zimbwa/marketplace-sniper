import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "") or default)
    except ValueError:
        return default


@dataclass(frozen=True)
class Config:
    telegram_token: str = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    telegram_chat_id: str = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    anthropic_key: str = os.getenv("ANTHROPIC_API_KEY", "").strip()
    claude_model: str = os.getenv("CLAUDE_MODEL", "").strip() or "claude-opus-5-5"
    radius_km: int = _int("RADIUS_KM", 10)
    min_interval: int = _int("MIN_INTERVAL", 60)
    max_interval: int = _int("MAX_INTERVAL", 120)
    deal_threshold: int = _int("DEAL_THRESHOLD", 7)
    headless: bool = os.getenv("HEADLESS", "true").strip().lower() not in ("0", "false", "no")
    location_id: str = os.getenv("LOCATION_ID", "").strip()
    judge_mode: str = os.getenv("JUDGE", "").strip().lower() or "rules"  # rules | off
    ollama_url: str = (os.getenv("OLLAMA_URL", "").strip() or "http://localhost:11434").rstrip("/")
    ollama_model: str = os.getenv("OLLAMA_MODEL", "").strip()  # blank = auto-pick; "off" = never use
    ollama_timeout: int = _int("OLLAMA_TIMEOUT", 180)  # a photo check takes ~50 s on a laptop CPU
    far_radius_km: int = _int("FAR_RADIUS_KM", 60)   # wider ring for exceptional deals only; 0 = off
    far_min_score: int = _int("FAR_MIN_SCORE", 9)    # how good a far-away deal must be to be sent
    fresh_minutes: int = _int("FRESH_MINUTES", 120)  # only alert on listings posted this recently
    rescan_minutes: int = _int("RESCAN_MINUTES", 30)
    lock_port: int = _int("LOCK_PORT", 47615)
    browser_channel: str = os.getenv("BROWSER_CHANNEL", "").strip()  # chromium | chrome | msedge; blank = auto
    profile_dir: Path = ROOT / "fb_profile"
    db_path: Path = ROOT / "mpwatch.db"
    log_dir: Path = ROOT / "logs"


config = Config()
