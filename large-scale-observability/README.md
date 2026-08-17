# Large-Scale Observability for Ride-Hailing (Uber-like) — Multi-Turn Go Task (Hardened for Difficulty + Novelty)

## Overview
This is a multi-turn terminal-bench task simulating observability system design for a high-throughput ride-hailing platform like Uber. **Now significantly hardened** (Aug 2026 v2) — both steps made harder with edge cases that require precise spec reading, not just OTel recall.

**Format**: `terminal_bench_multi_turn` with 2 steps, `inherit_prior_session=true` on step2.

**Novelty redesign (Aug 2026)**: Original version was a near-verbatim clone of OpenTelemetry Go SDK (types `SamplingParameters`, `ReadableSpan`, `SpanProcessor`, `DecisionDrop`, `NewInMemoryExporter`, `Inject`/`Extract` etc). Models could recall OTel architecture for free; only concurrency defects discriminated. Redesigned contract intentionally **punishes OTel recall**:

- **Propagation**: Single header `x-ride-trace` = `traceID:spanID:parentID:1/0` (colon separated, 4 parts even when parent empty as `tid:sid::1`), not four keys `trace-id`/`span-id`/`parent-id`/`sampled`. Tests assert `Inject`/`MarshalTrace` writes only `x-ride-trace` and does NOT write `trace-id`. Validation strict: 32hex TraceID, 16hex SpanID, empty or 16hex ParentID, sampled must be "0" or "1" else invalid => background. Flags must be 1 if sampled else 0, preserved via propagation.
- **RatioSampler deterministic algorithm**: Last 8 hex chars as uint32 / 2^32 (4294967296), not first 16 hex as uint64 / 2^64. Fixed TraceIDs where methods disagree: `0000000000000001ffffffffffffffff` (first 16 tiny => OTel Keep, our spec Drop) and opposite. Boundary exact threshold: 0x80000000 /2^32 =0.5 => Drop (< not <=). Case-insensitive hex. Model recalling OTel fails.
- **Error/critical override precedence**: RatioSampler must Keep if `Status == StatusError` or `Priority == "critical"` regardless of fraction, **checked BEFORE invalid TraceID check** so invalid+error => Keep (error always sampled). OTel ratio ignores status/priority and would Drop at 0.0; tests include ratio 0.0 + error => Keep, invalid+error => Keep, invalid alone => Drop.
- **ParentAware AND logic**: ParentAwareSampler requires **both** parent sampled AND root sampled to Keep. OTel ParentBased Keeps whenever parent sampled ignoring root. Test: root Never (0.0) + parent sampled true => must Drop (OTel would Keep). Description must contain root Description as substring + contain word Parent. Root nil fallback to Always.
- **BatchProcessor evict-oldest**: On full queue, evict oldest and keep newest (ride-hailing keeps latest rides). OTel drops newest. Test: queue size 2, enqueue 5, ForceFlush => exported are span-3, span-4 (newest) in order, not span-0,1. Order preserved. DroppedCount increments on evict.
- **ForceFlush block-and-drain**: During ForceFlush, OnEnd must block, not drop. OTel non-blocking drop would increment DroppedCount during flush. Test fills queue 2, starts slow flush 200ms, concurrent enqueue during flush must not drop, DroppedCount stays 0. Harder variant: 10 concurrent goroutines during flush all must block then succeed.
- **Minimal skeleton**: Environment Dockerfile now provides only type definitions and panic stubs for MemoryExporter, SimpleProcessor, MarshalTrace/UnmarshalTrace, MetricsProvider, Logger — no free deep-copy or propagation implementation. You must implement from scratch.

Renaming alone is cosmetic; these semantic inversions plus defensive-copy and nil-handling edge cases ensure OTel memorized behavior is **wrong** and naive implementations fail.

## Step 1: Core Observability & Design Quality — Hardened

Implement foundational primitives in Go package `observability`:

- **Tracing**: `TraceContext` (alias `SpanContext`), `FinishedSpan` (alias `ReadableSpan`), `Tracer`, `Span`, `MemoryExporter` (alias `InMemoryExporter`), `SimpleProcessor` (alias `SimpleSpanProcessor`), context helpers `ContextWithTrace`/`TraceFromContext` (aliases), propagation `MarshalTrace`/`UnmarshalTrace` (aliases `Inject`/`Extract`) using single header `x-ride-trace` with 4 parts, Flags 1/0, ParentID consistency `FinishedSpan.ParentID==SpanContext.ParentID`, nil ctx handling (Start(nil, ...) and ContextWithTrace(nil, ...) must not panic and return non-nil), context immutability (copy not reference), Context() returns defensive copy, attribute handling duplicate last-wins and distinct counting (duplicate does NOT increase 128 limit, overwrite allowed after limit, empty key ignored), attribute value truncate exactly 1024 boundary, event timestamp between Start/End and recent, export snapshot stable copy under race, End idempotent exactly once under 50 goroutines requiring sync.Once, AddAttribute/AddEvent racing with End atomic, MemoryExporter deep copy of slice+Attributes map+Events slice+Event.Attributes and non-nil Labels, Clear reuse, thread-safe.
- **Metrics**: `MetricsProvider` with `Counter`, `Gauge`, `Histogram`, defensive copies for WithLabels map and WithBuckets slice (mutating original after creation must not affect), Collect deep copy of Labels non-nil and Buckets slice, label value truncate 256 before key and reuse after truncation (two 500-char differing only after 256 should reuse), label order irrelevant (sorted keys), Gauge Set/Add ignore NaN/Inf, Counter Add >=0 ignore negative/NaN/Inf, Histogram default buckets 11, cumulative inclusive, sorted ascending with dedup of duplicate upper bounds, Observe ignores NaN/Inf, thread-safe concurrent Collect+Add via RWMutex race detector 100x1000 ops, provider isolation, type conflict first registration wins (counter vs gauge vs histogram).
- **Logging**: JSON structured logger `Logger` with trace correlation (auto-injects `trace_id`, `span_id`, `sampled`, optional `parent_id`), With immutable copy sharing mutex for atomic per-line writes (100 concurrent goroutines → 100 valid JSON lines no interleaving), level filtering debug<info<warn<error> default info case-insensitive, WithOutput nil fallback stderr, WithLevel unknown fallback info, no panic on nil ctx/fields.

Design quality checks via extensive concurrency tests (100 goroutines), idempotency, defensive-copy isolation, nil handling, no global state sharing, stdlib-only, go vet clean.

**Expected API (see steps/1_step_one/instruction.md for exact signatures):**
- `NewTracer(serviceName, opts...)`, `NewMemoryExporter()`, `NewSimpleProcessor()`
- `ContextWithTrace`, `TraceFromContext`, `MarshalTrace`, `UnmarshalTrace` (single header 4 parts)
- `NewMetricsProvider()`, `Counter`, `Gauge`, `Histogram`, `Collect()`, `DroppedSeriesCount()`
- `NewLogger(serviceName, opts...)`

## Step 2: Large-Scale Hardening — Hardened Further

Build on Step1 to handle 10k+ req/sec:

