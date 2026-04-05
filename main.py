#!/usr/bin/env python3
"""
Atlanta Used SUV Finder with Consumer Reports Data

Scrapes used SUV listings from car listing sites (AutoTrader, CarGurus, RapidAPI)
and enriches them with Consumer Reports reliability/satisfaction ratings.
Outputs a CSV for manual inspection.

Usage:
    python main.py          # Live scrape (requires internet)
    python main.py --demo   # Demo mode with sample data (for testing)
"""

import csv
import os
import sys
import time

from config import OUTPUT_DIR, OUTPUT_CSV, SEARCH_PARAMS


# CSV column order - most important columns first
CSV_COLUMNS = [
    # Core vehicle info
    "year",
    "make",
    "model",
    "trim",
    "price",
    "mileage",

    # Consumer Reports data
    "cr_overall_score",
    "cr_reliability",
    "cr_predicted_reliability",
    "cr_owner_satisfaction",
    "cr_safety",
    "cr_road_test_score",
    "cr_recommended",
    "cr_url",

    # Vehicle details
    "exterior_color",
    "interior_color",
    "engine",
    "transmission",
    "drivetrain",
    "fuel_type",
    "mpg_city",
    "mpg_highway",

    # Seller info
    "seller_name",
    "seller_type",
    "seller_city",
    "seller_state",
    "seller_distance_mi",

    # History & metadata
    "days_on_market",
    "accidents_reported",
    "owner_count",
    "carfax_one_owner",
    "carfax_clean_title",
    "vin",
    "stock_number",
    "listing_url",
]


def write_csv(listings, output_path):
    """Write enriched listings to CSV."""
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    # Determine all columns (CSV_COLUMNS + any extras from API supplemental data)
    all_keys = set()
    for listing in listings:
        all_keys.update(listing.keys())

    # Use defined order, append any extra columns at the end
    columns = list(CSV_COLUMNS)
    extras = sorted(k for k in all_keys if k not in columns)
    columns.extend(extras)

    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for listing in listings:
            writer.writerow(listing)

    print(f"\n[output] CSV written to: {output_path}")
    print(f"[output] Total rows: {len(listings)}")
    print(f"[output] Columns: {len(columns)}")


def print_summary(listings):
    """Print a quick summary of results."""
    if not listings:
        print("\nNo listings found.")
        return

    makes = {}
    for l in listings:
        key = f"{l['make']} {l['model']}"
        makes[key] = makes.get(key, 0) + 1

    prices = [float(l["price"]) for l in listings if l.get("price") and str(l["price"]).replace(".", "").isdigit()]
    cr_count = sum(1 for l in listings if l.get("cr_reliability"))

    print("\n" + "=" * 60)
    print("SEARCH RESULTS SUMMARY")
    print("=" * 60)
    print(f"Total listings:        {len(listings)}")
    print(f"Unique models:         {len(makes)}")
    if prices:
        print(f"Price range:           ${min(prices):,.0f} - ${max(prices):,.0f}")
        print(f"Median price:          ${sorted(prices)[len(prices)//2]:,.0f}")
    print(f"With CR data:          {cr_count}")
    print(f"\nTop models by listing count:")
    for model, count in sorted(makes.items(), key=lambda x: -x[1])[:15]:
        print(f"  {model:30s} {count:4d} listings")
    print("=" * 60)


