"""백테스트 엔진 검증.

1) 랜덤워크(예측 불가능한 데이터)에서는 어떤 셋업도 유의한 초과수익이 나오면 안 됨 → 룩어헤드(미래 참조) 없음
2) 미래 수익률을 '몰래' 점수로 넣으면 IC·5분위가 이를 확실히 잡아내야 함 → 통계 계산이 제대로 동작
3) 진입=다음날 시가, 비용 차감 확인
"""
import numpy as np

import pipeline.backtest as bt
from tests.test_technical import frame


def universe(k=24, n=700, seed=0):
    rng = np.random.default_rng(seed)
    out = {}
    for j in range(k):
        c = 100 * np.exp(np.cumsum(rng.normal(0.0003, 0.015, n)))
        out[f"T{j}"] = frame(c, spread=0.008)
    return out


def test_random_walk_has_no_edge():
    res = bt.run(universe(), workers=1)
    assert res["ok"] and res["samples"] > 1000
    sig = []
    for s, b in res["by_setup"].items():
        st = b["all"]["20"]["ex"]
        if st:
            sig.append((s, st["t"], st["avg"]))
            assert abs(st["t"]) < 3.5, (s, st)
    assert sig, "통계가 하나도 계산되지 않음"
    for f, d in res["ic"].items():
        if "20" in d:
            assert abs(d["20"]["ic"]) < 0.06, (f, d)


def test_planted_signal_is_detected(monkeypatch):
    hist = universe(k=20, n=600, seed=1)
    fut = {}
    for t, df in hist.items():
        c, o = df["Close"].values, df["Open"].values
        for i in range(len(df) - 21):
            fut[(df.index[i], round(float(c[i]), 6))] = c[i + 20] / o[i + 1] - 1

    def cheat(sl, tcfg=None):  # 시점 t의 '미래 20일 수익률'을 점수로 (일부러 미래 참조)
        v = fut.get((sl.index[-1], round(float(sl["Close"].iloc[-1]), 6)), 0.0) * 1000
        return {"setup": "neutral", "signals": [], "scores": {"total": v, "trend": v, "momentum": -v, "timing": 0.0}}

    monkeypatch.setattr(bt, "analyze", cheat)
    res = bt.run(hist, workers=1)
    assert res["ic"]["total"]["20"]["ic"] > 0.5
    assert res["ic"]["momentum"]["20"]["ic"] < -0.5
    q = res["quintiles"]["total"]["20"]
    assert q[4] > q[0]


def test_next_open_entry_and_cost():
    c = np.full(600, 100.0)  # 표본 ≥ 30개가 되도록
    df = frame(c, spread=0.0)
    res = bt.run({"005930.KS": df}, workers=1)
    st = res["base"]["kr"]["5"]["abs"]
    assert st and abs(st["avg"] + bt.COST["kr"]) < 1e-9
