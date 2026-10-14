#!/usr/bin/env python3
"""
anim_cli.py -- pipeline driver for game-animation (plain CPython; Blender headless for import).

  anim_cli.py claim [pattern] [--owner N] [--list] [--release]   assets/claims.json (default anim_* -> spec owner)
  anim_cli.py new <set_id> [--assets DIR] [--force]               scaffold <assets>/src/anim/<set>/animset.json
  anim_cli.py scan <take.bvh|fbx|glb> [--mapping cmu] [--assets DIR]
                                                                  bones, mapping coverage, per-second motion table,
                                                                  loop-window candidates -> pick clips from a long take
  anim_cli.py build <animset.json> [clip ...]                     import -> retarget -> cleanup -> export glb + meta
  anim_cli.py validate <animset.json>                             validate_anim.py -> _work/validate.json
  anim_cli.py playback <animset.json> [--engine DIR] [--body ID]  vitest through the engine's ClipLibrary/retarget
  anim_cli.py review <animset.json> [--video] [--bodies m,f]      Babylon renders on the real bodies -> review/<set>/
  anim_cli.py blend <animset.json>                                first-person: headless Blender inspection/polish .blend
  anim_cli.py manifest <animset.json>                             upsert assets/manifest.json (status from animset)
  anim_cli.py licenses <animset.json> [--write]                   LICENSES.md rows for the set's sources
  anim_cli.py all <animset.json> [--video]                        build -> validate -> playback -> review -> manifest
  anim_cli.py status [--assets DIR]                               animsets vs exports vs validate vs manifest

Owner: $GAM_OWNER (same claims file as game-asset-modeling). Reviews take every review slot
on the machine: one headless browser at a time, never the game client.
"""
import datetime, fcntl, json, os, shutil, subprocess, sys, tempfile, time

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import animlib as al  # noqa: E402
import anim_build  # noqa: E402
from validate_glb import find_spec  # noqa: E402
import gam_cli  # noqa: E402  (claims, Slot, blender_bin)


def ctx(animset_path):
    a = json.load(open(animset_path)); P = anim_build.paths_for(animset_path, a); return a, P


# --------------------------------------------------------------------------- claims / new

def cmd_claim(argv):
    if "--list" not in argv and not any(not x.startswith("--") for x in argv):
        argv = ["anim_*"] + argv
    if "--owner" not in argv and "--list" not in argv and not os.environ.get("GAM_OWNER"):
        spec = json.load(open(find_spec(os.path.join(os.getcwd(), "x"))))
        argv = argv + ["--owner", spec.get("animation", {}).get("owner", "anim"), "--note", "game-animation skill: anim_ sets"]
    gam_cli.cmd_claim(argv)


def cmd_new(argv):
    import argparse
    ap = argparse.ArgumentParser(prog="anim_cli.py new"); ap.add_argument("id"); ap.add_argument("--assets"); ap.add_argument("--force", action="store_true")
    a = ap.parse_args(argv)
    sp = find_spec(os.path.join(a.assets or os.getcwd(), "x")); spec = json.load(open(sp)); root = os.path.dirname(sp)
    an = spec.get("animation") or {}
    if not a.id.startswith("anim_"): raise SystemExit("set ids start with anim_")
    gam_cli.claims_check(root, spec, a.id)
    d = os.path.join(root, spec["paths"]["src"], an.get("src_dir", "anim"), a.id); os.makedirs(os.path.join(d, "_sources"), exist_ok=True)
    p = os.path.join(d, an.get("set_file", "animset.json"))
    if os.path.exists(p) and not a.force: raise SystemExit("exists: %s (use --force)" % p)
    tpl = "fp_animset.json" if a.id.startswith("anim_fp_") else "animset.json"
    t = json.load(open(os.path.join(SKILL, "templates", tpl))); t["id"] = a.id
    json.dump(t, open(p, "w"), indent=2); print(p)


# --------------------------------------------------------------------------- scan

