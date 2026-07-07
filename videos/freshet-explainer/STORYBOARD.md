---
format: 1920x1080
message: "Freshet turns the AI sessions your team already runs into a reviewed, cited company brain."
arc: concept-explainer with process
audience: engineering leaders and developers who live in AI coding assistants
music: focused minimal electronic, warm, understated, low-key optimistic
---

## Video direction

- **Palette (from frame.md, by role):** warm paper canvas, single dark warm ink, accent orange as scarce voltage (one emphasized element per frame, never a wash). Code/mono voice sits on the warm-navy code surface. No gradients beyond the pack's paper swell; hairline ink rules are the only borders.
- **Type (by role):** display serif for hero lines (sentence case), body sans for labels, mono for anything machine-flavored — file paths, tokens, session ids, badges. Type is the primary visual; heroes run near full-bleed in the top ~83% (caption band clear; centered heroes anchor at y ≈ 0.42 × height).
- **Motion grammar:** smooth long-tail settles (`power3` default) — no bounce, no overshoot. Every frame reveals sequentially on its spoken cue with most arrivals in the back ~50%; nothing front-loads. During holds: stillness, at most subtle jitter (`sine-wave-loop`, low amplitude) or live SVG internals. Entrances are `fromTo` on the paused timeline; no CSS keyframes, no repeat/yoyo, no randomness.
- **Consistent stage:** Frames 4–7 share one left-to-right pipeline rail (hairline path on paper) so the mechanism reads as one continuous machine; their seam is a repeated `push-slide LEFT`.
- **Rhythm / held frames:** Frame 10 (thesis) is the deliberate breather — near-total stillness. Frame 2 ends on a long dead-file hold. Everything else develops to the VO.
- **Negative list:** no purple-blue "AI" gradients, no bokeh, no floating particles as decoration, no lazy breathing, no back-half camera drift, no slideshow front-load, no screensaver float. No browser chrome or scrollbars — the only reconstructed UI is the review card (Frame 8) and the query bar (Frame 9), each a minimal hairline card, not a browser.

## Frame 1 — Hook

- scene: Bold type beats — "Your team's sharpest thinking" swaps context words in place: Claude Code / Codex / Kilo
- voiceover: "Your team's sharpest thinking happens inside AI sessions — Claude Code. Codex. Kilo."
- duration: 6.5s
- transition_in: cut
- status: animated
- src: compositions/frames/01-hook.html
- type: hook
- persuasion: Pain validation + anchoring on a familiar referent
- beat: recognition
- blueprint: kinetic-type-beats (Reproduce)
- focal: the mono token slot cycling the three tool names
- roles: two-line display headline = foreground subject · faint hairline grid + warm radial paper swell = background (dim) · mono token pill = supporting, becomes focal in Scene 3
- sfx: tick

narrativeRole: Opens the gap — the viewer already lives in these tools; the video names where their real thinking happens.
keyMessage: The richest record of how your team works is inside AI assistant sessions.

Scene 1 (0.0–2.2s): warm paper field with a faint hairline grid; "Your team's sharpest thinking" enters via per-word staggered reveal (`dynamic-content-sequencing`) on a long-tail settle — centered, upper-third, near full-bleed display serif.
Scene 2 (2.2–3.4s): "happens inside AI sessions —" lands beneath on its spoken cue (per-word reveal), smaller weight; the accent spike mark ✱ draws beside it (`svg-path-draw`).
Scene 3 (3.4–6.0s): a mono token pill under the headline runs an in-place token cycle (`discrete-text-sequence`) — Claude Code → Codex → Kilo — one hard-cut swap per spoken name; the final token holds with a keyword glow (`asr-keyword-glow`); then stillness.

## Frame 2 — The knowledge dies

- scene: Transcript lines glow, then collapse into a dim file tile on a lone laptop silhouette; three closers slam on beats
- voiceover: "Then it dies — a log file on one laptop. Invisible. Unsearchable. Forgotten."
- duration: 7s
- transition_in: crossfade
- status: animated
- src: compositions/frames/02-knowledge-dies.html
- type: pain_point
- persuasion: Concretization (knowledge → a dead file) + rule of three
- beat: tension + loss
- blueprint: kinetic-type-beats (Adapt)
- focal: the dead .jsonl file tile
- roles: transcript column → file tile = foreground subject · laptop hairline silhouette = supporting · dimmed paper field = background
- sfx: thud-soft, whoosh-soft

