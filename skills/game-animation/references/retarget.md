# Retargeting

## Pipeline position
`blender_import.py` (headless Blender) imports the take, samples each source bone's world
matrix per frame (converted to glTF axes: +Y up), and calls `animlib.retarget` — the same
numpy code the CPython stages use. It saves the raw result keyed on the real
`char_skeleton_ref` armature in `_work/<source>.retarget.blend` (verified identical to the
numpy FK). Never ship the source skeleton: only the target bone names reach the glb.

## Math (world-space delta retarget with rest alignment)
For a mapped target bone b with source bone s:

    Wc(b, t) = R_s(t) · R_s(rest)⁻¹ · C_b⁻¹

- `R_s` world rotation of the source bone; rest = `rest_frame` (a T-pose frame) or the bind pose.
- `C_b` = minimal rotation turning the source rest direction (joint → next mapped joint down
  the chain that is not coincident, e.g. CMU Neck sits on Spine1 so upper_chest uses Head)
  onto the target rest direction. It absorbs T-pose vs A-pose (arms ~40°) and posture
  differences. Leaves and hands without fingers inherit the parent's C; the head keeps
  C = identity (`mapping.world_rest`), because every sane rest looks forward.
- `Wc` is the *canonical* world delta: the bone's rotation relative to its own rest. Local
  canonical rotations are `Wc(parent)⁻¹ · Wc(b)`; unmapped bones follow their parent.
- Result: target limb directions equal the source's, twist is carried relative to rest.
- Take facing is normalised (hip line → faces +Z); hips height scales by leg length
  (hips above the lowest foot joint at rest); no absolute units are needed.
- Missing fingers get a relaxed curl (`RELAXED_HAND_DEG`, curling toward the palm, which faces
  the thigh in the A-pose).

After resampling: foot pitch calibration (median planted ankle→ball pitch matched to rest;
fixes T-poses whose feet aren't flat) and ground calibration (25th percentile of the lowest
foot point per frame → 0).

## Frame conventions (why the file looks "unrigged")
The delivered MakeHuman rigs have rolled bone frames. `retarget.ts` converts clip tracks per
rig as `q_rig = A_parent⁻¹ · q_clip · A_bone`, which is exact when clip rotations are canonical
(rest = identity). So the glb's nodes carry rest translations but **identity rotations**, and
tracks are canonical local rotations. Hips translation is written as
`key = (0,0.95,0) + (d − d_rest) · 0.95/|d_rest|` so that `convertPosition` (which assumes the
REST_POSITIONS hips at 0.95 m and scales by the rig's hips height) reproduces the pose exactly
on char_skeleton_ref-proportioned bodies and scales hip bob on others. The playback test
checks this end to end (engine vs build: 0.001 cm). If the engine ever consumes clips in the
rig's own frames, set `animation.engine.bone_frames: "rig"` — the writer and reader support it.

## Adding a mapping table (`references/mappings/<name>.json`)
`{"name", "description", "rest_frame": 0 | "bind", "licence", "notes", "bones": {target: source}}`.
Source names match case-insensitively with namespaces (`mixamorig:`) and `|` paths stripped.
Required: hips, upper_leg_l/r; map spine/chest/upper_chest/neck/head, shoulders, arms, legs,
feet, toes where they exist. Skip intermediate joints (Neck1, spine_04): world-space retarget
keeps their effect. Leave crude finger stubs unmapped to get the relaxed hand. Run
`anim_cli.py scan` on the first file: it reports coverage and unmatched table bones. The
ue_mannequin (Quaternius UAL) and 100style tables are written from the published naming and
must be verified on the first real file.

## Rest frame choices
`rest_frame`: `0` (take starts with a T-pose, CMU), `"bind"` (clean T-pose bind, ACCAD Female1,
100STYLE), `"neutral"` (neither: the frame whose bone directions are closest to the target rest,
after removing that frame's facing; ACCAD Male1/Male2 bind offsets are arbitrary per take).
Directions always match the source whatever the rest; the rest only fixes the twist reference, so a
near-neutral frame keeps C small and twist natural.

## Known source quirks (pilot)
- ACCAD's `ToSpine` joint never moves: all lumbar bend lands on `chest` (78 deg in a crouch).
- CMU 104_* (StartJog/JogStop): foot rotations flip ~180 deg about the bone for blocks of frames
  (source solve); unusable without repair.
- CMU 77_02.bvh in the pilot download is byte-identical to 139_02.bvh.
- CMU takes start with a synthetic T-pose facing +Z, then the actor turns: in-place clips must use
  `root_yaw: constant` (the default for loops), never `none`.
