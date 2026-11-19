"""Quick check: python search_test.py "xbox series x" -> prints what the watcher sees."""
import asyncio
import sys

from mpwatch.browser import FBBrowser
from mpwatch.config import config
from mpwatch import geo
from mpwatch.db import DB


async def main(query: str) -> None:
    b = FBBrowser()
    await b.start()
    loc = DB().get("location") or config.location_id or None
    radius = int(DB().get("radius") or config.radius_km)
    print("URL:", b.search_url(query, loc, radius))
    res = await b.search(query, loc, radius)
    print("status:", res.status, res.detail)
    db = DB()
    print("search centre:", res.center)
    for l in res.listings[:30]:
        ok = await geo.within(db, l.location, res.center, radius)
        c = await geo.geocode(db, l.location) if l.location else None
        dist = f"{geo.km(res.center, c):5.1f} km" if (c and res.center) else "   ?    "
        print(f"{'KEEP' if ok else 'drop'} {dist}  {l.price_text:>10}  {l.title[:45]:<45}  {l.location}{'  [SOLD]' if l.sold else ''}")
    print(f"\n{len(res.listings)} listings")
    await b.close()


if __name__ == "__main__":
    asyncio.run(main(" ".join(sys.argv[1:]) or "xbox series x"))
