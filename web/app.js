/* 장전노트 PWA — 정적 JSON(web/data/*.json)을 읽어 화면을 그립니다. 프레임워크 없이 동작. */
"use strict";

const CFG = window.APP_CONFIG || {};
const $ = (s, el = document) => el.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const store = {
  get(k, d) { try { const v = localStorage.getItem(k); return v === null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* 저장 불가 환경 */ } },
};
const state = {
  data: {},
  picksH: store.get("picksH", "daily"),
  picksM: store.get("picksM", "all"),
  scrM: store.get("scrM", "all"),
  scrSort: store.get("scrSort", "score_weekly"),
  scrQ: "",
  scrF: store.get("scrF", "all"),
  capital: store.get("capital", null),
  charts: {},
  labH: store.get("labH", "20"),
  labM: store.get("labM", "all"),
  labF: store.get("labF", "total"),
  finP: store.get("finP", "a"),
};

/* ── 포맷 ── */
const isKR = (t) => /\.(KS|KQ)$/.test(t || "");
const code = (t) => (t || "").replace(/\.(KS|KQ)$/, "");
const nf = (v, d = 0) => (v == null || Number.isNaN(v) ? "-" : Number(v).toLocaleString("ko-KR", { minimumFractionDigits: d, maximumFractionDigits: d }));
const price = (v, t) => (v == null ? "-" : isKR(t) ? nf(v, 0) + "원" : "$" + nf(v, 2));
const cls = (v) => (v == null ? "flat" : v > 0.0001 ? "up" : v < -0.0001 ? "down" : "flat");
const sgn = (v, d = 2, suf = "%") => (v == null ? "-" : (v > 0 ? "+" : "") + nf(v, d) + suf);
const arrow = (v) => (v == null ? "" : v > 0 ? "▲" : v < 0 ? "▼" : "");
const krw = (v) => {
  if (v == null) return "-";
  const a = Math.abs(v);
  if (a >= 1e8) return nf(v / 1e8, 2) + "억원";
  if (a >= 1e4) return nf(v / 1e4, a >= 1e6 ? 0 : 1) + "만원";
  return nf(v) + "원";
};
const when = (iso) => {
  if (!iso) return "";
  const d = new Date(iso);
  return `${d.getMonth() + 1}/${d.getDate()} ${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
};
const VCLASS = { "강한 매수": "v-strong-buy", "매수": "v-buy", "중립": "v-hold", "매도": "v-sell", "강한 매도": "v-strong-sell" };
const verdict = (v) => (v ? `<span class="verdict ${VCLASS[v] || "v-hold"}">${esc(v)}</span>` : "");
const SECTOR_KO = {
  Technology: "기술", "Financial Services": "금융", Healthcare: "헬스케어", Energy: "에너지",
  "Consumer Defensive": "필수소비재", "Consumer Cyclical": "경기소비재", Industrials: "산업재",
  "Communication Services": "커뮤니케이션", Utilities: "유틸리티", "Basic Materials": "소재", "Real Estate": "부동산",
};
const sector = (s) => SECTOR_KO[s] || s || "";

function spark(arr, w = 64, h = 30) {
  if (!arr || arr.length < 2) return "";
  const lo = Math.min(...arr), hi = Math.max(...arr), rng = hi - lo || 1;
  const pts = arr.map((v, i) => `${((i / (arr.length - 1)) * w).toFixed(1)},${(h - 2 - ((v - lo) / rng) * (h - 4)).toFixed(1)}`).join(" ");
  const c = arr[arr.length - 1] >= arr[0] ? "var(--up)" : "var(--down)";
  return `<svg class="spark" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" aria-hidden="true"><polyline fill="none" stroke="${c}" stroke-width="1.6" stroke-linejoin="round" points="${pts}"/></svg>`;
}
function toast(msg) {
  const t = $("#toast"); t.textContent = msg; t.classList.add("show");
  clearTimeout(toast._t); toast._t = setTimeout(() => t.classList.remove("show"), 2200);
}
const chartFile = (t) => t.replace(/[^A-Za-z0-9.\-]/g, "_") + ".json";
async function loadChart(t) {
  if (state.charts[t] !== undefined) return state.charts[t];
  try {
    const r = await fetch(`data/charts/${chartFile(t)}?d=${encodeURIComponent(state.data.meta?.morning || "")}`);
    state.charts[t] = r.ok ? await r.json() : null;
  } catch { state.charts[t] = null; }
  return state.charts[t];
}
const setupPill = (label, setup) => (label && setup !== "insufficient" ? `<span class="setup s-${esc(setup)}">${esc(label)}</span>` : "");
const shortTxt = (t) => String(t).split(" — ")[0];
const lvTxt = (l, t) => (l ? `${price(l.price, t)}(${esc(l.label || "")}, ${sgn(l.dist, 1)})` : "-");
function levelsRow(lv, t) {
  if (!lv) return "";
  const s = (lv.support || [])[0], r = (lv.resistance || [])[0];
  const cell = (lab, l) => `<div><small>${lab}</small><b>${l ? price(l.price, t) : "-"}</b>${l ? `<em>${esc(l.label || "")} · ${sgn(l.dist, 1)}</em>` : ""}</div>`;
  return `<div class="levels">
    ${cell("1차 지지", s)}${cell("1차 저항", r)}
    <div><small>손절 참고</small><b>${lv.stop ? price(lv.stop.price, t) : "-"}</b>${lv.stop ? `<em>${lv.stop.basis === "2ATR" ? "2×ATR" : "지지선 − 0.5×ATR"} · ${sgn(lv.stop.dist, 1)}</em>` : ""}</div>
    <div><small>손익비 <span class="sr">1차 저항까지 기대폭 ÷ 손절폭</span></small><b>${lv.rr == null ? "-" : nf(lv.rr, 2) + " : 1"}</b><em>1차 저항 ÷ 손절폭</em></div>
  </div>`;
}
/* 한국어 증권 사이트 링크: 파이프라인이 만든 links(네이버 코드 확인분) 우선, 없으면 네이버 국내 종목 페이지/통합검색 */
function linksOf(t) {
  const r = (state.data.screen?.rows || []).find((x) => x.ticker === t);
  if (r?.links) return r.links;
  const cd = state.charts[t];
  if (cd?.co?.links) return cd.co.links;
  const nm = r?.name || t;
  const news = `https://search.naver.com/search.naver?where=news&query=${encodeURIComponent(nm + " 주가")}`;
  return isKR(t) ? { naver: `https://m.stock.naver.com/domestic/stock/${code(t)}/total`, fnguide: `https://comp.fnguide.com/SVO2/ASP/SVD_Main.asp?pGB=1&gicode=A${code(t)}`, news }
    : { naver: `https://search.naver.com/search.naver?query=${encodeURIComponent(nm + " 주가")}`, news };
}
const extLink = (t) => linksOf(t).naver;
function linkBtns(L, cls = "btn ghost sm") {
  if (!L) return "";
  const a = (href, label) => (href ? `<a class="${cls}" href="${esc(href)}" target="_blank" rel="noopener">${label}</a>` : "");
  return a(L.naver, "네이버 증권") + a(L.news, "관련 뉴스") + a(L.fnguide, "FnGuide 재무");
}
/* 재무 숫자 포맷 */
const money = (v, cur) => {
  if (v == null || Number.isNaN(v)) return "-";
  const a = Math.abs(v);
  if (cur === "KRW") return a >= 1e12 ? nf(v / 1e12, 1) + "조" : nf(v / 1e8, 0) + "억";
  return a >= 1e9 ? "$" + nf(v / 1e9, 1) + "B" : "$" + nf(v / 1e6, 0) + "M";
};
const epsFmt = (v, cur) => (v == null ? "-" : cur === "KRW" ? nf(v, 0) + "원" : "$" + nf(v, 2));
const pctOf = (a, b) => (a == null || b == null || !b ? null : (a / b) * 100);
const yoy = (a, b) => (a == null || b == null || !b ? null : b > 0 ? (a / b - 1) * 100 : ((a - b) / Math.abs(b)) * 100);

/* ── 데이터 ── */
const FILES = ["meta", "briefing", "briefing_us", "watch", "picks", "track", "screen", "dividend", "alerts", "backtest", "model", "forward", "scorecard", "pdca", "pdca_review", "changelog", "geo", "geo_weekly"];
async function load() {
  const btn = $("#refresh"); btn.classList.add("spin");
  const res = await Promise.allSettled(FILES.map((f) => fetch(`data/${f}.json?t=${Date.now()}`, { cache: "no-store" }).then((r) => (r.ok ? r.json() : null))));
  res.forEach((r, i) => { state.data[FILES[i]] = r.status === "fulfilled" ? r.value : null; });
  btn.classList.remove("spin");
  renderAll();
}

/* ── 브리핑 ── */
/* 국장(아침)/미장(저녁) 전환: 기본값은 시간대로 — 16시~다음날 6시는 미장 */
function autoBriefM() {
  const h = new Date().getHours();
  return (h >= 16 || h < 6) && state.data.briefing_us ? "us" : "kr";
}
function briefSeg() {
  const bu = state.data.briefing_us, bk = state.data.briefing;
  return `<div class="seg brief-seg" role="group" aria-label="브리핑 종류">
    <button data-bm="kr" aria-pressed="${state.briefM === "kr"}">☀️ 국장 장전<small>${bk ? esc(when(bk.generated)) : "06:37"}</small></button>
    <button data-bm="us" aria-pressed="${state.briefM === "us"}">🌙 미장 장전<small>${bu ? esc(when(bu.generated)) : "21:13"}</small></button></div>`;
}
const briefTick = (q) => `
    <div class="tick ${q.chg > 0 ? "up-bg" : q.chg < 0 ? "down-bg" : ""}">
      <span class="nm">${esc(q.name)}</span>
      <span class="ch ${cls(q.chg)}"><span class="ar" aria-hidden="true">${arrow(q.chg)}</span>${sgn(q.chg)}</span>
      ${spark(q.spark, 64, 32)}
      <span class="px">${nf(q.last, 2)}</span>
    </div>`;
const macroChips = (list) => (list || []).map((q) => {
  const v = q.chg_bp != null ? sgn(q.chg_bp, 0, "bp") : sgn(q.chg);
  return `<div class="chip">${esc(q.name)}<b>${nf(q.last, q.last > 100 ? 1 : 2)} <span class="${cls(q.chg)}">${v}</span></b></div>`;
}).join("");
const newsList = (title, list) => ((list || []).length ? `<div class="sec"><h2>${title}</h2><div class="panel news">${list.map((n) => `<a href="${esc(n.link)}" target="_blank" rel="noopener">${esc(n.title)}<small>${esc(n.publisher || "")} ${n.time ? "· " + esc(when(n.time)) : ""}</small></a>`).join("")}</div></div>` : "");
const earnRows = (list, title) => ((list || []).length ? `<div class="sec"><h2>${title}</h2><div class="panel">${list.map((e) => `
    <button class="row" data-t="${esc(e.ticker)}"><div><div class="t1">${esc(e.name)}</div><div class="t2">${esc(isKR(e.ticker) ? code(e.ticker) : e.ticker)}</div></div><span></span>
    <div class="num"><b>${e.in_days === 0 ? "오늘" : e.in_days === 1 ? "내일" : e.in_days + "일 후"}</b><span>${esc(e.date.slice(5).replace("-", "/"))}</span></div></button>`).join("")}</div></div>` : "");