def cmd_scan(argv):
    import argparse
    import numpy as np
    ap = argparse.ArgumentParser(prog="anim_cli.py scan"); ap.add_argument("take"); ap.add_argument("--mapping", default="auto")
    ap.add_argument("--assets"); ap.add_argument("--rest-frame"); ap.add_argument("--window", type=float, default=1.0)
    a = ap.parse_args(argv)
    sp = find_spec(os.path.join(a.assets or os.getcwd(), "x")); spec = json.load(open(sp)); root = os.path.dirname(sp)
    skel = al.load_target(spec, root)
    work = os.path.join(tempfile.gettempdir(), "ganim_scan", os.path.splitext(os.path.basename(a.take))[0])
    P = {"spec": spec, "root": root, "set_dir": os.getcwd(), "work": work}
    names = anim_build.scan_bone_names(os.path.abspath(a.take), P)
    cov, best = al.detect_mapping(names)
    print("bones (%d): %s" % (len(names), " ".join(names)))
    print("mapping: best table %s covers %.0f%%%s" % (best, cov * 100, "" if a.mapping == "auto" else " (using %s)" % a.mapping))
    src = {"file": os.path.abspath(a.take), "mapping": best if a.mapping == "auto" else a.mapping}
    if a.rest_frame is not None: src["rest_frame"] = a.rest_frame if a.rest_frame in ("bind", "neutral") else int(a.rest_frame)
    raw, info = anim_build.import_source("scan", src, P, skel)
    mp = al.load_mapping(src["mapping"])
    miss = [t for t, s in mp["bones"].items() if s and al.norm_name(s) not in {al.norm_name(n) for n in names}]
    print("unmatched table bones: %s" % (miss or "none"))
    print("source: %d frames @ %g fps (%.1f s); scale %.4f; facing %+.0f deg; rest %s" % (info["source_frames"], info["source_fps"], info["source_frames"] / info["source_fps"], info["scale"], info["source_facing_deg"], info["rest"]))
    t30 = al.resample(raw, 30); t30, fp = al.calibrate_foot_pitch(skel, t30); t30, off = al.ground_calibrate(skel, t30)
    rooted = al.extract_root(skel, t30, True, "facing", 0.4)
    Wc, Pw = al.fk(skel, rooted); con, _ = al.detect_contacts(skel, rooted, Wc=Wc, P=Pw)
    yaw = al.facing_yaw(skel, Pw); h = Pw[:, skel.i("hips"), 1]
    w = max(1, int(a.window * 30))
    print("\n  time      speed   turn    hips   contacts   (speed m/s of root, turn deg/s, hips height m, L/R planted %)")
    for s in range(0, t30.F - 1, w):
        e = min(t30.F - 1, s + w); dt = (e - s) / 30
        sp_ = np.linalg.norm(rooted.root_p[e, [0, 2]] - rooted.root_p[s, [0, 2]]) / dt
        tr = np.degrees(yaw[e] - yaw[s]) / dt
        print("  %5.1f-%-5.1f %5.2f %6.0f %7.2f   L%3.0f R%3.0f" % (s / 30, e / 30, sp_, tr, h[s:e].mean(), con["l"]["contact"][s:e].mean() * 100, con["r"]["contact"][s:e].mean() * 100))
    print("\nloop candidates (cost = RMS joint mismatch cm; frames are source frames @ %g fps):" % info["source_fps"])
    for label, per in (("walk 2-step", (0.8, 1.5)), ("jog/run 2-step", (0.5, 0.9)), ("idle", (4.0, 8.0))):
        if per[0] * 30 >= t30.F - 2: continue
        c = al.find_cycles(skel, rooted, period_s=per, top=3)
        for cost, i, j in c:
            f0, f1 = int(round(i / 30 * info["source_fps"])), int(round(j / 30 * info["source_fps"]))
            print("  %-15s cost %5.2f  %.2f-%.2f s  frames [%d, %d]" % (label, cost, i / 30, j / 30, f0, f1))
    print("\nUse these as clip entries: {\"frames\": [a, b]} (source frames) or {\"range\": \"auto\", \"window_s\": [...], \"period_s\": [...]}")


