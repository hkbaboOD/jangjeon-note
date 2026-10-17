"""재무제표·실적·컨센서스 수집과 규칙 기반 실적 분석.

- 상세(관심종목 + 추천 종목): 연간/분기 손익, 재무상태, 현금흐름, 실적 서프라이즈, 다음 실적 발표일,
  컨센서스(EPS·매출 추정치)와 추정치 변화 → 차트 파일의 "fin" 블록 (3일 캐시)
- 전 종목: 이익 추정치 변화율(EPS 리비전) → 'revision' 팩터 (실전 검증용으로 가중치 0에서 기록 시작)

모든 문장은 수집한 숫자에서만 만들어집니다(추정·창작 없음). 데이터가 없으면 해당 항목을 비웁니다.
"""
from __future__ import annotations

import hashlib
import logging
import math
import time
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd

from .common import STATE_DIR, market_of, read_json, safe_float, write_json

log = logging.getLogger(__name__)
CACHE = "financials_cache.json"
TTL_DAYS = 3
TREND_CACHE = "eps_trend_cache.json"
TREND_HOURS = 20
EPS_HIST = "eps_hist.json"

INCOME = {"revenue": ["Total Revenue", "Operating Revenue"], "gross": ["Gross Profit"],
          "op": ["Operating Income", "Total Operating Income As Reported"],
          "net": ["Net Income Common Stockholders", "Net Income", "Net Income Including Noncontrolling Interests"],
          "eps": ["Diluted EPS", "Basic EPS"]}
BALANCE = {"assets": ["Total Assets"], "liab": ["Total Liabilities Net Minority Interest", "Total Liabilities"],
           "equity": ["Stockholders Equity", "Common Stock Equity", "Total Equity Gross Minority Interest"],
           "debt": ["Total Debt"], "cash": ["Cash Cash Equivalents And Short Term Investments", "Cash And Cash Equivalents"],
           "cur_assets": ["Current Assets"], "cur_liab": ["Current Liabilities"]}
CASHFLOW = {"ocf": ["Operating Cash Flow", "Cash Flow From Continuing Operating Activities"],
            "capex": ["Capital Expenditure"], "fcf": ["Free Cash Flow"],
            "div": ["Cash Dividends Paid", "Common Stock Dividend Paid"],
            "buyback": ["Repurchase Of Capital Stock", "Common Stock Payments"]}


# ── 포맷 ──
def money(v, cur: str | None) -> str:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "-"
    a = abs(v)
    if cur == "KRW":
        return f"{v / 1e12:,.1f}조원" if a >= 1e12 else f"{v / 1e8:,.0f}억원"
    sym = "$" if cur in (None, "USD") else ""
    return f"{sym}{v / 1e9:,.1f}B" if a >= 1e9 else f"{sym}{v / 1e6:,.0f}M"


def _pct(a, b):
    if a is None or b is None or not b:
        return None
    return (a / b - 1) * 100 if b > 0 else ((a - b) / abs(b)) * 100


# ── 표 변환 ──
def _row(df: pd.DataFrame, names: list[str]):
    for n in names:
        if n in df.index:
            return df.loc[n]
    return None


def table(df, mapping: dict, n: int, quarterly: bool = False) -> dict | None:
    """yfinance 재무표(열=기간, 최신이 왼쪽) → {"periods": [...], key: [...]} (과거→최신)."""
    if df is None or not isinstance(df, pd.DataFrame) or df.empty:
        return None
    cols = sorted([c for c in df.columns if pd.notna(c)], key=lambda c: pd.Timestamp(c))[-n:]
    out = {"periods": [pd.Timestamp(c).strftime("%y.%m" if quarterly else "%Y.%m") for c in cols]}
    have = False
    for k, names in mapping.items():
        s = _row(df, names)
        vals = [safe_float(s.get(c)) if s is not None else None for c in cols]
        out[k] = vals
        have |= any(v is not None for v in vals)
    return out if have else None


def _est(df) -> dict | None:
    if df is None or not isinstance(df, pd.DataFrame) or df.empty:
        return None
    out = {}
    for idx, r in df.iterrows():
        out[str(idx)] = {str(k): safe_float(v) for k, v in r.items()}
    return out


