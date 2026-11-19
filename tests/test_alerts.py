"""What the Telegram alert says, and when it is sent: straight away, with the photo check added afterwards."""
import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mpwatch import judge, pricing  # noqa: E402
from mpwatch.bot import SentAlert, alert_text  # noqa: E402
from mpwatch.db import DB  # noqa: E402
from mpwatch.main import Watcher  # noqa: E402
from mpwatch.parser import Listing  # noqa: E402

WATCH = SimpleNamespace(id=1, query="xbox series x", max_price=None)


def listing(title="Xbox Series X 1TB", price=3000, lid="1", **kw):
    desc = kw.pop("description", "Condition\nUsed - like new\nListed 2 hours ago in Pretoria\nJoined Facebook in 2019")
    return Listing(id=lid, title=title, price=price, price_text=f"R{price:,}", location="Pretoria, South Africa",
                   description=desc, photo="https://example.invalid/p.jpg", **kw)


def verdict(l, typical=7500):
    return asyncio.run(judge.judge(l, "xbox series x", None, typical))


# ---- layout ----

def test_steal_alert_reads_top_down():
    l = listing()
    v = verdict(l)
    text = alert_text(WATCH, l, v, steal=judge.is_steal(l, v))
    lines = text.split("\n")
    assert lines[0] == "🔥🔥 <b>STEAL · R3,000</b> · 2 h ago"            # what the phone notification shows
    assert lines[1] == "<b>Xbox Series X 1TB</b>"
    assert lines[2] == "About 60% under the usual R7,500 - you'd save about R4,500."
    assert "📍 Pretoria, South Africa" in lines and "📦 Used - like new" in lines
    assert "⚠️ <b>Check first</b>" in lines
    assert "• Far below the usual price - verify in person before paying" in lines
    assert lines[-1] == "<i>xbox series x · 10/10</i>"
    assert "ZAR" not in text and text.count("R3,000") == 1               # no repetition
    assert len(text) < 1000                                              # fits a Telegram photo caption


def test_tiers_and_extras():
    good = listing(price=6000)
    assert alert_text(WATCH, good, verdict(good)).startswith("👍 <b>Good deal · R6,000</b>")
    great = listing(price=5000)
    assert alert_text(WATCH, great, verdict(great)).startswith("🔥 <b>Great deal · R5,000</b>")
    meh = listing(price=8500)
    assert alert_text(WATCH, meh, verdict(meh)).startswith("ℹ️ <b>R8,500</b>")            # only via 'test'
    assert alert_text(WATCH, meh, None).startswith("🆕 <b>New listing · R8,500</b>")      # judge off
    dropped = alert_text(WATCH, good, verdict(good), drop_from=7500).split("\n")
    assert dropped[0] == "📉 <b>Price dropped</b> – was R7,500" and dropped[1].startswith("👍")
    far = listing(price=3000, distance_km=28.0, far=True)
    assert "🚗 Pretoria, South Africa · about 28 km away, outside your usual area" in alert_text(WATCH, far, verdict(far))
    nasty = listing(title="<b>Xbox</b> Series X & more", price=3000)
    assert "&lt;b&gt;Xbox&lt;/b&gt; Series X &amp; more" in alert_text(WATCH, nasty, verdict(nasty))


def test_photo_line_states():
    l = listing()
    v = verdict(l)
    assert "📷 <i>Checking the photo…</i>" in alert_text(WATCH, l, v, photo_pending=True)
    done = alert_text(WATCH, l, v, photo_line="Photo looks right: shows the console")
    assert "📷 Photo looks right: shows the console" in done and "Checking" not in done
    warn = alert_text(WATCH, l, v, photo_line="Photo check: a catalogue picture", photo_warn=True)
    assert "• Photo check: a catalogue picture" in warn and "📷" not in warn      # a warning joins 'Check first'
    assert "📷" not in alert_text(WATCH, l, v) and "Checking" not in alert_text(WATCH, l, v)


# ---- timing: the alert never waits for the photo check ----

class FakeBot:
    def __init__(self):
        self.events = []

    async def send(self, text):
        self.events.append(("msg", text))

    async def send_alert(self, watch, l, v, **kw):
        self.events.append(("alert", l.id, kw))
        return SentAlert(len(self.events), True, {}, l.url)

    async def finish_photo_check(self, sent, line, warn):
        self.events.append(("photo", line, warn))


