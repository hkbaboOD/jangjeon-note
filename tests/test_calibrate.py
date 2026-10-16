"""자가 보정 안전장치 검증."""
from pipeline.calibrate import ADJ_CAP, MAX_ADJ_STEP, MAX_STEP, calibrate
from pipeline.valuation import HORIZON_WEIGHTS


def bt(ic_total=0.0, t_total=0.0, timing=(0.0, 0.0), setup_ex=None):
    ic = {f: {h: {"ic": 0.0, "t": 0.0, "dates": 100} for h in ("5", "20", "60")} for f in ("total", "trend", "momentum", "timing")}
    for h in ("5", "20", "60"):
        ic["total"][h] = {"ic": ic_total, "t": t_total, "dates": 100}
        ic["timing"][h] = {"ic": timing[0], "t": timing[1], "dates": 100}
    by_setup = {}
    if setup_ex:
        avg, t = setup_ex
        st = {"n": 500, "n_eff": 200, "avg": avg, "med": avg, "win": 55, "t": t}
        by_setup["pullback"] = {"all": {h: {"ex": st, "abs": st} for h in ("5", "20", "60")}}
    return {"ok": True, "ic": ic, "by_setup": by_setup, "period": ["2021-01-01", "2026-01-01"]}


def test_weights_always_sum_to_one_and_fundamentals_kept():
    m = calibrate(bt(0.05, 4.0, (0.06, 4.0)), None, None)
    for h, w in m["weights"].items():
        assert abs(sum(w.values()) - 1) < 0.01
        for k in ("value", "quality", "target"):
            assert w.get(k, 0) > 0.1


def test_step_limit_per_week():
    m1 = calibrate(bt(0.05, 4.0, (0.08, 5.0)), None, None)
    for h, w in m1["weights"].items():
        for k, v in w.items():
            assert abs(v - HORIZON_WEIGHTS[h].get(k, 0)) <= MAX_STEP + 0.001  # 반올림 오차만 허용


def test_insignificant_results_do_not_move_chart_split():
    m = calibrate(bt(0.03, 2.5, (0.04, 1.2)), None, None)  # 타이밍 IC t 1.2 → 무시
    d = m["weights"]["daily"]
    assert abs(d["timing"] / (d["timing"] + d["trend"]) - 2 / 3) < 0.05


def test_setup_adjustment_bounded_and_gradual():
    m1 = calibrate(bt(setup_ex=(4.0, 5.0)), None, None)  # 초과수익 +4%p → 목표 +5점(상한)
    assert m1["setup_adj"]["weekly"]["pullback"] == MAX_ADJ_STEP
    m2 = calibrate(bt(setup_ex=(4.0, 5.0)), None, m1)
    m3 = calibrate(bt(setup_ex=(4.0, 5.0)), None, m2)
    assert m3["setup_adj"]["weekly"]["pullback"] == ADJ_CAP
    m4 = calibrate(bt(setup_ex=(4.0, 1.0)), None, m3)  # 유의성 사라지면 0으로 서서히 복귀
    assert m4["setup_adj"]["weekly"]["pullback"] == ADJ_CAP - MAX_ADJ_STEP


def test_no_evidence_is_stable_week_to_week():
    b = bt(0.0, 0.0)
    m1 = calibrate(b, None, None)
    m2 = calibrate(b, None, m1)
    assert m2["weights"] == m1["weights"] or not m2["changed"] or m2["version"] == m1["version"] + 1
    m3 = calibrate(b, None, calibrate(b, None, m2))
    assert m3["weights"] == calibrate(b, None, m3)["weights"]  # 수렴
