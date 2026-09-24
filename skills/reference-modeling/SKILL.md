---
name: reference-modeling
description: Model a real-world object in Blender from reference images with accurate proportions, correct 3D form, AND real polygon topology — not stacked primitives or a flat uniform extrusion. Use whenever the user wants to model a recognizable real object (vehicle, tool, gadget, weapon, furniture, vessel, prop, building, character part) in Blender, says "model X from a reference/blueprint", asks for hard-surface or poly modeling, wants reference/blueprint images set up, or is unhappy that a from-scratch model looks blocky, boxy, flat, or "not realistic". It picks a construction strategy (revolve / mirror / array / freeform) from the references and gates every phase on measured proportion, silhouette IoU, and form taper rather than on visual judgment. Wants a filled silhouette mask per available reference view for the IoU checks.
---

# reference-modeling

Real objects look real because their **proportions** are right, their **silhouettes**
match from every angle, and their **3D form** — how the cross-section changes along the
length — is correct. None of that comes from guessing: it comes from references, and from
polygon modeling in edit mode, not from parking primitives near each other.

The classic AI-modeling failure is two-staged. First a **stack of boxes** that reads as
the object only because the boxes sit in roughly the right places. Then, once a silhouette
is matched, a **uniform extrusion** — a flat slab with the right outline but no real
thickness variation. *(Example: a pistol modeled as a rectangle "slide" on a slab "grip";
or that same outline swept at one constant width.)* Both happen because they're the only
things that give a reliable result *while flying blind*. This skill replaces blind work
with a perception → action → **measured-gate** loop, and never lets a phase end on a
visual hunch. **Blockout is a starting point, never the deliverable; a matched silhouette
is half-done until the 3D form is right too.**

## How it drives Blender

Through the **BlenderMCP server**: `execute_blender_code` (run Python),
`get_viewport_screenshot` (see the viewport), `get_scene_info` / `get_object_info`.

Helpers live in `scripts/` next to this file. **Each MCP `execute_blender_code` call
is an independent exec — globals do NOT persist between calls.** So at the top of any
code block that needs a helper, load it:

```python
import os; S = os.path.expanduser("~/.claude/skills/reference-modeling/scripts")  # adjust if installed elsewhere
exec(open(S + "/set_ortho_view.py").read())
exec(open(S + "/silhouette_metrics.py").read())   # dim_gate
exec(open(S + "/blender_ref.py").read())          # setup / measurement / box library
```

Always `print(...)` anything you need back — the MCP returns stdout.

## When to use

- "Model a <real object> in Blender" where it should actually look like that object.
- "Set up reference images for X." / "Use a blueprint to model Y."
- Hard-surface / poly modeling requests, or "make it look real, not blocky/flat."
- The user is unhappy a from-scratch model looks fake — almost always a missing-reference,
  stayed-at-blockout, or stayed-flat problem. Switch to this skill.

If the user explicitly wants a quick stylized/blocky prop and doesn't care about
accuracy, this is overkill — model directly.

## Prerequisites

- Blender open with the **BlenderMCP** addon server running (sidebar `N` → BlenderMCP →
  Start). If `get_scene_info` errors with "Could not connect", it isn't running — tell
  the user how to start it before continuing.
- Web access for references (WebSearch / WebFetch). Blueprint sites worth trying:
  `drawingdatabase.com`, `the-blueprints.com`, `getoutlines.com`, `3dmodels.org`,
  plus Wikimedia for clean ortho photos and patent PDFs for true-ortho figures.
  A product photo shot square-on against a white background (Wikimedia Commons has many) works
  as a primary side view once it's calibrated. Check its aspect against published dimensions,
  because photos are often a few % off.
- If the addon isn't running and Blender isn't open, you can launch it with the server
  auto-started: `open -a Blender --args --python start_mcp.py`, where `start_mcp.py`
  registers a `bpy.app.timers` callback that calls `bpy.ops.blendermcp.start_server()`.