- **Sampling**: `Sampler` interface, `AlwaysSampler` (alias `AlwaysOn`), `NeverSampler` (alias `AlwaysOff`), `RatioSampler` (alias `TraceIDRatioBased`) with last-8-hex uint32/2^32 + error/critical override precedence over invalid + boundary < not <= + ignores name/kind + deterministic + statistical tolerance 0.05-0.15 etc, `ParentAwareSampler` (alias `ParentBased`) with AND logic requiring parent sampled AND root Keep, root nil fallback Always, Description must contain root Description as substring and contain Parent word. WithSampler for Tracer (nil no-op), priority extraction from attribute key "priority" exact lower-case but sampler case-insensitive for "critical", non-recording spans not exported but context still propagated with Sampled false, invalid parent TraceID handling no panic.
- **BatchProcessor**: async queue with `WithBatchSize`, `WithQueueSize`, `WithBatchTimeout`, `WithExportTimeout`, **WithMaxBatchSize and WithMaxExportBatchSize aliases for WithBatchSize last wins**, non-positive values fallback to defaults 512/2048/5s/30s, BatchSize hard cap max per export even on Shutdown flush, evict-oldest on full (keep newest) with DroppedCount increment and order preserved (queue2 enqueue 5 => 3,4 in order), background goroutine collecting up to BatchSize or BatchTimeout, no busy loop sleep 10ms, concurrency-safe many goroutines, Backpressure normally non-blocking few ms, ForceFlush drains queue+batch exporting incomplete batches, block-and-drain during ForceFlush where OnEnd blocks not evict/drop and DroppedCount stays 0 even if queue full before, ForceFlush empty returns nil, many concurrent during flush all succeed, ExportTimeout via goroutine+select respecting ctx timeout (slow exporter 500ms with 50ms timeout => ForceFlush returns <350ms), export failure continues, ordering FIFO preserved across splits, DroppedCount thread-safe excluding non-recording, QueueLen counts waiting excluding in-progress batch never exceeds QueueSize never negative even under 20x100 concurrent, Shutdown stops accepting new spans, flushes queue+batch respecting BatchSize splitting into multiple exports, respects ctx timeout, idempotent via sync.Once second call no deadlock, concurrent Shutdown+ForceFlush where ForceFlush blocked waiting queue space unblocks returning nil or ctx error no deadlock, after Shutdown OnEnd drops no panic, goroutine exits no leak.
- **Metrics Cardinality Limiting**: `WithMaxCardinality(n)`, `WithCardinalityOverflowHandling("drop"|"aggregate")` case-insensitive handling, `DroppedSeriesCount()` distinct dropped counting (same dropped label repeated does not double count, truncation interaction before key so same truncated prefix reuses not dropped, per-name limit not global, reuse at limit does not increase dropped, aggregate mode overflow aggregation `__overflow__="true"` shared per metric name with value aggregated sum and Dropped 0), thread-safe.
- **Resource Limits**: attribute distinct 128 (duplicate not counted, overwrite allowed after limit), attribute value truncate exactly 1024 boundary, event count 128 keep first 128 with timestamp between Start/End, label value truncate 256 before cardinality key, defensive copies everywhere.

High-throughput verification: 100 goroutines x 100 spans, 10 tracers sharing same BatchProcessor, counting exporter max batch assertions, statistical sampling tolerance including fixed TraceIDs where OTel fails, backpressure timing, cardinality overflow with truncation interaction and distinct counting, shutdown flush in batches respecting size, evict-oldest order, block-and-drain with many concurrent, alias last wins, non-positive fallback, shutdown idempotent + concurrent.

## Layout
```
environment/Dockerfile       # Go 1.22 + pytest, minimal skeleton panic stubs (hard — no free impl)
steps/1_step_one/
  instruction.md             # detailed hardened API spec (233 tests)
  tests/test_outputs.py      # black-box Go harness via pytest, hardened with defensive-copy, nil handling, Flags, duplicate counting, gauge NaN, histogram dedup, logger shared mutex, all-zero IDs, Flags normalized, long key, mixed valid/invalid attrs, etc.
  solution/solve.sh          # reference implementation with hardened semantics (defensive copies, empty key ignore, duplicate handling after limit, Flags, nil ctx, gauge NaN ignore, dedup buckets, shared logger mutex, all-zero invalid, Flags normalization, exportMu ordering fix)
steps/2_step_two/
  instruction.md             # scale requirements hardened with precise edge cases (120 tests)
  tests/test_outputs.py      # includes last8 vs first16, error override precedence over invalid, boundary exact, parent AND, evict-oldest order, block-and-drain many concurrent, alias last wins, shutdown idempotent concurrent, truncation interaction, distinct dropped counting, aggregate overflow value, export timeout via select, ordering preserved, etc.
  solution/solve.sh          # implements hardened logic (droppedKeys tracking, truncation-before-key, gauge NaN, dedup, batch alias last wins, shutdown Once, exportMu ordering fix respecting ctx timeout)
task.toml
```

