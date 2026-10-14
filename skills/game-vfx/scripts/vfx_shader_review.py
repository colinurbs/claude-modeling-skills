"""
vfx_shader_review.py -- review for class `shader` effects (game-vfx round 5: vfx_screen_static, vfx_crt_roll, vfx_wet).

The reference snippet (references/shaders/<x>.glsl) is compiled and rendered as-is in headless WebGL (tools/review/
shoot_shader.mjs, one small page, SwiftShader), so the review proves the snippet compiles and shows exactly what it does.
Python then composes the sheet, the in-street context (day/night) and the Governor's CCTV view, and measures:
clipped (near-white) and crushed (near-black) share (S3: no pure white or black), and the full-field luma step between
consecutive 60 Hz frames at the worst setting (photosensitivity, spec classes.shader.max_full_field_luma_step).
Called by vfx_cli.py review when the effect's class is `shader`.
"""
import json, os, subprocess, sys
import numpy as np
import cv2
import vfxlib as vl

MAIN_SCREEN = """
uniform sampler2D u_picture; uniform float u_aspect; uniform float u_crt_on;
void main() {
  vec2 uv = vUV; vec2 cuv = u_crt_on > 0.5 ? ig_crt_uv(uv) : uv;
  vec3 pic = texture2D(u_picture, ig_static_uv(cuv)).rgb;
  vec3 c = ig_screen_static(pic, cuv, u_aspect);
  if (u_crt_on > 0.5) c = ig_crt_roll(c, uv, cuv);
  gl_FragColor = vec4(clamp(c, 0.0, 1.0), 1.0);
}"""

MAIN_WET = """
uniform sampler2D u_bc; uniform sampler2D u_orm; uniform sampler2D u_nrm; uniform float u_aspect; uniform float u_night;
uniform float u_zoom; uniform float u_pitch;
vec3 lin(vec3 c) { return pow(c, vec3(2.2)); }
void main() {
  vec2 ndc = vUV * 2.0 - 1.0; float th = 0.40 / u_zoom;
  vec3 d = normalize(vec3(ndc.x * u_aspect * th, ndc.y * th, -1.0));
  float pc = cos(-u_pitch), ps = sin(-u_pitch); d = vec3(d.x, d.y * pc - d.z * ps, d.y * ps + d.z * pc);
  vec3 ro = vec3(0.0, 1.7, 0.0);
  vec3 haze = u_night > 0.5 ? lin(vec3(0.125, 0.141, 0.149)) : lin(vec3(0.557, 0.580, 0.588));
  vec3 over = u_night > 0.5 ? lin(vec3(0.09, 0.10, 0.11)) : lin(vec3(0.725, 0.741, 0.745));
  if (d.y > -0.002) { gl_FragColor = vec4(pow(haze, vec3(1.0 / 2.2)), 1.0); return; }
  float t = -ro.y / d.y; vec3 wp = ro + d * t;
  vec2 tuv = wp.xz / 2.0;
  vec3 albedo = lin(texture2D(u_bc, tuv).rgb); float rough = texture2D(u_orm, tuv).g;
  vec3 nTS = texture2D(u_nrm, tuv).rgb * 2.0 - 1.0;
  vec2 pc2 = (wp.xz - vec2(0.2, -4.0)) / vec2(1.5, 0.9);
  float edge = texture2D(u_bc, tuv * 3.1).r - 0.5;
  float puddle = smoothstep(1.0, 0.5, length(pc2) + edge * 0.35);   // feathered, like dec_puddle_a's alpha
  float porosity = 0.9;
  ig_wet_surface(albedo, rough, nTS, wp, 1.0, porosity, puddle, t);
  vec3 N = normalize(vec3(nTS.x, nTS.z, -nTS.y)); vec3 V = -d;
  vec3 R = reflect(d, N);
  float F = 0.02 + 0.98 * pow(1.0 - max(dot(N, V), 0.0), 5.0);
  float gloss = pow(1.0 - rough, 2.0);
  vec3 env = over;   // overcast: the sky near the horizon is about as bright as the zenith
  vec3 col;
  if (u_night < 0.5) {
    vec3 L = normalize(vec3(0.3, 0.8, 0.4));
    col = albedo * (0.75 * (0.5 + 0.5 * N.y) * over * 1.3 + 0.35 * max(dot(N, L), 0.0));
  } else {
    vec3 lp = vec3(0.6, 4.5, -14.0); vec3 Lv = lp - wp; float dl = length(Lv); vec3 L = Lv / dl;
    vec3 lc = lin(vec3(0.812, 0.890, 0.863)) * 6.0 / (1.0 + dl * dl * 0.35);
    vec3 H = normalize(L + V); float sp = min(2.0 / max(1e-3, pow(rough, 4.0)) - 2.0, 600.0);
    col = albedo * (0.1 * over + lc * max(dot(N, L), 0.0)) + lc * F * 1.5 * pow(max(dot(N, H), 0.0), sp) * (sp + 8.0) / 25.0;
  }
  col = mix(col, env, clamp(F * gloss, 0.0, 1.0));
  col = mix(haze, col, exp(-0.012 * t));
  gl_FragColor = vec4(pow(clamp(col, 0.0, 1.0), vec3(1.0 / 2.2)), 1.0);
}"""


