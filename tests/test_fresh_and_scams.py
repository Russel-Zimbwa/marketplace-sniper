"""Only fresh listings are ever sent, and the wider scam checks."""
import asyncio
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mpwatch import judge, judge_rules as rules, parser, pricing  # noqa: E402,F401
from mpwatch.bot import alert_text  # noqa: E402
from mpwatch.db import DB  # noqa: E402
from mpwatch.main import Watcher  # noqa: E402
from mpwatch.parser import Listing, ago_text  # noqa: E402

NOW = time.time()


@pytest.fixture(autouse=True)
def no_ollama(monkeypatch):
    async def none():
        return None
    monkeypatch.setattr(judge, "_ollama_model", none)


def L(title="Xbox Series X", price=3000, mins=10, desc="", lid="1", **kw):
    return Listing(id=lid, title=title, price=price, price_text=f"R{price:,}", description=desc,
                   created=(NOW - mins * 60) if mins is not None else None, **kw)


# ---- reading Facebook's data ----

def test_search_data_gives_age_seller_and_delivery():
    d = {"id": "123", "marketplace_listing_title": "Xbox Series X", "creation_time": int(NOW) - 600,
         "listing_price": {"amount": "3000.00", "formatted_amount": "ZAR3,000"},
         "strikethrough_price": {"amount": "4500.00"}, "delivery_types": ["SHIPPING_OFFSITE"],
         "marketplace_listing_seller": {"id": "pfbid0abc", "name": "Someone"}}
    l = parser.listings_from_blobs([{"data": [d]}])["123"]
    assert 9 <= l.age_minutes() <= 11 and l.seller_id == "pfbid0abc" and l.shipping_only and l.was_price == 4500
    assert "Someone" not in repr(l)                                            # seller names are never kept
    d["delivery_types"] = ["IN_PERSON", "SHIPPING_OFFSITE"]
    assert not parser.listings_from_blobs([{"data": [d]}])["123"].shipping_only


def test_age_falls_back_to_the_listing_page():
    assert L(mins=None, desc="Listed 3 hours ago in Pretoria").age_minutes() == 180
    assert L(mins=None, desc="Listed a minute ago").age_minutes() == 1
    assert L(mins=None).age_minutes() is None
    assert [ago_text(x) for x in (0.5, 12, 61, 150, 4000)] == ["just now", "12 min ago", "1 h ago", "2 h ago",
                                                               "3 days ago"]


# ---- the watcher never sends an old post ----

class FakeBot:
    def __init__(self):
        self.sent = []

    async def send(self, text):
        self.sent.append(("msg", text))

    async def send_alert(self, watch, l, v, **kw):
        self.sent.append(("alert", l.id, kw))


class FakeBrowser:
    def __init__(self):
        self.opened = []

    async def details(self, l):
        self.opened.append(l.id)
        return l


def watcher(tmp_path):
    w = Watcher.__new__(Watcher)
    w.db, w.bot, w.browser, w._wake = DB(tmp_path / "t.db"), FakeBot(), FakeBrowser(), asyncio.Event()
    wid = w.db.add_watch("xbox series x", None)
    w.db.mark_seeded(wid)
    pricing.record(w.db, wid, [L(price=p, lid=f"p{i}") for i, p in enumerate((7000, 7500, 7500, 8000))],
                   "xbox series x")
    return w, w.db.get_watch(wid)


def alerts(w):
    return sorted(e[1] for e in w.bot.sent if e[0] == "alert")


def test_old_posts_are_never_opened_or_sent(tmp_path):
    w, watch = watcher(tmp_path)
    listings = [L(lid="fresh", mins=8), L(lid="old", mins=60 * 24 * 3), L(lid="edge", mins=125),
                L(lid="nodate", mins=None, desc="Listed 2 weeks ago in Pretoria"),
                L(lid="nodate-new", mins=None, desc="Listed 20 minutes ago in Pretoria")]
    asyncio.run(w.process(watch, listings))
    assert sorted(w.browser.opened) == ["fresh", "nodate", "nodate-new"]      # old ones are not even opened
    assert alerts(w) == ["fresh", "nodate-new"]


def test_fresh_window_can_be_changed(tmp_path):
    w, watch = watcher(tmp_path)
    w.db.set("fresh_minutes", 300)
    asyncio.run(w.process(watch, [L(lid="edge", mins=125)]))
    assert alerts(w) == ["edge"]


def test_price_drop_on_an_old_post_is_not_sent(tmp_path):
    w, watch = watcher(tmp_path)
    w.db.mark_seen(watch.id, "old", 6000)
    asyncio.run(w.process(watch, [L(lid="old", price=3000, mins=60 * 30)]))
    assert w.bot.sent == []


def test_first_report_sends_no_old_deals(tmp_path):
    w, watch = watcher(tmp_path)
    w.db.conn.execute("UPDATE watches SET seeded=0")
    asyncio.run(w.first_report(w.db.get_watch(watch.id), [L(lid="old", mins=60 * 24 * 5), L(lid="new", mins=15)]))
    assert alerts(w) == ["new"]
    head = w.bot.sent[0][1]
    assert "1 of them posted in the last 2 hours" in head and "first to message the seller" in head


