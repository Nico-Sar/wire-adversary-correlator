#!/usr/bin/env python3
"""
scripts/generate_multi_resource_pages.py
=========================================
Generates a multi-resource web site for traffic analysis research.

Each HTML page references separate external asset files so a browser visit
produces 35-50 distinct HTTP requests — mimicking real-site traffic fingerprints
rather than a single monolithic download.

Output structure:
  webserver/
    pages/          <- HTML entry points (filenames match config/urls.txt 101-115)
    assets/
      css/          <- 12 shared CSS files  (7-20 KB each)
      js/           <- 18 shared JS files   (9-30 KB each)
      img/          <- 25 SVG image files   (5-16 KB each)
      api/          <- 8  static JSON files (5-12 KB each)

HTML files reference assets via absolute paths (/assets/...) so they work
when rsynced directly to the web server root:
    rsync -av webserver/pages/  root@<server>:/var/www/html/
    rsync -av webserver/assets/ root@<server>:/var/www/html/assets/

Usage:
    python3 scripts/generate_multi_resource_pages.py [--out-dir webserver]
"""

import argparse
import json
from pathlib import Path

# ── Deterministic LCG pseudo-random (no stdlib random dependency) ──────────────
def _lcg(seed: int, n: int) -> list[int]:
    a, c, m = 1664525, 1013904223, 2**32
    s = seed & 0xFFFFFFFF
    out = []
    for _ in range(n):
        s = (a * s + c) & (m - 1)
        out.append(s)
    return out

def _flt(v: int) -> float:
    return v / (2**32)

def _hex(v: int, n: int = 6) -> str:
    return f"{v & ((1 << (n * 4)) - 1):0{n}x}"

def _pick(v: int, pool: list):
    return pool[v % len(pool)]

def _subset(pool: list, count: int, seed: int) -> list:
    """Deterministic Fisher-Yates subset of `count` items from pool."""
    idx = list(range(len(pool)))
    vs = _lcg(seed, len(pool) * 2)
    for i in range(len(idx) - 1, 0, -1):
        j = vs[i] % (i + 1)
        idx[i], idx[j] = idx[j], idx[i]
    return [pool[k] for k in idx[:count]]


# ── Asset pools ────────────────────────────────────────────────────────────────
CSS_POOL = [
    ("main",       12_000),
    ("reset",       8_000),
    ("theme",      15_000),
    ("grid",       10_000),
    ("typography",  9_000),
    ("components", 18_000),
    ("animations", 11_000),
    ("responsive", 13_000),
    ("dark-mode",   8_500),
    ("icons",       7_000),
    ("forms",      14_000),
    ("vendor",     20_000),
]

JS_POOL = [
    ("app",           25_000),
    ("analytics",     18_000),
    ("utils",         15_000),
    ("router",        12_000),
    ("components",    20_000),
    ("api-client",    14_000),
    ("charts",        22_000),
    ("search",        13_000),
    ("auth",          11_000),
    ("notifications",  9_000),
    ("carousel",      16_000),
    ("modal",         10_000),
    ("lazy-load",     12_000),
    ("polyfills",     18_000),
    ("vendor",        28_000),
    ("ui",            14_000),
    ("events",        11_000),
    ("worker",        15_000),
]

IMG_POOL = [
    ("hero-1", 14_000), ("hero-2", 12_000), ("hero-3", 16_000),
    ("hero-4", 13_000), ("hero-5", 15_000),
    ("thumb-01", 7_000), ("thumb-02", 8_000), ("thumb-03", 7_500),
    ("thumb-04", 9_000), ("thumb-05", 8_500), ("thumb-06", 7_000),
    ("thumb-07", 8_000), ("thumb-08", 9_500), ("thumb-09", 7_000),
    ("thumb-10", 8_000),
    ("banner-01", 11_000), ("banner-02", 12_000), ("banner-03", 10_000),
    ("banner-04", 13_000), ("banner-05", 11_500),
    ("avatar-01", 5_000), ("avatar-02", 5_500), ("avatar-03", 4_800),
    ("avatar-04", 5_200), ("avatar-05", 5_000),
]

JSON_POOL = [
    ("feed",        10_000),
    ("users",        8_000),
    ("products",    12_000),
    ("stats",        6_000),
    ("comments",     9_000),
    ("sidebar",      5_000),
    ("trending",     7_000),
    ("config",       4_500),
]

# ── Per-category subset counts ─────────────────────────────────────────────────
CATEGORIES = {
    "gallery":   {"css": 9,  "js": 14, "img": 20, "json": 5, "label": "Photo Gallery"},
    "dashboard": {"css": 8,  "js": 13, "img": 16, "json": 5, "label": "Analytics Dashboard"},
    "docs":      {"css": 7,  "js": 11, "img": 12, "json": 4, "label": "Documentation"},
}

