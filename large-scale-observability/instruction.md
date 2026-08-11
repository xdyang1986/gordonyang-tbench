# Large-Scale Observability for Ride-Hailing — Multi-Turn Task (Redesigned for Novelty)

This is a multi-turn terminal-bench task with 2 steps. You are building a ride-hailing observability library in Go (ride-observability) that handles 10k+ req/sec. Domain-specific design breaks generic OTel SDK recall.

> **Not an OTel clone**: Although tracing/batch/sampling are inspired by observability, this task defines its own contract that *punishes* verbatim OTel recall:
> - Propagation uses single header `x-ride-trace` (`{traceID}:{spanID}:{parentID}:{1/0}`), not four `trace-id`/`span-id`/`parent-id`/`sampled` keys.
> - Ratio sampler uses **last 8 hex chars** as uint32 / 2^32, not first 16 hex as uint64 / 2^64. Fixed TraceIDs are tested where the two methods disagree.
> - Ratio sampler always samples if `Status == StatusError` or `Priority == "critical"` regardless of fraction — OTel ratio ignores status/priority and would drop.
> - Parent-aware sampler requires **both parent sampled AND root sampled** to keep. OTel ParentBased samples whenever parent sampled, ignoring root. Tests include `root=0.0 + parent sampled=true => Drop` which OTel recall gets wrong.
> - Batch processor evicts **oldest** on full queue (keep newest ride), not newest. OTel drops newest. A queue-size-2 + 5-enqueue test checks exported set is last 2, not first 2.
> - ForceFlush is **block-and-drain**: during ForceFlush, OnEnd must block, not drop. OTel non-blocking drop would increment DroppedCount during flush.
> These differences are intentional; copying OTel verbatim fails.

## Overall Goal
Implement package `observability` in module `ride-observability` that provides:
- Distributed tracing with `TraceContext` propagation (TraceID 32 hex, SpanID 16 hex) via single-header `x-ride-trace`
- Metrics (Counter, Gauge, Histogram) with thread-safety and cardinality limiting
- Structured JSON logging correlated with tracing

## API Summary (New naming — breaks OTel exact identifiers)

### Core tracing (Step1) — see steps/1_step_one/instruction.md
- Types: `TraceContext` (was SpanContext), `FinishedSpan` (was ReadableSpan), `Span`, `Exporter`, `Processor`, `Tracer`, `MemoryExporter` (was InMemoryExporter), `SimpleProcessor` (was SimpleSpanProcessor)
- Aliases: `NewInMemoryExporter` -> `NewMemoryExporter`, `SpanContext` alias, `ReadableSpan` alias kept for compatibility but tests use new names
- Context: `ContextWithTrace`, `TraceFromContext`, `MarshalTrace`, `UnmarshalTrace` — single header `x-ride-trace` format `traceID:spanID:parentID:1`  (parentID may be empty). Old names `ContextWithSpanContext`, `SpanContextFromContext`, `Inject`, `Extract` are aliases wrapping new logic (single header).
- Status: `StatusUnset`, `StatusOK`, `StatusError` ; Kind: `KindInternal`, `KindServer`, `KindClient`
- Tracing: `NewTracer(serviceName, opts...)`, `NewMemoryExporter()`, `NewSimpleProcessor()`
- Metrics: `NewMetricsProvider()`, `Counter`, `Gauge`, `Histogram`, `Collect()` deep copy non-nil Labels, label value truncate 256
- Logger: JSON, `trace_id`/`span_id`, `With` immutable, level filter

### Scale hardening (Step2) — see steps/2_step_two/instruction.md
- Sampling: `SamplingDecision` (`DecisionDrop`, `DecisionKeep`, `DecisionRecordOnly`), `SamplingRequest` (includes `StatusCode`, `Priority`), `Sampler` interface
- Samplers: `NewAlwaysSampler` (alias `NewAlwaysOnSampler`), `NewNeverSampler` (alias `NewAlwaysOffSampler`), `NewRatioSampler` (alias `NewTraceIDRatioSampler`) with **last-8-hex** + error/critical override, `NewParentAwareSampler` (alias `NewParentBasedSampler`) with **parent AND root must both keep**
- Batch: `NewBatchProcessor` (alias `NewBatchSpanProcessor`) with `WithBatchSize`, `WithQueueSize`, `WithBatchTimeout`, `WithExportTimeout`, `WithMaxBatchSize` alias, **evict-oldest on full**, **ForceFlush block-and-drain**, DroppedCount/QueueLen required, QueueLen excludes in-progress batch, never exceeds QueueSize, non-positive options fallback defaults, BatchSize hard cap per export
- Metrics cardinality: per-name limiting with drop/aggregate overflow `__overflow__`, `DroppedSeriesCount`
- Resource limits: 128 attrs/events, attribute value truncate exactly 1024, label value truncate 256

## Execution
Code lives in `/app` (module `ride-observability`). Implement in `/app/observability/`. Tests are black-box Go harnesses importing your package at `/tests/`.

## Constraints
- Go 1.22, stdlib only
- Files must exist: `/app/observability/tracing.go`, `metrics.go`, `logger.go`
- Thread safety mandatory, no races
- `go vet ./...` and `go build ./...` must pass

Proceed Step1 then Step2 inherits.
