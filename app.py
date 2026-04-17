import hashlib
import json
import os
import re
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv
load_dotenv(override=True)  # must run before any agent imports so the key is in os.environ

import anthropic
import plotly.graph_objects as go
import streamlit as st

from agents.company_vs_company import compare_companies, generate_pdf_report
from agents.so_what import explain_so_what
from agents.country_risk import get_country_risk
from agents.meeting_prep import get_meeting_prep, generate_meeting_pdf
from db import get_db_connection, init_db

_key_preview = (os.getenv("ANTHROPIC_API_KEY") or "")[:10]
print(f"[BriefRoom] ANTHROPIC_API_KEY loaded: {_key_preview}... ({'OK' if _key_preview else 'MISSING — check .env'})")

# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="BriefRoom",
    page_icon="📡",
    layout="wide",
    initial_sidebar_state="expanded",
)

init_db()

# ---------------------------------------------------------------------------
# Custom CSS
# ---------------------------------------------------------------------------

st.markdown("""
<style>
/* ── Metric cards ── */
[data-testid="metric-container"] {
    background: #1e1e2e;
    border: 1px solid #2e2e3e;
    border-radius: 10px;
    padding: 14px 18px;
}

/* ── Section headers ── */
.section-header {
    font-size: 0.75rem;
    font-weight: 700;
    color: #64748b;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    border-bottom: 1px solid #2e2e3e;
    padding-bottom: 8px;
    margin-bottom: 16px;
    margin-top: 4px;
}

/* ── Article expander tweaks ── */
[data-testid="stExpander"] {
    border: 1px solid #2e2e3e !important;
    border-radius: 8px !important;
    background: #1e1e2e !important;
    margin-bottom: 6px !important;
}
[data-testid="stExpander"]:hover {
    border-color: #3b82f6 !important;
}

/* ── Source badges ── */
.badge {
    display: inline-block;
    padding: 2px 9px;
    border-radius: 999px;
    font-size: 0.68rem;
    font-weight: 700;
    letter-spacing: 0.05em;
    margin-right: 8px;
    vertical-align: middle;
}
.badge-ap        { background: #1c2f4a; color: #60a5fa; }
.badge-npr       { background: #1c2f4a; color: #60a5fa; }
.badge-guardian  { background: #1c2f4a; color: #60a5fa; }
.badge-cnbc      { background: #1c2f4a; color: #60a5fa; }
.badge-bbc-biz   { background: #1c2f4a; color: #60a5fa; }
.badge-bbc-world { background: #2d1b4e; color: #c084fc; }
.badge-hn        { background: #2d1e0e; color: #fb923c; }
.badge-tc        { background: #1a3328; color: #34d399; }

/* ── Article summary text ── */
.summary-text {
    font-size: 0.88rem;
    color: #94a3b8;
    line-height: 1.6;
    margin-bottom: 10px;
}
.read-link {
    font-size: 0.82rem;
    color: #3b82f6;
    text-decoration: none;
    font-weight: 600;
}
.read-link:hover { color: #93c5fd; }

/* ── Muted timestamp ── */
.ts {
    font-size: 0.72rem;
    color: #475569;
}

/* ── Briefing card ── */
.briefing-card {
    background: #1e1e2e;
    border: 1px solid #2e2e3e;
    border-left: 3px solid #3b82f6;
    border-radius: 8px;
    padding: 18px 22px;
    font-size: 0.93rem;
    line-height: 1.7;
    color: #e2e8f0;
}
.briefing-meta {
    font-size: 0.72rem;
    color: #475569;
    margin-top: 10px;
}
</style>
""", unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# Badge helper
# ---------------------------------------------------------------------------

SOURCE_BADGE_CLASS = {
    "AP News":       "badge-ap",
    "NPR Business":  "badge-npr",
    "The Guardian":  "badge-guardian",
    "CNBC":          "badge-cnbc",
    "BBC Business":  "badge-bbc-biz",
    "BBC World":     "badge-bbc-world",
    "Hacker News":   "badge-hn",
    "TechCrunch":    "badge-tc",
}

def badge_html(source: str) -> str:
    cls = SOURCE_BADGE_CLASS.get(source, "badge-ap")
    return f'<span class="badge {cls}">{source}</span>'

# ---------------------------------------------------------------------------
# Cached data loaders
# ---------------------------------------------------------------------------

@st.cache_data(ttl=300)
def load_market_snapshot():
    conn = get_db_connection()
    rows = conn.execute("""
        SELECT symbol, name, price, change_pct, category, fetched_at
        FROM market_data
        WHERE rowid IN (
            SELECT MAX(rowid) FROM market_data GROUP BY symbol
        )
        ORDER BY category, symbol
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@st.cache_data(ttl=300)
def load_spy_history():
    conn = get_db_connection()
    rows = conn.execute("""
        SELECT price, fetched_at
        FROM market_data
        WHERE symbol = 'SPY'
        ORDER BY fetched_at ASC
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@st.cache_data(ttl=300)
def load_articles_for_feed(categories: list[str], limit: int = 100):
    """Load articles for a list of categories, most recent first."""
    placeholders = ",".join("?" * len(categories))
    conn = get_db_connection()
    rows = conn.execute(f"""
        SELECT title, summary, source, category, url, scraped_at
        FROM articles
        WHERE category IN ({placeholders})
        ORDER BY scraped_at DESC
        LIMIT ?
    """, (*categories, limit)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@st.cache_data(ttl=300)
def load_recent_articles_for_briefing(limit: int = 20):
    conn = get_db_connection()
    rows = conn.execute("""
        SELECT title, summary, source
        FROM articles
        ORDER BY scraped_at DESC
        LIMIT ?
    """, (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@st.cache_data(ttl=300)
def load_cached_briefing(cache_key: str):
    conn = get_db_connection()
    row = conn.execute("""
        SELECT response, created_at FROM agent_cache WHERE query_hash = ?
    """, (cache_key,)).fetchone()
    conn.close()
    return dict(row) if row else None


@st.cache_data(ttl=300)
def load_last_updated():
    conn = get_db_connection()
    row = conn.execute("SELECT MAX(fetched_at) FROM market_data").fetchone()
    conn.close()
    return row[0] if row and row[0] else "—"

# ---------------------------------------------------------------------------
# Briefing generator
# ---------------------------------------------------------------------------

def generate_briefing(articles: list[dict]) -> str:
    """Call Claude and return a JSON briefing string."""
    headlines = "\n".join(
        f"- [{a['source']}] {a['title']}: {(a['summary'] or '')[:200]}"
        for a in articles
    )
    prompt = (
        "You are a senior business consultant. Based on these news headlines and summaries, "
        "return ONLY a valid JSON object — no markdown fences, no commentary.\n\n"
        "Return exactly this structure:\n"
        "{\n"
        '  "headline": "<one punchy sentence summarising the day\'s dominant story>",\n'
        '  "themes": [\n'
        '    {\n'
        '      "title": "<theme title>",\n'
        '      "summary": "<2-3 sentence summary of this theme>",\n'
        '      "key_companies": ["<company or entity>", ...],\n'
        '      "consultant_note": "<one sharp actionable observation>"\n'
        '    }\n'
        '  ],\n'
        '  "risk_of_the_day": "<the single most important risk a consultant should flag today>",\n'
        '  "opportunity_of_the_day": "<one specific opportunity emerging from today\'s news>"\n'
        "}\n\n"
        "Include 3-4 themes. Be specific — mention companies, figures, and geographies.\n\n"
        f"Headlines:\n{headlines}"
    )
    client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1500,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = message.content[0].text.strip()
    # Strip accidental markdown fences
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    # Validate it parses — raises if malformed so caller can handle
    json.loads(raw)
    return raw


def save_briefing(cache_key: str, text: str):
    conn = get_db_connection()
    conn.execute("""
        INSERT INTO agent_cache (query_hash, query_text, response, created_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(query_hash) DO UPDATE SET response=excluded.response, created_at=excluded.created_at
    """, (cache_key, "daily_briefing", text, datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")))
    conn.commit()
    conn.close()

# ---------------------------------------------------------------------------
# Article feed renderer
# ---------------------------------------------------------------------------

def apply_filters(
    articles: list[dict],
    selected_sources: list[str],
    keyword: str,
    today_only: bool,
) -> list[dict]:
    filtered = articles
    if selected_sources:
        filtered = [a for a in filtered if a["source"] in selected_sources]
    if keyword:
        kw = keyword.lower()
        filtered = [a for a in filtered if kw in (a["title"] or "").lower()]
    if today_only:
        cutoff = (datetime.utcnow() - timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")
        filtered = [a for a in filtered if (a["scraped_at"] or "") >= cutoff]
    return filtered


def render_feed(
    articles: list[dict],
    all_sources: list[str],
    feed_key: str,
    default_expanded: bool = False,
):
    """Filter bar + paginated expander cards for one feed column."""

    # ── Filter bar ──────────────────────────────────────────────────────────
    f1, f2, f3 = st.columns([3, 3, 2])
    with f1:
        selected_sources = st.multiselect(
            "Source",
            options=all_sources,
            default=[],
            placeholder="All sources",
            key=f"{feed_key}_sources",
            label_visibility="collapsed",
        )
    with f2:
        keyword = st.text_input(
            "Search",
            placeholder="Search titles...",
            key=f"{feed_key}_search",
            label_visibility="collapsed",
        )
    with f3:
        today_only = st.toggle("Today only", key=f"{feed_key}_today")

    filtered = apply_filters(articles, selected_sources, keyword, today_only)

    if not filtered:
        st.caption("No articles match the current filters.")
        return

    # ── Pagination state ─────────────────────────────────────────────────────
    page_key = f"{feed_key}_page"
    if page_key not in st.session_state:
        st.session_state[page_key] = 1

    # Reset page when filters change (use filter signature as a proxy)
    filter_sig = f"{selected_sources}{keyword}{today_only}"
    sig_key = f"{feed_key}_filter_sig"
    if st.session_state.get(sig_key) != filter_sig:
        st.session_state[page_key] = 1
        st.session_state[sig_key] = filter_sig

    page_size = 8
    total     = len(filtered)
    shown     = st.session_state[page_key] * page_size
    visible   = filtered[:shown]

    # ── Expander cards ───────────────────────────────────────────────────────
    for article in visible:
        title     = article["title"] or "(no title)"
        source    = article["source"] or ""
        url       = article["url"] or "#"
        summary   = (article.get("summary") or "").strip()
        ts        = (article["scraped_at"] or "")[:16]

        label = f"{source}  ·  {title}"
        with st.expander(label, expanded=default_expanded):
            st.markdown(
                f'{badge_html(source)} <span class="ts">{ts} UTC</span>',
                unsafe_allow_html=True,
            )
            st.markdown("<div style='height:6px'></div>", unsafe_allow_html=True)
            if summary:
                st.markdown(
                    f'<p class="summary-text">{summary}</p>',
                    unsafe_allow_html=True,
                )
            st.markdown(
                f'<a class="read-link" href="{url}" target="_blank">Read full article →</a>',
                unsafe_allow_html=True,
            )
        st.markdown("<div style='height:2px'></div>", unsafe_allow_html=True)

    # ── Load more ────────────────────────────────────────────────────────────
    if shown < total:
        remaining = total - shown
        if st.button(
            f"Load {min(page_size, remaining)} more  ({remaining} remaining)",
            key=f"{feed_key}_load_more",
            use_container_width=True,
        ):
            st.session_state[page_key] += 1
            st.rerun()
    else:
        st.caption(f"All {total} articles shown.")

# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Watchlist DB helpers
# ---------------------------------------------------------------------------

def _load_watchlist() -> list[str]:
    try:
        conn = get_db_connection()
        rows = conn.execute(
            "SELECT company_name FROM watchlist ORDER BY added_at DESC LIMIT 20"
        ).fetchall()
        conn.close()
        return [r["company_name"] for r in rows]
    except Exception:
        return []

def _add_to_watchlist(name: str):
    try:
        conn = get_db_connection()
        conn.execute(
            "INSERT OR IGNORE INTO watchlist (company_name) VALUES (?)", (name.strip(),)
        )
        conn.commit()
        conn.close()
    except Exception:
        pass

def _remove_from_watchlist(name: str):
    try:
        conn = get_db_connection()
        conn.execute("DELETE FROM watchlist WHERE company_name = ?", (name,))
        conn.commit()
        conn.close()
    except Exception:
        pass

def _load_recent_cache_entries(limit: int = 5) -> list[dict]:
    try:
        conn = get_db_connection()
        rows = conn.execute(
            """
            SELECT query_hash, query_text, created_at FROM agent_cache
            WHERE query_hash NOT LIKE 'daily_briefing%'
            ORDER BY created_at DESC LIMIT ?
            """,
            (limit,),
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception:
        return []

# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------

# Initialise sidebar-related session state
if "sidebar_watchlist" not in st.session_state:
    st.session_state.sidebar_watchlist = _load_watchlist()

with st.sidebar:
    st.markdown("## 📡 BriefRoom")
    st.caption("Real-time intelligence dashboard")
    st.divider()

    last_updated = load_last_updated()
    st.markdown(f"**Last updated**  \n`{last_updated} UTC`")

    if st.button("🔄 Refresh", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

    st.divider()
    page = st.radio(
        "Navigate",
        ["Dashboard", "Company vs Company", "So What?", "Country Risk", "Meeting Prep"],
        label_visibility="collapsed",
    )

    # ── Watchlist ─────────────────────────────────────────────────────────────
    st.divider()
    st.markdown(
        '<span style="font-size:0.68rem;font-weight:700;color:#64748b;'
        'text-transform:uppercase;letter-spacing:.08em">Watchlist</span>',
        unsafe_allow_html=True,
    )

    wl_input_col, wl_btn_col = st.columns([3, 1])
    wl_new = wl_input_col.text_input(
        "Add company",
        placeholder="Company name",
        label_visibility="collapsed",
        key="wl_add_input",
    )
    if wl_btn_col.button("＋", key="wl_add_btn", use_container_width=True, help="Add to watchlist"):
        name = (wl_new or "").strip()
        if name and name not in st.session_state.sidebar_watchlist:
            _add_to_watchlist(name)
            st.session_state.sidebar_watchlist = _load_watchlist()
            st.rerun()

    wl = st.session_state.sidebar_watchlist
    display_wl = wl[:8]
    if display_wl:
        for company in display_wl:
            pill_col, rm_col = st.columns([4, 1])
            if pill_col.button(
                company,
                key=f"wl_goto_{company}",
                use_container_width=True,
                help=f"Compare {company} in CvC",
            ):
                st.session_state["cvc_company_a"] = company
                st.session_state["cvc_company_b"] = ""
                st.session_state["cvc_result"]    = None
                # Navigate to CvC — we switch page via session_state trick
                st.session_state["_nav_page"] = "Company vs Company"
                st.rerun()
            if rm_col.button("×", key=f"wl_rm_{company}", help=f"Remove {company}"):
                _remove_from_watchlist(company)
                st.session_state.sidebar_watchlist = _load_watchlist()
                st.rerun()
        if len(wl) > 8:
            st.caption(f"+{len(wl) - 8} more")
    else:
        st.caption("No companies yet — add one above.")

    # ── Recent Analyses ───────────────────────────────────────────────────────
    st.divider()
    st.markdown(
        '<span style="font-size:0.68rem;font-weight:700;color:#64748b;'
        'text-transform:uppercase;letter-spacing:.08em">Recent Analyses</span>',
        unsafe_allow_html=True,
    )

    _CACHE_ICONS = {
        "cvc_":           "🥊",
        "so_what_":       "🔮",
        "country_risk_":  "🌍",
        "market_summary_":"📊",
    }

    recent_entries = _load_recent_cache_entries(5)
    if recent_entries:
        for entry in recent_entries:
            qhash = entry["query_hash"] or ""
            qtext = (entry["query_text"] or "").strip()
            ts    = (entry["created_at"] or "")[:16]
            icon  = "🧠"
            dest_page = None
            for prefix, ic in _CACHE_ICONS.items():
                if qhash.startswith(prefix):
                    icon = ic
                    if prefix == "cvc_":
                        dest_page = "Company vs Company"
                    elif prefix == "so_what_":
                        dest_page = "So What?"
                    elif prefix == "country_risk_":
                        dest_page = "Country Risk"
                    break

            label = f"{icon} {qtext[:28]}…" if len(qtext) > 28 else f"{icon} {qtext}"
            if st.button(label, key=f"ra_{qhash[:16]}", use_container_width=True, help=ts):
                if dest_page:
                    st.session_state["_nav_page"] = dest_page
                    if dest_page == "So What?" and qtext:
                        st.session_state["sw_input"]  = qtext
                        st.session_state["sw_result"] = None
                    elif dest_page == "Country Risk" and qtext:
                        st.session_state["cr_country"] = qtext
                        st.session_state["cr_result"]  = None
                    elif dest_page == "Company vs Company" and qtext:
                        parts = [p.strip() for p in qtext.split(" vs ")]
                        if len(parts) == 2:
                            st.session_state["cvc_company_a"] = parts[0]
                            st.session_state["cvc_company_b"] = parts[1]
                            st.session_state["cvc_result"]    = None
                    st.rerun()
    else:
        st.caption("No analyses yet.")

# Handle nav override from sidebar watchlist/recent clicks
if "_nav_page" in st.session_state:
    page = st.session_state.pop("_nav_page")

# ---------------------------------------------------------------------------
# DASHBOARD PAGE
# ---------------------------------------------------------------------------

if page == "Dashboard":
    st.markdown("# 📡 BriefRoom Dashboard")

    # ── Section 0: AI Daily Briefing ────────────────────────────────────────
    st.markdown('<div class="section-header">Today\'s Briefing</div>', unsafe_allow_html=True)

    today_key = "daily_briefing_" + datetime.utcnow().strftime("%Y-%m-%d")
    cached    = load_cached_briefing(today_key)

    if cached:
        try:
            brief = json.loads(cached["response"])
        except (json.JSONDecodeError, TypeError):
            # Old plain-text cache — clear it and prompt regeneration
            brief = None
            conn = get_db_connection()
            conn.execute("DELETE FROM agent_cache WHERE query_hash = ?", (today_key,))
            conn.commit()
            conn.close()
            st.cache_data.clear()
            cached = None

        if brief:
            # ── Headline ─────────────────────────────────────────────────────
            st.markdown(
                f'<div style="font-size:1.25rem;font-weight:800;color:#e2e8f0;'
                f'line-height:1.4;margin-bottom:16px">{brief.get("headline","")}</div>',
                unsafe_allow_html=True,
            )

            # ── Theme cards ──────────────────────────────────────────────────
            for theme in (brief.get("themes") or []):
                companies = theme.get("key_companies") or []
                pills = "".join(
                    f'<span style="background:#1e3a5f;color:#93c5fd;padding:2px 9px;'
                    f'border-radius:999px;font-size:0.72rem;margin-right:5px;'
                    f'display:inline-block;margin-bottom:3px">{c}</span>'
                    for c in companies
                )
                st.markdown(
                    f'<div style="background:#1e1e2e;border:1px solid #2e2e3e;border-left:'
                    f'3px solid #3b82f6;border-radius:8px;padding:14px 18px;margin-bottom:10px">'
                    f'<div style="font-size:0.85rem;font-weight:700;color:#e2e8f0;margin-bottom:6px">'
                    f'{theme.get("title","")}</div>'
                    f'<div style="font-size:0.85rem;color:#94a3b8;line-height:1.6;margin-bottom:8px">'
                    f'{theme.get("summary","")}</div>'
                    + (f'<div style="margin-bottom:8px">{pills}</div>' if pills else "")
                    + f'<div style="font-size:0.82rem;color:#64748b;font-style:italic">'
                    f'{theme.get("consultant_note","")}</div>'
                    f'</div>',
                    unsafe_allow_html=True,
                )

            # ── Risk / Opportunity ───────────────────────────────────────────
            risk_col, opp_col = st.columns(2)
            with risk_col:
                st.markdown(
                    f'<div style="background:#2e1a1a;border:1px solid #991b1b;border-radius:8px;'
                    f'padding:12px 16px"><div style="font-size:0.68rem;font-weight:700;color:#f87171;'
                    f'text-transform:uppercase;letter-spacing:.08em;margin-bottom:4px">⚠ Risk of the Day</div>'
                    f'<div style="font-size:0.86rem;color:#fecaca;line-height:1.6">'
                    f'{brief.get("risk_of_the_day","")}</div></div>',
                    unsafe_allow_html=True,
                )
            with opp_col:
                st.markdown(
                    f'<div style="background:#1a2e1a;border:1px solid #166534;border-radius:8px;'
                    f'padding:12px 16px"><div style="font-size:0.68rem;font-weight:700;color:#34d399;'
                    f'text-transform:uppercase;letter-spacing:.08em;margin-bottom:4px">✦ Opportunity of the Day</div>'
                    f'<div style="font-size:0.86rem;color:#bbf7d0;line-height:1.6">'
                    f'{brief.get("opportunity_of_the_day","")}</div></div>',
                    unsafe_allow_html=True,
                )

            st.markdown(
                f'<div style="font-size:0.72rem;color:#475569;margin-top:10px">'
                f'Generated at {cached["created_at"]} UTC</div>',
                unsafe_allow_html=True,
            )
    else:
        st.caption("No briefing generated yet for today.")

    if st.button("✨ Generate Briefing", disabled=bool(cached)):
        articles = load_recent_articles_for_briefing(20)
        if not articles:
            st.warning("No articles in the database yet. Run the scraper first.")
        else:
            with st.spinner("Claude is reading the news and writing your briefing..."):
                try:
                    text = generate_briefing(articles)
                    save_briefing(today_key, text)
                    st.cache_data.clear()
                    st.rerun()
                except Exception as e:
                    st.error(f"Claude API error: {e}")

    st.divider()

    # ── Section 1: Markets Pulse ─────────────────────────────────────────────
    st.markdown('<div class="section-header">Markets Pulse</div>', unsafe_allow_html=True)

    market_rows      = load_market_snapshot()
    market_by_symbol = {r["symbol"]: r for r in market_rows}

    # — Primary metric cards (4 heroes) —
    PRIMARY_SYMBOLS  = ["SPY", "QQQ", "BTC-USD", "GC=F"]
    SECONDARY_SYMBOLS = ["AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "ETH-USD", "CL=F"]
    ALL_SYMBOLS      = PRIMARY_SYMBOLS + SECONDARY_SYMBOLS
    SYMBOL_COLORS    = {
        "SPY":    "#3b82f6", "QQQ":    "#8b5cf6", "AAPL":   "#ec4899",
        "MSFT":   "#06b6d4", "GOOGL":  "#f59e0b", "AMZN":   "#10b981",
        "NVDA":   "#f97316", "BTC-USD":"#eab308", "ETH-USD":"#6366f1",
        "GC=F":   "#d4a017", "CL=F":   "#64748b",
    }

    hero_cols = st.columns(4)
    for col, sym in zip(hero_cols, PRIMARY_SYMBOLS):
        row = market_by_symbol.get(sym)
        if row:
            chg  = row["change_pct"]
            sign = "+" if chg >= 0 else ""
            col.metric(
                label=f"{sym}  —  {row['name']}",
                value=f"${row['price']:,.2f}",
                delta=f"{sign}{chg:.2f}%",
            )
        else:
            col.metric(label=sym, value="—")

    # — Secondary compact cards (scrollable row via HTML) —
    sec_cards = ""
    for sym in SECONDARY_SYMBOLS:
        row = market_by_symbol.get(sym)
        if not row:
            continue
        chg   = row["change_pct"]
        color = "#34d399" if chg >= 0 else "#f87171"
        sign  = "+" if chg >= 0 else ""
        sec_cards += (
            f'<div style="background:#1e1e2e;border:1px solid #2e2e3e;border-radius:8px;'
            f'padding:10px 14px;min-width:110px;flex-shrink:0;text-align:center">'
            f'<div style="font-size:0.7rem;font-weight:700;color:#64748b;margin-bottom:3px">{sym}</div>'
            f'<div style="font-size:0.95rem;font-weight:700;color:#e2e8f0">${row["price"]:,.2f}</div>'
            f'<div style="font-size:0.78rem;font-weight:600;color:{color}">{sign}{chg:.2f}%</div>'
            f'</div>'
        )
    if sec_cards:
        st.markdown(
            f'<div style="display:flex;gap:10px;overflow-x:auto;padding:10px 0 4px;'
            f'scrollbar-width:thin;scrollbar-color:#2e2e3e transparent">{sec_cards}</div>',
            unsafe_allow_html=True,
        )

    st.markdown("<div style='height:12px'></div>", unsafe_allow_html=True)

    # ── Chart session state defaults ─────────────────────────────────────────
    if "mp_chart_type"   not in st.session_state: st.session_state.mp_chart_type   = "Line"
    if "mp_time_range"   not in st.session_state: st.session_state.mp_time_range   = "1W"
    if "mp_active_syms"  not in st.session_state: st.session_state.mp_active_syms  = {"SPY", "QQQ"}

    # ── Controls row ─────────────────────────────────────────────────────────
    ctrl_l, ctrl_r = st.columns([1, 1])

    with ctrl_l:
        st.markdown(
            '<span style="font-size:0.7rem;font-weight:700;color:#64748b;'
            'text-transform:uppercase;letter-spacing:.08em">Chart Type</span>',
            unsafe_allow_html=True,
        )
        ct_cols = st.columns(3)
        for i, ctype in enumerate(["Line", "Bar", "Candlestick"]):
            active = st.session_state.mp_chart_type == ctype
            style  = (
                "background:#3b82f6;color:#fff;border:1px solid #3b82f6;"
                if active else
                "background:#1e1e2e;color:#94a3b8;border:1px solid #2e2e3e;"
            )
            if ct_cols[i].button(
                ctype,
                key=f"mp_ct_{ctype}",
                use_container_width=True,
                help=f"Switch to {ctype} chart",
            ):
                st.session_state.mp_chart_type = ctype
                st.rerun()

    with ctrl_r:
        st.markdown(
            '<span style="font-size:0.7rem;font-weight:700;color:#64748b;'
            'text-transform:uppercase;letter-spacing:.08em">Time Range</span>',
            unsafe_allow_html=True,
        )
        tr_cols = st.columns(3)
        for i, trange in enumerate(["1D", "1W", "1M"]):
            active = st.session_state.mp_time_range == trange
            if tr_cols[i].button(
                trange,
                key=f"mp_tr_{trange}",
                use_container_width=True,
                help=f"Show {trange} data",
            ):
                st.session_state.mp_time_range = trange
                st.rerun()

    # ── Symbol toggle pills ───────────────────────────────────────────────────
    st.markdown(
        '<span style="font-size:0.7rem;font-weight:700;color:#64748b;'
        'text-transform:uppercase;letter-spacing:.08em">Symbols</span>',
        unsafe_allow_html=True,
    )
    pill_cols = st.columns(len(ALL_SYMBOLS))
    for col, sym in zip(pill_cols, ALL_SYMBOLS):
        active   = sym in st.session_state.mp_active_syms
        sym_color = SYMBOL_COLORS.get(sym, "#3b82f6")
        if col.button(
            sym,
            key=f"mp_pill_{sym}",
            use_container_width=True,
            help=f"Toggle {sym}",
        ):
            current = set(st.session_state.mp_active_syms)
            if sym in current and len(current) > 1:
                current.discard(sym)
            elif sym not in current:
                current.add(sym)
            st.session_state.mp_active_syms = current
            st.rerun()

    # ── Data loaders (ttl=60) ────────────────────────────────────────────────
    @st.cache_data(ttl=60)
    def load_symbol_history(symbol: str, time_range: str) -> list[dict]:
        """Load price history for symbol. time_range is '1D', '1W', or '1M'."""
        _filter_map = {
            "1D": "-24 hours",
            "1W": "-168 hours",
            "1M": "-720 hours",
        }
        dt_filter = _filter_map.get(time_range, "-168 hours")
        conn = get_db_connection()
        rows = conn.execute(
            """
            SELECT price, fetched_at FROM market_data
            WHERE symbol = ?
              AND datetime(fetched_at) >= datetime('now', ?)
            ORDER BY fetched_at ASC
            """,
            (symbol, dt_filter),
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    @st.cache_data(ttl=60)
    def load_ohlc(symbol: str, period: str, interval: str) -> list[dict]:
        try:
            import yfinance as yf
            ticker = yf.Ticker(symbol)
            df     = ticker.history(period=period, interval=interval)
            if df.empty:
                return []
            df = df.reset_index()
            ts_col = "Datetime" if "Datetime" in df.columns else "Date"
            return [
                {
                    "ts":    str(row[ts_col]),
                    "open":  row["Open"],
                    "high":  row["High"],
                    "low":   row["Low"],
                    "close": row["Close"],
                }
                for _, row in df.iterrows()
            ]
        except Exception as e:
            print(f"[markets] OHLC fetch failed for {symbol}: {e}")
            return []

    # Map time range to yfinance params
    RANGE_MAP = {
        "1D": {"yf_period": "1d",  "yf_interval": "5m"},
        "1W": {"yf_period": "5d",  "yf_interval": "1h"},
        "1M": {"yf_period": "1mo", "yf_interval": "1d"},
    }
    rng        = RANGE_MAP[st.session_state.mp_time_range]
    chart_type = st.session_state.mp_chart_type
    active_syms= sorted(st.session_state.mp_active_syms)

    # ── Chart context label ───────────────────────────────────────────────────
    syms_label = ", ".join(active_syms) if len(active_syms) <= 5 else f"{len(active_syms)} symbols"
    st.markdown(
        f'<div style="font-size:0.7rem;color:#475569;margin-bottom:6px">'
        f'Showing: <b style="color:#94a3b8">{st.session_state.mp_time_range}</b>'
        f' &nbsp;·&nbsp; Symbols: <b style="color:#94a3b8">{syms_label}</b>'
        f' &nbsp;·&nbsp; Type: <b style="color:#94a3b8">{chart_type}</b>'
        f'</div>',
        unsafe_allow_html=True,
    )

    CHART_BG = "#0f0f1a"
    # yaxis and hovermode are intentionally excluded here — each chart type
    # specifies its own to avoid "multiple values for keyword argument" errors
    # when **LAYOUT_BASE is unpacked alongside those same keys.
    LAYOUT_BASE = dict(
        paper_bgcolor=CHART_BG, plot_bgcolor=CHART_BG,
        font=dict(color="#94a3b8"),
        xaxis=dict(gridcolor="#1e1e2e", showgrid=True, showspikes=True,
                   spikecolor="#475569", spikethickness=1),
        legend=dict(bgcolor="rgba(0,0,0,0)", bordercolor="#2e2e3e",
                    borderwidth=1, font=dict(size=11)),
        margin=dict(l=10, r=10, t=36, b=10),
        height=380,
    )

    fig = go.Figure()

    if chart_type == "Candlestick":
        sym = active_syms[0]
        if len(active_syms) > 1:
            st.info(f"Candlestick shows one symbol at a time — displaying **{sym}**.")
        ohlc = load_ohlc(sym, rng["yf_period"], rng["yf_interval"])
        if ohlc:
            fig.add_trace(go.Candlestick(
                x    =[r["ts"]    for r in ohlc],
                open =[r["open"]  for r in ohlc],
                high =[r["high"]  for r in ohlc],
                low  =[r["low"]   for r in ohlc],
                close=[r["close"] for r in ohlc],
                name =sym,
                increasing_line_color="#34d399",
                decreasing_line_color="#f87171",
            ))
            fig.update_layout(
                **LAYOUT_BASE,
                title=f"{sym} — Candlestick ({st.session_state.mp_time_range})",
                xaxis_rangeslider_visible=False,
                yaxis=dict(gridcolor="#1e1e2e", showgrid=True, tickprefix="$"),
                hovermode="x unified",
            )
        else:
            st.warning(f"No OHLC data available for {sym}.")
            fig = None

    elif chart_type == "Bar":
        # Grouped bars: latest change_pct per active symbol
        syms, vals, clrs = [], [], []
        for sym in active_syms:
            row = market_by_symbol.get(sym)
            if row:
                chg = row["change_pct"]
                syms.append(sym)
                vals.append(round(chg, 2))
                clrs.append("#34d399" if chg >= 0 else "#f87171")
        if syms:
            fig.add_trace(go.Bar(
                x=syms, y=vals,
                marker_color=clrs,
                text=[f"{v:+.2f}%" for v in vals],
                textposition="outside",
                hovertemplate="<b>%{x}</b><br>%{y:+.2f}%<extra></extra>",
                name="% Change",
            ))
            fig.update_layout(
                **LAYOUT_BASE,
                title=f"% Change — Latest Snapshot",
                yaxis=dict(gridcolor="#1e1e2e", showgrid=True, ticksuffix="%",
                           zeroline=True, zerolinecolor="#475569", zerolinewidth=1),
                hovermode="x",
                showlegend=False,
            )  # hovermode="x" (not "x unified") is intentional for bar charts
        else:
            fig = None

    else:  # Line
        def _hex_to_rgba(hex_color: str, alpha: float = 0.08) -> str:
            """Convert a 6-digit hex color string to rgba(r,g,b,alpha)."""
            h = hex_color.lstrip("#")
            r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
            return f"rgba({r},{g},{b},{alpha})"

        has_data = False
        for sym in active_syms:
            history = load_symbol_history(sym, st.session_state.mp_time_range)
            if not history:
                continue
            has_data = True
            color    = SYMBOL_COLORS.get(sym, "#3b82f6")
            fig.add_trace(go.Scatter(
                x   =[r["fetched_at"] for r in history],
                y   =[r["price"]      for r in history],
                mode="lines",
                name=sym,
                line=dict(color=color, width=2),
                fill="tozeroy" if len(active_syms) == 1 else "none",
                fillcolor=_hex_to_rgba(color, 0.08),
                hovertemplate=f"<b>{sym}</b>  $%{{y:,.2f}}<extra></extra>",
            ))
        if has_data:
            fig.update_layout(
                **LAYOUT_BASE,
                title=f"Price History — {st.session_state.mp_time_range}",
                yaxis=dict(gridcolor="#1e1e2e", showgrid=True, tickprefix="$"),
                hovermode="x unified",
            )
        else:
            st.info("No stored history for the selected symbols/range. Run the scraper to build history.")
            fig = None

    if fig and fig.data:
        st.plotly_chart(fig, use_container_width=True)

    # ── Market summary row ───────────────────────────────────────────────────
    rows_with_data = [r for r in market_rows if r.get("change_pct") is not None]
    if rows_with_data:
        best  = max(rows_with_data, key=lambda r: r["change_pct"])
        worst = min(rows_with_data, key=lambda r: r["change_pct"])
        most_volatile = max(rows_with_data, key=lambda r: abs(r["change_pct"]))

        s1, s2, s3 = st.columns(3)
        def _sign(v): return "+" if v >= 0 else ""
        s1.markdown(
            f'<div style="background:#1a2e1a;border:1px solid #166534;border-radius:8px;'
            f'padding:12px 16px;text-align:center">'
            f'<div style="font-size:0.68rem;color:#64748b;text-transform:uppercase;letter-spacing:.08em;margin-bottom:4px">Best Today</div>'
            f'<div style="font-size:1.1rem;font-weight:800;color:#34d399">{best["symbol"]}</div>'
            f'<div style="font-size:0.9rem;color:#86efac">{_sign(best["change_pct"])}{best["change_pct"]:.2f}%</div>'
            f'</div>', unsafe_allow_html=True,
        )
        s2.markdown(
            f'<div style="background:#2e1a1a;border:1px solid #991b1b;border-radius:8px;'
            f'padding:12px 16px;text-align:center">'
            f'<div style="font-size:0.68rem;color:#64748b;text-transform:uppercase;letter-spacing:.08em;margin-bottom:4px">Worst Today</div>'
            f'<div style="font-size:1.1rem;font-weight:800;color:#f87171">{worst["symbol"]}</div>'
            f'<div style="font-size:0.9rem;color:#fca5a5">{_sign(worst["change_pct"])}{worst["change_pct"]:.2f}%</div>'
            f'</div>', unsafe_allow_html=True,
        )
        s3.markdown(
            f'<div style="background:#1e1e2e;border:1px solid #475569;border-radius:8px;'
            f'padding:12px 16px;text-align:center">'
            f'<div style="font-size:0.68rem;color:#64748b;text-transform:uppercase;letter-spacing:.08em;margin-bottom:4px">Most Volatile</div>'
            f'<div style="font-size:1.1rem;font-weight:800;color:#e2e8f0">{most_volatile["symbol"]}</div>'
            f'<div style="font-size:0.9rem;color:#94a3b8">|{abs(most_volatile["change_pct"]):.2f}%| swing</div>'
            f'</div>', unsafe_allow_html=True,
        )

    # ── AI Market Summary ────────────────────────────────────────────────────
    st.markdown("<div style='height:10px'></div>", unsafe_allow_html=True)

    @st.cache_data(ttl=3600)
    def load_market_summary_cache(hour_key: str):
        try:
            conn = get_db_connection()
            row  = conn.execute(
                "SELECT response FROM agent_cache WHERE query_hash = ?", (hour_key,)
            ).fetchone()
            conn.close()
            return dict(row) if row else None
        except Exception:
            return None

    def _save_market_summary(hour_key: str, text: str):
        try:
            conn = get_db_connection()
            conn.execute(
                """
                INSERT INTO agent_cache (query_hash, query_text, response, created_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(query_hash) DO UPDATE
                  SET response=excluded.response, created_at=excluded.created_at
                """,
                (hour_key, "market_summary", json.dumps({"text": text}),
                 datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")),
            )
            conn.commit()
            conn.close()
        except Exception:
            pass

    ms_hour_key = "market_summary_" + datetime.utcnow().strftime("%Y-%m-%d_%H")
    ms_cached   = load_market_summary_cache(ms_hour_key)

    ms_btn_col, ms_result_col = st.columns([1, 3])
    with ms_btn_col:
        explain_market = st.button(
            "📊 Explain Today's Market",
            use_container_width=True,
            help="AI summary of current price moves + headlines (cached 1 hour)",
        )

    if explain_market or ms_cached:
        if ms_cached and not explain_market:
            try:
                ms_text = json.loads(ms_cached["response"]).get("text", "")
            except Exception:
                ms_text = ""
        else:
            # Build context: prices + recent headlines
            price_lines = []
            for r in market_rows[:8]:
                sym = r.get("symbol", "")
                prc = r.get("price", 0)
                chg = r.get("change_pct", 0)
                sign = "+" if chg >= 0 else ""
                price_lines.append(f"{sym}: ${prc:,.2f} ({sign}{chg:.2f}%)")

            headline_rows = []
            try:
                _conn = get_db_connection()
                headline_rows = _conn.execute(
                    "SELECT title FROM articles ORDER BY scraped_at DESC LIMIT 5"
                ).fetchall()
                _conn.close()
            except Exception:
                pass
            headlines = [r["title"] for r in headline_rows if r["title"]]

            all_changes = [abs(r.get("change_pct", 0)) for r in market_rows if r.get("change_pct") is not None]
            flat_market = all_changes and max(all_changes) < 0.5

            if flat_market:
                ms_text = "Markets are relatively flat today with no major moves exceeding 0.5% across tracked symbols."
            else:
                ms_prompt = (
                    "You are a concise market analyst. In 3-4 sentences, explain what is driving today's "
                    "market moves based on the price data and recent headlines below. Be specific — name "
                    "symbols, sectors, or macro factors. No bullet points; plain prose only.\n\n"
                    f"PRICE DATA:\n" + "\n".join(price_lines) +
                    "\n\nRECENT HEADLINES:\n" + "\n".join(f"- {h}" for h in headlines)
                )
                with st.spinner("Analysing today's market..."):
                    try:
                        _client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
                        _msg    = _client.messages.create(
                            model="claude-sonnet-4-6",
                            max_tokens=300,
                            messages=[{"role": "user", "content": ms_prompt}],
                        )
                        ms_text = _msg.content[0].text.strip()
                        _save_market_summary(ms_hour_key, ms_text)
                        load_market_summary_cache.clear()
                    except Exception as e:
                        ms_text = f"Market summary unavailable: {e}"

        if ms_text:
            st.markdown(
                f'<div style="background:#1a1f2e;border:1px solid #3b4a6b;border-radius:8px;'
                f'padding:14px 20px;color:#cbd5e1;font-size:0.88rem;line-height:1.7;margin-top:8px">'
                f'<span style="font-size:0.65rem;font-weight:700;color:#60a5fa;text-transform:uppercase;'
                f'letter-spacing:.08em;display:block;margin-bottom:6px">Powered by AI · Updated hourly</span>'
                f'{ms_text}</div>',
                unsafe_allow_html=True,
            )

    # ── Sector Heatmap ───────────────────────────────────────────────────────
    st.divider()
    st.markdown('<div class="section-header">Sector Heatmap</div>', unsafe_allow_html=True)

    SECTOR_ETFS = [
        ("XLK",  "Technology"),
        ("XLF",  "Financials"),
        ("XLV",  "Healthcare"),
        ("XLE",  "Energy"),
        ("XLI",  "Industrials"),
        ("XLC",  "Communications"),
        ("XLY",  "Consumer Disc."),
        ("XLP",  "Consumer Staples"),
        ("XLB",  "Materials"),
        ("XLRE", "Real Estate"),
        ("XLU",  "Utilities"),
    ]

    @st.cache_data(ttl=300)
    def load_sector_data() -> list[dict]:
        try:
            import yfinance as yf
            results = []
            for sym, name in SECTOR_ETFS:
                try:
                    info  = yf.Ticker(sym).fast_info
                    price = getattr(info, "last_price", None)
                    prev  = getattr(info, "previous_close", None)
                    if price and prev and prev > 0:
                        chg = (price - prev) / prev * 100
                    elif price:
                        chg = 0.0
                    else:
                        price, chg = 0.0, 0.0
                    results.append({
                        "symbol":  sym,
                        "name":    name,
                        "price":   round(price, 2),
                        "chg":     round(chg, 2),
                    })
                except Exception:
                    results.append({"symbol": sym, "name": name, "price": 0.0, "chg": 0.0})
            return results
        except Exception as e:
            print(f"[heatmap] Sector data fetch failed: {e}")
            return []

    def _sector_card_colors(chg: float) -> tuple:
        """Return (bg, border, text) hex colors based on % change."""
        if chg > 1.0:
            return "#14532d", "#16a34a", "#4ade80"
        elif chg > 0:
            return "#1a2e1a", "#166534", "#86efac"
        elif chg > -1.0:
            return "#2e1a1a", "#991b1b", "#fca5a5"
        else:
            return "#450a0a", "#7f1d1d", "#f87171"

    sector_data = load_sector_data()
    if sector_data:
        COLS_PER_ROW = 4
        for row_start in range(0, len(sector_data), COLS_PER_ROW):
            row_items = sector_data[row_start:row_start + COLS_PER_ROW]
            # Pad to full row width so columns stay even
            cols = st.columns(COLS_PER_ROW)
            for col, item in zip(cols, row_items):
                bg, border, txt = _sector_card_colors(item["chg"])
                sign  = "+" if item["chg"] >= 0 else ""
                price = f'${item["price"]:,.2f}' if item["price"] else "N/A"
                col.markdown(
                    f'<div title="{item["symbol"]} — {price}" style="'
                    f'background:{bg};border:1px solid {border};border-radius:8px;'
                    f'padding:14px 8px;text-align:center;cursor:default;'
                    f'transition:opacity .15s">'
                    f'<div style="font-size:1.35rem;font-weight:800;color:{txt};'
                    f'line-height:1.1">{sign}{item["chg"]:.2f}%</div>'
                    f'<div style="font-size:0.72rem;font-weight:700;color:{txt};'
                    f'opacity:.75;margin-top:4px;text-transform:uppercase;'
                    f'letter-spacing:.04em">{item["name"]}</div>'
                    f'<div style="font-size:0.65rem;color:{txt};opacity:.5;'
                    f'margin-top:2px">{item["symbol"]}</div>'
                    f'</div>',
                    unsafe_allow_html=True,
                )
    else:
        st.caption("Sector data unavailable — check yfinance connectivity.")

    st.divider()

    # ── Sections 2 & 3: News + Tech ──────────────────────────────────────────
    news_col, gap_col, tech_col = st.columns([10, 1, 10])

    NEWS_SOURCES = ["NPR Business", "The Guardian", "CNBC", "BBC Business", "BBC World"]
    TECH_SOURCES = ["Hacker News", "TechCrunch"]

    with news_col:
        st.markdown('<div class="section-header">Global News Feed</div>', unsafe_allow_html=True)
        news_articles = load_articles_for_feed(["business", "world"], limit=100)
        if news_articles:
            render_feed(news_articles, NEWS_SOURCES, feed_key="news")
        else:
            st.info("No news articles yet. Run the scraper.")

    with gap_col:
        pass  # visual breathing room

    with tech_col:
        st.markdown('<div class="section-header">Tech Radar</div>', unsafe_allow_html=True)
        tech_articles = load_articles_for_feed(["tech"], limit=100)
        if tech_articles:
            render_feed(tech_articles, TECH_SOURCES, feed_key="tech")
        else:
            st.info("No tech articles yet. Run the scraper.")

# ---------------------------------------------------------------------------
# COMPANY VS COMPANY PAGE
# ---------------------------------------------------------------------------

elif page == "Company vs Company":
    st.markdown("# 🏢 Company vs Company")
    st.caption("AI-powered competitive intelligence — powered by Claude")
    st.divider()

    # ── Input form ───────────────────────────────────────────────────────────
    for key in ("cvc_company_a", "cvc_company_b", "cvc_result"):
        if key not in st.session_state:
            st.session_state[key] = "" if key != "cvc_result" else None

    col_a, col_b = st.columns(2)
    with col_a:
        st.session_state.cvc_company_a = st.text_input(
            "Company A",
            value=st.session_state.cvc_company_a,
            placeholder="e.g. Apple",
        )
    with col_b:
        st.session_state.cvc_company_b = st.text_input(
            "Company B",
            value=st.session_state.cvc_company_b,
            placeholder="e.g. Microsoft",
        )

    both_filled = bool(
        st.session_state.cvc_company_a.strip()
        and st.session_state.cvc_company_b.strip()
    )
    run = st.button("⚡ Run Analysis", disabled=not both_filled)

    if run:
        a = st.session_state.cvc_company_a.strip()
        b = st.session_state.cvc_company_b.strip()
        with st.spinner(f"Gathering intelligence on {a} vs {b}..."):
            try:
                st.session_state.cvc_result = compare_companies(a, b)
            except Exception as e:
                st.error(f"Analysis failed: {e}")
                st.session_state.cvc_result = None

    # ── Results renderer ─────────────────────────────────────────────────────
    result = st.session_state.get("cvc_result")
    if result:
        a_name = st.session_state.cvc_company_a.strip()
        b_name = st.session_state.cvc_company_b.strip()

        # Insufficient data guard
        if result.get("insufficient_data"):
            st.warning(
                f"⚠️ Insufficient data for a full analysis.\n\n"
                f"{result.get('verdict', '')}"
            )
        else:
            # ── Momentum score helper ────────────────────────────────────────
            def score_color(s: int) -> str:
                if s >= 7: return "#16a34a"
                if s >= 5: return "#d97706"
                return "#dc2626"

            def score_bar(s: int) -> str:
                color = score_color(s)
                pct   = s * 10
                return (
                    f'<div style="margin:6px 0 12px">'
                    f'<span style="font-size:2rem;font-weight:800;color:{color}">{s}</span>'
                    f'<span style="font-size:1rem;color:#64748b"> / 10</span>'
                    f'<div style="background:#2e2e3e;border-radius:999px;height:8px;margin-top:6px">'
                    f'<div style="background:{color};width:{pct}%;height:8px;border-radius:999px"></div>'
                    f'</div></div>'
                )

            def green_bullets(items: list) -> str:
                return "".join(
                    f'<li style="color:#94a3b8;margin-bottom:4px">'
                    f'<span style="color:#34d399;margin-right:6px">●</span>{item}</li>'
                    for item in (items or ["—"])
                )

            def red_bullets(items: list) -> str:
                return "".join(
                    f'<li style="color:#94a3b8;margin-bottom:4px">'
                    f'<span style="color:#f87171;margin-right:6px">●</span>{item}</li>'
                    for item in (items or ["—"])
                )

            def timeline(items: list) -> str:
                return "".join(
                    f'<li style="color:#94a3b8;margin-bottom:4px">'
                    f'<span style="color:#60a5fa;margin-right:6px">→</span>{item}</li>'
                    for item in (items or ["—"])
                )

            def kv_rows(pairs: list) -> str:
                rows = ""
                for i, (k, v) in enumerate(pairs):
                    bg = "#1a1a2e" if i % 2 == 0 else "#1e1e2e"
                    rows += (
                        f'<tr style="background:{bg}">'
                        f'<td style="padding:6px 10px;color:#64748b;font-size:0.78rem;'
                        f'white-space:nowrap;vertical-align:top"><b>{k}</b></td>'
                        f'<td style="padding:6px 10px;color:#cbd5e1;font-size:0.82rem;'
                        f'vertical-align:top">{v}</td></tr>'
                    )
                return f'<table style="width:100%;border-collapse:collapse">{rows}</table>'

            def tags(items: list) -> str:
                if not items:
                    return '<span style="color:#475569;font-size:0.8rem">None reported</span>'
                return " ".join(
                    f'<span style="background:#1e3a5f;color:#93c5fd;padding:2px 8px;'
                    f'border-radius:999px;font-size:0.74rem;margin-right:4px">{t}</span>'
                    for t in items
                )

            def lightbulbs(items: list) -> str:
                return "".join(
                    f'<div style="background:#1e2a1e;border-left:3px solid #34d399;'
                    f'border-radius:4px;padding:8px 12px;margin-bottom:6px;'
                    f'color:#a7f3d0;font-size:0.84rem">💡 {item}</div>'
                    for item in (items or ["—"])
                )

            def company_card(cd: dict) -> str:
                fs = cd.get("financial_signals", {})
                ts = cd.get("tech_signals", {})
                s  = cd.get("momentum_score", 0)
                return (
                    f'<div style="background:#1e1e2e;border:1px solid #2e2e3e;'
                    f'border-radius:10px;padding:20px">'
                    + score_bar(s)
                    + f'<p style="font-size:0.7rem;color:#64748b;text-transform:uppercase;'
                    f'letter-spacing:.08em;margin:14px 0 4px"><b>Key Strengths</b></p>'
                    + f'<ul style="padding-left:4px;margin:0 0 10px">{green_bullets(cd.get("key_strengths", []))}</ul>'
                    + f'<p style="font-size:0.7rem;color:#64748b;text-transform:uppercase;'
                    f'letter-spacing:.08em;margin:10px 0 4px"><b>Key Risks</b></p>'
                    + f'<ul style="padding-left:4px;margin:0 0 10px">{red_bullets(cd.get("key_risks", []))}</ul>'
                    + f'<p style="font-size:0.7rem;color:#64748b;text-transform:uppercase;'
                    f'letter-spacing:.08em;margin:10px 0 4px"><b>Recent Moves</b></p>'
                    + f'<ul style="padding-left:4px;margin:0 0 14px">{timeline(cd.get("recent_moves", []))}</ul>'
                    + f'<p style="font-size:0.7rem;color:#64748b;text-transform:uppercase;'
                    f'letter-spacing:.08em;margin:10px 0 6px"><b>Financial Signals</b></p>'
                    + kv_rows([
                        ("Revenue Trend",       fs.get("revenue_trend", "N/A")),
                        ("Cost Pressures",      fs.get("cost_pressure_signals", "N/A")),
                        ("Investment Activity", fs.get("investment_activity", "N/A")),
                        ("Analyst Sentiment",   fs.get("analyst_sentiment", "N/A")),
                    ])
                    + f'<p style="font-size:0.7rem;color:#64748b;text-transform:uppercase;'
                    f'letter-spacing:.08em;margin:14px 0 6px"><b>Tech Signals</b></p>'
                    + f'<div style="margin-bottom:8px">{tags(ts.get("product_launches", []))}</div>'
                    + kv_rows([
                        ("Engineering Hiring", ts.get("engineering_hiring_trend", "N/A")),
                        ("AI / ML Activity",   ts.get("ai_ml_activity", "N/A")),
                        ("Technical Debt",     ts.get("technical_debt_signals", "N/A")),
                    ])
                    + f'<p style="font-size:0.7rem;color:#64748b;text-transform:uppercase;'
                    f'letter-spacing:.08em;margin:14px 0 6px"><b>Consulting Angles</b></p>'
                    + lightbulbs(cd.get("consulting_angles", []))
                    + '</div>'
                )

            # ── Two-column company cards ──────────────────────────────────────
            col_left, col_right = st.columns(2)
            with col_left:
                st.markdown(f"### {result['company_a'].get('name', a_name)}")
                st.markdown(company_card(result["company_a"]), unsafe_allow_html=True)
            with col_right:
                st.markdown(f"### {result['company_b'].get('name', b_name)}")
                st.markdown(company_card(result["company_b"]), unsafe_allow_html=True)

            st.divider()

            # ── Strategic Opportunities ───────────────────────────────────────
            st.markdown('<div class="section-header">Strategic Opportunities</div>', unsafe_allow_html=True)
            opps = result.get("strategic_opportunities", [])
            for opp in opps:
                st.markdown(
                    f'<div style="background:#1e1e2e;border-left:3px solid #3b82f6;'
                    f'border-radius:4px;padding:10px 14px;margin-bottom:8px;color:#cbd5e1;'
                    f'font-size:0.88rem">→ {opp}</div>',
                    unsafe_allow_html=True,
                )

            # ── M&A + Regulatory ──────────────────────────────────────────────
            ma_col, reg_col = st.columns(2)
            with ma_col:
                st.info(f"**M&A Signals**\n\n{result.get('merger_acquisition_signals', 'N/A')}")
            with reg_col:
                st.warning(f"**Regulatory Risk**\n\n{result.get('regulatory_risk', 'N/A')}")

            # ── Consulting Recommendation ─────────────────────────────────────
            st.markdown('<div class="section-header">Consulting Recommendation</div>', unsafe_allow_html=True)
            st.markdown(
                f'<div style="background:#1a2a1a;border:1px solid #16a34a;border-radius:8px;'
                f'padding:20px 24px;color:#d1fae5;font-size:0.93rem;line-height:1.7">'
                f'{result.get("consulting_recommendation", "")}</div>',
                unsafe_allow_html=True,
            )

            st.divider()

            # ── Verdict card ──────────────────────────────────────────────────
            st.markdown('<div class="section-header">Verdict</div>', unsafe_allow_html=True)
            st.markdown(
                f'<div style="background:#1e1e2e;border:1px solid #2e2e3e;border-radius:8px;padding:20px 24px">'
                f'<p style="color:#e2e8f0;font-size:0.95rem;line-height:1.7;margin-bottom:14px">'
                f'{result.get("verdict", "")}</p>'
                + kv_rows([
                    ("Who has momentum", f'<b style="color:#f0f0f0">{result.get("who_has_momentum", "N/A")}</b>'),
                    ("Key battleground", result.get("key_battleground", "N/A")),
                    ("Watch for",        result.get("watch_for", "N/A")),
                ])
                + '</div>',
                unsafe_allow_html=True,
            )

            st.divider()

            # ── PDF export ────────────────────────────────────────────────────
            today_str = datetime.utcnow().strftime("%Y-%m-%d")
            filename  = f"BriefRoom_{a_name}_vs_{b_name}_{today_str}.pdf".replace(" ", "_")
            try:
                pdf_bytes = generate_pdf_report(result, a_name, b_name)
                st.download_button(
                    label="📄 Export as PDF Report",
                    data=pdf_bytes,
                    file_name=filename,
                    mime="application/pdf",
                )
            except Exception as e:
                st.error(f"PDF generation failed: {e}")

        # ── Disclaimer ────────────────────────────────────────────────────────
        st.caption(
            "Analysis based on publicly available data and AI reasoning. "
            "Not financial advice."
        )

# ---------------------------------------------------------------------------
# SO WHAT? PAGE
# ---------------------------------------------------------------------------

elif page == "So What?":
    st.markdown("# 🔍 So What?")
    st.caption("Select a recent article or paste any headline — get an instant consultant-grade breakdown.")
    st.divider()

    for k, default in [("sw_input", ""), ("sw_result", None)]:
        if k not in st.session_state:
            st.session_state[k] = default

    @st.cache_data(ttl=300)
    def load_recent_for_sw(limit: int = 15):
        conn = get_db_connection()
        rows = conn.execute("""
            SELECT title, summary, source, category, url
            FROM articles ORDER BY scraped_at DESC LIMIT ?
        """, (limit,)).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    news_col, analysis_col = st.columns([2, 3])

    # ── Left: recent news list ────────────────────────────────────────────────
    with news_col:
        st.markdown(
            '<div style="font-size:0.7rem;font-weight:700;color:#64748b;text-transform:uppercase;'
            'letter-spacing:.08em;margin-bottom:10px">Recent Articles</div>',
            unsafe_allow_html=True,
        )
        recent = load_recent_for_sw(15)
        if recent:
            for idx, art in enumerate(recent):
                source  = art["source"] or ""
                title   = (art["title"] or "")[:80] + ("…" if len(art["title"] or "") > 80 else "")
                badge   = badge_html(source)
                summary = art.get("summary") or ""

                st.markdown(
                    f'<div style="background:#1e1e2e;border:1px solid #2e2e3e;border-radius:6px;'
                    f'padding:8px 10px;margin-bottom:5px">'
                    f'{badge}'
                    f'<span style="font-size:0.78rem;color:#cbd5e1;line-height:1.4;display:block;'
                    f'margin-top:4px">{title}</span>'
                    f'</div>',
                    unsafe_allow_html=True,
                )
                if st.button("Analyze →", key=f"sw_pick_{idx}", use_container_width=True):
                    text = art["title"] or ""
                    if summary:
                        text += f"\n\n{summary}"
                    st.session_state.sw_input = text
                    st.session_state.sw_result = None
                    st.rerun()
        else:
            st.caption("No articles yet. Run the scraper.")

    # ── Right: text area + analysis ───────────────────────────────────────────
    with analysis_col:
        st.session_state.sw_input = st.text_area(
            "News input",
            value=st.session_state.sw_input,
            placeholder="Paste any headline or news story here...",
            height=120,
            label_visibility="collapsed",
        )

        analyze = st.button(
            "🔍 Analyze",
            disabled=not st.session_state.sw_input.strip(),
        )

        if analyze:
            with st.spinner("Thinking like a consultant..."):
                try:
                    st.session_state.sw_result = explain_so_what(st.session_state.sw_input)
                except Exception as e:
                    st.error(f"Analysis failed: {e}")
                    st.session_state.sw_result = None

        result = st.session_state.sw_result
        if result:
            # ── What Happened ─────────────────────────────────────────────────
            st.markdown('<div class="section-header">What Happened</div>', unsafe_allow_html=True)
            st.markdown(
                f'<div style="background:#1e1e2e;border:1px solid #2e2e3e;border-radius:8px;'
                f'padding:16px 20px;color:#e2e8f0;font-size:0.95rem;line-height:1.7">'
                f'{result.get("what_happened", "")}</div>',
                unsafe_allow_html=True,
            )
            st.markdown("<div style='height:14px'></div>", unsafe_allow_html=True)

            # ── Winners / Losers ──────────────────────────────────────────────
            win_col2, lose_col2 = st.columns(2)
            with win_col2:
                st.markdown('<div class="section-header">Winners</div>', unsafe_allow_html=True)
                for w in (result.get("who_wins") or []):
                    st.markdown(
                        f'<div style="background:#1a2e1a;border:1px solid #166534;border-radius:6px;'
                        f'padding:8px 14px;margin-bottom:6px;color:#bbf7d0;font-size:0.87rem">'
                        f'<span style="color:#34d399;margin-right:8px">●</span>{w}</div>',
                        unsafe_allow_html=True,
                    )
            with lose_col2:
                st.markdown('<div class="section-header">Losers</div>', unsafe_allow_html=True)
                for l in (result.get("who_loses") or []):
                    st.markdown(
                        f'<div style="background:#2e1a1a;border:1px solid #991b1b;border-radius:6px;'
                        f'padding:8px 14px;margin-bottom:6px;color:#fecaca;font-size:0.87rem">'
                        f'<span style="color:#f87171;margin-right:8px">●</span>{l}</div>',
                        unsafe_allow_html=True,
                    )

            st.markdown("<div style='height:14px'></div>", unsafe_allow_html=True)

            # ── Second Order Effects ───────────────────────────────────────────
            st.markdown('<div class="section-header">Second Order Effects</div>', unsafe_allow_html=True)
            for i, effect in enumerate(result.get("second_order_effects") or [], 1):
                st.markdown(
                    f'<div style="background:#1e1e2e;border:1px solid #2e2e3e;border-radius:6px;'
                    f'padding:10px 14px;margin-bottom:6px;color:#cbd5e1;font-size:0.88rem">'
                    f'<span style="color:#60a5fa;font-weight:700;margin-right:10px">{i}</span>{effect}</div>',
                    unsafe_allow_html=True,
                )

            st.markdown("<div style='height:14px'></div>", unsafe_allow_html=True)

            # ── What To Watch ─────────────────────────────────────────────────
            st.markdown('<div class="section-header">What To Watch</div>', unsafe_allow_html=True)
            st.info(result.get("what_to_watch", ""))

            st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)

            # ── Consultant Take ────────────────────────────────────────────────
            st.markdown('<div class="section-header">Consultant Take</div>', unsafe_allow_html=True)
            st.markdown(
                f'<div style="background:#1a2a1a;border:1px solid #16a34a;border-radius:8px;'
                f'padding:20px 24px;color:#d1fae5;font-size:0.93rem;line-height:1.7">'
                f'{result.get("consultant_take", "")}</div>',
                unsafe_allow_html=True,
            )

# ---------------------------------------------------------------------------
# COUNTRY RISK PAGE
# ---------------------------------------------------------------------------

elif page == "Country Risk":
    st.markdown("# 🌍 Country Risk Brief")
    st.caption("Geopolitical and economic risk analysis — powered by Claude")
    st.divider()

    for key in ("cr_country", "cr_result"):
        if key not in st.session_state:
            st.session_state[key] = "" if key != "cr_result" else None

    st.session_state.cr_country = st.text_input(
        "Country",
        value=st.session_state.cr_country,
        placeholder="e.g. Brazil, Germany, India",
        label_visibility="collapsed",
    )

    analyze = st.button(
        "🌍 Analyze Country",
        disabled=not st.session_state.cr_country.strip(),
    )

    if analyze:
        country = st.session_state.cr_country.strip()
        with st.spinner(f"Building risk brief for {country}..."):
            try:
                st.session_state.cr_result = get_country_risk(country)
            except Exception as e:
                st.error(f"Analysis failed: {e}")
                st.session_state.cr_result = None

    result = st.session_state.cr_result
    if result:
        # ── Risk score helpers ────────────────────────────────────────────────
        def risk_color(score: int) -> str:
            if score <= 3: return "#16a34a"
            if score <= 6: return "#d97706"
            return "#dc2626"

        def trend_arrow(trend: str) -> str:
            return {"Improving": "↓", "Stable": "→", "Deteriorating": "↑"}.get(trend, "→")

        def trend_color(trend: str) -> str:
            return {"Improving": "#16a34a", "Stable": "#d97706", "Deteriorating": "#dc2626"}.get(trend, "#94a3b8")

        def risk_level_bg(level: str) -> str:
            return {
                "Low":        "#1a2e1a",
                "Moderate":   "#2a2a1a",
                "Elevated":   "#2a1f0e",
                "High":       "#2e1a1a",
                "Critical":   "#3b0a0a",
            }.get(level, "#1e1e2e")

        def risk_level_fg(level: str) -> str:
            return {
                "Low":        "#86efac",
                "Moderate":   "#fde68a",
                "Elevated":   "#fdba74",
                "High":       "#fca5a5",
                "Critical":   "#f87171",
            }.get(level, "#94a3b8")

        overall   = result.get("overall_risk_score", 0)
        level     = result.get("risk_level", "")
        trend     = result.get("risk_trend", "Stable")
        country   = result.get("country", st.session_state.cr_country)
        lvl_bg    = risk_level_bg(level)
        lvl_fg    = risk_level_fg(level)
        t_color   = trend_color(trend)
        arrow     = trend_arrow(trend)
        score_clr = risk_color(overall)

        # ── Top row ───────────────────────────────────────────────────────────
        st.markdown(
            f'<div style="background:#1e1e2e;border:1px solid #2e2e3e;border-radius:10px;'
            f'padding:20px 28px;display:flex;align-items:center;gap:28px;margin-bottom:20px;flex-wrap:wrap">'
            f'<div style="flex:1;min-width:160px">'
            f'  <div style="font-size:1.8rem;font-weight:800;color:#e2e8f0">{country}</div>'
            f'  <div style="font-size:0.75rem;color:#64748b;margin-top:2px">Country Risk Brief</div>'
            f'</div>'
            f'<div style="text-align:center">'
            f'  <div style="font-size:3rem;font-weight:900;color:{score_clr};line-height:1">{overall}</div>'
            f'  <div style="font-size:0.7rem;color:#64748b">/ 10 RISK SCORE</div>'
            f'</div>'
            f'<div style="background:{lvl_bg};border:1px solid {lvl_fg};border-radius:8px;'
            f'padding:8px 18px;text-align:center">'
            f'  <div style="font-size:1rem;font-weight:700;color:{lvl_fg}">{level}</div>'
            f'  <div style="font-size:0.68rem;color:#64748b;margin-top:2px">Risk Level</div>'
            f'</div>'
            f'<div style="text-align:center">'
            f'  <div style="font-size:2.2rem;font-weight:800;color:{t_color};line-height:1">{arrow}</div>'
            f'  <div style="font-size:0.68rem;color:#64748b;margin-top:2px">{trend}</div>'
            f'</div>'
            f'</div>',
            unsafe_allow_html=True,
        )

        # ── Three risk dimension columns ──────────────────────────────────────
        pol_col, econ_col, biz_col = st.columns(3)

        def risk_dim_card(col, title: str, dim: dict):
            score = dim.get("score", 0)
            color = risk_color(score)
            with col:
                st.markdown(
                    f'<div style="background:#1e1e2e;border:1px solid #2e2e3e;border-radius:8px;'
                    f'padding:16px 18px;height:100%">'
                    f'<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px">'
                    f'  <span style="font-size:0.7rem;font-weight:700;color:#64748b;text-transform:uppercase;letter-spacing:.08em">{title}</span>'
                    f'  <span style="font-size:1.4rem;font-weight:800;color:{color}">{score}<span style="font-size:0.75rem;color:#64748b">/10</span></span>'
                    f'</div>'
                    f'<p style="font-size:0.83rem;color:#94a3b8;line-height:1.6;margin-bottom:10px">{dim.get("summary","")}</p>'
                    + "".join(
                        f'<div style="font-size:0.78rem;color:#cbd5e1;padding:3px 0;border-bottom:1px solid #2e2e3e">'
                        f'<span style="color:#60a5fa;margin-right:6px">·</span>{f}</div>'
                        for f in (dim.get("key_factors") or [])
                    )
                    + '</div>',
                    unsafe_allow_html=True,
                )

        risk_dim_card(pol_col,  "Political Risk",       result.get("political_risk",      {}))
        risk_dim_card(econ_col, "Economic Risk",        result.get("economic_risk",        {}))
        risk_dim_card(biz_col,  "Business Environment", result.get("business_environment", {}))

        st.markdown("<div style='height:20px'></div>", unsafe_allow_html=True)

        # ── Opportunities / Threats ───────────────────────────────────────────
        opp_col, thr_col = st.columns(2)

        with opp_col:
            st.markdown('<div class="section-header">Opportunities</div>', unsafe_allow_html=True)
            for o in (result.get("opportunities") or []):
                st.markdown(
                    f'<div style="background:#1a2e1a;border:1px solid #166534;border-radius:6px;'
                    f'padding:9px 14px;margin-bottom:6px;color:#bbf7d0;font-size:0.86rem">'
                    f'<span style="color:#34d399;margin-right:8px">+</span>{o}</div>',
                    unsafe_allow_html=True,
                )

        with thr_col:
            st.markdown('<div class="section-header">Threats</div>', unsafe_allow_html=True)
            for t in (result.get("threats") or []):
                st.markdown(
                    f'<div style="background:#2e1a1a;border:1px solid #991b1b;border-radius:6px;'
                    f'padding:9px 14px;margin-bottom:6px;color:#fecaca;font-size:0.86rem">'
                    f'<span style="color:#f87171;margin-right:8px">−</span>{t}</div>',
                    unsafe_allow_html=True,
                )

        st.markdown("<div style='height:16px'></div>", unsafe_allow_html=True)

        # ── Consultant Recommendation ─────────────────────────────────────────
        st.markdown('<div class="section-header">Consultant Recommendation</div>', unsafe_allow_html=True)
        st.markdown(
            f'<div style="background:#1a2a1a;border:1px solid #16a34a;border-radius:8px;'
            f'padding:20px 24px;color:#d1fae5;font-size:0.93rem;line-height:1.7">'
            f'{result.get("consultant_recommendation","")}</div>',
            unsafe_allow_html=True,
        )

        st.markdown("<div style='height:16px'></div>", unsafe_allow_html=True)

        # ── Comparable Markets ────────────────────────────────────────────────
        comparables = result.get("comparable_markets") or []
        if comparables:
            st.markdown('<div class="section-header">Comparable Markets</div>', unsafe_allow_html=True)
            pills = " ".join(
                f'<span style="background:#1e2a3a;color:#93c5fd;padding:4px 14px;'
                f'border-radius:999px;font-size:0.8rem;margin-right:6px;display:inline-block;'
                f'margin-bottom:6px">{c}</span>'
                for c in comparables
            )
            st.markdown(f'<div style="padding:4px 0">{pills}</div>', unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# MEETING PREP PAGE
# ---------------------------------------------------------------------------

elif page == "Meeting Prep":
    st.markdown("# 📋 Meeting Prep Brief")
    st.caption("AI-powered preparation briefing for any client or company meeting — powered by Claude")
    st.divider()

    for k, default in [("mp_company", ""), ("mp_context", ""), ("mp_result", None)]:
        if k not in st.session_state:
            st.session_state[k] = default

    inp_col, ctx_col = st.columns(2)
    with inp_col:
        st.session_state.mp_company = st.text_input(
            "Company or client name",
            value=st.session_state.mp_company,
            placeholder="e.g. Salesforce",
        )
    with ctx_col:
        st.session_state.mp_context = st.text_input(
            "Meeting context (optional)",
            value=st.session_state.mp_context,
            placeholder="e.g. pitch meeting, due diligence, quarterly review",
        )

    prep_btn = st.button(
        "📋 Prepare Brief",
        disabled=not st.session_state.mp_company.strip(),
    )

    if prep_btn:
        company = st.session_state.mp_company.strip()
        context = st.session_state.mp_context.strip()
        with st.spinner(f"Preparing your meeting brief for {company}..."):
            try:
                st.session_state.mp_result = get_meeting_prep(company, context)
            except Exception as e:
                st.error(f"Brief generation failed: {e}")
                st.session_state.mp_result = None

    result = st.session_state.mp_result
    if result:
        company_name = result.get("company", st.session_state.mp_company)
        meet_ctx     = result.get("meeting_context", "")

        # ── Open with this ─────────────────────────────────────────────────────
        opener = result.get("one_line_opener", "")
        if opener:
            st.markdown('<div class="section-header">Open With This</div>', unsafe_allow_html=True)
            st.markdown(
                f'<div style="background:#1a2540;border:1px solid #3b5998;border-radius:10px;'
                f'padding:20px 24px;margin-bottom:16px">'
                f'<div style="font-size:0.65rem;font-weight:700;color:#60a5fa;text-transform:uppercase;'
                f'letter-spacing:.1em;margin-bottom:8px">Your opening line</div>'
                f'<div style="font-size:1.05rem;font-style:italic;color:#e2e8f0;line-height:1.6">'
                f'"{opener}"</div>'
                f'</div>',
                unsafe_allow_html=True,
            )

        # ── Executive Summary ──────────────────────────────────────────────────
        st.markdown('<div class="section-header">Executive Summary</div>', unsafe_allow_html=True)
        st.markdown(
            f'<div style="background:#1e1e2e;border:1px solid #2e2e3e;border-radius:8px;'
            f'padding:16px 20px;color:#cbd5e1;font-size:0.93rem;line-height:1.7">'
            f'{result.get("executive_summary","")}</div>',
            unsafe_allow_html=True,
        )

        st.markdown("<div style='height:16px'></div>", unsafe_allow_html=True)

        # ── Snapshot + Hot Topics ──────────────────────────────────────────────
        snap_col, hot_col = st.columns(2)
        snap = result.get("company_snapshot", {})

        with snap_col:
            st.markdown('<div class="section-header">Company Snapshot</div>', unsafe_allow_html=True)
            for label, key in [
                ("What They Do",       "what_they_do"),
                ("Size & Scale",       "size_and_scale"),
                ("Recent Performance", "recent_performance"),
                ("Leadership Signals", "leadership_signals"),
            ]:
                val = snap.get(key, "N/A")
                st.markdown(
                    f'<div style="background:#1e1e2e;border:1px solid #2e2e3e;border-radius:6px;'
                    f'padding:8px 14px;margin-bottom:6px">'
                    f'<div style="font-size:0.62rem;font-weight:700;color:#64748b;text-transform:uppercase;'
                    f'letter-spacing:.07em;margin-bottom:3px">{label}</div>'
                    f'<div style="font-size:0.85rem;color:#cbd5e1;line-height:1.5">{val}</div>'
                    f'</div>',
                    unsafe_allow_html=True,
                )

        with hot_col:
            st.markdown('<div class="section-header">Hot Topics Right Now</div>', unsafe_allow_html=True)
            for i, topic in enumerate(result.get("hot_topics", []), 1):
                st.markdown(
                    f'<div style="background:#1e1e2e;border-left:3px solid #f59e0b;'
                    f'border-radius:4px;padding:10px 14px;margin-bottom:8px">'
                    f'<span style="font-size:0.75rem;font-weight:800;color:#f59e0b;'
                    f'margin-right:10px">{i}</span>'
                    f'<span style="font-size:0.88rem;color:#e2e8f0">{topic}</span>'
                    f'</div>',
                    unsafe_allow_html=True,
                )

        st.markdown("<div style='height:16px'></div>", unsafe_allow_html=True)

        # ── Smart Questions ────────────────────────────────────────────────────
        st.markdown('<div class="section-header">Smart Questions to Ask</div>', unsafe_allow_html=True)
        questions = result.get("smart_questions", [])
        for i, q in enumerate(questions, 1):
            st.markdown(
                f'<div style="background:#1e1e2e;border:1px solid #2e2e3e;border-radius:6px;'
                f'padding:10px 16px;margin-bottom:6px;display:flex;align-items:flex-start;gap:10px">'
                f'<span style="font-size:1rem;flex-shrink:0">💡</span>'
                f'<span style="font-size:0.88rem;color:#cbd5e1;line-height:1.5">{i}. {q}</span>'
                f'</div>',
                unsafe_allow_html=True,
            )

        st.markdown("<div style='height:16px'></div>", unsafe_allow_html=True)

        # ── Opportunities + Things to Avoid ────────────────────────────────────
        opp_col, avoid_col = st.columns(2)

        with opp_col:
            st.markdown('<div class="section-header">Consulting Opportunities</div>', unsafe_allow_html=True)
            for item in (result.get("opportunities_to_pitch") or []):
                st.markdown(
                    f'<div style="background:#1a2e1a;border:1px solid #166534;border-radius:6px;'
                    f'padding:10px 14px;margin-bottom:6px;color:#bbf7d0;font-size:0.87rem;line-height:1.5">'
                    f'<span style="color:#34d399;margin-right:8px">→</span>{item}</div>',
                    unsafe_allow_html=True,
                )

        with avoid_col:
            st.markdown('<div class="section-header">Topics to Avoid</div>', unsafe_allow_html=True)
            for item in (result.get("things_to_avoid") or []):
                st.markdown(
                    f'<div style="background:#2e1a1a;border:1px solid #991b1b;border-radius:6px;'
                    f'padding:10px 14px;margin-bottom:6px;color:#fecaca;font-size:0.87rem;line-height:1.5">'
                    f'<span style="color:#f87171;margin-right:8px">✗</span>{item}</div>',
                    unsafe_allow_html=True,
                )

        st.markdown("<div style='height:16px'></div>", unsafe_allow_html=True)

        # ── Latest News ────────────────────────────────────────────────────────
        news_items = result.get("latest_news") or []
        if news_items:
            st.markdown('<div class="section-header">Latest News</div>', unsafe_allow_html=True)
            for item in news_items:
                headline = item.get("headline", "")
                source   = item.get("source", "")
                badge    = (
                    f'<span style="background:#1e2a3a;color:#93c5fd;font-size:0.62rem;'
                    f'font-weight:700;padding:2px 8px;border-radius:999px;margin-right:8px">'
                    f'{source}</span>' if source else ""
                )
                st.markdown(
                    f'<div style="padding:6px 0;border-bottom:1px solid #1e1e2e;'
                    f'font-size:0.85rem;color:#94a3b8">{badge}{headline}</div>',
                    unsafe_allow_html=True,
                )

        st.divider()

        # ── PDF Export ─────────────────────────────────────────────────────────
        today_str = datetime.utcnow().strftime("%Y-%m-%d")
        filename  = f"MeetingPrep_{company_name}_{today_str}.pdf".replace(" ", "_")
        try:
            pdf_bytes = generate_meeting_pdf(result, company_name)
            st.download_button(
                label="📄 Export as PDF Report",
                data=pdf_bytes,
                file_name=filename,
                mime="application/pdf",
            )
        except Exception as e:
            st.error(f"PDF generation failed: {e}")

        st.caption(
            "Brief based on publicly available news and AI reasoning. "
            "For internal preparation only — not investment advice."
        )
