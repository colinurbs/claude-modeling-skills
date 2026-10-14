#!/usr/bin/env python3
"""
fp_build.py -- build a first-person (view-model) set or the hand-pose library from its animset.json.
Keyed, not captured: the brief holds named key poses (item transform relative to the camera, the
support hand in a named space, finger poses, elbow poles), per-clip keys with timing and easing,
moving-part curves, events and secondary motion. Per frame: interpolate keys -> secondary motion ->
support-hand target -> finger grip fit (once per key pose) -> 2-bone arm IK with clavicle share and
forearm twist -> canonical clip keyed on the arm chains only.

  mode "first_person": -> <export>/anim/<set>.glb + .meta.json (fp block per clip), _work/fp_ref.json
  mode "hand_poses":   -> the finger-pose library as 2-frame clips anim_hand_<pose>_<l|r> (finger bones only)

Driven by anim_cli.py (build/all dispatch on animset "mode"). Contract: references/first-person.md.
"""
import copy, json, math, os, sys, time
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import animlib as al  # noqa: E402
import fplib as fl  # noqa: E402
import anim_build  # noqa: E402


def fp_spec(spec):
    return spec["animation"].get("first_person", {})


def item_path(P, item_id):
    mp = os.path.join(P["root"], P["spec"]["paths"]["manifest"])
    for e in (json.load(open(mp)) if os.path.exists(mp) else []):
        if e.get("id") == item_id and os.path.exists(os.path.join(P["root"], e["path"])): return os.path.join(P["root"], e["path"])
    for k in ("export", "placeholder"):
        c = os.path.join(P["root"], P["spec"]["paths"][k], "props", item_id + ".glb")
        if os.path.exists(c): return c
    raise SystemExit("item not found: %s" % item_id)


def deep_merge(a, b):
    out = copy.deepcopy(a)
    for k, v in (b or {}).items():
        # a hand entry that names a grip or a space REPLACES the inherited one (no stale fingers/pos from the base)
        replace = k in ("hand_l", "hand_r") and isinstance(v, dict) and ("grip" in v or "space" in v)
        out[k] = deep_merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) and not replace else copy.deepcopy(v)
    return out


def resolve_pose(poses, name, seen=()):
    if name not in poses: raise SystemExit("unknown key pose %s (brief poses: %s)" % (name, ", ".join(poses)))
    p = poses[name]
    if p.get("base"):
        if p["base"] in seen: raise SystemExit("pose base cycle at %s" % name)
        return deep_merge(resolve_pose(poses, p["base"], seen + (name,)), {k: v for k, v in p.items() if k != "base"})
    return copy.deepcopy(p)


class Ctx:
    def __init__(self, a, P):
        self.a, self.P = a, P
        self.spec = P["spec"]; self.an = self.spec["animation"]; self.fps = self.an.get("fps", 30)
        self.fpc = fp_spec(self.spec)
        self.skel = al.load_target(self.spec, P["root"])
        self.E0 = fl.eye_point(self.skel)
        self.lib = fl.load_pose_library(a.get("hand_poses"))
        self.hands = {s: fl.HandModel(self.skel, s) for s in "lr"}
        self.item = fl.Item(item_path(P, a["item"]), a["item"]) if a.get("item") else None
        self.grips = a.get("grips", {})
        self.fits = {}
        self.fit_cache = {} if a.get("fit_memo", True) else None   # brief "fit_memo": false disables the cross-key fit memo
        self.defaults = dict(self.fpc.get("pole_default", {}))
        self.part_defaults = dict(a.get("part_defaults", {}))
        self.grip_r = self.solve_grip(a.get("grip_r"))
        # "fit_model": "skin" -> finger wrap fits on the view-model arms' own hand skin (fplib.fit_fingers_skin; the
        # support hand also clears the strong hand's posed skin) instead of the bone-line capsules
        self.skins = None
        if a.get("fit_model") == "skin":
            import anim_cli
            aid, ok = anim_cli.fp_arms(P)
            if ok: self.skins = {s: fl.skin_hand(anim_cli.part_path(P, aid), self.skel, s) for s in "lr"}
            else: print("GANIM fit_model skin: no char_fp_arms delivered, capsule fit")

    def solve_grip(self, g):
        """Strong-hand grip transform G (attach_hand_r frame in ITEM space; the item hangs at attach o G^-1).
        Source of truth: the item's own root extra `ig_grip_r` (its local TRS under attach_hand_r, authored by the
        weapons pipeline; the engine applies the same value on attach in first and third person). Only when the item
        has none: the brief's grip_r -- None -> identity (plain Rig.attach); {"pos", "rot"} -> as given; "auto" /
        {"auto": {web, x0, pose}} -> fplib.suggest_grip, cached in _work/grip_r.json on the item file + pose."""
        self.grip_r_source = "identity"
        if self.item is not None and self.item.extras.get("ig_grip_r"):
            ig = self.item.extras["ig_grip_r"]
            if isinstance(ig, str): ig = json.loads(ig)
            self.grip_r_source = "item ig_grip_r"
            print("GANIM grip_r from %s ig_grip_r: t %s r %s" % (self.item.id, ig["t"], ig["r"]))
            return fl.grip_from_trs(ig)
        if not g or self.item is None: return fl.T()
        self.grip_r_source = "brief (item has no ig_grip_r)"
        if g == "auto" or "auto" in g:
            opt = g.get("auto", {}) if isinstance(g, dict) else {}
            import hashlib
            st = os.stat(self.item.path); key = hashlib.sha1(("%s%d%d%s%d" % (self.item.path, st.st_size, int(st.st_mtime), json.dumps(opt, sort_keys=True), int(os.stat(fl.__file__).st_mtime))).encode()).hexdigest()[:12]
            cp = os.path.join(self.P["work"], "grip_r.json"); cache = json.load(open(cp)) if os.path.exists(cp) else {}
            if cache.get("key") != key:
                ang = fl.pose_angles(self.lib, opt.get("pose", "pistol_grip"))
                for f in opt.get("open_fingers", ["middle", "ring", "pinky"]): ang[f]["flex"] = [0.0, 0.0, 0.0]   # wrapped later by the grip fit
                sol, rep = fl.suggest_grip(self.skel, self.item, "r", ang, web=fl.brief_vec(opt["web"]) if opt.get("web") else None, x0=opt.get("x0"),
                                           knuckle=fl.brief_vec(opt["knuckle"]) if opt.get("knuckle") else None)
                cache = {"key": key, "grip": sol, "report": rep}; os.makedirs(self.P["work"], exist_ok=True); json.dump(cache, open(cp, "w"), indent=1)
                print("GANIM grip_r solved: %s  %s" % (sol, rep))
            g = cache["grip"]
        return fl.brief_T(g)


# --------------------------------------------------------------------------- per-clip items, free right hand, contact points
# Opt-in (hands sets): a clip may name its own `item` (null = empty hands) and `grip_r`; a key whose hand_r names a
# `space` frees the right hand from the item (the item then follows its own keys; who holds it per frame goes to
# meta fp.item_attach); `contact` on a hand spec places a point of the hand on a target. Sets without these fields
# evaluate exactly as before.

