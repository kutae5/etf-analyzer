#!/usr/bin/env python3
"""
DCA Strategy Backtest (2021-2026)
==================================
월 300만원 적립 × DCA 타이밍 vs 단순 SPY 적립 비교.
매월 1일(근사) 시점에서 시장 상태를 분석하고,
DCA 타이밍 엔진의 투입 비율 결정을 시뮬레이션합니다.

검증:
1. DCA 타이밍 전략 총 수익률 vs SPY 단순 적립
2. 시장 하락기에 실제로 더 많이 투입했는지
3. 현금 버퍼 활용 효과
"""

import sys
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf
import yaml

from src.analyzer import analyze_etf
from src.enhanced import (
    compute_contrarian_scores, compute_market_breadth, compute_market_regime,
)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def load_config():
    with open("config.yaml", "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def get_tickers(config):
    names = {}
    for grp, tickers in config.get("etfs", {}).items():
        if isinstance(tickers, dict):
            names.update(tickers)
    return names


def fetch_data(tickers, end_date, lookback=400):
    start = end_date - timedelta(days=lookback)
    data = {}
    try:
        raw = yf.download(
            tickers, start=start.strftime("%Y-%m-%d"),
            end=end_date.strftime("%Y-%m-%d"), progress=False,
        )
        if raw.empty:
            return data
        if isinstance(raw.columns, pd.MultiIndex):
            avail = raw.columns.get_level_values("Ticker").unique()
            for t in tickers:
                if t in avail:
                    df = raw.xs(t, level="Ticker", axis=1).dropna(how="all")
                    if len(df) > 50:
                        data[t] = df
        else:
            if len(raw) > 50:
                data[tickers[0]] = raw
    except Exception:
        pass
    return data


def fetch_indicators(config, end_date):
    """Simplified market indicator fetch for backtest."""
    indicators_cfg = config.get("market_indicators", {})
    if not indicators_cfg:
        return {}
    symbols = list(indicators_cfg.keys())
    start = end_date - timedelta(days=15)
    result = {}
    try:
        raw = yf.download(
            symbols, start=start.strftime("%Y-%m-%d"),
            end=end_date.strftime("%Y-%m-%d"), progress=False,
        )
    except Exception:
        return result
    if raw.empty:
        return result

    for sym, name in indicators_cfg.items():
        try:
            if isinstance(raw.columns, pd.MultiIndex):
                close = raw.xs(sym, level="Ticker", axis=1)["Close"].dropna()
            else:
                close = raw["Close"].dropna()
            if len(close) < 2:
                continue
            v = float(close.iloc[-1])
            d1 = float(close.iloc[-2])
            d5 = float(close.iloc[-6]) if len(close) >= 6 else v
            result[name] = {
                "ticker": sym, "value": round(v, 2),
                "change_1d_pct": round((v - d1) / d1 * 100, 2) if d1 else 0,
                "change_5d_pct": round((v - d5) / d5 * 100, 2) if d5 else 0,
            }
        except (KeyError, ValueError):
            pass
    return result


def simulate_dca_timing(enhanced, etf_analysis):
    """Simplified DCA timing score (mirrors portfolio.py logic)."""
    from src.portfolio import compute_dca_timing
    result = compute_dca_timing(enhanced, etf_analysis, monthly_budget=3_000_000)
    return result["deploy_pct"], result["signal"]


def main():
    config = load_config()
    ticker_names = get_tickers(config)
    tickers = list(ticker_names.keys())
    leveraged = set(config.get("etfs", {}).get("leveraged", {}).keys())

    MONTHLY = 3_000_000
    # Monthly dates: 1st trading day of each month (approximate with 10th)
    months = pd.date_range("2021-06-10", "2026-03-10", freq="MS") + timedelta(days=9)

    print("=" * 65)
    print("  DCA Strategy Backtest (2021-2026)")
    print(f"  Monthly: {MONTHLY:,.0f}원 | Months: {len(months)}")
    print("=" * 65)

    # Fetch SPY for benchmark
    print("\n  Fetching SPY benchmark...")
    spy_raw = yf.download("SPY", start="2021-06-01", end="2026-04-13", progress=False)
    if isinstance(spy_raw.columns, pd.MultiIndex):
        spy_raw = spy_raw.xs("SPY", level="Ticker", axis=1)
    spy_close = spy_raw["Close"]

    # Results tracking
    dca_invested = 0
    dca_cash_reserve = 0
    spy_invested = 0
    spy_shares = 0.0
    month_results = []

    for i, date in enumerate(months):
        dt = date.to_pydatetime()
        label = dt.strftime("%Y-%m")
        print(f"\r  [{i+1}/{len(months)}] {label}...", end="", flush=True)

        try:
            # Get SPY price for this month
            spy_idx = spy_close.index.searchsorted(pd.Timestamp(dt))
            spy_idx = min(spy_idx, len(spy_close) - 1)
            spy_price = float(spy_close.iloc[spy_idx])

            # SPY simple DCA: always invest full amount
            spy_invested += MONTHLY
            spy_shares += MONTHLY / spy_price

            # DCA timing: analyze market state
            price_data = fetch_data(tickers[:30], dt, lookback=300)  # Top 30 for speed
            if len(price_data) < 10:
                # Fallback: full invest
                deploy_pct = 100
                signal = "FULL (no data)"
            else:
                etf_analysis = {}
                for t in list(price_data.keys()):
                    try:
                        etf_analysis[t] = analyze_etf(t, price_data[t])
                    except Exception:
                        pass

                if len(etf_analysis) < 5:
                    deploy_pct = 100
                    signal = "FULL (insufficient)"
                else:
                    market_ind = fetch_indicators(config, dt)
                    breadth = compute_market_breadth(etf_analysis)
                    enhanced = {
                        "market_regime": compute_market_regime(market_ind, breadth),
                        "market_breadth": breadth,
                        "contrarian_scores": compute_contrarian_scores(etf_analysis),
                    }
                    deploy_pct, signal = simulate_dca_timing(enhanced, etf_analysis)

            # Deploy with timing
            available = MONTHLY + dca_cash_reserve
            deploy_amount = min(round(MONTHLY * deploy_pct / 100), available)
            save_amount = MONTHLY - deploy_amount if deploy_pct < 100 else 0
            extra = max(0, deploy_amount - MONTHLY)

            # Invest in SPY (simplified: DCA timing investor also buys SPY)
            dca_shares_bought = deploy_amount / spy_price if spy_price > 0 else 0
            dca_invested += deploy_amount
            dca_cash_reserve = dca_cash_reserve - extra + save_amount

            month_results.append({
                "month": label,
                "spy_price": round(spy_price, 2),
                "signal": signal,
                "deploy_pct": deploy_pct,
                "deployed": deploy_amount,
                "saved": save_amount,
                "cash_reserve": dca_cash_reserve,
                "dca_shares": dca_shares_bought,
            })
        except Exception as e:
            month_results.append({
                "month": label, "spy_price": 0, "signal": f"ERROR: {e}",
                "deploy_pct": 100, "deployed": MONTHLY, "saved": 0,
                "cash_reserve": dca_cash_reserve, "dca_shares": 0,
            })

    print("\n")

    # Final valuation
    final_spy_price = float(spy_close.iloc[-1])
    spy_final_value = spy_shares * final_spy_price
    spy_total_invested = spy_invested

    dca_total_shares = sum(r["dca_shares"] for r in month_results)
    dca_final_value = dca_total_shares * final_spy_price + dca_cash_reserve
    dca_total_cost = MONTHLY * len(months)

    spy_return = ((spy_final_value - spy_total_invested) / spy_total_invested) * 100
    dca_return = ((dca_final_value - dca_total_cost) / dca_total_cost) * 100
    alpha = dca_return - spy_return

    # Report
    report = []
    report.append("# DCA Strategy Backtest Report")
    report.append(f"\n**Period**: 2021-06 ~ 2026-03 ({len(months)} months)")
    report.append(f"**Monthly Budget**: {MONTHLY:,.0f}원")
    report.append(f"**Total Invested**: {dca_total_cost:,.0f}원\n")

    report.append("## 1. Final Results\n")
    report.append("| Strategy | Total Cost | Final Value | Return | Alpha |")
    report.append("|----------|-----------|-------------|--------|-------|")
    report.append(f"| SPY Simple DCA | {spy_total_invested:,.0f}원 | {spy_final_value:,.0f}원 | {spy_return:+.1f}% | — |")
    report.append(f"| **DCA Timing** | {dca_total_cost:,.0f}원 | {dca_final_value:,.0f}원 | **{dca_return:+.1f}%** | **{alpha:+.1f}pp** |")

    report.append(f"\n## 2. Monthly Detail\n")
    report.append("| Month | SPY Price | Signal | Deploy% | Deployed | Saved | Cash Reserve |")
    report.append("|-------|-----------|--------|---------|----------|-------|-------------|")
    for r in month_results:
        report.append(
            f"| {r['month']} | ${r['spy_price']} | {r['signal']} | {r['deploy_pct']}% | "
            f"{r['deployed']:,.0f}원 | {r['saved']:,.0f}원 | {r['cash_reserve']:,.0f}원 |"
        )

    # Signal distribution
    report.append(f"\n## 3. Signal Distribution\n")
    signals = pd.DataFrame(month_results)
    signal_counts = signals["signal"].value_counts()
    for sig, count in signal_counts.items():
        report.append(f"- {sig}: {count}회 ({count/len(months)*100:.0f}%)")

    report.append(f"\n## 4. Key Findings\n")
    report.append(f"- DCA Timing vs Simple: **{alpha:+.1f}pp** alpha")
    if alpha > 0:
        report.append(f"- DCA 타이밍이 단순 적립보다 우수 (시장 하락 시 더 많이 투입 효과)")
    else:
        report.append(f"- 단순 적립이 더 나았음 (타이밍 비용이 기회비용 초과)")
    report.append(f"- 최종 현금 버퍼: {dca_cash_reserve:,.0f}원")

    avg_deploy = signals["deploy_pct"].mean()
    report.append(f"- 평균 투입 비율: {avg_deploy:.0f}%")

    out_path = Path("reports") / "Backtest_DCA_Strategy.md"
    out_path.write_text("\n".join(report), encoding="utf-8")

    print(f"{'=' * 65}")
    print(f"  DCA BACKTEST RESULTS")
    print(f"  SPY Simple:    {spy_return:+.1f}% ({spy_final_value:,.0f}원)")
    print(f"  DCA Timing:    {dca_return:+.1f}% ({dca_final_value:,.0f}원)")
    print(f"  Alpha:         {alpha:+.1f}pp")
    print(f"  Cash Reserve:  {dca_cash_reserve:,.0f}원")
    print(f"  Report: {out_path}")
    print(f"{'=' * 65}")


if __name__ == "__main__":
    main()
