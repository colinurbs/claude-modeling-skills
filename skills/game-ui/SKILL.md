---
name: game-ui
description: Build engine-ready game UI assets and prove them at game size. Covers icon sets (SVG sources authored on a grid, exported as currentColor SVG plus pre-coloured PNG atlases per density), nine-slice panels and frames (monitor bezels, paper slips, forms) painted procedurally with seamless tiling, form/card/dossier templates with slot layouts both clients can place text, icons and images into, a design-token JSON (palette roles with contrast, type scale, spacing, motion, CRT and paper treatments) plus generated CSS, and licensed fonts (OFL, verified, subset to WOFF2). Pipeline: brief with intent → author (glyph DSL or numpy painter) → export → automatic validation (palette compliance, grid snap, stroke, live area, transparent padding, legibility at the minimum size, distinctness, contrast, tiling seams, slot fit, naming, file sizes, licences, words from the copy sheet) → one headless Chromium review (icons at 16/24/32 px on the control-room screen, CRT treatment, ink on paper, frames, cards, dossiers, a mock console) → fresh-eyes critic → manifest and claims. Use it for ANY game UI art or HUD asset work, e.g. "make the unit icons", "the camera feed frame", "incident card", "dossier template", "design tokens for the HUD", "which font can we ship", "the ui_ list in ASSETS.md §11", "does this icon read at 16 px", even if the user just says "draw an icon for X" or "style the Governor panel" inside a game project. Client code changes stay with the client; this skill delivers assets and integration asks.
---

# game-ui

A UI asset is a **contract with two clients**, like a mesh or a clip. It is done when it:

1. **meets the delivery spec exactly** (`assets/spec.json` → `ui`): named `ui_*`, colours that are palette tokens or
   declared mixes of two, icons on the grid with the spec stroke, nine-slices with sane margins that tile without a
   seam, slots that fit their type style, fonts with their licence beside them, files inside the size caps;
2. **is readable where it is used**: icons at 16 px on the phosphor screen and printed on paper, text at its real
   size with enough contrast, a card at the width of the tray;
3. **looks like the game, not like an app** (irongrip: ASSETS.md S10 paper and phosphor; S13 never): no rounded
   cards, drop shadows, gradient icons, toasts, cartoon or modern-flat styling, real-world symbols.

Numbers are checked by scripts, the look by renders: yourself first, then a fresh-eyes critic. This is the UI
counterpart of **game-asset-modeling**, **game-animation** and **game-vfx**: same spec file, claims registry,
manifest, review slots (one headless browser on the machine at a time). It imports their code; it does not copy it.

## Why this format (engine-agnostic)

| Deliverable | Format | Why |
|---|---|---|
| Icons | one SVG per glyph (`currentColor`, 24 u grid) + PNG atlases per variant (`phos`, `ink`) and density (@1x, @2x) + a set JSON (frames, aliases from sim names) | DOM inlines SVG and tints with CSS; Godot imports SVG or uses `AtlasTexture`; canvas/Babylon use the atlas. Pre-coloured atlases avoid tinting a white mask (S3: no pure white anywhere). |
| Panels | `ig_ui_nine` JSON + PNG @1x/@2x | Nine-slice is native everywhere: CSS `border-image`, Godot `NinePatchRect`/`StyleBoxTexture`, Babylon GUI `Image.stretch = NINE_PATCH`. Grain on a 64 px periodic field so edges and centre tile (no stretched grain). |
| Templates | `ig_ui_layout` = a nine-slice + slots (text/icon/image/stamp/ticks, rect, anchor, stretch, style, role, data, copy key) | The paper is baked, the words are not: clients fill slots from sim data and the copy table, so localisation and new copy never need a repaint. |
| Tokens | `ui_tokens.json` (`ig_ui_tokens`) + generated `ui_tokens.css` | One source both clients parse (JSON) and the DOM uses directly (CSS custom properties, `@font-face`, `.ui-<style>` classes). |
| Fonts | TTF (Godot, tools) + WOFF2 subsets (browser) + OFL text | Licence travels with the file; subsets keep the web payload small. |

## Setup

- game-asset-modeling installed beside this skill with its `tools/setup.sh` run (Playwright + Chromium, numpy, PIL,
  opencv). game-animation installed too (the review lock and sheet compositor are imported from it).
- `pip3 install --user resvg-py fonttools brotli` (deterministic SVG rasteriser, font subsetting).
- The project spec needs a `ui` section (`references/spec-ui.md`). irongrip has it.
- Claim the family: `ui_cli.py claim` (default `ui_*` → `ui.owner`). Export `GAM_OWNER=<owner>` in every shell.

All commands: `python3 ~/.claude/skills/game-ui/scripts/ui_cli.py …` from inside the project's `assets/`.

## The loop

