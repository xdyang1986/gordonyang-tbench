# Step 2: Support Large QPS — High-Throughput Geofence Service

## Context
You completed Step 1: a CLI geofence manager with persistent JSON DB and correct point-in-polygon lookup. The file `/app/src/` already contains a working `geofencectl`.

In production, location pings arrive at high QPS (thousands per second) from mobile devices. The naive Step 1 loop scanning all geofences per query cannot sustain this:

- 1000 geofences * 50 points avg ~ 50k edge checks per query * 1000 QPS = 50M checks/s -> too slow
- Single-process CLI per lookup adds process/IO overhead
- No caching for hotspots (downtown query repeated thousands of times)
- No concurrency beyond single thread

You must evolve the same CLI to add an **HTTP server** that serves geofence lookups at high QPS, with spatial indexing, bounding-box prefilter, LRU cache, batch support, and concurrency safety.

Preserve Step 1 compatibility — all Step 1 commands must still work.

## New Command: `serve`

```
geofencectl --db <PATH> serve --port <int> [--grid-size <float>] [--cache-size <int>]
```

- `--db` same global flag, path to JSON DB file. Load all geofences into memory at startup. If file missing/empty -> 0 geofences. If corrupt -> stderr + exit 4.
- `--port` required int 1-65535, listen port. Server binds 0.0.0.0:<port>. Example `--port 8080`. Invalid -> exit 2.
- `--grid-size` optional float, grid cell size in degrees, >0 and <=45. Default 1.0 degree. This controls spatial index granularity. Smaller = more cells, finer filter, more memory. Larger = fewer cells, less filtering. Validate range else exit 2.
- `--cache-size` optional int >=0, max LRU cache entries. Default 1000. 0 means no cache. Invalid -> exit 2.
- On success, start HTTP server, block until SIGTERM/SIGINT. Print to stdout `serving on :<port>` or similar (tests check server starts, not exact message, but must print something).
- Must be graceful: handle shutdown? Not required beyond blocking.

### In-Memory Data Structures (Required for High QPS)

#### 1. Bounding Boxes
For each geofence, precompute bounding box:
```
minLat = min(p.lat for p in polygon)
maxLat = max(p.lat for p in polygon)
minLng = min(p.lng ...)
maxLng = max(...)
```
During lookup, quick reject: if query lat < minLat or > maxLat or lng < minLng or > maxLng -> cannot be inside, skip expensive pointInPolygon.

#### 2. Grid Spatial Index
Implement a uniform grid index:

- World bounds: lat [-90,90], lng [-180,180]
- Cell key: (latIdx, lngIdx) where
  latIdx = floor((lat + 90)/gridSize)
  lngIdx = floor((lng + 180)/gridSize)
- For each geofence, compute cells overlapping its bounding box:
  latStart = floor((minLat+90)/gridSize), latEnd = floor((maxLat+90)/gridSize) inclusive
  lngStart = floor((minLng+180)/gridSize), lngEnd similarly
  For each latIdx in [latStart..latEnd], lngIdx in [lngStart..lngEnd], add geofence ID to cell's list.

- Data structure: `map[CellKey][]GeofenceID` or `map[string][]Geofence`. CellKey could be string like "10_20" or struct.

