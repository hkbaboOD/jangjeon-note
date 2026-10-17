"""v6: 기업 정보·한국어 링크, 재무 분석, 국제 정세, 기록용 팩터."""
from datetime import datetime, timedelta, timezone

import pandas as pd
import yaml

from pipeline import company as co
from pipeline import financials as fin
from pipeline import geo
from pipeline.common import CONFIG_PATH
from pipeline.valuation import HORIZON_WEIGHTS, score_universe


def test_companies_cover_every_ticker_with_known_tags():
    cfg = yaml.safe_load(open(CONFIG_PATH, encoding="utf-8"))
    need = set(cfg["universe"]["us"]) | set(cfg["universe"]["kr"]) | {d["ticker"] for d in cfg["dividend_candidates"]} \
        | {w["ticker"] for w in cfg["watchlist"]}
    comp = co.load_companies()
    assert need <= set(comp)
    for t, c in comp.items():
        assert c.get("ko") and c.get("desc"), t
        assert set(c.get("tags") or []) <= set(co.TAG_KO), t
    exp_tags = {tg for th in geo.THEMES for tg in th["exp"]}
    assert exp_tags <= set(co.TAG_KO)


def test_korean_links():
    kr = co.links("005930.KS", "삼성전자", None)
    assert kr["naver"] == "https://m.stock.naver.com/domestic/stock/005930/total"
    assert "A005930" in kr["fnguide"] and "search.naver.com" in kr["news"]
    us = co.links("AAPL", "애플", {"code": "AAPL.O", "type": "stock"})
    assert us["naver"] == "https://m.stock.naver.com/worldstock/stock/AAPL.O/total"
    etf = co.links("SCHD", "슈왑", {"code": "SCHD.K", "type": "etf"})
    assert "/worldstock/etf/SCHD.K/" in etf["naver"]
    fb = co.links("KO", "코카콜라", {"code": None})
    assert fb["naver"].startswith("https://search.naver.com/") and "yahoo" not in str(fb)
    assert co.tags_of("005930.KS", co.load_companies())[-1] == "korea"


def _stmt():
    cols = [pd.Timestamp("2025-12-31"), pd.Timestamp("2024-12-31"), pd.Timestamp("2023-12-31")]
    return pd.DataFrame({cols[0]: [120.0, 24.0, 18.0, 1.8], cols[1]: [100.0, 15.0, 12.0, 1.2], cols[2]: [90.0, 9.0, -2.0, -0.2]},
                        index=["Total Revenue", "Operating Income", "Net Income", "Diluted EPS"])


def test_financial_table_and_analysis():
    tb = fin.table(_stmt(), fin.INCOME, 4)
    assert tb["periods"] == ["2023.12", "2024.12", "2025.12"]  # 과거 → 최신
    assert tb["revenue"] == [90.0, 100.0, 120.0] and tb["gross"] == [None, None, None]
    data = {"ok": True, "annual": tb,
            "cashflow": {"periods": ["2025.12"], "ocf": [30.0], "fcf": [20.0], "capex": [-10.0], "div": [-3.0], "buyback": [-2.0]},
            "balance": {"periods": ["2025.12"], "liab": [250.0], "equity": [100.0], "cash": [5.0], "debt": [40.0],
                        "cur_assets": [50.0], "cur_liab": [60.0]},
            "surprises": [{"q": "26.03", "est": 1.0, "act": 1.1, "surp": 10.0}, {"q": "26.06", "est": 1.0, "act": 1.05, "surp": 5.0}],
            "eps_trend": {"0y": {"current": 2.2, "30daysAgo": 2.0}}}
    txt = {a["text"]: a["pol"] for a in fin.analyze(data, {"currency": "USD"})}
    joined = " | ".join(txt)
    assert "전년 대비 +20.0%" in joined
    assert "영업이익률 15.0% → 20.0% (+5.0%p)" in joined
    assert "부채비율(부채/자본) 250%" in joined and txt["부채비율(부채/자본) 250%"] == -1
    assert any("유동비율 83%" in k for k in txt)
    assert any("2번 예상치 상회" in k for k in txt)
    assert any("EPS 추정치 30일 변화 +10.0%" in k for k in txt)
    assert fin.analyze({"ok": False}, {}) == []


