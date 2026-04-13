"""
Enhanced Analysis Indicators (5-Year Backtest Validated)
========================================================
8 new indicators based on 2021-2026 backtest findings.

1. Contrarian Score          - 극단적 약세 = 매수 기회 (backtest: Death Cross → avg +72%)
2. Mean Reversion Rank       - 3M 하위 종목 반등 후보 (backtest: 2/5년 승)
3. Vol-Adjusted Momentum     - 샤프비율 기반 모멘텀 (backtest: 3/5년 승)
4. Sector Relative Strength  - 섹터 vs SPY 상대강도
5. Dividend Yield Spread     - 배당 ETF vs 국채 매력도
6. Market Regime Detector    - Risk-On / Off / Transition 분류
7. Market Breadth            - 200일선 위 ETF 비율
8. Macro Calendar            - FOMC, 실적시즌 근접도
"""

import calendar
from datetime import datetime, timedelta
from typing import Any


# ─── 1. Contrarian Score ──────────────────────────────────────

def compute_contrarian_scores(
    etf_analysis: dict[str, dict],
) -> dict[str, dict[str, Any]]:
    """극단적 약세 시그널 복합 점수. 높을수록 역추세 매수 기회."""
    scores = {}
    for ticker, a in etf_analysis.items():
        score = 0
        reasons: list[str] = []

        rsi = a.get("rsi")
        if rsi is not None:
            if rsi < 25:
                score += 4; reasons.append(f"RSI {rsi:.0f} (극단적 과매도)")
            elif rsi < 30:
                score += 3; reasons.append(f"RSI {rsi:.0f} (과매도)")
            elif rsi < 40:
                score += 1; reasons.append(f"RSI {rsi:.0f} (약세)")

        trend = a.get("trend", "")
        if "Death Cross" in trend:
            score += 2; reasons.append("Death Cross")
        elif "Downtrend" in trend:
            score += 1; reasons.append("Downtrend")

        bb = str(a.get("bollinger_position", ""))
        if "Below Lower" in bb:
            score += 2; reasons.append("BB 하단 돌파")

        fh = a.get("from_52w_high", 0)
        if fh is not None:
            if fh < -30:
                score += 3; reasons.append(f"52W 고점 대비 {fh:.0f}%")
            elif fh < -20:
                score += 2; reasons.append(f"52W 고점 대비 {fh:.0f}%")
            elif fh < -10:
                score += 1

        if a.get("macd_crossover") == "Bearish":
            score += 1
            # Bearish MACD + Rising histogram = early reversal signal (bottom forming)
            if a.get("macd_momentum") == "Rising":
                score += 1
                reasons.append("MACD 히스토그램 상승 (바닥 반등 초기)")

        # ADX: trend strength context (cross-validated: ADX alone is NOT a reliable
        # alpha source, but adds context. DC+ADX>=25 was overfitted to one recovery period.)
        adx = a.get("adx")
        if adx is not None:
            if adx >= 25 and ("Death Cross" in trend or "Downtrend" in trend):
                score += 1  # reduced from 2: cross-validation showed -1.0pp avg alpha
                reasons.append(f"ADX {adx:.0f} (강한 추세)")

        score = max(0, min(score, 10))
        if score >= 3:
            level = "Strong Buy" if score >= 7 else ("Buy" if score >= 5 else "Watch")
            scores[ticker] = {"score": score, "level": level, "reasons": reasons}

    return dict(sorted(scores.items(), key=lambda x: x[1]["score"], reverse=True))


# ─── 2. Mean Reversion Candidates ────────────────────────────

def compute_mean_reversion(etf_analysis: dict) -> list[dict]:
    """3M 최저 수익률 종목 → 반등 후보 (backtest: 과매도 + 하락 = 반등 확률 높음)."""
    items = []
    for ticker, a in etf_analysis.items():
        ret_3m = a.get("returns", {}).get("3M")
        if ret_3m is None:
            continue
        items.append({
            "ticker": ticker,
            "return_3m": ret_3m,
            "rsi": a.get("rsi"),
            "trend": a.get("trend"),
            "macd_momentum": a.get("macd_momentum", "N/A"),
            "from_52w_high": a.get("from_52w_high"),
            "volatility": a.get("volatility_30d"),
        })
    items.sort(key=lambda x: x["return_3m"])
    return items[:15]


# ─── 3. Volatility-Adjusted Momentum ─────────────────────────

