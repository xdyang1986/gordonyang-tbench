# Large-Scale Observability for Ride-Hailing (Uber-like) — Multi-Turn Go Task (Hardened for Difficulty + Novelty)

## Overview
This is a multi-turn terminal-bench task simulating observability system design for a high-throughput ride-hailing platform like Uber. **Now significantly hardened** (Aug 2026 v2) — both steps made harder with edge cases that require precise spec reading, not just OTel recall.

**Format**: `terminal_bench_multi_turn` with 2 steps, `inherit_prior_session=true` on step2.

**Novelty redesign (Aug 2026)**: Original version was a near-verbatim clone of OpenTelemetry Go SDK (types `SamplingParameters`, `ReadableSpan`, `SpanProcessor`, `DecisionDrop`, `NewInMemoryExporter`, `Inject`/`Extract` etc). Models could recall OTel architecture for free; only concurrency defects discriminated. Redesigned contract intentionally **punishes OTel recall**:

- **Propagation**: Single header `x-ride-trace` = `traceID:spanID:parentID:1/0` (colon separated, 4 parts even when parent empty as `tid:sid::1`), not four keys `trace-id`/`span-id`/`parent-id`/`sampled`. Tests assert `Inject`/`MarshalTrace` writes only `x-ride-trace` and does NOT write `trace-id`. Validation strict: 32hex TraceID, 16hex SpanID, empty or 16hex ParentID, sampled must be "0" or "1" else invalid => background. Flags must be 1 if sampled else 0, preserved via propagation.
- **RatioSampler deterministic algorithm**: Last 8 hex chars as uint32 / 2^32 (4294967296), not first 16 hex as uint64 / 2^64. Fixed TraceIDs where methods disagree: `0000000000000001ffffffffffffffff` (first 16 tiny => OTel Keep, our spec Drop) and opposite. Boundary exact threshold: 0x80000000 /2^32 =0.5 => Drop (< not <=). Case-insensitive hex. Model recalling OTel fails.
- **Error/critical override precedence**: RatioSampler must Keep if `Status == StatusError` or `Priority == "critical"` regardless of fraction, **checked BEFORE invalid TraceID check** so invalid+error => Keep (error always sampled). OTel ratio ignores status/priority and would Drop at 0.0; tests include ratio 0.0 + error => Keep, invalid+error => Keep, invalid alone => Drop.
- **ParentAware AND logic**: ParentAwareSampler requires **both** parent sampled AND root sampled to Keep. OTel ParentBased Keeps whenever parent sampled ignoring root. Test: root Never (0.0) + parent sampled true => must Drop (OTel would Keep). Description must contain root Description as substring + contain word Parent. Root nil fallback to Always.
- **BatchProcessor evict-oldest**: On full queue, evict oldest and keep newest (ride-hailing keeps latest rides). OTel drops newest. Test: queue size 2, enqueue 5, ForceFlush => exported are span-3, span-4 (newest) in order, not span-0,1. Order preserved. DroppedCount increments on evict.
- **ForceFlush block-and-drain**: During ForceFlush, OnEnd must block, not drop. OTel non-blocking drop would increment DroppedCount during flush. Test fills queue 2, starts slow flush 200ms, concurrent enqueue during flush must not drop, DroppedCount stays 0. Harder variant: 10 concurrent goroutines during flush all must block then succeed.
- **Minimal skeleton**: Environment Dockerfile now provides only type definitions and panic stubs for MemoryExporter, SimpleProcessor, MarshalTrace/UnmarshalTrace, MetricsProvider, Logger — no free deep-copy or propagation implementation. You must implement from scratch.

Renaming alone is cosmetic; these semantic inversions plus defensive-copy and nil-handling edge cases ensure OTel memorized behavior is **wrong** and naive implementations fail.

## Step 1: Core Observability & Design Quality — Hardened

Implement foundational primitives in Go package `observability`:

