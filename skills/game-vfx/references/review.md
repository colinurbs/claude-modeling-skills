# Review: reading the sheet, running the critic

`vfx_cli.py review` renders in Babylon headless (SwiftShader), lit like `packages/engine/src/render/engine.ts`
createScene, graded like `post.ts` applyGrade (grain off for stable frames), with the skill's runtime (the same one the
playback test runs). One browser at a time on the machine (all review slots, shared with game-asset-modeling and
game-animation). Never launch the game client to check an effect.

## The sheet `review/<id>.png` (per-view PNGs in `review/<id>/`)
| Panel | Camera | Look for |
|---|---|---|
| strip_day | weapons: first-person eye, view-model FOV 54, weapon at its `ig_fp_offset`, effect at `attach.socket`; impacts: 2.4 m oblique, real wall material with the decal | timing (flash 2-3 frames), size vs the weapon/wall, palette, smoke behaviour, does it end cleanly |
| strip_night | same, curfew light (hemi 0.1, fluorescent floodlight), lit emitters at `night.ambient` | emissive things glow, lit things darken; the flash light on hand and wall; no blow-out |
| side (weapons) | 0.9 m beside the muzzle | plume shape and direction, scale vs the gun |
| context_day/night | weapons: eye view zoomed to FOV 13 on the muzzle; impacts: 8 m at world FOV 75 | sprite quality (soft edges, no square quads), how an impact reads at engagement range |
| cctv | 3.5 m mast, 9 m, 320×240, greyscale-green, scanlines | does the Governor see it: impacts should register as a puff, flashes may not |
| overdraw | effect only, white quads, 1/64 per layer | peak layers, mean fill; pale = 16+ layers |
| counts | measured alive particles per frame vs the estimate and the budget | spikes, effects that never end |

`review.json`: engine console errors (must be none), `measure` (peak_particles, overdraw_peak, overdraw_fill,
overdraw_samples, clipped per mode/time, clipped_pct = share of the effect's pixels it drove from a mid-tone (base luma ≤ `grade_check.mid_luma`) to white; raw_pct also counts pixels that were already near white), `counts`, `gates` (empty = pass).

**Known harness simplifications** (say so if they matter): the view model and the world share one camera
(engine: view-model layer at 54°, world at 75°); grain is off; the CCTV renders every frame (engine: 12 fps,
so 2-frame flashes are often skipped); impacts use the normal as the emission axis (no ricochet reflection).

## Critic subagent (run in the FOREGROUND, read-only)
Spawn a separate agent with no stake in finishing. Give it paths, not your opinion:

```
You are a fresh-eyes VFX critic for <project>. Find faults; do not praise. Read-only: do not edit files and do not
launch any browser or 3D app; look at the PNGs with the Read tool.
Effect sheet: <assets>/review/<id>.png, and the per-view PNGs in <assets>/review/<id>/ (strip_day, strip_night,
side, context_day, context_night, cctv, overdraw, counts). Measurements: review/<id>/review.json.
Brief with intent notes: <src/vfx/<id>/vfx.json>; built data: <export/vfx/<id>.json>; validator: <_work/validate.json>.
Texture sheets preview: <assets>/review/vfx_sheets.png.
Style bar: <paste S1 Restrained, S3 palette + rules, S6 fire/light, S9 rendering, S13 never>.
Judge against the intent notes and real-world reference (sizes in metres, durations in frames at 60 Hz).
For each problem: panel, frame/time, what is wrong, how far off (size, frames, colour, alpha, % of screen), the likely
cause (painter, data field, harness, runtime), and the fix. Classify blocker / major / minor and rank by harm to:
(1) grounded period look (no Hollywood, no blow-out, palette), (2) readability for the player, (3) readability on the
Governor's CCTV, (4) performance. Check: flash duration and size, flash shape from the eye AND from the side,
smoke density and lifetime, debris scale and fall, sprite edges (hard quads, square cut-offs), flipbook popping,
night vs day behaviour, decal pairing, anything brighter or more saturated than the style allows, blood restraint.
"No issues" only if you checked.
```

Verify each claim against numbers before acting (review.json, the data, a crop of the PNG). Fix the largest
blocker/major, rebuild, re-review. Done = validate PASS, review gates empty, playback PASS, critic reports only minors
(or items owned elsewhere, e.g. a casing mesh → game-asset-modeling, ricochet direction → engine).

**Volumes and props (`review.context: "ground"`)**: the effect at `point` along `normal` (default up), an optional `prop`
manifest glb (burning drum, chimney stack, body), and cameras from `views.{strip,context,cctv,overdraw}` (`pos`, `target`, `fov`).
`inputs` are passed to the spawn; `times`, `context_times`, `cctv_times`, `clip_times`, `overdraw_times` sample the settled loop
instead of its first frames. Put `views.overdraw` INSIDE a gas volume: that is its worst case. Loops are disposed between sequences.

`review.side_focus_m` (weapons, round 4): how far along the weapon's -Z the side view centres (default 0.35 m); a long gun's muzzle
sits off-frame at the default (vfx_muzzle_rifle uses 0.85).

Round 6 (`context: "ground"`): `review.anim` {set (anim glb id), clip, start_s, inputs {name: [[clip_t, value], ...]}} poses
the `prop` body with a real clip through the engine's retarget math, advancing 1/60 s per render; `review.attach_node`
(e.g. `attach_hand_r`) spawns the effect on that bone with `follow`, so a cigarette strand rides the smoking hand;
`review.held` {length_m, diameter_m, from_m, color} draws a stand-in stick along the bone +Y (the held item's forward);
the `inputs` schedule stands in for a missing animation event (a drag). `review.inputs_schedule` {name: [[t, value], ...]} without an anim
steps runtime inputs on each spawn's clock (exhaust: idle, a pull-away at full throttle, idle again). `views.cctv_far` (+ `cctv_far_label`) adds a day
CCTV row from a farther mast (fire at ~20 m).
