"""기본 지표 요약(가격·수익률·이평·RSI·스파크라인) + 차트 분석 결과(ta)."""
from __future__ import annotations

import numpy as np
import pandas as pd

from .technical import DEFAULT, analyze, rsi_series


def _ret(close: pd.Series, n: int) -> float | None:
    if len(close) <= n:
        return None
    return float((close.iloc[-1] / close.iloc[-1 - n] - 1) * 100)


def technicals(df: pd.DataFrame, tcfg: dict | None = None, with_ta: bool = True) -> dict:
    tcfg = {**DEFAULT, **(tcfg or {})}
    c = df["Close"].astype(float)
    last = float(c.iloc[-1])
    mas = tcfg["ma"]
    ma = {str(n): (float(c.rolling(n).mean().iloc[-1]) if len(c) >= n else None) for n in mas}
    long_ma = ma[str(mas[-1])]
    hi252, lo252 = float(c.iloc[-252:].max()), float(c.iloc[-252:].min())
    vol20 = float(c.pct_change().iloc[-20:].std() * np.sqrt(252) * 100) if len(c) > 21 else None
    r = rsi_series(c, tcfg["rsi"]).iloc[-1]
    out = {
        "last": last,
        "prev": float(c.iloc[-2]) if len(c) > 1 else None,
        "date": c.index[-1].strftime("%Y-%m-%d"),
        "chg_1d": _ret(c, 1), "chg_5d": _ret(c, 5), "chg_20d": _ret(c, 20),
        "chg_60d": _ret(c, 60), "chg_252d": _ret(c, 252),
        "ma": ma,
        "above_ma_long": (last > long_ma) if long_ma else None,
        "rsi14": None if pd.isna(r) else float(r),
        "drawdown_52w": (last / hi252 - 1) * 100,
        "from_low_52w": (last / lo252 - 1) * 100,
        "vol20_ann": vol20,
        "spark": [round(float(v), 4) for v in c.iloc[-90:].values],
    }
    if with_ta:
        out["ta"] = analyze(df, tcfg)
    return out