def test_alert_shows_how_fresh_it_is():
    l = L(mins=12)
    v = asyncio.run(judge.judge(l, "xbox series x", None, 7500))
    assert alert_text(SimpleNamespace(id=1, query="xbox series x"), l, v).split("\n")[0].endswith("· 12 min ago")


def test_facebook_price_cut_is_shown():
    l = L(price=5000, was_price=6500)
    v = asyncio.run(judge.judge(l, "xbox series x", None, 7500))
    assert "📉 <b>Price dropped</b> – was R6,500" in alert_text(SimpleNamespace(id=1, query="xbox series x"), l, v)


# ---- more scam checks ----

def verdict(desc="", price=3000, typical=7500, **ctx):
    l = L(price=price, desc=desc, **ctx)
    return asyncio.run(judge.judge(l, "xbox series x", None, typical)), l


@pytest.mark.parametrize("desc,ctx,reason", [
    ("Payment before delivery please, EFT only", {}, "payment or a fee"),
    ("Courier fee of R250 applies, delivered to your door", {}, "payment or a fee"),
    ("I'm out of town, my brother will deliver", {}, "someone else will deliver"),
    ("", {"shipping_only": True}, "only be shipped"),
    ("", {"seller_listings": 4}, "4 of these"),
    ("", {"duplicate_ads": 2}, "3 different listings"),
    ("Brand new, call 071 234 5678", {}, "off Facebook"),
    ("In stock! Each and every console comes with 2 controllers. Nationwide delivery", {}, "shop advert"),
])
def test_scam_patterns(desc, ctx, reason):
    v, l = verdict(desc, **ctx)
    assert v.likely_scam and not judge.is_steal(l, v), desc
    assert reason in v.summary, v.summary


@pytest.mark.parametrize("desc,ctx", [
    ("", {}),
    ("Collect in Pretoria, cash on collection. Can test before you buy", {}),
    ("No deposit needed, not shipping, collect only", {}),
    ("Selling because I upgraded. 1TB, 2 controllers", {"seller_listings": 2}),
])
def test_honest_steals_stay_steals(desc, ctx):
    v, l = verdict(desc, **ctx)
    assert not v.likely_scam and judge.is_steal(l, v), desc


@pytest.mark.parametrize("desc", ["Installment available", "R3000 per month", "Lay-by available",
                                  "Prices from R3000"])
def test_price_that_is_not_the_price(desc):
    v, l = verdict(desc)
    assert v.deal_score == 5 and any("price bait" in f for f in v.red_flags) and not judge.is_steal(l, v)


def test_stock_photo_tips_a_doubtful_steal_into_scam():
    l = L(desc="Brand new")      # 60% under + brand new: one doubt -> still a steal
    v = asyncio.run(judge.judge(l, "xbox series x", None, 7500))
    assert not v.likely_scam
    again = judge.rejudge_with_photo(l, "xbox series x", None, 7500, {"is_item": True, "photo": "stock"})
    assert again and again.likely_scam and "catalogue picture" in again.summary
    assert judge.rejudge_with_photo(l, "xbox series x", None, 7500, {"is_item": True, "photo": "real"}) is None
    assert judge.rejudge_with_photo(L(), "xbox series x", None, 7500, {"is_item": True, "photo": "stock"}) is None


def test_same_ad_text_is_noticed(tmp_path):
    w, _ = watcher(tmp_path)
    text = ("Xbox series X for sale, barely used, comes with two controllers and three games, "
            "collect in Pretoria east")
    a, b, c = L(lid="a", desc=text), L(lid="b", desc=text + "\nDetails\nSeller information"), L(lid="c", desc="Short")
    for l in (a, b, c):
        w.remember_ad(l)
    assert (a.duplicate_ads, b.duplicate_ads, c.duplicate_ads) == (0, 1, 0)


def test_seller_count_per_search(tmp_path):
    w, _ = watcher(tmp_path)
    ls = [L(lid=str(i), seller_id="s1") for i in range(3)] + [L(lid="x", seller_id="s2"), L(lid="y")]
    w.annotate(ls)
    assert [l.seller_listings for l in ls] == [3, 3, 3, 1, 1]


def test_one_failed_search_does_not_skip_the_others(tmp_path):
    w, watch = watcher(tmp_path)
    w.db.add_watch("ps5", None)
    for x in w.db.watches():
        w.db.mark_seeded(x.id)
        w.db.set(f"deep:{x.id}", time.time())
    calls = []

    async def search(query, *a, **k):
        calls.append(query)
        if query == "xbox series x":
            return SimpleNamespace(status="error", detail="Page.goto: Timeout 45000ms exceeded.", listings=[],
                                   center=None)
        return SimpleNamespace(status="ok", detail="", listings=[], center=None)
    w.browser.search, w.browser.public_mode = search, True

    async def no_sleep(*_):
        return None
    import mpwatch.main as m
    real = m.asyncio.sleep
    m.asyncio.sleep = no_sleep
    try:
        asyncio.run(w.cycle())                                  # doesn't raise: ps5 was still checked
        assert calls == ["xbox series x", "ps5"]
        w.db.delete_watch(2)
        with pytest.raises(RuntimeError):                       # only raises when everything failed
            asyncio.run(w.cycle())
    finally:
        m.asyncio.sleep = real
