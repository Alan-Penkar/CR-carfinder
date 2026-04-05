"""
Scrape used SUV listings from multiple sources.

Sources tried in order:
1. MarketCheck via RapidAPI (best structured data, requires API key)
2. AutoTrader internal API (free, no key needed)
3. CarGurus internal API (free, no key needed)
"""

import json
import re
import time

import requests
from bs4 import BeautifulSoup
from config import SEARCH_PARAMS


def _create_session():
    """Create a requests session with no proxy (bypass any env proxy)."""
    session = requests.Session()
    session.trust_env = False  # Ignore HTTP_PROXY/HTTPS_PROXY env vars
    return session


# ---------------------------------------------------------------------------
# AutoTrader
# ---------------------------------------------------------------------------

def fetch_listings_autotrader():
    """
    Fetch used SUV listings from AutoTrader's internal search API.
    The key is matching the exact headers their frontend sends.
    """
    session = _create_session()

    # Headers must closely match a real browser or AT returns HTML/403
    search_url = (
        f"https://www.autotrader.com/cars-for-sale/used-cars/suv/"
        f"atlanta-ga-{SEARCH_PARAMS['zip_code']}"
    )
    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/125.0.0.0 Safari/537.36"
        ),
        "accept": "*/*",
        "accept-encoding": "gzip, deflate, br",
        "accept-language": "en-US,en;q=0.9",
        "Cache-Control": "no-cache",
        "referer": search_url,
    })

    all_listings = []
    first_record = 0
    page_size = 100
    max_results = SEARCH_PARAMS["max_results"]

    print(f"[autotrader] Searching for used SUVs near {SEARCH_PARAMS['zip_code']}...")

    while first_record < max_results:
        params = {
            "zip": SEARCH_PARAMS["zip_code"],
            "searchRadius": SEARCH_PARAMS["radius_miles"],
            "vehicleStyleCodes": "SUVCROSS",
            "startYear": SEARCH_PARAMS["year_min"],
            "endYear": SEARCH_PARAMS["year_max"],
            "listingTypes": "USED",
            "sortBy": "relevance",
            "numRecords": page_size,
            "firstRecord": first_record,
            "channel": "ATC",
        }

        try:
            resp = session.get(
                "https://www.autotrader.com/rest/searchresults/base",
                params=params,
                timeout=30,
            )
            resp.raise_for_status()

            # Check if response is JSON (AT sometimes returns HTML on bot detection)
            content_type = resp.headers.get("Content-Type", "")
            if "json" not in content_type and "javascript" not in content_type:
                # Try parsing anyway - sometimes CT header is wrong
                try:
                    data = resp.json()
                except ValueError:
                    print(f"[autotrader] Got non-JSON response (likely bot detection). "
                          f"Content-Type: {content_type}")
                    break
            else:
                data = resp.json()

        except requests.exceptions.HTTPError as e:
            print(f"[autotrader] HTTP {resp.status_code} at offset {first_record}")
            break
        except (requests.exceptions.RequestException, ValueError) as e:
            print(f"[autotrader] Error at offset {first_record}: {e}")
            break

        listings = data.get("listings", [])
        if not listings:
            if first_record == 0:
                print(f"[autotrader] No listings in response. Keys: {list(data.keys())}")
            break

        total_count = data.get("totalResultCount", 0)
        for raw in listings:
            parsed = _parse_autotrader_listing(raw)
            if parsed["make"] and parsed["model"]:
                all_listings.append(parsed)

        print(f"[autotrader] Fetched {len(all_listings)} / {min(total_count, max_results)}")

        first_record += page_size
        if first_record >= total_count:
            break

        time.sleep(2.0)

    print(f"[autotrader] Done. Total: {len(all_listings)}")
    return all_listings


