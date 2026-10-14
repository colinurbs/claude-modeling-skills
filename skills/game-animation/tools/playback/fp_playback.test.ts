/**
 * Playback test for a FIRST-PERSON set (or the hand-pose library), run through the ENGINE's own code
 * (ClipLibrary, retargetLibrary, evalClip, applyPose, loadRig) with Babylon's NullEngine.
 * Driven by anim_cli.py playback (animset mode first_person / hand_poses) with env:
 *   GANIM_ENGINE packages/engine   GANIM_GLB set .glb   GANIM_META set .meta.json   GANIM_REF _work/ref.json
 *   GANIM_BODY  the view-model arms (char_fp_arms_*) or a body on the shared skeleton   GANIM_MODE   GANIM_SPEC
 */
import { describe, it, expect, beforeAll } from 'vitest';
import { readFileSync, realpathSync, existsSync } from 'node:fs';
import { join } from 'node:path';

const env = (k: string): string => { const v = process.env[k]; if (!v) throw new Error(`${k} not set`); return v; };
const ENGINE = realpathSync(env('GANIM_ENGINE'));
const imp = async (p: string) => import(realpathSync(join(ENGINE, p)));

/* eslint-disable @typescript-eslint/no-explicit-any */
let B: any, clips: any, retarget: any, rigm: any, posem: any;
let scene: any, lib: any, rig: any, rlib: any, rest: any;
const MODE = process.env.GANIM_MODE ?? 'first_person';
const meta: any[] = JSON.parse(readFileSync(env('GANIM_META'), 'utf8'));
const ref: any = existsSync(env('GANIM_REF')) ? JSON.parse(readFileSync(env('GANIM_REF'), 'utf8')) : { clips: {} };
const spec: any = JSON.parse(readFileSync(env('GANIM_SPEC'), 'utf8'));
const FP = spec.animation.first_person;
const FPS = 30;
const FINGERS = ['thumb', 'index', 'middle', 'ring', 'pinky'].flatMap((f) => [1, 2, 3].map((k) => `${f}_0${k}`));
const MASK = MODE === 'hand_poses' ? ['l', 'r'].flatMap((s) => FINGERS.map((b) => `${b}_${s}`)) : (FP.keyed_bones as string[]);

beforeAll(async () => {
  B = await import(join(realpathSync(join(ENGINE, 'node_modules/@babylonjs/core')), 'index.js'));
  clips = await imp('src/anim/clips.ts');
  retarget = await imp('src/anim/retarget.ts');
  rigm = await imp('src/character/rig.ts');
  posem = await imp('src/anim/pose.ts');
  scene = new B.Scene(new B.NullEngine());
  lib = new clips.ClipLibrary();
  await lib.addGlb(scene, env('GANIM_GLB'), meta);
  rig = await rigm.loadRig(scene, env('GANIM_BODY'));
  rlib = retarget.retargetLibrary(lib, rig);
  rest = posem.restPose(rig);
}, 120000);

const inv = () => B.Matrix.Invert(rig.gltfRoot.getWorldMatrix());
function worldOf(bone: string): { p: any; q: any } {
  rig.root.computeWorldMatrix(true);
  for (const n of rig.root.getDescendants(false)) n.computeWorldMatrix?.(true);
  const m = rig.node(bone).getWorldMatrix().multiply(inv());
  const s = new B.Vector3(), q = new B.Quaternion(), p = new B.Vector3(); m.decompose(s, q, p);
  return { p, q };
}

