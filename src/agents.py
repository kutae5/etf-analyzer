"""
Multi-Agent ETF Analysis System
================================
Anthropic 'Orchestrator-Workers' 패턴 기반
Financial Services Plugins 분석 템플릿 적용

Architecture:
  Phase 1 (Parallel): 5 specialized agents analyze different domains
  Phase 2 (Sequential): Synthesis agent combines into final report
"""

import asyncio
import os
from datetime import datetime
from typing import Any

from anthropic import AsyncAnthropic

# ─────────────────────────────────────────────────────────────
# Data Formatting Helpers
# ─────────────────────────────────────────────────────────────

def _fmt_market(indicators: dict) -> str:
    lines = ["## MARKET INDICATORS"]
    for name, d in indicators.items():
        lines.append(f"- {name}: {d['value']} (1D:{d['change_1d_pct']:+.2f}% 5D:{d['change_5d_pct']:+.2f}%)")
    return "\n".join(lines)


def _fmt_etfs(etf_analysis: dict, etf_groups: dict, group_keys: list[str]) -> str:
    lines = []
    for key in group_keys:
        tickers = etf_groups.get(key, {})
        if not tickers:
            continue
        label = key.upper().replace("_", " ")
        lines.append(f"\n### [{label}]")
        for ticker, name in tickers.items():
            if ticker not in etf_analysis:
                continue
            a = etf_analysis[ticker]
            r = a.get("returns", {})
            lines.append(
                f"{name} ({ticker}): ${a['price']} | "
                f"1W={r.get('1W','?')}% 1M={r.get('1M','?')}% "
                f"3M={r.get('3M','?')}% 6M={r.get('6M','?')}% | "
                f"RSI={a['rsi']} MACD={a['macd_crossover']} MACD_Mom={a.get('macd_momentum','N/A')} | "
                f"Trend={a['trend']} ADX={a.get('adx','N/A')} ({a.get('trend_strength','N/A')}) BB={a['bollinger_position']} | "
                f"Vol={a['volume_ratio']}x VolAnn={a['volatility_30d']}% | "
                f"52W_H={a['from_52w_high']:+.1f}%"
            )
    return "\n".join(lines)


def _fmt_rankings(sector_results: list, dividend_data: list | None = None) -> str:
    lines = []
    if sector_results:
        lines.append("\n## SECTOR RANKING (1M)")
        for i, s in enumerate(sector_results, 1):
            lines.append(f"{i}. {s['name']}({s['ticker']}): 1W={s.get('1W','?')}% 1M={s.get('1M','?')}% 3M={s.get('3M','?')}%")
    if dividend_data:
        lines.append("\n## DIVIDEND RANKING (1M)")
        for i, d in enumerate(dividend_data, 1):
            lines.append(f"{i}. {d}")
    return "\n".join(lines)


def _fmt_correlation(corr_data: dict) -> str:
    lines = ["## CORRELATION (Top pairs, 3M)"]
    for p in corr_data.get("pairs", [])[:15]:
        lines.append(f"- {p['pair']}: {p['correlation']}")
    lines.append("\n## VOLATILITY RANKING")
    for t, v in list(corr_data.get("volatility", {}).items())[:20]:
        lines.append(f"- {t}: {v}%")
    return "\n".join(lines)


def _fmt_news(news: list) -> str:
    lines = ["## RECENT NEWS"]
    for i, a in enumerate(news, 1):
        lines.append(f"{i}. [{a['source']}] {a['title']}")
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────
# Agent Definitions
# Prompts incorporate Financial Services Plugins patterns:
#   - Comparable Analysis (side-by-side tables)
#   - Investment Thesis (Bull/Bear/Base case)
#   - Risk Matrix (Probability x Impact)
# ─────────────────────────────────────────────────────────────

