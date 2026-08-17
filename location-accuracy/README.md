# codimango/location-accuracy

Multi-turn Go task for ride-sharing vehicle location tracking (Uber-like) with extreme accuracy improvements to avoid inaccurate pickup and dropoff.

## Overview

### Step 1: Vehicle Location Tracking Service (1_step_one, 99 tests, extreme-hard – hardened, hole-edge clarified)

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

### Step 2: Improve Location Accuracy (2_step_two, 153 tests, extreme-hard – hardened, original_lat clarified per feedback, inherit_prior_session true)

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

## Latest online validation result

**Commit `ca823003` (HEAD, v0.39) · run 2026-08-17 · jobs 4937724 / 4937725 / 4937726 / 4937727 · AFTR run 9157610**

> **All gates green.** Validation passing, Agentic Full-Task Review **GOOD**, TBR
> 18/18 with no quality concerns. Status is `draft` — no revision is outstanding.

| Field | Value |
| --- | --- |
| `validationStatus` | **passing** — all 5 gates |
| Agentic Full-Task Review | **GOOD** — difficulty `GENUINELY_HARD`, all 13 rubrics PASS, secondary issues NONE |
| `tbdReviewStatus` | pass — **TBR 18/18**, all six axes 3, `quality_concern_titles` null |
| Difficulty classification | GOOD — Opus 150/384 tests (39%), oracle 114/120 (95%) |
| Quality dimensions | depth **3** / realism **3** / originality **3** |
| Novelty risk | **LOW** |
| Contamination | Not checked — repo not yet covered by the pipeline |
| Provenance | CLEAN |
| Structural checks | 10/10 PASS |
| `status` / `reviewStatus` | draft |

| Stage | Job | Result | Reward split |
| --- | --- | --- | --- |
| oracle | 4937726 | **3/3** | 3 × 1.00 |
| codex (`gpt-5.5`) | 4937725 | **7/10** | 7 × 1.00, 1 × 0.50, 2 × 0.00 |
| agent (opus `claude-opus-4-8`) | 4937727 | **3/10** | 3 × 1.00, 7 × 0.00 |
| metacode (avocado `avocado-5.14-code`) | 4937724 | **3/10** | 3 × 1.00, 6 × 0.50, 1 × 0.00 |

Pass/fail balance gate: **passed** — avocado not trivial (3/10) and ≥1 agent solved.

### Failure spread (all 17 non-1.00 trials, from downloaded `ctrf.json`)

| Test | Step | Count | opus | avocado | codex |
| --- | --- | --- | --- | --- | --- |
| `test_geofence_check_on_hole_edge_outside_v2` | **1** | 7 | 6 | 1 | 0 |
| `test_estimate_prediction_exact_delta_north_v2` | **2** | 7 | 0 | 6 | 1 |
| `test_zones_antimeridian` | 1 | 4 | 2 | 0 | 2 |
| `test_zones_out_of_zone_rejection` | 1 | 3 | 2 | 0 | 1 |
| `test_zones_holes` | 1 | 3 | 2 | 0 | 1 |
| `test_zones_circle` | 1 | 3 | 2 | 0 | 1 |
| `test_zones_default_file` | 1 | 3 | 2 | 0 | 1 |
| `test_geofence_check_antimeridian` | 1 | 1 | 0 | 0 | 1 |

Reading:
- **Both steps discriminate, each through its own narrow test.** Step 1 is carried by
  `test_geofence_check_on_hole_edge_outside_v2` (hole-edge points must count as
  outside the zone); step 2 by `test_estimate_prediction_exact_delta_north_v2`
  (`original_lat`/`original_lng` must be the pre-prediction smoothed value). Neither
  step is load-bearing on a timing or hardware-dependent assertion.
- **Failures are surgical, not "ran out of time".** 13 of the 17 failing trials fail
  **exactly one test** out of 95 (step 1) or 152 (step 2). The other 4 fail a
  correlated 2–6 test cluster in the zones/antimeridian family — one wrong
  zones-file decision cascading, not independent misses.
- **The two models fail in mirror-image ways.** Opus never scores 0.50: it either
  solves both steps or fails step 1 outright (7/10). Avocado clears step 1 in 9/10
  and then stalls on step-2 prediction (6 × 0.50).

### AFTR: GOOD — no blockers

