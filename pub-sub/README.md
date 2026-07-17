# codimango/pub-sub

## Description
From-scratch task. The agent must create `/app/pubsub.py` containing a thread-safe, in-memory publish-subscribe broker (`class PubSub`) whose **delivery semantics deliberately depart from a textbook fan-out pub-sub**, across a broad API surface. The Dockerfile ships an empty `/app`; the agent writes the whole module. Scoring is all-or-nothing, so missing any single behavior fails the task.

Non-textbook behaviors (a naive "notify everyone who matches, in subscribe order" implementation fails):

1. **Specificity routing** — `publish` notifies only the most-specific matching tier (exact > longest `prefix.*` > global `*`); `publish_all` fans out.
2. **Ordering** — `(priority DESC, subscription-id DESC)` (LIFO), not ascending id.
3. **once-successful** — `subscribe_once` is removed only after a non-raising delivery.
4. **Live removal, snapshot additions** — a subscription removed mid-delivery is skipped and not counted; additions do not join the in-progress delivery.
5. **Publish pipeline** — pause/enqueue → filters (rewrite/abort) → retain → mute → route (order matters; a muted publish still retains).
6. **Retained replay** — retained messages are delivered to a subscriber at subscribe time (its own pattern, insertion order, honoring the once rule).
7. **k-of-n dependency-ordered delivery** — `publish_ordered(events)` delivers a batch in dependency order: an event is ready once ≥ `threshold` of its `deps` are delivered, delivering cascades to unblock others, ties break by `(priority DESC, arrival ASC)`, and events with missing deps / cycles / unreachable thresholds are undeliverable.
8. **Per-subscription call budgets** (`max_calls`, generalizing `subscribe_once`) and **data transforms**; a global **error handler** (`set_error_handler`); per-topic **history** logs (`get_history`/`clear_history`) recorded in the pipeline after the mute check; and batch ops (`subscribe_many`, `publish_batch`).
9. Plus filters, mute patterns, pause/resume queueing, and introspection (`get_matching_count`, `topics`, `delivered_count`, …).

Thread-safety uses `threading.RLock`; delivery snapshots under the lock and releases before invoking callbacks/filters so reentrant calls do not deadlock.

## Completion Rates
- Oracle: 1/1 locally and via `codimango bench run` (reference solution).
- `claude-code` / `claude-opus-4-6`: expected to pass with careful reading of the pipeline, routing, and dependency-ordering contract.
- `metacode` / `meta/avocado_dvsc_tester` (validation gate): expected to struggle — the spec is terse (no worked examples), the surface is broad, and scoring is binary, so mis-inferring any behavior fails.

Empirical: reference solution passes 102/102 local pytest tests.

## Model Analysis
Expected failure modes for an implementation relying on pub-sub priors rather than the spec:
- **Fan-out instead of specificity routing** → specificity/`get_matching_count` tests.
- **Ascending-id / no-priority ordering** → ordering tests.
- **Remove-once-on-any-delivery** → once-successful test.
- **Pure-snapshot delivery** → live-removal test.
- **Wrong pipeline order** (retaining after mute, not queueing while paused, etc.) → filter/mute/pause/retain tests.
- **No retained replay on subscribe** → retained tests.
- **Single-pass instead of cascade / wrong tie-break in `publish_ordered`** → dependency-ordering tests.
- **Missing methods** → `AttributeError`.
- **Holding the lock across callbacks / non-reentrant lock** → reentrancy deadlock timeout.
- **Broken/absent locking** → concurrency tests.

## Anti-Cheating Analysis
- **Hardcoded outputs**: Tests generate dynamic data and assert runtime behavior (counts, order, retained/queued state, dependency ordering); nothing is statically hardcodeable.
- **Overfitting to visible tests**: Tests are hidden at solve time and cover many feature combinations requiring a general implementation.
- **Modifying test files**: The Dockerfile does not copy tests into the image; the harness injects `/tests/` after the agent run.
- **Bypassing the intended path**: The only way to pass is to implement `PubSub` with the specified contract.
- **Pinned dependencies**: Stdlib only; pytest pinned in `tests/test.sh`; Dockerfile pins `python:3.11-slim` from the ECR mirror.
