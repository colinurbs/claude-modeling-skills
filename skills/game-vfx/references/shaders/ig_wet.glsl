// ig_wet.glsl -- reference snippet for class `shader`, type `surface` (game-vfx round 5): vfx_wet.
// A wet-surface modifier applied to a PBR material's inputs BEFORE lighting (Lagarde 2012/2013 "water drop" approach):
//   porous darkening of the albedo, smoother roughness, flattened normal where water pools, and rain ripples on
//   up-facing surfaces from the tiling data tile t_vfx_wet_ripple_n (R ring coord, G/B direction, A phase; LINEAR, no sRGB).
// Plain GLSL ES functions, no engine names. Babylon: a MaterialPluginBase on PBRMaterial (CUSTOM_FRAGMENT_UPDATE_ALBEDO /
// _MICROSURFACE / _BEFORE_LIGHTS for the normal), uniforms set per frame from the weather; Godot 4: a spatial .gdshader
// include, called in fragment() before ALBEDO/ROUGHNESS/NORMAL_MAP are written (sampler: `: repeat_enable, filter_linear`).
// The review renders THIS file (tools/review/shoot_shader.mjs).

uniform float u_time;
uniform sampler2D u_ripple_tex;   // t_vfx_wet_ripple_n
uniform float u_wetness;          // 0 dry .. 1 soaked (the weather drives it; puddles force 1)
uniform float u_rain;             // 0 .. 1 rain intensity (ripple density/strength); 0 = still water
uniform float u_darken;           // albedo multiplier at full wetness on a fully porous surface (0.55 = 45 % darker)
uniform float u_roughness_wet;    // roughness of a film of water
uniform float u_puddle_flatten;   // how much a puddle replaces the surface normal with the water's (0..1)
uniform float u_ripple_tiles_m;   // ripple tiles per metre (1 / tile size)
uniform float u_ripple_speed;     // ring cycles per second per drop
uniform float u_ripple_strength;  // normal perturbation of a ring (0..1)
uniform float u_up_threshold;     // world normal.y above which water collects (ripples, flattening)
uniform float u_ripple_fade_m;    // ripples fade out from this view distance to 2x it (they alias and sparkle beyond)
uniform vec3  u_water_tint;       // multiplies the albedo under standing water (palette `soot`/`cast`-ish greys, S3)
uniform float u_edge_darken;      // albedo multiplier on the soaked margin just outside standing water (the darker puddle edge)

// one ripple layer: returns a tangent-space xy perturbation
vec2 ig_ripple(vec2 uv, float t, float weight) {
  vec4 r = texture2D(u_ripple_tex, uv);
  vec2 dir = r.gb * 2.0 - 1.0;
  float dropFrac = fract(r.a + t);
  float timeFrac = dropFrac - 1.0 + r.r;
  float dropFactor = clamp(0.2 + weight * 0.8 - dropFrac, 0.0, 1.0);
  float f = dropFactor * r.r * sin(clamp(timeFrac * 9.0, 0.0, 3.0) * 3.14159265);
  return dir * f;
}

// worldPos.xz drives the ripple uv; nY = world normal y; porosity 0 (glass, metal, puddle water) .. 1 (concrete, brick);
// puddle = 0..1 standing-water depth mask (dec_puddle_a's soft alpha, or a height/AO-derived mask; feather it): water fills
// where mask > 1.15 - wetness (fully above 0.5 at soak), the band outside it is the darker soaked rim; viewDist = camera
// distance in metres. Modifies albedo, roughness, tangent normal. The ripple perturbation is in the ground's tangent space and
// assumes tangent = world +X, bitangent = world -Z (flat ground UVs); rotate p by the tangent frame otherwise.
void ig_wet_surface(inout vec3 albedo, inout float roughness, inout vec3 nTS, vec3 worldPos, float nY, float porosity, float puddle, float viewDist) {
  // Toksvig: the mip-filtered tangent normal's length (< 1 where the normal map's detail is below the pixel) is its variance;
  // nTS must arrive UNnormalised (sample * 2 - 1, mipmapped). Folded into the film's roughness below: specular AA.
  float nLen = clamp(length(nTS), 0.05, 1.0);
  float nVar = (1.0 - nLen) / nLen;
  float up = smoothstep(u_up_threshold, 1.0, nY);
  float wet = clamp(u_wetness, 0.0, 1.0);
  float pm = clamp(max(puddle, 0.0) * up, 0.0, 1.0);
  float water = smoothstep(1.2 - wet, 1.6 - wet, pm) * step(0.001, wet);    // standing water fills from the deepest part of the mask as wetness rises
  float rim = smoothstep(0.0, 0.3, pm) * wet;                                 // soaked margin, continuing under the water: ground > rim > water
  float wd = min(1.0, wet * 1.6), wf = wet * sqrt(wet);                        // pores darken first, the surface film comes later
  albedo *= mix(1.0, u_darken, wd * porosity);                                 // porous darkening
  albedo *= mix(1.0, u_edge_darken, rim * porosity);                           // darker edge
  albedo = mix(albedo, albedo * u_water_tint, water);
  // a damp film lowers roughness proportionally (keeps the material's own variation: damp, not plastic), floored at 0.3
  // (never raised above the material's own): below that the ground's normal detail glitters under lamps (specular aliasing)
  float rFilm = max(0.3, u_roughness_wet * (0.55 + 0.5 * roughness));
  roughness = mix(roughness, min(roughness, rFilm), wf * mix(0.55, 1.0, up));
  float aT = roughness * roughness;                                            // Toksvig in GGX alpha: a^2 += variance
  roughness = mix(roughness, sqrt(sqrt(aT * aT + min(nVar, 0.25))), wf);       // only where the film lowered it (dry = untouched)
  // the film fills the pores: it flattens the normal detail as it forms (a soft sheen, no glitter); the water's own normal
  // comes in ahead of its mirror roughness, so a half-filled edge never sparkles
  nTS = normalize(mix(nTS, vec3(0.0, 0.0, 1.0), max(min(1.0, water * 1.6) * u_puddle_flatten, wf * up * 0.45)));
  roughness = mix(roughness, 0.04, water * water);
  float rain = clamp(u_rain, 0.0, 1.0) * up * wet * (1.0 - smoothstep(u_ripple_fade_m, 2.0 * u_ripple_fade_m, viewDist));
  if (rain > 0.001) {
    vec2 uv = worldPos.xz * u_ripple_tiles_m;
    float t = u_time * u_ripple_speed;
    vec2 p = ig_ripple(uv, t, rain) + ig_ripple(uv * 0.7071 + vec2(0.37, 0.61), t * 0.83 + 0.5, rain * 0.8)
           + ig_ripple(uv * 1.3247 + vec2(0.71, 0.13), t * 1.137 + 0.25, rain * 0.6);   // 3 layers, irrational scales: no grid
    float k = u_ripple_strength * mix(0.25, 1.0, water);                        // rings read on puddles, faintly on wet ground
    nTS = normalize(nTS + vec3(p * k, 0.0));
    float crest = min(1.0, length(p) * k * 2.0) * water;
    roughness = max(roughness, 0.3 * crest);                                    // a ring crest scatters: breaks the mirror
    albedo *= 1.0 + 0.5 * crest;                                                // and lifts the water a little where the mirror shows nothing
  }
}
