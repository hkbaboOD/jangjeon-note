"""밸류에이션 + 차트 분석 통합 스코어링 · Top3 추천 · 추천 이유 문장화 · 5단계 판정 · 급락 진단.

원칙
- 펀더멘털 팩터는 같은 시장(미국/한국) 안에서 백분위(0~100)로 환산 → 한국 저PER 구조 왜곡 방지
- 선행 PER은 같은 섹터 종목이 4개 이상이면 섹터 내 백분위, 아니면 시장 내 백분위
- 차트 팩터(추세·모멘텀·타이밍)는 technical.py의 절대 점수(0~100)
- 결측 팩터는 중립(50), 가중치 기준 데이터 커버리지 60% 미만이면 추천 후보에서 제외
- '저평가인데 하락추세(역배열)'는 가치 함정 가능성이 커서 추천에서 제외
- 점수는 상대 순위일 뿐 매수 신호가 아님
"""
from __future__ import annotations

import math

import pandas as pd

HORIZON_WEIGHTS = {
    # 오늘: 저평가 + 차트 매수 타이밍(과매도 탈출·지지 확인·반전 캔들 등)
    "daily":   {"value": .25, "quality": .15, "target": .15, "timing": .30, "trend": .15},
    # 이번 주: 가치·퀄리티 + 추세·모멘텀
    "weekly":  {"value": .30, "quality": .25, "target": .15, "momentum": .15, "trend": .15},
    # 이번 달: 중장기 펀더멘털 + 장기 추세
    "monthly": {"value": .30, "quality": .30, "target": .15, "growth": .15, "trend": .10},
}
HORIZON_LABEL = {"daily": "저평가 + 차트 매수 타이밍", "weekly": "가치·퀄리티 + 추세",
                 "monthly": "중장기 저평가 + 장기 추세"}
# 차트 게이트 (부족하면 완화)
TECH_GATE = {"daily": ("timing", 55), "weekly": ("trend", 45), "monthly": ("trend", 40)}

VERDICTS = [(75, "강한 매수"), (60, "매수"), (40, "중립"), (25, "매도"), (-1, "강한 매도")]


def _pct_rank(s: pd.Series, higher_better: bool) -> pd.Series:
    return s.rank(pct=True, ascending=higher_better) * 100


