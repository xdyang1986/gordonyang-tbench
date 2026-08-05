# Step 1: Vehicle Location Tracking Service (Uber-like)

## Scenario
You are building the core location service for a ride-sharing platform similar to Uber. The platform needs to track real-time locations of thousands of vehicles to dispatch the nearest driver, estimate ETAs, and show vehicle movement to riders.

The service must be crash-consistent and persistent across process restarts, handling GPS pings from vehicles with out-of-order delivery, validation, and geospatial queries.

All code goes under `/app/src/` with a Go module. The CLI binary is `locationctl`.

## CLI Interface
```
locationctl --db <PATH> <command> [args] [flags]
```
- `--db` global flag: path to JSON database file (e.g., `/app/data/locations.json`). If file doesn't exist, start with empty store. Must create parent directories if needed. All writes must be atomic: write to temp file in same directory then rename.

### Data Model
Each vehicle location:
```json
{
  "vehicle_id": "veh_123",
  "lat": 37.7749,
  "lng": -122.4194,
  "timestamp_ms": 1710000000000,
  "accuracy": 5.0,
  "speed": 12.5,
  "heading": 90.0
}
```
- `vehicle_id`: non-empty string, no spaces, max 64 chars, alphanumeric, `_`, `-` allowed. Validation: regex `^[A-Za-z0-9_-]{1,64}$`
- `lat`: float64 in [-90, 90]
- `lng`: float64 in [-180, 180]
- `timestamp_ms`: int64 >=0 milliseconds since epoch
- `accuracy`: float64 >=0 meters, default 10.0 if not provided
- `speed`: float64 >=0 m/s, default 0.0
- `heading`: float64 in [0, 360), default 0.0 (degrees, 0=North, 90=East)

Store per vehicle the **latest** location by timestamp. If incoming timestamp <= stored timestamp, it's stale/out-of-order and must be ignored.

### Commands

#### 1. `update <vehicle_id> <lat> <lng> <timestamp_ms> [--accuracy <float>] [--speed <float>] [--heading <float>]`
- Update vehicle location.
- Validates all fields. On invalid, print error to stderr and exit 2, do not modify DB.
- If timestamp <= existing location's timestamp for that vehicle, print `stale` to stdout and exit 0 (do not update, keep existing).
- On success, atomically persist and print the stored JSON object (single line) to stdout, exit 0.

#### 2. `get <vehicle_id>`
- Print JSON object of vehicle location to stdout, exit 0.
- If vehicle not found, print error to stderr and exit 3.

#### 3. `list`
- Print JSON array of all location objects sorted by `vehicle_id` ascending lexicographic. Empty store -> `[]`. Exit 0.

#### 4. `near --lat <float> --lng <float> --radius <float>`
- Find vehicles within radius meters of given point.
- Radius must be >=0, <= 50000 (50km max). Invalid exit 2.
- Use Haversine formula for distance (Earth radius 6371000 meters).
- Output: JSON array of objects, each object is location object extended with `distance_m` float64, sorted by `distance_m` ascending, then `vehicle_id` ascending as tie-breaker. Only vehicles with distance <= radius included.
- Exit 0 even if empty.

#### 5. `clear`
- Delete all vehicles from DB (truncate). Atomically write empty store. Print `cleared` to stdout, exit 0.

### Persistence Format
DB file is JSON: object mapping vehicle_id -> Location. Example:
```json
{
  "veh_1": {"vehicle_id":"veh_1","lat":37.7,"lng":-122.4,"timestamp_ms":1000,"accuracy":5,"speed":0,"heading":0},
  "veh_2": {...}
}
```
- On startup, if file exists and is valid JSON, load it. If file is empty (0 bytes), treat as empty store.
- If file contains invalid JSON (corrupt), print error to stderr and exit 4.
- Atomic write: create `<db>.tmp.<pid>` in same directory, write JSON, fsync (best effort), rename to `<db>`.
- Concurrency: single-process, but must handle crash mid-write via atomic rename; torn file should not occur because rename is atomic. If DB file is missing, don't error.

### Validation & Exit Codes
- 0: success, or stale update ignored (print `stale`)
- 2: invalid argument / validation error (bad lat/lng/timestamp/vehicle_id/accuracy/speed/heading/radius/missing args)
- 3: vehicle not found for get
- 4: corrupt DB

Constraints:
- Go standard library only, no external deps.
- Must build with `go build -o locationctl .` and `go build ./...` from `/app/src` with no network, GOFLAGS=-mod=mod.
- go.mod must exist at `/app/src/go.mod` with module `locationservice`.
- Haversine formula must be correctly implemented.
- Parent directories creation for DB path.
- Vehicle ID regex strictly enforced.

### Example
```bash
/app/src/locationctl --db /app/data/locations.json update veh_123 37.7749 -122.4194 1710000000000 --accuracy 5 --speed 10 --heading 90
/app/src/locationctl --db /app/data/locations.json get veh_123
/app/src/locationctl --db /app/data/locations.json near --lat 37.7749 --lng -122.4194 --radius 1000
/app/src/locationctl --db /app/data/locations.json list
/app/src/locationctl --db /app/data/locations.json clear
```

### Deliverable
Place full implementation under `/app/src/` including go.mod and package main entry point. Binary must be buildable and run as described.

Tests will drive the CLI over fixed and randomized cases, check persistence, sorting, haversine correctness, stale handling, validation, and exit codes.
