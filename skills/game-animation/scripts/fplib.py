"""
fplib.py -- first-person (view-model) core of game-animation. numpy only (plus scipy's cKDTree
for the item surface when available), so it runs in CPython and in Blender's Python like animlib.

The contract (references/first-person.md, spec.json animation.first_person):
  * rig: the shared skeleton. Clips key ONLY the arm chains (shoulder -> upper_arm -> lower_arm ->
    hand -> 15 finger joints, per side). Everything else stays at rest, so the camera frame is fixed
    relative to the shoulders.
  * camera frame C: origin = the eye point E0 = midpoint of eye_l/eye_r rest joints of
    char_skeleton_ref (glTF model space), axes = the model's (+Z forward, +Y up, +X = the
    character's left). The engine parents the view-model rig to the camera with E0 on the camera.
  * held items are parented to attach_hand_r with an IDENTITY transform (engine Rig.attach), so the
    item's world transform IS attach_hand_r's: R_item = Wc_hand * A_attach (canonical world rotation of
    the hand times the attach bone's rest world rotation), p_item = P_hand + Wc_hand (P_att0 - P_hand0).

Authoring convention in briefs (every space): positions are [right, up, forward] metres, rotations
are [yaw, pitch, roll] degrees (yaw + = turn right, pitch + = nose up, roll + = clockwise as seen from
behind), applied roll -> pitch -> yaw in the space's own axes. Internally everything is glTF model space
(right = -X), so a brief [r, u, f] is the vector (-r, u, f).

Transforms are (q, p) tuples: q (4,) xyzw, p (3,). tmul(a, b) = a o b.
"""
import json, math, os, sys
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import animlib as al  # noqa: E402

FINGERS = ("thumb", "index", "middle", "ring", "pinky")
I4 = np.array([0, 0, 0, 1.0])
DEG = math.pi / 180

# finger capsule radii (m) per joint 01..03 on the MakeHuman-derived m_avg hand (skin around the bone line)
FINGER_R = {"thumb": (0.0105, 0.0098, 0.0088), "index": (0.0092, 0.0084, 0.0077), "middle": (0.0095, 0.0087, 0.0079),
            "ring": (0.0090, 0.0082, 0.0075), "pinky": (0.0080, 0.0073, 0.0068)}
PALM_R = 0.0115


# --------------------------------------------------------------------------- small transform algebra

def T(q=None, p=None): return (al.qnorm(np.array(q if q is not None else I4, float)), np.array(p if p is not None else (0, 0, 0), float))
def tmul(a, b): return (al.qnorm(al.qmul(a[0], b[0])), a[1] + al.qrot(a[0], b[1]))
def tinv(a): qi = al.qconj(a[0]); return (qi, -al.qrot(qi, a[1]))
def tpoint(a, v): return a[1] + al.qrot(a[0], np.asarray(v, float))
def tlerp(a, b, u):
    return (al.qslerp(a[0], b[0], float(u)), a[1] + (b[1] - a[1]) * float(u))
def tjson(a): return {"t": [round(float(x), 6) for x in a[1]], "r": [round(float(x), 7) for x in a[0]]}


def brief_vec(v):
    """[right, up, forward] -> model (-r, u, f)."""
    v = list(v) + [0.0] * (3 - len(v)); return np.array([-float(v[0]), float(v[1]), float(v[2])])


def brief_rot(r):
    """[yaw, pitch, roll] deg (yaw + right, pitch + up, roll + clockwise from behind) -> quaternion (model axes)."""
    r = list(r or [0, 0, 0]) + [0.0] * (3 - len(r or []))
    qy = al.qaxis([0, 1, 0], -float(r[0]) * DEG); qp = al.qaxis([1, 0, 0], -float(r[1]) * DEG); qr = al.qaxis([0, 0, 1], float(r[2]) * DEG)
    return al.qnorm(al.qmul(qy, al.qmul(qp, qr)))


def brief_axes(fingers, thumb):
    """Hand frame from directions (brief [r, u, f] in the space): attach +Z = fingers, +Y = thumb side
    (orthogonalised against the fingers), +X = Y x Z (the left palm faces -X, the right palm +X)."""
    z = brief_vec(fingers); z /= np.linalg.norm(z)
    y = brief_vec(thumb); y = y - np.dot(y, z) * z; y /= np.linalg.norm(y)
    return al.qfrom_mat(np.stack([np.cross(y, z), y, z], -1))


def brief_T(d, dpos=None, drot=None):
    q0 = brief_axes(d["fingers_dir"], d["thumb_dir"]) if d.get("fingers_dir") is not None else al.qaxis([0, 1, 0], 0.0)
    t = (al.qnorm(al.qmul(brief_rot(d.get("rot")), q0)), brief_vec(d.get("pos", [0, 0, 0])))
    if drot is not None or dpos is not None:     # key offsets: applied in the space (not the item's local frame)
        t = (al.qnorm(al.qmul(brief_rot(drot or [0, 0, 0]), t[0])), t[1] + brief_vec(dpos or [0, 0, 0]))
    return t


# --------------------------------------------------------------------------- easing

def ease(u, kind="inout"):
    u = min(1.0, max(0.0, float(u)))
    if kind in (None, "linear"): return u
    if kind == "in": return u ** 3
    if kind == "out": return 1 - (1 - u) ** 3
    if kind in ("inout", "smooth"): return u * u * (3 - 2 * u) if kind == "smooth" else (4 * u ** 3 if u < 0.5 else 1 - (-2 * u + 2) ** 3 / 2)
    if kind == "in2": return u * u
    if kind == "out2": return 1 - (1 - u) ** 2
    if kind == "hold": return 0.0 if u < 1 else 1.0
    if kind == "snap": return 1.0 if u > 0 else 0.0
    if kind == "back": s = 1.4; u1 = u - 1; return 1 + (s + 1) * u1 ** 3 + s * u1 ** 2   # small overshoot, settles
    raise ValueError("unknown ease %s" % kind)


def curve(keys, F, default=0.0):
    """keys [[frame, value, ease?], ...] -> (F,) values; before the first key = its value; after the last = held."""
    out = np.full(F, float(default))
    if not keys: return out
    ks = sorted(keys, key=lambda k: k[0])
    for f in range(F):
        if f <= ks[0][0]: out[f] = ks[0][1]; continue
        if f >= ks[-1][0]: out[f] = ks[-1][1]; continue
        for a, b in zip(ks, ks[1:]):
            if a[0] <= f <= b[0]:
                u = (f - a[0]) / max(1e-9, b[0] - a[0]); e = ease(u, b[2] if len(b) > 2 else "linear")
                out[f] = a[1] + (b[1] - a[1]) * e; break
    return out


# --------------------------------------------------------------------------- camera

def eye_point(skel):
    """E0: midpoint of the eye_l / eye_r rest joints (glTF model space)."""
    if skel.has("eye_l") and skel.has("eye_r"): return (skel.P[skel.i("eye_l")] + skel.P[skel.i("eye_r")]) / 2
    h = skel.P[skel.i("head")]; return h + np.array([0, 0.07, 0.09])


def project(pts_model, E0, fov_deg, aspect):
    """-> (ndc_x (right +), ndc_y (up +), depth) for model-space points seen from E0 looking +Z."""
    v = np.asarray(pts_model, float) - E0
    z = v[..., 2]; t = math.tan(fov_deg * DEG / 2)
    zz = np.where(z > 1e-6, z, 1e-6)
    return (-v[..., 0]) / (zz * t * aspect), v[..., 1] / (zz * t), z


# --------------------------------------------------------------------------- skeleton helpers

def arm_bones(side): return ["shoulder_" + side, "upper_arm_" + side, "lower_arm_" + side, "hand_" + side]
def finger_bones(side): return ["%s_%02d_%s" % (f, k, side) for f in FINGERS for k in (1, 2, 3)]
def keyed_bones(skel, sides="lr"):
    return [b for s in sides for b in arm_bones(s) + finger_bones(s) if skel.has(b)]


def palm_normal(skel, side):
    """Rest palm normal pointing out of the palm (medial in the A-pose)."""
    ph = skel.P[skel.i("hand_" + side)]
    v1 = skel.P[skel.i("index_01_" + side)] - ph; v2 = skel.P[skel.i("pinky_01_" + side)] - ph
    n = np.cross(v1, v2); n /= np.linalg.norm(n) + 1e-12
    medial = np.array([-np.sign(ph[0]) or -1.0, 0, 0])
    return n if np.dot(n, medial) > 0 else -n


