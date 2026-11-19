"""One-time Facebook login. Opens a real browser window; log in there and it finishes by itself.

Stop the watcher before running this (they share the same browser profile).
"""
import asyncio
import time

from mpwatch.browser import FBBrowser
from mpwatch.config import config
from mpwatch.db import DB

TIMEOUT_MIN = 15


async def logged_in(b: FBBrowser) -> bool:
    cookies = await b.ctx.cookies("https://www.facebook.com")
    return any(c["name"] == "c_user" for c in cookies)


async def main() -> None:
    b = FBBrowser(headless=False)
    await b.start()
    await b.page.goto("https://www.facebook.com/marketplace/")
    print("\nA browser window opened. Log into Facebook there (approve any security checks).", flush=True)
    print(f"I'll notice automatically once you're in (waiting up to {TIMEOUT_MIN} min)...", flush=True)

    deadline = time.time() + TIMEOUT_MIN * 60
    while not await logged_in(b):
        if time.time() > deadline:
            print("Timed out waiting for login. Run login.py again.", flush=True)
            await b.close()
            return
        await asyncio.sleep(3)

    print("Logged in! Letting Facebook settle...", flush=True)
    # Wait until any security checkpoint is cleared
    while "/checkpoint" in b.page.url or "/two_step" in b.page.url:
        if time.time() > deadline:
            break
        await asyncio.sleep(3)
    await asyncio.sleep(5)

    slug = DB().get("location") or config.location_id or await b.detect_location()
    if slug:
        DB().set("location", slug)
        print(f"Marketplace location detected: {slug}", flush=True)
    else:
        print("Couldn't detect your Marketplace location. Set LOCATION_ID in .env "
              "(the word after /marketplace/ in the URL when you browse your area).", flush=True)
    await b.close()
    print("Done. The browser window closes by itself.", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
