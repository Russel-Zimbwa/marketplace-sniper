"""The free judge: decides whether a listing is really the item and really a deal.

Layer 1 (always on, instant): deterministic rules in judge_rules.py. This alone decides what is
alerted, so an alert is never held up.

Layer 2 (optional, slow): if Ollama is running on this machine, a small local vision model looks at
the listing's photo and text *after* the alert has gone out, and its verdict is added to the same
Telegram message. It is also asked about listings the rules rejected without being sure (a vague
title at a great price): if it says "that is the item", the listing is alerted after all.
The model can add a warning or rescue a listing. It can never lower a score or hide a listing, and it
is never allowed to overrule the rules on which model something is (it is not reliable at that).

The paid Claude judge is kept in judge_claude.py. Set JUDGE=off in .env (or send "judge off" to the
bot) to get every nearby listing instead of only good deals.
"""
import asyncio
import base64
import json
import logging
import time
from typing import Optional

import httpx
from pydantic import BaseModel, Field

from . import judge_rules as rules
from .config import config
from .parser import Listing

log = logging.getLogger(__name__)


class Verdict(BaseModel):
    is_target_item: bool = Field(description="True only if this is actually the item the buyer wants.")
    condition: str = Field(description="Short condition note, e.g. 'used - like new, working', 'unknown'.")
    red_flags: list[str] = Field(description="Problem signals. Empty if none.")
    estimated_fair_price: Optional[float] = Field(description="Typical local price, or None if unknown.")
    deal_score: int = Field(description="1-10. 10 = exceptional bargain; 7 = good deal; 5 = normal; 1 = not the item.")
    summary: str = Field(description="One plain sentence for the phone notification.")
    likely_scam: bool = Field(default=False, description="Priced at half the usual or less, with the marks of "
                                                         "bait: sent quietly and labelled, never as a steal.")
    unsure: bool = Field(default=False, description="Rejected on a vague title, but it would be a deal if it "
                                                    "is the item: worth a second look by the local model.")


def enabled() -> bool:
    """True when the judge can run. The free judge needs no key; JUDGE=off in .env disables it."""
    return config.judge_mode != "off"


def describe() -> str:
    model = _ollama_cache[1]
    return "free rules" + (f" + photo check ({model})" if model else "")


def _verdict(res: rules.RuleResult, unsure: bool = False) -> Verdict:
    return Verdict(is_target_item=res.is_target, condition=res.condition, red_flags=res.red_flags,
                   estimated_fair_price=res.fair_price, deal_score=res.score, summary=res.summary, unsure=unsure,
                   likely_scam=res.likely_scam)


async def judge(listing: Listing, query: str, max_price: Optional[float],
                typical_price: Optional[float] = None) -> Optional[Verdict]:
    """Judge one listing with the rules. Instant. Returns None only if it truly cannot judge
    (the app then alerts anyway)."""
    try:
        ctx = context(listing)
        res = await asyncio.to_thread(
            rules.judge_listing, listing.title, listing.description, listing.price, listing.price_text,
            query, max_price, typical_price, False, None, ctx)
        unsure = False
        if not res.is_target and not res.target_certain:
            as_item = rules.judge_listing(listing.title, listing.description, listing.price, listing.price_text,
                                          query, max_price, typical_price, assume_target=True, ctx=ctx)
            unsure = as_item.is_target and as_item.score >= 6
        return _verdict(res, unsure)
    except Exception:
        log.exception("rule judge failed for %s", getattr(listing, "id", "?"))
        return None


def context(listing: Listing, stock_photo: bool = False) -> rules.Context:
    return rules.Context(age_minutes=listing.age_minutes(), shipping_only=getattr(listing, "shipping_only", False),
                         seller_listings=getattr(listing, "seller_listings", 1),
                         duplicate_ads=getattr(listing, "duplicate_ads", 0), stock_photo=stock_photo)


def rejudge_with_photo(listing: Listing, query: str, max_price: Optional[float], typical: Optional[float],
                       opinion: Optional[dict]) -> Optional[Verdict]:
    """After the photo check: if the picture is a catalogue image, judge again with that as one more doubt.
    Returns a new verdict only when that turns a deal into a probable scam."""
    if not opinion or str(opinion.get("photo") or "").lower() != "stock":
        return None
    try:
        res = rules.judge_listing(listing.title, listing.description, listing.price, listing.price_text, query,
                                  max_price, typical, ctx=context(listing, stock_photo=True))
    except Exception:
        log.exception("re-judge failed for %s", getattr(listing, "id", "?"))
        return None
    return _verdict(res) if res.is_target and res.likely_scam else None


def is_steal(listing: Listing, v: Verdict) -> bool:
    """Price at or below half the fair price - and a real price on a listing with no blocking problem."""
    fair = v.estimated_fair_price
    return bool(listing.price is not None and fair and listing.price <= 0.5 * fair and not v.likely_scam
                and v.deal_score >= 7 and not rules.placeholder_price(listing.price, fair))


# ---------- Layer 2: optional local vision model through Ollama ----------

_VISION_HINTS = ("gemma3", "llava", "vision", "moondream", "qwen2.5vl", "qwen3-vl", "minicpm")
_PREFERRED = ("gemma3:4b", "qwen2.5vl:3b", "llama3.2:3b", "qwen2.5:3b", "phi4-mini", "gemma3:1b")
_ollama_cache: list = [0.0, None]  # [checked_at, model name or None]
_model_lock: Optional[asyncio.Lock] = None  # one question at a time: the laptop has one CPU to give


