# First-person (view-model) mode

View models are keyed by hand, the way HL2's were: no open mocap source covers them. The pipeline is
the same as for third-person sets: brief, build, validate, playback, review, critic, manifest. The brief
holds key poses instead of takes. The project copy of this contract is in `assets/spec.json`
`animation.first_person`, and the project doc wins on conflict.

## Contract

| | |
|---|---|
| Rig | The shared skeleton (`char_skeleton_ref`). The view-model instance is a body on it, `char_fp_arms_*` when delivered, with the torso at REST. |
| Keyed bones | The arm chains only, per side: `shoulder`, `upper_arm`, `lower_arm`, `hand` and 15 finger joints (38 bones). Rotation channels only, no translation, no scale. Root, hips, spine, neck, head, eyes and legs are never keyed. `attach_*` bones are not keyed. |
| Eye / camera frame | E0 = midpoint of the `eye_l`/`eye_r` rest joints = (0, 1.6299, 0.1366) in glTF model space. The camera looks along model +Z with +Y up, and screen right is model −X. The engine parents the rig to the camera with E0 on the camera, and puts the physics eye 1.63 m above the feet. |
| FOV | The view model renders at 54° vertical in its own layer: a second camera with the same transform, `minZ` 0.01 m, and depth cleared. The world stays at FirstPersonCamera's 75°. Framing is checked at 4:3, the narrowest supported aspect. |
| Item | Hangs off `attach_hand_r`. Its local TRS there is the item's own root extra **`ig_grip_r`** `{t, r}` (glTF), the source of truth, applied by the engine on attach in first AND third person; the set copies it to `meta.fp.grip_r`. Items without it (pickups) attach at identity. Item frame: +Z muzzle, +Y up, origin at the grip point (`frame_check.py`). |
| Support hand | Uses the item's `socket_hand_l` when present, in the same frame as `attach_hand_l`: +Z toward the fingers, +Y toward the thumb, and the left palm facing −X. Otherwise it uses the brief's `fallback` grip in item space. |
| Sights | `socket_sight`: aim-down-sights clips put its origin on the camera axis. No ADS clip yet (see Open questions). |
| Moving parts | Normalised per-frame curves in `meta.fp.parts` (0 = rest, 1 = `ig_parts` `travel_m`/`angle_deg`); see below. |
| Events | Listed in `meta.fp.events` as `{name, frame, t, socket?, sfx?, vfx?}`. VFX, audio and gameplay key off these. Required events per action are in spec `events.required`. |
| Naming | `anim_fp_<item>.glb` (item id without `wpn_`). Clips are `anim_fp_<item>_<action>[_loop]`. The hands set is `anim_fp_hands`. The finger-pose library is `anim_hand_poses` (`anim_hand_<pose>_<l\|r>`). |

### Why part curves live in the meta file and not as node tracks
- `ClipLibrary` keys tracks by skeleton bone name and drops everything else. The anim glb has no item nodes.
- One clip set must drive every copy of the item: the view model, the third-person hand and the world pickup.
- Normalised values stay valid when the weapon is remodelled (a `travel_m` change), and they retime with the clip.
- The engine already reads `ig_parts`. Applying a curve is one line: `axis * travel * v` or `axis-angle(angle * v)`.
- `rotate_then_translate` parts (bolt actions: `angle_deg` about `axis` through the node origin, then `travel_m` along `then_axis`) use one curve: v 0–0.5 turns the handle, v 0.5–1 keeps the full turn and slides it back. A part listed in the brief's `part_wrap` (a revolver cylinder, one chamber per unit) is periodic: its curve carries `"wrap": true`, 1 ≡ 0, and the engine must not interpolate backwards across a 1 → 0 step.
- `visible` hides a dropped magazine (the engine spawns the physics copy on `mag_drop`). `carry` parents a part to `attach_hand_l` with a given local TRS inside a frame window. `state_in` / `state_out` let parts persist across clips, for example a slide locked back after `fire_last` until `reload_empty`.

