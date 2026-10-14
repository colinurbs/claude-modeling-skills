---
name: game-vfx
description: Build engine-ready game VFX (particle effects) as data + procedurally painted flipbook textures, and prove them in the target engine. Covers muzzle flashes, shell ejection, tracers, bullet impacts per material (dust, sparks, splinters, glass, blood), smoke (cigarette, chimney, barrel, tear-gas volume), fire, explosions, sabotage sparks, rain/breath, and screen effects. Pipeline: effect brief with intent, numpy-painted sprite sheets (smoke puffs, flashes, sparks, debris) as PNG, a portable `vfx_<name>.json` (emitters, curves, colours, blend, lighting, sound/decal/mesh/light hooks, LOD, budget) that maps onto Babylon ParticleSystems and Godot GPUParticles3D, automatic validation (schema, texture alpha and size, particle and overdraw budget, lifetimes, palette and HDR rules, required hooks), headless Babylon review (frame strips over time in context at the weapon socket or on a wall material, day and night, the Governor's CCTV view, measured particle count, overdraw, blow-out), an engine NullEngine playback test, claims and manifest. Use it for ANY game effects work, e.g. "make the muzzle flash", "impact effects per material", "tear gas smoke", "molotov fire", "explosion", "sparks when the camera is cut", "the vfx_ list in ASSETS.md §8", "particle budget", "why does my flash look like a fireball", even if the user just says "add an effect for X" inside a game project. Meshes belong to game-asset-modeling, animation clips and their events to game-animation; this skill consumes their sockets and events.
---

# game-vfx

A VFX asset is a **contract with the engine**, like a mesh or a clip. It is done when it:

1. **meets the delivery spec exactly**: an `ig_vfx` data file the engine's loader builds without guessing,
   textures that are power-of-two RGBA with clean alpha, inside the particle, overdraw and memory budgets,
   with every hook the gameplay code needs (event + socket, surface + decal + sfx, light);
2. **plays through the engine's own code**: spawned from the animation event at the weapon's socket or at
   a hit point along the surface normal, pooled, LOD-culled, retired at its duration (the playback test);
3. **reads in the game's look where players actually see it**: through the final grade (no bloom, no HDR
   blow-out, muted palette), at night under curfew light, and on the Governor's low-res CCTV feed. The
   project's style guide decides what "good" is (irongrip: ASSETS.md S3/S6/S9/S13: grounded, period, not
   Hollywood; blood desaturated and brief; fire is the only orange light).

Numbers are checked by scripts, the look is checked by renders: yourself first, then a fresh-eyes critic.
This is the effects counterpart of **game-asset-modeling** and **game-animation**, and it shares their spec
file, claims registry, manifest, review slots (one headless browser on the machine at a time) and Babylon
tooling. It imports their code; it does not copy it.

## Why data + painted sheets

- **Data (`export/vfx/vfx_<id>.json`, format `ig_vfx` v1)**: a small, engine-neutral subset of what every
  particle system has: emitters with bursts/rate windows, lifetime, speed, cone/sphere/disc shapes, gravity,
  drag, size and colour over life, flipbook frames, billboard mode, blend, plus hooks. Babylon's own
  serialised ParticleSystem JSON is Babylon-only and verbose; a NodeMaterial graph is unportable. `ig_vfx`
  maps 1:1 onto Babylon `ParticleSystem` (the reference runtime) and onto Godot `GPUParticles3D` +
  `ParticleProcessMaterial` (mapping table in `references/format.md`), and the validator can reason about it.
- **Textures (`export/vfx/tex/t_vfx_<sheet>_<e|bc>.png`)**: a handful of shared flipbook sheets painted by
  numpy painters (noise-warped smoke puffs, flash petals, spark streaks, debris chips). Deterministic, seconds
  to rebuild, no Blender run (memory-safe on a busy machine), and easy to restyle from one recipe file. Grey
  `_e` (emissive) and neutral smoke sheets are tinted by the data, so the palette lives in one place.
  The build pipeline converts PNG to KTX2 like mesh textures. Blender smoke sims are a later option for hero
  effects (explosion); they would render to the same sheet format.

## Setup

- game-asset-modeling installed next to this skill with its `tools/setup.sh` run (Babylon, Playwright,
  numpy, pillow, opencv). game-animation installed too (the review lock and sheet compositor are imported from it).
