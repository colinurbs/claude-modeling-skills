# Materials, textures, weathering, palette

## What survives export

glTF metal/roughness only: Principled BSDF with Base Color (image or constant), an ORM image
through Separate Color (G → Roughness, B → Metallic, R → the `glTF Material Output` group's
Occlusion — `gam.material(orm=...)` wires all of it), a Normal Map node on an OpenGL
tangent-space image, Emission color/strength, Alpha. Anything else (noise, ramps, bevel
node, geometry-dependent masks) is ignored or breaks the export → **bake it**.

## Three ways to texture an asset

| Approach | When | How |
|---|---|---|
| **Library tiling / trim** | kit, ground, large surfaces, families of similar props | `uv_world_box` / `uv_trim` into shared `m_` materials from `materials.json` — highest density per byte, consistent across the city |
| **Unique baked** | props with specific wear, small hero objects | primitives' meter UVs → `uv_pack` (or `uv_smart` for non-primitive meshes) → procedural source material → `bake` channels → `pack_orm` → `material(base=..., orm=...)` |
| **Hybrid** | props that need both | tiling material for the bulk + small unique decal/atlas sheet for labels and wear, or vertex colour (COLOR_0 multiplies base colour in glTF) for AO/grime gradients |

Pick size by measuring the packed layout: `choose_texture_size(obj, density)` (fails are
two-sided: over-density fails too). If a unique bake wants > max px, the answer
is shared UV space (identical slats/legs overlap in UV — bake AO with overlaps off or skip
AO for them), a tiling material, or splitting into two material sets — not a quietly lower
density.

## Material library (`assets/materials.json`)

```json
{ "m_brick_ochre_a": { "base": "t_brick_ochre_a_bc.png", "orm": "t_brick_ochre_a_orm.png",
                       "normal": "t_brick_ochre_a_n.png", "tile_m": 2.0,
                       "source": "ambientCG Bricks076", "notes": "pre-state masonry" } }
```
`gam.material_from_lib(name, lib, ctx.tex_dir)` builds it; `tile_m` = meters per texture
repeat → `uv_world_box(obj, meters_per_tile=tile_m)`. Density = texture px / tile_m; pick
`tile_m` so it meets the class density (1024 px at 4 m = 256 px/m for the kit).

### Module-locked tiling (kit plaster, brick, render)
A kit wall material that repeats exactly once per module (u = x / 3.0 m, v = z / storey)
makes seams disappear, but **every distinctive feature in the tile repeats in every bay**:
a spall, a stain, a missing brick becomes a pattern at street level. Keep the tile
low-frequency and uniform; put unique damage on `dec_` decals placed at district assembly.
Density is then fixed by the module: a 1024 px tile over 3 m = 341 px/m. Choose the texture
size so this lands inside the spec tolerance rather than chasing the exact class value.
Author tiling maps with `tex_tools` (periodic noise, `pblur`, `make_tileable`): ordinary
blurs and non-periodic noise put a seam in every repeat.

### Sourcing CC0 sets (Poly Haven, ambientCG)
`tex_tools.fetch_polyhaven(asset)` / `fetch_ambientcg(asset)` download with a proper
User-Agent (Poly Haven returns 403 to Python's default) into a cache OUTSIDE the project
(`~/.cache/game-asset-modeling/cc0`; raw sets are hundreds of MB and must not land in
`assets/`). Write a project texture generator (`assets/src/tex/build_<set>.py`) that fetches,
grades, composites, packs and names — re-runnable like the mesh builds — and log each
source with `tex_tools.license_row`.
- Poly Haven: `https://api.polyhaven.com/files/<asset>` → `['Diffuse'|'nor_gl'|'Rough'|'AO'|'arm']['1k'|'2k']['png']['url']`
  (`arm` is already AO/rough/metal = ORM). Use `nor_gl` (OpenGL), not `nor_dx`.
- ambientCG: `https://ambientcg.com/api/v2/full_json?id=<Asset>&include=downloadData` → zip
  with `_Color`, `_NormalGL`, `_Roughness`, `_AmbientOcclusion`, `_Metalness`.
- Grade to the project style **before** it enters the library (irongrip S4: desaturate
  10–20%, pull contrast down, roughness high), resize to power of two, rename
  `t_<name>_<bc|n|orm|e>.png`, pack ORM, log source/license/author/date in `LICENSES.md`.
  Grading in numpy/PIL is enough: `rgb = mean + (rgb-mean)*(1-desat)`; contrast around the
  image mean.

### Trim sheet (kit)
One 2048 sheet of horizontal strips, each a tileable-in-U moulding/frame/sill/edge profile
with its own normal and ORM. Record strip v-ranges in materials.json
(`"strips": {"cornice": [0.0, 0.125], ...}`) and atlas rectangles in
`"regions": {"apron": [u0, v0, u1, v1]}` (Blender V), then map faces with
`bm_profile_sweep(strip=...)` or `uv_trim(obj, faces, v0, v1, meters_per_u)`. The validator
fails any triangle whose UVs bleed outside every declared strip or region of its material.
Leave ~6 px guard rows between strips. A trim sheet + 2–3 tiling sets is how a kit stays
under a small material-set cap. Glass or transparent library entries:
`"base_color": "#hex", "opacity": 0.35, "alpha": "BLEND"`.

## Weathering in layers (irongrip S4, general principle)

Model the history, in order: **original** material → **neglect** (grime at the base, drips
under sills, rust streaks from fixings, soot) → **later additions** (in irongrip: the
state's clean, too-new paint, brackets, notices — never weathered to match).

- Original: the library tiling set.
- Neglect: on unique props bake it in (AO-driven grime, vertical-stretched noise for drips:
  object-space noise with Z scale ≪ X/Y, masked by height and by AO); on kit pieces use
  (a) weathered strips in the trim sheet, (b) `dec_` decals placed at district assembly,
  (c) vertex colour darkening toward the base (COLOR_0 multiplies base colour).
- Additions: **separate geometry with its own clean material** (a bracket, a stripe, a
  plate, a laminated notice) — that the addition is a different, newer material is the
  point.

## Palette checks

The validator reads `spec.palette`: every material's mean base colour (texture over used
texels, or the constant) must stay under `max_saturation` unless an `accent_rule` matches,
and a rule's accent (e.g. ministry ochre, chalk) is only allowed on materials/assets that
match its regex (`m_state_*`, `state_*`, `m_rebel_*`...). Name materials by role so the
rules can see intent. Albedo must stay inside the sRGB limits on used texels (no pure
black/white). Roughness mean ≥ spec floor (warn).

## Baking notes

- `gam.bake(obj, channel, size, path)`: `Base Color`, `Roughness`, `Metallic`, `Alpha`,
  `Emission Color` via emission reroute (exact values, no lighting); `AO`, `NORMAL` via
  Cycles. Margin defaults to size/64 px.
- Other scene objects are hidden from render during the bake (they would occlude AO).
- `ao_distance` ≈ 0.2–0.5 m for props, 1–2 m for architecture.
- Delete temp single-channel PNGs after packing; ship only `_bc`, `_orm`, `_n`, `_e`.
- Emissive only where something emits (windows at night, screens, lamps) — the spec says so
  and the validator only checks names, so be deliberate.
