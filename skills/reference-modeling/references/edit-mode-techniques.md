# Edit-mode techniques

The long-form detail behind Phases 2–4 of `SKILL.md`. Read the relevant section before
the corresponding phase. Everything here runs through `execute_blender_code`.

## Why `bmesh`, not `bpy.ops.mesh.*`

`bpy.ops.mesh.extrude/inset/loopcut/bevel` only work when there is an active mesh in
**edit mode** with a 3D area/region in the context. Headless over MCP that context is
fragile and a missing override throws `RuntimeError: context is incorrect`. `bmesh.ops.*`
operate on a bmesh you read from and write back to `obj.data` directly — **no context
needed** — so they are the reliable default for this skill. Use `bpy.ops` only when no
bmesh equivalent exists, with the override pattern at the bottom of this file.

A bmesh edit always has the same skeleton:

```python
import bpy, bmesh
obj = bpy.data.objects["Body"]
bm = bmesh.new(); bm.from_mesh(obj.data)
# ... bmesh.ops.* on bm ...
bm.normal_update()
bm.to_mesh(obj.data); obj.data.update(); bm.free()
```

Useful ops: `extrude_face_region`, `translate`, `bevel`, `inset_region`,
`inset_individual`, `subdivide_edges`, `bisect_plane`, `dissolve_limit`,
`recalc_face_normals`, `remove_doubles`.

---

## Profile extrusion (the Phase-2 default)

Many hard-surface forms read as a single 2D profile pushed to a width. Modeling them as
extruded outlines makes the topology follow the silhouette instead of approximating it
with a box. *(Example: a pistol's slide, frame, grip and guard each work this way.)*

**1. Trace the profile.** Read an ordered list of `(x, z)` points off the primary ortho
reference, going around the outline once (consistent winding). Put **many** points on
curved runs and few on straight ones. The `silhouette()` helper in `blender_ref.py`
samples the outer outline at a series of slices so you can read real edges straight off
the drawing rather than guessing.

**2. Build the profile face and extrude it to width:**

```python
import bpy, bmesh

profile = [ ... ]      # [(x,z), ...] ordered around the outline, read off the ref
depth   = 0.0255       # full width along the secondary axis (true scale, meters)

me = bpy.data.meshes.new("Body"); ob = bpy.data.objects.new("Body", me)
bpy.context.collection.objects.link(ob)
bm = bmesh.new()
vs = [bm.verts.new((x, -depth/2, z)) for (x, z) in profile]
f  = bm.faces.new(vs)
ext = bmesh.ops.extrude_face_region(bm, geom=[f])
moved = [g for g in ext["geom"] if isinstance(g, bmesh.types.BMVert)]
bmesh.ops.translate(bm, vec=(0, depth, 0), verts=moved)
bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
bm.to_mesh(me); bm.free()
```

**3. Bevel the hard edges** (covered next). Then run the critique loop: `set_ortho_view`,
screenshot, fix the single biggest discrepancy in the profile, repeat. Most fixes at
this stage are *moving profile verts* so the outline lands on the drawing — that is the
work, and it is what a box can never do.

This produces a constant-section prism; varying its cross-section along the length is the
job of the **3D-form pass** below. For a form that isn't a clean extrusion (a tapered end,
a rounded cross-section), extrude the prism first, then taper/round it: select an end loop
and scale it, or inset + push the side loops in. Stay in edit mode; add no new primitive.

---

## Topology target A — Bevel + weighted normals (default, game-res)

Goal: crisp shading on minimal geometry, no subdivision. The mesh you see is the mesh you
ship. **Bevel + weighted normals is the FINISHING stage** — see "Finishing" below for the
non-destructive workflow; do it only after the form gates pass and `mesh_report` is clean.

1. Model the form with as few faces as the silhouette needs.
2. Round the hard edges with `add_bevel(...)` (non-destructive Bevel modifier, angle- or
   weight-limited, small width, 2–3 segments).
3. Pair it with `finish_shading(...)` (shade smooth + sharp-by-angle + Weighted Normal),
   which makes large flats dominate the shading and the bevels read as crisp.

Keep bevel widths consistent across like features — random widths read as a mistake.

---

## Finishing — bevels & shading (the hard-surface payoff)

Real manufactured objects have almost **no perfectly sharp edges**: every edge carries a
small radius from machining / molding / casting / wear. A true 90° edge produces a single
hard specular line and reads as fake; a small rounded edge produces a soft highlight that
*defines the form*. Bevels aren't decoration — they're what makes a surface read as
physical, and they're the payoff of the bevel + weighted-normals target.