def generate_demo_data():
    """Generate realistic sample data for testing the CSV pipeline."""
    import random
    random.seed(42)

    demo_vehicles = [
        ("Toyota", "RAV4", ["LE", "XLE", "XLE Premium", "Adventure", "TRD Off-Road"]),
        ("Toyota", "Highlander", ["LE", "XLE", "Limited", "Platinum"]),
        ("Toyota", "4Runner", ["SR5", "SR5 Premium", "TRD Off-Road", "Limited", "TRD Pro"]),
        ("Honda", "CR-V", ["LX", "EX", "EX-L", "Touring"]),
        ("Honda", "Pilot", ["LX", "EX", "EX-L", "Touring", "Elite"]),
        ("Mazda", "CX-5", ["Sport", "Touring", "Grand Touring", "Signature"]),
        ("Mazda", "CX-9", ["Sport", "Touring", "Grand Touring", "Signature"]),
        ("Subaru", "Outback", ["Base", "Premium", "Limited", "Touring"]),
        ("Subaru", "Forester", ["Base", "Premium", "Sport", "Limited", "Touring"]),
        ("Hyundai", "Tucson", ["SE", "SEL", "N Line", "Limited"]),
        ("Hyundai", "Santa Fe", ["SE", "SEL", "Limited", "Calligraphy"]),
        ("Kia", "Telluride", ["LX", "S", "EX", "SX", "SX Prestige"]),
        ("Kia", "Sportage", ["LX", "EX", "SX", "SX Turbo"]),
        ("Ford", "Explorer", ["Base", "XLT", "Limited", "ST", "Platinum"]),
        ("Ford", "Escape", ["S", "SE", "SEL", "Titanium"]),
        ("Chevrolet", "Equinox", ["L", "LS", "LT", "Premier", "RS"]),
        ("Chevrolet", "Traverse", ["L", "LS", "LT", "RS", "High Country"]),
        ("Jeep", "Grand Cherokee", ["Laredo", "Limited", "Overland", "Summit", "Trackhawk"]),
        ("Jeep", "Cherokee", ["Latitude", "Latitude Plus", "Limited", "Trailhawk"]),
        ("Volkswagen", "Tiguan", ["S", "SE", "SE R-Line", "SEL", "SEL Premium R-Line"]),
        ("BMW", "X3", ["sDrive30i", "xDrive30i", "M40i"]),
        ("BMW", "X5", ["sDrive40i", "xDrive40i", "xDrive50i", "M50i"]),
        ("Mercedes-Benz", "GLC", ["GLC 300", "GLC 300 4MATIC", "AMG GLC 43"]),
        ("Lexus", "RX", ["RX 350", "RX 350 F SPORT", "RX 450h"]),
        ("Acura", "RDX", ["Base", "Technology", "A-Spec", "Advance"]),
    ]

    cr_ratings = {
        "Toyota RAV4": {"cr_overall_score": "80", "cr_reliability": "4", "cr_predicted_reliability": "4", "cr_owner_satisfaction": "3", "cr_safety": "5", "cr_road_test_score": "78", "cr_recommended": "Yes"},
        "Toyota Highlander": {"cr_overall_score": "82", "cr_reliability": "4", "cr_predicted_reliability": "5", "cr_owner_satisfaction": "4", "cr_safety": "5", "cr_road_test_score": "80", "cr_recommended": "Yes"},
        "Toyota 4Runner": {"cr_overall_score": "60", "cr_reliability": "4", "cr_predicted_reliability": "4", "cr_owner_satisfaction": "5", "cr_safety": "3", "cr_road_test_score": "48", "cr_recommended": "No"},
        "Honda CR-V": {"cr_overall_score": "84", "cr_reliability": "3", "cr_predicted_reliability": "3", "cr_owner_satisfaction": "4", "cr_safety": "5", "cr_road_test_score": "82", "cr_recommended": "Yes"},
        "Honda Pilot": {"cr_overall_score": "72", "cr_reliability": "3", "cr_predicted_reliability": "3", "cr_owner_satisfaction": "3", "cr_safety": "5", "cr_road_test_score": "73", "cr_recommended": "No"},
        "Mazda CX-5": {"cr_overall_score": "85", "cr_reliability": "5", "cr_predicted_reliability": "5", "cr_owner_satisfaction": "4", "cr_safety": "5", "cr_road_test_score": "83", "cr_recommended": "Yes"},
        "Mazda CX-9": {"cr_overall_score": "76", "cr_reliability": "4", "cr_predicted_reliability": "4", "cr_owner_satisfaction": "4", "cr_safety": "5", "cr_road_test_score": "75", "cr_recommended": "Yes"},
        "Subaru Outback": {"cr_overall_score": "73", "cr_reliability": "3", "cr_predicted_reliability": "3", "cr_owner_satisfaction": "4", "cr_safety": "5", "cr_road_test_score": "72", "cr_recommended": "No"},
        "Subaru Forester": {"cr_overall_score": "79", "cr_reliability": "4", "cr_predicted_reliability": "3", "cr_owner_satisfaction": "4", "cr_safety": "5", "cr_road_test_score": "76", "cr_recommended": "Yes"},
        "Hyundai Tucson": {"cr_overall_score": "72", "cr_reliability": "3", "cr_predicted_reliability": "3", "cr_owner_satisfaction": "3", "cr_safety": "4", "cr_road_test_score": "70", "cr_recommended": "No"},
        "Hyundai Santa Fe": {"cr_overall_score": "75", "cr_reliability": "4", "cr_predicted_reliability": "4", "cr_owner_satisfaction": "3", "cr_safety": "5", "cr_road_test_score": "74", "cr_recommended": "Yes"},
        "Kia Telluride": {"cr_overall_score": "83", "cr_reliability": "5", "cr_predicted_reliability": "4", "cr_owner_satisfaction": "5", "cr_safety": "5", "cr_road_test_score": "81", "cr_recommended": "Yes"},
        "Kia Sportage": {"cr_overall_score": "68", "cr_reliability": "3", "cr_predicted_reliability": "3", "cr_owner_satisfaction": "3", "cr_safety": "4", "cr_road_test_score": "66", "cr_recommended": "No"},
        "Ford Explorer": {"cr_overall_score": "48", "cr_reliability": "1", "cr_predicted_reliability": "2", "cr_owner_satisfaction": "3", "cr_safety": "4", "cr_road_test_score": "60", "cr_recommended": "No"},
        "Ford Escape": {"cr_overall_score": "52", "cr_reliability": "2", "cr_predicted_reliability": "2", "cr_owner_satisfaction": "2", "cr_safety": "4", "cr_road_test_score": "62", "cr_recommended": "No"},
        "Chevrolet Equinox": {"cr_overall_score": "62", "cr_reliability": "3", "cr_predicted_reliability": "3", "cr_owner_satisfaction": "2", "cr_safety": "4", "cr_road_test_score": "60", "cr_recommended": "No"},
        "Chevrolet Traverse": {"cr_overall_score": "65", "cr_reliability": "3", "cr_predicted_reliability": "3", "cr_owner_satisfaction": "2", "cr_safety": "4", "cr_road_test_score": "64", "cr_recommended": "No"},
        "Jeep Grand Cherokee": {"cr_overall_score": "55", "cr_reliability": "2", "cr_predicted_reliability": "2", "cr_owner_satisfaction": "4", "cr_safety": "4", "cr_road_test_score": "68", "cr_recommended": "No"},
        "Jeep Cherokee": {"cr_overall_score": "42", "cr_reliability": "1", "cr_predicted_reliability": "1", "cr_owner_satisfaction": "2", "cr_safety": "3", "cr_road_test_score": "48", "cr_recommended": "No"},
        "Volkswagen Tiguan": {"cr_overall_score": "58", "cr_reliability": "2", "cr_predicted_reliability": "3", "cr_owner_satisfaction": "2", "cr_safety": "4", "cr_road_test_score": "58", "cr_recommended": "No"},
        "BMW X3": {"cr_overall_score": "81", "cr_reliability": "3", "cr_predicted_reliability": "3", "cr_owner_satisfaction": "5", "cr_safety": "5", "cr_road_test_score": "88", "cr_recommended": "Yes"},
        "BMW X5": {"cr_overall_score": "76", "cr_reliability": "2", "cr_predicted_reliability": "2", "cr_owner_satisfaction": "5", "cr_safety": "5", "cr_road_test_score": "90", "cr_recommended": "No"},
        "Mercedes-Benz GLC": {"cr_overall_score": "70", "cr_reliability": "2", "cr_predicted_reliability": "2", "cr_owner_satisfaction": "4", "cr_safety": "5", "cr_road_test_score": "82", "cr_recommended": "No"},
        "Lexus RX": {"cr_overall_score": "82", "cr_reliability": "5", "cr_predicted_reliability": "5", "cr_owner_satisfaction": "4", "cr_safety": "5", "cr_road_test_score": "80", "cr_recommended": "Yes"},
        "Acura RDX": {"cr_overall_score": "74", "cr_reliability": "4", "cr_predicted_reliability": "4", "cr_owner_satisfaction": "4", "cr_safety": "5", "cr_road_test_score": "78", "cr_recommended": "Yes"},
    }

    sellers = [
        ("AutoNation Toyota Mall of Georgia", "dealer", "Buford", "GA"),
        ("Jim Ellis Toyota", "dealer", "Marietta", "GA"),
        ("Nalley Toyota Roswell", "dealer", "Roswell", "GA"),
        ("Rick Hendrick Honda", "dealer", "Sandy Springs", "GA"),
        ("Hennessy Honda", "dealer", "Woodstock", "GA"),
        ("Jim Ellis Mazda", "dealer", "Marietta", "GA"),
        ("Ed Voyles Hyundai", "dealer", "Smyrna", "GA"),
        ("Carvana", "online", "Atlanta", "GA"),
        ("CarMax Kennesaw", "dealer", "Kennesaw", "GA"),
        ("Private Seller", "private", "Decatur", "GA"),
        ("Hennessy Lexus", "dealer", "Duluth", "GA"),
        ("Nalley BMW", "dealer", "Decatur", "GA"),
        ("Mercedes-Benz of Buckhead", "dealer", "Atlanta", "GA"),
        ("Classic Chevrolet", "dealer", "Sugar Hill", "GA"),
        ("Landmark Ford", "dealer", "Springfield", "GA"),
    ]

    colors = ["White", "Black", "Silver", "Gray", "Blue", "Red", "Green"]
    drivetrains = ["FWD", "AWD", "4WD"]
    transmissions = ["Automatic", "CVT", "8-Speed Automatic", "9-Speed Automatic", "6-Speed Automatic"]

    listings = []
    for _ in range(80):
        make, model, trims = random.choice(demo_vehicles)
        year = random.randint(2018, 2024)
        trim = random.choice(trims)
        base_price = random.randint(18000, 55000)
        # Newer = more expensive
        price = base_price + (year - 2018) * random.randint(500, 2000)
        mileage = max(1000, random.randint(5000, 120000) - (year - 2018) * 8000)
        seller = random.choice(sellers)
        cr_key = f"{make} {model}"
        cr = cr_ratings.get(cr_key, {})

        listing = {
            "make": make,
            "model": model,
            "year": year,
            "trim": trim,
            "price": price,
            "mileage": mileage,
            "exterior_color": random.choice(colors),
            "interior_color": random.choice(["Black", "Gray", "Tan", "Brown"]),
            "engine": random.choice(["2.5L I4", "3.5L V6", "2.0L Turbo I4", "2.4L I4", "3.6L V6"]),
            "transmission": random.choice(transmissions),
            "drivetrain": random.choice(drivetrains),
            "fuel_type": random.choice(["Gasoline", "Gasoline", "Hybrid"]),
            "mpg_city": random.randint(19, 30),
            "mpg_highway": random.randint(26, 35),
            "vin": "".join(random.choices("0123456789ABCDEFGHJKLMNPRSTUVWXYZ", k=17)),
            "stock_number": f"STK{random.randint(10000, 99999)}",
            "seller_name": seller[0],
            "seller_type": seller[1],
            "seller_city": seller[2],
            "seller_state": seller[3],
            "seller_distance_mi": random.randint(1, 45),
            "listing_url": f"https://www.autotrader.com/cars-for-sale/vehicledetails.xhtml?listingId={random.randint(600000000, 900000000)}",
            "days_on_market": random.randint(1, 90),
            "accidents_reported": random.choice([0, 0, 0, 1]),
            "owner_count": random.randint(1, 3),
            "cr_overall_score": cr.get("cr_overall_score", ""),
            "cr_reliability": cr.get("cr_reliability", ""),
            "cr_predicted_reliability": cr.get("cr_predicted_reliability", ""),
            "cr_owner_satisfaction": cr.get("cr_owner_satisfaction", ""),
            "cr_safety": cr.get("cr_safety", ""),
            "cr_road_test_score": cr.get("cr_road_test_score", ""),
            "cr_recommended": cr.get("cr_recommended", ""),
            "cr_url": f"https://www.consumerreports.org/cars/{make.lower().replace(' ', '-')}/{model.lower().replace(' ', '-')}/{year}/reliability/",
        }
        listings.append(listing)

    return listings


