"""
agents/company_vs_company.py

Competitive intelligence agent: collects data from the local DB, Google News
RSS, and Indeed, then asks Claude for a structured JSON analysis.
"""

import hashlib
import io
import json
import re
from datetime import datetime
import os
from typing import Optional
from urllib.parse import quote

from dotenv import load_dotenv
load_dotenv(override=True)

import anthropic
import feedparser
import requests
from bs4 import BeautifulSoup
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

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    )
}

# ---------------------------------------------------------------------------
# Data collection helpers
# ---------------------------------------------------------------------------

def fetch_db_articles(company: str, limit: int = 10) -> list[dict]:
    """Pull articles from local DB whose title/summary mention the company."""
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
            (f"%{company}%", f"%{company}%", limit),
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception as e:
        print(f"  [cvc] DB article fetch failed for '{company}': {e}")
        return []


def fetch_db_market(company: str) -> Optional[dict]:
    """Return the latest market row whose symbol or name matches the company."""
    try:
        conn = get_db_connection()
        row = conn.execute(
            """
            SELECT symbol, name, price, change_pct, fetched_at
            FROM market_data
            WHERE symbol LIKE ? OR name LIKE ?
            ORDER BY fetched_at DESC
            LIMIT 1
            """,
            (f"%{company}%", f"%{company}%"),
        ).fetchone()
        conn.close()
        return dict(row) if row else None
    except Exception as e:
        print(f"  [cvc] DB market fetch failed for '{company}': {e}")
        return None


def fetch_google_news(company: str, limit: int = 5) -> list[dict]:
    """Scrape top results from Google News RSS for the company."""
    try:
        url = (
            f"https://news.google.com/rss/search"
            f"?q={quote(company)}&hl=en-US&gl=US&ceid=US:en"
        )
        feed = feedparser.parse(url)
        results = []
        for entry in feed.entries[:limit]:
            results.append({
                "title":   entry.get("title", "").strip(),
                "summary": entry.get("summary", "").strip(),
                "source":  entry.get("source", {}).get("title", "Google News"),
            })
        print(f"  [cvc] Google News: {len(results)} results for '{company}'")
        return results
    except Exception as e:
        print(f"  [cvc] Google News failed for '{company}': {e}")
        return []


def fetch_indeed_count(company: str) -> str:
    """Scrape job count from Indeed for the company. Returns string or 'N/A'."""
    try:
        url = f"https://www.indeed.com/jobs?q={quote(company)}&l="
        resp = requests.get(url, headers=HEADERS, timeout=10)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        # Indeed renders job count in a few different selectors
        for selector in [
            "[data-testid='jobsearch-JobCountAndSortPane-jobCount']",
            ".jobsearch-JobCountAndSortPane-jobCount",
            "#searchCountPages",
        ]:
            el = soup.select_one(selector)
            if el:
                text = el.get_text(strip=True)
                # Extract the first number sequence from the text
                match = re.search(r"[\d,]+", text)
                if match:
                    count = match.group().replace(",", "")
                    print(f"  [cvc] Indeed: {count} jobs for '{company}'")
                    return count

        print(f"  [cvc] Indeed: count not found for '{company}'")
        return "N/A"
    except Exception as e:
        print(f"  [cvc] Indeed failed for '{company}': {e}")
        return "N/A"


def collect_company_data(company: str) -> dict:
    """Aggregate all data sources for one company."""
    print(f"  [cvc] Collecting data for '{company}'...")
    return {
        "db_articles":   fetch_db_articles(company),
        "market":        fetch_db_market(company),
        "google_news":   fetch_google_news(company),
        "indeed_jobs":   fetch_indeed_count(company),
    }


# ---------------------------------------------------------------------------
# Prompt builder
# ---------------------------------------------------------------------------

def _format_articles(articles: list[dict], label: str) -> str:
    if not articles:
        return f"  {label}: No articles found.\n"
    lines = [f"  {label} ({len(articles)} articles):"]
    for a in articles:
        title   = a.get("title", "")
        summary = (a.get("summary") or "")[:200]
        source  = a.get("source", "")
        lines.append(f"    • [{source}] {title}: {summary}")
    return "\n".join(lines) + "\n"


