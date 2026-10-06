# Regenerate dark-mode overrides after editing style.css:
#   python3 tools/darkify.py app/static/style.css app/static/theme-dark.css
# Hand-tuned dark tokens and fixes live in static/theme.css.
import re, sys
src = open(sys.argv[1]).read()
src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
P = '[data-theme="dark"]'
RGBA = r"rgba?\(\s*\d+\s*,\s*\d+\s*,\s*\d+\s*(?:,\s*[.\d]+)?\s*\)"
HEX = r"#[0-9a-fA-F]{6}\b|#[0-9a-fA-F]{3}\b"
def prgba(t):
    m = re.match(r"rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*(?:,\s*([.\d]+))?\s*\)", t)
    return int(m[1]), int(m[2]), int(m[3]), float(m[4]) if m[4] else 1.0
def phex(h):
    h = h[1:]
    if len(h) == 3: h = "".join(c*2 for c in h)
    return int(h[0:2],16), int(h[2:4],16), int(h[4:6],16)
def lum(r,g,b): return (0.2126*r+0.7152*g+0.0722*b)/255
DARK_INVERT = {"#2a3242","#121822","#2b3445","#111722","#1a2230","#0e131b","#15171b","#23262b","#1f242c"}
ACCENT_TEXT = {"#1d5fb0":"#8fbcff","#9a5600":"#ffc98a","#c23a2e":"#ff9b90","#16865a":"#6fdcaa","#5a46c8":"#c9bfff","#174f96":"#9cc4ff",
  "#1d4fb8":"#a9c6ff","#6a4fd8":"#cdbcff","#b4561f":"#ffb27a","#3b5bdb":"#9db4ff","#0d6e48":"#6fdcaa","#b02f25":"#ff9b90",
  "#3b4350":"#c9ced6","#525b69":"#b3b9c3","#4f555e":"#b3b9c3","#2a2e34":"#d4d8de","#5a6069":"#a9afb8","#6b7280":"#a9afb8",
  "#e85c70":"#ff8597","#e28a18":"#ffb54d","#3480ec":"#7fb0ff","#16a88c":"#5fd6b9","#12a3c6":"#7fd8f0","#6f5ae6":"#b9a8ff"}
def is_ring(part): return re.match(r"\s*(inset\s+)?0\s+0\s+0\s", part) is not None

def conv_color(val, inverted):
    def rh(m):
        h = m.group(0).lower(); r,g,b = phex(h)
        if inverted and lum(r,g,b) > .9: return "#0e131b"
        if h in ACCENT_TEXT: return ACCENT_TEXT[h]
        if lum(r,g,b) < .25 and not inverted: return "#eef1f6"
        return m.group(0)
    def rr(m):
        r,g,b,a = prgba(m.group(0))
        if lum(r,g,b) < .3: return f"rgba(238, 241, 246, {a:g})"
        return m.group(0)
    return re.sub(RGBA, rr, re.sub(HEX, rh, val))

def conv_bg(val, inverted):
    def rh(m):
        h = m.group(0).lower(); r,g,b = phex(h); L = lum(r,g,b)
        if h in DARK_INVERT: return "#eef1f6" if L < .2 else "#dfe4ec"
        if L > .85: return f"rgba(255, 255, 255, {0.06 + (L - .85) * 0.9:.3f})"
        return m.group(0)
    def rr(m):
        r,g,b,a = prgba(m.group(0)); L = lum(r,g,b)
        if r > 240 and g > 240 and b > 240: return f"rgba(255, 255, 255, {0.03 + a * 0.09:.3f})"
        if L > .85: return f"rgba(32, 36, 44, {a:g})"
        if L < .35 and (r,g,b) != (0,0,0): return f"rgba(255, 255, 255, {min(.12, a * 1.3):.3f})"
        return m.group(0)
    return re.sub(RGBA, rr, re.sub(HEX, rh, val))

def conv_border(val):
    def rr(m):
        r,g,b,a = prgba(m.group(0)); L = lum(r,g,b)
        if L < .35: return f"rgba(255, 255, 255, {min(.2, a * 1.1):.3f})"
        if r > 240 and g > 240 and b > 240 and a >= .5: return "rgba(255, 255, 255, 0.12)"
        return m.group(0)
    val = re.sub(r"#fff\b|#ffffff\b", "rgba(255, 255, 255, 0.14)", val)
    return re.sub(RGBA, rr, val)

def conv_shadow(val):
    parts = re.split(r",(?![^(]*\))", val)
    out = []
    for p in parts:
        ring = is_ring(p)
        def rr(m):
            r,g,b,a = prgba(m.group(0)); L = lum(r,g,b)
            if L < .4: return f"rgba(255, 255, 255, {min(.16, a * 1.2):.3f})" if ring else f"rgba(0, 0, 0, {min(.5, a * 2.6):.3f})"
            if r > 240: return f"rgba(255, 255, 255, {a * .18:.3f})"
            return m.group(0)
        p2 = re.sub(r"#fff\b", "rgba(255, 255, 255, 0.1)", p)
        out.append(re.sub(RGBA, rr, p2))
    return ",".join(out)

def prefix(sel):
    out = []
    for s in re.split(r",(?![^(]*\))", sel):
        s = s.strip()
        if not s: continue
        if s.startswith(":root"): out.append(":root" + P + s[5:])
        elif s.startswith("html"): out.append("html" + P + s[4:])
        else: out.append(":root" + P + " " + s)
    return ", ".join(out)

def process(text):
    res = []; i = 0; n = len(text)
    while i < n:
        j = text.find("{", i)
        if j < 0: break
        head = text[i:j].strip()
        depth, k = 1, j + 1
        while depth and k < n:
            if text[k] == "{": depth += 1
            elif text[k] == "}": depth -= 1
            k += 1
        body = text[j+1:k-1]
        i = k
        if head.startswith("@media") or head.startswith("@supports"):
            inner = process(body)
            if inner.strip(): res.append(f"{head} {{\n{inner}}}\n")
            continue
        if head.startswith("@"): continue
        if head.startswith(":root") and "--" in body: continue          # tokens are written by hand
        inverted = bool(re.search(r"background[^;]*(" + "|".join(DARK_INVERT) + r"|var\(--ink\))", body, re.I))
        decls = []
        for d in body.split(";"):
            if ":" not in d: continue
            prop, val = d.split(":", 1); p = prop.strip().lower()
            if p.startswith("--"): continue
            if p in ("color", "fill", "stroke", "caret-color", "-webkit-text-fill-color"): nv = conv_color(val, inverted)
            elif p.startswith("background"):
                nv = conv_bg(val, inverted)
                if "var(--ink)" in val: nv = nv.replace("var(--ink)", "#eef1f6")
            elif p.startswith("border") or p.startswith("outline"): nv = conv_border(val)
            elif p in ("box-shadow", "text-shadow"): nv = conv_shadow(val)
            elif p == "filter" and "drop-shadow" in val: nv = conv_shadow(val)
            else: continue
            if inverted and p == "color" and re.search(r"#fff\b|#ffffff\b", val): nv = re.sub(r"#fff\b|#ffffff\b", "#0e131b", val)
            if inverted and p == "fill" and re.search(r"#fff\b", val): nv = re.sub(r"#fff\b", "#0e131b", val)
            if nv != val: decls.append(f"{prop.strip()}:{nv}")
        if decls: res.append(f"{prefix(head)} {{ {'; '.join(d.strip() for d in decls)}; }}\n")
    return "".join(res)

open(sys.argv[2], "w").write("/* GENERATED by tools/darkify.py from style.css: dark-mode overrides (do not edit by hand; regenerate). */\n" + process(src))
