"""Offline tests for the free judge. Run: .venv\\Scripts\\python -m pytest tests -q"""
import asyncio
import datetime
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mpwatch import judge, judge_rules as rules  # noqa: E402
from mpwatch.parser import Listing  # noqa: E402

TODAY = datetime.date(2026, 11, 12)


@pytest.fixture(autouse=True)
def no_ollama(monkeypatch):
    async def none():
        return None
    monkeypatch.setattr(judge, "_ollama_model", none)


def L(title, price, desc="", price_text=None):
    return Listing(id="1", title=title, price=price, description=desc,
                   price_text=price_text or (f"R{price:,.0f}" if price is not None else "?"))


def run(title, price, desc="", query="xbox series x", max_price=9000, typical=7500):
    listing = L(title, price, desc)
    v = asyncio.run(judge.judge(listing, query, max_price, typical))
    assert v is not None
    return v, judge.is_steal(listing, v)


def has(v, word):
    return any(word in f for f in v.red_flags)


# ---- the 12 required cases (query "xbox series x", max 9000, typical 7500) ----

def test_01_normal_price():
    v, steal = run("Xbox Series X", 8500, "Condition\nUsed - like new\nPretoria")
    assert v.is_target_item and v.deal_score in (4, 5) and not steal


def test_02_series_s_price_bait():
    v, steal = run("XBox series S and accessories", 6500, "NOT R6500 FOR EVERYTHING ASK PRICE ON WHAT YOU WANT!!")
    assert not v.is_target_item and has(v, "price bait") and not steal


def test_03_controller():
    v, steal = run("XBOX Controller for Series S and X", 850)
    assert not v.is_target_item and not steal


def test_04_real_steal():
    v, steal = run("Xbox Series X 1TB", 3000, "working, 2 controllers")
    assert v.is_target_item and v.deal_score >= 9 and steal
    assert "60%" in v.summary and "working" in v.condition


def test_05_odd_title():
    v, _ = run("Microsoft console XSX disc edition", 4500)
    assert v.is_target_item and v.deal_score >= 7


def test_06_swap():
    v, steal = run("Xbox series X to swap for decent gaming pc", 10000)
    assert has(v, "swap") and v.deal_score <= 3 and not steal


def test_07_for_parts():
    v, steal = run("Xbox Series X for parts, no display", 2000)
    assert "faulty" in v.condition and v.deal_score <= 2 and not steal


def test_08_wanted():
    v, steal = run("WTB Xbox Series X", 6000)
    assert not v.is_target_item and not steal


def test_09_case_and_poster():
    v, steal = run("GTA VI Xbox series X/S Case + Poster", 250)
    assert not v.is_target_item and not steal


def test_10_emigrating_steal():
    v, steal = run("Xbox Series X", 1500, "emigrating, must go today\nJoined Facebook in 2024")
    assert v.is_target_item and v.deal_score >= 9 and steal and has(v, "verify in person")


def test_11_one_x():
    v, _ = run("Xbox One X 1TB", 5000)
    assert not v.is_target_item


def test_12_placeholder():
    v, steal = run("Xbox Series X", 1, "inbox for price")
    assert v.is_target_item and v.deal_score == 5 and has(v, "placeholder") and not steal


# ---- other queries: the judge must work for anything the user types ----

