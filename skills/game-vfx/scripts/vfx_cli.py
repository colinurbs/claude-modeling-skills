#!/usr/bin/env python3
"""
vfx_cli.py -- pipeline driver for game-vfx (plain CPython + numpy/cv2; Babylon + Playwright for review).

  vfx_cli.py claim [pattern] [--owner N] [--list] [--release]   assets/claims.json (default vfx_* -> spec vfx.owner)
  vfx_cli.py new <vfx_id> --class muzzle|shell|impact|smoke|fire|explosion|spark|ambient [--force]
                                                                scaffold assets/src/vfx/<id>/vfx.json from the class template
  vfx_cli.py tex [sheet ...] [--preview]                        paint the sheets in src/vfx/sheets.json -> export/vfx/tex/
  vfx_cli.py build <vfx.json ...>                               brief -> export/vfx/<id>.json (resolved colours, textures, estimates)
  vfx_cli.py validate <vfx.json ...>                            validate_vfx.py -> <id>/_work/validate.json
  vfx_cli.py review <vfx.json ...>                              headless Babylon: strips day/night, context, CCTV, overdraw,
                                                                particle count -> review/<id>/ + review/<id>.png
  vfx_cli.py playback [--engine DIR]                            vitest + NullEngine: every export/vfx/*.json builds and runs,
                                                                decal hooks resolve through the engine's DecalSystem
  vfx_cli.py manifest <vfx.json ...>                            upsert assets/manifest.json (status from the brief)
  vfx_cli.py all <vfx.json ...>                                 build -> validate -> review -> manifest (stops at the first failure)
  vfx_cli.py status                                             briefs vs exports vs validate vs manifest

Owner: $GAM_OWNER (same claims file as game-asset-modeling / game-animation). Reviews hold EVERY review slot on
the machine (shared with those skills): one headless browser at a time, never the game client.
"""
import fcntl, json, os, re, shutil, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import vfxlib as vl  # noqa: E402  (puts game-asset-modeling/scripts on sys.path)
import gam_cli  # noqa: E402  (claims, Slot)
GANIM = os.path.join(os.path.dirname(SKILL), "game-animation", "scripts")


def exclusive_review():
    """game-animation's ExclusiveReview (all review slots), imported, not copied."""
    if GANIM not in sys.path: sys.path.insert(0, GANIM)
    from anim_cli import ExclusiveReview
    return ExclusiveReview()


# --------------------------------------------------------------------------- claim / new / tex

def cmd_claim(argv):
    if "--list" not in argv and not any(not x.startswith("--") for x in argv): argv = ["vfx_*"] + argv
    if "--owner" not in argv and "--list" not in argv and not os.environ.get("GAM_OWNER"):
        spec, _, _ = vl.project(); argv = argv + ["--owner", spec["vfx"].get("owner", "vfx"), "--note", "game-vfx skill: vfx_ effects"]
    gam_cli.cmd_claim(argv)


def cmd_new(argv):
    import argparse
    ap = argparse.ArgumentParser(prog="vfx_cli.py new"); ap.add_argument("id"); ap.add_argument("--class", dest="cls", required=True); ap.add_argument("--force", action="store_true")
    a = ap.parse_args(argv); spec, sp, root = vl.project()
    if not re.match(spec["vfx"]["id_regex"], a.id): raise SystemExit("id must match %s" % spec["vfx"]["id_regex"])
    if a.cls not in spec["vfx"]["classes"]: raise SystemExit("class one of %s" % list(spec["vfx"]["classes"]))
    gam_cli.claims_check(root, spec, a.id)
    d = os.path.join(root, spec["paths"]["src"], spec["vfx"]["src_dir"], a.id); os.makedirs(d, exist_ok=True)
    p = os.path.join(d, spec["vfx"]["brief_file"])
    if os.path.exists(p) and not a.force: raise SystemExit("exists: %s (--force)" % p)
    tpl = json.load(open(os.path.join(SKILL, "templates", "vfx.json")))
    b = tpl["classes"].get(a.cls) or tpl["classes"]["impact"]
    b = dict(b, id=a.id, **{"class": a.cls}); b["seed"] = sum(map(ord, a.id)) % 997
    json.dump(b, open(p, "w"), indent=1); print(p)


