"""장전 시황 브리핑 생성 (규칙 기반 + 선택적 Claude 요약)."""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone

import requests

log = logging.getLogger(__name__)


def _quote(tech: dict | None, name: str, ticker: str, is_yield=False) -> dict:
    if not tech:
        return {"ticker": ticker, "name": name, "last": None, "chg": None}
    q = {"ticker": ticker, "name": name, "last": tech["last"], "chg": tech["chg_1d"], "date": tech["date"],
         "spark": tech["spark"][-30:]}
    if is_yield and tech.get("prev") is not None:
        q["chg_bp"] = (tech["last"] - tech["prev"]) * 100
    return q


def build(cfg: dict, tech: dict, universe_rows: list[dict], watch_rows: list[dict], news: list[dict]) -> dict:
    b = cfg["briefing"]
    indices = [_quote(tech.get(i["ticker"]), i["name"], i["ticker"]) for i in b["indices"]]
    macro = [_quote(tech.get(m["ticker"]), m["name"], m["ticker"], m.get("is_yield")) for m in b["macro"]]
    by = {q["ticker"]: q for q in indices + macro}

    def chg(t):
        return (by.get(t) or {}).get("chg")

    # 시장 폭(breadth) · 상승/하락 상위
    breadth, movers = {}, {}
    for mk in ("us", "kr"):
        rows = [r for r in universe_rows if r["market"] == mk and r.get("tech") and r["tech"].get("chg_1d") is not None]
        if not rows:
            continue
        up = sum(1 for r in rows if r["tech"]["chg_1d"] > 0)
        breadth[mk] = {"up": up, "down": len(rows) - up, "total": len(rows)}
        srt = sorted(rows, key=lambda r: r["tech"]["chg_1d"])
        pick = lambda r: {"ticker": r["ticker"], "name": r.get("name") or r["ticker"], "chg": r["tech"]["chg_1d"]}  # noqa: E731
        movers[mk] = {"top": [pick(r) for r in srt[::-1][:5]], "bottom": [pick(r) for r in srt[:5]]}

    # 규칙 기반 해석
    points, outlook = [], []
    sox, ndx, spx = chg("^SOX"), chg("^IXIC"), chg("^GSPC")
    vix, wti = chg("^VIX"), chg("CL=F")
    tnx_bp = (by.get("^TNX") or {}).get("chg_bp")
    krw = chg("KRW=X")
    if spx is not None and ndx is not None:
        tone = "강세" if spx > 0.3 else "약세" if spx < -0.3 else "보합"
        points.append(f"미 증시 {tone}: S&P500 {spx:+.2f}%, 나스닥 {ndx:+.2f}%")
    if sox is not None:
        if sox <= -2:
            points.append(f"반도체 약세(SOX {sox:+.2f}%)")
            outlook.append("국내 반도체 대형주(삼성전자·SK하이닉스) 약세 출발 가능성 — 코스피 반도체 비중이 커 지수 영향 큼")
        elif sox >= 2:
            points.append(f"반도체 강세(SOX {sox:+.2f}%)")
            outlook.append("반도체 대형주 중심 강세 출발 기대")
    if tnx_bp is not None and abs(tnx_bp) >= 5:
        d = "상승" if tnx_bp > 0 else "하락"
        points.append(f"미 10년물 금리 {tnx_bp:+.0f}bp {d}")
        outlook.append("금리 상승 → 성장주 밸류에이션 부담" if tnx_bp > 0 else "금리 하락 → 성장주·기술주 우호적")
    if wti is not None and abs(wti) >= 3:
        points.append(f"유가 급변(WTI {wti:+.1f}%)")
        outlook.append("유가 상승: 정유·에너지 상대 수혜, 항공·화학 부담" if wti > 0 else "유가 하락: 항공·화학 수혜, 정유 부담")
    if vix is not None and vix >= 10:
        points.append(f"VIX {vix:+.1f}% 급등 — 위험회피 심리 확대")
        outlook.append("변동성 확대 국면: 분할 접근·현금 비중 점검")
    if krw is not None and abs(krw) >= 0.7:
        outlook.append(f"원/달러 {krw:+.2f}% — " + ("외국인 수급 부담" if krw > 0 else "외국인 수급 개선 여지"))
    if not outlook:
        outlook.append("뚜렷한 방향성 재료 부족 — 종목·업종별 차별화 장세 예상")

    # 관심종목 급락 진단
    diag = [{"ticker": w["ticker"], "name": w["name"], "chg_1d": w["tech"]["chg_1d"], **w["diag"]}
            for w in watch_rows if w.get("diag")]

    data = {"indices": indices, "macro": macro, "breadth": breadth, "movers": movers,
            "points": points, "outlook": outlook, "diagnosis": diag, "news": news[:12]}
    data["ai_summary"] = _llm_summary(cfg, data)
    return data


