# BriefRoom

> A business intelligence dashboard that gives consultants live market data, aggregated news, and on-demand Claude-powered analysis — all in one place.

Built with Claude (Anthropic) + Streamlit + Python. Runs locally or on any server.

---

## What it does

Consultants spend hours pulling data from different sources before every client meeting. BriefRoom pulls it all into one screen and lets Claude analyze it on demand.

**Six AI-powered modules:**

| Module | What it does |
|--------|-------------|
| 📊 **Company vs Company** | Side-by-side competitive intelligence on any two companies |
| 🌍 **Country Risk** | Geopolitical and economic risk assessment for any market |
| 🤝 **Meeting Prep** | Full pre-meeting brief: company background, recent news, talking points |
| 📰 **News Aggregator** | Filtered, summarized news across markets and sectors |
| 📈 **Market Data** | Live prices, indices, and market summaries |
| 💡 **So What?** | Claude synthesizes everything and tells you what it means for your client |

---

## Tech stack

| Layer | Tech |
|-------|------|
| LLM | Claude (Anthropic SDK) |
| Frontend | Streamlit |
| Data | yfinance, NewsAPI, web scrapers |
| Backend | Python, SQLite |

---

## Quickstart

```bash
git clone https://github.com/MuhammadFarid1990/BriefRoom
cd BriefRoom
pip install -r requirements.txt
cp .env.example .env
# Add your ANTHROPIC_API_KEY to .env
streamlit run app.py
```

---

## About the builder

**Muhammad Farid** — MS Business Analytics & AI @ UT Dallas.

[Portfolio](https://muhammadfarid1990.github.io) · [GitHub](https://github.com/MuhammadFarid1990)

Built with [Claude](https://claude.ai).
