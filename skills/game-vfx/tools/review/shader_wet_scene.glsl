// shader_wet_scene.glsl -- review `main` for class `shader` type `surface` (vfx_wet) when the brief opts in with
// review.scene: a small lit street ray-cast in the fragment so the wet snippet is judged WITH an environment reflection.
// Appended after references/shaders/ig_wet.glsl by vfx_shader_review.py (the snippet's ig_wet_surface does the wet work).
//   ground: sidewalk (paving, +0.15 m kerb) | carriageway (asphalt) | cobbled lane (setts) -- the real ground-kit sets,
//   2 m tiles, uv = world.xz / 2 like env_ground_*; dec_puddle_a (its atlas crop, alpha) twice along the kerb, plus
//   feathered low-spot puddles; facades both sides with a window grid; three 6 m street lamps (two left sidewalk, one right).
//   light: hemi + weak sun by day (engine.ts values), lamps as small sphere lights (GGX, Karis widening, capped at the
//   head's own LDR radiance x F: a reflection is never brighter than the lamp), a procedural overcast sky with cloud
//   structure. Reflections: the reflected ray is traced against sky, facades and poles (12 cone samples by roughness, fixed rotation: no per-pixel jitter).
//   fog exp2 0.012 (engine.ts), grade = post.ts igGrade (desaturate 0.12, lift 0.025, pow 1.02); no tonemap, no bloom.
// Harness simplifications: no shadows, ground is shaded once with the `ground` preset (standing water reaches the
// `puddle` preset's 0.04 roughness via the mask), decal blended into albedo before the wet modifier.
uniform sampler2D u_a_bc; uniform sampler2D u_a_orm; uniform sampler2D u_a_n;
uniform sampler2D u_c_bc; uniform sampler2D u_c_orm; uniform sampler2D u_c_n;
uniform sampler2D u_s_bc; uniform sampler2D u_s_orm; uniform sampler2D u_s_n;
uniform sampler2D u_pud;
uniform float u_aspect; uniform vec3 u_cam; uniform vec3 u_tgt; uniform float u_fov;
uniform vec3 u_sky_hi; uniform vec3 u_sky_lo; uniform float u_amb; uniform float u_sun;
uniform vec3 u_lamp_col; uniform float u_lamp_k; uniform float u_head_k; uniform float u_win_k;

#define PI 3.14159265
const float KERB_X = -3.5; const float KERB_H = 0.15; const float FAC_L = -6.0; const float FAC_R = 9.0;
const float H_L = 13.0; const float H_R = 10.0; const float COB_X = 3.5;
const vec3 SUN = vec3(0.331, 0.883, 0.331);

vec3 lin(vec3 c) { return pow(c, vec3(2.2)); }
float h21(vec2 p) { p = fract(p * vec2(123.34, 456.21)); p += dot(p, p + 45.32); return fract(p.x * p.y); }
float vnoise(vec2 p) { vec2 i = floor(p), f = fract(p); f = f * f * (3.0 - 2.0 * f);
  return mix(mix(h21(i), h21(i + vec2(1.0, 0.0)), f.x), mix(h21(i + vec2(0.0, 1.0)), h21(i + vec2(1.0, 1.0)), f.x), f.y); }
float fbm(vec2 p) { float s = 0.0, a = 0.5; for (int i = 0; i < 4; i++) { s += a * vnoise(p); p = p * 2.03 + 1.7; a *= 0.5; } return s; }
// lamps 0/1 on the left sidewalk, lamp 2 on the right (cobbled lane) placed so it mirrors in a road puddle from eye and close-up
vec3 lampPos(int i) { return i == 0 ? vec3(-2.4, 5.74, -10.0) : i == 1 ? vec3(-2.4, 5.74, -32.0) : vec3(7.9, 5.74, -20.0); }
float poleX(int i) { return i == 2 ? 8.5 : -3.0; }

