# Integration: how each client loads the UI assets

The skill never edits client code. These are the asks to hand over (irongrip: `apps/client` Babylon + DOM Governor,
`apps/godot` Godot 4 rebel client). Paths are relative to `assets/export/ui/`.

## Shared
- Read `ui_tokens.json` once at boot. Colours only from `color`/`roles`; type only from `type.scale`; spacing from
  `space_px`; motion and treatments from `motion`, `crt`, `paper`. No literal colours in client code.
- Map data to icons through `icons/<set>.json` → `aliases` (`incident:<type>`, `order:<type>`, `unit:<kind>`,
  `camera:live|sabotaged`, `objective:<type>`).
- Place template slots with the anchor/stretch rule in `references/format.md`. Text comes from sim data or the copy
  table, never from the art.

## DOM (Babylon client, Governor console)
- `<link rel="stylesheet" href="/assets/ui/ui_tokens.css">` (serve `export/ui/` statically; the CSS's `@font-face`
  points at `fonts/*.woff2` beside it). Drop the Google Fonts `<link>` (Barlow Condensed, Cutive Mono, VT323) and the
  oklch/hex literals in `gov/governor.css` and `style.css` in favour of `var(--ui-*)` and `.ui-<style>` classes.
- Icons: fetch the SVG once, inline it, colour with CSS `color` from a role (`phos.icon`, `paper.icon`). For the
  canvas/overlay markers use the `phos` atlas frame (@2x on hi-dpi).
- Panels: the `css` field of each nine-slice JSON is a ready `border-image` declaration. Put `will-change: transform`
  on any rotated paper so the slices composite before rotation.
- Cards and dossiers: build from `templates/*.json` slots; apply `paper.typewriter` per glyph (seeded by the text),
  `treatment.misalign_deg` per card, `motion.card_in` on arrival, `motion.stamp` on stamping.
- CRT: one overlay per phosphor surface (scanlines, a 1.5 px glow at 0.3 opacity, vignette) from `crt`; flicker on
  state change = 3 opacity steps over `motion.flicker_ms`.

## Godot (rebel client, and any Godot Governor later)
- Fonts: `FontFile` from `fonts/RobotoCondensed-wght.ttf` (variable: set `variation_opentype = {"wght": 500}`) and
  `fonts/CourierPrime-*.ttf`; replace the `SystemFont` (Menlo) in `scripts/hud.gd`.
