"""
animlib.py -- numpy core of the game-animation skill. Runs under plain CPython and under
Blender's bundled Python (it needs numpy only), so the retarget math is one implementation
used both inside the headless Blender import and by the CPython build/validator.

Conventions (all glTF model space: +Y up, character faces +Z, character's LEFT is +X):
  quaternions are (x, y, z, w) arrays, shape (..., 4)
  Skeleton  rest pose of the target (char_skeleton_ref): world positions P, world rotations A
  Clip      "canonical" data: per-frame LOCAL rotations in world-aligned bone frames
            (rest = identity for every bone), root translation, hips offset from root.
            This is the frame convention the engine's retarget.ts assumes for clips
            (q_rig = A_parent^-1 * q_canonical * A_bone), so the exported file plays on any
            rig whose rest is the A-pose, whatever its bone rolls.
"""
import json, math, os, sys
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
GAM_SCRIPTS = os.path.join(os.path.dirname(os.path.dirname(HERE)), "game-asset-modeling", "scripts")
if GAM_SCRIPTS not in sys.path: sys.path.insert(0, GAM_SCRIPTS)

UP = np.array([0.0, 1.0, 0.0])
DIR_VEC = {  # travel direction per suffix, glTF root space (right = -X)
    "f": (0, 1), "fl": (1, 1), "l": (1, 0), "bl": (1, -1), "b": (0, -1), "br": (-1, -1), "r": (-1, 0), "fr": (-1, 1)}
DIRS = ["f", "fl", "l", "bl", "b", "br", "r", "fr"]

# --------------------------------------------------------------------------- quaternions

def qnorm(q):
    q = np.asarray(q, float); return q / np.maximum(np.linalg.norm(q, axis=-1, keepdims=True), 1e-12)


def qmul(a, b):
    a = np.asarray(a, float); b = np.asarray(b, float)
    ax, ay, az, aw = np.moveaxis(a, -1, 0); bx, by, bz, bw = np.moveaxis(b, -1, 0)
    return np.stack([aw * bx + ax * bw + ay * bz - az * by,
                     aw * by - ax * bz + ay * bw + az * bx,
                     aw * bz + ax * by - ay * bx + az * bw,
                     aw * bw - ax * bx - ay * by - az * bz], -1)


def qconj(q):
    q = np.array(q, float); q[..., :3] *= -1; return q


def qrot(q, v):
    q = np.asarray(q, float); v = np.asarray(v, float)
    u = q[..., :3]; w = q[..., 3:4]
    t = 2.0 * np.cross(u, v)
    return v + w * t + np.cross(u, t)


def qaxis(axis, ang):
    axis = np.asarray(axis, float); axis = axis / np.maximum(np.linalg.norm(axis, axis=-1, keepdims=True), 1e-12)
    ang = np.asarray(ang, float)[..., None]
    return np.concatenate([axis * np.sin(ang / 2), np.cos(ang / 2)], -1)


def qyaw(yaw):
    yaw = np.asarray(yaw, float)
    return np.stack([np.zeros_like(yaw), np.sin(yaw / 2), np.zeros_like(yaw), np.cos(yaw / 2)], -1)


def qbetween(a, b):
    """Minimal rotation taking unit vector a onto unit vector b (broadcasts)."""
    a = np.asarray(a, float); b = np.asarray(b, float)
    a = a / np.maximum(np.linalg.norm(a, axis=-1, keepdims=True), 1e-12)
    b = b / np.maximum(np.linalg.norm(b, axis=-1, keepdims=True), 1e-12)
    c = np.cross(a, b); d = np.sum(a * b, -1, keepdims=True)
    q = np.concatenate([c, 1.0 + d], -1)
    bad = (q[..., 3] < 1e-8)
    if np.any(bad):   # opposite vectors: any perpendicular axis
        perp = np.cross(a, np.array([1.0, 0, 0])); small = np.linalg.norm(perp, axis=-1) < 1e-6
        perp = np.where(small[..., None], np.cross(a, np.array([0, 0, 1.0])), perp)
        q = np.where(bad[..., None], np.concatenate([perp, np.zeros_like(d)], -1), q)
    return qnorm(q)


def qslerp(a, b, t):
    a = qnorm(a); b = qnorm(b); t = np.asarray(t, float)
    d = np.sum(a * b, -1, keepdims=True); b = np.where(d < 0, -b, b); d = np.abs(d)
    t = t[..., None] if t.ndim == a.ndim - 1 else t
    lin = d > 0.9995
    th = np.arccos(np.clip(d, -1, 1)); s = np.sin(th)
    wa = np.where(lin, 1 - t, np.sin((1 - t) * th) / np.where(lin, 1, s))
    wb = np.where(lin, t, np.sin(t * th) / np.where(lin, 1, s))
    return qnorm(wa * a + wb * b)


def qangle(q):
    q = qnorm(q); return 2 * np.arccos(np.clip(np.abs(q[..., 3]), 0, 1))


def qfrom_mat(m):
    """3x3 (..., 3, 3) rotation matrices -> quaternions. Columns are normalised first (strips scale)."""
    m = np.asarray(m, float); m = m / np.maximum(np.linalg.norm(m, axis=-2, keepdims=True), 1e-12)
    t = m[..., 0, 0] + m[..., 1, 1] + m[..., 2, 2]
    out = np.zeros(m.shape[:-2] + (4,))
    c0 = t > 0
    s = np.sqrt(np.maximum(t + 1, 1e-12)) * 2
    out0 = np.stack([(m[..., 2, 1] - m[..., 1, 2]) / s, (m[..., 0, 2] - m[..., 2, 0]) / s, (m[..., 1, 0] - m[..., 0, 1]) / s, 0.25 * s], -1)
    s1 = np.sqrt(np.maximum(1 + m[..., 0, 0] - m[..., 1, 1] - m[..., 2, 2], 1e-12)) * 2
    out1 = np.stack([0.25 * s1, (m[..., 0, 1] + m[..., 1, 0]) / s1, (m[..., 0, 2] + m[..., 2, 0]) / s1, (m[..., 2, 1] - m[..., 1, 2]) / s1], -1)
    s2 = np.sqrt(np.maximum(1 + m[..., 1, 1] - m[..., 0, 0] - m[..., 2, 2], 1e-12)) * 2
    out2 = np.stack([(m[..., 0, 1] + m[..., 1, 0]) / s2, 0.25 * s2, (m[..., 1, 2] + m[..., 2, 1]) / s2, (m[..., 0, 2] - m[..., 2, 0]) / s2], -1)
    s3 = np.sqrt(np.maximum(1 + m[..., 2, 2] - m[..., 0, 0] - m[..., 1, 1], 1e-12)) * 2
    out3 = np.stack([(m[..., 0, 2] + m[..., 2, 0]) / s3, (m[..., 1, 2] + m[..., 2, 1]) / s3, 0.25 * s3, (m[..., 1, 0] - m[..., 0, 1]) / s3], -1)
    c1 = (~c0) & (m[..., 0, 0] > m[..., 1, 1]) & (m[..., 0, 0] > m[..., 2, 2])
    c2 = (~c0) & (~c1) & (m[..., 1, 1] > m[..., 2, 2])
    c3 = (~c0) & (~c1) & (~c2)
    for c, o in ((c0, out0), (c1, out1), (c2, out2), (c3, out3)): out = np.where(c[..., None], o, out)
    return qnorm(out)


def qto_mat(q):
    x, y, z, w = np.moveaxis(qnorm(q), -1, 0)
    return np.stack([np.stack([1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)], -1),
                     np.stack([2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)], -1),
                     np.stack([2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)], -1)], -2)


def qcontinuous(q, axis=0):
    """Flip signs so consecutive quaternions along `axis` stay in one hemisphere (no slerp flips)."""
    q = np.array(q, float); q = np.moveaxis(q, axis, 0)
    for i in range(1, len(q)):
        s = np.sum(q[i] * q[i - 1], -1) < 0; q[i][s] *= -1
    return np.moveaxis(q, 0, axis)


def swing_twist(q, axis):
    """Decompose q = swing * twist about unit `axis`. Returns (swing_rad, signed twist_rad)."""
    q = qnorm(q); axis = np.asarray(axis, float)
    p = np.sum(q[..., :3] * axis, -1, keepdims=True) * axis
    tw = np.concatenate([p, q[..., 3:4]], -1)
    n = np.linalg.norm(tw, axis=-1, keepdims=True)
    tw = np.where(n < 1e-9, np.array([0, 0, 0, 1.0]), tw / np.maximum(n, 1e-12))
    sw = qmul(q, qconj(tw))
    ta = 2 * np.arctan2(np.sum(tw[..., :3] * axis, -1), tw[..., 3])
    ta = (ta + np.pi) % (2 * np.pi) - np.pi
    return qangle(sw), ta


def yaw_of_dir(v):
    """Yaw (radians about +Y) of a ground direction, 0 = +Z, positive toward +X."""
    return np.arctan2(v[..., 0], v[..., 2])


# --------------------------------------------------------------------------- skeleton

def spec_bones(spec):
    return list(spec["skeleton"]["bones"])


def gltf_node_world(g):
    from validate_glb import walk, node_matrix  # noqa: F401  (reused from game-asset-modeling)
    W, par = {}, {}
    roots = g["scenes"][g.get("scene", 0)]["nodes"]
    for i, M, p, d in walk(g, roots): W[i] = M; par[i] = p
    return W, par


