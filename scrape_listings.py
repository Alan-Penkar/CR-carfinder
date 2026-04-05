"""
Scrape used SUV listings from AutoTrader's internal API.

AutoTrader provides a JSON API endpoint that their frontend uses. We query it
directly to get structured listing data for used SUVs in the Atlanta area.
"""

import time
import requests
from config import SEARCH_PARAMS

# AutoTrader's internal search API
AUTOTRADER_API = "https://www.autotrader.com/rest/searchresults/base"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Accept-Language": "en-US,en;q=0.9",
}


def _create_session():
    """Create a requests session with no proxy (bypass any env proxy)."""
    session = requests.Session()
    session.headers.update(HEADERS)
    session.trust_env = False  # Ignore HTTP_PROXY/HTTPS_PROXY env vars
    return session


def _build_params(first_record=0, num_records=100):
    """Build query parameters for AutoTrader search API."""
    return {
        "zip": SEARCH_PARAMS["zip_code"],
        "searchRadius": SEARCH_PARAMS["radius_miles"],
        "vehicleStyleCodes": "SUVCROSS",
        "startYear": SEARCH_PARAMS["year_min"],
        "endYear": SEARCH_PARAMS["year_max"],
        "listingTypes": "USED",
        "sortBy": "relevance",
        "numRecords": num_records,
        "firstRecord": first_record,
        "channel": "ATC",
    }


def _parse_listing(raw):
    """Extract relevant fields from a raw AutoTrader listing dict."""
    specs = raw.get("specifications", {})
    pricing = raw.get("pricingDetail", {})
    dealer = raw.get("owner", {})
    location = dealer.get("location", {})

    # Get mileage from specifications
    mileage = specs.get("mileage", {}).get("value", "")

    # Get engine/transmission/drivetrain
    engine = specs.get("engine", {}).get("value", "")
    transmission = specs.get("transmission", {}).get("value", "")
    drivetrain = specs.get("drivetrain", {}).get("value", "")
    fuel_type = specs.get("fuelType", {}).get("value", "")
    mpg_city = specs.get("mpgCity", {}).get("value", "")
    mpg_highway = specs.get("mpgHighway", {}).get("value", "")

    return {
        "make": raw.get("makeString", ""),
        "model": raw.get("modelString", ""),
        "year": raw.get("year", ""),
        "trim": raw.get("trimString", ""),
        "price": pricing.get("primary", pricing.get("salePrice", "")),
        "mileage": mileage,
        "exterior_color": raw.get("exteriorColorSimple", ""),
        "interior_color": raw.get("interiorColorSimple", ""),
        "engine": engine,
        "transmission": transmission,
        "drivetrain": drivetrain,
        "fuel_type": fuel_type,
        "mpg_city": mpg_city,
        "mpg_highway": mpg_highway,
        "vin": raw.get("vin", ""),
        "stock_number": raw.get("stockNumber", ""),
        "seller_name": dealer.get("name", ""),
        "seller_type": raw.get("ownerType", ""),
        "seller_city": location.get("city", ""),
        "seller_state": location.get("state", ""),
        "seller_distance_mi": raw.get("distanceFromSearch", ""),
        "listing_url": f"https://www.autotrader.com/cars-for-sale/vehicledetails.xhtml?listingId={raw.get('id', '')}",
        "days_on_market": raw.get("daysOnMarket", ""),
        "accidents_reported": raw.get("accidentCount", ""),
        "owner_count": raw.get("ownerCount", ""),
    }