# ---- opt-in lit street scene for surface shaders (brief review.scene; tools/review/shader_wet_scene.glsl) ----
# day = overcast (engine.ts hemi/sun), dusk = civic tungsten lamps + a few lit windows, curfew = state fluorescent only.
SCENE_LIGHT = {
    "day": {"u_sky_hi": "#B9BDBE", "u_sky_lo": "#8E9496", "u_amb": 1.0, "u_sun": 0.35, "u_lamp_col": "#E8C88A", "u_lamp_k": 0.0, "u_head_k": 0.0, "u_win_k": 0.0},
    "dusk": {"u_sky_hi": "#596168", "u_sky_lo": "#77797A", "u_amb": 0.3, "u_sun": 0.0, "u_lamp_col": "#E8C88A", "u_lamp_k": 40.0, "u_head_k": 0.8, "u_win_k": 0.5},
    "curfew": {"u_sky_hi": "#20272C", "u_sky_lo": "#2C3134", "u_amb": 0.08, "u_sun": 0.0, "u_lamp_col": "#CFE3DC", "u_lamp_k": 45.0, "u_head_k": 0.8, "u_win_k": 0.0},
}
SCENE_CAMS = {   # eye 1.7 m down the street; puddle close-up (crouched, placed so the far lamp mirrors in the road puddle); Governor mast 3.5 m at ~9 m (spec review.cctv)
    "eye": {"u_cam": [0.8, 1.7, 3.0], "u_tgt": [-0.4, 0.2, -20.0], "u_fov": 0.577, "w": 480, "h": 300},
    "close": {"u_cam": [0.73, 1.2, 0.25], "u_tgt": [0.0, 0.0, -5.6], "u_fov": 0.45, "w": 640, "h": 480},
    "cctv": {"u_cam": [6.0, 3.5, 0.5], "u_tgt": [-1.5, 0.0, -8.5], "u_fov": 0.42, "w": 320, "h": 240},
}


def _scene_setup(P, rv, sc, src, textures, samplers, shots, mains):
    for slot, m in (sc.get("materials") or {"a": "t_ground_asphalt", "c": "t_ground_setts", "s": "t_ground_paving"}).items():
        for k in ("bc", "orm", "n"):
            textures["sc_%s_%s" % (slot, k)] = src("src/tex/%s_%s.png" % (m, k)); samplers["u_%s_%s" % (slot, k)] = "sc_%s_%s" % (slot, k)
    dm = json.load(open(src("materials/decals.json")))[sc.get("decal_material", "m_decal_ground")]
    x, y, w, h = dm["decals"][sc.get("puddle_decal", "dec_puddle_a")]["region_px"]
    crop = cv2.imread(src("src/tex/" + dm["base"]), cv2.IMREAD_UNCHANGED)[y:y + h, x:x + w]
    pp = os.path.join(P["work"], "scene_puddle_decal.png"); cv2.imwrite(pp, crop)
    textures["sc_pud"] = pp; samplers["u_pud"] = "sc_pud"
    def U(light, cam, w_, r_):
        d = {k: (list(vl.hexrgb(v)) if isinstance(v, str) else v) for k, v in SCENE_LIGHT[light].items()}
        d.update({k: v for k, v in SCENE_CAMS[cam].items() if k.startswith("u_")}); d.update({"u_wetness": w_, "u_rain": r_})
        return d
    def add(name, light, cam, w_, r_, t=2.0):
        c = SCENE_CAMS[cam]; shots.append({"name": name, "mode": "scene", "w": c["w"], "h": c["h"], "time": t, "uniforms": U(light, cam, w_, r_)})
    for light in ("day", "dusk", "curfew"):
        for w_, r_ in rv["states"]:
            add("sc_%s_eye_wet%s_rain%s" % (light, w_, r_), light, "eye", w_, r_)
        add("sc_%s_close_still" % light, light, "close", 1.0, 0.0)
        add("sc_%s_cctv" % light, light, "cctv", 1.0, 1.0)
    for i, t in enumerate(sc.get("times", rv["times"])):
        add("sc_rain_%02d" % i, sc.get("time_light", "curfew"), "close", 1.0, 1.0, t)
    for light in ("day", "dusk", "curfew"):
        add("sc_%s_cctv_dry" % light, light, "cctv", 0.0, 0.0)
    mains["scene"] = open(os.path.join(vl.SKILL, "tools", "review", "shader_wet_scene.glsl")).read()