function moversBlock(movers, label) {
  return Object.entries(movers || {}).map(([mk, v]) => `
    <div class="sec"><h2>${label || (mk === "us" ? "미국" : "한국")} 등락 상위</h2><div class="panel">
    ${[...v.top.slice(0, 3), ...v.bottom.slice(0, 3)].map((m) => `
      <button class="row" data-t="${esc(m.ticker)}"><div><div class="t1">${esc(m.name)}</div><div class="t2">${esc(isKR(m.ticker) ? code(m.ticker) : m.ticker)}</div></div><span></span><div class="num"><b class="${cls(m.chg)}">${sgn(m.chg)}</b></div></button>`).join("")}
    </div></div>`).join("");
}
function renderBriefUS(el) {
  const b = state.data.briefing_us;
  if (!b) { el.innerHTML = briefSeg() + `<div class="empty">미장 장전 브리핑은 평일 저녁 21시 전후(미국 개장 전)에 생성됩니다.</div>`; return; }
  const d = new Date(b.date + "T00:00:00");
  const dow = "일월화수목금토"[d.getDay()];
  const watch = (b.watch || []).slice().sort((a, c) => (a.market === "us" ? 0 : 1) - (c.market === "us" ? 0 : 1)).map((r) => `
    <button class="row" data-t="${esc(r.ticker)}">
      <div><div class="t1">${esc(r.name)}</div><div class="t2">${price(r.last, r.ticker)} · ${esc(r.setup_label || "")} · RSI ${nf(r.rsi14)}${r.market === "us" ? " · 전일 종가" : " · 오늘 종가"}</div></div>
      ${spark(r.spark, 56, 26)}
      <div class="num"><b class="${cls(r.chg_1d)}">${sgn(r.chg_1d)}</b><span class="${cls(r.chg_5d)}">5일 ${sgn(r.chg_5d, 1)}</span></div>
    </button>`).join("");
  const br = b.breadth?.kr;
  el.innerHTML = `${briefSeg()}
    <div class="dateline"><h1>${d.getMonth() + 1}월 ${d.getDate()}일 ${dow}요일 미장 장전</h1><p>미국 개장 ${esc(b.us_open_kst || "")} KST${b.dst ? "(서머타임)" : ""} · ${when(b.generated)} 생성 · 선물은 생성 시점 기준</p></div>
    <div class="tape">${(b.futures || []).map(briefTick).join("")}${(b.kr_indices || []).map(briefTick).join("")}</div>
    <div class="macro">${macroChips(b.macro)}</div>
    ${b.ai_summary ? `<div class="sec"><h2>AI 시황 요약</h2><div class="ai">${esc(b.ai_summary)}</div></div>` : ""}
    <div class="sec"><h2>오늘 밤 미국장 체크</h2><ul class="bullets">${(b.tonight || []).map((p) => `<li>${esc(p)}</li>`).join("")}</ul></div>
    <div class="sec"><h2>오늘 국내장 마감</h2><ul class="bullets">${(b.review || []).map((p) => `<li>${esc(p)}</li>`).join("") || "<li>데이터 없음</li>"}</ul>
      ${br ? `<div class="panel pad breadth" style="margin-top:8px"><div><div class="bl"><span>한국 유니버스</span><span><span class="up">상승 ${br.up}</span> · <span class="down">하락 ${br.down}</span></span></div><div class="bar"><i style="width:${(br.up / br.total) * 100}%"></i></div></div></div>` : ""}</div>
    ${earnRows(b.earnings, "실적 발표 <small>미국 유니버스 · 미국 시간 기준</small>")}
    ${(b.us_picks || []).length ? `<div class="sec"><h2>오늘의 미국 추천 Top3 <small>아침 산출 · 추천 탭에서 근거 확인</small></h2><div class="panel">${b.us_picks.map((p, i) => `
      <button class="row" data-t="${esc(p.ticker)}"><div><div class="t1">${i + 1}. ${esc(p.name)}</div><div class="t2">${esc(p.setup_label || "")}</div></div><span></span><div class="num"><b>${nf(p.score, 1)}</b><span class="t2">점</span></div></button>`).join("")}</div></div>` : ""}
    ${b.geo ? geoCard(b.geo, state.data.geo_weekly) : ""}
    <div class="sec"><h2>관심종목</h2><div class="panel">${watch || '<div class="empty">관심종목 없음</div>'}</div></div>
    ${(b.us_prev || []).length ? `<div class="sec"><h2>전일 미 증시 마감</h2><div class="macro">${macroChips(b.us_prev)}</div></div>` : ""}
    ${moversBlock(b.movers, "오늘 한국")}
    ${newsList("뉴욕증시 헤드라인", b.news_kr)}
    ${newsList("해외 헤드라인", b.news)}
    <p class="note">${esc(state.data.meta?.disclaimer || "")}</p>`;
}
function renderBrief() {
  const b = state.data.briefing, w = state.data.watch;
  const el = $("#view-brief");
  if (state.briefM == null) state.briefM = autoBriefM();
  if (state.briefM === "us") { renderBriefUS(el); return; }
  if (!b) { el.innerHTML = briefSeg() + `<div class="empty">아직 브리핑이 없습니다. 평일 아침 7시 전후로 생성됩니다.</div>`; return; }
  const d = new Date(b.date + "T00:00:00");
  const dow = "일월화수목금토"[d.getDay()];
  const idx = b.indices || [];
  const tick = (q, big) => `
    <div class="tick ${big ? "big" : ""} ${q.chg > 0 ? "up-bg" : q.chg < 0 ? "down-bg" : ""}">
      <span class="nm">${esc(q.name)}</span>
      <span class="ch ${cls(q.chg)}"><span class="ar" aria-hidden="true">${arrow(q.chg)}</span>${sgn(q.chg)}</span>
      ${spark(q.spark, 64, 32)}
      <span class="px">${nf(q.last, 2)}</span>
    </div>`;
  const macro = (b.macro || []).map((q) => {
    const v = q.chg_bp != null ? sgn(q.chg_bp, 0, "bp") : sgn(q.chg);
    return `<div class="chip">${esc(q.name)}<b>${nf(q.last, q.last > 100 ? 1 : 2)} <span class="${cls(q.chg)}">${v}</span></b></div>`;
  }).join("");

  const breadth = Object.entries(b.breadth || {}).map(([mk, v]) => `
    <div><div class="bl"><span>${mk === "us" ? "미국 유니버스" : "한국 유니버스"}</span><span><span class="up">상승 ${v.up}</span> · <span class="down">하락 ${v.down}</span></span></div>
    <div class="bar"><i style="width:${(v.up / v.total) * 100}%"></i></div></div>`).join("");

  const movers = Object.entries(b.movers || {}).map(([mk, v]) => `
    <div class="sec"><h2>${mk === "us" ? "미국" : "한국"} 등락 상위</h2><div class="panel">
    ${[...v.top.slice(0, 3), ...v.bottom.slice(0, 3)].map((m) => `
      <button class="row" data-t="${esc(m.ticker)}"><div><div class="t1">${esc(m.name)}</div><div class="t2">${esc(isKR(m.ticker) ? code(m.ticker) : m.ticker)}</div></div><span></span><div class="num"><b class="${cls(m.chg)}">${sgn(m.chg)}</b></div></button>`).join("")}
    </div></div>`).join("");

  const watch = (w?.rows || []).map((r) => `
    <button class="row" data-t="${esc(r.ticker)}">
      <div><div class="t1">${esc(r.name)} ${verdict(r.verdict)}</div><div class="t2">${price(r.tech?.last, r.ticker)} · ${esc(r.tech?.ta?.setup_label || "")} · RSI ${nf(r.tech?.rsi14)}</div></div>
      ${spark(r.tech?.spark?.slice(-60), 56, 26)}
      <div class="num"><b class="${cls(r.tech?.chg_1d)}">${sgn(r.tech?.chg_1d)}</b><span class="${cls(r.tech?.chg_5d)}">5일 ${sgn(r.tech?.chg_5d, 1)}</span></div>
    </button>`).join("");

  const diag = (b.diagnosis || []).map((x) => `
    <div class="diag"><h3>${esc(x.name)} <span class="${cls(x.chg_1d)}">${sgn(x.chg_1d)}</span></h3>
    <div>매도신호 성격: <span class="kind">${esc(x.type)}</span></div>
    <ul>${(x.evidence || []).map((e) => `<li>${esc(e)}</li>`).join("")}</ul></div>`).join("");

  el.innerHTML = `
    ${briefSeg()}
    <div class="dateline"><h1>${d.getMonth() + 1}월 ${d.getDate()}일 ${dow}요일 국장 장전</h1><p>전일 종가 기준 · ${when(b.generated)} 생성</p></div>
    <div class="tape">${idx.slice(0, 2).map((q) => tick(q, false)).join("")}${idx.slice(2).map((q) => tick(q, false)).join("")}</div>
    <div class="macro">${macro}</div>
    ${b.ai_summary ? `<div class="sec"><h2>AI 시황 요약</h2><div class="ai">${esc(b.ai_summary)}</div></div>` : ""}
    <div class="sec"><h2>간밤 포인트</h2><ul class="bullets">${(b.points || []).map((p) => `<li>${esc(p)}</li>`).join("") || "<li>특이사항 없음</li>"}</ul></div>
    <div class="sec"><h2>오늘 국내장 체크</h2><ul class="bullets">${(b.outlook || []).map((p) => `<li>${esc(p)}</li>`).join("")}</ul></div>
    ${geoCard(state.data.geo, state.data.geo_weekly)}
    ${earnRows(b.earnings, "실적 발표 예정 <small>국내 2일 · 미국 1일 이내</small>")}
    ${diag ? `<div class="sec"><h2>관심종목 급락 진단</h2>${diag}</div>` : ""}
    <div class="sec"><h2>관심종목 <small>${esc(when(w?.updated))}</small></h2><div class="panel">${watch || '<div class="empty">config/settings.yaml 의 watchlist 에 종목을 추가하세요.</div>'}</div></div>
    ${breadth ? `<div class="sec"><h2>시장 폭</h2><div class="panel pad breadth">${breadth}</div></div>` : ""}
    ${movers}
    ${(b.news_kr || []).length ? `<div class="sec"><h2>국내 증시 헤드라인</h2><div class="panel news">${b.news_kr.map((n) => `<a href="${esc(n.link)}" target="_blank" rel="noopener">${esc(n.title)}<small>${esc(n.publisher || "")} ${n.time ? "· " + esc(when(n.time)) : ""}</small></a>`).join("")}</div></div>` : ""}
    ${(b.news || []).length ? `<div class="sec"><h2>해외 헤드라인</h2><div class="panel news">${b.news.map((n) => `<a href="${esc(n.link)}" target="_blank" rel="noopener">${esc(n.title)}<small>${esc(n.publisher || "")} ${n.time ? "· " + esc(when(n.time)) : ""}</small></a>`).join("")}</div></div>` : ""}
    <p class="note">${esc(state.data.meta?.disclaimer || "")}</p>`;
}

/* ── 국제 정세 체크 ── */
const GEO_LV = { "매우 높음": "lv4", 높음: "lv3", 보통: "lv2", 낮음: "lv1" };
function geoCard(g, gw) {
  if (!g?.themes?.length && !gw) return "";
  const th = (g?.themes || []).slice(0, 6).map((x) => {
    const dir = x.direction >= 0.2 ? "esc" : x.direction <= -0.2 ? "de" : "mix";
    const mv = Object.entries(x.moves || {}).map(([k, v]) => `${esc(GEO_TK[k] || k)} <span class="${cls(v)}">${sgn(v, 1)}</span>`).join(" · ");
    return `<details class="geo-t">
      <summary><div class="gh"><b>${esc(x.label)}</b><span class="gs ${dir}">${esc(x.stance)}</span></div>
        <div class="gm"><span class="heat ${GEO_LV[x.level] || ""}"><i style="width:${Math.round((x.heat || 0) * 100)}%"></i></span>
        <small>관심 ${esc(x.level)} · 48시간 ${nf(x.n)}건${x.ratio ? ` (평소의 ${nf(x.ratio, 1)}배)` : ""}</small></div>
        ${(x.up?.length || x.down?.length) && Math.abs(x.intensity) >= 0.1 ? `<div class="gi">${x.up?.length ? `<span class="up">유리 ${esc(x.up.join(", "))}</span>` : ""}${x.down?.length ? `<span class="down">부담 ${esc(x.down.join(", "))}</span>` : ""}</div>` : ""}
      </summary>
      <div class="gd">${mv ? `<p>시장 반응(5일): ${mv}</p>` : ""}<p>제목 어조: 고조 ${nf(x.esc_n)} · 완화 ${nf(x.de_n)}</p>
        ${(x.headlines || []).map((n) => `<a href="${esc(n.link)}" target="_blank" rel="noopener">${esc(n.title)}<small>${esc(n.publisher || "")}</small></a>`).join("")}</div>
    </details>`;
  }).join("");
  const wk = gw?.items?.length ? `<div class="geo-wk"><h3>주간 정세 해설 <small>${esc(gw.week || "")} · 웹 검색 기반 자동 작성</small></h3>
    <ul class="bullets">${gw.items.map((i) => `<li><b>${esc(i.title)}</b> ${esc(i.text || "")}${i.impact ? `<br><small>영향: ${esc(i.impact)}</small>` : ""}${(i.sources || []).length ? `<br><small>${i.sources.map((s) => `<a href="${esc(s.url)}" target="_blank" rel="noopener">${esc(s.name || "출처")}</a>`).join(" · ")}</small>` : ""}</li>`).join("")}</ul></div>` : "";
  return `<div class="sec"><h2>국제 정세 체크 <small>뉴스 관심도 × 어조 × 시장 반응</small></h2>
    <div class="panel pad geo">${(g?.summary || []).length ? `<ul class="bullets">${g.summary.map((s) => `<li>${esc(s)}</li>`).join("")}</ul>` : ""}${th}${wk}
    <p class="note" style="margin:8px 0 0">키워드·가격 반응 기반 자동 판정이라 기사 맥락을 완전히 반영하지 못합니다. 종목별 영향은 상세 화면 ‘국제 정세 영향’에서 볼 수 있습니다.</p></div></div>`;
}
const GEO_TK = { "^SOX": "반도체지수", "^GSPC": "S&P500", "KRW=X": "원달러", "CL=F": "WTI", "BZ=F": "브렌트", "GC=F": "금", "^KS11": "코스피", "^VIX": "VIX", "^TNX": "미 10년물" };

