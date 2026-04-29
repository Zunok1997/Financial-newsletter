import os
import re
import smtplib
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import calendar
import time

import anthropic
import feedparser
import yfinance as yf


# ── Configuration ──────────────────────────────────────────────────────────────

TICKERS = {
    # US Equities & Volatility
    "S&P 500":      "^GSPC",
    "Nasdaq":       "^IXIC",
    "Dow Jones":    "^DJI",
    "VIX":          "^VIX",
    "VIX 3M":       "^VIX3M",
    # Fixed Income
    "3M T-Bill":    "^IRX",
    "10Y Treasury": "^TNX",
    "30Y Treasury": "^TYX",
    # FX
    "DXY":          "DX-Y.NYB",
    "EUR/USD":      "EURUSD=X",
    "USD/JPY":      "USDJPY=X",
    # Commodities
    "WTI Crude":    "CL=F",
    "Brent Crude":  "BZ=F",
    "Gold":         "GC=F",
    "Silver":       "SI=F",
    "Copper":       "HG=F",
    # Global Markets
    "DAX":          "^GDAXI",
    "FTSE 100":     "^FTSE",
    "CAC 40":       "^FCHI",
    "Nikkei 225":   "^N225",
    "Hang Seng":    "^HSI",
    "S&P IPSA":     "^IPSA",
}

TICKER_GROUPS = [
    ("US Equities & Volatility", ["S&P 500", "Nasdaq", "Dow Jones", "VIX", "VIX 3M"]),
    ("Fixed Income",             ["3M T-Bill", "10Y Treasury", "30Y Treasury"]),
    ("FX",                       ["DXY", "EUR/USD", "USD/JPY"]),
    ("Commodities",              ["WTI Crude", "Brent Crude", "Gold", "Silver", "Copper"]),
    ("Global Markets",           ["DAX", "FTSE 100", "CAC 40", "Nikkei 225", "Hang Seng", "S&P IPSA"]),
]

RSS_FEEDS = [
    "https://feeds.reuters.com/reuters/businessNews",
    "https://finance.yahoo.com/news/rssindex",
    "https://www.cnbc.com/id/100003114/device/rss/rss.html",
    "https://feeds.apnews.com/rss/apf-business",
    "https://feeds.marketwatch.com/marketwatch/topstories/",
    "https://finviz.com/news_feed.ashx",
]

POLITICAL_RSS_FEEDS = [
    "https://feeds.reuters.com/reuters/politicsNews",
    "https://feeds.apnews.com/rss/apf-politics",
    "https://feeds.reuters.com/reuters/businessNews",
    "https://www.cnbc.com/id/100003114/device/rss/rss.html",
    "https://feeds.marketwatch.com/marketwatch/topstories/",
]

KEY_FIGURES = {
    "trump":           ("Trump",       "#dc2626"),
    "powell":          ("Powell",      "#2563eb"),
    "musk":            ("Musk",        "#7c3aed"),
    "bessent":         ("Bessent",     "#16a34a"),
    "yellen":          ("Yellen",      "#0d9488"),
    "federal reserve": ("Fed",         "#2563eb"),
    "fed rate":        ("Fed",         "#2563eb"),
    "tariff":          ("Tariffs",     "#d97706"),
    "executive order": ("Exec Order",  "#6366f1"),
    "white house":     ("White House", "#6b7280"),
}

FIGURE_DIRECT_SOURCES = [
    {
        "name":      "Donald Trump",
        "initials":  "DT",
        "platform":  "Truth Social",
        "color":     "#dc2626",
        "rss":       "https://truthsocial.com/@realDonaldTrump.rss",
        "is_social": True,
    },
    {
        "name":      "Jerome Powell",
        "initials":  "JP",
        "platform":  "Federal Reserve",
        "color":     "#2563eb",
        "rss":       "https://www.federalreserve.gov/feeds/speeches.xml",
        "is_social": False,
    },
]

THEORY_COLORS = {
    "Graham":    "#16a34a",
    "Buffett":   "#2563eb",
    "Lynch":     "#7c3aed",
    "Fama":      "#d97706",
    "Soros":     "#dc2626",
    "Markowitz": "#0d9488",
    "Fisher":    "#db2777",
    "Marks":     "#ea580c",
    "Dalio":     "#6366f1",
}


# ── Data fetching ──────────────────────────────────────────────────────────────

def fetch_market_data() -> dict:
    data = {}
    for name, ticker in TICKERS.items():
        try:
            t = yf.Ticker(ticker)
            hist = t.history(period="5d")
            if len(hist) < 2:
                continue
            curr = hist.iloc[-1]
            prev = hist.iloc[-2]
            change = curr["Close"] - prev["Close"]
            pct = (change / prev["Close"]) * 100
            data[name] = {
                "price":      curr["Close"],
                "open":       curr["Open"],
                "high":       curr["High"],
                "low":        curr["Low"],
                "change":     change,
                "pct_change": pct,
            }
        except Exception as e:
            print(f"  [warn] {name}: {e}")
    return data


def fetch_news(max_items: int = 10) -> list[dict]:
    articles = []
    for url in RSS_FEEDS:
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:6]:
                articles.append({
                    "title":   entry.get("title", ""),
                    "summary": re.sub(r"<[^>]+>", "", entry.get("summary", "")),
                    "link":    entry.get("link", ""),
                })
        except Exception as e:
            print(f"  [warn] RSS {url}: {e}")
    return articles[:max_items]


def _calculate_rsi(prices, period: int = 14):
    if len(prices) < period + 1:
        return None
    delta = prices.diff().dropna()
    gain = delta.clip(lower=0).ewm(com=period - 1, adjust=True).mean()
    loss = (-delta.clip(upper=0)).ewm(com=period - 1, adjust=True).mean()
    rs = gain / loss
    return round(float((100 - 100 / (1 + rs)).iloc[-1]), 1)


def fetch_stock_metrics(tickers: list) -> dict:
    def _get(ticker: str):
        try:
            t = yf.Ticker(ticker)
            info = t.info
            hist = t.history(period="30d")
            pe = info.get("trailingPE") or info.get("forwardPE")
            rsi = _calculate_rsi(hist["Close"]) if len(hist) >= 15 else None
            mc = info.get("marketCap")
            if mc and mc >= 1e12:
                mc_str = f"${mc/1e12:.1f}T"
            elif mc and mc >= 1e9:
                mc_str = f"${mc/1e9:.1f}B"
            elif mc:
                mc_str = f"${mc/1e6:.0f}M"
            else:
                mc_str = None
            return ticker, {
                "pe":         f"{pe:.1f}" if pe else "N/A",
                "rsi":        str(rsi)    if rsi else "N/A",
                "market_cap": mc_str      or "N/A",
            }
        except Exception as e:
            print(f"  [warn] metrics {ticker}: {e}")
            return ticker, {"pe": "N/A", "rsi": "N/A", "market_cap": "N/A"}

    with ThreadPoolExecutor(max_workers=6) as ex:
        futures = [ex.submit(_get, t) for t in tickers]
        return dict(f.result() for f in as_completed(futures))


