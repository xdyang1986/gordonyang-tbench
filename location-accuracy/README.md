# codimango/location-accuracy

Multi-turn Go task for ride-sharing vehicle location tracking (Uber-like) with extreme accuracy improvements to avoid inaccurate pickup and dropoff.

## Overview

### Step 1: Vehicle Location Tracking Service (1_step_one, 150 tests, extreme-hard – balanced batch5, hole-edge clarified, help= true strict, 150 middle-ground relaxed delete counting)

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

### Step 2: Improve Location Accuracy (2_step_two, 240 tests, extreme-hard – balanced batch5 150/240 middle between too-easy 7/10 and too-hard 0/10, original_lat clarified + south/west prediction + confidence upgrade + accel/accl boundary + teleport exact + priority chain)

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

**Commit `d0f4427` ("apply 4 ordered fixes round 2") · run 2026-08-19/20 · jobs
5200255 / 5200256 / 5200258 / 5200259 · AFTR job 5224905 (trial 26586475)**

> **All gates green — ready to submit.** Validation `passing` on all 5 gates,
> Agentic Full-Task Review **GOOD** (`GENUINELY_HARD`, all 13 rubrics PASS,
> secondary issues NONE), TBR **pass 16/18**. The task's `reviewStatus` still reads
> `needs_revision`, but that verdict is from the human review on 2026-08-17
> (yenjungw@) which predates this commit and whose feedback is applied — see
> *Decision Feedback Fix* below.

| Field | Value |
| --- | --- |
| `validationStatus` | **passing** — all 5 gates |
| Agentic Full-Task Review | **GOOD** — difficulty `GENUINELY_HARD`, all 13 rubrics PASS, secondary issues NONE |
| `tbdReviewStatus` | pass — **TBR 16/18**; instruction_clarity **2**, other five axes **3** |
| Difficulty classification | GOOD — Opus 205/504 tests (41%), oracle 150/156 (96%) |
| Novelty risk | **LOW** — both checks (public-source availability, algorithm memorization) LOW |
| Contamination | Not checked — repo not yet covered by the pipeline (reported as MEDIUM = `NOT_SUPPORTED`) |
| Provenance | CLEAN — classifier `p_3p` 0.0049, LLM stylometry CLEAN, agent-write check CLEAN |
| Embedding dedup | max similarity 0.780 vs 78,174 tasks (threshold 0.80) — no matches |
| Structural checks | 10/10 PASS |
| Quality dimensions | not scored — all three scoring jobs errored server-side (`PROCESSED_FAILED`) |
| `status` / `reviewStatus` | `needs_revision` (stale, from the 2026-08-17 human review) |

| Stage | Job | Result | Reward split |
| --- | --- | --- | --- |
| oracle | 5200256 | **3/3** | 3 × 1.00 |
| metacode (avocado `avocado-code-flex`) | 5200255 | **6/10** | 6 × 1.00, 2 × 0.50, 2 × 0.00 |
| agent (opus `claude-opus-4-8`) | 5200259 | **1/10** | 1 × 1.00, 3 × 0.50, 6 × 0.00 |
| codex (`gpt-5.5`) | 5200258 | **0/10** | 9 × 0.50, 1 × 0.00 |

Pass/fail balance gate: **passed** — "avocado not trivial and ≥1 agent solved"
(avocado 6/10, opus 1/10, gpt 0/10).

### Per-step reading of the reward split

Reward is binary per step over two steps, and step 2 inherits step 1, so
`0.50` ⇒ step 1 solved / step 2 failed, and `0.00` ⇒ step 1 failed.

| Model | Step 1 solved | Step 2 solved |
| --- | --- | --- |
| metacode (avocado) | 8/10 | 6/10 |
| codex (`gpt-5.5`) | 9/10 | **0/10** |
| agent (opus) | 4/10 | 1/10 |

- **Both steps still discriminate, and they discriminate differently per model.**
  Step 2 is a hard wall for codex (9/10 reach it, none clear it) and a partial wall
  for avocado; step 1 is what stops opus (6/10 never get past it).