def _scene_rows(rv, sc, S):
    rows, ims = [], []
    for light in ("day", "dusk", "curfew"):
        st = [S("sc_%s_eye_wet%s_rain%s" % (light, w_, r_)) for w_, r_ in rv["states"]]
        ims += st
        rows.append(_row([_label(i, "%s eye 1.7 m: wet %s rain %s" % (light, w_, r_)) for i, (w_, r_) in zip(st, rv["states"])], 230))
    cl = [S("sc_%s_close_still" % l) for l in ("day", "dusk", "curfew")]
    tv = [S("sc_rain_%02d" % i) for i in range(len(sc.get("times", rv["times"])))]
    ims += cl + tv
    sm = lambda i: cv2.resize(i, (320, 240), interpolation=cv2.INTER_AREA)   # label at sheet scale (full-res PNGs keep the rings)
    rows.append(_row([_label(sm(i), "puddle close-up, still, %s" % l) for i, l in zip(cl, ("day", "dusk", "curfew"))], 230))
    rows.append(_row([_label(sm(im), "%s rain 1 t %.2fs" % (sc.get("time_light", "curfew"), t)) for im, t in zip(tv, sc.get("times", rv["times"]))], 200))
    cc, cl_ = [], []
    for l in ("day", "dusk", "curfew"):
        cc += [S("sc_%s_cctv_dry" % l), S("sc_%s_cctv" % l)]; cl_ += [l + " dry", l + " soaked+rain"]
    cv = [_label(cv2.resize(_cctv(i), (480, 360), interpolation=cv2.INTER_NEAREST), "CCTV mast 3.5 m ~9 m: " + l) for i, l in zip(cc, cl_)]
    rows.append(_row(cv[:3], 240)); rows.append(_row(cv[3:], 240))
    return rows, ims + cc


def _scene_ground_delta(rv, S):
    """near-ground luminance per light and wet state (lower 38 % of the eye view, full width): p25 = the ground away from
    lamps (should fall as it soaks: darker), p90 = the lamp sheen (may rise: slicker, but stay far from clipping)."""
    out = {}
    for light in ("day", "dusk", "curfew"):
        pc = []
        for w_, r_ in rv["states"]:
            im = S("sc_%s_eye_wet%s_rain%s" % (light, w_, r_)).astype(np.float32); h = im.shape[0]
            L = im[int(h * 0.62):] @ np.array([0.2126, 0.7152, 0.0722], np.float32)
            pc.append([round(float(np.percentile(L, q)), 1) for q in (25, 50, 90)])
        out[light] = {"states": rv["states"], "luma_p25_p50_p90": pc, "p25_wet_vs_dry": [round(x[0] / max(pc[0][0], 1e-3), 3) for x in pc]}
    return out


def _u(eff):
    """effect uniforms -> {u_name: value}; colours as 0..1 triples."""
    out = {}
    for k, u in ((eff.get("shader") or {}).get("uniforms") or {}).items():
        if k == "time": continue
        v = u.get("default")
        out["u_" + k] = list(vl.hexrgb(v)) if u.get("type") == "color" else v
    return out


def _preset(eff, name):
    U = (eff.get("shader") or {}).get("uniforms") or {}
    return {"u_" + k: (list(vl.hexrgb(v)) if U.get(k, {}).get("type") == "color" else v) for k, v in ((eff["shader"].get("presets") or {}).get(name) or {}).items()}


def _read(p):
    im = cv2.imread(p, cv2.IMREAD_COLOR); return cv2.cvtColor(im, cv2.COLOR_BGR2RGB)