- Offline helpers (`trace_profile.py`, `sdf_mesher.py`) run in normal CPython:
  `pip install numpy scipy pillow opencv-python-headless scikit-image`.

## Conventions & orientation (record per subject, then hold constant)

- **Real-world meters.** 1 Blender unit = 1 m. Model at true size (a handheld object is
  ~0.1–0.3 m, a chair ~0.8 m, a car ~4 m). True scale makes proportions and assembly
  correct.
- **Record the orientation for THIS subject before modeling**, and refer to axes by
  *role* thereafter — never by hardcoded letters:
  - **primary axis** — the object's dominant length/height direction;
  - **up axis**;
  - **view → ortho mapping** — which Blender ortho view each reference image corresponds to.
  - Sensible default the helpers assume: primary/forward = **+X**, up = **+Z**, width along
    **+Y**, model centered on Y=0.
- One reference view is the **primary silhouette** (the most informative — usually the
  side); match and lock it first. The other two ortho silhouettes are brought into
  agreement in the form pass (Phase 3).
- Reference view ↔ image plane ↔ ortho view to judge it (at the default orientation):

  | Reference (what it shows) | Image plane | Ortho VIEW (`set_ortho_view`) |
  |---------------------------|-------------|-------------------------------|
  | Side / primary profile    | XZ          | `FRONT`                       |
  | Top / plan                | XY          | `TOP`                         |
  | Front / end / head-on     | YZ          | `RIGHT`                       |

- Keep reference images **and masks** in a `references/` folder.

---

# Objective alignment — numbers decide "done", not your eyes

You **cannot** perceive sub-centimeter misalignment in a low-contrast, full-frame
render, and you are strongly biased toward declaring work finished so you can advance.
"Look harder" does not fix this — it only produces more critical-sounding language, not
more accurate judgment. So: **the screenshot is for locating the next fix; computed
numbers decide whether alignment is acceptable.** Default stance: assume the alignment is
*wrong* until the metrics prove otherwise.

## The numeric gate

A phase may not advance, and the critique loop may not stop, until the checks relevant to
that phase pass:

- **Dimensional** — `dim_gate(part, target_len, target_height, tol=0.05)` → `passed` for
  every part (each span within 5 % of its real target). No image, so it cannot be
  rationalized away; catches the gross proportion errors that are most of what goes wrong.
- **Silhouette IoU** — `silhouette_iou([...], mask, ref_empty=...)` ≥ 0.90, **per
  reference view that has a mask**. Dimensions catch proportion; IoU catches contour.
- **Form taper** — `extent_profile(obj, along=<primary>, measure=<secondary>)` for 3D
  form, used as the gate where a view has no mask (the form pass).
- **Landmark** — `landmark_delta(feature, target)` for interior / 3D features that no
  silhouette contains.
- **Regression** — the already-matched **primary-silhouette IoU must not drop** while you
  work the other axes.

Report the raw numbers every time, e.g. `length 0.215 vs 0.204 → +5.6 %, IoU 0.83,
width-taper 0.028→0.028 (flat)`. Those are facts you cannot vibe away.

## Banned language (no verdicts at the critique step)

Do **not** emit qualitative verdicts — no "well", "good", "close", "tracks the
reference", "decisive improvement", "looks right". There is no slot for "looks good,
moving on". Output a **structured delta list** instead: every region where the mesh
boundary leaves the reference, each with a direction and an approximate magnitude:

    - lower-front opening: ~10 mm too high, ~6 mm too far back
    - top edge: ~2 mm proud from mid-span to the end
    - rear curve: bulges ~4 mm beyond the reference outline

Then fix the single largest measured delta. Repeat. Stop only when the numeric gate passes.

## Fresh-eyes critic (separate subagent)

