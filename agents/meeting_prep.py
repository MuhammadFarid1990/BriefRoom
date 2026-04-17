"""
agents/meeting_prep.py

Meeting Prep Brief agent: collects news, market context, and industry signals
for a company, then asks Claude for a structured meeting brief.
"""

import hashlib
import io
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
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import (
    HRFlowable,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from db import get_db_connection

CACHE_TTL_HOURS = 6


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
                "summary": entry.get("summary", "").strip()[:300],
                "source":  entry.get("source", {}).get("title", ""),
                "link":    entry.get("link", ""),
            })
        print(f"  [meeting_prep] Google News '{query}': {len(results)} results")
        return results
    except Exception as e:
        print(f"  [meeting_prep] Google News failed for '{query}': {e}")
        return []


def _fetch_db_articles(company: str, limit: int = 10) -> list[dict]:
    try:
        conn = get_db_connection()
        rows = conn.execute(
            """
            SELECT title, summary, source, scraped_at
            FROM articles
            WHERE title LIKE ? OR summary LIKE ?
            ORDER BY scraped_at DESC LIMIT ?
            """,
            (f"%{company}%", f"%{company}%", limit),
        ).fetchall()
        conn.close()
        results = [dict(r) for r in rows]
        print(f"  [meeting_prep] Local DB: {len(results)} articles for '{company}'")
        return results
    except Exception as e:
        print(f"  [meeting_prep] DB fetch failed: {e}")
        return []


def _fetch_market_data(company: str) -> Optional[dict]:
    """Try to get live market data if the company name maps to a known ticker."""
    try:
        import yfinance as yf
        # Use the company name as a search term — yfinance search
        ticker = yf.Ticker(company.upper())
        info   = ticker.fast_info
        price  = getattr(info, "last_price", None)
        if price and price > 0:
            prev  = getattr(info, "previous_close", price)
            chg   = ((price - prev) / prev * 100) if prev else 0
            return {
                "symbol":     company.upper(),
                "price":      round(price, 2),
                "change_pct": round(chg, 2),
            }
    except Exception:
        pass
    return None


def _collect(company: str, context: str) -> dict:
    print(f"  [meeting_prep] Collecting data for '{company}'...")
    # Derive an industry/sector search query from the context or just use the company
    industry_query = f"{company} industry sector trends" if not context else f"{company} {context} industry"
    return {
        "company_news":   _fetch_google_news(company, limit=10),
        "industry_news":  _fetch_google_news(industry_query, limit=5),
        "db_articles":    _fetch_db_articles(company),
        "market_data":    _fetch_market_data(company),
    }


# ---------------------------------------------------------------------------
# Prompt builder
# ---------------------------------------------------------------------------

def _format_news(articles: list[dict], label: str) -> str:
    if not articles:
        return f"  {label}: No data found.\n"
    lines = [f"  {label}:"]
    for a in articles:
        src   = a.get("source", "")
        title = a.get("title", "")
        blurb = (a.get("summary") or "")[:200]
        lines.append(f"    • [{src}] {title}: {blurb}")
    return "\n".join(lines) + "\n"