# --------------------------------------------------------------------------- build / validate

def fp_mode(p):
    return json.load(open(p)).get("mode") in ("first_person", "hand_poses")


def cmd_build(p, only=None):
    t = time.time()
    try:
        if fp_mode(p):
            import fp_build
            fp_build.build(p, only=only)
        else:
            anim_build.build(p, only=only)
    except SystemExit as e:
        print("BUILD FAILED:", e); return False
    print("build %.1fs" % (time.time() - t)); return True


def cmd_validate(p, quiet=False):
    import validate_anim
    a, P = ctx(p)
    if a.get("mode") in ("first_person", "hand_poses"):
        import validate_fp
        a["_path"] = os.path.abspath(p)
        rep, summ = validate_fp.run(P["glb"], a, P["spec"], P["root"], P["meta"])
    else:
        rep, summ = validate_anim.run(P["glb"], a, P["spec"], P["root"], P["meta"])
    nf = len(rep.failed)
    os.makedirs(P["work"], exist_ok=True)
    json.dump({"id": a["id"], "result": "FAIL" if nf else "PASS", "items": rep.items, "summary": summ}, open(os.path.join(P["work"], "validate.json"), "w"), indent=1)
    for it in rep.items:
        if not quiet or it["level"] in ("FAIL", "WARN"): print("%-4s  %-46s %s" % (it["level"], it["check"], it["detail"]))
    print("VALIDATE %s: %d fail, %d warn" % ("FAIL" if nf else "PASS", nf, sum(1 for i in rep.items if i["level"] == "WARN")))
    return nf == 0


# --------------------------------------------------------------------------- playback (engine)

def engine_dir(P, override=None):
    for c in (override, os.environ.get("GANIM_ENGINE"), os.path.join(P["root"], P["spec"]["animation"].get("engine_dir", "../packages/engine"))):
        if c and os.path.exists(os.path.join(c, "src", "anim", "clips.ts")): return os.path.realpath(c)
    raise SystemExit("engine not found: pass --engine <packages/engine> or set GANIM_ENGINE")


def find_bin(start, name):
    d = start
    while True:
        c = os.path.join(d, "node_modules", ".bin", name)
        if os.path.exists(c): return c
        if os.path.dirname(d) == d: return None
        d = os.path.dirname(d)


def part_path(P, pid):
    mp = os.path.join(P["root"], P["spec"]["paths"]["manifest"])
    for e in (json.load(open(mp)) if os.path.exists(mp) else []):
        if e.get("id") == pid and os.path.exists(os.path.join(P["root"], e["path"])): return os.path.join(P["root"], e["path"])
    for k in ("export", "placeholder"):
        c = os.path.join(P["root"], P["spec"]["paths"][k], "char", pid + ".glb")
        if os.path.exists(c): return c
    raise SystemExit("character part not found: %s" % pid)


