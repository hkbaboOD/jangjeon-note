"""배당주 백테스트와 배당 점수 자가 보정 (매주 토요일).

1) 종목별 배당 재투자 백테스트 — 스노우볼(mysnowball.kr/backtest)과 같은 방식
   1·3·5·10년 전 매수 → 세후 배당을 배당락일 종가로 같은 종목에 재투자(DRIP) → 오늘 가치
   총수익·연환산·주가 몫·배당 몫에 더해 스노우볼에 없는 최대낙폭(MDD)과
   '1년 보유 승률'(매월 시작한 1년 보유가 지수(배당 재투자)를 이긴 비율)을 함께 계산
   가격은 분할만 반영한 종가(auto_adjust=False), 배당은 Yahoo 배당 이력 → 스노우볼과 같은 원천

2) 배당 점수 검증·보정 — 과거 매월 말 시점에 그때까지의 데이터만으로 배당 점수를 매겨 상위 N종목을 고르고,
   이후 12개월 세후 배당 재투자 수익이 후보 전체 평균을 이겼는지(승률)·얼마나 이겼는지(초과수익)를 채점
   현재·기본·방향별 후보(근거 측정은 ±0.15 거리) 중 학습·검증 구간 모두 확실히 좋을 때만 그쪽으로 한 걸음(0.05) 이동
   안전장치: 주당 한 걸음(±0.05), 각 항목 ≥0.05, 초과수익 개선 ≥0.5%p·승률 하락 ≤5%p,
   학습·검증 두 구간 모두 개선, 개선 t≥2(겹침 보정). 기본값이 이기면 기본값 쪽으로 롤백.
   한계: 현재 후보 목록으로 과거를 보므로 생존 편향이 있고, 과거 시점 배당성향 자료가 없어 그 항목은 고정.
"""
from __future__ import annotations

import logging
import math
from datetime import date

import numpy as np
import pandas as pd

from . import dividend as div_mod
from .common import market_of

log = logging.getLogger(__name__)
HORIZONS = (1, 3, 5, 10)
BENCH = {"us": ("SPY", "S&P500(SPY)"), "kr": ("069500.KS", "코스피200(KODEX 200)")}
FWD_DAYS = 252          # 1년 보유
STEP = 0.05             # 주당 가중치 이동 한도
PROBE = 0.15            # 근거 측정용 후보 거리(효과가 작아 0.05 차이로는 유의성 판단이 어려움) — 실제 이동은 STEP만
FLOOR = 0.05
MARGIN_EX = 0.5         # 초과수익 개선 최소 %p
WIN_GUARD = 5.0         # 승률 하락 허용 %p
T_MIN = 2.0
TOP_N = 6
KEYS = ("yield", "growth", "stability")  # 지속가능성(배당성향)은 과거 자료가 없어 0.20 고정


# ── 배당 재투자 ──
def _align_divs(close: pd.Series, divs: pd.Series) -> pd.Series:
    """배당락일을 거래일에 맞춤(휴장일이면 다음 거래일)."""
    if divs is None or len(divs) == 0:
        return pd.Series(0.0, index=close.index)
    d = divs[(divs.index >= close.index[0]) & (divs.index <= close.index[-1])]
    pos = close.index.searchsorted(d.index)
    out = pd.Series(0.0, index=close.index)
    for p, v in zip(pos, d.values):
        if p < len(out):
            out.iloc[p] += float(v)
    return out


def drip_value(close: pd.Series, divs: pd.Series, tax: float) -> pd.Series:
    """1주로 시작해 세후 배당을 배당락일 종가로 재투자한 평가액 경로."""
    c = close.dropna()
    if len(c) < 2:
        return pd.Series(dtype=float)
    d = _align_divs(c, divs).values
    px = c.values.astype(float)
    shares = np.empty(len(px))
    s = 1.0
    for i in range(len(px)):
        if d[i] > 0 and px[i] > 0:
            s += s * d[i] * (1 - tax) / px[i]
        shares[i] = s
    return pd.Series(shares * px, index=c.index)


def _mdd(v: pd.Series) -> float | None:
    if len(v) < 2:
        return None
    return float((v / v.cummax() - 1).min() * 100)


