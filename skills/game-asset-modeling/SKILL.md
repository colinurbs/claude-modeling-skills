---
name: game-asset-modeling
description: Build game-ready 3D assets in Blender and ship them as engine-ready glTF — environment and modular kit pieces, props, physics/breakable props, Governor/state apparatus, vehicles, weapons and held items, decals, and character meshes (bodies, heads, outfits). Covers the whole pipeline: asset brief, placeholder, form, game topology to a triangle budget, UVs and lightmap UVs, PBR textures (baked, tiling, trim sheet), LODs, collision, pivots, naming, custom properties, sockets and states, export, automated validation against the project's delivery spec, and review renders in the target engine (Babylon by default). Use it whenever anything is being modeled FOR A GAME or engine — "make the crate / facade piece / lamp / van / pistol / outfit", "grey-box the kit", "placeholder for X", "export to glb", "add LODs / collision", "does this asset meet the spec", ASSETS.md or a manifest entry — even if the user just says "model X" inside a game project. For matching the exact form of a recognizable real object it pulls in the reference-modeling skill for the form stage; this skill still owns everything that makes it a game asset.
---

# game-asset-modeling

A game asset is a **contract**, not a picture. It is done when it:

1. **meets the project's delivery spec exactly**: scale, axes, pivot, naming, LODs, collider,
   UV sets, texture packing, custom properties, file layout, manifest. The engine code
   assumes these, and an asset that breaks them doesn't load or behaves wrong.
2. **fits its budget**: triangles, texture size, texel density, material count.
3. **reads right where players actually see it**, in the engine's renderer. That means first
   person at eye height, the commander's iso camera, a low-res CCTV feed, or a hand in front
   of the camera. It does not mean a Cycles beauty shot.

The spec is checked by scripts. Form and style are checked by looking at engine renders,
yourself and through a fresh-eyes critic. Neither replaces the other.

## Relationship to other skills

- **reference-modeling** is the form tool for recognizable real-world objects (a specific
  tram, van, pistol, lamp standard, phone booth). Use its Phase 0–3 (references, proportion
  gate, silhouette IoU, 3D form) *inside* this skill's Form stage, then come back here for
  game finishing. Don't use its Phase 5 materials: Cycles-only procedural shading doesn't
  survive glTF export. Bake it.
- If Blender MCP is connected, use it to *look* (viewport screenshots) while iterating on
  form. The **build itself always runs as a headless script** so it is reproducible and
  runs in parallel.

## Setup

- **Tools, once per machine:** `~/.claude/skills/game-asset-modeling/tools/setup.sh`
  installs Babylon, Playwright and the Khronos glTF validator. It also needs Blender 4.2+
  (tested on 5.1) and python3 with numpy, pillow and opencv.
- **Project spec, once per project:** the scripts read `<project>/assets/spec.json`, which
  is the machine-readable form of the project's delivery-spec document (for irongrip:
  ASSETS.md §0 plus Style Guide S3/S13). If it's missing, build it first, following
  `references/project-spec.md`. When the document and spec.json disagree, the document
  wins: fix spec.json and say so. Spec contradictions (e.g. a floor height that isn't on the
  grid) go to the user. Don't silently pick one.
- **Material library:** `<project>/assets/materials.json` plus `assets/src/tex/`. Kit and
  environment assets draw from it; see `references/materials.md`. Build the library
  *before* the kit, because interiors and props are cheap once it exists.

## The per-asset loop

All paths below are relative to the skill. `gam_cli.py` is `scripts/gam_cli.py`.

### 0. Brief (always first, ~5 minutes)
`gam_cli.py new <id> --category <cat> --track <track>` scaffolds
`assets/src/<dir>/<id>/brief.json` and `build_<id>.py`. Fill the brief before modeling:

- **what**: what it is, where it stands, who touches it
- **dims**: target size in meters (Blender axes: x width, y depth, z height), with their
  source. Use the spec's reference dimensions and real-world standards, never guesses.
- **pivot, collider, props**: category defaults from the spec, overridden when needed
- **variants/states/sockets**
- **views.primary**: the view that decides how much detail matters
- **style_notes**: which weathering layer it belongs to, its palette role, and which
  "Never" rules it risks

The brief drives the validator (dims, props, budget classes), so a vague brief means weak
checks.

