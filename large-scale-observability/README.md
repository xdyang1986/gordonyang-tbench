# Large-Scale Observability for Ride-Hailing (Uber-like) — Multi-Turn Go Task (Redesigned for Novelty)

## Overview
This is a multi-turn terminal-bench task simulating observability system design for a high-throughput ride-hailing platform like Uber.

**Format**: `terminal_bench_multi_turn` with 2 steps, `inherit_prior_session=true` on step2.

**Novelty redesign (Aug 2026)**: Original version was a near-verbatim clone of OpenTelemetry Go SDK (types `SamplingParameters`, `ReadableSpan`, `SpanProcessor`, `DecisionDrop`, `NewInMemoryExporter`, `Inject`/`Extract` etc). Models could recall OTel architecture for free; only concurrency defects discriminated. Redesigned contract intentionally **punishes OTel recall**:

- **Propagation**: Single header `x-ride-trace` = `traceID:spanID:parentID:1/0` (colon separated), not four keys `trace-id`/`span-id`/`parent-id`/`sampled`. Tests assert `Inject`/`MarshalTrace` writes only `x-ride-trace` and does NOT write `trace-id`.
- **RatioSampler deterministic algorithm**: Last 8 hex chars as uint32 / 2^32, not first 16 hex as uint64 / 2^64. Fixed TraceIDs where methods disagree: `0000000000000001ffffffffffffffff` (first 16 tiny => OTel Keep, our spec Drop) and opposite. A model recalling OTel fails.
- **Error/critical override**: RatioSampler must Keep if `Status == StatusError` or `Priority == "critical"` regardless of fraction. OTel ratio ignores status/priority and would Drop at 0.0; tests include ratio 0.0 + error => Keep.
- **ParentAware AND logic**: ParentAwareSampler requires **both** parent sampled AND root sampled to Keep. OTel ParentBased Keeps whenever parent sampled ignoring root. Test: root Never (0.0) + parent sampled true => must Drop (OTel would Keep).
- **BatchProcessor evict-oldest**: On full queue, evict oldest and keep newest (ride-hailing keeps latest rides). OTel drops newest. Test: queue size 2, enqueue 5, ForceFlush => exported are span-3, span-4 (newest), not span-0,1. OTel recall fails.
- **ForceFlush block-and-drain**: During ForceFlush, OnEnd must block, not drop. OTel non-blocking drop would increment DroppedCount during flush. Test fills queue 2, starts slow flush 200ms, concurrent enqueue during flush must not drop, DroppedCount stays 0.

Renaming alone is cosmetic; these semantic inversions ensure OTel memorized behavior is **wrong**.

## Step 1: Core Observability & Design Quality (new names)

Implement foundational primitives in Go package `observability`:

- **Tracing**: `TraceContext` (alias `SpanContext` for compat), `FinishedSpan` (alias `ReadableSpan`), `Tracer`, `Span`, `MemoryExporter` (alias `InMemoryExporter`), `SimpleProcessor` (alias `SimpleSpanProcessor`), context helpers `ContextWithTrace`/`TraceFromContext` (aliases `ContextWithSpanContext`/`SpanContextFromContext`), propagation `MarshalTrace`/`UnmarshalTrace` (aliases `Inject`/`Extract`) using single header `x-ride-trace`.
- **Metrics**: `MetricsProvider` with `Counter`, `Gauge`, `Histogram` (custom buckets), thread-safe reuse, label validation, `Collect()` snapshot deep copy with non-nil Labels, provider isolation.
- **Logging**: JSON structured logger `Logger` with trace correlation (auto-injects `trace_id`, `span_id`), `With()` immutability, level filtering.

Design quality checks via concurrency tests (100 goroutines), idempotency, no global state sharing, stdlib-only.

**Expected API (see steps/1_step_one/instruction.md for exact signatures):**
- `NewTracer(serviceName, opts...)`, `NewMemoryExporter()`, `NewSimpleProcessor()`
- `ContextWithTrace`, `TraceFromContext`, `MarshalTrace`, `UnmarshalTrace` (single header)
- `NewMetricsProvider()`, `Counter`, `Gauge`, `Histogram`, `Collect()`
- `NewLogger(serviceName, opts...)`