narrativeRole: Makes the cost visceral — decisions, fixes, and reasoning evaporate the moment the session ends.
keyMessage: Today that knowledge is trapped in local files nobody can search.

Adapt: keep the beat-slam signature (short phrases landing on percussive beats); the bare-canvas pain statements share the stage with a concretizing transform (transcript → dead file).
Scene 1 (0.0–1.8s): a centered column of four dim mono transcript lines glows faintly; "Then it dies —" slams in above it (`kinetic-beat-slam`), display serif, on the spoken beat.
Scene 2 (1.8–3.6s): as the VO says "a log file on one laptop," the transcript column collapses via scale-swap (`scale-swap-transition`) into a small dim mono file tile "session.jsonl" seated on a hairline laptop silhouette — layout shifts to asymmetric 60/40, tile right of center.
Scene 3 (3.6–7.0s): three words slam sequentially on their spoken beats — Invisible. Unsearchable. Forgotten. (`kinetic-beat-slam`, left column) — each landing dims the file tile one step further; long hold on the dead tile, subtle jitter only (`sine-wave-loop`, low amplitude).

## Frame 3 — Name the idea

- scene: Freshet wordmark assembles from flowing chunk-particles; a boundary ring draws around it — local first
- voiceover: "Freshet captures it. Everything runs on your machine — nothing leaves without your say-so."
- duration: 7s
- transition_in: crossfade
- status: animated
- src: compositions/frames/03-freshet-intro.html
- type: product_intro
- persuasion: Concept announcement + subtractive framing (what it does NOT do: phone home)
- beat: orientation + relief
- blueprint: logo-assemble-lockup (Adapt)
- focal: the Freshet wordmark inside its machine-boundary ring
- roles: wordmark = foreground subject · hairline boundary ring = supporting (becomes co-focal) · warm radial swell + faint grid = background
- sfx: whoosh-soft

narrativeRole: Introduces the protagonist and immediately defuses the privacy objection.
keyMessage: Freshet is local-first — capture happens on your machine.

