#!/usr/bin/env python3
"""
validate_glb.py -- check an exported .glb against the project spec (assets/spec.json) and
the asset's brief. Plain CPython (numpy, pillow, opencv). No Blender needed.

    python3 validate_glb.py path/to/asset.glb --brief path/to/brief.json [--spec spec.json]
            [--placeholder] [--json out.json] [--no-khronos]

Exit code 0 = every FAIL-level check passed. Prints one line per check:
    PASS|FAIL|WARN  check_id  detail
Coordinates are reported in Blender axes (x right, y back, z up) so they match the brief.
"""
import argparse, io, json, math, os, re, struct, subprocess, sys
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.join(os.path.dirname(HERE), "tools")

# --------------------------------------------------------------------------- glb parsing

CT = {5120: np.int8, 5121: np.uint8, 5122: np.int16, 5123: np.uint16, 5125: np.uint32, 5126: np.float32}
NC = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}


def load_glb(path):
    b = open(path, "rb").read()
    magic, ver, length = struct.unpack_from("<III", b, 0)
    if magic != 0x46546C67: raise ValueError("not a GLB")
    off = 12; js = None; bin_ = b""
    while off < length:
        clen, ctype = struct.unpack_from("<II", b, off); off += 8
        chunk = b[off:off + clen]; off += clen
        if ctype == 0x4E4F534A: js = json.loads(chunk)
        elif ctype == 0x004E4942: bin_ = chunk
    return js, bin_


def accessor(g, binb, i):
    a = g["accessors"][i]; bv = g["bufferViews"][a["bufferView"]]
    dt = CT[a["componentType"]]; n = NC[a["type"]]; cnt = a["count"]
    base = bv.get("byteOffset", 0) + a.get("byteOffset", 0)
    stride = bv.get("byteStride", 0); isz = np.dtype(dt).itemsize * n
    if stride and stride != isz:
        raw = np.frombuffer(binb, np.uint8, count=stride * (cnt - 1) + isz, offset=base)
        rows = np.lib.stride_tricks.as_strided(raw, (cnt, isz), (stride, 1))
        arr = np.frombuffer(rows.copy().tobytes(), dt).reshape(cnt, n)
    else:
        arr = np.frombuffer(binb, dt, count=cnt * n, offset=base).reshape(cnt, n)
    arr = arr.astype(np.float64)
    if a.get("normalized"):
        arr /= float(np.iinfo(dt).max)
    return arr


def image_bytes(g, binb, i):
    im = g["images"][i]
    if "bufferView" in im:
        bv = g["bufferViews"][im["bufferView"]]; o = bv.get("byteOffset", 0)
        return binb[o:o + bv["byteLength"]], im.get("mimeType", "")
    return None, im.get("uri", "")


def quat_mat(q):
    x, y, z, w = q
    return np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                     [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                     [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])


def node_matrix(n):
    if "matrix" in n: return np.array(n["matrix"]).reshape(4, 4).T
    M = np.eye(4)
    R = quat_mat(n.get("rotation", [0, 0, 0, 1])); S = np.diag(n.get("scale", [1, 1, 1]))
    M[:3, :3] = R @ S; M[:3, 3] = n.get("translation", [0, 0, 0]); return M


def walk(g, roots):
    """Yield (node_index, world_matrix, parent_index, depth)."""
    stack = [(r, np.eye(4), None, 0) for r in roots]
    while stack:
        i, P, par, d = stack.pop()
        W = P @ node_matrix(g["nodes"][i]); yield i, W, par, d
        for c in g["nodes"][i].get("children", []): stack.append((c, W, i, d + 1))


def mesh_data(g, binb, mi):
    """Concatenate positions/indices/uv0/uv1/normals across primitives (triangles only)."""
    out = []
    for p in g["meshes"][mi]["primitives"]:
        at = p["attributes"]; pos = accessor(g, binb, at["POSITION"])
        idx = accessor(g, binb, p["indices"]).astype(np.int64).ravel() if "indices" in p else np.arange(len(pos))
        mode = p.get("mode", 4)
        out.append(dict(pos=pos, idx=idx.reshape(-1, 3) if mode == 4 else np.zeros((0, 3), np.int64), mode=mode,
                        uv0=accessor(g, binb, at["TEXCOORD_0"]) if "TEXCOORD_0" in at else None,
                        uv1=accessor(g, binb, at["TEXCOORD_1"]) if "TEXCOORD_1" in at else None,
                        nrm="NORMAL" in at, material=p.get("material"), attrs=at, targets=p.get("targets", [])))
    return out


def to_blender(v):
    """glTF (x, y, z) -> Blender (x, -z, y)."""
    v = np.asarray(v, float); return np.stack([v[..., 0], -v[..., 2], v[..., 1]], -1)


# --------------------------------------------------------------------------- report

class Report:
    def __init__(self): self.items = []
    def add(self, level, cid, ok, detail=""):
        lvl = "PASS" if ok else level
        self.items.append({"level": lvl, "check": cid, "detail": detail})
    def fail(self, cid, ok, detail=""): self.add("FAIL", cid, ok, detail)
    def warn(self, cid, ok, detail=""): self.add("WARN", cid, ok, detail)
    def info(self, cid, detail): self.items.append({"level": "INFO", "check": cid, "detail": detail})
    @property
    def failed(self): return [i for i in self.items if i["level"] == "FAIL"]


def srgb(c): c = np.clip(c, 0, 1); return np.where(c <= 0.0031308, 12.92 * c, 1.055 * c ** (1 / 2.4) - 0.055)


def hsv(rgb):
    import colorsys; return colorsys.rgb_to_hsv(*[float(x) for x in rgb])


def hex_rgb(h): h = h.lstrip("#"); return np.array([int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)])


# --------------------------------------------------------------------------- checks

def tri_area3(p, t):
    a, b, c = p[t[:, 0]], p[t[:, 1]], p[t[:, 2]]
    return 0.5 * np.linalg.norm(np.cross(b - a, c - a), axis=1)


