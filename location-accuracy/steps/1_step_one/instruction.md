# Step 1: Vehicle Location Tracking Service – Extreme Hard (Uber-like)

## Scenario
You are building the core location service for a ride-sharing platform similar to Uber at `/app`. It tracks real-time locations of thousands of vehicles for dispatch, ETA, and rider display. Must be crash-consistent, persistent, handle out-of-order GPS pings, validation, geospatial queries including complex geofences (polygon with holes, circles, time windows, antimeridian crossing), operator batch ops with atomicity, analytics, and road-snapping filters.

All code goes under `/app/src/` with Go module `locationservice`. Binary `locationctl`.

## CLI Interface
```
locationctl --db <PATH> <command> [args] [flags]
```
- `--db` global flag: path to JSON DB file (e.g., `/app/data/locations.json`). If missing, start empty. Must create parent dirs if needed. All writes atomic: write temp file in same dir then rename. Temp pattern `<db>.tmp.<pid>`, no leftover tmp files on success.
- If no command or `help` / `--help` / `-h` appears alone or as first arg, print help containing strings `update,get,list,near,track,distance,delete,stats,batch,clear,geofence-check` and exit 0. Unknown command → exit 2.

### Data Model
Each vehicle extended location:
```json
{
  "vehicle_id": "veh_123",
  "lat": 37.7749,
  "lng": -122.4194,
  "timestamp_ms": 1710000000000,
  "accuracy": 5.0,
  "speed": 12.5,
  "heading": 90.0,
  "total_distance_m": 1235.6,
  "history": [
    {"lat":37.7748,"lng":-122.4193,"timestamp_ms":1710000000000,"accuracy":5,"speed":10,"heading":90},
    ...
  ]
}
```
- `vehicle_id`: regex `^[A-Za-z0-9_-]{1,64}$` – strict, no spaces, no empty, length 1-64.
- `lat` [-90,90], `lng` [-180,180] – float64, must reject NaN, Inf, -Inf.
- `timestamp_ms` int64 >=0, must be integer string (no float, no hex).
- `accuracy` >=0 meters, default 10.0, NaN/Inf invalid.
- `speed` >=0 and <=50 m/s, default 0.0 – >50 invalid exit 2, negative invalid.
- `heading` [0,360) default 0.0, 360 exclusive, NaN/Inf invalid.
- `total_distance_m`: sum of Haversine distances between successive accepted updates for that vehicle, persisted, starts 0. R=6371000m.
- `history`: last up to 10 accepted locations sorted timestamp ascending (oldest first, newest last includes current).

DB file JSON map vehicle_id → Location. Empty file (0 bytes) OR whitespace-only → empty store. Invalid JSON (non-empty unparsable) OR JSON not object (e.g., array) → stderr + exit 4. Atomic write.

Store per vehicle latest by timestamp. If incoming timestamp <= stored timestamp, stale/out-of-order ignored: print `stale` stdout exit 0, no DB change.

### Geofence Zones – Extreme
Zones file format JSON array of zones, each zone polygon-based OR circle-based:

Polygon:
```json
{
  "id":"zone_a",
  "polygon":[{"lat":37.7,"lng":-122.4},{"lat":37.8,"lng":-122.4},{"lat":37.8,"lng":-122.5}],
  "holes":[[{"lat":37.75,"lng":-122.45},{"lat":37.76,"lng":-122.45},{"lat":37.76,"lng":-122.44}]],
  "active_from":1000,"active_to":2000
}
```
Circle:
```json
{"id":"circle_1","center":{"lat":37.7,"lng":-122.4},"radius_m":500,"active_from":1000,"active_to":2000}
```
- id non-empty.
- Polygon >=3 valid points. Holes optional each >=3 valid.
- Circle center valid, radius_m >0 <=1000000.
- If both polygon and circle present → invalid zone file exit 2.
- active_from/to optional >=0, if both require from<=to else invalid.
- Point-in-polygon ray casting even-odd x=lng y=lat MUST handle antimeridian: unwrap polygon longitudes to continuous (no 358° jump) and unwrap query lng near polygon. Point on edge/vertex → inside.
- Circle: Haversine distance <= radius.
- For update: if --zones <path> provided use it else if default /app/data/zones.json exists use it. After filtering active zones at update timestamp, if active list non-empty, location must be inside at least one active zone else out_of_zone exit 3.
- For near: --zones optional filter.
- For list: --zones and --roads optional.
- For geofence-check: see below.

