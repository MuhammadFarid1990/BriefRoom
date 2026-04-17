"""
agents/so_what.py

"So What?" explainer agent: takes any headline or news snippet and returns
a structured consultant-grade analysis via Claude.
"""

import hashlib
import json
import os
import re
from datetime import datetime
from typing import Optional

from dotenv import load_dotenv
load_dotenv(override=True)

import anthropic

from db import get_db_connection

CACHE_KEY_PREFIX = "so_what_"

# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------

def _cache_key(text: str) -> str:
    return CACHE_KEY_PREFIX + hashlib.md5(text.strip().lower().encode()).hexdigest()


def _load_cache(key: str) -> Optional[dict]:
    try:
        conn = get_db_connection()
        row = conn.execute(
            """
            SELECT response, created_at FROM agent_cache
            WHERE query_hash = ?
            """,
            (key,),
        ).fetchone()
        conn.close()
        return dict(row) if row else None
    except Exception as e:
        print(f"[so_what] Cache read error: {e}")
        return None


def _save_cache(key: str, text: str, data: dict):
    try:
        conn = get_db_connection()
        conn.execute(
            """
            INSERT INTO agent_cache (query_hash, query_text, response, created_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(query_hash) DO UPDATE
              SET response=excluded.response, created_at=excluded.created_at
            """,
            (key, text[:500], json.dumps(data), datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[so_what] Cache write error: {e}")


# ---------------------------------------------------------------------------
# Main agent
# ---------------------------------------------------------------------------

def explain_so_what(text: str) -> dict:
    """
    Analyse a headline or news snippet and return a structured dict.
    Results are cached indefinitely (same input → same analysis).
    Raises on Claude API failure.
    """
    key    = _cache_key(text)
    cached = _load_cache(key)
    if cached:
        print(f"[so_what] Cache hit")
        return json.loads(cached["response"])

    prompt = f"""You are a senior business consultant advising a Fortune 500 board.
Analyze the following headline or news snippet and return ONLY a valid JSON object — no markdown fences, no commentary before or after.

NEWS:
{text.strip()}

Return exactly this JSON structure:
{{
  "what_happened": "<1-2 sentence plain-English explanation of the event and its immediate significance>",
  "who_wins": ["<winner + brief reason>", ...],
  "who_loses": ["<loser + brief reason>", ...],
  "second_order_effects": [
    "<specific downstream consequence 1>",
    "<specific downstream consequence 2>",
    "<specific downstream consequence 3>"
  ],
  "what_to_watch": "<the single most important leading indicator or next event to monitor>",
  "consultant_take": "<2-3 sentence sharp, opinionated take a partner would give a client — include a specific action or framing>"
}}

Guidelines:
- Be specific: name companies, sectors, geographies, or regulators where relevant.
- who_wins / who_loses: include both direct and indirect parties.
- second_order_effects: think one level beyond the obvious — supply chain, competitors, regulators, consumers.
- consultant_take: avoid generic statements. Give a concrete framing or action."""

    client  = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
    )

    raw = message.content[0].text.strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)

    result = json.loads(raw)
    _save_cache(key, text, result)
    return result