def _surprises(df) -> list[dict]:
    if df is None or not isinstance(df, pd.DataFrame) or df.empty:
        return []
    rows = []
    for idx, r in df.iterrows():
        est, act = safe_float(r.get("epsEstimate")), safe_float(r.get("epsActual"))
        if est is None or act is None:
            continue
        try:
            q = pd.Timestamp(idx).strftime("%y.%m")
        except Exception:
            q = str(idx)
        rows.append({"q": q, "est": est, "act": act, "surp": round((act - est) / abs(est) * 100, 1) if est else None})
    return rows[-4:]


def _calendar(cal) -> dict | None:
    if not cal:
        return None
    if isinstance(cal, pd.DataFrame):
        cal = {k: v.iloc[0] if len(v) else None for k, v in cal.to_dict(orient="series").items()}
    ed = cal.get("Earnings Date")
    if isinstance(ed, (list, tuple)):
        ed = ed[0] if ed else None
    try:
        ed = pd.Timestamp(ed).strftime("%Y-%m-%d") if ed is not None else None
    except Exception:
        ed = None
    return {"next_earnings": ed, "eps_avg": safe_float(cal.get("Earnings Average")),
            "rev_avg": safe_float(cal.get("Revenue Average"))}


def fetch_live(yf, ticker: str) -> dict:
    t = yf.Ticker(ticker)
    out: dict = {"ok": False}
    getters = {
        "annual": lambda: table(t.income_stmt, INCOME, 4),
        "quarterly": lambda: table(t.quarterly_income_stmt, INCOME, 5, quarterly=True),
        "balance": lambda: table(t.balance_sheet, BALANCE, 2),
        "cashflow": lambda: table(t.cashflow, CASHFLOW, 4),
        "surprises": lambda: _surprises(t.earnings_history),
        "calendar": lambda: _calendar(t.calendar),
        "eps_est": lambda: _est(t.earnings_estimate),
        "rev_est": lambda: _est(t.revenue_estimate),
        "eps_trend": lambda: _est(t.eps_trend),
        "eps_rev": lambda: _est(t.eps_revisions),
    }
    for k, fn in getters.items():
        try:
            out[k] = fn()
        except Exception as e:  # 항목별 실패는 무시(무료 데이터 결측 흔함)
            log.info("재무 %s/%s 없음: %s", ticker, k, e)
            out[k] = None
        time.sleep(0.25)
    out["ok"] = bool(out.get("annual") or out.get("quarterly"))
    return out


def fetch_demo(ticker: str, fund: dict) -> dict:
    rng = np.random.default_rng(int(hashlib.md5(ticker.encode()).hexdigest()[:8], 16))
    if (fund or {}).get("sector") == "ETF":
        return {"ok": False}
    kr = market_of(ticker) == "kr"
    base = float(rng.uniform(5e12, 3e14) if kr else rng.uniform(5e9, 3e11))
    g = rng.uniform(-0.05, 0.25)
    years = [base * (1 + g) ** i for i in range(4)]
    m = rng.uniform(0.05, 0.3)
    yr0 = date.today().year - 4
    annual = {"periods": [f"{yr0 + i}.12" for i in range(4)], "revenue": years,
              "gross": [y * (m + 0.2) for y in years], "op": [y * (m + rng.normal(0, 0.02)) for y in years],
              "net": [y * m * 0.75 for y in years], "eps": [round(y * m * 0.75 / 1e9, 2) for y in years]}
    q0 = base * (1 + g) ** 3 / 4
    qs = [q0 * (1 + g / 4) ** i * (1 + rng.normal(0, 0.03)) for i in range(5)]
    quarterly = {"periods": ["25.06", "25.09", "25.12", "26.03", "26.06"], "revenue": qs,
                 "gross": [q * (m + 0.2) for q in qs], "op": [q * (m + rng.normal(0, 0.03)) for q in qs],
                 "net": [q * m * 0.75 for q in qs], "eps": [round(q * m * 0.75 / 1e9, 2) for q in qs]}
    eq = base * rng.uniform(0.4, 1.2)
    balance = {"periods": [f"{yr0 + 2}.12", f"{yr0 + 3}.12"], "assets": [eq * 1.8, eq * 2.0],
               "liab": [eq * 0.8, eq * 1.0], "equity": [eq * 0.95, eq], "debt": [eq * 0.3, eq * 0.35],
               "cash": [eq * 0.2, eq * 0.25], "cur_assets": [eq * 0.6, eq * 0.7], "cur_liab": [eq * 0.4, eq * 0.45]}
    ocf = [y * (m + 0.05) for y in years]
    cashflow = {"periods": annual["periods"], "ocf": ocf, "capex": [-o * 0.4 for o in ocf],
                "fcf": [o * 0.6 for o in ocf], "div": [-o * 0.15 for o in ocf], "buyback": [-o * 0.1 for o in ocf]}
    eps_now = annual["eps"][-1]
    surprises = [{"q": q, "est": round(e * 0.97, 2), "act": e, "surp": 3.1} for q, e in zip(quarterly["periods"][1:], quarterly["eps"][1:])]
    cur = eps_now * 1.12
    return {"ok": True, "annual": annual, "quarterly": quarterly, "balance": balance, "cashflow": cashflow,
            "surprises": surprises, "calendar": {"next_earnings": (date.today() + timedelta(days=20)).isoformat(),
                                                 "eps_avg": round(cur / 4, 2), "rev_avg": qs[-1] * 1.03},
            "eps_est": {"0y": {"avg": round(cur, 2), "numberOfAnalysts": 12, "growth": 0.12, "yearAgoEps": eps_now},
                        "+1y": {"avg": round(cur * 1.1, 2), "numberOfAnalysts": 10, "growth": 0.10}},
            "rev_est": {"0y": {"avg": years[-1] * 1.08, "numberOfAnalysts": 12, "growth": 0.08},
                        "+1y": {"avg": years[-1] * 1.16, "numberOfAnalysts": 10, "growth": 0.07}},
            "eps_trend": {"0y": {"current": round(cur, 2), "7daysAgo": round(cur, 2), "30daysAgo": round(cur * 0.98, 2),
                                 "90daysAgo": round(cur * 0.95, 2)}},
            "eps_rev": {"0y": {"upLast30days": 5, "downLast30days": 1}}}


