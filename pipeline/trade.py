"""매매 판단 검증 — '추천을 실제로 샀다면'이 아니라 '내가 사고팔았다면'으로 채점합니다 (2026-11-25 사용자 요청).

추천마다 추천 시점의 차트 자료만으로 매매 계획을 세웁니다(미래 데이터 미사용).
- 지금 매수: 1차 목표까지 기대폭 ÷ 손절폭(손익비) ≥ 1.5, 손절폭 ≤ 12%, 과열(RSI 70↑ 또는 20일 이격도 108↑) 아님
  → 다음 거래일 시가에 매수
- 기다렸다 매수: 지금 값으로는 손익비가 모자라거나 과열이지만, 가까운 지지선(−8% 이내, 손절가 위)까지 내려오면
  손익비 ≥ 1.5 → 그 가격에 지정가 주문, 정해진 거래일 안에 체결되지 않으면 '미체결'(거래 없음)
- 보류(자신 없음): 게이트 완화로 들어온 추천, 40주선 4단계, 역배열 하락추세, 손절 기준 없음, 손익비 부족
  → 사지 않았으므로 승률에 넣지 않음

청산: 손절가(장중 저가가 닿으면, 갭 하락이면 시가) / 목표가(장중 고가가 닿으면, 갭 상승이면 시가) /
보유 기간(오늘 5·이번 주 20·이번 달 60거래일) 종가 중 먼저 오는 것. 같은 날 손절과 목표가 모두 닿으면 손절로 봄(보수적).
왕복 비용 차감(한국 0.25%, 미국 0.05%). 지수 대비 초과수익 = 같은 보유 기간 지수 수익률과의 차이.
"""
from __future__ import annotations

import math
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

KST, NY = ZoneInfo("Asia/Seoul"), ZoneInfo("America/New_York")
MIN_RR = 1.5
MAX_STOP = 12.0       # 손절폭 상한(%)
MAX_WAIT_DIST = 8.0   # 지정가는 현재가 대비 −8% 이내 지지선만
WAIT_DAYS = {"daily": 3, "weekly": 5, "monthly": 10}
HOLD = {"daily": 5, "weekly": 20, "monthly": 60}
COST = {"kr": 0.25, "us": 0.05}
RULE_TEXT = (f"손익비 {MIN_RR} 이상·손절폭 {MAX_STOP:.0f}% 이하·과열 아님 → 지금 매수 / 지지선(−{MAX_WAIT_DIST:.0f}% 이내)에서 "
             f"손익비 {MIN_RR} 이상 → 지정가 대기(오늘 {WAIT_DAYS['daily']}·이번 주 {WAIT_DAYS['weekly']}·이번 달 "
             f"{WAIT_DAYS['monthly']}거래일) / 그 밖은 보류(채점 안 함)")


def _num(x):
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(x) else x


def _fmt(px: float) -> str:
    return f"{px:,.0f}" if px >= 1000 else f"{px:,.2f}"


def _target(res: list[dict], entry: float, stop: float, horizon: str) -> tuple[float, str]:
    """목표가: 오늘은 1차 저항, 이번 주·달은 2차 저항(없으면 1차). 진입가 위 1% 이상인 저항만. 없으면 2R."""
    above = [r for r in res if _num(r.get("price")) and r["price"] > entry * 1.01]
    if above:
        r = above[0] if horizon == "daily" or len(above) == 1 else above[1]
        return float(r["price"]), r.get("label") or "저항선"
    return entry + 2 * (entry - stop), "손절폭 2배"