## Step 2: Large-Scale Hardening (prior-violating)

Build on Step1 to handle 10k+ req/sec:

- **Sampling**: `Sampler` interface, `AlwaysSampler` (alias `AlwaysOn`), `NeverSampler` (alias `AlwaysOff`), `RatioSampler` (alias `TraceIDRatioBased`) with last-8-hex + error/critical override, `ParentAwareSampler` (alias `ParentBased`) with AND logic requiring parent AND root. `WithSampler` for Tracer, non-recording spans not exported. Description must mention parent and root.
- **BatchProcessor**: async queue with `WithBatchSize`, `WithQueueSize`, `WithBatchTimeout`, `WithExportTimeout`, **evict-oldest on full** (keep newest), **ForceFlush block-and-drain** (must block not drop during flush), BatchSize hard cap max per export, QueueSize enforced, DroppedCount/QueueLen required, non-positive options fallback defaults.
- **Metrics Cardinality Limiting**: `WithMaxCardinality(n)`, `WithCardinalityOverflowHandling("drop"|"aggregate")`, `DroppedSeriesCount()`, per-metric distinct label set limiting, overflow aggregation `__overflow__`.
- **Resource Limits**: attribute count 128, attribute value truncation exactly 1024 chars, event count 128, label value truncate 256.

High-throughput verification: concurrent producers, statistical sampling tolerance (including fixed TraceIDs where OTel fails), backpressure timing, cardinality overflow, shutdown flush, evict-oldest, block-and-drain.

## Layout
```
environment/Dockerfile       # Go 1.22 + pytest, skeleton go.mod + ride/service.go with new single-header propagation
steps/1_step_one/
  instruction.md             # detailed API spec with new names
  tests/test.sh + test_outputs.py   # black-box Go harness via pytest, now checks x-ride-trace single header
  solution/solve.sh          # reference implementation with new semantics
steps/2_step_two/
  instruction.md             # scale requirements with prior-violating semantics
  tests/test.sh + test_outputs.py # includes last8 vs first16, error override, parent AND, evict-oldest, block-and-drain
  solution/solve.sh          # implements prior-violating logic
task.toml
```

## Running
Verifier is pytest that generates ephemeral Go modules importing `ride-observability` from `/app` and runs `go run`.

Reference solutions:
- Step1: passes 75 tests (includes single-header propagation test that would fail OTel 4-key recall)
- Step2: passes 54 tests (includes 5 prior-violating tests that punish OTel recall: last8 vs first16, error/critical override, parent AND logic, evict-oldest, block-and-drain)

Test harness uses `set +e` around pytest to ensure reward.txt is always written.

## Difficulty
Hard, ~90min expert, 180min junior. Both turns discriminate:
- **Turn 1** — mild weak-model gate via `test_metrics_collect_copy` (Collect() must return deep copy) + single-header propagation (OTel recall writes wrong keys)
- **Turn 2** — main discriminator via `test_batch_batch_size_limit` (hard cap) + prior-violating tests: evict-oldest, block-and-drain, ratio last8, error override, parent AND

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

**Fix**:
- Renamed core types: `TraceContext` (new primary), `FinishedSpan` (new primary), `Exporter`/`Processor`, `MemoryExporter`, `SimpleProcessor`, `ContextWithTrace`/`TraceFromContext`, `MarshalTrace`/`UnmarshalTrace` (single header), `SamplingDecision` with `DecisionKeep`/`DecisionDrop`, `SamplingRequest` with `Priority`/`Status`, `NewAlwaysSampler`/`NewNeverSampler`/`NewRatioSampler`/`NewParentAwareSampler`, `NewBatchProcessor`. Old names kept as **aliases wrapping new logic** so old solutions still compile but tests check new single-header behavior and new semantics.
- Semantic inversions (prior-violating) as listed above make OTel recall actively wrong.
- Kept concurrency invariants (duplicate export, close-of-closed-channel) which were the parts that genuinely discriminated.

