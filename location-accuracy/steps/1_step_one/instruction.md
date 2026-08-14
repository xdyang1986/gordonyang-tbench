# Step 1: Vehicle Location Tracking Service – Extreme Hard

## Scenario
You are building the core location service for a ride-sharing platform at `/app`. It tracks real-time vehicle locations for dispatch, ETA, and rider display. Must be crash-consistent, persistent, handle out-of-order GPS pings, geofences (polygon with holes, circles, time windows, antimeridian crossing, edge-inside), road-snapping, batch atomic ops, analytics.

Binary: `locationctl` built via `go build -o locationctl .` from `/app/src`, module `locationservice`. Stdlib only.

## CLI
```
locationctl --db <PATH> <command> [args] [flags]
```
- `--db` path to JSON DB file. If missing start empty. Must create parent dirs. Writes atomic: temp `<db>.tmp.<pid>` in same dir then rename, fsync best effort, no leftover on success. No corrupt leave.
- No command or `help`/`--help`/`-h` alone or first arg → print help containing strings `update,get,list,near,track,distance,delete,stats,batch,clear,geofence-check` and exit 0. Unknown command → exit 2.

### Data Model
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
- `vehicle_id`: `^[A-Za-z0-9_-]{1,64}$`, no spaces/empty, 1-64.
- `lat` [-90,90], `lng` [-180,180], reject NaN/Inf/-Inf.
- `timestamp_ms` int64 ≥0, must be integer string no float/hex.
- `accuracy` ≥0 default 10, NaN/Inf invalid.
- `speed` 0-50 m/s default 0, >50 invalid exit2, negative invalid.
- `heading` [0,360) default 0, 360 exclusive.
- `total_distance_m`: sum Haversine (R=6371000m) between successive accepted updates per vehicle, starts 0, persisted.
- `history`: up to 10 last accepted locations sorted timestamp asc (oldest first, newest last includes current).

DB file JSON map vehicle_id→Location. 0-byte or whitespace-only file → empty store. Non-empty unparsable or JSON not object (e.g. array) → stderr + exit 4.

Stale/out-of-order: per vehicle keep latest by timestamp. If incoming timestamp ≤ stored timestamp → ignored: print `stale` to stdout, exit 0, no DB change.

### Zones – polygon with holes, circles, time, antimeridian
File JSON array of zones, each either polygon-based OR circle-based:
```json
{"id":"zone_a","polygon":[{"lat":...,"lng":...},...],"holes":[[{...},...]],"active_from":1000,"active_to":2000}
{"id":"circle_1","center":{"lat":...,"lng":...},"radius_m":500}
```
- id non-empty.
- polygon ≥3 valid points, holes optional each ≥3 valid.
- circle center valid, radius_m >0 ≤1e6.
- Both polygon+circle present → invalid zone file exit2.
- active_from/to optional ≥0, if both require from≤to else invalid.
- Point-in-polygon must be even-odd, x=lng y=lat, handle antimeridian by unwrapping longitudes to continuous (no 358° jump) so a rectangle 179→-179 is 2° wide, not 358°. Point on edge/vertex → inside. Circle via Haversine ≤radius. Holes mean inside outer but outside hole → outside.
- For `update`: if `--zones <path>` provided use it else if default `/app/data/zones.json` exists use it. After filtering active zones at update timestamp (active if (from==absent or ts≥from) and (to==absent or ts≤to)), if active list non-empty, location must be inside at least one active zone else output must contain string `out_of_zone` (stdout preferred, stderr also accepted for fairness) and exit 3.
- For `near`/`list`: `--zones` optional filter active zones (filtered by `--now` if provided else all).
- For `geofence-check`: see below.

### Roads & Snapping
File mixed formats:
```json
[{"id":"road_1","points":[{"lat":...,"lng":...},...]},
 {"id":"seg","start":{"lat":...,"lng":...},"end":{"lat":...,"lng":...}}]
```
- id non-empty, points ≥2 valid or start/end valid. Any invalid entry → exit2 when that roads file is used.
- Snapping: equirectangular projection R=6371000, lat_ref=query lat, find closest point on any segment, distance ≤50m → snapped/on-road. Must check all segments of polyline, not just endpoints. Output snapped bool, road_id when snapped.