def build_prompt(company_a: str, data_a: dict, company_b: str, data_b: dict) -> str:
    def fmt(company: str, data: dict) -> str:
        mkt = data["market"]
        mkt_str = (
            f"  Market data: {mkt['symbol']} at ${mkt['price']:,.2f}, "
            f"{mkt['change_pct']:+.2f}% change"
            if mkt else "  Market data: Not publicly traded or not found."
        )
        return (
            f"=== {company.upper()} ===\n"
            + _format_articles(data["db_articles"],  "Local DB news")
            + _format_articles(data["google_news"],  "Google News")
            + mkt_str + "\n"
            + f"  Indeed job postings: {data['indeed_jobs']}\n"
        )

    context = fmt(company_a, data_a) + "\n" + fmt(company_b, data_b)

    return f"""You are a senior strategy consultant at a top-tier firm specialising in financial and technology sectors.

Analyse the following competitive intelligence data for {company_a} vs {company_b} and return ONLY a valid JSON object — no markdown fences, no commentary, no extra text before or after.

If the data is clearly insufficient to make informed assessments, set "insufficient_data": true and briefly explain in the "verdict" field. Otherwise set "insufficient_data": false.

CONTEXT DATA:
{context}

Return this exact JSON structure:
{{
  "insufficient_data": false,
  "company_a": {{
    "name": "{company_a}",
    "momentum_score": <integer 1-10>,
    "key_strengths": ["<strength>", ...],
    "key_risks": ["<risk>", ...],
    "recent_moves": ["<move>", ...],
    "financial_signals": {{
      "revenue_trend": "<inferred from earnings/growth mentions>",
      "cost_pressure_signals": "<layoffs, cost-cutting, margin pressure>",
      "investment_activity": "<capex, acquisitions, funding rounds>",
      "analyst_sentiment": "<upgrade/downgrade signals or neutral>"
    }},
    "tech_signals": {{
      "product_launches": ["<product or feature>", ...],
      "engineering_hiring_trend": "<growing/stable/shrinking + evidence>",
      "ai_ml_activity": "<any AI/ML initiatives or signals>",
      "technical_debt_signals": "<legacy system rewrites, outages, migrations>"
    }},
    "consulting_angles": [
      "<specific pitch angle 1>",
      "<specific pitch angle 2>",
      "<specific pitch angle 3>"
    ]
  }},
  "company_b": {{
    "name": "{company_b}",
    "momentum_score": <integer 1-10>,
    "key_strengths": ["<strength>", ...],
    "key_risks": ["<risk>", ...],
    "recent_moves": ["<move>", ...],
    "financial_signals": {{
      "revenue_trend": "<inferred from earnings/growth mentions>",
      "cost_pressure_signals": "<layoffs, cost-cutting, margin pressure>",
      "investment_activity": "<capex, acquisitions, funding rounds>",
      "analyst_sentiment": "<upgrade/downgrade signals or neutral>"
    }},
    "tech_signals": {{
      "product_launches": ["<product or feature>", ...],
      "engineering_hiring_trend": "<growing/stable/shrinking + evidence>",
      "ai_ml_activity": "<any AI/ML initiatives or signals>",
      "technical_debt_signals": "<legacy system rewrites, outages, migrations>"
    }},
    "consulting_angles": [
      "<specific pitch angle 1>",
      "<specific pitch angle 2>",
      "<specific pitch angle 3>"
    ]
  }},
  "verdict": "<3-4 sentence comparative summary>",
  "who_has_momentum": "<company name + one sentence why>",
  "key_battleground": "<the main dimension where they compete>",
  "watch_for": "<the single most important near-term signal to monitor>",
  "strategic_opportunities": [
    "<opportunity arising from the competitive gap>",
    "<opportunity 2>",
    "<opportunity 3>"
  ],
  "merger_acquisition_signals": "<any signals either could be acquirer or target>",
  "regulatory_risk": "<relevant regulatory pressures on either company>",
  "consulting_recommendation": "<one paragraph a consultant would use as an opening pitch to a client about this competitive landscape>"
}}

Guidelines:
- momentum_score: 1-10 based on news sentiment, market performance, hiring signals. 10 = exceptional momentum.
- financial_signals: infer from news mentions of earnings, layoffs, cost cuts, funding rounds, debt.
- tech_signals: infer from job postings (especially engineering/AI roles), product announcements, any GitHub/OSS activity in the news.
- consulting_angles: be specific — name real pain points visible in the data, not generic advice.
- strategic_opportunities: focus on the GAP between the two companies, not just individual strengths.
- Only use the provided context. If a field cannot be determined from the data, say so explicitly rather than hallucinating."""