def cmd_playback(p, argv=()):
    a, P = ctx(p)
    eng = engine_dir(P, argv[argv.index("--engine") + 1] if "--engine" in argv else None)
    body = argv[argv.index("--body") + 1] if "--body" in argv else P["spec"]["animation"]["review"]["bodies"][0]["body"]
    # Each run gets its own dir (run-<time>-<pid>) with its own test, log and a snapshot of the set's glb/meta/ref, so
    # concurrent runs and mid-run rebuilds cannot mix into it. Kept on failure, removed on PASS; playback.log in the
    # parent always holds the latest run's log (backward compatible).
    import time
    base = os.path.join(P["work"], "playback"); os.makedirs(base, exist_ok=True)
    d = os.path.join(base, "run-%s-%d" % (time.strftime("%Y%m%d-%H%M%S"), os.getpid())); os.makedirs(d)
    test = "fp_playback.test.ts" if a.get("mode") in ("first_person", "hand_poses") else "anim_playback.test.ts"
    shutil.copy(os.path.join(SKILL, "tools", "playback", test), d)
    snap = {}
    for k, src in (("glb", P["glb"]), ("meta", P["meta"]), ("ref", os.path.join(P["work"], "ref.json"))):
        snap[k] = os.path.join(d, os.path.basename(src))
        if os.path.exists(src): shutil.copy(src, snap[k])
    open(os.path.join(d, "vitest.config.mjs"), "w").write(
        "export default { test: { include: ['" + test + "'], testTimeout: 180000, hookTimeout: 240000, pool: 'forks', poolOptions: { forks: { singleFork: true } } } };\n")
    vit = find_bin(eng, "vitest")
    if not vit: raise SystemExit("vitest not found above %s" % eng)
    if a.get("mode") in ("first_person", "hand_poses") and "--body" not in argv:
        body = fp_arms(P)[0]
    envv = dict(os.environ, GANIM_ENGINE=eng, GANIM_GLB=snap["glb"], GANIM_META=snap["meta"], GANIM_REF=snap["ref"], GANIM_BODY=part_path(P, body),
                GANIM_MODE=a.get("mode", "third_person"), GANIM_SPEC=P["spec_path"])
    r = subprocess.run([vit, "run", "--config", os.path.join(d, "vitest.config.mjs"), "--root", d, "--reporter", "verbose"], cwd=d, env=envv, capture_output=True, text=True, timeout=900)
    txt = r.stdout + r.stderr; open(os.path.join(d, "playback.log"), "w").write(txt)
    tmp = os.path.join(base, "playback.log.%d" % os.getpid()); open(tmp, "w").write("# run: %s\n%s" % (os.path.basename(d), txt)); os.replace(tmp, os.path.join(base, "playback.log"))
    if r.returncode == 0: shutil.rmtree(d, ignore_errors=True)
    import re
    for l in txt.splitlines():
        s = re.sub(r"\x1b\[[0-9;]*m", "", l)
        if re.search(r"(✓|×|✗|FAIL|Tests |Test Files|AssertionError|Error:|expected)", s): print("  " + s.strip()[:220])
    print("PLAYBACK %s (engine %s, body %s) -> %s" % ("PASS" if r.returncode == 0 else "FAIL", os.path.relpath(eng, os.path.dirname(P["root"])) if eng.startswith(os.path.dirname(P["root"])) else eng, body, os.path.relpath(os.path.join(base if r.returncode == 0 else d, "playback.log"), P["root"])))
    return r.returncode == 0


# --------------------------------------------------------------------------- review

class ExclusiveReview:
    """Hold EVERY review slot (shared with game-asset-modeling) so only one headless browser runs."""
    def __init__(self): self.fhs = []
    def __enter__(self):
        n = int(os.environ.get("GAM_SLOTS_REVIEW", 3)); dd = os.path.join(tempfile.gettempdir(), "gam_slots"); os.makedirs(dd, exist_ok=True)
        t = time.time()
        for i in range(n):
            fh = open(os.path.join(dd, "review_%d.lock" % i), "w"); fcntl.flock(fh, fcntl.LOCK_EX); self.fhs.append(fh)
        if time.time() - t > 2: print("GANIM review slots acquired after %ds" % (time.time() - t))
        return self
    def __exit__(self, *a):
        for fh in self.fhs: fcntl.flock(fh, fcntl.LOCK_UN); fh.close()


def diag_for(P, a):
    import numpy as np
    skel = al.load_target(P["spec"], P["root"]); th = P["spec"]["animation"]["thresholds"]
    clips, _ = al.read_anim_glb(P["glb"], skel, P["spec"]); out = {}; charts = {}
    for n, c in clips.items():
        Wc, Pw = al.fk(skel, c)
        con, tr = al.detect_contacts(skel, c, h_tol=th["contact_height_m"], v_max=th["contact_speed_mps"], Wc=Wc, P=Pw)
        sl = al.slide_per_step(tr, con); bad = {}
        for s in "lr":
            b = np.zeros(c.F, bool)
            for (x0, x1), d in sl[s]:
                if d * 100 > th["foot_slide_cm"][0]: b[x0:x1 + 1] = True
            bad[s] = b.tolist()
        out[n] = {"contact": {s: con[s]["contact"].tolist() for s in "lr"}, "slide_bad": bad}
        charts[n] = (c, con, tr, sl)
    return out, charts, clips