class Skeleton:
    """Target rest pose. names in topological order; parent index -1 for the root."""

    def __init__(self, names, parent, P, A, blen=None):
        self.names = list(names); self.parent = np.asarray(parent, int)
        self.P = np.asarray(P, float); self.A = qnorm(np.asarray(A, float))
        self.idx = {n: i for i, n in enumerate(self.names)}
        self.offset = np.array([self.P[i] - (self.P[p] if p >= 0 else 0) for i, p in enumerate(self.parent)])
        self.blen = np.asarray(blen if blen is not None else np.full(len(names), 0.05), float)
        self.children = {i: [j for j, p in enumerate(self.parent) if p == i] for i in range(len(names))}

    def __len__(self): return len(self.names)
    def i(self, n): return self.idx[n]
    def has(self, n): return n in self.idx
    def axis(self, n):
        """Rest bone direction (Blender exports bones with local +Y along the bone)."""
        return qrot(self.A[self.idx[n]], UP)

    @classmethod
    def from_glb(cls, path, bone_names, skel_json=None):
        from validate_glb import load_glb, quat_mat  # noqa
        g, _ = load_glb(path)
        W, par = gltf_node_world(g)
        byname = {}
        for i, n in enumerate(g["nodes"]):
            if n.get("name") in bone_names and n.get("name") not in byname: byname[n["name"]] = i
        missing = [b for b in bone_names if b not in byname]
        if missing: raise ValueError("skeleton glb lacks bones: %s" % missing)
        names, parent = [], []
        rev = {v: k for k, v in byname.items()}
        order = []
        def visit(b):
            if b in order: return
            p = par[byname[b]]; pb = rev.get(p)
            if pb: visit(pb)
            order.append(b)
        for b in bone_names: visit(b)
        P = np.array([W[byname[b]][:3, 3] for b in order])
        A = qfrom_mat(np.array([W[byname[b]][:3, :3] for b in order]))
        parent = [order.index(rev[par[byname[b]]]) if rev.get(par[byname[b]]) else -1 for b in order]
        blen = np.full(len(order), 0.05)
        if skel_json and os.path.exists(skel_json):
            sj = {b["name"]: b for b in json.load(open(skel_json))["bones"]}
            for k, b in enumerate(order):
                if b in sj: blen[k] = float(np.linalg.norm(np.subtract(sj[b]["tail"], sj[b]["head"])))
        return cls(order, parent, P, A, blen)

    def to_dict(self):
        return {"names": self.names, "parent": self.parent.tolist(), "P": self.P.tolist(), "A": self.A.tolist(), "blen": self.blen.tolist()}

    @classmethod
    def from_dict(cls, d): return cls(d["names"], d["parent"], d["P"], d["A"], d.get("blen"))


def load_target(spec, assets_root):
    a = spec.get("animation", {})
    glb = os.path.join(assets_root, a.get("skeleton_glb", "export/char/char_skeleton_ref.glb"))
    sj = os.path.join(assets_root, a.get("skeleton_json", "export/char/char_skeleton_ref.skeleton.json"))
    return Skeleton.from_glb(glb, spec_bones(spec), sj)


# --------------------------------------------------------------------------- clip data

class Clip:
    """Canonical clip. ql: (F,B,4) local rotations (world-aligned frames, rest = identity).
    root_p: (F,3) root translation. hips_d: (F,3) hips offset from root in root's frame."""

    def __init__(self, fps, ql, root_p, hips_d, name="", meta=None):
        self.fps = float(fps); self.ql = qnorm(np.asarray(ql, float))
        self.root_p = np.asarray(root_p, float); self.hips_d = np.asarray(hips_d, float)
        self.name = name; self.meta = dict(meta or {})

    @property
    def F(self): return len(self.ql)
    @property
    def duration(self): return (self.F - 1) / self.fps

    def copy(self, **kw):
        c = Clip(self.fps, self.ql.copy(), self.root_p.copy(), self.hips_d.copy(), self.name, self.meta)
        for k, v in kw.items(): setattr(c, k, v)
        return c

    def slice(self, a, b):
        """Frames a..b inclusive."""
        return Clip(self.fps, self.ql[a:b + 1].copy(), self.root_p[a:b + 1].copy(), self.hips_d[a:b + 1].copy(), self.name, self.meta)


def fk(skel, clip):
    """-> (Wc (F,B,4) canonical world rotations, P (F,B,3) world joint positions)."""
    F, B = clip.F, len(skel)
    Wc = np.zeros((F, B, 4)); P = np.zeros((F, B, 3))
    r, h = skel.i("root"), skel.i("hips")
    for b in range(B):
        p = skel.parent[b]
        if p < 0:
            Wc[:, b] = clip.ql[:, b]; P[:, b] = clip.root_p
        else:
            Wc[:, b] = qmul(Wc[:, p], clip.ql[:, b])
            if b == h: P[:, b] = P[:, p] + qrot(Wc[:, p], clip.hips_d)
            else: P[:, b] = P[:, p] + qrot(Wc[:, p], skel.offset[b])
    return Wc, P


def locals_from_world(skel, Wc):
    ql = np.zeros_like(Wc)
    for b in range(len(skel)):
        p = skel.parent[b]
        ql[:, b] = Wc[:, b] if p < 0 else qmul(qconj(Wc[:, p]), Wc[:, b])
    return qnorm(ql)


def world_point(skel, Wc, P, bone, rest_point):
    """Pose a rest-space world point rigidly attached to `bone`."""
    b = skel.i(bone)
    return P[:, b] + qrot(Wc[:, b], np.asarray(rest_point, float) - skel.P[b])


# --------------------------------------------------------------------------- retarget

TARGET_CHILD = {"hips": "spine", "spine": "chest", "chest": "upper_chest", "upper_chest": "neck", "neck": "head",
                "upper_leg_l": "lower_leg_l", "lower_leg_l": "foot_l", "foot_l": "toes_l",
                "upper_leg_r": "lower_leg_r", "lower_leg_r": "foot_r", "foot_r": "toes_r",
                "shoulder_l": "upper_arm_l", "upper_arm_l": "lower_arm_l", "lower_arm_l": "hand_l", "hand_l": "middle_01_l",
                "shoulder_r": "upper_arm_r", "upper_arm_r": "lower_arm_r", "lower_arm_r": "hand_r", "hand_r": "middle_01_r"}
for _s in "lr":
    for _f in ("thumb", "index", "middle", "ring", "pinky"):
        TARGET_CHILD["%s_01_%s" % (_f, _s)] = "%s_02_%s" % (_f, _s); TARGET_CHILD["%s_02_%s" % (_f, _s)] = "%s_03_%s" % (_f, _s)

RELAXED_HAND_DEG = {"thumb": (4, 8, 6), "index": (14, 18, 10), "middle": (17, 22, 12), "ring": (21, 25, 12), "pinky": (25, 28, 12)}


def norm_name(n):
    return n.split(":")[-1].split("|")[-1].strip().lower()


def load_mapping(name_or_path):
    p = name_or_path
    if not os.path.exists(p): p = os.path.join(os.path.dirname(HERE), "references", "mappings", name_or_path + ".json")
    return json.load(open(p))


def detect_mapping(src_names):
    """Pick the mapping table whose bone names cover the source best."""
    d = os.path.join(os.path.dirname(HERE), "references", "mappings"); best = (0, None)
    have = {norm_name(n) for n in src_names}
    for f in sorted(os.listdir(d)):
        if not f.endswith(".json"): continue
        m = json.load(open(os.path.join(d, f)))
        vals = [v for v in m["bones"].values() if v]
        cov = sum(1 for v in vals if norm_name(v) in have) / max(1, len(vals))
        if cov > best[0]: best = (cov, f[:-5])
    return best


def finger_curl(skel, Wc, side, scale=1.0):
    """Apply a neutral relaxed hand (in place on canonical world rotations Wc (F,B,4))."""
    hb = "hand_" + side
    if not all(skel.has(x) for x in (hb, "index_01_" + side, "pinky_01_" + side)): return
    ph = skel.P[skel.i(hb)]
    v1 = skel.P[skel.i("index_01_" + side)] - ph; v2 = skel.P[skel.i("pinky_01_" + side)] - ph
    n = np.cross(v1, v2); n /= np.linalg.norm(n) + 1e-12
    medial = np.array([-np.sign(ph[0]) or -1.0, 0, 0])      # palms face the thigh in the A-pose
    if np.dot(n, medial) < 0: n = -n
    for f, angs in RELAXED_HAND_DEG.items():
        for k in range(3):
            b = "%s_%02d_%s" % (f, k + 1, side)
            if not skel.has(b): continue
            bi = skel.i(b)
            d = skel.axis(b)
            ax = np.cross(d, n);
            if np.linalg.norm(ax) < 1e-6: continue
            R = qaxis(ax, math.radians(angs[k] * scale))
            # canonical world of this finger = parent world * (rotation about the rest axis)
            Wc[:, bi] = qmul(Wc[:, bi], R)
            for c in _descendants(skel, bi): Wc[:, c] = qmul(Wc[:, c], R)


def _descendants(skel, b):
    out, st = [], list(skel.children[b])
    while st:
        c = st.pop(); out.append(c); st.extend(skel.children[c])
    return out


def neutral_frame(skel, bm, X, weights=None):
    """Frame of a take whose pose is closest to the target rest pose, bone by bone (direction
    from each mapped joint to its next mapped child, after removing the frame's own facing).
    For takes whose bind pose is not a usable T-pose (ACCAD Male1/Male2 BVH store an arbitrary
    bind with the upper spine along +X), the retarget rest must come from real posed data: a
    standing frame if the take has one, else the most neutral walking frame. Twist is carried
    relative to this frame, so a near-neutral pose keeps the rest alignment C small."""
    pairs = []
    for t, si in bm.items():
        ch = TARGET_CHILD.get(t)
        while ch and ch not in bm: ch = TARGET_CHILD.get(ch)
        if not ch or not skel.has(ch): continue
        td = skel.P[skel.i(ch)] - skel.P[skel.i(t)]
        if np.linalg.norm(td) < 1e-4: continue
        pairs.append((si, bm[ch], td / np.linalg.norm(td), (weights or {}).get(t, 1.0)))
    X = np.asarray(X, float)
    left = X[:, bm["upper_leg_l"]] - X[:, bm["upper_leg_r"]]; left[:, 1] = 0
    fwd = np.cross(left, UP); yaw = yaw_of_dir(fwd)
    Q = qyaw(-yaw)
    cost = np.zeros(len(X))
    for si, ci, td, w in pairs:
        d = qrot(Q, X[:, ci] - X[:, si]); n = np.linalg.norm(d, axis=-1)
        ok = n > 1e-6
        c = np.where(ok, np.sum(d / np.maximum(n, 1e-9)[:, None] * td, -1), 1.0)
        cost += w * np.arccos(np.clip(c, -1, 1))
    return int(np.argmin(cost))


