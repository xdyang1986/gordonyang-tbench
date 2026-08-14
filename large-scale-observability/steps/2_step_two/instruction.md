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

#### Behavior — hardened with precise semantics

- **AlwaysSampler**: always `DecisionKeep`. Description non-empty, must not panic on zero-value SamplingRequest.
- **NeverSampler**: always `DecisionDrop`. Description non-empty.
- **RatioSampler** — prior-violating vs OTel, many edge cases:
  - If `fraction <= 0`: always `DecisionDrop` **except** when `Status == StatusError` or `Priority == "critical"` (case-insensitive? spec says exactly "critical" lower-case, but implementation should lower-case check for robustness) then `DecisionKeep`. This override still applies even if TraceID is invalid? Decision precedence: **error/critical override must be checked BEFORE invalid TraceID check**, so a request with StatusError and invalid TraceID must still Keep (error always sampled). If no override, invalid TraceID => Drop.
  - If `fraction >= 1`: always `DecisionKeep` (even if TraceID invalid).
  - For `0 < fraction < 1`: deterministically decide based on TraceID:
    - Algorithm: take **last 8 hex characters** of TraceID string (positions 24..31), parse as hex uint32 (base 16, case-insensitive). Compute `value / 2^32` where 2^32 = 4294967296.0. If value/2^32 < fraction, Keep else Drop. Use float64 division.
    - Must use last 8, not first 8 nor first 16. Tests include fixed TraceIDs where first-16 vs last-8 disagree: `0000000000000001ffffffffffffffff` (first 16 tiny => OTel Keep, our spec Drop) and opposite `ffffffffffffffff0000000000000000` (last 8 zero => Keep).
    - If Status == StatusError or Priority == "critical", always Keep regardless of fraction and TraceID (override checked before parsing).
    - Properties: same TraceID always same decision (deterministic). Uniformly distributed over random TraceIDs (ratio 0.1 yields sampled ratio within 0.05-0.15 over 10k random IDs; ratio 0.5 within 0.4-0.6; ratio 0.25 within 0.18-0.32; ratio 0.75 within 0.68-0.82). Statistical tolerance accounts for randomness.
    - Based solely on TraceID, Status, Priority, fraction, not span name/kind. Same TraceID with different SpanName/Kind/Attributes must give same decision (unless Status/Priority differs). Tests check that.
    - Case-insensitive hex: upper and lower case both valid.
  - **Invalid TraceID handling:** Any TraceID not valid 32 hex char string (wrong length, non-hex, empty) must return `DecisionDrop` **unless overridden by error/critical** and must NOT panic. Must not panic on empty string, nil? TraceID is string so empty case handled.
  - **Boundary**: value exactly at threshold? Use < fraction, not <=, so if value/2^32 == fraction, Drop. For fraction 0.5, value 0x80000000 (2147483648) / 2^32 = 0.5 => Drop.
  - **Description**: must be non-empty and contain fraction (e.g., "RatioSampler{0.5000}" or similar). Not strictly checked but should be non-empty.
- **ParentAwareSampler** — AND logic, harder than OTel ParentBased:
  - If `HasParent == false`: delegate to root sampler (no parent => root decides).
  - If `HasParent == true` and `Parent.Sampled == false`: always `DecisionDrop` regardless of root (even if root Always).
  - If `HasParent == true` and `Parent.Sampled == true`: delegate to root sampler. Keep only if **both** parent sampled AND root says Keep (AND logic). OTel ParentBased would Keep whenever parent sampled ignoring root; we require AND.
  - `Description()` must be non-empty and mention parent and root description: must contain root's Description as substring AND contain word "Parent" or "parent" (case-insensitive substring). Example "ParentAware{root=AlwaysSampler}" satisfies. Must handle root nil? Root nil should be treated as AlwaysSampler (per constructor fallback).
  - Must handle root that itself is ParentAware (nested) — delegate correctly.
  - Statistical: root ratio sampler 0.5, parent sampled true, over 5000 random IDs sampled ratio ~0.5 (0.35-0.65).

### Tracer integration — hardened

```go
func WithSampler(s Sampler) TracerOption
```

