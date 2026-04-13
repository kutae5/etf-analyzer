#!/usr/bin/env python3
"""
Report-Level Backtest (Full Pipeline)
======================================
각 역사 시점(2022/2023/2024-04-11)에서 **전체 파이프라인**을 실제로 실행해
Claude가 생성한 최종 보고서의 **BUY 추천 종목**이 2025-04-11 기준으로
얼마나 alpha를 냈는지 측정합니다.

이전 backtest_multi_year.py가 raw 시그널(trend/RSI/MACD) 정확도를 측정한 반면,
이 스크립트는 보고서 레벨의 품질(추천 종목의 실제 성과)을 측정합니다.

파이프라인:
    historical fetch → analyze → enhanced → generate_via_claude_code →
    evaluator-optimizer → LLM rec extraction → alpha scoring
"""

import json
import re
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import yaml
import yfinance as yf

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from src.analyzer import analyze_etf, analyze_sectors, analyze_correlations
from src.enhanced import (
    compute_contrarian_scores, compute_mean_reversion, compute_vol_adj_momentum,
    compute_sector_rs, compute_dividend_spread, compute_market_regime,
    compute_market_breadth, format_enhanced_data,
)
from src.reporter import (
    format_analysis_data, generate_via_claude_code, _run_claude_cli,
)


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
    for g in config.get("etfs", {}).values():
        if isinstance(g, dict):
            ticker_names.update(g)
    return ticker_names


def fetch_hist_prices(tickers, end_date, lookback_days=365):
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
        print(f"    [!] price fetch error: {e}")
    return data


def fetch_hist_indicators(indicators_cfg: dict, end_date: datetime) -> dict:
    """Historical market indicators (VIX, TNX, DXY, gold, oil, etc.).

    Matches the convention of src/fetcher.py:fetch_market_indicators:
    config keys are yahoo symbols (^VIX, ^TNX, ...), values are display names.
    Result dict is keyed by display name so enhanced.py can consume it.
    """
    if not indicators_cfg:
        return {}
    symbols = list(indicators_cfg.keys())
    start = end_date - timedelta(days=15)
    result = {}
    try:
        raw = yf.download(
            symbols,
            start=start.strftime("%Y-%m-%d"),
            end=end_date.strftime("%Y-%m-%d"),
            progress=False,
            auto_adjust=True,
        )
    except Exception as e:
        print(f"    [!] indicator fetch error: {e}")
        return result
    if raw.empty:
        return result

    def _extract_close(sym):
        try:
            if isinstance(raw.columns, pd.MultiIndex):
                return raw.xs(sym, level="Ticker", axis=1)["Close"].dropna()
            return raw["Close"].dropna()
        except (KeyError, ValueError):
            return pd.Series(dtype=float)

    for sym, name in indicators_cfg.items():
        close = _extract_close(sym)
        if len(close) < 2:
            continue
        v = float(close.iloc[-1])
        d1 = float(close.iloc[-2])
        d5 = float(close.iloc[-6]) if len(close) >= 6 else v
        result[name] = {
            "ticker": sym,
            "value": round(v, 2),
            "change_1d_pct": round((v - d1) / d1 * 100, 2) if d1 else 0.0,
            "change_5d_pct": round((v - d5) / d5 * 100, 2) if d5 else 0.0,
        }
    return result


def fetch_verify_prices(tickers, verify_date):
    start = verify_date - timedelta(days=5)
    end = verify_date + timedelta(days=5)
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
        print(f"    [!] verify fetch error: {e}")
    return prices


