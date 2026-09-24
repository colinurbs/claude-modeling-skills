# bevel.py — non-destructive edge rounding, the hard-surface FINISHING step.
#
# Real manufactured objects have almost no perfectly sharp edges: every edge carries a
# small radius from machining/molding/casting/wear. A true 90° edge gives one hard
# specular line and reads as fake; a small rounded edge gives a soft highlight that
# defines the form. Bevels are what make a hard-surface model read as physical.
#
# PRECONDITION — bevel only AFTER the form gates pass and `mesh_report` is clean (no
# ngons, no non-manifold, no fan-triangulation at transitions). Beveling unclean topology
# (fans, poles, ngons, near-coincident verts) self-intersects and makes things worse.
#
# This rounds SHARP EDGES. A surface that is itself curved (a domed top, a cylindrical
# barrel, a grip swell) is FORM — shape it in the Phase-3 form pass, not here.
#
# Run inside Blender via the blender MCP execute_blender_code tool.

import bpy, math


def add_bevel(obj_name, width=0.0015, segments=2, limit='ANGLE', angle_deg=30):
    """Non-destructive edge rounding via the Bevel modifier. Keep it a MODIFIER until the
    model is otherwise final so width/segments stay tunable (and so booleans aren't run on
    beveled geometry).

    width    : the radius — keep it SMALL for hard surface, just enough to catch a
               highlight. Big widths read soft/toy-like; tiny crisp ones read as machined
               metal. Match to material + any fillet radii the reference gives.
    segments : 1 = flat chamfer, 2-3 = rounded fillet (use 2-3 for hard surface).
    limit    : 'ANGLE' rounds only edges sharper than angle_deg (leaves smooth transitions
               alone — never blanket-bevel everything); 'WEIGHT' rounds only edges you've
               given a bevel weight (use when different edges need different radii).
    """
    m = bpy.data.objects[obj_name].modifiers.new("Bevel", 'BEVEL')
    m.width = width
    m.segments = segments
    m.limit_method = limit
    if limit == 'ANGLE':
        m.angle_limit = math.radians(angle_deg)
    m.harden_normals = True   # needs shade-smooth + auto-smooth (finish_shading) to show
    return m.name
