# symmetry.py — construction-strategy helpers. Symmetry/repetition is a DECISION made
# from the references in Phase 0 (revolution / mirror / radial / linear / none), not a
# default. These wrap the chosen mechanism into one call. Always build the PARTIAL mesh
# (half, quarter, single unit, or profile) first, then add the modifier.
#
# Validate the symmetry actually holds BEFORE committing: silhouette_symmetry() measures
# it objectively on the reference. Treating approximate symmetry as exact is a common,
# hard-to-spot error.
#
# Run inside Blender via the blender MCP execute_blender_code tool.

import bpy, math, numpy as np


def add_mirror(obj_name, axes=(False, False, False), merge=0.001):
    """axes = booleans for (X,Y,Z) mirror planes. Weld center verts onto the plane(s)
    first; use_clip stops verts crossing back over."""
    m = bpy.data.objects[obj_name].modifiers.new("Mirror", 'MIRROR')
    m.use_axis = axes
    m.use_clip = True
    m.use_mirror_merge = True
    m.merge_threshold = merge
    return m.name


def add_screw(obj_name, axis='Z', angle=6.28318, steps=64):
    """Revolve a profile (lathe) — for circular cross-sections. Model a single
    half-profile edge/loop on one side of the axis first; the revolve makes the 3D form,
    so this REPLACES silhouette-extrusion for revolved subjects."""
    m = bpy.data.objects[obj_name].modifiers.new("Screw", 'SCREW')
    m.axis = axis
    m.angle = angle
    m.steps = steps
    m.use_merge_vertices = True
    return m.name


def add_radial_array(obj_name, count, empty_name):
    """Repeat a unit around an axis (gear teeth, fan blades, bolt circle). Make an empty
    at the center rotated 360/count about the axis (see make_offset_empty) and pass it."""
    m = bpy.data.objects[obj_name].modifiers.new("Array", 'ARRAY')
    m.count = count
    m.use_relative_offset = False
    m.use_object_offset = True
    m.offset_object = bpy.data.objects[empty_name]
    return m.name


def add_linear_array(obj_name, count, offset=(1.0, 0.0, 0.0), relative=True):
    """Repeat a unit in a line (fins, slats, keys, stairs). relative=True scales the
    offset by the unit's bounding box; relative=False uses offset as meters."""
    m = bpy.data.objects[obj_name].modifiers.new("Array", 'ARRAY')
    m.count = count
    if relative:
        m.use_relative_offset = True
        m.relative_offset_displace = offset
    else:
        m.use_relative_offset = False
        m.use_constant_offset = True
        m.constant_offset_displace = offset
    return m.name


def make_offset_empty(name, location=(0, 0, 0), rot_axis='Z', count=6):
    """Create the rotated empty that drives add_radial_array (360/count about rot_axis)."""
    e = bpy.data.objects.new(name, None)
    bpy.context.scene.collection.objects.link(e)
    e.location = location
    ax = {'X': 0, 'Y': 1, 'Z': 2}[rot_axis]
    rot = [0.0, 0.0, 0.0]; rot[ax] = 2 * math.pi / count
    e.rotation_euler = rot
    return name


def silhouette_symmetry(image_path, axis='vertical', thresh=0.5):
    """Objectively test whether a reference silhouette is mirror-symmetric, by mirroring
    the filled shape about its OWN centroid and IoU-ing against itself.
    axis='vertical' tests a left-right mirror, 'horizontal' a top-bottom mirror.
    iou ~> 0.95 strongly supports a mirror strategy on that plane; a low value means the
    silhouette is NOT symmetric there — don't force a mirror."""
    img = bpy.data.images.load(image_path, check_existing=True)
    w, h = img.size
    a = np.array(img.pixels[:], np.float32).reshape(h, w, 4)[::-1]
    inside = a[..., :3].mean(2) < thresh
    ys, xs = np.where(inside)
    if len(xs) == 0:
        return {"error": "empty mask"}
    mir = np.zeros_like(inside)
    if axis == 'vertical':
        c = int(round(xs.mean())); xs2 = 2 * c - xs
        ok = (xs2 >= 0) & (xs2 < w); mir[ys[ok], xs2[ok]] = True
    else:
        c = int(round(ys.mean())); ys2 = 2 * c - ys
        ok = (ys2 >= 0) & (ys2 < h); mir[ys2[ok], xs[ok]] = True
    inter = int(np.logical_and(inside, mir).sum())
    union = int(np.logical_or(inside, mir).sum())
    return {"axis": axis, "iou": round(inter / union if union else 0.0, 4)}
