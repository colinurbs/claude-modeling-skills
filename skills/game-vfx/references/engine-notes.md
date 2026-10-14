# Engine notes: Babylon behaviour the runtime depends on, and the open engine asks

## Babylon 9 particle facts (verified in the pilot)
- **Readiness swallows short effects.** `ParticleSystem.animate` emits nothing until `isReady()`: shaders are
  lazy-imported (`_shadersLoaded`, async), the texture must be loaded, the draw wrapper compiled. A fresh system
  needs about 2 frames even when everything is cached. A 2-3 frame muzzle flash is lost on first use. Hence
  `rt.prepare(defs)` at load plus a per-effect **pool** of stopped systems (reuse → emits on frame 1).
- **NullEngine**: a RawTexture's internal `isReady` stays false; the runtime sets it for its 4×4 white sheet. Await
  `rt.prepare` in tests (it polls with `setTimeout`, letting the shader imports resolve).
- `updateSpeed = 1/60` + `scene.getAnimationRatio()` ⇒ lifetimes, emit power and gravity are per second.
- `emitPower` 0 zeroes the direction: stretched billboards need speed > 0 (validator).
- Size gradients replace size (factor1..factor2 picked per key); the runtime feeds size_m × size_curve into them.
- Drag gradients scale the per-frame displacement, not velocity; the runtime maps `drag` (1/s) to a velocity gradient
  `exp(-drag·t·life_mean)`.
- **`translationPivot` is a trap**: the billboard shaders compute `(offset-0.5-pivot)*size` and then add `pivot` again
  unscaled (metres). `[0,-0.5]` moved every stretched quad 0.5 m backwards (sparks behind the gun). The runtime shifts the
  spawn point instead.
- **Sheet rows**: the ANIMATESHEET path uses `vUV = (offset.x, 1-offset.y) + (col,row)`, so with Babylon's default
  `invertY=true` rows count from the image bottom (a fixed casing frame showed a concrete chip; smoke rows played
  reversed). Particle sheets load with `invertY=false` so cell 0 is top-left, as in Godot.
- `engine.readPixels` after a DefaultRenderingPipeline can read a stale target: the harness decodes the canvas.
- Particles get the scene's image processing and fog; the grade applies to them like everything else.

## Engine asks (the skill does not edit engine code)
1. **`packages/engine/src/render/vfx.ts`**: adopt `tools/runtime/vfx_babylon.mjs` (typed). Load
   `export/vfx/*.json` via the manifest (`category: vfx`), `prepare()` them at level load, keep the pool.
2. **Event dispatch → spawn at socket.** The first-person anim layer fires `meta.fp.events` on frame crossings (also
   on loop wrap and refire): `rt.fire(event, socketsOfHeldItem)`. Third person: the same events from the
   third-person fire clip, at the world weapon's sockets (`Rig.attach` + `ig_grip_r`). Render first-person effects
   in the view-model layer (same camera/FOV as the arms), others in the world.
3. **Impacts + decal pairing.** On a hit: `surface` from `ig_material` (`SoundMaterial`), pick the effect whose
   `hooks.surface` matches (fallback concrete), `rt.spawnAtHit(def, point, normal, { distance, ambient })` and
   `DecalSystem.spawn(hooks.decal.id, pick)` (`place: ground_below` = ray down from the hit, as `rebel.ts` does for
   blood); play `hooks.sfx`. Replace the `tracer()` line in `apps/client/src/game/rebel.ts` with a subtle tracer
   effect (next tier). A ricochet direction (reflect the shot about the normal) for metal sparks is optional.
4. **Pooling and budgets.** Keep the pool per effect (`poolSize` 4+, more for SMG bursts); enforce
   `spec.vfx.scene_budget` (max alive particles, active systems, ≤ 2 VFX lights: the oldest or farthest light hook
   drops first; only the first 8 lamps get real lights today).
5. **LOD and light level.** Pass `distance` (camera → spawn) for `lod.reduce_m/cull_m`, and `ambient` (light level at
   the spawn point: lightmap/probe sample or a day/curfew constant) for `lit` emitters.
