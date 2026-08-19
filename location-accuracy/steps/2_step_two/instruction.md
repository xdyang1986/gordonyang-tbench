# Step 2: Improve Location Accuracy

## Context

You have completed Step 1, which implemented a full vehicle location service with zones, roads, batch atomic operations, stale handling, and history tracking. The working binary is in `/app/src/` as `locationctl`.

Production still sees noisy GPS: urban canyon jumps, teleportation, heading flips, acceleration spikes, accuracy spikes, off-road positions, stale timestamps, wrong street assignments, and moving vehicles incorrectly marked as arrived.

A first attempt at the accuracy layer was deployed. Its source is in `/app/buggy/main.go` (and a copy at `/app/buggy/buggy_main.go`). It builds and passes all Step 1 checks, but it has subtle bugs. Your task is to fix `/app/src/` so it handles the observed production failures described below while remaining fully backward compatible with Step 1.

## Data Model Extension

Extend the location record with `outlier_count`:

```json
{"vehicle_id":"veh_123","lat":...,"lng":...,"timestamp_ms":...,"accuracy":5,"speed":...,"heading":...,"total_distance_m":...,"history":[...10 asc including current...],"outlier_count":2}
```

- `history` must contain up to 10 accepted locations sorted by timestamp ascending, including the current location as the last entry.
- `outlier_count` counts the number of rejected outliers for that vehicle. It must persist even when the location update is rejected, and must survive a process restart (tests verify across separate CLI invocations via `get --verbose`).
- `total_distance_m` is the sum of Haversine distances (R=6371000) over accepted updates only.
- An old database file that lacks `history`, `total_distance_m`, or `outlier_count` must be auto-migrated: treat missing history as empty, missing distance as 0, and missing outlier_count as 0. Whitespace-only files are empty stores, while an array `[]` or literal `null` is corrupt with exit 4 and a backup file `<db>.corrupt.<nanosec>` containing the original content.
- `total_distance_m` must not increase on outlier, low_accuracy, stale, or out_of_zone rejections. History must not include rejected attempts. This applies to both `update` and `batch` paths.

## Enhanced Update Logic

The update pipeline now has additional checks that run before zone validation and history update. The order matters and differs between the direct update path and the batch path; see failing observations for the divergence.

### Low Accuracy Filter

If accuracy is too high (inaccurate fix), the update must be rejected with message `low_accuracy` printed to stdout and exit 3. Do not change the database and do not change outlier_count.

Worked example: `update veh1 0 0 1000 --accuracy 150` should print `low_accuracy` exit 3, and a subsequent `get veh1 --verbose` must still show outlier_count 0. Boundary: accuracy 100 should succeed, 101 should be low_accuracy.

Observed failures:
- Vehicle at 0,0 ts 1000 acc100 should succeed, but buggy rejects as low_accuracy.
- Vehicle at 0,0 ts 1000 acc101 should be low_accuracy, but buggy succeeds.
- After two low_accuracy rejections (acc150, acc120) and one teleport outlier, `get --verbose` should show outlier_count 1, but buggy shows 3.

### Speed Cap

Speed above 50 m/s is invalid and must exit 2, same as Step 1.

### Outlier Detection – Six Conditions

Compute:
- `dt = (new_timestamp - old_timestamp) / 1000` seconds. If dt <=0 stale already handled.
- `distance` = Haversine between new and old.
- `implied = distance / dt`

If any of six qualitative conditions is true, the update is an outlier: increment `outlier_count` for that vehicle and persist it even though location is not updated, print `outlier` and exit 3, do not modify location, total_distance, or history. If multiple conditions are true, count as one increment only. outlier_count must not be incremented for low_accuracy or stale rejections.

Six conditions (qualitative):
1. Teleport: large jump in short time with good accuracy.
2. Heading Flip: both speeds are significant, heading angular difference is large, but distance is small (unrealistic flip).
3. Median Deviation: if history has at least 2 prior points, compute implied speeds for last up to 3 transitions, take median; if new implied deviates significantly and distance is significant.
4. Acceleration Spike: impossible acceleration given speed change and time.
5. Accuracy Spike: sudden degradation vs old accuracy.
6. Speed vs Implied Mismatch: reports nearly stopped but actually moved far in short time.

Quantitative thresholds are not listed here; they must be inferred from the failing observations below and from the hidden tests. Each observation pins a boundary.

Failing observations – current buggy binary `/app/buggy/main.go`:

1. `update veh1 0 0 1000 acc10 speed10`, then `update veh1 0.02 0 11000 acc10 speed10` (distance ~2220 m, dt 10 s, implied ~222 m/s, both accuracies good) – buggy returns success, should reject as outlier.
2. Same first, second `0.05 0 301000 acc10 speed10` (dt exactly 300 s) – distance ~5555 m, implied ~18 m/s, buggy rejects as outlier, should succeed because dt not < threshold and implied not > threshold. Indicates teleport needs dt < 300 and implied > 50.
3. First `0 0 1000 acc10 speed15 heading0`, second `0.0005 0 2000 acc10 speed15 heading150` (distance ~55 m, heading diff 150°) – should be outlier. Then heading diff exactly 120° (heading 120) – buggy rejects but should succeed, so heading flip needs diff > 120.
4. First `0 0 1000 acc10 speed10`, second `0.0005 0 2000 acc10 speed10 heading150` where speed exactly 10 – should succeed, not outlier, so heading flip needs speed > 10.
5. Distance boundary: first `0 0 1000 acc10 speed15 heading0`, second `0.0045 0 2000 acc10 speed15 heading150` distance ~500 m – should succeed (needs distance < 500), so boundary 500 not outlier.
6. Acceleration: first `0 0 1000 acc10 speed0`, second `0.0005 0 2000 acc10 speed30` (Δspeed/dt =30, dist ~55 m) – should be outlier. Variant `speed 15 dt1s Δ=15` at boundary should NOT be outlier, so needs >15.
7. Distance for accel: `0.0027 deg ~300 m` with Δ 30 – at boundary 300 should NOT be outlier, so needs <300.
8. Accuracy spike: first `0 0 1000 acc10`, second `0.0005 0 2000 acc80` where old 10 – old*2+30=50, new 80 >50 and >75 should be outlier. Boundary new acc exactly 75 should NOT be outlier.
9. Speed vs implied: first `0 0 1000 acc10 speed10`, second `0.0072 0 11000 acc10 speed1` dist ~800 m dt10s implied 80 – at boundary 80 should NOT be outlier; with dist 1000+ and dt<60 and speed<2 and implied>80 should be outlier. Speed exactly 2 should NOT be outlier.

### Zones

Same as Step 1 extreme: polygon with holes, circles, time windows, antimeridian unwrapping, edge/vertex inside (outer edge inside, hole edge inside hole thus outside). Same out_of_zone handling.

### History and Distance

Same as Step1 but total_distance must not increase on outlier/low_accuracy/stale/out_of_zone for both paths. History must not include rejected.

### Batch – Enhanced for Step 2

Batch inherits Step1 atomicity (all-or-nothing). For Step 2, batch handling is:

- Zones pre-pass: if default `/app/data/zones.json` exists, check each update op per its own timestamp. If any would be out_of_zone, fail whole batch exit 2 with no DB change, even if that op would be stale/low_accuracy/outlier. This pre-pass happens before per-op filtering.
- Then per-op handling: parse all lines first, validate vehicle_id, lat, lng, timestamp, accuracy, speed, heading same as update. For each op in order:
  - Low accuracy: if accuracy indicates low accuracy, reject: skip location update, do not count as applied, do not add distance/history, outlier_count unchanged, batch continues.
  - Stale: if ts <= stored, skip.
  - Outlier: six conditions same as update. If outlier, increment outlier_count persistently, skip, do not count applied.
  - Delete: 2 fields exactly, remove if exists and count as applied (both 0 and 1 accepted for nonexistent).
- After validation, apply sequentially with single atomic write, print `batch_ok <applied>` where applied excludes stale/low_accuracy/outlier rejections.

Single definition: batch does zones pre-pass first, then per-op low_accuracy/stale/outlier. There is no separate low_accuracy-before-zones rule for batch.

Failing observations for batch ordering:
- Setup default zones file `/app/data/zones.json` containing polygon `0,0 0,10 10,10 10,0`. Update `veh1 5 5 1000` inside succeeds.
- Then `batch` input single line `update veh1 20 20 2000 150 0 0` (outside zone, low_accuracy). Correct should fail whole batch exit 2 with no DB change because zones pre-pass catches out_of_zone even though op is low_accuracy. Buggy returns `batch_ok 0`.
- Same scenario via direct `update veh1 20 20 2000 --accuracy 150` without explicit --zones (uses default) should return `low_accuracy` exit 3, not out_of_zone. This divergence is intentional: update path does low_accuracy before zones, batch path does zones pre-pass first.

Observation for batch outlier separation: batch input `update veh1 0.0001 0 2000 acc150` (low_accuracy) and `update veh1 0.05 0 2100 acc10 speed30` (teleport) – after batch, `get --verbose` should show outlier_count 1 and total_distance unchanged for low_accuracy part.

## Roads

Roads file has mixed formats: each entry is either `{"id":"...","points":[... at least 2 ...]}` as polyline, or `{"id":"...","start":{...},"end":{...}}` as legacy segment. Any invalid entry must cause exit 2 when that roads file is used.