### 1. Placeholder (the first deliverable, not a throwaway)
Set `"status": "placeholder"`. The template builds a grey box at the brief's dims, with the
right pivot, `_col` and props. Run `gam_cli.py all brief.json`. It exports to
`export/_placeholder/`, validates in placeholder mode, and upserts the manifest. Engine and
level work unblock immediately. Grey-box a whole family before beautifying any member of it.

### 2. Form: pick the track
| Track | For | Form method | Recipe |
|---|---|---|---|
| `kit` | modular architecture, ground, barriers, anything that tiles | grid-snapped bmesh construction, trim-sheet UVs, seam-matched edges | tracks.md §kit |
| `invented` | generic period objects with no single real original (crate, bench, checkpoint booth, loudspeaker cluster) | dims from standards + type references; bmesh parts + bevel | tracks.md §invented |
| `real` | recognizable real-type objects (tram, van, rifle, street lamp model) | **reference-modeling** Phases 0–3, then return | tracks.md §real |
| `character` | bodies, heads, outfits, riot gear | bought/CC base conformed to the reference skeleton; outfits as separate skinned meshes | characters.md |
| `placeholder` | stage 1 only | box at dims | template |

Author in world space, 1 unit = 1 m, Z up, **front facing −Y**, near the origin. Build
from **gam primitives** into a bmesh: `bm_box` (with `skip=` for faces that will never be
seen), `bm_cylinder` (frustums, `phase=` so lower-LOD rings are vertex subsets),
`bm_sweep` (pipes, cables, bent brackets along a polyline), `bm_prism` (any planar
profile extruded: gussets, plates, signs), `bm_profile_sweep` (a moulding profile along a
path with mitred corners, UVs straight into a trim-sheet strip: cornices, sills,
architraves around openings, kerbs, handrails), placed with `rotate_about` or `matrix=`.
All of them write outward normals and **UVs in meters**, so an assembly packs at a single
density with `gam.uv_pack`. Group parts with `gam.tag_parts` when you'll need them later
(breakage, states, LOD drop lists). Every form decision lives in the build script, so
re-running it reproduces the asset exactly. Don't model faces nobody can see: caps inside
other parts, undersides on the ground. They waste texels and bake black (validator WARN).
If Blender MCP is connected, screenshotting the viewport mid-build is fine. The review
harness is still the judge.

### 3. Game mesh
Mid-poly hard surface is the default: real bevels (`gam.bevel`, 1–2 segments) plus
weighted normals (`gam.shade_hard_surface`), with no normal-map bake needed. Bake from a
high poly only for sculpted or organic detail (cloth folds, faces, cast iron ornament, SDF
parts from reference-modeling). Stay within `budget.lod0`. Spend triangles on the
silhouette at the primary view distance, not on faces nobody sees (undersides, backs of
wall-mounted things). Details are in `references/game-mesh.md`.

### 4. UVs and materials
- UV0 (`UVMap`): unique/baked props → primitives' meter UVs + `gam.uv_pack` (fall back to
  `gam.uv_smart` only for meshes not built from primitives, as it cuts tubes into strips);
  tiling or kit materials → `gam.uv_world_box`; trim-sheet strips → `gam.uv_trim`.
- Texture size: **measure** after packing, with `gam.choose_texture_size(obj,
  ctx.density())`. The validator fails density outside ±tolerance in *both* directions,
  so too big a texture fails just like too small. `texture_size_for` is only a
  pre-unwrap estimate. If nothing fits, share UV space between identical parts or switch
  to a tiling material. Don't quietly drop the density.
- Only glTF-exportable node graphs survive: Principled BSDF with image textures, an ORM
  image through Separate Color, and a Normal Map node. Anything procedural gets **baked**
  (`gam.bake` for base colour, roughness, metallic, AO and normal, then `gam.pack_orm`) and
  the material is rebuilt from the images (`gam.material`).
- Apply the project's weathering and palette rules at this stage, not afterwards. See
  `references/materials.md`.

### 5. Game data: `gam.finish(ctx, obj, ...)`
One call does it all. It triangulates stray n-gons, moves the pivot per spec (everything
shifts with it), applies weighted normals, adds the lightmap UV when the category needs
it, builds LODs `<id>_lod0..2` and the collider `<id>_col` (tagged `ig_collider`), then
puts everything under the root `<id>` with the brief's props. Pass in any extras:

- `lods=[lod1, lod2]`: hand-built lower LODs, used when collapse decimation shreds thin
  parts (brackets, rails, posts). Rebuild with the same part functions, using fewer sides
  (same `phase`), no bevels and small parts dropped, then `gam.transfer_uvs(lod0, lodN)`
  so they reuse the LOD0 texture. Bake AO with parts that some LOD drops moved aside
  (`gam.moved_faces`), or the lower LOD shows black where they sat.
- `collider_src=`: a simplified hand-built collider (required for kit pieces, which
  shouldn't use a decimated render mesh)
- `lod_group="_intact"` plus a `<id>_sabotaged` mesh in extras for state assets (recipe in
  tracks.md §state apparatus)
- `extra_children=[...]`: sockets (`gam.socket`), state meshes,
  a `_broken` group (`gam.make_broken`, or `gam.voronoi_fracture` for solids), hinges
- engine-space vectors in props must go through `gam.to_gltf(v)`, because the exporter
  doesn't convert custom-property coordinates

### 6. Export, validate, review
`gam_cli.py all <brief.json>` runs build → validate → review → manifest and stops at the
first failure. The steps can also run on their own (`build`, `validate`, `review`,
`manifest`).

- **validate** (`scripts/validate_glb.py`) checks the .glb against spec.json and the
  brief: naming, one root, applied transforms, pivot rule, dims, grid, budget, LOD ratios,
  collider presence, kind and not-the-render-mesh, required and typed props, sockets and
  states, broken-piece colliders, normals, UV0, lightmap UV overlap and padding,
  materials, alpha modes, ORM packing, texture power-of-two, size, format and naming,
  albedo range over used texels, palette accent rules, texel density, and the Khronos
  validator. Every FAIL has to be fixed. Read WARN and INFO lines too, since they often
  point at the real cause.
- **review** (`tools/review/shoot.mjs`) loads the .glb in Babylon: overcast HDRI, the
  project's fog and ground, and a 1.75 m mannequin. It renders front, three-quarter, eye
  height, iso (framed and at gameplay scale), CCTV feed, LOD strip, collision overlay,
  UV-density checker, and the sabotaged and broken states when present. Output goes to
  `assets/review/<id>.png` plus `assets/review/<id>/review.json`, which records engine
  console errors and the extras exactly as Babylon sees them. Engine errors fail the
  review.

### 7. Look, critique, iterate
Kit pieces and anything that sits above the ground should add `review_context` to the
brief: `{"tile": [3, 3], "elevation": 4.0}`. Ground and floor tiles should use
`{"tile": [4, 4], "plane": "ground"}`, which tiles flat instead of stacking upward. The piece is then tiled at its real
placement height over a grey stand-in, which adds `ctx_front`, `ctx_street` (looking up
from the pavement), `ctx_three_quarter`, `ctx_iso_context` and `ctx_feed`. This is where
seams, repetition and real viewing angles get judged. On its own, an upper-floor piece is
a speck on the ground.
`review_with` entries can be plain ids or paths (loaded at the origin), or
`{"id": "dec_grime_drip_a", "offset": [x, y, z], "rot_z": 90}` in Blender axes. Use the
second form to put an insert on its host's socket, a decal on a wall, or siblings side by
side.
Wall- or ceiling-mounted pieces (pivot at the mount plate, geometry hanging below it)
should add `"review_offset": [0, 0, <mount height>]` so they are reviewed where they hang
instead of under the ground plane.
Anything that emits (lamps, lit windows, screens) should add `"night"` to the brief's
`review_views`: the scene goes near-dark, so the emissive reads as it will at curfew.
Held items (weapons, tools, papers) should add `"review_held": true` to the brief, or
`{"lift": 1.02, "grip": "pistol_grip", "hands": 1}`; grip and hands default to the root's
`ig_grip`/`ig_hands`. The item is lifted to hand height, and the review adds `fp`/`fp_close`
(the first-person view-model camera) and item-only `item_side_l/r`, `item_top`,
`item_front`. At full frame a 0.2 m item is a speck next to the mannequin.
For tall or wide assets, add `review_focus` to the brief (`[{"name": "head", "center":
[x, y, z] in Blender axes, "radius": 0.4}]`). The review then renders close-ups from two
sides, and in both states when states exist. The standard views frame the whole asset, so
a 0.5 m camera head on a 3.7 m pole ends up only about 40 px tall.
Read the sheet yourself first, then give it to a **fresh-eyes critic subagent**: the sheet,
the individual views, the brief, the style-guide excerpt, and the validate output. The
prompt is in `references/review.md`. The critic returns a ranked delta list. Fix the
biggest item, rebuild, and repeat until the critic has nothing above "minor" and validate
passes. Don't stop because it "looks good". Stop because the list is empty or only
cosmetic.

### 8. Deliver
Set `status` to `done` in the brief, then run `gam_cli.py all` one more time, which
refreshes the export, textures in `src/tex/`, the review sheet and the manifest. Anything
not made from scratch (a CC0 texture, a bought base mesh) gets a `LICENSES.md` row. If the
project has its own asset check (irongrip: `pnpm validate-assets`, which checks the
manifest, paths, licenses and review screenshots), run it too. Report back with the numbers (tris per LOD, texture sizes, density, validate result), the sheet
path, and any spec questions you hit.

## Batch production

- Work by **family**, not by item: every window variant, or every barrier, shares one
  build module (`assets/src/<dir>/_family_<name>.py`) that the per-asset scripts import
  with different parameters. Consistency comes free and fixes propagate.
- Order: placeholders for the whole tier, then the material library and trim sheet, then
  one hero per family taken all the way to done and critiqued, then the rest of the family.
- Parallelism: builds are separate headless Blender processes, so run several
  `gam_cli.py all` at once (4–6 on a laptop; bakes are the heavy part). If you're using
  subagents, give each one a family and have it follow this skill. Never have two agents
  write the same asset or the same library file.
- **Tiny builds** (decals, cards, 2–200-tri pieces) set `"light_build": true` in the brief.
  They then use their own 2-slot lane and never queue behind other agents' bakes.
- **Batch cheap builds:** `gam_cli.py build a/brief.json b/brief.json ...` holds one machine
  slot for the whole batch. Kit pieces that build in 10 s otherwise wait minutes behind
  other agents' bakes.
- **Claims:** `assets/claims.json` maps id patterns to owners, so two sessions don't build
  the same family. Before starting a family, check `gam_cli.py claim --list` and then
  `gam_cli.py claim '<pattern>' --owner <you>`. Export `GAM_OWNER=<you>` in every shell,
  because `new` and `build` refuse ids that another owner has claimed. First match wins,
  so put narrow patterns before broad ones.
- `gam_cli.py status` shows briefs vs exports vs validate results vs manifest.

## Gotchas (all hit for real)

- **The exporter doesn't convert custom-property vectors.** Hinges, axes and footprints
  must be given in glTF space (`gam.to_gltf`). Node transforms *are* converted.
- **Anything overlapping the bake target ruins AO.** Coincident broken pieces, LOD copies
  and colliders all catch the rays. `gam.bake` hides every other object from render.
  Factory-empty scenes have no world, and AO needs one; `gam.bake` creates it. A
  near-black AO map (validator WARN) means a failed bake, not a dark prop.
- **glTF UV origin is the image's top-left.** Blender flips V on export. Anything that
  rasterizes glTF UVs must use `y = v * h`.
- **Broken pieces must inherit the intact mesh's UVs**: `gam.tag_parts` before
  `join`, then `gam.split_parts` after texturing. Re-unwrapping the pieces makes them
  reuse the texture wrongly.
- **Stale view-layer entries after data-level deletes** break `select_set`. Use
  `gam.select_only`, which refreshes the view layer.
- **A mesh named exactly `<id>`** would shadow the root empty. Name render meshes
  `<id>_lod0` from the start; `gam.root` evicts squatters.
- **Validator LOD ratios and dims only measure `_lod0`.** Items with separate moving-part
  child nodes (slides, bolts, lids) get ratios against the static body, and dims that leave
  out the parts. Put the whole-item size in the brief notes and check it in the review.
- **Inserts and leaves** (doors, window inserts, gate leaves) are real-world sized (a
  0.9 × 2.1 m door) and sit inside an on-grid host piece. Put `"grid": false` in their
  brief, and attach them to the host through a socket (`socket_opening`, `socket_door`).
- **Density on shared library materials** (trim sheets, tiling sets) fails only when it is
  under target, and warns when over. Extra density there costs no memory. Unique per-asset
  textures fail in both directions. Non-square textures are measured as sqrt(w·h).
- **Seam-absolute mouldings:** `bm_profile_sweep(..., u_axis="x")` takes U from world X, so
  a cornice continues across neighbouring modules. `u_start` offsets relative U.
- **Texture size follows density, not habit.** A 0.8 m slatted crate at 512 px/m needs
  2048² because its interior faces are visible. Check `texture_size_for` before baking,
  and consider shared UVs or tiling wood when it gets expensive.
- **Unused texels are black after a bake.** The margin (`size/64` px) dilates islands so
  mipmaps don't bleed black into the edges. The validator's albedo check ignores texels
  outside the UVs.
- **The review renderer is SwiftShader** (software WebGL), so absolute performance means
  nothing there. Use it for look and correctness only.
- `Material.use_nodes` is deprecated in Blender 5.x; `gam.material` only sets it when
  needed. It also turns on backface culling (the exporter writes `doubleSided` from
  that); pass `double_sided=True` only for real cards (foliage, flags, paper).
- **`me.materials.clear()` resets every face's `material_index` to 0** (Blender 5.x). Swap
  slots with `gam.replace_material(obj, old, new)` instead.
- **`shade_hard_surface`, `bevel` and `apply_modifiers` replace `obj.data`.** Re-read
  `obj.data` after calling them; a held `me` reference raises "StructRNA ... removed".
- **Bevel wider than a thin part** leaves sliver faces that bake black (albedo FAIL). Keep
  bevel width under half the thinnest dimension, or bevel those parts separately.
- **Temporary bakes go in `ctx.bake_dir`** (per asset), never in the shared `src/tex`.
  Parallel agents with fixed names like `ao_tmp.png` overwrite each other.
- **Blender's Python has no cv2 or PIL.** Write arrays with `gam.save_png`; do real texture
  authoring in CPython with `tex_tools`.
- **`voronoi_fracture` caps holes with `holes_fill`,** which spans the cavity of hollow
  meshes (barrels, bins). For hollow objects, fill each cut loop separately
  (`bmesh.ops.triangle_fill` per boundary loop), or use part-based breakage.
- **A killed build can leave a stale `.blend`** that makes the next export fail with
  "Cannot change old file". Delete the asset's `.blend` and rebuild.
- **`gam.join` works at the data level and drops vertex-group names.** Don't use it on
  skinned meshes; join those with Blender's join operator, or keep them as separate
  primitives under one armature.
- **`gam.material(name)` returns the existing material if the name is taken.** Build the
  procedural bake source under a different name (`m_src_*`) from the final `m_<id>`, or
  you get `m_<id>.001` (validator FAIL).
- **Passing validation doesn't mean it looks right.** In the first test, validation passed
  while a camera's broken state was unreadable, its smashed window rendered pitch black,
  and a LOD showed a black patch. The critic and the close-ups caught all three.
- Texture names: `t_<asset id>_<bc|n|orm|e>` for unique sets,
  `t_<library name>_<slot>` for library materials.

## Files

| Path | What |
|---|---|
| `scripts/gam.py` | Blender-side library: scene, bmesh parts, pivots, shading, UVs, texel density, materials, baking, ORM packing, LODs, colliders, sockets, fracture and part breakage, `finish`, `export` |
| `scripts/gam_cli.py` | Pipeline driver: `new`, `build`, `validate`, `review`, `manifest`, `all`, `status` |
| `scripts/validate_glb.py` | Spec and brief validator (CPython; runs Khronos too) |
| `scripts/tex_tools.py` | Texture authoring (CPython): CC0 fetch (Poly Haven, ambientCG) with a cache outside the project, tileable noise and blur, grading, height→normal (OpenGL), normal blend and flip, ORM pack, LICENSES rows |
| `tools/review/shoot.mjs`, `harness.html` | Babylon review renders plus engine-side load check |
| `tools/setup.sh` | Installs node tooling |
| `templates/brief.json`, `templates/build_asset.py`, `templates/spec.default.json` | Scaffolding |
| `references/project-spec.md` | Turning a project's delivery-spec document into spec.json; field reference |
| `references/tracks.md` | Kit, invented, real, and category notes (vehicles, weapons, state apparatus, decals, physics) |
| `references/game-mesh.md` | Topology, budgets, bevel versus bake, LODs, colliders, breakage, sockets and hinges |
| `references/materials.md` | Material library, trim sheets, CC0 sourcing, baking, ORM, weathering layers, palette, decals, vertex colour |
| `references/characters.md` | Skeleton conformance, weights, blendshapes, outfits, heads and portraits, LODs for crowds |
| `references/review.md` | Reading the review sheet, the critic prompt, what to look for per view |
