"""
uilib.py -- core of game-ui (plain CPython + numpy): project paths, palette and token resolution,
colour maths (sRGB mix, WCAG contrast, hue/saturation), file writers shared by the builders.

Shared with game-asset-modeling by import (find_spec, Report, claims, Slot); nothing is copied.
"""
import colorsys, json, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)
SKILLS = os.path.dirname(SKILL)
GAM_SCRIPTS = os.path.join(SKILLS, "game-asset-modeling", "scripts")
GANIM_SCRIPTS = os.path.join(SKILLS, "game-animation", "scripts")
if GAM_SCRIPTS not in sys.path: sys.path.insert(0, GAM_SCRIPTS)
from validate_glb import find_spec, Report  # noqa: E402,F401  (game-asset-modeling)


# --------------------------------------------------------------------------- paths

def project(start=None):
    sp = find_spec(os.path.join(start or os.getcwd(), "x")); spec = json.load(open(sp)); root = os.path.dirname(sp)
    if "ui" not in spec: raise SystemExit("assets/spec.json has no 'ui' section: see references/spec-ui.md")
    return spec, sp, root


def kind_of(spec, aid):
    for k, v in spec["ui"]["kinds"].items():
        if aid == v["prefix"] or aid.startswith(v["prefix"]): return k
    raise SystemExit("%s: no ui kind prefix matches (%s)" % (aid, [v["prefix"] for v in spec["ui"]["kinds"].values()]))


def paths_for(brief_path):
    brief_path = os.path.abspath(brief_path)
    spec, sp, root = project(os.path.dirname(brief_path)); b = json.load(open(brief_path))
    k = b.get("kind") or kind_of(spec, b["id"]); K = spec["ui"]["kinds"][k]
    exp = os.path.join(root, spec["paths"]["export"], K["export_dir"])
    return {"brief_path": brief_path, "brief": b, "dir": os.path.dirname(brief_path), "id": b["id"], "kind": k,
            "spec": spec, "spec_path": sp, "root": root, "export_dir": exp,
            "json": os.path.join(exp, b["id"] + ".json"), "work": os.path.join(os.path.dirname(brief_path), "_work"),
            "review": os.path.join(root, spec["paths"]["review"], b["id"]),
            "sheet_png": os.path.join(root, spec["paths"]["review"], b["id"] + ".png")}


def all_briefs(root, spec):
    out = []
    base = os.path.join(root, spec["paths"]["src"])
    for k, K in spec["ui"]["kinds"].items():
        d = os.path.join(base, K["src_dir"])
        if not os.path.isdir(d): continue
        for n in sorted(os.listdir(d)):
            p = os.path.join(d, n, spec["ui"]["brief_file"])
            if os.path.exists(p): out.append(p)
    return out


def tokens_path(root, spec):
    return os.path.join(root, spec["paths"]["export"], spec["ui"]["kinds"]["tokens"]["export_dir"], "ui_tokens.json")


def load_tokens(root, spec):
    p = tokens_path(root, spec)
    if not os.path.exists(p): raise SystemExit("ui_tokens.json not built yet: ui_cli.py build assets/src/ui/tokens/ui_tokens/brief.json")
    return json.load(open(p))


def write_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"; json.dump(obj, open(tmp, "w"), indent=1, ensure_ascii=False); os.replace(tmp, path)


# --------------------------------------------------------------------------- colour

def hex_rgb(h):
    h = h.lstrip("#"); return tuple(int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))


def rgb_hex(c):
    return "#%02X%02X%02X" % tuple(int(round(min(1, max(0, x)) * 255)) for x in c[:3])


def mix(a, b, t):
    """sRGB mix: t=0 -> a, t=1 -> b (what CSS color-mix in srgb and a Godot lerp do)."""
    A, B = hex_rgb(a), hex_rgb(b); return rgb_hex([A[i] + (B[i] - A[i]) * t for i in range(3)])


def lin(c):
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def luminance(h):
    r, g, b = hex_rgb(h); return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)


def contrast(a, b):
    la, lb = luminance(a), luminance(b); hi, lo = max(la, lb), min(la, lb); return (hi + 0.05) / (lo + 0.05)


def hsv(h):
    return colorsys.rgb_to_hsv(*hex_rgb(h))


