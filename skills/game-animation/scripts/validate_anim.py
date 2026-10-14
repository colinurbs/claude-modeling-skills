#!/usr/bin/env python3
"""
validate_anim.py -- check an exported animation set (.glb + .meta.json) against the project
spec (assets/spec.json "animation", transcribed from ASSETS.md §0.8) and the set's
animset.json. Plain CPython + numpy. Prints one line per check like validate_glb:

    PASS|WARN|FAIL|INFO  check_id  detail

    python3 validate_anim.py <set.glb> --animset animset.json [--meta set.meta.json] [--json out.json] [--no-khronos]

Exit 0 when no FAIL. All motion measures are on the reference skeleton (char_skeleton_ref)
with root motion applied, in glTF space (+Y up, faces +Z, left = +X).
"""
import argparse, json, math, os, re, subprocess, sys
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import animlib as al  # noqa: E402
from validate_glb import Report, find_spec, TOOLS as GAM_TOOLS  # noqa: E402

LIMITS = json.load(open(os.path.join(os.path.dirname(HERE), "references", "joint_limits.json")))


def grade(rep, cid, val, th, detail, lower_is_better=True):
    w, f = th
    lvl = "FAIL" if val > f else "WARN" if val > w else "PASS"
    rep.items.append({"level": lvl, "check": cid, "detail": detail})
    return lvl


# --------------------------------------------------------------------------- geometry helpers

def seg_seg_dist(p1, q1, p2, q2):
    """Closest distance between segments p1q1 and p2q2 (vectorised over leading dims)."""
    d1 = q1 - p1; d2 = q2 - p2; r = p1 - p2
    a = np.sum(d1 * d1, -1); e = np.sum(d2 * d2, -1); f = np.sum(d2 * r, -1)
    c = np.sum(d1 * r, -1); b = np.sum(d1 * d2, -1); den = a * e - b * b
    s = np.where(den > 1e-12, np.clip((b * f - c * e) / np.where(den > 1e-12, den, 1), 0, 1), 0.0)
    t = (b * s + f) / np.maximum(e, 1e-12)
    t_c = np.clip(t, 0, 1)
    s = np.where(t < 0, np.clip(-c / np.maximum(a, 1e-12), 0, 1), np.where(t > 1, np.clip((b - c) / np.maximum(a, 1e-12), 0, 1), s))
    c1 = p1 + d1 * s[..., None]; c2 = p2 + d2 * t_c[..., None]
    return np.linalg.norm(c1 - c2, axis=-1)


def load_capsules(path):
    if not path or not os.path.exists(path): return []
    return json.load(open(path)).get("bodies", [])


def posed_capsules(skel, Wc, P, bodies):
    out = {}
    for b in bodies:
        if not skel.has(b["bone"]): continue
        cap = b["capsule"]
        out[b["name"]] = (al.world_point(skel, Wc, P, b["bone"], cap["a"]), al.world_point(skel, Wc, P, b["bone"], cap["b"]), cap["radius"], b.get("parent"))
    return out


# Torso x thigh is the hip-flexion fold: both hang off the pelvis and the ragdoll's torso capsule
# (one fat capsule, r ~16 cm) already touches the thigh tops at rest, so ANY sit, crouch or kneel
# reads as interpenetration there. Only the thigh's distal part (knee end) is tested against the
# torso: a knee in the chest is still caught, the belly/thigh fold is not.
FOLD_TRIM = {("spine", "upper_leg_l"): 0.45, ("spine", "upper_leg_r"): 0.45}


def penetration(caps, frames=None):
    """-> {pair: depth per frame (m)} for non-adjacent capsule pairs."""
    names = list(caps); res = {}
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            A, Bc = caps[names[i]], caps[names[j]]
            if A[3] == names[j] or Bc[3] == names[i]: continue          # parent/child joint
            tr = FOLD_TRIM.get((names[i], names[j])) or FOLD_TRIM.get((names[j], names[i]))
            if tr:                                                        # thigh starts at its distal part
                if names[i].startswith("upper_leg"): A = (A[0] + (A[1] - A[0]) * tr, A[1], A[2], A[3])
                else: Bc = (Bc[0] + (Bc[1] - Bc[0]) * tr, Bc[1], Bc[2], Bc[3])
            d = seg_seg_dist(A[0], A[1], Bc[0], Bc[1])
            res[(names[i], names[j])] = (A[2] + Bc[2]) - d
    return res


