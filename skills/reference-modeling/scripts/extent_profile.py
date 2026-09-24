# extent_profile.py — subject-agnostic form-taper metric. Samples how far the mesh
# extends along one axis (`measure`) at a series of stations down another (`along`).
# Set `along` = the primary axis and `measure` = the axis whose variation you're checking.
# This is the mask-free objective gate for "does the cross-section actually change along
# the length", the thing that turns a uniform slab into real 3D form.
#
# Units are scene units (meters here). A flat profile (all stations ~equal) is the
# signature of an un-differentiated extrusion.
#
# Run inside Blender via the blender MCP execute_blender_code tool.

import bpy, bmesh, json


def extent_profile(obj_name, along='x', measure='y', n_stations=12):
    idx = {'x': 0, 'y': 1, 'z': 2}
    a, m = idx[along], idx[measure]
    obj = bpy.data.objects[obj_name]
    bm = bmesh.new(); bm.from_mesh(obj.data)
    mw = obj.matrix_world
    co = [mw @ v.co for v in bm.verts]
    bm.free()
    if not co:
        return {"error": f"{obj_name} has no geometry"}
    pa = [c[a] for c in co]
    lo, hi = min(pa), max(pa)
    step = (hi - lo) / n_stations if hi > lo else 0.0
    out = []
    for i in range(n_stations):
        s0 = lo + i * step
        s1 = s0 + step * (1.001 if i == n_stations - 1 else 1.0)
        vals = [c[m] for c in co if s0 <= c[a] <= s1]
        if vals:
            out.append({"pos": round((s0 + s1) / 2, 4),
                        "extent": round(max(vals) - min(vals), 4)})
    return {"object": obj_name, "along": along, "measure": measure,
            "min_extent": round(min(o["extent"] for o in out), 4),
            "max_extent": round(max(o["extent"] for o in out), 4),
            "stations": out}


# call as: print(json.dumps(extent_profile("Body", along='x', measure='y')))