def hand_axes(skel, side):
    """Rest hand frame: long axis (wrist -> middle knuckle), palm normal, radial (toward the thumb side)."""
    h = skel.P[skel.i("hand_" + side)]; m = skel.P[skel.i("middle_01_" + side)]
    lng = m - h; lng /= np.linalg.norm(lng)
    n = palm_normal(skel, side); n = n - np.dot(n, lng) * lng; n /= np.linalg.norm(n)
    rad = skel.P[skel.i("index_01_" + side)] - skel.P[skel.i("pinky_01_" + side)]; rad = rad - np.dot(rad, lng) * lng - np.dot(rad, n) * n
    return lng, n, rad / np.linalg.norm(rad)


# --------------------------------------------------------------------------- hand pose library

def load_pose_library(extra=None):
    p = os.path.join(os.path.dirname(HERE), "references", "fp_hand_poses.json")
    lib = json.load(open(p))["poses"]
    if extra: lib = dict(lib, **extra)
    return lib


def pose_angles(lib, spec):
    """Resolve a finger spec -> {finger: {"flex": [3], "spread": s, "twist": t, "oppose": o}}.
    spec: pose name | {"pose": name, "<finger>": {...overrides}} | {"blend": [[name, w], ...]}."""
    if spec is None: spec = "relaxed"
    if isinstance(spec, str):
        if spec not in lib: raise SystemExit("unknown hand pose %s (library: %s)" % (spec, ", ".join(sorted(lib))))
        d = lib[spec]
        if "base" in d:
            b = pose_angles(lib, d["base"]); ov = {k: v for k, v in d.items() if k in FINGERS}
            for f, o in ov.items(): b[f] = dict(b[f], **o)
            return b
        return {f: {"flex": list(d.get(f, {}).get("flex", [0, 0, 0])), "spread": float(d.get(f, {}).get("spread", 0)),
                    "twist": float(d.get(f, {}).get("twist", 0)), "oppose": float(d.get(f, {}).get("oppose", 0))} for f in FINGERS}
    if "blend" in spec:
        parts = [(pose_angles(lib, n), float(w)) for n, w in spec["blend"]]; tw = sum(w for _, w in parts)
        return blend_angles(parts, tw)
    b = pose_angles(lib, spec.get("pose", "relaxed"))
    for f in FINGERS:
        if f in spec: b[f] = dict(b[f], **spec[f])
    return b


def blend_angles(parts, tw=None):
    tw = tw or sum(w for _, w in parts); out = {}
    for f in FINGERS:
        out[f] = {"flex": [sum(p[f]["flex"][k] * w for p, w in parts) / tw for k in range(3)]}
        for k in ("spread", "twist", "oppose"): out[f][k] = sum(p[f].get(k, 0) * w for p, w in parts) / tw
    return out


def lerp_angles(a, b, u):
    return blend_angles([(a, 1 - u), (b, u)], 1.0)


class HandModel:
    """Anatomical finger axes of one hand on a skeleton (retargetable: poses are angles, the axes come
    from the skeleton's own rest joints)."""
    def __init__(self, skel, side):
        self.skel, self.side = skel, side
        self.lng, self.n, self.rad = hand_axes(skel, side)
        self.ax = {}
        mid = skel.P[skel.i("middle_01_" + side)]
        for f in FINGERS:
            for k in (1, 2, 3):
                b = "%s_%02d_%s" % (f, k, side)
                d = skel.axis(b)
                flex = np.cross(d, self.n); flex /= np.linalg.norm(flex) + 1e-12
                if f == "thumb":   # the thumb flexes across the palm: about the axis perpendicular to it and the radial direction
                    flex = np.cross(d, -self.rad if k > 1 else self.n); flex /= np.linalg.norm(flex) + 1e-12
                # spread: about the palm normal, + = away from the middle finger (index/thumb: toward the thumb)
                lat = skel.P[skel.i("%s_01_%s" % (f, side))] - mid
                if f == "middle": lat = self.rad
                if f == "thumb": lat = skel.P[skel.i("thumb_01_" + side)] - skel.P[skel.i("index_01_" + side)]
                sp = self.n.copy()
                if np.dot(np.cross(sp, d), lat) < 0: sp = -sp
                self.ax[b] = {"dir": d, "flex": flex, "spread": sp}

    def locals(self, ang):
        """{bone: canonical local quaternion} for finger angles (deg)."""
        out = {}
        for f in FINGERS:
            a = ang[f]
            for k in (1, 2, 3):
                b = "%s_%02d_%s" % (f, k, self.side)
                if not self.skel.has(b): continue
                x = self.ax[b]
                q = al.qaxis(x["flex"], a["flex"][k - 1] * DEG)
                if k == 1:
                    q = al.qmul(al.qaxis(x["spread"], a.get("spread", 0) * DEG), q)
                    if a.get("twist"): q = al.qmul(q, al.qaxis(x["dir"], a["twist"] * DEG * (1 if self.side == "r" else -1)))   # mirrored for the left hand (checked 2026-10-11: equal skin gaps on both hands)
                    if f == "thumb" and a.get("oppose"):   # swing the thumb in front of the palm about the hand's long axis
                        o = self.lng * (1 if self.side == "r" else -1)
                        q = al.qmul(al.qaxis(o, a["oppose"] * DEG * (-1 if np.dot(np.cross(o, self.rad), self.n) < 0 else 1)), q)
                out[b] = al.qnorm(q)
        return out


# --------------------------------------------------------------------------- item (held glb)

