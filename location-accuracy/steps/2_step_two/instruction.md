# Step 2: Improve Location Accuracy to Avoid Inaccurate Pickup/Dropoff

## Context
You completed Step 1: basic vehicle tracking with update/get/list/near/clear. The file `/app/src/` already contains a working `locationctl` binary.

In production Uber-like system, raw GPS is noisy:
- Urban canyoning causes 50-100m jumps
- Low accuracy fixes (>100m) lead to driver showing on wrong street
- Stale locations (vehicle app backgrounded) cause inaccurate ETA and pickup on wrong side
- Riders complain: driver appears 200m away but is actually in front, or driver marked as arrived but is still around corner
- Need snap-to-road to align vehicle to road network

Business requires **pickup/dropoff accuracy improvements**: filtering outliers, rejecting low-accuracy fixes, detecting stale data, snapping to road, and motion-aware estimation for validation.

You must evolve the same CLI (`locationctl`) to add accuracy features, preserving Step 1 compatibility.

## Updated Data Model
Extend stored location to include history and optional snap info. Keep backward compatible reading of Step1 DB files (which had no history). On load, if old format without `history`, convert to new format with history containing just current location.

New model per vehicle:
```json
{
  "vehicle_id": "veh_123",
  "lat": 37.7749,
  "lng": -122.4194,
  "timestamp_ms": 1710000000000,
  "accuracy": 5.0,
  "speed": 12.5,
  "heading": 90.0,
  "history": [
    {"lat":37.7748,"lng":-122.4193,"timestamp_ms":1710000000000,"accuracy":5,"speed":10,"heading":90},
    ...
  ]
}
```
- `history`: last up to 5 locations for this vehicle sorted by timestamp ascending (oldest first, newest last includes current). When updating, push new location and trim to 5.
- For simplicity, history entries can be minimal object with lat,lng,timestamp_ms,accuracy,speed,heading, but you may store full.

Persistence: still JSON map vehicle_id -> extended location. Must still be readable if old file without history exists (auto-migrate).

## Enhanced Update Logic (must replace Step1 logic)

#### A. Low Accuracy Filter
- If `accuracy` > 100.0 meters, reject as low_accuracy: print `low_accuracy` to stdout and exit 3, do NOT update DB.
- Applies to update command.

#### B. Outlier / Teleport Detection
- If vehicle already has a location, compute:
  - `dt = (new_ts - old_ts) / 1000.0` seconds (must be >0 because out-of-order already filtered)
  - `distance = haversine(old.lat, old.lng, new.lat, new.lng)` meters
  - `implied_speed = distance / dt`
- If `dt < 300` seconds (5 min) AND `distance > 1000` meters AND `implied_speed > 50` m/s (~180 km/h) AND `old.accuracy < 50` AND `new.accuracy < 50`, then consider it an outlier/teleport (GPS glitch):
  - Print `outlier` to stdout and exit 3, do NOT update.
- Otherwise proceed.
- Note: if `dt <=0` already handled as stale case.

#### C. Speed Sanity Cap
- If `speed` field > 50 m/s, reject: print `invalid_speed` or treat as validation error exit 2? For this task, treat speed >50 as invalid argument exit 2.
- Actually keep validation: speed must be >=0 and <=50, else exit 2.

#### D. History Maintenance
- On successful update, maintain history: append previous current location to history if exists, then push new current, keep only last 5 entries sorted by timestamp ascending.

## New & Enhanced Commands

### Existing commands must still work with new logic and new exit codes:
- `update`: now may exit 3 for low_accuracy/outlier (previously only 0,2). Still exit 0 stale.
- `get`, `list`, `near`, `clear` same but `list` and `get` output now includes history? To preserve backward compat, `get` and `list` should output base Location object WITHOUT history field by default (or include it optional). For simplicity, output base fields only (vehicle_id,lat,lng,timestamp_ms,accuracy,speed,heading) as before, to keep Step1 tests passing if run again. History is internal but may be optionally revealed via `--verbose` flag for debugging. Definition: `get` without `--verbose` prints base JSON (6 fields). With `--verbose` prints extended including history and snap info if available.
- `near` same as before but now **must exclude stale vehicles** unless `--include-stale` flag provided, **but only when `--now` is provided**. Stale definition: vehicle location age > 30000 ms relative to `--now` timestamp. For `near`, add optional flag `--now <timestamp_ms>` (int64). If `--now` is provided, compute `age = now - vehicle.timestamp_ms`; if age >30000, consider stale and exclude unless `--include-stale`. If `--now` is not provided, do NOT apply staleness filtering (include all vehicles regardless of age) to preserve Step1 backward compatibility. Tests that need staleness filtering will always provide `--now`.
- `near` still outputs JSON array with distance_m, sorted.