# ---------------------------------------------------------------------------
# Caching
# ---------------------------------------------------------------------------

CACHE_TTL_HOURS = 6


def _cache_key(company_a: str, company_b: str) -> str:
    pair = "_vs_".join(sorted([company_a.lower().strip(), company_b.lower().strip()]))
    return "cvc_" + hashlib.md5(pair.encode()).hexdigest()


def load_cache(key: str) -> Optional[dict]:
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
        if row:
            return json.loads(row["response"])
        return None
    except Exception as e:
        print(f"  [cvc] Cache read failed: {e}")
        return None


def save_cache(key: str, query_text: str, data: dict):
    try:
        conn = get_db_connection()
        conn.execute(
            """
            INSERT INTO agent_cache (query_hash, query_text, response, created_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(query_hash) DO UPDATE
              SET response=excluded.response, created_at=excluded.created_at
            """,
            (key, query_text, json.dumps(data), datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"  [cvc] Cache write failed: {e}")


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def compare_companies(company_a: str, company_b: str) -> dict:
    """
    Full pipeline: collect → prompt → Claude → cache → return parsed dict.
    Raises on Claude API failure; all other failures degrade gracefully.
    """
    key = _cache_key(company_a, company_b)
    cached = load_cache(key)
    if cached:
        print(f"[cvc] Cache hit for {company_a} vs {company_b}")
        return cached

    print(f"[cvc] Starting analysis: {company_a} vs {company_b}")
    data_a = collect_company_data(company_a)
    data_b = collect_company_data(company_b)

    prompt = build_prompt(company_a, data_a, company_b, data_b)

    client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=4000,
        messages=[{"role": "user", "content": prompt}],
    )

    raw = message.content[0].text.strip()
    result = _extract_json(raw)
    save_cache(key, f"{company_a} vs {company_b}", result)
    print(f"[cvc] Analysis complete and cached.")
    return result


def _extract_json(raw: str) -> dict:
    """
    Robustly extract and parse a JSON object from a Claude response.
    Attempts in order:
      1. Strip markdown fences, parse directly.
      2. Find the outermost { } block and parse that.
      3. Truncate at the last complete top-level field and close the object.
    Raises ValueError if all attempts fail.
    """
    # 1 — strip fences and try direct parse
    cleaned = re.sub(r"^```(?:json)?\s*", "", raw.strip())
    cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # 2 — find outermost { } block
    start = cleaned.find("{")
    end   = cleaned.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(cleaned[start:end + 1])
        except json.JSONDecodeError:
            pass

    # 3 — truncate at last complete key-value pair and close the object
    # Walk backwards from rfind("}") to find a position that parses
    if start != -1:
        candidate = cleaned[start:]
        # Find the last comma followed only by whitespace/incomplete content
        for cut in range(len(candidate) - 1, 0, -1):
            if candidate[cut] in (",", "{"):
                attempt = candidate[:cut].rstrip().rstrip(",") + "\n}"
                try:
                    return json.loads(attempt)
                except json.JSONDecodeError:
                    continue

    raise ValueError(
        f"Could not extract valid JSON from Claude response "
        f"(response length: {len(raw)} chars). "
        f"Raw preview: {raw[:300]}..."
    )


# ---------------------------------------------------------------------------
# PDF report generator
# ---------------------------------------------------------------------------

