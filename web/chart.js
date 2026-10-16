/* 장전노트 차트 — 캔들 + 이동평균(20/50/120/250) + 볼린저 + 지지·저항, 거래량, RSI, MACD.
   외부 라이브러리 없이 SVG로 그리고, 터치/마우스 크로스헤어로 값을 읽습니다. */
"use strict";
window.TAChart = (() => {
  const MA = {
    20: { c: "var(--ma20)", dash: "", w: 1.5 },
    50: { c: "var(--ma50)", dash: "", w: 1.5 },
    120: { c: "var(--ma120)", dash: "5 3", w: 1.5 },
    250: { c: "var(--ma250)", dash: "", w: 2 },
  };
  const maStyle = (k) => MA[k] || { c: "var(--ink-2)", dash: "2 2", w: 1.2 };
  const fmt = (v, big) => (v == null ? "-" : Number(v).toLocaleString("ko-KR", { maximumFractionDigits: big ? 0 : v < 10 ? 3 : 2 }));
  const fmtVol = (v) => (v >= 1e8 ? (v / 1e8).toFixed(1) + "억" : v >= 1e4 ? Math.round(v / 1e4).toLocaleString("ko-KR") + "만" : String(v));

  function lin(lo, hi, top, bot) {
    const r = hi - lo || 1;
    return (v) => bot - ((v - lo) / r) * (bot - top);
  }
  function linePath(arr, x, y) {
    let d = "", pen = false;
    arr.forEach((v, i) => {
      if (v == null) { pen = false; return; }
      d += (pen ? "L" : "M") + x(i).toFixed(1) + "," + y(v).toFixed(1);
      pen = true;
    });
    return d;
  }
  function niceTicks(lo, hi, n = 4) {
    const span = hi - lo || 1;
    const raw = span / n, mag = Math.pow(10, Math.floor(Math.log10(raw)));
    const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => span / s <= n) || 10 * mag;
    const out = [];
    for (let v = Math.ceil(lo / step) * step; v <= hi; v += step) out.push(+v.toFixed(10));
    return out;
  }

  /* ── 카드용 미니 차트: 종가 + 이평선 ── */
  function mini(data, w = 320, h = 92) {
    const n = data.c.length;
    const keys = Object.keys(data.ma);
    const vals = data.c.concat(...keys.map((k) => data.ma[k])).filter((v) => v != null);
    const lo = Math.min(...vals), hi = Math.max(...vals);
    const x = (i) => 2 + (i / (n - 1)) * (w - 4);
    const y = lin(lo, hi, 4, h - 4);
    // 미니 차트에서는 종가(진한 실선)와 구분되도록 250일선을 반투명으로
    const mas = keys.map((k) => { const s = maStyle(k); return `<path d="${linePath(data.ma[k], x, y)}" fill="none" stroke="${s.c}" stroke-width="${s.w * 0.85}" stroke-dasharray="${s.dash}" opacity="${k === "250" ? 0.4 : 1}"/>`; }).join("");
    const last = data.c[n - 1];
    return `<svg viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" role="img" aria-label="최근 ${n}거래일 종가와 이동평균">
      ${mas}<path d="${linePath(data.c, x, y)}" fill="none" stroke="var(--ink)" stroke-width="1.6" stroke-linejoin="round"/>
      <circle cx="${x(n - 1)}" cy="${y(last)}" r="3" fill="var(--ink)" stroke="var(--surface)" stroke-width="2"/></svg>`;
  }
  function legendHTML(keys, withPrice = true) {
    return `<div class="ta-keys">${withPrice ? '<span><i style="background:var(--ink)"></i>종가</span>' : ""}${keys.map((k) => {
      const s = maStyle(k);
      return `<span><i class="${s.dash ? "dash" : ""}" style="--c:${s.c};background:${s.dash ? "none" : s.c};opacity:${withPrice && k === "250" ? 0.4 : 1}"></i>${k}일</span>`;
    }).join("")}</div>`;
  }

  /* ── 상세 차트 ── */
  const TOG_KEY = "chartToggles";
  function toggles(keys) {
    let t = {};
    try { t = JSON.parse(localStorage.getItem(TOG_KEY) || "{}"); } catch { /* 무시 */ }
    keys.forEach((k) => { if (t[k] === undefined) t[k] = true; });
    if (t.bb === undefined) t.bb = false;
    if (t.lv === undefined) t.lv = true;
    return t;
  }
  function saveToggles(t) { try { localStorage.setItem(TOG_KEY, JSON.stringify(t)); } catch { /* 무시 */ } }

  function full(el, data, levels) {
    const keys = Object.keys(data.ma);
    const tg = toggles(keys);
    const big = (data.c[data.c.length - 1] || 0) >= 1000;
    el.innerHTML = `
      <div class="ta-tools" role="group" aria-label="차트 표시 항목">
        ${keys.map((k) => { const s = maStyle(k); return `<button type="button" data-tg="${k}" aria-pressed="${tg[k]}"><i class="${s.dash ? "dash" : ""}" style="--c:${s.c};background:${s.dash ? "none" : s.c}"></i>${k}일</button>`; }).join("")}
        <button type="button" data-tg="bb" aria-pressed="${tg.bb}"><i class="band"></i>볼린저</button>
        <button type="button" data-tg="lv" aria-pressed="${tg.lv}"><i class="dash" style="--c:var(--muted)"></i>지지·저항</button>
      </div>
      <div class="ta-readout" aria-live="polite"></div>
      <div class="ta-svg"></div>`;
    const draw = () => {
      const W = Math.max(300, el.clientWidth || 340);
      el.querySelector(".ta-svg").innerHTML = svg(data, levels, tg, W, big);
      bind(el, data, W, big, tg);
    };
    el.querySelectorAll("[data-tg]").forEach((b) => b.addEventListener("click", () => {
      const k = b.dataset.tg; tg[k] = !tg[k]; b.setAttribute("aria-pressed", tg[k]); saveToggles(tg); draw();
    }));
    draw();
    // 화면 회전·창 크기 변경 시 실제 폭으로 다시 그림(글자 크기 유지)
    if (window.ResizeObserver) {
      let lastW = el.clientWidth;
      const ro = new ResizeObserver(() => {
        if (!el.isConnected) { ro.disconnect(); return; }
        if (Math.abs(el.clientWidth - lastW) > 16) { lastW = el.clientWidth; draw(); }
      });
      ro.observe(el);
    }
  }

  function geom(n, W) {
    const L = 4, R = 52; // 오른쪽에 가격축
    const P = { top: 6, main: 210, vol: 42, rsi: 62, macd: 62, gap: 22 };
    const y0 = P.top, y1 = y0 + P.main;
    const v0 = y1 + 8, v1 = v0 + P.vol;
    const r0 = v1 + P.gap, r1 = r0 + P.rsi;
    const m0 = r1 + P.gap, m1 = m0 + P.macd;
    const H = m1 + 18;
    const bw = (W - L - R) / n;
    return { L, R, W, H, bw, y0, y1, v0, v1, r0, r1, m0, m1, x: (i) => L + (i + 0.5) * bw };
  }

  function svg(d, levels, tg, W, big) {
    const n = d.c.length, g = geom(n, W);
    const showMA = Object.keys(d.ma).filter((k) => tg[k]);
    let vals = d.h.concat(d.l);
    showMA.forEach((k) => (vals = vals.concat(d.ma[k])));
    if (tg.bb) vals = vals.concat(d.bb.u, d.bb.l);
    vals = vals.filter((v) => v != null);
    let lo = Math.min(...vals), hi = Math.max(...vals);
    const pad = (hi - lo) * 0.04; lo -= pad; hi += pad;
    const y = lin(lo, hi, g.y0, g.y1), x = g.x;
    const out = [];

    // 가격 격자·축
    niceTicks(lo, hi, 4).forEach((t) => {
      out.push(`<line x1="${g.L}" x2="${W - g.R}" y1="${y(t)}" y2="${y(t)}" class="grid"/><text x="${W - g.R + 4}" y="${y(t) + 4}" class="ax">${fmt(t, big)}</text>`);
    });
    // 월 경계
    let prevM = null;
    d.d.forEach((ds, i) => {
      const m = ds.slice(5, 7);
      if (prevM !== null && m !== prevM) {
        out.push(`<line x1="${x(i)}" x2="${x(i)}" y1="${g.y0}" y2="${g.m1}" class="grid v"/><text x="${x(i) + 3}" y="${g.H - 4}" class="ax">${+m}월</text>`);
      }
      prevM = m;
    });
    // 볼린저 밴드
    if (tg.bb) {
      let top = "", bot = "";
      d.bb.u.forEach((u, i) => { if (u != null && d.bb.l[i] != null) { top += (top ? "L" : "M") + x(i).toFixed(1) + "," + y(u).toFixed(1); } });
      for (let i = n - 1; i >= 0; i--) if (d.bb.l[i] != null && d.bb.u[i] != null) bot += "L" + x(i).toFixed(1) + "," + y(d.bb.l[i]).toFixed(1);
      if (top) out.push(`<path d="${top}${bot}Z" class="bb"/>`);
    }
    // 지지·저항·손절 참고선
    if (tg.lv && levels) {
      // 1차 지지·저항 + 손절 참고만 선으로 (2차는 아래 목록). 라벨은 겹치지 않게 12px 간격으로 밀어냄
      const lv = [];
      const s1 = (levels.support || [])[0], r1 = (levels.resistance || [])[0];
      if (r1) lv.push({ p: r1.price, t: "저항", c: "var(--down)" });
      if (s1) lv.push({ p: s1.price, t: "지지", c: "var(--up)" });
      if (levels.stop) lv.push({ p: levels.stop.price, t: "손절 참고", c: "var(--muted)" });
      const vis = lv.filter((l) => l.p > lo && l.p < hi).sort((a, b) => b.p - a.p);
      let lastY = -Infinity;
      vis.forEach((l) => {
        const ly = y(l.p);
        const ty = Math.max(ly - 3, lastY + 12);
        lastY = ty;
        out.push(`<line x1="${g.L}" x2="${W - g.R}" y1="${ly}" y2="${ly}" stroke="${l.c}" stroke-width="1" stroke-dasharray="3 3" opacity=".85"/>
          <text x="${g.L + 2}" y="${ty}" class="lv" fill="${l.c}">${l.t} ${fmt(l.p, big)}</text>`);
      });
    }
    // 캔들
    const cw = Math.max(1, Math.min(8, g.bw * 0.62));
    for (let i = 0; i < n; i++) {
      const o = d.o[i], c = d.c[i], h = d.h[i], l = d.l[i];
      if (c == null) continue;
      const up = c >= o, col = up ? "var(--c-up)" : "var(--c-down)";
      const yt = y(Math.max(o, c)), yb = y(Math.min(o, c));
      out.push(`<line x1="${x(i)}" x2="${x(i)}" y1="${y(h)}" y2="${y(l)}" stroke="${col}" stroke-width="1"/>
        <rect x="${(x(i) - cw / 2).toFixed(1)}" y="${yt.toFixed(1)}" width="${cw.toFixed(1)}" height="${Math.max(1, yb - yt).toFixed(1)}" fill="${col}"/>`);
    }
    // 이동평균
    showMA.forEach((k) => {
      const s = maStyle(k);
      out.push(`<path d="${linePath(d.ma[k], x, y)}" fill="none" stroke="${s.c}" stroke-width="${s.w}" stroke-dasharray="${s.dash}" stroke-linejoin="round"/>`);
    });
    // 현재가 태그
    const last = d.c[n - 1];
    out.push(`<rect x="${W - g.R + 1}" y="${y(last) - 9}" width="${g.R - 2}" height="18" rx="4" fill="var(--ink)"/>
      <text x="${W - g.R + 4}" y="${y(last) + 4}" class="ax tag">${fmt(last, big)}</text>`);

    // 거래량
    const vmax = Math.max(...d.v, 1);
    const vy = lin(0, vmax, g.v0, g.v1);
    for (let i = 0; i < n; i++) {
      if (!d.v[i]) continue;
      const up = d.c[i] >= d.o[i];
      out.push(`<rect x="${(x(i) - cw / 2).toFixed(1)}" y="${vy(d.v[i]).toFixed(1)}" width="${cw.toFixed(1)}" height="${(g.v1 - vy(d.v[i])).toFixed(1)}" fill="${up ? "var(--c-up)" : "var(--c-down)"}" opacity=".45"/>`);
    }
    out.push(`<text x="${g.L + 2}" y="${g.v0 + 10}" class="pl">거래량</text>`);

    // RSI
    const ry = lin(0, 100, g.r0, g.r1);
    out.push(`<rect x="${g.L}" y="${ry(70)}" width="${W - g.L - g.R}" height="${ry(30) - ry(70)}" class="band30"/>`);
    [30, 50, 70].forEach((t) => out.push(`<line x1="${g.L}" x2="${W - g.R}" y1="${ry(t)}" y2="${ry(t)}" class="grid ${t === 50 ? "" : "ref"}"/><text x="${W - g.R + 4}" y="${ry(t) + 4}" class="ax">${t}</text>`));
    out.push(`<path d="${linePath(d.rsi, x, ry)}" fill="none" stroke="var(--ink)" stroke-width="1.5"/>`);
    out.push(`<text x="${g.L + 2}" y="${g.r0 - 6}" class="pl">RSI 14 · ${d.rsi[n - 1] == null ? "-" : d.rsi[n - 1].toFixed(0)}</text>`);

    // MACD
    const mv = d.macd.m.concat(d.macd.s, d.macd.h).filter((v) => v != null);
    const mm = Math.max(Math.abs(Math.min(...mv)), Math.abs(Math.max(...mv))) || 1;
    const my = lin(-mm, mm, g.m0, g.m1);
    out.push(`<line x1="${g.L}" x2="${W - g.R}" y1="${my(0)}" y2="${my(0)}" class="grid ref"/>`);
    for (let i = 0; i < n; i++) {
      const hv = d.macd.h[i];
      if (hv == null) continue;
      const yy = my(hv), y0 = my(0);
      out.push(`<rect x="${(x(i) - cw / 2).toFixed(1)}" y="${Math.min(yy, y0).toFixed(1)}" width="${cw.toFixed(1)}" height="${Math.max(0.5, Math.abs(yy - y0)).toFixed(1)}" fill="${hv >= 0 ? "var(--c-up)" : "var(--c-down)"}" opacity=".45"/>`);
    }
    out.push(`<path d="${linePath(d.macd.m, x, my)}" fill="none" stroke="var(--ink)" stroke-width="1.5"/>`);
    out.push(`<path d="${linePath(d.macd.s, x, my)}" fill="none" stroke="var(--ma20)" stroke-width="1.3" stroke-dasharray="4 2"/>`);
    out.push(`<text x="${g.L + 2}" y="${g.m0 - 6}" class="pl">MACD 12·26·9 — 실선 MACD, 점선 시그널</text>`);

    // 크로스헤어
    out.push(`<line class="xh" x1="0" x2="0" y1="${g.y0}" y2="${g.m1}" visibility="hidden"/>
      <circle class="xh-dot" r="3.5" cx="0" cy="0" visibility="hidden"/>
      <rect class="hit" x="${g.L}" y="${g.y0}" width="${W - g.L - g.R}" height="${g.m1 - g.y0}" fill="transparent"/>`);
    return `<svg viewBox="0 0 ${W} ${g.H}" width="${W}" height="${g.H}" class="ta" role="img" aria-label="캔들 차트, 이동평균, 거래량, RSI, MACD">${out.join("")}</svg>`;
  }

  function bind(el, d, W, big, tg) {
    const n = d.c.length, g = geom(n, W);
    const s = el.querySelector("svg"), xh = s.querySelector(".xh"), dot = s.querySelector(".xh-dot"), hit = s.querySelector(".hit");
    const ro = el.querySelector(".ta-readout");
    let vals = d.h.concat(d.l);
    Object.keys(d.ma).filter((k) => tg[k]).forEach((k) => (vals = vals.concat(d.ma[k])));
    if (tg.bb) vals = vals.concat(d.bb.u, d.bb.l);
    vals = vals.filter((v) => v != null);
    let lo = Math.min(...vals), hi = Math.max(...vals);
    const pad = (hi - lo) * 0.04; lo -= pad; hi += pad;
    const y = lin(lo, hi, g.y0, g.y1);

    const show = (i, live) => {
      const prev = i > 0 ? d.c[i - 1] : null;
      const ch = prev ? ((d.c[i] / prev - 1) * 100) : null;
      const mas = Object.keys(d.ma).filter((k) => tg[k]).map((k) => `<span><i style="background:${maStyle(k).c}"></i>${k} ${fmt(d.ma[k][i], big)}</span>`).join("");
      ro.innerHTML = `<b>${d.d[i].slice(2).replace(/-/g, ".")}</b>
        <span>시 ${fmt(d.o[i], big)}</span><span>고 ${fmt(d.h[i], big)}</span><span>저 ${fmt(d.l[i], big)}</span>
        <span>종 <b class="${ch > 0 ? "up" : ch < 0 ? "down" : ""}">${fmt(d.c[i], big)}${ch == null ? "" : ` (${ch > 0 ? "+" : ""}${ch.toFixed(2)}%)`}</b></span>
        <span>거래량 ${fmtVol(d.v[i])}</span><span>RSI ${d.rsi[i] == null ? "-" : d.rsi[i].toFixed(0)}</span>${mas}`;
      const vis = live ? "visible" : "hidden";
      xh.setAttribute("visibility", vis); dot.setAttribute("visibility", vis);
      xh.setAttribute("x1", g.x(i)); xh.setAttribute("x2", g.x(i));
      dot.setAttribute("cx", g.x(i)); dot.setAttribute("cy", y(d.c[i]));
    };
    const idx = (ev) => {
      const r = s.getBoundingClientRect();
      const px = ((ev.clientX - r.left) / r.width) * W;
      return Math.max(0, Math.min(n - 1, Math.floor((px - g.L) / g.bw)));
    };
    hit.addEventListener("pointermove", (ev) => show(idx(ev), true));
    hit.addEventListener("pointerdown", (ev) => show(idx(ev), true));
    hit.addEventListener("pointerleave", () => show(n - 1, false));
    show(n - 1, false);
  }

  return { mini, full, legendHTML };
})();