/* ── 추천 ── */
function renderPicks() {
  const el = $("#view-picks"), p = state.data.picks, tr = state.data.track;
  const H = [["daily", "오늘"], ["weekly", "이번 주"], ["monthly", "이번 달"]];
  const M = [["all", "전체"], ["us", "미국"], ["kr", "한국"]];
  const block = p?.[state.picksH];
  const list = block?.[state.picksM] || [];
  const DESC = {
    daily: "저평가 종목 중 차트 매수 타이밍(과매도 탈출·이평선 지지·반전 캔들·다이버전스)이 나온 후보입니다. 역배열 하락추세는 제외합니다.",
    weekly: "가치·퀄리티에 이평선 추세와 모멘텀을 더한 주간 후보입니다. 매주 월요일 갱신.",
    monthly: "ROE 8% 이상·애널리스트 3명 이상 중 장기 추세(250일선)가 살아 있는 중장기 저평가 후보입니다. 매월 첫 평일 갱신.",
  };
  const card = (x, i) => {
    const tx = x.techx || {};
    const sc = tx.scores || {};
    return `
    <article class="pick">
      <div class="rank">${i + 1}</div>
      <div>
        <div class="head"><div><h3>${esc(x.name)}</h3><div class="sub">${esc(isKR(x.ticker) ? code(x.ticker) : x.ticker)} · ${esc(sector(x.sector))} · ${price(x.price, x.ticker)}</div>
          <div class="pills">${verdict(x.verdict)}${setupPill(tx.setup_label, tx.setup)}</div></div>
        <div class="score"><b>${nf(x.score, 1)}</b><small>종합점수</small>${x.adj ? `<small class="adj ${x.adj > 0 ? "up" : "down"}">백테스트 ${sgn(x.adj, 1, "")}</small>` : ""}</div></div>
      </div>
      ${coDesc(x.ticker) ? `<p class="co-desc">${esc(coDesc(x.ticker))}</p>` : ""}
      <p class="summary">${esc(x.summary || "")}</p>
      ${(x.geo_notes || []).length ? `<ul class="geo-notes">${x.geo_notes.map((n) => `<li class="${n.pol > 0 ? "pos" : "neg"}">${n.pol > 0 ? "🌐 " : "⚠️ "}${esc(n.text)}</li>`).join("")}</ul>` : ""}
      <figure class="mini" data-mini="${esc(x.ticker)}" aria-label="${esc(x.name)} 미니 차트"></figure>
      <div class="metrics">
        <div class="metric"><small>선행 PER</small><b>${nf(x.fpe, 1)}</b></div>
        <div class="metric"><small>ROE</small><b>${x.roe == null ? "-" : nf(x.roe * 100, 0) + "%"}</b></div>
        <div class="metric"><small>상승여력</small><b class="${cls(x.upside)}">${sgn(x.upside, 0)}</b></div>
        <div class="metric"><small>RSI</small><b>${nf(x.rsi)}</b></div>
      </div>
      <div class="reasons">
        <section><h4>왜 싼가 <small>펀더멘털</small></h4>
          <ul class="why">${(x.why || []).slice(0, 3).map((s) => `<li>${esc(s)}</li>`).join("") || "<li>-</li>"}</ul>
          ${(x.risk || []).length ? `<ul class="risk">${x.risk.slice(0, 2).map((s) => `<li>${esc(s)}</li>`).join("")}</ul>` : ""}</section>
        <section><h4>왜 지금인가 <small>차트 · 추세 ${nf(sc.trend)} · 타이밍 ${nf(sc.timing)}</small></h4>
          <ul class="why">${(tx.pos || []).slice(0, 3).map((s) => `<li>${esc(s)}</li>`).join("") || "<li>뚜렷한 긍정 신호 없음</li>"}</ul>
          ${(tx.neg || []).length ? `<ul class="risk">${tx.neg.slice(0, 2).map((s) => `<li>${esc(s)}</li>`).join("")}</ul>` : ""}</section>
      </div>
      ${levelsRow(tx.levels, x.ticker)}
      ${x.relaxed ? '<p class="note" style="grid-column:1/-1;margin:6px 0 0">차트 조건을 완화해 채운 후보입니다.</p>' : ""}
      <div class="pick-btns"><button class="btn ghost more" data-t="${esc(x.ticker)}">차트·재무·근거 전체 보기</button>${linkBtns(x.links || linksOf(x.ticker))}</div>
    </article>`;
  };

  const items = (tr?.items || []).slice().reverse().slice(0, 20);
  const s = tr?.summary || {};
  const bySetup = Object.entries(tr?.by_setup || {});
  el.innerHTML = `
    <div class="seg" role="group" aria-label="기간">${H.map(([k, l]) => `<button data-h="${k}" aria-pressed="${state.picksH === k}">${l}</button>`).join("")}</div>
    <div class="seg sm" role="group" aria-label="시장">${M.map(([k, l]) => `<button data-m="${k}" aria-pressed="${state.picksM === k}">${l}</button>`).join("")}</div>
    <p class="note" style="margin:0 2px 12px">${DESC[state.picksH]} ${block ? "· " + esc(when(block.generated)) + " 기준" : ""}</p>
    ${list.length ? list.map(card).join("") : `<div class="empty">조건을 통과한 종목이 없습니다. 유니버스를 넓히거나 다음 갱신을 기다려 주세요.</div>`}
    <div class="sec"><h2>추천 성과 추적</h2>
      <div class="kpis"><div class="kpi"><small>추적 건수</small><b>${nf(s.count)}</b></div>
      <div class="kpi"><small>평균 수익률</small><b class="${cls(s.avg_ret)}">${sgn(s.avg_ret, 1)}</b></div>
      <div class="kpi"><small>플러스 비율</small><b>${s.hit_rate == null ? "-" : nf(s.hit_rate, 0) + "%"}</b></div></div>
      ${predVsActual(tr)}
      ${bySetup.length ? `<div class="panel" style="margin-top:8px"><div class="row head-row"><span>차트 셋업별</span><span></span><span>평균 · 플러스 비율</span></div>${bySetup.map(([k, v]) => `
        <div class="row" style="cursor:default"><div><div class="t1">${esc(k)}</div><div class="t2">${nf(v.count)}건</div></div><span></span>
        <div class="num"><b class="${cls(v.avg_ret)}">${sgn(v.avg_ret, 1)}</b><span>${nf(v.hit_rate, 0)}%</span></div></div>`).join("")}</div>` : ""}
      <div class="panel" style="margin-top:8px">${items.map((i) => `
        <button class="row" data-t="${esc(i.ticker)}"><div><div class="t1">${esc(i.name)}</div><div class="t2">${esc(i.date)} · ${{ daily: "오늘", weekly: "주간", monthly: "월간" }[i.horizon]}${i.setup ? " · " + esc(i.setup) : ""} · 추천가 ${price(i.entry, i.ticker)}</div></div><span></span>
        <div class="num"><b class="${cls(i.ret)}">${sgn(i.ret, 1)}</b></div></button>`).join("") || '<div class="empty">추천이 쌓이면 이후 수익률을 자동으로 추적합니다.</div>'}</div>
    </div>
    <p class="note">점수는 같은 시장 안에서의 상대 순위(0~100)이고, 차트 점수는 이평선·RSI·MACD·볼린저·거래량 규칙 점수입니다. 지지·저항·손절 참고가는 기술적 기준선일 뿐 매매 지시가 아닙니다.</p>`;
  el.querySelectorAll("[data-mini]").forEach(async (fig) => {
    const d = await loadChart(fig.dataset.mini);
    if (!d || !fig.isConnected) { fig.remove(); return; }
    fig.innerHTML = TAChart.mini(d, 320, 92) + TAChart.legendHTML(Object.keys(d.ma));
  });
}

const coDesc = (t) => (state.data.screen?.rows || []).find((x) => x.ticker === t)?.desc || "";

/* ── 스크리닝 ── */
const SORTS = [
  ["score_weekly", "종합(주간) 점수", -1], ["score_daily", "오늘(타이밍) 점수", -1], ["score_monthly", "중장기 점수", -1],
  ["tech", "차트 종합 점수", -1], ["trend", "추세 점수", -1], ["timing", "매수 타이밍 점수", -1],
  ["fpe", "선행 PER 낮은순", 1], ["upside", "상승여력 높은순", -1], ["roe", "ROE 높은순", -1],
  ["rsi", "RSI 낮은순", 1], ["chg_1d", "일간 하락순", 1], ["chg_1d_desc", "일간 상승순", -1],
];
const FILTERS = [
  ["all", "전체"], ["pullback", "눌림목"], ["turn", "추세 전환"], ["reversal", "반등 시도"], ["breakout", "돌파"],
  ["aligned", "완전 정배열"], ["golden", "골든크로스"], ["oversold", "과매도"], ["div_bull", "상승 다이버전스"], ["squeeze", "스퀴즈"],
  ["downtrend", "역배열"],
];
function renderScreen() {
  const el = $("#view-screen"), rows = state.data.screen?.rows || [];
  if (!el.dataset.ready) {
    el.innerHTML = `
      <div class="tools"><input id="q" type="search" placeholder="종목명·티커 검색" aria-label="검색" autocomplete="off">
      <select id="sort" aria-label="정렬">${SORTS.map(([k, l]) => `<option value="${k}">${l}</option>`).join("")}</select></div>
      <div class="seg sm" role="group" aria-label="시장" id="scr-m"></div>
      <div class="chips" role="group" aria-label="차트 조건" id="scr-f"></div>
      <div class="panel" id="scr-list"></div>
      <p class="note">행을 누르면 차트 신호 체크리스트·지지/저항·팩터 점수를 볼 수 있습니다(캔들 차트는 관심종목·추천 종목). 한국 종목은 무료 데이터 특성상 컨센서스가 비어 있는 경우가 많습니다.</p>`;
    el.dataset.ready = "1";
    $("#sort").value = state.scrSort;
    $("#q").addEventListener("input", (e) => { state.scrQ = e.target.value.trim().toLowerCase(); drawScreenList(); });
    $("#sort").addEventListener("change", (e) => { state.scrSort = e.target.value; store.set("scrSort", state.scrSort); drawScreenList(); });
  }
  $("#scr-m").innerHTML = [["all", "전체"], ["us", "미국"], ["kr", "한국"]].map(([k, l]) => `<button data-sm="${k}" aria-pressed="${state.scrM === k}">${l} ${k === "all" ? rows.length : rows.filter((r) => r.market === k).length}</button>`).join("");
  $("#scr-f").innerHTML = FILTERS.map(([k, l]) => {
    const c = k === "all" ? rows.length : rows.filter((r) => (r.tags || []).includes(k)).length;
    return `<button data-sf="${k}" aria-pressed="${state.scrF === k}" ${c || k === "all" ? "" : "disabled"}>${l} <span>${c}</span></button>`;
  }).join("");
  drawScreenList();
}
function drawScreenList() {
  const rows = state.data.screen?.rows || [];
  const [key, , dir] = SORTS.find((s) => s[0] === state.scrSort) || SORTS[0];
  const k = key === "chg_1d_desc" ? "chg_1d" : key;
  const q = state.scrQ;
  const list = rows
    .filter((r) => state.scrM === "all" || r.market === state.scrM)
    .filter((r) => state.scrF === "all" || (r.tags || []).includes(state.scrF))
    .filter((r) => !q || (r.name || "").toLowerCase().includes(q) || r.ticker.toLowerCase().includes(q))
    .sort((a, b) => (a[k] == null) - (b[k] == null) || dir * ((a[k] ?? 0) - (b[k] ?? 0)));
  const metric = (r) => {
    if (k.startsWith("score") || ["tech", "trend", "timing"].includes(k)) return `<b>${nf(r[k], 1)}</b><span class="t2">점</span>`;
    if (k === "fpe") return `<b>${nf(r.fpe, 1)}</b><span class="t2">PER</span>`;
    if (k === "upside") return `<b class="${cls(r.upside)}">${sgn(r.upside, 0)}</b><span class="t2">여력</span>`;
    if (k === "roe") return `<b>${r.roe == null ? "-" : nf(r.roe * 100, 0) + "%"}</b><span class="t2">ROE</span>`;
    if (k === "rsi") return `<b>${nf(r.rsi)}</b><span class="t2">RSI</span>`;
    return `<b class="${cls(r.chg_1d)}">${sgn(r.chg_1d)}</b><span class="t2">일간</span>`;
  };
  $("#scr-list").innerHTML = list.slice(0, 200).map((r) => `
    <button class="row" data-t="${esc(r.ticker)}">
      <div><div class="t1">${esc(r.name)} ${verdict(r.verdict)}</div><div class="t2">${esc(isKR(r.ticker) ? code(r.ticker) : r.ticker)} · ${esc(r.setup_label || "")} · PER ${nf(r.fpe, 1)} · <span class="${cls(r.chg_1d)}">${sgn(r.chg_1d)}</span></div></div>
      ${spark(r.spark?.slice(-60), 56, 26)}
      <div class="num">${metric(r)}</div>
    </button>`).join("") || `<div class="empty">조건에 맞는 종목이 없습니다. 필터를 ‘전체’로 바꿔 보세요.</div>`;
}