def feet_chart(path, name, c, con, tr, sl, W=1920, H=300):
    import numpy as np
    from PIL import Image, ImageDraw
    im = Image.new("RGB", (W, H), (27, 28, 28)); d = ImageDraw.Draw(im)
    F = c.F; x0, x1 = 60, W - 20; y0, y1 = 30, H - 30
    X = lambda k: x0 + (x1 - x0) * k / max(1, F - 1)
    rows = [("l", (224, 160, 48)), ("r", (56, 160, 224))]
    for i, (s, col) in enumerate(rows):
        for k in range(F):
            if con[s]["contact"][k]:
                yy = y1 - 8 - 10 * i; d.rectangle([X(k) - (x1 - x0) / F / 2, yy, X(k) + (x1 - x0) / F / 2, yy + 7], fill=tuple(int(v * 0.8) for v in col))
    hmax = max(0.25, max(float((tr[(sd, kk)][0][:, 1] - tr[(sd, kk)][1]).max()) for sd in 'lr' for kk in ('heel', 'ball')) * 1.05)
    Y = lambda h: y1 - 30 - (y1 - 60 - y0) * min(h, hmax) / hmax
    for gv in (0, 0.05, 0.1, 0.2):
        d.line([x0, Y(gv), x1, Y(gv)], fill=(60, 62, 60)); d.text((8, Y(gv) - 6), "%2.0fcm" % (gv * 100), fill=(150, 150, 140))
    for s, col in rows:
        for k_, w_ in (("heel", 2), ("ball", 1)):
            x, h0 = tr[(s, k_)]; pts = [(X(k), Y(max(0.0, x[k, 1] - h0))) for k in range(F)]
            d.line(pts, fill=col, width=w_)
    rs = np.linalg.norm(np.gradient(c.root_p[:, [0, 2]], axis=0), axis=-1) * c.fps
    if rs.max() > 0:
        pts = [(X(k), y0 + 5 + 40 * (1 - rs[k] / max(rs.max(), 1e-6))) for k in range(F)]; d.line(pts, fill=(230, 230, 220), width=1)
    worst = {s: max([dd for _, dd in sl[s]] or [0]) * 100 for s in "lr"}
    d.text((x0, 6), "%s  feet: heel (thick) / ball height above rest, contact bars below (L orange, R blue); white = root speed (max %.2f m/s); worst slide L %.1f cm R %.1f cm" % (name, rs.max(), worst["l"], worst["r"]), fill=(230, 226, 214))
    im.save(path)


def compose(path, rows, width=1920, title=None):
    from PIL import Image, ImageDraw
    ims = []
    for row in rows:
        row = [Image.open(r) if isinstance(r, str) else r for r in row if r and (not isinstance(r, str) or os.path.exists(r))]
        if not row: continue
        w_each = width // len(row)
        sc = [im.resize((w_each, int(im.height * w_each / im.width))) for im in row]
        h = max(i.height for i in sc); canvas = Image.new("RGB", (width, h), (27, 28, 28))
        for i, im in enumerate(sc): canvas.paste(im, (i * w_each, 0))
        ims.append(canvas)
    top = 34 if title else 0
    out = Image.new("RGB", (width, sum(i.height for i in ims) + top), (27, 28, 28)); y = top
    if title: ImageDraw.Draw(out).text((10, 10), title, fill=(230, 226, 214))
    for im in ims: out.paste(im, (0, y)); y += im.height
    out.save(path); return path


