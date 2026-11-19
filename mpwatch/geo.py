"""Distance filter. Facebook's search leaks listings from well outside the radius, and listings
only carry a town name, so we geocode town names (cached) with OpenStreetMap and check distance."""
import asyncio
import json
import logging
import math
import re
import time
from typing import Optional

import httpx

from .db import DB

log = logging.getLogger(__name__)
CENTER_RE = re.compile(r'"buyLocation":\{"latitude":(-?[\d\.]+),"longitude":(-?[\d\.]+)')
_last_call = 0.0
SLACK_KM = 3  # town centres, not street addresses


def center_from_html(html: str) -> Optional[tuple[float, float]]:
    m = CENTER_RE.search(html)
    return (float(m.group(1)), float(m.group(2))) if m else None


def km(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(h))


async def geocode(db: DB, place: str) -> Optional[tuple[float, float]]:
    global _last_call
    key = "geo:" + place.lower().strip()
    cached = db.get(key)
    if cached is not None:
        return tuple(json.loads(cached)) if cached != "null" else None
    wait = 1.1 - (time.time() - _last_call)  # Nominatim policy: max 1 request/second
    if wait > 0:
        await asyncio.sleep(wait)
    _last_call = time.time()
    try:
        async with httpx.AsyncClient(timeout=10, headers={"User-Agent": "marketplace-sniper/1.0"}) as http:
            r = await http.get("https://nominatim.openstreetmap.org/search",
                               params={"q": place, "format": "json", "limit": 1})
            data = r.json() if r.status_code == 200 else None
    except (httpx.HTTPError, ValueError):
        return None  # don't cache failures; try again next time
    if data is None:
        return None
    coords = (float(data[0]["lat"]), float(data[0]["lon"])) if data else None
    db.set(key, json.dumps(coords))
    return coords


async def distance(db: DB, place: str, center: Optional[tuple[float, float]]) -> Optional[float]:
    """Km from the search centre to the listing's town, or None when it can't be worked out."""
    if not center or not place:
        return None
    coords = await geocode(db, place)
    return km(center, coords) if coords else None


async def within(db: DB, place: str, center: Optional[tuple[float, float]], radius_km: float) -> bool:
    """True if the place is within radius (city-level, so allow a few km slack). Unknown -> True."""
    d = await distance(db, place, center)
    return d is None or d <= radius_km + SLACK_KM
