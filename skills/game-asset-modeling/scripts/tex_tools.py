"""
tex_tools.py -- texture authoring helpers for game-asset-modeling (plain CPython: numpy,
opencv, pillow). Import from a project texture-generator script:

    import os, sys; sys.path.insert(0, os.path.expanduser("~/.claude/skills/game-asset-modeling/scripts"))
    from tex_tools import *

Everything that must tile is done periodically (FFT), because opencv's blur has no wrap
border and a non-periodic blur puts a seam in every repeat.
Images are float32 arrays in [0, 1], HxWx3 (or HxW), row 0 = top of the image.
"""
import datetime, io, json, math, os, urllib.request, zipfile
import numpy as np

UA = {"User-Agent": "game-asset-modeling/1.0 (+asset pipeline)"}   # Poly Haven 403s Python's default UA
CACHE = os.path.expanduser(os.environ.get("GAM_CC0_CACHE", "~/.cache/game-asset-modeling/cc0"))


# --------------------------------------------------------------------------- io

def load(path, size=None):
    import cv2
    a = cv2.imread(path, cv2.IMREAD_UNCHANGED)
    if a is None: raise FileNotFoundError(path)
    if a.ndim == 3 and a.shape[2] == 4: a = a[..., [2, 1, 0, 3]]           # BGRA -> RGBA
    elif a.ndim == 3: a = a[..., :3][..., ::-1]
    a = a.astype(np.float32) / (65535.0 if a.dtype == np.uint16 else 255.0)
    if size: a = cv2.resize(a, size, interpolation=cv2.INTER_AREA)
    return a


def save(path, a):
    """PNG, 8-bit. RGB or gray. Power-of-two is the caller's job (see pot())."""
    import cv2
    a = np.clip(a, 0, 1); os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    out = (a * 255 + 0.5).astype(np.uint8)
    if out.ndim == 3 and out.shape[2] == 4: out = out[..., [2, 1, 0, 3]]      # RGBA -> BGRA (keep alpha last)
    elif out.ndim == 3: out = out[..., ::-1]                                 # RGB -> BGR
    cv2.imwrite(path, out)
    return path


def pot(a, size):
    """Resize to size x size (power of two), area filter."""
    import cv2
    return cv2.resize(a, (size, size), interpolation=cv2.INTER_AREA)


# --------------------------------------------------------------------------- CC0 sources

def fetch_polyhaven(asset, maps=("Diffuse", "nor_gl", "arm"), res="2k", fmt="png", cache=CACHE):
    """Download Poly Haven texture maps (cached OUTSIDE the project; raw sources are large and
    don't belong in assets/). Returns {map: path}. 'arm' = AO/rough/metal = glTF ORM order.
    Always nor_gl (OpenGL), never nor_dx."""
    d = os.path.join(cache, "polyhaven", asset); os.makedirs(d, exist_ok=True)
    out = {}; files = None
    for k in maps:
        f = os.path.join(d, "%s_%s_%s.%s" % (asset, k.lower(), res, fmt))
        if os.path.exists(f) and os.path.getsize(f) < 1024:
            os.remove(f)                                     # failed download cached as an empty file
        if not os.path.exists(f):
            if files is None:
                files = json.load(urllib.request.urlopen(urllib.request.Request("https://api.polyhaven.com/files/" + asset, headers=UA), timeout=60))
            url = files[k][res][fmt]["url"]
            data = urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=120).read()
            if len(data) < 1024: raise IOError("download too small (%d bytes): %s" % (len(data), url))
            open(f + ".part", "wb").write(data); os.replace(f + ".part", f)
        out[k] = f
    return out


def fetch_ambientcg(asset, res="2K", fmt="PNG", cache=CACHE):
    """Download + unzip an ambientCG material. Returns {Color|NormalGL|Roughness|AmbientOcclusion|Metalness|Displacement|Opacity: path}."""
    d = os.path.join(cache, "ambientcg", asset); os.makedirs(d, exist_ok=True)
    if not any(f.endswith("." + fmt.lower()) for f in os.listdir(d)):
        url = "https://ambientcg.com/get?file=%s_%s-%s.zip" % (asset, res, fmt)
        z = zipfile.ZipFile(io.BytesIO(urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=300).read()))
        z.extractall(d)
    out = {}
    for f in os.listdir(d):
        for k in ("Color", "NormalGL", "Roughness", "AmbientOcclusion", "Metalness", "Displacement", "Opacity"):
            if "_%s." % k in f: out[k] = os.path.join(d, f)
    return out


def license_row(licenses_md, asset_id, url, license="CC0", author="", notes=""):
    """Append a LICENSES.md table row (idempotent on asset_id + url)."""
    row = "| %s | %s | %s | %s | %s | %s |\n" % (asset_id, url, license, author, datetime.date.today().isoformat(), notes)
    import fcntl
    with open(licenses_md + ".lock", "w") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        txt = open(licenses_md).read() if os.path.exists(licenses_md) else ""
        if "| %s | %s |" % (asset_id, url) not in txt:
            open(licenses_md, "a").write(row)


# --------------------------------------------------------------------------- periodic ops

