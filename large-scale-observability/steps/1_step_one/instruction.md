# Large-Scale Observability for Ride-Hailing — Step 1: Core Observability & Design Quality

You are building an observability library for a ride-hailing system (rider, driver, matching, trip, payment).

## Package layout expected

```
/app/
  go.mod (module ride-observability)
  observability/
    tracing.go
    metrics.go
    logger.go
```

Public API must match below. Tests import `ride-observability/observability`.

## 1. Tracing

### Types

```go
type TraceContext struct {
    TraceID  string // 32 hex chars (128-bit TripID)
    SpanID   string // 16 hex chars (64-bit SegmentID)
    ParentID string // 16 hex chars parent or empty
    Sampled  bool
    Flags    byte // 01 if Sampled
}

type SpanStatus int
const (
    StatusUnset SpanStatus = 0
    StatusOK    SpanStatus = 1
    StatusError SpanStatus = 2
)

type SpanKind int
const (
    KindInternal SpanKind = 0
    KindServer   SpanKind = 1
    KindClient   SpanKind = 2
    SpanKindInternal = KindInternal
    SpanKindServer   = KindServer
    SpanKindClient   = KindClient
)

type Attribute struct {
    Key   string
    Value interface{} // string, int, float64, bool allowed
}

type SpanEvent struct {
    Name       string
    Timestamp  time.Time
    Attributes []Attribute
}

type FinishedSpan struct {
    Name          string
    SpanContext   TraceContext
    ParentID      string
    Kind          SpanKind
    StartTime     time.Time
    EndTime       time.Time
    Attributes    map[string]interface{}
    Events        []SpanEvent
    StatusCode    SpanStatus
    StatusMessage string
    ServiceName   string
}
type ReadableSpan = FinishedSpan
type SpanContext = TraceContext
```

### Interfaces

```go
type Span interface {
    End()
    AddAttribute(key string, value interface{})
    AddEvent(name string, attrs ...Attribute)
    SetStatus(code SpanStatus, message string)
    Context() TraceContext
    SpanContext() TraceContext
    IsRecording() bool
}

type Exporter interface {
    ExportSpans(ctx context.Context, spans []FinishedSpan) error
}
type SpanExporter = Exporter

type Processor interface {
    OnStart(ctx context.Context, span FinishedSpan)
    OnEnd(span FinishedSpan)
    Shutdown(ctx context.Context) error
    ForceFlush(ctx context.Context) error
}
type SpanProcessor = Processor

type Tracer interface {
    Start(ctx context.Context, name string, opts ...SpanStartOption) (context.Context, Span)
}
```

#### Options

```go
type TracerOption func(*tracerConfig)
type SpanStartOption func(*spanStartConfig)

func WithServiceName(name string) TracerOption
func WithProcessor(p Processor) TracerOption
func WithSpanProcessor(p Processor) TracerOption
func WithIDGenerator(gen IDGenerator) TracerOption

func WithAttributes(attrs ...Attribute) SpanStartOption
func WithSpanKind(k SpanKind) SpanStartOption
func WithParent(sc TraceContext) SpanStartOption

type IDGenerator interface {
    NewTraceID() string
    NewSpanID() string
}
```

Provide default IDGenerator producing 32 hex and 16 hex random IDs via crypto/rand.

#### Tracer construction

```go
func NewTracer(serviceName string, opts ...TracerOption) Tracer
func NewMemoryExporter() *MemoryExporter
func NewInMemoryExporter() *MemoryExporter
func NewSimpleProcessor(exporter Exporter) Processor
func NewSimpleSpanProcessor(exporter Exporter) Processor
```

`MemoryExporter`:

```go
type MemoryExporter struct{ /* ... */ }
func (e *MemoryExporter) ExportSpans(ctx context.Context, spans []FinishedSpan) error
func (e *MemoryExporter) GetSpans() []FinishedSpan
func (e *MemoryExporter) Clear()
func (e *MemoryExporter) GetCount() int
```

Tracer behavior — hardened (read carefully, many edge cases):

