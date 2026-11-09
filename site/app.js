// predmarket-mcp · Cross-venue board. Vanilla JS, hand-rolled SVG, no build step.
// Every label that comes from the venues is inserted with textContent, never innerHTML.
"use strict";

const SVGNS = "http://www.w3.org/2000/svg";
const DAY = 86400;
const VENUES = [
  { key: "kalshi", cls: "k", name: "Kalshi" },
  { key: "polymarket", cls: "p", name: "Polymarket" },
];
const state = { data: null, topic: "", q: "", sort: "gap", open: new Set() };
const $ = (s, el = document) => el.querySelector(s);

// ---------- formatting ----------
function pct(x) {
  if (x == null) return "–";
  return (x * 100).toFixed(1) + "%";
}
function pts(gap) {
  if (gap == null) return "–";
  const v = gap * 100;
  const s = Math.abs(v) < 0.05 ? "±" : v > 0 ? "+" : "−";
  return s + Math.abs(v).toFixed(1);
}
function compact(n) {
  if (n == null) return "–";
  const a = Math.abs(n);
  if (a >= 1e9) return (n / 1e9).toFixed(1).replace(/\.0$/, "") + "B";
  if (a >= 1e6) return (n / 1e6).toFixed(1).replace(/\.0$/, "") + "M";
  if (a >= 1e4) return Math.round(n / 1e3) + "K";
  if (a >= 1e3) return (n / 1e3).toFixed(1).replace(/\.0$/, "") + "K";
  return String(Math.round(n));
}
function vol(m) {
  if (m.volume == null) return "–";
  return m.volume_unit === "usd" ? "$" + compact(m.volume) : compact(m.volume) + " ct";
}
const dateFmt = new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", year: "numeric" });
const dayFmt = new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric" });
const tipFmt = new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
const fmtDate = (iso) => (iso ? dateFmt.format(new Date(iso)) : "–");
function ago(iso) {
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 90) return "just now";
  if (s < 3600) return Math.round(s / 60) + " min ago";
  if (s < 86400 * 2) return Math.round(s / 3600) + " h ago";
  return Math.round(s / 86400) + " days ago";
}
function el(tag, attrs = {}, ...kids) {
  const n = tag.startsWith("svg:") ? document.createElementNS(SVGNS, tag.slice(4)) : document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v == null || v === false) continue;
    if (k === "class") n.setAttribute("class", v);
    else if (k === "text") n.textContent = v;
    else n.setAttribute(k, v);
  }
  for (const kid of kids) if (kid != null) n.append(kid);
  return n;
}

// pairs that settle on different days or barely trade deserve a visible note
function flags(p) {
  const out = [];
  const a = p.kalshi.close_time, b = p.polymarket.close_time;
  if (a && b) {
    // flag a date mismatch when it is large next to the time left: 3 days matters for a
    // weekly price market, not for a year-end one
    const ta = new Date(a), tb = new Date(b);
    const d = Math.abs(ta - tb) / 864e5;
    const left = Math.max(1, (Math.min(ta, tb) - new Date(state.data.generated_at)) / 864e5);
    if (d >= 2 && d / left >= 0.1) out.push(`closes ${Math.round(d)} days apart`);
  }
  for (const v of VENUES) if ((p[v.key].history || []).length < 2) out.push(`no ${v.name} trades in 7 days`);
  return out;
}

// ---------- tooltip ----------
const tip = $("#tip");
function showTip(content, x, y) {
  tip.replaceChildren(...content);
  tip.hidden = false;
  const r = tip.getBoundingClientRect();
  const pad = 12;
  let left = x + 16, top = y + 16;
  if (left + r.width > innerWidth - pad) left = x - r.width - 16;
  if (left < pad) left = pad;
  if (top + r.height > innerHeight - pad) top = y - r.height - 16;
  if (top < pad) top = pad;
  tip.style.left = left + "px";
  tip.style.top = top + "px";
}
const hideTip = () => { tip.hidden = true; };
function tipRow(v, value, sub) {
  return el("div", { class: "r" }, el("i", { class: "key " + v.cls }), el("span", { class: "s", text: v.name }), el("b", { text: value }),
    sub ? el("span", { class: "sub", text: sub }) : null);
}
function quoteTip(p) {
  return [
    el("div", { class: "when", text: "Quote at snapshot" }),
    ...VENUES.map((v) => tipRow(v, pct(p[v.key].mid), `bid ${pct(p[v.key].bid)} · ask ${pct(p[v.key].ask)}`)),
    el("div", { class: "gapline" }, el("span", { text: "Gap (Poly − Kalshi)" }), el("b", { text: pts(p.gap) + " pts" })),
  ];
}

