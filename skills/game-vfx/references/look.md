# Look: per-class recipes and the grounded-vs-Hollywood checklist

Real numbers first, then restraint. Sizes are world metres, times seconds, at 60 Hz.

## Muzzle (event `fire` at `socket_muzzle`)
| | pistol 9 mm | SMG 9 mm (WWII, open bolt, ~9 rps) | sawn-off 12 ga |
|---|---|---|---|
| flash core (camera sprite) | 0.09-0.13 m, 2-3 frames | 0.07-0.10 m | 0.20-0.27 m |
| side plume (stretched) | 0.17-0.22 m long | 0.12-0.16 m | 0.36-0.47 m |
| sparks (unburnt powder) | none | none | 6-10, 7-13 m/s, ~0.1 s |
| smoke | 2-3 wisps, alpha ~0.3, ~1 s | 1-2, alpha ~0.22 (bursts stack) | 4-6, alpha ~0.4, ~1.5 s |
| light | tungsten, ~2.5, 4.5 m, 0.05 s | ~2.0, 4 m, 0.04 s | ~3.5, 6 m, 0.06 s |
Short barrels flash bigger (more powder burns outside). Flash keys are warm grey-gold (#E2CFA8 → #C49A64), never
orange-red; alpha starts below 1. Smoke rises slowly (buoyancy +0.2 m/s²), drags hard (3/s).
First person: the camera sees the flash from behind, as a star with a short plume pointing into the screen.
From the side it reads as a plume. Check both views.

## Shell (event `shell_eject` / `case_eject` at `socket_eject`)
A mesh with physics is right (it lands, rolls, rings). The sprite fallback tumbles (900-1600 °/s) out along the
socket's -Z at 1.6-2.4 m/s and fades just before it would land. Add a faint port wisp.

## Impacts (event `hit` at point + normal; decal + sfx hooks)
| surface | reads as | emitters |
|---|---|---|
| concrete/plaster | pale grey pop + chips + slow haze | dust pop 0.10-0.14 m ×3 alpha 0.6 for ~0.3 s; haze 0.14-0.2 m ×3.5 alpha 0.35 for 1-1.6 s, sinking; 6-9 chips 1-2.6 cm at 2.5-5.5 m/s |
| metal | tiny flash + short sparks + flakes | flash 0.04-0.06 m 2 frames; 7-12 sparks (stretched ×6, 4-9 m/s, ≤0.25 s, gravity); 2-4 paint flakes; one grey whiff |
| wood | splinters + fibres + brown dust | 4-7 splinters 2-4.5 cm tumbling; 4-6 fibres; 2-3 dust puffs #8A7F70 |
| flesh | small dark mist + droplets + cloth puff | mist #5A1E1A alpha ≤0.45, ≤0.32 s; 3-5 droplets 6-12 mm; cloth dust; decal on the ground below |
| glass | glints + shards, no dust | one 2-frame glint 7-9 cm + 5-8 tiny stretched glint streaks over 0.2 s (add, near-neutral, sat < 0.08); 9-13 slivers 3-6 cm (`t_vfx_impact_glass_shard_bc`: translucent body, dark side-on rims so they read on pale AND dark backgrounds) falling straight down; 4-6 glitter crumbs; a very faint powder puff alpha ≤ 0.22; `dec_bullet_hole_glass_a`. Most real spray leaves the exit side (engine ask) |

## Tracer (class `tracer`, event `fire` at `socket_muzzle`)
Not tracer ammunition: a readability cue, kept faint. One stretched streak (`t_vfx_tracer_streak_e`), ~1.9 m × 3 cm,
~300 m/s, starting 0.6 m past the muzzle (no beam out of the gun in first person), alpha ≤ 0.35, warm grey (#C8AE84).
Additive, so it all but vanishes over overcast grey and reads at night. The engine aims it at the hit and clips its life.

## Sabotage sparks (class `spark`, event `cut` at `socket_cut`)
A live cable bitten through: a 1-2 frame arc 4-5 cm across the jaws (near-neutral cold white, sat < 0.08, alpha ≤ 0.45,
a re-strike at 2 frames), 11-16 short copper sparks (stretched ×6, 0.8-2.2 m/s, ≤ 0.35 s, gravity, first key alpha low
so the burst at the spawn point does not stack to white), 2-4 slow glowing beads, one grey insulation whiff. A short
pale warm light (the spec caps VFX light hue to tungsten). The feed static is the feed shader's job, not a particle effect.
Emit along the surface normal (local -Z), cone 25-55°. A ricochet direction (reflect the shot) is an engine ask.

## Smoke, fire, explosions (next tiers)
- Tear gas is a **gameplay volume** first: the sim owns radius, density and drift; the effect reads them (loop, rate ×
  density, size × radius). It must show the volume's edge honestly; never denser than the gameplay.
- Fire (molotov, barrel): the only orange light in the city (S6). Flame BODY alpha-blended (unlit, `_e` flame sheet) so it
  stays orange by day, a small ADDITIVE CORE (hot band at the fuel, <= 30% of the flame area, value x alpha <= 0.36: spec
  `fire_look`), alpha smoke, a flickering PointLight (hook curve), sparks. It reads from across the district at night.
  Read as fire, not haze: give the body structure on its own sheet (`flame_lick`: one tongue per sprite with a crisp
  ragged edge, a side lick, the tip pinching off as a tuft late in life, hot band above the root and darker sooty tips:
  the luminance structure is what reads on the grey CCTV feed), a low bed of short tongues filling the fuel surface, and
  the smoke column starting above the tips.
- Explosions: a short flash with light, a dust/debris ring, then a long smoke column; scorch decal. Avoid the Hollywood fireball.

## Grounded, not Hollywood: checklist
- [ ] Real size and duration (a flash is frames, not seconds).
- [ ] No pure white: keys ≤ #E6E6E6-ish; the review's clipped share ≤ 2% after the grade.
- [ ] No glow halos, lens flares, starburst logos, heat distortion as style.
- [ ] Smoke and dust greys and browns (sat ≤ 0.25); the ministry ochre and chalk accents are never used by effects.
- [ ] Blood dark, small and brief; never the brightest or reddest thing on screen.
- [ ] Night: lit emitters darken with the scene (`lit: true`); only emissive things glow.
- [ ] CCTV: impacts register as a puff at 320×240; flashes may not, which is honest.
