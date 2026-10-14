"""Build script for __ASSET_ID__ (run via: gam_cli.py build brief.json).

Re-runnable from scratch: every run resets the scene, rebuilds the form, and exports.
Author in world space, 1 unit = 1 m, Z up, the asset's FRONT FACING -Y, roughly centered.
`gam.finish` handles pivot, shading, lightmap UV, LODs, collider, props and hierarchy.
"""
import os, sys
sys.path.insert(0, os.path.expanduser("~/.claude/skills/game-asset-modeling/scripts"))
import bpy, bmesh, math
from mathutils import Vector
import gam

ctx = gam.Ctx.from_argv()
B = ctx.brief
gam.reset()
lib = gam.load_material_lib(ctx)          # project materials.json (may be empty)


def build_form():
    """Return the render mesh object (materials assigned, UV0 made).
    Placeholder: a correctly sized grey box is the whole job."""
    d = B["dims"]
    if ctx.placeholder:
        ob = gam.box(ctx.id + "_lod0", (d["x"], d["y"], d["z"]), base_z=0)
        ob.data.materials.append(gam.material("m_placeholder_grey", "#8A8B86", roughness=0.9))
        gam.uv_world_box(ob, meters_per_tile=1.0)
        return ob
    raise NotImplementedError("model the form here (see references/tracks.md for the track recipe)")


def placeholder_extras():
    """Sockets the spec requires for this id prefix, parked at the top-front of the box."""
    d = B["dims"]; out = []
    for pref, names in ctx.spec.get("sockets", {}).get("required", {}).items():
        if ctx.id.startswith(pref):
            out += [gam.socket(n, (0, -d["y"] / 2, d["z"] * 0.95)) for n in names]
    return out


obj = build_form()
extras = placeholder_extras() if ctx.placeholder else []
root = gam.finish(ctx, obj, extra_children=extras)   # collider kind from brief["collider"]
gam.export(ctx, root)
print("GAM_REPORT", gam.mesh_report(bpy.data.objects[ctx.id + "_lod0"]))