`near --roads` / `list --roads`: only vehicles snapped within 50m.

### Commands

#### 1. update <vehicle_id> <lat> <lng> <timestamp_ms> [--accuracy <f> --speed <f> --heading <f> --zones <path>]
Validate all fields per Data Model + NaN/Inf checks. Zones check as above (out_of_zone exit3). Stale check (stale stdout exit0). Compute Haversine distance to prior accepted location add to total_distance, maintain history 10 asc, atomic persist, print JSON of stored location without history but including total_distance_m.

#### 2. get <vehicle_id> [--verbose]
Base returns without history but with total_distance_m, verbose full with history. Not found exit3, invalid id exit2.

#### 3. list [--since <ts> --until <ts> --limit <n> --offset <m> --zones <path> --roads <path> --now <ts>]
Sorted vehicle_id asc, since/until inclusive, since≤until else exit2, zones active filtered by now if provided else all, roads snap filter, pagination offset then limit, limit 0→[], offset>len→[].

#### 4. near --lat <f> --lng <f> --radius <f> [--accuracy-max <f> --speed-min <f> --limit <n> --offset <m> --now <ts> --include-stale --zones <path> --roads <path>]
lat/lng/radius validation radius [0,50000] else exit2, accuracy-max ≥0, speed-min 0-50. now age = now-ts, age<0→0, age>30000 stale excluded only when now provided unless include-stale. zones active filtered by now if provided, roads snap, distance Haversine ≤radius, sort distance asc then vehicle_id asc, pagination. Each result is base location plus `distance_m` (Haversine from query point).

#### 5. track <vehicle_id> --from <ts> --to <ts> [--limit <n> --offset <m>]
History within [from,to] inclusive sorted asc paginated, both flags required, from≤to else exit2, not found exit3.

#### 6. distance <vehicle_id> → {"vehicle_id":...,"total_distance_m":...} exit0, not found exit3

#### 7. delete <vehicle_id> → `deleted` stdout exit0, invalid id exit2. If vehicle not found, still prints `deleted` exit0.

#### 8. stats → {"live":#vehicles, "total_updates": total accepted count, "total_distance_m": sum, "avg_accuracy": avg}

#### 9. batch – reads stdin tab-delimited, empty/whitespace lines ignored.
- `update\tveh_id\tlat\tlng\ttimestamp[\taccuracy[\tspeed[\theading]]]` – variable 5-8 fields: 5 minimal defaults 10,0,0; 6-8 may have empty string meaning default; >8 or <5 → fail exit2.
- `delete\tveh_id` exactly 2 fields.
- All-or-nothing: parse all lines first, validate all, zones check default zones.json if exists per op timestamp (before stale), if any out_of_zone → fail exit2 no change. Stale ops are skipped not failed but zone check still applies before stale.
- Apply sequentially simulating state, single atomic write, print `batch_ok <applied>` where applied excludes stale.

#### 10. clear → `cleared` exit0

#### 11. geofence-check <lat> <lng> [--zones <path>] [--now <ts>]
Check inside any active zone (polygon with holes, circles, antimeridian, edge-inside). --zones optional else default /app/data/zones.json if exists else outside. --now optional active filtering. Output {"inside":bool,"zone_id":string} first matching by file order. Invalid args/zones → exit2.

### Exit Codes 0 success/stale/deleted/cleared/batch_ok, 2 invalid arg/malformed/zones/roads invalid/batch fail, 3 not found/out_of_zone, 4 corrupt DB

### Constraints
Stdlib only, atomic writes, parent dirs, no tmp leftover, ID regex, zones holes circles time antimeridian edge inside, roads mixed, help contains commands, pagination offset then limit, batch variable, NaN/Inf reject, large scale 800+ vehicles perf <3s, history 10 asc, total_distance Haversine.

Tests cover all above extreme cases. Delivery under /app/src/.