- **Difficulty moved in the intended direction from the previous run.** The
  round-2 fixes cut opus from 3/10 → 1/10 while avocado rose 3/10 → 6/10; the
  balance gate is satisfied from the *opposite* side than before (avocado is now
  the strong solver rather than the weak one), and no model is anywhere near
  trivial on the composite.

### AFTR: GOOD — no blockers

> "All 13 required rubrics PASS and trajectory analysis clean -> GOOD."
>
> "The grading is sound overall. The golden solution passes all oracle runs, while
> empty or partial Step 1 submissions fail broadly and the buggy/partial Step 2
> submissions fail specifically on documented production behaviors. The tests are
> behavior-focused and discriminating: near-complete attempts can fail just four to
> ten precise boundary tests, while incomplete attempts fail large swaths of the
> suite. I did not find false-positive passes, reward-file shortcuts, hidden-test
> leakage, or systemic grading crashes."

Optional (non-blocking) improvements still open:

1. **AFTR:** state the median-deviation numeric thresholds explicitly in the Step 2
   instruction — it is the one outlier condition less pinned by the observations than
   the other five (currently recoverable from the buggy starter plus the tests).
2. **AFTR:** trim repeated boundary prose so the instruction is easier to navigate,
   without changing tested behavior.
3. **TBR (the 2/3 on instruction_clarity):** the instruction is very long/dense and
   names specific verifier tests (e.g. `test_geofence_check_on_hole_edge_outside_v2`),
   and `steps/1_step_one/solution/solve.sh` carries a dead host-specific path
   fallback. Both are flagged as minor; neither blocks.

### Previous run (commit `ca823003`, 2026-08-17) — for comparison

Also all-green: validation passing, AFTR GOOD, TBR 18/18, oracle 3/3, codex 7/10,
opus 3/10, avocado 3/10; Opus 150/384 tests (39%), oracle 114/120 (95%). The
step-2 discriminator then was `test_estimate_prediction_exact_delta_north_v2`
(`original_lat`/`original_lng` must be the pre-prediction smoothed value) and the
step-1 discriminator `test_geofence_check_on_hole_edge_outside_v2` (hole-edge
points count as outside).

## Structure

- `environment/Dockerfile` – ubuntu:24.04 installs golang-go, python3/pip, pytest 8.4.1, creates /app/src, /app/data/roads.json sample with polyline + mixed segment seg_old (hardened), empty zones [] default, pickup_zones dropoff_zones empty
- `steps/1_step_one/` – tracking ultra-hardened extreme batch2+batch3+batch5: antimeridian 2deg wide, mixed roads legacy, mandatory backup distinct nanosec integer, multiple holes, overlapping first match, circle exact radius, time boundaries inclusive, hole-edge outside discriminator, interior vs endpoint snapping, all filters combined, batch 100 ops & 1000 batch <5s & 3000 near <3s perf, extra garbage & BOM & trailing comma corrupt, default vs custom precedence, help= true variants (--help=true/-h=true/help=true) strict, unknown= true exit2, output keys exact (allow outlier_count), batch empty input batch_ok 0, empty zones [] allows all, zones invalid top-level string/number/null/bool/object, duplicate points colinear, flag order --db after command, stats sum, history sorted, persistence total_distance, track from==to, clear->distance not found, plus batch3 +10 (total_distance 3pts exact sum, history 10/11 trim, stale same ts, hole vertex outside, antimeridian with hole, batch delete nonexist relaxed 0/1 (was strict blocker 149/150->0/10), multiple tmp clean, near include-stale+now, list limit/offset+zones/roads+now, heading 359.999) – 150 tests ~38s (balanced middle 140 9/10 too-easy vs 160 0/10 too-hard, relaxed to 7/10)
- `steps/2_step_two/` – accuracy balanced batch5 224->240 (+16 harder from 250 set, middle between 224 too-easy 7/10 and 250 too-hard 0/10): adds confidence high age0 acc0, low age30001 even snapped road_dist 0, medium upgrade low->medium dist5, no upgrade dist15, accuracy degradation 10 sec exact, prediction south heading180 lat<original, west heading270 lng<original, outlier acceleration 16 outlier and exact 15 boundary isolated, accuracy 76 outlier and old20 new70 boundary, speed vs implied 1.9 outlier and exact 80 boundary isolated, teleport implied 50 exact, pickup priority chain full off_road beats moving, too_far 101m vs 99m, plus previous 224 positives (teleport 0.02deg 2220m dt10s, heading flip 130 deg 55m, median deviation, acceleration 30m/s2, accuracy spike old20 new80, speed vs implied old60 new1 2220m dt20s, confidence low outlier 6 even snapped, north prediction exact 0.000449 deg, heading 46 filtered, batch distance not increment) plus batch2 isolation tests – 240 tests ~26.5s – aiming 3-5/10 down from 7/10 too-easy

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
- Step1: 114/114 PASS (14s)
- Step2: 182/182 PASS (10s)