def score_universe(rows: list[dict], model: dict | None = None) -> pd.DataFrame:
    """rows: [{ticker, market, name, fund{...}, tech{..., ta{...}}}] → 팩터·종합 점수 DataFrame.

    model: 자가 보정 결과(model.json). weights(기간별 가중치)·setup_adj(셋업 가감점)가 있으면 사용."""
    weights = (model or {}).get("weights") or HORIZON_WEIGHTS
    setup_adj = (model or {}).get("setup_adj") or {}
    recs = []
    for r in rows:
        f, t = r.get("fund") or {}, r.get("tech") or {}
        ta = t.get("ta") or {}
        sc = ta.get("scores") or {}
        price = t.get("last") or f.get("price")
        tgt = f.get("target_mean")
        recs.append({
            "ticker": r["ticker"], "market": r["market"], "name": r.get("name") or f.get("name") or r["ticker"],
            "sector": f.get("sector") or "기타", "price": price,
            "fpe": f.get("forward_pe") if (f.get("forward_pe") or 0) > 0 else None,
            "peg": f.get("peg") if f.get("peg") and 0 < f["peg"] <= 5 else None,
            "pb": f.get("pb") if (f.get("pb") or 0) > 0 else None,
            "roe": min(f["roe"], 0.6) if f.get("roe") is not None else None,
            "opm": f.get("op_margin"), "de": f.get("debt_to_equity"),
            "eg": max(min(f["earnings_growth"], 1.0), -0.5) if f.get("earnings_growth") is not None else None,
            "upside": (tgt / price - 1) * 100 if tgt and price else None,
            "analysts": f.get("analyst_count") or 0, "rec_mean": f.get("rec_mean"), "rec_key": f.get("rec_key"),
            "rsi": t.get("rsi14"), "above_long": t.get("above_ma_long"), "dd52": t.get("drawdown_52w"),
            "chg_1d": t.get("chg_1d"), "chg_5d": t.get("chg_5d"), "chg_20d": t.get("chg_20d"),
            "chg_60d": t.get("chg_60d"), "spark": t.get("spark"),
            "t_trend": sc.get("trend"), "t_mom": sc.get("momentum"), "t_timing": sc.get("timing"),
            "tech": sc.get("total"), "setup": ta.get("setup"), "setup_label": ta.get("setup_label"),
            "trend_label": ta.get("trend_label"), "tags": ta.get("tags") or [], "_ta": ta,
        })
    df = pd.DataFrame(recs)
    if df.empty:
        return df

    parts = []
    for _, g in df.groupby("market"):
        g = g.copy()
        g["s_fpe"] = _pct_rank(g["fpe"], higher_better=False)
        for _, gs in g.groupby("sector"):
            if gs["fpe"].notna().sum() >= 4:
                g.loc[gs.index, "s_fpe"] = _pct_rank(gs["fpe"], higher_better=False)
        g["sector_fpe_median"] = g.groupby("sector")["fpe"].transform("median")
        g["market_fpe_median"] = g["fpe"].median()
        g["s_peg"] = _pct_rank(g["peg"], False)
        g["s_pb"] = _pct_rank(g["pb"], False)
        g["s_roe"] = _pct_rank(g["roe"], True)
        g["s_opm"] = _pct_rank(g["opm"], True)
        g["s_de"] = _pct_rank(g["de"], False)
        shrink = (g["analysts"].clip(0, 10) / 10).fillna(0)  # 커버리지 적으면 목표가 신뢰도 축소
        g["s_upside"] = 50 + (_pct_rank(g["upside"], True) - 50) * shrink
        g["s_rec"] = _pct_rank(g["rec_mean"], False)
        g["s_eg"] = _pct_rank(g["eg"], True)
        g["s_mom"] = _pct_rank(g["chg_60d"], True)
        parts.append(g)
    df = pd.concat(parts)

    def wavg(row, cols_w):
        tot = w_have = 0.0
        for c, w in cols_w:
            v = row[c]
            if pd.notna(v):
                tot += v * w
                w_have += w
        return (tot / w_have) if w_have else None, w_have / sum(w for _, w in cols_w)

    def val(x):
        return None if x is None or pd.isna(x) else float(x)

    out = []
    for _, r in df.iterrows():
        value, cv = wavg(r, [("s_fpe", .55), ("s_peg", .25), ("s_pb", .20)])
        quality, cq = wavg(r, [("s_roe", .50), ("s_opm", .30), ("s_de", .20)])
        upside, cu = wavg(r, [("s_upside", .75), ("s_rec", .25)])
        growth = val(r["s_eg"])
        mom_parts = [x for x in (val(r["s_mom"]), val(r["t_mom"])) if x is not None]
        momentum = sum(mom_parts) / len(mom_parts) if mom_parts else None
        factors = {"value": value, "quality": quality, "target": upside, "growth": growth,
                   "momentum": momentum, "trend": val(r["t_trend"]), "timing": val(r["t_timing"])}
        coverage = {"value": cv, "quality": cq, "target": cu}
        coverage.update({k: 1.0 if factors[k] is not None else 0.0 for k in ("growth", "momentum", "trend", "timing")})
        row = r.to_dict()
        row.update({k: (round(v, 1) if v is not None else None) for k, v in factors.items()})
        for h in HORIZON_WEIGHTS:
            ws = weights.get(h) or HORIZON_WEIGHTS[h]
            s = cov = 0.0
            for k, w in ws.items():
                s += (factors.get(k) if factors.get(k) is not None else 50.0) * w
                cov += coverage.get(k, 0.0) * w
            adj = float((setup_adj.get(h) or {}).get(row.get("setup"), 0.0))  # 백테스트 기반 셋업 가감점
            row[f"score_{h}"] = round(s + adj, 1)
            row[f"adj_{h}"] = adj
            row[f"cov_{h}"] = round(cov, 2)
        row["verdict"] = verdict(row["score_weekly"], row)
        out.append(row)
    return pd.DataFrame(out)


def verdict(score: float, r) -> str:
    idx = next(i for i, (th, _) in enumerate(VERDICTS) if score >= th)
    if r.get("setup") == "downtrend":  # 역배열 하락추세면 한 단계 하향
        idx = min(idx + 1, len(VERDICTS) - 1)
    return VERDICTS[idx][1]