def plan(tx: dict | None, price, horizon: str, relaxed: bool = False, stage40=None, asof: str | None = None) -> dict:
    """추천 시점 차트 분석(tx: technical.analyze 결과 또는 추천의 techx)으로 매매 계획."""
    price = _num(price)
    base = {"horizon": horizon, "hold": HOLD.get(horizon, 20), "asof": asof}
    tx = tx or {}
    lv = tx.get("levels") or {}
    vals = tx.get("values") or {}
    stop = _num((lv.get("stop") or {}).get("price"))
    skip = None
    if not price or not tx or tx.get("setup") in (None, "insufficient"):
        skip = "차트 자료 부족"
    elif relaxed:
        skip = "차트 조건 미달로 완화해 넣은 추천"
    elif stage40 == 4 or ((tx.get("wma40") or {}).get("stage") == 4):
        skip = "40주선 4단계(하락 국면)"
    elif tx.get("setup") == "downtrend":
        skip = "역배열 하락추세"
    elif not stop or stop >= price:
        skip = "손절 기준을 잡을 수 없음"
    if skip:
        return {**base, "action": "skip", "reason": skip}
    sup, res = lv.get("support") or [], lv.get("resistance") or []
    rsi, disp = _num(vals.get("rsi")), _num(vals.get("disparity"))
    hot = (rsi is not None and rsi >= 70) or (disp is not None and disp >= 108)
    stop_pct = (price - stop) / price * 100

    def rr_at(entry):
        tgt, lab = _target(res, entry, stop, horizon)
        return (tgt - entry) / (entry - stop), tgt, lab

    rr_now, tgt_now, lab_now = rr_at(price)
    if rr_now >= MIN_RR and stop_pct <= MAX_STOP and not hot:
        return {**base, "action": "buy", "entry": round(price, 4), "stop": round(stop, 4), "target": round(tgt_now, 4),
                "target_label": lab_now, "rr": round(rr_now, 2),
                "reason": f"손익비 {rr_now:.1f}, 손절폭 {stop_pct:.1f}% — 지금 값에서 사도 됨"}
    for s in sup:
        px = _num(s.get("price"))
        if not px or px <= stop * 1.005 or px >= price or (price - px) / price * 100 > MAX_WAIT_DIST:
            continue
        rr_s, tgt_s, lab_s = rr_at(px)
        if rr_s >= MIN_RR and (px - stop) / px * 100 <= MAX_STOP:
            why = "과열" if hot else (f"지금 값은 손익비 {math.floor(rr_now * 10) / 10:.1f}" if rr_now < MIN_RR
                                     else f"손절폭 {stop_pct:.1f}%로 큼")
            return {**base, "action": "wait", "limit": round(px, 4), "limit_label": s.get("label"),
                    "wait_days": WAIT_DAYS.get(horizon, 5), "stop": round(stop, 4), "target": round(tgt_s, 4),
                    "target_label": lab_s, "rr": round(rr_s, 2),
                    "reason": f"{why} — {s.get('label') or '지지선'} {_fmt(px)}까지 내려오면 매수(손익비 {rr_s:.1f})"}
    if hot:
        why = "과열이고 받쳐 줄 지지선이 멀어"
    elif rr_now < MIN_RR:
        why = f"손익비 {math.floor(rr_now * 10) / 10:.1f}에 그치고 가까운 지지선에서도 {MIN_RR}이 안 돼"
    else:
        why = f"손절폭 {stop_pct:.1f}%로 커서"
    return {**base, "action": "skip", "reason": f"{why} 자신 없음"}


def entry_pos(df: pd.DataFrame, asof: str | None, date: str, ref_price=None) -> int | None:
    """진입 기준 봉 위치: 분석에 쓴 마지막 봉(asof) 다음 봉. asof가 없으면 추천일 이전에서 종가가 추천가와 같은 봉 다음,
    그것도 없으면 추천일 이후 첫 봉."""
    idx = df.index
    if asof:
        p = int(idx.searchsorted(pd.Timestamp(asof), side="right"))
        return p if p < len(idx) else None
    d = pd.Timestamp(date)
    ref = _num(ref_price)
    if ref:
        c = df["Close"].astype(float).values
        for i in range(int(idx.searchsorted(d, side="right")) - 1, max(-1, int(idx.searchsorted(d, side="right")) - 6), -1):
            if i >= 0 and abs(c[i] / ref - 1) < 0.003:
                return i + 1 if i + 1 < len(idx) else None
    p = int(idx.searchsorted(d, side="left"))
    return p if p < len(idx) else None


