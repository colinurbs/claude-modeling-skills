"""
validate_ui.py -- automatic checks for game-ui exports (uses game-asset-modeling's Report).

run(P) -> (Report, summary). P from uilib.paths_for(brief). Checks per kind:
  common    naming, files present, file sizes
  tokens    base colours = S3/S10, derived = declared mix of two base colours, role contrast by use, no pure black/white,
            type scale min sizes and families, spacing ascending
  fonts     files + OFL text present, LICENSES.md entry, glyph coverage of the copy sheet, tabular figures, size caps
  iconset   SVG whitelist (no gradient/filter/opacity/transform/text/image), grid snap, stroke width, live area, transparent
            padding, legibility at the minimum size, distinctness, crispness, pre-coloured atlas colour, atlas rules, contrast
  panel /   images per density at size x d, slice margins, hollow centre, seamless tiling of centre and edges, palette
  template  compliance of pixels, @1x vs @2x drift, slots inside the panel and styled from tokens, baked words come
            from the copy sheet, slot copy keys known (proposed words flagged)
"""
import io, json, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import numpy as np  # noqa: E402
import uilib as L  # noqa: E402
from uilib import Report  # noqa: E402


def _png(path):
    from PIL import Image
    return np.array(Image.open(path).convert("RGBA"))


def run(P):
    rep = Report(); spec = P["spec"]; U = spec["ui"]; summ = {}
    rep.fail("naming.id", bool(re.match(U["id_regex"], P["id"])), P["id"])
    out = json.load(open(P["json"]))
    k = P["kind"]
    if k == "tokens": summ = check_tokens(P, out, rep)
    elif k == "fonts": summ = check_fonts(P, out, rep)
    elif k == "iconset": summ = check_iconset(P, out, rep)
    elif k in ("panel", "template"): summ = check_nine(P, out, rep)
    elif k == "gauge": summ = check_gauge(P, out, rep)
    elif k == "map": summ = check_map(P, out, rep)
    elif k == "hud": summ = check_hud(P, out, rep)
    return rep, summ


# --------------------------------------------------------------------------- tokens

def check_tokens(P, t, rep):
    spec = P["spec"]; U = spec["ui"]; base = L.base_palette(spec); C = U["contrast"]
    rep.fail("tokens.format", t.get("format") == "ig_ui_tokens", t.get("format"))
    bad = [k for k, v in base.items() if t["base"].get(k, "").upper() != v.upper()]
    rep.fail("tokens.base_matches_spec", not bad, "mismatch: %s" % bad if bad else "%d base colours = S3/S10" % len(base))
    for k, expr in t.get("derived", {}).items():
        try:
            v = L.resolve_color(expr, base); rep.fail("tokens.derived:" + k, v.upper() == t["color"][k].upper(), "%s = %s" % (expr, v))
        except ValueError as e:
            rep.fail("tokens.derived:" + k, False, str(e))
    lo, hi = U["palette"]["no_pure_min"], U["palette"]["no_pure_max"]
    pure = [k for k, v in t["color"].items() if all(c * 255 < lo for c in L.hex_rgb(v)) or all(c * 255 > hi for c in L.hex_rgb(v))]
    rep.fail("tokens.no_pure_black_white", not pure, "pure: %s" % pure if pure else "S3")
    for name, r in t["roles"].items():
        use = r["use"]; need = {"text": C["text_min"], "large_text": C["large_text_min"], "icon": C["icon_min"]}.get(use)
        c = L.contrast(t["color"][r["fg"]], t["color"][r["bg"]])
        if need: rep.fail("contrast:" + name, c >= need, "%s on %s = %.2f (min %.1f for %s)" % (r["fg"], r["bg"], c, need, use))
        else: rep.info("contrast:" + name, "%s on %s = %.2f (decor, exempt)" % (r["fg"], r["bg"], c))
    fams = t["type"]["families"]
    for k, s in t["type"]["scale"].items():
        rep.fail("type.family:" + k, s["family"] in fams, s["family"])
        mn = U["type"]["min_px"].get(s["family"], 11)
        rep.fail("type.min_px:" + k, s["size"] >= mn, "%dpx (min %d)" % (s["size"], mn))
        rep.warn("type.tracking:" + k, -0.02 <= s["tracking_em"] <= 0.04, "%.2fem (S10: tight tracking)" % s["tracking_em"])
    fd = os.path.join(os.path.dirname(P["json"]), "fonts")
    for k, f in fams.items():
        miss = [x for x in f["files"] if not os.path.exists(os.path.join(os.path.dirname(P["json"]), x))]
        rep.fail("type.files:" + k, not miss, "missing %s (build ui_fonts first)" % miss if miss else "%d files" % len(f["files"]))
    sp = t["space_px"]; rep.fail("space.ascending", sp == sorted(sp) and len(set(sp)) == len(sp), str(sp))
    css = os.path.join(os.path.dirname(P["json"]), "ui_tokens.css"); rep.fail("tokens.css", os.path.exists(css), "ui_tokens.css generated")
    return {"colors": len(t["color"]), "roles": len(t["roles"])}


# --------------------------------------------------------------------------- fonts

