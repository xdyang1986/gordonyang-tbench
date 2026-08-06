# Step 2: Improve Location Accuracy to Avoid Inaccurate Pickup/Dropoff – Hard

## Context
You completed Step 1: vehicle tracking with update/get/list/near/track/distance/delete/stats/batch/clear and zone geofencing. `/app/src/` already contains working `locationctl`.

Production Uber-like system still has noisy GPS:
- Urban canyoning 50-100m jumps, teleport glitches, heading flip-flops
- Low accuracy fixes cause driver on wrong street
- Stale backgrounded app causes ETA errors
- Map mismatch: vehicle appears off-road, rider sees driver on parallel street
- Moving vehicle marked as arrived (must be stopped)
- Pickup on wrong road (must match road id)
- Need heading-aware snap-to-road and EMA smoothing with time decay

Business requires hardened accuracy improvements.

You must evolve same CLI, preserving Step1 compatibility.

## Updated Data Model
Extend Location to include outlier tracking:
```json
{
  "vehicle_id":"veh_123",
  "lat":37.7749,"lng":-122.4194,"timestamp_ms":1710000000000,
  "accuracy":5.0,"speed":12.5,"heading":90.0,
  "total_distance_m":123.4,
  "history":[...10 max...],
  "outlier_count":2
}
```
- `history`: last up to 10, sorted timestamp asc, oldest first, newest last includes current
- `outlier_count`: number of rejected outlier updates for this vehicle, for confidence degradation
- `total_distance_m`: sum Haversine of successive accepted updates

Persistence JSON map vehicle_id → Location, backward compatible reading old DB without history/total_distance/outlier_count (auto-migrate).

## Enhanced Update Logic (replaces Step1)

#### A. Low Accuracy Filter
- If `accuracy` >100 → print `low_accuracy` stdout exit 3, no update.

#### B. Speed Cap
- If `speed` >50 → invalid argument exit 2.

#### C. Outlier / Teleport Detection – Hardened
- Compute `dt = (new_ts - old_ts)/1000` sec (must >0 else stale)
- `distance = haversine(old,new)`
- `implied_speed = distance/dt`
- Conditions:
  - If `dt<300 && distance>1000 && implied_speed>50 && old.accuracy<50 && new.accuracy<50` → outlier
  - **Heading flip**: if `old.speed>10 && new.speed>10` (actually new speed passed) and `angularDiff(old.heading,new.heading)>120` and `distance<500` → outlier (sharp turn at high speed unrealistic)
  - **Median deviation**: if history has >=2 prior points, compute implied speeds of last 2 history transitions (history[k]→existing). Take median `med`. If `abs(currSpeed-med)>30 && currSpeed>30` → outlier
- On outlier: increment `outlier_count` in DB (persist even though update rejected), print `outlier` stdout exit 3, do NOT update location (save outlier_count).

#### D. Zones
- Same as Step1: if `--zones <path>` provided use it, else if default `/app/data/zones.json` exists use it. If zones non-empty, location must be inside at least one polygon → else `out_of_zone` exit 3.

#### E. History & Distance
- On success: add distance to total_distance, push history entry, trim to 10, sorted.

### Polyline Roads & Heading-Aware Snapping

Roads file format **hardened** – polyline:
```json
[
  {"id":"road_1","points":[{"lat":37.7749,"lng":-122.4194},{"lat":37.7849,"lng":-122.4094},{"lat":37.7949,"lng":-122.4194}]}
]
```
Each road has id and points array >=2. Each point has lat,lng valid.

Support backward compat: also support old segment format `{"id","start":{lat,lng},"end":{lat,lng}}` – convert to points [start,end].

