"""차트 분석 엔진 단위테스트 — 합성 가격 패턴으로 각 신호가 의도대로 잡히는지 확인.

    pip install pytest && python -m pytest -q
"""
import numpy as np
import pandas as pd

from pipeline.technical import analyze, chart_series, last_cross, rsi_series


def frame(close, spread=0.006, vol=None, opens=None):
    close = np.asarray(close, dtype=float)
    idx = pd.bdate_range("2024-01-01", periods=len(close))
    o = np.r_[close[0], close[:-1]] if opens is None else np.asarray(opens, dtype=float)
    hi = np.maximum(o, close) * (1 + spread)
    lo = np.minimum(o, close) * (1 - spread)
    v = np.full(len(close), 1e6) if vol is None else np.asarray(vol, dtype=float)
    return pd.DataFrame({"Open": o, "High": hi, "Low": lo, "Close": close, "Volume": v}, index=idx)


def codes(res):
    return {s["code"] for s in res["signals"]}


def test_uptrend_is_fully_aligned():
    rng = np.random.default_rng(1)
    c = 100 * np.exp(np.cumsum(rng.normal(0.0015, 0.006, 500)))
    r = analyze(frame(c))
    assert r["trend_label"] == "완전 정배열"
    assert "ma_aligned" in codes(r)
    assert r["scores"]["trend"] >= 75
    assert r["setup"] in ("uptrend", "breakout", "pullback")
    assert r["levels"]["support"] and all(s["price"] < c[-1] for s in r["levels"]["support"])


def test_downtrend_is_reversed_and_flagged():
    rng = np.random.default_rng(2)
    c = 100 * np.exp(np.cumsum(rng.normal(-0.0015, 0.006, 500)))
    r = analyze(frame(c))
    assert r["trend_label"] == "완전 역배열"
    assert r["scores"]["trend"] <= 25
    assert r["setup"] in ("downtrend", "reversal")


def test_golden_cross_detected_with_days_ago():
    a = pd.Series([1, 2, 3, 4, 5, 6], dtype=float)
    b = pd.Series([3, 3, 3, 3.5, 4.5, 5], dtype=float)
    kind, ago = last_cross(a, b, lookback=10)
    assert kind == "golden" and ago == 2  # 인덱스3(마지막에서 2봉 전)에 처음 위로


def test_short_term_golden_cross_after_v_bottom():
    c = np.r_[np.linspace(100, 70, 300), np.linspace(70, 81, 22)]
    r = analyze(frame(c))
    assert any(x["pair"] == [20, 50] and x["kind"] == "golden" for x in r["crosses"])


def test_rsi_reclaim_30_after_capitulation():
    c = np.r_[np.linspace(100, 101, 300), np.linspace(101, 80, 15), [83, 86, 89]]
    rs = rsi_series(pd.Series(c))
    assert rs.iloc[-4] < 30 < rs.iloc[-1]
    r = analyze(frame(c))
    assert "rsi_reclaim30" in codes(r)
    assert r["scores"]["timing"] > 55


def test_bullish_divergence():
    # 급락으로 첫 저점(RSI 매우 낮음) → 반등 → 완만한 하락으로 조금 더 낮은 두 번째 저점(RSI는 높음) → 반등
    base = np.linspace(120, 118, 260)
    leg1 = np.linspace(118, 96, 8)              # 급락
    bounce = np.linspace(96, 104, 12)
    leg2 = np.linspace(104, 95.2, 22)            # 완만한 하락, 저점은 더 낮음
    up = np.linspace(95.2, 97.5, 6)
    r = analyze(frame(np.r_[base, leg1, bounce, leg2, up], spread=0.002))
    assert "div_bull" in codes(r), [s["text"] for s in r["signals"]]


def test_hammer_candle():
    c = list(np.linspace(100, 80, 300))
    df = frame(c)
    # 마지막 봉: 시가 80, 저가 76, 종가 80.4, 고가 80.5 → 긴 아래꼬리
    df.iloc[-1] = [80.0, 80.5, 76.0, 80.4, 1e6]
    r = analyze(df)
    assert "candle_hammer" in codes(r)


def test_volume_spike_up():
    c = np.r_[np.linspace(100, 110, 300), [113.5]]
    vol = np.r_[np.full(300, 1e6), [3.2e6]]
    r = analyze(frame(c, vol=vol))
    assert "vol_up" in codes(r)


def test_squeeze_detected():
    rng = np.random.default_rng(3)
    wild = 100 + np.cumsum(rng.normal(0, 1.5, 280))
    calm = wild[-1] + np.cumsum(rng.normal(0, 0.05, 40))
    r = analyze(frame(np.r_[wild, calm]))
    assert "bb_squeeze" in codes(r)


def test_short_history_is_handled():
    r = analyze(frame(np.linspace(10, 12, 40)))
    assert r["setup"] == "insufficient"


def test_chart_series_lengths():
    c = np.linspace(100, 150, 400)
    s = chart_series(frame(c))
    n = len(s["d"])
    assert n == 130 and all(len(s["ma"][k]) == n for k in s["ma"]) and len(s["rsi"]) == n
    assert s["ma"]["250"][-1] is not None


def test_pullback_in_uptrend():
    rng = np.random.default_rng(4)
    up = 100 * np.exp(np.cumsum(rng.normal(0.0012, 0.004, 460)))
    dip = up[-1] * np.exp(np.cumsum(np.full(6, -0.008)))  # 약 -5% 조정
    r = analyze(frame(np.r_[up, dip]))
    assert r["setup"] == "pullback", (r["setup"], r["trend_label"], r["values"]["rsi"])
    assert r["scores"]["trend"] >= 55


def test_long_term_turn_after_golden_cross():
    c = np.r_[np.linspace(100, 80, 300), np.linspace(80, 87.2, 90)]
    r = analyze(frame(c))
    assert any(x["pair"] == [50, 250] and x["kind"] == "golden" for x in r["crosses"])
    assert r["setup"] == "turn"
    assert "golden" in r["tags"]