### 1. `estimate <vehicle_id> [--now <timestamp_ms>] [--roads <path>]`
- Estimates accurate current location for a vehicle, returning JSON:
```json
{
  "vehicle_id": "veh_123",
  "lat": 37.7749,
  "lng": -122.4194,
  "timestamp_ms": 1710000000000,
  "accuracy": 5.0,
  "speed": 12.5,
  "heading": 90.0,
  "confidence": "high",
  "age_ms": 5000,
  "snapped": false,
  "road_id": "",
  "predicted": false,
  "original_lat": 37.7749,
  "original_lng": -122.4194
}
```
- Logic:
  - If vehicle not found -> stderr, exit 3.
  - Determine `now`: if --now provided use it, else time.Now().UnixMilli().
  - `age_ms = now - location.timestamp_ms`. Can be negative if now < timestamp (future ping) -> treat age as 0.
  - Smoothing: If history has >=2 points, compute weighted average using inverse accuracy weighting of last up to 3 points (including current). Weighted avg lat = sum(lat_i / accuracy_i) / sum(1/accuracy_i) if accuracy>0 else simple average. Same for lng. Use this smoothed position as base (if history available). Otherwise use stored location.
  - Prediction: If `age_ms` >0 and <=30000 and speed >0, predict forward position based on speed, heading, dt:
    - distance = speed * dt (dt in seconds = age_ms/1000)
    - Use equirectangular approximation for small distances:
      - R = 6371000
      - delta_lat = (distance * cos(heading_rad)) / R * (180/pi)
      - delta_lng = (distance * sin(heading_rad)) / (R * cos(lat_rad)) * (180/pi)
    - Heading rad = heading * pi/180, 0=North, 90=East
    - predicted lat = base_lat + delta_lat, lng = base_lng + delta_lng
    - Set `predicted=true` if prediction applied (age>0), else false.
    - If predicted, accuracy degrades: new_accuracy = original_accuracy + (age_ms/1000)*0.5 (drift 0.5m per sec) capped.
  - Road Snapping: If --roads provided, load roads file (JSON array of segments, see environment Dockerfile /app/data/roads.json format). Each road: `{"id": string, "start": {"lat":float,"lng":float}, "end": {"lat":float,"lng":float}}`. Find nearest segment to estimated/predicted location. Compute point-to-segment distance using local Cartesian approximation:
    - Convert lat/lng to meters around reference point: use equirectangular: x = R * lng_rad * cos(lat_ref), y = R * lat_rad. Use location lat as lat_ref for conversion.
    - Compute closest point on segment (clamped) and distance.
    - If minimal distance <= 50 meters, snap: set lat/lng to closest point's lat/lng converted back, set snapped=true, road_id=nearest road id, store original_lat/original_lng as pre-snap. If no road within 50m, snapped=false, road_id="", keep original_lat/original_lng equal to final lat/lng or keep original before snap.
  - Confidence:
    - high: accuracy <=10 and age_ms <=10000 and snapped==true OR accuracy <=10 and age <=10000
    - medium: accuracy <=25 and age <=20000
    - low: otherwise
    - Implementation: if accuracy <=10 && age_ms <=10000 -> high, else if accuracy <=25 && age_ms <=20000 -> medium, else low. If snapped, upgrade one level? Simplified: if snapped && confidence==medium -> high? Or keep simple. For task, require: high when accuracy <=10 and age <=10000, medium when accuracy <=25 and age <=20000, else low. If snapped and distance to road <10m and original confidence not low, upgrade: medium->high, low->medium.
  - Output JSON with fields described. Ensure lat/lng are final estimated (snapped if applied). original_lat/original_lng are before snap but after smoothing+prediction.
  - Exit 0.