### The strong-hand grip: `ig_grip_r` on the item (source of truth)
`attach_hand_r` (from `assets/src/char/_char_lib.py`) has +Z along the metacarpals, +Y toward the thumb, and its origin 2 cm palm-side of the 70 % wrist→knuckle point. An item attached at identity then sits with the grip through the palm, the knuckles 2–3 cm ahead of the front strap, and the index finger inside the frame. The pilot's first review renders showed exactly this. The build therefore solves the grip on the item (`fplib.suggest_grip`): the palm rests on the right grip panel, the index lies along the frame, and the thumb web sits under the hammer spur. This correction now lives on the item: the weapons pipeline solves it once per item on the view-model arms' own hand SKIN (`fplib.solve_grip_skin`: palm/web/index skin touching the grip, no skin inside the item; `wpn_tools/grip_solve.py`), stores it in the catalog and writes it as the root extra `ig_grip_r` = the item's local TRS under `attach_hand_r`; `frame_check.py` re-checks it (palm contact, penetration after a finger wrap fit on the skin).

- **fp_build** reads `ig_grip_r` from the item and copies it to `meta.fp.grip_r` (`grip_r_source: "item ig_grip_r"`). Only when the item has none does it use the brief's `grip_r` (explicit `{pos, rot}`, or `auto` = `fplib.suggest_grip`, the old capsule solve).
- **validate_fp** fails `fp.grip_r_item` when a clip's `meta.fp.grip_r` differs from the item's `ig_grip_r`: rebuild the set after the item changes.
- **Engine:** apply `ig_grip_r` whenever an item is attached to `attach_hand_r`, in the view model and in third person (the same palm-through-grip error shows there).
- Why the skin and not the capsules: on `char_fp_arms_bare_m_a` the finger and palm bones run near the back of the hand (dorsal skin 3–5 mm off the phalanx line, volar 14–18 mm; palm volar ~20 mm vs the 11.5 mm palm capsule), so a capsule-clean grip still sinks the item ~7 mm into the palm skin. `fplib.SkinHand` poses the arms' real hand vertices (LBS; the bind pose is the skeleton rest), `fplib.fit_fingers_skin` wraps fingers on it, and `Item.sdf` now resolves the sign of far points by bbox + winding number (the normal vote gave false 7 cm penetrations near open parts such as a slide channel).

## Brief (`animset.json`, `"mode": "first_person"`)
```jsonc
{ "id": "anim_fp_pistol_a", "mode": "first_person", "item": "wpn_pistol_a",
  "part_defaults": {"hammer": 1.0},                      // part values when a clip doesn't curve them
  "grip_r": {"auto": {"pose": "pistol_grip", "web": [0, 0.027, -0.024]}},   // or {"pos", "rot"}; omit = identity
  "breath": {"period_s": 4, "pos_m": [r, u, f], "rot_deg": [yaw, pitch, roll], "drift_deg": 0.3},
  "grips": {"support": {"space": "socket:socket_hand_l", "pos": [...], "fingers": "cup", "fit": true,
                        "fallback": {"space": "item", "pos": [...], "fingers_dir": [...], "thumb_dir": [...]}},
            "mag_l": {"space": "part:mag", "pos": [...], "fingers_dir": [...], "thumb_dir": [...], "fingers": "mag_hold", "fit": true}},
  "poses": {"ready": {"item": {"pos": [0.12, -0.125, 0.34], "rot": [-3, 1.5, 0]},
                      "hand_r": {"fingers": "pistol_grip", "fit": true, "pole": [r, u, f]},
                      "hand_l": {"grip": "support"}},
            "ready_trigger": {"base": "ready", "hand_r": {"fingers": "pistol_trigger", "fit": true}}},
  "clips": [{"name": "anim_fp_pistol_a_fire", "action": "fire", "frames": 18, "refire_frame": 9,
             "keys": [{"f": 0, "pose": "ready_trigger"}, {"f": 17, "pose": "ready_trigger", "ease": "inout",
                       "item": {"dpos": [...], "drot": [...]}}],
             "parts": {"slide": [[1, 0], [2, 0.55], [3, 1], [5, 0, "in2"]]},
             "visible": {"mag": [[12, 0], [16, 1]]}, "carry": [{"part": "mag", "grip": "mag_l", "frames": [16, 30]}],
             "recoil": [{"f": 2, "kick_deg": 8, "back_m": 0.038, "rise_m": 0.013, "freq_hz": 3.6, "damping": 0.62}],
             "settle": [{"f": 18, "down_m": 0.007, "pitch_deg": 1.2}],
             "events": [{"f": 2, "name": "fire", "socket": "socket_muzzle", "sfx": "..."}],
             "frame_policy": "hold|enter|exit|free", "state_in": {"slide": 1}}]}
```
- **Conventions (every space).** Positions are `[right, up, forward]` in metres. Rotations are `[yaw, pitch, roll]` in degrees (+ = turn right, nose up, clockwise from behind), applied roll → pitch → yaw. For hands, `fingers_dir` / `thumb_dir` give the attach frame directly (+Z fingers, +Y thumb).
- **Spaces for `hand_l`.** `camera` (swayed with the view), `item`, `part:<name>` (follows the part at its current value), `socket:<name>` (with `fallback`), and `rest` (the A-pose arm, off screen behind the camera).
- **Interpolation between keys.** When both keys share a space, the grip is interpolated in that space, so holds are exact. When the spaces differ, both targets are resolved at the current frame and blended in model space. Easing uses the destination key's `ease`: `linear`, `in`, `out`, `inout`, `in2`, `out2`, `smooth`, `hold`, `snap`, `back`.
- **Fingers.** A library pose name, `{"pose": name, "<finger>": {...}}`, or `{"blend": [[a, w], ...]}`. With `fit: true` each key pose is closed onto the item once, at its own frame. The support hand also treats the strong hand's capsules as obstacles. Between keys the finger angles blend.
- **`"fit_model": "skin"`** (set level, opt-in). Finger fits run on the arms asset's own hand skin
  (`fplib.fit_fingers_skin`, with backtracking: a joint that cannot clear uncurls its parent joint and refits), and the
  support hand also clears the strong hand's posed skin (`fplib.skin_sdf`). validate_fp then grades `fp.fingers_vs_item`
  and `fp.hand_vs_hand` on the skin; the capsule numbers stay as INFO. Needs `char_fp_arms_*` delivered; the build is
  slower (anim_fp_pistol_a: about 25 min). The capsules sit on the bone lines, which on this mesh run near the back of
  the hand, so they misjudge contact by 2-4x (see the ig_grip_r section).
