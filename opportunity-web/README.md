# codimango/opportunity-web

## Description

The agent must build, **from scratch**, an "Ads Opportunity Board" — a React 18 +
TypeScript + Vite single-page app that helps a sales rep work a list of upsell
opportunities for ads customers. The container ships only a scaffold: build tooling
(Vite/TypeScript/Tailwind), shared types (`src/types.ts`), and a **large** fixed
dataset (`src/data/opportunities.ts` — thousands of rows; `src/data/reps.ts`).
`src/main.tsx` already mounts a `src/App.tsx` that does not exist yet, so the project
does not build until the agent creates it.

From that scaffold the agent must implement the whole app: render the opportunities
in a table that **stays responsive at thousands of rows** (pagination or
virtualization — not all rows in the DOM at once); a header summary (visible count +
total estimated monthly uplift over the *full filtered set*); multi-term search over
customer name + rationale; dropdown filters for industry, product, confidence, and
status; sorting by estimated uplift, current spend, confidence (High>Medium>Low,
tie-broken by uplift), and a computed **priority** score (ROI = uplift/spend, weighted
by confidence, scaled and rounded); per-opportunity workflow state (status +
assignee) that stays tied to the opportunity across re-sorts and keeps the view
reactive (a row whose status no longer matches an active status filter must leave the
table immediately); and **full persistence to `localStorage`** — every change,
including the search query, active filters, and sort selection (not just row state),
is restored automatically after a refresh.

This tests whether an agent can turn a terse product brief plus a data contract into a
correct, integrated, and **performant** React app — composing derived/memoized views,
pure list transforms, a persistence layer, and a large-data rendering strategy. A
naive approach fails on the details that are independently graded: descending/weighted
sort semantics, multi-term AND search, locale currency formatting, summary aggregation
over the full filtered set (not the visible page), state keyed by opportunity, and —
most distinguishing — recognizing that a multi-thousand-row table must not be dumped
into the DOM.

## How it is graded

The verifier runs in the agent's own container (everything pre-baked; no network at
verify time):

1. **Build** — `npm run build` (`tsc -b && vite build`) must succeed.
2. **Real-browser suite** (`tests/grading/run-playwright.mts`, copied to
   `/app/__grading__/` only at verify time). The built app is served with
   `vite preview` and driven in a **headless Chromium via Playwright**, so both
   pagination and virtualization render correctly (jsdom cannot run virtualization).
   Behaviour is asserted through prompt-pinned `data-testid` hooks, with all expected
   values computed from the shipped dataset. Each assertion surfaces as its own pytest
   case (31 total: build + suite-ran + 29 behaviour cases).

The dataset is ~2,000 rows (`src/data/opportunities.ts` — 20 curated seed rows + 1,980 generated fixtures), so the large-data requirement is real and reproducible.

### UI contract the grader depends on

`search`, `filter-industry`, `filter-product`, `filter-confidence`, `filter-status`,
`sort`, `summary-count`, `summary-uplift`, `opp-row` (one per visible row, contains
the customer name), `row-status`, `row-assignee`.

## Completion Rates

Empirical pass rates with real-browser (Playwright) grading:

| Model | Pass rate (k=5) |
|-------|-----------------|
| Oracle | **3/3** |
| Sonnet 4.6 (informational) | 5/5 |
| Opus 4.6 | 5/5 |
| **Avocado** | **4/5 (0.80)** |

> Calibration target: **Avocado is in-band** — it passes ≥1 and fails ≥1 of 5. Opus
> 4.6 solves it cleanly (5/5).

## Model Analysis

- **Avocado — 4/5 passed.** The 1 failing trial **never created `src/App.tsx`** (built
  the root component elsewhere), so the contracted entry point was missing → setup
  error across all cases. A genuine spec-following gap.
- **Opus 4.6 — 5/5 passed.** Handles the full app, including the trickier requirements
  (large-data bounded rendering, ROI priority, reactive status-filter, and full
  view-persistence across refresh).
- **Sonnet 4.6 — 5/5 passed** (informational only).
- **Oracle — 3/3.**

**Dominant failure mode:** entry-contract adherence — not creating the root component
at the contracted `src/App.tsx`. Earlier iterations also showed large-data handling
(failing to bound the rendered rows) and full-view persistence (persisting search/
filters/sort across refresh, not just row state) as differentiators. All are genuine
engineering / spec-following reasoning gaps, not task-setup issues: the environment is
fully baked and offline (no verify-time downloads), and the grading runs the agent's
*real* app in a real browser
(so virtualization is graded fairly, not penalized as a jsdom artifact). The
arithmetically tricky ROI priority and the reactive status-filter rule add further
correctness surface that weaker implementations slip on.

## Anti-Cheating Analysis

- **Hardcoded outputs** — Expected values (counts, totals, sort orders, filtered
  subsets, priority scores) are computed from the shipped dataset and checked against
  the live DOM after real user interactions; static markup cannot satisfy the
  search/filter/sort/summary/persistence assertions together.
- **Overfitting to visible tests** — The grading script is never in the agent's
  container during its run; it is copied to `/app/__grading__/` only at verify time.
  The agent sees only the scaffold and the instruction's UI contract.
- **Modifying test files** — Tests live under `/tests`, are staged in at verify time
  after the agent stops, and are rerun fresh; the reward file is written by `test.sh`,
  not the agent. The grading tooling is installed `--no-save`, so the agent-visible
  `package.json` reveals nothing about it.
- **Bypassing the intended solution path** — Grading serves and drives the agent's
  real built app in a real browser, and `npm run build` forces a type-correct Vite
  build; stubbing, faking a green run, or hardcoding a single view fails. The
  bounded-window check (large dataset, real DOM node count) prevents "render
  everything" from passing.
