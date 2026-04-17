"""
agents/country_risk.py

Country Risk Brief agent: collects news context from Google News RSS and the
local articles table, then asks Claude for a structured geopolitical risk brief.
"""

import json
import os
import re
from datetime import datetime
from typing import Optional
from urllib.parse import quote

from dotenv import load_dotenv
load_dotenv(override=True)

import anthropic
import feedparser

from db import get_db_connection

CACHE_TTL_HOURS = 12

# ---------------------------------------------------------------------------
# Data collection
# ---------------------------------------------------------------------------

def _fetch_google_news(query: str, limit: int = 10) -> list[dict]:
    try:
        url = (
            f"https://news.google.com/rss/search"
            f"?q={quote(query)}&hl=en-US&gl=US&ceid=US:en"
        )
        feed = feedparser.parse(url)
        results = []
        for entry in feed.entries[:limit]:
            results.append({
                "title":   entry.get("title", "").strip(),
                "summary": entry.get("summary", "").strip(),
                "source":  entry.get("source", {}).get("title", ""),
            })
        print(f"  [country_risk] Google News '{query}': {len(results)} results")
        return results
    except Exception as e:
        print(f"  [country_risk] Google News failed for '{query}': {e}")
        return []


def _fetch_db_articles(country: str, limit: int = 10) -> list[dict]:
    try:
        conn = get_db_connection()
        rows = conn.execute(
            """
            SELECT title, summary, source, scraped_at
            FROM articles
            WHERE title LIKE ? OR summary LIKE ?
            ORDER BY scraped_at DESC
            LIMIT ?
            """,
            (f"%{country}%", f"%{country}%", limit),
        ).fetchall()
        conn.close()
        results = [dict(r) for r in rows]
        print(f"  [country_risk] Local DB: {len(results)} articles for '{country}'")
        return results
    except Exception as e:
        print(f"  [country_risk] DB fetch failed for '{country}': {e}")
        return []


def _collect(country: str) -> dict:
    print(f"  [country_risk] Collecting data for '{country}'...")
    return {
        "economy_politics": _fetch_google_news(f"{country} economy politics", limit=10),
        "trade_investment":  _fetch_google_news(f"{country} trade investment", limit=5),
        "db_articles":       _fetch_db_articles(country),
    }


# ---------------------------------------------------------------------------
# Prompt builder
# ---------------------------------------------------------------------------

def _format_articles(articles: list[dict], label: str) -> str:
    if not articles:
        return f"  {label}: No data found.\n"
    lines = [f"  {label} ({len(articles)} items):"]
    for a in articles:
        title   = a.get("title", "")
        summary = (a.get("summary") or "")[:200]
        source  = a.get("source", "")
        lines.append(f"    • [{source}] {title}: {summary}")
    return "\n".join(lines) + "\n"


def _build_prompt(country: str, data: dict) -> str:
    context = (
        _format_articles(data["economy_politics"], "Economy & Politics (Google News)")
        + _format_articles(data["trade_investment"],  "Trade & Investment (Google News)")
        + _format_articles(data["db_articles"],       "Local news database")
    )

    return f"""You are a geopolitical risk analyst at a top-tier consulting firm.
Based on the provided news context about {country}, return ONLY a valid JSON object — no markdown fences, no commentary.

CONTEXT:
{context}

Return exactly this JSON structure:
{{
  "country": "{country}",
  "overall_risk_score": <integer 1-10>,
  "risk_level": "<Low|Moderate|Elevated|High|Critical>",
  "risk_trend": "<Improving|Stable|Deteriorating>",
  "political_risk": {{
    "score": <integer 1-10>,
    "summary": "<2 sentence summary>",
    "key_factors": ["<factor>", ...]
  }},
  "economic_risk": {{
    "score": <integer 1-10>,
    "summary": "<2 sentence summary>",
    "key_factors": ["<factor>", ...]
  }},
  "business_environment": {{
    "score": <integer 1-10>,
    "summary": "<2 sentence summary>",
    "key_factors": ["<factor>", ...]
  }},
  "opportunities": ["<specific opportunity>", ...],
  "threats": ["<specific threat>", ...],
  "consultant_recommendation": "<2-3 sentence actionable recommendation for a client considering entry, expansion, or exposure in this market>",
  "comparable_markets": ["<comparable country>", ...]
}}

Guidelines:
- Scores are 1-10 where 10 is highest risk.
- risk_level: Low (1-2), Moderate (3-4), Elevated (5-6), High (7-8), Critical (9-10).
- risk_trend reflects whether conditions appear to be getting better, worse, or holding steady based on the news.
- opportunities and threats: list 3-4 each, be specific to this country and moment.
- comparable_markets: 2-3 countries with a similar risk profile for benchmarking.
- If context is limited, say so in the summaries and use your training knowledge to fill gaps — but flag uncertainty explicitly."""


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

def _cache_key(country: str) -> str:
    return f"country_risk_{country.strip().lower().replace(' ', '_')}"


def _load_cache(key: str) -> Optional[dict]:
    try:
        conn = get_db_connection()
        row = conn.execute(
            """
            SELECT response, created_at FROM agent_cache
            WHERE query_hash = ?
              AND datetime(created_at) > datetime('now', ?)
            """,
            (key, f"-{CACHE_TTL_HOURS} hours"),
        ).fetchone()
        conn.close()
        return dict(row) if row else None
    except Exception as e:
        print(f"  [country_risk] Cache read error: {e}")
        return None


def _save_cache(key: str, country: str, data: dict):
    try:
        conn = get_db_connection()
        conn.execute(
            """
            INSERT INTO agent_cache (query_hash, query_text, response, created_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(query_hash) DO UPDATE
              SET response=excluded.response, created_at=excluded.created_at
            """,
            (key, country, json.dumps(data), datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"  [country_risk] Cache write error: {e}")


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def get_country_risk(country_name: str) -> dict:
    """
    Full pipeline: collect → prompt → Claude → cache → return parsed dict.
    Raises on Claude API failure; all other failures degrade gracefully.
    """
    key    = _cache_key(country_name)
    cached = _load_cache(key)
    if cached:
        print(f"[country_risk] Cache hit for '{country_name}'")
        return json.loads(cached["response"])

    print(f"[country_risk] Generating risk brief for '{country_name}'...")
    data   = _collect(country_name)
    prompt = _build_prompt(country_name, data)

    client  = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1500,
        messages=[{"role": "user", "content": prompt}],
    )

    raw = message.content[0].text.strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)

    result = json.loads(raw)
    _save_cache(key, country_name, result)
    print(f"[country_risk] Brief complete and cached.")
    return result