def fetch_earnings_calendar(days_ahead: int = 7) -> list:
    MAJOR = [
        "AAPL","MSFT","NVDA","AMZN","GOOGL","META","TSLA","JPM","V","MA",
        "UNH","XOM","JNJ","PG","HD","NFLX","AMD","BAC","WFC","GS","C",
        "DIS","BA","CAT","F","INTC","CRM","ADBE","COST","ORCL","SBUX",
    ]
    today  = date.today()
    cutoff = today + timedelta(days=days_ahead)

    def _get(ticker: str):
        try:
            cal = yf.Ticker(ticker).calendar
            if not isinstance(cal, dict):
                return None
            ed = cal.get("Earnings Date")
            if ed is None:
                return None
            if isinstance(ed, (list, tuple)):
                ed = ed[0]
            ed = ed.date() if hasattr(ed, "date") else ed
            if isinstance(ed, date) and today <= ed <= cutoff:
                return {"date": ed.strftime("%b %d"), "ticker": ticker}
        except Exception:
            return None

    with ThreadPoolExecutor(max_workers=8) as ex:
        futures = [ex.submit(_get, t) for t in MAJOR]
        events = [f.result() for f in as_completed(futures) if f.result()]

    return sorted(events, key=lambda x: x["date"])


def fetch_influential_posts(max_items: int = 10) -> list[dict]:
    now = time.time()
    cutoff = 24 * 3600

    def _is_fresh(entry) -> bool:
        pp = entry.get("published_parsed")
        if not pp:
            return True
        return (now - calendar.timegm(pp)) <= cutoff

    def _time_ago(entry) -> str:
        pp = entry.get("published_parsed")
        if not pp:
            return ""
        age = now - calendar.timegm(pp)
        if age < 3600:   return f"{int(age / 60)}m ago"
        if age < 86400:  return f"{int(age / 3600)}h ago"
        return f"{int(age / 86400)}d ago"

    def _detect_figure(title: str, summary: str) -> tuple[str, str]:
        text = (title + " " + summary).lower()
        for kw, (label, color) in KEY_FIGURES.items():
            if kw in text:
                return label, color
        return "Politics", "#6b7280"

    posts = []

    # 1. Direct social/official sources (Trump Truth Social, Powell/Fed)
    for fig in FIGURE_DIRECT_SOURCES:
        try:
            feed = feedparser.parse(fig["rss"])
            for entry in feed.entries[:6]:
                if not _is_fresh(entry):
                    continue
                raw = entry.get("content", [{}])[0].get("value", "") or entry.get("summary", "")
                content = re.sub(r"<[^>]+>", "", raw).strip()[:600]
                posts.append({
                    "name":      fig["name"],
                    "initials":  fig["initials"],
                    "platform":  fig["platform"],
                    "color":     fig["color"],
                    "is_social": fig["is_social"],
                    "title":     entry.get("title", ""),
                    "content":   content,
                    "link":      entry.get("link", ""),
                    "time_ago":  _time_ago(entry),
                })
        except Exception as e:
            print(f"  [warn] direct source {fig['name']}: {e}")

    # 2. News RSS filtered by key figures
    for url in POLITICAL_RSS_FEEDS:
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:12]:
                if not _is_fresh(entry):
                    continue
                title   = entry.get("title", "")
                summary = re.sub(r"<[^>]+>", "", entry.get("summary", ""))
                text    = (title + " " + summary).lower()
                if not any(kw in text for kw in KEY_FIGURES):
                    continue
                label, color = _detect_figure(title, summary)
                initials = "".join(w[0] for w in label.split()[:2]).upper()
                posts.append({
                    "name":      label,
                    "initials":  initials,
                    "platform":  feed.feed.get("title", "News"),
                    "color":     color,
                    "is_social": False,
                    "title":     title,
                    "content":   summary[:400],
                    "link":      entry.get("link", ""),
                    "time_ago":  _time_ago(entry),
                })
        except Exception as e:
            print(f"  [warn] political RSS {url}: {e}")

    seen: set = set()
    unique = []
    for p in posts:
        key = p["title"][:60].lower()
        if key not in seen:
            seen.add(key)
            unique.append(p)

    return unique[:max_items]


# ── AI content generation ──────────────────────────────────────────────────────

def generate_content(market_data: dict, news: list[dict]) -> str:
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    today = date.today().strftime("%A, %B %d, %Y")

    market_lines = "\n".join(
        f"  {name}: {info['price']:,.4f}  ({info['pct_change']:+.2f}%)"
        for name, info in market_data.items()
    )

    news_lines = "\n".join(
        f"  [{i+1}] {a['title']}: {a['summary'][:120]}"
        for i, a in enumerate(news[:8])
    )

    vix = market_data.get("VIX", {}).get("price", 20)
    if vix < 15:
        vix_label = "COMPLACENCY"
    elif vix < 20:
        vix_label = "NEUTRAL"
    elif vix < 30:
        vix_label = "FEAR"
    else:
        vix_label = "EXTREME FEAR"

    prompt = f"""Senior market analyst. Frameworks: Graham, Buffett, Lynch, Fisher, Soros, Marks, Dalio, Markowitz.
Today: {today}. VIX {vix:.2f} ({vix_label}).

MARKET DATA:
{market_lines}

NEWS:
{news_lines}

Output ONLY these sections with their exact delimiters. No intro, no closing.

##CATALYSTS##
5 entries, one per line: RATING|HEADLINE|3-sentence analysis (what happened, market implication, what to watch)
RATING: BULLISH/BEARISH/NEUTRAL

##TRADE_IDEAS##
7 trades (mix LONG/SHORT, ≥1 HEDGE), one per line:
TICKER|COMPANY|DIRECTION|ENTRY|TARGET|STOP|R/R|SIZE($5k)|2-sentence thesis|P/E|RSI|THEORY|RISK
DIRECTION: LONG/SHORT/HEDGE · THEORY: Graham/Buffett/Lynch/Fama/Soros/Markowitz/Fisher/Marks/Dalio · RISK: LOW/MEDIUM/HIGH/VERY HIGH

##ETF_SPOTLIGHTS##
4 ETFs (1 sector, 1 factor, 1 inverse/hedge, 1 commodity or geo), one per line:
TICKER|NAME|THEME|ER|2-sentence thesis|THEORY|ENTRY|TARGET|STOP|DIRECTION

##WATCHLIST##
8 tickers, one per line: TICKER|why watching (1s)|trigger to act (1s)|LONG or SHORT

##HIDDEN_GEMS##
6 stocks <$10B cap, one per line:
TICKER|COMPANY|CAP RANGE|thesis(1s)|catalyst(1s)|THEORY|risk(1s)|ENTRY|TARGET|STOP|DIRECTION

##PULSE_ANALYSIS##
One line per group: GROUP|2-sentence analysis
Groups: US_EQUITIES|FIXED_INCOME|FX|COMMODITIES|GLOBAL_MARKETS

##TODAY_THEME##
3-5 bullets (each starting with •): macro theme, key levels per asset class, 1-2 upcoming events.

##NEWS_ANALYSIS##
One per article: NUM|HEADLINE|3-sentence analysis (what happened, market impact, what to watch)

##DAILY_CONCLUSION##
Beginner-friendly, mentor tone, plain language. Use these exact headers on their own lines:
RESUMEN:
(2-3 sentences on today's market action and macro theme)
LO MÁS IMPORTANTE:
• event — why it matters in 1 sentence
• event — why it matters in 1 sentence
• event — why it matters in 1 sentence
CONCEPTO DEL DÍA:
(2-3 sentences defining one concept from today, with analogy if helpful)
PARA MAÑANA:
(1-2 sentences on what to watch in the next 24-48h)"""

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=10000,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.content[0].text


# ── Content parsing ────────────────────────────────────────────────────────────

