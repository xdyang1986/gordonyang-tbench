# Large-Scale Observability for Ride-Hailing — Multi-Turn Task

This is a multi-turn terminal-bench task with 2 steps. You are building an observability library in Go for a ride-hailing platform like Uber.

## Overall Goal
Implement package `observability` in module `ride-observability` that provides:
- Distributed tracing with SpanContext propagation (TraceID 32 hex, SpanID 16 hex)
- Metrics (Counter, Gauge, Histogram) with thread-safety and cardinality limiting
- Structured JSON logging correlated with tracing

## Steps
- **Step 1 (1_step_one)**: Core observability & design quality — see `steps/1_step_one/instruction.md` for exact API. Must implement tracing (Tracer, Span, InMemoryExporter, SimpleSpanProcessor, Inject/Extract), metrics (Counter reuse, distinct labels, concurrency, Collect copy), logger (JSON, trace_id/span_id, With immutable, level filter). No external deps, thread-safe, go vet clean.
- **Step 2 (2_step_two)**: Large-scale hardening — see `steps/2_step_two/instruction.md`. Adds sampling (AlwaysOn, AlwaysOff, TraceIDRatioBased deterministic on first 16 hex chars, ParentBased respecting parent Sampled), BatchSpanProcessor (async queue, drop on full/backpressure, BatchSize, QueueSize, BatchTimeout triggers export without ForceFlush, ExportTimeout via goroutine select, ForceFlush drains queue, Shutdown flushes and drops new, DroppedCount/QueueLen), metrics cardinality limiting per-name with drop/aggregate overflow and DroppedSeriesCount, resource limits (128 attrs/events, 1024 char truncation).

## Execution
Your code lives in `/app` (module `ride-observability`). Implement in `/app/observability/`. Tests are black-box Go harnesses importing your package and are injected at `/tests/` at verification time.

## Constraints
- Go 1.22, stdlib only
- Files must exist: `/app/observability/tracing.go`, `metrics.go`, `logger.go`
- Thread safety mandatory, no races (`go run -race`)
- `go vet ./...` and `go build ./...` must pass

Proceed to Step 1 — follow `steps/1_step_one/instruction.md` first, then Step 2 inherits your Step1 files.
