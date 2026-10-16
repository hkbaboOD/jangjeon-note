"""차트 규칙 백테스트 — '이 셋업·신호·점수가 과거에 실제로 통했는가'를 수치로 확인합니다.

방법
- 유니버스 종목의 5년 일봉에서, 시장별 공통 달력 기준 5거래일마다 표본 시점 t를 잡습니다.
- 시점 t까지의 데이터만으로(최근 320봉) technical.analyze()를 실행 → 셋업·신호·점수 기록 (미래 데이터 미사용)
- 진입 = t+1일 시가, 청산 = t+h일 종가 (h = 5·20·60거래일), 왕복 비용 차감(한국 0.25%, 미국 0.05%)
- 초과수익 = 같은 날짜·같은 시장 표본 전체 평균 대비 (상승장 착시 제거)
- t값은 표본 중첩을 보정: 유효 표본 수 = n × min(1, 표본간격/h)
- 팩터 IC = 날짜별 (점수 순위 ↔ 이후 수익률 순위) 스피어만 상관의 평균

한계(앱에도 표시)
- 펀더멘털(PER·목표가 등)은 과거 시점 자료가 무료로 없어 백테스트 대상이 아님 → 차트 부분만 검증
- 현재 유니버스(오늘 살아남은 대형주)로 과거를 보므로 생존 편향 → 절대 수익률은 부풀려짐(초과수익이 더 공정한 지표)
"""
from __future__ import annotations

import logging
import math
import os
from concurrent.futures import ProcessPoolExecutor

import numpy as np
import pandas as pd

from .common import market_of
from .technical import analyze

log = logging.getLogger(__name__)
HORIZONS = (5, 20, 60)
COST = {"kr": 0.25, "us": 0.05}  # 왕복 비용(%)
STEP = 5
LOOKBACK = 320
MIN_BARS = 300
MIN_N = 30  # 통계 표시 최소 표본


def _eval_ticker(args):
    t, df, dates, tcfg = args
    mk = market_of(t)
    cost = COST[mk]
    o, c = df["Open"].astype(float).values, df["Close"].astype(float).values
    pos = df.index.get_indexer(dates)
    out = []
    n = len(df)
    for i in pos:
        if i < MIN_BARS or i >= n - 1:
            continue
        ta = analyze(df.iloc[max(0, i - LOOKBACK): i + 1], tcfg)
        if ta.get("setup") == "insufficient":
            continue
        entry = o[i + 1] if o[i + 1] > 0 and not math.isnan(o[i + 1]) else c[i]
        rec = {"date": df.index[i], "ticker": t, "market": mk, "setup": ta["setup"],
               "codes": [s["code"] for s in ta["signals"] if s["pol"] != 0], **{f"s_{k}": v for k, v in ta["scores"].items()}}
        for h in HORIZONS:
            rec[f"r{h}"] = (c[i + h] / entry - 1) * 100 - cost if i + h < n else np.nan
        out.append(rec)
    return out


def _stats(x: pd.Series, h: int) -> dict | None:
    x = x.dropna()
    n = len(x)
    if n < MIN_N:
        return None
    n_eff = max(1.0, n * min(1.0, STEP / h))
    sd = x.std(ddof=1)
    t = float(x.mean() / (sd / math.sqrt(n_eff))) if sd > 0 else 0.0
    return {"n": int(n), "n_eff": round(n_eff, 1), "avg": round(float(x.mean()), 2), "med": round(float(x.median()), 2),
            "win": round(float((x > 0).mean() * 100), 1), "t": round(t, 2)}


def run(hist: dict[str, pd.DataFrame], tcfg: dict | None = None, workers: int | None = None) -> dict:
    # 시장별 공통 달력 → STEP 간격 표본 날짜
    cal = {}
    for t, df in hist.items():
        cal.setdefault(market_of(t), set()).update(df.index)
    sample = {mk: sorted(d)[MIN_BARS::STEP] for mk, d in cal.items()}
    jobs = [(t, df, pd.DatetimeIndex(sample[market_of(t)]), tcfg) for t, df in hist.items() if len(df) > MIN_BARS + 5]
    workers = workers or min(4, os.cpu_count() or 1)
    recs = []
    if workers > 1 and len(jobs) > 4:
        with ProcessPoolExecutor(max_workers=workers) as ex:
            for part in ex.map(_eval_ticker, jobs, chunksize=2):
                recs.extend(part)
    else:
        for j in jobs:
            recs.extend(_eval_ticker(j))
    if not recs:
        return {"ok": False, "reason": "표본 없음(이력 부족)"}

    df = pd.DataFrame(recs)
    for h in HORIZONS:
        df[f"x{h}"] = df[f"r{h}"] - df.groupby(["date", "market"])[f"r{h}"].transform("mean")

    def block(sub: pd.DataFrame) -> dict:
        return {str(h): {"abs": _stats(sub[f"r{h}"], h), "ex": _stats(sub[f"x{h}"], h)} for h in HORIZONS}

    by_setup = {}
    for s, g in df.groupby("setup"):
        b = {"all": block(g)}
        for mk, gm in g.groupby("market"):
            b[mk] = block(gm)
        by_setup[s] = b

    ex = df[["codes"] + [f"x{h}" for h in HORIZONS] + [f"r{h}" for h in HORIZONS]].explode("codes").dropna(subset=["codes"])
    by_signal = {}
    for code, g in ex.groupby("codes"):
        if len(g) >= MIN_N:
            by_signal[code] = block(g)

    factors = ["s_total", "s_trend", "s_momentum", "s_timing"]
    ic, quint = {}, {}
    for f in factors:
        ic[f[2:]], quint[f[2:]] = {}, {}
        for h in HORIZONS:
            col = f"r{h}"
            sub = df[[f, col, "date", "market"]].dropna()
            vals = []
            for _, g in sub.groupby(["date", "market"]):
                if len(g) >= 8 and g[f].nunique() > 2:
                    vals.append(g[f].rank().corr(g[col].rank()))
            vals = [v for v in vals if not np.isnan(v)]
            if len(vals) >= 10:
                m, sd = float(np.mean(vals)), float(np.std(vals, ddof=1))
                n_eff = len(vals) * min(1.0, STEP / h)
                ic[f[2:]][str(h)] = {"ic": round(m, 4), "t": round(m / (sd / math.sqrt(n_eff)), 2) if sd > 0 else 0.0,
                                     "dates": len(vals)}
            # 5분위 (날짜·시장 내 상대 순위)
            q = sub.groupby(["date", "market"])[f].transform(
                lambda s: pd.qcut(s.rank(method="first"), 5, labels=False) if len(s) >= 10 else np.nan)
            sub = sub.assign(q=q, x=df.loc[sub.index, f"x{h}"]).dropna(subset=["q"])
            quint[f[2:]][str(h)] = [round(float(sub[sub.q == k]["x"].mean()), 2) if (sub.q == k).any() else None
                                    for k in range(5)]

    period = [df["date"].min().strftime("%Y-%m-%d"), df["date"].max().strftime("%Y-%m-%d")]
    return {
        "ok": True, "period": period, "samples": int(len(df)), "tickers": int(df["ticker"].nunique()),
        "step": STEP, "horizons": list(HORIZONS), "cost": COST,
        "base": {mk: block(g) for mk, g in df.groupby("market")},
        "by_setup": by_setup, "by_signal": by_signal, "ic": ic, "quintiles": quint,
        "setup_share": {k: round(v * 100, 1) for k, v in df["setup"].value_counts(normalize=True).items()},
    }