def fp_arms(P):
    """-> (part id, is the fp arms asset). char_fp_arms_* when delivered, else the m_avg body (arm triangles only)."""
    rv = P["spec"]["animation"].get("first_person", {}).get("review", {})
    try:
        part_path(P, rv.get("arms", "char_fp_arms_bare_m_a")); return rv.get("arms", "char_fp_arms_bare_m_a"), True
    except SystemExit:
        return "char_civ_body_m_avg", False


def cmd_review(p, argv=()):
    if fp_mode(p):
        import fp_review
        return fp_review.review(p, argv)
    a, P = ctx(p); spec = P["spec"]; an = spec["animation"]; rv = dict(an.get("review", {}), **a.get("review", {}))
    video = "--video" in argv or rv.get("video", False)
    bodies = rv["bodies"]
    if "--bodies" in argv:
        want = argv[argv.index("--bodies") + 1].split(","); bodies = [b for b in bodies if any(b["body"].endswith(w) or b["body"] == w for w in want)]
    diag, charts, clips = diag_for(P, a)
    out = os.path.join(P["root"], spec["paths"]["review"], a["id"]); os.makedirs(out, exist_ok=True)
    skel = al.load_target(spec, P["root"])
    job = {"spec": P["spec_path"], "out": out, "clips": list(clips), "loops": [n for n, c in clips.items() if n.endswith("_loop")],
           "bodies": [{"name": b["body"].replace("char_civ_body_", ""), "parts": [part_path(P, x) for x in [b["body"], b.get("head")] + b.get("outfits", []) if x]} for b in bodies],
           "diag": diag, "frames": rv.get("contact_frames", 8), "size": rv.get("size", [960, 540]), "video": video, "fps": an.get("fps", 30),
           "bones": skel.names, "parents": {n: (skel.names[skel.parent[i]] if skel.parent[i] >= 0 else None) for i, n in enumerate(skel.names)},
           "hips_rest_src": an["engine"]["hips_rest_src"]}
    jp = os.path.join(P["work"], "review_job.json"); json.dump(job, open(jp, "w"))
    t = time.time()
    with ExclusiveReview():
        r = subprocess.run(["node", os.path.join(SKILL, "tools", "review", "shoot_anim.mjs"), P["glb"], "--job", jp], capture_output=True, text=True, timeout=3600)
    print(r.stdout[-1500:])
    if r.returncode != 0: print(r.stderr[-2000:])
    rj = json.load(open(os.path.join(out, "review.json"))) if os.path.exists(os.path.join(out, "review.json")) else {"errors": ["no review.json"], "bodies": []}
    for e in rj["errors"][:10]: print("ENGINE ERROR:", str(e)[:300])
    # babylon-posed joints vs the build's FK reference (first body shares char_skeleton_ref's rest)
    ref = json.load(open(os.path.join(P["work"], "ref.json")))
    import numpy as np
    if rj["bodies"]:
        b0 = rj["bodies"][0]; worst = (0, "")
        for n, cr in b0.get("clips", {}).items():
            rc = ref["clips"].get(n)
            if not rc: continue
            Wc, Pw = al.fk(skel, clips[n])
            for k, jpk in cr["joints"].items():
                k = int(k); root = np.array(jpk["root"])
                for j, v in jpk.items():
                    if v is None or j == "root": continue
                    e = np.linalg.norm((np.array(v) - root) - (Pw[k, skel.i(j)] - Pw[k, skel.i("root")]))
                    if e > worst[0]: worst = (e, "%s f%d %s" % (n, k, j))
        print("REVIEW pose check (%s in Babylon vs build FK): worst %.2f cm at %s" % (b0["body"], worst[0] * 100, worst[1]))
    # sheets
    sheets = []
    for n, (c, con, tr, sl) in charts.items():
        cd = os.path.join(out, n); os.makedirs(cd, exist_ok=True)
        fc = os.path.join(cd, "feet.png"); feet_chart(fc, n, c, con, tr, sl)
        bn = [b["name"] for b in job["bodies"]]
        rows = [[os.path.join(cd, "%s_strip.png" % b)] for b in bn]
        rows += [[os.path.join(cd, "%s_%s.png" % (b, v)) for v in ("side", "top", "cctv")] for b in bn]
        rows.append([fc])
        m = next((x for x in json.load(open(P["meta"])) if x["clip"] == n), {})
        sp = compose(os.path.join(out, n + ".png"), rows, title="%s   %d frames  %.2f s  %s%s  speed %.2f m/s   tags %s" % (n, c.F, c.duration, "loop " if m.get("loop") else "", "root motion" if m.get("root_motion") else "in place", m.get("speed_mps", 0), ",".join(m.get("tags", []))))
        sheets.append(sp)
        if video:
            for b in bn:
                vd = os.path.join(cd, "%s_video" % b)
                if not os.path.isdir(vd): continue
                ff = shutil.which("ffmpeg")
                if ff:
                    subprocess.run([ff, "-y", "-loglevel", "error", "-framerate", str(an.get("fps", 30)), "-i", os.path.join(vd, "f%04d.png"), "-vf", "scale=960:-2", "-pix_fmt", "yuv420p", "-c:v", "libx264", os.path.join(cd, "%s.mp4" % b)])
                    subprocess.run([ff, "-y", "-loglevel", "error", "-framerate", str(an.get("fps", 30)), "-i", os.path.join(vd, "f%04d.png"), "-vf", "fps=15,scale=480:-1:flags=lanczos,split[s0][s1];[s0]palettegen=max_colors=96[p];[s1][p]paletteuse", os.path.join(cd, "%s.gif" % b)])
                    shutil.rmtree(vd)
    first_strips = [[os.path.join(out, n, "%s_strip.png" % job["bodies"][0]["name"])] for n in clips]
    set_sheet = compose(os.path.join(P["root"], spec["paths"]["review"], a["id"] + ".png"), first_strips, title="%s  %d clips  (%s)  per-clip sheets: review/%s/<clip>.png" % (a["id"], len(clips), job["bodies"][0]["name"], a["id"]))
    ok = r.returncode == 0 and not rj["errors"]
    print("REVIEW %s %.0fs -> %s + %d clip sheets" % ("OK" if ok else "ERRORS", time.time() - t, os.path.relpath(set_sheet, P["root"]), len(sheets)))
    return ok


