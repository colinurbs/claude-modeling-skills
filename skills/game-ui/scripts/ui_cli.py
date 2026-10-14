#!/usr/bin/env python3
"""
ui_cli.py -- pipeline driver for game-ui (plain CPython + numpy/PIL/resvg/fontTools; Chromium via Playwright for review).

  ui_cli.py claim [pattern] [--owner N] [--list] [--release]   assets/claims.json (default ui_* -> spec ui.owner)
  ui_cli.py new <ui_id> [--force]                               scaffold assets/src/ui/<kind>/<id>/brief.json (+ build_<id>.py
                                                                for panels/templates, glyphs.py for icon sets) from templates/
  ui_cli.py build <brief.json ...>                              brief -> export/ui/... (tokens, fonts, icon SVG + atlases, nine-slices)
  ui_cli.py validate <brief.json ...>                           validate_ui.py -> <id>/_work/validate.json
  ui_cli.py review [<brief.json ...>]                           ONE headless Chromium (DOM, no WebGL): icons at game size on the
                                                                control-room screen, CRT treatment, paper, nine-slice stress,
                                                                incident cards, dossier, a mock console -> review/<id>/ + sheets
  ui_cli.py manifest <brief.json ...>                           upsert assets/manifest.json (status from the brief)
  ui_cli.py all <brief.json ...>                                build -> validate -> manifest (review runs once, separately)
  ui_cli.py status                                              briefs vs exports vs validate vs manifest

Build order matters: fonts -> tokens -> icon sets -> panels -> templates (later kinds read the earlier exports).
Owner: $GAM_OWNER (claims shared with game-asset-modeling / game-animation / game-vfx). Review holds EVERY review slot on
the machine (one headless browser at a time) and never launches the game client.
"""
import json, os, re, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import uilib as L  # noqa: E402  (puts game-asset-modeling/scripts on sys.path)
import gam_cli  # noqa: E402  (claims, manifest lock conventions)

ORDER = ["fonts", "tokens", "iconset", "panel", "template", "gauge", "map", "hud"]   # hud: optional kind (ui_hud_*), built by script   # gauge/map: optional kinds (spec ui.kinds), built by script like panels


def exclusive_review():
    """game-animation's ExclusiveReview (all review slots), imported, not copied."""
    if L.GANIM_SCRIPTS not in sys.path: sys.path.insert(0, L.GANIM_SCRIPTS)
    from anim_cli import ExclusiveReview
    return ExclusiveReview()


def sort_briefs(paths):
    def k(p):
        P = L.paths_for(p); return (ORDER.index(P["kind"]), P["id"])
    return sorted(paths, key=k)


# --------------------------------------------------------------------------- claim / new

def cmd_claim(argv):
    if "--list" not in argv and not any(not x.startswith("--") for x in argv): argv = ["ui_*"] + argv
    if "--owner" not in argv and "--list" not in argv and not os.environ.get("GAM_OWNER"):
        spec, _, _ = L.project(); argv = argv + ["--owner", spec["ui"].get("owner", "ui"), "--note", "game-ui skill: ui_ assets"]
    gam_cli.cmd_claim(argv)


def cmd_new(argv):
    import argparse
    ap = argparse.ArgumentParser(prog="ui_cli.py new"); ap.add_argument("id"); ap.add_argument("--force", action="store_true")
    a = ap.parse_args(argv); spec, sp, root = L.project()
    if not re.match(spec["ui"]["id_regex"], a.id): raise SystemExit("id must match %s" % spec["ui"]["id_regex"])
    kind = L.kind_of(spec, a.id); K = spec["ui"]["kinds"][kind]
    gam_cli.claims_check(root, spec, a.id)
    d = os.path.join(root, spec["paths"]["src"], K["src_dir"], a.id); os.makedirs(d, exist_ok=True)
    p = os.path.join(d, spec["ui"]["brief_file"])
    if os.path.exists(p) and not a.force: raise SystemExit("exists: %s (--force)" % p)
    tpl = json.load(open(os.path.join(SKILL, "templates", "brief.json")))[kind]
    tpl = dict(tpl, id=a.id, kind=kind); json.dump(tpl, open(p, "w"), indent=1); print(p)
    extra = {"panel": "build_panel.py", "template": "build_template.py", "iconset": "glyphs.py"}.get(kind)
    if extra:
        dst = os.path.join(d, "glyphs.py" if kind == "iconset" else "build_%s.py" % a.id)
        if not os.path.exists(dst) or a.force:
            open(dst, "w").write(open(os.path.join(SKILL, "templates", extra)).read().replace("__ID__", a.id)); print(dst)