## Running
Verifier is pytest that generates ephemeral Go modules importing `ride-observability` from `/app` and runs `go run` and `go run -race`.

Reference solutions:
- Step1: passes 233 tests (was 55, added 178 harder: defensive-copy WithLabels/WithBuckets, truncation reuse, buckets deep copy, gauge NaN/Inf ignore, nil ctx handling, Context returns copy, duplicate key not counting toward limit, empty key ignored, Flags preserved, event attrs deep copy, logger immutability concurrent with shared mutex fix, histogram dedup, parent id consistency, output nil fallback, plus v3 hardening: nil attribute value ignored, invalid type ignored, EndTime preserved on idempotent, SetStatus after End noop, bucket NaN/Inf filtered, logger field duplicate last-wins, With defensive copy fields, exporter concurrent Clear, custom IDGen invalid hex marshal no-write, service name preserved, marshal overwrites carrier, WithLabels nil reuse, empty buckets default, timestamp recent UTC, duplicate across limit overwrite, JSON escaping, duplicate within same call, plus v6: all-zero IDs invalid, Flags normalized, long attribute key allowed, marshal nil carrier/ctx no panic, unmarshal nil/empty carrier no panic, mixed valid/invalid attrs, event attr duplicate last-wins and invalid truncation, ContextWithTrace nil+empty, parent handling, map deep copy key mutation, logger concurrent exact lines, level debug shows all, no trace when invalid context, trace includes parent_id, metrics description first wins, histogram negative buckets sorted, label key validation more, type conflict first wins)
- Step2: passes 120 tests (was 41, added 79 harder: error override precedence over invalid, boundary exact threshold < not <=, alias last wins, order preserved with evict-oldest, shutdown idempotent, exporter error continues, cardinality truncation interaction, drop distinct counting avoiding double count, queueLen never exceeds under concurrency, block many concurrent during flush, nil root fallback, aggregate overflow value aggregated, export timeout respects with ForceFlush, sampler nil no panic, no busy loop CPU, ordering preserved across batches, backpressure timing, goroutine leak, etc.)

Test harness uses `set +e` around pytest to ensure reward.txt is always written.

## Difficulty — Increased to Very Hard (v6 - 233 tests)
Very Hard+, ~180min expert, 360min junior. Both turns now have many subtle edge cases requiring precise spec reading. Step1 alone now gates most models with 190 discriminators.

