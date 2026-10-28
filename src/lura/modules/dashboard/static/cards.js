// Lura dashboard, the newer cards: microphone & speaker, memory & history, skills.
// Uses the helpers in app.js ($, api, t, save, toast, fail, S); loaded after it.
"use strict";

const el = (tag, props = {}, ...kids) => {
  const node = Object.assign(document.createElement(tag), props);
  node.append(...kids);
  return node;
};

// ── microphone & speaker
let devices = null;   // {inputs, outputs} from /api/audio/devices

async function loadDevices() {
  if (devices) return;
  devices = { inputs: [], outputs: [] };
  try { devices = await api("GET", "/api/audio/devices"); } catch (e) { fail(e); }
  renderAudio();
}

function fillDevices(sel, list, current, name) {
  const box = $(sel);
  const opts = [el("option", { value: "", textContent: t("audio.default") })];
  for (const d of list) opts.push(el("option", { value: d.name, textContent: d.name + (d.is_default ? ` · ${t("audio.system")}` : "") }));
  // A saved device that is unplugged stays visible, so the choice is not lost silently.
  if (current && !list.some((d) => d.name === current)) opts.push(el("option", { value: current, textContent: `${current} · ${t("audio.missing")}` }));
  box.replaceChildren(...opts);
  box.value = current || "";
  box.onchange = () => save({ [name]: box.value || null });
}

function renderAudio() {
  const s = S.settings;
  if (!devices) loadDevices();
  fillDevices("#input-device", (devices || {}).inputs || [], s.input_device, "input_device");
  fillDevices("#output-device", (devices || {}).outputs || [], s.output_device, "output_device");
  const pause = $("#silence-ms");
  if (document.activeElement !== pause) pause.value = s.silence_ms;
  $("#pause-value").textContent = t("audio.seconds", { n: (pause.value / 1000).toFixed(1) });
  pause.oninput = () => ($("#pause-value").textContent = t("audio.seconds", { n: (pause.value / 1000).toFixed(1) }));
  pause.onchange = () => save({ silence_ms: Number(pause.value) });
  const follow = $("#follow-up");
  follow.value = String(s.follow_up_seconds);
  if (follow.value === "") follow.append(el("option", { value: String(s.follow_up_seconds), textContent: `${s.follow_up_seconds} s`, selected: true }));
  follow.onchange = () => save({ follow_up_seconds: Number(follow.value) });

  $("#mic-test").onclick = async () => {
    const btn = $("#mic-test"), out = $("#mic-result"), bar = $("#mic-meter i");
    btn.disabled = true;
    out.textContent = t("audio.testing");
    bar.style.width = "0";
    try {
      const r = await api("POST", "/api/audio/test-mic", { device: $("#input-device").value || null });
      if (r.error) { out.textContent = t("audio.mic_error", { error: r.error }); return; }
      // Replay the levels on the meter so a quiet mic is visible, not just a number.
      for (const v of r.levels) { bar.style.width = `${Math.min(100, v * 400)}%`; await new Promise((ok) => setTimeout(ok, 40)); }
      bar.style.width = `${Math.min(100, r.peak * 400)}%`;
      out.textContent = t(r.ok ? "audio.mic_ok" : "audio.mic_quiet", { peak: r.peak, floor: r.floor, rate: r.rate });
    } catch (e) { fail(e); out.textContent = ""; } finally { btn.disabled = false; }
  };
  $("#speaker-test").onclick = () => api("POST", "/api/audio/test-speaker", { device: $("#output-device").value || null }).catch(fail);
}

// ── memory & history
let memory = null;        // {facts, summary, path}
let historyQuery = null;  // the search the list was loaded for

async function loadMemory() {
  try { memory = await api("GET", "/api/memory"); } catch (e) { memory = { facts: "", summary: "", path: "" }; fail(e); }
  renderMemory();
}

async function loadHistory() {
  const q = $("#history-search").value.trim();
  historyQuery = q;
  let list = [];
  try { list = (await api("GET", `/api/history?q=${encodeURIComponent(q)}`)).conversations; } catch (e) { fail(e); }
  if (q !== $("#history-search").value.trim()) return;   // a newer search is on its way
  const when = (ts) => new Date(ts * 1000).toLocaleString(uiLang(), { dateStyle: "medium", timeStyle: "short" });
  $("#history").replaceChildren(...(list.length ? list.map((c) => {
    const row = el("div", { className: "conv" });
    const open = el("button", { className: "conv-open" }, el("small", { textContent: when(c.started) }),
                    el("span", { textContent: c.title || "…", dir: "auto" }), el("small", { textContent: t("mem.messages", { n: c.messages }) }));
    open.onclick = () => showTranscript(c.id);
    const rm = el("button", { className: "btn ghost danger", textContent: "✕", title: t("mem.delete") });
    rm.onclick = async () => {
      if (!confirm(t("mem.delete_confirm"))) return;
      try { await api("DELETE", `/api/history/${c.id}`); $("#transcript").hidden = true; loadHistory(); } catch (e) { fail(e); }
    };
    row.append(open, rm);
    return row;
  }) : [el("div", { className: "model-empty", textContent: t(q ? "mem.no_match" : "mem.empty") })]));
}

