# Step 1: Vehicle Location Tracking Service – Hard (Uber-like)

## Scenario
You are building the core location service for a ride-sharing platform similar to Uber at `/app`. It tracks real-time locations of thousands of vehicles for dispatch, ETA, and rider display. Must be crash-consistent, persistent, handle out-of-order GPS pings, validation, geospatial queries, operator batch ops, analytics, and geofence compliance.

All code goes under `/app/src/` with Go module `locationservice`. Binary `locationctl`.

## CLI Interface
```
locationctl --db <PATH> <command> [args] [flags]
```
- `--db` global flag: path to JSON DB file (e.g., `/app/data/locations.json`). If missing, start empty. Must create parent dirs if needed. All writes atomic: write temp file in same dir then rename.

If no command or `help` / `--help` / `-h` appears alone or as first arg, print help containing strings `update,get,list,near,track,distance,delete,stats,batch,clear` and exit 0. Unknown command → exit 2.

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
- `vehicle_id`: regex `^[A-Za-z0-9_-]{1,64}$`
- `lat` [-90,90], `lng` [-180,180] – float64
- `timestamp_ms` int64 >=0
- `accuracy` >=0 meters, default 10.0
- `speed` >=0 and <=50 m/s, default 0.0 – >50 is invalid argument exit 2
- `heading` [0,360) default 0.0
- `total_distance_m`: sum of Haversine distances between successive accepted updates for that vehicle, persisted, starts 0
- `history`: last up to 10 accepted locations for that vehicle sorted timestamp ascending (oldest first, newest last includes current). On update, push new and trim oldest.

DB file is JSON: map vehicle_id → Location (including total_distance_m + history). Empty file (0 bytes) → empty store. Invalid JSON → stderr + exit 4. Atomic write: `<db>.tmp.<pid>` same dir then rename.

Store per vehicle latest by timestamp. If incoming timestamp <= stored timestamp, stale/out-of-order ignored: print `stale` stdout exit 0, no DB change.

### Geofence Zones
Zones file `/app/data/zones.json` optionally exists (environment may create). Format JSON array:
```json
[
  {"id":"zone_a","polygon":[{"lat":37.7,"lng":-122.4},{"lat":37.8,"lng":-122.4},{"lat":37.8,"lng":-122.5}]}
]
```
- Each zone id non-empty, polygon >=3 points, lat/lng valid.
- Point-in-polygon via ray casting (even-odd, x=lng, y=lat).
- For `update`, check zones: if `--zones <path>` provided, load that file; else if default `/app/data/zones.json` exists, use it. If zones list non-empty, location must be inside at least one polygon, else print `out_of_zone` stdout exit 3, no update.
- For `near`, optional `--zones <path>`: if provided, only include vehicles whose current location inside any zone in that file. No default zones for near unless flag provided.

### Commands

#### 1. `update <vehicle_id> <lat> <lng> <timestamp_ms> [--accuracy <f> --speed <f> --heading <f> --zones <path>]`
- Validates all. On invalid → stderr exit 2.
- Zones check → `out_of_zone` exit 3 if outside.
- Stale check → `stale` exit 0.
- Compute distance from old location to new via Haversine (Earth R=6371000m), add to total_distance_m.
- Maintain history (10 max, sorted asc).
- Atomically persist, print JSON of stored location **without history** but including total_distance_m (single line) to stdout, exit 0.

#### 2. `get <vehicle_id> [--verbose]`
- Without verbose: prints JSON base + total_distance_m (no history) exit 0.
- With `--verbose`: prints full extended including history.
- Not found → stderr exit 3.
- Invalid vehicle_id → exit 2.

#### 3. `list [--since <ts> --until <ts> --limit <n> --offset <m>]`
- List all vehicles sorted by vehicle_id asc.
- `--since` int64 >=0, `--until` int64 >=0: filter vehicles whose latest timestamp in [since,until] inclusive. If both provided require since<=until else exit 2.
- Pagination: offset then limit. Offset default 0, limit default -1 meaning all. Limit 0 means output []? Actually limit 0 means output empty array. Define limit >=0, offset >=0 else exit 2. If offset > len, output [].
- Output JSON array of base objects (including total_distance_m) sorted.

#### 4. `near --lat <f> --lng <f> --radius <f> [--accuracy-max <f> --speed-min <f> --limit <n> --offset <m> --now <ts> --include-stale --zones <path>]`
- `lat` [-90,90], `lng` [-180,180], `radius` [0,50000] else exit 2.
- Optional `--accuracy-max` >=0: only vehicles accuracy <= max
- `--speed-min` >=0 <=50: only speed >= min
- `--now <ts>`: if provided, compute `age = now - vehicle.timestamp_ms` (age<0 =>0). If age>30000 (30s) consider stale and exclude **only when --now is provided** unless `--include-stale` flag present. If --now not provided, do NOT apply staleness filtering (include all) to preserve Step1 backward compatibility.
- `--zones <path>`: only include vehicles whose location inside any zone in that file.
- After filtering by distance (Haversine <= radius) plus above, sort by distance_m asc then vehicle_id asc.
- Pagination: offset then limit (limit 0 => [])
- Output JSON array of objects extended with `distance_m` float.

