"""파이프라인 진입점.

사용법
    python -m pipeline.run morning            # 국장 장전: 브리핑 + 스크리닝 + 오늘의 Top3 (+월요일 주간, +매월 첫 평일 월간) + 배당 + 알림
    python -m pipeline.run evening            # 미장 장전: 국내장 마감 리뷰 + 미 지수 선물·매크로 + 오늘 밤 실적 발표 + 정세
    python -m pipeline.run alerts             # 장중: 관심종목 신호만 점검·푸시
    python -m pipeline.run all                # 전체 강제 실행 (주간·월간·배당 포함)
    python -m pipeline.run backtest           # 주간: 5년 백테스트 → 가중치 자가 보정(model.json)
    python -m pipeline.run all --demo         # 합성 데이터로 화면·로직 검증 (네트워크 불필요)
"""
from __future__ import annotations

import argparse
import logging
import re

import numpy as np

import pandas as pd

from . import alerts as alert_mod
from . import backtest as bt_mod
from . import briefing as briefing_mod
from . import dividend as div_mod
from . import company as co_mod
from . import financials as fin_mod
from . import forward as fwd
from . import geo as geo_mod
from . import pdca as pdca_mod
from datetime import timezone

from .common import DATA_DIR, STATE_DIR, load_config, market_of, now_kst, read_json, write_json
from .calibrate import calibrate
from .data import get_source
from .indicators import technicals
from .technical import chart_series
from .valuation import HORIZON_LABEL, diagnose_drop, pick_top, score_universe

