#!/usr/bin/env python3
"""Render the roadmap deck sources (deck.json + slides/*.html) to PDF, PNG and PPTX.

The slide sources are the Claude Slides format: one <section> per slide on a
1920x1080 canvas with inline styles, plus <x-shape> / <x-connector> custom
elements and an <aside> of speaker notes. This script turns them into a plain
HTML page (custom elements rewritten as CSS and SVG), prints it with Chromium
through Playwright (vector PDF with embedded fonts, one PNG per slide), and
packs the PNGs with the speaker notes into a 16:9 PPTX with python-pptx.

Usage (from this directory):
    npm i @fontsource/red-hat-display @fontsource/red-hat-text @fontsource/red-hat-mono
    pip install python-pptx playwright && playwright install chromium   # once
    python3 build.py <source-dir> <out-dir> [basename]

<source-dir> holds deck.json and slides/<id>.html.
"""
import html
import json
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

CSS = """
@page { size: 1920px 1080px; margin: 0; }
* { box-sizing: border-box; }
html, body { margin: 0; padding: 0; background: #ffffff; }
section { position: relative; width: 1920px; height: 1080px; overflow: hidden;
          page-break-after: always; break-after: page; }
section > * { position: relative; }
section > [style*="position:absolute"] { position: absolute; }
h1, h2, h3, p, ul, ol { margin: 0; }
h1 { font-size: 96px; font-weight: 600; line-height: 1.1; }
h2 { font-size: 64px; font-weight: 600; line-height: 1.15; }
h3 { font-size: 44px; font-weight: 600; line-height: 1.2; }
p  { font-size: 32px; font-weight: 400; line-height: 1.4; }
ul { list-style: disc; padding-left: 1.1em; }
ol { list-style: decimal; padding-left: 1.2em; }
li { padding-left: 0.1em; }
table { border-collapse: collapse; width: 100%; }
th, td { border: 1px solid #dfe2e6; padding: 0.35em 0.6em; vertical-align: top; text-align: left; }
th { font-weight: 700; }
aside { display: none; }
x-shape { display: block; flex: none; }
x-shape[kind="arrow-right"] { clip-path: polygon(0 30%, 60% 30%, 60% 0, 100% 50%, 60% 100%, 60% 70%, 0 70%); }
svg.xc { position: absolute; left: 0; top: 0; width: 100%; height: 100%; overflow: visible; pointer-events: none; }
"""

FONT_CSS = [
    "node_modules/@fontsource/red-hat-display/500.css",
    "node_modules/@fontsource/red-hat-display/700.css",
    "node_modules/@fontsource/red-hat-display/800.css",
    "node_modules/@fontsource/red-hat-text/400.css",
    "node_modules/@fontsource/red-hat-text/500.css",
    "node_modules/@fontsource/red-hat-text/700.css",
    "node_modules/@fontsource/red-hat-mono/400.css",
    "node_modules/@fontsource/red-hat-mono/500.css",
]

_marker_n = 0


def _style_prop(style: str, name: str, default: str) -> str:
    m = re.search(r"(?:^|;)\s*" + re.escape(name) + r"\s*:\s*([^;]+)", style)
    return m.group(1).strip() if m else default


def connector_to_svg(tag: str) -> str:
    """<x-connector x1 y1 x2 y2 head style> -> an absolutely positioned SVG line."""
    global _marker_n
    attrs = dict(re.findall(r'([a-z0-9-]+)="([^"]*)"', tag))
    x1, y1, x2, y2 = (float(attrs.get(k, "0")) for k in ("x1", "y1", "x2", "y2"))
    head = attrs.get("head", "end")
    style = attrs.get("style", "")
    color = _style_prop(style, "color", "#5a6069")
    width = _style_prop(style, "border-width", "2px").replace("px", "")
    dashed = "dashed" in _style_prop(style, "border-style", "")
    _marker_n += 1
    mid = "xc-head-%d" % _marker_n
    marker = ""
    marker_ref = ""
    if head != "none":
        marker = ('<defs><marker id="%s" markerWidth="4" markerHeight="4" refX="4" refY="2" '
                  'orient="auto" markerUnits="strokeWidth"><path d="M0 0 L4 2 L0 4 Z" fill="%s"/>'
                  '</marker></defs>' % (mid, color))
        marker_ref = ' marker-end="url(#%s)"' % mid
    dash = ' stroke-dasharray="12 10"' if dashed else ""
    return ('<svg class="xc" aria-hidden="true">%s<line x1="%g" y1="%g" x2="%g" y2="%g" '
            'stroke="%s" stroke-width="%s" stroke-linecap="round"%s%s/></svg>'
            % (marker, x1, y1, x2, y2, color, width, dash, marker_ref))


def render_sections(src: Path):
    deck = json.loads((src / "deck.json").read_text(encoding="utf-8"))
    sections, notes = [], []
    for sid in deck["order"]:
        text = (src / "slides" / (sid + ".html")).read_text(encoding="utf-8")
        text = re.sub(r"<x-connector\b[^>]*>\s*</x-connector>", lambda m: connector_to_svg(m.group(0)), text)
        aside = re.search(r"<aside>(.*?)</aside>", text, re.S)
        notes.append(html.unescape(aside.group(1)).strip() if aside else "")
        sections.append(text)
    return deck, sections, notes


def main():
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    src, out = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve()
    base = sys.argv[3] if len(sys.argv) > 3 else "deck"
    out.mkdir(parents=True, exist_ok=True)
    deck, sections, notes = render_sections(src)

    links = "\n".join('<link rel="stylesheet" href="%s">' % (HERE / p) for p in FONT_CSS)
    page = ("<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\"><title>%s</title>%s"
            "<style>%s</style></head><body>%s</body></html>"
            % (html.escape(deck["title"]), links, CSS, "\n".join(sections)))
    render_html = out / "render.html"
    render_html.write_text(page, encoding="utf-8")
    (out / "notes.json").write_text(json.dumps(notes, indent=1), encoding="utf-8")

    pdf = out / (base + ".pdf")
    subprocess.run(["node", str(HERE / "render.js"), str(render_html), str(pdf), str(out / "png")], check=True)

    from pptx import Presentation
    from pptx.util import Inches

    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)
    blank = prs.slide_layouts[6]
    for i, note in enumerate(notes, 1):
        slide = prs.slides.add_slide(blank)
        slide.shapes.add_picture(str(out / "png" / ("slide-%02d.png" % i)), 0, 0,
                                 width=prs.slide_width, height=prs.slide_height)
        if note:
            slide.notes_slide.notes_text_frame.text = note
    prs.core_properties.title = deck["title"]
    pptx_path = out / (base + ".pptx")
    prs.save(str(pptx_path))
    print("PDF ", pdf)
    print("PPTX", pptx_path)
    print("PNG ", out / "png", "(%d slides)" % len(notes))


if __name__ == "__main__":
    main()