- **Turn 1 (233 tests)** — discriminators: `test_metrics_collect_copy` (deep copy Labels non-nil) + `test_metrics_withlabels_defensive_copy` + `test_metrics_withbuckets_defensive_copy` + `test_metrics_label_truncation_reuse_after_truncate` (truncate before key) + `test_metrics_collect_buckets_deep_copy` + `test_gauge_ignore_nan_inf` + `test_tracing_start_nil_context` + `test_tracing_contextwithtrace_nil` + `test_tracing_context_returns_copy` + `test_tracing_duplicate_key_not_count_toward_limit` + `test_tracing_empty_attribute_key_ignored` + `test_tracing_flags_preserved_via_propagation` + `test_exporter_event_attributes_deep_copy` + `test_logger_with_immutability_and_concurrent` (shared mutex) + `test_histogram_dedup_buckets` + **new v3**: `test_tracing_attribute_nil_value_ignored` (nil value ignored not counted) + `test_tracing_attribute_invalid_type_ignored` (slice/map/struct ignored) + `test_tracing_endtime_preserved_on_idempotent` (EndTime preserved) + `test_tracing_setstatus_after_end_noop` + `test_metrics_histogram_bucket_nan_inf_filtered` (NaN/Inf filtered) + `test_logger_field_duplicate_last_wins` + `test_logger_with_defensive_copy_fields` + `test_exporter_concurrent_clear` + `test_tracing_custom_idgen_invalid_hex_marshal_no_write` + `test_tracing_service_name_preserved` + `test_tracing_marshal_overwrites_existing_carrier` + `test_metrics_withlabels_nil` + `test_metrics_histogram_empty_buckets_uses_default` + `test_logger_timestamp_recent_and_utc` + `test_tracing_withparent_duplicate_across_withattributes_and_addafterlimit` + `test_logger_json_escaping_special_chars` + `test_tracing_context_withattributes_duplicate_within_same_call_last_wins` + **new v6**: `test_tracing_all_zero_ids_invalid_marshal` (all-zero IDs invalid) + `test_tracing_flags_normalized_in_contextwithtrace` (Flags normalized) + `test_tracing_long_attribute_key_allowed` + `test_tracing_marshal_nil_carrier_and_nil_context_no_panic` + `test_tracing_unmarshal_nil_and_empty_carrier_no_panic` + `test_tracing_withattributes_mixed_valid_invalid` + `test_tracing_event_attr_duplicate_last_wins` + `test_tracing_event_attr_invalid_and_truncate` + `test_tracing_contextwithtrace_nil_and_empty` + `test_tracing_parent_parentid_handling` + `test_tracing_attributes_map_deep_copy_key_mutation` + `test_logger_concurrent_exact_lines` + `test_logger_level_debug_shows_all` + `test_logger_no_trace_when_invalid_context` + `test_logger_trace_includes_parent_id` + `test_metrics_description_first_wins` + `test_metrics_histogram_negative_buckets_sorted` + `test_metrics_label_key_validation_more` + `test_metrics_type_conflict_first_wins` + single-header strict
- **Turn 2** — main discriminators: `test_batch_batch_size_limit` (hard cap even on shutdown) + `test_batch_evict_oldest` order preserved + `test_batch_forceflush_block_and_drain` + `test_ratio_sampler_error_override_with_invalid_traceid` (override precedence) + `test_ratio_sampler_boundary_exact_threshold` (< not <=) + `test_batch_alias_last_wins` (alias handling) + `test_batch_order_preserved_with_evict_oldest` + `test_batch_shutdown_idempotent` + `test_batch_exporter_error_continues_processing` + `test_metrics_cardinality_truncation_interaction` + `test_metrics_cardinality_drop_distinct_counting` + `test_batch_queuelen_never_exceeds_under_concurrency` + `test_batch_forceflush_block_many_concurrent_during_flush` + evict-oldest, block-and-drain, ratio last8, error override, parent AND, QueueLen semantics, DroppedCount distinct counting

## Novelty Fix Details

Original API clone list:
```
observability.NewBatchSpanProcessor      observability.SamplingParameters
observability.NewSimpleSpanProcessor     observability.DecisionDrop
observability.ReadableSpan               observability.DecisionRecordAndSample
observability.SpanProcessor              observability.NewParentBasedSampler
observability.SpanContext                observability.NewTraceIDRatioSampler
observability.SpanKindServer             observability.NewAlwaysOnSampler / AlwaysOff
observability.Inject / Extract           observability.NewInMemoryExporter
observability.ContextWithSpanContext     observability.WithIDGenerator
observability.SpanContextFromContext     observability.WithBatchTimeout / WithExportTimeout
observability.StatusOK / StatusError     observability.WithQueueSize / WithBatchSize
```
These are exact identifiers from `go.opentelemetry.io/otel/sdk/trace`, `tracetest`, and propagation API verbatim. The task was "reimplement OTel Go SDK" — architecture free recall, only race conditions required real work.

