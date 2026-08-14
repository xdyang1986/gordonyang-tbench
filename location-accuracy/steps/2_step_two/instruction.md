# Step 2: Improve Location Accuracy – Extreme Hard

## Context
You finished Step1 (extreme: zones polygon holes circles time antimeridian, roads mixed polyline+segment, batch variable all-or-nothing, stale handling, history 10). `/app/src/` contains working `locationctl`.

Production still sees noisy GPS: urban canyon jumps, teleport, heading flip, acceleration spike, accuracy spike, off-road, stale, wrong street, moving marked arrived.

You must evolve same CLI, preserving Step1 compatibility including all validation, zones, roads, batch semantics. Add outlier filtering, low-accuracy filter, EMA smoothing, prediction, heading-aware road snapping, confidence, and validate-pickup/dropoff.

## Data Model
Extend with `outlier_count`:
```json
{"vehicle_id":"veh_123","lat":...,"lng":...,"timestamp_ms":...,"accuracy":5,"speed":...,"heading":...,"total_distance_m":...,"history":[...10 asc including current...],"outlier_count":2}
```
- history up to 10 accepted locations sorted asc includes current
- outlier_count counts rejected outliers (persists even when rejected)
- total_distance sum Haversine R=6371000 over accepted only
- old DB without history/total_distance/outlier_count must auto-migrate (empty history, 0 distance, 0 outliers)

## Enhanced Update Logic (prior-violating)

#### A. Low Accuracy Filter (prior of zones)
If `accuracy >100` → print `low_accuracy` (stdout, stderr accepted) exit 3, no DB change, outlier_count unchanged.

#### B. Speed Cap
speed >50 → invalid exit2 (same as step1)

#### C. Outlier Detection – 6 interacting conditions (this is the main discriminator, not a transcription exercise)
Compute `dt=(new-old)/1000` sec, if ≤0 → stale case already handled. `distance` = Haversine(new,old), `implied = distance/dt`.

If any condition below true → outlier: increment `outlier_count` for that vehicle (persist even though location not updated), print `outlier` (stdout, stderr accepted) exit 3, no location/total_distance/history change.

Conditions (all must be implemented, thresholds matter – tests enforce them):

- **Teleport**: large jump in short time with good accuracy: dt<300 && distance>1000 && implied>50 && old.accuracy<50 && new.accuracy<50
- **Heading flip**: both moving >10 m/s, angular diff >120°, but distance <500m → unrealistic flip
- **Median deviation**: if history has ≥2 prior points, compute implied speeds of last up to 3 history transitions (history[i]→history[i+1]), median `med`. If |implied-med|>30 && implied>30 && distance>500 → outlier
- **Acceleration spike**: |new.speed-old.speed|/dt >15 && distance<300 → impossible acceleration
- **Accuracy spike**: new.accuracy>75 && new.accuracy > old.accuracy*2+30
- **Speed vs implied mismatch**: implied>80 && new.speed<2 && distance>1000 && dt<60 → reports stopped but actually moved far

#### D. Zones – same as Step1 extreme (polygon holes circles time antimeridian edge-inside). Same out_of_zone handling: output contains `out_of_zone` stdout/stderr accepted exit3.

#### E. History & Distance – same as Step1 but distance not added on outlier/low_accuracy/stale/out_of_zone.

### Roads – polyline + heading-aware snapping (no fallback is load-bearing)

- Roads file mixed: each entry either `{"id":"...","points":[...≥2...]}` polyline or `{"id":"...","start":{...},"end":{...}}` legacy segment. Any invalid entry → exit2 when roads file is used.
- For each road each segment points[i]→points[i+1], equirectangular with lat_ref = vehicle lat, R=6371000, find closest clamped t∈[0,1], hypot distance. Track best overall.
- Bearing via initial bearing formula.
- Heading-aware: when `headingAware=true` (used for estimate and validate when `--roads` provided and vehicle speed>1), candidate road filtered out if min(angularDiff(heading, roadBearing), angularDiff(heading, roadBearing+180)) >45. If all candidates filtered → no snap (must NOT fallback to non-heading-aware).
- If best distance ≤50m → snapped true with lat/lng = snapped point, road_id, road_bearing, road_dist_m. Else not snapped.

### Commands (all Step1 must still work)