def _build_prompt(company: str, context: str, data: dict) -> str:
    mkt = data.get("market_data")
    mkt_line = ""
    if mkt:
        sign = "+" if mkt["change_pct"] >= 0 else ""
        mkt_line = f"\nMarket data: {mkt['symbol']} at ${mkt['price']:,.2f} ({sign}{mkt['change_pct']:.2f}% today)\n"

    news_context = (
        _format_news(data["company_news"],  f"Recent news about {company}")
        + _format_news(data["industry_news"], "Industry / sector news")
        + _format_news(data["db_articles"],   "Local news database")
        + mkt_line
    )

    ctx_line = f" The meeting context is: {context}." if context.strip() else ""

    return f"""You are a senior consultant preparing a client-facing team for a meeting with {company}.{ctx_line}
Based on the provided news context, return ONLY a valid JSON object — no markdown fences, no commentary.

CONTEXT:
{news_context}

Return exactly this JSON structure:
{{
  "company": "{company}",
  "meeting_context": "{context}",
  "executive_summary": "<3-4 sentence synthesis of the company's current state and what matters most right now>",
  "company_snapshot": {{
    "what_they_do": "<1-2 sentences on core business>",
    "size_and_scale": "<estimated revenue, employees, geographic reach if known>",
    "recent_performance": "<financial or operational highlights from last 6-12 months>",
    "leadership_signals": "<any notable leadership moves, strategy pivots, or culture signals>"
  }},
  "hot_topics": [
    "<Topic 1: something the company is actively focused on right now>",
    "<Topic 2>",
    "<Topic 3>"
  ],
  "things_to_avoid": [
    "<sensitive topic, controversy, or bad news — brief reason>",
    "<another thing to avoid>"
  ],
  "smart_questions": [
    "<Question 1 that shows you did your homework>",
    "<Question 2>",
    "<Question 3>",
    "<Question 4>",
    "<Question 5>"
  ],
  "opportunities_to_pitch": [
    "<Consulting angle 1 — specific to their current situation>",
    "<Consulting angle 2>",
    "<Consulting angle 3>"
  ],
  "latest_news": [
    {{"headline": "<title>", "source": "<source>"}},
    {{"headline": "<title>", "source": "<source>"}},
    {{"headline": "<title>", "source": "<source>"}}
  ],
  "one_line_opener": "<One impressive, specific sentence to open the meeting that shows you know what's happening with them right now>"
}}

Guidelines:
- Be specific. Name products, executives, geographies, and competitors where relevant.
- hot_topics should reflect what the company's leadership is actively working on based on the news.
- things_to_avoid: include recent controversies, layoffs, regulatory issues, or bad earnings — topics that could create tension.
- smart_questions: should feel like they come from someone who has read their annual report and followed their news for months.
- opportunities_to_pitch: tie consulting angles directly to the company's current challenges or growth moves.
- one_line_opener: must be specific enough that it could not apply to any other company this week.
- If context is thin, use your training knowledge but flag uncertainty in the executive_summary."""


# ---------------------------------------------------------------------------
# JSON extraction (same 3-strategy pattern used in CvC)
# ---------------------------------------------------------------------------

def _extract_json(raw: str) -> dict:
    # Strategy 1: direct parse
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    # Strategy 2: find outermost braces
    start = raw.find("{")
    end   = raw.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(raw[start:end + 1])
        except json.JSONDecodeError:
            pass

    # Strategy 3: truncation walk-back
    if start != -1:
        candidate = raw[start:]
        while candidate:
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                candidate = candidate[:candidate.rfind("}")]
                if not candidate:
                    break

    raise ValueError(
        f"Could not extract valid JSON from Claude response "
        f"(length: {len(raw)}). Preview: {raw[:300]}"
    )


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

def _cache_key(company: str) -> str:
    slug = company.strip().lower().replace(" ", "_")
    date = datetime.utcnow().strftime("%Y-%m-%d")
    return f"meeting_prep_{slug}_{date}"


