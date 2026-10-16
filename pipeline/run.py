"""파이프라인 진입점.

사용법
    python -m pipeline.run morning            # 장전: 브리핑 + 스크리닝 + 오늘의 Top3 (+월요일 주간, +매월 첫 평일 월간·배당플랜) + 알림
    python -m pipeline.run alerts             # 장중: 관심종목 신호만 점검·푸시
    python -m pipeline.run all                # 전체 강제 실행 (주간·월간·배당 포함)
    python -m pipeline.run backtest           # 주간: 5년 백테스트 → 가중치 자가 보정(model.json)
    python -m pipeline.run all --demo         # 합성 데이터로 화면·로직 검증 (네트워크 불필요)
"""
from __future__ import annotations

import argparse
import logging
import re

import pandas as pd

from . import alerts as alert_mod
from . import backtest as bt_mod
from . import briefing as briefing_mod
from . import dividend as div_mod
from . import forward as fwd
from .common import DATA_DIR, STATE_DIR, load_config, market_of, now_kst, read_json, write_json
from .calibrate import calibrate
from .data import get_source
from .indicators import technicals
from .technical import chart_series
from .valuation import HORIZON_LABEL, diagnose_drop, pick_top, score_universe

log = logging.getLogger("pipeline")
DISCLAIMER = "정보 제공용이며 투자 권유가 아닙니다. 무료 데이터(Yahoo Finance) 특성상 지연·결측이 있을 수 있으니 공시·증권사 자료로 교차 확인하세요."


def chart_file(ticker: str) -> str:
    return re.sub(r"[^A-Za-z0-9.\-]", "_", ticker) + ".json"


def _is_first_weekday_of_month(d) -> bool:
    first = d.replace(day=1)
    while first.weekday() >= 5:
        first = first.replace(day=first.day + 1)
    return d.day == first.day


def _rows(src, tickers, hist, names=None, with_fund=True, tcfg=None):
    names = names or {}
    rows = []
    for t in tickers:
        h = hist.get(t)
        if h is None or len(h) < 30:
            log.warning("이력 부족 → 제외: %s", t)
            continue
        fund = src.fundamentals(t) if with_fund else {}
        rows.append({"ticker": t, "market": market_of(t), "name": names.get(t) or fund.get("name") or t,
                     "fund": fund, "tech": technicals(h, tcfg)})
    return rows


def _watch_rows(src, cfg, hist, scored: pd.DataFrame | None):
    names = {w["ticker"]: w["name"] for w in cfg["watchlist"]}
    rows = _rows(src, list(names), hist, names, tcfg=cfg.get("technical"))
    prev = read_json("fundamentals_prev.json", STATE_DIR, default={}) or {}
    sc = scored.set_index("ticker") if scored is not None and not scored.empty else None
    for r in rows:
        s = sc.loc[r["ticker"]].to_dict() if sc is not None and r["ticker"] in sc.index else {}
        r["verdict"] = s.get("verdict")
        r["score"] = s.get("score_weekly")
        t = r["tech"]
        if (t.get("chg_1d") or 0) <= -4 or (t.get("chg_5d") or 0) <= -8:
            r["diag"] = diagnose_drop(r["fund"], prev.get(r["ticker"]), s)
    return rows


BENCH = {"kr": "^KS11", "us": "^GSPC"}