- **Thumb in the skin fit.** When the thumb cannot clear at its library angles it re-aims (opposition, spread, curl)
  and keeps the setting whose pad lies on the item. A thumb swung straight out into the air reads wrong in first
  person. For a thumb that must lie in a specific place (the pistol support thumb along the frame), give explicit angles.
- **IK per frame.** 2-bone arm IK towards the hand target. The clavicle takes 18 % of the swing (capped at 22°), the elbow follows the `pole`, and 55 % of the wrist twist moves into the forearm.
- **Fit memo.** Key-pose finger fits are memoised across keys and clips on (finger spec, hand-in-item placement to 1 mm, strong-hand placement for the support hand, part values). Skin fits cost minutes each, and the same grip recurs in every clip, so this takes a skin-fit set from hours to minutes. Set `"fit_memo": false` in the brief to disable it.
- **Secondary motion.** Breathing sway plus periodic drift, seamless on loops; one-shot clips end on the sway they started with (`breath.end_match`, default true), so chained clips (fire → idle, reload_round → reload_round) meet without a sub-cm step. Recoil is a damped spring (kick, back, rise, roll, yaw). Settle is a damped dip when a pose lands.

### Chains and per-item events (the four long-gun / revolver sets)
- **Interruptible loading** is a chain of clips that start and end in the same pose, so the engine can repeat the middle and cut to the end at any time: revolver `reload_start → reload_eject ×spent → reload_round ×rounds → reload_end`; bolt rifle `reload_start → reload_round ×n → reload_end`, and `fire → bolt` after every shot.
- **Automatic fire** is a `fire_loop` whose recoil is periodic: the brief lists each shot again one and more loop periods earlier (negative `f`), so the damped spring tails wrap across the seam.
- **Per-item required events**: spec `events.required_item[<item>][<action>]` overrides `events.required` where the mechanism differs (revolver/break-action: no case at the shot; open-bolt SMG: `bolt_close` on the last round, `bolt_charge` on the empty reload). New event names: `fire_2`, `cylinder_index`, `gate_open/close`, `case_eject`, `round_grab`, `round_in`, `bolt_open/close`, `bolt_charge`, `barrels_open/close`, `shell_extract`, `shell_in`.
- **Loose rounds and shells** are not item parts: the engine shows a round mesh on `attach_hand_l` from `round_grab` to `round_in`/`shell_in`, and spawns spent ones at `socket_eject` on `case_eject`/`shell_extract`.
- **Strong hand never leaves the grip**: the item hangs off `attach_hand_r`, so bolt and charging-handle work is done by the left hand over the top (rifle, SMG). A right-hand bolt stroke needs an item hand-swap window (engine + skill ask).
- **...unless the support hand takes the item (anim_fp_rifle_hunting_a).** Keys whose `hand_r` names a `space` (the hands-set free right hand) work with a set-level item too: while the support hand holds in an item/part/socket space the item rides `attach_hand_l` (`meta.fp.item_attach` holder `l`), the right hand works the bolt or loads rounds, and an attached key puts it back on the grip. Tips from the rifle: put the knob grips in ITEM space, one key per point of the knob's path (in `part:` space a rotate_then_translate handle rolls the hand 80 deg over the top); leave and retake the grip through 2 cm / 4.5 cm side-offset keys refitted with `fit` (blending the grip fit straight to an open hand drags the fingertips through the wood); clip `"item_sway": true` keeps the rifle breathing on the support hand; the review routes such sets through the per-frame item track (fp_review.review). The weapons pipeline's `socket_hand_l` and `ig_grip_r` still decide whether the view-model arms (0.50 m upper+lower arm, torso at rest) can reach a long gun's fore-end: plan poses with reach numbers before keying.

