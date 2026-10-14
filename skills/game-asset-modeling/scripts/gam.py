"""
gam.py -- Blender-side helpers for game-asset-modeling.

Load at the top of a build script (headless `blender -b --python build.py`) or an
MCP `execute_blender_code` call:

    import os, sys; sys.path.insert(0, os.path.expanduser("~/.claude/skills/game-asset-modeling/scripts"))
    import gam; ctx = gam.Ctx.from_argv()        # reads --brief / --spec after "--"

Everything here is context-free where possible (bmesh + data API, not bpy.ops), so
it runs identically headless and over MCP. Axes follow the project spec; defaults:
Blender Z up, model front faces -Y, 1 unit = 1 m, glTF export +Y up (front -> +Z).
"""
import bpy, bmesh, json, math, os, random, shutil, sys
from mathutils import Vector, Matrix

# --------------------------------------------------------------------------- context

from validate_glb import find_spec   # one implementation shared with the validator


class Ctx:
    """Brief + spec + resolved paths for one asset."""

    def __init__(self, brief_path, spec_path=None):
        self.brief_path = os.path.abspath(brief_path)
        self.brief = json.load(open(self.brief_path))
        self.spec_path = spec_path or find_spec(self.brief_path)
        self.spec = json.load(open(self.spec_path))
        self.assets_root = os.path.dirname(self.spec_path)          # .../assets
        self.id = self.brief["id"]
        self.cat = self.spec["categories"][self.brief["category"]]
        self.placeholder = self.brief.get("status") == "placeholder"
        exp = self.spec["paths"]["placeholder" if self.placeholder else "export"]
        self.glb = os.path.join(self.assets_root, exp, self.cat["export_dir"], self.id + ".glb")
        self.src_dir = os.path.dirname(self.brief_path)
        self.blend = os.path.join(self.src_dir, self.id + ".blend")
        self.tex_dir = os.path.join(self.assets_root, self.spec["paths"]["tex"])
        self.bake_dir = os.path.join(self.src_dir, "_bake")     # per-asset temp bakes (parallel-safe)

    @classmethod
    def from_argv(cls):
        argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
        brief = spec = None
        for i, a in enumerate(argv):
            if a == "--brief": brief = argv[i + 1]
            if a == "--spec": spec = argv[i + 1]
        if not brief:
            raise SystemExit("pass -- --brief path/to/brief.json")
        return cls(brief, spec)

    # spec lookups with brief overrides
    def budget(self):
        return self.brief.get("tri_budget") or self.spec["budgets"][self.brief.get("budget_class", self.cat["budget_class"])]

    def density(self):
        return self.brief.get("texel_density") or self.spec["texel_density"][self.brief.get("density_class", self.cat["density_class"])]

    def pivot(self):
        return self.brief.get("pivot", self.cat["pivot"])


# --------------------------------------------------------------------------- scene

def reset():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    us = bpy.context.scene.unit_settings
    us.system, us.scale_length, us.length_unit = "METRIC", 1.0, "METERS"


def link(obj):
    bpy.context.scene.collection.objects.link(obj)
    return obj


def obj_from_bm(name, bm, mats=None):
    me = bpy.data.meshes.new(name)
    bm.normal_update(); bm.to_mesh(me); bm.free()
    ob = link(bpy.data.objects.new(name, me))
    for m in mats or []:
        me.materials.append(m)
    return ob


def _uv_layer(bm, name="UVMap"):
    return bm.loops.layers.uv.get(name) or bm.loops.layers.uv.new(name)


def _face(bm, verts, outward=None, uvs=None, mat=0):
    """New face; flipped if its normal points against `outward` (a direction or a point the
    normal should point AWAY from, given as ('from', point)). uvs: per-vertex (u, v) meters."""
    f = bm.faces.new(verts); f.material_index = mat; f.normal_update()
    if outward is not None:
        if isinstance(outward, tuple) and outward and outward[0] == "from":
            d = f.calc_center_median() - Vector(outward[1])
        else:
            d = Vector(outward)
        if f.normal.dot(d) < 0:
            f.normal_flip()
    if uvs is not None:
        lay = _uv_layer(bm); m = {v: uv for v, uv in zip(verts, uvs)}
        for l in f.loops: l[lay].uv = m[l.vert]
    return f


def bm_box(bm, lo, hi, mat=0, skip=()):
    """Axis-aligned box lo..hi. UVs in meters (box-projected per face) so any assembly packs
    at one density. skip: subset of bottom/top/front/back/left/right (front = -Y).
    Returns faces in order bottom, top, front, right, back, left (skipped ones omitted)."""
    (x0, y0, z0), (x1, y1, z1) = lo, hi
    v = [bm.verts.new(p) for p in [(x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0),
                                   (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)]]
    spec = [("bottom", (0, 3, 2, 1), (0, 0, -1), lambda p: (p[0], -p[1])),
            ("top", (4, 5, 6, 7), (0, 0, 1), lambda p: (p[0], p[1])),
            ("front", (0, 1, 5, 4), (0, -1, 0), lambda p: (p[0], p[2])),
            ("right", (1, 2, 6, 5), (1, 0, 0), lambda p: (p[1], p[2])),
            ("back", (2, 3, 7, 6), (0, 1, 0), lambda p: (-p[0], p[2])),
            ("left", (3, 0, 4, 7), (-1, 0, 0), lambda p: (-p[1], p[2]))]
    out = []
    for nm, idx, n, proj in spec:
        if nm in skip: continue
        vs = [v[i] for i in idx]
        out.append(_face(bm, vs, n, [proj(x.co) for x in vs], mat))
    for x in v:
        if not x.link_faces: bm.verts.remove(x)
    return out


def bm_cylinder(bm, r0, z0, z1, n=16, center=(0.0, 0.0), r1=None, top=True, bottom=True,
                phase=None, mat=0, matrix=None):
    """Z-axis cylinder/frustum (r0 at z0, r1 at z1). Side unrolled in meters (u = arc length,
    seam at +Y/back, v = slant height); caps planar. `phase` fixes ring orientation so a
    lower-LOD cylinder with n/2 sides is a vertex subset. `matrix` places it afterwards."""
    r1 = r0 if r1 is None else r1
    ph = (math.pi / 2 + math.pi / n) if phase is None else phase
    cx, cy = center
    ang = [ph + 2 * math.pi * i / n for i in range(n + 1)]
    lo = [bm.verts.new((cx + r0 * math.cos(a), cy + r0 * math.sin(a), z0)) for a in ang[:n]]
    hi = [bm.verts.new((cx + r1 * math.cos(a), cy + r1 * math.sin(a), z1)) for a in ang[:n]]
    rm = (r0 + r1) / 2; slant = math.hypot(z1 - z0, r1 - r0)
    fs = []
    for i in range(n):
        j = (i + 1) % n
        u0, u1 = 2 * math.pi * rm * i / n, 2 * math.pi * rm * (i + 1) / n
        fs.append(_face(bm, [lo[i], lo[j], hi[j], hi[i]], ("from", (cx, cy, (lo[i].co.z + hi[i].co.z) / 2)),
                        [(u0, 0), (u1, 0), (u1, slant), (u0, slant)], mat))
    if top:
        fs.append(_face(bm, hi, (0, 0, 1), [(x.co.x, x.co.y) for x in hi], mat))
    if bottom:
        fs.append(_face(bm, lo, (0, 0, -1), [(x.co.x, -x.co.y) for x in lo], mat))
    if matrix is not None:
        bmesh.ops.transform(bm, matrix=matrix, verts=list({v for f in fs for v in f.verts}))
    # re-orient after placement (and for reversed z ranges): caps face away from the solid
    axis = Vector((0, 0, 1 if z1 >= z0 else -1)); c0 = Vector((cx, cy, z0)); c1 = Vector((cx, cy, z1))
    if matrix is not None:
        axis = (matrix.to_3x3() @ axis).normalized(); c0 = matrix @ c0; c1 = matrix @ c1
    mid = (c0 + c1) / 2
    for f in fs:
        f.normal_update()
        if f.normal.dot(f.calc_center_median() - mid) < 0: f.normal_flip()
    return fs


