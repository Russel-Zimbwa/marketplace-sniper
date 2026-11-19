import asyncio
import logging
import random
import re
import sys
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urlencode

from playwright.async_api import BrowserContext, Page, async_playwright

from . import geo, parser
from .config import config
from .parser import Listing

log = logging.getLogger(__name__)


@dataclass
class SearchResult:
    status: str  # "ok" | "logged_out" | "error"
    listings: list[Listing] = field(default_factory=list)
    detail: str = ""
    center: Optional[tuple] = None  # (lat, lon) of the Marketplace search location


class FBBrowser:
    def __init__(self, headless: bool = config.headless):
        self.headless = headless
        self.pw = None
        self.ctx: Optional[BrowserContext] = None
        self.page: Optional[Page] = None
        self.lock = asyncio.Lock()  # one page, one action at a time
        self._capture: Optional[list] = None
        self.public_mode = False  # True when browsing Marketplace without being logged in

    async def start(self) -> None:
        self.pw = await async_playwright().start()
        # Use Playwright's own Chromium when it works; on some Windows machines it fails to
        # start, so fall back to an installed Chrome or Edge (BROWSER_CHANNEL in .env forces one).
        channels = [config.browser_channel] if config.browser_channel else ["chromium", "chrome", "msedge"]
        channel, version, last_err = None, "", None
        for cand in channels:
            try:
                tmp = await self.pw.chromium.launch(headless=True, channel=cand)
                version = tmp.version
                await tmp.close()
                channel = cand
                break
            except Exception as e:  # noqa: BLE001
                last_err = e
                log.warning("browser '%s' could not start: %s", cand, str(e).splitlines()[0])
        if channel is None:
            raise RuntimeError(f"No usable browser found (tried {', '.join(channels)}). "
                               "Install Google Chrome, or run: python -m playwright install chromium") from last_err
        log.info("using browser '%s' %s", channel, version)
        ua = None
        if self.headless:
            # Headless Chromium announces itself as "HeadlessChrome"; present a normal UA instead.
            platform = (
                "Windows NT 10.0; Win64; x64" if sys.platform.startswith("win")
                else "Macintosh; Intel Mac OS X 10_15_7" if sys.platform == "darwin"
                else "X11; Linux x86_64"
            )
            ua = (f"Mozilla/5.0 ({platform}) AppleWebKit/537.36 (KHTML, like Gecko) "
                  f"Chrome/{version.split('.')[0]}.0.0.0 Safari/537.36")
        config.profile_dir.mkdir(parents=True, exist_ok=True)
        self.ctx = await self.pw.chromium.launch_persistent_context(
            str(config.profile_dir),
            headless=self.headless,
            channel=channel,  # a full browser (new headless) so it can read the login cookies
            user_agent=ua,
            viewport={"width": 1280, "height": 900},
            args=["--disable-blink-features=AutomationControlled"],
        )
        self.page = self.ctx.pages[0] if self.ctx.pages else await self.ctx.new_page()
        self.page.on("response", self._on_response)

    async def close(self) -> None:
        if self.ctx:
            await self.ctx.close()
        if self.pw:
            await self.pw.stop()

    def _on_response(self, response) -> None:
        if self._capture is not None and "/api/graphql" in response.url:
            self._capture.append(asyncio.ensure_future(self._safe_text(response)))

    @staticmethod
    async def _safe_text(response) -> str:
        try:
            return await response.text()
        except Exception:
            return ""

    async def _logged_out(self) -> bool:
        url = self.page.url
        if "/login" in url or "/checkpoint" in url:
            return True
        return await self.page.locator('input[name="email"], input[name="pass"]').count() > 0

    def search_url(self, query: str, location: Optional[str], radius: Optional[int] = None,
                   days: Optional[int] = 1, newest: bool = True) -> str:
        params = {"query": query, "radius": radius or config.radius_km, "exact": "false"}
        if newest:
            params["sortBy"] = "creation_time_descend"
        if days:
            params["daysSinceListed"] = days
        base = f"https://www.facebook.com/marketplace/{location}/search" if location else \
            "https://www.facebook.com/marketplace/search"
        return f"{base}?{urlencode(params)}"

    async def search(self, query: str, location: Optional[str], radius: Optional[int] = None,
                     days: Optional[int] = 1, newest: bool = True, scrolls: int = 1) -> SearchResult:
        """days=1/newest=True is the regular 'what is new' check. days=None/newest=False/scrolls=3 is the
        market survey done once when a watch starts: everything currently for sale, to learn prices."""
        async with self.lock:
            self._capture = []
            try:
                await self.page.goto(self.search_url(query, location, radius, days, newest), wait_until="domcontentloaded", timeout=45000)
                if "/checkpoint" in self.page.url or "/login" in self.page.url:
                    return SearchResult("logged_out", detail=self.page.url)
                try:
                    await self.page.wait_for_selector('a[href*="/marketplace/item/"]', timeout=15000)
                except Exception:
                    # No listings. Logged-out pages always carry a login form, so that alone proves
                    # nothing: a search with no results in the last 24h still shows the Marketplace
                    # search page (with its location data). Only a page without it is a login wall.
                    # A slow page looks the same for a few seconds, so look again (and reload once) first.
                    empty_ok = False
                    for attempt in range(4):
                        if "/marketplace/" in self.page.url and \
                                geo.center_from_html(await self.page.content()) is not None:
                            empty_ok = True
                            break
                        if attempt == 1:
                            try:
                                await self.page.reload(wait_until="domcontentloaded", timeout=30000)
                            except Exception:  # noqa: BLE001
                                pass
                        await asyncio.sleep(4)
                    if not empty_ok and await self._logged_out():
                        return SearchResult("logged_out", detail=self.page.url)
                self.public_mode = await self._logged_out()
                await asyncio.sleep(random.uniform(1.5, 3.0))
                for _ in range(max(1, scrolls)):
                    await self.page.mouse.wheel(0, random.randint(600, 1200))
                    await asyncio.sleep(random.uniform(1.5, 2.5))

                html = await self.page.content()
                bodies = [await t for t in self._capture]
                blobs = list(parser.json_blobs_from_html(html))
                for b in bodies:
                    blobs.extend(parser.json_blobs_from_graphql(b))
                found = parser.listings_from_blobs(blobs)

                anchors = await self.page.eval_on_selector_all(
                    'a[href*="/marketplace/item/"]',
                    "els => els.map(a => ({href: a.getAttribute('href'), text: a.innerText,"
                    " img: (a.querySelector('img') || {}).src || null}))",
                )
                for lid, lst in parser.listings_from_anchors(anchors).items():
                    found.setdefault(lid, lst)

                # keep page order (newest first) where we can
                order = [m for a in anchors for m in [re.search(r"/item/(\d+)", a["href"] or "")] if m]
                ordered_ids = list(dict.fromkeys([m.group(1) for m in order] + list(found)))
                return SearchResult("ok", [found[i] for i in ordered_ids if i in found],
                                    center=geo.center_from_html(html))
            except Exception as e:  # noqa: BLE001
                log.exception("search failed")
                return SearchResult("error", detail=str(e))
            finally:
                self._capture = None

    async def details(self, listing: Listing) -> Listing:
        async with self.lock:
            try:
                await self.page.goto(listing.url, wait_until="domcontentloaded", timeout=45000)
                await asyncio.sleep(random.uniform(1.5, 3.0))
                info = parser.item_details(await self.page.content(), listing.id)
                visible = parser.visible_details(await self.page.inner_text("body"), listing.title)
                listing.description = "\n\n".join(x for x in (info["description"], visible) if x)
                big_imgs = await self.page.eval_on_selector_all(
                    "img[src*='scontent']",
                    "els => els.filter(i => i.naturalWidth >= 300 && i.naturalHeight >= 300).map(i => i.src)",
                )
                photos = list(dict.fromkeys(info["photos"] + big_imgs))
                listing.photos = photos or ([listing.photo] if listing.photo else [])
                listing.sold = listing.sold or info["sold"]
            except Exception:
                log.exception("detail fetch failed for %s", listing.id)
                listing.photos = [listing.photo] if listing.photo else []
            return listing

    async def detect_location(self) -> Optional[str]:
        async with self.lock:
            await self.page.goto("https://www.facebook.com/marketplace/", wait_until="domcontentloaded")
            await asyncio.sleep(4)
            if "/marketplace/" not in self.page.url:
                return None
            # The URL may already include the location
            slug = parser.detect_location([self.page.url])
            if slug:
                return slug
            hrefs = await self.page.eval_on_selector_all("a[href*='/marketplace/']", "els => els.map(a => a.href)")
            return parser.detect_location(hrefs)
