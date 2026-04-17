import yfinance as yf
from datetime import datetime
from db import get_db_connection, init_db

SYMBOLS = {
    "stocks": ["SPY", "QQQ", "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA"],
    "crypto": ["BTC-USD", "ETH-USD"],
    "commodities": ["GC=F", "CL=F"],
}

DISPLAY_NAMES = {
    "SPY":    "S&P 500 ETF",
    "QQQ":    "Nasdaq 100 ETF",
    "AAPL":   "Apple",
    "MSFT":   "Microsoft",
    "GOOGL":  "Alphabet",
    "AMZN":   "Amazon",
    "NVDA":   "NVIDIA",
    "BTC-USD": "Bitcoin",
    "ETH-USD": "Ethereum",
    "GC=F":   "Gold Futures",
    "CL=F":   "Crude Oil Futures",
}


def scrape_markets():
    print("[markets] Starting market data scrape...")
    init_db()
    conn = get_db_connection()
    fetched_at = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    saved = 0
    failed = 0

    for category, symbols in SYMBOLS.items():
        for symbol in symbols:
            try:
                ticker = yf.Ticker(symbol)
                info = ticker.fast_info

                price = info.last_price
                prev_close = info.previous_close

                if price is None or prev_close is None or prev_close == 0:
                    print(f"  [markets] Skipping {symbol} — incomplete data")
                    failed += 1
                    continue

                change_pct = ((price - prev_close) / prev_close) * 100
                name = DISPLAY_NAMES.get(symbol, symbol)

                conn.execute(
                    """
                    INSERT INTO market_data (symbol, name, price, change_pct, category, fetched_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (symbol, name, round(price, 4), round(change_pct, 4), category, fetched_at),
                )
                conn.commit()
                print(f"  [markets] {symbol} ({name}): ${price:.2f}  {change_pct:+.2f}%")
                saved += 1

            except Exception as e:
                print(f"  [markets] ERROR fetching {symbol}: {e}")
                failed += 1
                continue

    conn.close()
    print(f"[markets] Done. {saved} saved, {failed} skipped/failed.\n")