def _frames(pts):
    """Parallel-transport frames (tangent, normal) along a polyline (no twisting)."""
    T = []
    for i in range(len(pts)):
        a = pts[max(i - 1, 0)]; b = pts[min(i + 1, len(pts) - 1)]
        T.append((b - a).normalized())
    ref = Vector((0, 0, 1)) if abs(T[0].z) < 0.9 else Vector((1, 0, 0))
    N = [T[0].cross(ref).normalized()]
    for i in range(1, len(pts)):
        n = N[-1] - T[i] * N[-1].dot(T[i]); N.append(n.normalized())
    return T, N


def bm_sweep(bm, pts, r, n=8, cap0=True, cap1=True, square=False, mat=0):
    """Tube (or square section of half-width r) along polyline pts: pipes, cables, rails,
    bent brackets. UVs: u = around * r (meters), v = distance along the path."""
    pts = [Vector(p) for p in pts]; T, N = _frames(pts)
    rr = r * (math.sqrt(2) if square else 1)
    rings = []
    for p, t, nn in zip(pts, T, N):
        bn = t.cross(nn)
        rings.append([bm.verts.new(p + (nn * math.cos(a) + bn * math.sin(a)) * rr)
                      for a in (2 * math.pi * k / n + (math.pi / 4 if square else 0) for k in range(n))])
    dist = [0.0]
    for a, b in zip(pts[:-1], pts[1:]): dist.append(dist[-1] + (b - a).length)
    circ = 2 * math.pi * r if not square else 8 * r
    fs = []
    for i, (a, b) in enumerate(zip(rings[:-1], rings[1:])):
        mid = (pts[i] + pts[i + 1]) / 2
        for k in range(n):
            j = (k + 1) % n
            u0, u1 = circ * k / n, circ * (k + 1) / n
            fs.append(_face(bm, [a[k], a[j], b[j], b[k]], ("from", mid),
                            [(u0, dist[i]), (u1, dist[i]), (u1, dist[i + 1]), (u0, dist[i + 1])], mat))
    if cap0: fs.append(_face(bm, rings[0], -T[0], [(v.co - pts[0]).to_2d()[:] for v in rings[0]], mat))
    if cap1: fs.append(_face(bm, rings[-1], T[-1], [(v.co - pts[-1]).to_2d()[:] for v in rings[-1]], mat))
    return fs


def bm_prism(bm, poly, offset, mat=0, caps=True):
    """Extrude a planar polygon (list of 3D points, any winding) by vector `offset` into a
    closed solid with outward normals: gussets, brackets, profiles, sign plates.
    UVs: sides unrolled (u = perimeter, v = along offset), caps planar in meters."""
    off = Vector(offset); P = [Vector(p) for p in poly]; n = len(P)
    a = [bm.verts.new(p) for p in P]; b = [bm.verts.new(p + off) for p in P]
    L = off.length; per = 0.0; fs = []
    ax = off.normalized(); e1 = (P[1] - P[0]).normalized(); e2 = ax.cross(e1)
    # polygon winding about the extrusion axis (Newell): works for concave profiles, where
    # "away from the centroid" flips the inner faces of a U or L
    nw = Vector()
    for i in range(n):
        p0, p1 = P[i], P[(i + 1) % n]
        nw += Vector(((p0.y - p1.y) * (p0.z + p1.z), (p0.z - p1.z) * (p0.x + p1.x), (p0.x - p1.x) * (p0.y + p1.y)))
    wind = 1.0 if nw.dot(ax) >= 0 else -1.0
    for i in range(n):
        j = (i + 1) % n; seg = (P[j] - P[i]).length
        outward = (P[j] - P[i]).cross(ax) * wind
        fs.append(_face(bm, [a[i], a[j], b[j], b[i]], outward,
                        [(per, 0), (per + seg, 0), (per + seg, L), (per, L)], mat))
        per += seg
    if caps:
        proj = lambda v: ((v.co - P[0]).dot(e1), (v.co - P[0]).dot(e2))
        fs.append(_face(bm, a, -ax, [proj(v) for v in a], mat))
        fs.append(_face(bm, b, ax, [proj(v) for v in b], mat))
    return fs


def bm_profile_sweep(bm, path, profile, plane_normal=(0, -1, 0), strip=None, u_m=1.0,
                     mat=0, caps=False, flip=False, u_start=0.0, u_axis=None):
    """Sweep a 2D moulding profile along a path lying in a plane: cornices, string courses,
    sills, architraves around an opening (mitred corners), skirting, handrails, kerbs.

    path: 3D points in the plane (open polyline; corners are mitred).
    profile: [(a, d), ...] where a = in-plane offset to the path's LEFT (perpendicular to
      the path, inside the plane) and d = offset along plane_normal (out of the wall).
      E.g. a cornice running along +X on a facade whose outside is -Y: plane_normal (0,-1,0),
      a = height above the path line, d = projection from the wall. Normals face away from
      the solid (the profile closed back along the wall at d=0); pass flip=True if not.
    strip: (v0, v1) trim-sheet strip; V follows cumulative profile length inside it, U =
      (u_start + path distance) / u_m. Without strip, UVs are meters (then uv_pack).
    u_axis: 'x'|'y'|'z' -> U from that WORLD coordinate instead (seam-absolute: mouldings on
      neighbouring kit modules continue across the module line).
    caps: close the two path ends with the profile shape."""
    P = [Vector(p) for p in path]; pn = Vector(plane_normal).normalized(); n = len(P)
    seg_t = [(P[i + 1] - P[i]).normalized() for i in range(n - 1)]
    sides = []
    for i in range(n):
        if i == 0: t0 = t1 = seg_t[0]
        elif i == n - 1: t0 = t1 = seg_t[-1]
        else: t0, t1 = seg_t[i - 1], seg_t[i]
        s0 = pn.cross(t0).normalized(); s1 = pn.cross(t1).normalized()
        m = (s0 + s1); m = m.normalized() if m.length > 1e-6 else s0
        sides.append(m / max(m.dot(s0), 0.2))                   # mitre: longer at corners
    dist = [0.0]
    for i in range(n - 1): dist.append(dist[-1] + (P[i + 1] - P[i]).length)
    cum = [0.0]
    for (a0, d0), (a1, d1) in zip(profile, profile[1:]): cum.append(cum[-1] + math.hypot(a1 - a0, d1 - d0))
    L = max(cum[-1], 1e-9)
    # orientation: close the profile along the wall (d = 0) and take its signed area in (a, d)
    ring = list(profile) + ([(profile[-1][0], 0.0), (profile[0][0], 0.0)] if profile[-1][1] or profile[0][1] else [])
    area = sum(x0 * y1 - x1 * y0 for (x0, y0), (x1, y1) in zip(ring, ring[1:] + ring[:1])) / 2
    sgn = (1 if area > 0 else -1) * (-1 if flip else 1)
    V = [[bm.verts.new(P[i] + sides[i] * a + pn * d) for (a, d) in profile] for i in range(n)]
    fs = []
    for j in range(len(profile) - 1):
        (a0, d0), (a1, d1) = profile[j], profile[j + 1]
        ta, td = a1 - a0, d1 - d0
        na, nd = td * sgn, -ta * sgn                               # right-hand normal in (a, d)
        if strip: vv = (strip[0] + cum[j] / L * (strip[1] - strip[0]), strip[0] + cum[j + 1] / L * (strip[1] - strip[0]))
        else: vv = (cum[j], cum[j + 1])
        for i in range(n - 1):
            side = (sides[i] + sides[i + 1]).normalized()
            out = side * na + pn * nd
            if u_axis:
                ax_i = "xyz".index(u_axis); u0, u1 = P[i][ax_i] / u_m, P[i + 1][ax_i] / u_m
            else:
                u0, u1 = (u_start + dist[i]) / u_m, (u_start + dist[i + 1]) / u_m
            fs.append(_face(bm, [V[i][j], V[i + 1][j], V[i + 1][j + 1], V[i][j + 1]], out,
                            [(u0, vv[0]), (u1, vv[0]), (u1, vv[1]), (u0, vv[1])], mat))
    if caps:
        amin = min(a for a, _ in ring); amax = max(a for a, _ in ring); dmax = max(abs(d) for _, d in ring) or 1.0
        for i, t in ((0, -seg_t[0]), (n - 1, seg_t[-1])):
            cap = list(V[i])                                        # reuse the ring: welded, manifold
            if len(ring) > len(profile):                            # wall-closing points (d = 0)
                cap += [bm.verts.new(P[i] + sides[i] * a + pn * d) for (a, d) in ring[len(profile):]]
            if strip:   # squeeze the cap into the strip so trim-strip validation holds
                uvs = [(abs(d) / dmax * 0.25, strip[0] + (a - amin) / max(amax - amin, 1e-9) * (strip[1] - strip[0])) for a, d in ring]
            else:
                uvs = [(d, a) for a, d in ring]
            fs.append(_face(bm, cap, t, uvs, mat))
    return fs