# ── 캐시 래퍼 ──
class Financials:
    def __init__(self, src):
        self.yf = getattr(src, "yf", None)  # LiveSource 만 yf 보유
        self.cache = read_json(CACHE, STATE_DIR, default={}) or {}
        self.tcache = read_json(TREND_CACHE, STATE_DIR, default={}) or {}
        self.dirty = self.tdirty = False

    def get(self, ticker: str, fund: dict | None = None) -> dict:
        if self.yf is None:
            return fetch_demo(ticker, fund or {})
        hit = self.cache.get(ticker)
        if hit and datetime.utcnow() - datetime.fromisoformat(hit["_fetched"]) < timedelta(days=TTL_DAYS):
            return hit
        try:
            f = fetch_live(self.yf, ticker)
        except Exception as e:
            log.warning("재무 수집 실패 %s: %s", ticker, e)
            return hit or {"ok": False}
        f["_fetched"] = datetime.utcnow().isoformat()
        self.cache[ticker] = f
        self.dirty = True
        return f

    def trend(self, ticker: str) -> dict | None:
        """EPS 추정치 추이(현재/7·30·90일 전). 전 종목 리비전 팩터용(20시간 캐시)."""
        if self.yf is None:
            return None
        hit = self.tcache.get(ticker)
        if hit and datetime.utcnow() - datetime.fromisoformat(hit["_fetched"]) < timedelta(hours=TREND_HOURS):
            return hit.get("v")
        try:
            v = _est(self.yf.Ticker(ticker).eps_trend)
        except Exception as e:
            log.info("EPS 추이 없음 %s: %s", ticker, e)
            v = None
        time.sleep(0.25)
        self.tcache[ticker] = {"v": v, "_fetched": datetime.utcnow().isoformat()}
        self.tdirty = True
        return v

    def flush(self):
        if self.dirty:
            # 오래된 항목 정리(관심·추천에서 빠진 종목)
            cut = datetime.utcnow() - timedelta(days=30)
            self.cache = {k: v for k, v in self.cache.items() if datetime.fromisoformat(v["_fetched"]) > cut}
            write_json(CACHE, self.cache, STATE_DIR)
        if self.tdirty:
            write_json(TREND_CACHE, self.tcache, STATE_DIR)


# ── EPS 리비전 ──
def trend_revision(tr: dict | None) -> float | None:
    """이번 회계연도(없으면 내년) EPS 추정치의 30일 변화율(%)."""
    for per in ("0y", "+1y"):
        r = (tr or {}).get(per) or {}
        cur, ago = r.get("current"), r.get("30daysAgo")
        if cur is not None and ago is not None and abs(ago) > 1e-9:
            return round(float(np.clip((cur - ago) / abs(ago) * 100, -50, 50)), 2)
    return None


