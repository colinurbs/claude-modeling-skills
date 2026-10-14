#!/usr/bin/env python3
"""
gam_cli.py -- per-asset pipeline driver for game-asset-modeling (plain CPython).

  gam_cli.py new <id> --category phys [--tier S] [--track invented] [--assets DIR]
        scaffold <assets>/src/<src_dir>/<id>/{brief.json, build_<id>.py}
  gam_cli.py build <brief.json>         run build_<id>.py headless in Blender
  gam_cli.py validate <brief.json>      validate_glb.py on the exported .glb
  gam_cli.py review <brief.json>        Babylon render set -> <assets>/review/<id>/ + <id>.png sheet
  gam_cli.py manifest <brief.json>      upsert the manifest entry (status from the brief)
  gam_cli.py all <brief.json> [...]     build -> validate -> review -> manifest (stops on failure)
  gam_cli.py status [--assets DIR]      table of briefs vs exports vs manifest
  gam_cli.py claim <pattern> --owner N  claim an id pattern (assets/claims.json); --list, --release
        Set $GAM_OWNER in every session/agent: new/build refuse ids claimed by another owner.

Blender: $BLENDER or /Applications/Blender.app/Contents/MacOS/Blender or `blender` on PATH.
Several `build`/`all` runs can go in parallel (separate processes); keep MCP for inspection.
"""
import json, os, shutil, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)
TOOLS = os.path.join(SKILL, "tools")
sys.path.insert(0, HERE)
from validate_glb import find_spec, run as validate_run  # noqa: E402


class Slot:
    """Machine-wide cap on concurrent heavy jobs (parallel agents share one machine).
    GAM_SLOTS_BUILD / GAM_SLOTS_REVIEW override the default of 3 each."""
    def __init__(self, kind, n):
        import tempfile
        self.n = int(os.environ.get("GAM_SLOTS_" + kind.upper(), n)); self.kind = kind; self.fh = None
        self.dir = os.path.join(tempfile.gettempdir(), "gam_slots"); os.makedirs(self.dir, exist_ok=True)
    def __enter__(self):
        import fcntl
        waited = 0
        while True:
            for i in range(self.n):
                fh = open(os.path.join(self.dir, "%s_%d.lock" % (self.kind, i)), "w")
                try:
                    fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB); self.fh = fh
                    if waited: print("GAM_SLOT %s acquired after %ds" % (self.kind, waited))
                    return self
                except OSError:
                    fh.close()
            time.sleep(2); waited += 2
    def __exit__(self, *a):
        import fcntl
        fcntl.flock(self.fh, fcntl.LOCK_UN); self.fh.close()


def blender_bin():
    for c in (os.environ.get("BLENDER"), "/Applications/Blender.app/Contents/MacOS/Blender", shutil.which("blender")):
        if c and os.path.exists(c): return c
    raise SystemExit("Blender not found; set $BLENDER")


def claims_check(root, spec, asset_id, strict=True):
    """assets/claims.json: [{"pattern": "prop_car_*", "owner": "irongrip-ba", "note": ...}].
    Refuse ids claimed by another owner. Owner = $GAM_OWNER (set it in every agent/session);
    unset owner only warns. Prevents two sessions building the same family."""
    import fnmatch
    cp = os.path.join(root, spec["paths"].get("claims", "claims.json"))
    if not os.path.exists(cp): return True
    me = os.environ.get("GAM_OWNER", "")
    for c in json.load(open(cp)):
        if fnmatch.fnmatch(asset_id, c["pattern"]):
            if c["owner"] == me: return True
            msg = "CLAIMED: %s matches %s owned by %s (%s). Set GAM_OWNER or pick another id." % (asset_id, c["pattern"], c["owner"], c.get("note", ""))
            if me and strict: raise SystemExit(msg)
            print("WARN " + msg + " (GAM_OWNER unset: warning only)")
            return False
    return True