def _label(im, text):
    im = im.copy(); cv2.putText(im, text, (6, 16), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (230, 226, 214), 1, cv2.LINE_AA); return im


def _row(ims, h=240):
    out = [cv2.resize(i, (int(i.shape[1] * h / i.shape[0]), h), interpolation=cv2.INTER_AREA if i.shape[0] > h else cv2.INTER_NEAREST) for i in ims]
    return np.concatenate(out, 1)


def _stack(rows, W=1280):
    rr = []
    for r in rows:
        if r.shape[1] > W: r = cv2.resize(r, (W, int(r.shape[0] * W / r.shape[1])), interpolation=cv2.INTER_AREA)
        rr.append(np.pad(r, ((0, 6), (0, W - r.shape[1]), (0, 0)), constant_values=27))
    return np.concatenate(rr, 0)


def _cctv(img, res=(320, 240)):
    """the Governor's feed look (engine feed.ts, approximated): low res, greyscale with a green cast, scanlines."""
    g = cv2.resize(img, res, interpolation=cv2.INTER_AREA).astype(np.float32) / 255
    lum = g @ np.array([0.2126, 0.7152, 0.0722], np.float32)
    o = np.stack([lum * 0.82 + 0.02, lum * 0.98 + 0.05, lum * 0.88 + 0.03], -1)
    o *= (0.85 + 0.15 * np.sin(np.arange(res[1]) * np.pi))[:, None, None]
    return (np.clip(o, 0, 1) * 255).astype(np.uint8)


def _measure(img):
    f = img.astype(np.int32)
    return {"clipped_pct": round(100 * float((f.min(-1) >= 250).mean()), 3), "crushed_pct": round(100 * float((f.max(-1) <= 5).mean()), 3)}


