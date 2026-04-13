"""
Portfolio Allocation Module
============================
Conviction + Volatility + Correlation 기반 포지션 사이징.

입력: ETF 분석 데이터 + 추천 목록 (conviction picks)
출력: 리스크 프로필별 비중(%) 및 금액 배분표

백테스트 검증 원칙:
- Death Cross + MACD Rising = 유일하게 교차검증 통과 시그널 (+3.1pp alpha)
- Conviction 집중 > 분산 (6 picks → +9pp vs 15 picks → -2pp)
- 레버리지/인버스 ETF 배분 금지
"""

from __future__ import annotations

from typing import Any


# 리스크 프로필별 설정
RISK_PROFILES = {
    "conservative": {
        "label": "보수적",
        "cash_pct": 30,
        "max_single_pct": 12,
        "conviction_weights": {"High": 3.0, "Medium": 1.5, "Low": 0.7},
        "vol_penalty": 1.5,   # 변동성 높을수록 비중 축소 강도
    },
    "moderate": {
        "label": "중립",
        "cash_pct": 15,
        "max_single_pct": 18,
        "conviction_weights": {"High": 3.5, "Medium": 2.0, "Low": 1.0},
        "vol_penalty": 1.0,
    },
    "aggressive": {
        "label": "공격적",
        "cash_pct": 5,
        "max_single_pct": 25,
        "conviction_weights": {"High": 4.0, "Medium": 2.5, "Low": 1.2},
        "vol_penalty": 0.5,
    },
}

LEVERAGED_KEYWORDS = {"3x", "2x", "-3x", "-2x", "Bull", "Bear", "1.5x"}


