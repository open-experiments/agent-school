# Roadmap deck

The working-group kickoff deck (November 2026) on what is coming for Agent School: where the project stands, the skill / capability / role segregation, the new 4xx platform-layer series (401 Skills and capabilities, 402 Knowledge, 403 Domain model track with OTEL 2.0), the syllabus upgrade, the gated roadmap with candidate partner lanes, and the decisions the working group owns. The canonical text of the plan is [RoadMap.md](../RoadMap.md); this deck is its presentation.

## Files

- `agent-school-roadmap-wg-kickoff-2026-11.pdf`: vector PDF, one page per slide, fonts embedded. The copy to share.
- `agent-school-roadmap-wg-kickoff-2026-11.pptx`: 16:9 PowerPoint with each slide as a picture and the speaker notes attached. Edit the sources and rebuild rather than editing inside PowerPoint.
- `web/`: 1280x720 JPEG of each slide, what the slide viewer on the site's Roadmap page shows ([open-experiments.github.io/agent-school/#roadmap](https://open-experiments.github.io/agent-school/#roadmap)).
- `deck/`: the sources and the build. `deck.json` is the slide order and typefaces; `slides/<id>.html` is one slide each in the Claude Slides format (a 1920x1080 `<section>` with inline styles, `<x-shape>` / `<x-connector>` diagram elements, and an `<aside>` of speaker notes); `build.py` and `render.js` render the sources to PDF, PNG, PPTX and the web JPEGs.

## Regenerate

```bash
cd docs/roadmap/deck
npm i                                   # Red Hat Display / Text / Mono from Fontsource
pip install python-pptx playwright pillow && playwright install chromium   # once
python3 build.py . /tmp/deck-out agent-school-roadmap-wg-kickoff-2026-11
cp /tmp/deck-out/*.pdf /tmp/deck-out/*.pptx .. && cp /tmp/deck-out/web/*.jpg ../web/
```

`build.py` rewrites the custom elements as CSS and SVG, prints the page with Chromium (Playwright), screenshots each slide, packs the pictures plus notes into the PPTX with python-pptx, and resizes the screenshots to the web JPEGs. The site's `docs/tests/site.test.js` checks that the viewer, the PDF and the PPTX are served; run it after a rebuild. Typography follows the repo convention for Red Hat solutioning deliverables: Red Hat Display for headings, Red Hat Text for body, Red Hat Mono for labels.
