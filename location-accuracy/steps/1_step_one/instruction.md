# Step 1: Vehicle Location Tracking Service (Simplified)

## Scenario

You are building the core location service for a ride-sharing platform under `/app`. It tracks real-time vehicle locations for dispatch and ETA. It must be persistent, handle out-of-order GPS pings, support basic geofences, perform road-snapping, and provide batch operations.

Build a binary named `locationctl` with `go build -o locationctl .` from `/app/src`, module `locationservice`. Use only Go standard library.

## CLI Format

```
locationctl --db <PATH> <command> [args] [flags]
```

- `--db` path to JSON DB file. If not exists, start empty. Must create parent directories. Writes atomic: write to `<db>.tmp.<pid>` then rename. No tmp leftover on success.
- If no command or first arg is `help`, `--help`, `-h`, print help containing strings `update,get,list,near,track,distance,delete,stats,batch,clear,geofence-check` exit 0. Unknown command exit 2.

## Data Model

```json
{
  "vehicle_id": "veh_123",
  "lat": 37.7749, "lng": -122.4194,
  "timestamp_ms": 1710000000000,
  "accuracy": 5.0, "speed": 12.5, "heading": 90.0,
  "total_distance_m": 1235.6,
  "history": [{"lat":37.7748,"lng":-122.4193,"timestamp_ms":1710000000000,"accuracy":5,"speed":10,"heading":90}]
}
```

Validation:
- `vehicle_id` regex `^[A-Za-z0-9_-]{1,64}$`, else invalid exit 2.
- `lat` [-90,90], `lng` [-180,180]. Reject NaN, Inf, Infinity case-insensitive.
- `timestamp_ms` int64 >=0 integer string only. Reject `1000.0`, `1e3`, `0x3e8`.
- `accuracy` >=0 default 10 NaN/Inf invalid.
- `speed` [0,50] default 0 >50 invalid exit 2, negative invalid.
- `heading` [0,360) default 0, 360 exclusive invalid.
- `total_distance_m` Haversine sum R=6371000 over accepted updates only, starts 0 persisted.
- `history` up to 10 last accepted sorted asc timestamp, includes current as last.

DB file: JSON object mapping vehicle_id to Location. 0-byte or whitespace-only = empty store. Unparsable, array `[]`, literal `null`, truncated = corrupt exit 4 (backup optional).
Stale: per vehicle keep latest timestamp. If incoming <= stored, print `stale` stdout exit 0, no DB change, no distance/history change.
Crash: ignore existing `<db>.tmp.*` on load, clean on next successful write.

## Zones – Simple Polygons Only (Simplified)

Zones file JSON array:

```json
{"id":"zone_a","polygon":[{"lat":...,"lng":...},...]}
```

Rules (simplified):
- `id` non-empty.
- Polygon at least 3 valid points (lat/lng validation same as vehicle).
- No holes, no circles, no time windows for Step 1 – keep it simple. If file contains `holes`, `center`, `radius_m`, `active_from`, `active_to`, treat as invalid and exit 2 when that file is used (to keep parser strict but spec simple).
- Point-in-polygon even-odd rule x=lng y=lat. Point on edge or vertex counts as inside.

Filtering (simplified, no --now):
- `update`: if `--zones <path>` given use it, else if `/app/data/zones.json` exists use it. If zones non-empty, location must be inside at least one zone else `out_of_zone` exit 3.
- `list`, `near`, `geofence-check`: `--zones` optional, if provided filter to inside at least one zone. No time filtering (`--now` flag should be accepted but ignored for backward compat, or treated as invalid? For simplicity Step1: if --now provided, ignore it – no time filtering). This removes the hard divergence.
- If zones file empty array, allow all.

## Roads and Snapping (Simplified)

Roads file JSON array of polylines:

```json
[{"id":"road_1","points":[{"lat":...,"lng":...},...]}]
```

