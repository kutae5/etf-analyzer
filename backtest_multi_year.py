#!/usr/bin/env python3
"""
Multi-Year ETF Analysis Backtest
================================
각 연도(2022/2023/2024) 4월 11일 기준 분석을 수행하고,
공통 검증일 2025-04-11 실제 가격과 비교하여 분석 모델의 정확도를 측정합니다.

각 분석 시점별 forward horizon:
    2022-04-11 -> 2025-04-11  (~3Y forward)
    2023-04-11 -> 2025-04-11  (~2Y forward)
    2024-04-11 -> 2025-04-11  (~1Y forward)

검증 항목:
1. Trend 방향 정확도 (Golden Cross/Death Cross → 실제 상승/하락?)
2. RSI 과매수/과매도 시그널 정확도
3. MACD Bullish/Bearish 정확도
4. 섹터 순위 예측 (analyzer가 만든 순위 vs 실제 수익률 순위)
5. Growth vs Value 판단 정확도
"""

import sys
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import yaml
import yfinance as yf

from src.analyzer import analyze_etf


ANALYSIS_DATES = [
    datetime(2022, 4, 11),
    datetime(2023, 4, 11),
    datetime(2024, 4, 11),
]
VERIFY_DATE = datetime(2025, 4, 11)


def load_config(path="config.yaml"):
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def get_all_tickers(config):
    ticker_names = {}
    for group in config.get("etfs", {}).values():
        if isinstance(group, dict):
            ticker_names.update(group)
    return ticker_names


def fetch_historical_data(tickers, end_date, lookback_days=365):
    """end_date 시점까지의 과거 1년치 데이터."""
    start = end_date - timedelta(days=lookback_days + 30)
    data = {}
    try:
        raw = yf.download(
            tickers,
            start=start.strftime("%Y-%m-%d"),
            end=end_date.strftime("%Y-%m-%d"),
            progress=False,
            auto_adjust=True,
        )
        if raw.empty:
            return data
        if isinstance(raw.columns, pd.MultiIndex):
            available = raw.columns.get_level_values("Ticker").unique()
            for t in tickers:
                if t in available:
                    df = raw.xs(t, level="Ticker", axis=1).dropna(how="all")
                    if not df.empty:
                        data[t] = df
        else:
            data[tickers[0]] = raw
    except Exception as e:
        print(f"  [!] Download error: {e}")
    return data


def fetch_spot_price(tickers, target_date):
    """target_date 근처 종가 수집 (앞뒤 5일 window)."""
    start = target_date - timedelta(days=5)
    end = target_date + timedelta(days=5)
    prices = {}
    try:
        raw = yf.download(
            tickers,
            start=start.strftime("%Y-%m-%d"),
            end=end.strftime("%Y-%m-%d"),
            progress=False,
            auto_adjust=True,
        )
        if raw.empty:
            return prices
        if isinstance(raw.columns, pd.MultiIndex):
            available = raw.columns.get_level_values("Ticker").unique()
            for t in tickers:
                if t in available:
                    df = raw.xs(t, level="Ticker", axis=1).dropna(how="all")
                    if not df.empty and "Close" in df.columns:
                        prices[t] = float(df["Close"].iloc[-1])
        else:
            if "Close" in raw.columns:
                prices[tickers[0]] = float(raw["Close"].iloc[-1])
    except Exception as e:
        print(f"  [!] Spot price error: {e}")
    return prices


