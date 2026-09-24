# finish_shading.py — the other half of hard-surface finishing: crisp flats + clean
# rounded bevels. Without it, smooth shading bleeds across flat faces and the form looks
# melted/warped; with it, flats stay crisp and only the bevels round in shading.
#
# Pairs with bevel.py. MODIFIER ORDER MATTERS: Bevel must come before Weighted Normal in
# the stack — call add_bevel() first, then finish_shading() (which appends WN after it).
#
# Auto-smooth moved in Blender 4.1: pre-4.1 used mesh.use_auto_smooth + auto_smooth_angle;
# 4.1+ removed those and shades by the per-edge sharp flag instead. So on 4.1+ we BAKE the
# sharp-by-angle flags straight onto the mesh (the native shading mechanism) and let the
# Weighted Normal modifier read them. This avoids the operator-added "Smooth by Angle"
# node modifier, which Blender pins to the bottom of the stack — that would force it AFTER
# Weighted Normal, so WN wouldn't see the sharp flags and the flats would melt.
# Verified on Blender 5.1.
#
# Run inside Blender via the blender MCP execute_blender_code tool.

import bpy, bmesh, math


def finish_shading(obj_name, angle_deg=30):
    """Shade smooth, mark sharp-by-angle (version-correct), add a Weighted Normal modifier
    after the bevel. Run alongside the bevel so harden_normals + WN take effect."""
    obj = bpy.data.objects[obj_name]
    for p in obj.data.polygons:
        p.use_smooth = True

    if hasattr(obj.data, "use_auto_smooth"):          # Blender < 4.1 (legacy auto-smooth)
        obj.data.use_auto_smooth = True
        obj.data.auto_smooth_angle = math.radians(angle_deg)
    else:                                              # Blender 4.1+ (incl. 5.x)
        thr = math.radians(angle_deg)
        bm = bmesh.new(); bm.from_mesh(obj.data)
        for e in bm.edges:                            # sharp where the dihedral is steep
            e.smooth = not (len(e.link_faces) == 2 and e.calc_face_angle(0.0) > thr)
        bm.to_mesh(obj.data); obj.data.update(); bm.free()

    if "WeightedNormal" not in [m.name for m in obj.modifiers]:
        wn = obj.modifiers.new("WeightedNormal", 'WEIGHTED_NORMAL')
        wn.keep_sharp = True
    return "WeightedNormal"
