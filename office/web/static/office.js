/* Clip Desk: the office floor. Everything drawn here comes from /api/state (polled every 2.5 s). No assets:
   rooms, furniture and people are drawn as pixels from the config. */
(() => {
"use strict";
let GW = 40, GH = 24;
const T = 16, WALK_Y = 10;           // tile size, grid, corridor row people walk on
const POLL_MS = 2500, SPEED = 70;                       // px per second
const $ = (s) => document.querySelector(s);
const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;
let S = null, seq = 0, layout = null, selected = null, tab = "staff", online = false, lastDetail = null;
const people = {};                                       // name -> sprite
let token = "";
try { token = localStorage.getItem("office_token") || ""; } catch (e) {}

// ---------- helpers ----------
const hash = (s) => { let h = 2166136261; for (const c of s) { h ^= c.charCodeAt(0); h = Math.imul(h, 16777619) >>> 0; } return h; };
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}[c]));
const ago = (ts) => { if (!ts) return ""; const s = Math.max(0, Date.now() / 1000 - ts); return s < 60 ? `${s | 0}s` : s < 3600 ? `${(s / 60) | 0}m` : s < 86400 ? `${(s / 3600) | 0}h` : `${(s / 86400) | 0}d`; };
const hhmm = (ts) => new Date(ts * 1000).toLocaleTimeString([], {hour: "2-digit", minute: "2-digit"});
const money = (n) => "$" + Number(n || 0).toLocaleString(undefined, {maximumFractionDigits: 0});
function toast(msg, ms = 2600) { const t = $("#toast"); t.textContent = msg; t.classList.add("show"); clearTimeout(t._h); t._h = setTimeout(() => t.classList.remove("show"), ms); }
function deptColor(d) { return (S && S.departments[d] && S.departments[d].color) || "#8b95a5"; }

async function api(path, body) {
  const opts = body === undefined ? {} : {method: "POST", headers: {"Content-Type": "application/json", "X-Office-Token": token}, body: JSON.stringify(body)};
  let r = await fetch(path, opts);
  if (r.status === 401 && body !== undefined) {
    const t = prompt("Office token (data/token on the server, or OFFICE_TOKEN):", "");
    if (t === null) throw new Error("cancelled");
    token = t.trim();
    try { localStorage.setItem("office_token", token); } catch (e) {}
    opts.headers["X-Office-Token"] = token;
    r = await fetch(path, opts);
  }
  const j = await r.json().catch(() => ({error: `HTTP ${r.status}`}));
  if (j.error) throw new Error(j.error);
  return j.data;
}

// ---------- layout ----------
function buildLayout() {
  GW = Math.max(40, ...S.rooms.map((r) => r.x + r.w + 1)); GH = Math.max(24, ...S.rooms.map((r) => r.y + r.h + 2));   // the floor grows with the rooms
  const rooms = S.rooms.map((r) => {
    const above = r.y < WALK_Y;
    const door = {x: r.x + Math.floor(r.w / 2), y: above ? r.y + r.h - 1 : r.y};
    return {...r, above, door, desks: [], spots: [], racks: []};
  });
  const byDept = {};
  const roomed = new Set(rooms.filter((r) => r.department).map((r) => r.department));
  const overflow = rooms.filter((r) => r.department).slice(-1)[0];          // departments without a room of their own share the last department room
  for (const e of S.employees) {
    const d = roomed.has(e.department) || !overflow ? e.department : overflow.department;
    (byDept[d] = byDept[d] || []).push(e);
  }
  for (const r of rooms) {
    if (r.department) {
      const staff = (byDept[r.department] || []).slice().sort((a, b) => a.name.localeCompare(b.name));
      const cols = Math.max(1, Math.floor((r.w - 2) / 3));
      const top = r.above ? r.y + 2 : r.y + 3;
      staff.forEach((e, i) => {
        const col = i % cols, row = Math.floor(i / cols);
        const x = r.x + 2 + col * 3, y = top + row * 3;
        r.desks.push({name: e.name, x, y, seat: {x, y: y + 1}});
      });
    } else if (r.kind === "break") {
      for (let i = 0; i < 6; i++) r.spots.push({x: r.x + 2 + (i % 3) * 2, y: r.y + 4 + Math.floor(i / 3) * 3});
    } else if (r.kind === "ceo") {
      r.deskAt = {x: r.x + Math.floor(r.w / 2), y: r.y + 3};
      for (let i = 0; i < 6; i++) r.spots.push({x: r.x + 1 + (i % 4) * 2, y: r.y + r.h - 3 + Math.floor(i / 4) * 2});
    } else if (r.kind === "server") {
      S.machines.forEach((m, i) => r.racks.push({machine: m, x: r.x + 1 + (i % 3) * 2, y: r.y + 3 + Math.floor(i / 3) * 5}));
    }
  }
  layout = {rooms, deskOf: {}, breakRoom: rooms.find((r) => r.kind === "break"), ceo: rooms.find((r) => r.kind === "ceo")};
  for (const r of rooms) for (const d of r.desks) layout.deskOf[d.name] = {room: r, desk: d};
}
const roomAt = (tx, ty) => layout.rooms.find((r) => tx >= r.x && tx < r.x + r.w && ty >= r.y && ty < r.y + r.h);
const px = (t) => ({x: t.x * T + T / 2, y: t.y * T + T});