def main():
    demo_mode = "--demo" in sys.argv

    print("=" * 60)
    print("ATLANTA USED SUV FINDER")
    print(f"Years: {SEARCH_PARAMS['year_min']}-{SEARCH_PARAMS['year_max']}")
    print(f"Area: {SEARCH_PARAMS['zip_code']} ({SEARCH_PARAMS['radius_miles']}mi radius)")
    if demo_mode:
        print("MODE: DEMO (sample data)")
    print("=" * 60)

    start = time.time()

    if demo_mode:
        print("\n--- Generating demo data ---")
        enriched = generate_demo_data()
    else:
        from scrape_listings import get_all_listings
        from scrape_cr import enrich_listings_with_cr

        # Step 1: Fetch listings
        print("\n--- Step 1: Fetching SUV listings ---")
        listings = get_all_listings()

        if not listings:
            print("\nERROR: No listings found. Check your network connection and try again.")
            print("TIP: Run with --demo to test with sample data.")
            sys.exit(1)

        # Step 2: Enrich with Consumer Reports data
        print("\n--- Step 2: Fetching Consumer Reports data ---")
        enriched = enrich_listings_with_cr(listings)

    # Sort by value (price ascending, then CR score descending)
    enriched.sort(key=lambda x: (
        float(x.get("price", 999999)) if str(x.get("price", "")).replace(".", "").isdigit() else 999999,
        -float(x.get("cr_overall_score", 0) or 0),
    ))

    # Write CSV
    print("\n--- Writing CSV ---")
    write_csv(enriched, OUTPUT_CSV)

    elapsed = time.time() - start
    print_summary(enriched)
    print(f"\nCompleted in {elapsed:.1f}s")
    print(f"Open {OUTPUT_CSV} in Excel or Google Sheets to review.")


if __name__ == "__main__":
    main()
