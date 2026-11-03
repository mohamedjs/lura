// Lura landing page: language, theme, OS detection, live download links, the face.
"use strict";

const REPO = "mohamedjs/lura";
const T = {
  en: {
    "nav.download": "Download", "nav.features": "Features", "nav.open": "Open source", "nav.faq": "FAQ",
    "hero.title": "Talk to your computer.",
    "hero.lead": "Say “Lura” and just talk, in Arabic or English. She answers out loud, opens your apps and pages, drives Chrome, finds your files and gets things done.",
    "hero.download": "Download for {os}", "hero.other": "Other platforms", "hero.say": "say “Lura”",
    "hero.version": "Version {v} · free", "hero.mobile": "Lura runs on your computer — open this page there.",
    "dl.title": "Download Lura", "dl.sub": "Free for Windows, macOS and Linux. Install, add one key in the dashboard, and say “Lura”.",
    "dl.yours": "your computer", "dl.copy": "Copy", "dl.copied": "Copied",
    "dl.win.req": "Windows 10 or 11, 64-bit", "dl.win.setup": "Download installer", "dl.win.portable": "Portable zip (no install)",
    "dl.win.note": "Windows may show “Windows protected your PC” for new apps: choose More info → Run anyway.",
    "dl.mac.req": "macOS 12 Monterey or later", "dl.mac.arm": "Apple Silicon (M1–M4)", "dl.mac.intel": "Intel Mac",
    "dl.linux.req": "Ubuntu, Debian and friends, 64-bit", "dl.linux.deb": "Download .deb", "dl.linux.tar": "Other distros (.tar.gz)",
    "dl.node": "Chrome, web and files connectors need Node.js", "dl.all": "All releases",
    "dl.mac.note": "Lura isn’t notarised by Apple yet, so the first open needs one extra step.",
    "dl.mac.how": "How to open it",
    "mac.title": "Opening Lura on a Mac",
    "mac.sub": "Lura is free and open source, and isn’t signed with a paid Apple developer certificate yet. So macOS says it “could not verify” the app. Here’s how to open it.",
    "mac.shot": "If you see this, click Done — not Move to Trash — and follow the steps.",
    "mac.brew.badge": "Easiest",
    "mac.brew.t": "With Homebrew",
    "mac.brew.d": "Run these two commands in Terminal. Homebrew installs Lura in Applications and clears the warning for you, so it simply opens.",
    "mac.brew.update": "Update later with: brew upgrade --cask --greedy lura",
    "mac.zip.t": "From the download",
    "mac.zip.1": "Unzip the file and drag Lura into Applications.",
    "mac.zip.2": "Open Lura. When macOS says “Lura” Not Opened, click Done.",
    "mac.zip.3": "Open System Settings → Privacy & Security, scroll down, and click Open Anyway next to “Lura”.",
    "mac.zip.4": "Confirm with your password or Touch ID. From then on Lura opens normally.",
    "mac.zip.or": "Or do it in one Terminal command:",
    "mac.perm.t": "Then allow the microphone",
    "mac.perm.d": "The first time she listens, macOS asks to let Lura use the microphone — click Allow. Lura lives in the menu bar (top right), not in the Dock.",
    "how.title": "How it works",
    "how.1.t": "Say “Lura”", "how.1.d": "She listens for her name on your computer, offline. Nothing leaves it until you call her.",
    "how.2.t": "Just talk", "how.2.d": "Ask anything, in Arabic or English. The AI you choose understands you and decides what to do.",
    "how.3.t": "She answers and acts", "how.3.d": "A natural voice answers while she opens apps, plays videos, searches the web or checks your system.",
    "feat.title": "Everything in one voice",
    "feat.brain.t": "Any brain", "feat.brain.d": "Google Gemini, OpenAI, Claude or OpenRouter — pick the model you trust in the dashboard.",
    "feat.voice.t": "One natural voice", "feat.voice.d": "Gemini 3.8 voice with 30 voices, female or male. She starts speaking while the answer is still being written.",
    "feat.lang.t": "Arabic & English", "feat.lang.d": "Speaks Egyptian Arabic and English natively, and the whole app switches language, right to left.",
    "feat.chrome.t": "Drives your browser", "feat.chrome.d": "Opens tabs, pages and videos in Chrome, reads live news and pages, finds files on your computer.",
    "feat.mcp.t": "Connect anything", "feat.mcp.d": "Add any MCP server by URL and token, or paste its mcp.json — GitHub, Notion, databases and more.",
    "feat.safe.t": "Safe by design", "feat.safe.d": "Deleting or overwriting files is blocked in code, not just asked nicely. Your keys never leave your computer.",
    "shots.title": "See it running", "shots.sub": "Real screenshots of the released app — the face floats over your apps on every screen.",
    "shots.win": "Windows: the face over your apps, with the dashboard", "shots.dash": "The dashboard: keys, brain, voice and language",
    "shots.mac": "macOS: the face and the menu-bar icon",
    "faq.title": "Questions",
    "faq.1.q": "Which keys do I need?", "faq.1.a": "One Google Gemini or OpenRouter key gives Lura her voice and a brain. Add an OpenAI or Anthropic key too if you want GPT or Claude to do the thinking.",
    "faq.2.q": "Is my voice recorded or sent somewhere?", "faq.2.a": "The wake word runs offline on your computer. Only after you say “Lura” is what you say sent — to the AI provider you chose, with your own key.",
    "faq.3.q": "Does she work in Arabic?", "faq.3.a": "Yes. Choose Arabic and she answers in Egyptian Arabic; the dashboard switches to Arabic, right to left.",
    "faq.4.q": "Can she delete my files?", "faq.4.a": "No. Destructive commands (rm, del, format, sudo…) are blocked in code on every operating system.",
    "open.title": "Free and open source", "open.lead": "Lura is MIT-licensed. Every feature is free, nothing is locked, and you can read every line that runs on your computer.",
    "open.f1": "Your keys and conversations stay on your computer", "open.f2": "Any AI provider: Gemini, OpenAI, Claude, OpenRouter", "open.f3": "Add any MCP connector and your own skills",
    "open.cta": "View on GitHub", "open.contrib": "How to contribute", "open.keys": "You bring your own AI keys and pay the provider directly, usually a fraction of a cent per conversation.",
    "faq.5.q": "Is it really free?", "faq.5.a": "Yes. Lura is open source under the MIT licence, with nothing locked. You only pay your AI provider for what you use, with your own key.",
    "foot.made": "Made with care for people who'd rather talk",
  },
  ar: {
    "nav.download": "التحميل", "nav.features": "المميزات", "nav.open": "مفتوحة المصدر", "nav.faq": "أسئلة",
    "hero.title": "كلّم جهازك.",
    "hero.lead": "قول «لورا» واتكلم عادي، عربي أو إنجليزي. بترد عليك بصوتها، وتفتحلك برامجك وصفحاتك، وتتحكم في كروم، وتلاقيلك ملفاتك، وتخلّص الحاجة.",
    "hero.download": "حمّل لـ {os}", "hero.other": "أنظمة تانية", "hero.say": "قول «لورا»",
    "hero.version": "إصدار {v} · مجاني", "hero.mobile": "لورا بتشتغل على الكمبيوتر — افتح الصفحة دي من عليه.",
    "dl.title": "حمّل لورا", "dl.sub": "مجانية على ويندوز وماك ولينكس. سطّبها، حط مفتاح واحد في لوحة التحكم، وقول «لورا».",
    "dl.yours": "جهازك", "dl.copy": "انسخ", "dl.copied": "اتنسخ",
    "dl.win.req": "ويندوز 10 أو 11، 64-bit", "dl.win.setup": "حمّل ملف التسطيب", "dl.win.portable": "نسخة محمولة (من غير تسطيب)",
    "dl.win.note": "ممكن ويندوز يقولك «Windows protected your PC» للبرامج الجديدة: اختار More info وبعدين Run anyway.",
    "dl.mac.req": "macOS 12 أو أحدث", "dl.mac.arm": "Apple Silicon (M1–M4)", "dl.mac.intel": "ماك Intel",
    "dl.linux.req": "أوبونتو وديبيان وأخواتهم، 64-bit", "dl.linux.deb": "حمّل ‎.deb", "dl.linux.tar": "توزيعات تانية (.tar.gz)",
    "dl.node": "كونكتورز كروم والويب والملفات محتاجة Node.js", "dl.all": "كل الإصدارات",
    "dl.mac.note": "لورا لسه مش موقّعة من Apple، فأول مرة تفتحها محتاجة خطوة زيادة.",
    "dl.mac.how": "إزاي تفتحها",
    "mac.title": "فتح لورا على الماك",
    "mac.sub": "لورا مجانية ومفتوحة المصدر، ولسه مش موقّعة بشهادة مطوّر Apple المدفوعة، فالماك بيقول إنه «مش قادر يتأكد» من التطبيق. ده إزاي تفتحها.",
    "mac.shot": "لو ظهرتلك الرسالة دي، دوس Done — مش Move to Trash — وامشي على الخطوات.",
    "mac.brew.badge": "الأسهل",
    "mac.brew.t": "بـ Homebrew",
    "mac.brew.d": "شغّل الأمرين دول في الـ Terminal. Homebrew بيسطّب لورا في Applications وبيشيل التحذير لوحده، فتفتح على طول.",
    "mac.brew.update": "وتحدّثها بعدين بـ: brew upgrade --cask --greedy lura",
    "mac.zip.t": "من ملف التحميل",
    "mac.zip.1": "فك ضغط الملف واسحب لورا جوه Applications.",
    "mac.zip.2": "افتح لورا. لما الماك يقول «Lura» Not Opened، دوس Done.",
    "mac.zip.3": "افتح System Settings ← Privacy & Security، انزل لتحت، ودوس Open Anyway جنب «Lura».",
    "mac.zip.4": "أكّد بالباسورد أو Touch ID. من بعدها لورا هتفتح عادي.",
    "mac.zip.or": "أو اعملها بأمر واحد في الـ Terminal:",
    "mac.perm.t": "وبعدين اسمح بالمايك",
    "mac.perm.d": "أول ما تسمعك، الماك هيسألك تسمح للورا تستخدم المايك — دوس Allow. لورا بتعيش في شريط القوائم (فوق على اليمين)، مش في الـ Dock.",
    "how.title": "بتشتغل إزاي",
    "how.1.t": "قول «لورا»", "how.1.d": "بتسمع اسمها على جهازك من غير نت. ولا كلمة بتطلع برّه غير لما تناديها.",
    "how.2.t": "اتكلم عادي", "how.2.d": "اسأل أي حاجة بالعربي أو الإنجليزي. الذكاء الاصطناعي اللي تختاره بيفهمك ويقرر يعمل إيه.",
    "how.3.t": "بترد وبتنفّذ", "how.3.d": "صوت طبيعي بيرد عليك وهي بتفتح البرامج، وتشغّل الفيديوهات، وتدوّر على النت، أو تطمّنك على الجهاز.",
    "feat.title": "كل حاجة بصوت واحد",
    "feat.brain.t": "أي عقل", "feat.brain.d": "Google Gemini أو OpenAI أو Claude أو OpenRouter — اختار الموديل اللي تثق فيه من لوحة التحكم.",
    "feat.voice.t": "صوت طبيعي واحد", "feat.voice.d": "صوت Gemini 3.8 بـ 30 صوت، ست أو راجل. بتبدأ تتكلم والرد لسه بيتكتب.",
    "feat.lang.t": "عربي وإنجليزي", "feat.lang.d": "بتتكلم مصري وإنجليزي، والتطبيق كله بيقلب عربي من اليمين للشمال.",
    "feat.chrome.t": "بتسوق المتصفح", "feat.chrome.d": "بتفتح تابات وصفحات وفيديوهات في كروم، وتقرا الأخبار والصفحات لايف، وتلاقي الملفات على جهازك.",
    "feat.mcp.t": "وصّلها بأي حاجة", "feat.mcp.d": "ضيف أي سيرفر MCP بلينك وتوكن، أو الصق mcp.json — GitHub وNotion وقواعد بيانات وغيرهم.",
    "feat.safe.t": "آمنة من الأساس", "feat.safe.d": "مسح الملفات أو الكتابة فوقها ممنوع في الكود نفسه، مش بالرجاء. ومفاتيحك مابتسيبش جهازك.",
    "shots.title": "شوفها شغالة", "shots.sub": "صور حقيقية من النسخة المنشورة — الوش بيطفو فوق برامجك على كل الشاشات.",
    "shots.win": "ويندوز: الوش فوق برامجك ومعاه لوحة التحكم", "shots.dash": "لوحة التحكم: المفاتيح والعقل والصوت واللغة",
    "shots.mac": "ماك: الوش وأيقونة شريط القوائم",
    "faq.title": "أسئلة",
    "faq.1.q": "محتاج مفاتيح إيه؟", "faq.1.a": "مفتاح واحد من Google Gemini أو OpenRouter بيدّي لورا صوتها وعقلها. ضيف مفتاح OpenAI أو Anthropic لو عايز GPT أو Claude هو اللي يفكّر.",
    "faq.2.q": "صوتي بيتسجّل أو بيتبعت لحد؟", "faq.2.a": "كلمة التشغيل بتشتغل على جهازك من غير نت. بعد ما تقول «لورا» بس، كلامك بيروح للمزوّد اللي اخترته بمفتاحك إنت.",
    "faq.3.q": "بتتكلم عربي؟", "faq.3.a": "أيوه. اختار العربي وهترد عليك بالمصري، ولوحة التحكم بتقلب عربي من اليمين للشمال.",
    "faq.4.q": "ممكن تمسح ملفاتي؟", "faq.4.a": "لأ. الأوامر الخطيرة (rm وdel وformat وsudo…) ممنوعة في الكود على كل الأنظمة.",
    "open.title": "مجانية ومفتوحة المصدر", "open.lead": "لورا برخصة MIT. كل المميزات مجانية ومفيش حاجة مقفولة، وتقدر تقرا كل سطر بيشتغل على جهازك.",
    "open.f1": "مفاتيحك وكلامك بيفضلوا على جهازك", "open.f2": "أي مزوّد ذكاء اصطناعي: Gemini وOpenAI وClaude وOpenRouter", "open.f3": "ضيف أي كونكتور MCP ومهاراتك الخاصة",
    "open.cta": "شوفها على GitHub", "open.contrib": "إزاي تساهم", "open.keys": "بتستخدم مفاتيحك وبتدفع للمزوّد على طول، غالباً جزء من السنت في المحادثة.",
    "faq.5.q": "هي مجانية بجد؟", "faq.5.a": "أيوه. لورا مفتوحة المصدر برخصة MIT ومفيش حاجة مقفولة. بتدفع بس لمزوّد الذكاء الاصطناعي على قد استخدامك، بمفتاحك.",
    "foot.made": "معمولة بحب للناس اللي بتحب تتكلم",
  },
};
const OS_NAME = { windows: "Windows", macos: "macOS", linux: "Linux" };

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const store = { get: (k) => { try { return localStorage.getItem(k); } catch { return null; } },
                set: (k, v) => { try { localStorage.setItem(k, v); } catch {} } };