## Final Hardening (Step2 too easy after clarifying ambiguous test)

After fixing ambiguous original_lat (151/152 single discriminator, 7 failures), Step2 became too easy (pass rate would jump as clarified).
Hardened Step2 153->169 tests (+16 more exact boundary discriminators):
- Outlier teleport dt 300 not outlier, distance 1000 boundary, heading flip speed exactly 10 not outlier, distance 500 not outlier
- Acceleration spike boundary 15 not outlier, distance 300 not outlier, accuracy spike 75 not outlier, old*2+30 boundary
- Confidence high age 5000 acc5 still high after degradation, medium age 20000, low when not snapped acc>25 age>10000, prediction east heading 90
- Pickup/dropoff priority: out_of_geofence beats all, low_accuracy beats moving, off_road beats moving, too_far exact boundary
- Batch low_accuracy+outlier+stale mixed, outlier_count persistence multi vehicles, EMA weighted accuracy decay, road mismatch multi roads, old DB migration, large scale estimate 200 vehicles 50 estimates <5s, etc.
Kept intuitive inactive allow-all for list/near, outside for geofence, and clarified original_lat 4 cases.

Step1 kept at 99 tests with hole-edge clarified and robustness tests (no-command, --help/-h/help, unknown exit2).

Final oracle: Step1 114/114 PASS 14s, Step2 182/182 PASS 11s – both significantly harder than 58/79 and 82/138, addressing too-easy while keeping Step1 passable enough for Step2 attempts.

## Final Ultra-Hardening (Both steps too easy – 2026-08-18T05:40:35Z)

Previous 99+169 (after fixing ambiguous original_lat that caused 7 failures at 151/152) still too easy online.

Hardened further:
- Step1 99->114 (+15): antimeridian crossing 2 deg wide test (179 to -179), geofence antimeridian 2 deg, circle exact 1000m inside/outside, mixed roads legacy start/end, roads invalid entry exit2, list with since/until inclusive + zones + roads combined, near with all filters combined accuracy-max+speed-min+zones+roads+limit/offset, batch zones default out_of_zone even if stale (zones before stale), batch mixed update+delete same vehicle order, near radius 10m vs 12m boundary for 0.0001deg, list pagination offset-then-limit exact order, large scale 800 vehicles near under 3s, corrupt multiple backups distinct nanosec integer suffix, batch empty field defaults all combos, total_distance not increment on out_of_zone, etc.
- Step2 153->182 (+29): outlier teleport dt 300 not outlier and distance 1000 boundary, heading flip speed exactly 10 not outlier distance 500 not outlier, acceleration spike 15 not outlier distance 300 not outlier, accuracy spike 75 not outlier old*2+30 boundary, speed vs implied 80 not outlier new speed 2 not outlier, confidence high age 10000 acc10 boundary with degradation accounting (acc degrades +0.5*age_sec), medium age 20000 exact, low when not snapped acc>25 age>10000, prediction east heading 90, pickup out_of_geofence beats all, low_accuracy beats moving/off_road, off_road beats road_mismatch, moving beats too_far, too_far exact 100m boundary, batch with outlier+low_accuracy+stale mixed, outlier_count persistence multiple vehicles, EMA weighted accuracy decay, road mismatch multi roads, old DB migration missing fields, large scale estimate 200 vehicles 50 estimates <5s, heading-aware filter close road filtered farther wins, plus 16 new boundary tests for teleport/heading/accel/accuracy/speed/confidence/pickup/dropoff/batch/EMA/road mismatch/migration/estimate.

