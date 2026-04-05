"""Configuration for the SUV scraper."""

import os
import sys
from dotenv import load_dotenv

# Load .env from project root
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

# RapidAPI - check env var, then fall back to hardcoded default
RAPIDAPI_KEY = os.getenv("RAPIDAPI_KEY", "").strip()

# Allow passing key as CLI arg: python main.py --key YOUR_KEY
for i, arg in enumerate(sys.argv):
    if arg == "--key" and i + 1 < len(sys.argv):
        RAPIDAPI_KEY = sys.argv[i + 1].strip()

if not RAPIDAPI_KEY:
    print("WARNING: No RAPIDAPI_KEY found.")
    print("  Set it in .env file:  RAPIDAPI_KEY=your_key_here")
    print("  Or pass via CLI:      python main.py --key your_key_here")
    print()

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