- `Start` creates new span. If ctx already contains a sampled TraceContext, child inherits TraceID and sets ParentID = parent SpanID. Step1 always sampled true. Preserve parent chain for 3+ levels (traceID same across root→child→grandchild, ParentID chain correct).
- TraceID generation: if parent exists (either via ctx or via `WithParent`), reuse parent TraceID. Else generate new via injected IDGenerator. SpanID always new via IDGenerator, even when reusing TraceID. Custom IDGenerator may return non-unique IDs — must not panic.
- `Start` must handle **nil context**: if ctx is nil, treat as `context.Background()`, create span, return non-nil context containing TraceContext. Must not panic.
- Store span in context internally (private key) storing a **copy** of TraceContext, not reference. Mutating original TraceContext after ContextWithTrace must not affect stored context. New context carries this span's TraceContext. `Context()` method must return defensive copy — mutating returned TraceContext must not affect internal span state.
- `Span.End()` computes EndTime, calls processor OnEnd. Idempotent, concurrency-safe. Concurrent calls to End() on same span must export exactly once (50 goroutines calling End() on same span → exporter receives exactly 1 span, no panic, clean under `go run -race`), requiring sync.Once or CAS, not just boolean check. EndTime must be after StartTime and within bounds of before→after wall clock.
- `AddAttribute`, `AddEvent`, `SetStatus` concurrency-safe. `AddEvent` must copy its attributes slice; mutating original slice afterwards must not affect recorded event. AddAttribute/AddEvent racing with End() must be atomic for the no-op check: add-after-end is defined as no-op, but under concurrency the check and End must be synchronized or race detector will catch write to already-exported slice. Export must snapshot the span: the FinishedSpan passed to OnEnd must be a stable copy (Attributes map and Events slice copied) so that concurrent AddAttribute does not mutate already-exported data; verified under `go run -race` with 100 parallel AddAttribute goroutines.
- WithAttributes duplicate keys: if same key appears multiple times in initial attributes or via AddAttribute, last write wins. Duplicate does **NOT** increase attribute distinct count toward 128 limit — overwriting existing key must not count as new distinct key. E.g., 200 Adds of same key "k" → count 1, not 128 exhausted.
- **Resource limits — strict:**
  - Span Attributes: max 128 **distinct** keys per span. If more than 128 distinct added (via WithAttributes or AddAttribute), ignore excess beyond 128 (keep first 128 distinct). Duplicate key overwrites allowed even after limit reached if key already exists, and must update value (last wins). Truncate string attribute values >1024 to exactly 1024 (keep first 1024). Empty key handling: ignore attribute if key empty string (no-op, not counted).
  - Span Events: max 128 events per span. Drop excess beyond 128 (keep first 128). Timestamp set at AddEvent time, must be between StartTime and EndTime, not zero, and must be recent (within test window). Event's Attributes slice must be deep-copied.
  - Attribute value size: string >1024 truncated to exactly 1024 chars (keep first 1024). Boundary: exactly 1024 must stay 1024, not 1023.
- `IsRecording()` true if Sampled true and not ended. After End(), IsRecording false even if sampled was true.
- ParentID rules: root span's ParentID must be empty string, and FinishedSpan.ParentID must equal SpanContext.ParentID (both empty for root). Child span's ParentID must equal parent's SpanID and not empty. FinishedSpan.ParentID == SpanContext.ParentID consistency required at export time. Parent chain 3 levels must preserve traceID and link parent IDs.
- Flags: `TraceContext.Flags` must be `1` if Sampled true else `0`. Must be set at Start time and preserved via propagation Marshal/Unmarshal and Context storage. Unmarshal when sampled=1 must set Flags=1, when 0 must set Flags=0.
- MemoryExporter must be concurrency-safe and `GetSpans()` must return **deep copy**: mutating returned slice, its Name, Attributes map, Events slice, Events[i].Attributes slice, or any nested map must not affect exporter's internal state. Subsequent GetSpans must return original values. Attributes map in returned spans must be non-nil when set (empty map not nil if any attributes were added? Actually when no attrs, may be empty but if set expect non-nil). `GetSpans()` called concurrently with ExportSpans must be safe. `Clear()` and `GetCount()` must be concurrency-safe and Clear-then-reuse must work (exporter reusable after Clear).
- Exporter: Clear() resets internal slice to empty, GetCount() returns len, both thread-safe.

#### Context propagation — hardened

```go
func ContextWithTrace(ctx context.Context, tc TraceContext) context.Context
func TraceFromContext(ctx context.Context) (TraceContext, bool)

func MarshalTrace(ctx context.Context, carrier map[string]string)
func UnmarshalTrace(carrier map[string]string) context.Context

func ContextWithSpanContext(ctx context.Context, tc TraceContext) context.Context
func SpanContextFromContext(ctx context.Context) (TraceContext, bool)
func Inject(ctx context.Context, carrier map[string]string)
func Extract(carrier map[string]string) context.Context
```

