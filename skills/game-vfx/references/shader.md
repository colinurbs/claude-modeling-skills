# Class `shader`: screen and surface treatments (round 5)

Some §8 effects are not particles: the propaganda screen's static, the CRT roll on the Governor's feeds, wet surfaces.
They ship as the same `ig_vfx` file with `emitters: []` and a `shader` block, tiling textures painted by `vfx_tex.py`
(`"tiling": true` sheets), and a **reference GLSL snippet** in `references/shaders/` that the review renders as-is.

## The `shader` block
| field | meaning |
|---|---|
| `type` | `screen` (emissive of a screen / a feed post-process) or `surface` (a PBR input modifier) - `spec.vfx.classes.shader.types` |
| `targets` | where it applies (`classes.shader.targets[type]`): `state_propaganda_screen_a`, `prop_dom_tv_crt_a`, `governor_feed`; `ground`, `puddle` |
| `reference`, `entry`, `helpers`, `chain` | snippet path (relative to the skill), its entry function, helpers, and the order effects compose in |
| `textures` | `{slot: sheet}`; the sampler is `u_<slot>`. `_e` = greyscale mask (sRGB ok), `_n` = linear DATA (no sRGB decode) |
| `uniforms` | `{name: {default, min, max, note}}` or `{type: "color", default: swatch|hex}`; the GLSL name is `u_<name>`; `time` is seconds |
| `presets` | `{target: {uniform: value}}` per target (must be inside the ranges) |
| `flicker` | `{hz, luma_step}`: worst full-field flicker; validated against `classes.shader.max_flash_hz` / `max_full_field_luma_step` (WCAG 2.3.1) |
| top-level `inputs` | which uniforms gameplay drives (same `inputs` schema as loops) |

## Porting
| | Babylon 9 | Godot 4 |
|---|---|---|
| screen (emissive) | `ShaderMaterial` on the screen primitive, or a `MaterialPluginBase` on its PBR (emissive = `ig_screen(...)`) | `shader_type spatial; render_mode unshaded;` `EMISSION = ig_screen(...)` |
| feed (post) | `PostProcess` fragment (engine `render/feed.ts`): replace its `hash()` static and `sin()` scanlines with the snippet | `shader_type canvas_item;` on the feed's `SubViewport` `TextureRect` |
| surface (wet) | `MaterialPluginBase` on PBRMaterial: call `ig_wet_surface` in `CUSTOM_FRAGMENT_UPDATE_ALBEDO` / `_MICROSURFACE` / before lights for the normal | spatial `.gdshader` include, call before writing `ALBEDO`, `ROUGHNESS`, `NORMAL_MAP` |
| samplers | `Texture` with `wrapU/V = WRAP`, trilinear; `_n`: `gammaSpace = false` | `uniform sampler2D u_x : repeat_enable, filter_linear_mipmap;` masks `source_color`; `_n` without `source_color` |
| `texture2D` | GLSL ES 1/3 (Babylon converts for WebGPU) | `texture` |

Surface snippets do their own specular AA (Toksvig: the unnormalised, mipmapped tangent normal's length folded into
roughness), so pass `nTS` before normalising; engines add theirs on top (Babylon `PBRMaterial.enableSpecularAntiAliasing`,
Godot `screen_space_roughness_limiter`). Review harnesses must sample reflections deterministically (fixed cone rotation,
enough samples, or a prefiltered env); a per-pixel jittered cone reads as glitter and blames the shader.

Rules the snippet keeps: no `#version`, no `main`, no engine names; every uniform in the data appears in the snippet
(validator `shader.uniforms_in_src`); output stays LDR between `black_level` and `white_level` (S3).

## Validation (`check_shader`, `check_tiling`)
`shader.type / targets / required_uniforms / uniform_range / colour / preset / reference / entry / uniforms_in_src /
samplers / photosensitive / tex_mb`, and per texture `tex.tiling_seam` (wrap step <= 2x the tile's own pixel step),
`tex.emissive_grey` and `tex.mask_range` (masks <= 240/255), `tex.data` info for `_n`.

## Review (`vfx_shader_review.py` + `tools/review/shoot_shader.mjs`)
The snippet is compiled in headless WebGL (one small page, SwiftShader, no Babylon scene) with a tiny `main`, so the
review proves it compiles and shows exactly what it does. Screens: the driving uniform swept (e.g. static 0..1) on the
propaganda card, a time strip, the Governor feed at its display size (2x the 320x240 content), the screen 9 m down a
street day/night, that street on CCTV, the other presets; a 24-frame 60 Hz series at the worst setting measures the
full-field luma step. Surface: a ground plane (ray-cast in the fragment, the project's asphalt PBR set) dry / damp /
soaked+rain, day and under a fluorescent floodlight at night, a puddle close-up over time, CCTV. Gates in `review.json`:
compile errors, clipped (near-white) and crushed (near-black) share <= `grade_check.max_clipped_fraction`, flicker step.
Harness simplifications: hand-written lighting for the wet plane (hemi + one sun, or one point light), no engine grade.
Opt-in `review.scene` (surface; vfx_wet): a lit street ray-cast in the fragment (`tools/review/shader_wet_scene.glsl`) on the
real ground-kit sets (`materials` slots a/c/s, default asphalt/setts/paving), dec_puddle_a's atlas alpha along the kerb plus
low-spot puddles, a procedural overcast sky, facades and three 6 m lamps; the reflected ray is traced against sky/facades/poles (sphere-light
GGX lobes capped at the lamp head's LDR radiance x F), engine fog and the post.ts grade; day / dusk (civic tungsten) / curfew
(state fluorescent) at eye height, a crouched puddle close-up still and over time, and the CCTV mast. It replaces the plane
rows (`keep_plane_rows: true` keeps them). Simplifications: no shadows; the ground is shaded once with the default uniforms.
The engine's own adoption is an engine ask (`engine-notes.md`).