### Hands sets: per-clip items, a free right hand, contact points (opt-in; `anim_fp_hands`)
- **Per-clip item.** Set-level `"item": null`; a clip may name its own `item` (or none = empty hands) and `grip_r`. For pickups `grip_r` can be `"socket:socket_hand_r"` or `{"socket": "socket_hand_r", "offset": [x, y, z]}` (attach-frame metres); the item's own `ig_grip_r` still wins. `meta.fp.item` is per clip; validate and review load it per clip.
- **Free right hand.** A key whose `hand_r` names a `space` (`camera | item | socket:<s> | part:<p> | hand_l | rest`) IK's the right hand to that target (same spec as `hand_l`, incl. named `grip`s) instead of carrying the item. The item then follows its own keys and does not breathe with the view. Who holds it per frame (right hand / left hand holding it in an item space / world) goes to `meta.fp.item_attach` windows `{holder, bone, frames, offset}`: the engine re-parents the item at each window start (`world` = leave it where it is). `fp.item_world_static` checks a world-held item does not move. Empty-hands clips need `hand_r.space` on every key.
- **Contact points.** Any hand spec may carry `"contact": {"point": "pinch|index_tip|thumb_tip|palm", "at": [r, u, f], "pose"?}`: the attach position is derived so that point of the hand (in its finger pose) lands on `at` in the hand's space, with the spec's `rot` / `fingers_dir` / `thumb_dir` (twist a knob with `rot`, the pinch stays on it).
- **Options.** `open_deg` (clip or hand spec: how far the wrap fit may open fingers, default -8), `fit_every` (clip: re-wrap between keys, for hands moving onto or off an item), frame policy `show` + clip `"show": [a, b]` (item in frame only in that window), set-level `events_required` {action: [names]} (`fp.events_set`) and `event_names` for actions the spec does not list, clip `interaction` (copied to meta, e.g. the use point in camera space).
- **Thrown items: `release`** (clip, a frame). From that frame on the item has left the view model: `meta.fp.item_attach` gets a `gone` window (offset = the camera-space TRS where the engine spawns the projectile), the review hides the item, and validate ignores it (in frame, near plane, fingers). Key the release as an attached key followed by a free `hand_r` key (`space`) on the release frame; put `throw_release` on the same frame. Used by the molotov and pipe-bomb sets (`anim_fp_hands_throw` timing).
- **Regrip: `regrip_r`** (clip, a grip spec in item space like `grip_r`: `{pos, rot | fingers_dir/thumb_dir}` or `contact`). The right hand holds the item at this TRS in the clip instead of the item's `ig_grip_r` (spray can: index on the actuator; radio: fingers on the side talk bar). `meta.fp.grip_r` stays the item's `ig_grip_r` (`fp.grip_r_item` still passes); `meta.fp.regrip_r` holds the clip's, and `item_attach` windows (`r` at `regrip_r`) tell the engine to re-parent with that offset. A transition clip slides the hand between grips with free `hand_r` keys in `item` space while the item stays still (`world` window, `fp.item_world_static`). Engine ask: honour `item_attach` offsets on attach.
- **Hanging items: clip `hang`** (`true` or `{damping, range_deg: [lo, hi]}`; the item needs the root extra `ig_hang` `{pivot, axis, com}`, glTF item space). While the right hand carries the item it swings as a damped pendulum about the bar in the fingers, driven by the hand's motion and gravity: the hand follows the KEYED item, the shown item = keyed item o rotation about pivot/axis. `range_deg` are hard stops (the item meets the hand). Meta `fp.hang` `{pivot, axis, com, angle_deg[]}` (the engine applies the angle on top of `grip_r`, or simulates it); validate and review place the item with it. Used by `anim_fp_hands_pickup` (ammo tin by its bail; key the hand turned over for the look, the tin stays plumb).
- **Secondary items: clip `props`** (`[{item, attach: [{holder: "r", frames} | {holder: "item", socket, own_socket, frames}], parts: {p: curve}, part_defaults}]`). A second item shown for a window: in the right hand at its own `ig_grip_r`, or seated with its `own_socket` on the main item's `socket` (identity). Meta `fp.props` carries the item_attach windows (offset = the prop root under attach_hand_r, or in main-item space) and part curves; the review places it per frame. Validate does not grade fingers against a prop. Used by `anim_fp_rifle_police_a` reload_clip / reload_end (wpn_stripper_clip_a, rounds 0 -> 1 on the push; to seat it exactly, solve the hand grip back from the seat: `_work/clipgrip.py`).
- **Sleeves in review.** `anim_cli.py review <set> --sleeve <char_fp_sleeve_id> [--clips a,b]` wears that fp_sleeve layer on the arms (zone culling as `Rig.refreshMask`); `review.sleeve_swap: {clip: [from, to]}` swaps it at the clip's `sleeve_swap` event. Output in `review/<set>/_<sleeve>/`.