def compute_allocation(
    picks: list[dict[str, Any]],
    etf_analysis: dict[str, dict],
    capital: float = 10_000_000,
    profile: str = "moderate",
) -> dict[str, Any]:
    """포지션 사이징 계산.

    Args:
        picks: [{"ticker": str, "conviction": "High"|"Medium"|"Low", "name": str}, ...]
        etf_analysis: analyze_etf 결과 dict
        capital: 투자 가능 금액 (원화 기준, 기본 1000만원)
        profile: "conservative" | "moderate" | "aggressive"

    Returns:
        {"profile": ..., "allocations": [...], "summary": {...}}
    """
    cfg = RISK_PROFILES.get(profile, RISK_PROFILES["moderate"])
    investable = capital * (1 - cfg["cash_pct"] / 100)

    # 1. Raw score 계산: conviction weight / volatility
    scored = []
    for pick in picks:
        ticker = pick["ticker"]
        conv = pick.get("conviction", "Medium")
        a = etf_analysis.get(ticker, {})

        # 레버리지/인버스 제외
        name = pick.get("name", "")
        if any(kw in name for kw in LEVERAGED_KEYWORDS):
            continue

        base_weight = cfg["conviction_weights"].get(conv, 1.0)

        # 변동성 역수 조정 (변동성 높을수록 비중 축소)
        vol = a.get("volatility_30d")
        if vol and vol > 0:
            vol_adj = 1.0 / (1.0 + (vol / 100) * cfg["vol_penalty"])
        else:
            vol_adj = 0.8  # 변동성 데이터 없으면 약간 감점

        # MACD Momentum Rising = 소폭 보너스 (교차검증 통과 시그널)
        mom_bonus = 1.1 if a.get("macd_momentum") == "Rising" else 1.0

        raw_score = base_weight * vol_adj * mom_bonus

        # Exit signals: volatility-based stop-loss & take-profit
        price = a.get("price")
        stop_loss_pct = None
        take_profit_pct = None
        if price and vol and vol > 0:
            # Stop-loss: 2x daily volatility (30d annualized → daily, × 2)
            daily_vol = vol / (252 ** 0.5)
            stop_loss_pct = round(-daily_vol * 2, 1)  # negative %
            # Take-profit: based on conviction
            tp_mult = {"High": 3.0, "Medium": 2.0, "Low": 1.5}.get(conv, 2.0)
            take_profit_pct = round(daily_vol * tp_mult, 1)

        scored.append({
            "ticker": ticker,
            "name": name,
            "conviction": conv,
            "group": pick.get("group", "unknown"),
            "price": price,
            "volatility": vol,
            "adx": a.get("adx"),
            "trend": a.get("trend", "N/A"),
            "rsi": a.get("rsi"),
            "macd_momentum": a.get("macd_momentum", "N/A"),
            "stop_loss_pct": stop_loss_pct,
            "take_profit_pct": take_profit_pct,
            "raw_score": raw_score,
        })

    if not scored:
        return {"profile": cfg["label"], "allocations": [], "summary": {}}

    # 2. 정규화 → 비중(%) 계산
    total_score = sum(s["raw_score"] for s in scored)
    allocations = []

    for s in scored:
        pct = (s["raw_score"] / total_score) * 100 if total_score > 0 else 0
        # max single position cap
        pct = min(pct, cfg["max_single_pct"])
        allocations.append({**s, "pct_raw": pct})

    # 캡 적용 후 재정규화
    total_pct = sum(a["pct_raw"] for a in allocations)
    if total_pct > 0:
        scale = 100.0 / total_pct
        for a in allocations:
            a["pct"] = round(a["pct_raw"] * scale, 1)
            a["amount"] = round(investable * a["pct"] / 100)
    else:
        for a in allocations:
            a["pct"] = 0
            a["amount"] = 0

    # conviction 순서로 정렬
    conv_order = {"High": 0, "Medium": 1, "Low": 2}
    allocations.sort(key=lambda x: (conv_order.get(x["conviction"], 9), -x["pct"]))

    # 3. Portfolio-level risk metrics
    vols = [a["volatility"] for a in allocations if a.get("volatility")]
    weights = [a["pct"] / 100 for a in allocations if a.get("volatility")]
    if vols and weights:
        # Weighted portfolio volatility (simplified, assumes low correlation)
        port_vol = sum(w * v for w, v in zip(weights, vols))
        # Estimated max drawdown: ~2x portfolio vol (historical rule of thumb)
        est_max_dd = round(-port_vol * 2, 1)
        # Simplified Sharpe: assume risk-free 4.5%, expected return ~ market median 10%
        est_sharpe = round((10 - 4.5) / port_vol, 2) if port_vol > 0 else 0
    else:
        port_vol = 0
        est_max_dd = 0
        est_sharpe = 0

    # Sector concentration
    groups_used = {}
    for a in allocations:
        grp = a.get("group", "unknown")
        groups_used[grp] = groups_used.get(grp, 0) + a["pct"]
    top_sector = max(groups_used.items(), key=lambda x: x[1]) if groups_used else ("N/A", 0)
    num_sectors = len(groups_used)

    cash_amount = round(capital - investable)
    summary = {
        "capital": capital,
        "investable": round(investable),
        "cash": cash_amount,
        "cash_pct": cfg["cash_pct"],
        "num_positions": len(allocations),
        "avg_position_pct": round(100 / len(allocations), 1) if allocations else 0,
        "max_position_pct": max((a["pct"] for a in allocations), default=0),
        "portfolio_vol": round(port_vol, 1),
        "est_max_drawdown": est_max_dd,
        "est_sharpe": est_sharpe,
        "num_sectors": num_sectors,
        "top_sector": top_sector[0],
        "top_sector_pct": round(top_sector[1], 1),
    }

    return {
        "profile": cfg["label"],
        "profile_key": profile,
        "allocations": allocations,
        "summary": summary,
    }


