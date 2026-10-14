# Look: control-room UI that predates the operator

Read the project's own UI style section first (irongrip: ASSETS.md S10, with S3 palette and S13 never). This file is
how to turn that kind of brief into assets.

## The two materials (irongrip S10)
- **Phosphor** (map, unit markers, dispatch, gauge, camera selection): amber on near-black, monochrome. States are
  intensity (`phosphor`, `phosphor_dim`), inverse video (alarm, selected), and a hard 3-step flicker on change.
  Never a second hue for alarms: a red lamp is a physical lamp on the bezel, not a phosphor colour. The CRT treatment
  (scanlines ~3 px at 0.16, a 1.5 px glow at 0.3, vignette) is a whisper; if it reads as "retro filter" it is too much.
- **Paper** (cards, dossiers, orders, end lists): two inks, ministry ink for text and icons, ministry ochre for heavy
  rules and stripes only (2.5:1 on paper: never text). Stamps in S3 "Dried" red-brown. Misalignment ±0.6°, 1 px
  registration offset, faint carbon second impression, typewriter inking (per-glyph opacity 0.78–1, ±0.5 px baseline)
  seeded by the text so a document never re-rolls.
- **Desk and bezels**: painted steel in ministry ink, lit top/left 1 px edge and a dark lip (a physical bevel, not a
  drop shadow), screws, engraved labels in a light grey that still clears 4.5:1.

## Icons
- 24 u grid, 2 u monoline, butt caps, mitred joins, integer coordinates. Solid fills only for small masses (heads,
  arrowheads, cartridge tips, the stamp). No curves where a straight line will do: the period is stencils and
  engraved plates, not rounded app glyphs.
- **Families before content.** The Governor should know the *kind* of mark before reading it: irongrip frames the
  state's own units in a square (the apparatus), brackets rebel objective candidates (suspected, unconfirmed), and
  leaves tools and incidents bare. Pick a system like this for any set over ~15 glyphs.
- **Variants share a base.** live/dead camera = same glyph + a slash; keep these pairs deliberately close and check
  the distinctness report treats them as intended.
- Optical sizing: circles look smaller than squares of the same box; a 9 u radius circle sits with an 18 u square.
- Test at the smallest size it is used (16 px map marker), then at the key size (24 px), then printed in ink.
- Never: flags (even a plain pennant reads as one), fists, stars, skulls, crowns, crosses, hammers, anything resembling
  a real party or national symbol (S13); emoji-style faces; gradients, glows baked in, drop shadows; outlined + filled
  duplicates of the same glyph as "states" (states are colour/intensity).

## Panels and templates
- Paint at each density directly; never upscale @1x.
- Nine-slice discipline: printed elements in corners; rules that run uniformly along an edge; grain on a periodic
  field of `tile_period_px` so edges and centre repeat (CSS `border-image-repeat: repeat`, Godot TILE). Torn edges are
  periodic too.
- Rotated nine-slices seam in browsers (each slice is drawn separately): composite first (`will-change: transform`
  on the panel) or rotate the content, not the panel.
- Bake no words except the copy sheet's printed ones (ministry names, imprint). Every label is a slot with a copy key.
- Slots: size each to one line of its style (`size × line`), anchor to the corner it belongs to, stretch only along
  axes the panel stretches.
- Photos on paper: greyscale, contrast up, multiplied onto the paper, held by photo corners.

## Anti-patterns (S10 "no modern UI vocabulary")
Rounded cards, drop shadows, soft elevation, toasts, pills, gradient buttons, hamburger/kebab icons, emoji, map pins,
material ripples, glassmorphism, neon glow, cyan/purple accents, pure #000/#FFF, icon fonts with filled-and-outlined
pairs, centred hero text. An alert is a card sliding into a tray. A dispatch is a form being stamped.
