"""
ETF 기술적 분석 및 시장 분석 모듈
- 기술적 지표 (RSI, MACD, 볼린저밴드, 이동평균)
- 섹터 성과 비교
- 상관관계 및 변동성 분석
"""

import numpy as np
import pandas as pd
from typing import Any


# ─── 기술적 지표 계산 ─────────────────────────────────────────

def _rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """Wilder's RSI using exponential smoothing (industry standard)."""
    delta = series.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)
    # Wilder's smoothing: EMA with alpha = 1/period
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def _macd(series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    ema_fast = series.ewm(span=fast, adjust=False).mean()
    ema_slow = series.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


def _adx(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    """Average Directional Index — measures trend strength (0-100)."""
    prev_high = high.shift(1)
    prev_low = low.shift(1)
    prev_close = close.shift(1)

    # True Range
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)

    # Directional Movement
    plus_dm = ((high - prev_high).where((high - prev_high) > (prev_low - low), 0.0)
               .where((high - prev_high) > 0, 0.0))
    minus_dm = ((prev_low - low).where((prev_low - low) > (high - prev_high), 0.0)
                .where((prev_low - low) > 0, 0.0))

    # Wilder's smoothing
    alpha = 1 / period
    atr = tr.ewm(alpha=alpha, min_periods=period, adjust=False).mean()
    plus_di = 100 * plus_dm.ewm(alpha=alpha, min_periods=period, adjust=False).mean() / atr
    minus_di = 100 * minus_dm.ewm(alpha=alpha, min_periods=period, adjust=False).mean() / atr

    dx = (plus_di - minus_di).abs() / (plus_di + minus_di) * 100
    adx = dx.ewm(alpha=alpha, min_periods=period, adjust=False).mean()
    return adx


def _bollinger(series: pd.Series, period: int = 20, num_std: int = 2):
    sma = series.rolling(window=period).mean()
    std = series.rolling(window=period).std()
    upper = sma + num_std * std
    lower = sma - num_std * std
    return upper, sma, lower


def _safe(val) -> float | None:
    """numpy/pandas 값을 안전하게 float로 변환."""
    if val is None:
        return None
    try:
        v = float(val)
        return None if pd.isna(v) else round(v, 4)
    except (TypeError, ValueError):
        return None


def _get_close(df: pd.DataFrame) -> pd.Series:
    """Close 컬럼 추출 (Adj Close 우선)."""
    if "Adj Close" in df.columns:
        return df["Adj Close"]
    return df["Close"]


# ─── ETF 개별 분석 ────────────────────────────────────────────

def analyze_etf(ticker: str, df: pd.DataFrame) -> dict[str, Any]:
    """단일 ETF에 대한 종합 기술적 분석."""
    close = _get_close(df)
    if close.empty:
        return {}

    price = float(close.iloc[-1])

    # --- 수익률 ---
    period_days = {"1D": 1, "1W": 5, "1M": 21, "3M": 63, "6M": 126, "1Y": 252}
    returns = {}
    for label, days in period_days.items():
        if len(close) > days:
            past = float(close.iloc[-(days + 1)])
            returns[label] = round(((price - past) / past) * 100, 2)

    # --- 이동평균 ---
    sma_20 = _safe(close.rolling(20).mean().iloc[-1]) if len(close) >= 20 else None
    sma_50 = _safe(close.rolling(50).mean().iloc[-1]) if len(close) >= 50 else None
    sma_200 = _safe(close.rolling(200).mean().iloc[-1]) if len(close) >= 200 else None

    # --- RSI ---
    rsi_series = _rsi(close)
    rsi_val = _safe(rsi_series.iloc[-1])

    # --- MACD ---
    macd_line, signal_line, histogram = _macd(close)
    macd_val = _safe(macd_line.iloc[-1])
    signal_val = _safe(signal_line.iloc[-1])
    macd_hist = _safe(histogram.iloc[-1])
    macd_cross = "Bullish" if (macd_val and signal_val and macd_val > signal_val) else "Bearish"

    # MACD histogram momentum: rising = improving, falling = weakening
    macd_hist_prev = _safe(histogram.iloc[-2]) if len(histogram) >= 2 else None
    if macd_hist is not None and macd_hist_prev is not None:
        macd_momentum = "Rising" if macd_hist > macd_hist_prev else "Falling"
    else:
        macd_momentum = "N/A"

    # --- 볼린저밴드 ---
    bb_upper, bb_mid, bb_lower = _bollinger(close)
    bb_pos = None
    if len(bb_upper.dropna()) > 0:
        u, l = float(bb_upper.iloc[-1]), float(bb_lower.iloc[-1])
        if price > u:
            bb_pos = "Above Upper (Overbought)"
        elif price < l:
            bb_pos = "Below Lower (Oversold)"
        elif u != l:
            pct = (price - l) / (u - l) * 100
            bb_pos = f"Within Bands ({pct:.0f}%)"

    # --- 52주 고/저 ---
    window = min(252, len(close))
    high_52w = float(close[-window:].max())
    low_52w = float(close[-window:].min())
    from_high = round(((price - high_52w) / high_52w) * 100, 2)
    from_low = round(((price - low_52w) / low_52w) * 100, 2)

    # --- 거래량 ---
    vol = df["Volume"]
    avg_vol_20 = float(vol.rolling(20).mean().iloc[-1]) if len(vol) >= 20 else float(vol.mean())
    cur_vol = float(vol.iloc[-1])
    vol_ratio = round(cur_vol / avg_vol_20, 2) if avg_vol_20 > 0 else 1.0

    # --- ADX (추세 강도) ---
    adx_val = None
    if "High" in df.columns and "Low" in df.columns and len(df) >= 28:
        adx_series = _adx(df["High"], df["Low"], close)
        adx_val = _safe(adx_series.iloc[-1])

    # --- 추세 판단 ---
    if sma_50 and sma_200:
        if price > sma_50 > sma_200:
            trend = "Strong Uptrend (Golden Cross)"
        elif price > sma_200:
            trend = "Uptrend"
        elif price < sma_50 < sma_200:
            trend = "Strong Downtrend (Death Cross)"
        elif price < sma_200:
            trend = "Downtrend"
        else:
            trend = "Sideways"
    elif sma_50:
        trend = "Uptrend" if price > sma_50 else "Downtrend"
    else:
        trend = "N/A"

    # ADX-based trend strength qualifier
    if adx_val is not None:
        if adx_val < 20:
            trend_strength = "Weak (No Trend)"
        elif adx_val < 25:
            trend_strength = "Developing"
        elif adx_val < 40:
            trend_strength = "Strong"
        else:
            trend_strength = "Very Strong"
    else:
        trend_strength = "N/A"

    # --- 변동성 (30일 연환산) ---
    daily_ret = close.pct_change().dropna()
    vol_30d = None
    if len(daily_ret) >= 30:
        vol_30d = round(float(daily_ret[-30:].std() * np.sqrt(252) * 100), 2)

    return {
        "price": round(price, 2),
        "returns": returns,
        "52w_high": round(high_52w, 2),
        "52w_low": round(low_52w, 2),
        "from_52w_high": from_high,
        "from_52w_low": from_low,
        "sma_20": sma_20,
        "sma_50": sma_50,
        "sma_200": sma_200,
        "rsi": rsi_val,
        "macd": macd_val,
        "macd_signal": signal_val,
        "macd_histogram": macd_hist,
        "macd_crossover": macd_cross,
        "macd_momentum": macd_momentum,
        "bollinger_position": bb_pos,
        "volume_ratio": vol_ratio,
        "trend": trend,
        "adx": adx_val,
        "trend_strength": trend_strength,
        "volatility_30d": vol_30d,
    }


# ─── 섹터 분석 ────────────────────────────────────────────────

def analyze_sectors(
    price_data: dict[str, pd.DataFrame],
    sector_map: dict[str, str],
) -> list[dict[str, Any]]:
    """섹터 ETF 성과 비교 및 순위."""
    results = []

    for ticker, name in sector_map.items():
        if ticker not in price_data:
            continue
        close = _get_close(price_data[ticker])
        price = float(close.iloc[-1])

        perf = {}
        for label, days in {"1W": 5, "1M": 21, "3M": 63, "6M": 126, "1Y": 252}.items():
            if len(close) > days:
                past = float(close.iloc[-(days + 1)])
                perf[label] = round(((price - past) / past) * 100, 2)

        results.append({"ticker": ticker, "name": name, "price": round(price, 2), **perf})

    # 1M 수익률 기준 정렬
    results.sort(key=lambda x: x.get("1M", 0), reverse=True)
    return results


# ─── 상관관계 분석 ─────────────────────────────────────────────

def analyze_correlations(
    price_data: dict[str, pd.DataFrame],
    tickers: list[str],
    period: int = 63,
) -> dict[str, Any]:
    """ETF 간 상관관계 및 변동성 분석."""
    returns_df = pd.DataFrame()

    for ticker in tickers:
        if ticker in price_data:
            close = _get_close(price_data[ticker])
            returns_df[ticker] = close.pct_change()

    returns_df = returns_df.dropna()
    if returns_df.empty or len(returns_df.columns) < 2:
        return {"pairs": [], "volatility": {}}

    # 최근 N일 기준
    recent = returns_df[-period:]
    corr = recent.corr()

    # 상관계수 상위/하위 쌍
    pairs = []
    cols = list(corr.columns)
    for i, t1 in enumerate(cols):
        for t2 in cols[i + 1:]:
            pairs.append({
                "pair": f"{t1} / {t2}",
                "correlation": round(float(corr.loc[t1, t2]), 3),
            })
    pairs.sort(key=lambda x: abs(x["correlation"]), reverse=True)

    # 변동성 순위
    volatility = {}
    for ticker in recent.columns:
        vol = float(recent[ticker].std() * np.sqrt(252) * 100)
        volatility[ticker] = round(vol, 2)
    volatility = dict(sorted(volatility.items(), key=lambda x: x[1], reverse=True))

    return {"pairs": pairs[:20], "volatility": volatility}