@pytest.mark.parametrize("query,title,expected", [
    ("ps5", "PlayStation 5 disc edition", True),
    ("ps5", "PS5 controllers", False),
    ("ps5", "Ps4 slim 1tb", False),
    ("ps5 digital", "PS5 Digital Edition 825GB", True),
    ("ps5 digital", "PS5 disc edition", False),
    ("ps5 digital", "Sony PS5", True),                      # edition not stated: lean yes
    ("ps5 controller", "PS5 controller white", True),      # the accessory IS the item
    ("iphone 13 pro", "iPhone 13 Pro 128gb", True),
    ("iphone 13 pro", "iphone 13pro 256", True),
    ("iphone 13 pro", "iPhone 13 Pro Max 256GB", False),
    ("iphone 13 pro max", "iPhone 13 Pro", False),
    ("iphone 13", "iPhone 13 Black", True),
    ("iphone 13", "iPhone 13 mini", False),
    ("iphone 13", "iPhone 14", False),
    ("iphone 13", "iPhone 13 case", False),
    ("iphone 13", "iPhone 13 with case and charger", True),
    ("iphone 13 256gb", "iPhone 13 128GB", False),
    ("road bike", "Merida Scultura 905 Carbon Road Bike", True),
    ("road bike", "Alpinestars motorbike boots US9", False),
    ("road bike", "Road bike helmet", False),
    ("xbox series x", "Xbox Series X with 2 controllers and 3 games", True),
    ("xbox series x", "Xbox Series X 1TB 2 controllers", True),
    ("xbox series x", "2 x Xbox Series Controllers - LIKE NEW!!", False),
    ("xbox series x", "Looking for Xbox Series X", False),
    ("xbox series x", "Xbox Series X empty box", False),
    ("nintendo switch", "Nintendo Switch Lite", False),
    ("nintendo switch", "Nintendo Switch OLED", False),
    ("nintendo switch", "Nintendo Switch in box", True),
])
def test_target_matching(query, title, expected):
    assert rules.classify_target(title, "", query).is_target is expected, title


def test_description_rescues_vague_title():
    v, _ = run("Microsoft console 1TB", 4000, "Xbox Series X in great condition, comes with controller")
    assert v.is_target_item and v.deal_score >= 7


def test_game_for_the_console_is_not_the_console():
    v, steal = run("FIFA 23 Xbox Series X", 300)
    assert not v.is_target_item and not steal


def test_negation_is_not_a_problem():
    v, steal = run("Xbox Series X", 3500, "No scratches, never broken, nothing wrong with it. Not for swap, no trades. "
                                         "No deposit needed, collect in Pretoria.")
    assert v.is_target_item and v.red_flags == ["far below the usual price - verify in person before paying"]
    assert v.deal_score >= 9 and steal


def test_real_problems_cap_the_score():
    assert run("Xbox Series X", 3000, "screen output is faulty")[0].deal_score <= 2
    assert run("Xbox Series X", 3000, "R500 deposit to hold it")[0].deal_score <= 4
    assert run("Xbox Series X", 3000, "courier only")[0].deal_score <= 4
    v, steal = run("Xbox Series X", 3000, "Pudo at buyers expense")
    assert v.deal_score >= 9 and steal and has(v, "courier")      # a flag, not a reject


def test_swap_or_cash_is_only_a_flag():
    v, _ = run("Xbox Series X", 3000, "open to swaps or cash")
    assert v.deal_score >= 9 and has(v, "open to swaps")


def test_parts_query_wants_broken_items():
    v, _ = run("Xbox Series X faulty for parts", 1000, query="xbox series x faulty", typical=2000)
    assert v.is_target_item and v.deal_score >= 9


def test_no_price_history_still_alerts():
    v, steal = run("Xbox Series X", 6000, typical=None)
    assert v.is_target_item and v.deal_score == 7 and v.estimated_fair_price is None and not steal


def test_new_account_flag():
    r = rules.judge_listing("Xbox Series X", "Joined Facebook in 2026", 7000, "R7,000", "xbox series x",
                            typical=7500, today=TODAY)
    assert any("new account" in f for f in r.red_flags)


@pytest.mark.parametrize("price,expected", [(0, True), (1, True), (123, True), (1234, True), (999999, True),
                                             (3000, False), (9999, False), (None, False)])
def test_placeholder_prices(price, expected):
    assert rules.placeholder_price(price) is expected


def test_never_raises_on_odd_input():
    for listing in (L("", None), L("🔥🔥🔥", 0, "\n\n"), L("x" * 5000, 1e12, "y" * 20000)):
        assert asyncio.run(judge.judge(listing, "xbox series x", None, None)) is not None
    assert asyncio.run(judge.judge(L("Xbox Series X", 5000), "", None, 7500)) is not None


# ---- Layer 2 merge policy: the local model may help, never hide ----

def _rules(title, price, query="xbox series x"):
    return rules.judge_listing(title, "", price, f"R{price:,}", query, typical=7500)


