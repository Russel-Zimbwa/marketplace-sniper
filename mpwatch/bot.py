"""Telegram remote control + alerts."""
import html
import logging
import re
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.error import Conflict
from telegram.ext import Application, ContextTypes, MessageHandler, filters

from . import judge, judge_rules
from .config import config
from .parser import ago_text, listed_ago

if TYPE_CHECKING:
    from .main import Watcher

log = logging.getLogger(__name__)

HELP = (
    "🎯 <b>Marketplace Sniper</b>\n"
    "Type what you want – <b>xbox series s</b>, or <b>iphone 13 under 5000</b> – and I'll work out the usual "
    "price, show you the best ones near you, and message you the moment a good one is listed.\n\n"
    "<b>Your watches</b>\n"
    "<b>list</b> – what I'm watching\n"
    "<b>stop 2</b> – stop watch 2 (or <b>stop xbox</b>)\n"
    "<b>pause</b> / <b>resume</b> – everything, or just one: <b>pause 2</b>\n\n"
    "<b>What you get sent</b>\n"
    "<b>far</b> – far-away steals on/off (<b>far 100</b> sets how far)\n"
    "<b>scams</b> – show or hide listings that look too good to be true\n"
    "<b>fresh 60</b> – only send listings posted in the last 60 minutes\n"
    "<b>radius 20</b> – your normal search distance, in km\n"
    "<b>threshold 7</b> – how good a deal must be, 1–10\n"
    "<b>judge off</b> – every new listing instead of only good deals\n"
    "<b>location sydney</b> – change the area\n\n"
    "<b>Checks</b>\n"
    "<b>status</b> – is everything working\n"
    "<b>test 1</b> – send a sample alert for watch 1"
)

# Things people say to a bot that are not items to shop for
CHATTER = {"ok", "okay", "k", "thanks", "thank you", "thx", "cool", "nice", "great", "good", "yes", "no", "yep",
           "nope", "lol", "wow", "hmm", "what", "why", "stop", "wait", "nvm", "never mind", "sorry", "test"}
SCAM_WORDS = {"scam", "scams", "scammer", "scammers", "fake", "spam", "fraud", "its a scam", "it's a scam",
              "this is a scam", "that's a scam", "thats a scam"}
SCAM_REPLY = ("Noted. Listings priced too good to be true (new seller accounts, WhatsApp-only, unsold for days) "
              "are now labelled <b>⚠️ Too good to be true?</b> and arrive silently instead of as steals.\n"
              "Send <b>scams off</b> to hide them completely.")

PRICE_RE = re.compile(r"^(.*?)\s+(?:under|below|max|for|<|<=|upto|up to)\s*[^\d\s]{0,3}\s*([\d][\d,\.]*)(k?)\s*$", re.I)


def parse_watch(text: str) -> tuple[str, Optional[float]]:
    m = PRICE_RE.match(text.strip())
    if not m:
        return text.strip(), None
    price = float(m.group(2).replace(",", ""))
    if m.group(3).lower() == "k":
        price *= 1000
    return m.group(1).strip(), price


def fmt_price(p: Optional[float]) -> str:
    return "any price" if p is None else f"up to {p:,.0f}"


@dataclass
class SentAlert:
    """An alert already in the chat, kept so the photo check can be written into it afterwards."""
    message_id: int
    is_photo: bool
    kwargs: dict
    url: str


def _cap(text: str) -> str:
    return text[:1].upper() + text[1:] if text else text