The instance that built the mesh is biased toward blessing it. Before declaring any phase
done, spawn a **separate critic subagent** (the Agent tool) whose only job is to find
faults and which has no stake in advancing. Feed it the metric numbers, the
`silhouette_iou` diff overlay, and **zoomed, cropped** high-contrast renders
(`zoom_region(...)`) of one feature region at a time — an opening, a curve, an end. At
full frame a 5 mm error is ~2 px and invisible; cropped, it is obvious. Treat the critic's
delta list as the next work queue.

## Honest expectation

These gates guarantee the mesh isn't *grossly* wrong and stop false "looks right"
advances. Strategy-driven construction (revolve, array, *validated* mirror) comes out
accurate and fast because the aid enforces the symmetry exactly; mirror-blocked distinct
cross-sections are reliable too. The frontier is subtle organic 3D form — compound curves
and swells — where the gates prevent gross error but matching a nuanced surface still takes
iteration.

---

# The phases

Work through them in order. Two gates are load-bearing: the **proportion gate** ending
Phase 1 (stops a box-stack) and the **form gates** in Phase 3 (stop a flat slab).

## Phase 0 — Research, setup & strategy

1. **Get orthographic blueprint(s).** A clean primary profile is mandatory; the other two
   ortho views too if you can. **Perspective photos are why a model comes out loose** —
   you cannot judge proportion through perspective distortion. Patent drawings and
   blueprint sites are genuinely orthographic.
2. **Pin down real dimensions as gate targets.** Overall length, height, width, plus
   per-part target spans — these become the `dim_gate` targets the build must hit within
   5 %. Proportions come from *numbers*, not eyeballing; spans are in meters.
   *(Example — Glock 17: ~204 mm overall length, 186 × 25.5 mm slide, 139 mm height.)*
3. **Record orientation & views** for this subject: primary axis, up axis, and which ortho
   view matches each reference image (see Conventions). Refer to axes by role from here on.
4. **Decide the construction strategy — this is a DECISION from the references, not a
   default.** It sets the *base construction method*, so commit before building.
   Examine the views and ask, in order:
   1. Are cross-sections perpendicular to some axis essentially **circular**? → surface of
      **revolution** (lathe).
   2. Is a unit **repeated around an axis** (teeth, blades, bolts) or **in a line** (fins,
      slats, keys)? → **radial** or **linear array**.
   3. Is a silhouette a **mirror** of itself across one plane (or two)? → **mirror**.
   4. Otherwise → **no aid; model the full form.**

   Pick the strongest strategy the references *actually* support — the aid that most
   reduces work **while faithfully matching the true form**. Never apply an aid the
   references don't justify; assuming symmetry that isn't there bakes the error into every
   later step. **Validate before committing:** `silhouette_symmetry(mask, axis)` measures
   mirror symmetry objectively (≈≥0.95 ⇒ real). Most subjects are *partially* symmetric
   (symmetric base + asymmetric details) — use the aid for the base, then add asymmetric
   features as separate geometry or after applying the modifier.

   | Strategy | Recognition | Mechanism (`symmetry.py`) |
   |----------|-------------|---------------------------|
   | Bilateral mirror | one silhouette's halves match across a plane | `add_mirror` on that plane's axis; build one half, weld center verts, clip on |
   | Multi-plane mirror | symmetric across two planes | `add_mirror` two axes; build a quarter |
   | Revolution | circular cross-sections (cup, bottle, lens, turned leg) | `add_screw` on a single half-**profile** — replaces silhouette-extrusion |
   | N-fold radial | a unit repeated around an axis | `add_radial_array(count, empty)` (+ `make_offset_empty`) |
   | Linear repeat | identical elements in a row | `add_linear_array(count, offset)` |
   | None / freeform | nothing symmetric or repeated | model the full form |