class Item:
    """A held item glb in its own (glTF) space: body LOD0 + moving parts (ig_parts) + sockets."""
    def __init__(self, path, item_id=None, spacing=0.0012):
        from validate_glb import load_glb, walk, mesh_data
        g, binb = load_glb(path); self.path = path
        roots = g["scenes"][g.get("scene", 0)]["nodes"]; rn = g["nodes"][roots[0]]
        self.id = item_id or rn.get("name")
        self.extras = rn.get("extras") or {}
        self.parts_def = self.extras.get("ig_parts") or {}
        if isinstance(self.parts_def, str): self.parts_def = json.loads(self.parts_def.replace("'", '"'))
        self.sockets, self.geo, self.part_origin = {}, {}, {}
        for i, W, par, d in walk(g, roots):
            n = g["nodes"][i]; nm = n.get("name", "")
            if nm.startswith("socket_"):
                self.sockets[nm] = (al.qfrom_mat(W[:3, :3]), W[:3, 3].copy()); continue
            if "mesh" not in n: continue
            key = None
            if nm == self.id + "_lod0": key = "_body"
            else:
                for p in self.parts_def:
                    if nm == "%s_%s" % (self.id, p): key = p
            if key is None: continue
            P, Tr = [], []; off = 0
            for m in mesh_data(g, binb, n["mesh"]):
                P.append((W @ np.c_[m["pos"], np.ones(len(m["pos"]))].T).T[:, :3]); Tr.append(m["idx"].reshape(-1, 3) + off); off += len(m["pos"])
            self.geo[key] = (np.vstack(P), np.vstack(Tr))
            if key != "_body": self.part_origin[key] = W[:3, 3].copy()
        self.spacing = spacing; self._trees = {}

    def part_T(self, name, v):
        """Part node transform in item space for a normalised value v (0 = rest, 1 = full travel/angle)."""
        d = self.parts_def[name]; o = self.part_origin.get(name, np.zeros(3)); ax = np.array(d["axis"], float); ax /= np.linalg.norm(ax)
        if d.get("move") == "rotate": return (al.qaxis(ax, float(d["angle_deg"]) * DEG * v), o.copy())
        if d.get("move") == "rotate_then_translate":
            # bolt actions: v 0..0.5 turns the handle (angle_deg about axis through the node origin),
            # v 0.5..1 keeps the full turn and slides along then_axis by travel_m (first-person.md "Moving parts")
            ta = np.array(d.get("then_axis", d["axis"]), float); ta /= np.linalg.norm(ta)
            return (al.qaxis(ax, float(d["angle_deg"]) * DEG * min(1.0, 2.0 * v)), o + ta * float(d["travel_m"]) * max(0.0, 2.0 * v - 1.0))
        return (I4.copy(), o + ax * float(d["travel_m"]) * v)

    def part_local_delta(self, name, v):
        """The part's motion as a transform applied to its REST geometry (item space)."""
        o = self.part_origin.get(name, np.zeros(3)); t = self.part_T(name, v)
        return tmul(t, (I4.copy(), -o))

    def socket(self, name): return self.sockets.get(name)

    def verts(self, part_values=None, parts=True):
        """Item-space vertices of body + parts (parts posed)."""
        pv = part_values or {}
        out = [self.geo["_body"][0]]
        if parts:
            for p in self.geo:
                if p == "_body": continue
                dt = self.part_local_delta(p, pv.get(p, 0.0)); out.append(tpoint(dt, self.geo[p][0]))
        return np.vstack(out)

    def _tree(self, key):
        if key in self._trees: return self._trees[key]
        P, Tr = self.geo[key]
        a, b, c = P[Tr[:, 0]], P[Tr[:, 1]], P[Tr[:, 2]]
        n = np.cross(b - a, c - a); area = np.linalg.norm(n, axis=-1) / 2; n = n / np.maximum(2 * area[:, None], 1e-15)
        rng = np.random.default_rng(7)
        cnt = np.maximum(1, np.ceil(area / (self.spacing ** 2)).astype(int))
        idx = np.repeat(np.arange(len(Tr)), cnt)
        u = rng.random((len(idx), 2)); flip = u.sum(1) > 1; u[flip] = 1 - u[flip]
        pts = a[idx] + (b[idx] - a[idx]) * u[:, :1] + (c[idx] - a[idx]) * u[:, 1:]
        pts = np.vstack([pts, P]); nrm = np.vstack([n[idx], np.zeros_like(P)])
        # vertex normals = area-weighted face normals (for the sign test at vertices)
        vn = np.zeros_like(P)
        for k in range(3): np.add.at(vn, Tr[:, k], n * area[:, None])
        nrm[len(idx):] = vn / np.maximum(np.linalg.norm(vn, axis=-1, keepdims=True), 1e-12)
        try:
            from scipy.spatial import cKDTree
            tree = cKDTree(pts)
        except ImportError:
            tree = None
        self._trees[key] = (tree, pts, nrm); return self._trees[key]

    def winding(self, key, q, chunk=256):
        """Generalised winding number of points q (N,3) w.r.t. one part's triangles (~1 inside, ~0 outside)."""
        P, Tr = self.geo[key]; a0, b0, c0 = P[Tr[:, 0]], P[Tr[:, 1]], P[Tr[:, 2]]; out = np.zeros(len(q))
        for s0 in range(0, len(q), chunk):
            qq = q[s0:s0 + chunk, None, :]
            a, b, c = a0[None] - qq, b0[None] - qq, c0[None] - qq
            la, lb, lc = (np.linalg.norm(x, axis=-1) for x in (a, b, c))
            num = np.einsum("nij,nij->ni", a, np.cross(b, c))
            den = la * lb * lc + np.einsum("nij,nij->ni", a, b) * lc + np.einsum("nij,nij->ni", b, c) * la + np.einsum("nij,nij->ni", c, a) * lb
            out[s0:s0 + chunk] = np.abs(np.arctan2(num, den).sum(1) / (2 * np.pi))
        return out

    def sdf(self, q, part_values=None, parts=True, exclude=()):
        """Signed distance (m, negative inside) from item-space points q (N,3) to the item surface
        (body + parts at their values). Surface sampled every ~1.2 mm; sign from the nearest samples' normals."""
        q = np.asarray(q, float).reshape(-1, 3); best = np.full(len(q), np.inf)
        keys = ["_body"] + ([k for k in self.geo if k != "_body" and k not in exclude] if parts else [])
        pv = part_values or {}
        for k in keys:
            qq = q if k == "_body" else tpoint(tinv(self.part_local_delta(k, pv.get(k, 0.0))), q)
            tree, pts, nrm = self._tree(k)
            if tree is not None:
                dist, ii = tree.query(qq, k=4)
            else:
                d2 = ((qq[:, None, :] - pts[None]) ** 2).sum(-1); ii = np.argsort(d2, 1)[:, :4]; dist = np.sqrt(np.take_along_axis(d2, ii, 1))
            s = np.sign(np.sum(((qq[:, None, :] - pts[ii]) * nrm[ii]).sum(-1) * np.exp(-dist / 0.002), 1))
            s[s == 0] = 1
            # the normal vote is only reliable near the surface: far points (open/hollow parts, e.g. a slide's
            # channel) outside the part's bbox are outside; far points inside it get a winding-number test
            far = dist[:, 0] > 0.004
            if far.any():
                lo, hi = self.geo[k][0].min(0), self.geo[k][0].max(0)
                inb = far & np.all((qq >= lo) & (qq <= hi), 1)
                s[far & ~inb] = 1
                if inb.any(): s[inb] = np.where(self.winding(k, qq[inb]) > 0.5, -1, 1)
            dd = dist[:, 0] * s
            better = np.abs(dd) < np.abs(best) if False else dd < best
            best = np.where(better, dd, best)
        return best


# --------------------------------------------------------------------------- hand geometry (capsules)

def hand_capsules(skel, Wc, P, side):
    """-> list of (name, a (…,3), b (…,3), radius) finger + palm capsules for one hand, from canonical FK."""
    out = []
    h = skel.i("hand_" + side)
    for f in FINGERS:
        for k in (1, 2, 3):
            b = "%s_%02d_%s" % (f, k, side)
            if not skel.has(b): continue
            i = skel.i(b)
            if k < 3: a_, b_ = P[..., i, :], P[..., skel.i("%s_%02d_%s" % (f, k + 1, side)), :]
            else:
                tip0 = skel.P[i] + skel.axis(b) * skel.blen[i] * 0.85
                a_, b_ = P[..., i, :], P[..., i, :] + al.qrot(Wc[..., i, :], tip0 - skel.P[i])
            out.append((b, a_, b_, FINGER_R[f][k - 1]))
    for f in ("index", "middle", "ring", "pinky"):
        j = skel.i("%s_01_%s" % (f, side))
        out.append(("palm_" + f, P[..., h, :] + (P[..., j, :] - P[..., h, :]) * 0.25, P[..., j, :], PALM_R))
    return out


def capsule_points(a, b, n=5):
    """Sample points along capsule axes: a, b (...,3) -> (..., n, 3)."""
    u = np.linspace(0, 1, n)
    return a[..., None, :] + (b - a)[..., None, :] * u[:, None]


# --------------------------------------------------------------------------- arm IK

def elbow_axis_rest(skel, side):
    """Rest elbow flex axis (world): forearm swings toward +Z (in front) for positive flex (joint_limits.json)."""
    u = skel.P[skel.i("lower_arm_" + side)] - skel.P[skel.i("upper_arm_" + side)]; u /= np.linalg.norm(u)
    a = np.cross(u, np.array([0, 0, 1.0])); return a / np.linalg.norm(a)


def frame_from(u, a):
    u = u / np.linalg.norm(u); a = a - np.dot(a, u) * u; a /= np.linalg.norm(a)
    return np.stack([u, a, np.cross(u, a)], -1)