# --------------------------------------------------------------------------- build / validate / manifest

def cmd_build(p):
    import ui_build as B
    P = L.paths_for(p); gam_cli.claims_check(P["root"], P["spec"], P["id"]); t = time.time()
    fn = {"tokens": B.build_tokens, "fonts": B.build_fonts, "iconset": B.build_iconset, "panel": B.build_script, "template": B.build_script, "gauge": B.build_script, "map": B.build_script, "hud": B.build_script}[P["kind"]]
    fn(P)
    print("BUILD %s (%s) %.1fs -> %s" % (P["id"], P["kind"], time.time() - t, os.path.relpath(P["json"], P["root"])))
    return True


def cmd_validate(p, quiet=False):
    import validate_ui
    P = L.paths_for(p)
    if not os.path.exists(P["json"]): print("VALIDATE FAIL: not built (%s)" % P["json"]); return False
    rep, summ = validate_ui.run(P)
    nf = len(rep.failed); os.makedirs(P["work"], exist_ok=True)
    json.dump({"id": P["id"], "result": "FAIL" if nf else "PASS", "items": rep.items, "summary": summ}, open(os.path.join(P["work"], "validate.json"), "w"), indent=1)
    for it in rep.items:
        if not quiet or it["level"] in ("FAIL", "WARN"): print("%-4s  %-30s %s" % (it["level"], it["check"], it["detail"]))
    print("VALIDATE %s %s: %d fail, %d warn" % (P["id"], "FAIL" if nf else "PASS", nf, sum(1 for i in rep.items if i["level"] == "WARN")))
    return nf == 0


def cmd_manifest(p):
    import fcntl
    P = L.paths_for(p); b = P["brief"]; root = P["root"]
    mp = os.path.join(root, P["spec"]["paths"]["manifest"])
    lock = open(mp + ".lock", "w"); fcntl.flock(lock, fcntl.LOCK_EX)
    man = json.load(open(mp)) if os.path.exists(mp) else []
    entry = {"id": b["id"], "path": os.path.relpath(P["json"], root), "category": "ui", "tier": b.get("tier", "S"),
             "status": b.get("status", "wip"), "source": b.get("source", "make"), "license": b.get("license", "see LICENSES.md"),
             "notes": b.get("manifest_note", "")}
    man = [e for e in man if e.get("id") != b["id"]] + [entry]; man.sort(key=lambda e: e["id"])
    tmp = mp + ".tmp"; json.dump(man, open(tmp, "w"), indent=2); os.replace(tmp, mp)
    fcntl.flock(lock, fcntl.LOCK_UN); lock.close()
    print("MANIFEST upsert %s (%s)" % (b["id"], entry["status"])); return True


# --------------------------------------------------------------------------- review