### Roads & Snapping
Roads file polyline + backward compat segment mixed:
```json
[
  {"id":"road_1","points":[{"lat":37.7749,"lng":-122.4194},{"lat":37.7849,"lng":-122.4094}]},
  {"id":"seg","start":{"lat":37.7749,"lng":-122.4194},"end":{"lat":37.7749,"lng":-122.4094}}
]
```
- id non-empty, points >=2 valid, or start/end valid. Mixed allowed. Invalid → exit 2 when used.
- Snapping: equirectangular x=R*lng_rad*cos(lat_ref) y=R*lat_rad lat_ref=query lat, R=6371000, closest point clamped t in [0,1], distance hypot, best <=50m → snapped/on-road.

For near --roads: only vehicles whose location snaps within 50m.
For list --roads: same.

### Commands
#### 1. update <vehicle_id> <lat> <lng> <timestamp_ms> [--accuracy <f> --speed <f> --heading <f> --zones <path>]
- Validates, zones check out_of_zone exit3, stale check stale exit0, compute Haversine distance add to total_distance, maintain history 10 max sorted, atomic persist, print JSON without history but including total_distance_m.

#### 2. get <vehicle_id> [--verbose]
- Without verbose base+total_distance, with verbose full with history. Not found exit3, invalid id exit2.

#### 3. list [--since <ts> --until <ts> --limit <n> --offset <m> --zones <path> --roads <path> --now <ts>]
- Sorted by vehicle_id asc, since/until filter inclusive, since<=until else exit2, zones filter active filtered by --now if provided else all active, roads filter snap, pagination offset then limit, limit 0=>[], offset>len=>[].

#### 4. near --lat <f> --lng <f> --radius <f> [--accuracy-max <f> --speed-min <f> --limit <n> --offset <m> --now <ts> --include-stale --zones <path> --roads <path>]
- lat [-90,90] lng [-180,180] radius [0,50000] else exit2, accuracy-max >=0, speed-min 0-50, now age = now - ts age<0=>0 age>30000 stale excluded only when now provided unless include-stale, zones active filtered by now if provided, roads snap, distance filter Haversine <=radius, sort distance asc then vehicle_id asc, pagination.
- Each result object is the base location object plus "distance_m": Haversine distance in metres from the query point.

#### 5. track <vehicle_id> --from <ts> --to <ts> [--limit <n> --offset <m>]
- History within [from,to] inclusive sorted asc paginated, requires both flags from<=to else exit2, not found exit3.

#### 6. distance <vehicle_id> -> {"vehicle_id":...,"total_distance_m":...} exit0 not found exit3

#### 7. delete <vehicle_id> -> deleted/not_found exit0 invalid id exit2

#### 8. stats -> {"live":..., "total_updates":..., "total_distance_m":..., "avg_accuracy":...}

#### 9. batch
- Reads stdin tab-delimited, empty/whitespace lines ignored.
- update\tvehicle_id\tlat\tlng\ttimestamp[\taccuracy[\tspeed[\theading]]] – variable 5-8 fields: 5 minimal defaults 10,0,0; 6-8 may have empty string meaning default; >8 or <5 fail exit2. accuracy>=0 speed 0-50 heading [0,360) else fail.
- delete\tvehicle_id exactly 2 fields.
- All-or-nothing: parse all, validate, zones check default zones.json if exists per op timestamp, if any out_of_zone fail exit2 no change. Stale skips not fail (but zone check still before stale).
- Apply sequentially simulating state, single atomic write, print batch_ok <applied> where applied excludes stale.

#### 10. clear -> cleared exit0

#### 11. geofence-check <lat> <lng> [--zones <path>] [--now <ts>]
- Check inside any active zone (polygon holes circles antimeridian edge inside). --zones optional else default /app/data/zones.json if exists else outside. --now optional active filtering, if provided filter active, else ignore time. Output {"inside":bool,"zone_id":string} first matching. Invalid args/zones exit2.

### Exit Codes 0 success/stale/delete/clear/batch_ok, 2 invalid arg/malformed/zones/roads invalid/batch fail, 3 not found/out_of_zone, 4 corrupt DB

### Constraints
Go stdlib only, go build -o locationctl ., go.mod locationservice, Haversine R=6371000, atomic writes, parent dirs, no tmp leftover, vehicle ID regex, zones holes circles time antimeridian edge inside, roads mixed, help contains commands, pagination offset then limit, batch variable, NaN/Inf reject, large scale 800+ perf <3s.

Deliverable /app/src/. Tests cover all extreme.

