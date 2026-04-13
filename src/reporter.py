"""
Claude AI 보고서 생성 모듈
- 분석 데이터를 그룹별 구조화 텍스트로 변환
- Claude API 호출 또는 Claude Code CLI로 투자 분석 보고서 생성
- Markdown 파일 저장
"""

import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import anthropic

SKILLS_DIR = Path(__file__).resolve().parent.parent / ".claude" / "skills"


def _load_skill(name: str) -> str:
    """Load a skill file from .claude/skills/ and return its body (frontmatter stripped)."""
    path = SKILLS_DIR / f"{name}.md"
    if not path.exists():
        raise FileNotFoundError(f"Skill not found: {path}")
    text = path.read_text(encoding="utf-8")
    return re.sub(r"^---\n.*?\n---\n", "", text, count=1, flags=re.DOTALL).strip()

GROUP_LABELS = {
    "indices": "Major Indices (주요 지수)",
    "sectors": "GICS Sectors (섹터)",
    "dividend": "Dividend ETFs (배당)",
    "growth_value": "Growth & Value (성장/가치)",
    "size": "Small & Mid Cap (중소형주)",
    "bonds": "Bonds & Fixed Income (채권)",
    "commodities": "Commodities (원자재)",
    "international": "International (해외/국가별)",
    "thematic": "Thematic & Innovation (테마/혁신)",
    "real_estate": "Real Estate / REITs (부동산)",
    "leveraged": "Leveraged & Inverse (레버리지/인버스)",
    "crypto": "Crypto-Related (암호화폐)",
}


def _fmt_etf(ticker: str, name: str, a: dict) -> str:
    """단일 ETF 분석 결과를 텍스트로."""
    return (
        f"### {name} ({ticker})\n"
        f"- Price: ${a['price']} | Trend: {a['trend']}\n"
        f"- Returns: {a['returns']}\n"
        f"- 52W: ${a['52w_high']} / ${a['52w_low']} ({a['from_52w_high']:+.1f}% from high)\n"
        f"- SMA 20/50/200: {a['sma_20']} / {a['sma_50']} / {a['sma_200']}\n"
        f"- RSI(14): {a['rsi']} | MACD Cross: {a['macd_crossover']} | MACD Momentum: {a.get('macd_momentum', 'N/A')} | BB: {a['bollinger_position']}\n"
        f"- ADX: {a.get('adx', 'N/A')} ({a.get('trend_strength', 'N/A')}) | Vol Ratio: {a['volume_ratio']}x | 30D Volatility: {a['volatility_30d']}%"
    )


def format_analysis_data(
    etf_analysis: dict[str, dict],
    etf_groups: dict[str, dict[str, str]],
    sector_results: list[dict],
    correlation_data: dict,
    market_indicators: dict,
    news: list[dict],
) -> str:
    """모든 분석 데이터를 그룹별로 구조화하여 Claude 프롬프트용 텍스트 생성."""
    sections = []

    # 1. 시장 지표
    sections.append("## MARKET INDICATORS")
    for name, data in market_indicators.items():
        sections.append(
            f"- {name}: {data['value']} "
            f"(1D: {data['change_1d_pct']:+.2f}%, 5D: {data['change_5d_pct']:+.2f}%)"
        )

    # 2. ETF 분석 (그룹별)
    all_names = {}
    for group_tickers in etf_groups.values():
        all_names.update(group_tickers)

    for group_key, group_tickers in etf_groups.items():
        label = GROUP_LABELS.get(group_key, group_key)
        group_entries = []
        for ticker, name in group_tickers.items():
            if ticker in etf_analysis:
                group_entries.append(_fmt_etf(ticker, name, etf_analysis[ticker]))
        if group_entries:
            sections.append(f"\n## [{label}]")
            sections.append("\n".join(group_entries))

    # 3. 섹터 성과 순위
    if sector_results:
        sections.append("\n## SECTOR PERFORMANCE RANKING (by 1M return)")
        for i, s in enumerate(sector_results, 1):
            sections.append(
                f"{i}. {s['name']} ({s['ticker']}): "
                f"1W={s.get('1W','N/A')}% | 1M={s.get('1M','N/A')}% | "
                f"3M={s.get('3M','N/A')}% | 1Y={s.get('1Y','N/A')}%"
            )

    # 4. 배당 ETF 성과 순위
    div_tickers = etf_groups.get("dividend", {})
    if div_tickers:
        div_ranked = []
        for ticker, name in div_tickers.items():
            if ticker in etf_analysis:
                a = etf_analysis[ticker]
                div_ranked.append({"ticker": ticker, "name": name, **a.get("returns", {})})
        div_ranked.sort(key=lambda x: x.get("1M", 0), reverse=True)
        if div_ranked:
            sections.append("\n## DIVIDEND ETF PERFORMANCE RANKING (by 1M return)")
            for i, d in enumerate(div_ranked, 1):
                sections.append(
                    f"{i}. {d['name']} ({d['ticker']}): "
                    f"1M={d.get('1M','N/A')}% | 3M={d.get('3M','N/A')}% | 1Y={d.get('1Y','N/A')}%"
                )

    # 5. 상관관계
    sections.append("\n## CORRELATION (Top Pairs, 3M)")
    for pair in correlation_data.get("pairs", [])[:20]:
        sections.append(f"- {pair['pair']}: {pair['correlation']}")

    sections.append("\n## VOLATILITY RANKING (Annualized, Top 20)")
    for ticker, vol in list(correlation_data.get("volatility", {}).items())[:20]:
        name = all_names.get(ticker, ticker)
        sections.append(f"- {name} ({ticker}): {vol}%")

    # 6. 뉴스
    sections.append("\n## RECENT FINANCIAL NEWS")
    for i, article in enumerate(news, 1):
        sections.append(f"{i}. [{article['source']}] {article['title']}")

    return "\n".join(sections)


