"""
arm_pose.py -- keyed upper-body override on a mocap clip (third person).

Clip option `arm_pose` (anim_build): both arm chains (clavicle, upper arm, forearm, hand, fingers)
are replaced by a keyed pose, solved by 2-bone IK per frame on top of the mocap legs and torso.
The arms ride the (mocap) upper chest, so the body's own breathing and weight shifts carry
through; small loop-periodic secondary motion (sway, breath lift, hand tremor) keeps the hold
alive. Built for anim_civ_hands_up_loop (100STYLE ArmsAboveHead read as stretching: hands
touching overhead), usable for any held arm pose.

    "arm_pose": {
      "hand_sep_m": 0.45,       palm-centre separation (m)
      "elbow_deg": 25,          elbow flex (0 = straight)
      "forward_deg": 10,        shoulder->wrist line forward of vertical (side view, root frame)
      "pole": [1, 0, 0.35],     elbow direction for the left arm [lateral, up, forward] (mirrored for the right)
      "palm": [0, 0, 1],        palm normal (left arm, [lateral, up, forward]); "fingers_up_deg": hand long
                                axis tilt back from the forearm-ish vertical (wrist extension)
      "fingers": "open_palm" | {"pose": ..., "thumb": {...}}   (references/fp_hand_poses.json)
      "shrug": 0.18, "shrug_max_deg": 18,     clavicle share of the arm swing (fear hunch)
      "sway_deg": 1.5 (angular, about the shoulder), "breath_m": 0.004, "breath_cycles": 2,
      "tremor_deg": 0.6, "tremor_m": 0.0015, "tremor_finger_deg": 1.5, "tremor_hz": [5, 8],
      "seed": 7,
      "sides": "lr" | "l" | "r",          only these arm chains are replaced (default both)
      "hand_dir": [lat, up, fwd],         hand long axis (left arm, wrist -> knuckles) instead of the vertical
                                          hands-up default (e.g. [-1, 0.1, 0.2]: across the chest, watch check)
      "weight_keys": [[t_s, w], ...]      blend keyed pose over the mocap arm per frame (smoothstep between keys;
                                          default 1 throughout): raise-hold-lower one-shots on top of the mocap
    }

All secondary motion is periodic over the clip's F-1 frames, so a loop's last frame stays its first.
"""
import math
import numpy as np
import animlib as al
import fplib as fl

DEG = math.pi / 180


def _unit(v): v = np.asarray(v, float); return v / np.linalg.norm(v)


def _side_vec(v, side):
    """[lateral, up, forward] for the LEFT arm (lateral = outward) -> model space (+X = character's left)."""
    s = 1.0 if side == "l" else -1.0
    return np.array([s * v[0], v[1], v[2]], float)


def _frame_rot(d0, n0, d1, n1):
    def basis(d, n):
        d = _unit(d); n = np.asarray(n, float) - np.dot(n, d) * d; n = _unit(n); return np.stack([d, n, np.cross(d, n)], -1)
    return al.qfrom_mat(basis(d1, n1) @ basis(d0, n0).T)


def _cyc_noise(F, cycles, amp, rng):
    """Sum of sines with integer cycle counts over F-1 frames (exactly periodic), peak-normalised to amp."""
    t = np.arange(F) / max(1, F - 1); x = np.zeros(F)
    for c in cycles:
        x += rng.normal() * np.sin(2 * np.pi * c * t + rng.random() * 2 * np.pi)
    m = np.abs(x).max()
    return x * (amp / m if m > 0 else 0.0)


def measure(skel, clip):
    """Per-frame pose numbers: palm-centre separation (m), elbow flex (deg, per side), shoulder->wrist
    line forward of vertical (deg, per side, root frame), hand height above the head (m)."""
    Wc, P = al.fk(skel, clip); r = skel.i("root")
    qi = al.qconj(Wc[:, r])
    rel = lambda n: al.qrot(qi, P[:, skel.i(n)] - P[:, r])
    out = {}
    pc = {s: 0.5 * (rel("hand_" + s) + rel("middle_01_" + s)) for s in "lr"}
    out["hand_sep_m"] = np.linalg.norm(pc["l"] - pc["r"], axis=-1)
    for s in "lr":
        u = rel("lower_arm_" + s) - rel("upper_arm_" + s); f = rel("hand_" + s) - rel("lower_arm_" + s)
        cos = np.sum(u * f, -1) / (np.linalg.norm(u, axis=-1) * np.linalg.norm(f, axis=-1))
        out["elbow_deg_" + s] = np.degrees(np.arccos(np.clip(cos, -1, 1)))
        a = rel("hand_" + s) - rel("upper_arm_" + s)
        out["forward_deg_" + s] = np.degrees(np.arctan2(a[:, 2], a[:, 1]))
        out["lateral_deg_" + s] = np.degrees(np.arctan2(np.abs(a[:, 0]), a[:, 1]))
        sg = 1.0 if s == "l" else -1.0
        out["upper_out_deg_" + s] = np.degrees(np.arctan2(sg * u[:, 0], u[:, 1]))     # + = elbow out from the shoulder
        out["fore_out_deg_" + s] = np.degrees(np.arctan2(sg * f[:, 0], f[:, 1]))      # + = hand out from the elbow
    out["hands_above_head_m"] = 0.5 * (pc["l"][:, 1] + pc["r"][:, 1]) - rel("head")[:, 1]
    return out


