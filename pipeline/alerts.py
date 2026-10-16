"""가격·신호 알림: 규칙 평가 → 중복 제거 → Web Push 발송 → alerts.json 기록.

Web Push(아이폰)는 iOS 16.4+ 에서 '홈 화면에 추가'한 PWA에만 동작합니다.
필요한 GitHub Secrets: VAPID_PRIVATE_KEY, VAPID_PUBLIC_KEY, VAPID_SUBJECT(mailto:...), PUSH_SUBSCRIPTIONS
"""
from __future__ import annotations

import json
from datetime import date
import logging
import os

from .common import STATE_DIR, now_kst, read_json, write_json

log = logging.getLogger(__name__)

# 종류별 재알림 쿨다운(일). 0 = 같은 날만 중복 차단
COOLDOWN = {"div_": 10, "setup_": 5, "bb_reclaim": 3, "high52": 3, "rsi_low": 2, "rsi_high": 2,
            "target": 7, "price_": 1, "move": 0, "cross_": 0, "rsi_reclaim": 0, "verdict": 0}


def evaluate(cfg: dict, watch_rows: list[dict]) -> list[dict]:
    a = cfg["alerts"]
    custom = {}
    for c in a.get("custom_price") or []:
        custom.setdefault(c["ticker"], []).append(c)
    out = []
    for w in watch_rows:
        t, f, name = w.get("tech") or {}, w.get("fund") or {}, w["name"]
        if not t:
            continue
        last, c1 = t.get("last"), t.get("chg_1d")

        def add(kind, msg, level="info"):
            out.append({"ticker": w["ticker"], "name": name, "kind": kind, "level": level, "msg": msg,
                        "price": last, "chg_1d": c1})

        if c1 is not None and abs(c1) >= a["daily_move_pct"]:
            diag = w.get("diag")
            extra = f" · {diag['type']} 추정" if diag and diag["type"] != "판단 보류" else ""
            add("move", f"{name} {c1:+.1f}% 급{'등' if c1 > 0 else '락'}{extra}", "warn" if c1 < 0 else "good")
        r = t.get("rsi14")
        if r is not None and r <= a["rsi_low"]:
            add("rsi_low", f"{name} RSI {r:.0f} 과매도", "good")
        if r is not None and r >= a["rsi_high"]:
            add("rsi_high", f"{name} RSI {r:.0f} 과매수", "warn")
        ta = t.get("ta") or {}
        if a.get("ma_cross"):  # 오늘 발생한 이평 크로스·장기선 돌파/이탈
            for cr in ta.get("crosses", []):
                if cr["ago"] != 0:
                    continue
                g = cr["kind"] == "golden"
                p0, p1 = cr["pair"]
                msg = (f"{name} {p1}일선 {'상향 돌파' if g else '하향 이탈'}" if p0 == "price"
                       else f"{name} {p0}·{p1}일선 {'골든' if g else '데드'}크로스")
                add(f"cross_{p0}_{p1}", msg, "good" if g else "warn")
        sig_codes = {s["code"]: s for s in ta.get("signals", [])}
        chart_events = {"rsi_reclaim30": ("rsi_reclaim", "RSI 30 상향 돌파(과매도 탈출)", "good"),
                        "bb_reclaim": ("bb_reclaim", "볼린저 하단 재진입", "good"),
                        "high52_new": ("high52", "52주 신고가", "good"),
                        "div_bull": ("div_bull", "RSI 상승 다이버전스", "good"),
                        "div_bear": ("div_bear", "RSI 하락 다이버전스", "warn")}
        if a.get("chart_signals", True):
            for code, (kind, label, lvl) in chart_events.items():
                s = sig_codes.get(code)
                if s and ("오늘" in s["text"] or code in ("bb_reclaim", "high52_new", "div_bull", "div_bear")):
                    add(kind, f"{name} {label}", lvl)
            if ta.get("setup") in ("pullback", "reversal", "breakout", "turn"):
                add(f"setup_{ta['setup']}", f"{name} 차트 셋업: {ta['setup_label']}", "info")
        if a.get("target_reach") and f.get("target_mean") and last and last >= f["target_mean"]:
            add("target", f"{name} 평균 목표가({f['target_mean']:,.0f}) 도달", "info")
        for c in custom.get(w["ticker"], []):
            if "above" in c and last and last >= c["above"]:
                add("price_above", f"{name} {c['above']:,} 돌파 (현재 {last:,.2f})", "good")
            if "below" in c and last and last <= c["below"]:
                add("price_below", f"{name} {c['below']:,} 하회 (현재 {last:,.2f})", "warn")
    return out