// ---------- header + proof ----------
function renderHead(d) {
  const stamp = $("#stamp");
  const t = el("time", { datetime: d.generated_at, title: d.generated_at.replace("T", " ").replace("Z", " UTC"), text: ago(d.generated_at) });
  const hours = (Date.now() - new Date(d.generated_at)) / 36e5;
  stamp.classList.toggle("ok", hours < 12);
  stamp.classList.toggle("stale", hours >= 12);
  stamp.replaceChildren(el("span", { class: "pulse", "aria-hidden": "true" }), el("span", {}, "Snapshot · ", t, hours >= 12 ? " · refresh overdue" : " · refreshes every 6 h"));

  const s = d.summary, ms = d.markets_scanned;
  const widest = d.pairs.reduce((a, p) => (p.gap != null && (!a || Math.abs(p.gap) > Math.abs(a.gap)) ? p : a), null);
  const cells = [
    [String(s.pairs), null, "Matched pairs", `across ${d.topics.filter((t) => t.pairs).length} of ${d.topics.length} topics`],
    [s.median_abs_gap == null ? "–" : (s.median_abs_gap * 100).toFixed(1), "pts", "Median gap", "absolute, Polymarket − Kalshi"],
    [s.max_abs_gap == null ? "–" : (s.max_abs_gap * 100).toFixed(1), "pts", "Widest gap", widest ? widest.topic : ""],
    [compact(ms.kalshi + ms.polymarket), null, "Markets scanned", `${ms.kalshi} Kalshi · ${ms.polymarket} Polymarket`],
    [String(s.rejected), null, "Look-alikes refused", "similar words, different question"],
  ];
  $("#proof").replaceChildren(...cells.map(([v, unit, label, sub]) =>
    el("div", {}, el("dt", {}, label, el("span", { text: sub })), el("dd", {}, v, unit ? el("small", { text: unit }) : null))));

  const sel = $("#f-topic");
  sel.replaceChildren(el("option", { value: "", text: `All topics (${d.pairs.length})` }),
    ...d.topics.map((t) => el("option", { value: t.topic, disabled: t.pairs ? null : "", text: `${t.topic} (${t.pairs})` })));
  sel.value = state.topic;

  $("#topics").replaceChildren(el("b", { text: "Topics scanned this run · " }),
    ...d.topics.flatMap((t) => [el("span", { text: `${t.topic} ${t.pairs}` }), " "]));
}

// ---------- board ----------
function visiblePairs() {
  const q = state.q.trim().toLowerCase();
  let list = state.data.pairs.filter((p) => (!state.topic || p.topic === state.topic) &&
    (!q || [p.topic, p.kalshi.title, p.polymarket.title, p.kalshi.event || "", p.polymarket.event || ""].join(" ").toLowerCase().includes(q)));
  const closeOf = (p) => Math.min(...VENUES.map((v) => (p[v.key].close_time ? +new Date(p[v.key].close_time) : Infinity)));
  const by = {
    gap: (a, b) => Math.abs(b.gap ?? 0) - Math.abs(a.gap ?? 0),
    sim: (a, b) => b.similarity - a.similarity,
    close: (a, b) => closeOf(a) - closeOf(b),
  }[state.sort];
  return list.slice().sort(by);
}
const pairKey = (p) => p.kalshi.id + "|" + p.polymarket.id;

function renderBoard() {
  const list = visiblePairs();
  const rows = $("#rows");
  if (!list.length) {
    rows.replaceChildren(emptyState("No pairs match these filters.", state.q ? `Nothing in this snapshot mentions “${state.q}”.` : "This topic has no matched pairs in this snapshot.", "Clear filters", () => {
      state.topic = ""; state.q = ""; $("#f-q").value = ""; $("#f-topic").value = ""; renderBoard();
    }));
  } else rows.replaceChildren(...list.map(renderRow));
  $("#board-foot").textContent = `Showing ${list.length} of ${state.data.pairs.length} pairs · gap = Polymarket mid − Kalshi mid, in percentage points · mid = (bid + ask) / 2, or the last trade when a side is empty`;
}