def generate_pdf_report(data: dict, company_a: str, company_b: str) -> bytes:
    """Generate a ReportLab PDF and return the raw bytes."""
    buf    = io.BytesIO()
    doc    = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=2 * cm,
        rightMargin=2 * cm,
        topMargin=2 * cm,
        bottomMargin=2 * cm,
    )
    base   = getSampleStyleSheet()
    W      = A4[0] - 4 * cm  # usable width

    # ── Style definitions ──────────────────────────────────────────────────
    S = {
        "cover_title": ParagraphStyle("cover_title", parent=base["Title"],
            fontSize=26, leading=32, textColor=colors.HexColor("#1e3a5f"),
            alignment=TA_CENTER, spaceAfter=6),
        "cover_sub": ParagraphStyle("cover_sub", parent=base["Normal"],
            fontSize=14, textColor=colors.HexColor("#64748b"),
            alignment=TA_CENTER, spaceAfter=4),
        "cover_date": ParagraphStyle("cover_date", parent=base["Normal"],
            fontSize=10, textColor=colors.HexColor("#94a3b8"),
            alignment=TA_CENTER),
        "h1": ParagraphStyle("h1", parent=base["Heading1"],
            fontSize=16, leading=20, textColor=colors.HexColor("#1e3a5f"),
            spaceBefore=16, spaceAfter=6),
        "h2": ParagraphStyle("h2", parent=base["Heading2"],
            fontSize=12, leading=16, textColor=colors.HexColor("#334155"),
            spaceBefore=10, spaceAfter=4),
        "body": ParagraphStyle("body", parent=base["Normal"],
            fontSize=9, leading=14, textColor=colors.HexColor("#1e293b"),
            spaceAfter=4),
        "bullet": ParagraphStyle("bullet", parent=base["Normal"],
            fontSize=9, leading=14, textColor=colors.HexColor("#1e293b"),
            leftIndent=12, firstLineIndent=-10, spaceAfter=2),
        "tag": ParagraphStyle("tag", parent=base["Normal"],
            fontSize=8, leading=12, textColor=colors.HexColor("#1e3a5f"),
            backColor=colors.HexColor("#dbeafe"), spaceAfter=2),
        "highlight": ParagraphStyle("highlight", parent=base["Normal"],
            fontSize=9, leading=14, textColor=colors.HexColor("#1e293b"),
            backColor=colors.HexColor("#f0f9ff"),
            leftIndent=8, rightIndent=8, spaceAfter=4),
        "disclaimer": ParagraphStyle("disclaimer", parent=base["Normal"],
            fontSize=7, leading=11, textColor=colors.HexColor("#94a3b8"),
            alignment=TA_CENTER),
        "score_label": ParagraphStyle("score_label", parent=base["Normal"],
            fontSize=20, leading=24, textColor=colors.HexColor("#1e3a5f"),
            alignment=TA_CENTER),
    }

    def hr():
        return HRFlowable(width="100%", thickness=0.5,
                          color=colors.HexColor("#e2e8f0"), spaceAfter=6)

    def bullets(items: list, prefix: str = "•") -> list:
        return [Paragraph(f"{prefix}  {item}", S["bullet"]) for item in (items or ["N/A"])]

    def kv_table(pairs: list[tuple]) -> Table:
        """Two-column label/value table."""
        tdata = [[Paragraph(f"<b>{k}</b>", S["body"]),
                  Paragraph(str(v), S["body"])] for k, v in pairs]
        t = Table(tdata, colWidths=[W * 0.35, W * 0.65])
        t.setStyle(TableStyle([
            ("VALIGN",       (0, 0), (-1, -1), "TOP"),
            ("ROWBACKGROUNDS",(0, 0), (-1, -1),
             [colors.HexColor("#f8fafc"), colors.white]),
            ("GRID",         (0, 0), (-1, -1), 0.3, colors.HexColor("#e2e8f0")),
            ("LEFTPADDING",  (0, 0), (-1, -1), 6),
            ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            ("TOPPADDING",   (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING",(0, 0), (-1, -1), 4),
        ]))
        return t

    def score_color(score: int) -> colors.HexColor:
        if score >= 7:
            return colors.HexColor("#16a34a")
        if score >= 5:
            return colors.HexColor("#d97706")
        return colors.HexColor("#dc2626")

    def company_section(company_key: str) -> list:
        cd    = data.get(company_key, {})
        name  = cd.get("name", company_key)
        score = cd.get("momentum_score", 0)
        fs    = cd.get("financial_signals", {})
        ts    = cd.get("tech_signals", {})
        elems = []

        elems.append(Paragraph(name, S["h1"]))
        elems.append(hr())

        # Momentum score
        sc = ParagraphStyle("sc", parent=S["score_label"],
                            textColor=score_color(score))
        elems.append(Paragraph(f"Momentum Score: {score} / 10", sc))
        elems.append(Spacer(1, 0.3 * cm))

        elems.append(Paragraph("Key Strengths", S["h2"]))
        elems.extend(bullets(cd.get("key_strengths", []), "✓"))

        elems.append(Paragraph("Key Risks", S["h2"]))
        elems.extend(bullets(cd.get("key_risks", []), "⚠"))

        elems.append(Paragraph("Recent Moves", S["h2"]))
        elems.extend(bullets(cd.get("recent_moves", []), "→"))

        elems.append(Paragraph("Financial Signals", S["h2"]))
        elems.append(kv_table([
            ("Revenue Trend",         fs.get("revenue_trend", "N/A")),
            ("Cost Pressures",        fs.get("cost_pressure_signals", "N/A")),
            ("Investment Activity",   fs.get("investment_activity", "N/A")),
            ("Analyst Sentiment",     fs.get("analyst_sentiment", "N/A")),
        ]))
        elems.append(Spacer(1, 0.2 * cm))

        elems.append(Paragraph("Tech Signals", S["h2"]))
        launches = ts.get("product_launches", [])
        if launches:
            elems.append(Paragraph("Product Launches:  " + "  |  ".join(launches), S["tag"]))
        elems.append(kv_table([
            ("Engineering Hiring",  ts.get("engineering_hiring_trend", "N/A")),
            ("AI / ML Activity",    ts.get("ai_ml_activity", "N/A")),
            ("Technical Debt",      ts.get("technical_debt_signals", "N/A")),
        ]))
        elems.append(Spacer(1, 0.2 * cm))

        elems.append(Paragraph("Consulting Angles", S["h2"]))
        for i, angle in enumerate(cd.get("consulting_angles", []), 1):
            elems.append(Paragraph(f"💡  {angle}", S["highlight"]))

        return elems

    # ── Build story ─────────────────────────────────────────────────────────
    story = []
    today = datetime.utcnow().strftime("%B %d, %Y")

    # Cover
    story.append(Spacer(1, 3 * cm))
    story.append(Paragraph("Competitive Intelligence Report", S["cover_title"]))
    story.append(Spacer(1, 0.4 * cm))
    story.append(Paragraph(f"{company_a}  vs  {company_b}", S["cover_sub"]))
    story.append(Spacer(1, 0.3 * cm))
    story.append(Paragraph(f"Generated {today} · BriefRoom", S["cover_date"]))
    story.append(PageBreak())

    # Executive summary
    story.append(Paragraph("Executive Summary", S["h1"]))
    story.append(hr())
    story.append(Paragraph(data.get("verdict", ""), S["body"]))
    story.append(Spacer(1, 0.3 * cm))
    story.append(kv_table([
        ("Who has momentum",  data.get("who_has_momentum", "N/A")),
        ("Key battleground",  data.get("key_battleground", "N/A")),
        ("Watch for",         data.get("watch_for", "N/A")),
    ]))
    story.append(PageBreak())

    # Company A
    story.extend(company_section("company_a"))
    story.append(PageBreak())

    # Company B
    story.extend(company_section("company_b"))
    story.append(PageBreak())

    # Strategic section
    story.append(Paragraph("Strategic Analysis", S["h1"]))
    story.append(hr())

    story.append(Paragraph("Strategic Opportunities", S["h2"]))
    for opp in (data.get("strategic_opportunities") or []):
        story.append(Paragraph(f"→  {opp}", S["highlight"]))

    story.append(Spacer(1, 0.4 * cm))
    story.append(kv_table([
        ("M&A Signals",     data.get("merger_acquisition_signals", "N/A")),
        ("Regulatory Risk", data.get("regulatory_risk", "N/A")),
    ]))

    story.append(Spacer(1, 0.5 * cm))
    story.append(Paragraph("Consulting Recommendation", S["h2"]))
    rec_style = ParagraphStyle("rec", parent=S["body"],
                               backColor=colors.HexColor("#fefce8"),
                               leftIndent=10, rightIndent=10,
                               borderPadding=8)
    story.append(Paragraph(data.get("consulting_recommendation", ""), rec_style))

    # Disclaimer
    story.append(Spacer(1, 1 * cm))
    story.append(hr())
    story.append(Paragraph(
        "Analysis based on publicly available data and AI reasoning. "
        "Not financial advice. BriefRoom · Powered by Claude.",
        S["disclaimer"],
    ))

    doc.build(story)
    return buf.getvalue()