def compute_accuracy(df: pd.DataFrame) -> dict:
    """단일 시점의 정확도 지표 계산."""
    bullish_trends = ["Strong Uptrend (Golden Cross)", "Uptrend"]
    bearish_trends = ["Strong Downtrend (Death Cross)", "Downtrend"]

    def trend_ok(row):
        if row["trend"] in bullish_trends and row["fwd_return"] > 0:
            return True
        if row["trend"] in bearish_trends and row["fwd_return"] <= 0:
            return True
        return False

    df = df.copy()
    df["trend_correct"] = df.apply(trend_ok, axis=1)

    market_med = df["fwd_return"].median()

    overbought = df[df["rsi"].notna() & (df["rsi"] > 70)]
    oversold = df[df["rsi"].notna() & (df["rsi"] < 30)]
    ob_acc = (overbought["fwd_return"] < market_med).mean() * 100 if len(overbought) else None
    os_acc = (oversold["fwd_return"] > 0).mean() * 100 if len(oversold) else None

    macd_bull = df[df["macd"] == "Bullish"]
    macd_bear = df[df["macd"] == "Bearish"]
    macd_bull_acc = (macd_bull["fwd_return"] > 0).mean() * 100 if len(macd_bull) else None
    macd_bear_acc = (macd_bear["fwd_return"] <= 0).mean() * 100 if len(macd_bear) else None
    macd_total = len(macd_bull) + len(macd_bear)
    macd_overall = None
    if macd_total:
        correct = (macd_bull["fwd_return"] > 0).sum() + (macd_bear["fwd_return"] <= 0).sum()
        macd_overall = correct / macd_total * 100

    return {
        "n": len(df),
        "trend_acc": df["trend_correct"].mean() * 100,
        "median_return": market_med,
        "mean_return": df["fwd_return"].mean(),
        "positive_ratio": (df["fwd_return"] > 0).mean() * 100,
        "overbought_n": len(overbought),
        "overbought_acc": ob_acc,
        "oversold_n": len(oversold),
        "oversold_acc": os_acc,
        "macd_bull_n": len(macd_bull),
        "macd_bull_acc": macd_bull_acc,
        "macd_bear_n": len(macd_bear),
        "macd_bear_acc": macd_bear_acc,
        "macd_overall": macd_overall,
    }


def run_one_backtest(analysis_date: datetime, verify_date: datetime, tickers, ticker_names, sector_tickers):
    horizon_days = (verify_date - analysis_date).days
    horizon_label = f"{horizon_days / 365:.1f}Y"
    print(f"\n{'─' * 60}")
    print(f"  Analysis: {analysis_date:%Y-%m-%d}  →  Verify: {verify_date:%Y-%m-%d}  ({horizon_label})")
    print(f"{'─' * 60}")

    # 1. Historical analysis
    hist = fetch_historical_data(tickers, analysis_date)
    analyses = {}
    for t in tickers:
        if t in hist:
            try:
                analyses[t] = analyze_etf(t, hist[t])
            except Exception:
                pass
    print(f"  [1] {len(analyses)} ETFs analyzed")

    # 2. Future prices
    future = fetch_spot_price(tickers, verify_date)
    print(f"  [2] {len(future)} future prices loaded")

    # 3. Merge
    rows = []
    for t, a in analyses.items():
        if t not in future:
            continue
        p0 = a["price"]
        p1 = future[t]
        fwd = ((p1 - p0) / p0) * 100 if p0 else None
        if fwd is None:
            continue
        rows.append({
            "ticker": t,
            "name": ticker_names.get(t, t),
            "price_start": round(p0, 2),
            "price_end": round(p1, 2),
            "fwd_return": round(fwd, 2),
            "trend": a["trend"],
            "rsi": a["rsi"],
            "macd": a["macd_crossover"],
            "bollinger": a.get("bollinger_position"),
            "volatility": a.get("volatility_30d"),
        })
    df = pd.DataFrame(rows)
    if df.empty:
        print("  [!] No matched data")
        return None

    metrics = compute_accuracy(df)
    print(
        f"  [3] Trend acc={metrics['trend_acc']:.1f}%  "
        f"MACD acc={metrics['macd_overall']:.1f}%  "
        f"Median return={metrics['median_return']:+.1f}%"
    )

    # Growth vs Value
    growth_tickers = ["VUG", "IWF", "SCHG", "MGK"]
    value_tickers = ["VTV", "IWD", "SCHV"]
    growth_ret = df[df["ticker"].isin(growth_tickers)]["fwd_return"].mean()
    value_ret = df[df["ticker"].isin(value_tickers)]["fwd_return"].mean()

    # Sector ranking quality: analyzer가 만든 trend 순위 vs 실제 수익률 순위
    sector_df = df[df["ticker"].isin(sector_tickers)].copy()

    return {
        "analysis_date": analysis_date,
        "verify_date": verify_date,
        "horizon": horizon_label,
        "df": df,
        "metrics": metrics,
        "growth_ret": growth_ret,
        "value_ret": value_ret,
        "sector_df": sector_df,
    }


