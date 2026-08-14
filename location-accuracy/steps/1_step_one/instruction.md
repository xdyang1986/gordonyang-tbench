# Step 1: Vehicle Location Tracking Service

## Scenario

You are building the core location service for a ride-sharing platform under `/app`. This service tracks real-time vehicle locations for dispatch, ETA calculation, and rider display. It must be crash-consistent and persistent, handle out-of-order GPS pings, support complex geofences, perform road-snapping, and provide batch atomic operations and analytics.

Build a binary named `locationctl` with `go build -o locationctl .` from `/app/src`, module `locationservice`. You may use only Go standard library.

## CLI Format

```
locationctl --db <PATH> <command> [args] [flags]
```

- `--db` is the path to the JSON database file. If the file does not exist, start with an empty store. You must create parent directories if needed. Writes must be atomic: write to a temporary file named `<db>.tmp.<pid>` in the same directory, then rename. Use best-effort fsync. On success, no temporary files should remain.
- If no command is given, or the first argument is `help`, `--help`, or `-h`, print help text that contains the strings `update,get,list,near,track,distance,delete,stats,batch,clear,geofence-check` and exit 0. An unknown command must exit 2.

## Data Model

Each location record looks like:

```json
{
  "vehicle_id": "veh_123",
  "lat": 37.7749, "lng": -122.4194,
  "timestamp_ms": 1710000000000,
  "accuracy": 5.0, "speed": 12.5, "heading": 90.0,
  "total_distance_m": 1235.6,
  "history": [{"lat":37.7748,"lng":-122.4193,"timestamp_ms":1710000000000,"accuracy":5,"speed":10,"heading":90}, ...]
}
```

Validation rules:

- `vehicle_id` must match `^[A-Za-z0-9_-]{1,64}$`. Empty, spaces, or longer than 64 characters are invalid.
- `lat` must be in [-90, 90], `lng` in [-180, 180]. You must reject NaN, Inf, Infinity (case-insensitive).
- `timestamp_ms` must be an int64 >= 0 and must be provided as an integer string. Reject floats like `1000.0`, scientific notation like `1e3`, or hex like `0x3e8`.
- `accuracy` must be >= 0, default 10. NaN/Inf is invalid.
- `speed` must be in [0, 50] m/s, default 0. Values above 50 are invalid and must exit 2. Negative is invalid.
- `heading` must be in [0, 360), default 0. 360 is exclusive and invalid.
- `total_distance_m` is the sum of Haversine distances (R=6371000m) between successive accepted updates for a vehicle. It starts at 0 and is persisted.
- `history` stores up to 10 last accepted locations, sorted by timestamp ascending. The oldest entry comes first, the newest last, and it must include the current location as the last entry.

Database file format: JSON object mapping vehicle_id to Location. A 0-byte file or whitespace-only file should be treated as an empty store. A non-empty file that is unparsable, or whose JSON is not an object (for example an array `[]` or literal `null`), must be treated as corrupt: exit 4, and create a backup file named `<db>.corrupt.<nanosec>` where `<nanosec>` is an integer nanosecond timestamp. The backup must contain the original corrupt content.

Stale and out-of-order handling: per vehicle, keep only the latest timestamp. If an incoming timestamp is less than or equal to the stored timestamp, ignore it: print `stale` to stdout, exit 0, and do not modify the database. Stale updates must not affect total_distance or history.

Crash consistency:
- Pre-existing stale files like `<db>.tmp.<pid>` must be ignored when loading the DB and must be cleaned up on the next successful write.
- A truncated file (for example a valid JSON prefix cut mid-object) must take the corruption path: exit 4 and create a `.corrupt.<nanosec>` backup.

## Zones – Polygons with Holes, Circles, Time Windows, Antimeridian

Zones are stored in a JSON file as an array. Each zone is either polygon-based OR circle-based:

```json
{"id":"zone_a","polygon":[{"lat":...,"lng":...},...],"holes":[[{...},...]],"active_from":1000,"active_to":2000}
{"id":"circle_1","center":{"lat":...,"lng":...},"radius_m":500}
```