def cmd_claim(argv):
    """gam_cli.py claim <pattern> --owner NAME [--note TEXT] [--assets DIR] | claim --list"""
    import argparse, fcntl
    ap = argparse.ArgumentParser(prog="gam_cli.py claim")
    ap.add_argument("pattern", nargs="?"); ap.add_argument("--owner"); ap.add_argument("--note", default="")
    ap.add_argument("--assets"); ap.add_argument("--list", action="store_true"); ap.add_argument("--release", action="store_true")
    a = ap.parse_args(argv)
    spec_path = find_spec(os.path.join(a.assets or os.getcwd(), "x")); spec = json.load(open(spec_path))
    cp = os.path.join(os.path.dirname(spec_path), spec["paths"].get("claims", "claims.json"))
    lock = open(cp + ".lock", "w"); fcntl.flock(lock, fcntl.LOCK_EX)
    cl = json.load(open(cp)) if os.path.exists(cp) else []
    if a.list or not a.pattern:
        for c in cl: print("%-28s %-16s %s" % (c["pattern"], c["owner"], c.get("note", "")))
        return
    owner = a.owner or os.environ.get("GAM_OWNER")
    if not owner: raise SystemExit("--owner or $GAM_OWNER required")
    other = [c for c in cl if c["pattern"] == a.pattern and c["owner"] != owner]
    if other and not a.release: raise SystemExit("already claimed by %s" % other[0]["owner"])
    cl = [c for c in cl if c["pattern"] != a.pattern]
    if not a.release: cl.append({"pattern": a.pattern, "owner": owner, "note": a.note})
    tmp = cp + ".tmp"; json.dump(cl, open(tmp, "w"), indent=2); os.replace(tmp, cp)
    print("%s %s -> %s" % ("released" if a.release else "claimed", a.pattern, owner))


def load(brief_path):
    brief = json.load(open(brief_path)); spec_path = find_spec(brief_path); spec = json.load(open(spec_path))
    root = os.path.dirname(spec_path); cat = spec["categories"][brief["category"]]
    ph = brief.get("status") == "placeholder"
    glb = os.path.join(root, spec["paths"]["placeholder" if ph else "export"], cat["export_dir"], brief["id"] + ".glb")
    return brief, spec, spec_path, root, cat, glb


def cmd_new(argv):
    import argparse
    ap = argparse.ArgumentParser(prog="gam_cli.py new")
    ap.add_argument("id"); ap.add_argument("--category", required=True); ap.add_argument("--tier", default="S")
    ap.add_argument("--track", default="invented", choices=["kit", "invented", "real", "character", "placeholder"])
    ap.add_argument("--assets", default=None); ap.add_argument("--force", action="store_true")
    a = ap.parse_args(argv)
    spec_path = find_spec(os.path.join(a.assets or os.getcwd(), "x"))
    spec = json.load(open(spec_path)); root = os.path.dirname(spec_path)
    cat = spec["categories"][a.category]
    if not a.id.startswith(cat["prefix"]): raise SystemExit("id must start with %s" % cat["prefix"])
    claims_check(root, spec, a.id)
    d = os.path.join(root, spec["paths"]["src"], cat["src_dir"], a.id); os.makedirs(d, exist_ok=True)
    bp = os.path.join(d, "brief.json")
    if os.path.exists(bp) and not a.force: raise SystemExit("exists: %s (use --force)" % bp)
    brief = json.load(open(os.path.join(SKILL, "templates", "brief.json")))
    brief.update(id=a.id, category=a.category, tier=a.tier, track=a.track,
                 status="placeholder" if a.track == "placeholder" else "wip",
                 pivot=cat["pivot"], collider=(cat.get("colliders") or ["none"])[0],
                 budget_class=cat["budget_class"], density_class=cat["density_class"],
                 props={k: None for k in cat.get("required_props", [])})
    json.dump(brief, open(bp, "w"), indent=2)
    tpl = open(os.path.join(SKILL, "templates", "build_asset.py")).read().replace("__ASSET_ID__", a.id)
    sp = os.path.join(d, "build_%s.py" % a.id)
    if not os.path.exists(sp) or a.force: open(sp, "w").write(tpl)
    print(bp); print(sp)


