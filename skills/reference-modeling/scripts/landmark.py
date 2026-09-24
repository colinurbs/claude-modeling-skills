# landmark.py — coordinate metric for interior / 3D features that NO silhouette captures
# (an axle center, a hinge pin, a button, a recessed boss, the tip of a spout). Model the
# feature as its own object (or select its verts), read its target world coordinate off
# the references once, and gate on DISTANCE — never on appearance.
#
# Subject-agnostic; units are scene units (meters here).
#
# Run inside Blender via the blender MCP execute_blender_code tool.

import bpy, bmesh, math, json


def landmark(obj_name, use_selected=False):
    """World-space centroid of an object's geometry (or just its selected verts)."""
    obj = bpy.data.objects.get(obj_name)
    if obj is None:
        return {"error": f"no object named {obj_name}"}
    bm = bmesh.new(); bm.from_mesh(obj.data); mw = obj.matrix_world
    vs = [v for v in bm.verts if (v.select or not use_selected)]
    if not vs:
        bm.free(); return {"error": "no verts"}
    n = len(vs)
    c = [sum((mw @ v.co)[i] for v in vs) / n for i in range(3)]
    bm.free()
    return {"object": obj_name, "co": [round(x, 4) for x in c]}


def landmark_delta(obj_name, target, tol=0.005, use_selected=False):
    """Distance from a feature's centroid to a target world coord (read off the
    reference). tol in scene units. Returns dist, per-axis error, and pass/fail."""
    m = landmark(obj_name, use_selected)
    if "error" in m:
        return m
    c = m["co"]
    d = math.dist(c, target)
    return {"object": obj_name, "measured": c, "target": [round(t, 4) for t in target],
            "dist": round(d, 4), "per_axis": [round(c[i] - target[i], 4) for i in range(3)],
            "tol": tol, "passed": d <= tol}


# call as: print(json.dumps(landmark_delta("Axle", [0.10, 0.0, 0.03], tol=0.003)))