def tri_area2(u, t):
    a, b, c = u[t[:, 0]], u[t[:, 1]], u[t[:, 2]]
    return 0.5 * np.abs((b[:, 0] - a[:, 0]) * (c[:, 1] - a[:, 1]) - (c[:, 0] - a[:, 0]) * (b[:, 1] - a[:, 1]))


def uv_islands(uv, t):
    """Union-find islands by shared (quantized) UV vertex positions."""
    key = np.round(uv * 1e5).astype(np.int64); _, inv = np.unique(key, axis=0, return_inverse=True)
    inv = inv.ravel(); parent = list(range(inv.max() + 1))
    def f(x):
        while parent[x] != x: parent[x] = parent[parent[x]]; x = parent[x]
        return x
    for a, b, c in inv[t]:
        ra, rb, rc = f(a), f(b), f(c); parent[rb] = ra; parent[f(rc)] = ra
    return np.array([f(inv[t[k, 0]]) for k in range(len(t))])


def check_lightmap_uv(uv, t, res, pad, rep, cid):
    import cv2
    if uv is None: rep.fail(cid + ".present", False, "no TEXCOORD_1"); return
    rep.fail(cid + ".range", bool((uv >= -1e-4).all() and (uv <= 1 + 1e-4).all()),
             "min %.3f max %.3f" % (uv.min(), uv.max()))
    isl = uv_islands(uv, t); labels = np.unique(isl)
    px = np.stack([uv[:, 0] * res, uv[:, 1] * res], 1)
    cover = np.zeros((res, res), np.int32); masks = []
    for L in labels:
        m = np.zeros((res, res), np.uint8)
        for tri in t[isl == L]:
            cv2.fillPoly(m, [np.round(px[tri] * 16).astype(np.int32)], 1, lineType=cv2.LINE_8, shift=4)
        masks.append(m); cover += m
    # erode 1px so shared-edge rasterization is not counted as overlap between different islands
    inner = [cv2.erode(m, np.ones((3, 3), np.uint8)) for m in masks]
    tot = np.zeros((res, res), np.int32)
    for m in inner: tot += m
    over = int((tot > 1).sum())
    rep.fail(cid + ".overlap", over == 0, "%d overlapping px at %d (islands: %d)" % (over, res, len(labels)))
    # padding: dilate each island by pad-1 px; any collision with another island = too tight
    k = np.ones((2 * (pad - 1) + 1,) * 2, np.uint8) if pad > 1 else np.ones((1, 1), np.uint8)
    dil = np.zeros((res, res), np.int32)
    for m in masks: dil += cv2.dilate(m, k)
    tight = int((dil > 1).sum())
    rep.fail(cid + ".padding", tight == 0, "%d px closer than %d px between islands" % (tight, pad))
    rep.info(cid + ".coverage", "%.1f%% of the lightmap square used" % (100 * (cover > 0).mean()))


def uv_mask(uvs_tris, w, h, grow=2):
    """Texels covered by the given (uv, tris) sets (UVs wrapped into [0,1))."""
    import cv2
    m = np.zeros((h, w), np.uint8)
    for uv, t in uvs_tris:
        if uv is None: continue
        u = uv - np.floor(uv.min(0) + 0.02)     # tolerate slight undershoot (decimated/transferred UVs)
        if (u.max(0) > 1.02).any():            # genuinely tiling UVs: every texel can be used
            return None
        u = np.clip(u, 0.0, 1.0)
        px = np.stack([u[:, 0] * w, u[:, 1] * h], 1)           # glTF UV origin = image top-left
        for tri in t: cv2.fillPoly(m, [np.round(px[tri] * 16).astype(np.int32)], 1, shift=4)
    return cv2.dilate(m, np.ones((2 * grow + 1,) * 2, np.uint8)) > 0 if grow else m > 0


def check_texture(name, data, role, spec, rep, used=None):
    from PIL import Image
    im = Image.open(io.BytesIO(data)); w, h = im.size
    tx = spec["textures"]
    pot = lambda x: x & (x - 1) == 0
    rep.fail("tex.%s.pow2" % name, (not tx["power_of_two"]) or (pot(w) and pot(h)), "%dx%d" % (w, h))
    rep.fail("tex.%s.max" % name, max(w, h) <= tx["max_px"], "%dx%d (max %d)" % (w, h, tx["max_px"]))
    rep.fail("tex.%s.format" % name, im.format.lower() == tx["format"], im.format)
    a = np.asarray(im.convert("RGBA"), np.float32) / 255
    if role == "base":
        mat = spec["materials"]; lum = a[..., :3].mean(-1)
        vis = a[..., 3] > 0.5
        if used is not None:
            um = uv_mask(used, w, h)
            if um is not None: vis &= um
        clip = float(((lum < mat["albedo_srgb_min"]) | (lum > mat["albedo_srgb_max"]))[vis].mean()) if vis.any() else 0
        rep.fail("tex.%s.albedo_range" % name, clip <= mat["albedo_clip_fraction_max"],
                 "%.2f%% px outside sRGB [%.2f, %.2f] (no pure black/white)" % (100 * clip, mat["albedo_srgb_min"], mat["albedo_srgb_max"]))
        return a[..., :3][vis].mean(0) if vis.any() else a[..., :3].mean((0, 1))
    if role == "normal":
        mean = a[..., :3].reshape(-1, 3).mean(0)
        rep.fail("tex.%s.normal_tangent" % name, mean[2] > 0.7 and abs(mean[0] - 0.5) < 0.12 and abs(mean[1] - 0.5) < 0.12,
                 "mean rgb %.2f %.2f %.2f (tangent-space expects ~0.5 0.5 1)" % tuple(mean))
        rep.info("tex.%s.normal_y" % name, "OpenGL +Y cannot be proven from pixels; bake with Blender (OpenGL) or flip G from DirectX sources")
    if role == "orm":
        px = a[..., :3].reshape(-1, 3); px = px[px.sum(1) > 0.02] if (px.sum(1) > 0.02).any() else px
        m = px.mean(0)
        rep.info("tex.%s.orm_means" % name, "R(ao) %.2f G(rough) %.2f B(metal) %.2f (non-empty texels)" % tuple(m))
        rep.warn("tex.%s.ao_level" % name, m[0] > 0.45, "mean AO %.2f; a near-black AO map usually means a failed bake (no world / wrong distance)" % m[0])
        return m
    return None


