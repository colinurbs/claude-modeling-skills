# Game mesh: topology, budgets, LODs, colliders, breakage, sockets

## Budgets are a design input

Read `budget.lod0` from the spec before modeling and plan where the triangles go. A good
split for a prop: 60% silhouette (what the primary view sees against the sky/wall), 30%
readable secondary forms (panel lines, frames, handles), 10% everything else. Faces nobody
sees get deleted: undersides of things that sit on the ground, backs of wall-mounted
items, faces inside joins.

Primitives (`bm_box`, `bm_cylinder`, `bm_sweep`, `bm_prism`) take flags to omit faces
(`skip=`, `top=`, `bottom=`, `cap0/cap1=`) — use them for every face that sits inside
another part or against the ground.

Rough costs: a 1-segment beveled box ≈ 44 tris, 2-segment ≈ 108; a 16-sided cylinder with
caps ≈ 60; a 12-sided ≈ 44. Count before you add.

## Mid-poly hard surface (default)

- Real geometry bevels on edges that catch light at the primary view distance:
  `gam.bevel(obj, width, segments=1..2, angle_deg=30..40)`. Width guide: 2–4 mm small
  props, 5–10 mm furniture, 15–30 mm architecture (stone/concrete arrises), 1 mm machined
  metal.
- `gam.shade_hard_surface(obj)` → smooth + sharp-by-angle + applied Weighted Normal. Flats
  stay flat, bevels read round, no normal map required.
- No n-gons in export (`finish` triangulates stray ones; better to not make them).
  Quads + tris are fine. Long thin triangles shade badly with weighted normals — add a
  loop instead.
- Interpenetrating parts are fine (cheaper than booleans, invisible) **except** where the
  intersection is visible up close or where a bake needs clean AO — then union or leave a
  tiny gap.

## When to bake from a high poly

Sculpted/organic detail (cloth, faces, cast ornament, carved wood), SDF/marching-cubes
parts from reference-modeling, dense panel detail on hero weapons. Low = budgeted mesh with
unique UVs; `gam.bake(low, 'NORMAL', size, path, high=high, extrusion=...)` (OpenGL +Y,
tangent space). Set extrusion to just cover the high-poly offset (1–3 cm props); too big
causes projection overlaps. Bake AO from the high poly too.

## LODs

`finish` → `make_lods`: collapse-decimate copies at spec ratios (default 1 / 0.4 / 0.15),
weighted normals recomputed per LOD. Assets ≤ `lod.min_tris` ship a single `_lod0`.
Check the `lods` review view: LOD2 must keep the silhouette at the distance it shows (30 m
for crowds/props, 60–100 m for architecture). If decimation eats the silhouette (thin
posts, wires, railings), build LODs by hand in the family module instead (drop the bevels,
drop the small parts, reduce cylinder sides with the same `phase`) and pass them:
`finish(ctx, lod0, lods=[lod1, lod2])` after `transfer_uvs(lod0, lodN)`. Validator: a LOD
heavier than ratio+tolerance FAILS; lighter only WARNS (check the `lods` view for pops).

## Colliders

| Kind | Use | Build |
|---|---|---|
| `box` | boxy props, crates, cabinets | bbox of the render mesh (automatic) |
| `convex` | physics props that aren't boxes | hull ≤ 64 verts (automatic) |
| `mesh` | static kit/props | **hand-built `collider_src`** from a few boxes; no ornaments, no small recesses |

**Compound colliders** (tables, chairs, trestles: anything with a gap a single hull would
fill): pass `finish(ctx, obj, collider_kind="box"|"convex", collider_parts=[top, leg1, ...])`.
That exports `<id>_col_01`, `_col_02`, and so on. The engine builds one Havok container
from them. Use either `<id>_col` or `_col_NN`, never both.

**Breakables**: `make_broken` already names pieces `<id>_broken_NN` with a child
`<id>_broken_NN_col`, which is what the engine's `breakProp` expects. The loader hides the
set at load.

Colliders carry no material and no UVs. Walkable surfaces (stairs, ramps, curbs) should be
smooth ramps in the collider, not steps, unless the spec says otherwise.

## Breakage

- Part-based (built things): `tag_parts(parts)` → `join` → texture → `split_parts(obj,
  groups)` → `make_broken(ctx, pieces)` → `finish(..., extra_children=[broken])`. Group
  parts into 5–10 chunks of similar mass.
- Voronoi (solids): `voronoi_fracture(solid, n=6..12, seed, inner_mat=m_inner)` — cut faces
  get the interior material (splintered wood, raw concrete, glass edge).
- Each piece gets its own convex `_col`; pieces keep the intact mesh's position so the
  engine can swap in place.

## Sockets, hinges, pivots

- `gam.socket(name, loc, forward, up)` — empty with local +Y forward (→ glTF −Z) and +Z up.
- Hinges/axes/footprints in custom properties: engine space via `gam.to_gltf`.
- Moving parts (doors, lids, wheels, slides) as separate child meshes whose origin is on
  the motion axis — set with `gam.set_pivot(part, 'point', point)` before parenting.

## Mesh report

`gam.mesh_report(obj)` → verts/faces/tris/ngons/non-manifold/loose. Non-manifold is fine
for open shells (a sign, a card, a wall piece with an open back) but a red flag on anything
the COM or AO bake depends on.
