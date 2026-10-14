# The `animation` section of `assets/spec.json`

The scripts are project-agnostic; everything project-specific lives in
`<project>/assets/spec.json` → `animation`, transcribed from the project's delivery doc.
For irongrip that is ASSETS.md §0.8 (quoted verbatim in `animation.rules_0_8`), §0.7
(skeleton), §0.4 (naming), §6 (clip list), S8 (style), §0.12/§0.13 (done, manifest).
**The document wins on conflict**: fix spec.json and say so. Contradictions go to the user.

## Building it for a new project
1. Read the delivery doc's animation, skeleton and naming sections and the engine's clip
   loader (what names it looks up, which frame convention it assumes, how it reads root motion).
2. Copy irongrip's section (below) and change what differs. Quote the doc's rules in `rules_*`.
3. Record `spec_source`. Run `anim_cli.py build` + `playback` on one test take before any
   real work.

## Field reference

| Field | Used by | Meaning |
|---|---|---|
| `fps` | build, validate | Delivery frame rate (30). |
| `export_dir`, `src_dir`, `set_file` | cli | `export/<dir>/<set>.glb`, `src/<dir>/<set>/animset.json`. |
| `owner` | claim | Default claims owner for `anim_*`. |
| `skeleton_glb`, `skeleton_json`, `ragdoll_json` | all | Target rest pose, bone lengths, capsules (read only). Bone list is `skeleton.bones`. |
| `position_tracks` | validate | Only these bones may carry translation (root, hips). |
| `unkeyed_bones` | build | Never keyed (eyes: runtime look-at). `attach_*` are never keyed. |
| `allow_scale_keys` | validate | false: any scale channel FAILs. |
| `meta.file/required/source_enum` | build, validate | One meta array per set; required fields; allowed `source`. |
| `naming.*` | validate | Clip/set regex, loop suffix, additive prefix, direction suffixes, the engine's lookup patterns. |
| `directions_gltf` | validate, playback | Root travel vector [x, z] per suffix (glTF: faces +Z, right = −X). |
| `engine.bone_frames` | build, validate | `world_aligned` (engine today) or `rig`. |
| `engine.hips_rest_src` | build, review | Hips rest the engine assumes for clips (REST_POSITIONS.hips). |
| `engine.locomotion_bands_mps` | validate | Band speeds hardcoded in locomotion.ts. |
| `engine.additive_storage` | build | `full_pose`: files hold base0·delta; the engine subtracts. |
| `engine_dir` | playback | Engine package relative to assets/. |
| `durations_s.idle`, `cycle_steps` | validate | §0.8 idle length and steps per cycle. |
| `thresholds.*` | validate | `[warn, fail]` pairs; see validation.md. |
| `end_pose_tags`, `end_pose_name_re` | validate | Which clips must end ragdoll-ready. |
| `airborne_tags` | validate | Clips exempt from the grounded check. |
| `review.bodies` | review | Body + head + outfits per rendered body; `contact_frames`, `size`, `video`. |

## Contradictions found while transcribing irongrip (for the user)
1. **Clip names.** §0.4's example is `anim_loco_walk_f_loop`; the engine looks up
   `anim_walk_f_loop` / `anim_idle_a_loop` / `anim_hands_up` … (locomotion.ts, character.ts).
   Engine names are used; one of the two should change.
2. **Additive storage.** §0.8 says additive clips are "deltas from the first frame of the
   base pose"; `clips.ts evalAdditive` computes the delta itself (base⁻¹·q) from full poses.
   Files therefore hold base0·delta, which is the same information in the form the engine reads.
3. **Meta file.** §0.8 says "every clip gets a .meta.json"; the placeholder and
   `ClipLibrary.addGlb` use one array per set. One per set is written.
4. **Licences.** §0.12 allows CC0, CC-BY, purchased, Mixamo. The CMU database's terms
   ("free for use, may be included in commercial products, may not be resold") are none of
   those words: confirm before shipping CMU-derived clips.
