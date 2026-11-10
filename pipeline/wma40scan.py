"""40주선 돌파·이탈 스캐너 — 유망 국내 100·미국 100 기업 중 40주 이동평균선을 막 넘거나 깨뜨린 종목.

방법(2026-11-10 사용자 요청)
- 주봉 종가(주 마지막 거래일, 진행 중인 주는 현재가)로 40주 이동평균을 계산 (technical.wma40과 같은 기준)
- 최근 3주(이번 주 포함) 안에 주봉 종가가 40주선을 아래→위로 넘으면 '돌파', 위→아래로 내려가면 '이탈'.
  넘은 뒤 현재가가 다시 반대편으로 돌아갔으면 제외
- 거래량: 넘은 주의 하루 평균 거래량 ÷ 직전 10주 하루 평균(와인스타인: 돌파는 거래량 동반이 중요)
- 유망도(0~100) = 같은 시장 100개 안에서의 퀄리티 35% + 가치 30% + 상승여력 20% + 성장 15% 백분위 점수
  → 유망도 55 이상만 안내. 차트(40주선) 정보와 기업 체력(유망도)을 따로 보여 줌
- 감지한 신호는 state/wma40_events.json 에 기록하고 20거래일 뒤 지수 대비 성과를 채점(효과 검증용)
"""
from __future__ import annotations

import math

import pandas as pd

from .technical import STAGE_LABEL, WMA_SLOPE_WEEKS, WMA_WEEKS

LOOKBACK_WEEKS = 3
VOL_WEEKS = 10
PROMISE_MIN = 55.0
PROMISE_W = {"quality": .35, "value": .30, "target": .20, "growth": .15}
LIST_MAX = 15
SCORE_DAYS = 20
BENCH = {"kr": "^KS11", "us": "^GSPC"}


def _weekly(df: pd.DataFrame) -> pd.DataFrame:
    idx = pd.DatetimeIndex(df.index)
    g = df.groupby(idx.to_period("W-FRI"))
    vol = df["Volume"].astype(float) if "Volume" in df else pd.Series(0.0, index=df.index)
    return pd.DataFrame({"close": g["Close"].last().astype(float), "vol": vol.groupby(idx.to_period("W-FRI")).mean(),  # 하루 평균 → 진행 중인 주도 공정 비교
                         "last_day": pd.Series(idx, index=idx).groupby(idx.to_period("W-FRI")).last()})


def cross_event(df: pd.DataFrame, lookback: int = LOOKBACK_WEEKS) -> dict | None:
    """최근 lookback주 안의 가장 최근 40주선 교차. 없거나 되돌아갔으면 None. 시점 t까지의 데이터만 사용."""
    if df is None or len(df) < 30:
        return None
    w = _weekly(df.dropna(subset=["Close"]))
    if len(w) < WMA_WEEKS + WMA_SLOPE_WEEKS:
        return None
    w["ma"] = w["close"].rolling(WMA_WEEKS).mean()
    w = w.dropna(subset=["ma"])
    if len(w) < WMA_SLOPE_WEEKS + 2:
        return None
    side = (w["close"] >= w["ma"]).astype(int)
    last = len(w) - 1
    for k in range(lookback):  # k주 전에 교차
        i = last - k
        if i < 1 or side.iloc[i] == side.iloc[i - 1]:
            continue
        kind = "up" if side.iloc[i] == 1 else "down"
        if side.iloc[last] != side.iloc[i]:  # 넘은 뒤 다시 되돌아감
            return None
        prior = w["vol"].iloc[max(0, i - VOL_WEEKS):i]
        vr = float(w["vol"].iloc[i] / prior.mean()) if len(prior) and prior.mean() > 0 else None
        ma_now, ma_prev = float(w["ma"].iloc[-1]), float(w["ma"].iloc[-1 - WMA_SLOPE_WEEKS])
        slope = (ma_now / ma_prev - 1) * 100
        price = float(df["Close"].dropna().iloc[-1])
        return {"kind": kind, "weeks_ago": k, "week": pd.Timestamp(w["last_day"].iloc[i]).strftime("%Y-%m-%d"),
                "vol_ratio": round(vr, 2) if vr is not None and math.isfinite(vr) else None,
                "ma": ma_now, "dist": round((price / ma_now - 1) * 100, 1), "slope": round(slope, 2),
                "price": price}
    return None


def breadth(hist: dict, tickers: list[str]) -> float | None:
    """40주선 위에 있는 종목 비율(%) — 와인스타인식 시장 체력 지표."""
    up = n = 0
    for t in tickers:
        h = hist.get(t)
        if h is None or len(h) < 30:
            continue
        w = _weekly(h.dropna(subset=["Close"]))
        if len(w) < WMA_WEEKS:
            continue
        ma = w["close"].rolling(WMA_WEEKS).mean().iloc[-1]
        n += 1
        up += int(w["close"].iloc[-1] >= ma)
    return round(up / n * 100, 1) if n else None


def promise(row: dict) -> float | None:
    tot = wsum = 0.0
    for k, w in PROMISE_W.items():
        v = row.get(k)
        if v is not None and not (isinstance(v, float) and math.isnan(v)):
            tot += float(v) * w
            wsum += w
    if wsum < 0.6:  # 퀄리티·가치 등 핵심 자료가 부족하면 판단 보류
        return None
    return round(tot / wsum, 1)