def _parse_autotrader_listing(raw):
    """Extract fields from an AutoTrader listing dict."""
    specs = raw.get("specifications", {})
    pricing = raw.get("pricingDetail", {})
    dealer = raw.get("owner", {})
    location = dealer.get("location", {})

    def spec_val(key):
        v = specs.get(key, {})
        return v.get("value", "") if isinstance(v, dict) else v

    return {
        "make": raw.get("makeString", ""),
        "model": raw.get("modelString", ""),
        "year": raw.get("year", ""),
        "trim": raw.get("trimString", ""),
        "price": pricing.get("primary", pricing.get("salePrice", "")),
        "mileage": spec_val("mileage"),
        "exterior_color": raw.get("exteriorColorSimple", ""),
        "interior_color": raw.get("interiorColorSimple", ""),
        "engine": spec_val("engine"),
        "transmission": spec_val("transmission"),
        "drivetrain": spec_val("drivetrain"),
        "fuel_type": spec_val("fuelType"),
        "mpg_city": spec_val("mpgCity"),
        "mpg_highway": spec_val("mpgHighway"),
        "vin": raw.get("vin", ""),
        "stock_number": raw.get("stockNumber", ""),
        "seller_name": dealer.get("name", ""),
        "seller_type": raw.get("ownerType", ""),
        "seller_city": location.get("city", ""),
        "seller_state": location.get("state", ""),
        "seller_distance_mi": raw.get("distanceFromSearch", ""),
        "listing_url": (
            f"https://www.autotrader.com/cars-for-sale/vehicledetails.xhtml"
            f"?listingId={raw.get('id', '')}"
        ),
        "days_on_market": raw.get("daysOnMarket", ""),
        "accidents_reported": raw.get("accidentCount", ""),
        "owner_count": raw.get("ownerCount", ""),
    }


# ---------------------------------------------------------------------------
# CarGurus
# ---------------------------------------------------------------------------

def fetch_listings_cargurus():
    """
    Fetch used SUV listings from CarGurus.
    Uses their inventory listing page and extracts embedded JSON data.
    """
    print("[cargurus] Searching for used SUVs...")
    session = _create_session()

    # CarGurus needs full browser-like headers to avoid 406
    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/125.0.0.0 Safari/537.36"
        ),
        "Accept": (
            "text/html,application/xhtml+xml,application/xml;q=0.9,"
            "image/avif,image/webp,image/apng,*/*;q=0.8"
        ),
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Cache-Control": "no-cache",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Upgrade-Insecure-Requests": "1",
    })

    all_listings = []
    offset = 0
    page_size = 15  # CG returns ~15 per page in HTML

    while offset < SEARCH_PARAMS["max_results"]:
        # Use the inventory listing page URL
        url = (
            "https://www.cargurus.com/Cars/inventorylisting/"
            "viewDetailsFilterViewInventoryListing.action"
        )
        params = {
            "zip": SEARCH_PARAMS["zip_code"],
            "inventorySearchWidgetType": "AUTO",
            "bodyTypeGroup": "bg_suv_702",
            "startYear": SEARCH_PARAMS["year_min"],
            "endYear": SEARCH_PARAMS["year_max"],
            "distance": SEARCH_PARAMS["radius_miles"],
            "sortDir": "ASC",
            "sortType": "DEAL_SCORE",
            "offset": offset,
            "maxResults": page_size,
            "filtersModified": "true",
        }

        try:
            resp = session.get(url, params=params, timeout=30)
            resp.raise_for_status()
        except requests.exceptions.HTTPError as e:
            print(f"[cargurus] HTTP {resp.status_code} at offset {offset}")
            # Try alternative URL pattern
            if offset == 0:
                alt = _fetch_cargurus_alt(session)
                if alt:
                    return alt
            break
        except requests.exceptions.RequestException as e:
            print(f"[cargurus] Error at offset {offset}: {e}")
            break

        # Parse embedded JSON from the HTML
        page_listings = _extract_cargurus_listings(resp.text)

        if not page_listings:
            if offset == 0:
                print("[cargurus] No listings found in page HTML")
                # Try alternative approach
                alt = _fetch_cargurus_alt(session)
                if alt:
                    return alt
            break

        all_listings.extend(page_listings)
        print(f"[cargurus] Fetched {len(all_listings)} listings so far")

        if len(page_listings) < page_size:
            break

        offset += page_size
        time.sleep(2.0)

    print(f"[cargurus] Done. Total: {len(all_listings)}")
    return all_listings