function emptyState(title, body, action, onClick) {
  const li = el("li", { class: "empty" }, el("span", {}), el("div", {}, el("h3", { text: title }), el("p", { text: body }),
    action ? el("button", { class: "btn", type: "button", text: action }) : null));
  if (action) $("button", li).addEventListener("click", onClick);
  return li;
}

function renderRow(p, i) {
  const key = pairKey(p);
  const id = "d" + i + "-" + key.replace(/[^a-z0-9]/gi, "").slice(0, 24);
  const open = state.open.has(key);
  const li = el("li", { class: "row" + (open ? " open" : "") });

  const k = p.kalshi.mid, m = p.polymarket.mid;
  const track = el("div", { class: "track", "aria-hidden": "true" }, el("i", { class: "base" }), el("i", { class: "tk", text: "0" }), el("i", { class: "tk r", text: "100%" }));
  if (k != null && m != null) {
    const lo = Math.min(k, m), hi = Math.max(k, m);
    track.append(el("i", { class: "seg", style: `left:${lo * 100}%;width:${(hi - lo) * 100}%` }));
  }
  for (const v of VENUES) if (p[v.key].mid != null) track.append(el("i", { class: "m " + v.cls, style: `left:${p[v.key].mid * 100}%` }));

  const fl = flags(p);
  const titles = el("div", { class: "titles" },
    ...VENUES.map((v) => el("div", { class: "leg " + v.cls }, el("i", { class: "dot " + v.cls, "aria-hidden": "true" }),
      el("span", { class: "t", title: p[v.key].title }, el("span", { class: "sr-only", text: v.name + ": " }), p[v.key].title),
      el("span", { class: "v", text: pct(p[v.key].mid) }))),
    fl.length ? el("div", { class: "flags" }, ...fl.map((f) => el("span", { class: "flag", text: f }))) : null);

  const flat = p.gap == null || Math.abs(p.gap) < 0.01;
  const main = el("div", {
    class: "row-main", role: "button", tabindex: "0", "aria-expanded": String(open), "aria-controls": id,
    "aria-label": `${p.topic}. Kalshi ${pct(k)}, ${p.kalshi.title}. Polymarket ${pct(m)}, ${p.polymarket.title}. Gap ${pts(p.gap)} points.${fl.length ? " Note: " + fl.join(", ") + "." : ""} ${open ? "Hide" : "Show"} price history.`,
  },
    el("span", { class: "topic" }, el("span", { class: "chev", "aria-hidden": "true", text: "▸" }), p.topic),
    titles,
    track,
    el("span", { class: "gap" + (flat ? " flat" : "") }, pts(p.gap), el("small", { text: "pts" })),
    el("span", { class: "vol" }, ...VENUES.map((v) => el("span", {}, el("i", { class: "dot " + v.cls, "aria-hidden": "true" }), vol(p[v.key])))));

  const detail = el("div", { class: "detail", id, hidden: open ? null : "" });
  li.append(main, detail);
  if (open) requestAnimationFrame(() => renderDetail(detail, p));

  const toggle = () => {
    const now = !li.classList.contains("open");
    li.classList.toggle("open", now);
    main.setAttribute("aria-expanded", String(now));
    main.setAttribute("aria-label", main.getAttribute("aria-label").replace(/(Show|Hide) price history\.$/, (now ? "Hide" : "Show") + " price history."));
    detail.hidden = !now;
    if (now) { state.open.add(key); renderDetail(detail, p); } else state.open.delete(key);
    hideTip();
  };
  main.addEventListener("click", (e) => { if (!e.target.closest("a")) toggle(); });
  main.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") { e.preventDefault(); toggle(); }
    if (e.key === "Escape") hideTip();
  });
  // quote tooltip: hover anywhere on the row, or keyboard focus (anchored to the dumbbell)
  main.addEventListener("pointermove", (e) => { if (e.pointerType !== "touch") showTip(quoteTip(p), e.clientX, e.clientY); });
  main.addEventListener("pointerleave", hideTip);
  main.addEventListener("focus", () => { const r = track.getBoundingClientRect(); showTip(quoteTip(p), r.left + r.width / 2, r.bottom); });
  main.addEventListener("blur", hideTip);
  return li;
}

