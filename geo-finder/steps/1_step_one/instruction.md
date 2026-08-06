# Step 1: Geofence Lookup Service (Basic)

## Scenario
You are building a geofencing service for a delivery / ride-sharing platform. Operations needs to define geographic zones (polygons) and quickly answer "which zones contain this GPS point?". Examples: downtown, airport, no-parking, surge pricing, delivery area.

You must build a CLI tool `geofencectl` in Go that persists geofences to a JSON file and performs correct point-in-polygon lookups.

All code goes under `/app/src/` with a Go module. The binary must be `geofencectl` buildable via `go build -o geofencectl .` from `/app/src`.

## CLI Interface
```
geofencectl --db <PATH> <command> [args] [flags]
```
- `--db` global flag: path to JSON database file (e.g., `/app/data/geofences.json`). If file doesn't exist, start with empty store. Must create parent directories if needed. All writes must be atomic: write to temp file in same directory then rename (e.g., `<db>.tmp.<pid>`), fsync best effort, then rename.
  Supported forms: `--db /path` and `--db=/path`. If not provided, default to `/app/data/geofences.json`? For this task, require explicit flag: if missing, print error to stderr and exit 2. But to simplify, make it required: if not provided, exit 2.

### Data Model
```go
type Point struct {
  Lat float64 `json:"lat"`
  Lng float64 `json:"lng"`
}

type Geofence struct {
  ID      string  `json:"id"`
  Name    string  `json:"name"`
  Polygon []Point `json:"polygon"`
}
```
- `ID`: non-empty, max 64 chars, regex `^[A-Za-z0-9_-]{1,64}$`. Alphanumeric, underscore, hyphen only.
- `Name`: non-empty string, max 128 chars, trimmed. Cannot be only whitespace.
- `Polygon`: array of Points, at least 3 points, at most 1000 points. Each point:
  - `lat` float64 in [-90, 90]
  - `lng` float64 in [-180, 180]
  No self-intersection check needed, but polygon is considered closed (last edge connects to first). For simplicity, assume simple polygons (may be convex or concave).

### Persistence Format
DB file is JSON object mapping geofence ID -> Geofence:
```json
{
  "zone_1": {"id":"zone_1","name":"Downtown","polygon":[{"lat":37.7,"lng":-122.4},...]},
  "zone_2": {...}
}
```
- On startup, if file exists and valid JSON, load it.
- If file is empty (0 bytes) or does not exist, treat as empty store (0 geofences).
- If file contains invalid JSON (corrupt) or JSON is not an object, print error to stderr and exit 4.
- Atomic write: create temp file in same directory `<db>.tmp.<pid>`, write JSON, rename to `<db>`.
- Concurrency: single-process for this step, but atomic rename guarantees no torn file.

### Commands

#### 1. `add <id> --polygon "<lat1,lng1;lat2,lng2;...>" --name "<name>"`
- Add or overwrite a geofence.
- Args:
  - `<id>` positional: must satisfy ID regex, else exit 2.
  - `--polygon` required: string format `"lat,lng;lat,lng;..."` e.g., `"37.7749,-122.4194;37.8049,-122.4194;37.8049,-122.4094;37.7749,-122.4094"`. Parsing: split by `;`, each part `lat,lng` trimmed, split by `,`, parse floats. At least 3 points, at most 1000. Each lat/lng validated range else exit 2. Empty segments or invalid float -> exit 2.
  - `--name` required: string, trimmed non-empty max 128 chars else exit 2.
- On invalid args/validation, print error to stderr and exit 2, do NOT modify DB.
- On success, atomically persist and print stored Geofence JSON object (single line) to stdout, exit 0. JSON should have fields id, name, polygon in same shape as model. Order of fields not strictly enforced but must be valid JSON and contain those fields.

Example:
```bash
geofencectl --db /app/data/geofences.json add downtown --polygon "37.7749,-122.4194;37.8049,-122.4194;37.8049,-122.4094;37.7749,-122.4094" --name "Downtown SF"
```

#### 2. `remove <id>`
- Delete geofence by ID.
- If ID invalid format -> exit 2.
- If ID not found -> print error to stderr and exit 3.
- Else atomically write empty store without that ID, print `removed` to stdout, exit 0.

#### 3. `list`
- Print JSON array of all geofence objects sorted by ID ascending lexicographic. Empty -> `[]`.
- Exit 0.
- Output must be valid JSON array. Each element is Geofence object.

#### 4. `lookup --lat <float> --lng <float> [--verbose]`
- Find geofences containing point.
- `--lat` required float in [-90,90], `--lng` required in [-180,180]. Invalid/missing -> exit 2.
- `--verbose` optional bool flag: if present, output full Geofence objects instead of just IDs.
- Algorithm: point-in-polygon ray casting. Must treat points on edge or vertex as **inside**.
  Pseudocode for correctness:
  - First check if point lies on any polygon edge (segment) within epsilon 1e-9 degrees: if distance to segment < 1e-9 and projection within segment bounds -> inside.
  - Else ray casting: shoot ray to +infinity in +lng (east) direction, count crossings. For each edge (p1->p2) where y = lat, x = lng:
    Condition: (p1.lat > lat) != (p2.lat > lat) and lng < (p2.lng - p1.lng)*(lat - p1.lat)/(p2.lat - p1.lat) + p1.lng
    Toggle inside flag on crossing.
  - Return inside if odd crossings.
- Must work for concave polygons, not just convex.
- Output: if not --verbose: JSON array of matching IDs sorted ascending lexicographic. Example `["downtown","zone_2"]`. If --verbose: JSON array of matching Geofence objects sorted by ID ascending.
- Exit 0 even if empty (print `[]`).

#### 5. `clear`
- Delete all geofences, atomically write empty JSON object `{}`, print `cleared` to stdout, exit 0.

### Validation & Exit Codes
- 0: success
- 2: invalid argument / validation error (bad ID, name, lat/lng, polygon format, missing flags, etc.)
- 3: not found (remove non-existent ID)
- 4: corrupt DB file

### Constraints
- Go standard library only, no external dependencies. `go.mod` must not have external deps beyond stdlib.
- Must build with `go build -o geofencectl .` and `go build ./...` from `/app/src` with GOFLAGS=-mod=mod, no network.
- go.mod must exist at `/app/src/go.mod` with module `geofence` (or any name, but `geofence` preferred).
- Parent directories creation for DB path is required.
- Atomic write via temp file + rename is required.
- Point on edge must be considered inside (tests include edge cases).
- Sorting of list and lookup results by ID ascending is required for deterministic output.

### Example Flow
```bash
/app/src/geofencectl --db /tmp/g.json add zone_a --polygon "0,0;0,1;1,1;1,0" --name "Square A"
# -> {"id":"zone_a","name":"Square A","polygon":[...]}

./geofencectl --db /tmp/g.json list
# -> [{"id":"zone_a","name":"Square A","polygon":[...]}]

./geofencectl --db /tmp/g.json lookup --lat 0.5 --lng 0.5
# -> ["zone_a"]

./geofencectl --db /tmp/g.json lookup --lat 2 --lng 2
# -> []

./geofencectl --db /tmp/g.json remove zone_a
# -> removed

./geofencectl --db /tmp/g.json clear
# -> cleared
```

### Deliverable
Place full implementation under `/app/src/` including `go.mod` and package main entry point. Binary must be buildable and runnable as described.

Tests will cover: add/list/remove/clear persistence, atomic mkdir, sorting, polygon parsing validation, ID/name validation, point-in-polygon correctness (inside/outside/edge/vertex/concave/overlapping), lookup with multiple matches, corrupt DB handling, empty DB handling, error codes.