- The project spec `assets/spec.json` needs a `vfx` section (`references/spec-vfx.md`). irongrip has it.
- Claim the family: `vfx_cli.py claim` (default `vfx_*` → `vfx.owner`). Export `GAM_OWNER=<owner>` in every shell.

All commands are `python3 ~/.claude/skills/game-vfx/scripts/vfx_cli.py …` from inside the project.

## The per-effect loop

### 0. Brief: `vfx_cli.py new vfx_<name> --class <class>`
Scaffolds `assets/src/vfx/<id>/vfx.json` from `templates/vfx.json`. Write the **notes first, as intent**:
what the player must read, and what the Governor's CCTV must read ("a wall was hit, from across the street";
"a shot, but never the brightest thing on screen"). Then the physical facts: calibre, barrel length, material,
real sizes in metres, real durations (a muzzle flash lasts 2-4 frames; a dust haze lasts a second or two).
Classes, budgets and required hooks are in the spec (`vfx.classes`). Read `references/look.md` for the per-class
recipes and the grounded-vs-Hollywood checklist.

### 1. Sheets: `vfx_cli.py tex`
Paints every sheet in `assets/src/vfx/sheets.json` into `export/vfx/tex/` and writes the preview
`review/vfx_sheets.png` (over grey and over black). Reuse an existing sheet before adding one: frame ranges,
tints and sizes make one smoke sheet serve gun smoke, dust, blood mist and cloth puffs. A new look means a new
painter in `scripts/vfx_tex.py` (rules in its docstring: transparent cell borders, RGB bled under A=0,
greyscale for `_e`).

### 2. Build: `vfx_cli.py build <vfx.json>`
Resolves palette names to hex, attaches each sheet's path/size/grid, computes estimates (peak alive particles,
fill at a reference view, true end time) and writes `export/vfx/<id>.json`.

### 3. Validate: `vfx_cli.py validate <vfx.json>`. Gate: no FAIL
`scripts/validate_vfx.py` checks schema, texture sheets, budgets, loops, palette/HDR, hooks and LOD
(`references/validation.md`). Fix the cause, not the threshold. A threshold that is wrong for the project is a
spec change: make it in `assets/spec.json` with a note saying why.

### 4. Review: `vfx_cli.py review <vfx.json>`. Look, then run the critic
Headless Babylon (`tools/review/`), lit like the engine's overcast scene and graded like `post.ts`, using the
same runtime the playback test uses. Writes `review/<id>/` and the sheet `review/<id>.png`:
frame strips over time (day and night; first-person eye at the weapon's `socket_muzzle`, or 2.4 m from an impact
on the real wall material with its decal), a side view of the weapon, context frames (a muzzle close-up from
the eye, or the impact from 8 m), the Governor's CCTV feed (3.5 m mast, 9 m, 320×240, day and night),
an overdraw heat map (quad layers), and the alive-particle chart against the budget. `review.json` records
the measured peak particles, overdraw (peak layers, mean fill) and the **clipped** share of the effect's pixels
after the grade (blow-out = bloom-as-style). Gates: measured peak ≤ budget, fill ≤ budget, clipped ≤ spec.

Read the sheet yourself first. Then run the **fresh-eyes critic in the foreground** (prompt in
`references/review.md`) with the sheet, the per-view PNGs, the brief, validate.json and review.json. Fix the
biggest blocker/major, rebuild, re-review, until only minors remain.

### 5. Playback: `vfx_cli.py playback`. Gate: PASS
Copies `tools/playback/vfx_playback.test.ts` into `assets/src/vfx/_work/playback/` and runs the engine's
vitest with the engine's Babylon under NullEngine: every exported effect builds (one system per emitter, enough
capacity), plays to its duration within budget and returns its systems to the pool, the LOD cull and reduce work,
the event spawns at the socket and emits along its -Z, and the decal hook resolves through the engine's own
`DecalSystem`. Nothing is written into the engine.

### 6. Deliver
Set `status: done` in the brief, then run `vfx_cli.py manifest <vfx.json>`. `vfx_cli.py all` runs
build → validate → review → manifest. Report the effects, particles, textures, validate/critic results and the engine asks.

## The runtime contract (what the engine must do)