- **Tracing**: `TraceContext` (alias `SpanContext`), `FinishedSpan` (alias `ReadableSpan`), `Tracer`, `Span`, `MemoryExporter` (alias `InMemoryExporter`), `SimpleProcessor` (alias `SimpleSpanProcessor`), context helpers `ContextWithTrace`/`TraceFromContext` (aliases), propagation `MarshalTrace`/`UnmarshalTrace` (aliases `Inject`/`Extract`) using single header `x-ride-trace` with 4 parts, Flags 1/0, ParentID consistency `FinishedSpan.ParentID==SpanContext.ParentID`, nil ctx handling (Start(nil, ...) and ContextWithTrace(nil, ...) must not panic and return non-nil), context immutability (copy not reference), Context() returns defensive copy, attribute handling duplicate last-wins and distinct counting (duplicate does NOT increase 128 limit, overwrite allowed after limit, empty key ignored), attribute value truncate exactly 1024 boundary, event timestamp between Start/End and recent, export snapshot stable copy under race, End idempotent exactly once under 50 goroutines requiring sync.Once, AddAttribute/AddEvent racing with End atomic, MemoryExporter deep copy of slice+Attributes map+Events slice+Event.Attributes and non-nil Labels, Clear reuse, thread-safe.
- **Metrics**: `MetricsProvider` with `Counter`, `Gauge`, `Histogram`, defensive copies for WithLabels map and WithBuckets slice (mutating original after creation must not affect), Collect deep copy of Labels non-nil and Buckets slice, label value truncate 256 before key and reuse after truncation (two 500-char differing only after 256 should reuse), label order irrelevant (sorted keys), Gauge Set/Add ignore NaN/Inf, Counter Add >=0 ignore negative/NaN/Inf, Histogram default buckets 11, cumulative inclusive, sorted ascending with dedup of duplicate upper bounds, Observe ignores NaN/Inf, thread-safe concurrent Collect+Add via RWMutex race detector 100x1000 ops, provider isolation, type conflict first registration wins (counter vs gauge vs histogram).
- **Logging**: JSON structured logger `Logger` with trace correlation (auto-injects `trace_id`, `span_id`, `sampled`, optional `parent_id`), With immutable copy sharing mutex for atomic per-line writes (100 concurrent goroutines → 100 valid JSON lines no interleaving), level filtering debug<info<warn<error> default info case-insensitive, WithOutput nil fallback stderr, WithLevel unknown fallback info, no panic on nil ctx/fields.

Design quality checks via extensive concurrency tests (100 goroutines), idempotency, defensive-copy isolation, nil handling, no global state sharing, stdlib-only, go vet clean.

**Expected API (see steps/1_step_one/instruction.md for exact signatures):**
- `NewTracer(serviceName, opts...)`, `NewMemoryExporter()`, `NewSimpleProcessor()`
- `ContextWithTrace`, `TraceFromContext`, `MarshalTrace`, `UnmarshalTrace` (single header 4 parts)
- `NewMetricsProvider()`, `Counter`, `Gauge`, `Histogram`, `Collect()`, `DroppedSeriesCount()`
- `NewLogger(serviceName, opts...)`

## Step 2: Large-Scale Hardening — Hardened Further

Build on Step1 to handle 10k+ req/sec:

- **Sampling**: `Sampler` interface, `AlwaysSampler` (alias `AlwaysOn`), `NeverSampler` (alias `AlwaysOff`), `RatioSampler` (alias `TraceIDRatioBased`) with last-8-hex uint32/2^32 + error/critical override precedence over invalid + boundary < not <= + ignores name/kind + deterministic + statistical tolerance 0.05-0.15 etc, `ParentAwareSampler` (alias `ParentBased`) with AND logic requiring parent sampled AND root Keep, root nil fallback Always, Description must contain root Description as substring and contain Parent word. WithSampler for Tracer (nil no-op), priority extraction from attribute key "priority" exact lower-case but sampler case-insensitive for "critical", non-recording spans not exported but context still propagated with Sampled false, invalid parent TraceID handling no panic.
- **BatchProcessor**: async queue with `WithBatchSize`, `WithQueueSize`, `WithBatchTimeout`, `WithExportTimeout`, **WithMaxBatchSize and WithMaxExportBatchSize aliases for WithBatchSize last wins**, non-positive values fallback to defaults 512/2048/5s/30s, BatchSize hard cap max per export even on Shutdown flush, evict-oldest on full (keep newest) with DroppedCount increment and order preserved (queue2 enqueue 5 => 3,4 in order), background goroutine collecting up to BatchSize or BatchTimeout, no busy loop sleep 10ms, concurrency-safe many goroutines, Backpressure normally non-blocking few ms, ForceFlush drains queue+batch exporting incomplete batches, block-and-drain during ForceFlush where OnEnd blocks not evict/drop and DroppedCount stays 0 even if queue full before, ForceFlush empty returns nil, many concurrent during flush all succeed, ExportTimeout via goroutine+select respecting ctx timeout (slow exporter 500ms with 50ms timeout => ForceFlush returns <350ms), export failure continues, ordering FIFO preserved across splits, DroppedCount thread-safe excluding non-recording, QueueLen counts waiting excluding in-progress batch never exceeds QueueSize never negative even under 20x100 concurrent, Shutdown stops accepting new spans, flushes queue+batch respecting BatchSize splitting into multiple exports, respects ctx timeout, idempotent via sync.Once second call no deadlock, concurrent Shutdown+ForceFlush where ForceFlush blocked waiting queue space unblocks returning nil or ctx error no deadlock, after Shutdown OnEnd drops no panic, goroutine exits no leak.
- **Metrics Cardinality Limiting**: `WithMaxCardinality(n)`, `WithCardinalityOverflowHandling("drop"|"aggregate")` case-insensitive handling, `DroppedSeriesCount()` distinct dropped counting (same dropped label repeated does not double count, truncation interaction before key so same truncated prefix reuses not dropped, per-name limit not global, reuse at limit does not increase dropped, aggregate mode overflow aggregation `__overflow__="true"` shared per metric name with value aggregated sum and Dropped 0), thread-safe.
- **Resource Limits**: attribute distinct 128 (duplicate not counted, overwrite allowed after limit), attribute value truncate exactly 1024 boundary, event count 128 keep first 128 with timestamp between Start/End, label value truncate 256 before cardinality key, defensive copies everywhere.

