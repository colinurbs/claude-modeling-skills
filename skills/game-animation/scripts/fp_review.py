#!/usr/bin/env python3
"""
fp_review.py -- review renders for first-person sets and the hand-pose library (anim_cli.py review
dispatches here on animset "mode").

Per clip in review/<set>/<clip>/: eye_strip.png (the player's view at the view-model FOV, frame numbers,
events, 4:3 safe frame, screen centre), side.png (the view-model rig from outside), hands.png (grip
close-ups), world_fov.png (the same frame at the world FOV), timing.png (part curves, item height/pitch,
support-hand holds, events, keys), <clip>.mp4/.gif with --video; review/<set>/<clip>.png = the clip sheet,
review/<set>.png = the set sheet. Babylon's posed attach_hand_r / muzzle are probed against the build.
"""
import json, os, shutil, subprocess, sys, time
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__)); SKILL = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import animlib as al  # noqa: E402
import fplib as fl  # noqa: E402
import anim_build  # noqa: E402


def timing_chart(path, name, meta, ref, item, fps, W=1920, H=420):
    from PIL import Image, ImageDraw
    im = Image.new("RGB", (W, H), (27, 28, 28)); d = ImageDraw.Draw(im)
    fpm = meta["fp"]; F = meta["frames"]; x0, x1 = 150, W - 20
    X = lambda k: x0 + (x1 - x0) * k / max(1, F - 1)
    for k in range(F):
        if k % 5 == 0:
            d.line([X(k), 26, X(k), H - 24], fill=(44, 46, 45)); d.text((X(k) - 6, H - 20), str(k), fill=(140, 140, 130))
    for k in ref.get("keys", []):
        d.line([X(k), 26, X(k), H - 24], fill=(90, 92, 80), width=2)
    rows = []
    cols = {"slide": (230, 150, 60), "mag": (90, 170, 230), "trigger": (220, 90, 80), "hammer": (170, 210, 110)}
    parts = list(ref.get("parts", {}))
    rh = 40; y = 34
    for p in parts:
        v = np.array(ref["parts"][p]); vis = np.array(ref["visible"].get(p, [1] * F))
        d.text((8, y + 12), "%s%s" % (p, "" if p in fpm.get("parts", {}) else " (held)"), fill=cols.get(p, (200, 200, 200)))
        d.line([x0, y + rh, x1, y + rh], fill=(60, 62, 60))
        pts = [(X(k), y + rh - rh * float(np.clip(v[k], 0, 1))) for k in range(F)]
        d.line(pts, fill=cols.get(p, (200, 200, 200)), width=2)
        for k in range(F):
            if not vis[k]: d.rectangle([X(k) - 2, y + rh - 4, X(k) + 2, y + rh], fill=(120, 40, 40))
        for cw in fpm.get("carry", []):
            if cw["part"] == p: d.rectangle([X(cw["frames"][0]), y + 2, X(cw["frames"][1]), y + 6], fill=(90, 170, 230))
        y += rh + 8
    # item height and pitch (camera space)
    it = ref["item_w"]; E0 = np.array(ref["E0"])
    hgt = np.array([x["t"][1] - E0[1] for x in it]); fwd = np.array([al.qrot(np.array(x["r"]), np.array([0, 0, 1.0])) for x in it])
    pitch = np.degrees(np.arcsin(np.clip(fwd[:, 1], -1, 1)))
    for lab, v, col in (("item up (m)", hgt, (230, 226, 214)), ("item pitch (deg)", pitch, (200, 160, 220))):
        lo, hi = float(v.min()), float(v.max()); rng = max(1e-6, hi - lo)
        d.text((8, y + 12), lab, fill=col); d.text((8, y + 26), "%.2f..%.2f" % (lo, hi), fill=(140, 140, 130))
        d.line([(X(k), y + rh - rh * (v[k] - lo) / rng) for k in range(F)], fill=col, width=2)
        y += rh + 8
    d.text((8, y + 2), "support hand", fill=(224, 160, 48))
    for k in range(F):
        s = ref["space_l"][k]
        c = (224, 160, 48) if ref["hold_l"][k] else (90, 90, 80) if s in ("camera",) else (50, 50, 46)
        d.rectangle([X(k) - (x1 - x0) / F / 2, y + 4, X(k) + (x1 - x0) / F / 2, y + 14], fill=c)
    y += 24
    for e in fpm.get("events", []):
        k = e["frame"]; d.line([X(k), 26, X(k), H - 24], fill=(255, 176, 64), width=1)
        d.text((X(k) + 3, y + (hash(e["name"]) % 3) * 13), e["name"], fill=(255, 176, 64))
    d.text((x0, 6), "%s  %d frames @%d fps  timing: parts (0..1 of ig_parts travel; blue bar = carried by the support hand, red = hidden), item height/pitch, support hand (orange = holding the item), events (orange), keys (grey)" % (name, F, fps), fill=(230, 226, 214))
    im.save(path)