def verdict_changes(watch_rows: list[dict]) -> list[dict]:
    state = read_json("verdicts.json", STATE_DIR, default={}) or {}
    out = []
    for w in watch_rows:
        v_new, v_old = w.get("verdict"), state.get(w["ticker"])
        if v_new and v_old and v_new != v_old:
            out.append({"ticker": w["ticker"], "name": w["name"], "kind": "verdict", "level": "info",
                        "msg": f"{w['name']} 판정 변경: {v_old} → {v_new}", "price": (w.get("tech") or {}).get("last"),
                        "chg_1d": (w.get("tech") or {}).get("chg_1d")})
        if v_new:
            state[w["ticker"]] = v_new
    write_json("verdicts.json", state, STATE_DIR)
    return out


def dispatch(new_alerts: list[dict], extra_push: dict | None = None) -> list[dict]:
    """같은 (종목,종류) 알림은 하루 1회. 며칠씩 유지되는 차트 신호는 쿨다운 기간 동안 1회만."""
    today_d = now_kst().date()
    today = today_d.isoformat()
    sent = read_json("sent.json", STATE_DIR, default={}) or {}
    sent = {k: v for k, v in sent.items() if (today_d - date.fromisoformat(v)).days <= max(COOLDOWN.values())}
    fresh = []
    for al in new_alerts:
        key = f"{al['ticker']}|{al['kind']}"
        last = sent.get(key)
        cool = next((d for pre, d in COOLDOWN.items() if al["kind"].startswith(pre)), 0)
        if last and (today_d - date.fromisoformat(last)).days <= cool:
            continue
        sent[key] = today
        al["time"] = now_kst().isoformat(timespec="minutes")
        fresh.append(al)
    write_json("sent.json", sent, STATE_DIR)

    hist = read_json("alerts.json", default={"items": []}) or {"items": []}
    hist["items"] = (fresh[::-1] + hist["items"])[:200]
    hist["updated"] = now_kst().isoformat(timespec="minutes")
    write_json("alerts.json", hist)

    if fresh:
        body = "\n".join(a["msg"] for a in fresh[:5]) + (f"\n외 {len(fresh) - 5}건" if len(fresh) > 5 else "")
        push({"title": f"📈 신호 알림 {len(fresh)}건", "body": body, "url": "./#alerts"})
    if extra_push:
        push(extra_push)
    return fresh


def push(payload: dict) -> int:
    priv, subs_raw = os.environ.get("VAPID_PRIVATE_KEY"), os.environ.get("PUSH_SUBSCRIPTIONS")
    if not priv or not subs_raw:
        log.info("푸시 생략(VAPID_PRIVATE_KEY/PUSH_SUBSCRIPTIONS 미설정): %s", payload.get("title"))
        return 0
    try:
        from pywebpush import WebPushException, webpush
    except ImportError:
        log.warning("pywebpush 미설치")
        return 0
    subs = json.loads(subs_raw)
    subs = subs if isinstance(subs, list) else [subs]
    ok = 0
    for s in subs:
        try:
            webpush(subscription_info=s, data=json.dumps(payload, ensure_ascii=False),
                    vapid_private_key=priv,
                    vapid_claims={"sub": os.environ.get("VAPID_SUBJECT", "mailto:me@example.com")}, ttl=3600)
            ok += 1
        except WebPushException as e:
            log.warning("푸시 실패: %s", e)
    log.info("푸시 발송 %d/%d", ok, len(subs))
    return ok
