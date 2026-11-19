"""Free deal checks (no AI): learned typical price + warning words."""
import re
import statistics
import time
from typing import Optional

from . import judge_rules
from .db import DB
from .parser import Listing

WARN_WORDS = {
    "swap": r"\bswap", "trade": r"\btrad(e|ing)\b", "controller": r"\bcontroll?ers?\b",
    "case/cover": r"\b(case|cover|skin)s?\b", "box only": r"\b(box only|empty box)\b",
    "broken": r"\b(broken|faulty|cracked|damaged|not working|doesn'?t work|for parts|spares)\b",
    "wanted ad": r"\b(wanted|looking for|wtb|iso|buying)\b", "game only": r"\b(game|disc) only\b",
    "deposit/courier": r"\b(deposit|courier only|pudo)\b", "negotiable": r"\b(neg|negotiable|ono)\b",
    "not full price for all": r"\bnot .{0,25}for everything\b|\bask price\b",
}


def _words(query: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", query.lower())


def strict_match(query: str, title: str) -> bool:
    """Every query word appears as a whole word in the title (used only for price stats)."""
    t = set(re.findall(r"[a-z0-9]+", title.lower()))
    return all(w in t for w in _words(query))


def warnings(listing: Listing) -> list[str]:
    text = f"{listing.title}\n{listing.description}".lower()
    return [name for name, rx in WARN_WORDS.items() if re.search(rx, text)]


def record(db: DB, watch_id: int, listings: list[Listing], query: str) -> None:
    for l in listings:
        # Only real examples of the item teach the typical price: not accessories, other models,
        # wanted ads or placeholder prices
        if (l.price and not judge_rules.placeholder_price(l.price)
                and not judge_rules.swap_listing(l.title)
                and judge_rules.classify_target(l.title, "", query).is_target):
            db.conn.execute(
                "INSERT OR IGNORE INTO prices(watch_id,listing_id,price,seen_at) VALUES(?,?,?,?)",
                (watch_id, l.id, l.price, time.time()),
            )
    db.conn.commit()


def typical(db: DB, watch_id: int, days: int = 30) -> Optional[float]:
    rows = [r[0] for r in db.conn.execute(
        "SELECT price FROM prices WHERE watch_id=? AND seen_at>=?", (watch_id, time.time() - days * 86400))]
    if len(rows) < 4:
        return None
    med = statistics.median(rows)
    # drop joke prices at both ends (accessories, "R1", swap bait), then re-take the median
    # Drop joke prices at the top, and anything under ~half the median at the bottom: that is bait, scams
    # and broken goods, and leaving it in drags the "usual price" down until ordinary listings look like
    # bargains. Then re-take the median.
    rows = [p for p in rows if 0.55 * med <= p <= 2.5 * med] or rows
    return statistics.median(rows)


def samples(db: DB, watch_id: int, days: int = 30) -> int:
    return db.conn.execute("SELECT COUNT(*) FROM prices WHERE watch_id=? AND seen_at>=?",
                           (watch_id, time.time() - days * 86400)).fetchone()[0]


def meter(listing: Listing, typ: Optional[float]) -> str:
    if listing.price is None or not typ:
        return ""
    pct = round((1 - listing.price / typ) * 100)
    if pct >= 70:
        return f"❗ {pct}% below typical ({typ:,.0f}) – accessory, scam, or a real steal? Check it now"
    if pct >= 40:
        return f"🔥🔥 {pct}% below typical ({typ:,.0f}) – move fast!"
    if pct >= 15:
        return f"🔥 {pct}% below typical ({typ:,.0f})"
    if pct > -10:
        return f"About the typical price ({typ:,.0f})"
    return f"{-pct}% above typical ({typ:,.0f})"
