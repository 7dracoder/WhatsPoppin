# Frontend Design Spec (v2 — Network-to-Market Explorer)

**Status:** Draft
**Source:** Replaces the v1 dashboard-first spec after a product pivot. Produced with the `frontend-design` skill, against the user's described flow (graph → market comparison → annotation → query), the 5-theme palette work from v1 (`docs/mock_design.png`), and existing feature specs ([[display-charting]], [[trending-cards]], [[news-graphing]], `backend/models/market.py`, `backend/models/alert.py`, `backend/classification/`).

## What changed from v1, and why it's a rewrite, not a patch

v1 designed a dashboard: KPI tiles, an alerts table, a chart, tabs. The actual product you've described is a different interaction model — a **spatial, stateful exploration flow** (empty canvas → graph grows → user curates markets → view slides to a comparison chart → user annotates with evidence → user queries that evidence). That's closer to a graph-exploration tool (think Obsidian's graph view, or a knowledge-graph explorer) crossed with a charting tool, than to an admin dashboard. The component vocabulary is almost entirely different (force-directed graph, card-picker, slide transitions, a right-side evidence drawer) so this is a ground-up rewrite of the flow section.

**Kept from v1**, because it's still correct and theme-agnostic:
- The 5-theme token architecture (`--bg`, `--surface`, `--accent`, gradient-panel device, soft radius) — still the visual skin.
- Locked status/semantic colors across all themes — still the rule, now also governing graph edge colors where they overlap with market direction.
- Geist + Geist Mono typography, dark mode as a systematic transform.

**Discarded from v1**: the dashboard layout itself (KPI tiles, alerts table as the primary view, tab-based navigation) — none of that is in the new flow.

## Design read

A **network-driven market research tool**: a user picks a company, watches its real-world relationship graph (suppliers, competitors, board interlocks) build itself out, and the app uses that graph to surface the prediction markets most likely to be *about* that company's exposure — then lets the user back that choice with a chart, real tweets, and real news, and interrogate that evidence with a query instead of just reading a feed. Audience is the same analyst/investor audience as before, but the "aha" moment is now the graph build-out and the market-suggestion leap, not a dashboard glance.

**Design read:** *a graph-exploration research tool for an analyst sourcing a trading thesis, with a "cute but credible" pastel language (unchanged from v1) and a noticeably more cinematic build/transition feel than a typical dashboard, leaning toward a force-directed graph library + the existing Tailwind v4 theme-token system + a slide-panel transition pattern instead of route-based navigation.*

## Dials (revised)

| Dial | v1 | v2 | Why it moved |
|---|---|---|---|
| `DESIGN_VARIANCE` | 5 | **5** | Unchanged — card grids and panels stay calm; the graph itself provides organic variance so the chrome around it shouldn't compete. |
| `MOTION_INTENSITY` | 4 | **6** | The product's core "wow" moments are motion: nodes progressively appearing/settling, the slide transition from graph view to market view, dotted timeline lines drawing in. This isn't decorative motion — it's load-bearing for the demo. |
| `VISUAL_DENSITY` | 3/5-6 | **3 (graph view) / 6 (market view)** | The graph view should feel like an art-gallery canvas — empty space is the point, it's what makes an appearing node feel like something. The market view is denser (chart + cards + feed) once the user has committed to a thesis. |

## End-to-end flow

Each stage below is a distinct state, not necessarily a distinct route — see "Navigation model" after the flow for why.

### Stage 0 — Empty network canvas
A full-bleed grid/dot-pattern canvas (the "empty graph" — signals *this is a workspace*, not a form). A single input sits centered or docked top: "Add a company by ticker." No nav chrome competing with it. This is the `VISUAL_DENSITY: 3` moment — the emptiness is deliberate, same instinct as an empty-state illustration, except here the payoff for filling it is literal (nodes appear).

### Stage 1 — Ticker added → progressive graph build
Backend returns a precompiled company-to-company graph (your note: "precompiled network of company to company connections"). Nodes **progressively** appear rather than all rendering at once:
- **Why progressive matters architecturally, not just visually**: it tells the user the graph is populating from something precomputed and real, not a static illustration — and it gives you a natural place to stream/paginate if the backend graph is large, instead of blocking on one big payload.
- Center node = the searched ticker. Edges fan out to 1st-degree connections (suppliers, competitors, etc.), each edge colored by relationship type.
- **Library choice**: use a force-directed graph library (`react-force-graph` — wraps `d3-force` physics with a React-friendly API, or `cytoscape.js` if you need more layout algorithms later) rather than hand-rolling canvas physics. `d3-force`'s physics (nodes repel, edges act as springs, the whole thing "settles") is exactly the organic-but-controlled motion `MOTION_INTENSITY: 6` wants, and it's a known quantity under MVP time pressure — don't reinvent this.

### Stage 2 — Sub-networks (board of directors)
Per your note, each company node can expand a **smaller, secondary network** centered on that company for board-of-directors relationships (person nodes, not company nodes). This is a different node *kind*, not just more of the same:
- Recommend this stays collapsed by default (click-to-expand on a node, not always-rendered) — a full board-of-directors layer rendered for every company by default would overwhelm the primary company-to-company story at `VISUAL_DENSITY: 3`. Progressive disclosure here mirrors the dashboard-drilldown principle from v1, just applied to a graph instead of a table row.
- Visually distinct node shape or size (smaller, person-icon) so "this is a person, not a company" is legible without reading the label.

### Stage 3 — Tag generation (Gemini)
Once the user is satisfied with the graph (or after a short settle delay), a "Generate tags" step sends the graph's relationship data to Gemini, which returns a set of descriptive tags (e.g. "semiconductor supply chain," "EV battery exposure," "antitrust risk"). These tags are the query vector into Kalshi, not shown as a dead-end feature — flag this in the UI as a visible, labeled step (e.g. a chip row appearing above the market cards) so the user understands *why* these specific markets got suggested, rather than the suggestion feeling arbitrary.

### Stage 4 — Market card picker
~10 Kalshi markets matching the tags render as cards: market photo/thumbnail, market title, a checkbox. Plus a search field for the user to find and add their own market outside the suggested set.
- Card grid, not a table — these are being *chosen*, not scanned for data, so the card format (image-forward, like a product picker) is the right call over a dense table row.
- Checkbox state is the only interaction; no destructive or ambiguous actions here.

### Stage 5 — Generate → slide transition to Market View
Clicking "Generate" with 1+ markets checked slides the whole viewport from the graph view to the Market View (a literal horizontal slide, not a route change/page reload) — see Navigation model below for why this needs to be an animated state transition, not routing.

### Stage 6 — Market View: the comparison chart
This is where v1's "dual-scale" assumption needs to be corrected against what you actually described:

> **Architecture decision — normalized single-axis, not dual-axis.** v1 spec'd a dual-axis chart (stock in USD on the left axis, odds in % on the right axis) to preserve real units. Your description here says "a chart with the **normalized** kalshi market chosen and stock chosen" — that's a different, and for this product's actual question, better fit: normalize both series to a common baseline (e.g., % change from the start of the visible window, or index-to-100) and plot them on **one shared y-axis**. Dual-axis preserves real units but makes "do these two move together" a visual guessing game across two independently-scaled axes; normalizing to % change makes correlation/divergence directly readable, which is the actual job of this chart (does the market's odds move track the stock's move). Keep real units available as a tooltip value on hover, so nothing's lost, just not what's plotted.

- Color: stock gets the neutral "ground truth" line (`--text-primary`-adjacent), each market series gets a distinct hue from a categorical palette (deferred to the `dataviz` skill for exact values) — unchanged reasoning from v1.
- X-axis is the shared timeline that Stages 7-8 annotate.

### Stage 7 — Adding Twitter posts and news
Two explicit "Add" actions (buttons, not auto-populated) surface candidates:
- **Twitter**: relevant accounts/posts (tied to the company/market entities) shown as a pickable list — user adds specific ones.
- **News**: articles matching the chart's visible timeline window, same pick-to-add pattern.
Both reuse the entity-matching already built for `ContentItem` in `backend/ingestion/news_api` — this view doesn't need new backend matching logic, just a UI over the existing `EntityMatcher` output.

### Stage 8 — Timeline annotations + evidence drawer
Each added news/tweet item draws a **dotted vertical line** on the chart at its timestamp. Clicking a line opens a **right-side slide-in panel** (not a modal — a modal would cover the chart the user is trying to correlate the evidence against):
- **News template**: source logo, headline, snippet, timestamp, link out. This needs a small logo-lookup table (source name/domain → logo asset) — flag as a build task, not a design question.
- **Tweet template**: render it looking like an actual tweet (avatar, handle, text, timestamp) rather than inventing a custom card — users already have a mental model for "this is a tweet," don't fight it. If embedding real Twitter/X oEmbed isn't feasible in the time available, a close static facsimile is an acceptable fallback, but should visually read as "this is the tweet UI," not "this is a generic social card."

### Stage 9 — Query card (NL query over evidence)
A card where the user types a natural-language query (e.g., "what % of these are positive sentiment" or a custom true/false/choice question) and runs it against the top N added news/tweets, returning a stat (e.g. "73% positive, n=11"):
- This is a direct UI surface for the backend's existing classification work (`backend/classification/`, the Jev-based text classifier already built per recent commits) — worth confirming with whoever owns that code whether it already supports arbitrary user-provided queries/choices or only fixed categories, since that changes whether this card's input is a free-text query or a preset dropdown.
- Result display: a single stat card (percentage + n), not a chart — this is a quick gut-check, not a new visualization.

### Stage 10 — Projects sidebar (left, hover-reveal)
A collapsed rail on the left expands on hover to show tabs, one per saved **Project** (= ticker + chosen market(s) + added news/tweets, as a persisted bundle). Clicking a tab restores that whole state (graph + market view).
- **Flag on hover-only reveal**: hover-to-expand is a nice space-saving pattern, but it's unreliable on touch devices and for keyboard navigation (nothing to focus/tab into if it only opens on mouseover). Recommend hover-to-*preview* plus click-to-*pin open*, so there's always a non-hover way to get into it. Worth confirming this app is mouse/desktop-only for the MVP (v1 flagged the same question and deferred it) before treating hover-only as final.

## Navigation model: state machine, not routes

Recommend the whole flow (Stages 0-9) lives as **client-side state transitions within one view**, not separate routed pages:
- The slide transition in Stage 5 only makes sense as an animated state change — a route change (even with a shared-layout animation library) adds complexity for no benefit here, since there's no reason to deep-link into "market view" without also carrying the graph that produced it.
- A Project (Stage 10) is the thing that gets a stable identity/URL (`/project/:id`), not the intermediate steps — restoring a project restores the state machine directly into Stage 6+ with its graph/market/evidence already populated, skipping the build animation on reload.
- State shape sketch (for the architecture spec to formalize): `{ stage, ticker, graph: { nodes, edges }, tags, availableMarkets, selectedMarkets, twitterAdds, newsAdds, queryResults }`, persisted as a `Project` once named/saved.

## Visual system notes

- **Edge/relationship coloring**: use a fixed categorical legend (e.g. supplier/consumer = one hue, competitor = another, industry-peer = another, board-interlock = a distinct hue for Stage 2's person-nodes) — always render a visible legend near the graph, don't make color-coding something the user has to infer.
- **Node styling**: company nodes use the theme's `--accent` family for the searched/center node specifically (it's the one node that's always "yours"), with other company nodes in a neutral tone so the center doesn't get lost once the graph is dense.
- **Status-color rule still applies**: if any node or edge ever encodes market direction (bullish/bearish) rather than relationship type, it must use the locked `--status-positive`/`--status-negative` tokens from v1, never a new ad-hoc color — don't let the graph's relationship palette and the market-direction palette collide on the same hue.
- Theme/token system, dark mode transform, and theme switcher from v1 carry over unchanged — this spec doesn't revisit them.

## Open questions (carry into the frontend architecture spec)

- **Graph library pick**: confirm `react-force-graph` (or Cytoscape.js) against actual graph size expectations (how many 1st-degree connections does the precompiled graph typically have per ticker?) — physics-based layout degrades past a few hundred nodes, worth knowing before committing.
- **Gemini tag step latency**: Stage 3 adds a live LLM call into the critical path before markets even render — decide whether this needs a loading/skeleton state that's actually designed (per the dashboard skill's "skeleton shaped like the final thing" principle) or whether it's fast enough to not need one.
- **Query card backend contract**: does `backend/classification` support arbitrary user queries today, or only fixed categories — resolves whether Stage 9's input is free text or a constrained picker.
- **Hover-only sidebar**: confirmed open per above — desktop-only MVP scope assumption needs sign-off same as v1's mobile question.
- Tweet rendering: real oEmbed vs. static facsimile — depends on time available and Twitter API access, not a design call.

## Acceptance criteria

- Adding a ticker on the empty canvas progressively renders a real company-to-company graph (not an instant full-graph render).
- A company node can expand a secondary board-of-directors sub-network without replacing the primary graph.
- Generated tags visibly drive the Kalshi market suggestions shown in the card picker, and a user can add a market the suggestions missed via search.
- Generate transitions (slides, doesn't reload) into a Market View showing stock and chosen market(s) normalized onto one shared axis.
- Added news/tweets appear as dotted timeline markers; clicking one opens a right-side panel with the correct template (news vs. tweet) without closing or obscuring the chart.
- The query card returns a real stat (percentage + n) against the currently-added evidence set.
- A saved Project reopens directly into its populated Market View state, skipping the graph-build animation.
