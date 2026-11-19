"""Offline tests for the model picker and the near/far split. Run: .venv\\Scripts\\python -m pytest tests -q"""
import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mpwatch import judge_rules as rules  # noqa: E402
from mpwatch.db import DB  # noqa: E402
from mpwatch.main import Watcher  # noqa: E402
from mpwatch.parser import Listing  # noqa: E402


def _items(*groups):
    return [(title, price) for title, price, n in groups for _ in range(n)]


# ---- broad names: "which model do you mean?" ----

def test_broad_name_lists_the_models_for_sale():
    items = _items(("Garmin Forerunner 245", 2800, 2), ("Garmin Forerunner 245 Music", 3200, 1),
                   ("Garmin Forerunner 45", 1500, 2), ("Garmin Forerunner 35 GPS running watch", 700, 2),
                   ("Garmin Forerunner 745 triathlon", 5500, 2), ("Garmin Forerunner watch", 2000, 1),
                   ("Garmin Forerunner 245 strap", 150, 1))
    found = rules.find_variants("garmin forerunner", items)
    assert found[0].query == "garmin forerunner 245" and found[0].count == 3
    assert {v.label for v in found} == {"245", "45", "35", "745"}
    assert {v.label: v.price for v in found}["35"] == 700


def test_two_word_models_and_folding():
    items = _items(("Xbox Series X 1TB", 7000, 4), ("Xbox Series S", 3500, 3), ("Xbox One S", 2000, 2),
                   ("Xbox 360", 800, 2), ("Xbox controller", 500, 3))
    assert {v.query for v in rules.find_variants("xbox", items)} == {
        "xbox series x", "xbox series s", "xbox one s", "xbox 360"}
    mac = _items(("MacBook Pro 2019", 9000, 1), ("MacBook Pro 13 inch", 8000, 1), ("Macbook Pro M1", 12000, 1),
                 ("MacBook Air 2020", 7000, 1), ("MacBook Air", 6500, 2))   # one-offs fold into "pro" / "air"
    assert {v.label for v in rules.find_variants("macbook", mac)} == {"pro", "air"}


@pytest.mark.parametrize("query,items", [
    ("iphone 13", _items(("iPhone 13", 4000, 6), ("iPhone 13 128gb", 4200, 1), ("iPhone 13 Black", 5300, 1),
                         ("iPhone 13 Pro", 6500, 3), ("iPhone 13 Pro Max", 7500, 2))),
    ("xbox series x", _items(("Xbox Series X", 7000, 5), ("Xbox Series X 1TB", 7500, 3),
                             ("Xbox series x with 2 controllers", 8000, 2))),
    ("road bike", _items(("Merida Scultura road bike", 6500, 3), ("Road bike 54cm", 4000, 1),
                         ("Specialized road bike for sale", 7000, 4))),
    ("garmin forerunner", _items(("Garmin Forerunner 245", 2800, 2), ("Garmin Forerunner 45", 1500, 1))),  # too few
])
def test_specific_names_are_not_questioned(query, items):
    assert rules.find_variants(query, items) == []


def test_model_number_before_accessory_is_not_a_quantity():
    assert not rules.classify_target("Garmin Forerunner 245 strap", "", "garmin forerunner").is_target
    assert rules.classify_target("Xbox Series X 1TB 2 controllers", "", "xbox series x").is_target


def _watcher(tmp_path):
    w = Watcher.__new__(Watcher)
    w.db = DB(tmp_path / "t.db")
    w._wake = asyncio.Event()
    return w