def _fmt(v, nd=1, suffix=""):
    return "-" if v is None or pd.isna(v) else f"{v:,.{nd}f}{suffix}"


def _clean_nan(r: dict) -> dict:
    """DataFrame 행의 NaN(참으로 평가됨)을 None으로 — 'PEG -' 같은 빈 근거 방지."""
    return {k: (None if isinstance(v, float) and math.isnan(v) else v) for k, v in r.items()}


def explain_fund(r: dict) -> tuple[list[str], list[str]]:
    """펀더멘털 근거·리스크."""
    r = _clean_nan(r)
    why, risk = [], []
    if r.get("fpe"):
        med = r.get("sector_fpe_median") or r.get("market_fpe_median")
        rel = f" (섹터 중앙값 {_fmt(med)}배 대비 {(r['fpe'] / med - 1) * 100:+.0f}%)" if med else ""
        why.append(f"선행 PER {_fmt(r['fpe'])}배{rel}")
    if r.get("peg"):
        (why if r["peg"] < 1.2 else risk).append(f"PEG {_fmt(r['peg'], 2)}")
    if r.get("roe") is not None:
        (why if r["roe"] >= 0.12 else risk).append(f"ROE {_fmt(r['roe'] * 100)}%")
    if r.get("upside") is not None and r.get("analysts"):
        (why if r["upside"] >= 5 else risk).append(
            f"평균 목표가 대비 상승여력 {r['upside']:+.0f}% (애널리스트 {int(r['analysts'])}명)")
    if r.get("eg") is not None and (r["eg"] >= 0.1 or r["eg"] < 0):
        (why if r["eg"] >= 0.1 else risk).append(f"이익 증가율 {_fmt(r['eg'] * 100)}%")
    if r.get("de") is not None and r["de"] > 150:
        risk.append(f"부채비율(D/E) {_fmt(r['de'], 0)}%")
    if (r.get("analysts") or 0) < 5:
        risk.append("애널리스트 커버리지 적음 → 목표가 신뢰도 낮음")
    return why, risk


def _short(text: str) -> str:
    return text.split(" — ")[0]


def explain_tech(ta: dict) -> dict:
    sig = ta.get("signals") or []
    pos = sorted([s for s in sig if s["pol"] > 0], key=lambda s: -s["w"])
    neg = sorted([s for s in sig if s["pol"] < 0], key=lambda s: -s["w"])
    neu = [s for s in sig if s["pol"] == 0]
    return {
        "setup": ta.get("setup"), "setup_label": ta.get("setup_label"), "setup_desc": ta.get("setup_desc"),
        "trend_label": ta.get("trend_label"), "scores": ta.get("scores"),
        "pos": [s["text"] for s in pos[:5]], "neg": [s["text"] for s in neg[:4]], "neu": [s["text"] for s in neu[:2]],
        "levels": ta.get("levels"), "values": ta.get("values"),
    }


def summarize(r: dict, tx: dict) -> str:
    """한 문장 추천 이유: 왜 싸고(펀더멘털) + 왜 지금인가(차트) + 주의점."""
    r = _clean_nan(r)
    bits = []
    if r.get("fpe"):
        med = r.get("sector_fpe_median") or r.get("market_fpe_median")
        if med and r["fpe"] <= med * 0.95:
            bits.append(f"선행 PER {r['fpe']:.1f}배(섹터 대비 {(r['fpe'] / med - 1) * 100:+.0f}%)")
        else:
            bits.append(f"선행 PER {r['fpe']:.1f}배")
    if r.get("upside") is not None and (r.get("analysts") or 0) >= 3 and r["upside"] >= 10:
        bits.append(f"목표가 상승여력 {r['upside']:+.0f}%")
    elif r.get("roe") is not None and r["roe"] >= 0.15:
        bits.append(f"ROE {r['roe'] * 100:.0f}%")
    head = " · ".join(bits[:2])
    s = f"{head}의 저평가 종목." if head else "가치 점수 상위 종목."
    if tx.get("setup_label") and tx["setup"] not in ("neutral", "insufficient"):
        ev = ", ".join(_short(t) for t in tx["pos"][:2])
        s += f" 차트는 '{tx['setup_label']}'" + (f" — {ev}." if ev else ".")
    elif tx.get("pos"):
        s += f" 차트: {_short(tx['pos'][0])}."
    strong_neg = [t for t in tx.get("neg", [])][:1]
    if strong_neg:
        s += f" 단, {_short(strong_neg[0])} 유의."
    return s


