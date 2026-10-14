# Project spec (`assets/spec.json`)

The scripts are project-agnostic. Everything project-specific — axes, pivots, budgets,
naming, required custom properties, palette rules, review cameras — lives in
`<project>/assets/spec.json`, generated **from the project's own delivery-spec document**
(for irongrip: ASSETS.md §0 + Style Guide S3/S13). The document is the source of truth;
spec.json is its executable shadow.

## Building it

1. Read the delivery-spec doc and the style guide fully.
2. Start from `templates/spec.default.json` (a complete, commented-by-example spec).
3. Transcribe every rule that a script can check. Leave rules that need judgment in the doc
   (they surface in the critic prompt instead).
4. Note contradictions and ask the user; don't pick silently. Examples of the kind that
   exist in real specs: a floor-to-floor height (3.2 m) that isn't a multiple of the kit's
   minor grid (0.25 m); a category with no source folder in the layout (where do `phys_`
   sources go?); a pivot rule that conflicts with a "resting on the ground" rule.
5. Record `spec_source` (doc + section) so the next agent knows what to re-read.

## Field reference

| Field | Used by | Meaning |
|---|---|---|
| `axes` | gam, docs | Blender Z-up, front −Y; export +Y up (glTF front +Z). |
| `reference_dims_m` | briefs, review | Human, eye height, door, floor height... dims come from here first. |
| `paths.*` | cli, gam, validator | Relative to `assets/`. `placeholder` mirrors `export`. |
| `naming.*` | validator | Regexes for ids, nodes, materials (`m_`), textures (`t_..._bc|n|orm|e`), suffixes. |
| `categories.<key>` | all | `prefix`, `export_dir`, `src_dir`, default `pivot`, `budget_class`, `density_class`, `lightmap_uv`, allowed `colliders` (empty = none), `required_props`, optional `grid`. |
| `budgets` | validator | LOD0 triangle budgets per class. A brief may name another class (`vehicle`, `hero`) or set `tri_budget`. |
| `texel_density` (+ `_tolerance`) | gam, validator, review | px/m per class. Brief may override with `density_class`. |
| `lod` | gam, validator | `min_tris` (below: no LODs), `ratios`, `ratio_tolerance`. |
| `collision` | gam, validator | Custom prop name for collider kind, allowed kinds, convex vert cap. |
| `uv` | gam, validator | UV layer names, lightmap resolution + padding px. |
| `textures` | validator | Max px, power-of-two, format, normal convention. |
| `materials` | validator | Workflow, kit material-set cap, alpha-mode word lists, albedo sRGB limits, roughness floor. |
| `palette` | validator | Swatches, max saturation for ordinary materials, `accent_rules` (hue/sat/value → only allowed on matching material or asset regex). |
| `physics` | validator | Material enum, densities (mass sanity info). |
| `prop_schema` | validator | Type per custom property: `number>0`, `bool`, `vec2/3`, `enum:a,b` or `enum:section.key`. |
| `coordinate_props` | docs | Props holding engine-space vectors (must go through `gam.to_gltf`). |
| `sockets` / `states` | validator | Required socket/state nodes by id prefix. |
| `skeleton` | validator (skins) | Bone name list, max bones/influences, required blendshapes. |
| `review` | shoot.mjs | Background, ground, fog, exposure, HDRI (null = skill default overcast), mannequin height, views, CCTV feed treatment, iso angles. |

## Brief overrides (per asset)

`dims`, `pivot` (`custom` + `pivot_point` for weapons/grips), `collider`, `props`,
`required_props`, `budget_class` / `tri_budget`, `density_class` / `texel_density`,
`review_views`, `status` (`placeholder|wip|done`), `source`, `license`, `notes`.

## Other engines

Nothing here is Babylon-specific except the review harness. For Godot or three.js keep
glTF export; adjust `review` (or skip `review` and inspect in that engine) and the naming
of collider hints (Godot imports `-col`/`-convcolonly` suffixes natively — set
`naming.col_suffix` accordingly).
