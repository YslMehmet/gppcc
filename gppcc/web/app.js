"use strict";

// ---------------------------------------------------------------- helpers
const $ = (sel) => document.querySelector(sel);

function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === undefined || v === null || v === false) continue;
    if (k === "class") el.className = v;
    else if (k === "style") el.style.cssText = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "value") el.value = v;
    else if (k === "checked") el.checked = !!v;
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) {
    if (c === null || c === undefined || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}

function select(options, value, onchange, attrs = {}) {
  const el = h("select", { ...attrs, onchange: (e) => onchange(e.target.value) },
    options.map((o) => {
      const [val, label] = Array.isArray(o) ? o : [o, o];
      return h("option", { value: val }, label);
    }));
  el.value = value ?? "";
  return el;
}

async function api(method, path, body) {
  const res = await fetch("/api/" + path, {
    method,
    headers: body !== undefined ? { "Content-Type": "application/json" } : {},
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || res.statusText);
  return data;
}

let toastTimer;
function toast(msg, isError = false) {
  const t = $("#toast");
  t.textContent = msg;
  t.className = "toast" + (isError ? " error" : "");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.add("hidden"), isError ? 5000 : 2500);
}

const clone = (o) => JSON.parse(JSON.stringify(o));
const uid = () => Math.random().toString(16).slice(2, 10);

// ---------------------------------------------------------------- state
const S = {
  meta: null,
  profiles: { active: null, names: [] },
  profile: null,          // profile being edited (always the active one)
  live: null,             // latest /api/state snapshot
  tab: "live",
  selectedMacro: null,
  remaps: {},             // device name -> { raw: target }
  capture: null,          // { since, onCapture, el }
};

const INPUT_LABELS = {
  LB: "LB / L1", RB: "RB / R1", LT: "LT / L2", RT: "RT / R2",
  LS: "L-Stick click", RS: "R-Stick click", Back: "Back / Select / Share", Start: "Start / Options",
  Guide: "Guide / Home / PS", Up: "D-pad Up", Down: "D-pad Down", Left: "D-pad Left", Right: "D-pad Right",
  A: "A / ✕", B: "B / ○", X: "X / □", Y: "Y / △",
};
const inputLabel = (i) => INPUT_LABELS[i] || i.replace("_", " ");

const TRIGGER_HELP = {
  press: "fires once when pressed",
  release: "fires once when released (skipped if the button was used as a combo modifier)",
  hold: "held while the button is held (keys/mouse stay down, macros loop)",
  repeat: "fires on press, then repeats while held",
  toggle: "press once to start/hold, again to stop/release",
};

const ACTION_LABELS = {
  none: "— nothing —",
  key: "Key / shortcut",
  mouse: "Mouse button",
  scroll: "Scroll",
  move: "Move cursor",
  text: "Type text",
  macro: "Run macro",
  precision: "Precision (slow cursor)",
  toggle_enabled: "Pause / resume gppcc",
  profile: "Switch to profile",
  next_profile: "Next profile",
};

const OP_LABELS = {
  tap: "Tap keys", key_down: "Key down", key_up: "Key up", text: "Type text",
  click: "Click", mouse_down: "Mouse down", mouse_up: "Mouse up",
  move: "Move by", move_to: "Move to", scroll: "Scroll", wait: "Wait",
};

// ---------------------------------------------------------------- saving
let saveTimer = null;
let saving = false;
function setSaveStatus(t) { $("#saveStatus").textContent = t; }

function changed() {
  setSaveStatus("Unsaved…");
  clearTimeout(saveTimer);
  saveTimer = setTimeout(saveNow, 350);
}

async function saveNow() {
  clearTimeout(saveTimer);
  saveTimer = null;
  if (!S.profile) return;
  saving = true;
  setSaveStatus("Saving…");
  try {
    const saved = await api("PUT", "profile/" + encodeURIComponent(S.profile.name), S.profile);
    // keep server-assigned binding ids, but don't clobber edits made meanwhile
    if (!saveTimer) {
      S.profile.bindings.forEach((b, i) => { if (saved.bindings[i]) b.id = saved.bindings[i].id; });
    }
    setSaveStatus("Saved ✓");
  } catch (e) {
    setSaveStatus("Save failed");
    toast("Save failed: " + e.message, true);
  } finally {
    saving = false;
  }
}

async function loadProfiles() {
  S.profiles = await api("GET", "profiles");
  S.profile = await api("GET", "profile/" + encodeURIComponent(S.profiles.active));
  renderProfileSelect();
}

function renderProfileSelect() {
  const sel = $("#profileSelect");
  sel.replaceChildren(...S.profiles.names.map((n) => h("option", { value: n }, n)));
  sel.value = S.profiles.active;
}

async function activateProfile(name) {
  if (saveTimer) await saveNow();
  await api("POST", `profile/${encodeURIComponent(name)}/activate`, {});
  await loadProfiles();
  S.selectedMacro = null;
  render();
  toast(`Profile “${name}” active`);
}

// ---------------------------------------------------------------- polling
let pollHandle;
async function poll() {
  try {
    S.live = await api("GET", "state");
    updateHeader();
    // Profile switched from the gamepad? Reload it.
    if (S.profiles.active && S.live.profile !== S.profiles.active && !saveTimer && !saving) {
      await loadProfiles();
      render();
    }
    if (S.capture) {
      const li = S.capture.raw ? S.live.last_raw : S.live.last_input;
      if (li.name && li.t > S.capture.since) {
        const cap = S.capture;
        S.capture = null;
        cap.onCapture(li.name);
      }
    }
    if (S.tab === "live") updateLive();
    if (S.tab === "bindings") highlightBindings();
    if (S.tab === "macros") updateMacroRunning();
    if (S.tab === "controller") {
      if ((currentDevice() || "") !== S.renderedDevice && !S.capture) render();
      else updateControllerLive();
    }
  } catch (e) {
    $("#devicePill").textContent = "gppcc not running";
    $("#devicePill").className = "pill";
  }
  const fast = S.tab === "live" || S.tab === "bindings" || S.tab === "controller" || S.capture;
  pollHandle = setTimeout(poll, fast ? 80 : 600);
}

function updateHeader() {
  const L = S.live;
  const pill = $("#devicePill");
  pill.textContent = L.device.connected ? "🎮 " + L.device.name : "No gamepad";
  pill.title = L.device.connected ? L.device.name + (L.device.standard ? "" : " (raw joystick mode)") : "";
  pill.className = "pill" + (L.device.connected ? " ok" : "");
  $("#enabledToggle").checked = L.enabled;
  $("#enabledLabel").textContent = L.enabled ? "Active" : "Paused";
  $("#permBanner").classList.toggle("hidden", L.permissions !== false);
}

// ---------------------------------------------------------------- capture
function startCapture(btn, onCapture, raw = false) {
  if (S.capture) { S.capture.el.classList.remove("capturing"); S.capture.el.textContent = "🎮"; }
  btn.classList.add("capturing");
  btn.textContent = "press…";
  const since = (S.live?.now) ?? Date.now() / 1000;
  S.capture = {
    since, el: btn, raw,
    onCapture: (name) => { btn.classList.remove("capturing"); btn.textContent = "🎮"; onCapture(name); },
  };
  setTimeout(() => {
    if (S.capture && S.capture.el === btn) {
      S.capture = null; btn.classList.remove("capturing"); btn.textContent = "🎮";
    }
  }, 8000);
}

// ---------------------------------------------------------------- views
const view = () => $("#view");

function render() {
  document.querySelectorAll("#tabs button").forEach((b) => b.classList.toggle("active", b.dataset.tab === S.tab));
  const v = view();
  v.replaceChildren();
  if (!S.profile || !S.meta) return;
  ({ live: renderLive, controller: renderController, cursor: renderCursor, bindings: renderBindings,
    macros: renderMacros, profiles: renderProfiles })[S.tab]();
}

// ---- Live ---------------------------------------------------------------
function renderLive() {
  const chips = S.meta.inputs.filter((i) => !i.includes("_") && !/^(Misc|Paddle|Touchpad)/.test(i));
  const stick = (id, label) => h("div", {},
    h("div", { class: "stick", id },
      h("div", { class: "dz" }),
      h("div", { class: "dot" })),
    h("div", { class: "stick-label" }, label));
  const bar = (id, label) => h("div", {},
    h("div", { class: "bar" }, h("div", { class: "fill", id })),
    h("div", { class: "stick-label" }, label));

  view().append(
    h("div", { class: "grid two" },
      h("div", { class: "card" },
        h("h3", {}, "Controller"),
        h("div", { class: "pad-grid", id: "chips" }, chips.map((i) => h("div", { class: "chip", "data-input": i }, i))),
        h("div", { style: "height:16px" }),
        h("div", { class: "sticks" },
          bar("ltFill", "LT"), stick("stickL", "Left stick"), stick("stickR", "Right stick"), bar("rtFill", "RT"))),
      h("div", { class: "card" },
        h("h3", {}, "Status"),
        h("div", { id: "statusList" }),
        h("div", { style: "height:12px" }),
        h("h3", {}, "Activity"),
        h("div", { class: "log", id: "log" }))),
    h("div", { class: "card" },
      h("h3", {}, "Quick start"),
      h("p", { class: "hint" },
        `${S.profile.cursor.stick === "none" ? "Cursor stick is off" : `The ${S.profile.cursor.stick} stick moves the cursor`}, `,
        `${S.profile.scroll.stick === "none" ? "scrolling is off" : `the ${S.profile.scroll.stick} stick scrolls`}. `,
        "Configure buttons under Bindings, build key/mouse sequences under Macros. ",
        "All changes save and apply instantly. Use the switch at the top right to pause control while you're gaming.")));
  updateLive();
}

function updateLive() {
  const L = S.live;
  if (!L || !$("#chips")) return;
  const pressed = new Set(L.pressed);
  document.querySelectorAll("#chips .chip").forEach((c) => c.classList.toggle("on", pressed.has(c.dataset.input)));
  const place = (id, x, y, dz) => {
    const el = $("#" + id);
    if (!el) return;
    el.querySelector(".dot").style.left = 50 + x * 50 + "%";
    el.querySelector(".dot").style.top = 50 + y * 50 + "%";
    const z = el.querySelector(".dz");
    z.style.cssText = `left:${50 - dz * 50}%;top:${50 - dz * 50}%;width:${dz * 100}%;height:${dz * 100}%`;
  };
  const dzFor = (side) => S.profile.cursor.stick === side ? S.profile.cursor.deadzone
    : S.profile.scroll.stick === side ? S.profile.scroll.deadzone : 0;
  place("stickL", L.pad.lx, L.pad.ly, dzFor("left"));
  place("stickR", L.pad.rx, L.pad.ry, dzFor("right"));
  $("#ltFill").style.height = L.pad.lt * 100 + "%";
  $("#rtFill").style.height = L.pad.rt * 100 + "%";

  const stat = (k, v) => h("div", { class: "stat" }, h("span", {}, k), h("b", {}, v));
  $("#statusList").replaceChildren(
    stat("Gamepad", L.device.connected ? L.device.name : "not connected"),
    stat("Control", L.enabled ? "active" : "paused"),
    stat("Profile", L.profile),
    stat("Precision mode", L.precision ? "on" : "off"),
    stat("Pressed", L.pressed.join(", ") || "—"),
    stat("Running macros", L.macros_running.join(", ") || "—"),
  );
  const fmt = (t) => new Date(t * 1000).toLocaleTimeString();
  const log = $("#log");
  const lines = L.log.slice().reverse().map((e) => h("div", {}, `${fmt(e.t)}  ${e.msg}`));
  if (log.childElementCount !== lines.length || log.firstChild?.textContent !== lines[0]?.textContent) {
    log.replaceChildren(...(lines.length ? lines : [h("div", {}, "no activity yet")]));
  }
}

// ---- Controller (button remapping) --------------------------------------
// Visual map: click a button on the screen, then press the physical button that
// should act as it. map = { rawName: targetName } (rawName = what the pad reports).
const PAD_GROUPS = [
  ["Shoulders & triggers", ["LB", "RB", "LT", "RT"]],
  ["Face buttons", ["Y", "X", "B", "A"]],
  ["D-pad", ["Up", "Left", "Right", "Down"]],
  ["Menu", ["Back", "Guide", "Start"]],
  ["Stick clicks", ["LS", "RS"]],
  ["Extra", ["Misc", "Paddle1", "Paddle2", "Paddle3", "Paddle4", "Touchpad"]],
];
const STANDARD = new Set(PAD_GROUPS.flatMap(([, list]) => list));

function currentDevice() { return S.live?.device?.connected ? S.live.device.name : null; }

async function saveRemap(device, map) {
  try {
    const r = await api("PUT", "remap", { device, map });
    if (Object.keys(r.map).length) S.remaps[device] = r.map; else delete S.remaps[device];
    return true;
  } catch (e) { toast("Saving mapping failed: " + e.message, true); return false; }
}

// Make physical button `raw` act as `target`, keeping every target single-sourced.
function assign(map, raw, target) {
  for (const [r, t] of Object.entries(map)) {
    if (t === target && r !== raw) {
      if (STANDARD.has(r)) map[r] = "none"; else delete map[r];
    }
  }
  // A button still reporting `target` under its own (unmapped) name would double up.
  if (raw !== target && !(target in map)) map[target] = "none";
  if (raw === target) delete map[raw]; else map[raw] = target;
}

// Raw names currently feeding `target`.
function sourcesOf(map, target) {
  const src = Object.entries(map).filter(([, t]) => t === target).map(([r]) => r);
  if (!(target in map)) src.unshift(target);
  return src;
}

async function remapButton(chip, target) {
  const device = currentDevice();
  if (!device) return;
  if (S.capture && S.capture.el === chip) {   // second click cancels
    S.capture.cancel();
    return;
  }
  if (S.capture) S.capture.cancel();
  // Pause control so the press doesn't click or type anything.
  const wasEnabled = S.live.enabled;
  if (wasEnabled) await api("POST", "enabled", { enabled: false }).catch(() => {});
  const restore = () => { if (wasEnabled) api("POST", "enabled", { enabled: true }).catch(() => {}); };

  chip.classList.add("capturing");
  const hint = $("#remapHint");
  if (hint) hint.textContent = `Press the controller button that should act as ${inputLabel(target)}…  (click it again to cancel)`;
  const done = () => {
    chip.classList.remove("capturing");
    if (hint) hint.textContent = "Click a button, then press the physical button you want for it.";
    clearTimeout(timer);
  };
  const timer = setTimeout(() => { if (S.capture?.el === chip) S.capture.cancel(); }, 10000);
  S.capture = {
    el: chip, raw: true, since: S.live.now,
    cancel: () => { S.capture = null; done(); restore(); },
    onCapture: async (raw) => {
      done();
      if (raw.includes("_")) {  // stick direction – not a button
        toast("That was a stick movement — press a button instead", true);
        restore();
        return;
      }
      const map = { ...(S.remaps[device] || {}) };
      assign(map, raw, target);
      if (await saveRemap(device, map)) toast(raw === target ? `${target} reset to default` : `${raw} → ${target}`);
      restore();
      render();
    },
  };
}

function renderController() {
  const device = currentDevice();
  S.renderedDevice = device || "";
  if (!device) {
    view().append(h("div", { class: "card" }, h("h2", {}, "Controller"),
      h("p", { class: "hint" }, "No controller connected. Turn it on or pair it, and it will show up here.")));
    return;
  }
  const map = { ...(S.remaps[device] || {}) };
  const persist = () => saveRemap(device, map).then(() => render());
  const targets = S.meta.remap_targets;

  const chip = (target) => {
    const src = sourcesOf(map, target);
    const changed = !(src.length === 1 && src[0] === target);
    const el = h("button", { class: "remap-chip" + (changed ? " changed" : "") + (src.length ? "" : " empty"),
      "data-input": target, title: "Click, then press the controller button for " + inputLabel(target) },
      h("span", { class: "remap-name" }, target),
      h("span", { class: "remap-src" }, src.length ? (changed ? "← " + src.join(", ") : "default") : "unassigned"));
    el.addEventListener("click", () => remapButton(el, target));
    return el;
  };

  const disabled = Object.entries(map).filter(([, t]) => t === "none").map(([r]) => r);
  const rows = Object.entries(map).map(([raw, t]) => h("tr", {},
    h("td", {}, h("code", {}, raw)), h("td", {}, "→"),
    h("td", {}, select(targets.map((x) => [x, x === "none" ? "— disabled —" : inputLabel(x)]), t,
      (v) => { map[raw] = v; persist(); })),
    h("td", {}, h("button", { class: "btn small icon danger", title: "Remove",
      onclick: () => { delete map[raw]; persist(); } }, "✕"))));

  view().append(
    h("div", { class: "card" },
      h("div", { class: "row", style: "margin-bottom:4px" },
        h("h2", {}, device), h("div", { class: "spacer" }),
        Object.keys(map).length ? h("button", { class: "btn small danger", onclick: () => {
          if (confirm("Remove all remapping for this controller?")) saveRemap(device, {}).then(() => render());
        } }, "Reset to default") : null),
      h("p", { class: "hint", id: "remapHint" }, "Click a button, then press the physical button you want for it."),
      h("div", { class: "remap-layout" },
        PAD_GROUPS.map(([title, list]) => h("div", { class: "remap-group" },
          h("h3", {}, title), h("div", { class: "remap-grid" }, list.map(chip))))),
      h("div", { class: "row center", style: "margin-top:14px; gap:24px" },
        h("span", { class: "hint", style: "margin:0" }, "Controller reports: ", h("b", { id: "rawNow" }, "—")),
        h("span", { class: "hint", style: "margin:0" }, "gppcc sees: ", h("b", { id: "mappedNow" }, "—"))),
      disabled.length ? h("p", { class: "hint", style: "margin-top:10px; text-align:center" },
        `Disabled (not assigned to anything): ${disabled.join(", ")}`) : null),
    h("div", { class: "card" },
      h("h3", {}, "Mapping details"),
      rows.length
        ? h("table", {}, h("thead", {}, h("tr", {}, ["Controller reports", "", "Acts as", ""].map((t) => h("th", {}, t)))),
            h("tbody", {}, rows))
        : h("p", { class: "hint" }, "No remapping — buttons are used as the controller reports them.")));
  updateControllerLive();
}

function updateControllerLive() {
  const a = $("#rawNow"), b = $("#mappedNow");
  if (!a || !S.live) return;
  a.textContent = S.live.raw_pressed.join(", ") || "—";
  b.textContent = S.live.pressed.join(", ") || "—";
  const pressed = new Set(S.live.pressed);
  document.querySelectorAll(".remap-chip").forEach((c) => c.classList.toggle("on", pressed.has(c.dataset.input)));
}

// ---- Cursor & Scroll ----------------------------------------------------
function rangeField(label, obj, key, min, max, step, hint) {
  const num = h("input", { type: "number", min, max, step, value: obj[key] });
  const rng = h("input", { type: "range", min, max, step, value: obj[key] });
  const set = (v) => {
    v = Number(v);
    if (Number.isNaN(v)) return;
    obj[key] = v; num.value = v; rng.value = v; changed();
  };
  num.addEventListener("change", (e) => set(e.target.value));
  rng.addEventListener("input", (e) => set(e.target.value));
  return h("div", { class: "field" },
    h("span", {}, label),
    h("div", { class: "range-row" }, rng, num),
    hint ? h("div", { class: "hint", style: "margin:0" }, hint) : null);
}

function checkField(label, obj, key) {
  return h("label", { class: "field inline", style: "margin-bottom:12px" },
    h("input", { type: "checkbox", checked: obj[key], onchange: (e) => { obj[key] = e.target.checked; changed(); } }),
    h("span", {}, label));
}

function stickField(label, obj) {
  return h("div", { class: "field" }, h("span", {}, label),
    select(S.meta.sticks.map((s) => [s, s === "none" ? "Off" : s[0].toUpperCase() + s.slice(1) + " stick"]),
      obj.stick, (v) => { obj.stick = v; changed(); }));
}

function renderCursor() {
  const P = S.profile;
  const c = P.cursor, s = P.scroll;
  view().append(
    h("div", { class: "grid two" },
      h("div", { class: "card" },
        h("h3", {}, "Cursor"),
        stickField("Stick", c),
        rangeField("Max speed (px/s)", c, "max_speed", 100, 6000, 50, "Speed at full stick deflection."),
        rangeField("Response curve", c, "curve", 0.5, 5, 0.1, "1 = linear. Higher = finer control near the center, fast at the edge."),
        rangeField("Deadzone", c, "deadzone", 0, 0.6, 0.01, "Ignore small stick drift."),
        rangeField("Precision multiplier", c, "precision_factor", 0.05, 1, 0.05, "Speed while a “Precision” binding is held/toggled."),
        checkField("Invert Y axis", c, "invert_y")),
      h("div", { class: "card" },
        h("h3", {}, "Scroll"),
        stickField("Stick", s),
        rangeField("Speed (notches/s)", s, "speed", 1, 100, 1),
        rangeField("Response curve", s, "curve", 0.5, 5, 0.1),
        rangeField("Deadzone", s, "deadzone", 0, 0.6, 0.01),
        checkField("Horizontal scrolling", s, "horizontal"),
        checkField("Invert direction", s, "invert"))),
    h("div", { class: "card" },
      h("h3", {}, "Buttons"),
      rangeField("Analog press threshold", P, "trigger_threshold", 0.05, 0.95, 0.05,
        "How far LT/RT or a stick direction must travel to count as “pressed”."),
      rangeField("Repeat delay (ms)", P, "repeat_delay_ms", 0, 2000, 10, "For “repeat” bindings: wait before repeating starts."),
      rangeField("Repeat interval (ms)", P, "repeat_interval_ms", 10, 1000, 5, "Time between repeats (bindings can override)."),
      h("button", { class: "btn", onclick: () => {
        const d = S.meta.defaults;
        Object.assign(P.cursor, clone(d.cursor));
        Object.assign(P.scroll, clone(d.scroll));
        P.trigger_threshold = d.trigger_threshold;
        P.repeat_delay_ms = d.repeat_delay_ms;
        P.repeat_interval_ms = d.repeat_interval_ms;
        changed(); render();
      } }, "Reset to defaults")));
}

// ---- Bindings -------------------------------------------------------------
function keysInput(obj, key = "keys", placeholder = "e.g. mod+c") {
  return h("input", {
    type: "text", list: "keyNames", value: obj[key] || "", placeholder,
    oninput: (e) => { obj[key] = e.target.value; changed(); },
  });
}

function numInput(obj, key, label, attrs = {}) {
  return h("label", {}, label,
    h("input", { type: "number", value: obj[key] ?? 0, ...attrs,
      oninput: (e) => { obj[key] = Number(e.target.value); changed(); } }));
}

function actionParams(a) {
  switch (a.type) {
    case "key": return [keysInput(a)];
    case "mouse": return [
      select(S.meta.mouse_buttons, a.button || "left", (v) => { a.button = v; changed(); }),
      numInput(a, "count", "clicks", { min: 1, max: 3, style: "width:52px" })];
    case "scroll": return [numInput(a, "dx", "x", { step: 0.5, style: "width:64px" }), numInput(a, "dy", "y", { step: 0.5, style: "width:64px" })];
    case "move": return [numInput(a, "dx", "dx"), numInput(a, "dy", "dy")];
    case "text": return [h("input", { type: "text", value: a.text || "", placeholder: "text to type",
      oninput: (e) => { a.text = e.target.value; changed(); } })];
    case "macro": {
      const names = Object.keys(S.profile.macros);
      return [select([["", "— choose —"], ...names], a.name || "", (v) => { a.name = v; changed(); })];
    }
    case "profile":
      return [select([["", "— choose —"], ...S.profiles.names], a.name || "", (v) => { a.name = v; changed(); })];
    default: return [];
  }
}

function defaultAction(type) {
  return {
    key: { type, keys: "" }, mouse: { type, button: "left", count: 1 }, scroll: { type, dx: 0, dy: -3 },
    move: { type, dx: 0, dy: 0 }, text: { type, text: "" }, macro: { type, name: Object.keys(S.profile.macros)[0] || "" },
    profile: { type, name: "" },
  }[type] || { type };
}

function renderBindings() {
  const P = S.profile;
  const inputs = S.meta.inputs;
  const inputOpts = inputs.map((i) => [i, inputLabel(i)]);
  const extraInputs = P.bindings.map((b) => b.input).filter((i) => !inputs.includes(i));
  const allInputOpts = [...inputOpts, ...[...new Set(extraInputs)].map((i) => [i, i])];

  const rows = P.bindings.map((b, idx) => {
    const inputCell = h("div", { class: "row" },
      select(allInputOpts, b.input, (v) => { b.input = v; changed(); }),
      h("button", { class: "btn icon small", title: "Press a gamepad button to capture",
        onclick: (e) => startCapture(e.currentTarget, (name) => { b.input = name; changed(); renderBindings_refresh(); }) }, "🎮"));
    const withCell = select([["", "—"], ...allInputOpts], b.with || "", (v) => { b.with = v || null; changed(); });
    const triggerCell = select(S.meta.triggers, b.trigger, (v) => { b.trigger = v; changed(); renderBindings_refresh(); },
      { title: TRIGGER_HELP[b.trigger] });
    const typeCell = select(S.meta.action_types.map((t) => [t, ACTION_LABELS[t] || t]), b.action.type,
      (v) => { b.action = defaultAction(v); changed(); renderBindings_refresh(); });
    const params = h("div", { class: "params" }, actionParams(b.action),
      b.trigger === "repeat" ? numInput(b, "repeat_ms", "every ms", { min: 10, placeholder: P.repeat_interval_ms, style: "width:70px" }) : null);

    return h("tr", { "data-input": b.input, "data-with": b.with || "" },
      h("td", {}, inputCell), h("td", {}, withCell), h("td", {}, triggerCell),
      h("td", {}, typeCell), h("td", {}, params),
      h("td", {}, h("div", { class: "row" },
        h("button", { class: "btn small", title: "Fire this action in 2 seconds (switch to the target window)",
          onclick: () => testAction(b.action) }, "Test"),
        h("button", { class: "btn small icon", title: "Duplicate",
          onclick: () => { P.bindings.splice(idx + 1, 0, { ...clone(b), id: uid() }); changed(); renderBindings_refresh(); } }, "⧉"),
        h("button", { class: "btn small icon danger", title: "Delete",
          onclick: () => { P.bindings.splice(idx, 1); changed(); renderBindings_refresh(); } }, "✕"))));
  });

  view().append(
    h("div", { class: "card" },
      h("div", { class: "row", style: "margin-bottom:8px" },
        h("h2", {}, "Bindings"), h("div", { class: "spacer" }),
        h("button", { class: "btn primary", onclick: () => {
          P.bindings.push({ id: uid(), input: "A", with: null, trigger: "press", action: { type: "key", keys: "" } });
          changed(); renderBindings_refresh();
        } }, "+ Add binding")),
      h("p", { class: "hint" },
        "Map a gamepad input to an operation. “With” makes it a combo (hold that button first). ",
        "Key shortcuts use names joined by +, e.g. ", h("code", {}, "ctrl+shift+t"), ", ", h("code", {}, "alt+f4"), ", ",
        h("code", {}, "mod+c"), " (", h("code", {}, "mod"), " = Cmd on Mac, Ctrl on Windows), ",
        h("code", {}, "f5"), ", ", h("code", {}, "media_volume_up"), "."),
      h("table", {},
        h("thead", {}, h("tr", {}, ["Input", "With", "Trigger", "Operation", "Parameters", ""].map((t) => h("th", {}, t)))),
        h("tbody", {}, rows.length ? rows : h("tr", {}, h("td", { colspan: 6, class: "hint" }, "No bindings yet."))))),
    h("div", { class: "card" },
      h("h3", {}, "Triggers"),
      Object.entries(TRIGGER_HELP).map(([k, v]) => h("div", { class: "stat" }, h("b", {}, k), h("span", { class: "hint", style: "margin:0" }, v)))));
}

function renderBindings_refresh() {
  if (S.tab !== "bindings") return;
  const y = window.scrollY;
  render();
  window.scrollTo(0, y);
}

function highlightBindings() {
  if (!S.live) return;
  const pressed = new Set(S.live.pressed);
  document.querySelectorAll("tr[data-input]").forEach((tr) => {
    const w = tr.dataset.with;
    tr.classList.toggle("lit", pressed.has(tr.dataset.input) && (!w || pressed.has(w)));
  });
}

async function testAction(action) {
  try {
    await api("POST", "test-action", { action, delay: 2 });
    toast("Firing in 2 s — switch to the target window");
  } catch (e) { toast(e.message, true); }
}

// ---- Macros -------------------------------------------------------------
function stepParams(s) {
  switch (s.op) {
    case "tap": case "key_down": case "key_up": return [keysInput(s)];
    case "text": return [h("input", { type: "text", value: s.text || "", placeholder: "text to type", style: "width:260px",
      oninput: (e) => { s.text = e.target.value; changed(); } })];
    case "click": return [select(S.meta.mouse_buttons, s.button || "left", (v) => { s.button = v; changed(); }),
      numInput(s, "count", "×", { min: 1, max: 3, style: "width:52px" })];
    case "mouse_down": case "mouse_up":
      return [select(S.meta.mouse_buttons, s.button || "left", (v) => { s.button = v; changed(); })];
    case "move": return [numInput(s, "dx", "dx"), numInput(s, "dy", "dy")];
    case "move_to": return [numInput(s, "x", "x"), numInput(s, "y", "y"),
      h("button", { class: "btn small", title: "Capture the current cursor position in 3 s",
        onclick: () => captureCursor(s) }, "pick (3s)")];
    case "scroll": return [numInput(s, "dx", "x", { step: 0.5, style: "width:64px" }), numInput(s, "dy", "y", { step: 0.5, style: "width:64px" })];
    case "wait": return [numInput(s, "ms", "ms", { min: 0, step: 10 })];
    default: return [];
  }
}

function defaultStep(op) {
  return {
    tap: { op, keys: "" }, key_down: { op, keys: "" }, key_up: { op, keys: "" }, text: { op, text: "" },
    click: { op, button: "left", count: 1 }, mouse_down: { op, button: "left" }, mouse_up: { op, button: "left" },
    move: { op, dx: 0, dy: 0 }, move_to: { op, x: 0, y: 0 }, scroll: { op, dx: 0, dy: -3 }, wait: { op, ms: 100 },
  }[op];
}

function captureCursor(step) {
  toast("Move the cursor to the spot… capturing in 3 s");
  setTimeout(async () => {
    try {
      const st = await api("GET", "state");
      if (st.cursor) { step.x = st.cursor[0]; step.y = st.cursor[1]; changed(); render(); toast(`Captured ${step.x}, ${step.y}`); }
    } catch (e) { toast(e.message, true); }
  }, 3000);
}

function renameMacro(oldName, newName) {
  const P = S.profile;
  newName = newName.trim();
  if (!newName || newName === oldName) return false;
  if (P.macros[newName]) { toast("A macro with that name exists", true); return false; }
  const rebuilt = {};
  for (const [k, v] of Object.entries(P.macros)) rebuilt[k === oldName ? newName : k] = v;
  P.macros = rebuilt;
  P.bindings.forEach((b) => { if (b.action.type === "macro" && b.action.name === oldName) b.action.name = newName; });
  S.selectedMacro = newName;
  changed();
  return true;
}

function renderMacros() {
  const P = S.profile;
  const names = Object.keys(P.macros);
  if (!S.selectedMacro || !P.macros[S.selectedMacro]) S.selectedMacro = names[0] || null;
  const running = new Set(S.live?.macros_running || []);

  const list = h("div", { class: "macro-list", id: "macroList" },
    names.map((n) => h("button", { class: n === S.selectedMacro ? "active" : "", "data-name": n,
      onclick: () => { S.selectedMacro = n; render(); } }, n, running.has(n) ? h("span", { class: "running" }, "● running") : null)),
    h("button", { onclick: () => {
      let i = 1; let n = "New macro";
      while (P.macros[n]) n = `New macro ${++i}`;
      P.macros[n] = { repeat: 1, steps: [{ op: "tap", keys: "" }] };
      S.selectedMacro = n; changed(); render();
    } }, "+ New macro"));

  let editor;
  const m = S.selectedMacro && P.macros[S.selectedMacro];
  if (!m) {
    editor = h("div", { class: "card" }, h("p", { class: "hint" }, "No macros yet. Create one on the left."));
  } else {
    const name = S.selectedMacro;
    const usedBy = P.bindings.filter((b) => b.action.type === "macro" && b.action.name === name)
      .map((b) => (b.with ? `${b.with} + ` : "") + b.input + ` (${b.trigger})`);
    const steps = m.steps.map((s, i) => h("div", { class: "step" },
      h("span", { class: "num" }, i + 1),
      select(S.meta.macro_ops.map((o) => [o, OP_LABELS[o] || o]), s.op, (v) => { m.steps[i] = defaultStep(v); changed(); render(); }),
      h("div", { class: "params" }, stepParams(s)),
      h("button", { class: "btn small icon", title: "Move up", disabled: i === 0,
        onclick: () => { [m.steps[i - 1], m.steps[i]] = [m.steps[i], m.steps[i - 1]]; changed(); render(); } }, "↑"),
      h("button", { class: "btn small icon", title: "Move down", disabled: i === m.steps.length - 1,
        onclick: () => { [m.steps[i + 1], m.steps[i]] = [m.steps[i], m.steps[i + 1]]; changed(); render(); } }, "↓"),
      h("button", { class: "btn small icon", title: "Duplicate",
        onclick: () => { m.steps.splice(i + 1, 0, clone(s)); changed(); render(); } }, "⧉"),
      h("button", { class: "btn small icon danger", title: "Delete",
        onclick: () => { m.steps.splice(i, 1); changed(); render(); } }, "✕")));

    const quick = ["tap", "text", "click", "wait", "key_down", "key_up", "move", "move_to", "scroll"].map((op) =>
      h("button", { class: "btn small", onclick: () => { m.steps.push(defaultStep(op)); changed(); render(); } }, "+ " + OP_LABELS[op]));

    editor = h("div", { class: "card" },
      h("div", { class: "row", style: "margin-bottom:12px" },
        h("label", { class: "field inline" }, h("span", {}, "Name"),
          h("input", { type: "text", value: name, style: "width:220px",
            onchange: (e) => { if (!renameMacro(name, e.target.value)) e.target.value = name; else render(); } })),
        h("label", { class: "field inline" }, h("span", {}, "Repeat"),
          h("input", { type: "number", min: 1, value: m.repeat, style: "width:70px",
            oninput: (e) => { m.repeat = Math.max(1, Number(e.target.value) || 1); changed(); } })),
        h("div", { class: "spacer" }),
        h("button", { class: "btn primary", title: "Runs once after 3 s so you can switch windows",
          onclick: async () => {
            try { await api("POST", "test-macro", { name, macro: m, delay: 3 }); toast("Running in 3 s — switch to the target window"); }
            catch (e) { toast(e.message, true); }
          } }, "▶ Test (3s)"),
        h("button", { class: "btn danger", onclick: () => {
          if (!confirm(`Delete macro “${name}”?`)) return;
          delete P.macros[name]; S.selectedMacro = null; changed(); render();
        } }, "Delete")),
      h("p", { class: "hint" },
        "Repeat applies to “press” triggers. With “hold” the macro loops while held; with “toggle” it loops until pressed again. ",
        "Keys left down by key_down / mouse_down are released automatically when the macro stops. ",
        usedBy.length ? `Bound to: ${usedBy.join(", ")}.` : "Not bound yet — add a binding with operation “Run macro”."),
      h("div", {}, steps.length ? steps : h("p", { class: "hint" }, "No steps.")),
      h("div", { class: "row", style: "margin-top:12px" }, quick));
  }

  view().append(h("div", { class: "macro-layout" }, h("div", {}, list), editor));
}

function updateMacroRunning() {
  const running = new Set(S.live?.macros_running || []);
  document.querySelectorAll("#macroList button[data-name]").forEach((b) => {
    const has = b.querySelector(".running");
    if (running.has(b.dataset.name) && !has) b.append(h("span", { class: "running" }, "● running"));
    if (!running.has(b.dataset.name) && has) has.remove();
  });
}

// ---- Profiles -----------------------------------------------------------
function renderProfiles() {
  const promptName = (msg, def = "") => {
    const n = prompt(msg, def);
    return n === null ? null : n.trim();
  };
  const act = async (fn, okMsg) => {
    try { await fn(); await loadProfiles(); render(); if (okMsg) toast(okMsg); }
    catch (e) { toast(e.message, true); }
  };

  const items = S.profiles.names.map((n) => h("div", { class: "profile-item" },
    h("span", { class: "name" }, n, " ", n === S.profiles.active ? h("span", { class: "badge" }, "active") : null),
    n !== S.profiles.active ? h("button", { class: "btn small", onclick: () => activateProfile(n) }, "Activate") : null,
    h("button", { class: "btn small", onclick: () => {
      const to = promptName("Name for the copy:", n + " copy");
      if (to) act(() => api("POST", `profile/${encodeURIComponent(n)}/copy`, { to }), `Copied to “${to}”`);
    } }, "Duplicate"),
    h("button", { class: "btn small", onclick: async () => {
      const to = promptName("Rename profile:", n);
      if (!to || to === n) return;
      if (saveTimer) await saveNow();
      act(() => api("POST", `profile/${encodeURIComponent(n)}/rename`, { to }), "Renamed");
    } }, "Rename"),
    h("button", { class: "btn small", onclick: async () => {
      const p = await api("GET", "profile/" + encodeURIComponent(n));
      const a = h("a", { href: URL.createObjectURL(new Blob([JSON.stringify(p, null, 2)], { type: "application/json" })),
        download: `gppcc-${n}.json` });
      a.click(); URL.revokeObjectURL(a.href);
    } }, "Export"),
    h("button", { class: "btn small danger", onclick: () => {
      if (confirm(`Delete profile “${n}”?`)) act(() => api("DELETE", "profile/" + encodeURIComponent(n)), "Deleted");
    } }, "Delete")));

  const fileInput = h("input", { type: "file", accept: ".json,application/json", class: "hidden",
    onchange: async (e) => {
      const f = e.target.files[0];
      if (!f) return;
      try {
        const data = JSON.parse(await f.text());
        const name = promptName("Import as profile name:", data.name || f.name.replace(/\.json$/i, ""));
        if (!name) return;
        if (S.profiles.names.includes(name) && !confirm(`Overwrite “${name}”?`)) return;
        await act(() => api("PUT", "profile/" + encodeURIComponent(name), data), `Imported “${name}”`);
      } catch (err) { toast("Import failed: " + err.message, true); }
      e.target.value = "";
    } });

  view().append(h("div", { class: "card" },
    h("div", { class: "row", style: "margin-bottom:8px" },
      h("h2", {}, "Profiles"), h("div", { class: "spacer" }),
      h("button", { class: "btn", onclick: () => fileInput.click() }, "Import…"),
      h("button", { class: "btn primary", onclick: () => {
        const name = promptName("New profile name:");
        if (!name) return;
        if (S.profiles.names.includes(name)) return toast("That profile already exists", true);
        act(() => api("PUT", "profile/" + encodeURIComponent(name), { ...clone(S.meta.defaults), name }), `Created “${name}”`);
      } }, "+ New profile")),
    h("p", { class: "hint" },
      "Profiles hold cursor settings, bindings and macros. Make one per app or game and switch from here, ",
      "the header, or a gamepad binding (operation “Switch to profile” / “Next profile”)."),
    items, fileInput));
}

// ---------------------------------------------------------------- boot
async function boot() {
  document.querySelectorAll("#tabs button").forEach((b) =>
    b.addEventListener("click", () => { location.hash = b.dataset.tab; }));
  const fromHash = () => {
    const t = location.hash.slice(1);
    S.tab = ["live", "controller", "cursor", "bindings", "macros", "profiles"].includes(t) ? t : "live";
    if (S.capture?.cancel) S.capture.cancel(); else S.capture = null;
    render();
  };
  window.addEventListener("hashchange", fromHash);
  fromHash();
  $("#profileSelect").addEventListener("change", (e) => activateProfile(e.target.value));
  $("#enabledToggle").addEventListener("change", (e) =>
    api("POST", "enabled", { enabled: e.target.checked }).catch((err) => toast(err.message, true)));
  $("#permButton").addEventListener("click", () => api("POST", "permissions", {}));
  window.addEventListener("beforeunload", () => { if (saveTimer) saveNow(); });

  try {
    S.meta = await api("GET", "meta");
    S.remaps = (await api("GET", "remap")).remaps;
    await loadProfiles();
  } catch (e) {
    view().append(h("div", { class: "card" }, "Could not reach gppcc: " + e.message));
    return;
  }
  document.body.append(h("datalist", { id: "keyNames" }, S.meta.keys.map((k) => h("option", { value: k }))));
  render();
  poll();
}

boot();
