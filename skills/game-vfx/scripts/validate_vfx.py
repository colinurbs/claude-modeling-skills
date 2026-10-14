"""
validate_vfx.py -- checks an exported vfx_<id>.json and its texture sheets against assets/spec.json 'vfx'.

Levels as game-asset-modeling's validate_glb (PASS/WARN/FAIL/INFO via its Report class).
  schema.*    data shape, names, ranges, frames within the sheet
  tex.*       PNG, RGBA, power of two, size, cell size, border alpha, empty cells, RGB bleed, greyscale _e,
              palette of _bc pixels, memory per effect and in total
  budget.*    alive particles, fill (overdraw estimate), duration vs particle lifetimes, flash life
  loop.*      loop flag vs class, rate/burst consistency, fade-out
  palette.*   every gradient key: LDR, emissive hue/saturation, non-emissive saturation/value, blood, accent rules
  hooks.*     class-required hooks: attach event/socket, light limits, decal id in the decal table, surface, sfx, mesh
  lod.*       reduce/cull distances and factor
"""
import glob, json, os, re
import numpy as np
import vfxlib as vl
from validate_glb import Report  # noqa: E402  (game-asset-modeling)


def _png(path):
    import cv2
    im = cv2.imread(path, cv2.IMREAD_UNCHANGED)
    if im is None: return None
    if im.ndim == 2 or im.shape[2] == 3: return im
    return cv2.cvtColor(im, cv2.COLOR_BGRA2RGBA)


def check_colour_key(rep, where, hx, alpha, blend, em, effect, V, spec):
    h, s, v = vl.hsv(hx); col = V["colour"]; cls = effect["class"]
    tag = "%s %s" % (where, hx)
    if not (0 <= alpha <= 1): rep.fail("palette.alpha_range", False, "%s alpha %.2f outside 0..1" % (tag, alpha))
    blood = em.get("blood", False)
    if blood:
        b = col["blood"]; bh = vl.hsv(b["hex"])[0]
        ok = vl.hue_dist(h, bh) <= b["hue_tolerance_deg"] or s < 0.12
        rep.fail("palette.blood", ok and v <= b["max_value"] and s <= b["max_saturation"],
                 "%s: blood must be desaturated brown-red near %s (hue %.0f vs %.0f +-%d, value %.2f <= %.2f, sat %.2f <= %.2f; S3/S13)" % (tag, b["hex"], h, bh, b["hue_tolerance_deg"], v, b["max_value"], s, b["max_saturation"]))
    elif blend == "add" or (em.get("texture", "").endswith("_e") and not em.get("lit", False)):   # an unlit emissive-sheet sprite is light, whatever its blend (an alpha-blended fire body)
        e = col["emissive"]
        smax = e.get("fire_max_saturation", e["max_saturation"]) if cls in ("fire", "explosion") else e["max_saturation"]   # fire must out-colour tungsten windows after additive wash-out
        hue_ok = (e["hue_deg"][0] <= h <= e["hue_deg"][1]) or s < 0.08
        rep.fail("palette.emissive", hue_ok and s <= smax and v <= e["max_value"],
                 "%s: emissive keys stay tungsten/fire (hue %.0f in %s, sat %.2f <= %.2f; S6: fire is the only orange light, never neon)" % (tag, h, e["hue_deg"], s, smax))
    else:
        ne = col["non_emissive"]; smax = col["smoke_dust_max_saturation"] if em["texture"].startswith("t_vfx_smoke") else ne["max_saturation"]
        rep.fail("palette.non_emissive", s <= smax + 1e-6 and ne["min_value"] - 1e-6 <= v <= ne["max_value"] + 1e-6,
                 "%s: sat %.2f <= %.2f, value %.2f in [%.2f, %.2f] (S3: grey/brown, no pure black or white)" % (tag, s, smax, v, ne["min_value"], ne["max_value"]))
    fl = col["emissive"].get("fire_look") or {}
    if cls in fl.get("classes", []) and em.get("texture", "").endswith("_e") and not em.get("lit", False): return   # fire-look rule: an unlit flame/ember key is the light (S6), not a state surface
    for r in spec["palette"].get("accent_rules", []):
        rh, rs, rv = vl.hsv(r["hex"])
        if vl.hue_dist(h, rh) <= r["hue_tolerance_deg"] and s >= r.get("min_saturation", 0) and v >= r.get("min_value", 0) and (r["name"] != "chalk" or s <= 0.15):
            lvl = rep.warn if cls in ("fire", "explosion") else rep.fail
            lvl("palette.accent_%s" % r["name"], False, "%s reads as %s (%s); %s" % (tag, r["name"], r["hex"], r["why"]))


