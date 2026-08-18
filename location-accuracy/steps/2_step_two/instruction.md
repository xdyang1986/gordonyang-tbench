# Step 2: Improve Location Accuracy

## Context

You have completed Step 1, which implemented a full vehicle location service with zones (polygon with holes, circles, time windows, antimeridian crossing, edge-inside), mixed roads (polyline and segment), batch atomic operations with variable fields, stale handling, and history tracking of the last 10 locations. The working binary is in `/app/src/` as `locationctl`.

Production still sees noisy GPS signals: urban canyon jumps, teleportation, heading flips, acceleration spikes, accuracy spikes, off-road positions, stale timestamps, wrong street assignments, and moving vehicles incorrectly marked as arrived.

Your task is to evolve the same CLI while preserving complete backward compatibility with Step 1, including all validation rules, zone handling, road handling, and batch semantics. You will add outlier filtering, low-accuracy filtering, EMA smoothing, position prediction, heading-aware road snapping, confidence scoring, and pickup/dropoff validation.

## Data Model Extension

Extend the location record with `outlier_count`:

```json
{"vehicle_id":"veh_123","lat":...,"lng":...,"timestamp_ms":...,"accuracy":5,"speed":...,"heading":...,"total_distance_m":...,"history":[...10 asc including current...],"outlier_count":2}
```

- `history` must contain up to 10 accepted locations sorted by timestamp ascending, including the current location as the last entry.
- `outlier_count` counts the number of rejected outliers for that vehicle. It must persist even when the location update is rejected.
- `total_distance_m` is the sum of Haversine distances (R=6371000) over accepted updates only.
- An old database file that lacks `history`, `total_distance_m`, or `outlier_count` must be auto-migrated: treat missing history as empty, missing distance as 0, and missing outlier_count as 0. Whitespace-only files are empty stores, while an array `[]` or literal `null` is corrupt with exit 4 and a backup file `<db>.corrupt.<nanosec>` containing the original content.

Importantly, `outlier_count` must survive a process restart. It is persisted in the DB file, so after a process exits, a new process loading the same DB must still see the count via `get --verbose`. Tests will verify this across separate CLI invocations.

## Enhanced Update Logic

The update pipeline now has additional checks that run before zone validation and history update. The order matters.

### A. Low Accuracy Filter (runs before zones)

If `accuracy > 100`, print `low_accuracy` (stdout preferred, stderr accepted for fairness) and exit 3. Do not change the database, and do not change `outlier_count`. This separation is load-bearing: low_accuracy rejections must not increment outlier_count.

### B. Speed Cap

Speed above 50 m/s is invalid and must exit 2, same as Step 1.

### C. Outlier Detection – Six Conditions

Compute `dt = (new_timestamp - old_timestamp) / 1000` seconds. If dt <= 0, the stale case has already been handled. Compute `distance` as Haversine between new and old locations, and `implied = distance / dt`.

If any of the six conditions below is true, the update is an outlier: increment `outlier_count` for that vehicle and persist it even though the location is not updated, print `outlier` and exit 3, and do not modify location, total_distance, or history.

All six conditions must be implemented, and thresholds matter:

1. **Teleport**: a large jump in short time with good accuracy: `dt < 300 && distance > 1000 && implied > 50 && old.accuracy < 50 && new.accuracy < 50`.

2. **Heading Flip**: both old and new speeds > 10 m/s, angular difference between headings > 120 degrees, but distance < 500m. This is an unrealistic flip.

3. **Median Deviation**: if history has at least 2 prior points, compute implied speeds for the last up to 3 transitions in history (history[i] -> history[i+1]). Take the median `med`. If `|implied - med| > 30 && implied > 30 && distance > 500`, treat as outlier.

4. **Acceleration Spike**: `|new.speed - old.speed| / dt > 15 && distance < 300`. Impossible acceleration.

5. **Accuracy Spike**: `new.accuracy > 75 && new.accuracy > old.accuracy * 2 + 30`.

6. **Speed vs Implied Mismatch**: `implied > 80 && new.speed < 2 && distance > 1000 && dt < 60`. Reports stopped but actually moved far.

If multiple conditions are true for the same update, it must still count as only one outlier increment.

`outlier_count` must not be incremented for low_accuracy or stale rejections — only for these six outlier conditions.

### D. Zones

