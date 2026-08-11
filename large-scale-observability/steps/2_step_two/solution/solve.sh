#!/bin/bash
set -e
mkdir -p /app/observability
cat > /app/observability/tracing.go <<'GO'
package observability

import (
    "context"
    "crypto/rand"
    "encoding/hex"
    "fmt"
    "regexp"
    "strconv"
    "strings"
    "sync"
    "time"
)

// ---- Core types ----
type TraceContext struct {
    TraceID  string
    SpanID   string
    ParentID string
    Sampled  bool
    Flags    byte
}
type SpanContext = TraceContext

type SpanStatus int
const (
    StatusUnset SpanStatus = 0
    StatusOK    SpanStatus = 1
    StatusError SpanStatus = 2
)
type StatusCode = SpanStatus

type SpanKind int
const (
    KindInternal SpanKind = 0
    KindServer   SpanKind = 1
    KindClient   SpanKind = 2
    SpanKindInternal SpanKind = KindInternal
    SpanKindServer   SpanKind = KindServer
    SpanKindClient   SpanKind = KindClient
)

type Attribute struct {
    Key   string
    Value interface{}
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

type IDGenerator interface {
    NewTraceID() string
    NewSpanID() string
}
type defaultIDGenerator struct{}
func (g *defaultIDGenerator) NewTraceID() string {
    b := make([]byte, 16)
    _, _ = rand.Read(b)
    return hex.EncodeToString(b)
}
func (g *defaultIDGenerator) NewSpanID() string {
    b := make([]byte, 8)
    _, _ = rand.Read(b)
    return hex.EncodeToString(b)
}

// ---- Sampling with prior-violating semantics ----
type SamplingDecision int
const (
    DecisionDrop SamplingDecision = iota
    DecisionKeep
    DecisionRecordOnly
    DecisionRecordAndSample = DecisionKeep
)
const (
    SamplingDrop = DecisionDrop
    SamplingKeep = DecisionKeep
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

type alwaysSampler struct{}
func NewAlwaysSampler() Sampler { return &alwaysSampler{} }
func (s *alwaysSampler) ShouldSample(p SamplingRequest) SamplingDecision { return DecisionKeep }
func (s *alwaysSampler) Description() string { return "AlwaysSampler" }
func NewAlwaysOnSampler() Sampler { return NewAlwaysSampler() }

type neverSampler struct{}
func NewNeverSampler() Sampler { return &neverSampler{} }
func (s *neverSampler) ShouldSample(p SamplingRequest) SamplingDecision { return DecisionDrop }
func (s *neverSampler) Description() string { return "NeverSampler" }
func NewAlwaysOffSampler() Sampler { return NewNeverSampler() }

type ratioSampler struct{ fraction float64 }

func NewRatioSampler(fraction float64) Sampler { return &ratioSampler{fraction: fraction} }
func NewTraceIDRatioSampler(fraction float64) Sampler { return NewRatioSampler(fraction) }

func (s *ratioSampler) Description() string { return fmt.Sprintf("RatioSampler{%.4f}", s.fraction) }

func (s *ratioSampler) ShouldSample(p SamplingRequest) SamplingDecision {
    // error/critical override — domain-specific, breaks OTel recall
    if p.Status == StatusError || strings.ToLower(p.Priority) == "critical" {
        return DecisionKeep
    }
    if s.fraction <= 0 {
        return DecisionDrop
    }
    if s.fraction >= 1 {
        return DecisionKeep
    }
    if len(p.TraceID) != 32 {
        return DecisionDrop
    }
    for _, ch := range p.TraceID {
        if !((ch >= '0' && ch <= '9') || (ch >= 'a' && ch <= 'f') || (ch >= 'A' && ch <= 'F')) {
            return DecisionDrop
        }
    }
    // last 8 hex chars as uint32 / 2^32 — prior-violating vs OTel first 16 hex uint64
    last8 := p.TraceID[24:32]
    val, err := strconv.ParseUint(last8, 16, 32)
    if err != nil {
        return DecisionDrop
    }
    threshold := s.fraction * float64(^uint32(0))
    // Actually 2^32 = 4294967296 = max uint32 +1. Use ^uint32(0) = 4294967295, close enough, but to be precise use 4294967296
    if float64(val) < s.fraction*4294967296.0 {
        _ = threshold
        return DecisionKeep
    }
    if float64(val) < threshold {
        return DecisionKeep
    }
    return DecisionDrop
}

type parentAwareSampler struct{ root Sampler }

func NewParentAwareSampler(root Sampler) Sampler {
    if root == nil { root = NewAlwaysSampler() }
    return &parentAwareSampler{root: root}
}
func NewParentBasedSampler(root Sampler) Sampler { return NewParentAwareSampler(root) }

func (s *parentAwareSampler) Description() string {
    return fmt.Sprintf("ParentAware{root=%s}", s.root.Description())
}
func (s *parentAwareSampler) ShouldSample(p SamplingRequest) SamplingDecision {
    if !p.HasParent {
        return s.root.ShouldSample(p)
    }
    if !p.Parent.Sampled {
        return DecisionDrop
    }
    // AND logic: parent sampled true, but must also pass root
    return s.root.ShouldSample(p)
}

// ---- Tracer options ----
type tracerConfig struct {
    serviceName string
    processor   Processor
    idGen       IDGenerator
    sampler     Sampler
}
type TracerOption func(*tracerConfig)
func WithServiceName(name string) TracerOption { return func(c *tracerConfig) { c.serviceName = name } }
func WithProcessor(p Processor) TracerOption { return func(c *tracerConfig) { c.processor = p } }
func WithSpanProcessor(p Processor) TracerOption { return func(c *tracerConfig) { c.processor = p } }
func WithIDGenerator(gen IDGenerator) TracerOption { return func(c *tracerConfig) { c.idGen = gen } }
func WithSampler(s Sampler) TracerOption { return func(c *tracerConfig) { c.sampler = s } }

type spanStartConfig struct {
    attributes []Attribute
    kind       SpanKind
    parent     *TraceContext
}
type SpanStartOption func(*spanStartConfig)
func WithAttributes(attrs ...Attribute) SpanStartOption {
    return func(c *spanStartConfig) { c.attributes = append(c.attributes, attrs...) }
}
func WithSpanKind(k SpanKind) SpanStartOption { return func(c *spanStartConfig) { c.kind = k } }
func WithParent(sc TraceContext) SpanStartOption { return func(c *spanStartConfig) { c.parent = &sc } }

type traceContextKey struct{}
func ContextWithTrace(ctx context.Context, tc TraceContext) context.Context {
    if ctx == nil { ctx = context.Background() }
    return context.WithValue(ctx, traceContextKey{}, tc)
}
func TraceFromContext(ctx context.Context) (TraceContext, bool) {
    if ctx == nil { return TraceContext{}, false }
    sc, ok := ctx.Value(traceContextKey{}).(TraceContext)
    return sc, ok
}
func ContextWithSpanContext(ctx context.Context, sc TraceContext) context.Context { return ContextWithTrace(ctx, sc) }
func SpanContextFromContext(ctx context.Context) (TraceContext, bool) { return TraceFromContext(ctx) }

var (
    hex32Regex = regexp.MustCompile(`^[0-9a-fA-F]{32}$`)
    hex16Regex = regexp.MustCompile(`^[0-9a-fA-F]{16}$`)
)

func MarshalTrace(ctx context.Context, carrier map[string]string) {
    if carrier == nil { return }
    sc, ok := TraceFromContext(ctx)
    if !ok { return }
    if !hex32Regex.MatchString(sc.TraceID) || !hex16Regex.MatchString(sc.SpanID) { return }
    if sc.ParentID != "" && !hex16Regex.MatchString(sc.ParentID) { return }
    samp := "0"
    if sc.Sampled { samp = "1" }
    carrier["x-ride-trace"] = fmt.Sprintf("%s:%s:%s:%s", sc.TraceID, sc.SpanID, sc.ParentID, samp)
}
func UnmarshalTrace(carrier map[string]string) context.Context {
    if carrier == nil { return context.Background() }
    val, ok := carrier["x-ride-trace"]
    if !ok { return context.Background() }
    parts := strings.Split(val, ":")
    if len(parts) != 4 { return context.Background() }
    tid, sid, pid, sampStr := parts[0], parts[1], parts[2], parts[3]
    if !hex32Regex.MatchString(tid) || !hex16Regex.MatchString(sid) { return context.Background() }
    if pid != "" && !hex16Regex.MatchString(pid) { return context.Background() }
    sampled := sampStr == "1"
    tc := TraceContext{TraceID: tid, SpanID: sid, ParentID: pid, Sampled: sampled}
    if sampled { tc.Flags = 1 }
    return ContextWithTrace(context.Background(), tc)
}
func Inject(ctx context.Context, carrier map[string]string) { MarshalTrace(ctx, carrier) }
func Extract(carrier map[string]string) context.Context { return UnmarshalTrace(carrier) }

// ---- Exporter ----
type MemoryExporter struct {
    mu    sync.Mutex
    spans []FinishedSpan
}
func NewMemoryExporter() *MemoryExporter { return &MemoryExporter{} }
func NewInMemoryExporter() *MemoryExporter { return NewMemoryExporter() }
func (e *MemoryExporter) ExportSpans(ctx context.Context, spans []FinishedSpan) error {
    e.mu.Lock()
    defer e.mu.Unlock()
    for _, s := range spans {
        cpy := s
        if s.Attributes != nil {
            cpy.Attributes = make(map[string]interface{}, len(s.Attributes))
            for k, v := range s.Attributes { cpy.Attributes[k] = v }
        }
        if s.Events != nil {
            cpy.Events = make([]SpanEvent, len(s.Events))
            for j, ev := range s.Events {
                ne := ev
                if ev.Attributes != nil {
                    ne.Attributes = append([]Attribute(nil), ev.Attributes...)
                }
                cpy.Events[j] = ne
            }
        }
        e.spans = append(e.spans, cpy)
    }
    return nil
}
func (e *MemoryExporter) GetSpans() []FinishedSpan {
    e.mu.Lock()
    defer e.mu.Unlock()
    cpy := make([]FinishedSpan, len(e.spans))
    for i, s := range e.spans {
        ns := s
        if s.Attributes != nil {
            ns.Attributes = make(map[string]interface{}, len(s.Attributes))
            for k, v := range s.Attributes { ns.Attributes[k] = v }
        }
        if s.Events != nil {
            ns.Events = make([]SpanEvent, len(s.Events))
            for j, ev := range s.Events {
                ne := ev
                if ev.Attributes != nil {
                    ne.Attributes = append([]Attribute(nil), ev.Attributes...)
                }
                ns.Events[j] = ne
            }
        }
        cpy[i] = ns
    }
    return cpy
}
func (e *MemoryExporter) Clear() { e.mu.Lock(); defer e.mu.Unlock(); e.spans = nil }
func (e *MemoryExporter) GetCount() int { e.mu.Lock(); defer e.mu.Unlock(); return len(e.spans) }
type InMemoryExporter = MemoryExporter

type simpleProcessor struct{ exporter Exporter }
func NewSimpleProcessor(exporter Exporter) Processor { return &simpleProcessor{exporter: exporter} }
func NewSimpleSpanProcessor(exporter Exporter) Processor { return NewSimpleProcessor(exporter) }
func (p *simpleProcessor) OnStart(ctx context.Context, span FinishedSpan) {}
func (p *simpleProcessor) OnEnd(span FinishedSpan) { _ = p.exporter.ExportSpans(context.Background(), []FinishedSpan{span}) }
func (p *simpleProcessor) Shutdown(ctx context.Context) error { return nil }
func (p *simpleProcessor) ForceFlush(ctx context.Context) error { return nil }

type tracerImpl struct {
    serviceName string
    processor   Processor
    idGen       IDGenerator
    sampler     Sampler
}
func NewTracer(serviceName string, opts ...TracerOption) Tracer {
    cfg := &tracerConfig{serviceName: serviceName, idGen: &defaultIDGenerator{}, sampler: NewAlwaysSampler()}
    for _, o := range opts { if o!=nil { o(cfg) } }
    if cfg.serviceName == "" { cfg.serviceName = serviceName }
    if cfg.processor == nil { cfg.processor = &simpleProcessor{exporter: NewMemoryExporter()} }
    if cfg.idGen == nil { cfg.idGen = &defaultIDGenerator{} }
    if cfg.sampler == nil { cfg.sampler = NewAlwaysSampler() }
    return &tracerImpl{serviceName: cfg.serviceName, processor: cfg.processor, idGen: cfg.idGen, sampler: cfg.sampler}
}

type spanImpl struct {
    mu            sync.Mutex
    name          string
    traceContext  TraceContext
    parentID      string
    kind          SpanKind
    startTime     time.Time
    endTime       time.Time
    attributes    map[string]interface{}
    events        []SpanEvent
    statusCode    SpanStatus
    statusMessage string
    serviceName   string
    processor     Processor
    ended         bool
    recording     bool
}

func (t *tracerImpl) Start(ctx context.Context, name string, opts ...SpanStartOption) (context.Context, Span) {
    cfg := &spanStartConfig{kind: KindInternal}
    for _, o := range opts { if o!=nil { o(cfg) } }
    var parentSC *TraceContext
    if cfg.parent != nil { parentSC = cfg.parent } else if sc, ok := TraceFromContext(ctx); ok { tmp := sc; parentSC = &tmp }
    var traceID string
    var parentSpanID string
    var hasParent bool
    var parentCtx TraceContext
    if parentSC != nil {
        traceID = parentSC.TraceID
        parentSpanID = parentSC.SpanID
        hasParent = true
        parentCtx = *parentSC
    } else {
        traceID = t.idGen.NewTraceID()
        parentSpanID = ""
        hasParent = false
    }
    spanID := t.idGen.NewSpanID()

    // extract priority from attributes
    var priority string
    for _, a := range cfg.attributes {
        if a.Key == "priority" {
            if s, ok := a.Value.(string); ok { priority = s }
        }
    }

    samplingParams := SamplingRequest{
        TraceID:    traceID,
        SpanName:   name,
        Kind:       cfg.kind,
        Parent:     parentCtx,
        HasParent:  hasParent,
        Attributes: cfg.attributes,
        Status:     StatusUnset,
        Priority:   priority,
    }
    decision := t.sampler.ShouldSample(samplingParams)
    sampled := decision == DecisionKeep
    recording := sampled

    sc := TraceContext{TraceID: traceID, SpanID: spanID, ParentID: parentSpanID, Sampled: sampled}
    if sampled { sc.Flags = 1 }

    span := &spanImpl{name: name, traceContext: sc, parentID: parentSpanID, kind: cfg.kind, startTime: time.Now(), attributes: make(map[string]interface{}), events: []SpanEvent{}, statusCode: StatusUnset, serviceName: t.serviceName, processor: t.processor, recording: recording}

    for _, a := range cfg.attributes {
        if len(span.attributes) >= 128 { break }
        val := a.Value
        if s, ok := val.(string); ok && len(s) > 1024 { val = s[:1024] }
        span.attributes[a.Key] = val
    }

    if recording {
        rs := FinishedSpan{Name: span.name, SpanContext: span.traceContext, ParentID: span.parentID, Kind: span.kind, StartTime: span.startTime, Attributes: copyMap(span.attributes), ServiceName: span.serviceName}
        t.processor.OnStart(ctx, rs)
    }

    newCtx := ContextWithTrace(ctx, sc)
    return newCtx, span
}

func copyMap(in map[string]interface{}) map[string]interface{} {
    if in == nil { return nil }
    out := make(map[string]interface{}, len(in))
    for k, v := range in { out[k] = v }
    return out
}

func (s *spanImpl) End() {
    s.mu.Lock()
    if s.ended { s.mu.Unlock(); return }
    s.ended = true
    s.endTime = time.Now()
    attrs := copyMap(s.attributes)
    events := append([]SpanEvent(nil), s.events...)
    sc := s.traceContext
    parentID := s.parentID
    name := s.name
    kind := s.kind
    start := s.startTime
    end := s.endTime
    status := s.statusCode
    statusMsg := s.statusMessage
    service := s.serviceName
    recording := s.recording
    processor := s.processor
    s.mu.Unlock()
    if !recording { return }
    rs := FinishedSpan{Name: name, SpanContext: sc, ParentID: parentID, Kind: kind, StartTime: start, EndTime: end, Attributes: attrs, Events: events, StatusCode: status, StatusMessage: statusMsg, ServiceName: service}
    processor.OnEnd(rs)
}

func (s *spanImpl) AddAttribute(key string, value interface{}) {
    s.mu.Lock()
    defer s.mu.Unlock()
    if s.ended || !s.recording { return }
    if len(s.attributes) >= 128 { return }
    if str, ok := value.(string); ok && len(str) > 1024 { value = str[:1024] }
    s.attributes[key] = value
}
func (s *spanImpl) AddEvent(name string, attrs ...Attribute) {
    s.mu.Lock()
    defer s.mu.Unlock()
    if s.ended || !s.recording { return }
    if len(s.events) >= 128 { return }
    ev := SpanEvent{Name: name, Timestamp: time.Now()}
    if len(attrs) > 0 { ev.Attributes = append([]Attribute(nil), attrs...) }
    s.events = append(s.events, ev)
}
func (s *spanImpl) SetStatus(code SpanStatus, message string) {
    s.mu.Lock()
    defer s.mu.Unlock()
    if s.ended || !s.recording { return }
    s.statusCode = code
    s.statusMessage = message
}
func (s *spanImpl) Context() TraceContext { s.mu.Lock(); defer s.mu.Unlock(); return s.traceContext }
func (s *spanImpl) SpanContext() TraceContext { return s.Context() }
func (s *spanImpl) IsRecording() bool { s.mu.Lock(); defer s.mu.Unlock(); return s.recording && !s.ended }

// ---- Batch processor with evict-oldest + block-and-drain ----
type batchConfig struct {
    batchSize     int
    queueSize     int
    batchTimeout  time.Duration
    exportTimeout time.Duration
}
type BatchOption func(*batchConfig)
type BatchSpanProcessorOption = BatchOption
func WithBatchSize(n int) BatchOption { return func(c *batchConfig) { c.batchSize = n } }
func WithQueueSize(n int) BatchOption { return func(c *batchConfig) { c.queueSize = n } }
func WithBatchTimeout(d time.Duration) BatchOption { return func(c *batchConfig) { c.batchTimeout = d } }
func WithExportTimeout(d time.Duration) BatchOption { return func(c *batchConfig) { c.exportTimeout = d } }
func WithMaxBatchSize(n int) BatchOption { return func(c *batchConfig) { c.batchSize = n } }
func WithMaxExportBatchSize(n int) BatchOption { return func(c *batchConfig) { c.batchSize = n } }
func WithQueueLimit(n int) BatchOption { return WithQueueSize(n) }
func WithBatchCapacity(n int) BatchOption { return WithBatchSize(n) }
func WithFlushPeriod(d time.Duration) BatchOption { return WithBatchTimeout(d) }
func WithExportDeadline(d time.Duration) BatchOption { return WithExportTimeout(d) }

type batchProcessor struct {
    exporter      Exporter
    mu            sync.Mutex
    queue         []FinishedSpan
    batch         []FinishedSpan
    queueSize     int
    batchSize     int
    batchTimeout  time.Duration
    exportTimeout time.Duration
    stopCh        chan struct{}
    stopped       bool
    flushing      bool
    dropped       int
    droppedMu     sync.Mutex
    wg            sync.WaitGroup
    shutdownOnce  sync.Once
}

func NewBatchProcessor(exporter Exporter, opts ...BatchOption) Processor {
    cfg := &batchConfig{batchSize: 512, queueSize: 2048, batchTimeout: 5 * time.Second, exportTimeout: 30 * time.Second}
    for _, o := range opts { if o!=nil { o(cfg) } }
    if cfg.batchSize <= 0 { cfg.batchSize = 512 }
    if cfg.queueSize <= 0 { cfg.queueSize = 2048 }
    if cfg.batchTimeout <= 0 { cfg.batchTimeout = 5 * time.Second }
    if cfg.exportTimeout <= 0 { cfg.exportTimeout = 30 * time.Second }

    b := &batchProcessor{
        exporter:      exporter,
        queue:         make([]FinishedSpan, 0, cfg.queueSize),
        batch:         make([]FinishedSpan, 0, cfg.batchSize),
        queueSize:     cfg.queueSize,
        batchSize:     cfg.batchSize,
        batchTimeout:  cfg.batchTimeout,
        exportTimeout: cfg.exportTimeout,
        stopCh:        make(chan struct{}),
    }
    b.wg.Add(1)
    go b.run()
    return b
}
func NewBatchSpanProcessor(exporter Exporter, opts ...BatchOption) Processor {
    return NewBatchProcessor(exporter, opts...)
}

func (b *batchProcessor) run() {
    defer b.wg.Done()
    ticker := time.NewTicker(b.batchTimeout)
    defer ticker.Stop()
    for {
        select {
        case <-b.stopCh:
            b.mu.Lock()
            // drain queue + batch
            all := append([]FinishedSpan(nil), b.batch...)
            all = append(all, b.queue...)
            b.batch = nil
            b.queue = nil
            b.mu.Unlock()
            for len(all) > 0 {
                n := b.batchSize
                if n <= 0 { n = len(all) }
                if n > len(all) { n = len(all) }
                chunk := all[:n]
                all = all[n:]
                b.exportWithTimeout(chunk)
            }
            return
        case <-ticker.C:
            b.mu.Lock()
            if len(b.batch) > 0 {
                toExport := b.batch
                b.batch = make([]FinishedSpan, 0, b.batchSize)
                b.mu.Unlock()
                b.exportWithTimeout(toExport)
            } else {
                b.mu.Unlock()
            }
        default:
            b.mu.Lock()
            if len(b.queue) > 0 && len(b.batch) < b.batchSize {
                needed := b.batchSize - len(b.batch)
                take := needed
                if take > len(b.queue) { take = len(b.queue) }
                b.batch = append(b.batch, b.queue[:take]...)
                b.queue = b.queue[take:]
            }
            var toExport []FinishedSpan
            if len(b.batch) >= b.batchSize {
                toExport = b.batch
                b.batch = make([]FinishedSpan, 0, b.batchSize)
            }
            b.mu.Unlock()
            if len(toExport) > 0 {
                b.exportWithTimeout(toExport)
                ticker.Reset(b.batchTimeout)
            } else {
                time.Sleep(10 * time.Millisecond)
            }
        }
    }
}

func (b *batchProcessor) exportWithTimeout(spans []FinishedSpan) {
    if len(spans) == 0 { return }
    ctx, cancel := context.WithTimeout(context.Background(), b.exportTimeout)
    defer cancel()
    done := make(chan error, 1)
    go func() { done <- b.exporter.ExportSpans(ctx, spans) }()
    select {
    case <-ctx.Done():
        return
    case <-done:
        return
    }
}

func (b *batchProcessor) OnStart(ctx context.Context, span FinishedSpan) {}

func (b *batchProcessor) OnEnd(span FinishedSpan) {
    for {
        b.mu.Lock()
        if b.stopped {
            b.mu.Unlock()
            return
        }
        if len(b.queue) < b.queueSize {
            b.queue = append(b.queue, span)
            b.mu.Unlock()
            return
        }
        // queue full
        if b.flushing {
            // block-and-drain: wait until space, do NOT drop
            b.mu.Unlock()
            time.Sleep(5 * time.Millisecond)
            continue
        } else {
            // evict oldest
            if len(b.queue) > 0 {
                b.queue = b.queue[1:]
                b.droppedMu.Lock()
                b.dropped++
                b.droppedMu.Unlock()
            }
            // now has space, enqueue newest
            if len(b.queue) < b.queueSize {
                b.queue = append(b.queue, span)
                b.mu.Unlock()
                return
            }
            // still full due to race, loop again
            b.mu.Unlock()
            continue
        }
    }
}

func (b *batchProcessor) ForceFlush(ctx context.Context) error {
    b.mu.Lock()
    if b.stopped {
        b.mu.Unlock()
        return nil
    }
    b.flushing = true
    b.mu.Unlock()
    defer func() {
        b.mu.Lock()
        b.flushing = false
        b.mu.Unlock()
    }()

    for {
        select {
        case <-ctx.Done():
            return ctx.Err()
        default:
        }
        b.mu.Lock()
        if len(b.queue) == 0 && len(b.batch) == 0 {
            b.mu.Unlock()
            return nil
        }
        if len(b.queue) > 0 {
            needed := b.batchSize - len(b.batch)
            if needed > 0 {
                take := needed
                if take > len(b.queue) { take = len(b.queue) }
                b.batch = append(b.batch, b.queue[:take]...)
                b.queue = b.queue[take:]
            }
        }
        var toExport []FinishedSpan
        if len(b.batch) > 0 {
            toExport = b.batch
            b.batch = make([]FinishedSpan, 0, b.batchSize)
        }
        b.mu.Unlock()
        if len(toExport) > 0 {
            b.exportWithTimeout(toExport)
        }
        time.Sleep(5 * time.Millisecond)
    }
}

func (b *batchProcessor) Shutdown(ctx context.Context) error {
    var err error
    b.shutdownOnce.Do(func() {
        b.mu.Lock()
        b.stopped = true
        b.mu.Unlock()
        close(b.stopCh)
        done := make(chan struct{})
        go func() { b.wg.Wait(); close(done) }()
        select {
        case <-done:
        case <-ctx.Done():
            err = ctx.Err()
        }
    })
    return err
}

func (b *batchProcessor) DroppedCount() int {
    b.droppedMu.Lock()
    defer b.droppedMu.Unlock()
    return b.dropped
}
func (b *batchProcessor) QueueLen() int {
    b.mu.Lock()
    defer b.mu.Unlock()
    return len(b.queue)
}

GO
cat > /app/observability/metrics.go <<'GO'
package observability

import (
    "math"
    "regexp"
    "sort"
    "sync"
)

func isNaNOrInf(f float64) bool { return math.IsNaN(f) || math.IsInf(f, 0) }

type Counter interface { Inc(); Add(delta float64) }
type Gauge interface { Set(v float64); Inc(); Dec(); Add(delta float64) }
type Histogram interface { Observe(v float64) }

type metricDesc struct {
    labels      map[string]string
    description string
    buckets     []float64
}
type MetricOption func(*metricDesc)
func WithLabels(labels map[string]string) MetricOption {
    return func(m *metricDesc) {
        if labels == nil { m.labels = nil; return }
        cp := make(map[string]string, len(labels))
        for k, v := range labels { cp[k] = v }
        m.labels = cp
    }
}
func WithDescription(desc string) MetricOption { return func(m *metricDesc) { m.description = desc } }
func WithBuckets(buckets []float64) MetricOption {
    return func(m *metricDesc) {
        if buckets == nil { m.buckets = nil; return }
        cp := make([]float64, len(buckets))
        copy(cp, buckets)
        m.buckets = cp
    }
}

type providerConfig struct {
    maxCardinality int
    overflowMode   string
}
type MetricsProviderOption func(*providerConfig)
type MetricsOption func(*providerConfig)
func WithMaxCardinality(n int) MetricsProviderOption { return func(c *providerConfig) { c.maxCardinality = n } }
func WithCardinalityOverflowHandling(mode string) MetricsProviderOption { return func(c *providerConfig) { c.overflowMode = mode } }

type MetricFamily struct {
    Name    string
    Type    string
    Help    string
    Metrics []MetricSample
}
type MetricSample struct {
    Labels  map[string]string
    Value   float64
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
    DroppedSeriesCount() int
}

var (
    metricNameRegex = regexp.MustCompile(`^[a-zA-Z_][a-zA-Z0-9_]*$`)
    labelKeyRegex   = regexp.MustCompile(`^[a-zA-Z_][a-zA-Z0-9_]*$`)
)
var defaultHistogramBuckets = []float64{0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10}

type noopCounter struct{}
func (n *noopCounter) Inc() {}
func (n *noopCounter) Add(delta float64) {}
type noopGauge struct{}
func (n *noopGauge) Set(v float64) {}
func (n *noopGauge) Inc() {}
func (n *noopGauge) Dec() {}
func (n *noopGauge) Add(delta float64) {}
type noopHistogram struct{}
func (n *noopHistogram) Observe(v float64) {}

type counterImpl struct {
    mu     sync.Mutex
    value  float64
    labels map[string]string
}
func (c *counterImpl) Inc() { c.mu.Lock(); c.value++; c.mu.Unlock() }
func (c *counterImpl) Add(delta float64) {
    if delta < 0 || isNaNOrInf(delta) { return }
    c.mu.Lock(); c.value += delta; c.mu.Unlock()
}
func (c *counterImpl) getValue() float64 { c.mu.Lock(); defer c.mu.Unlock(); return c.value }

type gaugeImpl struct {
    mu     sync.Mutex
    value  float64
    labels map[string]string
}
func (g *gaugeImpl) Set(v float64) { g.mu.Lock(); g.value = v; g.mu.Unlock() }
func (g *gaugeImpl) Inc() { g.mu.Lock(); g.value++; g.mu.Unlock() }
func (g *gaugeImpl) Dec() { g.mu.Lock(); g.value--; g.mu.Unlock() }
func (g *gaugeImpl) Add(delta float64) { g.mu.Lock(); g.value += delta; g.mu.Unlock() }
func (g *gaugeImpl) getValue() float64 { g.mu.Lock(); defer g.mu.Unlock(); return g.value }

type histogramImpl struct {
    mu           sync.Mutex
    count        uint64
    sum          float64
    buckets      []float64
    bucketCounts []uint64
    labels       map[string]string
}
func newHistogramImpl(labels map[string]string, buckets []float64) *histogramImpl {
    if len(buckets) == 0 { buckets = append([]float64(nil), defaultHistogramBuckets...) } else {
        cp := append([]float64(nil), buckets...)
        sort.Float64s(cp)
        buckets = cp
    }
    return &histogramImpl{buckets: buckets, bucketCounts: make([]uint64, len(buckets)), labels: copyLabels(labels)}
}
func (h *histogramImpl) Observe(v float64) {
    if isNaNOrInf(v) { return }
    h.mu.Lock()
    defer h.mu.Unlock()
    h.count++
    h.sum += v
    for i, ub := range h.buckets {
        if v <= ub { h.bucketCounts[i]++ }
    }
}
func (h *histogramImpl) snapshot() (count uint64, sum float64, buckets []float64, counts []uint64) {
    h.mu.Lock()
    defer h.mu.Unlock()
    count = h.count
    sum = h.sum
    buckets = append([]float64(nil), h.buckets...)
    counts = append([]uint64(nil), h.bucketCounts...)
    return
}

func copyLabels(in map[string]string) map[string]string {
    if in == nil { return map[string]string{} }
    out := make(map[string]string, len(in))
    for k, v := range in {
        if len(v) > 256 { v = v[:256] }
        out[k] = v
    }
    return out
}
func labelsKey(labels map[string]string) string {
    if len(labels) == 0 { return "" }
    keys := make([]string, 0, len(labels))
    for k := range labels { keys = append(keys, k) }
    sort.Strings(keys)
    s := ""
    for i, k := range keys {
        if i > 0 { s += "," }
        s += k + "=" + labels[k]
    }
    return s
}
func isValidMetricName(name string) bool { return metricNameRegex.MatchString(name) }
func isValidLabels(labels map[string]string) bool {
    for k := range labels { if !labelKeyRegex.MatchString(k) { return false } }
    return true
}

type metricsProvider struct {
    mu                 sync.RWMutex
    counters           map[string]map[string]*counterImpl
    gauges             map[string]map[string]*gaugeImpl
    histograms         map[string]map[string]*histogramImpl
    overflowCounters   map[string]*counterImpl
    overflowGauges     map[string]*gaugeImpl
    overflowHistograms map[string]*histogramImpl
    familyHelp         map[string]string
    maxCardinality     int
    overflowMode       string
    dropped            int64
    droppedMu          sync.Mutex
}

func NewMetricsProvider(opts ...MetricsProviderOption) MetricsProvider {
    cfg := &providerConfig{maxCardinality: 0, overflowMode: "drop"}
    for _, o := range opts { if o!=nil { o(cfg) } }
    if cfg.overflowMode != "aggregate" { cfg.overflowMode = "drop" }
    return &metricsProvider{
        counters:           make(map[string]map[string]*counterImpl),
        gauges:             make(map[string]map[string]*gaugeImpl),
        histograms:         make(map[string]map[string]*histogramImpl),
        overflowCounters:   make(map[string]*counterImpl),
        overflowGauges:     make(map[string]*gaugeImpl),
        overflowHistograms: make(map[string]*histogramImpl),
        familyHelp:         make(map[string]string),
        maxCardinality:     cfg.maxCardinality,
        overflowMode:       cfg.overflowMode,
    }
}

func (p *metricsProvider) Counter(name string, opts ...MetricOption) Counter {
    if !isValidMetricName(name) { return &noopCounter{} }
    desc := &metricDesc{}
    for _, o := range opts { if o!=nil { o(desc) } }
    labels := desc.labels
    if labels == nil { labels = map[string]string{} }
    if !isValidLabels(labels) { return &noopCounter{} }
    key := labelsKey(labels)
    p.mu.Lock()
    defer p.mu.Unlock()
    if _, ok := p.gauges[name]; ok { return &noopCounter{} }
    if _, ok := p.histograms[name]; ok { return &noopCounter{} }
    if _, ok := p.counters[name]; !ok { p.counters[name] = make(map[string]*counterImpl) }
    inner := p.counters[name]
    if existing, ok := inner[key]; ok { return existing }
    if p.maxCardinality > 0 && len(inner) >= p.maxCardinality {
        if p.overflowMode == "aggregate" {
            if oc, ok := p.overflowCounters[name]; ok { return oc }
            overflowLabels := map[string]string{"__overflow__": "true"}
            oc := &counterImpl{labels: overflowLabels}
            p.overflowCounters[name] = oc
            return oc
        } else {
            p.droppedMu.Lock()
            p.dropped++
            p.droppedMu.Unlock()
            return &noopCounter{}
        }
    }
    ci := &counterImpl{labels: copyLabels(labels)}
    inner[key] = ci
    if desc.description != "" { if _, exists := p.familyHelp[name]; !exists { p.familyHelp[name] = desc.description } }
    return ci
}

func (p *metricsProvider) Gauge(name string, opts ...MetricOption) Gauge {
    if !isValidMetricName(name) { return &noopGauge{} }
    desc := &metricDesc{}
    for _, o := range opts { if o!=nil { o(desc) } }
    labels := desc.labels
    if labels == nil { labels = map[string]string{} }
    if !isValidLabels(labels) { return &noopGauge{} }
    key := labelsKey(labels)
    p.mu.Lock()
    defer p.mu.Unlock()
    if _, ok := p.counters[name]; ok { return &noopGauge{} }
    if _, ok := p.histograms[name]; ok { return &noopGauge{} }
    if _, ok := p.gauges[name]; !ok { p.gauges[name] = make(map[string]*gaugeImpl) }
    inner := p.gauges[name]
    if existing, ok := inner[key]; ok { return existing }
    if p.maxCardinality > 0 && len(inner) >= p.maxCardinality {
        if p.overflowMode == "aggregate" {
            if og, ok := p.overflowGauges[name]; ok { return og }
            og := &gaugeImpl{labels: map[string]string{"__overflow__": "true"}}
            p.overflowGauges[name] = og
            return og
        } else {
            p.droppedMu.Lock()
            p.dropped++
            p.droppedMu.Unlock()
            return &noopGauge{}
        }
    }
    gi := &gaugeImpl{labels: copyLabels(labels)}
    inner[key] = gi
    if desc.description != "" { if _, exists := p.familyHelp[name]; !exists { p.familyHelp[name] = desc.description } }
    return gi
}

func (p *metricsProvider) Histogram(name string, opts ...MetricOption) Histogram {
    if !isValidMetricName(name) { return &noopHistogram{} }
    desc := &metricDesc{}
    for _, o := range opts { if o!=nil { o(desc) } }
    labels := desc.labels
    if labels == nil { labels = map[string]string{} }
    if !isValidLabels(labels) { return &noopHistogram{} }
    key := labelsKey(labels)
    p.mu.Lock()
    defer p.mu.Unlock()
    if _, ok := p.counters[name]; ok { return &noopHistogram{} }
    if _, ok := p.gauges[name]; ok { return &noopHistogram{} }
    if _, ok := p.histograms[name]; !ok { p.histograms[name] = make(map[string]*histogramImpl) }
    inner := p.histograms[name]
    if existing, ok := inner[key]; ok { return existing }
    if p.maxCardinality > 0 && len(inner) >= p.maxCardinality {
        if p.overflowMode == "aggregate" {
            if oh, ok := p.overflowHistograms[name]; ok { return oh }
            oh := newHistogramImpl(map[string]string{"__overflow__": "true"}, desc.buckets)
            p.overflowHistograms[name] = oh
            return oh
        } else {
            p.droppedMu.Lock()
            p.dropped++
            p.droppedMu.Unlock()
            return &noopHistogram{}
        }
    }
    hi := newHistogramImpl(labels, desc.buckets)
    inner[key] = hi
    if desc.description != "" { if _, exists := p.familyHelp[name]; !exists { p.familyHelp[name] = desc.description } }
    return hi
}

func (p *metricsProvider) Collect() []MetricFamily {
    p.mu.RLock()
    defer p.mu.RUnlock()
    var families []MetricFamily
    for name, inner := range p.counters {
        fam := MetricFamily{Name: name, Type: "counter", Help: p.familyHelp[name]}
        for _, ci := range inner {
            fam.Metrics = append(fam.Metrics, MetricSample{Labels: copyLabels(ci.labels), Value: ci.getValue()})
        }
        if oc, ok := p.overflowCounters[name]; ok {
            fam.Metrics = append(fam.Metrics, MetricSample{Labels: copyLabels(oc.labels), Value: oc.getValue()})
        }
        families = append(families, fam)
    }
    for name, inner := range p.gauges {
        fam := MetricFamily{Name: name, Type: "gauge", Help: p.familyHelp[name]}
        for _, gi := range inner {
            fam.Metrics = append(fam.Metrics, MetricSample{Labels: copyLabels(gi.labels), Value: gi.getValue()})
        }
        if og, ok := p.overflowGauges[name]; ok {
            fam.Metrics = append(fam.Metrics, MetricSample{Labels: copyLabels(og.labels), Value: og.getValue()})
        }
        families = append(families, fam)
    }
    for name, inner := range p.histograms {
        fam := MetricFamily{Name: name, Type: "histogram", Help: p.familyHelp[name]}
        for _, hi := range inner {
            count, sum, buckets, counts := hi.snapshot()
            sample := MetricSample{Labels: copyLabels(hi.labels), Count: count, Sum: sum}
            for i, ub := range buckets {
                sample.Buckets = append(sample.Buckets, HistogramBucket{UpperBound: ub, Count: counts[i]})
            }
            fam.Metrics = append(fam.Metrics, sample)
        }
        if oh, ok := p.overflowHistograms[name]; ok {
            count, sum, buckets, counts := oh.snapshot()
            sample := MetricSample{Labels: copyLabels(oh.labels), Count: count, Sum: sum}
            for i, ub := range buckets {
                sample.Buckets = append(sample.Buckets, HistogramBucket{UpperBound: ub, Count: counts[i]})
            }
            fam.Metrics = append(fam.Metrics, sample)
        }
        families = append(families, fam)
    }
    return families
}

func (p *metricsProvider) DroppedSeriesCount() int {
    p.droppedMu.Lock()
    defer p.droppedMu.Unlock()
    return int(p.dropped)
}

GO
cat > /app/observability/logger.go <<'GO'
package observability

import (
    "context"
    "encoding/json"
    "io"
    "os"
    "strings"
    "sync"
    "time"
)

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
type loggerConfig struct {
    output io.Writer
    level  string
}
type LoggerOption func(*loggerConfig)
func WithOutput(w io.Writer) LoggerOption { return func(c *loggerConfig) { c.output = w } }
func WithLevel(level string) LoggerOption { return func(c *loggerConfig) { c.level = level } }

type loggerImpl struct {
    serviceName string
    output      io.Writer
    minLevel    int
    fields      []Field
    mu          sync.Mutex
}
func levelToInt(l string) int {
    switch strings.ToLower(l) {
    case "debug": return 0
    case "info": return 1
    case "warn", "warning": return 2
    case "error": return 3
    default: return 1
    }
}
func intToLevelStr(i int) string {
    switch i {
    case 0: return "debug"
    case 1: return "info"
    case 2: return "warn"
    case 3: return "error"
    default: return "info"
    }
}
func NewLogger(serviceName string, opts ...LoggerOption) Logger {
    cfg := &loggerConfig{output: os.Stderr, level: "info"}
    for _, o := range opts { if o!=nil { o(cfg) } }
    if cfg.output == nil { cfg.output = os.Stderr }
    return &loggerImpl{serviceName: serviceName, output: cfg.output, minLevel: levelToInt(cfg.level), fields: []Field{}}
}
func (l *loggerImpl) With(fields ...Field) Logger {
    newFields := append([]Field(nil), l.fields...)
    newFields = append(newFields, fields...)
    return &loggerImpl{serviceName: l.serviceName, output: l.output, minLevel: l.minLevel, fields: newFields}
}
func (l *loggerImpl) log(ctx context.Context, level int, msg string, fields ...Field) {
    if level < l.minLevel { return }
    m := make(map[string]interface{})
    m["timestamp"] = time.Now().UTC().Format(time.RFC3339Nano)
    m["level"] = intToLevelStr(level)
    m["service"] = l.serviceName
    m["message"] = msg
    if ctx != nil {
        if sc, ok := TraceFromContext(ctx); ok {
            m["trace_id"] = sc.TraceID
            m["span_id"] = sc.SpanID
            m["sampled"] = sc.Sampled
            if sc.ParentID != "" { m["parent_id"] = sc.ParentID }
        }
    }
    for _, f := range l.fields { m[f.Key] = f.Value }
    for _, f := range fields { m[f.Key] = f.Value }
    b, _ := json.Marshal(m)
    b = append(b, '\n')
    l.mu.Lock()
    _, _ = l.output.Write(b)
    l.mu.Unlock()
}
func (l *loggerImpl) Info(ctx context.Context, msg string, fields ...Field) { l.log(ctx, 1, msg, fields...) }
func (l *loggerImpl) Error(ctx context.Context, msg string, fields ...Field) { l.log(ctx, 3, msg, fields...) }
func (l *loggerImpl) Debug(ctx context.Context, msg string, fields ...Field) { l.log(ctx, 0, msg, fields...) }
func (l *loggerImpl) Warn(ctx context.Context, msg string, fields ...Field) { l.log(ctx, 2, msg, fields...) }

GO
cat > /app/observability/doc.go <<'GO'
package observability
GO
cd /app && go mod tidy && go build ./... && go vet ./...