function route(sp, target) {                               // tile waypoints from the sprite's tile to the target tile
  const cur = {x: Math.floor(sp.x / T), y: Math.floor((sp.y - 1) / T)};
  const a = roomAt(cur.x, cur.y), b = roomAt(target.x, target.y);
  if (a === b) return [target];
  const out = [];
  if (a) out.push({x: a.door.x, y: a.door.y}, {x: a.door.x, y: WALK_Y});
  else out.push({x: cur.x, y: WALK_Y});
  if (b) out.push({x: b.door.x, y: WALK_Y}, {x: b.door.x, y: b.door.y});
  out.push(target);
  return out;
}

// ---------- sprites ----------
function ensureSprite(e) {
  let sp = people[e.name];
  if (!sp) {
    const h = hash(e.name);
    const d = layout.deskOf[e.name];
    const start = d ? d.desk.seat : {x: 20, y: WALK_Y};
    const p = px(start);
    sp = people[e.name] = {name: e.name, x: p.x, y: p.y, path: [], facing: 1, frame: 0, dist: 0, skin: ["#f1c9a5", "#d9a26e", "#b57b4a", "#8d5a3b", "#f7dcc1"][h % 5],
      hair: ["#2b1d14", "#5a3a22", "#c9a24a", "#9c3b2b", "#1f1f1f", "#d8d2c6"][(h >> 3) % 6], wanderAt: Date.now() + 8000 + (h % 40000), wanderUntil: 0, where: "desk"};
  }
  sp.e = e;
  return sp;
}

function targetFor(sp) {
  const e = sp.e, d = layout.deskOf[e.name];
  const now = Date.now();
  if (e.state === "waiting" && layout.ceo) {
    const waiting = S.employees.filter((x) => x.state === "waiting").map((x) => x.name).sort();
    return layout.ceo.spots[waiting.indexOf(e.name) % layout.ceo.spots.length];
  }
  if (e.state === "idle" && layout.breakRoom) {
    if (sp.where === "desk" && now > sp.wanderAt) { sp.where = "break"; sp.wanderUntil = now + 7000 + Math.random() * 8000; sp.spot = layout.breakRoom.spots[hash(e.name + now) % layout.breakRoom.spots.length]; }
    if (sp.where === "break") {
      if (now > sp.wanderUntil) { sp.where = "desk"; sp.wanderAt = now + 40000 + Math.random() * 80000; }
      else return sp.spot;
    }
  } else sp.where = "desk";
  return d ? d.desk.seat : {x: 20, y: WALK_Y};
}

function stepSprites(dt) {
  for (const e of S.employees) {
    if (e.state === "leave") continue;
    const sp = ensureSprite(e);
    const tgt = px(targetFor(sp));
    const key = `${tgt.x},${tgt.y}`;
    if (sp.goal !== key) { sp.goal = key; sp.path = route(sp, {x: Math.floor(tgt.x / T), y: Math.floor((tgt.y - 1) / T)}).map(px); }
    if (reduced) { sp.x = tgt.x; sp.y = tgt.y; sp.path = []; sp.moving = false; continue; }
    sp.moving = false;
    let budget = SPEED * dt;
    while (sp.path.length && budget > 0) {
      const w = sp.path[0], dx = w.x - sp.x, dy = w.y - sp.y, dist = Math.hypot(dx, dy);
      if (dist <= budget) { sp.x = w.x; sp.y = w.y; sp.path.shift(); budget -= dist; }
      else { sp.x += dx / dist * budget; sp.y += dy / dist * budget; budget = 0; }
      if (Math.abs(dx) > 0.5) sp.facing = dx > 0 ? 1 : -1;
      sp.moving = true;
    }
    if (sp.moving) { sp.dist += SPEED * dt; sp.frame = Math.floor(sp.dist / 8) % 2; } else sp.frame = 0;
  }
  for (const n of Object.keys(people)) if (!S.employees.find((e) => e.name === n)) delete people[n];
}

// ---------- drawing ----------
const cv = $("#floor"), cx = cv.getContext("2d");
let scale = 1;
function resize() {
  const st = $("#stage"), dpr = window.devicePixelRatio || 1;
  scale = Math.min(st.clientWidth / (GW * T), st.clientHeight / (GH * T));
  if (scale >= 2) scale = Math.floor(scale);
  cv.style.width = `${GW * T * scale}px`; cv.style.height = `${GH * T * scale}px`;
  cv.width = Math.round(GW * T * scale * dpr); cv.height = Math.round(GH * T * scale * dpr);
  cx.setTransform(scale * dpr, 0, 0, scale * dpr, 0, 0);
  cx.imageSmoothingEnabled = false;
}
addEventListener("resize", resize);