AGENTS = {
    "macro": {
        "name": "Macro & Index Analyst",
        "groups": ["indices"],
        "needs_market": True,
        "needs_sectors": False,
        "needs_correlation": False,
        "needs_news": False,
        "system": """You are a senior macro economist analyzing US equity markets.
Write in Korean. Use markdown tables. Be data-driven with specific numbers.

PRODUCE EXACTLY:

## 1. 시장 요약 (Executive Summary)
3-4문장으로 현재 시장 상태를 핵심 요약.

## 2. 주요 시장 지표 분석
| 지표 | 현재값 | 1일 | 5일 | 해석 |
VIX, 금리(단/장기), 달러, 금, 유가 각각 해석. 수익률 곡선 형태 분석.

## 3. 주요 지수 ETF 분석
| ETF | 가격 | 1주 | 1개월 | 3개월 | 6개월 | 추세 | RSI |
SPY, QQQ, DIA, IWM, VTI 비교 분석. 상대강도, 모멘텀 차이 해석.""",
    },

    "sector_dividend": {
        "name": "Sector & Dividend Strategist",
        "groups": ["sectors", "dividend", "growth_value", "size"],
        "needs_market": False,
        "needs_sectors": True,
        "needs_correlation": False,
        "needs_news": False,
        "system": """You are a sector rotation specialist and dividend investment strategist.
Write in Korean. Use markdown tables and rankings.

Apply Comparable Analysis framework (from financial-services-plugins):
- Side-by-side comparison tables
- Relative value assessment
- Style factor analysis

PRODUCE EXACTLY:

## 4. 섹터 분석
| 순위 | 섹터 | 1주 | 1개월 | 3개월 | RSI | 추세 | 특이사항 |
강세/약세 섹터 식별. 섹터 로테이션 흐름 해석. 과매수/과매도 경고.

## 5. 배당 ETF 비교 분석
| ETF | 1개월 | 3개월 | 6개월 | 변동성 | 추세 | 전략유형 |
고배당(VYM,HDV,SPYD) vs 배당성장(VIG,DGRO) vs 커버드콜(JEPI,JEPQ) 비교.
위험조정수익률 관점에서 최적 배당 ETF 추천.

## 6. 성장 vs 가치
| 스타일 | 대표ETF | 1M | 3M | 6M | 추세 |
현재 어느 스타일이 유리한지, 갭의 크기, 전환 신호 여부.

## 7. 중소형주 동향
소형주/중형주 vs 대형주(SPY) 상대성과. 리스크온/오프 시사점.""",
    },

    "fixed_commodity": {
        "name": "Fixed Income & Commodity Analyst",
        "groups": ["bonds", "commodities"],
        "needs_market": True,
        "needs_sectors": False,
        "needs_correlation": False,
        "needs_news": False,
        "system": """You are a fixed income strategist and commodity market analyst.
Write in Korean. Use markdown tables.

PRODUCE EXACTLY:

## 8. 채권 시장 분석
| ETF | 만기 | 1개월 | 3개월 | 6개월 | 추세 | 변동성 |
수익률 곡선 분석 (SHY vs IEF vs TLT).
크레딧 스프레드 (HYG vs LQD vs AGG).
TIPS 인플레이션 기대. EM채권 전망.
듀레이션 포지셔닝 추천.

## 9. 원자재 분석
| 자산 | 1개월 | 3개월 | 6개월 | RSI | 특이사항 |
금/은: 인플레이션 헤지 관점.
에너지: 원유/천연가스 수급.
농산물: 과매도 반등 기회.
원자재 사이클 현재 위치 판단.""",
    },

    "global_thematic": {
        "name": "Global & Thematic Analyst",
        "groups": ["international", "thematic", "real_estate"],
        "needs_market": False,
        "needs_sectors": False,
        "needs_correlation": False,
        "needs_news": False,
        "system": """You are a global equity strategist and thematic investment analyst.
Write in Korean. Use markdown tables.

PRODUCE EXACTLY:

## 10. 해외 시장 분석
선진국 테이블:
| 국가 | ETF | 1개월 | 3개월 | 6개월 | RSI | 추세 |
신흥국 테이블 (같은 형식).
US 대비 아웃퍼폼/언더퍼폼 분석. 달러 약세 영향.
국가별 투자 매력도 순위.

## 11. 테마/혁신 ETF
강세 테마 테이블 + 약세 테마 테이블.
반도체, AI, 바이오, 클린에너지, 사이버보안 등 모멘텀 분석.
52주 신고가 도달 테마 vs Death Cross 테마.

## 12. 부동산/REITs
VNQ, IYR, SCHH 분석. 금리 환경과의 관계.""",
    },

    "risk_technical": {
        "name": "Risk & Technical Analyst",
        "groups": ["leveraged", "crypto"],
        "needs_market": True,
        "needs_sectors": False,
        "needs_correlation": True,
        "needs_news": True,
        "system": """You are a risk manager and technical analyst.
Write in Korean. Use markdown tables.

Apply Risk Matrix framework (from financial-services-plugins):
| Risk | Probability | Impact | Monitoring |

PRODUCE EXACTLY:

## 13. 레버리지/인버스 & 암호화폐
레버리지 ETF 흐름을 시장 심리 지표로 해석.
TQQQ/SQQQ 비율, SOXL 과열도, UVXY 방향성.
비트코인 ETF 현황과 위험자산 선호도.

## 14. 기술적 분석 하이라이트
과매수 종목 테이블 (RSI>75 or BB 상단 돌파):
| ETF | RSI | 볼린저 | 6M수익률 | 의미 |
과매도 종목 테이블 (RSI<35 or BB 하단 돌파).
Golden Cross / Death Cross 종목 목록.
52주 신고가 / 신저가 종목.

## 15. 상관관계 및 변동성
핵심 상관관계 쌍 해석. 변동성 상위 종목과 의미.

## 16. 뉴스 및 시장 심리
주요 뉴스 테마 3-4개로 분류하여 시장 영향 분석.

## 17. 리스크 요인
| Risk | Probability | Impact | Action |
최소 5개 리스크 요인 식별.""",
    },
}

