"""추천 예측 vs 실제 확정 채점 · 승률 가드 · PDCA 리포트."""
import json

import numpy as np

from pipeline import common
from pipeline import pdca
from pipeline import run as R
from pipeline.calibrate import _holdout_win
from pipeline.valuation import HORIZON_WEIGHTS
from tests.test_forward import synthetic_live


def test_pick_is_finalized_after_holding_period(tmp_path, monkeypatch):
    monkeypatch.setattr(common, "DATA_DIR", tmp_path)
    today = common.now_kst().date()
    d_old = str(np.busday_offset(today, -6, roll="backward"))   # 6거래일 전 → 오늘(5일) 추천은 확정
    d_new = str(np.busday_offset(today, -2, roll="backward"))   # 2거래일 전 → 아직 진행 중
    items = [
        {"date": d_old, "horizon": "daily", "ticker": "AAA", "name": "A", "entry": 100, "score": 70, "setup": "상승추세 속 눌림목",
         "bench": "^GSPC", "bench_entry": 1000},
        {"date": d_old, "horizon": "weekly", "ticker": "BBB", "name": "B", "entry": 100, "score": 70, "bench": "^GSPC", "bench_entry": 1000},
        {"date": d_new, "horizon": "daily", "ticker": "CCC", "name": "C", "entry": 100, "score": 70, "bench": "^GSPC", "bench_entry": 1000},
    ]
    (tmp_path / "track.json").write_text(json.dumps({"items": items}))
    R._update_track_record({}, {"AAA": 104, "BBB": 90, "CCC": 101, "^GSPC": 1010})
    tr = json.loads((tmp_path / "track.json").read_text())
    a, b, c = tr["items"]
    assert a["final"]["win"] is True and abs(a["final"]["ex"] - 3.0) < 1e-6  # +4% − 지수 +1% = +3%p
    assert "final" not in b  # 이번 주(20일) 추천은 아직
    assert "final" not in c
    assert tr["matured"]["daily"]["count"] == 1 and tr["matured"]["daily"]["win_rate"] == 100


def test_holdout_win_rate_reflects_signal():
    live = synthetic_live(signal="quality", strength=0.8)
    good = {"quality": 1.0}
    bad = {"quality": -1.0}
    assert _holdout_win(live, good, 20) > 60
    assert _holdout_win(live, bad, 20) < 40


def test_pdca_report_structure():
    model = {"version": 3, "weights": HORIZON_WEIGHTS, "basis": {}, "changed": True, "contest": {},
             "log": [{"date": "2026-10-17", "text": "[오늘] 가치 0.25→0.28"}]}
    ev = {"days": 12, "recent": {}, "top3": {}}
    track = {"items": [{"date": "2026-10-10", "horizon": "daily", "name": "A", "score": 70, "setup": "x",
                        "final": {"ret": 2.0, "ex": 1.0, "win": True, "days": 5, "closed": "2026-10-16"}}],
             "matured": {"daily": {"count": 1, "win_rate": 100.0, "avg_ex": 1.0, "recent_win": 100.0, "recent_n": 1}}}
    r = pdca.build(model, ev, track, None, "2026-10-17")
    assert r["plan"]["version"] == 3 and r["do"]["days_recorded"] == 12
    assert r["check"]["closed_this_week"][0]["name"] == "A"
    assert r["act"]["changes"] == ["[오늘] 가치 0.25→0.28"]
    assert any("실전" in n for n in r["next"])
    r2 = pdca.build(model, ev, track, r, "2026-10-24")
    assert len(r2["history"]) == 2 and r2["check"]["win_delta"]["daily"] == 0.0
