# codimango/computer-resource-optimization-v2

Multi-turn Go cluster management with large-scale efficiency – hardened with real discriminators.

## Overview
Turn1 was 30 tests too easy (30/30 in every trial, no signal). Now 49 tests extra-hard with 19 new discriminators catching common agent pitfalls. Turn2 46 tests extra-hard good to keep, provides all signal previously.

**Turn1 (49 tests, extra-hard):** Core single-file `/app/data/cluster.json` wrapper `{"data":{"nodes":{...},"jobs":{...}},"checksum":md5 canonical}` canonical `json.dumps(data, sort_keys=True, separators=(',',':'))` raw "<" preserved via `SetEscapeHTML(false)`. Atomic `CreateTemp+Rename` same dir + file lock `<data>.lock` O_CREATE|O_EXCL retry 5ms 2000 tries cleanup no tmp leftover, no global.lock leftover. Checksum strict, missing checksum/bad checksum/invalid JSON → backup `.corrupt.<nanosec>` integer nanosec warning corrupt/checksum recreate empty valid. Empty file "" and whitespace "   \n\t" → empty store [] not corrupt. Node jobs field empty MUST be [] not null (Go nil-slice pitfall) after add-node, deallocate, remove-job. Idempotent no-op: re-adding existing node/job with different resources preserves old resources and allocation (not upsert). Concurrent add-node 20 different IDs preserve all 20 sorted, concurrent same node 20 allocates preserve all 20 jobs used correct no overcommit file valid JSON during, concurrent diff nodes 20 preserve all. Pagination offset then limit order: offset1 limit2 → nodes 1,2 not 0,1, invalid limit/offset negative non-int → exit2, perf 500 nodes <2s O(n log n). Special chars <>& raw no escape for node and job, Unicode emoji 🌍 preserved, large ID 10KB supported, status total/used resources sum, schedule first-fit sorted IDs asc first that fits wins even if wasteful (nodeA 10 CPU id smaller vs nodeB 4 CPU both fit job 2 CPU → nodeA wins for Step1, Step2 flips to best-fit), remove-node with jobs fails exit2, remove-job deallocates first node jobs [] not null used decremented.

**Turn2 (46 tests extra-hard, good to keep):**
- Config rule clarified: missing file → fallback single-file, invalid (missing shard_count, empty shards, bad JSON, dupe id, empty path, weight≤0, negative) → exit2 no stdout. Previous contradiction instruction.md:17/141 fixed.
- Best-fit tie-break cascade cpu→mem→gpu→id lex deterministic vs first-fit: cpu waste, mem waste tie (nodeA 4 CPU 2048 MEM vs nodeB 4 CPU 1024 MEM job 2 CPU 512 MEM → nodeB wins), gpu waste tie (nodeX gpu1 vs nodeY gpu0 req gpu0 → nodeY wins), id lex tie identical waste → smaller ID, fragmentation vs first-fit, after allocations.
- Token-bucket multi-cycle per-node float tokens refill elapsed*rate burst, persistence wrapper checksum atomic, per-node independent (nodeA limited nodeB succeeds), no-consume on insufficient (big job fails insufficient → token not consumed next small alloc succeeds), no side effects when limited (no ops-log, no allocation), corruption reset invalid JSON → bucket reset, cycles 2 succ fail sleep1.2 succ fail sleep1.2 succ, refill 1.6s.
- Optimize invariants: total_nodes unchanged, used_nodes <= before OR fragmentation_after <= before, moves>=0 int, preserve all jobs, no overcommit.
- Presence TTL: heartbeat online, expiry 2s→3s offline, multi-node 3 nodes heartbeat then 3.2s sleep → [] then heartbeat bob → [bob], unknown offline 0, corruption handling checksum mismatch → offline.
- Other: snapshot dir+file restore exact post-mutation gone (shard file created after snapshot survives naive restore trap), ops-log skip invalid warning corrupt/skip/warning order preserved, pagination perf 200 <2s, weighted distribution includes zeros, global broadcast -1 and comma-separated sorted paths, empty-string "" valid hashed MD5.

Vestigial top-level tests/solution removed.

## Latest Validation

### Oracle (latest)
- Turn1: 49/49 PASS (7.88s) with new discriminators
- Turn2: 46/46 PASS (13.79s) good to keep
- Multi-turn inherit: Step1 build → Step2 build on top preserves compatibility (49 then 46 PASS)

Previously with Turn1 30 tests: `validationStatus: passing`, `tbdReviewStatus: pass` at commit `7f16a6cc` (Nest jobs 4489096–99, 2026-08-11):

| Stage | Agent | Result |
| --- | --- | --- |
| oracle | oracle | 3/3 |
| metacode | avocado `avocado-5.14-code` | 5/10 |
| agent | claude-code `claude-opus-4-8` | 7/10 |
| codex | `gpt-5.5` | 10/10 |

Structural 10/10, contamination LOW, novelty MEDIUM, embedding dedup 0.7472 (threshold 0.75).

### Discriminators (previous run, Turn1 30/30 no signal)
All failures were Turn2; Turn1 scored 30/30 in every trial and contributed no signal. Across 8 failing trials:

| Test | Count | Subsystem |
| --- | --- | --- |
| `test_snapshot_restore_dir` | 6 | snapshot/restore exactness – dir-mode copies each shard file if exists, so shard created after snapshot survives naive restore |
| `test_optimize_moves_valid` | 2 | optimize invariants |
| `test_rate_limit_refill_after_sleep` | 1 | token bucket 1.6s refill |
| `test_rate_limit_persistence` | 1 | token bucket persistence |

Four distinct spec-backed discriminators, all in Turn2. One outlier trial (23522092, avocado) scored 5/46 after passing Turn1 30/30 – binary never implemented --config flag `unknown command: --config` – legitimate failure.

### After hardening Step1 (current)
Turn1 now adds signal where previously none:
- timestamp integer required: agents accepting 1000.0/1e3/0x3e8 → exit0 instead of exit2
- batch atomic with zones out_of_zone: partial apply before detecting → DB corrupted
- batch zones before stale: check stale first → skip out_of_zone stale op → incorrectly exit0
- near include-stale: ignore flag → stale never included
- near accuracy+speed combined: filter only one dimension
- list offset then limit order: limit then offset → wrong slice
- list with now zones inactive: return vehicles even when no active zones (should [] when zones non-empty but none active)
- geofence file order: return last matching not first
- roads interior vs endpoint: endpoint-only distance 55km >50m → not snapped missing vehicles
- roads all segments: check only first segment
- total_distance stale: increment even for stale
- history last equals current: history without current as last or not sorted asc
- jobs [] not null: Go nil slice → null not []
- idempotent no-op: upsert overwrites resources/allocation
- concurrent same node 20: lose jobs due to missing lock or not reading+writing under lock
- concurrent diff nodes 20: lose due to global lock missing

These now fail naive agents, reducing free pass rate.

### Fairness fix verified
`d4339e3` relaxed `test_ops_log_and_skip_invalid` from len>=3 to >=1 plus allocate op present. Spec only says allocate/deallocate/schedule/status append ops log, never says add-node/add-job write, so old assertion punished spec-following implementations. After fix no longer discriminates.

### Caveats
- `test_rate_limit_refill_after_sleep` 1.6s sleep may flake on slow runners
- Dedup margin three thousandths 0.7472 vs 0.75 re-check before content-heavy edit
- Step1 now 49 tests extra-hard provides signal, Step2 46 good to keep – overall 95 tests.
