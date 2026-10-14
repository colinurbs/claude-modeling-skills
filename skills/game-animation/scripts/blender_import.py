"""
blender_import.py -- headless Blender stage of game-animation: import a mocap take
(BVH / FBX / glTF), sample every source bone in world space, retarget it onto the target
skeleton with animlib (same math the CPython stages use), and save a .blend with the
retargeted action on the real char_skeleton_ref armature for hand inspection/polish.

  Blender -b --factory-startup --python-exit-code 1 --python blender_import.py -- \
      --source take.bvh --out take.npz [--job job.json]

job.json (written by anim_cli.py): {"skeleton": Skeleton.to_dict(), "skeleton_glb": path,
  "mapping": {...}, "rest_frame": 0|"bind"|null, "scale": null, "blend": out.blend,
  "armature": optional source armature name, "action": optional action name}

The .npz holds the raw source samples (glTF axes: +Y up) and, with a job, the retargeted
canonical clip at the source frame rate (ql, root_p, hips_d, fps) plus retarget info JSON.
"""
import bpy, json, os, re, sys
import numpy as np
from mathutils import Matrix, Quaternion

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import animlib as al  # noqa: E402

argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
def arg(k, d=None): return argv[argv.index(k) + 1] if k in argv else d

SRC, OUT, JOB = arg("--source"), arg("--out"), arg("--job")
job = json.load(open(JOB)) if JOB else {}
K = np.array([[1, 0, 0], [0, 0, 1], [0, -1, 0]], float)     # Blender (x, y, z) -> glTF (x, z, -y)


def log(*a): print("GANIM_", *a, flush=True)


bpy.ops.wm.read_factory_settings(use_empty=True)
scene = bpy.context.scene
ext = os.path.splitext(SRC)[1].lower()
src_fps = None
if ext == ".bvh":
    txt = open(SRC, errors="ignore").read(200000)
    m = re.search(r"Frame Time:\s*([0-9.eE+-]+)", txt)
    src_fps = 1.0 / float(m.group(1)) if m else None
    bpy.ops.import_anim.bvh(filepath=SRC, target="ARMATURE", global_scale=1.0, frame_start=1, use_fps_scale=False,
                            update_scene_fps=False, update_scene_duration=True, rotate_mode="NATIVE", axis_forward="-Z", axis_up="Y")
elif ext == ".fbx":
    bpy.ops.import_scene.fbx(filepath=SRC, automatic_bone_orientation=False, ignore_leaf_bones=False, use_anim=True)
elif ext in (".glb", ".gltf"):
    bpy.ops.import_scene.gltf(filepath=SRC)
else:
    raise SystemExit("unsupported source format " + ext)

arms = [o for o in scene.objects if o.type == "ARMATURE"]
if job.get("armature"): arms = [o for o in arms if o.name == job["armature"]] or arms
if not arms: raise SystemExit("no armature in " + SRC)
arm = max(arms, key=lambda o: len(o.data.bones))
ad = arm.animation_data
act = None
if job.get("action"): act = bpy.data.actions.get(job["action"])
if act is None and ad and ad.action: act = ad.action
if act is None and bpy.data.actions: act = bpy.data.actions[0]
if act is None: raise SystemExit("no action on %s" % arm.name)
if not arm.animation_data: arm.animation_data_create()
arm.animation_data.action = act
if hasattr(arm.animation_data, "action_slot") and getattr(act, "slots", None) and len(act.slots):
    try: arm.animation_data.action_slot = act.slots[0]
    except Exception: pass
f0, f1 = int(round(act.frame_range[0])), int(round(act.frame_range[1]))
if src_fps is None: src_fps = scene.render.fps / scene.render.fps_base
names = [b.name for b in arm.data.bones]
parents = [names.index(b.parent.name) if b.parent else -1 for b in arm.data.bones]
log("SOURCE", os.path.basename(SRC), "armature", arm.name, "bones", len(names), "frames", f0, f1, "fps", round(src_fps, 3), "action", act.name)


def to_gltf(M):
    M = np.array(M, float); R = M[:3, :3]; t = M[:3, 3]
    return K @ R @ K.T, K @ t


def sample(Ms):
    Rg, Xg, Yg = [], [], []
    for M in Ms:
        R, t = to_gltf(M)
        R = R / np.maximum(np.linalg.norm(R, axis=0, keepdims=True), 1e-12)
        Rg.append(R); Xg.append(t); Yg.append(R[:, 1])
    return al.qfrom_mat(np.array(Rg)), np.array(Xg), np.array(Yg)


mw = arm.matrix_world.copy()
rest = sample([mw @ b.matrix_local for b in arm.data.bones])
R, X, Y = [], [], []
for f in range(f0, f1 + 1):
    scene.frame_set(f)
    r, x, y = sample([mw @ pb.matrix for pb in (arm.pose.bones[n] for n in names)])
    R.append(r); X.append(x); Y.append(y)
