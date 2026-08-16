# Step 1: Vehicle Location Tracking Service

## Scenario

You are building the core location service for a ride-sharing platform under `/app`. This service tracks real-time vehicle locations for dispatch, ETA, and rider display. It must be persistent, handle out-of-order GPS pings, support geofences, perform road-snapping, and provide batch operations.

Build a binary named `locationctl` with `go build -o locationctl .` from `/app/src`, module `locationservice`. You may use only Go standard library.

## CLI Format

```
locationctl --db <PATH> <command> [args] [flags]
```

- `--db` is the path to the JSON database file. If the file does not exist, start with an empty store. You must create parent directories if needed. Writes must be atomic: write to a temporary file named `<db>.tmp.<pid>` in the same directory, then rename. On success, no temporary files should remain.
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

- `vehicle_id` must match `^[A-Za-z0-9_-]{1,64}$`. Empty, spaces, or longer than 64 are invalid.
- `lat` must be in [-90, 90], `lng` in [-180, 180]. Reject NaN, Inf, Infinity (case-insensitive).
- `timestamp_ms` must be int64 >=0 and provided as integer string. Reject floats like `1000.0`, scientific notation `1e3`, hex `0x3e8`.
- `accuracy` >=0, default 10. NaN/Inf invalid.
- `speed` in [0, 50] m/s, default 0. Above 50 invalid exit 2. Negative invalid.
- `heading` in [0, 360), default 0. 360 exclusive invalid.
- `total_distance_m` is sum of Haversine distances (R=6371000m) between successive accepted updates. Starts at 0 and persisted.
- `history` stores up to 10 last accepted locations, sorted by timestamp ascending. Includes current as last entry.

Database file: JSON object mapping vehicle_id to Location. 0-byte or whitespace-only file is empty store. Unparsable JSON, JSON array `[]`, literal `null`, or truncated file is corrupt: exit 4 (backup file `.corrupt.<nanosec>` creation is optional).

Stale handling: per vehicle, keep latest timestamp. If incoming timestamp <= stored timestamp, ignore: print `stale` to stdout, exit 0, do not modify DB. Stale must not affect total_distance or history.

Crash consistency: ignore pre-existing `%s.tmp.*` leftover files on load, and clean them up on next successful write.

## Zones – Polygons with Holes, Circles, Time Windows

Zones file is JSON array:

```json
{"id":"zone_a","polygon":[{"lat":...,"lng":...},...],"holes":[[{...},...]],"active_from":1000,"active_to":2000}
{"id":"circle_1","center":{"lat":...,"lng":...},"radius_m":500}
```

Rules:
- `id` non-empty.
- Polygon at least 3 valid points. Holes optional, each at least 3 valid points.
- Circle center valid lat/lng, radius 0 < radius <=1e6.
- Both polygon and circle in same zone is invalid → exit 2 when used.
- `active_from`/`active_to` optional >=0. If both present, from <= to else invalid file exit 2.
- Point-in-polygon uses even-odd rule with x=lng y=lat. Point on edge or vertex counts as inside. Circle uses Haversine distance: inside if distance <= radius. Hole inside means outside.

Zone activation:
- Active at `ts` if `(active_from absent OR ts >= active_from) AND (active_to absent OR ts <= active_to)`. Bounds inclusive.
- Only-from: active from that time onward. Only-to: active up to that time.

Filtering:
- `update`: if `--zones <path>` provided use it, else if `/app/data/zones.json` exists use it. Filter active zones by update's own timestamp. If active list non-empty, location must be inside at least one else `out_of_zone` exit 3. If no active zones at that timestamp, allow all (no filtering).
- `list`, `near`, `geofence-check`: `--zones` optional. If `--now <ts>` provided, filter active zones by `now`. Otherwise consider all zones (no time filtering). If no active zones at given `now`, treat as no filtering (include all) – this is the simplified intuitive behavior.
  Example divergence still kept but simpler: update at ts=500 when zone active_from=1000 (no active zones, so update allowed even outside), later `list --now 1500` will filter because zone active at 1500 and outside vehicle excluded. Conversely update at 1500 inside zone succeeds, `list --now 500` includes it because no active zones at 500.

## Roads and Snapping

Roads file is JSON array of polylines:

```json
[{"id":"road_1","points":[{"lat":...,"lng":...},...]}]
```

