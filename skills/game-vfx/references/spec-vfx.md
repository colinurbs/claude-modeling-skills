# The `vfx` section of `assets/spec.json`

Transcribed from the project's delivery doc (irongrip: ASSETS.md §0.3/§0.4/§0.13, §8, S3, S6, S9, S13). The document
wins on conflict; when a threshold proves wrong, change it here with a `*_note` saying why (as the pilot did for
`textures.max_effect_tex_mb` 2.0 → 2.5 and `classes.muzzle.max_duration_s` 1.2 → 2.0).

| key | meaning |
|---|---|
| `owner` | claims owner for `vfx_*` (irongrip: `irongrip-vfx`) |
| `format`, `version` | `ig_vfx`, 1 |
| `id_regex`, `texture_regex` | `vfx_<name>`; `t_vfx_<sheet>_(e|bc)` |
| `src_dir`, `export_dir`, `tex_export_dir`, `sheets_file`, `brief_file` | `src/vfx/<id>/vfx.json`, `export/vfx/<id>.json`, `export/vfx/tex/`, `src/vfx/sheets.json` |
| `frame` | effect-local axes (-Z out, +Y up, glTF) |
| `classes.<c>` | `max_particles`, `max_duration_s` (0 = loop), `loop`, `overdraw_max` / `overdraw_fp_max`, `max_flash_life_s`, `required_hooks`, allowed `events` and `sockets` |
| `scene_budget` | engine-wide alive particles, active systems, VFX lights |
| `textures` | power of two, `max_px`, `min_cell_px`, per-effect and total MB (RGBA8 + mips), `border_alpha_max` |
| `blend` | allowed modes; additive only for emissive sheets |
| `colour` | LDR; non-emissive sat/value window; smoke/dust sat; emissive hue window and sat; blood rule; accent rules from `palette` |
| `light_hook` | max intensity, range, duration, hue |
| `grade_check` | max clipped share of the effect's pixels after the grade (blow-out) |
| `lod` | required fields |
| `hooks` | surfaces (= engine `SoundMaterial`), decal table, sfx and mesh name patterns, where event names come from |
| `review` | first-person FOVs, CCTV mount, day/night lighting (+ `ambient` for lit emitters), grade parameters, frame size |

## Adding it to a new project
Copy irongrip's block, then: palette swatches and accent rules come from the project's `palette`; event names
from `animation.first_person.events.names` (or list them under `hooks.events`); decal table path; lighting and grade
values from the engine's scene setup and post chain; budgets from the target hardware. Write the brief template's
default sizes in the project's units.
