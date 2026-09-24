# blender_ref.py — reusable helpers for reference-based modeling in Blender.
#
# These functions are meant to be run *inside Blender* via the blender MCP
# `execute_blender_code` tool. Each MCP call is an independent exec, and Blender
# does NOT reliably persist globals between calls, so the simplest robust pattern
# is: paste the function(s) you need at the top of the same code block that calls
# them. They are deliberately small and self-contained for that reason.
#
# CONVENTIONS (keep these consistent across a whole model):
#   * Work in real-world METERS. 1 Blender unit = 1 m. Set scene unit scale to mm
#     display if you like, but model at true size.
#   * Forward (muzzle / nose / front) points +X. Up is +Z. Width is along Y, with
#     the model centered on Y=0.
#   * Side profile  -> Front orthographic view  (numpad 1), image lies in the XZ plane.
#   * Top  view     -> Top orthographic view    (numpad 7), image lies in the XY plane.
#   * Front view    -> Right orthographic view  (numpad 3), image lies in the YZ plane.

import bpy, os, math, numpy as np, mathutils


# ---------------------------------------------------------------- image i/o ----
def load_lum(path_or_name):
    """Return (image, W, H, lum) where lum is a (H,W) float array, row 0 = TOP.
    Accepts a datablock name already loaded, or an absolute filepath."""
    img = bpy.data.images.get(os.path.basename(path_or_name))
    if img is None:
        img = bpy.data.images.load(path_or_name, check_existing=True)
    W, H = img.size
    a = np.array(img.pixels[:], dtype=np.float32).reshape(H, W, 4)
    lum = a[..., :3].mean(2)[::-1]          # flip rows: Blender pixels are bottom-up
    return img, W, H, lum


def save_array(name, arr, ref_dir):
    """arr is (H,W,4) top-down float RGBA. Saves <name>.png into ref_dir, returns path."""
    H, W = arr.shape[0], arr.shape[1]
    out = bpy.data.images.new(name, width=W, height=H, alpha=True)
    out.pixels = arr[::-1].ravel().tolist()  # back to bottom-up for Blender
    out.filepath_raw = os.path.join(ref_dir, name + ".png")
    out.file_format = 'PNG'
    out.save()
    return out.filepath_raw


def crop(src_name, out_name, ref_dir, x0, x1, ytop0, ytop1):
    """Crop a sub-rectangle. x in pixels; ytop0/ytop1 are fractions from the TOP
    (0=top, 1=bottom) — handy for slicing a multi-view blueprint sheet."""
    img, W, H, _ = load_lum(src_name)
    a = np.array(img.pixels[:], dtype=np.float32).reshape(H, W, 4)[::-1]  # top-down
    y0, y1 = int(H * ytop0), int(H * ytop1)
    return save_array(out_name, a[y0:y1, x0:x1, :], ref_dir)


def flip_h(src_name, out_name, ref_dir):
    """Mirror left<->right. Use to make the muzzle/nose point +X (right)."""
    img, W, H, _ = load_lum(src_name)
    a = np.array(img.pixels[:], dtype=np.float32).reshape(H, W, 4)[::-1]
    return save_array(out_name, a[:, ::-1, :], ref_dir)


# ------------------------------------------------------------- measurement ----
def measure_extent(lum, thresh=0.5, exclude=None):
    """Robust ink bounding box (percentiles trim sparse labels/leader lines).
    `exclude` is an optional list of (x0,x1,y0,y1) pixel boxes to blank out
    (e.g. an inset detail drawing). Returns dict of px anchors, top-down coords."""
    m = lum < thresh
    if exclude:
        for x0, x1, y0, y1 in exclude:
            m[y0:y1, x0:x1] = False
    ys, xs = np.where(m)
    return dict(x_left=np.percentile(xs, 0.2),  x_right=np.percentile(xs, 99.8),
                y_top =np.percentile(ys, 0.2),  y_bot  =np.percentile(ys, 99.8))


# --------------------------------------------------- pixel <-> world mapping ----
# An image empty with empty_image_offset=(-0.5,-0.5) has its origin at the image
# CENTER. For a LANDSCAPE image, empty_display_size == the image's world WIDTH.
def make_mapping(empty, W, H):
    """Return (w2px, px2w) converters for a *side/front-style* empty in the XZ/ YZ
    plane. worldX uses width, worldZ uses height. (For a TOP empty, Z->Y.)"""
    Dw = empty.empty_display_size
    Dh = Dw * (H / W)
    lx, _, lz = empty.location
    def w2px(wx, wz):
        return (((wx - lx) / Dw + 0.5) * W, (0.5 - (wz - lz) / Dh) * H)
    def px2w(px, py):
        return (lx + (px / W - 0.5) * Dw, lz + (0.5 - py / H) * Dh)
    return w2px, px2w


