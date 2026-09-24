# add_reference.py — load an orthographic blueprint into the scene as an aligned
# image empty to model over. The minimal "quick loader".
#
# For pixel-precise TRUE-SCALE placement (measure a known dimension off the drawing,
# derive pixels-per-meter, place the empty so the same landmark lands at the same
# world coordinate in every view), use the calibration path in blender_ref.py:
#   measure_extent() -> calibrate_ppm() -> add_ref_empty().
# Use this helper when you already know the image's real-world span and just want it
# pinned to an axis fast.
#
# Run inside Blender via the blender MCP execute_blender_code tool.

import bpy
from math import radians


def add_reference(filepath, axis='FRONT', size=1.0, opacity=0.35):
    """Load an ortho blueprint as an empty-image aligned to a view axis.

    axis:    the ortho VIEW the image faces — 'FRONT'/'BACK'/'LEFT'/'RIGHT'/'TOP'/
             'BOTTOM'. A SIDE profile aligns to 'FRONT', a top-down plan to 'TOP',
             a head-on view to 'RIGHT'.
    size:    empty_display_size == the image's world WIDTH. Set it to the reference's
             true real-world span (in meters) so dimensions are anchored, not guessed.
    opacity: 0..1 alpha; bump toward ~0.85 while actively matching a silhouette.

    After adding, set_ortho_view(axis) + screenshot and confirm the image lines up
    before modeling over it. The LEFT/RIGHT/BACK rotations especially: VERIFY by eye.
    """
    bpy.ops.object.empty_add(type='IMAGE', location=(0, 0, 0))
    emp = bpy.context.active_object
    emp.data = bpy.data.images.load(filepath, check_existing=True)
    emp.empty_display_size = size
    emp.empty_image_offset = (-0.5, -0.5)      # centre the image on the empty origin
    emp.use_empty_image_alpha = True
    emp.color[3] = opacity
    emp.show_empty_image_perspective = False
    emp.hide_select = True                     # don't grab it while modeling
    emp.rotation_euler = {
        'FRONT':  (radians(90), 0, 0),                 # stands in XZ, faces -Y
        'BACK':   (radians(90), 0, radians(180)),      # stands in XZ, faces +Y
        'RIGHT':  (radians(90), 0, radians(90)),       # stands in YZ, faces -X — VERIFY
        'LEFT':   (radians(90), 0, radians(-90)),      # stands in YZ, faces +X — VERIFY
        'TOP':    (0, 0, 0),                           # lies in XY, faces +Z
        'BOTTOM': (radians(180), 0, 0),                # lies in XY, faces -Z
    }.get(axis, (0, 0, 0))
    emp.name = f"REF_{axis}"
    return emp.name