def holding_stats(close: pd.Series, divs: pd.Series, tax: float, today: pd.Timestamp | None = None) -> dict:
    """스노우볼 방식: N년 전 매수 → 오늘. 데이터가 모자라면 해당 기간은 비움."""
    c = close.dropna()
    today = today or c.index[-1]
    out = {}
    for n in HORIZONS:
        start = today - pd.DateOffset(years=n)
        if c.index[0] > start + pd.Timedelta(days=10):
            continue
        seg = c[c.index >= start]
        if len(seg) < 20:
            continue
        v = drip_value(seg, divs, tax)
        tot = v.iloc[-1] / v.iloc[0] - 1
        prc = seg.iloc[-1] / seg.iloc[0] - 1
        yrs = (v.index[-1] - v.index[0]).days / 365.25
        out[f"{n}y"] = {"start": v.index[0].strftime("%Y-%m-%d"), "total": round(tot * 100, 2),
                        "cagr": round(((1 + tot) ** (1 / yrs) - 1) * 100, 2) if yrs > 0.5 and tot > -1 else None,
                        "price": round(prc * 100, 2), "div": round((tot - prc) * 100, 2), "mdd": round(_mdd(v), 2)}
    return out


def rolling_win(v: pd.Series, bench: pd.Series | None, h: int = FWD_DAYS) -> dict | None:
    """매월 첫 거래일에 시작한 h거래일 보유: 플러스 비율·지수 대비 승률(둘 다 배당 재투자 기준)."""
    if len(v) < h + 21:
        return None
    starts = v.groupby([v.index.year, v.index.month]).head(1).index
    pos, win, n, ex = 0, 0, 0, []
    for s in starts:
        i = v.index.get_loc(s)
        if i + h >= len(v):
            break
        r = v.iloc[i + h] / v.iloc[i] - 1
        n += 1
        pos += r > 0
        if bench is not None and s in bench.index:
            j = bench.index.get_loc(s)
            if j + h < len(bench):
                rb = bench.iloc[j + h] / bench.iloc[j] - 1
                win += r > rb
                ex.append((r - rb) * 100)
    if n == 0:
        return None
    return {"n": n, "pos_rate": round(pos / n * 100, 1),
            "win_rate": round(win / len(ex) * 100, 1) if ex else None,
            "avg_excess": round(float(np.mean(ex)), 2) if ex else None}


# ── 과거 시점 배당 점수 ──
def pit_components(close: pd.Series, divs: pd.Series, t: pd.Timestamp, meta: dict, cap: float) -> dict | None:
    """t 시점까지의 데이터만으로 계산한 점수 항목(미래 데이터 없음)."""
    c = close[close.index <= t]
    if len(c) < 200 or divs is None or len(divs) == 0:
        return None
    d = divs[divs.index <= t]
    ttm = float(d[d.index > t - pd.Timedelta(days=365)].sum())
    if ttm <= 0:
        return None
    yld = ttm / float(c.iloc[-1])
    annual = d.groupby(d.index.year).sum()
    full = annual[annual.index < t.year]
    cagr = None
    if len(full) >= 4:
        y_end, y_start = full.index[-1], full.index[max(0, len(full) - 6)]
        if y_end > y_start and full[y_start] > 0:
            cagr = (full[y_end] / full[y_start]) ** (1 / (y_end - y_start)) - 1
    cuts = int(sum(1 for a, b in zip(full.values[:-1], full.values[1:]) if b < a * 0.95)) if len(full) > 1 else 0
    return div_mod.components(yld, cagr, cuts, None, meta, cap)


def score_with(comp, w: dict) -> float:
    return div_mod.weighted(comp, w)


def strategy_samples(series: dict, metas: dict, tax: dict, cap: float) -> pd.DataFrame:
    """월말마다 (날짜, 종목, 점수 항목, 이후 1년 세후 배당 재투자 수익) 표."""
    rows = []
    allidx = sorted(set().union(*[set(s["close"].index) for s in series.values()])) if series else []
    if not allidx:
        return pd.DataFrame()
    idx = pd.DatetimeIndex(allidx)
    month_ends = idx.to_series().groupby([idx.year, idx.month]).max().values
    for t in pd.DatetimeIndex(month_ends):
        for tk, s in series.items():
            c, v = s["close"], s["value"]
            comp = pit_components(c, s["divs"], t, metas[tk], cap)
            if comp is None:
                continue
            after = v.index[v.index > t]
            if len(after) <= FWD_DAYS:
                continue
            i0 = v.index.get_loc(after[0])
            r = v.iloc[i0 + FWD_DAYS] / v.iloc[i0] - 1
            rows.append({"date": t, "ticker": tk, **comp, "fwd": r * 100})
    return pd.DataFrame(rows)


