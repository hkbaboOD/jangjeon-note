"""데이터 소스 계층.

- LiveSource : Yahoo Finance(yfinance) — 무료, 미국·한국 종목 모두 지원
- DemoSource : 합성 데이터 — 네트워크 없이 앱 화면·로직 검증용 (`--demo`)

모든 상위 모듈은 아래 4개 메서드만 사용합니다.
    history(tickers, period) -> {ticker: DataFrame[Open,High,Low,Close,Volume]}
    fundamentals(ticker)     -> 정규화된 dict (FUND_KEYS)
    dividends(ticker)        -> pd.Series (index=배당락일, 값=주당배당금)
    news(ticker)             -> [{title, link, publisher, time}]
"""
from __future__ import annotations

import hashlib
import logging
import time
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from .common import STATE_DIR, market_of, now_kst, read_json, safe_float, write_json

log = logging.getLogger(__name__)

FUND_KEYS = [
    "name", "sector", "currency", "price", "forward_pe", "trailing_pe", "peg", "pb",
    "roe", "op_margin", "debt_to_equity", "revenue_growth", "earnings_growth",
    "target_mean", "target_high", "target_low", "analyst_count", "rec_key", "rec_mean",
    "payout_ratio", "market_cap", "high_52w", "low_52w",
]


# ──────────────────────────────────────────────────────────────
# Live: yfinance
def _iso(ts) -> str | None:
    if isinstance(ts, (int, float)):
        return datetime.utcfromtimestamp(ts).isoformat() + "Z"
    return ts


def parse_yf_news(items: list) -> list[dict]:
    """yfinance 뉴스 응답(신형 content 래핑·구형 평면 형식 모두)을 {title, link, publisher, time}로 정규화."""
    out = []
    for n in items[:8]:
        if not isinstance(n, dict):
            continue
        c = n.get("content") if isinstance(n.get("content"), dict) else n
        title = c.get("title")
        link = ((c.get("canonicalUrl") or {}).get("url") or (c.get("clickThroughUrl") or {}).get("url")
                or c.get("link") or n.get("link"))
        pub = (c.get("provider") or {}).get("displayName") or c.get("publisher")
        ts = c.get("pubDate") or c.get("displayTime") or c.get("providerPublishTime")
        if title and link:
            out.append({"title": title, "link": link, "publisher": pub, "time": _iso(ts)})
    return out


def parse_rss(xml_text: str, limit: int = 10) -> list[dict]:
    """RSS 2.0(Google 뉴스 등) → {title, link, publisher, time}. 제목 끝의 ' - 언론사'는 분리."""
    import xml.etree.ElementTree as ET
    from email.utils import parsedate_to_datetime

    out = []
    root = ET.fromstring(xml_text)
    for it in root.iter("item"):
        title = (it.findtext("title") or "").strip()
        link = (it.findtext("link") or "").strip()
        pub = (it.findtext("source") or "").strip() or None
        if pub and title.endswith(" - " + pub):
            title = title[: -len(" - " + pub)]
        ts = it.findtext("pubDate")
        try:
            ts = parsedate_to_datetime(ts).astimezone().isoformat() if ts else None
        except Exception:
            pass
        if title and link:
            out.append({"title": title, "link": link, "publisher": pub, "time": ts})
        if len(out) >= limit:
            break
    return out


