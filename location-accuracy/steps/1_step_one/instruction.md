# Step 1: Vehicle Location Tracking Service (Hardened)

## Scenario

You are building the core location service for a ride-sharing platform under `/app`. This service tracks real-time vehicle locations for dispatch, ETA, and rider display. It must be crash-consistent and persistent, handle out-of-order GPS pings, support complex geofences (polygons with holes, circles, time windows, antimeridian crossing), perform road-snapping with mixed formats, and provide batch atomic operations and analytics.

Build a binary `locationctl` with `go build -o locationctl .` from `/app/src`, module `locationservice`. Stdlib only.

## CLI Format

```
locationctl --db <PATH> <command> [args] [flags]
```

- `--db` path to JSON DB file. If not exists start empty. Must create parent directories if needed. Writes atomic: write to temp `<db>.tmp.<pid>` in same directory, then rename. Use best-effort fsync. On success no tmp leftover.
- If no command or first arg `help`, `--help`, `-h` → print help containing `update,get,list,near,track,distance,delete,stats,batch,clear,geofence-check` exit 0. Unknown command exit 2.

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
- `vehicle_id` regex `^[A-Za-z0-9_-]{1,64}$`. Empty, spaces, special chars, longer than 64 invalid. Dash/underscore allowed.
- `lat` [-90,90], `lng` [-180,180]. Reject NaN, Inf, Infinity case-insensitive, also check isNaN/IsInf after parse.
- `timestamp_ms` int64 >=0 integer string only. Reject `1000.0`, `1e3`, `0x3e8`, `1E3`, negative, empty, whitespace.
- `accuracy` >=0 default 10 NaN/Inf invalid, 0 valid.
- `speed` [0,50] default 0 >50 invalid exit2, negative invalid.
- `heading` [0,360) default 0, 360 exclusive invalid.
- `total_distance_m` Haversine sum R=6371000 between successive accepted updates only, not on stale or out_of_zone. Starts 0 persisted.
- `history` up to 10 last accepted locations sorted asc timestamp, includes current as last. After stale, last must still equal current.

DB file: JSON object mapping vehicle_id to Location. 0-byte or whitespace-only = empty store. Unparsable JSON, array `[]`, literal `null`, truncated file = corrupt: exit 4 and mandatory backup `<db>.corrupt.<nanosec>` where nanosec is integer nanosecond timestamp, containing original corrupt content. Multiple corruptions must produce distinct suffixes. Backup file must contain exact original content.

Stale handling: per vehicle keep latest timestamp. If incoming <= stored, print `stale` stdout exit 0, no DB change, no distance/history change. Same timestamp also stale. History last must remain current after stale.

Crash consistency:
- Pre-existing stale files like `<db>.tmp.<pid>` must be ignored when loading DB and cleaned on next successful write.
- Truncated file (valid JSON prefix cut mid-object) must take corruption path: exit 4 and create `.corrupt.<nanosec>` backup.
- Parent directories deeply nested like `/a/b/c/d/e/f/db.json` must be auto-created.

## Zones – Polygons with Holes, Circles, Time Windows, Antimeridian

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
- `active_from`/`active_to` optional >=0, if both present from <= to else invalid file exit 2.
- Point-in-polygon even-odd x=lng y=lat. Must handle antimeridian crossing by unwrapping longitudes to continuous range, so rectangle from 179 to -179 is 2 deg wide not 358. Point on edge or vertex counts as inside. Circle Haversine distance <= radius inside. Holes outside (inside outer but inside hole → outside).
- Circle exact radius boundary: distance == radius counts as inside; just beyond radius outside.

Zone activation and filtering (seam):
- Active at `ts` if `(active_from absent OR ts >= active_from) AND (active_to absent OR ts <= active_to)`. Bounds inclusive: ts==from and ts==to both active. Only-from active onward, only-to up to that time. from-1 and to+1 inactive.
- `update`: if --zones <path> else default `/app/data/zones.json` if exists. Filter active zones by update's own timestamp. If active non-empty must be inside at least one else out_of_zone exit 3. If no active at that time allow all.
- `list`, `near`, `geofence-check`: --zones optional. If --now <ts> provided filter active by now, else all zones (no time filter). Intuitive: if no active zones at given now, list/near allow all (no filtering) rather than [], geofence-check returns outside (inside false). This keeps time-window seam but intuitive.
  Example: update at 500 when zone active_from=1000 (no active → update succeeds even outside), later `list --zones <file> --now 1500` should exclude outside because active at 1500. Conversely update at 1500 inside succeeds, `list --now 500` includes it because no active at 500.

## Roads and Snapping – Mixed Formats

Roads file mixed formats:

```json
[{"id":"road_1","points":[{"lat":...,"lng":...},...]},
 {"id":"seg","start":{"lat":...,"lng":...},"end":{"lat":...,"lng":...}}]
```