# --------------------------------------------------------------------------- manifest / licences / status

def cmd_manifest(p):
    a, P = ctx(p); mp = os.path.join(P["root"], P["spec"]["paths"]["manifest"])
    lock = open(mp + ".lock", "w"); fcntl.flock(lock, fcntl.LOCK_EX)
    man = json.load(open(mp)) if os.path.exists(mp) else []
    meta = json.load(open(P["meta"])) if os.path.exists(P["meta"]) else []
    kinds = sorted({s.get("kind", "mocap") for s in a["sources"].values()})
    adds = ["%s (base %s)" % (m["clip"], m["base"]) for m in meta if m.get("additive")]
    notes = "%d clips: %s%s; meta %s" % (len(meta), ", ".join(m["clip"] for m in meta), ("; additive: " + ", ".join(adds)) if adds else "", os.path.basename(P["meta"]))
    if a.get("notes"): notes = a["notes"] + " | " + notes
    entry = {"id": a["id"], "path": os.path.relpath(P["glb"], P["root"]), "category": "anim", "tier": a.get("tier", "S"),
             "status": a.get("status", "wip"), "source": ("make" if kinds == ["hand"] else kinds[0]) if len(kinds) == 1 else "mocap",
             "license": "project-owned (keyed)" if kinds == ["hand"] else "see LICENSES.md", "notes": notes}
    man = [e for e in man if e.get("id") != a["id"]] + [entry]; man.sort(key=lambda e: e["id"])
    tmp = mp + ".tmp"; json.dump(man, open(tmp, "w"), indent=2); os.replace(tmp, mp)
    fcntl.flock(lock, fcntl.LOCK_UN); lock.close()
    print("MANIFEST upsert %s (%s)" % (a["id"], entry["status"])); return True