def update_eps_hist(feps: dict[str, float | None], today: str) -> dict:
    """선행 EPS(yfinance forwardEps) 일별 기록 — EPS 추이가 없는 종목의 리비전 대체 계산용."""
    hist = read_json(EPS_HIST, STATE_DIR, default={}) or {}
    cut = (date.fromisoformat(today) - timedelta(days=120)).isoformat()
    for t, v in feps.items():
        if v is None:
            continue
        h = {d: x for d, x in (hist.get(t) or {}).items() if d >= cut}
        h[today] = round(float(v), 4)
        hist[t] = h
    write_json(EPS_HIST, hist, STATE_DIR)
    return hist


def hist_revision(h: dict | None, today: str, days: int = 28) -> float | None:
    if not h:
        return None
    ref = (date.fromisoformat(today) - timedelta(days=days)).isoformat()
    older = sorted(d for d in h if d <= ref)
    if not older or today not in h:
        return None
    ago, cur = h[older[-1]], h[today]
    if not ago or abs(ago) < 1e-9:
        return None
    return round(float(np.clip((cur - ago) / abs(ago) * 100, -50, 50)), 2)


# ── 규칙 기반 실적 분석 ──
def analyze(fin: dict, fund: dict | None) -> list[dict]:
    """[{pol: +1/0/-1, cat, text}] — 모든 숫자는 수집 데이터에서 계산."""
    if not fin or not fin.get("ok"):
        return []
    cur = (fund or {}).get("currency")
    out = []

    def add(pol, cat, text):
        out.append({"pol": pol, "cat": cat, "text": text})

    a = fin.get("annual") or {}
    if a.get("revenue") and len(a["revenue"]) >= 2:
        r0, r1 = a["revenue"][-2], a["revenue"][-1]
        g = _pct(r1, r0)
        if g is not None:
            add(1 if g >= 5 else -1 if g < 0 else 0, "성장",
                f"연간 매출 {money(r1, cur)} ({a['periods'][-1]}), 전년 대비 {g:+.1f}%")
    if a.get("op") and a.get("revenue") and len(a["op"]) >= 2:
        m = [o / r * 100 if o is not None and r else None for o, r in zip(a["op"], a["revenue"])]
        if m[-1] is not None and m[-2] is not None:
            d = round(m[-1] - m[-2], 1) + 0.0  # -0.0 방지
            add(1 if d >= 1 else -1 if d <= -1 else 0, "수익성",
                f"영업이익률 {m[-2]:.1f}% → {m[-1]:.1f}% ({d:+.1f}%p)")
    if a.get("net") and len(a["net"]) >= 2 and a["net"][-1] is not None and a["net"][-2] is not None:
        n0, n1 = a["net"][-2], a["net"][-1]
        if n0 < 0 <= n1:
            add(1, "수익성", f"순이익 흑자 전환 ({money(n1, cur)})")
        elif n0 >= 0 > n1:
            add(-1, "수익성", f"순이익 적자 전환 ({money(n1, cur)})")
    q = fin.get("quarterly") or {}
    if q.get("revenue") and len(q["revenue"]) >= 5 and q["revenue"][-1] and q["revenue"][-5]:
        g = _pct(q["revenue"][-1], q["revenue"][-5])
        add(1 if g >= 5 else -1 if g < 0 else 0, "성장",
            f"최근 분기({q['periods'][-1]}) 매출 전년 동기 대비 {g:+.1f}%")
    if q.get("op") and len(q["op"]) >= 5 and q["op"][-1] is not None and q["op"][-5]:
        g = _pct(q["op"][-1], q["op"][-5])
        if g is not None:
            add(1 if g >= 10 else -1 if g < 0 else 0, "성장", f"최근 분기 영업이익 전년 동기 대비 {g:+.1f}%")
    c = fin.get("cashflow") or {}
    if c.get("fcf") and c["fcf"][-1] is not None:
        f1 = c["fcf"][-1]
        rev = (a.get("revenue") or [None])[-1]
        txt = f"잉여현금흐름(FCF) {money(f1, cur)}" + (f", 매출 대비 {f1 / rev * 100:.1f}%" if rev else "")
        add(1 if f1 > 0 else -1, "현금흐름", txt)
        ni = (a.get("net") or [None])[-1]
        ocf = (c.get("ocf") or [None])[-1]
        if ni and ni > 0 and ocf is not None:
            ratio = ocf / ni
            if ratio < 0.8:
                add(-1, "이익의 질", f"영업현금흐름이 순이익의 {ratio:.1f}배 — 이익 대비 현금 유입이 적음")
            elif ratio >= 1.2:
                add(1, "이익의 질", f"영업현금흐름이 순이익의 {ratio:.1f}배 — 이익이 현금으로 뒷받침됨")
    ret = sum(-(x or 0) for x in ((c.get("div") or [None])[-1:] + (c.get("buyback") or [None])[-1:]))
    if ret > 0:
        add(1, "주주환원", f"최근 연도 배당+자사주 매입 {money(ret, cur)}")
    b = fin.get("balance") or {}
    if b.get("liab") and b.get("equity") and b["liab"][-1] is not None and b["equity"][-1]:
        dr = b["liab"][-1] / b["equity"][-1] * 100
        add(-1 if dr >= 200 else 1 if dr <= 100 else 0, "재무안정성", f"부채비율(부채/자본) {dr:.0f}%")
    if b.get("cash") and b.get("debt") and b["cash"][-1] is not None and b["debt"][-1] is not None:
        net = b["cash"][-1] - b["debt"][-1]
        add(1 if net > 0 else 0, "재무안정성", f"{'순현금' if net > 0 else '순차입금'} {money(abs(net), cur)}")
    if b.get("cur_assets") and b.get("cur_liab") and b["cur_assets"][-1] and b["cur_liab"][-1]:
        cr = b["cur_assets"][-1] / b["cur_liab"][-1] * 100
        if cr < 100:
            add(-1, "재무안정성", f"유동비율 {cr:.0f}% — 단기 지급 여력 낮음")
    s = fin.get("surprises") or []
    sv = [x for x in s if x.get("surp") is not None]
    if sv:
        beats = sum(x["surp"] > 0 for x in sv)
        avg = sum(x["surp"] for x in sv) / len(sv)
        add(1 if beats >= len(sv) * 0.75 else -1 if beats <= len(sv) * 0.25 else 0, "실적 발표",
            f"최근 {len(sv)}개 분기 중 {beats}번 예상치 상회 (평균 서프라이즈 {avg:+.1f}%)")
    rev = trend_revision(fin.get("eps_trend"))
    if rev is not None:
        add(1 if rev >= 1 else -1 if rev <= -1 else 0, "컨센서스", f"올해 EPS 추정치 30일 변화 {rev:+.1f}%")
    er = ((fin.get("eps_rev") or {}).get("0y") or {})
    up, dn = er.get("upLast30days"), er.get("downLast30days")
    if up is not None and dn is not None and (up + dn) > 0:
        add(1 if up > dn else -1 if dn > up else 0, "컨센서스", f"최근 30일 추정치 상향 {up:.0f}건 · 하향 {dn:.0f}건")
    ee = (fin.get("eps_est") or {}).get("+1y") or {}
    if ee.get("growth") is not None:
        add(1 if ee["growth"] > 0.05 else -1 if ee["growth"] < 0 else 0, "컨센서스",
            f"내년 EPS 컨센서스 성장률 {ee['growth'] * 100:+.1f}%" + (f" (애널리스트 {ee['numberOfAnalysts']:.0f}명)" if ee.get("numberOfAnalysts") else ""))
    cal = fin.get("calendar") or {}
    if cal.get("next_earnings"):
        try:
            dd = (date.fromisoformat(cal["next_earnings"]) - date.today()).days
            if 0 <= dd <= 14:
                add(0, "실적 발표", f"{dd}일 뒤({cal['next_earnings']}) 실적 발표 예정 — 발표 전후 변동성 확대 가능")
        except ValueError:
            pass
    return out


def compact(fin: dict) -> dict:
    """차트 파일에 넣을 크기로 정리."""
    if not fin or not fin.get("ok"):
        return {"ok": False}
    keep = ("annual", "quarterly", "balance", "cashflow", "surprises", "calendar", "eps_est", "rev_est", "eps_trend", "eps_rev")
    return {"ok": True, **{k: fin.get(k) for k in keep}}