const R = (x, y, w, h, c) => { cx.fillStyle = c; cx.fillRect(x, y, w, h); };
function skyColor() {
  const h = new Date().getHours() + new Date().getMinutes() / 60;
  if (h < 5.5 || h > 20) return "#0d1530";
  if (h < 7.5) return "#d98a5a";
  if (h < 17.5) return "#7fb8e6";
  if (h < 19.5) return "#e0905a";
  return "#2a3a6a";
}
function text(s, x, y, c = "#e6e9ee", size = 7, align = "left") {
  cx.font = `${size}px ui-monospace, Menlo, Consolas, monospace`; cx.textAlign = align; cx.textBaseline = "alphabetic"; cx.fillStyle = c; cx.fillText(s, x, y);
}
function drawRoom(r) {
  const x = r.x * T, y = r.y * T, w = r.w * T, h = r.h * T;
  const col = r.department ? deptColor(r.department) : r.kind === "ceo" ? "#e8b04b" : r.kind === "server" ? "#5d6b80" : "#8b95a5";
  R(x, y, w, h, "#232a35");                                        // floor
  for (let i = 0; i < r.w; i++) for (let j = 0; j < r.h; j++) if ((i + j) % 2) R(x + i * T, y + j * T, T, T, "#262e3a");
  cx.globalAlpha = 0.08; R(x, y, w, h, col); cx.globalAlpha = 1;
  R(x, y, w, 2, "#3b4656"); R(x, y + h - 2, w, 2, "#3b4656"); R(x, y, 2, h, "#3b4656"); R(x + w - 2, y, 2, h, "#3b4656");   // walls
  const dx = r.door.x * T, dy = r.above ? y + h - 2 : y;
  R(dx, dy, T, 2, "#1a2029"); R(dx + 2, dy - (r.above ? 0 : 0), T - 4, 2, "#6b4a2b");                                      // door gap + sill
  if (r.above) for (let i = 2; i < r.w - 2; i += 4) { R(x + i * T, y + 2, 2 * T, 6, skyColor()); R(x + i * T, y + 2, 2 * T, 1, "#3b4656"); R(x + i * T + T - 1, y + 2, 1, 6, "#3b4656"); }
  text(r.title.toUpperCase(), x + 4, y + (r.above ? 16 : 11), col, 7);
  R(x + w - 8, y + h - 10, 4, 6, "#2f5d2f"); R(x + w - 10, y + h - 14, 8, 5, "#4f9a4f");                                   // plant
  if (r.department) {
    for (const d of r.desks) {
      const e = S.employees.find((q) => q.name === d.name) || {};
      const px0 = d.x * T, py0 = d.y * T;
      R(px0 - 6, py0 + 6, 28, 8, "#5c3f27"); R(px0 - 6, py0 + 6, 28, 1, "#8a6240");                                        // desk
      R(px0 + 2, py0 - 1, 12, 8, "#1a1f27");                                                                               // monitor
      const on = e.state === "working", dim = e.state === "offline";
      R(px0 + 3, py0, 10, 6, on ? "#53d4c0" : dim ? "#222831" : "#2f3b4a");
      if (on && !reduced && Math.floor(Date.now() / 300) % 2) R(px0 + 4 + (Math.floor(Date.now() / 150) % 4) * 2, py0 + 2, 1, 2, "#0f1216");
      R(px0 + 7, py0 + 7, 2, 2, "#2a3340");
      if (r.id === "front_desk") R(px0 - 8, py0 + 14, 32, 3, "#6b4a2b");                                                   // counter
    }
    if (r.id === "research") { R(x + w - 46, y + 4, 36, 14, "#e6e9ee"); R(x + w - 44, y + 7, 20, 1, "#4fb3bf"); R(x + w - 44, y + 10, 26, 1, "#c47ad6"); R(x + w - 44, y + 13, 14, 1, "#e8b04b"); }
  } else if (r.kind === "break") {
    R(x + 8, y + 18, 10, 14, "#b23b3b"); R(x + 10, y + 20, 6, 4, "#1a1f27"); R(x + 11, y + 27, 4, 3, "#e6e9ee");           // coffee machine
    R(x + 30, y + 20, 8, 16, "#4fb3bf"); R(x + 31, y + 22, 6, 6, "#9fdbe3"); text("H2O", x + 30, y + 44, "#8b95a5", 5);     // water cooler
    R(x + 10, y + h - 30, 44, 12, "#5a4a7a"); R(x + 10, y + h - 34, 44, 4, "#6e5a92");                                      // sofa
    R(x + 70, y + 40, 28, 16, "#5c3f27");                                                                                  // table
  } else if (r.kind === "ceo") {
    const d = r.deskAt; R(d.x * T - 20, d.y * T + 4, 40, 12, "#4a2f1c"); R(d.x * T - 20, d.y * T + 4, 40, 1, "#7a5436");
    R(d.x * T - 6, d.y * T - 6, 12, 9, "#1a1f27"); R(d.x * T - 5, d.y * T - 5, 10, 7, "#2f3b4a");
    text((S.company.owner || "YOU").toUpperCase(), d.x * T, d.y * T + 24, "#e8b04b", 6, "center");
    R(d.x * T - 5, d.y * T - 16, 10, 8, "#3b4656");                                                                        // chair
    R(x + 8, y + 20, 14, 10, "#2a3340"); R(x + 10, y + 22, 10, 6, "#4fb3bf");                                              // wall frame
    R(x + 8, y + h - 44, w - 16, 1, "#3b4656"); text("WAITING FOR YOUR OK", x + w / 2, y + h - 46, "#8b95a5", 5, "center");
  } else if (r.kind === "server") {
    for (const k of r.racks) {
      const m = k.machine, rx = k.x * T, ry = k.y * T;
      R(rx - 4, ry - 20, 20, 36, "#1a1f27"); R(rx - 3, ry - 19, 18, 34, "#2a3340");
      for (let i = 0; i < 6; i++) { R(rx - 1, ry - 16 + i * 5, 14, 3, "#161b22"); const blink = m.online && !reduced && ((Date.now() / 400 + i) | 0) % 3 === 0; R(rx + 10, ry - 15 + i * 5, 2, 1, m.online ? (blink ? "#2f5d2f" : "#7bc96f") : "#e06c6c"); }
      text((m.name || "").toUpperCase(), rx + 6, ry + 24, m.online ? "#7bc96f" : "#e06c6c", 6, "center");
    }
  }
}
function drawPerson(sp) {
  const e = sp.e, x = Math.round(sp.x), y = Math.round(sp.y), f = sp.facing, shirt = deptColor(e.department), dim = e.state === "offline";
  cx.globalAlpha = dim ? 0.45 : 1;
  R(x - 4, y - 1, 9, 2, "rgba(0,0,0,.25)");
  const lo = sp.moving ? (sp.frame ? 1 : -1) : 0;
  R(x - 4, y - 3 + (lo > 0 ? 1 : 0), 3, 3 - (lo > 0 ? 1 : 0), "#2a2f3a"); R(x + 1, y - 3 + (lo < 0 ? 1 : 0), 3, 3 - (lo < 0 ? 1 : 0), "#2a2f3a");   // legs
  R(x - 5, y - 8, 10, 5, shirt);                                                                                                       // shirt
  R(x - 6, y - 8, 1, 3, sp.skin); R(x + 5, y - 8, 1, 3, sp.skin);                                                                       // hands
  R(x - 4, y - 12, 8, 4, sp.skin);                                                                                                     // face
  R(x - 4, y - 15, 8, 3, sp.hair); R(x - 4 + (f > 0 ? 0 : 7), y - 12, 1, 2, sp.hair);                                                   // hair
  R(x - 2 + (f > 0 ? 1 : -1), y - 11, 1, 1, "#1a1f27"); R(x + 1 + (f > 0 ? 1 : -1), y - 11, 1, 1, "#1a1f27");                           // eyes
  cx.globalAlpha = 1;
  if (selected === e.name) { cx.strokeStyle = "#e8b04b"; cx.lineWidth = 1; cx.strokeRect(x - 7.5, y - 17.5, 15, 19); }
  text(e.name, x, y + 7, selected === e.name ? "#e8b04b" : "#c7ceda", 6, "center");
  if (e.state === "waiting") { R(x + 5, y - 19, 5, 7, "#e8b04b"); text("!", x + 7.5, y - 13, "#1a1407", 7, "center"); }
  if (e.state === "error") { R(x + 5, y - 19, 5, 7, "#e06c6c"); text("x", x + 7.5, y - 13, "#fff", 7, "center"); }
}
function drawBubble(sp) {
  const e = sp.e;
  let msg = "";
  if (e.state === "working") msg = e.bubble || "working";
  else if (e.state === "waiting") msg = "needs your OK";
  else if (e.state === "error") msg = e.bubble || "something broke";
  else if (e.state === "offline") msg = e.bubble;
  else if (sp.where === "break") msg = "coffee";
  if (!msg) return;
  if (e.state === "working" && !reduced) { const n = Math.floor(Date.now() / 400) % 4; msg = msg.slice(0, 34) + (msg.length > 34 ? "…" : "") + " " + ".".repeat(n); }
  else msg = msg.length > 36 ? msg.slice(0, 35) + "…" : msg;
  cx.font = "6px ui-monospace, Menlo, Consolas, monospace";
  const w = Math.ceil(cx.measureText(msg).width) + 8, h = 11;
  let bx = Math.round(sp.x - w / 2), by = Math.round(sp.y - 30);
  if (e.state === "waiting") { const q = S.employees.filter((x) => x.state === "waiting").map((x) => x.name).sort().indexOf(e.name); by -= (q % 2) * 13; }
  bx = Math.max(2, Math.min(GW * T - w - 2, bx));
  if (by < 2) by = Math.round(sp.y + 10);
  const bg = e.state === "error" ? "#e06c6c" : e.state === "waiting" ? "#e8b04b" : "#f3f5f8";
  R(bx, by, w, h, bg); R(bx + 1, by - 1, w - 2, 1, bg); R(bx + 1, by + h, w - 2, 1, bg);
  R(Math.round(sp.x) - 1, by + h + 1, 3, 1, bg); R(Math.round(sp.x), by + h + 2, 1, 1, bg);
  text(msg, bx + 4, by + 8, "#0f1216", 6);
}
let last = performance.now();
function frame(now) {
  const dt = Math.min(0.1, (now - last) / 1000); last = now;
  if (S && layout) {
    stepSprites(dt);
    R(0, 0, GW * T, GH * T, "#1a2029");
    for (let i = 0; i < GW; i++) for (let j = 9; j < 12; j++) if ((i + j) % 2) R(i * T, j * T, T, T, "#1c232d");
    for (const r of layout.rooms) drawRoom(r);
    const sps = Object.values(people).filter((s) => s.e && s.e.state !== "leave").sort((a, b) => a.y - b.y);
    for (const sp of sps) drawPerson(sp);
    for (const sp of sps) drawBubble(sp);
  }
  requestAnimationFrame(frame);
}