Rules:
- `id` non-empty. Polyline `points` >=2 valid points, or legacy `start/end` both valid. Any invalid entry → exit 2 when used.
- Snapping uses equirectangular projection R=6371000 lat_ref=query lat. For each segment in each road, find closest point clamped t in [0,1]. Keep best overall. If distance <=50m snapped on-road. Must check all segments and interior points, not just endpoints. Closest among segments matters.

For `near --roads` and `list --roads`, only snapped within 50m included.

## Commands

### 1. update <vehicle_id> <lat> <lng> <timestamp_ms> [--accuracy <f> --speed <f> --heading <f> --zones <path>]
Validate all fields including NaN/Inf checks. Perform zones check out_of_zone exit3 before stale. On stale print stale exit0. Otherwise compute Haversine distance to prior accepted, add to total_distance, maintain history 10 sorted asc, persist atomically, print JSON without history but with total_distance_m.

### 2. get <vehicle_id> [--verbose]
Base returns without history but with total_distance_m. --verbose full including history. Not found exit3 invalid id exit2.

### 3. list [--since <ts> --until <ts> --limit <n> --offset <m> --zones <path> --roads <path> --now <ts>]
Sorted vehicle_id asc. since/until inclusive, must satisfy since <= until else exit2. Zones filter uses active filtered by now if provided else all, with intuitive no-active=allow-all. Roads via snapping. Pagination offset then limit: offset first then limit. Limit0=[], offset>len=[].

### 4. near --lat <f> --lng <f> --radius <f> [--accuracy-max <f> --speed-min <f> --limit <n> --offset <m> --now <ts> --include-stale --zones <path> --roads <path>]
Validate lat/lng, radius [0,50000] else exit2. accuracy-max >=0, speed-min [0,50]. For staleness: age=now-timestamp_ms negative→0, if now provided age>30000 stale excluded unless --include-stale. Zones active filtered by now if provided else all, no-active=allow-all. Roads via snapping. Distance Haversine <=radius. Sort distance asc then vehicle_id asc. Pagination offset then limit. Each result includes base location plus distance_m (Haversine). Radius 0 exact boundary: only exactly at query point.

### 5. track <vehicle_id> --from <ts> --to <ts> [--limit <n> --offset <m>]
Return history entries within [from,to] inclusive sorted asc and paginated. Both flags required from<=to else exit2. Not found exit3.

### 6. distance <vehicle_id>
Returns `{"vehicle_id":...,"total_distance_m":...}` exit0 not found 3.

### 7. delete <vehicle_id>
Prints deleted stdout exit0 even if not exist invalid id exit2.

### 8. stats
`{"live":#vehicles, "total_updates": total accepted count, "total_distance_m": sum, "avg_accuracy": avg}`.

### 9. batch
Reads stdin tab-delimited. Empty/whitespace lines ignored.

Line formats:
- `update\tveh_id\tlat\tlng\ttimestamp[\taccuracy[\tspeed[\theading]]]` variable 5-8. 5 minimal defaults 10,0,0. 6-8 may have empty string meaning default. More than 8 or fewer than 5 fail exit2.
- `delete\tveh_id` exactly 2 fields. Mixed update+delete same vehicle order matters.
All-or-nothing atomic: parse all first validate all. Zones check uses default zones.json if exists per op timestamp, must be before stale check. If any operation would be out_of_zone, fail exit2 DB unchanged even if that operation is stale (zones before stale still fails when stale op out_of_zone). Stale operations skipped not failed. Batch with accuracy/speed/heading validation same as update. After validation apply sequentially to simulated state with single atomic write, print `batch_ok <applied>` where applied excludes stale.

### 10. clear
Prints cleared exit0.

### 11. geofence-check <lat> <lng> [--zones <path>] [--now <ts>]
Check if point inside any active zone (polygons with holes, circles, antimeridian, edge-inside). --zones optional else default /app/data/zones.json if exists else outside. --now optionally filters active zones, if no active at now → outside. Output `{"inside":bool,"zone_id":string}` first matching by file order. Invalid args or zones must exit2. Circle exact radius: distance==radius inside, just beyond outside.

## Exit Codes
0 success/stale/deleted/cleared/batch_ok, 2 invalid arg/malformed/invalid zones/roads/batch fail, 3 not found/out_of_zone, 4 corrupt DB (with `.corrupt.<nanosec>` backup mandatory).

## Constraints
- Only Go stdlib.
- Atomic writes tmp+rename no leftover tmp files clean stale tmp files parent dirs auto-created deeply nested.
- ID regex, zones with holes/circles/time/antimeridian/edge-inside, roads mixed polyline and start/end, pagination offset then limit, batch variable fields empty default, NaN/Inf rejection, history 10 asc with current as last, total_distance Haversine, batch zones before stale.
- Large scale: 800+ vehicles near query under 3 seconds, list with zones and roads 200 vehicles.
- Persistence survive restart including corrupt backup handling with integer nanosec suffix distinct, containing original.

Delivery under `/app/src/`.
