"use strict";

// ---- bridge ----------------------------------------------------------------
const bridge = () => (window.pywebview && window.pywebview.api) || window.mockApi;
const call = (name, ...args) => bridge()[name](...args);

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
const css = name => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
const C = {};
const reduceMotion = matchMedia("(prefers-reduced-motion: reduce)").matches;

const RECENTRE_KINDS = new Set(["calibrated", "combo", "smart", "putdown", "idle", "manual", "calib"]);
const TOAST_KINDS = new Set(["combo", "smart", "putdown", "idle", "manual", "calib", "connect", "disconnect", "warn", "fix"]);

const LABELS = {
  playstation: { a: "✕", b: "○", x: "□", y: "△", back: "Share", start: "Options", guide: "PS", lb: "L1", rb: "R1", lt: "L2", rt: "R2", misc: "Mute", combo: "Share + Options" },
  xbox: { a: "A", b: "B", x: "X", y: "Y", back: "View", start: "Menu", guide: "Xbox", lb: "LB", rb: "RB", lt: "LT", rt: "RT", misc: "Share", combo: "View + Menu" },
  nintendo: { a: "A", b: "B", x: "X", y: "Y", back: "−", start: "+", guide: "Home", lb: "L", rb: "R", lt: "ZL", rt: "ZR", misc: "Capture", combo: "Minus + Plus" },
  generic: { a: "A", b: "B", x: "X", y: "Y", back: "Back", start: "Start", guide: "Guide", lb: "LB", rb: "RB", lt: "LT", rt: "RT", misc: "Misc", combo: "Back + Start" },
};

let state = null;
let selected = null;
let view = "live";
let lastEventId = null;
let structSig = "";
let settingsSig = "";
let eventsSig = "";
let padFamily = null;
const trails = { left: { raw: [], out: [] }, right: { raw: [], out: [] } };
const gateBorn = new Map();