def _update_track_record(picks_all: dict, prices: dict):
    """추천 이력 저장 + 추천 이후 수익률·지수 대비 초과수익 갱신."""
    hist = read_json("track.json", default={"items": []}) or {"items": []}
    today = now_kst().strftime("%Y-%m-%d")
    have = {(i["date"], i["horizon"], i["ticker"]) for i in hist["items"]}
    for h, block in picks_all.items():
        if block.get("generated", "")[:10] != today:
            continue
        for p in block["all"]:
            key = (today, h, p["ticker"])
            if key not in have and p.get("price"):
                b = BENCH[market_of(p["ticker"])]
                hist["items"].append({"date": today, "horizon": h, "ticker": p["ticker"], "name": p["name"],
                                      "entry": p["price"], "score": p["score"], "setup": p.get("setup_label"),
                                      "bench": b, "bench_entry": prices.get(b)})
    for i in hist["items"]:
        px = prices.get(i["ticker"])
        if px:
            i["last"] = px
            i["ret"] = (px / i["entry"] - 1) * 100
            bp = prices.get(i.get("bench"))
            if bp and i.get("bench_entry"):
                i["ex_ret"] = i["ret"] - (bp / i["bench_entry"] - 1) * 100
    hist["items"] = hist["items"][-300:]
    rets = [i["ret"] for i in hist["items"] if i.get("ret") is not None]
    exs = [i["ex_ret"] for i in hist["items"] if i.get("ex_ret") is not None]
    hist["summary"] = {"count": len(rets), "avg_ret": sum(rets) / len(rets) if rets else None,
                       "hit_rate": sum(r > 0 for r in rets) / len(rets) * 100 if rets else None,
                       "avg_ex": sum(exs) / len(exs) if exs else None}
    by = {}
    for i in hist["items"]:
        if i.get("ret") is not None and i.get("setup"):
            by.setdefault(i["setup"], []).append(i)
    hist["by_setup"] = {}
    for k, v in sorted(by.items(), key=lambda kv: -len(kv[1])):
        r = [i["ret"] for i in v]
        x = [i["ex_ret"] for i in v if i.get("ex_ret") is not None]
        hist["by_setup"][k] = {"count": len(r), "avg_ret": sum(r) / len(r), "hit_rate": sum(y > 0 for y in r) / len(r) * 100,
                               "avg_ex": sum(x) / len(x) if x else None}
    write_json("track.json", hist)