def cmd_tex(argv):
    import vfx_tex
    spec, sp, root = vl.project(); V = spec["vfx"]
    sheets = json.load(open(os.path.join(root, spec["paths"]["src"], V["sheets_file"])))["sheets"]
    out = os.path.join(root, spec["paths"]["export"], V["tex_export_dir"])
    names = [x for x in argv if not x.startswith("--")]
    done = vfx_tex.paint_all(sheets, out, names or None)
    prev = os.path.join(root, spec["paths"]["review"], "vfx_sheets.png")
    vfx_tex.contact_sheet(sorted(os.path.join(out, n + ".png") for n in sheets), prev)
    print("TEX %d sheets -> %s; preview %s" % (len(done), os.path.relpath(out, root), os.path.relpath(prev, root)))
    return True


# --------------------------------------------------------------------------- build / validate

def cmd_build(p):
    P0 = vl.paths_for(p); gam_cli.claims_check(P0["root"], P0["spec"], P0["id"])
    P, out = vl.build(p); e = out["estimates"]
    print("BUILD %s -> %s  (%d emitters, peak %d particles, fill %.2f, ends %.2fs)" % (P["id"], os.path.relpath(P["json"], P["root"]), len(out["emitters"]), e["peak_alive"], e["fill_peak"], e["end_s"]))
    return True


def cmd_validate(p, quiet=False):
    import validate_vfx
    P = vl.paths_for(p)
    if not os.path.exists(P["json"]): print("VALIDATE FAIL: not built (%s)" % P["json"]); return False
    rep, summ = validate_vfx.run(P["json"], P["spec"], P["root"])
    nf = len(rep.failed); os.makedirs(P["work"], exist_ok=True)
    json.dump({"id": P["id"], "result": "FAIL" if nf else "PASS", "items": rep.items, "summary": summ}, open(os.path.join(P["work"], "validate.json"), "w"), indent=1)
    for it in rep.items:
        if not quiet or it["level"] in ("FAIL", "WARN"): print("%-4s  %-28s %s" % (it["level"], it["check"], it["detail"]))
    print("VALIDATE %s %s: %d fail, %d warn" % (P["id"], "FAIL" if nf else "PASS", nf, sum(1 for i in rep.items if i["level"] == "WARN")))
    return nf == 0


# --------------------------------------------------------------------------- review

def manifest_path(P, aid):
    mp = os.path.join(P["root"], P["spec"]["paths"]["manifest"])
    for e in (json.load(open(mp)) if os.path.exists(mp) else []):
        if e.get("id") == aid and os.path.exists(os.path.join(P["root"], e["path"])): return os.path.join(P["root"], e["path"])
    return None


