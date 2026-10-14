// shoot_fp.mjs -- first-person review renders in Babylon (headless): the player's view from the eye at the
// view-model FOV, hand close-ups, a side check, per-frame video frames, and a Babylon-vs-build probe.
//
//   node shoot_fp.mjs <set.glb> --job job.json
//
// job.json (written by anim_cli.py review for first_person / hand_poses sets):
//   { spec, out, mode: "fp"|"poses", clips:[{name, frames, loop, strip:[k...], labels:{k:{lines, events}}}],
//     parts:[arms-or-body glb], arms_asset: bool, item: glb|null, item_id, parts_def, meta:{clip: meta},
//     eye:[3], fov, world_fov, near, size:[w,h], video: bool, fps, bones, parents, arm_bones }
// Writes <out>/<clip>/{eye_strip,side,hands,world_fov}.png (+ video frames) and <out>/review.json.
// One browser, one page. Never start more than one.
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
const hdri = path.join(GAM_TOOLS, 'review', 'env', 'overcast_1k.hdr');
const types = { '.html': 'text/html', '.js': 'text/javascript', '.glb': 'model/gltf-binary', '.hdr': 'application/octet-stream', '.wasm': 'application/wasm' };
const server = http.createServer((req, res) => {
  const u = decodeURIComponent(req.url.split('?')[0]); let f = null;
  if (u === '/harness.html') f = path.join(HERE, 'harness_fp.html');
  else if (u === '/anim/set.glb') f = glb;
  else if (u.startsWith('/part/')) f = job.parts[parseInt(u.slice(6))];
  else if (u === '/item/item.glb') f = job.item;
  else if (u.startsWith('/item/')) f = ((job.items || {})[path.basename(u, '.glb')] || {}).glb;   // hands sets: one item per clip
  else if (u.startsWith('/sleeve/')) f = (job.sleeves || {})[path.basename(u, '.glb')];
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
const report = { glb, clips: {}, errors: [], warnings: [] };
try {
  const page = await browser.newPage({ viewport: { width: W, height: H } });
  const msgs = [];
  page.on('console', m => { if (['error', 'warning'].includes(m.type())) msgs.push({ type: m.type(), text: m.text() }); });
  page.on('pageerror', e => msgs.push({ type: 'pageerror', text: String(e) }));
  await page.goto(`http://127.0.0.1:${port}/harness.html`);
  let stats = null;
  try {
    stats = await page.evaluate(c => window.boot(c), { w: W, h: H, review: spec.review || {}, hdri: '/env.hdr', bones: job.bones, parents: job.parents, arm_bones: job.arm_bones,
      arms_asset: job.arms_asset, item: !!job.item, item_id: job.item_id, grip_r: job.grip_r, parts_def: job.parts_def || {}, meta: job.meta,
      items: Object.fromEntries(Object.entries(job.items || {}).map(([k, v]) => [k, { parts_def: v.parts_def }])), sleeves: Object.fromEntries(Object.keys(job.sleeves || {}).map(k => [k, '/sleeve/' + k + '.glb'])),
      clipcfg: job.items || job.sleeves ? Object.fromEntries(job.clips.map(c => [c.name, { item: c.item, grip_r: c.grip_r, track: c.track, sleeves: c.sleeves, props: c.props }])) : null, eye: job.eye, fov: job.fov, near: job.near, fps: job.fps });
  } catch (e) { msgs.push({ type: 'boot', text: String(e) }); }
  report.stats = stats;
  if (stats) {
    for (const c of job.clips) {
      const name = c.name;
      if (!stats.clips.includes(name)) { report.errors.push(`clip ${name} missing from the glb as Babylon loaded it`); continue; }
      const n = await page.evaluate(nm => window.frames(nm), name);
      const cdir = path.join(out, name); const files = {};
      if (job.mode === 'poses') {
        const side = name.endsWith('_l') ? 'l' : 'r'; const shots = [];
        for (const v of ['palm', 'back', 'front', 'top', 'palm34', 'thumb34']) shots.push({ name: { palm34: '3/4 palm-up (into the palm, from the fingertips)', thumb34: '3/4 thumb side' }[v] || v, url: await page.evaluate(([nm, s, v]) => window.shootHand(nm, 0, s, v), [name, side, v]) });
        files.hands = save(path.join(cdir, 'hands.png'), await page.evaluate(([it, t]) => window.sheet(it, 6, t, 0.5), [shots, `${name}`]));
        files.palm34 = save(path.join(cdir, 'palm34.png'), shots[4].url); files.thumb34 = save(path.join(cdir, 'thumb34.png'), shots[5].url);   // full-res 3/4 views
        ['palm', 'back', 'front', 'top'].forEach((v, i) => { files[v] = save(path.join(cdir, v + '.png'), shots[i].url); });   // full-res orthogonal views too (critic, 2026-10-13)
        report.clips[name] = { frames: n, files }; console.log(`${name}`); continue;
      }
      const shots = [];
      for (const k of c.strip) shots.push({ name: `f${k}${(c.labels[k]?.events || []).length ? '  ' + c.labels[k].events.join(' ') : ''}`, url: await page.evaluate(([nm, k, lab]) => window.shootEye(nm, k, null, lab), [name, k, c.labels[k] || {}]) });
      files.eye_strip = save(path.join(cdir, 'eye_strip.png'), await page.evaluate(([it, t]) => window.sheet(it, 4, t, 0.5), [shots, `${name}  eye view  FOV ${job.fov} deg vertical, near ${job.near} m  (dashed = 4:3 safe frame, + = screen centre)`]));
      const sd = [];
      for (const k of c.strip.filter((_, i) => i % 2 === 0).slice(0, 4)) sd.push({ name: `side f${k}`, url: await page.evaluate(([nm, k]) => window.shootSide(nm, k), [name, k]) });
      files.side = save(path.join(cdir, 'side.png'), await page.evaluate(([it, t]) => window.sheet(it, 4, t, 0.5), [sd, `${name}  outside view of the view-model rig (arms only; camera = the eye)`]));
      const hk = c.hold_frame ?? c.strip[Math.floor(c.strip.length / 2)];
      const hs = [];
      for (const [s, v] of [['r', 'eye'], ['r', 'palm'], ['r', 'back'], ['l', 'palm'], ['l', 'back'], ['r', 'top']]) hs.push({ name: `f${hk} hand_${s} ${v}`, url: await page.evaluate(([nm, k, s, v]) => window.shootHand(nm, k, s, v), [name, hk, s, v]) });
      files.hands = save(path.join(cdir, 'hands.png'), await page.evaluate(([it, t]) => window.sheet(it, 3, t, 0.5), [hs, `${name}  grip close-ups at frame ${hk}`]));
      files.world_fov = save(path.join(cdir, 'world_fov.png'), await page.evaluate(([nm, k, f, lab]) => window.shootEye(nm, k, f, lab), [name, hk, job.world_fov, { lines: [`same frame at the WORLD FOV ${job.world_fov} deg (what a single-camera view model would look like)`] }]));
      if (job.video) {
        const vd = path.join(cdir, 'video'); fs.mkdirSync(vd, { recursive: true });
        const reps = c.loop ? 2 : 1; let i = 0; const last = c.loop ? n - 2 : n - 1;
        for (let r = 0; r < reps; r++) for (let k = 0; k <= last; k++) save(path.join(vd, `f${String(i++).padStart(4, '0')}.png`), await page.evaluate(([nm, k, lab]) => window.shootEye(nm, k, null, lab), [name, k, c.labels[k] || { lines: [`${name}  f${k}`] }]));
        files.video_frames = vd;
      }
      const probe = {};
      for (const k of c.probe || []) { await page.evaluate(([nm, k]) => window.poseFrame(nm, k), [name, k]); probe[k] = await page.evaluate(() => window.probe()); }
      report.clips[name] = { frames: n, files, probe };
      console.log(`${name}: ${n} frames`);
    }
  }
  report.errors.push(...msgs.filter(m => m.type !== 'warning').map(m => m.text));
  report.warnings = msgs.filter(m => m.type === 'warning').map(m => m.text).slice(0, 20);
  await page.close();
} finally {
  await browser.close(); server.close();
}
fs.writeFileSync(path.join(out, 'review.json'), JSON.stringify(report, null, 1));
console.log(JSON.stringify({ errors: report.errors.length, out, stats: report.stats && { arm_tris_kept: report.stats.arm_tris_kept, parts: report.stats.parts, sockets: report.stats.sockets } }, null, 1));
process.exit(report.errors.length ? 1 : 0);