### 0. Brief: `ui_cli.py new ui_<kind>_<name>`
Kinds come from the id prefix: `ui_iconset_*`, `ui_panel_*`, `ui_tpl_*`, `ui_tokens`, `ui_fonts`. Write the
**notes first, as intent**: who reads it, where, at what size, under which treatment ("16 px map marker on the
phosphor screen", "340 px slip in the side tray, 11-13 px type"), what the style guide demands and forbids. List
any words to bake into `baked_text`; they must exist in the project's copy sheet (irongrip:
`assets/src/iconography/copy.md`). Everything else is a slot with a `label_key` in `src/ui/review_copy.json`
(status `approved` / `client` / `proposed`): **new words are listed for the human, never invented into art.**
Read `references/look.md` before drawing.

### 1. Author
- **Icons**: `glyphs.py` in the brief folder, the DSL in `scripts/ui_icons.py` (line, poly, rect, circle, arc,
  fill, dot; 2 u stroke on integer coordinates so edges land on whole pixels). Design families so the *kind* reads
  before the content (frames, brackets, bare). Import canonical marks (an emblem) from their owner module; never
  redraw them. Map the data model's names to glyphs in `ALIASES` so clients need no table; declare deliberate state pairs (`META[name] = {"variant_of": base}`) so the distinctness check holds them to the looser variant cap.
  Glyphs that must be crisp at 16 px (map markers) can get a hand-hinted pixel master: `GLYPHS16 = {name: fn(g)}` on a
  16 u grid (1 u = 1 px, whole-pixel fills); it feeds the `16px` atlas, exports `ui_icon_<name>@16.svg` (`svg_16`),
  and the validator and review plates use it at 16 px. Test a set cold: a critic names each glyph from an unlabelled sheet.
- **Panels/templates**: `build_<id>.py` with `build(ctx)`, painting with `scripts/ui_panels.py` (`Canvas`: grain,
  rule, box, fill_rect, edge_light, disc, icon, text, torn_edge, clear_rect) at each density, returning
  `nine_json(...)` with slots. Printed elements go in corners or run uniformly along an edge; anything else repeats every
  tile (`nine.edge_uniform` warns; declare intended repeats in `edge_repeat_ok`). Give random detail (a tear) a few
  `variants` so stacked copies differ.

Build order: fonts → tokens → icon sets → panels → templates (later kinds read earlier exports).

### 2. Build + validate: `ui_cli.py all <brief.json …>` (gate: no FAIL)
`validate_ui.py` (`references/validation.md`). Fix the cause, not the threshold; a threshold wrong for the project
is a spec change with a note. WARNs worth reading: `icon.crisp_24` (diagonals/curves), `copy.approved` (words
awaiting approval).

### 3. Review: `ui_cli.py review` → look, then run the critic
ONE Chromium (DOM only, GPU off), all slots held. Plates in `review/ui/`, per-asset sheets `review/<id>.png`, the
mock `review/ui_control_room.png`, measurements in `review/ui_review.json` (page errors, text-fit overflow per
slot, fonts actually loaded). Plates and what to look for: `references/review.md`. Read them yourself, then run
the **fresh-eyes critic in the FOREGROUND** with the prompt there. Fix the biggest blocker/major, rebuild, re-review,
until only minors remain.

### 4. Deliver
Set `status: done` in each brief, `ui_cli.py manifest …`. Report: assets, sizes, validate/critic results,
integration asks (`references/integration.md`), words needing approval.

## Never

- Never edit client code: hand over integration asks. Never launch the game client to check UI; the harness renders
  the exported files on their own.
- Never bake words that are not in the copy sheet; never invent canonical copy.
- Never run two reviews at once (the command holds every review slot); on a crash-prone machine run nothing heavy
  beside it.

## Files

| Path | What |
|---|---|
| `scripts/ui_cli.py` | claim, new, build, validate, review, manifest, all, status |
| `scripts/uilib.py` | paths, palette and token resolution, colour maths (mix, WCAG contrast), pixel palette report |
| `scripts/ui_icons.py` | glyph DSL, SVG writer, resvg rasteriser, atlas packer |
| `scripts/ui_panels.py` | numpy painters (periodic grain, print with uneven ink, torn edges), `nine_json` |
| `scripts/ui_build.py` | builders: tokens (+CSS), fonts (+WOFF2 subsets), icon sets, panel/template script runner |
| `scripts/validate_ui.py` | validator (game-asset-modeling's `Report`) |
| `tools/review/shoot_ui.mjs`, `harness_ui.html` | headless review plates |
| `templates/` | brief templates per kind, starter `glyphs.py`, `build_panel.py`, `build_template.py` |
| `references/format.md` | `ig_ui_tokens`, `ig_ui_iconset`, `ig_ui_nine`, `ig_ui_layout`, `ig_ui_fonts` field by field |
| `references/spec-ui.md` | the spec section and how to add it to a new project |
| `references/look.md` | control-room look, icon families, paper and phosphor recipes, anti-patterns |
| `references/validation.md` | every check and threshold |
| `references/review.md` | the plates, reading them, the critic prompt |
| `references/integration.md` | how the Babylon DOM client and the Godot client load tokens, fonts, icons, nine-slices, layouts |