def review_job(P, brief, eff):
    spec = P["spec"]; rv = brief.get("review", {}); ctx = dict(rv)
    if rv.get("context") == "weapon":
        g = manifest_path(P, rv["weapon"]); ctx["glb"] = g
        if not g: raise SystemExit("review weapon %s not found" % rv["weapon"])
    elif rv.get("context") == "surface":
        lib = json.load(open(os.path.join(P["root"], spec["paths"]["materials_dir"], rv.get("lib", "props") + ".json")))[rv["material"]]
        ctx["tex"] = {k: os.path.join(P["root"], spec["paths"]["tex"], lib[k]) for k in ("base", "orm", "normal") if lib.get(k)}; ctx["tile_m"] = lib.get("tile_m", 2.0)
        if not ctx["tex"] and lib.get("base_color") is not None:   # constant material (e.g. BLEND glass): colour, opacity, roughness
            bc = lib["base_color"]; bc = bc if isinstance(bc, str) else "#%02X%02X%02X" % tuple(int(round(min(1, max(0, c)) * 255)) for c in bc[:3])
            ctx["const"] = {"color": bc, "opacity": lib.get("opacity", 1.0) if lib.get("alpha") == "BLEND" else 1.0, "roughness": lib.get("roughness", 0.9), "metallic": lib.get("metallic", 0.0)}
    elif rv.get("context") == "body":
        ctx["glb"] = manifest_path(P, rv["body"])
    elif rv.get("context") == "ground" and rv.get("prop"):   # volumes/props: optional context glb (drum, chimney stack, body)
        ctx["glb"] = manifest_path(P, rv["prop"])
        if not ctx["glb"]: raise SystemExit("review prop %s not found" % rv["prop"])
        if rv.get("anim"):   # round 6: the prop body plays a real clip (review.anim {set, clip, start_s, inputs}); retarget data from the skeleton
            A = dict(rv["anim"]); A["glb"] = manifest_path(P, A["set"]) or os.path.join(P["root"], spec["paths"]["export"], "anim", A["set"] + ".glb")
            if not os.path.exists(A["glb"]): raise SystemExit("review anim set %s not found" % A["set"])
            an = spec.get("animation", {}); sk = json.load(open(os.path.join(P["root"], an.get("skeleton_json", "export/char/char_skeleton_ref.skeleton.json"))))
            A["bones"] = [b["name"] for b in sk["bones"]]; A["parents"] = {b["name"]: b["parent"] for b in sk["bones"]}
            A["hips_rest_src"] = an.get("engine", {}).get("hips_rest_src", [0, 0.95, 0]); ctx["anim"] = A
    dec = json.load(open(os.path.join(P["root"], spec["paths"]["materials_dir"], "decals.json")))["m_decal_runtime"]
    dhook = (eff.get("hooks") or {}).get("decal")
    decal = None
    if dhook and dhook.get("id") in dec["decals"]:
        decal = {"id": dhook["id"], "place": dhook.get("place", "surface"), "def": dec["decals"][dhook["id"]],
                 "base": os.path.join(P["root"], spec["paths"]["tex"], dec["base"]), "roughness": dec.get("roughness", 0.9)}
    dur = eff["duration_s"]
    # strip times: the first frames hold the flash/pop, then spread over the life
    times = sorted({0.0, 1 / 60, 2 / 60, 4 / 60, 8 / 60} | {round(dur * f, 4) for f in (0.25, 0.5, 0.8)})
    times = [t for t in times if t < dur][:8]
    if rv.get("times"): times = list(rv["times"])[:8]       # loops: sample the settled volume, not the first frames
    est = vl.estimate(eff, vl.ref_for(eff, spec))
    return {"spec": P["spec_path"], "out": P["review"], "effect": eff, "context": ctx, "decal": decal, "times": times,
            "texture_base": os.path.join(P["root"], spec["paths"]["export"], spec["vfx"]["export_dir"]),
            "review": spec["vfx"]["review"], "grade": spec["vfx"]["grade_check"], "fp": spec["animation"]["first_person"],
            "peak_t": est["fill_peak_t"], "palette": spec["palette"]["swatches"]}


def chart_counts(path, rj, eff, budget, spec):
    from PIL import Image, ImageDraw
    W, H = 960, 220; im = Image.new("RGB", (W, H), (27, 28, 28)); d = ImageDraw.Draw(im)
    c = rj.get("counts", []); est = vl.estimate(eff, vl.ref_for(eff, spec))["alive_curve"]
    n = max(len(c), len(est), 2); top = max([budget] + c + est + [1]) * 1.1
    X = lambda k: 50 + (W - 70) * k / (n - 1); Y = lambda v: H - 30 - (H - 60) * v / top
    d.line([50, Y(budget), W - 20, Y(budget)], fill=(150, 60, 50)); d.text((W - 160, Y(budget) - 14), "budget %d" % budget, fill=(200, 110, 90))
    if est: d.line([(X(k), Y(v)) for k, v in enumerate(est)], fill=(120, 120, 110), width=1)
    if c: d.line([(X(k), Y(v)) for k, v in enumerate(c)], fill=(224, 160, 48), width=2)
    for gv in range(0, int(top) + 1, max(1, int(top / 4))): d.text((6, Y(gv) - 6), str(gv), fill=(150, 150, 140))
    d.text((50, 6), "alive particles over time (orange = measured in Babylon, grey = data estimate at life max); %d frames @60 Hz; peak %d" % (len(c), max(c or [0])), fill=(230, 226, 214))
    im.save(path); return path


