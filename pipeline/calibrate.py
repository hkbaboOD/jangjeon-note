"""앱 자가 보정 — 매주 '예측 → 채점 → 보정' 루프로 추천 가중치를 조금씩 개선.

코드는 바꾸지 않고 web/data/model.json 의 숫자만 바꿉니다. 모든 변경은 사유와 함께 기록됩니다.

근거
1) 백테스트(과거 10년, 차트 팩터만) — 첫날부터 사용
2) 실전 채점(앱이 매일 냈던 예측 vs 실제 수익률, 펀더멘털 포함 전 팩터) — 쌓일수록 비중이 커짐

절차 (기간별: 오늘=5일, 이번 주=20일, 이번 달=60일 수익률 기준)
- 실전 기록을 '학습 구간'과 '최근 검증 구간(홀드아웃)'으로 나눔
- 학습 구간 근거로 새 가중치 후보를 만듦 (유의한 IC t≥2만 사용, 기본값과 50:50 축소)
- 현재 모델 · 기본 모델 · 새 후보를 검증 구간에서 겨룸 → 가장 높은 IC가 현재보다 0.005 이상 좋을 때만 교체
  (기본 모델이 이기면 = 자동 롤백)
- 실전 기록이 부족한 동안은 백테스트 근거로 차트 비중만 조정(기존 방식)

안전장치: 주당 각 가중치 ±0.05 이동 / 단일 팩터 최대 45% / 가치 10%·퀄리티 8% 최소 유지 /
          셋업 가감점 ±2점씩 최대 ±5점 / 유의하지 않은 결과 무시
"""
from __future__ import annotations

import copy
import math

import pandas as pd

from .common import now_kst
from .forward import composite, date_ics, ic_stat
from .technical import SETUPS
from .valuation import HORIZON_WEIGHTS

CHART = ("trend", "momentum", "timing")
FACTORS = ("value", "quality", "target", "growth", "momentum", "trend", "timing", "revision", "geo")
HZ = {"daily": "5", "weekly": "20", "monthly": "60"}
NAME = {"trend": "추세", "momentum": "모멘텀", "timing": "타이밍", "value": "가치", "quality": "퀄리티",
        "target": "상승여력", "growth": "성장", "revision": "이익 추정치 변화", "geo": "국제 정세"}
H_NAME = {"daily": "오늘", "weekly": "이번 주", "monthly": "이번 달"}
MAX_STEP, MAX_ADJ_STEP, ADJ_CAP = 0.05, 2.0, 5.0
T_MIN = 2.0
BLOCK_RANGE = (0.7, 1.3)
FLOORS = {"value": 0.10, "quality": 0.08}
CAP = 0.45
MARGIN = 0.005  # 교체에 필요한 최소 IC 개선폭
WIN_GUARD = 5.0  # 교체 후보의 검증구간 승률이 현재보다 5%p 넘게 낮으면 교체 금지


def _clip(x, lo, hi):
    return max(lo, min(hi, x))


def _norm(w: dict) -> dict:
    s = sum(v for v in w.values() if v > 0) or 1
    return {k: v / s for k, v in w.items() if v > 0}


def _bound(w: dict) -> dict:
    """합=1을 유지하면서 단일 팩터 ≤CAP, 가치·퀄리티 ≥FLOORS를 동시에 만족하도록 초과분을 나머지에 재분배."""
    w = _norm(w)
    fixed: dict = {}
    for _ in range(20):
        add = {k: CAP for k, v in w.items() if k not in fixed and v > CAP + 1e-12}
        add.update({k: fl for k, fl in FLOORS.items() if k not in fixed and w.get(k, 0) < fl - 1e-12})
        if not add:
            break
        fixed.update(add)
        free = {k: v for k, v in w.items() if k not in fixed}
        rem = 1 - sum(fixed.values())
        fs = sum(free.values()) or 1
        w = {**fixed, **{k: v / fs * rem for k, v in free.items()}}
    return w