def check_chalkfont(P, f, rep):
    """ig_ui_chalkfont (drawn letter sprites): atlases, palette, coverage of the brief's coverage_text (uppercased),
    variants per glyph and how different they are, one cap height (never reads as mixed case), kerning keys."""
    spec = P["spec"]; d = os.path.dirname(P["json"]); toks = L.load_tokens(P["root"], spec); allowed = L.palette_hexes(spec, toks)
    A = spec["ui"]["atlas"]; cap = f["cap_px"]["1"]; nv = f["variants"]; gl = f["glyphs"]; top = f["cell"]["top_em"]
    rep.fail("chalkfont.variants", nv >= 3, "%d drawings per glyph" % nv)
    ims = {}
    for kind, per in f["atlases"].items():
        for v, dd in per.items():
            for den, fn in dd.items():
                p = os.path.join(d, fn); ok = os.path.exists(p); rep.fail("chalkfont.file:" + fn, ok, fn)
                if not ok: continue
                im = _png(p); ims[(kind, v, den)] = im
                rep.fail("chalkfont.atlas_px:" + fn, max(im.shape[:2]) <= A["max_px"] * int(den), "%dx%d" % (im.shape[1], im.shape[0]))
                rep.fail("chalkfont.bytes:" + fn, os.path.getsize(p) <= A["max_png_bytes"], "%d B" % os.path.getsize(p))
                pr = L.pixel_palette_report(im, spec, allowed)
                rep.fail("palette.no_pure:" + fn, pr["pure"] == 0, "%d near-pure pixels" % pr["pure"])
                rep.fail("palette.mix_hull:" + fn, pr["off_frac"] <= 0.02, "%.1f%% off the two-token mixes" % (pr["off_frac"] * 100))
    text = ""
    cov = P["brief"].get("coverage_text") or []
    for c in (cov if isinstance(cov, list) else [cov]):
        cp = os.path.normpath(os.path.join(P["dir"], c)); text += open(cp).read() if os.path.exists(cp) else ""
    text += "".join(P["brief"].get("coverage_extra", []))
    need = sorted({c for c in text.upper() if c.isalnum() or c in ".,:;'-–()/!?·"} - {"§"})
    miss = [c for c in need if c not in gl]
    rep.fail("chalkfont.coverage", not miss, "missing %s" % "".join(miss) if miss else "all %d chars (uppercased) of the coverage text" % len(need))
    rep.fail("chalkfont.no_lowercase", not any(ch.islower() for ch in gl), "capitals only")
    rep.fail("chalkfont.kerning_keys", all(len(k) == 2 and k[0] in gl and k[1] in gl for k in f.get("kerning_em", {})), "%d pairs" % len(f.get("kerning_em", {})))
    hs, tops, bots, sims = [], [], [], []
    for ch, m in gl.items():
        rep.fail("chalkfont.frames:" + ch, len(m["frames"]) == nv and all(m["frames"]), "")
        if not (ch.isalpha() and ch.isascii()) or ch in "QJ": continue
        al = []
        for v, fr in enumerate(m["frames"]):
            im = ims.get(("chalk", str(v), "1"))
            if im is None: continue
            a = im[fr["y"]:fr["y"] + fr["h"], fr["x"]:fr["x"] + fr["w"], 3].astype(np.float32) / 255; al.append(a)
            rows = np.where(a.max(1) > 0.35)[0]
            if len(rows): t, b = top * cap - rows[0], rows[-1] - top * cap; tops.append(t / cap); bots.append(b / cap); hs.append((t + b) / cap)
        for i in range(len(al)):
            for j in range(i + 1, len(al)):
                if al[i].shape == al[j].shape: sims.append(float(np.abs(al[i] - al[j]).mean() / max(1e-6, (al[i] + al[j]).mean() / 2)))
    if hs:
        rep.fail("chalkfont.cap_height", max(hs) / min(hs) <= 1.15, "ink height %.2f-%.2f em over every capital and variant (one case: ratio <= 1.15)" % (min(hs), max(hs)))
        rep.warn("chalkfont.baseline", np.ptp(bots) <= 0.12 and np.ptp(tops) <= 0.14, "cap line spread %.2f em, baseline spread %.2f em" % (np.ptp(tops), np.ptp(bots)))
    if sims:
        rep.fail("chalkfont.variants_differ", min(sims) >= 0.15, "min normalised variant difference %.2f (>= 0.15: repeats never match)" % min(sims))
    return {"glyphs": len(gl), "variants": nv, "atlases": len(ims)}


def check_fonts(P, f, rep):
    if f.get("format") == "ig_ui_chalkfont": return check_chalkfont(P, f, rep)
    from fontTools.ttLib import TTFont
    spec = P["spec"]; U = spec["ui"]; d = os.path.dirname(P["json"]); root = P["root"]
    lic_md = open(os.path.join(root, spec["paths"]["licenses"])).read()
    cov = P["brief"].get("coverage_text"); text = ""
    if cov:
        cp = os.path.normpath(os.path.join(P["dir"], cov))
        text = open(cp).read() if os.path.exists(cp) else ""
        rep.fail("fonts.coverage_source", bool(text), cp)
    need = sorted({c for c in text if ord(c) > 32 and c not in "|`*#_\\"} | set("0123456789:·–—"))
    fams = set()
    for e in f["files"]:
        p = os.path.join(d, e["file"]); ok = os.path.exists(p)
        rep.fail("fonts.file:" + e["file"], ok, "%d bytes" % e["bytes"] if ok else "missing")
        if not ok: continue
        rep.fail("fonts.size:" + e["file"], e["bytes"] <= U["type"]["max_font_bytes"], "%d <= %d" % (e["bytes"], U["type"]["max_font_bytes"]))
        lf = os.path.join(d, e["license_file"])
        rep.fail("fonts.license_text:" + e["file"], os.path.exists(lf) and "SIL Open Font License" in open(lf).read() and "Reserved Font Name" in open(lf).read(), e["license_file"])
        if os.path.exists(lf):
            head = open(lf).read().split("This Font Software")[0]
            rep.fail("fonts.no_reserved_name:" + e["file"], "Reserved Font Name" not in head, "OFL header names no RFN, so subsets may keep the name")
        ft = TTFont(p); cm = ft.getBestCmap()
        miss = [c for c in need if ord(c) not in cm and c not in "№"]
        rep.fail("fonts.coverage:" + e["file"], not miss, "missing %s" % "".join(miss) if miss else "all %d glyphs of the copy sheet" % len(need))
        if e["role"] == "state":
            adv = {ft["hmtx"][cm[ord(c)]][0] for c in "0123456789"}
            rep.fail("fonts.tabular_figures:" + e["file"], len(adv) == 1, "digit advances %s (S10 monospaced numerals)" % sorted(adv))
        fams.add(e["family"])
    for fam in fams:
        rep.fail("fonts.licenses_md:" + fam, fam in lic_md and "OFL" in lic_md, "assets/LICENSES.md lists %s" % fam)
    return {"files": len(f["files"]), "families": sorted(fams)}


