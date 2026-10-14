// shoot.mjs -- render an exported .glb in Babylon (the target engine) and save review images.
//
//   node shoot.mjs <asset.glb> --spec assets/spec.json --brief brief.json --out assets/review/<id>
//                  [--views front,three_quarter,eye,...] [--size 1280x720] [--sheet assets/review/<id>.png]
//                  [--with other1.glb,other2.glb]   context assets loaded alongside (LOD0 shown), e.g. a
//                                                    character's body + head + outfits rendered together
//
// Writes <out>/<view>.png, the contact sheet, and <out>/review.json (console errors, loaded
// node names, extras as Babylon sees them, per-LOD triangle counts, sockets). Loading with no
// console errors is part of the definition of done, so errors are reported, not hidden.
import fs from 'node:fs';
import http from 'node:http';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const TOOLS = path.dirname(HERE);
const args = process.argv.slice(2);
const opt = (k, d) => { const i = args.indexOf('--' + k); return i >= 0 ? args[i + 1] : d; };
const glb = path.resolve(args[0]);
// --with a.glb,b.glb@x:y:z@rotZ : context assets, optional placement in Blender axes (z up)
const withSpecs = (opt('with') || '').split(',').filter(Boolean).map(e => {
  const [p, off, rot] = e.split('@');
  const o = off ? off.split(':').map(Number) : [0, 0, 0];
  return { file: path.resolve(p), offset: [o[0], o[2], -o[1]], rotY: rot ? Number(rot) : 0 };   // -> glTF axes
});
const withGlbs = withSpecs.map(w => w.file);
const spec = JSON.parse(fs.readFileSync(opt('spec'), 'utf8'));
const brief = opt('brief') ? JSON.parse(fs.readFileSync(opt('brief'), 'utf8')) : { id: path.basename(glb, '.glb') };
const out = path.resolve(opt('out', path.join(path.dirname(glb), 'review_' + brief.id)));
const [W, H] = opt('size', '1280x720').split('x').map(Number);
const review = spec.review || {};
let views = (opt('views') || (brief.review_views || review.views || ['front', 'three_quarter', 'eye', 'iso']).join(',')).split(',');
if (!views.includes('iso_context') && views.includes('iso')) views.splice(views.indexOf('iso') + 1, 0, 'iso_context');
const cat = spec.categories?.[brief.category] || {};
const densCls = brief.density_class || cat.density_class || 'prop';
const target = brief.texel_density || spec.texel_density?.[densCls] || 512;
fs.mkdirSync(out, { recursive: true });

const hdri = review.hdri ? path.resolve(path.dirname(opt('spec')), review.hdri) : path.join(HERE, 'env', 'overcast_1k.hdr');
const types = { '.html': 'text/html', '.js': 'text/javascript', '.glb': 'model/gltf-binary', '.hdr': 'application/octet-stream', '.png': 'image/png', '.wasm': 'application/wasm' };
const server = http.createServer((req, res) => {
  const u = decodeURIComponent(req.url.split('?')[0]);
  let f = null;
  if (u === '/asset/asset.glb') f = glb;
  else if (u.startsWith('/with/')) f = withGlbs[parseInt(u.slice(6))];
  else if (u === '/env.hdr') f = hdri;
  else if (u.startsWith('/tools/')) f = path.join(TOOLS, u.slice(7));
  if (!f || !fs.existsSync(f)) { res.writeHead(404); res.end(); return; }
  res.writeHead(200, { 'Content-Type': types[path.extname(f)] || 'application/octet-stream' });
  fs.createReadStream(f).pipe(res);
});
await new Promise(r => server.listen(0, '127.0.0.1', r));
const port = server.address().port;