let lang = store.get("lura-lang") || ((navigator.language || "").startsWith("ar") ? "ar" : "en");
let release = null;
const OS = detectOS();

function t(key, vars = {}) {
  let s = (T[lang] && T[lang][key]) || T.en[key] || key;
  for (const [k, v] of Object.entries(vars)) s = s.replaceAll(`{${k}}`, v);
  return s;
}

function detectOS() {
  const ua = navigator.userAgent || "";
  const p = (navigator.userAgentData && navigator.userAgentData.platform) || navigator.platform || "";
  if (/android|iphone|ipad|ipod/i.test(ua)) return "mobile";
  if (/win/i.test(p) || /windows/i.test(ua)) return "windows";
  if (/mac/i.test(p) || /mac os x/i.test(ua)) return "macos";
  if (/linux|x11|cros/i.test(p + ua)) return "linux";
  return "windows";
}

// ── release assets: release.json written at build time, the GitHub API as a fallback ──
const PICK = {
  setup: /^Lura-Setup-.*\.exe$/i, portable: /windows-x64-portable\.zip$/i,
  "mac-arm": /macos-arm64\.zip$/i, "mac-intel": /macos-x86_64\.zip$/i,
  deb: /_amd64\.deb$/i, tar: /linux-x86_64\.tar\.gz$/i, sums: /^SHA256SUMS/i,
};
const HERO_ASSET = { windows: "setup", macos: "mac-arm", linux: "deb" };

