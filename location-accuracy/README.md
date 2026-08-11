# codimango/location-accuracy

Multi-turn Go extreme-hard task for ride-sharing vehicle location tracking (Uber-like) with accuracy improvements to avoid inaccurate pickup/dropoff.

## Overview
- **Step1 (1_step_one, 78 tests, extreme-hard, was 59 too easy)**: Build `locationctl` in Go at `/app/src` module `locationservice` stdlib only, `--db` JSON map vehicle_id→Location atomic `.tmp.<pid>`+rename no leftover, ID regex `^[A-Za-z0-9_-]{1,64}$`, lat [-90,90] lng [-180,180] NaN/Inf reject, timestamp int64≥0 **must be integer string** (reject 1000.0, 1e3, 0x3e8), accuracy≥0 default10 speed 0-50 default0 >50 invalid exit2 heading [0,360) default0, total_distance Haversine R=6371000 sum over accepted only not on stale/out_of_zone, history up to 10 asc includes current last equals current. Zones polygon≥3 holes≥3 circles radius 0<≤1e6 both present invalid, active_from/to filtering, antimeridian unwrapping 179→-179 =2°, edge/vertex inside, holes outside, circle ≤radius exact radius inside, for update default `/app/data/zones.json` if exists else --zones active filtered by ts, active non-empty must be inside ≥1 else out_of_zone stdout/stderr accepted exit3, for near/list --zones optional filtered by --now else all. Roads mixed points≥2 + start/end id non-empty invalid→exit2 when used, snapping equirectangular lat_ref=query lat closest clamped t∈[0,1] distance ≤50m must check **all segments and interior points not just endpoints**. Commands: update prints without history but with total_distance, get --verbose full, list sorted id asc --since/--until inclusive, zones/roads filters, pagination **offset then limit** limit0→[], near --lat --lng --radius [0,50000] accuracy-max speed-min now age>30000 stale excluded only when now provided unless --include-stale, distance ≤radius sort distance asc then vehicle_id asc distance tie id asc, track --from --to paginated, delete `deleted` even if not found, stats live/total_updates/total_distance/avg_accuracy, batch tab-delimited variable 5-8 fields empty→default >8/<5 fail exit2 delete 2 fields, **all-or-nothing atomic** zones check before stale (zone check applies even for stale ops), stale skipped print batch_ok <applied>, clear cleared, geofence-check {"inside":bool,"zone_id":string} **first matching by file order**.

- **Step2 (2_step_two, 66 tests, extreme-hard, good enough, inherit_prior_session true)**: Backward compat. Adds outlier_count, low accuracy >100 → low_accuracy exit3 no change outlier_count unchanged. Outlier 6 conditions (main discriminator): teleport dt<300 dist>1000 implied>50 old<50 new<50, heading flip both >10 angular>120 dist<500, median deviation history≥2 last 3 speeds median |implied-med|>30 implied>30 dist>500, accel spike |Δspeed|/dt>15 dist<300, accuracy spike new>75 new>old*2+30, speed vs implied implied>80 new.speed<2 dist>1000 dt<60 → outlier_count++ persist print outlier exit3 no location change. Roads heading-aware when speed>1 filter if min(diff(heading,roadBear), diff(heading,roadBear+180))>45 **no fallback**. estimate EMA last 5 weight 1/(acc+1)*exp(-age/10000) prediction if age>0 ≤30000 speed>0 delta formulas accuracy degrades +0.5*age_sec, confidence high acc≤5 age≤5000 OR acc≤10 age≤10000 medium acc≤25 age≤20000 else low, snapped road_dist≤10 upgrade medium→high low→medium, outlier>2 high→medium >5 low age>30000 low acc>50 low not snapped acc>25 age>10000 and (acc>40 or age>15000)→low. validate-pickup/dropoff priority out_of_geofence (zones --zones else pickup_zones.json/dropoff_zones.json), stale age>30000, low_accuracy acc>50, off_road roads not snapped, moving ≥5 (dropoff ≥10), road_mismatch both snapped diff road_id, heading_mismatch same road speed>1 bearing diff>90 dist>10, too_far >100 (dropoff >150) else ok reason literal "ok" fairness, output valid reason distance_m confidence age_ms accuracy snapped road_id pickup_road_id + dropoff_road_id alias.

## Latest Validation (oracle)
Built from `steps/*/solution/solve.sh` at `/app/src` go 1.22 GOTOOLCHAIN=local.