**Precondition — never bevel before form + topology are right.** Bevel is a finishing
operation. Run it only after the form gates pass *and* `mesh_report` is clean (no ngons,
no non-manifold, no fan-triangulation at transitions). Beveling unclean topology (fans,
poles, ngons, near-coincident verts) self-intersects and overlaps — a bevel makes a
collapse or stray fan **worse**, not better. Repair first.

**Edge rounding ≠ curved surface.** Two different things get called "rounding":
- a sharp edge that needs a fillet/chamfer → a **bevel** (this section);
- a surface that is *itself* curved (a domed top, a cylinder, a swelling grip) → that's
  **form**, done by section shaping / loft-bridge in the Phase-3 form pass.

A flat-topped slab with rounded edges is not a slab with a curved top. Decide which the
reference shows; the common failure is having *neither* (sharp edges + flat sections that
should be curved). Check both.

**The two helpers (non-destructive, keep them as modifiers until final):**

```python
import os; S = os.path.expanduser("~/.claude/skills/reference-modeling/scripts")  # adjust if installed elsewhere
exec(open(S + "/bevel.py").read()); exec(open(S + "/finish_shading.py").read())
add_bevel("Body", width=0.0015, segments=2, limit='ANGLE', angle_deg=30)  # before WN
finish_shading("Body", angle_deg=30)                                       # WN after bevel
```

Guidance:
- **Small width, 2–3 segments.** 1 segment = flat chamfer; 2–3 = rounded fillet. Width is
  the radius — small for hard surface (just catches a highlight). Big bevels read soft/
  toy-like; tiny crisp ones read as machined metal. Match width to material and to any
  fillet radii the reference gives (metal → tiny/crisp; plastic → slightly larger/softer).
- **Limit which edges round.** `limit='ANGLE'` rounds only edges sharper than `angle_deg`
  (leaves smooth transitions alone); assign edge **bevel weights** + `limit='WEIGHT'` when
  features need different radii (a larger round on a top, a tiny chamfer on a parting line).
  Never blanket-bevel everything — it looks mechanically wrong.
- **Order:** Bevel **before** Weighted Normal in the stack. `finish_shading` appends WN
  after the bevel and bakes sharp-by-angle flags so WN keeps the flats crisp.
- **Apply only at the very end.** Destructive early beveling makes every later edit fight
  the extra geometry, and booleans on beveled geometry are a mess.

**Failure modes to check:**
- *New non-manifold/ngons after beveling* → width too large for some local feature
  (self-intersection on a tight concave area). Reduce width or weight to spare that region.
  Run `mesh_report` after beveling as a regression check.
- *Pinched/ugly result* → beveled over poles or ngons; re-confirm topology is clean first.
- *Melted look* → weighted normals / auto-smooth not set up (run `finish_shading`).
- *Mechanical uniform look* → blanket-beveled; switch to angle- or weight-limiting.

**Auto-smooth across versions:** Blender < 4.1 uses `mesh.use_auto_smooth` +
`auto_smooth_angle`; 4.1+ removed those and shades by the per-edge sharp flag (the operator
`shade_auto_smooth` adds a "Smooth by Angle" node modifier, but Blender pins it to the
bottom of the stack — *after* Weighted Normal — which defeats WN). `finish_shading` handles
both: legacy attribute pre-4.1, and on 4.1+ it bakes the sharp flags onto the mesh directly
so WN reads them. Verified on Blender 5.1.

## Topology target B — Subdivision control cage (higher fidelity)

Goal: smooth, perfectly even surfaces. The visible mesh is the Subdivision Surface
output; you model the **control cage** that drives it.

1. Model with **all-quad** topology. Subsurf amplifies ngons and poles into visible
   pinching, so keep them off curved/visible areas.
2. Every hard edge needs **supporting loops** on both sides to hold it under subsurf —
   the closer the support loops sit to the edge, the sharper it stays. Add them with a
   bevel (profile 1.0, 2 segments) on the hard edges, or loop cuts hugging each edge:

   ```python
   import bpy, bmesh, math
   ob = bpy.data.objects["Frame"]
   bm = bmesh.new(); bm.from_mesh(ob.data)
   hard = [e for e in bm.edges
           if len(e.link_faces) == 2 and e.calc_face_angle(0.0) > math.radians(30)]
   bmesh.ops.bevel(bm, geom=hard, offset=0.0008, segments=2, profile=1.0, affect='EDGES')
   bm.to_mesh(ob.data); bm.free()
   sub = ob.modifiers.new("Subsurf", 'SUBSURF'); sub.levels = 2; sub.render_levels = 2
   ```