Same as Step 1 extreme (polygon with holes, circles, time windows, antimeridian unwrapping, edge/vertex inside). Same out_of_zone handling: output contains `out_of_zone` (stdout/stderr accepted) and exit 3.

### E. History and Distance

Same as Step 1, but total_distance must not be increased on outlier, low_accuracy, stale, or out_of_zone rejections. This applies to both the direct `update` path and the `batch` path. History must not include rejected attempts either.

### F. Batch – Enhanced for Step 2 (explicit)

Batch inherits Step 1 atomicity (all-or-nothing, zones check before stale, applied excludes skipped). In Step 2, batch must also handle the new filters with same semantics as `update`:

- Parse all lines first. Validate vehicle_id, lat, lng, timestamp, accuracy, speed, heading same as update.
- Low accuracy filter runs **before** zones (same order as update pipeline: low_accuracy → zones → stale → outlier). If `accuracy > 100`, that operation is treated as rejected: skip location update, do not count as applied, do not add distance, do not include in history, `outlier_count` unchanged, batch continues.
- Zones check: if default `/app/data/zones.json` exists, check each update op per its own timestamp (same as Step1 batch). If any would be out_of_zone, fail whole batch exit 2 with no DB change, even if that op would be stale/low_accuracy/outlier (zones check still applies before stale, per L92, and low_accuracy is before zones per pipeline A).
- Stale check: if op ts <= stored timestamp, skip without counting as applied, no distance/history change.
- Outlier detection: six conditions same as update. If outlier, increment `outlier_count` persistently for that vehicle, skip location update, do not count as applied, do not add distance/history, batch continues.
- Delete ops: 2 fields exactly, remove if exists and count as applied.
- After validation, apply sequentially to simulated state with single atomic write, print `batch_ok <applied>` where applied excludes stale, low_accuracy, and outlier rejections.

Spec explicitly states: distance not added on outlier/low_accuracy/stale/out_of_zone for both direct update and batch paths.

## Roads – Polyline plus Heading-Aware Snapping (No Fallback is Load-Bearing)

Roads file has mixed formats: each entry is either `{"id":"...","points":[... at least 2 ...]}` as polyline, or `{"id":"...","start":{...},"end":{...}}` as legacy segment. Any invalid entry must cause exit 2 when that roads file is used.

For each road and each segment `points[i] -> points[i+1]`, convert to equirectangular projection with latitude reference set to the vehicle latitude, R=6371000, find closest point clamped with t in [0,1], compute Euclidean distance in projected space. Track the best overall.

Compute road bearing via initial bearing formula.

Heading-aware filtering: when `headingAware` is true (used for `estimate` and `validate-pickup/dropoff` when `--roads` is provided and vehicle speed > 1), a candidate road is filtered out if `min(angularDiff(heading, roadBearing), angularDiff(heading, roadBearing+180)) > 45`. If all candidates are filtered out, the result is not snapped — you must NOT fallback to non-heading-aware snapping.

If best distance <= 50m, snapped is true with lat/lng set to snapped point, plus road_id, road_bearing, road_dist_m. Otherwise not snapped.

## Commands

All Step 1 commands must continue to work.

### estimate <vehicle_id> [--now <ts> --roads <path>]