def rotate_about(point, axis, deg):
    """Matrix rotating `deg` degrees about `axis` ('X'|'Y'|'Z' or vector) through `point`."""
    p = Vector(point)
    R = Matrix.Rotation(math.radians(deg), 4, axis if isinstance(axis, str) else Vector(axis))
    return Matrix.Translation(p) @ R @ Matrix.Translation(-p)


def box(name, size, center=(0, 0, 0), base_z=None, mats=None):
    """Box object. `base_z` puts its bottom at that height instead of centering on center.z."""
    sx, sy, sz = size; cx, cy, cz = center
    if base_z is not None: cz = base_z + sz / 2
    bm = bmesh.new()
    bm_box(bm, (cx - sx / 2, cy - sy / 2, cz - sz / 2), (cx + sx / 2, cy + sy / 2, cz + sz / 2))
    return obj_from_bm(name, bm, mats)


def tag_parts(objs, attr="part"):
    """Write an integer face attribute = index into each object, so the parts can be told
    apart after join() (for part-based breakage that keeps the joined mesh's UVs)."""
    for i, o in enumerate(objs):
        a = o.data.attributes.get(attr) or o.data.attributes.new(attr, "INT", "FACE")
        a.data.foreach_set("value", [i] * len(o.data.polygons))


def split_parts(obj, groups, name_fmt="chunk_{:02d}", attr="part"):
    """Copies of obj keeping only faces whose `attr` is in each group (UVs, materials and
    normals are inherited, so chunks match the baked textures of the intact mesh)."""
    out = []
    for gi, g in enumerate(groups):
        keep = set(g)
        bm = bmesh.new(); bm.from_mesh(obj.data)
        lay = bm.faces.layers.int.get(attr)
        bmesh.ops.delete(bm, geom=[f for f in bm.faces if f[lay] not in keep], context="FACES")
        me = bpy.data.meshes.new(name_fmt.format(gi)); bm.to_mesh(me); bm.free()
        for m in obj.data.materials: me.materials.append(m)
        o = link(bpy.data.objects.new(me.name, me)); o.matrix_world = obj.matrix_world.copy()
        out.append(o)
    return out


def join(objs, name):
    """Join mesh objects into one (data-level, no ops). Materials are merged by name."""
    bm = bmesh.new(); mats = []
    for o in objs:
        me = o.data.copy(); me.transform(o.matrix_world)
        remap = []
        for m in me.materials:
            if m not in mats: mats.append(m)
            remap.append(mats.index(m))
        for p in me.polygons:
            p.material_index = remap[p.material_index] if remap else 0
        bm.from_mesh(me); bpy.data.meshes.remove(me)
    for o in objs: bpy.data.objects.remove(o, do_unlink=True)
    return obj_from_bm(name, bm, mats)


def apply_transforms(obj, location=False):
    """Bake rotation/scale (and optionally location) into mesh data. No ops, no context."""
    M = obj.matrix_basis.copy()
    if location:
        obj.data.transform(M); obj.matrix_basis = Matrix.Identity(4)
    else:
        loc = M.translation.copy(); M.translation = (0, 0, 0)
        obj.data.transform(M); obj.matrix_basis = Matrix.Translation(loc)
    obj.data.update()


def apply_modifiers(obj):
    """Evaluate the modifier stack into the mesh (keeps custom normals and UV layers)."""
    dg = bpy.context.evaluated_depsgraph_get()
    ev = obj.evaluated_get(dg)
    me = bpy.data.meshes.new_from_object(ev, preserve_all_data_layers=True, depsgraph=dg)
    old = obj.data; name = old.name; obj.modifiers.clear(); obj.data = me
    if old.users == 0: bpy.data.meshes.remove(old)
    me.name = name                                  # after removal, so no ".001" suffix


def bounds(obj, world=True):
    pts = [obj.matrix_world @ v.co if world else v.co.copy() for v in obj.data.vertices]
    lo = Vector((min(p.x for p in pts), min(p.y for p in pts), min(p.z for p in pts)))
    hi = Vector((max(p.x for p in pts), max(p.y for p in pts), max(p.z for p in pts)))
    return lo, hi


def center_of_mass(obj):
    """Volume centroid of a closed mesh (signed tetrahedra). Falls back to bbox center."""
    bm = bmesh.new(); bm.from_mesh(obj.data)
    bmesh.ops.triangulate(bm, faces=bm.faces[:])
    vol = 0.0; c = Vector()
    for f in bm.faces:
        a, b, d = (v.co for v in f.verts)
        v6 = a.dot(b.cross(d)); vol += v6; c += v6 * (a + b + d) / 4
    bm.free()
    if abs(vol) < 1e-9:
        lo, hi = bounds(obj, world=False); return (lo + hi) / 2, 0.0
    return c / vol, vol / 6


def set_pivot(obj, rule, point=None):
    """Move the origin to the spec's pivot rule and place the object on the ground at the origin.

    rules: bottom_center | bottom_left_back | center_of_mass | point (pass `point`, local coords)
    bottom_left_back = (min X, max Y, min Z) in Blender = glTF bbox min corner (front faces -Y).
    """
    apply_transforms(obj, location=True)
    lo, _ = bounds(obj, world=False)
    p = pivot_point(obj, rule, point)
    obj.data.transform(Matrix.Translation(-p)); obj.data.update()
    obj.location = (0, 0, p.z - lo.z) if rule == "center_of_mass" else (0, 0, 0)
    return p


# --------------------------------------------------------------------------- shading / topology

def shade_hard_surface(obj, angle_deg=30.0, weighted=True):
    """Smooth + sharp-by-angle + applied Weighted Normal: flats stay flat, bevels read round."""
    me = obj.data
    me.shade_smooth()
    me.set_sharp_from_angle(angle=math.radians(angle_deg))
    if weighted:
        m = obj.modifiers.new("WN", "WEIGHTED_NORMAL"); m.keep_sharp = True; m.mode = "FACE_AREA"
        apply_modifiers(obj)


def bevel(obj, width, segments=2, angle_deg=30.0, limit="ANGLE"):
    m = obj.modifiers.new("Bevel", "BEVEL")
    m.width, m.segments, m.limit_method = width, segments, limit
    m.angle_limit = math.radians(angle_deg); m.harden_normals = False
    m.use_clamp_overlap = True
    apply_modifiers(obj)


def triangulate_ngons(obj):
    bm = bmesh.new(); bm.from_mesh(obj.data)
    ngons = [f for f in bm.faces if len(f.verts) > 4]
    if ngons: bmesh.ops.triangulate(bm, faces=ngons, quad_method="BEAUTY", ngon_method="BEAUTY")
    bm.to_mesh(obj.data); bm.free(); obj.data.update()
    return len(ngons)


def mesh_report(obj):
    bm = bmesh.new(); bm.from_mesh(obj.data)
    r = dict(verts=len(bm.verts), faces=len(bm.faces),
             tris=sum(len(f.verts) - 2 for f in bm.faces),
             ngons=sum(1 for f in bm.faces if len(f.verts) > 4),
             non_manifold_edges=sum(1 for e in bm.edges if not e.is_manifold),
             loose_verts=sum(1 for v in bm.verts if not v.link_edges))
    bm.free(); return r


