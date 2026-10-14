#!/usr/bin/env python3
"""
anim_build.py -- build one animation set from its animset.json (plain CPython + a headless
Blender import per source take).

  import (Blender) -> retarget (animlib, inside Blender) -> resample 30 fps -> ground calibrate
  -> per clip: segment (+margin) -> root motion extraction -> foot lock / ground clamp
  -> loop crossfade or trim -> [clip "hook": project-side fix] -> [arm_pose] -> start at origin facing +Z -> additive rebase
  -> <export>/anim/<set>.glb + <set>.meta.json, _work/build_report.json, _work/ref.json

Usually driven by anim_cli.py (build/all). Importable: build(animset_path) -> report.
"""
import hashlib, json, math, os, subprocess, sys, time
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import animlib as al  # noqa: E402
from validate_glb import find_spec  # noqa: E402  (game-asset-modeling)
from gam_cli import Slot, blender_bin  # noqa: E402

REF_JOINTS = ["root", "hips", "spine", "head", "hand_l", "hand_r", "foot_l", "foot_r", "toes_l", "lower_arm_r", "lower_leg_l"]


def paths_for(animset_path, a=None):
    a = a or json.load(open(animset_path))
    spec_path = find_spec(animset_path); spec = json.load(open(spec_path)); root = os.path.dirname(spec_path)
    an = spec.get("animation", {})
    ph = a.get("status") == "placeholder"
    exp = os.path.join(root, spec["paths"]["placeholder" if ph else "export"], an.get("export_dir", "anim"))
    work = os.path.join(os.path.dirname(os.path.abspath(animset_path)), "_work")
    return {"spec": spec, "spec_path": spec_path, "root": root, "glb": os.path.join(exp, a["id"] + ".glb"),
            "meta": os.path.join(exp, a["id"] + ".meta.json"), "work": work, "set_dir": os.path.dirname(os.path.abspath(animset_path))}


def resolve_source(p, root, set_dir):
    for c in (p, os.path.join(set_dir, p), os.path.join(root, p)):
        if os.path.exists(c): return os.path.abspath(c)
    raise SystemExit("source not found: %s" % p)


def import_source(key, src, P, skel):
    """Run blender_import.py for one take (cached on inputs). Returns (take Clip @ source fps, info)."""
    mapping = src.get("mapping") or src.get("skeleton") or "auto"
    file = resolve_source(src["file"], P["root"], P["set_dir"])
    os.makedirs(P["work"], exist_ok=True)
    if mapping == "auto":
        names = scan_bone_names(file, P)
        cov, mapping = al.detect_mapping(names)
        print("GANIM mapping auto-detected: %s (%.0f%% of its bones present)" % (mapping, cov * 100))
    mp = al.load_mapping(mapping) if isinstance(mapping, str) else mapping
    if src.get("bones"): mp = dict(mp, bones=dict(mp["bones"], **src["bones"]))
    job = {"skeleton": skel.to_dict(), "skeleton_glb": os.path.join(P["root"], P["spec"]["animation"]["skeleton_glb"]),
           "mapping": mp, "rest_frame": src.get("rest_frame", mp.get("rest_frame")), "scale": src.get("scale"),
           "relax_hands": src.get("relax_hands", True), "armature": src.get("armature"), "action": src.get("action"),
           "blend": os.path.join(P["work"], key + ".retarget.blend")}
    h = hashlib.sha1()
    for f in (file, os.path.join(HERE, "blender_import.py"), os.path.join(HERE, "animlib.py"), job["skeleton_glb"]):
        st = os.stat(f); h.update(("%s%d%d" % (f, st.st_size, int(st.st_mtime))).encode())
    h.update(json.dumps(job, sort_keys=True).encode()); digest = h.hexdigest()[:16]
    npz = os.path.join(P["work"], "%s.%s.npz" % (key, digest))
    if not os.path.exists(npz):
        jp = os.path.join(P["work"], key + ".job.json"); json.dump(job, open(jp, "w"))
        with Slot("build", 3):
            t = time.time()
            r = subprocess.run([blender_bin(), "-b", "--factory-startup", "--python-exit-code", "1", "--python",
                                os.path.join(HERE, "blender_import.py"), "--", "--source", file, "--out", npz, "--job", jp],
                               capture_output=True, text=True)
        open(os.path.join(P["work"], key + ".blender.log"), "w").write(r.stdout + "\n--- stderr ---\n" + r.stderr)
        for l in r.stdout.splitlines():
            if l.startswith("GANIM_") or "Error" in l: print(l)
        if r.returncode != 0 or not os.path.exists(npz):
            print(r.stderr[-2000:]); raise SystemExit("blender import failed for %s (see %s.blender.log)" % (key, key))
        print("GANIM import %s %.1fs" % (key, time.time() - t))
        for f in os.listdir(P["work"]):   # drop stale caches of this source
            if f.startswith(key + ".") and f.endswith(".npz") and f != os.path.basename(npz): os.remove(os.path.join(P["work"], f))
    d = np.load(npz, allow_pickle=True)
    info = json.loads(str(d["info"])); info["mapping"] = mp.get("name", "custom"); info["source_file"] = file
    info["source_frames"] = int(d["R"].shape[0]); info["source_fps"] = float(d["fps"])
    return al.Clip(float(d["clip_fps"]), d["ql"], d["root_p"], d["hips_d"], key), info


