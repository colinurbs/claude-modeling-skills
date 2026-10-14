// shoot_anim.mjs -- render an animation set on the real character bodies in Babylon (headless).
//
//   node shoot_anim.mjs <set.glb> --job job.json
//
// job.json (written by anim_cli.py review): { spec, out, clips:[names], bodies:[{name, parts:[glb,...]}],
//   diag: {clip: {contact:{l:[],r:[]}, slide_bad:{l:[],r:[]}}}, frames: 8, size:[w,h], video: bool, fps: 30,
//   bones:[...], parents:{bone: parent}, hips_rest_src:[...] }
// Writes <out>/<clip>/<body>_{strip,side,top,cctv}.png (+ video frames dir) and <out>/review.json.
// One browser, one page at a time; the page is closed between bodies. Never start more than one.
import fs from 'node:fs';
import http from 'node:http';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const GAM_TOOLS = path.resolve(HERE, '..', '..', '..', 'game-asset-modeling', 'tools');
const { chromium } = await import(pathToFileURL(path.join(GAM_TOOLS, 'node_modules', 'playwright', 'index.mjs')).href);
const args = process.argv.slice(2);
const opt = (k, d) => { const i = args.indexOf('--' + k); return i >= 0 ? args[i + 1] : d; };
const glb = path.resolve(args[0]);
const job = JSON.parse(fs.readFileSync(opt('job'), 'utf8'));
const spec = JSON.parse(fs.readFileSync(job.spec, 'utf8'));
const out = path.resolve(job.out); fs.mkdirSync(out, { recursive: true });
const [W, H] = job.size || [960, 540];
let parts = [];
const hdri = path.join(GAM_TOOLS, 'review', 'env', 'overcast_1k.hdr');
const types = { '.html': 'text/html', '.js': 'text/javascript', '.glb': 'model/gltf-binary', '.hdr': 'application/octet-stream', '.wasm': 'application/wasm' };
const server = http.createServer((req, res) => {
  const u = decodeURIComponent(req.url.split('?')[0]); let f = null;
  if (u === '/harness.html') f = path.join(HERE, 'harness_anim.html');
  else if (u === '/anim/set.glb') f = glb;
  else if (u.startsWith('/part/')) f = parts[parseInt(u.slice(6))];
  else if (u === '/env.hdr') f = hdri;
  else if (u.startsWith('/gam/')) f = path.join(GAM_TOOLS, u.slice(5));
  if (!f || !fs.existsSync(f)) { res.writeHead(404); res.end(); return; }
  res.writeHead(200, { 'Content-Type': types[path.extname(f)] || 'application/octet-stream' });
  fs.createReadStream(f).pipe(res);
});
await new Promise(r => server.listen(0, '127.0.0.1', r));
const port = server.address().port;
const save = (file, url) => { fs.mkdirSync(path.dirname(file), { recursive: true }); fs.writeFileSync(file, Buffer.from(url.split(',')[1], 'base64')); return file; };