# --------------------------------------------------------------------------- icons

SVG_OK_TAGS = {"svg", "path", "circle", "rect", "polygon", "polyline"}
SVG_BAD = re.compile(r"(gradient|filter|opacity|transform|<text|<image|mask|clip-path|style=)", re.I)


def check_iconset(P, s, rep):
    import ui_icons as I
    spec = P["spec"]; U = spec["ui"]; IC = U["icon"]; d = os.path.dirname(P["json"])
    toks = L.load_tokens(P["root"], spec)
    G, meta, aliases = I.load_glyphs(os.path.join(P["dir"], P["brief"].get("glyphs", "glyphs.py")), IC["grid_u"], IC["stroke_u"][0])
    pre = U["kinds"]["iconset"]["icon_prefix"]; lo, hi = IC["live_u"]
    rep.info("icons.count", "%d glyphs" % len(G))
    for n, g in sorted(G.items()):
        canon = meta.get(n, {}).get("canonical", False)
        rep.fail("icon.name:" + n, bool(re.match(r"^[a-z][a-z0-9]*(_[a-z0-9]+)*$", n)) and (pre + n).startswith("ui_icon_"), pre + n)
        sp = os.path.join(d, s["icons"][n]["svg"]); svg = open(sp).read()
        tags = set(re.findall(r"<([a-z]+)", svg))
        rep.fail("icon.svg_whitelist:" + n, tags <= SVG_OK_TAGS and not SVG_BAD.search(svg) and set(re.findall(r'(?:fill|stroke)="([^"]+)"', svg)) <= {"none", "currentColor"},
                 "tags %s" % sorted(tags))
        rep.fail("icon.svg_bytes:" + n, len(svg) <= IC["max_svg_bytes"], "%d B" % len(svg))
        x0, y0, x1, y1 = g.extent()
        rep.fail("icon.live_area:" + n, x0 >= lo - 1e-6 and y0 >= lo - 1e-6 and x1 <= hi + 1e-6 and y1 <= hi + 1e-6, "ink %.1f,%.1f..%.1f,%.1f within %s..%s u" % (x0, y0, x1, y1, lo, hi))
        if not canon:
            off = [(kind, v) for kind, v in g.coords() for q in (v if isinstance(v, tuple) else (v,)) if abs(q / IC["snap_u"] - round(q / IC["snap_u"])) > 1e-6]
            rep.fail("icon.grid_snap:" + n, not off, "off-grid %s" % off[:4] if off else "all points, centres, radii on the %g u grid" % IC["snap_u"])
            rep.fail("icon.stroke:" + n, g.sw in IC["stroke_u"], "%g u" % g.sw)
            small = [it for it in g.items if it[0] == "dot" and it[1][2] * 2 < IC["min_feature_u"]] + [it for it in g.items if it[0] == "circle" and it[1][2] < 1]
            rep.fail("icon.min_feature:" + n, not small, "dots/circles below %g u: %s" % (IC["min_feature_u"], small[:2]) if small else "ok")
    # rasters
    px = IC["export_px"][0]; mn = IC["min_legible_px"]; ink = toks["color"][IC["variants"]["phos"]]
    masks16, part = {}, {}
    M16 = I.load_masters(os.path.join(P["dir"], P["brief"].get("glyphs", "glyphs.py")), mn) if hasattr(I, "load_masters") else {}
    if M16: rep.info("icons.masters_%dpx" % mn, "%d hand-hinted %d u masters: %s" % (len(M16), mn, ", ".join(sorted(M16))))
    for n, g in G.items():
        a24 = I.raster(g.svg(ink), px)[..., 3].astype(np.float32) / 255
        b = 2 * px // IC["grid_u"]
        ring = np.concatenate([a24[:b].ravel(), a24[-b:].ravel(), a24[:, :b].ravel(), a24[:, -b:].ravel()])
        rep.fail("icon.transparent_padding:" + n, ring.max() < 0.02, "%d px border clear at %d px (max alpha %.2f)" % (b, px, ring.max()))
        inked = a24 > 0.02; pf = float(((a24 > 0.02) & (a24 < 0.98)).sum()) / max(1, inked.sum()); part[n] = pf
        if not meta.get(n, {}).get("canonical"):
            rep.warn("icon.crisp_24:" + n, pf <= IC["max_partial_px_frac_24"], "%.0f%% of inked pixels are partial at %d px (diagonals and curves raise it)" % (pf * 100, px))
        a48 = I.raster(g.svg(ink), mn * 3)[..., 3].astype(np.float32) / 255
        ref = a48.reshape(mn, 3, mn, 3).mean((1, 3)) > 0.5
        m16 = M16.get(n)                                  # a hand-hinted master replaces the straight raster at this size
        a16f = I.raster((m16 or g).svg(ink), mn)[..., 3].astype(np.float32) / 255; a16 = a16f > 0.5
        iou = (ref & a16).sum() / max(1, (ref | a16).sum()); masks16[n] = a16
        if not m16:
            rep.fail("icon.legible_%dpx:" % mn + n, iou >= 0.6 and a16.sum() >= 6, "IoU %.2f vs area-downsampled master, %d px inked" % (iou, a16.sum()))
        else:   # hinting moves edges by up to 1 px on purpose (1.33 px strokes -> 1 px), so plain IoU punishes it: hold every pixel to within 1 px instead
            near = lambda a: np.pad(a, 1)[np.add.outer(np.arange(3), np.arange(mn))[:, None, :, None], np.add.outer(np.arange(3), np.arange(mn))[None, :, None, :]].any((0, 1))
            prec = (a16 & near(ref)).sum() / max(1, a16.sum()); rec = (ref & near(a16)).sum() / max(1, ref.sum())
            rep.fail("icon.legible_%dpx:" % mn + n, prec >= 0.95 and rec >= 0.95 and a16.sum() >= 6,
                     "hinted %d u master: %.0f%% of its pixels within 1 px of the area-downsampled 24 u glyph, %.0f%% of that glyph's pixels within 1 px of the master (IoU %.2f), %d px inked" % (mn, prec * 100, rec * 100, iou, a16.sum()))
        if m16:
            pf16 = float(((a16f > 0.02) & (a16f < 0.98)).sum()) / max(1, (a16f > 0.02).sum())
            rep.fail("icon.master%d_crisp:" % mn + n, pf16 <= 0.05, "%.0f%% of inked pixels partial in the hinted master" % (pf16 * 100))
            x0, y0, x1, y1 = m16.extent()
            rep.fail("icon.master%d_live_area:" % mn + n, min(x0, y0) >= 1 and max(x1, y1) <= mn - 1, "ink %.1f,%.1f..%.1f,%.1f within 1..%d px" % (x0, y0, x1, y1, mn - 1))
            sp16 = os.path.join(d, s["icons"][n].get("svg_%d" % mn, "")); svg16 = open(sp16).read() if os.path.isfile(sp16) else ""
            rep.fail("icon.master%d_svg:" % mn + n, bool(svg16) and set(re.findall(r"<([a-z]+)", svg16)) <= SVG_OK_TAGS and not SVG_BAD.search(svg16) and len(svg16) <= IC["max_svg_bytes"],
                     "%s exported, whitelisted, %d B" % (s["icons"][n].get("svg_%d" % mn, "svg_%d missing in set json" % mn), len(svg16)))
    # distinctness at the minimum size, family frames removed
    groups = {}
    for n in G: groups.setdefault(n.split("_")[0], []).append(n)
    common = {gname: np.logical_and.reduce([masks16[n] for n in ns]) if len(ns) > 2 else np.zeros_like(masks16[ns[0]]) for gname, ns in groups.items()}
    worst = []
    names = sorted(G)
    for i, a in enumerate(names):
        for b2 in names[i + 1:]:
            ma = masks16[a] & ~common[a.split("_")[0]]; mb = masks16[b2] & ~common[b2.split("_")[0]]
            iou = (ma & mb).sum() / max(1, (ma | mb).sum()); worst.append((iou, a, b2))
    worst.sort(reverse=True)
    var = lambda a, b2: meta.get(a, {}).get("variant_of") == b2 or meta.get(b2, {}).get("variant_of") == a
    for iou, a, b2 in [w for w in worst if var(w[1], w[2])]:
        rep.fail("icon.variant:%s~%s" % (a, b2), iou <= IC.get("variant_iou_max", 0.85), "IoU %.2f at %d px (declared variant; max %.2f)" % (iou, mn, IC.get("variant_iou_max", 0.85)))
    for iou, a, b2 in [w for w in worst if not var(w[1], w[2])][:5]:
        rep.fail("icon.distinct:%s~%s" % (a, b2), iou <= IC["distinct_iou_max"], "IoU %.2f at %d px (frames removed; max %.2f)" % (iou, mn, IC["distinct_iou_max"]))
    # shape class: interiors at a coarse 6x6 grid, cosine similarity (catches "box with an arch" pairs that IoU misses)
    def coarse(n):
        a = I.raster(G[n].svg(ink), 48)[..., 3].astype(np.float32) / 255
        fr = np.logical_and.reduce([I.raster(G[m].svg(ink), 48)[..., 3] > 127 for m in groups[n.split("_")[0]]]) if len(groups[n.split("_")[0]]) > 2 else np.zeros(a.shape, bool)
        a = a * ~fr; v = a.reshape(6, 8, 6, 8).mean((1, 3)).ravel(); return v / max(1e-6, np.linalg.norm(v))
    cv = {n: coarse(n) for n in names}; sims = []
    for i, a in enumerate(names):
        for b2 in names[i + 1:]:
            if not var(a, b2): sims.append((float(cv[a] @ cv[b2]), a, b2))
    sims.sort(reverse=True)
    for sim, a, b2 in sims[:5]:   # mass distribution is a weak signal (any centred blob scores ~0.9): a list to eyeball, not a gate
        rep.info("icon.shape_class:%s~%s" % (a, b2), "coarse interior similarity %.2f: look at this pair side by side at %d px" % (sim, mn))
    # aliases
    bad = {k: v for k, v in aliases.items() if v not in G}
    rep.fail("icon.aliases", not bad, "unknown targets %s" % bad if bad else "%d sim names map to glyphs" % len(aliases))
    # atlases
    A = U["atlas"]
    bgs = {"phos": toks["color"]["screen"], "ink": toks["color"]["paper"]}
    bgs.update({v: toks["color"][t] for v, t in P["brief"].get("variant_bg", {}).items()})   # set-declared variants name their ground
    for var, dens in s["atlases"].items():
        tok = toks["color"][dens["1"]["color"]]
        c = L.contrast(tok, bgs.get(var, toks["color"]["screen"]))
        bgn = P["brief"].get("variant_bg", {}).get(var) or ("screen" if var == "phos" else "paper" if var == "ink" else "screen")
        rep.fail("icon.contrast:" + var, c >= U["contrast"]["icon_min"], "%s on %s = %.2f" % (dens["1"]["color"], bgn, c))
        for dd, at in dens.items():
            p = os.path.join(d, at["image"]); im = _png(p); h, w = im.shape[:2]
            rep.fail("atlas.pow2:%s@%s" % (var, dd), (w & (w - 1)) == 0 and (h & (h - 1)) == 0 and max(w, h) <= A["max_px"], "%dx%d" % (w, h))
            rep.fail("atlas.bytes:%s@%s" % (var, dd), os.path.getsize(p) <= A["max_png_bytes"], "%d B" % os.path.getsize(p))
            op = im[..., 3] > 8; rgb = im[..., :3][op].astype(np.float32) / 255
            dev = np.abs(rgb - np.array(L.hex_rgb(tok))).max() if len(rgb) else 0
            rep.fail("atlas.colour:%s@%s" % (var, dd), dev <= 0.02, "all inked pixels = %s (max dev %.3f; straight alpha)" % (tok, dev))
            pad = A["pad_px"] * (1 if dd.endswith("px") else int(dd)); leak = 0
            for fr in at["frames"].values():
                x, y, fw, fh = fr["x"], fr["y"], fr["w"], fr["h"]
                ring = im[max(0, y - pad):y + fh + pad, max(0, x - pad):x + fw + pad, 3].copy(); ring[pad:pad + fh, pad:pad + fw] = 0
                leak += int((ring > 0).sum())
            rep.fail("atlas.padding:%s@%s" % (var, dd), leak == 0, "%d px of %d px gutters inked" % (leak, pad))
            missing = set(G) - set(at["frames"])
            rep.fail("atlas.frames:%s@%s" % (var, dd), not missing, "missing %s" % sorted(missing) if missing else "%d frames" % len(at["frames"]))
    return {"glyphs": len(G), "most_similar": [(round(i, 2), a, b) for i, a, b in worst[:3]], "partial_frac_max": round(max(part.values()), 2)}


