"""실전 예측 기록·채점 + 챔피언/도전자 보정 검증."""
import numpy as np
import pandas as pd

from pipeline import forward as fwd
from pipeline.calibrate import MAX_STEP, calibrate
from pipeline.valuation import HORIZON_WEIGHTS
from tests.test_technical import frame


def synthetic_live(n_dates=160, n_tk=40, signal="value", strength=0.6, harmful=None, seed=0):
    """signal 팩터가 이후 수익률을 실제로 예측하고, harmful 팩터는 반대로 움직이는 가상 실전 기록."""
    rng = np.random.default_rng(seed)
    dates = [d.strftime("%Y-%m-%d") for d in pd.bdate_range("2026-01-01", periods=n_dates)]
    recs = []
    for d in dates:
        f = {k: rng.uniform(0, 100, n_tk) for k in fwd.FACTORS}
        z = (f[signal] - 50) / 29
        zh = (f[harmful] - 50) / 29 if harmful else 0
        for h in fwd.HORIZONS:
            f[f"r{h}"] = strength * z * np.sqrt(h) - (strength * zh * np.sqrt(h) if harmful else 0) + rng.normal(0, np.sqrt(h), n_tk)
        for i in range(n_tk):
            recs.append({"date": d, "ticker": f"T{i}", "market": "us", **{k: f[k][i] for k in fwd.FACTORS},
                         **{f"r{h}": f[f"r{h}"][i] for h in fwd.HORIZONS}})
    df = pd.DataFrame(recs)
    for name in fwd.SCORES:
        df[f"s_{name}"] = fwd.composite(df, HORIZON_WEIGHTS[name])
    for h in fwd.HORIZONS:
        df[f"x{h}"] = df[f"r{h}"] - df.groupby("date")[f"r{h}"].transform("mean")
    return df


def test_snapshot_roundtrip_and_returns(tmp_path):
    c = np.linspace(100, 130, 120)
    hist = {"AAA": frame(c), "BBB": frame(c[::-1])}
    d0 = hist["AAA"].index[50].strftime("%Y-%m-%d")
    scored = pd.DataFrame([{"ticker": t, "setup": "neutral", **{k: 60.0 for k in fwd.FACTORS},
                            "score_daily": 60, "score_weekly": 60, "score_monthly": 60} for t in hist])
    fwd.save_snapshot(scored, d0, {t: d0 for t in hist}, snap_dir=tmp_path)
    snaps = fwd.load_snapshots(tmp_path)
    assert len(snaps) == 2 and set(snaps["ticker"]) == {"AAA", "BBB"}
    live = fwd.attach_returns(snaps, hist)
    a = live[live.ticker == "AAA"].iloc[0]
    o, cl = hist["AAA"]["Open"].values, hist["AAA"]["Close"].values
    assert abs(a["r5"] - (cl[55] / o[51] - 1) * 100) < 1e-9  # 다음날 시가 진입, 5거래일 뒤 종가
    assert live[live.ticker == "BBB"].iloc[0]["r20"] < 0


def test_evaluate_detects_predictive_factor():
    ev = fwd.evaluate(synthetic_live())
    assert ev["ic"]["value"]["20"]["ic"] > 0.15 and ev["ic"]["value"]["20"]["t"] > 2
    assert abs(ev["ic"]["timing"]["20"]["ic"]) < 0.08


def test_better_model_is_adopted_gradually():
    live = synthetic_live(signal="quality", strength=0.8, harmful="timing")
    m = calibrate({"ok": False}, None, None, live)
    c = m["contest"]["daily"]
    assert c["chosen"] == "새 후보", c
    d = m["weights"]["daily"]
    assert d["quality"] > HORIZON_WEIGHTS["daily"]["quality"] and d["timing"] < HORIZON_WEIGHTS["daily"]["timing"]
    for k, v in d.items():
        assert abs(v - HORIZON_WEIGHTS["daily"].get(k, 0)) <= MAX_STEP + 0.002


def test_bad_current_model_rolls_back_to_default():
    live = synthetic_live(signal="value", strength=0.6, harmful="timing")
    bad = {h: dict(w) for h, w in HORIZON_WEIGHTS.items()}
    bad["daily"] = {"timing": 0.45, "value": 0.10, "quality": 0.15, "target": 0.15, "trend": 0.15}
    m = calibrate({"ok": False}, None, {"weights": bad, "version": 3}, live)
    assert m["contest"]["daily"]["chosen"] in ("기본", "새 후보")
    assert m["weights"]["daily"]["timing"] < 0.45  # 해로운 팩터 비중이 줄어듦


def test_not_enough_live_data_uses_backtest_only():
    live = synthetic_live(n_dates=25)
    m = calibrate({"ok": False}, None, None, live)
    assert "weekly" not in m["contest"] and "monthly" not in m["contest"]


def test_floors_and_cap_respected():
    live = synthetic_live(signal="growth", strength=1.5)
    prev = None
    for _ in range(12):  # 몇 주 연속 같은 근거 → 수렴해도 상·하한 유지
        prev = calibrate({"ok": False}, None, prev, live)
    w = prev["weights"]["monthly"]
    assert w.get("value", 0) >= 0.095 and w.get("quality", 0) >= 0.075
    assert max(w.values()) <= 0.455
