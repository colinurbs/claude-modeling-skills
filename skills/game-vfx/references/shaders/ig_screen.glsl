// ig_screen.glsl -- reference snippet for class `shader`, type `screen` (game-vfx round 5):
//   vfx_screen_static : ig_screen_static()  analog snow / weak-signal interference / no signal (sabotage)
//   vfx_crt_roll      : ig_crt_roll()       tube treatment: scanlines, vertical roll bar, hold wobble, barrel, phosphor tint,
//                                           vignette, black/white levels (no pure black or white, S3)
// Order on a screen: picture -> ig_screen_static -> ig_crt_roll (see ig_screen() at the end).
// Plain GLSL ES 1.00/3.00 functions: no #version, no main, no engine names. Uniform names = the effect data's `shader.uniforms`
// keys (prefix u_ here). Babylon: paste into a ShaderMaterial / PostProcess fragment (or a MaterialPluginBase emissive hook);
// Godot 4: paste into a .gdshader `shader_type spatial;` (emission) or `canvas_item;` (feeds), rename texture2D -> texture,
// declare samplers as `uniform sampler2D x : repeat_enable, filter_linear;` WITHOUT source_color: the tiles are raw data
// (Babylon: gammaSpace = false). u_time: wrap it on the CPU (e.g. mod 600 s) so mediump hashes stay stable.
// The review renders THIS file (tools/review/shoot_shader.mjs), so the snippet and the review cannot drift.

uniform float u_time;            // seconds
// ---- vfx_screen_static
uniform sampler2D u_noise_tex;   // t_vfx_screen_noise_e (tiling, greyscale)
uniform float u_static_amount;   // 0 clean .. ~0.2 weak signal .. 1 no signal (sabotaged)
uniform float u_static_level;    // brightness of the snow at full static (LDR, <= 0.85)
uniform float u_noise_fps;       // snow frames per second (a new random tile offset each frame)
uniform float u_noise_tiles;     // noise tiles per screen height (256 px tile: 1.0 ~ 256 lines)
uniform float u_tear;            // horizontal line tearing at partial static, in screen widths
uniform float u_flicker;         // full-field brightness step per snow frame (photosensitivity: <= 0.05 -> step < 0.1)
uniform float u_grain;           // constant feed grain (engine feed.ts 0.08 on feeds, 0 on broadcast screens)
// ---- vfx_crt_roll
uniform sampler2D u_scan_tex;    // t_vfx_screen_scanline_e (32 lines per tile)
uniform sampler2D u_roll_tex;    // t_vfx_screen_roll_e (bar at v 0.5, tiles in v)
uniform float u_lines;           // scanlines per screen height (feeds 240, propaganda screen 288)
uniform float u_scan_strength;   // 0..0.4 (engine feed.ts uses 0.15)
uniform float u_roll_speed;      // roll-bar travel in screen heights per second (feed.ts 0.05)
uniform float u_roll_strength;   // depth of the roll bar 0..0.5 (feed.ts 0.15)
uniform float u_wobble;          // vertical hold wobble amplitude in screen heights (feed.ts 0.004)
uniform float u_barrel;          // barrel distortion k (feed.ts 0.08)
uniform float u_greyscale;       // 0 keep the picture's colour .. 1 luminance only (feeds 1)
uniform vec3  u_phosphor_tint;   // multiplies the luminance (palette `fluorescent` = the state's light, S3)
uniform float u_vignette;        // 0..1 corner darkening
uniform float u_black_level;     // lift: no pure black (S3)
uniform float u_white_level;     // ceiling: no pure white (S3)

float ig_hash12(vec2 p) { vec3 p3 = fract(vec3(p.xyx) * 0.1031); p3 += dot(p3, p3.yzx + 33.33); return fract((p3.x + p3.y) * p3.z); }