def extract_recommendations(report: str) -> dict:
    """Parse the generated report's Key Insights via claude -p into structured JSON.

    Uses 'primary ticker per pick' semantics aligned with Hard Rule #5:
    each Conviction-tagged pick contributes EXACTLY ONE ticker to BUY —
    the primary one in the pick's title/heading. Alternative/related ETFs
    mentioned in body prose are ignored.
    """
    prompt = (
        "Below is a Korean ETF market analysis report. Extract the ETF tickers "
        "that this report EXPLICITLY recommends using **primary-only semantics**.\n\n"
        "Output STRICT JSON only. No prose, no code fences. Schema:\n"
        '{"buy":["TICKER"...],"avoid":["TICKER"...],"sentiment_only":["TICKER"...]}\n\n'
        "Definitions:\n"
        "- **buy** = the ONE primary ticker per Conviction-tagged pick in Section 17 "
        "(Key Insights / 핵심 투자 인사이트). If a pick's title or heading mentions "
        "one ticker as the primary name (e.g. '**1. SMH — AI·반도체 코어**'), include "
        "only that ticker. **Ignore alternative/related tickers mentioned in the body "
        "prose (e.g. '대안: SOXX', '관련 ETF: ...')** — those do NOT count as BUY picks.\n"
        "- If a pick groups multiple tickers without a clear primary "
        "(e.g. 'Growth Overweight - VUG, MGK, IWF, QQQ, SCHG'), pick only the FIRST ticker listed.\n"
        "- **avoid** = tickers the report explicitly says to reduce/회피/축소/매도 권고\n"
        "- **sentiment_only** = leverage/inverse ETFs (TQQQ/SQQQ/SOXL/UVXY/TMF/UPRO/etc) "
        "mentioned ONLY as sentiment indicators, never as picks\n\n"
        "Skip passing mentions in data tables. The goal is to measure what a disciplined "
        "investor would actually buy (one per conviction pick).\n\n"
        f"REPORT:\n{report}\n\n"
        "Output ONLY the JSON object."
    )
    try:
        out = _run_claude_cli(prompt, timeout=300)
    except Exception as e:
        print(f"    [!] extraction failed: {e}")
        return {"buy": [], "avoid": [], "sentiment_only": []}

    out = re.sub(r"^```(?:json)?\s*", "", out.strip())
    out = re.sub(r"\s*```$", "", out)
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", out, flags=re.DOTALL)
        if not m:
            return {"buy": [], "avoid": [], "sentiment_only": []}
        try:
            data = json.loads(m.group(0))
        except json.JSONDecodeError:
            return {"buy": [], "avoid": [], "sentiment_only": []}

    for key in ("buy", "avoid", "sentiment_only"):
        data.setdefault(key, [])
        data[key] = [str(t).upper().strip() for t in data[key] if t]
    return data