3. Edge creases (`bm.edges.layers.crease`) are a quicker but coarser alternative to
   support loops; prefer real loops where you want control.

Pick **one** target in Phase 0 and model toward it — mixing them gives inconsistent
shading.

---

## Construction-strategy mechanisms (Phase 0 decision → Phase 2 base method)

The strategy chosen from the references determines how the base mesh is built. Always
construct the **partial** mesh first (half / quarter / single unit / profile), then add
the modifier; keep the modifier live until the form is gated, then apply it.

- **Mirror** — model one half on one side of the plane, **weld the center verts exactly
  onto the plane** (`x≈0` etc.), then `add_mirror(obj, axes=(True,False,False))`. `use_clip`
  stops verts crossing back over the seam. The seam is the usual failure: after applying,
  `mesh_report` must show no loose/doubled verts along it (a missed weld leaves a slit).
- **Revolution** — model a single **half-profile** as an edge loop on one side of the axis
  (this is a *profile curve*, not a slab), then `add_screw(obj, axis='Z', steps=64)`. The
  3D form is produced by the revolve, so the "form pass" is just profile refinement. Check
  the poles (start/end of the revolve) merge cleanly (`use_merge_vertices`).
- **Radial array** — model one unit, `make_offset_empty(name, count=n)` (an empty rotated
  360/n about the axis), then `add_radial_array(obj, n, empty)`. Validate the *assembled*
  ring against the reference, and that adjacent units join without a gap or overlap.
- **Linear array** — model one unit, `add_linear_array(obj, n, offset)`. Same join check.

Whatever the aid, **validate the symmetry is real before committing** —
`silhouette_symmetry(mask, axis)` on the reference. Forcing a mirror on a not-quite-
symmetric subject is a hard-to-spot, baked-in error.

---

## The 3D-form pass (Phase 3) — differentiating cross-section along the length

A silhouette-extruded mesh is a constant-section prism: right outline, no thickness
variation. Turn it into real form by making the cross-section vary correctly along the
primary axis. (For **revolution** the form is already 3D — refine the profile instead. For
**array**, do this on the unit cell.)

**1. Segment with loop cuts at section transitions.** One loop pair per place the section
changes; loop *spacing* sets transition sharpness (tight = hard step, spaced = smooth
taper).

```python
import bmesh
bm = bmesh.new(); bm.from_mesh(ob.data)
# cut a loop ring by bisecting across the primary axis at world x = X0
geom = bm.verts[:] + bm.edges[:] + bm.faces[:]
bmesh.ops.bisect_plane(bm, geom=geom, plane_co=(X0,0,0), plane_no=(1,0,0),
                       clear_inner=False, clear_outer=False)
bm.to_mesh(ob.data); bm.free()
```

**2. Set the envelope** (match the second length-containing view). Select each region's
bounding loops and reshape on the **secondary** axis only — scale for a uniform
widen/narrow, move verts for a shaped edge. Pivot at the region center; **do not** let a
primary-axis component creep into the transform or the primary silhouette drifts. Gate:
`extent_profile(ob, along=<primary>, measure=<secondary>)` should now vary station-to-
station (no longer flat), and that view's IoU ≥ 0.90.

**3. Shape the cross-section** (match the end view) — loft/bridge between station loops, the
clean method for a section that changes *shape*:

```python
import bmesh, math
bm = bmesh.new(); bm.from_mesh(ob.data)
# after shaping each station loop to its target section, delete the faces spanning
# between two stations, leaving two open loops, then bridge them:
faces_between = [f for f in bm.faces if X0 < f.calc_center_median().x < X1]
bmesh.ops.delete(bm, geom=faces_between, context='FACES')
loops = [e for e in bm.edges if e.is_boundary]          # the two open rings
bmesh.ops.bridge_loops(bm, edges=loops)                 # quad bridge that interpolates
bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
bm.to_mesh(ob.data); bm.free()
```

Scaling slab segments leaves a faceted, blocky result; bridging *defined* sections gives
quad topology that flows along the form. Gate on the end-view IoU (section mask) or
`extent_profile` on the second secondary axis.

**4. Compound / organic form last** — swells and double-tapers via proportional editing
(`bmesh` vertex moves weighted by distance) once the blocky sections gate good.

Throughout: `mesh_report` after each step, and **re-run the primary-view `silhouette_iou`
as a regression gate** — if it dropped, a transform pivot or a stray primary-axis component
moved geometry you'd already matched; undo and redo the edit on the secondary axis only.

---

## Booleans + cleanup (Phase 4)

Holes and recesses (an opening, a port, a recess, vents, screw holes) are cut with
booleans. The cut is the easy half; **cleanup is mandatory** or the
mesh fills with ngon spaghetti that `mesh_report` will flag.