# --------------------------------------------------------------------------- nine-slice panels and templates

def _copy_words(P):
    cp = os.path.join(P["root"], P["spec"]["paths"]["src"], "iconography", "copy.md")
    return open(cp).read() if os.path.exists(cp) else ""


def check_nine(P, n, rep):
    spec = P["spec"]; U = spec["ui"]; PN = U["panel"]; d = os.path.dirname(P["json"])
    toks = L.load_tokens(P["root"], spec)
    rep.fail("nine.format", n.get("format") in ("ig_ui_nine", "ig_ui_layout"), n.get("format"))
    w, h = n["size"]; s = n["slice"]
    rep.fail("nine.slice_min", min(s.values()) >= PN["min_slice_px"], str(s))
    rep.fail("nine.slice_fits", s["l"] + s["r"] < w and s["t"] + s["b"] < h, "l+r %d < %d, t+b %d < %d" % (s["l"] + s["r"], w, s["t"] + s["b"], h))
    rep.fail("nine.modes", n["center"] in PN["center_modes"] and n["edges"] in PN["edge_modes"], "%s / %s" % (n["center"], n["edges"]))
    allowed = L.palette_hexes(spec, toks)
    ims = {}
    for dd in [str(x) for x in PN["densities"]]:
        p = os.path.join(d, n["images"].get(dd, "")); ok = os.path.exists(p)
        rep.fail("nine.image@%sx" % dd, ok, n["images"].get(dd, "missing"))
        if not ok: continue
        im = _png(p); ims[dd] = im; k = int(dd)
        rep.fail("nine.size@%sx" % dd, im.shape[1] == w * k and im.shape[0] == h * k, "%dx%d (want %dx%d)" % (im.shape[1], im.shape[0], w * k, h * k))
        rep.fail("nine.bytes@%sx" % dd, os.path.getsize(p) <= PN["max_png_bytes"], "%d B" % os.path.getsize(p))
        l, t, r, b = s["l"] * k, s["t"] * k, s["r"] * k, s["b"] * k
        C = im[t:im.shape[0] - b, l:im.shape[1] - r].astype(np.float32) / 255
        if n["center"] == "hollow":
            rep.fail("nine.hollow@%sx" % dd, C[..., 3].max() < 0.01, "centre alpha max %.2f" % C[..., 3].max())
        regions = []
        if n["center"] == "tile": regions.append(("centre", C, "xy"))
        if n["edges"] == "tile":
            regions += [("top", im[:t, l:im.shape[1] - r].astype(np.float32) / 255, "x"), ("bottom", im[im.shape[0] - b:, l:im.shape[1] - r].astype(np.float32) / 255, "x"),
                        ("left", im[t:im.shape[0] - b, :l].astype(np.float32) / 255, "y"), ("right", im[t:im.shape[0] - b, im.shape[1] - r:].astype(np.float32) / 255, "y")]
        for name, R, ax in regions:
            if name != "centre" and R.size:
                # an edge tiles along its axis: anything printed in it must be uniform along that axis (else it repeats)
                prof = (R[..., :3].mean(2) * R[..., 3]).mean(0 if ax == "x" else 1)
                spike = float(np.abs(prof - np.median(prof)).max())
                if name in P["brief"].get("edge_repeat_ok", []): rep.info("nine.edge_uniform:%s@%sx" % (name, dd), "deviation %.3f; repeat declared intended in the brief (edge_repeat_ok)" % spike)
                else: rep.warn("nine.edge_uniform:%s@%sx" % (name, dd), spike <= 0.06, "max deviation of the %s profile along the tile axis %.3f (a feature here repeats every tile)" % (name, spike))
            for a in ax:
                if a == "x": seam = np.abs(R[:, -1] - R[:, 0]).mean(); typ = np.abs(R[:, 1:] - R[:, :-1]).mean()
                else: seam = np.abs(R[-1] - R[0]).mean(); typ = np.abs(R[1:] - R[:-1]).mean()
                rep.fail("nine.tile_seam:%s.%s@%sx" % (name, a, dd), seam <= typ + PN["seam_tol"], "wrap diff %.3f vs neighbour diff %.3f" % (seam, typ))
        pr = L.pixel_palette_report(im, spec, allowed)
        rep.fail("palette.no_pure@%sx" % dd, pr["pure"] == 0, "%d near-pure black/white pixels" % pr["pure"])
        rep.fail("palette.saturation@%sx" % dd, pr["sat_frac"] <= 0.01, "%.1f%% pixels over the saturation cap outside accent hues" % (pr["sat_frac"] * 100))
        rep.fail("palette.mix_hull@%sx" % dd, pr["off_frac"] <= 0.02, "%.1f%% pixels further than %.2f from any mix of two palette tokens (max %.3f)" % (pr["off_frac"] * 100, U["palette"]["pixel_tolerance"], pr["max_dist"]))
    if "1" in ims and "2" in ims:
        a = ims["1"].astype(np.float32) / 255; b2 = ims["2"].astype(np.float32).reshape(a.shape[0], 2, a.shape[1], 2, 4).mean((1, 3)) / 255
        drift = np.abs(a - b2).mean(); rep.fail("nine.density_drift", drift <= 0.06, "@2x downsampled vs @1x mean diff %.3f" % drift)
    styles = toks["type"]["scale"]; roles = toks["roles"]
    for sl in n.get("slots", []):
        x, y, sw, sh = sl["rect"]
        rep.fail("slot.inside:" + sl["id"], x >= 0 and y >= 0 and x + sw <= w and y + sh <= h, "%s in %dx%d" % (sl["rect"], w, h))
        if sl.get("style"): rep.fail("slot.style:" + sl["id"], sl["style"] in styles, sl["style"])
        if sl.get("role"): rep.fail("slot.role:" + sl["id"], sl["role"] in roles, sl["role"])
        if sl.get("kind") == "text" and sl.get("style"):
            st = styles[sl["style"]]; rep.fail("slot.fits_line:" + sl["id"], sh >= st["size"] * st["line"] - 0.5, "h %d >= %.1f" % (sh, st["size"] * st["line"]))
    words = _copy_words(P)
    for bt in P["brief"].get("baked_text", []):
        rep.fail("copy.baked:" + bt[:24], bt in words, "%r %s copy.md" % (bt, "in" if bt in words else "NOT in"))
    cpath = os.path.join(P["root"], spec["paths"]["src"], "ui", "review_copy.json")
    ctab = json.load(open(cpath)).get("labels", {}) if os.path.exists(cpath) else {}
    for sl in n.get("slots", []):
        key = sl.get("label_key")
        if not key: continue
        e = ctab.get(key)
        if e is None: rep.fail("copy.key:" + key, False, "label key not in src/ui/review_copy.json")
        else: rep.warn("copy.approved:" + key, e.get("status") == "approved", "%r (%s)" % (e.get("en"), e.get("status")))
    return {"size": n["size"], "slice": s, "slots": len(n.get("slots", []))}