def run_one(analysis_date, verify_date, config):
    ticker_names = get_all_tickers(config)
    tickers = list(ticker_names.keys())
    sector_map = config.get("etfs", {}).get("sectors", {})
    etf_groups = config.get("etfs", {})

    print(f"\n{'=' * 60}")
    print(f"  Analysis: {analysis_date:%Y-%m-%d}  →  Verify: {verify_date:%Y-%m-%d}")
    print(f"{'=' * 60}")

    start_t = time.time()

    print("  [1] Fetching historical prices...")
    price_data = fetch_hist_prices(tickers, analysis_date)
    print(f"      {len(price_data)}/{len(tickers)} tickers loaded")

    print("  [2] Fetching historical market indicators...")
    indicators_cfg = config.get("market_indicators", {})
    market_indicators = fetch_hist_indicators(indicators_cfg, analysis_date)
    print(f"      {len(market_indicators)} indicators")

    print("  [3] Running analysis...")
    etf_analysis = {}
    for t in tickers:
        if t in price_data:
            try:
                etf_analysis[t] = analyze_etf(t, price_data[t])
            except Exception:
                pass
    sector_results = analyze_sectors(price_data, sector_map)
    corr_tickers = list(etf_groups.get("indices", {}).keys()) + list(sector_map.keys())
    for extra in ["TLT", "GLD", "EEM", "SCHD", "SMH"]:
        if extra in price_data and extra not in corr_tickers:
            corr_tickers.append(extra)
    correlation_data = analyze_correlations(price_data, corr_tickers)
    print(f"      {len(etf_analysis)} analyzed, {len(sector_results)} sectors ranked")

    print("  [4] Enhanced indicators...")
    try:
        breadth = compute_market_breadth(etf_analysis)
        enhanced = {
            "contrarian_scores": compute_contrarian_scores(etf_analysis),
            "mean_reversion": compute_mean_reversion(etf_analysis),
            "vol_adj_momentum": compute_vol_adj_momentum(etf_analysis),
            "sector_rs": compute_sector_rs(etf_analysis, list(sector_map.keys())),
            "dividend_spread": compute_dividend_spread(
                etf_analysis,
                list(etf_groups.get("dividend", {}).keys()),
                market_indicators,
            ),
            "market_regime": compute_market_regime(market_indicators, breadth),
            "market_breadth": breadth,
            "macro_calendar": [],
        }
    except Exception as e:
        print(f"      [!] enhanced fail: {e}")
        enhanced = {}

    print("  [5] Formatting raw text...")
    analysis_text = format_analysis_data(
        etf_analysis, etf_groups, sector_results,
        correlation_data, market_indicators, [],
    )
    enhanced_text = format_enhanced_data(enhanced, ticker_names) if enhanced else ""
    full_text = f"{analysis_text}\n\n{enhanced_text}"

    stamp = analysis_date.strftime("%Y-%m-%d")
    raw_path = Path("reports") / f"Backtest_Raw_{stamp}.md"
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_text(f"# ETF Raw Data — {stamp}\n\n{full_text}", encoding="utf-8")
    print(f"      Raw data: {raw_path}")

    print("  [6] Generating report via Claude Code CLI (with evaluator)...")
    gen_path = generate_via_claude_code(str(raw_path), output_dir="reports", run_eval=True)
    # Move to dated filename (replace overwrites on Windows, unlike rename)
    hist_report_path = Path("reports") / f"Backtest_Report_{stamp}.md"
    Path(gen_path).replace(hist_report_path)
    print(f"      Report: {hist_report_path}")

    print("  [7] Extracting recommendations via LLM...")
    report_text = hist_report_path.read_text(encoding="utf-8")
    recs = extract_recommendations(report_text)
    print(
        f"      BUY={len(recs['buy'])}  AVOID={len(recs['avoid'])}  "
        f"SENTIMENT={len(recs['sentiment_only'])}"
    )
    print(f"      BUY list: {recs['buy']}")

    print("  [8] Fetching 2025 verify prices...")
    start_prices = {t: etf_analysis[t]["price"] for t in etf_analysis}
    end_prices = fetch_verify_prices(tickers, verify_date)

    def score_list(lst):
        out = []
        for t in lst:
            if t in start_prices and t in end_prices:
                p0, p1 = start_prices[t], end_prices[t]
                if p0:
                    out.append({"ticker": t, "return": round((p1 - p0) / p0 * 100, 2)})
        return out

    buy_ret = score_list(recs["buy"])
    avoid_ret = score_list(recs["avoid"])
    senti_ret = score_list(recs["sentiment_only"])

    all_returns = []
    for t in etf_analysis:
        if t in end_prices and start_prices.get(t):
            all_returns.append((end_prices[t] - start_prices[t]) / start_prices[t] * 100)
    all_returns.sort()
    median = all_returns[len(all_returns) // 2] if all_returns else 0.0
    mean = sum(all_returns) / len(all_returns) if all_returns else 0.0

    buy_avg = sum(r["return"] for r in buy_ret) / len(buy_ret) if buy_ret else 0.0
    avoid_avg = sum(r["return"] for r in avoid_ret) / len(avoid_ret) if avoid_ret else 0.0
    senti_avg = sum(r["return"] for r in senti_ret) / len(senti_ret) if senti_ret else 0.0
    buy_positive = sum(1 for r in buy_ret if r["return"] > 0)

    elapsed = time.time() - start_t
    print(f"  [9] Scores ({elapsed:.0f}s):")
    print(f"      Market median: {median:+.2f}%  mean: {mean:+.2f}%")
    print(f"      BUY avg:       {buy_avg:+.2f}%  ({buy_positive}/{len(buy_ret)} positive)")
    print(f"      Alpha vs median: {buy_avg - median:+.2f}pp")
    if avoid_ret:
        print(f"      AVOID avg:     {avoid_avg:+.2f}%")
    if senti_ret:
        print(f"      SENTIMENT avg: {senti_avg:+.2f}% (should NOT have been bought)")

    return {
        "analysis_date": analysis_date,
        "verify_date": verify_date,
        "horizon_y": round((verify_date - analysis_date).days / 365, 1),
        "report_path": str(hist_report_path),
        "recs": recs,
        "buy_ret": buy_ret,
        "avoid_ret": avoid_ret,
        "senti_ret": senti_ret,
        "market_median": median,
        "market_mean": mean,
        "buy_avg": buy_avg,
        "avoid_avg": avoid_avg,
        "senti_avg": senti_avg,
        "buy_positive": buy_positive,
        "elapsed_s": round(elapsed, 1),
    }


def write_report(results):
    lines = [
        "# Report-Level Backtest (Full Pipeline)",
        "",
        f"**Verify Date**: {VERIFY_DATE:%Y-%m-%d}",
        f"**Analysis Dates**: {', '.join(d.strftime('%Y-%m-%d') for d in ANALYSIS_DATES)}",
        "",
        "이 백테스트는 현재 파이프라인(데이터 수집 → 분석 → Claude 보고서 생성 → evaluator-optimizer)을 "
        "과거 시점 데이터로 실행해, 생성된 **보고서의 실제 추천 종목이 alpha를 냈는지** 측정합니다. "
        "`backtest_multi_year.py`가 시그널 단 정확도를 본 것과 달리, 여기서는 최종 유저가 받는 "
        "보고서 품질을 직접 평가합니다.",
        "",
        "## 1. Alpha Summary",
        "",
        "| 분석 시점 | Horizon | BUY수 | BUY 평균 | 시장 중앙값 | 시장 평균 | **Alpha** | AVOID 평균 | BUY-AVOID |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in results:
        alpha = r["buy_avg"] - r["market_median"]
        gap = r["buy_avg"] - r["avoid_avg"] if r["avoid_ret"] else 0
        avoid_cell = f"{r['avoid_avg']:+.2f}% ({len(r['avoid_ret'])})" if r["avoid_ret"] else "—"
        lines.append(
            f"| {r['analysis_date']:%Y-%m-%d} | {r['horizon_y']}Y | "
            f"{len(r['buy_ret'])} | **{r['buy_avg']:+.2f}%** | "
            f"{r['market_median']:+.2f}% | {r['market_mean']:+.2f}% | "
            f"**{alpha:+.2f}pp** | {avoid_cell} | "
            f"{gap:+.2f}pp |"
        )

    lines.append("")
    lines.append("## 2. Recommendation Detail per Date")
    lines.append("")
    for r in results:
        lines.append(f"### {r['analysis_date']:%Y-%m-%d}  ({r['horizon_y']}Y forward)")
        lines.append("")
        lines.append(f"**Report**: `{r['report_path']}`  |  **Generation time**: {r['elapsed_s']}s")
        lines.append("")
        lines.append("**BUY picks** (report가 명시적으로 편입 추천한 종목)")
        lines.append("")
        if r["buy_ret"]:
            lines.append("| Ticker | Return |")
            lines.append("|---|---|")
            for item in sorted(r["buy_ret"], key=lambda x: x["return"], reverse=True):
                marker = "✓" if item["return"] > r["market_median"] else "✗"
                lines.append(f"| {item['ticker']} | **{item['return']:+.2f}%** {marker} |")
        else:
            lines.append("_(none)_")
        lines.append("")

        if r["avoid_ret"]:
            lines.append("**AVOID picks** (회피/축소 권고)")
            lines.append("")
            lines.append("| Ticker | Return |")
            lines.append("|---|---|")
            for item in sorted(r["avoid_ret"], key=lambda x: x["return"]):
                marker = "✓" if item["return"] < r["market_median"] else "✗"
                lines.append(f"| {item['ticker']} | **{item['return']:+.2f}%** {marker} |")
            lines.append("")

        if r["senti_ret"]:
            lines.append(
                f"**Sentiment-only ETFs mentioned**: "
                f"{', '.join(r['recs']['sentiment_only'])} "
                f"(Hard Rule #1 — must NOT appear in BUY; "
                f"평균 성과 {r['senti_avg']:+.2f}%)"
            )
            lines.append("")

    lines.append("## 3. Cross-Horizon 해석")
    lines.append("")
    alphas = [r["buy_avg"] - r["market_median"] for r in results]
    if alphas:
        positive = sum(1 for a in alphas if a > 0)
        lines.append(f"- **평균 Alpha**: {sum(alphas) / len(alphas):+.2f}pp")
        lines.append(f"- **Alpha 양수 시점**: {positive}/{len(alphas)}")
        lines.append(f"- **Best Alpha**: {max(alphas):+.2f}pp")
        lines.append(f"- **Worst Alpha**: {min(alphas):+.2f}pp")
    lines.append("")
    lines.append("### 해석 가이드")
    lines.append("- **Alpha > 0**: 보고서의 BUY 추천이 시장 중앙값보다 앞섬 → 파이프라인이 가치 생성")
    lines.append("- **Alpha < 0**: 보고서가 시장 평균 이하를 추천 → 개선 필요")
    lines.append("- **BUY-AVOID gap이 양수**: 회피 판단도 유효")
    lines.append("- **Sentiment-only 종목이 BUY에 없음**: Hard Rule #1이 실제로 작동")

    out_path = Path("reports") / "Backtest_Reports_FullPipeline.md"
    out_path.write_text("\n".join(lines), encoding="utf-8")
    return out_path


def main():
    print("=" * 60)
    print("  Report-Level Backtest (Full Pipeline)")
    print(f"  Dates: {', '.join(d.strftime('%Y-%m-%d') for d in ANALYSIS_DATES)}")
    print(f"  Verify: {VERIFY_DATE:%Y-%m-%d}")
    print("  Estimated time: ~10-12 minutes")
    print("=" * 60)

    config = load_config()
    results = []
    for d in ANALYSIS_DATES:
        try:
            r = run_one(d, VERIFY_DATE, config)
            results.append(r)
        except Exception as e:
            import traceback
            print(f"  [ERROR] {d:%Y-%m-%d} failed: {e}")
            traceback.print_exc()

    if not results:
        print("\n[ERROR] no results produced")
        return

    out_path = write_report(results)
    print(f"\n{'=' * 60}")
    print("  SUMMARY")
    for r in results:
        alpha = r["buy_avg"] - r["market_median"]
        print(
            f"  {r['analysis_date']:%Y-%m-%d} ({r['horizon_y']}Y): "
            f"BUY={r['buy_avg']:+.2f}%  Median={r['market_median']:+.2f}%  "
            f"Alpha={alpha:+.2f}pp  (n={len(r['buy_ret'])})"
        )
    print(f"\n  Full report: {out_path}")
    print("=" * 60)


if __name__ == "__main__":
    main()