## Finger-pose library (`references/fp_hand_poses.json`)
The poses: relaxed, open_palm, fist, pistol_grip (trigger finger off), pistol_trigger, pinch, two_hand_support (alias support), cup, mag_hold, overhand, flat_push. Each finger has anatomical angles: flex per joint, spread, twist, and oppose for the thumb. The axes come from each skeleton's rest joints (`fplib.HandModel`), so poses retarget. The names match the weapons' `ig_grip_pose` / `ig_grip_pose_l`. `anim_hand_poses` exports each pose as a 2-frame, fingers-only clip per side, which third person can use as a finger-masked layer.

## Checks (`validate_fp.py`, thresholds in the spec)
The file-level rules are the same as for all sets (names, 30 fps, meta fields, world-aligned frames). On top of those:

| Check | Measures |
|---|---|
| `fp.keyed_bones` / `fp.all_mask_bones_keyed` / `fp.rotation_only` | Channels are exactly the arm mask (the finger mask for poses), rotation only. |
| `fp.reach_r` / `fp.reach_l` | The item (through `grip_r`) and the support hand against the brief's targets. A miss means the arm cannot reach. |
| `fp.hand_on_grip_l` | Support hand against its grip while holding (item, part or socket space), in cm and degrees. |
| `fp.in_frame`, `fp.muzzle_in_frame` | Projected item vertices at the view-model FOV and 4:3, per `frame_policy`. |
| `fp.screen_cover`, `fp.centre_clear` | The gun must not fill the screen or sit on the crosshair during holds. |
| `fp.near_plane` | Closest visible arm or finger capsule or item vertex to the eye. The upper arm is cut at t = 0.45, as in `char_fp_arms`. |
| `fp.fingers_vs_item`, `fp.hand_vs_hand` | Finger and palm capsules against the item's signed distance field (1.2 mm surface samples), and strong hand against support hand. |
| `loop.seam`, `loop.seam_velocity` | Idles and lowered loops. |
| `fp.events` | Required events per action, and frames in range. |
| `fp.parts` / `fp.parts_driven` | Curves name real `ig_parts`, values stay in [0, 1], and fire/reload drive their parts. |
| `fp.item_speed` | Muzzle and rotation speed on non-fast actions (catches pops). |
| `fp.finger_limits` | Every keyed finger joint against anatomical ranges on the HandModel axes (flex per joint, knuckle spread, lateral bend of the middle/distal joints, twist; thumb CMC total) -- `fplib.FINGER_LIMITS`, spec `first_person.finger_limits` overrides; grade `thresholds.finger_limit_over_deg` (default WARN > 0, FAIL > 10 deg for the pose library; first-person sets WARN only: their keys carry brief overrides and fitted angles, e.g. anim_fp_pistol_a_lowered_loop's index knuckle at -43 deg). |
| `fp.fingertip_palm` | Deepest fingertip skin (middle + distal phalanges, thumb tip) under the palm skin of `char_fp_arms_*`, hand-local (the arm pose does not matter); `thresholds.fingertip_palm_mm` (default 2 / 5 mm for the pose library; first-person sets WARN past 2 mm and never FAIL by default, since the skin fit's thumb re-aim checks the item, not the palm -- anim_fp_molotov_a's fitted fist thumb sits 13 mm inside the palm). Catches library poses whose tips sink into the palm when no item stops the fit. |
| `fp.finger_clearance` | Adjacent fingers (index-middle, middle-ring, ring-pinky) on the skin of `char_fp_arms_*`, hand-local: middle + distal phalanx skin of one finger behind the other's local skin plane (`fplib.finger_finger_pen`; proximal skin left out, it joins at the webs). Reports the depth and the per-pair penetration/gap. `thresholds.finger_finger_mm` (default 2 / 5 mm for the pose library; first-person sets WARN past 2 mm and never FAIL by default, like `fp.fingertip_palm`). Library at introduction (2026-10-12): all poses <= 1.6 mm (flat_push index-middle). |
| `fp.thumb_clearance` | Thumb middle + distal skin against each finger's whole skin (`fplib.thumb_finger_pen`, same depth measure, pen/gap per finger). Graded only for library poses that declare `"thumb_clear_mm"` (the thumb must stay that far off every finger, e.g. the cup's thumb wall): value = penetration + shortfall of the gap, `thresholds.thumb_finger_mm` (default WARN > 0, FAIL > 2 mm). Everything else (fists and grips wrap the thumb over the fingers by design; first-person sets) is INFO. At introduction (2026-10-13): fist/pistol_grip/overhand thumbs read ~5.8 mm "into" the curled fingers by this measure. |