SYNTHESIS_SYSTEM = """You are a Chief Investment Strategist at a major asset management firm.
You are synthesizing analyses from 5 specialist analysts into a cohesive executive briefing.
Write in Korean.

Apply Investment Thesis format (from financial-services-plugins):
- Clear conviction levels (High/Medium/Low)
- Specific ETF recommendations with rationale
- Risk-adjusted perspective

PRODUCE EXACTLY:

# ETF 시장 종합 분석 보고서

## Executive Summary (시장 종합 요약)
5-6문장으로 전체 시장 상황, 핵심 트렌드, 주요 기회와 리스크를 요약.

(여기에 각 전문가 분석 섹션을 순서대로 배치)

## 핵심 투자 인사이트

각 인사이트를 다음 형식으로:
### [제목] (Conviction: High/Medium/Low)
- 근거: 구체적 데이터 인용
- 관련 ETF: 티커와 수치
- 주의사항: 리스크 요인

최소 6개, 최대 8개 인사이트.

---
> 본 보고서는 정보 제공 목적이며 투자 권유가 아닙니다. 투자 결정은 개인의 재무 상황과 리스크 허용 범위를 고려하여 전문가와 상담 후 이루어져야 합니다."""


# ─────────────────────────────────────────────────────────────
# Agent Execution Engine
# ─────────────────────────────────────────────────────────────

async def _call_agent(
    client: AsyncAnthropic,
    model: str,
    system: str,
    user_prompt: str,
    max_tokens: int = 4000,
) -> str:
    """Execute a single agent call."""
    response = await client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user_prompt}],
    )
    return response.content[0].text


def _build_agent_prompt(
    agent_key: str,
    agent_cfg: dict,
    etf_analysis: dict,
    etf_groups: dict,
    market_indicators: dict,
    sector_results: list,
    correlation_data: dict,
    news: list,
) -> str:
    """Build the user prompt for a specific agent with relevant data only."""
    today = datetime.now().strftime("%Y-%m-%d")
    parts = [f"Analysis Date: {today}\n"]

    # Market indicators (if needed)
    if agent_cfg.get("needs_market"):
        parts.append(_fmt_market(market_indicators))

    # ETF data for this agent's groups
    parts.append(_fmt_etfs(etf_analysis, etf_groups, agent_cfg["groups"]))

    # Sector rankings
    if agent_cfg.get("needs_sectors"):
        # Build dividend ranking from analysis data
        div_tickers = etf_groups.get("dividend", {})
        div_ranked = []
        for t, n in div_tickers.items():
            if t in etf_analysis:
                r = etf_analysis[t].get("returns", {})
                div_ranked.append(f"{n}({t}): 1M={r.get('1M','?')}% 3M={r.get('3M','?')}% 6M={r.get('6M','?')}%")
        parts.append(_fmt_rankings(sector_results, div_ranked))

    # Correlation & volatility
    if agent_cfg.get("needs_correlation"):
        parts.append(_fmt_correlation(correlation_data))

    # News
    if agent_cfg.get("needs_news"):
        parts.append(_fmt_news(news))

    return "\n\n".join(parts)