def run(job: str, demo: bool):
    cfg = load_config()
    src = get_source(demo)
    now = now_kst()
    meta = read_json("meta.json", default={}) or {}
    meta.update({"demo": demo, "disclaimer": DISCLAIMER})
    watch = [w["ticker"] for w in cfg["watchlist"]]

    if job == "alerts":
        hist = src.history(watch, period="2y")  # 250일선 기울기 계산에 2년 필요
        rows = _watch_rows(src, cfg, hist, None)
        prev_screen = read_json("screen.json", default={}) or {}
        vmap = {r["ticker"]: r.get("verdict") for r in prev_screen.get("rows", [])}
        for r in rows:
            r["verdict"] = vmap.get(r["ticker"])
        fresh = alert_mod.dispatch(alert_mod.evaluate(cfg, rows))
        meta["alerts"] = now.isoformat(timespec="minutes")
        write_json("meta.json", meta)
        src.flush()
        log.info("알림 %d건", len(fresh))
        return

    if job == "backtest" or (job == "all" and not read_json("backtest.json")):
        run_backtest(cfg, src, meta)
        if job == "backtest":
            write_json("meta.json", meta)
            src.flush()
            return

    # ── morning / all ──
    model = read_json("model.json")
    kr_names = cfg["universe"]["kr"] if isinstance(cfg["universe"]["kr"], dict) else {}
    uni = cfg["universe"]["us"] + list(cfg["universe"]["kr"])
    names = {**kr_names, **{d["ticker"]: d["name"] for d in cfg["dividend_candidates"]},
             **{w["ticker"]: w["name"] for w in cfg["watchlist"]}}
    div_c = [d["ticker"] for d in cfg["dividend_candidates"]]
    b = cfg["briefing"]
    extra = [i["ticker"] for i in b["indices"]] + [m["ticker"] for m in b["macro"]]
    hist = src.history(list(dict.fromkeys(uni + watch + div_c + extra)), period="2y")
    tcfg = cfg.get("technical")
    tech_all = {t: technicals(h, tcfg, with_ta=False) for t, h in hist.items() if len(h) >= 30}

    # 1) 스크리닝 (유니버스 + 관심종목)
    uni_rows = _rows(src, list(dict.fromkeys(uni + watch)), hist, names, tcfg=tcfg)
    scored = score_universe(uni_rows, model)
    # 오늘의 예측을 기록 → 나중에 실제 수익률로 채점해 보정 근거로 사용
    fwd.save_snapshot(scored, now.strftime("%Y-%m-%d"), {r["ticker"]: r["tech"].get("date") for r in uni_rows})
    keep = ["ticker", "name", "market", "sector", "price", "fpe", "peg", "pb", "roe", "opm", "de", "eg",
            "upside", "analysts", "rec_key", "rsi", "above_long", "dd52", "chg_1d", "chg_5d", "chg_20d", "chg_60d",
            "value", "quality", "target", "growth", "momentum", "trend", "timing", "tech",
            "setup", "setup_label", "trend_label", "tags",
            "score_daily", "score_weekly", "score_monthly", "verdict", "sector_fpe_median", "spark"]
    screen_rows = []
    for rec in (scored.to_dict("records") if not scored.empty else []):
        row = {k: rec.get(k) for k in keep}
        ta = rec.get("_ta") or {}
        row["sig"] = [[s["pol"], s["cat"], s["text"]] for s in ta.get("signals", [])]  # 상세 시트용
        row["levels"] = ta.get("levels")
        row["setup_desc"] = ta.get("setup_desc")
        row["tvals"] = ta.get("values")
        row["tscores"] = ta.get("scores")
        screen_rows.append(row)

    # 2) 관심종목 + 진단 (screen.json은 차트 파일 생성 후 기록)
    watch_rows = _watch_rows(src, cfg, hist, scored)
    write_json("watch.json", {"updated": now.isoformat(timespec="minutes"), "rows": [
        {k: r.get(k) for k in ("ticker", "name", "market", "verdict", "score", "diag")} |
        {"tech": {k: v for k, v in r["tech"].items()}, "fund": {k: r["fund"].get(k) for k in (
            "forward_pe", "peg", "roe", "target_mean", "analyst_count", "rec_key", "currency")}}
        for r in watch_rows]})

    # 3) 브리핑
    news = []
    for t in b.get("news_tickers", []):
        news += src.news(t)
    seen, uniq = set(), []
    for n in news:
        if n["title"] not in seen:
            seen.add(n["title"])
            uniq.append(n)
    brief = briefing_mod.build(cfg, tech_all, uni_rows, watch_rows, uniq)
    brief["date"] = now.strftime("%Y-%m-%d")
    brief["generated"] = now.isoformat(timespec="minutes")
    write_json("briefing.json", brief)
    archive_dir = DATA_DIR / "archive"
    write_json(f"briefing_{brief['date']}.json", brief, archive_dir)

    # 4) 추천 Top3
    picks = read_json("picks.json", default={}) or {}
    horizons = ["daily"]
    if job == "all" or now.weekday() == 0 or "weekly" not in picks:
        horizons.append("weekly")
    if job == "all" or _is_first_weekday_of_month(now.date()) or "monthly" not in picks:
        horizons.append("monthly")
    for h in horizons:
        picks[h] = {"label": HORIZON_LABEL[h], "generated": now.isoformat(timespec="minutes"),
                    "all": pick_top(scored, h), "us": pick_top(scored, h, "us"), "kr": pick_top(scored, h, "kr")}
    write_json("picks.json", picks)

    # 4-1) 상세 차트 파일 (관심종목 + 현재 추천 종목) — 오래된 파일은 삭제해 저장소 비대화 방지
    chart_dir = DATA_DIR / "charts"
    chart_set = set(watch)
    for blk in picks.values():
        for m in ("all", "us", "kr"):
            chart_set |= {p["ticker"] for p in blk.get(m, [])}
    ta_map = {r["ticker"]: r["tech"].get("ta") for r in uni_rows + watch_rows}
    written = set()
    for t in sorted(chart_set):
        h = hist.get(t)
        if h is None or len(h) < 60:
            continue
        fname = chart_file(t)
        write_json(fname, {"ticker": t, "name": names.get(t, t), "ta": ta_map.get(t), **chart_series(h, tcfg)},
                   chart_dir)
        written.add(fname)
    for f in chart_dir.glob("*.json"):
        if f.name not in written:
            f.unlink()
    for row in screen_rows:
        row["chart"] = chart_file(row["ticker"]) in written
    write_json("screen.json", {"updated": now.isoformat(timespec="minutes"), "rows": screen_rows})

    _update_track_record(picks, {t: v["last"] for t, v in tech_all.items()})

    # 5) 배당 플랜 (월간)
    if "monthly" in horizons or not read_json("dividend.json"):
        usdkrw = (tech_all.get("KRW=X") or {}).get("last") or 1400.0
        cands = []
        for d in cfg["dividend_candidates"]:
            t = d["ticker"]
            price = (tech_all.get(t) or {}).get("last")
            fund = src.fundamentals(t)
            cands.append(div_mod.analyze(t, d, src.dividends(t), price, fund, cfg["dividend_plan"]))
        plan = div_mod.build_plan(cands, cfg["dividend_plan"], usdkrw)
        plan["generated"] = now.isoformat(timespec="minutes")
        plan["month"] = now.strftime("%Y-%m")
        write_json("dividend.json", plan)
        meta["dividend"] = plan["generated"]

    # 6) 알림 (판정 변경 포함) + 브리핑 도착 푸시
    head = " / ".join(f"{q['name']} {q['chg']:+.2f}%" for q in brief["indices"][:3] if q.get("chg") is not None)
    alert_mod.dispatch(alert_mod.evaluate(cfg, watch_rows) + alert_mod.verdict_changes(watch_rows),
                       extra_push={"title": f"☀️ 장전 시황 {brief['date']}", "body": head or "브리핑 업데이트",
                                   "url": "./#brief"})

    meta.update({"morning": now.isoformat(timespec="minutes"), "horizons_run": horizons,
                 "universe_size": len(uni_rows), "model_version": (model or {}).get("version")})
    write_json("meta.json", meta)
    src.flush()
    log.info("완료: 유니버스 %d종목, 실행 %s", len(uni_rows), horizons)


