# The `ui` section of assets/spec.json

The machine-readable copy of the project's UI rules. The design doc wins on conflict; transcribe changes here with
a note. irongrip's section is the reference; for a new project copy it and change the values.

| Key | What |
|---|---|
| `owner` | claims owner for `ui_*` |
| `id_regex`, `kinds` | `ui_iconset_*`, `ui_panel_*`, `ui_tpl_*`, `ui_tokens`, `ui_fonts`: src and export folders per kind; `icon_prefix` (`ui_icon_`) |
| `palette` | `base` (S10 materials), `from_swatches` (S3 names taken from `spec.palette.swatches`), `derived_rule`, `no_pure_min/max`, `accent_hues` (hues allowed above the saturation cap), `max_saturation_outside_accents`, `pixel_tolerance` (distance to the mix hull) |
| `contrast` | WCAG minimums per use |
| `icon` | `grid_u` 24, `live_u` [2, 22], `snap_u` 1, `stroke_u` [2], `min_feature_u` 2, `export_px` [24, 48], `densities`, `min_legible_px` 16, `variants` (name → colour token), `distinct_iou_max`, `max_partial_px_frac_24`, `max_svg_bytes` |
| `atlas` | `pad_px`, `pow2`, `max_px`, `max_png_bytes` |
| `panel` | `densities`, `tile_period_px` 64, `seam_tol`, `max_png_bytes`, `min_slice_px`, modes |
| `type` | `families` (state, typewriter: family, files, axes, numerals), `scale` (named styles), `min_px`, `max_font_bytes` |
| `space_px`, `stroke_px` | spacing steps, rule weights |
| `review` | background, icon sizes, zoom, mock size, the feed image and portrait folder the plates use |
| `never` | the style guide's forbidden UI vocabulary, quoted for the critic |

Adding it to a new project: copy irongrip's block, replace `base`/`from_swatches` with that project's palette,
re-derive `accent_hues`, pick the families and scale from its typography section, set `review.feed_image` to any
in-game render, and run `ui_cli.py claim`.