// weak-signal line tearing: sample the PICTURE at ig_static_uv(cuv) so its lines slip sideways (bands of 4 lines)
vec2 ig_static_uv(vec2 uv) {
  float a = clamp(u_static_amount, 0.0, 1.0);
  float frame = floor(u_time * u_noise_fps * 0.5);
  float band = floor(uv.y * u_lines * 0.25);
  float tearRow = step(1.0 - 0.45 * a, ig_hash12(vec2(band, frame)));
  return uv + vec2(u_tear * a * tearRow * (ig_hash12(vec2(band, frame + 3.0)) - 0.5) * 2.0, 0.0);
}

// uv: 0..1 across the screen (u right, v up). aspect = width / height. pic = the broadcast or feed picture (LDR).
vec3 ig_screen_static(vec3 pic, vec2 uv, float aspect) {
  float a = clamp(u_static_amount, 0.0, 1.0);
  float frame = floor(u_time * u_noise_fps);
  vec2 jump = vec2(ig_hash12(vec2(frame, 1.7)), ig_hash12(vec2(frame, 9.2)));          // a new tile offset every snow frame
  vec2 nuv = vec2(uv.x * aspect, uv.y) * u_noise_tiles + jump;
  float snow = texture2D(u_noise_tex, nuv).r;
  float field = 1.0 + u_flicker * (ig_hash12(vec2(frame, 4.4)) - 0.5) * 2.0 * a;        // bounded full-field flicker
  float m = pow(a, 0.75);                                                                  // 0.1 -> 0.18, 0.25 -> 0.35 snow
  vec3 noisy = mix(pic, vec3(snow * u_static_level) * field, m);
  noisy += (snow - 0.5) * (0.22 * sqrt(a) * (1.0 - m) + u_grain);                          // grain over the weak picture + feed grain
  return max(noisy, vec3(0.0));
}

vec2 ig_crt_uv(vec2 uv) {
  vec2 c = uv - 0.5; float r2 = dot(c, c);
  uv = 0.5 + c * (1.0 + u_barrel * r2);
  uv.y += u_wobble * sin(u_time * 0.7 + uv.y * 6.0);
  return uv;
}

// call with the picture already sampled at ig_crt_uv(uv) (so the barrel and wobble move the picture). The scanlines and the
// roll bar are the tube's own raster: sampled at the barrel-only uv, never through the wobble (no line crawl).
vec3 ig_crt_roll(vec3 pic, vec2 uv, vec2 cuv) {
  vec2 c = uv - 0.5; vec2 buv = 0.5 + c * (1.0 + u_barrel * dot(c, c));
  float lum = dot(pic, vec3(0.2126, 0.7152, 0.0722));
  vec3 tint = u_phosphor_tint / max(max(u_phosphor_tint.r, u_phosphor_tint.g), u_phosphor_tint.b);   // a cast, not a dimmer
  vec3 col = mix(pic, vec3(lum), u_greyscale) * tint;
  float scan = texture2D(u_scan_tex, vec2(buv.x, buv.y * u_lines / 32.0)).r / 0.92;       // 32 lines per tile
  col *= mix(1.0, scan, u_scan_strength);
  float roll = texture2D(u_roll_tex, vec2(0.5, buv.y + u_time * u_roll_speed)).r / 0.92;   // full-width bar
  col *= mix(1.0, roll, u_roll_strength / 0.85);                                           // bar depth 0.85 in the tile
  col *= 1.0 - u_vignette * smoothstep(0.25, 0.75, length(c * vec2(1.0, 1.1)));
  vec2 e = smoothstep(vec2(0.0), vec2(0.004), buv) * smoothstep(vec2(0.0), vec2(0.004), 1.0 - buv);   // soft tube edge
  col = u_black_level + clamp(col, 0.0, 1.0) * (u_white_level - u_black_level) * e.x * e.y;
  return col;
}

// the full chain for a screen: sample your picture at cuv, then static, then the tube.
//   vec2 cuv = ig_crt_uv(uv); vec3 pic = texture2D(picture, ig_static_uv(cuv)).rgb;
//   gl_FragColor = vec4(ig_screen(pic, uv, cuv, aspect), 1.0);    // emissive on state_propaganda_screen_a
vec3 ig_screen(vec3 pic, vec2 uv, vec2 cuv, float aspect) {
  return ig_crt_roll(ig_screen_static(pic, cuv, aspect), uv, cuv);
}
