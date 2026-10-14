"""
fp_blend.py -- headless Blender: write the inspection / hand-polish .blend for a first-person set.

  Blender -b --factory-startup --python fp_blend.py -- --job _work/fp_blend_job.json

Imports char_skeleton_ref.glb (and the fp arms glb when given) and the item glb; parents the item to
attach_hand_r at the set's grip transform; per clip creates an action on the armature (the exported
FK, keyed on every frame of the arm chains), an action on the item's part nodes (the meta curves), and
IK rigs for animators: empties `ik_hand_r` / `ik_hand_l` / `pole_r` / `pole_l` animated along the build's
hand targets, with IK constraints on lower_arm_* (chain 2) that are MUTED by default -- unmute them to
edit the view model by moving the targets instead of rotating bones, then re-export the keys.
Clips are strips on the armature's NLA (one track per clip, muted) so every take is inspectable.
"""
import json, os, sys
import numpy as np
import bpy
from mathutils import Matrix, Quaternion, Vector

argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
job = json.load(open(argv[argv.index("--job") + 1]))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import animlib as al  # noqa: E402

K = np.array([[1, 0, 0], [0, 0, -1], [0, 1, 0]], float)   # glTF -> Blender (x, -z, y)
def to_bl(v): return Vector((v[0], -v[2], v[1]))
def q_to_bl(q):
    M = K @ al.qto_mat(np.asarray(q, float)) @ K.T
    return Matrix([list(r) for r in M]).to_quaternion()

for o in list(bpy.context.scene.objects): bpy.data.objects.remove(o, do_unlink=True)
bpy.ops.import_scene.gltf(filepath=job["skeleton_glb"])
arm = next(o for o in bpy.context.scene.objects if o.type == "ARMATURE")
if job.get("arms_glb"):
    before = set(bpy.context.scene.objects)
    bpy.ops.import_scene.gltf(filepath=job["arms_glb"])
    for o in set(bpy.context.scene.objects) - before:
        if o.type == "MESH":
            for m in o.modifiers:
                if m.type == "ARMATURE": m.object = arm
            o.parent = arm
        elif o.type == "ARMATURE": bpy.data.objects.remove(o, do_unlink=True)
skel = al.Skeleton.from_dict(job["skeleton"])
# item
before = set(bpy.context.scene.objects)
bpy.ops.import_scene.gltf(filepath=job["item_glb"])
new = set(bpy.context.scene.objects) - before
item_root = next(o for o in new if o.name == job["item_id"])
for o in new:
    if o.name.endswith(("_lod1", "_lod2")): o.hide_set(True); o.hide_render = True
item_root.parent = arm; item_root.parent_type = "BONE"; item_root.parent_bone = "attach_hand_r"
g = job["grip_r"]
# Blender bone-parent space is the bone TAIL with the bone's axes (bone Y along the bone); the attach bones
# export with glTF node frame == Blender bone frame, so the grip TRS maps directly after moving to the head.
blen = arm.data.bones["attach_hand_r"].length
item_root.matrix_parent_inverse = Matrix.Identity(4)
item_root.location = Vector((g["t"][0], g["t"][1] - blen, g["t"][2]))
item_root.rotation_mode = "QUATERNION"; item_root.rotation_quaternion = Quaternion((g["r"][3], g["r"][0], g["r"][1], g["r"][2]))
parts = {p: next((o for o in new if o.name == "%s_%s" % (job["item_id"], p)), None) for p in job["parts_def"]}
rest = {p: (o.location.copy(), o.rotation_quaternion.copy() if o.rotation_mode == "QUATERNION" else o.rotation_euler.to_quaternion()) for p, o in parts.items() if o}

rest_local = {b.name: np.array(b.matrix_local) for b in arm.data.bones}
mw_inv = np.array(arm.matrix_world.inverted())
arm.animation_data_create(); arm.animation_data.action = None
for pb in arm.pose.bones: pb.rotation_mode = "QUATERNION"
# IK helpers
empt = {}
for n in ("ik_hand_r", "ik_hand_l", "pole_r", "pole_l"):
    e = bpy.data.objects.new(n, None); e.empty_display_type = "SPHERE" if n.startswith("pole") else "ARROWS"; e.empty_display_size = 0.03
    bpy.context.scene.collection.objects.link(e); empt[n] = e
for s in "rl":
    c = arm.pose.bones["lower_arm_" + s].constraints.new("IK")
    c.target = empt["ik_hand_" + s]; c.pole_target = empt["pole_" + s]; c.chain_count = 2; c.mute = True; c.name = "IK (muted: unmute to edit by target)"
    cr = arm.pose.bones["hand_" + s].constraints.new("COPY_ROTATION"); cr.target = empt["ik_hand_" + s]; cr.mute = True