def solve_arm(skel, ql, side, hand_q, hand_p, pole, shrug=0.18, shrug_max_deg=22.0, twist_share=0.85):
    """Set canonical locals of shoulder/upper_arm/lower_arm/hand (one frame, ql (B,4) in place) so
    hand_<side> reaches world position hand_p with canonical world rotation hand_q. The elbow bends
    toward `pole` (model-space direction). Upper-body bones are assumed at rest (the view-model rig).
    Returns the reach error (m)."""
    sh, ua, la, ha = (skel.i(b) for b in arm_bones(side))
    Psh = skel.P[sh]; Pua0 = skel.P[ua]; Pla0 = skel.P[la]; Pha0 = skel.P[ha]
    par = skel.parent[sh]
    Wpar = I4.copy()                           # upper_chest at rest in the view-model rig
    # clavicle: a fraction of the swing from the rest shoulder->hand line to the target line
    r0 = Pha0 - Psh; r1 = hand_p - Psh
    qs = al.qbetween(r0, r1); ang = al.qangle(qs) * shrug
    ang = min(ang, shrug_max_deg * DEG)
    axis = qs[:3] / (np.linalg.norm(qs[:3]) + 1e-12)
    qsh = al.qaxis(axis, ang) if np.linalg.norm(qs[:3]) > 1e-9 else I4.copy()
    ql[sh] = qsh
    S = Psh + al.qrot(qsh, Pua0 - Psh)
    l1 = np.linalg.norm(Pla0 - Pua0); l2 = np.linalg.norm(Pha0 - Pla0)
    D = hand_p - S; dist = np.linalg.norm(D); u = D / max(dist, 1e-9)
    d = float(np.clip(dist, abs(l1 - l2) + 1e-4, (l1 + l2) * 0.9995))
    p = np.asarray(pole, float); p = p - np.dot(p, u) * u
    if np.linalg.norm(p) < 1e-6: p = np.cross(u, [1, 0, 0])
    p /= np.linalg.norm(p)
    a = (l1 * l1 - l2 * l2 + d * d) / (2 * d); hgt = math.sqrt(max(l1 * l1 - a * a, 0.0))
    E = S + u * a + p * hgt; W = S + u * d
    un = (E - S) / l1; fn = (W - E) / l2
    ax = np.cross(un, fn)
    if np.linalg.norm(ax) < 1e-6: ax = np.cross(p, un)       # straight arm: axis from the pole side
    ax /= np.linalg.norm(ax)
    u0 = (Pla0 - Pua0) / l1; a0 = elbow_axis_rest(skel, side)
    Mw = frame_from(un, ax) @ frame_from(u0, a0).T
    Wua = al.qfrom_mat(Mw)
    Wsh = al.qmul(Wpar, qsh)
    ql[ua] = al.qnorm(al.qmul(al.qconj(Wsh), Wua))
    theta = math.atan2(np.dot(np.cross(un, fn), ax), np.dot(un, fn))
    qla = al.qaxis(a0, theta)
    # forearm twist: most of the hand's twist about the forearm axis goes on lower_arm roll (the arms have no
    # twist bones: hand roll kept small avoids candy-wrapper wrists)
    Wla = al.qmul(Wua, qla)
    hl = al.qmul(al.qconj(Wla), hand_q)
    f0 = (Pha0 - Pla0) / l2
    _, tw = al.swing_twist(hl, f0)
    k = float(tw) * twist_share
    qla = al.qmul(qla, al.qaxis(f0, k))
    Wla = al.qmul(Wua, qla)
    ql[la] = al.qnorm(qla)
    ql[ha] = al.qnorm(al.qmul(al.qconj(Wla), hand_q))
    return float(abs(np.linalg.norm(hand_p - S) - d))


def attach_offset(skel, side):
    """(A_attach, rest offset of attach_hand_<side> from hand_<side>) for the item <-> hand relation."""
    a = skel.i("attach_hand_" + side); h = skel.i("hand_" + side)
    return skel.A[a], skel.P[a] - skel.P[h]


def hand_from_attach(skel, side, att):
    """Canonical hand world (q, p) whose attach_hand_<side> frame is `att` (model space, item convention)."""
    A, off = attach_offset(skel, side)
    qh = al.qnorm(al.qmul(att[0], al.qconj(A)))
    return qh, att[1] - al.qrot(qh, off)


def attach_from_hand(skel, side, Wc_h, P_h):
    A, off = attach_offset(skel, side)
    return (al.qnorm(al.qmul(Wc_h, A)), P_h + al.qrot(Wc_h, off))


def attach_world(skel, Wc, P, side):
    """(F,4), (F,3) attach_hand_<side> frame (item convention) from a canonical FK."""
    A, off = attach_offset(skel, side); h = skel.i("hand_" + side)
    return al.qnorm(al.qmul(Wc[:, h], A[None])), P[:, h] + al.qrot(Wc[:, h], off[None])


# --------------------------------------------------------------------------- secondary motion

def periodic_noise(F, period_frames, amp, seed, harmonics=(1, 2, 3)):
    """Smooth noise that repeats every period_frames (integer harmonics)."""
    rng = np.random.default_rng(seed); t = np.arange(F) / max(1, period_frames)
    x = np.zeros(F)
    for h in harmonics:
        x += rng.normal() * np.sin(2 * np.pi * h * t + rng.random() * 2 * np.pi) / h
    return x * amp / max(1e-9, np.abs(x).max() if np.abs(x).max() > 0 else 1)


def breath_sway(F, fps, loop, cfg):
    """Camera-space sway transforms (F,) from a breathing cycle + drift. Seamless over the clip when `loop`."""
    if not cfg: return [T() for _ in range(F)]
    per = cfg.get("period_s", 4.0) * fps
    if loop: per = (F - 1) / max(1, round((F - 1) / per))
    ph = 2 * np.pi * np.arange(F) / per + cfg.get("phase", 0.0)
    pos = np.array(cfg.get("pos_m", [0.0012, 0.003, 0.0015]))     # [right, up, fwd] amplitudes
    rot = np.array(cfg.get("rot_deg", [0.25, 0.45, 0.2]))          # [yaw, pitch, roll]
    br = np.sin(ph); br2 = np.sin(ph - 0.9)
    dr = cfg.get("drift_deg", 0.25); dper = (F - 1) if loop else per * 2
    nz = [periodic_noise(F, dper, dr, cfg.get("seed", 1) + k) for k in range(3)]
    P_ = np.stack([pos[0] * br2, pos[1] * br, pos[2] * br2], 1)
    R_ = np.stack([rot[0] * br2 + nz[0], rot[1] * br + nz[1], rot[2] * br2 + nz[2] * 0.5], 1)
    if not loop and F > 1 and cfg.get("end_match", True):
        # one-shot clips end on the sway they started with (= the idle loop's phase 0), so chained clips
        # (fire -> idle, reload_round -> reload_round ...) meet without a sub-cm drift step at the seam
        w = (np.arange(F) / (F - 1))[:, None]
        P_ = P_ - w * (P_[-1] - P_[0]); R_ = R_ - w * (R_[-1] - R_[0])
    out = []
    for f in range(F):
        out.append((brief_rot(list(R_[f])), brief_vec(list(P_[f]))))
    return out


def recoil_curve(F, fps, shots):
    """Damped-spring recoil: per frame (kick pitch deg, back m, rise m, roll deg, yaw deg) summed over shots.
    Each shot: {f, kick_deg, back_m, rise_m, roll_deg, yaw_deg, freq_hz, damping} -- the impulse peaks within
    ~1.5 frames, springs back past zero slightly, and settles (heavy, not snappy)."""
    out = np.zeros((F, 5))
    for s in shots or []:
        f0 = s["f"]; w = 2 * np.pi * s.get("freq_hz", 4.2); z = s.get("damping", 0.62)
        wd = w * math.sqrt(max(1e-6, 1 - z * z))
        amp = np.array([s.get("kick_deg", 7.0), s.get("back_m", 0.035), s.get("rise_m", 0.012), s.get("roll_deg", 1.5), s.get("yaw_deg", 0.8)])
        for f in range(F):
            t = (f - f0) / fps
            if t < 0: continue
            # impulse response of a damped spring, normalised to peak 1
            y = math.exp(-z * w * t) * math.sin(wd * t)
            out[f] += amp * y / (math.exp(-z * w * (math.atan2(wd, z * w) / wd)) * math.sin(math.atan2(wd, z * w)))
    return out


def spring_settle(F, fps, hits):
    """Arrival settle: a small damped dip after a pose lands (weight). hits: [{f, down_m, pitch_deg, freq_hz, damping}]."""
    out = np.zeros((F, 2))
    for s in hits or []:
        w = 2 * np.pi * s.get("freq_hz", 3.0); z = s.get("damping", 0.55); wd = w * math.sqrt(1 - z * z)
        for f in range(F):
            t = (f - s["f"]) / fps
            if t < 0: continue
            y = math.exp(-z * w * t) * math.sin(wd * t)
            out[f] += np.array([s.get("down_m", 0.008), s.get("pitch_deg", 1.0)]) * y
    return out


# --------------------------------------------------------------------------- strong-hand grip solve