def compute_vol_adj_momentum(etf_analysis: dict) -> list[dict]:
    """3M 수익률 / 30D 변동성 = 위험조정 모멘텀 (샤프 유사)."""
    items = []
    for ticker, a in etf_analysis.items():
        ret = a.get("returns", {}).get("3M")
        vol = a.get("volatility_30d")
        if ret is not None and vol and vol > 0:
            score = round(ret / vol, 3)
            items.append({
                "ticker": ticker,
                "return_3m": ret,
                "volatility": vol,
                "momentum_score": score,
            })
    items.sort(key=lambda x: x["momentum_score"], reverse=True)
    return items


# ─── 4. Sector Relative Strength ─────────────────────────────

def compute_sector_rs(
    etf_analysis: dict, sector_tickers: list[str],
) -> list[dict]:
    """섹터 vs SPY 상대강도. RS > 0 = SPY 아웃퍼폼."""
    spy = etf_analysis.get("SPY", {}).get("returns", {})
    if not spy:
        return []

    results = []
    for ticker in sector_tickers:
        a = etf_analysis.get(ticker)
        if not a:
            continue
        sr = a.get("returns", {})
        rs = {}
        for p in ("1W", "1M", "3M", "6M"):
            s, b = sr.get(p), spy.get(p)
            if s is not None and b is not None:
                rs[p] = round(s - b, 2)

        # RS 추세: 개선 or 악화
        vals = [rs.get(p) for p in ("3M", "1M", "1W") if rs.get(p) is not None]
        trend = "Improving" if len(vals) >= 2 and vals[-1] > vals[0] else "Weakening"

        results.append({"ticker": ticker, **{f"rs_{p.lower()}": rs.get(p) for p in ("1W","1M","3M","6M")}, "rs_trend": trend})

    results.sort(key=lambda x: x.get("rs_1m") or 0, reverse=True)
    return results


# ─── 5. Dividend vs Treasury Spread ──────────────────────────

def compute_dividend_spread(
    etf_analysis: dict,
    dividend_tickers: list[str],
    market_indicators: dict,
) -> dict:
    """배당 ETF 평균 6M 수익률 vs 10Y 국채 수익률 비교."""
    # 배당 ETF 평균 수익률
    div_returns = []
    for t in dividend_tickers:
        a = etf_analysis.get(t)
        if a:
            r6m = a.get("returns", {}).get("6M")
            if r6m is not None:
                div_returns.append(r6m)

    div_avg_6m = sum(div_returns) / len(div_returns) if div_returns else None

    # SPY 6M 수익률
    spy_6m = etf_analysis.get("SPY", {}).get("returns", {}).get("6M")

    # 10Y 국채 수익률
    treasury_10y = None
    for name, data in market_indicators.items():
        if "10-Year" in name:
            treasury_10y = data["value"]

    # 배당 프리미엄/디스카운트
    div_vs_spy = round(div_avg_6m - spy_6m, 2) if div_avg_6m is not None and spy_6m is not None else None

    return {
        "dividend_avg_6m": round(div_avg_6m, 2) if div_avg_6m is not None else None,
        "spy_6m": spy_6m,
        "dividend_vs_spy": div_vs_spy,
        "treasury_10y": treasury_10y,
        "interpretation": (
            "Dividend ETFs outperforming SPY" if div_vs_spy and div_vs_spy > 2
            else "Dividend ETFs underperforming SPY" if div_vs_spy and div_vs_spy < -2
            else "Dividend ETFs tracking SPY"
        ),
    }


# ─── 6. Market Regime Detector ───────────────────────────────

