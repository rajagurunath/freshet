# Context Hub: Market Opportunity

**Every company running AI coding assistants is generating a goldmine of structured decision logs — and deleting nearly all of it.** When a developer works through an architectural trade-off with Claude Code, the reasoning lives in a local `.jsonl` file on one laptop, unseen by anyone else, ephemeral by default. Context Hub captures those sessions at the agent/CLI boundary, aggregates them company-wide, and exposes them through a single internal RAG agent — the organizational brain that Shopify, Ramp, and Dan Shipper have independently described as the inevitable next infrastructure layer for every knowledge-work company.

**TL;DR**
- AI coding sessions are the richest, most under-used knowledge artifact in modern engineering orgs; no product captures them at the team level today
- The "one agent per company" thesis is validated by Shopify's River (1-in-8 PRs company-wide), Ramp's 300+ internal agents, and Glean's $7.2B enterprise valuation
- TAM ~$9B / SAM ~$4B / SOM ~$62M ARR at $300/seat/year; AI coding is the #1 enterprise GenAI category at $4B and growing 7x YoY
- No competitor sits at the intersection of session capture + team aggregation + RAG agent; the whitespace is explicit
- GTM is bottoms-up: free personal desktop app → paid shared org agent → enterprise with SOC2/VPC deployment
- The moat is compounding, non-portable organizational context — the longer an org runs, the more irreplaceable the corpus becomes

---

## 1. The Big Idea & Vision

The single most important infrastructure bet for the next three years is not which AI model a company buys — it is whether the company builds a memory layer that makes its AI investment compound over time.