Adapt: keep the assemble→lockup signature move; the assembling parts are mono glyph "chunk" tiles (the product's own raw material) instead of abstract logo fragments.
Scene 1 (0.0–2.0s): on "Freshet captures it," small mono glyph tiles scatter-assemble in 3D (`depth-scatter-assemble`) into the "Freshet" display wordmark, centered at y ≈ 0.42; smooth long-tail settle.
Scene 2 (2.0–4.5s): on "runs on your machine," a hairline circle draws itself around the wordmark (`svg-path-draw`) — the machine boundary; a small mono label "parses locally · ~/.claude · ~/.codex · kilo" reveals beneath on its cue (per-word reveal).
Scene 3 (4.5–7.0s): on "nothing leaves," one stray tile glides toward the ring's edge and stops at the boundary (deterministic path), a tiny accent tick marking the block; "nothing leaves without your say-so" mono label reveals; ambient glow blooms softly behind the lockup (`ambient-glow-bloom`); hold still.

## Frame 4 — Pipeline: parse + redact

- scene: Pipeline stage 1 on a shared left-to-right rail — session folders feed a parser node; a redaction pass strikes secrets out in accent orange
- voiceover: "It parses every session locally — and scrubs secrets before anything else."
- duration: 6s
- transition_in: push-slide LEFT
- status: animated
- src: compositions/frames/04-parse-redact.html
- type: feature_showcase
- persuasion: Causal chain (A → B) + signposting (step one of the pipeline)
- beat: comprehension
- blueprint: grid-card-assemble (Adapt)
- focal: the parser/redaction node card
- roles: parser node card = foreground subject · three source folder cards + rail = supporting · paper field with faint grid = background
- sfx: tick, scribble

narrativeRole: First move of the mechanism — raw files become clean, safe transcripts.
keyMessage: Parsing and secret-redaction happen locally, before anything else.

Adapt: keep the staggered-cascade assembly signature; the grid is a pipeline stage — three source cards cascading onto a shared rail, feeding one node.
Scene 1 (0.0–1.5s): a hairline rail draws left→right across the frame (`svg-path-draw`); three mono source cards — claude / codex / kilo — cascade in at the left end (`grid-card-assemble` stagger), full-width strip layout, content in the top ~83%.
Scene 2 (1.5–3.5s): on "parses every session locally," small line-tokens stream from the cards along the rail into a parser node card right-of-center; gentle zoom-to-target on the node (`coordinate-target-zoom`) as it pulses once on arrival.
Scene 3 (3.5–6.0s): on "scrubs secrets," inside the node card a mono line `API_KEY=sk-live-…` is struck through by an accent marker sweep (`css-marker-patterns` highlight) and re-renders as `•••`; a small "redacted locally" badge reveals on cue; hold still.

## Frame 5 — Pipeline: local RAG index

- scene: Pipeline stage 2 on the same rail — chunks stream into a database; two index tracks (vector waves, keyword mono tokens) fuse into one ranked list
- voiceover: "Then it builds a private search index — vectors for meaning, keywords for exact error codes — fused into one hybrid search."
- duration: 8.256s
- transition_in: push-slide LEFT
- status: animated
- src: compositions/frames/05-local-rag.html
- type: feature_showcase
- persuasion: Progressive disclosure (two indexes, then the fusion) + comparison of two options
- beat: comprehension + "aha"
- blueprint: grid-card-assemble (Adapt)
- focal: the two index tracks converging into the ranked hybrid list
- roles: database cylinder + two track strips = foreground subject · rail continuation = supporting · paper field = background
- sfx: whoosh-soft, tick

narrativeRole: The core mechanism — sessions become a searchable local RAG that finds both meaning and exact identifiers.
keyMessage: Your sessions become a private hybrid vector + keyword index on your machine.

Adapt: keep the staggered assembly signature; items assemble into two parallel tracks and then converge — the convergence is the "aha" reveal, timed to "fused."
Scene 1 (0.0–2.0s): the rail continues from the left edge (stage continuity); chunk tiles stream along it into a database cylinder that draws itself (`svg-path-draw`) center-left; asymmetric 40/60 layout.
Scene 2 (2.0–5.8s): on "vectors for meaning," an upper track reveals — a soft dotted embedding wave strip (layer reveal, staggered dots); on "keywords for exact error codes," a lower track reveals — mono tokens with `ERR_5021_FOO` landing a keyword glow (`asr-keyword-glow`) exactly on its spoken cue.
Scene 3 (5.8–8.0s): on "fused into one hybrid search," the two tracks converge into a single ranked-list card (three hairline result rows) — a reverse cluster expansion (`center-outward-expansion` inverted) — with a small mono "RRF" chip; accent glow lands on the top row; hold still.

## Frame 6 — Pipeline: knowledge graph

- scene: Entity nodes (person, decision, PR, service) spring into a ring around a session core; edges draw on, linking across sessions
- voiceover: "A knowledge graph links the people, decisions, and pull requests behind every session."
- duration: 6s
- transition_in: push-slide LEFT
- status: animated
- src: compositions/frames/06-knowledge-graph.html
- type: feature_showcase
- persuasion: Demonstration (the graph assembling is the mechanism running)
- beat: fascination
- blueprint: constellation-hub (Reproduce)
- focal: the session core node at the hub
- roles: core node = foreground subject · ringed entity nodes + hairline edges = supporting · dim paper field = background
- sfx: tick

narrativeRole: Third mechanism layer — flat transcripts gain structure: who decided what, where it shipped.
keyMessage: Freshet connects sessions into a graph of people, decisions, and PRs.

Scene 1 (0.0–1.5s): a session core node lands center (spring-pop entrance on a smooth settle, `spring-pop-entrance`), mono label "session"; centered layout, hero ~50% of frame with the ring.
Scene 2 (1.5–4.2s): as the VO names each — a person node, a decision node, a PR node spring into a ring around the core (`avatar-cloud-network` staggered entry), each exactly on its spoken cue; hairline edges draw from each to the core (`svg-path-draw`); three smaller unlabeled nodes fill the ring after.
Scene 3 (4.2–6.0s): camera pushes gently IN on the core (`multi-phase-camera`, single move, ends before the hold) while the outer ring softens under a selective blur (`depth-of-field-blur`); hold still on the resolved hub.

## Frame 7 — Push the gold

- scene: One session card among many lifts, seals into an envelope stamped "summary or full transcript", and glides along a drawn path toward a hub mark
- voiceover: "Found gold? Push it — the summary, or the full transcript — to the company hub."
- duration: 6s
- transition_in: push-slide LEFT
- status: animated
- src: compositions/frames/07-push.html
- type: feature_showcase
- persuasion: Frame-then-fill (most sessions are noise; this one matters)
- beat: momentum
- blueprint: compose
- focal: the sealed envelope in transit
- roles: envelope = foreground subject · dim session-card column + hub mark + drawn path = supporting · paper field = background
- sfx: whoosh-soft

narrativeRole: The turn from personal tool to shared system — deliberate, explicit sharing.
keyMessage: You choose what leaves your machine, and in what form.

Compose: motion built from the vocabulary — keyword glow, card morph, path draw + travel; reveals paced to the three VO cues (found gold / push it, two forms / to the hub).
Scene 1 (0.0–1.5s): a left column of five dim session cards (stage continuity with the rail's end point); on "Found gold?" one card lights — accent hairline + keyword glow (`asr-keyword-glow`); rule-of-thirds layout.
Scene 2 (1.5–4.0s): on "Push it," the lit card lifts and morphs into a sealed envelope (`card-morph-anchor`); two mono chips reveal on their cues — "summary" then "full transcript" — beneath it (per-word reveal).
Scene 3 (4.0–6.0s): a hairline path draws rightward (`svg-path-draw`) toward a hub mark entering at the right edge; the envelope travels the path with a slight motion-blur streak (`motion-blur-streak`) and arrives at the hub's gate; hold at the doorstep — the arrival is deliberately unresolved (the next frame answers it).

## Frame 8 — The review queue

- scene: Reconstructed review-queue card — pushed session sits "pending"; a cursor reads and approves; a second reviewer approves; status flips to Merged
- voiceover: "But nothing merges blind. Like a pull request, teammates review and approve — then it joins the company brain."
- duration: 7.488s
- transition_in: crossfade
- status: animated
- src: compositions/frames/08-review-queue.html
- type: feature_showcase
- persuasion: Analogy (pull request) + demonstration
- beat: trust + conviction
- blueprint: cursor-ui-demo (Adapt)
- focal: the Approve interaction and the pending→merged status flip
- roles: review card = foreground subject · custom cursor + reviewer avatar chips = supporting · dim paper field with faint grid = background
- sfx: click, tick

narrativeRole: The trust mechanism — human sign-off gates the shared brain, keeping it high-signal.
keyMessage: Pushed context merges only after teammate review, exactly like a PR.

Adapt: keep the cursor-drives-state-change signature; the "app" is a single minimal hairline review card (no browser chrome), and the flow is vote → threshold → merge.
Scene 1 (0.0–1.7s): on "nothing merges blind," a minimal review card fades up centered (~55% width, y ≈ 0.42): serif title "Pricing decision — discovery thread", mono author line, a badge "pending review · 0 of 2 approvals"; still hold — the stillness reads as the gate.
Scene 2 (1.7–4.6s): on "teammates review and approve," a custom cursor arrives (`cursor-click-ripple`), traces the summary lines, and clicks **Approve** — tactile press (`press-release-spring`), ripple, badge ticks to "1 of 2"; a second reviewer avatar chip slides in and its check lands on cue — badge ticks "2 of 2".
Scene 3 (4.6–7.488s): on "joins the company brain," the status pill morphs pending→**Merged** in accent (`scale-swap-transition`); the card scales down and is absorbed into a small hub/brain glyph upper-right (`scale-swap-transition`), leaving the glyph with one more filled dot; hold still.

## Frame 9 — Ask the company brain

- scene: A question types into a query bar — "What did we decide about pricing?" — an answer card slides up with three citation chips linking to real sessions
- voiceover: "Now anyone can ask — what did we decide about pricing? — and get an answer with citations to the real conversation."
- duration: 7.808s
- transition_in: crossfade
- status: animated
- src: compositions/frames/09-ask-brain.html
- type: benefit_highlight
- persuasion: Worked example + question→answer pairing
- beat: "aha" + delight
- blueprint: typewriter-reveal (Adapt)
- focal: the three citation chips
- roles: query bar + typed question = foreground subject (Scenes 1–2) · answer card = foreground subject (Scene 2 on) · citation chips = supporting, become focal · paper field = background
- sfx: typing, tick

narrativeRole: The payoff made concrete — one org-wide agent, grounded answers, receipts included.
keyMessage: One company-wide RAG agent answers with citations to actual sessions.

Adapt: keep the typed-line→payoff signature; the payoff is the cited answer card rather than a brand pop.
Scene 1 (0.0–2.6s): a minimal hairline query bar centered at y ≈ 0.38; the question types in behind a blinking caret (`discrete-text-sequence` + `context-sensitive-cursor`), timed so the typed words track the spoken question.
Scene 2 (2.6–5.6s): on "get an answer," an answer card slides up beneath the bar (single restrained move) — two serif lines summarizing the decision, revealed per-word under the VO; asymmetric 60/40 with the bar holding the upper third.
Scene 3 (5.6–7.808s): on "citations," three mono citation chips cascade in under the answer (`grid-card-assemble` stagger) — session id + author each — landing a keyword glow (`asr-keyword-glow`) on the middle chip; hold still.

## Frame 10 — Thesis

- scene: Calm title card — "Knowledge stops evaporating." then, in accent, "It compounds."
- voiceover: "Knowledge stops evaporating. It compounds."
- duration: 3.15s
- transition_in: blur-crossfade
- status: animated
- src: compositions/frames/10-thesis.html
- type: branding
- persuasion: Distillation + callback (the evaporating file from Frame 2)
- beat: inevitability
- blueprint: titlecard-reveal (Reproduce)
- focal: the accent line "It compounds."
- roles: two serif lines = foreground subject · warm radial swell = background · nothing else — the emptiness is the design
- sfx: none

narrativeRole: The generalizable landing — the one line the viewer walks away with. This is the video's deliberate held breather.
keyMessage: With capture, review, and retrieval, company knowledge compounds instead of dying.

Scene 1 (0.0–1.5s): "Knowledge stops evaporating." rises in on a single slide-up crossfade (the blueprint's one restrained move) — centered display serif at y ≈ 0.40.
Scene 2 (1.5–3.029s): "It compounds." lands beneath on its spoken cue, accent color, slightly larger; then absolute stillness — no jitter, no drift; the held read against the prior motion is the beat.

## Frame 11 — CTA

- scene: Freshet lockup centered; beneath it, "Give your company a brain." then a mono open-source line
- voiceover: "Freshet. Give your company a brain."
- duration: 2.112s
- transition_in: crossfade
- status: animated
- src: compositions/frames/11-cta.html
- type: cta
- persuasion: Coined term ("company brain") + direct address
- beat: resolve
- blueprint: kinetic-type-beats (Adapt)
- focal: the Freshet lockup
- roles: lockup = foreground subject · tagline + mono repo line = supporting · warm swell + soft ambient glow = background
- sfx: whoosh-soft

narrativeRole: The ask — remember the name, find the repo.
keyMessage: Freshet is the company brain; go get it.

Adapt: keep the beat-landing signature; two beats only, resolving on the lockup instead of a token cycle.
Scene 1 (0.0–0.7s): on "Freshet," the wordmark lands centered (spring-pop entrance, smooth long-tail settle — no overshoot) at y ≈ 0.40, with the accent spike mark ✱.
Scene 2 (0.7–1.5s): on "give your company a brain," the tagline reveals per-word beneath (`dynamic-content-sequencing`).
Scene 3 (1.5–2.112s): a mono sub-line "open source — github.com/rajagurunath/context-hub" fades up quickly; ambient glow blooms softly behind the lockup (`ambient-glow-bloom`); as the final frame, a gentle fade-to-paper exit in the last ~0.3s.