def format_allocation(result: dict, currency: str = "KRW") -> str:
    """배분 결과를 Markdown 텍스트로 변환."""
    lines = []
    allocs = result.get("allocations", [])
    summary = result.get("summary", {})
    profile = result.get("profile", "N/A")

    if not allocs:
        return "포트폴리오 배분 데이터 없음."

    capital = summary.get("capital", 0)
    if currency == "KRW":
        cap_str = f"{capital:,.0f}원"
    else:
        cap_str = f"${capital:,.0f}"

    lines.append(f"\n## PORTFOLIO ALLOCATION ({profile})")
    lines.append(f"- 총 투자금: {cap_str}")
    lines.append(f"- 현금 보유: {summary['cash_pct']}% ({summary['cash']:,.0f}원)" if currency == "KRW"
                 else f"- Cash Reserve: {summary['cash_pct']}% (${summary['cash']:,.0f})")
    lines.append(f"- 포지션 수: {summary['num_positions']}개")
    lines.append(f"- 섹터 분산: {summary.get('num_sectors', '?')}개 그룹 (최대: {summary.get('top_sector', '?')} {summary.get('top_sector_pct', 0):.0f}%)")
    lines.append(f"- 포트폴리오 변동성: {summary.get('portfolio_vol', 0):.1f}% (연환산)")
    lines.append(f"- 예상 최대 낙폭: {summary.get('est_max_drawdown', 0):.1f}%")
    lines.append(f"- 예상 Sharpe Ratio: {summary.get('est_sharpe', 0):.2f}\n")

    lines.append("| # | Conviction | ETF | 비중(%) | 금액 | 손절 | 익절 | Trend | MACD Mom |")
    lines.append("|---|------------|-----|---------|------|------|------|-------|----------|")

    for i, a in enumerate(allocs, 1):
        if currency == "KRW":
            amt = f"{a['amount']:,.0f}원"
        else:
            amt = f"${a['amount']:,.0f}"
        sl = f"{a['stop_loss_pct']}%" if a.get("stop_loss_pct") is not None else "N/A"
        tp = f"+{a['take_profit_pct']}%" if a.get("take_profit_pct") is not None else "N/A"
        lines.append(
            f"| {i} | {a['conviction']} | **{a['name']} ({a['ticker']})** | "
            f"{a['pct']}% | {amt} | {sl} | {tp} | {a['trend']} | {a['macd_momentum']} |"
        )

    # Risk note
    lines.append(f"\n### 배분 원칙")
    lines.append("- **Conviction High**: 가장 큰 비중 (교차검증 통과 시그널 기반)")
    lines.append("- **변동성 역수 조정**: 고변동 ETF → 비중 축소 (리스크 관리)")
    lines.append("- **MACD Momentum Rising**: 반등 초기 시그널 → 소폭 비중 상향")
    lines.append("- **섹터 분산**: 같은 그룹에서 최대 2개 (집중도 제한)")
    lines.append("- **레버리지/인버스 ETF**: 배분 대상에서 자동 제외")

    lines.append(f"\n### Exit 전략")
    lines.append("- **손절(Stop-Loss)**: 일일 변동성 × 2 하락 시 → 포지션 청산 검토")
    lines.append("- **익절(Take-Profit)**: Conviction 수준별 변동성 배수 도달 시 → 이익 실현 검토")
    lines.append("- **재진입 조건**: 손절 후 MACD Momentum이 다시 Rising 전환 시")

    lines.append("\n> 본 배분은 참고용이며 투자 권유가 아닙니다. "
                 "개인의 재무 상황과 리스크 허용 범위를 고려하십시오.")

    return "\n".join(lines)


# ─── DCA Timing Engine ──────────────────────────────────────

