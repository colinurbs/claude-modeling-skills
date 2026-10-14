# Review: the plates, reading them, the critic

`ui_cli.py review [briefs…]` (default: every ui brief) writes one job, takes every review slot on the machine
(game-animation's `ExclusiveReview`), and runs ONE Chromium page (`--disable-gpu`, DOM only, device scale 1). Each
plate is screenshotted at the size the game draws it, so a 16 px icon is 16 real pixels. Outputs:

- `review/ui/<plate>.png`: the plates; `review/<asset id>.png`: the plates for that asset stacked;
  `review/ui_control_room.png`: the mock.
- `review/ui_review.json`: `errors` (page errors, failed requests: must be empty), `fonts` (faces actually loaded:
  the families in ui_fonts must appear, otherwise the plates show fallbacks), `fits` (per slot: overflow_x/y).

| Plate | Shows | Look for |
|---|---|---|
| icons_screen | every glyph at 16/24/32 px in phosphor on the screen colour, by family; 4x nearest-neighbour atlas frames | does each read at 16 px, families tell apart, stroke weight even across the set, optical size (a circle glyph looks smaller than a square one), no glyph busier than the rest |
| icons_crt | 24 px tools with the CRT treatment (scanlines, glow whisper, vignette) in four states (normal, dim, flicker step, inverse); markers over a dark map with labels | still legible under scanlines, glow stays a whisper (S9), dim state readable, inverse works as the alarm, markers read on the map next to each other |
| icons_paper | every glyph in ministry ink on paper at 16 and 24 px | holds up printed; fills not too heavy |
| panel_feed | the feed frame at main, mux and an odd stress size with an approximate feed inside | bezel reads as a physical monitor not a card, tiling seams at the stress size, lip and screws scale, emblem placement, label plate legibility |
| cards | four sim incidents in the tray, severity ticks, a stamp, one card at 2x | paper not app, text fits (also see `fits`), the torn edge, priority boxes, misalignment subtle, typewriter inking subtle |
| dossier | a new file (CCTV still) and a processed file (portrait, five observations, stamp) | the form reads as printed, header and imprint, photo corners, ruled fields line up with typed values, lines tile as the file grows |
| tokens | the type scale on screen and paper; every colour role with its contrast | hierarchy, tracking (S10: caps, tight), numerals tabular |
| mock_console | 1600x900 control room built only from the assets | does the whole feel like an operator's desk predating the Governor; anything modern (S10 vocabulary list) |

Project plates: `ui_cli.py review` also loads every `<src>/ui/_review/*.mjs` as a plate module
(`export default async (H) => {...}`; `H` = the harness helpers: job, tokens, `plate`, `nine`, `put`, `slotRect`, `typed`,
`icon` (async), `lens`, `style`, `roleFg`, `fits`). Use it for assets the built-in plates do not cover (gauges, map styles,
new templates) instead of editing the harness; push text measurements into `fits` so overflow is reported.

Known harness simplifications: the feed picture is a static render with a CSS approximation of the S9 feed
treatment (the real one is the engine's); there is no animation (flicker is shown as one step; card slide and stamp
motion are tokens only); the map is a schematic.

## Critic subagent (run in the FOREGROUND, read-only)

Spawn a separate agent with no stake in finishing. Give it paths, not your opinion:

```
You are a fresh-eyes game UI critic for <project>. Find faults; do not praise. Read-only: do not edit files and do
not launch any browser or app; look at the PNGs with the Read tool (crop mentally; the plates are at game size).
Plates: <assets>/review/ui/{icons_screen,icons_crt,icons_paper,panel_feed,cards,dossier,tokens,mock_console}.png
Measurements: <assets>/review/ui_review.json (page errors, fonts loaded, text-fit overflow per slot).
Briefs with intent notes: <assets>/src/ui/**/brief.json. Glyph sources: <assets>/src/ui/icons/<set>/glyphs.py.
Exports: <assets>/export/ui/ (ui_tokens.json, icons/*.json, panels/*.json, templates/*.json).
Validator results: <assets>/src/ui/**/_work/validate.json. Copy table: <assets>/src/ui/review_copy.json.
Style bar: <paste the project's UI style section (irongrip: S10 Governor/Rebels/Typography/Iconography), the
palette rules (S3), writing (S12) and the never list (S13)>.
For each problem: plate, element (glyph name, slot id, region), what is wrong, how far off (px, contrast ratio,
which state), likely cause (glyph geometry, painter, slice margins, token, harness), and the fix. Classify blocker /
major / minor; rank by harm to (1) legibility at game size, (2) the period look (no modern app vocabulary, palette
discipline), (3) consistency across the set, (4) engine practicality (tiling, slots, sizes). Check every glyph at
16 px: name what it reads as to a first-time player, and flag any two that could be confused, any real-world
symbol, anything cartoonish. "No issues" only if you checked.
```

Verify each claim against the files before acting (validate.json, the JSON, a crop of the plate). Fix the
largest blocker/major, rebuild, re-review. Done = validate PASS on every asset, `ui_review.json` errors empty and no
overflow on required slots, critic reports only minors.