async def _ollama_model() -> Optional[str]:
    """The model to use, or None when Ollama isn't running. Re-checked every 5 minutes."""
    if config.ollama_model.lower() == "off":
        return None
    if time.time() - _ollama_cache[0] < 300:
        return _ollama_cache[1]
    model = None
    try:
        async with httpx.AsyncClient(timeout=1.5) as http:
            r = await http.get(f"{config.ollama_url}/api/tags")
            names = [m.get("name", "") for m in r.json().get("models", [])] if r.status_code == 200 else []
        if config.ollama_model:
            model = config.ollama_model if any(n.startswith(config.ollama_model) for n in names) else None
        else:
            model = next((n for p in _PREFERRED for n in names if n.startswith(p)), names[0] if names else None)
    except (httpx.HTTPError, ValueError, OSError):
        model = None
    if model != _ollama_cache[1]:
        log.info("photo check: %s", model or "off (Ollama not running) - rules only")
    _ollama_cache[:] = [time.time(), model]
    return model


async def model_name() -> Optional[str]:
    """Name of the local model that will do photo checks, or None when there isn't one."""
    return await _ollama_model()


async def _first_photo_b64(listing: Listing) -> Optional[str]:
    url = (listing.photos or [listing.photo] or [None])[0]
    if not url:
        return None
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as http:
            r = await http.get(url)
            return base64.b64encode(r.content).decode() if r.status_code == 200 else None
    except httpx.HTTPError:
        return None


async def _ask_ollama(listing: Listing, query: str, model: str) -> Optional[dict]:
    img = await _first_photo_b64(listing) if any(h in model.lower() for h in _VISION_HINTS) else None
    prompt = (
        "You check second-hand listings for a buyer.\n"
        f"The buyer wants: {query}\n\n"
        f"Listing title: {listing.title}\nPrice: {listing.price_text}\n"
        f"Details: {(listing.description or '(none)')[:600]}\n\n"
        + ("The attached image is the listing's main photo.\n\n" if img else "")
        + "Answer these about the listing:\n"
        f"- is_item: is the thing for sale really a {query} - the item itself, not an accessory, game, "
        "empty box or a wanted ad?\n"
        "- photo: 'real' if the photo is the seller's own picture of the actual item; 'stock' if it is a "
        "catalogue, advert or screenshot image; 'different' if it shows some other product; 'none' if there "
        "is no photo.\n"
        "- condition: a few words about what you can see, e.g. sealed in box, like new, scratched, damaged, unknown.\n"
        "- reason: at most 12 words saying what the photo shows."
    )
    schema = {"type": "object", "required": ["is_item", "photo", "condition", "reason"], "properties": {
        "is_item": {"type": "boolean"}, "photo": {"type": "string", "enum": ["real", "stock", "different", "none"]},
        "condition": {"type": "string"}, "reason": {"type": "string"}}}
    body = {"model": model, "prompt": prompt, "format": schema, "stream": False, "keep_alive": "30m",
            "options": {"temperature": 0, "num_predict": 120}}
    if img:
        body["images"] = [img]
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(config.ollama_timeout, connect=2)) as http:
            r = await http.post(f"{config.ollama_url}/api/generate", json=body)
            if r.status_code != 200:
                return None
            out = json.loads(r.json().get("response", ""))
    except (httpx.HTTPError, ValueError):
        return None
    if not (isinstance(out, dict) and isinstance(out.get("is_item"), bool)):
        return None
    if not img:
        out["photo"] = "none"
    return out


async def second_look(listing: Listing, query: str) -> Optional[dict]:
    """Ask the local model about one listing (photo + text). Slow (tens of seconds on a laptop CPU), so
    it is called after the alert has been sent. Never raises; None when there is no model or no answer."""
    global _model_lock
    try:
        model = await _ollama_model()
        if not model:
            return None
        if _model_lock is None:
            _model_lock = asyncio.Lock()
        async with _model_lock:
            started = time.time()
            opinion = await _ask_ollama(listing, query, model)
        log.info("photo check %s on %s '%s': %s (%.0fs)", model, listing.id, listing.title[:40], opinion,
                 time.time() - started)
        return opinion
    except Exception:
        log.warning("photo check failed for %s", getattr(listing, "id", "?"), exc_info=True)
        return None


def rescue(listing: Listing, query: str, max_price: Optional[float], typical: Optional[float],
           opinion: Optional[dict]) -> Optional[Verdict]:
    """A listing the rules rejected on a vague title: if the model says it is the item, judge it as one.
    Returns None when there is nothing to rescue. Certain rejections (wanted ad, different model,
    accessory 'for' the item) are never overturned."""
    if not opinion or not opinion.get("is_item"):
        return None
    try:
        res = rules.judge_listing(listing.title, listing.description, listing.price, listing.price_text,
                                  query, max_price, typical, assume_target=True, ctx=context(listing))
    except Exception:
        log.exception("rescue failed for %s", getattr(listing, "id", "?"))
        return None
    if not res.is_target:
        return None
    res.score = min(res.score, 8)   # a guess confirmed by a small model is never presented as a sure steal
    return _verdict(res)


def photo_line(opinion: Optional[dict], query: str) -> tuple[str, bool]:
    """One line for the alert about what the model saw, and whether it is a warning. ('', False) = say nothing."""
    if not opinion:
        return "", False
    reason = str(opinion.get("reason") or "").strip().rstrip(".")[:90]
    reason = reason[:1].lower() + reason[1:] if reason else ""
    photo = str(opinion.get("photo") or "").lower()
    if not opinion.get("is_item"):
        return f"Photo check: may not be the {query}" + (f" - {reason}" if reason else ""), True
    if photo == "stock":
        return "Photo check: a catalogue or advert picture, not the seller's own - ask for a real photo", True
    if photo == "different":
        return "Photo check: the picture seems to show something else - look closely", True
    if photo != "real":
        return "", False
    return f"Photo looks right: {reason or 'shows the item'}", False