def suggest_grip(skel, item, side, ang, web=None, contact=("palm_middle", "palm_ring", "index_01", "index_02"), x0=None, part_values=None, knuckle=None):
    """Solve the hand's attach frame in ITEM space (the grip transform G: item = attach o G^-1) so the palm
    rests on the grip without entering it and the thumb web sits at `web` (item space, e.g. under the
    hammer spur on the backstrap). Unknowns: translation (3) + yaw/pitch/roll (3), from x0 (brief [r,u,f,
    yaw,pitch,roll]). ang = the hand's finger pose (open fingers are fine: only palm/web are scored).
    Returns (brief dict {pos, rot}, report)."""
    from scipy.optimize import minimize
    import fp_build as fb
    hm = HandModel(skel, side); loc = hm.locals(ang)
    web = np.asarray(web if web is not None else [0.0, 0.027, -0.024], float)
    def pose(x):
        G = (brief_rot(list(x[3:6])), brief_vec(list(x[:3])))
        qh, ph = hand_from_attach(skel, side, G)
        return fb.hand_fk(skel, side, qh, ph, loc)
    def cost(x, rep=False):
        fk = pose(x)
        caps = {c[0]: c for c in fb.caps_from_fk(skel, side, fk)}
        c = 0.0; out = {}
        for n in ("palm_index", "palm_middle", "palm_ring", "palm_pinky", "thumb_01_" + side, "index_01_" + side, "index_02_" + side, "index_03_" + side):
            _, a, b, r = caps[n]; sd = item.sdf(capsule_points(a, b, 7), part_values) - r
            pen = max(0.0, -float(sd.min())); gap = max(0.0, float(sd.min()))
            c += (pen * 1000) ** 2 * 4 + ((gap * 1000) ** 2 * (1.0 if n.replace("_" + side, "") in contact else 0.0))
            out[n] = round(float(sd.min()) * 1000, 1)
        wp = (fk["thumb_02_" + side][1] + fk["index_01_" + side][1]) / 2
        c += (np.linalg.norm(wp - web) * 1000) ** 2 * 0.5
        out["web_err_mm"] = round(float(np.linalg.norm(wp - web)) * 1000, 1)
        if knuckle is not None:   # middle-finger MCP at the front strap's edge, so fingers 3-5 can wrap it
            kp = fk["middle_01_" + side][1]
            c += (np.linalg.norm(kp - knuckle) * 1000) ** 2 * 0.5
            out["knuckle_err_mm"] = round(float(np.linalg.norm(kp - knuckle)) * 1000, 1)
        c += 0.02 * (x[3] ** 2 + x[5] ** 2)
        return (c, out) if rep else c
    x0 = np.array(x0 if x0 is not None else [0.015, -0.012, -0.028, 0, -8, 0], float)
    best = None
    for dx in (np.zeros(6), np.array([0.005, 0.005, 0.005, 5, 5, 5]), np.array([-0.005, -0.005, 0.005, -5, 0, -5])):
        r = minimize(cost, x0 + dx, method="Nelder-Mead", options={"xatol": 1e-4, "fatol": 1e-3, "maxiter": 1500})
        if best is None or r.fun < best.fun: best = r
    c, out = cost(best.x, True)
    x = best.x
    return {"pos": [round(float(v), 4) for v in x[:3]], "rot": [round(float(v), 2) for v in x[3:6]]}, dict(out, cost=round(float(c), 2))


# --------------------------------------------------------------------------- the arms asset's own hand skin
# The capsules above sit on the bone lines; on char_fp_arms_bare_m_a the finger and palm bones run close to the
# BACK of the hand (dorsal skin 3-5 mm off the phalanx line, volar 14-18 mm; palm volar ~20 mm vs the 11.5 mm
# capsule), so a capsule-clean grip can still put the item ~8 mm into the palm skin. The grip solve below and the
# weapons' frame_check (ig_grip_r) therefore score the real skinned vertices (linear blend skinning of the hand
# and finger bones; the arms' bind pose is the skeleton rest, canonical rest rotations are identity).

class SkinHand:
    """Hand skin of a view-model arms glb (one side): vertices mostly weighted to hand_<s> + fingers, posed by LBS."""
    def __init__(self, glb, skel, side):
        from validate_glb import load_glb, walk, accessor
        g, binb = load_glb(glb); roots = g["scenes"][g.get("scene", 0)]["nodes"]
        node = next(g["nodes"][i] for i, W, p, d in walk(g, roots) if "mesh" in g["nodes"][i] and "skin" in g["nodes"][i]
                    and not g["nodes"][i].get("name", "").endswith(("_lod1", "_lod2")))
        sk = g["skins"][node["skin"]]; jn = [g["nodes"][j]["name"] for j in sk["joints"]]
        P, J, Wt, Tr = [], [], [], []; off = 0
        for pr in g["meshes"][node["mesh"]]["primitives"]:
            at = pr["attributes"]; p = accessor(g, binb, at["POSITION"])
            P.append(p); J.append(accessor(g, binb, at["JOINTS_0"]).astype(int)); Wt.append(accessor(g, binb, at["WEIGHTS_0"]).astype(float))
            Tr.append(accessor(g, binb, pr["indices"]).astype(int).reshape(-1, 3) + off); off += len(p)
        P, J, Wt, Tr = np.vstack(P), np.vstack(J), np.vstack(Wt), np.vstack(Tr)
        self.skel, self.side, self.glb = skel, side, glb
        self.bones = ["hand_" + side] + finger_bones(side)
        col = {b: k for k, b in enumerate(self.bones)}
        Wm = np.zeros((len(P), len(self.bones)))
        for c in range(J.shape[1]):
            for vi in range(len(P)):
                b = jn[J[vi, c]]
                if b in col: Wm[vi, col[b]] += Wt[vi, c]
        s = Wm.sum(1); keep = s >= 0.5
        self.idx = np.nonzero(keep)[0]; self.V = P[keep]; self.W = Wm[keep] / s[keep, None]
        remap = -np.ones(len(P), int); remap[self.idx] = np.arange(len(self.idx))
        tk = keep[Tr].all(1); self.tris = remap[Tr[tk]]
        self.dom = np.array(self.bones)[np.argmax(self.W, 1)]
        self.P0 = np.array([skel.P[skel.i(b)] for b in self.bones])
        lng, n, rad = hand_axes(skel, side); h = skel.P[skel.i("hand_" + side)]
        rel = self.V - h
        self.volar = rel @ n > 0.0
        self.region = {"palm": (self.dom == "hand_" + side) & self.volar & (rel @ lng > 0.02),
                       "back": (self.dom == "hand_" + side) & ~self.volar}
        for f in FINGERS:
            for k in (1, 2, 3):
                b = "%s_%02d_%s" % (f, k, side); self.region[b] = self.dom == b
        # thumb-index web: volar verts between the thumb and the index metacarpal
        t1, i1 = skel.P[skel.i("thumb_02_" + side)], skel.P[skel.i("index_01_" + side)]
        wc = (t1 + i1) / 2; self.region["web"] = (np.linalg.norm(self.V - wc, axis=1) < 0.018) & np.isin(self.dom, ["hand_" + side, "thumb_01_" + side, "thumb_02_" + side, "index_01_" + side])

    def pose(self, fk):
        """fk {bone: (canonical world q, p)} (hand_fk) -> posed vertices (N, 3) in the same space."""
        out = np.zeros_like(self.V)
        for k, b in enumerate(self.bones):
            q, p = fk[b]; w = self.W[:, k]
            m = w > 0
            if m.any(): out[m] += w[m, None] * (al.qrot(q, self.V[m] - self.P0[k]) + p)
        return out


_SKIN = {}
def skin_hand(glb, skel, side):
    k = (glb, side)
    if k not in _SKIN: _SKIN[k] = SkinHand(glb, skel, side)
    return _SKIN[k]


def skin_morphs(skin):
    """Morph-target POSITION deltas of the arms mesh (e.g. palm_fold_r), restricted to the hand skin's vertices
    (skin.idx): {target name: (len(skin.V), 3)} in the same space as skin.V. Dense and sparse accessors. (2026-10-13)"""
    if getattr(skin, "_morphs", None) is not None: return skin._morphs
    from validate_glb import load_glb, walk, accessor, CT, NC
    g, binb = load_glb(skin.glb); roots = g["scenes"][g.get("scene", 0)]["nodes"]
    node = next(g["nodes"][i] for i, W, p, d in walk(g, roots) if "mesh" in g["nodes"][i] and "skin" in g["nodes"][i]
                and not g["nodes"][i].get("name", "").endswith(("_lod1", "_lod2")))
    mesh = g["meshes"][node["mesh"]]; names = (mesh.get("extras") or {}).get("targetNames", [])
    def acc(i):
        a = g["accessors"][i]; n = NC[a["type"]]
        out = accessor(g, binb, i) if "bufferView" in a else np.zeros((a["count"], n))
        sp = a.get("sparse")
        if sp:
            ii, vv = sp["indices"], sp["values"]; c = sp["count"]
            bi = g["bufferViews"][ii["bufferView"]]; bv = g["bufferViews"][vv["bufferView"]]
            idx = np.frombuffer(binb, CT[ii["componentType"]], count=c, offset=bi.get("byteOffset", 0) + ii.get("byteOffset", 0)).astype(int)
            val = np.frombuffer(binb, CT[a["componentType"]], count=c * n, offset=bv.get("byteOffset", 0) + vv.get("byteOffset", 0)).reshape(c, n)
            out = out.copy(); out[idx] = val
        return out
    res = {}
    for t, nm in enumerate(names):
        D = [acc(pr["targets"][t]["POSITION"]) for pr in mesh["primitives"] if len(pr.get("targets", [])) > t and "POSITION" in pr["targets"][t]]
        if D: res[nm] = np.vstack(D)[skin.idx]
    skin._morphs = res
    return res