def _fetch_cargurus_alt(session):
    """
    Alternative CarGurus approach: use their search results page
    which sometimes has a different anti-bot policy.
    """
    print("[cargurus] Trying alternative URL pattern...")

    url = "https://www.cargurus.com/Cars/l-Used-SUV_702-Atlanta_L14607"
    try:
        resp = session.get(url, timeout=30)
        resp.raise_for_status()
        listings = _extract_cargurus_listings(resp.text)
        if listings:
            print(f"[cargurus] Alt found {len(listings)} listings")
            return listings
    except requests.exceptions.RequestException as e:
        print(f"[cargurus] Alt approach failed: {e}")

    return []


def _extract_cargurus_listings(html):
    """Extract listing data from CarGurus HTML page."""
    listings = []
    soup = BeautifulSoup(html, "lxml")

    # Method 1: Look for JSON-LD structured data
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
            if isinstance(data, list):
                for item in data:
                    if item.get("@type") == "Car" or item.get("@type") == "Vehicle":
                        listings.append(_parse_jsonld_car(item))
            elif isinstance(data, dict) and data.get("@type") in ("Car", "Vehicle"):
                listings.append(_parse_jsonld_car(data))
        except (json.JSONDecodeError, KeyError):
            continue

    if listings:
        return listings

    # Method 2: Look for embedded JS data (window.__PREFLIGHT_DATA__ etc.)
    for script in soup.find_all("script"):
        text = script.string or ""

        # Try various embedded data patterns
        for pattern in [
            r'window\.__PREFLIGHT_DATA__\s*=\s*({.*?});',
            r'window\.__NEXT_DATA__\s*=\s*({.*?});',
            r'"results"\s*:\s*(\[.*?\])\s*[,}]',
            r'"listings"\s*:\s*(\[.*?\])\s*[,}]',
        ]:
            match = re.search(pattern, text, re.DOTALL)
            if match:
                try:
                    data = json.loads(match.group(1))
                    if isinstance(data, list):
                        for item in data:
                            listing = _parse_cargurus_item(item)
                            if listing:
                                listings.append(listing)
                    elif isinstance(data, dict):
                        # Nested structure
                        for key in ("results", "listings", "inventory"):
                            items = data.get(key, [])
                            if isinstance(items, list):
                                for item in items:
                                    listing = _parse_cargurus_item(item)
                                    if listing:
                                        listings.append(listing)
                    if listings:
                        return listings
                except (json.JSONDecodeError, ValueError):
                    continue

    return listings


def _parse_jsonld_car(item):
    """Parse a JSON-LD Car/Vehicle schema into our listing format."""
    offers = item.get("offers", {})
    seller = offers.get("seller", item.get("seller", {}))
    return {
        "make": item.get("brand", {}).get("name", item.get("manufacturer", "")),
        "model": item.get("model", ""),
        "year": item.get("vehicleModelDate", item.get("productionDate", "")),
        "trim": item.get("vehicleConfiguration", ""),
        "price": offers.get("price", ""),
        "mileage": item.get("mileageFromOdometer", {}).get("value", ""),
        "exterior_color": item.get("color", ""),
        "interior_color": item.get("vehicleInteriorColor", ""),
        "engine": item.get("vehicleEngine", {}).get("name", "") if isinstance(item.get("vehicleEngine"), dict) else "",
        "transmission": item.get("vehicleTransmission", ""),
        "drivetrain": item.get("driveWheelConfiguration", ""),
        "fuel_type": item.get("fuelType", ""),
        "mpg_city": "",
        "mpg_highway": "",
        "vin": item.get("vehicleIdentificationNumber", ""),
        "stock_number": "",
        "seller_name": seller.get("name", "") if isinstance(seller, dict) else "",
        "seller_type": seller.get("@type", "") if isinstance(seller, dict) else "",
        "seller_city": "",
        "seller_state": "",
        "seller_distance_mi": "",
        "listing_url": item.get("url", offers.get("url", "")),
        "days_on_market": "",
        "accidents_reported": "",
        "owner_count": "",
    }