def compute_market_regime(
    market_indicators: dict,
    breadth: dict,
) -> dict:
    """VIX + 수익률곡선 + 달러 + Breadth → 시장 레짐 분류."""
    score = 0  # + = risk-on, - = risk-off
    signals: dict[str, str] = {}

    ten_y = None
    short_y = None

    for name, data in market_indicators.items():
        val = data["value"]
        d5 = data["change_5d_pct"]

        if "VIX" in name and "MOVE" not in name:
            if val < 15:
                score += 2; signals["VIX"] = f"{val:.1f} (Low Vol → Risk-On)"
            elif val < 20:
                score += 1; signals["VIX"] = f"{val:.1f} (Normal)"
            elif val < 30:
                score -= 1; signals["VIX"] = f"{val:.1f} (Elevated → Caution)"
            else:
                score -= 2; signals["VIX"] = f"{val:.1f} (Crisis → Risk-Off)"

            if d5 < -10:
                score += 1; signals["VIX_trend"] = f"Rapidly falling ({d5:+.1f}%)"
            elif d5 > 10:
                score -= 1; signals["VIX_trend"] = f"Rapidly rising ({d5:+.1f}%)"

        if "10-Year" in name:
            ten_y = val
        if "13-Week" in name or "3-Month" in name or "Bill" in name:
            short_y = val

        if "Dollar" in name:
            if d5 < -1:
                score += 1; signals["USD"] = f"Weakening ({d5:+.1f}%) → EM Favorable"
            elif d5 > 1:
                score -= 1; signals["USD"] = f"Strengthening ({d5:+.1f}%) → Risk-Off"
            else:
                signals["USD"] = f"Stable ({d5:+.1f}%)"

    # Yield curve
    if ten_y is not None and short_y is not None:
        spread = ten_y - short_y
        if spread > 0.5:
            score += 1; signals["Yield_Curve"] = f"Normal (+{spread:.2f}%)"
        elif spread > 0:
            signals["Yield_Curve"] = f"Flat (+{spread:.2f}%)"
        else:
            score -= 2; signals["Yield_Curve"] = f"INVERTED ({spread:+.2f}%) → Recession Risk"

    # Breadth
    b200 = breadth.get("above_200sma_pct", 50)
    if b200 > 70:
        score += 1; signals["Breadth"] = f"{b200:.0f}% above 200SMA (Broad strength)"
    elif b200 < 30:
        score -= 1; signals["Breadth"] = f"{b200:.0f}% above 200SMA (Broad weakness)"
    else:
        signals["Breadth"] = f"{b200:.0f}% above 200SMA"

    if score >= 3:
        regime = "RISK-ON"
    elif score >= 1:
        regime = "RISK-ON (Moderate)"
    elif score >= -1:
        regime = "TRANSITION"
    elif score >= -3:
        regime = "RISK-OFF (Moderate)"
    else:
        regime = "RISK-OFF"

    return {"regime": regime, "score": score, "signals": signals}


# ─── 7. Market Breadth ───────────────────────────────────────

def compute_market_breadth(etf_analysis: dict) -> dict:
    """200일선/50일선 위 ETF 비율 = 시장 건강도."""
    total = above_200 = above_50 = gc = dc = 0

    for a in etf_analysis.values():
        price = a.get("price")
        sma200 = a.get("sma_200")
        sma50 = a.get("sma_50")
        trend = a.get("trend", "")

        if price and sma200:
            total += 1
            if price > sma200:
                above_200 += 1
            if sma50 and price > sma50:
                above_50 += 1
            if "Golden Cross" in trend:
                gc += 1
            if "Death Cross" in trend:
                dc += 1

    if total == 0:
        return {}

    pct200 = above_200 / total * 100
    health = (
        "Strong Bull" if pct200 > 70
        else "Moderate Bull" if pct200 > 50
        else "Mixed / Weakening" if pct200 > 30
        else "Bear Market"
    )
    return {
        "above_200sma_pct": round(pct200, 1),
        "above_50sma_pct": round(above_50 / total * 100, 1),
        "golden_cross": gc,
        "death_cross": dc,
        "total": total,
        "health": health,
    }


# ─── 8. Macro Calendar ───────────────────────────────────────

def get_macro_calendar(date: datetime | None = None) -> list[str]:
    """FOMC, 실적시즌, 쿼드위칭, 월말 리밸런싱 근접도."""
    if date is None:
        date = datetime.now()

    events: list[str] = []

    # FOMC 2025-2026
    fomc = [
        datetime(2025,1,29), datetime(2025,3,19), datetime(2025,5,7),
        datetime(2025,6,18), datetime(2025,7,30), datetime(2025,9,17),
        datetime(2025,10,29), datetime(2025,12,10),
        datetime(2026,1,28), datetime(2026,3,18), datetime(2026,4,29),
        datetime(2026,6,17), datetime(2026,7,29), datetime(2026,9,16),
        datetime(2026,10,28), datetime(2026,12,9),
    ]
    for fd in fomc:
        days = (fd - date).days
        if -2 <= days <= 7:
            tag = "TODAY" if days == 0 else (f"D-{days}" if days > 0 else f"D+{abs(days)}")
            events.append(f"FOMC: {fd.strftime('%m/%d')} ({tag})")

    # Earnings season
    m, d = date.month, date.day
    if m in (1,4,7,10) and 10 <= d <= 31:
        q = {1:4, 4:1, 7:2, 10:3}[m]
        events.append(f"Earnings Season (Q{q}) Active")

    # Quad witching
    if m in (3,6,9,12):
        weeks = calendar.monthcalendar(date.year, m)
        fridays = [w[calendar.FRIDAY] for w in weeks if w[calendar.FRIDAY]]
        if len(fridays) >= 3:
            qw = datetime(date.year, m, fridays[2])
            days = (qw - date).days
            if -2 <= days <= 5:
                events.append(f"Quad Witching: {qw.strftime('%m/%d')}")

    # Month-end
    last_day = calendar.monthrange(date.year, m)[1]
    if last_day - d <= 3:
        events.append("Month-End Rebalancing Window")

    return events or ["No major macro events this week"]