def clip_ctx(ctx, c):
    """ctx for one clip: its own item / grip_r when the clip names them, else ctx itself. grip_r extras for pickups:
    "socket:<s>" or {"socket": <s>, "offset": [x, y, z]} = the item's hand socket (authored in the attach frame),
    moved by `offset` in the attach frame; the item's own ig_grip_r still wins (Ctx.solve_grip)."""
    if c.get("regrip_r"):   # opt-in: the hand holds the item differently in this clip (e.g. spray can: index on the actuator)
        base = clip_ctx(ctx, {k: v for k, v in c.items() if k != "regrip_r"})
        key = json.dumps(["regrip", c.get("item"), c.get("grip_r"), c["regrip_r"]], sort_keys=True); cache = ctx.__dict__.setdefault("_clip_ctx", {})
        if key in cache: return cache[key]
        sub = copy.copy(base); sub.grip_r_item = base.grip_r; g2 = c["regrip_r"]
        sub.grip_r = fl.brief_T(with_contact(sub, g2, "r") if isinstance(g2, dict) and "contact" in g2 else g2)
        sub.grip_r_source = base.grip_r_source + " + clip regrip_r"
        cache[key] = sub; return sub
    if "item" not in c and "grip_r" not in c: return ctx
    item = c.get("item", ctx.a.get("item")); g = c.get("grip_r", ctx.a.get("grip_r") if item == ctx.a.get("item") else None)
    key = json.dumps([item, g], sort_keys=True); cache = ctx.__dict__.setdefault("_clip_ctx", {})
    if key in cache: return cache[key]
    sub = copy.copy(ctx); sub.a = dict(ctx.a, item=item, grip_r=g)
    sub.item = fl.Item(item_path(ctx.P, item), item) if item else None
    sock = g[7:] if isinstance(g, str) and g.startswith("socket:") else g.get("socket") if isinstance(g, dict) else None
    if sock and sub.item is not None and not sub.item.extras.get("ig_grip_r"):
        s = sub.item.socket(sock)
        if s is None: raise SystemExit("%s: grip_r socket %s missing on %s" % (c["name"], sock, item))
        off = g.get("offset", [0, 0, 0]) if isinstance(g, dict) else [0, 0, 0]
        sub.grip_r = fl.tmul((al.qnorm(np.array(s[0], float)), np.array(s[1], float)), (fl.I4.copy(), np.array(off, float)))
        sub.grip_r_source = "item %s (+offset %s)" % (sock, off)
    else: sub.grip_r = sub.solve_grip(None if sock else with_contact(sub, g, "r") if isinstance(g, dict) and "contact" in g else g)   # contact: place a hand point on an item point
    cache[key] = sub; return sub


_CONTACT = {}


def contact_point(ctx, side, fingers, point):
    """A point of the hand in its attach frame (+Z fingers, +Y thumb) for a finger pose: pinch (thumb/index pad
    midpoint), index_tip, thumb_tip, palm (palm surface)."""
    k = (side, json.dumps(fingers, sort_keys=True), point)
    if k in _CONTACT: return _CONTACT[k]
    skel = ctx.skel; qh, ph = fl.hand_from_attach(skel, side, fl.T())
    caps = {c_[0]: c_ for c_ in caps_from_fk(skel, side, hand_fk(skel, side, qh, ph, ctx.hands[side].locals(fl.pose_angles(ctx.lib, fingers))))}
    mid = lambda n: (caps[n][1] + caps[n][2]) / 2
    if point == "pinch": m = (mid("thumb_03_" + side) + mid("index_03_" + side)) / 2
    elif point == "index_tip": m = caps["index_03_" + side][2]
    elif point == "thumb_tip": m = caps["thumb_03_" + side][2]
    elif point == "palm": m = mid("palm_middle") + np.array([1.0 if side == "r" else -1.0, 0, 0]) * caps["palm_middle"][3]
    else: raise SystemExit("unknown contact point %s" % point)
    _CONTACT[k] = np.array(m, float); return _CONTACT[k]


def with_contact(ctx, spec, side):
    """{"contact": {"point", "at": [r, u, f], "pose"?}} -> "pos" such that that hand point lands on `at` (in the spec's space)."""
    ct = spec.get("contact")
    if not ct: return spec
    q = fl.brief_T(dict(spec, pos=[0, 0, 0]))[0]
    p = fl.brief_vec(ct["at"]) - al.qrot(q, contact_point(ctx, side, ct.get("pose", spec.get("fingers", "relaxed")), ct.get("point", "pinch")))
    out = {k: v for k, v in spec.items() if k != "contact"}; out["pos"] = [round(float(-p[0]), 5), round(float(p[1]), 5), round(float(p[2]), 5)]
    return out


def hand_r_spec(ctx, h):
    """hand_r entry: named grip (shared with hand_l), contact -> pos. A `space` frees the hand from the item."""
    h = dict(h or {})
    if h.get("grip"):
        g = ctx.grips.get(h["grip"])
        if g is None: raise SystemExit("unknown grip %s" % h["grip"])
        h = deep_merge(g, {k: v for k, v in h.items() if k != "grip"})
    return with_contact(ctx, h, "r")


# --------------------------------------------------------------------------- per-key resolution

def cam_T(ctx, t):
    """camera-space transform (model axes, origin E0) -> model space."""
    return (t[0], ctx.E0 + t[1])


def hand_l_spec(ctx, hl):
    """Normalise a hand_l entry: {"grip": name} expands to the named grip; returns dict with space, T (local)."""
    if hl is None: return {"space": "rest"}
    if hl.get("grip"):
        g = ctx.grips.get(hl["grip"])
        if g is None: raise SystemExit("unknown grip %s" % hl["grip"])
        hl = deep_merge(g, {k: v for k, v in hl.items() if k != "grip"})
    if hl.get("space", "camera") == "socket" or str(hl.get("space", "")).startswith("socket:"):
        sname = hl["space"].split(":", 1)[1] if ":" in hl["space"] else hl.get("socket")
        if ctx.item is None or ctx.item.socket(sname) is None:
            fb = hl.get("fallback")
            if not fb: raise SystemExit("socket %s missing on %s and no fallback" % (sname, ctx.a.get("item")))
            hl = deep_merge({k: v for k, v in hl.items() if k not in ("space", "fallback")}, fb); hl["_fallback_from"] = sname
    return hl


def resolve_space(ctx, space, item_w, pv, carry_T=None):
    """World frame of a space at this frame."""
    if space == "item": return item_w
    if space == "camera": return (fl.I4.copy(), ctx.E0.copy())
    if space.startswith("part:"):
        p = space.split(":", 1)[1]
        if carry_T is not None and p in carry_T: return fl.tmul(item_w, carry_T[p])
        return fl.tmul(item_w, ctx.item.part_T(p, pv.get(p, 0.0)))
    if space.startswith("socket:"):
        s = ctx.item.socket(space.split(":", 1)[1]); return fl.tmul(item_w, s)
    raise SystemExit("unknown space %s" % space)


