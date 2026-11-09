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


# ── 2026-11-07 주간 점검: 일봉 제공 지연(개별 재조회로도 안 채워짐) → 60분봉으로 마지막 일봉 보완 ──
def _hourly(day, tz, closes):
    idx = pd.date_range(f"{day} 09:30", periods=len(closes), freq="h", tz=tz)
    c = np.array(closes, dtype=float)
    return pd.DataFrame({"Open": c - 0.5, "High": c + 1, "Low": c - 1, "Close": c, "Volume": 10.0}, index=idx)


def test_append_intraday_days_builds_missing_daily_bar():
    daily = _df("2026-11-05")
    intra = pd.concat([_hourly("2026-11-05", "America/New_York", [119, 120]),
                       _hourly("2026-11-06", "America/New_York", [121, 125, 118, 122])])
    merged, added = data_mod.append_intraday_days(daily, intra)
    assert added == 1 and merged.index[-1] == pd.Timestamp("2026-11-06") and merged.index.tz is None
    bar = merged.iloc[-1]
    assert (bar.Open, bar.High, bar.Low, bar.Close, bar.Volume) == (120.5, 126.0, 117.0, 122.0, 40.0)
    assert len(merged) == len(daily) + 1  # 이미 있는 11/05는 덮어쓰지 않음
    same, n = data_mod.append_intraday_days(daily, _hourly("2026-11-05", "Asia/Seoul", [1, 2]))
    assert n == 0 and same is daily


def test_refresh_stale_falls_back_to_intraday_when_daily_lags():
    class _LagYF:
        def Ticker(self, t):
            return _FakeTicker(t, {t: _df("2026-11-05")})  # 개별 일봉도 하루 늦음(실제 증상)

        def download(self, chunk, **kw):
            assert kw["interval"] == "60m"
            frames = {t: _hourly("2026-11-06", "Asia/Seoul", [100, 101]) for t in chunk}
            return pd.concat(frames, axis=1)

    src = data_mod.LiveSource.__new__(data_mod.LiveSource)
    src.yf = _LagYF()
    out = {"^KS11": _df("2026-11-05"), "005930.KS": _df("2026-11-05"), "^GSPC": _df("2026-11-06")}
    assert src._refresh_stale(out, "2y", expect={"kr": date(2026, 11, 6)}) == 2
    assert out["^KS11"].index[-1] == pd.Timestamp("2026-11-06") and out["005930.KS"]["Close"].iloc[-1] == 101
    assert data_mod.stale_tickers(out, {"kr": date(2026, 11, 6)}) == []


# ── 2026-11-09 사용자 요청: 전통적 40주선(와인스타인 단계 분석) 점수 ──
from pipeline import technical as tech_mod  # noqa: E402
from pipeline import valuation as val_mod  # noqa: E402


def _series(vals, end="2026-11-06"):
    return pd.Series(np.asarray(vals, dtype=float), index=pd.bdate_range(end=end, periods=len(vals)))


def test_wma40_stage_and_score():
    up = tech_mod.wma40(_series(np.linspace(100, 200, 300)))
    down = tech_mod.wma40(_series(np.linspace(200, 100, 300)))
    assert up["stage"] == 2 and up["dist"] > 0 and up["slope"] > 0.5 and up["score"] >= 80
    assert down["stage"] == 4 and down["score"] <= 20
    # 오르는 40주선 아래로 급락 → 3단계(천장 형성)
    top = tech_mod.wma40(_series(list(np.linspace(100, 200, 290)) + [150] * 10))
    assert top["stage"] == 3
    # 40주 평균은 주봉 종가 기준: 주 마지막 거래일 값만 사용(미래 데이터 미사용)
    s = _series(np.linspace(100, 200, 300))
    wk = s.groupby(s.index.to_period("W-FRI")).last()
    assert abs(up["ma"] - wk.iloc[-40:].mean()) < 1e-6
    assert tech_mod.wma40(_series(np.linspace(100, 120, 150))) is None  # 44주 미만이면 계산 안 함


def test_analyze_exposes_wma40_and_stage4_signal():
    c = np.linspace(200, 100, 320)
    df = pd.DataFrame({"Open": c, "High": c + 1, "Low": c - 1, "Close": c, "Volume": 1000.0},
                      index=pd.bdate_range(end="2026-11-06", periods=320))
    ta = tech_mod.analyze(df)
    assert ta["wma40"]["stage"] == 4 and "wma40" in ta["scores"]
    assert any(s["code"] == "wma40_stage4" and s["pol"] < 0 for s in ta["signals"])


def test_weights_include_wma40_within_guards():
    for h, w in val_mod.HORIZON_WEIGHTS.items():
        assert abs(sum(w.values()) - 1) < 1e-9
        assert w["wma40"] > 0 and max(w.values()) <= 0.45 and w["value"] >= 0.10 and w["quality"] >= 0.08


def test_pick_top_excludes_stage4():
    def row(t, stage, sector):
        ta = {"scores": {"trend": 60, "momentum": 60, "timing": 70, "total": 63, "wma40": 15 if stage == 4 else 80},
              "setup": "neutral", "wma40": {"stage": stage}}
        return {"ticker": t, "market": "us", "name": t, "tech": {"ta": ta, "last": 100.0},
                "fund": {"forward_pe": 8 if stage == 4 else 12, "peg": 1, "pb": 1, "roe": 0.2, "op_margin": 0.2,
                         "debt_to_equity": 50, "target_mean": 130, "analyst_count": 10, "sector": sector}}
    rows = [row("AAA", 4, "S1"), row("BBB", 2, "S2")] + [row(f"Z{i}", 2, f"Z{i}") for i in range(6)]
    df = val_mod.score_universe(rows)
    assert df.loc[df.ticker == "AAA", "wma40"].iloc[0] == 15
    picks = val_mod.pick_top(df, "daily", n=10)
    assert "AAA" not in [p["ticker"] for p in picks] and all(p["stage40"] != 4 for p in picks)
