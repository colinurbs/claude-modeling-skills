#!/usr/bin/env python3
"""
validate_fp.py -- checks for first-person (view-model) sets and the hand-pose library, on top of the
file/meta/naming rules validate_anim applies to every set. Thresholds: spec animation.first_person.thresholds.

  item in frame        projected item vertices at the view-model FOV and the narrowest aspect, per frame_policy
  near plane           no visible arm/finger capsule or item vertex closer to the eye than near_margin_m
  hand on grip         support hand vs its target while holding (item / part / socket space); strong-hand IK reach
  fingers vs item      finger + palm capsules against the item's signed distance field (while holding)
  hand vs hand         strong vs support hand capsules
  loops                arm pose + item transform seam
  events               required events per action present, frames in range
  parts                every curve names an ig_parts part, values within [0, 1] (the part's travel/angle)
  speed                item muzzle speed / angular speed (pops), not on fire/melee
All measured on the exported glb (read back and FK'd), against the build's targets in _work/fp_ref.json.
"""
import json, math, os, re, subprocess, sys
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import animlib as al  # noqa: E402
import fplib as fl  # noqa: E402
import validate_anim as va  # noqa: E402
from validate_glb import Report, TOOLS as GAM_TOOLS  # noqa: E402

LIMITS = va.LIMITS

_ITEMS = {}


def _ITEM(item_id, assets_root, spec):
    """The clip's item (meta fp.item: hands sets carry one per clip; null = empty hands)."""
    if not item_id: return None
    if item_id not in _ITEMS:
        import fp_build
        _ITEMS[item_id] = fl.Item(fp_build.item_path({"root": assets_root, "spec": spec}, item_id), item_id)
    return _ITEMS[item_id]


def grade(rep, cid, val, th, detail):
    return va.grade(rep, cid, val, th, detail)


def action_of(name, item_key):
    m = re.match(r"^anim_fp_%s_(.+?)(_loop)?$" % re.escape(item_key), name)
    return m.group(1) if m else None