async def run_multi_agent(
    etf_analysis: dict,
    etf_groups: dict,
    sector_results: list,
    correlation_data: dict,
    market_indicators: dict,
    news: list,
    config: dict,
) -> str:
    """
    Phase 1: Run 5 specialized agents in parallel
    Phase 2: Synthesis agent combines all outputs
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise EnvironmentError(
            "ANTHROPIC_API_KEY 환경변수가 설정되지 않았습니다.\n"
            "설정: export ANTHROPIC_API_KEY='your-key-here'"
        )

    client = AsyncAnthropic()
    claude_cfg = config.get("claude", {})
    model = claude_cfg.get("model", "claude-sonnet-4-6-20250514")

    # ── Phase 1: Parallel specialist agents ──
    print(f"  Phase 1: {len(AGENTS)} specialist agents (parallel)...")

    tasks = {}
    for key, cfg in AGENTS.items():
        prompt = _build_agent_prompt(
            key, cfg, etf_analysis, etf_groups,
            market_indicators, sector_results, correlation_data, news,
        )
        tasks[key] = _call_agent(client, model, cfg["system"], prompt, max_tokens=4000)

    results = await asyncio.gather(*tasks.values(), return_exceptions=True)
    agent_outputs = {}
    for key, result in zip(tasks.keys(), results):
        if isinstance(result, Exception):
            print(f"    [!] {AGENTS[key]['name']} failed: {result}")
            agent_outputs[key] = f"(Analysis unavailable: {result})"
        else:
            name = AGENTS[key]["name"]
            print(f"    [OK] {name}")
            agent_outputs[key] = result

    # ── Phase 2: Synthesis ──
    print("  Phase 2: Synthesis agent...")

    # Combine all agent outputs in order
    ordered_keys = ["macro", "sector_dividend", "fixed_commodity", "global_thematic", "risk_technical"]
    combined_analyses = "\n\n---\n\n".join(
        f"### From: {AGENTS[k]['name']}\n{agent_outputs.get(k, '(unavailable)')}"
        for k in ordered_keys
    )

    synthesis_prompt = (
        f"Date: {datetime.now().strftime('%Y-%m-%d')}\n\n"
        f"Below are analyses from 5 specialized financial analysts.\n"
        f"Synthesize into a cohesive report. Keep ALL section content from the analysts.\n"
        f"Add Executive Summary at the top and Key Investment Insights at the bottom.\n\n"
        f"{combined_analyses}"
    )

    final_report = await _call_agent(
        client, model, SYNTHESIS_SYSTEM, synthesis_prompt, max_tokens=8000,
    )

    # Build complete report: synthesis wraps the individual analyses
    report_parts = []

    # Extract executive summary from synthesis (first section)
    synth_lines = final_report.split("\n")
    exec_summary_lines = []
    insights_lines = []
    in_insights = False

    for line in synth_lines:
        if "핵심 투자 인사이트" in line or "Key Investment" in line:
            in_insights = True
        if in_insights:
            insights_lines.append(line)
        else:
            exec_summary_lines.append(line)

    # Assemble final report
    report_parts.append("\n".join(exec_summary_lines))
    report_parts.append("\n---\n")

    for key in ordered_keys:
        report_parts.append(agent_outputs.get(key, ""))

    report_parts.append("\n---\n")
    report_parts.append("\n".join(insights_lines) if insights_lines else "")
    report_parts.append(
        "\n\n---\n> 본 보고서는 정보 제공 목적이며 투자 권유가 아닙니다. "
        "투자 결정은 개인의 재무 상황과 리스크 허용 범위를 고려하여 "
        "전문가와 상담 후 이루어져야 합니다."
    )

    print("  Phase 2: Complete")
    return "\n\n".join(report_parts)