`tools/runtime/vfx_babylon.mjs` is the reference loader and the proposed `packages/engine/src/render/vfx.ts`.
The skill never edits engine code; it hands these over as asks (`references/engine-notes.md`):
- **prepare at level load** (`rt.prepare(defs)`): sheets loaded and shaders compiled, warm systems pooled.
  Babylon particle systems skip emission until texture and shader are ready, which swallows a 2-frame muzzle
  flash on first use. **Pool** retired systems per effect (a new system needs 2 frames to become ready).
- **Event dispatch**: `meta.fp.events` crossings → `rt.fire(event, sockets)` at the held item's socket nodes
  (`attach.event`/`attach.socket`, plus `attach.also`, e.g. `fire_2` → `socket_muzzle_2`).
- **Impacts**: `rt.spawnAtHit(def, point, normal)` + the `hooks.decal` via `DecalSystem.spawn` (`place:
  surface | ground_below`) + `hooks.sfx`; surface from `ig_material`.
- **Lights**: `hooks.light` is a short PointLight (tungsten); budget it (`vfx.scene_budget.max_lights_from_vfx`).
- **LOD**: pass `distance` (camera → spawn); beyond `cull_m` skip, beyond `reduce_m` scale counts.
- **Time**: systems run at `updateSpeed = 1/60`, so all data is in seconds/metres at the scene's animation ratio.
- **Gameplay-driven loops** (gas, fire, chimney): `inputs` + per-emitter `drive` (data authored at the worst case), `inst.setInputs/
  setDistance/moveTo/stop`, `follow` for thrown items, looping flicker lights; review them with `review.context: "ground"`
  (format.md, review.md, engine-notes.md asks 14-18).

## Style bar (irongrip; read the project's own guide elsewhere)

- **Grounded, period, not Hollywood.** Real sizes, real durations. A pistol flash is a 10 cm, 2-frame warm
  blink; most of what a shot leaves is a thin grey wisp. No fireballs, starburst logos, lens flares, glow halos.
- **Restrained (S1/S9).** LDR colours only (no HDR multipliers, no "intensity"), additive only for things that
  emit light, no bloom-as-style. The clipped-pixels gate in review enforces it.
- **Palette (S3).** Smoke and dust are greys/browns (sat ≤ 0.25), debris within the palette, fire/flash keys
  tungsten-ish (hue 18-55°, sat ≤ 0.55) and never the ministry-ochre accent; blood is dried brown-red, dark,
  small and brief (S13: no gore as spectacle).
- **Read on the feed.** The Governor sees through 320×240 CCTV at 12 fps: impacts should register as a puff;
  a 2-frame flash will usually be missed there, which is honest (gameplay gets `shots_fired` from the sim).

## Files

| Path | What |
|---|---|
| `scripts/vfx_cli.py` | claim, new, tex, build, validate, review, playback, manifest, all, status |
| `scripts/vfxlib.py` | paths, build (brief → export), estimates (alive, fill, end), colour helpers |
| `scripts/vfx_tex.py` | numpy/cv2 sheet painters: flash_front, flash_side, smoke_puff, spark_streak, debris |
| `scripts/validate_vfx.py` | validator (uses game-asset-modeling's `Report`) |
| `tools/runtime/vfx_babylon.mjs` | reference runtime: ig_vfx → Babylon ParticleSystems, prepare/pool, events, hits, lights, LOD |
| `tools/review/harness_vfx.html`, `shoot_vfx.mjs` | headless review (engine-matched light, grade, CCTV) |
| `tools/playback/vfx_playback.test.ts` | engine playback test (vitest, NullEngine, engine DecalSystem) |
| `templates/vfx.json` | brief templates per class |
| `references/format.md` | the `ig_vfx` schema field by field, Babylon and Godot mappings |
| `references/spec-vfx.md` | the spec section, how to add it to a new project |
| `references/look.md` | per-class recipes, real-world numbers, grounded-vs-Hollywood checklist |
| `references/validation.md` | every check and threshold |
| `references/review.md` | reading the sheet, the critic prompt |
| `references/engine-notes.md` | Babylon particle behaviour the runtime depends on, open engine asks |
| `references/shader.md`, `references/shaders/*.glsl` | class `shader` (screens, feeds, wet surfaces): data, porting, reference snippets |
| `scripts/vfx_shader_review.py`, `tools/review/shoot_shader.mjs` | class `shader` review: renders the reference snippet itself in headless WebGL |
