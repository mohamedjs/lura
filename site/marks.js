// Notebook marker strokes for landing pages (same shapes as the reels' marks.js).
//   <span data-mark="circle|circle-r|underline|box|cross|tick|arrow-r|arrow-l|arrow-d|scribble">…</span>
//   optional data-mark-delay="ms". The stroke draws on (~0.6 s) the first time the element scrolls into
//   view, and .hl highlighter spans sweep in the same way. Re-fitted to the element's size on resize.
//   Reduced motion: everything is drawn at once. Include with <script src="marks.js" defer></script>.
(function () {
  const NS = "http://www.w3.org/2000/svg";
  const SHAPES = {
    circle: ["M 52 4 C 82 3 99 22 97 50 C 95 80 72 97 46 96 C 18 94 2 74 4 48 C 6 22 26 6 58 8"],
    "circle-r": ["M 48 6 C 18 5 2 24 4 52 C 6 80 30 96 56 95 C 84 93 98 72 96 46 C 94 20 72 5 40 9"],
    underline: ["M 2 60 C 30 72 66 68 98 52"],
    scribble: ["M 2 40 C 25 70 45 20 60 55 S 85 30 98 50"],
    box: ["M 3 6 L 97 4 L 96 95 L 4 96 Z"],
    cross: ["M 8 10 C 35 38 62 64 92 90", "M 92 8 C 66 36 36 64 8 92"],
    tick: ["M 12 54 L 40 82 L 92 14"],
    "arrow-r": ["M 2 50 C 30 30 60 34 92 50", "M 92 50 L 74 34", "M 92 50 L 72 64"],
    "arrow-l": ["M 98 50 C 70 30 40 34 8 50", "M 8 50 L 26 34", "M 8 50 L 28 64"],
    "arrow-d": ["M 50 2 C 34 30 36 60 50 92", "M 50 92 L 34 72", "M 50 92 L 66 74"],
  };
  // margin around the element as a fraction of its size [x, y]; underline sits under the text
  const MARGIN = { circle: [.14, .3], "circle-r": [.14, .3], box: [.06, .14], cross: [.04, .06], tick: [.04, .06], underline: [.02, 0], scribble: [.02, 0] };
  const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const marks = [];

  function build(el) {
    const shape = el.dataset.mark, d = SHAPES[shape]; if (!d) return;
    if (getComputedStyle(el).position === "static") el.style.position = "relative";
    const svg = document.createElementNS(NS, "svg"); svg.setAttribute("class", "nb-mark"); svg.setAttribute("aria-hidden", "true");
    const paths = d.map(() => svg.appendChild(document.createElementNS(NS, "path")));
    el.appendChild(svg);
    const m = { el, shape, d, svg, paths, margin: MARGIN[shape] || [0, 0], drawn: false };
    fit(m); marks.push(m); return m;
  }
  function fit(m) {
    const ow = m.el.offsetWidth, oh = m.el.offsetHeight, [mx, my] = m.margin, low = m.shape === "underline" || m.shape === "scribble";
    const W = Math.max(8, ow * (1 + 2 * mx)), H = low ? Math.max(10, oh * .35) : oh * (1 + 2 * my);
    Object.assign(m.svg.style, { left: -mx * ow + "px", top: (low ? oh * .82 : -my * oh) + "px", width: W + "px", height: H + "px" });
    m.svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
    m.paths.forEach((p, i) => {
      let n = 0; p.setAttribute("d", m.d[i].replace(/-?\d+(\.\d+)?/g, (v) => ((n++ % 2 ? H : W) * +v / 100).toFixed(1)));
      const L = p.getTotalLength() + 2; p.style.strokeDasharray = `${L} ${L}`;
      if (!m.drawn) p.style.strokeDashoffset = L;
    });
  }
  function draw(m) {
    if (m.drawn) return; m.drawn = true;
    const delay = +(m.el.dataset.markDelay || 0);
    m.paths.forEach((p, i) => {
      p.style.transition = reduce ? "none" : `stroke-dashoffset ${i ? .22 : .65}s cubic-bezier(.3,.7,.2,1) ${delay / 1000 + (i ? .55 + i * .12 : 0)}s`;
      requestAnimationFrame(() => { p.style.strokeDashoffset = 0; });
    });
  }
  let io = null;
  const seen = new WeakSet();
  // pick up [data-mark] and .hl elements not seen yet; call window.nbScan() after you change the DOM (language switch)
  function scan(root = document) {
    root.querySelectorAll("[data-mark], .hl").forEach((el) => {
      if (seen.has(el)) return; seen.add(el);
      if (el.dataset.mark) build(el);
      if (io) io.observe(el); else { el.classList.add("hl-on"); const m = marks.find((x) => x.el === el); if (m) draw(m); }
    });
  }
  function init() {
    document.documentElement.classList.remove("no-js");
    if ("IntersectionObserver" in window) io = new IntersectionObserver((es) => es.forEach((e) => {
      if (!e.isIntersecting) return; io.unobserve(e.target);
      e.target.classList.add("hl-on");
      const m = marks.find((x) => x.el === e.target); if (m) draw(m);
    }), { threshold: .5 });
    scan();
    let t; addEventListener("resize", () => { clearTimeout(t); t = setTimeout(() => marks.forEach(fit), 120); });
    if (document.fonts) document.fonts.ready.then(() => marks.forEach(fit));
    window.nbScan = scan;
    window.nbRefit = () => marks.forEach(fit);
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init); else init();
})();
