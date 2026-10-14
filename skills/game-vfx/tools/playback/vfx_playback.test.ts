/**
 * Engine playback test for exported VFX (game-vfx). Runs with the ENGINE's Babylon (@babylonjs/core from
 * packages/engine/node_modules) under NullEngine, the way packages/engine/src/test/decals.test.ts does, and the
 * skill's reference runtime (tools/runtime/vfx_babylon.mjs, the proposed packages/engine/src/render/vfx.ts).
 *
 * Driven by `vfx_cli.py playback`, which copies this file to assets/src/vfx/_work/playback/ and runs the engine's
 * vitest with env: GVFX_ENGINE (packages/engine), GVFX_DIR (export/vfx), GVFX_SPEC (assets/spec.json),
 * GVFX_RUNTIME (vfx_babylon.mjs), GVFX_DECALS (materials/decals.json). Nothing is written into the engine.
 *
 * Per effect: the data builds one ParticleSystem per emitter with enough capacity; the effect plays to its duration
 * at 60 Hz with the measured alive count inside the class budget and frees every system at the end; event dispatch
 * spawns it at the socket and particles leave along the socket's -Z; the decal hook resolves through the engine's
 * own DecalSystem.
 */
import { describe, it, expect, beforeAll } from 'vitest';
import { readFileSync, readdirSync, realpathSync } from 'node:fs';
import { join } from 'node:path';

const env = (k: string): string => { const v = process.env[k]; if (!v) throw new Error(`${k} not set`); return v; };
const ENGINE = realpathSync(env('GVFX_ENGINE'));
const DIR = env('GVFX_DIR');
const spec = JSON.parse(readFileSync(env('GVFX_SPEC'), 'utf8'));
const decals = JSON.parse(readFileSync(env('GVFX_DECALS'), 'utf8')).m_decal_runtime;
const effects = readdirSync(DIR).filter((f) => /^vfx_.*\.json$/.test(f)).map((f) => JSON.parse(readFileSync(join(DIR, f), 'utf8')))
  .filter((d) => d.class !== 'shader');   // class shader (round 5) has no particle systems: its snippet is compiled by the review

/* eslint-disable @typescript-eslint/no-explicit-any */
let B: any, R: any, DS: any;
// rt.prepare = what the engine should do at level load: shaders (lazy-imported in Babylon 9) and textures ready, warm
// systems pooled, so the first spawn emits on its first frame.
const warm = (def: any) => def;
const newScene = () => {
  const scene = new B.Scene(new B.NullEngine());
  scene.getAnimationRatio = () => 1;
  const cam = new B.FreeCamera('c', new B.Vector3(0, 1.6, 4), scene); cam.setTarget(B.Vector3.Zero()); scene.activeCamera = cam;
  return scene;
};

beforeAll(async () => {
  B = await import(join(realpathSync(join(ENGINE, 'node_modules/@babylonjs/core')), 'index.js'));
  R = await import(env('GVFX_RUNTIME'));
  DS = await import(realpathSync(join(ENGINE, 'src/render/decals.ts')));
});