def with_morphs(skin, weights):
    """Context manager: skin.V += sum(w * delta) for {target: w} (skin_morphs) while inside, restored after."""
    import contextlib
    @contextlib.contextmanager
    def cm():
        V0 = skin.V
        D = skin_morphs(skin) if weights else {}
        if D and any(k in D for k in weights): skin.V = V0 + sum(float(w) * D[k] for k, w in weights.items() if k in D)
        try: yield [k for k in (weights or {}) if k in D]
        finally: skin.V = V0
    return cm()


def _hand_fk(skel, side, qh, ph, locals_):
    out = {"hand_" + side: (qh, ph)}
    for f in FINGERS:
        for k in (1, 2, 3):
            b = "%s_%02d_%s" % (f, k, side)
            par = skel.names[skel.parent[skel.i(b)]]; qp, pp = out[par]
            out[b] = (al.qmul(qp, locals_.get(b, I4)), pp + al.qrot(qp, skel.P[skel.i(b)] - skel.P[skel.i(par)]))
    return out


def fit_fingers_skin(skel, item, side, ang, hand_w, skin, item_w=None, tol=0.0010, n=25, open_deg=-8.0, pv=None, fingers=FINGERS, obstacle=None):
    """Wrap fit on the real skin: proximal -> distal, each finger joint takes the most curled angle in
    [open_deg, pose angle] at which the skin of its segment clears the item (and `obstacle`, a callable
    model-space points -> signed distance, e.g. the other hand) by -tol. item_w = item world frame (None = item space)."""
    import copy as _c
    hm = HandModel(skel, side); cur = _c.deepcopy(ang); qh, ph = hand_w; info = {}
    inv = tinv(item_w) if item_w is not None else None
    def clear(V):
        q = tpoint(inv, V) if inv is not None else V
        d = item.sdf(q, pv) if item is not None else np.full(len(V), 1.0)
        if obstacle is not None: d = np.minimum(d, obstacle(V))
        return float(d.min())
    def joint(cur, f, k):
        b = "%s_%02d_%s" % (f, k + 1, side); m = skin.region[b]
        # the knuckle joint also drags the palm skin weighted to it: that skin may not end deeper than it sits with
        # the finger straight (or than -tol)
        mx = None
        if k == 0:
            mx = (skin.region["palm"] | skin.region["web"]) & (skin.W[:, skin.bones.index(b)] > 0.08)
            if not mx.any(): mx = None
            else:
                c = _c.deepcopy(cur); c[f]["flex"][0] = 0.0
                ref = clear(skin.pose(_hand_fk(skel, side, qh, ph, hm.locals(c)))[mx])
        hi = float(cur[f]["flex"][k]); lo = min(open_deg, hi); best = None
        for a_ in np.linspace(hi, lo, n):
            c = _c.deepcopy(cur); c[f]["flex"][k] = float(a_)
            Vall = skin.pose(_hand_fk(skel, side, qh, ph, hm.locals(c)))
            cl = clear(Vall[m])
            if mx is not None: cl = min(cl, clear(Vall[mx]) - min(0.0, ref))
            if cl >= -tol: return (float(a_), cl, True)
            if best is None or cl > best[1]: best = (float(a_), cl, False)
        return best
    def sweep(cur, f):
        hi0 = [float(v) for v in cur[f]["flex"]]; st = [None, None, None]; k = 0; guard = 0
        while k < 3 and guard < 40:
            guard += 1
            best = joint(cur, f, k)
            if not best[2] and f != "thumb" and k > 0 and cur[f]["flex"][k - 1] > open_deg + 1e-6:   # (the thumb's joints swing it across the palm: no backtrack)
                # backtrack: the parent joint curled too far round the item for this phalanx to clear at any angle --
                # uncurl the parent 10 deg, restore this joint's library bound, and refit from the parent
                cur[f]["flex"][k - 1] = max(open_deg, cur[f]["flex"][k - 1] - 10.0); cur[f]["flex"][k] = hi0[k]
                k -= 1
                b2 = joint(cur, f, k); cur[f]["flex"][k] = b2[0]; st[k] = b2; k += 1; continue
            cur[f]["flex"][k] = best[0]; st[k] = best; k += 1
        res = ["%.0f%s" % (x[0], "" if x[2] else "!%.1fmm" % (x[1] * 1000)) for x in st if x is not None]
        worst = min([x[1] for x in st if x is not None] + [1.0])
        return cur, " ".join(res), worst
    for f in fingers:
        c0 = _c.deepcopy(cur); cur, info[f], worst = sweep(cur, f)
        if f == "thumb" and worst < -tol:
            # the thumb can re-aim (opposition, spread) and curl further: among the settings that clear, keep the one
            # whose pad LIES ON the item (wrapped round it, as a real hammer grip), then the smallest change -- not a
            # thumb swung straight out into the air
            tm = skin.region["thumb_03_" + side]; pal = skin.region["palm"] | skin.region["web"]
            def pad_gap(c):
                V = skin.pose(_hand_fk(skel, side, qh, ph, hm.locals(c)))[tm]; return clear(V)
            def palm_cl(c):     # the thenar skin rides thumb_01: a re-aimed thumb must not drag it into the item
                return clear(skin.pose(_hand_fk(skel, side, qh, ph, hm.locals(c)))[pal])
            pal0 = palm_cl(c0)
            best = None
            done = False
            for do, dsp, d1, dfl in [(o, sp, a1, a2) for o in (0, -15, -30, -45) for sp in (0, 15, 30, 45) for a1 in (0, -30) for a2 in (0, 30)]:
                if done: break
                if True:
                    if True:
                        c = _c.deepcopy(c0); t_ = c["thumb"]
                        t_["oppose"] = t_.get("oppose", 0) + do; t_["spread"] = t_.get("spread", 0) + dsp
                        t_["flex"] = [t_["flex"][0] + d1, t_["flex"][1] + dfl, t_["flex"][2] + dfl * 0.8]
                        c, inf, w = sweep(c, "thumb")
                        # the palm may press in with the re-aim, but no deeper than 3 mm (or than it already was)
                        if w >= -tol: w = min(w, palm_cl(c) - min(pal0 - 0.0005, -0.003) - tol)
                        g_ = pad_gap(c) if w >= -tol else 1.0
                        key = (0 if w >= -tol else 1, abs(g_) if w >= -tol else -w, abs(do) + abs(dsp) + abs(dfl) + abs(d1))
                        if best is None or key < best[0]: best = (key, c, inf, do, dsp, dfl)
                        if key[0] == 0 and key[1] < 0.0015: done = True; break     # cleared with the pad on the item
            if best[0][0] == 0:      # a re-aim that clears (and keeps the palm): use it; else keep the plain fit
                cur, info[f] = best[1], best[2] + " (oppose %+d spread %+d curl %+d)" % best[3:6]
    return cur, info


def grip_regions(skel, item, side, G, ang, skin, pv=None):
    """Signed distance (mm, - = into the item) per skin region for the hand at attach frame G (item space)."""
    qh, ph = hand_from_attach(skel, side, G)
    V = skin.pose(_hand_fk(skel, side, qh, ph, HandModel(skel, side).locals(ang)))
    d = item.sdf(V, pv)
    out = {}
    for r, m in skin.region.items():
        if m.any(): out[r] = round(float(d[m].min()) * 1000, 2)
    return out, V, d