// ---------- state polling ----------
async function poll() {
  const my = ++seq;
  try {
    const r = await fetch("/api/state", {cache: "no-store"});
    const j = await r.json();
    if (my !== seq) return;                 // a newer answer already landed
    S = j.data; online = true;
    buildLayout(); resize(); renderTop(); renderPanel();
  } catch (e) { online = false; renderTop(); }
  setTimeout(poll, POLL_MS);
}
function renderTop() {
  $("#conn").classList.toggle("on", online);
  if (!S) return;
  $("#title").textContent = S.company.short || S.company.name || "HQ";
  $("#chip-machines").innerHTML = S.machines.map((m) => `<span class="m ${m.online ? "on" : ""} ${m.kind === "viewer" ? "view" : ""}"></span>${esc(m.name)}`).join(" ");
  const lic = S.license, lc = $("#chip-license");
  if (!lc) {}
  else if (lic) { lc.textContent = `L&I ${lic.status}${lic.expires ? " · " + lic.expires : ""}`; lc.className = "chip " + (lic.status === "ACTIVE" ? "ok" : "bad"); }
  else { lc.textContent = "L&I: not checked yet"; lc.className = "chip"; }
  $("#inbox-n").textContent = S.approvals.length;
  $("#btn-inbox").classList.toggle("accent", S.approvals.length > 0);
  document.title = (S.approvals.length ? `(${S.approvals.length}) ` : "") + (S.company.short || "HQ");
}