def rest_attach(ctx, side):
    return fl.attach_from_hand(ctx.skel, side, fl.I4.copy(), ctx.skel.P[ctx.skel.i("hand_" + side)])


# --------------------------------------------------------------------------- grip fit

def hand_fk(skel, side, qh, ph, locals_):
    """Canonical world (q, p) of the hand and its finger joints for one hand pose."""
    out = {"hand_" + side: (qh, ph)}
    for f in fl.FINGERS:
        for k in (1, 2, 3):
            b = "%s_%02d_%s" % (f, k, side)
            if not skel.has(b): continue
            par = skel.names[skel.parent[skel.i(b)]]
            qp, pp = out[par]
            q = al.qmul(qp, locals_.get(b, fl.I4)); p = pp + al.qrot(qp, skel.P[skel.i(b)] - skel.P[skel.i(par)])
            out[b] = (q, p)
    return out


def caps_from_fk(skel, side, fk):
    B = len(skel); Wc = np.tile(fl.I4, (B, 1)); P = skel.P.copy()
    for b, (q, p) in fk.items(): Wc[skel.i(b)] = q; P[skel.i(b)] = p
    return fl.hand_capsules(skel, Wc, P, side)


def cap_clearance(ctx, caps, item_w, pv, others=()):
    """min over capsules of (distance to obstacles - radius); obstacles = item SDF (+ other hand's capsules)."""
    inv = fl.tinv(item_w); res = {}
    for name, a, b, r in caps:
        pts = fl.capsule_points(a, b, 5).reshape(-1, 3)
        d = ctx.item.sdf(fl.tpoint(inv, pts), pv) - r if ctx.item is not None else np.full(len(pts), 1.0)
        dmin = float(d.min())
        for on, oa, ob, orad in others:
            import validate_anim as va
            dd = float(va.seg_seg_dist(a, b, oa, ob)) - r - orad
            dmin = min(dmin, dd)
        res[name] = dmin
    return res


def fit_tip(ctx, side, f, cur, target_item, hand_w, item_w, pv, others=(), tol=0.0012):
    """Place one finger's distal pad on an item-space point (e.g. the index on the trigger face): Nelder-Mead
    over (spread, flex x3) minimising pad distance + penetration of the finger's three segments."""
    from scipy.optimize import minimize
    hm = ctx.hands[side]; skel = ctx.skel; qh, ph = hand_w
    tw = fl.tpoint(item_w, target_item); bones = ["%s_%02d_%s" % (f, k, side) for k in (1, 2, 3)]
    def build(x):
        c = copy.deepcopy(cur); c[f]["spread"] = float(x[0]); c[f]["flex"] = [float(v) for v in x[1:4]]; return c
    def cost(x, rep=False):
        c = build(x); fk = hand_fk(skel, side, qh, ph, hm.locals(c))
        caps = [cp for cp in caps_from_fk(skel, side, fk) if cp[0] in bones]
        cl = cap_clearance(ctx, caps, item_w, pv, others)
        pad = (caps[2][1] + caps[2][2]) / 2
        d = float(np.linalg.norm(pad - tw)); pen = sum(max(0.0, -v - tol) for v in cl.values())
        val = (d * 1000) ** 2 + (pen * 1000) ** 2 * 6 + 0.01 * float(np.sum(np.square(x[1:4]))) + 0.05 * x[0] ** 2
        return (val, d, cl) if rep else val
    x0 = np.array([cur[f].get("spread", 0.0)] + list(cur[f]["flex"]), float)
    best = min((minimize(cost, x0 + dx, method="Nelder-Mead", options={"xatol": 0.2, "fatol": 0.05, "maxiter": 600}) for dx in (np.zeros(4), np.array([-8, 5, 10, 5]), np.array([8, -5, 5, 10]))), key=lambda r: r.fun)
    v, d, cl = cost(best.x, True)
    out = build(best.x)
    return out, "tip %.1f mm from target, spread %.0f flex %s, clearance %s mm" % (d * 1000, best.x[0], [round(float(a), 0) for a in best.x[1:4]], [round(c * 1000, 1) for c in cl.values()])


def fit_fingers(ctx, side, ang, hand_w, item_w, pv, others=(), tol=0.0012, n=25, open_deg=-8.0, tips=None):
    """Grip fit ("wrap"): proximal to distal, each finger joint takes the MOST curled angle in
    [open_deg, pose angle] at which its own segment clears the obstacles (item SDF, plus the other hand's
    capsules for the support hand) by -tol; if no angle clears, the one with the most clearance. Each segment
    ends up laid against the surface or at the pose angle (the library angle is the upper bound)."""
    hm = ctx.hands[side]; skel = ctx.skel
    cur = copy.deepcopy(ang); qh, ph = hand_w; info = {}
    for f in fl.FINGERS:
        bones = ["%s_%02d_%s" % (f, k, side) for k in (1, 2, 3)]
        res = []
        for k in range(3):
            hi = float(ang[f]["flex"][k]); lo = min(open_deg, hi)
            best = None
            for a_ in np.linspace(hi, lo, n):
                c = copy.deepcopy(cur); c[f]["flex"][k] = float(a_)
                fk = hand_fk(skel, side, qh, ph, hm.locals(c))
                cap = [x for x in caps_from_fk(skel, side, fk) if x[0] == bones[k]]
                cl = cap_clearance(ctx, cap, item_w, pv, others)[bones[k]]
                if cl >= -tol: best = (float(a_), cl, True); break
                if best is None or cl > best[1]: best = (float(a_), cl, False)
            cur[f]["flex"][k] = best[0]
            res.append("%.0f%s" % (best[0], "" if best[2] else "!%.1fmm" % (best[1] * 1000)))
        info[f] = " ".join(res)
    for f, tgt in (tips or {}).items():
        cur, info[f] = fit_tip(ctx, side, f, cur, fl.brief_vec(tgt), hand_w, item_w, pv, others)
    return cur, info


# --------------------------------------------------------------------------- clip evaluation