```python
import bpy, bmesh, math
tgt = bpy.data.objects["Part"]
cut = bpy.data.objects["Cutter"]          # a primitive shaped to the opening

m = tgt.modifiers.new("bool", 'BOOLEAN')
m.operation = 'DIFFERENCE'; m.object = cut; m.solver = 'EXACT'
with bpy.context.temp_override(object=tgt):
    bpy.ops.object.modifier_apply(modifier=m.name)
bpy.data.objects.remove(cut, do_unlink=True)

# clean the boolean's coplanar triangle fans back into tidy faces
bm = bmesh.new(); bm.from_mesh(tgt.data)
bmesh.ops.dissolve_limit(bm, angle_limit=math.radians(2.0),
                         verts=bm.verts, edges=bm.edges)
bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
bm.to_mesh(tgt.data); bm.free()
```

After limited-dissolve, look where the cut meets a curved surface — booleans leave
triangles fanning into the curve. Redraw a couple of edges by hand (or rebuild a clean
quad loop around the hole) if the area will be seen up close or sit under subsurf. For a
subdiv model, prefer cutting holes by hand over booleans, or boolean first then rebuild
clean loops — raw boolean output pinches badly under subsurf.

Confirm with `mesh_report`: ngon count should return to roughly its pre-boolean level,
`loose_verts` should be 0.

### Align the cutter before you cut

The cutter's placement is a perception problem identical to the main silhouette — and
you are just as blind to a few-mm error on it. So gate it the same way: build the cutter,
overlay it on the reference, and confirm it lands on the feature **before** applying the
modifier.

- **Derive the opening from the reference, don't eyeball it.** Flood-fill the opening's
  region in the blueprint (same `flood`/`moore`/`dp` helpers used for the body silhouette)
  and build the cutter from that contour. A hand-traced 8-point oval will be too small,
  too low, or shifted — which is exactly how a hole lands in the wrong place.
- **Verify the overlay.** `zoom_region(...)` cropped to the feature + screenshot, with the
  cutter a bright flat colour over the reference. Only cut once it sits on the drawn edge.

### Cut the whole opening; model the insert as its own object

When an opening contains an element (examples: a trigger in a trigger guard, a mullion in a
window, a knob in a recess — do not flood "around" the element and cut a notched hole.
That fights the geometry and yields tangled topology. Instead:

1. Capture the **whole** opening (flood both sub-chambers, union them, then a
   morphological *close* big enough to swallow the inner element → one clean opening).
2. Cut that clean opening as negative space.
3. Build the inner element **separately**: it is `opening_region AND NOT open_space`
   (the filled element), extruded to its own thinner depth and seated in the gap.

### A clean boolean is manifold — fix the cause, not the symptom

`EXACT` difference of two closed manifold solids is manifold. New `non_manifold_edges`
after a cut almost always trace to a **self-touching profile**: the traced silhouette
pinches to 1px (a sight bump on a thin neck), so two non-adjacent contour points coincide
and the extruded prism gets a 4-face "fin". Don't patch the mesh — clean the contour
first by collapsing the smaller loop at any self-touch:

```python
import math
def fix_selftouch(p, mind=0.002):           # p: ordered [(x,z), ...] world contour
    while True:
        n = len(p); hit = None
        for i in range(n):
            for j in range(i + 2, n):
                if i == 0 and j == n - 1: continue
                if math.dist(p[i], p[j]) < mind: hit = (i, j); break
            if hit: break
        if not hit: return p
        i, j = hit; inner = j - i - 1
        p = (p[:i+1] + p[j:]) if inner <= n - inner - 2 else p[i:j+1]   # drop smaller loop
```

Then keep `limited dissolve` only if it leaves `non_manifold_edges` unchanged (run it on
a `bm.copy()` first and compare).

---

## `bpy.ops` edit-mode override (only when no bmesh op fits)

```python
import bpy
ob = bpy.data.objects["Body"]
area = next(a for a in bpy.context.screen.areas if a.type == 'VIEW_3D')
region = next(r for r in area.regions if r.type == 'WINDOW')
for o in bpy.context.view_layer.objects: o.select_set(o == ob)
bpy.context.view_layer.objects.active = ob
with bpy.context.temp_override(area=area, region=region, active_object=ob,
                               selected_objects=[ob]):
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.select_all(action='SELECT')
    # ... bpy.ops.mesh.* here ...
    bpy.ops.object.mode_set(mode='OBJECT')
```

Prefer the bmesh skeleton at the top of this file; reach for this only for operators
with no `bmesh.ops` equivalent.
