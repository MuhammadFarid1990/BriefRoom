# 📡 BriefRoom

**Real-time business intelligence for consultants, analysts, and founders — powered by Claude AI.**

Bloomberg costs $24,000/year. BriefRoom delivers the same core intelligence loop — live markets, aggregated news, and on-demand AI analysis — for a few dollars in API calls, running entirely on your machine.

---

## Why BriefRoom Exists

Every morning, consultants and analysts open a dozen tabs — market terminals, RSS readers, Google News, competitor sites — and spend an hour assembling context before their first meeting. The tools that automate this (Bloomberg, PitchBook, AlphaSense) cost thousands per seat and still require manual synthesis.

BriefRoom closes that gap. It continuously scrapes financial markets, global news, and tech publications into a local database, then exposes a suite of **AI agents built on Claude** that transform raw headlines into structured, decision-ready intelligence — competitive analyses, geopolitical risk briefs, meeting prep dossiers, and more — all from a single dashboard.

---

## What You Get

### 📈 Markets Pulse + Sector Heatmap
Live price tracking for 11 symbols across equities (SPY, QQQ, AAPL, MSFT, GOOGL, AMZN, NVDA), crypto (BTC, ETH), and commodities (Gold, Oil). Interactive charts with line/bar/candlestick views, 1D/1W/1M time ranges, and a sector heatmap showing real-time performance across all 11 S&P sectors. An AI-powered market summary explains what's driving today's moves in plain English.

### 🧠 AI Daily Briefing
One-click executive briefing that reads the 20 most recent articles and produces a structured analysis: dominant headline, 3–4 thematic breakdowns with named companies and consultant notes, a risk-of-the-day, and an opportunity-of-the-day. Cached per calendar day — Claude is called at most once.

### 🥊 Company vs Company
Enter any two companies. The agent collects local DB articles, live market data, Google News results, and Indeed job posting signals for both, then produces a structured competitive analysis: momentum scores (1–10), financial signals, tech signals, consulting angles, strategic opportunities, M&A signals, regulatory risk, and a consulting recommendation. Results export as a formatted PDF report.

### 🔮 So What? Explainer
Paste any headline or news snippet. Claude returns a structured breakdown: what happened, who wins, who loses, second-order effects, what to watch, and a sharp consultant take. Stress-test your read of any news item in seconds.

### 🌍 Country Risk Brief
Enter any country. The agent pulls Google News context on economy, politics, trade, and investment climate, then generates a scored geopolitical risk brief (political, economic, business environment dimensions), opportunities, threats, a consultant recommendation, and comparable markets.

### 📋 Meeting Prep Brief
Enter a company name and optional meeting context. The agent builds a complete preparation dossier: opening line, executive summary, company snapshot, hot topics, smart questions to ask, consulting opportunities to pitch, topics to avoid, and latest news. Exports as a PDF you can print and bring to the meeting.

---

## Architecture

BriefRoom is split into two independent layers that communicate only through a shared SQLite database:

```
┌─────────────────────────────────────────────────────────────────┐
│  PASSIVE LAYER — Always Running, Zero AI Cost                   │
│                                                                 │
│  RSS Feeds + yfinance  →  scraper.py (30 min)  →  SQLite DB    │
│  (NPR, Guardian, CNBC,    (news, markets, tech)   (articles,   │
│   BBC, HN, TechCrunch)                             market_data)│
└───────────────────────────────┬─────────────────────────────────┘
                                │
                          SQLite (shared)
                                │
┌───────────────────────────────┴─────────────────────────────────┐
│  ACTIVE LAYER — User-Triggered, Claude API                      │
│                                                                 │
│  User action  →  Context from DB + live scrape  →  Claude API   │
│                  →  Structured JSON response  →  Cache + Render │
│                                                                 │
│  Agents: Daily Briefing | CvC | So What? | Country Risk |      │
│          Meeting Prep | Market Summary                          │
└─────────────────────────────────────────────────────────────────┘
```

**Why this matters:**

- The **passive layer** runs 24/7 with zero AI cost — just RSS parsing and Yahoo Finance polling. Data is always fresh regardless of whether anyone is using the dashboard.
- The **active layer** calls Claude only when a user explicitly triggers an agent. Every response is cached with a TTL (daily for briefings, 6h for competitive analysis, 12h for country risk), so the same query never costs twice.
- **Scraper and dashboard are separate processes.** Either can fail and restart independently. The scraper can run headless on a server while the dashboard runs on a laptop.

---

## Tech Stack