def test_model_can_rescue_an_unsure_listing():
    listing = L("Xbox Series console black", 3000)
    res = _rules(listing.title, 3000)
    assert not res.is_target and not res.target_certain
    v = asyncio.run(judge.judge(listing, "xbox series x", None, 7500))
    assert not v.is_target_item and v.unsure                       # rejected, but worth a second look
    out = judge.rescue(listing, "xbox series x", None, 7500, {"is_item": True, "photo": "real"})
    assert out.is_target_item and out.deal_score == 8   # capped: never presented as a sure steal
    assert judge.rescue(listing, "xbox series x", None, 7500, {"is_item": False}) is None
    assert judge.rescue(listing, "xbox series x", None, 7500, None) is None


def test_model_cannot_overturn_a_certain_no():
    yes = {"is_item": True, "photo": "real"}
    for title in ("WTB Xbox Series X", "Xbox Series S", "Xbox One X 1TB", "XBOX Controller for Series S and X"):
        listing = L(title, 3000)
        v = asyncio.run(judge.judge(listing, "xbox series x", None, 7500))
        assert not v.is_target_item and not v.unsure, title        # sure it's not the item: never asked
        assert judge.rescue(listing, "xbox series x", None, 7500, yes) is None, title


def test_unsure_only_when_it_would_be_a_deal():
    assert not asyncio.run(judge.judge(L("Microsoft console 1TB", 9000), "xbox series x", None, 7500)).unsure
    assert asyncio.run(judge.judge(L("Giant TCR", 2000), "road bike", None, 6000)).is_target_item is False


# ---- typing just the item, typos included ----

XBOX_TITLES = ["Xbox Series S", "Xbox series s 512gb", "XBOX SERIES S console", "Xbox Series S with controller",
               "Xbox series X", "Xbox Series S 1TB", "Series S xbox", "Xbox One S"]


@pytest.mark.parametrize("typed,expected", [
    ("xbox seires s", "xbox series s"),
    ("xbx series s", "xbox series s"),
    ("xbox series s", "xbox series s"),
    ("Xbox Series S", "Xbox Series S"),
    ("ps5", "ps5"),                       # nothing similar in the listings: left alone
])
def test_typos_are_fixed_from_listing_words(typed, expected):
    assert rules.correct_query(typed, XBOX_TITLES) == expected


def test_no_listings_no_correction():
    assert rules.correct_query("xbox seires s", []) == "xbox seires s"


def test_swap_ads_do_not_teach_prices():
    assert rules.swap_listing("Xbox series X to swap for decent gaming pc")
    assert not rules.swap_listing("Xbox Series X, no swaps")


def test_price_stats_and_price_drop_memory(tmp_path):
    from mpwatch import pricing
    from mpwatch.db import DB
    db = DB(tmp_path / "t.db")
    wid = db.add_watch("xbox series s", None)
    titles = [("Xbox Series S", 3500), ("Xbox series s 512gb", 3800), ("Xbox Series S", 3200), ("Xbox Series S", 4000),
              ("Xbox Series S controller", 600), ("Xbox Series X", 7500), ("Xbox series s to swap for ps5", 10000),
              ("Xbox Series S", 1), ("Xbox Series S", 40000)]
    ls = [Listing(id=str(i), title=t, price=p, price_text=f"R{p}") for i, (t, p) in enumerate(titles)]
    pricing.record(db, wid, ls, "xbox series s")
    assert pricing.typical(db, wid) == 3650       # only the four real, sanely priced consoles count
    db.mark_seen(wid, "0", 3500)
    assert db.is_seen(wid, "0") and db.seen_price(wid, "0") == 3500
    db.set_seen_price(wid, "0", 2500)
    assert db.seen_price(wid, "0") == 2500
    db.rename_watch(wid, "xbox series s 1tb")
    assert db.get_watch(wid).query == "xbox series s 1tb"


def test_plain_text_is_a_watch():
    from mpwatch.bot import parse_watch
    assert parse_watch("xbox series s") == ("xbox series s", None)
    assert parse_watch("iphone 13 under 5000") == ("iphone 13", 5000.0)