High-throughput verification: 100 goroutines x 100 spans, 10 tracers sharing same BatchProcessor, counting exporter max batch assertions, statistical sampling tolerance including fixed TraceIDs where OTel fails, backpressure timing, cardinality overflow with truncation interaction and distinct counting, shutdown flush in batches respecting size, evict-oldest order, block-and-drain with many concurrent, alias last wins, non-positive fallback, shutdown idempotent + concurrent.

## Layout
```
environment/Dockerfile       # Go 1.22 + pytest, minimal skeleton panic stubs (hard — no free impl)
steps/1_step_one/
  instruction.md             # detailed hardened API spec (step 1: 284 defs, 281 collected)
  tests/test_outputs.py      # black-box Go harness via pytest, hardened with defensive-copy, nil handling, Flags, duplicate counting, gauge NaN, histogram dedup, logger shared mutex across child loggers, all-zero IDs, Flags normalized, long key, mixed valid/invalid attrs, IDGenerator empty, invalid parent hex, sampled strict, spaces trimmed per-part, parent invalid reuse, service fallback, etc.
  solution/solve.sh          # reference implementation with hardened semantics (defensive copies, empty key ignore, duplicate handling after limit, Flags normalized both directions, nil ctx, gauge NaN ignore keep prev, dedup buckets, shared logger mutex across With chain, all-zero invalid, invalid parent hex no write, IDGenerator empty handling, exportMu ordering fix respecting ctx timeout, exporter events deep copy)
steps/2_step_two/
  instruction.md             # scale requirements hardened with precise edge cases (step 2: 128 defs, 127 collected)
  tests/test_outputs.py      # includes last8 vs first16, error override precedence over invalid, boundary exact, parent AND, evict-oldest order, block-and-drain many concurrent, alias last wins, shutdown idempotent concurrent, truncation interaction, distinct dropped counting, aggregate overflow value, export timeout via select, ordering preserved, etc.
  solution/solve.sh          # implements hardened logic (droppedKeys tracking, truncation-before-key, gauge NaN, dedup, batch alias last wins, shutdown Once, exportMu ordering fix respecting ctx timeout)
task.toml                    # explicit NOT API log analyzer, for dedup separation
```

## Running

Verifier is pytest that generates ephemeral Go modules importing `ride-observability`
from `/app` and runs `go run` and `go run -race`. The harness wraps pytest in `set +e`
so `reward.txt` is always written.

```bash
bash steps/1_step_one/solution/solve.sh && pytest steps/1_step_one/tests/test_outputs.py -q
bash steps/2_step_two/solution/solve.sh && pytest steps/2_step_two/tests/test_outputs.py -q
```

## Latest online validation result

**Commit `f6cf85ba` (HEAD, v1.48) · run 2026-08-17 · jobs 4968008 / 4968009 / 4968010 / 4968011 · AFTR run 9223875**

> **All gates green.** Validation passing, Agentic Full-Task Review **GOOD**, TBR
> 17/18 now passing (was 16/18 `fail` at `c021115c`). Status is `draft` — no revision
> outstanding.