> "All 13 required rubrics PASS and trajectory analysis clean -> GOOD."
>
> "The grading is sound. The oracle baseline records reward 0.0 for the empty
> starting state, while the reference solution and all three oracle trials pass every
> test… The hidden tests are broad and discriminating: failed rollouts were rejected
> for real behavioral defects such as treating antimeridian polygons as covering
> longitude 0, counting hole-edge points as inside the zone, or setting `original_lat`
> equal to the predicted latitude. I did not find false-positive evidence, reward
> hacking, grading crashes, or timeouts."

Optional (non-blocking) improvements the AFTR suggested:

1. Add small tests for no-command, `--help`/`-h`, unknown command exit 2, and a
   practical observable check around temp-file/fsync behaviour.
2. Slightly clarify the Step 2 road-snapping sentence about `original_lat`/
   `original_lng` after prediction — several models made the same mistake even though
   the prediction section already defines the expected behaviour. Note this is the
   task's top step-2 discriminator (7 failures), so clarifying it would likely raise
   the avocado pass rate; weigh that against the balance gate before acting.

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

## Decision Feedback Fix (Request changes – keep difficulty)

**Decision: Request changes**
Reason: Step 2 is decided by one test. All seven step-2 failures are `test_estimate_prediction_exact_delta_north_v2` at 151/152, across Avocado and Codex. instruction.md defines original_lat as smoothed before prediction, then under Road snapping as smoothed base before prediction (or predicted if predicted). The test uses the un-snapped path. The Avocado trajectory shows deliberation, not incapacity: it quotes both sentences, argues four times, writes "We need to clarify", then picks the other reading. The reference resolves it with a branch that cannot change anything, since smoothedLat is never written after originalSmoothedLat is copied from it.

**Fix applied, keeping difficulty:**
- **Step2 instruction clarified**: Split Original vs Final into explicit 4 cases, removed ambiguous parenthetical "(or predicted if predicted)". Now:
  - Not predicted, not snapped: original=smoothed, final=smoothed
  - Predicted, not snapped: original=smoothed (ALWAYS smoothed before prediction when not snapped), final=smoothed+delta, so lat > original for north
  - Not predicted, snapped: original=smoothed, final=snapped
  - Predicted, snapped: original=predicted (smoothed+delta) position before snapping, final=snapped
  This makes un-snapped test expect lat > original (smoothed) and clarifies snapped case.
- **Reference solution fixed**: Removed dead branch `if predicted { originalLat = smoothedLat }` which did nothing because `smoothedLat` == `originalSmoothedLat`. Now code is clean: `originalLat := originalSmoothedLat` always for un-snapped, and `originalLat = estLat` only when snapped (before snapping). Added comments explaining clarified spec.
- **Deciding test made unambiguous but kept hard**: `test_estimate_prediction_exact_delta_north_v2` now checks exact expected values:
  - original_lat == 0 ±1e-6 (smoothed)
  - lat == expected_delta_lat ~0.000449 for 50m north with tolerance 0.0001
  - lat > original_lat and original != lat (catches misreading where original=predicted)
  - predicted True
  Added complementary `test_estimate_prediction_snapped_original_is_predicted_before_snapping` to clarify both paths.
- **Step1 hole-edge clarified**: Instruction now explicitly says "Point on outer edge/vertex inside, point on hole edge/vertex inside hole thus outside zone – discriminator for test_geofence_check_on_hole_edge_outside_v2". Solution already implements this (pointInPolygonSingle true for edge, hole check returns false).
- **Added small robustness tests per AFTR optional improvement 1**: test_help_no_command_prints_help, test_help_flag_variants (--help/-h/help), test_unknown_command_exit2, test_help_precedence_any_token – all passing.
- **Difficulty kept**: Step1 95→99 (added 4 robustness + kept 82 hard), Step2 152→153 (added 1 new snapped-original test, clarified deciding test). Overall still extreme-hard, but no longer decided by ambiguous single sentence. The 7 previous failures at 151/152 should now be resolved by clear spec, while other discriminators (hole-edge, antimeridian, mixed roads, backup mandatory, outlier boundaries, confidence, priority chain) keep difficulty.

**Latest oracle after fix:**
- Step1: 99/99 PASS (14s)
- Step2: 153/153 PASS (10s)

