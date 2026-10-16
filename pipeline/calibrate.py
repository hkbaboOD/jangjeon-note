"""앱 자가 보정 — 백테스트와 실제 추천 성과로 '차트 가중치'와 '셋업 가감점'을 매주 조금씩 조정.

코드는 바꾸지 않고 web/data/model.json 의 숫자만 바꿉니다. 모든 변경은 사유와 함께 기록됩니다.

안전장치
- 조정 대상은 차트 관련 가중치(추세·모멘텀·타이밍)와 셋업 가감점뿐. 펀더멘털 비율은 고정(백테스트 불가)
- 축소 추정: 목표치와 기본값을 50:50으로 섞음 → 한 번의 결과에 과민 반응 방지
- 차트 블록 전체 비중은 기본값의 70~130% 범위
- 한 번(주 1회)에 각 가중치는 최대 ±0.05, 셋업 가감점은 최대 ±2점만 이동
- 유의하지 않은 결과(t < 2, 유효표본 < 30)는 무시
"""
from __future__ import annotations

import copy

from .common import now_kst
from .technical import SETUPS
from .valuation import HORIZON_WEIGHTS

CHART = ("trend", "momentum", "timing")
HZ = {"daily": "5", "weekly": "20", "monthly": "60"}
NAME = {"trend": "추세", "momentum": "모멘텀", "timing": "타이밍", "value": "가치", "quality": "퀄리티",
        "target": "상승여력", "growth": "성장"}
H_NAME = {"daily": "오늘", "weekly": "이번 주", "monthly": "이번 달"}
MAX_STEP, MAX_ADJ_STEP, ADJ_CAP = 0.05, 2.0, 5.0
T_MIN = 2.0  # 12개 검정(4팩터×3기간)에서 우연한 신호를 거르기 위한 최소 t값
BLOCK_RANGE = (0.7, 1.3)


def _clip(x, lo, hi):
    return max(lo, min(hi, x))


def _target_weights(base: dict, bt: dict, hz: str) -> tuple[dict, list[str]]:
    why = []
    block_def = sum(base.get(k, 0) for k in CHART)
    if block_def <= 0:
        return dict(base), why
    tot = (bt.get("ic", {}).get("total") or {}).get(hz)
    scale = 1.0
    if tot:
        if tot["ic"] > 0 and tot["t"] >= 3:
            scale = _clip(1 + tot["ic"] * 5, 1.0, BLOCK_RANGE[1])
            why.append(f"차트 종합 IC {tot['ic']:+.3f}(t {tot['t']:.1f}) 유효 → 차트 비중 확대")
        elif tot["ic"] <= 0 or tot["t"] < 1:
            scale = BLOCK_RANGE[0] if tot["ic"] <= 0 else 0.85
            why.append(f"차트 종합 IC {tot['ic']:+.3f}(t {tot['t']:.1f}) 약함 → 차트 비중 축소")
    block = block_def * scale

    default_split = {k: base.get(k, 0) / block_def for k in CHART}
    pos = {}
    for k in CHART:
        v = (bt.get("ic", {}).get(k) or {}).get(hz)
        if v and v["ic"] > 0 and v["t"] >= T_MIN:
            pos[k] = v["ic"]
    if pos:
        s = sum(pos.values())
        split = {k: 0.5 * default_split[k] + 0.5 * pos.get(k, 0) / s for k in CHART}
        why.append("블록 내 배분: " + ", ".join(f"{NAME[k]} IC {v:+.3f}" for k, v in pos.items()))
    else:
        split = default_split
    fund = [k for k in base if k not in CHART]
    fund_def = sum(base[k] for k in fund)
    tgt = {k: (1 - block) * base[k] / fund_def for k in fund}
    tgt.update({k: block * split[k] for k in CHART})
    return tgt, why


def _live_by_setup(track: dict | None) -> dict:
    """실제 추천 성과(지수 대비 초과수익) — 셋업 라벨 → 코드."""
    lab2code = {v[0]: k for k, v in SETUPS.items()}
    out = {}
    for lab, v in ((track or {}).get("by_setup") or {}).items():
        if lab in lab2code and v.get("avg_ex") is not None:
            out[lab2code[lab]] = v
    return out


