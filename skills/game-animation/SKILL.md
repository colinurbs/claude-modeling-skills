---
name: game-animation
description: Turn open motion capture (BVH, FBX, glTF: CMU, Mixamo, Quaternius, 100STYLE...) into engine-ready game animation clips on the project's shared skeleton: clip-set brief, headless Blender import and retarget (mapping tables, A-pose rest alignment, relaxed hands), 30 fps, root motion onto root, seamless loops, foot-slide and ground cleanup, 8-direction naming, additive clips, glb + meta export, automatic validation (loop seams, slide, penetration, joint limits, interpenetration, end poses, speed), Babylon review renders on the real bodies incl. the Governor's CCTV view, an engine playback test, claims and manifest. Use it for ANY game animation work: "retarget this mocap", "make the walk cycle", "loop this", "fix foot sliding", "extract root motion", "build the anim set", idles, hit reactions, additive lean, keyed first-person view-model clips (weapon draw/fire/reload with moving parts and events, hand poses), an ASSETS.md §6 row or anim_ manifest entry, even if the user just says "animate X" in a game project. Meshes and rigs belong to game-asset-modeling.
---

# game-animation

An animation clip is a **contract with the engine**, like a mesh. It is done when it:

1. **meets the delivery spec exactly**: 30 fps, root motion on `root` (never left on `hips`),
   loops whose last frame is the first with no velocity pop, names the engine looks up,
   only skeleton bones, no scale keys, a `.meta.json` that matches the file;
2. **plays through the engine's own code without surprises**: `ClipLibrary` resolves it,
   `retarget.ts` maps it onto every body's rolled bone frames, root deltas integrate at
   the speed the locomotion blend expects;
3. **reads as a human where players actually see it**: under the Governor's surveillance
   camera, at 3.5 m up and 9 m away, low-res. Feet plant, weight shifts, nobody moves like
   a game character (ASSETS.md S8).

Numbers are checked by scripts, the look is checked by engine renders — yourself first,
then a fresh-eyes critic. This is the animation counterpart of **game-asset-modeling**: it
shares its spec file, claims registry, manifest, Slot locks, glb reader and review tooling.

## Setup

- game-asset-modeling must be installed next to this skill (`~/.claude/skills/`) with its
  `tools/setup.sh` run: this skill imports its `validate_glb.py` and `gam_cli.py` and uses its
  node_modules (Babylon, Playwright, Khronos validator). Blender 4.2+ (tested 5.1), python3
  with numpy + pillow, ffmpeg for optional video.
- **Project spec**: `<project>/assets/spec.json` needs an `animation` section, transcribed
  from the delivery doc. For irongrip it is already there (from ASSETS.md §0.8/§0.7/§0.4/§6/S8).
  For a new project follow `references/spec-animation.md`. The document wins on conflict.
- **Claims**: `anim_cli.py claim` (default `anim_*` → `animation.owner`). Export
  `GAM_OWNER=<owner>` in every shell; `new`/`build` respect other owners' patterns.

All commands below are `python3 ~/.claude/skills/game-animation/scripts/anim_cli.py …`,
run from inside the project (it finds `assets/spec.json` upward).

## The per-set loop

### 0. Brief: `anim_cli.py new anim_<set>`
Scaffolds `assets/src/anim/<set>/animset.json` (+ `_sources/`). One set = one glb
(`anim_loco`, `anim_civ_life`, …). Fill it before touching data:

- **sources**: per take — `file`, `mapping` (`cmu|mixamo|ue_mannequin|100style|auto` or a
  path), `rest_frame` (`0` for takes that start with a T-pose, `"bind"` otherwise), `kind`
  (`mocap|mixamo|hand`), `credit`, `url`, `license`, `acquired`. Licence first: CC0, CC-BY,
  purchased, Mixamo only (ASSETS.md §0.12). No CC-BY-NC (LaFAN1, many academic sets), no
  game rips. Anything else goes to the user before use.
- **clips**: `name`, `source`, a range (`frames` in *source* frames, `seconds`, or
  `range: "auto"` + `window_s` + `period_s` for loops), `loop`, `root_motion`, `root_yaw`
  (`constant` straight locomotion/strafes, `facing` turns, `none`), `direction` (`f fl l bl b
  br r fr`), `speed_band` (`walk|jog|run`), `tags`, `additive` + `base`, and **notes = intent**:
  what the Governor's camera must read ("unhurried, going somewhere", "fear: fast and total").
  The source/licence attribution is appended to the meta notes automatically.