# --------------------------------------------------------------------------- shared slot + copy checks (gauge, map)

def _slots_and_copy(P, n, rep, toks, size=None):
    styles = toks["type"]["scale"]; roles = toks["roles"]
    for sl in n.get("slots", []):
        if size and sl.get("rect"):
            x, y, sw, sh = sl["rect"]; w, h = size
            rep.fail("slot.inside:" + sl["id"], x >= 0 and y >= 0 and x + sw <= w and y + sh <= h, "%s in %dx%d" % (sl["rect"], w, h))
            if sl.get("kind") == "text" and sl.get("style") in styles:
                st = styles[sl["style"]]; rep.fail("slot.fits_line:" + sl["id"], sh >= st["size"] * st["line"] - 0.5, "h %d >= %.1f" % (sh, st["size"] * st["line"]))
        if sl.get("style"): rep.fail("slot.style:" + sl["id"], sl["style"] in styles, sl["style"])
        if sl.get("role"): rep.fail("slot.role:" + sl["id"], sl["role"] in roles, sl["role"])
    words = _copy_words(P)
    for bt in P["brief"].get("baked_text", []):
        rep.fail("copy.baked:" + bt[:24], bt in words, "%r %s copy.md" % (bt, "in" if bt in words else "NOT in"))
    cpath = os.path.join(P["root"], P["spec"]["paths"]["src"], "ui", "review_copy.json")
    ctab = json.load(open(cpath)).get("labels", {}) if os.path.exists(cpath) else {}
    keys = [sl.get("label_key") for sl in n.get("slots", [])] + [b.get("label_key") for b in n.get("bands", [])] + [l.get("label_key") for l in n.get("legend", [])]
    for key in sorted(set(k for k in keys if k)):
        e = ctab.get(key)
        if e is None: rep.fail("copy.key:" + key, False, "label key not in src/ui/review_copy.json")
        else: rep.warn("copy.approved:" + key, e.get("status") == "approved", "%r (%s)" % (e.get("en"), e.get("status")))