def evaluate(df: pd.DataFrame, w: dict, top_n: int = TOP_N) -> pd.DataFrame:
    """날짜별: 상위 N 평균 수익 − 후보 전체 평균 = 초과수익, 이겼는지."""
    out = []
    for t, g in df.groupby("date"):
        if len(g) < top_n * 2:
            continue
        sc = g.apply(lambda r: score_with(r, w), axis=1)
        top = g.loc[sc.sort_values(ascending=False).index[:top_n]]
        ex = top["fwd"].mean() - g["fwd"].mean()
        out.append({"date": t, "excess": ex, "win": ex > 0, "n": len(g)})
    return pd.DataFrame(out)


def _stat(ev: pd.DataFrame) -> dict | None:
    if ev is None or ev.empty:
        return None
    n = len(ev)
    ex = ev["excess"]
    sd = ex.std(ddof=1) if n > 1 else float("nan")
    t = float(ex.mean() / sd * math.sqrt(n * min(1.0, 1 / 12))) if n > 1 and sd > 0 else 0.0
    return {"months": n, "excess": round(float(ex.mean()), 2), "win_rate": round(float(ev["win"].mean() * 100), 1),
            "t": round(t, 2), "from": ev["date"].min().strftime("%Y-%m"), "to": ev["date"].max().strftime("%Y-%m")}


def _diff_t(a: pd.DataFrame, b: pd.DataFrame) -> float:
    m = a.merge(b, on="date", suffixes=("_a", "_b"))
    if len(m) < 3:
        return 0.0
    d = m["excess_a"] - m["excess_b"]
    sd = d.std(ddof=1)
    return float(d.mean() / sd * math.sqrt(len(d) * min(1.0, 1 / 12))) if sd > 0 else 0.0


def neighbors(w: dict) -> list[dict]:
    """a를 늘리고 b를 줄인 방향별 후보 1개씩(거리 PROBE, 바닥값에 걸리면 가능한 만큼만)."""
    out = []
    for a in KEYS:
        for b in KEYS:
            if a == b:
                continue
            d = min(PROBE, round(w[b] - FLOOR, 4))
            if d < STEP - 1e-9:
                continue
            x = dict(w)
            x[a] = round(x[a] + d, 4)
            x[b] = round(x[b] - d, 4)
            out.append(x)
    return out


def _toward(cur: dict, target: dict) -> dict:
    """target 쪽으로 최대 한 걸음(합계 유지)."""
    diff = {k: target[k] - cur[k] for k in KEYS}
    up = max(diff, key=diff.get)
    dn = min(diff, key=diff.get)
    if diff[up] <= 1e-9:
        return dict(cur)
    step = min(STEP, diff[up], -diff[dn])
    x = dict(cur)
    x[up] = round(x[up] + step, 4)
    x[dn] = round(x[dn] - step, 4)
    return x


