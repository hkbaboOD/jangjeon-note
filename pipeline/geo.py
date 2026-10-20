"""국제 정세 체크 — 뉴스 관심도 × 어조 × 시장 반응으로 테마별 강도를 재고, 업종·종목 영향으로 연결.

테마별 계산 (매일 장전)
  관심도(heat)  : 최근 48시간 한국어 뉴스 건수 ÷ 평소(최근 기록 중앙값). 기록이 쌓이기 전엔 절대 건수 기준
  어조(tone)    : 제목의 긴장 고조 단어 vs 완화 단어 비율 (-1 완화 ~ +1 고조)
  시장 반응      : 관련 가격(유가·반도체지수·원달러·미 10년물·VIX·금)의 5일 변화가 '고조' 방향인지
  방향 = 어조·시장 반응 가중 평균, 강도 = 관심도(0~1) × 방향(-1~+1)
업종 영향 = Σ 테마 강도 × 노출도(config/companies.yaml 태그), 종목 영향 = 보유 태그 합
→ 'geo' 팩터(0~100, 50=중립)로 매일 기록. 가중치 0에서 시작해 실전 IC가 유의할 때만 자가 보정이 비중을 줌.

한계: 키워드 기반이라 기사 맥락을 완전히 이해하지는 못합니다. 주간 자동 점검이 웹 검색으로 정세 해설(geo_weekly.json)을 덧붙입니다.
"""
from __future__ import annotations

import logging
import math
import statistics
from datetime import datetime, timedelta, timezone

from .common import STATE_DIR, read_json, write_json
from .company import TAG_KO, tags_of

log = logging.getLogger(__name__)
HIST = "geo_hist.json"

ESC = ["격화", "확전", "제재", "보복", "강화", "공습", "공격", "도발", "고조", "위협", "봉쇄", "충돌", "부과", "강경",
       "위기", "미사일", "침공", "폭격", "경고", "중단", "긴장", "불안", "쇼크"]
DE = ["휴전", "합의", "타결", "완화", "진전", "유예", "해제", "철회", "대화", "정상회담", "진정", "종전", "평화",
      "인하", "면제", "안정", "협력"]

THEMES = [
    {"key": "us_china", "label": "미중 갈등·수출규제", "q": "미중 갈등 OR 대중 수출통제 OR 반도체 수출규제",
     "exp": {"china": -1.0, "semis": -0.6, "tariff": -0.3, "defense": 0.3},
     "confirm": [("^SOX", -1, 5.0)], "mkt_w": 0.4, "esc_word": "갈등 고조", "de_word": "갈등 완화"},
    {"key": "tariff", "label": "관세·무역분쟁", "q": "관세 부과 OR 무역협상 OR 관세 인상",
     "exp": {"tariff": -1.0, "export": -0.6, "china": -0.3, "defensive": 0.3},
     "confirm": [("^GSPC", -1, 3.0), ("KRW=X", 1, 1.5)], "mkt_w": 0.4, "esc_word": "무역 긴장", "de_word": "협상 진전"},
    {"key": "mideast", "label": "중동 정세", "q": "중동 정세 OR 이란 이스라엘 OR 호르무즈",
     "exp": {"oil_up": 1.0, "oil_down": -1.0, "defense": 0.6, "shipping": 0.4, "korea": -0.2},
     "confirm": [("CL=F", 1, 6.0), ("GC=F", 1, 3.0)], "mkt_w": 0.5, "esc_word": "긴장 고조", "de_word": "긴장 완화"},
    {"key": "russia", "label": "러시아·우크라이나", "q": "러시아 우크라이나 전쟁 OR 러우 휴전",
     "exp": {"defense": 1.0, "oil_up": 0.4, "oil_down": -0.4},
     "confirm": [("BZ=F", 1, 6.0), ("GC=F", 1, 3.0)], "mkt_w": 0.3, "esc_word": "전쟁 격화", "de_word": "휴전 기대"},
    {"key": "nk", "label": "북한·한반도", "q": "북한 미사일 OR 북한 도발",
     "exp": {"korea": -0.6, "defense": 0.5},
     "confirm": [("KRW=X", 1, 1.5)], "mkt_w": 0.3, "esc_word": "도발", "de_word": "긴장 완화"},
    {"key": "taiwan", "label": "대만 해협", "q": "대만해협 OR 양안 긴장 OR 중국 대만 군사",
     "exp": {"semis": -0.8, "china": -0.5, "ai": -0.3, "defense": 0.6},
     "confirm": [("^SOX", -1, 5.0), ("^VIX", 1, 20.0)], "mkt_w": 0.4, "esc_word": "긴장 고조", "de_word": "긴장 완화"},
    {"key": "fed", "label": "연준·금리", "q": "연준 금리 OR FOMC OR 파월",
     "exp": {"rate_sensitive": -1.0, "bank": 0.6, "ai": -0.3},
     "confirm": [("^TNX", 1, 4.0)], "mkt_w": 0.7, "esc_word": "매파(금리 상승)", "de_word": "비둘기(금리 하락)",
     "esc": ["인상", "매파", "긴축", "동결 장기화", "상승", "높은 금리", "물가 상승"], "de": ["인하", "비둘기", "완화", "하락", "피벗"]},
    {"key": "fx", "label": "환율(원화)", "q": "원달러 환율",
     "exp": {"export": 0.6, "oil_down": -0.4, "korea": -0.3},
     "confirm": [("KRW=X", 1, 1.5)], "mkt_w": 0.8, "esc_word": "원화 약세", "de_word": "원화 강세",
     "esc": ["급등", "상승", "약세", "돌파", "고환율"], "de": ["하락", "강세", "급락", "안정"]},
]
TH = {t["key"]: t for t in THEMES}
TK_KO = {"^SOX": "반도체지수", "^GSPC": "S&P500", "KRW=X": "원달러", "CL=F": "WTI", "BZ=F": "브렌트", "GC=F": "금",
         "^KS11": "코스피", "^VIX": "VIX", "^TNX": "미 10년물 금리"}


