# blender-modeling-skills

A [Claude Code](https://claude.com/claude-code) skill for modeling **real-world objects in
Blender from reference images**. It aims for accurate proportions, correct 3D form and clean
topology, rather than a stack of boxes or a flat extruded slab.

Claude drives a live Blender session through the
[BlenderMCP](https://github.com/ahujasid/blender-mcp) server. The skill's main idea is that
**measured numbers, not screenshots, decide when something is done**. Every phase has to
pass numeric gates before the next one starts:

| Gate | What it checks |
|------|----------------|
| `dim_gate` | Each part's measured spans against real dimensions (±5 %) |
| `silhouette_iou` | Rendered silhouette against a filled reference mask, per view (≥ 0.90) |
| `extent_profile` | Cross-section width sampled along the part, which catches the "uniform slab" failure |
| `landmark_delta` | Interior features that no silhouette shows |
| `mesh_report` | ngons, non-manifold edges, loose verts |

A separate "fresh-eyes critic" subagent reviews zoomed renders. It has no stake in calling
the model finished.

## What's in it

```
skills/reference-modeling/
├── SKILL.md                       # the workflow: phases 0–5, gates, critique loop, gotchas
├── references/
│   ├── edit-mode-techniques.md    # profile extrusion, loft/bridge form pass, booleans, bmesh
│   ├── sdf-molded-parts.md        # rounded/molded parts from side-view fields (marching cubes)
│   └── materials-and-rendering.md # scale-aware lighting, procedural materials, render setup
└── scripts/
    ├── (run inside Blender via MCP)
    │   set_ortho_view · silhouette_metrics · silhouette_iou · extent_profile · landmark
    │   mesh_report · symmetry · bevel · finish_shading · zoom_region · add_reference
    │   blender_ref · import_npz
    └── (run offline in CPython)
        trace_profile.py   # photo → filled mask → calibrated world-space contours
        sdf_mesher.py      # side-view width/radius/squareness fields → smooth solid (.npz)
```

### Workflow in brief

1. **Research and set up.** Find orthographic references (blueprints, patent drawings, or a
   square-on product photo) and pin down the real dimensions. Choose a construction strategy
   (revolve, mirror, array or freeform) from what the references show. Calibrate the image
   to true scale and build a filled silhouette mask for each view.
2. **Blockout** with primitives until the proportions pass `dim_gate`.
3. **Primary silhouette.** No new primitives from here on. Use edit-mode profile extrusion
   traced from the mask. For molded parts, use the **SDF inflate** method instead.
4. **3D form.** Add loop cuts at section changes, set the envelope, shape cross-sections
   with loft/bridge, and gate with `extent_profile`.
5. **Detail.** Align booleans to the reference before cutting, then clean up afterwards.
6. **Final QA.** Re-run every gate, add limited bevels with weighted normals, apply
   materials, and render beauty shots.

## Install

```bash
git clone <this repo>
mkdir -p ~/.claude/skills
ln -s "$PWD/blender-modeling-skills/skills/reference-modeling" ~/.claude/skills/reference-modeling
```

Code blocks in `SKILL.md` load helpers from `~/.claude/skills/reference-modeling/scripts`.
If you install the skill elsewhere (for example a project's `.claude/skills/`), update that
path.

### Requirements

- **Blender 4.x–5.x** with the **BlenderMCP** add-on running (sidebar `N` → BlenderMCP →
  Start), and the BlenderMCP server configured in Claude Code. Tested on Blender 5.1.
- For the offline helpers, run `pip install -r requirements.txt` (numpy, scipy, pillow,
  opencv-python-headless, scikit-image).
- Web access, so Claude can find reference images.

## Usage

Ask Claude Code something like:

> model a Stanley No. 4 hand plane in Blender from references, as realistic as you can

The skill triggers on requests to model a recognizable real object, to set up reference or
blueprint images, or when a model "looks blocky/flat/fake".

## Honest limits

The gates make sure a model isn't *grossly* wrong, and they stop premature "looks right"
verdicts. They don't guarantee beauty. Subtle compound curvature and fine surface detail
still take iteration, and whether a bevel radius "reads as steel" is still a judgment call.

## License

MIT, see [LICENSE](LICENSE).
