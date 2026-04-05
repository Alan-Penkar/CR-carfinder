"""
Scrape used SUV listings from multiple sources.

Sources tried in order:
1. MarketCheck via RapidAPI (best structured data, requires API key)
2. AutoTrader (fetch searchresults.xhtml, parse mountRoot() embedded JSON)
3. CarGurus AJAX endpoint (ajaxFetchSubsetInventoryListing.action)
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
# AutoTrader  (fetch HTML, extract mountRoot() JSON)
# ---------------------------------------------------------------------------

def fetch_listings_autotrader():
    """
    Fetch used SUV listings from AutoTrader.

    AutoTrader no longer exposes a clean JSON REST API. Instead we fetch the
    search-results HTML page (searchresults.xhtml) and extract the JSON blob
    that is passed to their mountRoot() bootstrapper.
    """
    session = _create_session()

    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/125.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Cache-Control": "no-cache",
    })

    all_listings = []
    first_record = 0
    page_size = 100
    max_results = SEARCH_PARAMS["max_results"]

    print(f"[autotrader] Searching for used SUVs near {SEARCH_PARAMS['zip_code']}...")

    while first_record < max_results:
        # Use the searchresults.xhtml page URL (the HTML page that embeds JSON)
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
        }

        try:
            resp = session.get(
                "https://www.autotrader.com/cars-for-sale/searchresults.xhtml",
                params=params,
                timeout=30,
            )
            resp.raise_for_status()
        except requests.exceptions.HTTPError as e:
            print(f"[autotrader] HTTP {resp.status_code} at offset {first_record}")
            break
        except requests.exceptions.RequestException as e:
            print(f"[autotrader] Request error at offset {first_record}: {e}")
            break

        html = resp.text

        # Extract JSON from mountRoot() call embedded in the page
        data = _extract_autotrader_json(html)
        if data is None:
            if first_record == 0:
                print("[autotrader] Could not find mountRoot() data in page HTML")
                # Maybe we got a CAPTCHA or redirect
                if "captcha" in html.lower() or "challenge" in html.lower():
                    print("[autotrader] Bot detection / CAPTCHA triggered")
            break

        # The listing data lives in data["inventory"] or data["listings"]
        inventory = data.get("inventory", data.get("listings", []))
        if not inventory:
            if first_record == 0:
                print(f"[autotrader] No inventory in data. Top keys: {list(data.keys())[:10]}")
            break

        total_count = data.get("totalResultCount", data.get("totalCount", len(inventory)))

        for raw in inventory:
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


def _extract_autotrader_json(html):
    """
    Extract the JSON data blob from AutoTrader's HTML page.
    AT embeds listing data inside a mountRoot({...}) call in a <script> tag.
    """
    # Pattern 1: mountRoot(JSON)
    match = re.search(r'mountRoot\((\{.+?\})\);\s*</script>', html, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass

    # Pattern 2: window.__BONNET_DATA__ = JSON
    match = re.search(r'window\.__BONNET_DATA__\s*=\s*(\{.+?\});\s*</script>', html, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass

    # Pattern 3: __NEXT_DATA__ (if AT migrated to Next.js)
    match = re.search(r'__NEXT_DATA__\s*=\s*(\{.+?\})\s*;?\s*</script>', html, re.DOTALL)
    if match:
        try:
            data = json.loads(match.group(1))
            return data.get("props", {}).get("pageProps", data)
        except json.JSONDecodeError:
            pass

    # Pattern 4: look for large JSON blobs in script tags
    soup = BeautifulSoup(html, "lxml")
    for script in soup.find_all("script"):
        text = script.string or ""
        if '"inventory"' in text or '"listings"' in text:
            # Try to find a JSON object containing inventory
            for pat in [
                r'(\{"inventory"\s*:\s*\[.+?\].*?\})',
                r'(\{"listings"\s*:\s*\[.+?\].*?\})',
            ]:
                m = re.search(pat, text, re.DOTALL)
                if m:
                    try:
                        return json.loads(m.group(1))
                    except json.JSONDecodeError:
                        continue

    return None


def _parse_autotrader_listing(raw):
    """Extract fields from an AutoTrader listing dict."""
    specs = raw.get("specifications", {})
    pricing = raw.get("pricingDetail", {})
    dealer = raw.get("owner", {})
    location = dealer.get("location", {})

    def spec_val(key):
        v = specs.get(key, {})
        return v.get("value", "") if isinstance(v, dict) else v

    listing_id = raw.get("id", raw.get("listingId", ""))

    return {
        "make": raw.get("makeString", raw.get("make", "")),
        "model": raw.get("modelString", raw.get("model", "")),
        "year": raw.get("year", ""),
        "trim": raw.get("trimString", raw.get("trim", "")),
        "price": pricing.get("primary", pricing.get("salePrice", raw.get("price", ""))),
        "mileage": spec_val("mileage"),
        "exterior_color": raw.get("exteriorColorSimple", raw.get("exteriorColor", "")),
        "interior_color": raw.get("interiorColorSimple", raw.get("interiorColor", "")),
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
            f"?listingId={listing_id}"
        ),
        "days_on_market": raw.get("daysOnMarket", ""),
        "accidents_reported": raw.get("accidentCount", ""),
        "owner_count": raw.get("ownerCount", ""),
    }


# ---------------------------------------------------------------------------
# CarGurus  (AJAX endpoint returns JSON directly)
# ---------------------------------------------------------------------------

def fetch_listings_cargurus():
    """
    Fetch used SUV listings from CarGurus using their AJAX endpoint.

    The correct endpoint is ajaxFetchSubsetInventoryListing.action (NOT
    searchResults.action which returns 406). The AJAX endpoint returns JSON
    when called with X-Requested-With: XMLHttpRequest.
    """
    print("[cargurus] Searching for used SUVs...")
    session = _create_session()

    # First, hit the main search page to get a session cookie
    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/125.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
    })

    # Warm up session with a page load to get cookies
    try:
        session.headers["Accept"] = (
            "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
        )
        warmup = session.get(
            "https://www.cargurus.com/Cars/inventorylisting/"
            "viewDetailsFilterViewInventoryListing.action",
            params={
                "zip": SEARCH_PARAMS["zip_code"],
                "inventorySearchWidgetType": "AUTO",
                "distance": SEARCH_PARAMS["radius_miles"],
            },
            timeout=30,
        )
        warmup.raise_for_status()
        print("[cargurus] Session cookies acquired")
    except requests.exceptions.RequestException as e:
        print(f"[cargurus] Cookie warmup failed: {e}")
        # Try to continue anyway

    # Now use the AJAX endpoint that returns JSON
    ajax_url = (
        "https://www.cargurus.com/Cars/inventorylisting/"
        "ajaxFetchSubsetInventoryListing.action"
    )

    # Set AJAX headers — these are critical to get JSON back
    session.headers.update({
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "X-Requested-With": "XMLHttpRequest",
        "Referer": (
            "https://www.cargurus.com/Cars/inventorylisting/"
            "viewDetailsFilterViewInventoryListing.action"
        ),
    })

    all_listings = []
    page = 1
    max_pages = SEARCH_PARAMS["max_results"] // 15 + 1

    while page <= max_pages:
        params = {
            "sourceContext": "carGurusHomePageModel",
            "zip": SEARCH_PARAMS["zip_code"],
            "distance": SEARCH_PARAMS["radius_miles"],
            "startYear": SEARCH_PARAMS["year_min"],
            "endYear": SEARCH_PARAMS["year_max"],
            "bodyTypeGroup": "bg_suv_702",
            "inventorySearchWidgetType": "AUTO",
            "sortDir": "ASC",
            "sortType": "DEAL_SCORE",
            "page": page,
            "allYearsForTrimName": "true",
            "displayFeaturedListings": "true",
            "isRecentSearchView": "false",
        }

        try:
            resp = session.get(ajax_url, params=params, timeout=30)
            resp.raise_for_status()

            # Verify we got JSON
            ct = resp.headers.get("Content-Type", "")
            if "json" in ct or "javascript" in ct:
                data = resp.json()
            else:
                # Sometimes CG returns JSON with wrong content-type
                try:
                    data = resp.json()
                except ValueError:
                    print(f"[cargurus] Non-JSON response on page {page} (CT: {ct})")
                    if page == 1:
                        # Fall back to HTML parsing
                        return _parse_cargurus_html_fallback(session)
                    break

        except requests.exceptions.HTTPError as e:
            print(f"[cargurus] HTTP {resp.status_code} on page {page}")
            if page == 1:
                return _parse_cargurus_html_fallback(session)
            break
        except requests.exceptions.RequestException as e:
            print(f"[cargurus] Error on page {page}: {e}")
            break

        # Parse listings from JSON response
        page_listings = _extract_cargurus_json(data)
        if not page_listings:
            if page == 1:
                print(f"[cargurus] No listings in JSON response. "
                      f"Keys: {list(data.keys()) if isinstance(data, dict) else type(data)}")
                return _parse_cargurus_html_fallback(session)
            break

        all_listings.extend(page_listings)
        print(f"[cargurus] Fetched {len(all_listings)} listings (page {page})")

        page += 1
        time.sleep(2.0)

    print(f"[cargurus] Done. Total: {len(all_listings)}")
    return all_listings


def _extract_cargurus_json(data):
    """Extract listings from CarGurus AJAX JSON response."""
    listings = []

    if isinstance(data, dict):
        # The AJAX response has listings at various possible paths
        raw_items = (
            data.get("listings", [])
            or data.get("results", [])
            or data.get("inventoryListings", [])
        )

        # Sometimes nested under a key
        if not raw_items:
            for key in data:
                val = data[key]
                if isinstance(val, list) and len(val) > 0 and isinstance(val[0], dict):
                    if any(k in val[0] for k in ("makeName", "modelName", "price", "vin")):
                        raw_items = val
                        break

        for item in raw_items:
            listing = _parse_cargurus_item(item)
            if listing:
                listings.append(listing)

    elif isinstance(data, list):
        for item in data:
            listing = _parse_cargurus_item(item)
            if listing:
                listings.append(listing)

    return listings


def _parse_cargurus_html_fallback(session):
    """
    Fallback: if the AJAX endpoint fails, try parsing the HTML search page
    for embedded JSON data or JSON-LD structured data.
    """
    print("[cargurus] Trying HTML fallback...")
    session.headers["Accept"] = (
        "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
    )
    session.headers.pop("X-Requested-With", None)

    try:
        resp = session.get(
            "https://www.cargurus.com/Cars/inventorylisting/"
            "viewDetailsFilterViewInventoryListing.action",
            params={
                "zip": SEARCH_PARAMS["zip_code"],
                "distance": SEARCH_PARAMS["radius_miles"],
                "bodyTypeGroup": "bg_suv_702",
                "startYear": SEARCH_PARAMS["year_min"],
                "endYear": SEARCH_PARAMS["year_max"],
                "inventorySearchWidgetType": "AUTO",
                "sortDir": "ASC",
                "sortType": "DEAL_SCORE",
            },
            timeout=30,
        )
        resp.raise_for_status()
    except requests.exceptions.RequestException as e:
        print(f"[cargurus] HTML fallback failed: {e}")
        return []

    listings = []
    soup = BeautifulSoup(resp.text, "lxml")

    # Try JSON-LD structured data
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
            items = data if isinstance(data, list) else [data]
            for item in items:
                if item.get("@type") in ("Car", "Vehicle"):
                    listings.append(_parse_jsonld_car(item))
        except (json.JSONDecodeError, KeyError):
            continue

    if listings:
        print(f"[cargurus] HTML fallback found {len(listings)} listings via JSON-LD")
        return listings

    # Try embedded JS data
    for script in soup.find_all("script"):
        text = script.string or ""
        for pattern in [
            r'"listings"\s*:\s*(\[.*?\])\s*[,}]',
            r'"results"\s*:\s*(\[.*?\])\s*[,}]',
            r'"inventoryListings"\s*:\s*(\[.*?\])\s*[,}]',
        ]:
            match = re.search(pattern, text, re.DOTALL)
            if match:
                try:
                    items = json.loads(match.group(1))
                    for item in items:
                        listing = _parse_cargurus_item(item)
                        if listing:
                            listings.append(listing)
                    if listings:
                        print(f"[cargurus] HTML fallback found {len(listings)} "
                              f"listings in embedded JS")
                        return listings
                except json.JSONDecodeError:
                    continue

    print("[cargurus] HTML fallback found no listings")
    return listings


def _parse_jsonld_car(item):
    """Parse a JSON-LD Car/Vehicle schema into our listing format."""
    offers = item.get("offers", {})
    seller = offers.get("seller", item.get("seller", {}))
    brand = item.get("brand", {})

    return {
        "make": brand.get("name", "") if isinstance(brand, dict) else str(brand),
        "model": item.get("model", ""),
        "year": item.get("vehicleModelDate", item.get("productionDate", "")),
        "trim": item.get("vehicleConfiguration", ""),
        "price": offers.get("price", ""),
        "mileage": (
            item.get("mileageFromOdometer", {}).get("value", "")
            if isinstance(item.get("mileageFromOdometer"), dict)
            else item.get("mileageFromOdometer", "")
        ),
        "exterior_color": item.get("color", ""),
        "interior_color": item.get("vehicleInteriorColor", ""),
        "engine": (
            item.get("vehicleEngine", {}).get("name", "")
            if isinstance(item.get("vehicleEngine"), dict)
            else str(item.get("vehicleEngine", ""))
        ),
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
    """Parse a CarGurus listing item from AJAX or embedded data."""
    if not isinstance(item, dict):
        return None

    make = item.get("makeName", item.get("make", ""))
    model = item.get("modelName", item.get("model", ""))
    if not make or not model:
        return None

    url = item.get("url", item.get("listingUrl", ""))
    if url and url.startswith("/"):
        url = f"https://www.cargurus.com{url}"

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
        "listing_url": url,
        "days_on_market": item.get("daysOnMarket", ""),
        "accidents_reported": item.get("accidentCount", ""),
        "owner_count": item.get("ownerCount", ""),
        "deal_rating": item.get("dealRating", item.get("dealScore", "")),
    }


# ---------------------------------------------------------------------------
# MarketCheck via RapidAPI
# ---------------------------------------------------------------------------

def fetch_listings_marketcheck():
    """
    Fetch used SUV listings via MarketCheck API on RapidAPI.
    MarketCheck aggregates 14M+ listings from 50k+ US dealers.

    The correct RapidAPI host is marketcheck-prod.apigee.net (confirmed
    from official SDK and documentation).
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

    headers = {
        "X-RapidAPI-Key": RAPIDAPI_KEY,
        "X-RapidAPI-Host": "marketcheck-prod.apigee.net",
    }

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

        try:
            resp = session.get(
                "https://marketcheck-prod.apigee.net/v2/search/car/active",
                headers=headers,
                params=params,
                timeout=30,
            )

            if resp.status_code in (401, 403):
                error_msg = ""
                try:
                    error_msg = resp.json().get("message", resp.text[:200])
                except ValueError:
                    error_msg = resp.text[:200]
                print(f"[marketcheck] Auth error ({resp.status_code}): {error_msg}")
                print("[marketcheck] Verify your RapidAPI key is subscribed to "
                      "the MarketCheck 'Cars Search' API at rapidapi.com")
                return []

            resp.raise_for_status()
            data = resp.json()
        except requests.exceptions.HTTPError as e:
            print(f"[marketcheck] HTTP error at offset {start}: {e}")
            break
        except (requests.exceptions.RequestException, ValueError) as e:
            print(f"[marketcheck] Error at offset {start}: {e}")
            break

        listings = data.get("listings", [])
        if not listings:
            if start == 0:
                if "error" in data or "message" in data:
                    print(f"[marketcheck] API message: "
                          f"{data.get('error', data.get('message', ''))}")
                else:
                    print(f"[marketcheck] Empty response. Keys: {list(data.keys())}")
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

    # Fallback to AutoTrader (HTML scraping)
    listings = fetch_listings_autotrader()
    if listings:
        return listings

    # Fallback to CarGurus (AJAX endpoint)
    listings = fetch_listings_cargurus()
    if listings:
        return listings

    print("[listings] WARNING: No listings found from any source!")
    return []
