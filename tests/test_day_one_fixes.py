"""Problems found in the first full day of real use (13 Nov 2026) and the fixes for them."""
import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mpwatch import judge, judge_rules as rules, pricing  # noqa: E402
from mpwatch.bot import CHATTER, SCAM_WORDS, alert_text  # noqa: E402
from mpwatch.db import DB  # noqa: E402
from mpwatch.main import Watcher  # noqa: E402
from mpwatch.parser import Listing  # noqa: E402
from types import SimpleNamespace  # noqa: E402

YES = {"is_item": True, "photo": "real", "condition": "", "reason": "Shows it."}


def L(title, price, desc=""):
    return Listing(id="1", title=title, price=price, price_text=f"R{price:,}", description=desc)


def verdict(title, price, query, typical, desc=""):
    return asyncio.run(judge.judge(L(title, price, desc), query, None, typical))


# ---- things that were alerted and should not have been ----

@pytest.mark.parametrize("title,query", [
    ("Ps5 PlayStation Joystick Controller", "xbox series x"),     # was sent as a 10/10 Xbox steal
    ("PlayStation Joystick series", "xbox series x"),
    ("Fun Foods Machine Hire", "slushie machine"),                # hire, not for sale
    ("Slush machines for hire", "slushie machine"),
    ("Party machine for HIRE", "slushie machine"),
    ("Popcorn Machine to hire Pretoria East", "slushie machine"),
])
def test_never_alerted_even_if_the_photo_model_says_yes(title, query):
    v = verdict(title, 2000, query, 5250)
    assert not v.is_target_item and not v.unsure
    assert judge.rescue(L(title, 2000), query, None, 5250, YES) is None


# ---- things that needed the photo model and no longer do ----

@pytest.mark.parametrize("title,query", [
    ("XboxseriesS", "xbox series s"),              # typed without spaces
    ("slushy machine", "slushie machine"),         # spelled differently
    ("Slushies machine", "slushie machine"),
    ("X Box x seried", "xbox series x"),
    ("xbox seriesx 1tb", "xbox series x"),
])
def test_recognised_by_the_rules(title, query):
    assert rules.classify_target(title, "", query).is_target, title


@pytest.mark.parametrize("title,query", [
    ("XboxseriesS", "xbox series x"),              # glued, but the other model
    ("Serious gamer xbox x", "xbox series x"),     # 'serious' is not 'series'
    ("Slush puppy syrup", "slushie machine"),
])
def test_close_spelling_is_not_a_free_pass(title, query):
    assert not rules.classify_target(title, "", query).is_target, title


# ---- "STEAL 10/10" alerts that were scams ----

def test_too_good_to_be_true():
    v = verdict("Xbox series x", 1000, "xbox series x", 7500,
                "Brand new sealed. WhatsApp me 071 234 5678\nListed 2 weeks ago in Johannesburg\nJoined Facebook in 2026")
    assert v.is_target_item and v.likely_scam and not judge.is_steal(L("Xbox series x", 1000), v)
    assert "too good to be true" in v.summary and "14 days" in v.summary
    text = alert_text(SimpleNamespace(id=1, query="xbox series x"), L("Xbox series x", 1000), v)
    assert text.startswith("⚠️ <b>Too good to be true? · R1,000</b>") and "STEAL" not in text


@pytest.mark.parametrize("desc,scam", [
    ("Listed 3 hours ago in Pretoria\nJoined Facebook in 2015", False),             # fresh, old account: a real steal
    ("emigrating, must go today\nJoined Facebook in 2024", False),
    ("Listed 5 days ago in Pretoria\nJoined Facebook in 2015", True),               # a real steal doesn't last 5 days
    ("Listed 3 hours ago in Pretoria\nJoined Facebook in 2026\nwhatsapp only", True),
    ("Listed 3 hours ago\nJoined Facebook in 2026", False),                         # one doubt alone is not enough
])
def test_scam_signals(desc, scam):
    v = verdict("Xbox Series X", 3000, "xbox series x", 7500, desc)
    assert v.likely_scam is scam and v.deal_score >= 9
    assert judge.is_steal(L("Xbox Series X", 3000), v) is (not scam)


