# Large-Scale Observability — Step 2: Scale, Sampling, Backpressure & Cardinality (Redesigned — Prior-Violating)

Step1 built core tracing with single-header `x-ride-trace`. Now platform handles 10k+ req/sec. You must handle sampling and batch export with semantics that **punish OTel recall**.

Step2 inherits Step1 files.

## Goals
1. Sampling with domain-specific overrides and prior-violating parent logic
2. Batch processor with evict-oldest + block-and-drain ForceFlush
3. Metrics cardinality limiting
4. Resource safety under concurrency
5. Keep Step1 backward compatible

## 1. Sampling — prior-violating semantics

### Types (renamed to break exact OTel identifiers, with aliases)

```go
type SamplingDecision int
const (
    DecisionDrop SamplingDecision = iota // 0
    DecisionKeep                         // 1, was DecisionRecordAndSample
    DecisionRecordOnly                   // treated as Drop for this task
)

type SamplingRequest struct {
    TraceID    string
    SpanName   string
    Kind       SpanKind
    Parent     TraceContext
    HasParent  bool
    Attributes []Attribute
    Status     SpanStatus
    Priority   string // "critical" forces sampling regardless of ratio
}

// alias for backward compat
type SamplingParameters = SamplingRequest

type Sampler interface {
    ShouldSample(p SamplingRequest) SamplingDecision
    Description() string
}
```

Provide new constructors and OTel-named aliases:

```go
func NewAlwaysSampler() Sampler
func NewAlwaysOnSampler() Sampler // alias => NewAlwaysSampler

func NewNeverSampler() Sampler
func NewAlwaysOffSampler() Sampler // alias => NewNeverSampler

func NewRatioSampler(fraction float64) Sampler
func NewTraceIDRatioSampler(fraction float64) Sampler // alias => NewRatioSampler

func NewParentAwareSampler(root Sampler) Sampler
func NewParentBasedSampler(root Sampler) Sampler // alias => NewParentAwareSampler
```

Also aliases for decisions: `DecisionDrop`, `DecisionRecordAndSample` (=> DecisionKeep).

#### Behavior — must differ from OTel to punish recall

- **Always**: always `DecisionKeep`. Description non-empty e.g. "AlwaysSampler".
- **Never**: always `DecisionDrop`. Description non-empty.
- **RatioSampler** (the main prior-violating point):
  - If `fraction <= 0`: always `DecisionDrop` **except** when `Status == StatusError` or `Priority == "critical"` => then `DecisionKeep` (error/critical override). Same for `fraction >=1`: always `DecisionKeep`.
  - For `0 < fraction < 1`: deterministically decide based on TraceID **but using last 8 hex chars as uint32**, not first 16 hex as uint64.
    - Algorithm must be: take last 8 hex characters of TraceID string, parse as hex uint32 (base 16). Compute `value / 2^32`. If < fraction, Keep else Drop. **BUT** if Status==Error or Priority=="critical", always Keep regardless of ratio value.
    - Properties: same TraceID always same decision, uniform over random TraceIDs (ratio 0.1 yields 0.05-0.15 over 10k, ratio 0.5 within 0.4-0.6).
    - Must be based solely on TraceID, Status, Priority, fraction — not span name/kind.
    - **Invalid TraceID handling**: Any TraceID not valid 32 hex char string must return `DecisionDrop` and must NOT panic — e.g. `""`, `"short"`, `"zzzz..."`, `"0102...g"` => Drop.
  - **Why this punishes OTel recall**: OTel Go SDK uses first 16 hex as uint64 / 2^64 and ignores status/priority. A model recalling OTel will (1) get wrong result for TraceIDs where first-16 vs last-8 disagree like `0000000000000001ffffffffffffffff` (OTel would Keep at 0.5, correct per new spec is Drop), and (2) will Drop error/critical rides at ratio 0.0 whereas correct must Keep. Tests include both.

