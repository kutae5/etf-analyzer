#!/usr/bin/env python3
"""
ETF Market Analysis System
미국 ETF 시장 분석 및 Claude AI 보고서 생성

Usage:
    python run.py                          # Single-agent 분석 (기본)
    python run.py --multi                  # Multi-agent 분석 (5 전문가 병렬)
    python run.py --etfs SPY QQQ IWM       # 특정 ETF만 분석
    python run.py --period 6mo             # 분석 기간 변경
    python run.py --no-ai                  # AI 보고서 없이 데이터만 수집
    python run.py --model claude-opus-4-6  # Claude 모델 변경
"""

import argparse
import asyncio
import sys
import time
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import yaml

from src.fetcher import fetch_etf_prices, fetch_market_indicators, fetch_news
from src.analyzer import analyze_etf, analyze_sectors, analyze_correlations
from src.reporter import (
    format_analysis_data, generate_report, generate_via_claude_code,
    run_multi_agent_via_cli, save_report,
)
from src.enhanced import (
    compute_contrarian_scores, compute_mean_reversion, compute_vol_adj_momentum,
    compute_sector_rs, compute_dividend_spread, compute_market_regime,
    compute_market_breadth, get_macro_calendar, format_enhanced_data,
)
from src.portfolio import (
    compute_allocation, format_allocation,
    compute_dca_timing, format_dca_plan,
)


def load_config(path: str = "config.yaml") -> dict:
    config_path = Path(path)
    if not config_path.exists():
        print(f"[ERROR] config not found: {path}")
        sys.exit(1)
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def get_all_tickers(config: dict) -> tuple[list[str], dict[str, str]]:
    """설정에서 모든 ETF 티커와 이름 맵을 추출."""
    ticker_names: dict[str, str] = {}
    for group in config.get("etfs", {}).values():
        if isinstance(group, dict):
            ticker_names.update(group)
    return list(ticker_names.keys()), ticker_names