async function loadRelease() {
  for (const url of ["release.json", `https://api.github.com/repos/${REPO}/releases/latest`]) {
    try {
      const res = await fetch(url, { cache: "no-cache" });
      if (res.ok) { const data = await res.json(); if (data && data.assets) return data; }
    } catch {}
  }
  return null;
}

function asset(kind) {
  return release && release.assets.find((a) => PICK[kind].test(a.name));
}

function renderDownloads() {
  const fallback = `https://github.com/${REPO}/releases/latest`;
  $$("[data-asset]").forEach((el) => {
    const a = asset(el.dataset.asset);
    const label = t(el.dataset.i18n);
    el.href = a ? a.browser_download_url : fallback;
    el.innerHTML = "";
    el.append(label);
    if (a) {
      const s = document.createElement("small");
      s.textContent = ` · ${(a.size / 1e6).toFixed(0)} MB`;
      el.append(s);
    }
  });
  const sums = asset("sums");
  $("#checksums").href = sums ? sums.browser_download_url : fallback;
  const deb = asset("deb");
  if (deb) $("#apt-cmd").textContent = `sudo apt install ./${deb.name}`;
  const v = release ? release.tag_name.replace(/^v/, "") : "";
  $("#hero-version").textContent = OS === "mobile" ? t("hero.mobile") : v ? t("hero.version", { v }) : "";
  $("#foot-version").textContent = v ? `v${v}` : "";

  $$(".platform").forEach((card) => card.classList.toggle("mine", card.dataset.os === OS));
  const hero = $("#hero-download");
  if (OS in HERO_ASSET) {
    const a = asset(HERO_ASSET[OS]);
    hero.href = a ? a.browser_download_url : "#download";
    $("#hero-icon").dataset.os = OS;
    $("#hero-icon").hidden = false;
    $("#hero-download-text").textContent = t("hero.download", { os: OS_NAME[OS] });
  } else {
    hero.href = "#download";
    $("#hero-icon").hidden = true;
    $("#hero-download-text").textContent = t("dl.title");
  }
}