Rules:
- `id` non-empty, `points` at least 2 valid points. Invalid → exit 2 when used.
- Snapping: equirectangular projection R=6371000 `lat_ref` = query lat. For each segment, closest point clamped t in [0,1], best overall. If distance <=50m snapped. Check all segments, not just endpoints.

`near --roads` and `list --roads` only include snapped vehicles.

## Commands

### 1. update <vehicle_id> <lat> <lng> <timestamp_ms> [--accuracy <f> --speed <f> --heading <f> --zones <path>]
Validate, zones check out_of_zone exit 3 before stale. On stale print stale exit 0. Otherwise distance Haversine to prior, add total_distance, history 10 sorted asc, persist atomically, print JSON without history but with total_distance_m.

### 2. get <vehicle_id> [--verbose]
Base without history but with total_distance_m. --verbose full with history. Not found exit 3, invalid id exit 2.

### 3. list [--since <ts> --until <ts> --limit <n> --offset <m> --zones <path> --roads <path>]
Sorted vehicle_id asc. since/until inclusive, since <= until else exit 2. Zones filter simple inside. Roads snapping. Pagination offset then limit: offset first then limit. Limit 0 => [], offset>len => [].

### 4. near --lat <f> --lng <f> --radius <f> [--accuracy-max <f> --speed-min <f> --limit <n> --offset <m> --include-stale --zones <path> --roads <path>]
Validate lat/lng, radius [0,50000] else exit 2. accuracy-max >=0, speed-min [0,50]. No --now needed for Step1; if --now provided ignore it for simplicity (do not filter by age). Alternatively implement simple stale: if --now provided (for compat), age>30000 stale excluded unless --include-stale. For simplified spec we keep --now optional for stale exclusion but NOT for zones. Zones no time filtering.
Distance Haversine <= radius. Sort distance asc then vehicle_id asc. Pagination offset then limit. Include distance_m.

### 5. track <vehicle_id> --from <ts> --to <ts> [--limit <n> --offset <m>]
History within [from,to] inclusive sorted asc paginated. Both required, from <= to else exit 2. Not found exit 3.

### 6. distance <vehicle_id>
`{"vehicle_id":...,"total_distance_m":...}` exit 0, not found 3.

### 7. delete <vehicle_id>
Print `deleted` stdout exit 0 even if not exist. Invalid id exit 2.

### 8. stats
`{"live":#vehicles, "total_updates": total accepted count, "total_distance_m": sum, "avg_accuracy": avg}`.

### 9. batch
Stdin tab-delimited, empty/whitespace ignored.
- `update\tveh_id\tlat\tlng\ttimestamp[\taccuracy[\tspeed[\theading]]]` 5-8 fields, 5 minimal defaults 10,0,0. >8/<5 fail exit2.
- `delete\tveh_id` exactly 2 fields.
All-or-nothing: parse all first validate. Zones check uses default zones.json if exists, before stale. If any out_of_zone fail exit2 DB unchanged even if stale. Stale skipped not failed. Apply sequential simulated state, single atomic write, print `batch_ok <applied>` applied excludes stale.

### 10. clear
Print `cleared` exit 0.

### 11. geofence-check <lat> <lng> [--zones <path>]
Check point inside any zone (simple polygons, edge-inside). --zones optional else default /app/data/zones.json if exists else outside. No --now needed; if provided ignore. Output `{"inside":bool,"zone_id":string}` first matching file order. Invalid args/zones exit2.

## Exit Codes
0 success/stale/deleted/cleared/batch_ok, 2 invalid arg/zones/roads/batch fail, 3 not found/out_of_zone, 4 corrupt DB.

## Constraints
- Only Go stdlib.
- Atomic writes tmp+rename, no leftover, clean stale tmp, parent dirs auto.
- ID regex, zones polygon >=3 edge-inside, roads polyline points >=2, pagination offset then limit, batch 5-8, NaN/Inf rejection, history 10 asc with current last, Haversine total_distance.
- Large scale: 100+ vehicles near under 3s.
- Persistence survive restart.

Delivery under `/app/src/`.
