# claude-modeling-skills

[Claude Code](https://claude.com/claude-code) skills for making 3D game content with measured
gates instead of "looks right" verdicts. Five skills share one idea: every phase has to pass
numeric checks (dimensions, silhouettes, triangle budgets, validators, headless engine renders)
before the next one starts, and a separate fresh-eyes critic reviews the result with no stake
in calling it done.

| Skill | What it makes | Proves it with |
|---|---|---|
| `reference-modeling` | real-world objects in Blender from reference images | dimension and silhouette gates, extent profiles, mesh report |
| `game-asset-modeling` | engine-ready glTF: kit pieces, props, vehicles, weapons, decals, characters | triangle budgets, UV checks, the Khronos validator, Babylon review renders |
| `game-animation` | game clips from open motion capture on a shared skeleton, incl. first-person sets | loop seams, foot slide, joint limits, engine playback |
| `game-vfx` | particle effects as portable JSON plus painted flipbook sheets | schema and budget validation, overdraw and frame-strip review |
| `game-ui` | icon sets, nine-slice panels, templates, design tokens, licensed fonts | palette and legibility gates, headless review at game size |

The four `game-*` skills were built for one project (an asymmetric police-state game with a
Babylon web console and a Godot first-person client) and carry its conventions in their
references; the pipelines, validators and review harnesses are general.

## reference-modeling

Model **real-world objects in Blender from reference images**, aiming for accurate
proportions, correct 3D form and clean topology rather than a stack of boxes or a flat
extruded slab. Claude drives a live Blender session through the
[BlenderMCP](https://github.com/ahujasid/blender-mcp) server. Every phase has to pass
numeric gates before the next one starts:

| Gate | What it checks |
|------|----------------|
| `dim_gate` | Each part's measured spans against real dimensions (±5 %) |
| `silhouette_iou` | Rendered silhouette against a filled reference mask, per view (≥ 0.90) |
| `extent_profile` | Cross-section width sampled along the part, which catches the "uniform slab" failure |
| `landmark_delta` | Interior features that no silhouette shows |
| `mesh_report` | ngons, non-manifold edges, loose verts |

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
git clone git@github.com:colinurbs/claude-modeling-skills.git
mkdir -p ~/.claude/skills
for s in reference-modeling game-asset-modeling game-animation game-vfx game-ui; do
  ln -s "$PWD/claude-modeling-skills/skills/$s" ~/.claude/skills/$s
done
# the game skills' review and validation tooling (Babylon, Playwright, the Khronos validator)
sh ~/.claude/skills/game-asset-modeling/tools/setup.sh
```

Code blocks in the skills load helpers from `~/.claude/skills/<skill>/scripts` and
`.../tools`. If you install a skill elsewhere (for example a project's `.claude/skills/`),
update that path. The `game-*` skills read a project's `assets/spec.json`, `ASSETS.md` and
`assets/manifest.json` for budgets, naming and delivery status; see each `SKILL.md`.

### Requirements

- **Blender 4.x–5.x**. `reference-modeling` needs the **BlenderMCP** add-on running (sidebar
  `N` → BlenderMCP → Start) and the BlenderMCP server configured in Claude Code; the `game-*`
  skills drive Blender headless from the command line. Tested on Blender 5.1.
- **Node 20+** for the game skills' tooling (`tools/setup.sh` installs it).
- For the offline helpers, run `pip install -r requirements.txt` (numpy, scipy, pillow,
  opencv-python-headless, scikit-image).
- Web access, so Claude can find reference images.

## Usage

Ask Claude Code something like:

> model a Stanley No. 4 hand plane in Blender from references, as realistic as you can

> build the delivery truck from ASSETS.md as a static blocker, with LODs and a collider

> retarget this CMU walk onto our skeleton as a seamless loop with root motion

> make the muzzle flash and the tear-gas volume for the riot launcher

> design the dossier template and the unit icon set for the control room

Each skill triggers on its own kind of request; the descriptions in the `SKILL.md`
frontmatter say what counts.

## Honest limits

The gates make sure a model isn't *grossly* wrong, and they stop premature "looks right"
verdicts. They don't guarantee beauty. Subtle compound curvature and fine surface detail
still take iteration, and whether a bevel radius "reads as steel" is still a judgment call.

## License

MIT, see [LICENSE](LICENSE).
