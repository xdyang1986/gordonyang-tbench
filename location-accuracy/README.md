# codimango/location-accuracy

Multi-turn Go task for ride-sharing vehicle location tracking (Uber-like) with extreme accuracy improvements to avoid inaccurate pickup and dropoff.

## Overview

### Step 1: Vehicle Location Tracking Service (1_step_one, 82 tests, extreme-hard – hardened)

Build `locationctl` in Go at `/app/src`, module `locationservice`, stdlib only.

Core features (hardened from 58 too easy):
- **Persistence**: JSON map vehicle_id to Location, atomic writes via `<db>.tmp.<pid>` then rename, no leftover tmp files. Parent directories must be created deeply nested. Empty or whitespace-only file is empty store. Corrupt files (unparsable JSON, array `[]`, literal `null`, truncated file) must exit 4 and **mandatory backup** `<db>.corrupt.<nanosec>` where nanosec is integer nanosecond timestamp, distinct suffixes, containing exact original content. Stale tmp files must be ignored and cleaned on next successful write.
- **Validation**: vehicle_id regex `^[A-Za-z0-9_-]{1,64}$`, length boundaries 1/64/65, dash/underscore allowed, spaces/special chars invalid. lat [-90,90], lng [-180,180], reject NaN/Inf/Infinity case-insensitive, isNaN/IsInf after parse. timestamp must be integer string only (reject `1000.0`, `1e3`, `0x3e8`, `1E3`, negative, hex). accuracy >=0 default 10 NaN/Inf invalid 0 valid, speed [0,50] default 0 >50 invalid exit2, heading [0,360) default 0 360 exclusive invalid.
- **History and Distance**: total_distance is Haversine sum R=6371000 over accepted updates only, not on stale or out_of_zone or outlier/low_accuracy in Step2. History keeps up to 10 last accepted locations sorted asc, includes current as last. After stale, last must equal current.
- **Zones**: polygon >=3 points, holes each >=3, circles radius 0< <=1e6, both polygon+circle present invalid, active_from/to time filtering inclusive bounds ts==from and ts==to both active, only-from active onward, only-to up to that time, from-1/to+1 inactive, from > to invalid exit2. Must handle antimeridian crossing by unwrapping longitudes to continuous range, so rectangle from 179 to -179 is 2 deg wide not 358. Point on edge or vertex counts as inside. Circle distance Haversine <= radius inside, exact radius inside just beyond outside. Holes outside (inside outer but inside hole → outside).
- **Filtering**: `update`: if `--zones <path>` else default `/app/data/zones.json` if exists. Filter active zones by update's own timestamp. If active non-empty must be inside at least one else out_of_zone exit3, if no active allow all. `list`, `near`, `geofence-check`: --zones optional filtered by --now if provided else all, intuitive: if no active zones at given now, list/near allow all (no filtering) rather than [], geofence-check returns outside. This keeps time-window seam (inclusive, only_from/to, divergence --now vs update) but intuitive.
- **Roads**: mixed formats polyline `points` >=2 and legacy `start/end` both valid, any invalid entry exit2 when used. Snapping equirectangular R=6371000 lat_ref=query lat, checks all segments t in [0,1], distance <=50m snapped, interior points not just endpoints, closest among segments.
- **Commands**: update prints without history but with total_distance, get --verbose full, list sorted id asc with since/until inclusive and zones/roads filters pagination offset then limit (limit0=[], offset>len=[]), near lat/lng/radius [0,50000] plus accuracy-max, speed-min, now with age>30000 stale exclusion only when now provided unless --include-stale, distance <=radius sorted distance asc then vehicle_id asc with tie-breaking, radius 0 exact boundary, haversine accuracy 10m vs 12m for 0.0001deg, track --from --to paginated, delete prints deleted even if not found, stats live/total_updates/total_distance/avg_accuracy, batch tab-delimited variable 5-8 fields empty means default >8/<5 fail exit2, all-or-nothing atomic zones check before stale stale skipped batch_ok <applied>, mixed update+delete same vehicle order matters, batch empty field defaults all combos, batch with zones default file out_of_zone even if stale, batch whitespace empty lines ignored, clear, geofence-check first matching zone file order.