def _clip(x, lo=-1.0, hi=1.0):
    return max(lo, min(hi, x))


def tone(titles: list[str], esc: list[str] | None = None, de: list[str] | None = None) -> tuple[float, int, int]:
    """제목별로 고조/완화 중 하나로 분류. 둘 다 있으면 뒤에 나온 단어 기준(한국어는 서술이 뒤에 옴: '제재 완화'→완화)."""
    esc, de = esc or ESC, de or DE
    e = d = 0
    for t in titles:
        pe = max((t.rfind(w) for w in esc), default=-1)
        pd_ = max((t.rfind(w) for w in de), default=-1)
        if pe < 0 and pd_ < 0:
            continue
        if pe > pd_:
            e += 1
        else:
            d += 1
    if e + d < 3:  # 표본이 적으면 판단 보류
        return 0.0, e, d
    return round((e - d) / (e + d), 2), e, d


def heat(n: int, past: list[int]) -> tuple[float, float | None, float | None, str]:
    """(0~1 관심도, 평소 건수, 배율, 수준)."""
    base = statistics.median(past) if len(past) >= 5 else None
    if base is not None:
        ratio = n / max(base, 3.0)
        h = _clip((ratio - 0.5) / 1.5, 0.0, 1.0)
        lvl = "매우 높음" if ratio >= 2 else "높음" if ratio >= 1.3 else "보통" if ratio >= 0.7 else "낮음"
        return round(h, 2), round(base, 1), round(ratio, 2), lvl
    h = _clip(n / 80, 0.0, 1.0)
    lvl = "높음" if n >= 60 else "보통" if n >= 25 else "낮음"
    return round(h, 2), None, None, lvl


def _recent(items: list[dict], hours: int = 48) -> list[dict]:
    cut = datetime.now(timezone.utc) - timedelta(hours=hours)
    out = []
    for n in items:
        try:
            ts = datetime.fromisoformat(str(n.get("time")).replace("Z", "+00:00"))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            if ts < cut:
                continue
        except Exception:
            pass  # 시각 없으면 포함
        out.append(n)
    return out


def assess(news_by_theme: dict[str, list[dict]], tech_all: dict, today: str, hist: dict | None = None) -> dict:
    """테마별 강도·업종 영향 계산 (순수 함수: 테스트 용이)."""
    hist = hist if hist is not None else {}
    themes, impact = [], {}
    for th in THEMES:
        k = th["key"]
        items = _recent(news_by_theme.get(k) or [])
        titles = [n["title"] for n in items]
        past = [e["n"] for e in (hist.get(k) or []) if e["date"] != today][-30:]
        h, base, ratio, lvl = heat(len(items), past)
        tn, e_cnt, d_cnt = tone(titles, th.get("esc"), th.get("de"))
        conf, moves = [], {}
        for tk, sign, scale in th["confirm"]:
            ch = (tech_all.get(tk) or {}).get("chg_5d")
            if ch is None:
                continue
            moves[tk] = round(ch, 2)
            conf.append(_clip(sign * ch / scale))
        cf = sum(conf) / len(conf) if conf else 0.0
        w = th["mkt_w"] if conf else 0.0
        # 가격은 여러 원인으로 움직이므로, 뉴스 어조와 같은 방향일 때만 온전히 반영(어조 보류면 절반, 반대면 1/4)
        agree = 1.0 if tn * cf > 0 else 0.5 if tn == 0 else 0.25
        direction = _clip((1 - w) * tn + w * cf * agree)
        inten = round(h * direction, 3)
        stance = (th["esc_word"] if direction >= 0.2 else th["de_word"] if direction <= -0.2 else "방향 혼재")
        if abs(direction) < 0.2:  # 방향 혼재 → 업종 영향 없음으로 처리
            inten = 0.0
        up = sorted([(t, v * inten) for t, v in th["exp"].items() if v * inten > 0.02], key=lambda x: -x[1])
        down = sorted([(t, v * inten) for t, v in th["exp"].items() if v * inten < -0.02], key=lambda x: x[1])
        for t, v in th["exp"].items():
            impact[t] = impact.get(t, 0.0) + v * inten
        themes.append({
            "key": k, "label": th["label"], "n": len(items), "base": base, "ratio": ratio, "level": lvl, "heat": h,
            "tone": tn, "esc_n": e_cnt, "de_n": d_cnt, "confirm": round(cf, 2), "moves": moves,
            "direction": round(direction, 2), "intensity": inten, "stance": stance,
            "up": [TAG_KO.get(t, t) for t, _ in up], "down": [TAG_KO.get(t, t) for t, _ in down],
            "headlines": items[:3],
        })
    themes.sort(key=lambda x: -(abs(x["intensity"]) + 0.3 * x["heat"]))
    impact = {t: round(v, 3) for t, v in impact.items() if abs(v) >= 0.005}
    return {"date": today, "themes": themes, "impact": impact}