**Pose morphs.** A library pose may carry `"morphs": {"palm_fold": 1.0}`: corrective shape-key weights on the arms mesh
(`palm_fold_r`/`_l` on `char_fp_arms_bare_*`, built by `assets/src/char_fp/_fp_palm_fold.py`: the metacarpal fold that the
skeleton cannot make, since it has no metacarpal bones). `anim_hand_poses` writes them per side to the clip meta
`pose.morphs` (anim glbs carry bone tracks only, `ClipLibrary` drops anything else); the review harness applies them
(`applyMorphs`); the engine must too (ask: set the arms' morph target influence from the playing pose layer's meta, blended
with the layer weight). First-person sets do not carry them yet (their fitted support hands would need the fold in the skin fit).

## Review (`fp_review.py`, `tools/review/harness_fp.html`)
Per clip, written to `review/<set>/<clip>/`:
- `eye_strip.png`: the player's view at 54°, with frame numbers, events, the 4:3 frame and the screen centre.
- `hands.png`: grip close-ups.
- `side.png`: the rig from outside.
- `world_fov.png`: the same frame at 75°.
- `timing.png`: part curves, carry and hidden bars, item height and pitch, support-hand hold bar, events and keys.
- `<clip>.mp4` / `.gif` when run with `--video`.

The item is attached through `grip_r`, and parts are driven from the meta curves exactly as the engine is asked to. The Babylon-posed item is probed against the build (it should be 0.00 cm). The review uses `char_fp_arms_bare_m_a` when it is delivered. Otherwise it falls back to the m_avg body with only its arm triangles kept.

