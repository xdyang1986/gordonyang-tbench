# Step 2: High-QPS Geofence Service with Dynamic CRUD (Hard)

## Context
You completed Step 1: a CLI geofence manager with persistent JSON DB, strict polygon validation (no duplicate, non-zero area, no self-intersection), and correct point-in-polygon lookup with edge handling.

In production, location pings arrive at high QPS and operators need to manage geofences via HTTP as well. You must evolve the same CLI to add an HTTP server that serves lookups at high QPS with spatial indexing, bounding-box prefilter, LRU cache with 6-decimal rounding, batch support, dynamic CRUD via HTTP, concurrency safety, and strict correctness.

Preserve Step 1 compatibility — all Step 1 commands must still work, including new strict validations.

## New Command: `serve`

```
geofencectl --db <PATH> serve --port <int> [--grid-size <float>] [--cache-size <int>]
```

- `--db` same global flag, path to JSON DB file. Load all geofences into memory at startup. If missing/empty -> 0 geofences. If corrupt -> stderr + exit 4.
- `--port` required int 1-65535, bind 0.0.0.0:<port>. Invalid -> exit 2.
- `--grid-size` optional float >0 <=45, default 1.0. Controls spatial index granularity.
- `--cache-size` optional int >=0, default 1000, 0 = no cache. Invalid -> exit 2.
- On success, start HTTP server, block until SIGTERM/SIGINT. Print to stdout `serving on :<port>`.
- Must use `runtime.GOMAXPROCS` to utilize available CPUs for high QPS.

### Required In-Memory Data Structures

#### 1. Bounding Boxes
Precompute min/max lat/lng per geofence. During lookup, quick reject if outside bbox (with eps 1e-9). Must be used for both CLI and HTTP.

#### 2. Grid Spatial Index
Uniform grid over world lat [-90,90], lng [-180,180]:
- Cell key `(latIdx,lngIdx)` where `latIdx=floor((lat+90)/gridSize)`, `lngIdx=floor((lng+180)/gridSize)`
- For each geofence, compute cells overlapping its bbox (inclusive range) and add geofence ID to each cell's list. Each cell's list must be kept sorted lexicographically for deterministic fast lookup.
- On query, compute cell for point, get candidates from index (empty cell -> empty result). Then bbox check, then pointInPolygon.
- Must track `index_cells` = number of cells with >=1 geofence.
- For large polygons (e.g., world bounds), grid may contain many cells (e.g., grid-size 1.0 world = 180*360=64800 cells). Implementation must handle large bboxes without OOM and still be fast for empty-area lookups.
- Index must be updatable on HTTP POST/DELETE (see CRUD). On mutation, update bboxes, grid, and invalidate cache correctly under write lock.

#### 3. LRU Cache with 6-Decimal Rounding
- Key must be lat,lng rounded to 6 decimals: e.g., `fmt.Sprintf("%.6f,%.6f", lat, lng)` or `FormatFloat(..., 'f', 6)`. This means `0.5000001,0.5000001` and `0.5000002,0.5000002` share same cache key `0.500000,0.500000`.
- Value is sorted list of matching IDs (must be `[]` not `null` for empty).
- Max entries = cache-size. On exceeding, evict least recently used (true LRU).
- Thread-safe with mutex.
- Stats: `cache_hits` increments on hit, `cache_size` current entries.
- Cache must be consulted before index search.
- On HTTP POST/DELETE that mutates geofences, cache must be invalidated (simplest: clear entire cache) to avoid stale results.
- Tests will verify rounding (nearby points hit) and eviction (LRU order).

#### 4. Concurrency Safety
- HTTP server uses net/http (each request separate goroutine).
- Shared data `db`, `bboxes`, `grid` are mutable now via HTTP CRUD, so must be protected by `sync.RWMutex`: RLock for reads (lookup), Lock for writes (POST/DELETE).
- Cache has its own mutex.
- Must not have data race (verified with concurrent clients).
- No per-request file IO: load DB once at startup, but on HTTP POST/DELETE must atomically persist to DB file (same atomic write as CLI) while also updating in-memory structures under lock.

## HTTP API (All JSON, Content-Type application/json)

### `GET /lookup?lat=<float>&lng=<float>[&verbose=true]`
- Query lat,lng required, valid range else 400 `{"error":"..."}`
- Optional `verbose` bool: if `true` (case-insensitive `true`, `1`, `t`), return full Geofence objects instead of IDs.
- Returns 200:
```json
{"lat":0.5,"lng":0.5,"geofences":["zone_a"],"count":1}
```
or verbose:
```json
{"lat":0.5,"lng":0.5,"geofences":[{"id":"zone_a",...}],"count":1}
```
- `geofences` sorted asc by ID, must be `[]` not `null` when empty. `count` = len(geofences).
- Must use index+bbox+cache internally.
- Increment `total_queries` by 1, `cache_hits` on hit.

