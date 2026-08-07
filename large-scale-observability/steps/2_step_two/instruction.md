# Large-Scale Observability — Step 2: Scale, Sampling, Backpressure & Cardinality

You have completed Step 1 with core tracing, metrics and logging. Now the ride-hailing platform handles **10k+ req/sec across 100+ services**. Naively exporting every span, storing unlimited label cardinality, and synchronous exporting collapses.

**Step 2 inherits Step 1 files** (`/app/...`). Extend the observability package to handle large scale.

## Goals

1. **Sampling** to control volume
2. **BatchSpanProcessor** for async, efficient export with backpressure & shutdown
3. **Metrics Cardinality Limiting** to avoid explosion from high-cardinality labels (rider_id, driver_id, ride_id)
4. **Resource safety** under concurrency
5. Keep Step1 API backward compatible; existing tests must still pass.

## 1. Sampling

### Sampler interface

```go
type SamplingDecision int
const (
    DecisionDrop SamplingDecision = iota
    DecisionRecordAndSample
    DecisionRecordOnly // record but not sample — for this task treat as Drop (IsRecording=false, not exported)
)

type SamplingParameters struct {
    TraceID      string
    SpanName     string
    SpanKind     SpanKind
    ParentContext SpanContext // zero value if no parent
    HasParent    bool
    Attributes   []Attribute
}

type Sampler interface {
    ShouldSample(p SamplingParameters) SamplingDecision
    Description() string
}
```

Provided implementations you must implement:

```go
func NewAlwaysOnSampler() Sampler
func NewAlwaysOffSampler() Sampler
func NewTraceIDRatioSampler(fraction float64) Sampler // 0.0 to 1.0 inclusive
func NewParentBasedSampler(root Sampler) Sampler
```

Behavior:

- **AlwaysOn**: always `DecisionRecordAndSample`. `Description()` returns non-empty string, e.g. `"AlwaysOnSampler"`.
- **AlwaysOff**: always `DecisionDrop`. `Description()` returns non-empty string.
- **TraceIDRatioBased**:
  - If `fraction <= 0`: always `DecisionDrop`.
  - If `fraction >= 1`: always `DecisionRecordAndSample`.
  - For `0 < fraction < 1`: deterministically decide based on TraceID. Properties required:
    - Same TraceID always yields same decision.
    - Decision is uniformly distributed over random TraceIDs (e.g., ratio 0.1 yields sampled ratio within 0.05-0.15 over 10k random IDs; ratio 0.5 within 0.4-0.6).
    - Decision must be based solely on TraceID and fraction, not span name/kind.
  - **Invalid TraceID handling: Any TraceID that is not a valid 32 hex char string (empty, wrong length, or contains non-hex) MUST return `DecisionDrop` and MUST NOT panic** – e.g., `""`, `"short"`, `"zzzz..."`, `"0102...g"` must not panic and result in Drop.