PAGES = [
    ("page_heavy_gallery_1",   "gallery",   1),
    ("page_heavy_gallery_2",   "gallery",   2),
    ("page_heavy_gallery_3",   "gallery",   3),
    ("page_heavy_gallery_4",   "gallery",   4),
    ("page_heavy_gallery_5",   "gallery",   5),
    ("page_heavy_dashboard_1", "dashboard", 1),
    ("page_heavy_dashboard_2", "dashboard", 2),
    ("page_heavy_dashboard_3", "dashboard", 3),
    ("page_heavy_dashboard_4", "dashboard", 4),
    ("page_heavy_dashboard_5", "dashboard", 5),
    ("page_heavy_docs_1",      "docs",      1),
    ("page_heavy_docs_2",      "docs",      2),
    ("page_heavy_docs_3",      "docs",      3),
    ("page_heavy_docs_4",      "docs",      4),
    ("page_heavy_docs_5",      "docs",      5),
]


# ── CSS generator ──────────────────────────────────────────────────────────────
def make_css(name: str, target: int, seed: int) -> str:
    vs = _lcg(seed, 8000)
    vi = 0
    props = ["color", "background-color", "font-size", "margin", "padding",
             "border-radius", "opacity", "width", "height", "transform",
             "transition", "letter-spacing", "line-height", "gap", "box-shadow",
             "display", "flex-direction", "grid-template-columns", "overflow", "z-index"]
    units = ["px", "em", "rem", "%", "vh", "vw"]
    displays = ["flex", "grid", "block", "inline-flex", "inline-block"]

    out = [f"/* {name}.css */\n"]

    while sum(len(x) for x in out) < target:
        t = vs[vi % 8000] % 4; vi += 1
        n = vs[vi % 8000];     vi += 1
        if t == 0:
            sel = f".c-{_hex(n, 4)}"
        elif t == 1:
            sel = f"#id-{_hex(n, 4)}"
        elif t == 2:
            tags = ["div", "span", "p", "a", "button", "input", "section", "article", "li"]
            sel = f"{_pick(n, tags)}.v{_hex(n, 3)}"
        else:
            sel = f"[data-v='{_hex(n, 4)}']"

        out.append(f"{sel} {{\n")
        np_ = 2 + vs[vi % 8000] % 4; vi += 1
        for _ in range(np_):
            p  = _pick(vs[vi % 8000], props);   vi += 1
            vv = vs[vi % 8000];                  vi += 1
            if p in ("color", "background-color", "box-shadow"):
                val = f"#{_hex(vv)}"
                if p == "box-shadow":
                    val = f"0 {vv % 8}px {vv % 16}px #{_hex(vv)}"
            elif p == "opacity":
                val = f"{_flt(vv):.3f}"
            elif p == "transform":
                val = f"rotate({vv % 360}deg) scale({0.5 + _flt(vv) * 1.5:.2f})"
            elif p == "transition":
                val = f"all {_flt(vv) * 0.6:.2f}s ease-in-out"
            elif p == "display":
                val = _pick(vv, displays)
            elif p == "flex-direction":
                val = _pick(vv, ["row", "column", "row-reverse", "column-reverse"])
            elif p == "grid-template-columns":
                val = f"repeat({2 + vv % 4}, 1fr)"
            elif p == "overflow":
                val = _pick(vv, ["hidden", "auto", "scroll", "visible"])
            else:
                val = f"{vv % 120}{_pick(vv, units)}"
            out.append(f"  {p}: {val};\n")
        out.append("}\n")

    return "".join(out)


# ── JS generator ───────────────────────────────────────────────────────────────
def make_js(name: str, target: int, seed: int) -> str:
    vs = _lcg(seed, 10000)
    vi = 0
    mod = name.upper().replace("-", "_")

    out = [
        f"/* {name}.js */\n",
        '"use strict";\n',
        f"(function(g){{ var M={{}};",
    ]

    while sum(len(x) for x in out) < target - 100:
        fn  = f"f{_hex(vs[vi % 10000], 5)}"; vi += 1
        na  = vs[vi % 10000] % 4;            vi += 1
        args = ",".join(f"a{i}" for i in range(na))
        out.append(f"\nfunction {fn}({args}){{")
        ns = 3 + vs[vi % 10000] % 5; vi += 1
        last = "0"
        for _ in range(ns):
            vn = f"v{_hex(vs[vi % 10000], 3)}"; vi += 1
            ev = vs[vi % 10000];                vi += 1
            et = ev % 5
            if et == 0:
                expr = str(ev % 100000)
            elif et == 1:
                expr = f'"{_hex(ev, 10)}"'
            elif et == 2:
                b = vs[vi % 10000] % 9999; vi += 1
                expr = f"{ev % 9999}+{b}"
            elif et == 3:
                nums = ",".join(str(vs[(vi + j) % 10000] % 255) for j in range(6))
                vi += 6
                expr = f"[{nums}]"
            else:
                expr = f"Math.floor(Math.random()*{ev % 1000})"
            out.append(f"\nvar {vn}={expr};")
            last = vn
        out.append(f"\nreturn {last};}}")
        out.append(f"\nM.{fn}={fn};")

    out.append(f"\ng.{name.replace('-','_')}=M;")
    out.append("\n})(window);\n")
    return "".join(out)