def _load_cache(key: str) -> Optional[dict]:
    try:
        conn = get_db_connection()
        row  = conn.execute(
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
        print(f"  [meeting_prep] Cache read error: {e}")
        return None


def _save_cache(key: str, company: str, data: dict):
    try:
        conn = get_db_connection()
        conn.execute(
            """
            INSERT INTO agent_cache (query_hash, query_text, response, created_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(query_hash) DO UPDATE
              SET response=excluded.response, created_at=excluded.created_at
            """,
            (key, company, json.dumps(data), datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"  [meeting_prep] Cache write error: {e}")


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def get_meeting_prep(company: str, context: str = "") -> dict:
    """
    Full pipeline: collect → prompt → Claude → cache → return parsed dict.
    """
    key    = _cache_key(company)
    cached = _load_cache(key)
    if cached:
        print(f"[meeting_prep] Cache hit for '{company}'")
        return json.loads(cached["response"])

    print(f"[meeting_prep] Generating meeting brief for '{company}'...")
    data   = _collect(company, context)
    prompt = _build_prompt(company, context, data)

    client  = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=2000,
        messages=[{"role": "user", "content": prompt}],
    )

    raw    = message.content[0].text.strip()
    raw    = re.sub(r"^```(?:json)?\s*", "", raw)
    raw    = re.sub(r"\s*```$",          "", raw)
    result = _extract_json(raw)

    _save_cache(key, company, result)
    print(f"[meeting_prep] Brief complete and cached.")
    return result


# ---------------------------------------------------------------------------
# PDF report generator
# ---------------------------------------------------------------------------

def generate_meeting_pdf(data: dict, company: str) -> bytes:
    """Generate a clean 2-page meeting prep PDF and return raw bytes."""
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=2 * cm,
        rightMargin=2 * cm,
        topMargin=2 * cm,
        bottomMargin=2 * cm,
    )
    base = getSampleStyleSheet()
    W    = A4[0] - 4 * cm

    S = {
        "cover_title": ParagraphStyle("cover_title", parent=base["Title"],
            fontSize=24, leading=30, textColor=colors.HexColor("#1e3a5f"),
            alignment=TA_CENTER, spaceAfter=6),
        "cover_sub": ParagraphStyle("cover_sub", parent=base["Normal"],
            fontSize=13, textColor=colors.HexColor("#64748b"),
            alignment=TA_CENTER, spaceAfter=4),
        "cover_date": ParagraphStyle("cover_date", parent=base["Normal"],
            fontSize=10, textColor=colors.HexColor("#94a3b8"),
            alignment=TA_CENTER),
        "opener": ParagraphStyle("opener", parent=base["Normal"],
            fontSize=11, leading=17, textColor=colors.HexColor("#1e293b"),
            backColor=colors.HexColor("#dbeafe"),
            leftIndent=10, rightIndent=10, spaceAfter=10, spaceBefore=4),
        "h1": ParagraphStyle("h1", parent=base["Heading1"],
            fontSize=14, leading=18, textColor=colors.HexColor("#1e3a5f"),
            spaceBefore=14, spaceAfter=4),
        "h2": ParagraphStyle("h2", parent=base["Heading2"],
            fontSize=11, leading=15, textColor=colors.HexColor("#334155"),
            spaceBefore=8, spaceAfter=3),
        "body": ParagraphStyle("body", parent=base["Normal"],
            fontSize=9, leading=14, textColor=colors.HexColor("#1e293b"),
            spaceAfter=4),
        "bullet": ParagraphStyle("bullet", parent=base["Normal"],
            fontSize=9, leading=14, textColor=colors.HexColor("#1e293b"),
            leftIndent=14, firstLineIndent=-12, spaceAfter=3),
        "numbered": ParagraphStyle("numbered", parent=base["Normal"],
            fontSize=9, leading=14, textColor=colors.HexColor("#1e293b"),
            leftIndent=18, firstLineIndent=-14, spaceAfter=4),
        "avoid": ParagraphStyle("avoid", parent=base["Normal"],
            fontSize=9, leading=14, textColor=colors.HexColor("#7f1d1d"),
            backColor=colors.HexColor("#fef2f2"),
            leftIndent=8, rightIndent=8, spaceAfter=3),
        "opportunity": ParagraphStyle("opportunity", parent=base["Normal"],
            fontSize=9, leading=14, textColor=colors.HexColor("#14532d"),
            backColor=colors.HexColor("#f0fdf4"),
            leftIndent=8, rightIndent=8, spaceAfter=3),
        "news": ParagraphStyle("news", parent=base["Normal"],
            fontSize=8, leading=12, textColor=colors.HexColor("#475569"),
            spaceAfter=2),
        "disclaimer": ParagraphStyle("disclaimer", parent=base["Normal"],
            fontSize=7, leading=11, textColor=colors.HexColor("#94a3b8"),
            alignment=TA_CENTER),
    }

    def hr():
        return HRFlowable(width="100%", thickness=0.5,
                          color=colors.HexColor("#e2e8f0"), spaceAfter=6)

    def bullets(items: list, prefix: str = "•") -> list:
        return [Paragraph(f"{prefix}  {item}", S["bullet"]) for item in (items or ["N/A"])]

    def numbered_list(items: list) -> list:
        return [Paragraph(f"{i}.  {item}", S["numbered"])
                for i, item in enumerate(items or ["N/A"], 1)]

    snap  = data.get("company_snapshot", {})
    story = []

    # ── Cover ──────────────────────────────────────────────────────────────
    story.append(Spacer(1, 1.5 * cm))
    story.append(Paragraph("Meeting Prep Brief", S["cover_title"]))
    meeting_ctx = data.get("meeting_context", "")
    sub_line    = f"{company}" + (f" — {meeting_ctx}" if meeting_ctx else "")
    story.append(Paragraph(sub_line, S["cover_sub"]))
    story.append(Paragraph(
        f"Prepared {datetime.utcnow().strftime('%B %d, %Y')} · BriefRoom · Powered by Claude",
        S["cover_date"],
    ))
    story.append(Spacer(1, 0.6 * cm))
    story.append(hr())

    # ── Open with this ────────────────────────────────────────────────────
    opener = data.get("one_line_opener", "")
    if opener:
        story.append(Paragraph("OPEN WITH THIS", S["h2"]))
        story.append(Paragraph(f'"{opener}"', S["opener"]))
        story.append(hr())

    # ── Executive Summary ─────────────────────────────────────────────────
    story.append(Paragraph("Executive Summary", S["h1"]))
    story.append(Paragraph(data.get("executive_summary", "N/A"), S["body"]))
    story.append(hr())

    # ── Company Snapshot ──────────────────────────────────────────────────
    story.append(Paragraph("Company Snapshot", S["h1"]))
    snap_rows = [
        ("What They Do",       snap.get("what_they_do",       "N/A")),
        ("Size & Scale",       snap.get("size_and_scale",     "N/A")),
        ("Recent Performance", snap.get("recent_performance", "N/A")),
        ("Leadership Signals", snap.get("leadership_signals", "N/A")),
    ]
    tdata = [[Paragraph(f"<b>{k}</b>", S["body"]),
              Paragraph(str(v),         S["body"])] for k, v in snap_rows]
    t = Table(tdata, colWidths=[W * 0.30, W * 0.70])
    t.setStyle(TableStyle([
        ("VALIGN",        (0, 0), (-1, -1), "TOP"),
        ("ROWBACKGROUNDS",(0, 0), (-1, -1),
         [colors.HexColor("#f8fafc"), colors.white]),
        ("GRID",          (0, 0), (-1, -1), 0.3, colors.HexColor("#e2e8f0")),
        ("LEFTPADDING",   (0, 0), (-1, -1), 6),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 6),
        ("TOPPADDING",    (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(t)

    story.append(PageBreak())

    # ── Hot Topics ────────────────────────────────────────────────────────
    story.append(Paragraph("Hot Topics Right Now", S["h1"]))
    story += numbered_list(data.get("hot_topics", []))
    story.append(hr())

    # ── Smart Questions ───────────────────────────────────────────────────
    story.append(Paragraph("Smart Questions to Ask", S["h1"]))
    for i, q in enumerate(data.get("smart_questions", []), 1):
        story.append(Paragraph(f"{i}.  {q}", S["numbered"]))
    story.append(hr())

    # ── Opportunities & Things to Avoid ──────────────────────────────────
    story.append(Paragraph("Consulting Opportunities", S["h1"]))
    for item in (data.get("opportunities_to_pitch") or []):
        story.append(Paragraph(f"→  {item}", S["opportunity"]))
    story.append(Spacer(1, 0.3 * cm))

    story.append(Paragraph("Topics to Avoid", S["h1"]))
    for item in (data.get("things_to_avoid") or []):
        story.append(Paragraph(f"✗  {item}", S["avoid"]))
    story.append(hr())

    # ── Latest News ───────────────────────────────────────────────────────
    story.append(Paragraph("Latest News", S["h1"]))
    for item in (data.get("latest_news") or []):
        hl  = item.get("headline", "")
        src = item.get("source", "")
        story.append(Paragraph(
            f'<b>[{src}]</b> {hl}' if src else hl, S["news"]
        ))
    story.append(Spacer(1, 0.5 * cm))

    # ── Disclaimer ────────────────────────────────────────────────────────
    story.append(hr())
    story.append(Paragraph(
        "Generated by BriefRoom · Powered by Claude · For internal preparation only · Not investment advice.",
        S["disclaimer"],
    ))

    doc.build(story)
    return buf.getvalue()