// ---------- detail: facts + 7-day chart ----------
function renderDetail(detail, p) {
  if (!detail.firstChild) {
    const facts = el("div", { class: "facts" }, ...VENUES.map((v) => {
      const mk = p[v.key];
      return el("section", { class: "venue" },
        el("h3", {}, el("i", { class: "dot " + v.cls, "aria-hidden": "true" }), v.name),
        mk.url ? el("a", { class: "q", href: mk.url, target: "_blank", rel: "noopener", text: mk.title + " ↗" }) : el("span", { class: "q", text: mk.title }),
        el("dl", {},
          el("div", {}, el("dt", { text: "Bid" }), el("dd", { text: pct(mk.bid) })),
          el("div", {}, el("dt", { text: "Ask" }), el("dd", { text: pct(mk.ask) })),
          el("div", {}, el("dt", { text: "Mid" }), el("dd", { text: pct(mk.mid) })),
          el("div", { class: "wide" }, el("dt", { text: "Closes" }), el("dd", { text: fmtDate(mk.close_time) })),
          el("div", { class: "wide" }, el("dt", { text: "Volume" }), el("dd", { text: mk.volume == null ? "–" : mk.volume_unit === "usd" ? "$" + compact(mk.volume) + " traded" : compact(mk.volume) + " contracts" }))));
    }));
    const chartWrap = el("div", { class: "chart-wrap" },
      el("div", { class: "chart-head" }, el("p", { class: "label", text: "Last trade, every 3 h · 7 days" }), el("p", { class: "label", text: "word overlap " + Math.round(p.similarity * 100) + "%" })),
      el("div", { class: "chart-slot" }));
    const caveat = el("p", { class: "callout" }, el("span", { class: "label", text: "Check the rules" }),
      "These two markets were paired on wording. Before reading the gap as mispricing, confirm both resolve on the same event, source and date. Venues often differ on all three.");
    detail.append(chartWrap, facts, caveat);
  }
  drawChart($(".chart-slot", detail), p);
}

function niceStep(span) {
  for (const s of [1, 2, 5, 10, 20, 25]) if (span / s <= 6) return s;
  return 25;
}

