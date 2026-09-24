# trace_profile.py — turn a reference PHOTO/drawing into a filled silhouette mask and
# world-space profile contours. Runs OFFLINE in regular CPython (not inside Blender):
#   pip install numpy scipy pillow opencv-python-headless
#
# Typical flow for a side-view photo on a white/plain background:
#   m    = mask_from_photo("references/ref_side.png")            # filled, holes kept open
#   save_mask(m, "references/mask_side.png")                     # DARK-on-LIGHT, for silhouette_iou
#   fr   = Frame.from_span(m.shape, span_px=(175, 1905), span_m=0.186)   # calibrate on a known length
#   part = m & (rows(m) >= 221)                                  # split parts with row/col cuts
#   pts  = trace(part, fr, eps_px=1.5)                           # [(x, z), ...] in meters
#
# World mapping (the skill's default orientation): image x -> world +X, image y (down) -> world -Z,
# origin at the image centre unless you pass cx/cy. Put the reference empty at the same origin
# (add_ref_empty(..., location=(0, y_offset, 0))) and traced contours land exactly on it.

import numpy as np
from scipy import ndimage as nd
from PIL import Image

try:
    import cv2
except ImportError:  # contour tracing needs OpenCV; mask building does not
    cv2 = None


class Frame:
    """Pixel <-> world (meters) mapping for one reference image."""

    def __init__(self, shape, ppm, cx=None, cy=None):
        self.H, self.W = shape[:2]
        self.ppm = ppm
        self.cx = self.W / 2 if cx is None else cx
        self.cy = self.H / 2 if cy is None else cy

    @classmethod
    def from_span(cls, shape, span_px, span_m, **kw):
        """Calibrate from a known real dimension measured in pixels (x0, x1)."""
        return cls(shape, abs(span_px[1] - span_px[0]) / span_m, **kw)

    def to_world(self, px, py):
        return (px - self.cx) / self.ppm, (self.cy - py) / self.ppm

    def to_px(self, x, z):
        return x * self.ppm + self.cx, self.cy - z * self.ppm


def rows(m):
    return np.mgrid[0:m.shape[0], 0:m.shape[1]][0]


def cols(m):
    return np.mgrid[0:m.shape[0], 0:m.shape[1]][1]


def mask_from_photo(path, bg_thresh=215, min_hole_px=2000, open_iter=2):
    """Filled subject mask from a photo on a light plain background.

    Background = bright pixels connected to the image border. Enclosed bright regions larger
    than `min_hole_px` (e.g. a trigger-guard opening, a handle loop) stay OPEN; smaller ones
    (glare, printed text) are filled. Returns a bool (H, W) array, True = subject.
    """
    im = np.asarray(Image.open(path).convert("RGB")).astype(int)
    bg = im.min(axis=2) > bg_thresh
    lab, n = nd.label(bg)
    border = set(np.unique(np.r_[lab[0], lab[-1], lab[:, 0], lab[:, -1]])) - {0}
    outside = np.isin(lab, list(border))
    sizes = nd.sum(bg, lab, range(1, n + 1))
    holes = [i + 1 for i, s in enumerate(sizes) if s > min_hole_px and (i + 1) not in border]
    fg = ~outside & ~np.isin(lab, holes)
    fg = nd.binary_opening(fg, iterations=open_iter)
    l2, n2 = nd.label(fg)
    return l2 == (np.argmax(nd.sum(fg, l2, range(1, n2 + 1))) + 1)


def holes_of(mask, min_px=500):
    """Enclosed openings of a mask as a bool array (useful as a boolean-cutter mask)."""
    filled = nd.binary_fill_holes(mask)
    h = filled & ~mask
    lab, n = nd.label(h)
    sizes = nd.sum(h, lab, range(1, n + 1))
    return np.isin(lab, [i + 1 for i, s in enumerate(sizes) if s >= min_px])


def smooth_mask(mask, sigma_px):
    """Remove printed-texture bumps (stippling, knurling) from an outline. Apply per region:
    np.where(region, smooth_mask(m, 9), smooth_mask(m, 2.5))."""
    return nd.gaussian_filter(mask.astype(float), sigma_px) > 0.5


def save_mask(mask, path):
    Image.fromarray(np.where(mask, 0, 255).astype(np.uint8)).save(path)


def trace(mask, frame, eps_px=1.5, with_holes=False):
    """Outer contour (and optionally holes) of the largest component as world (x, z) lists.
    eps_px is the Douglas-Peucker tolerance: 1-2 px keeps curves, drops pixel stair-steps."""
    if cv2 is None:
        raise ImportError("trace() needs opencv-python-headless")
    mode = cv2.RETR_CCOMP if with_holes else cv2.RETR_EXTERNAL
    cs, hier = cv2.findContours(mask.astype(np.uint8), mode, cv2.CHAIN_APPROX_NONE)
    conv = lambda c: [frame.to_world(float(x), float(y)) for x, y in cv2.approxPolyDP(c, eps_px, True)[:, 0, :]]
    outer_i = max(range(len(cs)), key=lambda i: cv2.contourArea(cs[i]))
    if not with_holes:
        return conv(cs[outer_i])
    holes = [conv(c) for i, c in enumerate(cs) if hier[0][i][3] == outer_i and cv2.contourArea(c) > 50]
    return {"outer": conv(cs[outer_i]), "holes": holes}


def convex_opening(mask_of_opening):
    """Convex hull of an opening mask — swallows an inner element (a trigger in its guard) so the
    cutter is one clean hole and the inner element can be modelled separately."""
    cs, _ = cv2.findContours(mask_of_opening.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    hull = cv2.convexHull(np.vstack(cs))
    out = np.zeros(mask_of_opening.shape, np.uint8)
    cv2.fillPoly(out, [hull], 1)
    return out.astype(bool)