Finding nearest:
- For each road, for each segment points[i]→points[i+1], convert to local Cartesian XY using equirectangular: `x=R*lng_rad*cos(lat_ref)`, `y=R*lat_rad` where lat_ref = vehicle lat (or query lat) for projection, R=6371000.
- Compute closest point on segment clamped (t in [0,1]), distance = hypot.
- Track best across all segments/roads.
- Compute road bearing for that segment via initial bearing formula.
- **Heading-aware**: if `headingAware=true` (for estimate/validate with --roads and vehicle heading available), require minimal angular difference between vehicle heading and road bearing OR opposite direction <=45 degrees: `min(angularDiff(heading, roadBearing), angularDiff(heading, bearing+180)) <=45`, else skip candidate (no snap).
- If best distance <=50m → snapped, return snapped lat/lng (converted back), road_id, road_bearing, road_dist.
- Else not snapped.

### New & Enhanced Commands (Step1 must still work)

#### `estimate <vehicle_id> [--now <ts> --roads <path>]`
JSON output:
```json
{
  "vehicle_id":"veh_123","lat":37.775,"lng":-122.419,"timestamp_ms":1710000000000,
  "accuracy":5.0,"speed":12.5,"heading":90.0,
  "total_distance_m":123.4,
  "confidence":"high","age_ms":5000,
  "snapped":true,"road_id":"road_1","road_bearing":45.0,"road_dist_m":5.0,
  "predicted":true,"original_lat":37.7749,"original_lng":-122.4194
}
```
- Find vehicle else exit 3.
- Now: if --now provided use it else time.Now().UnixMilli()
- Age = now - timestamp, if negative =>0
- **Smoothing EMA with time decay**: if history >=2, take last up to 5 entries, weight each as `w_i = (1/(accuracy_i+1)) * (1/(1+age_i/10000))` where age_i = now - history_i.timestamp, exponential decay approx 10s. Compute weighted avg lat/lng. Use current accuracy/speed/heading for prediction base.
- **Prediction**: if age>0 and <=30000 and speed>0, predict forward: `dist=speed*dt`, `delta_lat = dist*cos(heading_rad)/R *180/pi`, `delta_lng = dist*sin(heading_rad)/(R*cos(lat_rad))*180/pi`. Set predicted true, accuracy degrades +0.5m per sec.
- **Road snapping**: if --roads provided, find nearest on polyline with heading-aware check (vehicle heading). If snapped, original_lat/lng = pre-snap estimated, lat/lng = snapped point.
- **Confidence Hardened**:
  - high: accuracy<=5 && age<=5000 && snapped
  - OR accuracy<=10 && age<=10000
  - medium: accuracy<=25 && age<=20000 (if snapped → high)
  - low: else, but if snapped && road_dist<10 → medium
  - outlier_count degrades: if outlier_count>2 && confidence==high → medium; if outlier_count>5 → low regardless
- Exit 0.

#### `validate-pickup <vehicle_id> <pickup_lat> <pickup_lng> [--now <ts> --roads <path> --zones <path>]`
- Estimate vehicle location via same logic as estimate (including prediction and optional heading-aware snap if --roads).
- Snap pickup point as well to nearest road (without heading-aware) to get pickup_road_id if roads provided.
- Zones: if --zones provided check pickup point inside any zone in that file; else if default `/app/data/pickup_zones.json` exists check inside it. If outside → valid false reason `out_of_geofence` exit 1.
- Check in order:
  1. stale if age>30000 → valid false reason `stale`
  2. low_accuracy if estimated accuracy>50 → `low_accuracy`
  3. moving if vehicle speed >=5 (must be stopped for accurate pickup) → `moving`
  4. road_mismatch if --roads provided and both vehicle snapped and pickup snapped but road_ids differ → `road_mismatch`
  5. too_far if distance between estimated vehicle and pickup >100m → `too_far`
  6. else valid true `ok`
- Output JSON `{"valid":bool,"reason":string,"distance_m":float,"confidence":string,"age_ms":int,"accuracy":float,"snapped":bool,"road_id":string,"pickup_road_id":string,"vehicle_lat":float,"vehicle_lng":float}`
- Exit 0 valid, 1 invalid, 3 not found, 2 invalid args or roads/zones read fail.

