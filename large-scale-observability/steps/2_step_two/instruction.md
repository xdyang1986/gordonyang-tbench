# Large-Scale Observability — Step 2: Scale, Sampling, Backpressure & Cardinality

Step2 inherits Step1 files. Extend the observability package to handle 10k+ req/sec.

## Goals
1. Sampling to control volume
2. Batch processor for async export with queue management and shutdown
3. Metrics cardinality limiting
4. Resource safety under concurrency
5. Keep Step1 backward compatible

## 1. Sampling

### Types

```go
type SamplingDecision int
const (
    DecisionDrop SamplingDecision = iota
    DecisionKeep
    DecisionRecordOnly
)

type SamplingRequest struct {
    TraceID    string
    SpanName   string
    Kind       SpanKind
    Parent     TraceContext
    HasParent  bool
    Attributes []Attribute
    Status     SpanStatus
    Priority   string
}
type SamplingParameters = SamplingRequest

type Sampler interface {
    ShouldSample(p SamplingRequest) SamplingDecision
    Description() string
}
```

Constructors (new names with old aliases for build compatibility):

```go
func NewAlwaysSampler() Sampler
func NewAlwaysOnSampler() Sampler

func NewNeverSampler() Sampler
func NewAlwaysOffSampler() Sampler

func NewRatioSampler(fraction float64) Sampler
func NewTraceIDRatioSampler(fraction float64) Sampler

func NewParentAwareSampler(root Sampler) Sampler
func NewParentBasedSampler(root Sampler) Sampler
```

Also provide `DecisionRecordAndSample` alias for `DecisionKeep`.

#### Behavior

- **AlwaysSampler**: always `DecisionKeep`. Description non-empty.
- **NeverSampler**: always `DecisionDrop`. Description non-empty.
- **RatioSampler**:
  - If `fraction <= 0`: always `DecisionDrop` except when `Status == StatusError` or `Priority == "critical"` then `DecisionKeep`. If `fraction >= 1`: always `DecisionKeep`.
  - For `0 < fraction < 1`: deterministically decide based on TraceID:
    - Algorithm: take last 8 hex characters of TraceID string, parse as hex uint32 (base 16). Compute `value / 2^32`. If < fraction, Keep else Drop.
    - If Status == StatusError or Priority == "critical", always Keep regardless of fraction and TraceID.
    - Properties: same TraceID always same decision. Uniformly distributed over random TraceIDs (ratio 0.1 yields sampled ratio within 0.05-0.15 over 10k random IDs; ratio 0.5 within 0.4-0.6).
    - Based solely on TraceID, Status, Priority, fraction, not span name/kind.
  - **Invalid TraceID handling:** Any TraceID not valid 32 hex char string must return `DecisionDrop` and must NOT panic.