### Step 2: Improve Location Accuracy (2_step_two, 138 tests, extreme-hard – hardened, inherit_prior_session true)

Backward compatible with Step 1, adds:

- **Low Accuracy Filter**: accuracy >100 → print low_accuracy exit3, no DB change, outlier_count unchanged. Separation load-bearing: low_accuracy must not increment outlier_count.
- **Outlier Detection – Six Conditions** (main discriminator, thresholds matter):
  - Teleport: dt<300 && distance>1000 && implied>50 && old.accuracy<50 && new.accuracy<50
  - Heading Flip: both speeds >10, angular diff >120°, distance <500
  - Median Deviation: history >=2, median of last up to 3 implied speeds, |implied-med|>30 && implied>30 && distance>500
  - Acceleration Spike: |Δspeed|/dt>15 && distance<300
  - Accuracy Spike: new>75 && new>old*2+30
  - Speed vs Implied: implied>80 && new.speed<2 && distance>1000 && dt<60
  On outlier: increment outlier_count persistently, print outlier exit3, no location change. Multiple true counts as one. Must not increment for low_accuracy or stale, stale dt==0 not outlier.

- **Outlier Count Family (strengthened)**:
  - Must survive process restart: persisted to DB, reload and check get --verbose. Estimate confidence must reflect persisted count after restart.
  - Demotion chain boundaries: exactly 3 outliers (>2→high demotes to medium) and exactly 6 (>5→low regardless). Tests verify off-by-one: 2 still high, 3 medium, 5 medium not low, 6 low.
  - Must not increment for low_accuracy or stale, separation mixed, double-trigger counts one, demotion chain.
  - History not include outlier and low_accuracy, total_distance not increment on outlier/low_accuracy.

- **Roads**: heading-aware when speed>1, filter if min(diff(heading,roadBearing), diff(heading,roadBearing+180))>45, no fallback is load-bearing: if all filtered, not snapped. Opposite allowed, closest among segments, mixed format, invalid entry exit2.

- **Estimate**: EMA last 5 entries weight 1/(accuracy+1)*exp(-age_i/10000) age_i=now-history_i.timestamp_ms, weighted average lat/lng smoothed base. Prediction if age>0 && <=30000 && speed>0: dist=speed*age_sec, delta_lat/dist*cos(heading)/R*180/pi, delta_lng/dist*sin(heading)/(R*cos(lat))*180/pi, predicted lat/lng=smoothed+delta, original=smoothed before prediction, otherwise predicted false original=smoothed. Accuracy degrades +0.5*age_sec. EMA with accuracy decay (good accuracy weights more), exponential decay time decay, prediction exact delta 100m north/east.

- **Confidence**: high if (acc<=5 age<=5000) OR (acc<=10 age<=10000), medium if acc<=25 age<=20000 else low, snapped road_dist<=10 upgrades medium->high when acc<=25 and low->medium when acc<=40 age<=15000, no upgrade when road_dist>10, outlier>2 high->medium, >5 low regardless, age>30000 low, accuracy>50 low, not snapped acc>25 age>10000 and (acc>40 or age>15000) -> low, confidence low when age>30000 even if snapped high accuracy, with outlier_count 2 still high, 3 medium, 5 medium not low, 6 low.

- **Validate Pickup/Dropoff**: priority order exhaustive chain (first matching wins):
  1 out_of_geofence (zones --zones else pickup_zones.json/dropoff_zones.json, active filtered by now if provided else all, if pickup outside and active zones exist → valid false reason out_of_geofence exit1, ok reason literal "ok" when valid)
  2 stale if estimate age>30000
  3 low_accuracy if estimate accuracy>50
  4 off_road if roads provided and vehicle not snapped
  5 moving if vehicle speed >=5 (dropoff >=10)
  6 road_mismatch if both vehicle and pickup snapped but road_ids differ
  7 heading_mismatch if same road, vehicle speed>1, bearing from vehicle to pickup angular diff>90 and distance>10
  8 too_far if Haversine distance vehicle->pickup >100 (dropoff >150)
  9 otherwise ok valid true
  Output valid bool reason string distance_m confidence age_ms accuracy snapped road_id pickup_road_id plus dropoff_road_id alias containing same value as pickup_road_id for compatibility. Exit 0 if valid, 1 if invalid, 3 if not found, 2 if invalid args/zones/roads/lat/lng. Boundaries: exact 100m boundary ok, same location ok, speed leniency boundary for dropoff, priority: out_of_geofence beats all, low_accuracy beats heading_mismatch/moving/off_road/road_mismatch/too_far, stale beats moving, off_road beats moving/road_mismatch, moving beats heading_mismatch/road_mismatch/too_far, etc.