6. **Shell casings.** `hooks.mesh` asks for a physics casing (`wpn_casing_9mm_a`, 8 g, ~6 s life, `sfx_land`); until
   the mesh exists the sprite fallback emitter plays. Mesh owner: game-asset-modeling (wpn_ family).
7. **CCTV.** Feeds render at 12 fps, so 2-frame flashes are usually skipped on the Governor's monitors; the sim's
   `shots_fired` incident remains the source of truth. Optionally hold the light hook for one feed frame.
8. **Godot port** (`apps/godot`): map `ig_vfx` per `references/format.md` (GPUParticles3D + ParticleProcessMaterial),
   same prepare/pool rules (Godot compiles particle shaders on first draw too).
9. **Tracer** (`vfx_tracer`, class `tracer`): replace the `tracer()` mesh in `rebel.ts` with
   `rt.spawn(def, { position: muzzle, rotation: lookAt(hit) })` (not the muzzle's own -Z: the shot follows the aim
   ray), and clip the streak's life to `hitDistance / speed` per spawn (a runtime `lifeScale` option). Spawn it on some
   shots, not every one (suggest ~1 in 3 in SMG bursts); third-person shooters too. No light (the muzzle flash owns it).
10. **Glass exit spray.** A round passes through glass: spawn `vfx_impact_glass` again at the exit point with
    normal = shot direction (most real fragments leave the far side).
11. **Particles vs transparent meshes.** Babylon draws particles BEFORE transparent meshes in a rendering group, so a
    BLEND glass pane hides shards flying toward the camera. Render VFX in rendering group 1 with
    `scene.setRenderingAutoClearDepthStencil(1, false, false, false)` (the review harness does this for BLEND surfaces).
12. **Sabotage.** `cut` (anim_fp_hands cut_wire, frame 24) fires `vfx_sabotage_sparks` at the wirecutters' `socket_cut`
    in first person; third person (other rebels, AI) needs the same event from a third-person cut clip, or a spawn at
    the cutter's hand when the sim logs `sabotage`. At the same moment the client swaps `state_camera_*_intact` →
    `_sabotaged` and the feed goes to static.
13. **Sabotaged feed static (shader in `feed.ts`)**, not a particle effect. Today `sabotaged` is a hard 0/1 switch to
    full-frame grey noise up to 0.9. Ask: (a) a `sabotagedAt` time so the cut plays a 0.3-0.6 s death (the picture
    tears into 3-6 horizontal bands, vertical hold rolls once, brightness collapses toward a line, then static);
    (b) settled static keeps the feed's greyscale-green cast and scanlines, values 0.08-0.7 (S3: no pure white or
    black), slow darker horizontal bands, a faint roll bar every ~4 s; (c) timestamp and camera ID stay burned in over
    the static (they come from the recorder), so the Governor sees which camera died and when; (d) noise refreshes at
    the feed rate (12 fps). Propaganda-screen static and CRT rolls are a separate item.

## Gas, fire, chimney, cigarette asks (round 2)
14. **Gameplay-driven loops.** The sim owns gas volumes and fires; the effect only shows them. Proposed sim state per volume:
   `GasVolume { centre, radius_m, density 0..1, venting bool, wind }`, `Fire { centre, radius_m, fuel 0..1, prop? }`, a district
   `weather.wind` (m/s XZ). Each tick: `inst.setInputs(...)`, `inst.moveTo(centre)` if it drifts, `inst.setDistance(d)` ~2 Hz,
   `inst.stop()` when it ends (drain, then pool). Nothing in packages/sim has gas or fire yet.
15. **Inside the gas.** When the camera is inside a gas volume's radius, add a one-layer screen tint (#C9CAC4 x density x ~0.55)
    and near-fade billows within ~1.5 m of the camera (soft/near-fade particles), instead of more particle layers. Inside the
    cloud the review measured ~5-6 full-screen quad layers on average; that is the game's worst overdraw case.
    15b (vfx_teargas round 3). Soft particles too: big camera-facing gas quads that cross the ground or a wall show a straight
    cut line inside the cloud; give bank/skirt a depth fade (~0.8 m; Godot `proximity_fade_enabled`, Babylon a depth-fade particle
    shader). A volume first seen late (join) should start full (Godot `spawn_loop(..., preprocess ~6 s)`). The sim now has gas
    (packages/sim systems/gas.ts: radius 1 -> 7 m, density 1 -> 0 over 40 s; no venting flag, clients derive `source`).