def earnings_soon(rows: list[dict], now_utc: datetime, days: int, tz_offset_h: int) -> list[dict]:
    """다음 실적 발표가 오늘~days일 안인 종목. tz_offset_h: 기준 시간대(미국 동부 -4/-5, 한국 +9)."""
    tz = timezone(timedelta(hours=tz_offset_h))
    today = now_utc.astimezone(tz).date()
    out = []
    for r in rows:
        ts = (r.get("fund") or {}).get("earnings_ts")
        if not ts:
            continue
        d = datetime.fromtimestamp(ts, tz).date()
        dd = (d - today).days
        if 0 <= dd <= days:
            out.append({"ticker": r["ticker"], "name": r.get("name") or r["ticker"], "date": d.isoformat(), "in_days": dd})
    return sorted(out, key=lambda x: (x["in_days"], x["name"]))


def us_eastern_offset(now_utc: datetime) -> int:
    """미국 동부 서머타임(3월 둘째 일요일 ~ 11월 첫째 일요일) 여부 → -4 / -5."""
    y = now_utc.year
    mar = datetime(y, 3, 8, 7, tzinfo=timezone.utc)
    start = mar + timedelta(days=(6 - mar.weekday()) % 7)
    nov = datetime(y, 11, 1, 6, tzinfo=timezone.utc)
    end = nov + timedelta(days=(6 - nov.weekday()) % 7)
    return -4 if start <= now_utc < end else -5


def build_us(cfg: dict, tech: dict, kr_rows: list[dict], watch_rows: list[dict], us_rows: list[dict],
             news: list[dict], now_utc: datetime) -> dict:
    """미장 장전 브리핑: 국내장 마감 리뷰 + 미 지수 선물·매크로 + 오늘 밤 체크(실적 발표 등)."""
    b = cfg["briefing"]
    bu = cfg.get("briefing_us") or {}
    fut = [_quote(tech.get(f["ticker"]), f["name"], f["ticker"]) for f in bu.get("futures", [])]
    kr_idx = [_quote(tech.get(i["ticker"]), i["name"], i["ticker"]) for i in b["indices"] if i["ticker"] in ("^KS11", "^KQ11")]
    us_prev = [_quote(tech.get(i["ticker"]), i["name"], i["ticker"]) for i in b["indices"] if i["ticker"] in ("^GSPC", "^IXIC", "^SOX")]
    macro = [_quote(tech.get(m["ticker"]), m["name"], m["ticker"], m.get("is_yield")) for m in b["macro"]]
    by = {q["ticker"]: q for q in fut + kr_idx + us_prev + macro}

    def chg(t):
        return (by.get(t) or {}).get("chg")

    # 국내장 마감 리뷰
    review, breadth, movers = [], {}, {}
    ks, kq, krw = chg("^KS11"), chg("^KQ11"), chg("KRW=X")
    if ks is not None:
        review.append(f"코스피 {by['^KS11']['last']:,.2f} ({ks:+.2f}%)" + (f", 코스닥 {kq:+.2f}%" if kq is not None else "")
                      + (f" · 원/달러 {by['KRW=X']['last']:,.1f}원({krw:+.2f}%)" if krw is not None else ""))
    rows = [r for r in kr_rows if r.get("tech") and r["tech"].get("chg_1d") is not None]
    if rows:
        up = sum(1 for r in rows if r["tech"]["chg_1d"] > 0)
        breadth["kr"] = {"up": up, "down": len(rows) - up, "total": len(rows)}
        srt = sorted(rows, key=lambda r: r["tech"]["chg_1d"])
        pick = lambda r: {"ticker": r["ticker"], "name": r.get("name") or r["ticker"], "chg": r["tech"]["chg_1d"]}  # noqa: E731
        movers["kr"] = {"top": [pick(r) for r in srt[::-1][:5]], "bottom": [pick(r) for r in srt[:5]]}
        review.append(f"국내 유니버스 상승 {up} · 하락 {len(rows) - up} — 강세 {srt[-1].get('name')} {srt[-1]['tech']['chg_1d']:+.1f}%, "
                      f"약세 {srt[0].get('name')} {srt[0]['tech']['chg_1d']:+.1f}%")

    # 오늘 밤 미국장 체크
    tonight = []
    es, nq = chg("ES=F"), chg("NQ=F")
    if es is not None:
        avg = (es + nq) / 2 if nq is not None else es
        tone = "강세" if avg > 0.3 else "약세" if avg < -0.3 else "보합"
        tonight.append(f"미 지수 선물 {tone}: S&P500 선물 {es:+.2f}%" + (f", 나스닥100 선물 {nq:+.2f}%" if nq is not None else ""))
    vix = by.get("^VIX") or {}
    if vix.get("last") is not None:
        lv = vix["last"]
        tonight.append(f"VIX {lv:.1f} — " + ("변동성 높은 구간(25 이상)" if lv >= 25 else "변동성 경계 구간(20~25)" if lv >= 20 else "안정 구간(20 미만)"))
    tnx_bp = (by.get("^TNX") or {}).get("chg_bp")
    if tnx_bp is not None and abs(tnx_bp) >= 4:
        tonight.append(f"미 10년물 금리 {tnx_bp:+.0f}bp — " + ("성장주 부담" if tnx_bp > 0 else "성장주 우호적"))
    wti = chg("CL=F")
    if wti is not None and abs(wti) >= 2:
        tonight.append(f"WTI {wti:+.1f}% — " + ("에너지 강세·인플레 우려" if wti > 0 else "에너지 약세·인플레 부담 완화"))
    dxy = chg("DX-Y.NYB")
    if dxy is not None and abs(dxy) >= 0.5:
        tonight.append(f"달러인덱스 {dxy:+.2f}%")
    off = us_eastern_offset(now_utc)
    earn = earnings_soon(us_rows, now_utc, 1, off)
    if earn:
        td = [e["name"] for e in earn if e["in_days"] == 0]
        tm = [e["name"] for e in earn if e["in_days"] == 1]
        tonight.append("실적 발표 — " + " / ".join(x for x in (
            ("오늘(미국 시간): " + ", ".join(td[:6])) if td else "", ("내일: " + ", ".join(tm[:6])) if tm else "") if x))
    if not tonight:
        tonight.append("뚜렷한 선행 신호 없음 — 개장 후 수급 확인")
    open_kst = (datetime(now_utc.year, now_utc.month, now_utc.day, 13, 30, tzinfo=timezone.utc)
                - timedelta(hours=off + 4)).astimezone(timezone(timedelta(hours=9)))

    diag = [{"ticker": w["ticker"], "name": w["name"], "chg_1d": w["tech"]["chg_1d"], **w["diag"]}
            for w in watch_rows if w.get("diag")]
    data = {"futures": fut, "kr_indices": kr_idx, "us_prev": us_prev, "macro": macro, "breadth": breadth, "movers": movers,
            "review": review, "tonight": tonight, "earnings": earn, "diagnosis": diag, "news": news[:12],
            "us_open_kst": open_kst.strftime("%H:%M"), "dst": off == -4}
    data["ai_summary"] = _llm_summary(cfg, data, kind="us")
    return data


