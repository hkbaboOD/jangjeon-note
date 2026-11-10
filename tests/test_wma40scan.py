"""40주선 돌파·이탈 스캐너 (2026-11-10 사용자 요청)."""
import numpy as np
import pandas as pd

from pipeline import wma40scan as w


def _df(closes, vols=None, end="2026-11-06"):
    idx = pd.bdate_range(end=end, periods=len(closes))
    c = np.asarray(closes, dtype=float)
    v = np.asarray(vols if vols is not None else [1000.0] * len(c), dtype=float)
    return pd.DataFrame({"Open": c, "High": c, "Low": c, "Close": c, "Volume": v}, index=idx)


def test_cross_up_detected_with_volume_ratio():
    base = list(np.linspace(120, 100, 280))  # 하락 → 40주선 아래
    jump = [130.0] * 5                        # 마지막 주 급등 → 돌파
    vols = [1000.0] * 280 + [3000.0] * 5
    ev = w.cross_event(_df(base + jump, vols))
    assert ev["kind"] == "up" and ev["weeks_ago"] == 0 and ev["dist"] > 0
    assert ev["vol_ratio"] == 3.0


def test_cross_down_and_reverted_cross_ignored():
    up = list(np.linspace(100, 150, 280))
    ev = w.cross_event(_df(up + [100.0] * 5))
    assert ev["kind"] == "down"
    # 2주 전 이탈했다가 이번 주 다시 40주선 위로 → 되돌아감(이번 주 '돌파'로 잡힘, 이탈은 아님)
    ev2 = w.cross_event(_df(up + [100.0] * 5 + [160.0] * 5))
    assert ev2["kind"] == "up"
    assert w.cross_event(_df(up)) is None  # 교차 없음


def test_cross_uses_only_past_data():
    closes = list(np.linspace(120, 100, 280)) + [130.0] * 5
    full = _df(closes)
    cut = full.iloc[:-5]
    assert w.cross_event(cut) is None  # 돌파 전 시점에서는 신호 없음


def test_promise_and_scan_filters():
    assert w.promise({"quality": 80, "value": 70, "target": 60, "growth": 50}) == 68.5
    assert w.promise({"growth": 90}) is None  # 핵심 자료 부족
    hist = {"AAA": _df(list(np.linspace(120, 100, 280)) + [130.0] * 5),
            "BBB": _df(list(np.linspace(120, 100, 280)) + [130.0] * 5)}
    scored = pd.DataFrame([{"ticker": "AAA", "quality": 80, "value": 80, "target": 60, "growth": 60, "name": "A"},
                           {"ticker": "BBB", "quality": 20, "value": 30, "target": 40, "growth": 50, "name": "B"}])
    res = w.scan(hist, scored, {"AAA": "에이"}, {"us": ["AAA", "BBB"]})
    m = res["markets"]["us"]
    assert [x["ticker"] for x in m["up"]] == ["AAA"] and m["n_cross_up"] == 2
    assert m["up"][0]["name"] == "에이" and m["above_pct"] == 100.0


def test_events_scored_after_20_days():
    closes = list(np.linspace(120, 100, 280)) + [130.0] * 5
    h = _df(closes + [140.0] * 25, end="2026-12-11")
    b = _df([100.0] * 310, end="2026-12-11")
    det = h.index[284].strftime("%Y-%m-%d")
    hist_then = {"AAA": h.iloc[:285], "^GSPC": b.iloc[:285]}
    res = w.scan(hist_then, pd.DataFrame([{"ticker": "AAA", "quality": 80, "value": 80, "target": 60, "growth": 60}]),
                 {}, {"us": ["AAA"]})
    ev = w.update_events([], res, hist_then, det)
    assert len(ev) == 1 and "final" not in ev[0]
    ev = w.update_events(ev, res, hist_then, det)
    assert len(ev) == 1  # 같은 신호는 한 번만 기록
    ev = w.update_events(ev, {"markets": {"us": {"up": [], "down": []}}}, {"AAA": h, "^GSPC": b}, "2026-12-11")
    f = ev[0]["final"]
    assert f["hit"] and round(f["ret"], 2) == round((140 / 130 - 1) * 100, 2)
    assert w.event_summary(ev)["up"] == {"n": 1, "pending": 0, "hit_rate": 100.0, "avg_ex": f["ex"]}