def _chart_proposal(base: dict, bt: dict, hz: str) -> tuple[dict, list[str]]:
    """실전 기록이 부족할 때: 백테스트 근거로 차트 블록 비중·배분만 조정."""
    why = []
    block_def = sum(base.get(k, 0) for k in CHART)
    if block_def <= 0 or not bt.get("ok"):
        return dict(base), why
    tot = (bt.get("ic", {}).get("total") or {}).get(hz)
    scale = 1.0
    if tot:
        if tot["ic"] > 0 and tot["t"] >= 3:
            scale = _clip(1 + tot["ic"] * 5, 1.0, BLOCK_RANGE[1])
            why.append(f"백테스트 차트 종합 IC {tot['ic']:+.3f}(t {tot['t']:.1f}) 유효 → 차트 비중 확대")
        elif tot["ic"] <= 0 or tot["t"] < 1:
            scale = BLOCK_RANGE[0] if tot["ic"] <= 0 else 0.85
            why.append(f"백테스트 차트 종합 IC {tot['ic']:+.3f}(t {tot['t']:.1f}) 약함 → 차트 비중 축소")
    block = block_def * scale
    default_split = {k: base.get(k, 0) / block_def for k in CHART}
    pos = {k: v["ic"] for k in CHART if (v := (bt.get("ic", {}).get(k) or {}).get(hz)) and v["ic"] > 0 and v["t"] >= T_MIN}
    if pos:
        s = sum(pos.values())
        split = {k: 0.5 * default_split[k] + 0.5 * pos.get(k, 0) / s for k in CHART}
        why.append("차트 내 배분: " + ", ".join(f"{NAME[k]} IC {v:+.3f}" for k, v in pos.items()))
    else:
        split = default_split
    fund = [k for k in base if k not in CHART]
    fund_def = sum(base[k] for k in fund)
    tgt = {k: (1 - block) * base[k] / fund_def for k in fund}
    tgt.update({k: block * split[k] for k in CHART})
    return tgt, why


def _evidence(bt: dict, train: pd.DataFrame | None, h: int) -> dict:
    """팩터별 근거 IC = 백테스트(차트 팩터) + 실전 학습구간, 유효표본 가중 결합."""
    hz = str(h)
    out = {}
    for f in FACTORS:
        parts = []
        b = (bt.get("ic", {}).get(f) or {}).get(hz) if bt.get("ok") else None
        if b:
            parts.append((b["ic"], b["t"], b["dates"] * min(1.0, 5 / h)))
        if train is not None and not train.empty:
            lv = ic_stat(date_ics(train, train[f], h), h)
            if lv:
                parts.append((lv["ic"], lv["t"], lv["n_eff"]))
        if not parts:
            continue
        n = sum(p[2] for p in parts)
        ic = sum(p[0] * p[2] for p in parts) / n
        t = sum(p[1] * math.sqrt(p[2]) for p in parts) / math.sqrt(n)  # 가중 Stouffer
        out[f] = {"ic": round(ic, 4), "t": round(t, 2), "n_eff": round(n, 1), "src": "+".join(
            s for s, ok in (("백테스트", bool(b)), ("실전", len(parts) > (1 if b else 0))) if ok)}
    return out


def _full_proposal(base: dict, ev: dict) -> tuple[dict, list[str]]:
    pos = {f: e["ic"] for f, e in ev.items() if e["ic"] > 0 and e["t"] >= T_MIN}
    neg = [f for f, e in ev.items() if e["ic"] < 0 and e["t"] <= -T_MIN]
    if not pos and not neg:
        return dict(base), []
    keys = set(base) | set(pos)
    s = sum(pos.values())
    tgt = {k: 0.5 * base.get(k, 0) + (0.5 * pos.get(k, 0) / s if s else 0.5 * base.get(k, 0)) for k in keys}
    for k in neg:
        tgt[k] = base.get(k, 0) * 0.5
    tgt = _bound(tgt)
    why = []
    if pos:
        why.append("유효: " + ", ".join(f"{NAME[k]} IC {v:+.3f}" for k, v in sorted(pos.items(), key=lambda x: -x[1])))
    if neg:
        why.append("역효과: " + ", ".join(NAME[k] for k in neg))
    return tgt, why


