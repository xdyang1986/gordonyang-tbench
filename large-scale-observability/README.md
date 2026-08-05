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

Reference solution passes 23 tests step1, 21 tests step2.

## Difficulty
Hard, ~90min expert, 180min junior.