def tris(obj):
    return sum(len(p.vertices) - 2 for p in obj.data.polygons)


# --------------------------------------------------------------------------- UVs

def _ensure_uv(me, name, index=None):
    uv = me.uv_layers.get(name) or me.uv_layers.new(name=name)
    return uv


def uv_world_box(obj, meters_per_tile, layer="UVMap", offset=(0.0, 0.0), faces=None):
    """Box-project UVs in world meters: exact texel density for tiling / trim materials.
    density(px/m) = texture_px / meters_per_tile. `faces`: optional polygon indices."""
    me = obj.data; uv = _ensure_uv(me, layer).data
    M = obj.matrix_world
    sel = set(faces) if faces is not None else None
    for p in me.polygons:
        if sel is not None and p.index not in sel: continue
        n = (M.to_3x3() @ p.normal).normalized()
        ax = max(range(3), key=lambda i: abs(n[i]))
        for li in p.loop_indices:
            co = M @ me.vertices[me.loops[li].vertex_index].co
            if ax == 0: u, v = (co.y if n.x > 0 else -co.y), co.z      # side walls
            elif ax == 1: u, v = (-co.x if n.y > 0 else co.x), co.z    # front(-Y)/back
            else: u, v = co.x, (co.y if n.z > 0 else -co.y)            # top/bottom
            uv[li].uv = (u / meters_per_tile + offset[0], v / meters_per_tile + offset[1])


def uv_trim(obj, faces, v0, v1, meters_per_u, u_offset=0.0, layer="UVMap"):
    """Map faces onto one horizontal strip of a trim sheet: U runs along the face's longest
    in-plane axis in world meters (tiles), V spans [v0, v1] across the face's extent."""
    me = obj.data; uv = _ensure_uv(me, layer).data; M = obj.matrix_world
    for fi in faces:
        p = me.polygons[fi]
        n = (M.to_3x3() @ p.normal).normalized()
        cos = [M @ me.vertices[me.loops[li].vertex_index].co for li in p.loop_indices]
        # in-plane basis: along = horizontal-ish if the face is vertical, else X
        up = Vector((0, 0, 1))
        if abs(n.z) < 0.9:
            along = up.cross(n).normalized(); across = n.cross(along).normalized()
        else:
            along = Vector((1, 0, 0)); across = n.cross(along).normalized()
        a = [c.dot(along) for c in cos]; b = [c.dot(across) for c in cos]
        if max(b) - min(b) > max(a) - min(a):     # long axis is `across`: swap
            a, b = b, a
        bmin, bmax = min(b), max(b); span = max(bmax - bmin, 1e-6)
        for li, ai, bi in zip(p.loop_indices, a, b):
            uv[li].uv = (ai / meters_per_u + u_offset, v0 + (bi - bmin) / span * (v1 - v0))


def select_only(objs, active=None):
    """Deselect all, select objs, set active. Refreshes the view layer first: after data-level
    object removal (join, collider cleanup) its object list can hold stale entries."""
    vl = bpy.context.view_layer; vl.update()
    for o in bpy.context.scene.objects:
        if o.name in vl.objects: o.select_set(False)
    for o in objs: o.select_set(True)
    vl.objects.active = active or (objs[0] if objs else None)


def edit_mode(obj):
    """Enter edit mode on obj with everything selected (for bpy.ops.mesh/uv operators)."""
    _edit(obj)


def _edit(obj):
    if bpy.context.object and bpy.context.object.mode != "OBJECT": bpy.ops.object.mode_set(mode="OBJECT")
    select_only([obj])
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")


def uv_smart(obj, layer="UVMap", angle_deg=66.0, margin_frac=0.004):
    """Smart-project into [0,1] (for unique/baked textures)."""
    me = obj.data; uv = _ensure_uv(me, layer); me.uv_layers.active = uv
    _edit(obj)
    bpy.ops.uv.smart_project(angle_limit=math.radians(angle_deg), island_margin=margin_frac,
                             area_weight=0.0, correct_aspect=True, scale_to_bounds=False)
    bpy.ops.uv.pack_islands(rotate=True, margin_method="FRACTION", margin=margin_frac, shape_method="CONCAVE")
    bpy.ops.object.mode_set(mode="OBJECT")


def uv_lightmap(obj, spec_uv):
    """Second UV set for the district lightmap bake: non-overlapping, padded, in [0,1]."""
    me = obj.data
    if spec_uv["uv0"] not in me.uv_layers:
        raise RuntimeError("make UV0 before the lightmap UV so UV0 stays TEXCOORD_0")
    uv = _ensure_uv(me, spec_uv["uv1"]); me.uv_layers.active = uv
    pad = spec_uv["lightmap_padding_px"] / spec_uv["lightmap_res"]
    _edit(obj)
    bpy.ops.uv.smart_project(angle_limit=math.radians(60), island_margin=pad * 3,
                             area_weight=0.0, correct_aspect=True, scale_to_bounds=False)
    bpy.ops.uv.pack_islands(rotate=True, margin_method="FRACTION", margin=pad * 3.2,
                            shape_method="CONCAVE")     # 1.6x failed uv1.padding on many-island pieces
    bpy.ops.object.mode_set(mode="OBJECT")
    me.uv_layers.active = me.uv_layers[spec_uv["uv0"]]
    me.uv_layers[spec_uv["uv0"]].active_render = True


def texel_density(obj, tex_px, layer="UVMap", tex_h=None):
    """Average px/m of `layer` for a texture of tex_px (x tex_h for non-square), area-weighted."""
    me = obj.data; uv = me.uv_layers[layer].data; M = obj.matrix_world
    wa = ua = 0.0
    for p in me.polygons:
        li = list(p.loop_indices)
        w = [M @ me.vertices[me.loops[i].vertex_index].co for i in li]
        for k in range(1, len(li) - 1):
            wa += (w[k] - w[0]).cross(w[k + 1] - w[0]).length / 2
            a, b, c = uv[li[0]].uv, uv[li[k]].uv, uv[li[k + 1]].uv
            ua += abs((b.x - a.x) * (c.y - a.y) - (c.x - a.x) * (b.y - a.y)) / 2
    return math.sqrt(ua * tex_px * (tex_h or tex_px) / wa) if wa > 0 else 0.0


def texture_size_for(obj, px_per_m, efficiency=0.65, max_px=2048):
    """ESTIMATE before unwrapping (planning only; after packing use choose_texture_size, which
    measures). Power-of-two size giving px_per_m with a unique layout packed at `efficiency`. Returns (size, predicted_density). If size would exceed max_px the
    asset needs shared/overlapping UVs, a tiling or trim material, or a lower density class."""
    area = sum(p.area for p in obj.data.polygons) * obj.matrix_world.to_scale().x ** 2
    need = px_per_m * math.sqrt(area / efficiency)
    size = 2 ** math.ceil(math.log2(max(need, 64)))
    size = min(size, max_px)
    return size, size * math.sqrt(efficiency / area)


def uv_pack(obj, margin_frac=0.006, layer="UVMap", rotate=True):
    """Pack all islands of `layer` into [0,1] with ONE uniform scale (relative island sizes
    kept). Use after building from gam primitives, whose UVs are all in meters, so the whole
    asset lands at a single density. Returns the density factor (px/m per texture px)."""
    me = obj.data; me.uv_layers.active = me.uv_layers[layer]
    _edit(obj)
    bpy.ops.uv.select_all(action="SELECT")
    bpy.ops.uv.pack_islands(rotate=rotate, rotate_method="CARDINAL", scale=True, margin_method="FRACTION",
                            margin=margin_frac, shape_method="CONCAVE")   # 90° steps: straight parts stay straight
    bpy.ops.object.mode_set(mode="OBJECT")
    return texel_density(obj, 1, layer)