const browser = await chromium.launch({ args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist', '--js-flags=--max-old-space-size=1536'] });
const report = { glb, bodies: [], errors: [], warnings: [] };
try {
  for (const body of job.bodies) {
    parts = body.parts;
    const page = await browser.newPage({ viewport: { width: W, height: H } });
    const msgs = [];
    page.on('console', m => { if (['error', 'warning'].includes(m.type())) msgs.push({ type: m.type(), text: m.text() }); });
    page.on('pageerror', e => msgs.push({ type: 'pageerror', text: String(e) }));
    await page.goto(`http://127.0.0.1:${port}/harness.html`);
    const rb = { body: body.name, clips: {}, errors: [] };
    let stats = null;
    try {
      stats = await page.evaluate(c => window.boot(c), { w: W, h: H, review: spec.review || {}, hdri: '/env.hdr', parts: parts.map((_, i) => i), bones: job.bones, parents: job.parents, hips_rest_src: job.hips_rest_src, fps: job.fps });
    } catch (e) { msgs.push({ type: 'boot', text: String(e) }); }
    rb.stats = stats;
    if (stats) {
      for (const name of job.clips) {
        if (!stats.clips.includes(name)) { rb.errors.push(`clip ${name} missing from the glb as Babylon loaded it`); continue; }
        const n = await page.evaluate(nm => window.frames(nm), name);
        const cdir = path.join(out, name); const files = {};
        const last = job.loops.includes(name) ? n - 2 : n - 1;   // loops: last frame duplicates the first
        const N = Math.min(job.frames || 8, last + 1);
        const ks = [...new Set(Array.from({ length: N }, (_, i) => Math.round(i * last / Math.max(1, N - 1))))];
        const shots = [];
        for (const k of ks) shots.push({ name: `f${k}`, url: await page.evaluate(([nm, k]) => window.shootFrame(nm, k), [name, k]) });
        files.strip = save(path.join(cdir, `${body.name}_strip.png`), await page.evaluate(([it, t]) => window.sheet(it, 4, t, 0.5), [shots, `${name}  ${body.name}  frames ${ks.join(',')} of ${n} (in place)`]));
        const diag = job.diag[name];
        const mk = await page.evaluate(([nm, d]) => window.buildMarkers(nm, d), [name, diag]);
        const ko = [...new Set(Array.from({ length: 5 }, (_, i) => Math.round(i * last / 4)))];
        files.side = save(path.join(cdir, `${body.name}_side.png`), await page.evaluate(([nm, k]) => window.shootSide(nm, k), [name, ko]));
        files.top = save(path.join(cdir, `${body.name}_top.png`), await page.evaluate(([nm, k]) => window.shootTop(nm, k), [name, ko]));
        const cc = [];
        for (const k of ko.slice(0, 4)) cc.push({ name: `CCTV f${k}`, url: await page.evaluate(([nm, k]) => window.shootCCTV(nm, k), [name, k]) });
        files.cctv = save(path.join(cdir, `${body.name}_cctv.png`), await page.evaluate(([it, t]) => window.sheet(it, 2, t, 0.5), [cc, `${name}  ${body.name}  Governor CCTV (mast ${spec.review?.feed?.mount_height ?? 3.5} m)`]));
        if (job.video) {
          const vd = path.join(cdir, `${body.name}_video`); fs.mkdirSync(vd, { recursive: true });
          const reps = job.loops.includes(name) ? 2 : 1; let i = 0;
          for (let r = 0; r < reps; r++) for (let k = 0; k <= last; k++) save(path.join(vd, `f${String(i++).padStart(4, '0')}.png`), await page.evaluate(([nm, k]) => window.shootTrack(nm, k), [name, k]));
          files.video_frames = vd;
        }
        // joint positions as posed on this body (checked against the build's reference in anim_cli)
        const jp = {};
        for (const k of [0, Math.floor(last / 2), n - 1]) { await page.evaluate(([nm, k]) => window.poseFrame(nm, k, true), [name, k]); jp[k] = await page.evaluate(b => window.jointPos(b), ['root', 'hips', 'head', 'hand_l', 'hand_r', 'foot_l', 'foot_r']); }
        rb.clips[name] = { frames: n, files, markers: mk.markers, joints: jp };
        console.log(`${body.name} ${name}: ${n} frames`);
      }
    }
    rb.errors.push(...msgs.filter(m => m.type !== 'warning').map(m => m.text));
    rb.warnings = msgs.filter(m => m.type === 'warning').map(m => m.text).slice(0, 20);
    report.bodies.push(rb);
    await page.close();
  }
} finally {
  await browser.close(); server.close();
}
report.errors = report.bodies.flatMap(b => b.errors.map(e => `${b.body}: ${e}`));
fs.writeFileSync(path.join(out, 'review.json'), JSON.stringify(report, null, 1));
console.log(JSON.stringify({ errors: report.errors.length, out }, null, 1));
process.exit(report.errors.length ? 1 : 0);
