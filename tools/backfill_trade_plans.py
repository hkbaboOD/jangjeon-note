"""한 번만 실행: 매매 판단 기능(2026-11-25) 이전 추천에 '그때의 추천 파일'(git 이력의 web/data/picks.json)로 매매 계획을 붙임.
계획은 추천 당시 자료(techx·relaxed·stage40·price)만 쓰므로 미래 데이터를 쓰지 않습니다. 결과 채점은 파이프라인이 합니다."""
import json
import subprocess
import sys

sys.path.insert(0, ".")
from pipeline import trade  # noqa: E402

TRACK = "web/data/track.json"
revs = subprocess.run(["git", "log", "--format=%H", "--", "web/data/picks.json"], capture_output=True, text=True).stdout.split()
picks = {}  # (date, horizon, ticker) -> [pick, ...]
for rev in revs:
    try:
        d = json.loads(subprocess.run(["git", "show", f"{rev}:web/data/picks.json"], capture_output=True, text=True).stdout)
    except json.JSONDecodeError:
        continue
    for h, blk in d.items():
        if not isinstance(blk, dict):
            continue
        day = (blk.get("generated") or "")[:10]
        for p in blk.get("all") or []:
            picks.setdefault((day, h, p["ticker"]), []).append(p)
t = json.load(open(TRACK))
n = miss = 0
for i in t["items"]:
    if i.get("plan"):
        continue
    cands = picks.get((i["date"], i["horizon"], i["ticker"])) or []
    p = next((c for c in cands if c.get("price") and abs(c["price"] / i["entry"] - 1) < 1e-6), None)
    if p is None:
        miss += 1
        continue
    pl = trade.plan(p.get("techx"), p["price"], i["horizon"], bool(p.get("relaxed")), p.get("stage40"), None)
    pl["backfill"] = True
    i["plan"] = pl
    n += 1
json.dump(t, open(TRACK, "w"), ensure_ascii=False, indent=1)
print(f"계획 추가 {n}건, 당시 추천 파일 없음 {miss}건")