// ---------- panel ----------
function setTab(t) { tab = t; document.querySelectorAll("#tabs button").forEach((b) => b.classList.toggle("on", b.dataset.tab === t)); document.querySelectorAll(".tab").forEach((s) => s.classList.toggle("on", s.id === "tab-" + t)); $("#panel").classList.remove("hidden"); renderPanel(); }
document.querySelectorAll("#tabs button").forEach((b) => b.addEventListener("click", () => setTab(b.dataset.tab)));
$("#btn-inbox").addEventListener("click", () => setTab("inbox"));
$("#btn-task").addEventListener("click", () => setTab("new"));
$("#btn-panel").addEventListener("click", () => { $("#panel").classList.toggle("hidden"); resize(); });

function renderPanel() {
  if (!S) return;
  if (tab === "staff") renderStaff();
  else if (tab === "inbox") renderInbox();
  else if (tab === "tasks") renderTasks();
  else if (tab === "leads") renderLeads();
  else if (tab === "clips") renderClips();
  else if (tab === "log") renderLog();
  else if (tab === "new") renderNew();
}
function avatarDataUrl(name, dept) {
  const c = document.createElement("canvas"); c.width = 12; c.height = 16; const g = c.getContext("2d");
  const h = hash(name), skin = ["#f1c9a5", "#d9a26e", "#b57b4a", "#8d5a3b", "#f7dcc1"][h % 5], hair = ["#2b1d14", "#5a3a22", "#c9a24a", "#9c3b2b", "#1f1f1f", "#d8d2c6"][(h >> 3) % 6];
  const r = (x, y, w, hh, col) => { g.fillStyle = col; g.fillRect(x, y, w, hh); };
  r(2, 13, 3, 3, "#2a2f3a"); r(7, 13, 3, 3, "#2a2f3a"); r(1, 8, 10, 5, deptColor(dept)); r(2, 4, 8, 4, skin); r(2, 1, 8, 3, hair); r(4, 5, 1, 1, "#1a1f27"); r(7, 5, 1, 1, "#1a1f27");
  return c.toDataURL();
}
function staffCard(e) {
  return `<div class="card click" data-emp="${esc(e.name)}"><h4><img class="avatar" style="width:24px;height:32px" src="${avatarDataUrl(e.name, e.department)}" alt="">${esc(e.name)} <span class="role">${esc(e.role.replace("_", " "))}</span><span class="st ${e.state}">${e.state}</span></h4>
    <div class="small muted">${esc(e.bubble || (e.state === "idle" ? "nothing assigned" : ""))}</div></div>`;
}
function renderStaff() {
  const el = $("#tab-staff");
  if (selected) return renderDetail(el);
  const groups = {};
  for (const e of S.employees) (groups[e.department] = groups[e.department] || []).push(e);
  el.innerHTML = Object.entries(groups).map(([d, list]) => `<div class="muted mono" style="margin:6px 0 4px;color:${deptColor(d)}">${esc((S.departments[d] || {}).title || d)}</div>` + list.map(staffCard).join("")).join("") +
    `<p class="muted small">Machines: ${S.machines.map((m) => `${esc(m.label || m.name)} ${m.online ? "online" : "offline" + (m.last_ts ? " (" + ago(m.last_ts) + " ago)" : "")}`).join(" · ")}</p>`;
  el.querySelectorAll("[data-emp]").forEach((c) => c.addEventListener("click", () => select(c.dataset.emp)));
}
async function renderDetail(el) {
  const e = S.employees.find((x) => x.name === selected);
  if (!e) { selected = null; return renderStaff(); }
  if (!lastDetail || lastDetail.name !== e.name || Date.now() - lastDetail.at > 5000) {
    try { lastDetail = {name: e.name, at: Date.now(), d: await api(`/api/employees/${encodeURIComponent(e.name)}`)}; } catch (err) { lastDetail = {name: e.name, at: Date.now(), d: {stats: {}, events: []}}; }
  }
  const d = lastDetail.d, st = d.stats || {};
  const machines = Object.keys(S.departments) && S.machines.map((m) => m.name);
  el.innerHTML = `<button class="back" id="back">&larr; all staff</button>
    <div class="detail-head"><img class="avatar" src="${avatarDataUrl(e.name, e.department)}" alt=""><div><h3 style="margin:0">${esc(e.name)} <span class="st ${e.state}">${e.state}</span></h3>
    <div class="muted small">${esc(e.role.replace("_", " "))} · ${esc((S.departments[e.department] || {}).title || e.department)}</div></div></div>
    <div class="kv"><span>now</span><span>${esc(e.bubble || (e.current ? e.current.title : "nothing assigned"))}</span>
    <span>works on</span><span>${esc(e.home)}${e.backup ? ", backup " + esc(e.backup) : ""} · ${esc(e.tier)} tier${e.model ? " · " + esc(e.model) : ""}</span>
    <span>persona</span><span>${esc(e.persona || "")}</span></div>
    <div class="stats"><div><b>${st.done || 0}</b><span>done</span></div><div><b>${st.approved || 0}</b><span>approved</span></div><div><b>${st.failed || 0}</b><span>failed</span></div><div><b>${st.avg_secs || 0}s</b><span>avg</span></div></div>
    <div class="row"><button class="primary" id="give">Give ${esc(e.name)} a task</button>
      <select id="home" title="home machine for ${esc(e.name)}">${["any", ...machines].map((m) => `<option ${m === e.home ? "selected" : ""}>${esc(m)}</option>`).join("")}</select>
      <select id="backup" title="backup machine (takes tasks after 45 s when the home machine is off)"><option value="">no backup</option>${machines.map((m) => `<option value="${esc(m)}" ${m === e.backup ? "selected" : ""}>backup ${esc(m)}</option>`).join("")}</select>
      <select id="tier"><option ${e.tier === "fast" ? "selected" : ""}>fast</option><option ${e.tier === "smart" ? "selected" : ""}>smart</option></select>
      <select id="status"><option ${e.status === "active" ? "selected" : ""}>active</option><option ${e.status === "on_leave" ? "selected" : ""}>on_leave</option></select></div>
    <div class="muted mono" style="margin-top:10px">recent</div>
    ${(d.events || []).map((ev) => `<div class="ev ${ev.kind}"><time>${hhmm(ev.ts)}</time><div><b>${esc(ev.kind)}</b> ${esc(ev.text)}${ev.secs ? ` <span class="muted">(${ev.secs}s${ev.model ? ", " + esc(ev.model) : ""})</span>` : ""}</div></div>`).join("") || "<p class='muted small'>no activity yet</p>"}`;
  $("#back").addEventListener("click", () => { selected = null; renderStaff(); });
  $("#give").addEventListener("click", () => { setTab("new"); $("#task-who").value = e.name; updateHint(); $("#task-text").focus(); });
  for (const f of ["home", "backup", "tier", "status"]) $("#" + f).addEventListener("change", async (ev) => {
    try { await api(`/api/employees/${encodeURIComponent(e.name)}`, {[f]: ev.target.value}); toast(`${e.name}: ${f} = ${ev.target.value}`); lastDetail = null; } catch (err) { toast(err.message); }
  });
}
function select(name) { selected = name; lastDetail = null; setTab("staff"); }