def choose_texture_size(obj, target_px_m, tol=0.35, sizes=(64, 128, 256, 512, 1024, 2048), layer="UVMap"):
    """Measure the REAL packed layout at each size and return (size, density) closest to the
    target. The validator fails both under- and over-density (|d/target - 1| > tol), so the
    next power of two up is often wrong. If nothing is within tol, the layout is wasting space
    (improve packing / share UVs) or the asset needs a tiling material."""
    best = min(sizes, key=lambda px: abs(texel_density(obj, px, layer) / target_px_m - 1))
    d = texel_density(obj, best, layer)
    if abs(d / target_px_m - 1) > tol:
        # between two powers of two: shrink UVs (keeps packing, adds margin) so the smaller
        # size lands inside tolerance, rather than overshooting with the bigger one
        cands = [px for px in sizes if texel_density(obj, px, layer) > target_px_m]
        if cands:
            px = min(cands); f = (target_px_m * (1 + tol * 0.8)) / texel_density(obj, px, layer)
            if f < 1:
                for dd in obj.data.uv_layers[layer].data: dd.uv *= f
                best, d = px, texel_density(obj, px, layer)
        if abs(d / target_px_m - 1) > tol:
            print("GAM_WARN texture size: best %d gives %.0f px/m vs %d target" % (best, d, target_px_m))
    return best, d


def transfer_uvs(src, dst, layer="UVMap", normal_slack=0.05):
    """Copy UVs from src onto a simplified version dst (hand-built LODs, collision-free
    variants): each dst face takes the affine plane->UV map of the src face with the
    best-matching normal (ties: nearest center). Works for unbeveled vs beveled boxes and for
    fewer-sided cylinders built with the same `phase`. Both in the same space."""
    import numpy as np
    sm = src.data; suv = sm.uv_layers[layer].data; S = []
    Ms, Md = src.matrix_world, dst.matrix_world
    for p in sm.polygons:
        P = np.array([Ms @ sm.vertices[sm.loops[li].vertex_index].co for li in p.loop_indices])
        U = np.array([suv[li].uv[:] for li in p.loop_indices])
        n = np.array((Ms.to_3x3() @ p.normal).normalized()); c = P.mean(0)
        e1 = P[1] - P[0]; e1 /= (np.linalg.norm(e1) + 1e-12); e2 = np.cross(n, e1)
        X = np.c_[np.ones(len(P)), (P - c) @ e1, (P - c) @ e2]
        coef, *_ = np.linalg.lstsq(X, U, rcond=None)
        lo, hi = U.min(0), U.max(0); pad = (hi - lo) * 0.25 + 1e-4
        S.append((c, e1, e2, coef, lo - pad, hi + pad))       # clamp box: no extrapolation off the atlas
    SN = np.array([np.array((Ms.to_3x3() @ p.normal).normalized()) for p in sm.polygons])
    SC = np.array([t[0] for t in S])
    dm = dst.data; duv = _ensure_uv(dm, layer).data
    for p in dm.polygons:
        n = np.array((Md.to_3x3() @ p.normal).normalized())
        c = np.array(Md @ p.center)
        dots = SN @ n; cand = np.where(dots >= dots.max() - normal_slack)[0]
        k = cand[np.argmin(np.linalg.norm(SC[cand] - c, axis=1))]
        sc, e1, e2, coef, ulo, uhi = S[k]
        for li in p.loop_indices:
            v = np.array(Md @ dm.vertices[dm.loops[li].vertex_index].co) - sc
            duv[li].uv = tuple(np.clip(np.array([1.0, v @ e1, v @ e2]) @ coef, ulo, uhi))


class moved_faces:
    """Context manager: temporarily translate faces whose int attribute `attr` is in `groups`
    (e.g. sabotage-only or lower-LOD-only parts) so they don't occlude the AO bake of the rest.
        with gam.moved_faces(obj, "grp", {G_SAB}, (4, 0, 0)): gam.bake(obj, "AO", ...)"""
    def __init__(self, obj, attr, groups, offset):
        self.obj, self.attr, self.groups, self.off = obj, attr, set(groups), Vector(offset)
    def _shift(self, sign):
        me = self.obj.data; lay = me.attributes[self.attr].data
        vs = {v for p in me.polygons if lay[p.index].value in self.groups for v in p.vertices}
        for i in vs: me.vertices[i].co += self.off * sign
        me.update()
    def __enter__(self): self._shift(1); return self
    def __exit__(self, *a): self._shift(-1)


def scale_uv_to_density(obj, target_px_m, tex_px, layer="UVMap"):
    """Uniformly scale a UV layer (about its bbox min) so the average density hits target."""
    cur = texel_density(obj, tex_px, layer)
    if cur <= 0: return 0
    s = target_px_m / cur
    for d in obj.data.uv_layers[layer].data: d.uv *= s
    return s


# --------------------------------------------------------------------------- materials

def _gltf_output_group():
    ng = bpy.data.node_groups.get("glTF Material Output")
    if ng: return ng
    ng = bpy.data.node_groups.new("glTF Material Output", "ShaderNodeTree")
    ng.interface.new_socket(name="Occlusion", in_out="INPUT", socket_type="NodeSocketFloat")
    return ng


def _img(path, noncolor):
    img = bpy.data.images.load(path, check_existing=True)
    img.colorspace_settings.name = "Non-Color" if noncolor else "sRGB"
    return img


def srgb_to_linear(c):
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def hex_rgba(h, a=1.0):
    h = h.lstrip("#"); return tuple(srgb_to_linear(int(h[i:i + 2], 16) / 255) for i in (0, 2, 4)) + (a,)


def material(name, base=(0.5, 0.5, 0.5, 1), roughness=0.85, metallic=0.0, orm=None, normal=None,
             emissive=None, emissive_strength=1.0, alpha=None, normal_strength=1.0, double_sided=False):
    """glTF-exportable metal/rough material.
    base: RGBA (linear) or '#hex' or PNG path. orm: packed PNG (R=AO, G=rough, B=metal).
    normal: OpenGL (+Y) tangent-space PNG. alpha: None | 'MASK' | 'BLEND' (+ base alpha)."""
    m = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    if not m.node_tree: m.use_nodes = True
    nt = m.node_tree; nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputMaterial"); out.location = (600, 0)
    bs = nt.nodes.new("ShaderNodeBsdfPrincipled"); bs.location = (250, 0)
    nt.links.new(bs.outputs["BSDF"], out.inputs["Surface"])
    if isinstance(base, str) and base.startswith("#"): base = hex_rgba(base)
    if isinstance(base, str):
        t = nt.nodes.new("ShaderNodeTexImage"); t.image = _img(base, False); t.location = (-300, 300)
        nt.links.new(t.outputs["Color"], bs.inputs["Base Color"])
        if alpha == "MASK":        # glTF exporter writes alphaMode MASK (cutoff 0.5) only for a Round node
            rn = nt.nodes.new("ShaderNodeMath"); rn.operation = "ROUND"; rn.location = (-50, 150)
            nt.links.new(t.outputs["Alpha"], rn.inputs[0]); nt.links.new(rn.outputs[0], bs.inputs["Alpha"])
        elif alpha: nt.links.new(t.outputs["Alpha"], bs.inputs["Alpha"])
    else:
        bs.inputs["Base Color"].default_value = base
        if alpha: bs.inputs["Alpha"].default_value = base[3]
    bs.inputs["Roughness"].default_value = roughness
    bs.inputs["Metallic"].default_value = metallic
    if orm:
        t = nt.nodes.new("ShaderNodeTexImage"); t.image = _img(orm, True); t.location = (-500, 0)
        sep = nt.nodes.new("ShaderNodeSeparateColor"); sep.location = (-200, 0)
        nt.links.new(t.outputs["Color"], sep.inputs["Color"])
        nt.links.new(sep.outputs["Green"], bs.inputs["Roughness"])
        nt.links.new(sep.outputs["Blue"], bs.inputs["Metallic"])
        g = nt.nodes.new("ShaderNodeGroup"); g.node_tree = _gltf_output_group(); g.location = (250, -400)
        nt.links.new(sep.outputs["Red"], g.inputs["Occlusion"])
    if normal:
        t = nt.nodes.new("ShaderNodeTexImage"); t.image = _img(normal, True); t.location = (-500, -300)
        nm = nt.nodes.new("ShaderNodeNormalMap"); nm.location = (-200, -300)
        nm.inputs["Strength"].default_value = normal_strength
        nt.links.new(t.outputs["Color"], nm.inputs["Color"]); nt.links.new(nm.outputs["Normal"], bs.inputs["Normal"])
    if emissive is not None:
        if isinstance(emissive, str) and not emissive.startswith("#"):
            t = nt.nodes.new("ShaderNodeTexImage"); t.image = _img(emissive, False); t.location = (-300, -600)
            nt.links.new(t.outputs["Color"], bs.inputs["Emission Color"])
        else:
            bs.inputs["Emission Color"].default_value = hex_rgba(emissive) if isinstance(emissive, str) else emissive
        bs.inputs["Emission Strength"].default_value = emissive_strength
    if alpha:
        m.surface_render_method = "BLENDED" if alpha == "BLEND" else "DITHERED"
        m["gltf_alpha"] = alpha
    m.use_backface_culling = not double_sided      # exporter: doubleSided = not culling
    return m