vec3 sky(vec3 d) {
  float e = clamp(d.y, 0.0, 1.0);
  vec3 c = mix(lin(u_sky_lo), lin(u_sky_hi), pow(e, 0.45));
  float cl = fbm(d.xz / max(d.y, 0.08) * 0.45);
  return c * (0.86 + 0.28 * cl * smoothstep(0.0, 0.2, e));       // soft overcast cloud structure (reads in mirrors)
}
vec3 fogc() { return lin(u_sky_lo); }
float fogf(float t) { float k = 0.012 * t; return exp(-k * k); }

float ibox(vec3 ro, vec3 rd, vec3 bmin, vec3 bmax, out vec3 n) {
  vec3 inv = 1.0 / rd; vec3 t0 = (bmin - ro) * inv, t1 = (bmax - ro) * inv;
  vec3 tmn = min(t0, t1), tmx = max(t0, t1);
  float tn = max(max(tmn.x, tmn.y), tmn.z), tf = min(min(tmx.x, tmx.y), tmx.z);
  n = vec3(0.0, 1.0, 0.0);
  if (tn > tf || tn < 0.0) return 1e9;
  n = -sign(rd) * step(tmn.yzx, tmn.xyz) * step(tmn.zxy, tmn.xyz);
  return tn;
}

// id: 0 sky, 1 asphalt, 2 cobble, 3 sidewalk, 4 kerb face, 5/6 facade L/R, 7 pole/arm, 8 lamp head (emissive below)
float isect(vec3 ro, vec3 rd, bool ground, out float id, out vec3 n) {
  rd = mix(rd, vec3(1e-5), step(abs(rd), vec3(1e-5)));
  float tb = 1e9; id = 0.0; n = vec3(0.0, 1.0, 0.0);
  if (ground && rd.y < 0.0) {
    float t = -ro.y / rd.y; vec3 p = ro + rd * t;
    if (t > 0.0 && p.x >= KERB_X && p.x <= FAC_R) { tb = t; id = p.x > COB_X ? 2.0 : 1.0; }
    float t2 = (KERB_H - ro.y) / rd.y; vec3 p2 = ro + rd * t2;
    if (t2 > 0.0 && t2 < tb && p2.x < KERB_X && p2.x >= FAC_L) { tb = t2; id = 3.0; }
  }
  if (ground && rd.x < 0.0) { float t = (KERB_X - ro.x) / rd.x; vec3 p = ro + rd * t;
    if (t > 0.0 && t < tb && p.y >= 0.0 && p.y <= KERB_H) { tb = t; id = 4.0; n = vec3(1.0, 0.0, 0.0); } }
  if (rd.x < 0.0) { float t = (FAC_L - ro.x) / rd.x; vec3 p = ro + rd * t;
    if (t > 0.0 && t < tb && p.y >= 0.0 && p.y <= H_L && p.z > -140.0 && p.z < 40.0) { tb = t; id = 5.0; n = vec3(1.0, 0.0, 0.0); } }
  if (rd.x > 0.0) { float t = (FAC_R - ro.x) / rd.x; vec3 p = ro + rd * t;
    if (t > 0.0 && t < tb && p.y >= 0.0 && p.y <= H_R && p.z > -140.0 && p.z < 40.0) { tb = t; id = 6.0; n = vec3(-1.0, 0.0, 0.0); } }
  for (int i = 0; i < 3; i++) {
    vec3 L = lampPos(i); float px = poleX(i), sd = sign(L.x - px);
    vec2 oc = ro.xz - vec2(px, L.z); vec2 dd = rd.xz;
    float a = dot(dd, dd), b = dot(oc, dd), c = dot(oc, oc) - 0.0049, disc = b * b - a * c;
    if (disc > 0.0 && a > 1e-8) { float t = (-b - sqrt(disc)) / a; float y = ro.y + rd.y * t;
      if (t > 0.0 && t < tb && y > KERB_H && y < 5.95) { tb = t; id = 7.0; vec3 p = ro + rd * t; n = normalize(vec3(p.x - px, 0.0, p.z - L.z)); } }
    vec3 bn; float t = ibox(ro, rd, vec3(min(px, L.x) - 0.04, 5.86, L.z - 0.035), vec3(max(px, L.x) + 0.04, 5.93, L.z + 0.035), bn);
    if (t < tb) { tb = t; id = 7.0; n = bn; }
    t = ibox(ro, rd, vec3(L.x - 0.28, 5.76, L.z - 0.13), vec3(L.x + 0.28, 5.95, L.z + 0.13), bn);
    if (t < tb) { tb = t; id = 8.0; n = bn; }
  }
  return tb;
}