def cmd_review(p):
    P = vl.paths_for(p); brief = json.load(open(p))
    if not os.path.exists(P["json"]): print("REVIEW: build first"); return False
    eff = json.load(open(P["json"]))
    if eff.get("class") == "shader":   # round 5: render the reference GLSL snippet itself (vfx_shader_review.py)
        import vfx_shader_review
        return vfx_shader_review.review(P, brief, eff, exclusive_review)
    job = review_job(P, brief, eff)
    os.makedirs(P["review"], exist_ok=True); os.makedirs(P["work"], exist_ok=True)
    jp = os.path.join(P["work"], "review_job.json"); json.dump(job, open(jp, "w"), indent=1)
    t = time.time()
    with exclusive_review():
        r = subprocess.run(["node", os.path.join(SKILL, "tools", "review", "shoot_vfx.mjs"), "--job", jp], capture_output=True, text=True, timeout=1800)
    print(r.stdout[-1500:])
    if r.returncode != 0: print(r.stderr[-2500:])
    rjp = os.path.join(P["review"], "review.json")
    rj = json.load(open(rjp)) if os.path.exists(rjp) else {"errors": ["no review.json"]}
    for e in rj.get("errors", [])[:10]: print("ENGINE ERROR:", str(e)[:300])
    C = P["spec"]["vfx"]["classes"][eff["class"]]
    rj["_spec"] = P["spec_path"]
    cp = chart_counts(os.path.join(P["review"], "counts.png"), rj, eff, C["max_particles"], P["spec"])
    rows = [[os.path.join(P["review"], "strip_day.png")], [os.path.join(P["review"], "strip_night.png")],
            [os.path.join(P["review"], v) for v in ("context_day.png", "context_night.png")],
            [os.path.join(P["review"], "cctv.png")], [os.path.join(P["review"], "overdraw.png"), cp]]
    if os.path.exists(os.path.join(P["review"], "side.png")): rows.insert(2, [os.path.join(P["review"], "side.png")])
    if GANIM not in sys.path: sys.path.insert(0, GANIM)
    from anim_cli import compose
    m = rj.get("measure", {})
    title = "%s  [%s]  %.2fs  peak %s particles (budget %d)  overdraw peak %s layers, fill %s  clipped %s%%  %s" % (
        eff["id"], eff["class"], eff["duration_s"], m.get("peak_particles", "?"), C["max_particles"], m.get("overdraw_peak", "?"), m.get("overdraw_fill", "?"),
        m.get("clipped_pct", "?"), "decal " + job["decal"]["id"] if job["decal"] else "")
    compose(P["sheet_png"], rows, width=1920, title=title)
    # gates measured in the engine
    gate = []
    if m:
        if m.get("peak_particles", 0) > C["max_particles"]: gate.append("measured peak %d > %d" % (m["peak_particles"], C["max_particles"]))
        gc = P["spec"]["vfx"]["grade_check"]
        if m.get("clipped_pct", 0) / 100 > gc["max_clipped_fraction"]: gate.append("clipped %.1f%% > %.1f%% (blow-out)" % (m["clipped_pct"], gc["max_clipped_fraction"] * 100))
        sp_ = m.get("sheet_probe") or {}
        import numpy as np, cv2
        for tex, cells in sp_.items():
            im = cv2.cvtColor(cv2.imread(os.path.join(job["texture_base"], eff["textures"][tex]["path"]), cv2.IMREAD_UNCHANGED), cv2.COLOR_BGRA2RGBA).astype(np.float32) / 255
            cols, rows = eff["textures"][tex]["grid"]; ch, cw = im.shape[0] // rows, im.shape[1] // cols
            def cell_mean(k):
                r_, c_ = divmod(int(k), cols); c = im[r_ * ch:(r_ + 1) * ch, c_ * cw:(c_ + 1) * cw]; a = c[..., 3:4]
                return (c[..., :3] * a).sum((0, 1)) / max(1e-6, a.sum())
            ks = sorted(cells, key=int)
            if len(ks) == 2 and all(cells[k] for k in ks):
                a0, a1 = cell_mean(ks[0]), cell_mean(ks[1]); r0, r1 = np.array(cells[ks[0]][:3]), np.array(cells[ks[1]][:3])
                # scale-invariant: the rendered probe is darkened by lighting/coverage, so compare each pair about its own mean
                sa, sr = max(1e-6, (a0 + a1).mean() / 2), max(1e-6, (r0 + r1).mean() / 2); a0, a1, r0, r1 = a0 / sa, a1 / sa, r0 / sr, r1 / sr
                if np.abs(a0 - a1).sum() > 0.05 and np.linalg.norm(r0 - a0) + np.linalg.norm(r1 - a1) > np.linalg.norm(r0 - a1) + np.linalg.norm(r1 - a0):
                    gate.append("sheet %s renders cells in the wrong order (rows flipped / invertY)" % tex)
        om = C.get("overdraw_fp_max") or C.get("overdraw_max")
        if om and m.get("overdraw_fill", 0) > om: gate.append("measured fill %.2f > %.1f" % (m["overdraw_fill"], om))
    rj["gates"] = gate; json.dump({k: v for k, v in rj.items() if k != "_spec"}, open(rjp, "w"), indent=1)
    ok = r.returncode == 0 and not rj.get("errors") and not gate
    print("REVIEW %s %s %.0fs -> %s %s" % (P["id"], "OK" if ok else "ISSUES", time.time() - t, os.path.relpath(P["sheet_png"], P["root"]), "; ".join(gate)))
    return ok


