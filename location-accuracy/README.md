# codimango/location-accuracy

Multi-turn Go task for ride-sharing vehicle location tracking (Uber-like) with extreme accuracy improvements to avoid inaccurate pickup and dropoff.

## Overview

### Step 1: Vehicle Location Tracking Service (1_step_one, 43 tests, medium – simplified to increase chance for Step 2)

Build `locationctl` in Go at `/app/src`, module `locationservice`, stdlib only.

Core features (further simplified from 63 extreme-hard → 60 → 43 medium):
- **Persistence**: JSON map vehicle_id to Location, atomic writes via `<db>.tmp.<pid>` then rename, no leftover temp files. Parent directories must be created. Empty or whitespace-only file is empty store. Corrupt files (unparsable JSON, array `[]`, literal `null`, truncated) must exit 4 (backup optional). Stale tmp ignored and cleaned on next write.
- **Validation**: vehicle_id regex `^[A-Za-z0-9_-]{1,64}$`, lat [-90,90], lng [-180,180], reject NaN/Inf, timestamp integer string only (reject `1000.0`, `1e3`, `0x3e8`), accuracy >=0 default 10, speed 0-50 default 0 >50 invalid exit 2, heading [0,360) default 0.
- **History and Distance**: total_distance Haversine sum R=6371000 over accepted only, not stale/out_of_zone. History up to 10 last accepted sorted asc, includes current as last.
- **Zones (simplified to polygons only)**: polygon >=3 valid points, edge/vertex inside, no holes, no circles, no time windows, no antimeridian for Step1. For update: if --zones given else default `/app/data/zones.json` if exists, if zones non-empty must be inside at least one else out_of_zone exit 3. For list/near/geofence-check: --zones optional simple inside filtering, no time filtering (--now accepted but ignored for zones; kept for near stale handling). Empty zones array allows all.
- **Roads (simplified)**: polyline only `points` >=2, no legacy start/end. Snapping equirectangular lat_ref=query lat, all segments t in [0,1], distance <=50m snapped.
- **Commands**: update prints without history but with total_distance, get --verbose full, list sorted id asc with since/until inclusive and zones/roads filters pagination offset then limit (limit0=[], offset>len=[]), near lat/lng/radius [0,50000] plus accuracy-max, speed-min, now for stale exclusion age>30000 unless --include-stale, distance <=radius sorted distance asc then vehicle_id asc, track --from --to paginated, delete prints deleted even if not found, stats live/total_updates/total_distance/avg_accuracy, batch tab-delimited 5-8 fields >8/<5 fail exit2 atomic zones before stale stale skipped batch_ok <applied>, clear, geofence-check first matching zone file order.

Simplifications vs original extreme-hard to increase Step2 run chance:
- Removed antimeridian unwrapping (179 to -179)
- Removed mixed road legacy start/end
- Removed holes, circles, time windows, inclusive boundary seam, divergence --now vs update timestamp – all moved to Step2 as advanced features
- Zones now simple polygon only for Step1, no --now filtering
- Corrupt backup optional, only exit 4
- Large scale 100 not 800, 1.9s not 13s
- Batch simplified to core atomicity

### Step 2: Improve Location Accuracy (2_step_two, 79 tests, extreme-hard, inherit_prior_session true)

Backward compatible with Step 1, adds:

- **Low Accuracy Filter**: accuracy >100 -> print low_accuracy exit 3, no DB change, outlier_count unchanged.
- **Outlier Detection – Six Conditions** (main discriminator):
  - Teleport: dt<300 && distance>1000 && implied>50 && old.accuracy<50 && new.accuracy<50
  - Heading Flip: both speeds >10, angular diff >120°, distance <500
  - Median Deviation: history >=2, median of last up to 3 implied speeds, |implied-med|>30 && implied>30 && distance>500
  - Acceleration Spike: |Δspeed|/dt>15 && distance<300
  - Accuracy Spike: new>75 && new>old*2+30
  - Speed vs Implied: implied>80 && new.speed<2 && distance>1000 && dt<60
  On outlier: increment outlier_count persistently, print outlier exit 3, no location change. Multiple conditions true still counts as one.

- **Outlier Count Family (strengthened)**:
  - Must survive process restart: persisted to DB, reload and check get --verbose. Estimate confidence must reflect persisted count after restart.
  - Demotion chain boundaries: exactly 3 outliers (>2 -> high demotes to medium) and exactly 6 (>5 -> low regardless). Tests verify off-by-one: 2 still high, 3 medium, 5 medium not low, 6 low.
  - Must not increment for low_accuracy or stale rejections — only six outlier conditions. Mixed scenario verifies separation.