const browser = await chromium.launch({ args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist'] });
const page = await browser.newPage({ viewport: { width: W, height: H } });
const consoleMsgs = [];
page.on('console', m => { if (['error', 'warning'].includes(m.type())) consoleMsgs.push({ type: m.type(), text: m.text() }); });
page.on('pageerror', e => consoleMsgs.push({ type: 'pageerror', text: String(e) }));
await page.goto(`http://127.0.0.1:${port}/tools/review/harness.html`);
let stats;
try {
  stats = await page.evaluate(c => window.boot(c), {
    w: W, h: H, review, hdri: '/env.hdr', target_density: target,
    eye_height: spec.reference_dims_m?.eye || 1.62, with: withGlbs.map((_, i) => `/with/${i}.glb`), withXf: withSpecs.map(w => ({ offset: w.offset, rotY: w.rotY })),
  });
} catch (e) {
  consoleMsgs.push({ type: 'boot', text: String(e) });
}
const shots = [];
if (stats && brief.review_offset && !brief.review_held) {
  // wall/ceiling-mounted pieces: pivot at the mount, geometry below/behind it -> move the
  // asset to its mounted height for review (Blender axes in the brief: [x, y, z], z up)
  const o = brief.review_offset;
  await page.evaluate(h => window.liftAsset(h), { lift: o[2] || 0 });
}
if (stats && brief.review_held) {
  // held items: lift to hand height; item-only ortho views + first-person view-model camera
  const ex = Object.values(stats.extras || {})[0] || {};
  const held = Object.assign({ lift: 1.02, grip: ex.ig_grip || 'pistol_grip', hands: ex.ig_hands || 1 }, brief.review_held === true ? {} : brief.review_held);
  await page.evaluate(h => window.liftAsset(h), held);
  views = ['fp', 'fp_close', 'item_side_l', 'item_side_r', 'item_top', 'item_front', ...views.filter(v => !['front', 'feed'].includes(v))];
}
if (stats) {
  for (const v of views) {
    if (v === 'sabotaged' && !stats.states.length) continue;
    if (v === 'broken' && !stats.broken) continue;
    if (v === 'collision' && !stats.col_meshes.length) continue;
    if (v === 'lods' && Object.keys(stats.lod_tris).length < 2) continue;
    const r = ['fp', 'fp_close', 'item_side_l', 'item_side_r', 'item_top', 'item_front'].includes(v)
      ? await page.evaluate(v => window.shootHeld(v), v) : await page.evaluate(v => window.shoot(v), v);
    const file = path.join(out, v + '.png');
    fs.writeFileSync(file, Buffer.from(r.url.split(',')[1], 'base64'));
    shots.push({ name: v, url: r.url, note: r.note, file });
  }
  if (stats.states.length && !views.includes('sabotaged')) {
    const r = await page.evaluate(v => window.shoot(v), 'sabotaged');
    fs.writeFileSync(path.join(out, 'sabotaged.png'), Buffer.from(r.url.split(',')[1], 'base64'));
    shots.push({ name: 'sabotaged', url: r.url, note: '', file: path.join(out, 'sabotaged.png') });
  }
  if (stats.broken && !views.includes('broken')) {
    const r = await page.evaluate(v => window.shoot(v), 'broken');
    fs.writeFileSync(path.join(out, 'broken.png'), Buffer.from(r.url.split(',')[1], 'base64'));
    shots.push({ name: 'broken', url: r.url, note: '', file: path.join(out, 'broken.png') });
  }
  // close-ups from brief.review_focus: [{name, center:[x,y,z] (Blender axes), radius}]
  for (const f of brief.review_focus || []) {
    const cg = [f.center[0], f.center[2], -f.center[1]];
    const modes = stats.states.length ? ['default', 'sabotaged'] : ['default'];
    const angles = (brief.category === 'env' || f.front_only) ? [[-35, 'a'], [35, 'b']] : [[-35, 'a'], [145, 'b']];   // facades are single-sided
    for (const mode of modes) for (const [az, tag] of angles) {
      const r = await page.evaluate(ff => window.shootFocus(ff), { center: cg, radius: f.radius, az, el: 15, mode });
      const name = `focus_${f.name}${mode === 'sabotaged' ? '_sab' : ''}_${tag}`;
      const file = path.join(out, name + '.png');
      fs.writeFileSync(file, Buffer.from(r.url.split(',')[1], 'base64'));
      shots.push({ name, url: r.url, note: '', file });
    }
  }
  // context pass: brief.review_context = {tile:[cols,rows], elevation, base} -> ctx_* views
  if (brief.review_context) {
    await page.evaluate(c => window.buildContext(c), brief.review_context);
    for (const v of brief.review_context.views || ['front', 'street', 'three_quarter', 'iso_context', 'feed']) {
      const r = await page.evaluate(v => window.shoot(v), v);
      const name = 'ctx_' + v, file = path.join(out, name + '.png');
      fs.writeFileSync(file, Buffer.from(r.url.split(',')[1], 'base64'));
      shots.push({ name, url: r.url, note: '', file });
    }
  }
  const tris = Object.entries(stats.lod_tris).map(([k, v]) => `lod${k} ${v}`).join(' / ');
  const title = `${brief.id}   ${tris} tris   bbox ${stats.bbox_gltf.min.join(',')} -> ${stats.bbox_gltf.max.join(',')}   target ${target} px/m`;
  const sheet = await page.evaluate(([items, t]) => window.sheet(items, 3, t), [shots.map(s => ({ name: s.name, url: s.url, note: s.note })), title]);
  const sheetFile = opt('sheet', path.join(out, 'sheet.png'));
  fs.mkdirSync(path.dirname(sheetFile), { recursive: true });
  fs.writeFileSync(sheetFile, Buffer.from(sheet.split(',')[1], 'base64'));
  shots.push({ name: 'sheet', file: sheetFile });
}
const errors = consoleMsgs.filter(m => m.type !== 'warning');
const report = { glb, id: brief.id, errors, warnings: consoleMsgs.filter(m => m.type === 'warning'), stats, views: shots.map(s => ({ view: s.name, file: s.file })) };
fs.writeFileSync(path.join(out, 'review.json'), JSON.stringify(report, null, 1));
console.log(JSON.stringify({ id: brief.id, errors: errors.length, views: shots.map(s => s.file) }, null, 1));
await browser.close(); server.close();
process.exit(errors.length ? 1 : 0);
