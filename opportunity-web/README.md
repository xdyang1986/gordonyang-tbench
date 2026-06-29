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
in a table that **stays responsive at thousands of rows** via **mandatory windowed
virtualization** (only on-screen rows in the DOM; scrolling reveals later rows and
off-screen rows unmount; pagination is not accepted); a header summary (visible count +
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
sort semantics (incl. a **zero-spend ROI edge** — `currentSpend = 0` ⇒ highest
priority), multi-term AND search, locale currency formatting (incl. `$0`), summary
aggregation over the full filtered set counting only **open** pipeline (New/Contacted;
Won/Lost excluded), state keyed by opportunity, and — most distinguishing — **true
windowed virtualization** (scrolling reveals deep rows and off-screen rows unmount),
not pagination or render-all.

## How it is graded

The verifier runs in the agent's own container (everything pre-baked; no network at
verify time):

1. **Build** — `npm run build` (`tsc -b && vite build`) must succeed.
2. **Real-browser suite** (`tests/grading/run-playwright.mts`, copied to
   `/app/__grading__/` only at verify time). The built app is served with
   `vite preview` and driven in a **headless Chromium via Playwright**, so the
   scroll-driven windowed virtualization renders correctly (jsdom cannot run it).
   Behaviour is asserted through prompt-pinned `data-testid` hooks, with all expected
   values computed from the shipped dataset. Each assertion surfaces as its own pytest
   case (41 total: build + suite-ran + 39 behaviour cases).

The dataset is ~2,000 rows (`src/data/opportunities.ts` — 20 curated seed rows + 1,980 generated fixtures), so the large-data requirement is real and reproducible.

### UI contract the grader depends on

`search`, `filter-industry`, `filter-product`, `filter-confidence`, `filter-status`,
`sort`, `summary-count`, `summary-uplift`, `opp-row` (one per visible row, contains
the customer name), `row-status`, `row-assignee`, plus per-column header + resize
hooks `col-<key>` and `resize-<key>` for keys `customer, industry, product, spend,
uplift, confidence, status, assignee` (drag `resize-<key>` to resize a column; widths
persist).

## Completion Rates (current — PASSING)

Commit `bf315da`, real-browser (Playwright) grading; the reference oracle passes
**41/41** offline. Platform validation **passes**: balance check *"avocado not trivial
and ≥1 agent solved."* AI assessment **Accept**, contamination **LOW**.

| Model | Pass rate (k=5) |
|-------|-----------------|
| Oracle | 3/3 |
| Avocado | **4/5** — in-band (passes ≥1, fails ≥1) |

avocado lands at 4/5: solvable but non-trivial. The margin is thin (one trial below
5/5), so the per-run result sits right at the in-band boundary.

## Why models fail (current)

All failures are on **stated** behavior. The distinguishing surface:

- **Windowed virtualization (Notes §3).** The table must render only the on-screen
  window — no pagination; scrolling reveals later rows and off-screen rows unmount. The
  grader checks the deep row appears on scroll, the top row unmounts at the bottom (and
  vice-versa on a round-trip), and the window is centered correctly at mid-scroll —
  catching pagination, render-all, and mis-centered windows.
- **Open-pipeline uplift (rule 11).** The header total counts only New/Contacted
  opportunities (Won/Lost excluded), recomputed across filters/search — a status change
  silently changes the aggregate.
- **Zero-spend ROI edge (rule 9).** A `currentSpend = 0` opportunity has the highest
  priority — a div-by-zero edge a careful-but-wrong guard gets wrong.
- **Integrated core:** descending/weighted sort with tie-breaks, multi-term AND search
  over name+rationale, USD-no-decimals formatting (incl. `$0`), summary over the full
  filtered set, per-opportunity state reactive to filters, full view persistence, and
  column resize+persist.

The environment is fully baked and offline, and grading drives the agent's *real* app in
a real browser, so virtualization is graded fairly (not penalized as a jsdom artifact).

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