# --------------------------------------------------------------------------- playback

def find_bin(start, name):
    d = start
    while True:
        c = os.path.join(d, "node_modules", ".bin", name)
        if os.path.exists(c): return c
        if os.path.dirname(d) == d: return None
        d = os.path.dirname(d)


def cmd_playback(argv=()):
    spec, sp, root = vl.project()
    eng = None
    for c in ((argv[argv.index("--engine") + 1] if "--engine" in argv else None), os.environ.get("GVFX_ENGINE"), os.path.join(root, spec["animation"].get("engine_dir", "../packages/engine"))):
        if c and os.path.exists(os.path.join(c, "src", "render", "decals.ts")): eng = os.path.realpath(c); break
    if not eng: raise SystemExit("engine not found (--engine packages/engine)")
    # Each run gets its own dir (run-<time>-<pid>) with its own test, log and a SNAPSHOT of export/vfx/*.json, spec and
    # decals, so concurrent runs and mid-run rebuilds by other agents cannot mix into it. Kept on failure, removed on PASS;
    # playback.log in the parent always holds the latest run's log (backward compatible).
    base = os.path.join(root, spec["paths"]["src"], spec["vfx"]["src_dir"], "_work", "playback"); os.makedirs(base, exist_ok=True)
    d = os.path.join(base, "run-%s-%d" % (time.strftime("%Y%m%d-%H%M%S"), os.getpid())); os.makedirs(d)
    src_dir = os.path.join(root, spec["paths"]["export"], spec["vfx"]["export_dir"]); snap = os.path.join(d, "export_vfx"); os.makedirs(snap)
    for f in sorted(os.listdir(src_dir)):
        if not (f.startswith("vfx_") and f.endswith(".json")): continue
        for _ in range(5):   # a file being rewritten by another agent's build: wait until it parses
            try: txt = open(os.path.join(src_dir, f)).read(); json.loads(txt); break
            except (ValueError, OSError): time.sleep(0.5)
        else: raise SystemExit("playback: %s does not parse (being rebuilt?); retry" % f)
        open(os.path.join(snap, f), "w").write(txt)
    n_snap = len(os.listdir(snap))
    shutil.copy(sp, os.path.join(d, "spec.json")); shutil.copy(os.path.join(root, spec["paths"]["materials_dir"], "decals.json"), os.path.join(d, "decals.json"))
    shutil.copy(os.path.join(SKILL, "tools", "playback", "vfx_playback.test.ts"), d)
    open(os.path.join(d, "vitest.config.mjs"), "w").write("export default { test: { include: ['vfx_playback.test.ts'], testTimeout: 120000, pool: 'forks', poolOptions: { forks: { singleFork: true } } } };\n")
    vit = find_bin(eng, "vitest")
    if not vit: raise SystemExit("vitest not found above %s" % eng)
    env = dict(os.environ, GVFX_ENGINE=eng, GVFX_DIR=snap, GVFX_SPEC=os.path.join(d, "spec.json"),
               GVFX_RUNTIME=os.path.join(SKILL, "tools", "runtime", "vfx_babylon.mjs"), GVFX_DECALS=os.path.join(d, "decals.json"))
    r = subprocess.run([vit, "run", "--config", os.path.join(d, "vitest.config.mjs"), "--root", d, "--reporter", "verbose"], cwd=d, env=env, capture_output=True, text=True, timeout=600)
    txt = r.stdout + r.stderr; open(os.path.join(d, "playback.log"), "w").write(txt)
    tmp = os.path.join(base, "playback.log.%d" % os.getpid()); open(tmp, "w").write("# run: %s\n%s" % (os.path.basename(d), txt)); os.replace(tmp, os.path.join(base, "playback.log"))
    for l in txt.splitlines():
        s = re.sub(r"\x1b\[[0-9;]*m", "", l)
        if re.search(r"(✓|×|✗|FAIL|Tests |Test Files|AssertionError|Error:|expected)", s): print("  " + s.strip()[:220])
    ok = r.returncode == 0
    if ok: shutil.rmtree(d, ignore_errors=True)
    print("PLAYBACK %s (%d effects snapshotted) -> %s" % ("PASS" if ok else "FAIL", n_snap, os.path.relpath(os.path.join(base if ok else d, "playback.log"), root)))
    return ok