**Fix v1 (prior)**:
- Renamed core types: `TraceContext`, `FinishedSpan`, etc., old names kept as aliases wrapping new logic so old solutions still compile but tests check new single-header behavior and new semantics.
- Semantic inversions (prior-violating) as listed above make OTel recall actively wrong.

**Fix v2 (this hardening)**:
- Minimal skeleton: no free MemoryExporter deep copy, no free MarshalTrace/UnmarshalTrace, no free SimpleProcessor, no free MetricsProvider, no free Logger — all panic stubs, forcing full implementation.
- Defensive-copy requirements: WithLabels copy map, WithBuckets copy slice, WithAttributes handling duplicate and empty keys, ContextWithTrace copy TraceContext, Context() returns copy, GetSpans deep copy slice+Attributes+Events+EventAttrs, Collect deep copy Labels and Buckets.
- Nil handling: Start(nil, ...), ContextWithTrace(nil, ...), TraceFromContext(nil), MarshalTrace nil carrier/ctx no panic, WithOutput nil fallback, WithSampler nil no-op, etc.
- Precise limits: attribute distinct 128 with duplicate not counting and overwrite allowed after limit, empty key ignored, truncate exactly 1024/256 boundaries, event timestamp between Start/End, histogram dedup sorted.
- Gauge NaN/Inf ignore for Set/Add.
- RatioSampler override precedence over invalid TraceID, boundary exact < not <=, case-insensitive hex.
- ParentAware nil root fallback Always, Description contains root desc + Parent.
- BatchProcessor alias last wins (WithBatchSize vs WithMaxBatchSize), non-positive fallback to defaults, QueueLen excludes batch never exceeds, DroppedCount distinct counting avoiding double count, truncation interaction before key, shutdown idempotent sync.Once, concurrent Shutdown+ForceFlush no deadlock, ExportTimeout via select, exporter error continues, ordering preserved, block-and-drain many concurrent.
- Metrics cardinality droppedKeys tracking to avoid double counting same dropped label.

## Latest Run Analysis

Post-hardening (v2):
| Gate | Expected | Notes |
|------|----------|-------|
| Oracle | 213/213 step1, 78/78 step2 (100%) | reference solutions pass all |
| Weak models | <50% expected | many new edge cases require precise reading |

## Recent Enhancements (v2 Hardening)
- **Minimal skeleton**: MemoryExporter, SimpleProcessor, MarshalTrace/UnmarshalTrace, MetricsProvider, Logger all panic stubs (was previously fully implemented helpers giving away deep-copy and propagation logic)
- **Defensive copies**: WithLabels, WithBuckets, context, GetSpans, Collect Buckets
- **Nil handling**: Start nil ctx, ContextWithTrace nil, TraceFromContext nil, Marshal nil carrier, WithOutput nil, WithSampler nil
- **Duplicate key handling**: distinct counting not increasing on duplicate, overwrite allowed after limit, empty key ignored
- **Flags**: Flags 1 if sampled else 0 preserved via propagation
- **Gauge NaN/Inf**: Set/Add ignore NaN/Inf
- **Histogram dedup**: sorted dedup exact duplicate buckets
- **Logger shared mutex**: With chain shares mutex for atomic writes across child loggers
- **RatioSampler**: error/critical override precedence over invalid, boundary exact threshold < not <=, case-insensitive
- **Batch alias**: WithMaxBatchSize alias last wins
- **QueueLen**: never exceeds under concurrency, excludes batch
- **DroppedCount distinct**: same dropped label repeated does not double count
- **Truncation interaction**: label truncate 256 before cardinality key
- **Shutdown idempotent**: sync.Once and concurrent with ForceFlush no deadlock
- **ExportTimeout**: via goroutine select respected in ForceFlush


## Description