def fetch_listings():
    """
    Fetch all used SUV listings from AutoTrader for the configured search area.
    Returns a list of parsed listing dicts.
    """
    session = _create_session()

    all_listings = []
    first_record = 0
    page_size = 100
    max_results = SEARCH_PARAMS["max_results"]

    print(f"[listings] Searching AutoTrader for used SUVs near {SEARCH_PARAMS['zip_code']}...")
    print(f"[listings] Years {SEARCH_PARAMS['year_min']}-{SEARCH_PARAMS['year_max']}, "
          f"radius {SEARCH_PARAMS['radius_miles']}mi")

    while first_record < max_results:
        params = _build_params(first_record, page_size)
        try:
            resp = session.get(AUTOTRADER_API, params=params, timeout=30)
            resp.raise_for_status()
            data = resp.json()
        except requests.exceptions.HTTPError as e:
            print(f"[listings] HTTP error at offset {first_record}: {e}")
            break
        except (requests.exceptions.RequestException, ValueError) as e:
            print(f"[listings] Request error at offset {first_record}: {e}")
            break

        listings = data.get("listings", [])
        if not listings:
            break

        total_count = data.get("totalResultCount", 0)
        for raw in listings:
            parsed = _parse_listing(raw)
            if parsed["make"] and parsed["model"]:
                all_listings.append(parsed)

        print(f"[listings] Fetched {len(all_listings)} / {min(total_count, max_results)} listings")

        first_record += page_size
        if first_record >= total_count:
            break

        # Be polite - rate limit
        time.sleep(1.5)

    print(f"[listings] Done. Total listings: {len(all_listings)}")
    return all_listings


def fetch_listings_cargurus_fallback():
    """
    Fallback: Fetch used SUV listings from CarGurus if AutoTrader fails.
    Uses CarGurus' internal inventory API.
    """
    print("[listings] Trying CarGurus fallback...")
    session = _create_session()

    all_listings = []
    offset = 0
    page_size = 50

    while offset < SEARCH_PARAMS["max_results"]:
        url = "https://www.cargurus.com/Cars/searchResults.action"
        params = {
            "zip": SEARCH_PARAMS["zip_code"],
            "inventorySearchWidgetType": "BODYSTYLE",
            "bodyStyleGroup": "bg_suvcrossover",
            "startYear": SEARCH_PARAMS["year_min"],
            "endYear": SEARCH_PARAMS["year_max"],
            "maxMileage": 150000,
            "searchDistance": SEARCH_PARAMS["radius_miles"],
            "sortType": "BEST_MATCH",
            "sortDirection": "ASC",
            "offset": offset,
            "maxResults": page_size,
            "filtersModified": "true",
        }

        try:
            resp = session.get(url, params=params, timeout=30)
            resp.raise_for_status()
        except requests.exceptions.RequestException as e:
            print(f"[listings] CarGurus error at offset {offset}: {e}")
            break

        # CarGurus returns HTML; parse for JSON data embedded in page
        from bs4 import BeautifulSoup
        import json
        import re

        soup = BeautifulSoup(resp.text, "lxml")

        # Look for listing data in script tags
        scripts = soup.find_all("script")
        listings_data = []
        for script in scripts:
            text = script.string or ""
            # CarGurus embeds listing data as JSON in various script blocks
            match = re.search(r'"listings"\s*:\s*(\[.*?\])\s*[,}]', text, re.DOTALL)
            if match:
                try:
                    listings_data = json.loads(match.group(1))
                    break
                except json.JSONDecodeError:
                    continue

        if not listings_data:
            print(f"[listings] CarGurus: no listings found at offset {offset}")
            break

        for item in listings_data:
            listing = {
                "make": item.get("makeName", ""),
                "model": item.get("modelName", ""),
                "year": item.get("carYear", ""),
                "trim": item.get("trimName", ""),
                "price": item.get("expectedPrice", item.get("price", "")),
                "mileage": item.get("mileage", ""),
                "exterior_color": item.get("exteriorColorName", ""),
                "interior_color": item.get("interiorColorName", ""),
                "engine": item.get("engine", ""),
                "transmission": item.get("transmission", ""),
                "drivetrain": item.get("driveTrain", ""),
                "fuel_type": item.get("fuelType", ""),
                "mpg_city": "",
                "mpg_highway": "",
                "vin": item.get("vin", ""),
                "stock_number": "",
                "seller_name": item.get("dealerName", ""),
                "seller_type": "dealer" if item.get("dealerName") else "private",
                "seller_city": item.get("dealerCity", ""),
                "seller_state": item.get("dealerState", ""),
                "seller_distance_mi": item.get("distanceFromSearchZip", ""),
                "listing_url": f"https://www.cargurus.com{item.get('url', '')}",
                "days_on_market": item.get("daysOnMarket", ""),
                "accidents_reported": "",
                "owner_count": "",
            }
            if listing["make"] and listing["model"]:
                all_listings.append(listing)

        print(f"[listings] CarGurus: fetched {len(all_listings)} listings")
        offset += page_size

        if len(listings_data) < page_size:
            break

        time.sleep(1.5)

    print(f"[listings] CarGurus done. Total: {len(all_listings)}")
    return all_listings