- **ParentBased**:
  - If `HasParent == false`: delegate to root sampler.
  - If `HasParent == true` and `ParentContext.Sampled == true`: always `DecisionRecordAndSample` regardless of root.
  - If `HasParent == true` and `ParentContext.Sampled == false`: always `DecisionDrop`.
  - `Description()` must be non-empty and mention parent and root description (e.g. contain root's Description string and the word "Parent" or "parent").

### Tracer integration

Add new TracerOption:

```go
func WithSampler(s Sampler) TracerOption
```

- If no sampler set, default is `AlwaysOn` (preserves Step1 behavior).
- On `Start`: create `SamplingParameters` from traceID, span name, kind, parent. Call `sampler.ShouldSample`. Set `SpanContext.Sampled` and `TraceFlags` (01 if sampled) accordingly.
- If `DecisionDrop` OR `DecisionRecordOnly`: span `IsRecording()` = false, `AddAttribute` / `AddEvent` / `SetStatus` must be no-op, and span **must NOT be exported**. `End()` should not call processor `OnEnd()` in this case.
- If `DecisionRecordAndSample`: `IsRecording()` true, span is recorded and exported on `End()`.
- ParentBased: tracer must use parent's Sampled flag via `SpanContextFromContext` or explicit `WithParent`. Provide helper to create root spans vs child.
- TraceID generation remains same: if parent exists, reuse parent TraceID; else generate new.
- If parent exists and ParentBased with sampled parent, child TraceID inherits, and Sampled=true regardless of root ratio.
- Context propagation must preserve Sampled flag and ParentSpanID.

## 2. BatchSpanProcessor (large-scale export)

Step1 `SimpleSpanProcessor` exports synchronously; under 10k req/sec it blocks request path.

Implement:

```go
type BatchSpanProcessorOption func(*batchConfig)

func WithBatchSize(n int) BatchSpanProcessorOption // max spans per batch export, default 512, must be hard cap
func WithQueueSize(n int) BatchSpanProcessorOption // max queue size, default 2048
func WithBatchTimeout(d time.Duration) BatchSpanProcessorOption // max time to wait before exporting incomplete batch, default 5s
func WithExportTimeout(d time.Duration) BatchSpanProcessorOption // timeout per export, default 30s
func WithMaxExportBatchSize(n int) BatchSpanProcessorOption // alias for WithBatchSize, default 512

func NewBatchSpanProcessor(exporter SpanExporter, opts ...BatchSpanProcessorOption) SpanProcessor
```

Spec:

- `WithMaxExportBatchSize` is an **alias for `WithBatchSize`**: it sets the same underlying max batch size. If both are supplied, the last option wins. Default 512 for both.
- **Non-positive option values fall back to the default**: If any option is supplied with a non-positive value (e.g., `WithBatchSize(0)`, `WithQueueSize(0)`, `WithBatchTimeout(0)`, `WithExportTimeout(0)`), it must be treated as if not supplied and the default (512/2048/5s/30s) used. This ensures a processor constructed with all-zero options still exports.
- **Hard cap enforcement:** `BatchSize` is the maximum spans per batch. Exporter must never receive a batch larger than `BatchSize`. Your background goroutine must export at most `BatchSize` spans per call, splitting larger pending sets into multiple exports in order.
- Async: maintains internal queue (channel or slice protected). `OnEnd` enqueues `ReadableSpan`. If queue full, **drop** the span (do not block caller) and increment dropped counter.
- Background goroutine: collects spans up to `BatchSize` or `BatchTimeout` elapsed, then calls `exporter.ExportSpans`.
- Must be concurrency-safe: many goroutines calling `OnEnd`.
- Must handle backpressure: non-blocking enqueue. Under heavy load with small queue, calling goroutines must not be blocked for more than a few milliseconds. Tests measure fast enqueue timing.
- `Shutdown(ctx)` must: stop accepting new spans, flush remaining queue (export all pending batches respecting `BatchSize`), wait for in-flight exports, respect `ctx` timeout, return nil if success or `ctx` error. After `Shutdown`, `OnEnd` should drop (no-op) and not panic.
- `ForceFlush(ctx)` blocks until all currently queued spans are exported or `ctx` timeout. It must export incomplete batches as well, not wait for `BatchSize` to fill.
- **Required methods** (not optional) for observability:
  ```go
  func (b *BatchSpanProcessor) DroppedCount() int
  func (b *BatchSpanProcessor) QueueLen() int
  ```
  These must be implemented on the concrete `*BatchSpanProcessor` type and be accessible via interface assertion (e.g., processor assigned to `SpanProcessor` then asserted to `interface{ DroppedCount() int }`).
  - `QueueLen()` counts spans waiting in the queue, excluding any already moved into the in-progress batch, and must never exceed `QueueSize`.
  - `DroppedCount()` counts spans dropped due to full queue.
- Export failure: if `exporter.ExportSpans` returns error, processor must not crash/panic, and must continue processing.
- Ordering: batches export in order enqueued, but no strict global ordering guarantee beyond per-processor.
- Resource: goroutine must exit on `Shutdown`, no leaks.
- `ExportTimeout`: each export should respect timeout via context with timeout. Implement export with goroutine + select on context if exporter might be slow.

Implementation guidance:

- Use channel of size QueueSize for spans. Non-blocking send via `select { case queue <- span: default: drop }` to detect full.
- Use `time.Ticker` or `time.Timer` for BatchTimeout.
- Batch export uses context with ExportTimeout.

Tests will:

- Produce 5000 spans quickly with Large batch size 256, queue 5000, ensure all exported eventually after ForceFlush.
- Produce with small queue 10, produce 100 spans, ensure dropped >0 and no deadlock.
- Concurrent producers 100 goroutines x 100 spans each, queue large enough, after shutdown all exported.
- Shutdown flushes all pending.
- Backpressure: enqueues should not block > 10ms even when queue full (since should drop).
- `IsRecording() == false` spans must not be queued nor counted as dropped.
- Batch size limit enforced: no batch larger than configured size.
- Export timeout: slow exporter should not block processor forever; timeout via context select.

## 3. Metrics Cardinality Limiting

In ride-hailing, labeling metrics by `ride_id`, `rider_id`, `driver_id` causes cardinality explosion. Implement limiting.

Extend MetricsProvider:

```go
type MetricsProviderOption func(*metricsConfig)
func WithMaxCardinality(n int) MetricsProviderOption // max distinct label sets per metric name, default unlimited (0 or negative means unlimited)
func WithCardinalityOverflowHandling(mode string) MetricsProviderOption // "drop" (default) or "aggregate" (merge into overflow)
```

- Per metric name, track number of distinct label set combinations seen. If limit N reached, new distinct label combo handling per mode:
  - **"drop"**: return no-op instrument, not included in `Collect()`, and increment internal dropped counter.
  - **"aggregate"**: map all excess distinct combos to a special overflow time series per metric name with label `__overflow__="true"`. All excess series share same overflow counter. For counters, increments add to overflow; for gauge, set overwrites overflow; for histogram, observe into overflow histogram.
- Provide method:
  ```go
  type MetricsProvider interface {
      // existing methods...
      Counter(...) Counter
      Gauge(...) Gauge
      Histogram(...) Histogram
      Collect() []MetricFamily
      // new:
      DroppedSeriesCount() int // total number of label sets dropped due to cardinality limit in "drop" mode
  }
  ```
  - If provider has unlimited cardinality (0 or negative means unlimited), `DroppedSeriesCount` always 0.
  - In "aggregate" mode, `DroppedSeriesCount` returns 0 (since not dropped but aggregated).
- Thread safety: cardinality check must be concurrent-safe, e.g. `sync.RWMutex`.
- `Collect()` must not expose overflow internals incorrectly; if drop mode, overflow not in metrics; if aggregate mode, overflow series appears with label `__overflow__="true"` and its value reflects aggregated operations.
- Behavior when same label set repeated after limit reached must reuse existing instrument if already exists, even if at limit. Only genuinely new distinct combos cause overflow/drop. `DroppedSeriesCount` should not increase on reuse.
- Mixed metric types with same name: if a metric name already used as Counter, subsequent `Gauge()` or `Histogram()` with same name should return no-op to avoid type conflict.
- Performance: metrics writes under high concurrency should be fast. Tests will exercise correctness under concurrency (100 goroutines x 1000 adds) with limiter enabled and large N.
- Label value truncation: truncate label values longer than 256 chars to 256 (already required in Step1). Keep this.

## 4. Additional Large-Scale Hardening

### Resource Limits for Tracing

- Span attribute count limit: max 128 per span. If more than 128 added (via `WithAttributes` or `AddAttribute`), ignore excess beyond 128 (keep first 128). Enforce under concurrency.
- Span attribute value size: if string value > 1024 chars, truncate to exactly 1024 chars (keep first 1024).
- Span event count limit: max 128 events per span; beyond drop (keep first 128).
- Total spans in memory via Batch processor queue must not OOM; enforce `QueueSize` drops.

### Graceful Degradation

- Exporters may be slow; Batch processor export timeout ensures not blocking forever.
- No busy loops, no unbounded goroutine spawning per span.

## 5. API Additions Summary (must implement)

- Samplers: `AlwaysOn`, `AlwaysOff`, `TraceIDRatio`, `ParentBased` with `Description()` and `ShouldSample`
- `WithSampler` TracerOption — default AlwaysOn
- `BatchSpanProcessor` options + `NewBatchSpanProcessor` + required `DroppedCount()`, `QueueLen()`
- MetricsProvider `WithMaxCardinality`, `WithCardinalityOverflowHandling`, `DroppedSeriesCount()`
- Ensure backward compat: `NewTracer(service, ...)` with no sampler still AlwaysOn; MetricsProvider without limit still unlimited; logger unchanged.
- Files must stay: `tracing.go`, `metrics.go`, `logger.go`. You may add `sampling.go`, `batch.go`, etc. but ensure package `observability` and all public symbols exist.

## 6. Ride Service Scale Simulation (optional)

`/app/ride/service.go` simulates high throughput — you may instrument it with your new observability but not required. Tests import only `observability` package and do not depend on `trace_bench` harness. There is no external benchmark binary required for correctness; scale is validated via concurrent Go harnesses.

## Verification

Step2 test harness will:

- Test samplers: AlwaysOn/off, Ratio 0/1/0.1/0.5 deterministic properties, invalid TraceID => Drop (no panic), ParentBased inheritance, ParentBased Description contains root description.
- Test parent propagation of sampled flag via `Inject`/`Extract` + `ParentBased`.
- Test BatchSpanProcessor: correctness, concurrency, batch size hard cap, drop on full, shutdown flush, ForceFlush drains, ExportTimeout via goroutine select, backpressure non-blocking.
- Test combination: Tracer with RatioSampler + BatchProcessor — many spans, ensure sampling rate approx fraction, only sampled exported, no deadlock under concurrency.
- Test Metrics cardinality: limit 100, create 200 distinct label sets, ensure `Collect()` returns <=100, `DroppedSeriesCount() >=100` in drop mode; aggregate mode valid.
- Test Metrics concurrency with cardinality limit large (e.g., 10000).
- Thread Safety: your code must be race-free (`go test -race` style concurrent usage).
- Resource limits: attribute truncation to exactly 1024, event limit 128, attribute count limit 128.
- Ensure Step1 tests still pass (backward compat).

## Constraints

- Stdlib only.
- No global state leaks across tracers/providers.
- Files must stay: `tracing.go`, `metrics.go`, `logger.go`. You may add more files but ensure package `observability`.
- `go vet ./...` and `go build ./...` must pass.
- Implement efficiently: no busy loops, no unbounded goroutine spawning per span.

Build for scale, but keep correctness.