- **ParentAwareSampler**:
  - If `HasParent == false`: delegate to root sampler.
  - If `HasParent == true` and `Parent.Sampled == false`: always `DecisionDrop` regardless of root.
  - If `HasParent == true` and `Parent.Sampled == true`: delegate to root sampler. Keep only if parent sampled and root says Keep.
  - `Description()` must be non-empty and mention parent and root description (contain root's Description and word "Parent" or "parent").

### Tracer integration

```go
func WithSampler(s Sampler) TracerOption
```

- If no sampler set, default AlwaysSampler (preserves Step1 behavior).
- On Start: create SamplingRequest from traceID, span name, kind, parent, attributes. Priority is taken from attribute with key `priority` if provided as string value; Status is StatusUnset at Start time.
- If `DecisionDrop` or `DecisionRecordOnly`: span IsRecording = false, AddAttribute/AddEvent/SetStatus no-op, and span must NOT be exported. End() should not call processor OnEnd().
- If `DecisionKeep`: IsRecording true, span recorded and exported on End().
- TraceID generation: if parent exists, reuse parent TraceID; else generate new. ParentID = parent SpanID.
- Context propagation must preserve Sampled flag and ParentID via single-header `x-ride-trace`.

## 2. Batch Processor

Implement:

```go
type BatchOption func(*batchConfig)

func WithBatchSize(n int) BatchOption
func WithQueueSize(n int) BatchOption
func WithBatchTimeout(d time.Duration) BatchOption
func WithExportTimeout(d time.Duration) BatchOption
func WithMaxBatchSize(n int) BatchOption
func WithMaxExportBatchSize(n int) BatchOption

func NewBatchProcessor(exporter Exporter, opts ...BatchOption) Processor
func NewBatchSpanProcessor(exporter Exporter, opts ...BatchOption) Processor
```

Spec:

- `WithMaxBatchSize` and `WithMaxExportBatchSize` are aliases for `WithBatchSize`: same underlying max batch size. Last option wins. Default 512.
- **Non-positive option values fall back to default**: If any option supplied with non-positive value (e.g., WithBatchSize(0), WithQueueSize(0), WithBatchTimeout(0), WithExportTimeout(0)), treat as not supplied and use default (512/2048/5s/30s). Processor constructed with all-zero options still exports.
- **Hard cap enforcement:** BatchSize is maximum spans per batch. Exporter must never receive batch larger than BatchSize. Background must export at most BatchSize spans per call, splitting larger pending sets into multiple exports in order.
- Async: maintains internal queue. `OnEnd` enqueues FinishedSpan.
  - If queue not full: enqueue.
  - If queue full: evict oldest queued span (remove from front), increment dropped counter, and enqueue newest. Queue keeps newest rides.
- Background goroutine collects spans up to BatchSize or BatchTimeout elapsed, then calls ExportSpans.
- Concurrency-safe: many goroutines calling OnEnd.
- Backpressure: normally non-blocking enqueue (with evict-oldest). Calling goroutines must not be blocked for more than a few milliseconds in normal mode.
- `Shutdown(ctx)` must: stop accepting new spans, flush remaining queue (export all pending batches respecting BatchSize), wait for in-flight exports, respect ctx timeout, return nil or ctx error. After Shutdown, OnEnd should drop (no-op) and not panic.
- `ForceFlush(ctx)` blocks until all currently queued spans are exported or ctx timeout. Must export incomplete batches as well, not wait for BatchSize to fill. During ForceFlush, OnEnd must block (not evict/drop) until queue has space or ctx timeout. DroppedCount must not increase during ForceFlush even if queue was full before flush.
- Required methods (not optional):
  ```go
  func (b *BatchProcessor) DroppedCount() int
  func (b *BatchProcessor) QueueLen() int
  ```
  Accessible via interface assertion from Processor.
  - QueueLen counts spans waiting in queue, excluding any already moved into in-progress batch, never exceeds QueueSize.
  - DroppedCount counts spans dropped/evicted due to full queue.
- Export failure: if ExportSpans returns error, must not crash/panic, continue processing.
- Ordering: batches export in order enqueued.
- Goroutine must exit on Shutdown, no leaks.
- ExportTimeout: each export should respect timeout via context with timeout. Implement export with goroutine + select on context.

Tests will:
- Produce 5000 spans quickly with queue 5000, ensure all exported after ForceFlush.
- Produce with small queue 2, produce 5 spans, ensure exported set is last 2 (newest) after evict-oldest, DroppedCount >=3, no deadlock.
- Concurrent producers 100 goroutines x 100 spans, queue large enough, after shutdown all exported.
- Shutdown flushes all pending respecting BatchSize.
- Backpressure: normal enqueues not blocking >10ms.
- ForceFlush blocking: fill queue, start slow ForceFlush, concurrent enqueue during flush must block not drop, DroppedCount unchanged.
- IsRecording false spans must not be queued nor counted as dropped.
- Batch size limit enforced.
- Export timeout via goroutine select.

## 3. Metrics Cardinality Limiting

Extend MetricsProvider:

```go
type MetricsProviderOption func(*metricsConfig)
func WithMaxCardinality(n int) MetricsProviderOption
func WithCardinalityOverflowHandling(mode string) MetricsProviderOption

type MetricsProvider interface {
    Counter(...) Counter
    Gauge(...) Gauge
    Histogram(...) Histogram
    Collect() []MetricFamily
    DroppedSeriesCount() int
}
```

- Per metric name, track distinct label sets. If limit N reached, new distinct combo:
  - "drop": return no-op, not included in Collect, increment dropped counter.
  - "aggregate": map all excess to special overflow time series with label `__overflow__="true"` shared per metric name.
- If unlimited cardinality (0 or negative), DroppedSeriesCount always 0. In aggregate mode, DroppedSeriesCount 0.
- Thread safety: cardinality check concurrent-safe.
- Collect must not expose overflow incorrectly; drop mode overflow not in metrics; aggregate mode overflow series appears with label `__overflow__="true"` and value reflects aggregated operations.
- Same label set repeated after limit reached must reuse existing instrument if already exists, even at limit. DroppedSeriesCount not increase on reuse.
- Mixed metric types with same name: if metric name already used as Counter, subsequent Gauge/Histogram with same name should return no-op to avoid type conflict.
- Label value truncation 256 already required.

## 4. Additional Hardening

### Resource Limits for Tracing
- Span attribute count limit: max 128 per span. If more than 128 added (via WithAttributes or AddAttribute), ignore excess beyond 128 (keep first 128).
- Span attribute value size: if string value >1024 chars, truncate to exactly 1024 chars (keep first 1024).
- Span event count limit: max 128 events per span; beyond drop (keep first 128).

### Graceful Degradation
- Exporters may be slow; Batch processor export timeout ensures not blocking forever.
- No busy loops, no unbounded goroutine spawning per span.

## 5. API Additions Summary
- Samplers: Always, Never, Ratio (last-8-hex + error/critical override), ParentAware (parent AND root) with Description and ShouldSample
- WithSampler TracerOption — default Always
- BatchProcessor options + NewBatchProcessor + required DroppedCount(), QueueLen()
- MetricsProvider WithMaxCardinality, WithCardinalityOverflowHandling, DroppedSeriesCount()
- Ensure backward compat: NewTracer with no sampler still Always; MetricsProvider without limit still unlimited.
- Files must stay: tracing.go, metrics.go, logger.go. May add more files but ensure package observability.
- go vet and go build must pass.

Build for scale, but keep correctness.