function drawChart(slot, p) {
  const series = VENUES.map((v) => ({ ...v, pts: (p[v.key].history || []).map(([t, y]) => [t, y * 100]) }));
  const all = series.flatMap((s) => s.pts);
  const end = Math.floor(new Date(state.data.generated_at).getTime() / 1000);
  const start = end - (state.data.settings?.history_hours || 168) * 3600;
  slot.replaceChildren();
  if (!all.length) {
    slot.append(el("p", { class: "chart-note", text: "Neither venue recorded a trade in the last 7 days, so there is no history to draw." }));
    return;
  }
  const W = Math.max(280, slot.clientWidth || 600), H = 240;
  const narrow = W < 460;
  const M = { l: 34, r: narrow ? 58 : 92, t: 10, b: 24 };
  const iw = W - M.l - M.r, ih = H - M.t - M.b;

  let lo = Math.min(...all.map((d) => d[1])), hi = Math.max(...all.map((d) => d[1]));
  if (hi - lo < 8) { const c = (hi + lo) / 2; lo = c - 4; hi = c + 4; }
  const step = niceStep(hi - lo);
  lo = Math.max(0, Math.floor(lo / step) * step);
  hi = Math.min(100, Math.ceil(hi / step) * step);
  if (hi === lo) hi = Math.min(100, lo + step);
  const x = (t) => M.l + ((t - start) / (end - start)) * iw;
  const y = (v) => M.t + (1 - (v - lo) / (hi - lo)) * ih;

  const svg = el("svg:svg", { class: "chart", viewBox: `0 0 ${W} ${H}`, width: W, height: H, role: "img", tabindex: "0",
    "aria-label": `Seven-day YES price for both venues. ${series.map((s) => s.pts.length ? `${s.name} from ${pct(s.pts[0][1] / 100)} to ${pct(s.pts.at(-1)[1] / 100)}` : `${s.name} has no trades`).join(". ")}. Use the left and right arrow keys to read values.` });
  const grid = el("svg:g", { class: "grid" }), axis = el("svg:g", { class: "axis" });
  for (let v = lo; v <= hi + 1e-9; v += step) {
    grid.append(el("svg:line", { x1: M.l, x2: M.l + iw, y1: y(v), y2: y(v) }));
    axis.append(el("svg:text", { x: M.l - 8, y: y(v) + 3.5, "text-anchor": "end", text: v + (v === hi ? "%" : "") }));
  }
  // day ticks at local midnight
  const d0 = new Date(start * 1000); d0.setHours(24, 0, 0, 0);
  let n = 0;
  for (let t = d0.getTime() / 1000; t < end; t += DAY, n++) {
    if (narrow && n % 2) continue;
    axis.append(el("svg:text", { x: x(t), y: H - 6, "text-anchor": "middle", text: dayFmt.format(new Date(t * 1000)) }));
    grid.append(el("svg:line", { x1: x(t), x2: x(t), y1: M.t + ih, y2: M.t + ih + 4 }));
  }
  svg.append(grid, axis);

  const ends = [];
  for (const s of series) {
    if (!s.pts.length) continue;
    if (s.pts.length > 1) svg.append(el("svg:path", { class: "series " + s.cls, d: s.pts.map((d, i) => (i ? "L" : "M") + x(d[0]).toFixed(1) + "," + y(d[1]).toFixed(1)).join("") }));
    const last = s.pts.at(-1);
    svg.append(el("svg:circle", { class: "end " + s.cls, cx: x(last[0]), cy: y(last[1]), r: 4.5 }));
    ends.push({ s, cx: x(last[0]), cy: y(last[1]), v: last[1] });
  }
  // direct end labels in the right margin; if they'd collide, spread them and draw leaders
  ends.sort((a, b) => a.cy - b.cy);
  const ly = ends.map((e) => e.cy);
  if (ly.length === 2 && ly[1] - ly[0] < 16) { const mid = (ly[0] + ly[1]) / 2; ly[0] = mid - 8; ly[1] = mid + 8; }
  ends.forEach((e, i) => {
    const lx = M.l + iw + 12;
    if (Math.abs(ly[i] - e.cy) > 1 || lx - e.cx > 14) svg.append(el("svg:line", { class: "cross", x1: e.cx + 6, y1: e.cy, x2: lx - 3, y2: ly[i] }));
    const t = el("svg:text", { class: "endlabel", x: lx, y: ly[i] + 4 });
    t.append(el("svg:tspan", { text: e.v.toFixed(1) }));
    if (!narrow) t.append(el("svg:tspan", { class: "n", dx: 5, text: e.s.name === "Polymarket" ? "Poly" : e.s.name }));
    svg.append(t);
  });

  // crosshair: snaps to the nearest recorded time; one tooltip lists both venues
  const times = [...new Set(all.map((d) => d[0]))].sort((a, b) => a - b);
  const cross = el("svg:line", { class: "cross", y1: M.t, y2: M.t + ih, visibility: "hidden" });
  const dots = series.map((s) => el("svg:circle", { class: "hover-dot", r: 4.5, fill: `var(--${s.cls === "k" ? "kalshi" : "poly"})`, visibility: "hidden" }));
  const hit = el("svg:rect", { x: M.l, y: 0, width: iw + 8, height: H, fill: "transparent" });
  svg.append(cross, ...dots, hit);
  const nearest = (arr, t) => {
    let lo2 = 0, hi2 = arr.length - 1;
    while (hi2 - lo2 > 1) { const mid = (lo2 + hi2) >> 1; if (arr[mid][0] < t) lo2 = mid; else hi2 = mid; }
    return Math.abs(arr[lo2][0] - t) <= Math.abs(arr[hi2][0] - t) ? arr[lo2] : arr[hi2];
  };
  let cursor = times.length - 1;
  const show = (t, cx, cy) => {
    const X = x(t);
    cross.setAttribute("x1", X); cross.setAttribute("x2", X); cross.setAttribute("visibility", "visible");
    const vals = series.map((s, i) => {
      if (!s.pts.length) { dots[i].setAttribute("visibility", "hidden"); return null; }
      const d = nearest(s.pts, t);
      const far = Math.abs(d[0] - t) > 6 * 3600;
      dots[i].setAttribute("cx", x(d[0])); dots[i].setAttribute("cy", y(d[1])); dots[i].setAttribute("visibility", far ? "hidden" : "visible");
      return { s, d, far };
    });
    const rows = vals.filter(Boolean).map(({ s, d, far }) => tipRow(s, d[1].toFixed(1) + "%", far ? "last trade " + tipFmt.format(new Date(d[0] * 1000)) : null));
    const both = vals.filter(Boolean);
    const content = [el("div", { class: "when", text: tipFmt.format(new Date(t * 1000)) }), ...rows];
    if (both.length === 2) content.push(el("div", { class: "gapline" }, el("span", { text: "Gap" }), el("b", { text: pts((both[1].d[1] - both[0].d[1]) / 100) + " pts" })));
    showTip(content, cx, cy);
  };
  const off = () => { cross.setAttribute("visibility", "hidden"); dots.forEach((d) => d.setAttribute("visibility", "hidden")); hideTip(); };
  hit.addEventListener("pointermove", (e) => {
    const r = svg.getBoundingClientRect();
    const t = start + ((e.clientX - r.left) * (W / r.width) - M.l) / iw * (end - start);
    const i = times.reduce((b, tt, j) => (Math.abs(tt - t) < Math.abs(times[b] - t) ? j : b), 0);
    cursor = i; show(times[i], e.clientX, e.clientY);
  });
  hit.addEventListener("pointerleave", off);
  svg.addEventListener("keydown", (e) => {
    if (e.key !== "ArrowLeft" && e.key !== "ArrowRight" && e.key !== "Home" && e.key !== "End") { if (e.key === "Escape") off(); return; }
    e.preventDefault();
    cursor = e.key === "Home" ? 0 : e.key === "End" ? times.length - 1 : Math.max(0, Math.min(times.length - 1, cursor + (e.key === "ArrowRight" ? 1 : -1)));
    const r = svg.getBoundingClientRect();
    show(times[cursor], r.left + x(times[cursor]) * (r.width / W), r.top + M.t + 20);
  });
  svg.addEventListener("blur", off);
  slot.append(svg);

  const notes = series.filter((s) => s.pts.length < 2).map((s) => s.pts.length ? `${s.name} traded once in 7 days; its dot is that trade.` : `${s.name} recorded no trades in 7 days.`);
  if (notes.length) slot.append(el("p", { class: "chart-note", text: notes.join(" ") }));

  // the same numbers as a table, for screen readers
  const byT = new Map();
  series.forEach((s, i) => s.pts.forEach(([t, v]) => { const r = byT.get(t) || [null, null]; r[i] = v; byT.set(t, r); }));
  const table = el("table", { class: "sr-only" }, el("caption", { text: "Seven-day YES price, percent" }),
    el("thead", {}, el("tr", {}, el("th", { scope: "col", text: "Time" }), ...series.map((s) => el("th", { scope: "col", text: s.name })))),
    el("tbody", {}, ...[...byT.entries()].sort((a, b) => a[0] - b[0]).map(([t, r]) => el("tr", {}, el("th", { scope: "row", text: tipFmt.format(new Date(t * 1000)) }), ...r.map((v) => el("td", { text: v == null ? "–" : v.toFixed(1) }))))));
  slot.append(table);
}

