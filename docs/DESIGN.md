# Design System — Context Hub

The aesthetic target: the confident, editorial, slightly-warm look of a top-tier modern startup landing + product. Lots of whitespace, strong typographic hierarchy, one decisive accent color, crisp hairline borders, near-zero gradients/shadows-as-decoration. It should feel **fast, founder-grade, and trustworthy** — not "AI-generated dashboard."

## Palette (CSS variables, defined in `src/styles/index.css`)
```
--bg            #FBFAF8   warm paper white (app background)
--bg-elevated   #FFFFFF   cards / surfaces
--bg-sunken     #F4F2EE   wells, code blocks, hover
--ink           #1A1815   near-black primary text
--ink-soft      #56524B   secondary text
--ink-faint     #8E897F   tertiary / metadata
--border        #E7E3DB   hairline borders
--border-strong #D8D2C7
--accent        #F2541B   decisive orange (CTAs, active, links)
--accent-ink    #C8400F   accent text/hover
--accent-wash   #FCEDE6   accent background tint
--success       #2E7D52
--warn          #B8860B
--danger        #C2401E
```
Dark mode optional later; ship light first.

## Type
- **Sans**: `Inter` (system fallback). Tight tracking on headings (`-0.02em`).
- **Mono**: `JetBrains Mono` for code, session content, token counts.
- Scale: display 40/32, h1 28, h2 20, h3 16, body 15, small 13, micro 12.
- Headings: weight 600–700, color `--ink`. Body 400/450.

## Layout & spacing
- 8px spacing grid. Generous: section padding 32–64px.
- Max content width 1180px; reading columns ~720px.
- App shell: left sidebar (240px, `--bg`, hairline right border) + main scroll area.
- Cards: `--bg-elevated`, 1px `--border`, radius 10px, **no drop shadow** (use border + subtle hover bg). Radius 8–12 throughout; pills fully rounded.

## Components (build as `src/components/ui/*`)
`Button` (primary=accent solid, secondary=ink outline, ghost), `Card`, `Badge`/`Tag` (category + tool chips, color-coded), `Input`, `Textarea`, `Select`, `Toggle`, `Tabs`, `Modal`, `Toast`, `Spinner`, `EmptyState`, `Tooltip`, `Avatar`, `KeyValue`.

Tool chips: Claude Code = warm orange tint, Codex = slate, Kilo Code = teal. Category chips each get a fixed muted color.

## Motion
- 120–180ms ease-out on hover/press. Subtle. No bouncy springs.
- Page transitions: 80ms fade. Skeleton loaders, not spinners, for lists.

## Voice
Editorial and direct. Headlines like "Everything your team taught the AI, in one place." Empty states are helpful sentences, not "No data."

## Anti-patterns (avoid the AI-slop look)
- ❌ purple/blue gradients, glassmorphism, neon glow.
- ❌ heavy drop shadows, emoji as UI icons (use `lucide-react`).
- ❌ centered everything; embrace left-aligned editorial layout.
- ✅ hairline borders, restrained accent, real hierarchy, dense-but-airy tables.