- Not found must exit 3, invalid roads file must exit 2.
- `now` handling: if `--now` is provided use it, otherwise use current time `time.Now().UnixMilli()`. Compute `age = now - timestamp_ms`, if negative set to 0.
- **EMA smoothing**: if history has at least 2 entries, take last up to 5 entries, weight each by `w = (1/(accuracy+1)) * exp(-age_i/10000)` where `age_i = now - history_i.timestamp_ms`. Weighted average lat/lng is the smoothed base position. If history has 1 or 0 entries, smoothed = latest stored location.
- **Prediction**: accuracy always degrades by `+0.5 * age_sec` where `age_sec = age/1000` regardless of predicted flag. If `age > 0 && age <= 30000 && speed > 0`, prediction is true: `dist = speed * age_sec`, `delta_lat = dist * cos(heading_rad) / R * 180 / pi`, `delta_lng = dist * sin(heading_rad) / (R * cos(lat_rad)) * 180 / pi`, where heading_rad = heading * pi/180, lat_rad = smoothed lat * pi/180, R=6371000. Predicted position = smoothed + delta. Otherwise predicted false and predicted position = smoothed.
- **Road snapping**: if `--roads` is given, for each road and each segment points[i]->points[i+1] (or start/end for legacy), convert to equirectangular projection with lat_ref = vehicle latitude (or predicted latitude if predicted path), R=6371000, find closest point clamped t in [0,1], track best overall by Euclidean distance in projected space. Compute road bearing via initial bearing formula. Heading-aware filtering: when `headingAware` is true (used for `estimate` and `validate-pickup/dropoff` when `--roads` is provided and vehicle speed >1), filter out candidate if `min(angularDiff(heading, roadBearing), angularDiff(heading, roadBearing+180)) > 45`. If all candidates filtered, result not snapped – you must NOT fallback to non-heading-aware. If best distance <=50m, snapped true with lat/lng = snapped point, plus road_id, road_bearing, road_dist_m. Otherwise not snapped.
- **Original vs Final (clarified – this was ambiguous before):**
  - `original_lat` / `original_lng` is always the **smoothed base position BEFORE prediction and BEFORE snapping** when the final result is **not snapped**. This means for un-snapped path: original = smoothed (even when predicted true).
  - When the final result **is snapped**, `original_lat` / `original_lng` is the **position BEFORE snapping** (i.e., predicted position if predicted true, otherwise smoothed base). This lets you see what was snapped.
  - `lat` / `lng` (final) is after prediction and after snapping: if snapped → snapped point, else if predicted → predicted position (smoothed+delta), else → smoothed base.
  - So:
    - Not predicted, not snapped: original = smoothed, final = smoothed (same)
    - Predicted, not snapped: original = smoothed, final = smoothed+delta (different, lat != original)
    - Not predicted, snapped: original = smoothed, final = snapped
    - Predicted, snapped: original = smoothed+delta (predicted), final = snapped
  - This matches reference implementation and makes `test_estimate_prediction_exact_delta_north_v2` (un-snapped) expect `lat > original_lat` for north heading.
- **Confidence**: compute base confidence:
  - high if `(acc <= 5 && age <= 5000)` OR `(acc <= 10 && age <= 10000)`
  - medium if `acc <= 25 && age <= 20000`, otherwise low
  - If snapped and road_dist <= 10: upgrade medium to high if acc <=25, upgrade low to medium if acc <=40 and age <=15000
  - Demotion by outlier_count: if `outlier_count > 2`, high demotes to medium. If `outlier_count > 5`, final confidence is low regardless. Boundaries matter: exactly 3 outliers must demote high to medium, exactly 2 must stay high. Exactly 6 outliers must be low, exactly 5 must be medium (if other signals allow).
  - If age > 30000, confidence is low.
  - If accuracy > 50, confidence is low.
  - If not snapped and acc > 25 and age > 10000 and (acc > 40 or age > 15000), confidence is low.

Output JSON with fields: vehicle_id, lat, lng, timestamp_ms, accuracy, speed, heading, total_distance_m, confidence, age_ms, snapped, road_id, road_bearing, road_dist_m, predicted, original_lat, original_lng. Exit 0.

### validate-pickup <vehicle_id> <pickup_lat> <pickup_lng> [--now <ts> --roads <path> --zones <path>]

1. Estimate vehicle position via same EMA, prediction, and heading-aware snap logic as `estimate`.
2. Snap pickup point to nearest road without heading-aware filtering (to get pickup_road_id) if roads provided.
3. Zones: if `--zones` provided, check if pickup point is inside any active zone (active filtering by now if provided, else all). If zones file is not provided, check default `/app/data/pickup_zones.json` if it exists and is non-empty. If pickup is outside and active zones exist, valid false with reason `out_of_geofence` and exit 1. Reason `ok` must be exactly the literal string "ok" when valid.

Priority order for validation (first matching reason wins):

1. out_of_geofence (if pickup outside active geofence and zones exist)
2. stale if estimate age > 30000
3. low_accuracy if estimate accuracy > 50
4. off_road if roads provided and vehicle not snapped
5. moving if vehicle speed >=5
6. road_mismatch if both vehicle and pickup snapped but road_ids differ
7. heading_mismatch if same road, vehicle speed >1, bearing from vehicle to pickup has angular diff >90 and distance >10
8. too_far if Haversine distance vehicle->pickup >100
9. otherwise ok, valid true

