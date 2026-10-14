# Validator checks (`validate_anim.py`)

Thresholds are `[warn, fail]` in `spec.animation.thresholds`. Motion is measured on
char_skeleton_ref with root motion applied, glTF space. Foot points: heel (below the
ankle, 3.5 cm back), ball (toes joint), toe tip. A point is *planted* when within 3.5 cm of
its rest height and moving < 0.45 m/s horizontally; spans < 3 frames are dropped.

| Check | Measures | Level |
|---|---|---|
| `set.id` | set id regex, file name = id | FAIL |
| `file.animations/unique_names` | ≥ 1 animation, unique names | FAIL |
| `khronos.errors` | Khronos glTF validator (from game-asset-modeling) | FAIL |
| `bones.subset_of_skeleton`, `bones.count` | animated node names ⊆ spec bones, ≤ max_bones | FAIL |
| `bones.no_scale_keys` | no scale channels (unless allowed) | FAIL |
| `bones.translation_only_root_hips` | translation only on root/hips | FAIL |
| `bones.attach_unkeyed` | attach_* not animated | WARN |
| `bones.frame_convention` | rest rotations identity for `world_aligned` (else retarget.ts converts twice) | FAIL |
| `tracks.linear` | LINEAR samplers | WARN |
| `meta.*` | file present, one entry per clip, required fields, source enum, source+licence in notes | FAIL/WARN |
| `brief.no_mirrored_clips` | no `mirror` in the animset (§0.8 author both sides) | FAIL |
| `name.*` | regex, `_loop` ⇔ loop, `anim_add_` ⇔ additive, direction suffix ⇔ direction, engine lookup names | FAIL/WARN |
| `fps` | key spacing = 1/30 s ±1%, uniform, meta fps | FAIL |
| `root.on_ground`, `root.yaw_only` | root y = 0; root rotation has no pitch/roll | FAIL |
| `root.facing` | mean body facing (hip/shoulder lines) vs root +Z, non-turn clips: WARN > 25 deg, FAIL > 60 (e.g. `root_yaw: none` on an idle whose performer turned after the T-pose) | WARN/FAIL |
| `root_motion.present` | root travels/turns in root-motion clips | WARN |
| `root_motion.hips_follow_root` | max horizontal hips offset from root (m) | 0.25 / 0.4 |
| `root_motion.root_static`, `.no_hips_displacement` | in-place clips: root still; hips don't carry displacement | FAIL / 0.25 / 0.4 m |
| `loop.seam_position` | max joint distance first vs last frame (root-relative), cm | 0.5 / 2 |
| `loop.seam_velocity` | frame-to-frame velocity change at the seam beyond the clip's own max, per joint, m/s | 0.3 / 0.8 |
| `loop.root_velocity` | same for root velocity | 0.1 / 0.3 |
| `loop.idle_length` | idles 4–8 s | WARN |
| `loop.two_steps` | one plant per foot in a locomotion cycle (seam-crossing span counted once) | WARN |
| `feet.slide_per_step` | max horizontal drift of a planted point within one contact, cm; if the foot yaws > 15 deg in the span it is a pivot and the least-moving planted point is measured (the other end sweeps an arc) | 2 / 5 |
| `feet.grounded` | 10th percentile of the lowest foot point above ground, cm (not for airborne tags) | 2 / 5 |
| `feet.ground_penetration` | lowest foot point / body capsule below ground, cm | 1 / 3 |
| `feet.step_width` | lateral spacing of planted ankles (6–30 cm) | WARN |
| `speed.band` | root speed vs engine band (walk 1.3, jog 2.5, run 4.5 m/s), relative | 15% / 40% |
| `direction` | root travel angle vs suffix axis, deg | 10 / 25 |
| `joints.limits` | per bone (references/joint_limits.json): swing, twist, signed hinge for knee/elbow; worst overage, deg | >0 / 15 |
| `pose.torso_lean_back` | mean pitch of hips + spine in the root frame, + = leaning back (arched back under raised arms reads badly on the bodies); skipped for `torso_lean_exempt_tags` (sit, lying, death, stagger, hit, dragged, fall); fix with clip `level_torso: {bone: target_deg}` (+ `level_torso_keep`, default legs and upper arms keep their world orientation), spec `torso_lean_back_deg` | 8 / 20 |
| `body.interpenetration` | ragdoll capsules posed per frame, non-adjacent pairs, depth beyond rest, cm. Torso x thigh is tested from 45% down the thigh only (the fat torso capsule touches the thigh tops at rest, so any sit/crouch/kneel fold would read as a hit; a knee in the chest still does) | 2 / 6 |
| `end_pose.settled` | hit/death clips: fastest joint over the last 0.3 s, m/s | 0.05 / 0.15 |
| `end_pose.no_interpenetration` | same clips: capsule overlap at the last frame, cm | 1 / 2 |
| `additive.base`, `additive.no_root_motion` | base clip in the same set; root static | FAIL |

Not machine-checked (review + critic): style, cloth/skirt intersections (capsules only cover
bones), hand poses, whether the intent reads.