def calibrate(df: pd.DataFrame, prev: dict | None, today: str) -> dict:
    default = dict(div_mod.DEFAULT_WEIGHTS)
    prev = prev or {}
    cur = {k: prev.get("weights", {}).get(k, default[k]) for k in KEYS}
    hist = list(prev.get("history") or [])
    logs = list(prev.get("log") or [])
    res = {"weights": cur, "default": default, "version": prev.get("version", 1), "changed": False,
           "history": hist, "log": logs, "rules": {"step": STEP, "floor": FLOOR, "margin_excess": MARGIN_EX,
                                                   "win_guard": WIN_GUARD, "t_min": T_MIN, "top_n": TOP_N}}
    if df.empty:
        res["contest"] = None
        res["note"] = "검증할 과거 표본이 부족합니다."
        return res
    dates = sorted(df["date"].unique())
    cut = dates[int(len(dates) * 2 / 3)]
    train, hold = df[df["date"] < cut], df[df["date"] >= cut]
    cands = {"현재": cur, "기본": default}
    for i, nb in enumerate(neighbors(cur)):
        cands[f"후보{i + 1}"] = nb
    ev = {k: (evaluate(train, w), evaluate(hold, w), evaluate(df, w)) for k, w in cands.items()}
    st = {k: {"train": _stat(a), "hold": _stat(b), "all": _stat(c), "weights": cands[k]} for k, (a, b, c) in ev.items()}
    base = st["현재"]
    chosen, why = "현재", "어느 후보도 학습·검증 두 구간에서 확실히 낫지 않아 유지"

    def better(k):
        s, b = st[k], base
        if not (s["hold"] and b["hold"] and s["train"] and b["train"]):
            return False
        return (s["hold"]["excess"] - b["hold"]["excess"] >= MARGIN_EX
                and s["train"]["excess"] > b["train"]["excess"]
                and s["hold"]["win_rate"] >= b["hold"]["win_rate"] - WIN_GUARD
                and _diff_t(ev[k][2], ev["현재"][2]) >= T_MIN)

    if cur != default and better("기본"):
        chosen, why = "기본", "기본 가중치가 학습·검증 구간 모두에서 더 좋아 기본값 쪽으로 롤백"
    else:
        best = None
        for k in cands:
            if k.startswith("후보") and better(k):
                if best is None or st[k]["hold"]["excess"] > st[best]["hold"]["excess"]:
                    best = k
        if best:
            chosen = best
            why = (f"검증 구간 초과수익 {st[best]['hold']['excess']:+.2f}%p(현재 {base['hold']['excess']:+.2f}%p), "
                   f"승률 {st[best]['hold']['win_rate']:.0f}%(현재 {base['hold']['win_rate']:.0f}%)로 개선")
    new = cur if chosen == "현재" else _toward(cur, cands[chosen])
    if new != cur:
        res["weights"], res["changed"], res["version"] = new, True, res["version"] + 1
        logs.insert(0, {"date": today, "text": "배당 점수 가중치 조정: " + ", ".join(
            f"{NAME[k]} {cur[k] * 100:.0f}→{new[k] * 100:.0f}%" for k in KEYS if abs(new[k] - cur[k]) > 1e-9) + f" ({why})"})
    res["contest"] = {"chosen": chosen, "why": why, "cut": pd.Timestamp(cut).strftime("%Y-%m"), "scores": st}
    entry = {"week": today, "version": res["version"], **{f"w_{k}": res["weights"][k] for k in KEYS},
             "hold_excess": (st["현재"]["hold"] or {}).get("excess"), "hold_win": (st["현재"]["hold"] or {}).get("win_rate"),
             "all_excess": (st["현재"]["all"] or {}).get("excess"), "all_win": (st["현재"]["all"] or {}).get("win_rate")}
    res["history"] = [h for h in hist if h["week"] != today][-103:] + [entry]
    res["log"] = logs[:40]
    return res


NAME = {"yield": "배당수익률", "growth": "배당성장", "stability": "무삭감"}


def run(close_map: dict, div_map: dict, cands: list[dict], cfg: dict, prev: dict | None, today: str | None = None) -> dict:
    """close_map/div_map: 종목→일별 종가/배당. cands: dividend.json 후보(ticker, type, covered_call, market)."""
    today = today or date.today().isoformat()
    tax = cfg["tax"]
    cap = cfg["max_yield_cap"]
    bench_v = {}
    for mk, (bt, _) in BENCH.items():
        if bt in close_map:
            bench_v[mk] = drip_value(close_map[bt], div_map.get(bt), tax[mk])
    tickers, series, metas = {}, {}, {}
    for c in cands:
        tk = c["ticker"]
        close = close_map.get(tk)
        if close is None or len(close.dropna()) < 60:
            continue
        mk = market_of(tk)
        divs = div_map.get(tk)
        v = drip_value(close, divs, tax[mk])
        rw = rolling_win(v, bench_v.get(mk))
        tickers[tk] = {"hold": holding_stats(close, divs, tax[mk]), "roll": rw, "bench": BENCH[mk][1],
                       "since": close.dropna().index[0].strftime("%Y-%m-%d")}
        series[tk] = {"close": close.dropna(), "divs": divs, "value": v}
        metas[tk] = {"type": c.get("type"), "covered_call": c.get("covered_call")}
    df = strategy_samples(series, metas, tax, cap)
    model = calibrate(df, prev, today)
    bench_stats = {mk: {"name": BENCH[mk][1], "hold": holding_stats(close_map[BENCH[mk][0]], div_map.get(BENCH[mk][0]), tax[mk])}
                   for mk in BENCH if BENCH[mk][0] in close_map}
    log.info("배당 백테스트: %d종목, 전략 표본 %d건, 가중치 %s%s", len(tickers), len(df), model["weights"],
             " (변경)" if model["changed"] else "")
    return {"tickers": tickers, "bench": bench_stats, "model": model,
            "samples": int(len(df)), "method": ("세후 배당을 배당락일 종가로 같은 종목에 재투자(스노우볼 백테스트와 같은 방식). "
                                                "가격은 분할 반영 종가, 배당은 Yahoo Finance. 수수료·양도세는 빼지 않음.")}