function renderInbox() {
  const el = $("#tab-inbox");
  if (!S.approvals.length) { el.innerHTML = "<p class='muted'>Nothing waiting. Anything that needs your OK lands here before anything goes out.</p>"; return; }
  el.innerHTML = S.approvals.map((a) => {
    const p = a.payload || {};
    let body = "";
    if (a.kind === "reply") body = `<div class="muted small">from ${esc(p.to)} via ${esc(p.channel)}:</div><pre>${esc(p.incoming)}</pre><div class="muted small">draft reply:</div><pre id="draft-${a.id}">${esc(p.draft)}</pre>`;
    else if (a.kind === "estimate") {
      const b = p.breakdown || {};
      body = `<div class="kv"><span>range</span><span><b>${money(b.low)} to ${money(b.high)}</b> (point ${money(b.total)})</span><span>hours</span><span>${b.hours}</span><span>labour</span><span>${money(b.labor)}</span><span>materials</span><span>${money(b.materials)} (${b.gallons} gal ${esc(b.grade)})</span><span>markup</span><span>${money(b.markup)}</span><span>scope</span><span>${b.walls_sqft} sq ft walls, ${b.ceiling_sqft} ceiling, ${b.trim_lf} ft trim, ${b.exterior_sqft} exterior</span></div>
        ${(b.rooms || []).length ? `<table class="tasks">${b.rooms.map((r) => `<tr><td>${esc(r.room)}</td><td>${r.walls_sqft} w / ${r.ceiling_sqft} c / ${r.trim_lf} t</td><td>${r.hours} h</td></tr>`).join("")}</table>` : ""}
        ${p.note ? `<p class="small" style="color:var(--amber)">${esc(p.note)}</p>` : ""}${p.proposal ? `<pre id="draft-${a.id}">${esc(p.proposal)}</pre>` : ""}
        <details><summary class="muted small">assumptions</summary><ul class="small muted">${(b.assumptions || []).map((s) => `<li>${esc(s)}</li>`).join("")}</ul></details>`;
    } else if (a.kind === "post") body = `<div class="muted small">${esc(p.platform)} · ${esc(p.topic)}</div><pre id="draft-${a.id}">${esc(p.draft)}</pre>`;
    else body = `<pre>${esc(JSON.stringify(p, null, 2))}</pre>`;
    const flags = (p.flags || []).length ? `<p class="small" style="color:var(--red)">flagged: ${esc(p.flags.join(", "))}</p>` : "";
    let actions = "";
    if (a.kind === "reply") {
      const subj = encodeURIComponent("Re: " + (p.incoming || "").slice(0, 60).replace(/\s+/g, " "));
      const bodyEnc = encodeURIComponent(p.draft || "");
      actions = `<a class="ghost lnk" href="mailto:?subject=${subj}&body=${bodyEnc}">Open in Mail</a><a class="ghost lnk" href="sms:?&body=${bodyEnc}">Open in Messages</a>`;
    } else if (a.kind === "estimate") actions = `<a class="ghost lnk" href="/estimate/${a.id}" target="_blank" rel="noopener">Printable estimate</a>`;
    return `<div class="card"><h4>${esc(a.summary)}<span class="st waiting">${esc(a.employee)}</span></h4><div class="muted small">${ago(a.ts)} ago${p.model ? " · " + esc(p.model) : ""}</div>${body}${flags}
      <div class="row"><button class="ok" data-act="approve" data-id="${a.id}">Approve</button><button class="no" data-act="reject" data-id="${a.id}">Reject</button>${(p.draft || p.proposal) ? `<button class="ghost" data-copy="${a.id}">Copy text</button>` : ""}${actions}</div></div>`;
  }).join("");
  el.querySelectorAll("[data-act]").forEach((b) => b.addEventListener("click", async () => {
    const note = b.dataset.act === "reject" ? (prompt("Why? (optional, the employee sees it)") || "") : "";
    try { await api(`/api/approvals/${b.dataset.id}`, {action: b.dataset.act, note}); toast(b.dataset.act === "approve" ? "Approved. Nothing is sent automatically: copy the text where it needs to go." : "Rejected."); seq++; poll(); } catch (e) { toast(e.message); }
  }));
  el.querySelectorAll("[data-copy]").forEach((b) => b.addEventListener("click", async () => {
    const pre = el.querySelector(`#draft-${b.dataset.copy}`);
    try { await navigator.clipboard.writeText(pre ? pre.textContent : ""); toast("Copied"); } catch (e) { toast("Copy failed: select the text by hand"); }
  }));
}
function renderTasks() {
  const el = $("#tab-tasks");
  el.innerHTML = `<table class="tasks">${S.tasks.map((t) => `<tr data-task="${t.id}"><td>#${t.id}</td><td><b>${esc(t.title)}</b><div class="muted small">${esc(t.assigned || t.role)} · <span class="st ${t.status === "running" ? "working" : t.status === "failed" ? "error" : t.status === "needs_approval" ? "waiting" : "idle"}">${esc(t.status)}</span> · ${ago(t.ts)} ago${t.started_ts && t.ended_ts ? " · " + Math.round(t.ended_ts - t.started_ts) + "s" : ""}</div>${t.error ? `<div class="small" style="color:var(--red)">${esc(t.error)}</div>` : ""}<div class="small" id="res-${t.id}"></div></td></tr>`).join("")}</table>` || "<p class='muted'>no tasks yet</p>";
  el.querySelectorAll("[data-task]").forEach((tr) => tr.addEventListener("click", async () => {
    const box = tr.querySelector(`#res-${tr.dataset.task}`);
    if (box.innerHTML) { box.innerHTML = ""; return; }
    try { const t = await api(`/api/tasks/${tr.dataset.task}`); let r = t.result; try { r = JSON.parse(r); } catch (e) {}
      box.innerHTML = `<pre>${esc(typeof r === "object" && r && r.text ? r.text : typeof r === "string" ? r : JSON.stringify(r, null, 2))}</pre>`; } catch (e) { box.innerHTML = `<span class="muted">${esc(e.message)}</span>`; }
  }));
}
let leadsCache = {at: 0, rows: []};
async function renderLeads() {
  const el = $("#tab-leads");
  if (Date.now() - leadsCache.at > 15000) {
    try { leadsCache = {at: Date.now(), rows: await api("/api/leads")}; } catch (e) { el.innerHTML = `<p class="muted">${esc(e.message)}</p>`; return; }
  }
  const rows = leadsCache.rows.slice().sort((a, b) => (b.score || 0) - (a.score || 0) || b.first_seen - a.first_seen);
  el.innerHTML = `<p class="muted small">${rows.length} permit leads from Clark County, newest sweep first within score. Score is Harper's 0-10 guess at painting work; land-use stage, so these are projects to watch, not jobs to bid yet.</p>` +
    (rows.map((l) => `<div class="card"><h4>${l.score != null ? `<span class="st ${l.score >= 7 ? "working" : l.score >= 4 ? "waiting" : "idle"}" style="margin:0">${l.score}</span>` : ""}${esc(l.title)}</h4>
      <div class="small">${esc(l.address || "no address listed")}${l.posted ? ` · received ${esc(l.posted)}` : ""} · seen ${ago(l.first_seen)} ago</div>
      <div class="small muted">${esc((l.description || "").slice(0, 220))}</div>${l.url ? `<div class="small"><a class="lnk" href="${esc(l.url)}" target="_blank" rel="noopener">county record</a></div>` : ""}</div>`).join("") || "<p class='muted'>No leads yet. Harper sweeps every Monday, or give Harper a task now.</p>");
}
let clipsCache = {at: 0, d: {}};
async function renderClips() {
  const el = $("#tab-clips");
  if (Date.now() - clipsCache.at > 10000) {
    try { clipsCache = {at: Date.now(), d: await api("/api/clips")}; } catch (e) { el.innerHTML = `<p class="muted">${esc(e.message)}</p>`; return; }
  }
  const d = clipsCache.d;
  if (!d.ts) { el.innerHTML = "<p class='muted'>No report yet. Dex files one every 15 minutes while the laptop worker is on.</p>"; return; }
  const rel = (d.release || []).map((r) => `<tr><td>${esc(r.name)}</td><td>${r.secs > 0 ? esc(r.in) : "out"}</td></tr>`).join("");
  el.innerHTML = `<div class="card"><h4>Launch clock <span class="st ${d.youtube ? "working" : "waiting"}">${d.youtube ? "youtube connected" : "youtube not connected"}</span></h4>
      <table class="tasks">${rel}</table>
      <div class="small muted">Posting opens ${esc(d.post_after || "?")} UTC · auto-post ${d.enabled ? "on" : "OFF"} · report ${ago(d.ts)} ago</div>
      <div class="small">${d.ready_n} ready · ${d.posted_n} posted · ${d.failed_n} failed · ${d.inbox_n} file(s) in inbox</div></div>` +
    (d.posted || []).map((c) => `<div class="card"><h4>${esc(c.title)}</h4><div class="small">posted ${ago(c.at)} ago${c.views != null ? ` · ${c.views} views` : ""} · <a class="lnk" href="${esc(c.url)}" target="_blank" rel="noopener">watch</a></div></div>`).join("") +
    (d.ready || []).map((c) => `<div class="card"><h4>${esc(c.title)}</h4><div class="small muted">ready · from ${esc(c.source)} · cut by ${esc(c.by)}</div></div>`).join("");
}
function renderLog() {
  $("#tab-log").innerHTML = S.events.map((ev) => `<div class="ev ${ev.kind}"><time>${hhmm(ev.ts)}</time><div><b>${esc(ev.employee || "office")}</b> <span class="muted">${esc(ev.kind)}</span> ${esc(ev.text)}</div></div>`).join("") || "<p class='muted'>quiet so far</p>";
}
const HINTS = {clip_director: "Scans data/clips/inbox for new footage and queues clipper jobs. Text is ignored.",
  clipper: "Cuts clips from a footage file. Normally queued by Dex; to do it by hand give the full path to the file.",
  publisher: "Posts ready clips to YouTube if the launch embargo has passed. Text is ignored."};
