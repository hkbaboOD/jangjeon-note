"""실전 예측 기록·채점 (forward test).

매일 아침 전 종목의 팩터 점수(가치·퀄리티·상승여력·성장·모멘텀·추세·타이밍)와 종합점수를 '예측'으로 저장하고,
5·20·60거래일이 지난 뒤 실제 수익률과 비교해 채점합니다. 백테스트와 달리 펀더멘털까지 포함한
'실제로 앱이 냈던 예측'의 성적이라, 시간이 갈수록 보정의 가장 중요한 근거가 됩니다.

- 진입 = 예측에 쓴 마지막 종가 다음 거래일 시가, 청산 = h거래일 뒤 종가 (미래 정보 미사용)
- 초과수익 = 같은 날·같은 시장 전체 평균 대비
- IC = 날짜별 (점수 순위 ↔ 이후 수익률 순위) 스피어만 상관의 평균, t값은 일별 표본 중첩 보정(유효 날짜 = 날짜수/h)
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

from .common import STATE_DIR, market_of, read_json, write_json

FACTORS = ["value", "quality", "target", "growth", "momentum", "trend", "timing", "revision", "geo"]
SCORES = {"daily": ("score_daily", 5), "weekly": ("score_weekly", 20), "monthly": ("score_monthly", 60)}
HORIZONS = (5, 20, 60)
SNAP_DIR = STATE_DIR / "snapshots"
KEEP = 600  # 보관할 스냅샷 수(약 2.5년)


def _r(v, nd=1):
    try:
        f = float(v)
        return None if math.isnan(f) else round(f, nd)
    except (TypeError, ValueError):
        return None


def save_snapshot(scored: pd.DataFrame, date: str, px_dates: dict[str, str], snap_dir: Path | None = None) -> Path | None:
    """오늘의 예측 저장. px_dates: 티커 → 점수 계산에 쓴 마지막 봉 날짜."""
    if scored is None or scored.empty:
        return None
    d = snap_dir or SNAP_DIR
    d.mkdir(parents=True, exist_ok=True)
    rows = []
    for rec in scored.to_dict("records"):
        t = rec["ticker"]
        if not px_dates.get(t):
            continue
        rows.append({"t": t, "pxd": px_dates[t], "setup": rec.get("setup"),
                     "f": {k: _r(rec.get(k)) for k in FACTORS},
                     "s": {h: _r(rec.get(col)) for h, (col, _) in SCORES.items()}})
    path = d / f"{date}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"date": date, "rows": rows}, f, ensure_ascii=False, separators=(",", ":"))
    files = sorted(d.glob("*.json"))
    for old in files[:-KEEP]:
        old.unlink()
    return path


def load_snapshots(snap_dir: Path | None = None) -> pd.DataFrame:
    d = snap_dir or SNAP_DIR
    recs = []
    for p in sorted(d.glob("*.json")):
        try:
            js = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        for r in js.get("rows", []):
            recs.append({"date": js["date"], "ticker": r["t"], "pxd": r["pxd"], "setup": r.get("setup"),
                         **{k: r["f"].get(k) for k in FACTORS}, **{f"s_{h}": r["s"].get(h) for h in SCORES}})
    df = pd.DataFrame(recs)
    if df.empty:
        return df
    # 같은 종목·같은 가격 봉(pxd)으로 두 번 기록된 예측(토요일 코드 반영 실행 등)은 먼저 기록된 것만 채점
    # → 같은 결과가 두 번 세어져 IC 표본·t값이 부풀려지는 것을 막음
    return df.sort_values("date", kind="stable").drop_duplicates(["ticker", "pxd"], keep="first").reset_index(drop=True)


def attach_returns(snaps: pd.DataFrame, hist: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """각 예측에 이후 실제 수익률(r5/r20/r60)과 초과수익(x5/x20/x60)을 붙임. 아직 기간이 안 지난 건 NaN."""
    if snaps.empty:
        return snaps
    out = []
    for t, g in snaps.groupby("ticker"):
        df = hist.get(t)
        g = g.copy()
        for h in HORIZONS:
            g[f"r{h}"] = np.nan
        if df is not None and len(df) > 2:
            idx = df.index.normalize()
            o, c = df["Open"].astype(float).values, df["Close"].astype(float).values
            pos = idx.get_indexer(pd.to_datetime(g["pxd"]).dt.normalize())
            for h in HORIZONS:
                vals = []
                for i in pos:
                    if i < 0 or i + h >= len(df):
                        vals.append(np.nan)
                        continue
                    entry = o[i + 1] if o[i + 1] > 0 else c[i]
                    vals.append((c[i + h] / entry - 1) * 100)
                g[f"r{h}"] = vals
        g["market"] = market_of(t)
        out.append(g)
    res = pd.concat(out, ignore_index=True)
    for h in HORIZONS:
        res[f"x{h}"] = res[f"r{h}"] - res.groupby(["date", "market"])[f"r{h}"].transform("mean")
    return res


def date_ics(df: pd.DataFrame, score: pd.Series, h: int) -> pd.Series:
    """날짜별 IC 시계열 (date-market별 상관을 날짜로 평균)."""
    sub = pd.DataFrame({"date": df["date"], "market": df["market"], "s": score, "r": df[f"r{h}"]}).dropna()
    vals = {}
    for (d, _), g in sub.groupby(["date", "market"]):
        if len(g) >= 8 and g["s"].nunique() > 2:
            v = g["s"].rank().corr(g["r"].rank())
            if not np.isnan(v):
                vals.setdefault(d, []).append(v)
    return pd.Series({d: float(np.mean(v)) for d, v in vals.items()}).sort_index()


def ic_stat(series: pd.Series, h: int) -> dict | None:
    s = series.dropna()
    if len(s) < 5:
        return None
    n_eff = max(1.0, len(s) / h)
    sd = s.std(ddof=1)
    t = float(s.mean() / (sd / math.sqrt(n_eff))) if sd and sd > 0 else 0.0
    return {"ic": round(float(s.mean()), 4), "t": round(t, 2), "dates": int(len(s)), "n_eff": round(n_eff, 1)}


def composite(df: pd.DataFrame, weights: dict) -> pd.Series:
    """저장된 팩터 점수로 주어진 가중치의 종합점수를 재계산 (결측은 50 = 중립, 앱 채점과 동일)."""
    tot = pd.Series(0.0, index=df.index)
    for k, w in weights.items():
        if k in df:
            tot += df[k].astype(float).fillna(50.0) * w
    return tot


def evaluate(df: pd.DataFrame) -> dict:
    """채점 결과 요약: 팩터별 실전 IC, 기간별 종합점수 IC, Top3 초과수익."""
    if df.empty:
        return {"ok": False, "days": 0}
    res = {"ok": True, "days": int(df["date"].nunique()), "first": df["date"].min(), "last": df["date"].max(),
           "matured": {}, "ic": {}, "composite": {}, "top3": {}}
    for h in HORIZONS:
        res["matured"][str(h)] = int(df.dropna(subset=[f"r{h}"])["date"].nunique())
    for f in FACTORS:
        res["ic"][f] = {}
        for h in HORIZONS:
            st = ic_stat(date_ics(df, df[f], h), h)
            if st:
                res["ic"][f][str(h)] = st
    for name, (col, h) in SCORES.items():
        k = f"s_{name}"
        st = ic_stat(date_ics(df, df[k], h), h)
        if st:
            res["composite"][name] = st
        sub = df.dropna(subset=[f"x{h}", k])
        if not sub.empty:
            top = sub.sort_values(k, ascending=False).groupby(["date", "market"]).head(3)
            per_date = top.groupby("date")[f"x{h}"].mean()
            if len(per_date) >= 5:
                n_eff = max(1.0, len(per_date) / h)
                sd = per_date.std(ddof=1)
                res["top3"][name] = {"avg_ex": round(float(per_date.mean()), 2), "dates": int(len(per_date)),
                                     "t": round(float(per_date.mean() / (sd / math.sqrt(n_eff))), 2) if sd and sd > 0 else 0.0,
                                     "win": round(float((per_date > 0).mean() * 100), 1)}
    return res


def update_scorecard(ev: dict, model_version: int | None, date: str, window: int = 60) -> dict:
    """주간 예측력 기록(최근 window 날짜 기준) — 예측력이 실제로 좋아지는지 추적."""
    sc = read_json("scorecard.json", default={"history": []}) or {"history": []}
    entry = {"date": date, "version": model_version, "days": ev.get("days", 0)}
    for name in SCORES:
        c = (ev.get("recent") or {}).get(name) or ev.get("composite", {}).get(name)
        if c:
            entry[f"ic_{name}"] = c["ic"]
        t3 = ev.get("top3", {}).get(name)
        if t3:
            entry[f"top3_{name}"] = t3["avg_ex"]
    sc["history"] = [e for e in sc["history"] if e["date"] != date] + [entry]
    sc["history"] = sc["history"][-156:]  # 3년치 주간 기록
    sc["window"] = window
    write_json("scorecard.json", sc)
    return sc