vec3 lampE(int i, vec3 p, bool spec, out vec3 L, out float d) {   // irradiance/PI from lamp i at p (downward shade, ~18 m
  vec3 Lv = lampPos(i) - p; d = length(Lv); L = Lv / d;               // reach for diffuse; a reflection of the lamp has no reach limit)
  float cone = smoothstep(-0.05, 0.35, L.y);
  float win = spec ? 1.0 : pow(clamp(1.0 - pow(d / 18.0, 4.0), 0.0, 1.0), 2.0);
  return lin(u_lamp_col) * u_lamp_k * cone * win / (d * d + 1.0);
}
vec3 diffuseLight(vec3 p, vec3 N) {
  vec3 c = u_amb * mix(lin(vec3(0.227, 0.231, 0.224)), lin(u_sky_hi), 0.5 + 0.5 * N.y) + u_sun * max(dot(N, SUN), 0.0);
  for (int i = 0; i < 3; i++) { vec3 L; float d; vec3 E = lampE(i, p, false, L, d); c += E * max(dot(N, L), 0.0); }
  return c;
}

vec3 shadeSolid(vec3 p, vec3 n, float id, bool heads) {   // facades, poles, heads (diffuse only)
  if (id > 7.5) {
    if (n.y < -0.5 && u_head_k > 0.0) return heads ? lin(u_lamp_col) * u_head_k : lin(vec3(0.2));
    return lin(vec3(0.16, 0.165, 0.16)) * diffuseLight(p, n);
  }
  if (id > 6.5) return lin(vec3(0.19, 0.2, 0.19)) * diffuseLight(p, n);
  float side = id > 5.5 ? 1.0 : 0.0;
  vec3 alb = lin(vec3(0.541, 0.545, 0.525)) * (0.8 + 0.35 * fbm(p.zy * vec2(0.6, 0.9) + side * 7.0));
  if (p.y < 0.6) alb *= 0.6;
  vec3 em = vec3(0.0);
  float fy = p.y - 3.6, wy = mod(fy, 3.2), wz = mod(p.z + side * 1.1, 3.0);
  if (fy > 0.0 && p.y < (side > 0.5 ? H_R : H_L) - 1.0 && wy > 0.5 && wy < 2.1 && wz > 0.9 && wz < 2.1) {
    alb = lin(vec3(0.17, 0.18, 0.18));
    float lit = step(h21(floor(vec2((p.z + side * 1.1) / 3.0, fy / 3.2)) + side * 3.7), 0.22);
    em = lin(vec3(0.91, 0.78, 0.54)) * u_win_k * lit * (0.6 + 0.4 * vnoise(p.zy * 3.0));
  }
  return alb * diffuseLight(p, n) + em;
}

vec3 traceRad(vec3 ro, vec3 rd) {   // reflected ray: sky, facades, poles; lamp heads dark (their reflection is the analytic lobe)
  float id; vec3 n; float t = isect(ro, rd, false, id, n);
  if (id < 0.5) return sky(rd);
  return mix(fogc(), shadeSolid(ro + rd * t, n, id, false), fogf(t));
}

