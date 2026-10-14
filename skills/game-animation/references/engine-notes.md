# Engine behaviour the clips depend on (packages/engine/src/anim, character) and open asks

## What the exported files rely on
- `ClipLibrary.addGlb(scene, glb, meta)`: animation groups keyed by node name → spec bone;
  `loop` = meta.loop ?? name ends `_loop`; `additive` = meta ?? name contains `_add_`;
  `rootMotion` = meta ?? (not additive and root has a position track); `base` from meta.
- `frameAt` wraps `u` over `to − from`, `rootEnd` reads the root at `to`: loops must have
  last frame == first frame with the root one full cycle ahead (what the build writes).
- `retargetLibrary(lib, rig)`: clips are world-aligned; converted per rig family (see retarget.md).
- `evalAdditive`: delta = base_frame0⁻¹ · q; base = frame 0 of the named base clip (same library).
- `LocomotionBlend`: looks up `anim_idle_a_loop` and `anim_{walk|jog|run}_{f|fr|r|br|b|bl|l|fl}_loop`,
  speed bands hardcoded 0 / 1.3 / 2.5 / 4.5 m/s, phase-synchronised by clip duration.
- `AnimatedCharacter` / `AnimLayers` play `anim_hands_up`, `anim_flinch`, `anim_death_fwd`,
  `anim_add_aim` (u 0..1 = pitch −60..+60), `anim_add_lean` (u 0..1 = lean −1..+1).
- Root-motion deltas are in the glTF `__root__` frame (`rootMotionWorld` converts).

## Asks (not changed here — engine code is not edited by this skill)
1. **Speed bands from data.** `BANDS` hardcodes 1.3/2.5/4.5 m/s. Mocap walks land at
   1.2–1.45 m/s; a mismatch shows as slide when gameplay drives velocity. Derive band speed
   from each band's `_f` clip root speed (meta `speed_mps`) or accept it via options.
2. **Per-body stride.** Root motion is the same for every body; only hip bob is scaled by
   hips height. A shorter body (f_avg, hips 0.87 m vs 0.95) overstrides at the same speed.
   Scale root-motion playback (or animation rate) by rig hips height / clip reference height.
3. **REST_POSITIONS mirroring.** `sourceRestPos` negates x and the placeholder mannequin
   puts `_l` bones at −X in glTF, while char_skeleton_ref (and anatomy for a +Z-facing glTF
   character) has `_l` at +X. Harmless today (only root/hips carry positions, both at x = 0)
   but any future position track on a lateral bone would be mirrored.
4. **Clip naming.** Decide between ASSETS.md §0.4's `anim_loco_walk_f_loop` and the engine's
   `anim_walk_f_loop` (LocomotionBlend's `clipName` can map either).
5. **Additive semantics in docs.** Either update §0.8 to "full poses, engine subtracts frame 0
   of the base" or change `evalAdditive` to read stored deltas.
6. **CI.** The playback test runs from the skill (copied into the set's `_work/`). To keep it
   in `pnpm test`, copy `tools/playback/anim_playback.test.ts` into
   `packages/engine/src/test/` with the env replaced by the real export paths.
   Each `anim_cli.py playback` runs in its own `_work/playback/run-<time>-<pid>/` with a snapshot of the set's glb/meta/ref (safe to run concurrently; kept on FAIL, removed on PASS; `_work/playback/playback.log` = latest run).

## First-person asks (see references/first-person.md "Engine asks" for detail)
7. View-model layer (second camera, FOV 54 deg vertical, minZ 0.01, depth cleared, layerMask); rig parented
   to FirstPersonCamera with the eye midpoint E0 on the camera; physics eye at E0.y (1.63 m), not pos.y + 0.75.
8. Bone-masked playback (`ig_mask` arms / fingers on the anim glb) so view-model clips and hand-pose
   layers leave the rest of the rig alone.
9. Item grip transform: `meta.fp.grip_r` as the item's local TRS under attach_hand_r (first and third person).
10. Moving-part hooks (`meta.fp.parts` / `visible` / `carry` / `state_out` onto `<item>_<part>` via ig_parts).
11. Event dispatch from `meta.fp.events` (VFX at sockets, sfx cues, ammo, physics mag, melee hit window),
    exactly once per crossing including loop wrap and cancels at `refire_frame`.
12. Pose morphs (2026-10-13): hand-pose clips may carry corrective shape-key weights in the meta (`pose.morphs`,
    e.g. `anim_hand_cup_r` -> `{"palm_fold_r": 1}`); the anim glb has bone tracks only and `ClipLibrary` drops
    anything else. Set the arms mesh's morph target influence by name (Babylon `morphTargetManager`, targets from
    `extras.targetNames` on `char_fp_arms_bare_*`), scaled by the finger layer's weight and crossfaded with it;
    0 when no playing pose names it. Third-person bodies have no such targets (ignore missing names).
