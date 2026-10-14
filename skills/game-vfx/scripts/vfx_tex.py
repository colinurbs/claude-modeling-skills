"""
vfx_tex.py -- procedural flipbook / sprite-sheet painters for game-vfx (numpy + cv2, no Blender).

Every painter returns straight-alpha RGBA float32 cells (h, w, 4) in 0..1. A sheet is a grid of
cells laid out row-major from the TOP-LEFT (Babylon particle sprite cells and Godot
particles_anim frames both count that way). Painters are deterministic for a given seed.

  _e  sheets: greyscale luminance in RGB, coverage in A; tinted by the effect data, blended additively.
  _bc sheets: real colour in RGB (palette-checked), straight alpha, alpha-blended; tinted near-white in data.
  Neutral _bc cells meant to be TINTED by data (smoke puffs, blood droplets) sit at value ~0.85 so the data colour is
  the colour the player sees; a darker neutral silently darkens every effect that uses it.

Add a painter: def p_<name>(cell, k, n, rng, prm) -> (cell, cell, 4) array, register it in PAINTERS.
Keep borders transparent (the validator checks border alpha per cell) and leave RGB bled under A=0
(paint_sheet does the bleed) so mips do not drag dark fringes into the sprite.
"""
import json, os
import numpy as np
import cv2


# --------------------------------------------------------------------------- helpers

def hexrgb(h):
    h = h.lstrip("#"); return np.array([int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4)], np.float32)


def grid(n):
    """cell coordinates in -1..1 (x right, y UP), pixel centres."""
    c = (np.arange(n, dtype=np.float32) + 0.5) / n * 2 - 1
    x, y = np.meshgrid(c, -c)
    return x, y


def smoothstep(e0, e1, x):
    t = np.clip((x - e0) / (e1 - e0), 0, 1); return t * t * (3 - 2 * t)


