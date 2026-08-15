# codimango/location-accuracy

Multi-turn Go task for ride-sharing vehicle location tracking (Uber-like) with extreme accuracy improvements to avoid inaccurate pickup and dropoff.

## Overview

### Step 1: Vehicle Location Tracking Service (1_step_one, 71 tests, extreme-hard)

Build `locationctl` in Go at `/app/src`, module `locationservice`, stdlib only.

Core features:
- **Persistence**: JSON map vehicle_id to Location, atomic writes via `<db>.tmp.<pid>` then rename, no leftover temp files. Parent directories must be created. Empty or whitespace-only file is an empty store. Corrupt files (unparsable JSON, array `[]`, literal `null`, truncated file) must exit 4 and create a backup `<db>.corrupt.<nanosec>` with integer nanosecond suffix containing original content. Stale tmp files must be ignored and cleaned up on next successful write.
- **Validation**: vehicle_id regex `^[A-Za-z0-9_-]{1,64}$`, lat [-90,90], lng [-180,180], reject NaN/Inf variations, timestamp must be integer string (reject `1000.0`, `1e3`, `0x3e8`), accuracy >=0 default 10, speed 0-50 default 0 >50 invalid exit 2, heading [0,360) default 0.
- **History and Distance**: total_distance is Haversine sum (R=6371000) over accepted updates only, not on stale or out_of_zone. History keeps up to 10 last accepted locations sorted ascending, includes current as last entry.
- **Zones**: polygon >=3 points, holes each >=3, circles radius 0< <=1e6, both polygon+circle present invalid, active_from/to time filtering, antimeridian unwrapping so 179 to -179 is 2 degrees wide, edge and vertex count as inside, holes outside, circle distance <= radius. For update, use `--zones` if given else default `/app/data/zones.json` if exists. Active zones filtered by timestamp, if active non-empty location must be inside at least one else out_of_zone exit 3. For near/list, zones optional filtered by --now if provided else all.
- **Roads**: mixed polyline `points` >=2 and legacy `start/end`, invalid entry exit 2 when used. Snapping uses equirectangular projection with lat_ref = query lat, checks all segments with clamped t in [0,1], distance <=50m snapped. Must check interior points not just endpoints.
- **Commands**: update prints without history but with total_distance, get --verbose full, list sorted id asc with since/until inclusive and zones/roads filters and pagination offset then limit (limit 0 -> [], offset>len -> []), near with lat/lng/radius [0,50000] plus accuracy-max, speed-min, now with age>30000 stale exclusion only when now provided unless --include-stale, distance <=radius sorted distance asc then vehicle_id asc, track --from --to paginated, delete prints deleted even if not found, stats live/total_updates/total_distance/avg_accuracy, batch tab-delimited variable 5-8 fields empty means default, >8/<5 fail exit2, all-or-nothing atomic with zones check before stale, stale skipped, prints batch_ok <applied>, clear, geofence-check returns first matching zone by file order.

### Step 2: Improve Location Accuracy (2_step_two, 126 tests, extreme-hard, inherit_prior_session true)

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

- **Step1: 71/71 PASS (13s)** – eased from 84 too hard:
  - Base validation, total_distance, history 10, stale handling, integer timestamp, NaN/Inf, ID regex, batch atomic with zones before stale, near include-stale, zones active vs inactive, geofence, roads interior vs endpoint and all segments.
  - **Crash-consistency gate (saturated)**: corrupt backup `.corrupt.<nanosec>` integer suffix for invalid JSON, array, null, truncated; backup contains original; stale tmp ignored and cleaned; atomic no tmp leftover.
  - **Time-window seam (the only seam that ever fired)**: inclusive bounds ts==from and ts==to both active in same test, zones with only from and only to at boundary, divergence --now vs update timestamp: update uses its own ts, list/near/geofence-check use --now, so vehicle outside accepted at ts=500 when inactive, but list --now=1500 excludes it.

- **Step2: 126/126 PASS (7s)** – eased from 138 too hard:
  - All Step1 compat still passes (71 tests).
  - Outlier six conditions, low_accuracy, speed cap, roads closest among segments and heading-aware no fallback (no fallback), EMA exp decay, prediction delta, confidence chain with outlier_count and snapped upgrade, validate-pickup/dropoff priority.
  - **Outlier_count family (strengthened, the only working discriminator)**: persistence across restart (DB reload, get --verbose), boundaries exactly 3 (>2→medium) and 6 (>5→low) with off-by-one checks 2 still high, 5 medium not low, non-increment for low_accuracy and stale, separation mixed, double-trigger counts as one.
  - **Batch handling clarified**: spec now explicitly states batch handles low_accuracy (skip, no distance, outlier_count unchanged) and outlier (increment count, skip location) with zones before stale still applies. Removes unstated-rule gate that was gating opus entirely (test_low_accuracy_in_batch removed).
  - Removed 12 hardest extra tests: prediction exact delta, EMA weighting, confidence no upgrade when road_dist>10, exhaustive priority chain, multiple corrupt backups, etc.

## Agent Failure Analysis

Step1 was too easy (59 tests → 0% fail). First hardening to 67 added crash-consistency backup and stale tmp handling. Second hardening to 81 added many obscure boundaries (vehicle_id length, speed 50.0001, timestamp float, deep dirs, multiple backups, etc.) → too hard. Trimmed to 71: keep core crash-consistency (8 tests) plus time-window seam which is the only pair that ever fired (geofence_check_time_based + zones_time_boundary_inclusive). Extended time-window exactly: inclusive both bounds in same test, only-from and only-to zones at boundary, divergence --now vs update timestamp (update uses its own ts, list/near use --now). Don't add more crash-consistency (saturated).

Step2 was only outlier_count working. Extended to 126 with restart persistence, boundaries 3 (>2→medium) and 6 (>5→low), non-increment for low_accuracy/stale. Second extension to 138 added many hard extras (prediction exact delta, EMA weighting, exhaustive priority chain, multiple backups) → too hard, plus test_low_accuracy_in_batch was gating opus entirely as unstated-rule gate. Eased to 126 by removing 12 hardest extra tests including prediction exact delta, EMA weighting, confidence road_dist>10, exhaustive chain, multiple backups, and the low_accuracy batch gate. Spec clarified for batch: low_accuracy and outlier handling explicitly stated for batch path (low_accuracy skip, outlier increment count, distance not added, zones before stale), so no longer unstated.

## Structure

- `environment/Dockerfile` – ubuntu:24.04 installs golang-go, python3/pip, pytest 8.4.1, creates /app/src, /app/data/roads.json sample (polyline + mixed segment sf_market seg_old), empty zones [] default
- `steps/1_step_one/` – tracking with crash-consistency + time-window boundaries (inclusive, only-from/only-to, now vs update divergence), 71 tests
- `steps/2_step_two/` – accuracy with outlier_count family (persistence, boundaries 3/6, non-increment), batch clarified, 126 tests

## Run Locally

```bash
bash steps/1_step_one/solution/solve.sh
pytest steps/1_step_one/tests/test_outputs.py -v
bash steps/2_step_two/solution/solve.sh
pytest steps/2_step_two/tests/test_outputs.py -v
```

Binary: `go build -o locationctl .` from `/app/src` module `locationservice` stdlib only.
