#!/usr/bin/env python3
"""
scripts/generate_heavy_pages.py
================================
Generates 15 self-contained HTML pages each ≥200 KB for use as
resource-heavy web-server targets.  Pages embed all assets inline
(no external requests) so every byte is served by the web server.

Output: webserver/pages/page_heavy_{category}_{n}.html

Run:
    python scripts/generate_heavy_pages.py
"""

import hashlib
import math
import os
import random
import sys

OUTDIR = os.path.join(os.path.dirname(__file__), "..", "webserver", "pages")
TARGET_BYTES = 210_000   # comfortable margin above 200 KB
SEED = 42


def _lcg(state: int) -> int:
    """Tiny deterministic pseudo-random integer."""
    return (1664525 * state + 1013904223) & 0xFFFFFFFF


def make_large_svg(width: int, height: int, n_paths: int, seed: int) -> str:
    """Generate a deterministic SVG with many path elements (~30–60 KB)."""
    rng = seed
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
             f'viewBox="0 0 {width} {height}">']
    for _ in range(n_paths):
        rng = _lcg(rng)
        x1, y1 = rng % width, (_lcg(rng) % height)
        rng = _lcg(rng)
        x2, y2 = rng % width, (_lcg(rng) % height)
        rng = _lcg(rng)
        cx, cy = rng % width, (_lcg(rng) % height)
        rng = _lcg(rng)
        r = (rng % 64) + 8
        rng = _lcg(rng)
        hue = rng % 360
        rng = _lcg(rng)
        sat = 40 + rng % 60
        rng = _lcg(rng)
        lit = 30 + rng % 50
        rng = _lcg(rng)
        stroke_w = 1 + rng % 4
        color = f"hsl({hue},{sat}%,{lit}%)"
        rng = _lcg(rng)
        shape = rng % 3
        if shape == 0:
            parts.append(
                f'<path d="M{x1},{y1} Q{cx},{cy} {x2},{y2}" '
                f'stroke="{color}" stroke-width="{stroke_w}" fill="none"/>'
            )
        elif shape == 1:
            parts.append(
                f'<circle cx="{cx}" cy="{cy}" r="{r}" '
                f'fill="{color}" opacity="0.7"/>'
            )
        else:
            parts.append(
                f'<rect x="{x1}" y="{y1}" width="{r*2}" height="{r}" '
                f'fill="{color}" opacity="0.6"/>'
            )
    parts.append("</svg>")
    return "\n".join(parts)


def make_css_block(n_rules: int, seed: int) -> str:
    """Generate a large CSS stylesheet (~25–40 KB)."""
    rng = seed
    lines = ["<style>"]
    props = [
        "color", "background-color", "border-color", "outline-color",
        "box-shadow", "text-shadow", "font-size", "line-height",
        "margin", "padding", "border-radius", "opacity",
    ]
    for i in range(n_rules):
        rng = _lcg(rng)
        selector = f".c{i:04x}"
        rng = _lcg(rng)
        n_props = 4 + rng % 5
        decls = []
        for _ in range(n_props):
            rng = _lcg(rng)
            prop = props[rng % len(props)]
            rng = _lcg(rng)
            if "color" in prop:
                val = f"#{rng & 0xFFFFFF:06x}"
            elif prop in ("font-size", "line-height"):
                val = f"{10 + rng % 24}px"
            elif prop in ("margin", "padding"):
                val = f"{rng % 32}px {rng % 32}px"
            elif prop == "border-radius":
                val = f"{rng % 20}px"
            elif prop == "opacity":
                val = f"{0.3 + (rng % 70) / 100:.2f}"
            else:
                val = f"0 {1 + rng % 4}px {2 + rng % 8}px #{rng & 0xFFFFFF:06x}"
            decls.append(f"  {prop}: {val};")
        lines.append(f"{selector} {{")
        lines.extend(decls)
        lines.append("}")
    lines.append("</style>")
    return "\n".join(lines)


def make_js_data_block(n_records: int, seed: int) -> str:
    """Generate a <script> block containing a large JSON data array (~80–120 KB)."""
    rng = seed
    lines = ["<script>", "const DATA = ["]
    words = [
        "alpha", "beta", "gamma", "delta", "epsilon", "zeta", "eta",
        "theta", "iota", "kappa", "lambda", "mu", "nu", "xi",
        "omicron", "pi", "rho", "sigma", "tau", "upsilon",
    ]
    for i in range(n_records):
        rng = _lcg(rng)
        w1 = words[rng % len(words)]
        rng = _lcg(rng)
        w2 = words[rng % len(words)]
        rng = _lcg(rng)
        val_a = rng % 100_000
        rng = _lcg(rng)
        val_b = (rng & 0xFFFF) / 100.0
        rng = _lcg(rng)
        val_c = rng % 360
        rng = _lcg(rng)
        val_d = bool(rng % 2)
        comma = "," if i < n_records - 1 else ""
        lines.append(
            f'  {{"id":{i},"tag":"{w1}-{w2}","score":{val_a},'
            f'"ratio":{val_b:.2f},"angle":{val_c},"active":{str(val_d).lower()}}}{comma}'
        )
    lines.append("];")
    lines.append("</script>")
    return "\n".join(lines)