| Component | Technology | Why |
|---|---|---|
| Language | Python 3.9+ | End-to-end, scraping through rendering |
| Dashboard | Streamlit | Reactive UI with zero frontend build step |
| Database | SQLite | Zero infrastructure, single-file, perfect for local-first |
| AI Engine | Anthropic Claude (Sonnet) | Structured JSON generation, multi-agent orchestration |
| Market Data | yfinance | Real-time equities, crypto, commodities, sector ETFs |
| News Feeds | feedparser | Lightweight RSS parsing across 7 sources |
| Web Scraping | requests + BeautifulSoup4 | Indeed job signals, fallback content extraction |
| Charts | Plotly | Interactive line/bar/candlestick with dark theme |
| PDF Export | ReportLab | Formatted competitive analysis and meeting prep reports |
| Environment | python-dotenv | Secret management via `.env` |

---

## Quick Start

```bash
# 1. Clone the repo
git clone https://github.com/MuhammadFarid1990/BriefRoom.git
cd BriefRoom

# 2. Install dependencies
pip install -r requirements.txt

# 3. Add your Anthropic API key
cp .env.example .env
# Edit .env → set ANTHROPIC_API_KEY=sk-ant-...

# 4. Initialize the database
python db.py

# 5. (Optional) Seed with 48h of sample data for instant demo
python seed.py

# 6. Terminal 1 — start the scraper
python scraper.py

# 7. Terminal 2 — start the dashboard
streamlit run app.py
```

Open **http://localhost:8501** and you're live.

---

## Project Structure

```
BriefRoom/
├── app.py                    # Streamlit dashboard — all pages, UI, and agent triggers
├── db.py                     # SQLite schema and connection helpers
├── scraper.py                # Orchestrator: runs all scrapers on a 30-min cycle
├── seed.py                   # Populates DB with 48h of realistic sample data
├── requirements.txt          # Python dependencies
├── .env.example              # API key template
│
├── scrapers/
│   ├── markets.py            # yfinance — 11 symbols across 3 asset classes
│   ├── news.py               # RSS — NPR, Guardian, CNBC, BBC Business, BBC World
│   └── tech.py               # RSS — Hacker News, TechCrunch
│
├── agents/
│   ├── company_vs_company.py # Competitive intelligence + PDF export
│   ├── so_what.py            # Headline explainer agent
│   ├── country_risk.py       # Geopolitical risk brief agent
│   └── meeting_prep.py       # Meeting preparation dossier + PDF export
│
└── docs/
    └── BriefRoom_Pitch.pptx  # Project pitch deck
```

---

## How the AI Agents Work

Each agent follows the same pattern:

1. **Check cache.** If a valid cached response exists (within TTL), return it immediately — no API call.
2. **Collect context.** Pull relevant data from the local SQLite database, then augment with live sources (Google News RSS, Indeed job counts, yfinance).
3. **Construct prompt.** Build a structured prompt with all collected context and a strict JSON output schema.
4. **Call Claude.** Send the prompt to Claude Sonnet with explicit instructions to return only valid JSON.
5. **Parse and cache.** Validate the JSON, write it to `agent_cache`, and return the structured result for rendering.

This architecture keeps costs predictable (Claude is called only on user action), responses fast (cache-first), and the codebase maintainable (every agent is a standalone module with the same interface).

---

## Design Decisions

- **SQLite over Postgres.** BriefRoom is designed to run on a single machine. SQLite requires zero infrastructure and handles the read/write patterns here perfectly. Migrating to Postgres is a one-line change in `db.py` if multi-user deployment is ever needed.

- **RSS over web scraping.** RSS is lightweight, structured, and stable — a website redesign doesn't break an RSS parser. Playwright is included in dependencies as a fallback for JS-heavy sources but isn't wired to the default scraping loop to keep setup simple.

- **Streamlit over React.** The entire dashboard — six pages, interactive charts, real-time filters, PDF exports — is a single Python file. No build step, no npm, no webpack. For a self-hosted tool where the user is also the developer, this tradeoff is worth it.

- **Claude only on explicit triggers.** Calling the API on every page load would be expensive and generate analysis nobody reads. Instead, every agent checks its cache first and only hits Claude when a user asks for something specific. Costs are proportional to actual usage.

---

## Limitations

- **Single-user, local-only.** No auth, no multi-tenant isolation. Don't expose this on a public URL without adding authentication.
- **Market data is snapshots.** yfinance is polled every 30 minutes, not streamed. Intraday charts show coarse resolution.
- **Agent context depends on what's been scraped.** A company or country that hasn't appeared in any scraped feed will produce thinner analysis.

---

## Future Roadmap

- **Scheduled email digest** — Morning briefing + top market moves pushed to a distribution list via APScheduler
- **Watchlist alerts** — Slack/email notifications when a tracked company or keyword appears in scraped feeds
- **Persistent agent memory** — Vector store for past analyses so agents can reference how competitive dynamics evolve over time
- **Multi-user deployment** — Auth layer + Postgres migration for team-wide access

---

## Built With

Built by [Muhammad Farid](https://github.com/MuhammadFarid1990) — MS Business Analytics & AI, UT Dallas.

Powered by [Claude](https://www.anthropic.com/claude) from Anthropic.

---

## License

MIT