def simulate(pl: dict, df: pd.DataFrame, start: int | None, market: str, bench: pd.Series | None = None) -> dict:
    """계획대로 사고팔았을 때 결과. start = 진입 기준 봉 위치(다음 거래일)."""
    if pl.get("action") == "skip":
        return {"status": "skipped"}
    if start is None or start >= len(df):
        return {"status": "waiting" if pl["action"] == "wait" else "pending"}
    o = df["Open"].astype(float).values
    h = df["High"].astype(float).values
    lo = df["Low"].astype(float).values
    c = df["Close"].astype(float).values
    n = len(df)
    fix = lambda v, alt: v if v and v > 0 and not math.isnan(v) else alt  # noqa: E731
    if pl["action"] == "buy":
        fi, fill = start, fix(o[start], c[start])
    else:
        fi = fill = None
        for i in range(start, min(n, start + pl["wait_days"])):
            if fix(lo[i], c[i]) <= pl["limit"]:
                fi, fill = i, min(pl["limit"], fix(o[i], c[i]))
                break
        if fi is None:
            return {"status": "unfilled" if start + pl["wait_days"] <= n else "waiting"}
    stop, tgt, hold = pl["stop"], pl["target"], pl["hold"]
    ex_i = ex_px = why = None
    for i in range(fi, min(n, fi + hold)):
        oi = fix(o[i], c[i])
        lo_i, hi_i = fix(lo[i], c[i]), fix(h[i], c[i])
        first = i == fi
        if (not first or pl["action"] == "buy") and oi <= stop:
            ex_i, ex_px, why = i, oi, "손절(갭 하락)"
            break
        if lo_i <= stop:  # 지정가 체결 당일에 손절가까지 밀렸으면 순서를 알 수 없으므로 손절로 봄(보수적)
            ex_i, ex_px, why = i, stop, "손절"
            break
        if first and pl["action"] == "wait":  # 체결 당일 목표가 도달은 체결 뒤인지 알 수 없어 인정하지 않음
            continue
        if not first and oi >= tgt:
            ex_i, ex_px, why = i, oi, "목표가(갭 상승)"
            break
        if hi_i >= tgt:
            ex_i, ex_px, why = i, tgt, "목표가"
            break
    if ex_i is None:
        last = fi + hold - 1
        if last >= n:
            cur = (c[-1] / fill - 1) * 100
            return {"status": "open", "fill_date": df.index[fi].strftime("%Y-%m-%d"), "fill": round(fill, 4),
                    "cur": round(cur, 2), "days": n - fi}
        ex_i, ex_px, why = last, c[last], "보유 기간 만료"
    ret = (ex_px / fill - 1) * 100 - COST.get(market, 0.1)
    out = {"status": "closed", "fill_date": df.index[fi].strftime("%Y-%m-%d"), "fill": round(fill, 4),
           "exit_date": df.index[ex_i].strftime("%Y-%m-%d"), "exit": round(float(ex_px), 4), "why": why,
           "days": int(ex_i - fi + 1), "ret": round(float(ret), 2),
           "r": round(float((ex_px - fill) / (fill - stop)), 2) if fill > stop else None, "win": bool(ret > 0)}
    if bench is not None and len(bench):
        b = bench.astype(float)
        b0 = b.asof(df.index[fi] - pd.Timedelta(days=1)) if pl["action"] == "buy" else b.asof(df.index[fi])
        b1 = b.asof(df.index[ex_i])
        if _num(b0) and _num(b1):
            out["ex"] = round(float(ret - (b1 / b0 - 1) * 100), 2)
    return out


def complete_bars(df: pd.DataFrame, market: str, now) -> pd.DataFrame:
    """아직 끝나지 않은 오늘 장의 봉은 빼고 채점(장중 실행 시 '기간 만료'를 장중 값으로 확정하지 않도록)."""
    if df is None or not len(df):
        return df
    if market == "kr":
        local = now.astimezone(KST) if now.tzinfo else now
        if local.hour * 60 + local.minute < 15 * 60 + 40:
            return df[df.index.normalize() < pd.Timestamp(local.date())]
        return df
    us = (now if now.tzinfo else now.replace(tzinfo=KST)).astimezone(NY)
    if us.weekday() < 5 and us.hour * 60 + us.minute < 16 * 60 + 10:
        return df[df.index.normalize() < pd.Timestamp(us.date())]
    return df


