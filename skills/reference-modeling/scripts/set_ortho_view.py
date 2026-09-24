# set_ortho_view.py — snap the viewport to a clean orthographic axis view.
#
# The single most important perception helper. You can only judge proportion
# silhouette-against-silhouette in ORTHOGRAPHIC projection — perspective distorts
# every edge, which is exactly how a model drifts into "approximately right boxes".
# Call this immediately before every get_viewport_screenshot so each comparison is
# the model's outline laid over the reference's outline, not a perspective guess.
#
# Run inside Blender via the blender MCP execute_blender_code tool. Needs Blender
# 3.2+ for temp_override (fine for the 3.0+ requirement).

import bpy


def set_ortho_view(axis='FRONT', focus_object=None, xray=None):
    """Snap the 3D viewport to a clean orthographic axis view.

    axis:          'FRONT','BACK','LEFT','RIGHT','TOP','BOTTOM' (the ortho VIEW,
                   not the reference type — a SIDE profile is judged in 'FRONT').
    focus_object:  name of an object to select + frame (view_selected zooms to fit
                   without changing the rotation). None leaves framing untouched.
    xray:          None leaves x-ray as-is; True/False toggles it. Turn it ON when
                   the reference empty sits behind the solid mesh and you want the
                   drawing to show through for an overlay comparison.
    """
    area = next((a for a in bpy.context.screen.areas if a.type == 'VIEW_3D'), None)
    if area is None:
        raise RuntimeError("No 3D viewport found")
    region = next((r for r in area.regions if r.type == 'WINDOW'), None)
    space = area.spaces.active
    with bpy.context.temp_override(area=area, region=region, space_data=space):
        bpy.ops.view3d.view_axis(type=axis)
        space.region_3d.view_perspective = 'ORTHO'
        if focus_object:
            obj = bpy.data.objects[focus_object]
            for o in bpy.context.view_layer.objects:
                o.select_set(o == obj)
            bpy.context.view_layer.objects.active = obj
            bpy.ops.view3d.view_selected()      # keeps rotation, zooms to fit
    if xray is not None:
        space.shading.show_xray = bool(xray)
    return axis
