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
    DecisionRecordOnly // optional: record but not sample? For this task treat as Drop for export, but keep IsRecording false? Define.
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

- **AlwaysOn**: always RecordAndSample.
- **AlwaysOff**: always Drop.
- **TraceIDRatioBased**: samplers deterministically decision based on TraceID. If fraction <=0 drop all, >=1 sample all. For 0<frac<1: compute hash of TraceID (must be 32 hex). Example algorithm: parse first 8 bytes of TraceID as uint64? Or use fnv or sha? Requirement for tests: we will provide deterministic test: sampling 10k random traceIDs with ratio 0.1 should result in sampled ratio within 0.08-0.12 tolerance (if using random TraceIDs, probabilistic). OR more deterministic: compute `value = hexToUint64(traceID[:16]) / (2^64 max)` and sample if < fraction. Must be consistent for same TraceID. Implement something that yields uniform.
  - Invalid TraceID -> treat as Drop? Or fallback to random? For simplicity if TraceID cannot be parsed, sample if fraction >0.5? But tests will use valid IDs.
- **ParentBased**: 
  - If HasParent false: delegate to root sampler.
  - If HasParent true and ParentContext.Sampled true: always RecordAndSample regardless of root.
  - If HasParent true and ParentContext.Sampled false: always Drop.
  - Description() should mention parent and root description.

### Tracer integration

Add new TracerOption:

```go
func WithSampler(s Sampler) TracerOption
```

- If no sampler set, default is AlwaysOn (preserves Step1).
- On Start: create SamplingParameters from traceID, span name, kind, parent. Call sampler.ShouldSample. Set SpanContext.Sampled accordingly (and TraceFlags).
- If DecisionDrop: span IsRecording() = false, attributes/events should be no-op? Spec: when not sampled, Span should not record; AddAttribute etc noop; End still calls processor? No — per OTEL, even non-recording spans should not be exported. For this task: if Drop, `IsRecording()` false, and span should NOT be exported (processor OnEnd should NOT export). Implement.
- If RecordAndSample: IsRecording true, export.
- ParentBased: tracer must use parent's Sampled flag. Provide helper to create root spans vs child.

Also:

- TraceID generation remains same.
- If parent exists and ParentBased with sampled parent, child TraceID inherits, and Sampled=true regardless of root ratio.
- Context propagation should preserve Sampled flag.

## 2. BatchSpanProcessor (large-scale export)

Step1 SimpleSpanProcessor exports synchronously; under 10k req/sec it blocks request path.

Implement:

```go
type BatchSpanProcessorOption func(*batchConfig)

func WithBatchSize(n int) BatchSpanProcessorOption // max spans per batch export, default 512
func WithQueueSize(n int) BatchSpanProcessorOption // max queue size, default 2048
func WithBatchTimeout(d time.Duration) BatchSpanProcessorOption // max time to wait before exporting incomplete batch, default 5s
func WithExportTimeout(d time.Duration) BatchSpanProcessorOption // timeout per export, default 30s
func WithMaxExportBatchSize(n int) BatchSpanProcessorOption // alias or max batch, default 512

func NewBatchSpanProcessor(exporter SpanExporter, opts ...BatchSpanProcessorOption) SpanProcessor
```

Spec:

- Async: maintains internal queue (channel or slice protected). OnEnd enqueues ReadableSpan. If queue full, **drop** the span (do not block caller) and increment dropped counter.
- Background goroutine: collects spans up to BatchSize or BatchTimeout elapsed, then calls exporter.ExportSpans.
- Must be concurrency-safe: many goroutines calling OnEnd.
- Must handle backpressure: non-blocking enqueue. Tests will check that under heavy load with small queue, calling goroutines are not blocked for > few ms.
- Shutdown(ctx) must: stop accepting new spans (or drain), flush remaining queue (export all pending batches), wait for in-flight exports, respect ctx timeout, return nil if success or ctx error. After Shutdown, OnEnd should drop (or no-op).
- ForceFlush(ctx) blocks until all currently queued spans are exported or ctx timeout.
- Methods to expose for testing (optional but helpful):

```go
func (b *BatchSpanProcessor) DroppedCount() int
func (b *BatchSpanProcessor) QueueLen() int
```

- Export failure: if exporter.ExportSpans returns error, processor should log? For this task, retry logic not required, but must not crash, and must continue.
- Ordering: batches export in order enqueue, but no strict global ordering guarantee beyond per-processor.
- Resource: goroutine must exit on Shutdown, no leaks.

Implementation guidance:

- Use channel of size QueueSize for spans? Or ring buffer. Channel is simplest. Use non-blocking send via select default to detect full.
- Use ticker for BatchTimeout.
- Batch export uses context with ExportTimeout.

Tests will:

- Produce 5000 spans quickly with Large batch size 256, queue 5000, ensure all exported eventually after ForceFlush.
- Produce with small queue 10, produce 100 spans, ensure dropped >0 and no deadlock.
- Concurrent producers 100 goroutines x 100 spans each, queue large enough, after shutdown all exported.
- Shutdown flushes.
- Backpressure: producer timing: enqueues should not block > 10ms even when queue full (since should drop).
- IsRecording false spans must not be queued.

