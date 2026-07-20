# codimango/pub-sub

## Description
From-scratch **Go** task. The agent writes a Go program under `/app` (golang image ships an empty `/app`, stdlib only) implementing a command-driven in-memory publish-subscribe broker: it reads commands from stdin (one per line) and prints exactly one output line per command. The pytest verifier builds the program with `go build` and drives it as a subprocess, asserting on printed output. Scoring is all-or-nothing.

The broker's semantics deliberately depart from a textbook fan-out pub-sub, and the surface is large and algorithm-heavy (chosen because Go is verbose, so a broad correct implementation is substantial):

1. **Specificity routing** — `PUB` notifies only the most-specific matching tier (exact > longest `prefix.*` > global `*`); `PUBALL` fans out.
2. **Ordering** — `(priority DESC, id DESC)`.
3. **max_calls** — per-subscription delivery budget; auto-removed after N deliveries (`-1` = unlimited).
4. **Publish pipeline** — pause/enqueue → retain → mute → history → route (a muted publish still retains).
5. **Retained replay on subscribe** — a new subscription immediately receives retained messages its pattern matches (insertion order, honoring its budget).
6. **k-of-n dependency-ordered delivery** — `ORDERED` delivers a batch in dependency order with cascade release, priority tie-break, and undeliverable detection (missing deps / cycles / unreachable thresholds).
7. **Capacity-weighted distribution** — `DISTRIBUTE` integer water-filling with a saturation cascade.
8. **Rendezvous-hash sharded delivery** — `SHARD` HRW selection of the top-n by `sha256("key:id")`.
9. **Consistent-hash ring routing** — `RING` virtual nodes on a `sha256` ring with wraparound.
10. **Weighted fair scheduling** — `FAIR` stride scheduling by capacity (persistent per-sub pass).
11. **Token-bucket rate limiting** — `METER`/`REFILL`/`TOKENS`, bucket sized by capacity.
12. **In-order sequence delivery** — `SEQ` reorder buffer with contiguous-run flush; `NEXTSEQ`/`PENDING`.
13. Plus mute patterns, pause/resume queueing, history, and introspection (`MATCH`, `TOPICS`, `COUNT`, `DELIVERED`).

## Completion Rates
- Oracle: passes locally and via `codimango bench run` (reference `solve.sh` writes `main.go`).
- `claude-code` / `claude-opus-4-6` and `metacode` / `meta/avocado_dvsc_tester`: calibration measured empirically online.

Empirical: reference solution passes 71/71 local pytest tests (build + drive the Go CLI).

## Anti-Cheating Analysis
- **Hardcoded outputs**: tests drive the built binary with dynamic command scripts and assert on runtime behavior (routing, ordering, allocations, hashing, sequence/dependency state); hashing expectations are computed independently in the test via sha256; nothing is statically hardcodeable.
- **Overfitting to visible tests**: tests are hidden at solve time; the agent must implement the general protocol.
- **Modifying test files**: the Dockerfile does not copy tests into the image; the harness injects `/tests/` after the agent run.
- **Bypassing the intended path**: the grade builds and runs `/app`'s Go program against the command protocol; only a correct implementation passes.
- **Pinned toolchain**: `GOTOOLCHAIN=local` on a pinned `golang` image; no network needed to build.