For each road and each segment `points[i] -> points[i+1]`, convert to equirectangular projection with latitude reference set to the vehicle latitude (or predicted latitude if predicted path), R=6371000, find closest point clamped with t in [0,1], compute Euclidean distance in projected space. Track best overall. Compute road bearing via initial bearing formula.

Heading-aware filtering: used for `estimate` and `validate-pickup/dropoff` when `--roads` is provided and vehicle speed > 1. A candidate road is filtered out if angular difference to its bearing (considering both directions) exceeds a threshold. If all candidates filtered, result is not snapped.

Worked examples:
- Road `north` bearing 0 (points 0,0 ->1,0). Vehicle at 0.0001,0.0001 heading 90 speed 10 distance ~11 m – heading-aware active, diff ~90° should cause not snapped, but buggy snaps due to extra search.
- Two roads `north_south` (0,0->10,0 bearing 0) and `east_west` (0,0->0,10 bearing 90) both near origin. Vehicle at 0.0001,0.0001 heading 90 speed 10 – closest might be north_south, but heading matches east_west, so east_west should win; buggy picks north_south.

Snapping rule: if closest projected distance is within a small threshold, snapped true with road_id, road_bearing, road_dist_m. Threshold pinned by observations: distance 49m should snap, 51m should not (0.00044 deg lat ~49m). Heading diff 45° should snap, 46° on north-south road with heading 46 should be filtered.

## Commands

All Step 1 commands must continue to work.

### estimate <vehicle_id> [--now <ts> --roads <path>]

- Not found exit 3, invalid roads file exit 2.
- `now` handling: if `--now` provided use it, otherwise current time. Compute `age = now - timestamp_ms`, if negative set 0.
- EMA smoothing: if history has at least 2 entries, take last up to 5 entries, weight each by `(1/(accuracy+1)) * exp(-age_i/10000)` where `age_i = now - history_i.timestamp_ms`. Weighted avg lat/lng is smoothed base. If history has 1 or 0, smoothed = latest.
- Prediction: accuracy degrades by `+0.5 * age_sec` where `age_sec=age/1000` regardless of predicted flag. If `age>0 && age<=30000 && speed>0`, prediction true: `dist = speed*age_sec`, `delta_lat = dist * cos(heading_rad)/R*180/pi`, `delta_lng = dist * sin(heading_rad)/(R*cos(lat_rad))*180/pi`. Predicted position = smoothed+delta. Otherwise predicted false and position = smoothed.
- Road snapping: as described, when `--roads` given, find closest with heading-aware when speed>1. If all filtered, not snapped.
- Original vs final:
  - When not snapped, `original_lat`/`original_lng` is always the smoothed base before prediction and before snapping, even when predicted true.
  - When snapped, original is position before snapping (predicted if predicted, otherwise smoothed).
  Four cases:
    - Not predicted, not snapped: original==final==smoothed
    - Predicted, not snapped: original==smoothed, final==smoothed+delta
    - Not predicted, snapped: original==smoothed, final==snapped
    - Predicted, snapped: original==predicted before snapping, final==snapped
- Confidence: base high/medium/low based on accuracy and age. Snapped with small road distance can upgrade. Demotion by outlier_count: many outliers demote high to medium and eventually force low regardless. Age>30000 or accuracy>50 forces low. Not snapped with moderate acc and age can be low.

Failing observations:
1. Vehicle at 0,0 ts1000 acc5 speed10 heading0 north, now=6000 (age 5s). Smoothed 0,0 predicted ~0.000449 north. Buggy returns original_lat = predicted, expected original_lat 0 and lat 0.000449, predicted true, lat > original_lat.
2. Same but with road at lat0 lng0->0,10: predicted 50m north, distance exactly 50m should snap. When snapped+predicted, original should be predicted (~0.000449) not smoothed 0, final snapped (0,0). Buggy returns original 0.
3. EMA last 5 only: history 10 entries tiny increments, estimate should use last 5 only. Buggy uses 10.
4. Confidence: acc5 age0 after 2 outliers -> high; after 3 -> medium; after 6 -> low even when snapped within small threshold.

Output JSON with fields: vehicle_id, lat, lng, timestamp_ms, accuracy, speed, heading, total_distance_m, confidence, age_ms, snapped, road_id, road_bearing, road_dist_m, predicted, original_lat, original_lng. Exit 0. Optional outlier_count allowed.

### validate-pickup <vehicle_id> <pickup_lat> <pickup_lng> [--now <ts> --roads <path> --zones <path>]

1. Estimate vehicle position via same EMA, prediction, and heading-aware snap.
2. Snap pickup point to nearest road without heading-aware if roads provided.
3. Zones: if `--zones` provided check pickup inside any active zone (active filtering by now if provided else all). If not provided, check default `/app/data/pickup_zones.json` if exists and non-empty. If outside and active zones exist, valid false reason out_of_geofence exit 1. Reason ok must be exactly "ok" when valid.