log = logging.getLogger("pipeline")
DISCLAIMER = ("정보 제공용이며 투자 권유가 아닙니다. 무료 데이터(Yahoo Finance·Google 뉴스) 특성상 지연·결측이 있을 수 있으니 "
              "네이버 증권·DART 공시·증권사 자료로 교차 확인하세요.")


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
HOLD = {"daily": 5, "weekly": 20, "monthly": 60}  # 추천별 채점 기간(거래일)


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
        # 채점 기간에 도달하면 결과 확정(예측 vs 실제): 지수 대비 초과수익 > 0 이면 적중
        hold = HOLD.get(i.get("horizon"), 20)
        elapsed = int(np.busday_count(i["date"], today))
        if "final" not in i and elapsed >= hold and i.get("ret") is not None:
            ex = i.get("ex_ret")
            i["final"] = {"ret": round(i["ret"], 2), "ex": None if ex is None else round(ex, 2), "days": elapsed,
                          "win": bool((ex if ex is not None else i["ret"]) > 0), "closed": today}
    hist["items"] = hist["items"][-600:]
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
        x = [(i.get("final") or {}).get("ex", i.get("ex_ret")) for i in v]
        x = [e for e in x if e is not None]
        hist["by_setup"][k] = {"count": len(r), "avg_ret": sum(r) / len(r), "hit_rate": sum(y > 0 for y in r) / len(r) * 100,
                               "avg_ex": sum(x) / len(x) if x else None}
    hist["matured"] = {}
    for h in HOLD:
        fin = [i["final"] for i in hist["items"] if i.get("horizon") == h and i.get("final")]
        if fin:
            ex = [f["ex"] for f in fin if f["ex"] is not None]
            last = fin[-20:]
            hist["matured"][h] = {"count": len(fin), "win_rate": sum(f["win"] for f in fin) / len(fin) * 100,
                                  "avg_ex": sum(ex) / len(ex) if ex else None,
                                  "recent_win": sum(f["win"] for f in last) / len(last) * 100, "recent_n": len(last)}
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

    if job == "evening":
        run_evening(cfg, src, meta)
        write_json("meta.json", meta)
        src.flush()
        return

    if job == "backtest" or (job == "all" and not read_json("backtest.json")):
        run_backtest(cfg, src, meta)
        if job == "backtest":
            write_json("meta.json", meta)
            src.flush()
            return

    # ── morning / all ──
    model = read_json("model.json")
    companies = co_mod.load_companies()
    kr_names = cfg["universe"]["kr"] if isinstance(cfg["universe"]["kr"], dict) else {}
    uni = cfg["universe"]["us"] + list(cfg["universe"]["kr"])
    names = {**{d["ticker"]: d["name"] for d in cfg["dividend_candidates"]}, **kr_names,
             **{t: c["ko"] for t, c in companies.items() if c.get("ko")},
             **{w["ticker"]: w["name"] for w in cfg["watchlist"]}}
    today = now.strftime("%Y-%m-%d")
    div_c = [d["ticker"] for d in cfg["dividend_candidates"]]
    b = cfg["briefing"]
    extra = [i["ticker"] for i in b["indices"]] + [m["ticker"] for m in b["macro"]]
    hist = src.history(list(dict.fromkeys(uni + watch + div_c + extra)), period="2y")
    tcfg = cfg.get("technical")
    tech_all = {t: technicals(h, tcfg, with_ta=False) for t, h in hist.items() if len(h) >= 30}

    # 0) 국제 정세(테마별 뉴스 관심도·어조·시장 반응) + 네이버 코드
    try:
        geo_res = geo_mod.collect(src, tech_all, today)
    except Exception as e:  # 뉴스 실패가 장전 파이프라인 전체를 막지 않도록
        log.warning("국제 정세 수집 실패: %s", e)
        geo_res = {"date": today, "themes": [], "impact": {}}
    naver = co_mod.resolve_naver(uni + watch + div_c) if not demo else {}
    fins = fin_mod.Financials(src)

    # 1) 스크리닝 (유니버스 + 관심종목)
    uni_rows = _rows(src, list(dict.fromkeys(uni + watch)), hist, names, tcfg=tcfg)
    eps_hist = fin_mod.update_eps_hist({r["ticker"]: r["fund"].get("forward_eps") for r in uni_rows}, today)
    for r in uni_rows:  # EPS 리비전(추정치 30일 변화) + 정세 노출 점수 → 기록용 팩터
        rv = fin_mod.trend_revision(fins.trend(r["ticker"]))
        r["revision"] = rv if rv is not None else fin_mod.hist_revision(eps_hist.get(r["ticker"]), today)
        r["geo"], r["geo_notes"] = geo_mod.ticker_geo(r["ticker"], companies, geo_res)
    scored = score_universe(uni_rows, model)
    # 오늘의 예측을 기록 → 나중에 실제 수익률로 채점해 보정 근거로 사용
    fwd.save_snapshot(scored, now.strftime("%Y-%m-%d"), {r["ticker"]: r["tech"].get("date") for r in uni_rows})
    keep = ["ticker", "name", "market", "sector", "price", "fpe", "peg", "pb", "roe", "opm", "de", "eg",
            "upside", "analysts", "rec_key", "rsi", "above_long", "dd52", "chg_1d", "chg_5d", "chg_20d", "chg_60d",
            "value", "quality", "target", "growth", "momentum", "trend", "timing", "tech",
            "setup", "setup_label", "trend_label", "tags", "rev", "revision", "geo",
            "score_daily", "score_weekly", "score_monthly", "verdict", "sector_fpe_median", "spark"]
    geo_notes = {r["ticker"]: r.get("geo_notes") or [] for r in uni_rows}
    geo_score = {r["ticker"]: r.get("geo") for r in uni_rows}
    screen_rows = []
    for rec in (scored.to_dict("records") if not scored.empty else []):
        row = {k: rec.get(k) for k in keep}
        c = companies.get(rec["ticker"]) or {}
        row["desc"] = c.get("desc")
        row["ctags"] = [co_mod.TAG_KO.get(x, x) for x in (companies.get(rec["ticker"]) or {}).get("tags") or []]
        row["links"] = co_mod.links(rec["ticker"], names.get(rec["ticker"]), naver.get(rec["ticker"]))
        row["geo_notes"] = geo_notes.get(rec["ticker"])
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
    if not news:  # yfinance 헤드라인이 비면 RSS로 대체 (헤드라인 뉴스 빈 화면 방지)
        for q in b.get("news_fallback_queries", ["stock market today", "코스피 증시"]):
            hl, gl = ("ko", "KR") if any("\uac00" <= ch <= "\ud7a3" for ch in q) else ("en-US", "US")
            news += src.market_news(q, hl=hl, gl=gl)
        log.info("대체 뉴스(RSS) %d건", len(news))
    seen, uniq = set(), []
    for n in news:
        if n["title"] not in seen:
            seen.add(n["title"])
            uniq.append(n)
    brief = briefing_mod.build(cfg, tech_all, uni_rows, watch_rows, uniq)
    brief["news_kr"] = src.market_news("코스피 OR 국내 증시 when:1d", hl="ko", gl="KR", limit=8)
    now_utc = now.astimezone(timezone.utc)
    brief["earnings"] = (briefing_mod.earnings_soon([r for r in uni_rows if r["market"] == "kr"], now_utc, 2, 9)
                         + briefing_mod.earnings_soon([r for r in uni_rows if r["market"] == "us"], now_utc, 1,
                                                      briefing_mod.us_eastern_offset(now_utc)))
    brief["geo"] = geo_mod.summary_lines(geo_res)
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
    for blk in picks.values():  # 정세 영향·한국어 링크는 매일 최신으로 덧붙임
        for m in ("all", "us", "kr"):
            for p in blk.get(m, []):
                p["geo_notes"] = geo_notes.get(p["ticker"]) or []
                p["links"] = co_mod.links(p["ticker"], names.get(p["ticker"]), naver.get(p["ticker"]))
                p["name"] = names.get(p["ticker"], p["name"])
    write_json("picks.json", picks)

    # 4-1) 상세 차트 파일 (관심종목 + 현재 추천 종목) — 오래된 파일은 삭제해 저장소 비대화 방지
    chart_dir = DATA_DIR / "charts"
    chart_set = set(watch)
    for blk in picks.values():
        for m in ("all", "us", "kr"):
            chart_set |= {p["ticker"] for p in blk.get(m, [])}
    ta_map = {r["ticker"]: r["tech"].get("ta") for r in uni_rows + watch_rows}
    fund_map = {r["ticker"]: r["fund"] for r in uni_rows + watch_rows}
    written = set()
    for t in sorted(chart_set):
        h = hist.get(t)
        if h is None or len(h) < 60:
            continue
        fname = chart_file(t)
        fund = fund_map.get(t) or src.fundamentals(t)
        fin = fins.get(t, fund)
        write_json(fname, {"ticker": t, "name": names.get(t, t), "ta": ta_map.get(t),
                           "co": co_mod.profile(t, companies, fund, naver.get(t)),
                           "fin": {**fin_mod.compact(fin), "currency": fund.get("currency"),
                                   "analysis": fin_mod.analyze(fin, fund)},
                           "geo": {"score": geo_score.get(t),
                                   "notes": geo_notes.get(t) or []},
                           **chart_series(h, tcfg)}, chart_dir)
        written.add(fname)
    for f in chart_dir.glob("*.json"):
        if f.name not in written:
            f.unlink()
    for row in screen_rows:
        row["chart"] = chart_file(row["ticker"]) in written
    write_json("screen.json", {"updated": now.isoformat(timespec="minutes"), "rows": screen_rows})

    write_json("geo.json", {**geo_res, "generated": now.isoformat(timespec="minutes"),
                            "summary": brief["geo"], "tag_ko": co_mod.TAG_KO})
    _update_track_record(picks, {t: v["last"] for t, v in tech_all.items()})

    # 5) 배당 플랜 (매일 최신 가격으로 갱신 — 배당 이력은 주 1회 캐시) · 앱에서 구성 변경 가능
    if True:
        usdkrw = (tech_all.get("KRW=X") or {}).get("last") or 1400.0
        cands = []
        pool = [dict(d, pool="배당 후보") for d in cfg["dividend_candidates"]]
        have = {d["ticker"] for d in pool}
        pool += [{"ticker": t, "name": names.get(t, t), "type": "stock", "pool": "유니버스"}
                 for t in uni if t not in have]
        for d in pool:
            t = d["ticker"]
            d = {**d, "name": names.get(t) or d.get("name") or t}
            price = (tech_all.get(t) or {}).get("last")
            fund = src.fundamentals(t)
            c = div_mod.analyze(t, d, src.dividends(t), price, fund, cfg["dividend_plan"])
            if c and (d["pool"] == "배당 후보" or c["yield"] >= 1.0):  # 유니버스는 배당률 1% 이상만
                cands.append(c)
        plan = div_mod.build_plan(cands, cfg["dividend_plan"], usdkrw)
        plan["generated"] = now.isoformat(timespec="minutes")
        plan["month"] = now.strftime("%Y-%m")
        write_json("dividend.json", plan)
        meta["dividend"] = plan["generated"]

    # 6) 알림 (판정 변경 포함) + 브리핑 도착 푸시
    head = " / ".join(f"{q['name']} {q['chg']:+.2f}%" for q in brief["indices"][:3] if q.get("chg") is not None)
    alert_mod.dispatch(alert_mod.evaluate(cfg, watch_rows) + alert_mod.verdict_changes(watch_rows),
                       extra_push={"title": f"☀️ 국장 장전 {brief['date']}", "body": head or "브리핑 업데이트",
                                   "url": "./#brief"})

    if job == "all" or not read_json("briefing_us.json"):  # 코드 변경 시 미장 브리핑도 함께 갱신(푸시 없음)
        try:
            run_evening(cfg, src, meta, push=False)
        except Exception as e:
            log.warning("미장 브리핑 생성 실패: %s", e)
    meta.update({"morning": now.isoformat(timespec="minutes"), "horizons_run": horizons,
                 "universe_size": len(uni_rows), "model_version": (model or {}).get("version")})
    write_json("meta.json", meta)
    src.flush()
    fins.flush()
    log.info("완료: 유니버스 %d종목, 실행 %s", len(uni_rows), horizons)


