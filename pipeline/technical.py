"""차트(기술적) 분석 엔진.

일봉 OHLCV 하나로 아래를 계산해 '왜 이 종목인가'를 차트 관점에서 설명합니다.

이평선(기본 20/50/120/250일)
  - 배열: 완전 정배열 / 역배열 / 단기 정배열 전환 / 장기 상승추세 속 단기 조정 / 혼조
  - 기울기(250일선 20일 변화율, 50일선 10일 변화율), 주가-이평 거리, 20일 이격도
  - 크로스: 20·50(단기), 50·120(중기), 50·250(장기 골든/데드크로스), 주가의 250일선 돌파·이탈
  - 지지/저항 테스트: 저가가 이평선을 터치하고 종가가 회복(지지) / 고가가 터치 후 밀림(저항)
모멘텀: RSI(14) 구간·30 상향 돌파·70 하향 이탈·다이버전스, MACD(12,26,9) 크로스·0선·히스토그램 개선
추세 강도: ADX(14), +DI/-DI
변동성: 볼린저(20,2) %B·하단 이탈 후 재진입·상단 돌파·밴드폭 스퀴즈
거래량: 20일 평균 대비 배수, 최근 20일 상승일/하락일 거래량 비율
캔들: 망치형·상승장악형·하락장악형·장대양봉/음봉, 52주 신고가/신저가
가격대: 스윙 고·저점 + 이평선 기반 지지·저항, ATR 기반 손절 참고가, 손익비

산출물: signals(근거 문장, +/−/중립), 점수(추세·모멘텀·타이밍·종합 0~100), 셋업 분류, 가격대, 주요 수치.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

DEFAULT = {"ma": [20, 50, 120, 250], "rsi": 14, "macd": [12, 26, 9], "bb": [20, 2.0], "chart_days": 130}

SETUPS = {
    "breakout": ("신고가·돌파형", "정배열 상승추세에서 저항을 거래량과 함께 돌파 — 추세 추종 관점. 돌파 가격대 재이탈 여부 확인"),
    "pullback": ("상승추세 속 눌림목", "장기 상승추세(120·250일선 위)를 유지한 채 단기 조정 — 지지선 근처 분할 접근 관점"),
    "reversal": ("하락 후 반등 시도", "하락 추세에서 과매도 해소·반전 신호 출현 — 20일선 회복 등 추세 전환 확인 전까지 비중 제한"),
    "turn": ("장기 추세 전환 초기", "50·250일선 골든크로스 또는 250일선 돌파 — 하락→상승 전환 초기, 250일선 재테스트(되돌림) 지지 여부 확인"),
    "squeeze": ("변동성 수축(방향 대기)", "볼린저 밴드폭이 6개월 최저 수준 — 돌파 방향 확인 후 대응"),
    "uptrend": ("정배열 추세 지속", "이평선 정배열 유지 — 단기 과열(RSI·이격도) 여부만 점검"),
    "range": ("박스권 횡보", "추세 강도 약함(ADX 20 미만) — 지지·저항 구간 중심 접근"),
    "downtrend": ("역배열 하락추세", "저평가라도 추세 반전 신호 전까지는 관망 — 가치 함정 주의"),
    "neutral": ("중립", "뚜렷한 차트 셋업 없음"),
    "insufficient": ("데이터 부족", "상장 기간이 짧아 장기 이평선 분석 불가"),
}


# ──────────────────────────── 지표 계산 ────────────────────────────
def sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n, min_periods=n).mean()


def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False).mean()


def rsi_series(c: pd.Series, n: int = 14) -> pd.Series:
    d = c.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    with np.errstate(divide="ignore", invalid="ignore"):
        r = 100 - 100 / (1 + up / dn)
    r = r.where(dn != 0, np.where(up > 0, 100.0, 50.0))
    return r.where(up.notna())


def macd_series(c: pd.Series, f=12, s=26, g=9):
    m = ema(c, f) - ema(c, s)
    sig = ema(m, g)
    return m, sig, m - sig


def bollinger(c: pd.Series, n=20, k=2.0):
    mid = sma(c, n)
    sd = c.rolling(n, min_periods=n).std(ddof=0)
    up, lo = mid + k * sd, mid - k * sd
    with np.errstate(divide="ignore", invalid="ignore"):
        pctb = (c - lo) / (up - lo)
        bw = (up - lo) / mid
    return mid, up, lo, pctb, bw


def adx_series(h, l, c, n=14):
    up, dn = h.diff(), -l.diff()
    pdm = pd.Series(np.where((up > dn) & (up > 0), up, 0.0), index=c.index)
    mdm = pd.Series(np.where((dn > up) & (dn > 0), dn, 0.0), index=c.index)
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    w = dict(alpha=1 / n, adjust=False, min_periods=n)
    atr = tr.ewm(**w).mean()
    with np.errstate(divide="ignore", invalid="ignore"):
        pdi = 100 * pdm.ewm(**w).mean() / atr
        mdi = 100 * mdm.ewm(**w).mean() / atr
        dx = 100 * (pdi - mdi).abs() / (pdi + mdi)
    return dx.ewm(**w).mean(), pdi, mdi, atr


def last_cross(a: pd.Series, b: pd.Series, lookback: int = 10):
    """가장 최근 크로스 ('golden'|'dead', n거래일 전) 또는 None."""
    d = (a - b).dropna()
    if len(d) < 2:
        return None
    s = np.sign(d.values)
    for i in range(len(s) - 1, max(len(s) - 1 - lookback, 0), -1):
        prev = s[i - 1]
        j = i - 1
        while prev == 0 and j > 0:  # 정확히 0인 날은 그 이전 부호로
            j -= 1
            prev = s[j]
        if s[i] > 0 > prev:
            return "golden", len(s) - 1 - i
        if s[i] < 0 < prev:
            return "dead", len(s) - 1 - i
    return None


def pivots(x: np.ndarray, w: int, kind: str) -> list[int]:
    out = []
    for i in range(w, len(x) - w):
        win = x[i - w:i + w + 1]
        if np.isnan(x[i]):
            continue
        if (kind == "low" and x[i] <= np.nanmin(win)) or (kind == "high" and x[i] >= np.nanmax(win)):
            if not out or i - out[-1] > 1:  # 같은 값이 연속되면 첫 지점만
                out.append(i)
    return out


def _f(x, nd=None):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    if math.isnan(v) or math.isinf(v):
        return None
    return round(v, nd) if nd is not None else v


def _r(x):
    """차트용 반올림: 큰 가격은 정수, 작은 값은 소수 2~4자리."""
    v = _f(x)
    if v is None:
        return None
    a = abs(v)
    return round(v) if a >= 1000 else round(v, 2) if a >= 1 else round(v, 4)


# ──────────────────────────── 분석 ────────────────────────────
def analyze(df: pd.DataFrame, cfg: dict | None = None) -> dict:
    cfg = {**DEFAULT, **(cfg or {})}
    mas = list(cfg["ma"])
    c = df["Close"].astype(float)
    o = df["Open"].astype(float).fillna(c)
    h = df["High"].astype(float).fillna(c)
    l = df["Low"].astype(float).fillna(c)
    v = df["Volume"].astype(float).fillna(0) if "Volume" in df else pd.Series(0.0, index=c.index)
    n = len(c)
    if n < 60:
        lab, desc = SETUPS["insufficient"]
        return {"setup": "insufficient", "setup_label": lab, "setup_desc": desc, "trend_label": "-",
                "scores": {}, "signals": [], "tags": [], "levels": {}, "values": {}}

    last = float(c.iloc[-1])
    sig: list[dict] = []

    def add(code, cat, pol, w, text):
        sig.append({"code": code, "cat": cat, "pol": pol, "w": w, "text": text})

    # ── 이평선 ──
    ma_s = {m: sma(c, m) for m in mas}
    ma = {m: _f(ma_s[m].iloc[-1]) for m in mas}
    have = [m for m in mas if ma[m] is not None]
    dist = {m: (last / ma[m] - 1) * 100 for m in have}
    s_short, s_mid = mas[0], mas[1] if len(mas) > 1 else mas[0]
    s_long = mas[-1]
    s_mid2 = mas[2] if len(mas) > 2 else s_mid

    seq = [last] + [ma[m] for m in have]
    pairs = len(seq) - 1
    ordered = sum(seq[i] > seq[i + 1] for i in range(pairs))
    reversed_ = sum(seq[i] < seq[i + 1] for i in range(pairs))
    full_long = len(have) == len(mas)
    names = "·".join(str(m) for m in have)

    def slope(m, k):
        s = ma_s[m].dropna()
        if len(s) <= k:
            return None
        return (s.iloc[-1] / s.iloc[-1 - k] - 1) * 100

    sl_long = slope(s_long, 20) if ma[s_long] else None
    sl_mid = slope(s_mid, 10) if ma[s_mid] else None
    sl_mid2 = slope(s_mid2, 10) if ma.get(s_mid2) else None

    # 장기 상승추세: 주가가 상승 중인 장기선 위 + 중기선(120)이 장기선(250) 위
    long_up = bool(ma.get(s_mid2) and ma.get(s_long) and last > ma[s_long] and (sl_long or 0) > 0
                   and ma[s_mid2] > ma[s_long])
    below = [m for m in have if last < ma[m]]
    above = [m for m in have if last >= ma[m]]
    if ordered == pairs and full_long:
        trend_label = "완전 정배열"
        add("ma_aligned", "이평선", 1, 3, f"이평선 완전 정배열(주가>{names}일선) — 단·중·장기 모두 상승 추세")
    elif reversed_ == pairs and full_long:
        trend_label = "완전 역배열"
        add("ma_reversed", "이평선", -1, 3, f"이평선 역배열(주가<{names}일선) — 단·중·장기 모두 하락 추세")
    elif ma.get(s_mid) and ma.get(s_mid2) and ma.get(s_long) and ma[s_mid] > ma[s_mid2] > ma[s_long] and \
            (last < ma[s_short] or ma[s_short] < ma[s_mid]):
        trend_label = "장기 정배열 속 단기 조정"
        where = (f"{max(below)}일선 아래까지 조정, {min(above, key=lambda m: dist[m])}일선 위에서 지지 시도"
                 if below and above else f"{s_short}일선 부근으로 조정")
        add("ma_long_aligned", "이평선", 1, 2, f"{s_mid}>{s_mid2}>{s_long}일선 장기 정배열 유지 — 주가는 {where}")
    elif ma.get(s_short) and ma.get(s_mid) and last > ma[s_short] > ma[s_mid] and \
            ma.get(s_long) and last < max(ma[s_long], ma.get(s_mid2) or 0):
        trend_label = "단기 정배열 전환"
        add("ma_short_turn", "이평선", 1, 2,
            f"단기 정배열(주가>{s_short}>{s_mid}일선) 전환, 아직 {s_mid2}·{s_long}일선 아래 — 추세 전환 초기")
    else:
        trend_label = "혼조(수렴)"
        add("ma_mixed", "이평선", 0, 1, "이평선 혼조 — 방향성 탐색 구간")

    if ma.get(s_long):
        side = "위" if last > ma[s_long] else "아래"
        pol = 1 if last > ma[s_long] else -1
        txt = f"주가 {s_long}일선 {side}({dist[s_long]:+.1f}%)"
        if sl_long is not None:
            if sl_long > 1:
                txt += f", {s_long}일선 우상향(20일간 {sl_long:+.1f}%) — 장기 추세 상승"
            elif sl_long < -1:
                txt += f", {s_long}일선 우하향(20일간 {sl_long:+.1f}%) — 장기 추세 하락"
                pol = -1 if pol < 0 else 0
            else:
                txt += f", {s_long}일선 횡보"
        add("ma_long_pos", "이평선", pol, 2, txt)

    # 크로스
    cross_pairs = [(s_short, s_mid, "단기"), (s_mid, s_mid2, "중기"), (s_mid, s_long, "장기")]
    crosses = []
    for a, b, lab in cross_pairs:
        if a == b or not (ma.get(a) and ma.get(b)):
            continue
        x = last_cross(ma_s[a], ma_s[b], lookback=10)
        if x:
            kind, ago = x
            when = "오늘" if ago == 0 else f"{ago}거래일 전"
            g = kind == "golden"
            extra = (" — 장기 추세 상승 전환 신호" if g else " — 장기 추세 하락 전환 신호") if lab == "장기" else ""
            add(f"cross_{a}_{b}_{kind}", "이평선", 1 if g else -1, 3 if lab == "장기" else 2,
                f"{a}·{b}일선 {'골든' if g else '데드'}크로스({lab}, {when}){extra}")
            crosses.append({"pair": [a, b], "kind": kind, "ago": ago})
    if ma.get(s_long):
        x = last_cross(c, ma_s[s_long], lookback=5)
        if x:
            kind, ago = x
            when = "오늘" if ago == 0 else f"{ago}거래일 전"
            g = kind == "golden"
            add(f"break_{s_long}_{'up' if g else 'down'}", "이평선", 1 if g else -1, 2,
                f"주가 {s_long}일선 {'상향 돌파' if g else '하향 이탈'}({when})")
            crosses.append({"pair": ["price", s_long], "kind": kind, "ago": ago})

    # 이평 지지/저항 테스트 (최근 3봉) — 가장 긴 이평 우선
    tested = None
    for m in sorted(have, reverse=True):
        mv = ma_s[m]
        recent = range(n - 3, n)
        prior_above = (c.iloc[-13:-3] > mv.iloc[-13:-3]).sum()
        prior_below = (c.iloc[-13:-3] < mv.iloc[-13:-3]).sum()
        touch_sup = any(l.iloc[i] <= mv.iloc[i] * 1.01 for i in recent) and last > ma[m]
        touch_res = any(h.iloc[i] >= mv.iloc[i] * 0.99 for i in recent) and last < ma[m]
        if touch_sup and prior_above >= 7:
            add(f"ma_support_{m}", "이평선", 1, 3 if m >= s_mid else 2, f"{m}일선 지지 확인 — 저가가 이평선 터치 후 종가 회복")
            tested = ("support", m)
            break
        if touch_res and prior_below >= 7:
            add(f"ma_resist_{m}", "이평선", -1, 2, f"{m}일선 저항 — 고가가 이평선 터치 후 밀림")
            tested = ("resist", m)
            break

    disp = (last / ma[s_short] * 100) if ma.get(s_short) else None
    if disp is not None:
        if disp >= 110:
            add("disp_hot", "이평선", -1, 2, f"{s_short}일 이격도 {disp:.0f} — 단기 과열(평균 회귀 압력)")
        elif disp <= 90:
            add("disp_cold", "이평선", 1, 1, f"{s_short}일 이격도 {disp:.0f} — 단기 침체(기술적 반등 여지)")

    # ── RSI ──
    rs = rsi_series(c, cfg["rsi"])
    r_now = _f(rs.iloc[-1])
    if r_now is not None:
        if r_now <= 30:
            add("rsi_oversold", "모멘텀", 1, 2, f"RSI {r_now:.0f} — 과매도 구간")
        elif r_now >= 70:
            add("rsi_overbought", "모멘텀", -1, 2, f"RSI {r_now:.0f} — 과매수 구간(단기 추격 주의)")
        elif r_now >= 50:
            add("rsi_bull", "모멘텀", 1, 1, f"RSI {r_now:.0f} — 50 이상 강세 영역")
        elif r_now <= 40:
            add("rsi_weak", "모멘텀", -1, 1, f"RSI {r_now:.0f} — 약세 영역")
        else:
            add("rsi_neutral", "모멘텀", 0, 1, f"RSI {r_now:.0f} — 중립")
        x = last_cross(rs, pd.Series(30.0, index=rs.index), lookback=5)
        if x and x[0] == "golden":
            add("rsi_reclaim30", "모멘텀", 1, 3, f"RSI 30 상향 돌파({'오늘' if x[1] == 0 else str(x[1]) + '거래일 전'}) — 과매도 탈출")
        x = last_cross(rs, pd.Series(70.0, index=rs.index), lookback=5)
        if x and x[0] == "dead":
            add("rsi_lose70", "모멘텀", -1, 2, f"RSI 70 하향 이탈({'오늘' if x[1] == 0 else str(x[1]) + '거래일 전'}) — 과열 해소·탄력 둔화")

    # 다이버전스 (최근 60봉, 스윙 w=3, 두 번째 저/고점이 최근 15봉 이내)
    div = None
    W = 60
    lw, hw, rw = l.values[-W:], h.values[-W:], rs.values[-W:]
    lows, highs = pivots(lw, 3, "low"), pivots(hw, 3, "high")
    if lows:
        i2 = lows[-1]
        earlier = [i for i in lows if i <= i2 - 5]
        if earlier and i2 >= W - 15:
            i1 = min(earlier, key=lambda i: lw[i])
            if lw[i2] < lw[i1] and not np.isnan(rw[i1]) and not np.isnan(rw[i2]) and rw[i2] > rw[i1] + 3 and rw[i1] < 40:
                div = "bull"
                add("div_bull", "모멘텀", 1, 3, f"RSI 상승 다이버전스 — 주가 저점은 낮아졌지만 RSI 저점은 상승({rw[i1]:.0f}→{rw[i2]:.0f}), 하락 탄력 약화")
    if highs and div is None:
        i2 = highs[-1]
        earlier = [i for i in highs if i <= i2 - 5]
        if earlier and i2 >= W - 15:
            i1 = max(earlier, key=lambda i: hw[i])
            if hw[i2] > hw[i1] and not np.isnan(rw[i1]) and not np.isnan(rw[i2]) and rw[i2] < rw[i1] - 3 and rw[i1] > 60:
                div = "bear"
                add("div_bear", "모멘텀", -1, 3, f"RSI 하락 다이버전스 — 주가 고점은 높아졌지만 RSI 고점은 하락({rw[i1]:.0f}→{rw[i2]:.0f}), 상승 탄력 약화")

    # ── MACD ──
    f_, s_, g_ = cfg["macd"]
    m_line, m_sig, m_hist = macd_series(c, f_, s_, g_)
    mv_, sv_, hv_ = _f(m_line.iloc[-1]), _f(m_sig.iloc[-1]), _f(m_hist.iloc[-1])
    x = last_cross(m_line, m_sig, lookback=5)
    if x:
        g = x[0] == "golden"
        zone = "0선 아래" if (mv_ or 0) < 0 else "0선 위"
        add(f"macd_{x[0]}", "모멘텀", 1 if g else -1, 2,
            f"MACD 시그널 {'상향' if g else '하향'} 돌파({'오늘' if x[1] == 0 else str(x[1]) + '거래일 전'}, {zone})")
    elif mv_ is not None and sv_ is not None:
        add("macd_state", "모멘텀", 1 if mv_ > sv_ else -1, 1, f"MACD {'시그널 위' if mv_ > sv_ else '시그널 아래'}, {'0선 위' if mv_ > 0 else '0선 아래'}")
    hh = m_hist.iloc[-4:].values
    if len(hh) == 4 and hh[-1] < 0 and all(hh[i] > hh[i - 1] for i in range(1, 4)):
        add("macd_hist_improve", "모멘텀", 1, 1, "MACD 히스토그램 3일 연속 개선 — 하락 모멘텀 둔화")
    elif len(hh) == 4 and hh[-1] > 0 and all(hh[i] < hh[i - 1] for i in range(1, 4)):
        add("macd_hist_fade", "모멘텀", -1, 1, "MACD 히스토그램 3일 연속 축소 — 상승 모멘텀 둔화")

    # ── ADX ──
    adx, pdi, mdi, atr = adx_series(h, l, c, 14)
    a_now, p_now, m_now, atr_now = _f(adx.iloc[-1]), _f(pdi.iloc[-1]), _f(mdi.iloc[-1]), _f(atr.iloc[-1])
    if a_now is not None:
        if a_now >= 25:
            up = (p_now or 0) > (m_now or 0)
            add("adx_trend", "추세", 1 if up else -1, 2,
                f"ADX {a_now:.0f} — 강한 {'상승' if up else '하락'} 추세(+DI {p_now:.0f} / −DI {m_now:.0f})")
        elif a_now < 20:
            add("adx_range", "추세", 0, 1, f"ADX {a_now:.0f} — 비추세(박스권), 오실레이터(RSI·볼린저) 신호 신뢰도 상대적으로 높음")

    # ── 볼린저 ──
    bn, bk = cfg["bb"]
    _, bb_u, bb_l, pctb, bw = bollinger(c, bn, bk)
    pb = _f(pctb.iloc[-1])
    bw_hist = bw.iloc[-120:].dropna()
    bw_pct = _f((bw_hist < bw_hist.iloc[-1]).mean() * 100) if len(bw_hist) > 30 else None
    vol_ma = v.iloc[-21:-1].mean()
    vratio = _f(v.iloc[-1] / vol_ma) if vol_ma and vol_ma > 0 else None
    breakout_regime = (a_now or 0) >= 25 and (p_now or 0) > (m_now or 0)
    if pb is not None:
        recent_pb = pctb.iloc[-4:-1]
        if (recent_pb < 0).any() and pb > 0:
            add("bb_reclaim", "변동성", 1, 2, "볼린저 하단 이탈 후 밴드 안 재진입 — 과매도 반등 신호")
        elif pb <= 0.05:
            add("bb_lower", "변동성", 1 if (a_now or 30) < 25 else 0, 1, f"볼린저 하단 접근(%B {pb:.2f})")
        elif pb >= 1:
            if breakout_regime or (vratio or 0) >= 1.5:
                add("bb_break_up", "변동성", 1, 2, f"볼린저 상단 돌파(%B {pb:.2f}) — 추세 가속(밴드 워크)")
            else:
                add("bb_upper_hot", "변동성", -1, 1, f"볼린저 상단 이탈(%B {pb:.2f}) — 추세 약한 상태의 과열")
    if bw_pct is not None and bw_pct <= 10:
        add("bb_squeeze", "변동성", 0, 2, f"볼린저 밴드폭 6개월 하위 {bw_pct:.0f}% — 변동성 수축(스퀴즈), 큰 움직임 임박 가능")

    # ── 거래량 ──
    chg1 = (last / c.iloc[-2] - 1) * 100
    updown = None
    if v.iloc[-20:].sum() > 0:
        if vratio is not None and vratio >= 2:
            if chg1 > 0:
                add("vol_up", "거래량", 1, 2, f"거래량 20일 평균의 {vratio:.1f}배 동반 상승 — 매수세 유입")
            elif chg1 < 0:
                add("vol_down", "거래량", -1, 2, f"거래량 20일 평균의 {vratio:.1f}배 동반 하락 — 매도 압력")
        dc = c.diff().iloc[-20:]
        uv, dv = v.iloc[-20:][dc > 0].sum(), v.iloc[-20:][dc < 0].sum()
        if dv > 0:
            updown = uv / dv
            if updown >= 1.5:
                add("vol_accum", "거래량", 1, 1, f"최근 20일 상승일 거래량이 하락일의 {updown:.1f}배 — 매집 우위")
            elif updown <= 0.67:
                add("vol_distrib", "거래량", -1, 1, f"최근 20일 하락일 거래량이 상승일의 {1 / updown:.1f}배 — 매도(분산) 우위")

    # ── 캔들 ──
    body = (c - o).abs()
    rng = (h - l).replace(0, np.nan)
    ob, cb, ol, oh = o.iloc[-1], c.iloc[-1], l.iloc[-1], h.iloc[-1]
    b_now = abs(cb - ob)
    lower_sh, upper_sh = min(ob, cb) - ol, oh - max(ob, cb)
    fell = c.iloc[-2] < c.iloc[-7] if n > 7 else False  # 캔들 직전 5봉 추세
    rose = c.iloc[-2] > c.iloc[-7] if n > 7 else False
    avg_body = body.iloc[-21:-1].mean()
    candle = None
    if b_now > 0 and lower_sh >= 2 * b_now and upper_sh <= b_now and fell:
        candle = "hammer"
        add("candle_hammer", "캔들", 1, 2, "망치형 캔들 — 하락 후 긴 아래꼬리, 저가 매수세 유입")
    elif n > 2:
        po, pc = o.iloc[-2], c.iloc[-2]
        if pc < po and cb > ob and cb >= po and ob <= pc and fell:
            candle = "bull_engulf"
            add("candle_bull_engulf", "캔들", 1, 2, "상승장악형 — 전일 음봉 몸통을 양봉이 감싸며 반전")
        elif pc > po and cb < ob and cb <= po and ob >= pc and rose:
            candle = "bear_engulf"
            add("candle_bear_engulf", "캔들", -1, 2, "하락장악형 — 전일 양봉 몸통을 음봉이 감싸며 반전")
    if candle is None and avg_body and b_now >= 2 * avg_body and rng.iloc[-1] and b_now / rng.iloc[-1] >= 0.6:
        bull = cb > ob
        add("candle_long_bull" if bull else "candle_long_bear", "캔들", 1 if bull else -1, 1,
            f"{'장대양봉' if bull else '장대음봉'} — 몸통이 20일 평균의 {b_now / avg_body:.1f}배")

    # ── 52주 고·저 ──
    hi52 = float(h.iloc[-253:-1].max()) if n > 30 else None
    lo52 = float(l.iloc[-253:-1].min()) if n > 30 else None
    new_high = hi52 is not None and last >= hi52
    if new_high:
        add("high52_new", "가격대", 1, 2, "52주 신고가 경신 — 매물 부담 없는 구간, 추세 추종 신호")
    elif hi52 and last >= hi52 * 0.97:
        add("high52_near", "가격대", 0, 1, f"52주 고점({_r(hi52):,}) 대비 {(last / hi52 - 1) * 100:.1f}% — 고점 저항 테스트")
    if lo52 is not None and last <= lo52:
        add("low52_new", "가격대", -1, 2, "52주 신저가 — 하락 추세 진행")

    # ── 지지·저항·손절 참고 ──
    swing_lo = [l.values[-120:][i] for i in pivots(l.values[-120:], 5, "low")]
    swing_hi = [h.values[-120:][i] for i in pivots(h.values[-120:], 5, "high")]
    sup_c = [(x, "스윙 저점") for x in swing_lo if x < last] + [(ma[m], f"{m}일선") for m in have if ma[m] < last]
    res_c = [(x, "스윙 고점") for x in swing_hi if x > last] + [(ma[m], f"{m}일선") for m in have if ma[m] > last]
    if hi52 and hi52 > last:
        res_c.append((hi52, "52주 고점"))

    def nearest(cands, below):
        cands = sorted(cands, key=lambda t: -t[0] if below else t[0])
        out = []
        for px, lab in cands:
            if out and abs(px / out[-1]["price"] - 1) < 0.015:  # 1.5% 이내는 하나로
                out[-1]["label"] += f"·{lab}" if lab not in out[-1]["label"] else ""
                continue
            out.append({"price": _r(px), "label": lab, "dist": round(float((px / last - 1) * 100), 1)})
            if len(out) == 2:
                break
        return out

    supports, resistances = nearest(sup_c, True), nearest(res_c, False)
    atr_pct = (atr_now / last * 100) if atr_now else None
    stop = None
    if atr_now:
        base = supports[0]["price"] - 0.5 * atr_now if supports else None
        if base is None or (last - base) / last > 0.12:
            base = last - 2 * atr_now
        stop = {"price": _r(base), "dist": round(float((base / last - 1) * 100), 1),
                "basis": "지지선−0.5ATR" if supports and (last - (supports[0]["price"] - 0.5 * atr_now)) / last <= 0.12 else "2ATR"}
    rr = None
    if stop and resistances and last > stop["price"]:
        rr = round(float((resistances[0]["price"] - last) / (last - stop["price"])), 2)

    # ── 점수 ──
    trend = 50.0
    if trend_label == "완전 정배열":
        trend += 25
    elif trend_label == "완전 역배열":
        trend -= 25
    elif trend_label == "장기 정배열 속 단기 조정":
        trend += 12
    elif trend_label == "단기 정배열 전환":
        trend += 5
    else:
        trend += (ordered - reversed_) / max(pairs, 1) * 10
    if ma.get(s_long):
        trend += 8 if last > ma[s_long] else -8
    if sl_long is not None:
        trend += max(-8, min(8, sl_long * 2))
    if sl_mid is not None:
        trend += max(-5, min(5, sl_mid * 1.5))
    if a_now is not None and a_now >= 25:
        trend += 5 if (p_now or 0) > (m_now or 0) else -5
    for cr in crosses:
        if cr["pair"] in ([s_mid, s_long], ["price", s_long]):
            trend += 5 if cr["kind"] == "golden" else -5

    mom = 50.0
    if r_now is not None:
        mom += 10 if 50 <= r_now < 65 else 5 if 65 <= r_now < 70 else -5 if r_now >= 70 else -3 if r_now >= 40 else -10
    if mv_ is not None and sv_ is not None:
        mom += 8 if mv_ > sv_ else -8
        mom += 6 if mv_ > 0 else -6
    codes = {s["code"] for s in sig}
    mom += 5 * ("macd_hist_improve" in codes) - 5 * ("macd_hist_fade" in codes)
    mom += 6 * ("vol_accum" in codes) - 6 * ("vol_distrib" in codes)
    mom += 8 * new_high + 4 * ("high52_near" in codes) - 10 * ("low52_new" in codes)
    mom += 6 * ("vol_up" in codes) - 6 * ("vol_down" in codes)

    tim = 50.0
    if r_now is not None:
        tim += 15 if r_now <= 30 else 8 if r_now <= 40 else -15 if r_now >= 70 else -5 if r_now >= 60 else 0
    tim += 12 * ("rsi_reclaim30" in codes) + 15 * (div == "bull") - 15 * (div == "bear")
    tim += 10 * ("bb_reclaim" in codes) + 5 * ("bb_lower" in codes) - 8 * ("bb_upper_hot" in codes)
    tim += 8 * ("disp_cold" in codes) - 10 * ("disp_hot" in codes) - 5 * ("rsi_lose70" in codes)
    tim += 12 * (tested is not None and tested[0] == "support") - 10 * (tested is not None and tested[0] == "resist")
    tim += 8 * (candle in ("hammer", "bull_engulf")) - 8 * (candle == "bear_engulf") - 6 * ("vol_down" in codes)
    tim += 5 * ("macd_golden" in codes) - 5 * ("macd_dead" in codes)
    knife = trend_label == "완전 역배열" and (sl_long or 0) < 0 and div != "bull" and "rsi_reclaim30" not in codes
    if knife:
        tim -= 12
        add("falling_knife", "추세", -1, 2, "역배열 + 장기선 하락 중 반전 신호 없음 — '떨어지는 칼날' 주의")

    clip = lambda x: round(float(max(0.0, min(100.0, x))), 1)  # noqa: E731
    scores = {"trend": clip(trend), "momentum": clip(mom), "timing": clip(tim)}
    scores["total"] = clip(0.4 * scores["trend"] + 0.3 * scores["momentum"] + 0.3 * scores["timing"])

    # ── 셋업 분류 ──
    reversal_sig = div == "bull" or "rsi_reclaim30" in codes or "bb_reclaim" in codes or \
        candle in ("hammer", "bull_engulf") or ("macd_golden" in codes and (mv_ or 0) < 0)
    # 눌림 증거: RSI 45 이하 / 20일선 아래·근접(+1% 이내) / 이평선 지지 확인
    dip = (r_now or 50) <= 45 or dist.get(s_short, 99) <= 1.0 or bool(tested and tested[0] == "support")
    long_turn = any(cr["kind"] == "golden" and cr["pair"] in ([s_mid, s_long], ["price", s_long]) for cr in crosses)
    if (new_high or "bb_break_up" in codes) and scores["trend"] >= 60:
        setup = "breakout"
    elif long_up and dip and trend_label in ("완전 정배열", "장기 정배열 속 단기 조정", "혼조(수렴)"):
        setup = "pullback"
    elif long_turn:
        setup = "turn"
    elif reversal_sig and (not ma.get(s_mid2) or last < ma[s_mid2] or trend_label == "완전 역배열"):
        setup = "reversal"
    elif "bb_squeeze" in codes:
        setup = "squeeze"
    elif trend_label == "완전 정배열":
        setup = "uptrend"
    elif trend_label == "완전 역배열" or (ma.get(s_long) and last < ma[s_long] and (sl_long or 0) < -1):
        setup = "downtrend"
    elif a_now is not None and a_now < 20:
        setup = "range"
    else:
        setup = "neutral"
    lab, desc = SETUPS[setup]

    tags = [setup]
    if trend_label == "완전 정배열":
        tags.append("aligned")
    if (r_now is not None and r_now <= 30) or "rsi_reclaim30" in codes:
        tags.append("oversold")
    if any(cr["kind"] == "golden" for cr in crosses):
        tags.append("golden")
    if "bb_squeeze" in codes:
        tags.append("squeeze")
    if div == "bull":
        tags.append("div_bull")
    tags = list(dict.fromkeys(tags))

    sig.sort(key=lambda s: (-abs(s["pol"]) if s["pol"] else 0, -s["w"]))
    return {
        "setup": setup, "setup_label": lab, "setup_desc": desc, "trend_label": trend_label,
        "scores": scores, "signals": sig, "tags": tags,
        "levels": {"support": supports, "resistance": resistances, "stop": stop, "rr": rr},
        "crosses": crosses,
        "values": {
            "ma": {str(m): _r(ma[m]) for m in mas}, "dist": {str(m): _f(dist.get(m), 1) for m in mas},
            "slope_long": _f(sl_long, 2), "slope_mid": _f(sl_mid, 2), "slope_mid2": _f(sl_mid2, 2),
            "disparity": _f(disp, 1), "rsi": _f(r_now, 1), "macd": _r(mv_), "macd_signal": _r(sv_),
            "macd_hist": _r(hv_), "adx": _f(a_now, 1), "pdi": _f(p_now, 1), "mdi": _f(m_now, 1),
            "pctb": _f(pb, 2), "bw_pct": _f(bw_pct, 0), "vol_ratio": _f(vratio, 2), "updown_vol": _f(updown, 2),
            "atr_pct": _f(atr_pct, 2), "hi52": _r(hi52), "lo52": _r(lo52),
        },
    }


def chart_series(df: pd.DataFrame, cfg: dict | None = None) -> dict:
    """앱 상세 차트용 시계열 (최근 chart_days 봉)."""
    cfg = {**DEFAULT, **(cfg or {})}
    k = cfg["chart_days"]
    c = df["Close"].astype(float)
    o = df["Open"].astype(float).fillna(c)
    h = df["High"].astype(float).fillna(c)
    l = df["Low"].astype(float).fillna(c)
    v = df["Volume"].astype(float).fillna(0) if "Volume" in df else pd.Series(0.0, index=c.index)
    _, bu, bl, _, _ = bollinger(c, *cfg["bb"])
    m, s, hist = macd_series(c, *cfg["macd"])
    rs = rsi_series(c, cfg["rsi"])
    tail = lambda x: [_r(t) for t in x.iloc[-k:].values]  # noqa: E731
    return {
        "d": [d.strftime("%Y-%m-%d") for d in c.index[-k:]],
        "o": tail(o), "h": tail(h), "l": tail(l), "c": tail(c), "v": [int(x) for x in v.iloc[-k:].values],
        "ma": {str(n): tail(sma(c, n)) for n in cfg["ma"]},
        "bb": {"u": tail(bu), "l": tail(bl)},
        "rsi": [_f(x, 1) for x in rs.iloc[-k:].values],
        "macd": {"m": tail(m), "s": tail(s), "h": tail(hist)},
    }
