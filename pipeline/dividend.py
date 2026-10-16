"""배당주 분석 · 월간 배당 플랜.

- 후보별: TTM 배당수익률, 5년 DPS CAGR, 배당 성향, 배당 삭감 이력, 지급 월(배당락월) 패턴
- 점수: 수익률 40% · 성장 30% · 지속가능성(배당성향) 20% · 안정성(삭감 없음) 10%
- 플랜: 점수 + '월 커버리지'를 함께 고려해 종목 선택 → 매월 고르게 현금흐름이 들어오도록 구성
- 커버드콜 ETF(JEPI/JEPQ 등)는 원금 잠식·상승 제한 리스크로 합산 비중 상한 적용
"""
from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

from .common import market_of


def analyze(ticker: str, meta: dict, divs: pd.Series, price: float | None, fund: dict, cfg: dict) -> dict | None:
    if not price or divs is None or len(divs) == 0:
        return None
    today = pd.Timestamp(date.today())
    last12 = divs[divs.index > today - pd.Timedelta(days=365)]
    if len(last12) == 0:
        return None
    ttm = float(last12.sum())
    yld = ttm / price

    # 연도별 합계 → CAGR · 삭감 이력 (완결된 연도만)
    annual = divs.groupby(divs.index.year).sum()
    full = annual[annual.index < today.year]
    cagr = None
    if len(full) >= 4:
        y_end, y_start = full.index[-1], full.index[max(0, len(full) - 6)]
        n = y_end - y_start
        if n > 0 and full[y_start] > 0:
            cagr = (full[y_end] / full[y_start]) ** (1 / n) - 1
    cuts = int(sum(1 for a, b in zip(full.values[:-1], full.values[1:]) if b < a * 0.95)) if len(full) > 1 else 0

    # 최근 12개월 배당락월 → 월별 주당배당금
    by_month = {m: 0.0 for m in range(1, 13)}
    ex_days = {}
    for d, v in last12.items():
        by_month[d.month] += float(v)
        ex_days[d.month] = d.day

    pr = fund.get("payout_ratio")
    cap = cfg["max_yield_cap"]
    s_yield = min(yld, cap) / cap * 100
    s_growth = 30 if cagr is None else max(0.0, min(100.0, 30 + cagr * 700))  # 0%→30, 10%→100
    if meta.get("type") == "etf" or pr is None:
        s_sust = 70
    elif pr <= 0.6:
        s_sust = 100
    elif pr <= 0.9:
        s_sust = 100 - (pr - 0.6) / 0.3 * 70
    else:
        s_sust = 10
    s_stab = max(0, 100 - cuts * 35)
    score = 0.40 * s_yield + 0.30 * s_growth + 0.20 * s_sust + 0.10 * s_stab
    if yld > cap:
        score -= 10  # 과도한 고배당은 지속가능성 의심

    notes = []
    if meta.get("covered_call"):
        notes.append("커버드콜 — 원금 잠식·상승 제한 구조, 소수 비중 권장")
    if pr and pr > 0.9:
        notes.append(f"배당성향 {pr * 100:.0f}% — 지속가능성 점검 필요")
    if cuts:
        notes.append(f"최근 연간 배당 감소 {cuts}회")
    if market_of(ticker) == "kr" and len(last12) == 1:
        notes.append("연 1회 결산배당(지급은 통상 익년 4월 전후)")

    return {
        "ticker": ticker, "name": meta.get("name", ticker), "type": meta.get("type"),
        "market": market_of(ticker), "covered_call": bool(meta.get("covered_call")),
        "price": price, "currency": fund.get("currency") or ("KRW" if market_of(ticker) == "kr" else "USD"),
        "ttm_dps": ttm, "yield": yld * 100, "cagr5": None if cagr is None else cagr * 100,
        "payout": None if pr is None else pr * 100, "cuts": cuts, "freq": len(last12),
        "months": [m for m in range(1, 13) if by_month[m] > 0], "dps_by_month": by_month,
        "ex_days": ex_days, "score": round(score, 1), "notes": notes,
    }