// ---------- refused look-alikes ----------
const KINDS = [
  [/^different dates: (.*)$/, "Date", (m) => m[1]],
  [/^different timing: (.*)$/, "Timing", (m) => m[1]],
  [/^different period: (.*)$/, "Period", (m) => m[1]],
  [/^different place: (.*)$/, "Place", (m) => m[1]],
  [/^different stage: (.*)$/, "Stage", (m) => m[1]],
  [/^narrower on one side: (.*)$/, "Scope", (m) => m[1] + " on one side only"],
  [/^different threshold: (.*)$/, "Threshold", (m) => m[1]],
  [/^threshold on one side only: (.*)$/, "Threshold", (m) => m[1] + " on one side only"],
  [/^different comparison: (.*)$/, "Comparison", (m) => m[1]],
  [/^different year: (.*)$/, "Year", (m) => m[1]],
  [/^opposite direction: (.*)$/, "Direction", (m) => m[1]],
  [/^negated on one side only/, "Negation", () => "one side says no / not"],
  [/^only names or dates/, "Subject", () => "shares only names and dates"],
  [/^closes (.*)$/, "Close", (m) => "closes " + m[1]],
];
function parseReason(r) {
  for (const [rx, kind, fmt] of KINDS) { const m = r.match(rx); if (m) return { kind, detail: fmt(m) }; }
  return { kind: "Rule", detail: r };
}
const escapeRx = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
const SKIP = /^(on|one|side|only|says|no|not|exactly|shares|names|and|dates|a|date|by|before|quarter|implied|days?|apart|left|limit|closes|with|vs)$/i;
const MONTH3 = /^(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)$/i;
// underline the words the rule tripped on (dates, thresholds, places, stages), so the eye
// finds the difference; terms only match whole words, so "13" never lights up inside "2013"
function marked(title, kind, detail) {
  if (kind === "Timing" || kind === "Close" || kind === "Subject" || kind === "Negation") return [title];
  const terms = [...new Set(detail.replace(/[(),]/g, " ").split(/\s+/).filter((w) => w.length > 1 && !SKIP.test(w)))];
  if (!terms.length) return [title];
  const alts = terms.map((w) => {
    let core = MONTH3.test(w) ? escapeRx(w) + "[a-z]*" : w.endsWith("bps") ? escapeRx(w.slice(0, -3)) + "\\s?bps" : escapeRx(w);
    if (/^\w/.test(w)) core = "(?<![\\w.,])" + core;
    if (/\w$/.test(w)) core += "(?![\\w])";
    return core;
  });
  const rx = new RegExp("(" + alts.join("|") + ")", "gi");
  return title.split(rx).map((s, i) => (i % 2 ? el("mark", { text: s }) : s));
}
function renderRefused(d) {
  const tb = $("#refused-table tbody");
  if (!d.rejected.length) {
    tb.replaceChildren(el("tr", {}, el("td", { colspan: "4", text: "No high-overlap look-alikes in this snapshot: every similar pair either matched or was too different to score." })));
    return;
  }
  tb.replaceChildren(...d.rejected.map((n) => {
    const { kind, detail } = parseReason(n.reason);
    const cell = (v) => el("td", { class: "diff" }, el("i", { class: "dot " + v.cls, "aria-hidden": "true" }), el("span", { class: "sr-only", text: v.name + ": " }),
      n[v.key].url ? el("a", { href: n[v.key].url, target: "_blank", rel: "noopener" }, ...marked(n[v.key].title, kind, detail)) : el("span", {}, ...marked(n[v.key].title, kind, detail)));
    return el("tr", {}, cell(VENUES[0]), cell(VENUES[1]),
      el("td", {}, el("div", { class: "why" }, el("span", { class: "chip", title: n.reason }, el("b", { text: kind }), el("span", { text: detail })))),
      el("td", { class: "num", text: Math.round(n.similarity * 100) + "%" }));
  }));
}

