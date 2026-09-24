# Materials, surface detail and rendering

The Phase-5 payoff. A model with the right form still reads as CG if its materials are flat
or its lighting is off. Everything here is procedural, with no texture files needed.

## Scale-aware lighting (the #1 render mistake)

Handheld subjects are ~0.2 m and lights sit ~0.4 m away. Blender's "typical" area-light
wattage assumes metres-scale scenes. Irradiance goes with 1/d², so **divide typical wattage
by roughly (5 m / 0.5 m)² ≈ 100**:

| Light at ~0.4 m | Power |
|-----------------|-------|
| Key softbox 0.35 m | 8 W |
| Fill 0.5 m | 1.5 W |
| Rim strip 0.25×0.6 m | 10 W |
| Reflection strip 0.7×0.08 m (for metal) | 3 W |

Symptom of too much light: a near-black material (albedo 0.02) renders mid-grey and the
backdrop goes white. Fix the wattage, then trim with `view_settings.exposure` (≈ −2 was needed
here) rather than darkening albedos, which would be physically wrong.

**Metal needs something bright to reflect.** A dark slide in a dark studio shows no form;
add a long thin strip light that the flat faces can mirror.

## Material recipes (Principled BSDF)

- **Nitrided / blued steel:** base ≈ 0.03, metallic 1, roughness 0.36 ± 0.06 (noise on
  object coords), micro-bump noise at ~9000/m, strength 0.04.
- **Edge wear** (Cycles only): a Bevel node (radius 0.6 mm) gives a rounded normal. Take the
  dot product with the true normal; where it drops (edges), map through
  `MapRange(0.985 → 0.90)`, multiply by noise, and mix base color toward ≈ 0.2 grey.
  Worn machined edges then appear without any texture painting.
- **Molded polymer:** base ≈ 0.013–0.02, roughness ≈ 0.58–0.62, specular ≈ 0.45, fine
  noise bump (~6000/m).
- **Moulded grip texture** (pyramids, stipple): a pyramid grid from object coordinates,
  `1 − 2·max(|fract(u/pitch) − 0.5|, |fract(v/pitch) − 0.5|)`, scaled and clamped for flat
  tops, pitch ≈ 1.5–2 mm. Multiply it by a **vertex attribute** mask (sample an eroded
  side-view panel mask per vertex in numpy → `mesh.attributes.new('stipple', 'FLOAT',
  'POINT')` → Attribute node). **Project per face orientation**: use (X, Z) where the normal
  is mostly ±Y and (Y, Z) where it is mostly ±X, or the texture smears into streaks on
  front and back surfaces.
- **Engraved markings:** a text curve (extrude ≈ 0.4 mm, centred on the surface) converted to
  a mesh and used as a boolean difference on the (low-poly) part. Make up serial numbers;
  don't copy a real one from a reference photo.
- **White sight inserts:** separate tiny meshes with a white rough material, not texture.

## Render setup

- Cycles, GPU (`compute_device_type = 'METAL' | 'CUDA' | 'OPTIX'`, then `get_devices()`),
  denoise on. 64–96 samples for checks, 256 for finals.
- View transform AgX (or Filmic on older builds).
- Product shot: camera 70–100 mm lens, f/11 depth of field focused on the subject, a
  seamless dark backdrop plane.
- Environment shot: a CC0 HDRI from Poly Haven
  (`https://api.polyhaven.com/files/<asset>` → `hdri['4k']['hdr']['url']`) on an Environment
  Texture node. No extra lights are needed.
- Render check views (3/4 both sides, front, rear, and a straight side view) and hand those
  to the fresh-eyes critic subagent.
- Long renders: save the .blend and render with
  `blender -b file.blend --python enable_gpu.py -a` in a background shell. A long render
  inside an MCP call can exceed the socket timeout even though Blender finishes the job.
