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

Reference solution passes 53 tests step1, 60 tests step2.

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