def run_evening(cfg: dict, src, meta: dict, push: bool = True):
    """미장 장전(평일 저녁): 예측 기록·추천은 하지 않고(하루 1회 원칙) 브리핑만 생성."""
    now = now_kst()
    companies = co_mod.load_companies()
    b, bu = cfg["briefing"], cfg.get("briefing_us") or {}
    kr_uni = list(cfg["universe"]["kr"])
    us_uni = cfg["universe"]["us"]
    watch = [w["ticker"] for w in cfg["watchlist"]]
    names = {**(cfg["universe"]["kr"] if isinstance(cfg["universe"]["kr"], dict) else {}),
             **{t: c["ko"] for t, c in companies.items() if c.get("ko")},
             **{w["ticker"]: w["name"] for w in cfg["watchlist"]}}
    extra = [i["ticker"] for i in b["indices"]] + [m["ticker"] for m in b["macro"]] + [f["ticker"] for f in bu.get("futures", [])]
    hist = src.history(list(dict.fromkeys(kr_uni + watch + extra)), period="2y")
    tcfg = cfg.get("technical")
    tech_all = {t: technicals(h, tcfg, with_ta=False) for t, h in hist.items() if len(h) >= 30}
    kr_rows = [{"ticker": t, "market": "kr", "name": names.get(t, t), "tech": tech_all[t]} for t in kr_uni if t in tech_all]
    # 실적 발표일: 아침에 받은 펀더멘털 캐시 재사용(추가 호출 거의 없음)
    us_rows = [{"ticker": t, "name": names.get(t, t), "fund": src.fundamentals(t)} for t in us_uni]
    watch_rows = _watch_rows(src, cfg, hist, None)
    news = []
    for t in bu.get("news_tickers", ["SPY", "QQQ"]):
        news += src.news(t)
    seen, uniq = set(), []
    for n in news:
        if n["title"] not in seen:
            seen.add(n["title"])
            uniq.append(n)
    brief = briefing_mod.build_us(cfg, tech_all, kr_rows, watch_rows, us_rows, uniq, now.astimezone(timezone.utc))
    brief["news_kr"] = src.market_news(bu.get("news_query_kr", "뉴욕증시 when:1d"), hl="ko", gl="KR", limit=8)
    try:
        g = geo_mod.collect(src, tech_all, now.strftime("%Y-%m-%d"), record=False)
        g["summary"] = geo_mod.summary_lines(g)
        brief["geo"] = g
    except Exception as e:
        log.warning("국제 정세(저녁) 실패: %s", e)
    picks = read_json("picks.json", default={}) or {}
    brief["us_picks"] = [{k: p.get(k) for k in ("ticker", "name", "score", "setup_label", "summary")}
                         for p in (picks.get("daily") or {}).get("us", [])]
    brief["date"] = now.strftime("%Y-%m-%d")
    brief["generated"] = now.isoformat(timespec="minutes")
    for r in watch_rows:
        r["name"] = names.get(r["ticker"], r["name"])
    brief["watch"] = [{"ticker": r["ticker"], "name": r["name"], "market": r["market"],
                       "last": r["tech"].get("last"), "chg_1d": r["tech"].get("chg_1d"), "chg_5d": r["tech"].get("chg_5d"),
                       "rsi14": r["tech"].get("rsi14"), "spark": (r["tech"].get("spark") or [])[-60:],
                       "setup_label": (r["tech"].get("ta") or {}).get("setup_label")} for r in watch_rows]
    write_json("briefing_us.json", brief)
    write_json(f"briefing_us_{brief['date']}.json", brief, DATA_DIR / "archive")
    head = " / ".join(f"{q['name']} {q['chg']:+.2f}%" for q in brief["futures"][:2] + brief["kr_indices"][:1] if q.get("chg") is not None)
    if push:
        alert_mod.push({"title": f"🌙 미장 장전 {brief['date']}", "body": head or "미장 브리핑 업데이트", "url": "./#brief-us"})
    meta["evening"] = now.isoformat(timespec="minutes")
    log.info("미장 브리핑 완료: 선물 %s, 실적 %d건", [q.get("chg") for q in brief["futures"]], len(brief["earnings"]))


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
    write_json("pdca.json", pdca_mod.build(model, ev, read_json("track.json") or {}, read_json("pdca.json"),
                                           now.strftime("%Y-%m-%d")))
    meta["backtest"] = res["generated"]
    log.info("백테스트 완료: 표본 %s, 실전 기록 %s일, 모델 v%s (변경 %s)", res.get("samples"), ev.get("days", 0),
             model["version"], model["changed"])
    if model["changed"] and prev:
        alert_mod.push({"title": f"🔧 모델 자가 보정 v{model['version']}",
                        "body": "\n".join(c["text"] for c in model["log"][:3]), "url": "./#lab"})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("job", choices=["morning", "evening", "alerts", "all", "backtest"])
    ap.add_argument("--demo", action="store_true", help="합성 데이터 사용")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    run(a.job, a.demo)


if __name__ == "__main__":
    main()