def calibrate(bt: dict, track: dict | None, prev: dict | None) -> dict:
    prev = prev or {}
    defaults = HORIZON_WEIGHTS
    prev_w = copy.deepcopy(prev.get("weights") or defaults)
    prev_adj = copy.deepcopy(prev.get("setup_adj") or {})
    today = now_kst().strftime("%Y-%m-%d")
    changes, basis = [], {}
    weights, setup_adj = {}, {}
    live = _live_by_setup(track)

    for h, base in defaults.items():
        hz = HZ[h]
        tgt, why = _target_weights(base, bt, hz) if bt.get("ok") else (dict(base), [])
        basis[h] = why
        pw = prev_w.get(h, base)
        ps = sum(pw.values()) or 1
        pw_n = {k: v / ps for k, v in pw.items()}
        ts = sum(tgt.values()) or 1
        keys = set(tgt) | set(pw_n)
        delta = {k: tgt.get(k, 0.0) / ts - pw_n.get(k, 0.0) for k in keys}
        # 이동 벡터 전체를 같은 비율로 줄여 '합=1'과 '각 ±MAX_STEP'을 동시에 보장
        alpha = min(1.0, MAX_STEP / max(1e-9, max(abs(d) for d in delta.values())))
        new = {k: pw_n.get(k, 0.0) + alpha * delta[k] for k in keys}
        new = {k: round(v, 3) for k, v in new.items() if v >= 0.005}
        moved = [(k, pw.get(k, 0), new.get(k, 0)) for k in sorted(set(new) | set(pw)) if abs(new.get(k, 0) - pw.get(k, 0)) >= 0.01]
        if moved:
            changes.append({"date": today, "text": f"[{H_NAME[h]}] " + ", ".join(f"{NAME.get(k, k)} {a:.2f}→{b:.2f}" for k, a, b in moved)
                            + (f" — {why[0]}" if why else "")})
        weights[h] = new

        # 셋업 가감점: 백테스트 초과수익(유의할 때만) + 실제 추천 성과(30건 이상이면 혼합)
        adj_h = {}
        for setup, blk in (bt.get("by_setup") or {}).items():
            st = ((blk.get("all") or {}).get(hz) or {}).get("ex")
            target = 0.0
            if st and st["n_eff"] >= 30 and abs(st["t"]) >= 2:
                avg = st["avg"]
                lv = live.get(setup)
                if lv and lv.get("count", 0) >= 30:
                    w = lv["count"] / (lv["count"] + 100)
                    avg = (1 - w) * avg + w * lv["avg_ex"]
                target = _clip(round(2 * avg, 1), -ADJ_CAP, ADJ_CAP)  # 초과수익 0.5%p ≈ 1점
            p = prev_adj.get(h, {}).get(setup, 0.0)
            v = round(p + _clip(target - p, -MAX_ADJ_STEP, MAX_ADJ_STEP), 1)
            if v:
                adj_h[setup] = v
            if abs(v - p) >= 0.5:
                changes.append({"date": today, "text": f"[{H_NAME[h]}] '{SETUPS[setup][0]}' 가감점 {p:+.1f}→{v:+.1f}"
                                + (f" (백테스트 {hz}일 초과수익 {st['avg']:+.2f}%, t {st['t']:.1f})" if st else "")})
        setup_adj[h] = adj_h

    log = (changes + (prev.get("log") or []))[:60]
    version = (prev.get("version") or 0) + (1 if changes else 0)
    return {
        "updated": now_kst().isoformat(timespec="minutes"), "version": version, "weights": weights,
        "setup_adj": setup_adj, "defaults": defaults, "basis": basis, "log": log, "changed": bool(changes),
        "backtest_period": bt.get("period"),
        "rules": "차트 가중치·셋업 가감점만 조정 / 기본값과 50:50 축소 / 차트 블록 70~130% / 주당 가중치 ±0.05·가감점 ±2점 / 가중치는 IC t≥2, 가감점은 초과수익 t≥2·유효표본 30 이상일 때만",
    }
