// vfx_babylon.mjs -- game-vfx reference runtime: ig_vfx v1 data (export/vfx/vfx_<id>.json) -> Babylon.js CPU ParticleSystems.
//
// One file, two users: the headless review harness (UMD BABYLON global) and the engine playback test (NullEngine,
// @babylonjs/core ESM). Babylon is passed in as `B`, so this module imports nothing. It is the proposed body of
// packages/engine/src/render/vfx.ts (an engine ask; the skill never writes engine code).
//
//   const rt = createVfxRuntime(B, scene, { textureBase: '/assets/export/vfx/', ambient: 1 });
//   rt.register(def)                                   // optional: preload textures
//   rt.spawn(def, { position, rotation, ambient, distance })   // rotation: effect-local -> world (local -Z = out);
//                                                      // ambient: light level at the spot for lit emitters; distance: LOD
//   rt.spawnAtNode(def, socketNode)                    // a glTF socket empty (weapon socket_muzzle, socket_eject ...)
//   rt.spawnAtHit(def, point, normal)                  // impacts: local -Z along the surface normal (out of the wall)
//   rt.bindEvents(defs); rt.fire('fire', { socket_muzzle: node })   // meta.fp.events dispatch
//   rt.update(dt)                                      // called automatically from onBeforeRender unless autoUpdate:false
//
// Loops driven by gameplay (tear gas, fire, chimney; ig_vfx `inputs` + per-emitter `drive`, see references/format.md):
//   const inst = rt.spawn(def, { position, rotation, inputs: { radius_m: 3.2, density: 0.8, wind_mps: [0.6, 0.2] } })
//   inst.setInputs({ radius_m: 4.1 })                  // from the sim each tick; rate/size/alpha/speed/radius/light rescale
//   inst.setDistance(d)                                // LOD for long-lived loops (cull = stop emitting, keep the instance)
//   inst.moveTo(pos)                                   // the volume drifts; or spawnAtNode(def, node, { follow: true })
//   inst.stop()                                        // stop emitting, let particles die, then pool (rt.stop(inst) too)
//
// Time: every ParticleSystem runs with updateSpeed = 1/60 so lifetimes, speeds (m/s) and rates (1/s) are in seconds at
// the scene's animation ratio. All emission is manual (bursts and rate windows are driven by the instance clock), so an
// effect is reproducible given the same random source.

export const VFX_FORMAT = 'ig_vfx';
export const VFX_VERSION = 1;

const rng2 = (v) => (Array.isArray(v) ? [Number(v[0]), Number(v[1])] : [Number(v), Number(v)]);
const lerp = (a, b, t) => a + (b - a) * t;
const hex = (h) => { h = String(h).replace('#', ''); return [0, 2, 4].map((i) => parseInt(h.substr(i, 2), 16) / 255); };
function curveAt(curve, t, d = 1) {
  if (curve == null) return d;
  if (typeof curve === 'number') return curve;
  const p = [...curve].sort((a, b) => a[0] - b[0]);
  if (t <= p[0][0]) return p[0][1];
  for (let i = 1; i < p.length; i++) if (t <= p[i][0]) return lerp(p[i - 1][1], p[i][1], (t - p[i - 1][0]) / Math.max(1e-9, p[i][0] - p[i - 1][0]));
  return p[p.length - 1][1];
}

/** Runtime inputs (ig_vfx `inputs`): clamp to [min, max], default when missing. */
export function resolveInputs(def, given = {}) {
  const out = {};
  for (const [k, d] of Object.entries(def.inputs ?? {})) {
    let v = given[k] ?? d.default;
    if (typeof v === 'number') v = Math.min(d.max ?? Infinity, Math.max(d.min ?? -Infinity, v));
    out[k] = v;
  }
  return out;
}
/** drive list [[input, power], ...] -> product of (value / ref)^power (scalar inputs only). */
export function driveFactor(def, inputs, list) {
  let f = 1;
  for (const [k, p] of list ?? []) {
    const d = def.inputs?.[k]; const v = inputs[k];
    if (!d || typeof v !== 'number') continue;
    const ref = d.ref ?? d.max ?? d.default ?? 1;
    if (ref > 0) f *= Math.pow(Math.max(0, v) / ref, p);
  }
  return f;
}

