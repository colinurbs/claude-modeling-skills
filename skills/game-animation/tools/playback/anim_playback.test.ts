/**
 * Playback test for an exported animation set, run through the ENGINE's own code
 * (packages/engine/src/anim: ClipLibrary, retargetLibrary, evalClip, LocomotionBlend) with
 * Babylon's NullEngine, in the same way packages/engine/src/test/character.test.ts does.
 *
 * Driven by anim_cli.py playback, which copies this file into the set's _work dir and runs
 * vitest with env:
 *   GANIM_ENGINE  packages/engine dir      GANIM_GLB   exported set .glb
 *   GANIM_META    set .meta.json           GANIM_REF   _work/ref.json (build's FK reference)
 *   GANIM_BODY    a body .glb (char_civ_body_m_avg shares char_skeleton_ref's rest pose)
 */
import { describe, it, expect, beforeAll } from 'vitest';
import { readFileSync, realpathSync } from 'node:fs';
import { join } from 'node:path';

const env = (k: string): string => { const v = process.env[k]; if (!v) throw new Error(`${k} not set`); return v; };
const ENGINE = realpathSync(env('GANIM_ENGINE'));
const imp = async (p: string) => import(realpathSync(join(ENGINE, p)));

/* eslint-disable @typescript-eslint/no-explicit-any */
let B: any, clips: any, retarget: any, rigm: any, posem: any, loco: any;
let scene: any, lib: any, rig: any, rlib: any, rest: any;
const meta: any[] = JSON.parse(readFileSync(env('GANIM_META'), 'utf8'));
const ref: any = JSON.parse(readFileSync(env('GANIM_REF'), 'utf8'));
const FPS = 30;

beforeAll(async () => {
  const bjs = realpathSync(join(ENGINE, 'node_modules/@babylonjs/core'));
  B = await import(join(bjs, 'index.js'));
  clips = await imp('src/anim/clips.ts');
  retarget = await imp('src/anim/retarget.ts');
  rigm = await imp('src/character/rig.ts');
  posem = await imp('src/anim/pose.ts');
  loco = await imp('src/anim/locomotion.ts');
  scene = new B.Scene(new B.NullEngine());
  lib = new clips.ClipLibrary();
  await lib.addGlb(scene, env('GANIM_GLB'), meta);
  rig = await rigm.loadRig(scene, env('GANIM_BODY'));
  rlib = retarget.retargetLibrary(lib, rig);
  rest = posem.restPose(rig);
}, 120000);