## 3. Metrics Cardinality Limiting

In ride-hailing, labeling metrics by `ride_id`, `rider_id`, `driver_id` causes cardinality explosion. Implement limiting.

Extend MetricsProvider:

```go
type MetricsProviderOption func(*metricsConfig)
func WithMaxCardinality(n int) MetricsProviderOption // max distinct label sets per metric name, default unlimited
func WithCardinalityOverflowHandling(mode string) MetricsProviderOption // "drop" (default) or "aggregate" (merge into overflow)
```

- Per metric name, track number of distinct label set combinations seen. If limit N reached, new distinct label combo should be handled per mode:
  - "drop": return no-op instrument, not included in Collect, and increment internal dropped counter.
  - "aggregate": map new series to special overflow label set: e.g., add label `__overflow__=true` or aggregate under single overflow series. For simplicity if mode aggregate, treat all excess as one overflow time series per metric (share same overflow counter). For counters, increments add to overflow; for gauge, set overwrites overflow; for histogram, observe into overflow histogram.
  - Tests will use "drop" mode primarily.
- Provide method to inspect dropped:

```go
type MetricsProvider interface {
    // existing methods...
    Counter(...) Counter
    Gauge(...) Gauge
    Histogram(...) Histogram
    Collect() []MetricFamily
    // new:
    DroppedSeriesCount() int // total number of label sets dropped due to cardinality limit
}
```

If provider has unlimited cardinality (0 or negative means unlimited), DroppedSeriesCount always 0.

- Thread safety: cardinality check must be concurrent-safe, with sharded lock or sync.RWMutex.
- Collect must not expose overflow internals incorrectly; if drop mode, overflow not in metrics; if aggregate mode, overflow series appears with label `__overflow__=true`.
- Behavior when same label set repeated after limit reached? Should reuse existing instrument if already exists, even if at limit. Only new distinct combos cause overflow/drop.

- Performance: metrics writes under high concurrency should be fast (< microsec avg). Tests will benchmark? We'll test correctness under concurrency (100 goroutines 1000 adds) similar to step1 but with limiter enabled and large N.

- Also implement per-provider attribute/lable value size limit? Not required but you may add: truncate label value longer than 256 chars.

## 4. Additional Large-Scale Hardening

### Resource Limits for Tracing

- Span attribute count limit: already 128 in step1. Ensure enforcement under concurrency.
- Span attribute value size: if string value > 1024 chars, truncate to 1024.
- Span event count limit: max 128 events per span; beyond drop.
- Total spans in memory via Batch processor queue should not OOM; enforce QueueSize.

### Logger Rate Limiting (optional but bonus)

Add option `WithRateLimit(rps int)`? Not required for pass but good.

### Graceful Degradation

- Exporters may be slow; Batch processor export timeout ensures not blocking forever.

## 5. API Additions Summary (must implement)

- Samplers: AlwaysOn, AlwaysOff, TraceIDRatio, ParentBased
- WithSampler TracerOption
- BatchSpanProcessorOptions + NewBatchSpanProcessor
- Batch processor DroppedCount, QueueLen (optional but tests use via interface? We'll test via behavior, but provide)
- MetricsProvider WithMaxCardinality, DroppedSeriesCount
- Ensure backward compat: NewTracer(service, ...) with no sampler still AlwaysOn; MetricsProvider without limit still unlimited; logger unchanged.

## 6. Ride Service Scale Simulation

`/app/ride/service.go` simulates high throughput — you may instrument it with your new observability but not required. Tests will not directly import ride service, only observability package.

However, we will provide a benchmark harness `trace_bench` that simulates 10k rides/sec using your library to ensure it scales.

## Verification

Step2 test harness will:

- Test samplers: AlwaysOn/off, Ratio 0/1/0.1/0.5 deterministic properties, ParentBased inheritance.
- Test parent propagation of sampled flag via Inject/Extract + ParentBased.
- Test BatchSpanProcessor: correctness, concurrency, drop, shutdown flush.
- Test combination: Tracer with RatioSampler + BatchProcessor — many spans, ensure sampling rate approx fraction, only sampled exported, no deadlock under concurrency.
- Test Metrics cardinality: limit 100, create 200 distinct label sets, ensure Collect returns <=100, DroppedCount >=100.
- Test Metrics concurrency with cardinality limit large (e.g., 10000) — 100 goroutines.
- Test Thread Safety with `go test -race` enabled (verifier runs with -race? We run standard but your code must be race-free).
- Test resource limits: attribute truncation, event limit.
- Ensure Step1 tests still pass (backward compat).

## Constraints

- Stdlib only.
- No global state leaks.
- Files must stay: tracing.go, metrics.go, logger.go. You may add sampling.go, batch.go, etc but ensure package observability.
- go vet must pass.
- Implement efficiently: no busy loops, no unbounded goroutine spawning per span.

Build for scale, but keep correctness.

## Deliverable

Finish implementation such that:

```
go test ./observability -run TestStep2 -v # plus our harness
```

passes. Verifier runs Python harness that builds multiple temporary Go modules importing your library, to validate.

Proceed.