#### 5. `track <vehicle_id> --from <ts> --to <ts> [--limit <n> --offset <m>]`
- Returns history points for vehicle within [from,to] inclusive, sorted timestamp asc, paginated (offset then limit). Each entry is HistoryEntry JSON.
- Requires both --from and --to, from<=to else exit 2. from,to >=0 else exit 2.
- Not found → exit 3.
- Output JSON array.

#### 6. `distance <vehicle_id>`
- Prints JSON `{"vehicle_id":...,"total_distance_m": float}` exit 0, not found exit 3.

#### 7. `delete <vehicle_id>`
- Deletes vehicle if exists. Always exit 0. Prints `deleted` if removed else `not_found`.
- Invalid id → exit 2.

#### 8. `stats`
- Prints JSON `{"live": int, "total_updates": int, "total_distance_m": float, "avg_accuracy": float}` where live = number of vehicles, total_updates = sum len(history) across all vehicles, total_distance_m = sum total_distance_m, avg_accuracy = average accuracy of current locations (0 if live==0). Exit 0.

#### 9. `batch`
- Reads operations from stdin, one per line tab-delimited, empty lines ignored.
- Format:
  - `update\tvehicle_id\tlat\tlng\ttimestamp_ms\taccuracy\tspeed\theading` – exactly 8 tab fields, all must be valid (accuracy>=0, speed 0-50, heading [0,360) etc) else batch fails exit 2. Accuracy,speed,heading optional? For harder, require 8 fields with defaults allowed as empty string? To keep deterministic, require exactly 8 fields where accuracy,speed,heading may be empty string meaning default (10,0,0). Example: `update\tveh1\t37.0\t-122.0\t1000\t\t\t` means defaults. Implement empty handling.
  - `delete\tvehicle_id`
- All-or-nothing: parse all lines first, validate. If any malformed, invalid arg, or would be out_of_zone (check default zones file `/app/data/zones.json` if exists), fail whole batch exit 2 and append nothing, no DB change. Stale updates are NOT considered failure; they are skipped (do not count toward applied).
- If all valid, apply sequentially in order to simulate state (including total_distance increment for updates, history), then single atomic file write at end.
- On success print `batch_ok <applied>` where applied = number of actually applied ops (excluding stale skips), exit 0.

#### 10. `clear`
- Truncate DB to empty map atomically, print `cleared` exit 0.

### Validation & Exit Codes
- 0 success, or stale ignored (print `stale`), or delete/clear/batches.
- 2 invalid argument / malformed / out_of_zone in batch context? For update, out_of_zone is exit 3 per spec, but in batch out_of_zone makes batch fail exit 2.
- 3 vehicle not found (get,track,distance) or out_of_zone for single update, or low_accuracy/outlier in Step2 (not Step1)
- 4 corrupt DB

### Constraints
- Go stdlib only, no external deps.
- Must build with `go build -o locationctl .` and `go build ./...` from `/app/src`, GOFLAGS=-mod=mod, no network.
- go.mod module `locationservice`.
- Haversine mandatory.
- Atomic writes, parent dir creation.
- Vehicle ID regex strictly enforced.
- Zones point-in-polygon required if file present.
- Help must contain command names.
- Pagination logic must be correct (offset then limit).

### Example
```bash
locationctl --db /app/data/locations.json update veh_123 37.7749 -122.4194 1000 --accuracy 5 --speed 10 --heading 90
locationctl --db /app/data/locations.json get veh_123
locationctl --db /app/data/locations.json list --since 500 --until 1500 --limit 2 --offset 0
locationctl --db /app/data/locations.json near --lat 37.7749 --lng -122.4194 --radius 1000 --accuracy-max 20 --speed-min 1 --limit 10
locationctl --db /app/data/locations.json track veh_123 --from 0 --to 2000 --limit 5
locationctl --db /app/data/locations.json distance veh_123
locationctl --db /app/data/locations.json stats
printf "update\tveh2\t37.0\t-122.0\t2000\t5\t10\t90\ndelete\tveh_123\n" | locationctl --db /app/data/locations.json batch
locationctl --db /app/data/locations.json delete veh_123
locationctl --db /app/data/locations.json clear
```

Deliverable under `/app/src/`. Tests will cover persistence, sorting, Haversine, stale, zones polygon (inside/outside, concave), list/near/track pagination, batch atomicity (fail on one bad line keeps DB unchanged, stale skips), stats, distance total, corrupt handling, help.
