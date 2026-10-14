# Formats

All JSON is UTF-8; sizes and rects are CSS pixels at density 1 unless named otherwise; colours are `#RRGGBB`
(sRGB). Paths inside a JSON are relative to that JSON's folder.

## ig_ui_tokens (`export/ui/ui_tokens.json`, + generated `ui_tokens.css`)
| Field | Meaning |
|---|---|
| `base` | name → hex: the S10 materials (screen, phosphor, paper) + chosen S3 swatches |
| `derived` | name → `mix(a, b, t)` recipe (sRGB lerp, t = share of b) |
| `color` | base + derived resolved to hex: the only colours UI may use |
| `roles` | `{fg, bg, use: text|large_text|icon|decor, fg_hex, bg_hex, contrast, note}` |
| `type.families` | `state`, `typewriter`: family, `files` (relative to export/ui), fallback, numerals, case |
| `type.scale` | style → `{family, size, weight, tracking_em, line, case, use}` |
| `space_px`, `stroke_px` | spacing steps; rule/frame weights |
| `icon` | grid, export px, densities, min legible px, variants (variant → colour token) |
| `motion` | flicker (ms, steps), card_in, stamp, persistence; each with the intended curve in words |
| `crt` | scanline period/opacity, glow px/opacity (a whisper), vignette, noise, where it applies |
| `paper` | misalign range, registration offset, typewriter inking (opacity range, jitter, heavy strike), carbon |
| `rebel` | the near-nothing HUD's few values |

CSS: `--ui-<colour>`, `--ui-font-<family>`, `--ui-space-<i>`, `--ui-<style>` (font shorthand) and
`--ui-<style>-tracking`, `@font-face` for the WOFF2 files, `.ui-<style>` classes.

## ig_ui_iconset (`export/ui/icons/<set>.json`)
| Field | Meaning |
|---|---|
| `icons` | name → `{svg: "<set>/ui_icon_<name>.svg", group, means?, canonical?, variant_of?, svg_16?}` (`svg_16`: hand-hinted 16 u master `ui_icon_<name>@16.svg`, from `GLYPHS16` in glyphs.py; draw it at 16 px instead of `svg`; the `16px` atlas already uses it) |
| `aliases` | data-model name (`incident:shots_fired`, `order:patrol`, `objective:radio`, `camera:sabotaged`) → glyph |
| `atlases` | variant → density (`"1"`, `"2"`, and `"16px"` for `icon.extra_px`) → `{image, size, cell, frames: {name: {x, y, w, h}}, color}` |

SVG: `viewBox 0 0 24 24`, `stroke="currentColor" stroke-width="2"`, butt caps, miter joins; fills use
`currentColor`. Atlas PNGs are straight alpha with the variant's RGB everywhere (safe to sample with bilinear and
mipmaps); a 2 px × density transparent gutter around every cell.

## ig_ui_nine (`export/ui/panels/<id>.json`) and ig_ui_layout (`export/ui/templates/<id>.json`)
| Field | Meaning |
|---|---|
| `size` | nominal size of the @1x image (the slot rects are in this space) |
| `slice` | `{l, t, r, b}` margins |
| `center` | `tile` (repeat), `stretch`, or `hollow` (transparent; something else shows through) |
| `edges` | `tile` or `stretch` |
| `images` | density → file |
| `content` | `[l, t, r, b]` padding of the content box |
| `slots[]` | `{id, kind: text|icon|image|stamp|ticks|lamp|buttons, rect, anchor: tl|tr|bl|br|stretch, stretch?: x|y|xy, style?, role?, data?, label_key?, icon?, treatment?, rotate_deg?, optional?, …}` |
| `css` | a ready `border-image` declaration |
| `godot` | NinePatchRect/StyleBoxTexture margins and axis mode |
| `treatment` (layout) | misalign range, typewriter inking, photo print filter |
| `variants` | optional name → density → file: the same panel with a different random detail (tear), pick one per instance |
| `stamp_band` | px to add to the height when a stamp slot is used, so the stamp lands below the text |

Placing a slot in a panel of size (w, h): `x += w - W` for right anchors, `y += h - H` for bottom anchors,
`sw += w - W` / `sh += h - H` when the slot stretches on that axis (W, H = `size`).