data = np.load(job["npz"], allow_pickle=True)
clips = json.loads(str(data["names"]))
for ci, name in enumerate(clips):
    ql = data["ql_%d" % ci]; F = ql.shape[0]
    clip = al.Clip(30, ql, np.zeros((F, 3)), np.tile(skel.P[skel.i("hips")] - skel.P[skel.i("root")], (F, 1)), name)
    Wc, P = al.fk(skel, clip)
    act = bpy.data.actions.new(name); arm.animation_data.action = act
    posed = {}
    for bn in arm.data.bones:
        if bn.name not in skel.idx: continue
        i = skel.i(bn.name)
        D = al.qto_mat(Wc[:, i]); Db = np.einsum("ij,fjk,kl->fil", K, D, K.T)
        Pb = (K @ P[:, i].T).T
        RL = rest_local[bn.name]
        Ms = np.zeros((F, 4, 4)); Ms[:, :3, :3] = Db @ RL[:3, :3]; Ms[:, :3, 3] = Pb; Ms[:, 3, 3] = 1
        posed[bn.name] = mw_inv @ Ms
    for pb in arm.pose.bones:
        if pb.name not in job["keyed"]: continue
        b = pb.bone; M = posed[pb.name]
        rel = np.linalg.inv(np.array(b.parent.matrix_local)) @ np.array(b.matrix_local)
        basis = np.linalg.inv(rel)[None] @ np.linalg.inv(posed[b.parent.name]) @ M
        q = al.qcontinuous(al.qfrom_mat(basis[:, :3, :3]))
        vals = np.stack([q[:, 3], q[:, 0], q[:, 1], q[:, 2]], -1)
        for k in range(4):
            dp = 'pose.bones["%s"].rotation_quaternion' % pb.name
            try: fc = act.fcurve_ensure_for_datablock(arm, dp, index=k, group_name=pb.name)
            except AttributeError: fc = act.fcurves.new(dp, index=k, action_group=pb.name)
            fc.keyframe_points.add(F); co = np.empty(F * 2); co[0::2] = np.arange(F) + 1; co[1::2] = vals[:, k]
            fc.keyframe_points.foreach_set("co", co); fc.update()
    tr = arm.animation_data.nla_tracks.new(); tr.name = name; tr.strips.new(name, 1, act); tr.mute = True
    arm.animation_data.action = None
    # IK empties along the build's targets (model space -> Blender)
    ref = job["ref"][name]
    for f in range(F):
        it = ref["item_w"][f]; al_ = ref["att_l_target"][f]
        for e, t in ((empt["ik_hand_r"], it), (empt["ik_hand_l"], al_)):
            e.location = to_bl(t["t"]); e.rotation_mode = "QUATERNION"; e.rotation_quaternion = q_to_bl(t["r"])
            e.keyframe_insert("location", frame=f + 1, group=name); e.keyframe_insert("rotation_quaternion", frame=f + 1, group=name)
    # parts
    fp = job["meta"][name]["fp"]
    for p, o in parts.items():
        if not o: continue
        d = job["parts_def"][p]; ax = np.array(d["axis"], float); ax /= np.linalg.norm(ax)
        vals = fp.get("parts", {}).get(p, {}).get("values") or [fp["state_in"].get(p, 0.0)] * F
        o.rotation_mode = "QUATERNION"
        for f, v in enumerate(vals):
            if d.get("move") == "rotate":
                o.rotation_quaternion = q_to_bl(al.qaxis(ax, np.radians(d["angle_deg"]) * v)) @ rest[p][1]; o.keyframe_insert("rotation_quaternion", frame=f + 1)
            elif d.get("move") == "rotate_then_translate":
                ta = np.array(d.get("then_axis", d["axis"]), float); ta /= np.linalg.norm(ta)
                o.rotation_quaternion = q_to_bl(al.qaxis(ax, np.radians(d["angle_deg"]) * min(1.0, 2 * v))) @ rest[p][1]; o.keyframe_insert("rotation_quaternion", frame=f + 1)
                o.location = rest[p][0] + to_bl(ta * d["travel_m"] * max(0.0, 2 * v - 1)); o.keyframe_insert("location", frame=f + 1)
            else:
                o.location = rest[p][0] + to_bl(ax * d["travel_m"] * v); o.keyframe_insert("location", frame=f + 1)
bpy.context.scene.frame_start = 1; bpy.context.scene.render.fps = 30
cam = bpy.data.objects.new("fp_eye", bpy.data.cameras.new("fp_eye")); bpy.context.scene.collection.objects.link(cam)
cam.location = to_bl(job["eye"]); cam.rotation_euler = (np.radians(90), 0, np.radians(180))
cam.data.sensor_fit = "VERTICAL"; cam.data.angle = np.radians(job["fov"]); cam.data.clip_start = job["near"]
bpy.context.scene.camera = cam
bpy.ops.wm.save_as_mainfile(filepath=job["blend"], compress=True)
print("GANIM_BLEND", job["blend"])
