/* Lozanska korpa — static front-end. Data comes from data/*.json (built by `python -m scraper build`). */
(() => {
  "use strict";

  const UNIT = { kg: "kg", l: "l", pc: "kom." };
  const INLINE = window.LP_DATA || null;
  const $app = document.getElementById("app");
  const state = {
    meta: null, basket: null, promos: null, index: null, history: {},
    view: "korpa", open: new Set(),
    promo: { q: "", stores: new Set(), sort: "pct", min: 0, group: "", frozen: "", soon: false, limit: 60 },
    prod: { q: "", stores: new Set(), promoOnly: false, limit: 80 },
    chartMode: {},
  };

  // ── utilities ─────────────────────────────────────────────────────────
  function h(tag, attrs, ...kids) {
    const el = document.createElement(tag);
    setAttrs(el, attrs);
    append(el, kids);
    return el;
  }
  function s(tag, attrs, ...kids) {
    const el = document.createElementNS("http://www.w3.org/2000/svg", tag);
    setAttrs(el, attrs);
    append(el, kids);
    return el;
  }
  function setAttrs(el, attrs) {
    if (!attrs) return;
    for (const [k, v] of Object.entries(attrs)) {
      if (v == null || v === false) continue;
      if (k === "class") el.setAttribute("class", v);
      else if (k === "text") el.textContent = v;
      else if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2), v);
      else el.setAttribute(k, v === true ? "" : String(v));
    }
  }
  function append(el, kids) {
    for (const k of kids.flat(Infinity)) {
      if (k == null || k === false) continue;
      el.append(k instanceof Node ? k : document.createTextNode(String(k)));
    }
  }
  function fill(el, ...kids) {
    el.textContent = "";
    append(el, kids);
    return el;
  }
  // Serbian plural: plural(5, "proizvod", "proizvoda", "proizvoda")
  function plural(n, one, few, many) {
    const a = Math.abs(n) % 10, b = Math.abs(n) % 100;
    if (a === 1 && b !== 11) return one;
    if (a >= 2 && a <= 4 && (b < 12 || b > 14)) return few;
    return many;
  }
  const store = {
    get(key, fallback) { try { const v = localStorage.getItem("lk:" + key); return v == null ? fallback : JSON.parse(v); } catch { return fallback; } },
    set(key, value) { try { localStorage.setItem("lk:" + key, JSON.stringify(value)); } catch { /* storage unavailable */ } },
  };
  async function load(name) {
    if (INLINE) {
      if (name in INLINE) return INLINE[name];
      throw new Error("nema podataka: " + name);
    }
    const r = await fetch("data/" + name, { cache: "no-cache" });
    if (!r.ok) throw new Error(name + " (HTTP " + r.status + ")");
    return r.json();
  }
  const nf2 = new Intl.NumberFormat("de-CH", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const nf0 = new Intl.NumberFormat("de-CH", { maximumFractionDigits: 0 });
  const money = (v) => (v == null || !isFinite(v) ? "—" : nf2.format(v));
  const fold = (t) => (t || "").normalize("NFKD").replace(/[̀-ͯ]/g, "").toLowerCase().replace(/œ/g, "oe").replace(/æ/g, "ae");
  const parseISO = (iso) => { const [y, m, d] = iso.split("-").map(Number); return Date.UTC(y, m - 1, d); };
  const DAY = 86400000;
  function fmtDate(iso, year) {
    if (!iso) return "—";
    const [y, m, d] = iso.slice(0, 10).split("-");
    return `${+d}. ${+m}.` + (year ? ` ${y}.` : "");
  }
  function relDay(iso) {
    if (!iso) return "nikad";
    const days = Math.round((parseISO(realToday()) - parseISO(iso.slice(0, 10))) / DAY);
    if (days <= 0) return "danas";
    if (days === 1) return "juče";
    if (days < 5) return `pre ${days} dana`;
    return fmtDate(iso, true);
  }
  function realToday() {
    const d = new Date();
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
  }
  const storeName = (k) => (state.meta && state.meta.stores[k] ? state.meta.stores[k].name : k);
  const swatch = (k, cls = "swatch") => h("span", { class: cls, style: `background:var(--s-${k})`, "aria-hidden": "true" });
  const unitLabel = (u) => UNIT[u] || u || "";

  function sticker(p, mini) {
    if (!p || !(p.pr || p.soon)) return null;
    let text = p.pl || (p.pct ? `-${Math.round(p.pct)}%` : "akcija");
    const cls = ["sticker"];
    if (mini) cls.push("mini");
    if (p.cd) cls.push("cond");
    if (p.soon) { cls.push("soon"); text = "uskoro · " + text; }
    const title = p.cd ? "Akcija uz uslov (npr. kupovina više komada ili aplikacija)" : "Akcija";
    // the store's own "-N%" wins over our rounding (1.35 instead of 1.45 is sold as -6%, not -7%)
    const short = p.pl && /^-\d+%$/.test(p.pl) ? p.pl : p.pct ? `-${Math.round(p.pct)}%` : "%";
    return h("span", { class: cls.join(" "), title, text: mini ? short : text });
  }

  // ── chart ─────────────────────────────────────────────────────────────
  function niceTicks(min, max, count) {
    if (min === max) { min -= 1; max += 1; }
    const span = max - min;
    const step0 = span / Math.max(1, count);
    const mag = Math.pow(10, Math.floor(Math.log10(step0)));
    const norm = step0 / mag;
    const step = (norm > 5 ? 10 : norm > 2 ? 5 : norm > 1 ? 2 : 1) * mag;
    const lo = Math.floor(min / step) * step;
    const hi = Math.ceil(max / step) * step;
    const ticks = [];
    for (let v = lo; v <= hi + step / 2; v += step) ticks.push(+v.toFixed(6));
    return ticks;
  }

  /**
   * Step-line chart on a time axis.
   * opts: {dates:[iso], series:[{key,label,color,values:[num|null]}], unit, bands:[[iso,iso]], height}
   */
  function lineChart(opts) {
    const wrap = h("div", { class: "chart" });
    const draw = () => {
      const width = Math.max(260, Math.round(wrap.clientWidth || 600));
      fill(wrap, renderChart(opts, width, wrap));
    };
    if ("ResizeObserver" in window) {
      let last = 0;
      new ResizeObserver((entries) => {
        const w = Math.round(entries[0].contentRect.width);
        if (w && Math.abs(w - last) > 2) { last = w; draw(); }
      }).observe(wrap);
    } else {
      requestAnimationFrame(draw);
    }
    return wrap;
  }

  function renderChart(opts, W, host) {
    const { dates, series, unit } = opts;
    const H = opts.height || 220;
    const M = { l: 46, r: 58, t: 24, b: 28 };
    const pw = W - M.l - M.r;
    const ph = H - M.t - M.b;
    const times = dates.map(parseISO);
    const t0 = times[0];
    const t1 = times[times.length - 1];
    const single = t0 === t1;
    const x = (t) => (single ? M.l + pw / 2 : M.l + ((t - t0) / (t1 - t0)) * pw);
    const vals = series.flatMap((se) => se.values.filter((v) => v != null));
    const vmin = Math.min(...vals);
    const vmax = Math.max(...vals);
    const pad = (vmax - vmin) * 0.12 || Math.max(0.05, vmax * 0.08);
    const ticks = niceTicks(Math.max(0, vmin - pad), vmax + pad, 4);
    const y0 = ticks[0];
    const y1 = ticks[ticks.length - 1];
    const y = (v) => M.t + ph - ((v - y0) / (y1 - y0 || 1)) * ph;
    const svg = s("svg", { viewBox: `0 0 ${W} ${H}`, height: H, role: "img", "aria-label": opts.label || "Grafikon cena" });

    const tstep = ticks.length > 1 ? ticks[1] - ticks[0] : 1;
    const dec = Math.max(0, Math.min(2, Math.ceil(-Math.log10(tstep) - 1e-9)));
    for (const tv of ticks) {
      svg.append(s("line", { class: "grid", x1: M.l, x2: W - M.r, y1: y(tv), y2: y(tv) }));
      svg.append(s("text", { x: M.l - 8, y: y(tv) + 4, "text-anchor": "end", text: tv.toFixed(dec) }));
    }
    svg.append(s("line", { class: "axis", x1: M.l, x2: W - M.r, y1: M.t + ph, y2: M.t + ph }));
    svg.append(s("text", { x: 0, y: 11, "text-anchor": "start", text: unit ? `CHF/${unitLabel(unit)}` : "CHF" }));

    // x ticks: first, last, and a few in between
    const xt = single ? [t0] : pickTimeTicks(t0, t1, Math.max(2, Math.floor(pw / 90)));
    for (const t of xt) {
      const iso = new Date(t).toISOString().slice(0, 10);
      svg.append(s("text", { x: x(t), y: H - 8, "text-anchor": single ? "middle" : t === t0 ? "start" : t === t1 ? "end" : "middle", text: fmtDate(iso, false) }));
    }

    // promo bands (product chart)
    for (const [a, b] of opts.bands || []) {
      const xa = x(parseISO(a));
      const xb = x(parseISO(b)) ;
      svg.append(s("rect", { x: xa, y: M.t + ph - 6, width: Math.max(4, xb - xa), height: 6, rx: 1, fill: "var(--sticker)" }));
    }

    // series
    const ends = [];
    for (const se of series) {
      let d = "";
      let prev = null;
      se.values.forEach((v, i) => {
        if (v == null) { prev = null; return; }
        const X = x(times[i]);
        const Y = y(v);
        if (prev == null) d += `M${X.toFixed(1)} ${Y.toFixed(1)}`;
        else d += `H${X.toFixed(1)}V${Y.toFixed(1)}`;
        prev = v;
      });
      if (d && !d.includes("H")) {
        // isolated points only: draw dots
      }
      svg.append(s("path", { d, fill: "none", stroke: se.color, "stroke-width": 2, "stroke-linejoin": "round", "stroke-linecap": "round" }));
      // dots for isolated observations
      se.values.forEach((v, i) => {
        if (v == null) return;
        const before = i > 0 ? se.values[i - 1] : null;
        const after = i < se.values.length - 1 ? se.values[i + 1] : null;
        if (before == null && after == null) {
          svg.append(s("circle", { cx: x(times[i]), cy: y(v), r: 4, fill: se.color, stroke: "var(--surface)", "stroke-width": 2 }));
        }
      });
      let li = -1;
      for (let i = se.values.length - 1; i >= 0; i--) if (se.values[i] != null) { li = i; break; }
      if (li >= 0) {
        svg.append(s("circle", { cx: x(times[li]), cy: y(se.values[li]), r: 4, fill: se.color, stroke: "var(--surface)", "stroke-width": 2 }));
        ends.push({ se, v: se.values[li], X: x(times[li]), Y: y(se.values[li]), last: li === se.values.length - 1 });
      }
    }
    // direct label: cheapest current value only
    const current = ends.filter((e) => e.last).sort((a, b) => a.v - b.v)[0];
    if (current) {
      svg.append(s("text", { class: "lbl", x: current.X + 8, y: current.Y + 4, text: money(current.v) }));
    }

    // hover layer
    const cross = s("line", { class: "cross", x1: 0, x2: 0, y1: M.t, y2: M.t + ph, visibility: "hidden" });
    const hit = s("rect", { class: "hit", x: M.l - 6, y: M.t, width: pw + 12, height: ph, tabindex: 0, "aria-label": "Pomeri strelicama za vrednosti po datumu" });
    svg.append(cross, hit);
    let tip = null;
    let idx = dates.length - 1;
    const show = (i) => {
      idx = Math.max(0, Math.min(dates.length - 1, i));
      const X = x(times[idx]);
      cross.setAttribute("x1", X); cross.setAttribute("x2", X); cross.setAttribute("visibility", "visible");
      if (!tip) { tip = h("div", { class: "tip", role: "status" }); host.append(tip); }
      const rows = series
        .map((se) => ({ se, v: se.values[idx] }))
        .filter((r) => r.v != null)
        .sort((a, b) => a.v - b.v);
      fill(tip, 
        h("div", { class: "d", text: fmtDate(dates[idx], true) }),
        ...(rows.length ? rows.map((r) => h("div", { class: "r" },
          h("i", { style: `background:${r.se.color}` }),
          h("b", { text: money(r.v) }),
          h("span", { text: r.se.label + (r.se.promo && r.se.promo[idx] ? " · akcija" : "") }))) : [h("div", { class: "muted", text: "nema cene tog dana" })]),
      );
      const scale = host.clientWidth / W || 1;
      const left = X * scale;
      tip.style.top = `${M.t}px`;
      if (left > host.clientWidth / 2) { tip.style.left = ""; tip.style.right = `${host.clientWidth - left + 12}px`; }
      else { tip.style.right = ""; tip.style.left = `${left + 12}px`; }
    };
    const hide = () => { cross.setAttribute("visibility", "hidden"); if (tip) { tip.remove(); tip = null; } };
    const nearest = (clientX) => {
      const rect = svg.getBoundingClientRect();
      const px = ((clientX - rect.left) / rect.width) * W;
      let best = 0;
      let bd = Infinity;
      times.forEach((t, i) => { const dd = Math.abs(x(t) - px); if (dd < bd) { bd = dd; best = i; } });
      return best;
    };
    hit.addEventListener("pointermove", (e) => show(nearest(e.clientX)));
    hit.addEventListener("pointerleave", hide);
    hit.addEventListener("focus", () => show(idx));
    hit.addEventListener("blur", hide);
    hit.addEventListener("keydown", (e) => {
      if (e.key === "ArrowLeft") { show(idx - 1); e.preventDefault(); }
      else if (e.key === "ArrowRight") { show(idx + 1); e.preventDefault(); }
      else if (e.key === "Escape") hide();
    });
    return svg;
  }

  function pickTimeTicks(t0, t1, n) {
    const out = [t0];
    const span = t1 - t0;
    for (let i = 1; i < n; i++) {
      const t = t0 + Math.round((span * i) / n / DAY) * DAY;
      if (t - out[out.length - 1] >= span / (n + 1) && t1 - t >= span / (n + 1)) out.push(t);
    }
    out.push(t1);
    return out;
  }

  function dataTable(dates, series, unit) {
    const rows = dates.map((d, i) => ({ d, i })).reverse();
    return h("div", { class: "dtable" },
      h("table", null,
        h("thead", null, h("tr", null, h("th", { text: "Datum" }), series.map((se) => h("th", { text: `${se.label} (CHF/${unitLabel(unit)})` })))),
        h("tbody", null, rows.map(({ d, i }) => h("tr", null,
          h("td", { text: fmtDate(d, true) }),
          series.map((se) => h("td", { text: se.values[i] == null ? "—" : money(se.values[i]) + (se.promo && se.promo[i] ? " %" : "") })))))));
  }

  function chartBlock(id, opts) {
    const legend = opts.series.length > 1
      ? h("div", { class: "legend" }, opts.series.map((se) => h("span", null, h("i", { style: `background:${se.color}` }), se.label)))
      : h("div", { class: "legend" }, opts.caption || "");
    const mode = state.chartMode[id] || "chart";
    const body = h("div");
    const seg = h("div", { class: "seg", role: "group", "aria-label": "Prikaz" });
    const setMode = (m) => {
      state.chartMode[id] = m;
      seg.querySelectorAll("button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.m === m)));
      fill(body, m === "table" ? dataTable(opts.dates, opts.series, opts.unit) : lineChart(opts));
    };
    for (const [m, label] of [["chart", "Grafikon"], ["table", "Tabela"]]) {
      seg.append(h("button", { type: "button", "data-m": m, onclick: () => setMode(m) }, label));
    }
    const card = h("div", { class: "chart-card" }, h("div", { class: "chart-top" }, legend, seg), body);
    setMode(mode);
    if (opts.dates.length < 2 && mode === "chart") {
      card.append(h("div", { class: "chart-empty", text: `Istorija počinje ${fmtDate(opts.dates[0], true)} — grafikon se dopunjuje posle svakog dnevnog preuzimanja.` }));
    }
    return card;
  }

  function sparkline(dates, values) {
    const W = 88, H = 30, P = 4;
    const pts = values.map((v, i) => [i, v]).filter((p) => p[1] != null);
    const svg = s("svg", { class: "spark", viewBox: `0 0 ${W} ${H}`, "aria-hidden": "true" });
    if (!pts.length) return svg;
    const vs = pts.map((p) => p[1]);
    const lo = Math.min(...vs), hi = Math.max(...vs);
    const times = dates.map(parseISO);
    const t0 = times[0], t1 = times[times.length - 1];
    const X = (i) => (t1 === t0 ? W - P : P + ((times[i] - t0) / (t1 - t0)) * (W - 2 * P));
    const Y = (v) => (hi === lo ? H / 2 : H - P - ((v - lo) / (hi - lo)) * (H - 2 * P));
    let d = "";
    pts.forEach(([i, v], k) => { d += (k ? `H${X(i).toFixed(1)}V${Y(v).toFixed(1)}` : `M${X(i).toFixed(1)} ${Y(v).toFixed(1)}`); });
    if (pts.length > 1) {
      svg.append(s("path", { d: `${d}V${H - 1}H${X(pts[0][0]).toFixed(1)}Z`, fill: "var(--accent)", opacity: 0.1 }));
      svg.append(s("path", { d, fill: "none", stroke: "var(--accent)", "stroke-width": 1.5, "stroke-linejoin": "round" }));
    }
    const [li, lv] = pts[pts.length - 1];
    svg.append(s("circle", { cx: X(li), cy: Y(lv), r: 3, fill: "var(--accent)" }));
    return svg;
  }

  // ── basket ────────────────────────────────────────────────────────────
  // a store whose last good download is days older than the others is not compared
  const STALE_DAYS = 3;
  function isStale(k) {
    const snaps = state.meta.order.map((x) => state.meta.stores[x].snapshot).filter(Boolean).sort();
    const own = state.meta.stores[k].snapshot;
    if (!own || !snaps.length) return false;
    return (parseISO(snaps[snaps.length - 1]) - parseISO(own)) / DAY > STALE_DAYS;
  }
  function currentCand(item, k) {
    const c = item.now[k] && item.now[k].cands && item.now[k].cands[0];
    return c && !isStale(k) ? c : null;
  }
  function bestNow(item) {
    let best = null;
    for (const k of state.meta.order) {
      const c = currentCand(item, k);
      if (c && (best == null || c.iu < best.c.iu)) best = { k, c };
    }
    if (best) {
      // stores that match the best price as displayed (5.40 = 5.40) share the win
      const shown = money(best.c.iu);
      best.ties = new Set(state.meta.order.filter((k) => {
        const c = currentCand(item, k);
        return c && money(c.iu) === shown;
      }));
    }
    return best;
  }

  function renderKorpa() {
    const { items, dates } = state.basket;
    const order = state.meta.order;
    const wins = Object.fromEntries(order.map((k) => [k, 0]));
    const avail = Object.fromEntries(order.map((k) => [k, 0]));
    for (const it of items) {
      const b = bestNow(it);
      if (b) for (const k of b.ties) wins[k] += 1;
      for (const k of order) if (it.now[k] && it.now[k].cands.length) avail[k] += 1;
    }
    const tiles = h("section", { class: "stores", "aria-label": "Prodavnice" }, order.map((k) => {
      const m = state.meta.stores[k];
      const st = storeStatus(m);
      return h("div", { class: "store-tile" },
        h("div", { class: "name" }, swatch(k), m.name),
        h("div", { class: "meta" }, "najjeftinija za"),
        h("div", { class: "big" }, String(wins[k]), h("small", { text: plural(wins[k], "proizvod", "proizvoda", "proizvoda") })),
        h("div", { class: "meta" }, `ima cenu za ${avail[k]} od ${items.length}`),
        h("div", { class: "meta" }, h("span", { class: `dot ${st.cls}` }), st.text));
    }));

    const groups = [];
    for (const it of items) {
      let g = groups.find((x) => x.name === it.group);
      if (!g) groups.push((g = { name: it.group, items: [] }));
      g.items.push(it);
    }
    const head = () => h("div", { class: "bhead", "aria-hidden": "true" },
      h("span", { text: "Proizvod" }), h("span", { text: "Najjeftinije danas" }),
      order.map((k) => h("span", { class: "sh" }, swatch(k), storeName(k))),
      h("span", { text: "Trend", style: "text-align:right" }));

    const view = h("div", null,
      h("div", { class: "view-head" },
        h("h2", { text: "Korpa: gde je šta najjeftinije" }),
        h("p", { text: "Za svaki proizvod bira se najjeftinija varijanta po kilogramu, litru ili komadu u svakoj prodavnici. Klikni red za istoriju i konkretne proizvode. Kad više prodavnica ima istu najnižu cenu, broji se svakoj." })),
      tiles,
      groups.map((g) => h("section", { class: "group" },
        h("h3", { text: g.name }),
        h("div", { class: "btable", role: "list" }, head(), g.items.map((it) => basketRow(it, dates))))));
    fill($app, view);
  }

  function basketRow(it, dates) {
    const order = state.meta.order;
    const best = bestNow(it);
    const minSeries = dates.map((_, i) => {
      let m = null;
      for (const k of Object.keys(it.hist)) { const v = it.hist[k][i]; if (v != null && (m == null || v < m)) m = v; }
      return m;
    });
    const open = state.open.has(it.id);
    const row = h("div", { class: "brow", role: "listitem", tabindex: 0, "aria-expanded": String(open), "data-id": it.id },
      h("div", { class: "item-name" }, it.name, h("span", { class: "unit", text: `cena po ${unitLabel(it.unit)}` })),
      best
        ? h("div", { class: "best" },
          h("div", { class: "price" }, money(best.c.iu), h("small", { text: `CHF/${unitLabel(it.unit)}` }), " ", sticker(best.c, true)),
          h("div", { class: "where" }, swatch(best.k), h("b", { text: storeName(best.k) }), h("span", { class: "pn", text: best.c.n, title: best.c.n })))
        : h("div", { class: "best" }, h("span", { class: "muted", text: "nema podataka" })),
      h("div", { class: "cells" }, order.map((k) => {
        const c = it.now[k] && it.now[k].cands[0];
        const win = best && best.ties.has(k);
        const old = c && isStale(k);
        const title = !c ? `${storeName(k)}: nema ovog proizvoda`
          : old ? `${storeName(k)}: cena od ${fmtDate(state.meta.stores[k].snapshot)} (prodavnica nije ažurirana, ne ulazi u poređenje)`
          : `${storeName(k)}: ${c.n}`;
        return h("div", { class: `cell${c ? "" : " none"}${win ? " win" : ""}${old ? " stale" : ""}`, title },
          h("span", { class: "store-label", text: storeName(k) }),
          c ? money(c.iu) : "—",
          c && c.pr ? h("span", { class: "pc" }, sticker(c, true)) : null);
      })),
      sparkline(dates, minSeries));
    const toggle = () => {
      if (state.open.has(it.id)) state.open.delete(it.id); else state.open.add(it.id);
      const next = basketRow(it, dates);
      const detailEl = row.nextElementSibling && row.nextElementSibling.classList.contains("detail") ? row.nextElementSibling : null;
      if (detailEl) detailEl.remove();
      row.replaceWith(...next);
      const focusTarget = document.querySelector(`.brow[data-id="${CSS.escape(it.id)}"]`);
      if (focusTarget) focusTarget.focus({ preventScroll: true });
    };
    row.addEventListener("click", (e) => { if (!e.target.closest("a")) toggle(); });
    row.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); toggle(); } });
    return open ? [row, basketDetail(it, dates)] : [row];
  }

  function basketDetail(it, dates) {
    const order = state.meta.order;
    const series = order.filter((k) => it.hist[k]).map((k) => ({
      key: k, label: storeName(k), color: `var(--s-${k})`, values: it.hist[k], promo: it.promo[k],
    }));
    const chart = dates.length && series.length
      ? chartBlock("b:" + it.id, { dates, series, unit: it.unit, label: `Najniža cena po ${unitLabel(it.unit)} za ${it.name}` })
      : h("div", { class: "chart-empty", text: "Još nema istorije cena za ovaj proizvod." });
    const lists = h("div", { class: "cands" }, order.map((k) => {
      const now = it.now[k];
      const m = state.meta.stores[k];
      const cands = (now && now.cands) || [];
      return h("div", null,
        h("h4", null, swatch(k), m.name, h("span", { class: "muted", text: cands.length ? `· ${cands.length} ${plural(cands.length, "proizvod", "proizvoda", "proizvoda")}` : "" })),
        cands.length && isStale(k) ? h("div", { class: "cand" }, h("div", { class: "s", text: `Cene od ${fmtDate(m.snapshot, true)} — prodavnica od tada nije uspešno preuzeta, pa ne ulazi u poređenje.` })) : null,
        cands.length
          ? cands.slice(0, 3).map((c) => candRow(k, c, it.unit))
          : h("div", { class: "cand" }, h("div", { class: "s", text: m.mode === "pool" ? "Trenutno nije na akciji (ova prodavnica objavljuje samo cene akcija)." : m.mode === "catalog" ? "Nije pronađen u asortimanu." : "Nije pronađen u pretrazi." })));
    }));
    return h("div", { class: "detail" },
      it.note ? h("div", { class: "note", text: it.note }) : null,
      h("div", { class: "detail-grid" }, chart, lists));
  }

  const frozenChip = (p) => (p && p.fz ? h("span", { class: "frz", title: "Smrznut proizvod", text: "❄ smrznuto" }) : null);

  function candRow(k, c, unit) {
    const name = c.url ? h("a", { href: c.url, target: "_blank", rel: "noopener", text: c.n }) : h("span", { text: c.n });
    const open = () => openProduct(k, c.id);
    return h("div", { class: "cand" },
      h("div", { class: "n" }, name, h("div", { class: "s" }, [c.b, c.s].filter(Boolean).join(" · "), [c.b, c.s].some(Boolean) ? " · " : "",
        h("button", { type: "button", class: "linkish", onclick: open, style: "border:0;background:none;padding:0;color:var(--accent);cursor:pointer;font-size:12px" }, "istorija"))),
      h("div", { class: "p" },
        c.r && c.pr ? h("span", { class: "old", text: money(c.r) }) : null,
        frozenChip(c), h("b", { text: money(c.p) }), " ", sticker(c),
        h("div", { class: "s", text: `${money(c.iu)} CHF/${unitLabel(unit)}${c.ax ? " (približno)" : ""}` })));
  }

  // ── promotions ────────────────────────────────────────────────────────
  // Meat is split by animal. The product name (French + Serbian) decides; the shop category
  // only helps when it names a single animal ("Viande de porc emballée", not "Viandes & poissons").
  const MEAT = [
    ["riba", "Riba i morski plodovi", /poisson|saumon|\bthon|cabillaud|truite|crevette|fruits de mer|merlu|colin|sardine|maquereau|moule|calamar|seiche|poulpe|pesce|lachs|scampi|limande|carrelet|\bsole\b|dorade|loup de mer|perche|\blieu\b|hareng|anchois|surimi|gambas|langoustine|saint-jacques|pangasius|tilapia|omble|brochet|espadon|\briba\b|\bribe\b|\bribl|\blosos|\btunjevin|\btuna\b|\bbakalar|\bskamp|\boslic|\bpastrmk|\bharing|\bsardin|\blignj|\bdagnj|\bskus|\bpangasij|\bsmudj|\borad|\bbrancin|\bsip[ae]\b|\bhobotnic|\bkozic|\brakov/],
    ["piletina", "Piletina i živina", /poulet|volaille|dinde|poularde|pollo|chicken|canard|caille|pintade|coquelet|\bpoule\b|\bpilec|\bpiletin|\bpilic|\bcuret|\bcurec|\bpacj|\bpatk|\bprepelic/],
    ["junetina", "Junetina i teletina", /boeuf|\bveau\b|entrecote|rumsteak|rumsteck|angus|bresaola|manzo|\brind|vitello|\bbeef|charolais|viande sechee|\bjunet|\bjunec|\bgoved|\btelet|\btelec|\bramstek|\bbiftek/],
    ["svinjetina", "Svinjetina", /\bporc|cochon|jambon|\blard|lardons|salami|salametti|chorizo|coppa|prosciutto|speck|pancetta|maiale|schwein|saucisson|mortadelle|cervelas|\bsvinj|\bslanin|\bsunk|\bpancet|\bprsut|\bkulen/],
    ["meso", "Ostalo meso", /viande|agneau|gibier|cerf|chevreuil|sanglier|lapin|cheval|charcuterie|saucisse|steak|hache|cordon bleu|kebab|burger|\bjagnjet|\bjagnjec|\bdivljac|\bjelen|\bsrnet|\bzec|\bkonjsk|\bmeso|\bmesn|\bkobasic|\bvirsl|\bcevap|\bpljeskavic|\bcufte/],
  ];
  // ready meals, sauces, snacks and vegetarian products that merely mention meat belong elsewhere
  const NOT_MEAT = /pizza|sauce|sugo|mayo|chips|soupe|bouillon|lasagne|raviol|tortell|sandwich|wrap|nourriture|\bpates|nouille|quiche|tarte|\bchat\b|chien|croissant|vegetar|vegan|veggie|vegetal|\bsos\b|\bpica\b|cips|\bsupa\b|corba|lazanj|sendvic|testenin|hrana za|keks|kreker|namaz|kroasan|povrtn|biljn/;
  const OTHER_GROUPS = [
    ["mlecni", "Mlečni i jaja", /lait|fromage|yog|yaourt|beurre|creme|oeuf|sere|quark|skyr|mozzarella|gruyere|emmental|laitier/],
    ["voce", "Voće i povrće", /fruit|legume|pomme|banane|tomate|salade|carotte|oignon|poivron|courgette|raisin|orange|citron|poire|baies|fraise|champignon|avocat|brocoli|chou|melon|kiwi|mangue|ananas|potimarron|courge|epinard/],
    ["pekara", "Hleb i doručak", /pain|boulang|patisser|croissant|toast|cereale|muesli|flocon|confiture|miel|brioche|tresse/],
    ["ostava", "Ostava", /pates|spaghetti|riz|huile|conserve|sauce|farine|sucre|epice|condiment|bouillon|garde-manger|provisions|base/],
    ["pice", "Piće i kafa", /boisson|biere|vin|cafe|the\b|jus|eau|sirop|limonade|soda|energ/],
    ["slatkisi", "Slatkiši i grickalice", /chocolat|biscuit|snack|chips|bonbon|confiserie|glace|dessert|friandise|noix|amande|cacahuete/],
  ];
  const GROUPS = [...MEAT, ...OTHER_GROUPS].map(([key, label]) => [key, label]);
  function promoGroup(p) {
    const name = fold(`${p.n} ${p.sr || ""}`);
    if (!NOT_MEAT.test(name)) {
      for (const [key, , rx] of MEAT) if (rx.test(name)) return key;
      const cat = fold(p.c || "");
      const hits = MEAT.slice(0, 4).filter(([, , rx]) => rx.test(cat));
      if (hits.length === 1) return hits[0][0];
    }
    const t = fold(`${p.c || ""} ${p.n} ${p.sr || ""}`);
    for (const [key, , rx] of OTHER_GROUPS) if (rx.test(t)) return key;
    return "ostalo";
  }

  function filteredPromos() {
    const f = state.promo;
    const q = fold(f.q).trim();
    const words = q ? q.split(/\s+/) : [];
    let list = state.promos.filter((p) => {
      if (!f.soon && p.soon) return false;
      if (f.stores.size && !f.stores.has(p.st)) return false;
      if (f.min && (p.pct || 0) < f.min) return false;
      if (f.group && (p._g || (p._g = promoGroup(p))) !== f.group) return false;
      if (f.frozen === "fresh" && p.fz) return false;
      if (f.frozen === "frozen" && !p.fz) return false;
      if (words.length) {
        const t = p._t || (p._t = fold(`${p.n} ${p.sr || ""} ${p.b || ""} ${p.c || ""}`));
        if (!words.every((w) => t.includes(w))) return false;
      }
      return true;
    });
    const by = {
      pct: (a, b) => (b.pct || 0) - (a.pct || 0) || a.n.localeCompare(b.n, "fr"),
      price: (a, b) => (a.p ?? 1e9) - (b.p ?? 1e9),
      unit: (a, b) => (a.up ?? 1e9) - (b.up ?? 1e9),
      name: (a, b) => (a.sr || a.n).localeCompare(b.sr || b.n, "sr"),
      saving: (a, b) => ((b.r || 0) - (b.p || 0)) - ((a.r || 0) - (a.p || 0)),
    }[f.sort];
    return list.sort(by);
  }

  function renderAkcije() {
    const f = state.promo;
    const order = state.meta.order;
    const counts = Object.fromEntries(order.map((k) => [k, state.promos.filter((p) => p.st === k && (f.soon || !p.soon)).length]));
    const results = h("div");
    const info = h("p", { class: "resinfo", "aria-live": "polite" });
    const redraw = () => {
      const list = filteredPromos();
      info.textContent = list.length ? `${list.length} ${plural(list.length, "artikal", "artikla", "artikala")} na akciji` : "";
      const shown = list.slice(0, f.limit);
      fill(results, 
        list.length ? h("div", { class: "cards" }, shown.map(promoCard)) : h("div", { class: "empty", text: "Nijedna akcija ne odgovara filterima." }),
        list.length > f.limit ? h("div", { class: "more" }, h("button", { class: "btn ghost", type: "button", onclick: () => { f.limit += 60; redraw(); } }, `Prikaži još (${list.length - f.limit})`)) : null);
      store.set("promo", { sort: f.sort, min: f.min, group: f.group, frozen: f.frozen, soon: f.soon });
    };
    const search = h("label", { class: "search" },
      h("span", { class: "sr-only", text: "Pretraga akcija" }),
      s("svg", { viewBox: "0 0 24 24", "aria-hidden": "true" }, s("circle", { cx: 11, cy: 11, r: 7 }), s("path", { d: "m20 20-3.5-3.5" })),
      h("input", { id: "promo-q", type: "search", placeholder: "Traži: poulet, beurre, café…", value: f.q, autocomplete: "off",
        oninput: (e) => { f.q = e.target.value; f.limit = 60; redraw(); } }));
    const chips = h("div", { class: "chips", role: "group", "aria-label": "Prodavnice" }, order.map((k) => h("button", {
      class: "chip", type: "button", "aria-pressed": String(f.stores.has(k)),
      onclick: (e) => { f.stores.has(k) ? f.stores.delete(k) : f.stores.add(k); e.currentTarget.setAttribute("aria-pressed", String(f.stores.has(k))); f.limit = 60; redraw(); },
    }, swatch(k, "dot"), storeName(k), h("span", { class: "n", text: counts[k] }))));
    const select = (id, label, value, options, onchange) => h("label", null,
      h("span", { class: "sr-only", text: label }),
      h("select", { id, onchange: (e) => { onchange(e.target.value); f.limit = 60; redraw(); } },
        options.map(([v, t]) => h("option", { value: v, selected: String(v) === String(value) }, t))));
    const filters = h("div", { class: "filters" },
      search, chips,
      select("promo-group", "Grupa", f.group, [["", "Sve grupe"], ...GROUPS.map(([k, t]) => [k, t]), ["ostalo", "Ostalo"]], (v) => { f.group = v; }),
      select("promo-frozen", "Sveže ili smrznuto", f.frozen, [["", "Sveže i smrznuto"], ["fresh", "Samo sveže"], ["frozen", "Samo smrznuto"]], (v) => { f.frozen = v; }),
      select("promo-min", "Najmanji popust", f.min, [[0, "Svaki popust"], [20, "Bar 20 %"], [30, "Bar 30 %"], [40, "Bar 40 %"], [50, "Bar 50 %"]], (v) => { f.min = +v; }),
      select("promo-sort", "Sortiranje", f.sort, [["pct", "Najveći popust"], ["saving", "Najveća ušteda (CHF)"], ["unit", "Najniža cena po jedinici"], ["price", "Najniža cena"], ["name", "Po nazivu"]], (v) => { f.sort = v; }),
      h("label", { class: "check" }, h("input", { id: "promo-soon", type: "checkbox", checked: f.soon, onchange: (e) => { f.soon = e.target.checked; f.limit = 60; redraw(); } }), "i akcije koje tek počinju"));
    fill($app, h("div", null,
      h("div", { class: "view-head" },
        h("h2", { text: "Akcije ove nedelje" }),
        h("p", { text: "Sve trenutne akcije na hrani iz pet prodavnica. Ime proizvoda otvara istoriju cene." })),
      filters, info, results));
    redraw();
  }

  function promoCard(p) {
    const img = p.img && !window.LP_NO_IMAGES ? h("img", { src: p.img, alt: "", loading: "lazy", referrerpolicy: "no-referrer", onerror: (e) => { e.target.replaceWith(h("span", { class: "ph", text: initials(p.n) })); } }) : h("span", { class: "ph", text: initials(p.n) });
    const until = p.soon ? (p.from ? `od ${fmtDate(p.from)}` : "uskoro") : p.to ? `važi do ${fmtDate(p.to)}` : "";
    const pic = !window.LP_NO_IMAGES;
    return h("article", { class: pic ? "card has-pic" : "card" },
      h("div", { class: "top" }, h("span", { class: "st" }, swatch(p.st, "dot"), storeName(p.st), frozenChip(p)), sticker(p)),
      pic ? h("div", { class: "pic" }, img) : null,
      h("div", null,
        h("h4", null, h("button", {
          type: "button",
          onclick: () => openProduct(p.st, p.id, p.sr),
          title: p.sra ? "Približan prevod naziva" : null,
          text: p.sr ? (p.sra ? "≈ " : "") + p.sr : p.n,
        })),
        p.sr ? h("div", { class: "orig", lang: "fr", text: p.n }) : null,
        h("div", { class: "sub", text: [p.b, p.s].filter(Boolean).join(" · ") })),
      h("div", null,
        h("div", { class: "pr" },
          h("span", { class: "now", text: p.p == null ? "—" : money(p.p) }),
          p.r && p.p != null && p.r > p.p ? h("span", { class: "old", text: money(p.r) }) : null,
          p.up ? h("span", { class: "up", text: `${money(p.up)} CHF/${unitLabel(p.u)}${p.ax ? " (približno)" : ""}` }) : null),
        h("div", { class: "foot" }, h("span", { text: until || (p.c || "") }), p.url ? h("a", { href: p.url, target: "_blank", rel: "noopener", text: "Prodavnica ↗" }) : null)));
  }
  const SMALL_WORDS = new Set(["de", "du", "des", "la", "le", "les", "au", "aux", "et", "en", "a", "à", "d", "l", "di", "con", "avec", "sans", "pour"]);
  const initials = (t) => {
    const words = (t || "").replace(/[^A-Za-zÀ-ÿ ]/g, " ").trim().split(/\s+/).filter(Boolean);
    const main = words.filter((w) => !SMALL_WORDS.has(w.toLowerCase()));
    return ((main.length ? main : words).slice(0, 2).map((w) => w[0]).join("") || "?").toUpperCase();
  };

  // ── product history ───────────────────────────────────────────────────
  async function ensureIndex() {
    if (!state.index) state.index = await load("index.json");
    return state.index;
  }
  async function ensureHistory(k) {
    if (!state.history[k]) state.history[k] = await load(`history/${k}.json`);
    return state.history[k];
  }

  async function renderProizvodi() {
    fill($app, h("p", { class: "muted", text: "Učitavanje spiska proizvoda…" }));
    const index = await ensureIndex();
    const f = state.prod;
    const order = state.meta.order;
    const results = h("div");
    const info = h("p", { class: "resinfo", "aria-live": "polite" });
    const redraw = () => {
      const q = fold(f.q).trim();
      const words = q ? q.split(/\s+/) : [];
      let list = index.filter((r) => (!f.stores.size || f.stores.has(r[0])) && (!f.promoOnly || r[6]));
      if (words.length) {
        list = list.filter((r) => { const t = r._t || (r._t = fold(`${r[2]} ${r[3]}`)); return words.every((w) => t.includes(w)); });
        list.sort((a, b) => (fold(a[2]).startsWith(words[0]) ? 0 : 1) - (fold(b[2]).startsWith(words[0]) ? 0 : 1) || a[2].localeCompare(b[2], "fr"));
        info.textContent = `${list.length} ${plural(list.length, "proizvod", "proizvoda", "proizvoda")}`;
      } else {
        list = list.filter((r) => r[8] > 1).sort((a, b) => b[8] - a[8] || b[9] - a[9]);
        info.textContent = list.length ? "Proizvodi sa najviše promena cene (upiši naziv za pretragu svih praćenih proizvoda)" : `Prati se ${index.length} proizvoda — upiši naziv za pretragu.`;
      }
      fill(results, list.length
        ? h("div", { class: "plist" }, list.slice(0, f.limit).map((r) => h("button", { class: "prow", type: "button", onclick: () => openProduct(r[0], r[1]) },
          h("span", { class: "st" }, swatch(r[0], "dot"), storeName(r[0])),
          h("span", { class: "nm" }, h("b", { text: r[2] }), h("span", { text: [r[3], r[4]].filter(Boolean).join(" · ") })),
          h("span", { class: "pp" }, r[6] ? h("span", { class: "sticker mini", text: "%" }) : null, " ", money(r[5])),
          h("span", { class: "ch", text: r[8] > 1 ? `${r[8] - 1} ${plural(r[8] - 1, "promena", "promene", "promena")}` : "bez promena" }))))
        : (words.length ? h("div", { class: "empty", text: "Nema praćenih proizvoda sa tim nazivom." }) : null));
    };
    const filters = h("div", { class: "filters" },
      h("label", { class: "search" }, h("span", { class: "sr-only", text: "Pretraga proizvoda" }),
        s("svg", { viewBox: "0 0 24 24", "aria-hidden": "true" }, s("circle", { cx: 11, cy: 11, r: 7 }), s("path", { d: "m20 20-3.5-3.5" })),
        h("input", { id: "prod-q", type: "search", placeholder: "Traži proizvod…", value: f.q, autocomplete: "off", oninput: (e) => { f.q = e.target.value; redraw(); } })),
      h("div", { class: "chips", role: "group", "aria-label": "Prodavnice" }, order.map((k) => h("button", {
        class: "chip", type: "button", "aria-pressed": String(f.stores.has(k)),
        onclick: (e) => { f.stores.has(k) ? f.stores.delete(k) : f.stores.add(k); e.currentTarget.setAttribute("aria-pressed", String(f.stores.has(k))); redraw(); },
      }, swatch(k, "dot"), storeName(k)))),
      h("label", { class: "check" }, h("input", { id: "prod-promo", type: "checkbox", checked: f.promoOnly, onchange: (e) => { f.promoOnly = e.target.checked; redraw(); } }), "samo trenutno na akciji"));
    fill($app, h("div", null,
      h("div", { class: "view-head" },
        h("h2", { text: "Istorija cena" }),
        h("p", { text: "Svaka promena cene se beleži. Aldi se prati ceo asortiman; Migros i Coop proizvodi iz korpe i akcija; Lidl i Aligro artikli sa akcija." })),
      filters, info, results));
    redraw();
  }

  function productSeries(p, today) {
    const spans = p.sp && p.sp.length ? p.sp : [[p.f, p.l]];
    const start = spans[0][0];
    const end = today > spans[spans.length - 1][1] ? spans[spans.length - 1][1] : today;
    const dates = [];
    for (let t = parseISO(start); t <= parseISO(end); t += DAY) dates.push(new Date(t).toISOString().slice(0, 10));
    const hist = p.h || [];
    const values = [], promo = [], bands = [];
    let hi = -1, band = null;
    for (const d of dates) {
      while (hi + 1 < hist.length && hist[hi + 1][0] <= d) hi++;
      const inSpan = spans.some(([a, b]) => d >= a && d <= b);
      const row = hi >= 0 ? hist[hi] : null;
      const v = inSpan && row ? row[1] : null;
      values.push(v);
      const pr = inSpan && row ? row[3] : 0;
      promo.push(pr);
      if (pr) { if (band && band[1] === prevDay(d)) band[1] = d; else bands.push((band = [d, d])); }
    }
    return { dates, values, promo, bands };
  }
  const prevDay = (iso) => new Date(parseISO(iso) - DAY).toISOString().slice(0, 10);

  async function openProduct(k, pid, srName) {
    const dlg = document.getElementById("sheet");
    fill(dlg, h("div", { class: "sheet-body" }, h("p", { class: "muted", text: "Učitavanje istorije…" })));
    if (!dlg.open) dlg.showModal();
    let p;
    try { p = (await ensureHistory(k))[pid]; } catch (err) { p = null; }
    if (!p) {
      fill(dlg, sheetHead("Proizvod nije u istoriji", storeName(k), dlg), h("div", { class: "sheet-body" }, h("p", { text: "Za ovaj proizvod još nema sačuvane istorije." })));
      return;
    }
    const today = state.meta.today;
    const ser = productSeries(p, today);
    const prices = (p.h || []).map((r) => r[1]).filter((v) => v != null);
    const last = (p.h || [])[p.h.length - 1];
    // a promotion period starts whenever the price goes from regular to promo
    const promoPeriods = (p.h || []).filter((r, i, a) => r[3] && !(i && a[i - 1][3])).length;
    const promoDays = ser.promo.filter(Boolean).length;
    const stats = h("div", { class: "stats" },
      stat("Poslednja cena", last ? `${money(last[1])} CHF` : "—"),
      stat("Najniža", prices.length ? `${money(Math.min(...prices))} CHF` : "—"),
      stat("Najviša", prices.length ? `${money(Math.max(...prices))} CHF` : "—"),
      stat("Na akciji", promoPeriods ? `${promoPeriods}× (${promoDays} ${plural(promoDays, "dan", "dana", "dana")})` : "nije bilo"),
      stat("Praćen od", fmtDate(p.f, true)));
    const sub = h("div", { class: "sub" },
      srName ? h("span", { lang: "fr", text: p.n }) : null,
      h("span", { style: "display:inline-flex;align-items:center;gap:6px" }, swatch(k), [storeName(k), p.b, p.s, p.c].filter(Boolean).join(" · ")),
      p.url ? h("a", { href: p.url, target: "_blank", rel: "noopener", text: "otvori u prodavnici ↗", style: "color:var(--accent)" }) : null);
    const chart = ser.values.some((v) => v != null)
      ? chartBlock(`p:${k}:${pid}`, {
        dates: ser.dates,
        series: [{ key: k, label: storeName(k), color: `var(--s-${k})`, values: ser.values, promo: ser.promo }],
        unit: null, bands: ser.bands, height: 200, label: `Cena: ${p.n}`,
        caption: h("span", null, h("span", { class: "sticker mini", text: "%", style: "margin-right:6px" }), "žuta traka = na akciji"),
      })
      : h("div", { class: "chart-empty", text: "Nema cena za prikaz." });
    const changes = h("div", { class: "dtable" }, h("table", null,
      h("thead", null, h("tr", null, h("th", { text: "Od datuma" }), h("th", { text: "Cena" }), h("th", { text: "Redovna" }), h("th", { text: "Akcija" }))),
      h("tbody", null, [...(p.h || [])].reverse().map((r) => h("tr", null,
        h("td", { text: fmtDate(r[0], true) }), h("td", { text: money(r[1]) }), h("td", { text: money(r[2]) }), h("td", { text: r[3] ? (r[4] || "da") : "" }))))));
    fill(dlg, sheetHead(srName || p.n, sub, dlg), h("div", { class: "sheet-body" }, stats, chart, h("h4", { text: "Promene cene", style: "margin:4px 0 0" }), changes));
  }
  function stat(k, v) { return h("div", { class: "stat" }, h("div", { class: "k", text: k }), h("div", { class: "v", text: v })); }
  function sheetHead(title, sub, dlg) {
    return h("div", { class: "sheet-head" },
      h("div", null, h("h3", { id: "sheet-title", text: title }), typeof sub === "string" ? h("div", { class: "sub", text: sub }) : sub),
      h("button", { class: "x", type: "button", "aria-label": "Zatvori", onclick: () => dlg.close() }, "×"));
  }

  // ── about ─────────────────────────────────────────────────────────────
  function storeStatus(m) {
    if (!m.last_run) return { cls: "warn", text: "još nije preuzeto" };
    if (m.status === "error") return { cls: "bad", text: `greška · poslednji uspeh ${relDay(m.last_ok)}` };
    const age = m.last_ok ? Math.round((parseISO(realToday()) - parseISO(m.last_ok)) / DAY) : 99;
    if (age > 2) return { cls: "warn", text: `ažurirano ${relDay(m.last_ok)}` };
    if (m.status === "partial") return { cls: "warn", text: `ažurirano ${relDay(m.last_ok)} (delimično)` };
    return { cls: "ok", text: `ažurirano ${relDay(m.last_ok)}` };
  }

  function renderAbout() {
    const order = state.meta.order;
    const rows = order.map((k) => {
      const m = state.meta.stores[k];
      const st = storeStatus(m);
      return h("tr", null,
        h("td", null, h("div", { style: "display:flex;align-items:center;gap:8px;font-weight:600" }, swatch(k), m.name), h("div", { class: "muted", style: "font-size:12px;max-width:34ch", text: m.note })),
        h("td", null, h("div", { style: "display:flex;align-items:center;gap:6px" }, h("span", { class: `dot ${st.cls}` }), st.text),
          m.error ? h("div", { class: "err", text: m.error }) : null,
          (m.warnings || []).slice(0, 2).map((w) => h("div", { class: "wrn", text: w }))),
        h("td", { class: "n", text: m.counts.promos ?? "—" }),
        h("td", { class: "n", text: m.counts.products ?? "—" }));
    });
    fill($app, h("div", null,
      h("div", { class: "view-head" }, h("h2", { text: "O sajtu" })),
      h("div", { class: "about" },
        h("div", { class: "panel" },
          h("h3", { text: "Stanje preuzimanja" }),
          h("div", { class: "tablewrap" }, h("table", { class: "status" },
            h("thead", null, h("tr", null, h("th", { text: "Prodavnica" }), h("th", { text: "Status" }), h("th", { text: "Akcije", style: "text-align:right" }), h("th", { text: "Proizvodi", style: "text-align:right" }))),
            h("tbody", null, rows))),
          h("p", { class: "muted", style: "font-size:12.5px;margin:10px 0 0", text: `Podaci generisani ${fmtDate(state.meta.generated, true)} u ${state.meta.generated.slice(11, 16)} UTC.` })),
        h("div", { class: "panel" },
          h("h3", { text: "Kako radi" }),
          h("ul", null,
            h("li", { text: "Svakog jutra GitHub Action preuzima cene sa javnih sajtova prodavnica i čuva ih u repozitorijumu." }),
            h("li", { text: "Za svaki proizvod iz korpe traži se više varijanti; bira se najjeftinija po kg, litru ili komadu. Pravila su u fajlu basket.toml i mogu se menjati." }),
            h("li", { text: "Akcije „uz uslov“ (npr. „dès 2“, Lidl Plus) se prikazuju, ali se u poređenju računa redovna cena." }),
            h("li", { text: "Migros: cene regiona Vaud. Aldi: filijala Chavannes-près-Renens. Aligro: tržnica Chavannes." }),
            h("li", { text: "Lidl i Aligro javno objavljuju samo cene akcija, pa se kod njih proizvod iz korpe vidi samo dok je na akciji." }),
            h("li", { text: "Cene su informativne; u prodavnici mogu biti drugačije." }))))));
  }

  // ── shell ─────────────────────────────────────────────────────────────
  function updateHeader() {
    const el = document.getElementById("updated");
    const stores = Object.values(state.meta.stores);
    const lastOk = stores.map((m) => m.last_ok).filter(Boolean).sort().pop();
    const bad = state.meta.order.filter((k) => ["error"].includes(state.meta.stores[k].status));
    const stale = !lastOk || Math.round((parseISO(realToday()) - parseISO(lastOk)) / DAY) > 2;
    const cls = bad.length || stale ? "warn" : "ok";
    fill(el, h("span", { class: `dot ${cls}` }),
      h("span", { text: lastOk ? `Ažurirano ${relDay(lastOk)}` : "Još nema podataka" }),
      bad.length ? h("span", { text: `· problem: ${bad.map(storeName).join(", ")}` }) : null);
    const active = state.promos.filter((p) => !p.soon).length;
    document.getElementById("promo-count").textContent = active ? String(active) : "";
  }

  const VIEWS = { korpa: renderKorpa, akcije: renderAkcije, proizvodi: renderProizvodi, "o-sajtu": renderAbout };
  async function route() {
    const hash = (location.hash || "").replace(/^#/, "");
    const [view, ...rest] = hash.split("/");
    if (view === "proizvod" && rest.length === 2) {
      await go("proizvodi");
      openProduct(decodeURIComponent(rest[0]), decodeURIComponent(rest[1]));
      return;
    }
    if (view === "korpa" && rest[0]) state.open.add(decodeURIComponent(rest[0]));
    await go(VIEWS[view] ? view : store.get("view", "korpa"));
    if (view === "korpa" && rest[0]) {
      const row = document.querySelector(`.brow[data-id="${CSS.escape(decodeURIComponent(rest[0]))}"]`);
      if (row) row.scrollIntoView({ block: "center" });
    }
  }
  async function go(view) {
    if (!VIEWS[view]) view = "korpa";
    state.view = view;
    store.set("view", view);
    document.querySelectorAll("#tabs a").forEach((a) => {
      if (a.dataset.view === view) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
    });
    try {
      await VIEWS[view]();
    } catch (err) {
      fill($app, h("div", { class: "empty", text: `Greška pri prikazu: ${err.message}` }));
      console.error(err);
    }
  }

  async function start() {
    try {
      const [meta, basket, promos] = await Promise.all([load("meta.json"), load("basket.json"), load("promos.json")]);
      Object.assign(state, { meta, basket, promos });
    } catch (err) {
      fill($app, h("div", { class: "empty" },
        h("p", { text: "Podaci još nisu dostupni." }),
        h("p", { class: "muted", text: `(${err.message}) Pokreni GitHub Action „Preuzmi cene“ ili lokalno: python -m scraper all` })));
      document.getElementById("updated").replaceChildren(h("span", { class: "dot bad" }), h("span", { text: "Nema podataka" }));
      return;
    }
    const saved = store.get("promo", null);
    if (saved) Object.assign(state.promo, { sort: saved.sort || "pct", min: saved.min || 0, group: saved.group === "meso-smrz" ? "" : saved.group || "", frozen: saved.frozen || "", soon: !!saved.soon });
    updateHeader();
    window.addEventListener("hashchange", route);
    document.getElementById("sheet").addEventListener("click", (e) => { if (e.target.id === "sheet") e.target.close(); });
    route();
  }
  start();
})();
