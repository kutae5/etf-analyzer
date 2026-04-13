# ETF Market Analysis System

US ETF 시장 분석 + DCA 타이밍 + 포트폴리오 배분 시스템.

108개 미국 ETF를 실시간 분석하여 **"이번 달 얼마를, 어디에, 언제 팔지"** 를 알려줍니다.

## Quick Start

```bash
# 설치
uv sync

# 매월 300만원 적립 투자 가이드 (명령어 하나!)
uv run python run.py --no-ai --monthly 3000000

# AI 보고서 포함 (Claude Max plan 필요)
uv run python run.py --cc --monthly 3000000
```

## 출력 예시

```
MONTHLY INVESTMENT PLAN [정상 투입]

이번 달 투자 판단:
- 시그널: FULL
- 투입: 3,000,000원 / 3,000,000원 (100%)

매수 계획:
| ETF       | Conviction | 비중  | 금액      | 손절  | 익절  |
|-----------|------------|-------|----------|-------|-------|
| UNG       | High       | 19.4% | 582,000원 | -5.9% | +8.9% |
| XLE       | Medium     | 16.7% | 501,000원 | -2.9% | +2.9% |
| CIBR      | Medium     | 16.1% | 483,000원 | -3.5% | +3.5% |
| ...       | ...        | ...   | ...       | ...   | ...   |
```

보고서: `reports/ETF_Report_YYYY-MM-DD_HHMM.md`
요약본: `reports/Summary_YYYY-MM-DD.md`

## 주요 명령어

```bash
# 기본: 데이터만 (AI 없이, 가장 빠름)
uv run python run.py --no-ai --monthly 3000000

# AI 보고서 (17개 섹션 한국어 분석)
uv run python run.py --cc --monthly 3000000

# 멀티에이전트 (5명 전문가 병렬 분석)
uv run python run.py --cc --multi --monthly 3000000

# 리스크 프로필 변경
uv run python run.py --no-ai --monthly 3000000 --risk-profile conservative
uv run python run.py --no-ai --monthly 3000000 --risk-profile aggressive

# 축적 현금이 있는 경우
uv run python run.py --no-ai --monthly 3000000 --cash-reserve 1500000

# 일회성 투자 (DCA 아님)
uv run python run.py --no-ai --capital 50000000

# 특정 ETF만 분석
uv run python run.py --no-ai --etfs SPY QQQ IWM

# 백테스트
uv run python backtest.py              # 1년 시그널 정확도
uv run python backtest_5year.py        # 5년 교차검증
uv run python backtest_dca.py          # DCA 전략 5년 시뮬레이션
```

## 시스템이 답하는 질문

| 질문 | 답변 |
|------|------|
| 이번 달 얼마를 넣어야 하나? | DCA 타이밍: FULL/CAUTIOUS/ACCELERATE (33~150%) |
| 어떤 ETF에? | Conviction High/Medium/Low 종목 + 비중(%) + 금액 |
| 언제 팔아야 하나? | 변동성 기반 손절선 + Conviction별 익절선 |
| 시장이 폭락하면? | ACCELERATE: 축적 현금 방출, 150% 투입 |
| 시장이 과열이면? | CAUTIOUS: 66% 투입, 34% 현금 축적 |

## DCA 타이밍 시그널

| 시그널 | 투입비율 | 조건 |
|--------|---------|------|
| ACCELERATE | 150% | 폭락 + 역추세 다수 |
| OVERWEIGHT | 120% | 약세장 → 비중 확대 |
| **FULL** | **100%** | **정상 시장 (기본값)** |
| CAUTIOUS | 66% | 과열 시장 |
| MINIMAL | 33% | 극단 과열 |

## 백테스트 결과 (2021-2026)

**DCA 타이밍 vs SPY 단순 적립 (57개월)**

| 전략 | 총 투자 | 최종 가치 | 수익률 |
|------|---------|----------|--------|
| SPY 단순 적립 | 171,000,000원 | 247,575,000원 | +44.8% |
| **DCA 타이밍** | 171,000,000원 | **247,855,000원** | **+44.9%** |

- 하락장(2022)에서 ACCELERATE로 저가 매수 → 회복 시 수익 극대화
- 평균 투입 비율 108% (하락 시 가속, 상승 시 정상)

## 분석 지표 (5년 교차검증 완료)

| 지표 | 유효성 |
|------|--------|
| Wilder's RSI (14) | 과매도 < 30: 반등 확률 높음 |
| MACD + Histogram Momentum | Death Cross + MACD Rising: **+3.1pp alpha** (유일한 교차검증 통과) |
| ADX (Average Directional Index) | 추세 강도 측정, 단독 alpha 없음 |
| Contrarian Score (0-10) | 61.9% 정확도 (시장 의존적) |
| Market Regime | VIX + 금리 + 달러 + Breadth 종합 |

## 아키텍처

```
run.py                      # 메인 진입점
├── src/fetcher.py           # yfinance 데이터 수집
├── src/analyzer.py          # RSI, MACD, ADX, Bollinger 등 기술적 분석
├── src/enhanced.py          # Contrarian score, Mean reversion, Regime 등
├── src/portfolio.py         # DCA 타이밍 + 포트폴리오 배분 + Exit 시그널
├── src/reporter.py          # Claude AI 보고서 생성
├── src/agents.py            # 멀티에이전트 (5 전문가)
├── config.yaml              # 108개 ETF 유니버스
└── .claude/skills/          # Claude 프롬프트 (6개 전문가 스킬)

backtest.py                  # 1년 시그널 백테스트
backtest_5year.py            # 5년 교차검증
backtest_dca.py              # DCA 전략 백테스트
```

## 주의사항

- 본 시스템은 **투자 참고용**이며 투자 권유가 아닙니다
- 과거 성과가 미래 수익을 보장하지 않습니다
- 환율, 매매 수수료, 세금은 반영되지 않습니다
- 반드시 본인의 판단과 책임하에 투자 결정을 하십시오
