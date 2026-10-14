"""glyphs.py -- __ID__ (game-ui glyph DSL: 24 u grid, y down, stroke 2 u on integer coordinates).
Families: say how the kind of mark is told apart (frames, brackets, bare). See references/look.md."""


def frame(g):
    g.rect(3, 3, 18, 18)


def example_tool(g):            # replace: one function per glyph, named <family>_<name>
    g.line(5, 12, 19, 12)
    g.poly([(13, 6), (19, 12), (13, 18)])


GLYPHS = {n: f for n, f in globals().items() if callable(f) and n not in ("frame",)}
META = {}       # name -> {"canonical": True, "means": "..."} for imported marks exempt from grid rules
ALIASES = {}    # data-model name -> glyph, e.g. "incident:shots_fired": "inc_shots"
