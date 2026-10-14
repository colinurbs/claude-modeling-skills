"""
ui_panels.py -- numpy painters for game-ui nine-slice panels and form templates.

Everything is painted at the target density directly (no upscaling): a builder calls paint(d) once per density
in spec ui.panel.densities, in CSS pixels x d. Grain and fibres come from a P-periodic field (P = spec
ui.panel.tile_period_px x d), so any P-wide window of the centre or an edge repeats seamlessly; printed
elements go in the corners, or run uniformly along an edge (rules) so tiling never shows.

    from ui_panels import Canvas
    c = Canvas(w, h, d, seed)                          # CSS px; d = density
    c.fill(hex); c.grain(hex_a, hex_b, amount, period) # periodic two-colour grain (paper fibre, painted steel)
    c.rule(x0, y0, x1, y1, hex, w=1, ink=0.9)          # printed rule with uneven ink and optional misregistration
    c.box(x, y, w, h, hex, w=1); c.fill_rect(...); c.clear_rect(...) (hollow centre), c.text(...)
    c.icon(svg, x, y, px, hex)                         # an icon from the set, printed
    c.torn_edge('bottom', depth, period)               # periodic torn paper edge (alpha)
    c.save(path)

Colours are hex strings that must be palette tokens (the validator checks the pixels).
"""
import math, os
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))


def hex_rgb(h):
    h = h.lstrip("#"); return np.array([int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4)], np.float32)


def periodic_noise(h, w, period, octaves=4, seed=0, aniso=(1.0, 1.0)):
    """Sum of sinusoids on the integer lattice of the period: exactly periodic in x and y with `period` px."""
    rs = np.random.RandomState(seed)
    y, x = np.mgrid[0:h, 0:w].astype(np.float32)
    out = np.zeros((h, w), np.float32); amp = 1.0; tot = 0
    for o in range(octaves):
        f = 2 ** o
        for _ in range(6):
            kx = rs.randint(-f * 2, f * 2 + 1) * aniso[0]; ky = rs.randint(-f * 2, f * 2 + 1) * aniso[1]
            kx, ky = round(kx), round(ky)
            if kx == 0 and ky == 0: continue
            ph = rs.uniform(0, 2 * math.pi)
            out += amp * np.cos(2 * math.pi * (kx * x + ky * y) / period + ph); tot += amp
        amp *= 0.55
    return out / max(tot, 1e-6) * 2.0


def fibre(h, w, period, seed=0, density=0.0025):
    """Short paper fibres scattered on a periodic torus (wraps at `period`)."""
    import cv2
    rs = np.random.RandomState(seed + 11)
    tile = np.zeros((period * 3, period * 3), np.float32)
    n = max(1, int(period * period * density))
    for _ in range(n):
        cx, cy = rs.uniform(period, 2 * period, 2); a = rs.uniform(0, math.pi); L = rs.uniform(2, 7)
        p0 = (int((cx - L * math.cos(a)) * 4), int((cy - L * math.sin(a)) * 4)); p1 = (int((cx + L * math.cos(a)) * 4), int((cy + L * math.sin(a)) * 4))
        cv2.line(tile, p0, p1, float(rs.uniform(0.4, 1.0)), 1, cv2.LINE_AA, shift=2)
    t = tile[period:2 * period, period:2 * period]
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            if dx or dy: t = np.maximum(t, tile[period + dy * period:2 * period + dy * period, period + dx * period:2 * period + dx * period])
    reps = (int(math.ceil(h / period)) + 1, int(math.ceil(w / period)) + 1)
    return np.tile(t, reps)[:h, :w]


