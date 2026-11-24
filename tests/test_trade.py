"""매매 판단 검증(지금 매수/기다렸다 매수/보류 + 손절·목표가·기간 청산) 테스트."""
import numpy as np
import pandas as pd

from pipeline import trade


def tx(price=100.0, sup=((95.0, "20일선"), (90.0, "스윙 저점")), res=((112.0, "스윙 고점"), (125.0, "52주 고점")),
       stop=93.0, rsi=50.0, disp=102.0, setup="pullback"):
    return {"setup": setup,
            "levels": {"support": [{"price": p, "label": l, "dist": round((p / price - 1) * 100, 1)} for p, l in sup],
                       "resistance": [{"price": p, "label": l, "dist": round((p / price - 1) * 100, 1)} for p, l in res],
                       "stop": {"price": stop}},
            "values": {"rsi": rsi, "disparity": disp}}


def bars(rows, start="2026-01-05"):
    """rows = [(open, high, low, close), ...]"""
    idx = pd.bdate_range(start, periods=len(rows))
    return pd.DataFrame(rows, index=idx, columns=["Open", "High", "Low", "Close"], dtype=float)


def test_plan_buy_now_when_rr_ok():
    pl = trade.plan(tx(), 100.0, "daily")
    assert pl["action"] == "buy" and pl["target"] == 112.0 and abs(pl["rr"] - 12 / 7) < 0.01
    # 이번 주는 2차 저항이 목표
    assert trade.plan(tx(), 100.0, "weekly")["target"] == 125.0


def test_plan_wait_when_extended_or_low_rr():
    # 1차 저항이 가까워 지금 값 손익비 4/7 < 1.5 → 20일선 95에서 손익비 (104-95)/(95-93)=4.5 → 지정가 대기
    pl = trade.plan(tx(res=((104.0, "20일선"),)), 100.0, "daily")
    assert pl["action"] == "wait" and pl["limit"] == 95.0 and pl["wait_days"] == 3
    # 과열(RSI 72)이면 손익비가 좋아도 기다림
    assert trade.plan(tx(rsi=72.0), 100.0, "daily")["action"] == "wait"


def test_plan_skip_cases():
    assert trade.plan(tx(), 100.0, "daily", relaxed=True)["action"] == "skip"
    assert trade.plan(tx(), 100.0, "daily", stage40=4)["action"] == "skip"
    assert trade.plan(tx(setup="downtrend"), 100.0, "daily")["action"] == "skip"
    assert trade.plan(tx(stop=None), 100.0, "daily")["action"] == "skip"
    # 저항 가깝고 지지선이 손절가 아래뿐 → 보류
    pl = trade.plan(tx(res=((103.0, "20일선"),), sup=((92.0, "스윙 저점"),)), 100.0, "daily")
    assert pl["action"] == "skip" and "자신 없음" in pl["reason"]
    assert trade.plan(None, 100.0, "daily")["action"] == "skip"


def test_simulate_target_stop_time_and_gap():
    pl = trade.plan(tx(), 100.0, "daily")  # 진입 다음 날 시가, 손절 93, 목표 112, 5거래일
    # 3일째 고가가 목표가 도달
    df = bars([(100, 101, 99, 100), (101, 105, 100, 104), (104, 113, 103, 111), (111, 111, 108, 109)])
    r = trade.simulate(pl, df, 0, "us")
    assert r["status"] == "closed" and r["why"] == "목표가" and r["exit"] == 112.0 and r["days"] == 3
    assert abs(r["ret"] - (12 - 0.05)) < 1e-6 and r["win"]
    # 같은 날 손절·목표가 모두 닿으면 손절(보수적)
    df = bars([(100, 113, 92, 100)])
    assert trade.simulate(pl, df, 0, "us")["why"] == "손절"
    # 갭 하락이면 시가에 손절
    df = bars([(100, 101, 99, 100), (90, 91, 88, 89)])
    r = trade.simulate(pl, df, 0, "kr")
    assert r["why"] == "손절(갭 하락)" and r["exit"] == 90.0 and abs(r["ret"] - (-10 - 0.25)) < 1e-6
    # 5거래일 동안 아무것도 안 닿으면 5일째 종가
    df = bars([(100, 102, 98, 101)] * 4 + [(101, 103, 99, 102)] + [(102, 120, 101, 119)])
    r = trade.simulate(pl, df, 0, "us")
    assert r["why"] == "보유 기간 만료" and r["exit"] == 102.0 and r["days"] == 5
    # 아직 기간이 안 끝났으면 진행 중
    assert trade.simulate(pl, bars([(100, 102, 98, 101)] * 2), 0, "us")["status"] == "open"