vec3 envBRDF(float f0, float r, float NV) {   // Karis mobile split-sum approximation
  vec4 c0 = vec4(-1.0, -0.0275, -0.572, 0.022), c1 = vec4(1.0, 0.0425, 1.04, -0.04);
  vec4 q = r * c0 + c1; float a004 = min(q.x * q.x, exp2(-9.28 * NV)) * q.x + q.y;
  vec2 AB = vec2(-1.04, 1.04) * a004 + q.zw; return vec3(f0 * AB.x + AB.y);
}

vec3 shadeGround(vec3 ro, vec3 rd, float t, float id) {
  vec3 P = ro + rd * t; vec2 uv = id > 3.5 ? P.zy / 2.0 : P.xz / 2.0;
  vec3 albedo; vec4 orm; vec3 nTS;
  if (id < 1.5) { albedo = texture2D(u_a_bc, uv).rgb; orm = texture2D(u_a_orm, uv); nTS = texture2D(u_a_n, uv).rgb; }
  else if (id < 2.5) { albedo = texture2D(u_c_bc, uv).rgb; orm = texture2D(u_c_orm, uv); nTS = texture2D(u_c_n, uv).rgb; }
  else { albedo = texture2D(u_s_bc, uv).rgb; orm = texture2D(u_s_orm, uv); nTS = texture2D(u_s_n, uv).rgb; }
  albedo = lin(albedo); float rough = orm.g; float ao = mix(1.0, orm.r, 0.6); nTS = nTS * 2.0 - 1.0;
  float pud = 0.0;
  if (id < 1.5) {
    for (int k = 0; k < 2; k++) {   // dec_puddle_a along the kerb (+V of the decal against the kerb), its own alpha
      float zc = k == 0 ? -12.5 : -24.0;
      vec2 duv = vec2((P.z - (zc - 0.8)) / 1.6, 1.0 - (P.x - KERB_X) / 0.7);
      if (duv.x > 0.0 && duv.x < 1.0 && duv.y > 0.0 && duv.y < 1.0) {
        vec4 dc = texture2D(u_pud, duv); albedo = mix(albedo, lin(dc.rgb), dc.a);
        pud = max(pud, smoothstep(0.3, 0.58, dc.a));
      }
    }
  }
  if (id < 2.5) {   // low spots (engine: height/AO-derived mask), feathered with ground-texture noise
    float edge = texture2D(u_a_bc, uv * 3.1).r - 0.5 + (vnoise(P.xz * 2.2) - 0.5) * 0.6;
    float m1 = length((P.xz - vec2(0.0, -5.0)) / vec2(1.35, 0.9));
    float m2 = length((P.xz - vec2(1.9, -14.5)) / vec2(1.7, 1.0));
    float m3 = length((P.xz - vec2(5.6, -8.0)) / vec2(1.0, 0.75));
    float m4 = length((P.xz - vec2(2.2, -2.8)) / vec2(1.1, 0.85));   // on lamp 2's mirror line from eye and close-up
    float wob = (vnoise(P.xz * 3.0) - 0.5) * 0.3 + (vnoise(P.xz * 7.0) - 0.5) * 0.06;   // irregular low-spot outline
    pud = max(pud, smoothstep(1.2, 0.55, min(min(m1, m2), min(m3, m4)) + edge * 0.35 + wob));
  }
  float nY = id > 3.5 ? 0.0 : 1.0;
  ig_wet_surface(albedo, rough, nTS, P, nY, 0.9, pud, t);
  vec3 N = id > 3.5 ? vec3(1.0, 0.0, 0.0) : normalize(vec3(nTS.x, nTS.z, -nTS.y));
  vec3 V = -rd; float NV = max(dot(N, V), 1e-3); float f0 = mix(0.04, 0.02, pud);
  vec3 R = reflect(rd, N); if (R.y < 0.02) R.y = 0.02 + (0.02 - R.y) * 0.5;
  // environment: trace the reflected ray (cone-jittered by roughness), blend to the hemisphere average when rough
  vec3 skyAvg = mix(lin(u_sky_lo), lin(u_sky_hi), 0.55) * 0.8;
  vec3 env = skyAvg;
  if (rough < 0.75) {
    // stable cone (fixed rotation, 12 golden-angle samples): a per-pixel random rotation read as glitter on the damp film
    float a = rough * rough, rot = 0.0;
    vec3 T = normalize(cross(R, vec3(0.001, 1.0, 0.0))); vec3 B = cross(R, T); vec3 acc = vec3(0.0);
    for (int k = 0; k < 12; k++) { float fk = float(k); float ang = rot + fk * 2.39996; float rr = sqrt((fk + 0.5) / 12.0) * a * 1.3;
      vec3 d = normalize(R + (T * cos(ang) + B * sin(ang)) * rr); d.y = abs(d.y);
      acc += traceRad(P + N * 0.01, d); }
    env = mix(acc / 12.0, skyAvg, smoothstep(0.4, 0.75, rough));
  }
  vec3 spec = env * envBRDF(f0, rough, NV) * ao;
  // lamps: GGX sphere lights (r 0.12 m, Karis widening) at every roughness incl. mirror water; the lobe is capped at the
  // head's LDR radiance x F (x4, <= 0.8): a reflection is never brighter than the lamp itself
  for (int i = 0; i < 3; i++) {
    vec3 L; float d; vec3 E = lampE(i, P, true, L, d);   // mirror water sees every lamp; the damp film's sheen keeps the lamp's 18 m reach
    E *= mix(1.0, pow(clamp(1.0 - pow(d / 18.0, 4.0), 0.0, 1.0), 2.0), smoothstep(0.1, 0.3, rough));
    vec3 H = normalize(L + V); float NL = max(dot(N, L), 0.0), NH = max(dot(N, H), 0.0);
    float a = max(rough * rough, 0.002), a2 = clamp(a + 0.12 / (2.0 * d), 0.0, 1.0), nrm = (a / a2) * (a / a2);
    float den = NH * NH * (a2 * a2 - 1.0) + 1.0; float D = a2 * a2 / (PI * den * den);
    float Vis = 0.5 / (NL * (NV * (1.0 - a2) + a2) + NV * (NL * (1.0 - a2) + a2) + 1e-4);
    float F = f0 + (1.0 - f0) * pow(1.0 - max(dot(H, V), 0.0), 5.0);
    spec += min(D * Vis * F * nrm * NL * PI * E, lin(u_lamp_col) * u_head_k * min(0.8, F * 4.0));
  }
  vec3 col = albedo * diffuseLight(P, N) * ao + spec;
  return mix(fogc(), col, fogf(t));
}

void main() {
  vec2 ndc = vUV * 2.0 - 1.0;
  vec3 fw = normalize(u_tgt - u_cam), rt = normalize(cross(fw, vec3(0.0, 1.0, 0.0))), up = cross(rt, fw);
  vec3 rd = normalize(fw + ndc.x * u_aspect * u_fov * rt + ndc.y * u_fov * up);
  float id; vec3 n; float t = isect(u_cam, rd, true, id, n);
  vec3 col;
  if (id < 0.5) col = sky(rd);
  else if (id < 4.5) col = shadeGround(u_cam, rd, t, id);
  else col = mix(fogc(), shadeSolid(u_cam + rd * t, n, id, true), fogf(t));
  vec3 g = pow(clamp(col, 0.0, 1.0), vec3(1.0 / 2.2));
  float lum = dot(g, vec3(0.2126, 0.7152, 0.0722));
  g = mix(vec3(lum), g, 1.0 - 0.12);
  g = g * (1.0 - 0.025) + vec3(0.025 * 0.9, 0.025 * 0.92, 0.025 * 0.9);
  g = pow(g, vec3(1.02));
  gl_FragColor = vec4(g, 1.0);
}