Output JSON: valid (bool), reason (string), distance_m, confidence, age_ms, accuracy, snapped, road_id, pickup_road_id, vehicle_lat, vehicle_lng, plus `dropoff_road_id` alias containing same value as pickup_road_id for compatibility with dropoff command. Exit 0 if valid, 1 if invalid, 3 if not found, 2 if invalid args/zones/roads/lat/lng.

### validate-dropoff <vehicle_id> <dropoff_lat> <dropoff_lng> [--now <ts> --roads <path> --zones <path>]

Same logic as pickup but more lenient: moving threshold >=10 (instead of 5), too_far >150 (instead of 100), and zones default file is `/app/data/dropoff_zones.json`.

### geofence-check, near, list, track, etc.

Same as Step 1 extreme behavior.

## Persistence and Crash Consistency

- Same atomic write pattern as Step 1: temp file `<db>.tmp.<pid>` + rename, no leftover, cleanup stale tmp files, including BOM and trailing comma corrupt.
- On corruption (invalid JSON, array, null, truncated file, BOM, trailing comma), create backup `<db>.corrupt.<nanosec>` with integer nanosec suffix, then exit 4.
- Stale tmp files must be ignored on load and cleaned on next successful write.

## Robustness (batch2, for too-easy hardening)

- Help variants with equals: `--help=true`, `-h=true`, `help=true` → print help containing `update,get,list,near,track,distance,delete,stats,batch,clear,estimate,validate-pickup,validate-dropoff,geofence-check` exit0. Unknown with equals `--unknown=true` exit2.
- Output keys exact: estimate must contain exactly `vehicle_id,lat,lng,timestamp_ms,accuracy,speed,heading,total_distance_m,confidence,age_ms,snapped,road_id,road_bearing,road_dist_m,predicted,original_lat,original_lng` plus optional `outlier_count`. Pickup/dropoff must contain at least `valid,reason,distance_m,confidence,age_ms,accuracy,snapped,road_id,pickup_road_id`.
- Batch empty input → `batch_ok 0`.
- Zones file: empty array `[]` allows all, top-level non-array (string, number, null, bool, object) invalid exit2.
- Flag order: `--db` may appear after command (e.g. `update veh 0 0 1000 --db path`) must work.
- Large scale: 1000 batch <5s, 500 near <3s, 200 vehicles estimate 50 queries <5s.
- Outlier boundaries isolated: test teleport dt 300 not outlier, distance 1000, implied 50, old accuracy 50, new accuracy 50, heading 120, speed 10, dist 500, accel 15, dist 300, accuracy 75, old*2+30, implied 80, speed 2 – each must avoid overlapping conditions (set speed 10 to avoid speed-vs-implied, accuracy 60 to avoid teleport).
- Estimate original_lat 4 cases exhaustive must hold: not predicted not snapped original==final==smoothed, predicted not snapped original==smoothed != final, not predicted snapped original==smoothed, predicted snapped original==predicted before snapping.
- EMA last 5 only with small increments 0.0001 to avoid outlier, prediction east/north/south delta exact, heading-aware 45 boundary snaps, 46 on north-south road filtered.
- Batch zones before low_accuracy still fails: batch with low_accuracy out_of_zone must exit2 (zones check before low_accuracy in batch), while update low_accuracy out_of_zone returns low_accuracy exit3 (low_accuracy before zones in update).
- Total_distance not increment on outlier/low_accuracy/out_of_zone for both paths, history not include outlier/low_accuracy, outlier_count not increment for low_accuracy/stale, double-trigger counts one.
- Confidence no upgrade when road_dist>10 and outlier_count boundaries 2/3/5/6.

## Exit Codes

- 0: success or valid pickup/dropoff or batch_ok.
- 1: invalid pickup/dropoff (reason indicates why).
- 2: invalid argument, malformed value, unreadable zones/roads file, batch failure.
- 3: not found, out_of_zone, low_accuracy, outlier.
- 4: corrupt DB (with `.corrupt.<nanosec>` backup).

## Backward Compatibility and Performance

- Old DB files without history, total_distance, or outlier_count must be readable and auto-migrated.
- Whitespace-only files are empty stores, array and null are corrupt (BOM also corrupt).
- All Step 1 tests must still pass (140 tests).
- Large scale: 800+ vehicles, near and estimate queries under 3 seconds, plus batch 1000 <5s.

Deliverable remains `/app/src/` with stdlib-only Go binary.