def test_ordinary_good_deals_are_never_called_scams():
    v = verdict("Xbox Series X", 5000, "xbox series x", 7500, "Listed 2 weeks ago\nJoined Facebook in 2026\nwhatsapp")
    assert not v.likely_scam and v.deal_score == 8


def test_age():
    assert rules.age_days("Listed 2 weeks ago in Pretoria") == 14
    assert rules.age_days("Listed a day ago") == 1 and rules.age_days("Listed 12 hours ago") == 0.5
    assert rules.age_days("nothing here") is None


# ---- the usual price kept sinking as cheap bait piled up ----

def test_usual_price_ignores_bait_prices(tmp_path):
    db = DB(tmp_path / "t.db")
    wid = db.add_watch("xbox series x", None)
    prices = [1000, 1800, 2600, 3400, 6000, 6500, 7000, 7500, 7500, 8000, 8500, 9000]
    pricing.record(db, wid, [Listing(id=str(i), title="Xbox Series X", price=p, price_text=f"R{p}")
                             for i, p in enumerate(prices)], "xbox series x")
    assert pricing.typical(db, wid) == 7500        # a plain median of these would say 6,750 and keep falling


# ---- chatting to the bot is not shopping ----

def test_chatter_and_scam_words():
    assert {"scam", "fake", "it's a scam"} <= SCAM_WORDS and {"ok", "thanks", "no"} <= CHATTER
    assert not (SCAM_WORDS & CHATTER)


# ---- a login wall is retried, never a permanent stop ----

def test_login_wall_backs_off_and_recovers(tmp_path):
    sent = []

    class Bot:
        async def send(self, text):
            sent.append(text)
    w = Watcher.__new__(Watcher)
    w.db, w.bot, w._wake = DB(tmp_path / "t.db"), Bot(), asyncio.Event()

    async def go():
        import time
        waits = []
        for _ in range(4):
            await w.handle_logged_out("https://www.facebook.com/login")
            waits.append(round((float(w.db.get("blocked_until")) - time.time()) / 60))
        assert waits == [5, 15, 30, 60] and w.db.get("paused") != "1"      # never pauses itself
        assert len(sent) == 1 and "login wall" in sent[0]                    # told once, on the third
        await w.facebook_ok()
        assert w.db.get("block_count") == "0" and float(w.db.get("blocked_until")) == 0
        assert len(sent) == 2 and "letting me in again" in sent[1]
        await w.handle_logged_out("x")
        await w.facebook_ok()
        assert len(sent) == 2                                                # a single blip stays silent
    asyncio.run(go())


@pytest.mark.parametrize("title,price,query,typical", [
    ("Xbox Series X Wwe 2k22", 300, "xbox series x", 6000),        # a game: was 'rescued' and sent as 8/10
    ("Xbox Series X controller", 800, "xbox series x", 6000),
    ("PS5 Fifa 2026", 600, "ps5", 8500),
])
def test_games_and_accessories_are_final(title, price, query, typical):
    v = verdict(title, price, query, typical)
    assert not v.is_target_item and not v.unsure
    assert judge.rescue(L(title, price), query, None, typical, YES) is None


def test_only_a_vague_title_gets_a_second_look():
    v = verdict("Xbox Series console", 3000, "xbox series x", 7500)
    assert not v.is_target_item and v.unsure


def test_very_cheap_plus_one_doubt_is_enough():
    # Seen live: a Series S at R1,800 (71% under) from a WhatsApp-only seller went out as a 10/10 steal
    v = verdict("Xbox Series S", 1800, "xbox series s", 6199, "whatsapp me\nListed 2 hours ago\nJoined Facebook in 2018")
    assert v.likely_scam
    v = verdict("Xbox Series X", 1500, "xbox series x", 7500, "emigrating, must go today\nJoined Facebook in 2018")
    assert not v.likely_scam and v.deal_score >= 9          # very cheap with nothing else wrong: still a steal