# ──────────────────────────────────────────────────────────────
class LiveSource:
    CACHE_FILE = "fundamentals_cache.json"
    PREV_FILE = "fundamentals_prev.json"
    CACHE_HOURS = 18

    def __init__(self):
        import yfinance as yf  # 지연 import (데모 모드에선 불필요)

        self.yf = yf
        self._cache = read_json(self.CACHE_FILE, STATE_DIR, default={}) or {}
        self._dirty = False

    # --- 가격 이력 (배치 다운로드) ---
    def history(self, tickers: list[str], period: str = "2y") -> dict[str, pd.DataFrame]:
        out: dict[str, pd.DataFrame] = {}
        tickers = list(dict.fromkeys(tickers))
        for i in range(0, len(tickers), 40):
            chunk = tickers[i:i + 40]
            df = None
            for attempt in range(3):
                try:
                    df = self.yf.download(chunk, period=period, interval="1d", group_by="ticker",
                                          auto_adjust=False, progress=False, threads=True)
                    break
                except Exception as e:  # 호출 제한 등
                    log.warning("download 실패(%s) 재시도 %d: %s", chunk[:3], attempt + 1, e)
                    time.sleep(5 * (attempt + 1))
            if df is None or df.empty:
                continue
            for t in chunk:
                try:
                    sub = df[t] if isinstance(df.columns, pd.MultiIndex) else df
                    sub = sub[["Open", "High", "Low", "Close", "Volume"]].dropna(subset=["Close"])
                    if len(sub) > 0:
                        sub.index = pd.to_datetime(sub.index).tz_localize(None)
                        out[t] = sub
                except Exception:
                    log.warning("이력 없음: %s", t)
            time.sleep(1)
        return out

    # --- 펀더멘털 (캐시) ---
    def fundamentals(self, ticker: str) -> dict:
        hit = self._cache.get(ticker)
        if hit:
            age = datetime.utcnow() - datetime.fromisoformat(hit["_fetched"])
            if age < timedelta(hours=self.CACHE_HOURS):
                return hit
        info = {}
        for attempt in range(3):
            try:
                info = self.yf.Ticker(ticker).info or {}
                break
            except Exception as e:
                log.warning("info 실패 %s (%d): %s", ticker, attempt + 1, e)
                time.sleep(4 * (attempt + 1))
        time.sleep(0.6)  # 호출 제한 회피
        f = self._normalize(info)
        f["_fetched"] = datetime.utcnow().isoformat()
        if hit:  # 직전 스냅샷 보관 → 목표가·의견 변화(펀더멘털 변화) 감지용
            prev = read_json(self.PREV_FILE, STATE_DIR, default={}) or {}
            prev[ticker] = hit
            write_json(self.PREV_FILE, prev, STATE_DIR)
        self._cache[ticker] = f
        self._dirty = True
        return f

    @staticmethod
    def _normalize(i: dict) -> dict:
        g = lambda *ks: next((i.get(k) for k in ks if i.get(k) is not None), None)  # noqa: E731
        return {
            "name": g("shortName", "longName"),
            "sector": g("sector", "category", "quoteType"),
            "currency": g("currency"),
            "price": safe_float(g("currentPrice", "regularMarketPrice", "navPrice", "previousClose")),
            "forward_pe": safe_float(g("forwardPE")),
            "trailing_pe": safe_float(g("trailingPE")),
            "peg": safe_float(g("trailingPegRatio", "pegRatio")),
            "pb": safe_float(g("priceToBook")),
            "roe": safe_float(g("returnOnEquity")),
            "op_margin": safe_float(g("operatingMargins")),
            "debt_to_equity": safe_float(g("debtToEquity")),
            "revenue_growth": safe_float(g("revenueGrowth")),
            "earnings_growth": safe_float(g("earningsGrowth")),
            "target_mean": safe_float(g("targetMeanPrice")),
            "target_high": safe_float(g("targetHighPrice")),
            "target_low": safe_float(g("targetLowPrice")),
            "analyst_count": safe_float(g("numberOfAnalystOpinions")),
            "rec_key": g("recommendationKey"),
            "rec_mean": safe_float(g("recommendationMean")),
            "payout_ratio": safe_float(g("payoutRatio")),
            "market_cap": safe_float(g("marketCap", "totalAssets")),
            "high_52w": safe_float(g("fiftyTwoWeekHigh")),
            "low_52w": safe_float(g("fiftyTwoWeekLow")),
        }

    def dividends(self, ticker: str) -> pd.Series:
        try:
            s = self.yf.Ticker(ticker).dividends
            if s is None or len(s) == 0:
                return pd.Series(dtype=float)
            s.index = pd.to_datetime(s.index).tz_localize(None)
            return s.astype(float)
        except Exception as e:
            log.warning("배당 이력 실패 %s: %s", ticker, e)
            return pd.Series(dtype=float)

    def news(self, ticker: str) -> list[dict]:
        """헤드라인: yfinance 최신 뉴스 → 전체 탭 → 검색 API 순으로 시도(빈 응답 대비)."""
        attempts = [
            ("latest", lambda: self.yf.Ticker(ticker).news),
            ("all", lambda: self.yf.Ticker(ticker).get_news(count=10, tab="all")),
            ("search", lambda: self.yf.Search(ticker, max_results=1, news_count=8).news),
        ]
        for label, fn in attempts:
            try:
                out = parse_yf_news(fn() or [])
            except Exception as e:
                log.warning("뉴스 실패 %s(%s): %s", ticker, label, e)
                continue
            if out:
                return out
        log.warning("뉴스 비어 있음 %s (yfinance 3가지 경로 모두 0건)", ticker)
        return []

    def market_news(self, query: str, hl: str = "en-US", gl: str = "US") -> list[dict]:
        """yfinance 헤드라인이 모두 비었을 때 쓰는 대체 경로: Google 뉴스 RSS."""
        import requests
        from urllib.parse import quote_plus

        url = (f"https://news.google.com/rss/search?q={quote_plus(query)}"
               f"&hl={hl}&gl={gl}&ceid={gl}:{hl.split('-')[0]}")
        try:
            r = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0 jangjeon-note"})
            r.raise_for_status()
            return parse_rss(r.text)
        except Exception as e:
            log.warning("대체 뉴스(RSS) 실패 %s: %s", query, e)
            return []

    def flush(self):
        if self._dirty:
            write_json(self.CACHE_FILE, self._cache, STATE_DIR)


