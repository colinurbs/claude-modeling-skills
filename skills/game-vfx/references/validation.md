# Validation (`scripts/validate_vfx.py`)

Runs on the exported `export/vfx/<id>.json` plus its sheets; thresholds come from `assets/spec.json` → `vfx`.
Levels: FAIL (gate), WARN (keep only with a sentence in the brief notes), INFO.

| check | what | threshold source |
|---|---|---|
| schema.format / id / class / field_* | format+version, id regex = file name, known class, typed top-level fields | `vfx.format`, `id_regex`, `classes` |
| schema.emitters / blend / billboard / shape | non-empty, unique names, allowed enums | `blend.allowed` |
| schema.range_* | life/size/speed are [min ≤ max], ≥ 0 | |
| schema.color / size_curve | gradient keys sorted, first t=0, last t=1, hex + alpha | |
| schema.spawn | bursts or rate.per_s | |
| schema.texture_ref / frames / frame_mode | sheet listed, frame range inside the grid | |
| schema.stretched_speed | stretched billboards need speed > 0 (their axis is the velocity) | |
| palette.ldr_* | no `intensity`, `emissive_scale`, `bloom`, `hdr` fields | S9 |
| tex.exists / name / rgba / size / cell | PNG, `t_vfx_*_(e|bc)`, RGBA, power of two ≤ max, grid divides, cell ≥ min | `textures` |
| tex.border_alpha | max alpha on any cell border ≤ 3/255 (no hard quad edges, no bleeding between cells) | `textures.border_alpha_max` |
| tex.empty_cells | every cell has coverage | |
| tex.bleed (WARN) | RGB under A=0 not much darker than the sprite (dark mip fringes) | |
| tex.emissive_grey | `_e` sheets greyscale (tinted by data) | |
| tex.palette | `_bc` cells: 95th-percentile saturation ≤ max (or dried blood), no pure white | `colour.non_emissive`, `colour.blood` |
| tex.memory_effect / memory_total | RGBA8+mips of the effect's sheets / all sheets | `textures.max_effect_tex_mb`, `max_total_tex_mb` |
| budget.particles | peak alive (life = max, per 1/60 s) ≤ class max | `classes.*.max_particles` |
| budget.fill | Σ quad area / view area at the class reference view ≤ class max | `overdraw_fp_max` / `overdraw_max` |
| budget.duration | every particle dead by `duration_s` (instances retire then) | |
| budget.class_duration | `duration_s` ≤ class max | `max_duration_s` |
| budget.flash_life | additive `flash` emitters live ≤ max (2-4 frames) | `max_flash_life_s` |
| loop.class / loop.rate | loop flag matches the class; loops spawn by rate only | |
| loop.fade_out (WARN) | last colour key alpha ≈ 0 (or `pop_out` with a reason) | |
| palette.add_emissive | additive only with `_e` sheets | `blend.add_only_for_emissive` |
| palette.emissive | add keys: hue 18-55°, sat ≤ 0.55 | `colour.emissive` (S6) |
| palette.non_emissive | alpha keys: sat ≤ 0.38 (smoke sheet ≤ 0.25), value 0.08-0.85 | `colour.non_emissive` (S3) |
| palette.blood | `blood: true` keys: near #5A1E1A, value ≤ 0.42 | `colour.blood` (S3/S13) |
| palette.accent_* | no key reads as ministry ochre or chalk (WARN for fire/explosion) | `palette.accent_rules` |
| hooks.event / socket | class-required animation event (in `animation.first_person.events.names` + hit/ignite/detonate) and socket | `classes.*.events/sockets` |
| hooks.surface / decal / decal_place / sfx | impacts: surface enum, decal id in `materials/decals.json` m_decal_runtime, sfx name | `hooks` |
| hooks.light | intensity, range, duration, hue limits | `light_hook` (S9) |
| hooks.mesh / mesh_delivered (WARN) / mesh_fallback | mesh id pattern; if not in the manifest a `role: mesh_fallback` emitter must exist | |
| lod.fields | 0 < reduce_m < cull_m, 0 < factor ≤ 1 | `lod` |

The review adds measured gates (`review.json` → `gates`): measured peak particles ≤ budget, measured fill ≤ budget,
clipped share of the effect's pixels after the grade ≤ `grade_check.max_clipped_fraction`.

Round 2 (loops driven by gameplay): `inputs.range` (min ≤ default ≤ max, 0 < ref ≤ max), `inputs.drive` (known targets and
inputs), `inputs.worst_case` (no drive raises rate/size/alpha/intensity above the authored data at the input limits), and
`hooks.light` accepts a looping light (`loop: true` + `period_s`) on a looping effect instead of a flash `duration_s`.
`palette.emissive` also applies to unlit emitters on `_e` sheets with `blend: alpha` (an alpha-blended fire body is light, not a
surface): a fire body is alpha-blended so its orange survives the pale overcast street, with a small additive core on top.

Round 6 (fire look, spec `colour.emissive.fire_look`, classes listed there: fire): flame BODIES are unlit alpha-blended sprites
on a flame sheet (`flame_sheet_regex`), glow comes from a small additive CORE on top. `palette.fire_body` (INFO) replaces the
`palette.alpha_on_emissive` WARN for those bodies; the ministry-ochre accent rule is skipped for keys of unlit `_e` emitters in
the class (the light, S6), not for lit/smoke keys. `palette.fire_core_share` (FAIL): additive camera-billboard flame-sheet
emitters cover <= `core_max_area_share` of the steady flame area (rate x mean life x (mean size x mean size_curve)^2; rate-window
emitters and stretched sparks excluded; an all-additive flame is 100% and fails). `palette.fire_core_add` (FAIL): every core key
value x alpha <= `core_max_add` (brightness ceiling under the S9 grade; the review's clipped gate stays the measured check).

Playback isolation: each `vfx_cli.py playback` runs in its own `_work/playback/run-<time>-<pid>/` on a snapshot of `export/vfx/*.json`, spec and decals taken at start (safe alongside other agents' builds and runs; kept on FAIL, removed on PASS; `_work/playback/playback.log` = latest run).