def test_revision_helpers():
    assert fin.trend_revision({"0y": {"current": 1.1, "30daysAgo": 1.0}}) == 10.0
    assert fin.trend_revision({"0y": {"current": None}, "+1y": {"current": 0.9, "30daysAgo": 1.0}}) == -10.0
    assert fin.trend_revision(None) is None
    today = "2026-10-17"
    h = {"2026-09-10": 2.0, "2026-09-18": 2.1, today: 2.31}
    assert round(fin.hist_revision(h, today), 1) == 10.0  # 28일 이전 중 가장 최근(9/18) 대비
    assert fin.hist_revision({today: 2.0}, today) is None


def _news(words, n):
    now = datetime.now(timezone.utc).isoformat()
    return [{"title": f"기사 {words[i % len(words)]} {i}", "link": "x", "time": now} for i in range(n)]


def test_geo_tone_heat_and_impacts():
    assert geo.tone(["휴전 합의", "협상 타결", "긴장 완화"])[0] == -1.0
    assert geo.tone(["공습 확전", "제재"])[0] == 0.0  # 표본 < 3 → 보류
    h, base, ratio, lvl = geo.heat(60, [20] * 10)
    assert ratio == 3.0 and lvl == "매우 높음" and h == 1.0
    old = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()
    assert len(geo._recent([{"title": "a", "time": old}, {"title": "b", "time": None}])) == 1

    news = {"mideast": _news(["공습 확전", "보복 위협", "봉쇄 긴장"], 60)}
    tech = {"CL=F": {"chg_5d": 8.0}, "GC=F": {"chg_5d": 2.0}}
    res = geo.assess(news, tech, "2026-10-17", {"mideast": [{"date": f"d{i}", "n": 20} for i in range(10)]})
    me = next(t for t in res["themes"] if t["key"] == "mideast")
    assert me["stance"] == "긴장 고조" and me["intensity"] > 0.5 and res["themes"][0]["key"] == "mideast"
    assert res["impact"]["oil_up"] > 0 > res["impact"]["oil_down"]
    comp = co.load_companies()
    s_oil, notes = geo.ticker_geo("010950.KS", comp, res)   # S-Oil: 정유
    s_chem, notes2 = geo.ticker_geo("051910.KS", comp, res)  # LG화학: 유가 원가 부담
    assert s_oil > 50 > s_chem
    assert notes[0]["pol"] == 1 and notes2[0]["pol"] == -1 and "중동" in notes2[0]["text"]
    quiet = geo.assess({}, {}, "2026-10-17", {})
    assert all(t["intensity"] == 0 for t in quiet["themes"]) and geo.summary_lines(quiet)


def test_new_factors_are_recorded_but_do_not_move_scores():
    rows = []
    for i in range(12):
        f = {"forward_pe": 10 + i, "roe": 0.1 + i / 100, "op_margin": 0.2, "debt_to_equity": 50, "target_mean": None,
             "earnings_growth": 0.1, "sector": "Tech"}
        base = {"ticker": f"T{i}", "market": "us", "name": f"T{i}", "fund": f,
                "tech": {"last": 100, "chg_60d": i, "ta": {"scores": {"trend": 50, "momentum": 50, "timing": 50, "total": 50}}}}
        rows.append(base)
    a = score_universe(rows)
    for i, r in enumerate(rows):
        r["revision"] = i - 6.0
        r["geo"] = 30.0 + i
    b = score_universe(rows)
    assert a["revision"].isna().all() and b["revision"].notna().all() and b["geo"].iloc[-1] == 41.0
    assert b.sort_values("revision")["ticker"].iloc[-1] == "T11"
    for h in HORIZON_WEIGHTS:
        assert "revision" not in HORIZON_WEIGHTS[h] and "geo" not in HORIZON_WEIGHTS[h]
        assert (a[f"score_{h}"].values == b[f"score_{h}"].values).all()


def test_demo_financials_deterministic():
    x, y = fin.fetch_demo("MU", {}), fin.fetch_demo("MU", {})
    assert x["annual"]["revenue"] == y["annual"]["revenue"] and x["ok"]
    assert fin.fetch_demo("SCHD", {"sector": "ETF"})["ok"] is False
    assert fin.money(1.5e12, "KRW") == "1.5조원" and fin.money(2.5e9, "USD") == "$2.5B"


def test_tone_uses_trailing_predicate():
    assert geo.tone(["대이란 제재 완화", "휴전 협상 중단", "긴장 고조 속 공습"]) == (round(1 / 3, 2), 2, 1)
