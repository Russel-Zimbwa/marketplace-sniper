"""Entry point: python -m mpwatch.main"""
import asyncio
import hashlib
import html
import json
import logging
import random
import re
import socket
import sys
import time
from logging.handlers import RotatingFileHandler
from typing import Optional

from . import geo, judge, judge_rules, pricing
from .bot import Bot
from .browser import FBBrowser
from .config import config
from .db import DB, Watch
from .parser import Listing

log = logging.getLogger("mpwatch")


def setup_logging() -> None:
    config.log_dir.mkdir(exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    fh = RotatingFileHandler(config.log_dir / "mpwatch.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    fh.setFormatter(fmt)
    handlers = [fh]
    if sys.stdout is not None:  # pythonw.exe (Windows background) has no console
        sh = logging.StreamHandler(sys.stdout)
        sh.setFormatter(fmt)
        handlers.append(sh)
    logging.basicConfig(level=logging.INFO, handlers=handlers)
    logging.getLogger("httpx").setLevel(logging.WARNING)


class Watcher:
    def __init__(self):
        self.db = DB()
        self.browser = FBBrowser()
        self.bot = Bot(self)
        self._wake = asyncio.Event()

    @property
    def judge_on(self) -> bool:
        """The bot's 'judge on/off' wins; otherwise JUDGE in .env (on by default)."""
        s = self.db.get("judge")
        return s == "on" if s in ("on", "off") else judge.enabled()

    @property
    def threshold(self) -> int:
        return int(self.db.get("threshold") or config.deal_threshold)

    @property
    def location(self) -> Optional[str]:
        # A location set from Telegram wins; .env LOCATION_ID is only the starting default
        return self.db.get("location") or config.location_id or None

    @property
    def radius(self) -> int:
        try:
            return int(self.db.get("radius") or config.radius_km)
        except ValueError:
            return config.radius_km

    @property
    def far_radius(self) -> int:
        """Wider ring where only exceptional deals are sent. 0 = off."""
        try:
            return int(self.db.get("far_radius") or config.far_radius_km)
        except ValueError:
            return config.far_radius_km

    @property
    def fresh_minutes(self) -> int:
        """Only listings posted within this many minutes are ever sent."""
        try:
            return int(self.db.get("fresh_minutes") or config.fresh_minutes)
        except ValueError:
            return config.fresh_minutes

    def is_fresh(self, l: Listing) -> bool:
        age = l.age_minutes()
        return age is not None and age <= self.fresh_minutes

    def annotate(self, listings: list[Listing]) -> None:
        """Facts that need more than one listing: how many of these the same seller has up right now."""
        per_seller: dict[str, int] = {}
        for l in listings:
            if l.seller_id:
                per_seller[l.seller_id] = per_seller.get(l.seller_id, 0) + 1
        for l in listings:
            l.seller_listings = per_seller.get(l.seller_id, 1) if l.seller_id else 1

    def remember_ad(self, l: Listing) -> None:
        """Note the description; flag it if other listings carry word-for-word the same text."""
        body = re.split(r"\n\s*(?:details|seller information|listed\s)", l.description or "", maxsplit=1, flags=re.I)[0]
        words = re.findall(r"[a-z0-9]+", body.lower())
        if len(words) >= 12:  # short texts ("Xbox series s 512g") repeat by chance
            l.duplicate_ads = self.db.same_ad(hashlib.sha1(" ".join(words).encode()).hexdigest(), l.id)

    @property
    def scams_on(self) -> bool:
        """Send probable scams (quietly, labelled) rather than hiding them. On unless 'scams off'."""
        return self.db.get("scams") != "off"

    def wake(self) -> None:
        self._wake.set()

    async def _sleep(self, seconds: float) -> None:
        try:
            await asyncio.wait_for(self._wake.wait(), timeout=seconds)
        except asyncio.TimeoutError:
            pass
        self._wake.clear()

    # ---- main loop ----
    async def run(self) -> None:
        await self.browser.start()
        await self.bot.start()
        log.info("started; judge=%s (%s)", self.judge_on, judge.describe())
        if not self.location:
            slug = await self.browser.detect_location()
            if slug:
                self.db.set("location", slug)
                log.info("detected location %s", slug)
        if time.time() - float(self.db.get("last_check") or 0) > 600:  # quiet on a quick restart
            n = len(self.db.watches(only_active=True))
            await self.bot.send(f"✅ Back online, watching {n} {'item' if n == 1 else 'items'}. "
                                "Send <b>help</b> to see what I can do.")
        failures = 0
        try:
            while True:
                started = time.time()
                if self.db.get("paused") != "1" and time.time() >= float(self.db.get("blocked_until") or 0):
                    try:
                        await self.cycle()
                        failures = 0
                    except Exception:
                        failures += 1
                        log.exception("cycle failed (%d in a row)", failures)
                        if failures == 5:
                            await self.bot.send("⚠️ <b>My checks keep failing</b> (5 in a row). I'll keep trying; "
                                                "if this goes on, look at logs/mpwatch.log on the laptop.")
                # The cycle itself takes a while with several watches; only wait for what is left of the
                # interval, so every watch is looked at again within a minute or two.
                await self._sleep(max(15.0, random.uniform(config.min_interval, config.max_interval)
                                      - (time.time() - started)))
        finally:
            await self.bot.stop()
            await self.browser.close()

    async def cycle(self) -> None:
        searched, errors = 0, []
        for w in self.db.watches(only_active=True):
            if self.db.get(f"pick:{w.id}"):
                continue  # waiting for the buyer to say which model they want
            if searched:
                await asyncio.sleep(random.uniform(4, 12))
            searched += 1
            # Mostly a quick "what's new in the last 24h" check; every half hour a full re-scan of
            # everything for sale, which catches price drops and anything missed while offline.
            deep = time.time() - float(self.db.get(f"deep:{w.id}") or 0) > config.rescan_minutes * 60
            if not w.seeded:
                res = await self.survey(w)
            elif deep:
                res = await self.browser.search(w.query, self.location, self.radius, days=None, newest=False, scrolls=3)
            else:
                res = await self.browser.search(w.query, self.location, self.radius)
            if res is None:
                continue  # asked which model; nothing to do until they answer
            if deep and res.status == "ok":
                self.db.set(f"deep:{w.id}", time.time())
            self.db.set("last_check", time.time())
            if res.status == "logged_out":
                await self.handle_logged_out(res.detail)
                return
            if res.status != "ok":
                # One slow page shouldn't cost every other item its check this round
                errors.append(f"#{w.id}: {str(res.detail).splitlines()[0][:120]}")
                log.warning("watch #%d '%s' search failed: %s", w.id, w.query, errors[-1])
                continue
            self.db.set("fb_status", "not logged in (public browsing) ✅" if self.browser.public_mode else "logged in ✅")
            await self.facebook_ok()
            pricing.record(self.db, w.id, res.listings, w.query)  # all listings, near or far, teach typical price
            self.annotate(res.listings)
            listings, far = await self.locate(res)
            if res.listings and not any(l.created for l in res.listings):
                log.warning("watch #%d: Facebook gave no posting times; ages will come from each listing's page",
                            w.id)
            log.info("watch #%d '%s': %d listings, %d within %d km, %d more within %d km", w.id, w.query,
                     len(res.listings), len(listings), self.radius, len(far), self.far_radius)
            if w.seeded:
                await self.process(w, listings, far)
            else:
                await self.first_report(w, listings, far)
        if errors and len(errors) >= searched:
            raise RuntimeError("every search failed: " + "; ".join(errors))


    async def survey(self, w: Watch):
        """First look at a new watch: everything currently for sale (any age), to learn the usual price,
        and fix typos in the item name from the words sellers use."""
        res = await self.browser.search(w.query, self.location, self.radius, days=None, newest=False, scrolls=3)
        if res.status != "ok" or not res.listings:
            return res
        fixed = judge_rules.correct_query(w.query, [l.title for l in res.listings])
        if fixed.lower() != w.query.lower():
            log.info("watch #%d: read '%s' as '%s'", w.id, w.query, fixed)
            self.db.rename_watch(w.id, fixed)
            w.query = fixed
            await asyncio.sleep(random.uniform(3, 6))
            again = await self.browser.search(fixed, self.location, self.radius, days=None, newest=False, scrolls=3)
            if again.status != "ok":
                return again
            if again.listings:
                res = again
        if self.judge_on and not self.db.get(f"asked:{w.id}"):
            models = judge_rules.find_variants(w.query, [(l.title, l.price) for l in res.listings])
            if models:
                await self.ask_model(w, models)
                return None
        return res

    # ---- "which model do you mean?" ----
    async def ask_model(self, w: Watch, models) -> None:
        e = html.escape
        rows = [f"{i}. <b>{e(v.query)}</b> – {v.count} for sale" + (f", about {v.price:,.0f}" if v.price else "")
                for i, v in enumerate(models, 1)]
        text = (f"🤔 <b>{e(w.query)}</b> covers several models with different prices. Which do you want?\n\n"
                + "\n".join(rows)
                + "\n\nReply with the number (or several, like <b>1 3</b>), <b>all</b> for every model above, "
                  "or <b>skip</b> to watch it exactly as you typed it. If your model isn't listed, just type "
                  "its full name.")
        self.db.set(f"asked:{w.id}", "1")
        self.db.set(f"pick:{w.id}", json.dumps({"at": time.time(), "text": text,
                                                 "options": [v.query for v in models]}))
        log.info("watch #%d '%s': asked which model (%s)", w.id, w.query, ", ".join(v.label for v in models))
        await self.bot.send(text)

    def pending_pick(self) -> Optional[tuple[int, dict]]:
        """The most recent unanswered 'which model?' question."""
        picks = []
        for key, value in self.db.items("pick:"):
            try:
                picks.append((int(key.split(":")[1]), json.loads(value)))
            except (ValueError, TypeError):
                self.db.delete(key)
        return max(picks, key=lambda p: p[1].get("at", 0)) if picks else None

    def typed_model(self, query: str) -> Optional[int]:
        """The buyer answered 'which model?' by typing the full name ('garmin forerunner 265')
        instead of a number: narrow that waiting watch rather than leaving it hanging."""
        new = set(judge_rules.tokens(query))
        for key, _ in self.db.items("pick:"):
            wid = int(key.split(":")[1])
            w = self.db.get_watch(wid)
            old = set(judge_rules.tokens(w.query)) if w else set()
            if w and old and old < new:
                self.db.delete(key)
                self.db.rename_watch(wid, query)
                self.wake()
                return wid
        return None

    def answer_pick(self, text: str) -> str:
        """Apply the buyer's reply to the pending question. Returns the message to send back."""
        pend = self.pending_pick()
        if not pend:
            return "Nothing to pick right now. Type an item to watch, or <b>list</b>."
        wid, data = pend
        w, options = self.db.get_watch(wid), data.get("options", [])
        answer = text.lower().strip()
        if w is None:
            self.db.delete(f"pick:{wid}")
            return self.answer_pick(text) if self.pending_pick() else "That watch was stopped."
        if answer in ("skip", "none", "any", "as typed"):
            chosen = []
        elif answer in ("all", "everything", "every"):
            chosen = options
        else:
            nums = [int(x) for x in re.findall(r"\d+", answer)]
            if not nums or any(not 1 <= x <= len(options) for x in nums):
                return f"Reply with a number from 1 to {len(options)}, <b>all</b>, or <b>skip</b>."
            chosen = [options[x - 1] for x in dict.fromkeys(nums)]
        self.db.delete(f"pick:{wid}")
        if not chosen:
            reply = f"OK - watching <b>{html.escape(w.query)}</b> exactly as you typed it."
        else:
            existing = {x.query.lower() for x in self.db.watches() if x.id != wid}
            chosen = [q for q in chosen if q.lower() not in existing]
            if not chosen:
                self.db.delete_watch(wid)
                reply = "You're already watching those."
            else:
                self.db.rename_watch(wid, chosen[0])
                for q in chosen[1:]:
                    self.db.set(f"asked:{self.db.add_watch(q, w.max_price)}", "1")
                names = ", ".join(f"<b>{html.escape(q)}</b>" for q in chosen)
                reply = f"👍 Watching {names}. Give me a minute to price {'it' if len(chosen) == 1 else 'them'}."
        self.wake()
        nxt = self.pending_pick()
        return reply + ("\n\n" + nxt[1].get("text", "") if nxt else "")

    async def first_report(self, w: Watch, listings: list[Listing], far: list[Listing] = ()) -> None:
        """Sent once when a watch starts: the usual price, and any good deal posted in the last couple of
        hours. Older listings are only used to learn the price - they are never sent."""
        self.db.mark_seeded(w.id)
        for l in [*listings, *far]:
            self.db.mark_seen(w.id, l.id, l.price)
        if not self.judge_on:
            await self.process_unjudged_summary(w, [l for l in listings if self.is_fresh(l)], len(listings))
            return
        e = html.escape
        typ, n = pricing.typical(self.db, w.id), pricing.samples(self.db, w.id)
        like = next((l.price_text for l in [*listings, *far] if l.price), "")  # for the currency sign
        ok = lambda l: not l.sold and (w.max_price is None or l.price is None or l.price <= w.max_price)  # noqa: E731
        fresh = [l for l in listings if ok(l) and self.is_fresh(l)]
        good, scams = [], 0
        for l in sorted(fresh, key=lambda l: (l.price is None, l.price or 0))[:6]:
            try:
                await self.browser.details(l)
                self.remember_ad(l)
                v = await judge.judge(l, w.query, w.max_price, typ)
            except Exception:
                log.exception("first report: could not judge %s", l.id)
                continue
            if not v or not v.is_target_item or l.sold:
                continue
            if v.likely_scam:
                scams += 1
            elif v.deal_score >= self.threshold or judge.is_steal(l, v):
                good.append((v, l))
        good.sort(key=lambda x: (-x[0].deal_score, x[1].price or 0))
        window = ago_window(self.fresh_minutes)
        head = [f"🎯 <b>{e(w.query)}</b>" + (f" · up to {judge_rules._money(w.max_price, like)}" if w.max_price else "")]
        head.append(f"Usual price: about <b>{judge_rules._money(typ, like)}</b> (from {n} listings)" if typ else
                    "Not enough listings yet to know the usual price, so for now I'll send you every real one.")
        head.append(f"{len(listings)} for sale within {self.radius} km of {e(self.location or 'you')}, "
                    f"{len(fresh)} of them posted in the last {window}.")
        if good:
            head.append(f"\n⬇️ <b>Just posted and worth a look</b>:")
        if scams:
            head.append(f"\n⚠️ {scams} new one{'s' if scams > 1 else ''} looked too good to be true, so I left "
                        f"{'them' if scams > 1 else 'it'} out.")
        head.append(f"\nI only send listings posted in the last {window}, the moment I see them, so you can be "
                    "first to message the seller" + (f" – and steals up to {self.far_radius} km away." if self.far_radius
                                                     else "."))
        await self.bot.send("\n".join(head))
        for v, l in good[:3]:
            await self.alert(w, l, v)
        for l in [x for x in far if ok(x) and self.is_fresh(x)][:4]:
            try:
                await self.evaluate_and_alert(w, l, min_score=config.far_min_score)
            except Exception:
                log.exception("first report: could not judge far listing %s", l.id)

    async def locate(self, res) -> tuple[list[Listing], list[Listing]]:
        """Split results into near (inside the radius, or town unknown) and far (outside it but inside
        the wider ring, where only exceptional deals are worth the drive). Anything further is dropped."""
        if res.center:
            self.db.set("center", f"{res.center[0]},{res.center[1]}")
        center = res.center
        if not center and self.db.get("center"):
            center = tuple(map(float, self.db.get("center").split(",")))
        near, far = [], []
        for l in res.listings:
            l.distance_km = await geo.distance(self.db, l.location, center)
            if l.distance_km is None or l.distance_km <= self.radius + geo.SLACK_KM:
                near.append(l)
            elif l.distance_km <= self.far_radius:
                l.far = True
                far.append(l)
        return near, far

    async def nearby(self, res) -> list[Listing]:
        return (await self.locate(res))[0]

    BLOCK_WAITS = (5, 15, 30, 60, 120)  # minutes between retries while Facebook shows a login wall

    async def handle_logged_out(self, url: str) -> None:
        """Facebook showed a login wall instead of results. Back off and retry on our own; tell the
        buyer only if it keeps happening. (Stopping until someone sends 'resume' once cost 22 hours
        of watching over a single slow page.)"""
        n = int(self.db.get("block_count") or 0) + 1
        wait = self.BLOCK_WAITS[min(n, len(self.BLOCK_WAITS)) - 1]
        self.db.set("block_count", n)
        self.db.set("blocked_until", time.time() + wait * 60)
        self.db.set("fb_status", f"⚠️ login wall {n} time{'s' if n > 1 else ''} in a row - retrying every {wait} min")
        log.warning("facebook login wall (%d in a row), retrying in %d min: %s", n, wait, url)
        if n == 3:
            await self.bot.send("🚫 <b>Facebook keeps showing me a login wall</b>, so I can't see listings right now.\n"
                                "I'm retrying on my own every hour or so and will tell you when it works again. "
                                "Nothing for you to do.")

    async def facebook_ok(self) -> None:
        n = int(self.db.get("block_count") or 0)
        if n:
            self.db.set("block_count", 0)
            self.db.set("blocked_until", 0)
            log.info("facebook is serving results again after %d login wall(s)", n)
            if n >= 3:
                await self.bot.send("✅ <b>Facebook is letting me in again.</b> Back to checking every minute or two.")

    async def process_unjudged_summary(self, w: Watch, listings: list[Listing], total: int = 0) -> None:
        existing = [l for l in listings if not l.sold
                    and (w.max_price is None or l.price is None or l.price <= w.max_price)]
        existing.sort(key=lambda l: (l.price is None, l.price or 0))
        typ = pricing.typical(self.db, w.id)
        rows = [f"• <a href=\"{l.url}\">{html.escape(l.price_text)} – {html.escape(l.title[:50])}</a>"
                f" ({html.escape(l.location)})" for l in existing[:10]]
        await self.bot.send(
            f"🎯 <b>{html.escape(w.query)}</b>\n{total or len(listings)} for sale nearby, "
            f"{len(listings)} posted in the last {ago_window(self.fresh_minutes)}"
            + (f", usual price about {typ:,.0f}" if typ else "") + ".\n"
            + ("\nJust posted:\n" + "\n".join(rows) + "\n" if rows else "")
            + "\nI'll send you every new one the moment it's posted."
        )

    async def process(self, w: Watch, listings: list[Listing], far: list[Listing] = ()) -> None:
        new, dropped = [], {}
        for l in [*listings, *far]:
            if not self.db.is_seen(w.id, l.id):
                self.db.mark_seen(w.id, l.id, l.price)
                new.append(l)
                continue
            old = self.db.seen_price(w.id, l.id)
            if l.price and l.price != old:
                self.db.set_seen_price(w.id, l.id, l.price)
                if old and l.price <= 0.9 * old:  # seller dropped the price: judge it again
                    dropped[l.id] = old
                    new.append(l)
        # No title filtering: Facebook's search already matched the query, and odd titles
        # ("XSX", "Microsoft console") are exactly where steals hide. The judge looks at every one.
        # Only what was posted in the last couple of hours: old listings are gone or are bait. A listing
        # with no posting time in the search data is opened, and its own page decides.
        candidates = [
            l for l in new
            if not l.sold and (w.max_price is None or l.price is None or l.price <= w.max_price)
            and (l.created is None or self.is_fresh(l))
        ]
        stale = sum(1 for l in new if l.created and not self.is_fresh(l))
        if stale:
            log.info("watch #%d: skipped %d listing(s) not posted in the last %d min", w.id, stale,
                     self.fresh_minutes)
        # Cheapest first, so the most likely steal is alerted soonest
        candidates.sort(key=lambda l: (l.price is None, l.price or 0))
        typ = pricing.typical(self.db, w.id)
        for l in candidates:
            if l.far:
                # Far away: only open it if title and price alone already make it exceptional
                if not self.judge_on:
                    continue
                q = judge_rules.judge_listing(l.title, "", l.price, l.price_text, w.query, w.max_price, typ)
                if not (q.is_target and q.score >= config.far_min_score):
                    continue
            try:
                await self.evaluate_and_alert(w, l, drop_from=dropped.get(l.id),
                                              min_score=config.far_min_score if l.far else None)
            except Exception:
                if l.far:
                    log.exception("evaluate failed for far listing %s; skipped", l.id)
                    continue
                log.exception("evaluate failed for %s; alerting anyway", l.id)
                await self.bot.send_alert(w, l, None, note="I couldn't check this one automatically - have a look.")

    async def evaluate_and_alert(self, w: Watch, l: Listing, force: bool = False,
                                 drop_from: Optional[float] = None, min_score: Optional[int] = None) -> None:
        """Open the listing, judge it (instant), and alert straight away if it is good enough."""
        need = max(self.threshold, min_score or 0)
        await self.browser.details(l)
        if l.sold and not force:
            return
        if not force and not self.is_fresh(l):
            age = l.age_minutes()
            log.info("not sent, too old or age unknown: %s '%s' (%s)", l.id, l.title,
                     f"{age:.0f} min" if age is not None else "no posting time")
            return
        self.remember_ad(l)
        typ = pricing.typical(self.db, w.id)
        if not self.judge_on:
            # Judge off: every new nearby listing under max price is sent, tagged so junk is obvious
            extra = [x for x in [pricing.meter(l, typ)] if x]
            warn = pricing.warnings(l)
            if warn:
                extra.append("Mentions: " + ", ".join(warn))
            await self.bot.send_alert(w, l, None, extra=extra, drop_from=drop_from)
            self.db.log_alert(w.id, l, None, "; ".join(extra))
            return
        verdict = await judge.judge(l, w.query, w.max_price, typ)
        if verdict is None:
            if not l.far:  # never silently drop a listing just because the check failed
                await self.bot.send_alert(w, l, None, drop_from=drop_from,
                                          note="I couldn't check this one automatically - have a look.")
                self.db.log_alert(w.id, l, None, "")
            return
        steal = judge.is_steal(l, verdict)
        log.info("judged %s '%s' %s: score=%d target=%s fair=%s steal=%s unsure=%s flags=%s", l.id, l.title,
                 l.price_text, verdict.deal_score, verdict.is_target_item, verdict.estimated_fair_price, steal,
                 verdict.unsure, verdict.red_flags)
        if verdict.likely_scam and not force:
            log.info("probable scam %s '%s' %s: %s", l.id, l.title, l.price_text, verdict.summary)
            if verdict.is_target_item and self.scams_on and not l.far:
                await self.bot.send_alert(w, l, verdict, drop_from=drop_from, silent=True)
                self.db.log_alert(w.id, l, verdict.deal_score, verdict.summary)
            return
        if force or (verdict.is_target_item and (steal or verdict.deal_score >= need)):
            await self.alert(w, l, verdict, drop_from)
        elif verdict.unsure and not l.far:
            # Vague title at a great price: let the photo decide, without holding up the other checks
            self.spawn(self.second_look(w, l, typ, need, drop_from))

    async def alert(self, w: Watch, l: Listing, verdict, drop_from: Optional[float] = None) -> None:
        """Send the alert now; the photo check (slow) is written into the same message when it is done."""
        model = await judge.model_name() if (l.photos or l.photo) else None
        sent = await self.bot.send_alert(w, l, verdict, steal=judge.is_steal(l, verdict), drop_from=drop_from,
                                         photo_pending=bool(model))
        self.db.log_alert(w.id, l, verdict.deal_score, verdict.summary)
        if sent and model:
            self.spawn(self.photo_followup(w, l, sent))

    async def photo_followup(self, w: Watch, l: Listing, sent) -> None:
        opinion = await judge.second_look(l, w.query)
        line, warn = judge.photo_line(opinion, w.query)
        scam = judge.rejudge_with_photo(l, w.query, w.max_price, pricing.typical(self.db, w.id), opinion)
        if scam:  # a catalogue photo on top of other doubts: relabel the alert we already sent
            log.info("photo check turned %s '%s' into a probable scam", l.id, l.title)
            sent.kwargs.update(verdict=scam, steal=False)
        await self.bot.finish_photo_check(sent, line, warn)

    async def second_look(self, w: Watch, l: Listing, typ: Optional[float], need: int,
                          drop_from: Optional[float] = None) -> None:
        """A listing the rules rejected on a vague title: alert it after all if the photo check says it is the item."""
        opinion = await judge.second_look(l, w.query)
        verdict = judge.rescue(l, w.query, w.max_price, typ, opinion)
        if not verdict or verdict.likely_scam or verdict.deal_score < need:
            return
        log.info("rescued %s '%s' %s: score=%d", l.id, l.title, l.price_text, verdict.deal_score)
        line, warn = judge.photo_line(opinion, w.query)
        await self.bot.send_alert(w, l, verdict, drop_from=drop_from, photo_line=line, photo_warn=warn)
        self.db.log_alert(w.id, l, verdict.deal_score, verdict.summary)

    # ---- background work (photo checks) ----
    def spawn(self, coro) -> None:
        tasks = self.__dict__.setdefault("_tasks", set())
        task = asyncio.ensure_future(coro)
        tasks.add(task)
        task.add_done_callback(self._task_done)

    def _task_done(self, task) -> None:
        self.__dict__.get("_tasks", set()).discard(task)
        if not task.cancelled() and task.exception():
            log.error("background task failed", exc_info=task.exception())

    async def drain(self) -> None:
        """Wait for photo checks still running (used by tests and scripts)."""
        while self.__dict__.get("_tasks"):
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    async def test_alert(self, w: Watch) -> None:
        """'test 1': send the best listing near you right now, whatever its score, to show alerts work."""
        res = await self.browser.search(w.query, self.location, self.radius, days=None, newest=False)
        if res.status != "ok" or not res.listings:
            await self.bot.send(f"🧪 Test: the search came back '{res.status}' with {len(res.listings)} listings. "
                                f"{html.escape(res.detail)}")
            return
        listings = [l for l in (await self.nearby(res) or res.listings) if not l.sold]
        typ = pricing.typical(self.db, w.id)

        def rank(l: Listing):
            r = judge_rules.judge_listing(l.title, "", l.price, l.price_text, w.query, w.max_price, typ)
            return (r.is_target, r.score, -(l.price or 0))
        await self.bot.send(f"🧪 <b>Test</b> – the best <b>{html.escape(w.query)}</b> near you right now "
                            "(sent whatever its score):")
        await self.evaluate_and_alert(w, max(listings or res.listings, key=rank), force=True)


def ago_window(minutes: int) -> str:
    if minutes < 60:
        return f"{minutes} minutes"
    h = minutes / 60
    return f"{h:g} hour{'s' if h != 1 else ''}" if minutes < 2880 else f"{minutes // 1440} days"


def single_instance() -> Optional[socket.socket]:
    """Hold a local port so a second copy (double-clicked start, task + manual run) exits instead of
    fighting over the browser profile and the Telegram bot."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", config.lock_port))
        s.listen(1)
        return s
    except OSError:
        return None


def main() -> None:
    setup_logging()
    lock = single_instance()
    if lock is None:
        log.warning("another Marketplace Sniper is already running; exiting")
        sys.exit(0)
    if not config.telegram_token:
        sys.exit("TELEGRAM_BOT_TOKEN missing in .env - see README.md")
    asyncio.run(Watcher().run())


if __name__ == "__main__":
    main()
