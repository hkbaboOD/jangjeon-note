"""배당 재투자 백테스트(스노우볼 방식)와 배당 점수 자가 보정."""
import numpy as np
import pandas as pd

from pipeline import dividend, divtest


def _flat(n=600, price=100.0, start="2016-01-01"):
    idx = pd.bdate_range(start, periods=n)
    return pd.Series(price, index=idx)


def test_drip_reinvests_after_tax_on_ex_date():
    close = _flat(10)
    divs = pd.Series([2.0], index=[close.index[3] + pd.Timedelta(hours=0)])
    v = divtest.drip_value(close, divs, tax=0.15)
    # 1주 + 2달러×(1−15%)/100 = 1.017주 → 평가액 101.7
    assert abs(v.iloc[2] - 100.0) < 1e-9 and abs(v.iloc[-1] - 101.7) < 1e-9


def test_drip_ex_date_on_holiday_moves_to_next_trading_day():
    close = _flat(10)
    sat = close.index[4] + pd.Timedelta(days=1)  # 금요일 다음 토요일일 수도, 평일 사이일 수도 → 다음 거래일로
    v = divtest.drip_value(close, pd.Series([1.0], index=[sat]), tax=0.0)
    assert abs(v.iloc[-1] - 101.0) < 1e-9


def test_holding_stats_split_price_and_dividend():
    idx = pd.bdate_range("2015-01-01", "2026-11-20")
    close = pd.Series(np.linspace(50, 100, len(idx)), index=idx)
    divs = pd.Series(0.5, index=idx[::63])
    st = divtest.holding_stats(close, divs, 0.15)
    assert set(st) == {"1y", "3y", "5y", "10y"}
    for v in st.values():
        assert abs(v["total"] - (v["price"] + v["div"])) < 0.02 and v["div"] > 0 and v["mdd"] <= 0
    assert st["10y"]["total"] > st["1y"]["total"]


def test_point_in_time_components_ignore_future():
    idx = pd.bdate_range("2015-01-01", "2026-06-30")
    close = pd.Series(100.0, index=idx)
    divs = pd.Series(1.0, index=idx[::63])
    t = pd.Timestamp("2022-06-30")
    a = divtest.pit_components(close, divs, t, {"type": "stock"}, 0.09)
    fut = pd.concat([divs, pd.Series(50.0, index=[pd.Timestamp("2023-03-01")])]).sort_index()
    close2 = close.copy()
    close2[close2.index > t] = 1.0  # 미래 가격 폭락
    b = divtest.pit_components(close2, fut, t, {"type": "stock"}, 0.09)
    assert a == b


def test_default_score_matches_original_formula():
    comp = dividend.components(0.045, 0.06, 1, 0.5, {"type": "stock"}, 0.09)
    old = 0.40 * (0.045 / 0.09 * 100) + 0.30 * (30 + 0.06 * 700) + 0.20 * 100 + 0.10 * 65
    assert abs(dividend.weighted(comp) - old) < 1e-9


def _samples(seed, signal):
    """월별 30종목: signal=True면 배당수익률 항목이 이후 수익을 설명."""
    rng = np.random.default_rng(seed)
    rows = []
    for t in pd.date_range("2016-01-31", periods=96, freq="ME"):
        for i in range(40):
            sy, sg, st = rng.uniform(0, 100, 3)
            fwd = (0.3 * sy if signal else 0) + rng.normal(0, 3)
            rows.append({"date": t, "ticker": f"T{i}", "s_yield": sy, "s_growth": sg, "s_sust": 70.0,
                         "s_stability": st, "over_cap": False, "fwd": fwd})
    return pd.DataFrame(rows)


def test_calibrate_moves_one_step_toward_evidence():
    res = divtest.calibrate(_samples(1, True), None, "2026-11-21")
    w0 = dividend.DEFAULT_WEIGHTS
    assert res["changed"] and res["weights"]["yield"] == round(w0["yield"] + divtest.STEP, 4)
    moved = sum(abs(res["weights"][k] - w0[k]) for k in divtest.KEYS)
    assert abs(moved - 2 * divtest.STEP) < 1e-9 and abs(sum(res["weights"].values()) - 0.8) < 1e-9
    assert min(res["weights"].values()) >= divtest.FLOOR and res["log"][0]["text"].startswith("배당 점수 가중치 조정")


def test_calibrate_keeps_weights_on_noise():
    for seed in range(3):
        res = divtest.calibrate(_samples(seed + 10, False), None, "2026-11-21")
        assert not res["changed"] and res["weights"] == {k: dividend.DEFAULT_WEIGHTS[k] for k in divtest.KEYS}


def test_rollback_toward_default_when_default_wins():
    prev = {"weights": {"yield": 0.20, "growth": 0.30, "stability": 0.30}, "version": 3}
    res = divtest.calibrate(_samples(1, True), prev, "2026-11-28")
    assert res["changed"] and res["version"] == 4
    assert res["weights"]["yield"] == 0.25  # 한 걸음(0.05)만 기본값/근거 쪽으로


def test_run_end_to_end_small():
    idx = pd.bdate_range("2015-01-01", "2026-11-20")
    rng = np.random.default_rng(0)
    close_map, div_map, cands = {}, {}, []
    for i in range(14):
        t = f"D{i}"
        close_map[t] = pd.Series(100 * np.exp(np.cumsum(rng.normal(0.0002, 0.01, len(idx)))), index=idx)
        div_map[t] = pd.Series(0.5 + i * 0.1, index=idx[::63])
        cands.append({"ticker": t, "type": "stock"})
    close_map["SPY"] = pd.Series(np.linspace(200, 600, len(idx)), index=idx)
    div_map["SPY"] = pd.Series(1.5, index=idx[::63])
    cfg = {"tax": {"us": 0.15, "kr": 0.154}, "max_yield_cap": 0.09}
    out = divtest.run(close_map, div_map, cands, cfg, None, "2026-11-21")
    assert len(out["tickers"]) == 14 and out["samples"] > 500 and out["bench"]["us"]["hold"]["5y"]["total"] > 0
    r = out["tickers"]["D0"]["roll"]
    assert r["n"] > 100 and 0 <= r["win_rate"] <= 100