#### estimate <vehicle_id> [--now <ts> --roads <path>]
- Not found → exit3, invalid roads → exit2
- Now: if --now provided else time.Now().UnixMilli(). age = now - ts, if negative →0.
- EMA smoothing: if history ≥2, take last up to 5 entries, weight `w = (1/(accuracy+1)) * exp(-age_i/10000)` where `age_i = now - history_i.timestamp_ms`. Weighted avg lat/lng = smoothed base.
- Prediction: always accuracy degrades +0.5*age_sec (age_sec=age/1000). If age>0 && age≤30000 && speed>0 → predicted true: dist=speed*age_sec, delta_lat=dist*cos(heading_rad)/R*180/π, delta_lng=dist*sin(heading_rad)/(R*cos(lat_rad))*180/π, predicted lat/lng, original_lat/lng = smoothed before prediction. Else predicted false, original = smoothed.
- Road snapping if --roads: heading-aware if speed>1 else normal. If snapped, final lat/lng = snapped point, original_lat/lng = smoothed base before prediction.
- Confidence (degrades by many signals, upgraded by on-road):
  high if (acc≤5 age≤5000) OR (acc≤10 age≤10000)
  medium if acc≤25 age≤20000 else low
  If snapped and road_dist≤10: medium+acc≤25 → high, low+acc≤40 age≤15000 → medium
  outlier_count>2 high→medium, >5 → low regardless, age>30000 → low, accuracy>50 → low, if not snapped and acc>25 age>10000 and (acc>40 or age>15000) → low
- Output JSON with vehicle_id lat lng timestamp_ms accuracy speed heading total_distance_m confidence age_ms snapped road_id road_bearing road_dist_m predicted original_lat original_lng
- Exit0.

#### validate-pickup <vehicle_id> <pickup_lat> <pickup_lng> [--now <ts> --roads <path> --zones <path>]
- Estimate vehicle via same EMA/prediction/heading-aware snap logic as estimate.
- Snap pickup to nearest road without heading-aware (to get pickup_road_id) if roads provided.
- Zones: if --zones provided check pickup inside any active zone filtered by now if provided else all; else if default /app/data/pickup_zones.json exists and non-empty check inside it. If outside and active zones exist → valid false reason `out_of_geofence` exit1. Valid true reason must be exactly "ok" (specify literal) – fairness fix.
- Check priority order (first matching reason wins, defines output reason):
  1. out_of_geofence (if pickup outside active geofence and zones exist)
  2. stale if estimate age>30000
  3. low_accuracy if estimate accuracy>50
  4. off_road if roads provided and vehicle not snapped
  5. moving if vehicle speed≥5
  6. road_mismatch if both vehicle and pickup snapped but road_ids differ
  7. heading_mismatch if same road, vehicle speed>1, bearing vehicle→pickup angular diff >90 and distance>10
  8. too_far if Haversine vehicle→pickup >100
  9. else ok valid true
- Output: {"valid":bool,"reason":string,"distance_m":..., "confidence":..., "age_ms":..., "accuracy":..., "snapped":..., "road_id":..., "pickup_road_id":..., "vehicle_lat":..., "vehicle_lng":...} plus `dropoff_road_id` alias containing same as pickup_road_id for dropoff command compatibility.
- Exit 0 valid, 1 invalid, 3 not found, 2 invalid args/zone/road/lat/lng.

#### validate-dropoff <vehicle_id> <dropoff_lat> <dropoff_lng> [--now <ts> --roads <path> --zones <path>]
Same but lenient: moving ≥10 (instead of 5), too_far >150, zones default `/app/data/dropoff_zones.json`.

#### geofence-check, near, list, track, etc – same as Step1 extreme

### Road/Zone Data
Environment creates /app/data/roads.json sample, zones files empty `[]` by default to avoid breaking. Loader must support holes, circles, time windows, antimeridian when provided.

### Exit Codes
0 success/valid, 1 invalid pickup/dropoff, 2 invalid arg/malformed/zones/roads unreadable/batch fail, 3 not found/out_of_zone/low_accuracy/outlier, 4 corrupt DB

### Backward Compat & Large Scale
- Old DB without history/total_distance/outlier_count readable, whitespace-only empty, array → corrupt 4.
- All Step1 tests still pass.
- Large scale 800+ vehicles, near/estimate <3s.

### Why hard?
Spec no longer provides copy-paste pseudocode for every edge. You must infer from behavior: outlier 6 conditions interact with history and accuracy, heading-aware no-fallback, median deviation uses last 3 speeds, EMA exp decay weighting, prediction delta formulas, confidence upgrade/downgrade chain, road_mismatch vs off_road priority, pickup snap without heading vs vehicle snap with heading, batch all-or-nothing with zones before stale, total_distance only over accepted. Tests enforce byte-exact JSON and reason literals ("ok", "moving", "off_road", etc.) and contain out_of_zone in either stdout or stderr.

Deliverable `/app/src/`.