## Playback (`tools/playback/fp_playback.test.ts`)
The test runs through the engine's own ClipLibrary, retargetLibrary, evalClip and applyPose on the view-model arms asset. It checks:
- only the mask bones are keyed, and the meta flags match;
- loops close;
- the rig's eye midpoint matches the contract (within 3 mm), and the rig has the attach bones;
- the `attach_hand_r` / `attach_hand_l` frames posed by the engine match the build within 1 cm and 1°;
- the meta `fp` block is complete.

## Engine asks
1. **View-model layer:** a second camera at FOV 54° with `minZ` 0.01 and depth cleared, plus a layerMask. The rig is parented to the FirstPersonCamera with E0 on the camera, and the rig's torso is never animated.
2. **Bone-masked playback:** `layers.play(clip, {mask: 'arms' | 'fingers'})`, driven by the glb extras `ig_mask`. Finger-pose clips become an additive-free override layer for third person too.
3. **Grip transform:** apply the item's root extra `ig_grip_r` (`{t, r}`, glTF) as its local TRS under `attach_hand_r` on every attach, in first and third person (`meta.fp.grip_r` carries the same value for sets).
4. **Part hooks:** apply `meta.fp.parts` / `visible` / `carry` / `state_out` to the `<item>_<part>` nodes via `ig_parts`. Hold part state across clips.
5. **Event dispatch:** fire `meta.fp.events` at frame times, once per crossing, including on loop wrap and at cancel/refire. Consumers: muzzle flash at `socket_muzzle`, shell at `socket_eject`, the physics mag on `mag_drop`, ammo changes, the melee damage window (`hit_window`), and sfx cues.
6. **State machine:** draw → idle ⇄ fire (refire_frame) / fire_last → idle with the slide held → reload_empty; reload; holster; melee; lowered while sprinting. Crossfade 3–5 frames.
7. **Eye height:** rebel.ts puts the camera at `pos.y + 0.75`. Set it so the eye is 1.63 m above the feet (E0.y), so first person matches third person and the arms asset.
8. **REST_POSITIONS mirroring:** see engine-notes #3. It would bite if attach bones were ever keyed with translation, which is why this contract keys rotations only and moves the item with `grip_r`.

## Open questions and conflicts
- `char_skeleton_ref.skeleton.json` `attach_convention` says bone +Y = item forward. The exported frames and `_char_lib.py` say +Z = fingers (forward) and +Y = thumb. The pipeline follows the exported frames.
- The weapons' `ig_sight_eye_relief_m` for the pistol is 0.62 m. That puts the sight about 0.75 m in front of the shoulder, which the 0.57 m arm cannot reach. For view-model ADS use about 0.40–0.45 m, or lean the view-model torso (not keyed today).
- The `char_fp_arms` review plan says camera FOV 75°. This contract proposes 54° for the view-model layer. The arms agent's renders should use 54° to match.
- **Per-body view models.** Clips are authored on char_skeleton_ref (m_avg proportions, eye 1.63 m). `char_fp_arms_bare_f_a` has its eye at (0, 1.4963, 0.1266) and shorter arms. On it the same clips put the hands (and so the gun) elsewhere relative to the eye, and a two-hand grip opens up. Options: (a) build `anim_fp_<item>_f` sets by pointing the build at the f_avg skeleton, which needs a `skeleton_glb` override per set (TODO in fp_build); (b) an engine-side 2-bone IK pass that pins attach_hand_r/l to the m_avg-authored eye-relative targets. (a) is cheaper and keeps review parity.
- **Weapons pipeline, `wpn_pistol_a` grip.** The identity attach puts the grip about 1.5 cm too deep in the palm and 2–3 cm too far forward, which both the arms review and this review found. `grip_r` corrects it for the view model. Proposal for the weapons owner: author `ig_grip_r` (the solved TRS) on each item, or move the item origin to match. `socket_hand_l` (cup) is used as delivered, 3 cm lower for the pistol so the support palm clears the strong hand's pinky, which wraps under the TT-33's short grip.
- **Rack vs slide stop.** `reload_empty` releases the slide with the shooting thumb on the slide stop. An overhand slingshot on this skeleton put the support forearm across the view and the fingers through the slide. The contract still allows `part:slide` grips for guns without a slide stop.