/** Upper bound of particles alive at once for one emitter (capacity). */
export function emitterCapacity(em, duration) {
  const life = rng2(em.life_s)[1];
  let n = 0;
  for (const b of em.bursts || []) n += rng2(b.count ?? 1)[1];
  const r = em.rate;
  if (r && r.per_s > 0) n += Math.ceil(r.per_s * Math.min(life, (r.t1 ?? duration) - (r.t0 ?? 0)) + 1);
  return Math.max(1, Math.ceil(n) + 2);
}

export function createVfxRuntime(B, scene, opts = {}) {
  const base = (opts.textureBase ?? '/assets/export/vfx/').replace(/\/?$/, '/');
  const random = opts.random ?? Math.random;
  const textures = new Map();
  const instances = new Set();
  const byEvent = new Map();
  const pool = new Map();          // def.id -> [{ holder, systems }] released instances, reused by spawn
  let white = null;
  const rt = { scene, instances, options: opts };

  function texture(def, name) {
    if (opts.overdraw || opts.textures === false) {   // overdraw pass, or headless tests: a 4x4 white sheet (a null texture never gets ready)
      if (!white) { white = B.RawTexture.CreateRGBATexture(new Uint8Array(4 * 16).fill(255), 4, 4, scene, false, false, B.Texture.NEAREST_SAMPLINGMODE); white.hasAlpha = true; const it = white.getInternalTexture(); if (it) it.isReady = true; /* raw upload is synchronous; NullEngine leaves the flag false */ }
      return white;
    }
    const key = base + def.textures[name].path;
    let t = textures.get(key);
    // invertY=false: Babylon's sheet shader counts cell rows from v=0, so v=0 must be the image top (cell 0 = top-left).
    if (!t) { t = new B.Texture(key, scene, false, false, B.Texture.TRILINEAR_SAMPLINGMODE); t.hasAlpha = true; t.wrapU = t.wrapV = B.Texture.CLAMP_ADDRESSMODE; textures.set(key, t); }
    return t;
  }
  rt.register = (def) => { for (const em of def.emitters) texture(def, em.texture); return def; };

  // ------------------------------------------------------------------ shapes (effect-local, -Z = out)
  function sampleDir(shape, out) {
    const type = shape?.type ?? 'point';
    if (type === 'sphere') { const z = random() * 2 - 1, a = random() * Math.PI * 2, s = Math.sqrt(1 - z * z); out.set(s * Math.cos(a), s * Math.sin(a), z); return out; }
    if (type === 'hemisphere') { const z = -random(), a = random() * Math.PI * 2, s = Math.sqrt(1 - z * z); out.set(s * Math.cos(a), s * Math.sin(a), z); return out; }
    const ang = ((shape?.angle_deg ?? 0) * Math.PI) / 180;
    const amin = ((shape?.angle_min_deg ?? 0) * Math.PI) / 180;   // > 0: a hollow cone (ring of directions), e.g. a blast's ground skirt
    const cz = lerp(Math.cos(amin), Math.cos(ang), random()), a = random() * Math.PI * 2, s = Math.sqrt(Math.max(0, 1 - cz * cz));
    out.set(s * Math.cos(a), s * Math.sin(a), -cz); return out;      // cone about -Z (point / cone / disc / box)
  }
  function samplePos(shape, out) {
    const type = shape?.type ?? 'point'; const r = shape?.radius ?? 0;
    if (type === 'box') { const s = shape.size ?? [0, 0, 0]; out.set((random() - 0.5) * s[0], (random() - 0.5) * s[1], (random() - 0.5) * s[2]); return out; }
    if (type === 'disc') { const q = shape?.inner ?? 0, a = random() * Math.PI * 2, rr = r * Math.sqrt(q * q + (1 - q * q) * random()); out.set(rr * Math.cos(a), rr * Math.sin(a), 0); return out; }   // inner: ring (fraction of radius)
    if (r > 0) { sampleDir({ type: 'sphere' }, out); out.scaleInPlace(r * Math.cbrt(random())); return out; }
    out.set(0, 0, 0); return out;
  }

  // colour over life (LDR, straight alpha). Lit emitters take the light level at the spawn point (`ambient`, scalar or rgb):
  // applied per spawn, so pooled systems follow day/night and interiors.
  function applyColour(ps, em, ambient, alphaK = 1) {
    for (const g of [...(ps.getColorGradients() ?? [])]) ps.removeColorGradient(g.gradient);
    if (opts.overdraw) { const k = opts.overdrawUnit ?? 1 / 32; ps.color1 = ps.color2 = ps.colorDead = new B.Color4(k, k, k, 1); return; }
    // lit_floor: smoke lit by its own fire never falls below this light level (a fire's smoke column at curfew)
    const a0 = ambient ?? opts.ambient ?? 1; const lf = em.lit_floor ?? 0; const a = Array.isArray(a0) ? a0.map((v) => Math.max(v, lf)) : Math.max(a0, lf);
    const amb = em.lit ? (Array.isArray(a) ? a : [a, a, a]) : [1, 1, 1];
    const ak = Math.min(1, Math.max(0, alphaK));
    for (const [t, h, al] of em.color) { const c = hex(h); ps.addColorGradient(t, new B.Color4(c[0] * amb[0], c[1] * amb[1], c[2] * amb[2], al * ak)); }
  }

  // runtime inputs -> per-emitter factors (rate, radius, size, alpha, speed) and wind; recomputed on setInputs, from the
  // data each time (pooled systems never inherit another instance's scaling).
  function applyInputs(inst) {
    const def = inst.def; if (!def.inputs) return;
    const wind = inst.inputs.wind_mps; const w = Array.isArray(wind) ? wind : [0, 0];
    inst.emitters.forEach((e, i) => {
      const em = e.em; const dv = em.drive ?? {}; const ps = e.ps;
      const k = { rate: 1, radius: 1, size: 1, alpha: 1, speed: 1 };
      for (const key of Object.keys(k)) k[key] = driveFactor(def, inst.inputs, dv[key]);
      e.k = k; inst.ek[i] = k;
      const [c0, c1] = rng2(em.scale ?? 1); const st = em.stretch ?? 1;
      ps.minScaleX = c0 * k.size; ps.maxScaleX = c1 * k.size; ps.minScaleY = c0 * st * k.size; ps.maxScaleY = c1 * st * k.size;
      const [v0, v1] = rng2(em.speed_mps); ps.minEmitPower = v0 * k.speed; ps.maxEmitPower = v1 * k.speed;
      const g = em.gravity ?? [0, 0, 0]; const wg = em.wind_gain ?? 0;
      ps.gravity = new B.Vector3(g[0] + w[0] * wg, g[1], g[2] + w[1] * wg);
      applyColour(ps, em, inst.ambient, k.alpha);
    });
  }

  // ------------------------------------------------------------------ one emitter -> ParticleSystem
  function buildSystem(def, em, holder, idx = 0) {
    const inst = holder.inst;
    const tx = def.textures[em.texture];
    const cells = tx.grid[0] * tx.grid[1];
    const cap = emitterCapacity(em, def.duration_s);
    const ps = new B.ParticleSystem(`${def.id}.${em.name}`, cap, scene, null, cells > 1);
    ps.particleTexture = texture(def, em.texture);
    ps.blendMode = opts.overdraw || em.blend === 'add' ? B.ParticleSystem.BLENDMODE_ADD : B.ParticleSystem.BLENDMODE_STANDARD;
    ps.updateSpeed = 1 / 60;
    ps.emitter = inst.position.clone();
    ps.isLocal = false;
    ps.manualEmitCount = 0;
    ps.disposeOnStop = false;
    ps.preventAutoStart = true;
    if (opts.renderingGroupId != null) ps.renderingGroupId = opts.renderingGroupId;
    if (opts.layerMask != null) ps.layerMask = opts.layerMask;
    const [l0, l1] = rng2(em.life_s); ps.minLifeTime = l0; ps.maxLifeTime = l1;
    const [v0, v1] = rng2(em.speed_mps); ps.minEmitPower = v0; ps.maxEmitPower = v1;
    const [s0, s1] = rng2(em.size_m);
    if (em.size_curve) { for (const [t, k] of [...em.size_curve].sort((a, b) => a[0] - b[0])) ps.addSizeGradient(t, s0 * k, s1 * k); }
    else { ps.minSize = s0; ps.maxSize = s1; }
    const [c0, c1] = rng2(em.scale ?? 1); const st = em.stretch ?? 1;
    ps.minScaleX = c0; ps.maxScaleX = c1; ps.minScaleY = c0 * st; ps.maxScaleY = c1 * st;
    const [r0, r1] = rng2(em.rotation_deg ?? 0); ps.minInitialRotation = (r0 * Math.PI) / 180; ps.maxInitialRotation = (r1 * Math.PI) / 180;
    const [w0, w1] = rng2(em.spin_dps ?? 0); ps.minAngularSpeed = (w0 * Math.PI) / 180; ps.maxAngularSpeed = (w1 * Math.PI) / 180;
    const g = em.gravity ?? [0, 0, 0]; ps.gravity = new B.Vector3(g[0], g[1], g[2]);
    if (em.drag) { const lm = (l0 + l1) / 2; for (const t of [0, 0.25, 0.5, 0.75, 1]) ps.addVelocityGradient(t, Math.exp(-em.drag * t * lm)); }
    applyColour(ps, em, inst.ambient);
    // billboard
    ps.isBillboardBased = true;
    ps.billboardMode = em.billboard === 'stretched' ? B.ParticleSystem.BILLBOARDMODE_STRETCHED : em.billboard === 'y_axis' ? B.ParticleSystem.BILLBOARDMODE_Y : B.ParticleSystem.BILLBOARDMODE_ALL;
    // pivot: Babylon's billboard shaders add translationPivot a second time, unscaled (metres), so it cannot place a quad's
    // base at the particle. Instead the spawn point moves along the emission axis by half the mean quad length.
    const [p0, p1] = rng2(em.size_m), [q0, q1] = rng2(em.scale ?? 1);
    const baseShift = em.pivot ? -em.pivot[1] * ((p0 + p1) / 2) * ((q0 + q1) / 2) * (em.stretch ?? 1) : 0;
    // flipbook
    if (cells > 1) {
      ps.spriteCellWidth = tx.size[0] / tx.grid[0]; ps.spriteCellHeight = tx.size[1] / tx.grid[1];
      const fr = em.frames ?? [0, cells - 1]; const mode = em.frame_mode ?? 'over_life';
      ps.startSpriteCellID = fr[0]; ps.endSpriteCellID = mode === 'fixed' ? fr[0] : fr[1];
      ps.spriteCellLoop = false;
      ps.spriteRandomStartCell = mode === 'random';
      ps.spriteCellChangeSpeed = mode === 'over_life' ? 1 : 0;
    }
    // emission in the instance's frame
    const tmp = new B.Vector3();
    ps.startDirectionFunction = (_wm, dir) => { sampleDir(em.shape, tmp); B.Vector3.TransformNormalToRef(tmp, holder.inst.basis, dir); dir.normalize(); };
    ps.startPositionFunction = (_wm, pos) => { samplePos(em.shape, tmp); const kr = holder.inst.ek?.[idx]?.radius ?? 1; if (kr !== 1) tmp.scaleInPlace(kr); const o = em.offset ?? [0, 0, 0]; tmp.addInPlaceFromFloats(o[0], o[1], o[2] - (em.pivot_up ? 0 : baseShift)); B.Vector3.TransformCoordinatesToRef(tmp, holder.inst.basis, pos); pos.addInPlace(holder.inst.position); if (em.pivot_up) pos.y += baseShift; };
    // pivot_up: the base shift goes along world +Y (buoyant smoke on a socket whose axes point anywhere, e.g. a hand bone)
    // near_fade_m [n0, n1]: particles closer than n1 to the active camera fade out, closer than n0 are not drawn (size 0), so
    // a camera inside a gas volume does not pay for (or see) the quads cutting through it
    if (em.near_fade_m) {
      const [n0, n1] = em.near_fade_m; const base = ps.updateFunction; const resize = !!em.size_curve;   // size gradients re-set size every frame
      const fade = !opts.overdraw;                                                                  // the overdraw pass counts quads, not alpha
      ps.updateFunction = (particles) => {
        base(particles); const cam = scene.activeCamera; if (!cam) return; const c = cam.globalPosition;
        for (const p of particles) {
          const d = B.Vector3.Distance(p.position, c);
          if (d < n0 && resize) p.size = 0; else if (d < n1 && fade) { const k = Math.max(0, (d - n0) / (n1 - n0)); p.color.a *= k * k * (3 - 2 * k); }
        }
      };
    }
    ps.start();
    return ps;
  }

  // ------------------------------------------------------------------ instances
  rt.spawn = (def, where = {}) => {
    const position = where.position ? where.position.clone() : B.Vector3.Zero();
    const rotation = where.rotation ?? B.Quaternion.Identity();
    const dist = where.distance ?? 0; const lod = def.lod ?? {};
    if (lod.cull_m && dist > lod.cull_m) return null;
    const factor = lod.reduce_m && dist > lod.reduce_m ? lod.reduce_factor ?? 1 : 1;
    const basis = new B.Matrix(); B.Matrix.FromQuaternionToRef(rotation, basis);
    const inst = { def, t: 0, position, rotation, basis, factor, distFactor: factor, systems: [], emitters: [], light: null, alive: true, mute: !!where.mute, ambient: where.ambient,
                   inputs: resolveInputs(def, where.inputs), ek: [], stopping: false, follow: where.follow ?? null, lightK: 1, flick: 1, flickTarget: 1, flickT: 0 };
    const free = pool.get(def.id);
    let slot = free && free.length ? free.pop() : null;
    if (slot && (slot.systems.length !== def.emitters.length || slot.systems.some((ps, i) => ps.getCapacity() < emitterCapacity(def.emitters[i], def.duration_s)))) { for (const ps of slot.systems) ps.dispose(false); slot = null; }
    if (slot) {                                  // reuse warm systems: no texture/shader wait, emits on the first frame
      slot.holder.inst = inst;
      slot.systems.forEach((ps, i) => { ps.emitter = position.clone(); ps.manualEmitCount = 0; applyColour(ps, def.emitters[i], inst.ambient); ps.start(); });
    } else {
      const holder = { inst };
      slot = { holder, systems: def.emitters.map((em, i) => buildSystem(def, em, holder, i)) };
    }
    inst.slot = slot;
    def.emitters.forEach((em, i) => { const ps = slot.systems[i]; inst.systems.push(ps); inst.emitters.push({ em, ps, acc: 0, fired: new Set() }); });
    const L = def.hooks?.light;
    if (L && opts.lights !== false && !opts.overdraw && !inst.mute) {
      const pl = new B.PointLight(`${def.id}.light`, position.add(B.Vector3.TransformNormal(new B.Vector3(0, 0, -(L.offset_m ?? 0.1)), basis)), scene);
      const c = hex(L.color); pl.diffuse = new B.Color3(c[0], c[1], c[2]); pl.specular = pl.diffuse.scale(0.2);
      pl.range = L.range_m; pl.intensity = 0; pl.falloffType = B.PointLight.FALLOFF_STANDARD;
      inst.light = pl;
    }
    inst.activeCount = () => inst.systems.reduce((n, ps) => n + ps.getActiveCount(), 0);
    inst.update = (dt) => step(inst, dt);
    inst.dispose = () => dispose(inst);
    inst.setInputs = (v = {}) => { inst.inputs = resolveInputs(def, { ...inst.inputs, ...v }); applyInputs(inst); return inst; };
    inst.setDistance = (d) => { inst.distFactor = lod.cull_m && d > lod.cull_m ? 0 : lod.reduce_m && d > lod.reduce_m ? lod.reduce_factor ?? 1 : 1; return inst; };
    inst.moveTo = (p) => { inst.position.copyFrom(p); placeLight(inst); return inst; };
    inst.stop = () => { inst.stopping = true; return inst; };
    applyInputs(inst);
    instances.add(inst);
    step(inst, 0);
    return inst;
  };
  rt.spawnAtNode = (def, node, extra = {}) => {
    node.computeWorldMatrix(true);
    const s = new B.Vector3(), q = new B.Quaternion(), p = new B.Vector3();
    node.getWorldMatrix().decompose(s, q, p);
    return rt.spawn(def, { ...extra, position: p, rotation: q, follow: extra.follow ? node : null });   // follow: track the node (a thrown molotov's socket_fuse)
  };
  rt.stop = (inst) => inst?.stop();
  function placeLight(inst) {
    const L = inst.def.hooks?.light; if (!inst.light || !L) return;
    inst.light.position.copyFrom(inst.position.add(B.Vector3.TransformNormal(new B.Vector3(0, 0, -(L.offset_m ?? 0.1)), inst.basis)));
  }
  rt.spawnAtHit = (def, point, normal, extra = {}) => {
    const q = new B.Quaternion(); B.Quaternion.FromUnitVectorsToRef(new B.Vector3(0, 0, -1), normal.normalizeToNew(), q);
    return rt.spawn(def, { ...extra, position: point, rotation: q });
  };
  rt.bindEvents = (defs) => {
    for (const d of defs) for (const a of [d.attach, ...(d.attach?.also ?? [])]) {
      if (!a?.event) continue; if (!byEvent.has(a.event)) byEvent.set(a.event, []); byEvent.get(a.event).push({ def: d, socket: a.socket });
    }
  };
  /** meta.fp.events dispatch: sockets = { socket_muzzle: node, socket_eject: node, ... } of the held item. */
  rt.fire = (event, sockets, extra = {}) => (byEvent.get(event) || []).map((b) => (sockets[b.socket] ? rt.spawnAtNode(b.def, sockets[b.socket], extra) : null)).filter(Boolean);

  function step(inst, dt) {
    if (!inst.alive) return false;
    if (inst.mute) return true;              // prepare(): compiled and pooled, never emits
    const t0 = inst.t; inst.t += dt; const t1 = inst.t; const def = inst.def;
    if (inst.follow && !inst.follow.isDisposed?.()) {
      inst.follow.computeWorldMatrix(true); const s = new B.Vector3();
      inst.follow.getWorldMatrix().decompose(s, inst.rotation, inst.position); B.Matrix.FromQuaternionToRef(inst.rotation, inst.basis); placeLight(inst);
      // round 6: emitters with `local: true` carry their live particles rigidly with the followed node (old world -> new
      // world, so an ember 7.5 cm out on a turning hand stays on the tip; tiny attached sprites must not trail dots behind a
      // moving hand). Babylon isLocal / Godot local_coords in the engine.
      const W = inst.follow.getWorldMatrix().clone();
      if (inst.prevW) {
        const M = inst.prevW.clone().invert().multiply(W);
        for (const e of inst.emitters) if (e.em.local && e.ps.particles) for (const p of e.ps.particles) B.Vector3.TransformCoordinatesToRef(p.position, M, p.position);
      }
      inst.prevW = W;
    }
    // loops: distance LOD (setDistance) x share of the effect's budget when many instances run (lod.share_n full-rate instances)
    let share = 1;
    if (def.loop && def.lod?.share_n) { let n = 0; for (const o of instances) if (o.def.id === def.id && !o.stopping && !o.mute) n++; share = Math.min(1, def.lod.share_n / Math.max(1, n)); }
    if (def.loop) inst.factor = inst.stopping ? 0 : inst.distFactor * share;
    for (const e of inst.emitters) {
      const em = e.em;
      (em.bursts || []).forEach((b, i) => {
        const bt = b.t ?? 0;
        if (!e.fired.has(i) && bt <= t1 + 1e-9) { e.fired.add(i); const [n0, n1] = rng2(b.count ?? 1); e.ps.manualEmitCount += Math.round(lerp(n0, n1, random()) * inst.factor); }
      });
      const r = em.rate;
      if (r && r.per_s > 0 && dt > 0) {
        const a = Math.max(t0, r.t0 ?? 0), z = Math.min(t1, r.t1 ?? (def.loop ? Infinity : def.duration_s));
        if (z > a) { e.acc += r.per_s * (z - a) * inst.factor * (e.k?.rate ?? 1); const n = Math.floor(e.acc); e.acc -= n; e.ps.manualEmitCount += n; }
      }
    }
    if (inst.light && def.hooks.light.loop) {
      // looping light (fire): periodic curve x smoothed random flicker x drive (fuel, size); fades out over 0.6 s when stopped
      const L = def.hooks.light; const per = Math.max(0.05, L.period_s ?? 1);
      if (inst.t >= inst.flickT) { inst.flickT = inst.t + (L.flicker_step_s ?? 0.07); inst.flickTarget = 1 - (L.jitter ?? 0.2) * random(); }
      inst.flick += (inst.flickTarget - inst.flick) * Math.min(1, dt * 18);
      if (inst.stopping) inst.lightK = Math.max(0, inst.lightK - dt / 0.6);
      const k = driveFactor(def, inst.inputs, L.drive) * inst.lightK * (inst.distFactor > 0 ? 1 : 0);
      inst.light.intensity = Math.min(L.intensity, L.intensity * k) * curveAt(L.curve ?? [[0, 1], [1, 1]], (inst.t % per) / per) * inst.flick;
    } else if (inst.light) {
      const L = def.hooks.light; const u = inst.t / Math.max(1e-6, L.duration_s);
      inst.light.intensity = u >= 1 ? 0 : L.intensity * curveAt(L.curve ?? [[0, 1], [1, 0]], u);
      if (u >= 1) { inst.light.dispose(); inst.light = null; }
    }
    if (!def.loop && inst.t >= def.duration_s - 1e-9) { dispose(inst); return false; }
    if (inst.stopping && dt > 0 && inst.activeCount() === 0 && inst.lightK <= 0.001 + (inst.light ? 0 : 1)) { dispose(inst); return false; }
    return true;
  }
  function dispose(inst) {
    if (!inst.alive) return; inst.alive = false;
    // release to the pool (stopped, cleared); beyond poolSize per effect the systems are disposed
    for (const ps of inst.systems) { ps.stop(); ps.reset(); ps.manualEmitCount = 0; }
    const free = pool.get(inst.def.id) ?? []; pool.set(inst.def.id, free);
    if (free.length < (opts.poolSize ?? 4)) free.push(inst.slot); else for (const ps of inst.systems) ps.dispose(false);
    inst.light?.dispose(); inst.light = null; instances.delete(inst);
  }
  /** Preload sheets and compile every system variant BEFORE the first spawn (call at level load). Particle systems do not
   *  emit until their texture and shader are ready, which would swallow a 2-frame muzzle flash on first use.
   *  `render` advances one frame (default scene.render); the muted warm-up instances (full capacity, no emission) sit 1 km below the world and go to the pool. */
  rt.prepare = async (defs, render = () => scene.render(), maxFrames = 600) => {
    const tex = defs.flatMap((d) => d.emitters.map((em) => texture(d, em.texture))).filter(Boolean);
    const insts = defs.map((d) => rt.spawn(d, { position: new B.Vector3(0, -1000, 0), mute: true }));
    for (let k = 0; k < maxFrames; k++) {
      if (tex.every((t) => t.isReady()) && insts.every((i) => !i || !i.alive || i.systems.every((ps) => ps.isReady()))) break;
      render(); await new Promise((r) => setTimeout(r, 5));
    }
    const ok = tex.every((t) => t.isReady());
    for (const i of insts) if (i) dispose(i);
    return ok;
  };
  rt.update = (dt) => { for (const i of [...instances]) step(i, dt); };
  rt.pooled = (id) => (pool.get(id) ?? []).length;
  rt.activeCount = () => [...instances].reduce((n, i) => n + i.activeCount(), 0);
  rt.dispose = () => { for (const i of [...instances]) dispose(i); for (const f of pool.values()) for (const sl of f) for (const ps of sl.systems) ps.dispose(false); pool.clear(); for (const t of textures.values()) t.dispose(); white?.dispose(); if (obs) scene.onBeforeRenderObservable.remove(obs); };
  const obs = opts.autoUpdate === false ? null : scene.onBeforeRenderObservable.add(() => rt.update(scene.getAnimationRatio() / 60));
  return rt;
}