def scan_bone_names(file, P):
    """Bone names of a take without retargeting (BVH parsed directly, others via Blender)."""
    if file.lower().endswith(".bvh"):
        import re
        return re.findall(r"(?:ROOT|JOINT)\s+(\S+)", open(file, errors="ignore").read(400000))
    npz = os.path.join(P["work"], "_scan.npz"); os.makedirs(P["work"], exist_ok=True)
    subprocess.run([blender_bin(), "-b", "--factory-startup", "--python", os.path.join(HERE, "blender_import.py"), "--",
                    "--source", file, "--out", npz], capture_output=True, text=True)
    return [str(x) for x in np.load(npz, allow_pickle=True)["names"]]


def clip_range(c, take, fps_src, skel, window_default=None):
    """-> (a, b) in 30 fps take frames. c['frames'] are SOURCE frames (file order, 0 = first frame);
    c['seconds'] is source time; c['range'] = 'auto' searches a loop window."""
    F = take.F; fps = take.fps
    if "frames" in c:
        a, b = c["frames"]; return int(round(a / fps_src * fps)), min(F - 1, int(round(b / fps_src * fps)))
    if "seconds" in c:
        a, b = c["seconds"]; return int(round(a * fps)), min(F - 1, int(round(b * fps)))
    return 0, F - 1


def auto_cycle(take, skel, c):
    win = c.get("window_s"); per = c.get("period_s", [0.8, 1.6])
    rooted = al.extract_root(skel, take, root_motion=True, yaw_mode="constant", smooth_s=0.5)
    cands = al.find_cycles(skel, rooted, period_s=per, window=win, top=5)
    # prefer frames with room before them for the seam crossfade
    K = c.get("blend_frames", 8)
    ok = [x for x in cands if x[1] - K >= 0] or cands
    return ok[0], cands