def _parse_cargurus_item(item):
    """Parse a CarGurus listing item from embedded JS data."""
    if not isinstance(item, dict):
        return None

    make = item.get("makeName", item.get("make", ""))
    model = item.get("modelName", item.get("model", ""))
    if not make or not model:
        return None

    return {
        "make": make,
        "model": model,
        "year": item.get("carYear", item.get("year", "")),
        "trim": item.get("trimName", item.get("trim", "")),
        "price": item.get("price", item.get("expectedPrice", item.get("listPrice", ""))),
        "mileage": item.get("mileage", item.get("mileageString", "")),
        "exterior_color": item.get("exteriorColorName", item.get("exteriorColor", "")),
        "interior_color": item.get("interiorColorName", item.get("interiorColor", "")),
        "engine": item.get("engine", ""),
        "transmission": item.get("transmission", ""),
        "drivetrain": item.get("driveTrain", item.get("driveType", "")),
        "fuel_type": item.get("fuelType", ""),
        "mpg_city": item.get("cityFuelEconomy", ""),
        "mpg_highway": item.get("highwayFuelEconomy", ""),
        "vin": item.get("vin", ""),
        "stock_number": item.get("stockNumber", ""),
        "seller_name": item.get("dealerName", item.get("sellerName", "")),
        "seller_type": "dealer" if item.get("dealerName") else "private",
        "seller_city": item.get("dealerCity", item.get("sellerCity", "")),
        "seller_state": item.get("dealerState", item.get("sellerState", "")),
        "seller_distance_mi": item.get("distanceFromSearchZip", item.get("distance", "")),
        "listing_url": (
            f"https://www.cargurus.com{item['url']}"
            if item.get("url", "").startswith("/") else item.get("url", "")
        ),
        "days_on_market": item.get("daysOnMarket", ""),
        "accidents_reported": "",
        "owner_count": "",
        "deal_rating": item.get("dealRating", item.get("dealScore", "")),
    }


# ---------------------------------------------------------------------------
# MarketCheck via RapidAPI
# ---------------------------------------------------------------------------

# Known RapidAPI host variants for MarketCheck
_MC_HOSTS = [
    "marketcheck-prod.apigee.net",
    "cars-search.p.rapidapi.com",
    "marketcheck.p.rapidapi.com",
]


def fetch_listings_marketcheck():
    """
    Fetch used SUV listings via MarketCheck API on RapidAPI.
    Tries multiple known host variants.
    """
    from config import RAPIDAPI_KEY

    if not RAPIDAPI_KEY:
        print("[marketcheck] No RapidAPI key configured, skipping")
        return []

    session = _create_session()
    years = ",".join(str(y) for y in range(
        SEARCH_PARAMS["year_min"], SEARCH_PARAMS["year_max"] + 1
    ))

    print("[marketcheck] Querying MarketCheck API for used SUVs...")

    for host in _MC_HOSTS:
        print(f"[marketcheck] Trying host: {host}")
        result = _try_marketcheck_host(session, host, RAPIDAPI_KEY, years)
        if result:
            return result

    print("[marketcheck] All host variants failed")
    return []