# ----------------------------------------------------------- ref empties -------
_VIEW_ROT = {
    'side':  (math.radians(90), 0, 0),                 # XZ plane, faces -Y (Front view)
    'top':   (0, 0, 0),                                # XY plane, faces +Z (Top view)
    'front': (math.radians(90), 0, math.radians(90)),  # YZ plane, faces -X (Right view)
}

def add_ref_empty(name, img_path, view, ppm, location, opacity=0.6):
    """Create an aligned reference image empty at TRUE SCALE.
       ppm = pixels-per-meter calibration (see calibrate_ppm).
       location = world origin for the image CENTER."""
    for o in list(bpy.data.objects):
        if o.name == name:
            bpy.data.objects.remove(o, do_unlink=True)
    img = bpy.data.images.load(img_path, check_existing=True)
    W, H = img.size
    e = bpy.data.objects.new(name, None)
    e.empty_display_type = 'IMAGE'
    e.data = img
    e.empty_display_size = W / ppm          # world width  (landscape image)
    e.empty_image_offset = (-0.5, -0.5)     # origin at image centre
    e.rotation_euler = _VIEW_ROT[view]
    e.location = location
    e.use_empty_image_alpha = True
    e.color = (1, 1, 1, opacity)
    e.show_empty_image_perspective = False
    e.hide_select = True                    # don't grab it while modeling
    bpy.context.scene.collection.objects.link(e)
    return e


def calibrate_ppm(anchors, real_height_m=None, real_width_m=None):
    """Pixels-per-meter from a known real dimension. Prefer HEIGHT for a side view
    (slide-top to grip-bottom etc.) — it is usually the most reliable measure."""
    if real_height_m:
        return (anchors['y_bot'] - anchors['y_top']) / real_height_m
    return (anchors['x_right'] - anchors['x_left']) / real_width_m


# ----------------------------------------------------- silhouette sampling -----
def silhouette(lum, px2w, w2px, x_slices, z_lo, z_hi, x_range=None, thresh=0.5):
    """For each world X in x_slices, return (X, top_Z, bot_Z) of the OUTER outline
    by scanning a vertical column between world Z in [z_lo, z_hi]. Restrict the
    horizontal search with x_range=(xmin,xmax) to avoid inset drawings/labels."""
    dark = lum < thresh
    H, W = lum.shape
    out = []
    py_hi = int(w2px(0, z_hi)[1]); py_lo = int(w2px(0, z_lo)[1])
    for wx in x_slices:
        px = int(w2px(wx, 0)[0])
        if px < 0 or px >= W:
            out.append((wx, None, None)); continue
        col = dark[py_hi:py_lo, px]
        idx = np.where(col)[0]
        if len(idx) == 0:
            out.append((wx, None, None)); continue
        out.append((wx, px2w(px, py_hi + idx.min())[1], px2w(px, py_hi + idx.max())[1]))
    return out


# ------------------------------------------------------------ modeling aids ----
def box(name, xmin, xmax, zmin, zmax, yhalf, ymid=0.0, coll=None):
    """Axis-aligned block from world min/max. The bread-and-butter of box modeling."""
    bpy.ops.mesh.primitive_cube_add(size=1)
    o = bpy.context.active_object
    o.name = name
    o.scale = ((xmax - xmin), (2 * yhalf), (zmax - zmin))
    o.location = ((xmin + xmax) / 2, ymid, (zmin + zmax) / 2)
    bpy.ops.object.transform_apply(scale=True)
    if coll:
        for c in o.users_collection: c.objects.unlink(o)
        coll.objects.link(o)
    return o


def shear_z(obj, k):
    """Shear a mesh in the XZ plane: X' = X + k*Z. Use for raked grips / slanted
    forms whose top & bottom edges must stay horizontal (a ROTATION would tilt
    them). k = horizontal_shift / vertical_drop."""
    S = mathutils.Matrix.Identity(4); S[0][2] = k
    obj.data.transform(S)


def overlay_view(axis='FRONT', xray=0.5):
    """Frame the selection in an ortho view with x-ray on, so the reference shows
    through the solid model for silhouette matching. axis in FRONT/TOP/RIGHT."""
    for area in bpy.context.screen.areas:
        if area.type != 'VIEW_3D':
            continue
        region = next(r for r in area.regions if r.type == 'WINDOW')
        with bpy.context.temp_override(area=area, region=region):
            bpy.ops.view3d.view_axis(type=axis)
            bpy.ops.view3d.view_selected()
        for sp in area.spaces:
            if sp.type == 'VIEW_3D':
                sp.shading.type = 'SOLID'
                sp.shading.show_xray = True
                sp.shading.xray_alpha = xray
