"""The setup wizard writes a working .env and keeps what was already there."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import setup_wizard as wiz  # noqa: E402


def test_write_env_fills_the_template_and_keeps_old_settings(tmp_path, monkeypatch):
    example = tmp_path / ".env.example"
    example.write_text("# token\nTELEGRAM_BOT_TOKEN=\nLOCATION_ID=\n# km\nRADIUS_KM=10\nHEADLESS=true\n", encoding="utf-8")
    env = tmp_path / ".env"
    env.write_text("TELEGRAM_BOT_TOKEN=old\nHEADLESS=false\nMY_EXTRA=1\n", encoding="utf-8")
    monkeypatch.setattr(wiz, "ENV", env)
    monkeypatch.setattr(wiz, "EXAMPLE", example)
    values = dict(wiz.read_env(env))
    values.update(TELEGRAM_BOT_TOKEN="123:new", LOCATION_ID="london")
    wiz.write_env(values)
    out = wiz.read_env(env)
    assert out == {"TELEGRAM_BOT_TOKEN": "123:new", "LOCATION_ID": "london", "RADIUS_KM": "10",
                   "HEADLESS": "false", "MY_EXTRA": "1"}
    assert "# token" in env.read_text(encoding="utf-8")          # comments survive


def test_location_accepts_a_pasted_address(monkeypatch):
    answers = iter(["https://www.facebook.com/marketplace/Pretoria/search?query=x", ""])
    monkeypatch.setattr("builtins.input", lambda _: next(answers))
    assert wiz.get_location("") == "pretoria"
    answers2 = iter(["two words!!", "new york"])
    monkeypatch.setattr("builtins.input", lambda _: next(answers2))
    assert wiz.get_location("") == "newyork"


def test_numbers_are_checked(monkeypatch):
    answers = iter(["abc", "9999", "", ])
    monkeypatch.setattr("builtins.input", lambda _: next(answers))
    assert wiz.get_number("km", "10", 1, 500) == "10"
