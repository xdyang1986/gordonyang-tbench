# Step 1: Geofence Lookup Service (Hard)

## Scenario
You are building a geofencing service for a delivery / ride-sharing platform. Operations needs to define geographic zones (polygons) and quickly answer "which zones contain this GPS point?". Examples: downtown, airport, no-parking, surge pricing, delivery area.

You must build a CLI tool `geofencectl` in Go that persists geofences to a JSON file and performs correct point-in-polygon lookups with strict validation.

All code goes under `/app/src/` with a Go module. The binary must be `geofencectl` buildable via `go build -o geofencectl .` from `/app/src`.

## CLI Interface
```
geofencectl --db <PATH> <command> [args] [flags]
```
- `--db` global flag: path to JSON database file (e.g., `/app/data/geofences.json`). If file doesn't exist, start with empty store. Must create parent directories if needed. All writes must be atomic: write to temp file in same directory then rename (e.g., `<db>.tmp.<pid>`), fsync best effort, then rename. Temp file must be removed after rename and must not be left behind on success. On failure, do not leave corrupt DB.
  Supported forms: `--db /path` and `--db=/path`. If not provided, print error to stderr and exit 2.

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
- `ID`: non-empty, max 64 chars, regex `^[A-Za-z0-9_-]{1,64}$`.
- `Name`: non-empty string, max 128 chars, trimmed. Cannot be only whitespace.
- `Polygon`: array of Points, at least 3 points, at most 1000 points. Each point lat in [-90,90], lng in [-180,180]. Polygon is considered closed (last edge connects to first). Must be simple (no self-intersection) and have non-zero area.

### Strict Polygon Validation (Hard Requirements)
On `add`, after basic parsing and range checks, you must also enforce:

1. **No empty segments**: Polygon string format `"lat,lng;lat,lng;..."` split by `;`. If any segment after trimming is empty (e.g., consecutive `;;`, leading `;`, trailing `;`), reject with exit 2. Example `"0,0;;0,1;1,1"` or `"0,0;0,1;1,1;1,0;"` is invalid.

2. **No duplicate points**: Polygon must not contain duplicate points with numerically equal lat and lng (e.g., `0,0` and `0.0,0.0` are duplicates). If any two points have identical coordinates, reject exit 2. This includes duplicate first/last point – you should not require explicit closure; closure is implicit. So `"0,0;0,1;1,1;1,0;0,0"` contains duplicate `0,0` and must be rejected.

3. **Non-zero area**: Polygon area computed via shoelace must have absolute area > 1e-9 (not colinear or degenerate). If degenerate (all points colinear or collapsed), reject exit 2.

4. **Self-intersection**: Polygon must be simple – no two non-adjacent edges may intersect, including colinear overlapping segments. Adjacent edges share a vertex and are allowed to meet at that vertex. If self-intersecting (e.g., bow-tie `"0,0;1,1;0,1;1,0"` or colinear overlap `"0,0;0,2;0,1;1,1;1,0"` where `0,0-0,2` overlaps `0,2-0,1`), reject exit 2. Implement correct segment intersection with orientation and on-segment checks, handling colinear cases. Complexity O(n^2) is acceptable for n<=1000.

Validation uses the coordinates exactly as given. Checks 2, 3 and 4 run on the raw parsed lat/lng values, before any longitude normalisation or antimeridian unwrapping. For validation, -180 and 180 are distinct longitudes. Consequently the world rectangle "-90,-180;-90,180;90,180;90,-180" contains no duplicate points and has shoelace area 64800 — it is a valid polygon and must be accepted.

If any validation fails, print error to stderr, do NOT modify DB (including overwrite attempts – old entry must survive), do not leave temp files, exit 2.

### Persistence Format
DB file is JSON object mapping ID -> Geofence:
```json
{
  "zone_1": {"id":"zone_1","name":"Downtown","polygon":[{"lat":37.7,"lng":-122.4},...]},
}
```
- On startup, if file exists and valid JSON, load it.
- If file is empty (0 bytes) or does not exist, treat as empty store.
- If file contains invalid JSON (corrupt) or JSON is not an object (e.g., array), print error to stderr and exit 4.
- Atomic write: create temp file in same directory `<db>.tmp.<pid>`, write JSON, fsync, rename. Ensure no temp files left behind on success.
- Concurrency: single-process for this step.

### Commands

#### 1. `add <id> --polygon "<lat1,lng1;...>" --name "<name>"`
- Add or overwrite a geofence after full validation (ID, name, polygon parsing, duplicate, area, self-intersection).
- On success, atomically persist and print stored Geofence JSON object (single line) to stdout, exit 0.

#### 2. `remove <id>`
- Delete by ID. Invalid format -> exit 2. Not found -> stderr + exit 3. Else print `removed`, exit 0. Ensure atomic write and no temp leftover.

#### 3. `list`
- Print JSON array of all geofence objects sorted by ID ascending lexicographic. Empty store must return an empty JSON array `[]`, not `null`. All empty array outputs must be `[]`, not `null` — in Go use non-nil empty slices (`make([]T,0)` or `[]T{}`) so `json.Marshal` produces `[]`, not `null`.

#### 4. `lookup --lat <float> --lng <float> [--verbose]`
- Find geofences containing point.
- lat in [-90,90], lng in [-180,180], else exit 2.
- Points on edge or vertex are considered **inside** (epsilon 1e-9). Must handle horizontal edges, vertices, concave polygons correctly.
- Longitude classification. Wrapping affects the point-in-polygon test and any bounding box, never validation. Classify each polygon once from its raw span maxLng - minLng:
  - span ≥ 360 — covers every longitude (e.g. the world rectangle -180…180). Not antimeridian-crossing: it matches any point inside its latitude band, at every longitude.
  - 180 < span < 360 — crosses the antimeridian (e.g. 179 … -179, span 358). Unwrap longitudes to a continuous interval; a point is outside only if it falls in the large gap, not the small wrapping interval.
  - span ≤ 180 — ordinary polygon, no wrapping.

Classify world-spanning before applying the crossing rule. The CLI must give the same answers the HTTP service is required to give in step 2.
- Output: default JSON array of matching IDs sorted asc. Must be `[]`, not `null`, when no matches. `--verbose` → JSON array of matching Geofence objects sorted by ID asc, must be `[]` not `null` when empty.
- Exit 0 even if empty (print `[]`, not `null`).
- Performance: With 500 geofences each up to 100 points, lookup should complete in <200ms via CLI (tests will measure). Implement bounding-box prefilter even for CLI to meet this.

#### 5. `clear`
- Delete all, atomically write `{}`, print `cleared`, exit 0.

### Exit Codes
- 0 success, 2 invalid arg/validation, 3 not found, 4 corrupt DB

### Constraints
- Go stdlib only, no external deps.
- Must build with `go build -o geofencectl .` from `/app/src`.
- Parent dir creation required.
- Atomic write via temp file + rename required.
- Sorting deterministic required.
- No leftover temp files.
- Self-intersection and duplicate detection required.
- All JSON array outputs must be valid JSON arrays, and empty arrays must be `[]`, not `null` (use non-nil empty slices in Go).

### Deliverable
Place full implementation under `/app/src/`.

Tests cover: persistence, atomic, sorting, polygon parsing (including empty segment, duplicate, degenerate, self-intersection), ID/name validation, point-in-polygon (inside/outside/edge/vertex/horizontal/concave/overlapping/world bounds), corrupt/empty DB, error codes, temp file cleanup, CLI performance with 500 geofences.
