# Large-Scale Observability for Ride-Hailing (Uber-like) — Multi-Turn Go Task

## Overview
This is a multi-turn terminal-bench task simulating observability system design for a high-throughput ride-hailing platform like Uber.

**Format**: `terminal_bench_multi_turn` with 2 steps, `inherit_prior_session=true` on step2.

## Step 1: Core Observability & Design Quality
Implement foundational observability primitives in Go package `observability`:

- **Tracing**: `Tracer`, `Span`, `SpanContext` with 32-char TraceID, 16-char SpanID, parent inheritance, `Inject`/`Extract` propagation via `map[string]string`, `InMemoryExporter`, `SimpleSpanProcessor`.
- **Metrics**: `MetricsProvider` with `Counter`, `Gauge`, `Histogram` (custom buckets), thread-safe reuse, label validation, `Collect()` snapshot, provider isolation.
- **Logging**: JSON structured logger `Logger` with trace correlation (auto-injects `trace_id`, `span_id`), `With()` immutability, level filtering.

Design quality checks enforced via concurrency tests (100 goroutines), idempotency, no global state sharing, stdlib-only.

**Expected API** (see `steps/1_step_one/instruction.md` for exact signatures):
- `NewTracer(serviceName, opts...)`, `NewInMemoryExporter()`, `NewSimpleSpanProcessor()`
- `ContextWithSpanContext`, `SpanContextFromContext`, `Inject`, `Extract`
- `NewMetricsProvider()`, `Counter`, `Gauge`, `Histogram`, `Collect()`
- `NewLogger(serviceName, opts...)`

## Step 2: Large-Scale Hardening
Build on Step1 to handle 10k+ req/sec:

- **Sampling**: `Sampler` interface, `AlwaysOn`, `AlwaysOff`, `TraceIDRatioBased` (deterministic hash of TraceID), `ParentBased` (respects parent Sampled flag). `WithSampler` for Tracer, non-recording spans not exported.
- **BatchSpanProcessor**: async queue with `WithBatchSize`, `WithQueueSize`, `WithBatchTimeout`, `WithExportTimeout`, non-blocking enqueue (drop on full), `ForceFlush`, `Shutdown`, `DroppedCount()`.
- **Metrics Cardinality Limiting**: `WithMaxCardinality(n)`, `WithCardinalityOverflowHandling("drop"|"aggregate")`, `DroppedSeriesCount()`, per-metric distinct label set limiting, overflow aggregation.
- **Resource Limits**: attribute count 128, attribute value truncation 1024 chars, event count 128, graceful degradation.

High-throughput verification: concurrent producers, statistical sampling tolerance, backpressure timing, cardinality overflow, shutdown flush.

## Layout
```
environment/Dockerfile       # Go 1.22 + pytest, skeleton go.mod + ride/service.go
steps/1_step_one/
  instruction.md             # detailed API spec for core observability
  tests/test.sh + test_outputs.py   # black-box Go harness via pytest
  solution/solve.sh          # reference implementation
steps/2_step_two/
  instruction.md             # scale requirements
  tests/test.sh + test_outputs.py
  solution/solve.sh
task.toml
```

## Running
Verifier is pytest that generates ephemeral Go modules importing `ride-observability` from `/app` and runs `go run`.

Reference solution passes 53 tests step1, 63 tests step2. Test harness now uses `set +e` around pytest to ensure reward.txt is always written (previous `set -e` caused voided trials).

## Difficulty
Hard, ~90min expert, 180min junior.

## Latest Run Analysis

Latest online validation — commit `e899930b` ("Clarify spec for 3 under-specified Turn1 tests that even Opus fails"). Well-calibrated: both turns carry signal, oracle solvable, weak model is the low gate.

| Gate | Model | Full pass | Turn 1 | Turn 2 (of T1 passers) | Mean reward |
|------|-------|-----------|--------|------------------------|-------------|
| Oracle | oracle | 3/3 (100%) | 3/3 | 3/3 | 1.00 |
| Codex | gpt-5.5 | 7/10 (70%) | 8/10 | 7/8 | 0.75 |
| Agent | claude-opus-4-8 | 4/10 (40%) | 10/10 | 4/10 | 0.70 |
| Metacode | meta/avocado-5.14-code | 2/10 (20%) | 7/10 | 2/7 | 0.45 |

**Both turns discriminate:**
- **Turn 1** — mild weak-model gate via `test_metrics_collect_copy` (Collect() must return a copy, not an alias): avocado 7/10, codex 8/10, Opus 10/10. Real failures (52/53), not flakes.
- **Turn 2** — main discriminator via `test_batch_batch_size_limit` (BatchSpanProcessor must chunk exports to ≤ `WithBatchSize`, not flush the whole queue): catches even Opus (4/10). Failures are near-misses (59/60).

**Calibration:** spread is healthy — oracle 3/3 confirms solvability, codex 7 / Opus 4 / avocado 2 are all non-zero and <100%, with the weak model as the low gate. Notable inversion: Opus (4/10) scores below Codex (7/10) because Opus trips Turn 2's batch-size chunking more often.

Note: the "clarify spec" commit resolved a prior too-hard state (commit `88e3063a`: codex 0/10, Opus 0/10, avocado 0/9) by clarifying three under-specified Turn-1 tests (`test_metrics_label_truncate`, `test_tracing_event_limit`, `test_metrics_collect_copy`); two of the three ceased to be walls, leaving `collect_copy` as a mild Turn-1 gate.

## Recent Enhancements (P0 fixes)

- **test_metrics_collect_copy** (P0): fixed at HEAD by 58f81ef — now uses `WithLabels({"env":"prod"})` and explicit nil-checks with readable panic messages instead of mutating nil map (which caused 6 voided trials: 4 codex, 1 avocado, 1 opus). Backing those out: opus 6/9 (67%), gpt 4/6 (67%), avocado 3/9 (33%).
- **test_batch_droppedcount_and_queuelen**: fixed to be tolerant of concrete return type — now does `var sp SpanProcessor = proc` then asserts `DroppedCount()`/`QueueLen()` via interface, compiling for both `*BatchSpanProcessor` and `SpanProcessor` returns. Previously failed at compile: `proc (variable of type *observability.BatchSpanProcessor) is not an interface`.
- **test_sampler_invalid_traceid**: spec contradiction resolved — invalid TraceID now definitively => `Drop` (not "sample if fraction>0.5" and not "tests will use valid IDs"). Instruction :56 clarified; test now requires Drop for all invalid forms and no panic.
- **test_batch_batch_size_limit**: kept as main discriminator — hard cap must be enforced (max batch <= WithBatchSize). Do not weaken.
- **test.sh set -e**: removed `set -e` at top; now uses `set +e` around pytest so reward.txt is written even on failure. Fixed also in `location-accuracy`.
- **Step2 instruction.md**: editing pass removed open authoring notes (:24 RecordOnly defined as Drop, :55 deterministic algorithm fixed to first 16 hex as uint64 / 2^64, :73 Drop => not exported, :92/:96 WithMaxExportBatchSize defined as alias, :109 DroppedCount/QueueLen required not optional, :184/ :197 bonus/hedging removed, :205 trace_bench phantom removed, :217/ :236 TestStep2/race question clarified). Oracle hints `test_metrics_*` removed from Step1 spec.
- **Environment Dockerfile**: AlwaysOn sampler now implemented (not panicking) for Step1 baseline.