def resolve_src(f, P):
    try: return anim_build.resolve_source(f, P["root"], P["set_dir"])
    except SystemExit: return f


def cmd_licenses(p, write=False):
    a, P = ctx(p); rows = []
    for k, s in a["sources"].items():
        rows.append("| %s (%s) | %s | %s | %s | %s | %s |" % (a["id"], os.path.basename(s["file"]), s.get("url", ""), s.get("license", "?"), s.get("credit", "?"),
                                                             s.get("acquired", datetime.date.today().isoformat()), "retargeted to char_skeleton_ref by game-animation; source take in src/anim/%s" % os.path.relpath(os.path.dirname(resolve_src(s["file"], P)), os.path.join(P["root"], "src", "anim"))))
    lp = os.path.join(P["root"], P["spec"]["paths"]["licenses"])
    have = open(lp).read() if os.path.exists(lp) else ""
    new = [r for r in rows if r.split("|")[1].strip() not in have]
    for r in rows: print(r)
    if write and new:
        open(lp, "a").write(("" if have.endswith("\n") else "\n") + "\n".join(new) + "\n"); print("LICENSES +%d rows" % len(new))
    return True


def cmd_status(argv):
    sp = find_spec(os.path.join(argv[1] if len(argv) > 1 and argv[0] == "--assets" else os.getcwd(), "x"))
    spec = json.load(open(sp)); root = os.path.dirname(sp); an = spec.get("animation", {})
    mp = os.path.join(root, spec["paths"]["manifest"]); man = {e["id"]: e for e in (json.load(open(mp)) if os.path.exists(mp) else [])}
    base = os.path.join(root, spec["paths"]["src"], an.get("src_dir", "anim"))
    for dp, _, fs in sorted(os.walk(base)):
        if an.get("set_file", "animset.json") in fs and "_work" not in dp:
            a = json.load(open(os.path.join(dp, an.get("set_file", "animset.json"))))
            v = os.path.join(dp, "_work", "validate.json"); vr = json.load(open(v))["result"] if os.path.exists(v) else "-"
            P = anim_build.paths_for(os.path.join(dp, an.get("set_file", "animset.json")), a)
            print("%-28s %-12s clips=%-3d export=%-3s validate=%-5s manifest=%s" % (a["id"], a.get("status", ""), len(a.get("clips", [])), "yes" if os.path.exists(P["glb"]) else "no", vr, man.get(a["id"], {}).get("status", "-")))


def main():
    if len(sys.argv) < 2: print(__doc__); sys.exit(2)
    c, rest = sys.argv[1], sys.argv[2:]
    if c == "claim": return cmd_claim(rest)
    if c == "new": return cmd_new(rest)
    if c == "scan": return cmd_scan(rest)
    if c == "status": return cmd_status(rest)
    files = [x for x in rest if x.endswith(".json")]; flags = [x for x in rest if not x.endswith(".json")]
    ok = True
    for p in files:
        if c == "build": ok &= cmd_build(p, [x for x in flags if x.startswith("anim_")] or None)
        elif c == "validate": ok &= cmd_validate(p)
        elif c == "playback": ok &= cmd_playback(p, flags)
        elif c == "review": ok &= cmd_review(p, flags)
        elif c == "manifest": ok &= cmd_manifest(p)
        elif c == "blend":
            import fp_build
            ok &= fp_build.write_blend(p)
        elif c == "licenses": ok &= cmd_licenses(p, "--write" in flags)
        elif c == "all":
            s = cmd_build(p) and cmd_validate(p, quiet=True)
            if s: s = cmd_playback(p, flags)
            if s: s = cmd_review(p, flags)
            if s: cmd_manifest(p)
            ok &= bool(s)
        else: print(__doc__); sys.exit(2)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