- Build a `Theme` at load from `ui_tokens.json` (`JSON.parse_string(FileAccess.get_file_as_string(...))`): colours
  via `Color.html(hex)`, font sizes from `type.scale`. Replace the literal `Color(...)` constants in `hud.gd` with
  token lookups (`chalk` for the rebels' few marks; `soot` for the suspicion edge).
- Icons: `AtlasTexture` with `region = Rect2(frame.x, frame.y, frame.w, frame.h)` on `icons/<set>_phos@2x.png`, or
  import the SVG directly (Godot rasterises SVG at import scale).
- Nine-slices: `NinePatchRect` (or `StyleBoxTexture`) with `patch_margin_*` from `slice` and
  `axis_stretch_* = TILE` when `edges == "tile"`; `draw_center = false` for `center == "hollow"`. Use the @2x image
  with margins ×2 on hi-dpi.

## Babylon GUI (if any panel moves into the 3D canvas)
`Image` with `stretch = Image.STRETCH_NINE_PATCH` and `sliceLeft/Top/Right/Bottom` from `slice` (it stretches, it does
not tile: keep such panels near their nominal size). Icons via `Image` with `sourceLeft/Top/Width/Height` from atlas frames.

## Gauges (`ig_ui_gauge`) and map styles (`ig_ui_mapstyle`)
- Gauge: stack `layers` by `z` at the nominal size (or exactly x2 with the @2x images; never stretch). Drive the
  `fill` layer by clipping it to whole segments (`fill.mapping`): DOM `clip-path: inset(0 <W - right>px 0 0)`;
  Godot `TextureProgressBar` (under = face, progress = lit, over = bezel) or a clipping `Control`. Bands and ticks are
  data; the band names are slots. Motion from `motion` (flicker on a band change, persistence on drop, no tweened sweep).
- Map style: draw every map layer in `z` order with `color` (a ui_tokens colour) at `opacity`; `width_px`/`dash` are
  screen px, geometry in metres. A `states` entry overrides the layer while it holds. The generated `<id>.css` carries
  the same values as `--ui-map-*` properties plus `.ui-map-*` classes for DOM overlay elements.

## irongrip asks: S-tier Governor pieces (2026-10-04, irongrip-ui)
Client code is not ours; these are the asks for `apps/client` (Babylon + DOM console) and `apps/godot`.

**Dispatch** (`panels/ui_panel_dispatch.json`, `templates/ui_tpl_dispatch_slip.json`)
- Replace `.board` (gov/console.ts, governor.css: phosphor `.slip` rows over a gradient) with the steel board at the city's
  width x 158: nine-slice `css`, plate label `desk.dispatch`, officers lamps (`officers` slot: 12 + 2 lenses, lit from
  `manpower` / `riotAvailable`; the header's `.men` row can go).
- One paper slip per `GovUnit` in the `slips` row (left to right by issue, 10 px gap, 240 px wide, variant per slip):
  icon `order:<order>` (`unit:riot` for riot squads), unit label, issue time, order, status from `status_map` (same
  precedence as governor.ts today), serial. Pending = no stamp + 4 px lower; on ack type nothing new, land `stamp.ack`
  (`motion.stamp`) and keep `audio.ack()`. Recall = click the counterfoil (`recall` slot): it tears off, then
  `order({type:'recall'})`. Remove `.slip` blink/ackFlash (no blinking on paper).

**Unrest gauge** (`gauges/ui_gauge_unrest.json`)
- Replace `Meter` (console.ts SVG needle with jitter, red riot arc) with the three layers at 320x76 in `.hdr-meter`
  (widen that header column from 190 to 336). `set(v)`: lit = whole segments per `fill.mapping`; crossing a band
  edge flickers the lit layer; drops decay over `persistence_ms`. No jitter. CRT only over `fill.rect`.
- Bands are read from `packages/sim` tuning by the build and re-checked by the validator: if `unrest.thresholds`
  change, rebuild the gauge (`ui_cli.py all src/ui/gauges/ui_gauge_unrest/brief.json`).
- The Situation lamps (Calm / Suspected / Declared) stay a separate instrument (sim AlertLevel, not Unrest); the
  Declared lamp's red blink should become phosphor + inverse label (a red lamp only if it is a physical bezel lamp).

**Probable objectives** (`panels/ui_panel_objectives.json`)
- Replace `.hdr-cand` (VT323 text with ▸ bullets) with the panel (300-380 wide, 148 tall for three) in the side
  column or the header: one `rows` row per `GovernorSnapshot.candidates[]` in sim order: `objective:<type>` glyph,
  site label, district name (district rect containing `pos`). Selected (camera centred there) = inverse row; quiet
  = dim. Clicking a row focuses the city on the site.

**Map** (`map/ui_map_style.json` + `.css`)
- governor.ts: `coneOn` ochre / `coneOff` grey -> `camera_cone` (phosphor fill 0.07, selected 0.14 + phosphor edge,
  tracking 0.11, sabotaged = faint dashed outline, no fill); `candRings` ochre torus -> `candidate` (phosphor_dim
  dashed 6 m ring + bracketed glyph + label); `lockLines` ochre -> `lockdown` (phosphor 2 px + 0.05 fill + MM:SS
  readout); `flash()` red `#e04a2a` disc -> `incident` pulse ring in phosphor + the incident glyph in an inverse box
  for severity 3; `unitLines` -> dashed `route`; `.reticle` circle -> corner brackets (`hover`); add `sightline`
  (camera to an observed mark while TRACK) and the `unit`/`camera` glyph markers on the overlay at far zoom.
  `paintFog` keeps its numbers; its veil colour is `screen`, never `#0c0e10`/pure black. District names stay
  `state_label` in `phos.text_dim`. Use `ui_map_style.css` classes for `.pip`, `.zone`, `.lbl` and drop the
  oklch/VT323 values there.
- apps/proto2d draw.ts/governor.ts: same table (each layer's `client.proto2d` names the call it replaces).
- apps/godot markers.gd (rebel client): `CAM_ON` red / `CAM_OFF` grey cones -> `rebel.rebel_camera_cone` (chalk fill
  0.07 + chalk edges 0.35; sabotaged: dashed faint edges, no fill). Proposal for Colin: it changes what the rebels see.

**Detention order** (`templates/ui_tpl_detention_order.json`)
- New document: open it when a `citizen_detained` incident arrives for a marked subject (typed fields in `lifecycle`
  order; photo = the dossier's still), close it on the matching `detain_end` (disposition `end.guilty`/`end.innocent`,
  then `stamp.detained`), and file it with the dossier. Needs the sim/server to send `detain_end` (name, guilty) to
  the Governor during the round (today only the end message carries it).

**End screen** (`templates/ui_tpl_endscreen.json`)
- Replace `endScreen()`'s HTML table (main.ts) with the register at 720 px centred on the desk: header, outcome stamp
  (`end.contained` / `end.failure`), reason, stats and objective lines (`end.stats`, `end.objective` templates), one
  64 px row per `detained[]` in detention order with `portrait(look)` in the photo box (photo_print), name, age,
  occupation, file, disposition; `end.total` at the foot; New round as a desk button below the paper. Rows type in
  one after another; the outcome stamp lands last. The optional per-row RELEASED stamp waits on Colin.

## HUD overlays (`ig_ui_hud`, optional kind `ui_hud_*`)
- A HUD asset is either an `ig_ui_nine`/`ig_ui_layout` (place it like a panel/template) or an `ig_ui_hud`: `layers[]`
  of sprites (`size`, `images` per density, optional `rect` in the widget box, `tile_x` for scrolling strips) +
  `slots[]` (same fields as layouts; `x_from: "bearing"` = moved along x at runtime) + `params` (drive, placement,
  states, motion, never). Clients read `params` instead of hard-coding numbers.
- Icon sets may declare their own `variants` (brief) instead of the spec's `phos`/`ink`: e.g. `chalk` over `keyline`
  (the same glyph grown 1 u in charcoal). Draw the keyline frame, then the main frame, same rect: a hard edge that
  holds on bright and dark scenes, not a drop shadow. Runtime text over a scene gets the same keyline (DOM: four 1 px
  `text-shadow`s with no blur; Godot `outline_size = 2`, `font_outline_color` = charcoal).

## irongrip asks: rebel HUD (2026-10-11, irongrip-ui)
For `apps/client` (rebel.ts, style.css) and `apps/godot` (hud.gd, markers.gd). Paths under `export/ui/`.

**Icons** (`icons/ui_iconset_rebel.json`): `chalk` + `keyline` atlases (@1x, @2x, 16px); SVGs for the DOM (colour
`chalk`, keyline via a second copy in `charcoal` with `stroke-width: 4` or the keyline atlas). Map sim names through
`aliases` (`step:<kind>`, `cache`, `squad[:down]`, `unit:<kind>`, `camera`, `prompt:*`, `me:*`).

**Suspicion edge** (`hud/ui_hud_suspicion.json`): replace the ochre `box-shadow` on `.vignette` (rebel.ts renderHud)
and the `ATTENTION` text label (hud.gd) with the hollow nine-slice over the viewport. Drive from `me.attentionTier`
(the web snapshot's `me.attention`): `params.tiers[tier]` gives opacity, reach (border = `border_px_at_1080 x reach x
vh/1080`) and pulse; `stopped_override` while stopped/rejected; `transition` rise/fall. No text, no bar, no number.

**Prompt** (`hud/ui_hud_prompt_papers.json`): replace `.prompt` / `.stopped` text (rebel.ts) and `prompt` Label
(hud.gd) with the layout, bottom-centre, width fitted to the text. Slots: `key` (bound key), `icon` (alias
`prompt:*`), `text`, `alt` (dim, right). Words from `review_copy.json` `hud.prompt.*` (proposed/client, not yet
approved) with the `chalk_jitter` treatment; variant by hash of the key. Papers themselves stay held in hand (S10).

**Compass** (`hud/ui_hud_compass.json`): replace `_compass()` text (hud.gd) and add to the web client: scroll the
`strip` layer by heading (`params.heading`, `zero`, `x0_px`), mask with `window`, `index` wedge under it; cardinals
and bearing icons in slots by `params.slot_x`. Objective bearing `obj_target` (`obj_extract` for extract), squad at 0.45.

**Ammo** (`hud/ui_hud_ammo.json`): only while armed (`params.visible_when`); pips from `round`/`round_spent`, the
`item_ammo` glyph, reserve digits. **Needs the sim to carry ammo** (magazine, loaded, reserve per weapon): RebelState
has `armed/drawn/weapon` only today; until then the client can hide the pips and show the glyph alone while drawn.

**World markers** (`hud/ui_hud_markers.json`): billboard `objective` / `objective_extract` at the step position with
the distance slot (`hud.unit.metres`), `squad` / `squad_down` over each squad mate at `ui_tokens.rebel.marker_opacity`,
and the `edge` chevron for an off-screen objective; sizes, occlusion and fades in `params`. Replaces the squad-name
text list (`squad: ...` in renderHud, hud.gd squad Label): positions only, never names (S10 near nothing).

## irongrip asks: front-end screens (2026-10-11, irongrip-ui)
Client code is not ours; these are the asks for `apps/client` (main.ts `lobbyScreen`/`endScreen`, style.css) and `apps/godot`.

**Role select** (`templates/ui_tpl_role_select.json` + `panels/ui_panel_role_rebel.json`)
- Before joining, replace the three `data-role` buttons with the `screen` composition (1600x900 nominal, scale to the
  viewport, split at 50%): left = `desk` fill + the console nine-slice at 520x340, top 150 (title `role.governor`,
  `role.governor.brief`, `seat` = `role.desk.vacant` / `role.desk.taken`, `join` = `role.join_governor`, lamp lit
  while a Governor is in `lobby.players`, CRT over the tube only); `spectate` under it. Right = the wall panel at
  800x900 (tiles; poster + chalk mark stay top-left). The client needs the lobby BEFORE joining to light the lamp and
  disable the desk: today it only receives `lobby` after `join` (ask the server for a read-only peek, or show the
  desk as unknown until joined).
- Rebel words use `treatment.chalk_hand`: chalk colour, per-glyph jitter seeded by the text, CSS `mask-image` with
  `extra_images.chalk_mask` (64 px repeat); hover = the `underline` strip revealed left to right. Godot: a CanvasItem
  shader multiplying the label's alpha by the mask, per-glyph offsets via RichTextEffect.
- Name/room inputs move onto the lobby form's fill-in lines (`ui_tpl_lobby` `room`, `name`, `input: true`).

**Lobby** (`templates/ui_tpl_lobby.json`)
- Replace the `<ul>` roster with the paper roster at 560 wide: one 32 px row per `lobby.players[]` in join order (no
  grouping by role), `role.<role>` typed, a typed `X` in the printed box when `ready`; own row in `type_head`.
  Height = 134 + 64 x ceil(n / 2) + 60. `note` carries `lobby.range` / `lobby.inprogress` / `lobby.needs` / server
  errors verbatim. Desk keys under the paper: `lobby.ready` (NEW: sends `{type:'ready'}`, which the server already
  handles) and `lobby.start` (disabled with `lobby.needs` as the reason).

**Round summary** (`templates/ui_tpl_round_summary.json`)
- New page one of `endScreen()` (the register `ui_tpl_endscreen` becomes page two behind `summary.register`), same
  page for both roles, 720 fixed width. `plot`: polyline of the `end` message's log entries `kind == 'trace'`
  (`data.unrest`, every 5 s) with x = t / roundSeconds, 1.5 px `ministry_ink`, a typed x at the last sample, no fill;
  `y_ticks` / `x_ticks` numerals; band names from `chart.bands` (= ui_gauge_unrest). `rows`: `objective.steps[]`
  (needs the server to add `steps: {label, done}[]` to the `end` message's `objective`; today it sends type, label,
  candidates only). `photos`: up to ten `portrait(look)` prints from `detained[]`, then `end.total`. Rebels see the
  extra `ending` line (`end.rebel.won` / `end.rebel.lost`); the Governor's copy leaves it blank.
- Godot (rebel client): the round summary is the one screen the rebels share with the state; build it from the same
  JSON (NinePatchRect TILE, slots via the anchor rule) when the Godot client gets an end screen.

## Chalk lettering (`ig_ui_chalkfont`, drawn letter sprites instead of a font)
For a faction whose words must be drawn, never typeset (irongrip S10 rebels). `ui_fonts_<name>.json` with
`build: "script"` in the brief. It holds capitals only, several drawings per glyph, and per variant a `chalk` atlas
and a `keyline` atlas (@1x/@2x, same frames). Lay out a runtime string like this (the reference is
`src/ui/_review/0_chalk_letters.mjs` `chalkWords`):
- Uppercase it first. A char with no glyph uses its NFD base (`Ǎ` -> `A`), or else a word space. Never fall back to a font.
- Scale `s = cap / cap_px["1"]`; `cap` is the slot's `cap_px` (capital height in css px). The pen starts at x = 0.
  For each glyph, take variant `v = (hash(text) + i + 3 * occurrence) % variants`, so a word never re-rolls and LL/EE
  never repeat a drawing. Draw `glyphs[ch].frames[v]` (@1x px in variant v's atlas; double it for @2x) at
  `(pen - side_em*cap, baseline - top_em*cap)`. Then advance by `advance_em*cap + tracking_em*cap`, plus `kerning_em[prev+ch]*cap`.
  Words are separated by `word_space_em*cap` and wrap only between words. The line height is `line_height_em*cap`.
- Over a scene (HUD), draw the keyline frame first, then the chalk frame in the same rect. The colours are baked; never tint.
- DOM: one absolutely placed span per glyph, using the @2x atlas as `background-image` scaled by `s`. Godot: an
  `AtlasTexture` per frame in a `Control._draw()` (`draw_texture_rect_region`), or a `FontFile` built from the frames
  (bitmap font, one variant per glyph: loses the variant rule, so prefer `_draw`).

### irongrip asks: rebel lettering (2026-10-11, irongrip-ui)
- `fonts/ui_fonts_rebel.json` replaces the `chalk_hand` treatment (role select, above) and the `chalk_jitter`
  treatment (rebel prompt) for every rebel runtime word. Slots now carry `treatment: "chalk_letters"` and `cap_px`
  (role wall title 52, brief 15, cell 18, join 22; prompt key/text/alt 10 with NO keyline, because they sit on the soot strip;
  compass cardinals and marker distance 9 with the keyline). Role-select hover is `extra_images.arrow`, a chalk arrow 48 px left of
  the join words, revealed along the stroke; it replaces the underline. `role.cell.count` may draw the `|` tally glyph instead of the numeral. The world-marker distance is drawn
  as capitals ("42 M"): the set has no lowercase, so `hud.unit.metres` is uppercased like everything else.
- Web (`apps/client` rebel.ts, main.ts role select): port `chalkWords` and fetch the 8 atlases (4 variants x chalk/keyline) at @2x.
  Godot (`hud.gd`): the same layout in `_draw()`. The old Roboto Condensed + jitter + mask path can be deleted once this is in.

### irongrip asks: new incident glyphs (2026-10-13, irongrip-ui)
`icons/ui_iconset_gov.json` gained four incident glyphs and their aliases (sim `IncidentType`, packages/sim/src/types.ts):
`incident:graffiti` -> `inc_graffiti` (spray can and spray), `incident:explosion` -> `inc_explosion` (wide low blast with thrown chunks),
`incident:fire` -> `inc_fire` (flame on a ground line), `incident:gas` -> `inc_gas` (canister and drifting fumes). Both clients already
resolve `incident:<type>` through `aliases`, so no code change is needed; the `inc_report` fallback in `apps/client/src/game/gov/map.ts`
(`mkGlyph`) and `gov/console.ts` (`placeIcon`) simply stops firing for these four. Atlases were repacked: read frames from the JSON, never cache x/y.
