// Holographic Lura head: the MediaPipe face mesh (face_mesh.js) lit from the
// upper left, drawn as glowing wireframe + shaded facets, with a dotted skull
// shell behind it. Eyes blink, the jaw drops with the narrator's voice.
// Pure function of its inputs, so frames can be rendered in any order.
(() => {
  const V0 = [], F = [], N = window.FACE_V.length / 3;
  for (let i = 0; i < N; i++) V0.push([FACE_V[3 * i], FACE_V[3 * i + 1], FACE_V[3 * i + 2]]);
  for (let i = 0; i < FACE_F.length; i += 3) F.push([FACE_F[i], FACE_F[i + 1], FACE_F[i + 2]]);

  const g = (x, s) => Math.exp(-(x * x) / (2 * s * s));
  const smooth = (a, b, x) => { const t = Math.min(1, Math.max(0, (x - a) / (b - a))); return t * t * (3 - 2 * t); };
  const avg = ids => [0, 1, 2].map(k => ids.reduce((s, i) => s + V0[i][k], 0) / ids.length);

  // Landmarks (MediaPipe indices): inner lips 13/14, eye contours.
  const LIP_UP = V0[13][1], LIP_LO = V0[14][1];
  const EYES = [avg([33, 133, 159, 145]), avg([263, 362, 386, 374])].map(e => [e[0], e[1], e[2] + 0.02]);
  // How far each vertex follows the jaw when the mouth opens.
  const JAW = V0.map(([x, y]) => smooth(LIP_UP - 0.01, LIP_LO + 0.005, y) * g(x, 0.42));

  // Unique edges, each with the triangles that share it.
  const EDGES = (() => {
    const m = new Map();
    F.forEach((f, ti) => [[f[0], f[1]], [f[1], f[2]], [f[2], f[0]]].forEach(([a, b]) => {
      const k = a < b ? `${a},${b}` : `${b},${a}`;
      if (!m.has(k)) m.set(k, { a, b, t: [] });
      m.get(k).t.push(ti);
    }));
    return [...m.values()];
  })();

  // Skull shell behind the mask: points on an ellipsoid, only where the mask isn't.
  const SHELL = [];
  for (let i = 0; i < 46; i++) for (let j = 0; j < 90; j++) {
    const th = (i + 0.5) / 46 * Math.PI, ph = j / 90 * Math.PI * 2;
    const p = [Math.sin(th) * Math.sin(ph) * 0.86, Math.cos(th) * 1.02 + 0.14, Math.sin(th) * Math.cos(ph) * 1.0 - 0.32];
    if (p[2] > 0.05 && p[1] < 0.95) continue;               // the face covers this
    if (p[1] < -0.55) continue;                                // no neck
    SHELL.push(p);
  }

  const LIGHT = (() => { const v = [-0.5, 0.55, 0.68], n = Math.hypot(...v); return v.map(a => a / n); })();

  function rot([x, y, z], yaw, pitch) {
    const cy = Math.cos(yaw), sy = Math.sin(yaw), cp = Math.cos(pitch), sp = Math.sin(pitch);
    [x, z] = [x * cy + z * sy, -x * sy + z * cy];
    [y, z] = [y * cp - z * sp, y * sp + z * cp];
    return [x, y, z];
  }

  // opts: {t, mouth 0..1, alpha, hue 'green'|'cyan', glow 0..1, yaw}
  window.drawFace = function (ctx, cx, cy, size, o) {
    const t = o.t, a = o.alpha ?? 1; if (a <= 0.003) return;
    const yaw = Math.sin(t * 0.45) * 0.28 + (o.yaw || 0), pitch = Math.sin(t * 0.31) * 0.07 - 0.06;
    const mouth = o.mouth || 0, glow = o.glow || 0;
    const col = o.hue === 'cyan' ? [34, 211, 238] : [52, 211, 153], hot = [210, 255, 245];
    const rgba = (al, c = col) => `rgba(${c[0]},${c[1]},${c[2]},${Math.min(1, al).toFixed(3)})`;
    const scanY = 1.25 - (((t * 0.55) % 2.0) / 2.0) * 2.7;   // band sweeping down in model space
    const P = v => { const k = 3.4 / (3.4 - v[2]); return [cx + v[0] * size * k, cy - v[1] * size * k, v[2]]; };

    const VV = V0.map((v, i) => rot([v[0], v[1] - JAW[i] * mouth * 0.11, v[2] - JAW[i] * mouth * 0.02], yaw, pitch));
    const S = VV.map(P);
    const band = i => g(V0[i][1] - scanY, 0.022);

    // per-triangle lighting
    const TL = F.map(([i, j, k]) => {
      const A = VV[i], B = VV[j], C = VV[k];
      const u = [B[0] - A[0], B[1] - A[1], B[2] - A[2]], v = [C[0] - A[0], C[1] - A[1], C[2] - A[2]];
      let n = [u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0]];
      const m = Math.hypot(...n) || 1; n = n.map(q => q / m);
      if (n[2] < 0) n = n.map(q => -q);                        // mesh winding varies; face the camera
      const lam = Math.max(0, n[0] * LIGHT[0] + n[1] * LIGHT[1] + n[2] * LIGHT[2]);
      return 0.05 + 0.95 * Math.pow(lam, 1.6) + 0.5 * Math.pow(1 - n[2], 4);
    });

    ctx.save();
    ctx.globalCompositeOperation = 'lighter';
    ctx.lineJoin = ctx.lineCap = 'round';

    const halo = ctx.createRadialGradient(cx, cy, size * 0.15, cx, cy, size * 1.7);
    halo.addColorStop(0, rgba((0.13 + 0.12 * glow) * a)); halo.addColorStop(1, rgba(0));
    ctx.fillStyle = halo; ctx.fillRect(cx - size * 2, cy - size * 2, size * 4, size * 4);

    // skull shell
    for (const p of SHELL) {
      const v = rot(p, yaw, pitch); if (v[2] < -0.2) continue;
      const q = P(v), al = (0.08 + 0.22 * smooth(-0.2, 0.6, v[2]) + 0.5 * g(p[1] - scanY, 0.04)) * a;
      ctx.fillStyle = rgba(al); ctx.fillRect(q[0] - 1, q[1] - 1, 2, 2);
    }

    // shaded facets
    F.forEach(([i, j, k], ti) => {
      ctx.fillStyle = rgba((0.03 + 0.20 * TL[ti] * TL[ti]) * a);
      ctx.beginPath(); ctx.moveTo(S[i][0], S[i][1]); ctx.lineTo(S[j][0], S[j][1]); ctx.lineTo(S[k][0], S[k][1]); ctx.fill();
    });

    // wireframe
    for (const e of EDGES) {
      const l = Math.max(...e.t.map(ti => TL[ti])), b = Math.max(band(e.a), band(e.b));
      ctx.strokeStyle = rgba((0.06 + 0.42 * l + 0.35 * b) * a, b > 0.8 ? hot : col);
      ctx.lineWidth = 0.9 + 0.4 * b;
      ctx.beginPath(); ctx.moveTo(S[e.a][0], S[e.a][1]); ctx.lineTo(S[e.b][0], S[e.b][1]); ctx.stroke();
    }

    // vertices
    for (let i = 0; i < S.length; i++) {
      const b = band(i), d = smooth(-0.3, 0.8, VV[i][2]);
      ctx.fillStyle = rgba((0.15 + 0.55 * d + 0.5 * b) * a, b > 0.7 || d > 0.85 ? hot : col);
      const r = 1.1 + 1.2 * d + b;
      ctx.fillRect(S[i][0] - r / 2, S[i][1] - r / 2, r, r);
    }

    // eyes
    const blink = 1 - Math.pow(Math.max(0, Math.sin(t * 0.9 + 1.3)), 80);
    for (const e of EYES) {
      const q = P(rot(e, yaw, pitch)), rx = size * 0.065, ry = rx * 0.55 * blink;
      const eg = ctx.createRadialGradient(q[0], q[1], 0, q[0], q[1], rx);
      eg.addColorStop(0, rgba((0.95 + 0.1 * glow) * a, hot)); eg.addColorStop(0.3, rgba(0.6 * a, [34, 211, 238])); eg.addColorStop(1, rgba(0));
      ctx.fillStyle = eg;
      ctx.beginPath(); ctx.ellipse(q[0], q[1], rx, Math.max(0.8, ry), -yaw * 0.1, 0, 7); ctx.fill();
    }

    // inner mouth glow while speaking
    if (mouth > 0.03) {
      const q = P(rot([0, (LIP_UP + LIP_LO) / 2 - mouth * 0.05, V0[13][2] - 0.05], yaw, pitch));
      const mg = ctx.createRadialGradient(q[0], q[1], 0, q[0], q[1], size * 0.2);
      mg.addColorStop(0, rgba(0.5 * mouth * a, hot)); mg.addColorStop(1, rgba(0));
      ctx.fillStyle = mg; ctx.beginPath(); ctx.ellipse(q[0], q[1], size * 0.2, size * (0.04 + 0.06 * mouth), 0, 0, 7); ctx.fill();
    }

    // orbit ring (the Lura motif): back half dimmer
    for (let i = 0; i < 160; i += 2) {
      const an = (i / 160) * Math.PI * 2 + t * 0.5;
      const v = rot([Math.cos(an) * 1.5, -0.05, Math.sin(an) * 1.5], yaw * 0.4, 0.22 + pitch), q = P(v);
      ctx.fillStyle = rgba((v[2] > 0 ? 0.6 : 0.18) * a, [34, 211, 238]);
      ctx.fillRect(q[0] - 1.2, q[1] - 1.2, 2.4, 2.4);
    }
    ctx.restore();
  };
})();