# ─── Unified Formatter ───────────────────────────────────────

def format_enhanced_data(
    enhanced: dict[str, Any],
    ticker_names: dict[str, str],
) -> str:
    """모든 Enhanced 지표를 텍스트로 변환."""
    lines: list[str] = []

    # Market Regime
    regime = enhanced.get("market_regime", {})
    lines.append("\n## MARKET REGIME")
    lines.append(f"**{regime.get('regime', 'N/A')}** (Score: {regime.get('score', 0)})")
    for k, v in regime.get("signals", {}).items():
        lines.append(f"- {k}: {v}")

    # Market Breadth
    br = enhanced.get("market_breadth", {})
    lines.append(f"\n## MARKET BREADTH")
    lines.append(f"- Above 200 SMA: {br.get('above_200sma_pct', 'N/A')}% ({br.get('health', 'N/A')})")
    lines.append(f"- Above 50 SMA: {br.get('above_50sma_pct', 'N/A')}%")
    lines.append(f"- Golden Cross: {br.get('golden_cross', 0)} | Death Cross: {br.get('death_cross', 0)}")

    # Macro Calendar
    lines.append(f"\n## MACRO CALENDAR")
    for e in enhanced.get("macro_calendar", []):
        lines.append(f"- {e}")

    # Contrarian Scores
    cs = enhanced.get("contrarian_scores", {})
    if cs:
        lines.append(f"\n## CONTRARIAN BUY SIGNALS (Backtest-validated)")
        for ticker, data in list(cs.items())[:10]:
            name = ticker_names.get(ticker, ticker)
            lines.append(f"- **{name} ({ticker})**: Score {data['score']}/10 [{data['level']}] - {', '.join(data['reasons'])}")

    # Mean Reversion
    mr = enhanced.get("mean_reversion", [])
    if mr:
        lines.append(f"\n## MEAN REVERSION CANDIDATES (3M worst performers)")
        for item in mr[:10]:
            name = ticker_names.get(item["ticker"], item["ticker"])
            macd_mom = item.get("macd_momentum", "N/A")
            lines.append(f"- {name} ({item['ticker']}): 3M={item['return_3m']:+.1f}% RSI={item.get('rsi','N/A')} MACD_Mom={macd_mom} 52W={item.get('from_52w_high','N/A')}%")

    # Vol-Adjusted Momentum Top/Bottom
    vam = enhanced.get("vol_adj_momentum", [])
    if vam:
        lines.append(f"\n## VOL-ADJUSTED MOMENTUM (Top 10)")
        for item in vam[:10]:
            name = ticker_names.get(item["ticker"], item["ticker"])
            lines.append(f"- {name} ({item['ticker']}): Score={item['momentum_score']:+.3f} (3M={item['return_3m']:+.1f}% / Vol={item['volatility']:.1f}%)")
        lines.append(f"\n## VOL-ADJUSTED MOMENTUM (Bottom 10)")
        for item in vam[-10:]:
            name = ticker_names.get(item["ticker"], item["ticker"])
            lines.append(f"- {name} ({item['ticker']}): Score={item['momentum_score']:+.3f} (3M={item['return_3m']:+.1f}% / Vol={item['volatility']:.1f}%)")

    # Sector RS
    srs = enhanced.get("sector_rs", [])
    if srs:
        lines.append(f"\n## SECTOR RELATIVE STRENGTH vs SPY")
        for s in srs:
            def _pp(v):
                return f"{v:+.2f}pp" if v is not None else "N/A"
            lines.append(f"- {s['ticker']}: 1W={_pp(s.get('rs_1w'))} 1M={_pp(s.get('rs_1m'))} 3M={_pp(s.get('rs_3m'))} | {s['rs_trend']}")

    # Dividend Spread
    ds = enhanced.get("dividend_spread", {})
    if ds:
        lines.append(f"\n## DIVIDEND vs TREASURY SPREAD")
        lines.append(f"- Dividend ETFs avg 6M return: {ds.get('dividend_avg_6m', 'N/A')}%")
        lines.append(f"- SPY 6M return: {ds.get('spy_6m', 'N/A')}%")
        lines.append(f"- Dividend vs SPY: {ds.get('dividend_vs_spy', 'N/A')}pp")
        lines.append(f"- 10Y Treasury Yield: {ds.get('treasury_10y', 'N/A')}%")
        lines.append(f"- Assessment: {ds.get('interpretation', 'N/A')}")

    return "\n".join(lines)
