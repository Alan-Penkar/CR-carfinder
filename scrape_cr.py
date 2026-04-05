"""
Scrape Consumer Reports for reliability and owner satisfaction ratings.

Uses CR's internal API endpoints that their frontend calls. These return
JSON data for subscribers. The RapidAPI key is used as the authentication
mechanism if a CR-specific API is available on RapidAPI, otherwise we
fall back to scraping CR's website directly.
"""

import re
import time
import json
import requests
from bs4 import BeautifulSoup
from config import RAPIDAPI_KEY

# CR's internal API base (used by their SPA frontend)
CR_API_BASE = "https://www.consumerreports.org/gateway/gl"
CR_SITE_BASE = "https://www.consumerreports.org"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/html, */*",
    "Accept-Language": "en-US,en;q=0.9",
}

# Cache to avoid redundant lookups (keyed by "make|model|year")
_cr_cache = {}


def _create_session():
    """Create a requests session with no proxy (bypass any env proxy)."""
    session = _create_session()
    session.trust_env = False
    return session


def _normalize_name(name):
    """Normalize make/model names for URL construction."""
    return (
        name.lower()
        .strip()
        .replace(" ", "-")
        .replace("_", "-")
        .replace(".", "")
    )


def _fetch_cr_gateway(make, model, year):
    """
    Try Consumer Reports' GraphQL/gateway API for car reliability data.
    This is the API their React frontend calls.
    """
    session = _create_session()

    make_slug = _normalize_name(make)
    model_slug = _normalize_name(model)

    # CR's gateway API for model-level data
    url = f"{CR_API_BASE}/content/cars/{make_slug}/{model_slug}/{year}/overview"
    try:
        resp = session.get(url, timeout=15)
        if resp.status_code == 200:
            data = resp.json()
            return _parse_cr_api_response(data, year)
    except (requests.exceptions.RequestException, ValueError):
        pass

    return None


def _fetch_cr_html(make, model, year):
    """
    Scrape Consumer Reports reliability page for a specific make/model/year.
    Falls back to model-level page if year-specific page is unavailable.
    """
    session = _create_session()

    make_slug = _normalize_name(make)
    model_slug = _normalize_name(model)

    # Try year-specific reliability URL patterns
    urls_to_try = [
        f"{CR_SITE_BASE}/cars/{make_slug}/{model_slug}/{year}/reliability/",
        f"{CR_SITE_BASE}/cars/{make_slug}/{model_slug}/reliability/",
        f"{CR_SITE_BASE}/cars/{make_slug}/{model_slug}/{year}/",
        f"{CR_SITE_BASE}/cars/{make_slug}/{model_slug}/",
    ]

    for url in urls_to_try:
        try:
            resp = session.get(url, timeout=15)
            if resp.status_code == 200:
                result = _parse_cr_html_page(resp.text, year)
                if result:
                    return result
        except requests.exceptions.RequestException:
            continue

    return None


def _parse_cr_api_response(data, year):
    """Parse CR gateway API JSON response."""
    result = {
        "cr_overall_score": "",
        "cr_reliability": "",
        "cr_owner_satisfaction": "",
        "cr_safety": "",
        "cr_road_test_score": "",
        "cr_predicted_reliability": "",
        "cr_recommended": "",
        "cr_url": "",
    }

    if not data:
        return result

    # CR API returns nested data - extract ratings
    ratings = data.get("ratings", data.get("data", {}))
    if isinstance(ratings, dict):
        result["cr_overall_score"] = ratings.get("overallScore", ratings.get("overall", ""))
        result["cr_reliability"] = ratings.get("reliability", ratings.get("reliabilityRating", ""))
        result["cr_owner_satisfaction"] = ratings.get(
            "ownerSatisfaction", ratings.get("satisfaction", "")
        )
        result["cr_safety"] = ratings.get("safety", "")
        result["cr_road_test_score"] = ratings.get("roadTestScore", ratings.get("roadTest", ""))
        result["cr_predicted_reliability"] = ratings.get(
            "predictedReliability", ratings.get("predReliability", "")
        )
        result["cr_recommended"] = ratings.get("recommended", ratings.get("crRecommended", ""))

    return result


def _parse_cr_html_page(html, year):
    """Parse CR HTML page for reliability and satisfaction data."""
    result = {
        "cr_overall_score": "",
        "cr_reliability": "",
        "cr_owner_satisfaction": "",
        "cr_safety": "",
        "cr_road_test_score": "",
        "cr_predicted_reliability": "",
        "cr_recommended": "",
        "cr_url": "",
    }

    soup = BeautifulSoup(html, "lxml")

    # CR embeds structured data in JSON-LD and in React state
    # Look for __NEXT_DATA__ or similar hydration data
    for script in soup.find_all("script"):
        text = script.string or ""

        # Try __NEXT_DATA__ (Next.js hydration)
        if "__NEXT_DATA__" in text:
            match = re.search(r'__NEXT_DATA__\s*=\s*({.*?})\s*;?\s*$', text, re.DOTALL)
            if match:
                try:
                    next_data = json.loads(match.group(1))
                    props = next_data.get("props", {}).get("pageProps", {})
                    return _extract_cr_from_props(props, year, result)
                except (json.JSONDecodeError, KeyError):
                    pass

        # Try inline JSON data blocks
        if '"reliability"' in text or '"overallScore"' in text:
            try:
                data = json.loads(text)
                return _extract_cr_from_json(data, year, result)
            except (json.JSONDecodeError, ValueError):
                pass

    # Fallback: parse HTML elements directly
    # Look for rating elements with data attributes or specific classes
    rating_map = {
        "Overall Score": "cr_overall_score",
        "Predicted Reliability": "cr_predicted_reliability",
        "Reliability": "cr_reliability",
        "Owner Satisfaction": "cr_owner_satisfaction",
        "Safety": "cr_safety",
        "Road Test": "cr_road_test_score",
    }

    for label, key in rating_map.items():
        # Try various selector patterns CR uses
        el = soup.find(string=re.compile(re.escape(label), re.I))
        if el:
            parent = el.find_parent()
            if parent:
                # Look for score nearby (in sibling or child elements)
                score_el = parent.find_next(
                    string=re.compile(r'^\d+(/\d+|/100)?$')
                )
                if score_el:
                    result[key] = score_el.strip()

    # Check if CR recommends
    rec_el = soup.find(string=re.compile(r"Recommended", re.I))
    if rec_el:
        result["cr_recommended"] = "Yes"

    has_data = any(v for k, v in result.items() if k != "cr_url")
    return result if has_data else None


def _extract_cr_from_props(props, year, result):
    """Extract CR data from Next.js page props."""
    # Navigate the props structure for rating data
    model_data = props.get("modelData", props.get("vehicleData", props))

    if isinstance(model_data, dict):
        # Look for year-specific data
        years_data = model_data.get("years", model_data.get("modelYears", {}))
        year_info = None

        if isinstance(years_data, dict):
            year_info = years_data.get(str(year), {})
        elif isinstance(years_data, list):
            for yd in years_data:
                if str(yd.get("year", "")) == str(year):
                    year_info = yd
                    break

        target = year_info if year_info else model_data

        result["cr_overall_score"] = str(target.get("overallScore", ""))
        result["cr_reliability"] = str(target.get("reliability", target.get("reliabilityVerdict", "")))
        result["cr_owner_satisfaction"] = str(target.get("ownerSatisfaction", ""))
        result["cr_safety"] = str(target.get("safety", ""))
        result["cr_road_test_score"] = str(target.get("roadTestScore", ""))
        result["cr_predicted_reliability"] = str(
            target.get("predictedReliability", target.get("predReliability", ""))
        )
        result["cr_recommended"] = str(target.get("recommended", target.get("crRecommended", "")))

    return result


def _extract_cr_from_json(data, year, result):
    """Extract CR data from generic JSON block."""
    if isinstance(data, dict):
        for key in ["overallScore", "reliability", "ownerSatisfaction", "safety",
                     "roadTestScore", "predictedReliability", "recommended"]:
            if key in data:
                cr_key = "cr_" + re.sub(r'([A-Z])', r'_\1', key).lower().lstrip("_")
                if cr_key in result:
                    result[cr_key] = str(data[key])
    return result


def _fetch_cr_rapidapi(make, model, year):
    """
    Try fetching CR data from the unofficial Consumer Reports API on RapidAPI
    (by apidojo), plus supplemental vehicle specs from API Ninjas.
    """
    if not RAPIDAPI_KEY:
        return None

    session = _create_session()
    result = {
        "cr_overall_score": "",
        "cr_reliability": "",
        "cr_owner_satisfaction": "",
        "cr_safety": "",
        "cr_road_test_score": "",
        "cr_predicted_reliability": "",
        "cr_recommended": "",
        "cr_url": "",
    }

    # Try the unofficial Consumer Reports API on RapidAPI (apidojo)
    make_slug = _normalize_name(make)
    model_slug = _normalize_name(model)

    cr_headers = {
        "X-RapidAPI-Key": RAPIDAPI_KEY,
        "X-RapidAPI-Host": "consumer-reports.p.rapidapi.com",
    }

    try:
        resp = session.get(
            f"https://consumer-reports.p.rapidapi.com/cars/{make_slug}/{model_slug}/{year}",
            headers=cr_headers,
            timeout=15,
        )
        if resp.status_code == 200:
            data = resp.json()
            if isinstance(data, dict):
                result["cr_overall_score"] = str(data.get("overallScore", data.get("overall_score", "")))
                result["cr_reliability"] = str(data.get("reliability", data.get("reliabilityRating", "")))
                result["cr_owner_satisfaction"] = str(data.get("ownerSatisfaction", data.get("owner_satisfaction", "")))
                result["cr_safety"] = str(data.get("safety", ""))
                result["cr_road_test_score"] = str(data.get("roadTestScore", data.get("road_test_score", "")))
                result["cr_predicted_reliability"] = str(data.get("predictedReliability", data.get("predicted_reliability", "")))
                result["cr_recommended"] = str(data.get("recommended", data.get("cr_recommended", "")))

                if any(v for k, v in result.items() if k != "cr_url"):
                    return result
    except requests.exceptions.RequestException:
        pass

    # Supplement with vehicle specs from API Ninjas
    ninja_headers = {
        "X-RapidAPI-Key": RAPIDAPI_KEY,
        "X-RapidAPI-Host": "cars-by-api-ninjas.p.rapidapi.com",
    }

    try:
        resp = session.get(
            "https://cars-by-api-ninjas.p.rapidapi.com/v1/cars",
            headers=ninja_headers,
            params={"make": make, "model": model, "year": year},
            timeout=15,
        )
        if resp.status_code == 200:
            data = resp.json()
            if data and isinstance(data, list) and len(data) > 0:
                car = data[0]
                result["_api_class"] = car.get("class", "")
                result["_api_cylinders"] = car.get("cylinders", "")
                result["_api_displacement"] = car.get("displacement", "")
                result["_api_city_mpg"] = car.get("city_mpg", "")
                result["_api_highway_mpg"] = car.get("highway_mpg", "")
                result["_api_combination_mpg"] = car.get("combination_mpg", "")
                return result
    except requests.exceptions.RequestException:
        pass

    return None


def get_cr_data(make, model, year):
    """
    Get Consumer Reports reliability/satisfaction data for a make/model/year.
    Tries multiple methods and caches results.
    """
    cache_key = f"{make}|{model}|{year}"
    if cache_key in _cr_cache:
        return _cr_cache[cache_key]

    empty_result = {
        "cr_overall_score": "",
        "cr_reliability": "",
        "cr_owner_satisfaction": "",
        "cr_safety": "",
        "cr_road_test_score": "",
        "cr_predicted_reliability": "",
        "cr_recommended": "",
        "cr_url": "",
    }

    make_slug = _normalize_name(make)
    model_slug = _normalize_name(model)
    cr_url = f"{CR_SITE_BASE}/cars/{make_slug}/{model_slug}/{year}/reliability/"

    # Try CR gateway API first
    result = _fetch_cr_gateway(make, model, year)
    if result and any(v for k, v in result.items() if k != "cr_url"):
        result["cr_url"] = cr_url
        _cr_cache[cache_key] = result
        return result

    # Try scraping CR HTML
    result = _fetch_cr_html(make, model, year)
    if result and any(v for k, v in result.items() if k != "cr_url"):
        result["cr_url"] = cr_url
        _cr_cache[cache_key] = result
        return result

    # Try RapidAPI supplemental data
    result = _fetch_cr_rapidapi(make, model, year)
    if result:
        result["cr_url"] = cr_url
        _cr_cache[cache_key] = result
        return result

    # Return empty result with URL for manual lookup
    empty_result["cr_url"] = cr_url
    _cr_cache[cache_key] = empty_result
    return empty_result


def enrich_listings_with_cr(listings):
    """
    Add Consumer Reports data to each listing.
    Groups by make/model/year to minimize API calls.
    """
    # Deduplicate make/model/year combos
    combos = set()
    for listing in listings:
        combos.add((listing["make"], listing["model"], listing["year"]))

    print(f"[cr] Looking up Consumer Reports data for {len(combos)} unique make/model/year combos...")

    # Pre-fetch all CR data
    cr_data = {}
    for i, (make, model, year) in enumerate(sorted(combos), 1):
        print(f"[cr] ({i}/{len(combos)}) {year} {make} {model}...")
        key = f"{make}|{model}|{year}"
        cr_data[key] = get_cr_data(make, model, year)
        time.sleep(0.5)  # Rate limit

    # Merge into listings
    enriched = []
    for listing in listings:
        key = f"{listing['make']}|{listing['model']}|{listing['year']}"
        cr = cr_data.get(key, {})
        merged = {**listing, **cr}
        enriched.append(merged)

    return enriched