def _holdout_ic(hold: pd.DataFrame, w: dict, h: int) -> float | None:
    st = ic_stat(date_ics(hold, composite(hold, w), h), h)
    return st["ic"] if st else None


def _holdout_win(hold: pd.DataFrame, w: dict, h: int) -> float | None:
    """검증 구간에서 날짜·시장별 Top3로 뽑았을 때 초과수익 > 0 비율(%) = 추천 승률."""
    d = hold.assign(_s=composite(hold, w)).dropna(subset=[f"x{h}"])
    if d.empty:
        return None
    top = d.sort_values("_s", ascending=False).groupby(["date", "market"]).head(3)
    return round(float((top[f"x{h}"] > 0).mean() * 100), 1) if len(top) >= 15 else None


def _step(prev: dict, target: dict) -> dict:
    pw = _norm(prev)
    tg = _norm(target)
    keys = set(pw) | set(tg)
    delta = {k: tg.get(k, 0.0) - pw.get(k, 0.0) for k in keys}
    alpha = min(1.0, MAX_STEP / max(1e-9, max(abs(d) for d in delta.values())))
    new = {k: pw.get(k, 0.0) + alpha * delta[k] for k in keys}
    return {k: round(v, 3) for k, v in new.items() if v >= 0.005}


def _live_by_setup(track: dict | None) -> dict:
    lab2code = {v[0]: k for k, v in SETUPS.items()}
    return {lab2code[lab]: v for lab, v in ((track or {}).get("by_setup") or {}).items()
            if lab in lab2code and v.get("avg_ex") is not None}