def run(glb, animset, spec, assets_root, meta_path=None, khronos=True):
    rep = Report(); an = spec["animation"]; fpc = an.get("first_person", {}); th = fpc.get("thresholds", {}); nm = an["naming"]
    FPS = an.get("fps", 30); mode = animset.get("mode")
    skel = al.load_target(spec, assets_root)
    set_id = animset["id"]
    rep.fail("set.id", bool(re.match(nm["set"], set_id)) and os.path.basename(glb) == set_id + ".glb", "%s -> %s" % (set_id, os.path.basename(glb)))
    if mode == "first_person":
        want = "anim_fp_" + re.sub(r"^wpn_", "", animset.get("item", "")) if animset.get("item") else None
        rep.warn("set.fp_naming", set_id.startswith("anim_fp_") and (want is None or set_id == want or set_id == "anim_fp_hands"), "%s (contract: %s)" % (set_id, want or "anim_fp_hands"))
    try:
        clips, raw = al.read_anim_glb(glb, skel, spec)
    except Exception as e:
        rep.fail("file.loads", False, str(e)[:200]); return rep, {}
    g = raw["gltf"]
    rep.fail("file.animations", len(clips) > 0, "%d animations" % len(clips))
    names = [a.get("name") for a in g.get("animations", [])]
    rep.fail("file.unique_names", len(set(names)) == len(names), "%d unique" % len(names))
    if khronos:
        js = os.path.join(GAM_TOOLS, "khronos_validate.mjs")
        if os.path.exists(os.path.join(GAM_TOOLS, "node_modules", "gltf-validator")):
            try:
                r = json.loads(subprocess.run(["node", js, glb], capture_output=True, text=True, timeout=120).stdout)
                rep.fail("khronos.errors", r["issues"]["numErrors"] == 0, "%d errors, %d warnings" % (r["issues"]["numErrors"], r["issues"]["numWarnings"]))
            except Exception as e: rep.warn("khronos.run", False, str(e)[:200])
    # -- tracks
    allowed = set(fl.finger_bones("l") + fl.finger_bones("r")) if mode == "hand_poses" else set(fpc.get("keyed_bones") or fl.keyed_bones(skel))
    animated = {n for c in raw["raw"].values() for n, p in c["paths"]}
    rep.fail("fp.keyed_bones", animated <= allowed, "extra: %s" % sorted(animated - allowed)[:6] if animated - allowed else "%d bones, all in the %s mask" % (len(animated), "finger" if mode == "hand_poses" else "arm-chain"))
    mask_of = (lambda c: set(fl.finger_bones(c[-1]))) if mode == "hand_poses" else (lambda c: allowed)
    per = [(c, mask_of(c) - {n for n, p in d["paths"]}) for c, d in raw["raw"].items()]
    if mode == "hand_poses":
        other = [(c, sorted({n for n, p in d["paths"]} - mask_of(c))[:3]) for c, d in raw["raw"].items() if {n for n, p in d["paths"]} - mask_of(c)]
        rep.fail("fp.named_side_only", not other, str(other[:3]) if other else "each clip keys only its own side's fingers (_r and _l layers combine)")
    miss = [(c, sorted(m)[:3]) for c, m in per if m]
    rep.fail("fp.all_mask_bones_keyed", not miss, str(miss[:3]) if miss else "every clip keys the whole mask (no bone left to another layer)")
    bad = sorted({n + ":" + p for d in raw["raw"].values() for n, p in d["paths"] if p != "rotation"})
    rep.fail("fp.rotation_only", not bad, ", ".join(bad[:6]) or "rotation channels only")
    rolled = [n.get("name") for n in g["nodes"] if n.get("name") in set(spec["skeleton"]["bones"]) and n.get("rotation") and abs(n["rotation"][3]) < 0.99999]
    rep.fail("bones.frame_convention", not rolled, "world_aligned: rest rotations identity" if not rolled else "rolled: %s" % rolled[:4])
    # -- meta
    mp = meta_path or glb[:-4] + ".meta.json"
    meta = {m["clip"]: m for m in json.load(open(mp))} if os.path.exists(mp) else {}
    rep.fail("meta.present", bool(meta), os.path.basename(mp))
    rep.fail("meta.matches_clips", set(meta) == set(clips), "%d entries" % len(meta) if set(meta) == set(clips) else "missing %s extra %s" % (sorted(set(clips) - set(meta)), sorted(set(meta) - set(clips))))
    req = an["meta"]["required"]; badm = [k for k, m in meta.items() if any(f not in m for f in req)]
    rep.fail("meta.fields", not badm, "missing fields in %s" % badm[:3] if badm else "all of %s" % ",".join(req))
    rep.fail("meta.source_enum", all(m.get("source") in an["meta"]["source_enum"] for m in meta.values()), "source hand")
    summary = {}
    ref = {}
    rp = os.path.join(os.path.dirname(os.path.abspath(animset.get("_path", ""))), "_work", "fp_ref.json")
    if mode == "first_person":
        if os.path.exists(rp): ref = json.load(open(rp)).get("clips", {})
        rep.fail("fp.reference", bool(ref), rp)
    item = None
    if mode == "first_person" and animset.get("item"):
        import fp_build
        P = {"root": assets_root, "spec": spec}
        item = fl.Item(fp_build.item_path(P, animset["item"]), animset["item"])
    E0 = fl.eye_point(skel); fov = fpc.get("viewmodel_fov_deg", 54); aspects = fpc.get("aspects", [1.3333, 1.7778])
    a_min, a_max = min(aspects), max(aspects)
    E0s = fpc.get("eye", {}).get("eye_m")
    if E0s: rep.fail("fp.eye_matches_skeleton", np.linalg.norm(np.array(E0s) - E0) < 0.002, "spec eye %s vs skeleton eye midpoint %s" % (E0s, E0.round(4).tolist()))
    bodies = va.load_capsules(os.path.join(assets_root, an.get("ragdoll_json", "")))
    arm_caps = [b for b in bodies if b["bone"].startswith(("upper_arm", "lower_arm"))]
    cut = 0.45   # char_fp_arms cut the upper arm at t=0.45 (ig_fp_cut): the shoulder end is not rendered
    item_key = re.sub(r"^wpn_", "", animset.get("item", "")) if animset.get("item") else "hands"
    skins = None
    if animset.get("fit_model") == "skin":       # grade contacts on the arms asset's own hand skin
        aid_ = an.get("first_person", {}).get("review", {}).get("arms", "char_fp_arms_bare_m_a")
        ap_ = os.path.join(assets_root, spec["paths"]["export"], "char", aid_ + ".glb")
        if os.path.exists(ap_): skins = {s_: fl.skin_hand(ap_, skel, s_) for s_ in "lr"}
    # finger joint limits + fingertip-into-palm on the arms' hand skin (every fp / hand-pose set)
    _aid = an.get("first_person", {}).get("review", {}).get("arms", "char_fp_arms_bare_m_a")
    _ap = os.path.join(assets_root, spec["paths"]["export"], "char", _aid + ".glb")
    palm_skins = {s_: fl.skin_hand(_ap, skel, s_) for s_ in "lr"} if os.path.exists(_ap) else None
    hms = {s_: fl.HandModel(skel, s_) for s_ in "lr"}; flim = dict(fl.FINGER_LIMITS, **fpc.get("finger_limits", {}))
    for name, clip in clips.items():
        pre = "clip[%s]." % name; m = meta.get(name, {}); fpm = m.get("fp", {})
        loop = m.get("loop", name.endswith("_loop"))
        rep.fail(pre + "name", bool(re.match(nm["clip"], name)), name)
        rep.fail(pre + "name.loop_suffix", loop == name.endswith("_loop"), "loop=%s" % loop)
        t = raw["raw"][name]["times"]; dt = np.diff(t) if t is not None and len(t) > 1 else np.array([1.0 / FPS])
        rep.fail(pre + "fps", abs(1 / np.median(dt) - FPS) <= FPS * an["thresholds"]["fps_tol"] and m.get("fps") == FPS, "%.2f fps" % (1 / np.median(dt)))
        Wc, P = al.fk(skel, clip)
        # joint limits (arm + fingers)
        worst = []
        for bn in fl.keyed_bones(skel):
            lim = LIMITS.get(bn) or (LIMITS.get("_fingers") if re.match(r"^(thumb|index|middle|ring|pinky)_", bn) else None)
            if not lim: continue
            sw, tw = al.swing_twist(clip.ql[:, skel.i(bn)], skel.axis(bn)); sw, tw = np.degrees(sw), np.degrees(np.abs(tw))
            if "hinge" in lim:
                ha = va.hinge_angle(skel, Wc, P, bn, lim["hinge_flex"])
                if ha is not None: worst.append((max(lim["hinge"][0] - float(np.min(ha)), float(np.max(ha)) - lim["hinge"][1]), bn, "hinge %.0f..%.0f" % (np.min(ha), np.max(ha))))
            else: worst.append((float(np.max(sw)) - lim["swing"], bn, "swing %.0f (limit %g)" % (np.max(sw), lim["swing"])))
            worst.append((float(np.max(tw)) - lim["twist"], bn, "twist %.0f (limit %g)" % (np.max(tw), lim["twist"])))
        worst.sort(reverse=True)
        grade(rep, pre + "joints.limits", max(0.0, worst[0][0]), (1e-6, an["thresholds"]["joint_limit_over_deg"][1]), "%s %s" % (worst[0][1], worst[0][2]))
        fo, pp, ffp, tfp = [], (0.0, ""), (0.0, "", {}), (0.0, "", {}); morphs_used = set()
        for sd in ((name[-1],) if mode == "hand_poses" else "lr"):   # pose clips key only their own side
            fb = [b_ for b_ in fl.finger_bones(sd) if skel.has(b_)]
            for f in range(clip.F):
                ql_ = {b_: clip.ql[f, skel.i(b_)] for b_ in fb}
                fo += [(o, b_, w + " f%d" % f) for o, b_, w in fl.finger_limit_overs(hms[sd], ql_, flim)]
                if palm_skins is not None:
                  # the skin checks run with the clip's corrective morphs applied (meta pose.morphs / fp.morphs, e.g. palm_fold_r: 1)
                  _mw = {k_: v_ for k_, v_ in dict((m.get("pose") or {}).get("morphs") or {}, **(fpm.get("morphs") or {})).items() if k_.endswith("_" + sd)}
                  with fl.with_morphs(palm_skins[sd], _mw) as _ma:
                    if _ma: morphs_used.update(_ma)
                    d_, fn_ = fl.fingertip_palm_pen(skel, sd, palm_skins[sd], ql_, hms[sd])
                    if d_ > pp[0]: pp = (d_, "%s_%s f%d" % (fn_, sd, f))
                    d_, pr_, gp_ = fl.finger_finger_pen(skel, sd, palm_skins[sd], ql_)
                    if d_ > ffp[0] or not ffp[2]: ffp = (d_, "%s_%s f%d" % (pr_ or "clear", sd, f), gp_)
                    d_, tf_, tg_ = fl.thumb_finger_pen(skel, sd, palm_skins[sd], ql_)
                    if d_ > tfp[0] or not tfp[2]: tfp = (d_, "%s_%s f%d" % (tf_ or "clear", sd, f), tg_)
        fo.sort(reverse=True)
        grade(rep, pre + "fp.finger_limits", max(0.0, fo[0][0]), th.get("finger_limit_over_deg", [1e-6, 10.0] if mode == "hand_poses" else [1e-6, float("inf")]),   # keyed/fitted fp sets: WARN only
              "%s %s %s, %.0f deg %s (anatomical ranges on the HandModel axes, fplib.FINGER_LIMITS)" % ("over:" if fo[0][0] > 0 else "closest to a limit:", fo[0][1], fo[0][2], abs(fo[0][0]), "over" if fo[0][0] > 0 else "inside"))
        if palm_skins is not None:
            # pose library: FAIL past 5 mm; fitted first-person grips: WARN only (the skin fit's thumb re-aim can curl a thumb into
            # its own palm -- the fit checks the item, not the palm; found by this check on 2026-10-04, fix per set)
            _mtag = ("; morphs %s" % ",".join(sorted(morphs_used))) if morphs_used else ""
            _tp = th.get("fingertip_palm_mm", [2.0, 5.0] if mode == "hand_poses" else [2.0, float("inf")])
            grade(rep, pre + "fp.fingertip_palm", pp[0], _tp, "deepest fingertip skin under the palm skin %.1f mm (%s; %s%s)%s" % (pp[0], pp[1] or "-", _aid, _mtag, "" if mode == "hand_poses" or pp[0] <= _tp[0] else " -- a fitted thumb/finger curled into its own palm: give the key explicit angles"))
            # adjacent fingers (index-middle, middle-ring, ring-pinky) must not interpenetrate on the skin: pose library FAIL past 5 mm, sets WARN only
            _tf = th.get("finger_finger_mm", [2.0, 5.0] if mode == "hand_poses" else [2.0, float("inf")])
            grade(rep, pre + "fp.finger_clearance", ffp[0], _tf, "deepest adjacent-finger skin penetration %.1f mm (%s; %s%s); pen/gap mm %s" % (ffp[0], ffp[1] or "-", _aid, _mtag, " ".join("%s %.1f/%.1f" % (k, v[0], v[1]) for k, v in ffp[2].items())))
            # thumb vs fingers (2026-10-13): graded only for library poses that declare "thumb_clear_mm" (the thumb must stay
            # that far off every finger's skin, e.g. the cup's thumb wall); fists/grips wrap the thumb over the fingers by design -> INFO
            _req = (fl.load_pose_library().get(name[len("anim_hand_"):-2], {}).get("thumb_clear_mm") if mode == "hand_poses" and name.startswith("anim_hand_") else None)
            _tdesc = "thumb skin vs fingers pen/gap mm %s (worst %s; %s%s)" % (" ".join("%s %.1f/%.1f" % (k, v[0], v[1]) for k, v in tfp[2].items()), tfp[1] or "-", _aid, _mtag)
            if _req is not None:
                _mg = min(v[1] for v in tfp[2].values()) if tfp[2] else float("inf")
                grade(rep, pre + "fp.thumb_clearance", tfp[0] + max(0.0, float(_req) - _mg), th.get("thumb_finger_mm", [1e-6, 2.0]), "need >= %g mm: %s" % (_req, _tdesc))
            else: rep.info(pre + "fp.thumb_clearance", _tdesc)
        else: rep.info(pre + "fp.fingertip_palm", "skipped: %s not delivered" % _aid)
        info = {"frames": clip.F, "duration_s": round(clip.duration, 3)}
        # wrist roll: the arms have no twist bones, so the hand's own roll vs the forearm stays small (candy wrapper)
        tws = []
        for sd in "lr":
            _, tw = al.swing_twist(clip.ql[:, skel.i("hand_" + sd)], skel.axis("lower_arm_" + sd)); tws.append((float(np.degrees(np.abs(tw)).max()), sd))
        tw, sd = max(tws)
        if mode != "hand_poses": grade(rep, pre + "fp.wrist_roll", tw, th.get("wrist_roll_deg", [30.0, 50.0]), "hand roll vs forearm up to %.0f deg (hand_%s); pronation belongs on lower_arm" % (tw, sd))
        if mode == "hand_poses":
            summary[name] = info; continue
        R = ref.get(name)
        if not R: rep.fail(pre + "fp.reference", False, "no fp_ref entry (rebuild)"); continue
        F = clip.F
        item = _ITEM(fpm.get("item"), assets_root, spec)
        _REQ = animset.get("events_required", {})   # per-action required events declared by the set (hands actions are not in the spec list)
        qa_r, pa_r = fl.attach_world(skel, Wc, P, "r"); qa_l, pa_l = fl.attach_world(skel, Wc, P, "l")
        gr = fpm.get("grip_r")
        ig = (item.extras.get("ig_grip_r") if item is not None else None)
        if ig:   # the item's ig_grip_r is the source of truth (engine applies it on attach): the clip's grip must be it
            if isinstance(ig, str): ig = json.loads(ig)
            dq = float(np.degrees(al.qangle(al.qmul(al.qconj(np.array(ig["r"], float)), np.array((gr or {}).get("r", [0, 0, 0, 1]), float)))))
            dt = float(np.linalg.norm(np.array(ig["t"], float) - np.array((gr or {}).get("t", [0, 0, 0]), float))) * 1000
            rep.fail(pre + "fp.grip_r_item", dt < 0.5 and dq < 0.1, "meta grip_r vs item ig_grip_r: %.2f mm, %.3f deg (rebuild the set after the item changes)" % (dt, dq))
        gr = fpm.get("regrip_r") or gr   # a clip's regrip_r (item_attach) places the item; grip_r_item above still checks meta grip_r
        if gr:   # the item hangs under attach_hand_r at the set's grip transform (meta fp.grip_r)
            gq, gt = np.array(gr["r"]), np.array(gr["t"])
            pa_r = pa_r + al.qrot(qa_r, np.broadcast_to(gt, pa_r.shape)); qa_r = al.qnorm(al.qmul(qa_r, np.broadcast_to(gq, qa_r.shape)))
        if R.get("hang_T"):   # hanging item (clip hang, meta fp.hang): carried at grip_r, then swung about ig_hang's pivot
            for _f, _h in enumerate(R["hang_T"]):
                qa_r[_f], pa_r[_f] = fl.tmul((qa_r[_f], pa_r[_f]), (np.array(_h["r"]), np.array(_h["t"])))
        _ra = np.array(R.get("attached_r", [True] * F), bool)
        _q0, _p0 = fl.attach_world(skel, Wc, P, "r")
        _iq = np.array([x["r"] for x in R["item_w"]]); _ip = np.array([x["t"] for x in R["item_w"]])
        qa_r = np.where(_ra[:, None], qa_r, _iq); pa_r = np.where(_ra[:, None], pa_r, _ip)
        _er = np.linalg.norm(_p0 - np.array([x["t"] for x in R["att_r_target"]]), axis=-1) * 100 if "att_r_target" in R else None
        _hold = R.get("holder", ["r"] * F); _mv, _wf = 0.0, -1
        for _f in range(1, F):
            if _hold[_f] == "world" and _hold[_f - 1] == "world":
                _d = float(np.linalg.norm(_ip[_f] - _ip[_f - 1])) * 1000
                if _d > _mv: _mv, _wf = _d, _f
        if item is not None and "world" in _hold: grade(rep, pre + "fp.item_world_static", _mv, (1.0, 5.0), "item nobody holds moves at most %.2f mm/frame (frame %d); windows %s" % (_mv, _wf, [w["holder"] + "@%d-%d" % tuple(w["frames"]) for w in fpm.get("item_attach", [])]))
        tgt_q = np.array([x["r"] for x in R["item_w"]]); tgt_p = np.array([x["t"] for x in R["item_w"]])
        # reach: exported attach_hand_r vs the brief's item target
        er = np.linalg.norm(pa_r - tgt_p, axis=-1) * 100; ea = np.degrees(al.qangle(al.qmul(al.qconj(tgt_q), qa_r)))
        if _er is not None: er = np.maximum(er, _er)
        grade(rep, pre + "fp.reach_r", float(er.max()), th.get("reach_cm", [0.5, 2.0]), "item (attach_hand_r o grip_r) vs brief target: max %.2f cm, %.2f deg at frame %d%s" % (er.max(), ea.max(), int(er.argmax()), " (arm cannot reach: pull the pose in)" if er.max() > 0.5 else ""))
        hold = np.array(R["hold_l"], bool)
        lq = np.array([x["r"] for x in R["att_l_target"]]); lp = np.array([x["t"] for x in R["att_l_target"]])
        el = np.linalg.norm(pa_l - lp, axis=-1) * 100
        if hold.any():
            grade(rep, pre + "fp.hand_on_grip_l", float(el[hold].max()), th.get("hand_on_grip_cm", [0.5, 1.5]), "support hand vs its grip over %d holding frames: max %.2f cm, %.2f deg (spaces %s)" % (
                hold.sum(), el[hold].max(), np.degrees(al.qangle(al.qmul(al.qconj(lq[hold]), qa_l[hold]))).max(), sorted(set(s for s, h in zip(R["space_l"], hold) if h))))
        free = ~hold & np.array([s not in ("rest",) for s in R["space_l"]])
        if free.any():
            grade(rep, pre + "fp.reach_l", float(el[free].max()), th.get("reach_cm", [0.5, 2.0]), "support hand vs its camera-space target: max %.2f cm" % el[free].max())
        # item geometry per frame (body + parts at their values; carried parts ride hand_l)
        pv = {p: np.array(v) for p, v in R["parts"].items()}
        _geo = item.geo if item is not None else {"_body": (np.zeros((0, 3)), None)}
        body = _geo["_body"][0]
        mz = item.socket("socket_muzzle") if item is not None else None
        _hp = _p0 if item is None else pa_r
        fr_in, mz_in, cover, centre, near_min, near_where = [], [], [], [], (9.0, ""), None
        pen_f, pen_h = (0.0, ""), (0.0, ""); spen_f, spen_h = (0.0, ""), (0.0, "")
        caps_r = fl.hand_capsules(skel, Wc, P, "r"); caps_l = fl.hand_capsules(skel, Wc, P, "l")
        ang_speed, lin_speed = 0.0, 0.0
        muz_prev = None; q_prev = None
        for f in range(F):
            iw = (qa_r[f], pa_r[f]); pvf = {p: float(v[f]) for p, v in pv.items()}
            vis = {p: R["visible"].get(p, [1] * F)[f] for p in pv}
            carried = R["carry_T"][f]
            pts = [fl.tpoint(iw, body)]
            for p in _geo:
                if p == "_body" or not vis.get(p, 1): continue
                if p in carried:
                    ct = (np.array(carried[p]["r"]), np.array(carried[p]["t"]))
                    pts.append(fl.tpoint(fl.tmul(iw, fl.tmul(ct, (fl.I4, -item.part_origin[p]))), item.geo[p][0]))
                else:
                    pts.append(fl.tpoint(fl.tmul(iw, item.part_local_delta(p, pvf[p])), item.geo[p][0]))
            V = np.vstack(pts)
            if _hold[f] == "gone": V = V[:0]   # released (thrown): no view-model item from the release frame on
            xn, yn, z = fl.project(V, E0, fov, a_min)
            inside = (np.abs(xn) <= 1) & (np.abs(yn) <= 1) & (z > 0)
            fr_in.append(float(inside.mean()) if len(V) else 1.0)
            if mz is not None:
                mp_ = fl.tpoint(fl.tmul(iw, mz), np.zeros(3)); mx, my, mzd = fl.project(mp_[None], E0, fov, a_min)
                mz_in.append(bool(abs(mx[0]) <= 1 and abs(my[0]) <= 1 and mzd[0] > 0))
                if muz_prev is not None: lin_speed = max(lin_speed, float(np.linalg.norm(mp_ - muz_prev)) * FPS)
                muz_prev = mp_
            if q_prev is not None: ang_speed = max(ang_speed, float(np.degrees(al.qangle(al.qmul(al.qconj(q_prev), qa_r[f])))) * FPS)
            q_prev = qa_r[f]
            if mz is None and (item is None or "item_attach" in fpm) and f > 0: lin_speed = max(lin_speed, float(np.linalg.norm(_hp[f] - _hp[f - 1])) * FPS)
            xw, yw, _ = fl.project(V, E0, fov, a_max)
            vv = (np.abs(xw) <= 1) & (np.abs(yw) <= 1) & (z > 0)
            if vv.any():
                x0, x1 = np.clip([xw[vv].min(), xw[vv].max()], -1, 1); y0, y1 = np.clip([yw[vv].min(), yw[vv].max()], -1, 1)
                cover.append(float((x1 - x0) * (y1 - y0) / 4) * 0.55)   # bbox area x a typical fill of a gun silhouette
                cr = th.get("centre_clear_ndc", 0.12)
                centre.append(bool(((np.abs(xw) < cr * 1.0) & (np.abs(yw) < cr) & (z > 0)).any()))
            else: cover.append(0.0); centre.append(False)
            # near plane: visible geometry closer than the margin
            cand = [(V, np.zeros(len(V)), "item")]
            for cn, a, b, r in caps_r + caps_l:
                cand.append((fl.capsule_points(a[f], b[f], 4), np.full(4, r), cn))
            for bdy in arm_caps:
                a = al.world_point(skel, Wc[f:f + 1], P[f:f + 1], bdy["bone"], bdy["capsule"]["a"])[0]; b = al.world_point(skel, Wc[f:f + 1], P[f:f + 1], bdy["bone"], bdy["capsule"]["b"])[0]
                if bdy["bone"].startswith("upper_arm"): a = a + (b - a) * cut
                cand.append((fl.capsule_points(a, b, 8), np.full(8, bdy["capsule"]["radius"]), bdy["name"]))
            for pts_, rr, cn in cand:
                xw2, yw2, zz = fl.project(pts_, E0, fov, a_max)
                d = zz - rr
                visible = (zz > 0) & (np.abs(xw2) <= 1 + 0.3) & (np.abs(yw2) <= 1 + 0.3)
                if visible.any():
                    dm = float(d[visible].min())
                    if dm < near_min[0]: near_min = (dm, "%s f%d" % (cn, f))
            # fingers vs item (strong hand always; support hand while holding)
            inv = fl.tinv(iw)
            for side, caps in (("r", caps_r), ("l", caps_l)):
                if item is None or _hold[f] == "gone" or (side == "l" and not hold[f] and _hold[f] != "l"): continue
                held = R["space_l"][f][5:] if side == "l" and R["space_l"][f].startswith("part:") else None
                held = held or next((p for p in carried if side == "l"), None)
                for cn, a, b, r in caps:
                    q = fl.capsule_points(a[f], b[f], 5)
                    sd = item.sdf(fl.tpoint(inv, q), pvf, exclude=(held,) if held else ())   # a hand holding a part (mag) grips it: not a collision
                    pen = float((r - sd).max())
                    if pen > pen_f[0]: pen_f = (pen, "%s f%d" % (cn if cn.endswith(("_l", "_r")) else cn + "_" + side, f))
            for cn, a, b, r in caps_r:
                for cn2, a2, b2, r2 in caps_l:
                    pen = float(r + r2 - va.seg_seg_dist(a[f], b[f], a2[f], b2[f]))
                    if pen > pen_h[0]: pen_h = (pen, "%s x %s f%d" % (cn, cn2, f))
            if skins is not None:   # the same contacts on the arms asset's own hand skin (sets fitted with fit_model skin)
                Vs = {}
                for side in "rl":
                    sk_ = skins[side]; Vs[side] = sk_.pose({b_: (Wc[f, skel.i(b_)], P[f, skel.i(b_)]) for b_ in sk_.bones})
                    if item is None or _hold[f] == "gone" or (side == "l" and not hold[f] and _hold[f] != "l"): continue
                    held = R["space_l"][f][5:] if side == "l" and R["space_l"][f].startswith("part:") else None
                    held = held or next((p for p in carried if side == "l"), None)
                    d = item.sdf(fl.tpoint(inv, Vs[side]), pvf, exclude=(held,) if held else ())
                    k = int(np.argmin(d))
                    if -d[k] > spen_f[0]: spen_f = (float(-d[k]), "%s f%d" % (sk_.dom[k], f))
                if hold[f] or _hold[f] == "l":
                    ob = fl.skin_sdf(Vs["r"], skins["r"].tris); d = ob(Vs["l"]); k = int(np.argmin(d))
                    if -d[k] > spen_h[0]: spen_h = (float(-d[k]), "%s into the strong hand f%d" % (skins["l"].dom[k], f))
        fr_in = np.array(fr_in); pol = fpm.get("frame_policy", "hold")
        keys = R.get("keys", [0, F - 1])
        span = {"hold": range(F), "enter": range(keys[-1], F), "exit": range(0, keys[0] + 1), "free": [0, F - 1], "show": list(range(*fpm.get("show", [0, F])))}.get(pol, range(F))
        span = [f for f in span if f < F]
        fmin = float(fr_in[span].min()) if span else 1.0
        va_ = th.get("in_frame_frac", [0.7, 0.5])
        rep.items.append({"level": "FAIL" if fmin < va_[1] else "WARN" if fmin < va_[0] else "PASS", "check": pre + "fp.in_frame",
                          "detail": "policy %s: min %.0f%% of the item on screen at %.2f aspect (frame %d), mean %.0f%%" % (pol, fmin * 100, a_min, span[int(np.argmin(fr_in[span]))] if span else -1, fr_in.mean() * 100)})
        if mz_in:
            mzi = np.array(mz_in); bad_m = [f for f in span if not mzi[f]]
            rep.items.append({"level": "FAIL" if (pol == "hold" and bad_m) else "WARN" if bad_m else "PASS", "check": pre + "fp.muzzle_in_frame", "detail": "muzzle off screen on %d of %d policy frames%s" % (len(bad_m), len(span), " (first %d)" % bad_m[0] if bad_m else "")})
        grade(rep, pre + "fp.screen_cover", max(cover) if cover else 0, th.get("screen_cover_max", [0.22, 0.35]), "item covers ~%.0f%% of the 16:9 screen at most" % (max(cover) * 100 if cover else 0))
        if pol == "hold":
            ct = sum(centre); rep.warn(pre + "fp.centre_clear", ct == 0, "item crosses the screen centre (crosshair) on %d frames" % ct)
        nm_ = th.get("near_margin_m", [0.04, 0.02])
        lvl = "FAIL" if near_min[0] < nm_[1] else "WARN" if near_min[0] < nm_[0] else "PASS"
        rep.items.append({"level": lvl, "check": pre + "fp.near_plane", "detail": "closest visible surface %.1f cm from the eye (%s); near %.2f m, margin %g/%g m" % (near_min[0] * 100, near_min[1], fpc.get("near_m", 0.01), *nm_)})
        if skins is not None:
            grade(rep, pre + "fp.fingers_vs_item", spen_f[0] * 1000, th.get("finger_item_mm", [2.5, 6.0]), "deepest hand SKIN (%s) inside the item %.1f mm (%s)" % (os.path.basename(skins["r"].glb), spen_f[0] * 1000, spen_f[1] or "-"))
            grade(rep, pre + "fp.hand_vs_hand", spen_h[0] * 1000, th.get("hand_hand_mm", [4.0, 10.0]), "deepest support-hand SKIN inside the strong hand %.1f mm (%s)" % (spen_h[0] * 1000, spen_h[1] or "-"))
            rep.info(pre + "fp.capsules", "bone-line capsule model (not graded for skin-fitted sets): fingers into item %.1f mm (%s), hand x hand %.1f mm (%s)" % (pen_f[0] * 1000, pen_f[1] or "-", pen_h[0] * 1000, pen_h[1] or "-"))
        else:
            grade(rep, pre + "fp.fingers_vs_item", pen_f[0] * 1000, th.get("finger_item_mm", [2.5, 6.0]), "deepest finger/palm capsule into the item %.1f mm (%s)" % (pen_f[0] * 1000, pen_f[1] or "-"))
            grade(rep, pre + "fp.hand_vs_hand", pen_h[0] * 1000, th.get("hand_hand_mm", [4.0, 10.0]), "deepest strong x support hand overlap %.1f mm (%s)" % (pen_h[0] * 1000, pen_h[1] or "-"))
        act = fpm.get("action") or action_of(name, item_key)
        fast = act in ("fire", "fire_last", "melee", "dry", "aim_fire") or "fast" in m.get("tags", [])
        if not fast:
            grade(rep, pre + "fp.item_speed", lin_speed, th.get("item_speed_mps", [4.0, 8.0]), "muzzle max %.2f m/s, rotation max %.0f deg/s" % (lin_speed, ang_speed))
        else:
            rep.info(pre + "fp.item_speed", "muzzle max %.2f m/s, rotation max %.0f deg/s (fast action: not graded)" % (lin_speed, ang_speed))
        if loop:
            rel = P - P[:, :1]
            pe = float(np.max(np.linalg.norm(rel[-1] - rel[0], axis=-1))) * 1000
            ie = float(np.linalg.norm(pa_r[-1] - pa_r[0])) * 1000
            grade(rep, pre + "loop.seam", max(pe, ie), th.get("loop_item_mm", [0.5, 2.0]), "last vs first: joints %.2f mm, item %.2f mm" % (pe, ie))
            if F >= 5:
                acc = np.linalg.norm(np.roll(rel[:-1], -1, 0) - 2 * rel[:-1] + np.roll(rel[:-1], 1, 0), axis=-1) * FPS
                pop = max(0.0, float(np.max(acc[0] - np.max(acc[1:], 0))))
                grade(rep, pre + "loop.seam_velocity", pop, an["thresholds"]["loop_vel_mps"], "velocity change at the seam beyond the clip's own max %.3f m/s" % pop)
        # events
        evs = fpm.get("events", [])
        need = fpc.get("events", {}).get("required", {}).get(act, [])
        need = fpc.get("events", {}).get("required_item", {}).get(fpm.get("item") or "", {}).get(act, need)   # per-item override (revolver: no case at fire)
        have = [e["name"] for e in evs]
        rep.fail(pre + "fp.events", all(n in have for n in need) and all(0 <= e["frame"] < F and abs(e["t"] - e["frame"] / FPS) < 1e-3 for e in evs),
                 "%s (required for %s: %s)" % (", ".join("%s@%d" % (e["name"], e["frame"]) for e in evs) or "none", act, ", ".join(need) or "-"))
        _need2 = _REQ.get(act, [])
        if _need2: rep.fail(pre + "fp.events_set", all(n in have for n in _need2), "set events_required for %s: %s" % (act, ", ".join(_need2)))
        known = set(fpc.get("events", {}).get("names", {})) | set(animset.get("event_names", {}))
        unk = [n for n in have if known and n not in known]
        rep.warn(pre + "fp.event_names", not unk, "not in the contract's event list: %s" % unk if unk else "all known")
        # parts
        pdef = item.parts_def if item is not None else {}
        badp = [p for p in fpm.get("parts", {}) if p not in pdef]
        rng_bad = [(p, round(min(v["values"]), 3), round(max(v["values"]), 3)) for p, v in fpm.get("parts", {}).items() if min(v["values"]) < -1e-4 or max(v["values"]) > 1 + 1e-4]
        lenbad = [p for p, v in fpm.get("parts", {}).items() if len(v["values"]) != F]
        rep.fail(pre + "fp.parts", not badp and not rng_bad and not lenbad, ("unknown %s " % badp if badp else "") + ("out of [0,1] %s " % rng_bad if rng_bad else "") + ("length %s" % lenbad if lenbad else "") or
                 "; ".join("%s %.2f-%.2f (%s)" % (p, min(v["values"]), max(v["values"]), ("%.1f mm" % (pdef[p]["travel_m"] * max(v["values"]) * 1000)) if pdef[p].get("move") == "translate" else ("%.0f deg + %.1f mm" % (pdef[p]["angle_deg"] * min(1, 2 * max(v["values"])), pdef[p]["travel_m"] * max(0, 2 * max(v["values"]) - 1) * 1000)) if pdef[p].get("move") == "rotate_then_translate" else "%.0f deg" % (pdef[p]["angle_deg"] * max(v["values"]))) for p, v in fpm.get("parts", {}).items()) or "no part curves")
        need_parts = {"fire": ["slide", "trigger", "hammer"], "fire_last": ["slide", "trigger"], "reload": ["mag"], "reload_empty": ["mag", "slide"]}.get(act, [])
        need_parts = [p for p in need_parts if p in pdef]
        rep.fail(pre + "fp.parts_driven", all(p in fpm.get("parts", {}) for p in need_parts), "%s must drive %s" % (act, need_parts) if need_parts else "n/a")
        info.update(in_frame_min=round(fmin, 2), near_cm=round(near_min[0] * 100, 1), finger_mm=round(pen_f[0] * 1000, 1), events=len(evs))
        summary[name] = info
        rep.info(pre + "summary", json.dumps(info))
    return rep, summary


if __name__ == "__main__":
    import argparse
    from validate_glb import find_spec
    ap = argparse.ArgumentParser(); ap.add_argument("animset"); a = ap.parse_args()
    import anim_build
    s = json.load(open(a.animset)); s["_path"] = a.animset; P = anim_build.paths_for(a.animset, s)
    rep, summ = run(P["glb"], s, P["spec"], P["root"], P["meta"])
    for it in rep.items: print("%-4s  %-44s %s" % (it["level"], it["check"], it["detail"]))
    print("VALIDATE %s" % ("FAIL" if rep.failed else "PASS"))