def test_simulate_wait_fill_unfilled_and_fill_day_stop():
    pl = trade.plan(tx(res=((104.0, "20일선"),)), 100.0, "daily")  # 지정가 95, 3거래일, 손절 93, 목표 104
    # 3일 안에 95까지 안 내려오면 미체결
    assert trade.simulate(pl, bars([(100, 101, 96, 100)] * 3), 0, "us")["status"] == "unfilled"
    assert trade.simulate(pl, bars([(100, 101, 96, 100)] * 2), 0, "us")["status"] == "waiting"
    # 2일째 체결(시가 98 → 지정가 95), 3일째 목표가
    df = bars([(100, 101, 96, 100), (98, 99, 94.5, 96), (97, 105, 96, 103)])
    r = trade.simulate(pl, df, 0, "us")
    assert r["fill"] == 95.0 and r["fill_date"] == df.index[1].strftime("%Y-%m-%d") and r["why"] == "목표가"
    # 갭 하락으로 지정가보다 낮은 시가에 체결
    assert trade.simulate(pl, bars([(94, 96, 93.5, 95), (95, 105, 95, 104)]), 0, "us")["fill"] == 94.0
    # 체결 당일 손절가까지 밀렸으면 손절로 봄
    r = trade.simulate(pl, bars([(97, 98, 92, 96)]), 0, "us")
    assert r["why"] == "손절" and r["exit"] == 93.0
    # 체결 당일 목표가 도달은 인정하지 않음(다음 날부터)
    r = trade.simulate(pl, bars([(97, 106, 94, 105), (105, 105, 104.5, 104.8)] + [(104, 104, 103, 103.5)] * 3), 0, "us")
    assert r["why"] == "목표가(갭 상승)" and r["exit"] == 105.0


def test_skip_not_scored_and_summary():
    items = []
    for k, (act_tx, rel) in enumerate([(tx(), False), (tx(), True)]):
        pl = trade.plan(act_tx, 100.0, "daily", relaxed=rel)
        df = bars([(100, 113, 99, 111)])
        items.append({"horizon": "daily", "plan": pl, "trade": trade.simulate(pl, df, 0, "us"),
                      "final": {"win": False, "ex": -1.0}})
    s = trade.summarize(items)["daily"]
    assert s["plans"] == 2 and s["skip"] == 1 and s["closed"] == 1 and s["win_rate"] == 100.0
    assert s["all_picks"]["n"] == 2 and s["all_picks"]["win_rate"] == 0.0


def test_entry_pos_rules():
    df = bars([(1, 1, 1, 10), (1, 1, 1, 11), (1, 1, 1, 12)], start="2026-03-02")
    assert trade.entry_pos(df, "2026-03-02", "2026-03-03") == 1
    # asof가 없으면 추천일 이전에서 추천가와 같은 종가 다음 봉
    assert trade.entry_pos(df, None, "2026-03-04", 11.0) == 2
    assert trade.entry_pos(df, None, "2026-03-03", None) == 1
    assert trade.entry_pos(df, "2026-03-04", "2026-03-04") is None


def test_backtest_has_trade_rules():
    from pipeline import backtest
    rng = np.random.default_rng(3)
    hist = {}
    for k in range(6):
        n = 520
        c = 100 * np.exp(np.cumsum(rng.normal(0.0004, 0.015, n)))
        o = c * (1 + rng.normal(0, 0.004, n))
        h = np.maximum(o, c) * (1 + np.abs(rng.normal(0, 0.006, n)))
        lo = np.minimum(o, c) * (1 - np.abs(rng.normal(0, 0.006, n)))
        hist[f"T{k}"] = pd.DataFrame({"Open": o, "High": h, "Low": lo, "Close": c, "Volume": 1e6},
                                     index=pd.bdate_range("2023-01-02", periods=n))
    res = backtest.run(hist, workers=1)
    tr = res["trade_rules"]
    assert set(tr) == {"daily", "weekly", "monthly"}
    d = tr["daily"]
    assert abs(sum(d["share"].values()) - 100) < 0.5 and d["samples"] > 0


def test_score_items_trims_unfinished_session_and_mismatch():
    from datetime import datetime
    from zoneinfo import ZoneInfo
    df = bars([(100, 101, 99, 100), (100, 101, 99, 100.5), (100, 113, 99, 111)], start="2026-03-02")  # 3/2~3/4
    item = {"date": "2026-03-03", "horizon": "daily", "ticker": "005930.KS", "entry": 100.0,
            "plan": trade.plan(tx(), 100.0, "daily", asof="2026-03-02")}
    mk = lambda t: "kr" if t.endswith(".KS") else "us"  # noqa: E731
    # 3/4 11:00 KST 장중 실행 → 3/4 봉(목표가 도달)은 아직 쓰지 않음
    trade.score_items([item], {"005930.KS": df}, datetime(2026, 3, 4, 11, 0, tzinfo=ZoneInfo("Asia/Seoul")), mk)
    assert item["trade"]["status"] == "open"
    trade.score_items([item], {"005930.KS": df}, datetime(2026, 3, 4, 16, 0, tzinfo=ZoneInfo("Asia/Seoul")), mk)
    assert item["trade"]["status"] == "closed" and item["trade"]["why"] == "목표가"
    # 추천가와 가격 이력이 20% 넘게 어긋나면 채점하지 않음
    bad = {**item, "entry": 50.0, "trade": None}
    trade.score_items([bad], {"005930.KS": df}, datetime(2026, 3, 5, 16, 0, tzinfo=ZoneInfo("Asia/Seoul")), mk)
    assert bad["trade"]["status"] == "data_mismatch"