## Latest Run Analysis

**Commit `540f4e79` (v1.13) · Nest jobs 4576826–29 · all four jobs complete**

| Stage | Job | Result | Reward split |
|---|---|---|---|
| oracle | 4576826 | **3/3** | 3 × 1.00 |
| metacode (avocado) | 4576829 | **5/10** | 5 × 1.00, 4 × 0.50, 1 × 0.00 |
| agent (`claude-opus-4-8`) | 4576827 | **10/10** | 10 × 1.00 |
| codex (`gpt-5.5`) | 4576828 | **8/10** | 8 × 1.00, 1 × 0.50, 1 × 0.00 |

Contamination **LOW** · novelty risk **MEDIUM** · embedding dedup **0.491** · quality
dimensions depth 3 / realism 3 / originality 2. Status `draft` (no longer rejected).

Note: `validationStatus` still reads `pending` with the oracle row saying "No oracle trials
run" and provenance not run — aggregation lag; all four jobs finished with real results.

### TBR: 18/18, full marks

```
total_score = 18            all six axes = 3          quality_concern_titles = null
is_memorizable = False      is_trivial = False        is_realistic = True
tests_fail_before_solution = True    tests_pass_after_solution = True
known_problem_reference = "Conceptually similar to OpenTelemetry Go SDK,
                           but renamed types/wire format prevent copy-paste"
```

The reviewer verified the cascade end to end — M1 tests fail before / pass after M1 solution
(75 pass); M2 tests fail on M1 state (22 fail) and pass after M2 solution (54 pass); no
regression — plus `go run -race`, a deterministic statistically-robust sampler test, and no
answer leakage (`/tests`, `/solution`, `/review_materials` absent at agent runtime).

This is up from 17/18, then a `fail` with null details. The novelty checker had also been
erroring with `Prompt is too long`; the payload shrink in `058739c` fixed both. **The
redesign's effectiveness is now explicitly credited by the reviewer** — the renamed types
and single-header wire format are recorded as preventing copy-paste, and `is_memorizable`
is False.

### Failure spread

Six substantive failures across **seven distinct tests**, with no dominant one:

| Test | Step | Count |
|---|---|---|
| `test_batch_timeout_trigger` | 2 | 2 (avocado 1, codex 1) |
| `test_tracing_withparent_overrides` | 1 | 1 (codex) |
| `test_batch_processor_basic` | 2 | 1 (avocado) |
| `test_batch_order_preserved` | 2 | 1 (avocado) |
| `test_batch_droppedcount_not_for_non_recording` | 2 | 1 (avocado) |
| `test_batch_forceflush_empty_queue` | 2 | 1 (avocado) |

Plus one avocado collapse at 8/75 (164 s — never built). Opus contributed zero failures.

This spread is the important property: no single assertion is load-bearing, so an
under-specified test could not by itself be driving the calibration. Step 1 now also
contributes signal (one substantive failure) rather than the 0 it contributed at every
earlier commit.

### Reading

Fair difficulty, clean checkers, best quality dimensions in the repo. The one weakness is
**opus at 10/10** — with codex at 8/10 and avocado at 5/10 there is still a real spread, but
the top of the range is saturated. Difficulty, if it needs raising, should come from the
batch-processor semantics family (timeout trigger, ordering, dropped-count accounting,
forceflush edge cases), which is where every genuine failure already lands.

## Recent Enhancements
- **Novelty redesign**: single-header propagation, last-8-hex ratio + error override, parent AND logic, evict-oldest batch, block-and-drain flush. Old OTel identifiers kept as aliases for backward compat but primary API is domain-specific.
- **test_metrics_collect_copy**: deep copy check with non-nil Labels (mild gate)
- **test_batch_batch_size_limit**: hard cap (main discriminator)
- **test.sh set -e**: removed, uses set +e around pytest so reward.txt always written
- **Environment Dockerfile**: AlwaysSampler implemented for Step1 baseline, propagation helpers fully implemented for single-header