def parse_sections(text: str) -> dict:
    result = {
        "catalysts":        [],
        "trade_ideas":      [],
        "etf_spotlights":   [],
        "watchlist":        [],
        "hidden_gems":      [],
        "today_theme":      "",
        "news_analysis":    [],
        "pulse_analysis":   {},
        "daily_conclusion": "",
    }

    section_map = {
        "##CATALYSTS##":        "catalysts",
        "##TRADE_IDEAS##":      "trade_ideas",
        "##ETF_SPOTLIGHTS##":   "etf_spotlights",
        "##WATCHLIST##":        "watchlist",
        "##HIDDEN_GEMS##":      "hidden_gems",
        "##PULSE_ANALYSIS##":   "pulse_analysis",
        "##TODAY_THEME##":      "today_theme",
        "##NEWS_ANALYSIS##":    "news_analysis",
        "##DAILY_CONCLUSION##": "daily_conclusion",
    }

    current = None
    theme_lines = []
    conclusion_lines = []

    for line in text.split("\n"):
        line = line.strip()
        if line in section_map:
            current = section_map[line]
            continue
        if not line or not current:
            continue

        if current == "today_theme":
            theme_lines.append(line)
            continue

        if current == "daily_conclusion":
            conclusion_lines.append(line)
            continue

        if current == "pulse_analysis":
            if "|" in line:
                k, _, v = line.partition("|")
                result["pulse_analysis"][k.strip()] = v.strip()
            continue

        if "|" not in line:
            continue

        parts = [p.strip() for p in line.split("|")]

        if current == "catalysts" and len(parts) >= 3:
            result["catalysts"].append({
                "rating":   parts[0],
                "headline": parts[1],
                "analysis": parts[2],
            })
        elif current == "trade_ideas" and len(parts) >= 13:
            result["trade_ideas"].append({
                "ticker":    parts[0],
                "company":   parts[1],
                "direction": parts[2],
                "entry":     parts[3],
                "target":    parts[4],
                "stop":      parts[5],
                "rr":        parts[6],
                "size":      parts[7],
                "thesis":    parts[8],
                "pe":        parts[9],
                "rsi":       parts[10],
                "theory":    parts[11],
                "risk":      parts[12],
            })
        elif current == "etf_spotlights" and len(parts) >= 6:
            result["etf_spotlights"].append({
                "ticker":  parts[0],
                "name":    parts[1],
                "theme":   parts[2],
                "expense": parts[3],
                "thesis":  parts[4],
                "theory":  parts[5],
                "entry":   parts[6]  if len(parts) > 6  else "—",
                "target":  parts[7]  if len(parts) > 7  else "—",
                "stop":    parts[8]  if len(parts) > 8  else "—",
                "direction": parts[9] if len(parts) > 9 else "LONG",
            })
        elif current == "watchlist" and len(parts) >= 4:
            result["watchlist"].append({
                "ticker":    parts[0],
                "reason":    parts[1],
                "trigger":   parts[2],
                "direction": parts[3],
            })
        elif current == "hidden_gems" and len(parts) >= 7:
            result["hidden_gems"].append({
                "ticker":     parts[0],
                "company":    parts[1],
                "market_cap": parts[2],
                "thesis":     parts[3],
                "catalyst":   parts[4],
                "theory":     parts[5],
                "risk":       parts[6],
                "entry":      parts[7]  if len(parts) > 7  else "—",
                "target":     parts[8]  if len(parts) > 8  else "—",
                "stop":       parts[9]  if len(parts) > 9  else "—",
                "direction":  parts[10] if len(parts) > 10 else "LONG",
            })
        elif current == "news_analysis":
            parts = [p.strip() for p in line.split("|", 3)]
            if len(parts) >= 3:
                result["news_analysis"].append({
                    "headline": parts[1] if len(parts) > 2 else parts[0],
                    "analysis": parts[2] if len(parts) > 2 else parts[1],
                })

    result["today_theme"]      = "\n".join(theme_lines)
    result["daily_conclusion"] = "\n".join(conclusion_lines)
    return result


# ── HTML component helpers ─────────────────────────────────────────────────────

def _arrow(pct: float) -> str:
    return "▲" if pct >= 0 else "▼"

def _color(pct: float) -> str:
    return "#16a34a" if pct >= 0 else "#dc2626"

def _sign(val: float) -> str:
    return "+" if val >= 0 else ""

def _theory_badge(theory: str) -> str:
    color = THEORY_COLORS.get(theory, "#6b7280")
    return (f'<span style="display:inline-block;background:{color};color:white;'
            f'font-size:10px;font-weight:700;padding:2px 8px;border-radius:20px;'
            f'white-space:nowrap;margin:2px;">{theory}</span>')

def _direction_badge(direction: str) -> str:
    styles = {
        "LONG":  ("background:#dcfce7;color:#16a34a;", "LONG"),
        "SHORT": ("background:#fee2e2;color:#dc2626;", "SHORT ▼"),
        "HEDGE": ("background:#fef3c7;color:#d97706;", "HEDGE"),
    }
    style, label = styles.get(direction, ("background:#f3f4f6;color:#374151;", direction))
    return (f'<span style="display:inline-block;{style}font-size:10px;font-weight:700;'
            f'padding:2px 9px;border-radius:20px;white-space:nowrap;">{label}</span>')

def _rating_badge(rating: str) -> str:
    styles = {
        "BULLISH": ("background:#dcfce7;color:#16a34a;", "● BULLISH"),
        "BEARISH": ("background:#fee2e2;color:#dc2626;", "● BEARISH"),
        "NEUTRAL": ("background:#f3f4f6;color:#6b7280;", "● NEUTRAL"),
    }
    style, label = styles.get(rating, ("background:#f3f4f6;color:#6b7280;", rating))
    return (f'<span style="display:inline-block;{style}font-size:10px;font-weight:700;'
            f'padding:2px 9px;border-radius:20px;white-space:nowrap;">{label}</span>')

def _risk_badge(risk: str) -> str:
    styles = {
        "LOW":       "background:#dcfce7;color:#16a34a;",
        "MEDIUM":    "background:#fef3c7;color:#d97706;",
        "HIGH":      "background:#fee2e2;color:#dc2626;",
        "VERY HIGH": "background:#fce7f3;color:#9d174d;",
    }
    style = styles.get(risk, "background:#f3f4f6;color:#374151;")
    return (f'<span style="display:inline-block;{style}font-size:10px;font-weight:700;'
            f'padding:2px 8px;border-radius:20px;white-space:nowrap;">{risk}</span>')

def _finviz_chart(ticker: str) -> str:
    url = f"https://finviz.com/chart.ashx?t={ticker}&ty=c&ta=1&p=d"
    return (f'<div style="margin-top:12px;border-top:1px solid #f1f5f9;padding-top:10px;">'
            f'<p style="margin:0 0 6px;font-size:10px;color:#9ca3af;font-weight:700;'
            f'letter-spacing:1px;text-transform:uppercase;">Chart (Finviz · Daily)</p>'
            f'<img src="{url}" style="width:100%;border-radius:6px;display:block;" '
            f'alt="{ticker} chart" onerror="this.parentElement.style.display=\'none\'">'
            f'</div>')