def pick_top(df: pd.DataFrame, horizon: str, market: str | None = None, n: int = 3) -> list[dict]:
    if df.empty:
        return []
    d = df.copy()
    if market:
        d = d[d["market"] == market]
    # 저평가 게이트: 선행 PER 양수 + 가치점수 50 이상 + 상승여력 ≥ 0(또는 결측) + 커버리지
    d = d[d["fpe"].notna() & (d["value"].fillna(0) >= 50) & (d["upside"].isna() | (d["upside"] >= 0))]
    d = d[d[f"cov_{horizon}"] >= 0.6]
    d = d[d["setup"] != "downtrend"]  # 가치 함정 배제
    if horizon == "monthly":
        d = d[(d["roe"].isna() | (d["roe"] >= 0.08)) & (d["analysts"] >= 3)]
    col, th = TECH_GATE[horizon]
    strict = d[d[col].fillna(0) >= th]
    d = d.assign(_relaxed=~d.index.isin(strict.index))
    d = pd.concat([d[~d["_relaxed"]].sort_values(f"score_{horizon}", ascending=False),
                   d[d["_relaxed"]].sort_values(f"score_{horizon}", ascending=False)])
    picks, sectors = [], set()
    for _, r in d.iterrows():
        if r["sector"] in sectors:  # 섹터당 1종목
            continue
        rd = r.to_dict()
        why, risk = explain_fund(rd)
        tx = explain_tech(rd["_ta"])
        picks.append({**{k: rd.get(k) for k in (
            "ticker", "name", "market", "sector", "price", "fpe", "peg", "roe", "upside", "analysts",
            "rec_key", "rsi", "chg_5d", "chg_20d", "verdict", "value", "quality", "trend", "timing", "tech",
            "spark", "setup", "setup_label", "trend_label")},
            "score": rd[f"score_{horizon}"], "adj": rd.get(f"adj_{horizon}", 0.0), "why": why, "risk": risk, "techx": tx,
            "summary": summarize(rd, tx), "relaxed": bool(rd["_relaxed"])})
        sectors.add(r["sector"])
        if len(picks) >= n:
            break
    return picks


def diagnose_drop(fund: dict, prev: dict | None, row: dict) -> dict:
    """급락 종목의 매도신호 성격 진단: 펀더멘털성 vs 수급·심리성."""
    evidence = []
    if not prev:
        return {"type": "판단 보류", "evidence": ["직전 펀더멘털 스냅샷 없음(다음 실행부터 비교 가능)"]}
    fund_hits = 0
    t0, t1 = prev.get("target_mean"), fund.get("target_mean")
    if t0 and t1:
        ch = (t1 / t0 - 1) * 100
        evidence.append(f"평균 목표가 {ch:+.1f}%")
        fund_hits += ch <= -5
    p0, f0 = prev.get("price"), prev.get("forward_pe")
    p1, f1 = fund.get("price"), fund.get("forward_pe")
    if p0 and f0 and p1 and f1 and f0 > 0 and f1 > 0:
        eps_ch = ((p1 / f1) / (p0 / f0) - 1) * 100
        evidence.append(f"선행 EPS 추정치 {eps_ch:+.1f}%")
        fund_hits += eps_ch <= -3
    r0, r1 = prev.get("rec_mean"), fund.get("rec_mean")
    if r0 and r1:
        evidence.append(f"투자의견 평균 {r0:.2f}→{r1:.2f} (낮을수록 매수)")
        fund_hits += (r1 - r0) >= 0.3
    if fund_hits:
        kind = "펀더멘털성"
    elif (row.get("value") or 0) >= 50 and (row.get("upside") or 0) >= 10:
        kind = "수급·심리성"
        evidence.append("이익 추정·목표가 유지 + 밸류에이션 저평가 구간 → 가격만 하락")
    else:
        kind = "수급·심리성(약)"
        evidence.append("추정치 변화 없으나 밸류에이션 매력은 제한적")
    return {"type": kind, "evidence": evidence}
