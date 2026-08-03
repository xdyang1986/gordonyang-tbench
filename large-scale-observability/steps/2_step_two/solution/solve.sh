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
	"sync"
	"time"
)

// ---------- Types ----------
type SpanContext struct {
	TraceID      string
	SpanID       string
	ParentSpanID string
	Sampled      bool
	TraceFlags   byte
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
	Value interface{}
}

type SpanEvent struct {
	Name       string
	Timestamp  time.Time
	Attributes []Attribute
}

type ReadableSpan struct {
	Name          string
	SpanContext   SpanContext
	ParentSpanID  string
	SpanKind      SpanKind
	StartTime     time.Time
	EndTime       time.Time
	Attributes    map[string]interface{}
	Events        []SpanEvent
	StatusCode    StatusCode
	StatusMessage string
	ServiceName   string
}

// Interfaces
type Span interface {
	End()
	AddAttribute(key string, value interface{})
	AddEvent(name string, attrs ...Attribute)
	SetStatus(code StatusCode, message string)
	SpanContext() SpanContext
	IsRecording() bool
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

// IDGenerator
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

// Sampling
type SamplingDecision int

const (
	DecisionDrop SamplingDecision = iota
	DecisionRecordAndSample
	DecisionRecordOnly
)

type SamplingParameters struct {
	TraceID       string
	SpanName      string
	SpanKind      SpanKind
	ParentContext SpanContext
	HasParent     bool
	Attributes    []Attribute
}

type Sampler interface {
	ShouldSample(p SamplingParameters) SamplingDecision
	Description() string
}

// AlwaysOn
type alwaysOnSampler struct{}

func NewAlwaysOnSampler() Sampler { return &alwaysOnSampler{} }
func (s *alwaysOnSampler) ShouldSample(p SamplingParameters) SamplingDecision {
	return DecisionRecordAndSample
}
func (s *alwaysOnSampler) Description() string { return "AlwaysOnSampler" }

// AlwaysOff
type alwaysOffSampler struct{}

func NewAlwaysOffSampler() Sampler { return &alwaysOffSampler{} }
func (s *alwaysOffSampler) ShouldSample(p SamplingParameters) SamplingDecision {
	return DecisionDrop
}
func (s *alwaysOffSampler) Description() string { return "AlwaysOffSampler" }

// TraceIDRatio
type traceIDRatioSampler struct {
	fraction float64
}

func NewTraceIDRatioSampler(fraction float64) Sampler {
	return &traceIDRatioSampler{fraction: fraction}
}
func (s *traceIDRatioSampler) Description() string {
	return fmt.Sprintf("TraceIDRatioBased{%.4f}", s.fraction)
}
func (s *traceIDRatioSampler) ShouldSample(p SamplingParameters) SamplingDecision {
	if s.fraction <= 0 {
		return DecisionDrop
	}
	if s.fraction >= 1 {
		return DecisionRecordAndSample
	}
	// parse first 16 hex chars -> 8 bytes -> uint64
	if len(p.TraceID) < 16 {
		return DecisionDrop
	}
	// take first 16 chars
	sub := p.TraceID[:16]
	val, err := strconv.ParseUint(sub, 16, 64)
	if err != nil {
		// fallback: hash-like using hex decode?
		return DecisionDrop
	}
	// compare val / maxUint64 < fraction
	// Equivalent to val < fraction * maxUint64
	// max uint64 = 2^64-1 ~ 1.84e19, but using float may lose precision, use float64 ratio
	// compute threshold
	threshold := s.fraction * float64(^uint64(0))
	if float64(val) < threshold {
		return DecisionRecordAndSample
	}
	return DecisionDrop
}

// ParentBased
type parentBasedSampler struct {
	root Sampler
}

func NewParentBasedSampler(root Sampler) Sampler {
	if root == nil {
		root = NewAlwaysOnSampler()
	}
	return &parentBasedSampler{root: root}
}
func (s *parentBasedSampler) Description() string {
	return fmt.Sprintf("ParentBased{root=%s}", s.root.Description())
}
func (s *parentBasedSampler) ShouldSample(p SamplingParameters) SamplingDecision {
	if !p.HasParent {
		return s.root.ShouldSample(p)
	}
	if p.ParentContext.Sampled {
		return DecisionRecordAndSample
	}
	return DecisionDrop
}

// Tracer options
type tracerConfig struct {
	serviceName string
	processor   SpanProcessor
	idGen       IDGenerator
	sampler     Sampler
}

type TracerOption func(*tracerConfig)

func WithServiceName(name string) TracerOption {
	return func(c *tracerConfig) { c.serviceName = name }
}
func WithSpanProcessor(p SpanProcessor) TracerOption {
	return func(c *tracerConfig) { c.processor = p }
}
func WithIDGenerator(gen IDGenerator) TracerOption {
	return func(c *tracerConfig) { c.idGen = gen }
}
func WithSampler(s Sampler) TracerOption {
	return func(c *tracerConfig) { c.sampler = s }
}

// Span start options
type spanStartConfig struct {
	attributes []Attribute
	kind       SpanKind
	parent     *SpanContext
}

type SpanStartOption func(*spanStartConfig)

func WithAttributes(attrs ...Attribute) SpanStartOption {
	return func(c *spanStartConfig) { c.attributes = append(c.attributes, attrs...) }
}
func WithSpanKind(k SpanKind) SpanStartOption {
	return func(c *spanStartConfig) { c.kind = k }
}
func WithParent(sc SpanContext) SpanStartOption {
	return func(c *spanStartConfig) { c.parent = &sc }
}

// context key
type spanContextKey struct{}

func ContextWithSpanContext(ctx context.Context, sc SpanContext) context.Context {
	return context.WithValue(ctx, spanContextKey{}, sc)
}
func SpanContextFromContext(ctx context.Context) (SpanContext, bool) {
	sc, ok := ctx.Value(spanContextKey{}).(SpanContext)
	return sc, ok
}

// Inject / Extract
var (
	hex32Regex = regexp.MustCompile(`^[0-9a-fA-F]{32}$`)
	hex16Regex = regexp.MustCompile(`^[0-9a-fA-F]{16}$`)
)

func Inject(ctx context.Context, carrier map[string]string) {
	if carrier == nil {
		return
	}
	sc, ok := SpanContextFromContext(ctx)
	if !ok {
		return
	}
	carrier["trace-id"] = sc.TraceID
	carrier["span-id"] = sc.SpanID
	carrier["parent-id"] = sc.ParentSpanID
	if sc.Sampled {
		carrier["sampled"] = "1"
	} else {
		carrier["sampled"] = "0"
	}
}

func Extract(carrier map[string]string) context.Context {
	if carrier == nil {
		return context.Background()
	}
	tid, hasTid := carrier["trace-id"]
	sid, hasSid := carrier["span-id"]
	if !hasTid || !hasSid {
		return context.Background()
	}
	if !hex32Regex.MatchString(tid) || !hex16Regex.MatchString(sid) {
		return context.Background()
	}
	parentID := carrier["parent-id"]
	if parentID != "" && !hex16Regex.MatchString(parentID) {
		// allow empty but if present must be valid; otherwise treat as invalid and ignore parent-id
		parentID = ""
	}
	sampledStr := carrier["sampled"]
	sampled := false
	if sampledStr == "1" || sampledStr == "true" || sampledStr == "True" || sampledStr == "TRUE" {
		sampled = true
	}
	sc := SpanContext{
		TraceID:      tid,
		SpanID:       sid,
		ParentSpanID: parentID,
		Sampled:      sampled,
	}
	if sampled {
		sc.TraceFlags = 1
	}
	return ContextWithSpanContext(context.Background(), sc)
}

// InMemoryExporter
type InMemoryExporter struct {
	mu    sync.Mutex
	spans []ReadableSpan
}

func NewInMemoryExporter() *InMemoryExporter {
	return &InMemoryExporter{}
}
func (e *InMemoryExporter) ExportSpans(ctx context.Context, spans []ReadableSpan) error {
	e.mu.Lock()
	defer e.mu.Unlock()
	// copy
	for _, s := range spans {
		// deep copy attributes map
		// ensure not sharing
		cpy := s
		if s.Attributes != nil {
			cpy.Attributes = make(map[string]interface{}, len(s.Attributes))
			for k, v := range s.Attributes {
				cpy.Attributes[k] = v
			}
		}
		if s.Events != nil {
			cpy.Events = append([]SpanEvent(nil), s.Events...)
		}
		e.spans = append(e.spans, cpy)
	}
	return nil
}
func (e *InMemoryExporter) GetSpans() []ReadableSpan {
	e.mu.Lock()
	defer e.mu.Unlock()
	cpy := make([]ReadableSpan, len(e.spans))
	copy(cpy, e.spans)
	return cpy
}
func (e *InMemoryExporter) Clear() {
	e.mu.Lock()
	defer e.mu.Unlock()
	e.spans = nil
}
func (e *InMemoryExporter) GetCount() int {
	e.mu.Lock()
	defer e.mu.Unlock()
	return len(e.spans)
}

// SimpleSpanProcessor
type simpleSpanProcessor struct {
	exporter SpanExporter
}

func NewSimpleSpanProcessor(exporter SpanExporter) SpanProcessor {
	return &simpleSpanProcessor{exporter: exporter}
}
func (p *simpleSpanProcessor) OnStart(ctx context.Context, span ReadableSpan) {}
func (p *simpleSpanProcessor) OnEnd(span ReadableSpan) {
	_ = p.exporter.ExportSpans(context.Background(), []ReadableSpan{span})
}
func (p *simpleSpanProcessor) Shutdown(ctx context.Context) error   { return nil }
func (p *simpleSpanProcessor) ForceFlush(ctx context.Context) error { return nil }

// tracerImpl and spanImpl
type tracerImpl struct {
	serviceName string
	processor   SpanProcessor
	idGen       IDGenerator
	sampler     Sampler
}

func NewTracer(serviceName string, opts ...TracerOption) Tracer {
	cfg := &tracerConfig{
		serviceName: serviceName,
		idGen:       &defaultIDGenerator{},
		sampler:     NewAlwaysOnSampler(),
	}
	// apply options
	for _, o := range opts {
		o(cfg)
	}
	if cfg.serviceName == "" {
		cfg.serviceName = serviceName
	}
	if cfg.processor == nil {
		// default no-op? Use in-memory? For safety, use noop processor that does nothing unless provided.
		// But we will use simple processor with in-memory if none? To avoid nil, create noop.
		cfg.processor = &simpleSpanProcessor{exporter: NewInMemoryExporter()}
	}
	if cfg.idGen == nil {
		cfg.idGen = &defaultIDGenerator{}
	}
	if cfg.sampler == nil {
		cfg.sampler = NewAlwaysOnSampler()
	}
	return &tracerImpl{
		serviceName: cfg.serviceName,
		processor:   cfg.processor,
		idGen:       cfg.idGen,
		sampler:     cfg.sampler,
	}
}

type spanImpl struct {
	mu            sync.Mutex
	name          string
	spanContext   SpanContext
	parentSpanID  string
	kind          SpanKind
	startTime     time.Time
	endTime       time.Time
	attributes    map[string]interface{}
	events        []SpanEvent
	statusCode    StatusCode
	statusMessage string
	serviceName   string
	processor     SpanProcessor
	ended         bool
	recording     bool
}

func (t *tracerImpl) Start(ctx context.Context, name string, opts ...SpanStartOption) (context.Context, Span) {
	cfg := &spanStartConfig{
		kind: SpanKindInternal,
	}
	for _, o := range opts {
		o(cfg)
	}
	// determine parent
	var parentSC *SpanContext
	if cfg.parent != nil {
		parentSC = cfg.parent
	} else if sc, ok := SpanContextFromContext(ctx); ok {
		// copy
		tmp := sc
		parentSC = &tmp
	}
	var traceID string
	var parentSpanID string
	var hasParent bool
	var parentCtx SpanContext
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

	// sampling
	samplingParams := SamplingParameters{
		TraceID:       traceID,
		SpanName:      name,
		SpanKind:      cfg.kind,
		ParentContext: parentCtx,
		HasParent:     hasParent,
		Attributes:    cfg.attributes,
	}
	decision := t.sampler.ShouldSample(samplingParams)
	sampled := decision == DecisionRecordAndSample
	recording := sampled

	// For AlwaysOn default, sampled true
	// Build SpanContext
	sc := SpanContext{
		TraceID:      traceID,
		SpanID:       spanID,
		ParentSpanID: parentSpanID,
		Sampled:      sampled,
	}
	if sampled {
		sc.TraceFlags = 1
	}

	span := &spanImpl{
		name:         name,
		spanContext:  sc,
		parentSpanID: parentSpanID,
		kind:         cfg.kind,
		startTime:    time.Now(),
		attributes:   make(map[string]interface{}),
		events:       []SpanEvent{},
		statusCode:   StatusUnset,
		serviceName:  t.serviceName,
		processor:    t.processor,
		recording:    recording,
	}

	// initial attributes from options
	for _, a := range cfg.attributes {
		if len(span.attributes) >= 128 {
			break
		}
		// truncate string value >1024
		val := a.Value
		if s, ok := val.(string); ok && len(s) > 1024 {
			val = s[:1024]
		}
		span.attributes[a.Key] = val
	}

	// if recording, call OnStart? optional
	if recording {
		// readable for OnStart could be empty at start
		rs := ReadableSpan{
			Name:         span.name,
			SpanContext:  span.spanContext,
			ParentSpanID: span.parentSpanID,
			SpanKind:     span.kind,
			StartTime:    span.startTime,
			Attributes:   copyMap(span.attributes),
			ServiceName:  span.serviceName,
		}
		t.processor.OnStart(ctx, rs)
	}

	newCtx := ContextWithSpanContext(ctx, sc)
	return newCtx, span
}

func copyMap(in map[string]interface{}) map[string]interface{} {
	if in == nil {
		return nil
	}
	out := make(map[string]interface{}, len(in))
	for k, v := range in {
		out[k] = v
	}
	return out
}

func (s *spanImpl) End() {
	s.mu.Lock()
	if s.ended {
		s.mu.Unlock()
		return
	}
	s.ended = true
	s.endTime = time.Now()
	// snapshot
	attrs := copyMap(s.attributes)
	events := append([]SpanEvent(nil), s.events...)
	sc := s.spanContext
	parentID := s.parentSpanID
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

	if !recording {
		return
	}

	rs := ReadableSpan{
		Name:          name,
		SpanContext:   sc,
		ParentSpanID:  parentID,
		SpanKind:      kind,
		StartTime:     start,
		EndTime:       end,
		Attributes:    attrs,
		Events:        events,
		StatusCode:    status,
		StatusMessage: statusMsg,
		ServiceName:   service,
	}
	processor.OnEnd(rs)
}

func (s *spanImpl) AddAttribute(key string, value interface{}) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.ended || !s.recording {
		return
	}
	if len(s.attributes) >= 128 {
		return
	}
	if str, ok := value.(string); ok && len(str) > 1024 {
		value = str[:1024]
	}
	s.attributes[key] = value
}
func (s *spanImpl) AddEvent(name string, attrs ...Attribute) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.ended || !s.recording {
		return
	}
	if len(s.events) >= 128 {
		return
	}
	ev := SpanEvent{
		Name:      name,
		Timestamp: time.Now(),
	}
	if len(attrs) > 0 {
		ev.Attributes = append([]Attribute(nil), attrs...)
	}
	s.events = append(s.events, ev)
}
func (s *spanImpl) SetStatus(code StatusCode, message string) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.ended || !s.recording {
		return
	}
	s.statusCode = code
	s.statusMessage = message
}
func (s *spanImpl) SpanContext() SpanContext {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.spanContext
}
func (s *spanImpl) IsRecording() bool {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.recording && !s.ended
}

