"""Cases taken from real Pretoria listings that the judge first got wrong."""
import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mpwatch import judge, judge_rules as rules  # noqa: E402
from mpwatch.parser import Listing, listed_ago, pretty_price  # noqa: E402


@pytest.fixture(autouse=True)
def no_ollama(monkeypatch):
    async def none():
        return None
    monkeypatch.setattr(judge, "_ollama_model", none)


def run(title, price, query, typical, desc=""):
    listing = Listing(id="1", title=title, price=price, price_text=f"R{price:,}", description=desc)
    v = asyncio.run(judge.judge(listing, query, None, typical))
    return v, judge.is_steal(listing, v)


def test_locked_phones_are_not_steals():
    for title in ("Locked iphone 13", "iPhone 13 locked", "iPhone 13 icloud locked"):
        v, steal = run(title, 1500, "iphone 13", 4000)
        assert v.deal_score <= 2 and not steal, title
    v, steal = run("iPhone 13 unlocked, never locked to a network", 1800, "iphone 13", 4000)
    assert v.deal_score >= 9 and steal


def test_game_or_side_product_named_after_the_console():
    assert not run("PS5 Fifa 2026", 600, "ps5", 8500)[0].is_target_item
    assert not run("PS5 portal", 3200, "ps5", 8500)[0].is_target_item
    assert not run("PS5 DualSense Wireless Controller - Red/Black", 599, "ps5", 8500)[0].is_target_item
    assert run("Ps5 disc version, 2 remotes, ps5 bag, 3 games", 9800, "ps5", 8500)[0].is_target_item
    assert run("Ps5 for sale brand new", 4500, "ps5", 8500)[0].deal_score >= 9
    assert run("Ps5", 3500, "ps5", 8500)[1]                    # a plain, very cheap one is still a steal


def test_synonyms_sellers_use():
    assert rules.classify_target("Brand new Gltne 28 inch road bicycle 21 speed", "", "road bike").is_target
    assert not rules.classify_target("Full carbon road fork", "", "road bike").is_target
    assert not rules.classify_target("Titan 650 Series, 8 Speed Bike", "", "road bike").is_target


# The Series S that got through: its description lists what "each and every console comes with", and the
# small local model once agreed it was a Series X. A different model in the title must be final.
SERIES_S_DESC = ("I'm selling  Xbox series S for R2500 on big special now. Normal price R5999.\n"
                 "Each and every console comes with>>>\n2 controllers\n5 games\n1TB space\nCables\n\n"
                 "Xbox Series S\nR2,500\nElectronics\nVideo game consoles\n"
                 "Listed 4 weeks ago in Kaalfontein, Johannesburg\nDetails\nSeller information\nSeller details\n"
                 "Thapello\nJoined Facebook in 2025")


def test_a_different_model_is_final():
    v, steal = run("Xbox Series S", 2500, "xbox series x", 7500, SERIES_S_DESC)
    assert not v.is_target_item and not v.unsure and not steal
    t = rules.classify_target("Xbox Series S", "also have an xbox series x for sale", "xbox series x")
    assert not t.is_target and t.certain                       # even if the description names the other one
    for title, query in [("Xbox Series S", "xbox series x"), ("Xbox One X 1TB", "xbox series x"),
                         ("iPhone 14", "iphone 13"), ("iPhone 13", "iphone 13 pro"), ("PS4 slim", "ps5")]:
        t = rules.classify_target(title, "", query)
        assert not t.is_target and t.certain, title
    for title, query in [("Xbox Series console", "xbox series x"), ("Trek road racer", "road bike")]:
        t = rules.classify_target(title, "", query)
        assert not t.is_target and not t.certain, title         # only vague: a photo may still settle it


def test_details_block_facts():
    assert listed_ago(SERIES_S_DESC) == "4 weeks ago"
    assert listed_ago("Listed a day ago in Tshwane, South Africa") == "a day ago"
    assert listed_ago("no such line") == ""
    assert pretty_price("ZAR8,500") == "R8,500" and pretty_price("$120") == "$120" and pretty_price("Free") == "Free"
    r = rules.judge_listing("Xbox Series X", "Joined Facebook in 2014", 3000, "R3,000", "xbox series x", typical=7500)
    assert not any("new account" in f for f in r.red_flags)
    assert r.summary == "About 60% under the usual R7,500 - you'd save about R4,500."
    cond = lambda d: rules.judge_listing("Xbox Series X", d, 7000, "R7,000", "xbox series x").condition  # noqa: E731
    assert cond("Details\nCondition\nUsed – like new\nPretoria") == "used - like new"
    assert cond("Details\nCondition\nNew\nPretoria") == "new"
    assert cond("In good condition\nInstallment available") == "good"      # seller prose is not the field
    assert cond("Selling my console") == "unknown"


# ---- photo check wording: it can warn, it never changes the score ----

@pytest.mark.parametrize("opinion,expect,warn", [
    ({"is_item": True, "photo": "real", "condition": "like new", "reason": "Shows a console and two controllers."},
     "Photo looks right: shows a console and two controllers", False),
    ({"is_item": True, "photo": "stock", "condition": "unknown", "reason": "x"}, "catalogue or advert", True),
    ({"is_item": True, "photo": "different", "condition": "unknown", "reason": "x"}, "something else", True),
    ({"is_item": False, "photo": "real", "condition": "", "reason": "It is a controller."},
     "may not be the xbox series x - it is a controller", True),
    ({"is_item": True, "photo": "none", "condition": "unknown", "reason": ""}, "", False),
    (None, "", False),
])
def test_photo_line(opinion, expect, warn):
    line, is_warn = judge.photo_line(opinion, "xbox series x")
    assert expect in line and (line == "" if not expect else True) and is_warn is warn