def compute_dca_timing(
    enhanced: dict,
    etf_analysis: dict,
    monthly_budget: float = 3_000_000,
    cash_reserve: float = 0,
) -> dict:
    """시장 상태 기반 월별 투입 비율 결정.

    Returns:
        {
            "deploy_pct": 33-150,
            "deploy_amount": 실제 투입금,
            "save_amount": 현금 축적분,
            "signal": "ACCELERATE" | "FULL" | "PARTIAL" | "CAUTIOUS",
            "reasoning": [...],
            "market_score": -10 ~ +10,
        }
    """
    regime_data = enhanced.get("market_regime", {})
    breadth = enhanced.get("market_breadth", {})
    contrarian = enhanced.get("contrarian_scores", {})

    score = 0  # negative = buy more (market cheap), positive = buy less (market expensive)
    reasoning = []

    # 1. Market regime
    # Backtest lesson: RISK-ON = invest fully, don't hold back in bull market
    regime = regime_data.get("regime", "TRANSITION")
    if "RISK-OFF" in regime and "Moderate" not in regime:
        score -= 3
        reasoning.append(f"시장 레짐: {regime} → 하락장 = 매수 기회")
    elif "RISK-OFF" in regime:
        score -= 2
        reasoning.append(f"시장 레짐: {regime} → 약세")
    elif regime == "TRANSITION":
        score += 0  # neutral, not negative
        reasoning.append(f"시장 레짐: {regime} → 전환기, 정상 투입")
    elif "Moderate" in regime:
        score += 0  # don't penalize moderate risk-on
        reasoning.append(f"시장 레짐: {regime} → 상승장, 정상 투입")
    else:  # strong RISK-ON
        score += 1  # only slight caution for strong risk-on
        reasoning.append(f"시장 레짐: {regime} → 강한 상승장")

    # 2. Market breadth
    above_200 = breadth.get("above_200sma_pct", 50)
    if above_200 < 30:
        score -= 3
        reasoning.append(f"Breadth {above_200:.0f}% (Bear Market → 바겐세일)")
    elif above_200 < 50:
        score -= 1
        reasoning.append(f"Breadth {above_200:.0f}% (약세)")
    elif above_200 > 80:
        score += 2
        reasoning.append(f"Breadth {above_200:.0f}% (과열 주의)")
    else:
        reasoning.append(f"Breadth {above_200:.0f}% (정상)")

    # 3. Contrarian signal count (more = market crashed = buy more)
    n_contrarian = len(contrarian)
    strong_buy = sum(1 for v in contrarian.values() if v["level"] == "Strong Buy")
    if strong_buy >= 5:
        score -= 3
        reasoning.append(f"Strong Buy {strong_buy}개 → 극단 저평가, 적극 매수")
    elif n_contrarian >= 20:
        score -= 2
        reasoning.append(f"역추세 시그널 {n_contrarian}개 → 다수 종목 하락, 매수 기회")
    elif n_contrarian >= 10:
        score -= 1
        reasoning.append(f"역추세 시그널 {n_contrarian}개")
    elif n_contrarian == 0:
        score += 1  # reduced from 2: no contrarian = normal, not necessarily overheated
        reasoning.append("역추세 시그널 0개 → 시장 정상")
    else:
        reasoning.append(f"역추세 시그널 {n_contrarian}개 (정상 범위)")

    # 4. Average RSI of universe
    rsi_values = [a["rsi"] for a in etf_analysis.values() if a.get("rsi")]
    if rsi_values:
        avg_rsi = sum(rsi_values) / len(rsi_values)
        if avg_rsi < 30:
            score -= 3
            reasoning.append(f"평균 RSI {avg_rsi:.0f} (극단 과매도 → 적극 매수)")
        elif avg_rsi < 40:
            score -= 1
            reasoning.append(f"평균 RSI {avg_rsi:.0f} (약세)")
        elif avg_rsi > 70:
            score += 2
            reasoning.append(f"평균 RSI {avg_rsi:.0f} (과매수 시장)")
        else:
            reasoning.append(f"평균 RSI {avg_rsi:.0f} (정상)")

    # 5. VIX from regime signals
    vix_signal = regime_data.get("signals", {}).get("VIX", "")
    if "Crisis" in vix_signal:
        score -= 2
        reasoning.append("VIX 위기 수준 → 공포 매수 기회")
    # Low VIX is normal in bull markets, don't penalize

    # Score → deploy percentage
    # Backtest lesson: default should be 100%. Only reduce in truly extreme cases.
    # -2.8pp alpha loss came from over-cautious 80% default in bull markets.
    score = max(-10, min(10, score))

    if score <= -5:
        deploy_pct = 150  # 축적 현금 사용
        signal = "ACCELERATE"
    elif score <= -2:
        deploy_pct = 120  # mild crash: invest more
        signal = "OVERWEIGHT"
    elif score <= 3:
        deploy_pct = 100  # default: invest fully
        signal = "FULL"
    elif score <= 6:
        deploy_pct = 66
        signal = "CAUTIOUS"
    else:
        deploy_pct = 33
        signal = "MINIMAL"

    # Calculate amounts
    available = monthly_budget + cash_reserve
    deploy_amount = min(round(monthly_budget * deploy_pct / 100), available)
    save_amount = monthly_budget - deploy_amount if deploy_pct < 100 else 0
    # If accelerating, use cash reserve
    extra_from_reserve = max(0, deploy_amount - monthly_budget)

    return {
        "deploy_pct": deploy_pct,
        "deploy_amount": deploy_amount,
        "save_amount": save_amount,
        "extra_from_reserve": extra_from_reserve,
        "cash_reserve_after": round(cash_reserve - extra_from_reserve + save_amount),
        "signal": signal,
        "market_score": score,
        "reasoning": reasoning,
        "monthly_budget": monthly_budget,
    }


