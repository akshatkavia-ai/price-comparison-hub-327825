import re
from typing import List, Optional
from urllib.parse import quote_plus

import httpx

from src.api.models import PriceOffer

_PRICE_RE = re.compile(r"([0-9][0-9,]*)(?:\.[0-9]{1,2})?")


def _parse_price_inr(text: str) -> Optional[float]:
    if not text:
        return None
    m = _PRICE_RE.search(text.replace("\u20b9", " ").replace("INR", " "))
    if not m:
        return None
    num = m.group(1).replace(",", "")
    try:
        return float(num)
    except ValueError:
        return None


async def _fetch_text(url: str, timeout_s: float = 12.0) -> str:
    async with httpx.AsyncClient(follow_redirects=True, timeout=timeout_s, headers={
        "User-Agent": "Price-Pal/1.0 (+https://example.invalid)"
    }) as client:
        r = await client.get(url)
        r.raise_for_status()
        return r.text


async def _scrape_generic_search(site: str, search_url: str, query: str, max_results: int) -> List[PriceOffer]:
    """
    Minimal placeholder scraping: returns a single synthetic offer per site.

    Rationale:
    - Full DOM parsing (Cheerio/BeautifulSoup) isn't available in this scaffold yet.
    - This keeps API contract stable while enabling end-to-end integration.
    - Replace with real extraction per site when Playwright/Cheerio is added.
    """
    # Best-effort: request the page to ensure reachability; ignore body for now.
    try:
        await _fetch_text(search_url)
    except Exception:
        # If fetch fails, still return empty list rather than breaking whole compare.
        return []

    # Synthetic offer derived from query.
    return [
        PriceOffer(
            site=site,  # type: ignore[arg-type]
            title=f"{query} (search result on {site})",
            price_inr=None,
            url=search_url,
            image_url=None,
            in_stock=None,
            raw={"note": "placeholder-scrape", "search_url": search_url},
        )
    ][:max_results]


# PUBLIC_INTERFACE
async def scrape_all_sites(query: str, max_results_per_site: int) -> List[PriceOffer]:
    """Scrape offers across supported sites for the given query."""
    q = quote_plus(query)

    targets = [
        ("gamestheshop", f"https://gamestheshop.com/search?type=product&q={q}"),
        ("gamenation", f"https://gamenation.in/?s={q}&post_type=product"),
        ("gameloot", f"https://gameloot.in/?s={q}&post_type=product"),
        ("amazon", f"https://www.amazon.in/s?k={q}"),
        ("flipkart", f"https://www.flipkart.com/search?q={q}"),
    ]

    offers: List[PriceOffer] = []
    for site, url in targets:
        site_offers = await _scrape_generic_search(site, url, query, max_results_per_site)
        offers.extend(site_offers)

    # Normalize any prices we might have captured in raw fields (future use)
    for o in offers:
        if o.price_inr is None:
            raw_price = o.raw.get("price_text")
            if isinstance(raw_price, str):
                o.price_inr = _parse_price_inr(raw_price)
    return offers