def check_tiling(rep, name, im, t):
    """class shader textures (round 5): seamless (wrap edge step no larger than the tile's own pixel steps), masks greyscale,
    data tiles (_n) carry data in all four channels (no alpha rules)."""
    f = im.astype(np.float32) / 255; ch = f if t.get("kind") == "data" else f[..., :3]
    inner = max(float(np.abs(np.diff(ch, axis=1)).mean()), float(np.abs(np.diff(ch, axis=0)).mean()), 1e-3)
    seam = max(float(np.abs(ch[:, 0] - ch[:, -1]).mean()), float(np.abs(ch[0] - ch[-1]).mean()))
    rep.fail("tex.tiling_seam", seam <= 2.0 * inner + 0.01, "%s: wrap seam step %.3f vs inner step %.3f (a tiling shader texture must wrap)" % (name, seam, inner))
    if t.get("kind") == "mask":
        spread = int((im[..., :3].max(-1).astype(int) - im[..., :3].min(-1).astype(int)).max())
        rep.fail("tex.emissive_grey", spread <= 2, "%s: shader masks are greyscale (tinted by uniforms); channel spread %d/255" % (name, spread))
        rep.fail("tex.mask_range", im[..., :3].max() <= 240, "%s: mask max %d/255 (<= 240: S3 no pure white even at full weight)" % (name, im[..., :3].max()))
    else:
        rep.info("tex.data", "%s: linear data tile (no sRGB decode), %s" % (name, t.get("slot")))