# --------------------------------------------------------------------------- gauge (ig_ui_gauge): layered instrument art + a fill/needle drive

def check_gauge(P, g, rep):
    """Layers per density at size x d and inside the palette; the drive (segment fill or needle) inside the art; bands
    ascending, contiguous, covering the value range, on segment boundaries, and equal to the thresholds in the sim
    source the JSON names (re-read here, so a tuning change fails the gauge instead of silently drifting)."""
    spec = P["spec"]; PN = spec["ui"]["panel"]; d = os.path.dirname(P["json"]); toks = L.load_tokens(P["root"], spec)
    rep.fail("gauge.format", g.get("format") == "ig_ui_gauge", g.get("format"))
    w, h = g["size"]; allowed = L.palette_hexes(spec, toks); ims = {}
    for ly in g["layers"]:
        for dd in [str(x) for x in PN["densities"]]:
            p = os.path.join(d, ly["images"].get(dd, "")); ok = os.path.exists(p)
            rep.fail("gauge.image:%s@%sx" % (ly["id"], dd), ok, ly["images"].get(dd, "missing"))
            if not ok: continue
            im = _png(p); k = int(dd); ims[(ly["id"], dd)] = im
            rep.fail("gauge.size:%s@%sx" % (ly["id"], dd), im.shape[1] == w * k and im.shape[0] == h * k, "%dx%d (want %dx%d)" % (im.shape[1], im.shape[0], w * k, h * k))
            rep.fail("gauge.bytes:%s@%sx" % (ly["id"], dd), os.path.getsize(p) <= PN["max_png_bytes"], "%d B" % os.path.getsize(p))
            pr = L.pixel_palette_report(im, spec, allowed)
            rep.fail("palette.no_pure:%s@%sx" % (ly["id"], dd), pr["pure"] == 0, "%d near-pure pixels" % pr["pure"])
            rep.fail("palette.saturation:%s@%sx" % (ly["id"], dd), pr.get("sat_frac", 0) <= 0.01, "%.1f%% over the cap outside accent hues" % (pr.get("sat_frac", 0) * 100))
            rep.fail("palette.mix_hull:%s@%sx" % (ly["id"], dd), pr.get("off_frac", 0) <= 0.02, "%.1f%% off the palette mixes (max %.3f)" % (pr.get("off_frac", 0) * 100, pr.get("max_dist") or 0))
        if (ly["id"], "1") in ims and (ly["id"], "2") in ims:
            a = ims[(ly["id"], "1")].astype(np.float32) / 255; b2 = ims[(ly["id"], "2")].astype(np.float32).reshape(a.shape[0], 2, a.shape[1], 2, 4).mean((1, 3)) / 255
            rep.fail("gauge.density_drift:" + ly["id"], np.abs(a - b2).mean() <= 0.06, "mean diff %.3f" % np.abs(a - b2).mean())
    lo, hi = g["value_range"]
    F = g.get("fill")
    if F:
        x, y, fw, fh = F["rect"]; seg = F["segments"]; n = seg["count"]; per = (hi - lo) / n
        rep.fail("gauge.fill_inside", x >= 0 and y >= 0 and x + fw <= w and y + fh <= h, "%s in %dx%d" % (F["rect"], w, h))
        rects = [[seg["xs"][i] if seg.get("xs") else seg["x0"] + i * seg["pitch_px"], seg.get("y", y), seg.get("w_px", seg.get("pitch_px", 0) - seg.get("gap_px", 0)), seg.get("h", fh)] for i in range(n)]   # xs: explicit per-segment x (wider gaps at band edges)
        rep.fail("gauge.segments_fit", rects[0][0] >= x and rects[-1][0] + rects[-1][2] <= x + fw, "segments %d..%d px in fill %d..%d" % (rects[0][0], rects[-1][0] + rects[-1][2], x, x + fw))
        lit = ims.get((F["layer"], "1"))
        if lit is not None:
            A = lit[..., 3].astype(np.float32) / 255; inside = np.zeros_like(A, bool)
            for rx, ry, rw, rh in rects: inside[int(ry):int(ry + rh), int(rx):int(rx + rw)] = True
            lit_ok = all(A[int(ry):int(ry + rh), int(rx):int(rx + rw)].mean() > 0.5 for rx, ry, rw, rh in rects)
            rep.fail("gauge.lit_segments", lit_ok, "every segment of the %s layer is lit inside its rect" % F["layer"])
            rep.fail("gauge.lit_clean", A[~inside].max(initial=0) < 0.02, "lit layer alpha outside segments max %.2f (a clip at a segment edge must not cut a glow)" % A[~inside].max(initial=0))
        for b in g.get("bands", []):
            for v in (b["from"], b["to"]):
                rep.fail("gauge.band_on_segment:%s" % b["id"], abs((v - lo) / per - round((v - lo) / per)) < 1e-6, "%s on a %.1f-unit segment boundary" % (v, per))
    N = g.get("needle")
    if N:
        px, py = N["pivot"]; rep.fail("gauge.pivot_inside", 0 <= px <= w and 0 <= py <= h, str(N["pivot"]))
        rep.fail("gauge.needle_range", N["angle_deg"][0] < N["angle_deg"][1], str(N["angle_deg"]))
    bands = g.get("bands", [])
    rep.fail("gauge.bands_cover", bool(bands) and bands[0]["from"] == lo and bands[-1]["to"] == hi and all(bands[i]["to"] == bands[i + 1]["from"] for i in range(len(bands) - 1)), " | ".join("%s %s-%s" % (b["id"], b["from"], b["to"]) for b in bands))
    src = g.get("source") or {}
    if src.get("file") and src.get("regex"):
        fp = os.path.join(os.path.dirname(P["root"]), src["file"])
        if not os.path.exists(fp): rep.fail("gauge.source", False, "missing %s" % src["file"])
        else:
            m = re.search(src["regex"], open(fp).read())
            got = [float(x) for x in re.findall(r"[0-9.]+", m.group(1))] if m else None
            want = [b["from"] for b in bands[1:]]
            rep.fail("gauge.source_thresholds", got == want, "%s says %s; bands start at %s" % (src["file"], got, want))
    _slots_and_copy(P, g, rep, toks, (w, h))
    return {"size": g["size"], "layers": [l["id"] for l in g["layers"]], "bands": [(b["from"], b["to"]) for b in bands]}