def pnoise(h, w, feature_px, seed=0, octaves=4, aniso=(1.0, 1.0)):
    """Tileable fBm noise in [0,1] (FFT-filtered white noise). aniso scales the frequency axis
    it applies to: a SMALL x factor keeps fine horizontal detail -> features narrow in x, long
    in y. aniso=(0.2, 1) gives vertical streaks (drips, rain runs); (1, 0.2) horizontal bands."""
    rng = np.random.default_rng(seed)
    out = np.zeros((h, w), np.float32); amp = 1.0; tot = 0.0
    fy = np.fft.fftfreq(h)[:, None] * aniso[1]; fx = np.fft.fftfreq(w)[None, :] * aniso[0]
    r = np.sqrt(fx ** 2 + fy ** 2)
    for o in range(octaves):
        filt = np.exp(-0.5 * (r / ((2 ** o) / feature_px)) ** 2)
        n = np.real(np.fft.ifft2(np.fft.fft2(rng.standard_normal((h, w))) * filt)).astype(np.float32)
        out += amp * n / (n.std() + 1e-8); tot += amp; amp *= 0.5
    out /= tot
    return (out - out.min()) / (out.max() - out.min() + 1e-8)


def pblur(a, sigma):
    """Wrap-around Gaussian blur (keeps tiling textures seamless)."""
    h, w = a.shape[:2]
    g = np.exp(-2 * (np.pi * sigma) ** 2 * (np.fft.fftfreq(w)[None, :] ** 2 + np.fft.fftfreq(h)[:, None] ** 2))
    f = lambda c: np.real(np.fft.ifft2(np.fft.fft2(c) * g)).astype(np.float32)
    return f(a) if a.ndim == 2 else np.dstack([f(a[..., c]) for c in range(a.shape[2])])


def tile_to(a, w, h, ox=0, oy=0):
    """Tile a periodic image to w x h starting at pixel offset (ox, oy)."""
    ry = math.ceil((h + oy) / a.shape[0]) + 1; rx = math.ceil((w + ox) / a.shape[1]) + 1
    return np.tile(a, (ry, rx, 1) if a.ndim == 3 else (ry, rx))[oy:oy + h, ox:ox + w]


def make_tileable(a, blend_px=64):
    """Cross-fade opposite edges so a non-tiling photo repeats without a hard seam."""
    h, w = a.shape[:2]; b = a.copy()
    ramp = np.linspace(0, 1, blend_px, dtype=np.float32)
    rr = ramp[None, :, None] if a.ndim == 3 else ramp[None, :]
    b[:, :blend_px] = a[:, :blend_px] * rr + a[:, w - blend_px:] * (1 - rr)
    rc = ramp[:, None, None] if a.ndim == 3 else ramp[:, None]
    b[:blend_px] = b[:blend_px] * rc + b[h - blend_px:] * (1 - rc)
    return b


# --------------------------------------------------------------------------- grading / maps

def grade(rgb, desat=0.15, contrast=0.85, mean=None):
    """Style grade: desaturate, pull contrast toward the mean, optionally match a mean color
    (sRGB 0-1 triple, e.g. a palette swatch)."""
    lum = rgb.mean(-1, keepdims=True); rgb = lum + (rgb - lum) * (1 - desat)
    m = rgb.reshape(-1, 3).mean(0); rgb = m + (rgb - m) * contrast
    if mean is not None: rgb = rgb * (np.asarray(mean, np.float32) / (rgb.reshape(-1, 3).mean(0) + 1e-6))
    return np.clip(rgb, 0, 1)


def hex_srgb(h):
    h = h.lstrip("#"); return np.array([int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)], np.float32)


def height_to_normal(h, strength, wrap=True):
    """Height (0..1, rows top->bottom) -> OpenGL tangent normal, ENCODED (0..1)."""
    if wrap:
        dx = (np.roll(h, -1, 1) - np.roll(h, 1, 1)) * 0.5; dy = (np.roll(h, -1, 0) - np.roll(h, 1, 0)) * 0.5
    else:
        dx = np.gradient(h, axis=1); dy = np.gradient(h, axis=0)
    n = np.dstack([-dx * strength, dy * strength, np.ones_like(h)])     # +Y (green) = up the image
    n /= np.linalg.norm(n, axis=-1, keepdims=True)
    return n * 0.5 + 0.5


def normal_dx_to_gl(n):
    """Flip green: DirectX-convention normal map -> OpenGL."""
    n = n.copy(); n[..., 1] = 1 - n[..., 1]; return n


def blend_normals(a, b, wb=1.0):
    """Whiteout blend of two ENCODED normal maps; returns encoded."""
    A = a * 2 - 1; Bn = b * 2 - 1
    n = np.dstack([A[..., 0] + Bn[..., 0] * wb, A[..., 1] + Bn[..., 1] * wb, A[..., 2] * Bn[..., 2]])
    n /= np.linalg.norm(n, axis=-1, keepdims=True); return n * 0.5 + 0.5


def pack_orm(ao, rough, metal):
    """glTF ORM from three gray arrays or scalars (same size as the first array given)."""
    ref = next(x for x in (ao, rough, metal) if isinstance(x, np.ndarray))
    f = lambda x: np.full(ref.shape[:2], float(x), np.float32) if not isinstance(x, np.ndarray) else (x if x.ndim == 2 else x[..., 0])
    return np.dstack([f(ao), f(rough), f(metal)])


def check_albedo(rgb, lo=0.02, hi=0.94):
    """Fraction of texels outside the sRGB albedo limits (should be ~0)."""
    lum = rgb.mean(-1); return float(((lum < lo) | (lum > hi)).mean())