#### `validate-dropoff <vehicle_id> <dropoff_lat> <dropoff_lng> [--now <ts> --roads <path> --zones <path>]`
- Same as pickup but thresholds: distance >150 too_far, speed check still moving >=5? For dropoff allow moving? Keep same moving check for harder (must be stopped for dropoff too) or more lenient? We'll keep moving check but threshold speed >=10 for dropoff to be harder distinction: pickup requires speed<5, dropoff requires speed<10? To make tests distinct. Define pickup moving threshold 5 m/s, dropoff moving threshold 10 m/s. Document.
- Zones default file `/app/data/dropoff_zones.json` if --zones not provided.
- Reasons same plus out_of_geofence.

#### `geofence-check <lat> <lng> [--zones <path>]`
- Check if point inside any zone.
- --zones optional path, else default `/app/data/zones.json`.
- Output `{"inside":bool,"zone_id":string}` where zone_id is first matching zone id or "".
- Invalid lat/lng exit 2, zones read fail exit 2.

#### Enhanced `near`
- Already has --accuracy-max, --speed-min, --limit, --offset, --now, --include-stale, --zones.
- Must apply filters before distance check? Actually accuracy/speed filters first, then distance, then sort, then zones? But implement filter order: stale (if now provided), accuracy, speed, zones, distance.
- Staleness only when --now provided (fixed line 69 clarification).
- Pagination offset then limit.

#### Enhanced `list`
- Already has --since, --until, --limit, --offset.

#### Enhanced `track`
- Already has --from, --to, --limit, --offset.

#### Other commands same as Step1 hardened: `update,get,list,near,track,distance,delete,stats,batch,clear,help`

### Road Data
- Environment creates `/app/data/roads.json` sample with old segment format, but also polyline format may be used. Your loader must support both.
- Provide additional files for tests: `/app/data/pickup_zones.json` and `/app/data/dropoff_zones.json` optionally exist? For task, we create defaults in Dockerfile.

### Exit Codes
- 0 success / valid
- 1 invalid pickup/dropoff (still prints JSON)
- 2 invalid argument / malformed / zones/roads unreadable / batch fail
- 3 not found or out_of_zone / low_accuracy / outlier (for update)
- 4 corrupt DB

### Backward Compat
- Old DB without history/total_distance/outlier_count must be readable, auto-migrate history containing current, total_distance 0, outlier_count 0.

### Example
```bash
locationctl --db /tmp/db.json update veh1 37.7749 -122.4194 1000000000 --accuracy 5 --speed 0 --heading 90 --zones /app/data/zones.json
locationctl --db /tmp/db.json estimate veh1 --now 1000005000 --roads /app/data/roads.json
# roads.json polyline: [{"id":"r1","points":[{"lat":37.7749,"lng":-122.4194},{"lat":37.7849,"lng":-122.4094}]}]
locationctl --db /tmp/db.json validate-pickup veh1 37.7750 -122.4195 --now 1000005000 --roads /app/data/roads.json --zones /app/data/pickup_zones.json
# requires stopped (speed<5), same road, distance<=100, inside pickup zone, fresh, accurate
locationctl --db /tmp/db.json geofence-check 37.5 -121.5 --zones /app/data/zones.json
```

### Deliverable
Extend `/app/src/` implementation. Must compile `go build ./...` and `go build -o locationctl .`. Stdlib only.

Tests verify: outlier heading flip, median deviation, polyline snapping closest among multiple segments, heading-aware snap rejects when heading diff>45, EMA smoothing time decay, geofence polygon inside/outside, pickup requires stopped and road-id match and geofence, dropoff lenient moving threshold and distance 150, confidence degradation by outlier_count, near with all filters + zones + pagination, track pagination, batch atomicity with zones, stats total_distance, distance command, delete, clear, help, corrupt handling, large scale.
