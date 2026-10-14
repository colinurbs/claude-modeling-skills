# `ig_vfx` v1: the effect data format

One file per effect: `export/vfx/vfx_<id>.json`, built from the brief `src/vfx/<id>/vfx.json` by
`vfx_cli.py build` (the brief has the same fields plus `status`, `review` and free notes). Units: metres,
seconds, degrees. Axes: glTF (+Y up). **Effect-local frame = the socket's**: local **-Z is "out"** (the muzzle
direction, the eject direction, the surface normal for impacts), matching `gam.socket` empties (local -Z forward,
+Y up). Gravity and wind are world-space.

## Top level
| field | type | meaning |
|---|---|---|
| `format`, `version` | `"ig_vfx"`, 1 | written by build |
| `id` | `vfx_[a-z0-9_]+` | = file name |
| `class` | muzzle, shell, impact, smoke, fire, explosion, spark, ambient | picks budgets and required hooks (`spec.vfx.classes`) |
| `duration_s` | s | one-shots: the instance is retired (systems pooled) at this time; every particle must be dead by then |
| `loop` | bool | loops spawn by `rate` only and live until the gameplay stops them |
| `seed` | int | reserved for deterministic engines; the review seeds its RNG per sequence |
| `notes` | text | intent, copied to the manifest |
| `attach` | `{event, socket, also: [{event, socket}]}` | animation event (meta.fp.events) and the socket on the held item; `hit` for impacts |
| `hooks` | object | see below |
| `lod` | `{reduce_m, cull_m, reduce_factor}` | beyond reduce: counts × factor; beyond cull: not spawned |
| `emitters` | list | one particle system each |
| `textures` | `{name: {path, size, grid, kind}}` | written by build from `src/vfx/sheets.json` |
| `estimates` | object | written by build: `peak_alive`, `fill_peak`, `end_s`, `per_emitter_peak`, reference view |

## Hooks
| hook | fields | consumer |
|---|---|---|
| `light` | `color` (hex or swatch), `intensity`, `range_m`, `duration_s`, `curve` [[u, k]], `offset_m` (along -Z) | a short PointLight; the runtime creates and fades it |
| `sfx` | `sfx_<id>` | audio system (may not exist yet; validator only checks the name) |
| `decal` | `{id, place: surface|ground_below, chance}` | engine `DecalSystem.spawn(id, pick)`; id must exist in `materials/decals.json` |
| `surface` | wood, metal, concrete, glass, cloth, plastic, cardboard, flesh | the `ig_material` / `SoundMaterial` this impact is for |
| `mesh` | `{id, physics, mass_kg, speed_mps, spin_dps, life_s, sfx_land}` | engine spawns a physics mesh (shell casing); `role: mesh_fallback` emitters stand in until delivered |

