/* 배당 플랜 계산기 — 앱 안에서 구성·비중·세율을 바꾸면 즉시 다시 계산 (pipeline/dividend.py 의 build_plan 과 같은 규칙 + 사용자 설정)
   입력 후보: dividend.json candidates[] (가격·통화·월별 주당배당금 dps[12]·배당락일 ex_days·점수 등) */
"use strict";
const DivPlan = (() => {
  const DEFAULTS = {
    capital: 10000000, target: 0, mode: "auto", n: 6, market: "all", kind: "all", minYield: 0,
    weighting: "score", custom: {}, singleCap: 0.35, ccCap: 0.2, taxUs: 0.15, taxKr: 0.154,
    whole: true, pinned: [], excluded: [], selected: [], monthBonus: 4,
  };

  const isEtf = (c) => c.type === "etf";
  const passFilter = (c, o) =>
    (o.market === "all" || c.market === o.market) &&
    (o.kind === "all" || (o.kind === "etf" ? isEtf(c) : !isEtf(c))) &&
    (c.yield ?? 0) >= (o.minYield || 0);

  function choose(cands, o) {
    const by = new Map(cands.map((c) => [c.ticker, c]));
    const ccMax = o.ccCap <= 0 ? 0 : o.ccCap <= 0.2 ? 1 : 2;
    if (o.mode === "manual") {
      return o.selected.map((t) => by.get(t)).filter(Boolean);
    }
    const picks = [], covered = new Set();
    const add = (c) => { picks.push(c); c.months.forEach((m) => covered.add(m)); };
    o.pinned.map((t) => by.get(t)).filter((c) => c && !o.excluded.includes(c.ticker)).forEach(add);
    const pool = cands.filter((c) => passFilter(c, o) && !o.excluded.includes(c.ticker) && !picks.includes(c));
    while (pool.length && picks.length < o.n) {
      let bi = 0, bv = -Infinity;
      pool.forEach((c, i) => {
        const v = c.score + o.monthBonus * c.months.filter((m) => !covered.has(m)).length;
        if (v > bv) { bv = v; bi = i; }
      });
      const best = pool.splice(bi, 1)[0];
      if (best.covered_call && picks.filter((p) => p.covered_call).length >= ccMax) continue;
      add(best);
    }
    return picks;
  }

  /* 비중: 합 1 유지하며 커버드콜 합산 상한 · 단일 상한을 만족하도록 초과분 재분배 */
  function weights(picks, o) {
    const w = {};
    if (!picks.length) return w;
    if (o.weighting === "custom") {
      let tot = 0;
      picks.forEach((p) => { w[p.ticker] = Math.max(0, Number(o.custom[p.ticker] ?? 100 / picks.length)); tot += w[p.ticker]; });
      picks.forEach((p) => { w[p.ticker] = tot ? w[p.ticker] / tot : 1 / picks.length; });
      return w;
    }
    const raw = (p) => (o.weighting === "equal" ? 1 : o.weighting === "yield" ? Math.max(p.yield || 0, 0.1) : Math.max(p.score, 1));
    let tot = picks.reduce((s, p) => s + raw(p), 0);
    picks.forEach((p) => { w[p.ticker] = raw(p) / tot; });
    let single = Math.max(o.singleCap, 1 / picks.length);
    const fixed = new Set();
    for (let it = 0; it < 30; it++) {
      // 종목 수가 적어 상한을 지키면 100%를 못 채우는 경우(예: 3종목·커버드콜 20%·단일 35%) → 일반 종목 상한을 필요한 만큼만 완화
      const nonCC = picks.filter((p) => !p.covered_call).length;
      const ccNow = picks.filter((p) => p.covered_call).reduce((s, p) => s + w[p.ticker], 0);
      if (nonCC && ccNow <= o.ccCap + 1e-9) single = Math.max(single, (1 - ccNow) / nonCC);
      let changed = false;
      const cc = picks.filter((p) => p.covered_call && !fixed.has(p.ticker));
      const ccFixedW = picks.filter((p) => p.covered_call && fixed.has(p.ticker)).reduce((s, p) => s + w[p.ticker], 0);
      const ccW = cc.reduce((s, p) => s + w[p.ticker], 0) + ccFixedW;
      if (cc.length && ccW > o.ccCap + 1e-9) {
        const room = Math.max(0, o.ccCap - ccFixedW), cur = ccW - ccFixedW;
        cc.forEach((p) => { w[p.ticker] = cur ? (w[p.ticker] * room) / cur : 0; fixed.add(p.ticker); });
        changed = true;
      }
      picks.forEach((p) => { if (!fixed.has(p.ticker) && w[p.ticker] > single + 1e-9) { w[p.ticker] = single; fixed.add(p.ticker); changed = true; } });
      w._single = single;
      const free = picks.filter((p) => !fixed.has(p.ticker));
      const fixedSum = picks.filter((p) => fixed.has(p.ticker)).reduce((s, p) => s + w[p.ticker], 0);
      const freeSum = free.reduce((s, p) => s + w[p.ticker], 0);
      if (free.length && freeSum > 0) free.forEach((p) => { w[p.ticker] = (w[p.ticker] / freeSum) * Math.max(0, 1 - fixedSum); });
      if (!changed) break;
    }
    return w;
  }

  function build(cands, opts, usdkrw, today = new Date()) {
    const o = { ...DEFAULTS, ...opts };
    const picks = choose(cands, o);
    const w = weights(picks, o);
    const monthly = Array(12).fill(0);
    let invested = 0;
    const rows = picks.map((p) => {
      const fx = p.currency === "KRW" ? 1 : usdkrw;
      const unit = (p.price || 0) * fx;
      const target = o.capital * (w[p.ticker] || 0);
      const shares = unit > 0 ? (o.whole ? Math.floor(target / unit) : target / unit) : 0;
      const alloc = shares * unit;
      invested += alloc;
      const tax = p.market === "kr" ? o.taxKr : o.taxUs;
      const per = (p.dps || []).map((d) => shares * d * fx * (1 - tax));
      per.forEach((v, i) => { monthly[i] += v; });
      return { ...p, weight: (w[p.ticker] || 0) * 100, target, alloc, shares, unit, annual: per.reduce((a, b) => a + b, 0), per,
        pinned: o.pinned.includes(p.ticker) };
    });
    const annual = monthly.reduce((a, b) => a + b, 0);
    const covered = [...new Set(picks.flatMap((p) => p.months))].sort((a, b) => a - b);
    const yieldNet = invested ? (annual / invested) * 100 : null;
    const upcoming = [];
    const t0 = new Date(today.getFullYear(), today.getMonth(), today.getDate());
    picks.forEach((p) => Object.entries(p.ex_days || {}).forEach(([m, d]) => {
      [t0.getFullYear(), t0.getFullYear() + 1].forEach((y) => {
        const est = new Date(y, Number(m) - 1, Math.min(Number(d), 28));
        const dd = (est - t0) / 864e5;
        if (dd >= 0 && dd <= 45) upcoming.push({ ticker: p.ticker, name: p.name, date: est, days: Math.round(dd) });
      });
    }));
    upcoming.sort((a, b) => a.date - b.date);
    const warn = [];
    if (o.mode === "auto" && picks.length < o.n) warn.push(`조건을 만족하는 후보가 ${picks.length}개뿐입니다. 시장·유형·최소 배당률 조건을 넓혀 보세요.`);
    if (o.weighting === "custom") {
      const s = picks.reduce((a, p) => a + Number(o.custom[p.ticker] ?? 100 / picks.length), 0);
      if (picks.length && Math.abs(s - 100) > 0.5) warn.push(`직접 입력한 비중 합계가 ${s.toFixed(0)}%라 100% 기준으로 맞춰 계산했습니다.`);
    }
    if (w._single && w._single > Math.max(o.singleCap, 1 / Math.max(1, picks.length)) + 1e-6 && o.weighting !== "custom")
      warn.push(`종목 수가 적어 100%를 채우도록 단일 종목 상한을 ${Math.round(w._single * 100)}%로 완화했습니다.`);
    const ccW = rows.filter((r) => r.covered_call).reduce((a, r) => a + r.weight, 0);
    if (o.weighting === "custom" && ccW > o.ccCap * 100 + 0.5) warn.push(`커버드콜 비중 ${ccW.toFixed(0)}% — 상한 ${Math.round(o.ccCap * 100)}%를 넘습니다(직접 입력은 상한을 적용하지 않음).`);
    const missing = Array.from({ length: 12 }, (_, i) => i + 1).filter((m) => !covered.includes(m));
    const cash = o.capital - invested;
    return { rows, monthly, annual, invested, cash, yieldNet, covered, missing, upcoming, warn,
      need: o.target > 0 && yieldNet ? (o.target * 12) / (yieldNet / 100) : null };
  }

  return { DEFAULTS, build, choose, weights, passFilter };
})();
if (typeof module !== "undefined") module.exports = DivPlan;
