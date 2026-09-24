# silhouette_iou.py — Layer-2 of the objective-alignment gate, generalized to ANY
# orthographic view. It renders the listed objects' silhouette (alpha coverage, flat
# white-on-black) into the exact frame of a reference image empty, then computes pixel
# IoU against a provided filled silhouette mask. Require IoU >= 0.90 to advance.
#
# The camera is derived from the empty's own transform, so the SAME function gates the
# primary view (e.g. REF_side) and the orthogonal views (REF_top, REF_front, ...). Each
# view needs its own mask, registered to that view's framing/aspect.
#
# THE MASK: a clean FILLED silhouette of the target for that view, DARK on LIGHT,
# registered to the reference's framing/aspect. Masks are often missing for arbitrary
# subjects — when absent, fall back to extent_profile for that axis.
#
# Run inside Blender via the blender MCP execute_blender_code tool.

import bpy, mathutils, numpy as np


def _load_topdown(path, reload=False):
    name = bpy.path.basename(path)
    if reload and name in bpy.data.images:
        bpy.data.images.remove(bpy.data.images[name])
    img = bpy.data.images.load(path, check_existing=not reload)
    w, h = img.size
    a = np.array(img.pixels[:], dtype=np.float32).reshape(h, w, 4)[::-1]
    return img, w, h, a


def silhouette_iou(obj_names, mask_path, ref_empty="REF_side",
                   out_path="/tmp/sil_render.png", diff_path=None, thresh=0.5):
    """IoU of the listed objects' silhouette (rendered in ref_empty's frame) vs the mask.
    Returns {iou, passed, intersection, union, mesh_px, mask_px, resolution, diff}.
    diff_path saves a high-contrast overlay (mesh-only=red, mask-only=blue, both=white)."""
    if isinstance(obj_names, str):
        obj_names = [obj_names]
    scene = bpy.context.scene

    # 1. mask -> binary (filled silhouette is DARK on light => inside = lum < thresh)
    _, MW, MH, marr = _load_topdown(mask_path)
    mask_bin = marr[..., :3].mean(2) < thresh

    # 2. reference empty -> world frame (image right = local X, up = local Y, normal = Z)
    e = bpy.data.objects[ref_empty]
    W_img, H_img = e.data.size
    world_w = e.empty_display_size
    R = e.matrix_world.to_3x3()
    ex = (R @ mathutils.Vector((1, 0, 0))).normalized()
    ey = (R @ mathutils.Vector((0, 1, 0))).normalized()
    n  = (R @ mathutils.Vector((0, 0, 1))).normalized()
    center = e.matrix_world.translation
    if abs((MW / MH) - (W_img / H_img)) > 0.02:
        return {"error": "mask aspect != reference aspect; remake the mask at the "
                         "reference's framing", "mask": [MW, MH], "ref": [W_img, H_img]}

    # 3. save state
    r = scene.render
    saved = dict(engine=r.engine, rx=r.resolution_x, ry=r.resolution_y, pct=r.resolution_percentage,
                 film=r.film_transparent, fp=r.filepath, ff=r.image_settings.file_format,
                 cm=r.image_settings.color_mode, cam=scene.camera)
    hide = {o.name: o.hide_render for o in bpy.data.objects if o.type == 'MESH'}
    cam_o = cam_d = None
    try:
        # 4. flat alpha-coverage render config
        r.engine = 'BLENDER_WORKBENCH'; r.film_transparent = True
        r.resolution_x, r.resolution_y, r.resolution_percentage = MW, MH, 100
        r.image_settings.file_format = 'PNG'; r.image_settings.color_mode = 'RGBA'
        for o in bpy.data.objects:
            if o.type == 'MESH':
                o.hide_render = o.name not in set(obj_names)

        # 5. ortho camera framed to the empty: local +X=ex, +Y=ey, +Z=n; sits on +n side
        cam_d = bpy.data.cameras.new("IOU_CAM"); cam_d.type = 'ORTHO'
        cam_d.sensor_fit = 'HORIZONTAL'; cam_d.ortho_scale = world_w
        cam_o = bpy.data.objects.new("IOU_CAM", cam_d); scene.collection.objects.link(cam_o)
        rot = mathutils.Matrix((ex, ey, n)).transposed().to_4x4()
        cam_o.matrix_world = mathutils.Matrix.Translation(center + n * 1.0) @ rot
        scene.camera = cam_o

        # 6. render + read alpha coverage
        r.filepath = out_path
        bpy.ops.render.render(write_still=True)
        _, RW, RH, rarr = _load_topdown(out_path, reload=True)
        mesh_bin = rarr[..., 3] > thresh
    finally:
        scene.camera = saved['cam']
        if cam_o: bpy.data.objects.remove(cam_o, do_unlink=True)
        if cam_d: bpy.data.cameras.remove(cam_d, do_unlink=True)
        r.engine, r.resolution_x, r.resolution_y = saved['engine'], saved['rx'], saved['ry']
        r.resolution_percentage, r.film_transparent = saved['pct'], saved['film']
        r.filepath, r.image_settings.file_format = saved['fp'], saved['ff']
        r.image_settings.color_mode = saved['cm']
        for nm, hv in hide.items():
            o = bpy.data.objects.get(nm)
            if o: o.hide_render = hv

    inter = int(np.logical_and(mesh_bin, mask_bin).sum())
    union = int(np.logical_or(mesh_bin, mask_bin).sum())
    iou = inter / union if union else 0.0

    if diff_path:
        ov = np.zeros((MH, MW, 4), np.float32); ov[..., 3] = 1.0
        ov[np.logical_and(mask_bin, ~mesh_bin)] = (0, 0, 1, 1)
        ov[np.logical_and(mesh_bin, ~mask_bin)] = (1, 0, 0, 1)
        ov[np.logical_and(mesh_bin, mask_bin)] = (1, 1, 1, 1)
        oi = bpy.data.images.new("iou_diff", width=MW, height=MH, alpha=True)
        oi.pixels = ov[::-1].ravel().tolist()
        oi.filepath_raw = diff_path; oi.file_format = 'PNG'; oi.save()

    return {"iou": round(iou, 4), "passed": iou >= 0.90, "intersection": inter,
            "union": union, "mesh_px": int(mesh_bin.sum()), "mask_px": int(mask_bin.sum()),
            "resolution": [MW, MH], "ref_empty": ref_empty, "diff": diff_path}