def retarget(src, mapping, skel, rest_frame=None, scale=None, relax_hands=True, facing="auto"):
    """src: dict from blender_import.py (glTF space): names, fps, R (F,S,4), X (F,S,3), Y (F,S,3),
    rest_R, rest_X, rest_Y (bind pose). mapping: {"bones": {target: source}}.
    Returns (Clip at source fps with root at origin, info)."""
    names = [norm_name(n) for n in src["names"]]
    sidx = {n: i for i, n in enumerate(names)}
    bm = {t: sidx[norm_name(s)] for t, s in mapping["bones"].items() if s and norm_name(s) in sidx and skel.has(t)}
    R = qnorm(np.asarray(src["R"], float)); X = np.asarray(src["X"], float); Y = np.asarray(src["Y"], float)
    rf = mapping.get("rest_frame") if rest_frame is None else rest_frame
    rf_neutral = rf == "neutral"
    if rf_neutral:
        rf = neutral_frame(skel, bm, X)
    if rf is None or rf == "bind":
        rR, rX, rY = qnorm(np.asarray(src["rest_R"], float)), np.asarray(src["rest_X"], float), np.asarray(src["rest_Y"], float)
    else:
        rR, rX, rY = R[int(rf)], X[int(rf)], Y[int(rf)]
    info = {"mapped": sorted(bm), "unmapped_target": [b for b in skel.names if b not in bm and not b.startswith("attach_")],
            "rest": "bind" if rf in (None, "bind") else "frame %s" % rf}
    for need in ("hips", "upper_leg_l", "upper_leg_r"):
        if need not in bm: raise ValueError("mapping must cover %s" % need)
    # 1. face +Z at rest: rotate the whole take about +Y
    left = rX[bm["upper_leg_l"]] - rX[bm["upper_leg_r"]]; left[1] = 0
    fwd = np.cross(left, UP)
    yaw0 = yaw_of_dir(fwd) if facing == "auto" else math.radians(float(facing))
    Q0 = qyaw(-yaw0)
    R = qmul(Q0, R); X = qrot(Q0, X); Y = qrot(Q0, Y); rR = qmul(Q0, rR); rX = qrot(Q0, rX); rY = qrot(Q0, rY)
    info["source_facing_deg"] = round(math.degrees(yaw0), 1)
    # 2. scale by leg length (hips height above the lowest foot joint at rest)
    feet_t = [skel.P[skel.i(b), 1] for b in ("foot_l", "foot_r", "toes_l", "toes_r") if skel.has(b)]
    feet_s = [rX[bm[b], 1] for b in ("foot_l", "foot_r", "toes_l", "toes_r") if b in bm]
    g_t, g_s = min(feet_t), min(feet_s)
    h_t = skel.P[skel.i("hips"), 1] - g_t; h_s = rX[bm["hips"], 1] - g_s
    if rf_neutral and not scale:
        # the neutral frame may be flexed (a run): hip height would inflate the scale. Use the
        # pose-invariant leg chain length (hip->knee->ankle, both sides, median over the take)
        def chain(Xs, get):
            return sum(np.linalg.norm(get(Xs, "upper_leg_" + s_) - get(Xs, "lower_leg_" + s_), axis=-1) +
                       np.linalg.norm(get(Xs, "lower_leg_" + s_) - get(Xs, "foot_" + s_), axis=-1) for s_ in "lr") / 2
        L_s = float(np.median(chain(X, lambda A, b: A[:, bm[b]])))
        L_t = float(chain(skel.P[None], lambda A, b: A[:, skel.i(b)])[0])
        k = L_t / L_s; h_s = h_t / k
    else:
        k = float(scale) if scale else h_t / h_s
    info.update(scale=k, source_hips_height=float(h_s), target_hips_height=float(h_t))
    # 3. per-bone rest alignment C (swing that turns the source rest direction onto the target's)
    # Directions are joint-to-joint (the next mapped joint down the chain that is not at the same
    # spot: CMU's Neck sits on Spine1). Leaves and hands without fingers inherit the parent's C.
    C = {}; I4 = np.array([0, 0, 0, 1.0])
    for b in range(len(skel)):
        t = skel.names[b]
        if t not in bm: continue
        if t in ("root", "hips") or t in mapping.get("world_rest", ["head"]):
            # the head looks forward in any sane rest pose: keep its world orientation delta as-is
            # (inheriting the neck's swing would tilt the gaze by the source's neck posture)
            C[t] = I4; continue
        si = bm[t]; ch = TARGET_CHILD.get(t); found = None
        while ch:
            if ch in bm and np.linalg.norm(rX[bm[ch]] - rX[si]) > 0.02 * h_s: found = ch; break
            ch = TARGET_CHILD.get(ch)
        if found:
            C[t] = qbetween(rX[bm[found]] - rX[si], skel.P[skel.i(found)] - skel.P[b])
        else:
            p = skel.parent[b]
            while p >= 0 and skel.names[p] not in C: p = skel.parent[p]
            C[t] = C[skel.names[p]] if p >= 0 else I4
    info["rest_alignment_deg"] = {t: round(math.degrees(float(qangle(q))), 1) for t, q in C.items() if qangle(q) > 0.05}
    # 4. canonical world rotations
    F, B = len(R), len(skel)
    Wc = np.tile(np.array([0, 0, 0, 1.0]), (F, B, 1))
    for b in range(B):
        t = skel.names[b]; p = skel.parent[b]
        if t in bm:
            si = bm[t]
            Wc[:, b] = qmul(qmul(R[:, si], qconj(rR[si])), qconj(C[t]))
        elif p >= 0:
            Wc[:, b] = Wc[:, p]
    Wc[:, skel.i("root")] = np.array([0, 0, 0, 1.0])
    if relax_hands:
        for side in "lr":
            if not any(("%s_01_%s" % (f, side)) in bm for f in RELAXED_HAND_DEG): finger_curl(skel, Wc, side)
    ql = locals_from_world(skel, Wc)
    # 5. hips position (world == root frame, root at origin for now)
    hx = X[:, bm["hips"]].copy(); hx[:, 1] -= g_s; hx *= k; hx[:, 1] += g_t
    root_p = np.zeros((F, 3))
    return Clip(src["fps"], ql, root_p, hx, meta={"retarget": info}), info


# --------------------------------------------------------------------------- resample / trim

def resample(clip, fps):
    if abs(clip.fps - fps) < 1e-6: return clip.copy()
    n = int(math.floor(clip.duration * fps + 1e-6)) + 1
    t = np.arange(n) / fps * clip.fps
    i0 = np.clip(np.floor(t).astype(int), 0, clip.F - 1); i1 = np.clip(i0 + 1, 0, clip.F - 1); u = t - i0
    ql = qcontinuous(clip.ql)
    q = qslerp(ql[i0], ql[i1], np.broadcast_to(u[:, None], ql[i0].shape[:-1]))
    lerp = lambda a: a[i0] + (a[i1] - a[i0]) * u[:, None]
    return Clip(fps, q, lerp(clip.root_p), lerp(clip.hips_d), clip.name, clip.meta)


def gauss_smooth(x, sigma, mode="reflect"):
    if sigma <= 0: return np.array(x, float)
    x = np.asarray(x, float); r = int(math.ceil(3 * sigma)); k = np.exp(-0.5 * (np.arange(-r, r + 1) / sigma) ** 2); k /= k.sum()
    pad = np.pad(x, [(r, r)] + [(0, 0)] * (x.ndim - 1), mode=mode)
    out = np.zeros_like(x)
    for j, w in enumerate(k): out += w * pad[j:j + len(x)]
    return out


# --------------------------------------------------------------------------- root motion

def facing_yaw(skel, P):
    """Body facing from hip and shoulder lines (left joint minus right joint), unwrapped."""
    v = np.zeros((len(P), 3))
    for l, r, w in (("upper_leg_l", "upper_leg_r", 1.0), ("upper_arm_l", "upper_arm_r", 0.5)):
        if skel.has(l) and skel.has(r): v += w * (P[:, skel.i(l)] - P[:, skel.i(r)])
    v[:, 1] = 0; f = np.cross(v, UP)
    return np.unwrap(yaw_of_dir(f))


def extract_root(skel, clip, root_motion=True, yaw_mode="constant", smooth_s=0.4, keep_height=False):
    """Move ground-projected hips translation + yaw onto `root`; hips become relative to root.
    yaw_mode: constant (clip mean facing; straight locomotion, strafes), facing (smoothed body
    facing; turns, plant-turns), none (root never rotates). Starts at the origin facing +Z."""
    Wc, P = fk(skel, clip)
    h = skel.i("hips"); F = clip.F
    hp = P[:, h].copy()
    yaw = facing_yaw(skel, P)
    sig = smooth_s * clip.fps / 2.0
    if yaw_mode == "facing": yaw_s = gauss_smooth(yaw, sig, "edge")
    elif yaw_mode == "none": yaw_s = np.zeros(F)
    else: yaw_s = np.full(F, float(np.mean(yaw)))
    if root_motion:
        rp = gauss_smooth(hp, sig, "edge"); rp[:, 1] = 0
    else:
        rp = np.zeros((F, 3)); rp[:] = np.mean(hp, 0) * np.array([1, 0, 1])
    # normalise: start at origin facing +Z
    y0 = yaw_s[0]; Q0 = qyaw(-y0); o = rp[0].copy()
    rp = qrot(Q0, rp - o); yaw_s = yaw_s - y0
    hpn = qrot(Q0, hp - o)
    Wn = qmul(Q0, Wc)
    ry = qyaw(yaw_s)
    ql = locals_from_world(skel, Wn)
    ql[:, skel.i("root")] = ry
    ql[:, h] = qmul(qconj(ry), Wn[:, h])
    hd = qrot(qconj(ry), hpn - rp)
    if not root_motion: rp = np.zeros((F, 3)); ql[:, skel.i("root")] = np.array([0, 0, 0, 1.0])
    out = Clip(clip.fps, ql, rp, hd, clip.name, clip.meta)
    out.meta["root"] = {"yaw_mode": yaw_mode, "smooth_s": smooth_s, "root_motion": bool(root_motion)}
    return out


def renormalize(clip, skel):
    """Root at the origin facing +Z on frame 0 (hips stay relative to root)."""
    r = skel.i("root"); q0 = clip.ql[0, r]
    y0 = 2 * math.atan2(q0[1], q0[3])
    Q = qyaw(-y0); out = clip.copy()
    out.root_p = qrot(Q, clip.root_p - clip.root_p[0]); out.root_p[:, 1] = 0
    out.ql[:, r] = qmul(Q[None], clip.ql[:, r])
    return out