Rules:
- `id` must be non-empty.
- Polygon must have at least 3 valid points. Holes are optional, but each hole must have at least 3 valid points.
- Circle center must be a valid lat/lng, radius must satisfy 0 < radius <= 1e6.
- Specifying both polygon and circle in the same zone is invalid and must cause exit 2 when that zones file is used.
- `active_from` and `active_to` are optional and must be >=0. If both are present, `from` must be <= `to`, otherwise the file is invalid (exit 2).
- Point-in-polygon must use even-odd rule with x=lng and y=lat. You must handle antimeridian crossing by unwrapping longitudes to a continuous range, so a rectangle from 179 to -179 is 2 degrees wide, not 358 degrees. A point on an edge or vertex is considered inside. Circle check uses Haversine distance: inside if distance <= radius (exact radius counts as inside). Holes mean a point inside the outer polygon but inside a hole is considered outside.

Zone activation and filtering (this is the main seam with evidence):

- A zone is active at timestamp `ts` if `(active_from absent OR ts >= active_from) AND (active_to absent OR ts <= active_to)`. Bounds are inclusive: `ts == active_from` and `ts == active_to` are both active. A zone with only `active_from` is active from that time onward; a zone with only `active_to` is active up to that time.

- For `update`: if `--zones <path>` is provided, use it. Otherwise, if the default file `/app/data/zones.json` exists, use it. Filter active zones by the **update's own timestamp**, then if active list non-empty, location must be inside at least one active zone else `out_of_zone` exit 3.

- For `list`, `near`, and `geofence-check`: `--zones` optional. Active zones are filtered by `--now` if provided, otherwise all zones are considered (no time filtering). This creates intentional divergence: `update` uses its own timestamp, while `list`/`near`/`geofence-check` use `--now`. Example: vehicle updated at ts=500 when zone active_from=1000 (so no active zones at update time, update succeeds even outside zone), then `list --zones <file> --now 1500` should return [] if vehicle is outside, because at now=1500 the zone is active and filters. Conversely, vehicle updated at ts=1500 inside zone, then `list --now 500` should include it because no active zones at now=500.

- For `geofence-check`: same --now filtering as list/near, inclusive bounds.

Zone filtering summary:
- `update` → filter by update timestamp
- `list`/`near`/`geofence-check` → filter by `--now` if given, else no time filter

## Roads and Snapping

Roads file has mixed formats:

```json
[{"id":"road_1","points":[{"lat":...,"lng":...},...]},
 {"id":"seg","start":{"lat":...,"lng":...},"end":{"lat":...,"lng":...}}]
```

Rules:
- `id` must be non-empty. Polyline `points` must have at least 2 valid points, or legacy `start/end` must both be valid. Any invalid entry must cause exit 2 when that roads file is used.
- Snapping uses equirectangular projection with R=6371000 and `lat_ref` set to the query latitude. For each segment in each road, find the closest point clamped with t in [0,1]. Keep the best overall. If distance <=50m, the point is considered snapped and on-road. You must check all segments of a polyline and interior points, not just endpoints.

For `near --roads` and `list --roads`, only vehicles snapped within 50m should be included.

## Commands

### 1. update <vehicle_id> <lat> <lng> <timestamp_ms> [--accuracy <f> --speed <f> --heading <f> --zones <path>]

Validate all fields per Data Model, including NaN/Inf checks. Perform zones check (out_of_zone exit 3) before stale check. On stale, print `stale` and exit 0. Otherwise compute Haversine distance to prior accepted location, add to total_distance, maintain history 10 sorted ascending, persist atomically, and print JSON of stored location without history but including total_distance_m.

### 2. get <vehicle_id> [--verbose]

Base mode returns location without history but with total_distance_m. With `--verbose`, return full record including history. Not found must exit 3, invalid id must exit 2.

### 3. list [--since <ts> --until <ts> --limit <n> --offset <m> --zones <path> --roads <path> --now <ts>]