function renderNew() {
  const sel = $("#task-who");
  if (sel.dataset.n !== String(S.employees.length)) {
    sel.innerHTML = S.employees.map((e) => `<option value="${esc(e.name)}">${esc(e.name)} - ${esc(e.role.replace("_", " "))} (${esc((S.departments[e.department] || {}).title || e.department)})</option>`).join("");
    sel.dataset.n = S.employees.length;
  }
  updateHint();
}
function updateHint() { const e = S.employees.find((x) => x.name === $("#task-who").value); $("#task-hint").textContent = e ? HINTS[e.role] || "" : ""; }
$("#task-who").addEventListener("change", updateHint);
$("#task-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const e = S.employees.find((x) => x.name === $("#task-who").value);
  if (!e) return;
  let text = $("#task-text").value.trim();
  const input = {text};
  const m = /^(facebook|instagram|gbp)\s*:\s*/i.exec(text);
  if (e.role === "marketing" && m) { input.platform = m[1].toLowerCase(); input.text = text = text.slice(m[0].length); }
  try {
    const r = await api("/api/tasks", {role: e.role, department: e.department, title: (text || e.role).slice(0, 120), input});
    $("#task-msg").textContent = `#${r.id} sent to ${e.name}`; $("#task-text").value = ""; toast(`${e.name} has task #${r.id}`); seq++; poll();
  } catch (err) { $("#task-msg").textContent = err.message; }
});