def alert_text(watch, listing, verdict, note: str = "", extra: Optional[list] = None, steal: bool = False,
               drop_from: Optional[float] = None, photo_pending: bool = False, photo_line: str = "",
               photo_warn: bool = False) -> str:
    """The alert as it appears in Telegram. The first line is what shows in the phone notification, so it
    carries the verdict and the price; the reasons and the things to check follow."""
    e = lambda s: html.escape(str(s), quote=False)  # noqa: E731 - text only, never inside an attribute
    price = e(listing.price_text)
    score = verdict.deal_score if verdict else None
    if verdict is None:
        head = f"🆕 <b>New listing · {price}</b>"
    elif getattr(verdict, "likely_scam", False):
        head = f"⚠️ <b>Too good to be true? · {price}</b>"
    elif steal or score >= 9:
        head = f"🔥🔥 <b>STEAL · {price}</b>"
    elif score == 8:
        head = f"🔥 <b>Great deal · {price}</b>"
    elif score == 7:
        head = f"👍 <b>Good deal · {price}</b>"
    else:
        head = f"ℹ️ <b>{price}</b>"
    age = listing.age_minutes() if hasattr(listing, "age_minutes") else None
    if age is not None:  # how fresh it is, right in the notification
        head += f" · {ago_text(age)}"
    drop_from = drop_from or getattr(listing, "was_price", None)
    lines = []
    if drop_from:
        lines.append(f"📉 <b>Price dropped</b> – was {e(judge_rules._money(drop_from, listing.price_text))}")
    lines += [head, f"<b>{e(listing.title[:80])}</b>"]
    if verdict and verdict.summary:
        lines.append(e(verdict.summary))
    if note:
        lines.append(f"ℹ️ {e(note)}")

    facts = []
    if listing.location:
        d = getattr(listing, "distance_km", None)
        if getattr(listing, "far", False) and d:
            facts.append(f"🚗 {e(listing.location)} · about {d:.0f} km away, outside your usual area")
        else:
            facts.append(f"📍 {e(listing.location)}" + (f" · about {d:.0f} km away" if d and d >= 5 else ""))
    if verdict and verdict.condition and verdict.condition != "unknown":
        facts.append(f"📦 {e(_cap(verdict.condition))}")
    if age is None and listed_ago(listing.description):
        facts.append(f"🕒 Listed {e(listed_ago(listing.description))}")
    if facts:
        lines += ["", *facts]

    checks = [f"• {e(_cap(f[:110]))}" for f in (verdict.red_flags if verdict else [])[:5]]
    checks += [f"• {e(str(x)[:110])}" for x in (extra or [])]
    if photo_line and photo_warn:
        checks.append(f"• {e(photo_line)}")
    if checks:
        lines += ["", "⚠️ <b>Check first</b>" if verdict else "<b>Notes</b>", *checks]
    if photo_pending:
        lines += ["", "📷 <i>Checking the photo…</i>"]
    elif photo_line and not photo_warn:
        lines += ["", f"📷 {e(photo_line)}"]
    lines += ["", f"<i>{e(watch.query)}" + (f" · {score}/10" if score else "") + "</i>"]
    return "\n".join(lines)