def _section(number: str, title: str, content: str) -> str:
    return f"""
  <details style="border-bottom:1px solid #f1f5f9;">
    <summary style="padding:16px 28px;cursor:pointer;list-style:none;background:white;">
      <table style="width:100%;border-collapse:collapse;">
        <tr>
          <td style="width:1%;white-space:nowrap;padding-right:12px;">
            <span style="background:#1e3a5f;color:white;font-size:10px;font-weight:700;
                         padding:3px 9px;border-radius:20px;letter-spacing:0.5px;">{number}</span>
          </td>
          <td style="font-weight:700;font-size:14px;color:#111827;letter-spacing:0.3px;">{title}</td>
          <td style="text-align:right;font-size:12px;color:#9ca3af;white-space:nowrap;">click to expand ›</td>
        </tr>
      </table>
    </summary>
    <div style="padding:4px 28px 20px;">
      {content}
    </div>
  </details>"""


# ── Section HTML builders ──────────────────────────────────────────────────────

_PULSE_GROUP_KEYS = {
    "US Equities & Volatility": "US_EQUITIES",
    "Fixed Income":             "FIXED_INCOME",
    "FX":                       "FX",
    "Commodities":              "COMMODITIES",
    "Global Markets":           "GLOBAL_MARKETS",
}

def _build_market_pulse(market_data: dict, pulse_analysis: dict = None) -> str:
    # Derived: yield spread (10Y − 3M T-Bill)
    t10y = market_data.get("10Y Treasury", {}).get("price")
    t3m  = market_data.get("3M T-Bill",    {}).get("price")
    spread_row = ""
    if t10y and t3m:
        spread = t10y - t3m
        sc = "#16a34a" if spread >= 0 else "#dc2626"
        sl = "NORMAL" if spread >= 0 else "INVERTED ⚠"
        spread_row = f"""
        <tr style="background:#fffbeb;">
          <td colspan="2" style="padding:7px 12px;font-size:12px;color:#374151;
                                  border-bottom:1px solid #f8fafc;font-style:italic;">
            ↳ Yield Spread (10Y − 3M)</td>
          <td style="padding:7px 12px;text-align:right;font-weight:700;color:{sc};
                     border-bottom:1px solid #f8fafc;">{spread:+.2f}%</td>
          <td style="padding:7px 12px;text-align:right;border-bottom:1px solid #f8fafc;">
            <span style="background:{sc}22;color:{sc};font-size:10px;font-weight:700;
                         padding:2px 8px;border-radius:10px;">{sl}</span></td>
        </tr>"""

    # Derived: VIX term structure
    vix   = market_data.get("VIX",   {}).get("price")
    vix3m = market_data.get("VIX 3M",{}).get("price")
    vts_row = ""
    if vix and vix3m:
        structure = "CONTANGO" if vix3m > vix else "BACKWARDATION"
        sc2 = "#16a34a" if vix3m > vix else "#dc2626"
        vts_row = f"""
        <tr style="background:#fffbeb;">
          <td colspan="2" style="padding:7px 12px;font-size:12px;color:#374151;
                                  border-bottom:1px solid #f8fafc;font-style:italic;">
            ↳ VIX Term Structure &nbsp;
            <span style="font-size:11px;color:#6b7280;">({vix:.1f} spot / {vix3m:.1f} 3M)</span>
          </td>
          <td colspan="2" style="padding:7px 12px;text-align:right;border-bottom:1px solid #f8fafc;">
            <span style="background:{sc2}22;color:{sc2};font-size:10px;font-weight:700;
                         padding:2px 8px;border-radius:10px;">{structure}</span></td>
        </tr>"""

    rows = ""
    for group_name, group_tickers in TICKER_GROUPS:
        group_data = [(n, market_data[n]) for n in group_tickers if n in market_data]
        if not group_data:
            continue

        rows += f"""
        <tr style="background:#1e3a5f;">
          <td colspan="4" style="padding:6px 12px;font-size:10px;color:#93c5fd;
                                  font-weight:700;letter-spacing:1.5px;
                                  text-transform:uppercase;">{group_name}</td>
        </tr>"""

        for name, info in group_data:
            pct   = info["pct_change"]
            color = _color(pct)
            extra = ""
            if name == "VIX":
                v = info["price"]
                if v < 15:   label, bg, fg = "COMPLACENCY", "#dcfce7", "#16a34a"
                elif v < 20: label, bg, fg = "NEUTRAL",     "#f3f4f6", "#6b7280"
                elif v < 30: label, bg, fg = "FEAR",        "#fef3c7", "#d97706"
                else:        label, bg, fg = "EXTREME FEAR","#fee2e2", "#dc2626"
                extra = (f' <span style="background:{bg};color:{fg};font-size:10px;'
                         f'font-weight:700;padding:1px 7px;border-radius:10px;">{label}</span>')
            rows += f"""
            <tr>
              <td style="padding:9px 12px;font-weight:600;color:#111827;font-size:13px;
                         border-bottom:1px solid #f8fafc;white-space:nowrap;">{name}{extra}</td>
              <td style="padding:9px 12px;text-align:right;font-family:monospace;font-size:13px;
                         color:#111827;border-bottom:1px solid #f8fafc;">{info['price']:,.2f}</td>
              <td style="padding:9px 12px;text-align:right;font-size:13px;color:{color};
                         font-weight:700;border-bottom:1px solid #f8fafc;">
                {_arrow(pct)} {_sign(pct)}{pct:.2f}%
              </td>
              <td style="padding:9px 12px;text-align:right;font-size:12px;color:#9ca3af;
                         border-bottom:1px solid #f8fafc;">
                H {info['high']:,.2f} / L {info['low']:,.2f}
              </td>
            </tr>"""

        if group_name == "US Equities & Volatility":
            rows += vts_row
        elif group_name == "Fixed Income":
            rows += spread_row

        analysis_key  = _PULSE_GROUP_KEYS.get(group_name)
        analysis_text = (pulse_analysis or {}).get(analysis_key, "")
        if analysis_text:
            rows += f"""
        <tr style="background:#eff6ff;">
          <td colspan="4" style="padding:9px 14px 10px;font-size:12px;color:#1e40af;
                                  line-height:1.65;border-bottom:2px solid #dbeafe;
                                  font-style:italic;">{analysis_text}</td>
        </tr>"""

    return f"""
    <table style="width:100%;border-collapse:collapse;margin-top:8px;">
      <thead>
        <tr style="background:#f9fafb;">
          <th style="padding:8px 12px;text-align:left;font-size:10px;color:#9ca3af;
                     font-weight:700;letter-spacing:1.2px;text-transform:uppercase;">Asset</th>
          <th style="padding:8px 12px;text-align:right;font-size:10px;color:#9ca3af;
                     font-weight:700;letter-spacing:1.2px;text-transform:uppercase;">Price</th>
          <th style="padding:8px 12px;text-align:right;font-size:10px;color:#9ca3af;
                     font-weight:700;letter-spacing:1.2px;text-transform:uppercase;">Change</th>
          <th style="padding:8px 12px;text-align:right;font-size:10px;color:#9ca3af;
                     font-weight:700;letter-spacing:1.2px;text-transform:uppercase;">Day Range</th>
        </tr>
      </thead>
      <tbody>{rows}</tbody>
    </table>"""


def _empty(msg: str) -> str:
    return f'<p style="padding:12px;color:#9ca3af;font-size:13px;font-style:italic;">{msg}</p>'


