# Atlanta Used SUV Finder with Consumer Reports Data

Scrapes used SUV listings from multiple sources (AutoTrader, CarGurus, RapidAPI)
and enriches them with Consumer Reports reliability and owner satisfaction ratings.
Outputs a CSV file for manual inspection and comparison.

## Setup

```bash
pip install -r requirements.txt
```

Create a `.env` file with your RapidAPI key:

```
RAPIDAPI_KEY=your_rapidapi_key_here
```

## Usage

```bash
# Live scrape (requires internet access)
python main.py

# Demo mode with sample data (for testing)
python main.py --demo
```

## Configuration

Edit `config.py` to adjust search parameters:

- **zip_code**: Center of search area (default: 30301 for Atlanta)
- **radius_miles**: Search radius (default: 50)
- **year_min / year_max**: Model year range (default: 2018-2024)
- **max_results**: Maximum listings to fetch (default: 500)

## Output

The CSV is written to `output/atlanta_suvs.csv` with these columns:

| Column | Description |
|--------|-------------|
| year, make, model, trim | Vehicle identification |
| price | Listed price |
| mileage | Odometer reading |
| cr_overall_score | Consumer Reports overall score (0-100) |
| cr_reliability | CR reliability rating (1-5, 5=best) |
| cr_predicted_reliability | CR predicted reliability for the model year |
| cr_owner_satisfaction | CR owner satisfaction rating (1-5) |
| cr_safety | CR safety rating |
| cr_road_test_score | CR road test score |
| cr_recommended | Whether CR recommends the vehicle |
| cr_url | Direct link to CR page for manual verification |
| exterior_color, interior_color | Colors |
| engine, transmission, drivetrain | Powertrain details |
| fuel_type, mpg_city, mpg_highway | Fuel economy |
| seller_name, seller_type, seller_city, seller_state | Dealer/seller info |
| seller_distance_mi | Distance from search zip |
| days_on_market | How long the listing has been up |
| accidents_reported, owner_count | Vehicle history |
| vin | Vehicle Identification Number |
| listing_url | Direct link to the listing |

## Data Sources

1. **AutoTrader** (primary) - Internal search API
2. **RapidAPI car search** (fallback) - Uses your RapidAPI key
3. **CarGurus** (fallback) - HTML scraping
4. **Consumer Reports** - Gateway API and HTML scraping for reliability data

## Consumer Reports Notes

CR data is looked up by unique make/model/year combination and cached to
minimize requests. The `cr_url` column links directly to the CR reliability
page so you can verify ratings with your subscription.

CR reliability ratings use a 1-5 scale:
- 5 = Much Better Than Average
- 4 = Better Than Average
- 3 = Average
- 2 = Worse Than Average
- 1 = Much Worse Than Average