def calibrate_foot_pitch(skel, clip, max_deg=25.0):
    """Mocap T-poses rarely hold the feet flat, so the rest alignment can leave the target's
    feet pitched (toes floating or digging) all through stance. Measure the mean ankle->ball
    pitch over planted frames (low, slow ankle) and rotate each foot by the constant
    difference to the target's rest pitch. Returns (clip, {side: correction_deg})."""
    Wc, P = fk(skel, clip); out = clip.copy(); corr = {}
    for s in "lr":
        fb, tb = skel.i("foot_" + s), skel.i("toes_" + s)
        v0 = skel.P[tb] - skel.P[fb]
        v = P[:, tb] - P[:, fb]
        ank = P[:, fb]; sp = np.linalg.norm(np.gradient(ank[:, [0, 2]], axis=0), axis=-1) * clip.fps
        h = ank[:, 1]; planted = (sp < 0.35) & (h < np.percentile(h, 40))
        if planted.sum() < 3: corr[s] = 0.0; continue
        pitch = lambda x: np.degrees(np.arctan2(x[..., 1], np.linalg.norm(x[..., [0, 2]], axis=-1)))
        d = float(np.median(pitch(v[planted])) - pitch(v0))
        d = float(np.clip(d, -max_deg, max_deg)); corr[s] = round(d, 2)
        a = np.cross(v0, UP); a /= np.linalg.norm(a) + 1e-12
        R = qaxis(a, math.radians(-d))
        Wn = Wc.copy(); Wn[:, fb] = qmul(Wc[:, fb], R)
        out.ql[:, fb] = qmul(qconj(Wc[:, skel.parent[fb]]), Wn[:, fb])
        out.ql[:, tb] = qmul(qconj(Wn[:, fb]), Wc[:, tb])
    return out, corr


def ground_calibrate(skel, clip, pct=25.0):
    """Shift the take vertically so the feet meet the ground where they plant (source
    ground planes rarely match: CMU's inserted T-pose frame sits higher than the walk).
    Uses the pct-th percentile of the lowest foot point per frame. Returns (clip, offset m)."""
    Wc, P = fk(skel, clip); tr = foot_tracks(skel, clip, Wc, P)
    low = np.min(np.stack([x[:, 1] - h0 for (x, h0) in tr.values()]), 0)
    off = float(np.percentile(low, pct))
    out = clip.copy(); out.hips_d[:, 1] -= off
    return out, off


def ground_calibrate_plane(skel, clip, pct=40.0):
    """Like ground_calibrate, for capture volumes whose floor is not level (ACCAD: hips rise
    ~6 cm over a 6 m walk). Fits the lowest foot point on low (planted) frames as a plane over
    the horizontal hips position and removes it. Returns (clip, {"slope_cm_per_m", "offset_cm"})."""
    Wc, P = fk(skel, clip); tr = foot_tracks(skel, clip, Wc, P)
    low = np.min(np.stack([x[:, 1] - h0 for (x, h0) in tr.values()]), 0)
    hp = P[:, skel.i("hips")]
    sel = low <= np.percentile(low, pct)
    A = np.stack([hp[sel, 0], hp[sel, 2], np.ones(sel.sum())], -1)
    if sel.sum() < 10 or np.ptp(hp[:, [0, 2]], axis=0).max() < 1.0:   # too little travel: constant offset
        out, off = ground_calibrate(skel, clip); return out, {"slope_cm_per_m": 0.0, "offset_cm": round(off * 100, 2)}
    coef, *_ = np.linalg.lstsq(A, low[sel], rcond=None)
    plane = hp[:, 0] * coef[0] + hp[:, 2] * coef[1] + coef[2]
    out = clip.copy(); out.hips_d[:, 1] -= plane
    return out, {"slope_cm_per_m": round(float(np.hypot(coef[0], coef[1])) * 100, 2), "offset_cm": round(float(coef[2]) * 100, 2)}


# --------------------------------------------------------------------------- loops

def pose_features(skel, clip, bones=("hips", "spine", "chest", "neck", "upper_arm_l", "upper_arm_r", "lower_arm_l", "lower_arm_r",
                                     "upper_leg_l", "upper_leg_r", "lower_leg_l", "lower_leg_r", "foot_l", "foot_r")):
    """Joint positions relative to root in the root frame (m) + their per-frame velocity."""
    Wc, P = fk(skel, clip); r = skel.i("root")
    rel = qrot(qconj(Wc[:, r])[:, None], P - P[:, r:r + 1])
    ids = [skel.i(b) for b in bones if skel.has(b)]
    pos = rel[:, ids]; vel = np.gradient(pos, axis=0) * clip.fps
    return pos, vel


def find_cycles(skel, clip, period_s=(0.8, 1.6), window=None, top=5, vel_w=0.1):
    """Candidate loop windows (a, b): pose(b) ~ pose(a) with matching velocity.
    Returns [(cost, a, b)] sorted, where cost is RMS joint error in cm."""
    pos, vel = pose_features(skel, clip)
    F = clip.F; lo, hi = int(period_s[0] * clip.fps), int(period_s[1] * clip.fps)
    a0, a1 = (0, F - 1) if window is None else (int(window[0] * clip.fps), min(F - 1, int(window[1] * clip.fps)))
    cands = []
    for a in range(a0, a1):
        for b in range(a + lo, min(a1, a + hi) + 1):
            e = np.sqrt(np.mean(np.sum((pos[b] - pos[a]) ** 2, -1))) + vel_w * np.sqrt(np.mean(np.sum((vel[b] - vel[a]) ** 2, -1)))
            cands.append((e * 100, a, b))
    cands.sort()
    out = []
    for c in cands:   # distinct windows only
        if all(abs(c[1] - o[1]) > 3 or abs(c[2] - o[2]) > 3 for o in out): out.append(c)
        if len(out) >= top: break
    return out