def generate_report(
    analysis_text: str,
    config: dict[str, Any],
) -> str:
    """Claude API를 사용하여 투자 분석 보고서 생성."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise EnvironmentError(
            "ANTHROPIC_API_KEY 환경변수가 설정되지 않았습니다.\n"
            "설정 방법: export ANTHROPIC_API_KEY='your-key-here'"
        )

    client = anthropic.Anthropic()
    today = datetime.now().strftime("%Y-%m-%d")

    system_prompt = f"Today's date: {today}\n\n{_load_skill('etf-report-style')}"

    user_prompt = (
        "아래 시장 데이터와 기술적 분석 결과를 바탕으로, "
        "system prompt에 정의된 17개 섹션 구조를 따라 종합 ETF 시장 분석 보고서를 작성해주세요.\n\n"
        f"{analysis_text}"
    )

    claude_config = config.get("claude", {})
    model = claude_config.get("model", "claude-sonnet-4-6-20250514")
    max_tokens = claude_config.get("max_tokens", 16000)

    print(f"  Claude API ({model})...")
    response = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}],
    )

    return response.content[0].text


def _find_claude_cli() -> str:
    """Locate the `claude` CLI binary on the current platform."""
    import shutil
    claude_cmd = shutil.which("claude") or shutil.which("claude.cmd")
    if claude_cmd:
        return claude_cmd
    for candidate in [
        r"C:\nvm4w\nodejs\claude.cmd",
        r"C:\nvm4w\nodejs\claude",
        os.path.expanduser(r"~\AppData\Roaming\npm\claude.cmd"),
    ]:
        if os.path.exists(candidate):
            return candidate
    raise RuntimeError(
        "claude CLI not found. Install Claude Code or use --no-ai / API mode."
    )


def _run_claude_cli(prompt: str, timeout: int = 600) -> str:
    """Invoke `claude -p` with a prompt via stdin. Returns stdout text.

    Uses bytes mode for stdin/stdout so UTF-8 is explicit and doesn't depend on
    the host console's default code page (cp949 on Windows chokes on em-dashes
    and Korean text otherwise).
    """
    import subprocess
    claude_cmd = _find_claude_cli()
    try:
        result = subprocess.run(
            [claude_cmd, "-p", "--output-format", "text"],
            input=prompt.encode("utf-8"),
            capture_output=True,
            timeout=timeout,
        )
    except FileNotFoundError:
        raise RuntimeError(f"claude CLI not executable at: {claude_cmd}")
    if result.returncode != 0:
        err = result.stderr.decode("utf-8", errors="replace")[:300]
        raise RuntimeError(f"Claude Code error (exit {result.returncode}): {err}")
    out = result.stdout.decode("utf-8", errors="replace").strip()
    if not out:
        raise RuntimeError("Claude Code returned empty output")
    return out


def generate_via_claude_code(
    raw_data_path: str,
    output_dir: str = "reports",
    run_eval: bool = True,
) -> str:
    """Claude Code CLI로 단일 에이전트 보고서 생성 (Max 요금제, API 크레딧 불필요).

    run_eval=True이면 evaluator-optimizer loop를 돌려 하드룰 위반과 숫자 오인용을 교정."""
    path = Path(output_dir)
    path.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M")
    output_path = path / f"ETF_Report_{timestamp}.md"

    skill = _load_skill("etf-report-style")
    raw_data_text = Path(raw_data_path).read_text(encoding="utf-8")
    prompt = (
        f"{skill}\n\n"
        f"---\n\n"
        f"Read the file {raw_data_path} and produce the 17-section Korean report "
        f"following the rules and structure above. "
        f"Output ONLY the report content in Markdown, no commentary, no preamble."
    )

    print("  Claude Code CLI generating report...")
    report_text = _run_claude_cli(prompt, timeout=900)

    eval_note = ""
    if run_eval:
        from src.evaluator import evaluate_and_optimize
        report_text, result = evaluate_and_optimize(report_text, raw_data_text, llm_check=True)
        eval_note = f"evaluator_violations_final: {len(result.violations)}\n"

    header = f"""---