## Emitter
| field | type | Babylon (`vfx_babylon.mjs`) | Godot 4 (GPUParticles3D + ParticleProcessMaterial) |
|---|---|---|---|
| `name` | str | system `<id>.<name>` | node name |
| `role` | flash, sparks, mesh_fallback, (none) | validator only (flash life cap) | n/a |
| `texture` | sheet name | `particleTexture` (shared, trilinear, clamp) | StandardMaterial3D albedo texture |
| `frames` | [first, last] cell | `startSpriteCellID` / `endSpriteCellID` | `particles_anim_offset` range via h/v frames |
| `frame_mode` | over_life, random, fixed | changeSpeed 1 / `spriteRandomStartCell` + speed 0 / start=end | `anim_speed` 1 over life / `anim_offset` random, speed 0 |
| `blend` | alpha, add | BLENDMODE_STANDARD / BLENDMODE_ADD | BLEND_MODE_MIX / BLEND_MODE_ADD, unshaded |
| `billboard` | camera, stretched, y_axis | BILLBOARDMODE_ALL / _STRETCHED / _Y | BILLBOARD_PARTICLES / particle_flag_align_y + velocity / BILLBOARD_FIXED_Y |
| `pivot` | [0, -0.5] = the quad's base sits at the spawn point | spawn point moved along the emission axis by half the mean quad length (NOT `translationPivot`: Babylon adds it again unscaled in metres) | mesh offset on the draw pass quad |
| `lit` | bool | colour × runtime `ambient` (scene light level) | shaded material, or modulate by a light-level uniform |
| `blood` | bool | validator: blood palette rule | n/a |
| `shape` | `{type: point|cone|sphere|hemisphere|disc|box, angle_deg, angle_min_deg, radius, size}` | `startDirectionFunction`/`startPositionFunction` about local -Z; `angle_min_deg` > 0 = hollow cone (directions between min and max, e.g. an explosion's ground skirt) | `direction` (0,0,-1) + `spread` (hollow cone: custom process shader or a ring emission shape with radial velocity) |
| `offset` | [x,y,z] local | added to spawn position | emission offset |
| `bursts` | [{t, count | [min,max]}] | instance clock → `manualEmitCount` | one_shot + explosiveness 1, or sub-emitters for several bursts |
| `rate` | {per_s, t0, t1} | instance clock → `manualEmitCount` accumulation | `amount`/`lifetime` with emitting window |
| `life_s` | [min, max] | min/maxLifeTime (updateSpeed 1/60 ⇒ seconds) | lifetime + lifetime_randomness |
| `speed_mps` | [min, max] | min/maxEmitPower × unit direction | initial_velocity_min/max |
| `drag` | 1/s (exponential) | velocity gradient exp(-drag·t·life_mean) | damping (linear; approximate as drag·speed/2) |
| `gravity` | [x,y,z] world | `gravity` | `gravity` |
| `size_m` | [min, max] | size gradient keys × size_curve (or min/maxSize) | scale_min/max × quad size |
| `size_curve` | [[u, k]] over life | `addSizeGradient` | `scale_curve` |
| `scale`, `stretch` | [min,max], length/width | min/maxScaleX, ScaleY × stretch | quad mesh aspect / `scale_curve` y |
| `rotation_deg`, `spin_dps` | ranges | initial rotation / angular speed | angle_min/max, angular_velocity |
| `color` | [[u, "#hex", alpha]] u 0..1, first 0, last 1 | `addColorGradient` | `color_ramp` GradientTexture1D |

Colours are LDR sRGB hex (no intensities > 1). `_e` sheets are greyscale and tinted by `color`; `_bc` sheets carry
their own colour and are tinted near-white (`#D8D8D8`) or by a palette colour when the sheet is neutral (smoke,
blood droplets).

## Runtime behaviour every port must keep
1. All times in seconds: Babylon systems run with `updateSpeed = 1/60` at the scene's animation ratio.
2. Emission is driven by the instance clock (bursts at `t`, rates in windows), not by the system's own timers.
3. `prepare(defs)` at load: textures and shaders ready, systems pooled. A fresh Babylon system needs ~2 frames before
   it emits; a muzzle flash lives 2-3.
4. Instances retire at `duration_s` (one-shots) and return their systems to a per-effect pool.
5. LOD by camera distance at spawn.

## Gameplay-driven loops: `inputs`, `drive`, `wind_gain`, loop lights (round 2: gas, fire, chimney)
A loop whose size or strength the sim owns (tear-gas volume, spreading fire) declares **runtime inputs** and never bakes a size:
- top level `inputs: {name: {default, min, max, ref, source}}` (scalars), or `{default: [x, z], type: "vec2"}` for `wind_mps`.
- emitter `drive: {rate|radius|size|alpha|speed: [[input, power], ...]}`: factor = Π (value / ref)^power, applied on spawn
  and on `inst.setInputs()` (rate × emission, radius × shape position, size × quad scale, alpha × colour keys, speed × emit power).
- **Author the data at the worst case**: `ref` is the limit that maximises count/size/alpha, so the build's estimates and the
  validator's budgets hold for every input (`inputs.worst_case`); smaller inputs only scale down.
- emitter `wind_gain`: world gravity += wind_mps (x, z) × wind_gain (drag-limited drift downwind).
- `hooks.light` on a loop: `loop: true`, `period_s`, periodic `curve`, `jitter` (smoothed random dips), `flicker_step_s`,
  optional `drive: [[input, power]]` for intensity; it lives as long as the instance and fades 0.6 s after `stop()`.
- `lod.share_n` (loops): beyond n live instances of the same effect, each emits × n / live (shared budget).
Runtime: `inst.setInputs({...})`, `inst.setDistance(d)` (cull = stop emitting, keep the instance), `inst.moveTo(p)`,
`spawnAtNode(def, node, { follow: true })` (a thrown bottle's socket), `inst.stop()` (drain, then pool). Rate windows (`t0`/`t1`)
give a loop a one-off start (the molotov splash) without bursts. Godot: same factors on `amount_ratio`, `emission_*` extents,
`scale_min/max`, the colour ramp alpha and `gravity`.

## Class `shader` (round 5: screens, feeds, wet surfaces)
`class: "shader"`, `emitters: []`, a `shader` block (`type` screen|surface, `targets`, `reference` snippet + `entry`,
`textures` {slot: tiling sheet}, `uniforms` with ranges, `presets` per target, `flicker`), top-level `inputs` for the
uniforms gameplay drives. Textures: `tiling: true` sheets; `_n` = linear data. Full field list, Babylon/Godot porting and
the review in `references/shader.md`; snippets in `references/shaders/`.

Round 6: emitter `local: true` (with a `follow` spawn): live particles are carried rigidly with the followed node (old world matrix -> new),
for tiny attached sprites that must not trail behind a moving socket (a cigarette ember in a moving hand). Babylon: the
reference runtime moves `ps.particles` by the node's per-step world-matrix change (engine: `isLocal`); Godot: `local_coords = true`.