def test_answering_the_model_question(tmp_path):
    w = _watcher(tmp_path)
    wid = w.db.add_watch("garmin forerunner", 3000)
    opts = ["garmin forerunner 245", "garmin forerunner 45", "garmin forerunner 35"]
    w.db.set(f"pick:{wid}", json.dumps({"at": 1, "text": "?", "options": opts}))
    assert "1 to 3" in w.answer_pick("9") and w.pending_pick()            # bad answer: still waiting
    assert "garmin forerunner 245" in w.answer_pick("1, 3")
    watches = {x.query: x for x in w.db.watches()}
    assert set(watches) == {"garmin forerunner 245", "garmin forerunner 35"}
    assert all(x.max_price == 3000 and not x.seeded for x in watches.values())
    assert w.pending_pick() is None
    assert "Nothing to pick" in w.answer_pick("2")

    wid2 = w.db.add_watch("xbox", None)
    pick = {"at": 2, "text": "?", "options": ["xbox series x", "xbox series s"]}
    w.db.set(f"pick:{wid2}", json.dumps(pick))
    assert "as you typed" in w.answer_pick("skip")
    assert w.db.get_watch(wid2).query == "xbox" and w.pending_pick() is None
    w.db.set(f"pick:{wid2}", json.dumps(pick))
    w.answer_pick("all")
    assert {"xbox series x", "xbox series s"} <= {x.query for x in w.db.watches()}
    w.db.set(f"pick:{wid2}", json.dumps(pick))
    w.db.delete_watch(wid2)                                               # stopping a watch clears its question
    assert w.db.items("pick:") == [] and w.db.get(f"asked:{wid2}") is None


def test_typing_the_model_name_answers_the_question(tmp_path):
    w = _watcher(tmp_path)
    wid = w.db.add_watch("garmin forerunner", None)
    w.db.set(f"pick:{wid}", json.dumps({"at": 1, "text": "?", "options": ["garmin forerunner 55"]}))
    assert w.typed_model("iphone 13") is None and w.pending_pick()          # unrelated item: still waiting
    assert w.typed_model("garmin forerunner 265") == wid
    assert w.db.get_watch(wid).query == "garmin forerunner 265" and w.pending_pick() is None


def test_far_command_toggles(tmp_path):
    from mpwatch.bot import Bot
    w = _watcher(tmp_path)
    bot = Bot.__new__(Bot)
    bot.w = w
    far = lambda arg="": asyncio.run(bot.cmd_far(arg))  # noqa: E731
    assert w.far_radius == 60                                  # on by default
    assert "OFF" in far() and w.far_radius == 0                # bare "far" flips it off
    assert "ON" in far() and w.far_radius == 60                # ... and back on, same distance
    assert "ON" in far("100 km") and w.far_radius == 100
    assert "OFF" in far("off") and w.far_radius == 0
    assert "OFF" in far("off") and w.far_radius == 0           # already off: stays off
    assert "ON" in far("on") and w.far_radius == 100           # remembers the distance you chose
    assert "ON" in far("away 80") and w.far_radius == 80       # "far away 80" works too
    assert "far 100" in far("banana") and w.far_radius == 80   # nonsense changes nothing


# ---- the wider ring: near = everything good, far = exceptional only ----

def test_near_far_split(tmp_path):
    w = _watcher(tmp_path)
    w.db.set("radius", "10")
    for town, coords in {"pretoria, south africa": (-25.7461, 28.1881), "midrand, gauteng": (-25.9992, 28.1263),
                         "johannesburg": (-26.2041, 28.0473), "rustenburg": (-25.6676, 27.2421)}.items():
        w.db.set("geo:" + town, json.dumps(coords))
    w.db.set("geo:nowhere special", "null")
    towns = ["Pretoria, South Africa", "Midrand, Gauteng", "Johannesburg", "Rustenburg", "Nowhere special", ""]
    ls = [Listing(id=str(i), title="Xbox Series X", price=3000, price_text="R3,000", location=t)
          for i, t in enumerate(towns)]
    res = SimpleNamespace(center=(-25.7533, 28.1869), listings=ls)
    near, far = asyncio.run(w.locate(res))
    assert [l.location for l in near] == ["Pretoria, South Africa", "Nowhere special", ""]   # unknown town: keep
    assert [l.location for l in far] == ["Midrand, Gauteng", "Johannesburg"] and all(l.far for l in far)
    assert 25 < far[0].distance_km < 31 and not near[0].far
    w.db.set("far_radius", "0")                                                              # far ring off
    assert asyncio.run(w.locate(res))[1] == []