### `POST /lookup/batch`
- Body JSON `{"points":[{"lat":0.5,"lng":0.5},...]}`
- Max 1000 points else 400 `{"error":"batch too large"}`
- Each point must have valid lat/lng else 400.
- Empty array allowed -> `{"results":[]}`
- Returns 200 `{"results":[{"lat":...,"lng":...,"geofences":[...]},...]}` order must match input order, each geofences sorted asc, `[]` not `null`.
- Preserve `verbose`? Not required for batch.
- `total_queries` += len(points), `cache_hits` per hit.
- Must be efficient: reuse indexed lookup, sequential or worker pool preserving order, no file IO.
- Performance: 500 points batch must respond within 800ms (tests).

### `GET /geofences`
- Returns 200 JSON array of all geofence objects sorted by ID asc. Empty -> `[]` not `null`.

### `GET /geofences/:id`
- Returns 200 single Geofence object if found, else 404 `{"error":"not found"}`.
- Path param ID must be validated same regex? For lookup, if ID format invalid, also 404 (not 400) to match REST.

### `POST /geofences`
- Body JSON Geofence object `{"id":"...","name":"...","polygon":[{"lat":...,"lng":...},...]}`
- Validation identical to CLI `add`: ID regex, name trimmed non-empty <=128, polygon at least 3 points <=1000, each lat/lng range, no empty duplicate, non-zero area, no self-intersection. If invalid -> 400 `{"error":"..."}`
- On success, add or overwrite in DB and in-memory, update bboxes and grid (remove old bbox cells, add new), clear cache (or invalidate affected), atomically persist DB file, return 201 with stored object JSON.
- Must be concurrency safe.

### `DELETE /geofences/:id`
- Delete by ID. If not found -> 404 `{"error":"not found"}`. Else remove from DB, bboxes, grid, clear cache, atomic persist, return 200 `{"deleted":"<id>"}`.

### `GET /stats`
- Returns 200 JSON:
```json
{
  "total_geofences":5,
  "total_queries":1234,
  "cache_hits":100,
  "cache_size":2,
  "avg_latency_ms":0.5,
  "index_cells":10,
  "cache_hit_rate":0.08
}
```
Fields: total_geofences int, total_queries int, cache_hits int, cache_size int, avg_latency_ms float, index_cells int, cache_hit_rate float (hits/queries, 0 if no queries).
- Must reflect dynamic mutations.

### Other
- Unknown path -> 404 JSON `{"error":"not found"}`
- All errors JSON `{"error":"message"}`, not plain text.
- Empty slices must be `[]`, never `null`.

### Performance Targets (Enforced)

Tests use 100-500 geofences (mix squares, concave, world, 1000-point polygons) and measure:

1. **Correctness under concurrency**: 30 concurrent clients * 100 requests = 3000 lookups, all 200 and match naive CLI results. No race.

2. **QPS throughput**: 500 geofences, 1000 random points (50% inside), 30 concurrent clients, must complete in <4s (=> >=250 QPS) and achieve >=400 QPS measured. p50 <40ms, p99 <150ms under this load. Naive scanning 500 geofences * 4 edges avg = 2000 checks per query *1000 = 2M checks, plus 1000-point polygons for some, still might be heavy. Index required to pass.

3. **Batch**: 500 points batch must <800ms.

4. **Cache**: Repeated same point (with 6-decimal rounding) should hit. Eviction: with cache_size=2, 3 unique points -> cache_size 2, LRU evicted, re-query evicted should be miss.

5. **Index**: index_cells >0, and with grid-size 0.5 has more cells than grid-size 5.0 for same data. Also world polygon must not cause OOM and lookup far away point must be fast (empty area avg <10ms even with 500 zones).

6. **CRUD**: POST adds, GET finds, lookup finds new, DELETE removes, lookup miss after delete, cache invalidated.

If any performance fails, grading fails.

### Backward Compat
- All Step1 commands still work.

### Constraints
- Stdlib only.
- Must build `go build -o geofencectl .`
- Thread-safe with RWMutex + cache mutex + atomic counters.
- No per-request file read, except atomic write on POST/DELETE.
- LRU must be own implementation.
- Empty slice `[]` not `null`.

### Example
```bash
geofencectl --db /tmp/g.json add zone_a --polygon "0,0;0,1;1,1;1,0" --name "A"
geofencectl --db /tmp/g.json serve --port 8080 --grid-size 1.0 --cache-size 1000 &
curl "http://localhost:8080/lookup?lat=0.5&lng=0.5"
curl "http://localhost:8080/lookup?lat=0.5&lng=0.5&verbose=true"
curl -X POST http://localhost:8080/geofences -H "Content-Type: application/json" -d '{"id":"zone_b","name":"B","polygon":[{"lat":10,"lng":10},{"lat":10,"lng":11},{"lat":11,"lng":11},{"lat":11,"lng":10}]}'
curl http://localhost:8080/geofences/zone_b
curl -X DELETE http://localhost:8080/geofences/zone_b
curl http://localhost:8080/stats
kill %1
```

Tests verify CLI compat, HTTP correctness (including new CRUD, verbose, rounding, eviction), concurrency, QPS >=400, p50<40ms p99<150ms, batch <800ms, cache, index, stats monotonic, no null slices, no temp leftovers.