def score_items(items: list[dict], ohlc: dict, now, market_of) -> int:
    """계획이 있는 추천을 계획대로 채점. 끝난 거래(청산·미체결·보류)는 고정, 진행 중만 다시 계산. 채점한 건수 반환."""
    k = 0
    for i in items:
        pl = i.get("plan")
        if not pl or (i.get("trade") or {}).get("status") in ("closed", "unfilled", "skipped", "data_mismatch"):
            continue
        if pl.get("action") == "skip":
            i["trade"] = {"status": "skipped"}
            continue
        mk = market_of(i["ticker"])
        df = complete_bars(ohlc.get(i["ticker"]), mk, now)
        if df is None or not len(df):
            continue
        start = entry_pos(df, pl.get("asof"), i["date"], i.get("entry"))
        ref = _num(i.get("entry"))
        if start is not None and start > 0 and ref and abs(float(df["Close"].iloc[start - 1]) / ref - 1) > 0.2:
            i["trade"] = {"status": "data_mismatch"}  # 분할·자료 오류로 추천가와 가격 이력이 어긋나면 채점하지 않음
            continue
        b = complete_bars(ohlc.get(i.get("bench")), mk, now)
        i["trade"] = simulate(pl, df, start, mk, b["Close"] if b is not None and len(b) else None)
        k += 1
    return k


def summarize(items: list[dict]) -> dict:
    """기간별 매매 성과. 보류·미체결은 승률에 넣지 않음."""
    out = {}
    for hz in HOLD:
        its = [i for i in items if i.get("horizon") == hz and i.get("plan")]
        if not its:
            continue
        st = [((i.get("trade") or {}).get("status")) for i in its]
        closed = [i["trade"] for i in its if (i.get("trade") or {}).get("status") == "closed"]
        acts = [i["plan"]["action"] for i in its]
        waits_done = [s for i, s in zip(its, st) if i["plan"]["action"] == "wait" and s in ("closed", "open", "unfilled")]
        blk = {"plans": len(its), "buy": acts.count("buy"), "wait": acts.count("wait"), "skip": acts.count("skip"),
               "unfilled": st.count("unfilled"), "open": st.count("open"),
               "fill_rate": round(sum(s != "unfilled" for s in waits_done) / len(waits_done) * 100, 1) if waits_done else None,
               "closed": len(closed)}
        if closed:
            r = np.array([t["ret"] for t in closed])
            ex = [t["ex"] for t in closed if t.get("ex") is not None]
            rm = [t["r"] for t in closed if t.get("r") is not None]
            gain, loss = r[r > 0].sum(), -r[r < 0].sum()
            blk.update({"win_rate": round(float((r > 0).mean() * 100), 1), "avg_ret": round(float(r.mean()), 2),
                        "avg_ex": round(float(np.mean(ex)), 2) if ex else None,
                        "ex_win": round(float(np.mean([e > 0 for e in ex]) * 100), 1) if ex else None,
                        "avg_r": round(float(np.mean(rm)), 2) if rm else None,
                        "pf": round(float(gain / loss), 2) if loss > 0 else None,
                        "by_exit": {k: sum(t["why"].startswith(k) for t in closed) for k in ("목표가", "손절", "보유 기간")},
                        "by_action": {a: {"n": len(v), "win_rate": round(float(np.mean([t["win"] for t in v]) * 100), 1),
                                          "avg_ret": round(float(np.mean([t["ret"] for t in v])), 2)}
                                      for a in ("buy", "wait")
                                      for v in [[i["trade"] for i in its if i["plan"]["action"] == a
                                                 and (i.get("trade") or {}).get("status") == "closed"]] if v}})
            # 같은 추천을 '전부 다음 날 시가에 사서 기간 끝까지 보유'했을 때(기존 채점)와 비교
            base = [i["final"] for i in its if i.get("final")]
            if base:
                bex = [f["ex"] for f in base if f.get("ex") is not None]
                blk["all_picks"] = {"n": len(base), "win_rate": round(float(np.mean([f["win"] for f in base]) * 100), 1),
                                    "avg_ex": round(float(np.mean(bex)), 2) if bex else None}
        out[hz] = blk
    return out


def backtest_block(df: pd.DataFrame, i: int, ta: dict, market: str) -> dict:
    """백테스트 표본(시점 i까지의 분석 ta)에 같은 매매 규칙 적용 → 기간별 결과(지수 없이 절대수익)."""
    out = {}
    for hz, hd in HOLD.items():
        pl = plan(ta, float(df["Close"].iloc[i]), hz, stage40=(ta.get("wma40") or {}).get("stage"))
        tr = simulate(pl, df, i + 1, market)
        out[hz] = (pl["action"], tr.get("status"), tr.get("ret"))
    return out
