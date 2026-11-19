"""Ask Claude whether a listing is a genuinely great deal."""
import asyncio
import base64
import logging
from typing import Optional

import anthropic
import httpx
from pydantic import BaseModel, Field

from .config import config
from .parser import Listing

log = logging.getLogger(__name__)


class Verdict(BaseModel):
    is_target_item: bool = Field(description="True only if this is actually the item the buyer wants (right model/variant, the item itself, not just a box, accessory, game, or 'wanted' post).")
    condition: str = Field(description="Short condition note, e.g. 'working, lightly used', 'unknown', 'faulty / for parts'.")
    red_flags: list[str] = Field(description="Scam or problem signals: trade only, shipping only, too-good-to-be-true with stock photos, broken, banned console, missing parts, etc. Empty if none.")
    estimated_fair_price: Optional[float] = Field(description="Typical local second-hand price for this item in the listing's currency, or null if unsure.")
    deal_score: int = Field(description="1-10. 10 = exceptional bargain worth dropping everything for; 7 = clearly good deal; 5 = normal market price; 1 = overpriced or not the item.")
    summary: str = Field(description="One punchy sentence for the buyer's phone notification explaining why it is or isn't a deal.")


SYSTEM = (
    "You are a sharp second-hand marketplace deal hunter helping one buyer snap up bargains on "
    "Facebook Marketplace before anyone else. Judge each listing quickly and honestly. Be strict: "
    "the buyer only wants alerts for listings that are really the item they want and are priced well "
    "below normal used prices, from sellers that look legitimate. Consider the photos, title, "
    "description and price together.\n\n"
    "IMPORTANT - never miss a genuine steal: people regularly underprice things because they are "
    "moving, decluttering, don't know the value, or need cash fast. A shockingly low price is NOT "
    "on its own a reason to call it a scam or lower the score. If it really is the item and the "
    "price is far below market, give it a high deal_score (9-10) and list any concerns in red_flags "
    "instead. Only lower the score for concrete problems: clearly not the item, broken/for parts "
    "(unless the buyer asked for that), trade-only, shipping/deposit-only, or obvious scam patterns "
    "(e.g. asks to move to WhatsApp/email before viewing, stock photos plus brand-new account wording). "
    "When unsure whether it is the right item, lean towards is_target_item=true and explain in the summary."
)

_client: Optional[anthropic.AsyncAnthropic] = None


def enabled() -> bool:
    return bool(config.anthropic_key)


def _get_client() -> anthropic.AsyncAnthropic:
    global _client
    if _client is None:
        _client = anthropic.AsyncAnthropic(api_key=config.anthropic_key)
    return _client


async def _image_blocks(urls: list[str]) -> list[dict]:
    blocks = []
    async with httpx.AsyncClient(timeout=15, follow_redirects=True) as http:
        for url in urls[:3]:
            try:
                r = await http.get(url)
                ctype = r.headers.get("content-type", "image/jpeg").split(";")[0]
                if r.status_code != 200 or ctype not in ("image/jpeg", "image/png", "image/webp", "image/gif"):
                    continue
                blocks.append({
                    "type": "image",
                    "source": {"type": "base64", "media_type": ctype, "data": base64.b64encode(r.content).decode()},
                })
            except httpx.HTTPError:
                continue
    return blocks


async def judge(listing: Listing, query: str, max_price: Optional[float]) -> Optional[Verdict]:
    """Returns None if Claude couldn't judge it after retries (API error / refusal)."""
    for attempt in range(3):
        v = await _judge_once(listing, query, max_price)
        if v is not None:
            return v
        await asyncio.sleep(2 * (attempt + 1))
    return None


async def _judge_once(listing: Listing, query: str, max_price: Optional[float]) -> Optional[Verdict]:
    text = (
        f"The buyer is looking for: {query}\n"
        f"Buyer's maximum price: {max_price if max_price is not None else 'not set - judge against market value'}\n\n"
        f"LISTING\nTitle: {listing.title}\nPrice: {listing.price_text}\n"
        f"Location: {listing.location or 'unknown'}\n"
        f"Description:\n{listing.description or '(none)'}\n"
    )
    content = await _image_blocks(listing.photos or ([listing.photo] if listing.photo else []))
    content.append({"type": "text", "text": text})
    try:
        resp = await _get_client().messages.parse(
            model=config.claude_model,
            max_tokens=8000,
            system=SYSTEM,
            messages=[{"role": "user", "content": content}],
            output_format=Verdict,
            output_config={"effort": "high"},
        )
    except anthropic.RateLimitError:
        log.warning("Claude rate limited")
        return None
    except anthropic.APIStatusError as e:
        log.error("Claude API error %s: %s", e.status_code, e.message)
        return None
    except anthropic.APIConnectionError:
        log.warning("Claude connection error")
        return None
    if resp.stop_reason == "refusal" or resp.parsed_output is None:
        log.warning("Claude did not judge listing %s (stop_reason=%s)", listing.id, resp.stop_reason)
        return None
    v = resp.parsed_output
    v.deal_score = max(1, min(10, v.deal_score))
    return v


def is_steal(listing: Listing, v: Verdict) -> bool:
    """Price at or below half of Claude's own fair-price estimate."""
    return bool(listing.price is not None and v.estimated_fair_price
                and listing.price <= 0.5 * v.estimated_fair_price)