def eval_clip(ctx, c):
    """-> (Clip, meta fp block, reference dict, report)."""
    ctx = clip_ctx(ctx, c)
    skel, fps, E0 = ctx.skel, ctx.fps, ctx.E0
    poses = ctx.a.get("poses", {})
    keys = sorted(c["keys"], key=lambda k: k["f"])
    F = int(c.get("frames") or keys[-1]["f"] + 1)
    loop = c.get("loop", c["name"].endswith("_loop"))
    # resolved keys
    R = []
    for k in keys:
        p = resolve_pose(poses, k["pose"]) if k.get("pose") else {}
        p = deep_merge(p, {x: k[x] for x in ("item", "hand_r", "hand_l") if x in k})
        it = p.get("item", {"pos": [0.12, -0.15, 0.4], "rot": [0, 0, 0]})
        tT = fl.brief_T(it, it.get("dpos"), it.get("drot"))
        hl = with_contact(ctx, hand_l_spec(ctx, p.get("hand_l")), "l")
        hlT = fl.brief_T(hl, hl.get("dpos"), hl.get("drot")) if hl.get("space", "camera") != "rest" else None
        hr = hand_r_spec(ctx, p.get("hand_r", {}))
        free_r = "space" in hr   # the right hand goes to its own target; the item follows its keys
        if ctx.item is None and not free_r: raise SystemExit("%s f%d: a clip without an item needs hand_r.space" % (c["name"], k["f"]))
        hrT = fl.brief_T(hr, hr.get("dpos"), hr.get("drot")) if free_r and hr.get("space") != "rest" else None
        R.append({"f": k["f"], "ease": k.get("ease", "inout"), "item": tT, "hand_r": hr, "hand_l": hl, "hlT": hlT, "hrT": hrT, "free_r": free_r,
                  "pole_r": fl.brief_vec(hr.get("pole", ctx.defaults.get("r", [0.55, -1.0, -0.3]))),
                  "pole_l": fl.brief_vec(hl.get("pole", ctx.defaults.get("l", [-0.55, -1.0, -0.3])))})
    # part curves, visibility, carry
    pnames = list(ctx.item.parts_def) if ctx.item else []
    pd = dict(ctx.part_defaults, **c.get("state_in", {}))
    pv = {p: fl.curve(c.get("parts", {}).get(p), F, pd.get(p, 0.0)) for p in pnames}
    for p in c.get("parts", {}):
        if p not in pnames: raise SystemExit("%s: part %s not in %s ig_parts (%s)" % (c["name"], p, ctx.a.get("item"), pnames))
    vis = {p: fl.curve([[f, v, "hold"] for f, v in c.get("visible", {}).get(p, [])], F, 1.0) for p in pnames}
    carry = c.get("carry", [])
    # secondary
    sway = fl.breath_sway(F, fps, loop, c.get("breath", ctx.a.get("breath") if c.get("breath", True) is not False else None) if c.get("breath", True) is not False else None)
    rec = fl.recoil_curve(F, fps, c.get("recoil"))
    settle = fl.spring_settle(F, fps, c.get("settle"))
    ql = np.tile(fl.I4, (F, len(skel), 1))
    ref = {"item_w": [], "att_l": [], "att_l_target": [], "hold_l": [], "space_l": [], "reach": [], "carry_T": [], "E0": E0.tolist()}
    free_any = any(K["free_r"] for K in R)
    # clip "hang": true | {damping, max_deg}: a carried item hanging from the fingers (item root extra ig_hang {pivot, axis, com},
    # glTF item space) swings as a damped pendulum about the bar while the right hand carries it: the hand follows the KEYED
    # item, the shown item = keyed item o rotation about pivot/axis (meta fp.hang.angle_deg; ref hang_T per frame)
    hang = None
    if c.get("hang") and ctx.item is not None:
        ih = ctx.item.extras.get("ig_hang"); ih = json.loads(ih) if isinstance(ih, str) else ih
        if not ih: raise SystemExit("%s: clip hang needs the item's ig_hang root extra" % c["name"])
        hc = c["hang"] if isinstance(c["hang"], dict) else {}
        _ax = np.array(ih["axis"], float)
        hang = {"pivot": np.array(ih["pivot"], float), "axis": _ax / np.linalg.norm(_ax), "com": np.array(ih["com"], float), "src": ih,
                "zeta": float(hc.get("damping", 0.12)), "st": [0.0, 0.0], "hist": [], "ang": [],
                "lim": [float(x) for x in hc.get("range_deg", [-hc.get("max_deg", 50.0), hc.get("max_deg", 50.0)])]}   # stops: the item meets the hand
        ref["hang_T"] = []
    if free_any: ref.update(att_r_target=[], attached_r=[], holder=[], space_r=[]); att_l_log = []
    fit_report = {}

    def seg(f):
        if f <= R[0]["f"]: return R[0], R[0], 0.0
        if f >= R[-1]["f"]: return R[-1], R[-1], 0.0
        for x, y in zip(R, R[1:]):
            if x["f"] <= f <= y["f"]:
                u = (f - x["f"]) / max(1e-9, y["f"] - x["f"]); return x, y, fl.ease(u, y["ease"])

    # per frame: item + hand targets + IK + fingers. Key poses are fitted first, each at its own frame.
    def frame(f, force=None, record=True):
        a_, b_, u = force or seg(f)
        it_c = fl.tlerp(a_["item"], b_["item"], u)
        # secondary on the item: sway (camera-space rotation about the eye), recoil (item-local kick + camera back/up), settle
        s = sway[f]
        attached = (not a_["free_r"] and not b_["free_r"]) or (u == 0 and not a_["free_r"]) or (u >= 1 and not b_["free_r"])
        if attached or c.get("item_sway"):   # an item nobody carries stays put in the world (no breathing)
            it_c = (al.qnorm(al.qmul(s[0], it_c[0])), s[1] + al.qrot(s[0], it_c[1]))
        kick, back, rise, roll, yaw = rec[f]
        if any(rec[f]):
            it_c = (al.qnorm(al.qmul(al.qmul(fl.brief_rot([yaw, 0, roll]), it_c[0]), al.qaxis([1, 0, 0], -kick * fl.DEG))), it_c[1] + np.array([0, rise, -back]))
        if settle[f].any():
            it_c = (al.qnorm(al.qmul(it_c[0], al.qaxis([1, 0, 0], settle[f][1] * fl.DEG))), it_c[1] + np.array([0, -settle[f][0], 0]))
        item_w = cam_T(ctx, it_c)
        item_v = item_w   # the item as shown (differs from the keyed item_w only for a hanging item)
        if hang is not None and record:
            th, om = hang["st"]
            if attached:
                q_, t_ = item_w; dt = 1.0 / fps
                aw = al.qrot(q_, hang["axis"]); d0 = al.qrot(q_, hang["com"] - hang["pivot"]); L = float(np.linalg.norm(d0)); d0 = d0 / L
                H = hang["hist"]; H.append((t_ + al.qrot(q_, hang["pivot"]), q_))
                acc, alpha = np.zeros(3), 0.0
                if len(H) >= 3:
                    acc = (H[-1][0] - 2 * H[-2][0] + H[-3][0]) / dt ** 2
                    def _w(qa, qb):
                        d = al.qmul(qa, al.qconj(qb)); d = d if d[3] >= 0 else -d   # xyzw
                        return 2 * d[:3] / dt
                    alpha = float(np.dot((_w(H[-1][1], H[-2][1]) - _w(H[-2][1], H[-3][1])) / dt, aw))
                g_eff = np.array([0.0, -9.81, 0.0]) - acc; wn = np.sqrt(9.81 / L)
                for _ in range(8):
                    d = al.qrot(al.qaxis(aw, th), d0)
                    om += (float(np.dot(np.cross(d, g_eff), aw)) / L - alpha - 2 * hang["zeta"] * wn * om) * dt / 8; th += om * dt / 8
                lo, hi = np.radians(hang["lim"])
                if not lo <= th <= hi: th = float(np.clip(th, lo, hi)); om = 0.0
            else:
                hang["hist"].clear(); th, om = 0.0, 0.0
            hang["st"] = [th, om]
            qh_ = al.qaxis(hang["axis"], th); Hd = (qh_, hang["pivot"] - al.qrot(qh_, hang["pivot"]))
            item_v = fl.tmul(item_w, Hd)
            ref["hang_T"].append(fl.tjson(Hd)); hang["ang"].append(round(float(np.degrees(th)), 3))
        pvf = {p: float(pv[p][f]) for p in pnames}
        qf = ql[f]
        # support hand (targets first: a free right hand may be placed relative to it)
        hla, hlb = a_["hand_l"], b_["hand_l"]
        sa, sb = hla.get("space", "camera"), hlb.get("space", "camera")
        def world(hl, hT):
            if hl.get("space", "camera") == "rest": return rest_attach(ctx, "l")
            w = fl.tmul(resolve_space(ctx, hl.get("space", "camera"), item_w, pvf), hT)
            if hl.get("space", "camera") == "camera": w = (al.qnorm(al.qmul(s[0], w[0])), ctx.E0 + s[1] + al.qrot(s[0], w[1] - ctx.E0))
            return w
        if sa == "rest" and sb == "rest":
            att_l = rest_attach(ctx, "l"); hold = False
        else:
            if sa == sb and sa != "camera":
                att_l = fl.tmul(resolve_space(ctx, sa, item_w, pvf), fl.tlerp(a_["hlT"], b_["hlT"], u))
            else:
                att_l = fl.tlerp(world(hla, a_["hlT"]), world(hlb, b_["hlT"]), u)
            hold = (sa == sb and sa not in ("camera", "rest")) or (u == 0 and sa not in ("camera", "rest")) or (u >= 1 and sb not in ("camera", "rest"))
        # right hand: carries the item at grip_r, or (free_r keys) goes to its own target
        att_item = fl.tmul(item_w, ctx.grip_r)
        ra, rb = a_["hand_r"].get("space"), b_["hand_r"].get("space")
        def base_r(sp): return att_l if sp == "hand_l" else resolve_space(ctx, sp, item_w, pvf)
        def world_r(K):
            if not K["free_r"]: return att_item
            sp = K["hand_r"].get("space", "camera")
            if sp == "rest": return rest_attach(ctx, "r")
            w = fl.tmul(base_r(sp), K["hrT"])
            return (al.qnorm(al.qmul(s[0], w[0])), ctx.E0 + s[1] + al.qrot(s[0], w[1] - ctx.E0)) if sp == "camera" else w
        if not a_["free_r"] and not b_["free_r"]: att_r = att_item
        elif a_["free_r"] and b_["free_r"] and ra == rb and ra not in ("camera", "rest"): att_r = fl.tmul(base_r(ra), fl.tlerp(a_["hrT"], b_["hrT"], u))
        else: att_r = fl.tlerp(world_r(a_), world_r(b_), u)
        qh_r, ph_r = fl.hand_from_attach(skel, "r", att_r)
        pole_r = a_["pole_r"] + (b_["pole_r"] - a_["pole_r"]) * u
        if a_["free_r"] and b_["free_r"] and ra == "rest" and rb == "rest":
            err_r = 0.0
            for bn in fl.arm_bones("r"): qf[skel.i(bn)] = fl.I4
        else: err_r = fl.solve_arm(skel, qf, "r", qh_r, ph_r, pole_r)
        pole_l = a_["pole_l"] + (b_["pole_l"] - a_["pole_l"]) * u
        qh_l, ph_l = fl.hand_from_attach(skel, "l", att_l)
        if sa == "rest" and sb == "rest":
            err_l = 0.0
            for bn in fl.arm_bones("l"): qf[skel.i(bn)] = fl.I4
        else: err_l = fl.solve_arm(skel, qf, "l", qh_l, ph_l, pole_l)
        # carry: part node = attach_l o grip^-1 (item space for the engine/reference)
        cT = {}
        for cw in carry:
            if cw["frames"][0] <= f <= cw["frames"][1]:
                G = fl.brief_T(ctx.grips[cw["grip"]])
                cT[cw["part"]] = fl.tmul(fl.tinv(item_w), fl.tmul(att_l, fl.tinv(G)))
        # fingers: key poses (fitted once per key at the key's own frame), blended
        Wc_f, P_f = al.fk(skel, al.Clip(fps, qf[None], np.zeros((1, 3)), (skel.P[skel.i("hips")] - skel.P[skel.i("root")])[None]))
        hw = {sd: (Wc_f[0, skel.i("hand_" + sd)], P_f[0, skel.i("hand_" + sd)]) for sd in "rl"}
        pvc = dict(pvf)
        angs = {}
        for side in "rl":
            others = () if side == "r" else caps_from_fk(skel, "r", hand_fk(skel, "r", hw["r"][0], hw["r"][1], ctx.hands["r"].locals(angs["r"])))
            def fing(key):
                spec_ = key["hand_r"] if side == "r" else key["hand_l"]
                ang = fl.pose_angles(ctx.lib, spec_.get("fingers", "relaxed"))
                if not spec_.get("fit") or ctx.item is None: return ang
                ck = (side, c["name"], key["f"])
                if ck not in ctx.fits:
                    if force is None: raise SystemExit("internal: key %d not fitted" % key["f"])
                    pfit = {p: ctx.part_defaults.get(p, 0.0) for p in pvc}   # grips fit against the item at rest (parts in their default state)
                    pvv = pfit if spec_.get("fit_rest", not str(spec_.get("space", "")).startswith("part:")) else pvc
                    # memo across keys/clips: the same finger spec on the same hand-in-item placement (and, for the
                    # support hand, the same strong hand) gives the same fit -- skin fits cost minutes each
                    mk = None
                    if ctx.fit_cache is not None:
                        rel = fl.tmul(fl.tinv(item_w), hw[side]); relr = fl.tmul(fl.tinv(item_w), hw["r"])
                        mk = json.dumps([side, spec_.get("fingers"), spec_.get("tips"), spec_.get("fit_tol"), spec_.get("open_deg"),
                                         [round(float(x), 3) for x in list(rel[0]) + list(rel[1])],
                                         [round(float(x), 3) for x in list(relr[0]) + list(relr[1])] if side == "l" else None,
                                         json.loads(json.dumps(angs.get("r"), default=float)) if side == "l" else None,
                                         {k_: round(float(v_), 3) for k_, v_ in sorted(pvv.items())}], sort_keys=True, default=float)
                        if mk in ctx.fit_cache:
                            ctx.fits[ck] = ctx.fit_cache[mk]; return ctx.fits[ck]
                    if ctx.skins is not None:
                        obst = None
                        if side == "l":
                            fkr = hand_fk(skel, "r", hw["r"][0], hw["r"][1], ctx.hands["r"].locals(angs["r"]))
                            obst = fl.skin_sdf(ctx.skins["r"].pose(fkr), ctx.skins["r"].tris)
                        fa, info = fl.fit_fingers_skin(skel, ctx.item, side, ang, hw[side], ctx.skins[side], item_w=item_w, pv=pvv, obstacle=obst,
                                                       tol=spec_.get("fit_tol", 0.0010))
                        for f_, tgt in (spec_.get("tips") or {}).items():
                            fa, info[f_] = fit_tip(ctx, side, f_, fa, fl.brief_vec(tgt), hw[side], item_w, pvv, others)
                    else:
                        fa, info = fit_fingers(ctx, side, ang, hw[side], item_w, pvv, others, tips=spec_.get("tips"), open_deg=float(spec_.get("open_deg", c.get("open_deg", -8.0))))
                    ctx.fits[ck] = fa; fit_report["%s f%d %s" % (side, key["f"], spec_.get("fingers"))] = info
                    if ctx.fit_cache is not None: ctx.fit_cache[mk] = fa
                return ctx.fits[ck]
            fa = fing(a_); fb = fing(b_) if b_ is not a_ else fa
            angs[side] = fl.lerp_angles(fa, fb, u)
            sa_, sb_ = (a_["hand_r"], b_["hand_r"]) if side == "r" else (a_["hand_l"], b_["hand_l"])
            if c.get("fit_every") and force is None and ctx.item is not None and ctx.skins is None and 0 < u < 1 and (sa_.get("fit") or sb_.get("fit")):
                # opt-in: re-wrap between keys too (hands moving on/off an item); the blended angles are the upper bound
                raw = fl.lerp_angles(fl.pose_angles(ctx.lib, sa_.get("fingers", "relaxed")), fl.pose_angles(ctx.lib, sb_.get("fingers", "relaxed")), u)
                ub = {f_: {**raw[f_], "flex": [max(x, y) for x, y in zip(raw[f_]["flex"], angs[side][f_]["flex"])]} for f_ in raw}
                angs[side], _ = fit_fingers(ctx, side, ub, hw[side], item_w, pvc, others, open_deg=float(c.get("open_deg", -8.0)), n=13)
            for bn, q in ctx.hands[side].locals(angs[side]).items(): qf[skel.i(bn)] = q
        if record:
            ref["item_w"].append(fl.tjson(item_v)); ref["att_l_target"].append(fl.tjson(att_l)); ref["hold_l"].append(bool(hold))
            ref["space_l"].append(sa if u < 0.5 else sb); ref["reach"].append([round(err_r, 5), round(err_l, 5)])
            ref["carry_T"].append({k: fl.tjson(v) for k, v in cT.items()})
            if free_any:
                holder = "r" if attached else ("l" if hold and ctx.item is not None else ("world" if ctx.item is not None else "none"))
                if c.get("release") is not None and ctx.item is not None and f >= int(c["release"]): holder = "gone"   # thrown: the item left the view model
                ref["att_r_target"].append(fl.tjson(att_r)); ref["attached_r"].append(bool(attached)); ref["holder"].append(holder)
                ref["space_r"].append("item_grip" if not (a_ if u < 0.5 else b_)["free_r"] else ((ra if u < 0.5 else rb) or "camera")); att_l_log.append(att_l)

    for K in R:
        frame(min(K["f"], F - 1), force=(K, K, 0.0), record=False)
    for f in range(F):
        frame(f)
    ql = al.qcontinuous(ql)
    clip = al.Clip(fps, ql, np.zeros((F, 3)), np.tile(skel.P[skel.i("hips")] - skel.P[skel.i("root")], (F, 1)), c["name"])
    # events
    evs = []
    for e in sorted(c.get("events", []), key=lambda e: e["f"]):
        d = {k: v for k, v in e.items() if k != "f"}; d["frame"] = int(e["f"]); d["t"] = round(e["f"] / fps, 4); evs.append(d)
    cw_meta = []
    for cw in carry:
        g = ctx.grips[cw["grip"]]
        cw_meta.append({"part": cw["part"], "attach": "attach_hand_l", "frames": list(cw["frames"]), "offset": fl.tjson(fl.tinv(fl.brief_T(g))),
                        "note": "part node parented to attach_hand_l with this local TRS (glTF axes) inside the window; item-local part curve outside"})
    fpm = {"item": ctx.a.get("item"), "action": c.get("action"), "grip_r": fl.tjson(fl.tinv(getattr(ctx, "grip_r_item", ctx.grip_r))), "grip_r_source": ctx.grip_r_source, "eye_m": [round(float(x), 5) for x in E0],
           "fov_deg": ctx.fpc.get("viewmodel_fov_deg"), "near_m": ctx.fpc.get("near_m"), "frame_policy": c.get("frame_policy", "hold"),
           "events": evs,
           "parts": {p: {"values": [round(float(v), 4) for v in pv[p]], "unit": "normalized (ig_parts travel_m / angle_deg at 1)", **({"wrap": True} if p in ctx.a.get("part_wrap", []) else {})} for p in pnames if p in c.get("parts", {})},
           "visible": {p: [int(v > 0.5) for v in vis[p]] for p in pnames if p in c.get("visible", {})},
           "carry": cw_meta,
           "state_in": {p: round(float(pv[p][0]), 4) for p in pnames}, "state_out": {p: round(float(pv[p][-1]), 4) for p in pnames}}
    if hang is not None:
        fpm["hang"] = {"pivot": hang["src"]["pivot"], "axis": hang["src"]["axis"], "com": hang["src"]["com"], "angle_deg": hang["ang"],
                       "note": "while attach_hand_r carries it the item also turns by angle_deg about axis through pivot (item-local, glTF; "
                               "on top of grip_r): a damped pendulum (the item's ig_hang); the engine may simulate it instead"}
    for k in ("refire_frame", "cancel_frame", "hit_window", "interaction", "show", "release"):
        if k in c: fpm[k] = c[k]
    if hasattr(ctx, "grip_r_item"):   # regrip_r: the item hangs at this TRS under attach_hand_r in this clip (meta grip_r stays the item's ig_grip_r)
        fpm["regrip_r"] = fl.tjson(fl.tinv(ctx.grip_r))
        if not free_any: fpm["item_attach"] = [{"holder": "r", "bone": "attach_hand_r", "frames": [0, F - 1], "offset": fpm["regrip_r"], "note": "regrip: item under attach_hand_r at regrip_r (not ig_grip_r) for this clip"}]
    if free_any and ctx.item is not None:   # who holds the item, as windows: the engine re-parents the item at each window start
        wins = []
        for f, h in enumerate(ref["holder"]):
            if wins and wins[-1]["holder"] == h: wins[-1]["frames"][1] = f; continue
            iw = (np.array(ref["item_w"][f]["r"]), np.array(ref["item_w"][f]["t"]))
            if h == "r": bone, off, note = "attach_hand_r", fl.tjson(fl.tinv(ctx.grip_r)), "item under attach_hand_r at grip_r"
            elif h == "l": bone, off, note = "attach_hand_l", fl.tjson(fl.tmul(fl.tinv(att_l_log[f]), iw)), "item under attach_hand_l at this local TRS"
            elif h == "gone": bone, off, note = None, fl.tjson(fl.tmul((fl.I4.copy(), -E0), iw)), "released (clip 'release', the throw_release frame): hide the view-model item; the engine spawns the projectile at this camera-space TRS with the hand's velocity"
            else: bone, off, note = None, fl.tjson(fl.tmul((fl.I4.copy(), -E0), iw)), "nobody holds it: it stays in the world where it is (offset = camera-space TRS at the window start, eye at the origin)"
            wins.append({"holder": h, "bone": bone, "frames": [f, f], "offset": off, "note": note})
        fpm["item_attach"] = wins
    ref["parts"] = {p: pv[p].round(5).tolist() for p in pnames}; ref["visible"] = {p: vis[p].tolist() for p in pnames}
    # clip "props": secondary items shown for a window (a stripper clip in the right hand, then seated on a socket of the main
    # item): [{item, attach: [{holder: "r", frames} | {holder: "item", socket, own_socket, frames}], parts: {p: curve}}].
    # meta fp.props carries the windows + part curves for the engine; ref props the per-frame transforms for the review.
    if c.get("props"):
        Wc_, P_ = al.fk(skel, clip); qa_, pa_ = fl.attach_world(skel, Wc_, P_, "r"); fpm["props"] = []; ref["props"] = {}
        for pr in c["props"]:
            pit = fl.Item(item_path(ctx.P, pr["item"]), pr["item"])
            pg = pit.extras.get("ig_grip_r"); pg = json.loads(pg) if isinstance(pg, str) else pg
            G = (np.array(pg["r"], float), np.array(pg["t"], float)) if pg else (fl.I4.copy(), np.zeros(3))
            ppv = {p: fl.curve(pr.get("parts", {}).get(p), F, pr.get("part_defaults", {}).get(p, 0.0)) for p in pit.parts_def}
            track, wins = [None] * F, []
            for w in pr["attach"]:
                f0, f1 = int(w["frames"][0]), min(int(w["frames"][1]), F - 1)
                if w["holder"] == "r":
                    off = G; bone, note = "attach_hand_r", "under attach_hand_r at the prop's ig_grip_r"
                    for f in range(f0, f1 + 1): track[f] = fl.tmul((qa_[f], pa_[f]), G)
                else:
                    off = fl.tmul(ctx.item.socket(w["socket"]), fl.tinv(pit.socket(w["own_socket"])))
                    bone, note = None, "seated: the prop's %s on the main item's %s (identity); offset = the prop root in main-item space" % (w["own_socket"], w["socket"])
                    for f in range(f0, f1 + 1):
                        iw = (np.array(ref["item_w"][f]["r"]), np.array(ref["item_w"][f]["t"])); track[f] = fl.tmul(iw, off)
                wins.append({"holder": w["holder"], "bone": bone, **({"socket": w["socket"]} if bone is None else {}), "frames": [f0, f1], "offset": fl.tjson(off), "note": note})
            fpm["props"].append({"item": pr["item"], "item_attach": wins, "parts": {p: {"values": [round(float(v), 4) for v in ppv[p]]} for p in pr.get("parts", {})},
                                 "note": "shown only inside its item_attach windows; hidden (or handed to physics, see events) outside"})
            ref["props"][pr["item"]] = {"T": [fl.tjson(t) if t is not None else None for t in track], "parts": {p: ppv[p].round(5).tolist() for p in ppv}}
    ref["keys"] = [k["f"] for k in keys]; ref["policy"] = fpm["frame_policy"]; ref["grip_r"] = fpm["grip_r"]; ref["item"] = fpm["item"]
    return clip, fpm, ref, {"frames": F, "fit": fit_report, "max_reach_err_cm": [round(max(r[0] for r in ref["reach"]) * 100, 2), round(max(r[1] for r in ref["reach"]) * 100, 2)]}


