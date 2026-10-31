"""데이터 정합성: 마지막 봉 누락 보완, 같은 가격 봉 중복 예측 제거 (2026-10-24 주간 점검)."""
import numpy as np
import pandas as pd

from pipeline import data as data_mod
from pipeline import forward as fwd
from tests.test_technical import frame


def _df(end, n=80):
    idx = pd.bdate_range(end=end, periods=n)
    c = np.linspace(100, 120, n)
    return pd.DataFrame({"Open": c, "High": c + 1, "Low": c - 1, "Close": c, "Volume": 1000.0}, index=idx)


def test_stale_tickers_compares_within_market_and_skips_24h_quotes():
    hist = {"^GSPC": _df("2026-10-22"), "AAPL": _df("2026-10-21"), "MU": _df("2026-10-21"),
            "005930.KS": _df("2026-10-22"), "^KS11": _df("2026-10-21"),
            "KRW=X": _df("2026-10-23"), "CL=F": _df("2026-10-23")}
    lag = set(data_mod.stale_tickers(hist))
    # 미국 종목은 미 지수보다 하루 늦음, 코스피 지수는 국내 종목보다 하루 늦음. 환율·선물은 비교에서 제외
    assert lag == {"AAPL", "MU", "^KS11"}
    assert data_mod.stale_tickers({"AAPL": _df("2026-10-22"), "005930.KS": _df("2026-10-23")}) == []


class _FakeTicker:
    def __init__(self, t, full):
        self.t, self.full = t, full

    def history(self, **kw):
        df = self.full[self.t].copy()
        df.index = df.index.tz_localize("America/New_York")
        return df


class _FakeYF:
    def __init__(self, full):
        self.full = full

    def Ticker(self, t):
        return _FakeTicker(t, self.full)


def test_refresh_stale_replaces_only_lagging_tickers():
    full = {"AAPL": _df("2026-10-22", 81)}
    src = data_mod.LiveSource.__new__(data_mod.LiveSource)  # 네트워크 없이 메서드만 검증
    src.yf = _FakeYF(full)
    out = {"^GSPC": _df("2026-10-22"), "AAPL": _df("2026-10-21")}
    keep = out["^GSPC"]
    assert src._refresh_stale(out, "2y") == 1
    assert out["AAPL"].index[-1] == pd.Timestamp("2026-10-22") and out["AAPL"].index.tz is None
    assert out["^GSPC"] is keep


def test_duplicate_price_bar_predictions_are_scored_once(tmp_path):
    c = np.linspace(100, 130, 120)
    hist = {"AAA": frame(c), "BBB": frame(c[::-1])}
    d0 = hist["AAA"].index[50].strftime("%Y-%m-%d")
    d1 = hist["AAA"].index[51].strftime("%Y-%m-%d")
    scored = pd.DataFrame([{"ticker": t, "setup": "neutral", **{k: 60.0 for k in fwd.FACTORS},
                            "score_daily": 60, "score_weekly": 60, "score_monthly": 60} for t in hist])
    fwd.save_snapshot(scored, "2026-10-16", {t: d0 for t in hist}, snap_dir=tmp_path)
    fwd.save_snapshot(scored, "2026-10-17", {"AAA": d0, "BBB": d1}, snap_dir=tmp_path)  # 토요일 재실행: AAA는 같은 봉
    snaps = fwd.load_snapshots(tmp_path)
    assert len(snaps) == 3
    aaa = snaps[snaps.ticker == "AAA"]
    assert len(aaa) == 1 and aaa.iloc[0]["date"] == "2026-10-16"  # 먼저 기록된 예측만 남김
    assert set(snaps[snaps.ticker == "BBB"]["pxd"]) == {d0, d1}


# ── 2026-10-31 주간 점검: 미장 장전 '국내장 마감'이 매일 하루 전 봉으로 표시되던 문제 ──
from datetime import date, datetime, timedelta, timezone  # noqa: E402

from pipeline import briefing  # noqa: E402
from pipeline.common import load_config  # noqa: E402

KST = timezone(timedelta(hours=9))


def test_last_kr_session_handles_late_and_weekend_runs():
    f = data_mod.last_kr_session
    assert f(datetime(2026, 10, 30, 21, 13, tzinfo=KST)) == date(2026, 10, 30)  # 금 저녁(정시)
    assert f(datetime(2026, 10, 31, 3, 4, tzinfo=KST)) == date(2026, 10, 30)    # 토 새벽(지연 실행) → 금요일
    assert f(datetime(2026, 10, 27, 3, 41, tzinfo=KST)) == date(2026, 10, 26)   # 화 새벽 → 월요일
    assert f(datetime(2026, 10, 26, 6, 37, tzinfo=KST)) == date(2026, 10, 23)   # 월 아침 → 금요일
    assert f(datetime(2026, 10, 30, 15, 0, tzinfo=KST)) == date(2026, 10, 29)   # 장중 → 전날


def test_stale_tickers_with_expected_date_catches_whole_market_lag():
    hist = {"^KS11": _df("2026-10-29"), "005930.KS": _df("2026-10-29"), "^GSPC": _df("2026-10-30"), "KRW=X": _df("2026-10-30")}
    assert data_mod.stale_tickers(hist) == []  # 그룹 안 비교만으로는 못 잡음(실제 증상)
    assert set(data_mod.stale_tickers(hist, {"kr": date(2026, 10, 30)})) == {"^KS11", "005930.KS"}


def test_refresh_stale_stops_after_repeated_misses():
    calls = []

    class _NoNewYF:
        def Ticker(self, t):
            calls.append(t)
            return _FakeTicker(t, {t: _df("2026-10-29")})

    src = data_mod.LiveSource.__new__(data_mod.LiveSource)
    src.yf = _NoNewYF()
    out = {f"{i:06d}.KS": _df("2026-10-29") for i in range(20)}
    assert src._refresh_stale(out, "2y", expect={"kr": date(2026, 10, 30)}, max_miss=5) == 0
    assert len(calls) == 5  # 제공처에 아직 봉이 없으면 5번에서 멈춤


def test_build_us_flags_stale_kr_close():
    cfg = load_config()
    q = lambda d: {"last": 5937.66, "chg_1d": -1.24, "date": d, "spark": [1, 2, 3]}  # noqa: E731
    now = datetime(2026, 10, 30, 18, 4, tzinfo=timezone.utc)  # KST 10/31(토) 03:04
    stale = briefing.build_us(cfg, {"^KS11": q("2026-10-29"), "^KQ11": q("2026-10-29")}, [], [], [], [], now)
    assert stale["kr_stale"] is True and stale["review"][0].startswith("⚠ 국내장 데이터가 10/29 마감 기준")
    assert stale["review"][1].startswith("코스피 5,937.66")
    ok = briefing.build_us(cfg, {"^KS11": q("2026-10-30"), "^KQ11": q("2026-10-30")}, [], [], [], [], now)
    assert ok["kr_stale"] is False and ok["review"][0].startswith("코스피")
