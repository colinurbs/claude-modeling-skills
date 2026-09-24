# Molded / organic parts from a side profile (SDF inflate)

Profile extrusion + loop cuts (`edit-mode-techniques.md`) works for machined, prismatic
parts: a slide, a bracket, a blade. It fights you on **molded or organic parts**, where the
cross-section is a rounded rectangle whose width *and* roundness change continuously across
the part: a polymer pistol frame, a tool handle, a game controller, a power-tool housing, a
bottle or a shoe sole seen from the side. There, describe the part as **fields over the
side-view reference** and mesh it with marching cubes (`scripts/sdf_mesher.py`, runs
offline in CPython).

## When to choose it (Phase 0 strategy)

- The primary silhouette is matched from a mask (IoU gate) **and** the part's section is
  rounded, with a width that varies by region (grip vs. dust cover vs. trigger guard).
- The part is roughly symmetric about the silhouette plane (left/right). Asymmetric details
  get added afterwards as separate geometry.
- You'd otherwise need dozens of loop cuts plus proportional editing to get the swells.

Keep hard-edged machined parts (a steel slide) on the extrusion/bevel path. The two
approaches coexist in one model.

## Recipe

1. **Mask** (`trace_profile.mask_from_photo`, or a hand-filled drawing). Split the subject
   into parts with row/column cuts: the slide is everything above a parting row, the frame
   everything below. Cut enclosed openings with `holes_of` / `convex_opening`, and model any
   inner element (a trigger in its guard) separately.
2. **Clean the outline where the photo has texture.** Stippling, knurling and printed text
   make bumps in a silhouette. Smooth those regions harder than the rest (sigma ≈ 9–16 px vs
   2.5 px), or they come back as horizontal ridges on the surface.
3. **Fields** (`region_fields`). Paint regions with `(w_mm, r_mm, p)` and blur them
   (≈2–3 mm of pixels). Starting values for a polymer handgun frame:

   | Region | w (mm) | r (mm) | p |
   |--------|--------|--------|---|
   | Grip | 30 | 9.5 | 2.4 (rounded straps) |
   | Trigger guard | 14 | 5 | 2.2 (round bar) |
   | Upper frame / rails | 25.6 | 2.2 | 4 (boxy, small fillet) |
   | Dust cover | 22 | 2 | 4 |
   | Beavertail / tang | 24 | 6.5 | 2.3 |

4. **Flat caps** where the part meets another part or is open (the slide seam, a magwell).
   Build `round_mask` = mask extended past those edges (pad rows above the seam, columns
   below the grip bottom). Rounding is measured against it, and the final intersection with
   `mask` cuts the edge flat.
5. **Crisp recesses** (rail grooves, lever pockets, button pockets, a 0.5 mm parting-line
   groove) go in `dw_mm`, blurred only ~1 px, *not* in booleans afterwards.
6. `mesh_part(...)` → `.npz` → `import_npz(path, name, ratio=0.3)` in Blender. Shade smooth.
   A step of 0.28 mm is enough for a 200 mm part (~1M tris before decimation, ~1.5 s).
7. **Gate as usual.** Primary IoU should be unchanged (≥ 0.98 is typical, since the outline
   *is* the mask). Check widths per station with `extent_profile(along='z'|'x',
   measure='y')` against the table targets. `mesh_report`: 0 non-manifold, 0 loose.

## Pitfalls that were hit (don't repeat them)

- **`|y| − Y(x,z)` field** looks equivalent but has a kink at y = 0 → speckled, noisy seam
  along the silhouette equator. Use the superellipse implicit in `sdf_mesher.py`.
- **Voxel-remesh a thin extrusion, then push vertices to the target width**: vertices near
  the side/wall boundary get misclassified → jagged creases along every edge. Don't.
- **Booleans on the dense result** (grooves, pockets): ragged, chewed edges. Bake them into
  `dw_mm`. Booleans are still fine for round holes on flat faces (a guide-rod bore).
- A photo's outline includes **every** part: a magazine floorplate, a sight, a slide stop.
  Look at the reference crop before deciding what belongs to which part, or you'll model
  it twice.
- Surface features that follow the outline (a panel of grip texture, a smooth border
  around it) are best as a **vertex attribute** sampled from an eroded side-view mask, which
  then drives a procedural bump in the material. See `materials-and-rendering.md`.