// Batch processor options and implementation
type batchConfig struct {
	batchSize          int
	queueSize          int
	batchTimeout       time.Duration
	exportTimeout      time.Duration
	maxExportBatchSize int
}

type BatchSpanProcessorOption func(*batchConfig)

func WithBatchSize(n int) BatchSpanProcessorOption {
	return func(c *batchConfig) { c.batchSize = n }
}
func WithQueueSize(n int) BatchSpanProcessorOption {
	return func(c *batchConfig) { c.queueSize = n }
}
func WithBatchTimeout(d time.Duration) BatchSpanProcessorOption {
	return func(c *batchConfig) { c.batchTimeout = d }
}
func WithExportTimeout(d time.Duration) BatchSpanProcessorOption {
	return func(c *batchConfig) { c.exportTimeout = d }
}
func WithMaxExportBatchSize(n int) BatchSpanProcessorOption {
	return func(c *batchConfig) { c.maxExportBatchSize = n; c.batchSize = n }
}

type batchSpanProcessor struct {
	exporter      SpanExporter
	queue         chan ReadableSpan
	batchSize     int
	batchTimeout  time.Duration
	exportTimeout time.Duration

	mu           sync.Mutex
	batch        []ReadableSpan
	stopCh       chan struct{}
	stopped      bool
	dropped      int64
	droppedMu    sync.Mutex
	wg           sync.WaitGroup
	shutdownOnce sync.Once
}