- `MarshalTrace` writes into carrier key `x-ride-trace` with value `"{traceID}:{spanID}:{parentID}:{1|0}"` where traceID 32 hex, spanID 16 hex, parentID 16 hex or empty, sampled flag 1/0. Must produce exactly 4 colon-separated parts even when ParentID empty: e.g., `traceID:spanID::1` (empty third part). Example root: `0102030405060708090a0b0c0d0e0f10:0102030405060708::1`, child: `0102030405060708090a0b0c0d0e0f10:0102030405060708:0a0b0c0d0e0f0a0b:0`. Must validate IDs: if TraceID not 32 hex or SpanID not 16 hex or ParentID present but not 16 hex, must NOT write anything (carrier unchanged, no partial write). Must not panic on nil carrier or nil context. Must normalize? Keep original case but validation allows upper/lower. Output Flags derived: sampled 1 => Flags 1, sampled 0 => Flags 0 must be consistent. Must handle case where TraceFromContext returns false → do nothing.
- `UnmarshalTrace` reads `x-ride-trace`, validates hex (upper or lower case allowed), returns context with TraceContext. If missing or invalid (wrong parts count !=4, non-hex, parent non-hex, sampled part not "0" nor "1"? Actually spec says sampled flag 1/0 but we treat any non-"1" as 0? For strictness: if sampled part is not "0" nor "1", treat as invalid → background), return background context (no span). Must handle empty ParentID as valid root, nil and empty carrier as no-op returning background. Must set Flags correctly based on sampled.
- Alias `Inject` writes only `x-ride-trace` with same validation, must NOT write legacy keys `trace-id`, `span-id`, `parent-id`, `sampled`. Tests assert absence of `trace-id`. Alias `Extract` reads only `x-ride-trace`. Single header is canonical, not four keys.
- Validation: TraceID 32 hex, SpanID 16 hex, ParentID empty or 16 hex. If invalid, ignore extraction. Case-insensitive hex, but output from MarshalTrace should be lowercase? Existing impl keeps original but hex from generator is lowercase; test accepts upper/lower for input but checks preservation.
- Thread-safe and must not panic on nil contexts or carriers. `ContextWithTrace(nil, tc)` must return non-nil context containing trace (treat nil as Background). `TraceFromContext(nil)` must return false, not panic. Same for SpanContext aliases.
- Immutability: ContextWithTrace must store **copy** of tc, not reference. Mutating original tc after call must not affect context. Also `SpanContextFromContext` / `TraceFromContext` returns copy; mutating returned struct must not affect stored context (but Go value copy semantics already handle, but importance for map/slice fields if any).
- Empty ParentID format: When ParentID empty, format must still have 4 parts with empty third: `traceID:spanID::1`. Not `traceID:spanID:1` (3 parts) nor `traceID:spanID::1:` etc.

## 2. Metrics

```go
type Counter interface { Inc(); Add(delta float64) }
type Gauge interface { Set(v float64); Inc(); Dec(); Add(delta float64) }
type Histogram interface { Observe(v float64) }

type MetricOption func(*metricConfig)
func WithLabels(labels map[string]string) MetricOption
func WithDescription(desc string) MetricOption
func WithBuckets(buckets []float64) MetricOption

type MetricFamily struct {
    Name    string
    Type    string
    Help    string
    Metrics []MetricSample
}
type MetricSample struct {
    Labels map[string]string
    Value  float64
    Count   uint64
    Sum     float64
    Buckets []HistogramBucket
}
type HistogramBucket struct {
    UpperBound float64
    Count      uint64
}

type MetricsProvider interface {
    Counter(name string, opts ...MetricOption) Counter
    Gauge(name string, opts ...MetricOption) Gauge
    Histogram(name string, opts ...MetricOption) Histogram
    Collect() []MetricFamily
}

func NewMetricsProvider(opts ...MetricsOption) MetricsProvider
```

Rules — hardened with defensive copies and truncation interaction:

- Name must match `^[a-zA-Z_][a-zA-Z0-9_]*$`. Invalid => no-op instrument (operations discarded), not included in Collect. Must not panic.
- Labels key regex same `^[a-zA-Z_][a-zA-Z0-9_]*$`. Invalid label keys => no-op instrument. Empty label key invalid => no-op. Empty label value allowed (but still truncated).
- **Label value truncate 256 chars** (keep first 256), do not drop metric. Truncation must happen **before** storing and **before** cardinality key computation (if step2) and before label equality check. So two distinct raw values that truncate to same 256 prefix must be considered same series (reuse). Truncate exactly 256, not 255.
- **Defensive copy for metric options**: `WithLabels` must copy input map — mutating original map after Counter/Gauge/Histogram creation must not affect stored labels (test mutates original map and expects Collect unchanged). Similarly `WithBuckets` must copy slice — mutating original slice after Histogram creation must not affect buckets. `WithDescription` string copy trivial but ensure not sharing.
- Counter `Add(delta)` >=0 only, ignore negative, NaN, Inf. Must be thread-safe. `Inc()` always +1. Gauge `Set(v)` must ignore NaN, Inf (no-op, keep previous value). `Add(delta)` for Gauge must ignore NaN, Inf (no-op). `Inc()`/`Dec()` for Gauge always +/-1 even when current value is NaN? But since Set ignores NaN, current never NaN. Histogram `Observe(v)` ignore NaN/Inf.
- Same name + same label set reuse same instrument (inc on one affects other). Label set equality after truncation and after sorting keys: labels `{"a":"1","b":"2"}` equals `{"b":"2","a":"1"}` same series. Distinct label sets are separate series. Reuse must be concurrent-safe.
- Type conflict on the same name: the first registration fixes the metric's type. A later Counter/Gauge/Histogram call with the same name but a different type is a no-op — it returns a usable instrument whose operations are silently discarded, and Collect() still emits exactly one family for that name, with the original type and values. Example: Counter("m").Inc(), then Gauge("m").Set(100), then Histogram("m").Observe(5) → one family, Type == "counter", value 1. Type string in MetricFamily: "counter", "gauge", "histogram" lowercase.
- Thread safety required for all operations including concurrent Collect and Add/Inc/Observe — no races, verified with `go run -race` 100 goroutines x 1000 ops. Collect concurrent with mutations must not race and must return snapshot.
- `Collect()` must return deep copy — must not expose internal mutable maps: mutating returned slice, family slice, Metrics slice, Labels map, Buckets slice must not affect provider internal state; subsequent Collect must return original values. Returned Labels map must be non-nil when labels present (not nil map). Buckets slice in returned samples must also be deep copy. Help string must be preserved and also deep-copied? At least not shared mutable.
- Histogram default buckets `[0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10]`. Cumulative inclusive: value == upper bound counts in that bucket. So Observe(1) with buckets [1,5,10] => bucket 1 count 1, bucket 5 count 1, bucket 10 count 1. Sorted ascending even if input unsorted. Deduplicate? If input has duplicates, keep sorted and deduplicate exact duplicates (keep first). Example input [10,1,5,1] => sorted dedup [1,5,10].
- Histogram Observe and bucket counts cumulative: each bucket counts values <= upper bound. Sum and Count fields must be accurate. Count increments per Observe, Sum adds value.
- Provider isolation: different providers must not share state (global map bug fails). Also same provider reused after Collect must still work.
- Histogram buckets handling: Must sort ascending even if input unsorted, and must be defensive copy. Changing original bucket slice after creation must not affect stored buckets.
- Label truncation interaction with reuse: After truncation, same truncated key considered same. Test: labels with 500-char strings differing only after 256 chars should reuse same instrument.

## 3. Logging

```go
type Field struct { Key string; Value interface{} }
type Logger interface {
    Info(ctx context.Context, msg string, fields ...Field)
    Error(ctx context.Context, msg string, fields ...Field)
    Debug(ctx context.Context, msg string, fields ...Field)
    Warn(ctx context.Context, msg string, fields ...Field)
    With(fields ...Field) Logger
}
type LoggerOption func(*loggerConfig)
func WithOutput(w io.Writer) LoggerOption
func WithLevel(level string) LoggerOption
func NewLogger(serviceName string, opts ...LoggerOption) Logger
```

- JSON per line: `timestamp` RFC3339Nano, `level`, `service`, `message`, plus trace correlation `trace_id`, `span_id`, `sampled` if ctx has TraceContext, optional `parent_id` if TraceContext ParentID non-empty. Fields: timestamp, level, service, message, plus any fields from With chain and per-log call. Custom field overwrite: last write wins for same key (With chain then per-call fields). Must include `sampled` boolean when trace present.
- Thread-safe, `With` immutable copy (must copy underlying fields slice, not share array that could be mutated), level filtering debug<info<warn<error> default info case-insensitive. WithOutput nil => fallback to os.Stderr, not panic. WithLevel unknown string => default info.
- Atomic per-line write: concurrent logging from 100 goroutines must produce exactly 100 lines, each valid JSON, no interleaved bytes (use mutex around Write). Logger's output write must be protected.
- `With` chaining: base.With(a).With(b) must have both a and b, and child overriding parent same key. `With` called on logger with existing fields must not mutate original logger's fields (immutable). Concurrent With and log calls must be safe (no race).
- No panic on nil ctx, nil fields, empty message. `Info(nil, "msg")` must not panic and must log without trace correlation.
- Timestamp: RFC3339Nano, parseable, within test window (not zero, recent).
- Level filtering: debug=0, info=1, warn=2, error=3. Default info. WithLevel "ERROR" case-insensitive must work. Filtering: log only if level >= minLevel.
- JSON escaping: must be valid JSON even when message or field values contain special chars.

## Constraints
- Stdlib only, `go vet ./...` and `go build ./...` pass.
- Files must exist: `tracing.go`, `metrics.go`, `logger.go`
- Thread safety mandatory
- Implement all public symbols above.

## Grading
Binary pass/fail based on all sub-tests.

Implement step1 fully; do not yet implement samplers/batch/cardinality — those step2.
