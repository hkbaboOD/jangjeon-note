// node tests/js/divplan.test.js  — 배당 플랜 계산기(앱 내 재계산) 검증
const assert = require("assert");
const DP = require("../../web/divplan.js");
const mk = (t, o) => ({ ticker: t, name: t, type: "stock", market: "us", covered_call: false, yield: 3, score: 50, freq: 4,
  months: [3, 6, 9, 12], price: 100, currency: "USD", ex_days: { 3: 10, 6: 10, 9: 10, 12: 10 },
  dps: [0, 0, 0.75, 0, 0, 0.75, 0, 0, 0.75, 0, 0, 0.75], ...o });
const cands = [
  mk("A", { score: 90 }), mk("B", { score: 80 }), mk("C", { score: 70, months: [1, 4, 7, 10], dps: [0.75, 0, 0, 0.75, 0, 0, 0.75, 0, 0, 0.75, 0, 0] }),
  mk("CC", { score: 95, covered_call: true, type: "etf", yield: 10, months: [1,2,3,4,5,6,7,8,9,10,11,12], dps: Array(12).fill(0.8) }),
  mk("K", { market: "kr", currency: "KRW", price: 50000, score: 60, dps: [0,0,0,0,0,0,0,0,0,0,0,2000], months: [12] }),
];
const fx = 1400;
let p = DP.build(cands, { n: 3, whole: false }, fx, new Date(2026, 9, 20));
// 월 커버리지 보너스: CC(95+4*12) → C(새 월 0개.. ) 순서 확인, 커버드콜 비중 ≤ 20%
assert.strictEqual(p.rows.length, 3);
const cc = p.rows.find((r) => r.ticker === "CC");
assert(cc && Math.abs(cc.weight - 20) < 1e-6, "커버드콜 20% 상한");
assert(Math.abs(p.rows.reduce((s, r) => s + r.weight, 0) - 100) < 1e-6, "비중 합 100%");
assert(p.rows.every((r) => r.weight <= 40 + 1e-6), "단일 상한(3종목·커버드콜 20% → 40%로 완화)");
assert(p.warn.some((w) => w.includes("40%로 완화")));
let p6 = DP.build(cands.concat([mk("D", { score: 65 }), mk("E", { score: 64 }), mk("F", { score: 63 })]), { n: 6, whole: false }, fx);
assert(p6.rows.every((r) => r.weight <= 35 + 1e-6) && Math.abs(p6.rows.reduce((s, r) => s + r.weight, 0) - 100) < 1e-6 && !p6.warn.length);
// 세후 금액: 미국 15% 원천징수
const a = p.rows.find((r) => r.ticker === "A");
if (a) assert(Math.abs(a.annual - a.shares * 3 * fx * 0.85) < 1e-6);
// 커버드콜 제외 · 고정 · 제외
p = DP.build(cands, { n: 3, ccCap: 0, pinned: ["K"], excluded: ["A"] }, fx);
assert(!p.rows.some((r) => r.ticker === "CC" || r.ticker === "A"));
assert(p.rows.some((r) => r.ticker === "K") && p.rows.find((r) => r.ticker === "K").pinned);
// 국내 배당소득세 0(연금·ISA)
p = DP.build(cands, { mode: "manual", selected: ["K"], taxKr: 0, whole: true, capital: 1000000 }, fx);
assert.strictEqual(p.rows[0].shares, 20);
assert.strictEqual(p.rows[0].annual, 20 * 2000);
assert.strictEqual(p.cash, 0);
// 직접 비중: 정규화 + 경고
p = DP.build(cands, { mode: "manual", selected: ["A", "B"], weighting: "custom", custom: { A: 30, B: 30 }, whole: false }, fx);
assert(Math.abs(p.rows[0].weight - 50) < 1e-6 && p.warn.length === 1);
// 시장 필터 · 부족 경고 · 목표 월배당 → 필요 투자금
p = DP.build(cands, { n: 4, market: "kr", target: 100000, whole: false }, fx);
assert.strictEqual(p.rows.length, 1);
assert(p.warn.some((w) => w.includes("1개뿐")));
assert(Math.abs(p.need - (100000 * 12) / (p.yieldNet / 100)) < 1e-6);
// 균등 비중 + 정수 주: 남는 현금
p = DP.build(cands, { mode: "manual", selected: ["A", "B"], weighting: "equal", capital: 1000000, whole: true }, fx);
assert(p.rows.every((r) => Number.isInteger(r.shares)) && p.cash > 0 && p.invested + p.cash === 1000000);
// 다가오는 배당락(45일 이내)
p = DP.build(cands, { mode: "manual", selected: ["A"] }, fx, new Date(2026, 10, 20));
assert(p.upcoming.length === 1 && p.upcoming[0].date.getMonth() === 11);
console.log("divplan: all tests passed");
