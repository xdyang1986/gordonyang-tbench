# Large-Scale Observability for Ride-Hailing — Step 1: Core Observability & Design Quality

You are building an observability library for a micro-service ride-hailing system similar to Uber. Services include `rider`, `driver`, `matching`, `trip`, `payment`. Step 1 focuses on the foundational observability primitives and **design quality** that will be reused at scale.

Your code lives in `/app`. Module `ride-observability` (Go 1.22, stdlib only). Implement package `observability` at `/app/observability`.

## Package layout expected

```
/app/
  go.mod (module ride-observability)
  observability/
    tracing.go
    metrics.go
    logger.go
    exporter.go (optional but you need exporters)
```

You may split into more files, but public API must match below. Tests import `ride-observability/observability`.

## 1. Tracing

### Types

```go
type SpanContext struct {
    TraceID      string // 32 hex chars (128-bit)
    SpanID       string // 16 hex chars (64-bit)
    ParentSpanID string
    Sampled      bool
    TraceFlags   byte // optional, 01 if Sampled
}

type StatusCode int
const (
    StatusUnset StatusCode = 0
    StatusOK    StatusCode = 1
    StatusError StatusCode = 2
)

type SpanKind int
const (
    SpanKindInternal SpanKind = 0
    SpanKindServer   SpanKind = 1
    SpanKindClient   SpanKind = 2
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

type ReadableSpan struct {
    Name         string
    SpanContext  SpanContext
    ParentSpanID string
    SpanKind     SpanKind
    StartTime    time.Time
    EndTime      time.Time
    Attributes   map[string]interface{}
    Events       []SpanEvent
    StatusCode   StatusCode
    StatusMessage string
    ServiceName  string
}
```

### Interfaces

```go
type Span interface {
    End()
    AddAttribute(key string, value interface{})
    AddEvent(name string, attrs ...Attribute)
    SetStatus(code StatusCode, message string)
    SpanContext() SpanContext
    IsRecording() bool // true if sampled/recording
}

type SpanExporter interface {
    ExportSpans(ctx context.Context, spans []ReadableSpan) error
}

type SpanProcessor interface {
    OnStart(ctx context.Context, span ReadableSpan)
    OnEnd(span ReadableSpan)
    Shutdown(ctx context.Context) error
    ForceFlush(ctx context.Context) error
}

type Tracer interface {
    Start(ctx context.Context, name string, opts ...SpanStartOption) (context.Context, Span)
}
```

#### Options

```go
type TracerOption func(*tracerConfig)
type SpanStartOption func(*spanStartConfig)

func WithServiceName(name string) TracerOption // optional alias, but you must accept serviceName in NewTracer
func WithSpanProcessor(p SpanProcessor) TracerOption
func WithIDGenerator(gen IDGenerator) TracerOption // for testing

func WithAttributes(attrs ...Attribute) SpanStartOption
func WithSpanKind(k SpanKind) SpanStartOption
func WithParent(sc SpanContext) SpanStartOption // explicit parent

type IDGenerator interface {
    NewTraceID() string
    NewSpanID() string
}
```

Provide default IDGenerator producing 32 hex and 16 hex random IDs via crypto/rand or math/rand.

#### Tracer construction

```go
func NewTracer(serviceName string, opts ...TracerOption) Tracer
func NewInMemoryExporter() *InMemoryExporter // holds finished spans in memory
func NewSimpleSpanProcessor(exporter SpanExporter) SpanProcessor // synchronous export on End
```

`InMemoryExporter`:

```go
type InMemoryExporter struct{ /* ... */ }
func (e *InMemoryExporter) ExportSpans(ctx context.Context, spans []ReadableSpan) error
func (e *InMemoryExporter) GetSpans() []ReadableSpan
func (e *InMemoryExporter) Clear()
func (e *InMemoryExporter) GetCount() int
```

Tracer behavior (must satisfy design quality):

