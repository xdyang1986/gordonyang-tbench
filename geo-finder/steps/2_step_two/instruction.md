# Step 2: High-QPS Geofence Service with Dynamic CRUD (Hard)

## Context
You completed Step 1: a CLI geofence manager with persistent JSON DB, strict polygon validation (no duplicate, non-zero area, no self-intersection), and correct point-in-polygon lookup with edge handling.

In production, location pings arrive at high QPS and operators need to manage geofences via HTTP as well. You must evolve the same CLI to add an HTTP server that serves lookups at high QPS with spatial indexing, bounding-box prefilter, caching, batch support, dynamic CRUD via HTTP, and concurrency safety.

Preserve Step 1 compatibility — all Step 1 commands must still work, including new strict validations.

## New Command: `serve`

```
geofencectl --db <PATH> serve --port <int> [--grid-size <float>] [--cache-size <int>]
```

- `--db` same global flag, path to JSON DB file. Load all geofences into memory at startup. If missing/empty -> 0 geofences. If corrupt -> stderr + exit 4.
- `--port` required int 1-65535, bind 0.0.0.0:<port>. Invalid -> exit 2.
- `--grid-size` optional float >0 <=45, default 1.0.
- `--cache-size` optional int >=0, default 1000, 0 = no cache. Invalid -> exit 2.
- On success, start HTTP server, block until SIGTERM/SIGINT. Print to stdout `serving on :<port>`.

### Required In-Memory Data Structures

#### 1. Bounding Boxes
Precompute bounding box per geofence for quick reject. Must correctly handle polygons near the poles, polygons that cross the antimeridian, and polygons that span all longitudes.
Longitude classification: use the same classification rule defined in Step 1 for both bounding-box computation and point-in-polygon evaluation. Refer to Step 1 spec for the exact definition of world-spanning vs antimeridian-crossing vs ordinary polygons (based on raw longitude span before normalisation), including the requirement to classify world-spanning before checking crossing, and the semantics for each class (full longitude coverage, unwrapped continuous interval, ordinary). The same answers must come from the CLI lookup command and from GET /lookup, and bbox handling must match that classification.

#### 2. Grid Spatial Index
Uniform grid over world lat [-90,90], lng [-180,180]. You must implement spatial indexing to reduce candidates. On query, compute cell for point and get candidates from index (empty cell -> empty result). Then bbox check, then point-in-polygon. Must track `index_cells` = number of cells with >=1 geofence.

- Implementation must handle large bounding boxes without OOM and remain fast for empty-area lookups.
- Index must be updatable on HTTP POST/DELETE (see CRUD).

#### 3. Cache
Implement a cache for point lookups:

- Cache keys are derived from the query point rounded to 6 decimal places.
- Must have bounded size = cache-size flag, evict least recently used when exceeding.
- Thread-safe and consulted before index search.
- On HTTP POST/DELETE that mutates geofences, only cache entries whose query point lies within the affected geofence's bounding box may be invalidated. Entries for points outside that bounding box must survive, remain hits, and continue to count toward `cache_hits` and `cache_size`. For an overwrite via POST, invalidate entries within both the old and new bounding boxes. After a DELETE, a point that previously matched inside the deleted zone's bounding box must no longer match; points outside must stay cached.

#### 4. Concurrency Safety
- HTTP server handles concurrent requests.
- Shared mutable state — db, bboxes, grid, and the DB file on disk — must be protected for concurrent reads and writes. Cache has its own synchronization.
- Every POST/DELETE that returns success must be durable: concurrent mutations must not lose, corrupt, or fail each other's writes, and after the server exits the DB file must contain every acknowledged mutation.
- Must not have data race.

## HTTP API (All JSON)

- All JSON array fields must serialize as `[]` when empty, on every response path. This includes `geofences` in `GET /lookup`, each `geofences` in `POST /lookup/batch` results, `results` itself, and `GET /geofences` list.

### `GET /lookup?lat=<float>&lng=<float>[&verbose=true]`
- Query lat,lng required, valid range else 400 JSON error.
- Optional `verbose` bool: if true (accept `true`, `1`, `t`), return full Geofence objects instead of IDs.
- Returns 200 JSON with lat, lng echoed, geofences sorted asc (`[]` not `null` when empty), count.
- Must use spatial index + bbox + cache internally. Result must match naive point-in-polygon.
- Increment total_queries, cache_hits on hit.

### `POST /lookup/batch`
- Body JSON `{"points":[{"lat":...,"lng":...},...]}`
- Max 1000 points else 400. Each point must have valid lat/lng else 400. Empty array allowed.
- Returns 200 `{"results":[{"lat":...,"lng":...,"geofences":[...]},...]}` preserving input order, each geofences sorted asc. Empty `geofences` must be `[]` not `null`; empty `results` must be `[]` not `null`.
- total_queries += len(points), cache_hits per hit. Must be efficient, no per-request file IO.

### `GET /geofences`
- Returns 200 JSON array of all geofence objects sorted by ID asc. Empty list must be `[]` not `null`.

### `GET /geofences/:id`
- Returns 200 single Geofence if found, else 404 JSON error.

### `POST /geofences`
- Body JSON Geofence object `{"id":"...","name":"...","polygon":[{"lat":...,"lng":...},...]}`
- Validation identical to CLI `add`: ID regex, name, polygon at least 3 <=1000, lat/lng range, no duplicate points, non-zero area, no self-intersection. If invalid → 400.
- On success, add/overwrite in DB and in-memory, update bboxes and grid (including antimeridian handling), invalidate cache, atomically persist DB file, return 201 with stored object.

### `DELETE /geofences/:id`
- Delete by ID. If not found → 404. Else remove from DB, bboxes, grid, invalidate cache, atomic persist, return 200 `{"deleted":"<id>"}`.

### `GET /stats`
- Returns 200 JSON with at least:
  - total_geofences, total_queries, cache_hits, cache_size, avg_latency_ms, index_cells, cache_hit_rate (hits/queries)
- Must reflect dynamic mutations and be monotonic for total_queries.

### Other
- Unknown path → 404 JSON error.
- All error responses JSON `{"error":"message"}`.

### Performance & Correctness (No absolute floors)

Instead of absolute QPS/p50/p99 walls that depend on host, tests use relative checks proving an index exists and cache works:

- **Relative latency**: Measure avg lookup latency with few geofences vs many geofences for same point (inside and empty area). With a spatial index, latency should not grow linearly with number of zones.
- **Cold vs cached**: First query for a point is miss, second identical is hit and should be counted as hit; cache_size bounded.
- **Correctness under concurrency**: Concurrent clients must all succeed and match naive results. All response paths must return `[]` not `null` when empty.
- **Grid bookkeeping**: `index_cells` must accurately track cells that contain geofences and must reclaim empty cells after DELETE, so stale empty entries are not left behind. Stats must reflect dynamic mutations.

If relative checks fail, grading fails. Generous absolute upper bounds prevent hangs but do not cause flake.

### Backward Compatibility
- All Step 1 commands still work.

### Constraints
- Stdlib only, must build via `go build -o geofencectl .`
- Must be thread-safe.
- No per-request file read, except atomic write on POST/DELETE.
- LRU cache must be own implementation.
- All empty JSON array fields must serialize as `[]` when empty, on every response path.

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