| Field | Value |
| --- | --- |
| `validationStatus` | **passing** — all 5 gates |
| Agentic Full-Task Review | **GOOD** — difficulty `GENUINELY_HARD`, all 13 rubrics PASS, secondary issues NONE |
| `tbdReviewStatus` | **pass** — TBR 17/18 |
| TBR axes | instruction_clarity **2**; all five others 3 |
| Difficulty classification | GOOD — Opus 467/877 tests (53%), oracle 237/277 (86%) |
| Novelty risk | MEDIUM |
| Contamination | Not checked — repo not yet covered by the pipeline |
| Provenance | CLEAN |
| Embedding dedup | 0.7664 (threshold 0.8) |
| Structural checks | 10/10 PASS |
| `status` / `reviewStatus` | draft |

| Stage | Job | Result | Reward split |
| --- | --- | --- | --- |
| oracle | 4968009 | **3/3** | 3 × 1.00 |
| codex (`gpt-5.5`) | 4968011 | **9/10** | 9 × 1.00, 1 × 0.50 |
| metacode (avocado `avocado-5.14-code`) | 4968010 | **3/10** | 3 × 1.00, 2 × 0.50, 5 × 0.00 |
| agent (opus `claude-opus-4-8`) | 4968008 | **1/10** | 1 × 1.00, 9 × 0.00 |

Pass/fail balance gate: **passed** — avocado not trivial (3/10) and ≥1 agent solved.

### Failure spread (all 17 non-1.00 trials, from downloaded `ctrf.json`)

| Test | Step | Count | opus | avocado | codex |
| --- | --- | --- | --- | --- | --- |
| `test_tracing_flags_normalized_in_contextwithtrace` | **1** | 14 | 9 | 5 | 0 |
| `test_tracing_contextwithtrace_flags_normalized_both` | **1** | 14 | 9 | 5 | 0 |
| `test_tracing_all_zero_spanid_parentid_variants` | 1 | 4 | 4 | 0 | 0 |
| `test_batch_forceflush_block_many_concurrent_during_flush` | 2 | 2 | 0 | 2 | 0 |
| `test_batch_timeout_trigger` | 2 | 1 | 0 | 0 | 1 |
| `test_batch_order_preserved` | 2 | 1 | 0 | 1 | 0 |
| `test_batch_forceflush_concurrent_producers_block_then_succeed` | 2 | 1 | 0 | 1 | 0 |

Reading:
- **Step 1 rests on a single semantic rule, duplicated across two tests.** The two
  flags-normalization tests fail *in lockstep in all 14 failing trials* — never one
  without the other. They are one discriminator counted twice: `ContextWithTrace` must
  normalise `Flags` from `Sampled` in both directions. The AFTR independently suggests
  consolidating them.
- **That one rule sets opus's score.** Opus is 1/10, and every one of its 9 failures is
  a step-1 failure containing this pair. The rule *is* specified, but only inside the
  changelog-style dump at the end of step 1's `instruction.md` — the same text TBR
  penalises for clarity.
- **Failures are surgical.** No trial fails more than 3 tests out of 281 (step 1) or
  127 (step 2); most fail exactly 2 — the lockstep pair.
- **Step 2 discriminates only lightly**, via four distinct batch-processor tests
  (ForceFlush block-and-drain, FIFO order, timeout trigger) hitting one trial each.

### Open quality items (non-blocking)

Both remaining TBR concerns and the AFTR's optional notes, verified against the repo:

1. **Four test functions are shadowed by duplicate names.** Step 1 defines 284 but
   collects **281**; step 2 defines 128 but collects **127**. Duplicates:
   `test_logger_output_nil_fallback`, `test_tracing_concurrent_end_exactly_once`,
   `test_tracing_event_timestamp_between_start_end` (step 1) and
   `test_parent_aware_nested` (step 2). Python keeps only the last definition, so the
   earlier body never runs. This exactly explains the count mismatch the AFTR reports —
   the fix is to de-duplicate so all 284/128 run, not to restate 281/127 in prose.
2. **The "no busy loop CPU" tests do not measure CPU.**
   `test_batch_processor_no_busy_loop_cpu` (step 2) sleeps 500 ms then asserts one span
   exports; a busy-looping implementation passes. Vacuous relative to its name.
3. **Instruction verbosity** keeps `instruction_clarity` at 2 — the spec carries
   internal changelog noise (`v6: … v7 new: …`) and self-hedging prose, and leaks
   version and test-count information to the solver.
4. **Timing margins** — the AFTR asks that batch timing tests stay conservative, since
   `R09:Test reliability` depends on scheduler behaviour on slower hosts.

None of these block use; the AFTR states *"No required fixes are needed before use."*
Note that consolidating the duplicate flags-normalization tests would remove step 1's
only discriminator, so weigh that against the balance gate before acting.

## Novelty Fix Details

