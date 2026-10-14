"""__ID__ -- nine-slice panel. game-ui build script: build(ctx) paints each density and returns nine_json(...)."""
from ui_panels import Canvas, nine_json

L, T, R, B = 12, 12, 12, 12          # slice margins (css px); the centre and edges are ctx.period long so they tile


def paint(ctx, d):
    c = ctx.color; P = ctx.period
    w, h = L + P + R, T + P + B
    k = Canvas(w, h, d, ctx.seed)
    k.grain(c["bezel"], c["bezel_dark"], 0.5, P)
    k.edge_light(0, 0, w, h, c["bezel_light"], 1, "tl")
    return k


def build(ctx):
    imgs = {}
    for d in ctx.densities:
        paint(ctx, d).save(ctx.image_path(d)); imgs[str(d)] = ctx.image_name(d)
    w, h = L + ctx.period + R, T + ctx.period + B
    slots = []   # {"id", "kind", "rect", "anchor", "stretch", "style", "role", "label_key" | "data"}
    return nine_json(ctx.id, (w, h), {"l": L, "t": T, "r": R, "b": B}, "tile", "tile", imgs, slots=slots)
