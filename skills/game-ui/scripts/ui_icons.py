"""
ui_icons.py -- the icon DSL, SVG writer, rasteriser (resvg) and atlas packer for game-ui.

Glyphs are authored in grid units (spec ui.icon.grid_u, 24) with y down, as a list of primitives. Every primitive
is a monoline stroke of the spec width (2 u) or a solid fill; coordinates snap to the grid so stroke edges land
on whole pixels at 24 px and 48 px. A glyph module (in the project, e.g. assets/src/ui/icons/<set>/glyphs.py)
defines GLYPHS = {name: fn(g)} and optional META = {name: {"group": ..., "means": ...}}.

    def tool_camera(g):
        g.rect(4, 6, 12, 6)                 # x, y, w, h (stroke)
        g.poly([(16, 7), (20, 5), (20, 13), (16, 11)], closed=True)
        g.line(10, 12, 10, 19); g.line(6, 19, 14, 19)
        g.dot(7, 9, 1)                      # filled circle, radius in u (>= min_feature_u / 2)

Primitives: line, poly (open/closed), rect, circle, arc (centre, r, a0, a1 deg, clockwise from +x, y down),
fill (polygon), dot, fill_rect. Nothing else (no text, gradients, opacity, transforms): S10 says no icons with gradients.
"""
import io, math, os

SW = 2  # default stroke (u); validated against spec ui.icon.stroke_u


def _f(v):
    s = ("%.3f" % v).rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


class Glyph:
    def __init__(self, name, grid=24, stroke=SW):
        self.name = name; self.grid = grid; self.sw = stroke; self.items = []

    # strokes
    def line(self, x1, y1, x2, y2): self.items.append(("poly", [(x1, y1), (x2, y2)], False)); return self
    def poly(self, pts, closed=False): self.items.append(("poly", [tuple(p) for p in pts], closed)); return self
    def rect(self, x, y, w, h): return self.poly([(x, y), (x + w, y), (x + w, y + h), (x, y + h)], closed=True)
    def circle(self, cx, cy, r): self.items.append(("circle", (cx, cy, r))); return self
    def arc(self, cx, cy, r, a0, a1): self.items.append(("arc", (cx, cy, r, a0, a1))); return self
    # fills
    def fill(self, pts): self.items.append(("fill", [tuple(p) for p in pts])); return self
    def fill_rect(self, x, y, w, h): return self.fill([(x, y), (x + w, y), (x + w, y + h), (x, y + h)])
    def dot(self, cx, cy, r): self.items.append(("dot", (cx, cy, r))); return self
    def ring(self, cx, cy, ro, ri): self.items.append(("ring", (cx, cy, ro, ri))); return self   # filled annulus (canonical marks only)

    # ------------------------------------------------------------------ svg
    def svg(self, color="currentColor", px=None):
        g = self.grid; size = ' width="%d" height="%d"' % (px, px) if px else ""
        out = ['<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %d %d"%s fill="none" stroke="%s" stroke-width="%s" stroke-linecap="butt" stroke-linejoin="miter" stroke-miterlimit="4">' % (g, g, size, color, _f(self.sw))]
        for it in self.items:
            k = it[0]
            if k == "poly":
                pts, closed = it[1], it[2]
                d = "M" + " L".join("%s %s" % (_f(x), _f(y)) for x, y in pts) + (" Z" if closed else "")
                out.append('<path d="%s"/>' % d)
            elif k == "circle":
                cx, cy, r = it[1]; out.append('<circle cx="%s" cy="%s" r="%s"/>' % (_f(cx), _f(cy), _f(r)))
            elif k == "arc":
                cx, cy, r, a0, a1 = it[1]
                p0 = (cx + r * math.cos(math.radians(a0)), cy + r * math.sin(math.radians(a0)))
                p1 = (cx + r * math.cos(math.radians(a1)), cy + r * math.sin(math.radians(a1)))
                large = 1 if ((a1 - a0) % 360) > 180 else 0
                out.append('<path d="M%s %s A%s %s 0 %d 1 %s %s"/>' % (_f(p0[0]), _f(p0[1]), _f(r), _f(r), large, _f(p1[0]), _f(p1[1])))
            elif k == "fill":
                d = "M" + " L".join("%s %s" % (_f(x), _f(y)) for x, y in it[1]) + " Z"
                out.append('<path d="%s" fill="%s" stroke="none"/>' % (d, color))
            elif k == "dot":
                cx, cy, r = it[1]; out.append('<circle cx="%s" cy="%s" r="%s" fill="%s" stroke="none"/>' % (_f(cx), _f(cy), _f(r), color))
            elif k == "ring":
                cx, cy, ro, ri = it[1]
                d = "".join("M%s %s A%s %s 0 1 0 %s %s A%s %s 0 1 0 %s %s Z" % (_f(cx + r), _f(cy), _f(r), _f(r), _f(cx - r), _f(cy), _f(r), _f(r), _f(cx + r), _f(cy)) for r in (ro, ri))
                out.append('<path d="%s" fill="%s" fill-rule="evenodd" stroke="none"/>' % (d, color))
        out.append("</svg>")
        return "".join(out)

    # ------------------------------------------------------------------ geometry for the validator
    def coords(self):
        """Every authored number that must sit on the grid: points, centres, radii, arc end points."""
        out = []
        for it in self.items:
            k = it[0]
            if k in ("poly", "fill"): out += [("pt", p) for p in it[1]]
            elif k in ("circle", "dot"): out += [("pt", it[1][:2]), ("r", it[1][2])]
            elif k == "ring": out += [("pt", it[1][:2]), ("r", it[1][2]), ("r", it[1][3])]
            elif k == "arc":
                cx, cy, r, a0, a1 = it[1]; out += [("pt", (cx, cy)), ("r", r)]
        return out

    def extent(self):
        """Bounding box of the inked area in u (stroke half-width included; miter spikes approximated by the join points)."""
        xs, ys = [], []; h = self.sw / 2
        for it in self.items:
            k = it[0]
            if k == "poly":
                pts, closed = it[1], it[2]
                for x, y in pts: xs += [x - h, x + h]; ys += [y - h, y + h]
                n = len(pts)
                for i in range(n):                       # miter spikes at joins (limit 4, then bevel)
                    if not closed and i in (0, n - 1): continue
                    p0, p1, p2 = pts[i - 1], pts[i], pts[(i + 1) % n]
                    a = (p0[0] - p1[0], p0[1] - p1[1]); b = (p2[0] - p1[0], p2[1] - p1[1])
                    la, lb = math.hypot(*a), math.hypot(*b)
                    if la == 0 or lb == 0: continue
                    a = (a[0] / la, a[1] / la); b = (b[0] / lb, b[1] / lb)
                    cos_t = max(-1.0, min(1.0, a[0] * b[0] + a[1] * b[1])); th = math.acos(cos_t)
                    if th < 1e-6 or th > math.pi - 1e-6: continue
                    m = 1 / math.sin(th / 2)
                    if m > 4: continue
                    bis = (-(a[0] + b[0]), -(a[1] + b[1])); lbis = math.hypot(*bis)
                    xs.append(p1[0] + bis[0] / lbis * h * m); ys.append(p1[1] + bis[1] / lbis * h * m)
            elif k == "fill":
                for x, y in it[1]: xs.append(x); ys.append(y)
            elif k in ("circle", "arc"):
                cx, cy, r = it[1][:3]; xs += [cx - r - h, cx + r + h]; ys += [cy - r - h, cy + r + h]
            elif k in ("dot", "ring"):
                cx, cy, r = it[1][:3]; xs += [cx - r, cx + r]; ys += [cy - r, cy + r]
        return (min(xs), min(ys), max(xs), max(ys)) if xs else (0, 0, 0, 0)