func NewBatchSpanProcessor(exporter SpanExporter, opts ...BatchSpanProcessorOption) SpanProcessor {
	cfg := &batchConfig{
		batchSize:     512,
		queueSize:     2048,
		batchTimeout:  5 * time.Second,
		exportTimeout: 30 * time.Second,
	}
	for _, o := range opts {
		o(cfg)
	}
	if cfg.batchSize <= 0 {
		cfg.batchSize = 512
	}
	if cfg.queueSize <= 0 {
		cfg.queueSize = 2048
	}
	if cfg.batchTimeout <= 0 {
		cfg.batchTimeout = 5 * time.Second
	}
	if cfg.exportTimeout <= 0 {
		cfg.exportTimeout = 30 * time.Second
	}
	if cfg.maxExportBatchSize > 0 {
		cfg.batchSize = cfg.maxExportBatchSize
	}

	b := &batchSpanProcessor{
		exporter:      exporter,
		queue:         make(chan ReadableSpan, cfg.queueSize),
		batchSize:     cfg.batchSize,
		batchTimeout:  cfg.batchTimeout,
		exportTimeout: cfg.exportTimeout,
		batch:         make([]ReadableSpan, 0, cfg.batchSize),
		stopCh:        make(chan struct{}),
	}
	b.wg.Add(1)
	go b.run()
	return b
}