### 1. Pick clips from long takes: `anim_cli.py scan <take>`
Prints the bone list, which mapping table covers it, a per-second table (root speed, turn
rate, hips height, % planted per foot) and the best loop windows for walk, jog/run and idle
periods, in source frames. Read it like an editor's log: start/stop/turn sections show as
speed and turn-rate changes, planted % near 50/50 means steady gait, both feet ~100% means
standing. Choose:
- **loops** where speed and turn rate are steady for at least 3 cycles — the crossfade needs
  ~8 frames *before* the loop start, so never pick the first cycle of a take;
- **one-shots** with ~0.5 s of lead-in and tail kept (they are trimmed exactly, but the root
  smoother and foot lock use a 1 s margin);
- **idles** 4–8 s where the planted % stays ~100% for both feet;
- cost < ~5 cm on `range: auto` is a clean cycle; > 10 cm means the gait changes inside the
  window — narrow `window_s`.

### 2–5. Build: `anim_cli.py build animset.json [clip …]`
Per source, headless Blender (`scripts/blender_import.py`) imports the take, samples every
bone in world space, **retargets inside Blender** with `animlib.retarget` and saves
`_work/<source>.retarget.blend` (the raw retarget keyed on the real `char_skeleton_ref`
armature, for inspection or hand polish). Cached by input hash. Then in CPython:

- **resample** to 30 fps (slerp), **foot pitch** calibration (mocap T-poses rarely hold the
  feet flat: the median planted ankle→ball pitch is matched to the rest pitch), **ground**
  calibration from planted feet (source ground planes rarely match);
- per clip with a 1 s margin: **root motion** (smoothed ground-projected hips + yaw onto
  `root`, hips re-expressed relative to it), **foot lock** (planted heel/ball pinned at the
  span mean, 2-bone leg IK with a forward knee pole; FK output only — no IK is baked),
  **ground clamp**, then **loop** (crossfade the last 8 frames toward the frames that
  precede the loop start → last == first with matching velocity; then foot lock again on a
  3× tiling so the seam stays exact) or **trim**; start at the origin facing +Z; in-place
  clips re-centred; **additive** clips re-based on the base clip's first frame.
- export `assets/export/anim/<set>.glb` (one animation per clip, skeleton nodes only, bone
  frames per `animation.engine.bone_frames`) + `<set>.meta.json` (array, one entry per clip:
  clip, fps, loop, root_motion, additive, base, tags, source, notes + frames, duration_s,
  speed_mps, direction, source_file, source_frames, license), `_work/build_report.json`
  (ranges, cycle costs, foot-lock offsets) and `_work/ref.json` (FK reference for playback).

**Per-source / per-clip options added in the irongrip pilot** (all optional):
- source `rest_frame: "neutral"`: the take's frame closest to the target rest pose is the retarget
  rest. Use it when the bind pose is not a T-pose (ACCAD Male1/Male2: shoulders/neck got 130-160 deg
  of twist from `"bind"`). Source `ground: "plane"`: fit a sloped capture floor (ACCAD).
- source `format: "anim_glb"`: a delivered engine-convention glb (placeholder, authored stubs);
  clips carried as-is via `source_clip`; `additive` clips re-based from `source_base` onto this
  set's base; `cleanup: true` runs foot lock + ground clamp on a carried clip.
- clip `align_direction: true`: rotate the whole motion so net travel is on the named axis (strafes
  whose performer drifted); `level_head: true`: remove the mean head pitch (performer watched the
  feet); `ground_clip: true`: re-ground on the clip's own planted frames.
- clip `level_torso: {"hips": 0, "spine": 1.5, "chest": 13.7, "upper_chest": 4.2}`: constant per-bone pitch
  correction to those mean targets (deg, the anim_loco neutral idle), legs and upper arms keep their world
  orientation (`level_torso_keep`). For performers leaning back under raised arms (`pose.torso_lean_back`).
- clip `separate_arms: true` (or a list of torso capsules, e.g. `["spine", "pelvis", "upper_leg_l", "upper_leg_r"]`
  for sits): forearms out of the torso on bodies wider than the performer (arm swivel about
  shoulder-wrist keeps hand contacts, then a small wrist push). `forearm_twist: 0.5`: move half the
  wrist twist onto lower_arm (CMU forearms have no twist channel: candy-wrapper wrists otherwise).
