"""
Scrape used SUV listings from multiple sources.

Uses cloudscraper to bypass Cloudflare anti-bot protection that blocks
plain requests on AutoTrader and CarGurus.

Sources tried in order:
1. MarketCheck via RapidAPI (structured API, requires subscription)
2. AutoTrader (HTML scrape with cloudscraper + mountRoot JSON extraction)
3. CarGurus (AJAX endpoint with cloudscraper)
"""

import json
import re
import time

import cloudscraper
import requests
from bs4 import BeautifulSoup
from config import SEARCH_PARAMS


def _create_scraper():
    """
    Create a cloudscraper session that can bypass Cloudflare protection.
    cloudscraper is a drop-in replacement for requests.Session that
    automatically solves Cloudflare challenges.
    """
    scraper = cloudscraper.create_scraper(
        browser={
            "browser": "chrome",
            "platform": "windows",
            "desktop": True,
        }
    )
    scraper.trust_env = False  # Ignore proxy env vars
    return scraper


def _create_session():
    """Plain requests session for APIs that don't need bot bypass."""
    session = requests.Session()
    session.trust_env = False
    return session


# ---------------------------------------------------------------------------
# AutoTrader  (cloudscraper + mountRoot JSON extraction)
# ---------------------------------------------------------------------------

def fetch_listings_autotrader():
    """
    Fetch used SUV listings from AutoTrader using cloudscraper.

    AutoTrader embeds listing data as JSON inside a mountRoot() call
    in the search results HTML page. We use cloudscraper to bypass
    their Cloudflare protection and extract that JSON.
    """
    scraper = _create_scraper()

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
        }

        try:
            resp = scraper.get(
                "https://www.autotrader.com/cars-for-sale/searchresults.xhtml",
                params=params,
                timeout=30,
            )
            resp.raise_for_status()
        except Exception as e:
            print(f"[autotrader] Error at offset {first_record}: {e}")
            break

        html = resp.text

        # Extract JSON from mountRoot() or other embedded data
        data = _extract_autotrader_json(html)
        if data is None:
            if first_record == 0:
                if "captcha" in html.lower() or "challenge" in html.lower():
                    print("[autotrader] Bot detection still triggered despite cloudscraper")
                else:
                    print("[autotrader] Could not find listing data in page")
                    # Debug: show a snippet of what we got
                    print(f"[autotrader] Page length: {len(html)} chars, "
                          f"title: {_extract_title(html)}")
            break

        inventory = data.get("inventory", data.get("listings", []))
        if not inventory:
            if first_record == 0:
                print(f"[autotrader] No inventory key. Top keys: "
                      f"{list(data.keys())[:10]}")
            break

        total_count = data.get("totalResultCount",
                               data.get("totalCount", len(inventory)))

        for raw in inventory:
            parsed = _parse_autotrader_listing(raw)
            if parsed["make"] and parsed["model"]:
                all_listings.append(parsed)

        print(f"[autotrader] Fetched {len(all_listings)} / "
              f"{min(total_count, max_results)}")

        first_record += page_size
        if first_record >= total_count:
            break

        time.sleep(2.5)

    print(f"[autotrader] Done. Total: {len(all_listings)}")
    return all_listings


