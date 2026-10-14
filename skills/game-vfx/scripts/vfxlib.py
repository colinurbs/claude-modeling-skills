"""
vfxlib.py -- core of game-vfx (plain CPython + numpy): project paths, brief -> export build,
deterministic budget estimates (alive particles, fill/overdraw, duration), colour helpers.

Shared with game-asset-modeling by import (find_spec, Report, claims, Slot); nothing is copied.
"""
import colorsys, copy, json, math, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)
GAM_SCRIPTS = os.path.join(os.path.dirname(SKILL), "game-asset-modeling", "scripts")
if GAM_SCRIPTS not in sys.path: sys.path.insert(0, GAM_SCRIPTS)
from validate_glb import find_spec  # noqa: E402  (game-asset-modeling)

BLENDS = ("alpha", "add")
BILLBOARDS = ("camera", "stretched", "y_axis")
SHAPES = ("point", "cone", "sphere", "hemisphere", "box", "disc")
FRAME_MODES = ("over_life", "random", "fixed")
BRIEF_ONLY = ("status", "review", "source", "license", "_note")


# --------------------------------------------------------------------------- paths

def project(start=None):
    sp = find_spec(os.path.join(start or os.getcwd(), "x")); spec = json.load(open(sp)); root = os.path.dirname(sp)
    if "vfx" not in spec: raise SystemExit("assets/spec.json has no 'vfx' section: see references/spec-vfx.md")
    return spec, sp, root


def paths_for(brief_path):
    brief_path = os.path.abspath(brief_path)
    spec, sp, root = project(brief_path); v = spec["vfx"]; b = json.load(open(brief_path))
    exp = os.path.join(root, spec["paths"]["export"], v["export_dir"])
    return {"brief": brief_path, "dir": os.path.dirname(brief_path), "id": b["id"], "spec": spec, "spec_path": sp, "root": root,
            "export_dir": exp, "tex_dir": os.path.join(root, spec["paths"]["export"], v["tex_export_dir"]),
            "json": os.path.join(exp, b["id"] + ".json"), "work": os.path.join(os.path.dirname(brief_path), "_work"),
            "sheets": os.path.join(root, spec["paths"]["src"], v["sheets_file"]),
            "review": os.path.join(root, spec["paths"]["review"], b["id"]), "sheet_png": os.path.join(root, spec["paths"]["review"], b["id"] + ".png")}


def load_sheets(P):
    return json.load(open(P["sheets"]))["sheets"] if os.path.exists(P["sheets"]) else {}


# --------------------------------------------------------------------------- colours

def hexrgb(h):
    h = h.lstrip("#"); return tuple(int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))


def hsv(h):
    r, g, b = hexrgb(h); hh, s, v = colorsys.rgb_to_hsv(r, g, b); return hh * 360, s, v


def hue_dist(a, b):
    d = abs(a - b) % 360; return min(d, 360 - d)


def resolve_colour(c, spec):
    """'#RRGGBB' or a palette swatch name ('tungsten', 'slab', ...) -> '#RRGGBB'."""
    if isinstance(c, str) and c.startswith("#"): return c.upper()
    sw = spec["palette"]["swatches"]
    if c in sw: return sw[c].upper()
    raise SystemExit("unknown colour %r (hex or palette swatch: %s)" % (c, ", ".join(sw)))


# --------------------------------------------------------------------------- curves

def curve_eval(curve, t, default=1.0):
    """curve = [[t, v], ...] (t 0..1 of particle life), linear; or a scalar."""
    if curve is None: return default
    if isinstance(curve, (int, float)): return float(curve)
    pts = sorted(curve)
    if t <= pts[0][0]: return pts[0][1]
    for (t0, v0), (t1, v1) in zip(pts, pts[1:]):
        if t <= t1: return v0 + (v1 - v0) * (t - t0) / max(1e-9, t1 - t0)
    return pts[-1][1]


def rng2(v):
    if isinstance(v, (int, float)): return [float(v), float(v)]
    return [float(v[0]), float(v[1])]


# --------------------------------------------------------------------------- estimates

def spawn_schedule(em, duration, lod_factor=1.0):
    """[(t_spawn, count)] at 1/60 s resolution for bursts and rate windows."""
    out = []
    for b in em.get("bursts", []):
        out.append((float(b.get("t", 0)), max(0, int(round(rng2(b.get("count", 1))[1] * lod_factor)))))
    r = em.get("rate")
    if r and r.get("per_s", 0) > 0:
        t0 = float(r.get("t0", 0)); t1 = float(r.get("t1", duration)); acc = 0.0; t = t0
        while t < t1 - 1e-9:
            acc += r["per_s"] * lod_factor / 60.0; n = int(acc); acc -= n
            if n: out.append((t, n))
            t += 1 / 60.0
    return out


