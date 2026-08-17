# codimango/location-accuracy

Multi-turn Go task for ride-sharing vehicle location tracking (Uber-like) with extreme accuracy improvements to avoid inaccurate pickup and dropoff.

## Overview

### Step 1: Vehicle Location Tracking Service (1_step_one, 95 tests, extreme-hard – hardened for too-easy)

Build `locationctl` in Go at `/app/src`, module `locationservice`, stdlib only.

Core features (hardened from 58 too-easy to 95 extreme-hard):
- **Persistence**: JSON map vehicle_id to Location, atomic writes via `<db>.tmp.<pid>` then rename, no leftover tmp. Parent dirs deeply nested `/a/b/c/d/e/f`. Empty or whitespace-only = empty store. Corrupt files (unparsable JSON, array `[]`, literal `null`, truncated, extra garbage like `{"valid":1} garbage`) must exit 4 and mandatory backup `<db>.corrupt.<nanosec>` integer nanosec distinct, containing exact original. Stale tmp ignored and cleaned on next write.
- **Validation**: vehicle_id regex `^[A-Za-z0-9_-]{1,64}$`, length boundaries 1/64/65, dash/underscore allowed, spaces/@/./ invalid, empty invalid. lat [-90,90], lng [-180,180], reject NaN/Inf/Infinity case-insensitive. timestamp int64 >=0 integer string only, reject `1000.0`, `1e3`, `0x3e8`, negative. accuracy >=0 default 10, speed [0,50] default 0 >50 invalid exit2, heading [0,360) default 0 360 exclusive.
- **History and Distance**: total_distance Haversine R=6371000 over accepted only, not stale/out_of_zone. History up to 10 last accepted sorted asc, includes current as last, after stale last==current. total_distance not increment on out_of_zone.
- **Zones**: polygon >=3 valid points, holes each >=3, multiple holes, overlapping polygons first match file order, circles radius 0< <=1e6 (0 invalid, 1e6+1 invalid), both polygon+circle invalid, active_from/to inclusive bounds ts==from and ts==to active, only-from onward, only-to up to, from-1/to+1 inactive, from>to invalid exit2. Must handle antimeridian unwrapping 179 to -179 is 2 deg wide not 358. Edge/vertex inside, hole edge inside hole => outside, holes outside, circle <=radius exact radius inside just beyond outside, circle with time window.
- **Filtering**: update uses --zones if given else default `/app/data/zones.json` if exists, filter active by timestamp, if active non-empty must be inside else out_of_zone exit3, if no active allow all. list/near/geofence-check: --zones optional filtered by --now if provided else all, intuitive: if no active at now, list/near allow all (no filtering) rather than [], geofence-check outside. Divergence: update at 500 when zone from=1000 (no active → outside close point 10.4,5 allowed), later list --now 1500 excludes outside, list --now 500 includes (allow-all). Custom --zones overrides default.
- **Roads**: mixed formats polyline `points` >=2 and legacy `start/end` both valid, any invalid entry exit2. Snapping equirectangular R=6371000 lat_ref=query lat, all segments t in [0,1], distance <=50m snapped, interior vs endpoint must check interior, closest among many segments.
- **Commands**: update prints without history but with total_distance, get --verbose full, list sorted id asc with since/until inclusive and zones/roads filters combined and pagination offset then limit (limit0=[], offset>len=[]), near lat/lng/radius [0,50000] plus accuracy-max speed-min together, now age>30000 stale exclusion unless include-stale, distance <=radius sorted distance asc then vehicle_id asc tie-breaking, radius 0 exact, track --from --to paginated with limit/offset, delete prints deleted even if not exist, stats live/total_updates/total_distance_m/avg_accuracy after clear and reupdates, batch tab-delimited 5-8 empty means default >8/<5 fail atomic zones before stale still fails when stale op out_of_zone mixed update+delete order matters empty defaults all combos whitespace ignored batch_ok <applied> performance 100 ops <3s, clear, geofence-check first matching zone file order.

Additional hard edge cases for too-easy: multiple holes, overlapping first match, circle with time, hole edge outside, interior vs endpoint snapping, closest among segments, all filters combined, accuracy-max+speed-min together, track pagination, stats after clear, batch 100 ops, corrupt extra garbage, zones default vs custom precedence, antimeridian with hole, deeply nested parent dirs 6 levels, etc.

### Step 2: Improve Location Accuracy (2_step_two, 152 tests, extreme-hard – hardened, inherit_prior_session true)

Backward compatible with Step1, adds:

- **Low Accuracy Filter**: accuracy>100 → print low_accuracy exit3, no DB change, outlier_count unchanged. Separation load-bearing not increment outlier_count.
- **Outlier Detection – Six Conditions** thresholds matter:
  Teleport dt<300 && distance>1000 && implied>50 && old.accuracy<50 && new.accuracy<50
  Heading Flip both speeds>10 angular diff>120° distance<500, exact 120 not outlier
  Median Deviation history>=2 median of last up to 3 implied speeds |implied-med|>30 && implied>30 && distance>500
  Acceleration Spike |Δspeed|/dt>15 && distance<300 exact boundary
  Accuracy Spike new>75 && new>old*2+30 boundary 75
  Speed vs Implied implied>80 && new.speed<2 && distance>1000 && dt<60
  On outlier increment outlier_count persistently print outlier exit3 no location change, multiple true counts as one, must not increment for low_accuracy or stale, stale dt==0 not outlier.

- **Outlier Count Family**: survive process restart persisted to DB reload get --verbose, confidence reflects persisted count after restart, demotion chain boundaries exactly 3 (>2 high→medium) and exactly 6 (>5 low regardless) off-by-one 2 still high, 3 medium, 5 medium not low, 6 low, non-increment for low_accuracy/stale separation mixed double-trigger counts one.

- **Roads heading-aware no fallback**: when headingAware true (estimate and validate-pickup/dropoff when --roads provided and speed>1) candidate filtered if min(diff(heading,roadBearing), diff(heading,roadBearing+180))>45, if all filtered not snapped no fallback, opposite allowed, closest among segments, mixed format, invalid exit2.

- **Estimate**: EMA last 5 entries weight w=(1/(accuracy+1))*exp(-age_i/10000) age_i=now-history_i.timestamp_ms weighted average lat/lng smoothed base, prediction if age>0 && <=30000 && speed>0 dist=speed*age_sec delta_lat/dist*cos(heading)/R*180/pi delta_lng/dist*sin(heading)/(R*cos(lat))*180/pi predicted lat/lng=smoothed+delta original=smoothed before prediction otherwise predicted false original=smoothed, accuracy degrades +0.5*age_sec, EMA weighted accuracy decay good accuracy weights more.

- **Confidence**: high if (acc<=5 age<=5000) OR (acc<=10 age<=10000), medium if acc<=25 age<=20000 else low, snapped road_dist<=10 upgrade medium->high when acc<=25 and low->medium when acc<=40 age<=15000, no upgrade when road_dist>10, outlier>2 high->medium >5 low regardless boundaries 2 still high 3 medium 5 medium not low 6 low, age>30000 low even if snapped high accuracy, accuracy>50 low, not snapped acc>25 age>10000 and (acc>40 or age>15000) low.

- **Validate Pickup/Dropoff**: priority exhaustive chain first matching wins:
  1 out_of_geofence zones --zones else pickup_zones.json/dropoff_zones.json active filtered by now if provided else all, if outside and active exist valid false reason out_of_geofence exit1, ok literal "ok" when valid
  2 stale age>30000
  3 low_accuracy acc>50
  4 off_road roads not snapped
  5 moving pickup >=5 dropoff >=10 boundaries 4.9 valid 5.0 invalid pickup, 9.9 valid 10.0 invalid dropoff
  6 road_mismatch both snapped diff road_id with multiple roads
  7 heading_mismatch same road speed>1 bearing from vehicle to pickup diff>90 dist>10 exact 90 not mismatch
  8 too_far Haversine >100 pickup >150 dropoff exact 100m boundary ok same location ok
  9 otherwise ok valid true
  Output valid reason distance_m confidence age_ms accuracy snapped road_id pickup_road_id plus dropoff_road_id alias. Exit 0 valid 1 invalid 3 not found 2 invalid args/zones/roads/lat/lng.

- **Batch Enhanced Step2**: inherits Step1 atomicity zones before stale applied excludes skipped. Must handle low_accuracy and outlier: parse all first validate vehicle_id/lat/lng/timestamp/accuracy/speed/heading same as update, low_accuracy filter before zones pipeline low_accuracy→zones→stale→outlier if accuracy>100 rejected skip no count no distance/history outlier_count unchanged batch continues, zones check if default zones.json exists per op timestamp if any out_of_zone fail whole batch exit2 even if stale/low_accuracy/outlier, stale skip no count, outlier six conditions increment outlier_count persistently skip no count no distance/history batch continues including double-trigger counts one and low_accuracy+outlier mixed, delete ops 2 fields exactly count as applied, after validation apply sequential simulated state single atomic write batch_ok <applied> where applied excludes stale/low_accuracy/outlier.