def _extract_title(html):
    """Quick helper to get page title for debugging."""
    m = re.search(r"<title>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
    return m.group(1).strip() if m else "(no title)"


def _extract_autotrader_json(html):
    """
    Extract the JSON data blob from AutoTrader HTML page.
    AT embeds listing data inside mountRoot({...}) in a <script> tag.
    """
    # Pattern 1: mountRoot(JSON)  -- the main one
    match = re.search(r'mountRoot\((\{.+?\})\);\s*</script>', html, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass

    # Pattern 2: window.__BONNET_DATA__
    match = re.search(
        r'window\.__BONNET_DATA__\s*=\s*(\{.+?\});\s*</script>',
        html, re.DOTALL
    )
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass

    # Pattern 3: __NEXT_DATA__
    match = re.search(
        r'__NEXT_DATA__\s*=\s*(\{.+?\})\s*;?\s*</script>',
        html, re.DOTALL
    )
    if match:
        try:
            data = json.loads(match.group(1))
            return data.get("props", {}).get("pageProps", data)
        except json.JSONDecodeError:
            pass

    # Pattern 4: search for large JSON with inventory/listings arrays
    soup = BeautifulSoup(html, "lxml")
    for script in soup.find_all("script"):
        text = script.string or ""
        if len(text) > 1000 and ('"inventory"' in text or '"listings"' in text):
            for pat in [
                r'(\{"inventory"\s*:\s*\[.+?\]\s*[,}])',
                r'(\{"listings"\s*:\s*\[.+?\]\s*[,}])',
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
        "price": pricing.get("primary", pricing.get("salePrice",
                 raw.get("price", ""))),
        "mileage": spec_val("mileage"),
        "exterior_color": raw.get("exteriorColorSimple",
                          raw.get("exteriorColor", "")),
        "interior_color": raw.get("interiorColorSimple",
                          raw.get("interiorColor", "")),
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
# CarGurus  (cloudscraper + AJAX endpoint)
# ---------------------------------------------------------------------------

def fetch_listings_cargurus():
    """
    Fetch used SUV listings from CarGurus using cloudscraper.

    Uses the ajaxFetchSubsetInventoryListing.action AJAX endpoint which
    returns JSON when called with X-Requested-With: XMLHttpRequest.
    """
    print("[cargurus] Searching for used SUVs...")
    scraper = _create_scraper()

    # Step 1: Load the search page to get session cookies
    print("[cargurus] Warming up session...")
    try:
        warmup_resp = scraper.get(
            "https://www.cargurus.com/Cars/inventorylisting/"
            "viewDetailsFilterViewInventoryListing.action",
            params={
                "zip": SEARCH_PARAMS["zip_code"],
                "inventorySearchWidgetType": "AUTO",
                "distance": SEARCH_PARAMS["radius_miles"],
                "bodyTypeGroup": "bg_suv_702",
                "startYear": SEARCH_PARAMS["year_min"],
                "endYear": SEARCH_PARAMS["year_max"],
            },
            timeout=30,
        )
        warmup_resp.raise_for_status()
        print(f"[cargurus] Session ready (cookies: "
              f"{len(scraper.cookies)})")

        # Try to extract listings from the warmup HTML page directly
        html_listings = _extract_cargurus_from_html(warmup_resp.text)
        if html_listings:
            print(f"[cargurus] Found {len(html_listings)} listings in HTML")
            return html_listings

    except Exception as e:
        print(f"[cargurus] Warmup error: {e}")
        # Continue anyway - the AJAX call might still work

    # Step 2: Call the AJAX endpoint for JSON data
    ajax_url = (
        "https://www.cargurus.com/Cars/inventorylisting/"
        "ajaxFetchSubsetInventoryListing.action"
    )

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

        headers = {
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "X-Requested-With": "XMLHttpRequest",
            "Referer": (
                "https://www.cargurus.com/Cars/inventorylisting/"
                "viewDetailsFilterViewInventoryListing.action"
            ),
        }

        try:
            resp = scraper.get(
                ajax_url, params=params, headers=headers, timeout=30
            )
            resp.raise_for_status()

            try:
                data = resp.json()
            except ValueError:
                if page == 1:
                    print(f"[cargurus] AJAX returned non-JSON "
                          f"(CT: {resp.headers.get('Content-Type', '?')})")
                break

        except Exception as e:
            print(f"[cargurus] AJAX error on page {page}: {e}")
            break

        page_listings = _extract_cargurus_json(data)
        if not page_listings:
            if page == 1:
                print(f"[cargurus] No listings in AJAX response")
            break

        all_listings.extend(page_listings)
        print(f"[cargurus] Fetched {len(all_listings)} listings (page {page})")

        page += 1
        time.sleep(2.5)

    print(f"[cargurus] Done. Total: {len(all_listings)}")
    return all_listings


def _extract_cargurus_from_html(html):
    """Extract listings directly from CarGurus HTML page."""
    listings = []
    soup = BeautifulSoup(html, "lxml")

    # Method 1: JSON-LD structured data
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
        return listings

    # Method 2: Embedded JS data
    for script in soup.find_all("script"):
        text = script.string or ""
        if len(text) < 500:
            continue

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
                        return listings
                except json.JSONDecodeError:
                    continue

    return listings


def _extract_cargurus_json(data):
    """Extract listings from CarGurus AJAX JSON response."""
    listings = []

    if isinstance(data, dict):
        raw_items = (
            data.get("listings", [])
            or data.get("results", [])
            or data.get("inventoryListings", [])
        )

        if not raw_items:
            for key in data:
                val = data[key]
                if isinstance(val, list) and len(val) > 0:
                    if isinstance(val[0], dict) and any(
                        k in val[0] for k in
                        ("makeName", "modelName", "price", "vin")
                    ):
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


def _parse_jsonld_car(item):
    """Parse JSON-LD Car/Vehicle into our listing format."""
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
    """Parse a CarGurus listing item."""
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
        "price": item.get("price", item.get("expectedPrice",
                 item.get("listPrice", ""))),
        "mileage": item.get("mileage", item.get("mileageString", "")),
        "exterior_color": item.get("exteriorColorName",
                          item.get("exteriorColor", "")),
        "interior_color": item.get("interiorColorName",
                          item.get("interiorColor", "")),
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
        "seller_distance_mi": item.get("distanceFromSearchZip",
                              item.get("distance", "")),
        "listing_url": url,
        "days_on_market": item.get("daysOnMarket", ""),
        "accidents_reported": item.get("accidentCount", ""),
        "owner_count": item.get("ownerCount", ""),
        "deal_rating": item.get("dealRating", item.get("dealScore", "")),
    }


# ---------------------------------------------------------------------------
# MarketCheck via RapidAPI
# ---------------------------------------------------------------------------

# The RapidAPI proxy host for MarketCheck is marketcheck-prod.apigee.net
# (per official SDK). When accessed via RapidAPI, the X-RapidAPI-Host
# header tells RapidAPI's gateway which backend to route to.
_MC_RAPIDAPI_HOST = "marketcheck-prod.apigee.net"


def fetch_listings_marketcheck():
    """
    Fetch used SUV listings via MarketCheck API on RapidAPI.
    """
    from config import RAPIDAPI_KEY

    if not RAPIDAPI_KEY:
        print("[marketcheck] No RapidAPI key configured, skipping")
        return []

    session = _create_session()
    years = ",".join(str(y) for y in range(
        SEARCH_PARAMS["year_min"], SEARCH_PARAMS["year_max"] + 1
    ))

    print("[marketcheck] Querying MarketCheck API...")

    # RapidAPI routes the request: we call the RapidAPI gateway URL
    # with the X-RapidAPI-Host header set to the API's host
    headers = {
        "X-RapidAPI-Key": RAPIDAPI_KEY,
        "X-RapidAPI-Host": _MC_RAPIDAPI_HOST,
        "Accept": "application/json",
    }

    params = {
        "car_type": "used",
        "body_type": "SUV",
        "year": years,
        "zip": SEARCH_PARAMS["zip_code"],
        "radius": SEARCH_PARAMS["radius_miles"],
        "rows": 50,
        "start": 0,
        "sort_by": "price",
        "sort_order": "asc",
    }

    # The URL must go through RapidAPI's gateway, not directly to apigee
    url = f"https://{_MC_RAPIDAPI_HOST}/v2/search/car/active"

    all_listings = []
    max_results = SEARCH_PARAMS["max_results"]

    while params["start"] < max_results:
        try:
            resp = session.get(url, headers=headers, params=params, timeout=30)

            if resp.status_code in (401, 403):
                try:
                    msg = resp.json().get("message", resp.text[:300])
                except ValueError:
                    msg = resp.text[:300]
                print(f"[marketcheck] Auth error ({resp.status_code}): {msg}")
                print("[marketcheck] You may need to subscribe to MarketCheck's "
                      "'Cars Search' API at https://rapidapi.com/marketcheck/api/cars-search")
                return []

            resp.raise_for_status()
            data = resp.json()
        except requests.exceptions.ConnectionError as e:
            print(f"[marketcheck] Connection failed: {e}")
            print("[marketcheck] The MarketCheck API host may not be directly "
                  "accessible. Ensure your RapidAPI subscription is active.")
            return []
        except (requests.exceptions.RequestException, ValueError) as e:
            print(f"[marketcheck] Error: {e}")
            break

        listings = data.get("listings", [])
        if not listings:
            if params["start"] == 0:
                if "error" in data or "message" in data:
                    print(f"[marketcheck] API: "
                          f"{data.get('error', data.get('message', ''))}")
                else:
                    print(f"[marketcheck] Empty. Keys: {list(data.keys())}")
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
                "exterior_color": item.get("exterior_color",
                                  build.get("exterior_color", "")),
                "interior_color": item.get("interior_color",
                                  build.get("interior_color", "")),
                "engine": build.get("engine", item.get("engine", "")),
                "transmission": build.get("transmission",
                                item.get("transmission", "")),
                "drivetrain": build.get("drivetrain",
                              item.get("drivetrain", "")),
                "fuel_type": build.get("fuel_type",
                             item.get("fuel_type", "")),
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
                "days_on_market": item.get("dom",
                                  item.get("days_on_market", "")),
                "accidents_reported": "",
                "owner_count": "",
                "carfax_one_owner": item.get("carfax_1_owner", ""),
                "carfax_clean_title": item.get("carfax_clean_title", ""),
            }
            if listing["make"] and listing["model"]:
                all_listings.append(listing)

        print(f"[marketcheck] Fetched {len(all_listings)} / "
              f"{min(total, max_results)}")

        params["start"] += params["rows"]
        if params["start"] >= total:
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

    # Fallback to AutoTrader (cloudscraper + HTML parsing)
    listings = fetch_listings_autotrader()
    if listings:
        return listings

    # Fallback to CarGurus (cloudscraper + AJAX)
    listings = fetch_listings_cargurus()
    if listings:
        return listings

    print("[listings] WARNING: No listings found from any source!")
    return []
