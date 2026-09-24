# mesh_report.py — a non-visual signal that "real topology" has constraints.
#
# Screenshots tell you the silhouette is right; they do NOT tell you the mesh
# underneath is real geometry rather than overlapping boxes. Run this after each
# refinement edit so you can't declare victory on a sloppy mesh.
#
# Reading the numbers:
#   * ngons (faces with >4 sides) CLIMBING  -> sloppy work; an extrude/inset/boolean
#     left a mess. This is the main thing to act on.
#   * loose_verts > 0                        -> stray verts disconnected from the mesh;
#     clean them up (they come from sloppy merges / deletes).
#   * non_manifold_edges > 0                 -> EXPECTED mid-build for an open or
#     half-built hard-surface piece (every boundary edge is non-manifold). Only worry
#     about it in Phase 4 on a piece that is supposed to be closed.
#
# Run inside Blender via the blender MCP execute_blender_code tool.

import bpy, bmesh, json


def mesh_report(obj_name):
    obj = bpy.data.objects.get(obj_name)
    if obj is None:
        return {"error": f"no object named {obj_name}"}
    if obj.type != 'MESH':
        return {"error": f"{obj_name} is not a mesh"}
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    tris = quads = ngons = 0
    for f in bm.faces:
        n = len(f.verts)
        tris += n == 3
        quads += n == 4
        ngons += n > 4
    report = {
        "object": obj_name,
        "verts": len(bm.verts), "edges": len(bm.edges), "faces": len(bm.faces),
        "tris": int(tris), "quads": int(quads), "ngons": int(ngons),
        "non_manifold_edges": sum(1 for e in bm.edges if not e.is_manifold),
        "loose_verts": sum(1 for v in bm.verts if not v.link_edges),
    }
    bm.free()
    return report


# call as: print(json.dumps(mesh_report("Slide")))