- **Crash Consistency Gate**: same as Step1 – corrupt backup with .corrupt.<nanosec> integer suffix distinct, stale tmp ignored and cleaned, backup contains original, truncated file corruption path, old DB migration without history/total_distance/outlier_count auto-migrated whitespace-only empty store array [] or literal null corrupt exit4 with backup.

- **Old DB Migration**: old DB without history/total_distance/outlier_count must be auto-migrated, whitespace-only empty, array [] or null corrupt.

## Latest Validation (oracle)

Built from `steps/*/solution/solve.sh` at `/app/src` go 1.22 GOTOOLCHAIN=local.

- **Step1: 95/95 PASS (13s)** – hardened from 58 too-easy:
  Re-added antimeridian unwrapping 179 to -179 is 2 deg wide, mixed roads legacy start/end, mandatory backup gate with distinct nanosec suffixes and contains original and truncated and stale tmp and atomic cleans and canonicalization, deeply nested parent dirs /a/b/c/d/e/f, batch zones before stale still fails, mixed update+delete same vehicle order, empty defaults all combos, radius 0 exact, circle exact radius 1000m inside just beyond outside, time boundaries inclusive same zone both inclusive only_from/to at boundary from-1/to+1 inactive, list pagination offset-limit exact order, history last==current after stale, multiple holes per zone, overlapping polygons first match, circle with time window, hole edge outside, interior vs endpoint snapping, closest among many segments, all filters combined list with since/until+zones+roads+accuracy/speed, accuracy-max+speed-min together, track pagination, stats after clear, batch 100 ops performance, corrupt extra garbage, zones default vs custom precedence, etc. – previously removed to ease but now added back to make harder since 58 was too easy.

- **Step2: 152/152 PASS (10s)** – hardened from 79 too-easy:
  All Step1 compat 95 tests still passes, plus full extreme features: polygon with holes, circles, time windows inclusive, antimeridian, mixed roads, heading-aware no fallback comprehensive, outlier six conditions with exact boundaries teleport 0.05deg 101sec, heading flip exact 120 vs 150, acceleration spike 30/1sec, accuracy spike 80 vs 10, confidence high exact boundaries age 5000 acc5 and low when age 30001, prediction exact delta north 50m, pickup moving boundary 4.9 valid 5.0 invalid, dropoff 9.9 valid 10.0 invalid, batch low_accuracy and outlier mixed, EMA weighted accuracy decay, road mismatch multi roads, old DB migration, large scale estimate 200 vehicles 50 estimates <5s. Added back 59 hardest plus 14 new boundary tests.

## Agent Failure Analysis

Step1 was 59 0% fail no discrimination, hardened to 81/84 too hard 0% pass saturated crash-consistency backup gate, eased to 63 removing 8 backup tests, then to 60 removing antimeridian/mixed, then to 43 medium removing holes/circles/time → too easy high pass. Balanced to 58 hard keeping holes/circles/time but no antimeridian/mixed → still too easy online. Now hardened to 95 extreme-hard: re-added antimeridian (2 tests), mixed roads (1 test), mandatory backup gate (8 tests distinct nanosec and contains original and truncated etc.), plus 14 new hard edge cases multiple holes, overlapping first match, circle with time, hole edge outside, interior vs endpoint, closest among many, all filters combined, accuracy+speed together, track pagination, stats after clear, batch 100 ops, corrupt extra garbage, zones default vs custom precedence.

Step2 79 too easy after easing from 138 0% pass, now 152 with full priority chain and boundaries.

## Structure

- `environment/Dockerfile` – ubuntu:24.04 installs golang-go, python3/pip, pytest 8.4.1, creates /app/src, /app/data/roads.json sample with polyline + mixed segment seg_old (hardened), empty zones [] default, pickup_zones dropoff_zones empty
- `steps/1_step_one/` – tracking hardened extreme with antimeridian, mixed roads, mandatory backup, multiple holes, overlapping first match, circle with time, hole edge, interior snapping, all filters combined, batch 100 ops, extra garbage, default vs custom precedence – 95 tests 13s
- `steps/2_step_two/` – accuracy hardened extreme with outlier exact boundaries teleport/heading flip/accel/accuracy spike, confidence high exact and low age 30001, prediction north, pickup moving boundaries, dropoff leniency, batch mixed, EMA decay, road mismatch multi, old DB migration, large scale estimate – 152 tests 10s

## Run Locally

```bash
bash steps/1_step_one/solution/solve.sh
pytest steps/1_step_one/tests/test_outputs.py -v
bash steps/2_step_two/solution/solve.sh
pytest steps/2_step_two/tests/test_outputs.py -v
```

Binary: `go build -o locationctl .` from `/app/src` module `locationservice` stdlib only.