/* ── 상세 시트 ── */
const CAT_ORDER = ["이평선", "추세", "모멘텀", "변동성", "거래량", "캔들", "가격대"];
function checklist(sigs) {
  if (!sigs.length) return '<div class="empty">차트 신호 데이터가 없습니다.</div>';
  const by = {};
  sigs.forEach((s) => (by[s.cat] = by[s.cat] || []).push(s));
  return CAT_ORDER.filter((c) => by[c]).map((c) => `
    <div class="ck"><h4>${c}</h4><ul>${by[c].map((s) => `<li class="${s.pol > 0 ? "pos" : s.pol < 0 ? "neg" : "neu"}"><i aria-hidden="true">${s.pol > 0 ? "＋" : s.pol < 0 ? "－" : "·"}</i><span class="sr">${s.pol > 0 ? "긍정" : s.pol < 0 ? "부정" : "중립"}</span>${esc(s.text)}</li>`).join("")}</ul></div>`).join("");
}
/* 기업 개요 · 재무 · 정세 섹션 */
const COUNTRY_KO = { "United States": "미국", "South Korea": "한국", Taiwan: "대만", Netherlands: "네덜란드", Ireland: "아일랜드", Switzerland: "스위스", "United Kingdom": "영국", Canada: "캐나다" };
function coSection(t, r, cd) {
  const co = cd?.co || {};
  const desc = co.desc || r?.desc;
  const tags = co.tags_ko || r?.ctags || [];
  const facts = [
    co.industry && ["업종", esc(co.industry)], co.country && ["국가", esc(COUNTRY_KO[co.country] || co.country)],
    co.employees && ["임직원", nf(co.employees) + "명"], co.market_cap && ["시가총액", money(co.market_cap, co.currency)],
  ].filter(Boolean);
  if (!desc && !tags.length && !facts.length) return "";
  return `<div class="sec"><h2>기업 개요</h2><div class="panel pad co">
    ${desc ? `<p class="co-d">${esc(desc)}</p>` : ""}
    ${tags.length ? `<div class="tagrow">${tags.map((x) => `<span class="tag">${esc(x)}</span>`).join("")}</div>` : ""}
    ${facts.length ? `<div class="co-f">${facts.map(([k, v]) => `<div><small>${k}</small>${v}</div>`).join("")}</div>` : ""}
    ${co.website ? `<p class="note" style="margin:8px 0 0">홈페이지: <a href="${esc(co.website)}" target="_blank" rel="noopener">${esc(co.website.replace(/^https?:\/\//, ""))}</a></p>` : ""}
    <div class="btns" style="margin-top:10px">${linkBtns(co.links || r?.links || linksOf(t))}</div>
  </div></div>`;
}
function ftable(head, rows) {
  return `<div class="ftbl"><table><thead><tr><th></th>${head.map((h) => `<th>${esc(h)}</th>`).join("")}</tr></thead>
    <tbody>${rows.map(([lab, cells, cl]) => `<tr${cl ? ` class="${cl}"` : ""}><th>${lab}</th>${cells.map((c) => `<td>${c}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`;
}
function finBars(tb, cur) {
  const rev = tb.revenue || [], op = tb.op || [];
  const vals = [...rev, ...op].filter((v) => v != null);
  if (vals.length < 2) return "";
  const hi = Math.max(...vals.map((v) => Math.abs(v))) || 1, n = tb.periods.length, W = 320, H = 96, bw = W / n;
  const y0 = H - 16, sc = (v) => (Math.abs(v) / hi) * (H - 30);
  const bars = tb.periods.map((p, i) => {
    const x = i * bw + bw * 0.18, w = bw * 0.3;
    const r = rev[i], o = op[i];
    return `${r != null ? `<rect x="${x.toFixed(1)}" y="${(y0 - sc(r)).toFixed(1)}" width="${w.toFixed(1)}" height="${sc(r).toFixed(1)}" rx="2" class="b-rev"/>` : ""}
      ${o != null ? `<rect x="${(x + w + 2).toFixed(1)}" y="${o >= 0 ? (y0 - sc(o)).toFixed(1) : y0}" width="${w.toFixed(1)}" height="${sc(o).toFixed(1)}" rx="2" class="${o >= 0 ? "b-op" : "b-neg"}"/>` : ""}
      <text x="${(i * bw + bw / 2).toFixed(1)}" y="${H - 3}" text-anchor="middle">${esc(p)}</text>`;
  }).join("");
  return `<figure class="finbars"><svg viewBox="0 0 ${W} ${H}" role="img" aria-label="매출·영업이익 추이">${bars}</svg>
    <figcaption><span><i class="b-rev"></i>매출</span><span><i class="b-op"></i>영업이익</span><small>단위 ${cur === "KRW" ? "원" : "달러"}</small></figcaption></figure>`;
}
function incomeTable(tb, cur, quarterly) {
  if (!tb?.periods?.length) return "";
  const n = tb.periods.length, idx = [...Array(n).keys()];
  const lag = quarterly ? 4 : 1;
  const opm = idx.map((i) => pctOf(tb.op?.[i], tb.revenue?.[i]));
  const npm = idx.map((i) => pctOf(tb.net?.[i], tb.revenue?.[i]));
  const g = idx.map((i) => (i >= lag ? yoy(tb.revenue?.[i], tb.revenue?.[i - lag]) : null));
  return finBars(tb, cur) + ftable(tb.periods, [
    ["매출", idx.map((i) => money(tb.revenue?.[i], cur))],
    [quarterly ? "매출 YoY" : "매출 증가율", g.map((v) => `<span class="${cls(v)}">${v == null ? "-" : sgn(v, 1)}</span>`), "sub"],
    ["영업이익", idx.map((i) => `<span class="${(tb.op?.[i] ?? 0) < 0 ? "down" : ""}">${money(tb.op?.[i], cur)}</span>`)],
    ["영업이익률", opm.map((v) => (v == null ? "-" : nf(v, 1) + "%")), "sub"],
    ["순이익", idx.map((i) => `<span class="${(tb.net?.[i] ?? 0) < 0 ? "down" : ""}">${money(tb.net?.[i], cur)}</span>`)],
    ["순이익률", npm.map((v) => (v == null ? "-" : nf(v, 1) + "%")), "sub"],
    ["EPS", idx.map((i) => epsFmt(tb.eps?.[i], cur))],
  ]);
}
function finSection(fin, cd) {
  if (!cd) return `<div class="sec"><h2>실적·재무제표</h2><div class="panel pad"><p class="note" style="margin:0">재무제표·실적 분석은 관심종목과 추천 종목에 제공됩니다. 네이버 증권 링크에서 전체 재무를 볼 수 있습니다.</p></div></div>`;
  if (!fin?.ok) return `<div class="sec"><h2>실적·재무제표</h2><div class="panel pad"><p class="note" style="margin:0">${/ETF/.test(cd.co?.ko || "") ? "ETF는 재무제표가 없습니다." : "무료 데이터에서 재무제표를 받지 못했습니다. 네이버 증권에서 확인하세요."}</p></div></div>`;
  const cur = fin.currency;
  const per = state.finP === "q" && fin.quarterly ? "q" : "a";
  const b = fin.balance, c = fin.cashflow;
  const bal = b?.periods ? ftable(b.periods, [
    ["자산총계", b.periods.map((_, i) => money(b.assets?.[i], cur))],
    ["부채총계", b.periods.map((_, i) => money(b.liab?.[i], cur))],
    ["자본총계", b.periods.map((_, i) => money(b.equity?.[i], cur))],
    ["부채비율", b.periods.map((_, i) => { const v = pctOf(b.liab?.[i], b.equity?.[i]); return v == null ? "-" : `<span class="${v >= 200 ? "down" : ""}">${nf(v, 0)}%</span>`; }), "sub"],
    ["현금성자산", b.periods.map((_, i) => money(b.cash?.[i], cur))],
    ["총차입금", b.periods.map((_, i) => money(b.debt?.[i], cur))],
    ["유동비율", b.periods.map((_, i) => { const v = pctOf(b.cur_assets?.[i], b.cur_liab?.[i]); return v == null ? "-" : nf(v, 0) + "%"; }), "sub"],
  ]) : "";
  const cf = c?.periods ? ftable(c.periods, [
    ["영업활동현금흐름", c.periods.map((_, i) => money(c.ocf?.[i], cur))],
    ["설비투자(CAPEX)", c.periods.map((_, i) => money(c.capex?.[i], cur))],
    ["잉여현금흐름(FCF)", c.periods.map((_, i) => `<span class="${(c.fcf?.[i] ?? 0) < 0 ? "down" : ""}">${money(c.fcf?.[i], cur)}</span>`)],
    ["배당 지급", c.periods.map((_, i) => (c.div?.[i] == null ? "-" : money(-c.div[i], cur)))],
    ["자사주 매입", c.periods.map((_, i) => (c.buyback?.[i] == null ? "-" : money(-c.buyback[i], cur)))],
  ]) : "";
  const sp = fin.surprises || [];
  const sur = sp.length ? ftable(sp.map((x) => x.q), [
    ["예상 EPS", sp.map((x) => epsFmt(x.est, cur))], ["실제 EPS", sp.map((x) => epsFmt(x.act, cur))],
    ["서프라이즈", sp.map((x) => `<span class="${cls(x.surp)}">${x.surp == null ? "-" : sgn(x.surp, 1)}</span>`)],
  ]) : "";
  const EK = [["0q", "이번 분기"], ["+1q", "다음 분기"], ["0y", "올해"], ["+1y", "내년"]];
  const ee = fin.eps_est || {}, re = fin.rev_est || {}, et = fin.eps_trend || {};
  const ek = EK.filter(([k]) => ee[k] || re[k]);
  const cons = ek.length ? ftable(ek.map(([, l]) => l), [
    ["EPS 컨센서스", ek.map(([k]) => epsFmt(ee[k]?.avg, cur))],
    ["EPS 성장률", ek.map(([k]) => (ee[k]?.growth == null ? "-" : `<span class="${cls(ee[k].growth)}">${sgn(ee[k].growth * 100, 1)}</span>`)), "sub"],
    ["매출 컨센서스", ek.map(([k]) => money(re[k]?.avg, cur))],
    ["매출 성장률", ek.map(([k]) => (re[k]?.growth == null ? "-" : `<span class="${cls(re[k].growth)}">${sgn(re[k].growth * 100, 1)}</span>`)), "sub"],
    ["애널리스트", ek.map(([k]) => (ee[k]?.numberOfAnalysts ? nf(ee[k].numberOfAnalysts) + "명" : "-")), "sub"],
    ["EPS 30일 전 대비", ek.map(([k]) => { const v = yoy(et[k]?.current, et[k]?.["30daysAgo"]); return v == null ? "-" : `<span class="${cls(v)}">${sgn(v, 1)}</span>`; })],
    ["EPS 90일 전 대비", ek.map(([k]) => { const v = yoy(et[k]?.current, et[k]?.["90daysAgo"]); return v == null ? "-" : `<span class="${cls(v)}">${sgn(v, 1)}</span>`; })],
  ]) : "";
  const cal = fin.calendar || {};
  const an = fin.analysis || [];
  const byCat = {};
  an.forEach((x) => (byCat[x.cat] = byCat[x.cat] || []).push(x));
  return `
    <div class="sec"><h2>실적 자동 분석 <small>수집한 재무 숫자로만 작성</small></h2><div class="panel pad">
      ${an.length ? Object.entries(byCat).map(([k, v]) => `<div class="ck"><h4>${esc(k)}</h4><ul>${v.map((x) => `<li class="${x.pol > 0 ? "pos" : x.pol < 0 ? "neg" : "neu"}"><i aria-hidden="true">${x.pol > 0 ? "＋" : x.pol < 0 ? "－" : "·"}</i>${esc(x.text)}</li>`).join("")}</ul></div>`).join("") : '<p class="note" style="margin:0">분석할 데이터가 부족합니다.</p>'}
    </div></div>
    <div class="sec"><h2>손익 추이 <small>${per === "q" ? "분기" : "연간"} · 기간 말 기준</small></h2>
      <div class="seg sm" role="group" aria-label="기간"><button data-fp="a" aria-pressed="${per === "a"}">연간</button><button data-fp="q" aria-pressed="${per === "q"}" ${fin.quarterly ? "" : "disabled"}>분기</button></div>
      <div class="panel pad">${incomeTable(per === "q" ? fin.quarterly : fin.annual, cur, per === "q") || '<p class="note" style="margin:0">손익 데이터 없음</p>'}</div></div>
    ${cons || cal.next_earnings ? `<div class="sec"><h2>컨센서스 · 추정치 변화</h2><div class="panel pad">
      ${cal.next_earnings ? `<p class="earn">다음 실적 발표 <b>${esc(cal.next_earnings)}</b>${cal.eps_avg != null ? ` · 예상 EPS ${epsFmt(cal.eps_avg, cur)}` : ""}${cal.rev_avg != null ? ` · 예상 매출 ${money(cal.rev_avg, cur)}` : ""}</p>` : ""}
      ${cons}</div></div>` : ""}
    ${sur ? `<div class="sec"><h2>실적 서프라이즈 <small>최근 분기 · EPS</small></h2><div class="panel pad">${sur}</div></div>` : ""}
    ${bal ? `<div class="sec"><h2>재무상태</h2><div class="panel pad">${bal}</div></div>` : ""}
    ${cf ? `<div class="sec"><h2>현금흐름</h2><div class="panel pad">${cf}</div></div>` : ""}`;
}
function geoSection(cd, r) {
  const notes = cd?.geo?.notes || r?.geo_notes || [];
  const score = cd?.geo?.score ?? r?.geo;
  if (score == null && !notes.length) return "";
  return `<div class="sec"><h2>국제 정세 영향 <small>오늘 기준 · 50 = 중립</small></h2><div class="panel pad">
    <div class="factor"><span>정세 점수</span><div class="track"><i style="width:${score ?? 50}%"></i></div><b>${nf(score)}</b></div>
    ${notes.length ? `<ul class="geo-notes">${notes.map((n) => `<li class="${n.pol > 0 ? "pos" : "neg"}">${n.pol > 0 ? "🌐 " : "⚠️ "}${esc(n.text)}</li>`).join("")}</ul>` : '<p class="note" style="margin:6px 0 0">지금 두드러진 정세 테마와 이 종목의 노출이 겹치지 않습니다.</p>'}
    <p class="note" style="margin:6px 0 0">점수는 아직 추천 순위에 반영하지 않고(가중치 0) 매일 기록만 합니다. 실전에서 예측력이 통계적으로 확인되면 자동 보정이 비중을 줍니다.</p>
  </div></div>`;
}
async function openDetail(t) {
  const r = (state.data.screen?.rows || []).find((x) => x.ticker === t);
  const w = (state.data.watch?.rows || []).find((x) => x.ticker === t);
  if (!r && !w) { window.open(extLink(t), "_blank", "noopener"); return; }
  const name = r?.name || w?.name || t;
  const tech = w?.tech || {};
  const dlg = $("#sheet");
  const hasChart = r?.chart || !!w;
  const paint = (cd) => {
    const ta = cd?.ta || tech.ta || {};
    const sigs = ta.signals || (r?.sig || []).map(([pol, cat, text]) => ({ pol, cat, text }));
    const lv = ta.levels || r?.levels;
    const tv = ta.values || r?.tvals || {};
    const sc = ta.scores || r?.tscores || { trend: r?.trend, timing: r?.timing, total: r?.tech };
    const setup = ta.setup || r?.setup, setupLabel = ta.setup_label || r?.setup_label, setupDesc = ta.setup_desc || r?.setup_desc;
    const bar = (label, v) => `<div class="factor"><span>${label}</span><div class="track"><i style="width:${v ?? 0}%"></i></div><b>${v == null ? "-" : nf(v)}</b></div>`;
    const kv = (l, v) => `<div><small>${l}</small>${v}</div>`;
    const slopes = { 50: tv.slope_mid, 120: tv.slope_mid2, 250: tv.slope_long };
    const slopeTxt = { 50: "10일", 120: "10일", 250: "20일" };
    $("#sheet-in").innerHTML = `
      <div class="grip"></div>
      <div class="head" style="display:flex;justify-content:space-between;align-items:start;gap:10px">
        <div><h2 style="margin:0;font-size:21px">${esc(name)}</h2><div class="t2" style="color:var(--muted);font-size:13px">${esc(isKR(t) ? code(t) : t)} · ${esc(sector(r?.sector))}</div></div>
        <div style="text-align:right"><div style="font-size:19px;font-weight:600">${price(r?.price ?? tech.last, t)}</div><div class="${cls(r?.chg_1d ?? tech.chg_1d)}">${sgn(r?.chg_1d ?? tech.chg_1d)}</div></div>
      </div>
      <div class="pills" style="margin:10px 0">${verdict(r?.verdict || w?.verdict)}${setupPill(setupLabel, setup)}</div>
      ${coSection(t, r, cd)}
      ${setupDesc ? `<div class="setup-box"><b>${esc(setupLabel)}</b> · 이평선 ${esc(ta.trend_label || r?.trend_label || "")}<p>${esc(setupDesc)}</p></div>` : ""}
      <div class="sec"><h2>차트 ${cd ? `<small>최근 ${cd.d.length}거래일 · 일봉</small>` : ""}</h2>
        ${cd ? `<div class="panel pad ta-wrap" id="ta-full"></div>` : `<div class="panel pad"><p class="note" style="margin:0">캔들 차트는 관심종목과 추천 종목에만 제공됩니다. 신호 요약은 아래에서 확인하세요.</p></div>`}
      </div>
      <div class="sec"><h2>기술적 가격대 <small>스윙 고·저점 + 이평선, ATR 기준</small></h2>${levelsRow(lv, t) || '<div class="empty">-</div>'}
        ${lv && (lv.support?.[1] || lv.resistance?.[1]) ? `<p class="note">2차 지지 ${lvTxt(lv.support?.[1], t)} · 2차 저항 ${lvTxt(lv.resistance?.[1], t)}</p>` : ""}</div>
      <div class="sec"><h2>차트 점수 <small>규칙 기반 0~100</small></h2><div class="panel pad">
        ${bar("추세", sc.trend)}${bar("모멘텀", sc.momentum)}${bar("타이밍", sc.timing)}${bar("차트 종합", sc.total)}</div></div>
      ${tv.ma ? `<div class="sec"><h2>이동평균선</h2><div class="panel ma-table">
        <div class="mrow mh"><span>이평선</span><span>값</span><span>주가 위치</span><span>기울기</span></div>
        ${Object.keys(tv.ma).map((k) => `<div class="mrow"><span><i class="${k === "120" ? "dash" : ""}" style="--c:var(--ma${k});background:${k === "120" ? "none" : `var(--ma${k})`}"></i>${k}일</span><span>${price(tv.ma[k], t)}</span><span class="${cls(tv.dist?.[k])}">${tv.dist?.[k] == null ? "-" : (tv.dist[k] >= 0 ? "위 " : "아래 ") + sgn(tv.dist[k], 1)}</span><span class="${cls(slopes[k])}">${slopes[k] == null ? "-" : sgn(slopes[k], 1) + ` <small>${slopeTxt[k]}</small>`}</span></div>`).join("")}
      </div></div>` : ""}
      <div class="sec"><h2>차트 신호 체크리스트</h2><div class="panel pad">${checklist(sigs)}</div></div>
      <div class="sec"><h2>보조지표</h2><div class="kv">
        ${kv("RSI(14)", nf(tv.rsi, 1))}${kv("20일 이격도", nf(tv.disparity, 1))}
        ${kv("MACD / 시그널", `${nf(tv.macd, 2)} / ${nf(tv.macd_signal, 2)}`)}${kv("MACD 히스토그램", `<span class="${cls(tv.macd_hist)}">${nf(tv.macd_hist, 2)}</span>`)}
        ${kv("ADX(14)", nf(tv.adx, 0))}${kv("+DI / −DI", `${nf(tv.pdi, 0)} / ${nf(tv.mdi, 0)}`)}
        ${kv("볼린저 %B", nf(tv.pctb, 2))}${kv("밴드폭 6개월 백분위", tv.bw_pct == null ? "-" : nf(tv.bw_pct) + "%")}
        ${kv("거래량(20일 평균 대비)", tv.vol_ratio == null ? "-" : nf(tv.vol_ratio, 2) + "배")}${kv("상승/하락일 거래량비", nf(tv.updown_vol, 2))}
        ${kv("ATR(14) 변동폭", tv.atr_pct == null ? "-" : nf(tv.atr_pct, 1) + "%")}${kv("52주 고점 대비", sgn(r?.dd52 ?? tech.drawdown_52w, 1))}
      </div></div>
      ${r ? `<div class="sec"><h2>펀더멘털 팩터 <small>시장 내 백분위</small></h2><div class="panel pad">
        ${bar("가치", r.value)}${bar("퀄리티", r.quality)}${bar("상승여력", r.target)}${bar("성장", r.growth)}</div></div>
      <div class="sec"><h2>펀더멘털 지표</h2><div class="kv">
        ${kv("선행 PER", nf(r?.fpe, 1) + (r?.sector_fpe_median ? ` <small style="display:inline">섹터 ${nf(r.sector_fpe_median, 1)}</small>` : ""))}
        ${kv("PEG", nf(r?.peg, 2))}${kv("PBR", nf(r?.pb, 2))}${kv("ROE", r?.roe == null ? "-" : nf(r.roe * 100, 1) + "%")}
        ${kv("영업이익률", r?.opm == null ? "-" : nf(r.opm * 100, 1) + "%")}${kv("부채비율(D/E)", r?.de == null ? "-" : nf(r.de, 0) + "%")}
        ${kv("목표가 상승여력", `<span class="${cls(r?.upside)}">${sgn(r?.upside, 1)}</span>`)}${kv("애널리스트", r?.analysts ? nf(r.analysts) + "명" : "-")}
      </div></div>` : ""}
      ${finSection(cd?.fin, cd)}
      ${geoSection(cd, r)}
      ${w?.diag ? `<div class="sec"><h2>급락 성격 진단</h2><div class="diag"><span class="kind">${esc(w.diag.type)}</span><ul>${w.diag.evidence.map((e) => `<li>${esc(e)}</li>`).join("")}</ul></div></div>` : ""}
      <div class="btns"><a class="btn" href="${esc(extLink(t))}" target="_blank" rel="noopener">네이버 증권에서 보기</a><button class="btn ghost" id="sheet-close">닫기</button></div>`;
    $("#sheet-close").onclick = () => dlg.close();
    $("#sheet-in").querySelectorAll("[data-fp]").forEach((b) => (b.onclick = () => { state.finP = b.dataset.fp; store.set("finP", state.finP); const el = $("#sheet-in"), y = el.scrollTop; paint(cd); el.scrollTop = y; }));
    if (cd) TAChart.full($("#ta-full"), cd, lv);
  };
  if (!dlg.open) dlg.showModal();  // 먼저 열어야 차트가 실제 폭으로 그려짐
  paint(state.charts[t] || null);
  if (hasChart && state.charts[t] === undefined) {
    const cd = await loadChart(t);
    if (cd && dlg.open) paint(cd);
  }
}

/* ── 배당 (앱에서 플랜 구성 변경 → divplan.js 로 즉시 재계산) ── */
function dvOpts() {
  const d = state.data.dividend || {}, df = d.defaults || {};
  const base = { ...DivPlan.DEFAULTS, capital: df.capital || DivPlan.DEFAULTS.capital, n: df.max_picks || 6,
    singleCap: df.single_cap ?? 0.35, ccCap: df.covered_call_max_weight ?? 0.2, taxUs: df.tax_us ?? 0.15, taxKr: df.tax_kr ?? 0.154 };
  const saved = store.get("divPlan", {}) || {};
  if (state.capital && saved.capital == null) saved.capital = state.capital;  // 예전 투자금 설정 이어받기
  return { ...base, ...saved };
}
function dvSave(patch) {
  const cur = store.get("divPlan", {}) || {};
  store.set("divPlan", { ...cur, ...patch });
  renderDiv();
}
const dvDots = (ms) => `<span class="dots" aria-label="지급월 ${ms.join(",")}">${Array.from({ length: 12 }, (_, i) => `<i class="${ms.includes(i + 1) ? "on" : ""}"></i>`).join("")}</span>`;
const dvSeg = (key, val, opts) => `<div class="seg sm" role="group">${opts.map(([v, l]) => `<button data-dv="${key}" data-v="${v}" aria-pressed="${String(val) === String(v)}">${l}</button>`).join("")}</div>`;
const dvSel = (key, val, opts) => `<select data-dvs="${key}">${opts.map(([v, l]) => `<option value="${v}" ${Math.abs(Number(val) - Number(v)) < 1e-9 ? "selected" : ""}>${l}</option>`).join("")}</select>`;
function renderDiv() {
  const el = $("#view-div"), d = state.data.dividend;
  if (!d?.candidates?.length) { el.innerHTML = `<div class="empty">배당 데이터가 아직 없습니다. 평일 아침 실행 후 생성됩니다.</div>`; return; }
  if (!d.candidates[0].dps) { el.innerHTML = `<div class="empty">배당 데이터가 이전 형식입니다. 다음 아침 실행(또는 코드 갱신 직후 자동 실행) 후 설정 화면이 열립니다.</div>`; return; }
  const o = dvOpts();
  const P = DivPlan.build(d.candidates, o, d.usdkrw);
  const inPlan = new Set(P.rows.map((r) => r.ticker));
  const max = Math.max(...P.monthly, 1), nowM = new Date().getMonth();
  const shares = (r) => (o.whole ? nf(r.shares) : nf(r.shares, 2)) + "주";
  const fq = state.dvQ || "", ff = state.dvF || "all";
  const cands = d.candidates.filter((c) => (ff === "all" || (ff === "monthly" ? c.freq >= 12 : ff === "etf" ? c.type === "etf" : ff === "stock" ? c.type !== "etf" : c.market === ff))
    && (!fq || c.name.toLowerCase().includes(fq) || c.ticker.toLowerCase().includes(fq)));
  const candBtns = (c) => {
    if (o.mode === "manual") return `<button class="mini-btn ${inPlan.has(c.ticker) ? "on" : ""}" data-dva="${inPlan.has(c.ticker) ? "unselect" : "select"}" data-t2="${esc(c.ticker)}">${inPlan.has(c.ticker) ? "✓ 담김" : "+ 담기"}</button>`;
    const pin = o.pinned.includes(c.ticker), ex = o.excluded.includes(c.ticker);
    return ex ? `<button class="mini-btn" data-dva="unexclude" data-t2="${esc(c.ticker)}">제외 해제</button>`
      : `<button class="mini-btn ${pin ? "on" : ""}" data-dva="${pin ? "unpin" : "pin"}" data-t2="${esc(c.ticker)}">${pin ? "📌 고정됨" : "📌 고정"}</button><button class="mini-btn" data-dva="exclude" data-t2="${esc(c.ticker)}">제외</button>`;
  };
  el.innerHTML = `
    <div class="dateline"><h1>배당 플랜</h1><p>${esc(when(d.generated))} 가격 기준 · 환율 ${nf(d.usdkrw, 1)}원 · 설정은 이 기기에 저장</p></div>
    <div class="kpis">
      <div class="kpi"><small>연간 세후 배당</small><b>${krw(P.annual)}</b></div>
      <div class="kpi"><small>월 평균</small><b>${krw(P.annual / 12)}</b></div>
      <div class="kpi"><small>세후 수익률</small><b>${P.yieldNet == null ? "-" : nf(P.yieldNet, 2) + "%"}</b><small>지급월 ${P.covered.length}/12</small></div>
    </div>
    ${o.target > 0 ? `<div class="panel pad goal">월 <b>${krw(o.target)}</b> 목표 → 이 구성(세후 ${P.yieldNet == null ? "-" : nf(P.yieldNet, 2) + "%"})이면 필요 투자금 <b>${P.need ? krw(P.need) : "-"}</b>${P.need ? ` <span class="${P.need > o.capital ? "down" : "up"}">(현재 투자금 대비 ${P.need > o.capital ? "+" + krw(P.need - o.capital) + " 부족" : "충족"})</span>` : ""}</div>` : ""}
    <details class="panel pad dv-set" ${store.get("dvOpen", true) ? "open" : ""}>
      <summary><b>플랜 설정</b> <small>${o.mode === "auto" ? `자동 ${o.n}종목` : `직접 ${o.selected.length}종목`} · ${{ score: "점수 비례", equal: "균등", yield: "배당률 비례", custom: "직접 입력" }[o.weighting]} · 단일 ≤${Math.round(o.singleCap * 100)}%</small></summary>
      <div class="dv-grid">
        <label>투자금(원)<input data-dvi="capital" inputmode="numeric" value="${nf(o.capital)}"></label>
        <label>목표 월 배당(원, 선택)<input data-dvi="target" inputmode="numeric" value="${o.target ? nf(o.target) : ""}" placeholder="예: 1,000,000"></label>
      </div>
      <div class="dv-row"><span>구성 방식</span>${dvSeg("mode", o.mode, [["auto", "자동 추천"], ["manual", "직접 고르기"]])}</div>
      ${o.mode === "auto" ? `<div class="dv-row"><span>종목 수</span><div class="stepper"><button data-dva="n-" aria-label="줄이기">−</button><b>${o.n}</b><button data-dva="n+" aria-label="늘리기">＋</button></div></div>
      <div class="dv-row"><span>시장</span>${dvSeg("market", o.market, [["all", "전체"], ["us", "미국"], ["kr", "국내"]])}</div>
      <div class="dv-row"><span>유형</span>${dvSeg("kind", o.kind, [["all", "전체"], ["etf", "ETF"], ["stock", "개별주"]])}</div>
      <div class="dv-row"><span>최소 배당률</span>${dvSel("minYield", o.minYield, [[0, "제한 없음"], [1, "1% 이상"], [2, "2% 이상"], [3, "3% 이상"], [4, "4% 이상"], [5, "5% 이상"]])}</div>` : ""}
      <div class="dv-row"><span>비중 방식</span>${dvSeg("weighting", o.weighting, [["score", "점수"], ["equal", "균등"], ["yield", "배당률"], ["custom", "직접"]])}</div>
      ${o.weighting !== "custom" ? `<div class="dv-row"><span>단일 종목 상한</span>${dvSel("singleCap", o.singleCap, [[0.2, "20%"], [0.25, "25%"], [0.3, "30%"], [0.35, "35%"], [0.4, "40%"], [0.5, "50%"], [1, "제한 없음"]])}</div>` : ""}
      <div class="dv-row"><span>커버드콜 합산</span>${dvSel("ccCap", o.ccCap, [[0, "제외"], [0.1, "10% 이하"], [0.2, "20% 이하"], [0.3, "30% 이하"], [0.4, "40% 이하"]])}</div>
      <div class="dv-row"><span>세금</span>${dvSeg("taxPreset", Math.abs(o.taxKr) < 1e-9 ? "pension" : "normal", [["normal", "일반계좌"], ["pension", "연금·ISA"]])}</div>
      <div class="dv-grid">
        <label>미국 원천징수(%)<input data-dvi="taxUs" inputmode="decimal" value="${nf(o.taxUs * 100, 1)}"></label>
        <label>국내 배당소득세(%)<input data-dvi="taxKr" inputmode="decimal" value="${nf(o.taxKr * 100, 1)}"></label>
      </div>
      <label class="dv-check"><input type="checkbox" data-dvc="whole" ${o.whole ? "checked" : ""}> 정수 주 단위로 계산(남는 금액은 현금)</label>
      <div class="btns"><button class="btn ghost sm" data-dva="reset">기본값으로</button>${o.mode === "manual" ? `<button class="btn ghost sm" data-dva="fromauto">자동 추천으로 채우기</button><button class="btn ghost sm" data-dva="clear">모두 빼기</button>` : ""}</div>
      <p class="note" style="margin:6px 0 0">연금저축·ISA도 미국 배당의 현지 원천징수 15%는 그대로 떼이고, 국내 배당소득세만 과세이연·비과세 됩니다(한도·요건 확인 필요).</p>
    </details>
    ${P.warn.map((w) => `<p class="note warn">⚠️ ${esc(w)}</p>`).join("")}
    <div class="sec"><h2>월별 예상 입금 <small>만원 · 배당락월 기준 · 세후</small></h2>
      <div class="panel pad"><div class="months">${P.monthly.map((v, i) => `<div class="m ${i === nowM ? "now" : ""}" title="${i + 1}월 ${krw(v)}"><small>${v ? nf(v / 1e4, 0) : ""}</small><i style="height:${(v / max) * 100}%"></i><span>${i + 1}</span></div>`).join("")}</div>
      ${P.missing.length ? `<p class="note" style="margin:6px 0 0">배당이 없는 달: ${P.missing.join("·")}월 — 해당 월에 지급하는 종목을 고정하면 채워집니다.</p>` : ""}</div>
    </div>
    <div class="sec"><h2>플랜 구성 <small>${nf(P.rows.length)}종목 · 투자 ${krw(P.invested)}${P.cash > 1 ? ` · 현금 ${krw(P.cash)}` : ""}</small></h2><div class="panel">${P.rows.map((r) => `
      <div class="row dv-r" style="cursor:default"><div><div class="t1">${esc(r.name)}${r.pinned ? ' <span class="tag">📌</span>' : ""}${r.covered_call ? '<span class="tag">커버드콜</span>' : ""}</div>
        <div class="t2">${esc(isKR(r.ticker) ? code(r.ticker) : r.ticker)} · 배당률 ${nf(r.yield, 2)}% · 5년 성장 ${r.cagr5 == null ? "-" : sgn(r.cagr5, 1)} ${dvDots(r.months)}</div>
        <div class="t2">${shares(r)} × ${price(r.price, r.ticker)} · 연 세후 ${krw(r.annual)}</div></div><span></span>
        <div class="num">${o.weighting === "custom" ? `<label class="pct"><input data-dvw="${esc(r.ticker)}" inputmode="decimal" value="${nf(o.custom[r.ticker] ?? 100 / P.rows.length, 0)}">%</label><small class="t2">실제 ${nf(r.weight, 0)}%</small>` : `<b>${nf(r.weight, 0)}%</b>`}<span>${krw(r.alloc)}</span>
        <button class="mini-btn" data-dva="${o.mode === "manual" ? "unselect" : "exclude"}" data-t2="${esc(r.ticker)}" aria-label="${esc(r.name)} 빼기">빼기</button></div></div>`).join("") || '<div class="empty">담긴 종목이 없습니다. 아래 후보에서 담아 주세요.</div>'}</div>
      ${P.rows.some((r) => (r.notes || []).length) ? `<ul class="bullets" style="margin-top:8px">${P.rows.flatMap((r) => (r.notes || []).map((n) => `<li>${esc(r.name)}: ${esc(n)}</li>`)).join("")}</ul>` : ""}
    </div>
    ${P.upcoming.length ? `<div class="sec"><h2>다가오는 예상 배당락 <small>작년 일정 기준 추정</small></h2><div class="panel">${P.upcoming.map((u) => `<div class="row" style="cursor:default"><div class="t1">${esc(u.name)}</div><span></span><div class="num"><b>${u.date.getMonth() + 1}/${u.date.getDate()}</b><span>${u.days}일 후</span></div></div>`).join("")}</div></div>` : ""}
    <div class="sec"><h2>후보 ${nf(d.candidates.length)}종목 <small>${o.mode === "manual" ? "담기로 직접 구성" : "📌 고정 = 반드시 포함 · 제외 = 빼고 계산"}</small></h2>
      <div class="tools"><input id="dvq" type="search" placeholder="종목명·티커 검색" value="${esc(state.dvQ || "")}" autocomplete="off"></div>
      <div class="chips">${[["all", "전체"], ["etf", "ETF"], ["stock", "개별주"], ["monthly", "월배당"], ["us", "미국"], ["kr", "국내"]].map(([k, l]) => `<button data-dvf="${k}" aria-pressed="${ff === k}">${l}</button>`).join("")}</div>
      <div class="panel">${cands.slice(0, 120).map((c) => `
      <div class="row dv-r ${o.excluded.includes(c.ticker) && o.mode === "auto" ? "dim" : ""}" style="cursor:default"><div><div class="t1">${esc(c.name)}${c.covered_call ? '<span class="tag">커버드콜</span>' : ""}${inPlan.has(c.ticker) ? '<span class="tag on">플랜</span>' : ""}</div>
      <div class="t2">${esc(isKR(c.ticker) ? code(c.ticker) : c.ticker)} · 배당률 ${nf(c.yield, 2)}% · 성향 ${c.payout == null ? "-" : nf(c.payout, 0) + "%"} · 연 ${c.freq}회 ${dvDots(c.months)}</div>
      <div class="t2">${esc(c.pool || "")}${c.cagr5 != null ? ` · 5년 성장 ${sgn(c.cagr5, 1)}` : ""}${c.cuts ? ` · 감배 ${c.cuts}회` : ""}</div></div><span></span>
      <div class="num"><b>${nf(c.score, 1)}</b><span class="t2">점</span>${candBtns(c)}</div></div>`).join("") || '<div class="empty">조건에 맞는 후보가 없습니다.</div>'}</div></div>
    <p class="note">${esc(d.method)} 앱 설정을 바꾸면 같은 규칙(점수 + 새로 채워지는 지급월 보너스, 상한 재분배)으로 이 기기에서 다시 계산합니다.</p>`;
}
function dvBind() {
  const el = $("#view-div");
  el.addEventListener("click", (e) => {
    const b = e.target.closest("[data-dv],[data-dva],[data-dvf]");
    if (!b) return;
    const o = dvOpts();
    if (b.dataset.dvf) { state.dvF = b.dataset.dvf; renderDiv(); return; }
    if (b.dataset.dv) {
      const k = b.dataset.dv, v = b.dataset.v;
      if (k === "taxPreset") { dvSave(v === "pension" ? { taxKr: 0, taxUs: 0.15 } : { taxKr: 0.154, taxUs: 0.15 }); return; }
      if (k === "mode" && v === "manual" && !o.selected.length) {  // 직접 고르기로 바꾸면 현재 자동 구성을 출발점으로
        const cur = DivPlan.build(state.data.dividend.candidates, o, state.data.dividend.usdkrw).rows.map((r) => r.ticker);
        dvSave({ mode: v, selected: cur }); return;
      }
      if (k === "weighting" && v === "custom" && !Object.keys(o.custom).length) {
        const cur = DivPlan.build(state.data.dividend.candidates, o, state.data.dividend.usdkrw).rows;
        dvSave({ weighting: v, custom: Object.fromEntries(cur.map((r) => [r.ticker, Math.round(r.weight)])) }); return;
      }
      dvSave({ [k]: v }); return;
    }
    const a = b.dataset.dva, t = b.dataset.t2;
    const without = (arr) => arr.filter((x) => x !== t);
    if (a === "n-") dvSave({ n: Math.max(1, o.n - 1) });
    else if (a === "n+") dvSave({ n: Math.min(15, o.n + 1) });
    else if (a === "pin") dvSave({ pinned: [...without(o.pinned), t], excluded: without(o.excluded) });
    else if (a === "unpin") dvSave({ pinned: without(o.pinned) });
    else if (a === "exclude") dvSave({ excluded: [...without(o.excluded), t], pinned: without(o.pinned) });
    else if (a === "unexclude") dvSave({ excluded: without(o.excluded) });
    else if (a === "select") dvSave({ selected: [...without(o.selected), t] });
    else if (a === "unselect") dvSave({ selected: without(o.selected) });
    else if (a === "clear") dvSave({ selected: [] });
    else if (a === "fromauto") dvSave({ selected: DivPlan.build(state.data.dividend.candidates, { ...o, mode: "auto" }, state.data.dividend.usdkrw).rows.map((r) => r.ticker) });
    else if (a === "reset") { store.set("divPlan", {}); state.capital = null; store.set("capital", null); renderDiv(); toast("기본 설정으로 되돌렸습니다"); }
  });
  el.addEventListener("change", (e) => {
    const t = e.target;
    const num = (v) => Number(String(v).replace(/[^0-9.]/g, ""));
    if (t.dataset.dvi) {
      const k = t.dataset.dvi, v = num(t.value);
      if (k === "taxUs" || k === "taxKr") { if (v >= 0 && v <= 60) dvSave({ [k]: v / 100 }); return; }
      if (k === "capital" && v > 0) { dvSave({ capital: v }); toast(`투자금 ${krw(v)} 기준으로 다시 계산했습니다`); return; }
      if (k === "target") { dvSave({ target: v || 0 }); return; }
    }
    if (t.dataset.dvs) { dvSave({ [t.dataset.dvs]: Number(t.value) }); return; }
    if (t.dataset.dvc) { dvSave({ [t.dataset.dvc]: t.checked }); return; }
    if (t.dataset.dvw) { const o = dvOpts(); dvSave({ custom: { ...o.custom, [t.dataset.dvw]: num(t.value) } }); }
  });
  el.addEventListener("input", (e) => { if (e.target.id === "dvq") { state.dvQ = e.target.value.trim().toLowerCase(); const pos = e.target.selectionStart; renderDiv(); const q = $("#dvq"); q.focus(); q.setSelectionRange(pos, pos); } });
  el.addEventListener("toggle", (e) => { if (e.target.classList?.contains("dv-set")) store.set("dvOpen", e.target.open); }, true);
}

/* ── 규칙 검증 (백테스트 + 자가 보정) ── */
const SETUP_KO = { breakout: "신고가·돌파형", pullback: "상승추세 속 눌림목", turn: "장기 추세 전환 초기", reversal: "하락 후 반등 시도",
  squeeze: "변동성 수축", uptrend: "정배열 추세 지속", range: "박스권 횡보", downtrend: "역배열 하락추세", neutral: "중립" };
const FACTOR_KO = { total: "차트 종합", trend: "추세", momentum: "모멘텀", timing: "타이밍", value: "가치", quality: "퀄리티", target: "상승여력", growth: "성장", revision: "이익 추정치 변화", geo: "국제 정세" };
const SIG_KO = (code) => {
  const m = {
    ma_aligned: "이평선 완전 정배열", ma_reversed: "이평선 역배열", ma_long_aligned: "장기 정배열 속 조정", ma_short_turn: "단기 정배열 전환",
    ma_long_pos: "250일선 위치·기울기", disp_hot: "이격도 과열", disp_cold: "이격도 침체", rsi_oversold: "RSI 과매도", rsi_overbought: "RSI 과매수",
    rsi_bull: "RSI 50 이상", rsi_weak: "RSI 약세", rsi_reclaim30: "RSI 30 상향 돌파", rsi_lose70: "RSI 70 하향 이탈", div_bull: "RSI 상승 다이버전스",
    div_bear: "RSI 하락 다이버전스", macd_golden: "MACD 상향 돌파", macd_dead: "MACD 하향 돌파", macd_state: "MACD 위치", macd_hist_improve: "MACD 히스토그램 개선",
    macd_hist_fade: "MACD 히스토그램 축소", adx_trend: "ADX 강한 추세", bb_reclaim: "볼린저 하단 재진입", bb_lower: "볼린저 하단 접근",
    bb_break_up: "볼린저 상단 돌파", bb_upper_hot: "볼린저 상단 과열", vol_up: "거래량 동반 상승", vol_down: "거래량 동반 하락",
    vol_accum: "매집 우위", vol_distrib: "분산 우위", candle_hammer: "망치형", candle_bull_engulf: "상승장악형", candle_bear_engulf: "하락장악형",
    candle_long_bull: "장대양봉", candle_long_bear: "장대음봉", high52_new: "52주 신고가", low52_new: "52주 신저가", falling_knife: "떨어지는 칼날",
  };
  if (m[code]) return m[code];
  let x = code.match(/^cross_(\d+)_(\d+)_(golden|dead)$/);
  if (x) return `${x[1]}·${x[2]}일선 ${x[3] === "golden" ? "골든" : "데드"}크로스`;
  x = code.match(/^break_(\d+)_(up|down)$/);
  if (x) return `${x[1]}일선 ${x[2] === "up" ? "상향 돌파" : "하향 이탈"}`;
  x = code.match(/^ma_(support|resist)_(\d+)$/);
  if (x) return `${x[2]}일선 ${x[1] === "support" ? "지지" : "저항"}`;
  return code;
};
function dbar(v, max) {
  if (v == null) return '<div class="dbar"><span></span></div>';
  const w = Math.min(50, (Math.abs(v) / (max || 1)) * 50);
  return `<div class="dbar" aria-hidden="true"><i class="${v >= 0 ? "pos" : "neg"}" style="width:${w}%;${v >= 0 ? "left:50%" : `right:50%`}"></i></div>`;
}
const sigBadge = (t) => (t == null ? "" : Math.abs(t) >= 2 ? `<span class="sigb">유의</span>` : "");
/* PDCA · 예측 vs 실제 */
const HN = { daily: "오늘", weekly: "이번 주", monthly: "이번 달" };
function winTiles(m) {
  return `<div class="kpis">${["daily", "weekly", "monthly"].map((h) => { const v = m?.[h]; return `<div class="kpi"><small>${HN[h]} 추천 승률</small><b class="${v ? (v.win_rate >= 50 ? "up" : "down") : ""}">${v ? nf(v.win_rate, 0) + "%" : "-"}</b><small>${v ? `${nf(v.count)}건 확정 · 초과 ${sgn(v.avg_ex, 1)}` : "채점 대기"}</small></div>`; }).join("")}</div>`;
}
function predVsActual(tr) {
  const fin = (tr?.items || []).filter((i) => i.final).slice(-15).reverse();
  return `<h2 style="font-size:15px;margin:16px 0 8px">예측 vs 실제 <small style="font-weight:400;color:var(--muted)">기간 도달 시 확정 · 지수 대비</small></h2>
    ${winTiles(tr?.matured)}
    <div class="panel" style="margin-top:8px">${fin.map((i) => `
      <button class="row" data-t="${esc(i.ticker)}"><div><div class="t1">${i.final.win ? "✓" : "✗"} ${esc(i.name)}</div>
      <div class="t2">${esc(i.date.slice(5).replace("-", "/"))} ${HN[i.horizon]} 추천 · 점수 ${nf(i.score, 1)} · ${nf(i.final.days)}거래일 보유${i.setup ? " · " + esc(i.setup) : ""}</div></div><span></span>
      <div class="num"><b class="${cls(i.final.ex ?? i.final.ret)}">${sgn(i.final.ex ?? i.final.ret, 1)}</b><span>절대 ${sgn(i.final.ret, 1)}</span></div></button>`).join("") ||
      '<div class="empty">추천 후 5·20·60거래일이 지나면 여기서 예측이 맞았는지(✓/✗) 확정됩니다. 첫 결과는 다음 주에 나옵니다.</div>'}</div>`;
}
const CL_CLASS = { 전략: "cl-strat", 운영: "cl-ops", 되돌림: "cl-revert", 유지: "cl-keep" };
function changelogBlock(cl) {
  const items = (cl?.items || []).slice(0, 12);
  return `<div class="sec"><h2>자동 변경 이력 <small>Claude가 직접 반영·되돌린 내용</small></h2>
    <div class="panel">${items.map((c) => `<div class="alert"><i></i><div>
      <div><span class="cl ${CL_CLASS[c.type] || ""}">${esc(c.type || "")}</span> ${esc(c.title || "")}</div>
      ${c.detail ? `<div style="font-size:13.5px;color:var(--ink-2);margin-top:2px">${esc(c.detail)}</div>` : ""}
      <small>${esc(c.date || "")}${c.evidence ? " · 근거: " + esc(c.evidence) : ""}${c.commit ? ` · <a href="https://github.com/hkbaboOD/jangjeon-note/commit/${esc(c.commit)}" target="_blank" rel="noopener">${esc(c.commit)}</a>` : ""}</small>
    </div></div>`).join("") || '<div class="empty">아직 기록이 없습니다. 매주 토요일 점검 후 바뀐 내용(또는 유지 결정)이 여기 쌓입니다.</div>'}</div>
    ${(cl?.items || []).length > 12 ? `<p class="note">전체 이력: <a href="https://github.com/hkbaboOD/jangjeon-note/blob/main/docs/CHANGELOG.md" target="_blank" rel="noopener">docs/CHANGELOG.md</a></p>` : ""}</div>`;
}
function pdcaBlock(p, rv, tr) {
  if (!p) return `<div class="sec"><h2>이번 주 PDCA</h2><div class="panel pad"><p class="note" style="margin:0">첫 주간 PDCA 리포트는 토요일 09:10 자동 보정 후 생성됩니다. 그 전에도 매일 예측은 기록되고 있습니다.</p></div></div>`;
  const m = p.check?.matured || {}, dw = p.check?.win_delta || {};
  const wins = ["daily", "weekly", "monthly"].filter((h) => m[h]).map((h) => `${HN[h]} ${nf(m[h].win_rate, 0)}%${dw[h] ? ` <span class="dl ${cls(dw[h])}">${sgn(dw[h], 0, "%p")}</span>` : ""}`).join("<br>") || "채점 대기 중";
  const ch = p.act?.changes || [];
  return `
    <div class="sec"><h2>이번 주 PDCA <small>${esc(p.week)} · 자동</small></h2>
      <div class="pdca">
        <div><b>P 계획</b><p>모델 v${nf(p.plan?.version ?? 0)}</p><small>${p.plan?.changed ? "가중치를 조정했습니다" : "근거 부족 → 현재 모델 유지"}</small></div>
        <div><b>D 실행</b><p>예측 기록 ${nf(p.do?.days_recorded)}일</p><small>이번 주 신규 추천 ${nf(p.do?.new_picks)}건 · 진행 중 ${nf(p.do?.open_picks)}건</small></div>
        <div><b>C 점검</b><p>${wins}</p><small>이번 주 확정 ${nf(p.check?.closed_this_week?.length || 0)}건 · 승률 = 지수 대비 플러스 비율</small></div>
        <div><b>A 개선</b><p>${ch.length ? `${nf(ch.length)}건 조정` : "유지"}</p><small>${esc(ch[0] || "유의한 근거가 없어 바꾸지 않았습니다")}</small></div>
      </div>
      ${(p.next || []).length ? `<ul class="bullets" style="margin-top:8px">${p.next.map((n) => `<li>${esc(n)}</li>`).join("")}</ul>` : ""}
    </div>
    ${rv ? `<div class="sec"><h2>Claude 주간 점검 <small>${esc(rv.date || "")}</small></h2><div class="panel pad review">
      ${rv.summary ? `<p style="margin:0 0 6px">${esc(rv.summary)}</p>` : ""}
      ${(rv.findings || []).length ? `<h4>발견</h4><ul>${rv.findings.map((f) => `<li>${esc(f)}</li>`).join("")}</ul>` : ""}
      ${(rv.actions || []).length ? `<h4>조치</h4><ul>${rv.actions.map((f) => `<li>${esc(f)}</li>`).join("")}</ul>` : ""}
      ${(rv.proposals || []).length ? `<h4>다음 주 계획</h4><ul>${rv.proposals.map((f) => `<li>${esc(f)}</li>`).join("")}</ul>` : ""}
    </div></div>` : ""}`;
}

/* 실전 예측력 블록: 추이 · 채점 · 모델 선택 */
const HZ_KO = { daily: "오늘(5일)", weekly: "이번 주(20일)", monthly: "이번 달(60일)" };
const LINE_C = { daily: "var(--ma20)", weekly: "var(--ma50)", monthly: "var(--ink)" };
function icTrend(hist) {
  const pts = (hist || []).filter((e) => ["daily", "weekly", "monthly"].some((k) => e[`ic_${k}`] != null));
  if (pts.length < 2) return "";
  const W = 640, H = 170, L = 8, R = 92, T = 10, B = 24;
  const vals = pts.flatMap((e) => ["daily", "weekly", "monthly"].map((k) => e[`ic_${k}`]).filter((v) => v != null));
  const m = Math.max(0.05, ...vals.map(Math.abs)) * 1.15;
  const x = (i) => L + (i / (pts.length - 1)) * (W - L - R), y = (v) => T + ((m - v) / (2 * m)) * (H - T - B);
  const ends = [];
  const lines = ["daily", "weekly", "monthly"].map((k) => {
    const seg = pts.map((e, i) => [i, e[`ic_${k}`]]).filter(([, v]) => v != null);
    if (seg.length < 2) return "";
    const d = seg.map(([i, v], j) => `${j ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join("");
    const [li, lv] = seg[seg.length - 1];
    ends.push({ k, x: x(li) + 8, y: y(lv) + 4, v: lv });
    return `<path d="${d}" fill="none" stroke="${LINE_C[k]}" stroke-width="2"/>
      ${seg.map(([i, v]) => `<circle cx="${x(i)}" cy="${y(v)}" r="4" fill="${LINE_C[k]}" stroke="var(--surface)" stroke-width="2"><title>${pts[i].date} · ${HZ_KO[k]} IC ${v >= 0 ? "+" : ""}${v.toFixed(3)} · 모델 v${pts[i].version ?? "-"}</title></circle>`).join("")}`;
  }).join("");
  // 끝 라벨 겹침 방지: 위에서부터 13px 간격 확보
  ends.sort((a, b) => a.y - b.y).forEach((e, i, arr) => { if (i && e.y - arr[i - 1].y < 13) e.y = arr[i - 1].y + 13; });
  const labels = ends.map((e) => `<text x="${e.x}" y="${e.y}" class="ax" style="fill:var(--ink-2)">${HZ_KO[e.k].split("(")[0]} ${e.v >= 0 ? "+" : ""}${e.v.toFixed(3)}</text>`).join("");
  return `<svg viewBox="0 0 ${W} ${H}" class="ta" role="img" aria-label="주간 예측력(IC) 추이">
    <line x1="${L}" x2="${W - R}" y1="${y(0)}" y2="${y(0)}" class="grid ref"/>
    <text x="${W - R + 8}" y="${y(0) + 4}" class="ax">0</text>
    ${lines}${labels}
    <text x="${L}" y="${H - 6}" class="ax">${pts[0].date.slice(5).replace("-", "/")}</text>
    <text x="${W - R}" y="${H - 6}" class="ax" text-anchor="end">${pts[pts.length - 1].date.slice(5).replace("-", "/")}</text></svg>`;
}
function forwardBlock(fw, sc, md) {
  const days = fw?.days || 0;
  const hist = sc?.history || [];
  const trend = icTrend(hist);
  const cont = md?.contest || {};
  const keys = ["daily", "weekly", "monthly"];
  const comp = keys.map((k) => ({ k, c: fw?.recent?.[k] || fw?.composite?.[k], t3: fw?.top3?.[k] }));
  const facs = ["value", "quality", "target", "growth", "momentum", "trend", "timing"];
  return `
    <div class="sec"><h2>예측력 추이 <small>앱이 매일 낸 예측을 실제 결과로 채점 · 주간</small></h2>
      <div class="panel pad">${trend ? trend + `<div class="ta-keys">${keys.map((k) => `<span><i style="background:${LINE_C[k]}"></i>${HZ_KO[k]}</span>`).join("")}</div>` :
        `<p style="margin:0">예측 기록 <b>${nf(days)}일째</b>입니다. 매일 아침 전 종목의 점수를 기록하고, 기간이 지나면 실제 수익률로 채점합니다.</p>
         <ul class="bullets" style="margin-top:8px;padding:8px 8px 8px 26px;background:var(--surface-2)">
           <li>오늘(5일) 추천: 약 2달 뒤부터 실전 채점으로 보정</li><li>이번 주(20일): 약 3달 뒤부터</li><li>이번 달(60일): 약 9달 뒤부터</li>
           <li>그 전까지는 10년 백테스트로 차트 비중만 조정합니다</li></ul>`}</div>
      <p class="note">선이 0보다 위에서 점점 올라가면 예측이 실제로 맞아 가고 있다는 뜻입니다. 점을 누르면 날짜·모델 버전이 보입니다.</p>
    </div>
    <div class="sec"><h2>실전 채점 <small>최근 60일 · 종합점수 순위와 이후 수익률의 상관</small></h2>
      <div class="panel ma-table"><div class="mrow mh"><span>추천</span><span>예측력 IC</span><span>Top3 초과수익</span><span>채점 일수</span></div>
      ${comp.map(({ k, c, t3 }) => `<div class="mrow"><span>${HZ_KO[k]}</span><span class="${c && Math.abs(c.t) >= 2 ? cls(c.ic) : ""}">${c ? (c.ic >= 0 ? "+" : "") + c.ic.toFixed(3) + ` <small>t ${c.t.toFixed(1)}</small>` : "-"}</span><span class="${cls(t3?.avg_ex)}">${t3 ? sgn(t3.avg_ex, 2) : "-"}</span><span>${nf(fw?.matured?.[{ daily: "5", weekly: "20", monthly: "60" }[k]] ?? 0)}일</span></div>`).join("")}</div>
      ${fw?.ic && Object.keys(fw.ic.value || {}).length ? `<div class="panel ma-table" style="margin-top:8px"><div class="mrow mh"><span>팩터(실전)</span><span>5일</span><span>20일</span><span>60일</span></div>
      ${facs.map((f) => `<div class="mrow"><span>${FACTOR_KO[f]}</span>${["5", "20", "60"].map((h) => { const v = fw.ic[f]?.[h]; return `<span class="${v && Math.abs(v.t) >= 2 ? cls(v.ic) : ""}">${v ? (v.ic >= 0 ? "+" : "") + v.ic.toFixed(3) + ` <small>t ${v.t.toFixed(1)}</small>` : "-"}</span>`; }).join("")}</div>`).join("")}</div>` : ""}
    </div>
    <div class="sec"><h2>모델 선택 <small>최근 검증 구간에서 현재·기본·새 후보 비교</small></h2>
      <div class="panel ma-table">${Object.keys(cont).length ? `<div class="mrow mh"><span>추천</span><span>현재</span><span>기본</span><span>새 후보</span></div>
      ${keys.filter((k) => cont[k]).map((k) => { const c = cont[k]; return `<div class="mrow"><span>${HZ_KO[k]}</span>${["현재", "기본", "새 후보"].map((n) => `<span class="${c.chosen === n ? "chosen" : ""}">${c.scores[n] == null ? "-" : (c.scores[n] >= 0 ? "+" : "") + c.scores[n].toFixed(3)}${c.chosen === n ? " ✓" : ""}</span>`).join("")}</div>`; }).join("")}` :
      `<p class="note" style="margin:10px 0">실전 기록이 학습·검증 구간으로 나눌 만큼 쌓이면, 매주 세 모델을 겨뤄 더 정확한 쪽만 채택하고 나빠지면 기본으로 되돌립니다.</p>`}</div>
    </div>`;
}

function renderLab() {
  const el = $("#view-lab"), bt = state.data.backtest, md = state.data.model, tr = state.data.track;
  const fw = state.data.forward, sc = state.data.scorecard;
  if (!bt || !bt.ok) {
    el.innerHTML = `<div class="dateline"><h1>규칙 검증</h1></div><div class="empty">백테스트 결과가 아직 없습니다. 매주 토요일 09:10에 자동 실행되며, GitHub Actions에서 job ‘backtest’로 바로 실행할 수도 있습니다.</div>`;
    return;
  }
  const H = state.labH, M = state.labM;
  const setups = Object.entries(bt.by_setup || {}).map(([k, b]) => ({ k, st: (b[M] || {})[H]?.ex, ab: (b[M] || {})[H]?.abs }))
    .filter((x) => x.st).sort((a, b) => b.st.avg - a.st.avg);
  const maxS = Math.max(0.5, ...setups.map((x) => Math.abs(x.st.avg)));
  const q = (bt.quintiles?.[state.labF] || {})[H] || [];
  const maxQ = Math.max(0.3, ...q.filter((v) => v != null).map(Math.abs));
  const sigs = Object.entries(bt.by_signal || {}).map(([k, b]) => ({ k, st: b[H]?.ex })).filter((x) => x.st).sort((a, b) => b.st.avg - a.st.avg);
  const sigShow = sigs.length > 12 ? [...sigs.slice(0, 7), ...sigs.slice(-5)] : sigs;
  const maxG = Math.max(0.5, ...sigShow.map((x) => Math.abs(x.st.avg)));
  const base = bt.base?.[M === "all" ? "us" : M]?.[H]?.abs;
  const hz = { "5": "daily", "20": "weekly", "60": "monthly" }[H];
  const W = md?.weights?.[hz] || {}, D = md?.defaults?.[hz] || {};
  const adj = md?.setup_adj?.[hz] || {};
  const keys = [...new Set([...Object.keys(D), ...Object.keys(W)])];
  const liveSetup = Object.entries(tr?.by_setup || {});

  el.innerHTML = `
    <div class="dateline"><h1>규칙 검증</h1><p>${esc(bt.period?.[0] || "")} ~ ${esc(bt.period?.[1] || "")} · ${nf(bt.tickers)}종목 · 표본 ${nf(bt.samples)}개 · ${esc(when(bt.generated))} 실행</p></div>
    <div class="kpis">
      <div class="kpi"><small>모델 버전</small><b>v${nf(md?.version ?? 0)}</b></div>
      <div class="kpi"><small>최근 보정</small><b>${md?.log?.[0]?.date ? esc(md.log[0].date.slice(5).replace("-", "/")) : "-"}</b></div>
      <div class="kpi"><small>실추천 지수 대비</small><b class="${cls(tr?.summary?.avg_ex)}">${sgn(tr?.summary?.avg_ex, 1)}</b></div>
    </div>
    ${pdcaBlock(state.data.pdca, state.data.pdca_review, tr)}
    ${changelogBlock(state.data.changelog)}
    ${forwardBlock(fw, sc, md)}
    <div class="seg" role="group" aria-label="보유 기간">${[["5", "5일 후"], ["20", "20일 후"], ["60", "60일 후"]].map(([k, l]) => `<button data-lh="${k}" aria-pressed="${H === k}">${l}</button>`).join("")}</div>
    <div class="seg sm" role="group" aria-label="시장">${[["all", "전체"], ["us", "미국"], ["kr", "한국"]].map(([k, l]) => `<button data-lm="${k}" aria-pressed="${M === k}">${l}</button>`).join("")}</div>

    <div class="sec"><h2>차트 셋업별 성과 <small>같은 날 전체 평균 대비 초과수익 · 비용 차감</small></h2>
      <div class="panel lab-list">${setups.map((x) => `
        <div class="lrow"><div class="ln"><b>${esc(SETUP_KO[x.k] || x.k)}</b><small>${nf(x.st.n)}건 · 승률 ${nf(x.st.win, 0)}% · t ${nf(x.st.t, 1)} ${sigBadge(x.st.t)}${adj[x.k] ? ` · 가감점 ${sgn(adj[x.k], 1, "")}` : ""}</small></div>
        ${dbar(x.st.avg, maxS)}<div class="lv ${cls(x.st.avg)}">${sgn(x.st.avg, 2)}</div></div>`).join("") || '<div class="empty">표본이 30개 이상인 셋업이 없습니다.</div>'}</div>
      <p class="note">${base ? `참고: 같은 기간 표본 전체의 평균 절대수익률 ${sgn(base.avg, 2)}(${H}일). ` : ""}‘유의’는 중첩 표본을 보정한 t값 2 이상입니다. 자가 보정은 유의한 셋업에만 가감점을 줍니다.</p>
    </div>

    <div class="sec"><h2>점수가 높을수록 더 올랐나 <small>날짜별 5분위, 초과수익</small></h2>
      <div class="chips" role="group" aria-label="점수 종류">${["total", "trend", "momentum", "timing"].map((k) => `<button data-lf="${k}" aria-pressed="${state.labF === k}">${FACTOR_KO[k]}</button>`).join("")}</div>
      <div class="panel pad"><div class="qbars">${q.map((v, i) => `<div class="qb"><span class="${cls(v)}">${v == null ? "-" : sgn(v, 2)}</span><div class="qcol"><i class="${v >= 0 ? "pos" : "neg"}" style="height:${v == null ? 0 : (Math.abs(v) / maxQ) * 50}%;${v >= 0 ? "bottom:50%" : "top:50%"}"></i></div><small>${i === 0 ? "하위 20%" : i === 4 ? "상위 20%" : `${i + 1}분위`}</small></div>`).join("")}</div></div>
      <p class="note">왼쪽(낮은 점수)에서 오른쪽(높은 점수)으로 갈수록 막대가 올라가면 점수가 실제로 유효하다는 뜻입니다.</p>
    </div>

    <div class="sec"><h2>팩터 예측력(IC) <small>점수 순위와 이후 수익률 순위의 상관</small></h2>
      <div class="panel ma-table"><div class="mrow mh"><span>점수</span><span>5일</span><span>20일</span><span>60일</span></div>
      ${["total", "trend", "momentum", "timing"].map((f) => `<div class="mrow"><span>${FACTOR_KO[f]}</span>${["5", "20", "60"].map((h) => { const v = bt.ic?.[f]?.[h]; return `<span class="${v && Math.abs(v.t) >= 2 ? cls(v.ic) : ""}">${v ? (v.ic >= 0 ? "+" : "") + v.ic.toFixed(3) + ` <small>t ${v.t.toFixed(1)}</small>` : "-"}</span>`; }).join("")}</div>`).join("")}</div>
      <p class="note">주식에서 IC 0.02~0.05면 의미 있는 수준으로 봅니다. t 2 이상인 값만 색으로 표시했습니다.</p>
    </div>

    ${sigShow.length ? `<div class="sec"><h2>개별 신호 성과 <small>${H}일 초과수익 상·하위</small></h2>
      <div class="panel lab-list">${sigShow.map((x) => `
        <div class="lrow"><div class="ln"><b>${esc(SIG_KO(x.k))}</b><small>${nf(x.st.n)}건 · 승률 ${nf(x.st.win, 0)}% · t ${nf(x.st.t, 1)} ${sigBadge(x.st.t)}</small></div>
        ${dbar(x.st.avg, maxG)}<div class="lv ${cls(x.st.avg)}">${sgn(x.st.avg, 2)}</div></div>`).join("")}</div></div>` : ""}

    <div class="sec"><h2>현재 추천 가중치 <small>${{ daily: "오늘", weekly: "이번 주", monthly: "이번 달" }[hz]} 추천 · 기본값 대비</small></h2>
      <div class="panel ma-table"><div class="mrow mh w3"><span>팩터</span><span>기본</span><span>현재</span></div>
      ${keys.map((k) => { const d = D[k] || 0, w = W[k] ?? d; return `<div class="mrow w3"><span>${FACTOR_KO[k] || k}</span><span>${nf(d * 100)}%</span><span class="${w - d > 0.005 ? "up" : w - d < -0.005 ? "down" : ""}">${nf(w * 100)}%${Math.abs(w - d) > 0.005 ? ` <small>${sgn((w - d) * 100, 0, "%p")}</small>` : ""}</span></div>`; }).join("")}</div>
      <p class="note">${esc(md?.rules || "")}</p>
    </div>

    <div class="sec"><h2>자가 보정 기록</h2><div class="panel">${(md?.log || []).slice(0, 15).map((c) => `<div class="alert"><i></i><div><div>${esc(c.text)}</div><small>${esc(c.date)}</small></div></div>`).join("") || '<div class="empty">아직 바뀐 가중치가 없습니다. 유의한 근거가 쌓이면 매주 조금씩 조정됩니다.</div>'}</div></div>

    ${liveSetup.length ? `<div class="sec"><h2>실제 추천 성과 <small>추천 후 지수 대비 초과수익</small></h2><div class="panel">${liveSetup.map(([k, v]) => `
      <div class="row" style="cursor:default"><div><div class="t1">${esc(k)}</div><div class="t2">${nf(v.count)}건 · 플러스 ${nf(v.hit_rate, 0)}%</div></div><span></span>
      <div class="num"><b class="${cls(v.avg_ex)}">${sgn(v.avg_ex, 1)}</b><span>절대 ${sgn(v.avg_ret, 1)}</span></div></div>`).join("")}</div>
      <p class="note">셋업별 실추천이 30건 이상 쌓이면 백테스트 결과와 섞어 가감점에 반영합니다.</p></div>` : ""}

    <div class="sec"><h2>읽을 때 주의</h2><ul class="bullets">
      <li>차트 규칙만 검증합니다. PER·목표가 같은 과거 시점 펀더멘털은 무료 데이터가 없어 백테스트하지 않습니다.</li>
      <li>오늘 남아 있는 대형주로 과거를 본 결과라 절대 수익률은 부풀려집니다(생존 편향). 초과수익을 기준으로 보세요.</li>
      <li>다음날 시가 진입, ${H}거래일 뒤 종가 청산, 왕복 비용 한국 ${bt.cost?.kr}%·미국 ${bt.cost?.us}% 차감 기준입니다.</li>
      <li>과거 성과가 미래를 보장하지 않습니다. 시장 국면이 바뀌면 유효했던 셋업도 약해질 수 있습니다.</li>
    </ul></div>`;
}

/* ── 알림 ── */
const isStandalone = () => window.matchMedia("(display-mode: standalone)").matches || navigator.standalone === true;
function renderAlerts() {
  const el = $("#view-alerts"), a = state.data.alerts;
  const items = a?.items || [];
  const pushReady = "serviceWorker" in navigator && "PushManager" in window;
  let setup;
  if (!CFG.vapidPublicKey) {
    setup = `<p>푸시 알림을 쓰려면 GitHub 저장소에 VAPID 키를 등록해야 합니다(README 4단계). 등록 전에도 이 화면에서 알림 기록은 볼 수 있습니다.</p>`;
  } else if (!isStandalone() && /iPhone|iPad/.test(navigator.userAgent)) {
    setup = `<p>아이폰은 홈 화면에 추가한 앱에서만 푸시를 받을 수 있습니다.</p>
      <ol class="steps"><li>사파리 하단 공유 버튼 누르기</li><li>‘홈 화면에 추가’ 선택</li><li>홈 화면의 장전노트 아이콘으로 다시 열고 이 탭에서 알림 켜기</li></ol>`;
  } else if (!pushReady) {
    setup = `<p>이 브라우저는 웹 푸시를 지원하지 않습니다. iOS 16.4 이상 사파리에서 홈 화면 앱으로 열어 주세요.</p>`;
  } else {
    setup = `<p>알림을 켜면 이 기기 전용 구독 정보가 만들어집니다. 복사해서 GitHub Secret <b>PUSH_SUBSCRIPTIONS</b>에 붙여넣으면 장전 브리핑과 신호 알림이 이 폰으로 옵니다.</p>
      <div class="btns"><button class="btn" id="push-on">알림 켜기</button><button class="btn ghost" id="push-test">테스트 알림</button></div>
      <div id="sub-out"></div>`;
  }
  el.innerHTML = `
    <div class="sec" style="margin-top:6px"><h2>푸시 알림 설정</h2><div class="panel pad">${setup}</div></div>
    <div class="sec"><h2>신호 기록 <small>${esc(when(a?.updated))}</small></h2><div class="panel">
      ${items.map((x) => `<div class="alert ${esc(x.level)}"><i></i><div><div>${esc(x.msg)}</div><small>${esc(when(x.time))} · ${price(x.price, x.ticker)}</small></div></div>`).join("") || '<div class="empty">아직 발생한 신호가 없습니다. 장중 30분마다 관심종목을 점검합니다.</div>'}
    </div></div>
    <p class="note">규칙: 일간 ±5% 급변, RSI 30/70·30 상향 돌파, 20·50 / 50·120 / 50·250일선 크로스, 250일선 돌파·이탈, 볼린저 하단 재진입, 52주 신고가, RSI 다이버전스, 차트 셋업(눌림목·반등·돌파·추세 전환), 평균 목표가 도달, 5단계 판정 변경, 개별 가격 알림. config/settings.yaml에서 바꿀 수 있습니다.</p>`;
  $("#push-on")?.addEventListener("click", enablePush);
  $("#push-test")?.addEventListener("click", async () => {
    const reg = await navigator.serviceWorker.ready;
    if (Notification.permission !== "granted") { toast("먼저 알림을 켜 주세요"); return; }
    reg.showNotification("장전노트", { body: "이 기기에서 알림이 정상 표시됩니다.", icon: "icons/icon-192.png" });
  });
}
function b64ToU8(b64) {
  const pad = "=".repeat((4 - (b64.length % 4)) % 4);
  const raw = atob((b64 + pad).replace(/-/g, "+").replace(/_/g, "/"));
  return Uint8Array.from([...raw].map((c) => c.charCodeAt(0)));
}
async function enablePush() {
  try {
    const perm = await Notification.requestPermission();
    if (perm !== "granted") { toast("알림 권한이 거부되었습니다. 설정 > 알림에서 허용해 주세요"); return; }
    const reg = await navigator.serviceWorker.ready;
    const sub = (await reg.pushManager.getSubscription()) ||
      (await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: b64ToU8(CFG.vapidPublicKey) }));
    const json = JSON.stringify(sub);
    $("#sub-out").innerHTML = `<pre class="sub">${esc(json)}</pre><div class="btns"><button class="btn" id="copy-sub">구독 정보 복사</button></div>`;
    $("#copy-sub").onclick = async () => {
      try { await navigator.clipboard.writeText(json); toast("복사했습니다. GitHub Secret에 붙여넣으세요"); }
      catch { toast("복사 실패 — 위 텍스트를 길게 눌러 복사하세요"); }
    };
  } catch (e) {
    toast("알림 설정 실패: " + e.message);
  }
}

/* ── 공통 ── */
function renderAll() {
  const m = state.data.meta;
  $("#stamp").innerHTML = m?.morning ? `국장 ${esc(when(m.morning))}${m.evening ? ` · 미장 ${esc(when(m.evening))}` : ""}${m.alerts ? `<br>알림 ${esc(when(m.alerts))}` : ""}` : "";
  $("#banner").innerHTML = m?.demo ? `<div class="banner">데모 데이터로 표시 중입니다. GitHub Actions 첫 실행 후 실제 시세로 바뀝니다.</div>` :
    !m ? `<div class="banner">데이터를 불러오지 못했습니다. 인터넷 연결을 확인하거나 첫 데이터 생성을 기다려 주세요.</div>` : "";
  renderBrief(); renderPicks(); renderScreen(); renderDiv(); renderLab(); renderAlerts();
}
function route() {
  let tab = (location.hash || "#brief").slice(1);
  if (tab === "brief-us" || tab === "brief-kr") { state.briefM = tab.slice(6); tab = "brief"; if (state.data.briefing) renderBrief(); }
  const valid = ["brief", "picks", "screen", "div", "lab", "alerts"].includes(tab) ? tab : "brief";
  document.querySelectorAll(".view").forEach((v) => v.classList.toggle("active", v.id === "view-" + valid));
  document.querySelectorAll(".tabbar a").forEach((a) => (a.dataset.tab === valid ? a.setAttribute("aria-current", "page") : a.removeAttribute("aria-current")));
  window.scrollTo(0, 0);
}

document.addEventListener("click", (e) => {
  const h = e.target.closest("[data-h]"), m = e.target.closest("[data-m]"), sm = e.target.closest("[data-sm]");
  if (h) { state.picksH = h.dataset.h; store.set("picksH", state.picksH); renderPicks(); return; }
  if (m) { state.picksM = m.dataset.m; store.set("picksM", state.picksM); renderPicks(); return; }
  if (sm) { state.scrM = sm.dataset.sm; store.set("scrM", state.scrM); renderScreen(); return; }
  const lh = e.target.closest("[data-lh]"), lm = e.target.closest("[data-lm]"), lf = e.target.closest("[data-lf]");
  if (lh) { state.labH = lh.dataset.lh; store.set("labH", state.labH); renderLab(); return; }
  if (lm) { state.labM = lm.dataset.lm; store.set("labM", state.labM); renderLab(); return; }
  if (lf) { state.labF = lf.dataset.lf; store.set("labF", state.labF); renderLab(); return; }
  const bm = e.target.closest("[data-bm]");
  if (bm) { state.briefM = bm.dataset.bm; renderBrief(); return; }
  const sf = e.target.closest("[data-sf]");
  if (sf) { state.scrF = sf.dataset.sf; store.set("scrF", state.scrF); renderScreen(); return; }
  const t = e.target.closest("[data-t]");
  if (t && !e.target.closest("a")) openDetail(t.dataset.t);
});
$("#sheet").addEventListener("click", (e) => { if (e.target.id === "sheet") e.target.close(); });
$("#refresh").addEventListener("click", () => load().then(() => toast("최신 데이터로 갱신했습니다")));
window.addEventListener("hashchange", route);
document.addEventListener("visibilitychange", () => { if (!document.hidden) load(); });

if ("serviceWorker" in navigator) navigator.serviceWorker.register("sw.js").catch(() => {});
dvBind();
route();
load();
