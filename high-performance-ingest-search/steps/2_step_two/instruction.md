# Step 2: High-Performance Optimization — Ingest Throughput and Search Latency

You completed Step 1: a functional log ingest and search system is running at `/app` with `main.go` working.

Now the business has **high-performance requirements**:

- **Ingest throughput**: must sustain **> 2,000 docs/sec** bulk ingest (target 5k/s) for 20k logs without dropping.
- **Search latency**: p50 < 50ms, p99 < 200ms for 500 queries under concurrent ingest load, average < 50ms when idle.
- **Concurrency**: 10 writers + 20 readers concurrently must not crash, deadlock, or corrupt index.

The naive Step 1 implementation (single global RWMutex, synchronous disk persist per request, no caching, no batching, flat map scan for filters) will **fail Step 2 performance tests**. You must optimize.

## Context Inherited from Step 1

- `/app/main.go` exists and works
- `/app/go.mod` exists
- `/app/data/index.json` and `/app/data/wal.log` may exist with historic data
- Server already exposes `/ingest`, `/documents/{id}`, `/search`, `/stats`, `/health`
- Need to evolve for performance while preserving correctness

## Task Requirements

### 1. Config File — Mandatory

Create/update `/app/config.yaml` that the server reads on startup:

```yaml
ingest:
  workers: 4              # worker pool size for indexing, must be >=2
  batch_size: 500          # batch flush size
  flush_interval_ms: 100  # max ms to batch before flush
  queue_size: 10000       # internal queue buffer

search:
  cache_size: 100         # LRU cache capacity for query results
  cache_ttl_ms: 5000      # TTL for cached entries
  shard_count: 4          # number of index shards for concurrent access

persistence:
  async_writes: true      # if true, WAL buffered + async index.json writes
  wal_buffer_size: 1000
  sync_interval_ms: 1000

server:
  read_timeout_ms: 5000
  write_timeout_ms: 10000
  max_concurrent_search: 50
```

- Server must read `/app/config.yaml` if present on startup. If file missing or partial, use sensible defaults that still pass perf tests.
- Update `/app/config.yaml` to reflect high-performance mode. The test will check file exists and has at least keys `ingest.workers`, `ingest.batch_size`, `search.cache_size`, `search.shard_count` with values meeting minimums: workers >=2, batch_size >=100, cache_size >=50, shard_count >=2.
- Your `main.go` must implement config reading.

### 2. High-Throughput Bulk Ingest Endpoint — Mandatory

**POST /ingest/bulk** must be fully implemented and optimized:

- Accepts `Content-Type: application/x-ndjson` or `application/json` (treat body as NDJSON regardless)
- Body: NDJSON lines, each line JSON log entry. Empty lines skip.
- Response 201 same as Step 1 bulk spec:
  ```json
  {"ingested": 1000, "failed": 5, "errors": [{"line":3,"error":"..."}]}
  ```
- **Performance requirement**: Must support high throughput via:
  - **Worker pool**: parse NDJSON lines and push to channel queue, workers consume and index in parallel batches.
  - **Batching**: accumulate batch_size or flush_interval_ms whichever first, then bulk index + bulk WAL append + async persistence.
  - Do NOT fsync index.json per entry; batch write or async periodic sync.

Tests will:

- Send 10,000 docs via bulk in one request: must complete within **5 seconds** (2k docs/sec minimum) including HTTP.
- Send 20,000 docs via 4 concurrent bulk requests (5k each): must complete within **10 seconds** total and total docs in stats must be 20k (+ pre-existing).
- No data loss: after bulk, all ingested docs searchable.

Naive implementation that holds global write lock per doc AND does synchronous file write per doc will timeout and fail throughput test.

### 3. Sharded Index

Implement sharded inverted index to reduce contention:

- Shard count from config `search.shard_count`, default 4, minimum 2.
- Sharding strategy: hash doc id (e.g., FNV or crc32) modulo shard_count to assign doc to shard.
- Each shard has its own `RWMutex`, doc store map, and inverted index (`term->docSet`).
- Search must scatter-gather: query all shards in parallel (goroutines) and merge results, then filter/sort/paginate centrally.
- This improves concurrent read/write: writes to different shards don't block each other; many concurrent reads scale.

Tests will check concurrency: 10 concurrent bulk ingest writers + 20 concurrent readers doing searches; server must stay alive, no deadlocks, final doc count correct, search returns valid.

### 4. Query Result Cache

Implement LRU cache with TTL for search queries:

- Cache key: canonical representation of (q, service, level, tags, from, to, sort) — exclude limit/offset? For simplicity include all params except `took_ms`. So identical queries hit cache.
- Cache value: full result set before pagination? Simplest: cache list of matching doc IDs + total + scores computed, then apply pagination per request.
- LRU capacity from config `search.cache_size` (>=50).
- TTL from `search.cache_ttl_ms` (entries expire after TTL).
- Must track hit/miss metrics.
- On ingest/delete, must **invalidate** cache (or entries whose filter could be affected). Simplest: clear entire cache on any mutation (ingest/delete). This ensures correctness after writes.
- Expected behavior: repeated identical search queries should be served faster (cache hit). Tests will verify cache metrics via `/metrics` endpoint and performance benefit.

### 5. GET /metrics — Mandatory New Endpoint

For observability, expose:

```
GET /metrics
200 {
  "ingest": {
    "total_docs": 20000,
    "rate_per_sec": 3500.5,
    "queue_depth": 0,
    "workers": 4
  },
  "search": {
    "total_queries": 100,
    "avg_latency_ms": 12.3,
    "p50_ms": 10,
    "p99_ms": 45,
    "cache_hits": 30,
    "cache_misses": 70,
    "cache_hit_rate": 0.3,
    "cache_size": 5
  },
  "index": {
    "docs": 20000,
    "shards": 4,
    "terms": 1234
  }
}
```

- At minimum must have top-level `ingest`, `search`, `index` keys with listed sub-fields (types as shown float/int allowed).
- `ingest.total_docs` must equal stats docs.
- `search.cache_hits/misses` must increase appropriately; after cache clear on ingest, subsequent queries are misses.
- Tests will check existence and that cache hit rate increases after repeated queries.

### 6. Optimizations Required (Graded via Performance Tests)

You must ensure:

#### Throughput
- Bulk ingest 10k docs in one request <5 sec (test measures server side plus client HTTP; client is localhost loopback, so network negligible).
- 4 concurrent bulk 5k each (20k total) <10 sec.

#### Latency
- 500 sequential search queries (random q + filters) idle (no concurrent ingest):
  - avg latency < 50ms
  - p99 < 200ms
- 200 queries while 2 bulk ingest of 5k docs ongoing (concurrent load):
  - avg < 100ms
  - p99 < 400ms
  - no failures (all 200 return 200)

#### Correctness Under Optimization

- After all perf tests, correctness still holds: search AND, phrase, filters, time range, tags, sort must still return correct results as in Step 1.
- Persistence must still work: after bulk perf test, restart server and verify docs recovered via index.json or WAL.

#### Implementation Hints (not strict, but will help pass)

- Use buffered channel for ingest queue: `chan Doc` size `queue_size`.
- Workers: `workers` goroutines loop batch accumulation. Use `time.Ticker` for flush interval.
- For regular POST /ingest (non-bulk), you can reuse same batched path or process immediately but still use shard locks not global lock.
- Sharding: `func shardID(id string, shardCount int) int { h:=fnv.New32a(); h.Write([]byte(id)); return int(h.Sum32()) % shardCount }`
- Search scatter-gather: launch goroutine per shard, each returns matching IDs with scores, merge via channel.
- Cache: simple LRU using `container/list` + map + mutex, with expiry check on get. Key = `fmt.Sprintf("%s|%s|%s|%s|%s|%s|%s", q, service, level, tags, from, to, sort)`. Store struct with expiry time and results.
- Async persistence: after batch, append WAL buffered (write to file but not necessarily fsync each time), and periodic sync index.json in background goroutine every `sync_interval_ms` or after N batches. Must still ensure durability on graceful shutdown (fsync on SIGTERM if possible, or at least final flush).

### 7. Graceful Degradation

- Config may be missing → defaults that still meet perf SLOs (workers=4, shard=4, cache 100).
- Do NOT delete Step 1 endpoints or functionality.
- Preserve WAL and index.json persistence (may be async but must not lose data on normal shutdown; tests will do SIGTERM graceful? For simplicity test does restart after kill - not graceful, so WAL replay required to recover async not yet flushed to index.json - hence WAL must be written synchronously or buffered with flush on each batch).

### 8. Validation

Tests will:

1. Build binary `go build -o /tmp/highperf-server .` and start on random PORT with config.yaml present.
2. Check config.yaml exists with required fields meeting minimums.
3. Check all Step 1 functionality still passes (subset: CRUD, search AND, phrase, filters, time range, stats, persistence).
4. **Bulk throughput tests**:
   - POST /ingest/bulk 10k docs → <5 sec, ingested count matches, docs searchable.
   - 4 concurrent bulk 5k each → <10 sec, total docs correct, no corruption.
5. **Latency tests**:
   - 500 searches sequential → measure client observed latency, check avg <50ms, p99 <200ms.
   - 200 searches during concurrent bulk ingest → avg <100ms, p99 <400ms, all 200 succeed.
6. **Concurrency stress**: 10 writers bulk + 20 readers concurrent for 5 sec → no crash, final doc count plausible, server still returns 200 for health/search.
7. **Cache test**: call same search 20 times, check /metrics shows cache_hits increasing, hit_rate >0.5 after second call, repeated query latency second time significantly lower or cache hit counted.
8. **Sharding & metrics**: GET /metrics returns required fields, index.shards == config shard_count or >=2, ingest.workers >=2.
9. **Persistence after high-throughput**: restart server (kill and restart), check docs recovered >= previously ingested count.
10. **Invalid inputs still 400** (phrase empty, bad level).

Failure modes:

- Single global mutex indexing each doc separately with file sync per doc → throughput test will exceed 5 sec, fail.
- No cache → may still pass latency if sharded fast enough, but cache metrics will fail.
- No shard → concurrency test may show high contention and p99 violation.

### 9. Anti-Requirements

- No external search lib (same as Step 1).
- No hardcoding of test docs or shortcut that returns fake fast results without indexing (tests verify correctness after perf tests).
- Do NOT disable persistence to fake performance; after restart docs must be recovered.

## Deliverable

- Updated `/app/main.go` (and any other Go files) implementing high-performance features.
- `/app/config.yaml` present with high-performance tuning meeting minimums.
- Server must still support `go run .` and `go build -o /tmp/highperf-server .`.
- Ensure Step 1 tests would still pass.

Good luck!