# --------------------------------------------------------------------------- set builds

def build_hand_poses(a, P, ctx):
    skel, fps = ctx.skel, ctx.fps
    names = a.get("poses_list") or sorted(k for k, v in ctx.lib.items() if not v.get("_alias"))
    out = []; metas = []
    for pn in names:
        for side in "rl":
            ang = fl.pose_angles(ctx.lib, pn); ql = np.tile(fl.I4, (2, len(skel), 1))
            for bn, q in ctx.hands[side].locals(ang).items(): ql[:, skel.i(bn)] = q
            nm = "anim_hand_%s_%s" % (pn, side)
            clip = al.Clip(fps, ql, np.zeros((2, 3)), np.tile(skel.P[skel.i("hips")] - skel.P[skel.i("root")], (2, 1)), nm)
            out.append(clip)
            # corrective shape-key weights the pose wants on the arms mesh (library "morphs": {"palm_fold": 1} -> palm_fold_<side>);
            # anim glbs carry bone tracks only, so the engine applies these from the meta (engine ask: morph weights per pose layer)
            mo = {"%s_%s" % (k, side): float(v) for k, v in (ctx.lib[pn].get("morphs") or {}).items()}
            metas.append({"clip": nm, "fps": fps, "loop": False, "root_motion": False, "additive": False, "base": None,
                          "tags": ["hand_pose", "pose", side], "source": "hand",
                          "notes": "Finger pose '%s' (%s hand) from game-animation references/fp_hand_poses.json: %s Play as a finger-masked layer (finger bones only; hold frame 0)." % (pn, side, ctx.lib[pn].get("_note", "")),
                          "frames": 2, "duration_s": round(1 / fps, 4), "speed_mps": 0.0, "direction": None, "speed_band": None,
                          "source_file": "references/fp_hand_poses.json", "source_frames": [0, 1], "license": "project-owned (keyed)",
                          "pose": dict({"name": pn, "side": side, "angles": ang}, **({"morphs": mo} if mo else {}))})
    bones = {c.name: set(fl.finger_bones(c.name[-1])) for c in out}   # only the named side: _r and _l layers can play together
    extras = {"ig_kind": "anim", "ig_set": a["id"], "ig_clips": len(out), "ig_bone_frames": "world_aligned", "ig_mask": "fingers_<side>"}
    al.write_anim_glb(P["glb"], skel, out, P["spec"], a["id"], extras, bones=bones)
    json.dump(metas, open(P["meta"], "w"), indent=1)
    os.makedirs(P["work"], exist_ok=True)
    json.dump({"set": a["id"], "mode": "hand_poses", "clips": [m["clip"] for m in metas]}, open(os.path.join(P["work"], "build_report.json"), "w"), indent=1)
    print("BUILD OK -> %s (%d pose clips) + %s" % (os.path.relpath(P["glb"], P["root"]), len(out), os.path.basename(P["meta"])))