def _build_catalysts(catalysts: list) -> str:
    if not catalysts:
        return _empty("No catalysts generated — the AI may not have returned data in the expected format.")
    html = ""
    for cat in catalysts:
        border = "#16a34a" if cat["rating"] == "BULLISH" else "#dc2626" if cat["rating"] == "BEARISH" else "#d1d5db"
        html += f"""
        <details style="margin-bottom:6px;border-left:3px solid {border};
                        border-radius:6px;background:#f9fafb;overflow:hidden;">
          <summary style="padding:10px 14px;cursor:pointer;list-style:none;">
            <table style="width:100%;border-collapse:collapse;">
              <tr>
                <td style="width:1%;white-space:nowrap;padding-right:10px;">{_rating_badge(cat['rating'])}</td>
                <td style="font-weight:600;font-size:13px;color:#111827;line-height:1.4;">{cat['headline']}</td>
                <td style="width:1%;white-space:nowrap;padding-left:8px;font-size:11px;color:#9ca3af;">expand ›</td>
              </tr>
            </table>
          </summary>
          <div style="padding:0 14px 12px;border-top:1px solid #f1f5f9;">
            <p style="margin:10px 0 0;font-size:13px;color:#374151;line-height:1.65;">{cat['analysis']}</p>
          </div>
        </details>"""
    return html


def _build_trade_ideas(trades: list, real_metrics: dict = None) -> str:
    if not trades:
        return _empty("No trade ideas generated — the AI may not have returned data in the expected format.")
    html = ""
    for t in trades:
        direction = t["direction"].upper()
        border = "#16a34a" if direction == "LONG" else "#dc2626" if direction == "SHORT" else "#d97706"
        short_warning = ""
        if direction == "SHORT":
            short_warning = """
            <p style="margin:10px 0 0;font-size:11px;color:#dc2626;background:#fee2e2;
                       padding:8px 12px;border-radius:6px;line-height:1.5;">
              ⚠️ <strong>SHORT POSITION:</strong> Theoretically unlimited loss potential.
              Consider using put options for defined risk exposure.
            </p>"""

        html += f"""
        <details style="margin-bottom:8px;border:1px solid #e5e7eb;border-left:3px solid {border};
                        border-radius:8px;overflow:hidden;">
          <summary style="padding:12px 14px;background:#f9fafb;cursor:pointer;list-style:none;">
            <table style="width:100%;border-collapse:collapse;">
              <tr>
                <td style="width:1%;white-space:nowrap;padding-right:8px;">
                  <span style="font-family:monospace;font-weight:800;font-size:15px;color:#111827;">{t['ticker']}</span>
                </td>
                <td style="font-size:13px;color:#374151;padding-right:8px;">{t['company']}</td>
                <td style="width:1%;white-space:nowrap;padding-right:6px;">{_direction_badge(direction)}</td>
                <td style="width:1%;white-space:nowrap;padding-right:6px;">{_theory_badge(t['theory'])}</td>
                <td style="width:1%;white-space:nowrap;text-align:right;">{_risk_badge(t['risk'])}</td>
              </tr>
            </table>
          </summary>
          <div style="padding:14px 16px;background:white;border-top:1px solid #f1f5f9;">
            <table style="width:100%;border-collapse:collapse;font-size:12px;
                          color:#374151;margin-bottom:12px;">
              <tr>
                <td style="padding:4px 12px 4px 0;font-weight:700;color:#6b7280;
                           white-space:nowrap;">Entry Zone</td>
                <td style="padding:4px 0;font-weight:600;">{t['entry']}</td>
                <td style="padding:4px 12px;font-weight:700;color:#6b7280;
                           white-space:nowrap;">Target</td>
                <td style="padding:4px 0;color:#16a34a;font-weight:700;">{t['target']}</td>
              </tr>
              <tr>
                <td style="padding:4px 12px 4px 0;font-weight:700;color:#6b7280;
                           white-space:nowrap;">Stop-Loss</td>
                <td style="padding:4px 0;color:#dc2626;font-weight:600;">{t['stop']}</td>
                <td style="padding:4px 12px;font-weight:700;color:#6b7280;
                           white-space:nowrap;">R/R Ratio</td>
                <td style="padding:4px 0;font-weight:700;">{t['rr']}</td>
              </tr>
              <tr>
                <td style="padding:4px 12px 4px 0;font-weight:700;color:#6b7280;
                           white-space:nowrap;">Position Size</td>
                <td style="padding:4px 0;">{t['size']}</td>
                <td style="padding:4px 12px;font-weight:700;color:#6b7280;
                           white-space:nowrap;">P/E · RSI</td>
                <td style="padding:4px 0;">{(real_metrics or {}).get(t['ticker'], {}).get('pe', t['pe'])} · {(real_metrics or {}).get(t['ticker'], {}).get('rsi', t['rsi'])} <span style="font-size:9px;color:#16a34a;font-weight:700;">LIVE</span></td>
              </tr>
            </table>
            <p style="margin:0;font-size:13px;color:#374151;line-height:1.65;
                      border-top:1px solid #f1f5f9;padding-top:12px;">{t['thesis']}</p>
            {short_warning}
            {_finviz_chart(t['ticker'])}
          </div>
        </details>"""
    return html


def _build_etf_spotlights(etfs: list) -> str:
    if not etfs:
        return _empty("No ETF spotlights generated — the AI may not have returned data in the expected format.")
    html = ""
    for e in etfs:
        html += f"""
        <details style="margin-bottom:8px;border:1px solid #e5e7eb;border-radius:8px;overflow:hidden;">
          <summary style="padding:12px 14px;background:#f9fafb;cursor:pointer;list-style:none;">
            <table style="width:100%;border-collapse:collapse;">
              <tr>
                <td style="width:1%;white-space:nowrap;padding-right:8px;">
                  <span style="font-family:monospace;font-weight:800;font-size:15px;color:#111827;">{e['ticker']}</span>
                </td>
                <td style="font-size:13px;color:#374151;padding-right:8px;">{e['name']}</td>
                <td style="width:1%;white-space:nowrap;padding-right:6px;">
                  <span style="background:#eff6ff;color:#2563eb;font-size:10px;font-weight:700;
                               padding:2px 8px;border-radius:20px;white-space:nowrap;">{e['theme']}</span>
                </td>
                <td style="width:1%;white-space:nowrap;padding-right:6px;">{_theory_badge(e['theory'])}</td>
                <td style="width:1%;white-space:nowrap;text-align:right;font-size:12px;color:#9ca3af;">ER {e['expense']}</td>
              </tr>
            </table>
          </summary>
          <div style="padding:14px 16px;background:white;border-top:1px solid #f1f5f9;">
            <p style="margin:0 0 10px;font-size:13px;color:#374151;line-height:1.65;">{e['thesis']}</p>
            <table style="width:100%;border-collapse:collapse;font-size:12px;
                          color:#374151;background:#f9fafb;border-radius:6px;overflow:hidden;">
              <tr>
                <td style="padding:6px 12px;font-weight:700;color:#6b7280;white-space:nowrap;">Entry Zone</td>
                <td style="padding:6px 12px;font-weight:600;">{e['entry']}</td>
                <td style="padding:6px 12px;font-weight:700;color:#6b7280;white-space:nowrap;">Target</td>
                <td style="padding:6px 12px;color:#16a34a;font-weight:700;">{e['target']}</td>
              </tr>
              <tr>
                <td style="padding:6px 12px;font-weight:700;color:#6b7280;white-space:nowrap;">Stop-Loss</td>
                <td style="padding:6px 12px;color:#dc2626;font-weight:600;">{e['stop']}</td>
                <td style="padding:6px 12px;font-weight:700;color:#6b7280;white-space:nowrap;">Direction</td>
                <td style="padding:6px 12px;">{_direction_badge(e['direction'].upper())}</td>
              </tr>
            </table>
          </div>
        </details>"""
    return html