def replace_material(obj, old, new):
    """Swap one material slot in place. (me.materials.clear() + append resets EVERY face's
    material_index to 0 in Blender 5.x, so every role would bake/export as the first material.)"""
    for i, m in enumerate(obj.data.materials):
        if m == old or (isinstance(old, str) and m and m.name == old): obj.data.materials[i] = new


def save_png(arr, path, noncolor=True):
    """Write a numpy float array (H,W[,3|4], 0..1, row 0 = TOP) to PNG from inside Blender
    (Blender's Python has no cv2/PIL). For heavy texture work use tex_tools in CPython."""
    import numpy as np
    a = np.asarray(arr, np.float32)
    if a.ndim == 2: a = np.dstack([a, a, a])
    if a.shape[2] == 3: a = np.dstack([a, np.ones(a.shape[:2], np.float32)])
    h, w = a.shape[:2]
    img = bpy.data.images.new(os.path.basename(path).rsplit(".", 1)[0], w, h, alpha=True, is_data=noncolor)
    img.colorspace_settings.name = "Non-Color" if noncolor else "sRGB"
    img.pixels.foreach_set(a[::-1].ravel())
    os.makedirs(os.path.dirname(path), exist_ok=True)
    img.filepath_raw = path; img.file_format = "PNG"; img.save(); bpy.data.images.remove(img)
    return path


def material_from_lib(name, lib, tex_dir):
    """Build a material from the project library entry lib[name] (see materials.md)."""
    e = lib[name]; p = lambda f: os.path.join(tex_dir, f) if f else None
    base = e.get("base_color") or p(e.get("base"))
    if isinstance(base, str) and base.startswith("#"): base = hex_rgba(base, e.get("opacity", 1.0))
    elif isinstance(base, list): base = tuple(base) + ((e.get("opacity", 1.0),) if len(base) == 3 else ())
    return material(name, base=base, roughness=e.get("roughness", 0.85), metallic=e.get("metallic", 0.0),
                    orm=p(e.get("orm")), normal=p(e.get("normal")), emissive=e.get("emissive") and (p(e["emissive"]) if not e["emissive"].startswith("#") else e["emissive"]),
                    emissive_strength=e.get("emissive_strength", 1.0), alpha=e.get("alpha"),
                    normal_strength=e.get("normal_strength", 1.0), double_sided=e.get("double_sided", False))


def load_material_lib(ctx):
    from validate_glb import load_material_lib as _l
    return _l(ctx.assets_root, ctx.spec)


# --------------------------------------------------------------------------- baking