Results are sorted by vehicle_id ascending. `since` and `until` are inclusive, and must satisfy since <= until otherwise exit 2. Zones filter uses active zones filtered by now if provided, otherwise all zones. Roads filter uses snapping. Pagination is offset then limit: first apply offset, then limit. Limit 0 means empty array [], offset beyond length means []. If a zones file is non-empty but no zones are active at the given now, the result should be [].

### 4. near --lat <f> --lng <f> --radius <f> [--accuracy-max <f> --speed-min <f> --limit <n> --offset <m> --now <ts> --include-stale --zones <path> --roads <path>]

Validate lat/lng, radius in [0, 50000] else exit 2. `accuracy-max` must be >=0, `speed-min` in [0,50]. For staleness: `age = now - timestamp_ms`, if negative set to 0. If now is provided, vehicles with age > 30000 are stale and excluded, unless `--include-stale` is given. Zones active filtered by now if provided. Roads filter via snapping. Distance uses Haversine and must satisfy <= radius. Sort by distance ascending, then vehicle_id ascending. Pagination offset then limit. Each result includes base location plus `distance_m` (Haversine from query point).

### 5. track <vehicle_id> --from <ts> --to <ts> [--limit <n> --offset <m>]

Return history entries within [from,to] inclusive, sorted ascending and paginated. Both flags are required, from <= to else exit 2. Not found must exit 3.

### 6. distance <vehicle_id>

Returns `{"vehicle_id":...,"total_distance_m":...}` with exit 0, not found exit 3.

### 7. delete <vehicle_id>

Prints `deleted` to stdout and exits 0, even if vehicle does not exist. Invalid id must exit 2.

### 8. stats

Returns `{"live":#vehicles, "total_updates": total accepted count, "total_distance_m": sum, "avg_accuracy": avg}`.

### 9. batch

Reads stdin as tab-delimited lines. Empty or whitespace-only lines are ignored.

Line formats:
- `update\tveh_id\tlat\tlng\ttimestamp[\taccuracy[\tspeed[\theading]]]` with variable fields 5-8. Five fields is minimal with defaults 10,0,0. 6-8 fields may have empty string meaning default. More than 8 or fewer than 5 fields must fail with exit 2.
- `delete\tveh_id` exactly 2 fields.

All-or-nothing atomicity: parse all lines first and validate all. Zones check uses default zones.json if exists per operation timestamp, and must be performed before stale check. If any operation would be out_of_zone, fail with exit 2 and leave DB unchanged, even if that operation is stale. Stale operations are skipped, not failed. After validation, apply sequentially to a simulated state, perform a single atomic write, and print `batch_ok <applied>` where applied excludes stale entries.

### 10. clear

Prints `cleared` and exits 0.

### 11. geofence-check <lat> <lng> [--zones <path>] [--now <ts>]

Check if point is inside any active zone (polygons with holes, circles, antimeridian, edge-inside). `--zones` optional, otherwise default `/app/data/zones.json` if exists, else outside. `--now` optionally filters active zones. Output `{"inside":bool,"zone_id":string}` where zone_id is first matching by file order. Invalid args or zones must exit 2.

## Exit Codes

- 0: success, or stale, deleted, cleared, batch_ok.
- 2: invalid argument, malformed value, invalid zones/roads file, batch validation failure.
- 3: not found, out_of_zone.
- 4: corrupt DB (with `.corrupt.<nanosec>` backup creation).

## Constraints

- Only Go standard library.
- Atomic writes via temp file and rename, no leftover tmp files, clean stale tmp files.
- Parent directories must be created automatically.
- ID regex, zones with holes/circles/time/antimeridian/edge-inside, roads mixed, pagination offset then limit, batch variable fields, NaN/Inf rejection, history 10 ascending with current as last, total_distance via Haversine.
- Large scale: 800+ vehicles near query under 3 seconds.
- Persistence must survive process restart, including corrupt backup handling with integer nanosec suffix.

Delivery under `/app/src/`.