def main():
    parser = argparse.ArgumentParser(
        description="ETF Market Analysis System"
    )
    parser.add_argument(
        "--multi", action="store_true",
        help="Multi-agent mode via API (5 specialists + synthesizer)",
    )
    parser.add_argument(
        "--cc", action="store_true",
        help="Use Claude Code CLI (Max plan, no API credits needed)",
    )
    parser.add_argument(
        "--etfs", nargs="+", default=None,
        help="Specific ETF tickers (e.g. SPY QQQ IWM)",
    )
    parser.add_argument(
        "--period", default=None,
        help="Data period (e.g. 6mo, 1y, 2y)",
    )
    parser.add_argument(
        "--no-ai", action="store_true",
        help="Skip AI report, save raw data only",
    )
    parser.add_argument(
        "--model", default=None,
        help="Claude model (e.g. claude-opus-4-6)",
    )
    parser.add_argument(
        "--config", default="config.yaml",
        help="Config file path",
    )
    parser.add_argument(
        "--no-eval", action="store_true",
        help="Skip evaluator-optimizer loop (faster, no fact-check)",
    )
    parser.add_argument(
        "--capital", type=float, default=10_000_000,
        help="투자 가능 금액 (기본: 10,000,000원)",
    )
    parser.add_argument(
        "--monthly", type=float, default=None,
        help="월 적립금 (예: 3000000). DCA 타이밍 모드 활성화.",
    )
    parser.add_argument(
        "--cash-reserve", type=float, default=0,
        help="기존 축적 현금 (--monthly와 함께 사용)",
    )
    parser.add_argument(
        "--risk-profile", choices=["conservative", "moderate", "aggressive"],
        default="moderate",
        help="리스크 프로필 (기본: moderate)",
    )
    args = parser.parse_args()

    # ── Config ──
    config = load_config(args.config)
    period = args.period or config.get("analysis", {}).get("period", "1y")
    if args.model:
        config.setdefault("claude", {})["model"] = args.model

    # ── Tickers ──
    all_tickers, ticker_names = get_all_tickers(config)
    if args.etfs:
        tickers_to_analyze = [t.upper() for t in args.etfs]
        for t in tickers_to_analyze:
            if t not in ticker_names:
                ticker_names[t] = t
    else:
        tickers_to_analyze = all_tickers

    if args.cc and args.multi:
        mode_label = "Claude-Code Multi-Agent"
    elif args.cc:
        mode_label = "Claude-Code"
    elif args.multi:
        mode_label = "Multi-Agent (API)"
    else:
        mode_label = "Single-Agent (API)"
    print("=" * 55)
    print(f"  ETF Market Analysis System [{mode_label}]")
    print(f"  {len(tickers_to_analyze)} ETFs | Period: {period}")
    print("=" * 55)
    start_time = time.time()

    # ── Step 1: Data Collection ──
    print("\n[1/5] Fetching ETF price data")
    price_data = fetch_etf_prices(tickers_to_analyze, period=period)

    print("\n[2/5] Fetching market indicators")
    market_indicators = fetch_market_indicators(config.get("market_indicators", {}))
    print(f"  {len(market_indicators)} indicators collected")

    print("\n[3/5] Fetching news")
    news_count = config.get("analysis", {}).get("news_count", 25)
    news = fetch_news(count=news_count)

    # ── Step 2: Analysis ──
    print("\n[4/5] Running technical analysis")
    etf_analysis = {}
    for ticker in tickers_to_analyze:
        if ticker in price_data:
            etf_analysis[ticker] = analyze_etf(ticker, price_data[ticker])
    print(f"  {len(etf_analysis)} ETFs analyzed")

    sector_map = config.get("etfs", {}).get("sectors", {})
    sector_results = analyze_sectors(price_data, sector_map)
    print(f"  {len(sector_results)} sectors ranked")

    corr_tickers = list(config.get("etfs", {}).get("indices", {}).keys())
    corr_tickers += list(sector_map.keys())
    for key_etf in ["TLT", "GLD", "EEM", "SCHD", "SMH", "IBIT"]:
        if key_etf in price_data and key_etf not in corr_tickers:
            corr_tickers.append(key_etf)
    correlation_data = analyze_correlations(price_data, corr_tickers)
    print(f"  Correlation: {len(correlation_data.get('pairs', []))} pairs")

    # Enhanced indicators (backtest-validated)
    print("  Computing enhanced indicators...")
    breadth = compute_market_breadth(etf_analysis)
    enhanced = {
        "contrarian_scores": compute_contrarian_scores(etf_analysis),
        "mean_reversion": compute_mean_reversion(etf_analysis),
        "vol_adj_momentum": compute_vol_adj_momentum(etf_analysis),
        "sector_rs": compute_sector_rs(etf_analysis, list(sector_map.keys())),
        "dividend_spread": compute_dividend_spread(
            etf_analysis,
            list(config.get("etfs", {}).get("dividend", {}).keys()),
            market_indicators,
        ),
        "market_regime": compute_market_regime(market_indicators, breadth),
        "market_breadth": breadth,
        "macro_calendar": get_macro_calendar(),
    }
    regime = enhanced["market_regime"]["regime"]
    cs_count = len(enhanced["contrarian_scores"])
    print(f"  Market Regime: {regime} | Contrarian signals: {cs_count}")

    # Portfolio allocation: auto-generate picks from contrarian + momentum signals
    print("  Computing portfolio allocation...")
    auto_picks = _build_auto_picks(enhanced, etf_analysis, ticker_names, config.get("etfs", {}))

    if args.monthly:
        # DCA mode: monthly investment with timing
        dca_result = compute_dca_timing(
            enhanced, etf_analysis,
            monthly_budget=args.monthly,
            cash_reserve=args.cash_reserve,
        )
        deploy = dca_result["deploy_amount"]
        portfolio_result = compute_allocation(
            auto_picks, etf_analysis,
            capital=deploy, profile=args.risk_profile,
        )
        portfolio_text = format_dca_plan(dca_result, portfolio_result)
        n_alloc = len(portfolio_result.get("allocations", []))
        print(f"  DCA Signal: {dca_result['signal']} ({dca_result['deploy_pct']}%) → {deploy:,.0f}원 투입")
        print(f"  Portfolio: {n_alloc} positions ({portfolio_result.get('profile', '?')})")
    else:
        portfolio_result = compute_allocation(
            auto_picks, etf_analysis,
            capital=args.capital, profile=args.risk_profile,
        )
        portfolio_text = format_allocation(portfolio_result)
        n_alloc = len(portfolio_result.get("allocations", []))
        print(f"  Portfolio: {n_alloc} positions ({portfolio_result.get('profile', '?')})")

    # ── Step 3: Report Generation ──
    print("\n[5/5] Generating report")
    etf_groups = config.get("etfs", {})

    # Build analysis text (shared across all modes)
    analysis_text = format_analysis_data(
        etf_analysis, etf_groups, sector_results,
        correlation_data, market_indicators, news,
    )
    enhanced_text = format_enhanced_data(enhanced, ticker_names)
    full_text = f"{analysis_text}\n\n{enhanced_text}\n\n{portfolio_text}"

    if args.no_ai:
        report_content = f"# ETF Market Analysis Data (Raw)\n\n{full_text}"
        filepath = save_report(report_content)
        print(f"  Raw data saved: {filepath}")

    elif args.cc and args.multi:
        # ── Claude Code CLI Multi-Agent Mode (Max plan, 5 parallel subprocesses) ──
        try:
            report_content = run_multi_agent_via_cli(
                etf_analysis=etf_analysis,
                etf_groups=etf_groups,
                sector_results=sector_results,
                correlation_data=correlation_data,
                market_indicators=market_indicators,
                news=news,
                run_eval=not args.no_eval,
                raw_data_text=full_text,
            )
            filepath = save_report(report_content)
            print(f"  Report saved: {filepath}")
        except Exception as e:
            print(f"\n  [ERROR] CLI multi-agent failed: {e}")
            _fallback_raw(full_text)
            return

    elif args.cc:
        # ── Claude Code CLI Single-Agent Mode (Max plan, no API credits) ──
        raw_content = f"# ETF Analysis Data\n\n{full_text}"
        raw_path = save_report(raw_content, output_dir="reports")
        print(f"  Raw data: {raw_path}")
        try:
            filepath = generate_via_claude_code(raw_path, run_eval=not args.no_eval)
            print(f"  AI Report: {filepath}")
        except Exception as e:
            print(f"\n  [ERROR] Claude Code failed: {e}")
            filepath = raw_path

    elif args.multi:
        # ── Multi-Agent Mode ──
        from src.agents import run_multi_agent
        try:
            report_content = asyncio.run(run_multi_agent(
                etf_analysis=etf_analysis,
                etf_groups=etf_groups,
                sector_results=sector_results,
                correlation_data=correlation_data,
                market_indicators=market_indicators,
                news=news,
                config=config,
            ))
            filepath = save_report(report_content)
            print(f"  Report saved: {filepath}")
        except Exception as e:
            print(f"\n  [ERROR] Multi-agent failed: {e}")
            _fallback_raw(full_text)
            return

    else:
        # ── Single-Agent Mode ──
        try:
            report_content = generate_report(full_text, config)
            filepath = save_report(report_content)
            print(f"  Report saved: {filepath}")
        except Exception as e:
            print(f"\n  [ERROR] AI report failed: {e}")
            report_content = f"# ETF Market Analysis Data (Raw)\n\n{full_text}"
            filepath = save_report(report_content)
            print(f"  Fallback raw data: {filepath}")

    elapsed = time.time() - start_time

    # ── Summary: save + console output ──
    summary_path = _save_summary(
        enhanced, portfolio_result,
        dca_result if args.monthly else None,
        ticker_names, filepath, elapsed,
    )

    print(f"\n{'=' * 55}")
    print(f"  Done! ({elapsed:.1f}s)")
    print(f"  Report:  {filepath}")
    print(f"  Summary: {summary_path}")
    print(f"{'=' * 55}")