def cmd_review(paths):
    """One Chromium for every UI asset (the review is a page of DOM plates, cheap to render together)."""
    spec, sp, root = L.project()
    paths = sort_briefs(paths or L.all_briefs(root, spec))
    Ps = [L.paths_for(p) for p in paths]
    exp = os.path.join(root, spec["paths"]["export"])
    R = spec["ui"]["review"]
    copyp = os.path.join(root, spec["paths"]["src"], "ui", "review_copy.json")
    job = {"spec": sp, "root": root, "export": exp, "review_dir": os.path.join(root, spec["paths"]["review"]),
           "tokens": json.load(open(L.tokens_path(root, spec))), "assets": [{"id": P["id"], "kind": P["kind"], "json": json.load(open(P["json"]))} for P in Ps if os.path.exists(P["json"])],
           "review": R, "copy": json.load(open(copyp)) if os.path.exists(copyp) else {},
           "feed_image": os.path.join(root, R["feed_image"]), "portraits": sorted(os.path.join(root, R["portrait_dir"], f) for f in os.listdir(os.path.join(root, R["portrait_dir"])) if f.endswith(".png"))}
    # project review plates (optional, additive): <src>/ui/_review/*.mjs, each `export default async (H) => {...}` with the harness helpers
    pd = os.path.join(root, spec["paths"]["src"], "ui", "_review")
    job["plate_modules"] = sorted(os.path.join(pd, f) for f in os.listdir(pd) if f.endswith(".mjs")) if os.path.isdir(pd) else []
    work = os.path.join(root, spec["paths"]["src"], "ui", "_work"); os.makedirs(work, exist_ok=True)
    jp = os.path.join(work, "review_job_%d.json" % os.getpid()); json.dump(job, open(jp, "w"), indent=1)   # per process: two agents queueing for the lock must not share a job file
    t = time.time()
    with exclusive_review():
        r = subprocess.run(["node", os.path.join(SKILL, "tools", "review", "shoot_ui.mjs"), "--job", jp], capture_output=True, text=True, timeout=900)
        rj = os.path.join(job["review_dir"], "ui_review.json")   # read while still holding the lock (shared file)
        rep = json.load(open(rj)) if os.path.exists(rj) else {"errors": ["no ui_review.json"], "plates": {}}
    print(r.stdout[-3000:])
    if r.returncode != 0: print(r.stderr[-3000:])
    for e in rep.get("errors", [])[:10]: print("PAGE ERROR:", str(e)[:300])
    sheets(job, rep)
    ok = r.returncode == 0 and not rep.get("errors")
    print("REVIEW %s %.0fs -> %s" % ("OK" if ok else "ISSUES", time.time() - t, os.path.relpath(job["review_dir"], root)))
    return ok


def sheets(job, rep):
    """Per-asset contact sheets from the plates (game-animation's compose, imported)."""
    if L.GANIM_SCRIPTS not in sys.path: sys.path.insert(0, L.GANIM_SCRIPTS)
    from anim_cli import compose
    rd = job["review_dir"]; plates = rep.get("plates", {})
    by = {}
    for name, info in plates.items():
        for aid in info.get("assets", []): by.setdefault(aid, []).append(os.path.join(rd, info["file"]))
    for aid, files in by.items():
        rows = [[f] for f in files if os.path.exists(f)]
        if rows: compose(os.path.join(rd, aid + ".png"), rows, width=1600, title="%s  (game-ui review: %d plates)" % (aid, len(rows)))
    if "mock_console" in plates:
        compose(os.path.join(rd, "ui_control_room.png"), [[os.path.join(rd, plates["mock_console"]["file"])]], width=1600, title="control room mock (tokens + icons + panels + templates)")


# --------------------------------------------------------------------------- status / main

def cmd_status(argv):
    spec, sp, root = L.project()
    mp = os.path.join(root, spec["paths"]["manifest"]); man = {e["id"]: e for e in (json.load(open(mp)) if os.path.exists(mp) else [])}
    for p in sort_briefs(L.all_briefs(root, spec)):
        P = L.paths_for(p); v = os.path.join(P["work"], "validate.json")
        vr = json.load(open(v))["result"] if os.path.exists(v) else "-"
        print("%-28s %-9s %-6s built=%-3s validate=%-5s manifest=%s" % (P["id"], P["kind"], P["brief"].get("status", ""), "yes" if os.path.exists(P["json"]) else "no", vr, man.get(P["id"], {}).get("status", "-")))


def main():
    if len(sys.argv) < 2: print(__doc__); sys.exit(2)
    c, rest = sys.argv[1], sys.argv[2:]
    if c == "claim": return cmd_claim(rest)
    if c == "new": return cmd_new(rest)
    if c == "status": return cmd_status(rest)
    if c == "review": sys.exit(0 if cmd_review(rest) else 1)
    ok = True
    for p in sort_briefs(rest):
        if c == "build": ok &= cmd_build(p)
        elif c == "validate": ok &= cmd_validate(p)
        elif c == "manifest": ok &= cmd_manifest(p)
        elif c == "all":
            s = cmd_build(p) and cmd_validate(p, quiet=True)
            if s: cmd_manifest(p)
            ok &= s
        else: print(__doc__); sys.exit(2)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
