# sdf_mesher.py — mesh a side-profile part as a smooth, rounded 3D solid ("inflate").
# Runs OFFLINE in regular CPython:  pip install numpy scipy scikit-image
#
# For molded / cast / organic parts (polymer frames, handles, grips, housings, bottles seen
# from the side) whose cross-section is a rounded rectangle that changes width and roundness
# along the part. Instead of extruding a slab and fighting it into shape, describe the part
# by per-pixel FIELDS on the side-view image and let marching cubes build the surface:
#
#   w  (mm)  full width of the part at that side-view point
#   r  (mm)  how far in from the outline the edge rounding reaches
#   p        superellipse exponent: 2 = round/elliptical edge, 4+ = boxy with a small fillet
#   dw (mm)  optional crisp recesses subtracted from w (grooves, pockets, parting lines)
#
# Near the outline the section is  (|y|/a)^p + v^p = 1,  a = w/2,  v = max(r - sd, 0)/r,
# with sd the signed 2D distance to the outline. That field is C1 across y = 0, so the
# silhouette equator comes out clean. (The obvious |y| - Y(x,z) field has a kink at y = 0
# and marching cubes turns it into a speckled seam — don't use it.)
#
# Edges that must be cut FLAT instead of rounded (where the part meets another part, an open
# end like a magwell) are handled with `round_mask`: a copy of the mask extended past those
# edges. Rounding is measured against round_mask; the final solid is intersected with the
# plain extruded `mask`, which leaves a flat cap there.
#
# Blend w/r/p between regions with a Gaussian (sigma ~ 2-3 mm in pixels) so the form
# transitions smoothly; keep dw sharp (sigma ~1 px) so recesses have crisp walls.
#
# Output: .npz with verts/faces in world meters; load it in Blender with import_npz.py,
# then decimate (0.25-0.5) and shade smooth. Bake recesses into dw rather than cutting them
# with booleans afterwards: booleans on the dense marching-cubes mesh come out ragged.

import numpy as np
from scipy import ndimage as nd
from skimage import measure

PAD = 120   # px of padding so rounding near the image border is well defined
K = 300.0   # 1/m, scales the flat-cap field to match G's gradient near the surface


def _signed(mask, ppm):
    return (nd.distance_transform_edt(mask) - nd.distance_transform_edt(~mask)) / ppm   # + inside


def mesh_part(mask, w_mm, r_mm, p, frame, round_mask=None, dw_mm=None,
              step=0.00028, ymax=None, out=None):
    """mask/w_mm/r_mm/p/(dw_mm, round_mask): (H, W) arrays aligned to the reference image.
    frame: trace_profile.Frame (or anything with ppm, cx, cy). step: voxel size in meters.
    Returns (verts, faces); writes out (.npz) if given."""
    ppm, cx, cy = frame.ppm, frame.cx, frame.cy
    H, W = mask.shape
    if dw_mm is not None:
        w_mm = w_mm - dw_mm
    if ymax is None:
        ymax = float(np.max(w_mm[mask])) / 2000 + 0.002
    m = np.pad(mask, PAD)
    rm = np.pad(round_mask, PAD, mode='edge') if round_mask is not None else m
    w_mm, r_mm, p = (np.pad(a, PAD, mode='edge') for a in (w_mm, r_mm, p))
    sd_round, sd_ext = _signed(rm, ppm), _signed(m, ppm)

    ys, xs = np.nonzero(mask)
    wx0, wz1 = (xs.min() - 6 - cx) / ppm, (cy - (ys.min() - 6)) / ppm
    wx1, wz0 = (xs.max() + 6 - cx) / ppm, (cy - (ys.max() + 6)) / ppm
    gx = np.arange(wx0, wx1, step); gz = np.arange(wz0, wz1, step); gy = np.arange(-ymax, ymax + step, step)
    GX, GZ = np.meshgrid(gx, gz, indexing='ij')
    coords = [(cy - GZ * ppm + PAD).ravel(), (GX * ppm + cx + PAD).ravel()]
    S = lambda A: nd.map_coordinates(A, coords, order=1).reshape(GX.shape).astype(np.float32)
    sdr, sde, a, r, pp = S(sd_round), S(sd_ext), S(w_mm) / 2000, S(r_mm) / 1000, S(p)

    v = np.maximum(r - sdr, 0) / np.maximum(r, 1e-5)
    ay = np.abs(gy)[None, :, None] / np.maximum(a, 1e-5)[:, None, :]
    G = ay ** pp[:, None, :] + (v ** pp)[:, None, :] - 1.0
    cap = (-(sde - 0.0003) * K)[:, None, :]
    F = np.maximum(G, cap)
    verts, faces, _, _ = measure.marching_cubes(F, 0.0, spacing=(step, step, step))
    verts += np.array([gx[0], gy[0], gz[0]])
    if out:
        np.savez(out, verts=verts.astype(np.float32), faces=faces.astype(np.int32))
    return verts, faces


def region_fields(shape, regions, default=(26.0, 2.2, 4.0), blur_px=28):
    """Build blended (w, r, p) fields from [(bool_region, (w_mm, r_mm, p)), ...]; later regions win.
    blur_px ~ 2-3 mm of image pixels gives smooth transitions between regions."""
    w = np.full(shape, default[0], np.float32); r = np.full(shape, default[1], np.float32); p = np.full(shape, default[2], np.float32)
    for reg, (wv, rv, pv) in regions:
        if wv is not None: w[reg] = wv
        if rv is not None: r[reg] = rv
        if pv is not None: p[reg] = pv
    return tuple(nd.gaussian_filter(x, blur_px) for x in (w, r, p))