def _build_auto_picks(
    enhanced: dict, etf_analysis: dict, ticker_names: dict,
    etf_groups: dict | None = None,
) -> list[dict]:
    """Enhanced 지표 기반 자동 추천 후보 생성 (섹터 분산 적용).

    Diversification rule: 같은 config 그룹에서 최대 2개까지만 선택.
    """
    picks = []
    seen = set()
    group_counts: dict[str, int] = {}  # group_name → count
    MAX_PER_GROUP = 2
    leveraged_group = {"TQQQ", "SQQQ", "UPRO", "SOXL", "TMF", "UVXY"}

    # Build reverse map: ticker → group_name
    ticker_to_group: dict[str, str] = {}
    if etf_groups:
        for grp_name, grp_tickers in etf_groups.items():
            if isinstance(grp_tickers, dict):
                for t in grp_tickers:
                    ticker_to_group[t] = grp_name

    def _can_add(ticker: str) -> bool:
        if ticker in seen or ticker in leveraged_group:
            return False
        grp = ticker_to_group.get(ticker, "unknown")
        return group_counts.get(grp, 0) < MAX_PER_GROUP

    def _add_pick(ticker: str, conviction: str):
        grp = ticker_to_group.get(ticker, "unknown")
        picks.append({
            "ticker": ticker, "name": ticker_names.get(ticker, ticker),
            "conviction": conviction, "group": grp,
        })
        seen.add(ticker)
        group_counts[grp] = group_counts.get(grp, 0) + 1

    # 1. Contrarian Strong Buy → High conviction
    for ticker, data in enhanced.get("contrarian_scores", {}).items():
        if not _can_add(ticker):
            continue
        if data["level"] == "Strong Buy" and len([p for p in picks if p["conviction"] == "High"]) < 3:
            _add_pick(ticker, "High")

    # 2. Contrarian Buy → Medium conviction
    for ticker, data in enhanced.get("contrarian_scores", {}).items():
        if not _can_add(ticker):
            continue
        if data["level"] == "Buy" and len([p for p in picks if p["conviction"] == "Medium"]) < 4:
            _add_pick(ticker, "Medium")

    # 3. Vol-adjusted momentum top → Medium/Low (diversified)
    for item in enhanced.get("vol_adj_momentum", [])[:20]:
        ticker = item["ticker"]
        if not _can_add(ticker):
            continue
        a = etf_analysis.get(ticker, {})
        if a.get("adx") and a["adx"] >= 25 and item["momentum_score"] > 0.3:
            conv = "Medium" if item["momentum_score"] > 0.5 else "Low"
            if len(picks) < 8:
                _add_pick(ticker, conv)

    # 4. Fill remaining slots from mean-reversion
    if len(picks) < 5:
        for item in enhanced.get("mean_reversion", []):
            ticker = item["ticker"]
            if not _can_add(ticker):
                continue
            if len(picks) < 6:
                _add_pick(ticker, "Low")

    return picks


