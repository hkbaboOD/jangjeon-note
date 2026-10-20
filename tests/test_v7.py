"""v7: 미장 장전 브리핑 · 실적 발표 일정 · 앱 배당 플랜 데이터."""
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pytest

from pipeline import briefing, dividend
from pipeline.common import load_config


def _q(last, chg, prev=None):
    return {"last": last, "chg_1d": chg, "date": "2026-10-20", "spark": [1, 2, 3], "prev": prev}


def test_us_eastern_offset_dst():
    assert briefing.us_eastern_offset(datetime(2026, 10, 20, 12, tzinfo=timezone.utc)) == -4
    assert briefing.us_eastern_offset(datetime(2026, 11, 2, 12, tzinfo=timezone.utc)) == -5   # 11/1(일) 종료
    assert briefing.us_eastern_offset(datetime(2026, 3, 9, 12, tzinfo=timezone.utc)) == -4    # 3/8(일) 시작
    assert briefing.us_eastern_offset(datetime(2026, 1, 15, 12, tzinfo=timezone.utc)) == -5


def test_earnings_soon_uses_market_timezone():
    now = datetime(2026, 10, 20, 12, 13, tzinfo=timezone.utc)  # KST 21:13, 미 동부 08:13
    ts = lambda y, m, d, h: datetime(y, m, d, h, tzinfo=timezone.utc).timestamp()  # noqa: E731
    rows = [{"ticker": "NFLX", "name": "넷플릭스", "fund": {"earnings_ts": ts(2026, 10, 20, 20)}},   # 오늘 장 마감 후
            {"ticker": "TSLA", "name": "테슬라", "fund": {"earnings_ts": ts(2026, 10, 21, 20)}},
            {"ticker": "OLD", "name": "지난", "fund": {"earnings_ts": ts(2026, 10, 19, 20)}},
            {"ticker": "FAR", "name": "먼", "fund": {"earnings_ts": ts(2026, 10, 30, 20)}},
            {"ticker": "NONE", "fund": {}}]
    got = briefing.earnings_soon(rows, now, 1, -4)
    assert [(e["ticker"], e["in_days"]) for e in got] == [("NFLX", 0), ("TSLA", 1)]


def test_build_us_brief():
    cfg = load_config()
    tech = {"ES=F": _q(6750, 0.5), "NQ=F": _q(25100, 0.7), "YM=F": _q(46200, 0.2), "^KS11": _q(3610.5, -1.2),
            "^KQ11": _q(870, -2.0), "^GSPC": _q(6700, 0.1), "^IXIC": _q(22800, 0.3), "^SOX": _q(6900, 1.0),
            "^VIX": _q(26.4, 8.0), "^TNX": _q(4.15, 1.2, 4.08), "CL=F": _q(64, 3.1), "KRW=X": _q(1412.3, 0.4),
            "DX-Y.NYB": _q(99, 0.1), "BZ=F": _q(67, 2.0), "GC=F": _q(4100, 0.2)}
    kr = [{"ticker": f"{i:06d}.KS", "name": f"종목{i}", "market": "kr", "tech": {"chg_1d": c}} for i, c in enumerate([-3, -1, 2, 5])]
    now = datetime(2026, 10, 20, 12, 13, tzinfo=timezone.utc)
    us = [{"ticker": "NFLX", "name": "넷플릭스", "fund": {"earnings_ts": datetime(2026, 10, 20, 20, tzinfo=timezone.utc).timestamp()}}]
    b = briefing.build_us(cfg, tech, kr, [], us, [], now)
    assert [q["ticker"] for q in b["futures"]] == ["ES=F", "NQ=F", "YM=F"]
    assert b["review"][0].startswith("코스피 3,610.50 (-1.20%)") and "원/달러 1,412.3원" in b["review"][0]
    assert b["breadth"]["kr"] == {"up": 2, "down": 2, "total": 4} and b["movers"]["kr"]["top"][0]["chg"] == 5
    t = " | ".join(b["tonight"])
    assert "미 지수 선물 강세" in t and "VIX 26.4 — 변동성 높은 구간" in t and "+7bp" in t and "WTI +3.1%" in t
    assert "오늘(미국 시간): 넷플릭스" in t
    assert b["us_open_kst"] == "22:30" and b["dst"] is True
    b2 = briefing.build_us(cfg, tech, kr, [], [], [], datetime(2026, 12, 1, 12, tzinfo=timezone.utc))
    assert b2["us_open_kst"] == "23:30"