def run(glb, brief, spec, placeholder=False, khronos=True, assets_root=None):
    rep = Report()
    g, binb = load_glb(glb)
    aid = brief["id"]; catk = brief["category"]; cat = spec["categories"][catk]
    nm = spec["naming"]

    # ---- naming / file layout
    rep.fail("name.id", re.match(nm["id_regex"], aid) is not None and aid.startswith(cat["prefix"]),
             "%s (prefix %s)" % (aid, cat["prefix"]))
    rep.fail("file.name", os.path.basename(glb) == aid + ".glb", os.path.basename(glb))
    if assets_root:
        want = os.path.join(assets_root, spec["paths"]["placeholder" if placeholder else "export"], cat["export_dir"], aid + ".glb")
        rep.fail("file.path", os.path.abspath(glb) == os.path.abspath(want), os.path.relpath(glb, assets_root))

    scene = g["scenes"][g.get("scene", 0)]; roots = scene["nodes"]
    rep.fail("hier.single_root", len(roots) == 1, "%d root nodes" % len(roots))
    r0 = g["nodes"][roots[0]]
    rep.fail("hier.root_name", r0.get("name") == aid, "root '%s'" % r0.get("name"))
    nodes = list(walk(g, roots))
    names = {i: g["nodes"][i].get("name", "") for i, *_ in nodes}
    bad = [n for n in names.values() if not re.match(nm["node_regex"], n)]
    rep.fail("name.nodes", not bad, "bad: %s" % bad[:6] if bad else "%d nodes" % len(names))

    # ---- transforms
    badT = []
    for i, W, par, d in nodes:
        n = g["nodes"][i]; s = np.array(n.get("scale", [1, 1, 1]))
        if (np.abs(s - 1) > 1e-4).any(): badT.append("%s scale %s" % (names[i], s.round(4).tolist()))
        if "mesh" in n and "rotation" in n and np.abs(np.array(n["rotation"]) - [0, 0, 0, 1]).max() > 1e-4 \
                and not names[i].startswith(spec.get("sockets", {}).get("prefix", "socket_")):
            badT.append("%s rotated" % names[i])
        if "matrix" in n: badT.append("%s uses matrix" % names[i])
    rep.fail("xform.applied", not badT, "; ".join(badT[:5]) or "scale 1, mesh rotations identity")

    # ---- classify nodes
    lod_re = re.compile("^" + re.escape(aid) + r"_lod(\d+)$")
    col_name = aid + nm["col_suffix"]
    lods = {}; cols = []; render_other = []; W_of = {}
    for i, W, par, d in nodes:
        n = g["nodes"][i]; W_of[i] = W
        if "mesh" not in n: continue
        m = lod_re.match(names[i])
        if m and (par == roots[0] or names.get(par) == aid + "_intact"): lods[int(m.group(1))] = i
        elif re.search(r"_col(_walk|_\d\d)?$", names[i]): cols.append(i)          # _col, _col_walk, compound _col_NN
        elif d == 0 and names[i] == aid: lods[0] = i
        else: render_other.append(i)
    rep.fail("hier.lod0", 0 in lods, "lod nodes: %s" % sorted(lods))
    if 0 not in lods: return rep, g

    def world_pts(i):
        md = mesh_data(g, binb, g["nodes"][i]["mesh"]); W = W_of[i]
        allp = []; alltri = []; off = 0
        for p in md:
            P = (W @ np.c_[p["pos"], np.ones(len(p["pos"]))].T).T[:, :3]
            allp.append(P); alltri.append(p["idx"] + off); off += len(P)
        return np.vstack(allp), np.vstack(alltri), md

    P0, T0, md0 = world_pts(lods[0])
    tris0 = len(T0)
    # ---- budget / LODs
    budget = brief.get("tri_budget") or spec["budgets"][brief.get("budget_class", cat["budget_class"])]
    rep.fail("budget.lod0", tris0 <= budget, "%d tris (budget %d)" % (tris0, budget))
    if not placeholder:
        L = spec["lod"]
        if tris0 > L["min_tris"]:
            for k, want in enumerate(L["ratios"][1:], 1):
                if k not in lods: rep.fail("lod.%d" % k, False, "missing %s_lod%d" % (aid, k)); continue
                tk = len(world_pts(lods[k])[1]); ratio = tk / tris0
                prev = len(world_pts(lods[k - 1])[1]) if (k - 1) in lods else tris0
                detail = "%d tris = %.2f of lod0 (want ~%.2f)" % (tk, ratio, want)
                why = " -- heavier than spec" if ratio > want + L["ratio_tolerance"] else (" -- not lighter than LOD%d (%d tris)" % (k - 1, prev) if tk >= prev else "")
                rep.fail("lod.%d.max" % k, ratio <= want + L["ratio_tolerance"] and tk < prev, detail + why)
                rep.warn("lod.%d.min" % k, ratio >= want - L["ratio_tolerance"], detail + " -- lighter than spec; check the lods view for popping")
        else:
            rep.info("lod.skip", "lod0 %d tris <= %d, LODs not required" % (tris0, L["min_tris"]))
    if render_other:
        rep.info("hier.other_meshes", ", ".join(names[i] for i in render_other))

    # ---- dimensions and pivot (Blender axes)
    # Physics props with moving parts (root ig_parts -> nodes <id>_<key> / <id>_<key>_lod0, e.g.
    # wheels): the parts are part of the item's size, ground contact and centre of mass.
    Pd, Td = P0, T0
    _ip = (r0.get("extras", {}) or {}).get("ig_parts") or {}
    if _ip and brief.get("pivot", cat["pivot"]) == "center_of_mass":
        _pn = {aid + "_" + k + s for k in _ip for s in ("", "_lod0")}
        _hit = []
        for i, W, par, d in nodes:
            if "mesh" in g["nodes"][i] and names[i] in _pn:
                Pk, Tk, _ = world_pts(i); Td = np.vstack([Td, Tk + len(Pd)]); Pd = np.vstack([Pd, Pk]); _hit.append(names[i])
        rep.info("dims.parts", "dims/pivot include ig_parts meshes: %s" % ", ".join(_hit))
    B = to_blender(Pd); lo, hi = B.min(0), B.max(0); size = hi - lo
    rep.info("dims", "x %.3f  y %.3f  z %.3f m (Blender axes: width, depth, height)" % tuple(size))
    if "dims" in brief:
        d = brief["dims"]; tol = d.get("tol", 0.05)
        errs = []
        for k, ax in (("x", 0), ("y", 1), ("z", 2)):
            if k in d and d[k]:
                e = size[ax] / d[k] - 1
                if abs(e) > tol: errs.append("%s %.3f vs %.3f (%+.1f%%)" % (k, size[ax], d[k], 100 * e))
        rep.fail("dims.target", not errs, "; ".join(errs) or "within %.0f%%" % (100 * tol))
    rule = brief.get("pivot", cat["pivot"]); eps = 0.01
    if rule == "bottom_center":
        c = (lo + hi) / 2
        rep.fail("pivot.bottom_center", abs(lo[2]) < eps and abs(c[0]) < eps and abs(c[1]) < eps,
                 "bbox min z %.3f, center x %.3f y %.3f" % (lo[2], c[0], c[1]))
    elif rule == "bottom_left_back":
        rep.fail("pivot.bottom_left_back", abs(lo[0]) < eps and abs(hi[1]) < eps and abs(lo[2]) < eps,
                 "min x %.3f, max y(back) %.3f, min z %.3f" % (lo[0], hi[1], lo[2]))
    elif rule == "center_of_mass":
        rt = np.array(r0.get("translation", [0, 0, 0]))
        a, b, c = P0[T0[:, 0]], P0[T0[:, 1]], P0[T0[:, 2]]
        v6 = np.einsum("ij,ij->i", a, np.cross(b, c)); vol = v6.sum() / 6
        com = (v6[:, None] * (a + b + c) / 4).sum(0) / v6.sum() if abs(vol) > 1e-9 else (P0.min(0) + P0.max(0)) / 2
        off = np.linalg.norm(com - rt); tol = max(0.01, 0.03 * float(size.max()))
        rep.fail("pivot.center_of_mass", off < tol, "root at %s, mesh COM %s (off %.3f m, tol %.3f)" % (rt.round(3).tolist(), com.round(3).tolist(), off, tol))
        rep.fail("pivot.on_ground", abs(lo[2]) < eps, "mesh min height %.3f (must rest on y=0)" % lo[2])
        rep.info("volume", "%.4f m^3 closed-mesh volume" % abs(vol))
    elif rule == "skeleton_root":
        # characters (§0.2): pivot between the feet on the ground = the skeleton's root joint at the origin
        rj = [i for i, W, par, d in nodes if names[i] == "root"]
        if not rj:
            rep.fail("pivot.skeleton_root", False, "no joint named 'root'")
        else:
            rt = W_of[rj[0]][:3, 3]
            rep.fail("pivot.skeleton_root", float(np.abs(rt).max()) < eps, "root joint at %s (glTF)" % rt.round(4).tolist())
        kind = (r0.get("extras", {}) or {}).get("ig_kind") or brief.get("char", {}).get("kind", "")
        if kind in ("body", "skeleton"):
            gt = spec["categories"][catk].get("char_rules", {}).get("ground_tol_m", 0.03)
            rep.fail("pivot.feet_on_ground", abs(lo[2]) <= gt, "mesh min height %.3f m (tol %.2f)" % (lo[2], gt))
            rep.fail("pivot.centered_x", abs((lo[0] + hi[0]) / 2) < 0.02, "bbox centre x %.3f" % ((lo[0] + hi[0]) / 2))
    elif rule == "custom":
        rt = np.array(r0.get("translation", [0, 0, 0]))
        rep.fail("pivot.custom.root_at_origin", np.abs(rt).max() < 1e-4, "root translation %s" % rt.round(4).tolist())
        on_ground = brief.get("on_ground", catk not in ("wpn",) and brief.get("props", {}).get("ig_attach", "ground") == "ground")
        if on_ground:
            rep.fail("pivot.custom.on_ground", abs(lo[2]) < eps, "mesh min height %.3f (set brief on_ground=false if it hangs)" % lo[2])
        pp = brief.get("pivot_point")
        rep.info("pivot.custom", "pivot_point %s; bbox min %s max %s" % (pp, lo.round(3).tolist(), hi.round(3).tolist()))
    if "grid" in cat and brief.get("grid", True) is not False:   # inserts/leaves: brief "grid": false
        mnr = cat["grid"]["minor"]
        off = [float(abs(s / mnr - round(s / mnr)) * mnr) for s in size]
        rep.fail("grid.dims", max(off) < 0.005, "size %s must be multiples of %.2f m" % (size.round(3).tolist(), mnr))

    # ---- collision
    kinds_allowed = cat.get("colliders", [])
    cprop = spec["collision"]["prop"]
    if kinds_allowed:
        main_col = [i for i in cols if names[i] == col_name]
        compound = sorted(names[i] for i in cols if re.match("^" + re.escape(col_name) + r"_\d\d$", names[i]))
        if compound:
            want = ["%s_%02d" % (col_name, k) for k in range(1, len(compound) + 1)]
            rep.fail("col.compound", compound == want, "%d compound parts %s" % (len(compound), "contiguous _01.." if compound == want else "NOT contiguous: %s" % compound))
            rep.fail("col.not_both", not main_col, "use either %s or %s_NN, not both" % (col_name, col_name))
        rep.fail("col.present", bool(main_col or compound), "%s present" % (col_name if main_col else col_name + "_NN") if (main_col or compound) else "missing %s (or %s_01..)" % (col_name, col_name))
        for i in cols:
            ex = g["nodes"][i].get("extras", {}) or g["meshes"][g["nodes"][i]["mesh"]].get("extras", {})
            k = ex.get(cprop)
            if k is None and names[i].endswith(nm.get("col_walk_suffix", "_col_walk")): k = "mesh"   # walk-only default
            is_main = names[i] == col_name or re.match("^" + re.escape(col_name) + r"_\d\d$", names[i])
            ok = k in kinds_allowed or (not is_main and k in spec["collision"]["kinds"])
            rep.fail("col.kind.%s" % names[i], ok, "%s=%r (allowed %s)" % (cprop, k, kinds_allowed))
            Pc, Tc, _ = world_pts(i)
            reuse = g["nodes"][i]["mesh"] == g["nodes"][lods[0]]["mesh"]
            copy = len(Tc) == tris0 and tris0 > 24 and Pc.shape == P0.shape and np.allclose(np.sort(Pc, 0), np.sort(P0, 0), atol=1e-5)
            rep.fail("col.not_render_mesh.%s" % names[i], not (reuse or copy),
                     "%d tris vs render %d%s" % (len(Tc), tris0, " (reuses/copies the render mesh)" if reuse or copy else ""))
            if k == "convex":
                rep.warn("col.convex_verts.%s" % names[i], len(np.unique(Pc.round(5), axis=0)) <= spec["collision"]["max_convex_verts"],
                         "%d verts" % len(np.unique(Pc.round(5), axis=0)))
            if names[i] == col_name:
                Bc = to_blender(Pc); clo, chi = Bc.min(0), Bc.max(0)
                cover = np.minimum(chi, hi) - np.maximum(clo, lo)
                ratio = float(np.prod(np.clip(cover, 0, None)) / max(np.prod(size), 1e-9))
                rep.warn("col.fits_render", 0.6 <= ratio <= 1.3 and np.all(np.abs(clo - lo) < 0.15 * size.max() + 0.02),
                         "collider bbox overlap %.2f of render bbox" % ratio)
                out_lo = np.maximum(lo - clo, 0); out_hi = np.maximum(chi - hi, 0); worst = float(max(out_lo.max(), out_hi.max()))
                rep.warn("col.protrudes", worst <= 0.05, "collider extends %.3f m beyond the render bbox (min side %s, max side %s)"
                         % (worst, out_lo.round(3).tolist(), out_hi.round(3).tolist()))
    elif cols:
        rep.warn("col.unexpected", False, "category %s has no collider but found %s" % (catk, [names[i] for i in cols]))

    # ---- custom properties (root extras)
    ex = r0.get("extras", {})
    req = list(cat.get("required_props", [])) + list(brief.get("required_props", []))
    miss = [k for k in req if k not in ex]
    rep.fail("props.required", not miss, "missing %s" % miss if miss else "root extras: %s" % sorted(ex))
    schema = spec.get("prop_schema", {})
    for k, v in ex.items():
        if k not in schema: continue
        s = schema[k]; ok = True
        if s.startswith("number"): ok = isinstance(v, (int, float)) and not isinstance(v, bool) and (v > 0 if ">0" in s else True)
        elif s == "bool": ok = isinstance(v, (bool, int)) and v in (0, 1, True, False)
        elif s.startswith("vec"): ok = isinstance(v, list) and len(v) == int(s[3]) and all(isinstance(x, (int, float)) for x in v)
        elif s == "hex": ok = isinstance(v, str) and re.match(r"^#[0-9A-Fa-f]{6}$", v) is not None
        elif s == "str": ok = isinstance(v, str) and len(v) > 0
        elif s == "strlist": ok = isinstance(v, list) and all(isinstance(x, str) for x in v)
        elif s.startswith("enum:"):
            src = s[5:]
            opts = src.split(",") if "," in src else (lambda o: o)(spec[src.split(".")[0]][src.split(".")[1]])
            ok = v in opts
        rep.fail("props.type.%s" % k, ok, "%r (%s)" % (v, s))
    if "ig_mass" in ex and "ig_material" in ex and catk == "phys":
        dens = spec.get("physics", {}).get("density_kg_m3", {}).get(ex["ig_material"])
        if dens:
            a, b, c = P0[T0[:, 0]], P0[T0[:, 1]], P0[T0[:, 2]]
            vol = abs(np.einsum("ij,ij->i", a, np.cross(b, c)).sum() / 6)
            rep.info("props.mass_sanity", "ig_mass %.1f kg; solid %s would be %.1f kg (hollow/crate objects are lighter)" % (ex["ig_mass"], ex["ig_material"], vol * dens))

    # ---- sockets / states
    for pref, req_s in spec.get("sockets", {}).get("required", {}).items():
        if aid.startswith(pref):
            have = set(names.values())
            rep.fail("sockets", all(s in have for s in req_s), "need %s" % req_s)
    for pref, suffixes in spec.get("states", {}).items():
        if aid.startswith(pref) and not placeholder:
            have = " ".join(names.values())
            rep.fail("states", all(sfx in have for sfx in suffixes), "need nodes containing %s" % suffixes)
    # lights: emissive materials must exist; a socket_light is expected
    if "ig_light" in ex:
        mnames = {m.get("name") for m in g.get("materials", [])}
        missing = [m for m in ex.get("ig_emissive_material", []) if m not in mnames]
        rep.fail("light.emissive_materials", not missing, "missing %s" % missing if missing else "%d emissive materials" % len(ex.get("ig_emissive_material", [])))
        rep.warn("light.socket", any(n.startswith("socket_light") for n in names.values()), "socket_light empty (falls back to the root)")
    # door nodes anywhere in the hierarchy (nested doors are allowed)
    for i, W, par, d in nodes:
        nex = g["nodes"][i].get("extras", {})
        if nex.get("ig_type") == "door":
            miss = [k for k in ("ig_hinge", "ig_hinge_axis") if k not in nex]
            rep.fail("door.%s" % names[i], not miss, "missing %s" % miss if miss else "hinge %s axis %s open %s deg" % (nex["ig_hinge"], nex["ig_hinge_axis"], nex.get("ig_door_open_deg", 100)))
    for i, W, par, d in nodes:
        if names[i].endswith(nm.get("broken_suffix", "_broken")):
            kids = g["nodes"][i].get("children", [])
            pieces = [c for c in kids if "mesh" in g["nodes"][c] and not names[c].endswith("_col")]
            pc_cols = [c for c in walk(g, kids) if names[c[0]].endswith("_col")]
            rep.fail("broken.pieces_have_cols", len(pieces) > 0 and len(pc_cols) >= len(pieces),
                     "%d pieces, %d piece colliders" % (len(pieces), len(pc_cols)))

    if g.get("skins"):
        check_skins(g, binb, nodes, names, roots, lods, brief, spec, rep, r0)

    if placeholder:
        rep.info("mode", "placeholder: UV, texture, material and LOD checks skipped")
        return rep, g

    # ---- attributes / UVs
    uvn = spec["uv"]
    need_uv1 = cat.get("lightmap_uv", False)
    miss_n = [names[lods[0]]] if not all(p["nrm"] for p in md0) else []
    rep.fail("attr.normals", not miss_n, "all primitives have NORMAL" if not miss_n else "missing on %s" % miss_n)
    rep.fail("attr.uv0", all(p["uv0"] is not None for p in md0), "TEXCOORD_0 on every primitive")
    if need_uv1:
        uv1 = np.vstack([p["uv1"] for p in md0]) if all(p["uv1"] is not None for p in md0) else None
        check_lightmap_uv(uv1, T0, uvn["lightmap_res"], uvn["lightmap_padding_px"], rep, "uv1")
        for k, i in lods.items():
            if k and not all(p["uv1"] is not None for p in mesh_data(g, binb, g["nodes"][i]["mesh"])):
                rep.fail("uv1.lod%d" % k, False, "LOD%d missing lightmap UV" % k)

    # ---- materials & textures
    mats = g.get("materials", [])
    used = sorted({p["material"] for i in list(lods.values()) + render_other for p in mesh_data(g, binb, g["nodes"][i]["mesh"]) if p["material"] is not None})
    rep.fail("mat.assigned", all(p["material"] is not None for p in md0), "%d materials used" % len(used))
    matspec = spec["materials"]; tex_role = {}
    for mi in used:
        m = mats[mi]; mname = m.get("name", "")
        rep.fail("mat.%s.name" % mname, re.match(nm["material_regex"], mname) is not None, mname)
        rep.fail("mat.%s.workflow" % mname, "KHR_materials_pbrSpecularGlossiness" not in m.get("extensions", {}), "metal/rough")
        if m.get("doubleSided"):
            ds_words = matspec.get("double_sided_words", ["foliage", "leaf", "hedge", "weed", "card", "flag", "banner", "paper", "curtain", "cloth", "poster"])
            rep.fail("mat.%s.double_sided" % mname, any(w in mname for w in ds_words),
                     "doubleSided: true (gam.material culls by default; only cards/cloth/paper may be double-sided)")
        am = m.get("alphaMode", "OPAQUE")
        if am != "OPAQUE":
            words = matspec["alpha"].get(am, [])
            rep.fail("mat.%s.alpha" % mname, any(w in mname or w in aid for w in words), "%s allowed only for %s" % (am, words))
        pbr = m.get("pbrMetallicRoughness", {})
        for key, role in (("baseColorTexture", "base"), ("metallicRoughnessTexture", "orm")):
            if key in pbr: tex_role.setdefault(g["textures"][pbr[key]["index"]]["source"], set()).add((role, mname))
        if "normalTexture" in m: tex_role.setdefault(g["textures"][m["normalTexture"]["index"]]["source"], set()).add(("normal", mname))
        if "occlusionTexture" in m: tex_role.setdefault(g["textures"][m["occlusionTexture"]["index"]]["source"], set()).add(("orm", mname))
        if "emissiveTexture" in m: tex_role.setdefault(g["textures"][m["emissiveTexture"]["index"]]["source"], set()).add(("emissive", mname))
        if "occlusionTexture" in m and "metallicRoughnessTexture" in pbr:
            a = g["textures"][m["occlusionTexture"]["index"]]["source"]; b = g["textures"][pbr["metallicRoughnessTexture"]["index"]]["source"]
            rep.fail("mat.%s.orm_packed" % mname, a == b, "occlusion and metal/rough share one ORM image" if a == b else "separate AO image; pack ORM")
        # palette on constant colors
        if "baseColorTexture" not in pbr:
            c = srgb(np.array(pbr.get("baseColorFactor", [1, 1, 1, 1])[:3]))
            rep.fail("mat.%s.albedo_range" % mname, matspec["albedo_srgb_min"] <= c.mean() <= matspec["albedo_srgb_max"],
                     "baseColorFactor sRGB %s" % c.round(3).tolist())
            palette_check(c, mname, aid, spec, rep)
        else:
            rep.info("mat.%s.base" % mname, "textured")
        if "metallicRoughnessTexture" not in pbr:
            rough = pbr.get("roughnessFactor", 1.0)
            glossy_ok = m.get("alphaMode") == "BLEND" or any(w in mname for w in matspec.get("glossy_words", ["glass", "water", "wet", "screen", "lens"]))
            rep.warn("mat.%s.roughness" % mname, glossy_ok or rough >= matspec["roughness_min_mean"], "roughnessFactor %.2f (S4: roughness high)" % rough)
    for src, roles in tex_role.items():
        data, mime = image_bytes(g, binb, src)
        iname = g["images"][src].get("name", "img%d" % src)
        if data is None: rep.fail("tex.%s.embedded" % iname, False, "external uri %s" % mime); continue
        rep.fail("tex.%s.name" % iname, re.match(nm["texture_regex"], iname) is not None, iname)
        for role, mname in roles:
            used = [(p["uv0"], p["idx"]) for i in list(lods.values()) + render_other
                    for p in mesh_data(g, binb, g["nodes"][i]["mesh"]) if p["material"] is not None
                    and mats[p["material"]].get("name") == mname]
            meanc = check_texture(iname, data, role, spec, rep, used if role == "base" else None)
            if role == "base" and meanc is not None:
                palette_check(meanc, mname, aid, spec, rep)
            if role == "orm" and meanc is not None:
                rep.warn("tex.%s.roughness" % iname, meanc[1] >= matspec["roughness_min_mean"], "mean roughness %.2f" % meanc[1])
                used = [(p["uv0"], p["idx"]) for i in list(lods.values()) + render_other
                        for p in mesh_data(g, binb, g["nodes"][i]["mesh"]) if p["material"] is not None
                        and mats[p["material"]].get("name") == mname]
                from PIL import Image
                a = np.asarray(Image.open(io.BytesIO(data)).convert("RGB"), np.float32) / 255
                um = uv_mask(used, a.shape[1], a.shape[0], grow=0)
                if um is not None and um.any():
                    dark = float((a[..., 0][um] < 0.15).mean())
                    rep.warn("tex.%s.ao_black_used" % iname, dark < 0.02,
                             "%.1f%% of used texels have AO < 0.15: hidden faces inside other parts (delete them: primitives take top/bottom/skip flags) or parts absent in some LOD/state occluding the bake (gam.moved_faces)" % (100 * dark))

    # ---- trim-sheet strips / atlas regions from the project material library
    lib = load_material_lib(assets_root, spec) if assets_root else {}   # also used by texel_density
    for p in md0:
        mi = p["material"]
        if mi is None or p["uv0"] is None: continue
        mname = mats[mi].get("name", ""); e = lib.get(mname, {})
        if not (e.get("strips") or e.get("regions")): continue
        uv = p["uv0"].copy(); uv[:, 1] = 1 - uv[:, 1]            # back to Blender V (strips are Blender V)
        tv = uv[p["idx"]]; vmin, vmax = tv[..., 1].min(1), tv[..., 1].max(1); umin, umax = tv[..., 0].min(1), tv[..., 0].max(1)
        ok = np.zeros(len(tv), bool); eps = 1.0 / 2048
        for v0, v1 in (e.get("strips") or {}).values():
            ok |= (vmin >= v0 - eps) & (vmax <= v1 + eps)
        for u0, v0, u1, v1 in (e.get("regions") or {}).values():
            ok |= (vmin >= v0 - eps) & (vmax <= v1 + eps) & (umin >= u0 - eps) & (umax <= u1 + eps)
        bad = int((~ok).sum())
        rep.fail("uv.trim.%s" % mname, bad == 0, "%d of %d triangles bleed outside every strip/region of %s" % (bad, len(tv), mname))

    # ---- texel density (UV0 vs base color texture size)
    dens_cls = brief.get("density_class", cat["density_class"])
    target = brief.get("texel_density") or spec["texel_density"][dens_cls]
    tol = spec.get("texel_density_tolerance", 0.35); off = 0; rows = []
    for p in md0:
        n = len(p["pos"]); t = p["idx"]
        mi = p["material"]; pbr = mats[mi].get("pbrMetallicRoughness", {}) if mi is not None else {}
        if p["uv0"] is None or "baseColorTexture" not in pbr: continue
        from PIL import Image
        data, _ = image_bytes(g, binb, g["textures"][pbr["baseColorTexture"]["index"]]["source"])
        tw, th = Image.open(io.BytesIO(data)).size
        P = (W_of[lods[0]] @ np.c_[p["pos"], np.ones(n)].T).T[:, :3]
        wa = tri_area3(P, t).sum(); ua = tri_area2(p["uv0"], t).sum()
        if wa > 0: rows.append((mats[mi].get("name"), math.sqrt(ua * tw * th / wa), wa))   # non-square safe
    if rows:
        tot = sum(r[2] for r in rows); avg = sum(r[1] * r[2] for r in rows) / tot
        detail = "; ".join("%s %.0f px/m" % (r[0], r[1]) for r in rows)
        msg = "area-avg %.0f px/m vs %s target %d (+-%.0f%%): %s" % (avg, dens_cls, target, 100 * tol, detail)
        shared = assets_root and all(r[0] in lib for r in rows)
        if shared and avg > target * (1 + tol):
            # shared library/trim materials: extra density costs no memory -> only under-density fails
            rep.warn("texel_density", False, msg + " (over target on shared library materials: consistency only)")
        else:
            rep.fail("texel_density", abs(avg / target - 1) <= tol, msg)
    else:
        rep.info("texel_density", "no textured primitives")

    # ---- Khronos validator
    if khronos:
        js = os.path.join(TOOLS, "khronos_validate.mjs")
        if os.path.exists(os.path.join(TOOLS, "node_modules", "gltf-validator")):
            try:
                out = subprocess.run(["node", js, glb], capture_output=True, text=True, timeout=120)
                kr = json.loads(out.stdout)
                errs = kr["issues"]["numErrors"]; warns = kr["issues"]["numWarnings"]
                msgs = [m["code"] + ": " + m["message"] for m in kr["issues"]["messages"] if m["severity"] == 0][:4]
                rep.fail("khronos.errors", errs == 0, "%d errors, %d warnings %s" % (errs, warns, msgs))
            except Exception as e:
                rep.warn("khronos.run", False, str(e)[:200])
        else:
            rep.warn("khronos.installed", False, "run tools/setup.sh")
    return rep, g