// ---- helpers -----------------------------------------------------------------
const fmt = v => (v < -0.0049 ? "−" : " ") + Math.abs(v).toFixed(2);
const pct = v => Math.round(v * 100) + "%";
const esc = s => String(s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const device = () => state && state.devices.find(d => d.id === selected);
const labels = d => LABELS[(d && d.family) || "generic"] || LABELS.generic;

function fitCanvas(cv) {
  const dpr = window.devicePixelRatio || 1;
  const w = Math.round(cv.clientWidth * dpr), h = Math.round(cv.clientHeight * dpr);
  if (cv.width !== w || cv.height !== h) { cv.width = w; cv.height = h; }
  const ctx = cv.getContext("2d");
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  ctx.clearRect(0, 0, w, h);
  return { ctx, w, h, dpr };
}

function rgba(hex, a) {
  const n = parseInt(hex.slice(1), 16);
  return `rgba(${n >> 16},${(n >> 8) & 255},${n & 255},${a})`;
}

// ---- gate drawing -------------------------------------------------------------
function drawRing(ctx, cx, cy, R, dpr, progress = 1) {
  ctx.lineWidth = 2 * dpr;
  ctx.strokeStyle = C["rule-strong"];
  ctx.beginPath();
  ctx.arc(cx, cy, R, -Math.PI / 2, -Math.PI / 2 + Math.PI * 2 * progress);
  ctx.stroke();
  if (progress < 1) return;
  ctx.lineWidth = 1 * dpr;
  ctx.strokeStyle = rgba(C.rule, 0.7);
  ctx.beginPath(); ctx.arc(cx, cy, R / 2, 0, Math.PI * 2); ctx.stroke();
  ctx.beginPath();
  ctx.moveTo(cx - R, cy); ctx.lineTo(cx + R, cy);
  ctx.moveTo(cx, cy - R); ctx.lineTo(cx, cy + R);
  ctx.stroke();
}

function drawRange(ctx, cx, cy, R, st, dpr) {
  const ox = cx + st.centre[0] * R, oy = cy + st.centre[1] * R;
  const right = (st.hi[0] - st.centre[0]) * R, left = (st.centre[0] - st.lo[0]) * R;
  const down = (st.hi[1] - st.centre[1]) * R, up = (st.centre[1] - st.lo[1]) * R;
  ctx.save();
  ctx.setLineDash([5 * dpr, 5 * dpr]);
  ctx.lineWidth = 1.5 * dpr;
  ctx.strokeStyle = rgba(C.raw, 0.45);
  const q = [[right, down, 0], [left, down, 0.5], [left, up, 1], [right, up, 1.5]];
  ctx.beginPath();
  for (const [rx, ry, a] of q) ctx.ellipse(ox, oy, Math.max(rx, 1), Math.max(ry, 1), 0, a * Math.PI, (a + 0.5) * Math.PI);
  ctx.stroke();
  ctx.restore();
  // where the stick rests
  ctx.strokeStyle = C.raw;
  ctx.lineWidth = 2 * dpr;
  const s = 6 * dpr;
  ctx.beginPath();
  ctx.moveTo(ox - s, oy); ctx.lineTo(ox + s, oy);
  ctx.moveTo(ox, oy - s); ctx.lineTo(ox, oy + s);
  ctx.stroke();
}

function drawTrail(ctx, pts, cx, cy, R, color, dpr) {
  for (let i = 1; i < pts.length; i++) {
    ctx.strokeStyle = rgba(color, (i / pts.length) * 0.5);
    ctx.lineWidth = 3 * dpr * (i / pts.length);
    ctx.beginPath();
    ctx.moveTo(cx + pts[i - 1][0] * R, cy + pts[i - 1][1] * R);
    ctx.lineTo(cx + pts[i][0] * R, cy + pts[i][1] * R);
    ctx.stroke();
  }
}

function drawDot(ctx, x, y, r, color, dpr, halo) {
  if (halo) {
    ctx.fillStyle = rgba(color, 0.18);
    ctx.beginPath(); ctx.arc(x, y, r * 2.2, 0, Math.PI * 2); ctx.fill();
  }
  ctx.fillStyle = color;
  ctx.beginPath(); ctx.arc(x, y, r, 0, Math.PI * 2); ctx.fill();
}

function pushTrail(arr, x, y, n = 18) {
  const last = arr[arr.length - 1];
  if (!last || Math.hypot(last[0] - x, last[1] - y) > 0.004) arr.push([x, y]);
  while (arr.length > n) arr.shift();
  if (last && Math.hypot(last[0] - x, last[1] - y) <= 0.004 && arr.length > 1) arr.shift();
}

function drawGate(cv, d, which) {
  const { ctx, w, dpr } = fitCanvas(cv);
  const cx = w / 2, cy = w / 2, R = w * 0.42;
  if (!gateBorn.has(cv)) gateBorn.set(cv, performance.now());
  const born = (performance.now() - gateBorn.get(cv)) / 650;
  const progress = reduceMotion ? 1 : Math.min(1, born);
  drawRing(ctx, cx, cy, R, dpr, 1 - Math.pow(1 - progress, 3));
  if (progress < 1 || !d) return;

  const st = d[which];
  const i = which === "left" ? 0 : 2;
  const raw = [d.raw[i], d.raw[i + 1]], out = [d.out[i], d.out[i + 1]];

  // dead zone around the corrected centre
  ctx.save();
  ctx.setLineDash([2 * dpr, 4 * dpr]);
  ctx.strokeStyle = rgba(C.fixed, 0.35);
  ctx.lineWidth = 1 * dpr;
  ctx.beginPath(); ctx.arc(cx, cy, Math.max(state.settings.deadzone * R, 2 * dpr), 0, Math.PI * 2); ctx.stroke();
  ctx.restore();

  drawRange(ctx, cx, cy, R, st, dpr);
  const t = trails[which];
  pushTrail(t.raw, raw[0], raw[1]);
  pushTrail(t.out, out[0], out[1]);
  drawTrail(ctx, t.raw, cx, cy, R, C.raw, dpr);
  drawTrail(ctx, t.out, cx, cy, R, C.fixed, dpr);
  drawDot(ctx, cx + raw[0] * R, cy + raw[1] * R, 6 * dpr, C.raw, dpr, false);
  drawDot(ctx, cx + out[0] * R, cy + out[1] * R, 8 * dpr, C.fixed, dpr, true);
}

function drawSectors(cv, sectors, poly, label) {
  const { ctx, w, dpr } = fitCanvas(cv);
  const cx = w / 2, cy = w / 2, R = w * 0.42;
  drawRing(ctx, cx, cy, R, dpr);
  const n = sectors.length;
  for (let k = 0; k < n; k++) {
    if (!sectors[k]) continue;
    const a0 = (k / n) * Math.PI * 2, a1 = ((k + 1) / n) * Math.PI * 2;
    ctx.fillStyle = rgba(C.fixed, 0.22);
    ctx.beginPath(); ctx.moveTo(cx, cy); ctx.arc(cx, cy, R, a0, a1); ctx.closePath(); ctx.fill();
  }
  if (poly) {
    ctx.strokeStyle = C.fixed; ctx.lineWidth = 2 * dpr;
    ctx.beginPath();
    let started = false;
    poly.forEach((r, k) => {
      if (!r) return;
      const a = ((k + 0.5) / poly.length) * Math.PI * 2;
      const x = cx + Math.cos(a) * r * R, y = cy + Math.sin(a) * r * R;
      started ? ctx.lineTo(x, y) : ctx.moveTo(x, y);
      started = true;
    });
    ctx.stroke();
  }
}

// ---- theme ---------------------------------------------------------------------
const darkQuery = matchMedia("(prefers-color-scheme: dark)");
let themePref = null;

function effectiveTheme(pref) {
  return pref === "system" ? (darkQuery.matches ? "dark" : "light") : pref;
}

function applyTheme(pref, animate) {
  themePref = pref;
  const eff = effectiveTheme(pref);
  const root = document.documentElement;
  if (root.dataset.theme !== eff) {
    if (animate && !reduceMotion) {
      document.body.classList.add("theme-anim");
      setTimeout(() => document.body.classList.remove("theme-anim"), 300);
    }
    root.dataset.theme = eff;
  }
  for (const k of ["--night", "--rule", "--rule-strong", "--raw", "--fixed", "--ink", "--muted", "--deck"]) C[k.slice(2)] = css(k);
  $("#themeLabel").textContent = eff === "dark" ? "Dark" : "Light";
  if (window.pywebview && window.pywebview.api) call("title_theme", eff);
}

darkQuery.addEventListener("change", () => { if (themePref === "system") applyTheme("system", true); });

// ---- live view ---------------------------------------------------------------
function renderLive(d) {
  $("#booting").hidden = true;
  $("#empty").hidden = !!d;
  $("#live").hidden = !d;
  if (!d) return;
  for (const fig of $$(".gate")) {
    const which = fig.dataset.stick;
    const i = which === "left" ? 0 : 2;
    const st = d[which];
    drawGate($(".gate-canvas", fig), d, which);
    $(".v-raw", fig).textContent = `${fmt(d.raw[i])}, ${fmt(d.raw[i + 1])}`;
    $(".v-out", fig).textContent = `${fmt(d.out[i])}, ${fmt(d.out[i + 1])}`;
    const off = Math.hypot(st.centre[0], st.centre[1]);
    $(".v-off", fig).textContent = pct(Math.min(off, 1));
    const badge = $(".gate-badge", fig);
    const show = off > 0.05 && d.calibrated;
    badge.hidden = !show;
    if (show) badge.textContent = "Drift corrected";
    const warn = $(".gate-warn", fig);
    warn.hidden = !st.edge;
    if (st.edge) warn.textContent = "This stick rests at the end of its travel, so one direction can't register. Flick it a few times; if it keeps happening, clean the stick.";
  }
  updateGyroMeter($("#gyroMeter"), d);
  const lbl = $("#gyroLabel");
  lbl.textContent = !d.gyro ? "No gyro on this controller. Idle re-centring is used instead."
    : d.held === null ? "Waiting for gyro"
    : d.held ? "In your hands" : "Lying still";
}

function updateGyroMeter(el, d) {
  if (!el || !d) return;
  const scale = v => Math.sqrt(Math.min(v, 0.2) / 0.2);
  const fill = $(".meter-fill", el), mark = $(".meter-mark", el);
  mark.style.left = `calc(${scale(state.settings.gyro_still) * 100}% - 1px)`;
  fill.style.width = d.gyroNoise == null ? "0%" : scale(d.gyroNoise) * 100 + "%";
  el.classList.toggle("held", d.held === true);
  el.classList.toggle("still", d.held === false);
}

// ---- structure: picker, alerts, status, timeline -------------------------------
function renderStructure() {
  const sig = JSON.stringify([state.devices.map(d => [d.id, d.name, d.type, d.power, d.virtual, d.gyro]), selected,
    state.fix, state.paused, state.vigem, state.hidhide, state.remappers, state.error, state.settings]);
  if (sig === structSig) return;
  structSig = sig;
  const d = device();

  const picker = $("#devicePicker");
  picker.innerHTML = state.devices.length ? state.devices.map(x => `
    <button class="device-chip ${x.id === selected ? "selected" : ""} ${x.virtual.on ? "is-live" : ""}" data-id="${x.id}">
      <span class="dot"></span><b>${esc(x.name)}</b><span>${esc(x.type)}</span>
    </button>`).join("") : `<span class="device-none">No controller</span>`;

  const master = $("#master");
  master.setAttribute("aria-checked", String(!!state.fix));
  $(".master-label", master).textContent = state.fix ? "Fix on" : "Fix off";
  master.disabled = !state.vigem.ok;
  $("#outputNote").textContent = !state.vigem.ok ? "" : !state.fix ? "Games see the original controller"
    : state.paused ? "Paused while a Windows window is in front"
    : `Games see a fixed ${state.settings.output === "ds4" ? "DualShock 4" : "Xbox 360"} controller`;
  $("#outputNote").classList.toggle("paused", !!(state.fix && state.paused));

  const alerts = [];
  if (state.error) alerts.push(["bad", `Controller support failed to start: ${esc(state.error)}`]);
  if (!state.vigem.ok) alerts.push(["bad", "The ViGEmBus driver isn't installed, so the fixed controller can't be created.", "Get ViGEmBus", "vigem"]);
  if (state.remappers.length) {
    const names = esc(state.remappers.join(" and "));
    alerts.push(["bad", `<b>${names}</b> is running and passes the drifting stick straight through, so games, and Windows menus, still see the drift. Close it while using Stick Drift Fix.`, `Close ${names}`, "close-remappers"]);
  }
  for (const x of state.devices) if (x.virtual.error) alerts.push(["bad", `Couldn't create the fixed controller for ${esc(x.name)}: ${esc(x.virtual.error)}`]);
  if (state.hidhide.error && state.settings.hide_original) alerts.push(["bad", `Hiding the original controller failed: ${esc(state.hidhide.error)}`]);
  if (state.fix && state.devices.length && !state.settings.hide_original && state.vigem.ok) {
    alerts.push(state.hidhide.installed
      ? ["", "Games can still see the original controller next to the fixed one.", "Hide it", "hide"]
      : ["", "Games can still see the original controller next to the fixed one. Install HidHide to hide it.", "Get HidHide", "hidhide"]);
  }
  $("#alerts").innerHTML = alerts.map(([cls, msg, btn, act]) =>
    `<div class="alert ${cls}"><p>${msg}</p>${btn ? `<button class="btn" data-act="${act}">${btn}</button>` : ""}</div>`).join("");

  const l = labels(d);
  $("#comboHint").textContent = l.combo;
  $("#comboDesc").textContent = `Hold ${l.combo} for a second to re-centre. Those two buttons aren't sent to the game while held together.`;
  $("#lbl-lt").textContent = l.lt;
  $("#lbl-rt").textContent = l.rt;
  $("#ledTest").hidden = !(d && d.led);

  const s = state.settings;
  $("#guards").innerHTML = [
    [s.smart_recenter, "When you let go of a stick"],
    [d && d.gyro ? s.auto_putdown : s.idle_recenter, d && !d.gyro ? "After a long idle (no gyro)" : "When you put it down"],
    [s.combo, `When you hold ${l.combo}`],
  ].map(([on, text]) => `<li class="${on ? "on" : ""}">${esc(text)}${on ? "" : " (off)"}</li>`).join("");

  if (d) {
    $("#devName").textContent = d.name;
    const power = { wired: "Wired", full: "Battery full", medium: "Battery half", low: "Battery low", empty: "Battery empty" }[d.power];
    const out = d.virtual.on ? `Sending to games as ${d.virtual.kind === "ds4" ? "DualShock 4" : "Xbox 360"}` : "Not sending a fixed controller";
    $("#devMeta").innerHTML = [d.type + (power ? `, ${power.toLowerCase()}` : ""), d.gyro ? "Gyro available" : "No gyro", out].map(esc).join("<br>");
  }

  const hideToggle = $('[data-setting="hide_original"]');
  hideToggle.disabled = !state.hidhide.installed;
  $("#hideDesc").innerHTML = state.hidhide.installed
    ? "Uses HidHide so games only see the fixed controller. It's switched back off when the fix stops. Unplug and replug the controller if a game still shows two."
    : 'Needs HidHide, which isn\'t installed. <a href="#" data-act="hidhide">Get HidHide</a>';
}

function renderEvents() {
  const evs = state.events;
  const sig = evs.length ? evs[evs.length - 1].id : 0;
  if (lastEventId === null) lastEventId = sig;
  for (const ev of evs) {
    if (ev.id <= lastEventId) continue;
    if (TOAST_KINDS.has(ev.kind)) toast(ev.msg, ev.kind === "warn" ? "warn" : "");
    const d = device();
    if (RECENTRE_KINDS.has(ev.kind) && d && ev.device === d.name) {
      const both = ev.kind === "calibrated" || ev.msg.includes("left & right");
      for (const fig of $$(".gate")) {
        if (both || ev.msg.includes(fig.dataset.stick)) {
          fig.classList.remove("pulse"); void fig.offsetWidth; fig.classList.add("pulse");
        }
      }
    }
  }
  lastEventId = Math.max(lastEventId, sig);
  if (sig === eventsSig) return;
  eventsSig = sig;

  const kindClass = k => RECENTRE_KINDS.has(k) || k === "fix" ? "fixkind" : k === "warn" ? "warn" : (k === "connect" || k === "disconnect") ? "dev" : "";
  const rev = [...evs].reverse();
  $("#timeline").innerHTML = rev.length ? rev.map(e =>
    `<li><time>${e.time}</time><i class="k ${kindClass(e.kind)}"></i><span>${esc(e.msg)}${e.device ? `<small>${esc(e.device)}</small>` : ""}</span></li>`).join("")
    : `<li class="none">Nothing yet.</li>`;
  const d = device();
  const mine = rev.filter(e => RECENTRE_KINDS.has(e.kind) && d && e.device === d.name).slice(0, 5);
  $("#miniLog").innerHTML = mine.length ? mine.map(e => `<li><time>${e.time}</time><span>${esc(e.msg)}</span></li>`).join("")
    : `<li class="none">No re-centring needed yet.</li>`;
}

function toast(msg, cls) {
  const el = document.createElement("div");
  el.className = "toast " + cls;
  el.textContent = msg;
  $("#toasts").appendChild(el);
  while ($("#toasts").children.length > 4) $("#toasts").firstChild.remove();
  setTimeout(() => { el.classList.add("out"); setTimeout(() => el.remove(), 220); }, 3200);
}

// ---- settings -----------------------------------------------------------------
function renderSettings() {
  const s = state.settings;
  const sig = JSON.stringify(s);
  if (sig === settingsSig) return;
  settingsSig = sig;
  for (const el of $$("[data-setting]")) {
    const key = el.dataset.setting;
    if (el === document.activeElement && el.type === "range") continue;
    if (el.classList.contains("seg")) {
      for (const b of $$("button", el)) {
        const on = b.dataset.v === s[key];
        b.classList.toggle("on", on);
        b.setAttribute("aria-checked", String(on));
      }
    } else if (el.type === "checkbox") {
      el.checked = !!s[key];
    } else if (el.type === "range") {
      el.value = s[key];
      showRange(el);
    }
  }
  for (const row of $$("[data-needs]")) row.classList.toggle("off", !s[row.dataset.needs]);
}

function showRange(el) {
  const v = parseFloat(el.value);
  const unit = el.dataset.unit || "";
  const out = el.parentElement.querySelector("output");
  out.textContent = unit === "s" ? v.toFixed(v < 10 ? 1 : 0) + " s"
    : el.dataset.setting === "gyro_still" ? v.toFixed(3) : pct(v);
}

// ---- tester -------------------------------------------------------------------
// Controller outline with grips; the face plate sits between y=96 and y=290.
const PAD_BODY = "M196 98 C252 90 388 90 444 98 C524 108 568 132 592 198 C614 258 630 318 620 352 C610 388 570 396 544 374 C520 354 500 322 470 304 C432 284 208 284 170 304 C140 322 120 354 96 374 C70 396 30 388 20 352 C10 318 26 258 48 198 C72 132 116 108 196 98 Z";

// Two layouts: PlayStation puts both sticks low in the middle, everyone
// else offsets the left stick above the D-pad.
const LAYOUTS = {
  symmetric: { dpad: [160, 182], face: [480, 182], ls: [248, 262], rs: [392, 262], back: [214, 122], start: [426, 122], guide: [320, 214], misc: [320, 242] },
  offset: { ls: [168, 178], dpad: [244, 258], face: [474, 178], rs: [398, 258], back: [282, 172], start: [358, 172], guide: [320, 130], misc: [320, 212] },
};
let padLayout = LAYOUTS.offset;

function buildPad(family) {
  const l = LABELS[family] || LABELS.generic;
  const ps = family === "playstation";
  const L = padLayout = ps ? LAYOUTS.symmetric : LAYOUTS.offset;
  const key = (id, shape, label, lx, ly) =>
    `<g>${shape.replace(/^<(\w+)/, `<$1 class="key" data-b="${id}"`)}${label ? `<text class="key-label" x="${lx}" y="${ly}">${esc(label)}</text>` : ""}</g>`;
  const [dx, dy] = L.dpad, [fx, fy] = L.face;
  const arm = 30, w = 26;
  const stick = (side, [x, y], btn) => `
    <circle cx="${x}" cy="${y}" r="40" class="well"/>
    <circle cx="${x}" cy="${y}" r="4" class="nub-raw" id="raw${side}"/>
    <circle cx="${x}" cy="${y}" r="21" class="nub" id="nub${side}" data-b="${btn}"/>`;
  $("#pad").innerHTML = `
    <rect x="110" y="14" width="100" height="28" rx="9" class="key"/>
    <rect x="110" y="14" width="0" height="28" rx="9" class="trigger-fill" id="padLT"/>
    <text class="key-label" x="160" y="28">${esc(l.lt)}</text>
    <rect x="430" y="14" width="100" height="28" rx="9" class="key"/>
    <rect x="430" y="14" width="0" height="28" rx="9" class="trigger-fill" id="padRT"/>
    <text class="key-label" x="480" y="28">${esc(l.rt)}</text>
    ${key(9, '<rect x="104" y="54" width="112" height="26" rx="13"/>', l.lb, 160, 67)}
    ${key(10, '<rect x="424" y="54" width="112" height="26" rx="13"/>', l.rb, 480, 67)}
    <path d="${PAD_BODY}" class="body"/>
    ${ps ? key(20, '<rect x="256" y="104" width="128" height="76" rx="14"/>', "Touchpad", 320, 142) : ""}
    ${key(11, `<rect x="${dx - w / 2}" y="${dy - arm - 12}" width="${w}" height="${arm}" rx="6"/>`, "", 0, 0)}
    ${key(12, `<rect x="${dx - w / 2}" y="${dy + 12}" width="${w}" height="${arm}" rx="6"/>`, "", 0, 0)}
    ${key(13, `<rect x="${dx - arm - 12}" y="${dy - w / 2}" width="${arm}" height="${w}" rx="6"/>`, "", 0, 0)}
    ${key(14, `<rect x="${dx + 12}" y="${dy - w / 2}" width="${arm}" height="${w}" rx="6"/>`, "", 0, 0)}
    ${key(3, `<circle cx="${fx}" cy="${fy - 38}" r="19"/>`, l.y, fx, fy - 38)}
    ${key(0, `<circle cx="${fx}" cy="${fy + 38}" r="19"/>`, l.a, fx, fy + 38)}
    ${key(2, `<circle cx="${fx - 38}" cy="${fy}" r="19"/>`, l.x, fx - 38, fy)}
    ${key(1, `<circle cx="${fx + 38}" cy="${fy}" r="19"/>`, l.b, fx + 38, fy)}
    ${key(4, `<rect x="${L.back[0] - 24}" y="${L.back[1] - 11}" width="48" height="22" rx="11"/>`, l.back, L.back[0], L.back[1])}
    ${key(6, `<rect x="${L.start[0] - 24}" y="${L.start[1] - 11}" width="48" height="22" rx="11"/>`, l.start, L.start[0], L.start[1])}
    ${key(5, `<circle cx="${L.guide[0]}" cy="${L.guide[1]}" r="17"/>`, l.guide, L.guide[0], L.guide[1])}
    ${key(15, `<rect x="${L.misc[0] - 14}" y="${L.misc[1] - 6}" width="28" height="12" rx="6"/>`, "", 0, 0)}
    ${stick("L", L.ls, 7)}
    ${stick("R", L.rs, 8)}`;
  padFamily = family;
}

const circ = { running: false, source: "out", data: { left: new Array(72).fill(0), right: new Array(72).fill(0) } };

function circStep(d) {
  for (const [which, i] of [["left", 0], ["right", 2]]) {
    const src = circ.source === "out" ? d.out : d.raw;
    let x = src[i], y = src[i + 1];
    if (circ.source === "raw") { x -= d[which].centre[0]; y -= d[which].centre[1]; }
    const r = Math.hypot(x, y);
    if (r < 0.3) continue;
    const a = (Math.atan2(y, x) + Math.PI * 2) % (Math.PI * 2);
    const k = Math.floor((a / (Math.PI * 2)) * 72) % 72;
    circ.data[which][k] = Math.max(circ.data[which][k], Math.min(r, 1.5));
  }
}

function circError(arr) {
  const seen = arr.filter(r => r > 0);
  if (seen.length < 18) return null;
  return seen.reduce((s, r) => s + Math.abs(r - 1), 0) / seen.length;
}

function renderTester(d) {
  if (!d) return;
  if (padFamily !== d.family) buildPad(d.family);
  for (const el of $$("#pad [data-b]")) el.classList.toggle("on", !!d.buttons[+el.dataset.b]);
  const tw = v => Math.max(0, Math.min(1, v)) * 100;
  $("#padLT").setAttribute("width", tw(d.triggers[0]));
  $("#padRT").setAttribute("width", tw(d.triggers[1]));
  const move = (id, cx, cy, x, y, r) => { const el = $(id); el.setAttribute("cx", cx + x * r); el.setAttribute("cy", cy + y * r); };
  const [lx, ly] = padLayout.ls, [rx, ry] = padLayout.rs;
  move("#nubL", lx, ly, d.out[0], d.out[1], 18);
  move("#nubR", rx, ry, d.out[2], d.out[3], 18);
  move("#rawL", lx, ly, d.raw[0], d.raw[1], 32);
  move("#rawR", rx, ry, d.raw[2], d.raw[3], 32);

  $("#trigL").style.width = pct(d.triggers[0]);
  $("#trigR").style.width = pct(d.triggers[1]);
  $("#trigLv").textContent = pct(d.triggers[0]);
  $("#trigRv").textContent = pct(d.triggers[1]);

  if (circ.running) circStep(d);
  for (const [which, cv, out] of [["left", "#circL", "#circLv"], ["right", "#circR", "#circRv"]]) {
    const arr = circ.data[which];
    drawSectors($(cv), arr.map(r => r > 0), arr, null);
    const e = circError(arr);
    $(out).textContent = e == null ? (circ.running ? "Keep spinning" : "Not tested") : `Error ${(e * 100).toFixed(1)}%`;
  }
}

// ---- calibration ----------------------------------------------------------------
const calib = { open: false, sectors: { left: new Array(36).fill(false), right: new Array(36).fill(false) } };

function openCalibration() {
  const d = device();
  if (!d) return;
  calib.sectors = { left: new Array(36).fill(false), right: new Array(36).fill(false) };
  calib.open = true;
  $("#calib").hidden = false;
  call("calibration", d.id, "start");
  $("#calibSave").focus();
}

function closeCalibration(save) {
  const d = device();
  calib.open = false;
  $("#calib").hidden = true;
  if (d) call("calibration", d.id, save ? "finish" : "cancel");
}

function renderCalibration(d) {
  if (!calib.open || !d) return;
  for (const [which, i, cv, out] of [["left", 0, "#calibL", "#calibLv"], ["right", 2, "#calibR", "#calibRv"]]) {
    const st = d[which];
    const x = d.raw[i] - st.centre[0], y = d.raw[i + 1] - st.centre[1];
    if (Math.hypot(x, y) > 0.3) {
      const a = (Math.atan2(y, x) + Math.PI * 2) % (Math.PI * 2);
      calib.sectors[which][Math.floor((a / (Math.PI * 2)) * 36) % 36] = true;
    }
    const sec = calib.sectors[which];
    drawSectors($(cv), sec, null);
    const canvas = $(cv);
    const ctx = canvas.getContext("2d");
    const w = canvas.width, R = w * 0.42, dpr = window.devicePixelRatio || 1;
    drawDot(ctx, w / 2 + x * R, w / 2 + y * R, 6 * dpr, C.raw, dpr, true);
    $(out).textContent = `${Math.round((sec.filter(Boolean).length / 36) * 100)}% covered`;
  }
}

// ---- frame loop -------------------------------------------------------------------
function onState(s) {
  state = s;
  if (!s.ready) return;
  if (s.settings.theme !== themePref) applyTheme(s.settings.theme, themePref !== null);
  if (s.version) $("#version").textContent = "v" + s.version;
  if (!s.devices.some(d => d.id === selected)) selected = s.devices.length ? s.devices[0].id : null;
  renderStructure();
  renderSettings();
  renderEvents();
  const d = device();
  if (view === "live") renderLive(d);
  if (view === "tester") renderTester(d);
  if (view === "settings") updateGyroMeter($("#gyroMeter2"), d);
  renderCalibration(d);
}

let inflight = false;
async function frame() {
  if (!inflight && bridge()) {
    inflight = true;
    try { onState(await call("state")); } catch (e) { console.error(e); }
    inflight = false;
  }
  requestAnimationFrame(frame);
}

// ---- events -------------------------------------------------------------------------
function setView(v) {
  view = v;
  for (const b of $$(".nav-item")) {
    const on = b.dataset.view === v;
    b.classList.toggle("active", on);
    on ? b.setAttribute("aria-current", "page") : b.removeAttribute("aria-current");
  }
  for (const s of $$(".view")) s.classList.toggle("active", s.dataset.view === v);
  if (v === "live") for (const cv of $$(".gate-canvas")) gateBorn.delete(cv);
}

function nativeFeel() {
  // Behave like a desktop app, not a web page.
  document.addEventListener("contextmenu", e => { if (!e.target.closest("input, textarea")) e.preventDefault(); });
  document.addEventListener("keydown", e => {
    const k = e.key.toLowerCase();
    if (k === "f5" || k === "f3" || k === "f7" || (e.ctrlKey && ["r", "p", "f", "g", "j", "u", "s", "+", "-", "=", "0"].includes(k))) e.preventDefault();
  });
  document.addEventListener("wheel", e => { if (e.ctrlKey) e.preventDefault(); }, { passive: false });
  document.addEventListener("dragstart", e => e.preventDefault());
}

function wire() {
  for (const k of ["--night", "--rule", "--rule-strong", "--raw", "--fixed", "--ink", "--muted", "--deck"]) C[k.slice(2)] = css(k);
  nativeFeel();
  $("#themeBtn").addEventListener("click", () => {
    const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
    applyTheme(next, true);
    call("update_settings", { theme: next });
  });

  $(".nav").addEventListener("click", e => { const b = e.target.closest(".nav-item"); if (b) setView(b.dataset.view); });
  $("#devicePicker").addEventListener("click", e => {
    const b = e.target.closest(".device-chip");
    if (!b) return;
    selected = +b.dataset.id;
    structSig = ""; eventsSig = "";
    trails.left = { raw: [], out: [] }; trails.right = { raw: [], out: [] };
  });
  $("#master").addEventListener("click", () => call("set_fix", !state.fix));
  $("#btnRecentre").addEventListener("click", () => { const d = device(); if (d) call("recentre", d.id, "both"); });
  $("#btnCalibrate").addEventListener("click", openCalibration);
  $("#calibCancel").addEventListener("click", () => closeCalibration(false));
  $("#calibSave").addEventListener("click", () => closeCalibration(true));
  document.addEventListener("keydown", e => { if (e.key === "Escape" && calib.open) closeCalibration(false); });
  $("#btnReset").addEventListener("click", () => { const d = device(); if (d) call("reset_profile", d.id); });

  document.addEventListener("click", e => {
    const a = e.target.closest("[data-act]");
    if (!a) return;
    e.preventDefault();
    const act = a.dataset.act;
    if (act === "hide") call("set_hiding", true);
    else if (act === "close-remappers") call("close_remappers");
    else call("open_link", act);
  });

  $("#settings").addEventListener("input", e => {
    const el = e.target;
    if (el.type === "range") showRange(el);
  });
  $("#settings").addEventListener("change", e => {
    const el = e.target;
    const key = el.dataset.setting;
    if (!key) return;
    if (key === "hide_original") call("set_hiding", el.checked);
    else call("update_settings", { [key]: el.type === "checkbox" ? el.checked : parseFloat(el.value) });
  });
  $("#settings").addEventListener("click", e => {
    const b = e.target.closest(".seg button");
    if (!b) return;
    if (b.closest('[data-setting="output"]')) call("set_output", b.dataset.v);
    if (b.closest('[data-setting="theme"]')) { applyTheme(b.dataset.v, true); call("update_settings", { theme: b.dataset.v }); }
  });

  $("#btnCirc").addEventListener("click", () => {
    circ.running = !circ.running;
    if (circ.running) circ.data = { left: new Array(72).fill(0), right: new Array(72).fill(0) };
    $("#btnCirc").textContent = circ.running ? "Stop test" : "Start test";
  });
  $("#circSource").addEventListener("click", e => {
    const b = e.target.closest("button");
    if (!b) return;
    circ.source = b.dataset.v;
    circ.data = { left: new Array(72).fill(0), right: new Array(72).fill(0) };
    for (const x of $$("#circSource button")) { x.classList.toggle("on", x === b); x.setAttribute("aria-checked", String(x === b)); }
  });
  for (const b of $$("[data-rumble]")) b.addEventListener("click", () => {
    const d = device(); if (!d) return;
    const m = b.dataset.rumble;
    call("rumble", d.id, m === "high" ? 0 : 50000, m === "low" ? 0 : 50000, 600);
  });
  for (const b of $$("[data-led]")) b.addEventListener("click", () => {
    const d = device(); if (!d) return;
    const [r, g, bl] = b.dataset.led.split(",").map(Number);
    call("led", d.id, r, g, bl);
  });
}

function start() {
  wire();
  requestAnimationFrame(frame);
}

if (window.pywebview && window.pywebview.api) start();
else {
  let started = false;
  window.addEventListener("pywebviewready", () => { if (!started) { started = true; start(); } });
  // Opened in a normal browser: use simulated data so the UI can be previewed.
  setTimeout(() => {
    if (started || (window.pywebview && window.pywebview.api)) return;
    const s = document.createElement("script");
    s.src = "mock.js";
    s.onload = () => { if (!started) { started = true; start(); } };
    document.body.appendChild(s);
  }, 700);
}