def load_glyphs(module_path, grid=24, stroke=SW):
    import importlib.util
    spec = importlib.util.spec_from_file_location("glyphs_%d" % abs(hash(module_path)), module_path)
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    out = {}
    for name, fn in m.GLYPHS.items():
        g = Glyph(name, grid, stroke); fn(g); out[name] = g
    return out, getattr(m, "META", {}), getattr(m, "ALIASES", {})


def load_masters(module_path, px=16):
    """Optional hand-hinted small-size masters: a glyph module may define GLYPHS16 = {name: fn(g)} drawn on a px-unit
    grid (1 u = 1 px, stroke 1 u): draw with fill_rect/fill on integers (or strokes centred on .5) so every edge is a
    pixel edge. Used for the "<px>px" atlas and exported as ui_icon_<name>@<px>.svg. {} if the module has none."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("glyphs%d_%d" % (px, abs(hash(module_path))), module_path)
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    out = {}
    for name, fn in getattr(m, "GLYPHS%d" % px, {}).items():
        g = Glyph(name, px, 1); fn(g); out[name] = g
    return out


def raster(svg, px):
    """RGBA uint8 array via resvg (deterministic, no browser)."""
    import numpy as np, resvg_py
    from PIL import Image
    b = resvg_py.svg_to_bytes(svg_string=svg, width=px, height=px)
    return np.array(Image.open(io.BytesIO(bytes(b))).convert("RGBA"))


def pack_atlas(images, cell, pad, pow2=True, max_px=1024):
    """Fixed-cell grid packer: images {name: HxWx4} all cell x cell. Returns (atlas, frames)."""
    import numpy as np
    n = len(images); step = cell + pad * 2
    best = None
    for c in range(1, n + 1):
        r = int(math.ceil(n / c)); w, h = c * step, r * step
        w2 = 1 << (w - 1).bit_length() if pow2 else w; h2 = 1 << (h - 1).bit_length() if pow2 else h
        if w2 > max_px or h2 > max_px: continue
        key = (w2 * h2, abs(w2 - h2))
        if best is None or key < best[0]: best = (key, c, w2, h2)
    if best is None: raise SystemExit("atlas exceeds %d px: split the set or raise atlas.max_px" % max_px)
    _, cols, W2, H2 = best
    atlas = np.zeros((H2, W2, 4), np.uint8); frames = {}
    for i, (name, im) in enumerate(sorted(images.items())):
        r, c = divmod(i, cols); x, y = c * step + pad, r * step + pad
        atlas[y:y + cell, x:x + cell] = im; frames[name] = {"x": x, "y": y, "w": cell, "h": cell}
    return atlas, frames


def save_png(arr, path):
    from PIL import Image
    os.makedirs(os.path.dirname(path), exist_ok=True)
    Image.fromarray(arr).save(path, optimize=True)
