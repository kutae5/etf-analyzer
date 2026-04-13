"""
ETF 시장 데이터 수집 모듈
- ETF/주가 데이터 (yfinance)
- 시장 지표 (VIX, 금리, 달러 등)
- 금융 뉴스 (RSS)
"""

import pandas as pd
import yfinance as yf
import feedparser
from typing import Any


def fetch_etf_prices(tickers: list[str], period: str = "1y") -> dict[str, pd.DataFrame]:
    """ETF 가격 데이터를 일괄 수집 (yf.download는 thread-safe하지 않으므로 bulk 사용)."""
    data = {}
    print(f"  {len(tickers)}개 ETF 데이터 수집 중...")

    try:
        raw = yf.download(tickers, period=period, progress=False)

        if raw.empty:
            return data

        if isinstance(raw.columns, pd.MultiIndex):
            # 복수 티커: MultiIndex (Price, Ticker)
            available = raw.columns.get_level_values("Ticker").unique()
            for ticker in tickers:
                if ticker in available:
                    df = raw.xs(ticker, level="Ticker", axis=1).dropna(how="all")
                    if not df.empty:
                        data[ticker] = df
        else:
            # 단일 티커
            data[tickers[0]] = raw
    except Exception as e:
        print(f"  [!] 일괄 다운로드 실패: {e}")
        # Fallback: 순차 다운로드
        for ticker in tickers:
            try:
                df = yf.download(ticker, period=period, progress=False)
                if isinstance(df.columns, pd.MultiIndex):
                    df = df.droplevel("Ticker", axis=1)
                if not df.empty:
                    data[ticker] = df
            except Exception:
                continue

    print(f"  {len(data)}/{len(tickers)}개 수집 완료")
    return data


def fetch_market_indicators(indicators: dict[str, str]) -> dict[str, dict[str, Any]]:
    """시장 지표 데이터 수집 (VIX, 금리, 달러 등)."""
    results = {}

    # 일괄 다운로드
    tickers = list(indicators.keys())
    try:
        raw = yf.download(tickers, period="1mo", progress=False)
    except Exception:
        return results

    if raw.empty:
        return results

    for ticker_symbol, name in indicators.items():
        try:
            if isinstance(raw.columns, pd.MultiIndex):
                available = raw.columns.get_level_values("Ticker").unique()
                if ticker_symbol not in available:
                    continue
                df = raw.xs(ticker_symbol, level="Ticker", axis=1).dropna(how="all")
            else:
                df = raw

            if df.empty or len(df) < 2:
                continue

            current = float(df["Close"].iloc[-1])
            prev = float(df["Close"].iloc[-2])
            change_pct = ((current - prev) / prev) * 100

            prev_5d = float(df["Close"].iloc[-6]) if len(df) >= 6 else prev
            change_5d = ((current - prev_5d) / prev_5d) * 100

            results[name] = {
                "ticker": ticker_symbol,
                "value": round(current, 2),
                "change_1d_pct": round(change_pct, 2),
                "change_5d_pct": round(change_5d, 2),
            }
        except Exception:
            continue

    return results


def fetch_news(count: int = 20) -> list[dict[str, str]]:
    """금융 뉴스 RSS 수집."""
    rss_urls = [
        "https://news.google.com/rss/search?q=ETF+stock+market+economy&hl=en-US&gl=US&ceid=US:en",
        "https://news.google.com/rss/search?q=S%26P500+Nasdaq+treasury+bond&hl=en-US&gl=US&ceid=US:en",
    ]

    articles: list[dict[str, str]] = []
    seen_titles: set[str] = set()

    for url in rss_urls:
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries:
                title = entry.get("title", "").strip()
                if title and title not in seen_titles:
                    seen_titles.add(title)
                    articles.append({
                        "title": title,
                        "source": entry.get("source", {}).get("title", "Unknown"),
                        "published": entry.get("published", ""),
                        "link": entry.get("link", ""),
                    })
        except Exception:
            continue

    articles = articles[:count]
    print(f"  {len(articles)}개 뉴스 수집 완료")
    return articles
