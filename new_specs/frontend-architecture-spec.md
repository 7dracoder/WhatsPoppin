# Frontend Architecture Spec

**Status:** Draft
**Depends on:** [`frontend-design-spec.md`](./frontend-design-spec.md) (v2 — Network-to-Market Explorer). This spec maps that flow onto `frontend/src/`, picks the libraries, and defines the data contracts the backend needs to expose. It does not re-litigate visual design.

## 0. What already exists (read before building)

The scaffold in `frontend/src/` is intentionally minimal:
- `react-router-dom` is wired (`router.tsx`, one route, `AppLayout` → `HomePage`), Tailwind **v3.4.13** (not v4), no state library, no chart/graph library, no UI primitives library yet.
- `lib/apiClient.ts` is a typed `fetch` wrapper with a `get<T>()` helper only — and its own comment is explicit that **no backend HTTP endpoints exist yet** (`ingestion/news_api` is a Python library, called in-process, not a server route).
- `types/content.ts` hand-mirrors `backend/ingestion/news_api/models.py::ContentItem` with a comment flagging there's no shared-types codegen — edits to the Python model don't propagate automatically.

**This matters for sequencing**: almost every stage of the v2 flow (graph fetch, Gemini tags, Kalshi suggestions, Twitter/news candidates, classification query, project persistence) needs a backend HTTP endpoint that doesn't exist yet. Section 5 below defines the contracts; building those endpoints is a prerequisite, not a frontend task, and should be called out to whoever owns the backend before frontend work assumes they're there.

**Tailwind v3 vs v4**: the `frontend-design` skill defaults to v4. Recommend **staying on v3** here — the scaffold already has it configured, CSS-variable theming (the whole v2 token system) works identically in both, and migrating mid-build for a version bump with no feature you actually need is pure churn. Revisit after the MVP, not now.

## 1. Library choices

| Need | Pick | Why |
|---|---|---|
| Force-directed graph (Stages 1-2) | `react-force-graph` (2D canvas variant, `react-force-graph-2d`) | Wraps `d3-force` physics with a React component API — matches the "nodes settle organically" motion the design spec calls load-bearing, without hand-rolling canvas physics under time pressure. Fallback: `cytoscape.js` if the precompiled graph turns out to need non-physics layouts (hierarchical board-of-directors trees, say) — decide after checking real graph shapes from the backend. |
| Charting (Stage 6 normalized overlay) | Defer to the `dataviz` skill for the exact library/config; architecturally this is a single-axis multi-series line chart over time, fed pre-normalized `(timestamp, value)` series. | Keeps this spec from inventing chart internals that skill already owns. |
| UI primitives (cards, checkboxes, dialogs/drawers, buttons) | `@radix-ui/themes` or bare Radix primitives + Tailwind | Named in the design spec's system map as the right fit for "modern accessible React foundation / custom dashboard" — the evidence drawer (Stage 8) and card picker (Stage 4) both want accessible primitives (focus trapping, keyboard dismiss) more than bespoke CSS. |
| State management for the workspace flow | `useReducer` + React Context (no external library) | The whole Stage 0-9 flow is one state machine with a handful of fields (`stage`, `ticker`, `graph`, `tags`, `selectedMarkets`, `annotations`, `queryResults` — see Section 4). That's squarely in `useReducer`'s sweet spot; reaching for Zustand/Redux here adds a dependency for a problem React's own primitive already solves. Revisit only if unrelated distant components (e.g. a global toast system) need the same state — they don't yet. |
| Animation (progressive node reveal, Stage 5 slide, dotted-line draw-in) | `motion` (`motion/react`) | Already the skill's default; needed for the slide transition between graph view and market view and the timeline line draw-in, which are genuinely state-driven animations, not CSS-only. |
| Icons | `@phosphor-icons/react` | Per skill default; avoids `lucide-react`. |

## 2. Navigation model

Confirms the design spec's call: **one route for the live workspace, one route per saved project**.

```
/                        → empty canvas, Stage 0 (no project loaded)
/project/:projectId      → restores a saved Project directly into Stage 6+ (populated Market View), skipping the build animation
```

