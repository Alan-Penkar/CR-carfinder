"""Configuration for the SUV scraper."""

import os
from dotenv import load_dotenv

load_dotenv()

# RapidAPI
RAPIDAPI_KEY = os.getenv("RAPIDAPI_KEY", "")

# Search parameters
SEARCH_PARAMS = {
    "zip_code": "30301",  # Atlanta, GA
    "radius_miles": 50,
    "body_type": "SUV",
    "year_min": 2018,
    "year_max": 2024,
    "condition": "used",
    "max_results": 500,
}

# Output
OUTPUT_DIR = "output"
OUTPUT_CSV = os.path.join(OUTPUT_DIR, "atlanta_suvs.csv")