def review_multi(p, argv=()):
    """Hands sets (opt-in, dispatched from review): one item per clip (placed where the build put it while the right
    hand does not carry it), empty-hands clips, `--sleeve <char_fp_sleeve_id>` renders with that fp_sleeve layer worn
    (zone culling as Rig.refreshMask; review.sleeve_swap {clip: [from, to]} swaps at the sleeve_swap event) into
    review/<set>/_<sleeve>/, `--clips a,b` renders a subset."""
    import anim_cli, fp_build
    argv = list(argv)
    a = json.load(open(p)); P = anim_build.paths_for(p, a); spec = P["spec"]; an = spec["animation"]; fpc = an.get("first_person", {})
    video = "--video" in argv or a.get("review", {}).get("video", False)
    sleeve = argv[argv.index("--sleeve") + 1] if "--sleeve" in argv else None
    only = argv[argv.index("--clips") + 1].split(",") if "--clips" in argv else None
    skel = al.load_target(spec, P["root"])
    meta = {m["clip"]: m for m in json.load(open(P["meta"]))}
    if only: meta = {k: v for k, v in meta.items() if k in only or any(k.endswith("_" + o) for o in only)}
    arms_id, is_arms = anim_cli.fp_arms(P)
    ref = json.load(open(os.path.join(P["work"], "fp_ref.json")))["clips"]
    items = {}
    for m in meta.values():
        iid = m["fp"].get("item")
        if iid and iid not in items:
            g = fp_build.item_path(P, iid); items[iid] = {"glb": g, "parts_def": fl.Item(g, iid).parts_def}
        for pr in m["fp"].get("props", []):   # secondary items (clip props)
            if pr["item"] not in items:
                g = fp_build.item_path(P, pr["item"]); items[pr["item"]] = {"glb": g, "parts_def": fl.Item(g, pr["item"]).parts_def}
    sw = a.get("review", {}).get("sleeve_swap", {})
    sleeves = {}
    if sleeve: sleeves[sleeve] = anim_cli.part_path(P, sleeve)
    for n in meta:
        for s in sw.get(n, []) if sleeve else []:
            if s and s not in sleeves: sleeves[s] = anim_cli.part_path(P, s)
    tag = "_" + sleeve.replace("char_fp_sleeve_", "") if sleeve else ""
    out = os.path.join(P["root"], spec["paths"]["review"], a["id"], tag) if sleeve else os.path.join(P["root"], spec["paths"]["review"], a["id"])
    os.makedirs(out, exist_ok=True)
    nstrip = fpc.get("review", {}).get("frames", 10)
    clips = []
    for n, m in meta.items():
        F = m["frames"]; last = F - 2 if m["loop"] else F - 1
        ev = sorted({e["frame"] for e in m.get("fp", {}).get("events", []) if e["frame"] <= last})
        base = [int(round(i * last / max(1, nstrip - 1))) for i in range(nstrip)]
        strip = sorted(set(base) | set(ev))
        while len(strip) > 16:
            drop = [f for f in strip if f not in ev and f not in (0, last)]
            if not drop: break
            strip.remove(drop[len(drop) // 2])
        labels = {}
        for k in range(F):
            evk = [e["name"] for e in m.get("fp", {}).get("events", []) if e["frame"] == k]
            labels[k] = {"lines": ["%s  f%d/%d  %.2fs%s" % (n, k, F - 1, k / m["fps"], ("  [" + sleeve.replace("char_fp_sleeve_", "") + "]") if sleeve else "")], "events": evk}
        R = ref[n]; keys = R.get("keys", [0])
        hold_frame = a.get("review", {}).get("hold_frame", {}).get(n, keys[len(keys) // 2])
        track = [{"h": R.get("holder", ["r"] * F)[k], "t": R["item_w"][k]["t"], "r": R["item_w"][k]["r"]} for k in range(F)] if m["fp"].get("item") else []
        for k, tr in enumerate(track):   # a hanging item (clip hang) swings off the hand: place it from the build's per-frame item
            if R.get("hang_T") and tr["h"] == "r": tr["h"] = "r_hang"
        sched = []
        if sleeve:
            sched = [[0, sleeve]]
            if n in sw:
                fr = next((e["frame"] for e in m["fp"]["events"] if e["name"] == "sleeve_swap"), None)
                sched = [[0, sw[n][0]]] + ([[fr, sw[n][1]]] if fr is not None else [])
            for k in range(F):   # label each frame with the sleeve actually worn
                cur = [sid for f0, sid in sched if k >= f0][-1]
                labels[k]["lines"][0] = labels[k]["lines"][0].split("  [")[0] + "  [" + cur.replace("char_fp_sleeve_", "") + "]"
        clips.append({"name": n, "frames": F, "loop": m["loop"], "strip": strip, "labels": labels, "hold_frame": min(hold_frame, F - 1),
                      "probe": sorted({0, F // 2, F - 1}), "item": m["fp"].get("item"), "grip_r": m["fp"].get("regrip_r") or m["fp"].get("grip_r"), "track": track, "sleeves": sched,
                      "props": [{"id": k_, "track": v_["T"], "parts": v_["parts"]} for k_, v_ in R.get("props", {}).items()]})
    job = {"spec": P["spec_path"], "out": out, "mode": "fp", "clips": clips, "parts": [anim_cli.part_path(P, arms_id)], "arms_asset": is_arms,
           "items": items, "sleeves": sleeves, "meta": meta,
           "eye": fl.eye_point(skel).tolist(), "fov": fpc.get("viewmodel_fov_deg", 54), "world_fov": fpc.get("world_fov_deg", 75), "near": fpc.get("near_m", 0.01),
           "size": fpc.get("review", {}).get("size", [960, 540]), "video": video, "fps": an.get("fps", 30),
           "bones": skel.names, "parents": {nn: (skel.names[skel.parent[i]] if skel.parent[i] >= 0 else None) for i, nn in enumerate(skel.names)},
           "arm_bones": fl.keyed_bones(skel)}
    jp = os.path.join(P["work"], "review_job%s.json" % tag); os.makedirs(P["work"], exist_ok=True); json.dump(job, open(jp, "w"))
    t = time.time()
    with anim_cli.ExclusiveReview():
        r = subprocess.run(["node", os.path.join(SKILL, "tools", "review", "shoot_fp.mjs"), P["glb"], "--job", jp], capture_output=True, text=True, timeout=3600)
    print(r.stdout[-1500:])
    if r.returncode != 0: print(r.stderr[-2000:])
    rj = json.load(open(os.path.join(out, "review.json"))) if os.path.exists(os.path.join(out, "review.json")) else {"errors": ["no review.json"], "clips": {}}
    for e in rj["errors"][:10]: print("ENGINE ERROR:", str(e)[:300])
    print("REVIEW body: %s%s%s" % (arms_id, "" if is_arms else " (FALLBACK body)", ("  sleeve " + sleeve) if sleeve else ""))
    worst = (0.0, "")
    for n, cr in rj.get("clips", {}).items():
        R = ref[n]
        for k, pr in cr.get("probe", {}).items():
            k = int(k)
            if "item" in pr:
                e = np.linalg.norm(np.array(pr["item"]["p"]) - np.array(R["item_w"][k]["t"]))
                if e > worst[0]: worst = (e, "%s f%d item" % (n, k))
            if "attach_hand_r" in pr and "att_r_target" in R:
                e = np.linalg.norm(np.array(pr["attach_hand_r"]["p"]) - np.array(R["att_r_target"][k]["t"]))
                if e > worst[0]: worst = (e, "%s f%d attach_hand_r vs its target" % (n, k))
    print("REVIEW probe (Babylon-posed item / attach_hand_r vs the build's targets): worst %.2f cm at %s" % (worst[0] * 100, worst[1]))
    sheets = []
    for c in clips:
        n = c["name"]; cd = os.path.join(out, n); os.makedirs(cd, exist_ok=True)
        m = meta[n]; R = dict(ref[n])
        if not m["fp"].get("item"): R["item_w"] = R["att_r_target"]   # empty hands: chart the right hand instead
        tc = os.path.join(cd, "timing.png"); timing_chart(tc, n, m, R, None, an.get("fps", 30))
        rows = [[os.path.join(cd, "eye_strip.png")], [os.path.join(cd, "hands.png"), os.path.join(cd, "side.png")], [tc]]
        sheets.append(anim_cli.compose(os.path.join(out, n + ".png"), rows, title="%s   %d frames  %.2f s  %s item %s  events %s   body %s%s" % (
            n, m["frames"], m["duration_s"], "loop " if m["loop"] else "", m["fp"].get("item") or "-", ", ".join("%s@%d" % (e["name"], e["frame"]) for e in m["fp"]["events"]) or "-", arms_id, (" + " + sleeve) if sleeve else "")))
        vd = os.path.join(cd, "video"); ff = shutil.which("ffmpeg")
        if video and os.path.isdir(vd) and ff:
            subprocess.run([ff, "-y", "-loglevel", "error", "-framerate", str(an.get("fps", 30)), "-i", os.path.join(vd, "f%04d.png"), "-vf", "scale=960:-2", "-pix_fmt", "yuv420p", "-c:v", "libx264", os.path.join(cd, n + ".mp4")])
            subprocess.run([ff, "-y", "-loglevel", "error", "-framerate", str(an.get("fps", 30)), "-i", os.path.join(vd, "f%04d.png"), "-vf", "fps=15,scale=480:-1:flags=lanczos,split[s0][s1];[s0]palettegen=max_colors=128[p];[s1][p]paletteuse", os.path.join(cd, n + ".gif")])
            shutil.rmtree(vd)
    first = [[os.path.join(out, c["name"], "eye_strip.png")] for c in clips]
    set_sheet = anim_cli.compose(os.path.join(P["root"], spec["paths"]["review"], a["id"] + tag + ".png"), first, title="%s  %d clips  (%s%s)  per-clip sheets: review/%s/%s<clip>.png" % (
        a["id"], len(clips), arms_id, (" + " + sleeve) if sleeve else "", a["id"], (tag + "/") if tag else ""))
    ok = r.returncode == 0 and not rj["errors"]
    print("REVIEW %s %.0fs -> %s + %d clip sheets" % ("OK" if ok else "ERRORS", time.time() - t, os.path.relpath(set_sheet, P["root"]), len(sheets)))
    return ok



def review(p, argv=()):
    import anim_cli
    a = json.load(open(p)); P = anim_build.paths_for(p, a); spec = P["spec"]; an = spec["animation"]; fpc = an.get("first_person", {})
    if a.get("mode") != "hand_poses" and (not a.get("item") or "--sleeve" in argv or any("item" in c or "release" in c or "regrip_r" in c for c in a.get("clips", []))):   # release: thrown items are tracked per frame (hidden once gone)
        return review_multi(p, argv)
    _fr = os.path.join(P["work"], "fp_ref.json")
    if a.get("mode") != "hand_poses" and os.path.exists(_fr) and any("holder" in r for r in json.load(open(_fr)).get("clips", {}).values()):
        return review_multi(p, argv)   # set-level item + free right-hand keys (strong-hand bolt / loading): track who holds the item per frame
    mode = "poses" if a.get("mode") == "hand_poses" else "fp"
    video = "--video" in argv or a.get("review", {}).get("video", False)
    skel = al.load_target(spec, P["root"])
    meta = {m["clip"]: m for m in json.load(open(P["meta"]))}
    arms_id, is_arms = anim_cli.fp_arms(P)
    ref = json.load(open(os.path.join(P["work"], "fp_ref.json")))["clips"] if mode == "fp" else {}
    item = None; item_glb = None
    if a.get("item"):
        import fp_build
        item_glb = fp_build.item_path(P, a["item"]); item = fl.Item(item_glb, a["item"])
    out = os.path.join(P["root"], spec["paths"]["review"], a["id"]); os.makedirs(out, exist_ok=True)
    nstrip = fpc.get("review", {}).get("frames", 10)
    clips = []
    for n, m in meta.items():
        F = m["frames"]; last = F - 2 if m["loop"] else F - 1
        ev = sorted({e["frame"] for e in m.get("fp", {}).get("events", []) if e["frame"] <= last})
        base = [int(round(i * last / max(1, nstrip - 1))) for i in range(nstrip)]
        strip = sorted(set(base) | set(ev))
        while len(strip) > 16:   # thin the evenly spaced frames, never the event frames
            drop = [f for f in strip if f not in ev and f not in (0, last)]
            if not drop: break
            strip.remove(drop[len(drop) // 2])
        labels = {}
        for k in range(F):
            ev = [e["name"] for e in m.get("fp", {}).get("events", []) if e["frame"] == k]
            labels[k] = {"lines": ["%s  f%d/%d  %.2fs" % (n, k, F - 1, k / m["fps"])], "events": ev}
        keys = ref.get(n, {}).get("keys", [0])
        hold_frame = keys[len(keys) // 2] if mode == "fp" else 0
        clips.append({"name": n, "frames": F, "loop": m["loop"], "strip": strip, "labels": labels, "hold_frame": min(hold_frame, F - 1),
                      "probe": sorted({0, F // 2, F - 1})})
    job = {"spec": P["spec_path"], "out": out, "mode": mode, "clips": clips, "parts": [anim_cli.part_path(P, arms_id)], "arms_asset": is_arms,
           "item": item_glb, "item_id": a.get("item"), "grip_r": (next(iter(meta.values()), {}).get("fp") or {}).get("grip_r"), "parts_def": item.parts_def if item else {}, "meta": meta,
           "eye": fl.eye_point(skel).tolist(), "fov": fpc.get("viewmodel_fov_deg", 54), "world_fov": fpc.get("world_fov_deg", 75), "near": fpc.get("near_m", 0.01),
           "size": fpc.get("review", {}).get("size", [960, 540]), "video": video, "fps": an.get("fps", 30),
           "bones": skel.names, "parents": {nn: (skel.names[skel.parent[i]] if skel.parent[i] >= 0 else None) for i, nn in enumerate(skel.names)},
           "arm_bones": fl.keyed_bones(skel)}
    jp = os.path.join(P["work"], "review_job.json"); os.makedirs(P["work"], exist_ok=True); json.dump(job, open(jp, "w"))
    t = time.time()
    with anim_cli.ExclusiveReview():
        r = subprocess.run(["node", os.path.join(SKILL, "tools", "review", "shoot_fp.mjs"), P["glb"], "--job", jp], capture_output=True, text=True, timeout=3600)
    print(r.stdout[-1200:])
    if r.returncode != 0: print(r.stderr[-2000:])
    rj = json.load(open(os.path.join(out, "review.json"))) if os.path.exists(os.path.join(out, "review.json")) else {"errors": ["no review.json"], "clips": {}}
    for e in rj["errors"][:10]: print("ENGINE ERROR:", str(e)[:300])
    print("REVIEW body: %s%s" % (arms_id, "" if is_arms else " (FALLBACK: char_fp_arms not delivered; m_avg body, arm triangles only, camera at the eye)"))
    sheets = []
    if mode == "fp":
        worst = (0.0, "")
        for n, cr in rj.get("clips", {}).items():
            R = ref[n]
            for k, pr in cr.get("probe", {}).items():
                k = int(k); w = np.array(R["item_w"][k]["t"])
                e = np.linalg.norm(np.array(pr["item"]["p"]) - w)
                if e > worst[0]: worst = (e, "%s f%d item" % (n, k))
        print("REVIEW probe (Babylon-posed item (attach_hand_r o grip_r) vs the build's item target): worst %.2f cm at %s" % (worst[0] * 100, worst[1]))
        for c in clips:
            n = c["name"]; cd = os.path.join(out, n); os.makedirs(cd, exist_ok=True)
            tc = os.path.join(cd, "timing.png"); timing_chart(tc, n, meta[n], ref[n], item, an.get("fps", 30))
            rows = [[os.path.join(cd, "eye_strip.png")], [os.path.join(cd, "hands.png"), os.path.join(cd, "side.png")], [tc]]
            m = meta[n]
            sheets.append(anim_cli.compose(os.path.join(out, n + ".png"), rows, title="%s   %d frames  %.2f s  %s policy %s  events %s   body %s" % (
                n, m["frames"], m["duration_s"], "loop " if m["loop"] else "", m["fp"]["frame_policy"], ", ".join("%s@%d" % (e["name"], e["frame"]) for e in m["fp"]["events"]) or "-", arms_id)))
            vd = os.path.join(cd, "video"); ff = shutil.which("ffmpeg")
            if video and os.path.isdir(vd) and ff:
                subprocess.run([ff, "-y", "-loglevel", "error", "-framerate", str(an.get("fps", 30)), "-i", os.path.join(vd, "f%04d.png"), "-vf", "scale=960:-2", "-pix_fmt", "yuv420p", "-c:v", "libx264", os.path.join(cd, n + ".mp4")])
                subprocess.run([ff, "-y", "-loglevel", "error", "-framerate", str(an.get("fps", 30)), "-i", os.path.join(vd, "f%04d.png"), "-vf", "fps=15,scale=480:-1:flags=lanczos,split[s0][s1];[s0]palettegen=max_colors=128[p];[s1][p]paletteuse", os.path.join(cd, n + ".gif")])
                shutil.rmtree(vd)
        first = [[os.path.join(out, c["name"], "eye_strip.png")] for c in clips]
    else:
        first = []
        names = [c["name"] for c in clips]
        for i in range(0, len(names), 2): first.append([os.path.join(out, x, "hands.png") for x in names[i:i + 2]])
    set_sheet = anim_cli.compose(os.path.join(P["root"], spec["paths"]["review"], a["id"] + ".png"), first, title="%s  %d clips  (%s)  per-clip sheets: review/%s/<clip>.png" % (a["id"], len(clips), arms_id, a["id"]))
    ok = r.returncode == 0 and not rj["errors"]
    print("REVIEW %s %.0fs -> %s + %d clip sheets" % ("OK" if ok else "ERRORS", time.time() - t, os.path.relpath(set_sheet, P["root"]), len(sheets)))
    return ok
