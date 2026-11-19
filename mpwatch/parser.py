"""Pull Marketplace listings out of Facebook's page data.

Facebook embeds listing data as JSON inside <script type="application/json"> tags on
first load, and sends more as GraphQL responses while scrolling. Both are parsed with the
same walker. Visible listing links are a last-resort fallback.
"""
import json
import re
from dataclasses import dataclass, field
from typing import Iterator, Optional

SCRIPT_RE = re.compile(r'<script type="application/json"[^>]*>(.*?)</script>', re.S)
PRICE_RE = re.compile(r"([^\d\s]{0,4})\s?(\d[\d,\.]*)")


@dataclass
class Listing:
    id: str
    title: str
    price: Optional[float]
    price_text: str
    photo: Optional[str] = None
    location: str = ""
    sold: bool = False
    description: str = ""
    photos: list[str] = field(default_factory=list)
    distance_km: Optional[float] = None  # from the search centre, when the town could be located
    far: bool = False                    # outside the normal radius, inside the wider ring
    created: Optional[float] = None      # when it was posted (unix time), from Facebook's own data
    seller_id: str = ""                  # Facebook's id for the seller (never shown to anyone)
    shipping_only: bool = False          # can't be collected in person
    was_price: Optional[float] = None    # the crossed-out price when the seller has already cut it
    seller_listings: int = 1             # listings of this item by the same seller in the same search
    duplicate_ads: int = 0               # other listings with word-for-word the same description

    def age_minutes(self, now: Optional[float] = None) -> Optional[float]:
        """Minutes since it was posted: from Facebook's timestamp, else from 'Listed 2 hours ago'."""
        import time as _time
        if self.created:
            return max(0.0, ((now or _time.time()) - self.created) / 60)
        ago = _ago_minutes(self.description)
        return ago

    @property
    def url(self) -> str:
        return f"https://www.facebook.com/marketplace/item/{self.id}/"


def clean_title(title: str) -> str:
    """One line, and 'Iphone+13+128gb' (how Facebook sometimes hands titles over) -> 'Iphone 13 128gb'."""
    t = " ".join((title or "").split())
    if "+" in t and " " not in t:
        t = " ".join(t.replace("+", " ").split())
    return t


def iter_dicts(obj) -> Iterator[dict]:
    """Iterate every dict inside a nested JSON structure (no recursion limit issues)."""
    stack = [obj]
    while stack:
        cur = stack.pop()
        if isinstance(cur, dict):
            yield cur
            stack.extend(cur.values())
        elif isinstance(cur, list):
            stack.extend(cur)


def json_blobs_from_html(html: str) -> Iterator[object]:
    for m in SCRIPT_RE.finditer(html):
        text = m.group(1)
        if "marketplace" not in text:
            continue
        try:
            yield json.loads(text)
        except json.JSONDecodeError:
            continue


def json_blobs_from_graphql(body: str) -> Iterator[object]:
    # GraphQL responses may be several JSON documents separated by newlines
    for line in body.splitlines():
        line = line.strip()
        if line.startswith("for (;;);"):
            line = line[len("for (;;);"):]
        if not line.startswith("{"):
            continue
        try:
            yield json.loads(line)
        except json.JSONDecodeError:
            continue


_AGO = re.compile(r"\bListed\s+(a|an|\d+)\s+(minute|hour|day|week|month|year)s?\s+ago", re.I)
_MIN = {"minute": 1, "hour": 60, "day": 1440, "week": 10080, "month": 43200, "year": 525600}


def _ago_minutes(text: str) -> Optional[float]:
    m = _AGO.search(text or "")
    if not m:
        return None
    n = 1 if m.group(1).lower() in ("a", "an") else int(m.group(1))
    return float(n * _MIN[m.group(2).lower()])


def ago_text(minutes: Optional[float]) -> str:
    """'just now', '12 min ago', '2 h ago', '3 days ago'."""
    if minutes is None:
        return ""
    if minutes < 2:
        return "just now"
    if minutes < 60:
        return f"{minutes:.0f} min ago"
    if minutes < 48 * 60:
        h = minutes / 60
        return f"{h:.0f} h ago" if h >= 1.5 else "1 h ago"
    return f"{minutes / 1440:.0f} days ago"


def pretty_price(text: str) -> str:
    """Facebook writes rand as 'ZAR8,500'; people write 'R8,500'. Other currencies are left as they are."""
    return re.sub(r"^\s*ZAR\s*", "R", (text or "").strip())


def listed_ago(description: str) -> str:
    """'2 hours ago' from the details block's 'Listed 2 hours ago in Pretoria', or '' if not there."""
    m = re.search(r"\bListed\s+(.{1,30}?\bago)\b", description or "", re.I)
    return " ".join(m.group(1).split()) if m else ""


def parse_price(text: str) -> Optional[float]:
    if not text:
        return None
    if text.strip().lower() in ("free", "gratis"):
        return 0.0
    m = PRICE_RE.search(text)
    if not m:
        return None
    try:
        return float(m.group(2).replace(",", ""))
    except ValueError:
        return None