describe('exported clips through the engine', () => {
  it('every clip in the meta resolves with matching flags and duration', () => {
    expect(lib.names().sort()).toEqual(meta.map((m) => m.clip).sort());
    for (const m of meta) {
      const c = lib.get(m.clip);
      expect(c.loop, `${m.clip} loop`).toBe(m.loop);
      expect(c.rootMotion, `${m.clip} root_motion`).toBe(m.root_motion);
      expect(c.additive, `${m.clip} additive`).toBe(m.additive);
      expect(c.base, `${m.clip} base`).toBe(m.base ?? null);
      expect(Math.abs(c.duration - m.duration_s), `${m.clip} duration ${c.duration} vs ${m.duration_s}`).toBeLessThan(1e-3);
      expect(c.tracks.size, `${m.clip} tracks`).toBeGreaterThan(20);
      if (m.additive) expect(c.basePose, `${m.clip} base pose resolved`).toBeTruthy();
    }
  });

  it('loops close: the pose just before the wrap equals frame 0 (retargeted onto the body)', () => {
    const a = new posem.Pose(), b = new posem.Pose();
    for (const m of meta.filter((x) => x.loop)) {
      const c = rlib.get(m.clip);
      clips.evalClip(c, 0, a, rest); clips.evalClip(c, c.duration - 1e-6, b, rest);
      let worstDot = 1, worstPos = 0;
      for (const i of c.tracks.keys()) {
        if (i === posem.ROOT) continue;
        const qa = a.getQ(i), qb = b.getQ(i);
        worstDot = Math.min(worstDot, Math.abs(qa.x * qb.x + qa.y * qb.y + qa.z * qb.z + qa.w * qb.w));
        worstPos = Math.max(worstPos, a.getP(i).subtract(b.getP(i)).length());
      }
      expect(worstDot, `${m.clip} rotation seam`).toBeGreaterThan(0.99995);
      expect(worstPos, `${m.clip} position seam`).toBeLessThan(0.002);
      expect(clips.frameAt(c, c.duration)).toBeCloseTo(c.from, 6);
    }
  });

  it('root deltas are sane: speed, direction, on the ground; in-place clips stay put', () => {
    const dirs: Record<string, [number, number]> = { f: [0, 1], fl: [1, 1], l: [1, 0], bl: [1, -1], b: [0, -1], br: [-1, -1], r: [-1, 0], fr: [-1, 1] };
    const e = new B.Vector3(), s = new B.Vector3();
    for (const m of meta) {
      const c = lib.get(m.clip);
      clips.rootEnd(c, e); clips.rootAt(c, 0, s);
      const d = e.subtract(s);
      if (m.root_motion) {
        const speed = Math.hypot(d.x, d.z) / c.duration;
        expect(Math.abs(speed - m.speed_mps) / Math.max(0.01, m.speed_mps), `${m.clip} speed ${speed}`).toBeLessThan(0.02);
        expect(speed, `${m.clip} human speed`).toBeLessThan(9);
        if (m.direction) {
          const [x, z] = dirs[m.direction]; const want = Math.atan2(x, z), got = Math.atan2(d.x, d.z);
          const diff = Math.abs(((got - want + Math.PI * 3) % (Math.PI * 2)) - Math.PI);
          expect(diff * 180 / Math.PI, `${m.clip} direction`).toBeLessThan(15);
        }
      } else {
        expect(d.length(), `${m.clip} root static`).toBeLessThan(0.01);
      }
      for (let k = 0; k <= Math.round(c.duration * FPS); k++) { clips.rootAt(c, k / FPS, s); expect(Math.abs(s.y), `${m.clip} root y`).toBeLessThan(0.001); }
    }
  });

  it('engine pose on the body reproduces the build reference (joint positions within 1 cm, root-relative)', () => {
    const pose = new posem.Pose();
    const inv = B.Matrix.Invert(rig.gltfRoot.getWorldMatrix());
    const worst: [number, string] = [0, ''];
    for (const m of meta) {
      const r = ref.clips[m.clip]; if (!r) continue;
      const c = rlib.get(m.clip);
      r.frames.forEach((k: number, fi: number) => {
        const t = c.loop && k === Math.round(c.duration * FPS) ? c.duration - 1e-6 : k / FPS;
        clips.evalClip(c, t, pose, rest);
        posem.applyPose(rig, pose, { keepRoot: true });
        rig.root.computeWorldMatrix(true);
        for (const n of rig.root.getDescendants(false)) n.computeWorldMatrix?.(true);
        const g = (b: string) => B.Vector3.TransformCoordinates(rig.node(b).getAbsolutePosition(), inv);
        const root = g('root');
        const rroot = r.pos.root[fi];
        for (const j of Object.keys(r.pos)) {
          const p = g(j).subtract(root); const w = r.pos[j][fi];
          const err = Math.hypot(p.x - (w[0] - rroot[0]), p.y - (w[1] - rroot[1]), p.z - (w[2] - rroot[2]));
          if (err > worst[0]) { worst[0] = err; worst[1] = `${m.clip} f${k} ${j}`; }
        }
      });
    }
    console.log(`engine-vs-build worst joint error ${(worst[0] * 100).toFixed(3)} cm at ${worst[1]}`);
    expect(worst[0], `worst ${worst[1]}`).toBeLessThan(0.01);
  });

  it('LocomotionBlend integrates walk root motion at the clip speed (driven at the walk band)', () => {
    const walk = meta.find((m) => /^anim_walk_f_loop$/.test(m.clip));
    if (!walk) return;
    // Drive at the engine's walk BAND speed, where the blend weight is walk_f alone, so this
    // checks root-motion integration (phase, wrap, deltas) and not the band mismatch: a clip
    // slower than the hardcoded band gets idle blended in when driven at its own speed (that
    // is validate's speed.band WARN and an engine ask; the figure is logged here).
    const band = (loco.BANDS as any[]).find((b) => b.key === 'walk')?.speed ?? walk.speed_mps;
    const run = (v: number) => { const lb = new loco.LocomotionBlend(rlib, rest); const t = new B.Vector3(); for (let i = 0; i < 120; i++) { lb.update(1 / 60, new B.Vector3(0, 0, v)); t.addInPlace(lb.rootMotionDelta); } return t; };
    const atClip = run(walk.speed_mps);
    console.log(`LocomotionBlend driven at the clip speed ${walk.speed_mps} m/s travels ${(atClip.length() / 2).toFixed(3)} m/s (band ${band} m/s)`);
    const total = run(band);
    expect(total.length()).toBeGreaterThan(2 * walk.speed_mps * 0.85);
    expect(total.length()).toBeLessThan(2 * walk.speed_mps * 1.15);
    expect(Math.abs(total.z)).toBeGreaterThan(Math.abs(total.x) * 5);
  });
});