def apply(skel, clip, cfg):
    """-> (clip, report). See the module doc for cfg."""
    F = clip.F; out = clip.copy(); src = clip.ql.copy(); sides = cfg.get("sides", "lr"); rng = np.random.default_rng(int(cfg.get("seed", 7)))
    sep = float(cfg.get("hand_sep_m", 0.45)); el = float(cfg.get("elbow_deg", 25.0)) * DEG
    fwd = float(cfg.get("forward_deg", 10.0)) * DEG
    shrug = float(cfg.get("shrug", 0.18)); shrug_max = float(cfg.get("shrug_max_deg", 18.0))
    lib = fl.load_pose_library()
    ang = fl.pose_angles(lib, cfg.get("fingers", "open_palm"))
    r, uc = skel.i("root"), skel.i("upper_chest")
    Wc, P = al.fk(skel, clip)
    # body frame: the root (yaw only for in-place clips); targets are solved once against the clip-mean
    # upper chest, then carried in its frame so the arms ride the torso's own motion
    q_rel = al.qmean(al.qmul(al.qconj(Wc[:, r]), Wc[:, uc]))
    p_rel = np.mean(al.qrot(al.qconj(Wc[:, r]), P[:, uc] - P[:, r]), 0)
    l1 = {s: np.linalg.norm(skel.P[skel.i("lower_arm_" + s)] - skel.P[skel.i("upper_arm_" + s)]) for s in "lr"}
    l2 = {s: np.linalg.norm(skel.P[skel.i("hand_" + s)] - skel.P[skel.i("lower_arm_" + s)]) for s in "lr"}
    Puc0 = skel.P[uc]; qr_i = al.qconj(q_rel)
    to_uc = lambda v: Puc0 + al.qrot(qr_i, np.asarray(v, float) - p_rel)      # body (root-relative) -> upper-chest rest coords
    to_b = lambda v: p_rel + al.qrot(q_rel, np.asarray(v, float) - Puc0)
    targets = {}; S_b = {}; ql0 = np.tile(np.array([0, 0, 0, 1.0]), (len(skel), 1))
    for s in "lr":
        lng, n, _rad = fl.hand_axes(skel, s)
        pole_b = _unit(_side_vec(cfg.get("pole", [1.0, 0.0, 0.35]), s))
        palm_b = _unit(_side_vec(cfg.get("palm", [0.0, 0.0, 1.0]), s))
        up_b = _unit(_side_vec([math.sin(float(cfg.get("fingers_out_deg", 6.0)) * DEG), 1.0, -math.tan(float(cfg.get("fingers_back_deg", 4.0)) * DEG)], s))
        if cfg.get("hand_dir"): up_b = _unit(_side_vec(cfg["hand_dir"], s))
        hand_q_b = _frame_rot(lng, n, up_b, palm_b)
        pc_off = al.qrot(hand_q_b, 0.5 * (skel.P[skel.i("middle_01_" + s)] - skel.P[skel.i("hand_" + s)]))
        d = math.sqrt(l1[s] ** 2 + l2[s] ** 2 + 2 * l1[s] * l2[s] * math.cos(el))
        targets[s] = dict(q=hand_q_b, pole=pole_b, pc_off=pc_off, d=d)
        S_b[s] = to_b(skel.P[skel.i("upper_arm_" + s)])
    # iterate: shoulder joints after the clavicle shrug -> wrists at distance d (elbow flex), forward_deg, palm centres
    # sep apart, centred between the two shoulders (the mocap torso may sit rolled/shifted: keeps the arms symmetric)
    for _ in range(8):
        cx = 0.5 * (S_b["l"][0] + S_b["r"][0])
        for s in "lr":
            t = targets[s]
            dx = cx + (1 if s == "l" else -1) * sep / 2 - t["pc_off"][0] - S_b[s][0]; rest = math.sqrt(max(t["d"] ** 2 - dx * dx, 1e-6))
            t["p"] = S_b[s] + np.array([dx, rest * math.cos(fwd), rest * math.sin(fwd)]); t["S"] = S_b[s].copy()
            fl.solve_arm(skel, ql0, s, al.qmul(qr_i, t["q"]), to_uc(t["p"]), al.qrot(qr_i, t["pole"]), shrug=shrug, shrug_max_deg=shrug_max)
        c = al.Clip(30, ql0[None], np.zeros((1, 3)), np.zeros((1, 3)) + skel.P[skel.i("hips")] - skel.P[r])
        _, Pp = al.fk(skel, c)
        for s in "lr": S_b[s] = to_b(Pp[0, skel.i("upper_arm_" + s)])
    # secondary motion (periodic over F-1 frames). Sway is angular about the shoulder (keeps the elbow bend steady);
    # breath lifts the hands; tremor is a faint 5-8 Hz hand/finger shake
    hz = cfg.get("tremor_hz", [5.0, 8.0]); T = (F - 1) / clip.fps
    tcyc = list(range(max(1, int(round(hz[0] * T))), max(2, int(round(hz[1] * T))) + 1, max(1, int(round(T)))))
    bc = int(cfg.get("breath_cycles", 2))
    breath = np.sin(2 * np.pi * bc * np.arange(F) / max(1, F - 1)) * float(cfg.get("breath_m", 0.004))
    sway = float(cfg.get("sway_deg", 1.5))
    common = [_cyc_noise(F, [1, 2], sway, rng) for _ in range(2)]
    sec = {}
    for s in "lr":
        t = targets[s]
        own = [_cyc_noise(F, [1, 2, 3], sway * 0.35, rng) for _ in range(2)]
        trem_p = [_cyc_noise(F, tcyc, float(cfg.get("tremor_m", 0.0015)), rng) for _ in range(3)]
        trem_r = [_cyc_noise(F, tcyc, float(cfg.get("tremor_deg", 0.6)), rng) for _ in range(2)]
        trem_f = _cyc_noise(F, tcyc, float(cfg.get("tremor_finger_deg", 1.5)), rng)
        arm = t["p"] - t["S"]; P_f = np.zeros((F, 3))
        for f in range(F):
            q = al.qmul(al.qaxis(np.array([0, 0, 1.0]), (common[0][f] + own[0][f]) * DEG), al.qaxis(np.array([1.0, 0, 0]), (common[1][f] + own[1][f]) * DEG))
            P_f[f] = t["S"] + al.qrot(q, arm) + np.array([trem_p[0][f], breath[f] + trem_p[1][f], trem_p[2][f]])
        sec[s] = (P_f - t["p"], trem_r, trem_f)
    hm = {s: fl.HandModel(skel, s) for s in "lr"}
    wk = cfg.get("weight_keys")
    if wk:      # per-frame keyed weight, smoothstep between keys
        tk = np.array([k[0] for k in wk], float) * clip.fps; wv = np.array([k[1] for k in wk], float)
        fr = np.arange(F, dtype=float); j = np.clip(np.searchsorted(tk, fr, side="right") - 1, 0, len(tk) - 1); j2 = np.minimum(j + 1, len(tk) - 1)
        u = np.where(j2 > j, np.clip((fr - tk[j]) / np.maximum(tk[j2] - tk[j], 1e-9), 0, 1), 0.0); u = u * u * (3 - 2 * u)
        wgt = np.where(fr < tk[0], wv[0], wv[j] + (wv[j2] - wv[j]) * u)
    else:
        wgt = np.ones(F)
    chain = {s: [skel.i(b) for b in fl.arm_bones(s)] + [skel.i(b) for b in hm[s].locals(ang)] for s in "lr"}
    for f in range(F):
        ql = out.ql[f]
        for s in sides:
            t = targets[s]; dp, trem_r, trem_f = sec[s]
            qb = al.qmul(al.qaxis(np.array([0, 0, 1.0]), trem_r[1][f] * DEG), al.qmul(al.qaxis(np.array([1.0, 0, 0]), trem_r[0][f] * DEG), t["q"]))
            # the arms ride the frame's own upper chest: constant in its frame (+ secondary)
            fl.solve_arm(skel, ql, s, al.qmul(qr_i, qb), to_uc(t["p"] + dp[f]), al.qrot(qr_i, t["pole"]), shrug=shrug, shrug_max_deg=shrug_max)
            a = {k: dict(v, flex=[x + trem_f[f] * (0.6 if k == "thumb" else 1.0) for x in v["flex"]]) for k, v in ang.items()}
            for b, q in hm[s].locals(a).items(): ql[skel.i(b)] = q
            if wgt[f] < 1.0:
                for bi in chain[s]: ql[bi] = al.qslerp(src[f, bi], ql[bi], float(wgt[f]))
    m = measure(skel, out)
    rep = {k: [round(float(v.min()), 3), round(float(v.mean()), 3), round(float(v.max()), 3)] for k, v in m.items()}
    return out, rep
