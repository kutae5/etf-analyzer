"""
Evaluator-Optimizer loop for ETF reports.

Three-stage validation:
1. Programmatic hard rule checks (deterministic):
   - Leverage ETFs mentioned in recommendation/insight sections
   - 17-section structural completeness
   - Death Cross density → turnaround warning enforcement
2. LLM numeric fact-check (via .claude/skills/evaluator.md)
3. Optimizer rewrite when violations found

Called from reporter.py after report generation. Returns the validated
(and if necessary, corrected) report text.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

# Leverage/inverse tickers that must not appear as "recommendations"
LEVERAGE_TICKERS = {
    "TQQQ", "SQQQ", "SOXL", "SOXS", "UVXY", "SVXY", "VXX",
    "TMF", "TMV", "SPXL", "SPXS", "UPRO", "SPXU",
    "TNA", "TZA", "FAS", "FAZ", "LABU", "LABD",
    "UDOW", "SDOW", "NAIL", "DRV", "BITI",
}

# Section-17 (Key Insights) header patterns
INSIGHT_HEADERS = ["핵심 투자 인사이트", "Key Investment Insights", "핵심 인사이트"]

# Required section names (loose match — any one substring is enough)
REQUIRED_SECTIONS = [
    "시장 요약", "시장 지표", "지수", "섹터", "배당",
    "성장", "중소형", "채권", "원자재", "해외",
    "테마", "레버리지", "기술적", "상관관계", "뉴스",
    "리스크", "인사이트",
]

# Words that indicate a leverage mention is in proper warning context
WARNING_CONTEXT_WORDS = [
    "금지", "제외", "부적합", "심리 지표", "심리지표",
    "decay", "현혹", "경계", "주의", "편입하지", "편입 금지",
    "추천 대상", "매수 금지", "보유 부적합",
]


@dataclass
class Violation:
    rule: str
    severity: str  # "high" | "medium" | "low"
    detail: str


@dataclass
class EvalResult:
    violations: list[Violation] = field(default_factory=list)

    @property
    def has_issues(self) -> bool:
        return len(self.violations) > 0

    @property
    def high_severity_count(self) -> int:
        return sum(1 for v in self.violations if v.severity == "high")


def _extract_insight_section(report: str) -> str:
    """Return the text of the Key Insights section, or empty string if not found."""
    for header in INSIGHT_HEADERS:
        pattern = rf"##\s*(?:\d+\.\s*)?{re.escape(header)}.*?(?=\n##\s|\Z)"
        m = re.search(pattern, report, flags=re.DOTALL | re.IGNORECASE)
        if m:
            return m.group(0)
    return ""


def _check_leverage_recommendations(report: str) -> list[Violation]:
    """
    Hard Rule #1: leverage ETFs must not appear as positive recommendations
    in the Key Insights section. Allowed only with warning context.
    """
    insight = _extract_insight_section(report)
    if not insight:
        return []

    violations = []
    for ticker in LEVERAGE_TICKERS:
        # ASCII-only \b so Korean particles (e.g. "UPRO로") still count as a boundary.
        # Without re.ASCII, Python 3's Unicode \w treats Hangul as word chars.
        pattern = rf"\b{ticker}\b"
        if not re.search(pattern, insight, flags=re.ASCII):
            continue
        # Check surrounding ±200 chars for warning context
        for m in re.finditer(pattern, insight, flags=re.ASCII):
            start = max(0, m.start() - 200)
            end = min(len(insight), m.end() + 200)
            window = insight[start:end]
            if not any(w in window for w in WARNING_CONTEXT_WORDS):
                violations.append(Violation(
                    rule="leverage_in_insights",
                    severity="high",
                    detail=f"{ticker} appears in Key Insights without warning context. "
                           f"Hard Rule #1: leverage ETFs must be sentiment-only, never recommended.",
                ))
                break  # one violation per ticker is enough
    return violations


def _check_section_completeness(report: str) -> list[Violation]:
    """Warn if fewer than 12 of 17 expected section keywords are present."""
    found = sum(1 for s in REQUIRED_SECTIONS if s in report)
    if found < 12:
        return [Violation(
            rule="missing_sections",
            severity="medium",
            detail=f"Only {found}/17 expected section keywords found in report. "
                   f"Structure appears incomplete.",
        )]
    return []


def _sector_death_cross_density(raw_data: str) -> float:
    """
    Estimate fraction of sector ETFs (XL*) marked as Death Cross / Strong Downtrend
    in the raw data. Returns 0.0 if not detectable.
    """
    sector_lines = [
        line for line in raw_data.splitlines()
        if re.search(r"\b(XLK|XLF|XLE|XLV|XLI|XLP|XLY|XLU|XLRE|XLC|XLB)\b", line)
    ]
    if not sector_lines:
        return 0.0
    death_count = sum(
        1 for line in sector_lines
        if "Death Cross" in line or "Strong Downtrend" in line
    )
    # Each sector may appear in multiple lines; normalize by unique tickers matched
    unique_sectors = set()
    death_sectors = set()
    for line in sector_lines:
        for tkr in re.findall(r"\b(XLK|XLF|XLE|XLV|XLI|XLP|XLY|XLU|XLRE|XLC|XLB)\b", line):
            unique_sectors.add(tkr)
            if "Death Cross" in line or "Strong Downtrend" in line:
                death_sectors.add(tkr)
    if not unique_sectors:
        return 0.0
    return len(death_sectors) / len(unique_sectors)


def _check_insight_concentration(report: str) -> list[Violation]:
    """
    Hard Rule #5: Key Insights must use Conviction High/Medium/Low tiering
    with max 10 BUY items, and High Conviction capped at 5.
    Avoids the "20-ticker diversified dump" pattern that dilutes alpha.
    """
    insight = _extract_insight_section(report)
    if not insight:
        return []

    violations = []

    # Count Conviction markers
    high_pattern = r"Conviction[:\s]*High"
    medium_pattern = r"Conviction[:\s]*Medium"
    high_count = len(re.findall(high_pattern, insight, flags=re.IGNORECASE))
    medium_count = len(re.findall(medium_pattern, insight, flags=re.IGNORECASE))
    has_conviction = "Conviction" in insight or "conviction" in insight

    # Structural check: insights should declare conviction tiers
    if not has_conviction:
        violations.append(Violation(
            rule="missing_conviction_structure",
            severity="medium",
            detail="Key Insights does not use Conviction: High/Medium/Low tier structure. "
                   "Hard Rule #5 requires every BUY recommendation to carry a conviction tag.",
        ))

    # High-conviction cap: ≤5
    if high_count > 5:
        violations.append(Violation(
            rule="too_many_high_conviction",
            severity="medium",
            detail=f"Found {high_count} 'Conviction: High' picks. "
                   f"Hard Rule #5 caps High Conviction at 5 to preserve alpha concentration.",
        ))

    # Total BUY cap: count ONLY top-level pick markers, not nested sub-bullets.
    # A "pick" is one of:
    #   - A ### header (subsection) that is NOT itself a category header
    #     like "Conviction: High" or "회피/축소 권고"
    #   - A top-level numbered item `1.` / `2.` at column 0 (no leading space)
    #     that contains a ticker-like all-caps token
    # Sub-bullets (leading whitespace or -/* markers under a pick) are ignored.
    divider_pattern = re.compile(
        r"^(?:\*\*)?Conviction\s*[:：]\s*(?:High|Medium|Low|상|중|하)(?:\*\*)?\s*[-—]?.*$",
        re.IGNORECASE,
    )
    category_header_words = (
        "회피", "축소", "경고", "주의", "원칙", "편집",
        "요약", "summary", "avoid", "warning",
    )

    picks = 0
    # ### subsection headers — pick if not a pure tier divider / category label
    for m in re.finditer(r"(?m)^###\s+(.+)$", insight):
        title = m.group(1).strip().strip("*").strip()
        # Pure tier divider like "### Conviction: High" (no ticker content)
        if divider_pattern.match(title):
            continue
        lower = title.lower()
        if any(w in lower for w in category_header_words):
            continue
        picks += 1

    # Top-level numbered picks at column 0 (ignore indented sub-bullets)
    for m in re.finditer(r"(?m)^\d+\.\s+(.+)$", insight):
        line = m.group(1)
        low = line.lower()
        if any(w in low for w in ("회피", "금지", "축소", "매도", "제외", "avoid", "warning")):
            continue
        # Require an ALL-CAPS token (ticker) in the line to count as a pick
        if not re.search(r"\b[A-Z]{2,5}\b", line):
            continue
        picks += 1

    if picks > 8:
        violations.append(Violation(
            rule="too_many_buy_items",
            severity="medium",
            detail=f"Found {picks} BUY picks in Key Insights (top-level only). "
                   f"Hard Rule #5 caps total BUY recommendations at 8. "
                   f"Diversified dumps dilute alpha (validated by 2022/2023 backtest).",
        ))

    # Hard Rule #5 (1 pick = 1 ticker): no "관련 ETF:" / "Related ETF:" / "ETF:"
    # multi-ticker groups. These dilute alpha by ~10pp per backtest.
    # Strip markdown bold markers (**) first so they don't break the pattern.
    insight_flat = insight.replace("**", "")
    group_pattern = re.compile(
        r"(?:관련\s*ETFs?|Related\s*ETFs?|ETFs?)\s*[:：]\s*"
        r"([A-Z]{2,5}(?:\s*[,，、/]\s*[A-Z]{2,5}){1,})",
        re.IGNORECASE,
    )
    group_matches = group_pattern.findall(insight_flat)
    if group_matches:
        total_grouped = sum(
            len(re.findall(r"\b[A-Z]{2,5}\b", m)) for m in group_matches
        )
        violations.append(Violation(
            rule="multi_etf_group_per_pick",
            severity="medium",
            detail=f"Found {len(group_matches)} '관련 ETF: A, B, C' style ticker groups "
                   f"({total_grouped} tickers total) in Key Insights. "
                   f"Hard Rule #5 requires 1 pick = 1 primary ticker. Group listings dilute "
                   f"alpha (2.5Y backtest: grouped 15+ tickers = −2pp alpha vs focused "
                   f"6 single-ticker picks = +9pp alpha, 11pp gap).",
        ))

    return violations


def _check_turnaround_warning(report: str, raw_data: str) -> list[Violation]:
    """
    Hard Rule #3: If ≥50% of sector ETFs are in Death Cross, the report must
    mention turnaround / contrarian / 저점 / 매수 기회 to warn the reader.
    """
    density = _sector_death_cross_density(raw_data)
    if density < 0.5:
        return []
    warning_keywords = ["turnaround", "저점", "역지표", "매수 기회", "contrarian", "역사적", "반등"]
    if any(kw in report for kw in warning_keywords):
        return []
    return [Violation(
        rule="missing_turnaround_warning",
        severity="high",
        detail=f"{density*100:.0f}% of sector ETFs are in Death Cross/Strong Downtrend, "
               f"but the report does not mention turnaround/contrarian possibility. "
               f"Hard Rule #3 requires warning that mass Death Cross may signal a "
               f"historical bottom, not a sell signal.",
    )]


def run_programmatic_checks(report: str, raw_data: str) -> list[Violation]:
    """Fast deterministic rule checks. No LLM calls."""
    violations = []
    violations.extend(_check_leverage_recommendations(report))
    violations.extend(_check_section_completeness(report))
    violations.extend(_check_turnaround_warning(report, raw_data))
    violations.extend(_check_insight_concentration(report))
    return violations


def run_numeric_factcheck(report: str, raw_data: str) -> list[Violation]:
    """
    LLM-based numeric citation check. Calls claude -p with the evaluator skill
    and parses the returned JSON array of mismatches.
    """
    from src.reporter import _load_skill, _run_claude_cli

    skill = _load_skill("evaluator")
    prompt = (
        f"{skill}\n\n"
        f"---\n\n## REPORT TO CHECK\n\n{report}\n\n"
        f"---\n\n## RAW SOURCE DATA\n\n{raw_data}\n\n"
        f"---\n\nNow output the JSON array of numeric mismatches. "
        f"Output ONLY the JSON, no prose, no code fences."
    )
    try:
        output = _run_claude_cli(prompt, timeout=600)
    except Exception as e:
        print(f"  [eval] Numeric fact-check failed ({e}); skipping")
        return []

    # Strip code fences if present
    output = re.sub(r"^```(?:json)?\s*", "", output.strip())
    output = re.sub(r"\s*```$", "", output)

    try:
        parsed = json.loads(output)
    except json.JSONDecodeError:
        # Attempt to extract JSON array from surrounding text
        m = re.search(r"\[.*\]", output, flags=re.DOTALL)
        if not m:
            print(f"  [eval] Could not parse evaluator output; skipping numeric check")
            return []
        try:
            parsed = json.loads(m.group(0))
        except json.JSONDecodeError:
            return []

    if not isinstance(parsed, list):
        return []

    violations = []
    for item in parsed:
        if not isinstance(item, dict):
            continue
        if item.get("found_in_data") is True:
            continue  # correct citation, skip
        claim = item.get("claim", "<unknown>")
        actual = item.get("actual", "")
        severity = item.get("severity", "medium")
        detail = f"Numeric mismatch: report says '{claim}'"
        if actual:
            detail += f", raw data says '{actual}'"
        violations.append(Violation(
            rule="numeric_mismatch",
            severity=severity if severity in ("high", "medium", "low") else "medium",
            detail=detail,
        ))
    return violations


def evaluate_report(report: str, raw_data: str, llm_check: bool = True) -> EvalResult:
    """Run all evaluators and return combined result."""
    result = EvalResult()
    result.violations.extend(run_programmatic_checks(report, raw_data))
    if llm_check:
        result.violations.extend(run_numeric_factcheck(report, raw_data))
    return result


def optimize_report(report: str, violations: list[Violation]) -> str:
    """Rewrite the report to fix all listed violations. Uses claude -p."""
    from src.reporter import _run_claude_cli

    violation_list = "\n".join(
        f"- [{v.severity.upper()}] {v.rule}: {v.detail}" for v in violations
    )

    has_concentration = any(
        v.rule in (
            "too_many_buy_items", "too_many_high_conviction",
            "missing_conviction_structure", "multi_etf_group_per_pick",
        )
        for v in violations
    )
    has_leverage = any(v.rule == "leverage_in_insights" for v in violations)
    has_group = any(v.rule == "multi_etf_group_per_pick" for v in violations)

    enforcement = []
    if has_concentration:
        enforcement.append(
            "- **Section 17 핵심 투자 인사이트는 반드시 다음 구조로 축소**:\n"
            "  - `### Conviction: High` 서브섹션 아래 BUY 추천 **정확히 3개**\n"
            "  - `### Conviction: Medium` 서브섹션 아래 BUY 추천 **최대 5개**\n"
            "  - **전체 primary pick 합은 절대 8개 초과 금지**\n"
            "  - 각 pick 제목에 `{티커} — {짧은 제목} (Conviction: High/Medium)` 형식\n"
            "  - 회피/축소 권고는 별도 `### 회피/축소 권고` 서브섹션으로 분리\n"
            "  - 축소 시 확신도가 높은 것만 남기고 나머지는 **완전 삭제**. 내용 합치거나 그룹화 금지\n"
            "  - 원본 항목이 많았다면 의도적 제외를 1-2문장으로 설명"
        )
    if has_group:
        enforcement.append(
            "- **1 pick = 1 primary ticker 강제 (Hard Rule #5)**:\n"
            "  - **'관련 ETF: A, B, C, D, E' 같은 다중 티커 나열 모두 제거**\n"
            "  - 각 pick 제목에 **정확히 1개** primary 티커만 명시\n"
            "  - 동일 테마에 여러 ETF가 적합하면 **가장 유동성·비용이 좋은 1개만 선택**\n"
            "  - 대안 티커는 본문 prose에 '대안: XXX (선택)' 형태로만 1-2개 짧게\n"
            "  - 대안도 primary로 셈 — 제목에 여러 티커 나열 절대 금지\n"
            "  - 백테스트 근거: 1 pick = 1 ticker 시 +9pp alpha vs 그룹핑 시 −2pp alpha (11pp 차이)"
        )
    if has_leverage:
        enforcement.append(
            "- **레버리지/인버스 ETF (TQQQ/SQQQ/SOXL/UVXY/TMF/UPRO/SPXL 등)**: "
            "Key Insights 섹션에서 모든 추천 문맥에서 **완전히 제거**할 것. "
            "필요 시 '회피/축소 권고' 서브섹션에 '심리 지표 전용, 매수 금지'로만 언급."
        )

    enforcement_block = "\n".join(enforcement) if enforcement else ""

    prompt = (
        "You are a senior editor revising an ETF market analysis report. "
        "Below is the original report followed by a list of issues that must be fixed. "
        "Rewrite the report to address **EVERY** issue. Write in Korean. "
        "Preserve all sections 1-16 unchanged. Only modify Section 17 (Key Insights) "
        "and any explicitly-flagged content. "
        "Output ONLY the corrected report in Markdown, no commentary, no preamble.\n\n"
        f"## ISSUES TO FIX\n{violation_list}\n\n"
        f"## MANDATORY ENFORCEMENT\n{enforcement_block}\n\n"
        "## CRITICAL\n"
        "Do NOT preserve the original Section 17 if it violates any rule. "
        "Cut it down aggressively. A shorter, sharper report is the goal. "
        "Deliberate exclusion is better than diluted conviction.\n\n"
        f"## ORIGINAL REPORT\n{report}"
    )
    return _run_claude_cli(prompt, timeout=900)


def evaluate_and_optimize(
    report: str,
    raw_data: str,
    llm_check: bool = True,
    max_rounds: int = 2,
) -> tuple[str, EvalResult]:
    """
    Full evaluator-optimizer loop.
    Returns (final_report, final_eval_result).
    """
    print("  [eval] Running evaluator (programmatic + LLM)...")
    result = evaluate_report(report, raw_data, llm_check=llm_check)

    if not result.has_issues:
        print("  [eval] No violations found — report approved")
        return report, result

    print(f"  [eval] Found {len(result.violations)} violation(s) "
          f"({result.high_severity_count} high severity):")
    for v in result.violations:
        print(f"    - [{v.severity}] {v.rule}: {v.detail[:100]}")

    current = report
    current_result = result
    for round_idx in range(max_rounds):
        print(f"  [eval] Optimizer round {round_idx + 1}/{max_rounds}...")
        try:
            current = optimize_report(current, current_result.violations)
        except Exception as e:
            print(f"  [eval] Optimizer failed ({e}); returning original")
            return report, result
        # Re-evaluate programmatic checks only (cheap) after rewrite
        current_result = EvalResult(violations=run_programmatic_checks(current, raw_data))
        if not current_result.has_issues:
            print(f"  [eval] All programmatic violations resolved after round {round_idx + 1}")
            break
        print(f"  [eval] {len(current_result.violations)} programmatic issue(s) remain")

    return current, current_result