title: ETF Market Analysis Report
date: {datetime.now().strftime("%Y-%m-%d %H:%M")}
generated_by: Claude Code CLI (Max Plan)
mode: claude-code
{eval_note}---

"""
    output_path.write_text(header + report_text, encoding="utf-8")
    return str(output_path)


# ─────────────────────────────────────────────────────────────
# Multi-Agent via Claude Code CLI (Max plan, parallel subprocesses)
# ─────────────────────────────────────────────────────────────

# Skill file name → specialist config (mirrors src/agents.py:AGENTS but sources
# the system prompt from .claude/skills/ so there is one source of truth).
CLI_SPECIALISTS = [
    {
        "key": "macro",
        "skill": "macro-analyst",
        "groups": ["indices"],
        "needs_market": True, "needs_sectors": False,
        "needs_correlation": False, "needs_news": False,
    },
    {
        "key": "sector_dividend",
        "skill": "sector-dividend-strategist",
        "groups": ["sectors", "dividend", "growth_value", "size"],
        "needs_market": False, "needs_sectors": True,
        "needs_correlation": False, "needs_news": False,
    },
    {
        "key": "fixed_commodity",
        "skill": "fixed-income-commodity",
        "groups": ["bonds", "commodities"],
        "needs_market": True, "needs_sectors": False,
        "needs_correlation": False, "needs_news": False,
    },
    {
        "key": "global_thematic",
        "skill": "global-thematic",
        "groups": ["international", "thematic", "real_estate"],
        "needs_market": False, "needs_sectors": False,
        "needs_correlation": False, "needs_news": False,
    },
    {
        "key": "risk_technical",
        "skill": "risk-technical",
        "groups": ["leveraged", "crypto"],
        "needs_market": True, "needs_sectors": False,
        "needs_correlation": True, "needs_news": True,
    },
]


def run_multi_agent_via_cli(
    etf_analysis: dict,
    etf_groups: dict,
    sector_results: list,
    correlation_data: dict,
    market_indicators: dict,
    news: list,
    run_eval: bool = True,
    raw_data_text: str | None = None,
) -> str:
    """
    Parallel 5-specialist + synthesis pipeline using Claude Code CLI subprocesses.
    Uses the Max plan — no API credits consumed.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from src.agents import (
        _fmt_market, _fmt_etfs, _fmt_rankings, _fmt_correlation, _fmt_news,
    )

    _find_claude_cli()  # fail fast if missing

    today = datetime.now().strftime("%Y-%m-%d")

    def build_prompt(cfg: dict) -> str:
        skill_body = _load_skill(cfg["skill"])
        parts = [skill_body, "\n---\n", f"Analysis Date: {today}\n"]
        if cfg["needs_market"]:
            parts.append(_fmt_market(market_indicators))
        parts.append(_fmt_etfs(etf_analysis, etf_groups, cfg["groups"]))
        if cfg["needs_sectors"]:
            div_tickers = etf_groups.get("dividend", {})
            div_ranked = []
            for t, n in div_tickers.items():
                if t in etf_analysis:
                    r = etf_analysis[t].get("returns", {})
                    div_ranked.append(
                        f"{n}({t}): 1M={r.get('1M','?')}% "
                        f"3M={r.get('3M','?')}% 6M={r.get('6M','?')}%"
                    )
            parts.append(_fmt_rankings(sector_results, div_ranked))
        if cfg["needs_correlation"]:
            parts.append(_fmt_correlation(correlation_data))
        if cfg["needs_news"]:
            parts.append(_fmt_news(news))
        parts.append(
            "\nOutput ONLY the Korean report sections defined above. "
            "No preamble, no meta commentary."
        )
        return "\n\n".join(parts)

    print(f"  Phase 1: {len(CLI_SPECIALISTS)} specialists (parallel CLI subprocess)...")

    prompts = {cfg["key"]: (cfg, build_prompt(cfg)) for cfg in CLI_SPECIALISTS}
    outputs: dict[str, str] = {}

    with ThreadPoolExecutor(max_workers=len(CLI_SPECIALISTS)) as pool:
        futures = {
            pool.submit(_run_claude_cli, prompt, 900): key
            for key, (_, prompt) in prompts.items()
        }
        for fut in as_completed(futures):
            key = futures[fut]
            try:
                outputs[key] = fut.result()
                print(f"    [OK] {key}")
            except Exception as e:
                print(f"    [!] {key} failed: {str(e)[:200]}")
                outputs[key] = f"(Analysis unavailable for {key}: {e})"

    # ── Phase 2: Synthesis ──
    print("  Phase 2: Synthesis (CLI subprocess)...")
    ordered = ["macro", "sector_dividend", "fixed_commodity", "global_thematic", "risk_technical"]
    combined = "\n\n---\n\n".join(
        f"### From: {k}\n{outputs.get(k, '(unavailable)')}" for k in ordered
    )
    synth_prompt = (
        f"{_load_skill('synthesis-strategist')}\n\n"
        f"---\n\nDate: {today}\n\n"
        f"Below are analyses from 5 specialists. Synthesize into a cohesive report. "
        f"Keep ALL section content from the analysts. "
        f"Add Executive Summary at the top and Key Investment Insights at the bottom. "
        f"Output ONLY the final Korean report.\n\n{combined}"
    )
    synthesis = _run_claude_cli(synth_prompt, timeout=900)

    # Split synthesis into exec summary + insights (mirror src/agents.py logic)
    synth_lines = synthesis.split("\n")
    exec_lines, insights_lines = [], []
    in_insights = False
    for line in synth_lines:
        if "핵심 투자 인사이트" in line or "Key Investment" in line:
            in_insights = True
        (insights_lines if in_insights else exec_lines).append(line)

    parts = ["\n".join(exec_lines), "\n---\n"]
    for k in ordered:
        parts.append(outputs.get(k, ""))
    parts.append("\n---\n")
    parts.append("\n".join(insights_lines) if insights_lines else "")
    parts.append(
        "\n\n---\n> 본 보고서는 정보 제공 목적이며 투자 권유가 아닙니다. "
        "투자 결정은 개인의 재무 상황과 리스크 허용 범위를 고려하여 "
        "전문가와 상담 후 이루어져야 합니다."
    )
    print("  Phase 2: Complete")
    final_report = "\n\n".join(parts)

    if run_eval and raw_data_text:
        from src.evaluator import evaluate_and_optimize
        final_report, _ = evaluate_and_optimize(final_report, raw_data_text, llm_check=True)

    return final_report


def save_report(content: str, output_dir: str = "reports") -> str:
    """보고서를 Markdown 파일로 저장."""
    path = Path(output_dir)
    path.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M")
    filename = f"ETF_Report_{timestamp}.md"
    filepath = path / filename

    header = f"""---
title: ETF Market Analysis Report
date: {datetime.now().strftime("%Y-%m-%d %H:%M")}
generated_by: ETF Market Analysis System (Claude AI)
---

"""
    filepath.write_text(header + content, encoding="utf-8")
    return str(filepath)
