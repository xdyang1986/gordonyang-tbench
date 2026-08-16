# Step 1: Vehicle Location Tracking Service (Balanced Hard)

## Scenario

You are building the core location service for a ride-sharing platform under `/app`. It tracks vehicle locations for dispatch and ETA. It must be persistent, handle out-of-order GPS pings, support geofences with holes and circles and time windows, perform road-snapping, and provide batch operations.

Build a binary `locationctl` with `go build -o locationctl .` from `/app/src`, module `locationservice`. Stdlib only.

## CLI Format

```
locationctl --db <PATH> <command> [args] [flags]
```

- `--db` path to JSON DB. If not exists start empty. Create parent directories. Atomic writes: `<db>.tmp.<pid>` then rename, no tmp leftover.
- No command or first arg `help`, `--help`, `-h` → print help containing `update,get,list,near,track,distance,delete,stats,batch,clear,geofence-check` exit 0. Unknown command exit 2.

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
- `vehicle_id` regex `^[A-Za-z0-9_-]{1,64}$` else exit 2.
- `lat` [-90,90], `lng` [-180,180]. Reject NaN, Inf, Infinity case-insensitive.
- `timestamp_ms` int64 >=0 integer string only. Reject `1000.0`, `1e3`, `0x3e8`.
- `accuracy` >=0 default 10 NaN/Inf invalid.
- `speed` [0,50] default 0 >50 invalid exit 2.
- `heading` [0,360) default 0.
- `total_distance_m` Haversine sum R=6371000 over accepted only, starts 0.
- `history` up to 10 last accepted sorted asc, includes current as last.

DB file: JSON object mapping vehicle_id to Location. 0-byte or whitespace-only = empty store. Unparsable, array `[]`, literal `null`, truncated = corrupt exit 4 (backup `.corrupt.<nanosec>` optional).
Stale: per vehicle keep latest timestamp. If incoming <= stored, print `stale` exit 0, no DB change, no distance/history change.
Crash: ignore `<db>.tmp.*` on load, clean on next write.

## Zones – Polygons with Holes, Circles, Time Windows (No Antimeridian)

Zones file JSON array:

```json
{"id":"zone_a","polygon":[{"lat":...,"lng":...},...],"holes":[[{...},...]],"active_from":1000,"active_to":2000}
{"id":"circle_1","center":{"lat":...,"lng":...},"radius_m":500}
```

Rules:
- `id` non-empty.
- Polygon >=3 valid points. Holes optional, each hole >=3 valid points.
- Circle center valid lat/lng, radius 0 < radius <=1e6.
- Both polygon and circle in same zone invalid → exit 2 when used.
- `active_from`/`active_to` optional >=0, if both present from <= to else invalid exit 2.
- Point-in-polygon even-odd x=lng y=lat. Edge or vertex counts as inside. No antimeridian unwrapping required for Step1 (simple even-odd). Circle distance Haversine <= radius inside. Hole inside means outside.

Zone activation:
- Active at `ts` if `(active_from absent OR ts >= active_from) AND (active_to absent OR ts <= active_to)`. Bounds inclusive: ts==from and ts==to both active. Only-from active onward, only-to up to that time.

Filtering (balanced, intuitive):
- `update`: if `--zones <path>` else default `/app/data/zones.json` if exists. Filter active zones by update's own timestamp. If active non-empty, location must be inside at least one else out_of_zone exit 3. If no active zones at that timestamp, allow all.
- `list`, `near`, `geofence-check`: `--zones` optional. If `--now <ts>` provided, filter active zones by now, else all zones (no time filter). Intuitive simplified: if no active zones at given now, `list`/`near` includes all (no filtering), `geofence-check` returns outside (inside false). This avoids confusing [] behavior but keeps time-window seam.
  Example: update at 500 when zone active_from=1000 (no active, update succeeds even outside), later `list --now 1500` filters because zone active at 1500 and outside excluded. Update at 1500 inside succeeds, `list --now 500` includes it because no active at 500 (intuitive allow-all).

## Roads and Snapping

Roads file polyline only:

