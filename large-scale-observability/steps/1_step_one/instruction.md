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

Tracer behavior — very hardened (read carefully, many subtle edge cases — this step gates weak models):

- `Start` creates new span. If ctx already contains a sampled TraceContext, child inherits TraceID and sets ParentID = parent SpanID. Step1 always sampled true. Preserve parent chain for 3+ levels (traceID same across root→child→grandchild, ParentID chain correct: gp SpanID == parent ParentID, parent SpanID == child ParentID).
- TraceID generation: if parent exists (either via ctx or via `WithParent`), reuse parent TraceID (even if parent TraceID is invalid hex — must not panic, must still reuse for child). Else generate new via injected IDGenerator. SpanID always new via IDGenerator, even when reusing TraceID and even when parent exists. Custom IDGenerator may return non-unique or invalid IDs — must not panic. IDGenerator nil fallback to default.
- `Start` must handle **nil context**: if ctx is nil, treat as `context.Background()`, create span, return non-nil context containing TraceContext. Must not panic. Also `WithParent` overrides ctx parent even when ctx nil. `Start` with nil opts must work.
- Store span in context internally (private key) storing a **copy** of TraceContext, not reference. Mutating original TraceContext after ContextWithTrace must not affect stored context. New context carries this span's TraceContext. `Context()` method must return defensive copy — mutating returned TraceContext must not affect internal span state. `SpanContext()` alias same.
- `Span.End()` computes EndTime via `time.Now()`, calls processor OnEnd once. Idempotent, concurrency-safe. Concurrent calls to End() on same span must export exactly once (50 goroutines calling End() → exporter receives exactly 1 span, no panic, clean under `go run -race`), requiring `sync.Once` or atomic CAS, not just boolean check. **EndTime preservation**: second End() must NOT update EndTime — first EndTime preserved. EndTime must be after StartTime and within bounds of before→after wall clock (test records before/after). StartTime must not be zero.
- `AddAttribute`, `AddEvent`, `SetStatus` concurrency-safe, protected by same mutex as End to make add-after-end atomic. `AddEvent` must copy its attributes slice; mutating original slice afterwards (including mutating slice elements Key/Value) must not affect recorded event. AddAttribute/AddEvent racing with End() must be atomic for the no-op check: add-after-end is defined as no-op, but under concurrency the check and End must be synchronized or race detector will catch write to already-exported slice. Export must snapshot the span: the FinishedSpan passed to OnEnd must be a stable copy (Attributes map and Events slice copied) so that concurrent AddAttribute does not mutate already-exported data; verified under `go run -race` with 100 parallel AddAttribute goroutines.
- WithAttributes duplicate keys: if same key appears multiple times in initial attributes (same `WithAttributes` call or across multiple `WithAttributes` options), last write wins. Duplicate does **NOT** increase attribute distinct count toward 128 limit — overwriting existing key must not count as new distinct key. E.g., 200 Adds of same key "k" → count 1, not 128 exhausted. Same for initial: `[ {k:v1}, {k:v2} ]` → count 1 with v2.
- **Resource limits — very strict:**
  - Span Attributes: max 128 **distinct** keys per span. If more than 128 distinct added (via WithAttributes or AddAttribute), ignore excess beyond 128 (keep first 128 distinct). Duplicate key overwrites allowed even after limit reached if key already exists, and must update value (last wins) even when at limit. Truncate string attribute values >1024 to exactly 1024 (keep first 1024). Empty key handling: ignore attribute if key empty string (no-op, not counted toward limit). Nil value handling: if value is nil, ignore attribute (no-op, not counted). Invalid type handling: only allowed types `string, int, int8, int16, int32, int64, uint, uint8, uint16, uint32, uint64, float32, float64, bool` stored; other types (slice, map, struct, etc.) ignored no-op not counted. This forces type filtering.
  - Span Events: max 128 events per span. Drop excess beyond 128 (keep first 128). Timestamp set at AddEvent time via `time.Now()`, must be between StartTime and EndTime, not zero, and must be recent (within test window) and monotonic non-decreasing across events. Event's Attributes slice must be deep-copied (including inner slice copy). Event attributes with empty key ignored? For simplicity, event attributes allowed even if empty key? But we require empty key ignored for span attributes only; for event attributes, empty key also ignored or kept? We keep same rule: empty key ignored for event attributes too (do not add to event's Attributes if key empty). Event's Name must be preserved.
  - Attribute value size: string >1024 truncated to exactly 1024 chars (keep first 1024). Boundary: exactly 1024 must stay 1024, not 1023; 1025 → 1024.
  - Event limit: keep first 128, drop rest.