def _build_watchlist(watchlist: list) -> str:
    if not watchlist:
        return _empty("No watchlist generated — the AI may not have returned data in the expected format.")
    rows = ""
    for w in watchlist:
        direction = w["direction"].upper()
        color = "#16a34a" if direction == "LONG" else "#dc2626"
        trigger = re.sub(
            r'\b(act|go|enter|buy|sell)\s+(long|short)\b[\s,;:]*',
            '', w['trigger'], flags=re.IGNORECASE
        ).strip().strip('.,;')
        rows += f"""
        <tr>
          <td style="padding:9px 12px;font-family:monospace;font-weight:800;color:#111827;
                     border-bottom:1px solid #f8fafc;white-space:nowrap;">{w['ticker']}</td>
          <td style="padding:9px 12px;font-size:12px;color:#374151;border-bottom:1px solid #f8fafc;">
            {w['reason']}</td>
          <td style="padding:9px 12px;font-size:12px;color:#374151;border-bottom:1px solid #f8fafc;">
            {trigger}</td>
          <td style="padding:9px 12px;border-bottom:1px solid #f8fafc;white-space:nowrap;">
            <span style="color:{color};font-weight:700;font-size:12px;">{direction}</span>
          </td>
        </tr>"""

    return f"""
    <table style="width:100%;border-collapse:collapse;margin-top:8px;">
      <thead>
        <tr style="background:#f9fafb;">
          <th style="padding:8px 12px;text-align:left;font-size:10px;color:#9ca3af;
                     font-weight:700;letter-spacing:1.2px;text-transform:uppercase;">Ticker</th>
          <th style="padding:8px 12px;text-align:left;font-size:10px;color:#9ca3af;
                     font-weight:700;letter-spacing:1.2px;text-transform:uppercase;">Why Watching</th>
          <th style="padding:8px 12px;text-align:left;font-size:10px;color:#9ca3af;
                     font-weight:700;letter-spacing:1.2px;text-transform:uppercase;">Trigger to Act</th>
          <th style="padding:8px 12px;text-align:left;font-size:10px;color:#9ca3af;
                     font-weight:700;letter-spacing:1.2px;text-transform:uppercase;">Bias</th>
        </tr>
      </thead>
      <tbody>{rows}</tbody>
    </table>"""


def _build_news(news: list[dict], news_analysis: list[dict]) -> str:
    html = ""
    for i, a in enumerate(news[:8]):
        analysis_text = news_analysis[i]["analysis"] if i < len(news_analysis) else a["summary"]
        link_open  = f'<a href="{a["link"]}" style="text-decoration:none;color:inherit;" target="_blank">' if a.get("link") else ""
        link_close = "</a>" if a.get("link") else ""
        html += f"""
        <details style="margin-bottom:6px;border-left:3px solid #2563eb;
                        border-radius:6px;background:#f9fafb;overflow:hidden;">
          <summary style="padding:10px 14px;cursor:pointer;list-style:none;">
            <table style="width:100%;border-collapse:collapse;">
              <tr>
                <td style="width:14px;padding-right:8px;vertical-align:middle;">
                  <span style="color:#2563eb;font-size:18px;line-height:1;">●</span>
                </td>
                <td style="font-weight:600;font-size:13px;color:#111827;line-height:1.4;">
                  {link_open}{a['title']}{link_close}
                </td>
                <td style="width:1%;white-space:nowrap;padding-left:8px;font-size:11px;color:#9ca3af;">expand ›</td>
              </tr>
            </table>
          </summary>
          <div style="padding:0 14px 12px;border-top:1px solid #f1f5f9;">
            <p style="margin:10px 0 0;font-size:13px;color:#374151;line-height:1.7;">{analysis_text}</p>
          </div>
        </details>"""
    if not html:
        return _empty("No news available at this time.")
    return html


def _build_hidden_gems(gems: list, real_metrics: dict = None) -> str:
    if not gems:
        return _empty("No hidden gems generated — the AI may not have returned data in the expected format.")
    html = ""
    for g in gems:
        html += f"""
        <details style="margin-bottom:8px;border:1px solid #e5e7eb;border-radius:8px;overflow:hidden;">
          <summary style="padding:12px 14px;background:#f9fafb;cursor:pointer;list-style:none;">
            <table style="width:100%;border-collapse:collapse;">
              <tr>
                <td style="width:1%;white-space:nowrap;padding-right:8px;">
                  <span style="font-family:monospace;font-weight:800;font-size:15px;color:#111827;">{g['ticker']}</span>
                </td>
                <td style="font-size:13px;color:#374151;padding-right:8px;">{g['company']}</td>
                <td style="width:1%;white-space:nowrap;padding-right:6px;">
                  <span style="background:#f5f3ff;color:#7c3aed;font-size:10px;font-weight:700;
                               padding:2px 8px;border-radius:20px;white-space:nowrap;">
                    {(real_metrics or {}).get(g['ticker'], {}).get('market_cap', g['market_cap'])}
                    <span style="font-size:9px;color:#16a34a;">LIVE</span>
                  </span>
                </td>
                <td style="width:1%;white-space:nowrap;">{_theory_badge(g['theory'])}</td>
              </tr>
            </table>
          </summary>
          <div style="padding:14px 16px;background:white;border-top:1px solid #f1f5f9;">
            <p style="margin:0 0 7px;font-size:13px;color:#374151;line-height:1.65;">
              <strong>Thesis:</strong> {g['thesis']}</p>
            <p style="margin:0 0 10px;font-size:13px;color:#374151;line-height:1.65;">
              <strong>Catalyst:</strong> {g['catalyst']}</p>
            <table style="width:100%;border-collapse:collapse;font-size:12px;
                          color:#374151;margin-bottom:10px;background:#f9fafb;
                          border-radius:6px;overflow:hidden;">
              <tr>
                <td style="padding:6px 12px;font-weight:700;color:#6b7280;white-space:nowrap;">Entry Zone</td>
                <td style="padding:6px 12px;font-weight:600;">{g['entry']}</td>
                <td style="padding:6px 12px;font-weight:700;color:#6b7280;white-space:nowrap;">Target</td>
                <td style="padding:6px 12px;color:#16a34a;font-weight:700;">{g['target']}</td>
              </tr>
              <tr>
                <td style="padding:6px 12px;font-weight:700;color:#6b7280;white-space:nowrap;">Stop-Loss</td>
                <td style="padding:6px 12px;color:#dc2626;font-weight:600;">{g['stop']}</td>
                <td style="padding:6px 12px;font-weight:700;color:#6b7280;white-space:nowrap;">Direction</td>
                <td style="padding:6px 12px;">{_direction_badge(g['direction'].upper())}</td>
              </tr>
            </table>
            <p style="margin:0;font-size:12px;color:#dc2626;background:#fee2e2;
                      padding:7px 10px;border-radius:6px;">
              <strong>Risk:</strong> {g['risk']}</p>
            {_finviz_chart(g['ticker'])}
          </div>
        </details>"""
    return html