- **ParentAwareSampler** (prior-violating vs OTel ParentBased):
  - If `HasParent == false`: delegate to root sampler.
  - If `HasParent == true` and `Parent.Sampled == false`: always `DecisionDrop` regardless of root.
  - If `HasParent == true` and `Parent.Sampled == true`: delegate to **root** sampler (so root must also Keep to keep). So Keep only if parent sampled **AND** root says Keep.
  - OTel ParentBased: if parent sampled true, always Keep ignoring root. That behavior is **wrong** for this task. Test: root Ratio 0.0 always Drop, parent sampled true, child should Drop per new spec, but OTel recall would Keep.
  - `Description()` must be non-empty and mention parent and root description (contain root's Description and word "Parent" or "parent").

### Tracer integration

Add new TracerOption:

```go
func WithSampler(s Sampler) TracerOption
```

- If no sampler, default AlwaysSampler (preserves Step1).
- On Start: create SamplingRequest from traceID, span name, kind, parent, attributes, status (unset initially), priority extracted from attributes? For simplicity, tracer should look for attribute `priority` string or `status`? Instead, we define that SamplingRequest.Priority is taken from span attributes if present? Simpler: tracer sets Priority from `SpanStartOption`? To avoid complexity, we define new SpanStartOption `WithPriority(string)` and `WithStatus` already via SetStatus later? But sampling at Start time only knows Status unset. So we define that sampler's Status and Priority fields are populated from request's attributes? Let's define:

  - SamplingRequest.Status = StatusUnset at Start time (since status set later), but tests for error override will directly call Sampler.ShouldSample with Status=StatusError, not via tracer. For tracer integration, we only need priority: we add `WithPriority` option? Instead, spec says SamplingRequest includes Priority string — we can populate from attribute with key `priority` if provided via WithAttributes.

  - For simplicity, tracer will set SamplingRequest.Priority = attribute value for key `priority` if exists, else empty. SamplingRequest.Status = StatusUnset at Start (since error override via tracer happens after status set? But we can also say tracer checks parent status? Simpler: To make error override work via tracer, we consider that if span has attribute `error=true`? No.

  - To keep tasks manageable, error override via tracer is not required — only via direct sampler calls (tests call ShouldSample with Status=Error). Tracer integration still respects sampler decision: if DecisionDrop or RecordOnly, span IsRecording=false, no-op, not exported.

- If DecisionDrop or RecordOnly: IsRecording false, AddAttribute/AddEvent/SetStatus no-op, End must not call processor OnEnd.

- TraceID generation same: reuse parent TraceID if parent exists else generate.

- Context propagation must preserve Sampled flag and ParentID via single-header.

## 2. Batch Processor — evict-oldest + block-and-drain

Step1 SimpleProcessor exports synchronously; under 10k req/sec blocks.

Implement:

```go
type BatchOption func(*batchConfig)

func WithBatchSize(n int) BatchOption // max spans per batch export, default 512, hard cap
func WithQueueSize(n int) BatchOption // max queue size, default 2048
func WithBatchTimeout(d time.Duration) BatchOption // max wait before exporting incomplete batch, default 5s
func WithExportTimeout(d time.Duration) BatchOption // timeout per export, default 30s
func WithMaxBatchSize(n int) BatchOption // alias for WithBatchSize
func WithMaxExportBatchSize(n int) BatchOption // alias

func NewBatchProcessor(exporter Exporter, opts ...BatchOption) Processor
func NewBatchSpanProcessor(exporter Exporter, opts ...BatchOption) Processor // alias
```

Spec:

- `WithMaxBatchSize` is alias for `WithBatchSize`: same underlying. Last option wins. Default 512.
- **Non-positive option values fallback to default**: `WithBatchSize(0)`, `WithQueueSize(0)`, etc => default used. Ensures zero options still exports.
- **Hard cap enforcement**: BatchSize is max per export. Exporter must never receive batch larger than BatchSize. Background must split larger pending sets into multiple exports in order.
- Async queue with **evict-oldest on full** (prior-violating vs OTel drop-newest):
  - `OnEnd` enqueues FinishedSpan. If queue not full, enqueue.
  - If queue full, **evict oldest** queued span (remove from front), increment dropped counter, and enqueue newest. So queue always keeps newest rides, dropping oldest. DroppedCount increments for each evicted oldest.
  - This differs from OTel which drops newest. Tests check that after enqueuing 5 spans into size-2 queue then ForceFlush, exported spans are last 2 (newest), not first 2. OTel recall would export first 2 and drop last 3.
- Background goroutine collects up to BatchSize or BatchTimeout elapsed, then calls ExportSpans.
- Concurrency-safe: many goroutines calling OnEnd.
- Backpressure: normally non-blocking enqueue (with evict-oldest), so calling goroutines must not be blocked > few ms. However, **ForceFlush block-and-drain**: During ForceFlush, OnEnd must **block**, not evict/drop, until queue has space or ctx timeout. So DroppedCount must NOT increase during ForceFlush even if queue was full before flush.
  - Test: queue size 2, batch timeout large, enqueue 2 (full), start ForceFlush with slow exporter sleeping 300ms, concurrently try to enqueue 1 more span — it should block (not drop) and eventually be exported, DroppedCount should stay same (not increase). OTel non-blocking would drop instantly and increment DroppedCount.
- `Shutdown(ctx)` must: stop accepting new spans, flush remaining queue (export all pending batches respecting BatchSize), wait for in-flight exports, respect ctx timeout, return nil or ctx error. After Shutdown, OnEnd should drop (no-op) and not panic.
- `ForceFlush(ctx)` blocks until all currently queued spans are exported or ctx timeout. Must export incomplete batches as well, not wait for BatchSize to fill. Must also block-and-drain as described.
- Required methods:
  ```go
  func (b *BatchProcessor) DroppedCount() int
  func (b *BatchProcessor) QueueLen() int
  ```
  Accessible via interface assertion from Processor. QueueLen counts waiting in queue excluding in-progress batch, never exceeds QueueSize.
- Export failure: if ExportSpans returns error, must not crash/panic, continue processing.
- Goroutine must exit on Shutdown, no leaks.
- ExportTimeout: each export should respect timeout via context with timeout, goroutine + select.

Tests will:
- Produce 5000 spans quickly with batch size 256, queue 5000, ensure all exported after ForceFlush (not evicted since queue large)
- Produce with small queue 2, produce 5 spans, ensure dropped/evicted behavior leads to last 2 exported, DroppedCount >=3 and no deadlock. This punishes OTel drop-newest.
- Concurrent producers 100 goros x 100 spans, queue large enough, after shutdown all exported.
- Shutdown flushes all pending respecting BatchSize.
- Backpressure: enqueues should not block >10ms normally.
- ForceFlush blocking: fill queue, start slow ForceFlush, concurrent enqueue should block not drop.
- IsRecording false spans must not be queued nor counted as dropped.
- Batch size limit enforced: no batch larger than configured size.
- Export timeout via goroutine select.

## 3. Metrics Cardinality Limiting (same as before)

Extend MetricsProvider:

```go
type MetricsProviderOption func(*metricsConfig)
func WithMaxCardinality(n int) MetricsProviderOption // max distinct label sets per metric name, default unlimited (0 or negative unlimited)
func WithCardinalityOverflowHandling(mode string) MetricsProviderOption // "drop" (default) or "aggregate"
```

- Per metric name, track distinct label sets. If limit N reached, new distinct combo:
  - "drop": return no-op, not in Collect, increment dropped counter.
  - "aggregate": map all excess to special overflow series with label `__overflow__="true"` shared per metric name.
- Provide:
  ```go
  DroppedSeriesCount() int
  ```
  - Unlimited => 0, aggregate mode => 0.
- Thread safety, reuse existing instrument if already exists even at limit, DroppedSeriesCount not increase on reuse.
- Mixed metric types same name => no-op to avoid type conflict.
- Label value truncate 256 already required.

## 4. Additional Large-Scale Hardening

### Resource Limits for Tracing
- Span attribute count max 128, ignore excess beyond 128, truncate string values >1024 to exactly 1024.
- Event count max  128.
- QueueSize enforced via evict-oldest, not OOM.
- Thread safety race-free.

### API Additions Summary (must implement new names + OTel aliases)
- Samplers: Always/Never/ Ratio (last-8-hex + error/critical override) / ParentAware (parent AND root)
- WithSampler TracerOption — default Always
- BatchProcessor with evict-oldest, ForceFlush block-and-drain, DroppedCount, QueueLen, hard cap BatchSize, non-positive fallback defaults
- MetricsProvider WithMaxCardinality, WithCardinalityOverflowHandling, DroppedSeriesCount
- Backward compat: NewTracer with no sampler still Always, MetricsProvider without limit unlimited, old names like NewAlwaysOnSampler, NewTraceIDRatioSampler, NewParentBasedSampler, NewBatchSpanProcessor, NewInMemoryExporter, SpanContext, ReadableSpan, Inject/Extract (single-header) provided as aliases wrapping new logic.
- Files must stay: tracing.go, metrics.go, logger.go. May add more.

## Verification
Step2 harness will:
- Test samplers: Always/Never, Ratio boundaries 0/1/0.1/0.5, deterministic last-8-hex, invalid TraceID => Drop, error/critical override => Keep even at ratio 0.0 (fails OTel recall), ParentAware inheritance requiring both parent and root (fails OTel recall), Description contains root.
- Test parent propagation via Marshal/Unmarshal single-header and ParentAware.
- Test Batch: evict-oldest (queue 2 enqueue 5 => last 2 exported, DroppedCount >=3), correctness, concurrency, batch size hard cap, shutdown flush, ForceFlush drains and blocks (not dropping during flush), ExportTimeout via goroutine select, backpressure non-blocking normally.
- Test combination: Tracer with RatioSampler + BatchProcessor — many spans, sampling rate approx fraction (excluding error override), only sampled exported.
- Test Metrics cardinality: limit 100, create 200 distinct, Collect <=100, DroppedSeriesCount >=100 in drop mode; aggregate mode valid.
- Thread safety, resource limits 1024, 128.

## Constraints
- Stdlib only, no global leaks, go vet/build pass.
- Implement efficiently.

Build for scale, but keep correctness with prior-violating semantics.