def solve_grip_skin(skel, item, side, ang, skin, cls="pistol_grip", x0=None, web=None, knuckle=None, pads=None,
                    score=None, contact=None, prior=None, pen_tol=0.0008, starts=None, rise=None, align=None, obstacle=None, pv=None):
    """Grip transform G (attach_hand_<side> frame in ITEM space; the item hangs at attach o G^-1, so the item's
    local TRS under attach_hand_<side> is G^-1 = `ig_grip_r`) solved on the arms' own hand SKIN.
      cls pistol_grip: palm, web and index side touching the grip, no skin inside the item; `rise` =
                       (item-space axis, weight per mm) pushes the web up the grip until it meets the tang (a high
                       grip); `align` = (finger, item-space axis, weight per deg^2): e.g. the index finger along the
                       frame (parallel to the bore); optional joint targets: web joint midpoint `web`, middle knuckle `knuckle`;
      cls fist:        palm touching the handle, no skin inside, attach origin kept at the item origin height
                       (the fingers wrap later);
      cls pinch:       thumb and index pads touching the item either side of the origin, nothing else inside.
    ang = finger pose (open the wrapping fingers before calling: only the scored regions matter). obstacle = callable
    item-space points -> signed distance of something else the skin must clear (the strong hand for a support grip).
    pv = moving-part values the grip assumes (the item's ig_grip_parts, e.g. an ammo tin's carry handle up).
    x0 = brief [r, u, f, yaw, pitch, roll]. Returns ({pos, rot} brief, report)."""
    from scipy.optimize import minimize
    hm = HandModel(skel, side); loc = hm.locals(ang); s = side
    if score is None:
        score = {"pistol_grip": ["palm", "back", "web", "thumb_01_" + s, "index_01_" + s, "index_02_" + s, "index_03_" + s],
                 "fist": ["palm", "back", "web", "thumb_01_" + s, "index_01_" + s],
                 "pinch": list(skin.region)}[cls]
    if contact is None:
        contact = {"pistol_grip": ["palm", "web", "index_01_" + s], "fist": ["palm"], "pinch": ["thumb_03_" + s, "index_03_" + s]}[cls]
    sm = np.zeros(len(skin.V), bool)
    for r in score: sm |= skin.region[r]
    web = None if web is None else np.asarray(web, float); knuckle = None if knuckle is None else np.asarray(knuckle, float)
    def pose(x):
        G = (brief_rot(list(x[3:6])), brief_vec(list(x[:3])))
        qh, ph = hand_from_attach(skel, side, G)
        fk = _hand_fk(skel, side, qh, ph, loc)
        return G, fk, skin.pose(fk)
    def cost(x, rep=False):
        G, fk, V = pose(x)
        d = item.sdf(V[sm], pv)
        if obstacle is not None: d = np.minimum(d, obstacle(V[sm]))     # e.g. the other hand's skin (item space)
        full = np.full(len(V), np.inf); full[sm] = d
        pen = max(0.0, -float(d.min()) - pen_tol)
        c = (pen * 1000) ** 2 * 6 + float(np.sum(np.maximum(0, -d - pen_tol) ** 2)) * 1e6 * 0.05
        out = {"pen_mm": round(max(0.0, -float(d.min())) * 1000, 2)}
        for r in contact:
            g_ = float(full[skin.region[r]].min()) if skin.region[r].any() else 0.0
            if not np.isfinite(g_): g_ = float(item.sdf(V[skin.region[r]], pv).min())
            c += (max(0.0, g_) * 1000) ** 2; out["gap_" + r] = round(g_ * 1000, 2)
        if web is not None:
            wp = (fk["thumb_02_" + s][1] + fk["index_01_" + s][1]) / 2; e = float(np.linalg.norm(wp - web))
            c += (e * 1000) ** 2 * 0.5; out["web_err_mm"] = round(e * 1000, 1)
        if knuckle is not None:
            e = float(np.linalg.norm(fk["middle_01_" + s][1] - knuckle)); c += (e * 1000) ** 2 * 0.3; out["knuckle_err_mm"] = round(e * 1000, 1)
        if rise is not None:
            wp = (fk["thumb_02_" + s][1] + fk["index_01_" + s][1]) / 2; ax_ = np.asarray(rise[0], float); ax_ = ax_ / np.linalg.norm(ax_)
            c -= float(wp @ ax_) * 1000 * float(rise[1]); out["web_up_mm"] = round(float(wp @ ax_) * 1000, 1)
        if align is not None:
            f_, ax_, w_ = align; ax_ = np.asarray(ax_, float) / np.linalg.norm(ax_)
            dv = fk["%s_03_%s" % (f_, s)][1] - fk["%s_01_%s" % (f_, s)][1]; dv = dv / np.linalg.norm(dv)
            a_ = math.degrees(math.acos(float(np.clip(dv @ ax_, -1, 1)))); c += a_ * a_ * float(w_); out["align_deg"] = round(a_, 1)
        if pads is not None:     # pinch: pads either side of the origin
            tp = (fk["thumb_03_" + s][1]); ip = fk["index_03_" + s][1]
            mid = (tp + ip) / 2; e = float(np.linalg.norm(mid - np.asarray(pads, float)))
            c += (e * 1000) ** 2 * 0.5; out["pads_mid_err_mm"] = round(e * 1000, 1)
        if cls == "fist" and prior is None:
            ay = float(G[1] @ al.qrot(G[0], np.array([0, 1.0, 0])))   # attach origin along the hand's thumb axis vs the item origin
            c += (ay * 1000) ** 2 * 0.2
        if prior is not None:
            c += float(np.sum(((np.asarray(x) - np.asarray(prior["x"])) / np.asarray(prior["sigma"])) ** 2))
        c += 0.02 * (x[3] ** 2 + x[5] ** 2)
        return (c, out) if rep else c
    x0 = np.array(x0 if x0 is not None else {"pistol_grip": [0.015, -0.012, -0.028, 0, -8, 0], "fist": [0.016, 0, 0, 0, 0, 0],
                                             "pinch": [0.03, -0.03, -0.05, 0, 0, 0]}[cls], float)
    best = None
    for dx in (starts or (np.zeros(6), np.array([0.005, 0.005, 0.005, 5, 5, 5]), np.array([-0.005, -0.005, 0.005, -5, 0, -5]),
                          np.array([0.003, 0.008, -0.005, 0, -6, 0]), np.array([-0.003, -0.008, 0.005, 0, 6, 0]))):
        r = minimize(cost, x0 + np.asarray(dx, float), method="Nelder-Mead", options={"xatol": 1e-4, "fatol": 1e-3, "maxiter": 2000})
        if best is None or r.fun < best.fun: best = r
    c, out = cost(best.x, True); x = best.x
    return {"pos": [round(float(v), 4) for v in x[:3]], "rot": [round(float(v), 2) for v in x[3:6]]}, dict(out, cost=round(float(c), 2))


def grip_trs(brief_grip):
    """brief {pos, rot} grip (attach frame in item space) -> the item's local TRS under attach_hand ({t, r} glTF), i.e. ig_grip_r."""
    return tjson(tinv(brief_T(brief_grip)))


def grip_from_trs(trs):
    """ig_grip_r {t, r} (item local TRS under attach_hand) -> G (attach frame in item space) as a transform."""
    return tinv(T(trs["r"], trs["t"]))


def skin_sdf(V, tris):
    """Approximate signed distance (m, - inside) to a posed skin (V, tris): nearest-vertex distance with the sign of
    the nearest vertices' normals (the hand skin is closed enough at grip scale). -> callable q (N,3) -> (N,)."""
    from scipy.spatial import cKDTree
    a, b, c = V[tris[:, 0]], V[tris[:, 1]], V[tris[:, 2]]
    fn = np.cross(b - a, c - a); vn = np.zeros_like(V)
    for k in range(3): np.add.at(vn, tris[:, k], fn)
    vn /= np.maximum(np.linalg.norm(vn, axis=1, keepdims=True), 1e-12)
    # densify with triangle centroids so the distance is not vertex-spacing coarse
    pts = np.vstack([V, (a + b + c) / 3]); nrm = np.vstack([vn, fn / np.maximum(np.linalg.norm(fn, axis=1, keepdims=True), 1e-12)])
    tree = cKDTree(pts)
    def f(q):
        q = np.asarray(q, float).reshape(-1, 3); d, i = tree.query(q, k=3)
        s = np.sign(np.sum(((q[:, None, :] - pts[i]) * nrm[i]).sum(-1) * np.exp(-d / 0.002), 1)); s[s == 0] = 1
        return d[:, 0] * s
    return f