16. **Looping fire lights.** `hooks.light.loop` lights live as long as the fire; with `scene_budget.max_lights_from_vfx` = 2 the
    engine keeps the nearest/brightest fires' lights and drops the rest (a fire without its light still reads by its flames).
17. **Follow + hand-off for thrown items.** `ignite` (anim_fp_molotov_a light clip) spawns vfx_fire_molotov_rag at socket_fuse with
    `follow: true`; on `throw_release` keep it following the projectile's socket_fuse; on impact `stop()` it and spawn
    vfx_fire_molotov at the hit (normal) + `dec_scorch_molotov_a`. Spread to ig_flammable props spawns vfx_fire_barrel on them
    (inputs radius_m from the prop's top radius).
18. **Attach sockets that do not exist yet**: env_roof_chimney_stack_a `socket_flue_0..3` (game-asset-modeling); a `drag`/`exhale`
    event in anim_civ_life anim_smoke_loop (game-animation) for the cigarette ember and an exhale puff at the mouth.

## Anim-event vfx names → effect ids (round 4: first-person anim sets)
The `anim_fp_*.meta.json` events carry a `vfx` field, sometimes without the `vfx_` prefix. Resolve it in this order:
(1) an exact effect id; (2) an effect whose top-level `aliases` (brief + export, round 4) lists the name; (3) `vfx_` + name;
(4) the exception table below. Dispatch by this name, never by event name alone: `vfx_shell_eject` (round 1) still lists
`case_eject` in `attach.also`, so an event-name lookup would play it AND `vfx_case_revolver`.

| anim name | effect id | used by (clip: event @ socket) |
|---|---|---|
| `vfx_muzzle_pistol` | vfx_muzzle_pistol | pistol fire/fire_last: fire @ socket_muzzle |
| `vfx_muzzle_smg` | vfx_muzzle_smg | smg fire/fire_loop/fire_last: fire @ socket_muzzle |
| `vfx_muzzle_shotgun` | vfx_muzzle_shotgun | sawn-off fire @ socket_muzzle, fire_2 clip (event `fire`) @ socket_muzzle_2 |
| `vfx_muzzle_revolver` | vfx_muzzle_revolver | revolver fire: fire @ socket_muzzle (+ cylinder-gap emitters, local +Z 0.113) |
| `vfx_muzzle_rifle` | vfx_muzzle_rifle | rifle_hunting fire: fire @ socket_muzzle |
| `vfx_shell_pistol` | vfx_shell_pistol | pistol + smg: shell_eject @ socket_eject |
| `vfx_shell_rifle` | vfx_shell_rifle | rifle_hunting bolt, reload_start: shell_eject @ socket_eject (skip when the chamber was empty) |
| `vfx_shell_shotgun` | vfx_shell_shotgun | sawn-off reload: shell_extract ×2 @ socket_eject (only fired chambers) |
| `vfx_case_revolver` | vfx_case_revolver | revolver reload_eject: case_eject @ socket_eject (once per spent case) |
| `spark_small` | vfx_spark_small | molotov/pipe_bomb light: lighter_strike @ socket_hand_l |
| `lighter_flame` | vfx_lighter_flame | molotov/pipe_bomb light: lighter_flame @ socket_hand_l |
| `rag_fire` | **vfx_fire_molotov_rag** (exception: not `vfx_` + name) | molotov light: ignite @ socket_fuse |
| `smoke_puff_small` | vfx_smoke_puff_small | molotov cancel: extinguish @ socket_fuse |
| `fuse_sparks` | vfx_fuse_sparks | pipe_bomb light: ignite @ socket_fuse (follow; stop at detonation) |
| `paint_stream` | vfx_paint_stream | spraycan spray_start: spray_on, spray_loop: spray @ socket_nozzle (stop on spray_off) |
The anim event's own `sfx` and the effect's `hooks.sfx` can name the same sound: play the event's, once.

## Round 4 asks (casings, revolver, fuse, spray can)
19. **Per-calibre casings.** `hooks.mesh` ids `wpn_casing_9mm_a`, `wpn_casing_rifle_a`, `wpn_casing_shotgun_a`, `wpn_casing_revolver_a`
    (game-asset-modeling; origin at the centre of mass, long axis +Z): spawn at socket_eject with velocity along its -Z
    (`speed_mps`) and a random spin about an axis ⟂ to the case (`spin_dps`), `mass_kg`, despawn after `life_s`, `sfx_land` on first
    contact. While a mesh is missing from the manifest, the `role: mesh_fallback` sprite (t_vfx_shell_casings_a_bc) plays instead.
    Gameplay gates: rifle reload_start's shell_eject only if a case was chambered; the sawn-off's two shell_extract only for fired
    barrels; the revolver's case_eject once per spent chamber.
20. **Revolver socket_eject during reload_eject** is the gate; the case falls out under gravity (0.3-0.8 m/s along -Z). Nothing ejects on fire.
21. **Fuse.** `vfx_fuse_sparks` follows socket_fuse like the molotov rag (ask 17) and stops at `detonate` (`attach.stop_event`), the
    instant vfx_explosion_pipebomb spawns. Optional: slide the instance down the cord as it burns (fuse length / burn time from gameplay).
    wpn_pipe_bomb_a has no socket_hand_l for the lighter effects (same as round 3).
22. **Spray can.** `vfx_paint_stream` (class `spray`, round 4) spawns on spray_on with `follow: true` at socket_nozzle; the spray_loop
    clip's `spray`@0 fires on every wrap: keep the live instance, never stack; `stop()` on spray_off (`attach.stop_event`).
    (a) No particle collision in the runtime: clip the mist's life at the wall, lifeScale = nozzle-ray hit distance / 0.5 m (as the
    tracer ask 9). (b) `hooks.paint_decal`: raycast from socket_nozzle along -Z (`reach_m` 0.6) while spraying and progressively
    reveal the gameplay-chosen `dec_graffiti_*` / `dec_rebel_mark_*` glb decal at the hit (`reveal_s` = seconds of spray to finish);
    these are export/dec glb decals, not entries in materials/decals.json m_decal_runtime, so DecalSystem.spawn needs a glb-decal
    path (or the dec family adds runtime atlas regions). The finished mark is a sim fact (a graffiti incident) for the Governor.
    (c) The mist is tinted chalk-grey in data; a different paint colour needs a runtime tint input (not in ig_vfx v1).
