# Context Hub — landing page

Pure static site. No build step, no JS frameworks, no animation libraries.

```
index.html   markup + inlined critical CSS (tokens, header, hero)
styles.css   below-the-fold layout + the 12s protocol animation
app.js       progressive enhancement only (scroll reveal, off-screen pause)
```

## Run it

```bash
make landing            # from the repo root
# or
cd apps/web && python3 -m http.server 8788
```

Then open http://localhost:8788.

## The protocol animation

The hero figure is a hand-rolled SVG scene on a single shared 12-second CSS
timeline (1 s = 8.333 % of every keyframe block, so the stages stay in
lockstep):

| window | stage |
|--------|-------|
| 0.0–1.5 s | **Capture** — transcript lines type into the desktop card |
| 1.7–2.4 s | **Redact** — secret spans get masked, "secrets redacted" check |
| 3.1–5.2 s | **Push** — envelope travels desktop → hub along a drawn accent path |
| 5.3–7.0 s | **Index** — S3 well flashes, index bars grow |
| 7.0–8.4 s | **Ask** — a teammate's question travels to the hub |
| 8.6–11.0 s | **Answer** — cited answer card composes, citation chip pops |
| 11.5–12 s | fade for a clean loop |

Techniques: `pathLength="1"` + `stroke-dashoffset` for line drawing,
`offset-path`/`offset-distance` for the traveling glyphs, staged opacity for
scene composition. `prefers-reduced-motion` swaps the loop for the fully
composed static scene. `app.js` pauses the loop while it is scrolled away.

## Design

Follows `docs/DESIGN.md`: warm paper palette, hairline borders, one orange
accent, no gradients or drop shadows, left-aligned editorial layout, Inter +
JetBrains Mono with system fallbacks. Total page weight (excluding web fonts,
which fall back to system fonts) is well under 200 KB.