def _llm_summary(cfg: dict, data: dict, kind: str = "kr") -> str | None:
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        return None
    if kind == "us":
        compact = {k: data[k] for k in ("futures", "kr_indices", "macro", "review", "tonight", "earnings")}
        secs = ("futures", "kr_indices", "macro")
        head = ("다음은 오늘 한국장 마감과 미국 개장 전 데이터입니다. 한국 개인투자자를 위한 '미장 장전' 요약을 한국어로 작성하세요. "
                "형식: ①오늘 국내장 마감 한 줄 ②미 지수 선물·매크로 한 줄 ③오늘 밤 미국장 체크포인트 2~3줄(실적 발표 포함). ")
    else:
        compact = {k: data[k] for k in ("indices", "macro", "breadth", "movers", "points", "diagnosis")}
        secs = ("indices", "macro")
        head = ("다음은 전일 미국·한국 시장 데이터입니다. 한국 개인투자자를 위한 '국장 장전' 요약을 한국어로 작성하세요. "
                "형식: ①전일 미 증시 한 줄 ②매크로(금리·유가·환율) 한 줄 ③오늘 코스피/코스닥 전망 2~3줄 "
                "④매도 신호가 펀더멘털성인지 수급·심리성인지 판단과 근거 1~2줄. ")
    for sec in secs:
        compact[sec] = [{k: q.get(k) for k in ("name", "last", "chg", "chg_bp")} for q in compact[sec]]
    compact["headlines"] = [n["title"] for n in data["news"][:10]]
    prompt = head + "데이터에 없는 수치는 만들지 마세요. 투자 권유 표현은 피하세요.\n\n" + json.dumps(compact, ensure_ascii=False)
    try:
        r = requests.post("https://api.anthropic.com/v1/messages", timeout=60, headers={
            "x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
            json={"model": cfg["briefing"].get("llm_model", "claude-sonnet-5-5"), "max_tokens": 900,
                  "messages": [{"role": "user", "content": prompt}]})
        r.raise_for_status()
        return "".join(b.get("text", "") for b in r.json().get("content", []))
    except Exception as e:
        log.warning("Claude 요약 실패: %s", e)
        return None