def blend_loop(clip, a, b, blend_frames=8):
    """Frames a..b of `clip` as a seamless loop: last frame == first frame, no velocity pop.
    The last `blend_frames` crossfade toward the frames that precede `a` in the take, so the
    seam carries the take's own motion into frame a. Falls back to spreading the residual
    evenly when there aren't enough frames before `a`."""
    P = b - a; K = min(blend_frames, P // 3)
    ql = qcontinuous(clip.ql)
    seg = clip.slice(a, b); q = qcontinuous(seg.ql).copy(); hd = seg.hips_d.copy()
    v = np.diff(clip.root_p, axis=0)
    if a - K >= 0 and K > 0:
        for j in range(K + 1):
            t = b - K + j; s = j / K; s = s * s * (3 - 2 * s)
            q[t - a] = qslerp(ql[t], ql[t - P], np.full(ql.shape[1], s))
            hd[t - a] = hd[t - a] * (1 - s) + clip.hips_d[t - P] * s
        vv = v[a:b].copy()
        for j in range(K):
            t = b - K + j; s = (j + 0.5) / K; s = s * s * (3 - 2 * s)
            vv[t - a] = v[t] * (1 - s) + v[t - P] * s
        mode = "crossfade"
    else:
        d = qmul(qconj(q[-1]), q[0]); u = np.linspace(0, 1, P + 1)
        for i in range(P + 1):
            q[i] = qmul(q[i], qslerp(np.array([0, 0, 0, 1.0]) * np.ones_like(d), d, np.full(d.shape[0], u[i])))
        hd = hd + (hd[0] - hd[-1]) * u[:, None]
        vv = v[a:b]; mode = "spread"
    q[-1] = q[0]; hd[-1] = hd[0]
    rp = np.zeros((P + 1, 3)); rp[1:] = np.cumsum(vv, 0); rp += clip.root_p[a] - rp[0]
    out = Clip(clip.fps, q, rp, hd, clip.name, clip.meta); out.meta["loop"] = {"a": int(a), "b": int(b), "mode": mode, "blend_frames": int(K)}
    return out


# --------------------------------------------------------------------------- feet

def foot_points(skel, side):
    """Rest-space contact points of one foot: heel, ball, toe tip (world rest coords)."""
    f, t = skel.P[skel.i("foot_" + side)], skel.P[skel.i("toes_" + side)]
    ground = min(skel.P[skel.i("toes_" + side), 1], f[1]) - 0.005
    heel = np.array([f[0], ground + 0.012, f[2] - 0.035])
    tip = t + skel.axis("toes_" + side) * max(0.04, skel.blen[skel.i("toes_" + side)])
    return {"heel": ("foot_" + side, heel), "ball": ("toes_" + side, t.copy()), "tip": ("toes_" + side, tip)}


def foot_tracks(skel, clip, Wc=None, P=None):
    if Wc is None: Wc, P = fk(skel, clip)
    out = {}
    for s in "lr":
        for k, (bone, rp) in foot_points(skel, s).items():
            out[(s, k)] = (world_point(skel, Wc, P, bone, rp), rp[1])
    return out


def detect_contacts(skel, clip, h_tol=0.035, v_max=0.45, min_len=3, Wc=None, P=None):
    """Per foot: boolean contact per frame + spans. A point is in contact when it is within
    h_tol of its rest height and moving slower than v_max (m/s, horizontal)."""
    tr = foot_tracks(skel, clip, Wc, P); res = {}
    for s in "lr":
        pts = {}
        for k in ("heel", "ball"):
            x, h0 = tr[(s, k)]
            sp = np.linalg.norm(np.gradient(x[:, [0, 2]], axis=0), axis=-1) * clip.fps
            pts[k] = (x[:, 1] < h0 + h_tol) & (sp < v_max)
        c = pts["heel"] | pts["ball"]
        c = _clean_bool(c, min_len)
        res[s] = {"contact": c, "heel": pts["heel"] & c, "ball": pts["ball"] & c, "spans": spans(c)}
    return res, tr


def _clean_bool(c, min_len):
    c = c.copy()
    for a, b in spans(~c):          # fill 1-2 frame gaps
        if b - a + 1 <= 2 and a > 0 and b < len(c) - 1: c[a:b + 1] = True
    for a, b in spans(c):
        if b - a + 1 < min_len: c[a:b + 1] = False
    return c


def spans(c):
    out, a = [], None
    for i, v in enumerate(c):
        if v and a is None: a = i
        if not v and a is not None: out.append((a, i - 1)); a = None
    if a is not None: out.append((a, len(c) - 1))
    return out


PIVOT_DEG = 15.0


def slide_per_step(tr, contacts):
    """Horizontal drift (m) of the planted point during each contact span, per foot.
    Normally the worst of heel and ball. When the foot yaws more than PIVOT_DEG inside the
    span it is a pivot (turn in place, plant-turn): the foot rotates about the point that
    stays planted, the other end sweeps an arc that is not slide, so the least-moving planted
    point is measured instead (a foot that skates still moves both points)."""
    out = {}
    for s in "lr":
        vals = []
        hv = tr[(s, "ball")][0] - tr[(s, "heel")][0]
        yaw = np.unwrap(np.arctan2(hv[:, 0], hv[:, 2]))
        for a, b in contacts[s]["spans"]:
            ds = []
            for k in ("heel", "ball"):
                m = contacts[s][k][a:b + 1]
                if m.sum() < 2: continue
                x = tr[(s, k)][0][a:b + 1][m][:, [0, 2]]
                ds.append(float(np.max(np.linalg.norm(x - x[0], axis=-1))))
            pivot = np.degrees(np.ptp(yaw[a:b + 1])) > PIVOT_DEG
            d = (min(ds) if pivot else max(ds)) if ds else 0.0
            vals.append(((a, b), d))
        out[s] = vals
    return out


def two_bone_ik(Wc, P, skel, side, target_ankle, frames):
    """Solve hip-knee-ankle so the ankle reaches target (keeps knee plane, keeps foot world rotation)."""
    ul, ll, ft = skel.i("upper_leg_" + side), skel.i("lower_leg_" + side), skel.i("foot_" + side)
    l1 = np.linalg.norm(skel.P[ll] - skel.P[ul]); l2 = np.linalg.norm(skel.P[ft] - skel.P[ll])
    for f in frames:
        H, K, A, T = P[f, ul], P[f, ll], P[f, ft], target_ankle[f]
        ht = T - H; d = np.clip(np.linalg.norm(ht), abs(l1 - l2) + 1e-4, l1 + l2 - 1e-4)
        u = ht / (np.linalg.norm(ht) + 1e-12)
        # knees bend forward: the pole is the thigh's rest-forward axis, never the current knee
        # (a slightly hyperextended source knee would otherwise flip the solve backwards)
        pole = qrot(Wc[f, ul], np.array([0.0, 0.0, 1.0])); pole = pole - np.dot(pole, u) * u
        if np.linalg.norm(pole) < 1e-6: continue
        pole /= np.linalg.norm(pole)
        a = (l1 * l1 - l2 * l2 + d * d) / (2 * d); hgt = math.sqrt(max(l1 * l1 - a * a, 0))
        K2 = H + u * a + pole * hgt; T2 = H + u * d
        r1 = qbetween(K - H, K2 - H)
        A1 = K2 + qrot(r1, A - K)
        r2 = qbetween(A1 - K2, T2 - K2)
        foot_w = Wc[f, ft].copy()
        Wc[f, ul] = qmul(r1, Wc[f, ul]); Wc[f, ll] = qmul(r2, qmul(r1, Wc[f, ll]))
        Wc[f, ft] = foot_w
        P[f, ll] = K2; P[f, ft] = T2


def foot_lock(skel, clip, contacts=None, blend=4, ground=True, lock=True, strength=1.0):
    """Reduce foot slide (pin planted points at their span mean) and lift feet out of the ground,
    with 2-bone IK on the legs. Pure FK output: no IK is baked into the file."""
    Wc, P = fk(skel, clip)
    if contacts is None: contacts, tr = detect_contacts(skel, clip, Wc=Wc, P=P)
    else: tr = foot_tracks(skel, clip, Wc, P)
    F = clip.F; report = {}
    for s in "lr":
        off = np.zeros((F, 3)); w = np.zeros(F)
        if lock:
            for a, b in contacts[s]["spans"]:
                for f in range(a, b + 1):
                    k = "ball" if contacts[s]["ball"][f] else "heel"
                    m = contacts[s][k][a:b + 1]
                    anchor = tr[(s, k)][0][a:b + 1][m].mean(0) if m.any() else tr[(s, k)][0][f]
                    off[f, [0, 2]] = (anchor - tr[(s, k)][0][f])[[0, 2]]; w[f] = 1
                for j in range(1, blend + 1):        # ease in/out around the span
                    e = 1 - j / (blend + 1); e = e * e * (3 - 2 * e)
                    for f, src in ((a - j, a), (b + j, b)):
                        if 0 <= f < F and w[f] < e: off[f] = off[src] * e; w[f] = e
        off *= strength
        if ground:
            low = np.min(np.stack([tr[(s, k)][0][:, 1] - tr[(s, k)][1] for k in ("heel", "ball", "tip")]), 0)
            off[:, 1] = np.maximum(0, -low)
        frames = [f for f in range(F) if np.linalg.norm(off[f]) > 1e-5]
        tgt = P[:, skel.i("foot_" + s)] + off
        two_bone_ik(Wc, P, skel, s, tgt, frames)
        report[s] = {"frames_adjusted": len(frames), "max_offset_cm": round(float(np.max(np.linalg.norm(off, axis=-1)) * 100), 2)}
    ql = locals_from_world(skel, Wc)
    ql[:, skel.i("root")] = clip.ql[:, skel.i("root")]
    out = Clip(clip.fps, ql, clip.root_p, clip.hips_d, clip.name, clip.meta); out.meta["foot_lock"] = report
    return out


def tile_loop(clip, n=3):
    """Repeat a loop (last frame == first) n times, root motion continuing, for cyclic processing."""
    body = slice(0, clip.F - 1); D = clip.root_p[-1] - clip.root_p[0]
    ql = np.concatenate([clip.ql[body]] * n + [clip.ql[-1:]], 0)
    hd = np.concatenate([clip.hips_d[body]] * n + [clip.hips_d[-1:]], 0)
    rp = np.concatenate([clip.root_p[body] + D * k for k in range(n)] + [clip.root_p[-1:] + D * (n - 1)], 0)
    return Clip(clip.fps, ql, rp, hd, clip.name, clip.meta)


def separate_legs(skel, clip, gap=0.11, iters=4, sigma=1.5, cyclic=True):
    """Keep the shins from passing through each other in synthesized cycles: where the
    knee-ankle segments come closer than `gap` (m), push the ankles apart along the root's
    lateral axis by the smoothed deficit (half each side) with 2-bone leg IK. Run before foot
    lock (plants move with it). Returns (clip, max push cm)."""
    out = clip.copy(); total = 0.0
    for _ in range(iters):
        Wc, P = fk(skel, out)
        d = _seg_min_dist(P[:, skel.i("lower_leg_l")], P[:, skel.i("foot_l")], P[:, skel.i("lower_leg_r")], P[:, skel.i("foot_r")])
        deficit = np.maximum(0.0, gap - d)
        if deficit.max() < 0.002: break
        deficit = gauss_smooth(deficit, sigma, "wrap" if cyclic else "edge") * 1.3
        lat = qrot(Wc[:, skel.i("root")], np.array([1.0, 0, 0]))          # character's left
        for x, sg in (("l", 1.0), ("r", -1.0)):
            tgt = P[:, skel.i("foot_" + x)] + lat * (sg * deficit / 2)[:, None]
            two_bone_ik(Wc, P, skel, x, tgt, [f for f in range(clip.F) if deficit[f] > 1e-4])
        ql = locals_from_world(skel, Wc); ql[:, skel.i("root")] = out.ql[:, skel.i("root")]
        out = Clip(out.fps, ql, out.root_p, out.hips_d, out.name, out.meta)
        if cyclic: out.ql[-1] = out.ql[0]
        total += float(deficit.max())
    return out, round(total * 100, 2)


def separate_arms(skel, clip, bodies, torso=("spine", "pelvis"), allow=0.02, sigma=2.0, max_deg=35.0, step_deg=5.0, iters=3):
    """Opt-in (clip option `separate_arms`): keep the forearms out of the torso on bodies wider
    than the performer. `bodies` = ragdoll bodies (char_*.ragdoll.json). Where a lower_arm
    capsule sinks into a torso capsule more than `allow` (m) beyond its rest overlap: first the
    arm is swivelled about the shoulder->wrist axis (the wrist stays put, so hand-to-mouth and
    hand-to-ear contacts survive; the hand turns with the arm so its own twist is unchanged) by
    the smallest angle up to `max_deg` that clears it; what is left is cleared by moving the
    wrist out of the torso with 2-bone arm IK (elbow pole = current elbow). Both smoothed over
    time. Returns (clip, {side: [max swivel deg, max wrist push cm]})."""
    caps = {b["name"]: b for b in bodies}
    def cap(Wc, P, n):
        b = caps[n]; c = b["capsule"]
        return world_point(skel, Wc, P, b["bone"], c["a"]), world_point(skel, Wc, P, b["bone"], c["b"]), c["radius"]
    def seg_closest(p1, q1, p2, q2, n=12):     # closest points (sampled on the first segment, exact on the second)
        t = np.linspace(0, 1, n)[None, :, None]; X = p1[:, None] + (q1 - p1)[:, None] * t
        d = q2 - p2; e = np.maximum((d * d).sum(-1), 1e-12)[:, None]
        u = np.clip(((X - p2[:, None]) * d[:, None]).sum(-1) / e, 0, 1)[..., None]; Y = p2[:, None] + d[:, None] * u
        dist = np.linalg.norm(X - Y, axis=-1); k = np.argmin(dist, 1); r = np.arange(len(k))
        return X[r, k], Y[r, k], dist[r, k]
    def smooth_peaks(x):
        k = int(3 * sigma); pad = np.pad(x, k, mode="edge")
        mx = np.array([pad[i:i + 2 * k + 1][np.argmax(np.abs(pad[i:i + 2 * k + 1]))] for i in range(len(x))])
        return gauss_smooth(mx, sigma, "edge")
    rest = Clip(clip.fps, np.tile(np.array([0, 0, 0, 1.0]), (1, len(skel), 1)), np.zeros((1, 3)), (skel.P[skel.i("hips")] - skel.P[skel.i("root")])[None])
    rW, rP = fk(skel, rest)
    Wc, P = fk(skel, clip); rep = {}
    angs = np.radians(np.concatenate([[0.0], np.ravel([[a, -a] for a in np.arange(step_deg, max_deg + 1e-6, step_deg)])]))
    for s in "lr":
        la = "lower_arm_" + s
        if la not in caps or not all(t in caps for t in torso): continue
        ua, lo, hd = skel.i("upper_arm_" + s), skel.i(la), skel.i("hand_" + s)
        chain = [ua] + _descendants(skel, ua)
        ra, rb, rr = cap(rW, rP, la)
        rest_pen = {t: max(0.0, float(rr + cap(rW, rP, t)[2] - _seg_min_dist(ra, rb, *cap(rW, rP, t)[:2])[0])) for t in torso}
        def worst(Wc, P, R=None):
            S = P[:, ua]; a0, b0, r0 = cap(Wc, P, la)
            if R is not None: a0 = S + qrot(R, a0 - S); b0 = S + qrot(R, b0 - S)
            best = None
            for t in torso:
                ta, tb, tr = cap(Wc, P, t); X, Y, d = seg_closest(a0, b0, ta, tb)
                ex = r0 + tr - d - rest_pen[t]
                if best is None: best = (ex, X - Y)
                else:
                    m = ex > best[0]; best = (np.where(m, ex, best[0]), np.where(m[:, None], X - Y, best[1]))
            return best
        swiv, push = 0.0, 0.0
        u = P[:, hd] - P[:, ua]; u /= np.linalg.norm(u, axis=-1, keepdims=True) + 1e-12
        ex0, _ = worst(Wc, P)
        if (ex0 > allow).any():
            ex_by = np.stack([worst(Wc, P, qaxis(u, np.full(clip.F, a)))[0] for a in angs])
            th = np.zeros(clip.F)
            for f in np.nonzero(ex0 > allow)[0]:
                ok = np.nonzero(ex_by[:, f] <= allow)[0]
                th[f] = angs[ok[0]] if len(ok) else angs[int(np.argmin(ex_by[:, f]))]
            th = smooth_peaks(th); R = qaxis(u, th); S = P[:, ua].copy()
            for j in chain:
                Wc[:, j] = qmul(R, Wc[:, j]); P[:, j] = S + qrot(R, P[:, j] - S)
            swiv = float(np.degrees(np.max(np.abs(th))))
        l1 = np.linalg.norm(skel.P[lo] - skel.P[ua]); l2 = np.linalg.norm(skel.P[hd] - skel.P[lo])
        for _ in range(iters):
            ex, n = worst(Wc, P)
            deficit = smooth_peaks(np.maximum(0.0, ex - allow + 0.005))
            if deficit.max() < 1e-3: break
            n = n / (np.linalg.norm(n, axis=-1, keepdims=True) + 1e-12)
            push += float(deficit.max())
            for f in np.nonzero(deficit > 1e-4)[0]:
                S, E, W = P[f, ua], P[f, lo], P[f, hd]; T = W + n[f] * deficit[f]
                st = T - S; d = np.clip(np.linalg.norm(st), abs(l1 - l2) + 1e-4, l1 + l2 - 1e-4); uu = st / (np.linalg.norm(st) + 1e-12)
                pole = (E - S) - np.dot(E - S, uu) * uu
                if np.linalg.norm(pole) < 1e-6: continue
                pole /= np.linalg.norm(pole)
                aa = (l1 * l1 - l2 * l2 + d * d) / (2 * d); hh = math.sqrt(max(l1 * l1 - aa * aa, 0))
                E2 = S + uu * aa + pole * hh; T2 = S + uu * d
                r1 = qbetween(E - S, E2 - S); W1 = E2 + qrot(r1, W - E); r2 = qbetween(W1 - E2, T2 - E2)
                Wc[f, ua] = qmul(r1, Wc[f, ua]); Wc[f, lo] = qmul(r2, qmul(r1, Wc[f, lo]))
                P[f, lo] = E2; dW = T2 - W
                for j in chain:
                    if j in (ua, lo): continue
                    P[f, j] = P[f, j] + dW          # hand and fingers translate, keep world rotation
        rep[s] = [round(swiv, 1), round(push * 100, 1)]
    ql = locals_from_world(skel, Wc); ql[:, skel.i("root")] = clip.ql[:, skel.i("root")]
    out = Clip(clip.fps, ql, clip.root_p, clip.hips_d, clip.name, clip.meta)
    return out, rep


def forearm_twist(skel, clip, frac=0.5):
    """Opt-in (clip option `forearm_twist`): sources whose forearm has no twist channel (CMU
    radius is a 1-DOF hinge) put all pronation/supination on the wrist, which reads as a
    candy-wrapper wrist on a skeleton without twist bones. Move `frac` of the hand's twist about
    the forearm axis onto lower_arm; the hand's world rotation is unchanged. Returns
    (clip, {side: max hand twist moved, deg})."""
    out = clip.copy(); rep = {}
    for s in "lr":
        lo, hd = skel.i("lower_arm_" + s), skel.i("hand_" + s)
        ax = skel.axis("lower_arm_" + s)
        _, tw = swing_twist(out.ql[:, hd], skel.axis("hand_" + s))
        T = qaxis(np.broadcast_to(ax, (clip.F, 3)), frac * tw)
        out.ql[:, lo] = qnorm(qmul(out.ql[:, lo], T)); out.ql[:, hd] = qnorm(qmul(qconj(T), out.ql[:, hd]))
        rep[s] = round(float(np.degrees(np.max(np.abs(frac * tw)))), 1)
    return out, rep


def stance_contacts(stance):
    """Contacts dict (as detect_contacts returns) from given per-foot stance masks; the ball
    is the anchor through the whole stance (heel strike rolls about it)."""
    out = {}
    for x in "lr":
        c = np.asarray(stance[x], bool)
        out[x] = {"contact": c, "heel": np.zeros_like(c), "ball": c.copy(), "spans": spans(c)}
    return out


def foot_lock_loop(skel, clip, stance=None, **kw):
    """Foot lock on a loop without breaking its seam: lock the middle copy of a 3x tiling
    (contacts that cross the seam are whole there), then cut it back out. `stance` (per-foot
    masks over the loop's frames) overrides contact detection (synthesized cycles)."""
    N = clip.F - 1
    if stance is not None:
        st = {x: np.concatenate([np.asarray(stance[x], bool)[:N]] * 3 + [np.asarray(stance[x], bool)[:1]]) for x in "lr"}
        kw = dict(kw, contacts=stance_contacts(st))
    t = foot_lock(skel, tile_loop(clip, 3), **kw)
    out = t.slice(N, 2 * N); out.root_p = clip.root_p.copy(); out.ql[:, skel.i("root")] = clip.ql[:, skel.i("root")]
    out.ql[-1] = out.ql[0]; out.hips_d[-1] = out.hips_d[0]
    out.meta = dict(clip.meta, foot_lock_loop=t.meta.get("foot_lock"))
    return out


def align_travel(skel, clip, direction):
    """Rotate the whole motion (root path and body) about the start so the clip's net travel
    lies exactly on the named direction axis, while the root keeps facing +Z: the body then
    faces off +Z by the correction relative to root. For strafes whose performer drifted
    diagonally (ACCAD side steps travel ~20 deg off lateral) -- the engine drives the root
    along the axis, so the travel must be on it. -> (clip, correction deg)."""
    d = clip.root_p[-1] - clip.root_p[0]
    if np.linalg.norm(d[[0, 2]]) < 0.05: return clip, 0.0
    want = math.atan2(*DIR_VEC[direction]); have = math.atan2(d[0], d[2])
    err = (want - have + math.pi) % (2 * math.pi) - math.pi
    Q = qyaw(err); out = clip.copy()
    out.root_p = qrot(Q[None], clip.root_p - clip.root_p[0]) + clip.root_p[0]
    h = skel.i("hips")          # root yaw stays; the body turns with the path (no foot slide)
    out.ql[:, h] = qmul(Q[None], clip.ql[:, h])
    out.hips_d = qrot(Q[None], clip.hips_d)
    return out, math.degrees(err)


def level_head(skel, clip, target_deg=0.0, max_deg=30.0):
    """Posture fix for performers who watched their feet (ACCAD side steps, some CMU idles):
    remove the clip's MEAN head pitch (gaze up/down relative to the root's forward, measured on
    the head's rest-forward axis) with a constant correction split between neck and head.
    Motion around the mean is kept. Returns (clip, correction deg)."""
    Wc, P = fk(skel, clip); r, n, h = skel.i("root"), skel.i("neck"), skel.i("head")
    f = qrot(qconj(Wc[:, r]), qrot(Wc[:, h], np.array([0, 0, 1.0])))       # head forward in root frame
    pitch = np.degrees(np.arctan2(-f[:, 1], np.hypot(f[:, 0], f[:, 2])))       # + = looking down
    corr = float(np.clip(np.mean(pitch) - target_deg, -max_deg, max_deg))
    out = clip.copy(); axis_w = qrot(Wc[:, r], np.array([1.0, 0, 0]))           # character's left axis
    for b, part in ((n, 0.5), (h, 0.5)):
        Wc, P = fk(skel, out)
        R = np.stack([qaxis(a, math.radians(-corr * part)) for a in axis_w])
        Wn = qmul(R, Wc[:, b]); p = skel.parent[b]
        out.ql[:, b] = qmul(qconj(Wc[:, p]), Wn)
    return out, corr


def torso_pitch(skel, clip, bones=("hips", "spine", "chest", "upper_chest"), Wc=None):
    """Per-frame pitch (deg, + = toward the root's forward) of each bone's rest-up axis in the root frame.
    World-aligned clips have identity rest rotations, so 0 = the bind posture."""
    if Wc is None: Wc, _ = fk(skel, clip)
    r = skel.i("root"); out = {}
    for b in bones:
        if not skel.has(b): continue
        u = qrot(qmul(qconj(Wc[:, r]), Wc[:, skel.i(b)]), np.array([0, 1.0, 0]))
        out[b] = np.degrees(np.arctan2(u[:, 2], u[:, 1]))
    return out


def level_torso(skel, clip, targets, keep_world=("upper_leg_l", "upper_leg_r", "upper_arm_l", "upper_arm_r"), max_deg=35.0):
    """Posture fix for a back-arched (or hunched) torso inherited from the performer (100STYLE ArmsAboveHead:
    pelvis and lumbar leaning back under raised arms). For each bone in `targets` (root -> tip order,
    {bone: target mean pitch deg}) a CONSTANT rotation about the character's left axis moves that bone's
    mean pitch (torso_pitch) to the target; motion around the mean is kept, so loops stay closed.
    Bones in `keep_world` keep their world orientation (legs stay planted, raised arms stay where they
    point). Returns (clip, {bone: correction deg})."""
    Wc0, _ = fk(skel, clip); r = skel.i("root")
    keep = [(skel.i(b), Wc0[:, skel.i(b)].copy()) for b in keep_world if skel.has(b)]
    out = clip.copy(); corr = {}
    for b, tgt in targets.items():
        if not skel.has(b): continue
        Wc, _ = fk(skel, out); bi = skel.i(b)
        c = float(np.clip(np.mean(torso_pitch(skel, out, (b,), Wc)[b]) - float(tgt), -max_deg, max_deg)); corr[b] = round(c, 2)
        axis_w = qrot(Wc[:, r], np.array([1.0, 0, 0]))
        R = np.stack([qaxis(a, math.radians(-c)) for a in axis_w])
        out.ql[:, bi] = qmul(qconj(Wc[:, skel.parent[bi]]), qmul(R, Wc[:, bi]))
    for bi, Wk in keep:
        Wc, _ = fk(skel, out)
        out.ql[:, bi] = qmul(qconj(Wc[:, skel.parent[bi]]), Wk)
    return out, corr


def recenter_in_place(skel, clip):
    """In-place clips: hips centred over the root on average (the clip stands on the origin)."""
    d_rest = skel.P[skel.i("hips")] - skel.P[skel.i("root")]
    out = clip.copy(); m = out.hips_d[:, [0, 2]].mean(0) - d_rest[[0, 2]]
    out.hips_d[:, 0] -= m[0]; out.hips_d[:, 2] -= m[1]
    return out


# --------------------------------------------------------------------------- cycle blending (diagonals)

def qlog(q):
    """Unit quaternion -> rotation vector (axis * angle), shortest arc."""
    q = qnorm(q); q = np.where(q[..., 3:4] < 0, -q, q)
    v = q[..., :3]; s = np.linalg.norm(v, axis=-1, keepdims=True)
    ang = 2 * np.arctan2(s, q[..., 3:4])
    return np.where(s > 1e-9, v / np.maximum(s, 1e-12) * ang, 2 * v)


def qexp(r):
    ang = np.linalg.norm(r, axis=-1, keepdims=True)
    ax = np.where(ang > 1e-9, r / np.maximum(ang, 1e-12), 0.0)
    return qnorm(np.concatenate([ax * np.sin(ang / 2), np.cos(ang / 2)], -1))


def qmean(q):
    """Mean rotation over axis 0 (hemisphere-aligned normalised sum; fine for gait spreads)."""
    q = qcontinuous(q.copy()); return qnorm(q.sum(0))


def _cycle_sample(clip, u):
    """Sample a loop (last frame == first) at normalised phases u in [0,1) with slerp.
    Returns (ql (K,B,4), hips_d (K,3), root velocity per unit phase (K,3) in root space)."""
    N = clip.F - 1; t = (np.asarray(u) % 1.0) * N
    i0 = np.floor(t).astype(int) % N; i1 = i0 + 1; f = t - np.floor(t)
    ql = qcontinuous(clip.ql)
    q = qslerp(ql[i0], ql[i1], np.broadcast_to(f[:, None], ql[i0].shape[:-1]))
    hd = clip.hips_d[i0] * (1 - f[:, None]) + clip.hips_d[i1] * f[:, None]
    r = 0
    rv = np.array([qrot(qconj(clip.ql[k, r]), clip.root_p[k + 1] - clip.root_p[k]) for k in range(N)]) * N
    rvc = np.concatenate([rv, rv[:1]], 0)
    v = rvc[i0] * (1 - f[:, None]) + rvc[i1] * f[:, None]
    return q, hd, v


def cycle_phase(skel, clip, side="l"):
    """Normalised phase of the first plant (contact onset) of one foot in a loop."""
    con, _ = detect_contacts(skel, clip)
    c = con[side]["contact"][:-1]; N = len(c)
    on = [k for k in range(N) if c[k] and not c[k - 1]]
    if not on: return 0.0
    return on[0] / N


def blend_cycles(skel, A, B, w=0.5, mode="amplitude", phase_side="l", N=None):
    """Synthesize a loop between two phase-aligned loops A and B (e.g. walk_f + walk_l -> walk_fl).
    Both are time-normalised to one cycle, aligned on the plant of `phase_side`'s foot, and
    blended per bone. mode 'linear': slerp(qa, qb, w), root velocity (1-w)va + w vb (a
    convex blend: the diagonal travels at ~0.7x the inputs' speed, like the engine's own
    runtime blend). mode 'amplitude': each clip = its mean pose * cyclic delta; means blend
    by w, cyclic deltas and root velocities are summed with gains (1-w)g, w g, g = 1/|(1-w, w)|,
    so orthogonal stride patterns keep their full amplitude and the diagonal keeps the inputs'
    speed. Root stays yaw-free (constant facing +Z). Returns a loop Clip (last == first)."""
    if phase_side == "search_full":
        # as 'search', but each candidate is scored after the stance plant + foot lock the build
        # applies, so leg crossings created by pinning the feet are seen (slower: 24 candidates)
        pa = cycle_phase(skel, A, "l"); best = (1e9, 0.0); M = 24
        for k in range(M):
            c = _blend_at(skel, A, B, w, mode, pa, k / M, N)
            st = c.meta["stance"]; sf = min(float(np.mean(st[x])) for x in "lr")
            c = foot_lock_loop(skel, plant_feet(skel, c, st), stance=st, lock=True, ground=True)
            best = min(best, (_blend_cost(skel, c) + max(0.0, 0.45 - sf) * 100, k / M))
        pb = best[1]
    elif phase_side == "search":
        # try every phase offset of B against A; keep the one whose raw blend is most physically
        # consistent: least foot slide before any cleanup, both feet planting, shins never crossing
        pa = cycle_phase(skel, A, "l"); best = (1e9, 0.0); M = 48
        for k in range(M):
            c = _blend_at(skel, A, B, w, mode, pa, k / M, N)
            sf = min(float(np.mean(c.meta["stance"][x])) for x in "lr")
            best = min(best, (_blend_cost(skel, c) + max(0.0, 0.45 - sf) * 100, k / M))
        pb = best[1]
    elif phase_side == "contacts":
        # align B to A so both feet are planted at the same phases (best contact agreement)
        pa = cycle_phase(skel, A, "l"); M = 120; g = np.arange(M) / M
        ca, _ = detect_contacts(skel, A); cb, _ = detect_contacts(skel, B)
        sa = {x: ca[x]["contact"][(((g + pa) % 1) * (A.F - 1)).astype(int)] for x in "lr"}
        best = (-1, 0.0)
        for k in range(M):
            idx = (((g + k / M) % 1) * (B.F - 1)).astype(int)
            sc = sum(int(np.sum(sa[x] == cb[x]["contact"][idx])) for x in "lr")
            if sc > best[0]: best = (sc, k / M)
        pb = best[1]
    else:
        pa, pb = cycle_phase(skel, A, phase_side), cycle_phase(skel, B, phase_side)
    out = _blend_at(skel, A, B, w, mode, pa, pb, N)
    out.meta["blend"]["phase_mode"] = phase_side
    return out


def _seg_min_dist(p1, q1, p2, q2, n=6):
    """Approximate min distance per frame between segments p1-q1 and p2-q2 (sampled)."""
    t = np.linspace(0, 1, n)
    a = p1[:, None] + (q1 - p1)[:, None] * t[None, :, None]; b = p2[:, None] + (q2 - p2)[:, None] * t[None, :, None]
    return np.min(np.linalg.norm(a[:, :, None] - b[:, None, :], axis=-1), axis=(1, 2))


def _blend_cost(skel, c):
    """Physical consistency of a raw blended cycle: worst foot slide (cm), shin clearance
    penalty, and a large penalty when a foot never plants."""
    Wc, P = fk(skel, c)
    con, tr = detect_contacts(skel, c, Wc=Wc, P=P); sl = slide_per_step(tr, con)
    slide = max([d for x in "lr" for _, d in sl[x]] or [0.0]) * 100
    miss = sum(1 for x in "lr" if not con[x]["contact"].any()) * 100
    d = _seg_min_dist(P[:, skel.i("lower_leg_l")], P[:, skel.i("foot_l")], P[:, skel.i("lower_leg_r")], P[:, skel.i("foot_r")])
    dt = _seg_min_dist(P[:, skel.i("upper_leg_l")], P[:, skel.i("lower_leg_l")], P[:, skel.i("upper_leg_r")], P[:, skel.i("lower_leg_r")])
    clear = (max(0.0, 0.10 - float(d.min())) + max(0.0, 0.16 - float(dt.min()))) * 100 * 5
    return slide + miss + clear


def _blend_at(skel, A, B, w, mode, pa, pb, N=None):
    NA, NB = A.F - 1, B.F - 1
    if N is None: N = int(round((1 - w) * NA + w * NB))
    u = np.arange(N + 1) / N
    qa, ha, va = _cycle_sample(A, u + pa)
    qb, hb, vb = _cycle_sample(B, u + pb)
    B_ = qa.shape[1]
    if mode == "linear":
        q = qslerp(qa, qcontinuous(np.where((np.sum(qa * qb, -1) < 0)[..., None], -qb, qb)), np.full(qa.shape[:-1], w))
        hd = ha * (1 - w) + hb * w; v = va * (1 - w) + vb * w; ka, kb = 1 - w, w
    else:   # "amplitude" or "amplitude:<k>" (gain scaled by k: between linear and full amplitude)
        g = 1.0 / math.hypot(1 - w, w) * (float(mode.split(":")[1]) if ":" in mode else 1.0); ka, kb = (1 - w) * g, w * g
        ma, mb = qmean(qa[:-1]), qmean(qb[:-1])
        m = qslerp(ma, np.where((np.sum(ma * mb, -1) < 0)[..., None], -mb, mb), np.full(B_, w))
        da = qlog(qmul(qconj(ma)[None], qa)); db = qlog(qmul(qconj(mb)[None], qb))
        q = qmul(m[None], qexp(ka * da + kb * db))
        hma, hmb = ha[:-1].mean(0), hb[:-1].mean(0)
        hd = (1 - w) * hma + w * hmb + ka * (ha - hma) + kb * (hb - hmb)
        v = ka * va + kb * vb
    # stance per foot from the inputs' own contacts at these phases (weighted vote)
    ca, _ = detect_contacts(skel, A); cb, _ = detect_contacts(skel, B)
    stance = {}
    for x in "lr":
        sa = ca[x]["contact"][:-1].astype(float); sb = cb[x]["contact"][:-1].astype(float)
        ia = (((u + pa) % 1.0) * NA).astype(int) % NA; ib = (((u + pb) % 1.0) * NB).astype(int) % NB
        stance[x] = ((1 - w) * sa[ia] + w * sb[ib]) >= 0.5
    q[:, 0] = np.array([0, 0, 0, 1.0])
    q[-1] = q[0]; hd[-1] = hd[0]
    rp = np.zeros((N + 1, 3)); rp[1:] = np.cumsum((v[:-1] + v[1:]) / 2 / N, 0); rp[:, 1] = 0
    out = Clip(A.fps, qnorm(q), rp, hd, "", {})
    out.meta["blend"] = {"a": A.name, "b": B.name, "w": round(float(w), 4), "mode": mode, "gains": [round(float(ka), 4), round(float(kb), 4)],
                         "phase": [round(float(pa), 4), round(float(pb), 4)], "frames": [NA + 1, NB + 1, N + 1]}
    out.meta["stance"] = stance
    return out


def plant_feet(skel, clip, stance, blend=3):
    """Pull each foot down to the ground on its stance frames (lowest of heel/ball to rest
    height) with 2-bone leg IK, eased over `blend` frames. For synthesized cycles, where the
    blend of two out-of-phase stance feet leaves the planted foot hovering. Loop-safe when the
    clip is a loop (stance is treated cyclically)."""
    Wc, P = fk(skel, clip); tr = foot_tracks(skel, clip, Wc, P); F = clip.F; rep = {}
    for x in "lr":
        st = np.asarray(stance[x], bool)[:F]
        low = np.min(np.stack([tr[(x, k)][0][:, 1] - tr[(x, k)][1] for k in ("heel", "ball")]), 0)
        w = st.astype(float)
        for j in range(1, blend + 1):
            e = 1 - j / (blend + 1)
            w = np.maximum(w, e * np.roll(st, j)); w = np.maximum(w, e * np.roll(st, -j))
        off = np.zeros((F, 3)); off[:, 1] = -np.maximum(low, 0) * w
        frames = [f for f in range(F) if abs(off[f, 1]) > 1e-5]
        two_bone_ik(Wc, P, skel, x, P[:, skel.i("foot_" + x)] + off, frames)
        rep[x] = {"stance_frac": round(float(st.mean()), 2), "max_pull_cm": round(float(-off[:, 1].min()) * 100, 2)}
    ql = locals_from_world(skel, Wc); ql[:, skel.i("root")] = clip.ql[:, skel.i("root")]
    out = Clip(clip.fps, ql, clip.root_p, clip.hips_d, clip.name, clip.meta); out.meta["plant"] = rep
    out.ql[-1] = out.ql[0]
    return out


# --------------------------------------------------------------------------- additive

def rebase_additive(clip, base, ref_frame=0):
    """Deltas of `clip` relative to its own frame `ref_frame`, re-applied on frame 0 of `base`.
    The file stores base0*delta per bone (and base0 + dp for hips); the engine recovers
    delta = base0^-1 * q at runtime (clips.ts evalAdditive)."""
    d = qmul(qconj(clip.ql[ref_frame])[None], clip.ql)
    ql = qmul(base.ql[0][None], d)
    out = clip.copy(ql=qnorm(ql)); out.root_p = np.zeros_like(clip.root_p)
    out.ql[:, 0] = np.array([0, 0, 0, 1.0])
    out.hips_d = base.hips_d[0][None] + (clip.hips_d - clip.hips_d[ref_frame][None])
    return out


# --------------------------------------------------------------------------- engine encoding

def engine_hips_consts(skel, spec):
    src_rest = np.array(spec.get("animation", {}).get("engine", {}).get("hips_rest_src", [0, 0.95, 0]), float)
    d_rest = skel.P[skel.i("hips")] - skel.P[skel.i("root")]
    return src_rest, d_rest, np.linalg.norm(src_rest) / np.linalg.norm(d_rest)


def encode_hips(skel, spec, hips_d):
    src_rest, d_rest, s = engine_hips_consts(skel, spec)
    return src_rest + (hips_d - d_rest) * s


def decode_hips(skel, spec, key):
    src_rest, d_rest, s = engine_hips_consts(skel, spec)
    return d_rest + (np.asarray(key) - src_rest) / s


# --------------------------------------------------------------------------- glb writer

def write_anim_glb(path, skel, clips, spec, set_id, extras=None, frames="world_aligned", bones=None):
    """One glb, one animation per clip. Nodes = the skeleton's bones (no mesh, no skin).
    frames='world_aligned': node rest rotations identity, tracks canonical (engine today).
    frames='rig': node rest = the skeleton's own local frames, tracks converted to them.
    bones: only these bones get channels (first-person clips key the arm chains only; root/hips
    translations are written only when listed); a dict maps clip name -> bone set. None = every bone."""
    import struct
    B = len(skel)
    nodes = [{"name": set_id, "children": [skel.i("root") + 1], "extras": dict(extras or {})}]
    src_rest, _, _ = engine_hips_consts(skel, spec)
    restA = skel.A
    for b, n in enumerate(skel.names):
        nd = {"name": n}
        p = skel.parent[b]
        if frames == "rig":
            Ap = restA[p] if p >= 0 else np.array([0, 0, 0, 1.0])
            nd["translation"] = [float(x) for x in qrot(qconj(Ap), skel.offset[b])] if p >= 0 else [0.0, 0.0, 0.0]
            nd["rotation"] = [float(x) for x in qnorm(qmul(qconj(Ap), restA[b]))]
        else:
            t = src_rest if n == "hips" else skel.offset[b]
            if p >= 0: nd["translation"] = [float(x) for x in t]
        ch = [c + 1 for c in skel.children[b]]
        if ch: nd["children"] = ch
        nodes.append(nd)
    blob = bytearray(); bviews = []; accs = []
    def add(arr, typ, minmax=False):
        arr = np.ascontiguousarray(arr, np.float32)
        while len(blob) % 4: blob.append(0)
        off = len(blob); blob.extend(arr.tobytes())
        bviews.append({"buffer": 0, "byteOffset": off, "byteLength": arr.nbytes})
        a = {"bufferView": len(bviews) - 1, "componentType": 5126, "count": int(arr.shape[0]), "type": typ}
        if minmax: a["min"] = [float(arr.min())]; a["max"] = [float(arr.max())]
        accs.append(a); return len(accs) - 1
    anims = []
    root_i, hips_i = skel.i("root"), skel.i("hips")
    skip = set(spec.get("animation", {}).get("unkeyed_bones", []))
    for c in clips:
        tin = add(np.arange(c.F) / c.fps, "SCALAR", True)
        ch, sm = [], []
        ql = qcontinuous(c.ql)
        for b, n in enumerate(skel.names):
            if n in skip or n.startswith("attach_"): continue
            cb = bones.get(c.name) if isinstance(bones, dict) else bones
            if cb is not None and n not in cb: continue
            q = ql[:, b]
            if frames == "rig":
                p = skel.parent[b]; Ap = restA[p] if p >= 0 else np.array([0, 0, 0, 1.0])
                q = qcontinuous(qmul(qmul(qconj(Ap)[None], q), restA[b][None]))
            sm.append({"input": tin, "output": add(q, "VEC4"), "interpolation": "LINEAR"})
            ch.append({"sampler": len(sm) - 1, "target": {"node": b + 1, "path": "rotation"}})
        for b, val in ((root_i, c.root_p), (hips_i, encode_hips(skel, spec, c.hips_d) if frames != "rig" else None)):
            cb = bones.get(c.name) if isinstance(bones, dict) else bones
            if cb is not None and skel.names[b] not in cb: continue
            if val is None:   # rig frames: hips local in root's rest frame
                val = qrot(qconj(restA[root_i])[None], c.hips_d)
            sm.append({"input": tin, "output": add(val, "VEC3"), "interpolation": "LINEAR"})
            ch.append({"sampler": len(sm) - 1, "target": {"node": b + 1, "path": "translation"}})
        anims.append({"name": c.name, "channels": ch, "samplers": sm})
    while len(blob) % 4: blob.append(0)
    g = {"asset": {"version": "2.0", "generator": "game-animation animlib"}, "scene": 0,
         "scenes": [{"nodes": [0]}], "nodes": nodes, "animations": anims,
         "buffers": [{"byteLength": len(blob)}], "bufferViews": bviews, "accessors": accs}
    js = json.dumps(g, separators=(",", ":")).encode()
    while len(js) % 4: js += b" "
    out = struct.pack("<III", 0x46546C67, 2, 12 + 8 + len(js) + 8 + len(blob))
    out += struct.pack("<II", len(js), 0x4E4F534A) + js + struct.pack("<II", len(blob), 0x004E4942) + bytes(blob)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, "wb").write(out)
    return path


