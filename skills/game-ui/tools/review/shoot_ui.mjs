// shoot_ui.mjs -- headless review of the game-ui assets (driven by ui_cli.py review).
//
//   node shoot_ui.mjs --job <assets>/src/ui/_work/review_job.json
//
// Serves harness_ui.html, the export/ui tree, the feed image and portraits from a local http server; runs ONE chromium
// page (DOM only, GPU off, no WebGL); screenshots every plate the harness builds at device scale 1 (game size, no
// resampling) into <review>/ui/<plate>.png and writes <review>/ui_review.json (plates, measured text fits, errors).
// One browser, one page. ui_cli holds every review slot while this runs. Never launches the game client.
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
const outDir = path.join(job.review_dir, 'ui'); fs.mkdirSync(outDir, { recursive: true });
const types = { '.html': 'text/html', '.js': 'text/javascript', '.mjs': 'text/javascript', '.png': 'image/png', '.svg': 'image/svg+xml', '.json': 'application/json', '.css': 'text/css', '.woff2': 'font/woff2', '.ttf': 'font/ttf' };
const server = http.createServer((req, res) => {
  const u = decodeURIComponent(req.url.split('?')[0]); let f = null;
  if (u === '/harness.html') f = path.join(HERE, 'harness_ui.html');
  else if (u === '/job.json') { res.writeHead(200, { 'Content-Type': 'application/json' }); res.end(JSON.stringify(job)); return; }
  else if (u.startsWith('/export/')) f = path.join(job.export, u.slice(8));
  else if (u === '/ctx/feed.png') f = job.feed_image;
  else if (u.startsWith('/ctx/portrait/')) f = job.portraits[Number(path.basename(u, '.png'))];
  else if (u.startsWith('/plates/')) f = (job.plate_modules || [])[Number(path.basename(u, '.mjs'))];   // project plate modules (src/ui/_review)
  if (!f || !fs.existsSync(f)) { res.writeHead(404); res.end(); return; }
  res.writeHead(200, { 'Content-Type': types[path.extname(f)] || 'application/octet-stream' });
  fs.createReadStream(f).pipe(res);
});
await new Promise((r) => server.listen(0, '127.0.0.1', r));
const port = server.address().port;
const browser = await chromium.launch({ args: ['--disable-gpu', '--js-flags=--max-old-space-size=512', '--font-render-hinting=none'] });
const report = { errors: [], warnings: [], plates: {}, fits: [], fonts: [] };
try {
  const page = await browser.newPage({ viewport: { width: 1600, height: 900 }, deviceScaleFactor: 1 });
  page.on('console', (m) => { if (m.type() === 'error') report.errors.push(m.text()); else if (m.type() === 'warning') report.warnings.push(m.text()); });
  page.on('pageerror', (e) => report.errors.push('pageerror: ' + String(e)));
  page.on('requestfailed', (r) => report.errors.push('requestfailed: ' + r.url()));
  await page.goto(`http://127.0.0.1:${port}/harness.html`);
  await page.waitForFunction(() => window.ready === true, null, { timeout: 60000 });
  const r = await page.evaluate(() => window.measure());
  report.fits = r.fits; report.fonts = r.fonts;
  for (const p of r.plates) {
    const el = await page.$('#plate-' + p.name);
    const file = path.join('ui', p.name + '.png');
    await el.screenshot({ path: path.join(job.review_dir, file) });
    report.plates[p.name] = { file, assets: p.assets, title: p.title };
    console.log(`plate ${p.name} -> ${file}`);
  }
} catch (e) {
  report.errors.push('harness: ' + String(e && e.stack || e));
} finally {
  report.errors = report.errors.filter((e) => !/favicon/.test(e));
  fs.writeFileSync(path.join(job.review_dir, 'ui_review.json'), JSON.stringify(report, null, 1));
  await browser.close(); server.close();
}