func (b *batchSpanProcessor) run() {
	defer b.wg.Done()
	ticker := time.NewTicker(b.batchTimeout)
	defer ticker.Stop()
	for {
		select {
		case <-b.stopCh:
			// flush remaining queue and batch
			b.mu.Lock()
			// drain queue
			closeDrain := func() []ReadableSpan {
				var remaining []ReadableSpan
				// drain channel without blocking
				for {
					select {
					case span, ok := <-b.queue:
						if !ok {
							return remaining
						}
						remaining = append(remaining, span)
					default:
						return remaining
					}
				}
			}
			queued := closeDrain()
			b.batch = append(b.batch, queued...)
			batchToExport := b.batch
			b.batch = nil
			b.mu.Unlock()
			if len(batchToExport) > 0 {
				b.exportWithTimeout(batchToExport)
			}
			return
		case span, ok := <-b.queue:
			if !ok {
				// channel closed, flush
				b.mu.Lock()
				batchToExport := b.batch
				b.batch = nil
				b.mu.Unlock()
				if len(batchToExport) > 0 {
					b.exportWithTimeout(batchToExport)
				}
				return
			}
			b.mu.Lock()
			b.batch = append(b.batch, span)
			if len(b.batch) >= b.batchSize {
				batchToExport := b.batch
				b.batch = make([]ReadableSpan, 0, b.batchSize)
				b.mu.Unlock()
				b.exportWithTimeout(batchToExport)
				ticker.Reset(b.batchTimeout)
			} else {
				b.mu.Unlock()
			}
		case <-ticker.C:
			b.mu.Lock()
			if len(b.batch) > 0 {
				batchToExport := b.batch
				b.batch = make([]ReadableSpan, 0, b.batchSize)
				b.mu.Unlock()
				b.exportWithTimeout(batchToExport)
			} else {
				b.mu.Unlock()
			}
		}
	}
}