async function showTranscript(id) {
  const box = $("#transcript");
  try {
    const { messages } = await api("GET", `/api/history/${id}`);
    box.replaceChildren(...messages.map((m) => el("p", { className: m.role, dir: "auto" }, el("b", { textContent: m.role === "user" ? (S.settings.user_name || t("mem.you")) : S.settings.assistant_name }), " ", m.text)));
    box.hidden = false;
  } catch (e) { fail(e); }
}

function renderMemory() {
  const s = S.settings;
  bindCheck("#memory-enabled", "memory_enabled");
  const budget = $("#context-budget");
  budget.value = String(s.context_budget_tokens);
  budget.onchange = () => save({ context_budget_tokens: Number(budget.value) });
  if (memory === null) { memory = { facts: "", summary: "", path: "" }; loadMemory(); }
  $("#mem-hint").textContent = t("mem.hint", { path: memory.path || "…" });
  const facts = $("#memory-facts");
  if (document.activeElement !== facts && !facts.dataset.dirty) facts.value = memory.facts;
  facts.oninput = () => (facts.dataset.dirty = "1");
  $("#facts-save").onclick = async () => {
    try { memory = await api("PUT", "/api/memory", { facts: facts.value, base: memory.facts }); delete facts.dataset.dirty; toast(t("mem.saved")); renderMemory(); }
    catch (e) { fail(e); if (e.status === 409) { delete facts.dataset.dirty; loadMemory(); } }
  };
  $("#mem-summary").textContent = memory.summary || t("mem.no_summary");
  $("#mem-reset").onclick = async () => {
    if (!confirm(t("mem.reset_confirm"))) return;
    try { memory = await api("POST", "/api/memory/reset"); toast(t("mem.reset_done")); renderMemory(); } catch (e) { fail(e); }
  };
  $("#history-clear").onclick = async () => {
    if (!confirm(t("mem.delete_all_confirm"))) return;
    try { await api("DELETE", "/api/history"); $("#transcript").hidden = true; await loadMemory(); loadHistory(); } catch (e) { fail(e); }
  };
  let timer = null;
  $("#history-search").oninput = () => { clearTimeout(timer); timer = setTimeout(loadHistory, 250); };
  if (historyQuery === null) loadHistory();
}

// ── skills
let skills = null;
let skillKind = "paste";

async function loadSkills() {
  try { skills = (await api("GET", "/api/skills")).skills; } catch (e) { skills = []; fail(e); }
  renderSkills();
}

function renderSkills() {
  if (skills === null) { skills = []; loadSkills(); }
  $("#skills").replaceChildren(...(skills.length ? skills.map((k) => {
    const meta = el("div", { className: "meta" }, el("b", { textContent: k.name }), el("small", { textContent: k.description }));
    if (k.files.length) meta.append(el("small", { textContent: t("skills.files", { n: k.files.length }) }));
    const cb = el("input", { type: "checkbox", checked: k.enabled });
    cb.onchange = async () => {
      try { skills = (await api("PATCH", `/api/skills/${k.name}`, { enabled: cb.checked })).skills; renderSkills(); } catch (e) { fail(e); }
    };
    const rm = el("button", { className: "btn ghost danger", textContent: "✕", title: t("skills.remove") });
    rm.onclick = async () => {
      if (!confirm(t("skills.remove_confirm", { name: k.name }))) return;
      try { skills = (await api("DELETE", `/api/skills/${k.name}`)).skills; renderSkills(); } catch (e) { fail(e); }
    };
    return el("div", { className: "conn" }, el("span", { className: "dot " + (k.enabled ? "ok" : "") }), meta,
              el("label", { className: "switch" }, cb, el("span")), el("div", {}, rm));
  }) : [el("p", { className: "hint", textContent: t("skills.none") })]));
  seg($("#skill-kind"), skillKind, (v) => { skillKind = v; renderSkills(); });
  $$("#skill-form [data-kind]").forEach((d) => d.classList.toggle("show", d.dataset.kind === skillKind));
  $("#skill-form").onsubmit = submitSkill;
}

async function submitSkill(e) {
  e.preventDefault();
  const f = e.target, body = { kind: skillKind };
  if (skillKind === "paste") body.text = f.text.value;
  else if (skillKind === "url") body.url = f.url.value.trim();
  else {
    const file = f.zip.files[0];
    if (!file) return toast(t("skills.pick_zip"), true);
    const bytes = new Uint8Array(await file.arrayBuffer());
    let bin = "";
    for (let i = 0; i < bytes.length; i += 0x8000) bin += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
    body.data = btoa(bin);
  }
  try {
    const res = await api("POST", "/api/skills", body);
    skills = res.skills;
    f.reset();
    toast(t("skills.added", { name: res.added }));
    renderSkills();
  } catch (err) { fail(err); }
}

let lastStatus = "";

function renderCards() {
  // A conversation just ended: it may have added facts and a history row.
  if (lastStatus === "talking" && S.status !== "talking") { loadMemory(); loadHistory(); }
  lastStatus = S.status;
  renderAudio();
  renderMemory();
  renderSkills();
}