23. **Round 4 leftovers.** (a) wpn_pipe_bomb_a `socket_fuse` -Z points up while the hooked cord's burning end points down:
    re-aim the socket along the cord tangent at its end (game-asset-modeling), then the fuse spits follow the cord. (b) A runtime
    rate-jitter option for loops (e.g. rate × random 0-2 on a 0.1-0.3 s step, shared with the light's flicker) for a fuse's
    sputter; ig_vfx v1 only has constant rates. (c) Muzzle light hooks still put 2-3 px of white specular on a view-model
    front sight at 0.2 m: clamp the near falloff, or exclude the view model from muzzle lights' specular. (d) Casings: once
    `wpn_casing_*_a` are in the manifest the fallback sprites retire automatically (validator `hooks.mesh_delivered`).

## Round 5 asks (screens, feeds, rain, breath, wet)
24. **Class `shader` adoption.** `render/feed.ts`: replace its inline `hash()` static and `sin()` scanlines with
    `references/shaders/ig_screen.glsl` (`ig_crt_uv` -> sample -> `ig_screen_static` -> `ig_crt_roll`), uniforms from
    `vfx_crt_roll` / `vfx_screen_static` preset `governor_feed` (defaults equal today's values: scan 0.15, roll 0.05/s,
    depth 0.15, wobble 0.004, barrel 0.08). `sabotaged` becomes `static_amount`, ramped 0 -> 1 over 0.4 s on a cut.
    Samplers: `t_vfx_screen_noise_e`, `t_vfx_screen_scanline_e`, `t_vfx_screen_roll_e` (WRAP, trilinear).
25. **Propaganda screen.** m_state_propaganda_screen_a_screen emissive = `ig_screen(VideoTexture/card, uv, ...)` with preset
    `state_propaganda_screen_a`; `static_amount` from district power/unrest (0..0.25) and 1 when its cable is cut (sfx_screen_static).
    Same for prop_dom_tv_crt_a (preset `prop_dom_tv_crt_a`). Beyond `lod.reduce_m` halve noise_fps and drop scanlines.
26. **Rain follows the camera.** One `vfx_rain` instance for the local player at (cam.x, groundY + 6.0, cam.z), normal down,
    `moveTo` every frame; `setInputs({ intensity, wind_mps })` from the weather; stop it indoors / under arcades (roof test or
    occlusion volumes). Splashes assume flat ground 5.97 m below the instance (ground raycast per frame for kerbs/steps).
    Feeds do not render the player's rain: a faint drizzle term in the feed shader (or a second instance at the feed camera) is
    a later ask. Scene budget: ~740 alive of the 4000.
27. **Wet surfaces.** A `MaterialPluginBase` calling `ig_wet_surface` (references/shaders/ig_wet.glsl) on ground/facade PBR
    materials, uniforms from the weather (`wetness` ramps up over ~3 min of rain, dries over ~10 min; `rain` = vfx_rain
    intensity); `porosity` per material (custom property, default 0.8; metal/glass 0); dec_puddle_a gets preset `puddle`
    (puddle = 1). `t_vfx_wet_ripple_n` is LINEAR data (gammaSpace false).
    Round 6 (puddle read): `puddle` is a DEPTH mask, not a switch: standing water fills where it exceeds 1.25 - wetness
    (so puddles grow as the street soaks) and the band outside is the darker soaked rim (`edge_darken`). Pass dec_puddle_a's
    alpha remapped `smoothstep(0.3, 0.58, a)` (its water core is a ~0.6-0.74), not a constant 1. Ground preset roughness is
    now a proportional film (`roughness_wet` 0.45 x 0.55..1.05 of the material's own), so only standing water is a mirror.
    **The puddles need something to reflect**: the engine's constant-radiance environment (engine.ts) makes them dark lenses.
    Ask: a reflection probe per street segment at ~1.5 m, box-projected (`boundingBoxSize`/`boundingBoxPosition`) to the
    facades, rendered at level load and on day/dusk/curfew changes, containing the sky dome (with some cloud structure) and
    the lamp heads' emissive; used as the ground and decal PBRs' `reflectionTexture`. The game-vfx review (`review.scene`)
    shows the target: facades, poles and the lamp head mirrored in still water; a soft sheen on damp ground.
    Round 7 (specular aliasing): the damp film lowers roughness (floored at 0.3) while the ground keeps its normal detail, so
    under lamps it can glitter pixel by pixel. The snippet now folds the normal-map variance into the film's roughness
    (Toksvig: `nTS` must arrive UNnormalised from a MIPMAPPED sample, `sample * 2 - 1`; its length < 1 is the variance).
    Ask: on every PBR the wet plugin touches set `PBRMaterial.enableSpecularAntiAliasing = true` (Babylon's geometric +
    normal-variance specular AA; reflection probes prefiltered, never per-pixel jittered: a jittered cone read as glitter in
    review). Godot 4: Project Settings `rendering/anti_aliasing/screen_space_roughness_limiter/enabled = true` (default on;
    amount 0.25, limit 0.18) and the normal map imported with mipmaps; the include's Toksvig term is kept as is.
28. **socket_mouth.** vfx_breath wants a head socket at the mouth (~[0, 1.58, 0.135] m on char_skeleton_ref, -Z = face forward):
    game-asset-modeling / game-animation. Until then: bone `head` + (0, -0.012, +0.09) m. Cadence: one exhale per ~4 s at rest,
    ~1.5 s running (input `exertion`), only when the weather is cold (< 7 C) and within `lod.cull_m` 14 m.