func (b *batchSpanProcessor) exportWithTimeout(spans []ReadableSpan) {
	if len(spans) == 0 {
		return
	}
	ctx, cancel := context.WithTimeout(context.Background(), b.exportTimeout)
	defer cancel()
	done := make(chan error, 1)
	go func() {
		done <- b.exporter.ExportSpans(ctx, spans)
	}()
	select {
	case <-ctx.Done():
		return
	case <-done:
		return
	}
}

func (b *batchSpanProcessor) OnStart(ctx context.Context, span ReadableSpan) {}

func (b *batchSpanProcessor) OnEnd(span ReadableSpan) {
	b.mu.Lock()
	if b.stopped {
		b.mu.Unlock()
		return
	}
	b.mu.Unlock()
	// non-blocking send
	select {
	case b.queue <- span:
	default:
		b.droppedMu.Lock()
		b.dropped++
		b.droppedMu.Unlock()
	}
}

func (b *batchSpanProcessor) ForceFlush(ctx context.Context) error {
	done := make(chan struct{})
	go func() {
		// flush batch
		b.mu.Lock()
		if len(b.batch) > 0 {
			batchCopy := b.batch
			b.batch = make([]ReadableSpan, 0, b.batchSize)
			b.mu.Unlock()
			b.exportWithTimeout(batchCopy)
			b.mu.Lock()
		}
		// drain queue
		var toExport []ReadableSpan
		for {
			select {
			case span := <-b.queue:
				toExport = append(toExport, span)
				if len(toExport) >= b.batchSize {
					b.mu.Unlock()
					b.exportWithTimeout(toExport)
					toExport = nil
					b.mu.Lock()
				}
			default:
				goto drainDone
			}
		}
	drainDone:
		if len(toExport) > 0 {
			b.mu.Unlock()
			b.exportWithTimeout(toExport)
			b.mu.Lock()
		}
		if len(b.batch) > 0 {
			bc := b.batch
			b.batch = nil
			b.mu.Unlock()
			b.exportWithTimeout(bc)
		} else {
			b.mu.Unlock()
		}
		close(done)
	}()
	select {
	case <-done:
		return nil
	case <-ctx.Done():
		return ctx.Err()
	}
}

func (b *batchSpanProcessor) Shutdown(ctx context.Context) error {
	var err error
	b.shutdownOnce.Do(func() {
		b.mu.Lock()
		b.stopped = true
		b.mu.Unlock()
		close(b.stopCh)
		done := make(chan struct{})
		go func() {
			b.wg.Wait()
			close(done)
		}()
		select {
		case <-done:
		case <-ctx.Done():
			err = ctx.Err()
		}
	})
	return err
}

func (b *batchSpanProcessor) DroppedCount() int {
	b.droppedMu.Lock()
	defer b.droppedMu.Unlock()
	return int(b.dropped)
}
func (b *batchSpanProcessor) QueueLen() int {
	return len(b.queue)
}

GO

cat > /app/observability/metrics.go <<'GO'
package observability

import (
	"regexp"
	"sort"
	"sync"
)

// Interfaces
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

// MetricOption for Counter/Gauge/Histogram creation
type metricDesc struct {
	labels      map[string]string
	description string
	buckets     []float64
}

type MetricOption func(*metricDesc)

func WithLabels(labels map[string]string) MetricOption {
	return func(m *metricDesc) {
		if labels == nil {
			m.labels = nil
			return
		}
		cp := make(map[string]string, len(labels))
		for k, v := range labels {
			cp[k] = v
		}
		m.labels = cp
	}
}
func WithDescription(desc string) MetricOption {
	return func(m *metricDesc) { m.description = desc }
}
func WithBuckets(buckets []float64) MetricOption {
	return func(m *metricDesc) {
		if buckets == nil {
			m.buckets = nil
			return
		}
		cp := make([]float64, len(buckets))
		copy(cp, buckets)
		m.buckets = cp
	}
}

// Provider options
type providerConfig struct {
	maxCardinality int
	overflowMode   string // drop or aggregate
}

type MetricsProviderOption func(*providerConfig)
type MetricsOption func(*providerConfig) // alias for backward compat

func WithMaxCardinality(n int) MetricsProviderOption {
	return func(c *providerConfig) { c.maxCardinality = n }
}
func WithCardinalityOverflowHandling(mode string) MetricsProviderOption {
	return func(c *providerConfig) { c.overflowMode = mode }
}

