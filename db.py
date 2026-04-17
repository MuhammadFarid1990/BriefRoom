import sqlite3
import os
from contextlib import contextmanager

DB_PATH = os.getenv("DB_PATH", "pulseboard.db")


def get_db_connection():
    """Return an open connection with row_factory set."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


@contextmanager
def db():
    """Context manager: commits on success, rolls back on error, always closes."""
    conn = get_db_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    """Create all tables if they don't already exist."""
    with db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS articles (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                title       TEXT NOT NULL,
                summary     TEXT,
                source      TEXT,
                category    TEXT,
                url         TEXT UNIQUE,
                scraped_at  DATETIME DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS market_data (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol      TEXT NOT NULL,
                name        TEXT,
                price       REAL,
                change_pct  REAL,
                category    TEXT,
                fetched_at  DATETIME DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS agent_cache (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                query_hash  TEXT UNIQUE NOT NULL,
                query_text  TEXT,
                response    TEXT,
                created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS watchlist (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                company_name TEXT UNIQUE NOT NULL,
                added_at     DATETIME DEFAULT CURRENT_TIMESTAMP
            );
        """)


if __name__ == "__main__":
    init_db()
    print(f"Database initialised at {DB_PATH}")