- **Batch Enhanced for Step2**: inherits Step1 atomicity zones before stale applied excludes skipped. Must handle low_accuracy and outlier: parse all first validate vehicle_id/lat/lng/timestamp/accuracy/speed/heading same as update, low_accuracy filter before zones (pipeline low_accuracy→zones→stale→outlier), if accuracy>100 treated as rejected skip no count no distance/history outlier_count unchanged batch continues, zones check if default zones.json exists per op timestamp if any would be out_of_zone fail whole batch exit2 even if stale/low_accuracy/outlier, stale skip no count, outlier six conditions same as update increment outlier_count persistently skip no count no distance/history batch continues, delete ops 2 fields exactly count as applied, after validation apply sequential simulated state single atomic write batch_ok <applied> where applied excludes stale/low_accuracy/outlier.

- **Crash Consistency Gate**: same as Step1 – corrupt backup with .corrupt.<nanosec> integer suffix distinct, stale tmp ignored and cleaned, backup contains original content, truncated file corruption path.

- **Old DB Migration**: old DB without history/total_distance/outlier_count must be auto-migrated: missing history empty, distance 0, outlier_count 0. Whitespace-only empty store, array [] or literal null corrupt exit4 with backup.

## Latest Validation (oracle)

Built from `steps/*/solution/solve.sh` at `/app/src` go 1.22 GOTOOLCHAIN=local.

- **Step1: 82/82 PASS (13s)** – hardened from 58 too easy:
  - Base validation boundaries: vehicle_id length 1/64/65 dash/underscore allowed special chars invalid, speed 50 boundary, heading 360 exclusive, accuracy 0 valid, timestamp integer rejection 1000.0/1e3/0x3e8/hex/negative, total_distance not increment on out_of_zone and history not include, batch mixed update+delete order semantics, batch empty field defaults all combos 5 minimal 6 empty accuracy 7 empty speed 8 empty heading >8 fail, near radius 0 exact and 10m vs 12m for 0.0001deg, geofence circle exact radius 1000m inside just beyond outside, zones time inclusive from/to same zone inclusive both, only_from and only_to at boundary from-1/to+1 inactive, list pagination offset-then-limit exact order variations, history last==current after stale, parent dirs deeply nested /a/b/c/d/e/f, multiple corrupt backups distinct nanosec suffixes, batch zones before stale still fails, antimeridian unwrapping 179 to -179 is 2 deg wide, mixed roads start/end legacy, interior points vs endpoints, all segments closest, large scale 800 vehicles near under 3s and list with zones and roads 200 vehicles, corrupt backup mandatory with integer nanosec suffix containing original content and truncated file path and stale tmp ignored and cleaned and atomic cleans tmp and no corrupt on success and canonicalization, vehicle_id boundaries, exact boundaries, timestamp rejection, total_distance not increment on out_of_zone, batch mixed, empty defaults all combos, radius boundary exact, circle exact radius, time boundary inclusive, list pagination exact order, history last==current after stale, parent dirs deeply nested, multiple backups distinct nanosec, batch zones before stale.
  - **Hardening**: re-added antimeridian (2 tests), mixed road legacy (1 test), mandatory corrupt backup gate (8 tests), batch zones before stale, mixed update+delete, empty defaults all combos, radius exact, circle exact radius, deeply nested parent dirs, multiple distinct nanosec backups – previously removed to ease but now added back to make harder since 58 was too easy.

