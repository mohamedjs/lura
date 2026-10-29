// Lura dashboard. No framework: one state object, a render per card.
"use strict";

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

// ── token: arrives in the URL fragment (never sent to a server), kept for the tab
const hashToken = new URLSearchParams(location.hash.slice(1)).get("t");
if (hashToken) {
  sessionStorage.setItem("lura-token", hashToken);
  history.replaceState(null, "", location.pathname);
}
const TOKEN = sessionStorage.getItem("lura-token") || "";

let S = null;          // the last /api/state
let connKind = "url";

async function api(method, path, body) {
  const res = await fetch(path, {
    method,
    headers: { "X-Lura-Token": TOKEN, ...(body ? { "Content-Type": "application/json" } : {}) },
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const err = new Error(data.error || `HTTP ${res.status}`);
    err.status = res.status;
    throw err;
  }
  return data;
}

// ── i18n
const uiLang = () => (S && S.settings.ui_language) || "en";
function t(key, vars) {
  const cat = (S && S.i18n[uiLang()]) || {};
  const en = (S && S.i18n.en) || {};
  let text = cat[key] || en[key] || key;
  for (const [k, v] of Object.entries(vars || {})) text = text.replaceAll(`{${k}}`, v);
  return text;
}
function applyI18n() {
  const lang = uiLang();
  document.documentElement.lang = lang;
  document.documentElement.dir = (S.languages[lang] || {}).dir || "ltr";
  $$("[data-i18n]").forEach((el) => (el.textContent = t(el.dataset.i18n)));
  $$("[data-i18n-placeholder]").forEach((el) => (el.placeholder = t(el.dataset.i18nPlaceholder)));
  document.title = `Lura — ${t("app.tagline")}`;
}

// ── toasts
function toast(msg, bad = false) {
  const el = document.createElement("div");
  el.className = "toast" + (bad ? " bad" : "");
  el.textContent = msg;
  $("#toasts").append(el);
  setTimeout(() => el.remove(), bad ? 7000 : 3500);
}
const fail = (e) => toast(e.message, true);

// ── settings
async function save(patch) {
  try {
    const res = await api("PUT", "/api/settings", { patch });
    (res.notes || []).forEach((note) => toast(note));
    S.settings = res.settings;
    S.capabilities = res.capabilities;
    S.restart_pending = res.restart_pending;
    render();
  } catch (e) { fail(e); render(); }
}

function seg(root, value, onPick) {
  $$("button", root).forEach((b) => {
    b.classList.toggle("on", b.dataset.value === value);
    b.onclick = () => onPick(b.dataset.value);
  });
}

// ── render
function renderStatus() {
  const caps = S.capabilities;
  const status = S.status;
  const dot = $("#status-pill .dot");
  let cls = "ok", key = "status.ready";
  if (!caps.can_talk) { cls = "warn"; key = "status.needs_setup"; }
  else if (status === "talking") { cls = "live"; key = "status.talking"; }
  else if (status === "loading" || status === "starting") { cls = "warn"; key = "status.loading"; }
  else if (status === "offline") { cls = "bad"; key = "status.offline"; }
  dot.className = "dot " + cls;
  $("#status-text").textContent = t(key);

  const talk = $("#talk-btn");
  talk.disabled = !caps.can_talk || status === "offline";
  talk.classList.toggle("live", status === "talking");
  $("#hero-sub").textContent = caps.can_talk
    ? t("hero.ready", { word: S.settings.wake_word })
    : t("hero.setup");
  const ul = $("#problems");
  ul.replaceChildren(...caps.problems.map((p) => Object.assign(document.createElement("li"), { textContent: t(p) })));
  $("#stop-btn").hidden = status !== "talking";
  const showRestart = !!S.restart_pending && S.can_restart !== false && status !== "offline";
  $("#restart-btn").hidden = !showRestart && !restarting;
  $("#restart-hint").hidden = !showRestart || restarting;
}

let restarting = false;
async function restartLura() {
  const btn = $("#restart-btn");
  restarting = true;
  btn.classList.add("busy");
  btn.disabled = true;
  toast(t("restart.restarting"));
  try { await api("POST", "/api/restart"); } catch (e) { restarting = false; btn.classList.remove("busy"); btn.disabled = false; return fail(e); }
  // Wait for this Lura to go away, then for the new one to answer. The token
  // survives restarts, so this same page keeps working.
  let wentAway = false;
  const t0 = Date.now();
  while (Date.now() - t0 < 60000) {
    await new Promise((r) => setTimeout(r, 1000));
    try {
      const st = await api("GET", "/api/status");
      if (wentAway || !st.restart_pending) {
        restarting = false;
        btn.classList.remove("busy");
        btn.disabled = false;
        toast(t("restart.done"));
        await refresh();
        return;
      }
    } catch (_) { wentAway = true; }
  }
  restarting = false;
  btn.classList.remove("busy");
  btn.disabled = false;
  toast(t("restart.timeout"), true);
}

// The key rows are built once and then only updated: rebuilding them on every
// render (a status poll, a connector starting) wiped what was being typed or
// pasted and took the cursor away mid-word.
const keyRows = {};

function buildKeyRow(p) {
  const r = { row: Object.assign(document.createElement("div"), { className: "key-row" }) };
  r.who = Object.assign(document.createElement("div"), { className: "who" });
  r.who.innerHTML = `<b></b><small></small><div class="tags"></div>`;
  r.field = Object.assign(document.createElement("div"), { className: "key-field" });
  r.input = Object.assign(document.createElement("input"), {
    type: "password", autocomplete: "off", spellcheck: false, autocapitalize: "off",
  });
  r.input.setAttribute("autocorrect", "off");
  // Show what was pasted. A password field also locks macOS to a Latin
  // keyboard (secure input); keys are Latin anyway, but seeing them helps.
  r.eye = Object.assign(document.createElement("button"), { className: "btn ghost eye", type: "button", textContent: "👁" });
  r.eye.onclick = () => { r.input.type = r.input.type === "password" ? "text" : "password"; r.input.focus(); };
  r.field.append(r.input, r.eye);
  r.actions = Object.assign(document.createElement("div"), { className: "row" });
  r.save = Object.assign(document.createElement("button"), { className: "btn primary", type: "button" });
  r.save.onclick = async () => {
    const key = r.input.value.trim();
    if (!key) { toast(t("keys.empty"), true); r.input.focus(); return; }
    r.save.disabled = true;
    r.save.textContent = t("keys.testing");
    try {
      const res = await api("PUT", `/api/keys/${p}`, { key });
      Object.assign(S, { keys: res.keys, capabilities: res.capabilities, settings: res.settings, restart_pending: res.restart_pending });
      r.input.value = "";
      r.input.type = "password";
      toast(`${S.keys[p].label}: ${res.message}`);
    } catch (e) { fail(e); }
    r.save.disabled = false;
    render();
  };
  // A block body: an on… handler that returns false cancels the keystroke, and
  // `e.key === "Enter" && …` is false for every other key — nothing could be
  // typed or pasted (#keys on macOS).
  r.input.onkeydown = (e) => { if (e.key === "Enter") r.save.click(); };
  r.link = Object.assign(document.createElement("a"), { className: "btn ghost", target: "_blank", rel: "noopener" });
  r.remove = Object.assign(document.createElement("button"), { className: "btn ghost danger", type: "button" });
  r.remove.onclick = async () => {
    try { Object.assign(S, await api("DELETE", `/api/keys/${p}`)); render(); } catch (e) { fail(e); }
  };
  r.actions.append(r.save, r.link, r.remove);
  r.row.append(r.who, r.field, r.actions);
  return r;
}

function renderKeys() {
  const box = $("#keys");
  for (const p of S.providers) {
    const k = S.keys[p];
    const r = keyRows[p] || (keyRows[p] = buildKeyRow(p));
    if (r.row.parentNode !== box) box.append(r.row);
    $("b", r.who).textContent = k.label;
    $("small", r.who).textContent = k.set ? `${t("keys.saved")} ${k.masked}${k.source === "env" ? " · env" : ""}` : t("keys.none");
    const tagList = [["tag", t("keys.tag.brain")]];
    if (k.voice) tagList.push(["tag voice", t("keys.tag.voice")]);
    if (k.realtime) tagList.push(["tag voice", t("keys.tag.realtime")]);
    $(".tags", r.who).replaceChildren(...tagList.map(([c, txt]) => Object.assign(document.createElement("span"), { className: c, textContent: txt })));
    r.input.placeholder = k.set ? t("keys.replace") : t("keys.paste");
    r.eye.title = t("keys.show");
    if (!r.save.disabled) r.save.textContent = t("keys.save");
    r.link.href = k.key_url;
    r.link.textContent = t("keys.get");
    r.remove.textContent = t("keys.remove");
    r.remove.hidden = !(k.set && k.source === "file");
  }
}

let catalog = null;          // {provider: [{id, name, hears}]} for providers with keys
let catalogFor = "";         // which key set it was loaded for

async function loadCatalog() {
  const keyed = S.providers.filter((p) => S.keys[p].set).join(",");
  if (catalog && catalogFor === keyed) return;
  catalogFor = keyed;
  catalog = null;
  renderModels();
  try { catalog = (await api("GET", "/api/catalog")).catalog; } catch (e) { catalog = {}; fail(e); }
  renderModels();
}

function pickModel(provider, id) {
  save({ brain_provider: provider, models: { [provider]: id } });
}

function renderModels() {
  const s = S.settings, box = $("#model-list");
  const cur = s.models[s.brain_provider] || "";
  const curEntry = (catalog && (catalog[s.brain_provider] || []).find((m) => m.id === cur));
  const head = $("#current-model");
  head.replaceChildren();
  head.append(Object.assign(document.createElement("small"), { textContent: t("brain.current", { provider: S.keys[s.brain_provider].label }) }),
              Object.assign(document.createElement("b"), { textContent: cur }));
  if (curEntry && curEntry.hears) head.append(Object.assign(document.createElement("span"), { className: "tag voice", textContent: t("brain.hears_short") }));

  if (catalog === null) { box.replaceChildren(Object.assign(document.createElement("div"), { className: "model-empty", textContent: t("brain.loading") })); return; }
  const q = $("#model-search").value.trim().toLowerCase();
  const rows = [];
  for (const p of S.providers) {
    if (!S.keys[p].set) continue;
    const models = (catalog[p] || []).filter((m) => !q || m.id.toLowerCase().includes(q) || m.name.toLowerCase().includes(q));
    const group = Object.assign(document.createElement("div"), { className: "model-group" });
    group.append(Object.assign(document.createElement("span"), { textContent: S.keys[p].label }),
                 Object.assign(document.createElement("span"), { textContent: t("brain.count", { n: models.length }) }));
    rows.push(group);
    for (const m of models.slice(0, q ? 200 : 60)) {
      const b = Object.assign(document.createElement("button"), { className: "model" + (p === s.brain_provider && m.id === cur ? " on" : ""), title: m.name });
      b.setAttribute("role", "option");
      b.append(Object.assign(document.createElement("span"), { className: "mid", textContent: m.id }));
      if (m.hears) b.append(Object.assign(document.createElement("span"), { className: "ear", textContent: "🎧 " + t("brain.hears_short") }));
      b.onclick = () => pickModel(p, m.id);
      rows.push(b);
    }
    // Any id the provider accepts still works, listed or not.
    if (q && !models.some((m) => m.id.toLowerCase() === q)) {
      const raw = $("#model-search").value.trim();
      const b = Object.assign(document.createElement("button"), { className: "model", textContent: t("brain.custom", { id: raw, provider: S.keys[p].label }) });
      b.onclick = () => pickModel(p, raw);
      rows.push(b);
    }
  }
  if (!rows.length) rows.push(Object.assign(document.createElement("div"), { className: "model-empty", textContent: t("brain.no_models") }));
  box.replaceChildren(...rows);
}

function renderBrain() {
  const s = S.settings;
  seg($("#mode"), s.mode, (v) => save({ mode: v }));
  $("#mode-hint").textContent = t(s.mode === "realtime" ? "brain.mode.realtime_hint" : "brain.mode.pipeline_hint");
  $("#brain-pipeline").hidden = s.mode === "realtime";
  const search = $("#model-search");
  search.oninput = renderModels;
  renderModels();
  loadCatalog();
  const effort = $("#effort");
  effort.value = s.reasoning_effort;
  effort.onchange = () => save({ reasoning_effort: effort.value });

  const p = s.brain_provider, m = s.models[p] || "";
  const hears = p === "gemini" || (p === "openrouter" && (m.startsWith("google/gemini") || m.startsWith("openai/gpt-audio")));
  $("#hearing-hint").textContent = t(hears ? "brain.hears" : "brain.transcribes");
}

function renderVoice() {
  const s = S.settings;
  const vp = $("#voice-provider");
  vp.value = s.voice_provider;
  vp.onchange = () => save({ voice_provider: vp.value });
  seg($("#gender"), s.voice_gender, (v) => save({ voice_gender: v }));
  const box = $("#voices");
  box.replaceChildren(...S.voices[s.voice_gender].map((name) => {
    const chip = Object.assign(document.createElement("div"), { className: "voice" + (name === s.voice_name ? " on" : "") });
    chip.setAttribute("role", "button");
    chip.tabIndex = 0;
    chip.append(Object.assign(document.createElement("span"), { textContent: name }));
    const play = Object.assign(document.createElement("button"), { className: "play", textContent: "▶", title: t("voice.preview") });
    play.onclick = async (e) => {
      e.stopPropagation();
      try { await api("POST", "/api/voice/preview", { voice: name }); toast(t("voice.previewing", { name })); } catch (err) { fail(err); }
    };
    chip.append(play);
    chip.onclick = () => save({ voice_name: name });
    chip.onkeydown = (e) => { if (e.key === "Enter") chip.click(); };
    return chip;
  }));
  bindText("#tts-style", "tts_style");
}

function bindText(sel, name, parse = (v) => v) {
  const el = $(sel);
  if (document.activeElement !== el) el.value = S.settings[name] ?? "";
  el.onchange = () => save({ [name]: parse(el.value) });
}
function bindCheck(sel, name) {
  const el = $(sel);
  el.checked = !!S.settings[name];
  el.onchange = () => save({ [name]: el.checked });
}

function renderLanguage() {
  seg($("#conv-lang"), S.settings.language, (v) => save({ language: v }));
  seg($("#ui-lang"), S.settings.ui_language, (v) => save({ ui_language: v }));
  bindText("#assistant-name", "assistant_name");
  bindText("#user-name", "user_name");
  bindText("#wake-word", "wake_word", (v) => v.trim().toLowerCase());
}

let chromeState = null;
async function loadChrome() {
  try { chromeState = await api("GET", "/api/chrome"); } catch (_) { chromeState = null; }
  renderChrome();
}
function renderChrome() {
  const ok = !!(chromeState && chromeState.connected);
  $("#chrome-dot").className = "dot " + (chromeState ? (ok ? "ok" : "warn") : "");
  $("#chrome-state").textContent = chromeState ? t(ok ? "chrome.connected" : "chrome.off") : "";
  $("#chrome-setup").hidden = ok;
  $("#chrome-setup").onclick = async () => {
    try { await api("POST", "/api/chrome/setup"); toast(t("chrome.opened")); } catch (e) { fail(e); }
    setTimeout(loadChrome, 8000);
  };
}

function renderConnectors() {
  renderChrome();
  const box = $("#connectors");
  box.replaceChildren(...S.connectors.map((c) => {
    const row = Object.assign(document.createElement("div"), { className: "conn" });
    const dot = Object.assign(document.createElement("span"), {
      className: "dot " + ({ ready: "ok", starting: "warn", error: "bad" }[c.status] || ""),
      title: t(`conn.status.${c.status}`),
    });
    const meta = Object.assign(document.createElement("div"), { className: "meta" });
    const title = document.createElement("b");
    title.textContent = c.name + (c.builtin ? ` · ${t("conn.builtin")}` : "");
    const desc = document.createElement("small");
    desc.textContent = c.description || (c.transport === "http" ? c.url : `${c.command} ${c.args.join(" ")}`);
    const info = document.createElement("small");
    info.textContent = `${t(`conn.status.${c.status}`)}${c.tools.length ? ` · ${t("conn.tools", { n: c.tools.length })}` : ""}`;
    meta.append(title, desc, info);
    if (c.error) meta.append(Object.assign(document.createElement("div"), { className: "err", textContent: c.error }));

    const sw = document.createElement("label");
    sw.className = "switch";
    const cb = Object.assign(document.createElement("input"), { type: "checkbox", checked: c.enabled });
    cb.onchange = async () => {
      try { S.connectors = (await api("PATCH", `/api/connectors/${c.name}`, { enabled: cb.checked })).connectors; render(); }
      catch (e) { fail(e); render(); }
    };
    sw.append(cb, document.createElement("span"));
    const actions = document.createElement("div");
    if (!c.builtin) {
      const rm = Object.assign(document.createElement("button"), { className: "btn ghost danger", textContent: "✕", title: t("conn.remove") });
      rm.onclick = async () => {
        if (!confirm(t("conn.remove_confirm", { name: c.name }))) return;
        try { S.connectors = (await api("DELETE", `/api/connectors/${c.name}`)).connectors; render(); } catch (e) { fail(e); }
      };
      actions.append(rm);
    }
    row.append(dot, meta, sw, actions);
    return row;
  }));

  seg($("#conn-kind"), connKind, (v) => { connKind = v; renderConnectors(); });
  $$("#conn-form [data-kind]").forEach((el) => el.classList.toggle("show", el.dataset.kind.split(" ").includes(connKind)));
}

async function submitConnector(e) {
  e.preventDefault();
  const f = e.target;
  const body = { kind: connKind };
  if (connKind === "json") body.json = f.json.value;
  else {
    body.name = f.name.value.trim();
    if (connKind === "url") Object.assign(body, { url: f.url.value.trim(), token: f.token.value.trim() });
    else {
      body.command = f.command.value.trim();
      body.env = Object.fromEntries(f.env.value.split("\n").map((l) => l.trim()).filter((l) => l.includes("="))
        .map((l) => [l.slice(0, l.indexOf("=")).trim(), l.slice(l.indexOf("=") + 1).trim()]));
    }
  }
  try {
    const res = await api("POST", "/api/connectors", body);
    S.connectors = res.connectors;
    f.reset();
    toast(t("conn.added", { names: res.added.join(", ") }));
    render();
    setTimeout(refresh, 4000);   // the first `npx -y` takes a moment
  } catch (err) {
    fail(err);
  }
}

function renderAdvanced() {
  bindCheck("#briefing", "briefing_on_boot");
  bindCheck("#start-login", "start_at_login");
  bindCheck("#overlay", "overlay_enabled");
  bindCheck("#subtitles", "subtitles");
  bindText("#silence-factor", "silence_factor", Number);
  $("#config-path").textContent = t("adv.config_path", { path: S.paths.config });
  $("#version").textContent = `Lura ${S.version} · ${S.os}`;
}

function render() {
  applyI18n();
  renderStatus();
  renderKeys();
  renderBrain();
  renderVoice();
  renderLanguage();
  renderConnectors();
  renderAdvanced();
  renderCards();   // cards.js
}

async function refresh() {
  try {
    S = await api("GET", "/api/state");
    render();
  } catch (e) {
    if (e.status === 401) document.body.textContent = "Open the dashboard from Lura (lura dashboard).";
    else if (S) { S.status = "offline"; renderStatus(); }
  }
}

async function poll() {
  try {
    const s = await api("GET", "/api/status");
    const changed = s.status !== S.status || s.capabilities.can_talk !== S.capabilities.can_talk ||
      s.restart_pending !== S.restart_pending ||
      JSON.stringify(s.connectors.map((c) => [c.status, c.tools.length])) !== JSON.stringify(S.connectors.map((c) => [c.status, c.tools.length]));
    Object.assign(S, s);
    if (changed) render();
  } catch (_) { if (!restarting) { S.status = "offline"; renderStatus(); } }
}

// ── theme
function applyTheme() {
  const theme = localStorage.getItem("lura-theme") || "auto";
  if (theme === "auto") delete document.documentElement.dataset.theme;
  else document.documentElement.dataset.theme = theme;
}
function cycleTheme() {
  const order = ["auto", "dark", "light"];
  const now = localStorage.getItem("lura-theme") || "auto";
  localStorage.setItem("lura-theme", order[(order.indexOf(now) + 1) % order.length]);
  applyTheme();
}

document.addEventListener("DOMContentLoaded", async () => {
  applyTheme();
  $("#theme-btn").onclick = cycleTheme;
  $("#talk-btn").onclick = () => api("POST", "/api/talk").then(() => toast(t("hero.listening"))).catch(fail);
  $("#stop-btn").onclick = () => api("POST", "/api/stop").catch(fail);
  $("#restart-btn").onclick = restartLura;
  $("#conn-form").onsubmit = submitConnector;
  await refresh();
  setInterval(poll, 2500);
  loadChrome();
  setInterval(loadChrome, 15000);
});