## ig_ui_fonts (`export/ui/fonts/ui_fonts.json`)
`files[]`: `{file, family, role, weight, style, bytes, sha256, license, license_file, copyright, source}`.

## ig_ui_chalkfont (`export/ui/fonts/ui_fonts_<name>.json`; kind `fonts`, brief `build: "script"`)
Drawn letter sprites, not a font. The fields are `case` ("upper"), `cap_px` per density, `variants`, `cell` (`top_em`, `bottom_em`,
`side_em`, `h_px`), `atlases.{chalk,keyline}[variant][density]`, `atlas_size_px["1"][variant]`, `metrics`
(`word_space_em`, `tracking_em`, `line_height_em`, `min_cap_px`), `kerning_em` ("AV": -0.1), and
`glyphs[ch] = {advance_em, w_em, frames[variant] = {x, y, w, h} @1x}`. Validated by `check_chalkfont`, which checks
coverage of `coverage_text` + `coverage_extra` uppercased, one cap height, variant difference, palette and atlas caps.
The layout rule is in integration.md "Chalk lettering".

## ig_ui_gauge (`export/ui/gauges/<id>.json`; optional kind `gauge`, prefix `ui_gauge_`)
An instrument, not a nine-slice: drawn at `size` (or exactly x2), never stretched.
| Field | Meaning |
|---|---|
| `layers[]` | `{id, z, role: static|fill|needle, images: {density: file}, note}`, drawn by `z` |
| `value_range` | `[lo, hi]` of the driving value |
| `fill` | `{layer, rect, axis, segments: {count, x0, pitch_px, gap_px, y, h}, snap, mapping, clip_css, godot}`: clip the fill layer to whole segments |
| `needle` | `{layer, pivot: [x, y], angle_deg: [at lo, at hi]}` or null |
| `bands[]` | `{id, from, to, label_key, x: [x0, x1]}`, ascending and contiguous over `value_range` |
| `ticks[]` | `{value, x, major}` |
| `source` | `{file, regex, thresholds, ...}`: where the band edges come from (re-read by the validator) |
| `motion`, `treatment`, `slots` | as for layouts |

## ig_ui_mapstyle (`export/ui/map/<id>.json` + `<id>.css`; optional kind `map`, prefix `ui_map_`)
| Field | Meaning |
|---|---|
| `background` | colour token the map is drawn on (contrast is measured against it) |
| `layers[]` | `{id, z, use: decor|line|marker|text, draws, data, stroke: {color, opacity, width_px, dash}, fill: {color, opacity}, label: {style, role, ...}, icon: {alias|name, px, far_px, color}, geometry, states: {name: overrides}, client: {babylon|proto2d|godot: the draw call it replaces}}` |
| `units`, `zoom`, `treatment` | width/dash in screen px, geometry in metres; near/far zoom rules; CRT and flicker |
| `rebel` | the other side's world-space marks, if the map style also covers them |
| `css` | generated custom properties `--ui-map-<layer>[-<state>]-stroke/-fill/-width/-dash` + `.ui-map-*` classes |

## ig_ui_hud (`export/ui/hud/<id>.json`; optional kind `hud`, prefix `ui_hud_`)
Overlay HUD art that is not a panel: sprites + parameters. (A HUD asset may also be an `ig_ui_nine`/`ig_ui_layout`,
e.g. a screen-edge vignette or a prompt; it then follows those formats and adds `params`.)
| Field | Meaning |
|---|---|
| `size` | the widget box at @1x (slot rects live here) |
| `layers[]` | `{id, size, images: {density: file}, rect?, tile_x?, role, ...}`: sprites; `tile_x` strips must wrap seamlessly in x |
| `slots[]` | as for layouts, plus `x_from: "bearing"` (moved along x at runtime), `repeat`, `opacity`, `icon: {set, name, variant: [under, over]}` |
| `params` | drive (which sim field), placement (anchor, px at 1080p), states, motion, `never`; clients read these, not literals |
Icon sets may declare `variants` / `variant_bg` / `variant_fx` (`dilate_u`, `grain`, `grain_min_px`, `streak`) in the brief.
