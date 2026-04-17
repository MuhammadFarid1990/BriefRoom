import feedparser
from datetime import datetime
from db import get_db_connection, init_db

FEEDS = [
    {
        "url":      "https://feeds.npr.org/1006/rss.xml",
        "source":   "NPR Business",
        "category": "business",
    },
    {
        "url":      "https://www.theguardian.com/uk/business/rss",
        "source":   "The Guardian",
        "category": "business",
    },
    {
        "url":      "https://www.cnbc.com/id/10001147/device/rss/rss.html",
        "source":   "CNBC",
        "category": "business",
    },
    {
        "url":      "http://feeds.bbci.co.uk/news/business/rss.xml",
        "source":   "BBC Business",
        "category": "business",
    },
    {
        "url":      "http://feeds.bbci.co.uk/news/world/rss.xml",
        "source":   "BBC World",
        "category": "world",
    },
]


def scrape_news():
    print("[news] Starting news scrape...")
    init_db()
    conn = get_db_connection()
    total_saved = 0
    total_skipped = 0

    for feed_cfg in FEEDS:
        source = feed_cfg["source"]
        try:
            print(f"  [news] Fetching {source}...")
            feed = feedparser.parse(feed_cfg["url"])

            if feed.bozo and not feed.entries:
                print(f"  [news] WARNING: {source} feed could not be parsed — skipping")
                continue

            saved = 0
            skipped = 0

            for entry in feed.entries:
                title   = entry.get("title", "").strip()
                url     = entry.get("link", "").strip()
                summary = entry.get("summary", entry.get("description", "")).strip()

                if not title or not url:
                    skipped += 1
                    continue

                try:
                    conn.execute(
                        """
                        INSERT OR IGNORE INTO articles (title, summary, source, category, url, scraped_at)
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            title,
                            summary,
                            feed_cfg["source"],
                            feed_cfg["category"],
                            url,
                            datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
                        ),
                    )
                    if conn.execute("SELECT changes()").fetchone()[0] > 0:
                        saved += 1
                    else:
                        skipped += 1
                except Exception as e:
                    print(f"    [news] DB error for '{title}': {e}")
                    skipped += 1

            conn.commit()
            print(f"  [news] {source}: {saved} new, {skipped} duplicates/skipped")
            total_saved += saved
            total_skipped += skipped

        except Exception as e:
            print(f"  [news] ERROR fetching {source}: {e}")
            continue

    conn.close()
    print(f"[news] Done. {total_saved} saved, {total_skipped} skipped.\n")