def _flags(ev: dict) -> list[str]:
    f = []
    if ev.get("vol_ratio") is not None and ev["vol_ratio"] >= 1.5:
        f.append(f"거래량 {ev['vol_ratio']:.1f}배 동반")
    s = ev.get("slope")
    if s is not None:
        f.append("40주선 상승 중" if s > 0.5 else "40주선 하락 중" if s < -0.5 else "40주선 평평")
    if ev["kind"] == "up" and (ev.get("dist") or 0) > 15:
        f.append("40주선과 15% 넘게 벌어짐")
    return f


def _stage(ev: dict) -> int:
    above, s = ev["dist"] >= 0, ev["slope"]
    if above:
        return 2 if s > 0.5 else 1
    return 4 if s < -0.5 else 3 if s > 0.5 else 1


def scan(hist: dict, scored: pd.DataFrame, names: dict, lists: dict) -> dict:
    """lists: {"kr": [...100], "us": [...100]}. scored: 같은 200종목으로 계산한 score_universe 결과."""
    sc = {r["ticker"]: r for r in (scored.to_dict("records") if scored is not None and not scored.empty else [])}
    out = {"markets": {}}
    for mk, tickers in lists.items():
        ups, downs, all_up, all_down = [], [], 0, 0
        for t in tickers:
            ev = cross_event(hist.get(t))
            if not ev:
                continue
            all_up += ev["kind"] == "up"
            all_down += ev["kind"] == "down"
            r = sc.get(t) or {}
            pr = promise(r)
            stage = _stage(ev)
            item = {"ticker": t, "name": names.get(t) or r.get("name") or t, "market": mk, "sector": r.get("sector"),
                    **{k: ev[k] for k in ("kind", "week", "weeks_ago", "vol_ratio", "dist", "slope", "price")},
                    "ma": round(ev["ma"], 2), "stage": stage, "stage_label": STAGE_LABEL[stage],
                    "flags": _flags(ev), "promise": pr,
                    **{k: (round(float(r[k]), 1) if r.get(k) is not None and not pd.isna(r.get(k)) else None)
                       for k in ("value", "quality", "target", "growth", "wma40", "score_weekly")},
                    **{k: r.get(k) for k in ("fpe", "roe", "upside", "verdict", "setup_label")}}
            if pr is not None and pr >= PROMISE_MIN:
                (ups if ev["kind"] == "up" else downs).append(item)
        key = lambda x: (-(x["promise"] or 0), x["weeks_ago"])  # noqa: E731
        out["markets"][mk] = {"up": sorted(ups, key=key)[:LIST_MAX], "down": sorted(downs, key=key)[:LIST_MAX],
                              "n_cross_up": int(all_up), "n_cross_down": int(all_down), "n": len(tickers),
                              "above_pct": breadth(hist, tickers)}
    return out


# ── 신호 기록 · 20거래일 뒤 채점 ──
def update_events(events: list[dict], res: dict, hist: dict, today: str) -> list[dict]:
    seen = {(e["ticker"], e["kind"], e["week"]) for e in events}
    for mk, blk in res["markets"].items():
        b = hist.get(BENCH[mk])
        b_last = float(b["Close"].dropna().iloc[-1]) if b is not None and len(b) else None
        for kind in ("up", "down"):
            for it in blk[kind]:
                key = (it["ticker"], kind, it["week"])
                if key in seen:
                    continue
                seen.add(key)
                events.append({"date": today, "ticker": it["ticker"], "name": it["name"], "market": mk, "kind": kind,
                               "week": it["week"], "entry": it["price"], "bench_entry": b_last,
                               "promise": it["promise"], "vol_ratio": it["vol_ratio"], "slope": it["slope"]})
    for e in events:
        if e.get("final"):
            continue
        h, b = hist.get(e["ticker"]), hist.get(BENCH[e["market"]])
        if h is None or b is None or not e.get("entry") or not e.get("bench_entry"):
            continue
        after = b.index[b.index > pd.Timestamp(e["date"])]
        if len(after) < SCORE_DAYS:
            continue
        d_end = after[SCORE_DAYS - 1]
        px = h["Close"].dropna()
        px = px[px.index <= d_end]
        if not len(px):
            continue
        ret = (float(px.iloc[-1]) / e["entry"] - 1) * 100
        bret = (float(b["Close"].loc[d_end]) / e["bench_entry"] - 1) * 100
        ex = ret - bret
        e["final"] = {"ret": round(ret, 2), "ex": round(ex, 2), "days": SCORE_DAYS, "closed": d_end.strftime("%Y-%m-%d"),
                      # 돌파는 지수보다 더 오르면, 이탈은 지수보다 덜 오르면(경고가 맞으면) 적중
                      "hit": bool(ex > 0) if e["kind"] == "up" else bool(ex < 0)}
    return events[-2000:]


def event_summary(events: list[dict]) -> dict:
    out = {}
    for kind in ("up", "down"):
        f = [e["final"] for e in events if e["kind"] == kind and e.get("final")]
        out[kind] = {"n": len(f), "pending": sum(1 for e in events if e["kind"] == kind and not e.get("final")),
                     "hit_rate": round(sum(x["hit"] for x in f) / len(f) * 100, 1) if f else None,
                     "avg_ex": round(sum(x["ex"] for x in f) / len(f), 2) if f else None}
    return out