def format_accuracy_table(results: list) -> str:
    lines = [
        "| 분석 시점 | Horizon | ETFs | Trend 정확도 | MACD 정확도 | RSI>70 Underperf | RSI<30 Bounce | 중앙 수익률 | 양수 비율 |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in results:
        m = r["metrics"]
        ob = f"{m['overbought_acc']:.1f}% ({m['overbought_n']})" if m["overbought_acc"] is not None else f"N/A ({m['overbought_n']})"
        os_ = f"{m['oversold_acc']:.1f}% ({m['oversold_n']})" if m["oversold_acc"] is not None else f"N/A ({m['oversold_n']})"
        lines.append(
            f"| {r['analysis_date']:%Y-%m-%d} | {r['horizon']} | {m['n']} | "
            f"**{m['trend_acc']:.1f}%** | {m['macd_overall']:.1f}% | {ob} | {os_} | "
            f"{m['median_return']:+.1f}% | {m['positive_ratio']:.1f}% |"
        )
    return "\n".join(lines)


def format_trend_breakdown(results: list) -> str:
    lines = []
    for r in results:
        df = r["df"].copy()
        bullish = ["Strong Uptrend (Golden Cross)", "Uptrend"]
        bearish = ["Strong Downtrend (Death Cross)", "Downtrend"]

        def tag(row):
            if row["trend"] in bullish:
                return "Bullish"
            if row["trend"] in bearish:
                return "Bearish"
            return "Neutral"

        df["tag"] = df.apply(tag, axis=1)
        grp = df.groupby("tag").agg(
            count=("ticker", "count"),
            avg_return=("fwd_return", "mean"),
        ).round(2)

        lines.append(f"\n**{r['analysis_date']:%Y-%m-%d} ({r['horizon']} forward)**\n")
        lines.append("| Trend 분류 | 종목수 | 평균 수익률 |")
        lines.append("|---|---|---|")
        for tag_, row in grp.iterrows():
            lines.append(f"| {tag_} | {int(row['count'])} | {row['avg_return']:+.2f}% |")
    return "\n".join(lines)


def format_growth_value(results: list) -> str:
    lines = [
        "| 분석 시점 | Horizon | Growth 평균 | Value 평균 | Winner | Gap |",
        "|---|---|---|---|---|---|",
    ]
    for r in results:
        winner = "Growth" if r["growth_ret"] > r["value_ret"] else "Value"
        gap = abs(r["growth_ret"] - r["value_ret"])
        lines.append(
            f"| {r['analysis_date']:%Y-%m-%d} | {r['horizon']} | "
            f"{r['growth_ret']:+.2f}% | {r['value_ret']:+.2f}% | "
            f"**{winner}** | {gap:.2f}pp |"
        )
    return "\n".join(lines)


def format_sector_performance(results: list) -> str:
    lines = []
    for r in results:
        sdf = r["sector_df"].sort_values("fwd_return", ascending=False)
        if sdf.empty:
            continue
        lines.append(f"\n**{r['analysis_date']:%Y-%m-%d} → {r['verify_date']:%Y-%m-%d} ({r['horizon']})**\n")
        lines.append("| Rank | Sector | Ticker | Start | End | Return | 시작 Trend | 시작 RSI |")
        lines.append("|---|---|---|---|---|---|---|---|")
        for i, (_, row) in enumerate(sdf.iterrows(), 1):
            rsi = f"{row['rsi']:.1f}" if pd.notna(row["rsi"]) else "N/A"
            lines.append(
                f"| {i} | {row['name']} | {row['ticker']} | ${row['price_start']} | "
                f"${row['price_end']} | **{row['fwd_return']:+.2f}%** | {row['trend']} | {rsi} |"
            )
    return "\n".join(lines)


def format_top_bottom(results: list, n: int = 5) -> str:
    lines = []
    for r in results:
        df_sorted = r["df"].sort_values("fwd_return", ascending=False)
        lines.append(f"\n**{r['analysis_date']:%Y-%m-%d} ({r['horizon']} forward)**\n")
        lines.append(f"Top {n}:\n")
        lines.append("| ETF | Return | Trend | RSI |")
        lines.append("|---|---|---|---|")
        for _, row in df_sorted.head(n).iterrows():
            rsi = f"{row['rsi']:.1f}" if pd.notna(row["rsi"]) else "N/A"
            lines.append(f"| {row['name']} ({row['ticker']}) | **{row['fwd_return']:+.2f}%** | {row['trend']} | {rsi} |")
        lines.append(f"\nBottom {n}:\n")
        lines.append("| ETF | Return | Trend | RSI |")
        lines.append("|---|---|---|---|")
        for _, row in df_sorted.tail(n).iloc[::-1].iterrows():
            rsi = f"{row['rsi']:.1f}" if pd.notna(row["rsi"]) else "N/A"
            lines.append(f"| {row['name']} ({row['ticker']}) | **{row['fwd_return']:+.2f}%** | {row['trend']} | {rsi} |")
    return "\n".join(lines)


def main():
    print("=" * 60)
    print("  Multi-Year ETF Analysis Backtest")
    print(f"  Verify Date: {VERIFY_DATE:%Y-%m-%d}")
    print(f"  Analysis Dates: {', '.join(d.strftime('%Y-%m-%d') for d in ANALYSIS_DATES)}")
    print("=" * 60)

    config = load_config()
    ticker_names = get_all_tickers(config)
    tickers = list(ticker_names.keys())
    sector_tickers = list(config.get("etfs", {}).get("sectors", {}).keys())
    print(f"  Tickers: {len(tickers)} | Sectors: {len(sector_tickers)}")

    results = []
    for d in ANALYSIS_DATES:
        r = run_one_backtest(d, VERIFY_DATE, tickers, ticker_names, sector_tickers)
        if r:
            results.append(r)

    if not results:
        print("\n[ERROR] No backtest results produced")
        return

    # ── Report ──
    lines = []
    lines.append("# Multi-Year ETF Analysis Backtest Report")
    lines.append(f"\n**Verify Date**: {VERIFY_DATE:%Y-%m-%d}")
    lines.append(f"**Analysis Dates**: {', '.join(d.strftime('%Y-%m-%d') for d in ANALYSIS_DATES)}")
    lines.append(f"**Universe**: {len(tickers)} ETFs from config.yaml\n")

    lines.append("## 1. Accuracy Summary (시점별 통합)\n")
    lines.append(format_accuracy_table(results))

    lines.append("\n\n## 2. Trend 분류별 실제 성과\n")
    lines.append(format_trend_breakdown(results))

    lines.append("\n\n## 3. Growth vs Value\n")
    lines.append(format_growth_value(results))

    lines.append("\n\n## 4. Sector Performance (시점별)\n")
    lines.append(format_sector_performance(results))

    lines.append("\n\n## 5. Top/Bottom Performers\n")
    lines.append(format_top_bottom(results))

    # ── Cross-horizon interpretation ──
    lines.append("\n\n## 6. Cross-Horizon 해석\n")
    trend_accs = [r["metrics"]["trend_acc"] for r in results]
    macd_accs = [r["metrics"]["macd_overall"] for r in results if r["metrics"]["macd_overall"] is not None]
    median_returns = [r["metrics"]["median_return"] for r in results]

    lines.append(
        f"- **Trend 정확도 범위**: {min(trend_accs):.1f}% ~ {max(trend_accs):.1f}% "
        f"(평균 {np.mean(trend_accs):.1f}%)"
    )
    if macd_accs:
        lines.append(
            f"- **MACD 정확도 범위**: {min(macd_accs):.1f}% ~ {max(macd_accs):.1f}% "
            f"(평균 {np.mean(macd_accs):.1f}%)"
        )
    lines.append(
        f"- **실제 시장 수익률 (중앙값)**: "
        + ", ".join(f"{r['analysis_date']:%Y}={r['metrics']['median_return']:+.1f}%" for r in results)
    )
    lines.append(
        "- **해석 가이드**: Trend 정확도가 horizon이 길어질수록 하락한다면 장기 예측력 한계, "
        "horizon에 관계없이 일정하면 구조적 유효성, 1Y→1Y 비교해 연도 장세에 따라 크게 변동하면 "
        "모델보다 매크로 요인 영향이 큼."
    )

    out_path = Path("reports") / "Backtest_MultiYear_vs_2025.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines), encoding="utf-8")

    print("\n" + "=" * 60)
    print("  MULTI-YEAR BACKTEST SUMMARY")
    for r in results:
        m = r["metrics"]
        print(
            f"  {r['analysis_date']:%Y-%m-%d} ({r['horizon']}): "
            f"Trend={m['trend_acc']:.1f}%  MACD={m['macd_overall']:.1f}%  "
            f"Median={m['median_return']:+.1f}%  n={m['n']}"
        )
    print(f"\n  Report: {out_path}")
    print("=" * 60)


if __name__ == "__main__":
    main()