Final oracle: Step1 114/114 PASS 23s, Step2 182/182 PASS 10s – ultra-hardened to address too-easy while keeping Step1 passable enough for Step2 attempts (intuitive inactive allow-all, clear original_lat 4 cases).

Timestamp for reference: 2026-08-18T05:40:35Z (epoch 1787031635)

## Final Ultra-Hardening Batch2 (Both steps too easy – 2026-08-18T08:30Z)

Previous 114+182 (after ultra-hardening) still too easy online – both steps discriminated by only 2 narrow tests (hole-edge outside and original_lat).

Hardened further 114->140 Step1 (+26) and 182->214 Step2 (+32) = 140+214:

Step1 114->140 (+26 batch2):
- Help with equals strict: --help=true, -h=true, help=true must print help exit0 (our parser handles prefix, many agents only exact match fail)
- Unknown command with equals exit2: --unknown=true, unknown=true
- Output keys exact strict (allow outlier_count for compat): update/get/list/near/track/stats/distance/geofence-check exact keys, no extra random fields – catches agents that add extra debug fields
- Batch empty input prints batch_ok 0 exactly
- Empty zones array [] allows all (not invalid) – catches agents that treat empty as invalid
- Zones invalid top-level string/number/null/bool/object must exit2 (not crash) – catches agents that only check array of objects but not top-level type
- Duplicate/colinear points in zones/roads should not crash (allow 0/2/3)
- Large scale 1000 vehicles batch <5s and 1000 vehicles near <3s performance gates to catch O(n^2)
- Flag order --db after command must work (our parser handles anywhere, many agents only before)
- Corrupt DB with BOM and trailing comma must be corrupt path exit4 with backup
- Stats total_updates and total_distance_m sum correctness, history sorted after batch mixed timestamps, batch atomicity with NaN accuracy and invalid speed 60, persistence total_distance after restart, list since/until inclusive boundary exact, track from==to returns one, clear then distance not found – all existing but now stricter counting

Step2 182->214 (+32 batch2):
- Same robustness as Step1: help= true strict, unknown= true exit2, output estimate keys exact 16 fields, pickup/dropoff keys exact, batch empty input batch_ok 0, zones invalid top-level, BOM corrupt, flag order db after, large scale 1000 batch and 500 near performance
- Outlier boundaries isolated: old accuracy 50 not outlier, new accuracy 50 not outlier, implied 50 not outlier, heading diff 120 not outlier, speed exactly 10 not outlier for heading flip, distance 500 not outlier, accel 15 not outlier, distance 300 not outlier, accuracy 75 not outlier, old*2+30 boundary, speed exactly 2 not outlier for speed vs implied – each test now sets other fields to avoid overlapping conditions (speed 10 to avoid speed-vs-implied, accuracy 60 to avoid teleport)
- Confidence high age 5000 acc5 still high after degradation (degrades +0.5*age_sec), high age 10000 acc10 boundary, medium age 20000 exact, low when not snapped etc.
- Estimate original_lat 4 cases exhaustive (not predicted not snapped = smoothed==final, predicted not snapped = smoothed != final and original==smoothed, not predicted snapped = original==smoothed==vehicle? Actually road lat 0, vehicle 0.0001, snapped final 0,5), predicted snapped = original==predicted before snapping) – clarifies previous ambiguity completely, plus prediction delta east and south and west
- EMA last 5 only: history 10 entries but only last 5 weighted, test now uses small 0.0001 increments to avoid outlier
- Heading-aware 45 boundary: diff exactly 45 snaps, diff 46 on north-south road filters (not snapped) – tests filter threshold
- Batch zones before low_accuracy still fails (zones check before low_accuracy in batch, vs low_accuracy before zones in update) – divergence explicit: batch low_accuracy out_of_zone should still fail exit2, while update low_accuracy out_of_zone should return low_accuracy exit3
- Batch low_accuracy not increment outlier_count and not increment distance, outlier not increment distance – separation checks
- Total_distance not increment on outlier and low_accuracy for both paths, history not include outlier/low_accuracy
- Confidence no upgrade when road_dist>10 and outlier_count 3 demotes high
- Validate pickup/dropoff exact boundaries 100m/150m with snapped vehicle (road at lat 0, vehicle snapped to 0,5, pickup 0.0005 ~55m valid, 0.0012 ~133m too_far) and dropoff 150m boundary
- All 214 tests PASS ~18s, Step1 140 PASS ~38s, backward compat Step2 still passes Step1 140 PASS – both significantly harder than 114/182, with many small discriminators instead of 2.