# --------------------------------------------------------------------------- manifest / status

def cmd_manifest(p):
    P = vl.paths_for(p); b = json.load(open(p)); mp = os.path.join(P["root"], P["spec"]["paths"]["manifest"])
    eff = json.load(open(P["json"])) if os.path.exists(P["json"]) else {}
    lock = open(mp + ".lock", "w"); fcntl.flock(lock, fcntl.LOCK_EX)
    man = json.load(open(mp)) if os.path.exists(mp) else []
    est = eff.get("estimates", {})
    notes = "%s | %d emitters, peak %s particles, %.2fs; textures %s; hooks %s" % (b.get("notes", "")[:160], len(eff.get("emitters", [])), est.get("peak_alive", "?"), eff.get("duration_s", 0),
                                                                            ", ".join(eff.get("textures", {})), ", ".join("%s=%s" % (k, (v.get("id") if isinstance(v, dict) and "id" in v else ("yes" if isinstance(v, dict) else v))) for k, v in (eff.get("hooks") or {}).items() if v))
    entry = {"id": b["id"], "path": os.path.relpath(P["json"], P["root"]), "category": "vfx", "tier": b.get("tier", "S"), "status": b.get("status", "wip"),
             "source": "make", "license": "project-owned (procedural)", "notes": notes}
    man = [e for e in man if e.get("id") != b["id"]] + [entry]; man.sort(key=lambda e: e["id"])
    tmp = mp + ".tmp"; json.dump(man, open(tmp, "w"), indent=2); os.replace(tmp, mp)
    fcntl.flock(lock, fcntl.LOCK_UN); lock.close()
    print("MANIFEST upsert %s (%s)" % (b["id"], entry["status"])); return True


def cmd_status():
    spec, sp, root = vl.project(); V = spec["vfx"]
    mp = os.path.join(root, spec["paths"]["manifest"]); man = {e["id"]: e for e in (json.load(open(mp)) if os.path.exists(mp) else [])}
    base = os.path.join(root, spec["paths"]["src"], V["src_dir"])
    for dp, _, fs in sorted(os.walk(base)):
        if V["brief_file"] in fs and "_work" not in dp:
            b = json.load(open(os.path.join(dp, V["brief_file"]))); P = vl.paths_for(os.path.join(dp, V["brief_file"]))
            v = os.path.join(P["work"], "validate.json"); vr = json.load(open(v))["result"] if os.path.exists(v) else "-"
            rv = os.path.join(P["review"], "review.json"); rr = ("ok" if not json.load(open(rv)).get("gates") else "gates") if os.path.exists(rv) else "-"
            print("%-24s %-9s %-6s export=%-3s validate=%-5s review=%-5s manifest=%s" % (b["id"], b.get("class"), b.get("status", ""), "yes" if os.path.exists(P["json"]) else "no", vr, rr, man.get(b["id"], {}).get("status", "-")))


def main():
    if len(sys.argv) < 2: print(__doc__); sys.exit(2)
    c, rest = sys.argv[1], sys.argv[2:]
    if c == "claim": return cmd_claim(rest)
    if c == "new": return cmd_new(rest)
    if c == "tex": sys.exit(0 if cmd_tex(rest) else 1)
    if c == "status": return cmd_status()
    if c == "playback": sys.exit(0 if cmd_playback(rest) else 1)
    files = [x for x in rest if x.endswith(".json")]; ok = True
    for p in files:
        if c == "build": ok &= cmd_build(p)
        elif c == "validate": ok &= cmd_validate(p)
        elif c == "review": ok &= cmd_review(p)
        elif c == "manifest": ok &= cmd_manifest(p)
        elif c == "all":
            s = cmd_build(p) and cmd_validate(p, quiet=True)
            if s: s = cmd_review(p)
            if s: cmd_manifest(p)
            ok &= bool(s)
        else: print(__doc__); sys.exit(2)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
