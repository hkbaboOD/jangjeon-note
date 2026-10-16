"""장전 시황 브리핑 생성 (규칙 기반 + 선택적 Claude 요약)."""
from __future__ import annotations

import json
import logging
import os

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


def _llm_summary(cfg: dict, data: dict) -> str | None:
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        return None
    compact = {k: data[k] for k in ("indices", "macro", "breadth", "movers", "points", "diagnosis")}
    for sec in ("indices", "macro"):
        compact[sec] = [{k: q.get(k) for k in ("name", "last", "chg", "chg_bp")} for q in compact[sec]]
    compact["headlines"] = [n["title"] for n in data["news"][:10]]
    prompt = ("다음은 전일 미국·한국 시장 데이터입니다. 한국 개인투자자를 위한 '장전 시황' 요약을 한국어로 작성하세요. "
              "형식: ①전일 미 증시 한 줄 ②매크로(금리·유가·환율) 한 줄 ③오늘 코스피/코스닥 전망 2~3줄 "
              "④매도 신호가 펀더멘털성인지 수급·심리성인지 판단과 근거 1~2줄. 데이터에 없는 수치는 만들지 마세요. "
              "투자 권유 표현은 피하세요.\n\n" + json.dumps(compact, ensure_ascii=False))
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