Everything between Stage 0 and a saved Project (graph build, tag generation, market picking, the slide transition itself) is **client-side state inside one route**, not sub-routes — matching the design spec's reasoning that there's no reason to deep-link into an intermediate, unsaved step. Saving a project (explicit user action, not autosave-by-default — confirm with the design spec's open question on this) is what promotes ephemeral workspace state into a real, linkable `/project/:id`.

## 3. Component tree

```
frontend/src/
├── pages/
│   ├── WorkspacePage.tsx        # "/" — hosts the whole Stage 0-9 state machine
│   └── ProjectPage.tsx          # "/project/:projectId" — loads a Project, renders WorkspaceShell pre-populated
├── features/
│   └── workspace/
│       ├── WorkspaceProvider.tsx       # useReducer + Context, holds WorkspaceState (Section 4)
│       ├── workspaceReducer.ts         # pure reducer, one action per stage transition
│       ├── useWorkspace.ts             # convenience hook wrapping useContext
│       │
│       ├── graph/
│       │   ├── GraphCanvas.tsx         # wraps react-force-graph-2d, progressive node append
│       │   ├── GraphLegend.tsx         # fixed relationship-type color legend
│       │   ├── TickerInput.tsx         # Stage 0 input
│       │   └── BoardSubNetwork.tsx     # Stage 2 expand-on-click secondary graph
│       │
│       ├── tagging/
│       │   └── TagGenerationStep.tsx   # Stage 3 — Gemini call + chip row + loading/skeleton state
│       │
│       ├── markets/
│       │   ├── MarketCardGrid.tsx      # Stage 4 — suggested cards + checkbox state
│       │   └── MarketSearchInput.tsx   # Stage 4 — user's own market search/add
│       │
│       ├── market-view/
│       │   ├── WorkspaceTransition.tsx # Stage 5 — the slide animation host (motion/react)
│       │   ├── OverlayChart.tsx        # Stage 6 — normalized single-axis chart (dataviz-skill-driven internals)
│       │   ├── AddEvidenceButtons.tsx  # Stage 7 — "Add tweets" / "Add news" triggers
│       │   ├── EvidencePickerList.tsx  # Stage 7 — candidate list to select from
│       │   ├── TimelineAnnotations.tsx # Stage 8 — dotted lines on the chart's x-axis
│       │   ├── EvidenceDrawer.tsx      # Stage 8 — right-side slide-in panel (Radix Dialog/Drawer)
│       │   │   ├── NewsEvidenceCard.tsx
│       │   │   └── TweetEvidenceCard.tsx
│       │   └── QueryCard.tsx           # Stage 9 — NL query input + stat result
│       │
│       └── projects/
│           ├── ProjectSidebar.tsx      # Stage 10 — hover-reveal rail, click-to-pin (per design spec's accessibility flag)
│           ├── ProjectTab.tsx
│           └── SaveProjectButton.tsx
│
├── lib/
│   ├── apiClient.ts              # existing — add post<T>()/put<T>() alongside get<T>()
│   └── api/
│       ├── graph.ts               # fetchCompanyGraph(ticker)
│       ├── tags.ts                # generateTags(graph)
│       ├── markets.ts             # fetchSuggestedMarkets(tags), searchMarkets(query)
│       ├── evidence.ts            # fetchCandidateTweets(entities), fetchCandidateNews(entities, window)
│       ├── classification.ts      # runQuery(items, query) — hits backend/classification
│       └── projects.ts            # listProjects(), getProject(id), saveProject(project)
│
└── types/
    ├── content.ts                 # existing ContentItem — reused for news/tweet evidence
    ├── graph.ts                   # GraphNode, GraphEdge, RelationshipType
    ├── market.ts                  # MarketCard — mirrors backend/models/market.py subset
    └── project.ts                 # Project, WorkspaceState (Section 4)
```

This mirrors the existing `pages/` + flat `lib/`/`types/` convention already in the scaffold, adding one `features/workspace/` tree rather than inventing a new top-level pattern.

## 4. State shape

```ts
type Stage =
  | "empty"
  | "building-graph"
  | "graph-ready"
  | "generating-tags"
  | "picking-markets"
  | "market-view";

interface WorkspaceState {
  stage: Stage;
  ticker: string | null;
  graph: { nodes: GraphNode[]; edges: GraphEdge[] } | null;
  tags: string[];
  suggestedMarkets: MarketCard[];
  selectedMarketIds: string[];
  evidence: { tweets: ContentItem[]; news: ContentItem[] };
  pinnedAnnotationId: string | null;   // which evidence drawer is open, if any
  queryResults: QueryResult[];
}

interface Project {
  id: string;
  name: string;
  createdAt: string;
  ticker: string;
  graphSnapshot: WorkspaceState["graph"];   // frozen at save time — don't re-fetch/re-animate on load
  selectedMarketIds: string[];
  evidence: WorkspaceState["evidence"];
}
```

`graphSnapshot` is deliberately a frozen copy, not a live re-fetch on project load — reopening a project should restore exactly what the user curated, not re-run Stage 1's build animation or risk the precompiled graph having changed upstream since the project was saved.

## 5. Backend contracts needed (new work, not yet built)

These are the endpoints `lib/api/*.ts` above assumes. None exist today per Section 0 — flagging the shape so backend work can start in parallel with frontend scaffolding against a mock.

| Endpoint | Request | Response | Backend source |
|---|---|---|---|
| `GET /companies/:ticker/graph` | ticker | `{ nodes: GraphNode[], edges: GraphEdge[] }` | New — reads the "precompiled network" store mentioned in the brief; location TBD, not yet in `backend/models/`. |
| `GET /companies/:ticker/board` | ticker | board-of-directors sub-graph | New, same store, different relationship type. |
| `POST /graphs/tags` | graph payload | `{ tags: string[] }` | New — thin wrapper calling Gemini. |
| `GET /markets/suggest` | `tags[]` | `MarketCard[]` | New — queries Kalshi (reuse `backend/ingestion/kalshi` client) filtered/ranked by tags. |
| `GET /markets/search` | free-text query | `MarketCard[]` | New — direct Kalshi search, no tag filtering. |
| `GET /evidence/tweets`, `GET /evidence/news` | entities, time window | `ContentItem[]` | Thin HTTP wrapper over the **existing** `backend/ingestion/news_api` / `twitter_lookup` libraries — this is "just" exposing library code over HTTP, the actual fetch/normalize logic already exists. |
| `POST /classification/query` | `items: ContentItem[]`, `query: string` | `{ percentage: number, n: number, label: string }` | Wraps `backend/classification` (Jev-based) — **confirm (open question carried from design spec) whether it supports arbitrary free-text queries today or only fixed categories**; this endpoint's shape depends on that answer. |
| `GET /projects`, `GET /projects/:id`, `POST /projects` | — | `Project[]` / `Project` / created `Project` | New — needs persistence (likely a new `projects` table; out of scope for this spec to design, flag to whoever owns `backend/models/`). |

## 6. Loading / error states

Per the design spec's flagged latency risk at Stage 3 (a live Gemini call in the critical path):
- `TagGenerationStep` needs a real skeleton (chip-shaped placeholders, not a spinner) — same "skeleton shaped like the final thing" principle the design spec already calls out for KPI tiles in v1, reapplied here.
- Every `fetch` in `lib/api/*.ts` should surface a typed error state into the reducer (`stage` could gain an `"error"` variant, or each step tracks its own `status: "idle" | "loading" | "error" | "done"` — recommend the latter, since Stage 3's tag call failing shouldn't nuke the already-built graph from Stage 1).
- Empty results (e.g. Kalshi suggests zero markets for a tag set) needs an explicit empty state in `MarketCardGrid`, not a blank grid — same principle as any dashboard empty state, just applied to a card grid instead of a table.

## 7. Open questions (carried + new)

- All backend-contract questions in Section 5 — these block real (non-mocked) integration and should be scoped with the backend owner before frontend work assumes a shape.
- Project autosave vs. explicit save: design spec didn't settle this; affects whether `SaveProjectButton` is the only write path or whether the reducer debounce-persists as the user goes.
- Graph size expectations (carried from design spec) — determines whether `react-force-graph-2d` is sufficient or a WebGL variant (`react-force-graph-3d`'s 2D-but-GPU sibling) is needed for performance.

## 8. Acceptance criteria

- `WorkspacePage` at `/` renders the full Stage 0→9 flow against mocked `lib/api/*` responses before any real backend endpoint exists, so frontend and backend work can proceed in parallel.
- Saving a project writes a frozen snapshot; reloading `/project/:id` renders directly into the populated Market View with no graph-build animation replay.
- Every async step (graph fetch, tag generation, market suggest, evidence fetch, classification query) has a distinct loading and error state wired into the reducer, not just a bare `isLoading` boolean per component.