- On query for point (lat,lng):
  Compute cell key for point, get candidate IDs from index (if cell empty, candidates = empty -> no geofence can contain point because bbox doesn't cover cell). Then for each candidate, first bbox check (redundant but safe), then pointInPolygon. This reduces from O(N) to O(k) where k << N typically.

- Index must be built at startup and after ... Since serve loads once, no need to rebuild on runtime for this task (but if you want to support dynamic add via HTTP, not required).

- Stats: track number of grid cells that have at least one geofence (`index_cells`). Expose via /stats.

#### 3. LRU Cache
Implement an LRU cache for point lookups:

- Key: formatted lat,lng with maybe 6 decimal precision or exact string: e.g., `fmt.Sprintf("%.6f,%.6f", lat, lng)` or use `Sprintf("%f,%f", lat,lng)` but ensure deterministic. Simpler: cache exact query point string representation? Use rounded to 6 decimals to improve hit rate for nearby points? Requirement: implement cache that stores recent lookup results.
  Spec: cache key is lat,lng pair rounded to 6 decimal places (or exact float). Value is sorted list of matching geofence IDs.
- Max entries = cache-size flag. When exceeding, evict least recently used.
- Thread-safe: use mutex.
- Stats: cache hits counter.
- Cache should be consulted before index search: on lookup, compute key, check cache under lock, if hit return cached result and increment cache_hits. If miss, do indexed search, then store result in cache.

- Cache invalidation: not needed for this task (geofences static after startup).

#### 4. Concurrency Safety
HTTP server handles concurrent requests via Go net/http (each request in separate goroutine). Shared data:

- Geofences slice/map, index, cache must be protected by sync.RWMutex for reads. Writes only at startup, so RWMutex read lock suffices for queries, but cache needs write lock on update. Use separate mutexes or combined.
- Must not have data race (tested with concurrent clients). Use sync.RWMutex and cache's own mutex.

## HTTP API

### `GET /lookup?lat=<float>&lng=<float>`
- Query params: lat, lng required. Must be valid float within range else 400 with JSON `{"error":"..."}`
- Returns 200 JSON:
```json
{
  "lat": 0.5,
  "lng": 0.5,
  "geofences": ["zone_a", "zone_b"],
  "count": 2
}
```
Fields: lat, lng echoed as float (original parsed), geofences sorted ascending, count len.
- Must use spatial index + bbox + cache internally (stats will show usage). Result must match naive point-in-polygon (correctness).
- Increment total_queries counter by 1 (atomic or under mutex).
- If cache hit, increment cache_hits, return quickly.

### `POST /lookup/batch`
- Body JSON:
```json
{
  "points": [
    {"lat":0.5,"lng":0.5},
    {"lat":2,"lng":2}
  ]
}
```
- Validation: body must be JSON object with "points" array. Each point must have lat,lng valid numbers in range. Max batch size 1000 points, if more -> 400 `{"error":"batch too large"}`. Empty array allowed -> returns empty results.
- Invalid JSON or missing points field or invalid point -> 400 JSON error.
- Returns 200 JSON:
```json
{
  "results": [
    {"lat":0.5,"lng":0.5,"geofences":["zone_a"]},
    {"lat":2,"lng":2,"geofences":[]}
  ]
}
```
Results order must match input order. Each result has lat,lng echoed, geofences sorted asc.
- Performance: implement efficiently. Should reuse indexed lookup per point. May process concurrently using worker pool or goroutines but preserve order. Simplest sequential loop still may pass performance if indexed. To achieve required batch performance (100 points <500ms), indexed sequential is okay. But ensure no per-request file IO.
- total_queries should increment by len(points), cache_hits increment per cache hit within batch.

### `GET /geofences`
- Returns 200 JSON array of all geofence objects sorted by ID ascending. Same shape as `list` command but via HTTP.
- Empty -> `[]`.

### `GET /stats`
- Returns 200 JSON:
```json
{
  "total_geofences": 5,
  "total_queries": 1234,
  "cache_hits": 100,
  "cache_size": 2,
  "avg_latency_ms": 0.5,
  "index_cells": 10
}
```
Fields:
- total_geofences: int, number loaded from DB
- total_queries: int, total point lookups since startup (including batch points)
- cache_hits: int, how many queries served from cache
- cache_size: int, current entries in cache
- avg_latency_ms: float, average lookup latency in ms (rolling average since startup). Update on each query: track total latency.
- index_cells: int, number of grid cells that have >=1 geofence. Must be >0 if geofences exist and grid index implemented.

### Other endpoints
- `GET /` or unknown -> 404 JSON `{"error":"not found"}`

### Error Handling HTTP
- 400 for invalid lat/lng, invalid batch JSON, batch too large, missing params
- 404 for unknown paths
- All error responses JSON with `{"error":"message"}`

### Performance Targets (Enforced by Tests)
Tests will spin up your server with 100+ geofences (mix of squares & complex polygons) and measure:

1. **Correctness under concurrency**: 20 concurrent clients * 50 requests = 1000 requests, all must succeed 200 and results must match naive scan (same as CLI lookup logic). No data race crash.

2. **QPS throughput**: Send 1000 requests via 20 concurrent threads, must complete within 5 seconds (implies >=200 QPS). On faster hardware may be higher, but threshold 200 QPS ensures indexed implementation though even naive Go might pass? With 500 geofences, naive would be slower. To be safe, we test with 300 geofences of 10 points each; naive would be ~300*10 edge checks per query *1000 = 3M checks, still might pass within 5s in Go (~0.5s per 1000 naive?). We will also test large scale: 500 geofences and require p50 latency <50ms and p99 <100ms under concurrent load. This forces index to help empty areas skip quickly.

3. **Batch performance**: POST batch of 100 points, must respond within 500ms (indexed). Naive loop of 100 points * 300 geofences * 10 edges = 300k checks, still maybe okay, but we require <500ms.

4. **Cache behavior**: Repeated same point 10 times, cache_hits should increase after first, and second batch faster or at least hits counted. After 10 identical queries, cache_hits >=9 (first miss, rest hits) if cache-size >=1.

5. **Index stats**: /stats must report total_geofences == loaded count, index_cells >0 when geofences present, cache_size <= cache-size flag.

6. **Grid-size effect**: Server with grid-size 1.0 and 5.0 both must work, return same results, but index_cells may differ.

If performance tests fail, grading fails.

### Backward Compatibility
- All Step 1 commands (add, remove, list, lookup, clear) must still work via CLI after Step 2 changes.
- DB format must remain same JSON object map, to allow existing DB from Step 1 to be loaded by Step 2 serve.

### Constraints
- Stdlib only, no external deps. `go.mod` must stay stdlib-only.
- Must build via `go build -o geofencectl .` from /app/src.
- Must handle parent dir creation for DB file (as before).
- Must be thread-safe: use sync.RWMutex, sync.Mutex, atomic counters.
- No per-request file read: load DB once at serve startup.
- LRU cache implementation must be your own, not external lib.

### Example Flow After Step 2
```bash
# Step1 commands still work
geofencectl --db /tmp/g.json add zone_a --polygon "0,0;0,1;1,1;1,0" --name "A"
geofencectl --db /tmp/g.json list

# Start high-QPS server
geofencectl --db /tmp/g.json serve --port 8080 --grid-size 1.0 --cache-size 1000 &
# Server prints serving on :8080

curl "http://localhost:8080/lookup?lat=0.5&lng=0.5"
# -> {"lat":0.5,"lng":0.5,"geofences":["zone_a"],"count":1}

curl -X POST http://localhost:8080/lookup/batch -H "Content-Type: application/json" -d '{"points":[{"lat":0.5,"lng":0.5},{"lat":2,"lng":2}]}'
# -> {"results":[...]}

curl http://localhost:8080/geofences
# -> [{"id":"zone_a",...}]

curl http://localhost:8080/stats
# -> {"total_geofences":1,"total_queries":3,"cache_hits":1,...}

# Stop server
kill %1
```

### Deliverable
Extend `/app/src/` to support new serve command and HTTP API with spatial index, bbox prefilter, LRU cache, batch, stats, concurrency safety. Must still pass Step1 tests if they were rerun (backward compat).

Tests will verify:
- CLI backward compat
- HTTP endpoint correctness vs naive
- Concurrency no race, no crash
- QPS throughput >=200 QPS for 1000 requests (20 conc), p50<50ms p99<100ms
- Batch latency <500ms for 100 points
- Cache hits counted, index_cells >0
- Stats fields present and monotonic
- Large geofence set (500) still fast lookup (<100ms p99)
``` 
