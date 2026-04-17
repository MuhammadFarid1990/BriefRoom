import time
from datetime import datetime
from scrapers.news import scrape_news
from scrapers.markets import scrape_markets
from scrapers.tech import scrape_tech

INTERVAL_MINUTES = 30
INTERVAL_SECONDS = INTERVAL_MINUTES * 60


def run_all():
    timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
    print(f"\n{'=' * 50}")
    print(f"  BriefRoom scrape cycle started: {timestamp}")
    print(f"{'=' * 50}\n")

    scrape_markets()
    scrape_news()
    scrape_tech()

    end = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
    print(f"{'=' * 50}")
    print(f"  Cycle complete: {end}")
    print(f"  Next run in {INTERVAL_MINUTES} minutes.")
    print(f"{'=' * 50}\n")


if __name__ == "__main__":
    print("BriefRoom scraper started. Press Ctrl+C to stop.")
    while True:
        try:
            run_all()
        except KeyboardInterrupt:
            print("\nScraper stopped by user.")
            break
        except Exception as e:
            print(f"[scraper] Unexpected error during cycle: {e}")

        try:
            time.sleep(INTERVAL_SECONDS)
        except KeyboardInterrupt:
            print("\nScraper stopped by user.")
            break