- **Step1: 78/78 PASS (17.74s)** – was 59 too easy, hardened to 78 with 19 new discriminators. Breakdown:
  - Basic: build, go.mod no external, help contains all commands, update/get, total_distance tracking and not increment on stale
  - Validation: lat/lng NaN/Inf/Infinity case-insensitive, ID empty/space/65, timestamp integer string (1000.0, 1e3, 0x3e8 invalid), timestamp<0 invalid, speed>50 heading 360 invalid accuracy negative NaN/Inf
  - History: 10 max sorted asc, last equals current, stale same timestamp `stale` stdout
  - Zones: polygon, holes, circle, antimeridian (179→-179 2° wide, point 180 inside, 0 outside), edge/vertex inside, time-based active_from/to, invalid file polygon<3, both polygon+circle, default file fallback
  - Roads: mixed polyline+segment, interior snap not endpoint (0,0)-(0,1) query 0.0001,0.5 ~11m <50m but endpoints ~55km), all segments not just first (polyline 0,0-0,1-1,1 second segment closest), invalid entry points<2 → exit2
  - List: sorted id asc, pagination edge limit0→[] offset>len→[], since/until inclusive, offset then limit order (offset1 limit2 → veh_1,veh_2 not veh_0,veh_1), zones filter polygon/circle, roads filter, with now zones active vs inactive empty when no active, with zones+roads combined
  - Near: basic radius, pagination sort distance asc tie id asc, radius 0, stale only when now provided, include-stale flag (now excludes stale, include-stale includes), accuracy-max+speed-min combined (must filter both), zones+roads combined, Haversine accuracy distance_m
  - Track/distance/stats: track from/to paginated inclusive, distance total_distance_m, stats live/total_updates/avg_accuracy
  - Batch: success variable fields 4 fields minimal defaults accuracy10, empty defaults "", 6 fields partial, atomicity fail keeps DB unchanged, stale skipped not fail, whitespace empty ignored, **atomic with zones out_of_zone** (inside+outside → exit2 DB unchanged), **zones check before stale still fails** (stale timestamp but out_of_zone → exit2), mixed update/delete atomic
  - Geofence-check: basic, holes, circle exact radius inside (≤), time-based with --now, antimeridian, **file order first matching**
  - Persistence: parent dirs creation /tmp/a/b/c/db.json, atomic no tmp leftover, 0-byte/whitespace empty → empty store, array → corrupt 4, unparsable → corrupt 4
  - Performance: large scale 800 vehicles near <3s, list with zones+roads 200 vehicles, near with zones+roads 500 vehicles <2s (O(n log n) not O(n²))

- **Step2: 66/66 PASS (5.24s)** – good enough, unchanged discriminators. Outlier teleport, heading flip, median deviation, accel spike, accuracy spike, speed vs implied, low accuracy filter, zones still extreme, heading-aware roads no fallback (all candidates filtered → no snap), EMA exp decay, prediction delta lat/lng, confidence chain with outlier_count and snapped upgrade, validate-pickup/dropoff reason literals "ok","moving","off_road" priority, out_of_geofence with pickup_zones.json/dropoff_zones.json defaults, road_mismatch, heading_mismatch, too_far 100/150, batch zones before stale preserved, old DB migration, large scale near/estimate <3s. Multi-turn inherits Step1 OK.

## Agent Failure Analysis (why Step1 was too easy, now hardened)

**Before hardening (59 tests)**: Most agents passed 80%+ because tests missed:
- integer timestamp requirement → agents accepted 1000.0 or 1e3 and got exit0 instead of exit2
- batch atomic with zones → agents partially applied first op before detecting out_of_zone → DB corrupted
- batch zones before stale → agents checked stale first and skipped out_of_zone stale op → incorrectly exit0
- near include-stale → agents ignored --include-stale flag → stale vehicles never included
- near accuracy-max+speed-min combined → agents filtered only one dimension → extra vehicles returned
- list offset then limit order → agents did limit then offset → offset1 limit2 returned veh_0,veh_1 instead of veh_1,veh_2
- list with now zones inactive → agents returned vehicles even when no active zones (should be [] when zones file non-empty but none active)
- geofence file order → agents returned last matching zone instead of first
- roads interior vs endpoint → endpoint-only snapping: distance to endpoints 55km >50m → incorrectly not snapped, missing vehicles in list/near roads filter
- roads all segments → agents checked only first segment → vehicles near second segment missed
- total_distance stale handling → agents incremented distance even for stale/rejected updates
- history last equals current → agents maintained history without including current as last or not sorted asc

**After hardening (78 tests)**: These 19 new tests add the discriminators above. Reference solution (solve.sh) already implements correct logic:
- parseIntArg via ParseInt base10 rejects float/scientific/hex
- batch first parses all, validates zones per op timestamp before stale, then all-or-nothing apply
- near handles now/include-stale, accuracy-max, speed-min, zones active filtered by now, roads interior snap via equirectangular + clamped t
- list offset then limit, since/until inclusive, zones active filtered by now else all, empty when no active but zones exist
- geofence first matching by file order
- roads closestPointOnSegment with t∈[0,1] over all segments
- stats total_updates = sum len(history), avg_accuracy = sum accuracy / live

Step1 now fails naive agents that previously passed. Step2 (66 tests) already had strong discriminators for outlier 6 conditions interacting with history/accuracy, heading-aware no fallback, EMA weighting, confidence downgrade with outlier_count>2 and >5, reason literal "ok" priority chain.

## Structure
- `environment/Dockerfile` – ubuntu:24.04 installs golang-go, python3/pip, pytest 8.4.1, creates /app/src, /app/data/roads.json sample (polyline + mixed segment sf_market seg_old), empty zones [] default
- `steps/1_step_one/` – basic tracking extreme-hard 78 tests
- `steps/2_step_two/` – accuracy extreme-hard 66 tests

## Run Locally
```bash
bash steps/1_step_one/solution/solve.sh
pytest steps/1_step_one/tests/test_outputs.py -v
bash steps/2_step_two/solution/solve.sh
pytest steps/2_step_two/tests/test_outputs.py -v
```
Binary: `go build -o locationctl .` from `/app/src` module `locationservice` stdlib only.
