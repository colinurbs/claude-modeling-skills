# silhouette_metrics.py — Layer 1 of the objective-alignment gate. NO vision involved,
# so it cannot be rationalized away: it measures the mesh's real spans and compares them
# to the object's known real dimensions. This catches gross proportion errors, which are
# most of what goes wrong, before any image is even looked at.
#
# UNIT CONVENTION: this skill models in real-world METERS (1 Blender unit = 1 m). So a
# Glock 17 is 0.186 long, 0.139 tall. Pass targets in the SAME unit (meters). If you
# instead chose millimeters-as-units, pass targets in mm — just be consistent.
#
# AXIS CONVENTION: the profile is built in the XZ plane — length along X, height along Z
# (Forward=+X, Up=+Z). If a build is oriented differently, swap the axes below and
# re-verify on the first run.
#
# Run inside Blender via the blender MCP execute_blender_code tool.

import bpy, bmesh, json


def silhouette_metrics(obj_name):
    """Real-world spans of one object's geometry, in scene units (meters)."""
    obj = bpy.data.objects.get(obj_name)
    if obj is None:
        return {"error": f"no object named {obj_name}"}
    bm = bmesh.new(); bm.from_mesh(obj.data)
    mw = obj.matrix_world
    xs = [(mw @ v.co).x for v in bm.verts]
    zs = [(mw @ v.co).z for v in bm.verts]
    bm.free()
    if not xs:
        return {"error": f"{obj_name} has no geometry"}
    return {"object": obj_name,
            "length_x": max(xs) - min(xs), "height_z": max(zs) - min(zs),
            "x_range": [min(xs), max(xs)], "z_range": [min(zs), max(zs)]}


def dim_gate(obj_name, target_length, target_height, tol=0.05):
    """Hard pass/fail on proportion. Returns measured spans, % error vs target on each
    axis, and a `passed` flag (every span within `tol`, default 5%). This is the number
    the runner must clear to advance — "length 0.171 vs 0.186 -> 8.1% short" is not a
    verdict you can vibe away."""
    m = silhouette_metrics(obj_name)
    if "error" in m:
        return m
    out = {"object": obj_name, "tol_pct": tol * 100}
    for axis, span, target in (("length_x", m["length_x"], target_length),
                               ("height_z", m["height_z"], target_height)):
        err = (span - target) / target
        out[axis] = {"measured": round(span, 5), "target": target,
                     "err_pct": round(err * 100, 1),
                     "verdict": "short" if err < 0 else "long",
                     "within_tol": abs(err) <= tol}
    out["passed"] = all(out[a]["within_tol"] for a in ("length_x", "height_z"))
    return out


# call as:
#   print(json.dumps(silhouette_metrics("PistolBody")))
#   print(json.dumps(dim_gate("PistolBody", 0.186, 0.139, tol=0.05)))