def estimate(effect, ref=None):
    """alive particles over time (life = max), fill (sum of quad areas / view area) at a reference distance,
    and the true end time. ref = {"distance": m, "fov_deg": vertical, "aspect": w/h}."""
    ref = ref or {"distance": 3.0, "fov_deg": 75, "aspect": 16 / 9}
    dur = float(effect.get("duration_s", 1.0)); loop = effect.get("loop", False)
    horizon = dur if not loop else max(dur, 4.0)
    steps = int(math.ceil(horizon * 60)) + 120
    alive = [0] * steps; fill = [0.0] * steps; end_t = 0.0
    vh = 2 * ref["distance"] * math.tan(math.radians(ref["fov_deg"]) / 2); area = vh * vh * ref["aspect"]
    per_em = {}
    for em in effect["emitters"]:
        life = rng2(em["life_s"])[1]; size = rng2(em["size_m"])[1]; scl = rng2(em.get("scale", 1.0))[1]
        stretch = float(em.get("stretch", 1.0)); peak_em = [0] * steps
        for (ts, n) in spawn_schedule(em, horizon):
            k0 = int(round(ts * 60)); k1 = int(round((ts + life) * 60))
            end_t = max(end_t, ts + life)
            for k in range(k0, min(steps, k1)):
                age = (k - k0) / max(1, k1 - k0)
                s = size * scl * curve_eval(em.get("size_curve"), age)
                alive[k] += n; peak_em[k] += n; fill[k] += n * s * s * stretch / area
        per_em[em["name"]] = max(peak_em) if peak_em else 0
    pk = max(range(steps), key=lambda k: fill[k])
    return {"peak_alive": max(alive), "peak_alive_t": alive.index(max(alive)) / 60.0, "fill_peak": round(fill[pk], 4), "fill_peak_t": round(pk / 60.0, 4),
            "end_s": round(end_t, 4), "per_emitter_peak": per_em, "ref": ref, "alive_curve": alive[:int(math.ceil(min(horizon, end_t + 0.1) * 60)) + 1]}


def ref_for(effect, spec):
    """reference view for fill estimates: first person for muzzle/shell (muzzle ~0.6 m from the eye at the
    view-model FOV), 3 m at the world FOV otherwise."""
    rv = spec["vfx"]["review"]
    if effect.get("class") in ("muzzle", "shell"): return {"distance": 0.6, "fov_deg": rv["fp"]["fov_deg"], "aspect": 16 / 9}
    return {"distance": 3.0, "fov_deg": rv["fp"]["world_fov_deg"], "aspect": 16 / 9}


# --------------------------------------------------------------------------- build

def build(brief_path):
    """brief (src/vfx/<id>/vfx.json) -> export/vfx/<id>.json: palette names resolved to hex, texture sheets
    resolved (relative path, size, grid, kind), estimates attached. Pure data, deterministic."""
    P = paths_for(brief_path); spec = P["spec"]; b = json.load(open(brief_path)); sheets = load_sheets(P)
    out = {k: copy.deepcopy(v) for k, v in b.items() if k not in BRIEF_ONLY}
    out["format"] = spec["vfx"]["format"]; out["version"] = spec["vfx"]["version"]
    tex = {}
    for em in out["emitters"]:
        t = em["texture"]
        if t not in sheets: raise SystemExit("%s: emitter %s uses unknown sheet %s (src/vfx/sheets.json)" % (b["id"], em["name"], t))
        sh = sheets[t]
        tex[t] = {"path": "tex/%s.png" % t, "size": [sh["size"], sh["size"]], "grid": sh["grid"], "kind": "emissive" if t.endswith("_e") else "colour"}
        for g in em.get("color", []):
            g[1] = resolve_colour(g[1], spec)
    if out.get("hooks", {}).get("light"):
        out["hooks"]["light"]["color"] = resolve_colour(out["hooks"]["light"].get("color", "tungsten"), spec)
    sh_ = out.get("shader")   # class shader (round 5): tiling textures by slot, colour uniforms (and presets) to hex
    if sh_:
        for slot, t in (sh_.get("textures") or {}).items():
            if t not in sheets: raise SystemExit("%s: shader slot %s uses unknown sheet %s" % (b["id"], slot, t))
            s_ = sheets[t]
            tex[t] = {"path": "tex/%s.png" % t, "size": [s_["size"], s_["size"]], "grid": s_["grid"], "tiling": True, "slot": slot,
                      "kind": "data" if t.endswith("_n") else ("mask" if t.endswith("_e") else "colour"), "srgb": False}   # tiling shader tiles are raw data (masks too): no sRGB decode
        for u in (sh_.get("uniforms") or {}).values():
            if u.get("type") == "color": u["default"] = resolve_colour(u["default"], spec)
        for pr in (sh_.get("presets") or {}).values():
            for k, v in list(pr.items()):
                if (sh_.get("uniforms") or {}).get(k, {}).get("type") == "color": pr[k] = resolve_colour(v, spec)
    out["textures"] = tex
    est = estimate(out, ref_for(out, spec)); est.pop("alive_curve")
    out["estimates"] = est
    os.makedirs(P["export_dir"], exist_ok=True)
    tmp = P["json"] + ".tmp"; json.dump(out, open(tmp, "w"), indent=1); os.replace(tmp, P["json"])
    return P, out