Original API clone list:
```
observability.NewBatchSpanProcessor      observability.SamplingParameters
observability.NewSimpleSpanProcessor     observability.DecisionDrop
observability.ReadableSpan               observability.DecisionRecordAndSample
observability.SpanProcessor              observability.NewParentBasedSampler
observability.SpanContext                observability.NewTraceIDRatioSampler
observability.SpanKindServer             observability.NewAlwaysOnSampler / AlwaysOff
observability.Inject / Extract           observability.NewInMemoryExporter
observability.ContextWithSpanContext     observability.WithIDGenerator
observability.SpanContextFromContext     observability.WithBatchTimeout / WithExportTimeout
observability.StatusOK / StatusError     observability.WithQueueSize / WithBatchSize
```
These are exact identifiers from `go.opentelemetry.io/otel/sdk/trace`, `tracetest`, and propagation API verbatim. The task was "reimplement OTel Go SDK" — architecture free recall, only race conditions required real work.

**Fix v1 (prior)**:
- Renamed core types: `TraceContext`, `FinishedSpan`, etc., old names kept as aliases wrapping new logic so old solutions still compile but tests check new single-header behavior and new semantics.
- Semantic inversions (prior-violating) as listed above make OTel recall actively wrong.

**Fix v2 (this hardening)**:
- Minimal skeleton: no free MemoryExporter deep copy, no free MarshalTrace/UnmarshalTrace, no free SimpleProcessor, no free MetricsProvider, no free Logger — all panic stubs, forcing full implementation.
- Defensive-copy requirements: WithLabels copy map, WithBuckets copy slice, WithAttributes handling duplicate and empty keys, ContextWithTrace copy TraceContext, Context() returns copy, GetSpans deep copy slice+Attributes+Events+EventAttrs, Collect deep copy Labels and Buckets.
- Nil handling: Start(nil, ...), ContextWithTrace(nil, ...), TraceFromContext(nil), MarshalTrace nil carrier/ctx no panic, WithOutput nil fallback, WithSampler nil no-op, etc.
- Precise limits: attribute distinct 128 with duplicate not counting and overwrite allowed after limit, empty key ignored, truncate exactly 1024/256 boundaries, event timestamp between Start/End, histogram dedup sorted.
- Gauge NaN/Inf ignore for Set/Add.
- RatioSampler override precedence over invalid TraceID, boundary exact < not <=, case-insensitive hex.
- ParentAware nil root fallback Always, Description contains root desc + Parent.
- BatchProcessor alias last wins (WithBatchSize vs WithMaxBatchSize), non-positive fallback to defaults, QueueLen excludes batch never exceeds, DroppedCount distinct counting avoiding double count, truncation interaction before key, shutdown idempotent sync.Once, concurrent Shutdown+ForceFlush no deadlock, ExportTimeout via select, exporter error continues, ordering preserved, block-and-drain many concurrent.
- Metrics cardinality droppedKeys tracking to avoid double counting same dropped label.

## Description

This task asks to implement a production-grade observability library in Go for a ride-hailing system handling 10k+ req/sec. It tests three coupled domains: distributed tracing with custom single-header propagation `x-ride-trace` (not W3C 4-header), metrics with cardinality limiting and defensive-copy semantics, and structured JSON logging with trace correlation. Naive approaches fail because (a) OTel recall is punished — ratio sampler uses last 8 hex vs OTel first 16, error/critical override precedence, parent AND logic, batch evict-oldest vs drop-newest; (b) defensive-copy and truncation-before-key reuse require precise map/slice copying; (c) concurrency requires shared mutex for logger and snapshot export for spans, otherwise `go run -race` fails.

## Anti-Cheating Analysis

- **Hardcoded outputs:** Tests generate ephemeral Go modules via `go run` importing `/app` and assert behavior via runtime panics, not static expected.txt files. No hardcoded output file to copy.
- **Overfitting to visible tests:** 281 collected step1 tests cover many combinatorial edge cases (case-insensitive, whitespace, truncation collision, concurrent create same labelset). Overfitting to a subset still fails others. No visible answer in test files — only Go code that must be executed.
- **Modifying test files:** Tests are mounted at `/tests/` at runtime (TBench harness mounts `tests/` directory). The container's task directory is read-only for tests? Even if agent edits `/app/observability`, it cannot modify `/tests/` to bypass. Solve.sh does not modify tests.
- **Bypassing intended path:** Dockerfile provides only minimal panic stubs for MemoryExporter, SimpleProcessor, MarshalTrace/UnmarshalTrace, MetricsProvider, Logger. No pre-solved files to diff. Agent must implement all public symbols from scratch; merely copying skeleton fails.