def _ref(ref, skel, clip, name, loop, rm):
    Wc, Pw = al.fk(skel, clip)
    fr = sorted({0, clip.F // 3, clip.F // 2, (2 * clip.F) // 3, clip.F - 1})
    ref["clips"][name] = {"frames": fr, "pos": {j: Pw[fr, skel.i(j)].round(5).tolist() for j in REF_JOINTS if skel.has(j)},
                          "root_end": clip.root_p[-1].round(5).tolist(), "duration": clip.duration, "loop": bool(loop), "root_motion": bool(rm)}


def blend_clip(c, built, metas, skel, spec, FPS):
    """Offline diagonal (or any in-between) loop synthesized from two built loops of this set.
    c["blend"] = {"a": clip, "b": clip, "w": 0..1 | "auto" (w chosen so travel hits the clip's
    direction), "mode": "amplitude" | "linear", "phase_side": "l"}. Then foot lock on the loop."""
    bl = c["blend"]; name = c["name"]
    A, B = built.get(bl["a"]), built.get(bl["b"])
    if A is None or B is None: raise SystemExit("%s: blend inputs %s/%s must be clips of this set built before it" % (name, bl["a"], bl["b"]))
    mode = bl.get("mode", "amplitude"); w = bl.get("w", "auto")
    if w == "auto":
        # speeds of the inputs along the target direction's two axes
        dA = (A.root_p[-1] - A.root_p[0])[[0, 2]]; dB = (B.root_p[-1] - B.root_p[0])[[0, 2]]
        sA, sB = np.linalg.norm(dA) / A.duration, np.linalg.norm(dB) / B.duration
        w = sA / (sA + sB)          # equal contributions along both axes -> 45 deg
    def finish(clip):
        if c.get("plant", True):
            clip = al.plant_feet(skel, clip, clip.meta["stance"])
        if c.get("separate_legs", True):
            clip, _ = al.separate_legs(skel, clip)
        if c.get("foot_lock", True):
            clip = al.foot_lock_loop(skel, clip, stance=clip.meta.get("stance") if c.get("plant", True) else None,
                                     lock=True, ground=c.get("ground_clamp", True), strength=c.get("foot_lock_strength", 1.0))
        return al.renormalize(clip, skel)
    ph = bl.get("phase_side", "l")
    if ph == "validate":
        # candidates: every phase offset x both modes, each finished exactly as delivered and
        # scored with the validator's own measures (limb interpenetration beyond rest, foot slide,
        # seam velocity pop, stance); amplitude preferred on ties (keeps the inputs' speed)
        import validate_anim as va
        bodies = va.load_capsules(os.path.join(ROOT_CACHE["root"], spec["animation"].get("ragdoll_json", "")))
        rest = al.Clip(FPS, np.tile(np.array([0, 0, 0, 1.0]), (1, len(skel), 1)), np.zeros((1, 3)), (skel.P[skel.i("hips")] - skel.P[skel.i("root")])[None])
        rW, rP = al.fk(skel, rest); rpen = va.penetration(va.posed_capsules(skel, rW, rP, bodies))
        def score(cl):
            W, Pw = al.fk(skel, cl); pen = va.penetration(va.posed_capsules(skel, W, Pw, bodies))
            ex = max(float(np.max(v)) - max(0.0, float(np.max(rpen.get(k, 0)))) for k, v in pen.items()) * 100
            con, tr = al.detect_contacts(skel, cl, Wc=W, P=Pw); sl = al.slide_per_step(tr, con)
            slide = max([d for x in "lr" for _, d in sl[x]] or [0.1]) * 100
            miss = sum(20 for x in "lr" if con[x]["contact"].mean() < 0.3)
            r = skel.i("root"); rel = al.qrot(al.qconj(W[:, r])[:, None], Pw - Pw[:, r:r + 1])[:-1]
            acc = np.linalg.norm(np.roll(rel, -1, 0) - 2 * rel + np.roll(rel, 1, 0), axis=-1) * FPS
            pop = max(0.0, float(np.max(acc[0] - np.max(acc[1:], 0))))
            band = spec["animation"]["engine"]["locomotion_bands_mps"].get(c.get("speed_band") or "", 0)
            spd = float(np.linalg.norm((cl.root_p[-1] - cl.root_p[0])[[0, 2]]) / cl.duration)
            th = spec["animation"]["thresholds"]
            fails = (ex > th["interpenetration_cm"][1]) + (slide > th["foot_slide_cm"][1]) + (pop > th["loop_vel_mps"][1]) + \
                    (bool(band) and abs(spd - band) / band > th["speed_rel"][1])
            slow = 40 * max(0.0, 0.66 - spd / band) if band else 0.0
            return (100 * fails + 4 * max(0.0, ex - 1.5) + max(0.0, slide - 1.5) + miss + 10 * max(0.0, pop - 0.2) + slow,
                    (round(ex, 1), round(slide, 1), round(pop, 2), round(spd, 2)))
        pa = al.cycle_phase(skel, A, "l"); best = None
        for md in (mode, mode + ":0.9", mode + ":0.8", "linear") if mode == "amplitude" else (mode,):
            for k in range(bl.get("phase_steps", 24)):
                cand = al._blend_at(skel, A, B, float(w), md, pa, k / bl.get("phase_steps", 24))
                fin = finish(cand); sc, parts = score(fin)
                sc += {mode: 0.0, mode + ":0.9": 0.3, mode + ":0.8": 0.6}.get(md, 1.0)
                if os.environ.get("GANIM_BLEND_DEBUG"): print("  cand %-14s k%2d score %6.2f %s" % (md, k, sc, parts))
                if best is None or sc < best[0]: best = (sc, fin, cand.meta["blend"], parts)
        clip = best[1]; cr = {"blend": dict(best[2], phase_mode="validate", score=round(best[0], 2), pen_slide_pop=best[3])}
        mode = best[2]["mode"]
    else:
        clip = al.blend_cycles(skel, A, B, w=float(w), mode=mode, phase_side=ph)
        cr = {"blend": clip.meta["blend"]}
        clip = finish(clip)
    clip.name = name
    ma, mb = metas[bl["a"]], metas[bl["b"]]
    dur = clip.duration; disp = clip.root_p[-1] - clip.root_p[0]; speed = float(np.linalg.norm(disp[[0, 2]]) / dur)
    notes = c.get("notes", "")
    attrib = "Synthesized offline (game-animation blend_cycles, %s, w %.3f) from %s [%s] and %s [%s]" % (
        mode, w, bl["a"], ma["notes"].split(" | ")[-1], bl["b"], mb["notes"].split(" | ")[-1])
    lic = "; ".join(sorted({x for x in (ma.get("license"), mb.get("license")) if x}))
    meta = {"clip": name, "fps": FPS, "loop": True, "root_motion": True, "additive": False, "base": None,
            "tags": c.get("tags", []), "source": c.get("kind", ma.get("source", "mocap")),
            "notes": (notes + " | " if notes else "") + attrib, "frames": clip.F, "duration_s": round(dur, 4), "speed_mps": round(speed, 3),
            "direction": c.get("direction"), "speed_band": c.get("speed_band"),
            "source_file": [ma.get("source_file"), mb.get("source_file")], "source_frames": [ma.get("source_frames"), mb.get("source_frames")],
            "license": lic, "blend": cr["blend"]}
    cr.update(frames=clip.F, duration_s=round(dur, 3), speed_mps=round(speed, 3))
    return clip, meta, cr


def carry_clip(c, srcclips, built, src, info, skel, P, FPS):
    """A clip taken over unchanged from an engine-convention anim glb (e.g. the procedural
    placeholder), so a set can stay a drop-in replacement while some clips have no mocap yet.
    Additive clips are re-based: delta = old_base0^-1 * q, stored as new_base0 * delta."""
    name = c["name"]; sname = c.get("source_clip", name)
    if sname not in srcclips: raise SystemExit("%s: %s not in %s" % (name, sname, info["source_file"]))
    clip = srcclips[sname].copy(); clip.name = name
    loop = c.get("loop", name.endswith("_loop")); rm = c.get("root_motion", False); cr = {"carried_from": sname}
    if c.get("additive"):
        ob = srcclips.get(c.get("source_base", c.get("base"))); nb = built.get(c.get("base"))
        if ob is None or nb is None: raise SystemExit("%s: additive bases (source %s, new %s) not found" % (name, c.get("source_base", c.get("base")), c.get("base")))
        d = al.qmul(al.qconj(ob.ql[0])[None], clip.ql)
        clip.ql = al.qnorm(al.qmul(nb.ql[0][None], d)); clip.ql[:, 0] = np.array([0, 0, 0, 1.0])
        clip.hips_d = nb.hips_d[0][None] + (clip.hips_d - ob.hips_d[0][None]); clip.root_p = np.zeros_like(clip.root_p)
        cr["rebased"] = "%s -> %s" % (c.get("source_base", c.get("base")), c.get("base"))
    elif c.get("cleanup"):          # foot lock + ground clamp (authored clips that move the hips)
        clip = al.foot_lock(skel, clip, lock=True, ground=True); clip.name = name; cr["foot_lock"] = clip.meta.get("foot_lock")
    notes = c.get("notes", "")
    lic = src.get("license", "")
    meta = {"clip": name, "fps": FPS, "loop": bool(loop), "root_motion": bool(rm), "additive": bool(c.get("additive", False)),
            "base": c.get("base") if c.get("additive") else None, "tags": c.get("tags", []), "source": src.get("kind", "hand"),
            "notes": (notes + " | " if notes else "") + "Carried over unchanged from %s (%s)%s" % (os.path.relpath(info["source_file"], P["root"]), sname, "; licence: " + lic if lic else ""),
            "frames": clip.F, "duration_s": round(clip.duration, 4),     # carried root-motion clips: real speed + brief direction (was 0 / None: playback speed check failed)
            "speed_mps": round(float(np.linalg.norm((clip.root_p[-1] - clip.root_p[0])[[0, 2]]) / clip.duration), 3) if rm and clip.duration > 0 else 0.0,
            "direction": c.get("direction"), "speed_band": c.get("speed_band"),
            "source_file": os.path.relpath(info["source_file"], P["root"]), "source_frames": [0, clip.F - 1], "license": lic}
    cr.update(frames=clip.F, duration_s=round(clip.duration, 3))
    return clip, meta, cr


ROOT_CACHE = {}


def build(animset_path, only=None, verbose=True):
    a = json.load(open(animset_path)); P = paths_for(animset_path, a); ROOT_CACHE["root"] = P["root"]
    spec = P["spec"]; an = spec["animation"]; FPS = an.get("fps", 30)
    skel = al.load_target(spec, P["root"])
    takes, infos, carried = {}, {}, {}
    for key, src in a["sources"].items():
        if src.get("format") == "anim_glb":
            # a delivered anim glb in the engine convention (e.g. the procedural placeholder):
            # clips are carried over as they are (no retarget, no cleanup), see clip "source_clip"
            f = resolve_source(src["file"], P["root"], P["set_dir"])
            carried[key], _ = al.read_anim_glb(f, skel, spec)
            infos[key] = {"source_file": f, "source_fps": float(FPS), "mapping": "anim_glb"}
            if verbose: print("GANIM carry %s: %d clips from %s" % (key, len(carried[key]), os.path.basename(f)))
            continue
        raw, info = import_source(key, src, P, skel)
        t30 = al.resample(raw, FPS)
        if src.get("foot_pitch", "stance") == "stance":
            t30, fp = al.calibrate_foot_pitch(skel, t30); info["foot_pitch_fix_deg"] = fp
        if src.get("ground", "contacts") == "contacts":
            t30, off = al.ground_calibrate(skel, t30); info["ground_offset_cm"] = round(off * 100, 2)
        elif src.get("ground") == "plane":     # sloped capture floor
            t30, gp = al.ground_calibrate_plane(skel, t30); info["ground_offset_cm"] = gp["offset_cm"]; info["ground_slope_cm_per_m"] = gp["slope_cm_per_m"]
        takes[key] = t30; infos[key] = info
        if verbose: print("GANIM take %s: %d frames @%d (%s, %.1f s), scale %.4f, mapped %d bones, ground %+.1f cm, foot pitch fix %s" % (
            key, t30.F, FPS, info["mapping"], t30.duration, info["scale"], len(info["mapped"]), info.get("ground_offset_cm", 0), info.get("foot_pitch_fix_deg")))
    out, report, ref = [], {"set": a["id"], "clips": {}, "sources": infos}, {"joints": REF_JOINTS, "clips": {}}
    defaults = a.get("defaults", {})
    built, metas = {}, {}
    # bases first: plain clips, then blends of them (diagonals), then additives
    order = sorted(a["clips"], key=lambda c: 2 if c.get("additive") else 1 if c.get("blend") else 0)
    need = set(only or [])
    for c0 in a["clips"]:          # a rebuild of a blend/additive needs its inputs
        if only and c0["name"] in only:
            if c0.get("blend"): need |= {c0["blend"]["a"], c0["blend"]["b"]}
            if c0.get("additive") and c0.get("base"): need.add(c0["base"])
    for c0 in order:
        c = dict(defaults, **c0); name = c["name"]
        if only and name not in need: continue
        if c.get("blend"):
            clip, meta, cr = blend_clip(c, built, metas, skel, spec, FPS)
            built[name] = clip; metas[name] = meta; out.append((clip, meta)); report["clips"][name] = cr
            _ref(ref, skel, clip, name, True, True)
            if verbose: print("GANIM clip %-28s %3d fr %5.2fs loop rm speed %.2f m/s  blend %s" % (name, clip.F, clip.duration, meta["speed_mps"], cr["blend"]))
            continue
        src = a["sources"][c["source"]]; info = infos[c["source"]]
        loop = c.get("loop", name.endswith("_loop")); rm = c.get("root_motion", loop and "locomotion" in c.get("tags", []))
        cr = {}
        if c["source"] in carried:
            clip, meta, cr = carry_clip(c, carried[c["source"]], built, src, info, skel, P, FPS)
            built[name] = clip; metas[name] = meta; out.append((clip, meta)); report["clips"][name] = cr
            _ref(ref, skel, clip, name, meta["loop"], meta["root_motion"])
            if verbose: print("GANIM clip %-28s %3d fr %5.2fs carried from %s" % (name, clip.F, clip.duration, os.path.basename(info["source_file"])))
            continue
        take = takes[c["source"]]
        if c.get("range") == "auto":
            (cost, ra, rb), cands = auto_cycle(take, skel, c)
            cr["auto_cycle"] = {"cost_cm": round(cost, 2), "frames30": [ra, rb], "candidates": [[round(x[0], 2), x[1], x[2]] for x in cands]}
        else:
            ra, rb = clip_range(c, take, info["source_fps"], skel)
        M = int(c.get("margin_s", 1.0) * FPS)
        m0, m1 = max(0, ra - M), min(take.F - 1, rb + M)
        seg = take.slice(m0, m1)
        if c.get("ground_clip"):      # re-ground on this clip's own planted frames (uneven capture floors)
            seg, goff = al.ground_calibrate(skel, seg); cr["ground_clip_cm"] = round(goff * 100, 2)
        seg = al.extract_root(skel, seg, root_motion=rm, yaw_mode=c.get("root_yaw", "constant" if loop else "facing"),
                              smooth_s=c.get("root_smooth_s", 0.5 if loop else 0.3))
        if c.get("foot_lock", True) or c.get("ground_clamp", True):
            seg = al.foot_lock(skel, seg, lock=c.get("foot_lock", True), ground=c.get("ground_clamp", True),
                               strength=c.get("foot_lock_strength", 1.0))
            cr["foot_lock"] = seg.meta.get("foot_lock")
        if c.get("level_torso"):      # opt-in: {bone: target mean pitch deg} -- straighten an arched/hunched performer torso
            seg, cr["level_torso_deg"] = al.level_torso(skel, seg, c["level_torso"], keep_world=tuple(c.get("level_torso_keep", ("upper_leg_l", "upper_leg_r", "upper_arm_l", "upper_arm_r"))))
        if c.get("level_head"):
            seg, hc = al.level_head(skel, seg, float(c.get("head_pitch_deg", 0.0))); cr["level_head_deg"] = round(hc, 1)
        if c.get("separate_arms"):    # opt-in: forearms out of the torso on bodies wider than the performer
            import validate_anim as va
            sa = c["separate_arms"]; kw = {"torso": tuple(sa)} if isinstance(sa, list) else {}   # a list = the capsules to clear (e.g. + upper_leg_r)
            seg, cr["separate_arms_deg"] = al.separate_arms(skel, seg, va.load_capsules(os.path.join(P["root"], spec["animation"].get("ragdoll_json", ""))), **kw)
        if c.get("forearm_twist"):    # opt-in: share wrist twist with the forearm (CMU forearms have no twist channel)
            seg, cr["forearm_twist_deg"] = al.forearm_twist(skel, seg, float(c["forearm_twist"]))
        if c.get("body_ground"):      # opt-in: lying poses (deaths, prone) -- lift hips so no ragdoll capsule sinks > 3 cm (ragdoll handoff)
            import validate_anim as va
            bodies = va.load_capsules(os.path.join(P["root"], spec["animation"].get("ragdoll_json", "")))
            Wg, Pg = al.fk(skel, seg); caps = va.posed_capsules(skel, Wg, Pg, bodies)
            low = np.min(np.stack([np.minimum(cp[0][:, 1], cp[1][:, 1]) - cp[2] for cp in caps.values()]), 0)
            lift = np.maximum(0.0, -low - 0.025); k = 3; pad = np.pad(lift, k, mode="edge")
            lift = al.gauss_smooth(np.array([pad[i:i + 2 * k + 1].max() for i in range(len(lift))]), 1.5, "edge")
            seg = seg.copy(); seg.hips_d[:, 1] += lift; cr["body_ground_cm"] = round(float(lift.max()) * 100, 1)
        if loop:
            clip = al.blend_loop(seg, ra - m0, rb - m0, c.get("blend_frames", 8)); cr["loop"] = clip.meta.get("loop")
            if c.get("foot_lock", True) and c.get("loop_relock", True):     # the seam crossfade can reintroduce slide
                clip = al.foot_lock_loop(skel, clip, lock=True, ground=c.get("ground_clamp", True), strength=c.get("foot_lock_strength", 1.0))
                cr["foot_lock_loop"] = clip.meta.get("foot_lock_loop")
        else:
            clip = seg.slice(ra - m0, rb - m0)
        if c.get("hook"):             # opt-in: project-side fix "tools/x.py:fn" (relative to the set dir): fn(skel, clip, c, P) -> (clip, report)
            import importlib.util
            hp, hf = c["hook"].split(":"); spec_ = importlib.util.spec_from_file_location("anim_hook_" + hf, os.path.join(P["set_dir"], hp))
            mod = importlib.util.module_from_spec(spec_); spec_.loader.exec_module(mod)
            clip, cr["hook"] = getattr(mod, hf)(skel, clip, c, P)
        if c.get("arm_pose"):         # opt-in: keyed arm chains + fingers over the mocap body (scripts/arm_pose.py)
            import arm_pose
            clip, cr["arm_pose"] = arm_pose.apply(skel, clip, c["arm_pose"])
        clip = al.renormalize(clip, skel)
        if rm and c.get("align_direction") and c.get("direction"):
            clip, adeg = al.align_travel(skel, clip, c["direction"]); cr["align_direction_deg"] = round(adeg, 1)
        if not rm: clip = al.recenter_in_place(skel, clip)
        if c.get("additive"):
            base = built.get(c.get("base"))
            if base is None: raise SystemExit("%s: additive base %s must be a clip of this set" % (name, c.get("base")))
            clip = al.rebase_additive(clip, base, c.get("additive_ref_frame", 0)); rm = False
        clip.name = name; built[name] = clip
        dur = clip.duration; disp = clip.root_p[-1] - clip.root_p[0]
        speed = float(np.linalg.norm(disp[[0, 2]]) / dur) if dur > 0 and rm else 0.0
        credit = src.get("credit", ""); lic = src.get("license", "")
        srcframes = [int(round((ra) / FPS * info["source_fps"])), int(round(rb / FPS * info["source_fps"]))]
        notes = c.get("notes", "")
        attrib = "Source: %s%s, %s frames %d-%d @ %g fps; licence: %s" % (
            credit or os.path.basename(info["source_file"]), (" <%s>" % src["url"]) if src.get("url") else "",
            os.path.basename(info["source_file"]), srcframes[0], srcframes[1], info["source_fps"], lic or "see LICENSES.md")
        meta = {"clip": name, "fps": FPS, "loop": bool(loop), "root_motion": bool(rm), "additive": bool(c.get("additive", False)),
                "base": c.get("base") if c.get("additive") else None, "tags": c.get("tags", []), "source": src.get("kind", "mocap"),
                "notes": (notes + " | " if notes else "") + attrib,
                "frames": clip.F, "duration_s": round(dur, 4), "speed_mps": round(speed, 3), "direction": c.get("direction"),
                "speed_band": c.get("speed_band"), "source_file": os.path.relpath(info["source_file"], P["root"]) if info["source_file"].startswith(P["root"]) else os.path.basename(info["source_file"]),
                "source_frames": srcframes, "license": lic}
        out.append((clip, meta)); metas[name] = meta
        cr.update(frames=clip.F, duration_s=round(dur, 3), speed_mps=round(speed, 3), range30=[ra, rb], margin30=[m0, m1])
        report["clips"][name] = cr
        Wc, Pw = al.fk(skel, clip)
        fr = sorted({0, clip.F // 3, clip.F // 2, (2 * clip.F) // 3, clip.F - 1})
        ref["clips"][name] = {"frames": fr, "pos": {j: Pw[fr, skel.i(j)].round(5).tolist() for j in REF_JOINTS if skel.has(j)},
                              "root_end": clip.root_p[-1].round(5).tolist(), "duration": dur, "loop": bool(loop), "root_motion": bool(rm)}
        if verbose:
            print("GANIM clip %-28s %3d fr %5.2fs %s%s speed %.2f m/s%s" % (name, clip.F, dur, "loop " if loop else "", "rm " if rm else "", speed,
                  ("  cycle cost %.2f cm" % cr["auto_cycle"]["cost_cm"]) if "auto_cycle" in cr else ""))
    # keep the brief's clip order in the file
    names = [c["name"] for c in a["clips"] if not only or c["name"] in need]
    out.sort(key=lambda x: names.index(x[0].name))
    extras = {"ig_kind": "anim", "ig_set": a["id"], "ig_clips": len(out), "ig_bone_frames": an["engine"].get("bone_frames", "world_aligned")}
    al.write_anim_glb(P["glb"], skel, [x[0] for x in out], spec, a["id"], extras, frames=an["engine"].get("bone_frames", "world_aligned"))
    json.dump([x[1] for x in out], open(P["meta"], "w"), indent=2)
    os.makedirs(P["work"], exist_ok=True)
    json.dump(report, open(os.path.join(P["work"], "build_report.json"), "w"), indent=1, default=float)
    json.dump(ref, open(os.path.join(P["work"], "ref.json"), "w"))
    print("BUILD OK -> %s (%d clips) + %s" % (os.path.relpath(P["glb"], P["root"]), len(out), os.path.basename(P["meta"])))
    return report


if __name__ == "__main__":
    build(sys.argv[1], only=sys.argv[2:] or None)