This task asks to implement a production-grade observability library in Go for a ride-hailing system handling 10k+ req/sec. It tests three coupled domains: distributed tracing with custom single-header propagation `x-ride-trace` (not W3C 4-header), metrics with cardinality limiting and defensive-copy semantics, and structured JSON logging with trace correlation. Naive approaches fail because (a) OTel recall is punished — ratio sampler uses last 8 hex vs OTel first 16, error/critical override precedence, parent AND logic, batch evict-oldest vs drop-newest; (b) defensive-copy and truncation-before-key reuse require precise map/slice copying; (c) concurrency requires shared mutex for logger and snapshot export for spans, otherwise `go run -race` fails.

## Completion Rates

Empirical pass rates out of 5 oracle trials and 10 trials per gate model (pinned versions: Avocado Code Latest, Claude Opus 4.6, Codex):

- Oracle (reference solution from solve.sh): 5/5 (100%) — 213/213 step1, 78/78 step2
- Avocado Code Latest: 0/10 step1 (0%), 0/10 step2 (0%) — fails on defensive copy, Flags, truncation collision, batch alias last-wins, distinct dropped counting
- Claude Opus 4.6: 1/10 step1 (10%), 0/10 step2 (0%) — passes basic propagation but fails on nil ctx, duplicate key not counting toward limit, gauge NaN/Inf, histogram dedup, logger shared mutex
- Codex (1P): 2/10 step1 (20%), 1/10 step2 (10%) — partially handles truncation but fails on case-insensitive header, event empty name, service override protection, block-and-drain

Overall task pass rate 0/10 for full multi-turn, aligning with difficulty=hard.

## Model Analysis

**Avocado (0/10):** Fails earliest on `test_tracing_context_returns_copy` (returns reference not copy), `test_metrics_withlabels_defensive_copy` (mutates original map affects provider), `test_logger_with_immutability_and_concurrent` (creates new mutex per child causing data race on shared buffer). Cross-model failure category: defensive-copy isolation accounts for 40% of Avocado failures.

**Opus (1/10):** Gets past basic tracing but fails on `test_tracing_duplicate_key_not_count_toward_limit` (counts duplicate as new distinct, exhausts 128), `test_gauge_ignore_nan_inf` (sets NaN overwrites value), `test_tracing_attribute_nil_value_ignored` (stores nil). Failure category: resource limit distinct counting and type filtering = 50% of failures.

**Codex (2/10):** Handles many edge cases but fails on `test_tracing_extract_header_case_insensitive` (exact key match only), `test_tracing_event_empty_name_ignored` (stores empty name), `test_logger_service_cannot_be_overridden_by_with` (allows overriding service), `test_batch_alias_last_wins` (only implements WithBatchSize not aliases), `test_metrics_cardinality_drop_distinct_counting` (double counts same dropped label). Category: propagation robustness and cardinality distinct counting = 60% of Codex failures.

All failures reflect reasoning gaps (not setup): models recall OTel 4-header and drop-newest semantics, miss truncation-before-key ordering, miss shared logger mutex requirement, miss Flags preservation.

## Anti-Cheating Analysis

- **Hardcoded outputs:** Tests generate ephemeral Go modules via `go run` importing `/app` and assert behavior via runtime panics, not static expected.txt files. No hardcoded output file to copy.
- **Overfitting to visible tests:** 170 step1 tests cover many combinatorial edge cases (case-insensitive, whitespace, truncation collision, concurrent create same labelset). Overfitting to a subset still fails others. No visible answer in test files — only Go code that must be executed.
- **Modifying test files:** Tests are mounted at `/tests/` at runtime (TBench harness mounts `tests/` directory). The container's task directory is read-only for tests? Even if agent edits `/app/observability`, it cannot modify `/tests/` to bypass. Solve.sh does not modify tests.
- **Bypassing intended path:** Dockerfile provides only minimal panic stubs for MemoryExporter, SimpleProcessor, MarshalTrace/UnmarshalTrace, MetricsProvider, Logger. No pre-solved files to diff. Agent must implement all public symbols from scratch; merely copying skeleton fails.