```json
[{"id":"road_1","points":[{"lat":...,"lng":...},...]}]
```

- `id` non-empty, `points` >=2 valid points. Invalid → exit 2.
- Snapping: equirectangular R=6371000 lat_ref=query lat. For each segment, closest point clamped t in [0,1], best overall. If distance <=50m snapped. Check all segments.

`near --roads` and `list --roads` only snapped.

## Commands

### 1. update <vehicle_id> <lat> <lng> <timestamp_ms> [--accuracy <f> --speed <f> --heading <f> --zones <path>]
Validate, zones check out_of_zone exit3 before stale. Stale print stale exit0. Otherwise Haversine distance to prior, add total_distance, history 10 sorted asc, persist atomic, print JSON without history but with total_distance_m.

### 2. get <vehicle_id> [--verbose]
Base without history but with total_distance_m. --verbose full. Not found exit3 invalid id exit2.

### 3. list [--since <ts> --until <ts> --limit <n> --offset <m> --zones <path> --roads <path> --now <ts>]
Sorted vehicle_id asc. since/until inclusive, since<=until else exit2. Zones filtered by now if provided else all, with intuitive no-active=allow-all for list. Roads snapping. Pagination offset then limit, limit0=[], offset>len=[].

### 4. near --lat <f> --lng <f> --radius <f> [--accuracy-max <f> --speed-min <f> --limit <n> --offset <m> --now <ts> --include-stale --zones <path> --roads <path>]
Validate lat/lng, radius [0,50000] else exit2. accuracy-max >=0, speed-min [0,50]. Stale: age=now-timestamp_ms negative→0, if now provided age>30000 excluded unless --include-stale. Zones filtered by now if provided else all, no-active=allow-all. Roads snapping. Distance Haversine <=radius. Sort distance asc then vehicle_id asc. Pagination offset then limit. Include distance_m.

### 5. track <vehicle_id> --from <ts> --to <ts> [--limit <n> --offset <m>]
History within [from,to] inclusive sorted asc paginated. Both required from<=to else exit2. Not found exit3.

### 6. distance <vehicle_id>
`{"vehicle_id":...,"total_distance_m":...}` exit0 not found 3.

### 7. delete <vehicle_id>
Print deleted exit0 even if not exist invalid id exit2.

### 8. stats
`{"live":#vehicles, "total_updates": total accepted count, "total_distance_m": sum, "avg_accuracy": avg}`.

### 9. batch
Stdin tab-delimited empty/whitespace ignored.
- `update\tveh_id\tlat\tlng\ttimestamp[\taccuracy[\tspeed[\theading]]]` 5-8 fields, 5 minimal defaults 10,0,0, empty string means default. >8/<5 fail exit2.
- `delete\tveh_id` exactly 2 fields.
All-or-nothing: parse all first validate. Zones check uses default zones.json if exists per op timestamp before stale. If any out_of_zone fail exit2 DB unchanged even if stale. Stale skipped not failed. Apply sequential simulated state single atomic write print `batch_ok <applied>` applied excludes stale.

### 10. clear
Print cleared exit0.

### 11. geofence-check <lat> <lng> [--zones <path>] [--now <ts>]
Check inside any active zone (polygons with holes, circles, edge-inside). --zones optional else default zones.json if exists else outside. --now filters active, if no active at now → outside. Output `{"inside":bool,"zone_id":string}` first matching file order. Invalid args/zones exit2.

## Exit Codes
0 success/stale/deleted/cleared/batch_ok, 2 invalid arg/zones/roads/batch, 3 not found/out_of_zone, 4 corrupt DB.

## Constraints
- Only Go stdlib.
- Atomic writes tmp+rename no leftover clean stale tmp parent dirs auto.
- ID regex, zones polygon >=3 holes >=3 circles radius 0< <=1e6 time inclusive edge-inside holes outside, roads polyline >=2, pagination offset then limit, batch 5-8 empty default, NaN/Inf rejection, history 10 asc current last, Haversine distance.
- Large scale: 150+ vehicles near under 3s.
- Persistence survive restart.

Delivery under `/app/src/`.
