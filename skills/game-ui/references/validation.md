# Validation: every check

`ui_cli.py validate <brief>` → `<brief dir>/_work/validate.json` (`result`, `items[]` level/check/detail, `summary`).
Thresholds live in `assets/spec.json` → `ui`; change them there, with a note, never in code.

## All kinds
| Check | Rule |
|---|---|
| naming.id | `ui.id_regex`; kind from the prefix (`ui.kinds`) |

## tokens (`ig_ui_tokens`)
| Check | Rule |
|---|---|
| tokens.base_matches_spec | every base colour equals `ui.palette.base` / S3 swatch it names |
| tokens.derived:<k> | derived colour = `mix(a, b, t)` of two base colours, recomputed |
| tokens.no_pure_black_white | no colour with all channels < `no_pure_min` or > `no_pure_max` (S3) |
| contrast:<role> | WCAG ratio ≥ `contrast.text_min` (text), `large_text_min` (large_text), `icon_min` (icon); decor exempt (INFO) |
| type.family / min_px / tracking | styles use a declared family; size ≥ `type.min_px`; tracking −0.02..0.12 em (WARN) |
| type.files | the family's font files exist next to the tokens (build ui_fonts first) |
| space.ascending, tokens.css | spacing strictly ascending; CSS generated |

## fonts (`ig_ui_fonts`)
| Check | Rule |
|---|---|
| fonts.file / size | each file present, ≤ `type.max_font_bytes` |
| fonts.license_text | the OFL text ships beside the font |
| fonts.no_reserved_name | the OFL header names no Reserved Font Name (subsetting keeps the name legally) |
| fonts.coverage | every character of the copy sheet (brief `coverage_text`) is in the font |
| fonts.tabular_figures | the state face's digits share one advance (monospaced numerals) |
| fonts.licenses_md | assets/LICENSES.md has a row for the family |

## iconset (`ig_ui_iconset`)
| Check | Rule |
|---|---|
| icon.svg_whitelist | only svg/path/circle/rect/polygon/polyline; fill/stroke `currentColor` or none; no gradient, filter, opacity, transform, text, image, mask, style |
| icon.svg_bytes | ≤ `icon.max_svg_bytes` |
| icon.live_area | inked extent (half stroke and miter spikes included) inside `icon.live_u` |
| icon.grid_snap | points, centres and radii on `icon.snap_u` (canonical marks exempt) |
| icon.stroke | stroke width in `icon.stroke_u` |
| icon.min_feature | dots ≥ `min_feature_u` across, circles r ≥ 1 u |
| icon.transparent_padding | 2 u border clear at 24 px (atlas sampling never bleeds) |
| icon.crisp_24 (WARN) | ≤ `max_partial_px_frac_24` of inked pixels anti-aliased at 24 px |
| icon.legible_<min>px | the min-size raster keeps IoU ≥ 0.6 with an area-downsampled master, ≥ 6 px inked (a glyph with a hand-hinted `GLYPHS16` master is held instead to: ≥ 95 % of the master's pixels within 1 px of the area-downsampled 24 u glyph and ≥ 95 % of that glyph's pixels within 1 px of the master, since hinting moves edges ≤ 1 px on purpose; distinctness uses the master too) |
| icon.master16_crisp / _live_area / _svg | hinted 16 u master: ≤ 5 % partial pixels, ink within 1..15 px, `svg_16` exported and whitelisted |
| icon.distinct:<a>~<b> | top-5 most similar pairs at min size, family frames removed: IoU ≤ `distinct_iou_max` |
| icon.variant:<a>~<b> | pairs declared `variant_of` in META (live/dead camera): IoU ≤ `variant_iou_max` |
| icon.aliases | every alias targets a glyph |
| icon.contrast:<variant> | variant colour on its background ≥ `contrast.icon_min` |
| atlas.pow2 / bytes / colour / padding / frames | pow2 ≤ `atlas.max_px`, ≤ `atlas.max_png_bytes`, every pixel's RGB = the variant token (straight alpha, bled), gutters empty, all glyphs present |

## panel / template (`ig_ui_nine`, `ig_ui_layout`)
| Check | Rule |
|---|---|
| nine.slice_min / slice_fits / modes | margins ≥ `panel.min_slice_px`, l+r < w, t+b < h, modes from the spec |
| nine.image@Nx / size@Nx / bytes@Nx | one PNG per density at size × d, ≤ `panel.max_png_bytes` |
| nine.hollow@Nx | hollow centre is fully transparent |
| nine.tile_seam:<region>.<axis>@Nx | wrap difference ≤ neighbour difference + `panel.seam_tol` (centre both axes, edges along theirs) |
| palette.no_pure / saturation / mix_hull @Nx | no near-pure pixels; ≤1% pixels (value > 0.35) over the saturation cap outside the accent hues; ≤2% pixels further than `palette.pixel_tolerance` from any mix of two palette tokens |
| nine.edge_uniform:<edge>@Nx (WARN) | a tiled edge's profile along its axis deviates ≤ 0.06: a one-off feature in an edge repeats every tile. Intended repeats (ruled lines, a periodic tear) are declared in the brief `edge_repeat_ok` and reported as INFO |
| nine.density_drift | @2x downsampled vs @1x mean difference ≤ 0.06 |
| slot.inside / style / role / fits_line | slot rect inside the nominal size, style and role exist, height ≥ one line of its style |
| copy.baked:<text> | every `baked_text` string appears in the copy sheet |
| copy.key / copy.approved (WARN) | every slot `label_key` is in `src/ui/review_copy.json`; WARN until status is approved |

## gauge (`ig_ui_gauge`)
| Check | Rule |
|---|---|
| gauge.image / size / bytes / density_drift :<layer> | one PNG per layer and density at size × d, ≤ `panel.max_png_bytes`, @2x ≈ @1x |
| palette.* :<layer> | as for panels, per layer |
| gauge.fill_inside, gauge.segments_fit | fill rect inside the gauge, every segment inside the fill rect |
| gauge.lit_segments, gauge.lit_clean | the fill layer is lit inside every segment and empty outside them (a clip at a segment edge cuts nothing) |
| gauge.band_on_segment, gauge.bands_cover | band edges on segment boundaries; bands contiguous over `value_range` |
| gauge.source_thresholds | band edges = the numbers `source.regex` finds in `source.file` (repo-relative): a tuning change fails the gauge |
| gauge.pivot_inside, gauge.needle_range | needle drives only |

## map (`ig_ui_mapstyle`)
| Check | Rule |
|---|---|
| map.z_unique | one layer per z |
| map.color / opacity :<layer>[.<state>] | every stroke/fill/icon colour is a ui_tokens colour; 0 < opacity ≤ 1 |
| map.width, map.dash | 1-4 screen px; dash arrays even, positive |
| map.contrast | `use` line/marker: the stroke mixed onto the background at its opacity ≥ `contrast.icon_min`; decor INFO |
| map.label_style / role / contrast | label style and role exist; text at its opacity ≥ `contrast.text_min` |
| map.icon, map.icon_contrast | icon name or alias (`alias:<type>` = every alias of that family) exists in an icon set; colour ≥ icon_min |