def cmd_build(brief_path, held=False):
    brief, spec, spec_path, root, cat, glb = load(brief_path)
    claims_check(root, spec, brief["id"])
    script = os.path.join(os.path.dirname(os.path.abspath(brief_path)), "build_%s.py" % brief["id"])
    import contextlib
    light = brief.get("light_build", False)       # tiny builds (decals, cards): own lane, never queued behind bakes
    with (contextlib.nullcontext() if held else (Slot("build_light", 2) if light else Slot("build", 3))):
        t = time.time()
        p = subprocess.run([blender_bin(), "-b", "--factory-startup", "--python-exit-code", "1", "--python", script,
                            "--", "--brief", os.path.abspath(brief_path), "--spec", spec_path],
                           capture_output=True, text=True)
    log = os.path.join(os.path.dirname(os.path.abspath(brief_path)), "build.log")
    open(log, "w").write(p.stdout + "\n--- stderr ---\n" + p.stderr)
    noise = ("Not freed memory blocks", "Error: Not freed", "total unfreed memory")
    lines = [l for l in p.stdout.splitlines() if (l.startswith(("GAM_", "Error", "Traceback")) or "Error" in l)
             and not any(n in l for n in noise)]
    print("\n".join(lines[-15:]))
    if p.returncode != 0 or not os.path.exists(glb):
        print(p.stderr[-3000:] or p.stdout[-3000:]); print("BUILD FAILED (%s)" % log); return False
    print("BUILD OK %.1fs -> %s" % (time.time() - t, os.path.relpath(glb, root))); return True


def cmd_validate(brief_path, quiet=False):
    brief, spec, spec_path, root, cat, glb = load(brief_path)
    rep, _ = validate_run(glb, brief, spec, placeholder=brief.get("status") == "placeholder", assets_root=root)
    out = os.path.join(os.path.dirname(os.path.abspath(brief_path)), "validate.json")
    nf = len(rep.failed)
    json.dump({"id": brief["id"], "result": "FAIL" if nf else "PASS", "items": rep.items}, open(out, "w"), indent=1)
    for it in rep.items:
        if not quiet or it["level"] in ("FAIL", "WARN", "INFO"):
            print("%-4s  %-34s %s" % (it["level"], it["check"], it["detail"]))
    print("VALIDATE %s: %d fail, %d warn" % ("FAIL" if nf else "PASS", nf, sum(1 for i in rep.items if i["level"] == "WARN")))
    return nf == 0


def cmd_review(brief_path):
    brief, spec, spec_path, root, cat, glb = load(brief_path)
    out = os.path.join(root, spec["paths"]["review"], brief["id"])
    sheet = os.path.join(root, spec["paths"]["review"], brief["id"] + ".png")
    extra = []
    if brief.get("review_with"):   # context assets (ids or assets-relative paths) rendered alongside
        paths = []
        for w in brief["review_with"]:
            xf = ""
            if isinstance(w, dict):     # {"id"|"path": ..., "offset": [x, y, z] Blender axes, "rot_z": deg}
                o = w.get("offset", [0, 0, 0]); xf = "@%g:%g:%g@%g" % (o[0], o[1], o[2], w.get("rot_z", 0))
                w = w.get("path") or w["id"]
            if w.endswith(".glb"): paths.append(os.path.join(root, w) + xf)
            else:
                hit = [e["path"] for e in (json.load(open(os.path.join(root, spec["paths"]["manifest"]))) if os.path.exists(os.path.join(root, spec["paths"]["manifest"])) else []) if e["id"] == w]
                cands = [os.path.join(root, h) for h in hit] + [os.path.join(root, spec["paths"][k], d, w + ".glb")
                         for k in ("export", "placeholder") for d in {c["export_dir"] for c in spec["categories"].values()}]
                f = next((c for c in cands if os.path.exists(c)), None)
                if f: paths.append(f + xf)
                else: print("review_with: %s not found" % w)
        if paths: extra = ["--with", ",".join(paths)]
    with Slot("review", 3):
        p = subprocess.run(["node", os.path.join(TOOLS, "review", "shoot.mjs"), glb, "--spec", spec_path,
                            "--brief", os.path.abspath(brief_path), "--out", out, "--sheet", sheet] + extra,
                           capture_output=True, text=True, timeout=1800)
    print(p.stdout[-2500:]);
    if p.returncode != 0:
        print(p.stderr[-2500:])
        rj = os.path.join(out, "review.json")
        if os.path.exists(rj):
            for e in json.load(open(rj))["errors"][:10]: print("ENGINE ERROR:", e["text"][:300])
    print("REVIEW %s -> %s" % ("OK" if p.returncode == 0 else "ERRORS", os.path.relpath(sheet, root)))
    return p.returncode == 0


