---
description: Run the ETF market analysis pipeline and generate a Korean-language report via Claude Code CLI (no API credits required — uses Max plan).
argument-hint: "[--multi] [--etfs TICKERS] [--period 6mo|1y|2y]"
---

# /etf-report

Execute the ETF market analysis pipeline using Claude Code CLI mode (`--cc`), which reuses the active Max-plan session instead of consuming API credits.

## Usage

- `/etf-report` — Single-agent Claude Code mode over the full config.yaml universe (~100 ETFs).
- `/etf-report --multi` — Orchestrator-workers mode: 5 specialist skills run in parallel, then synthesis.
- `/etf-report --etfs SPY QQQ XLK` — Restrict to specific tickers.
- `/etf-report --period 6mo` — Override analysis period.

## Execution

Run the following from the repo root:

```bash
python run.py --cc $ARGUMENTS
```

The pipeline:
1. Fetches prices (yfinance), market indicators, and news.
2. Runs technical + enhanced-indicator analysis locally in Python.
3. Saves raw data to `reports/ETF_Report_<timestamp>.md`.
4. Invokes `claude -p` (this CLI) with the raw data + report-style skill to produce the final Korean report.

## Related skills

The report style and 5 specialist personas live in `.claude/skills/`:
- `etf-report-style.md` — single-agent 17-section structure
- `macro-analyst.md`, `sector-dividend-strategist.md`, `fixed-income-commodity.md`, `global-thematic.md`, `risk-technical.md` — multi-agent specialists
- `synthesis-strategist.md` — final synthesis persona