def calibrate(bt: dict, track: dict | None, prev: dict | None, live: pd.DataFrame | None = None) -> dict:
    """live: forward.attach_returns() 결과(예측 기록 + 실제 수익률). 없거나 부족하면 백테스트만 사용."""
    prev = prev or {}
    defaults = HORIZON_WEIGHTS
    prev_w = copy.deepcopy(prev.get("weights") or defaults)
    prev_adj = copy.deepcopy(prev.get("setup_adj") or {})
    today = now_kst().strftime("%Y-%m-%d")
    changes, basis, contest, evidence = [], {}, {}, {}
    weights, setup_adj = {}, {}
    live_setup = _live_by_setup(track)

    for hname, base in defaults.items():
        h = int(HZ[hname])
        cur = prev_w.get(hname, base)
        mode, train, hold = "backtest", None, None
        if live is not None and not live.empty and f"r{h}" in live:
            matured = sorted(live.dropna(subset=[f"r{h}"])["date"].unique())
            n_hold = max(20, h)
            if len(matured) >= 2 * n_hold:  # 학습·검증 구간 모두 확보
                hold_dates = set(matured[-n_hold:])
                hold = live[live["date"].isin(hold_dates)]
                train = live[live["date"].isin(set(matured[:-n_hold]))]
                mode = "live"
        ev = _evidence(bt, train, h)
        evidence[hname] = ev
        if mode == "live":
            prop, why = _full_proposal(base, ev)
            cands = {"현재": cur, "기본": base, "새 후보": prop}
            scores = {k: _holdout_ic(hold, w, h) for k, w in cands.items()}
            wins = {k: _holdout_win(hold, w, h) for k, w in cands.items()}
            cur_ic = scores["현재"] if scores["현재"] is not None else -1
            ok = [k for k in scores if scores[k] is not None and
                  (wins[k] is None or wins["현재"] is None or wins[k] >= wins["현재"] - WIN_GUARD)]
            best = max(ok, key=lambda k: scores[k], default="현재")
            chosen = best if best != "현재" and scores[best] - cur_ic >= MARGIN else "현재"
            target = cands[chosen]
            contest[hname] = {"scores": {k: (round(v, 4) if v is not None else None) for k, v in scores.items()},
                              "wins": wins, "chosen": chosen, "hold_dates": int(hold["date"].nunique()),
                              "train_dates": int(train["date"].nunique())}
            if chosen == "기본":
                why = ["최근 검증 구간에서 기본 모델이 더 정확 → 자동 롤백"] + why
            elif chosen == "현재":
                why = ["새 후보가 최근 검증 구간에서 현재보다 낫지 않음 → 유지"] + why
            else:
                why = [f"새 후보가 최근 {contest[hname]['hold_dates']}일 검증에서 IC {scores['새 후보']:+.3f} (현재 {cur_ic:+.3f}) → 채택"] + why
        else:
            target, why = _chart_proposal(base, bt, str(h))
        basis[hname] = why
        new = _step(cur, target)
        moved = [(k, cur.get(k, 0), new.get(k, 0)) for k in sorted(set(new) | set(cur)) if abs(new.get(k, 0) - cur.get(k, 0)) >= 0.01]
        if moved:
            changes.append({"date": today, "text": f"[{H_NAME[hname]}] " + ", ".join(f"{NAME.get(k, k)} {a:.2f}→{b:.2f}" for k, a, b in moved)
                            + (f" — {why[0]}" if why else "")})
        weights[hname] = new

        # 셋업 가감점: 백테스트 초과수익(유의할 때만) + 실제 추천 성과(30건 이상이면 혼합)
        adj_h = {}
        for setup, blk in (bt.get("by_setup") or {}).items():
            st = ((blk.get("all") or {}).get(str(h)) or {}).get("ex")
            target_adj = 0.0
            if st and st["n_eff"] >= 30 and abs(st["t"]) >= 2:
                avg = st["avg"]
                lv = live_setup.get(setup)
                if lv and lv.get("count", 0) >= 30:
                    wl = lv["count"] / (lv["count"] + 100)
                    avg = (1 - wl) * avg + wl * lv["avg_ex"]
                target_adj = _clip(round(2 * avg, 1), -ADJ_CAP, ADJ_CAP)
            p = prev_adj.get(hname, {}).get(setup, 0.0)
            v = round(p + _clip(target_adj - p, -MAX_ADJ_STEP, MAX_ADJ_STEP), 1)
            if v:
                adj_h[setup] = v
            if abs(v - p) >= 0.5:
                changes.append({"date": today, "text": f"[{H_NAME[hname]}] '{SETUPS[setup][0]}' 가감점 {p:+.1f}→{v:+.1f}"
                                + (f" (백테스트 {h}일 초과수익 {st['avg']:+.2f}%, t {st['t']:.1f})" if st else "")})
        setup_adj[hname] = adj_h

    log = (changes + (prev.get("log") or []))[:80]
    version = (prev.get("version") or 0) + (1 if changes else 0)
    history = (prev.get("history") or [])
    if changes:
        history = (history + [{"version": version, "date": today, "weights": weights}])[-30:]
    return {
        "updated": now_kst().isoformat(timespec="minutes"), "version": version, "weights": weights,
        "setup_adj": setup_adj, "defaults": defaults, "basis": basis, "log": log, "changed": bool(changes),
        "changes_now": [c["text"] for c in changes],
        "contest": contest, "evidence": evidence, "history": history, "backtest_period": bt.get("period"),
        "rules": ("예측 기록을 학습·검증 구간으로 나눠 현재·기본·새 후보를 검증 구간 IC로 비교, 0.005 이상 좋을 때만 교체(기본이 이기면 롤백), 추천 승률이 5%p 넘게 떨어지는 교체는 금지 / "
                  "실전 기록이 부족하면 백테스트로 차트 비중만 조정 / 근거는 IC t≥2만 / 기본값과 50:50 축소 / "
                  "주당 가중치 ±0.05 / 단일 팩터 ≤45%, 가치 ≥10%·퀄리티 ≥8% / 셋업 가감점 주당 ±2점·최대 ±5점"),
    }
