"""종목 기본 정보(한국어 이름·사업 소개·정세 노출 태그) + 한국어 증권 사이트 링크.

- 소개·태그: config/companies.yaml (주간 자동 점검이 새 종목을 채움)
- 링크: 네이버페이 증권(모바일) — 국내는 종목코드, 해외는 네이버 로이터 코드(AAPL.O, KO 등)
  해외 코드는 실행 환경(GitHub Actions)에서 네이버 API로 한 번 확인해 state/naver_codes.json 에 캐시.
  확인이 안 되면 네이버 통합검색(“애플 주가”)으로 연결 — 항상 한국어 페이지로 이동.
"""
from __future__ import annotations

import logging
import time
from datetime import date, timedelta
from urllib.parse import quote, quote_plus

import yaml

from .common import ROOT, STATE_DIR, market_of, read_json, write_json

log = logging.getLogger(__name__)
COMP_PATH = ROOT / "config" / "companies.yaml"
NAVER_CACHE = "naver_codes.json"
RETRY_DAYS = 14  # 확인 실패한 코드는 2주 뒤 재시도
TAG_KO = {
    "semis": "반도체", "ai": "AI 인프라", "china": "중국 노출", "export": "수출주", "defense": "방산",
    "oil_up": "정유·에너지", "oil_down": "유가 원가 부담주", "shipping": "해운·조선", "bank": "금융(금리 수혜)",
    "rate_sensitive": "금리 민감", "tariff": "관세 민감", "defensive": "경기 방어", "battery": "이차전지",
    "pharma": "제약·바이오", "nuclear": "원전", "korea": "국내 상장주",
}


def load_companies() -> dict:
    try:
        return yaml.safe_load(COMP_PATH.read_text(encoding="utf-8")) or {}
    except FileNotFoundError:
        return {}


def tags_of(ticker: str, companies: dict) -> list[str]:
    tags = list((companies.get(ticker) or {}).get("tags") or [])
    if market_of(ticker) == "kr" and "korea" not in tags:
        tags.append("korea")
    return tags


def _code(t: str) -> str:
    return t.split(".")[0]


def links(ticker: str, ko_name: str | None, naver: dict | None) -> dict:
    """한국어 사이트 링크 묶음. naver: naver_codes.json 의 해당 종목 항목(없으면 검색 링크)."""
    q = ko_name or ticker
    news = f"https://search.naver.com/search.naver?where=news&query={quote_plus(q + ' 주가')}"
    if market_of(ticker) == "kr":
        c = _code(ticker)
        return {"naver": f"https://m.stock.naver.com/domestic/stock/{c}/total",
                "fnguide": f"https://comp.fnguide.com/SVO2/ASP/SVD_Main.asp?pGB=1&gicode=A{c}",
                "news": news}
    nv = naver or {}
    if nv.get("code"):
        base = f"https://m.stock.naver.com/worldstock/{nv.get('type') or 'stock'}/{quote(nv['code'])}"
        return {"naver": f"{base}/total", "news": news}
    return {"naver": f"https://search.naver.com/search.naver?query={quote_plus(q + ' 주가')}", "news": news}


def _candidates(t: str) -> list[str]:
    s = t.replace("-", ".")  # BRK-B → BRK.B (네이버 표기)
    return [f"{s}.O", s, f"{s}.N", f"{s}.K", f"{s}.A"]


def resolve_naver(tickers: list[str], budget_sec: float = 120.0) -> dict:
    """해외 종목의 네이버 로이터 코드 확인(캐시). 연결 자체가 막히면 이번 실행은 건너뜀."""
    cache = read_json(NAVER_CACHE, STATE_DIR, default={}) or {}
    today = date.today()
    todo = []
    for t in dict.fromkeys(tickers):
        if market_of(t) == "kr":
            continue
        hit = cache.get(t)
        if hit and (hit.get("code") or (today - date.fromisoformat(hit.get("checked", "2000-01-01"))) < timedelta(days=RETRY_DAYS)):
            continue
        todo.append(t)
    if not todo:
        return cache
    try:
        import requests
    except ImportError:
        return cache
    sess = requests.Session()
    sess.headers.update({"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) jangjeon-note",
                         "Referer": "https://m.stock.naver.com/"})
    start, conn_fail, changed = time.time(), 0, False
    for t in todo:
        if time.time() - start > budget_sec or conn_fail >= 3:
            break
        found = None
        for cand in _candidates(t):
            for kind in ("stock", "etf"):
                try:
                    r = sess.get(f"https://api.stock.naver.com/{kind}/{cand}/basic", timeout=6)
                except Exception:
                    conn_fail += 1
                    break
                if r.status_code != 200:
                    continue
                try:
                    j = r.json()
                except ValueError:
                    continue
                if isinstance(j, dict) and (j.get("stockName") or j.get("reutersCode")):
                    end = str(j.get("stockEndType") or kind).lower()
                    found = {"code": j.get("reutersCode") or cand, "type": "etf" if "etf" in end else "stock",
                             "ko": j.get("stockName"), "checked": today.isoformat()}
                    break
            if found or conn_fail >= 3:
                break
            time.sleep(0.15)
        if found:
            cache[t] = found
            changed = True
        elif conn_fail < 3:
            cache[t] = {"code": None, "checked": today.isoformat()}
            changed = True
    if conn_fail >= 3:
        log.warning("네이버 코드 확인 불가(연결 차단) — 해외 종목은 네이버 검색 링크로 연결")
    if changed:
        write_json(NAVER_CACHE, cache, STATE_DIR)
    log.info("네이버 코드: 확인 %d / 대상 %d", sum(1 for t in todo if (cache.get(t) or {}).get("code")), len(todo))
    return cache


def profile(ticker: str, companies: dict, fund: dict, naver: dict | None) -> dict:
    """상세 시트용 기업 개요."""
    c = companies.get(ticker) or {}
    ko = c.get("ko") or (naver or {}).get("ko") or fund.get("name") or ticker
    tags = tags_of(ticker, companies)
    return {
        "ko": ko, "desc": c.get("desc"), "tags": tags, "tags_ko": [TAG_KO.get(x, x) for x in tags if x != "korea"],
        "industry": fund.get("industry"), "country": fund.get("country"), "employees": fund.get("employees"),
        "website": fund.get("website"), "market_cap": fund.get("market_cap"), "currency": fund.get("currency"),
        "summary_en": fund.get("summary"), "links": links(ticker, ko, naver),
    }
