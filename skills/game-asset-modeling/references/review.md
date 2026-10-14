# Review: reading the sheet and running the critic

`gam_cli.py review` renders the asset in Babylon (the engine), not Blender. Views:

| View | Camera | Look for |
|---|---|---|
| `front` | ortho, straight on (glTF +Z) | proportions vs the mannequin, facing direction, pivot/ground contact |
| `three_quarter` | persp 35° elevated | overall form, bevel highlights, material separation |
| `eye` | persp at eye height (spec), FPS-like FOV | **what a player on foot sees**: scale, readability, tiling, texel density |
| `iso` | ortho from the commander angle, framed | top surfaces, roofs, silhouette from above |
| `iso_context` | same angle at gameplay scale (≈40 m wide) | does it read at all at the size the commander actually sees it |
| `feed` | CCTV mount height, low res, tinted, scanlines | does it read through the apparatus |
| `lods` | LOD0/1/2 side by side, edges on | silhouette kept, no collapsed parts, sensible triangle flow |
| `collision` | render at 25% + collider wire | collider covers the walkable/blocking volume, no snags, no gaps |
| `uv_density` | checker at 64 texels/cell + reference cube at target density | cell size ≈ reference; no stretching; consistent across parts |
| `sabotaged` / `broken` | auto when present | state reads; pieces make sense (broken pieces are spread apart) |

`review.json` holds engine console errors (must be empty), the extras as Babylon parsed
them (confirm the props made it), LOD triangle counts, sockets with world positions.

## Critic subagent

Spawn a separate agent (it has no stake in finishing). Give it file paths, not your
opinion:

```
You are reviewing a game asset for <project>. Find faults; do not praise.
Asset brief: <path to brief.json>
Style guide excerpt: <paste the relevant rules: palette, weathering layers, "Never" list>
Validator output: <path to validate.json>
Engine renders: <path to review/<id>.png> and the individual views in review/<id>/
Reference/type photos (if any): <paths>

For each problem give: view, region, what is wrong, how far off (size in cm, % of
dimension, or which rule), and the likely fix. Rank by how much it hurts the asset in its
PRIMARY view (<eye|iso|feed|held>). Classify each as blocker / major / minor. Check
specifically: scale vs the 1.75 m mannequin, whether it reads as the intended type within
2 seconds, silhouette at iso_context and feed, texel density vs the reference cube,
material separation (metal vs wood vs paint), style-guide violations, LOD2 silhouette,
collider fit. Say "no issues" for a category only if you checked it.
```

Treat its list as the work queue: verify each claim against the numbers (validate output,
review.json) before acting — critics are sometimes wrong about scale in perspective views.
Fix the largest blocker/major first, rebuild, re-review. Done = validate PASS, no engine
errors, critic reports only minors.

## Things the numbers can't see (look for them yourself)

- Wrong facing (front at +Y) — check `front` shows the front.
- Floating or sunk (look at the ground contact in `front`).
- Dark seams on UV edges (bake margin too small, or mip bleed).
- Weighted-normal artefacts: smeared shading on long thin triangles.
- "Everything the same roughness" — materials must separate under overcast light.
- Too clean for its layer / too dirty for a state addition (irongrip S4).
