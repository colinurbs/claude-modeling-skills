# Review: reading the sheets and running the critic

`anim_cli.py review` renders in Babylon (the engine), on the real bodies with head and
outfit, converting clips exactly as retarget.ts does. Per clip `review/<set>/<clip>.png`:

| Panel | Camera | Look for |
|---|---|---|
| strip (per body) | three-quarter, in place, 8 frames | posture, lean, gaze, arm swing, hands, knee direction, cloth poke-through, female body vs male |
| side (onion) | ortho from the right, root motion on | stride, hip bob, heel strike / toe off, lean, feet at ground |
| top (onion) | ortho from above | root path straightness, facing ticks, contact discs (orange L, blue R, red = slid) and their spacing |
| CCTV | mast 3.5 m, 9 m, 320×240 feed | does it read as a person going somewhere; readable cadence; nothing robotic |
| feet chart | — | heel/ball height per foot, contact bars, root speed; stance should sit near 0 cm |
| mp4/gif (`--video`) | tracking three-quarter, loops played twice | the seam, pops, rhythm |

`review.json`: engine console errors (must be none), clips as Babylon loaded them, joint
positions as posed (the CLI prints the worst difference to the build's FK; should be 0.00 cm).

## Critic subagent
Spawn a separate agent (no stake in finishing). Give it paths, not your opinion:

```
You are a fresh-eyes animation critic for <project>. Find faults; do not praise.
Read-only: do not edit files or launch any browser/3D app.
Clip sheets: <assets>/review/<set>/<clip>.png and the per-view PNGs in review/<set>/<clip>/
Brief with intent notes: <animset.json>; validator: <_work/validate.json>; meta: <set.meta.json>
Style bar: <paste S8>. Delivery rules: <paste §0.8>.
For each problem: view, frame(s), what is wrong, how far off (cm/deg/%/rule), likely pipeline
cause (retarget rest alignment, root extraction, foot lock, loop seam, proportions/body
variant, the harness itself). Classify blocker/major/minor, rank by harm to what the
Governor's CCTV must read. Check posture (lean, gaze), arm swing and hands, foot plant and
slide, knee pops, hip sway, female vs male body (skirt/limb intersections, overstride), whether
the loop reads as 2 steps, and whether any view is misleading. "No issues" only if checked.
```

Verify each claim against numbers before acting (validate.json, feet chart, a quick FK
measurement): in the first run the critic was right about skirt poke-through and a clamped
chart axis, half right about step width (8.8 cm: narrow but human), and wrong that the CCTV
tiles were in place. Fix the largest blocker/major, rebuild, re-review. Done = validate PASS,
playback PASS, no engine errors, critic reports only minors (or items owned elsewhere, e.g.
outfit weighting → character pipeline).