def fetch_listings_rapidapi():
    """
    Fetch used SUV listings via RapidAPI car listing APIs.
    Tries multiple available APIs.
    """
    from config import RAPIDAPI_KEY

    if not RAPIDAPI_KEY:
        print("[listings] No RapidAPI key configured, skipping RapidAPI source")
        return []

    all_listings = []
    session = _create_session()

    # Try the "real-time-used-car-search" API on RapidAPI
    print("[listings] Querying RapidAPI car search...")
    headers = {
        "X-RapidAPI-Key": RAPIDAPI_KEY,
        "X-RapidAPI-Host": "cargurus-api.p.rapidapi.com",
    }

    params = {
        "zip": SEARCH_PARAMS["zip_code"],
        "radius": SEARCH_PARAMS["radius_miles"],
        "bodyType": "SUV",
        "yearMin": SEARCH_PARAMS["year_min"],
        "yearMax": SEARCH_PARAMS["year_max"],
        "condition": "used",
        "maxResults": min(SEARCH_PARAMS["max_results"], 200),
    }

    try:
        resp = session.get(
            "https://cargurus-api.p.rapidapi.com/search",
            headers=headers,
            params=params,
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()

        results = data if isinstance(data, list) else data.get("results", data.get("listings", []))
        for item in results:
            listing = {
                "make": item.get("make", item.get("makeName", "")),
                "model": item.get("model", item.get("modelName", "")),
                "year": item.get("year", item.get("carYear", "")),
                "trim": item.get("trim", item.get("trimName", "")),
                "price": item.get("price", item.get("expectedPrice", "")),
                "mileage": item.get("mileage", ""),
                "exterior_color": item.get("exteriorColor", ""),
                "interior_color": item.get("interiorColor", ""),
                "engine": item.get("engine", ""),
                "transmission": item.get("transmission", ""),
                "drivetrain": item.get("drivetrain", item.get("driveTrain", "")),
                "fuel_type": item.get("fuelType", ""),
                "mpg_city": item.get("mpgCity", ""),
                "mpg_highway": item.get("mpgHighway", ""),
                "vin": item.get("vin", ""),
                "stock_number": item.get("stockNumber", ""),
                "seller_name": item.get("dealerName", item.get("sellerName", "")),
                "seller_type": item.get("sellerType", ""),
                "seller_city": item.get("city", ""),
                "seller_state": item.get("state", ""),
                "seller_distance_mi": item.get("distance", ""),
                "listing_url": item.get("url", item.get("listingUrl", "")),
                "days_on_market": item.get("daysOnMarket", ""),
                "accidents_reported": item.get("accidentCount", ""),
                "owner_count": item.get("ownerCount", ""),
            }
            if listing["make"] and listing["model"]:
                all_listings.append(listing)

        print(f"[listings] RapidAPI returned {len(all_listings)} listings")
    except requests.exceptions.RequestException as e:
        print(f"[listings] RapidAPI error: {e}")

    return all_listings


def get_all_listings():
    """
    Try multiple sources to get SUV listings. Returns the first successful
    non-empty result set.
    """
    # Try AutoTrader first
    listings = fetch_listings()
    if listings:
        return listings

    # Fallback to RapidAPI
    listings = fetch_listings_rapidapi()
    if listings:
        return listings

    # Fallback to CarGurus
    listings = fetch_listings_cargurus_fallback()
    if listings:
        return listings

    print("[listings] WARNING: No listings found from any source!")
    return []