def check_skins(g, binb, nodes, names, roots, lods, brief, spec, rep, r0):
    """Skinned characters (§0.7): joint names/count, hierarchy, influences, weights, morph names."""
    sk = spec.get("skeleton", {}); allowed = sk.get("bones", []); mx = sk.get("max_bones", 999)
    parent = {i: par for i, W, par, d in nodes}
    for si, skin in enumerate(g["skins"]):
        jn = [names.get(j, "?") for j in skin["joints"]]
        bad = [n for n in jn if n not in allowed]
        rep.fail("skin%d.joint_names" % si, not bad, "not in spec: %s" % bad[:8] if bad else "%d joints, all spec names" % len(jn))
        rep.fail("skin%d.joint_count" % si, len(jn) <= mx, "%d joints (max %d)" % (len(jn), mx))
        rep.fail("skin%d.inverse_bind" % si, "inverseBindMatrices" in skin, "inverseBindMatrices present")
        miss = [n for n in allowed if n not in jn]
        rep.warn("skin%d.full_skeleton" % si, not miss, "missing spec bones %s" % miss[:8] if miss else "all %d spec bones" % len(allowed))
    byname = {n: i for i, n in names.items()}
    if "root" in byname:
        ri = byname["root"]
        kids = [names[c] for c in g["nodes"][ri].get("children", [])]
        rep.fail("skel.root_children", kids == ["hips"], "root children %s (only hips allowed)" % kids)
        rep.fail("skel.root_parent", parent.get(ri) == roots[0], "root parent is the asset root node")
    # skinned primitives: only JOINTS_0/WEIGHTS_0, weights normalized, no NaN
    tol = spec["categories"].get(brief["category"], {}).get("char_rules", {}).get("weight_sum_tol", 0.01)
    worst = 0.0; extra_sets = []; unskinned = []
    for k, i in lods.items():
        n = g["nodes"][i]
        if "skin" not in n: unskinned.append(names[i]); continue
        for p in g["meshes"][n["mesh"]]["primitives"]:
            at = p["attributes"]
            if "JOINTS_1" in at or "WEIGHTS_1" in at: extra_sets.append(names[i])
            if "WEIGHTS_0" in at:
                W = accessor(g, binb, at["WEIGHTS_0"]); worst = max(worst, float(np.abs(W.sum(1) - 1).max()))
    rep.fail("skin.lods_skinned", not unskinned, "unskinned LOD nodes: %s" % unskinned if unskinned else "all LODs skinned")
    rep.fail("skin.max4", not extra_sets, "JOINTS_1/WEIGHTS_1 on %s" % extra_sets if extra_sets else "<= 4 influences (JOINTS_0 only)")
    rep.fail("skin.weights_normalized", worst <= tol, "max |sum-1| = %.4f" % worst)
    # morph targets
    kind = (r0.get("extras", {}) or {}).get("ig_kind") or brief.get("char", {}).get("kind", "")
    if kind == "head" and 0 in lods:
        mesh = g["meshes"][g["nodes"][lods[0]]["mesh"]]
        tn = (mesh.get("extras", {}) or {}).get("targetNames", [])
        req = sk.get("blendshapes_required_head", [])
        miss = [t for t in req if t not in tn]
        rep.fail("morph.required", not miss, "missing %s" % miss if miss else "%d targets incl. all %d required" % (len(tn), len(req)))
    leak = sorted(k for k in (r0.get("extras", {}) or {}) if not k.startswith("ig_"))
    rep.warn("props.foreign", not leak, "non-ig extras on root: %s" % leak[:6] if leak else "only ig_* extras")