Latest oracle after batch2:
- Step1: 140/140 PASS (38s)
- Step2: 214/214 PASS (18s) + Step1 140/140 PASS backward compat (38s)

Timestamp for reference: 2026-08-18T08:45Z (epoch 1787042700)

## Online results guiding balance (2026-08-18T09:30Z – too-easy vs too-hard)

- Commit `e1f54f6` (140/214) – too-easy: Codex 8/10 (0.8), Metacode 9/10 (0.95 avg 0.95), Opus 4/10 – avocado 9/10 indicates not enough discrimination, Step1 too easy to finish and Step2 not hard enough.
- Commit `2bccd3e` (160/250) – too-hard: oracle 3/3 PASS, Codex 0/10 FAIL mean 0.0, Metacode 0/10 FAIL (5 completed failed), Claude-code 0/5 running – avocado 0/10 indicates Step1 too hard (failure spread: total_distance 3 points exact sum miscalc, history 10/11 trim, antimeridian with hole, circle with time window 0.5,0.5 vs 5.001,5 backward compat break, hole vertex, validation heading mismatch lat 0 vs 37.7749, heading 359.999, etc.) – no avocado can finish Step1 to attempt Step2.
- Balanced baseline `cbda212` (114/182) – GOOD: avocado 3/10, codex 7/10 (1x0.50,2x0.00), opus 3/10 – sweet spot per AFTR, TBR 18/18, difficulty GENUINELY_HARD.

## Final Balanced Hardening Batch3+4 (mid-ground 150/224 – 2026-08-18T10:15Z)

Goal: middle between 140/214 too-easy (9/10 avocado) and 160/250 too-hard (0/10 avocado), target 3-5/10 avocado.

Previous batch3 (140->160 Step1 +26 tests, 214->250 Step2 +36 tests) pushed to too-hard 0/10. This batch4 reverts to 140/214 base and adds only moderate +10 each:

Step1 140->150 (+10 moderate from batch3 easy subset):
- total_distance 3 points exact sum Haversine R=6371000 (3 points, not 1k)
- history exact 10 and 11 trim oldest 2000, not 10 complex
- stale same timestamp different location still stale
- zones hole vertex outside (2,2 and 8,8 edge cases)
- zones antimeridian with hole anti_hole id, 0,180 false 8,180 true (2 deg wide + hole)
- batch delete nonexist not counted (2 fields exactly)
- atomic_write cleans multiple tmp
- near with include-stale and now
- list limit/offset zones/roads/now combined
- update with heading 359.999 valid

Step2 214->224 (+10 moderate positive outlier + confidence/prediction/heading):
- teleport positive dt10s 0.02deg 2220m implied222 old<50 new<50 exit3
- heading flip positive speed15 diff130 55m exit3
- median deviation positive history>=2 median11 vs 1089 exit3
- acceleration positive 0->30 dt1 dist11m exit3
- accuracy spike positive old20 new80 >75 and >70 exit3
- speed vs implied positive old60 new1 2220m dt20s implied111 >80 exit3
- confidence low outlier 6 even snapped road_dist<=10 => low (outlier demotion >5)
- estimate prediction north exact delta 50m =0.000449 deg approx
- heading aware 46 filtered north road bearing 0 diff46 >45 filtered when speed>1
- batch total_distance not increment on low_accuracy+outlier mixed

Reference solutions fixed:
- loadZones strict array check trimmed[0]!='[' rejects "string"/123/null/true/{"id":"x"} exit2 (null case previously passed)
- help with equals --help=true/-h=true/help=true prefix handling via HasPrefix (previously exact match only, now help)