// ---------- input on the floor ----------
function hit(ev) {
  const rect = cv.getBoundingClientRect();
  const lx = (ev.clientX - rect.left) / scale, ly = (ev.clientY - rect.top) / scale;
  let best = null, bd = 1e9;
  for (const sp of Object.values(people)) { if (!sp.e || sp.e.state === "leave") continue; const d = Math.hypot(sp.x - lx, sp.y - 8 - ly); if (d < 14 && d < bd) { best = sp; bd = d; } }
  if (best) return {person: best};
  if (layout) for (const r of layout.rooms) for (const k of r.racks || []) if (Math.abs(k.x * T + 6 - lx) < 12 && Math.abs(k.y * T - 2 - ly) < 22) return {machine: k.machine};
  return null;
}
cv.addEventListener("click", (ev) => {
  const h = hit(ev);
  if (h && h.person) select(h.person.name);
  else if (h && h.machine) { const m = h.machine, inf = m.info || {}; toast(`${m.label || m.name}: ${m.online ? "online" : "offline"}${m.last_ts ? ", seen " + ago(m.last_ts) + " ago" : ""}${inf.employees ? "; hosting " + inf.employees.join(", ") : ""}${inf.ollama && inf.ollama.length ? "; models: " + inf.ollama.slice(0, 4).join(", ") : ""}`, 5000); }
  else if (selected) { selected = null; renderPanel(); }
});
addEventListener("keydown", (e) => { if (e.key === "Escape" && selected) { selected = null; renderPanel(); } });
setInterval(() => { $("#clock").textContent = new Date().toLocaleTimeString([], {hour: "2-digit", minute: "2-digit"}); }, 1000);

const startTab = new URLSearchParams(location.search).get("tab");           // ?tab=inbox bookmarks the phone straight to the inbox
if (startTab && document.querySelector(`#tabs [data-tab="${startTab}"]`)) setTab(startTab);
resize(); poll(); requestAnimationFrame(frame);
if (location.search.includes("debug")) setInterval(() => { $("#title").textContent = `${innerWidth}x${innerHeight} body=${document.body.clientWidth} main=${$("#main").clientWidth} stage=${$("#stage").clientWidth} panel=${$("#panel").clientWidth} cv=${cv.clientWidth} scale=${scale.toFixed(2)}`; }, 500);
})();
