# import_npz.py — load a verts/faces .npz (e.g. from sdf_mesher.py) as a Blender mesh object.
# Run inside Blender via the blender MCP execute_blender_code tool.

import bpy, bmesh, numpy as np


def import_npz(path, name, ratio=0.3, smooth=True):
    """Create/replace object `name` from an .npz with 'verts' (N,3) and 'faces' (M,3).
    ratio < 1 applies a collapse Decimate (marching cubes output is very dense)."""
    d = np.load(path); v = d['verts']; f = d['faces']
    if name in bpy.data.objects:
        bpy.data.objects.remove(bpy.data.objects[name], do_unlink=True)
    me = bpy.data.meshes.new(name)
    me.vertices.add(len(v)); me.vertices.foreach_set("co", v.ravel())
    me.loops.add(f.size); me.loops.foreach_set("vertex_index", f.ravel())
    me.polygons.add(len(f)); me.polygons.foreach_set("loop_start", np.arange(0, f.size, 3))
    me.update(); me.validate()
    ob = bpy.data.objects.new(name, me); bpy.context.scene.collection.objects.link(ob)
    bm = bmesh.new(); bm.from_mesh(me)
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=1e-7)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)      # marching-cubes winding varies
    bm.to_mesh(me); bm.free()
    if ratio < 1:
        m = ob.modifiers.new("Dec", 'DECIMATE'); m.ratio = ratio; m.use_collapse_triangulate = True
        with bpy.context.temp_override(object=ob, active_object=ob):
            bpy.ops.object.modifier_apply(modifier="Dec")
    if smooth:
        for p in me.polygons: p.use_smooth = True
    return ob