Three independent signals, published within days of each other in May 2026, converged on the same prediction. Dan Shipper, CEO of Every, stated it directly: **"Every company will have one 'super-agent' inside their Slack that every employee talks to regularly"** — an "institutional infrastructure: a single place where the company's decisions, context, and operational knowledge live — queryable by anyone, updated continuously, maintained by someone whose job is to keep it accurate" (source: [lennysnewsletter.com/p/the-ai-paradox-dan-shipper](https://lennysnewsletter.com/p/the-ai-paradox-dan-shipper)). He explicitly cited Shopify and Ramp as already running this.

Shopify is the clearest proof point. **River**, Shopify's Slack-native agent backed by the Aquifer platform, now touches **1 in 8 merged PRs company-wide**: 59,918 River sessions across 5,170 Slack channels and 3,536 River-coauthored PRs merged in a single 30-day window (source: [shopify.engineering/under-the-river](https://shopify.engineering/under-the-river)). The design choice that made this possible: River operates only in public channels by default, so every session becomes a searchable, learnable transcript. "One person's hard-won fix becomes the next person's starting point."

Ramp operationalized the same idea on Notion: **300+ active custom agents running daily, 3-minute agent creation, ~70% reduction in productivity-tool costs, 3x faster team velocity** (source: [notion.com/customers/ramp](https://notion.com/customers/ramp)). Glean has built the commercial infrastructure layer: **$7.2B valuation, $200M+ ARR, 100M+ annual agent actions**, grounding agents in an Enterprise Context Graph that understands "how your company really works" (source: [businesswire.com](https://businesswire.com), [glean.com/product/enterprise-graph](https://glean.com/product/enterprise-graph)).

The through-line across all three: the agent must be **shared by default and backed by accumulated organizational memory**. Private sessions die with the conversation. Public sessions compound into institutional knowledge.

Context Hub's thesis is that the richest, most untapped source of that organizational memory is the AI coding sessions already happening on every developer's laptop — and that the capture layer at the agent/CLI boundary is the moat that none of the large incumbents has bothered to build.

---

## 2. Why Now

Three curves crossed simultaneously in 2025–2026, creating a window that did not exist eighteen months ago.

**Adoption is universal.** 84–91% of developers now use AI tools; ~42% of committed code is AI-assisted, projected to reach 65% by 2027 (source: [getdx.com/blog/ai-assisted-engineering-q4-impact-report-2025](https://getdx.com/blog/ai-assisted-engineering-q4-impact-report-2025); [uvik.net/blog/ai-coding-assistant-statistics](https://uvik.net/blog/ai-coding-assistant-statistics/)). GitHub Copilot alone surpassed 20 million all-time users by mid-2025 (source: [medium.com/@reliabledataengineering](https://medium.com/@reliabledataengineering/ai-is-writing-46-of-all-code-github-copilots-real-impact-on-15-million-developers-787d789fcfdc)). The behavior that generates sessions already exists at scale; Context Hub harvests it rather than creating it.

**Sessions became rich structured logs.** Agentic coding tools — Claude Code, Codex, Cursor, Kilo Code — now persist full transcripts locally as parseable JSON: prompts, tool calls, file diffs, reasoning chains, rejected alternatives. This is qualitatively richer raw material for RAG than wikis or Jira tickets, and it is sitting unused on disk at every company that has deployed these tools.

**The ROI reckoning has arrived.** Enterprise generative-AI spend reached $37B in 2025, up 3.2x from 2024; coding is the single largest application category at $4B (source: [menlovc.com/perspective/2025-the-state-of-generative-ai-in-the-enterprise](https://menlovc.com/perspective/2025-the-state-of-generative-ai-in-the-enterprise/)). Yet 73% of companies investing over $1M/year in gen-AI see only limited ROI (source: [deloitte.com/global/en/issues/generative-ai/ai-roi-the-paradox-of-rising-investment-and-elusive-returns](https://www.deloitte.com/global/en/issues/generative-ai/ai-roi-the-paradox-of-rising-investment-and-elusive-returns.html)). Leaders have bought thousands of seats and have no visibility into what is happening inside them. Context Hub is the first product that turns those sessions into a visible, queryable asset — the CTO's ROI slide and the organizational memory layer simultaneously.

---

## 3. The Problem / Pain

The core problem is that modern engineering knowledge now lives primarily inside AI sessions — and those sessions are invisible to everyone except the individual who ran them.

**Lost decisions and tribal knowledge.** When a developer works through three architectural options with Claude Code and selects one, the reasoning ("we picked Postgres over DynamoDB because of X; we rejected the queue approach because of Y") exists only in a local session file. Six months later a different team re-litigates the same trade-off from scratch, often reaching a worse conclusion without the context of what was already tried.

**Duplicated AI work.** Two engineers independently prompt the model to solve the same migration, debug the same flaky test, or scaffold the same integration. The organization pays for the tokens twice and the human time twice — at scale, a material and quantifiable waste.

**Onboarding tax.** New hires take ~8 months to reach full productivity; lost-productivity cost during ramp runs approximately $20K/engineer/month (source: [teamstation.dev/nearshore-it-staffing-articles/the-true-cost-of-onboarding-a-software-engineer-in-2025](https://teamstation.dev/nearshore-it-staffing-articles/the-true-cost-of-onboarding-a-software-engineer-in-2025)). A searchable record of how a codebase's decisions actually got made is the highest-leverage onboarding artifact that does not exist today.

**Search overhead.** Knowledge workers lose ~30% of the workday hunting for information (source: [cdpinstitute.org/news/knowledge-workers-lose-30-of-time-looking-for-data-forrester-study](https://www.cdpinstitute.org/news/knowledge-workers-lose-30-of-time-looking-for-data-forrester-study/); [cottrillresearch.com/various-survey-statistics-workers-spend-too-much-time-searching-for-information](https://cottrillresearch.com/various-survey-statistics-workers-spend-too-much-time-searching-for-information/)). That number was computed before the era of agentic coding — and the sessions that could answer most of those searches are sitting unindexed on individual laptops.

---

## 4. Market Sizing

Context Hub sits at the intersection of three converging markets: AI code tools, enterprise knowledge management, and RAG/vector infrastructure.

**Reference market anchors (2025):**
- AI coding assistant market: $7.37B in 2025, growing to $29.96B by 2031 at ~26% CAGR; large enterprises = 59.5% of revenue (source: [mordorintelligence.com/industry-reports/artificial-intelligence-code-tools-market](https://www.mordorintelligence.com/industry-reports/artificial-intelligence-code-tools-market))
- Enterprise knowledge management software: $22.9–23.2B in 2025, growing ~13.6% CAGR to ~$82B by 2035 (source: [futuremarketinsights.com/reports/knowledge-management-software-market](https://www.futuremarketinsights.com/reports/knowledge-management-software-market); [straitsresearch.com/report/knowledge-management-software-market](https://straitsresearch.com/report/knowledge-management-software-market))
- RAG market: $1.94B (2025) growing to $9.86B (2030) at 38.4% CAGR (source: [marketsandmarkets.com/Market-Reports/retrieval-augmented-generation-rag-market](https://www.marketsandmarkets.com/Market-Reports/retrieval-augmented-generation-rag-market-135976317.html))
- Enterprise GenAI coding spend specifically: $4B in 2025, up 7.3x from $550M in 2024 (source: [menlovc.com/perspective/2025-the-state-of-generative-ai-in-the-enterprise](https://menlovc.com/perspective/2025-the-state-of-generative-ai-in-the-enterprise/))

**Bottom-up TAM / SAM / SOM at $300/seat/year:**

| Tier | Seat Count | ARR |
|---|---|---|
| TAM — all professional developers using AI tools (36.5M × 84% adoption) | ~30.7M seats | ~$9.2B |
| SAM — enterprise segment only (~45% of AI-using pro devs in centralized orgs) | ~13.8M seats | ~$4.1B |
| SOM — 3-year realistic capture at 1.5% SAM penetration | ~207K seats | ~$62M |
| SOM conservative (Year 1–2, 0.2% SAM) | ~28K seats | ~$8M |

The $300/seat/year price sits between GitHub Copilot Business (~$228/year) and premium dev-intelligence tools, justified by the org-agent layer on top of raw capture. Doubling to $600/seat — defensible once the org agent displaces KM-software spend — doubles all figures.

The 36.5M professional developer figure (source: [slashdata.co/post/global-developer-population-trends-2025](https://www.slashdata.co/post/global-developer-population-trends-2025-how-many-developers-are-there)) and 84% AI adoption (source: [uvik.net/blog/ai-coding-assistant-statistics](https://uvik.net/blog/ai-coding-assistant-statistics/)) provide the seat-base foundation. The $4B enterprise GenAI coding spend validates the price point independently.

---

## 5. Competitive Landscape

The market splits into three adjacent categories. None of them sit where Context Hub sits.

| Competitor | What they capture | Team aggregation | RAG agent for the org | Agentic session capture |
|---|---|---|---|---|
| **Glean** ($7.2B, $200M ARR) | SaaS documents, Slack, Drive | Yes | Yes | No |
| **Dust** ($40M Series B, ~$20M ARR) | 100+ connectors, authored content | Yes | Yes | No |
| **Microsoft 365 Copilot / Gemini Enterprise** | SharePoint, OneDrive, Drive | Yes | Partial | No |
| **Unblocked** | Code artifacts, PRs, Jira, Slack | Yes | Partial | No |
| **Pieces for Developers** | OS-level snippets, terminal, chats, LTM-2 | No (individual only) | No | Closest — but single-player |
| **Continue Hub** | Agent configurations | Partial (config sharing) | No | No |
| **Langfuse / Helicone / LangSmith** | LLM traces (production apps) | No | No | Telemetry only |
| **Sourcegraph (Amp)** | Codebase retrieval | No | No | No |

**Whitespace analysis.** The gap is explicit: observability tools (Langfuse, Helicone, Braintrust) capture sessions but frame them as debugging telemetry for app builders, not searchable knowledge for engineers. Enterprise search tools (Glean, Dust, Notion AI) aggregate org knowledge but index documents and artifacts — not the ephemeral reasoning inside coding-agent sessions. Pieces proves single-player session capture resonates, but stops at the individual; Unblocked proves teams buy a context engine, but is blind to sessions (source: [getunblocked.com/blog/unblocked-context-engine-for-agents](https://getunblocked.com/blog/unblocked-context-engine-for-agents/)).

DIY evidence confirms the unmet pain: engineers are building personal knowledge bases from Claude Code sessions using Obsidian, local RAG-MCP plugins, and manually maintained `CLAUDE.md` files (source: [puvaan.dev/posts/building-a-persistent-knowledge-base-for-claude-code](https://puvaan.dev/posts/building-a-persistent-knowledge-base-for-claude-code/); [github.com/lyonzin/knowledge-rag](https://github.com/lyonzin/knowledge-rag)). Demand is real; the product does not exist yet.

**No vendor today does:** passively capture local agentic-coding sessions across multiple tools → aggregate them company-wide → expose them as a searchable RAG knowledge hub with a single org agent.

---

## 6. Our Wedge & Differentiation

**The wedge is capture at the agent/CLI boundary.** Not documents. Not PRs. Not Slack threads. The raw session: the prompt, the tool calls, the file diffs, the reasoning chain, the rejected alternatives — everything that happened inside Claude Code or Codex before a single line was committed. This is where modern engineering knowledge is created, and it is where every adjacent competitor is blind.

**The entry motion is single-player, then social.** Ship a genuinely useful free desktop app first: "browse, search, and summarize your own Claude Code/Codex history." No cloud, no consent questions, no IT approval. One engineer installs it for themselves, builds the habit, and immediately encounters the moment when a teammate's session would have answered the question they just asked the model again. That moment is the conversion to Team.

**The differentiation stack:**
1. **Capture layer** — hooks into Claude Code, Codex, and Kilo Code session files at the CLI/filesystem boundary. No API instrumentation required; no changes to existing tooling.
2. **On-device redaction** — automated secret-scanning (entropy + regex + named-entity recognition) runs before any upload. The developer sees exactly what would be shared and confirms each session explicitly. Privacy is not a feature — it is the architecture.
3. **Shared org agent** — the paid unlock. Once multiple developers have captured sessions, the org RAG agent can answer "how did we decide X" and "has anyone solved Y" across the full team corpus. This is the River/Aquifer thesis (source: [shopify.engineering/under-the-river](https://shopify.engineering/under-the-river)) applied to the engineering AI session layer specifically.
4. **ROI visibility** — anonymized team-level dashboards showing which questions are being re-asked, where duplicated AI work is occurring, and what the session corpus has already solved. This is the slide the CTO needs for the board.

---

## 7. Buyer & User Personas

| Persona | Role | Pain | How Context Hub wins |
|---|---|---|---|
| **Individual Developer** | User / installer | Own sessions unsearchable; re-solving the same problems | Free local app; instant personal value; no IT involvement |
| **VP Eng / Director of Engineering** | First economic buyer | Onboarding cost ($20K/mo lost productivity), duplicated work, lost decisions | Team tier; quantifiable time savings; onboarding artifact |
| **Head of AI Platform / DevEx** | Champion for org-wide deal | "Are our AI seats producing ROI?" | ROI dashboards; platform narrative; feeds existing AI tooling budget |
| **CTO** | Vision buyer / top-cover | Institutional memory, AI strategy, board-level ROI | Org memory narrative; aligns with Shopify/Ramp operating model |
| **CISO / Head of Data Governance** | Blocker to convert | Data leakage, employee surveillance, sovereignty | On-device redaction, VPC/self-hosted deployment, SOC2, explicit consent UX |
| **Head of Knowledge / Ops** (Phase 3) | Expansion buyer | Knowledge management gaps beyond engineering | Cross-functional hub expansion |

The land-and-expand motion: the developer loves the free tool → the eng leader buys Team → the DevEx lead turns a team pilot into a company contract → the CISO signs off because the privacy architecture was designed in, not bolted on.

---

## 8. Pricing & GTM

**Pricing model: per-seat anchor with metered AI consumption**, mirroring the convergent pattern across Glean, Dust, Langfuse, and Sourcegraph (source: [eesel.ai/blog/glean-pricing](https://www.eesel.ai/blog/glean-pricing); [dust.tt/home/pricing](https://dust.tt/home/pricing); [langfuse.com/pricing](https://langfuse.com/pricing); [sourcegraph.com/blog/changes-to-cody-free-pro-and-enterprise-starter-plans](https://sourcegraph.com/blog/changes-to-cody-free-pro-and-enterprise-starter-plans)).

| Tier | Price | What's included |
|---|---|---|
| **Free** | $0 / 1 user | Desktop app, unlimited local capture, personal search over own sessions |
| **Team** | ~$20/user/mo (annual) | Shared org RAG agent, team connectors, up to 25 seats, indexed-token quota, SOC2 report |
| **Business** | ~$40/user/mo | SSO, role-based access, audit logs, higher quotas |
| **Enterprise** | ~$50–65/user/mo + consumption overage, 100-seat floor | SCIM, data residency (EU/US), VPC/self-hosted deployment, SLAs, EU AI Act DPA, target ACVs $60K–$250K |

Pure usage pricing scares CFOs; pure per-seat strands the product when a large org's corpus and query volume grow. The metered overage on Enterprise captures incremental value from the accounts where the moat is deepest.

**GTM motion:**
1. **Land (PLG, individual dev, free):** Viral install inside orgs. No buyer involved. The hook is purely personal: "never lose the context of a coding session again." Targeting developers already using Claude Code and Codex who are hacking their own solutions today.
2. **Expand (Team tier, eng lead, self-serve):** Once 3–5 devs on a team are using the free app, in-product nudges activate at the moment of highest pain: "3 of your teammates have already solved this — see their sessions." Self-serve checkout; an eng lead can expense it without procurement.
3. **Enterprise (sales-assist triggered by bottoms-up adoption):** When seat count crosses ~50 or IT notices the desktop app, trigger a sales conversation. By then the usage data, proof of value, and internal champion (DevEx lead) already exist. The sales conversation is about SSO, residency, and SOC2 — not about whether the product works.

---

## 9. Moat / Defensibility

**Compounding organizational context is the primary moat.** After 12 months of capture, an org's RAG agent knows the undocumented decisions, dead-ends, and architectural reasoning that live nowhere else. This corpus is irreplaceable — it was not authored, it was observed, and it cannot be reconstructed from PRs or Slack threads. Switching to a competitor means starting the knowledge base from zero. The moat deepens with every session, and the metered Enterprise pricing grows with it — the product literally charges more as it becomes more valuable and harder to leave.

**Intra-org data network effects.** The more developers capture, the better every teammate's answers. This is the Shopify River thesis implemented as a product flywheel: "one person's hard-won fix becomes the next person's starting point" (source: [shopify.engineering/under-the-river](https://shopify.engineering/under-the-river)). Network effects are intra-org rather than cross-org (each customer's corpus is correctly siloed), so the durable moat is switching cost plus accumulated proprietary context — not a global network.

**Distribution moat from PLG.** A free desktop app installed by individuals seeds accounts before procurement engages — the same motion that built Notion and Sourcegraph's enterprise distribution. By the time a top-down conversation happens, the usage proof is already internal.

**Capture layer as a technical moat.** Hooking into local agent session files at the CLI/filesystem boundary — and building robust redaction, parsing, and indexing pipelines for every major agent tool — is non-trivial engineering. The more agents Context Hub supports (Claude Code, Codex, Kilo Code, Cursor, Cline), the wider the capture moat becomes relative to any new entrant starting from scratch.

---

## 10. Risks & Mitigations

| Risk | Description | Mitigation |
|---|---|---|
| **Secrets and credential leakage** | Sessions contain API keys, prod credentials, customer data embedded in prompts | Automated entropy + regex + NER secret-scanning on-device before any upload; block-by-default on detection; developer sees exactly what is redacted; opt-in per session, never default-on capture |
| **Employee surveillance perception** | Developers fear management monitoring individual sessions | Lead with developer value, not management dashboards; publishing is explicit per-session opt-in; ROI metrics are anonymized at team level; no keystroke monitoring; product narrative is "your memory," not "management visibility" |
| **Data governance / residency requirements** | EU, finance, and healthcare customers demand regional data pinning | VPC/self-hosted deployment at Enterprise tier; customer owns the store; vendor never sees raw session data; data residency by region |
| **Central store as high-value attack target** | All organizational decisions in one place is a significant attack surface | Encryption at rest/in transit; SSO/SCIM; tenant isolation; SOC2 Type 2 before first enterprise deal (required by 70%+ of enterprise RFPs); SOC2 audit underway from seed stage, not retrofitted |
| **EU AI Act compliance** | Company-wide RAG agent over employee work product may trigger Article 50 transparency obligations; August 2026 deadline | Pre-build a trust center and model-provenance documentation; ISO 27001/42001 + SOC2 cover 40–60% of AI Act controls; include AI Act DPA sections in Enterprise contracts (source: [workstreet.com/blog/eu-ai-act-compliance](https://www.workstreet.com/blog/eu-ai-act-compliance)) |
| **Foundation model COGS exposure** | Inference and embedding costs scale with corpus size | Metered overage on Enterprise tier; generous included quotas at Team/Business; avoid unlimited AI flat pricing |
| **Incumbent platform moves** | GitHub, Atlassian, or Microsoft adds session capture to existing tooling | Speed to defensible data corpus + depth of capture layer are the buffer; incumbents are document-centric by architecture, not session-centric; 12–18 months to build comparable capture fidelity |

The privacy and governance architecture is not a compliance checkbox — for this product it is the primary sales motion with the enterprise buyer who matters most.

---

## 11. 12-Month Plan / What We're Building

**Months 1–3: Personal capture layer (Free tier launch)**
- Desktop app for macOS/Windows capturing Claude Code and Codex session files locally
- Personal search and summarization over own session history
- On-device secret-scanning and redaction pipeline
- Instrumentation for session quality, engagement, and search patterns
- Target: 1,000 free installs; qualitative customer discovery with 20+ developers

**Months 4–6: Team hub (Team tier launch)**
- Shared org RAG agent with explicit per-session opt-in publishing
- Team connectors (GitHub PRs, Jira, Confluence) to enrich session context
- Self-serve Team tier checkout at $20/user/month
- In-product nudges at "your teammate solved this" moments
- SOC2 Type 2 audit initiated
- Target: 5–10 paying teams, $50K–$100K ARR

**Months 7–9: Enterprise readiness**
- SSO/SCIM, role-based access, audit logs (Business tier)
- Data residency options (US/EU); VPC deployment architecture built
- SOC2 Type 2 report complete
- First enterprise pilots with DevEx/AI Platform buyers at $40–65/seat
- Kilo Code and Cursor session capture added
- Target: 2–3 enterprise pilots, $200K–$400K ARR pipeline

**Months 10–12: Land-and-expand at scale**
- Sales-assist motion triggered by in-product seat-count signals
- EU AI Act compliance documentation and trust center published
- Anonymized team-level ROI dashboards for CTO/VP Eng buyers
- Cross-team expansion within pilot accounts
- Target: $1M–$2M ARR, 2–3 referenceable enterprise customers, Series A data room ready

---

## 12. Sources

- [shopify.engineering/under-the-river](https://shopify.engineering/under-the-river) — Shopify River / Aquifer architecture, May 28, 2026
- [lennysnewsletter.com/p/the-ai-paradox-dan-shipper](https://lennysnewsletter.com/p/the-ai-paradox-dan-shipper) — Dan Shipper on the super-agent thesis, May 24, 2026
- [notion.com/customers/ramp](https://notion.com/customers/ramp) — Ramp's AI operating system case study
- [aicatchup.com/practices/internal-ai-workspaces-playbook](https://aicatchup.com/practices/internal-ai-workspaces-playbook) — Ramp "Glass" internal AI workspace
- [glean.com/product/enterprise-graph](https://glean.com/product/enterprise-graph) — Glean Enterprise Context / Enterprise Graph
- [businesswire.com](https://businesswire.com) — Glean Work AI Institute launch, December 2025
- [epicenter.to/p/how-to-build-an-ai-agent-company](https://epicenter.to/p/how-to-build-an-ai-agent-company) — Glean CEO Arvind Jain on contextual intelligence
- [menlovc.com/perspective/2025-the-state-of-generative-ai-in-the-enterprise](https://menlovc.com/perspective/2025-the-state-of-generative-ai-in-the-enterprise/) — Menlo Ventures 2025 State of Gen-AI in the Enterprise
- [mordorintelligence.com/industry-reports/artificial-intelligence-code-tools-market](https://www.mordorintelligence.com/industry-reports/artificial-intelligence-code-tools-market) — AI code tools market sizing
- [market.us/report/ai-code-assistant-market](https://market.us/report/ai-code-assistant-market/) — AI code assistant market cross-check
- [slashdata.co/post/global-developer-population-trends-2025](https://www.slashdata.co/post/global-developer-population-trends-2025-how-many-developers-are-there) — Developer population 2025
- [uvik.net/blog/ai-coding-assistant-statistics](https://uvik.net/blog/ai-coding-assistant-statistics/) — AI coding adoption rates
- [futuremarketinsights.com/reports/knowledge-management-software-market](https://www.futuremarketinsights.com/reports/knowledge-management-software-market) — Enterprise KM software market
- [straitsresearch.com/report/knowledge-management-software-market](https://straitsresearch.com/report/knowledge-management-software-market) — KM software CAGR cross-check
- [marketsandmarkets.com/Market-Reports/retrieval-augmented-generation-rag-market-135976317.html](https://www.marketsandmarkets.com/Market-Reports/retrieval-augmented-generation-rag-market-135976317.html) — RAG market sizing
- [verifiedmarketresearch.com/product/vector-database-market](https://www.verifiedmarketresearch.com/product/vector-database-market/) — Vector database market sizing
- [getdx.com/blog/ai-assisted-engineering-q4-impact-report-2025](https://getdx.com/blog/ai-assisted-engineering-q4-impact-report-2025/) — DX AI-assisted engineering impact report
- [deloitte.com/global/en/issues/generative-ai/ai-roi-the-paradox-of-rising-investment-and-elusive-returns](https://www.deloitte.com/global/en/issues/generative-ai/ai-roi-the-paradox-of-rising-investment-and-elusive-returns.html) — Deloitte AI ROI paradox
- [teamstation.dev/nearshore-it-staffing-articles/the-true-cost-of-onboarding-a-software-engineer-in-2025](https://teamstation.dev/nearshore-it-staffing-articles/the-true-cost-of-onboarding-a-software-engineer-in-2025) — Engineering onboarding cost
- [cdpinstitute.org/news/knowledge-workers-lose-30-of-time-looking-for-data-forrester-study](https://www.cdpinstitute.org/news/knowledge-workers-lose-30-of-time-looking-for-data-forrester-study/) — Forrester knowledge worker time loss
- [cottrillresearch.com/various-survey-statistics-workers-spend-too-much-time-searching-for-information](https://cottrillresearch.com/various-survey-statistics-workers-spend-too-much-time-searching-for-information/) — McKinsey search overhead
- [medium.com/@reliabledataengineering](https://medium.com/@reliabledataengineering/ai-is-writing-46-of-all-code-github-copilots-real-impact-on-15-million-developers-787d789fcfdc) — GitHub Copilot 20M users, 46% code generation
- [getunblocked.com](https://getunblocked.com/) / [getunblocked.com/blog/unblocked-context-engine-for-agents](https://getunblocked.com/blog/unblocked-context-engine-for-agents/) — Unblocked context engine
- [glean.com/press/glean-raises-150m-series-f-at-7-2b-valuation](https://www.glean.com/press/glean-raises-150m-series-f-at-7-2b-valuation-to-accelerate-enterprise-ai-agent-innovation-globally) — Glean Series F
- [techcrunch.com/2025/06/10/enterprise-ai-startup-glean-lands-a-7-2b-valuation](https://techcrunch.com/2025/06/10/enterprise-ai-startup-glean-lands-a-7-2b-valuation/) — Glean TechCrunch coverage
- [siliconangle.com/2026/05/18/multiplayer-ai-startup-dust-swipes-40m-funding](https://siliconangle.com/2026/05/18/multiplayer-ai-startup-dust-swipes-40m-funding-help-enterprises-move-beyond-isolated-ai-assistants/) — Dust Series B
- [sifted.eu/articles/dust-series-b-40m](https://sifted.eu/articles/dust-series-b-40m) — Dust funding details
- [dust.tt/home/pricing](https://dust.tt/home/pricing) — Dust pricing
- [langfuse.com/pricing](https://langfuse.com/pricing) — Langfuse pricing
- [sourcegraph.com/blog/changes-to-cody-free-pro-and-enterprise-starter-plans](https://sourcegraph.com/blog/changes-to-cody-free-pro-and-enterprise-starter-plans) — Sourcegraph Cody plan changes
- [eesel.ai/blog/glean-pricing](https://www.eesel.ai/blog/glean-pricing) — Glean pricing reference
- [metronome.com/pricing-index/glean](https://metronome.com/pricing-index/glean) — Glean pricing index
- [weavai.app/blog/en/2026/04/30/sourcegraph-cody-review-2026-enterprise-ai-at-59-mo](https://weavai.app/blog/en/2026/04/30/sourcegraph-cody-review-2026-enterprise-ai-at-59-mo/) — Sourcegraph Cody $59/user
- [notion.com/pricing](https://www.notion.com/pricing) — Notion pricing
- [pieces.app/features/long-term-memory](https://pieces.app/features/long-term-memory) — Pieces LTM-2
- [pieces.app/blog/types-of-ai-memory](https://pieces.app/blog/types-of-ai-memory) — Pieces memory types
- [continue.dev/pricing](https://www.continue.dev/pricing) — Continue Hub pricing
- [blog.continue.dev](https://blog.continue.dev/what-are-continue-agents) — Continue agents
- [puvaan.dev/posts/building-a-persistent-knowledge-base-for-claude-code](https://puvaan.dev/posts/building-a-persistent-knowledge-base-for-claude-code/) — DIY Claude Code knowledge base
- [github.com/lyonzin/knowledge-rag](https://github.com/lyonzin/knowledge-rag) — DIY RAG-MCP plugin
- [tracxn.com/d/companies/lancedb](https://tracxn.com/d/companies/lancedb/__ie1HuEEUoPOIc3tEX5yowY9yMJz9kdNTH01mwCePxLw) — LanceDB Series A
- [helicone.ai/blog/the-complete-guide-to-LLM-observability-platforms](https://www.helicone.ai/blog/the-complete-guide-to-LLM-observability-platforms) — LLM observability landscape
- [braintrust.dev/articles/best-ai-observability-platforms-2025](https://www.braintrust.dev/articles/best-ai-observability-platforms-2025) — AI observability platforms
- [workstreet.com/blog/eu-ai-act-compliance](https://www.workstreet.com/blog/eu-ai-act-compliance) — EU AI Act for US SaaS
- [saascity.io/blog/saas-compliance-checklist-2026-soc2-gdpr-ai-act](https://saascity.io/blog/saas-compliance-checklist-2026-soc2-gdpr-ai-act) — SaaS compliance checklist 2026