### 2. `validate-pickup <vehicle_id> <pickup_lat> <pickup_lng> [--now <timestamp_ms>] [--roads <path>]`
- Validates if vehicle's current (estimated) location is accurate enough for pickup.
- Steps:
  - Compute estimated location via same logic as estimate command (including prediction and optional road snap if --roads provided).
  - If vehicle not found -> exit 3.
  - Check in order:
    1. stale: if age_ms > 30000 => valid=false, reason="stale"
    2. low_accuracy: if estimated accuracy >50 => valid=false, reason="low_accuracy"
    3. too_far: distance between estimated vehicle location and pickup point >100 meters => valid=false, reason="too_far"
    4. else valid=true, reason="ok"
  - Output JSON:
```json
{
  "valid": true,
  "reason": "ok",
  "distance_m": 15.5,
  "confidence": "high",
  "age_ms": 5000,
  "accuracy": 5.0,
  "snapped": true,
  "vehicle_lat": 37.7749,
  "vehicle_lng": -122.4194
}
```
  - distance_m is haversine between estimated vehicle location and pickup point.
  - vehicle_lat/lng is estimated final location.
  - Exit 0 if valid, exit 1 if invalid (but still prints JSON), exit 3 if vehicle not found, exit 2 if invalid args (bad lat/lng etc).

### 3. `validate-dropoff <vehicle_id> <dropoff_lat> <dropoff_lng> [--now <timestamp_ms>] [--roads <path>]`
- Same logic as validate-pickup but thresholds: distance >150 meters considered too_far for dropoff (more lenient), stale same 30s, low_accuracy >50 same.
- Output same JSON shape, reason values same.
- Exit codes same.

### Road Data
- File `/app/data/roads.json` provided in environment. Optionally user may provide different path via --roads.
- Format as described. Must handle missing/invalid file: if --roads provided and file cannot be read or invalid JSON, exit 2.

### CLI Details:
- Keep `--db` global flag.
- For new commands, `--now` optional int64, must be >=0 else exit 2.
- `--roads` optional string path.
- All lat/lng validation same as before: lat [-90,90], lng [-180,180].
- Accuracy must be >=0 and <=100 for update? Actually we reject >100 as low_accuracy exit3, not validation 2. For estimate/validate, accuracy from stored data may be any but confidence reflects it.

### Exit Codes Extended:
- 0: success (including valid pickup)
- 1: validation logic says invalid (pickup too_far/stale/low_accuracy)
- 2: invalid argument
- 3: vehicle not found, or low_accuracy/outlier rejected from update (to distinguish)
- 4: corrupt DB

### Backward Compatibility:
- Step1 tests would still need to pass if they were rerun, but since this is Step2 inheriting prior session, Step1 DB may exist. Your implementation must auto-migrate old DB without history: on load, if entry lacks history, set history=[current location] or empty then add.

### Example Flow:
```bash
# Step1 behavior still works
locationctl --db /tmp/db.json update veh1 37.7749 -122.4194 1710000000000 --accuracy 5 --speed 10 --heading 90
locationctl --db /tmp/db.json get veh1

# Step2 filtering
locationctl --db /tmp/db.json update veh1 38.7749 -123.4194 1710000010000 --accuracy 5  # >100km jump in 10s -> outlier, rejected
locationctl --db /tmp/db.json update veh1 37.7750 -122.4195 1710000005000 --accuracy 150 # low_accuracy reject

# Estimation with prediction + snap
locationctl --db /tmp/db.json estimate veh1 --now 1710000008000 --roads /app/data/roads.json

# Pickup validation
locationctl --db /tmp/db.json validate-pickup veh1 37.7749 -122.4194 --now 1710000008000 --roads /app/data/roads.json
# Returns {"valid": true, "reason":"ok", ...} if within 100m, accurate and fresh
```

### Deliverable
Extend `/app/src/` implementation to support new filtering, estimation, road snapping, and validation commands. Must still compile via `go build ./...` and `go build -o locationctl .`. All standard library only.

Tests will verify: outlier rejection, low_accuracy rejection, stale detection, road snapping within 50m, estimation with prediction, confidence scoring, pickup/dropoff validation success/failure, backward compatibility with Step1 DB, and enhanced near with --now and --include-stale.