- If no sampler set, default AlwaysSampler (preserves Step1 behavior). WithSampler(nil) must be no-op, keep existing sampler (default Always).
- On Start: create SamplingRequest from traceID, span name, kind, parent, attributes. Priority is taken from attribute with key `priority` if provided as string value (case-sensitive key, value must be string "critical" to trigger override, but samplers should do case-insensitive check for robustness). Also check if attribute key "priority" exists with any case? Spec says exactly "priority" lower-case. Status is StatusUnset at Start time — error/critical override via attributes only (Priority) at Start time, Status remains Unset at Start, but after Start if span's Status later set to Error? Sampling decision already made at Start, so later SetStatus does not retroactively affect sampling (but RatioSampler's ShouldSample with Status==Error must Keep when called directly with that Status; tracer's Start uses StatusUnset, so error override via tracer must come via Priority attribute).
- If `DecisionDrop` or `DecisionRecordOnly`: span IsRecording = false, AddAttribute/AddEvent/SetStatus no-op, and span must NOT be exported. End() should not call processor OnEnd(). TraceContext with Sampled=false must still be stored in returned context for propagation (child will inherit TraceID and ParentID but Sampled false, so child also dropped via ParentAware AND logic).
- If `DecisionKeep`: IsRecording true, span recorded and exported on End(), Sampled true, Flags 1.
- TraceID generation: if parent exists (via ctx or WithParent), reuse parent TraceID; else generate new via IDGenerator. ParentID = parent SpanID (empty for root). HasParent true iff parent exists. Parent field in SamplingRequest must be copy of parent TraceContext (if present) with Sampled flag accurate.
- Context propagation must preserve Sampled flag and ParentID via single-header `x-ride-trace`. MarshalTrace must output sampled 1 if Keep else 0, even for dropped spans (TraceContext still propagated with Sampled false). Unmarshal must restore Sampled false.
- Invalid parent TraceID handling: If ctx contains TraceContext with invalid TraceID (e.g., custom ID generator returned "invalid"), tracer must not panic, must generate new TraceID? Spec says invalid TraceID handling in sampler returns Drop. So tracer's Start with invalid parent TraceID should still work: it will reuse invalid TraceID as TraceID? But sampler will Drop if ratio sampler. For Always sampler, it would Keep even with invalid TraceID? Always keeps regardless of validity. Should not panic on invalid parent IDs.
- SamplingRequest TraceID for sampler must be the newly determined TraceID (reused parent or new), not parent's SpanID.
- Tracer with BatchProcessor and sampler: non-recording spans must not be queued.

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

Spec — hardened with shutdown/ForceFlush concurrency and alias handling:

- `WithMaxBatchSize` and `WithMaxExportBatchSize` are aliases for `WithBatchSize`: same underlying max batch size. Last option wins regardless of alias type. Default BatchSize 512, QueueSize 2048, BatchTimeout 5s, ExportTimeout 30s. Example: `WithBatchSize(10), WithMaxBatchSize(20)` → effective 20; `WithMaxBatchSize(20), WithBatchSize(10)` → 10.
- **Non-positive option values fall back to default**: If any option supplied with non-positive value (e.g., WithBatchSize(0), WithQueueSize(0), WithBatchTimeout(0), WithExportTimeout(0), negative), treat as not supplied and use default (512/2048/5s/30s). Processor constructed with all-zero options still exports (tests NewBatchProcessor with all zeros must export 1 span).
- **Hard cap enforcement:** BatchSize is maximum spans per batch. Exporter must never receive batch larger than BatchSize, even during Shutdown flush and ForceFlush. Background must export at most BatchSize spans per call, splitting larger pending sets into multiple exports in order. Counting exporter test asserts maxBatch <= BatchSize, total == enqueued.
- Async: maintains internal queue (FIFO for export order, but evict-oldest on full). `OnEnd` enqueues FinishedSpan.
  - If queue not full: enqueue at tail.
  - If queue full: evict oldest queued span (remove from front index 0), increment dropped counter by 1, and enqueue newest at tail. Queue keeps newest rides (ride-hailing keeps latest). This is opposite of OTel which drops newest. Test: queue size 2, enqueue 5 spans named span-0..span-4, after ForceFlush exported are span-3, span-4 (newest) in order, not span-0,1. DroppedCount >=3. Order preserved: exported order must be enqueue order (3 before 4).