- `Start` creates new span. If ctx already contains a sampled SpanContext, child inherits TraceID and sets ParentSpanID = parent SpanID. If parent not sampled, child also respects? For step1, always sample (Sampled=true) unless parent exists and explicitly not sampled? Simplify: Always Sampled=true in step1 (sampling logic added in step2). But preserve parent chain.
- TraceID generation: if parent exists, reuse parent TraceID. Else generate new traceID.
- SpanID always new.
- Store Span in context internally (context key private). New context returned carries this span's SpanContext.
- `Span.End()` computes EndTime, calls processor OnEnd. Should be callable once (idempotent or second call no-op). Must be concurrency-safe.
- `AddAttribute`, `AddEvent`, `SetStatus` must be concurrency-safe (protect with mutex).
- **Resource limits — must be implemented for design quality (these are checked):**
  - **Span Attributes**: max 128 per span. If more than 128 added (via `WithAttributes` or `AddAttribute`), ignore excess beyond 128 (keep first 128). Truncate string attribute values longer than 1024 chars to exactly 1024.
  - **Span Events**: max 128 events per span. If more than 128 `AddEvent` calls, drop excess beyond 128 (keep first 128). Events must store Name, Timestamp (set at AddEvent time), and Attributes. Limit applies to total events, including initial? For step1, only `AddEvent` path matters.
  - **Attribute value size**: string values >1024 truncated to 1024.
- StartTime set at Start, EndTime set at End.
- `IsRecording()` returns true if Sampled true and not ended.
- `SimpleSpanProcessor`: OnEnd exports synchronously via exporter. Must be thread-safe.

#### Context propagation

```go
func ContextWithSpanContext(ctx context.Context, sc SpanContext) context.Context
func SpanContextFromContext(ctx context.Context) (SpanContext, bool)
func Inject(ctx context.Context, carrier map[string]string)
func Extract(carrier map[string]string) context.Context
```

- `Inject` writes into carrier: keys `trace-id`, `span-id`, `parent-id`, `sampled` (1/0). If context has SpanContext, inject it.
- `Extract` reads carrier, if valid, returns context with SpanContext. If invalid carrier, return background context with empty? Should return ctx without span.
- Validation: TraceID must be 32 hex chars, SpanID 16 hex chars. If invalid, ignore extraction (return original context).
- Must handle case-insensitivity? Require exact lowercase keys but test uses lower.
- Must be thread-safe (carrier map is passed by caller, not shared).

### Tests that will run (design quality):

- Trace/span creation generates valid hex IDs.
- Child inherits TraceID, different SpanID, ParentSpanID correct.
- Context propagation Inject/Extract roundtrip.
- Concurrent Start/End from 100 goroutines does not race, no duplicate IDs beyond negligible, finished count correct.
- Finished spans stored in InMemoryExporter, attributes/events/status captured.
- Attribute limit enforcement.
- End idempotency.
- Thread safety: simultaneous AddAttribute/AddEvent/SetStatus/End.
- ServiceName present in ReadableSpan.
- No global mutable state shared across tracers that would cause cross contamination (two tracers with different service names isolated).

## 2. Metrics

```go
type Counter interface {
    Inc()
    Add(delta float64)
}

type Gauge interface {
    Set(v float64)
    Inc()
    Dec()
    Add(delta float64)
}

type Histogram interface {
    Observe(v float64)
}

type MetricOption func(*metricConfig)
func WithLabels(labels map[string]string) MetricOption
func WithDescription(desc string) MetricOption
func WithBuckets(buckets []float64) MetricOption // for histogram, e.g., [0.1, 0.5, 1, 5]

type MetricFamily struct {
    Name    string
    Type    string // "counter", "gauge", "histogram"
    Help    string
    Metrics []MetricSample
}

type MetricSample struct {
    Labels map[string]string
    Value  float64
    // for histogram: Count, Sum, Buckets
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
type MetricsOption func(*metricsConfig)
```

Rules:

- Name must match `^[a-zA-Z_][a-zA-Z0-9_]*$`. Return no-op or handle invalid? For this task: if invalid name, return no-op instrument that does nothing but Collect does NOT include invalid metric.
- Labels must match `^[a-zA-Z_][a-zA-Z0-9_]*$`. Invalid label keys cause metric to be no-op.
- **Label value handling:** label values may be long (e.g., 500 chars). To prevent high cardinality via long values, **truncate label values longer than 256 chars to 256 chars** (keep first 256). Do **not** drop the metric entirely for long values — truncate. This is required.
- Counter only inc positive? Add delta can be >=0 only. If negative, ignore. **Also ignore NaN and Inf for Counter Add and Histogram Observe** (do nothing).
- Same metric name + same label set should return same instrument instance (reuse). Same name but different label values -> different time series (distinct MetricSample entries).
- Thread safety: Inc/Add/Observe/Set may be called concurrently; must use atomic or mutex and not race. Test 100 goroutines x 1000 inc.
- `Collect()` returns snapshot of all metrics at call time. **Must return deep copy — must not expose internal mutable maps:** mutating the returned `MetricFamily` slice, any `Metrics` slice, or any `Labels` map (including mutating a label value or injecting a new key) must not affect the provider's internal state; a subsequent `Collect()` must return original values. You must copy both slices and maps. When labels are present, the returned `Labels` map must be non-nil and mutable safely without panicking.
- Histogram: default buckets if not provided: `[0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10]`. On Observe, increment count, sum, bucket counts (cumulative? Use inclusive). Return in Collect: Buckets with cumulative counts, plus Count and Sum. Buckets should be sorted ascending even if input unsorted.
- Counter/Gauge value stored as float64.

Design quality:

- Options pattern.
- No global registry; provider instance isolation.
- Concurrency-safe.
- MetricsProvider reuse logic: same name+labels returns same object pointer or value-equal behavior (inc on one affects other).

## 3. Logging

```go
type Field struct {
    Key   string
    Value interface{}
}

type Logger interface {
    Info(ctx context.Context, msg string, fields ...Field)
    Error(ctx context.Context, msg string, fields ...Field)
    Debug(ctx context.Context, msg string, fields ...Field)
    Warn(ctx context.Context, msg string, fields ...Field)
    With(fields ...Field) Logger
}

type LoggerOption func(*loggerConfig)
func WithOutput(w io.Writer) LoggerOption
func WithLevel(level string) LoggerOption // debug, info, warn, error

func NewLogger(serviceName string, opts ...LoggerOption) Logger
```

- Structured JSON logging to output (default os.Stderr? But for test, WithOutput used). Each log line is single JSON object.
- Required fields in JSON: `timestamp` (RFC3339Nano), `level`, `service`, `message`, plus any trace correlation: if ctx contains SpanContext, automatically include `trace_id`, `span_id`, `sampled`.
- Additional fields from args and from With() should be included.
- Thread-safe.
- `With` returns new Logger with additional fields, immutable copy, does not affect parent.
- Level filtering: if level > configured min level, skip? Implement debug < info < warn < error. Default info. If configured error, only error logs.

Design quality checks:

- Logger With immutable.
- Trace correlation automatic.
- JSON valid.
- Concurrent logging not interleaving lines.

## 4. Ride Service Integration (optional but recommended)

In `/app/ride/service.go` you may add observability wiring; tests do not require it but it validates integration.

## Constraints

- Only stdlib: no external deps. go.mod must not require non-stdlib.
- No `//go:embed` of test bypass.
- Thread safety mandatory; tests use `-race`? Will test with -race manually; ensure no races.
- Files must exist to pass: `/app/observability/tracing.go`, `/app/observability/metrics.go`, `/app/observability/logger.go`
- Must implement all public symbols above, or tests fail.

## Expected deliverable

After step1, `go vet ./...` and `go test -run TestNone ./...` (just build) should succeed. Full verification runs a harness inside `/tests` that imports your package and checks behavior.

Implement step1 fully; do not yet implement samplers/batch processor/cardinality — those are step2. Keep step1 sampling always true.

## Grading

Binary pass/fail based on all sub-tests. If any fails, step fails.

Start implementing.
