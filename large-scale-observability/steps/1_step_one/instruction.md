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

Tracer behavior:

- `Start` creates new span. If ctx already contains a sampled TraceContext, child inherits TraceID and sets ParentID = parent SpanID. Step1 always sampled true. Preserve parent chain.
- TraceID generation: if parent exists, reuse parent TraceID. Else generate new.
- SpanID always new.
- Store span in context internally (private key) storing a **copy** of TraceContext, not reference. Mutating original TraceContext after ContextWithTrace must not affect stored context. New context carries this span's TraceContext.
- `Span.End()` computes EndTime, calls processor OnEnd. Idempotent, concurrency-safe.
- `AddAttribute`, `AddEvent`, `SetStatus` concurrency-safe. `AddEvent` must copy its attributes slice; mutating original slice afterwards must not affect recorded event.
- WithAttributes duplicate keys: if same key appears multiple times in initial attributes or via AddAttribute, last write wins. Duplicate does not increase attribute count.
- **Resource limits:**
  - Span Attributes: max 128 distinct keys per span. If more than 128 added (via WithAttributes or AddAttribute), ignore excess beyond 128. Truncate string attribute values >1024 to exactly 1024.
  - Span Events: max 128 events per span. Drop excess beyond 128 (keep first 128). Timestamp set at AddEvent time.
  - Attribute value size: string >1024 truncated to exactly 1024 chars (keep first 1024).
- `IsRecording()` true if Sampled true and not ended.
- ParentID rules: root span's ParentID must be empty string, and FinishedSpan.ParentID must equal SpanContext.ParentID. Child span's ParentID must equal parent's SpanID and not empty.
- MemoryExporter must be concurrency-safe and `GetSpans()` must return deep copy: mutating returned slice, its Name, Attributes map, or Events must not affect exporter's internal state. Subsequent GetSpans must return original values. Attributes map in returned spans must be non-nil when set.
- Exporter: Clear() and GetCount() must be concurrency-safe.

#### Context propagation

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

- `MarshalTrace` writes into carrier key `x-ride-trace` with value `"{traceID}:{spanID}:{parentID}:{1|0}"` where traceID 32 hex, spanID 16 hex, parentID 16 hex or empty, sampled flag 1/0. Must produce exactly 4 colon-separated parts even when ParentID empty: e.g., `traceID:spanID::1` (empty third part). Example root: `0102030405060708090a0b0c0d0e0f10:0102030405060708::1`, child: `0102030405060708090a0b0c0d0e0f10:0102030405060708:0a0b0c0d0e0f0a0b:0`. Must validate IDs: if TraceID not 32 hex or SpanID not 16 hex or ParentID present but not 16 hex, must NOT write anything. Must not panic on nil carrier or nil context.
- `UnmarshalTrace` reads `x-ride-trace`, validates hex (upper or lower case allowed), returns context with TraceContext. If missing or invalid (wrong parts count !=4, non-hex, parent non-hex), return background context (no span). Must handle empty ParentID as valid root, nil and empty carrier as no-op returning background.
- Alias `Inject` writes only `x-ride-trace` with same validation. Alias `Extract` reads only `x-ride-trace`. Single header is canonical.
- Validation: TraceID 32 hex, SpanID 16 hex, ParentID empty or 16 hex. If invalid, ignore extraction.
- Thread-safe and must not panic on nil contexts or carriers. ContextWithTrace(nil, tc) must return non-nil context containing trace.

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

Rules:
- Name must match `^[a-zA-Z_][a-zA-Z0-9_]*$`. Invalid => no-op, not included in Collect.
- Labels key regex same. Invalid label keys => no-op.
- **Label value truncate 256 chars** (keep first 256), do not drop metric.
- Counter Add >=0 only, ignore negative, NaN, Inf. Histogram Observe ignore NaN/Inf.
- Same name + same label set reuse same instrument (inc on one affects other). Distinct label sets are separate series.
- Type conflict on the same name: the first registration fixes the metric's type. A later Counter/Gauge/Histogram call with the same name but a different type is a no-op — it returns a usable instrument whose operations are silently discarded, and Collect() still emits exactly one family for that name, with the original type and values. Example: Counter("m").Inc(), then Gauge("m").Set(100), then Histogram("m").Observe(5) → one family, Type == "counter", value 1.
- Thread safety required for all operations including concurrent Collect and Add/Inc/Observe — no races.
- `Collect()` must return deep copy — must not expose internal mutable maps: mutating returned slice, Metrics slice, or Labels map must not affect provider internal state; subsequent Collect must return original values. Returned Labels map must be non-nil when labels present. Buckets slice in returned samples must also be deep copy.
- Histogram default buckets `[0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10]`. Cumulative inclusive: value == upper bound counts in that bucket. So Observe(1) with buckets [1,5,10] => bucket 1 count 1, bucket 5 count 1, bucket 10 count 1. Sorted ascending even if input unsorted.
- Histogram Observe and bucket counts cumulative: each bucket counts values <= upper bound.
- Provider isolation: different providers must not share state.

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

- JSON per line: `timestamp` RFC3339Nano, `level`, `service`, `message`, plus trace correlation `trace_id`, `span_id`, `sampled` if ctx has TraceContext.
- Thread-safe, `With` immutable copy, level filtering debug<info<warn<error> default info.

## Constraints
- Stdlib only, `go vet ./...` and `go build ./...` pass.
- Files must exist: `tracing.go`, `metrics.go`, `logger.go`
- Thread safety mandatory
- Implement all public symbols above.

## Grading
Binary pass/fail based on all sub-tests.

Implement step1 fully; do not yet implement samplers/batch/cardinality — those step2.