def hinge_angle(skel, Wc, P, b, flex):
    ch = al.TARGET_CHILD.get(b); p = skel.parent[skel.i(b)]
    if not ch or not skel.has(ch): return None
    bi, ci = skel.i(b), skel.i(ch)
    u = P[:, bi] - P[:, p]; v = P[:, ci] - P[:, bi]
    u_rest = skel.P[bi] - skel.P[p]
    a_rest = np.cross(u_rest, np.asarray(flex, float)); a_rest /= np.linalg.norm(a_rest) + 1e-12
    a = al.qrot(Wc[:, p], a_rest)
    return np.degrees(np.arctan2(np.sum(np.cross(u, v) * a, -1), np.sum(u * v, -1)))


# --------------------------------------------------------------------------- main check

def run(glb, animset, spec, assets_root, meta_path=None, khronos=True):
    rep = Report(); an = spec["animation"]; th = an["thresholds"]; nm = an["naming"]
    FPS = an.get("fps", 30)
    skel = al.load_target(spec, assets_root)
    set_id = animset["id"] if animset else os.path.splitext(os.path.basename(glb))[0]
    rep.fail("set.id", bool(re.match(nm["set"], set_id)) and os.path.basename(glb) == set_id + ".glb",
             "%s -> %s" % (set_id, os.path.basename(glb)))
    try:
        clips, raw = al.read_anim_glb(glb, skel, spec)
    except Exception as e:
        rep.fail("file.loads", False, str(e)[:200]); return rep, {}
    g = raw["gltf"]
    rep.fail("file.animations", len(clips) > 0, "%d animations" % len(clips))
    names = [a.get("name") for a in g.get("animations", [])]
    rep.fail("file.unique_names", len(set(names)) == len(names), ", ".join(n for n in set(names) if names.count(n) > 1) or "%d unique" % len(names))
    # -- khronos (same validator as game-asset-modeling)
    if khronos:
        js = os.path.join(GAM_TOOLS, "khronos_validate.mjs")
        if os.path.exists(os.path.join(GAM_TOOLS, "node_modules", "gltf-validator")):
            try:
                r = json.loads(subprocess.run(["node", js, glb], capture_output=True, text=True, timeout=120).stdout)
                errs = r["issues"]["numErrors"]; msgs = [m["code"] for m in r["issues"]["messages"] if m["severity"] == 0][:5]
                rep.fail("khronos.errors", errs == 0, "%d errors, %d warnings %s" % (errs, r["issues"]["numWarnings"], msgs))
            except Exception as e:
                rep.warn("khronos.run", False, str(e)[:200])
    # -- bones / tracks
    bones = set(spec["skeleton"]["bones"])
    animated = {nm_ for c in raw["raw"].values() for nm_, p in c["paths"]}
    extra = sorted(animated - bones)
    rep.fail("bones.subset_of_skeleton", not extra, "unknown: %s" % extra if extra else "%d animated bones, all in the skeleton" % len(animated))
    rep.fail("bones.count", len(animated) <= spec["skeleton"].get("max_bones", 72), "%d <= %d" % (len(animated), spec["skeleton"].get("max_bones", 72)))
    scale = sorted({"%s:%s" % (c, nm_) for c, d in raw["raw"].items() for nm_, p in d["paths"] if p == "scale"})
    rep.fail("bones.no_scale_keys", an.get("allow_scale_keys", False) or not scale, ", ".join(scale[:6]) or "none")
    pos_ok = set(an.get("position_tracks", ["root", "hips"]))
    badpos = sorted({nm_ for d in raw["raw"].values() for nm_, p in d["paths"] if p == "translation" and nm_ not in pos_ok})
    rep.fail("bones.translation_only_root_hips", not badpos, ", ".join(badpos) or "root, hips only")
    att = sorted({nm_ for nm_ in animated if nm_.startswith("attach_")})
    rep.warn("bones.attach_unkeyed", not att, ", ".join(att) or "attach_* not animated")
    mode = an["engine"].get("bone_frames", "world_aligned")
    rolled = [n.get("name") for n in g["nodes"] if n.get("name") in bones and n.get("rotation") and abs(n["rotation"][3]) < 0.99999]
    rep.fail("bones.frame_convention", (not rolled) if mode == "world_aligned" else True,
             "%s: %s" % (mode, ("rest rotations on %s (rolled frames would be converted twice by retarget.ts)" % rolled[:4]) if rolled and mode == "world_aligned" else "rest rotations identity"))
    ir = [c for c, d in raw["raw"].items() for (nm_, p), (t, v, it) in d["tracks"].items() if it != "LINEAR"]
    rep.warn("tracks.linear", not ir, "non-LINEAR samplers in %s" % sorted(set(ir))[:4] if ir else "LINEAR")
    # -- meta
    mp = meta_path or glb[:-4] + ".meta.json"
    meta = {}
    if os.path.exists(mp):
        mj = json.load(open(mp)); ml = mj if isinstance(mj, list) else [dict(v, clip=k) for k, v in mj.items()]
        meta = {m["clip"]: m for m in ml}
        rep.fail("meta.present", True, os.path.basename(mp))
        rep.fail("meta.matches_clips", set(meta) == set(clips), "missing %s extra %s" % (sorted(set(clips) - set(meta)), sorted(set(meta) - set(clips))) if set(meta) != set(clips) else "%d entries" % len(meta))
        req = an["meta"]["required"]; bad = [(k, [f for f in req if f not in m]) for k, m in meta.items() if any(f not in m for f in req)]
        rep.fail("meta.fields", not bad, str(bad[:3]) if bad else "all of %s" % ",".join(req))
        badsrc = [k for k, m in meta.items() if m.get("source") not in an["meta"]["source_enum"]]
        rep.fail("meta.source_enum", not badsrc, ", ".join(badsrc) or "|".join(an["meta"]["source_enum"]))
        noattr = [k for k, m in meta.items() if m.get("source") != "hand" and not re.search(r"(?i)source:|licen[cs]e|cc0|cc-by|mixamo|cmu", m.get("notes", ""))]
        rep.warn("meta.attribution", not noattr, "no source/licence in notes: %s" % noattr if noattr else "source + licence in notes")
    else:
        rep.fail("meta.present", False, "missing %s" % mp)
    briefs = {c["name"]: dict(animset.get("defaults", {}), **c) for c in (animset or {}).get("clips", [])}
    rep.fail("brief.no_mirrored_clips", not any(c.get("mirror") for c in briefs.values()), "§0.8: author both sides")
    # -- per clip
    bodies = load_capsules(os.path.join(assets_root, an.get("ragdoll_json", "")))
    rest_clip = al.Clip(FPS, np.tile(np.array([0, 0, 0, 1.0]), (1, len(skel), 1)), np.zeros((1, 3)), (skel.P[skel.i("hips")] - skel.P[skel.i("root")])[None])
    rWc, rP = al.fk(skel, rest_clip)
    rest_pen = penetration(posed_capsules(skel, rWc, rP, bodies))
    summary = {}
    for name, clip in clips.items():
        pre = "clip[%s]." % name
        m = meta.get(name, {}); b = briefs.get(name, {})
        loop = m.get("loop", name.endswith(nm["loop_suffix"])); additive = m.get("additive", name.startswith(nm["additive_prefix"]))
        tags = set(m.get("tags", b.get("tags", [])))
        rm = m.get("root_motion", False)
        # naming
        rep.fail(pre + "name", bool(re.match(nm["clip"], name)), name)
        rep.fail(pre + "name.loop_suffix", loop == name.endswith(nm["loop_suffix"]), "loop=%s" % loop)
        rep.fail(pre + "name.additive_prefix", additive == name.startswith(nm["additive_prefix"]), "additive=%s" % additive)
        dm = re.search(r"_(%s)(_loop)?$" % "|".join(nm["directions"]), name)
        direction = m.get("direction") or b.get("direction") or (dm.group(1) if dm and ("locomotion" in tags or rm) else None)
        if direction:
            rep.fail(pre + "name.direction_suffix", bool(dm) and dm.group(1) == direction, "direction %s, name suffix %s" % (direction, dm.group(1) if dm else None))
        band = m.get("speed_band") or b.get("speed_band")
        mb = re.match(r"^anim_(walk|jog|run)_(%s)_loop$" % "|".join(nm["directions"]), name)
        if "locomotion" in tags and loop and not mb and not re.match(r"^anim_idle_[a-z0-9]+_loop$", name):
            rep.warn(pre + "name.engine_lookup", False, "LocomotionBlend looks up anim_{walk|jog|run}_{dir}_loop / anim_idle_a_loop; this name is only reachable via layers.play()")
        # fps / timing
        t = raw["raw"][name]["times"]; dt = np.diff(t) if t is not None and len(t) > 1 else np.array([1.0 / FPS])
        rep.fail(pre + "fps", abs(1 / np.median(dt) - FPS) <= FPS * th["fps_tol"] and np.ptp(dt) < 1e-4 and m.get("fps", FPS) == FPS,
                 "%.3f fps (keys every %.4f s), meta %s" % (1 / np.median(dt), float(np.median(dt)), m.get("fps")))
        # engine-space facts
        Wc, P = al.fk(skel, clip); r, h = skel.i("root"), skel.i("hips")
        rep.fail(pre + "root.on_ground", float(np.max(np.abs(clip.root_p[:, 1]))) * 100 <= th["root_height_cm"], "root y max %.2f cm" % (np.max(np.abs(clip.root_p[:, 1])) * 100))
        rq = clip.ql[:, r]; tilt = np.degrees(np.arcsin(np.clip(np.sqrt(rq[:, 0] ** 2 + rq[:, 2] ** 2), 0, 1)) * 2)
        rep.fail(pre + "root.yaw_only", float(np.max(tilt)) < 0.5, "root pitch/roll up to %.2f deg" % float(np.max(tilt)))
        travel = float(np.linalg.norm(clip.root_p[-1, [0, 2]] - clip.root_p[0, [0, 2]]))
        yaw_travel = float(np.degrees(np.max(al.qangle(al.qmul(al.qconj(rq[0])[None], rq)))))
        d_rest = skel.P[h] - skel.P[r]
        drift = float(np.max(np.linalg.norm((clip.hips_d - d_rest)[:, [0, 2]], axis=-1)))
        if rm:
            rep.warn(pre + "root_motion.present", travel > 0.05 or yaw_travel > 5, "root travels %.2f m, turns %.0f deg" % (travel, yaw_travel))
            grade(rep, pre + "root_motion.hips_follow_root", drift, th["hips_drift_m"], "hips strays up to %.1f cm from root (ground plane)" % (drift * 100))
        else:
            rep.fail(pre + "root_motion.root_static", travel < 0.01 and yaw_travel < 1, "root moves %.2f m / %.1f deg in a root_motion=false clip" % (travel, yaw_travel))
            grade(rep, pre + "root_motion.no_hips_displacement", drift, th["hips_drift_m"], "hips wander %.1f cm (displacement left on hips?)" % (drift * 100))
        # body facing vs root forward (+Z): the engine steers the root, the body must face where it points
        if "turn" not in tags and not additive:
            fy = al.facing_yaw(skel, P)
            ry = np.array([2 * math.atan2(q[1], q[3]) for q in rq])
            off = float(np.degrees(np.abs(np.angle(np.mean(np.exp(1j * (fy - ry)))))))
            if off > 60: rep.fail(pre + "root.facing", False, "body faces %.0f deg off the root's +Z on average (root yaw mode 'none' on a take that turned?)" % off)
            else: rep.warn(pre + "root.facing", off <= 25, "body faces %.0f deg off the root's +Z on average" % off)
        dur = clip.duration
        info = {"frames": clip.F, "duration_s": round(dur, 3), "travel_m": round(travel, 3)}
        # loops
        if loop:
            rel = al.qrot(al.qconj(Wc[:, r])[:, None], P - P[:, r:r + 1])
            pe = float(np.max(np.linalg.norm(rel[-1] - rel[0], axis=-1))) * 100
            rot_e = float(np.degrees(np.max(al.qangle(al.qmul(al.qconj(clip.ql[0]), clip.ql[-1])[1:]))))
            grade(rep, pre + "loop.seam_position", pe, th["loop_pos_cm"], "last vs first frame: %.2f cm max joint, %.2f deg max bone" % (pe, rot_e))
            if clip.F >= 5:
                # velocity pop = the extra change in velocity at the seam beyond the largest
                # frame-to-frame change the clip makes anywhere else (per joint), in m/s
                cyc = rel[:-1]; n = len(cyc)
                acc = np.linalg.norm(np.roll(cyc, -1, 0) - 2 * cyc + np.roll(cyc, 1, 0), axis=-1) * FPS   # (n, joints)
                pop = float(np.max(acc[0] - np.max(acc[1:], 0)))
                ve = max(0.0, pop)
                grade(rep, pre + "loop.seam_velocity", ve, th["loop_vel_mps"], "velocity change at the seam exceeds the clip's own max by %.3f m/s" % ve)
                rv = np.array([al.qrot(al.qconj(clip.ql[i, r]), clip.root_p[i + 1] - clip.root_p[i]) * FPS for i in range(clip.F - 1)])
                racc = np.linalg.norm(rv - np.roll(rv, 1, 0), axis=-1)
                rve = max(0.0, float(racc[0] - np.max(racc[1:]))) if len(racc) > 2 else 0.0
                grade(rep, pre + "loop.root_velocity", rve, th["loop_root_vel_mps"], "root velocity jump at the seam beyond the clip's own: %.3f m/s" % rve)
            if "idle" in tags or name.startswith("anim_idle"):
                lo, hi = an["durations_s"]["idle"]
                rep.warn(pre + "loop.idle_length", lo <= dur <= hi, "%.2f s (idles %g-%g s)" % (dur, lo, hi))
        # contacts, slide, ground
        contacts, tr = al.detect_contacts(skel, clip, h_tol=th["contact_height_m"], v_max=th["contact_speed_mps"], Wc=Wc, P=P)
        slides = al.slide_per_step(tr, contacts)
        nsteps = {s: len(contacts[s]["spans"]) for s in "lr"}
        allsl = [d for s in "lr" for (_, d) in slides[s]]
        if additive:
            rep.info(pre + "feet", "additive clip: contact/slide/ground checks skipped (deltas)")
        elif allsl:
            mx = max(allsl) * 100
            grade(rep, pre + "feet.slide_per_step", mx, th["foot_slide_cm"], "max %.2f cm, mean %.2f cm over %d contacts (l %d, r %d)" % (mx, np.mean(allsl) * 100, len(allsl), nsteps["l"], nsteps["r"]))
        elif rm:
            rep.info(pre + "feet.slide_per_step", "no foot contacts detected")
        low = min(float(np.min(x[:, 1] - 0.0)) for (x, h0) in tr.values())
        caps = posed_capsules(skel, Wc, P, bodies)
        cap_low = min([float(np.min(np.minimum(c[0][:, 1], c[1][:, 1]) - c[2])) for c in caps.values()] or [0])
        pen = max(0.0, -min(low, cap_low + 0.02)) * 100
        airborne = bool(tags & set(an.get("airborne_tags", ["jump", "vault", "mantle", "ladder", "air", "fall"])))
        if not additive and not airborne:
            per_frame_low = np.min(np.stack([x[:, 1] - h0 for (x, h0) in tr.values()]), 0)
            fl = max(0.0, float(np.percentile(per_frame_low, 10))) * 100
            grade(rep, pre + "feet.grounded", fl, th.get("float_cm", [2.0, 5.0]), "lowest foot sits %.1f cm above the ground on 90%% of frames%s" % (fl, "" if fl < 2 else " (floating: ground calibration / hips height)"))
        if not additive: grade(rep, pre + "feet.ground_penetration", pen, th["ground_penetration_cm"], "lowest foot point %.2f cm, lowest body capsule %.2f cm" % (low * 100, cap_low * 100))
        if "locomotion" in tags and rm and all(contacts[s_]["contact"].any() for s_ in "lr"):
            # step width: lateral spacing of the planted ankles in the root frame (real gait ~8-15 cm)
            lat = {s_: al.qrot(al.qconj(clip.ql[:, r]), P[:, skel.i("foot_" + s_)] - clip.root_p)[:, 0][contacts[s_]["contact"]].mean() for s_ in "lr"}
            sw = float(lat["l"] - lat["r"]) * 100
            rep.warn(pre + "feet.step_width", 6.0 <= sw <= 30.0, "planted ankles %.1f cm apart laterally (tightrope < 6, straddle > 30)" % sw)
        if loop and ("locomotion" in tags) and rm:
            spans_c = {s: contacts[s]["spans"] for s in "lr"}
            steps = {s: len(spans_c[s]) - (1 if len(spans_c[s]) > 1 and spans_c[s][0][0] == 0 and spans_c[s][-1][1] == clip.F - 1 else 0) for s in "lr"}
            rep.warn(pre + "loop.two_steps", steps["l"] == 1 and steps["r"] == 1, "contacts per foot l %d, r %d (a %d-step cycle has one each)" % (steps["l"], steps["r"], an.get("cycle_steps", 2)))
        # speed / direction
        if rm and dur > 0:
            speed = travel / dur; info["speed_mps"] = round(speed, 3)
            bandk = band or (mb.group(1) if mb else None)
            want = an["engine"]["locomotion_bands_mps"].get(bandk) if bandk else None
            if want:
                rel_e = abs(speed - want) / want
                grade(rep, pre + "speed.band", rel_e, th["speed_rel"], "%.2f m/s vs engine %s band %.2f m/s (%+.0f%%)" % (speed, bandk, want, (speed - want) / want * 100))
            else:
                rep.info(pre + "speed", "%.2f m/s" % speed)
            if direction and travel > 0.1:
                dv = an["directions_gltf"][direction]
                ang = abs((math.degrees(math.atan2(clip.root_p[-1, 0] - clip.root_p[0, 0], clip.root_p[-1, 2] - clip.root_p[0, 2]) - math.atan2(dv[0], dv[1])) + 180) % 360 - 180)
                grade(rep, pre + "direction", ang, th["direction_deg"], "travel %.0f deg off the _%s axis" % (ang, direction))
        # joint limits
        worst = []
        for bn, lim in LIMITS.items():
            if bn.startswith("_"): continue
            if not skel.has(bn): continue
            bi = skel.i(bn); axis = skel.axis(bn)
            sw, tw = al.swing_twist(clip.ql[:, bi], axis)
            sw, tw = np.degrees(sw), np.degrees(np.abs(tw))
            if "hinge" in lim:
                ha = hinge_angle(skel, Wc, P, bn, lim["hinge_flex"])
                if ha is not None:
                    over = max(lim["hinge"][0] - float(np.min(ha)), float(np.max(ha)) - lim["hinge"][1])
                    worst.append((over, bn, "hinge %.0f..%.0f (limit %g..%g)" % (np.min(ha), np.max(ha), *lim["hinge"])))
            else:
                worst.append((float(np.max(sw)) - lim["swing"], bn, "swing %.0f (limit %g)" % (np.max(sw), lim["swing"])))
            worst.append((float(np.max(tw)) - lim["twist"], bn, "twist %.0f (limit %g)" % (np.max(tw), lim["twist"])))
        fl = LIMITS.get("_fingers")
        if fl:
            for bn in skel.names:
                if re.match(r"^(thumb|index|middle|ring|pinky)_0\d_[lr]$", bn):
                    sw, tw = al.swing_twist(clip.ql[:, skel.i(bn)], skel.axis(bn))
                    worst.append((float(np.degrees(np.max(sw))) - fl["swing"], bn, "swing %.0f (limit %g)" % (np.degrees(np.max(sw)), fl["swing"])))
        worst.sort(reverse=True)
        over = [w for w in worst if w[0] > 0]
        grade(rep, pre + "joints.limits", max(0.0, worst[0][0]) if worst else 0.0, (th["joint_limit_over_deg"][0] + 1e-6, th["joint_limit_over_deg"][1]),
              ("; ".join("%s %s" % (w[1], w[2]) for w in over[:4])) if over else "all bones within limits (closest: %s %s)" % (worst[0][1], worst[0][2]) if worst else "no table")
        # torso lean back: mean pitch of pelvis + lumbar (hips, spine) in the root frame. A performer leaning back
        # under raised arms reads as an arched back on the bodies (anim_react hands_up, 100STYLE ArmsAboveHead:
        # hips -8 / spine -10 deg). Fix with the clip's level_torso option, not the threshold.
        exempt = set(an.get("torso_lean_exempt_tags", ["sit", "lying", "death", "dead", "stagger", "hit", "dragged", "fall", "ragdoll"]))
        if not (tags & exempt) and skel.has("spine"):
            tp = al.torso_pitch(skel, clip, ("hips", "spine"), Wc)
            back = -float(np.mean([np.mean(v) for v in tp.values()]))
            grade(rep, pre + "pose.torso_lean_back", max(0.0, back), tuple(th.get("torso_lean_back_deg", [8.0, 20.0])),
                  "pelvis+lumbar lean %+.1f deg (+ = back; hips %+.1f, spine %+.1f)" % (back, -np.mean(tp["hips"]), -np.mean(tp["spine"])))
        # self-interpenetration
        pen_pairs = penetration(caps)
        excess = {k: v - max(0.0, float(np.max(rest_pen.get(k, 0)))) for k, v in pen_pairs.items()}
        if excess:
            k, v = max(excess.items(), key=lambda kv: float(np.max(kv[1])))
            vm = float(np.max(v)) * 100; fr = int(np.argmax(v))
            grade(rep, pre + "body.interpenetration", max(0.0, vm), th["interpenetration_cm"], "deepest %s x %s %.1f cm beyond rest at frame %d" % (k[0], k[1], vm, fr))
        # end pose (hit / death)
        endpose = bool(tags & set(an.get("end_pose_tags", []))) or bool(re.search(an.get("end_pose_name_re", "$^"), name))
        if endpose and not loop:
            n = max(2, int(th["end_pose_window_s"] * FPS)); n = min(n, clip.F - 1)
            sp = float(np.max(np.linalg.norm(np.diff(P[-n:], axis=0), axis=-1)) * FPS)
            grade(rep, pre + "end_pose.settled", sp, th["end_pose_speed_mps"], "fastest joint over the last %.2f s: %.3f m/s" % (n / FPS, sp))
            if excess:
                vend = max(float(v[-1]) for v in excess.values()) * 100
                grade(rep, pre + "end_pose.no_interpenetration", max(0.0, vend), th["end_pose_interpenetration_cm"], "deepest limb overlap at the last frame %.1f cm" % vend)
        # additive
        if additive:
            base = m.get("base") or b.get("base")
            rep.fail(pre + "additive.base", bool(base) and base in clips, "base %s %s" % (base, "in this set" if base in clips else "NOT in this set (the engine resolves bases inside one ClipLibrary)"))
            rep.fail(pre + "additive.no_root_motion", not rm and travel < 0.01, "root static")
        info.update(contact_spans=nsteps)
        summary[name] = info
        rep.info(pre + "summary", json.dumps(info))
    return rep, summary


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("glb"); ap.add_argument("--animset"); ap.add_argument("--meta"); ap.add_argument("--spec")
    ap.add_argument("--json"); ap.add_argument("--no-khronos", action="store_true")
    a = ap.parse_args()
    spec_path = a.spec or find_spec(a.animset or a.glb); spec = json.load(open(spec_path))
    animset = json.load(open(a.animset)) if a.animset else None
    rep, summ = run(a.glb, animset, spec, os.path.dirname(spec_path), a.meta, khronos=not a.no_khronos)
    for it in rep.items: print("%-4s  %-44s %s" % (it["level"], it["check"], it["detail"]))
    nf = len(rep.failed)
    print("VALIDATE %s: %d fail, %d warn" % ("FAIL" if nf else "PASS", nf, sum(1 for i in rep.items if i["level"] == "WARN")))
    if a.json: json.dump({"result": "FAIL" if nf else "PASS", "items": rep.items, "summary": summ}, open(a.json, "w"), indent=1)
    sys.exit(1 if nf else 0)


if __name__ == "__main__":
    main()