class FakeBrowser:
    async def details(self, l):
        return l


def make_watcher(tmp_path, monkeypatch, opinion, model="gemma3:4b"):
    w = Watcher.__new__(Watcher)
    w.db = DB(tmp_path / "t.db")
    w._wake = asyncio.Event()
    w.bot, w.browser = FakeBot(), FakeBrowser()
    wid = w.db.add_watch("xbox series x", None)
    pricing.record(w.db, wid, [listing(price=p, lid=f"p{i}") for i, p in enumerate((7000, 7500, 7500, 8000))],
                   "xbox series x")
    asked = []

    async def model_name():
        return model

    async def ask(l, query, m):
        asked.append(l.id)
        await asyncio.sleep(0.05)       # the real one takes most of a minute
        return opinion
    monkeypatch.setattr(judge, "_ollama_model", model_name)
    monkeypatch.setattr(judge, "_ask_ollama", ask)
    monkeypatch.setattr(judge, "_model_lock", None)
    return w, w.db.get_watch(wid), asked


def test_alert_goes_out_before_the_photo_check(tmp_path, monkeypatch):
    opinion = {"is_item": True, "photo": "real", "condition": "like new", "reason": "Shows the console."}
    w, watch, asked = make_watcher(tmp_path, monkeypatch, opinion)

    async def go():
        await w.evaluate_and_alert(watch, listing(price=3000, lid="steal"))
        first = list(w.bot.events)
        await w.drain()
        return first
    first = asyncio.run(go())
    assert [e[0] for e in first] == ["alert"] and first[0][2]["photo_pending"] is True and first[0][2]["steal"]
    assert asked == ["steal"]
    assert w.bot.events[-1] == ("photo", "Photo looks right: shows the console", False)
    assert w.db.alerts_since(0) == 1


def test_photo_warning_is_added_but_the_alert_stays(tmp_path, monkeypatch):
    w, watch, _ = make_watcher(tmp_path, monkeypatch, {"is_item": True, "photo": "stock", "condition": "", "reason": ""})

    async def go():
        await w.evaluate_and_alert(watch, listing(price=3000, lid="steal"))
        await w.drain()
    asyncio.run(go())
    assert [e[0] for e in w.bot.events] == ["alert", "photo"] and w.bot.events[1][2] is True


def test_who_gets_a_photo_check(tmp_path, monkeypatch):
    yes = {"is_item": True, "photo": "real", "condition": "", "reason": "Shows an Xbox Series X."}
    w, watch, asked = make_watcher(tmp_path, monkeypatch, yes)

    async def go():
        await w.evaluate_and_alert(watch, listing(title="Xbox Series X", price=8500, lid="pricey"))      # no alert
        await w.evaluate_and_alert(watch, listing(title="Xbox Series S", price=2500, lid="wrong"))       # other model
        await w.evaluate_and_alert(watch, listing(title="WTB Xbox Series X", price=3000, lid="wanted"))
        await w.evaluate_and_alert(watch, listing(title="Xbox Series console", price=9000, lid="vague-dear"))
        before = list(w.bot.events)
        await w.evaluate_and_alert(watch, listing(title="Xbox Series console", price=3000, lid="vague-cheap"))
        mid = list(w.bot.events)
        await w.drain()
        return before, mid
    before, mid = asyncio.run(go())
    assert before == [] and mid == []                              # nothing sent yet, nothing held up
    assert asked == ["vague-cheap"]                                # the only one worth the laptop's time
    kind, lid, kw = w.bot.events[-1]
    assert (kind, lid) == ("alert", "vague-cheap") and kw["photo_line"].startswith("Photo looks right")


def test_without_a_model_everything_still_works(tmp_path, monkeypatch):
    w, watch, asked = make_watcher(tmp_path, monkeypatch, None, model=None)

    async def go():
        await w.evaluate_and_alert(watch, listing(price=3000, lid="steal"))
        await w.evaluate_and_alert(watch, listing(title="Xbox Series console", price=3000, lid="vague"))
        await w.drain()
    asyncio.run(go())
    assert [(e[0], e[1]) for e in w.bot.events] == [("alert", "steal")]
    assert w.bot.events[0][2]["photo_pending"] is False and asked == []