def run_backtest(cfg: dict, src, meta: dict):
    """5년 일봉 백테스트 → backtest.json, 자가 보정 → model.json."""
    now = now_kst()
    uni = cfg["universe"]["us"] + list(cfg["universe"]["kr"])
    watch = [w["ticker"] for w in cfg["watchlist"]]
    period = (cfg.get("backtest") or {}).get("period", "10y")
    hist = src.history(list(dict.fromkeys(uni + watch)), period=period)
    log.info("백테스트 시작: %d종목, %s", len(hist), period)
    res = bt_mod.run(hist, cfg.get("technical"))
    res["generated"] = now.isoformat(timespec="minutes")
    write_json("backtest.json", res)

    # 실전 예측 채점 (매일 저장한 예측 vs 실제 수익률)
    live = fwd.attach_returns(fwd.load_snapshots(), hist)
    ev = fwd.evaluate(live)
    if ev.get("ok"):
        ev["recent"] = {}
        for name, (col, h) in fwd.SCORES.items():
            ser = fwd.date_ics(live, live[f"s_{name}"], h).tail(60)
            st = fwd.ic_stat(ser, h)
            if st:
                ev["recent"][name] = st
    ev["generated"] = res["generated"]
    write_json("forward.json", ev)

    prev = read_json("model.json")
    model = calibrate(res, read_json("track.json"), prev, live if ev.get("ok") else None)
    write_json("model.json", model)
    fwd.update_scorecard(ev, model["version"], now.strftime("%Y-%m-%d"))
    meta["backtest"] = res["generated"]
    log.info("백테스트 완료: 표본 %s, 실전 기록 %s일, 모델 v%s (변경 %s)", res.get("samples"), ev.get("days", 0),
             model["version"], model["changed"])
    if model["changed"] and prev:
        alert_mod.push({"title": f"🔧 모델 자가 보정 v{model['version']}",
                        "body": "\n".join(c["text"] for c in model["log"][:3]), "url": "./#lab"})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("job", choices=["morning", "alerts", "all", "backtest"])
    ap.add_argument("--demo", action="store_true", help="합성 데이터 사용")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    run(a.job, a.demo)


if __name__ == "__main__":
    main()
