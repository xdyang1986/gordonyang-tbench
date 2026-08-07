# Step 2: Improve Location Accuracy to Avoid Inaccurate Pickup/Dropoff – Extreme Hard

## Context
You completed Step 1 extreme hardened (zones polygon holes circles time antimeridian, roads mixed, batch variable, list/near roads+zones, geofence-check). `/app/src/` contains working `locationctl`.

Production still has noisy GPS extreme:
- Urban canyon jumps, teleport, heading flip, acceleration spike, accuracy spike
- Off-road, stale, wrong street, moving marked arrived, road mismatch, heading mismatch

Business requires extreme accuracy improvements.

You must evolve same CLI, preserving Step1 compatibility.

## Data Model
Extend Location with outlier_count:
```json
{"vehicle_id":"veh_123","lat":37.7749,"lng":-122.4194,"timestamp_ms":1710000000000,"accuracy":5.0,"speed":12.5,"heading":90.0,"total_distance_m":123.4,"history":[...10...],"outlier_count":2}
```
- history up to 10 sorted asc includes current
- outlier_count rejected outlier count persisted even when rejected
- total_distance sum Haversine
Persistence map vehicle_id→Location, backward compat old DB without history/total_distance/outlier_count (auto-migrate).

## Enhanced Update Logic
#### A. Low Accuracy Filter
- accuracy >100 → low_accuracy stdout exit3 no update, outlier_count not increment.

#### B. Speed Cap
- speed >50 invalid exit2

#### C. Outlier Detection – Extreme (6 conditions)
Compute dt=(new-old)/1000 sec >0 else stale, distance haversine, implied=distance/dt
If any true → outlier:
1. Teleport: dt<300 && distance>1000 && implied>50 && old.accuracy<50 && new.accuracy<50
2. Heading flip: old.speed>10 && new.speed>10 && angularDiff>120 && distance<500
3. Median deviation: if history >=2, compute implied speeds of last up to 3 history transitions (history[i]->history[i+1]) median med, if abs(implied-med)>30 && implied>30 && distance>500 → outlier
4. Acceleration spike: dt>0 && abs(new.speed-old.speed)/dt >15 && distance<300 → outlier
5. Accuracy spike: new.accuracy>75 && new.accuracy > old.accuracy*2+30 → outlier
6. Speed vs implied: implied>80 && new.speed<2 && distance>1000 && dt<60 → outlier
On outlier increment outlier_count persist, print outlier exit3 no location update.

#### D. Zones – Same as Step1 extreme (polygon holes circles time antimeridian edge)

#### E. History & Distance – Same

### Polyline Roads & Heading-Aware Snapping Extreme
Roads file mixed polyline points and old segment start/end, any entry invalid → exit2 when used.
Finding nearest:
- For each road each segment points[i]->points[i+1], equirectangular x=R*lng_rad*cos(lat_ref) y=R*lat_rad lat_ref=vehicle lat R=6371000, closest clamped t in [0,1] distance hypot, track best.
- Bearing via initial bearing formula.
- Heading-aware if headingAware=true (estimate/validate with roads and speed>1): require min(angularDiff(heading,roadBearing), angularDiff(heading,bearing+180)) <=45 else skip candidate, if all filtered no snap (no fallback).
- If best distance <=50m → snapped return lat/lng road_id bearing dist else not snapped.

### Commands (Step1 must still work)
#### estimate <vehicle_id> [--now <ts> --roads <path>]
- Find vehicle else exit3. Now if --now else time.Now().UnixMilli(), age=now-ts negative=>0.
- EMA exponential: if history >=2 take last up to 5 entries, weight w=(1/(accuracy+1))*exp(-age_i/10000) age_i=now-history_i.timestamp, weighted avg lat/lng as smoothed base.
- Prediction: always degrade accuracy +0.5*age_sec (age_sec=age/1000). If age>0 && age<=30000 && speed>0 predicted true dist=speed*age_sec delta_lat=dist*cos(heading_rad)/R*180/pi delta_lng=dist*sin(heading_rad)/(R*cos(lat_rad))*180/pi, predicted lat/lng, original_lat/lng = smoothed before prediction else original=smoothed.
- Road snapping if --roads: heading-aware if speed>1 else not, if snapped original=pre-snap est lat/lng = snapped.
- Confidence extreme:
  high if (accuracy<=5 age<=5000) OR (accuracy<=10 age<=10000)
  medium if accuracy<=25 age<=20000 else low
  If snapped road_dist<=10: medium+acc<=25 → high, low+acc<=40 age<=15000 → medium
  outlier_count>2 high->medium, >5 low regardless, age>30000 low, accuracy>50 low, if not snapped accuracy>25 age>10000 and (accuracy>40 or age>15000) → low
- Output JSON with vehicle_id lat lng timestamp_ms accuracy speed heading total_distance_m confidence age_ms snapped road_id road_bearing road_dist_m predicted original_lat original_lng
- Exit0, roads invalid exit2.

#### validate-pickup <vehicle_id> <pickup_lat> <pickup_lng> [--now <ts> --roads <path> --zones <path>]
- Estimate vehicle via same logic as estimate (EMA, prediction, heading-aware snap if roads).
- Snap pickup to nearest road without heading-aware to get pickup_road_id if roads provided.
- Zones: if --zones provided check pickup inside any active zone filtered by now if provided else all; else if default /app/data/pickup_zones.json exists check inside it. If outside and active zones exist → valid false reason out_of_geofence exit1.
- Check order: out_of_geofence, stale age>30000, low_accuracy accuracy>50, off_road if roads provided and vehicle not snapped, moving speed>=5, road_mismatch both snapped road_ids differ, heading_mismatch if snapped same road speed>1 bearing vehicle->pickup diff >90 dist>10, too_far distance >100 else ok
- Output JSON valid bool reason distance_m confidence age_ms accuracy snapped road_id pickup_road_id vehicle_lat vehicle_lng (plus dropoff_road_id alias for dropoff)
- Exit0 valid 1 invalid 3 not found 2 invalid args/zone/road/lat lng.

#### validate-dropoff <vehicle_id> <dropoff_lat> <dropoff_lng> [--now <ts> --roads <path> --zones <path>]
- Same but thresholds: moving >=10, too_far >150, zones default /app/data/dropoff_zones.json

#### geofence-check <lat> <lng> [--zones <path>] [--now <ts>]
- Same as Step1 extreme.

#### Enhanced near/list/track etc same as Step1 extreme with zones circles holes time antimeridian, roads mixed, batch variable, etc.

### Road Data
Environment creates /app/data/roads.json sample, /app/data/zones.json empty by default (to avoid breaking tests), but loader must support circles holes time antimeridian etc when provided.

### Exit Codes 0 success/valid, 1 invalid pickup/dropoff, 2 invalid arg/malformed/zones/roads unreadable/batch fail, 3 not found/out_of_zone/low_accuracy/outlier, 4 corrupt DB

### Backward Compat
Old DB without history/total_distance/outlier_count readable, whitespace empty.

Tests verify all Step1 extreme plus 6 outlier conditions, road heading-aware no fallback, EMA exp decay, prediction accuracy degradation, confidence degradation, pickup off_road road_mismatch heading_mismatch etc, large scale.