// For compatibility, allow MetricsOption to be used as provider option
// Actually identical type

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

// implementation types
var (
	metricNameRegex = regexp.MustCompile(`^[a-zA-Z_][a-zA-Z0-9_]*$`)
	labelKeyRegex   = regexp.MustCompile(`^[a-zA-Z_][a-zA-Z0-9_]*$`)
)

var defaultHistogramBuckets = []float64{0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10}

// noop instruments
type noopCounter struct{}

func (n *noopCounter) Inc()              {}
func (n *noopCounter) Add(delta float64) {}

type noopGauge struct{}

func (n *noopGauge) Set(v float64)     {}
func (n *noopGauge) Inc()              {}
func (n *noopGauge) Dec()              {}
func (n *noopGauge) Add(delta float64) {}

type noopHistogram struct{}

func (n *noopHistogram) Observe(v float64) {}

type counterImpl struct {
	mu     sync.Mutex
	value  float64
	labels map[string]string
}

func (c *counterImpl) Inc() {
	c.mu.Lock()
	c.value++
	c.mu.Unlock()
}
func (c *counterImpl) Add(delta float64) {
	if delta < 0 {
		return
	}
	c.mu.Lock()
	c.value += delta
	c.mu.Unlock()
}
func (c *counterImpl) getValue() float64 {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.value
}

type gaugeImpl struct {
	mu     sync.Mutex
	value  float64
	labels map[string]string
}

func (g *gaugeImpl) Set(v float64) {
	g.mu.Lock()
	g.value = v
	g.mu.Unlock()
}
func (g *gaugeImpl) Inc() {
	g.mu.Lock()
	g.value++
	g.mu.Unlock()
}
func (g *gaugeImpl) Dec() {
	g.mu.Lock()
	g.value--
	g.mu.Unlock()
}
func (g *gaugeImpl) Add(delta float64) {
	g.mu.Lock()
	g.value += delta
	g.mu.Unlock()
}
func (g *gaugeImpl) getValue() float64 {
	g.mu.Lock()
	defer g.mu.Unlock()
	return g.value
}

type histogramImpl struct {
	mu           sync.Mutex
	count        uint64
	sum          float64
	buckets      []float64
	bucketCounts []uint64
	labels       map[string]string
}

