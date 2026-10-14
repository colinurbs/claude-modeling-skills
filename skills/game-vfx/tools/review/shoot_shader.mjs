// shoot_shader.mjs -- headless review of a class `shader` effect (game-vfx round 5), driven by vfx_shader_review.py.
//
//   node shoot_shader.mjs --job <id>/_work/shader_job.json
//
// Renders the REFERENCE SNIPPET itself (references/shaders/*.glsl) in plain WebGL (SwiftShader) on one small canvas: no
// Babylon, no scene, one page. Each shot = {name, mode: screen|wet, w, h, time, uniforms, picture: path | "@shot"} -> <out>/<name>.png.
// One browser at a time on the machine (the caller holds every review slot).
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const GAM_TOOLS = path.join(path.resolve(HERE, '..', '..', '..'), 'game-asset-modeling', 'tools');
const { chromium } = await import(pathToFileURL(path.join(GAM_TOOLS, 'node_modules', 'playwright', 'index.mjs')).href);
const args = process.argv.slice(2);
const job = JSON.parse(fs.readFileSync(args[args.indexOf('--job') + 1], 'utf8'));
const out = path.resolve(job.out); fs.mkdirSync(out, { recursive: true });
const b64 = (f) => 'data:image/png;base64,' + fs.readFileSync(f).toString('base64');
const tex = {}; for (const [k, f] of Object.entries(job.textures)) tex[k] = b64(f);
const pics = {}; for (const s of job.shots) if (s.picture && !s.picture.startsWith('@') && !pics[s.picture]) pics[s.picture] = b64(s.picture);

const browser = await chromium.launch({ args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist', '--js-flags=--max-old-space-size=512'] });
const page = await browser.newPage();
const errors = [];
page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()); });
page.on('pageerror', (e) => errors.push(String(e)));
await page.setContent('<html><body style="margin:0;background:#000"><canvas id="c"></canvas></body></html>');
const results = await page.evaluate(async ({ job, tex, pics }) => {
  const cv = document.getElementById('c');
  const gl = cv.getContext('webgl', { preserveDrawingBuffer: true, premultipliedAlpha: false });
  const load = (src) => new Promise((res, rej) => { const i = new Image(); i.onload = () => res(i); i.onerror = rej; i.src = src; });
  const mk = (img, srgbDataIsLinear) => {
    const t = gl.createTexture(); gl.bindTexture(gl.TEXTURE_2D, t);
    gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, true); gl.pixelStorei(gl.UNPACK_COLORSPACE_CONVERSION_WEBGL, gl.NONE);
    gl.pixelStorei(gl.UNPACK_PREMULTIPLY_ALPHA_WEBGL, false);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, img);
    const pot = (x) => (x & (x - 1)) === 0;
    const rep = pot(img.width) && pot(img.height);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, rep ? gl.REPEAT : gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, rep ? gl.REPEAT : gl.CLAMP_TO_EDGE);
    if (rep) gl.generateMipmap(gl.TEXTURE_2D);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, rep ? gl.LINEAR_MIPMAP_LINEAR : gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
    return t;
  };
  const T = {}; for (const [k, v] of Object.entries(tex)) T[k] = mk(await load(v));
  const P = {}; for (const [k, v] of Object.entries(pics)) P[k] = mk(await load(v));
  const vs = 'attribute vec2 p; varying vec2 vUV; void main(){ vUV = p * 0.5 + 0.5; gl_Position = vec4(p, 0.0, 1.0); }';
  const progs = {};
  const compile = (mode) => {
    if (progs[mode]) return progs[mode];
    const fs = 'precision highp float;\nvarying vec2 vUV;\n' + job.snippets[mode] + '\n' + job.mains[mode];
    const sh = (type, src) => { const s = gl.createShader(type); gl.shaderSource(s, src); gl.compileShader(s); if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) throw new Error(mode + ' compile: ' + gl.getShaderInfoLog(s)); return s; };
    const pr = gl.createProgram(); gl.attachShader(pr, sh(gl.VERTEX_SHADER, vs)); gl.attachShader(pr, sh(gl.FRAGMENT_SHADER, fs)); gl.linkProgram(pr);
    if (!gl.getProgramParameter(pr, gl.LINK_STATUS)) throw new Error(mode + ' link: ' + gl.getProgramInfoLog(pr));
    progs[mode] = pr; return pr;
  };
  const buf = gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER, buf);
  gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 1, -1, -1, 1, 1, 1]), gl.STATIC_DRAW);
  const shotTex = {}; const res = [];
  for (const s of job.shots) {
    cv.width = s.w; cv.height = s.h; gl.viewport(0, 0, s.w, s.h);
    const pr = compile(s.mode); gl.useProgram(pr);
    const loc = gl.getAttribLocation(pr, 'p'); gl.enableVertexAttribArray(loc); gl.vertexAttribPointer(loc, 2, gl.FLOAT, false, 0, 0);
    let unit = 0;
    const bindS = (name, t) => { const l = gl.getUniformLocation(pr, name); if (!l) return; gl.activeTexture(gl.TEXTURE0 + unit); gl.bindTexture(gl.TEXTURE_2D, t); gl.uniform1i(l, unit); unit++; };
    for (const [slot, k] of Object.entries(job.samplers)) bindS(slot, T[k]);
    if (s.picture) bindS('u_picture', s.picture.startsWith('@') ? shotTex[s.picture.slice(1)] : P[s.picture]);
    const U = Object.assign({}, job.uniforms, s.uniforms || {}, { u_time: s.time || 0, u_aspect: s.w / s.h });
    for (const [k, v] of Object.entries(U)) {
      const l = gl.getUniformLocation(pr, k); if (!l) continue;
      if (Array.isArray(v)) (v.length === 3 ? gl.uniform3fv : gl.uniform2fv).call(gl, l, v); else gl.uniform1f(l, v);
    }
    gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
    const url = cv.toDataURL('image/png'); res.push({ name: s.name, url });
    if (job.reuse.includes(s.name)) shotTex[s.name] = mk(await load(url));
  }
  return res;
}, { job, tex, pics }).catch((e) => { errors.push(String(e)); return []; });
for (const r of results) fs.writeFileSync(path.join(out, r.name + '.png'), Buffer.from(r.url.split(',')[1], 'base64'));
fs.writeFileSync(path.join(out, 'shots.json'), JSON.stringify({ shots: results.map((r) => r.name), errors }, null, 1));
await browser.close();
console.log('SHADER REVIEW', results.length, 'shots', errors.length ? 'ERRORS ' + errors.join(' | ') : 'ok');
process.exit(errors.length ? 1 : 0);