class Canvas:
    def __init__(self, w, h, d=1, seed=0):
        self.w, self.h, self.d, self.seed = w, h, d, seed
        self.W, self.H = int(round(w * d)), int(round(h * d))
        self.rgb = np.zeros((self.H, self.W, 3), np.float32); self.a = np.zeros((self.H, self.W), np.float32)
        self.rs = np.random.RandomState(seed)

    # --------------------------------------------------------------- base
    def fill(self, hexc, alpha=1.0):
        self.rgb[:] = hex_rgb(hexc); self.a[:] = alpha; return self

    def grain(self, hex_a, hex_b, amount, period_px, fibres=0.0, aniso=(1.0, 1.0)):
        """Blend toward hex_b by a periodic noise field (amount = max mix), plus optional fibres toward hex_b."""
        P = int(round(period_px * self.d))
        n = periodic_noise(self.H, self.W, P, seed=self.seed, aniso=aniso)
        t = np.clip(0.5 + 0.5 * n, 0, 1) * amount
        if fibres > 0: t = np.clip(t + fibre(self.H, self.W, P, self.seed) * fibres, 0, 1)
        A, B = hex_rgb(hex_a), hex_rgb(hex_b)
        self.rgb[:] = A + (B - A) * t[..., None]; self.a[:] = 1.0; return self

    def _px(self, v): return v * self.d

    def _ink_mask(self, mask, ink, seed_off=0):
        """Uneven inking: coverage modulated by a short-period noise (period 16 css px, still periodic for tiling)."""
        if ink >= 1.0: return mask
        P = max(4, int(round(16 * self.d)))
        n = periodic_noise(self.H, self.W, P, octaves=2, seed=self.seed + 101 + seed_off)
        return mask * np.clip(ink + (1 - ink) * n, 0.35, 1.0)

    def _comp(self, mask, hexc, opacity=1.0):
        c = hex_rgb(hexc); m = np.clip(mask * opacity, 0, 1)[..., None]
        self.rgb[:] = self.rgb * (1 - m) + c * m
        self.a[:] = np.maximum(self.a, m[..., 0]); return self

    def mask_rect(self, x, y, w, h):
        """Anti-aliased coverage of [x, x+w) x [y, y+h) in css px."""
        X0, Y0, X1, Y1 = self._px(x), self._px(y), self._px(x + w), self._px(y + h)
        xs = np.arange(self.W, dtype=np.float32); ys = np.arange(self.H, dtype=np.float32)
        cx = np.clip(np.minimum(xs + 1, X1) - np.maximum(xs, X0), 0, 1); cy = np.clip(np.minimum(ys + 1, Y1) - np.maximum(ys, Y0), 0, 1)
        return cy[:, None] * cx[None, :]

    def fill_rect(self, x, y, w, h, hexc, ink=1.0, opacity=1.0):
        return self._comp(self._ink_mask(self.mask_rect(x, y, w, h), ink), hexc, opacity)

    def rule(self, x0, y0, x1, y1, hexc, w=1, ink=1.0, opacity=1.0):
        """Axis-aligned printed rule (horizontal if y0 == y1)."""
        if y0 == y1: m = self.mask_rect(x0, y0, x1 - x0, w)
        else: m = self.mask_rect(x0, y0, w, y1 - y0)
        return self._comp(self._ink_mask(m, ink), hexc, opacity)

    def box(self, x, y, w, h, hexc, lw=1, ink=1.0, opacity=1.0):
        m = np.maximum.reduce([self.mask_rect(x, y, w, lw), self.mask_rect(x, y + h - lw, w, lw), self.mask_rect(x, y, lw, h), self.mask_rect(x + w - lw, y, lw, h)])
        return self._comp(self._ink_mask(m, ink), hexc, opacity)

    def clear_rect(self, x, y, w, h):
        m = self.mask_rect(x, y, w, h); self.a[:] = self.a * (1 - m); return self

    def edge_light(self, x, y, w, h, hexc, lw=1, sides="tl", opacity=1.0):
        """A physical bevel line (not a shadow): one-pixel light/dark on the named sides of a rectangle."""
        ms = []
        if "t" in sides: ms.append(self.mask_rect(x, y, w, lw))
        if "b" in sides: ms.append(self.mask_rect(x, y + h - lw, w, lw))
        if "l" in sides: ms.append(self.mask_rect(x, y, lw, h))
        if "r" in sides: ms.append(self.mask_rect(x + w - lw, y, lw, h))
        return self._comp(np.maximum.reduce(ms), hexc, opacity)

    def disc(self, cx, cy, r, hexc, opacity=1.0, ring=0):
        ys, xs = np.mgrid[0:self.H, 0:self.W].astype(np.float32) + 0.5
        dd = np.hypot(xs - self._px(cx), ys - self._px(cy))
        m = np.clip(self._px(r) - dd + 0.5, 0, 1)
        if ring: m = m * np.clip(dd - (self._px(r) - self._px(ring)) + 0.5, 0, 1)
        return self._comp(m, hexc, opacity)

    def icon(self, svg, x, y, px, hexc, ink=1.0, opacity=1.0):
        """Rasterise an icon SVG (currentColor) at px css px and print it."""
        import io, resvg_py
        from PIL import Image
        n = int(round(px * self.d))
        b = resvg_py.svg_to_bytes(svg_string=svg.replace("currentColor", "#000000"), width=n, height=n)
        a = np.array(Image.open(io.BytesIO(bytes(b))).convert("RGBA"))[..., 3].astype(np.float32) / 255
        m = np.zeros((self.H, self.W), np.float32); X, Y = int(round(self._px(x))), int(round(self._px(y)))
        m[Y:Y + n, X:X + n] = a[:max(0, min(n, self.H - Y)), :max(0, min(n, self.W - X))]
        return self._comp(self._ink_mask(m, ink, 7), hexc, opacity)

    def text(self, s, x, y, font_path, size, hexc, tracking_em=0.0, weight=None, ink=1.0, opacity=1.0):
        """Baked print (only for words that are printed on the form itself, from the project's copy sheet)."""
        from PIL import Image, ImageDraw, ImageFont
        f = ImageFont.truetype(font_path, int(round(size * self.d)))
        if weight is not None:
            try: f.set_variation_by_axes([weight])
            except Exception: pass
        im = Image.new("L", (self.W, self.H), 0); dr = ImageDraw.Draw(im)
        tr = tracking_em * size * self.d
        if tr == 0: dr.text((self._px(x), self._px(y)), s, font=f, fill=255)          # whole run: kerning kept
        else:
            for i, ch in enumerate(s):                                                  # tracked: kerned advance of the prefix + i x tracking
                dr.text((self._px(x) + f.getlength(s[:i]) + i * tr, self._px(y)), ch, font=f, fill=255)
        m = np.array(im, np.float32) / 255
        return self._comp(self._ink_mask(m, ink, 3), hexc, opacity)

    def torn_edge(self, side, depth, period_px, seed=None):
        """Periodic torn edge: alpha cut along one side (bottom|top), wraps at period_px so the edge tiles."""
        P = int(round(period_px * self.d))
        xs = np.arange(self.W, dtype=np.float32)
        rs = np.random.RandomState((self.seed if seed is None else seed) + 5); prof = np.zeros(self.W, np.float32)
        for k in range(1, 9):
            prof += rs.uniform(0.3, 1.0) / k * np.cos(2 * math.pi * k * xs / P * rs.choice([1, 2, 3]) + rs.uniform(0, 6.28))
        prof = (prof - prof.min()) / max(1e-6, prof.max() - prof.min()) * self._px(depth)
        ys = np.arange(self.H, dtype=np.float32)[:, None]
        if side == "bottom": cut = np.clip((self.H - prof[None, :]) - ys, 0, 1)
        else: cut = np.clip(ys - prof[None, :] + 1, 0, 1)
        self.a[:] = self.a * cut; return self

    def rgba8(self):
        out = np.concatenate([self.rgb, self.a[..., None]], -1)
        out[..., :3] = np.where(self.a[..., None] > 0, out[..., :3], out[..., :3])   # RGB kept under A=0 (bleed)
        return (np.clip(out, 0, 1) * 255 + 0.5).astype(np.uint8)

    def save(self, path):
        from PIL import Image
        os.makedirs(os.path.dirname(path), exist_ok=True)
        Image.fromarray(self.rgba8(), "RGBA").save(path, optimize=True); return path


