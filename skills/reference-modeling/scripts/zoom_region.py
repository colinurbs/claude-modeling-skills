# zoom_region.py — Layer 4 support. Frames the ORTHO viewport tightly on one world
# region (trigger guard, grip curve, slide nose) and cranks contrast, so a separate
# critic looking at the screenshot can actually SEE a few-millimeter deviation. At full
# frame a 5 mm error is ~2 px and invisible — that is why the critic gets crops, not
# overviews.
#
# It frames by dropping two throwaway corner empties at the box, view_selected on them,
# then deleting them (reliable, unlike poking region_3d zoom directly).
#
# Run inside Blender via the blender MCP execute_blender_code tool.

import bpy


def zoom_region(xmin, xmax, zmin, zmax, axis='FRONT', mesh_name=None,
                mesh_color=(1.0, 0.1, 0.05, 1.0), ref_name='REF_side', ref_opacity=0.95):
    """Frame [xmin,xmax]x[zmin,zmax] (world, XZ plane) in an ORTHO axis view with high
    contrast: the mesh forced to a bright flat single color over the reference at high
    opacity. Screenshot immediately after. Set mesh_name to recolor that object."""
    area = next((a for a in bpy.context.screen.areas if a.type == 'VIEW_3D'), None)
    if area is None:
        raise RuntimeError("No 3D viewport found")
    region = next(r for r in area.regions if r.type == 'WINDOW')
    sp = area.spaces.active

    # high-contrast shading: flat single bright color, x-ray so the ref shows through
    sp.shading.type = 'SOLID'
    sp.shading.light = 'FLAT'
    sp.shading.color_type = 'OBJECT'
    sp.shading.show_xray = True
    sp.shading.xray_alpha = 0.65
    if mesh_name:
        ob = bpy.data.objects.get(mesh_name)
        if ob:
            ob.color = mesh_color
    ref = bpy.data.objects.get(ref_name)
    if ref:
        ref.hide_set(False)
        ref.color[3] = ref_opacity

    # throwaway corner empties to frame the exact box
    corners = []
    for (x, z) in ((xmin, zmin), (xmax, zmax)):
        c = bpy.data.objects.new(f"_zoomtmp_{len(corners)}", None)
        c.empty_display_size = 1e-4
        c.location = (x, 0.0, z)
        bpy.context.scene.collection.objects.link(c)
        corners.append(c)
    for o in bpy.context.view_layer.objects:
        o.select_set(o in corners)
    bpy.context.view_layer.objects.active = corners[0]
    with bpy.context.temp_override(area=area, region=region, space_data=sp):
        bpy.ops.view3d.view_axis(type=axis)
        sp.region_3d.view_perspective = 'ORTHO'
        bpy.ops.view3d.view_selected()
    for c in corners:
        bpy.data.objects.remove(c, do_unlink=True)
    return {"framed": [xmin, xmax, zmin, zmax], "axis": axis}