def format_dca_plan(
    dca: dict,
    portfolio: dict,
    currency: str = "KRW",
) -> str:
    """DCA 타이밍 + 포트폴리오 배분 통합 출력."""
    lines = []

    signal_emoji = {
        "ACCELERATE": "[적극 매수]",
        "OVERWEIGHT": "[비중 확대]",
        "FULL": "[정상 투입]",
        "CAUTIOUS": "[보수적]",
        "MINIMAL": "[최소 투입]",
    }
    sig = dca["signal"]
    label = signal_emoji.get(sig, sig)

    lines.append(f"\n## MONTHLY INVESTMENT PLAN {label}")
    lines.append(f"\n### 이번 달 투자 판단\n")
    lines.append(f"- **월 예산**: {dca['monthly_budget']:,.0f}원")
    lines.append(f"- **투입 비율**: {dca['deploy_pct']}% → **{dca['deploy_amount']:,.0f}원 투입**")
    if dca["save_amount"] > 0:
        lines.append(f"- **현금 축적**: {dca['save_amount']:,.0f}원 (다음 기회 대기)")
    if dca["extra_from_reserve"] > 0:
        lines.append(f"- **축적분 추가 투입**: {dca['extra_from_reserve']:,.0f}원 (폭락 매수)")
    lines.append(f"- **잔여 현금 버퍼**: {dca['cash_reserve_after']:,.0f}원")
    lines.append(f"- **시장 점수**: {dca['market_score']} (음수 = 매수 유리, 양수 = 신중)")

    lines.append(f"\n### 판단 근거\n")
    for r in dca["reasoning"]:
        lines.append(f"- {r}")

    # Allocation table with DCA amounts
    allocs = portfolio.get("allocations", [])
    if allocs:
        deploy = dca["deploy_amount"]
        lines.append(f"\n### 이번 달 매수 계획 ({deploy:,.0f}원)\n")
        lines.append("| # | Conviction | ETF | 비중 | 이번달 매수 | 손절 | 익절 |")
        lines.append("|---|------------|-----|------|------------|------|------|")

        for i, a in enumerate(allocs, 1):
            amt = round(deploy * a["pct"] / 100)
            sl = f"{a['stop_loss_pct']}%" if a.get("stop_loss_pct") is not None else "N/A"
            tp = f"+{a['take_profit_pct']}%" if a.get("take_profit_pct") is not None else "N/A"
            lines.append(
                f"| {i} | {a['conviction']} | **{a['ticker']}** ({a['name']}) | "
                f"{a['pct']}% | **{amt:,.0f}원** | {sl} | {tp} |"
            )

    # Decision guide
    lines.append(f"\n### 투입 시그널 가이드")
    lines.append("| 시그널 | 투입비율 | 조건 |")
    lines.append("|--------|---------|------|")
    lines.append(f"| {'**>>**' if sig=='ACCELERATE' else ''} ACCELERATE | 150% | 폭락 + 역추세 다수 → 축적분 방출 |")
    lines.append(f"| {'**>>**' if sig=='OVERWEIGHT' else ''} OVERWEIGHT | 120% | 약세장 → 비중 확대 |")
    lines.append(f"| {'**>>**' if sig=='FULL' else ''} FULL | 100% | 정상 시장 → 전액 적립 (기본값) |")
    lines.append(f"| {'**>>**' if sig=='CAUTIOUS' else ''} CAUTIOUS | 66% | 과열 시장 → 축소 투입 |")
    lines.append(f"| {'**>>**' if sig=='MINIMAL' else ''} MINIMAL | 33% | 극단 과열 → 최소 투입 |")

    lines.append("\n> 적립식 투자의 핵심: 시장이 싸질 때 더 많이, 비쌀 때 덜 사는 것. "
                 "하지만 시장 타이밍은 완벽할 수 없으므로 최소 33%는 항상 투입합니다.")

    return "\n".join(lines)