def check_shader(rep, e, C, spec, root):
    """class shader (round 5): type/targets, uniforms (ranges, palette for colours), presets inside the ranges, reference
    snippet present, photosensitivity guard on full-field flicker and roll (spec classes.shader)."""
    import vfxlib as _vl
    sh = e.get("shader") or {}; typ = sh.get("type"); U = sh.get("uniforms") or {}
    rep.fail("shader.type", typ in C["types"], "shader.type %s (%s)" % (typ, C["types"]))
    tg = sh.get("targets") or []
    rep.fail("shader.targets", bool(tg) and all(x in C["targets"].get(typ, []) for x in tg), "targets %s (allowed for %s: %s)" % (tg, typ, C["targets"].get(typ)))
    req = C["required_uniforms"].get(typ, [])
    rep.fail("shader.required_uniforms", all(k in U for k in req), "uniforms include %s" % req)
    for k, u in U.items():
        if u.get("type") == "color":
            hh, ss, vv = _vl.hsv(u["default"]); ne = spec["vfx"]["colour"]["non_emissive"]
            rep.fail("shader.colour", ss <= spec["palette"]["max_saturation"] + 1e-6 and vv <= 0.92, "uniform %s %s: sat %.2f <= %.2f, value %.2f <= 0.92 (S3)" % (k, u["default"], ss, spec["palette"]["max_saturation"], vv))
        elif k != "time":
            ok = all(isinstance(u.get(x), (int, float)) for x in ("default", "min", "max")) and u["min"] <= u["default"] <= u["max"]
            rep.fail("shader.uniform_range", ok, "uniform %s: min <= default <= max (%s)" % (k, {x: u.get(x) for x in ("min", "default", "max")}))
    for pn, pr in (sh.get("presets") or {}).items():
        bad = [k for k, v in pr.items() if k not in U or (isinstance(v, (int, float)) and not (U[k].get("min", -1e9) <= v <= U[k].get("max", 1e9)))]
        rep.fail("shader.preset", not bad and pn in tg, "preset %s (a target) uses known uniforms inside their ranges; bad: %s" % (pn, bad or "none"))
    ref = sh.get("reference") or ""
    rp = os.path.join(_vl.SKILL, ref)
    rep.fail("shader.reference", bool(ref) and os.path.exists(rp), "reference snippet %s" % ref)
    if ref and os.path.exists(rp):
        src = open(rp).read(); fn = sh.get("entry", "")
        rep.fail("shader.entry", bool(fn) and fn in src, "entry function %s in %s" % (fn, ref))
        rep.fail("shader.uniforms_in_src", all(k in src for k in U), "every uniform name appears in the snippet: missing %s" % [k for k in U if k not in src])
    rep.fail("shader.samplers", all(s in (e.get("textures") or {}) for s in (sh.get("textures") or {}).values()), "sampler sheets resolved by build")
    fl = sh.get("flicker") or {}
    hz, step = fl.get("hz", 0), fl.get("luma_step", 0)
    rep.fail("shader.photosensitive", hz <= C["max_flash_hz"] or step <= C["max_full_field_luma_step"], "full-field flicker %.1f Hz at a %.2f luma step (<= %.1f Hz or <= %.2f step; WCAG 2.3.1)" % (hz, step, C["max_flash_hz"], C["max_full_field_luma_step"]))
    mem = sum(t["size"][0] * t["size"][1] * 4 * 1.334 / 2 ** 20 for t in (e.get("textures") or {}).values())
    rep.fail("shader.tex_mb", mem <= C["max_tex_mb"], "%.2f MB of shader textures (<= %.1f)" % (mem, C["max_tex_mb"]))