def build_plan(cands: list[dict], cfg: dict, usdkrw: float) -> dict:
    ranked = sorted([c for c in cands if c], key=lambda c: c["score"], reverse=True)
    picks, covered = [], set()
    pool = ranked[:]
    while pool and len(picks) < cfg["max_picks"]:
        # 점수 + 새로 채우는 월 수 보너스(월 4점) → 매월 현금흐름 평탄화
        best = max(pool, key=lambda c: c["score"] + 4 * len(set(c["months"]) - covered))
        pool.remove(best)
        if best["covered_call"] and sum(p["covered_call"] for p in picks) >= 1:
            continue  # 커버드콜은 1종목까지만
        picks.append(best)
        covered |= set(best["months"])

    # 가중치: 점수 비례, 단일 35% 상한, 커버드콜 합산 상한
    raw = {p["ticker"]: max(p["score"], 1) for p in picks}
    tot = sum(raw.values()) or 1
    w = {k: v / tot for k, v in raw.items()}
    for _ in range(5):
        cc = [p["ticker"] for p in picks if p["covered_call"]]
        cc_w = sum(w[t] for t in cc)
        if cc_w > cfg["covered_call_max_weight"]:
            scale = cfg["covered_call_max_weight"] / cc_w
            for t in cc:
                w[t] *= scale
        over = {t: x for t, x in w.items() if x > 0.35}
        for t in over:
            w[t] = 0.35
        fixed = set(cc) | set(over)
        free = [t for t in w if t not in fixed]
        free_s = sum(w[t] for t in free)
        if free and free_s:
            remain = 1 - sum(w[t] for t in fixed)
            for t in free:
                w[t] = w[t] / free_s * remain
        if abs(sum(w.values()) - 1) < 1e-6:
            break

    capital = cfg["default_capital_krw"]
    monthly = [0.0] * 12
    rows = []
    for p in picks:
        fx = 1.0 if p["currency"] == "KRW" else usdkrw
        alloc = capital * w[p["ticker"]]
        shares = alloc / (p["price"] * fx)
        tax = cfg["tax"]["kr"] if p["market"] == "kr" else cfg["tax"]["us"]
        per_month = []
        for m in range(1, 13):
            amt = shares * p["dps_by_month"][m] * fx * (1 - tax)
            monthly[m - 1] += amt
            per_month.append(amt)
        rows.append({**{k: p[k] for k in ("ticker", "name", "type", "market", "covered_call", "yield",
                                          "cagr5", "payout", "score", "notes", "months", "freq")},
                     "weight": w[p["ticker"]] * 100, "alloc_krw": alloc, "annual_net_krw": sum(per_month),
                     "monthly_krw": per_month})

    annual = sum(monthly)
    # 다가오는 예상 배당락 (작년 같은 달의 배당락일 기준 추정)
    today = date.today()
    upcoming = []
    for p in picks:
        for m, d in p["ex_days"].items():
            for y in (today.year, today.year + 1):
                try:
                    est = date(y, m, min(d, 28))
                except ValueError:
                    continue
                if today <= est <= today + timedelta(days=45):
                    upcoming.append({"ticker": p["ticker"], "name": p["name"], "est_ex_date": est.isoformat()})
    upcoming.sort(key=lambda x: x["est_ex_date"])

    return {
        "capital_krw": capital, "usdkrw": usdkrw, "picks": rows,
        "monthly_net_krw": monthly, "annual_net_krw": annual,
        "portfolio_yield_net": annual / capital * 100 if capital else None,
        "months_covered": sorted(covered), "upcoming_ex": upcoming,
        "candidates": [{k: c[k] for k in ("ticker", "name", "type", "market", "covered_call", "yield", "cagr5",
                                          "payout", "cuts", "freq", "months", "score", "notes")} for c in ranked],
        "method": "점수 = 배당수익률 40% + 5년 배당성장 30% + 배당성향 지속가능성 20% + 무삭감 안정성 10%. "
                  "플랜은 점수와 '새로 채워지는 지급월'을 함께 고려해 선택, 단일 종목 35%·커버드콜 합산 "
                  f"{cfg['covered_call_max_weight'] * 100:.0f}% 상한. 월별 금액은 최근 12개월 배당락월 기준 세후 추정치이며 "
                  "실제 입금은 미국 통상 2~4주, 국내 결산배당은 수개월 뒤입니다.",
    }