def _listing_from_dict(d: dict) -> Optional[Listing]:
    lid = d.get("id")
    title = d.get("marketplace_listing_title")
    if not lid or not title or not str(lid).isdigit():
        return None
    price_obj = d.get("listing_price") or {}
    price_text = pretty_price(price_obj.get("formatted_amount") or "")
    price = None
    if price_obj.get("amount") is not None:
        try:
            price = float(price_obj["amount"])
        except (TypeError, ValueError):
            price = None
    if price is None:
        price = parse_price(price_text)
    photo = ((d.get("primary_listing_photo") or {}).get("image") or {}).get("uri")
    geo = (d.get("location") or {}).get("reverse_geocode") or {}
    location = ((geo.get("city_page") or {}).get("display_name")) or ", ".join(
        x for x in (geo.get("city"), geo.get("state")) if x
    )
    created = d.get("creation_time")
    delivery = [str(x).upper() for x in (d.get("delivery_types") or [])]
    was = (d.get("strikethrough_price") or {}).get("amount")
    try:
        was = float(was) if was is not None else None
    except (TypeError, ValueError):
        was = None
    return Listing(
        id=str(lid),
        title=clean_title(title),
        price=price,
        price_text=price_text or (f"{price:g}" if price is not None else "?"),
        photo=photo,
        location=location or "",
        sold=bool(d.get("is_sold") or d.get("is_pending")),
        created=float(created) if isinstance(created, (int, float)) and created > 1e9 else None,
        seller_id=str((d.get("marketplace_listing_seller") or {}).get("id") or ""),
        shipping_only=bool(delivery) and "IN_PERSON" not in delivery,
        was_price=was if was and price and was > price else None,
    )


def listings_from_blobs(blobs) -> dict[str, Listing]:
    found: dict[str, Listing] = {}
    for blob in blobs:
        for d in iter_dicts(blob):
            if "marketplace_listing_title" in d:
                lst = _listing_from_dict(d)
                old = found.get(lst.id) if lst else None
                if lst and (old is None or (lst.photo and not old.photo) or (lst.created and not old.created)):
                    if old:  # keep whatever the other copy knew
                        lst.photo = lst.photo or old.photo
                        lst.created = lst.created or old.created
                        lst.seller_id = lst.seller_id or old.seller_id
                    found[lst.id] = lst
    return found


def listings_from_anchors(anchors: list[dict]) -> dict[str, Listing]:
    """Fallback: anchors = [{"href": ..., "text": ..., "img": ...}] scraped from the DOM."""
    found: dict[str, Listing] = {}
    for a in anchors:
        m = re.search(r"/marketplace/item/(\d+)", a.get("href") or "")
        if not m:
            continue
        lines = [ln.strip() for ln in (a.get("text") or "").splitlines() if ln.strip()]
        if not lines:
            continue
        price_text = lines[0]
        title = lines[1] if len(lines) > 1 else lines[0]
        location = lines[2] if len(lines) > 2 else ""
        found[m.group(1)] = Listing(
            id=m.group(1),
            title=clean_title(title),
            price=parse_price(price_text),
            price_text=pretty_price(price_text),
            photo=a.get("img"),
            location=location,
        )
    return found


def item_details(html: str, listing_id: str) -> dict:
    """Extract description + photos from a listing page."""
    description, photos, sold = "", [], False
    for blob in json_blobs_from_html(html):
        for d in iter_dicts(blob):
            rd = d.get("redacted_description")
            if isinstance(rd, dict) and rd.get("text") and not description:
                description = rd["text"]
            lp = d.get("listing_photos")
            if isinstance(lp, list) and not photos:
                for p in lp:
                    uri = ((p or {}).get("image") or {}).get("uri")
                    if uri:
                        photos.append(uri)
            if str(d.get("id")) == listing_id and (d.get("is_sold") or d.get("is_pending")):
                sold = True
    if not description:
        m = re.search(r'<meta (?:name|property)="(?:og:)?description" content="([^"]*)"', html)
        if m:
            import html as _html
            description = _html.unescape(m.group(1))
    return {"description": description, "photos": photos[:6], "sold": sold}


RESERVED = {
    "item", "category", "you", "inbox", "notifications", "create", "profile", "saved",
    "search", "buying", "selling", "groups", "learn_more", "learn-more", "np", "stores", "marketplace",
}


def detect_location(hrefs: list[str]) -> Optional[str]:
    """Guess the user's Marketplace location slug from links on the Marketplace home page."""
    counts: dict[str, int] = {}
    for h in hrefs:
        m = re.search(r"/marketplace/([A-Za-z0-9_\-]+)/[A-Za-z]", h or "")
        if m and m.group(1).lower() not in RESERVED:
            counts[m.group(1)] = counts.get(m.group(1), 0) + 1
    return max(counts, key=counts.get) if counts else None


def visible_details(body_text: str, title: str) -> str:
    """The listing's visible info block (price, condition, description, seller join date), as text."""
    start = body_text.find(title) if title else -1
    if start < 0:
        return ""
    end = len(body_text)
    for marker in ("Related searches", "Today's picks", "Sponsored"):
        i = body_text.find(marker, start)
        if i != -1:
            end = min(end, i)
    block = "\n".join(ln.strip() for ln in body_text[start:end].splitlines() if ln.strip())
    for noise in ("Message\n", "Save\n", "Share\n", "Send seller a message\n"):
        block = block.replace(noise, "")
    return block[:2500]