def test_dividend_candidates_carry_plan_inputs():
    idx = pd.to_datetime([f"{y}-{m:02d}-10" for y in range(2021, 2027) for m in (3, 6, 9, 12) if (y, m) < (2026, 10)])
    divs = pd.Series(0.5, index=idx)
    cfg = load_config()["dividend_plan"]
    c = dividend.analyze("KO", {"name": "코카콜라", "type": "stock", "pool": "유니버스"}, divs, 70.0, {"currency": "USD"}, cfg)
    plan = dividend.build_plan([c], cfg, 1400.0)
    cand = plan["candidates"][0]
    assert cand["pool"] == "유니버스" and cand["price"] == 70.0 and cand["currency"] == "USD"
    assert len(cand["dps"]) == 12 and cand["dps"][2] == 0.5 and cand["dps"][0] == 0
    assert set(plan["defaults"]) >= {"capital", "max_picks", "single_cap", "covered_call_max_weight", "tax_us", "tax_kr"}


def test_workflow_routes_evening_schedule():
    wf = (Path(__file__).parents[1] / ".github/workflows/pipeline.yml").read_text(encoding="utf-8")
    for cron, job in (("37 21 * * 0-4", "morning"), ("13 12 * * 1-5", "evening")):
        assert f'cron: "{cron}"' in wf and f'"{cron}" ]; then\n            echo "job={job}"' in wf


@pytest.mark.skipif(shutil.which("node") is None, reason="node 없음")
def test_divplan_js():
    r = subprocess.run(["node", str(Path(__file__).parent / "js" / "divplan.test.js")], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr + r.stdout


def test_live_dividends_cache_handles_no_dividend_stocks(tmp_path, monkeypatch):
    """무배당 종목(빈 배당 이력)도 오류 없이 처리하고, 캐시 재사용 시 같은 결과."""
    from pipeline import data as data_mod

    monkeypatch.setattr(data_mod, "STATE_DIR", tmp_path)
    idx = pd.to_datetime(["2024-03-10", "2025-03-10"]).tz_localize("America/New_York")

    class T:
        def __init__(self, t):
            self.dividends = pd.Series([0.5, 0.6], index=idx) if t == "KO" else pd.Series(dtype=float)

    class YF:
        Ticker = T

    src = data_mod.LiveSource.__new__(data_mod.LiveSource)
    src.yf, src._cache, src._dirty = YF(), {}, False
    monkeypatch.setattr(data_mod.time, "sleep", lambda s: None)
    assert src.dividends("AMZN").empty
    ko = src.dividends("KO")
    assert list(ko.values) == [0.5, 0.6] and ko.index.tz is None
    src.flush()
    src2 = data_mod.LiveSource.__new__(data_mod.LiveSource)
    src2.yf, src2._cache, src2._dirty = None, {}, False  # 캐시만으로 응답(네트워크 호출 시 실패)
    assert src2.dividends("AMZN").empty and list(src2.dividends("KO").values) == [0.5, 0.6]
    last12 = src2.dividends("AMZN")
    assert last12[last12.index > pd.Timestamp("2020-01-01")].empty


def test_chart_files_live_on_charts_branch():
    root = Path(__file__).parents[1]
    wf = (root / ".github/workflows/pipeline.yml").read_text(encoding="utf-8")
    assert "git fetch --depth=1 origin charts" in wf and 'refs/heads/charts' in wf
    assert wf.index("차트 파일 복원") < wf.index("파이프라인 실행") < wf.index("차트 파일 저장") < wf.index("upload-pages-artifact")
    assert "web/data/charts/" in (root / ".gitignore").read_text(encoding="utf-8")


def test_dividend_reit_payout_not_penalized():
    end = pd.Timestamp.today().normalize() - pd.Timedelta(days=1)
    divs = pd.Series(0.26, index=pd.date_range(end=end, periods=66, freq="30D"))  # 매달 1회, 최근 12개월 12회
    cfg = load_config()["dividend_plan"]
    reit = dividend.analyze("O", {"name": "리얼티 인컴", "type": "reit"}, divs, 57.0, {"payout_ratio": 2.9}, cfg)
    stock = dividend.analyze("X", {"name": "X", "type": "stock"}, divs, 57.0, {"payout_ratio": 2.9}, cfg)
    assert reit["score"] > stock["score"] and not any("배당성향" in n for n in reit["notes"])
    assert reit["freq"] >= 12 and len(reit["months"]) >= 11