def _build_economic_calendar(earnings: list) -> str:
    note = ('<p style="margin:0 0 12px;font-size:11px;color:#6b7280;background:#f9fafb;'
            'padding:8px 12px;border-radius:6px;border-left:3px solid #2563eb;">'
            '📅 <strong>Earnings (next 7 days)</strong> — source: Yahoo Finance (live). '
            'For macro events (CPI, FOMC, NFP) check '
            '<a href="https://www.investing.com/economic-calendar/" style="color:#2563eb;">Investing.com</a>.</p>')

    if not earnings:
        return note + _empty("No major earnings scheduled in the next 7 days.")

    by_date: dict = {}
    for e in earnings:
        by_date.setdefault(e["date"], []).append(e["ticker"])

    rows = ""
    for d, tickers in sorted(by_date.items()):
        chips = "".join(
            f'<span style="display:inline-block;background:#eff6ff;color:#2563eb;'
            f'font-family:monospace;font-size:11px;font-weight:700;'
            f'padding:3px 9px;border-radius:20px;margin:2px;">{t}</span>'
            for t in tickers
        )
        rows += f"""
        <tr>
          <td style="padding:9px 12px;font-weight:700;font-size:12px;color:#374151;
                     border-bottom:1px solid #f8fafc;white-space:nowrap;">{d}</td>
          <td style="padding:9px 12px;border-bottom:1px solid #f8fafc;">{chips}</td>
        </tr>"""

    return note + f"""
    <table style="width:100%;border-collapse:collapse;margin-top:4px;">
      <thead>
        <tr style="background:#f9fafb;">
          <th style="padding:8px 12px;text-align:left;font-size:10px;color:#9ca3af;
                     font-weight:700;letter-spacing:1.2px;text-transform:uppercase;
                     white-space:nowrap;">Date</th>
          <th style="padding:8px 12px;text-align:left;font-size:10px;color:#9ca3af;
                     font-weight:700;letter-spacing:1.2px;text-transform:uppercase;">Companies Reporting</th>
        </tr>
      </thead>
      <tbody>{rows}</tbody>
    </table>"""


def _build_influential_posts(posts: list[dict]) -> str:
    if not posts:
        return _empty("No market-moving statements found in today's news feeds.")
    html = ('<p style="margin:0 0 12px;font-size:11px;color:#6b7280;background:#f9fafb;'
            'padding:8px 12px;border-radius:6px;border-left:3px solid #d97706;">'
            '⚡ <strong>Market-Moving Figures</strong> — filtered from Reuters, AP, CNBC &amp; MarketWatch. '
            'Sorted by relevance. Click headline to read full article.</p>')
    for p in posts:
        link_open  = f'<a href="{p["link"]}" style="text-decoration:none;color:inherit;">' if p.get("link") else ""
        link_close = "</a>" if p.get("link") else ""
        card_border = f"border:1px solid {p['color']}55;" if p.get("is_social") else "border:1px solid #e5e7eb;"
        social_badge = ('<span style="background:#eff6ff;color:#2563eb;font-size:9px;font-weight:700;'
                        'padding:2px 7px;border-radius:20px;">SOCIAL</span>'
                        if p.get("is_social") else "")
        time_label = f' · {p["time_ago"]}' if p.get("time_ago") else ""
        body_text = p.get("content") or p.get("title", "")
        show_title_separately = not p.get("is_social") and p.get("title") and p.get("content") and p["title"] != p["content"]

        html += f"""
        <div style="{card_border}border-radius:12px;padding:14px 16px;margin-bottom:10px;
                    background:white;box-shadow:0 1px 3px rgba(0,0,0,0.06);">
          <table style="width:100%;border-collapse:collapse;margin-bottom:10px;">
            <tr>
              <td style="width:42px;vertical-align:top;">
                <div style="width:40px;height:40px;border-radius:50%;background:{p['color']};
                            display:inline-flex;align-items:center;justify-content:center;
                            font-weight:800;font-size:14px;color:white;">{p['initials']}</div>
              </td>
              <td style="padding-left:10px;vertical-align:top;">
                <div style="font-weight:700;font-size:13px;color:#111827;">{p['name']}</div>
                <div style="font-size:11px;color:#9ca3af;">{p['platform']}{time_label}</div>
              </td>
              <td style="width:1%;white-space:nowrap;vertical-align:top;">{social_badge}</td>
            </tr>
          </table>
          {"<p style='margin:0 0 6px;font-weight:700;font-size:13px;color:#111827;line-height:1.4;'>" + link_open + p['title'] + link_close + "</p>" if show_title_separately else ""}
          <p style="margin:0;font-size:13px;color:#111827;line-height:1.65;">{body_text}</p>
          {"<div style='margin-top:8px;'>" + link_open + "<span style='font-size:11px;color:#2563eb;'>Ver publicación →</span>" + link_close + "</div>" if p.get("link") and not p.get("is_social") else ""}
        </div>"""
    return html


def _build_daily_conclusion(text: str) -> str:
    if not text.strip():
        return _empty("No conclusion generated.")

    HEADERS = {
        "RESUMEN:":           ("#0f1f3d", "📋 Resumen del día"),
        "LO MÁS IMPORTANTE:": ("#15803d", "⭐ Lo más importante"),
        "CONCEPTO DEL DÍA:":  ("#7c3aed", "💡 Concepto del día"),
        "PARA MAÑANA:":       ("#d97706", "👀 Para mañana"),
    }

    COLORS = {
        "RESUMEN:":           ("#eff6ff", "#1e40af"),
        "LO MÁS IMPORTANTE:": ("#f0fdf4", "#15803d"),
        "CONCEPTO DEL DÍA:":  ("#f5f3ff", "#5b21b6"),
        "PARA MAÑANA:":       ("#fffbeb", "#92400e"),
    }

    html = ""
    current_header = None
    current_lines = []

    def _flush(header, lines):
        if not header or not lines:
            return ""
        bg, fg = COLORS.get(header, ("#f9fafb", "#374151"))
        _, label = HEADERS.get(header, ("#374151", header))
        content = ""
        for line in lines:
            if line.startswith("•"):
                item = line.lstrip("• ").strip()
                if "—" in item:
                    left, _, right = item.partition("—")
                    content += (f'<li style="margin-bottom:8px;line-height:1.65;">'
                                f'<strong>{left.strip()}</strong> — {right.strip()}</li>')
                else:
                    content += f'<li style="margin-bottom:8px;line-height:1.65;">{item}</li>'
            else:
                content += f'<p style="margin:0 0 6px;line-height:1.7;">{line}</p>'
        if "<li" in content:
            content = f'<ul style="margin:6px 0 0;padding-left:20px;">{content}</ul>'
        return f"""
        <div style="background:{bg};border-radius:8px;padding:14px 16px;margin-bottom:10px;">
          <div style="font-size:11px;font-weight:800;letter-spacing:1px;text-transform:uppercase;
                      color:{fg};margin-bottom:8px;">{label}</div>
          <div style="font-size:13px;color:#374151;">{content}</div>
        </div>"""

    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue
        if line in HEADERS:
            html += _flush(current_header, current_lines)
            current_header = line
            current_lines = []
        else:
            current_lines.append(line)
    html += _flush(current_header, current_lines)

    return html or _empty("No conclusion generated.")


# ── Full HTML email ────────────────────────────────────────────────────────────

def build_html(market_data: dict, sections: dict, news: list[dict],
               real_metrics: dict = None, earnings: list = None,
               influential_posts: list = None) -> str:
    today = date.today().strftime("%A, %B %d, %Y")

    legend = "".join(_theory_badge(t) for t in THEORY_COLORS)

    theme_items = "".join(
        f'<li style="margin-bottom:9px;line-height:1.65;">{line.lstrip("•- ").strip()}</li>'
        for line in sections["today_theme"].split("\n")
        if line.strip()
    )
    theme_html = f'<ul style="margin:0;padding-left:18px;">{theme_items}</ul>'

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Morning Financial Briefing — {today}</title>
<style>
  body{{margin:0;padding:0;background:#f3f4f6;
       font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;}}
  .wrap{{max-width:700px;margin:24px auto;background:#fff;border-radius:12px;
         overflow:hidden;box-shadow:0 4px 24px rgba(0,0,0,0.09);}}
  details>summary{{list-style:none;}}
  details>summary::-webkit-details-marker{{display:none;}}
