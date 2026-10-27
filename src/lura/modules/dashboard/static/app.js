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
    err.pro = data.pro_required;
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
    S.settings = res.settings;
    S.capabilities = res.capabilities;
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
}

function renderKeys() {
  const box = $("#keys");
  box.replaceChildren();
  for (const p of S.providers) {
    const k = S.keys[p];
    const row = document.createElement("div");
    row.className = "key-row";
    const who = document.createElement("div");
    who.className = "who";
    who.innerHTML = `<b></b><small></small><div class="tags"></div>`;
    $("b", who).textContent = k.label;
    $("small", who).textContent = k.set ? `${t("keys.saved")} ${k.masked}${k.source === "env" ? " · env" : ""}` : t("keys.none");
    const tags = $(".tags", who);
    const tagList = [["tag", t("keys.tag.brain")]];
    if (k.voice) tagList.push(["tag voice", t("keys.tag.voice")]);
    if (k.realtime) tagList.push(["tag voice", t("keys.tag.realtime")]);
    for (const [c, txt] of tagList) tags.append(Object.assign(document.createElement("span"), { className: c, textContent: txt }));

    const input = Object.assign(document.createElement("input"), {
      type: "password", placeholder: k.set ? t("keys.replace") : t("keys.paste"), autocomplete: "off", spellcheck: false,
    });
    const actions = document.createElement("div");
    actions.className = "row";
    const saveBtn = Object.assign(document.createElement("button"), { className: "btn primary", textContent: t("keys.save") });
    saveBtn.onclick = async () => {
      saveBtn.disabled = true;
      saveBtn.textContent = t("keys.testing");
      try {
        const res = await api("PUT", `/api/keys/${p}`, { key: input.value });
        Object.assign(S, { keys: res.keys, capabilities: res.capabilities, settings: res.settings });
        toast(`${k.label}: ${res.message}`);
        render();
      } catch (e) { fail(e); saveBtn.disabled = false; saveBtn.textContent = t("keys.save"); }
    };
    input.onkeydown = (e) => e.key === "Enter" && saveBtn.click();
    const link = Object.assign(document.createElement("a"), { className: "btn ghost", href: k.key_url, target: "_blank", rel: "noopener", textContent: t("keys.get") });
    actions.append(saveBtn, link);
    if (k.set && k.source === "file") {
      const rm = Object.assign(document.createElement("button"), { className: "btn ghost danger", textContent: t("keys.remove") });
      rm.onclick = async () => {
        try { Object.assign(S, await api("DELETE", `/api/keys/${p}`)); render(); } catch (e) { fail(e); }
      };
      actions.append(rm);
    }
    row.append(who, input, actions);
    box.append(row);
  }
}

function renderBrain() {
  const s = S.settings;
  seg($("#mode"), s.mode, (v) => save({ mode: v }));
  $("#mode-hint").textContent = t(s.mode === "realtime" ? "brain.mode.realtime_hint" : "brain.mode.pipeline_hint");
  $("#brain-pipeline").hidden = s.mode === "realtime";

  const sel = $("#brain-provider");
  sel.replaceChildren(...S.providers.map((p) => {
    const o = Object.assign(document.createElement("option"), { value: p, textContent: S.keys[p].label });
    if (!S.keys[p].set) { o.disabled = true; o.textContent += ` — ${t("brain.needs_key")}`; }
    return o;
  }));
  sel.value = s.brain_provider;
  sel.onchange = () => save({ brain_provider: sel.value });

  const model = $("#brain-model");
  if (document.activeElement !== model) model.value = s.models[s.brain_provider] || "";
  model.onchange = () => save({ models: { [s.brain_provider]: model.value.trim() } });
  fillModels(S.suggested_models[s.brain_provider] || []);
  model.onfocus = async () => {
    if (model.dataset.loaded === s.brain_provider) return;
    model.dataset.loaded = s.brain_provider;
    try { fillModels((await api("GET", `/api/models/${s.brain_provider}`)).models); } catch (_) {}
  };
  const effort = $("#effort");
  effort.value = s.reasoning_effort;
  effort.onchange = () => save({ reasoning_effort: effort.value });

  const p = s.brain_provider, m = s.models[p] || "";
  const hears = p === "gemini" || (p === "openrouter" && (m.startsWith("google/gemini") || m.startsWith("openai/gpt-audio")));
  $("#hearing-hint").textContent = t(hears ? "brain.hears" : "brain.transcribes");
}

function fillModels(list) {
  $("#model-list").replaceChildren(...list.map((id) => Object.assign(document.createElement("option"), { value: id })));
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
    chip.onkeydown = (e) => e.key === "Enter" && chip.click();
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

function renderPlan() {
  const plan = S.plan;
  const pro = plan.plan === "pro";
  $("#plan-free").classList.toggle("current", !pro);
  $("#plan-pro").classList.toggle("current", pro);
  $("#plan-free-actions").hidden = pro;
  $("#plan-pro-actions").hidden = !pro;
  $("#checkout").href = plan.checkout_url;
  $("#manage").href = plan.manage_url;
  $("#plan-pro-info").textContent = t("plan.pro_info", { email: plan.email || "—", status: plan.status || "active" });
  $("#activate").onclick = async () => {
    const btn = $("#activate");
    btn.disabled = true;
    try {
      S.plan = await api("POST", "/api/billing/activate", { key: $("#license-key").value });
      toast(t("plan.activated"));
      await refresh();
    } catch (e) { fail(e); } finally { btn.disabled = false; }
  };
  $("#deactivate").onclick = async () => {
    if (!confirm(t("plan.deactivate_confirm"))) return;
    try { S.plan = await api("POST", "/api/billing/deactivate"); await refresh(); } catch (e) { fail(e); }
  };
}

function renderConnectors() {
  const pro = S.plan.plan === "pro";
  $("#add-connector").classList.toggle("locked", !pro);
  const box = $("#connectors");
  box.replaceChildren(...S.connectors.map((c) => {
    const row = Object.assign(document.createElement("div"), { className: "conn" });
    const dot = Object.assign(document.createElement("span"), {
      className: "dot " + ({ ready: "ok", starting: "warn", error: "bad", locked: "bad" }[c.status] || ""),
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
    const cb = Object.assign(document.createElement("input"), { type: "checkbox", checked: c.enabled && c.status !== "locked" });
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
    if (err.pro) location.hash = "plan-card";
    fail(err);
  }
}

function renderAdvanced() {
  bindCheck("#briefing", "briefing_on_boot");
  bindCheck("#start-login", "start_at_login");
  bindCheck("#overlay", "overlay_enabled");
  bindText("#silence-ms", "silence_ms", Number);
  bindText("#silence-factor", "silence_factor", Number);
  bindText("#input-device", "input_device", (v) => v.trim() || null);
  bindText("#output-device", "output_device", (v) => v.trim() || null);
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
  renderPlan();
  renderConnectors();
  renderAdvanced();
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
      JSON.stringify(s.connectors.map((c) => [c.status, c.tools.length])) !== JSON.stringify(S.connectors.map((c) => [c.status, c.tools.length]));
    Object.assign(S, s);
    if (changed) render();
  } catch (_) { S.status = "offline"; renderStatus(); }
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
  $("#conn-form").onsubmit = submitConnector;
  await refresh();
  setInterval(poll, 2500);
});