// ---------- boot ----------
function bindFilters() {
  $("#f-topic").addEventListener("change", (e) => { state.topic = e.target.value; renderBoard(); });
  $("#f-sort").addEventListener("change", (e) => { state.sort = e.target.value; renderBoard(); });
  let t;
  $("#f-q").addEventListener("input", (e) => { clearTimeout(t); t = setTimeout(() => { state.q = e.target.value; renderBoard(); }, 120); });
  let w = innerWidth, rt;
  addEventListener("resize", () => {
    if (innerWidth === w) return;
    w = innerWidth; clearTimeout(rt);
    rt = setTimeout(() => document.querySelectorAll(".row.open .chart-slot").forEach((slot) => {
      const li = slot.closest(".row"); const i = [...li.parentNode.children].indexOf(li);
      const p = visiblePairs()[i]; if (p) drawChart(slot, p);
    }), 150);
  });
  addEventListener("scroll", hideTip, { passive: true });
}

async function boot() {
  const board = $("#board");
  board.classList.add("loading");
  $("#rows").replaceChildren(...Array.from({ length: 4 }, () => el("li", { class: "skeleton", "aria-hidden": "true" })));
  try {
    const res = await fetch("data.json", { cache: "no-store" });
    if (!res.ok) throw new Error("HTTP " + res.status);
    const d = await res.json();
    if (!Array.isArray(d.pairs)) throw new Error("unexpected format");
    state.data = d;
    renderHead(d);
    if (!state.open.size && d.pairs.length) state.open.add(pairKey(visiblePairs()[0])); // show one chart up front
    renderBoard();
    renderRefused(d);
  } catch (err) {
    const stamp = $("#stamp");
    stamp.classList.add("stale");
    stamp.replaceChildren(el("span", { class: "pulse", "aria-hidden": "true" }), el("span", { text: "Snapshot unavailable" }));
    $("#rows").replaceChildren(emptyState("The snapshot didn't load.", `The board reads a JSON file built from the live venue APIs every 6 hours, and this time it couldn't be fetched (${err.message}). Nothing is shown rather than stale or made-up numbers.`, "Try again", () => boot()));
    $("#refused-table tbody").replaceChildren(el("tr", {}, el("td", { colspan: "4", text: "Waiting for the snapshot." })));
  } finally {
    board.classList.remove("loading");
  }
}
bindFilters();
boot();