describe(`first-person clips through the engine (${MODE})`, () => {
  it('every clip resolves with the meta flags, the duration, and ONLY the bone mask', () => {
    expect(lib.names().sort()).toEqual(meta.map((m) => m.clip).sort());
    const BONES = (rigm.BONES ?? null) as string[] | null;
    for (const m of meta) {
      const c = lib.get(m.clip);
      expect(c.loop, `${m.clip} loop`).toBe(m.loop);
      expect(c.rootMotion, `${m.clip} root_motion`).toBe(false);
      expect(c.additive, `${m.clip} additive`).toBe(false);
      expect(Math.abs(c.duration - m.duration_s), `${m.clip} duration`).toBeLessThan(1e-3);
      expect(c.tracks.size, `${m.clip} tracks`).toBe(MODE === 'hand_poses' ? MASK.length / 2 : MASK.length);   // pose clips key only their own side
      for (const [i, t] of c.tracks) {
        expect(t.position, `${m.clip} bone ${i} position track`).toBeUndefined();
        expect(t.rotation, `${m.clip} bone ${i} rotation track`).toBeTruthy();
      }
      void BONES;
    }
  });

  it('loops close on the view-model rig (retargeted)', () => {
    const a = new posem.Pose(), b = new posem.Pose();
    for (const m of meta.filter((x) => x.loop)) {
      const c = rlib.get(m.clip);
      clips.evalClip(c, 0, a, rest); clips.evalClip(c, c.duration - 1e-6, b, rest);
      let worst = 1;
      for (const i of c.tracks.keys()) { const qa = a.getQ(i), qb = b.getQ(i); worst = Math.min(worst, Math.abs(qa.x * qb.x + qa.y * qb.y + qa.z * qb.z + qa.w * qb.w)); }
      expect(worst, `${m.clip} rotation seam`).toBeGreaterThan(0.99995);
    }
  });

  it('the rig carries the contract eye and attach bones', () => {
    for (const b of ['eye_l', 'eye_r', 'attach_hand_r', 'attach_hand_l']) expect(rig.has(b), b).toBe(true);
    posem.applyPose(rig, rest, { keepRoot: true });
    const e = worldOf('eye_l').p.add(worldOf('eye_r').p).scale(0.5);
    const want = FP.eye.eye_m;
    const err = Math.hypot(e.x - want[0], e.y - want[1], e.z - want[2]);
    console.log(`rig eye midpoint ${[e.x, e.y, e.z].map((v: number) => v.toFixed(4))} vs contract ${want} (${(err * 1000).toFixed(1)} mm)`);
    expect(err).toBeLessThan(0.003);
  });

  it('engine pose reproduces the build: attach_hand_r / attach_hand_l frames within 1 cm and 1 deg', () => {
    if (MODE === 'hand_poses') return;
    const pose = new posem.Pose(); let worst = [0, 0, ''] as [number, number, string];
    for (const m of meta) {
      const r = ref.clips[m.clip]; if (!r) continue;
      const c = rlib.get(m.clip);
      r.frames.forEach((k: number, fi: number) => {
        const t = c.loop && k === Math.round(c.duration * FPS) ? c.duration - 1e-6 : k / FPS;
        clips.evalClip(c, t, pose, rest);
        posem.applyPose(rig, pose, { keepRoot: true });
        for (const [bone, key] of [['attach_hand_r', 'attach_r'], ['attach_hand_l', 'attach_l']] as const) {
          const w = worldOf(bone); const rp = r[key].p[fi], rq = r[key].q[fi];
          const e = Math.hypot(w.p.x - rp[0], w.p.y - rp[1], w.p.z - rp[2]);
          const dot = Math.min(1, Math.abs(w.q.x * rq[0] + w.q.y * rq[1] + w.q.z * rq[2] + w.q.w * rq[3]));
          const ang = 2 * Math.acos(dot) * 180 / Math.PI;
          if (e > worst[0] || ang > worst[1]) worst = [Math.max(e, worst[0]), Math.max(ang, worst[1]), `${m.clip} f${k} ${bone}`];
        }
      });
    }
    console.log(`engine-vs-build attach frames: worst ${(worst[0] * 100).toFixed(3)} cm, ${worst[1].toFixed(3)} deg (${worst[2]})`);
    expect(worst[0]).toBeLessThan(0.01);
    expect(worst[1]).toBeLessThan(1.0);
  });

  it('meta fp block is complete for the engine: grip, events in range, part curves per frame within ig_parts', () => {
    if (MODE === 'hand_poses') return;
    for (const m of meta) {
      const f = m.fp; expect(f, `${m.clip} fp`).toBeTruthy();
      expect(f.grip_r?.t?.length, `${m.clip} grip_r`).toBe(3);
      expect(f.fov_deg).toBe(FP.viewmodel_fov_deg);
      for (const e of f.events) { expect(e.frame).toBeGreaterThanOrEqual(0); expect(e.frame).toBeLessThan(m.frames); expect(Math.abs(e.t - e.frame / FPS)).toBeLessThan(1e-3); }
      const need = FP.events.required_item?.[f.item]?.[f.action] ?? FP.events.required[f.action] ?? [];
      for (const n of need) expect(f.events.map((e: any) => e.name), `${m.clip} needs ${n}`).toContain(n);
      for (const [p, v] of Object.entries<any>(f.parts ?? {})) {
        expect(v.values.length, `${m.clip} ${p} length`).toBe(m.frames);
        expect(Math.min(...v.values)).toBeGreaterThanOrEqual(-1e-4); expect(Math.max(...v.values)).toBeLessThanOrEqual(1 + 1e-4);
      }
      for (const w of f.carry ?? []) { expect(w.frames[0]).toBeLessThanOrEqual(w.frames[1]); expect(rig.has(w.attach)).toBe(true); }
    }
  });
});