def read_anim_glb(path, skel, spec):
    """-> ({clip name: Clip}, report dict of raw facts: channels per clip, paths, bone names, times)."""
    from validate_glb import load_glb, accessor
    g, binb = load_glb(path)
    nodes = g["nodes"]; raw = {}; clips = {}
    rest_rot = {n.get("name"): n.get("rotation") for n in nodes}
    rig_frames = any(rest_rot.get(b) for b in skel.names if b != "root")
    for a in g.get("animations", []):
        tr = {}; times = None; paths = []
        for c in a["channels"]:
            s = a["samplers"][c["sampler"]]; nm = nodes[c["target"]["node"]].get("name"); p = c["target"]["path"]
            t = accessor(g, binb, s["input"]).ravel(); v = accessor(g, binb, s["output"])
            tr[(nm, p)] = (t, v, s.get("interpolation", "LINEAR")); paths.append((nm, p))
            times = t if times is None or len(t) > len(times) else times
        raw[a.get("name")] = {"paths": paths, "times": times, "tracks": tr}
        if times is None or len(times) < 1: continue
        dt = np.diff(times); fps = round(1.0 / float(np.median(dt))) if len(dt) else 30
        F = int(round((times[-1] - times[0]) * fps)) + 1; tt = times[0] + np.arange(F) / fps
        ql = np.tile(np.array([0, 0, 0, 1.0]), (F, len(skel), 1)); rp = np.zeros((F, 3))
        hd = np.tile(skel.P[skel.i("hips")] - skel.P[skel.i("root")], (F, 1))
        def samp(t, v):
            if len(t) == 1: return np.repeat(v, F, 0)
            if v.shape[1] == 4:
                i1 = np.clip(np.searchsorted(t, tt), 1, len(t) - 1); i0 = i1 - 1
                u = np.clip((tt - t[i0]) / np.maximum(t[i1] - t[i0], 1e-9), 0, 1)
                return qslerp(v[i0], v[i1], u)
            return np.stack([np.interp(tt, t, v[:, k]) for k in range(v.shape[1])], -1)
        for (nm, p), (t, v, _) in tr.items():
            if nm not in skel.idx: continue
            b = skel.i(nm)
            if p == "rotation":
                q = samp(t, v)
                if rig_frames:
                    pp = skel.parent[b]; Ap = skel.A[pp] if pp >= 0 else np.array([0, 0, 0, 1.0])
                    q = qmul(qmul(Ap[None], q), qconj(skel.A[b])[None])
                ql[:, b] = q
            elif p == "translation" and nm == "root": rp = samp(t, v)
            elif p == "translation" and nm == "hips":
                hv = samp(t, v)
                hd = qrot(skel.A[skel.i("root")][None], hv) if rig_frames else decode_hips(skel, spec, hv)
        clips[a.get("name")] = Clip(fps, ql, rp, hd, a.get("name"))
    return clips, {"gltf": g, "raw": raw, "node_names": [n.get("name") for n in nodes], "rig_frames": rig_frames}