- **Roads**: heading-aware when speed>1, filter if min(diff(heading,roadBearing), diff(heading,roadBearing+180))>45, no fallback.

- **Estimate**: EMA last 5 entries weight 1/(accuracy+1)*exp(-age/10000), prediction if age>0 && <=30000 && speed>0 using delta formulas, accuracy degrades +0.5*age_sec.

- **Confidence**: high if acc<=5 age<=5000 OR acc<=10 age<=10000, medium if acc<=25 age<=20000 else low, snapped road_dist<=10 upgrades medium->high when acc<=25 and low->medium when acc<=40 age<=15000, outlier>2 high->medium, >5 low regardless, age>30000 low, accuracy>50 low, not snapped acc>25 age>10000 and (acc>40 or age>15000) -> low.

- **Validate Pickup/Dropoff**: priority order out_of_geofence (zones --zones else pickup_zones.json/dropoff_zones.json), stale age>30000, low_accuracy acc>50, off_road roads not snapped, moving >=5 (dropoff >=10), road_mismatch both snapped diff road_id, heading_mismatch same road speed>1 bearing diff>90 dist>10, too_far >100 (dropoff >150) else ok reason literal "ok". Output valid reason distance_m confidence age_ms accuracy snapped road_id pickup_road_id plus dropoff_road_id alias.

- **Crash Consistency Gate**: same as Step 1 – corrupt backup with .corrupt.<nanosec> integer suffix, stale tmp ignored and cleaned.

## Latest Validation (oracle)

Built from `steps/*/solution/solve.sh` at `/app/src` go 1.22 GOTOOLCHAIN=local.

- **Step1: 43/43 PASS (1.9s)** – further simplified from 63 → 60 → 43 medium to increase Step2 run chance:
  - Base validation, total_distance, history 10, stale handling, integer timestamp, NaN/Inf, ID regex, batch atomic zones before stale, near include-stale, simple polygon zones edge-inside, geofence, roads polyline all segments.
  - Simplifications vs original: removed antimeridian (2 tests), removed mixed road legacy start/end (1 test), removed holes (2 tests), removed circles (2 tests), removed time windows inclusive/only_from/divergence (4 tests), removed combined zones+roads large scale, reduced large scale 800→100 for speed, corrupt backup optional.
  - **Goal**: Step1 medium (43 tests) lets agents pass and get chance to run Step2 extreme-hard. Original extreme-hard Step1 gated online, no chance for Step2.

- **Step2: 79/79 PASS (6s)** – still extreme-hard, but now more agents will reach it:
  - Step2 solution still implements full extreme features: polygon with holes, circles, time windows inclusive, antimeridian unwrapping, mixed roads, heading-aware no fallback, outlier six conditions, etc.
  - All simplified Step1 compat still passes, but Step2 adds advanced zone features as new requirements (holes, circles, time, antimeridian) that Step1 no longer requires – this is intentional iteration: Step1 simple polygon, Step2 adds complex geofences.
  - Core discriminator: outlier_count family persistence, boundaries exactly 3 (>2→medium) and 6 (>5→low), non-increment for low_accuracy/stale.

## Agent Failure Analysis

Step1 was too easy at 59 (0% fail), hardened to 81/84 too hard (0% pass, saturated crash-consistency). Eased to 63, then to 60 removing antimeridian/mixed, still hard and online pass low → not enough chance for Step2. Now further simplified to 43 medium: removed holes, circles, time windows, divergence, combined filters, reduced scale. This should raise Step1 pass rate significantly, giving more online runs for Step2.

Step2 was 79/79 pass with its own oracle, but rarely reached online due to Step1 gating.

## Structure

- `environment/Dockerfile` – ubuntu:24.04, golang-go, python3, pytest 8.4.1, /app/src, /app/data/roads.json sample polyline only, empty zones []
- `steps/1_step_one/` – simple vehicle tracking: polygon zones only (no holes/circles/time/antimeridian), polyline roads only, basic batch atomic, 43 tests medium (1.9s) – goal increase Step2 chance
- `steps/2_step_two/` – accuracy extreme-hard: adds holes, circles, time windows inclusive + divergence, antimeridian, mixed roads, heading-aware no fallback, outlier six conditions + outlier_count family, 79 tests

## Run Locally

```bash
bash steps/1_step_one/solution/solve.sh
pytest steps/1_step_one/tests/test_outputs.py -v
bash steps/2_step_two/solution/solve.sh
pytest steps/2_step_two/tests/test_outputs.py -v
```

Binary: `go build -o locationctl .` from `/app/src` module `locationservice` stdlib only.