# --------------------------------------------------------------------------- map style (ig_ui_mapstyle): tokens + line styles for map layers

def check_map(P, m, rep):
    """Every colour a token, widths and dashes sane, markers and labels readable on the screen (opacity included), z
    order unique, icons present in the set, label styles and roles from the tokens."""
    spec = P["spec"]; U = spec["ui"]; toks = L.load_tokens(P["root"], spec); C = toks["color"]; CT = U["contrast"]
    rep.fail("map.format", m.get("format") == "ig_ui_mapstyle", m.get("format"))
    bg = C.get(m.get("background", "screen"), C["screen"])
    sets = {}
    for f in os.listdir(os.path.join(P["root"], spec["paths"]["export"], U["kinds"]["iconset"]["export_dir"])):
        if f.endswith(".json"): sets[f[:-5]] = json.load(open(os.path.join(P["root"], spec["paths"]["export"], U["kinds"]["iconset"]["export_dir"], f)))
    zs = [l["z"] for l in m["layers"]]
    rep.fail("map.z_unique", len(zs) == len(set(zs)), "z order %s" % sorted(zs))
    def paint(lid, st, what):
        col = st.get("color"); op = float(st.get("opacity", 1.0))
        rep.fail("map.color:%s.%s" % (lid, what), col in C, "%s is %s" % (col, "a token" if col in C else "NOT a ui_tokens colour"))
        rep.fail("map.opacity:%s.%s" % (lid, what), 0 < op <= 1, str(op))
        return L.mix(bg, C[col], op) if col in C else None
    for ly in m["layers"]:
        lid = ly["id"]
        for state, S in [("", ly)] + [("." + k, dict(ly, **v)) for k, v in ly.get("states", {}).items()]:
            tag = lid + state
            if "stroke" in S and S["stroke"]:
                st = S["stroke"]; eff = paint(tag, st, "stroke")
                wpx = st.get("width_px", 1); rep.fail("map.width:" + tag, 1 <= wpx <= 4, "%s px (hairline 1 .. 4)" % wpx)
                dash = st.get("dash") or []
                rep.fail("map.dash:" + tag, len(dash) % 2 == 0 and all(x > 0 for x in dash), str(dash))
                if eff and S.get("use") in ("line", "marker"):
                    c = L.contrast(eff, bg); rep.fail("map.contrast:" + tag, c >= CT["icon_min"], "%.2f:1 on %s (min %.1f for %s)" % (c, bg, CT["icon_min"], S.get("use")))
                elif eff: rep.info("map.contrast:" + tag, "%.2f:1 (%s: exempt)" % (L.contrast(eff, bg), S.get("use", "decor")))
            if "fill" in S and S["fill"]: paint(tag, S["fill"], "fill")
            if S.get("label"):
                lb = S["label"]; sty = toks["type"]["scale"].get(lb.get("style")); rep.fail("map.label_style:" + tag, sty is not None, str(lb.get("style")))
                ro = toks["roles"].get(lb.get("role")); rep.fail("map.label_role:" + tag, ro is not None, str(lb.get("role")))
                if ro:
                    eff = L.mix(bg, ro["fg_hex"], float(lb.get("opacity", 1.0))); c = L.contrast(eff, bg)
                    rep.fail("map.label_contrast:" + tag, c >= CT["text_min"], "%.2f:1 at opacity %s" % (c, lb.get("opacity", 1.0)))
            if S.get("icon"):
                ic = S["icon"]; ok = False
                for sid, so in sets.items():
                    al = ic.get("alias", ""); names = [so.get("aliases", {}).get(al, ic.get("name"))]
                    if "<" in al: names = [v for k2, v in so.get("aliases", {}).items() if k2.startswith(al.split("<")[0])]   # alias:<type> = the family
                    if names and all(nm in so.get("icons", {}) for nm in names): ok = True
                rep.fail("map.icon:" + tag, ok, str(ic))
                if ic.get("color"):
                    eff = paint(tag, ic, "icon"); c = L.contrast(eff, bg) if eff else 0
                    rep.fail("map.icon_contrast:" + tag, c >= CT["icon_min"], "%.2f:1" % c)
    _slots_and_copy(P, m, rep, toks)
    return {"layers": len(m["layers"]), "colours": sorted(set(m["colors"].values() if isinstance(m.get("colors"), dict) else m.get("colors", [])))}