class Bot:
    def __init__(self, watcher: "Watcher"):
        self.w = watcher
        self.app = Application.builder().token(config.telegram_token).build()
        self.app.add_handler(MessageHandler(filters.TEXT, self.on_text))

    @property
    def owner(self) -> Optional[str]:
        return config.telegram_chat_id or self.w.db.get("owner_chat_id")

    async def start(self) -> None:
        await self.app.initialize()
        await self.app.start()
        await self.app.updater.start_polling(drop_pending_updates=True, error_callback=self._poll_error)

    def _poll_error(self, exc: Exception) -> None:
        # One line instead of a 50-line traceback every few seconds
        if isinstance(exc, Conflict):
            log.warning("Telegram: another copy of this bot is running somewhere else (same token). "
                        "Stop the other copy - commands and alerts will be unreliable until then.")
        else:
            log.warning("Telegram polling error: %s", exc)

    async def stop(self) -> None:
        await self.app.updater.stop()
        await self.app.stop()
        await self.app.shutdown()

    async def send(self, text: str) -> None:
        if not self.owner:
            log.info("No owner yet; would send: %s", text)
            return
        try:
            await self.app.bot.send_message(self.owner, text, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
        except Exception:
            log.exception("telegram send failed")

    @staticmethod
    def _keyboard(url: str) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup([[InlineKeyboardButton("Open listing · message seller", url=url)]])

    async def send_alert(self, watch, listing, verdict, note: str = "", extra: Optional[list] = None,
                         steal: bool = False, drop_from: Optional[float] = None, photo_pending: bool = False,
                         photo_line: str = "", photo_warn: bool = False, silent: bool = False) -> Optional[SentAlert]:
        """Send one listing. With photo_pending the message says the photo is being checked; the result
        is written into the same message later by finish_photo_check()."""
        if not self.owner:
            return None
        kwargs = dict(watch=watch, listing=listing, verdict=verdict, note=note, extra=extra, steal=steal,
                      drop_from=drop_from)
        text = alert_text(**kwargs, photo_pending=photo_pending, photo_line=photo_line, photo_warn=photo_warn)
        kb = self._keyboard(listing.url)
        photo = (listing.photos or [listing.photo])[0] if (listing.photos or listing.photo) else None
        try:
            if photo:
                m = await self.app.bot.send_photo(self.owner, photo, caption=text, parse_mode=ParseMode.HTML,
                                                  reply_markup=kb, disable_notification=silent)
                return SentAlert(m.message_id, True, kwargs, listing.url)
        except Exception:
            log.warning("photo send failed, falling back to text", exc_info=True)
        try:
            m = await self.app.bot.send_message(self.owner, text, parse_mode=ParseMode.HTML, reply_markup=kb,
                                                disable_web_page_preview=True, disable_notification=silent)
            return SentAlert(m.message_id, False, kwargs, listing.url)
        except Exception:
            log.exception("alert send failed")
            return None

    async def finish_photo_check(self, sent: Optional[SentAlert], line: str, warn: bool) -> None:
        """Replace 'Checking the photo…' in an alert that was already sent with what the photo check found
        (or remove it if the check found nothing to say)."""
        if not sent or not self.owner:
            return
        text = alert_text(**sent.kwargs, photo_line=line, photo_warn=warn)
        try:
            if sent.is_photo:
                await self.app.bot.edit_message_caption(chat_id=self.owner, message_id=sent.message_id, caption=text,
                                                        parse_mode=ParseMode.HTML, reply_markup=self._keyboard(sent.url))
            else:
                await self.app.bot.edit_message_text(text, chat_id=self.owner, message_id=sent.message_id,
                                                     parse_mode=ParseMode.HTML, reply_markup=self._keyboard(sent.url),
                                                     disable_web_page_preview=True)
        except Exception as exc:
            log.warning("could not add the photo check to alert %s: %s", sent.message_id, exc)

    # ---- command handling ----
    async def on_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        msg = update.effective_message
        chat_id = str(update.effective_chat.id)
        if not self.owner:
            self.w.db.set("owner_chat_id", chat_id)
            await msg.reply_text("👋 You're now the owner of this bot. Only this chat can control it.\n\n" + HELP, parse_mode=ParseMode.HTML)
            return
        if chat_id != self.owner:
            return  # ignore strangers silently

        text = (msg.text or "").strip()
        # A reply to "which model do you mean?" - or a stray number
        if re.fullmatch(r"[\d\s,&]+|all|everything|every|skip|none|any|as typed", text.lower()):
            await msg.reply_text(self.w.answer_pick(text), parse_mode=ParseMode.HTML, disable_web_page_preview=True)
            return
        cmd, _, arg = text.lstrip("/").partition(" ")
        cmd, arg = cmd.lower(), arg.strip()
        handler = {
            "watch": self.cmd_watch, "add": self.cmd_watch,
            "list": self.cmd_list, "watches": self.cmd_list,
            "stop": self.cmd_stop, "delete": self.cmd_stop, "remove": self.cmd_stop,
            "pause": self.cmd_pause, "resume": self.cmd_resume,
            "status": self.cmd_status, "test": self.cmd_test,
            "threshold": self.cmd_threshold, "location": self.cmd_location,
            "radius": self.cmd_radius, "judge": self.cmd_judge, "far": self.cmd_far, "faraway": self.cmd_far, "scams": self.cmd_scams, "fresh": self.cmd_fresh,
        }.get(cmd)
        if handler is None and text.lower().strip(" .!?") in SCAM_WORDS:
            await msg.reply_text(SCAM_REPLY, parse_mode=ParseMode.HTML)
            return
        if handler is None and text.lower().strip(" .!?") in CHATTER:
            await msg.reply_text("👍 Type an item to watch it, or <b>help</b> to see what I can do.",
                                 parse_mode=ParseMode.HTML)
            return
        if handler is None:
            if cmd in ("start", "help", "hi", "hello", "hey", "commands", "menu") or not re.search(r"[a-z0-9]", text, re.I):
                await msg.reply_text(HELP, parse_mode=ParseMode.HTML)
                return
            handler, arg = self.cmd_watch, text.lstrip("/")  # anything else is an item to look for
        reply = await handler(arg)
        if reply:
            await msg.reply_text(reply, parse_mode=ParseMode.HTML, disable_web_page_preview=True)

    def _find(self, arg: str):
        ws = self.w.db.watches()
        if arg.lstrip("#").isdigit():
            return [w for w in ws if w.id == int(arg.lstrip("#"))]
        return [w for w in ws if arg.lower() in w.query.lower()] if arg else []

    async def cmd_watch(self, arg: str) -> str:
        if not arg:
            return "Tell me what to watch, e.g. <b>watch xbox series x under 350</b>"
        query, price = parse_watch(arg)
        same = [w for w in self.w.db.watches() if w.query.lower() == query.lower()]
        if same:
            return f"I'm already watching <b>{html.escape(query)}</b> (#{same[0].id}). Send <b>list</b> to see all."
        wid = self.w.typed_model(query) or self.w.db.add_watch(query, price)
        self.w.wake()
        return (f"👀 <b>{html.escape(query)}</b> – on it. Give me a minute to find the usual price and "
                f"what's for sale near you" + (f" (up to {price:,.0f})" if price else "") + ".")

    async def cmd_list(self, arg: str) -> str:
        ws = self.w.db.watches()
        if not ws:
            return "Nothing yet. Just type what you want, e.g. <b>xbox series s</b>"
        rows = ["<b>Watching</b>"]
        for w in ws:
            waiting = self.w.db.get(f"pick:{w.id}")
            state = "⏳ waiting for you to pick a model" if waiting else fmt_price(w.max_price) if w.active else "⏸ paused"
            rows.append(f"{w.id}. <b>{html.escape(w.query)}</b> – {state}")
        rows.append("\nTo stop one: <b>stop 2</b>")
        return "\n".join(rows)

    async def cmd_stop(self, arg: str) -> str:
        found = self._find(arg)
        if len(found) != 1:
            return "Which one? Use the number from <b>list</b>, e.g. <b>stop 2</b>"
        self.w.db.delete_watch(found[0].id)
        return f"🗑 Stopped watching <b>{html.escape(found[0].query)}</b>"

    async def cmd_pause(self, arg: str) -> str:
        if not arg:
            self.w.db.set("paused", "1")
            return "⏸ Paused all watches. Send <b>resume</b> to continue."
        found = self._find(arg)
        if len(found) != 1:
            return "Which one? e.g. <b>pause 2</b>"
        self.w.db.set_active(found[0].id, False)
        return f"⏸ Paused #{found[0].id}"

    async def cmd_resume(self, arg: str) -> str:
        if not arg:
            self.w.db.set("paused", "0")
            self.w.db.set("blocked_until", 0)
            self.w.db.set("block_count", 0)
            self.w.db.set("fb_alerted", "0")
            self.w.wake()
            return "▶️ Resumed."
        found = self._find(arg)
        if len(found) != 1:
            return "Which one? e.g. <b>resume 2</b>"
        self.w.db.set_active(found[0].id, True)
        self.w.wake()
        return f"▶️ Resumed #{found[0].id}"

    async def cmd_status(self, arg: str) -> str:
        db = self.w.db
        last = float(db.get("last_check", "0") or 0)
        secs = int(time.time() - last)
        ago = "never" if not last else f"{secs} seconds ago" if secs < 120 else f"{secs // 60} minutes ago"
        fb = db.get("fb_status", "") or ""
        model = await judge.model_name()
        n = len(db.watches(only_active=True))
        alerts = db.alerts_since(time.time() - 86400)
        return "\n".join([
            ("⏸ <b>Paused</b> – send <b>resume</b> to carry on" if db.get("paused") == "1"
             else f"✅ <b>Running</b> – last check {ago}"),
            ("🌐 Facebook: browsing fine" if "✅" in fb else f"🌐 Facebook: {html.escape(fb or 'not checked yet')}"),
            f"📍 {html.escape(self.w.location or 'Facebook default')} · within {self.w.radius} km"
            + (f", steals up to {self.w.far_radius} km" if self.w.far_radius else ""),
            f"👀 {n} {'watch' if n == 1 else 'watches'} (send <b>list</b>)",
            (f"🧠 Only good deals: {self.w.threshold}/10 or better" if self.w.judge_on
             else "🧠 Judge off: every new nearby listing is sent"),
            f"📷 Photo check: {'on (' + html.escape(model) + ')' if model else 'off'}",
            f"🕒 Only listings posted in the last {self.w.fresh_minutes} min",
            f"🔔 {alerts} {'alert' if alerts == 1 else 'alerts'} in the last 24 hours",
        ])

    async def cmd_test(self, arg: str) -> str:
        found = self._find(arg) if arg else self.w.db.watches()[:1]
        if len(found) != 1:
            return "Which watch? e.g. <b>test 1</b>"
        await self.w.test_alert(found[0])
        return ""

    async def cmd_threshold(self, arg: str) -> str:
        if not arg.isdigit() or not 1 <= int(arg) <= 10:
            return f"Current threshold: {self.w.threshold}/10. Set with e.g. <b>threshold 6</b>"
        self.w.db.set("threshold", arg)
        return f"OK – alerting on deal score ≥ {arg}/10"

    async def cmd_location(self, arg: str) -> str:
        if not arg:
            return f"Location: {html.escape(self.w.location or 'Facebook default')}. Set with <b>location sydney</b>"
        slug = arg.strip("/ ").lower().replace(" ", "")
        self.w.db.set("location", slug)
        self.w.db.set("center", "")  # the cached search centre belongs to the old location
        self.w.wake()
        return (f"📍 Location set to <b>{html.escape(slug)}</b>. "
                "Send <b>status</b> after a minute to confirm it's finding listings there.")

    async def cmd_judge(self, arg: str) -> str:
        if arg.lower() not in ("on", "off"):
            return (f"Judge is {'on' if self.w.judge_on else 'off'}. <b>judge on</b> = only alert on good deals; "
                    "<b>judge off</b> = send every new nearby listing.")
        self.w.db.set("judge", arg.lower())
        if arg.lower() == "on":
            return f"🧠 Judge on - alerting on deal score ≥ {self.w.threshold}/10, and on anything at half price or less."
        return "📣 Judge off - every new nearby listing under your max price will be sent, with the price meter."

    async def cmd_fresh(self, arg: str) -> str:
        """fresh 60 = only send listings posted in the last 60 minutes (also '2h')."""
        m = re.fullmatch(r"\s*(\d+)\s*(m|min|mins|minutes?|h|hr|hrs|hours?)?\s*", arg.lower())
        now = self.w.fresh_minutes
        if not m:
            return (f"I only send listings posted in the last <b>{now} minutes</b>. "
                    "Change it with e.g. <b>fresh 30</b> or <b>fresh 3h</b>.")
        mins = int(m.group(1)) * (60 if (m.group(2) or "").startswith("h") else 1)
        if not 5 <= mins <= 2880:
            return "Pick something between 5 minutes and 48 hours, e.g. <b>fresh 60</b>."
        self.w.db.set("fresh_minutes", mins)
        return (f"🕒 Only listings posted in the last <b>{mins} minutes</b> will be sent. "
                "Anything older is used to learn prices, never sent.")

    async def cmd_scams(self, arg: str) -> str:
        """scams = toggle; scams on / scams off."""
        want = arg.lower().strip() or ("off" if self.w.scams_on else "on")
        if want not in ("on", "off"):
            return "Send <b>scams</b> to switch probable-scam listings on or off."
        self.w.db.set("scams", want)
        if want == "on":
            return ("⚠️ Probable scams are <b>ON</b> - listings priced too good to be true arrive silently, "
                    "labelled, so you can decide.\nSend <b>scams</b> to hide them.")
        return ("🙈 Probable scams are <b>OFF</b> - listings priced too good to be true are hidden.\n"
                "Send <b>scams</b> to see them again.")

    async def cmd_far(self, arg: str) -> str:
        """far = toggle on/off; far on / far off; far 100 = on, out to 100 km."""
        db, current = self.w.db, self.w.far_radius
        num = re.sub(r"\b(km|away|deals?)\b", "", arg.lower()).strip()
        if not num:
            num = "off" if current else "on"   # bare "far" flips it
        if num in ("off", "0", "no", "stop"):
            if current:
                db.set("far_radius_last", current)   # remembered for the next "far on"
            db.set("far_radius", "0")
            return (f"🚗 Far-away deals are <b>OFF</b> - you'll only get listings within {self.w.radius} km.\n"
                    "Send <b>far</b> to turn them back on.")
        if num in ("on", "yes", "start"):
            num = db.get("far_radius_last") or str(config.far_radius_km or 60)
        if not num.isdigit() or not 1 <= int(num) <= 1000:
            return ("Send <b>far</b> to switch far-away deals on or off, or <b>far 100</b> to set how far "
                    f"(they are {'on, up to ' + str(current) + ' km' if current else 'off'} right now).")
        db.set("far_radius", num)
        db.set("far_radius_last", num)
        self.w.wake()
        return (f"🚗 Far-away deals are <b>ON</b> - exceptional ones ({config.far_min_score}/10 or better) from up to "
                f"{num} km away. Everything good within {self.w.radius} km still comes through as before.\n"
                "Send <b>far</b> to turn them off.")

    async def cmd_radius(self, arg: str) -> str:
        num = arg.lower().replace("km", "").strip()
        if not num.isdigit() or not 1 <= int(num) <= 500:
            return f"Radius: {self.w.radius} km. Set with e.g. <b>radius 20</b>"
        self.w.db.set("radius", num)
        self.w.wake()
        return f"📏 Radius set to {num} km"
