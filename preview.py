"""
Run this script to preview the newsletter in your browser without sending an email.

Usage:
  python preview.py            → full run (calls API, saves cache)
  python preview.py --cached   → uses saved cache (no API call, free)
"""
import json
import os
import sys
import webbrowser
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

USE_CACHE = "--cached" in sys.argv
CACHE_FILE = Path(__file__).parent / "_preview_cache.json"

if not USE_CACHE and not os.getenv("ANTHROPIC_API_KEY"):
    raise SystemExit("ERROR: ANTHROPIC_API_KEY not set. Create a .env file (see .env.example).")

from newsletter import (fetch_market_data, fetch_news, fetch_stock_metrics,
                        fetch_earnings_calendar, fetch_influential_posts,
                        generate_content, parse_sections, build_html)

if USE_CACHE:
    if not CACHE_FILE.exists():
        raise SystemExit("ERROR: No cache found. Run 'python preview.py' once first to generate it.")
    print("Loading from cache (no API call)...")
    cache = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    data              = cache["market_data"]
    news              = cache["news"]
    ai_output         = cache["ai_output"]
    real_metrics      = cache["real_metrics"]
    earnings          = cache["earnings"]
    influential_posts = cache.get("influential_posts", [])
else:
    print("Fetching market data...")
    data = fetch_market_data()

    print("Fetching news...")
    news = fetch_news()

    print("Generating AI analysis (this takes ~15 seconds)...")
    ai_output = generate_content(data, news)

    print("Parsing sections for metric tickers...")
    _sections_tmp = parse_sections(ai_output)
    all_tickers = list(set(
        [t["ticker"] for t in _sections_tmp["trade_ideas"]] +
        [g["ticker"] for g in _sections_tmp["hidden_gems"]]
    ))
    print(f"Fetching live metrics for: {', '.join(all_tickers)}...")
    real_metrics = fetch_stock_metrics(all_tickers) if all_tickers else {}

    print("Fetching earnings calendar...")
    earnings = fetch_earnings_calendar()
    print(f"  Got {len(earnings)} upcoming earnings events")

    print("Fetching market-moving figures' posts...")
    influential_posts = fetch_influential_posts()
    print(f"  Got {len(influential_posts)} relevant posts")

    CACHE_FILE.write_text(json.dumps({
        "market_data":      data,
        "news":             news,
        "ai_output":        ai_output,
        "real_metrics":     real_metrics,
        "earnings":         earnings,
        "influential_posts": influential_posts,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Cache saved → {CACHE_FILE.name}")

print("Parsing sections...")
sections = parse_sections(ai_output)

print("Building HTML...")
html = build_html(data, sections, news, real_metrics, earnings, influential_posts)

output_path = Path(__file__).parent / "preview.html"
output_path.write_text(html, encoding="utf-8")

print(f"Saved to {output_path}")
print("Opening in browser...")
webbrowser.open(output_path.as_uri())
