#!/usr/bin/env python3
"""
ETF Analysis Backtest Pipeline
===============================
2025-04-11 기준 분석을 수행하고,
2026-04-10 실제 결과와 비교하여 정확도를 측정합니다.

검증 항목:
1. 추세 판단 정확도 (Golden Cross → 상승? Death Cross → 하락?)
2. RSI 과매수/과매도 시그널 정확도
3. 섹터 순위 예측 정확도
4. 성장 vs 가치 판단 정확도
"""

import sys
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf
import yaml

from src.analyzer import analyze_etf


def load_config(path="config.yaml"):
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def get_all_tickers(config):
    ticker_names = {}
    for group in config.get("etfs", {}).values():
        if isinstance(group, dict):
            ticker_names.update(group)
    return ticker_names


def fetch_historical_data(tickers, end_date, period_days=365):
    """특정 날짜 기준 과거 데이터 수집."""
    start_date = end_date - timedelta(days=period_days + 30)
    data = {}

    print(f"  Fetching data up to {end_date.strftime('%Y-%m-%d')}...")
    try:
        raw = yf.download(
            tickers, start=start_date.strftime("%Y-%m-%d"),
            end=end_date.strftime("%Y-%m-%d"), progress=False,
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

    print(f"  {len(data)}/{len(tickers)} tickers loaded")
    return data


def fetch_future_price(tickers, target_date):
    """특정 미래 날짜의 종가 수집."""
    start = target_date - timedelta(days=5)
    end = target_date + timedelta(days=5)
    prices = {}

    try:
        raw = yf.download(
            tickers, start=start.strftime("%Y-%m-%d"),
            end=end.strftime("%Y-%m-%d"), progress=False,
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
    except Exception:
        pass

    return prices


def run_backtest():
    config = load_config()
    ticker_names = get_all_tickers(config)
    tickers = list(ticker_names.keys())
    leveraged_tickers = set(config.get("etfs", {}).get("leveraged", {}).keys())

    analysis_date = datetime(2025, 4, 11)
    verify_date = datetime(2026, 4, 10)

    print("=" * 60)
    print("  ETF Analysis Backtest Pipeline")
    print(f"  Analysis Date : {analysis_date.strftime('%Y-%m-%d')}")
    print(f"  Verify Date   : {verify_date.strftime('%Y-%m-%d')}")
    print(f"  ETFs          : {len(tickers)}")
    print("=" * 60)

    # ── Step 1: 2025-04-11 기준 데이터 수집 및 분석 ──
    print("\n[1/3] Historical analysis (2025-04-11)")
    hist_data = fetch_historical_data(tickers, analysis_date)

    past_analysis = {}
    for t in tickers:
        if t in hist_data:
            try:
                past_analysis[t] = analyze_etf(t, hist_data[t])
            except Exception:
                pass
    print(f"  {len(past_analysis)} ETFs analyzed")

    # ── Step 2: 2025-04-11 가격 + 2026-04-10 가격 수집 ──
    print("\n[2/3] Fetching verification prices (2026-04-10)")
    future_prices = fetch_future_price(tickers, verify_date)
    print(f"  {len(future_prices)} future prices loaded")

    # 2025-04-11 가격 (분석 데이터에서)
    past_prices = {}
    for t, a in past_analysis.items():
        past_prices[t] = a["price"]

    # ── Step 3: 정확도 측정 ──
    print("\n[3/3] Calculating accuracy")

    results = []
    for t in tickers:
        if t not in past_analysis or t not in future_prices or t not in past_prices:
            continue

        a = past_analysis[t]
        p_old = past_prices[t]
        p_new = future_prices[t]
        actual_return = ((p_new - p_old) / p_old) * 100

        results.append({
            "ticker": t,
            "name": ticker_names.get(t, t),
            "price_2025": round(p_old, 2),
            "price_2026": round(p_new, 2),
            "actual_return_pct": round(actual_return, 2),
            "trend_2025": a["trend"],
            "rsi_2025": a["rsi"],
            "macd_2025": a["macd_crossover"],
            "macd_momentum_2025": a.get("macd_momentum", "N/A"),
            "adx_2025": a.get("adx"),
            "trend_strength_2025": a.get("trend_strength", "N/A"),
            "volatility_2025": a["volatility_30d"],
            "is_leveraged": t in leveraged_tickers,
        })

    df_all = pd.DataFrame(results)
    if df_all.empty:
        print("  [ERROR] No matching data for backtest")
        return

    # Split: 일반 ETF vs 레버리지/인버스
    df = df_all[~df_all["is_leveraged"]].copy()
    df_lev = df_all[df_all["is_leveraged"]].copy()

    # ── 정확도 분석 (일반 ETF만) ──
    print(f"\n  Total matched ETFs: {len(df_all)} (regular: {len(df)}, leveraged: {len(df_lev)})")

    # 1. 추세 판단 정확도
    def trend_correct(row):
        bullish_trends = ["Strong Uptrend (Golden Cross)", "Uptrend"]
        bearish_trends = ["Strong Downtrend (Death Cross)", "Downtrend"]
        if row["trend_2025"] in bullish_trends and row["actual_return_pct"] > 0:
            return True
        if row["trend_2025"] in bearish_trends and row["actual_return_pct"] <= 0:
            return True
        return False

    df["trend_correct"] = df.apply(trend_correct, axis=1)
    trend_acc = df["trend_correct"].mean() * 100

    # 2. RSI 과매수/과매도 정확도
    market_avg = df["actual_return_pct"].median()
    baseline_positive_rate = (df["actual_return_pct"] > 0).mean() * 100

    overbought = df[df["rsi_2025"].notna() & (df["rsi_2025"] > 70)]
    oversold = df[df["rsi_2025"].notna() & (df["rsi_2025"] < 30)]
    near_oversold = df[df["rsi_2025"].notna() & (df["rsi_2025"] >= 30) & (df["rsi_2025"] < 40)]

    ob_correct = 0
    if len(overbought) > 0:
        ob_correct = (overbought["actual_return_pct"] < market_avg).mean() * 100

    os_correct = 0
    if len(oversold) > 0:
        os_correct = (oversold["actual_return_pct"] > 0).mean() * 100

    # Near-oversold (RSI 30-40): captures signals shifted by Wilder's EMA
    near_os_correct = 0
    near_os_avg = 0.0
    if len(near_oversold) > 0:
        near_os_correct = (near_oversold["actual_return_pct"] > 0).mean() * 100
        near_os_avg = near_oversold["actual_return_pct"].mean()

    # 3. MACD 시그널 정확도
    macd_bull = df[df["macd_2025"] == "Bullish"]
    macd_bear = df[df["macd_2025"] == "Bearish"]
    macd_bull_acc = (macd_bull["actual_return_pct"] > 0).mean() * 100 if len(macd_bull) > 0 else 0
    macd_bear_acc = (macd_bear["actual_return_pct"] <= 0).mean() * 100 if len(macd_bear) > 0 else 0
    macd_overall = 0
    macd_total = len(macd_bull) + len(macd_bear)
    if macd_total > 0:
        macd_correct = (
            (macd_bull["actual_return_pct"] > 0).sum() +
            (macd_bear["actual_return_pct"] <= 0).sum()
        )
        macd_overall = macd_correct / macd_total * 100

    # 3b. MACD Momentum (histogram direction) accuracy
    macd_rising = df[df["macd_momentum_2025"] == "Rising"]
    macd_falling = df[df["macd_momentum_2025"] == "Falling"]
    macd_mom_rising_acc = (macd_rising["actual_return_pct"] > 0).mean() * 100 if len(macd_rising) > 0 else 0
    macd_mom_falling_acc = (macd_falling["actual_return_pct"] <= 0).mean() * 100 if len(macd_falling) > 0 else 0

    # 3c. Contrarian accuracy: bearish signals → positive return = correct contrarian call
    bearish_trends = ["Strong Downtrend (Death Cross)", "Downtrend"]
    bearish_df = df[df["trend_2025"].isin(bearish_trends)]
    contrarian_acc = (bearish_df["actual_return_pct"] > 0).mean() * 100 if len(bearish_df) > 0 else 0

    # Market regime detection
    total_downtrend = df["trend_2025"].isin(bearish_trends).sum()
    pct_downtrend = total_downtrend / len(df) * 100
    if pct_downtrend > 70:
        market_regime = "Crash / Extreme Bearish"
    elif pct_downtrend > 50:
        market_regime = "Bear Market"
    elif pct_downtrend > 30:
        market_regime = "Mixed / Transition"
    else:
        market_regime = "Bull Market"

    # 4. 섹터 분석
    sector_tickers = list(config.get("etfs", {}).get("sectors", {}).keys())
    sector_df = df[df["ticker"].isin(sector_tickers)].copy()

    # 5. 성장 vs 가치
    growth_tickers = ["VUG", "IWF", "SCHG", "MGK"]
    value_tickers = ["VTV", "IWD", "SCHV"]
    growth_ret = df[df["ticker"].isin(growth_tickers)]["actual_return_pct"].mean()
    value_ret = df[df["ticker"].isin(value_tickers)]["actual_return_pct"].mean()

    # 6. Top/Bottom performers
    df_sorted = df.sort_values("actual_return_pct", ascending=False)

    # ── 보고서 생성 ──
    report_lines = []
    report_lines.append("# ETF Analysis Backtest Report")
    report_lines.append(f"**Analysis Date**: 2025-04-11 | **Verification Date**: 2026-04-10")
    report_lines.append(f"**Matched ETFs**: {len(df_all)} (Regular: {len(df)}, Leveraged/Inverse: {len(df_lev)})")
    report_lines.append("**Note**: All accuracy metrics use regular ETFs only (excluding leveraged/inverse to avoid distortion).\n")

    report_lines.append("---\n")
    report_lines.append("## 1. Market Regime at Analysis Date\n")
    report_lines.append(f"- **Detected Regime**: {market_regime}")
    report_lines.append(f"- **ETFs in Downtrend/Death Cross**: {total_downtrend}/{len(df)} ({pct_downtrend:.0f}%)")
    if pct_downtrend > 50:
        report_lines.append(
            "- **Note**: Trend accuracy is expected to be LOW in crash/bear regimes. "
            "In such environments, bearish signals often mark the BOTTOM — "
            "treat them as contrarian buy opportunities."
        )

    report_lines.append("\n---\n")
    report_lines.append("## 2. Overall Accuracy Summary\n")
    report_lines.append("| Metric | Accuracy | Sample Size |")
    report_lines.append("|--------|----------|-------------|")
    report_lines.append(f"| Trend Direction (naive) | {trend_acc:.1f}% | {len(df)} |")
    report_lines.append(f"| **Contrarian (bearish → positive)** | **{contrarian_acc:.1f}%** | {len(bearish_df)} |")
    report_lines.append(f"| RSI Overbought (>70) underperform | {ob_correct:.1f}% | {len(overbought)} |")
    report_lines.append(f"| RSI Oversold (<30) bounce | {os_correct:.1f}% | {len(oversold)} |")
    report_lines.append(f"| RSI Near-Oversold (30-40) bounce | {near_os_correct:.1f}% (avg {near_os_avg:+.1f}%) | {len(near_oversold)} |")
    report_lines.append(f"| MACD Bullish → positive return | {macd_bull_acc:.1f}% | {len(macd_bull)} |")
    report_lines.append(f"| MACD Bearish → negative return | {macd_bear_acc:.1f}% | {len(macd_bear)} |")
    report_lines.append(f"| MACD Overall | {macd_overall:.1f}% | {macd_total} |")
    report_lines.append(f"| MACD Momentum Rising → positive | {macd_mom_rising_acc:.1f}% | {len(macd_rising)} |")
    report_lines.append(f"| MACD Momentum Falling → negative | {macd_mom_falling_acc:.1f}% | {len(macd_falling)} |")
    report_lines.append(f"| **Baseline** (any ETF → positive) | {baseline_positive_rate:.1f}% | {len(df)} |")

    # Lift = signal accuracy - baseline (how much better than random)
    report_lines.append("\n### Signal Lift vs Baseline\n")
    report_lines.append("| Signal | Accuracy | Baseline | **Lift** | Interpretation |")
    report_lines.append("|--------|----------|----------|----------|----------------|")

    def _lift_row(name, acc, n, baseline):
        if n == 0:
            return f"| {name} | N/A | {baseline:.1f}% | N/A | insufficient data |"
        lift = acc - baseline
        interp = "Useful" if lift > 5 else ("Marginal" if lift > 0 else "Useless/Contrarian")
        return f"| {name} | {acc:.1f}% | {baseline:.1f}% | **{lift:+.1f}pp** | {interp} |"

    report_lines.append(_lift_row("Contrarian (bearish→+)", contrarian_acc, len(bearish_df), baseline_positive_rate))
    report_lines.append(_lift_row("MACD Momentum Rising→+", macd_mom_rising_acc, len(macd_rising), baseline_positive_rate))
    report_lines.append(_lift_row("RSI Oversold (<30)→+", os_correct, len(oversold), baseline_positive_rate))
    report_lines.append(_lift_row("RSI Near-Oversold (30-40)→+", near_os_correct, len(near_oversold), baseline_positive_rate))
    report_lines.append(_lift_row("Trend Naive (direction match)", trend_acc, len(df), 50.0))

    report_lines.append("\n---\n")
    report_lines.append("## 3. Trend Prediction Detail\n")

    trend_groups = df.groupby("trend_2025").agg(
        count=("ticker", "count"),
        avg_return=("actual_return_pct", "mean"),
        correct=("trend_correct", "mean"),
    ).round(2)
    trend_groups["correct"] = (trend_groups["correct"] * 100).round(1)

    report_lines.append("| Trend (2025) | Count | Avg 1Y Return | Accuracy |")
    report_lines.append("|-------------|-------|---------------|----------|")
    for idx, row in trend_groups.iterrows():
        report_lines.append(f"| {idx} | {int(row['count'])} | {row['avg_return']:+.2f}% | {row['correct']:.1f}% |")

    report_lines.append("\n---\n")
    report_lines.append("## 4. Growth vs Value (2025 -> 2026)\n")
    report_lines.append(f"- **Growth ETFs avg return**: {growth_ret:+.2f}%")
    report_lines.append(f"- **Value ETFs avg return**: {value_ret:+.2f}%")
    winner = "Value" if value_ret > growth_ret else "Growth"
    report_lines.append(f"- **Winner**: {winner} (by {abs(value_ret - growth_ret):.2f}pp)")

    report_lines.append("\n---\n")
    report_lines.append("## 5. Sector Performance (Actual 1Y Return)\n")
    if not sector_df.empty:
        sector_sorted = sector_df.sort_values("actual_return_pct", ascending=False)
        report_lines.append("| Rank | Sector | Ticker | 2025 Price | 2026 Price | 1Y Return | 2025 Trend |")
        report_lines.append("|------|--------|--------|------------|------------|-----------|------------|")
        for i, (_, r) in enumerate(sector_sorted.iterrows(), 1):
            report_lines.append(
                f"| {i} | {r['name']} | {r['ticker']} | ${r['price_2025']} | "
                f"${r['price_2026']} | {r['actual_return_pct']:+.2f}% | {r['trend_2025']} |"
            )

    report_lines.append("\n---\n")
    report_lines.append("## 6. Top 10 Performers (Actual)\n")
    report_lines.append("| Rank | ETF | 2025 Price | 2026 Price | 1Y Return | 2025 Trend | 2025 RSI |")
    report_lines.append("|------|-----|------------|------------|-----------|------------|----------|")
    for i, (_, r) in enumerate(df_sorted.head(10).iterrows(), 1):
        rsi = f"{r['rsi_2025']:.1f}" if r['rsi_2025'] else "N/A"
        report_lines.append(
            f"| {i} | {r['name']} ({r['ticker']}) | ${r['price_2025']} | "
            f"${r['price_2026']} | **{r['actual_return_pct']:+.2f}%** | {r['trend_2025']} | {rsi} |"
        )

    report_lines.append("\n## 7. Bottom 10 Performers (Actual)\n")
    report_lines.append("| Rank | ETF | 2025 Price | 2026 Price | 1Y Return | 2025 Trend | 2025 RSI |")
    report_lines.append("|------|-----|------------|------------|-----------|------------|----------|")
    for i, (_, r) in enumerate(df_sorted.tail(10).iloc[::-1].iterrows(), 1):
        rsi = f"{r['rsi_2025']:.1f}" if r['rsi_2025'] else "N/A"
        report_lines.append(
            f"| {i} | {r['name']} ({r['ticker']}) | ${r['price_2025']} | "
            f"${r['price_2026']} | **{r['actual_return_pct']:+.2f}%** | {r['trend_2025']} | {rsi} |"
        )

    report_lines.append("\n---\n")
    report_lines.append("## 8. Statistical Summary\n")
    report_lines.append(f"- Median 1Y Return: {df['actual_return_pct'].median():+.2f}%")
    report_lines.append(f"- Mean 1Y Return: {df['actual_return_pct'].mean():+.2f}%")
    report_lines.append(f"- Positive returns: {(df['actual_return_pct'] > 0).sum()}/{len(df)} ({(df['actual_return_pct'] > 0).mean()*100:.1f}%)")
    report_lines.append(f"- Max gain: {df['actual_return_pct'].max():+.2f}% ({df_sorted.iloc[0]['name']})")
    report_lines.append(f"- Max loss: {df['actual_return_pct'].min():+.2f}% ({df_sorted.iloc[-1]['name']})")

    # ── 9. Signal Combination Analysis ──
    report_lines.append("\n---\n")
    report_lines.append("## 9. Signal Combination Analysis\n")
    report_lines.append("어떤 시그널 조합이 최고의 수익률을 냈는지 분석합니다.")
    report_lines.append("**주의**: 단일 기간 결과입니다. 5년 교차검증은 `backtest_5year.py` 참조.\n")
    report_lines.append("| Combination | ETFs | Avg Return | Median Return | vs Market Median | Win Rate |")
    report_lines.append("|-------------|------|------------|---------------|------------------|----------|")

    combos = []
    # RSI Near-Oversold + MACD Momentum Rising
    c1 = df[(df["rsi_2025"].notna()) & (df["rsi_2025"] < 40) & (df["macd_momentum_2025"] == "Rising")]
    combos.append(("RSI<40 + MACD Mom Rising", c1))

    # Death Cross + MACD Momentum Rising (reversal)
    c2 = df[(df["trend_2025"] == "Strong Downtrend (Death Cross)") & (df["macd_momentum_2025"] == "Rising")]
    combos.append(("Death Cross + MACD Mom Rising", c2))

    # Bearish + RSI<40 (deep contrarian)
    bearish_trends_list = ["Strong Downtrend (Death Cross)", "Downtrend"]
    c3 = df[(df["trend_2025"].isin(bearish_trends_list)) & (df["rsi_2025"].notna()) & (df["rsi_2025"] < 40)]
    combos.append(("Bearish + RSI<40", c3))

    # ADX Strong + any trend
    c4 = df[(df["adx_2025"].notna()) & (df["adx_2025"] >= 25)]
    combos.append(("ADX>=25 (Strong Trend)", c4))

    # ADX Weak (no trend) - expect sideways
    c5 = df[(df["adx_2025"].notna()) & (df["adx_2025"] < 20)]
    combos.append(("ADX<20 (No Trend)", c5))

    # Death Cross + ADX Strong (strong downtrend, but contrarian?)
    c6 = df[(df["trend_2025"] == "Strong Downtrend (Death Cross)") & (df["adx_2025"].notna()) & (df["adx_2025"] >= 25)]
    combos.append(("Death Cross + ADX>=25", c6))

    # RSI<40 + MACD Rising + ADX>=25 (triple signal)
    c7 = df[(df["rsi_2025"].notna()) & (df["rsi_2025"] < 40) &
            (df["macd_momentum_2025"] == "Rising") &
            (df["adx_2025"].notna()) & (df["adx_2025"] >= 25)]
    combos.append(("RSI<40 + MACD Rising + ADX>=25", c7))

    # All ETFs (baseline)
    combos.append(("All ETFs (Baseline)", df))

    for name, subset in combos:
        if len(subset) == 0:
            report_lines.append(f"| {name} | 0 | N/A | N/A | N/A | N/A |")
            continue
        avg = subset["actual_return_pct"].mean()
        med = subset["actual_return_pct"].median()
        vs_mkt = med - market_avg
        win = (subset["actual_return_pct"] > 0).mean() * 100
        report_lines.append(f"| {name} | {len(subset)} | {avg:+.1f}% | {med:+.1f}% | **{vs_mkt:+.1f}pp** | {win:.0f}% |")

    # ── 10. Leveraged/Inverse ETF (별도) ──
    report_lines.append("\n---\n")
    report_lines.append("## 10. Leveraged / Inverse ETFs (별도 분석)\n")
    report_lines.append("레버리지/인버스 ETF는 일반 분석에서 제외됩니다 (수익률 왜곡 방지).\n")
    if not df_lev.empty:
        lev_sorted = df_lev.sort_values("actual_return_pct", ascending=False)
        report_lines.append("| ETF | 2025 Price | 2026 Price | 1Y Return | Trend | RSI | ADX |")
        report_lines.append("|-----|------------|------------|-----------|-------|-----|-----|")
        for _, r in lev_sorted.iterrows():
            rsi = f"{r['rsi_2025']:.1f}" if r['rsi_2025'] else "N/A"
            adx = f"{r['adx_2025']:.1f}" if r['adx_2025'] else "N/A"
            report_lines.append(
                f"| {r['name']} ({r['ticker']}) | ${r['price_2025']} | "
                f"${r['price_2026']} | **{r['actual_return_pct']:+.2f}%** | {r['trend_2025']} | {rsi} | {adx} |"
            )
        lev_med = df_lev["actual_return_pct"].median()
        lev_avg = df_lev["actual_return_pct"].mean()
        report_lines.append(f"\n- Leveraged Median: {lev_med:+.1f}% | Mean: {lev_avg:+.1f}%")
        report_lines.append(f"- Regular ETF Median: {market_avg:+.1f}% (leveraged excluded from all metrics above)")
    else:
        report_lines.append("_(no leveraged ETFs matched)_")

    report = "\n".join(report_lines)

    # Save
    out_path = Path("reports") / "Backtest_2025-04-11_to_2026-04-10.md"
    out_path.write_text(report, encoding="utf-8")
    print(f"\n  Report saved: {out_path}")

    # Print summary
    print(f"\n{'=' * 60}")
    print(f"  BACKTEST RESULTS SUMMARY")
    print(f"  Market Regime:      {market_regime} ({pct_downtrend:.0f}% downtrend)")
    print(f"  Trend Accuracy:     {trend_acc:.1f}% (naive)")
    print(f"  Contrarian Acc:     {contrarian_acc:.1f}% (bearish->positive)")
    print(f"  MACD Accuracy:      {macd_overall:.1f}%")
    print(f"  MACD Momentum:      Rising={macd_mom_rising_acc:.1f}% ({len(macd_rising)}) | Falling={macd_mom_falling_acc:.1f}% ({len(macd_falling)})")
    print(f"  RSI Overbought:     {ob_correct:.1f}% ({len(overbought)} ETFs)")
    print(f"  RSI Oversold:       {os_correct:.1f}% ({len(oversold)} ETFs)")
    print(f"  RSI Near-Oversold:  {near_os_correct:.1f}% ({len(near_oversold)} ETFs, avg {near_os_avg:+.1f}%)")
    print(f"  Growth vs Value:    {winner} won by {abs(value_ret - growth_ret):.1f}pp")
    print(f"  Matched ETFs:       {len(df)}")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    run_backtest()
