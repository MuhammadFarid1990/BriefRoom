"""
seed.py — Populate the database with 48 hours of realistic sample data.
Run with:  python3 seed.py
"""

from datetime import datetime, timedelta
import random
from db import get_db_connection, init_db

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def ago(hours: float, jitter_minutes: int = 30) -> str:
    """Return a UTC timestamp string `hours` ago, with random jitter."""
    delta = timedelta(hours=hours, minutes=random.randint(0, jitter_minutes))
    return (datetime.utcnow() - delta).strftime("%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------------------------
# Sample articles  (20 total)
# ---------------------------------------------------------------------------

ARTICLES = [
    # --- business / Reuters ---
    ("Fed signals two rate cuts possible in 2025 amid cooling inflation",
     "Federal Reserve officials indicated they may implement two quarter-point rate cuts later this year if inflation continues its downward trajectory.",
     "Reuters", "business",
     "https://www.reuters.com/markets/fed-rate-cuts-2025-01", 46),

    ("Oil prices slip as OPEC+ considers raising output quotas",
     "Crude oil futures fell more than 1% after reports that OPEC+ members are debating a production increase at their next meeting.",
     "Reuters", "business",
     "https://www.reuters.com/markets/oil-opec-output-2025-02", 43),

    ("Amazon reports record Q4 revenue driven by AWS cloud growth",
     "Amazon's fourth-quarter revenue surpassed analyst expectations, with AWS cloud division growing 17% year-over-year.",
     "Reuters", "business",
     "https://www.reuters.com/technology/amazon-q4-results-2025-03", 40),

    ("European Central Bank holds rates steady, flags downside risks",
     "The ECB kept its benchmark rate unchanged at 3.5% citing persistent uncertainty in the eurozone economic outlook.",
     "Reuters", "business",
     "https://www.reuters.com/markets/ecb-rates-hold-2025-04", 37),

    ("Goldman Sachs upgrades US growth forecast to 2.4% for 2025",
     "Goldman Sachs economists revised their US GDP growth estimate upward, citing resilient consumer spending and a strong labour market.",
     "Reuters", "business",
     "https://www.reuters.com/markets/goldman-us-gdp-2025-05", 34),

    # --- world / BBC World ---
    ("G7 leaders agree on new sanctions framework targeting AI exports",
     "G7 nations reached a consensus on restricting exports of advanced AI chips and software to adversarial states.",
     "BBC World", "world",
     "https://www.bbc.co.uk/news/world/g7-ai-sanctions-2025-01", 30),

    ("UN warns of deepening humanitarian crisis in conflict zones",
     "The United Nations issued an urgent appeal for $4.2 billion to address worsening conditions affecting over 12 million people.",
     "BBC World", "world",
     "https://www.bbc.co.uk/news/world/un-humanitarian-2025-02", 27),

    ("India overtakes Japan to become world's fourth-largest economy",
     "IMF data confirms India's nominal GDP surpassed Japan's in the latest quarterly figures, a milestone economists had forecast for mid-decade.",
     "BBC World", "world",
     "https://www.bbc.co.uk/news/world/india-gdp-japan-2025-03", 24),

    ("COP30 draft agreement targets 45% emissions cut by 2035",
     "Negotiators in Belém released a draft climate text calling for deeper near-term emissions reductions and expanded carbon markets.",
     "BBC World", "world",
     "https://www.bbc.co.uk/news/world/cop30-draft-2025-04", 21),

    ("NATO members commit to 2.5% GDP defence spending target",
     "Alliance members agreed to raise the collective defence spending benchmark from 2% to 2.5% of GDP at the Brussels summit.",
     "BBC World", "world",
     "https://www.bbc.co.uk/news/world/nato-spending-2025-05", 18),

    # --- business / BBC Business ---
    ("Bitcoin surges past $95,000 as ETF inflows hit monthly record",
     "Bitcoin climbed above $95,000 for the first time in six weeks as spot ETF products recorded their highest single-month inflows since launch.",
     "BBC Business", "business",
     "https://www.bbc.co.uk/news/business/bitcoin-etf-2025-01", 16),

    ("Apple faces EU antitrust fine over App Store payment rules",
     "The European Commission is preparing a fine of up to €500 million against Apple for failing to comply with Digital Markets Act obligations.",
     "BBC Business", "business",
     "https://www.bbc.co.uk/news/business/apple-eu-antitrust-2025-02", 13),

    ("UK inflation falls to 2.1%, lowest since 2021",
     "The Office for National Statistics reported that UK consumer price inflation eased to 2.1% in March, just above the Bank of England's 2% target.",
     "BBC Business", "business",
     "https://www.bbc.co.uk/news/business/uk-inflation-2025-03", 10),

    ("Microsoft Copilot adoption reaches 60 million enterprise users",
     "Microsoft reported that its AI Copilot assistant has been activated by more than 60 million enterprise users across Microsoft 365 products.",
     "BBC Business", "business",
     "https://www.bbc.co.uk/news/business/microsoft-copilot-2025-04", 7),

    ("NVIDIA unveils Blackwell Ultra GPU, claims 3x inference speedup",
     "NVIDIA's latest GPU architecture promises a threefold improvement in inference throughput for large language models over the previous generation.",
     "BBC Business", "business",
     "https://www.bbc.co.uk/news/business/nvidia-blackwell-ultra-2025-05", 4),

    # --- tech / Hacker News ---
    ("Show HN: I built a local-first AI journal that never sends data to the cloud",
     "An open-source journaling app using on-device LLMs for summarisation and mood tracking, with full offline support.",
     "Hacker News", "tech",
     "https://news.ycombinator.com/item?id=40000001", 45),

    ("Ask HN: What's your stack for processing 1M+ events per second?",
     "Discussion thread on high-throughput event processing architectures, covering Kafka, Flink, ClickHouse, and custom solutions.",
     "Hacker News", "tech",
     "https://news.ycombinator.com/item?id=40000002", 38),

    ("SQLite is underrated for production workloads (2025)",
     "A deep-dive into SQLite's WAL mode, connection pooling, and benchmarks showing competitive performance against Postgres for read-heavy apps.",
     "Hacker News", "tech",
     "https://news.ycombinator.com/item?id=40000003", 22),

    # --- tech / TechCrunch ---
    ("OpenAI releases GPT-5 with native multimodal reasoning and 200K context",
     "OpenAI's latest flagship model supports text, images, audio, and video natively and ships with an expanded 200,000-token context window.",
     "TechCrunch", "tech",
     "https://techcrunch.com/2025/04/openai-gpt5-release", 12),

    ("Stripe launches AI-powered fraud detection that blocks 99.1% of card fraud",
     "Stripe's new adaptive fraud engine uses real-time transaction embeddings to cut false positives by 40% while maintaining a 99.1% fraud block rate.",
     "TechCrunch", "tech",
     "https://techcrunch.com/2025/04/stripe-ai-fraud-detection", 5),
]


# ---------------------------------------------------------------------------
# Sample market data  (11 symbols × multiple historical snapshots)
# ---------------------------------------------------------------------------

MARKET_SNAPSHOTS = [
    # (symbol, name, base_price, category)
    ("SPY",     "S&P 500 ETF",       521.40, "stocks"),
    ("QQQ",     "Nasdaq 100 ETF",    441.25, "stocks"),
    ("AAPL",    "Apple",             189.72, "stocks"),
    ("MSFT",    "Microsoft",         415.88, "stocks"),
    ("GOOGL",   "Alphabet",          174.50, "stocks"),
    ("AMZN",    "Amazon",            192.30, "stocks"),
    ("NVDA",    "NVIDIA",            875.60, "stocks"),
    ("BTC-USD", "Bitcoin",         94250.00, "crypto"),
    ("ETH-USD", "Ethereum",         3185.00, "crypto"),
    ("GC=F",    "Gold Futures",     2345.80, "commodities"),
    ("CL=F",    "Crude Oil Futures",  81.45, "commodities"),
]

# Produce 4 snapshots per symbol spread over 48 hours
FETCH_OFFSETS_HOURS = [48, 32, 16, 2]


def seed_articles(conn):
    print("[seed] Inserting sample articles...")
    count = 0
    for title, summary, source, category, url, hours_ago in ARTICLES:
        try:
            conn.execute(
                """
                INSERT OR IGNORE INTO articles (title, summary, source, category, url, scraped_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (title, summary, source, category, url, ago(hours_ago)),
            )
            if conn.execute("SELECT changes()").fetchone()[0] > 0:
                count += 1
        except Exception as e:
            print(f"  [seed] Skipping article '{title[:50]}...': {e}")
    conn.commit()
    print(f"[seed] Articles: {count} inserted.\n")


def seed_market_data(conn):
    print("[seed] Inserting sample market data...")
    count = 0
    for symbol, name, base_price, category in MARKET_SNAPSHOTS:
        for offset_h in FETCH_OFFSETS_HOURS:
            # Simulate slight price drift for each snapshot
            noise = random.uniform(-0.015, 0.015)
            price = round(base_price * (1 + noise), 4)
            change_pct = round(random.uniform(-3.5, 3.5), 4)
            fetched_at = ago(offset_h, jitter_minutes=10)
            try:
                conn.execute(
                    """
                    INSERT INTO market_data (symbol, name, price, change_pct, category, fetched_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (symbol, name, price, change_pct, category, fetched_at),
                )
                count += 1
            except Exception as e:
                print(f"  [seed] Skipping market row {symbol}@{offset_h}h: {e}")
    conn.commit()
    print(f"[seed] Market data: {count} rows inserted.\n")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("=" * 50)
    print("  BriefRoom — Database Seeder")
    print("=" * 50 + "\n")

    init_db()
    conn = get_db_connection()

    seed_articles(conn)
    seed_market_data(conn)

    conn.close()
    print("Seeding complete. Database is ready.")