- clip `arm_pose: {hand_sep_m, elbow_deg, forward_deg, pole, palm, fingers, shrug_max_deg, sway_m, breath_m,
  tremor_*}` (`scripts/arm_pose.py`): keyed arm chains + finger pose (library name or overrides) over the mocap
  legs/torso, IK per frame on the frame's own upper chest, loop-periodic sway/breath/tremor; the build report
  logs the measured separation/elbow/forward angle. Use when the performer's arms read wrong (anim_civ_hands_up_loop:
  "stretching" -> palms-apart surrender). The library's open_palm thumb sits ~40 deg off the index and ~35 deg
  out of the palm plane; `{"thumb": {"flex": [-10, 4, 4], "spread": -3, "oppose": -15}}` brings it to ~30/15. (Since 2026-10-04 the library's open_palm thumb itself is ~34 deg off the index and ~10 deg out of the palm plane; the override is no longer needed.)
  One arm only: `sides: "l"`; `hand_dir` sets the hand's long axis (default: fingers up); `weight_keys: [[t_s, w], ...]`
  blends the keyed arm over the mocap arm per frame (raise-hold-lower one-shots). anim_civ_life uses it for the
  smoker's free hand (relaxed hang) and the watch check (wrist to the chest, watch face to the eyes).
- clip `hook: "tools/x.py:fn"` (relative to the set dir) + any extra clip keys it reads: a project-side fix,
  `fn(skel, clip, clip_cfg, P) -> (clip, report)`, run after loop/trim and before arm_pose. anim_civ_life's
  `tools/civ_fixes.py` has reusable ops: `trunk_fold` (soft-limit a stool sit's trunk fold), `seat` (re-target the
  seat height, legs IK'd), `plant_foot`, `recenter` (side lean), `damp_arms`, `look_keys`, `head_pitch`.
- **Diagonals** (`blend: {a, b, w: "auto", mode: "amplitude", phase_side: "search"}`): offline
  synthesis from two built loops of the set (walk_f + walk_l -> walk_fl). Both are time-normalised,
  phase-aligned (`search` tries 48 offsets and keeps the raw blend with the least foot slide, both
  feet planting and shins never crossing; `l`/`r` = plant of that foot; `contacts` = best contact
  agreement), blended per bone and foot-locked on a loop tiling. `amplitude` keeps each input's
  cyclic motion at gain (1-w)g / w g with g = 1/|(1-w, w)| so the diagonal keeps the inputs' speed;
  `linear` is a convex blend (≈0.7x speed, like the engine's own runtime blend). `w: "auto"` picks
  the weight that puts travel at 45 deg. Validate them like any clip.

### 6. Validate: `anim_cli.py validate animset.json` — gate: no FAIL
`scripts/validate_anim.py` (PASS/WARN/FAIL/INFO like validate_glb; details in
`references/validation.md`). Fix the cause, not the threshold; a WARN needs a sentence in the
clip notes if you keep it.

### 7. Playback through the engine: `anim_cli.py playback animset.json` — gate: PASS
Copies `tools/playback/anim_playback.test.ts` into `_work/playback/` and runs the project's
vitest against `packages/engine` (`--engine` to override): ClipLibrary loads the glb with the
meta, every clip resolves with the right loop/root_motion/additive/base/duration, loops close
after `retargetLibrary` onto a real body, root deltas match speed and direction, the engine's
pose on `char_civ_body_m_avg` reproduces the build's FK within 1 cm, and `LocomotionBlend`
integrates the walk at the clip speed. Nothing is written into the engine.

### 8. Review: `anim_cli.py review animset.json [--video] [--bodies m_avg]` — look, then critic
Babylon + Playwright, same harness family as game-asset-modeling, on the real bodies with
head and outfit from `animation.review.bodies`. Per clip, `assets/review/<set>/<clip>.png`:
in-place frame strip per body, side onion-skin with root motion, top-down root path with
foot-contact discs (red = that step slid past the WARN threshold), the Governor's CCTV feed
(4 frames), and a feet chart (heel/ball height, contact bars, root speed). `--video` adds an
MP4 and GIF per body. `assets/review/<set>.png` is the set sheet (DoD screenshot). The review
also re-measures joint positions as Babylon posed them against the build (should be 0.00 cm).

**One headless render at a time**: review holds *every* review slot on the machine (shared
with game-asset-modeling), loads one body per page and closes it. Never launch the game
client to check animation; never run two reviews in parallel.

Read the sheet yourself, then spawn the **fresh-eyes critic** (prompt in `references/review.md`)
with the sheet, per-view PNGs, the animset, validate.json and the S8 excerpt. Fix the biggest
blocker/major, rebuild, re-review, until only minors remain.

### 9. Deliver
Set `status: done`, `anim_cli.py all animset.json` (build → validate → playback → review →
manifest; stops at the first failure), `anim_cli.py licenses animset.json --write` for the
LICENSES.md rows, then the project's own asset check (`pnpm validate-assets`). Report: clips,
durations, speeds, validate result, playback result, sheet path, and any spec/engine question.

## First-person (view-model) mode — keyed, not captured

ASSETS.md-style "Make" rows (weapon draw/idle/fire/reload/holster/melee, hands, papers) have no open
mocap, so they are keyed like HL2's view models. Same loop (brief → build → validate → playback →
review → critic → manifest), `"mode": "first_person"` in the animset; `anim_cli.py` dispatches every
stage on it. Read `references/first-person.md` first — it is the contract (also in spec
`animation.first_person`): arm chains only (38 bones, rotations), camera frame at the eye midpoint E0,
view-model FOV 54° vertical in its own layer, item under `attach_hand_r` at the set's `grip_r`,
support hand on `socket_hand_l` (fallback grip), moving parts as normalised curves in meta, events in meta.

- **Brief**: named `poses` (item `pos`/`rot` in camera space `[right, up, forward]` / `[yaw, pitch, roll]`,
  `hand_r` fingers + fit, `hand_l` in a space: `camera | item | part:<p> | socket:<s> | rest`), reusable
  `grips`, per-clip `keys` (frame, pose, ease, overrides), `parts` curves, `visible`, `carry` windows,
  `events`, `recoil`/`settle`/`breath` secondary motion, `frame_policy`, `state_in`. `grip_r: {"auto": ...}`
  solves the strong-hand grip on the item (`fplib.suggest_grip`, cached in `_work/grip_r.json`).
- **Build** (`fp_build.py`): per frame interpolate keys → secondary → support-hand target → finger grip
  fit per key pose (wrap onto the item's SDF and the other hand) → arm IK (clavicle share, pole, forearm
  twist) → glb keyed on the mask only + meta `fp` block + `_work/fp_ref.json`.
- **Finger poses**: `references/fp_hand_poses.json` (anatomical angles, retargetable, names = the
  weapons' `ig_grip_pose`). `"mode": "hand_poses"` exports the library as `anim_hand_poses` for third person.
- **Validate** (`validate_fp.py`): in frame at 4:3, near plane, reach / hand on grip, fingers vs item,
  hand vs hand, loops, events, parts within `ig_parts`, item speed. Thresholds in the spec.
- **Review** (`fp_review.py` + `harness_fp.html`): the eye view at 54° with events and the 4:3 frame, grip
  close-ups, outside view, world-FOV comparison, timing chart, mp4/gif (`--video`). Uses
  `char_fp_arms_bare_m_a` when delivered, else the m_avg body's arm triangles. One render at a time.
- **Playback** (`fp_playback.test.ts`): engine ClipLibrary/retarget/applyPose on the arms asset; mask-only
  tracks, loops, the rig's eye vs the contract, attach frames vs the build (1 cm / 1°), meta completeness.
- Critic prompt: as `references/review.md`, plus: "judge from the eye strip first: does the gun read as
  held (fingers wrapped, support hand working), is the motion weighty (S8), do parts move on the right
  frames (timing chart), any pops or hands through metal?"

## Style bar (ASSETS.md S8) — what the renders must show

The Governor watches civilians through cameras; the rebel is hidden among them. Civilian
motion is where "human among bots" is won, so the idles and the walk matter most.
- **Weight and restraint.** Feet plant (slide < 2 cm/step), turns take a step, idles are
  small and purposeful (a cough, a weight shift, a glance at a watch) — never a looping sway.
- **Civilians: purpose without urgency** (walk ~1.2–1.4 m/s, going somewhere; pauses have a
  reason). **Police: entitlement** — slower, wider, never stepping aside. **Rebels move like
  civilians**: same set; no tell may come from animation data.
- **Fear is physical** (hands up fast and total, cowering small, fleeing clumsy).
  **Violence is heavy** (big hit reactions, slow recovery, deaths are not flourishes and end
  in a pose the ragdoll can take over).
- Judge in the CCTV panel first: at 320×240 from the mast, does it read as a person with
  somewhere to be? Then the strips for posture and hands, then side/top for plant and path.

## Common failure modes (all have a check or a fix in the pipeline)

| Symptom | Cause | Where it's caught / fixed |
|---|---|---|
| Feet skate | root smoothing vs real step timing, proportions differ from the actor | foot lock; `feet.slide_per_step`; red discs in top view |
| Character floats or sinks | source ground ≠ 0, or T-pose frame at a different height | ground calibration; `feet.grounded`, `feet.ground_penetration` |
| Toes up/down through the whole stance | source T-pose feet not flat (rest mismatch) | foot pitch calibration (`foot_pitch: stance`) |
| Hip pop at the loop | cut without crossfade, cycle cost high | crossfade + relock; `loop.seam_velocity`, `loop.root_velocity` |
| Walk leaves displacement on hips / drifts off root | `root_motion` false on travelling data, or root yaw mode wrong | `root_motion.*` checks |
| Arms through the torso / legs crossing | proportions (female body is shorter), rest mismatch in shoulders | `body.interpenetration` (ragdoll capsules) |
| Gaze tilted up/down | neck posture of the source actor inherited by the head | head keeps its world orientation (`world_rest`) |
| Knees snap backwards | hyperextended source + IK pole from the current knee | forward knee pole; `joints.limits` hinge check |
| Twisted limbs / candy-wrapper wrists | wrong mapping, rolled bone frames converted twice | mapping review in `scan`; `bones.frame_convention` |
| Clip plays nowhere | name not what `LocomotionBlend`/`layers.play` look up | `name.engine_lookup` WARN; playback test |

**Rolled bone frames.** The MakeHuman-derived bodies carry rolled bone frames (root is −90°
about X, every bone's rest rotation differs). The engine's `retarget.ts` assumes clips are
authored in *world-aligned* frames (every rest rotation identity) and converts them per rig:
`q_rig = A_parent⁻¹ · q_clip · A_bone`, hips offsets scaled by rig hips height / 0.95. This
skill exports exactly that convention (`animation.engine.bone_frames: world_aligned`) and
encodes hips against the engine's assumed 0.95 m rest. Exporting in the skeleton's own
frames would be converted twice and twist every limb. See `references/retarget.md`.

## Files

| Path | What |
|---|---|
| `scripts/anim_cli.py` | claim, new, scan, build, validate, playback, review, manifest, licenses, all, status |
| `scripts/animlib.py` | numpy core (CPython and Blender): quaternions, Skeleton, canonical Clip + FK, retarget, resample, root extraction, loops, contacts, foot lock/IK, foot pitch + ground calibration, additive rebase, engine hips encoding, glb writer/reader |
| `scripts/blender_import.py` | headless Blender: import BVH/FBX/glTF, sample, retarget, save the inspection .blend |
| `scripts/anim_build.py` | per-set build orchestration (cache, per-clip cleanup, export, reports) |
| `scripts/validate_anim.py` | validator |
| `tools/review/shoot_anim.mjs`, `harness_anim.html` | Babylon review (engine-identical clip conversion) |
| `tools/playback/anim_playback.test.ts` | engine playback test (vitest, NullEngine) |
| `templates/animset.json` | brief template |
| `references/spec-animation.md` | the spec transcription (§0.8 etc.), field reference, contradictions found |
| `references/mappings/*.json` | bone maps: cmu, mixamo, ue_mannequin (Quaternius UAL), 100style, accad |
| `references/joint_limits.json` | per-bone limit table |
| `references/retarget.md` | retarget math, rest alignment, frame conventions, adding a mapping table |
| `references/validation.md` | what every check measures and its thresholds |
| `references/review.md` | reading the sheet, the critic prompt |
| `references/engine-notes.md` | engine behaviour the clips depend on, and open engine asks |
| `scripts/fplib.py`, `fp_build.py`, `validate_fp.py`, `fp_review.py` | first-person mode: core (IK, hand model, item SDF, grip solve, secondary), build, checks, review |
| `tools/review/harness_fp.html`, `shoot_fp.mjs`, `tools/playback/fp_playback.test.ts` | first-person review harness and engine playback test |
| `references/first-person.md`, `references/fp_hand_poses.json` | the first-person contract + brief format; the finger-pose library |