function applyLang() {
  document.documentElement.lang = lang;
  document.documentElement.dir = lang === "ar" ? "rtl" : "ltr";
  $$("[data-i18n]").forEach((el) => { if (!el.dataset.asset) el.textContent = t(el.dataset.i18n); });
  $$("#lang button").forEach((b) => b.classList.toggle("on", b.dataset.value === lang));
  document.title = `Lura — ${t("hero.title").replace(/\.$/, "")}`;
  // The intro video and its poster exist in both languages.
  const video = $("#intro"), src = `assets/intro-${lang}.mp4`;
  if (!video.currentSrc.endsWith(src)) {
    video.poster = `assets/intro-${lang}.jpg`;
    video.querySelector("source").src = src;
    video.load();
  }
  $("#shot-dash").src = `assets/shots/dashboard-${lang === "ar" ? "ar-dark" : "en-light"}.png`;
  renderDownloads();
}

function applyTheme() {
  const theme = store.get("lura-theme") || "auto";
  if (theme === "auto") delete document.documentElement.dataset.theme;
  else document.documentElement.dataset.theme = theme;
}

function dark() {
  const theme = document.documentElement.dataset.theme;
  return theme ? theme === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
}

// ── the face, live in the hero (face.js is the same head as the app's) ──
function startFace() {
  const canvas = $("#face");
  if (!canvas || typeof window.drawFace !== "function") return;
  const ctx = canvas.getContext("2d");
  const still = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const t0 = performance.now();
  function frame(now) {
    const t = still ? 2 : (now - t0) / 1000;
    const w = canvas.width, h = canvas.height;
    ctx.clearRect(0, 0, w, h);
    const ink = dark()
      ? { line: [150, 145, 255], hot: [120, 230, 245], eye: [60, 210, 235], blend: "lighter", boost: 1.1 }
      : { line: [96, 88, 220], hot: [12, 150, 172], eye: [12, 150, 172], blend: "source-over", boost: 1.8 };
    // A soft talking rhythm every few seconds, so it looks alive.
    const talk = Math.max(0, Math.sin(t * 0.7)) > 0.85 ? 0.3 + 0.4 * Math.abs(Math.sin(t * 12)) : 0;
    window.drawFace(ctx, w / 2, h * 0.46, w * 0.27, { t, mouth: talk, alpha: 1, glow: 0.3, ...ink });
    if (!still) requestAnimationFrame(frame);
  }
  requestAnimationFrame(frame);
}

document.addEventListener("DOMContentLoaded", async () => {
  applyTheme();
  applyLang();
  $$("#lang button").forEach((b) => (b.onclick = () => { lang = b.dataset.value; store.set("lura-lang", lang); applyLang(); }));
  $("#theme").onclick = () => {
    const order = ["auto", "dark", "light"];
    const now = store.get("lura-theme") || "auto";
    store.set("lura-theme", order[(order.indexOf(now) + 1) % order.length]);
    applyTheme();
  };
  $$(".copy").forEach((b) => (b.onclick = async () => {
    const code = b.parentElement.querySelector("code").textContent;
    try { await navigator.clipboard.writeText(code); b.textContent = t("dl.copied"); setTimeout(() => (b.textContent = t("dl.copy")), 1500); } catch {}
  }));
  release = await loadRelease();
  renderDownloads();
});
window.addEventListener("load", startFace);