def run(json_path, spec, root, brief=None):
    rep = Report(); V = spec["vfx"]
    e = json.load(open(json_path)); eid = e.get("id", "?")
    summ = {"id": eid}
    # ------------------------------------------------------------------ schema
    rep.fail("schema.format", e.get("format") == V["format"] and e.get("version") == V["version"], "format %s v%s (want %s v%s)" % (e.get("format"), e.get("version"), V["format"], V["version"]))
    rep.fail("schema.id", bool(re.match(V["id_regex"], eid)) and os.path.basename(json_path) == eid + ".json", "id %s, file %s" % (eid, os.path.basename(json_path)))
    cls = e.get("class"); C = V["classes"].get(cls)
    rep.fail("schema.class", C is not None, "class %s (one of %s)" % (cls, ", ".join(V["classes"])))
    if C is None: return rep, summ
    for k, typ in (("duration_s", (int, float)), ("loop", bool), ("seed", int), ("emitters", list), ("lod", dict), ("hooks", dict), ("attach", dict)):
        rep.fail("schema.field_%s" % k, isinstance(e.get(k), typ), "%s: %r" % (k, type(e.get(k)).__name__))
    ems = e.get("emitters") or []
    rep.fail("schema.emitters", (len(ems) > 0 or cls == "shader") and len({m.get("name") for m in ems}) == len(ems), "%d emitters, names unique (class shader: none)" % len(ems))
    tex = e.get("textures", {})
    for m in ems:
        n = m.get("name", "?"); w = "emitter %s" % n
        rep.fail("schema.blend", m.get("blend") in V["blend"]["allowed"], "%s blend %s (allowed %s)" % (w, m.get("blend"), V["blend"]["allowed"]))
        rep.fail("schema.billboard", m.get("billboard", "camera") in vl.BILLBOARDS, "%s billboard %s" % (w, m.get("billboard")))
        rep.fail("schema.shape", (m.get("shape") or {}).get("type") in vl.SHAPES, "%s shape %s" % (w, m.get("shape")))
        for k in ("life_s", "size_m", "speed_mps"):
            ok = k in m
            if ok:
                a, b = vl.rng2(m[k]); ok = 0 <= a <= b
            rep.fail("schema.range_%s" % k, ok, "%s %s = %s (min <= max, >= 0)" % (w, k, m.get(k)))
        cg = m.get("color") or []
        ok = len(cg) >= 2 and all(len(g) == 3 and 0 <= g[0] <= 1 for g in cg) and [g[0] for g in cg] == sorted(g[0] for g in cg) and cg[0][0] == 0 and cg[-1][0] == 1
        rep.fail("schema.color", ok, "%s color gradient [[t 0..1 sorted, first 0, last 1, '#hex', alpha], ...] (%d keys)" % (w, len(cg)))
        sc = m.get("size_curve")
        if sc is not None: rep.fail("schema.size_curve", all(len(p) == 2 and 0 <= p[0] <= 1 and p[1] >= 0 for p in sc), "%s size_curve %s" % (w, sc))
        rep.fail("schema.spawn", bool(m.get("bursts")) or (m.get("rate") or {}).get("per_s", 0) > 0, "%s needs bursts or rate.per_s" % w)
        t = tex.get(m.get("texture"))
        rep.fail("schema.texture_ref", t is not None, "%s texture %s listed in textures" % (w, m.get("texture")))
        if t:
            nf = t["grid"][0] * t["grid"][1]; fr = m.get("frames", [0, nf - 1])
            rep.fail("schema.frames", 0 <= fr[0] <= fr[1] < nf, "%s frames %s within the %dx%d sheet" % (w, fr, *t["grid"]))
            rep.fail("schema.frame_mode", m.get("frame_mode", "over_life") in vl.FRAME_MODES, "%s frame_mode %s" % (w, m.get("frame_mode")))
        if m.get("billboard") == "stretched":
            rep.fail("schema.stretched_speed", vl.rng2(m["speed_mps"])[0] > 0, "%s: a stretched billboard needs speed > 0 (its axis is the velocity)" % w)
        for bad in ("intensity", "emissive_scale", "bloom", "hdr"):
            rep.fail("palette.ldr_%s" % bad, bad not in m, "%s: no %s field (S9: LDR colours only; brightness comes from the colour and alpha)" % (w, bad))
    # ------------------------------------------------------------------ textures
    tdir = os.path.join(root, spec["paths"]["export"], V["export_dir"])
    T = V["textures"]; mem = 0.0
    for name, t in tex.items():
        p = os.path.join(tdir, t["path"]); im = _png(p) if os.path.exists(p) else None
        rep.fail("tex.exists", im is not None, "%s -> %s" % (name, os.path.relpath(p, root)))
        if im is None: continue
        rep.fail("tex.name", bool(re.match(V["texture_regex"], name)), "%s matches %s" % (name, V["texture_regex"]))
        h, w = im.shape[:2]
        rep.fail("tex.rgba", im.ndim == 3 and im.shape[2] == 4, "%s has an alpha channel" % name)
        pot = lambda x: x & (x - 1) == 0
        rep.fail("tex.size", pot(w) and pot(h) and max(w, h) <= T["max_px"] and [w, h] == list(t["size"]), "%s %dx%d (power of two, <= %d, data says %s)" % (name, w, h, T["max_px"], t["size"]))
        cols, rows = t["grid"]; cw, ch = w // cols, h // rows
        rep.fail("tex.cell", w % cols == 0 and h % rows == 0 and min(cw, ch) >= T["min_cell_px"], "%s cell %dx%d (>= %d)" % (name, cw, ch, T["min_cell_px"]))
        mem += w * h * 4 * (1.334 if T.get("mips", True) else 1) / 2 ** 20
        if im.ndim != 3 or im.shape[2] != 4: continue
        if t.get("tiling"): check_tiling(rep, name, im, t); continue   # class shader textures: seamless tiles, not sprite cells
        a = im[..., 3].astype(np.float32); rgb = im[..., :3].astype(np.float32) / 255
        worst_b, empty, darkbleed = 0, [], []
        for r in range(rows):
            for c in range(cols):
                ca = a[r * ch:(r + 1) * ch, c * cw:(c + 1) * cw]; crgb = rgb[r * ch:(r + 1) * ch, c * cw:(c + 1) * cw]
                border = np.concatenate([ca[0], ca[-1], ca[:, 0], ca[:, -1]]); worst_b = max(worst_b, border.max())
                if ca.max() < 128: empty.append(r * cols + c)
                lit = ca > 128; clear = (ca < 3)
                if lit.any() and clear.any():
                    l_in = crgb[lit].mean(); l_out = crgb[clear].mean()
                    if l_out < 0.5 * l_in: darkbleed.append(r * cols + c)
        rep.fail("tex.border_alpha", worst_b <= T["border_alpha_max"], "%s: max alpha on a cell border %d/255 (<= %d: no hard quad edges)" % (name, worst_b, T["border_alpha_max"]))
        rep.fail("tex.empty_cells", not empty, "%s: cells with no coverage %s" % (name, empty or "none"))
        rep.warn("tex.bleed", not darkbleed, "%s: RGB under A=0 much darker than the sprite in cells %s (dark mip fringes)" % (name, darkbleed or "none"))
        if name.endswith("_e"):
            spread = (im[..., :3].max(-1).astype(int) - im[..., :3].min(-1).astype(int))[a > 8].max(initial=0)
            rep.fail("tex.emissive_grey", spread <= 2, "%s: emissive sheets are greyscale (tinted by data); channel spread %d/255" % (name, spread))
        else:
            ne = V["colour"]["non_emissive"]; bl = V["colour"]["blood"]; bad = []
            for r in range(rows):
                for c in range(cols):
                    ca = a[r * ch:(r + 1) * ch, c * cw:(c + 1) * cw] > 128
                    if not ca.any(): continue
                    px = rgb[r * ch:(r + 1) * ch, c * cw:(c + 1) * cw][ca]
                    mx, mn = px.max(-1), px.min(-1); sat = np.where(mx > 1e-6, (mx - mn) / np.maximum(mx, 1e-6), 0)
                    s95, v99 = float(np.percentile(sat, 95)), float(np.percentile(mx, 99))
                    mean = px.mean(0); hh, ss, vv = vl.hsv("#%02X%02X%02X" % tuple(int(x * 255) for x in mean))
                    is_blood = vl.hue_dist(hh, vl.hsv(bl["hex"])[0]) <= bl["hue_tolerance_deg"] and vv <= bl["max_value"]
                    if not ((s95 <= ne["max_saturation"] + 0.04 or (is_blood and s95 <= bl["max_saturation"] + 0.05)) and v99 <= 0.95):
                        bad.append("cell %d sat95 %.2f v99 %.2f" % (r * cols + c, s95, v99))
            rep.fail("tex.palette", not bad, "%s: colour cells within S3 (sat <= %.2f or dried blood; no pure white): %s" % (name, ne["max_saturation"], "; ".join(bad) or "ok"))
    rep.fail("tex.memory_effect", mem <= T["max_effect_tex_mb"], "%.2f MB RGBA8+mips for this effect's sheets (<= %.1f; KTX2 is smaller)" % (mem, T["max_effect_tex_mb"]))
    tot = sum(_png(f).shape[0] * _png(f).shape[1] * 4 * 1.334 / 2 ** 20 for f in glob.glob(os.path.join(tdir, "tex", "*.png")))
    rep.fail("tex.memory_total", tot <= T["max_total_tex_mb"], "%.2f MB for all vfx sheets (<= %.1f)" % (tot, T["max_total_tex_mb"]))
    summ["tex_mb"] = round(mem, 2)
    # ------------------------------------------------------------------ budget / loop
    est = vl.estimate(e, vl.ref_for(e, spec)); summ.update({k: est[k] for k in ("peak_alive", "fill_peak", "end_s")})
    rep.fail("budget.particles", est["peak_alive"] <= C["max_particles"], "peak alive %d (class %s <= %d; per emitter %s)" % (est["peak_alive"], cls, C["max_particles"], est["per_emitter_peak"]))
    omax = C.get("overdraw_fp_max") or C.get("overdraw_max")
    if omax: rep.fail("budget.fill", est["fill_peak"] <= omax, "fill %.2f screens of quads at t=%.3fs, ref %.1f m @ %d deg (<= %.1f)" % (est["fill_peak"], est["fill_peak_t"], est["ref"]["distance"], est["ref"]["fov_deg"], omax))
    else: rep.info("budget.fill", "fill %.2f screens at %.1f m" % (est["fill_peak"], est["ref"]["distance"]))
    if not e.get("loop"):
        rep.fail("budget.duration", est["end_s"] <= e["duration_s"] + 1 / 60 + 1e-6, "last particle dies at %.3fs, declared duration %.3fs (instances are freed at duration)" % (est["end_s"], e["duration_s"]))
        if C["max_duration_s"]: rep.fail("budget.class_duration", e["duration_s"] <= C["max_duration_s"], "duration %.2fs (class %s <= %.2f)" % (e["duration_s"], cls, C["max_duration_s"]))
    if C.get("max_flash_life_s"):
        for m in ems:
            if m.get("blend") == "add" and m.get("role", "flash") == "flash":
                rep.fail("budget.flash_life", vl.rng2(m["life_s"])[1] <= C["max_flash_life_s"], "emitter %s life %s s (a flash lasts <= %.2f s: 2-4 frames)" % (m["name"], m["life_s"], C["max_flash_life_s"]))
    rep.fail("loop.class", bool(e.get("loop")) == bool(C["loop"]), "loop %s (class %s expects %s)" % (e.get("loop"), cls, C["loop"]))
    if e.get("loop"):
        rep.fail("loop.rate", all((m.get("rate") or {}).get("per_s", 0) > 0 and not m.get("bursts") for m in ems), "looping effects spawn by rate only (no bursts)")
    for m in ems:
        cg = m.get("color") or []
        if cg: rep.warn("loop.fade_out", cg[-1][2] <= 0.05 or m.get("pop_out", False), "emitter %s ends at alpha %.2f (fade to 0, or set pop_out with a reason)" % (m["name"], cg[-1][2]))
    # ------------------------------------------------------------------ palette
    FL = V["colour"]["emissive"].get("fire_look") or {}; fl_on = cls in FL.get("classes", [])
    flame = lambda m: fl_on and not m.get("lit", False) and m.get("billboard", "camera") != "stretched" and bool(re.match(FL.get("flame_sheet_regex", "^$"), m.get("texture", "")))
    fire_body = lambda m: flame(m) and m.get("blend") == "alpha"
    if fl_on and any(flame(m) for m in ems):   # fire-look rule: alpha body + a small, capped additive core
        def _area(m):
            if (m.get("rate") or {}).get("t1") is not None or m.get("bursts"): return 0.0   # catch/splash windows are transient
            sc = m.get("size_curve") or [[0, 1], [1, 1]]; k = sum(p[1] for p in sc) / len(sc)
            return m["rate"]["per_s"] * sum(vl.rng2(m["life_s"])) / 2 * (sum(vl.rng2(m["size_m"])) / 2 * k) ** 2
        tot = sum(_area(m) for m in ems if flame(m)); core = sum(_area(m) for m in ems if flame(m) and m.get("blend") == "add")
        share = core / tot if tot > 0 else 0.0
        rep.fail("palette.fire_core_share", share <= FL["core_max_area_share"] + 1e-6, "additive flame core covers %.0f%% of the steady flame area (<= %.0f%%; the body is alpha-blended, spec fire_look)" % (share * 100, FL["core_max_area_share"] * 100))
        worst = max([vl.hsv(g[1])[2] * g[2] for m in ems if flame(m) and m.get("blend") == "add" for g in m.get("color") or []] or [0.0])
        rep.fail("palette.fire_core_add", worst <= FL["core_max_add"] + 1e-6, "additive core adds at most value x alpha %.2f per layer (<= %.2f: brightness ceiling under the S9 grade)" % (worst, FL["core_max_add"]))
    for m in ems:
        if V["blend"]["add_only_for_emissive"]:
            rep.fail("palette.add_emissive", m.get("blend") != "add" or m["texture"].endswith("_e"), "emitter %s: additive only with an emissive (_e) sheet" % m["name"])
            if fire_body(m): rep.info("palette.fire_body", "emitter %s: alpha-blended unlit flame body on %s (spec colour.emissive.fire_look)" % (m["name"], m["texture"]))
            else: rep.warn("palette.alpha_on_emissive", m.get("blend") != "alpha" or not m["texture"].endswith("_e"), "emitter %s: alpha blend on an emissive sheet" % m["name"])
        for g in m.get("color") or []:
            check_colour_key(rep, "emitter %s t=%.2f" % (m["name"], g[0]), g[1], g[2], m.get("blend"), m, e, V, spec)
    # ------------------------------------------------------------------ hooks
    hooks = e.get("hooks", {}); at = e.get("attach", {}); H = V["hooks"]
    ev_names = set(spec["animation"]["first_person"]["events"]["names"]) | {"hit", "ignite", "detonate"}
    for req in C["required_hooks"]:
        if req == "attach.event":
            rep.fail("hooks.event", at.get("event") in C["events"] and at.get("event") in ev_names, "attach.event %s (class %s: %s)" % (at.get("event"), cls, C["events"]))
        elif req == "attach.socket":
            rep.fail("hooks.socket", at.get("socket") in C["sockets"], "attach.socket %s (class %s: %s)" % (at.get("socket"), cls, C["sockets"]))
        elif req == "surface":
            rep.fail("hooks.surface", hooks.get("surface") in H["surfaces"], "hooks.surface %s (%s)" % (hooks.get("surface"), H["surfaces"]))
        elif req == "decal":
            dt = json.load(open(os.path.join(root, spec["paths"]["materials_dir"], "decals.json")))["m_decal_runtime"]["decals"]
            d = hooks.get("decal") or {}
            rep.fail("hooks.decal", d.get("id") in dt, "hooks.decal.id %s in materials/decals.json m_decal_runtime" % d.get("id"))
            rep.fail("hooks.decal_place", d.get("place", "surface") in ("surface", "ground_below"), "decal place %s" % d.get("place"))
        elif req == "sfx":
            s = hooks.get("sfx")
            rep.fail("hooks.sfx", bool(s) and bool(re.match(H["sfx_regex"], s)), "hooks.sfx %s" % s)
        elif req == "light":
            L = hooks.get("light") or {}; LH = V["light_hook"]
            ok = bool(L)
            if ok:
                hh, ss, vv = vl.hsv(L.get("color", "#000000"))
                # a looping effect (fire) may keep its light for its life: `loop: true` + a flicker `period_s` instead of a flash duration
                dur_ok = L.get("duration_s", 99) <= LH["max_duration_s"] or (bool(e.get("loop")) and bool(L.get("loop")) and L.get("period_s", 0) > 0)
                lp = bool(e.get("loop")) and bool(L.get("loop"))   # a fire's standing light may be stronger than a flash (spec loop_max_*)
                mi = LH.get("loop_max_intensity", LH["max_intensity"]) if lp else LH["max_intensity"]; mr = LH.get("loop_max_range_m", LH["max_range_m"]) if lp else LH["max_range_m"]
                ok = L.get("intensity", 99) <= mi and L.get("range_m", 99) <= mr and dur_ok and LH["colour_hue_deg"][0] <= hh <= LH["colour_hue_deg"][1]
            rep.fail("hooks.light", ok, "light %s (intensity <= %g, range <= %g m, duration <= %g s, hue %s; S9 no bloom)" % (L, LH["max_intensity"], LH["max_range_m"], LH["max_duration_s"], LH["colour_hue_deg"]))
        elif req == "mesh":
            M = hooks.get("mesh") or {}
            ok = bool(M.get("id")) and bool(re.match(H["mesh_regex"], M["id"]))
            rep.fail("hooks.mesh", ok, "hooks.mesh.id %s (%s)" % (M.get("id"), H["mesh_regex"]))
            mp = os.path.join(root, spec["paths"]["manifest"]); man = {x["id"] for x in (json.load(open(mp)) if os.path.exists(mp) else [])}
            fb = [m["name"] for m in ems if m.get("role") == "mesh_fallback"]
            rep.warn("hooks.mesh_delivered", M.get("id") in man, "mesh %s %s; sprite fallback emitters: %s" % (M.get("id"), "in manifest" if M.get("id") in man else "NOT delivered yet (engine uses the fallback)", fb or "none"))
            rep.fail("hooks.mesh_fallback", M.get("id") in man or bool(fb), "an undelivered mesh needs a role=mesh_fallback emitter")
    if hooks.get("sfx") and "sfx" not in C["required_hooks"]:
        rep.fail("hooks.sfx", bool(re.match(H["sfx_regex"], hooks["sfx"])), "hooks.sfx %s" % hooks["sfx"])
    # ------------------------------------------------------------------ runtime inputs (gameplay-driven loops)
    ins = e.get("inputs") or {}
    for k, d in ins.items():
        if isinstance(d.get("default"), (int, float)):
            ok = all(isinstance(d.get(x), (int, float)) for x in ("min", "max")) and d["min"] <= d["default"] <= d["max"] and 0 < d.get("ref", d["max"]) <= d["max"] + 1e-9
            rep.fail("inputs.range", ok, "input %s: min <= default <= max, 0 < ref <= max (%s)" % (k, {x: d.get(x) for x in ("min", "default", "max", "ref")}))
    drives = [("emitter " + m["name"], m.get("drive") or {}) for m in ems] + [("light", (hooks.get("light") or {}).get("drive") and {"intensity": hooks["light"]["drive"]} or {})]
    for where, dv in drives:
        for tgt, lst in dv.items():
            rep.fail("inputs.drive", tgt in ("rate", "radius", "size", "alpha", "speed", "intensity") and all(len(x) == 2 and x[0] in ins for x in lst), "%s drive %s %s (targets rate/radius/size/alpha/speed; inputs %s)" % (where, tgt, lst, list(ins)))
            if tgt in ("rate", "size", "alpha", "intensity"):   # budgets are estimated at ref: the data must be authored at the worst case
                f = 1.0
                for k, pw in lst:
                    d = ins.get(k) or {}
                    if isinstance(d.get("max"), (int, float)): f *= (max(d["max"], 1e-9) / d.get("ref", d["max"])) ** pw if pw > 0 else (max(d.get("min", 1e-9), 1e-9) / d.get("ref", d["max"])) ** pw
                rep.fail("inputs.worst_case", f <= 1.0 + 1e-6, "%s drive %s reaches x%.2f at the input limits (author the data at the worst case: ref = the limit that maximises it)" % (where, tgt, f))
    if ins: rep.info("inputs", "runtime inputs %s" % ", ".join("%s[%s..%s]" % (k, d.get("min"), d.get("max")) for k, d in ins.items()))
    if cls == "shader": check_shader(rep, e, C, spec, root)
    # ------------------------------------------------------------------ lod
    L = e.get("lod") or {}
    ok = all(k in L for k in V["lod"]["fields"]) and 0 < L.get("reduce_m", 0) < L.get("cull_m", 0) and 0 < L.get("reduce_factor", 0) <= 1
    rep.fail("lod.fields", ok, "lod %s (0 < reduce_m < cull_m, 0 < reduce_factor <= 1)" % L)
    rep.info("summary", "peak %d particles, fill %.2f, ends %.2fs, %d emitters, %.2f MB tex" % (est["peak_alive"], est["fill_peak"], est["end_s"], len(ems), mem))
    return rep, summ