def _save_summary(
    enhanced: dict,
    portfolio_result: dict,
    dca_result: dict | None,
    ticker_names: dict,
    report_path: str,
    elapsed: float,
) -> str:
    """요약본 생성: 콘솔 출력 + MD 파일 저장."""
    from datetime import datetime
    now = datetime.now()
    lines = []

    lines.append(f"# ETF 투자 요약 — {now.strftime('%Y-%m-%d')}\n")

    # Market regime
    regime = enhanced.get("market_regime", {})
    breadth = enhanced.get("market_breadth", {})
    lines.append("## 시장 상태\n")
    lines.append(f"- **레짐**: {regime.get('regime', 'N/A')}")
    lines.append(f"- **Breadth**: {breadth.get('above_200sma_pct', 'N/A')}% above 200SMA ({breadth.get('health', 'N/A')})")
    lines.append(f"- **Golden Cross**: {breadth.get('golden_cross', 0)}개 | **Death Cross**: {breadth.get('death_cross', 0)}개")
    for k, v in regime.get("signals", {}).items():
        lines.append(f"- {k}: {v}")

    # DCA timing
    if dca_result:
        lines.append(f"\n## 이번 달 투자 판단\n")
        lines.append(f"- **시그널**: {dca_result['signal']}")
        lines.append(f"- **투입**: {dca_result['deploy_amount']:,.0f}원 / {dca_result['monthly_budget']:,.0f}원 ({dca_result['deploy_pct']}%)")
        if dca_result.get("save_amount", 0) > 0:
            lines.append(f"- **현금 축적**: {dca_result['save_amount']:,.0f}원")
        lines.append(f"- **근거**: {' | '.join(dca_result['reasoning'])}")

    # Portfolio
    allocs = portfolio_result.get("allocations", [])
    summary = portfolio_result.get("summary", {})
    if allocs:
        deploy_amt = dca_result["deploy_amount"] if dca_result else summary.get("investable", 0)
        lines.append(f"\n## 매수 계획\n")
        lines.append("| ETF | Conviction | 비중 | 금액 | 손절 | 익절 |")
        lines.append("|-----|------------|------|------|------|------|")
        for a in allocs:
            amt = round(deploy_amt * a["pct"] / 100) if deploy_amt else a.get("amount", 0)
            sl = f"{a['stop_loss_pct']}%" if a.get("stop_loss_pct") is not None else "-"
            tp = f"+{a['take_profit_pct']}%" if a.get("take_profit_pct") is not None else "-"
            lines.append(f"| **{a['ticker']}** ({a['name']}) | {a['conviction']} | {a['pct']}% | {amt:,.0f}원 | {sl} | {tp} |")

    # Risk
    if summary.get("portfolio_vol"):
        lines.append(f"\n## 리스크\n")
        lines.append(f"- 포트폴리오 변동성: {summary['portfolio_vol']:.1f}%")
        lines.append(f"- 예상 최대 낙폭: {summary['est_max_drawdown']:.1f}%")
        lines.append(f"- 섹터 분산: {summary.get('num_sectors', '?')}개 그룹")

    lines.append(f"\n---\n")
    lines.append(f"*Generated: {now.strftime('%Y-%m-%d %H:%M')} ({elapsed:.1f}s) | Full report: {report_path}*")
    lines.append(f"\n> 본 요약은 투자 권유가 아닙니다.")

    content = "\n".join(lines)

    # Save
    out_dir = Path("reports")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"Summary_{now.strftime('%Y-%m-%d')}.md"
    out_path.write_text(content, encoding="utf-8")

    # Console output
    print(f"\n{'=' * 55}")
    print(content)

    return str(out_path)


def _fallback_raw(full_text):
    report_content = f"# ETF Market Analysis Data (Raw)\n\n{full_text}"
    filepath = save_report(report_content)
    print(f"  Fallback raw data: {filepath}")


if __name__ == "__main__":
    main()