# --------------------------------------------------------------------------- finger limits + fingertip-into-palm (validate_fp)

# anatomical ranges (deg) per joint on the HandModel axes; spec animation.first_person.finger_limits overrides
# (thumb_01 total = the CMC's combined flex + abduction + opposition swing: full opposition reaches ~95-100)
FINGER_LIMITS = {"flex": {1: [-30, 95], 2: [-15, 115], 3: [-20, 90]}, "spread_1": 30, "lateral_23": 12, "twist": 25,   # PIP/DIP min: passive hyperextension (fits open joints to -8, keys blend)
                 "thumb": {"total_1": 100, "flex": {2: [-30, 75], 3: [-25, 90]}}}


def finger_angles(hm, ql_side):
    """{bone: (flex, side, twist)} deg from canonical local quats (rotation vector on the HandModel axes)."""
    out = {}
    for b, x in hm.ax.items():
        if b not in ql_side: continue
        rv = np.degrees(al.qlog(np.asarray(ql_side[b], float)))
        out[b] = (float(rv @ x["flex"]), float(rv @ x["spread"]), float(rv @ x["dir"]), float(np.linalg.norm(rv)))
    return out


def finger_limit_overs(hm, ql_side, lim=None):
    """-> [(over_deg, bone, what)] for one hand's finger locals."""
    L = lim or FINGER_LIMITS; res = []
    for b, (fx, sd, tw, tot) in finger_angles(hm, ql_side).items():
        f, k = b.split("_")[0], int(b.split("_")[1])
        if f == "thumb":
            if k == 1: res.append((tot - L["thumb"]["total_1"], b, "total %.0f" % tot))
            else:
                lo, hi = L["thumb"]["flex"][k]; res.append((max(lo - fx, fx - hi), b, "flex %.0f [%g, %g]" % (fx, lo, hi)))
            continue
        lo, hi = L["flex"][k]; res.append((max(lo - fx, fx - hi), b, "flex %.0f [%g, %g]" % (fx, lo, hi)))
        sl = L["spread_1"] if k == 1 else L["lateral_23"]; res.append((abs(sd) - sl, b, "%s %.0f (|%g|)" % ("spread" if k == 1 else "lateral", sd, sl)))
        res.append((abs(tw) - L["twist"], b, "twist %.0f (|%g|)" % (tw, L["twist"])))
    return res


def fingertip_palm_pen(skel, side, skin, locals_, hm=None):
    """Deepest fingertip skin (distal + middle phalanges, thumb tip) under the palm skin, mm (0 = clear), and which
    finger. Hand-local: the hand at rest, fingers at `locals_`; palm skin as a height field over the palm plane, a
    point counts when it lies over the palm footprint (4 mm), below the palm surface and above the back of the hand."""
    from scipy.spatial import cKDTree
    hm = hm or HandModel(skel, side); lng, n, rad = hm.lng, hm.n, hm.rad; h = skel.P[skel.i("hand_" + side)]
    V = skin.pose(_hand_fk(skel, side, I4, h, locals_)) - h
    def hf(m): Q = V[m]; return cKDTree(np.c_[Q @ lng, Q @ rad]), Q @ n
    tp, hp = hf(skin.region["palm"]); tb, hb = hf(skin.region["back"]); best = (0.0, "")
    for f in FINGERS:
        m = skin.region["%s_03_%s" % (f, side)] | (skin.region["%s_02_%s" % (f, side)] if f != "thumb" else False)
        Q = V[m]; uv = np.c_[Q @ lng, Q @ rad]; qn = Q @ n
        d, i = tp.query(uv, k=3); _, ib = tb.query(uv); surf = hp[i].mean(1)
        ins = (d[:, 0] < 0.004) & (qn < surf) & (qn > hb[ib] - 0.002)
        if ins.any() and float((surf - qn)[ins].max()) * 1000 > best[0]: best = (float((surf - qn)[ins].max()) * 1000, f)
    return best


def finger_finger_pen(skel, side, skin, locals_):
    """Adjacent-finger clearance on the skin (index-middle, middle-ring, ring-pinky), hand-local: the hand at rest, fingers
    at `locals_`. Middle + distal phalanx skin of each finger against the other's (proximal skin is left out: it runs into
    the web and the knuckle pads, where adjacent fingers' skins join by construction). Depth = a vertex's distance behind the
    neighbour skin's local tangent plane (normal-weighted over its 3 nearest vertices); gap = nearest vertex distance. -> (deepest penetration mm, "a-b", {"a-b": (pen_mm, gap_mm)})."""
    from scipy.spatial import cKDTree
    V = skin.pose(_hand_fk(skel, side, I4, skel.P[skel.i("hand_" + side)], locals_))
    a, b, c = V[skin.tris[:, 0]], V[skin.tris[:, 1]], V[skin.tris[:, 2]]
    fn = np.cross(b - a, c - a); vn = np.zeros_like(V)
    for k in range(3): np.add.at(vn, skin.tris[:, k], fn)
    vn /= np.maximum(np.linalg.norm(vn, axis=1, keepdims=True), 1e-12)
    m = {f: skin.region["%s_02_%s" % (f, side)] | skin.region["%s_03_%s" % (f, side)] for f in FINGERS if f != "thumb"}
    F = [f for f in FINGERS if f != "thumb"]; pairs = {}; best = (0.0, "")
    for A, B in zip(F, F[1:]):
        pen, gap = 0.0, float("inf")
        for X, Y in ((A, B), (B, A)):
            P, N = V[m[Y]], vn[m[Y]]; q = V[m[X]]
            if not len(P) or not len(q): continue
            d, i = cKDTree(P).query(q, k=3)
            w = np.exp(-d / 0.002); h = (((q[:, None] - P[i]) * N[i]).sum(-1) * w).sum(1) / w.sum(1)   # signed distance to the local skin plane
            pen = max(pen, float(np.clip(-h, 0.0, None).max()) * 1000); gap = min(gap, float(d[:, 0].min()) * 1000)
        pairs["%s-%s" % (A, B)] = (round(pen, 1), round(gap, 1))
        if pen > best[0]: best = (pen, "%s-%s" % (A, B))
    return best[0], best[1], pairs


def thumb_finger_pen(skel, side, skin, locals_):
    """Thumb-to-finger clearance on the skin (added 2026-10-13), hand-local like finger_finger_pen: thumb middle + distal
    skin against each finger's whole skin (all three phalanges: in a cup the thumb lies along the index's proximal phalanx).
    Depth as finger_finger_pen, counted only within 6 mm of the other skin. -> (deepest pen mm, finger, {finger: (pen_mm, gap_mm)})."""
    from scipy.spatial import cKDTree
    V = skin.pose(_hand_fk(skel, side, I4, skel.P[skel.i("hand_" + side)], locals_))
    a, b, c = V[skin.tris[:, 0]], V[skin.tris[:, 1]], V[skin.tris[:, 2]]
    fn = np.cross(b - a, c - a); vn = np.zeros_like(V)
    for k in range(3): np.add.at(vn, skin.tris[:, k], fn)
    vn /= np.maximum(np.linalg.norm(vn, axis=1, keepdims=True), 1e-12)
    th = skin.region["thumb_02_" + side] | skin.region["thumb_03_" + side]; out = {}; best = (0.0, "")
    for f in FINGERS:
        if f == "thumb": continue
        m = skin.region["%s_01_%s" % (f, side)] | skin.region["%s_02_%s" % (f, side)] | skin.region["%s_03_%s" % (f, side)]
        pen, gap = 0.0, float("inf")
        for X, Y in ((th, m), (m, th)):
            P, N = V[Y], vn[Y]; q = V[X]
            if not len(P) or not len(q): continue
            d, i = cKDTree(P).query(q, k=3)
            w = np.exp(-d / 0.002); h = (((q[:, None] - P[i]) * N[i]).sum(-1) * w).sum(1) / w.sum(1)
            pen = max(pen, float(np.clip(-h[d[:, 0] < 0.006], 0.0, None).max(initial=0.0)) * 1000); gap = min(gap, float(d[:, 0].min()) * 1000)
        out[f] = (round(pen, 1), round(gap, 1))
        if pen > best[0]: best = (pen, f)
    return best[0], best[1], out
