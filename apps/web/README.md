# Freshet — landing page

A complete product landing page. Pure static site: plain HTML/CSS/JS, **no build
step**, no frameworks, no animation libraries. The only external resources are
Google Fonts (Inter + JetBrains Mono), each with a system fallback. Every icon
and diagram is inline SVG; the product UI is hand-built in CSS (no images or
screenshots).

```
index.html   all 12 sections, semantic landmarks, inline SVG icons + diagrams
styles.css   the full design system + the dark product-mock app shell
app.js       progressive enhancement only (nav state, marquee, scroll reveal,
             SVG flow draw-in, product-mock state tabs)
```

The page is fully readable and styled with JavaScript disabled; `app.js` only
adds motion and the mock state switcher.

## Run it

```bash
make landing                       # from the repo root
# or
cd apps/web && python3 -m http.server 8901
```

Then open http://localhost:8901.

## What's on the page

1. **Nav** — sticky, hairline border, gains a solid background after 80px scroll.
2. **Hero** — announcement bar, headline, CTAs, and a dark mini product window
   (traffic lights, rail, session list with one expanded session card).
3. **Trust strip** — CSS marquee of grayscale team wordmarks (40s, pause on hover).
4. **Problem frame** — "The gap" copy + 2×2 Signal cards (accent left-edge on hover).
5. **How it works** — three numbered steps on a sunken band.
6. **Product mock (centerpiece)** — a full fake macOS app window in pure HTML/CSS:
   sidebar, session list (tool chips + status badges), session detail (tabs,
   redaction, Push to Hub), and a hub panel (SVG knowledge graph, skills, PR links).
   Toggle tabs below swap the mock between **Sessions / Knowledge Graph / Skills Hub**.
7. **Feature grid** — four pillars with accent-circle SVG icons.
8. **Protocol flow** — inline-SVG data-flow diagram; arrows draw in on scroll.
9. **Privacy** — centered card, four trust badges, and a JetBrains Mono path snippet.
10. **Social proof** — testimonial cards + a beta metric strip.
11. **Final CTA** — download + request team access.
12. **Footer** — wordmark, link columns, fine print.

## Motion

All scroll-triggered reveals use `IntersectionObserver`, with a `getBoundingClientRect`
fallback on scroll/resize and a final safety-net timeout, so content is never
permanently hidden. The protocol-flow arrows draw via `stroke-dashoffset` (each
arrow's length measured with `getTotalLength`), staggered 200ms, followed by a
single node pulse. `prefers-reduced-motion: reduce` disables every animation and
shows the final composed state immediately (both in CSS and in `app.js`).

## Design

Follows `docs/DESIGN.md` and the landing-page brief: warm-paper palette, hairline
borders, one orange accent (`#F2541B`), no gradients or drop shadows, left-aligned
editorial layout. The **only** dark surfaces are the product UI mocks — that
light/dark contrast is the look. Inter + JetBrains Mono with system fallbacks.

Sections collapse to a single column at ≤768px; the product mock scales down and
allows horizontal scroll inside its window frame rather than breaking. Semantic
landmarks, `aria-label`s on every visual mock/diagram, and visible focus states
throughout.

Total page weight (excluding web fonts) is ~87 KB — well under the 250 KB budget.