def build(animset_path, only=None):
    a = json.load(open(animset_path)); P = anim_build.paths_for(animset_path, a)
    ctx = Ctx(a, P)
    if a.get("mode") == "hand_poses": return build_hand_poses(a, P, ctx)
    out, metas, refs, report = [], [], {"E0": ctx.E0.tolist(), "clips": {}}, {"set": a["id"], "mode": "first_person", "clips": {}}
    src = next(iter(a.get("sources", {}).values()), {})
    for c0 in a["clips"]:
        c = dict(a.get("defaults", {}), **c0)
        t = time.time()
        clip, fpm, ref, rep = eval_clip(ctx, c)
        loop = c.get("loop", c["name"].endswith("_loop"))
        notes = c.get("notes", "")
        meta = {"clip": c["name"], "fps": ctx.fps, "loop": bool(loop), "root_motion": False, "additive": False, "base": None,
                "tags": c.get("tags", []), "source": "hand",
                "notes": (notes + " | " if notes else "") + "Keyed view-model clip (game-animation first-person mode); licence: %s" % src.get("license", "project-owned (keyed)"),
                "frames": clip.F, "duration_s": round(clip.duration, 4), "speed_mps": 0.0, "direction": None, "speed_band": None,
                "source_file": os.path.relpath(animset_path, P["root"]), "source_frames": [0, clip.F - 1], "license": src.get("license", "project-owned (keyed)"),
                "fp": fpm}
        out.append(clip); metas.append(meta); refs["clips"][c["name"]] = ref; report["clips"][c["name"]] = rep
        print("GANIM fp clip %-32s %3d fr %5.2fs %s reach err r %.2f l %.2f cm  events %s  (%.1fs)" % (
            c["name"], clip.F, clip.duration, "loop " if loop else "", rep["max_reach_err_cm"][0], rep["max_reach_err_cm"][1],
            ",".join("%s@%d" % (e["name"], e["frame"]) for e in fpm["events"]) or "-", time.time() - t))
    bones = set(fl.keyed_bones(ctx.skel))
    extras = {"ig_kind": "anim", "ig_set": a["id"], "ig_clips": len(out), "ig_bone_frames": "world_aligned", "ig_mask": "arms",
              "ig_fp": {"item": a.get("item"), "grip_r": fl.tjson(fl.tinv(ctx.grip_r)), "eye_m": [round(float(x), 5) for x in ctx.E0], "fov_deg": ctx.fpc.get("viewmodel_fov_deg"), "near_m": ctx.fpc.get("near_m")}}
    al.write_anim_glb(P["glb"], ctx.skel, out, P["spec"], a["id"], extras, bones=bones)
    json.dump(metas, open(P["meta"], "w"), indent=1)
    os.makedirs(P["work"], exist_ok=True)
    json.dump(refs, open(os.path.join(P["work"], "fp_ref.json"), "w"))
    json.dump(report, open(os.path.join(P["work"], "build_report.json"), "w"), indent=1, default=float)
    # FK reference for the playback test (arm joints + attach frames, model space)
    REF = ["hand_r", "hand_l", "lower_arm_r", "lower_arm_l", "index_03_r", "thumb_03_l"]
    ref = {"joints": REF, "clips": {}}
    for clip in out:
        Wc, Pw = al.fk(ctx.skel, clip); fr = sorted({0, clip.F // 3, clip.F // 2, (2 * clip.F) // 3, clip.F - 1})
        ar = fl.attach_world(ctx.skel, Wc, Pw, "r"); alw = fl.attach_world(ctx.skel, Wc, Pw, "l")
        ref["clips"][clip.name] = {"frames": fr, "pos": {j: Pw[fr, ctx.skel.i(j)].round(5).tolist() for j in REF},
                                   "attach_r": {"q": ar[0][fr].round(6).tolist(), "p": ar[1][fr].round(5).tolist()},
                                   "attach_l": {"q": alw[0][fr].round(6).tolist(), "p": alw[1][fr].round(5).tolist()},
                                   "duration": clip.duration, "loop": metas[out.index(clip)]["loop"]}
    json.dump(ref, open(os.path.join(P["work"], "ref.json"), "w"))
    print("BUILD OK -> %s (%d clips) + %s" % (os.path.relpath(P["glb"], P["root"]), len(out), os.path.basename(P["meta"])))
    return report


if __name__ == "__main__":
    build(sys.argv[1])


def write_blend(animset_path):
    """Headless Blender: _work/<set>.fp.blend (keyed arms per clip on NLA tracks, the item on attach_hand_r at
    grip_r with its parts keyed from the meta curves, muted IK rigs on animated hand targets, the eye camera)."""
    import subprocess
    from gam_cli import Slot, blender_bin
    a = json.load(open(animset_path)); P = anim_build.paths_for(animset_path, a); ctx = Ctx(a, P)
    skel = ctx.skel; spec = P["spec"]
    clips, _ = al.read_anim_glb(P["glb"], skel, spec)
    meta = {m["clip"]: m for m in json.load(open(P["meta"]))}
    ref = json.load(open(os.path.join(P["work"], "fp_ref.json")))["clips"]
    names = list(clips)
    npz = os.path.join(P["work"], "fp_blend.npz")
    np.savez_compressed(npz, names=json.dumps(names), **{"ql_%d" % i: clips[n].ql for i, n in enumerate(names)})
    arms = None
    try:
        import anim_cli
        aid, ok = anim_cli.fp_arms(P); arms = anim_cli.part_path(P, aid) if ok else None
    except SystemExit: pass
    job = {"skeleton": skel.to_dict(), "skeleton_glb": os.path.join(P["root"], spec["animation"]["skeleton_glb"]), "arms_glb": arms,
           "item_glb": ctx.item.path, "item_id": a["item"], "parts_def": ctx.item.parts_def, "grip_r": fl.tjson(fl.tinv(ctx.grip_r)),
           "npz": npz, "meta": meta, "ref": ref, "keyed": fl.keyed_bones(skel), "eye": ctx.E0.tolist(),
           "fov": ctx.fpc.get("viewmodel_fov_deg", 54), "near": ctx.fpc.get("near_m", 0.01), "blend": os.path.join(P["work"], a["id"] + ".fp.blend")}
    jp = os.path.join(P["work"], "fp_blend_job.json"); json.dump(job, open(jp, "w"))
    with Slot("build", 3):
        r = subprocess.run([blender_bin(), "-b", "--factory-startup", "--python-exit-code", "1", "--python", os.path.join(HERE, "fp_blend.py"), "--", "--job", jp], capture_output=True, text=True)
    open(os.path.join(P["work"], "fp_blend.log"), "w").write(r.stdout + "\n--- stderr ---\n" + r.stderr)
    ok = r.returncode == 0 and os.path.exists(job["blend"])
    print("BLEND %s -> %s" % ("OK" if ok else "FAILED (see _work/fp_blend.log)", os.path.relpath(job["blend"], P["root"])))
    return ok