src = {"names": names, "parents": parents, "fps": float(src_fps), "R": np.array(R), "X": np.array(X), "Y": np.array(Y),
       "rest_R": rest[0], "rest_X": rest[1], "rest_Y": rest[2], "frame_start": f0}
save = {k: (np.array(v) if not isinstance(v, (str, float, int)) else v) for k, v in src.items()}
save["names"] = np.array(names); save["source_file"] = os.path.abspath(SRC)

if job.get("mapping"):
    skel = al.Skeleton.from_dict(job["skeleton"])
    clip, info = al.retarget(src, job["mapping"], skel, rest_frame=job.get("rest_frame"), scale=job.get("scale"),
                             relax_hands=job.get("relax_hands", True))
    save.update(ql=clip.ql, root_p=clip.root_p, hips_d=clip.hips_d, clip_fps=clip.fps, info=json.dumps(info))
    log("RETARGET mapped", len(info["mapped"]), "scale", round(info["scale"], 4), "rest", info["rest"], "facing", info["source_facing_deg"])
    if job.get("blend") and job.get("skeleton_glb"):
        # inspection copy: the retargeted take keyed on the real skeleton armature
        for o in list(scene.objects): bpy.data.objects.remove(o, do_unlink=True)
        bpy.ops.import_scene.gltf(filepath=job["skeleton_glb"])
        tarm = next(o for o in scene.objects if o.type == "ARMATURE")
        tmw_inv = tarm.matrix_world.inverted()
        Wc, P = al.fk(skel, clip)
        Kb = K.T                                        # glTF -> Blender
        rest_local = {b.name: b.matrix_local.copy() for b in tarm.data.bones}
        nF = clip.F
        posed = {}
        for bn in tarm.data.bones:
            if bn.name not in skel.idx: continue
            i = skel.i(bn.name)
            D = al.qto_mat(Wc[:, i])                      # canonical world delta (glTF)
            Db = np.einsum("ij,fjk,kl->fil", Kb, D, K)    # to Blender axes
            Pb = (Kb @ P[:, i].T).T
            RL = np.array(rest_local[bn.name])
            Ms = np.zeros((nF, 4, 4)); Ms[:, :3, :3] = Db @ RL[:3, :3]; Ms[:, :3, 3] = Pb; Ms[:, 3, 3] = 1
            Ms = np.array(tmw_inv) @ Ms
            posed[bn.name] = Ms
        act2 = bpy.data.actions.new(os.path.splitext(os.path.basename(SRC))[0] + "_retarget")
        tarm.animation_data_create(); tarm.animation_data.action = act2
        for pb in tarm.pose.bones:
            if pb.name not in posed: continue
            pb.rotation_mode = "QUATERNION"
            b = pb.bone; M = posed[pb.name]
            if b.parent and b.parent.name in posed:
                rel_rest = np.linalg.inv(np.array(b.parent.matrix_local)) @ np.array(b.matrix_local)
                basis = np.linalg.inv(rel_rest)[None] @ np.linalg.inv(posed[b.parent.name]) @ M
            else:
                basis = np.linalg.inv(np.array(b.matrix_local))[None] @ M
            q = al.qcontinuous(al.qfrom_mat(basis[:, :3, :3])); loc = basis[:, :3, 3]
            for path, vals in (("rotation_quaternion", np.stack([q[:, 3], q[:, 0], q[:, 1], q[:, 2]], -1)), ("location", loc)):
                if path == "location" and pb.name not in ("root", "hips"): continue
                for k in range(vals.shape[1]):
                    dp = 'pose.bones["%s"].%s' % (pb.name, path)
                    try: fc = act2.fcurve_ensure_for_datablock(tarm, dp, index=k, group_name=pb.name)
                    except AttributeError: fc = act2.fcurves.new(dp, index=k, action_group=pb.name)
                    fc.keyframe_points.add(nF)
                    co = np.empty(nF * 2); co[0::2] = np.arange(nF) + 1; co[1::2] = vals[:, k]
                    fc.keyframe_points.foreach_set("co", co); fc.update()
        scene.frame_start, scene.frame_end = 1, nF
        scene.render.fps = int(round(clip.fps)) if abs(clip.fps - round(clip.fps)) < 1e-3 else scene.render.fps
        os.makedirs(os.path.dirname(os.path.abspath(job["blend"])), exist_ok=True)
        bpy.ops.wm.save_as_mainfile(filepath=os.path.abspath(job["blend"]), compress=True)
        log("BLEND", job["blend"])

np.savez_compressed(OUT, **save)
log("NPZ", OUT)