</style>
</head>
<body>
<div class="wrap">

  <!-- HEADER -->
  <div style="background:linear-gradient(135deg,#0f1f3d 0%,#1e3a5f 50%,#2563eb 100%);
              padding:28px 32px 24px;">
    <div style="font-size:10px;font-weight:700;letter-spacing:2.5px;text-transform:uppercase;
                color:#93c5fd;margin-bottom:8px;">Morning Financial Briefing</div>
    <h1 style="margin:0;color:white;font-size:24px;font-weight:800;letter-spacing:-0.5px;">
      {today}
    </h1>
    <p style="margin:8px 0 0;color:#bfdbfe;font-size:12px;line-height:1.5;">
      US · Europe · Asia · Fixed Income · FX · Commodities · Yield Curve · VIX Structure
    </p>
  </div>

  <!-- TODAY'S THEME -->
  <div style="padding:20px 28px;border-bottom:1px solid #f1f5f9;">
    <div style="font-size:10px;font-weight:700;letter-spacing:1.8px;text-transform:uppercase;
                color:#9ca3af;margin-bottom:12px;">Today's Macro Theme</div>
    <div style="background:#f0fdf4;border:1px solid #86efac;border-radius:8px;
                padding:16px 18px;color:#166534;font-size:14px;line-height:1.75;">
      {theme_html}
    </div>
  </div>

  <!-- THEORY LEGEND -->
  <div style="padding:12px 28px;background:#f9fafb;border-bottom:1px solid #f1f5f9;">
    <span style="font-size:10px;color:#9ca3af;font-weight:700;letter-spacing:1px;
                 text-transform:uppercase;margin-right:10px;">Frameworks</span>
    {legend}
  </div>

  {_section("01", "Market Pulse", _build_market_pulse(market_data, sections.get("pulse_analysis", {})))}
  {_section("02", "Earnings Calendar — Next 7 Days", _build_economic_calendar(earnings or []))}
  {_section("03", "Key Catalysts Today", _build_catalysts(sections["catalysts"]))}
  {_section("04", "Trade Ideas", _build_trade_ideas(sections["trade_ideas"], real_metrics))}
  {_section("05", "ETF Spotlights", _build_etf_spotlights(sections["etf_spotlights"]))}
  {_section("06", "Global Financial Newsletter", _build_news(news, sections.get("news_analysis", [])))}
  {_section("07", "Watchlist — Next 48h", _build_watchlist(sections["watchlist"]))}
  {_section("08", "Hidden Gems — Small &amp; Mid Cap", _build_hidden_gems(sections["hidden_gems"], real_metrics))}
  {_section("09", "Market-Moving Figures — Trump · Powell · Musk &amp; More", _build_influential_posts(influential_posts or []))}
  {_section("10", "Conclusión del Día — Para Principiantes", _build_daily_conclusion(sections.get("daily_conclusion", "")))}

  <!-- DISCLAIMER -->
  <div style="padding:16px 28px;background:#f9fafb;border-top:1px solid #e5e7eb;">
    <p style="margin:0;font-size:11px;color:#9ca3af;line-height:1.7;">
      <strong>Disclaimer:</strong> This briefing is for informational and educational purposes only.
      It does not constitute financial advice. Past performance does not guarantee future results.
      All prices are approximate and may be delayed. Financial metrics (P/E, RSI, etc.) are AI estimates —
      always verify before trading. Short selling carries theoretically unlimited loss potential.
      Consult a licensed financial advisor before investing.
    </p>
    <p style="margin:10px 0 0;text-align:center;font-size:11px;color:#9ca3af;">
      Automated Financial Newsletter · {today} · Claude Sonnet 4.6 · Data via Yahoo Finance &amp; Reuters/AP RSS
    </p>
  </div>

</div>
</body>
</html>"""


# ── Email sending ──────────────────────────────────────────────────────────────

def send_email(pages_url: str, subject: str) -> None:
    sender     = os.environ["GMAIL_USER"]
    recipients = [r.strip() for r in os.environ["RECIPIENT_EMAIL"].split(",")]
    password   = os.environ["GMAIL_APP_PASSWORD"]

    body_text = (
        "Buenos dias,\n\n"
        f"Tu briefing financiero de hoy esta disponible en:\n{pages_url}\n\n"
        "Saludos,\n"
        "Martin."
    )
    body_html = f"""<html><body style="font-family:sans-serif;font-size:14px;color:#111;">
<p>Buenos dias,</p>
<p>Tu briefing financiero de hoy esta disponible en:</p>
<p><a href="{pages_url}" style="background:#2563eb;color:white;padding:10px 20px;
   border-radius:6px;text-decoration:none;font-weight:700;display:inline-block;">
   Ver Briefing de Hoy &rarr;</a></p>
<p>Saludos,<br>Martin.</p>
</body></html>"""

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = f"Financial Briefing <{sender}>"
    msg["To"]      = ", ".join(recipients)
    msg.attach(MIMEText(body_text, "plain", "utf-8"))
    msg.attach(MIMEText(body_html, "html",  "utf-8"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as srv:
        srv.login(sender, password)
        srv.sendmail(sender, recipients, msg.as_string())

    print(f"Sent to {', '.join(recipients)}")


# ── Entry point ────────────────────────────────────────────────────────────────

def main() -> None:
    print("Fetching market data...")
    market_data = fetch_market_data()
    print(f"  Got: {', '.join(market_data.keys())}")

    print("Fetching news...")
    news = fetch_news()
    print(f"  Got {len(news)} articles")

    print("Generating briefing with Claude Sonnet 4.6...")
    raw_text = generate_content(market_data, news)

    print("Parsing sections...")
    sections = parse_sections(raw_text)
    for k, v in sections.items():
        count = len(v) if isinstance(v, list) else ("ok" if v else "empty")
        print(f"  {k}: {count}")

    all_tickers = list(set(
        [t["ticker"] for t in sections["trade_ideas"]] +
        [g["ticker"] for g in sections["hidden_gems"]]
    ))
    print(f"Fetching live metrics for: {', '.join(all_tickers)}...")
    real_metrics = fetch_stock_metrics(all_tickers) if all_tickers else {}

    print("Fetching earnings calendar...")
    earnings = fetch_earnings_calendar()
    print(f"  Got {len(earnings)} upcoming earnings events")

    print("Fetching market-moving figures' posts...")
    influential_posts = fetch_influential_posts()
    print(f"  Got {len(influential_posts)} relevant posts")

    print("Building HTML...")
    html = build_html(market_data, sections, news, real_metrics, earnings, influential_posts)

    print("Saving to docs/index.html...")
    os.makedirs("docs", exist_ok=True)
    with open("docs/index.html", "w", encoding="utf-8") as f:
        f.write(html)

    today_str = date.today().strftime("%A, %B %d, %Y")
    subject   = f"Morning Financial Briefing — {today_str}"
    pages_url = os.environ.get("PAGES_URL", "")

    print("Sending email...")
    send_email(pages_url, subject)
    print("Done!")


if __name__ == "__main__":
    main()