- `IsRecording()` true if Sampled true and not ended. After End(), IsRecording false even if sampled was true. After End(), AddAttribute/AddEvent/SetStatus no-op and must not affect exported span.
- ParentID rules: root span's ParentID must be empty string, and FinishedSpan.ParentID must equal SpanContext.ParentID (both empty for root). Child span's ParentID must equal parent's SpanID and not empty. FinishedSpan.ParentID == SpanContext.ParentID consistency required at export time. Parent chain 3 levels must preserve traceID and link parent IDs. Root's ParentID empty, child ParentID = parent SpanID.
- Flags: `TraceContext.Flags` must be `1` if Sampled true else `0`. Must be set at Start time and preserved via propagation Marshal/Unmarshal and Context storage. Unmarshal when sampled=1 must set Flags=1, when 0 must set Flags=0. Exported span's SpanContext.Flags must match Sampled.
- ServiceName: effective service name is `WithServiceName` if provided non-empty else first arg to NewTracer. Empty Both? Then empty string allowed but test checks override precedence: `NewTracer("original", WithServiceName("override"))` → ServiceName == "override". If WithServiceName empty string, should fallback to original arg? Spec: WithServiceName("") should be treated as override to empty? But we define: If WithServiceName provided with non-empty, overrides; if empty, keep original. For simplicity: if option sets serviceName to empty, treat as not overriding (keep original). Test checks non-empty override.
- MemoryExporter must be concurrency-safe and `GetSpans()` must return **deep copy**: mutating returned slice, its Name, Attributes map, Attributes map values (if string), Events slice, Events[i].Name, Events[i].Attributes slice, Events[i].Attributes[i].Key/Value must not affect exporter's internal state. Subsequent GetSpans must return original values. Attributes map in returned spans must be non-nil when attributes were set (non-nil even if empty? We require non-nil when at least one attribute added; when zero attributes, may be nil or empty non-nil both accepted but we check non-nil when set). `GetSpans()` called concurrently with ExportSpans and Clear must be safe (no race). `Clear()` and `GetCount()` must be concurrency-safe and Clear-then-reuse must work (exporter reusable after Clear, order preserved after Clear). `Clear()` concurrent with `GetSpans()` safe.
- Exporter: Clear() resets internal slice to empty (nil or empty), GetCount() returns len, both thread-safe. GetSpans slice mutation (append to returned slice) must not affect internal.
- Custom IDGenerator invalid: If IDGenerator returns non-hex or wrong length for TraceID/SpanID, span still created with those IDs (no validation at Start), but MarshalTrace must NOT write anything because validation fails (carrier unchanged). Must not panic. Tests: custom gen returns "invalid" TraceID → Unmarshal after Marshal should yield no trace, but span still exists with that invalid TraceID in exporter (since exporter doesn't validate).
- Context propagation overwrite: MarshalTrace with carrier that already has `x-ride-trace` must overwrite with new value.


- **Additional very hard edge cases (v4 - 170 tests):**
  - `UnmarshalTrace` header key lookup case-insensitive (`x-ride-trace`, `X-Ride-Trace`, `X-RIDE-TRACE` all valid).
  - `UnmarshalTrace` trims leading/trailing whitespace around entire header value.
  - Uppercase hex allowed for TraceID/SpanID/ParentID (validation case-insensitive).
  - Event attribute value truncation: string values >1024 truncated to 1024 for event attributes as well.
  - `AddEvent` empty name ignored (no-op, not counted toward 128 event limit).
  - Event attributes filtering: empty key ignored, nil/invalid type ignored, string truncation same as span attributes.
  - `WithSpanKind` last wins when multiple options: `WithSpanKind(Client), WithSpanKind(Server)` => Server.
  - `SetStatus` last wins before End: multiple calls before End keep last.
  - Concurrent `AddAttribute`, `AddEvent`, `SetStatus`, `End` race-safe: 10/10/5 goroutines plus End once -> exactly 1 span exported, no race.
  - `ContextWithTrace` overwrites previous trace in same context (second call wins).
  - SpanContext copy on parent: child ParentID snapshot, not live reference, preserved after parent End.
  - `MarshalTrace` preserves other unrelated carrier keys.
  - Attr limit and event limit independent: 128 span attrs + 128 events both allowed (not shared budget).
  - Counter `Add(-5)` negative noop (value unchanged), per spec Add >=0 only. Add(0) allowed.
  - Collect buckets deep copy: mutating returned Buckets slice (Count, UpperBound) must not affect internal; next Collect returns original.
  - Histogram observe negative allowed: e.g., buckets [0,10], Observe(-5) counts in bucket 0.
  - Label truncation collision: values differing only after 256 chars map to same series after truncation -> reuse same instrument, value 2.
  - Concurrent creation same labelset: 20 goroutines Counter same name+labels => 1 series, total 20.
  - Provider Collect does NOT clear: second Collect without new data still returns data (not empty). Reuse after Collect must work.
  - Gauge Add NaN/Inf ignored (like Set).
  - Counter race Add and Collect concurrent safe under -race.
  - Logger unknown level defaults to info, filtering works.
  - Logger error level filters lower (info, warn filtered).
  - Logger service field cannot be overridden by With field named "service" -> always real service name.
  - Logger fields JSON types: int, bool, float marshaled correctly.
  - Logger concurrent With and Log safe.
  - FinishedSpan ParentID empty for root, StartTime <= EndTime.
  - IDGenerator nil fallback to default valid IDs.

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

Rules — very hardened with defensive copies, truncation-before-key, NaN/Inf bucket filtering:

- Name must match `^[a-zA-Z_][a-zA-Z0-9_]*$`. Invalid => no-op instrument (operations discarded), not included in Collect. Must not panic. Name empty invalid => no-op.
- Labels key regex same `^[a-zA-Z_][a-zA-Z0-9_]*$`. Invalid label keys => no-op instrument. Empty label key invalid => no-op. Empty label value allowed (but still truncated). Invalid label keys in WithLabels with multiple keys: if any key invalid, entire metric becomes no-op (not just that label). Example `{"ok":"1","bad-key":"2"}` → invalid because "bad-key" contains hyphen → no-op.
- **Label value truncate 256 chars** (keep first 256), do not drop metric. Truncation must happen **before** storing and **before** cardinality key computation (if step2) and before label equality check. So two distinct raw values that truncate to same 256 prefix must be considered same series (reuse). Truncate exactly 256, not 255. Boundary: 256 stays 256, 257→256, 500→256. Empty value stays empty, not nil.
- **Defensive copy for metric options**: `WithLabels` must copy input map — mutating original map after Counter/Gauge/Histogram creation must not affect stored labels (test mutates original map and expects Collect unchanged). Similarly `WithBuckets` must copy slice — mutating original slice after Histogram creation must not affect buckets. `WithDescription` string copy trivial but ensure not sharing. Also `WithLabels` called with nil map should be treated as empty labels, not panic.
- Counter `Add(delta)` >=0 only, ignore negative, NaN, Inf. Must be thread-safe. `Inc()` always +1. `Add(0)` allowed (value unchanged but not ignored). Gauge `Set(v)` must ignore NaN, Inf (no-op, keep previous value) — keep current value unchanged, not set to 0. `Add(delta)` for Gauge must ignore NaN, Inf (no-op). `Inc()`/`Dec()` for Gauge always +/-1 even when current value was set to something. Histogram `Observe(v)` ignore NaN, Inf. Also Gauge `Add(NaN)` no-op.
- Same name + same label set reuse same instrument (inc on one affects other). Label set equality after truncation and after sorting keys: labels `{"a":"1","b":"2"}` equals `{"b":"2","a":"1"}` same series. Distinct label sets are separate series. Reuse must be concurrent-safe. Truncation interaction: same after truncation considered same.
- Type conflict on the same name: the first registration fixes the metric's type. A later Counter/Gauge/Histogram call with the same name but a different type is a no-op — it returns a usable instrument whose operations are silently discarded, and Collect() still emits exactly one family for that name, with the original type and values. Example: Counter("m").Inc(), then Gauge("m").Set(100), then Histogram("m").Observe(5) → one family, Type == "counter", value 1. Type string in MetricFamily: "counter", "gauge", "histogram" lowercase. Also same name same type but different description: first description wins (Help preserved).
- Thread safety required for all operations including concurrent Collect and Add/Inc/Observe — no races, verified with `go run -race` 100 goroutines x 1000 ops. Collect concurrent with mutations must not race and must return snapshot. Collect must be safe to call concurrently from many goroutines.
- `Collect()` must return deep copy — must not expose internal mutable maps: mutating returned slice, family slice, Metrics slice, Labels map, Buckets slice, Buckets elements must not affect provider internal state; subsequent Collect must return original values. Returned Labels map must be non-nil when labels present (not nil map). When no labels, Labels may be empty non-nil map or nil? We require non-nil when labels present, but when no labels, empty map may be empty (non-nil or nil both accepted but we test non-nil when labels present). Buckets slice in returned samples must also be deep copy. Help string must be preserved.
- Histogram default buckets `[0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10]`. Cumulative inclusive: value == upper bound counts in that bucket. So Observe(1) with buckets [1,5,10] => bucket 1 count 1, bucket 5 count 1, bucket 10 count 1. Sorted ascending even if input unsorted. Deduplicate exact duplicates (keep first after sort). **Filtering**: buckets containing NaN or Inf or negative? Spec: Buckets should be finite positive? For hardening we require filtering out NaN and Inf buckets — they should be ignored (removed) and not counted as bucket. Input `[1, NaN, 5, Inf, 10, -5]` → after filtering NaN/Inf, we get `[1,5,10,-5]` sorted? But negative buckets maybe allowed? For simplicity, we filter only NaN/Inf, keep negative if present but sorted ascending. Example test: input `[5,1,NaN,Inf,5]` → after filter and dedup sorted → `[1,5]`. So implement filtering of NaN/Inf for buckets.
- Histogram Observe and bucket counts cumulative: each bucket counts values <= upper bound. Sum and Count fields must be accurate. Count increments per Observe, Sum adds value. Buckets Count is cumulative.
- Provider isolation: different providers must not share state (global map bug fails). Also same provider reused after Collect must still work (Collect does not clear).
- Histogram buckets handling: Must sort ascending even if input unsorted, dedup, filter NaN/Inf, and must be defensive copy. Changing original bucket slice after creation must not affect stored buckets. WithBuckets(nil) or WithBuckets(empty) → default buckets.
- Label truncation interaction with reuse: After truncation, same truncated key considered same. Test: labels with 500-char strings differing only after 256 chars should reuse same instrument and value 2.
- Invalid name/labels no-op must return instrument that is not nil and whose methods don't panic, but Collect does not include that family.
- Counter reuse with different description: first description wins, second description ignored but instrument still reuses.

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

- JSON per line: `timestamp` RFC3339Nano, `level`, `service`, `message`, plus trace correlation `trace_id`, `span_id`, `sampled` if ctx has TraceContext, optional `parent_id` if TraceContext ParentID non-empty. Fields: timestamp, level, service, message, plus any fields from With chain and per-log call. Custom field overwrite: last write wins for same key (With chain then per-call fields, per-call overwrites With). Must include `sampled` boolean when trace present. Service field always present.
- Thread-safe, `With` immutable copy (must copy underlying fields slice, not share array that could be mutated — use append to new slice, not sharing underlying), and must share output mutex for atomic writes across child loggers (child loggers created via With share same mutex, otherwise concurrent child logging races on underlying bytes.Buffer). Level filtering debug<info<warn<error> default info case-insensitive. WithOutput nil => fallback to os.Stderr, not panic. WithLevel unknown string => default info, empty string => info. Level "warning" alias for "warn".
- Atomic per-line write: concurrent logging from 100 goroutines (including child loggers from With chain sharing same output) must produce exactly 100 lines, each valid JSON, no interleaved bytes (use mutex around Write). Logger's output write must be protected by shared mutex.
- `With` chaining: base.With(a).With(b) must have both a and b, and child overriding parent same key. `With` called on logger with existing fields must not mutate original logger's fields (immutable). Concurrent With and log calls must be safe (no race). Defensive copy for With: mutating original Field slice or fields passed to With after call must not affect logger (copy slice and also copy Field values? Field Value is interface{} shallow copy okay but slice must be copied).
- No panic on nil ctx, nil fields, empty message, empty field key? Field with empty key should be ignored (no-op, not included in JSON). `Info(nil, "msg")` must not panic and must log without trace correlation. `With` with empty fields `With()` must return new logger copy with same fields (immutable).
- Timestamp: RFC3339Nano, parseable via time.Parse, must be UTC (contains Z or timezone), within test window (not zero, recent within 2 seconds of now), monotonic? At least recent.
- Level filtering: debug=0, info=1, warn=2, error=3. Default info. WithLevel "ERROR" case-insensitive must work. Filtering: log only if level >= minLevel. Level "warning" alias for warn. Unknown level fallback info.
- JSON escaping: must be valid JSON even when message or field values contain special chars like quotes, newline, etc.
- Field duplicate handling: if same key appears multiple times in With chain or per-call, last wins. Example base With env=dev, child With env=prod, Info with env=staging → final env=staging.
- Output writer may return error? Should not panic if Write fails, ignore error.
- ServiceName passed to NewLogger must be preserved in JSON service field.

## Constraints
- Stdlib only, `go vet ./...` and `go build ./...` pass.
- Files must exist: `tracing.go`, `metrics.go`, `logger.go`
- Thread safety mandatory
- Implement all public symbols above.

## Grading
Binary pass/fail based on all sub-tests.

Implement step1 fully; do not yet implement samplers/batch/cardinality — those step2.

- **Additional v6 edge cases (233 tests):** empty ParentID format 4 parts, invalid sampled handling, spaces around colons trimmed, defensive copy of TraceContext, TraceFromContext returns copy, WithAttributes nil no panic, overwrite after limit, exporter deep copy name, provider isolation multiple, logger With() no args, empty key ignored, timestamp monotonic, Counter Inc after Collect (Collect does NOT clear), histogram unsorted sort, duplicate dedup, Count/Sum accurate, Gauge Inc/Dec after Set, SpanContext alias same, Inject/Extract preserve sampled, labels order irrelevant sorted keys, uint type attributes allowed, plus all-zero TraceID/SpanID/ParentID invalid for marshal (must not write carrier, all-zero variants), Flags normalized in ContextWithTrace (Sampled true => Flags 1 even if input Flags 0, Sampled false => Flags 0), long attribute key allowed (no key length limit), marshal nil carrier and nil context no panic, unmarshal nil and empty carrier no panic returning background, WithAttributes mixed valid/invalid (slice/map/struct/nil/empty key ignored, valid kept), event attr duplicate last-wins and invalid truncation (nil/invalid type ignored, string truncate 1024), ContextWithTrace nil and empty handling, ParentID handling for root/child consistency, Attributes map deep copy key mutation isolation (mutating original map key after creation not affect), logger concurrent exact lines (100 goroutines -> 100 valid JSON lines counting), logger level debug shows all, logger no trace when invalid context (empty TraceID/SpanID), logger trace includes parent_id, metrics description first wins, histogram negative buckets sorted ascending, label key validation more (hyphen invalid), type conflict first wins (counter vs gauge vs histogram first registration wins).