Rules:
- `id` non-empty. `points` at least 2 valid points. Invalid entry → exit 2 when file used.
- Snapping uses equirectangular projection with R=6371000 and `lat_ref` = query lat. For each segment in each road, find closest point clamped t in [0,1]. Keep best overall. If distance <=50m, snapped.
- Must check all segments, not just endpoints.

For `near --roads` and `list --roads`, only snapped vehicles within 50m included.

## Commands

### 1. update <vehicle_id> <lat> <lng> <timestamp_ms> [--accuracy <f> --speed <f> --heading <f> --zones <path>]

Validate all fields. Perform zones check (out_of_zone exit 3) before stale check. On stale print `stale` exit 0. Otherwise compute Haversine distance to prior accepted, add to total_distance, maintain history 10 sorted asc, persist atomically, print JSON without history but with total_distance_m.

### 2. get <vehicle_id> [--verbose]

Base returns without history but with total_distance_m. With `--verbose` full including history. Not found exit 3, invalid id exit 2.

### 3. list [--since <ts> --until <ts> --limit <n> --offset <m> --zones <path> --roads <path> --now <ts>]

Sorted by vehicle_id asc. `since`/`until` inclusive, must satisfy since <= until else exit 2. Zones filtered by now if provided else all. Roads via snapping. Pagination offset then limit: apply offset first, then limit. Limit 0 => [], offset>len => []. If no active zones at given now, no zone filtering (include all).

### 4. near --lat <f> --lng <f> --radius <f> [--accuracy-max <f> --speed-min <f> --limit <n> --offset <m> --now <ts> --include-stale --zones <path> --roads <path>]

Validate lat/lng, radius [0,50000] else exit 2. `accuracy-max` >=0, `speed-min` [0,50]. For staleness: `age = now - timestamp_ms`, negative set 0. If now provided, age>30000 stale excluded unless `--include-stale`. Zones filtered by now if provided. Roads via snapping. Distance Haversine <= radius. Sort distance asc then vehicle_id asc. Pagination offset then limit. Each result includes base location plus `distance_m`.

### 5. track <vehicle_id> --from <ts> --to <ts> [--limit <n> --offset <m>]

Return history entries within [from,to] inclusive, sorted asc and paginated. Both flags required, from <= to else exit 2. Not found exit 3.

### 6. distance <vehicle_id>

Returns `{"vehicle_id":...,"total_distance_m":...}` exit 0, not found exit 3.

### 7. delete <vehicle_id>

Prints `deleted` stdout exit 0 even if not found. Invalid id exit 2.

### 8. stats

Returns `{"live":#vehicles, "total_updates": total accepted count, "total_distance_m": sum, "avg_accuracy": avg}`.

### 9. batch

Reads stdin tab-delimited. Empty/whitespace lines ignored.

Line formats:
- `update\tveh_id\tlat\tlng\ttimestamp[\taccuracy[\tspeed[\theading]]]` variable fields 5-8. Five fields minimal with defaults 10,0,0. More than 8 or fewer than 5 fail exit 2.
- `delete\tveh_id` exactly 2 fields.

All-or-nothing atomic: parse all lines first and validate. Zones check uses default zones.json if exists per op timestamp, must be before stale check. If any out_of_zone fail exit 2 DB unchanged even if stale. Stale ops skipped not failed. After validation apply sequentially to simulated state, single atomic write, print `batch_ok <applied>` where applied excludes stale.

### 10. clear

Prints `cleared` exit 0.

### 11. geofence-check <lat> <lng> [--zones <path>] [--now <ts>]

Check if point inside any active zone (polygons with holes, circles, edge-inside). `--zones` optional else default `/app/data/zones.json` if exists else outside. `--now` filters active. Output `{"inside":bool,"zone_id":string}` first matching by file order. Invalid args/zones exit 2.

## Exit Codes

- 0: success, or stale, deleted, cleared, batch_ok.
- 2: invalid argument, malformed, invalid zones/roads, batch validation fail.
- 3: not found, out_of_zone.
- 4: corrupt DB.

## Constraints

- Only Go stdlib.
- Atomic writes via temp file and rename, no leftover tmp, clean stale tmp.
- Parent dirs auto-created.
- ID regex, zones polygons with holes/circles/time inclusive, edge-inside, roads polyline only, pagination offset then limit, batch 5-8 fields, NaN/Inf rejection, history 10 asc with current last, total_distance Haversine.
- Large scale: 800+ vehicles near query under 3 seconds.
- Persistence must survive restart.

Delivery under `/app/src/`.