- Background goroutine collects spans up to BatchSize or BatchTimeout elapsed, then calls ExportSpans. Must not busy-loop (sleep a few ms when no work).
- Concurrency-safe: many goroutines calling OnEnd concurrently must not race, must not corrupt queue, must not exceed QueueSize. QueueLen never exceeds QueueSize even under 100 goroutines x 100 spans.
- Backpressure: normally non-blocking enqueue (with evict-oldest). Calling goroutines must not be blocked for more than a few milliseconds in normal mode. Only during ForceFlush may OnEnd block when queue full (block-and-drain).
- `Shutdown(ctx)` must: stop accepting new spans (set stopped flag), flush remaining queue + in-progress batch (export all pending batches respecting BatchSize hard cap, i.e., split into multiple exports of max BatchSize), wait for in-flight exports, respect ctx timeout (if ctx times out before flush complete, return ctx.Err() after timeout). After Shutdown, OnEnd should drop (no-op) and not panic, and DroppedCount must not increase for spans after shutdown. Further calls to Shutdown must be idempotent (second call returns nil quickly, not deadlock). Goroutine must exit on Shutdown, no leaks.
- `ForceFlush(ctx)` blocks until all currently queued spans + in-progress batch are exported or ctx timeout. Must export incomplete batches as well, not wait for BatchSize to fill. During ForceFlush, OnEnd must block (not evict/drop) until queue has space or ctx timeout. DroppedCount must not increase during ForceFlush even if queue was full before flush (block-and-drain semantics). ForceFlush on empty queue must return nil quickly, not error.
- **Shutdown vs ForceFlush concurrency**: If Shutdown is called concurrently with ForceFlush, Shutdown takes precedence: after Shutdown completes, further ForceFlush must return nil and not panic or deadlock, even if ForceFlush was blocked waiting for queue space. If ForceFlush was blocked and Shutdown closes the processor, ForceFlush must unblock and return nil or context error, not deadlock. Test starts slow flush 200ms, fills queue 2, starts ForceFlush in background, concurrent enqueue during flush must block not drop, DroppedCount stays 0.
- Required methods (must be accessible via type assertion from Processor, even if Processor is interface):
  ```go
  func (b *BatchProcessor) DroppedCount() int
  func (b *BatchProcessor) QueueLen() int
  ```
  - QueueLen counts spans waiting in queue, **excluding** any already moved into in-progress batch (batch being exported), never exceeds QueueSize, even under concurrent producers, never negative. Must be thread-safe.
  - DroppedCount counts spans dropped/evicted due to full queue (evict-oldest). Thread-safe, non-negative. Non-recording spans (IsRecording false, from Never sampler or dropped by sampler) must not be queued and must not increment DroppedCount. Must be accurate under concurrency.
- Export failure: if ExportSpans returns error, must not crash/panic, continue processing remaining batches. Errors ignored but processing continues.
- Ordering: spans must be exported in order enqueued (FIFO). Batches preserve order across splits: if enqueue 10 spans order-0..order-9 with batch size 3, exports must be [0,1,2], [3,4,5], [6,7,8], [9] in that order.
- Goroutine must exit on Shutdown, no leaks (test runs many processors and ensures no goroutine leak via Shutdown).
- ExportTimeout: each export should respect timeout via context with timeout. Implement export with goroutine + select on context. Example: exporter sleeps 500ms, ExportTimeout 100ms, ForceFlush with background context should return within ~150ms, not block 500ms. Use context.WithTimeout for each export call, and select between done channel and ctx.Done().
- Backpressure timing: normal enqueues (when not flushing) must not block calling goroutine for more than a few milliseconds; only during ForceFlush may OnEnd block when queue full.
- QueueSize enforcement even with eviction: after filling queue 2 and enqueuing 5, queue length must be exactly 2 (not grow unbounded), and DroppedCount 3.
- IsRecording false: tracer with NeverSampler generates non-recording spans, BatchProcessor must detect and drop immediately without queueing, without DroppedCount increment.

Behavioral requirements — hardened:

- Enqueue 5000 spans quickly with queue 5000: all exported after ForceFlush (no eviction since queue large enough). Order preserved.
- Enqueue 5 spans with queue size 2: exported set is last 2 (newest) after evict-oldest semantics, order preserved (span-3 then span-4), DroppedCount >=3, no deadlock, QueueLen <=2 during and after.
- Concurrent producers 100 goroutines x 100 spans with queue 20000: after shutdown all 10000 exported, no race, max batch <= BatchSize.
- Concurrent different tracers sharing same BatchProcessor: 10 tracers each 100 spans, all exported.
- Shutdown flushes all pending respecting BatchSize hard cap (counting exporter asserts maxBatch <= limit even on shutdown).
- IsRecording false spans must not be queued nor counted as dropped.
- Batch size limit enforced per export even during Flush and Shutdown.
- Evict-oldest with order preserved and DroppedCount tracking.
- Block-and-drain: during ForceFlush, concurrent OnEnd blocks not drops, DroppedCount stays 0.
- Non-positive options fallback to defaults.
- Alias options last wins.
- Shutdown idempotent and concurrent with ForceFlush no deadlock.
- ExportTimeout respected via goroutine select.
- Exporter error continues processing.

## 3. Metrics Cardinality Limiting — hardened

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

- Per metric name, track distinct label sets. Distinct defined by label set key after truncation to 256 and sorting keys. If limit N reached (N >0), new distinct combo:
  - "drop": return no-op instrument (operations discarded), not included in Collect, increment dropped counter by 1 per new distinct dropped series.
  - "aggregate": map all excess to special overflow time series with label `__overflow__="true"` shared per metric name. All operations on overflow series aggregate into single series per metric name. Value reflects sum of all excess operations (for Counter Add/Inc, Gauge Set/Inc/Dec/Add, Histogram Observe Count/Sum/Buckets aggregated? For simplicity in this task: aggregate mode only required for Counter; for Gauge/Histogram aggregate behavior may be similar but tests focus on Counter. Implement aggregate for Counter at minimum: overflow series Value = sum of Add/Inc for overflow. For Gauge, overflow value should reflect last Set or aggregated? Tests mainly Counter.
- If unlimited cardinality (0 or negative or no option), DroppedSeriesCount always 0, no limiting.
- In aggregate mode, DroppedSeriesCount must be 0 (since overflow aggregated, not dropped).
- Thread safety: cardinality check concurrent-safe, DroppedSeriesCount thread-safe, uses mutex around map creation.
- Collect must not expose overflow incorrectly; drop mode overflow not in metrics; aggregate mode overflow series appears with label `__overflow__="true"` and value reflects aggregated operations. Overflow series Label map must be non-nil and contain only `__overflow__="true"`? Actually may also have other labels? Spec says special overflow series with label `__overflow__="true"` shared per metric name — so exactly that label.
- Same label set repeated after limit reached must reuse existing instrument if already exists, even at limit. DroppedSeriesCount must NOT increase on reuse of existing label set. Example: limit 2, create id=1, id=2 (limit reached), then id=3 dropped (DroppedCount 1), then reuse id=1 Inc() → DroppedCount stays 1, not 2, and Collect still has 2 series.
- Mixed metric types with same name: if metric name already used as Counter, subsequent Gauge/Histogram with same name should return no-op to avoid type conflict. Same for any type first wins.
- Label value truncation 256 already required and must happen **before** cardinality key computation and before equality check (so truncation interaction: two raw values that truncate to same 256 prefix considered same series).
- Cardinality per-name: limit applies per metric name separately, not globally. Example: limit 2, metric "a" with 3 distinct labels → 2 kept, metric "b" with 3 distinct → 2 kept, total distinct across both 4 (not 2 global).
- Reuse after limit: If limit 2 and you have id=1, id=2, then drop id=3, then reuse id=1 should return same instrument as before (value accumulates).
- Overflow handling mode string: "drop" or "aggregate" case-insensitive? Spec says "drop"/"aggregate" lowercase; implement case-insensitive for robustness or exact match lower-case; tests use lower-case.
- DroppedSeriesCount returns total number of distinct series dropped (not total operations). If same new label dropped multiple times via different Counter() calls with same label set after limit, should count as 1 dropped distinct? Spec ambiguous. We define: DroppedSeriesCount counts distinct label sets that were dropped, not operations. But also if you call Counter with new label that is dropped, and then call again with same dropped label, second call should be considered already dropped? Or should return same no-op and not increase count again? Simpler: count per new distinct dropped label set first time it is dropped. Reuse of already dropped label set should not increase count again. Tests check dropped >=100 when 200 distinct with limit 100, so counts distinct.
- Label truncation interaction: If raw label values "a"*500 and "a"*500+"b" both truncate to "a"*256, they are same series after truncation, so second should reuse first, not drop, DroppedCount not increase.

## 4. Additional Hardening

### Resource Limits for Tracing — exact
- Span attribute count limit: max 128 distinct per span. If more than 128 distinct added (via WithAttributes or AddAttribute), ignore excess beyond 128 (keep first 128 distinct). Duplicate key overwrite allowed even after limit if key already exists, last wins, does not increase distinct count.
- Span attribute value size: if string value >1024 chars, truncate to exactly 1024 chars (keep first 1024). Must happen for both WithAttributes initial and AddAttribute. Exactly 1024 stays 1024, not 1023. Non-string values not truncated.
- Span event count limit: max 128 events per span; beyond drop (keep first 128).
- Event timestamp: set at AddEvent time via time.Now(), must be between StartTime and EndTime, not zero.

### Defensive Copies Everywhere
- WithLabels must copy input map; WithBuckets copy slice; WithAttributes copy slice; AddEvent copy attributes slice; ContextWithTrace copy TraceContext; Exporter GetSpans deep copy Attributes map and Events slice and nested event attributes; Metrics Collect deep copy Labels map and Buckets; Logger With must copy fields slice not share underlying array.

### Graceful Degradation & Concurrency
- Exporters may be slow; Batch processor export timeout ensures not blocking forever via context with timeout + goroutine select.
- No busy loops: background run loop must sleep a few ms when no work, not spin hard.
- No unbounded goroutine spawning per span: only one background goroutine per BatchProcessor plus export timeout goroutines bounded.
- Race-free: all shared state protected by mutex, no data races under `go run -race` with 100 goroutines.

### Nil Handling
- ContextWithTrace(nil, tc) returns non-nil context with trace.
- TraceFromContext(nil) returns false.
- MarshalTrace(nil context or nil carrier) no-op no panic.
- UnmarshalTrace(nil carrier) returns Background.
- Tracer.Start(nil ctx, ...) must not panic, treat as Background.
- Logger WithOutput nil fallback to stderr, WithLevel unknown fallback to info, Info with nil ctx no panic.
- Metrics Counter with invalid name returns no-op not panic, same for Gauge/Histogram, WithLabels invalid key no-op.
- BatchProcessor OnEnd after Shutdown no panic.

## 5. API Additions Summary — hardened
- Samplers: Always, Never, Ratio (last-8-hex + error/critical override precedence over invalid, case-insensitive hex, 2^32 divisor, boundary < not <=, ignores name/kind), ParentAware (parent AND root, description contains root desc + Parent)
- WithSampler TracerOption — default Always, nil no-op
- BatchProcessor: WithBatchSize, WithQueueSize, WithBatchTimeout, WithExportTimeout, WithMaxBatchSize alias, WithMaxExportBatchSize alias, last wins, non-positive fallback, hard cap per export including shutdown, evict-oldest not drop newest, block-and-drain during ForceFlush, DroppedCount thread-safe excluding non-recording, QueueLen excludes batch and never exceeds QueueSize, shutdown idempotent and concurrent with ForceFlush safe, ExportTimeout via goroutine select, ordering preserved, backpressure timing, exporter error continues
- MetricsProvider: WithMaxCardinality, WithCardinalityOverflowHandling drop/aggregate, DroppedSeriesCount distinct dropped, per-name limit, reuse at limit does not increase dropped, label truncate 256 before key, defensive copies for labels and buckets, Collect deep copy Buckets
- Tracing limits and defensive copies and nil handling
- Logger atomic writes and immutable With
- Ensure backward compat: NewTracer with no sampler still Always; MetricsProvider without limit still unlimited.
- Files must stay: tracing.go, metrics.go, logger.go. May add more files but ensure package observability.
- go vet and go build must pass.
- Stdlib only, thread-safe, no races.

Build for scale, but keep correctness — harder now with precise edge cases.