def make_table(n_rows: int, seed: int) -> str:
    """Generate an HTML table with n_rows rows of deterministic data."""
    rng = seed
    cols = ["ID", "Name", "Value", "Score", "Status", "Timestamp"]
    rows = ["<table border='1' cellpadding='4' cellspacing='0' style='border-collapse:collapse;font-size:12px'>"]
    rows.append("<thead><tr>" + "".join(f"<th>{c}</th>" for c in cols) + "</tr></thead>")
    rows.append("<tbody>")
    for i in range(n_rows):
        rng = _lcg(rng)
        name = f"item-{rng % 9999:04d}"
        rng = _lcg(rng)
        value = rng % 100000
        rng = _lcg(rng)
        score = (rng & 0xFFFF) / 655.35
        rng = _lcg(rng)
        status = ["active", "pending", "archived", "draft"][rng % 4]
        ts = f"2025-{1 + i % 12:02d}-{1 + i % 28:02d} {i % 24:02d}:{i % 60:02d}"
        rows.append(
            f"<tr><td>{i}</td><td>{name}</td><td>{value}</td>"
            f"<td>{score:.1f}</td><td>{status}</td><td>{ts}</td></tr>"
        )
    rows.append("</tbody></table>")
    return "\n".join(rows)


CATEGORY_SPECS = {
    "gallery": {
        "title": "Image Gallery",
        "description": "A resource-heavy gallery page with embedded SVG artwork.",
        "n_svgs": 8, "svg_paths": 200,
        "n_css_rules": 600, "n_js_records": 600, "n_table_rows": 200,
    },
    "dashboard": {
        "title": "Analytics Dashboard",
        "description": "Performance analytics dashboard with large data tables and charts.",
        "n_svgs": 4, "svg_paths": 150,
        "n_css_rules": 700, "n_js_records": 800, "n_table_rows": 300,
    },
    "docs": {
        "title": "Technical Documentation",
        "description": "Detailed technical documentation with embedded diagrams and data.",
        "n_svgs": 5, "svg_paths": 180,
        "n_css_rules": 650, "n_js_records": 700, "n_table_rows": 250,
    },
}


def make_page(category: str, index: int) -> str:
    spec = CATEGORY_SPECS[category]
    seed = SEED + index * 997 + sum(ord(c) for c in category) * 31

    title = f"{spec['title']} {index}"
    desc  = spec["description"]

    css_block = make_css_block(spec["n_css_rules"], seed)
    js_block  = make_js_data_block(spec["n_js_records"], seed + 1)

    svgs = []
    for k in range(spec["n_svgs"]):
        w = 400 + (k * 37) % 400
        h = 200 + (k * 53) % 200
        svg = make_large_svg(w, h, spec["svg_paths"], seed + k + 2)
        svgs.append(f'<div class="svg-frame">{svg}</div>')

    table = make_table(spec["n_table_rows"], seed + 100)

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title}</title>
{css_block}
</head>
<body>
<header style="background:#1a1a2e;color:#eee;padding:20px 40px">
  <h1>{title}</h1>
  <p>{desc}</p>
</header>
<main style="padding:20px 40px">
  <section class="svg-gallery" style="display:flex;flex-wrap:wrap;gap:16px;margin-bottom:32px">
    {"".join(svgs)}
  </section>
  <section class="data-table" style="overflow-x:auto;margin-bottom:32px">
    <h2>Dataset Overview</h2>
    {table}
  </section>
</main>
{js_block}
</body>
</html>"""
    return html


def main():
    os.makedirs(OUTDIR, exist_ok=True)
    generated = []
    for cat in ["gallery", "dashboard", "docs"]:
        for idx in range(1, 6):
            fname = f"page_heavy_{cat}_{idx}.html"
            fpath = os.path.join(OUTDIR, fname)
            content = make_page(cat, idx)
            with open(fpath, "w", encoding="utf-8") as f:
                f.write(content)
            size_kb = os.path.getsize(fpath) / 1024
            status = "OK" if size_kb >= 200 else "SMALL"
            print(f"  [{status}] {fname}  {size_kb:.1f} KB")
            if size_kb < 200:
                print(f"         WARNING: below 200 KB threshold", file=sys.stderr)
            generated.append(fname)
    print(f"\nGenerated {len(generated)} pages in {OUTDIR}/")
    return generated


if __name__ == "__main__":
    main()