def ticker_geo(ticker: str, companies: dict, res: dict) -> tuple[float | None, list[dict]]:
    """종목의 정세 점수(0~100, 50 중립)와 테마별 영향 설명."""
    tags = tags_of(ticker, companies)
    if not tags or not res:
        return None, []
    raw = sum(res["impact"].get(t, 0.0) for t in tags)
    score = round(50 + 50 * math.tanh(raw * 1.5), 1)
    notes = []
    for th in res["themes"]:
        ex = TH[th["key"]]["exp"]
        eff = sum(ex.get(t, 0.0) for t in tags) * th["intensity"]
        if abs(eff) >= 0.08 and abs(th["direction"]) >= 0.2:  # 방향 혼재 테마는 종목 영향 표시 생략
            via = [TAG_KO.get(t, t) for t in tags if ex.get(t) and (ex[t] * th["intensity"] > 0) == (eff > 0)]
            notes.append({"pol": 1 if eff > 0 else -1, "theme": th["label"], "eff": round(eff, 2),
                          "text": f"{th['label']}: {th['stance']}(관심 {th['level']}) → "
                                  f"{'·'.join(via)} 성격상 {'우호적' if eff > 0 else '부담'}"})
    notes.sort(key=lambda x: -abs(x["eff"]))
    return score, notes[:3]


def summary_lines(res: dict, names_by_tag: dict[str, list[str]] | None = None) -> list[str]:
    out = []
    for th in res["themes"]:
        if th["heat"] < 0.25 and abs(th["intensity"]) < 0.1:
            continue
        mv = ", ".join(f"{TK_KO.get(k, k)} {v:+.1f}%" for k, v in th["moves"].items())
        s = (f"{th['label']}: 48시간 기사 {th['n']}건"
             + (f"(평소 {th['base']:.0f}건의 {th['ratio']:.1f}배)" if th["ratio"] else "")
             + f", {th['stance']}" + (f" · 5일 {mv}" if mv else ""))
        if abs(th["intensity"]) >= 0.1 and abs(th["direction"]) >= 0.2 and (th["up"] or th["down"]):
            s += " → " + " / ".join(x for x in (
                ("유리: " + ", ".join(th["up"])) if th["up"] else "",
                ("부담: " + ", ".join(th["down"])) if th["down"] else "") if x)
        out.append(s)
        if len(out) >= 4:
            break
    return out or ["주요 국제 정세 테마의 뉴스 관심도가 평소 수준이고 시장 반응도 제한적입니다."]


def collect(src, tech_all: dict, today: str, record: bool = True) -> dict:
    """뉴스 수집 + 평가 + 기록 갱신(record=False면 기록·평소 건수는 건드리지 않음: 저녁 미장 브리핑용)."""
    news = {}
    for th in THEMES:
        news[th["key"]] = src.market_news(th["q"] + " when:2d", hl="ko", gl="KR", limit=100)
    hist = read_json(HIST, STATE_DIR, default={}) or {}
    res = assess(news, tech_all, today, hist)
    if record:
        for th in res["themes"]:
            h = [e for e in (hist.get(th["key"]) or []) if e["date"] != today]
            hist[th["key"]] = (h + [{"date": today, "n": th["n"], "tone": th["tone"]}])[-90:]
        write_json(HIST, hist, STATE_DIR)
    log.info("국제 정세: %s", ", ".join(f"{t['label']} {t['n']}건/{t['stance']}" for t in res["themes"][:4]))
    return res