def _try_marketcheck_host(session, host, api_key, years):
    """Try fetching from a specific MarketCheck host."""
    all_listings = []
    page_size = 50
    start = 0
    max_results = SEARCH_PARAMS["max_results"]

    while start < max_results:
        params = {
            "car_type": "used",
            "body_type": "SUV",
            "year": years,
            "zip": SEARCH_PARAMS["zip_code"],
            "radius": SEARCH_PARAMS["radius_miles"],
            "rows": page_size,
            "start": start,
            "sort_by": "price",
            "sort_order": "asc",
        }

        headers = {
            "X-RapidAPI-Key": api_key,
            "X-RapidAPI-Host": host,
        }

        try:
            # Use the host in the URL
            url = f"https://{host}/v2/search/car/active"
            resp = session.get(url, headers=headers, params=params, timeout=30)

            if resp.status_code in (401, 403, 404):
                print(f"[marketcheck] {host} returned {resp.status_code}")
                return []  # Wrong host, try next

            resp.raise_for_status()
            data = resp.json()
        except (requests.exceptions.RequestException, ValueError) as e:
            print(f"[marketcheck] {host} error: {e}")
            return []

        listings = data.get("listings", [])
        if not listings and start == 0:
            # Might be wrong response format
            if "error" in data or "message" in data:
                print(f"[marketcheck] API error: {data.get('error', data.get('message', ''))}")
            return []

        if not listings:
            break

        total = data.get("num_found", 0)

        for item in listings:
            dealer = item.get("dealer", {})
            build = item.get("build", {})
            listing = {
                "make": item.get("make", build.get("make", "")),
                "model": item.get("model", build.get("model", "")),
                "year": item.get("year", build.get("year", "")),
                "trim": item.get("trim", build.get("trim", "")),
                "price": item.get("price", ""),
                "mileage": item.get("miles", item.get("mileage", "")),
                "exterior_color": item.get("exterior_color", build.get("exterior_color", "")),
                "interior_color": item.get("interior_color", build.get("interior_color", "")),
                "engine": build.get("engine", item.get("engine", "")),
                "transmission": build.get("transmission", item.get("transmission", "")),
                "drivetrain": build.get("drivetrain", item.get("drivetrain", "")),
                "fuel_type": build.get("fuel_type", item.get("fuel_type", "")),
                "mpg_city": build.get("city_mpg", ""),
                "mpg_highway": build.get("highway_mpg", ""),
                "vin": item.get("vin", ""),
                "stock_number": item.get("stock_no", ""),
                "seller_name": dealer.get("name", ""),
                "seller_type": dealer.get("type", ""),
                "seller_city": dealer.get("city", ""),
                "seller_state": dealer.get("state", ""),
                "seller_distance_mi": item.get("dist", ""),
                "listing_url": item.get("vdp_url", ""),
                "days_on_market": item.get("dom", item.get("days_on_market", "")),
                "accidents_reported": "",
                "owner_count": "",
                "carfax_one_owner": item.get("carfax_1_owner", ""),
                "carfax_clean_title": item.get("carfax_clean_title", ""),
            }
            if listing["make"] and listing["model"]:
                all_listings.append(listing)

        print(f"[marketcheck] Fetched {len(all_listings)} / {min(total, max_results)}")

        start += page_size
        if start >= total:
            break

        time.sleep(1.0)

    if all_listings:
        print(f"[marketcheck] Done. Total: {len(all_listings)}")
    return all_listings


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

def get_all_listings():
    """
    Try multiple sources to get SUV listings.
    Returns the first successful non-empty result set.

    Order: MarketCheck (RapidAPI) -> AutoTrader -> CarGurus
    """
    # Try MarketCheck via RapidAPI first (best structured data)
    listings = fetch_listings_marketcheck()
    if listings:
        return listings

    # Fallback to AutoTrader internal API
    listings = fetch_listings_autotrader()
    if listings:
        return listings

    # Fallback to CarGurus
    listings = fetch_listings_cargurus()
    if listings:
        return listings

    print("[listings] WARNING: No listings found from any source!")
    return []