def nine_json(pid, size, slc, center, edges, images, content=None, slots=None, notes="", fmt="ig_ui_nine"):
    """The engine-neutral nine-slice description. size and slice in css px at 1x."""
    l, t, r, b = slc["l"], slc["t"], slc["r"], slc["b"]
    return {
        "format": fmt, "version": 1, "id": pid, "size": list(size), "slice": dict(slc),
        "center": center, "edges": edges, "images": images,
        "content": content or [l, t, r, b], "slots": slots or [],
        "css": "border-image: url(%s) %d %d %d %d %s / %dpx %dpx %dpx %dpx %s; border-style: solid; border-width: %dpx %dpx %dpx %dpx;" % (
            images["1"], t, r, b, l, "fill" if center != "hollow" else "", t, r, b, l, "repeat" if edges == "tile" else "stretch", t, r, b, l),
        "godot": {"node": "NinePatchRect or StyleBoxTexture", "patch_margin": {"left": l, "top": t, "right": r, "bottom": b},
                  "axis_stretch": "TILE" if edges == "tile" else "STRETCH", "draw_center": center != "hollow",
                  "texture": images.get("2", images["1"]), "texture_scale_note": "use the @2x image with margins x2 on hi-dpi, or set StyleBoxTexture.texture_margin_* x2 and scale 0.5"},
        "notes": notes,
    }
