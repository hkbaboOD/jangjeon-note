"""주간 PDCA 리포트 — Plan(이번 주 모델) · Do(예측·추천 기록) · Check(예측 vs 실제) · Act(보정·롤백) 요약.

매주 토요일 백테스트·자가 보정 직후 자동 생성되어 앱 검증 탭 상단에 표시됩니다.
Claude 주간 점검(예약 작업)이 있으면 그 결과(pdca_review.json)도 함께 보여 줍니다.
"""
from __future__ import annotations

from datetime import date, timedelta

H_NAME = {"daily": "오늘", "weekly": "이번 주", "monthly": "이번 달"}
NEED = {"daily": 45, "weekly": 60, "monthly": 180}  # 실전 보정 시작까지 필요한 거래일(대략)


def build(model: dict, ev: dict, track: dict, prev: dict | None, today: str) -> dict:
    prev = prev or {}
    items = (track or {}).get("items") or []
    week_ago = (date.fromisoformat(today) - timedelta(days=7)).isoformat()
    new_picks = [i for i in items if i["date"] > week_ago]
    closed = [i for i in items if (i.get("final") or {}).get("closed", "") > week_ago]
    matured = (track or {}).get("matured") or {}

    check = {"matured": matured, "ic": (ev or {}).get("recent") or {}, "top3": (ev or {}).get("top3") or {},
             "closed_this_week": [{"name": i["name"], "horizon": i["horizon"], "date": i["date"], "score": i.get("score"),
                                   "setup": i.get("setup"), **i["final"]} for i in closed][-15:]}
    pc = (prev.get("check") or {}).get("matured") or {}
    delta = {h: round(v["win_rate"] - pc[h]["win_rate"], 1) for h, v in matured.items() if h in pc}

    act_today = model.get("changes_now")
    if act_today is None:  # 구버전 model.json 호환
        act_today = [c["text"] for c in (model.get("log") or []) if c.get("date") == today]
    contest = model.get("contest") or {}

    nxt = []
    days = (ev or {}).get("days", 0)
    for h, need in NEED.items():
        if h not in contest:
            left = max(0, need - days)
            nxt.append(f"{H_NAME[h]} 추천: 실전 기록 {days}일 — 실전 채점 기반 보정까지 약 {max(1, round(left / 5))}주")
    for h, v in matured.items():
        if v["count"] >= 20 and v["recent_win"] < 45:
            nxt.append(f"{H_NAME[h]} 추천 최근 승률 {v['recent_win']:.0f}%로 저조 → 다음 보정에서 새 후보·기본 모델 비교 결과를 우선 반영")
        elif v["count"] >= 20 and v["recent_win"] >= 60:
            nxt.append(f"{H_NAME[h]} 추천 최근 승률 {v['recent_win']:.0f}% 양호 → 현재 모델 유지, 과최적화 여부만 감시")
    if not act_today:
        nxt.append("이번 주 근거가 유의하지 않아 가중치 유지(억지로 바꾸지 않음)")

    hist = (prev.get("history") or [])
    entry = {"week": today, "version": model.get("version"),
             **{f"win_{h}": round(v["win_rate"], 1) for h, v in matured.items()},
             **{f"ic_{h}": v["ic"] for h, v in ((ev or {}).get("recent") or {}).items()}}
    hist = [e for e in hist if e["week"] != today] + [entry]

    return {
        "week": today,
        "plan": {"version": model.get("version"), "weights": model.get("weights"), "basis": model.get("basis"),
                 "changed": model.get("changed")},
        "do": {"days_recorded": days, "new_picks": len(new_picks), "open_picks": sum(1 for i in items if not i.get("final"))},
        "check": {**check, "win_delta": delta},
        "act": {"changes": act_today, "contest": contest},
        "next": nxt,
        "history": hist[-104:],
    }