Priority order (first wins):
1. out_of_geofence
2. stale
3. low_accuracy
4. off_road
5. moving
6. road_mismatch
7. heading_mismatch
8. too_far
9. otherwise ok

Failing observations:
- stale beats low_accuracy, off_road, moving, road_mismatch, too_far.
- low_accuracy beats off_road, moving, road_mismatch, heading_mismatch, too_far.
- off_road beats moving and too_far.
- moving beats too_far, road_mismatch, heading_mismatch.
- Boundaries: pickup speed 4.9 valid true, 5.0 moving invalid; dropoff 9.9 valid true, 10.0 moving invalid.
- too_far: pickup 90m valid true, 110m too_far; dropoff 140m valid, 160m too_far.
- heading_mismatch: east-west road, vehicle heading 90 speed2 pickup 50m west bearing diff 180 distance>10 -> heading_mismatch.
- road_mismatch: road_a at lat0 and road_b at lat0.001, vehicle snapped to a, pickup snapped to b -> road_mismatch.

Output JSON: valid, reason, distance_m, confidence, age_ms, accuracy, snapped, road_id, pickup_road_id, vehicle_lat, vehicle_lng, plus `dropoff_road_id` alias same value as pickup_road_id. Exit 0 valid, 1 invalid, 3 not found, 2 invalid args/zones/roads.

### validate-dropoff <vehicle_id> <dropoff_lat> <dropoff_lng> [--now <ts> --roads <path> --zones <path>]

Same as pickup but more lenient: moving and too_far thresholds larger. Zones default `/app/data/dropoff_zones.json`.

Example: vehicle speed 7 at same location: pickup moving invalid, dropoff ok valid true.

### geofence-check, near, list, track, etc.

Same as Step1 extreme behavior with zones filtering: list/near allow all when no active zones at given now, geofence-check returns outside when no active.

## Persistence and Crash Consistency

- Same atomic write pattern as Step 1: temp file `<db>.tmp.<pid>` + rename, no leftover, cleanup stale tmp files, plus best-effort fsync of temp file and directory after rename.
- On corruption (invalid JSON, array, null, truncated file, BOM, trailing comma), create backup `<db>.corrupt.<nanosec>` with integer nanosec suffix, then exit 4.
- Stale tmp files must be ignored on load and cleaned on next successful write.

## Behavioral Details

- Help variants: help, --help, -h and also with equals like `--help=true`, `-h=true`, `help=true` must print help containing `update,get,list,near,track,distance,delete,stats,batch,clear,estimate,validate-pickup,validate-dropoff,geofence-check` exit0. Unknown with equals `--unknown=true` must exit2.
- Output keys exact: estimate must contain exactly `vehicle_id,lat,lng,timestamp_ms,accuracy,speed,heading,total_distance_m,confidence,age_ms,snapped,road_id,road_bearing,road_dist_m,predicted,original_lat,original_lng` plus optional `outlier_count`. Pickup/dropoff must contain at least `valid,reason,distance_m,confidence,age_ms,accuracy,snapped,road_id,pickup_road_id`.
- Batch empty input -> `batch_ok 0`.
- Zones file: empty array `[]` allows all, top-level non-array (string, number, null, bool, object) invalid exit2.
- Flag order: `--db` may appear after command (e.g. `update veh 0 0 1000 --db path`) must work.
- Performance: 1000 batch <5s, 500 near <3s, 200 vehicles estimate 50 queries <5s.
- Total_distance not increment on outlier/low_accuracy/out_of_zone for both paths, history not include outlier/low_accuracy, outlier_count not increment for low_accuracy/stale, double-trigger counts one.
- Confidence no upgrade when road_dist>10.
- Delete prints `deleted` even if vehicle does not exist.

## Exit Codes

- 0: success or valid pickup/dropoff or batch_ok or deleted/cleared.
- 1: invalid pickup/dropoff (reason indicates why).
- 2: invalid argument, malformed value, unreadable zones/roads file, batch failure.
- 3: not found, out_of_zone, low_accuracy, outlier.
- 4: corrupt DB (with `.corrupt.<nanosec>` backup).

## Backward Compatibility and Performance

- Old DB files without history, total_distance, or outlier_count must be readable and auto-migrated.
- Whitespace-only files are empty stores, array and null are corrupt (BOM also corrupt).
- All Step1 tests must still pass.
- Large scale: 800+ vehicles, near and estimate queries under 3 seconds, plus batch 1000.

Deliverable remains `/app/src/` with stdlib-only Go binary. The buggy starter is at `/app/buggy/main.go` for reference.