def review(P, brief, eff, exclusive):
    spec = P["spec"]; root = P["root"]; rv = brief.get("review", {}); sh = eff["shader"]; typ = sh["type"]
    C = spec["vfx"]["classes"]["shader"]
    out = P["review"]; os.makedirs(out, exist_ok=True); os.makedirs(P["work"], exist_ok=True)
    tex_dir = P["tex_dir"]; skill = vl.SKILL
    # uniforms of this effect plus the sibling effects it chains with (review.with): the snippet needs all of them
    effs = [eff] + [json.load(open(os.path.join(P["export_dir"], w + ".json"))) for w in rv.get("with", [])]
    U, samplers, textures, snippets = {}, {}, {}, {}
    for e in effs:
        U.update(_u(e))
        for slot, t in (e["shader"].get("textures") or {}).items():
            samplers["u_" + slot] = t; textures[t] = os.path.join(tex_dir, t + ".png")
        snippets[e["shader"]["type"]] = open(os.path.join(skill, e["shader"]["reference"])).read()
    def merged_preset(name):
        d = {}
        for e in effs: d.update(_preset(e, name))
        return d
    shots, reuse = [], []
    src = lambda p: os.path.join(root, p)
    if typ == "screen":
        pic = src(rv["picture"]); drive = rv["drive"]          # drive = the uniform this effect owns
        fsrc = cv2.imread(src(rv["feed_picture"]), cv2.IMREAD_COLOR)   # the feed's CONTENT is 320x240 (spec review.cctv.res)
        feed_pic = os.path.join(P["work"], "feed_320x240.png"); cv2.imwrite(feed_pic, cv2.resize(fsrc, tuple(spec["vfx"]["review"]["cctv"]["res"]), interpolation=cv2.INTER_AREA))
        pp = dict(merged_preset("state_propaganda_screen_a"), u_crt_on=1.0); fp = dict(merged_preset("governor_feed"), u_crt_on=1.0)
        for v in rv["drive_values"]:
            shots.append({"name": "screen_%s_%s" % (drive, v), "mode": "screen", "w": 512, "h": 384, "time": 1.3, "picture": pic, "uniforms": dict(pp, **{"u_" + drive: v})})
        on_feed = rv.get("time_on") == "feed"
        for i, t in enumerate(rv["times"]):
            shots.append({"name": "time_%02d" % i, "mode": "screen", "w": 512, "h": 384, "time": t, "picture": feed_pic if on_feed else pic, "uniforms": dict(fp if on_feed else pp, **{"u_" + drive: rv.get("time_value", 1.0)})})
        for v in rv["feed_values"]:   # rendered at the feed panel's display size (2x): the 240 scanlines land on 2 px each, as in the control room
            shots.append({"name": "feed_%s" % v, "mode": "screen", "w": 640, "h": 480, "time": 2.1, "picture": feed_pic, "uniforms": dict(fp, **{"u_" + drive: v})})
        for i in range(24):   # worst-case flicker series, small
            shots.append({"name": "flk_%02d" % i, "mode": "screen", "w": 128, "h": 96, "time": 5.0 + i / 60.0, "picture": pic, "uniforms": dict(pp, **{"u_" + drive: rv.get("flicker_value", 1.0)})})
        for pn in (sh.get("presets") or {}):
            if pn in ("state_propaganda_screen_a", "governor_feed"): continue
            shots.append({"name": "preset_%s" % pn, "mode": "screen", "w": 512, "h": 384, "time": 1.3, "picture": pic, "uniforms": dict(merged_preset(pn), u_crt_on=1.0)})
        mains = {"screen": MAIN_SCREEN}
    else:
        mat = rv["ground"]
        for k in ("bc", "orm", "n"):
            textures["g_" + k] = src("src/tex/%s_%s.png" % (mat, k))
        samplers.update({"u_bc": "g_bc", "u_orm": "g_orm", "u_nrm": "g_n"})
        for night in (0, 1):
            for w, r in rv["states"]:
                shots.append({"name": "%s_wet%s_rain%s" % ("night" if night else "day", w, r), "mode": "surface", "w": 480, "h": 300, "time": 2.0,
                              "uniforms": {"u_night": float(night), "u_wetness": w, "u_rain": r, "u_zoom": 1.0, "u_pitch": 0.42}})
        for i, t in enumerate(rv["times"]):
            shots.append({"name": "time_%02d" % i, "mode": "surface", "w": 320, "h": 240, "time": t, "uniforms": {"u_night": float(rv.get("time_night", 0)), "u_wetness": 1.0, "u_rain": rv.get("time_rain", 0.5), "u_zoom": 3.2, "u_pitch": 0.405}})
        mains = {"surface": MAIN_WET}
        if rv.get("scene"):   # opt-in: lit street with environment reflection on the real ground kit (see SCENE_LIGHT)
            snippets["scene"] = snippets["surface"]
            _scene_setup(P, rv, rv["scene"], src, textures, samplers, shots, mains)
    job = {"out": out, "textures": textures, "samplers": samplers, "uniforms": U, "snippets": snippets, "mains": mains, "shots": shots, "reuse": reuse}
    jp = os.path.join(P["work"], "shader_job.json"); json.dump(job, open(jp, "w"), indent=1)
    with exclusive():
        r = subprocess.run(["node", os.path.join(skill, "tools", "review", "shoot_shader.mjs"), "--job", jp], capture_output=True, text=True, timeout=600)
    print(r.stdout[-800:]); errs = []
    if r.returncode != 0: print(r.stderr[-1500:]); errs.append((r.stdout + r.stderr)[-600:])
    S = lambda n: _read(os.path.join(out, n + ".png"))
    rj = {"id": eff["id"], "class": "shader", "errors": errs, "harness": "reference GLSL snippet compiled and rendered in headless WebGL (SwiftShader); context and CCTV composited in Python", "measure": {}, "gates": []}
    rows = []
    if not errs and typ == "screen":
        drive = rv["drive"]
        dv = [S("screen_%s_%s" % (drive, v)) for v in rv["drive_values"]]
        rows.append(_row([_label(im, "%s %s" % (drive, v)) for im, v in zip(dv, rv["drive_values"])], 200))
        tv = [S("time_%02d" % i) for i in range(len(rv["times"]))]
        rows.append(_row([_label(im, "t %.2fs" % t) for im, t in zip(tv, rv["times"])], 200))
        fv = [S("feed_%s" % v) for v in rv["feed_values"]]
        rows.append(_row([_label(im, "governor feed %s %s" % (drive, v)) for im, v in zip(fv, rv["feed_values"])], 260))
        pr = [S("preset_%s" % pn) for pn in sh.get("presets", {}) if pn not in ("state_propaganda_screen_a", "governor_feed")]
        # context: the screen 9 m down a street (day / night), and that street on the Governor's CCTV
        bg = _read(src(rv["context_bg"])); bg = cv2.resize(bg, (640, 360), interpolation=cv2.INTER_AREA)
        ctx = []
        for night in (0, 1):
            b = (bg.astype(np.float32) * (0.22 if night else 1.0)).astype(np.uint8)
            scr = cv2.resize(dv[min(len(dv) - 1, rv.get("context_index", 2))], tuple(rv.get("context_px", [92, 69])), interpolation=cv2.INTER_AREA)
            x, y = rv.get("context_xy", [300, 120]); b[y:y + scr.shape[0], x:x + scr.shape[1]] = scr
            ctx.append(_label(b, "in street ~9 m, %s" % ("night" if night else "day")))
        cctv = _label(cv2.resize(_cctv(ctx[0]), (640, 480), interpolation=cv2.INTER_NEAREST), "CCTV 320x240 (day)")
        rows.append(_row(ctx + [cctv] + pr, 240))
        allim = dv + tv + fv + pr
        m = [_measure(i) for i in allim]
        flk = np.array([S("flk_%02d" % i).astype(np.float32).mean() / 255 for i in range(24)])
        step = float(np.abs(np.diff(flk)).max())
        rj["measure"] = {"clipped_pct_max": max(x["clipped_pct"] for x in m), "crushed_pct_max": max(x["crushed_pct"] for x in m),
                         "flicker_luma_step_max": round(step, 4), "flicker_series_mean": [round(float(x), 3) for x in flk]}
    elif not errs:
        st = [S("%s_wet%s_rain%s" % (dn, w, r)) for dn in ("day", "night") for w, r in rv["states"]]
        lab = ["%s wet %s rain %s" % (dn, w, r) for dn in ("day", "night") for w, r in rv["states"]]
        n = len(rv["states"])
        rows.append(_row([_label(i, l) for i, l in zip(st[:n], lab[:n])], 230))
        rows.append(_row([_label(i, l) for i, l in zip(st[n:], lab[n:])], 230))
        tv = [S("time_%02d" % i) for i in range(len(rv["times"]))]
        rows.append(_row([_label(im, "puddle close-up t %.2fs" % t) for im, t in zip(tv, rv["times"])], 220))
        rows.append(_row([_label(cv2.resize(_cctv(st[i]), (480, 360), interpolation=cv2.INTER_NEAREST), "CCTV " + lab[i]) for i in (0, n - 1, 2 * n - 1)], 260))
        m = [_measure(i) for i in st + tv]
        if rv.get("scene"):
            srows, sims = _scene_rows(rv, rv["scene"], S)
            rows = srows + (rows if rv["scene"].get("keep_plane_rows") else [])
            m += [_measure(i) for i in sims]
            rj["scene_ground"] = _scene_ground_delta(rv, S)
            rj["harness"] += "; review.scene: lit street ray-cast (tools/review/shader_wet_scene.glsl) with sky/facade/pole reflections, sphere-light lamp lobes (capped at the head's radiance x F), engine fog and grade"
        rj["measure"] = {"clipped_pct_max": max(x["clipped_pct"] for x in m), "crushed_pct_max": max(x["crushed_pct"] for x in m)}
    gmax = spec["vfx"]["grade_check"].get("max_clipped_fraction", 0.02) * 100
    if errs: rj["gates"].append("snippet failed to compile/render")
    if rj["measure"].get("clipped_pct_max", 0) > gmax: rj["gates"].append("clipped %.2f%% > %.2f%%" % (rj["measure"]["clipped_pct_max"], gmax))
    if rj["measure"].get("crushed_pct_max", 0) > gmax: rj["gates"].append("crushed (near-black) %.2f%% > %.2f%% (S3)" % (rj["measure"]["crushed_pct_max"], gmax))
    if rj["measure"].get("flicker_luma_step_max", 0) > C["max_full_field_luma_step"]: rj["gates"].append("full-field flicker step %.3f > %.2f" % (rj["measure"]["flicker_luma_step_max"], C["max_full_field_luma_step"]))
    json.dump(rj, open(os.path.join(out, "review.json"), "w"), indent=1)
    if rows:
        sheet = _stack(rows); cv2.imwrite(P["sheet_png"], cv2.cvtColor(sheet, cv2.COLOR_RGB2BGR))
    print("REVIEW %s %s; measure %s; gates %s -> %s" % (eff["id"], "ok" if not errs else "ERRORS", {k: v for k, v in rj["measure"].items() if k != "flicker_series_mean"}, rj["gates"] or "none", os.path.relpath(P["sheet_png"], root)))
    return not errs and not rj["gates"]