def cmd_manifest(brief_path):
    brief, spec, spec_path, root, cat, glb = load(brief_path)
    mp = os.path.join(root, spec["paths"]["manifest"])
    import fcntl
    lock = open(mp + ".lock", "w"); fcntl.flock(lock, fcntl.LOCK_EX)      # parallel agents share this file
    man = json.load(open(mp)) if os.path.exists(mp) else []
    entry = {"id": brief["id"], "path": os.path.relpath(glb, root), "category": brief["category"],
             "tier": brief.get("tier", "S"), "status": brief.get("status", "wip"),
             "source": brief.get("source", "make"), "license": brief.get("license", "see LICENSES.md"),
             "notes": brief.get("notes", "")}
    man = [e for e in man if e.get("id") != brief["id"]] + [entry]
    man.sort(key=lambda e: e["id"])
    tmp = mp + ".tmp"; json.dump(man, open(tmp, "w"), indent=2); os.replace(tmp, mp)
    fcntl.flock(lock, fcntl.LOCK_UN); lock.close()
    print("MANIFEST upsert %s (%s)" % (brief["id"], entry["status"]))
    return True


def cmd_status(argv):
    spec_path = find_spec(os.path.join(argv[1] if len(argv) > 1 and argv[0] == "--assets" else os.getcwd(), "x"))
    spec = json.load(open(spec_path)); root = os.path.dirname(spec_path)
    mp = os.path.join(root, spec["paths"]["manifest"]); man = {e["id"]: e for e in (json.load(open(mp)) if os.path.exists(mp) else [])}
    rows = []
    for dp, _, fs in os.walk(os.path.join(root, spec["paths"]["src"])):
        if "brief.json" in fs:
            b = json.load(open(os.path.join(dp, "brief.json")))
            v = os.path.join(dp, "validate.json"); vr = json.load(open(v))["result"] if os.path.exists(v) else "-"
            rows.append((b["id"], b.get("tier", ""), b.get("status", ""), vr, man.get(b["id"], {}).get("status", "-")))
    for r in sorted(rows): print("%-40s %-2s %-12s validate=%-5s manifest=%s" % r)


def main():
    if len(sys.argv) < 2: print(__doc__); sys.exit(2)
    c, rest = sys.argv[1], sys.argv[2:]
    if c == "new": return cmd_new(rest)
    if c == "status": return cmd_status(rest)
    if c == "claim": return cmd_claim(rest)
    ok = True
    if c == "build" and len(rest) > 1:
        # several briefs: take ONE machine slot for the whole batch (cheap kit builds starve
        # when each waits for its own slot behind heavy bakes)
        with Slot("build", 3):
            for bp in rest: ok &= cmd_build(bp, held=True)
        sys.exit(0 if ok else 1)
    for bp in rest:
        if c == "build": ok &= cmd_build(bp)
        elif c == "validate": ok &= cmd_validate(bp)
        elif c == "review": ok &= cmd_review(bp)
        elif c == "manifest": ok &= cmd_manifest(bp)
        elif c == "all":
            step_ok = cmd_build(bp) and cmd_validate(bp, quiet=True)
            if step_ok: step_ok = cmd_review(bp)
            if step_ok: cmd_manifest(bp)
            ok &= step_ok
        else: print(__doc__); sys.exit(2)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