def palette_check(rgb_srgb, mname, aid, spec, rep):
    pal = spec.get("palette")
    if not pal: return
    h, s, v = hsv(rgb_srgb)
    for rule in pal.get("accent_rules", []):
        rh, rs, rv = hsv(hex_rgb(rule["hex"]))
        dh = min(abs(h - rh), 1 - abs(h - rh)) * 360
        hit = dh <= rule["hue_tolerance_deg"] and s >= rule.get("min_saturation", 0) * 0.85 and v >= rule.get("min_value", 0)
        if rule.get("min_saturation", 0) == 0: hit = v >= rule.get("min_value", 1) and s < 0.12
        if hit:
            ok = re.match(rule["allowed_material_regex"], mname) or re.match(rule["allowed_asset_regex"], aid)
            rep.fail("palette.%s.%s" % (rule["name"], mname), bool(ok), "reads as %s (h %.0f s %.2f v %.2f): %s" % (rule["name"], h * 360, s, v, rule["why"]))
            return
    sw = min(((np.abs(hex_rgb(hx) - np.asarray(rgb_srgb)).max(), nm) for nm, hx in pal.get("swatches", {}).items()), default=(1, ""))
    if sw[0] < 0.06:
        rep.fail("palette.saturation.%s" % mname, True, "mean color matches swatch %s" % sw[1]); return
    rep.fail("palette.saturation.%s" % mname, s <= pal["max_saturation"],
             "mean color sRGB %s  sat %.2f (max %.2f unless an accent rule allows it)" % (np.round(rgb_srgb, 3).tolist(), s, pal["max_saturation"]))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("glb"); ap.add_argument("--brief", required=True); ap.add_argument("--spec")
    ap.add_argument("--placeholder", action="store_true"); ap.add_argument("--json"); ap.add_argument("--no-khronos", action="store_true")
    a = ap.parse_args()
    brief = json.load(open(a.brief))
    sys.path.insert(0, HERE)
    spec_path = a.spec or find_spec(a.brief)
    spec = json.load(open(spec_path))
    ph = a.placeholder or brief.get("status") == "placeholder"
    rep, _ = run(a.glb, brief, spec, placeholder=ph, khronos=not a.no_khronos, assets_root=os.path.dirname(spec_path))
    for it in rep.items:
        print("%-4s  %-34s %s" % (it["level"], it["check"], it["detail"]))
    nf = len(rep.failed)
    print("\nRESULT %s: %d fail, %d warn, %d pass" % ("FAIL" if nf else "PASS", nf,
          sum(1 for i in rep.items if i["level"] == "WARN"), sum(1 for i in rep.items if i["level"] == "PASS")))
    if a.json:
        json.dump({"glb": a.glb, "id": brief["id"], "result": "FAIL" if nf else "PASS", "items": rep.items}, open(a.json, "w"), indent=1)
    sys.exit(1 if nf else 0)


def load_material_lib(assets_root, spec):
    """Project material library = materials.json merged with materials/*.json (one file per
    family/owner so parallel agents never write the same file). Later files win on clashes."""
    out = {}
    p = os.path.join(assets_root, spec["paths"].get("materials", "materials.json"))
    if os.path.exists(p): out.update(json.load(open(p)))
    d = os.path.join(assets_root, spec["paths"].get("materials_dir", "materials"))
    if os.path.isdir(d):
        for f in sorted(os.listdir(d)):
            if f.endswith(".json"): out.update(json.load(open(os.path.join(d, f))))
    return out


def find_spec(start):
    env = os.environ.get("GAM_SPEC")
    if env and os.path.exists(env): return os.path.abspath(env)
    d = os.path.abspath(os.path.dirname(start))
    while True:
        for cand in (os.path.join(d, "spec.json") if os.path.basename(d) == "assets" else None, os.path.join(d, "assets", "spec.json")):
            if cand and os.path.exists(cand): return cand
        if os.path.dirname(d) == d: raise SystemExit("no assets/spec.json found above %s" % start)
        d = os.path.dirname(d)


if __name__ == "__main__":
    main()