def _principled(mat):
    return next(n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED")


def bake(obj, channel, size, out_path, high=None, margin=None, samples=1, noncolor=None, extrusion=0.02, ao_distance=0.35):
    """Bake one channel of obj's materials to a PNG through its active UV (UV0).

    channel: a Principled input name ('Base Color', 'Roughness', 'Metallic', 'Alpha',
             'Emission Color') -> baked via an emission reroute (no lighting, exact values);
             or 'NORMAL' (tangent, OpenGL +Y) / 'AO' -> Cycles bake types.
    high: optional high-poly object to bake from (selected-to-active)."""
    sc = bpy.context.scene; sc.render.engine = "CYCLES"; sc.cycles.samples = samples
    sc.cycles.device = "CPU"
    th = int(os.environ.get("GAM_BAKE_THREADS", "3"))       # parallel agents share the machine
    if th > 0: sc.render.threads_mode = "FIXED"; sc.render.threads = th
    if margin is None: margin = max(8, size // 64)          # dilate islands so mips don't bleed background
    if sc.world is None:                                     # AO needs a world; factory-empty scenes have none
        sc.world = bpy.data.worlds.new("bake_world")
    sc.world.light_settings.distance = ao_distance
    if noncolor is None: noncolor = channel not in ("Base Color", "Emission Color")
    img = bpy.data.images.new(os.path.basename(out_path).rsplit(".", 1)[0], size, size, alpha=True,
                              float_buffer=False, is_data=noncolor)
    img.colorspace_settings.name = "Non-Color" if noncolor else "sRGB"
    img.pixels.foreach_set([0.0] * (size * size * 4))      # sentinel: alpha 0 = never written by the bake
    src = high or obj
    restore = []
    targets = []
    for slot in obj.material_slots:
        nt = slot.material.node_tree
        t = nt.nodes.new("ShaderNodeTexImage"); t.image = img; nt.nodes.active = t; targets.append((nt, t))
    if channel not in ("NORMAL", "AO"):
        for slot in src.material_slots:
            nt = slot.material.node_tree; bs = _principled(slot.material)
            out = next(n for n in nt.nodes if n.type == "OUTPUT_MATERIAL")
            old = out.inputs["Surface"].links[0].from_socket if out.inputs["Surface"].links else None
            em = nt.nodes.new("ShaderNodeEmission")
            inp = bs.inputs[channel]
            if inp.links: nt.links.new(inp.links[0].from_socket, em.inputs["Color"])
            else:
                dv = inp.default_value
                em.inputs["Color"].default_value = tuple(dv) if hasattr(dv, "__len__") else (dv, dv, dv, 1)
            nt.links.new(em.outputs["Emission"], out.inputs["Surface"])
            restore.append((nt, out, old, em))
        btype = "EMIT"
    else:
        btype = channel
    # anything else in the scene (LOD copies, broken pieces, colliders) would occlude AO / catch
    # rays: hide it from render for the bake
    hidden = [o for o in bpy.context.scene.objects if o not in (obj, high) and not o.hide_render]
    for o in hidden: o.hide_render = True
    select_only([obj] + ([high] if high else []), active=obj)
    kw = dict(type=btype, margin=margin, use_clear=False)     # keep the alpha-0 sentinel
    if high:
        kw.update(use_selected_to_active=True, cage_extrusion=extrusion)
    if btype == "NORMAL": kw.update(normal_space="TANGENT")
    if btype == "AO": sc.cycles.samples = max(samples, 64)
    bpy.ops.object.bake(**kw)
    for o in hidden: o.hide_render = False
    for nt, out, old, em in restore:
        if old: nt.links.new(old, out.inputs["Surface"])
        nt.nodes.remove(em)
    for nt, t in targets: nt.nodes.remove(t)
    _fill_background(img, normal=(btype == "NORMAL"))
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    img.filepath_raw = out_path; img.file_format = "PNG"; img.save()
    return out_path


def _fill_background(img, normal=False):
    """Texels the bake never wrote keep the alpha-0 sentinel set before baking. Fill them with a
    flat normal (normal maps) or the mean of the written texels, then make everything opaque.
    (Testing value == 0 instead is WRONG: metallic/roughness/AO are legitimately 0.)"""
    import numpy as np
    w, h = img.size
    px = np.empty(w * h * 4, np.float32); img.pixels.foreach_get(px); px = px.reshape(-1, 4)
    empty = px[:, 3] <= 0.0
    if empty.any() and not empty.all():
        px[empty, :3] = (0.5, 0.5, 1.0) if normal else px[~empty, :3].mean(0)
    px[:, 3] = 1.0
    img.pixels.foreach_set(px.ravel())


def pack_orm(ao_path, rough_path, metal_path, out_path):
    """Pack three grayscale PNGs into glTF ORM (R=AO, G=rough, B=metal). Any may be a float."""
    import numpy as np
    def load(p, size):
        if isinstance(p, (int, float)): return np.full((size, size), float(p), np.float32)
        im = bpy.data.images.load(p); im.colorspace_settings.name = "Non-Color"
        a = np.array(im.pixels[:], np.float32).reshape(im.size[1], im.size[0], 4)[..., 0]
        bpy.data.images.remove(im); return a
    size = next(bpy.data.images.load(p).size[0] for p in (ao_path, rough_path, metal_path) if isinstance(p, str))
    chans = [load(p, size) for p in (ao_path, rough_path, metal_path)]
    img = bpy.data.images.new(os.path.basename(out_path).rsplit(".", 1)[0], size, size, is_data=True)
    img.colorspace_settings.name = "Non-Color"
    import numpy as np
    px = np.dstack(chans + [np.ones_like(chans[0])]).ravel()
    img.pixels.foreach_set(px); img.filepath_raw = out_path; img.file_format = "PNG"; img.save()
    return out_path


# --------------------------------------------------------------------------- game data

def root(ctx_or_id):
    name = ctx_or_id.id if isinstance(ctx_or_id, Ctx) else ctx_or_id
    r = bpy.data.objects.get(name)
    if r is not None and r.type != "EMPTY":        # a mesh squatting on the root name
        r.name = name + "_mesh"; r = None
    r = r or link(bpy.data.objects.new(name, None))
    r.empty_display_type = "PLAIN_AXES"; r.empty_display_size = 0.25
    return r


def parent(child, par):
    """Parent keeping the child's current world transform."""
    child.parent = par; child.matrix_parent_inverse = par.matrix_world.inverted()


def _name(obj, name):
    """Name object and mesh data exactly (evicting stale datablocks that hold the name)."""
    for coll in (bpy.data.objects, bpy.data.meshes):
        old = coll.get(name)
        if old is not None and old not in (obj, obj.data): old.name = name + "_old"
    obj.name = name
    if obj.data is not None: obj.data.name = name


name_exact = None  # set below


def make_lods(obj, asset_id, ratios, min_tris, recompute_normals=True, angle_deg=30.0):
    """obj -> <id>_lod0 plus decimated siblings (<id>_lod1, _lod2 ...). Returns list.
    Below min_tris the single mesh is named <id>_lod0 and no LODs are made."""
    _name(obj, f"{asset_id}_lod0")
    out = [obj]
    if tris(obj) <= min_tris: return out
    for i, r in enumerate(ratios[1:], 1):
        c = obj.copy(); c.data = obj.data.copy(); link(c)
        _name(c, f"{asset_id}_lod{i}")
        m = c.modifiers.new("Dec", "DECIMATE"); m.decimate_type = "COLLAPSE"; m.ratio = r
        m.use_collapse_triangulate = False
        apply_modifiers(c)
        if recompute_normals: shade_hard_surface(c, angle_deg)
        out.append(c)
    return out


def collider(src, asset_id, kind, prop="ig_collider", max_hull_verts=64, planar_deg=5.0, name=None):
    """Collision child <id>_col: 'box' (bbox), 'convex' (hull), 'mesh' (planar-decimated copy).
    Never the render mesh itself. No materials."""
    name = name or f"{asset_id}_col"
    lo, hi = bounds(src, world=False)
    bm = bmesh.new()
    if kind == "box":
        bm_box(bm, lo, hi)
    else:
        bm.from_mesh(src.data)
        if kind == "convex":
            res = bmesh.ops.convex_hull(bm, input=bm.verts[:], use_existing_faces=False)
            kill = set(res.get("geom_interior", [])) | set(res.get("geom_unused", []))
            bmesh.ops.delete(bm, geom=[g for g in kill if isinstance(g, bmesh.types.BMVert)], context="VERTS")
        bmesh.ops.remove_doubles(bm, verts=bm.verts[:], dist=0.005)
        bmesh.ops.dissolve_limit(bm, angle_limit=math.radians(planar_deg), verts=bm.verts[:], edges=bm.edges[:])
        bmesh.ops.triangulate(bm, faces=bm.faces[:])
    c = obj_from_bm(name, bm)
    for uv in list(c.data.uv_layers): c.data.uv_layers.remove(uv)
    for a in [a.name for a in c.data.attributes if not a.name.startswith((".", "position", "sharp_face"))]:
        if a in c.data.attributes and not c.data.attributes[a].is_required: c.data.attributes.remove(c.data.attributes[a])
    if kind == "convex" and len(c.data.vertices) > max_hull_verts:
        m = c.modifiers.new("Dec", "DECIMATE"); m.ratio = max_hull_verts / len(c.data.vertices); apply_modifiers(c)
    c.matrix_world = src.matrix_world.copy()
    c[prop] = kind
    c.display_type = "WIRE"
    return c


def to_gltf(v):
    """Blender (x, y, z) -> glTF (x, z, -y). Custom-property coordinates are NOT converted by
    the exporter; always pass engine-space vectors through this."""
    return [round(v[0], 5), round(v[2], 5), round(-v[1], 5)]


def to_gltf_dir(v):
    return to_gltf(v)


def set_props(obj, props):
    for k, v in props.items():
        obj[k] = list(v) if isinstance(v, (tuple, Vector)) else v


def socket(name, loc, forward=(0, -1, 0), up=(0, 0, 1), par=None):
    """Empty whose local +Y = forward, +Z = up (-> glTF local -Z forward, +Y up)."""
    f = Vector(forward).normalized(); u = Vector(up).normalized()
    if abs(f.dot(u)) > 0.999:                       # forward parallel to up: pick another up (else zero scale)
        u = Vector((0, -1, 0)) if abs(f.z) > 0.9 else Vector((0, 0, 1))
    r = f.cross(u).normalized(); u = r.cross(f).normalized()
    rot = Matrix((r, f, u)).transposed().to_4x4()
    e = link(bpy.data.objects.new(name, None)); e.empty_display_type = "SINGLE_ARROW"; e.empty_display_size = 0.3
    e.matrix_world = Matrix.Translation(loc) @ rot
    if par: parent(e, par)
    return e


def voronoi_fracture(obj, n, seed=0, inner_mat=None, prefix=None, jitter=0.0):
    """Split a closed mesh into n convex-ish cells by bisecting with Voronoi planes.
    Cut faces get material `inner_mat` (appended). Returns list of piece objects."""
    rnd = random.Random(seed)
    lo, hi = bounds(obj, world=False)
    pts = [Vector((rnd.uniform(lo.x, hi.x), rnd.uniform(lo.y, hi.y), rnd.uniform(lo.z, hi.z))) for _ in range(n)]
    mats = list(obj.data.materials)
    inner_idx = None
    if inner_mat:
        mats.append(inner_mat); inner_idx = len(mats) - 1
    pieces = []
    for i, p in enumerate(pts):
        bm = bmesh.new(); bm.from_mesh(obj.data)
        for j, q in enumerate(pts):
            if i == j: continue
            mid = (p + q) / 2; nrm = (q - p).normalized()
            geom = bm.verts[:] + bm.edges[:] + bm.faces[:]
            res = bmesh.ops.bisect_plane(bm, geom=geom, plane_co=mid, plane_no=nrm, clear_outer=True)
            cut_edges = [e for e in res["geom_cut"] if isinstance(e, bmesh.types.BMEdge)]
            if cut_edges:
                fill = bmesh.ops.holes_fill(bm, edges=cut_edges, sides=0)
                for f in fill["faces"]:
                    if inner_idx is not None: f.material_index = inner_idx
            if not bm.faces: break
        if len(bm.faces) < 4: bm.free(); continue
        bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
        pc = obj_from_bm(f"{prefix or obj.name}_piece_{i:02d}", bm, mats)
        pc.matrix_world = obj.matrix_world.copy()
        pieces.append(pc)
    return pieces


def make_broken(ctx, pieces, kind="convex"):
    """Breakable variant: an empty <id>_broken holding <id>_broken_NN pieces, each with its own
    <id>_broken_NN_col. Pieces are authored in the same world space as the intact mesh.
    Part-based (planks, panels, legs) reads better than Voronoi for built objects; use
    voronoi_fracture for solids (concrete, glass, plaster, brick)."""
    b = link(bpy.data.objects.new(ctx.id + ctx.spec["naming"].get("broken_suffix", "_broken"), None))
    for i, pc in enumerate(pieces):
        apply_transforms(pc, location=True)
        lo, hi = bounds(pc, world=False); c = (lo + hi) / 2
        pc.data.transform(Matrix.Translation(-c)); pc.location = c        # piece origin at its center
        pc.name = pc.data.name = "%s_%02d" % (b.name, i)
        col = collider(pc, pc.name, kind, ctx.spec["collision"]["prop"])
        col.matrix_world = Matrix.Identity(4); parent_local(col, pc)
        parent(pc, b)
    return b


# --------------------------------------------------------------------------- finish + export

def pivot_point(obj, rule, point=None):
    """Pivot in obj's local coords for a spec rule (see set_pivot)."""
    lo, hi = bounds(obj, world=False)
    if rule == "bottom_center": return Vector(((lo.x + hi.x) / 2, (lo.y + hi.y) / 2, lo.z))
    if rule == "bottom_left_back": return Vector((lo.x, hi.y, lo.z))
    if rule == "center_of_mass": return center_of_mass(obj)[0]
    if rule in ("point", "custom"): return Vector(point)
    raise ValueError(rule)


def parent_local(child, par):
    """Parent keeping child.matrix_basis as its LOCAL transform (identity parent inverse)."""
    child.parent = par; child.matrix_parent_inverse = Matrix.Identity(4)


def finish(ctx, render_obj, collider_kind=None, collider_src=None, props=None, extra_children=(),
           lod=True, lightmap=None, shade=True, angle_deg=30.0, pivot_point_local=None, lod_group=None,
           lods=None, collider_parts=None):
    """Standard game finish. Author everything in world space (asset near the origin, front
    facing -Y); render_obj must already carry form, materials and UV0.

    1. triangulate stray n-gons   2. pivot per spec (everything shifts by -pivot)
    3. weighted-normal shading    4. lightmap UV (if the category needs it)
    5. LODs <id>_lod0..n          6. collider <id>_col from collider_src or the render mesh
    7. root empty <id> with custom props; extra_children (sockets, states, _broken) parented.
    collider_parts=[o1, o2, ...]: compound collider -> <id>_col_01, _col_02 ... (each box/convex
    of `collider_kind`), e.g. a table's top + legs so the space underneath stays open.
    lods=[lod1, lod2]: hand-built lower LODs (same world space, materials + UV0 done, e.g. via
    transfer_uvs) instead of automatic collapse decimation.
    lod_group="_intact": LODs go under an empty <id>_intact (state assets: pair it with a
    <id>_sabotaged child passed in extra_children).
    COM pivots: root sits at the COM height so the mesh rests on the ground (z = 0)."""
    spec = ctx.spec; rule = ctx.pivot()
    triangulate_ngons(render_obj)
    apply_transforms(render_obj, location=True)
    lo, _ = bounds(render_obj, world=False)
    p = pivot_point(render_obj, rule, pivot_point_local or ctx.brief.get("pivot_point"))
    shift = Matrix.Translation(-p)
    render_obj.data.transform(shift); render_obj.data.update()
    hand = list(lods or [])
    for h in hand:
        triangulate_ngons(h); apply_transforms(h, location=True); h.data.transform(shift); h.data.update()
    others = list(extra_children) + ([collider_src] if collider_src is not None else []) + list(collider_parts or [])
    for o in others:
        o.matrix_world = shift @ o.matrix_world
    if shade: shade_hard_surface(render_obj, angle_deg)
    if lightmap is None: lightmap = ctx.cat.get("lightmap_uv", False)
    if lightmap and not ctx.placeholder: uv_lightmap(render_obj, spec["uv"])
    r = root(ctx)
    r.location = (0, 0, p.z - lo.z) if rule == "center_of_mass" else (0, 0, 0)
    if hand and not ctx.placeholder:
        _name(render_obj, f"{ctx.id}_lod0"); lods = [render_obj]
        for i, h in enumerate(hand, 1):
            if shade: shade_hard_surface(h, angle_deg)
            if lightmap: uv_lightmap(h, spec["uv"])
            _name(h, f"{ctx.id}_lod{i}"); lods.append(h)
    elif lod and not ctx.placeholder:
        lods = make_lods(render_obj, ctx.id, spec["lod"]["ratios"], spec["lod"]["min_tris"], angle_deg=angle_deg)
    else:
        _name(render_obj, f"{ctx.id}_lod0"); lods = [render_obj]
    for h in hand:
        if h not in lods: bpy.data.objects.remove(h, do_unlink=True)
    lp = r
    if lod_group:
        lp = link(bpy.data.objects.new(ctx.id + lod_group, None)); parent_local(lp, r)
    for o in lods: parent_local(o, lp)
    kind = collider_kind or ctx.brief.get("collider")
    if collider_parts:
        for k, part in enumerate(collider_parts, 1):
            c = collider(part, ctx.id, kind or "convex", spec["collision"]["prop"], name="%s_col_%02d" % (ctx.id, k))
            c.matrix_world = part.matrix_world.copy(); bpy.data.objects.remove(part, do_unlink=True)
            parent_local(c, r)
        kind = None                                       # compound replaces the single _col
    if kind and kind != "none":
        c = collider(collider_src if collider_src is not None else render_obj, ctx.id, kind, spec["collision"]["prop"])
        c.matrix_world = (collider_src.matrix_world if collider_src is not None else Matrix.Identity(4)).copy()
        if collider_src is not None: bpy.data.objects.remove(collider_src, do_unlink=True)
        parent_local(c, r)
    for ch in extra_children:
        if ch.parent is None: parent_local(ch, r)
    allp = dict(ctx.brief.get("props", {})); allp.update(props or {})
    set_props(r, allp)
    return r


def hierarchy(r):
    out = [r]
    for c in r.children: out += hierarchy(c)
    return out


def export(ctx, r=None, save_blend=True, copy_textures=True, animations=False):
    r = r or bpy.data.objects[ctx.id]
    os.makedirs(os.path.dirname(ctx.glb), exist_ok=True)
    for o in hierarchy(r): o.hide_set(False)
    select_only(hierarchy(r), active=r)
    bpy.ops.export_scene.gltf(filepath=ctx.glb, export_format="GLB", use_selection=True,
                              export_extras=True, export_yup=True, export_apply=True,
                              export_texcoords=True, export_normals=True, export_tangents=False,
                              export_materials="EXPORT", export_image_format="AUTO",
                              export_cameras=False, export_lights=False,
                              export_animations=animations, export_skins=True, export_morph=True)
    if copy_textures and not ctx.placeholder:
        os.makedirs(ctx.tex_dir, exist_ok=True)
        for img in bpy.data.images:
            fp = bpy.path.abspath(img.filepath) if img.filepath else ""
            if fp and os.path.exists(fp) and os.path.dirname(os.path.abspath(fp)) != os.path.abspath(ctx.tex_dir):
                shutil.copy2(fp, ctx.tex_dir)
    if save_blend:
        os.makedirs(os.path.dirname(ctx.blend), exist_ok=True)
        bpy.ops.wm.save_as_mainfile(filepath=ctx.blend, compress=True)
    print("GAM_EXPORT", json.dumps({"glb": ctx.glb, "blend": ctx.blend,
                                   "tris": {o.name: tris(o) for o in hierarchy(r) if o.type == "MESH"}}))
    return ctx.glb


name_exact = _name     # public: name object + mesh data exactly, evicting stale holders
