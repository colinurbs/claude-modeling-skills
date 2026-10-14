// shoot_vfx.mjs -- headless Babylon review of one vfx effect (driven by vfx_cli.py review).
//
//   node shoot_vfx.mjs --job <id>/_work/review_job.json
//
// Serves harness_vfx.html, the reference runtime, the effect's texture sheets and its context (weapon glb, surface
// textures, body glb, decal atlas) from a local http server; runs ONE chromium (SwiftShader WebGL) page; writes
// <out>/{strip_day,strip_night,side,context_day,context_night,cctv,overdraw}.png and <out>/review.json.
// One browser, one page. Never start more than one (vfx_cli holds every review slot while this runs).
import fs from 'node:fs';
import http from 'node:http';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const SKILLS = path.resolve(HERE, '..', '..', '..');
const GAM_TOOLS = path.join(SKILLS, 'game-asset-modeling', 'tools');
const { chromium } = await import(pathToFileURL(path.join(GAM_TOOLS, 'node_modules', 'playwright', 'index.mjs')).href);
const args = process.argv.slice(2);
const job = JSON.parse(fs.readFileSync(args[args.indexOf('--job') + 1], 'utf8'));
const out = path.resolve(job.out); fs.mkdirSync(out, { recursive: true });
const C = job.context;
const types = { '.html': 'text/html', '.js': 'text/javascript', '.mjs': 'text/javascript', '.glb': 'model/gltf-binary', '.png': 'image/png', '.wasm': 'application/wasm' };
const server = http.createServer((req, res) => {
  const u = decodeURIComponent(req.url.split('?')[0]); let f = null;
  if (u === '/harness.html') f = path.join(HERE, 'harness_vfx.html');
  else if (u === '/runtime/vfx_babylon.mjs') f = path.join(HERE, '..', 'runtime', 'vfx_babylon.mjs');
  else if (u.startsWith('/vfx/')) f = path.join(job.texture_base, u.slice(5));
  else if (u === '/ctx/weapon.glb' || u === '/ctx/body.glb' || u === '/ctx/prop.glb') f = C.glb;
  else if (u === '/ctx/anim.glb') f = C.anim && C.anim.glb;   // round 6: review.anim clip set
  else if (u.startsWith('/ctx/tex/')) f = (C.tex || {})[path.basename(u, '.png')];
  else if (u.startsWith('/ctx/decal')) f = job.decal?.base;
  else if (u.startsWith('/gam/')) f = path.join(GAM_TOOLS, u.slice(5));
  if (!f || !fs.existsSync(f)) { res.writeHead(404); res.end(); return; }
  res.writeHead(200, { 'Content-Type': types[path.extname(f)] || (u.startsWith('/ctx/') ? 'image/png' : 'application/octet-stream') });
  fs.createReadStream(f).pipe(res);
});
await new Promise((r) => server.listen(0, '127.0.0.1', r));
const port = server.address().port;
const save = (name, url) => { const f = path.join(out, name + '.png'); fs.writeFileSync(f, Buffer.from(url.split(',')[1], 'base64')); return f; };

const browser = await chromium.launch({ args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist', '--js-flags=--max-old-space-size=1024'] });
const report = { id: job.effect.id, errors: [], warnings: [], files: {}, measure: {} };
try {
  const [W, H] = job.review.size;
  const page = await browser.newPage({ viewport: { width: W * 2, height: H * 2 } });
  page.on('console', (m) => { if (m.type() === 'error') report.errors.push(m.text()); else if (m.type() === 'warning') report.warnings.push(m.text()); });
  page.on('pageerror', (e) => report.errors.push('pageerror: ' + String(e)));
  await page.goto(`http://127.0.0.1:${port}/harness.html`);
  await page.waitForFunction(() => window.bootReady === true, null, { timeout: 60000 });
  await page.evaluate((j) => window.boot(j), job);
  const r = await page.evaluate(() => window.run());
  for (const [k, v] of Object.entries(r.files)) report.files[k] = path.relative(path.dirname(out), save(k, v));
  report.measure = r.measure; report.counts = r.measure.counts; delete report.measure.counts;
  console.log(`${job.effect.id}: peak ${report.measure.peak_particles} particles, overdraw peak ${report.measure.overdraw_peak} fill ${report.measure.overdraw_fill}, clipped ${report.measure.clipped_pct}%`);
} catch (e) {
  report.errors.push('harness: ' + String(e && e.stack || e));
} finally {
  // texture 404s for optional context files are noise; keep real errors
  report.errors = report.errors.filter((e) => !/favicon/.test(e));
  fs.writeFileSync(path.join(out, 'review.json'), JSON.stringify(report, null, 1));
  await browser.close(); server.close();
}
process.exit(report.errors.length ? 1 : 0);