def hue_dist(h1, h2):
    d = abs(h1 - h2) % 1.0; return min(d, 1 - d) * 360


def base_palette(spec):
    """name -> hex for every base token the UI may use (S10 materials + chosen S3 swatches)."""
    U = spec["ui"]["palette"]; sw = spec["palette"]["swatches"]
    out = dict(U["base"])
    for n in U["from_swatches"]: out[n] = sw[n]
    return out


def resolve_color(expr, base, derived=None):
    """'phosphor' | '#RRGGBB' (must be base) | 'mix(a, b, t)'."""
    expr = expr.strip()
    if expr in base: return base[expr]
    if derived and expr in derived: return derived[expr]
    m = re.match(r"mix\(\s*([a-z_]+)\s*,\s*([a-z_]+)\s*,\s*([0-9.]+)\s*\)$", expr)
    if m:
        a, b, t = m.group(1), m.group(2), float(m.group(3))
        if a not in base or b not in base: raise ValueError("mix() of non-base token in %r" % expr)
        return mix(base[a], base[b], t)
    raise ValueError("colour %r is not a base token or mix()" % expr)


def palette_hexes(spec, tokens=None):
    """All hexes allowed in pixels: base + the tokens' derived colours."""
    b = base_palette(spec); out = dict(b)
    if tokens: out.update(tokens.get("color", {}))
    return out


# --------------------------------------------------------------------------- pixels

def pixel_palette_report(rgba, spec, allowed_hexes, mask=None):
    """Check opaque-ish pixels against the palette rules. Returns dict of stats.
    - near-pure black/white (all channels < min or > max) -> violation
    - saturation > cap outside the accent hues -> violation
    - distance to the palette's convex 'mix' set is approximated by the nearest segment between two allowed colours."""
    import numpy as np
    U = spec["ui"]["palette"]
    a = rgba[..., 3] / 255.0 if rgba.dtype != np.float32 else rgba[..., 3]
    rgb = rgba[..., :3].astype(np.float32) / (255.0 if rgba.dtype != np.float32 else 1.0)
    sel = a > 0.5
    if mask is not None: sel &= mask
    px = rgb[sel]
    if len(px) == 0: return {"n": 0, "pure": 0, "sat": 0, "off": 0, "worst": None}
    if len(px) > 60000: px = px[np.random.RandomState(0).choice(len(px), 60000, replace=False)]
    lo, hi = U["no_pure_min"] / 255, U["no_pure_max"] / 255
    pure = ((px < lo).all(1) | (px > hi).all(1)).sum()
    mx, mn = px.max(1), px.min(1); sat = np.where(mx > 0, (mx - mn) / np.maximum(mx, 1e-6), 0)
    import colorsys as cs
    hues = np.array([cs.rgb_to_hsv(*p)[0] for p in px[::max(1, len(px) // 4000)]])
    sat_s = sat[::max(1, len(px) // 4000)]
    acc = [hsv(v["hex"])[0] for v in U["accent_hues"].values()]; tol = [v["tol_deg"] for v in U["accent_hues"].values()]
    in_acc = np.zeros(len(hues), bool)
    for h0, t in zip(acc, tol): in_acc |= np.array([hue_dist(h, h0) <= t for h in hues])
    over = ((sat_s > U["max_saturation_outside_accents"]) & ~in_acc & (px[::max(1, len(px) // 4000)].max(1) > 0.35)).sum()   # HSV saturation of dark colours is unstable (ministry ink is 0.39): value floor
    # distance to the mix hull (segments between allowed colours)
    cols = np.array([hex_rgb(h) for h in allowed_hexes.values()], np.float32)
    sub = px[::max(1, len(px) // 4000)]
    best = np.full(len(sub), 9.0, np.float32)
    for i in range(len(cols)):
        for j in range(i, len(cols)):
            A, B = cols[i], cols[j]; d = B - A; L = float((d * d).sum())
            t = np.clip(((sub - A) @ d) / L, 0, 1) if L > 0 else np.zeros(len(sub))
            q = A + t[:, None] * d; best = np.minimum(best, np.linalg.norm(sub - q, axis=1))
    off = (best > U["pixel_tolerance"]).mean()
    return {"n": int(sel.sum()), "pure": int(pure), "sat_frac": float(over) / max(1, len(sub)), "off_frac": float(off), "max_dist": float(best.max())}
