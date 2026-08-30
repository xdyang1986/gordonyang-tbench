# Step 2: High-Performance Optimization

You have a working ingest/search service from Step 1. Now it must meet production SLOs:

- **Ingest**: sustain high-throughput bulk ingest (thousands docs/sec) without drop.
- **Search**: low latency under load and idle, concurrent writers + readers no crash/deadlock/corruption.

The naive approach (single global lock, sync disk per request, no caching/batching/sharding) will fail perf tests. Optimize while preserving correctness.

## Context

- `/app/main.go` and `go.mod` exist and work for Step 1 correctness.
- `/app/data/index.json` and `wal.log` may exist.
- Server already exposes `/ingest`, `/documents/{id}`, `/search`, `/stats`, `/health`.
- You must evolve it.

## Requirements

### 1. Config File — Mandatory

Create/update `/app/config.yaml` read on startup. If missing or partial, use sensible defaults that still meet SLOs.

Required keys and minimums checked by tests:

- `ingest.workers` >=2
- `ingest.batch_size` >=100
- `search.cache_size` >=50
- `search.shard_count` >=2

Expected structure (values are examples, you tune for perf):

```yaml
ingest:
  workers: 4
  batch_size: 500
  flush_interval_ms: 100
  queue_size: 10000
search:
  cache_size: 100
  cache_ttl_ms: 5000
  shard_count: 4
persistence:
  async_writes: true
  wal_buffer_size: 1000
  sync_interval_ms: 1000
server:
  read_timeout_ms: 5000
  write_timeout_ms: 10000
  max_concurrent_search: 50
```

Parser must handle inline comments: lines like `workers: 4  # pool size` should parse as 4, not fail. Tests include such cases. Strip comments before int parsing or use real YAML parsing.

### 2. High-Throughput Bulk Ingest — Mandatory

`POST /ingest/bulk` must be fully implemented and optimized:

- Accepts NDJSON (Content-Type `application/x-ndjson` or `application/json`, treat body as NDJSON regardless).
- Body: lines each JSON log entry, empty lines skipped.
- Validation same as Step 1 but per-line: ingest valid lines, skip invalid with error list.
- Response 201: `{"ingested": 1000, "failed": 5, "errors": [{"line":3,"error":"..."}]}`

Performance expectations:

- 10k docs in one request must complete quickly (throughput target >2k docs/sec). Localhost loopback, but tests allow generous timeout.
- 4 concurrent bulk 5k each (20k total) must complete quickly and all docs searchable.
- Need batching and parallel indexing: avoid fsync per doc, avoid holding a single global lock per doc.

### 3. Sharded Index — Mandatory

Reduce contention by sharding:

- Shard count from config, default >=2, min 2.
- Assign each doc to a shard deterministically by its id (hash mod shard_count).
- Each shard should be independently lockable (own mutex, doc store, inverted index).
- Search should be parallel across shards and merged centrally, then filtered/sorted/paginated.
- Persistence, WAL, and correctness must still work with sharding.

Tests verify:
- `/metrics` reports shards == configured.
- Under high concurrency, no deadlocks and doc count correct.
- Server actually uses sharding to improve parallelism, not just reports a fake number.

### 4. Query Result Cache — Mandatory

Search results should be cached for repeated queries:

- Cache query results for identical filter combinations (consider q, service, level, tags, from, to, sort; exclude pagination? You decide but be consistent).
- LRU with capacity from config, TTL from config.
- Track hits/misses.
- On any mutation (ingest/delete), invalidate cache to ensure correctness. Simplest: clear on write, but you may implement finer-grained invalidation as long as correctness holds.
- Kept correct after writes: cache must not serve stale data.

Tests:
- Repeated identical query should hit cache (metrics shows increasing hits, hit_rate >0.5).
- After a write, cached entries must be invalidated (old result not returned for new doc).

### 5. GET /metrics — Mandatory

Expose:

```json
{
  "ingest": {"total_docs": 20000, "rate_per_sec": 3500.5, "queue_depth": 0, "workers": 4},
  "search": {"total_queries": 100, "avg_latency_ms": 12.3, "p50_ms": 10, "p99_ms": 45, "cache_hits": 30, "cache_misses": 70, "cache_hit_rate": 0.3, "cache_size": 5},
  "index": {"docs": 20000, "shards": 4, "terms": 1234}
}
```

Minimum: top-level `ingest`, `search`, `index` with listed sub-fields. Types float/int accepted. `ingest.total_docs` must match stats docs. cache hit/miss must evolve.

### 6. Performance SLOs (graded)

Throughput:
- Bulk 10k single request: must complete within threshold (generous to avoid hardware flake) and all searchable.
- 4 concurrent bulk 5k each: within threshold, total docs = 20k (+ pre-existing for that test).

Latency:
- 500 sequential searches idle: average and p99 must be within generous thresholds (2 CPUs).
- 200 searches during 2 concurrent bulk 5k ingests: avg and p99 higher allowed but must succeed.

Correctness still:
- After perf tests, search AND, phrase, filters, time range, sort, stats, invalid inputs still correct.
- Persistence after high-throughput: restart and verify recovery via index.json or WAL.

Concurrency:
- 10 writers bulk + 20 readers for several seconds: no crash, stays healthy.

Cache:
- Repeated query benefits from cache.

### 7. Operational

- Config missing → defaults that still pass.
- Do not delete Step 1 endpoints.
- Preserve WAL and index.json durability. After kill -TERM or abrupt stop, WAL replay must recover async buffered writes. WAL itself should be written reliably per batch.
- Respect `DATA_FILE` env.
- Fork-safe persistence.
- Validate `go.mod` still has no forbidden search libraries.
- WAL must reject bad checksums and skip corrupt lines without crashing.

### 8. Validation Steps (what tests do)

1. Build binary and start with config.yaml.
2. Check config exists and minimums.
3. Check forbidden libs not in go.mod.
4. Check DATA_FILE env handling.
5. Check WAL checksum rejection and corrupt-line handling.
6. Check cache invalidation after writes.
7. Check actual sharding vs fake reporting.
8. Check worker-pool / config semantics are honored.
9. Subset of Step 1 correctness.
10. Bulk throughput.
11. Latency idle and under load (relaxed thresholds).
12. Concurrency stress.
13. Cache hit rate.
14. Metrics.
15. Persistence after high-throughput.

Implement efficiently but correctly.

Good luck!
