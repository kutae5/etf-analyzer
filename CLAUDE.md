# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

US ETF market analysis system that fetches price data via `yfinance`, computes technical indicators, and generates Korean-language investment reports via Claude (API or CLI). Entry point is `run.py`, not `main.py` (which is a placeholder stub).

## Commands

Dependencies are managed with `uv` (see `uv.lock`, `pyproject.toml`); `requirements.txt` is also present as a fallback.

```bash
uv sync                               # install deps
python run.py                         # single-agent API mode (default, needs ANTHROPIC_API_KEY)
python run.py --multi                 # multi-agent API mode (5 specialists + synthesis)
python run.py --cc                    # single-agent via Claude Code CLI (Max plan, no API credits)
python run.py --cc --multi            # multi-agent via parallel Claude Code CLI subprocesses (Max plan)
python run.py --no-ai                 # dump raw formatted data, skip AI entirely
python run.py --etfs SPY QQQ IWM      # restrict tickers
python run.py --period 6mo            # override analysis period
python run.py --model claude-opus-4-6 # override Claude model (API modes only)
python run.py --capital 50000000      # set portfolio size (default: 10,000,000 KRW)
python run.py --risk-profile aggressive  # conservative / moderate / aggressive
python backtest.py                    # 1yr-ago → today accuracy check (trend, RSI, sector ranking)
python backtest_5year.py              # 5-year backtest with cross-validation
```

Within Claude Code itself, the `/etf-report` slash command wraps `python run.py --cc` — see `.claude/commands/etf-report.md`.

`ANTHROPIC_API_KEY` must be set for the two API modes. `--cc` variants shell out to the `claude` binary (auto-discovered via `shutil.which`, with Windows `C:\nvm4w\nodejs\claude.cmd` etc. as fallbacks) and consume the active Max-plan session instead of API credits.

Reports are written to `reports/ETF_Report_<timestamp>.md`.

## Architecture

Pipeline is orchestrated linearly in `run.py:main`:

1. **Fetch** (`src/fetcher.py`) — `fetch_etf_prices` (yfinance batch), `fetch_market_indicators` (VIX, yields, DXY, gold, oil), `fetch_news` (RSS via feedparser).
2. **Analyze** (`src/analyzer.py`) — `analyze_etf` computes per-ticker RSI/MACD/Bollinger/trend/returns/volatility; `analyze_sectors` ranks GICS sectors; `analyze_correlations` builds pair correlations + volatility ranking.
3. **Enhanced indicators** (`src/enhanced.py`) — backtest-validated overlays: contrarian scores (with ADX + MACD momentum), mean reversion, vol-adjusted momentum, sector RS, dividend spread, market regime, breadth, macro calendar. `format_enhanced_data` renders them for the prompt.
3b. **Portfolio allocation** (`src/portfolio.py`) — conviction + volatility + MACD momentum 기반 포지션 사이징. `compute_allocation` → risk-profile별 비중(%) 및 금액 배분. `format_allocation` → Markdown 테이블. `_build_auto_picks` (in `run.py`) auto-generates picks from contrarian + momentum signals.
4. **Report** (`src/reporter.py`) — `format_analysis_data` produces the shared text block. Four output paths:
   - `generate_report` → Anthropic API single-shot. System prompt loaded from `.claude/skills/etf-report-style.md`.
   - `generate_via_claude_code` → pipes prompt into `claude -p` via stdin (single-agent, Max plan).
   - `src/agents.py:run_multi_agent` → async orchestrator-workers via API: 5 specialists parallel with `asyncio.gather`, then synthesis.
   - `run_multi_agent_via_cli` → **Max-plan variant of the above**. Spawns 5 `claude -p` subprocesses concurrently via `ThreadPoolExecutor`, then one synthesis subprocess. Each specialist's system prompt is loaded from `.claude/skills/<skill>.md` (one source of truth shared with the API path).
5. **Save** — `save_report` writes markdown with YAML frontmatter.

Each specialist's prompt is built from only the data slices it declares via `needs_market`/`needs_sectors`/`needs_correlation`/`needs_news` + its `groups` list. Data slicing helpers (`_fmt_market`, `_fmt_etfs`, `_fmt_rankings`, `_fmt_correlation`, `_fmt_news`) live in `src/agents.py` and are reused by both the API and CLI paths.

## Config

`config.yaml` is the single source of ETF universe truth. Tickers are organized into groups (`indices`, `sectors`, `dividend`, `growth_value`, `size`, `bonds`, `commodities`, `international`, `thematic`, `real_estate`, `leveraged`, `crypto`). `run.py:get_all_tickers` flattens all groups into one ticker→name map. Agents in `src/agents.py` reference these group keys directly — adding a new group requires wiring it into at least one agent's `groups` list.

`config.claude.model` sets the default model; `config.analysis.period` / `news_count` control fetch defaults. `config.market_indicators` maps display names to yahoo symbols (e.g. `^VIX`, `^TNX`).

## Plugin layout (`.claude/`)

Adopts the file-based Claude Code plugin convention (cf. `anthropics/financial-services-plugins`):

```
.claude/
├── plugin.json                        # manifest
├── commands/
│   └── etf-report.md                  # /etf-report slash command
└── skills/
    ├── etf-report-style.md            # single-agent 17-section style
    ├── macro-analyst.md               # specialist 1 — indices & macro
    ├── sector-dividend-strategist.md  # specialist 2 — sectors, dividends, growth/value
    ├── fixed-income-commodity.md      # specialist 3 — bonds & commodities
    ├── global-thematic.md             # specialist 4 — international, thematic, REITs
    ├── risk-technical.md              # specialist 5 — leveraged, risk matrix, technicals
    └── synthesis-strategist.md        # final synthesis persona
```

Skills are the **single source of truth** for all Claude-facing prompts. `src/reporter.py:_load_skill` reads them from disk and strips YAML frontmatter; both API (`generate_report`) and CLI (`generate_via_claude_code`, `run_multi_agent_via_cli`) paths load the same files. `src/agents.py:AGENTS` is the legacy async-API path and still has inline system prompts — if you change a specialist's structure, update **both** the skill file and `src/agents.py:AGENTS[key]['system']` until that legacy dict is migrated.

## Output contracts

Specialists each own a fixed, numbered set of report sections (1-17). The synthesis agent assumes these section numbers exist — if you modify a skill's section headers, keep the `## N. 제목` format stable or update the synthesizer skill in tandem.