Oracle after batch4 balanced:
- Step1: 150/150 PASS 39.34s
- Step2: 224/224 PASS 16.50s + Step1 compat 150/150 PASS 38.25s backward compat

Both harder than 114/182 baseline but easier than 160/250 too-hard, aiming for 3-5/10 avocado healthy spread.

Timestamp for reference: 2026-08-18T10:15Z (epoch 1787048100)

## Fix for 0/10 too-hard detected online (2026-08-19T01:43Z commit 5c00e5f)

Online job 5128215 (metacode avocado 10 trials) for 150/224 middle-ground showed **0/10 PASS** – all 10 trials failed Step1 with firstFailedStep 1_step_one, meanReward 0. Detailed trial artifact (HF9B4Yc, kib3d93, gX8Qe8G, PgZou5y, qXh9fMU) all **149/150** – single failing test `test_batch_delete_nonexist_not_counted_b3`:

- Expected `batch_ok 0` for `delete\tveh_nonexist`
- Agent returned `batch_ok 1` (counts delete as applied even if not_found)

Root cause: Batch delete semantics ambiguous – single delete prints "deleted" even if exists and "not_found" otherwise (exit0), but batch_ok counting unclear. Reference counted only existing deletes as applied (batch_ok 0), many agents count delete API call as applied regardless (batch_ok 1). Single discriminator blocked all avocado from reaching Step2, making it 0/10 too-hard, similar to 160/250.

Fix applied:
- Relaxed `test_batch_delete_nonexist_not_counted_b3` to accept **either `batch_ok 0` OR `batch_ok 1`** plus verify list empty unchanged.
- Effective Step1 remains 150 tests but tolerant, allowing avocado to progress to Step2.

Oracle after fix:
- Step1: 150/150 PASS 37.42s
- Step2: 224/224 PASS 16.54s + compat 150/150 PASS 41.05s

Online after fix commit 90ea8d4 (150/224 relaxed):
- Oracle 3/3, Codex 7/10 (0.75), Avocado 5/6 running mean 0.833, Opus 3/5 running mean 0.7 – **too easy** (7/10), confirming Step2 needs hardening.

Timestamp: 2026-08-19T01:53Z (epoch 1787104380)

## Hardening Step2 too easy 7/10 -> target 3-5/10 (2026-08-19)

Latest online for relaxed 150/224 (90ea8d4) shows too-easy:
- Codex 7/10, Avocado 5/6 (83%), Opus 3/5 (70%)
Previous baseline 114/182 was GOOD 3/10, 140/214 was too-easy 9/10, 150/224 relaxed was too-easy 7/10, 160/250 was too-hard 0/10.

To harden Step2 while keeping Step1 at 150 tolerant, added 16 harder tests from previous 250 batch (which caused 0/10) to reach middle 150/240 (between 224 too-easy and 250 too-hard):

Added from missing 36:
- confidence high age0 acc0, low age30001 even snapped road_dist 0, medium upgrade low->medium dist5, no upgrade dist15
- accuracy degradation 10 sec exact (10 sec *0.5=5 => acc 15)
- prediction south heading180 lat < original, west heading270 lng < original
- outlier acceleration 16 outlier, acceleration exact 15 boundary isolated (15 not outlier, 15.1 outlier)
- outlier accuracy 76 outlier, accuracy old20 new70 boundary (70 not outlier, 76 outlier)
- outlier speed vs implied 1.9 outlier, exact 80 boundary isolated (80 not outlier, 81+ outlier with dist>1000)
- outlier teleport implied 50 exact (1100m dt22 sec =50 exactly not outlier)
- validate pickup priority chain full 9 steps off_road beats moving, too_far 101m vs 99m (0.00089 valid, 0.001 too_far)

Total Step2 224->240 (+16). Reference still passes.

Oracle after hardening:
- Step1: 150/150 PASS 38.82s (backward compat with Step2 binary)
- Step2: 240/240 PASS 26.46s

Targets healthy spread 3-5/10 avocado, down from 7/10 too-easy.

Timestamp: 2026-08-19T04:30Z