describe('vfx playback (NullEngine)', () => {
  it('finds exported effects', () => { expect(effects.length).toBeGreaterThan(0); });

  for (const def of effects) {
    const C = spec.vfx.classes[def.class];
    describe(def.id, () => {
      it('builds one ParticleSystem per emitter with enough capacity', async () => {
        const scene = newScene(); const rt = R.createVfxRuntime(B, scene, { textures: false, lights: true }); expect(await rt.prepare([warm(def)])).toBe(true);
        const inst = rt.spawn(def, { position: new B.Vector3(0, 1, 0) });
        expect(inst.systems.length).toBe(def.emitters.length);
        def.emitters.forEach((em: any, i: number) => {
          expect(inst.systems[i].name).toBe(`${def.id}.${em.name}`);
          expect(inst.systems[i].getCapacity()).toBeGreaterThanOrEqual(def.estimates.per_emitter_peak[em.name]);
          expect(inst.systems[i].updateSpeed).toBeCloseTo(1 / 60);
        });
        if (def.hooks?.light) expect(inst.light).not.toBeNull();
        rt.dispose(); scene.dispose();
      });

      it('plays to its duration inside the particle budget and frees everything', async () => {
        const scene = newScene(); const rt = R.createVfxRuntime(B, scene, { textures: false }); expect(await rt.prepare([warm(def)])).toBe(true);
        const inst = rt.spawn(def, { position: new B.Vector3(0, 1, 0) });
        const frames = Math.ceil(def.duration_s * 60) + 2; let peak = 0, seen = 0;
        for (let k = 0; k < frames; k++) { scene.render(); if (inst.alive) { const n = inst.activeCount(); peak = Math.max(peak, n); seen += n; } }
        expect(seen).toBeGreaterThan(0);                       // something was emitted
        expect(peak).toBeLessThanOrEqual(C.max_particles);
        expect(inst.alive).toBe(def.loop);                     // one-shots retire at duration_s
        if (!def.loop) {
          // retired systems go back to the runtime's pool: stopped, empty, reusable without a shader/texture wait
          expect(rt.pooled(def.id)).toBe(1); // the warm-up slot was reused by the spawn, then released
          for (const ps of inst.systems) { expect(ps.isStarted()).toBe(false); expect(ps.getActiveCount()).toBe(0); }
          const again = rt.spawn(def, { position: new B.Vector3(0, 1, 0) });
          expect(again.systems[0]).toBe(inst.systems[0]); expect(rt.pooled(def.id)).toBe(0);
          scene.render(); expect(again.activeCount()).toBeGreaterThan(0);  // a pooled system emits on its first frame
        }
        expect(rt.instances.size).toBe(1);
        rt.dispose(); scene.dispose();
      });

      it('lod: culled past cull_m, reduced past reduce_m', async () => {
        const scene = newScene(); const rt = R.createVfxRuntime(B, scene, { textures: false, lights: false });
        expect(rt.spawn(def, { position: B.Vector3.Zero(), distance: def.lod.cull_m + 1 })).toBeNull();
        const far = rt.spawn(def, { position: B.Vector3.Zero(), distance: def.lod.reduce_m + 1 });
        expect(far.factor).toBeCloseTo(def.lod.reduce_factor);
        rt.dispose(); scene.dispose();
      });

      if (def.attach?.socket && def.attach?.event) {
        it(`event ${def.attach.event} spawns at ${def.attach.socket} and emits along its -Z`, async () => {
          expect([...Object.keys(spec.animation.first_person.events.names), 'hit', 'ignite', 'detonate']).toContain(def.attach.event);
          const scene = newScene(); const rt = R.createVfxRuntime(B, scene, { textures: false, lights: false }); await rt.prepare([warm(def)]);
          const sock = new B.TransformNode(def.attach.socket, scene);
          sock.position.set(0.3, 1.4, -0.5); sock.rotationQuaternion = B.Quaternion.RotationYawPitchRoll(0.7, -0.2, 0.1);
          rt.bindEvents([def]);
          const [inst] = rt.fire(def.attach.event, { [def.attach.socket]: sock });
          expect(inst).toBeTruthy();
          expect(B.Vector3.Distance(inst.position, sock.getAbsolutePosition())).toBeLessThan(1e-5);
          const fwd = B.Vector3.TransformNormal(new B.Vector3(0, 0, -1), sock.getWorldMatrix()).normalize();
          scene.render(); scene.render();
          expect(inst.activeCount()).toBeGreaterThan(0);
          def.emitters.forEach((em: any, i: number) => {
            const ps = inst.systems[i]; const v = Array.isArray(em.speed_mps) ? em.speed_mps[0] : em.speed_mps;
            if (!(v > 0)) return;
            const cone = ((em.shape?.type === 'cone' ? em.shape.angle_deg : em.shape?.type === 'point' ? 0 : 90) + 8) * Math.PI / 180;
            for (const p of ps.particles) expect(B.Vector3.Dot(p.direction.normalizeToNew(), fwd)).toBeGreaterThanOrEqual(Math.cos(Math.min(Math.PI / 2 + 0.2, cone)) - 1e-3);
          });
          rt.dispose(); scene.dispose();
        });
      }

      if (def.loop) {
        it('loop: runtime inputs rescale it, stop() drains it into the pool, a followed node carries it', async () => {
          const scene = newScene(); const rt = R.createVfxRuntime(B, scene, { textures: false, lights: true }); await rt.prepare([def]);
          const node = new B.TransformNode('follow', scene); node.position.set(0, 1, 0);
          const inst = rt.spawnAtNode(def, node, { follow: true });
          for (let k = 0; k < 120; k++) scene.render();
          const full = inst.activeCount(); expect(full).toBeGreaterThan(0);
          // inputs at their minimum: every rate drive must not raise the count (data is authored at the worst case)
          const lo: any = {}; for (const [k, d] of Object.entries<any>(def.inputs ?? {})) if (typeof d.default === 'number') lo[k] = d.min;
          inst.setInputs(lo);
          node.position.set(5, 1, 0); scene.render();
          expect(B.Vector3.Distance(inst.position, new B.Vector3(5, 1, 0))).toBeLessThan(1e-5);
          for (let k = 0; k < 120; k++) scene.render();
          expect(inst.activeCount()).toBeLessThanOrEqual(Math.max(full, C.max_particles));
          inst.stop();
          const maxLife = Math.max(...def.emitters.map((em: any) => (Array.isArray(em.life_s) ? em.life_s[1] : em.life_s)));
          for (let k = 0; k < Math.ceil(maxLife * 60) + 60; k++) scene.render();
          expect(inst.alive).toBe(false); expect(rt.pooled(def.id)).toBeGreaterThanOrEqual(1);
          expect(inst.light ?? null).toBeNull();
          rt.dispose(); scene.dispose();
        });
      }

      if (def.hooks?.decal) {
        it(`decal hook ${def.hooks.decal.id} resolves through the engine DecalSystem`, () => {
          const scene = newScene();
          const sys = new DS.DecalSystem(scene, decals, { textures: false, capacity: 4 });
          expect(sys.has(def.hooks.decal.id)).toBe(true);
          const wall = B.MeshBuilder.CreateBox('wall', { width: 4, height: 3, depth: 0.3 }, scene);
          const d = sys.spawn(def.hooks.decal.id, { mesh: wall, point: new B.Vector3(0.2, 1.0, -0.15), normal: new B.Vector3(0, 0, -1) });
          expect(d).not.toBeNull(); expect(d.getTotalVertices()).toBeGreaterThan(0);
          if (def.hooks.surface && def.hooks.surface !== 'flesh') expect(DS.BULLET_HOLE[def.hooks.surface]).toBe(def.hooks.decal.id);
          sys.dispose(); scene.dispose();
        });
      }
    });
  }
});