def value_noise(n, freq, rng, octaves=4, gain=0.5, offset=(0.0, 0.0)):
    """tileless fBm value noise at n x n, 0..1. freq = lattice cells across the image for octave 0."""
    out = np.zeros((n, n), np.float32); amp = 1.0; tot = 0.0
    for o in range(octaves):
        f = max(2, int(round(freq * (2 ** o))))
        lat = rng.random((f + 3, f + 3)).astype(np.float32)
        big = cv2.resize(lat, (n * (f + 3) // f, n * (f + 3) // f), interpolation=cv2.INTER_CUBIC)
        ox = int(offset[0] * n / f) % max(1, big.shape[1] - n); oy = int(offset[1] * n / f) % max(1, big.shape[0] - n)
        out += amp * big[oy:oy + n, ox:ox + n]; tot += amp; amp *= gain
    out /= tot
    lo, hi = np.percentile(out, 1), np.percentile(out, 99)
    return np.clip((out - lo) / max(1e-6, hi - lo), 0, 1)


def warp_sample(field, dx, dy):
    n = field.shape[0]
    yy, xx = np.mgrid[0:n, 0:n].astype(np.float32)
    return cv2.remap(field, (xx + dx).astype(np.float32), (yy + dy).astype(np.float32), cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)


def edge_fade(n, margin=0.08):
    """1 inside, 0 at the cell border (square), so no sprite ever shows a hard quad edge."""
    x, y = grid(n); m = np.maximum(np.abs(x), np.abs(y))
    return 1 - smoothstep(1 - margin * 2, 1 - margin * 0.5, m)


def rgba(lum_or_rgb, a):
    a = np.clip(a, 0, 1).astype(np.float32)
    rgb = lum_or_rgb if lum_or_rgb.ndim == 3 else np.repeat(lum_or_rgb[..., None], 3, -1)
    return np.concatenate([np.clip(rgb, 0, 1).astype(np.float32), a[..., None]], -1)


def bleed(cell, iters=24):
    """push RGB outward into A~0 pixels (straight alpha; avoids dark mip fringes)."""
    rgb = cell[..., :3].copy(); a = cell[..., 3]; known = (a > 0.02).astype(np.float32)
    if known.sum() == 0: return cell
    k = np.ones((3, 3), np.float32)
    acc = rgb * known[..., None]; w = known.copy()
    for _ in range(iters):
        acc2 = cv2.filter2D(acc, -1, k, borderType=cv2.BORDER_REPLICATE); w2 = cv2.filter2D(w, -1, k, borderType=cv2.BORDER_REPLICATE)
        fill = (w == 0) & (w2 > 0)
        acc[fill] = acc2[fill] / w2[fill][..., None]; w[fill] = 1
        if not (w == 0).any(): break
    out = cell.copy(); m = known == 0
    out[..., :3][m] = acc[m]
    if (w == 0).any(): out[..., :3][w == 0] = acc[w > 0].mean(0)
    return out


def shade_from_density(d, light=(-0.5, 0.8), strength=0.35, blur=3):
    """fake top-left lighting of a density field: brighter where density falls off toward the light."""
    b = cv2.GaussianBlur(d, (0, 0), blur)
    gy, gx = np.gradient(b)
    # image y grows downward; light comes from up-left => favour -gx (left) and +gy (rows above are lower index)
    l = -(gx * light[0]) + (gy * light[1])
    l = l / (np.abs(l).max() + 1e-6)
    return np.clip(1.0 + strength * l, 0, 1.4)


# --------------------------------------------------------------------------- painters

def p_flash_front(n, k, nf, rng, prm):
    """muzzle flash seen along the bore: a hot core with 4-6 ragged gas petals. Variants (random frame)."""
    x, y = grid(n); r = np.sqrt(x * x + y * y) + 1e-6; th = np.arctan2(y, x)
    petals = int(rng.integers(prm.get("petals", [4, 6])[0], prm.get("petals", [4, 6])[1] + 1))
    base = rng.random() * np.pi * 2
    lum = np.zeros_like(r)
    for i in range(petals):
        ang = base + i * 2 * np.pi / petals + rng.normal(0, 0.25)
        d = np.angle(np.exp(1j * (th - ang)))
        width = rng.uniform(0.38, 0.62); length = rng.uniform(0.20, 0.34) * prm.get("reach", 1.0)   # lobes, not rays (no starburst)
        lum += np.exp(-(d / width) ** 2) * np.exp(-(r / length) ** 1.6) * rng.uniform(0.55, 1.0)
    core = np.exp(-(r / prm.get("core", 0.16)) ** 2)
    halo = np.exp(-(r / 0.45) ** 2) * 0.25
    turb = value_noise(n, 6, rng, 4)
    lum = (lum * (0.35 + 0.9 * turb) + core * 1.0 + halo * turb)
    lum = lum * edge_fade(n) * (1 - smoothstep(0.7, 0.98, r))
    a = np.clip(lum * 1.1, 0, 1)
    l = np.clip(lum, 0, 1) ** 0.8
    return rgba(np.clip(0.5 + 0.3 * l, 0, 0.8), a)   # peak 0.8: the data colour, not the sheet, sets the brightness


def p_flash_side(n, k, nf, rng, prm):
    """muzzle flash seen from the side, for a STRETCHED billboard: base at the BOTTOM of the cell (the muzzle),
    the gas plume grows upward (= along the particle's velocity), with a shock 'diamond' and ragged edges."""
    x, y = grid(n)
    v = (y + 1) / 2                                      # 0 at the bottom edge, 1 at the top
    turb = value_noise(n, 5, rng, 4)
    L = rng.uniform(0.78, 0.92)                           # plume length (fraction of the cell)
    t = np.clip((v - 0.04) / L, 0, 1)
    prof = (0.10 + 0.55 * np.sin(np.pi * np.clip(t, 0, 1) ** 0.55) * (1 - t) ** 0.6) * prm.get("width", 1.0)
    prof *= 0.8 + 0.4 * (turb - 0.5)
    w = np.maximum(prof, 1e-3)
    body = np.exp(-(x / w) ** 2 * 2.2) * smoothstep(0.0, 0.06, v) * (1 - smoothstep(0.75, 1.0, t))
    diamond = np.exp(-((t - rng.uniform(0.22, 0.32)) / 0.07) ** 2) * np.exp(-(x / (w * 0.5)) ** 2) * 0.6
    core = np.exp(-((v - 0.06) / 0.07) ** 2) * np.exp(-(x / 0.12) ** 2) * 1.2
    lum = (body * (0.55 + 0.6 * turb) + diamond + core) * edge_fade(n)
    a = np.clip(lum * 1.15, 0, 1)
    return rgba(np.clip(0.5 + 0.3 * np.clip(lum, 0, 1) ** 0.8, 0, 0.8), a)   # peak 0.8 like flash_front


def p_smoke_puff(n, k, nf, rng_sheet, prm):
    """one puff over its life (frame k of nf): dense and compact -> large, wispy, thin. Greyscale, lit from above.
    A noise-warped radial density (same noise every frame, so the flipbook is continuous), eroded with age."""
    age = k / max(1, nf - 1)
    na = value_noise(n, 4, np.random.default_rng(prm["_seed"] + 1), 5)
    nb = value_noise(n, 6, np.random.default_rng(prm["_seed"] + 2), 4)
    x, y = grid(n); y = y - 0.06 * age
    sw = 4 + 16 * age
    detail = 0.5 * na + 0.5 * warp_sample(nb, (na - 0.5) * sw, (na - 0.5) * sw)
    r = np.sqrt(x * x + y * y)
    R = prm.get("r0", 0.62) + (prm.get("r1", 0.95) - prm.get("r0", 0.62)) * (1 - (1 - age) ** 1.6)
    rw = r + (na - 0.5) * (0.18 + 0.25 * age)
    base = 1 - smoothstep(0.45 * R, 1.0 * R, rw)
    thr = 0.32 * age
    dens = np.clip((base * (0.5 + 0.7 * detail) - thr) / (1 - thr), 0, 1)
    dens *= (1 - smoothstep(0.84, 0.99, r)) * edge_fade(n, 0.04)
    a = np.clip(dens * 1.35 * (1.0 - 0.4 * age), 0, 1)
    a = cv2.GaussianBlur(a, (0, 0), max(0.7, n / 200 * (1 + 2 * age)))
    shade = shade_from_density(dens, strength=0.4, blur=max(1.5, n / 40))
    # neutral tint sheet: mean value ~0.85 so the data colour IS the perceived colour (shading +-0.1 around it)
    lum = np.clip(0.86 + 0.25 * (shade - 1.0) + 0.06 * (detail - 0.5), 0, 0.95)
    return rgba(lum, a)


def p_spark_streak(n, k, nf, rng, prm):
    """a hot spark streak for a STRETCHED billboard: head at the TOP (the direction of travel), tail fading down."""
    x, y = grid(n); v = (y + 1) / 2
    length = rng.uniform(0.55, 0.92); head = rng.uniform(0.84, 0.92)
    tail = head - length
    t = np.clip((v - tail) / max(1e-3, head - tail), 0, 1)
    width = 0.30 + 0.25 * t                       # the line fills the quad's width: data size_m = visible thickness
    line = np.exp(-(x / width) ** 2 * 1.5) * smoothstep(tail - 0.02, tail + 0.12, v) * (1 - smoothstep(head - 0.02, head + 0.06, v))
    lum = line * (0.25 + 0.75 * t ** 1.5) * edge_fade(n, 0.06)
    lum += np.exp(-((v - head + 0.03) / 0.05) ** 2) * np.exp(-(x / 0.45) ** 2) * 0.6
    a = np.clip(lum * 1.2, 0, 1)
    return rgba(np.clip(0.55 + 0.3 * np.clip(lum, 0, 1), 0, 0.85), a)   # peak 0.85: thin streaks stack additively


def _poly(n, pts, val=1.0):
    m = np.zeros((n * 4, n * 4), np.float32)
    p = ((np.array(pts) * 0.5 + 0.5) * n * 4).astype(np.int32); p[:, 1] = n * 4 - 1 - p[:, 1]
    cv2.fillPoly(m, [p], val, lineType=cv2.LINE_AA)
    return cv2.resize(m, (n, n), interpolation=cv2.INTER_AREA)


def _chip(n, rng, rx, ry, jag=0.25, verts=(6, 9), rot=None):
    nv = int(rng.integers(verts[0], verts[1] + 1)); a0 = rng.random() * np.pi * 2 if rot is None else rot
    angs = np.sort(rng.random(nv)) * np.pi * 2 + a0
    rr = 1 - jag * rng.random(nv)
    pts = np.stack([np.cos(angs) * rx * rr, np.sin(angs) * ry * rr], -1)
    if rot is not None:
        c, s = np.cos(rot), np.sin(rot); pts = pts @ np.array([[c, s], [-s, c]])
    return pts


def p_debris(n, k, nf, rng, prm):
    """debris atlas, one kind per cell from prm['cells'][k]: concrete | splinter | metal | paint | blood | casing | hull."""
    kinds = prm["cells"]; kind = kinds[k % len(kinds)]
    x, y = grid(n); noise = value_noise(n, 5, rng, 3)
    if kind in ("concrete", "metal", "paint"):
        base = {"concrete": prm.get("concrete", "#A3A49E"), "metal": prm.get("metal", "#4A4C4B"), "paint": prm.get("paint", "#5F6A60")}[kind]
        jag = 0.35 if kind == "concrete" else 0.5
        pts = _chip(n, rng, rng.uniform(0.82, 0.92), rng.uniform(0.68, 0.85), jag * 0.6)  # fill ~80% of the cell: data size_m = real size
        if kind != "concrete": pts[:, 1] *= 0.7
        m = _poly(n, pts)
        # facets: split by a random line through the chip, each half its own brightness
        ang = rng.random() * np.pi; f = (np.cos(ang) * x + np.sin(ang) * y) > rng.uniform(-0.2, 0.2)
        shade = np.where(f, rng.uniform(0.85, 1.0), rng.uniform(0.62, 0.78)).astype(np.float32)
        col = hexrgb(base)[None, None, :] * (shade * (0.85 + 0.3 * (noise - 0.5)))[..., None]
        if kind == "concrete":   # aggregate specks
            sp = value_noise(n, 18, rng, 1) > 0.78
            col = np.where(sp[..., None], col * 0.75, col)
        return rgba(col, m * edge_fade(n))
    if kind == "splinter":
        L = rng.uniform(0.9, 0.96); W = rng.uniform(0.16, 0.26); rot = rng.uniform(-0.5, 0.5) + np.pi / 4
        t = np.linspace(-1, 1, 9)
        top = np.stack([t * L, W * (1 - t ** 2) ** 0.6 * (1 + 0.4 * rng.random(9))], -1)
        bot = np.stack([t[::-1] * L, -W * (1 - t[::-1] ** 2) ** 0.6 * (1 + 0.4 * rng.random(9))], -1)
        pts = np.concatenate([top, bot]); c, s = np.cos(rot), np.sin(rot); pts = pts @ np.array([[c, s], [-s, c]])
        m = _poly(n, pts)
        u = (np.cos(rot) * x + np.sin(rot) * y); vv = (-np.sin(rot) * x + np.cos(rot) * y)
        grain = 0.85 + 0.15 * np.sin(vv * 60 + 3 * noise)
        fresh = hexrgb(prm.get("wood_fresh", "#A08D70")); weathered = hexrgb(prm.get("wood_face", "#6E665A"))
        col = np.where((vv > 0)[..., None], fresh[None, None], weathered[None, None]) * grain[..., None]
        return rgba(col, m * edge_fade(n))
    if kind == "blood":
        d = np.zeros_like(x); nb = int(rng.integers(1, 3))
        for i in range(nb):
            cx, cy = rng.uniform(-0.1, 0.1, 2); sx = rng.uniform(0.42, 0.58); sy = sx * rng.uniform(0.7, 1.4)
            d += np.exp(-(((x - cx) / sx) ** 2 + ((y - cy) / sy) ** 2) * 2.2)
        m = smoothstep(0.35, 0.6, d)
        # neutral grey: the effect data tints it dried blood (S3), so the palette rule lives in one place
        lum = 0.80 + 0.08 * np.clip(1 - d, 0, 1) + 0.07 * smoothstep(0.6, 1.2, d) * (y > 0)
        return rgba(np.clip(lum, 0, 0.95), m * edge_fade(n))
    if kind in ("casing", "hull"):
        # side view along the cell's vertical axis: case mouth up, rim at the bottom
        L = 0.62 if kind == "casing" else 0.85; R = 0.2 if kind == "casing" else 0.3
        body = (np.abs(x) < R) & (y > -L) & (y < L)
        rim = (np.abs(x) < R * 1.08) & (y > -L - 0.06) & (y < -L + 0.08)
        m = cv2.GaussianBlur((body | rim).astype(np.float32), (0, 0), 0.7)
        cyl = 0.6 + 0.4 * np.sqrt(np.clip(1 - (x / (R * 1.1)) ** 2, 0, 1))      # cylinder shading
        if kind == "casing":
            col = hexrgb(prm.get("brass", "#8A7A58"))[None, None] * cyl[..., None]
            mouth = (y > L - 0.08) & (np.abs(x) < R * 0.75)
            col = np.where(mouth[..., None], col * 0.45, col)
        else:
            hull = hexrgb(prm.get("hull", "#5A5240")); brass = hexrgb(prm.get("brass", "#8A7A58"))
            col = np.where((y < -L + 0.35)[..., None], brass[None, None], hull[None, None]) * cyl[..., None]
        return rgba(np.clip(col, 0, 1), m * edge_fade(n))
    raise SystemExit("debris: unknown cell kind %s" % kind)


def p_tracer_streak(n, k, nf, rng, prm):
    """a faint tracer streak for a STRETCHED billboard (~100:1): head at the TOP, a long soft tail fading down. Wide soft
    cross-section, no head blob, little variation, so low mips (the quad is far thinner than long) keep about the same
    average brightness: a faint line at range, a soft streak up close, never a hard laser bar."""
    x, y = grid(n); v = (y + 1) / 2
    head = prm.get("head", 0.95); tail = prm.get("tail", 0.04)
    t = np.clip((v - tail) / (head - tail), 0, 1)
    along = (0.25 + 0.75 * t ** prm.get("tail_power", 1.4)) * (1 - smoothstep(head - 0.05, head + 0.02, v)) * smoothstep(tail, tail + 0.08, v)
    across = np.exp(-(x / (0.42 * (prm.get('tail_width', 0.35) + (1 - prm.get('tail_width', 0.35)) * t))) ** 2 * 1.6)   # tapers to the tail
    lum = along * across * edge_fade(n, 0.06)
    return rgba(np.clip(0.5 + 0.25 * lum, 0, 0.75), np.clip(lum * 1.1, 0, 1))   # dim sheet: the data alpha keeps it faint


def p_arc(n, k, nf, rng, prm):
    """a live cable bitten through (camera billboard): a compact blue-white flash bead where the jaws meet the copper,
    with one or two SHORT ragged filaments (not a bolt between terminals). Variants per cell (random frame)."""
    S = 4; N = n * S; img = np.zeros((N, N), np.float32)

    def jag(a, b, depth, amp):
        if depth == 0: return [a, b]
        m = (a + b) / 2; d = b - a; nrm = np.array([-d[1], d[0]]) / (np.linalg.norm(d) + 1e-6)
        m = m + nrm * rng.normal(0, amp)
        return jag(a, m, depth - 1, amp * 0.5)[:-1] + jag(m, b, depth - 1, amp * 0.5)

    def draw(pts, w):
        P = ((np.clip(np.array(pts), -0.85, 0.85) * 0.5 + 0.5) * N).astype(np.int32); P[:, 1] = N - 1 - P[:, 1]
        cv2.polylines(img, [P], False, 1.0, max(1, int(w * S)), lineType=cv2.LINE_AA)
    c0 = rng.uniform(-0.05, 0.05, 2)
    for _ in range(int(rng.integers(1, 3))):
        ang = rng.uniform(0, 2 * np.pi); L = rng.uniform(0.25, prm.get("reach", 0.5))
        draw(jag(c0, c0 + L * np.array([np.cos(ang), np.sin(ang)]), 3, 0.05), prm.get("width_px", 1.6))
    core = cv2.resize(img, (n, n), interpolation=cv2.INTER_AREA)
    x, y = grid(n); r2 = (x - c0[0]) ** 2 + ((y - c0[1]) * rng.uniform(1.0, 1.6)) ** 2
    bead = np.exp(-r2 / 0.012) + 0.35 * np.exp(-r2 / 0.06) * value_noise(n, 6, rng, 3)
    lum = np.clip(core * np.exp(-r2 / 0.25) + bead + 0.2 * cv2.GaussianBlur(core, (0, 0), n / 40), 0, 1.2) * edge_fade(n, 0.06)
    return rgba(np.clip(0.5 + 0.32 * np.clip(lum, 0, 1), 0, 0.82), np.clip(lum, 0, 1))


def p_glass_shard(n, k, nf, rng, prm):
    """window-glass fragments (alpha _bc, tinted near-white by data): cells < prm['glitter_from'] are single slivers
    (thin angular triangles/quads, translucent green-grey body, bright edges where the light catches the break);
    later cells are clusters of tiny glitter crumbs. Values stay <= ~0.85 (no pure white, S3)."""
    body = hexrgb(prm.get("body", "#94A09A")); edge = hexrgb(prm.get("edge", "#C6CEC9")); x, y = grid(n)
    if k < prm.get("glitter_from", 12):
        L = rng.uniform(0.75, 0.9); W = L / rng.uniform(2.2, 4.5); rot = rng.uniform(0, np.pi)
        if rng.random() < 0.5:   # sliver triangle
            pts = np.array([[-L, rng.uniform(-W, W) * 0.3], [L, rng.uniform(-W, W)], [rng.uniform(-0.3, 0.3) * L, W]])
        else:                    # long quad with a skewed break
            pts = np.array([[-L, -W * rng.uniform(0.3, 1)], [L * rng.uniform(0.6, 1), -W], [L, W * rng.uniform(0.2, 0.8)], [-L * rng.uniform(0.5, 0.9), W]])
        c, s = np.cos(rot), np.sin(rot); pts = pts @ np.array([[c, s], [-s, c]])
        m = _poly(n, pts)
        inner = cv2.erode((m > 0.5).astype(np.float32), np.ones((3, 3), np.uint8), iterations=max(1, n // 40))
        rim = np.clip(m - cv2.GaussianBlur(inner, (0, 0), 0.6), 0, 1)
        ang = rng.uniform(0, np.pi); hl = np.exp(-(((np.cos(ang) * x + np.sin(ang) * y) - rng.uniform(-0.2, 0.2)) / 0.08) ** 2) * m
        # broken edges seen side-on are dark green (thick glass), faces catch a bright streak: reads on light AND dark
        dark = hexrgb(prm.get("rim_dark", "#4E5854")); side = (np.cos(rot + 1.2) * x + np.sin(rot + 1.2) * y) > 0
        a = np.clip(m * prm.get("body_alpha", 0.55) + rim * 0.6 + hl * 0.35, 0, 1)
        col = body[None, None] * np.ones_like(x)[..., None]
        rimc = np.where(side[..., None], dark[None, None], edge[None, None])
        w = np.clip(rim * 1.4, 0, 1)[..., None]; col = col * (1 - w) + rimc * w
        h = np.clip(hl * 1.2, 0, 1)[..., None]; col = col * (1 - h) + edge[None, None] * h
        return rgba(col, a * edge_fade(n))
    m = np.zeros_like(x)
    for _ in range(int(rng.integers(4, 8))):
        cx, cy = rng.uniform(-0.55, 0.55, 2); r = rng.uniform(0.06, 0.16)
        m = np.maximum(m, _poly(n, _chip(n, rng, r, r * rng.uniform(0.4, 0.9), 0.4, (3, 5)) + np.array([cx, cy])))
    col = (body * 0.4 + edge * 0.6)[None, None] * np.ones_like(x)[..., None]
    return rgba(col, np.clip(m * 0.9, 0, 1) * edge_fade(n))


PAINTERS = {"flash_front": p_flash_front, "flash_side": p_flash_side, "smoke_puff": p_smoke_puff,
            "spark_streak": p_spark_streak, "debris": p_debris,
            "tracer_streak": p_tracer_streak, "arc": p_arc, "glass_shard": p_glass_shard}


# --------------------------------------------------------------------------- fire / gas / wisp painters (irongrip round 2: vfx_teargas,
# vfx_fire_barrel, vfx_fire_molotov, vfx_smoke_cigarette, vfx_smoke_chimney). Registered with PAINTERS.update below.

def p_flame_tongue(n, k, nf, rng, prm):
    """a small bed of flame over its life (frame k of nf), for a camera billboard with its BASE at the BOTTOM of the cell
    (data pivot [0, -0.5]): 2-3 licking tongues on one root that grow, lick and thin out; the root stays on the cell bottom
    until the last frames (no floating tufts) and the tallest tip keeps headroom under the cell top (no flat lid).
    Greyscale _e (tinted by data). Same noise every frame, scrolled upward with age, so the flipbook is continuous.
    prm: sway (lateral lick, 0..1), width (base half-width), height (peak tip height as a fraction of the cell)."""
    age = k / max(1, nf - 1)
    s0 = prm["_seed"]
    na = value_noise(n, 3, np.random.default_rng(s0 + 11), 4)
    nb = value_noise(n, 7, np.random.default_rng(s0 + 12), 3)
    sr = np.random.default_rng(s0 + 13)                     # tongue layout: fixed for the sheet (continuous flipbook)
    tongues = [(0.0, 1.0, 1.0)] + [(sr.choice([-1, 1]) * sr.uniform(0.22, 0.34), sr.uniform(0.5, 0.72), sr.uniform(0.5, 0.7)) for _ in range(2)]
    x, y = grid(n)
    v = (y + 1) / 2                                         # 0 = bottom edge (the fuel), 1 = top
    lift = age * n * 0.55
    na_s = warp_sample(na, np.zeros_like(na), np.full_like(na, lift))
    nb_s = warp_sample(nb, np.zeros_like(nb), np.full_like(nb, lift * 1.6))
    Hmax = prm.get("height", 0.86)
    lo = 0.02 + max(0.0, age - 0.8) / 0.2 * 0.18            # the root holds; only the last frames lift off a little
    sway = prm.get("sway", 0.5)
    w0 = prm.get("width", 0.5) * (1.0 - 0.3 * age)
    body = np.zeros_like(x)
    for (ox, hk, wk) in tongues:
        H = Hmax * hk * (0.5 + 0.5 * smoothstep(0.0, 0.4, age)) * (1.0 - 0.25 * smoothstep(0.7, 1.0, age))
        t = np.clip((v - lo) / max(1e-3, H - lo), 0, 1)
        xc = ox * (1 - 0.4 * t) + sway * 0.16 * np.sin(v * 7.0 + 1.3 + age * 5.0 + ox * 9) * v + (na_s - 0.5) * 0.22 * v
        prof = w0 * wk * (np.sin(np.pi * np.clip(0.2 + 0.8 * t, 0, 1)) ** 0.7) * (1 - t) ** 0.6 + 1e-3
        xw = x + (na_s - 0.5) * 0.5 * (0.3 + t) + (nb_s - 0.5) * 0.3 * t      # strong lateral warp: ragged licks, not cones
        b = np.exp(-((xw - xc) / prof) ** 2 * 1.4) * (1 - smoothstep(H * 0.85, H * 1.0, v))
        body = np.maximum(body, b)
    body *= smoothstep(lo - 0.01, lo + 0.14, v)                # soft root: no straight cut along the cell bottom
    T = np.clip((v - lo) / max(1e-3, Hmax - lo), 0, 1)
    erode = 0.15 + 0.5 * T ** 1.2 + 0.2 * age
    tex = 0.5 * na_s + 0.5 * nb_s
    dens = np.clip((body * (0.75 + 0.5 * tex) - erode * 0.5) / (1 - erode * 0.3), 0, 1)
    dens = cv2.GaussianBlur(dens, (0, 0), max(0.8, n / 140))
    heat = (1 - 0.55 * T) * (1.0 - 0.45 * age) * (0.88 + 0.24 * tex)   # hottest low in the root, cooler at the tips
    lum = dens * heat * edge_fade(n, 0.05)
    a = np.clip(lum * 1.5, 0, 1)
    return rgba(np.clip(0.3 + 0.55 * np.clip(lum, 0, 1) ** 0.8, 0, 0.85), a)   # hot/cool interior range; peak 0.85: the data colour sets the brightness


def p_soft_billow(n, k, nf, rng, prm):
    """a soft, lobed smoke/gas billow (random-frame variants): 3-6 off-centre lumps of varied size, a wide edge falloff
    (~half the radius) and a peak alpha below 1, so overlapping quads merge into one continuous bank/plume instead of a
    row of cotton balls. Neutral _bc (value ~0.86, tinted by data), lit from above.
    prm: lumps [min, max], soft (falloff width 0..1), flat (underside flattening 0..1), alpha (peak), mass (overall size)."""
    x, y = grid(n)
    lo, hi = prm.get("lumps", [3, 6])
    nl = int(rng.integers(lo, hi + 1))
    ms = prm.get("mass", 1.0) * rng.uniform(0.75, 1.0)
    d = np.zeros_like(x)
    for i in range(nl):
        cx = rng.uniform(-0.32, 0.32) * ms; cy = rng.uniform(-0.2, 0.25) * ms; r = rng.uniform(0.2, 0.38) * ms
        d += np.exp(-(((x - cx) / r) ** 2 + ((y - cy) / (r * rng.uniform(0.75, 1.0))) ** 2) * 1.1) * rng.uniform(0.55, 1.0)
    flat = prm.get("flat", 0.4)
    d *= 1 - flat * smoothstep(-0.1, -0.75, y)
    na = value_noise(n, 3, rng, 4); nb = value_noise(n, 6, rng, 3)
    warped = warp_sample(d, (na - 0.5) * n * 0.10, (nb - 0.5) * n * 0.08)
    soft = prm.get("soft", 0.9)
    dens = smoothstep(0.03, 0.03 + soft, warped) ** 1.3 * (0.85 + 0.15 * nb)
    r = np.sqrt(x * x + y * y)
    dens *= 1 - smoothstep(0.55, 0.95, r)
    a = cv2.GaussianBlur(np.clip(dens, 0, 1), (0, 0), max(1.0, n / 18)) * prm.get("alpha", 0.7) * edge_fade(n, 0.06)
    shade = shade_from_density(dens, strength=0.18, blur=max(2.0, n / 20))
    lum = np.clip(0.86 + 0.16 * (shade - 1.0) + 0.03 * (na - 0.5), 0, 0.95)
    return rgba(lum, a)


def p_smoke_wisp(n, k, nf, rng, prm):
    """a thin rising smoke thread (random-frame variants) for a camera billboard with its BASE at the BOTTOM (pivot
    [0, -0.5]): laminar and straight near the source, then a lazy curl that widens and thins out toward the top, the way
    cigarette smoke breaks up a hand's width above the tip. Neutral _bc (value ~0.86, tinted by data)."""
    ss = 4
    N = n * ss
    canvas = np.zeros((N, N), np.float32)
    steps = 160
    t = np.linspace(0, 1, steps)
    lam = prm.get("laminar", 0.28)
    curl = smoothstep(lam * 0.6, lam + 0.3, t)
    # lateral drift grows above the laminar run: smoothed noise (2-3 lazy bends), the thread always rises
    wob = cv2.GaussianBlur(rng.normal(0, 1, steps).reshape(1, -1).astype(np.float32), (0, 0), 14).ravel()
    wob = wob / (np.abs(wob).max() + 1e-6)
    lean = rng.uniform(-0.25, 0.25)
    xs = curl * (rng.uniform(0.18, 0.3) * wob + lean * t)
    ys_ = 1.75 * t
    xs = np.clip(xs, -0.8, 0.8)
    ys = -0.92 + np.minimum(ys_, 1.75)
    width = (0.014 + 0.12 * curl * t ** 1.3) * N / 2
    alpha = (1 - smoothstep(0.5, 0.97, t)) * (0.95 - 0.55 * curl)
    for i in range(steps - 1):
        p0 = (int((xs[i] * 0.5 + 0.5) * N), int((0.5 - ys[i] * 0.5) * N))
        p1 = (int((xs[i + 1] * 0.5 + 0.5) * N), int((0.5 - ys[i + 1] * 0.5) * N))
        cv2.line(canvas, p0, p1, float(alpha[i]), max(1, int(width[i])), lineType=cv2.LINE_AA)
    canvas = cv2.GaussianBlur(canvas, (0, 0), ss * 0.9)
    blur2 = cv2.GaussianBlur(canvas, (0, 0), ss * 3.0)                   # the upper thread diffuses
    wgt = np.clip((np.linspace(1, 0, N)[:, None] - 0.35) * 2.0, 0, 1)    # rows from the top: 1 at the top
    canvas = canvas * (1 - wgt) + blur2 * 1.6 * wgt
    a = cv2.resize(canvas, (n, n), interpolation=cv2.INTER_AREA)
    a = cv2.GaussianBlur(a, (0, 0), 0.6)
    a = np.clip(a * 2.4, 0, 1) * edge_fade(n, 0.06)   # the thread core reaches full coverage; the data sets its alpha
    lum = np.full((n, n), 0.86, np.float32)
    return rgba(lum, a)


PAINTERS.update({"flame_tongue": p_flame_tongue, "soft_billow": p_soft_billow, "smoke_wisp": p_smoke_wisp})


# --------------------------------------------------------------------------- explosion painters (irongrip: vfx_explosion_charge,
# vfx_explosion_gas, vfx_explosion_pipebomb). Registered with PAINTERS.update below.

def p_blast_flash(n, k, nf, rng, prm):
    """a detonation flash seen from anywhere (camera billboard, random variant): a hot, irregular, roughly round core
    with 5-9 short blunt lumps of burning gas and noise-eroded edges. Lumps, never rays: no starburst, no lens star.
    Greyscale _e (tinted by data), peak 0.8 like the muzzle flashes."""
    x, y = grid(n); r = np.sqrt(x * x + y * y) + 1e-6; th = np.arctan2(y, x)
    lum = np.exp(-(r / prm.get("core", 0.3)) ** 2) * 1.0
    for _ in range(int(rng.integers(5, 10))):
        a = rng.random() * 2 * np.pi; d = rng.uniform(0.15, 0.45); s = rng.uniform(0.16, 0.28)
        cx, cy = np.cos(a) * d, np.sin(a) * d
        lum += np.exp(-(((x - cx) ** 2 + (y - cy) ** 2) / (s * s)) * 1.4) * rng.uniform(0.45, 0.9)
    turb = value_noise(n, 7, rng, 4); turb2 = value_noise(n, 3, rng, 3)
    lum = warp_sample(lum, (turb2 - 0.5) * n * 0.12, (turb - 0.5) * n * 0.12)
    fine = value_noise(n, 14, rng, 3)
    lum = lum * (0.45 + 0.6 * turb) * (0.8 + 0.4 * fine)
    lum = np.clip(lum - 0.15, 0, None) * (1 - smoothstep(0.6, 0.96, r)) * edge_fade(n)
    lum = lum / (lum.max() + 1e-6)
    a = np.clip(lum * 1.6, 0, 1)
    return rgba(np.clip(0.4 + 0.42 * lum ** 0.9, 0, 0.82), a)


def _dirt_jet(n, rng, prm):
    """an ejecta jet of soil, grit and dust for a STRETCHED billboard: base at the BOTTOM (the blast seat), the dense
    gritty head at the TOP (direction of travel); the dusty body widens upward and frays. Neutral _bc (tinted by data)."""
    x, y = grid(n); v = (y + 1) / 2
    na = value_noise(n, 4, rng, 4); nb = value_noise(n, 9, rng, 3)
    head = rng.uniform(0.80, 0.9)
    lean = rng.uniform(-0.12, 0.12)
    xc = lean * v ** 2 + (na - 0.5) * 0.22 * v
    w = (0.22 + 0.62 * v ** 0.9) * (0.8 + 0.4 * nb)                 # a wide ragged fan, not a spike
    body = np.exp(-((x - xc) / w) ** 2 * 1.6) * smoothstep(0.02, 0.16, v) * (1 - smoothstep(head - 0.08, head + 0.08, v + (nb - 0.5) * 0.18))
    dens = body * (0.35 + 0.85 * na) * (0.75 + 0.25 * v)
    # grit: small dark specks, denser toward the head and the core
    S = 3; N = n * S; specks = np.zeros((N, N), np.float32)
    for _ in range(int(prm.get("grit", 70))):
        vv = rng.uniform(0.25, head - 0.02); xx = lean * vv ** 2 + rng.normal(0, 0.1 + 0.3 * vv)
        px = int((xx * 0.5 + 0.5) * N); py = int((1 - vv) * N)
        cv2.circle(specks, (px, py), int(rng.integers(1, 3)) * S // 2 + 1, float(rng.uniform(0.6, 1.0)), -1, lineType=cv2.LINE_AA)
    specks = cv2.resize(specks, (n, n), interpolation=cv2.INTER_AREA) * edge_fade(n, 0.08)
    dens = np.clip(dens, 0, 1)
    a = np.clip(np.maximum(dens * 1.05, specks), 0, 1)
    a = cv2.GaussianBlur(a, (0, 0), 0.6) * edge_fade(n, 0.06)
    shade = shade_from_density(dens, strength=0.3, blur=max(1.5, n / 30))
    lum = np.clip(0.84 + 0.2 * (shade - 1.0) - 0.06 * dens - 0.45 * specks + 0.05 * (nb - 0.5), 0, 0.95)
    return rgba(lum, a)


def _dust_lump(n, rng, prm):
    """a dense, turbid blast-dust lump (camera billboard, random variant): cauliflower edges (warped multi-lump density,
    harder than soft_billow so it reads as a heavy dust mass, not gas), darker self-shadowed underside. Neutral _bc."""
    x, y = grid(n)
    d = np.zeros_like(x)
    for _ in range(int(rng.integers(5, 9))):
        cx = rng.uniform(-0.4, 0.4); cy = rng.uniform(-0.2, 0.2); r = rng.uniform(0.2, 0.36)
        d += np.exp(-(((x - cx) / (r * 1.3)) ** 2 + ((y - cy) / (r * 0.8)) ** 2) * 1.2) * rng.uniform(0.5, 1.0)
    d *= 1 - 0.35 * smoothstep(-0.1, -0.7, y)
    na = value_noise(n, 4, rng, 5); nb = value_noise(n, 10, rng, 3)
    warped = warp_sample(d, (na - 0.5) * n * 0.16, (nb - 0.5) * n * 0.12)
    dens = smoothstep(0.02, 1.0, warped * (0.6 + 0.6 * nb)) ** 1.4
    r = np.sqrt(x * x + y * y)
    dens *= 1 - smoothstep(0.6, 0.95, r)
    a = cv2.GaussianBlur(np.clip(dens, 0, 1), (0, 0), max(1.5, n / 22)) * prm.get("alpha", 0.85) * edge_fade(n, 0.06)
    shade = shade_from_density(dens, strength=0.45, blur=max(2.0, n / 30))
    lum = np.clip(0.86 + 0.3 * (shade - 1.0) - 0.08 * smoothstep(0.0, -0.6, y) + 0.12 * (na - 0.5), 0, 0.95)
    return rgba(lum, a)


def p_blast_dust(n, k, nf, rng, prm):
    """blast dust atlas: cells < prm['jets'] are ejecta jets (stretched billboard, base at the bottom, pivot [0, -0.5]),
    the rest are dense dust lumps (camera billboard). Random-frame variants within each range."""
    return _dirt_jet(n, rng, prm) if k < prm.get("jets", 8) else _dust_lump(n, rng, prm)


def p_fireball(n, k, nf, rng, prm):
    """a short, low gas fireball over its life (frame k of nf, camera billboard): a compact lumpy hot mass that swells,
    rolls upward and breaks into tatters and holes as it burns out (the soot is a separate alpha smoke emitter: additive
    cannot darken). Same noise every frame, scrolled up with age, so the flipbook is continuous. Greyscale _e."""
    age = k / max(1, nf - 1); s0 = prm["_seed"]
    na = value_noise(n, 4, np.random.default_rng(s0 + 21), 5)
    nb = value_noise(n, 9, np.random.default_rng(s0 + 22), 3)
    sr = np.random.default_rng(s0 + 23)
    lumps = [(sr.uniform(-0.3, 0.3), sr.uniform(-0.25, 0.25), sr.uniform(0.24, 0.4)) for _ in range(8)]
    x, y = grid(n); x = x * 0.62; y = y * 0.62                     # the ball fills most of the cell
    lift = age * n * 0.18
    na_s = warp_sample(na, np.zeros_like(na), np.full_like(na, lift)); nb_s = warp_sample(nb, np.zeros_like(nb), np.full_like(nb, lift * 1.5))
    grow = 0.6 + 0.5 * smoothstep(0.0, 0.5, age)
    d = np.zeros_like(x)
    for (cx, cy, r) in lumps:
        rr = r * grow * (1 - 0.25 * age); cx2 = cx * (grow + 0.6 * age); cy2 = cy * (grow + 0.4 * age) + 0.25 * age
        d += np.exp(-(((x - cx2) / rr) ** 2 + ((y - cy2) / rr) ** 2) * 1.2)
    d = warp_sample(d, (na_s - 0.5) * n * (0.08 + 0.14 * age), (nb_s - 0.5) * n * (0.08 + 0.12 * age))
    d = d / (d.max() + 1e-6)
    tex = 0.6 * na_s + 0.4 * nb_s
    thr = 0.1 + 0.55 * age ** 1.2                                 # burns out from the edges and in holes
    dens = np.clip((d ** 0.6 * (0.35 + 0.9 * tex) - thr) / max(0.2, 0.9 - thr * 0.8), 0, 1)
    r = np.sqrt(x * x + y * y) / 0.62
    dens *= (1 - smoothstep(0.7, 0.97, r)) * edge_fade(n, 0.05)
    dens = cv2.GaussianBlur(dens, (0, 0), max(0.8, n / 150))
    heat = (1.0 - 0.5 * age) * (0.55 + 0.6 * tex)
    lum = np.clip(dens, 0, 1) ** 0.7 * heat
    a = np.clip(dens * 1.8, 0, 1) * (1 - 0.25 * age)
    return rgba(np.clip(0.42 + 0.4 * np.clip(lum, 0, 1) ** 0.7, 0, 0.82), a)


PAINTERS.update({"blast_flash": p_blast_flash, "blast_dust": p_blast_dust, "fireball": p_fireball})


# --------------------------------------------------------------------------- small flames (irongrip round 3: vfx_lighter_flame)

def p_lighter_flame(n, k, nf, rng, prm):
    """one small steady flame (lighter, match, candle) as random-frame variants for a camera billboard with its BASE at
    the BOTTOM of the cell (data pivot [0, -0.5] or an offset): a single smooth teardrop, widest a third of the way up,
    a soft dim root that stands a little off the wick (the blue zone reads as nearly nothing), a slightly brighter
    inner cone, the tip leaning and narrowing per variant. Greyscale _e (tinted by data). Headroom under the cell top.
    prm: height (tip as a fraction of the cell), width (half-width at the widest), lean (max tip offset, -1..1 units)."""
    x, y = grid(n)
    v = (y + 1) / 2                                         # 0 = bottom edge, 1 = top
    H = prm.get("height", 0.82) * rng.uniform(0.86, 1.0)
    w = prm.get("width", 0.2) * rng.uniform(0.9, 1.1)
    lean = prm.get("lean", 0.12) * rng.uniform(-1, 1)
    lo = 0.08                                               # root gap above the wick
    t = np.clip((v - lo) / max(1e-3, H - lo), 0, 1)
    xc = lean * t ** 2                                      # the tip leans, the root stays put
    prof = w * (np.maximum(0, np.sin(np.pi * np.clip(0.15 + 0.85 * t, 0, 1))) ** 0.5) * (1 - t) ** 0.35 + 1e-3
    nz = value_noise(n, 3, np.random.default_rng(prm["_seed"] + 31 + k), 3)
    xw = x + (nz - 0.5) * 0.08 * t                           # a faint flutter, not ragged licks
    body = np.exp(-((xw - xc) / prof) ** 2 * 2.0) * smoothstep(lo - 0.02, lo + 0.1, v) * (1 - smoothstep(H * 0.9, H, v))
    inner = np.exp(-((xw - xc) / (prof * 0.55)) ** 2 * 2.0) * smoothstep(0.15, 0.35, t) * (1 - smoothstep(0.55, 0.8, t))
    root = 0.18 + 0.82 * smoothstep(0.04, 0.38, t)          # the base is dim (blue zone, ~15% of the height), the middle bright
    tip = 1 - 0.3 * smoothstep(0.6, 1.0, t)                 # the tip cools a little
    lum = np.clip(body * root * tip * (0.75 + 0.25 * inner), 0, 1)
    lum = cv2.GaussianBlur(lum, (0, 0), max(0.7, n / 160)) * edge_fade(n, 0.05)
    a = np.clip(lum * 1.2, 0, 1)
    return rgba(np.clip(0.62 + 0.23 * lum ** 0.6, 0, 0.85), a)   # a narrow RGB range: the coverage carries the shape, so the bled edge is not dark


PAINTERS.update({"lighter_flame": p_lighter_flame})


# --------------------------------------------------------------------------- casings and aerosol mist (irongrip round 4: vfx_shell_*,
# vfx_case_revolver, vfx_paint_stream). Registered with PAINTERS.update below.

# side profiles, long axis vertical, mouth UP, base (rim) DOWN; (t along the length 0..1, radius as a fraction of the length)
_CASE_PROFILES = {
    "pistol":   [(0.0, 0.26), (0.04, 0.26), (0.06, 0.23), (0.10, 0.258), (1.0, 0.252)],                  # 9x19: rimless, extractor groove
    "rifle":    [(0.0, 0.134), (0.04, 0.134), (0.045, 0.116), (0.70, 0.108), (0.76, 0.079), (1.0, 0.079)],  # 7.62x54R: rim, taper, shoulder, neck
    "revolver": [(0.0, 0.142), (0.04, 0.142), (0.045, 0.127), (0.94, 0.116), (1.0, 0.104)],               # 7.62x38R Nagant: long straight, crimped mouth
    "hull":     [(0.0, 0.158), (0.03, 0.158), (0.035, 0.146), (1.0, 0.146)],                              # 12 ga fired hull: brass head, paper tube
}


def p_casing_profile(n, k, nf, rng, prm):
    """a spent cartridge case seen side-on, one kind per cell from prm['cells'][k] (pistol | rifle | revolver | hull), long
    axis vertical and filling ~88% of the cell height, so data size_m = the case's real length. Brass (prm 'brass') with
    cylinder shading, a soot band at the mouth; the hull is a paper tube (prm 'hull') on a brass head (prm 'head_frac')."""
    kind = prm["cells"][k % len(prm["cells"])]; prof = _CASE_PROFILES[kind]
    x, y = grid(n); L = 0.88
    t = (y + L) / (2 * L)                                                # 0 = rim, 1 = mouth
    r = np.interp(np.clip(t, 0, 1), [p[0] for p in prof], [p[1] for p in prof]) * 2 * L
    inside = (np.abs(x) < r) & (t >= 0) & (t <= 1)
    m = cv2.GaussianBlur(inside.astype(np.float32), (0, 0), 0.6)
    u = np.clip(x / np.maximum(r, 1e-3), -1, 1)
    cyl = 0.55 + 0.35 * np.sqrt(np.clip(1 - u ** 2, 0, 1)) + 0.18 * np.exp(-((u + 0.35) / 0.18) ** 2)   # round body + a soft sheen
    brass = hexrgb(prm.get("brass", "#A8946A"))
    if kind == "hull":
        head = t < prm.get("head_frac", 0.22)
        col = np.where(head[..., None], brass[None, None], hexrgb(prm.get("hull", "#6A5A48"))[None, None])
        cyl = np.where(head, cyl, 0.75 + 0.25 * cyl)                     # paper is matt
        crimp = smoothstep(0.9, 1.0, t) * 0.3                             # the opened star crimp at the mouth reads darker
        col = col * (1 - crimp)[..., None]
    else:
        col = np.repeat(brass[None, None], n, 0).repeat(n, 1)
        col = col * (1 - 0.45 * smoothstep(0.86, 1.0, t))[..., None]    # powder soot at the mouth
    col = col * cyl[..., None] * (0.94 + 0.12 * (value_noise(n, 6, rng, 2) - 0.5))[..., None]
    return rgba(np.clip(col, 0, 0.92), m * edge_fade(n, 0.04))


def p_droplet_mist(n, k, nf, rng, prm):
    """aerosol paint mist (camera billboard, random-frame variants): a faint soft haze with a peppering of fine droplets
    that thins toward the edge, so a cone of these reads as atomised liquid rather than smoke. Neutral near-white _bc,
    tinted by data. prm: haze (peak haze alpha), dots (droplet count), dot_px (droplet radius in px)."""
    x, y = grid(n); r2 = x ** 2 + y ** 2
    nz = value_noise(n, 4, rng, 3)
    haze = np.exp(-(r2 / prm.get("haze_r2", 0.22)) ** prm.get("haze_power", 1.0)) * (0.75 + 0.5 * (nz - 0.5)) * prm.get("haze", 0.35)   # haze_power 2: flat-topped (no bright centre dot)
    dots = np.zeros((n, n), np.float32); nd = int(prm.get("dots", 70) * rng.uniform(0.8, 1.2))
    rr = np.abs(rng.normal(0, 0.38, nd)); aa = rng.random(nd) * np.pi * 2
    for i in range(nd):
        if rr[i] > 0.8: continue
        cx = int((0.5 + 0.5 * rr[i] * np.cos(aa[i])) * n); cy = int((0.5 + 0.5 * rr[i] * np.sin(aa[i])) * n)
        rad = max(0.6, prm.get("dot_px", 1.2) * rng.uniform(0.6, 1.4))
        cv2.circle(dots, (cx, cy), int(round(rad)), float(rng.uniform(0.35, 0.7)), -1, lineType=cv2.LINE_AA)
    dots = cv2.GaussianBlur(dots, (0, 0), prm.get("dot_blur", 0.9))
    a = np.clip(haze + dots * (1 - 0.5 * smoothstep(0.2, 0.7, r2)), 0, 1) * edge_fade(n, 0.06)
    lum = 0.82 + 0.06 * (nz - 0.5)
    return rgba(np.clip(lum, 0, 0.88), a)


PAINTERS.update({"casing_profile": p_casing_profile, "droplet_mist": p_droplet_mist})


# --------------------------------------------------------------------------- screens, rain, wet (irongrip round 5: vfx_screen_static,
# vfx_crt_roll, vfx_rain, vfx_wet). Tiling shader textures set "tiling": true in sheets.json (grid [1, 1], A = 1 or data, no
# bleed, seamless: every painter wraps). Rain cells are ordinary particle cells.

def _wrap_blur(f, sx, sy):
    """separable gaussian-ish blur with wrap-around (keeps a tile seamless)."""
    out = f.copy()
    for ax, s in ((1, sx), (0, sy)):
        if s <= 0: continue
        acc = np.zeros_like(out); w = 0.0
        for d in range(-s, s + 1):
            k = np.exp(-0.5 * (d / max(0.5, s / 2.0)) ** 2); acc += k * np.roll(out, d, ax); w += k
        out = acc / w
    return out


def _norm01(f, lo=1, hi=99):
    a, b = np.percentile(f, lo), np.percentile(f, hi); return np.clip((f - a) / max(1e-6, b - a), 0, 1)


def p_screen_noise(n, k, nf, rng, prm):
    """analog snow, tiling (grid [1, 1]): per-pixel white noise mixed with horizontally smeared noise (a tube's beam
    smears along the line) and a faint line-to-line brightness wobble. Greyscale in RGB, A = 1."""
    w = rng.random((n, n)).astype(np.float32)
    smear = _norm01(_wrap_blur(rng.random((n, n)).astype(np.float32), prm.get("smear_px", 3), 0))
    line = np.repeat(rng.random((n, 1)).astype(np.float32), n, 1)
    s = prm.get("smear", 0.35)
    f = (0.9 - s) * w + s * smear + 0.1 * line
    f = _norm01(f, 0.5, 99.5) * (prm.get("hi", 0.92) - prm.get("lo", 0.04)) + prm.get("lo", 0.04)
    return rgba(f, np.ones((n, n), np.float32))


def p_screen_scanline(n, k, nf, rng, prm):
    """scanline profile, tiling: prm['lines'] lines per tile, 1 at a line's centre, ~0 in the gap (raised cosine with a
    flat top, so a blend weight of 0.15-0.3 reads as a tube's line structure, not a venetian blind). A = 1."""
    yy = (np.arange(n, dtype=np.float32) + 0.5) / n * prm.get("lines", 16)
    ph = yy - np.floor(yy)
    prof = np.clip(0.5 + 0.5 * np.cos((ph - 0.25) * 2 * np.pi) * 1.25, 0, 1) ** prm.get("gamma", 0.8)   # centre at 0.25: 2 px/line -> 1 bright, 1 dark
    f = np.repeat(prof[:, None], n, 1) * 0.92
    return rgba(f, np.ones((n, n), np.float32))


def p_screen_roll(n, k, nf, rng, prm):
    """vertical-hold roll band, tiling in y (sampled at uv.y + time * roll_speed): mostly ~1 (no effect), a soft dark
    blanking bar prm['bar'] tall (fraction of the tile), a thin brighter retrace line under it and a ragged edge (the bar is
    not a ruler). Greyscale, A = 1; the shader multiplies the picture by mix(1, roll / 0.92, roll_strength)."""
    v = (np.arange(n, dtype=np.float32) + 0.5) / n
    bar, soft = prm.get("bar", 0.07), prm.get("soft", 0.035)
    d = np.abs(v - 0.5)
    dark = 1 - smoothstep(bar / 2, bar / 2 + soft, d)                        # 1 inside the bar
    retr = np.exp(-((v - (0.5 + bar / 2 + soft * 0.6)) / prm.get("retrace_w", 0.006)) ** 2)
    prof = 1 - prm.get("bar_depth", 0.85) * dark + prm.get("retrace", 0.08) * retr
    f = np.repeat(prof[:, None], n, 1)
    jit = _norm01(_wrap_blur(rng.random((n, n)).astype(np.float32), 6, 1)) - 0.5
    f = f + prm.get("ragged", 0.10) * jit * np.repeat(np.clip(dark + retr, 0, 1)[:, None], n, 1)
    f = np.clip(f / max(1.0, float(f.max())) * 0.92, 0.0, 0.92)
    return rgba(f, np.ones((n, n), np.float32))


def p_ripple_data(n, k, nf, rng, prm):
    """rain-ripple DATA tile (Lagarde 2012 water-drop layout), seamless: drops at random positions (wrap-around), each a
    disc of radius r_px. R = 1 at the drop centre -> 0 at its rim (ring coordinate), G, B = direction from the centre
    (x right, y up) * 0.5 + 0.5, A = the drop's random time phase 0..1. Outside every drop: R 0, GB 0.5, A 0. Not a colour
    texture: sampled linear (no sRGB decode), see references/shader.md."""
    out = np.zeros((n, n, 4), np.float32); out[..., 1:3] = 0.5
    yy, xx = np.mgrid[0:n, 0:n].astype(np.float32) + 0.5
    for i in range(int(prm.get("drops", 48))):
        cx, cy = rng.random() * n, rng.random() * n; r = prm.get("r_px", 22) * rng.uniform(0.7, 1.15); ph = rng.random()
        dx = (xx - cx + n / 2) % n - n / 2; dy = (yy - cy + n / 2) % n - n / 2; d = np.sqrt(dx * dx + dy * dy)
        R = np.clip(1 - d / r, 0, 1); m = R > out[..., 0]
        out[..., 0][m] = R[m]; out[..., 1][m] = (dx / np.maximum(d, 1e-3) * 0.5 + 0.5)[m]
        out[..., 2][m] = (-dy / np.maximum(d, 1e-3) * 0.5 + 0.5)[m]; out[..., 3][m] = ph
    return out


def p_rain(n, k, nf, rng, prm):
    """light-rain atlas (neutral near-white _bc, tinted by data). Cells < prm['streaks']: a falling drop as the eye sees it
    (motion-blurred), for a STRETCHED billboard: a hair-thin soft line, head at the TOP, tail fading down; widths vary.
    Remaining cells: a ground splash over its life (frame k: a flat ring that widens plus a tiny crown of droplets that
    rises and falls), for a camera billboard with its BASE near the BOTTOM of the cell."""
    x, y = grid(n); ns = int(prm.get("streaks", 8)); lum = prm.get("lum", 0.84)
    if k < ns:
        w = prm.get("width", 0.06) * rng.uniform(0.7, 1.3)
        t = (0.9 - y) / 1.8                                    # 0 at the head (top) -> 1 at the tail
        along = np.where(t < 0, np.exp(-(t / 0.02) ** 2), np.clip(1 - t, 0, 1) ** 1.6) * (t < 1.0)
        a = np.exp(-(x / w) ** 2) * along * prm.get("alpha", 0.9)
        return rgba(np.full((n, n), lum, np.float32), np.clip(a, 0, 1) * edge_fade(n, 0.06))
    j = k - ns; nj = max(1, nf - ns); u = (j + 0.5) / nj
    a = np.zeros((n, n), np.float32); base = -0.7
    rr = 0.18 + 0.7 * u; ell = np.sqrt((x / rr) ** 2 + ((y - base) / (rr * 0.22)) ** 2)
    a += np.exp(-((ell - 1) / 0.18) ** 2) * (1 - u) ** 1.2 * 0.55
    lr = np.random.default_rng(prm["_seed"] + 101); nc = int(prm.get("crown", 5))
    for i in range(nc):
        ang = (i + 0.5) / nc * np.pi + lr.uniform(-0.2, 0.2)
        vx = np.cos(ang) * lr.uniform(0.5, 0.9); vy = lr.uniform(1.6, 2.3)
        px = vx * u * 0.9; py = base + vy * u - 2.2 * u * u
        if py < base - 0.02: continue
        rad = 0.05 * (1 - 0.5 * u)
        a += np.exp(-(((x - px) ** 2 + (y - py) ** 2) / rad ** 2)) * (1 - u) * 0.9
    a = a / max(1e-3, float(a.max())) * prm.get("splash_peak", 0.8)   # every frame carries full coverage: the fade is the data's colour keys
    return rgba(np.full((n, n), lum, np.float32), np.clip(a, 0, 1) * edge_fade(n, 0.06))


PAINTERS.update({"screen_noise": p_screen_noise, "screen_scanline": p_screen_scanline, "screen_roll": p_screen_roll,
                 "ripple_data": p_ripple_data, "rain": p_rain})


# --------------------------------------------------------------------------- structured flame tongue (irongrip round 6: vfx_fire_barrel)

def p_flame_lick(n, k, nf, rng, prm):
    """ONE readable flame tongue over its life (frame k of nf) for a camera billboard with its BASE at the BOTTOM of the
    cell (data pivot [0, -0.5]): a rounded root that narrows to a pointed, leaning tip with a crisp (anti-aliased) ragged
    edge, plus a shorter side lick forking off low on the other side; it rises, licks sideways, then its tip pinches off as
    a separate tuft that climbs and thins (the flicker) while the root sinks back. Luminance carries the structure (data
    tints it): hottest in a band just above the root (the fuel), cooler and darker toward the tips and edges (sooty tips:
    dark on pale daylight, which is what makes a tongue read on a grey CCTV feed). Same noise every frame, scrolled up with
    age (continuous flipbook). Greyscale _e. prm: height (tip as a fraction of the cell), width (root half-width), lean
    (max tip lean), soot (tip darkening 0..1), crisp (edge width in density units; smaller = sharper), fork (side-lick
    height as a fraction of the main tongue, 0 = none), cool (value drop toward the tips; the data keys can carry the
    colour shift instead), tuft_rag (raggedness of the pinched-off tuft), tuft (its strength, 0 = none), floor (lowest
    luminance anywhere in the tongue), age_cool (value drop over life; keeps first and last cells distinct), bed (extra
    shorter tongues spread across the root, 0 = none: a burning-puddle carpet chunk with several tips), bed_h ([min, max]
    height of those as a fraction of the main tongue), bed_w (their root half-width as a fraction of width), bed_span (half-width of the root they spread over; default 0.85 x width)."""
    age = k / max(1, nf - 1)
    s0 = prm["_seed"]
    x, y = grid(n)
    v = (y + 1) / 2                                          # 0 = bottom edge (the fuel), 1 = top
    na = value_noise(n, 2.5, np.random.default_rng(s0 + 41), 4)
    nb = value_noise(n, 6, np.random.default_rng(s0 + 42), 3)
    lift = age * n * 0.6
    na_s = warp_sample(na, np.zeros_like(na), np.full_like(na, lift))
    nb_s = warp_sample(nb, np.zeros_like(nb), np.full_like(nb, lift * 1.7))
    Hmax = prm.get("height", 0.86); W = prm.get("width", 0.42); crisp = prm.get("crisp", 0.12)
    lean = prm.get("lean", 0.3) * np.sin(1.1 + age * 4.4)   # the tip licks one way, then back
    lo = 0.04
    xw = x + (nb_s - 0.5) * 0.16 * (0.3 + v)

    def tongue(H, ox, ln, w):
        t = np.clip((v - lo) / max(1e-3, H - lo), 0, 1.2)
        xc = ox * (1 - 0.5 * t) + ln * t ** 1.5 + (na_s - 0.5) * 0.22 * t
        hw = w * np.clip(1 - t, 0, 1) ** 0.8 * (0.62 + 0.38 * np.sin(np.pi * np.clip(0.15 + t * 1.6, 0, 1))) + 1e-3   # rounded root, widest a quarter up
        d = 1 - np.abs(xw - xc) / hw + (na_s - 0.5) * 0.55 * t + (nb_s - 0.5) * 0.25 * (0.4 + t)
        return np.where(t >= 1.0, -1.0, d)

    grow = 0.55 + 0.45 * smoothstep(0.0, 0.3, age)
    sink = 1.0 - 0.4 * smoothstep(0.5, 1.0, age)
    H = Hmax * grow * sink
    dens = tongue(H, 0.0, lean, W * (1.0 - 0.15 * age))
    fk = prm.get("fork", 0.55)
    if fk > 0:
        dens = np.maximum(dens, tongue(lo + (H - lo) * fk * (0.8 + 0.2 * np.sin(age * 7.0)), -np.sign(lean + 1e-6) * W * 0.45, -lean * 0.8, W * 0.55))
    nb_t = int(prm.get("bed", 0))                             # extra tongues across a wide root (a flame carpet chunk)
    if nb_t > 0:
        brng = np.random.default_rng(s0 + 51)
        bh = prm.get("bed_h", [0.45, 0.8]); bw = prm.get("bed_w", 0.35)
        for i in range(nb_t):
            sp = prm.get("bed_span", W * 0.85)
            ox = sp * (-1.0 + 2.0 * (i + 0.5 + brng.uniform(-0.3, 0.3)) / nb_t)
            hf = brng.uniform(bh[0], bh[1]) * (0.85 + 0.15 * np.sin(age * 6.0 + i * 2.1))
            ln = prm.get("lean", 0.3) * 0.6 * np.sin(brng.uniform(0, 6.28) + age * 4.0)
            dens = np.maximum(dens, tongue(lo + (H - lo) * hf, ox, ln, W * bw * brng.uniform(0.8, 1.2)))
    # the pinched-off tuft: a lens of flame above the neck, climbing and thinning in the second half of life
    tq = prm.get("tuft", 1.0) * smoothstep(0.42, 0.62, age) * (1 - smoothstep(0.85, 1.0, age))
    if tq > 0:
        hc = H + 0.06 + 0.32 * (age - 0.42); rr = 0.09 * (1.0 - 0.4 * age) + 0.03
        tx = lean * 1.1 + (na_s - 0.5) * 0.2
        tuft = 1 - np.sqrt(((xw - tx) / (rr * 0.8)) ** 2 + ((v - hc) / (rr * 1.5)) ** 2) + (nb_s - 0.5) * prm.get("tuft_rag", 0.4)
        dens = np.maximum(dens, tuft * tq)
    root = smoothstep(lo - 0.01, lo + 0.08 + 0.1 * na_s, v)  # a ragged, rounded root, no straight cut
    a = smoothstep(0.0, crisp, dens) * root * edge_fade(n, 0.05)
    a = cv2.GaussianBlur(a.astype(np.float32), (0, 0), max(0.6, n / 200))
    # luminance (the light structure): bright interior, hottest in a band just above the root, cooling up the tongue
    # toward ~0.55 at the tips; darker skin at the edges; vertical hot streaks inside (stretched noise, scrolled up)
    T = np.clip(v / max(1e-3, Hmax), 0, 1.4)
    heat = 1.0 - prm.get("cool", 0.3) * smoothstep(0.55, 1.0, T) - prm.get("soot", 0.35) * smoothstep(0.75, 1.1, T)   # full value over the lower two-thirds; cool: tip darkening
    heat *= 0.8 + 0.2 * smoothstep(0.0, 0.6, dens)            # slightly cooler skin, brighter heart (a rounded, volumetric read)
    st = cv2.resize(value_noise(n, 9, np.random.default_rng(s0 + 43), 2), (n, n * 4), interpolation=cv2.INTER_LINEAR)
    st = warp_sample(np.ascontiguousarray(st[:n]), (na_s - 0.5) * n * 0.06, np.full_like(na_s, lift * 1.2))
    heat *= 0.86 + 0.28 * (st - 0.5) * smoothstep(0.1, 0.5, dens)   # hot inner streaks
    heat *= 1.0 - prm.get("age_cool", 0.1) * age            # the tongue cools as it rises
    lum = np.clip(heat, prm.get("floor", 0.6), 0.98)   # floor: cool tips stay flame-coloured, not burnt paper
    return rgba(lum.astype(np.float32), np.clip(a, 0, 1))


PAINTERS.update({"flame_lick": p_flame_lick})


# --------------------------------------------------------------------------- ember dot (irongrip round 6: vfx_smoke_cigarette)

def p_ember_dot(n, k, nf, rng, prm):
    """a tiny glowing coal seen from anywhere (random-frame variants, camera billboard): a HARD round core at full coverage
    (radius prm core, so a 2-4 px sprite still keeps a solid pixel), a short soft rim, slightly irregular per variant, hot
    in the centre and a little cooler at the rim. Greyscale _e (tinted by data); the same cells serve the alpha ember and
    its additive halo (bigger sprite). prm: core (core radius, cell units), rim (rim width), hole (> 0: a ring for a halo
    that must not stack on the coal)."""
    x, y = grid(n)
    ang = np.arctan2(y, x); r = np.sqrt(x * x + y * y)
    wob = 1 + 0.08 * np.sin(3 * ang + rng.uniform(0, 6.3)) + 0.05 * np.sin(5 * ang + rng.uniform(0, 6.3))
    core = prm.get("core", 0.32) * wob; rim = prm.get("rim", 0.3)
    a = 1 - smoothstep(core, core + rim, r)
    if prm.get("hole", 0) > 0: a = a * smoothstep(prm["hole"] * 0.5, prm["hole"], r)   # a halo ring: no additive pile-up on the coal itself
    lum = 0.72 + 0.26 * (1 - smoothstep(0.0, core + rim * 0.5, r))
    return rgba(np.clip(lum, 0, 0.98).astype(np.float32), np.clip(a, 0, 1) * edge_fade(n, 0.06))


def p_soft_streak(n, k, nf, rng, prm):
    """a soft vertical streak for a STRETCHED billboard (velocity-aligned, head at the top): a gaussian core across
    (width), smooth round ends, a faint lengthwise flutter per variant; overlapping streaks merge into one continuous laminar
    column (a cigarette's first hand-width of smoke) with no beads. Neutral _bc (value ~0.86, tinted by data).
    prm: width (gaussian sigma across, cell units), alpha (peak)."""
    x, y = grid(n)
    w = prm.get("width", 0.22) * rng.uniform(0.85, 1.15)
    xs = x + 0.05 * np.sin(y * rng.uniform(2, 4) + rng.uniform(0, 6.3))
    a = np.exp(-(xs / w) ** 2) * (1 - smoothstep(0.55, 0.92, np.abs(y))) * prm.get("alpha", 0.9)
    return rgba(np.full((n, n), 0.86, np.float32), np.clip(a, 0, 1) * edge_fade(n, 0.06))


PAINTERS.update({"ember_dot": p_ember_dot, "soft_streak": p_soft_streak})


# --------------------------------------------------------------------------- gas bank (vfx_teargas)

def p_gas_bank(n, k, nf, rng, prm):
    """a broad, low-contrast slab of settled gas (random-frame variants) that FILLS its quad: a flat, dense underside
    that meets the ground, a soft domed top and sides broken by slow curls and wisps. Unlike soft_billow (a lump in the
    middle of the cell) the opaque core spans ~80% of the cell, so a few large overlapping quads read as ONE continuous
    mass with no pillow gaps, and the quad count (overdraw) stays low. Peak alpha ~0.95: the data sets the opacity.
    Neutral _bc (value ~0.86, tinted by data), faintly lit from above.
    prm: width (half extent of the core, 0..1), top (dome height above centre), floor (y where the flat underside sits),
         curl (edge warp strength), wisp (edge tendril amount), alpha (peak), margin (width of the fade to the cell border)."""
    x, y = grid(n)
    w = prm.get("width", 0.84) * rng.uniform(0.9, 1.0)
    top = prm.get("top", 0.55) * rng.uniform(0.8, 1.1)
    floor = prm.get("floor", -0.72)
    # large-scale domain warp: slow curls along the top and the sides
    na = value_noise(n, 2.5, rng, 3); nb = value_noise(n, 2.5, rng, 3)
    cu = prm.get("curl", 0.16)
    xw = x + (na - 0.5) * cu * 2; yw = y + (nb - 0.5) * cu * 1.6
    # an ellipse (soft falloff ~ the outer third) cut flat underneath: domed top, rounded ends, a level underside
    cy = floor + 0.25
    h = (top - cy) * (0.85 + 0.3 * value_noise(n, 2.0, rng, 2))           # the dome swells and dips along its length
    r = np.sqrt((xw / w) ** 2 + (np.maximum(0, yw - cy) / h) ** 2)
    d = 1 - smoothstep(0.42, 1.18, r)
    d *= smoothstep(floor - 0.2, floor + 0.06, yw)                         # flat underside, sits on the ground
    # edge wisps: finer noise eats into / pushes out of the boundary band only, the core stays solid
    nf_ = value_noise(n, 5, rng, 3)
    band = 4 * d * (1 - d)
    d = np.clip(d + band * (nf_ - 0.5) * prm.get("wisp", 0.6), 0, 1)
    # low-contrast interior: a gentle density variation, never holes
    nl = value_noise(n, 3.5, rng, 3)
    d = d * (0.86 + 0.14 * nl)
    d = cv2.GaussianBlur(d, (0, 0), max(1.0, n / 40))
    # a WIDE window to the cell border (the warp pushes density outward): these quads are metres across and cross the camera
    # and the ground, so any alpha left near a border reads as a straight quad edge inside the cloud
    mg = prm.get("margin", 0.3)
    win = smoothstep(0.99, 0.99 - mg, np.abs(x)) * smoothstep(-0.99, -0.99 + mg, y) * smoothstep(0.99, 0.99 - mg, y)
    a = np.clip(d, 0, 1) * prm.get("alpha", 0.95) * win * edge_fade(n, 0.05)
    shade = shade_from_density(d, light=(-0.3, 0.9), strength=0.12, blur=max(2.0, n / 16))
    vert = 0.95 + 0.05 * smoothstep(floor, top, y)                        # underside a touch darker
    lum = np.clip((0.86 + 0.14 * (shade - 1.0) + 0.02 * (nl - 0.5)) * vert, 0, 0.95)
    return rgba(lum, a)


PAINTERS.update({"gas_bank": p_gas_bank})


# --------------------------------------------------------------------------- sheets

def paint_sheet(name, cfg):
    size = int(cfg["size"]); cols, rows = cfg["grid"]; cw, ch = size // cols, size // rows
    if cw != ch: raise SystemExit("%s: cells must be square" % name)
    rng = np.random.default_rng(cfg.get("seed", 1)); prm = dict(cfg.get("params", {})); prm["_seed"] = cfg.get("seed", 1) * 7919
    fn = PAINTERS[cfg["painter"]]; nf = cols * rows
    sheet = np.zeros((size, size, 4), np.float32)
    for k in range(nf):
        cell = fn(cw, k, nf, rng, prm)
        if name.endswith("_e"): cell[..., :3] = np.repeat(np.clip(cell[..., :3].mean(-1, keepdims=True), 0, 1), 3, -1)
        if not cfg.get("tiling"): cell = bleed(cell)   # tiling shader textures (class shader) carry data in A: no bleed
        r, c = divmod(k, cols)
        sheet[r * ch:(r + 1) * ch, c * cw:(c + 1) * cw] = cell
    return sheet


def save_rgba(arr, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    u8 = (np.clip(arr, 0, 1) * 255 + 0.5).astype(np.uint8)
    cv2.imwrite(path, cv2.cvtColor(u8, cv2.COLOR_RGBA2BGRA))


def contact_sheet(paths, out, bg=(0.35, 0.37, 0.37)):
    """preview: every sheet over a mid grey and over black, labelled (review/vfx_sheets.png)."""
    from PIL import Image, ImageDraw
    tiles = []
    for p in paths:
        im = cv2.cvtColor(cv2.imread(p, cv2.IMREAD_UNCHANGED), cv2.COLOR_BGRA2RGBA).astype(np.float32) / 255
        im = cv2.resize(im, (384, 384), interpolation=cv2.INTER_AREA)
        a = im[..., 3:4]
        grey = im[..., :3] * a + np.array(bg)[None, None] * (1 - a)
        blk = im[..., :3] * a
        pair = np.concatenate([grey, blk], 1)
        t = Image.fromarray((np.clip(pair, 0, 1) * 255).astype(np.uint8)); d = ImageDraw.Draw(t)
        d.text((6, 6), os.path.basename(p), fill=(230, 226, 214)); tiles.append(t)
    W = 768; H = 384 * len(tiles); sheet = Image.new("RGB", (W, H), (27, 28, 28))
    for i, t in enumerate(tiles): sheet.paste(t, (0, i * 384))
    sheet.save(out); return out


def paint_all(sheets_cfg, out_dir, names=None):
    done = []
    for name, cfg in sheets_cfg.items():
        if names and name not in names: continue
        arr = paint_sheet(name, cfg); p = os.path.join(out_dir, name + ".png"); save_rgba(arr, p); done.append(p)
        print("TEX %s %dx%d grid %dx%d (%s)" % (name, cfg["size"], cfg["size"], cfg["grid"][0], cfg["grid"][1], cfg["painter"]))
    return done


if __name__ == "__main__":
    import sys
    cfg = json.load(open(sys.argv[1]))["sheets"]; paint_all(cfg, sys.argv[2], sys.argv[3:] or None)