# ──────────────────────────────────────────────────────────────
# Demo: 결정적(재현 가능한) 합성 데이터
# ──────────────────────────────────────────────────────────────
_SECTORS = ["Technology", "Financial Services", "Healthcare", "Energy", "Consumer Defensive",
            "Industrials", "Communication Services", "Utilities", "Consumer Cyclical"]


class DemoSource:
    def _rng(self, ticker: str, salt: str = "") -> np.random.Generator:
        seed = int(hashlib.md5((ticker + salt).encode()).hexdigest()[:8], 16)
        return np.random.default_rng(seed)

    def _base_price(self, ticker: str) -> float:
        r = self._rng(ticker, "p")
        if ticker.startswith("^") or "=" in ticker or "." in ticker and ticker.split(".")[-1] == "NYB":
            return {"^DJI": 46000, "^GSPC": 6700, "^IXIC": 22800, "^SOX": 6900, "^KS11": 3600,
                    "^KQ11": 860, "^TNX": 4.1, "^VIX": 17, "CL=F": 62, "BZ=F": 66,
                    "DX-Y.NYB": 98.5, "KRW=X": 1420, "GC=F": 4100}.get(ticker, 100)
        if market_of(ticker) == "kr":
            return float(r.uniform(20000, 400000))
        return float(r.uniform(30, 600))

    def history(self, tickers, period="2y"):
        nbar = {"1y": 252, "2y": 504, "5y": 1260, "10y": 2520}.get(period, 504)
        days = pd.bdate_range(end=pd.Timestamp(now_kst().date()) - pd.Timedelta(days=1), periods=nbar)
        out = {}
        for t in tickers:
            r = self._rng(t, "h")
            mu, sig = r.uniform(-0.0002, 0.0009), r.uniform(0.008, 0.028)
            rets = r.normal(mu, sig, len(days))
            rets[-3:] += r.normal(0, sig * 1.5, 3)  # 최근 변동성 약간 확대 → 알림 테스트
            close = self._base_price(t) * np.exp(np.cumsum(rets) - np.sum(rets))
            close = close / close[-1] * self._base_price(t)
            spread = np.abs(r.normal(0, sig / 2, len(days))) * close
            opn = close * (1 + r.normal(0, sig / 3, len(days)))
            out[t] = pd.DataFrame({
                "Open": opn,
                "High": np.maximum(opn, close) + spread, "Low": np.minimum(opn, close) - spread, "Close": close,
                "Volume": r.integers(1e5, 5e7, len(days)).astype(float),
            }, index=days)
        return out

    def fundamentals(self, ticker):
        r = self._rng(ticker, "f")
        price = self._base_price(ticker)
        etf = ticker in {"SCHD", "VYM", "VIG", "DGRO", "JEPI", "JEPQ", "SPY", "QQQ", "SOXX"}
        fpe = None if etf else float(r.uniform(5, 40))
        growth = float(r.uniform(-0.05, 0.45))
        tgt = None if etf else price * float(r.uniform(0.85, 1.6))
        return {
            "name": ticker, "sector": "ETF" if etf else _SECTORS[int(r.integers(0, len(_SECTORS)))],
            "currency": "KRW" if market_of(ticker) == "kr" else "USD", "price": price,
            "forward_pe": fpe, "trailing_pe": fpe * float(r.uniform(1.0, 1.4)) if fpe else None,
            "peg": (fpe / (growth * 100)) if fpe and growth > 0.02 else None,
            "pb": None if etf else float(r.uniform(0.5, 12)),
            "roe": None if etf else float(r.uniform(-0.05, 0.6)),
            "op_margin": None if etf else float(r.uniform(0.02, 0.5)),
            "debt_to_equity": None if etf else float(r.uniform(5, 250)),
            "revenue_growth": growth / 2, "earnings_growth": growth,
            "target_mean": tgt, "target_high": tgt * 1.25 if tgt else None,
            "target_low": tgt * 0.75 if tgt else None,
            "analyst_count": None if etf else float(r.integers(2, 55)),
            "rec_key": None if etf else ["strong_buy", "buy", "hold", "underperform"][int(r.integers(0, 4))],
            "rec_mean": None if etf else float(r.uniform(1.2, 3.5)),
            "payout_ratio": float(r.uniform(0.2, 0.95)), "market_cap": float(r.uniform(1e10, 3e12)),
            "high_52w": price * float(r.uniform(1.02, 1.5)), "low_52w": price * float(r.uniform(0.6, 0.95)),
        }

    def dividends(self, ticker):
        r = self._rng(ticker, "d")
        price = self._base_price(ticker)
        monthly = ticker in {"O", "JEPI", "JEPQ"}
        start = int(r.integers(1, 4))
        months = list(range(1, 13)) if monthly else [m for m in range(1, 13) if (m - start) % 3 == 0]
        if ticker.endswith(".KS") and not monthly:
            months = [12] if r.random() < 0.4 else [3, 6, 9, 12]
        annual_yield = r.uniform(0.07, 0.11) if ticker in {"JEPI", "JEPQ"} else r.uniform(0.012, 0.055)
        dates, vals = [], []
        today = now_kst().date()
        for y in range(today.year - 5, today.year + 1):
            for m in months:
                d = pd.Timestamp(year=y, month=m, day=int(r.integers(5, 25)))
                if d.date() >= today:
                    continue
                growth = (1 + r.uniform(0.0, 0.08)) ** (y - today.year)
                dates.append(d)
                vals.append(price * annual_yield / len(months) * growth)
        return pd.Series(vals, index=pd.DatetimeIndex(dates), dtype=float)

    def market_news(self, query, hl="en-US", gl="US"):
        return []

    def news(self, ticker):
        return [{"title": f"[데모] {ticker} 관련 헤드라인 예시 {i + 1}", "link": "https://finance.yahoo.com",
                 "publisher": "Demo", "time": now_kst().isoformat()} for i in range(3)]

    def flush(self):
        pass


def get_source(demo: bool):
    return DemoSource() if demo else LiveSource()