- **Step2: 138/138 PASS (8s)** – hardened from 79 too easy:
  - All Step1 compat 82 tests still passes.
  - Core: outlier six conditions with boundaries, low_accuracy, speed cap, roads closest among segments and heading-aware no fallback comprehensive with opposite allowed and 3 heading cases and close road filtered by heading farther matching wins, EMA weighted smoothing with accuracy decay and exponential decay time decay and smoothing time decay, prediction exact delta 100m north/east, accuracy degradation +0.5*age_sec, confidence upgrades: medium->high when snapped road_dist<=10 acc<=25, low->medium when acc<=40 age<=15000, no upgrade when road_dist>10, demotion by outlier_count exactly 3 high->medium and exactly 6 low regardless off-by-one 2 still high 5 medium not low, age>30000 low even if snapped high accuracy, accuracy>50 low.
  - **Pickup/Dropoff priority exhaustive chain**: out_of_geofence beats low_accuracy/stale/off_road/moving/road_mismatch/heading_mismatch/too_far, low_accuracy beats heading_mismatch/moving/off_road/road_mismatch/too_far, stale beats moving, off_road beats moving/road_mismatch, moving beats heading_mismatch/road_mismatch/too_far, plus exact boundaries 100m ok, same location ok, dropoff speed leniency >=10 vs pickup >=5, too_far 100 vs 150, heading_mismatch same road speed>1 bearing diff>90 dist>10, road_mismatch both snapped diff road_id.
  - **Batch enhanced**: low_accuracy and outlier in batch with zones before stale, distance not increment on outlier/low_accuracy, history not include, outlier_count persistence across restart and drives confidence after restart and separation low_accuracy and outlier mixed and double-trigger counts one.
  - **Hardening**: added back 59 tests that were removed to ease from 138 to 79: all priority exhaustive chain, prediction exact delta, EMA weighting, confidence no upgrade, heading-aware comprehensive, multiple backups, low_accuracy batch gate, EMA decay, etc. Since 79 was too easy, now 138 should be harder.

## Agent Failure Analysis

Step1 was too easy at 59 (0% fail, no discrimination). Hardened to 67 with crash-consistency backup gate, then to 81/84 with obscure boundaries → too hard (0% pass, saturated crash-consistency). Eased to 63 removing 8 backup tests which gated all, then to 60 removing antimeridian/mixed, then to 43 medium removing holes/circles/time → too easy. Balanced to 58 hard removing antimeridian/mixed but keeping holes/circles/time → still too easy per latest online. Now hardened to 82 extreme-hard: re-added antimeridian (2 tests), mixed roads (1 test), mandatory backup gate (8 tests with distinct nanosec suffixes and contains original and truncated and stale tmp handling), batch zones before stale, mixed update+delete, empty defaults all combos, radius exact, circle exact radius, deeply nested parent dirs, multiple distinct nanosec backups. This should give healthy spread.

Step2 was 79 too easy after easing from 138 (0% pass). Now hardened to 138 with full priority chain and confidence upgrades.

## Structure

- `environment/Dockerfile` – ubuntu:24.04 installs golang-go, ca-certificates, python3/pip, pytest 8.4.1, creates /app/src, /app/data/roads.json sample with polyline + mixed segment seg_old (hardened), empty zones [] default, pickup_zones and dropoff_zones empty
- `steps/1_step_one/` – tracking hardened extreme with antimeridian unwrapping, mixed roads, mandatory backup .corrupt.<nanosec> integer suffix distinct and contains original and truncated and stale tmp and canonicalization and deeply nested parent dirs and batch zones before stale and mixed update+delete and empty defaults all combos and radius exact and circle exact radius and time boundaries inclusive only_from/to and divergence intuitive allow-all when inactive – 82 tests 13s
- `steps/2_step_two/` – accuracy hardened extreme with full priority exhaustive chain, prediction exact delta, EMA weighting, confidence no upgrade, heading-aware comprehensive, outlier_count family persistence boundaries 3/6 non-increment separation mixed double-trigger demotion chain, batch low_accuracy/outlier handling, crash backup – 138 tests 8s

## Run Locally

```bash
bash steps/1_step_one/solution/solve.sh
pytest steps/1_step_one/tests/test_outputs.py -v
bash steps/2_step_two/solution/solve.sh
pytest steps/2_step_two/tests/test_outputs.py -v
```

Binary: `go build -o locationctl .` from `/app/src` module `locationservice` stdlib only.