# ── SVG image generator ────────────────────────────────────────────────────────
def make_svg(name: str, target: int, seed: int) -> str:
    vs = _lcg(seed, 8000)
    vi = 0

    if "hero" in name or "banner" in name:
        w, h = 1200, 400
    elif "avatar" in name:
        w, h = 100, 100
    else:
        w, h = 320, 220

    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}"',
        f' width="{w}" height="{h}">\n',
        f'<defs>\n',
    ]

    for gi in range(4):
        gid = f"gr{gi}{_hex(vs[vi % 8000], 3)}"; vi += 1
        c1  = _hex(vs[vi % 8000]);                vi += 1
        c2  = _hex(vs[vi % 8000]);                vi += 1
        x2  = vs[vi % 8000] % 100;                vi += 1
        y2  = vs[vi % 8000] % 100;                vi += 1
        out.append(
            f'<linearGradient id="{gid}" x1="0%" y1="0%" x2="{x2}%" y2="{y2}%">'
            f'<stop offset="0%" stop-color="#{c1}"/>'
            f'<stop offset="100%" stop-color="#{c2}"/>'
            f'</linearGradient>\n'
        )

    out.append('</defs>\n')
    bg = _hex(vs[vi % 8000]); vi += 1
    out.append(f'<rect width="{w}" height="{h}" fill="#{bg}"/>\n')

    shapes = ["rect", "circle", "ellipse", "path", "polygon"]

    while sum(len(x) for x in out) < target - 20:
        st  = vs[vi % 8000] % len(shapes); vi += 1
        col = _hex(vs[vi % 8000]);          vi += 1
        op  = f"{0.1 + _flt(vs[vi % 8000]) * 0.85:.2f}"; vi += 1

        if st == 0:  # rect
            x  = vs[vi % 8000] % w;              vi += 1
            y  = vs[vi % 8000] % h;              vi += 1
            rw = 10 + vs[vi % 8000] % (w // 3); vi += 1
            rh = 10 + vs[vi % 8000] % (h // 3); vi += 1
            rx = vs[vi % 8000] % 12;             vi += 1
            out.append(f'<rect x="{x}" y="{y}" width="{rw}" height="{rh}" rx="{rx}" fill="#{col}" opacity="{op}"/>\n')

        elif st == 1:  # circle
            cx = vs[vi % 8000] % w; vi += 1
            cy = vs[vi % 8000] % h; vi += 1
            r  = 5 + vs[vi % 8000] % 70; vi += 1
            out.append(f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="#{col}" opacity="{op}"/>\n')

        elif st == 2:  # ellipse
            cx = vs[vi % 8000] % w;  vi += 1
            cy = vs[vi % 8000] % h;  vi += 1
            rx = 10 + vs[vi % 8000] % 90; vi += 1
            ry = 5  + vs[vi % 8000] % 55; vi += 1
            out.append(f'<ellipse cx="{cx}" cy="{cy}" rx="{rx}" ry="{ry}" fill="#{col}" opacity="{op}"/>\n')

        elif st == 3:  # path
            sx = vs[vi % 8000] % w; vi += 1
            sy = vs[vi % 8000] % h; vi += 1
            d  = [f"M{sx},{sy}"]
            for _ in range(3 + vs[vi % 8000] % 5):
                vi += 1
                cmd = vs[vi % 8000] % 3; vi += 1
                if cmd == 0:
                    x2 = vs[vi % 8000] % w; vi += 1
                    y2 = vs[vi % 8000] % h; vi += 1
                    d.append(f"L{x2},{y2}")
                elif cmd == 1:
                    cx = vs[vi % 8000] % w; vi += 1
                    cy = vs[vi % 8000] % h; vi += 1
                    x2 = vs[vi % 8000] % w; vi += 1
                    y2 = vs[vi % 8000] % h; vi += 1
                    d.append(f"Q{cx},{cy} {x2},{y2}")
                else:
                    c1x = vs[vi % 8000] % w; vi += 1
                    c1y = vs[vi % 8000] % h; vi += 1
                    c2x = vs[vi % 8000] % w; vi += 1
                    c2y = vs[vi % 8000] % h; vi += 1
                    x2  = vs[vi % 8000] % w; vi += 1
                    y2  = vs[vi % 8000] % h; vi += 1
                    d.append(f"C{c1x},{c1y} {c2x},{c2y} {x2},{y2}")
            d.append("Z")
            out.append(f'<path d="{" ".join(d)}" fill="#{col}" opacity="{op}" stroke="none"/>\n')

        else:  # polygon
            np_ = 5 + vs[vi % 8000] % 6; vi += 1
            pts = []
            for _ in range(np_):
                px = vs[vi % 8000] % w; vi += 1
                py = vs[vi % 8000] % h; vi += 1
                pts.append(f"{px},{py}")
            out.append(f'<polygon points="{" ".join(pts)}" fill="#{col}" opacity="{op}"/>\n')

    out.append('</svg>\n')
    return "".join(out)


# ── JSON API generator ─────────────────────────────────────────────────────────
def make_json(name: str, target: int, seed: int) -> str:
    vs = _lcg(seed, 6000)
    vi = 0
    words = [
        "alpha", "beta", "gamma", "delta", "epsilon", "zeta", "eta", "theta",
        "iota", "kappa", "lambda", "mu", "nu", "xi", "omicron", "pi", "rho",
        "sigma", "tau", "upsilon", "phi", "chi", "psi", "omega",
        "apex", "base", "core", "data", "edge", "flux", "grid", "hash",
    ]
    items = []
    sentinel = json.dumps({"data": items, "meta": {"count": 0, "version": "1.0"}})
    while len(sentinel) < target - 100:
        v = vs[vi % 6000]; vi += 1
        item = {
            "id":     v % 1_000_000,
            "slug":   f"{_pick(v, words)}-{vs[vi % 6000] % 9999:04d}",
            "title":  " ".join(_pick(vs[(vi + j) % 6000], words) for j in range(5)),
            "score":  round(_flt(vs[vi % 6000]) * 100, 2),
            "tags":   [_pick(vs[(vi + j) % 6000], words) for j in range(3)],
            "active": bool(vs[vi % 6000] % 2),
            "ts":     f"2025-{1 + vs[vi % 6000] % 12:02d}-{1 + vs[(vi+1) % 6000] % 28:02d}T{vs[(vi+2) % 6000] % 24:02d}:{vs[(vi+3) % 6000] % 60:02d}:00Z",
            "meta":   {"src": name, "rank": vs[(vi+4) % 6000] % 500},
        }
        vi += 6
        items.append(item)
        sentinel = json.dumps({"data": items, "meta": {"count": len(items), "version": "1.0"}})
    payload = {"data": items, "meta": {"count": len(items), "version": "1.0", "source": name}}
    return json.dumps(payload, indent=2)


# ── HTML page generator ────────────────────────────────────────────────────────
def make_html(name: str, category: str, idx: int) -> tuple[str, dict]:
    spec = CATEGORIES[category]
    seed = hash(name) & 0xFFFFFFFF

    css_sel  = _subset([n for n, _ in CSS_POOL],  spec["css"],  seed ^ 0x1111_1111)
    js_sel   = _subset([n for n, _ in JS_POOL],   spec["js"],   seed ^ 0x2222_2222)
    img_sel  = _subset([n for n, _ in IMG_POOL],  spec["img"],  seed ^ 0x3333_3333)
    json_sel = _subset([n for n, _ in JSON_POOL], spec["json"], seed ^ 0x4444_4444)

    prefix = "/assets"

    css_tags = "\n".join(
        f'  <link rel="stylesheet" href="{prefix}/css/{n}.css">' for n in css_sel
    )
    js_tags = "\n".join(
        f'  <script src="{prefix}/js/{n}.js" defer></script>' for n in js_sel
    )
    img_tags = "\n".join(
        f'      <img src="{prefix}/img/{n}.svg" alt="{n}" loading="lazy" width="320" height="220">'
        for n in img_sel
    )
    fetch_block = "\n".join(
        f"    fetch('{prefix}/api/{n}.json').then(r=>r.json()).then(d=>void d);"
        for n in json_sel
    )

    total = 1 + len(css_sel) + len(js_sel) + len(img_sel) + len(json_sel)

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{spec['label']} — {idx}</title>
{css_tags}
{js_tags}
</head>
<body>
  <header id="site-header">
    <nav>
      <a href="/">Home</a>
      <a href="/gallery">Gallery</a>
      <a href="/dashboard">Dashboard</a>
      <a href="/docs">Docs</a>
    </nav>
    <h1>{spec['label']} {idx}</h1>
  </header>
  <main>
    <section class="media-grid">
{img_tags}
    </section>
    <section class="content">
      <h2>{name}</h2>
      <p>
        {spec['css']} stylesheets &bull; {spec['js']} scripts &bull;
        {spec['img']} images &bull; {spec['json']} API calls &bull;
        {total} total requests
      </p>
      <div id="app-root" data-page="{name}"></div>
    </section>
  </main>
  <footer>
    <p>Traffic-analysis research page &mdash; {name}</p>
  </footer>
  <script>
  (function() {{
{fetch_block}
  }})();
  </script>
</body>
</html>
"""
    manifest = {
        "page":           name,
        "category":       category,
        "css":            len(css_sel),
        "js":             len(js_sel),
        "images":         len(img_sel),
        "json_api":       len(json_sel),
        "total_requests": total,
        "css_files":      css_sel,
        "js_files":       js_sel,
        "img_files":      img_sel,
        "json_files":     json_sel,
    }
    return html, manifest


# ── Main ───────────────────────────────────────────────────────────────────────
def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate multi-resource web site for traffic-analysis research"
    )
    parser.add_argument("--out-dir", default="webserver", help="Output root directory")
    args = parser.parse_args()

    root      = Path(args.out_dir)
    pages_dir = root / "pages"
    css_dir   = root / "assets" / "css"
    js_dir    = root / "assets" / "js"
    img_dir   = root / "assets" / "img"
    api_dir   = root / "assets" / "api"

    for d in (pages_dir, css_dir, js_dir, img_dir, api_dir):
        d.mkdir(parents=True, exist_ok=True)

    print("Generating CSS files…")
    for i, (name, target) in enumerate(CSS_POOL):
        p = css_dir / f"{name}.css"
        content = make_css(name, target, seed=0xC550_0000 + i)
        p.write_text(content)
        print(f"  {p}  ({len(content):>7,} B)")

    print("Generating JS files…")
    for i, (name, target) in enumerate(JS_POOL):
        p = js_dir / f"{name}.js"
        content = make_js(name, target, seed=0xF510_0000 + i)
        p.write_text(content)
        print(f"  {p}  ({len(content):>7,} B)")

    print("Generating SVG image files…")
    for i, (name, target) in enumerate(IMG_POOL):
        p = img_dir / f"{name}.svg"
        content = make_svg(name, target, seed=0xA600_0000 + i)
        p.write_text(content)
        print(f"  {p}  ({len(content):>7,} B)")

    print("Generating JSON API files…")
    for i, (name, target) in enumerate(JSON_POOL):
        p = api_dir / f"{name}.json"
        content = make_json(name, target, seed=0xB700_0000 + i)
        p.write_text(content)
        print(f"  {p}  ({len(content):>7,} B)")

    print("Generating HTML pages…")
    manifests = []
    for page_name, category, idx in PAGES:
        p = pages_dir / f"{page_name}.html"
        html, mf = make_html(page_name, category, idx)
        p.write_text(html)
        manifests.append(mf)
        print(f"  {p}  ({len(html):>5,} B)  →  {mf['total_requests']} requests")

    # Print manifest table
    print()
    print(f"{'Page':<36}{'CSS':>5}{'JS':>5}{'IMG':>6}{'JSON':>6}{'TOTAL':>7}")
    print("─" * 65)
    for m in manifests:
        print(f"{m['page']:<36}{m['css']:>5}{m['js']:>5}{m['images']:>6}{m['json_api']:>6}{m['total_requests']:>7}")

    mins = min(m["total_requests"] for m in manifests)
    maxs = max(m["total_requests"] for m in manifests)
    total_assets = len(CSS_POOL) + len(JS_POOL) + len(IMG_POOL) + len(JSON_POOL)
    print(f"\nRequest range per page: {mins}–{maxs}")
    print(f"Shared asset pool: {total_assets} files ({len(CSS_POOL)} CSS, {len(JS_POOL)} JS, {len(IMG_POOL)} SVG, {len(JSON_POOL)} JSON)")
    print()
    print("Rsync commands:")
    print(f"  rsync -av {args.out_dir}/pages/  root@<server>:/var/www/html/")
    print(f"  rsync -av {args.out_dir}/assets/ root@<server>:/var/www/html/assets/")


if __name__ == "__main__":
    main()