# --------------------------------------------------------------------------- hud (optional kind ui_hud_*: in-world/overlay HUD art)

def check_hud(P, h, rep):
    """ig_ui_nine / ig_ui_layout -> check_nine; ig_ui_hud (layers of sprites + parameters) -> files, sizes, palette, slots, copy."""
    if h.get("format") in ("ig_ui_nine", "ig_ui_layout"):
        summ = check_nine(P, h, rep)
    else:
        spec = P["spec"]; d = os.path.dirname(P["json"]); toks = L.load_tokens(P["root"], spec); allowed = L.palette_hexes(spec, toks)
        rep.fail("hud.format", h.get("format") == "ig_ui_hud", h.get("format"))
        cap = spec["ui"]["panel"]["max_png_bytes"]
        for ly in h.get("layers", []):
            w, hh = ly["size"]; ims = {}
            for dd, fn in ly["images"].items():
                p = os.path.join(d, fn); ok = os.path.exists(p); rep.fail("hud.image:%s@%sx" % (ly["id"], dd), ok, fn)
                if not ok: continue
                im = _png(p); ims[dd] = im; k = int(dd)
                rep.fail("hud.size:%s@%sx" % (ly["id"], dd), im.shape[1] == w * k and im.shape[0] == hh * k, "%dx%d (want %dx%d)" % (im.shape[1], im.shape[0], w * k, hh * k))
                rep.fail("hud.bytes:%s@%sx" % (ly["id"], dd), os.path.getsize(p) <= cap, "%d B" % os.path.getsize(p))
                pr = L.pixel_palette_report(im, spec, allowed)
                rep.fail("palette.no_pure:%s@%sx" % (ly["id"], dd), pr["pure"] == 0, "%d near-pure pixels" % pr["pure"])
                rep.fail("palette.mix_hull:%s@%sx" % (ly["id"], dd), pr["off_frac"] <= 0.02, "%.1f%% off the two-token mixes (max %.3f)" % (pr["off_frac"] * 100, pr["max_dist"]))
                rep.fail("palette.saturation:%s@%sx" % (ly["id"], dd), pr["sat_frac"] <= 0.01, "%.1f%% over the saturation cap" % (pr["sat_frac"] * 100))
                if ly.get("tile_x"):
                    A = im.astype(np.float32) / 255; seam = np.abs(A[:, -1] - A[:, 0]).mean(); typ = np.abs(A[:, 1:] - A[:, :-1]).mean()
                    rep.fail("hud.tile_seam_x:%s@%sx" % (ly["id"], dd), seam <= typ + spec["ui"]["panel"]["seam_tol"], "wrap diff %.3f vs neighbour %.3f" % (seam, typ))
            if "1" in ims and "2" in ims:
                a = ims["1"].astype(np.float32) / 255; b2 = ims["2"].astype(np.float32).reshape(a.shape[0], 2, a.shape[1], 2, 4).mean((1, 3)) / 255
                drift = np.abs(a - b2).mean(); rep.fail("hud.density_drift:" + ly["id"], drift <= 0.06, "@2x downsampled vs @1x mean diff %.3f" % drift)
        summ = {"layers": len(h.get("layers", [])), "slots": len(h.get("slots", []))}
        _slots_and_copy(P, h, rep, toks, tuple(h["size"]) if h.get("size") else None)
    for key, v in (h.get("params") or {}).items():
        rep.info("hud.param:" + key, json.dumps(v)[:160])
    return summ
