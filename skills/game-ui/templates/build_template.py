"""__ID__ -- paper template (nine-slice + slots). game-ui build script: build(ctx) paints each density and returns nine_json(...)."""
from ui_panels import Canvas, nine_json

L, T, R, B = 16, 28, 16, 16          # header band (28) holds the printed rule; centre and edges are ctx.period long so they tile


def paint(ctx, d):
    c = ctx.color; P = ctx.period
    w, h = L + P + R, T + P + B
    k = Canvas(w, h, d, ctx.seed)
    k.grain(c["paper"], c["paper_grain"], 0.45, P, fibres=0.35)
    k.rule(0, 20, w, 20, c["ministry_ochre"], w=3, ink=0.85)
    return k


def build(ctx):
    imgs = {}
    for d in ctx.densities:
        paint(ctx, d).save(ctx.image_path(d)); imgs[str(d)] = ctx.image_name(d)
    w, h = L + ctx.period + R, T + ctx.period + B
    slots = []   # {"id", "kind", "rect", "anchor", "stretch", "style", "role", "label_key" | "data"}
    return nine_json(ctx.id, (w, h), {"l": L, "t": T, "r": R, "b": B}, "tile", "tile", imgs, slots=slots, fmt="ig_ui_layout")