func newHistogramImpl(labels map[string]string, buckets []float64) *histogramImpl {
	if len(buckets) == 0 {
		buckets = append([]float64(nil), defaultHistogramBuckets...)
	} else {
		// sort buckets
		cp := append([]float64(nil), buckets...)
		sort.Float64s(cp)
		buckets = cp
	}
	return &histogramImpl{
		buckets:      buckets,
		bucketCounts: make([]uint64, len(buckets)),
		labels:       copyLabels(labels),
	}
}
func (h *histogramImpl) Observe(v float64) {
	h.mu.Lock()
	defer h.mu.Unlock()
	h.count++
	h.sum += v
	// cumulative count: increment all buckets where v <= upperBound
	for i, ub := range h.buckets {
		if v <= ub {
			h.bucketCounts[i]++
		}
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
	if in == nil {
		return map[string]string{}
	}
	out := make(map[string]string, len(in))
	for k, v := range in {
		// truncate value >256? for safety
		if len(v) > 256 {
			v = v[:256]
		}
		out[k] = v
	}
	return out
}

func labelsKey(labels map[string]string) string {
	if len(labels) == 0 {
		return ""
	}
	keys := make([]string, 0, len(labels))
	for k := range labels {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	// build string
	// using fmt could be fine, but simple concatenation
	s := ""
	for i, k := range keys {
		if i > 0 {
			s += ","
		}
		s += k + "=" + labels[k]
	}
	return s
}

func isValidMetricName(name string) bool {
	return metricNameRegex.MatchString(name)
}
func isValidLabels(labels map[string]string) bool {
	for k := range labels {
		if !labelKeyRegex.MatchString(k) {
			return false
		}
	}
	return true
}

// provider impl
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
	cfg := &providerConfig{
		maxCardinality: 0,
		overflowMode:   "drop",
	}
	for _, o := range opts {
		o(cfg)
	}
	if cfg.overflowMode != "aggregate" {
		cfg.overflowMode = "drop"
	}
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
	if !isValidMetricName(name) {
		return &noopCounter{}
	}
	desc := &metricDesc{}
	for _, o := range opts {
		o(desc)
	}
	labels := desc.labels
	if labels == nil {
		labels = map[string]string{}
	}
	if !isValidLabels(labels) {
		return &noopCounter{}
	}
	key := labelsKey(labels)

	p.mu.Lock()
	defer p.mu.Unlock()

	// check type conflict: if name exists as gauge or histogram, return noop? For simplicity, allow but separate storage, but if same name used as different type, treat as conflict -> noop for new?
	// We'll allow same name across types? Better to prevent mixed types: if name in gauges or histograms, return noop.
	if _, ok := p.gauges[name]; ok {
		return &noopCounter{}
	}
	if _, ok := p.histograms[name]; ok {
		return &noopCounter{}
	}

	if _, ok := p.counters[name]; !ok {
		p.counters[name] = make(map[string]*counterImpl)
	}
	inner := p.counters[name]
	if existing, ok := inner[key]; ok {
		return existing
	}
	// cardinality check
	if p.maxCardinality > 0 && len(inner) >= p.maxCardinality {
		// limit reached
		if p.overflowMode == "aggregate" {
			if oc, ok := p.overflowCounters[name]; ok {
				return oc
			}
			// create overflow
			overflowLabels := map[string]string{"__overflow__": "true"}
			oc := &counterImpl{labels: overflowLabels}
			p.overflowCounters[name] = oc
			// still count as dropped? No, for aggregate mode, we should not count as dropped but as overflow aggregated
			// But for test we will not count dropped for aggregate, or count? We'll increment dropped for tracking but overflow still works.
			// Use dropped for both modes? For aggregate, we should not increment dropped as it's handled.
			return oc
		} else {
			// drop
			p.droppedMu.Lock()
			p.dropped++
			p.droppedMu.Unlock()
			return &noopCounter{}
		}
	}
	ci := &counterImpl{
		labels: copyLabels(labels),
	}
	inner[key] = ci
	if desc.description != "" {
		if _, exists := p.familyHelp[name]; !exists {
			p.familyHelp[name] = desc.description
		}
	}
	return ci
}

func (p *metricsProvider) Gauge(name string, opts ...MetricOption) Gauge {
	if !isValidMetricName(name) {
		return &noopGauge{}
	}
	desc := &metricDesc{}
	for _, o := range opts {
		o(desc)
	}
	labels := desc.labels
	if labels == nil {
		labels = map[string]string{}
	}
	if !isValidLabels(labels) {
		return &noopGauge{}
	}
	key := labelsKey(labels)

	p.mu.Lock()
	defer p.mu.Unlock()

	if _, ok := p.counters[name]; ok {
		return &noopGauge{}
	}
	if _, ok := p.histograms[name]; ok {
		return &noopGauge{}
	}
	if _, ok := p.gauges[name]; !ok {
		p.gauges[name] = make(map[string]*gaugeImpl)
	}
	inner := p.gauges[name]
	if existing, ok := inner[key]; ok {
		return existing
	}
	if p.maxCardinality > 0 && len(inner) >= p.maxCardinality {
		if p.overflowMode == "aggregate" {
			if og, ok := p.overflowGauges[name]; ok {
				return og
			}
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
	gi := &gaugeImpl{
		labels: copyLabels(labels),
	}
	inner[key] = gi
	if desc.description != "" {
		if _, exists := p.familyHelp[name]; !exists {
			p.familyHelp[name] = desc.description
		}
	}
	return gi
}

func (p *metricsProvider) Histogram(name string, opts ...MetricOption) Histogram {
	if !isValidMetricName(name) {
		return &noopHistogram{}
	}
	desc := &metricDesc{}
	for _, o := range opts {
		o(desc)
	}
	labels := desc.labels
	if labels == nil {
		labels = map[string]string{}
	}
	if !isValidLabels(labels) {
		return &noopHistogram{}
	}
	key := labelsKey(labels)

	p.mu.Lock()
	defer p.mu.Unlock()

	if _, ok := p.counters[name]; ok {
		return &noopHistogram{}
	}
	if _, ok := p.gauges[name]; ok {
		return &noopHistogram{}
	}
	if _, ok := p.histograms[name]; !ok {
		p.histograms[name] = make(map[string]*histogramImpl)
	}
	inner := p.histograms[name]
	if existing, ok := inner[key]; ok {
		return existing
	}
	if p.maxCardinality > 0 && len(inner) >= p.maxCardinality {
		if p.overflowMode == "aggregate" {
			if oh, ok := p.overflowHistograms[name]; ok {
				return oh
			}
			buckets := desc.buckets
			if len(buckets) == 0 {
				buckets = defaultHistogramBuckets
			}
			oh := newHistogramImpl(map[string]string{"__overflow__": "true"}, buckets)
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
	if desc.description != "" {
		if _, exists := p.familyHelp[name]; !exists {
			p.familyHelp[name] = desc.description
		}
	}
	return hi
}

func (p *metricsProvider) Collect() []MetricFamily {
	p.mu.RLock()
	defer p.mu.RUnlock()
	var families []MetricFamily

	for name, inner := range p.counters {
		fam := MetricFamily{
			Name: name,
			Type: "counter",
			Help: p.familyHelp[name],
		}
		for _, ci := range inner {
			sample := MetricSample{
				Labels: copyLabels(ci.labels),
				Value:  ci.getValue(),
			}
			fam.Metrics = append(fam.Metrics, sample)
		}
		// overflow
		if oc, ok := p.overflowCounters[name]; ok {
			sample := MetricSample{
				Labels: copyLabels(oc.labels),
				Value:  oc.getValue(),
			}
			fam.Metrics = append(fam.Metrics, sample)
		}
		families = append(families, fam)
	}
	for name, inner := range p.gauges {
		fam := MetricFamily{
			Name: name,
			Type: "gauge",
			Help: p.familyHelp[name],
		}
		for _, gi := range inner {
			sample := MetricSample{
				Labels: copyLabels(gi.labels),
				Value:  gi.getValue(),
			}
			fam.Metrics = append(fam.Metrics, sample)
		}
		if og, ok := p.overflowGauges[name]; ok {
			sample := MetricSample{
				Labels: copyLabels(og.labels),
				Value:  og.getValue(),
			}
			fam.Metrics = append(fam.Metrics, sample)
		}
		families = append(families, fam)
	}
	for name, inner := range p.histograms {
		fam := MetricFamily{
			Name: name,
			Type: "histogram",
			Help: p.familyHelp[name],
		}
		for _, hi := range inner {
			count, sum, buckets, counts := hi.snapshot()
			sample := MetricSample{
				Labels: copyLabels(hi.labels),
				Count:  count,
				Sum:    sum,
			}
			for i, ub := range buckets {
				sample.Buckets = append(sample.Buckets, HistogramBucket{UpperBound: ub, Count: counts[i]})
			}
			fam.Metrics = append(fam.Metrics, sample)
		}
		if oh, ok := p.overflowHistograms[name]; ok {
			count, sum, buckets, counts := oh.snapshot()
			sample := MetricSample{
				Labels: copyLabels(oh.labels),
				Count:  count,
				Sum:    sum,
			}
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

func WithOutput(w io.Writer) LoggerOption {
	return func(c *loggerConfig) { c.output = w }
}
func WithLevel(level string) LoggerOption {
	return func(c *loggerConfig) { c.level = level }
}

type loggerImpl struct {
	serviceName string
	output      io.Writer
	minLevel    int
	fields      []Field
	mu          sync.Mutex
}

func levelToInt(l string) int {
	switch strings.ToLower(l) {
	case "debug":
		return 0
	case "info":
		return 1
	case "warn", "warning":
		return 2
	case "error":
		return 3
	default:
		return 1 // info default
	}
}
func intToLevelStr(i int) string {
	switch i {
	case 0:
		return "debug"
	case 1:
		return "info"
	case 2:
		return "warn"
	case 3:
		return "error"
	default:
		return "info"
	}
}

func NewLogger(serviceName string, opts ...LoggerOption) Logger {
	cfg := &loggerConfig{
		output: os.Stderr,
		level:  "info",
	}
	for _, o := range opts {
		o(cfg)
	}
	if cfg.output == nil {
		cfg.output = os.Stderr
	}
	return &loggerImpl{
		serviceName: serviceName,
		output:      cfg.output,
		minLevel:    levelToInt(cfg.level),
		fields:      []Field{},
	}
}

func (l *loggerImpl) With(fields ...Field) Logger {
	// copy existing fields
	newFields := append([]Field(nil), l.fields...)
	newFields = append(newFields, fields...)
	return &loggerImpl{
		serviceName: l.serviceName,
		output:      l.output,
		minLevel:    l.minLevel,
		fields:      newFields,
	}
}

func (l *loggerImpl) log(ctx context.Context, level int, msg string, fields ...Field) {
	if level < l.minLevel {
		return
	}
	// build map
	m := make(map[string]interface{})
	m["timestamp"] = time.Now().UTC().Format(time.RFC3339Nano)
	m["level"] = intToLevelStr(level)
	m["service"] = l.serviceName
	m["message"] = msg

	// trace correlation
	if ctx != nil {
		if sc, ok := SpanContextFromContext(ctx); ok {
			m["trace_id"] = sc.TraceID
			m["span_id"] = sc.SpanID
			m["sampled"] = sc.Sampled
			if sc.ParentSpanID != "" {
				m["parent_id"] = sc.ParentSpanID
			}
		}
	}
	// logger base fields
	for _, f := range l.fields {
		m[f.Key] = f.Value
	}
	// per-call fields
	for _, f := range fields {
		m[f.Key] = f.Value
	}

	// marshal
	b, err := json.Marshal(m)
	if err != nil {
		// fallback
		b = []byte(`{"error":"marshal failed"}`)
	}
	b = append(b, '\n')
	l.mu.Lock()
	_, _ = l.output.Write(b)
	l.mu.Unlock()
}

func (l *loggerImpl) Info(ctx context.Context, msg string, fields ...Field) {
	l.log(ctx, 1, msg, fields...)
}
func (l *loggerImpl) Error(ctx context.Context, msg string, fields ...Field) {
	l.log(ctx, 3, msg, fields...)
}
func (l *loggerImpl) Debug(ctx context.Context, msg string, fields ...Field) {
	l.log(ctx, 0, msg, fields...)
}
func (l *loggerImpl) Warn(ctx context.Context, msg string, fields ...Field) {
	l.log(ctx, 2, msg, fields...)
}

GO

cat > /app/observability/doc.go <<'GO'
package observability
GO

echo "Solution applied"
cd /app && go mod tidy && go build ./... && go vet ./...