5. **Choose the topology target** (see [References](#references)): **bevel + weighted
   normals** (default, game-res) or **subdivision control cage** (higher fidelity). State
   it and model toward it consistently.
6. **Load reference plane(s) at true scale.** `add_reference(path, axis, size, opacity)`,
   or the pixel-precise `measure_extent` → `calibrate_ppm` → `add_ref_empty`
   (`blender_ref.py`); place each empty so the *same landmark* lands at the same world
   coordinate in every view. Offset each empty off the model plane so it doesn't z-fight.
7. **Verify alignment before modeling:** `set_ortho_view(axis)` → screenshot → confirm the
   drawing sits where you expect (drop a wireframe `box()` at the real dimensions and check
   it fills). A 2–3 % spec error is fine.
8. **Build a filled silhouette mask per available reference view** (required for IoU). A
   clean **filled** silhouette — DARK shape on LIGHT — registered to that reference's
   framing and aspect. Auto-segmenting a busy drawing is unreliable; fill the outline
   solid and export a PNG (flood-fill recipe in [References](#references)). Keep them in
   `references/`. Where a view has no mask, `extent_profile` is the fallback gate.
   For a **photo on a plain background**, `trace_profile.mask_from_photo()` builds the mask
   (it keeps large enclosed openings, such as a trigger guard, open). `Frame.from_span()`
   calibrates pixels-per-metre from a known length, and `trace()` returns world-space contours
   that land exactly on an `add_ref_empty` placed at the same origin.

## Phase 1 — Blockout (primitives allowed)

Establish the major masses as primitives matched to reference proportions in ortho view.
Read coordinates off the drawing instead of guessing — `silhouette()` samples the outer
outline at slices so you can read real edges off the image. Key moves: `box(...)`,
`shear_z(obj, k)` for raked forms (handle grips, A-pillars) whose top/bottom edges must
stay horizontal, and booleans for the obvious big cutouts. Verify each mass against the
reference with an ortho screenshot before moving on.

> ### ⛔ Phase gate — do not skip
> Blockout is **not** a deliverable. If the model is still a set of separable primitive
> boxes, you are in Phase 1, not finished. You may only consider the model done after
> Phase 5.
>
> You also may not leave Phase 1 until the **numeric gate** passes: `dim_gate` returns
> `passed: true` for every blocked-out mass (proportion within 5 %). Getting proportion
> right is the blockout's entire job — verify it with numbers, not a glance.

## Phase 2 — Primary-silhouette refinement (primitives **BANNED**)

**After blockout, no new primitives.** Every form is refined in edit mode. This converts
box-stacking into modeling: a primitive *approximates* a shape; an edit-mode refinement
*makes the geometry follow the silhouette*.

**The Phase-0 strategy sets the base method here:**
- **Revolution** → don't extrude a slab; model the half-**profile** curve on one side of
  the axis and `add_screw`. The 3D form comes from the revolve, so Phase 3 becomes profile
  refinement.
- **Mirror** → build one half (or quarter) of the silhouette, weld center verts to the
  plane, `add_mirror`.
- **Array** → model a single unit, then `add_radial_array` / `add_linear_array`.
- **None** → model the full silhouette.

**Default technique for mirror/none — silhouette-first / profile extrusion:** trace the
primary profile as a 2D vertex outline over the ortho reference, extrude to a first depth,
bevel hard edges. *(Example: a pistol's slide, frame, grip and guard each read as extruded
2D profiles.)* Step-by-step recipe in [References](#references). Trace the outline from the
mask (`trace_profile.trace`, Douglas-Peucker 1–2 px) rather than placing points by eye.

**Molded / organic parts (rounded sections that vary in width and roundness): SDF inflate.**
Describe the part by width / edge-radius / squareness fields over the side-view mask and
mesh it with marching cubes (`sdf_mesher.py` offline → `import_npz.py`). This does the
Phase-2 silhouette *and* the Phase-3 envelope + section in one step, with smooth blends
between regions and flat caps where the part meets another. Use it for polymer frames,
handles, grips and housings. Keep machined, hard-edged parts on profile extrusion. The
recipe, starting values and the pitfalls already hit are in
`references/sdf-molded-parts.md`.

**Allowed operations** (reach for these, not "add cube"): extrude, inset, loop cut, bevel,
knife, bridge edge loops, and direct **bmesh vertex placement** for precise outlines. Drive
them via `bmesh` where possible — it's more reliable headless than `bpy.ops.mesh.*`, which
needs an edit-mode/area context. Run **the critique loop** after every edit.

## Phase 3 — 3D form pass

The mesh now matches the primary silhouette but is 3D-naive — typically a constant-section
prism. This pass gives it correct three-dimensional form by bringing the other two ortho
silhouettes into agreement. **What's required depends on the Phase-0 strategy:**

- **Revolution:** the form is already 3D from the revolve. This pass collapses to
  **profile refinement** — adjust the half-profile until the orthogonal and end views
  match. Skip the steps below.
- **Array:** apply the steps below to the **unit cell** (the form inherits to all copies),
  then validate the assembled result against the references.
- **Mirror / none:** the mesh is a prism — differentiate it, in this order.

**View geometry (general):** of the three ortho views, the **primary view** is matched and
locked. The **second length-containing view** constrains the **envelope** — how far the
form extends on the second secondary axis, along its length. The **end view** (looking
down the primary axis) shows the **cross-section shape**. Envelope first, section second.

1. **Segment with loop cuts at section transitions.** Add loops along the primary axis
   wherever the cross-section changes. Between loops the section is constant; across them it
   interpolates — so **loop spacing controls the transition**: a tight pair reads as a hard
   edge, spaced loops as a smooth taper. Place them where the reference's section actually
   changes. This is the scaffolding everything hangs on.
2. **Set the envelope** (match the second length-containing view). Per region, reshape the
   bounding loops on the secondary axis to the target extent — scale for a uniform
   widen/narrow, edit verts for a shaped outline. **Highest-impact step** — it's what
   removes the "uniform slab". Gate on `extent_profile(along=primary, measure=secondary)`
   plus that view's IoU.
3. **Shape the cross-section** (match the end view). A swept prism has a rectangular
   section; real forms are rounded/chamfered/irregular and often *change shape* along the
   length. Two methods, increasing quality:
   - *Constant section:* bevel/chamfer/round the long edges. Fast; correct when the section
     shape doesn't change.
   - *Varying section — loft/bridge (the clean method):* shape each key station's loop to
     its target section, delete the faces between stations, **bridge edge loops** between
     adjacent stations. Produces quad topology flowing along the form. Prefer it wherever
     the section genuinely changes shape — it's the difference between real modeling and a
     deformed box. (Recipe in [References](#references).)
   Gate on the end-view IoU (section mask if one exists; else `extent_profile` on the second
   secondary axis).
4. **Compound / organic form last.** Subtle swells, double-tapers, ergonomic bulges come
   after the blocky sections gate good, via proportional editing or carefully added loops.
   The gates only keep this from being *grossly* wrong; it still takes iteration.

Throughout: `mesh_report` after each step, and re-check the **primary-silhouette IoU as a
regression gate** — secondary-axis edits must not disturb the view you already matched.
When it drifts, the cause is almost always a wrong transform pivot or a stray primary-axis
component in a scale/move. Whatever aid is in use, verify its **seam/merge boundary** stays
clean (`mesh_report`: loose / non-manifold at the mirror plane, screw start-end, or array
joins is the usual first-pass failure).

## Phase 4 — Detail & negative space

Cut holes and recesses with **boolean modifiers** *(examples: a trigger-guard opening, an
ejection port, a window, vents, a mag well, screw holes)*. Then — non-negotiable — **apply
the modifier and clean up the topology**: `limited dissolve` to merge the boolean's
coplanar triangle fans into clean faces, then fix stray loops. Un-cleaned booleans leave
ngon spaghetti that `mesh_report` will catch. Add small raised/recessed detail here too,
still in edit mode. Keep running the critique loop.

**Align the cutter to the reference BEFORE you apply the boolean.** A cutter is geometry
too, so placing it is the *same perception problem* as the main silhouette: position it,
overlay it on the reference (`zoom_region` + screenshot, or IoU against a mask of the
opening), and confirm it lands on the feature *before* cutting. Cutting blind is how a hole
ends up too small, too low, or shifted. Derive the opening's shape from the reference
(flood-fill its region; see [References](#references)) instead of eyeballing a few points.

**Cut the whole opening; model anything inside it as its own object.** When a feature is an
opening with an element inside *(example: a trigger inside a trigger guard; a mullion in a
window; a button in a recess)*, do **not** contort the cutter around the inner element.
Remove the entire opening as clean negative space, then build the inner element as a
**separate object** in the gap. Working the cutter around the insert gives tangled topology
and a wrong-shaped hole.

**A boolean of two closed manifolds is itself manifold.** So *new* `non_manifold_edges`
after a cut trace upstream — usually a **self-touching profile** (a 1px pinch in the traced
silhouette, e.g. a thin protrusion on a neck) or an over-aggressive `limited dissolve`. Fix
the contour (collapse self-touches before extruding) and keep the dissolve only if it
leaves `non_manifold_edges` unchanged.

## Phase 5 — Final QA

- **Numeric gate, one last time:** `dim_gate` `passed` on every part; `silhouette_iou ≥
  0.90` for **every** view that has a mask; `extent_profile` matching the target taper for
  views without one; `landmark_delta` within tol for interior features. If any fails, go
  back to the critique loop. Keep the diff overlays as evidence.
- Full `mesh_report` on every object: minimal ngons, **no *unintended* non-manifold edges**
  (open boundaries on a piece meant to be closed are the red flag — a genuinely open shell
  is fine), **loose_verts == 0**, poly count within budget. Re-check construction-aid seams.
- Multi-angle **ortho** screenshots compared to the references, plus a final fresh-eyes
  critic pass. The numbers, not the screenshots, certify it.
- **Finish — edge bevels + shading (the hard-surface payoff).** Only now, with the form
  gated and `mesh_report` clean (bevel *amplifies* any leftover ngon / pole / fan — fix
  those first; a bevel cannot rescue bad topology, it makes it worse): round the edges with
  a **non-destructive, limited** bevel paired with weighted normals. Real manufactured edges
  carry a small radius — a true-sharp edge gives one hard specular line and reads as fake; a
  small rounded edge gives the soft highlight that defines the form.
  `add_bevel(obj, width, segments, limit='ANGLE')` then `finish_shading(obj)`:
  - **Small width, 2–3 segments** (1 = flat chamfer). Width *is* the radius — just enough to
    catch a highlight; large reads toy-like, tiny-crisp reads as machined metal. **Record
    width/segments as numeric parameters** matched to the material and any fillet radii the
    reference gives (metal → tiny/crisp; molded plastic → slightly softer).
  - **Limit it — never blanket-bevel.** `limit='ANGLE'` rounds only genuinely sharp edges;
    assign bevel **weights** + `limit='WEIGHT'` when different edges need different radii.
  - **Keep both as modifiers** (tunable) until the model is final; apply last — never bevel
    before booleans.
  - **This rounds sharp EDGES. A surface that is itself curved** (a domed top, a barrel, a
    grip swell) **is FORM — shape it in Phase 3, not with a bevel.** Check for *both*: the
    common failure is neither (sharp edges *and* flat sections that should be curved).
  - **Post-bevel `mesh_report` regression:** new non-manifold / ngons mean the width is too
    large somewhere (self-intersection) — reduce it or weight that region. A *melted* look
    means weighted normals / auto-smooth isn't set; a *mechanically uniform* look means it
    wasn't angle/weight-limited.
  - Honest: fine bevel *aesthetics* (does this radius read as steel?) stay a judgment the
    gates don't capture — the numeric params + topology check keep it from being *wrong*,
    not guarantee it's beautiful.
- Assign materials **to match the reference** *(example: a polymer-framed pistol is matte
  frame + semi-gloss slide, not chrome)*, hide the empties, switch to MATERIAL shading, and
  take 3/4 + ortho beauty shots. Procedural recipes cover steel with edge wear, polymer,
  moulded grip texture masked by a vertex attribute, and engraved markings. Scale-aware
  studio lighting matters most: a 0.2 m subject needs ~1/100 the usual wattage. All of this
  is in `references/materials-and-rendering.md`.
- Feed the fresh-eyes critic renders from 5 angles (both 3/4 views, front, rear, straight
  side) plus the IoU diff. Expect it to find missing small parts (pins, levers, cover plates)
  and material problems. Also expect some claims to be wrong or already covered by your
  measurements, so verify each one against the numbers before acting.

---

# The critique loop

Connective tissue of Phases 1–4; metric-driven (see *Objective alignment*). After **each**
meaningful edit:

1. Make **one** focused edit. (Batching edits is how you lose track of what went wrong.)
2. Run the **objective checks** for the current phase: `dim_gate`; `silhouette_iou` per
   masked view; `extent_profile` for form taper; `landmark_delta` for interior features.
   Record the raw numbers.
3. `set_ortho_view(axis, focus_object=...)` (xray on if the reference is behind) +
   `get_viewport_screenshot` — to **locate** the error, never to judge it acceptable.
4. Emit the **structured delta list** (no verdict language): each region off the reference,
   with direction and approximate magnitude.
5. `mesh_report` the part; fix any rise in **ngons** / **loose_verts** (and aid-seam
   non-manifold) first.
6. Fix only the **single largest measured delta**. Repeat from 1.
7. **Stop only when the phase's numeric gate passes** — never because the render "looks
   good". Then run the fresh-eyes critic before calling the phase done.

---

# Helper reference (`scripts/`)

| File | Function | Use |
|------|----------|-----|
| `set_ortho_view.py` | `set_ortho_view(axis, focus_object=None, xray=None)` | Snap to a clean ORTHO axis. **Call before every screenshot** — perspective lies about proportion. |
| `silhouette_metrics.py` | `silhouette_metrics(name)`, `dim_gate(name, len, ht, tol)` | **Proportion gate.** Measured spans + pass/fail vs real dimensions. No vision. |
| `silhouette_iou.py` | `silhouette_iou(names, mask, ref_empty=...)` | **Contour gate, any view.** Pixel IoU of the rendered silhouette vs a filled mask, camera derived from the named reference empty; require ≥ 0.90. Saves a diff overlay. |
| `extent_profile.py` | `extent_profile(name, along, measure, n_stations)` | **Form-taper gate.** Cross-extent sampled along the primary axis — mask-free check that the section actually varies. |
| `symmetry.py` | `add_mirror`, `add_screw`, `add_radial_array`, `add_linear_array`, `make_offset_empty`, `silhouette_symmetry` | Construction-strategy mechanisms + objective symmetry validation. |
| `landmark.py` | `landmark(name)`, `landmark_delta(name, target, tol)` | Coordinate gate for interior / 3D features no silhouette contains. |
| `bevel.py` | `add_bevel(name, width, segments, limit, angle_deg)` | **Finishing.** Non-destructive limited edge rounding (Bevel modifier) — small width, 2–3 segments, angle/weight-limited, never blanket. Gate behind a clean `mesh_report`. |
| `finish_shading.py` | `finish_shading(name, angle_deg)` | **Finishing.** Shade smooth + sharp-by-angle + Weighted Normal so flats stay crisp and only bevels round. Pair with `add_bevel`. |
| `zoom_region.py` | `zoom_region(xmin,xmax,zmin,zmax, mesh_name=...)` | Crop the ortho view to one region at high contrast so a critic can see mm-scale error. |
| `mesh_report.py` | `mesh_report(name) -> dict` | Topology QA: verts/faces, tris/quads/**ngons**, non-manifold edges, loose verts. |
| `add_reference.py` | `add_reference(path, axis, size, opacity)` | Quick true-scale reference loader as an aligned image empty. |
| `trace_profile.py` *(offline)* | `mask_from_photo, holes_of, smooth_mask, save_mask, trace, convex_opening, Frame` | Photo → filled mask → calibrated world-space contours; openings as clean cutter masks. |
| `sdf_mesher.py` *(offline)* | `mesh_part(mask, w, r, p, frame, round_mask, dw)`, `region_fields` | Rounded molded parts from side-view fields via marching cubes → `.npz`. |
| `import_npz.py` | `import_npz(path, name, ratio)` | Load an `.npz` mesh into Blender, fix normals, decimate, shade smooth. |
| `blender_ref.py` | `load_lum, crop, flip_h, measure_extent, calibrate_ppm, make_mapping, add_ref_empty, silhouette, box, shear_z, overlay_view` | Reference prep, true-scale calibration, silhouette sampling, Phase-1 box primitives. |

# Gotchas learned the hard way

- **Judge in ORTHO, always.** A silhouette compared in perspective will be wrong — the #1
  cause of loose models. `set_ortho_view` before every screenshot.
- **Validate symmetry before you commit to it** (`silhouette_symmetry`). Approximate
  symmetry treated as exact bakes the error into every mirrored/arrayed copy.
- **Secondary-axis edits must not move the primary silhouette.** Watch the transform pivot;
  re-run the primary IoU as a regression gate after form edits.
- **A construction aid still needs a clean seam.** Check the mirror plane / screw start-end
  / array joins with `mesh_report` (loose / non-manifold) — the usual first-pass failure.
- **One change at a time** through the critique loop.
- **`image.pixels` is bottom-up RGBA floats** — reshape `(H,W,4)` and flip rows (the
  helpers do this).
- **Image empty:** `empty_image_offset=(-0.5,-0.5)` centers the origin; for a landscape
  image `empty_display_size` == the image's world width; height follows pixel aspect.
- **Auto bounding boxes catch the dense body but miss thin 1px outlines** — align those by
  eye.
- **Shear, don't rotate**, for slanted features whose top/bottom edges must stay horizontal.
- **Reference figures can differ in scale per figure** — calibrate each view independently;
  never assume cross-figure consistency.
- **Apply booleans and limited-dissolve them**; an un-applied/un-cleaned boolean is the
  most common source of an exploding ngon count.
- Set empties `hide_select=True` so you don't grab them while modeling.
- **Keep the build as re-runnable scripts** (`build_frame.py`, `build_slide.py`,
  `build_details.py`, ...) plus one `rebuild_all.py`. Upstream fixes such as a better mask
  or a field tweak then propagate by re-running, instead of hand-patching a mesh whose
  booleans are already applied.
- **Photo texture leaks into silhouettes.** Stippling, knurling and engraving make bumps in a
  traced outline, which come back as ridges on the surface. Smooth those regions of the mask
  harder before tracing.
- **Long renders over MCP time out** (the socket gives up even though Blender finishes).
  Save the file and render with `blender -b file.blend -a` in a background shell.

# References

Long-form detail in `references/edit-mode-techniques.md`: the profile-extrusion recipe;
the two topology targets and their edit-mode moves; the **3D-form pass** (loop
segmentation, envelope, loft/bridge between section loops) and the **construction-strategy
mechanisms** (mirror/revolve/array seam cleanup); the boolean + flood-fill-opening recipe;
and bmesh patterns for headless edit-mode work.

`references/sdf-molded-parts.md`: field-based modeling of molded and organic parts (recipe,
starting values, flat caps, baked recesses, pitfalls).

`references/materials-and-rendering.md`: scale-aware lighting, procedural material recipes,
grip-texture attribute masks, edge wear, render and HDRI setup.

# Connection check (paste-and-run)

```python
import bpy
print(bpy.app.version_string, "| objects:", [o.name for o in bpy.data.objects])
```
If this errors at the MCP layer, the Blender addon server isn't running.
