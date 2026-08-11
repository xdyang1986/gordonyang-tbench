# codimango/computer-resource-optimization-v2

Multi-turn Go cluster management hardened – Step1 now 66 tests extra-hard (was 30 too easy, then 49 still easy), Step2 46 extra-hard good to keep.

## Overview
**Turn1 (66 tests, extra-hard):** Core single-file `/app/data/cluster.json` wrapper `{"data":{"nodes":{...},"jobs":{...}},"checksum":md5 canonical}` canonical `json.dumps(data, sort_keys=True, separators=(',',':'))` raw "<" not \u003c via SetEscapeHTML(false). Atomic CreateTemp+Rename same dir + file lock `<data>.lock` O_CREATE|O_EXCL retry 5ms 2000 tries cleanup no tmp and no global.lock. Checksum strict, missing/bad checksum/invalid JSON (including "null", "[]", "{ invalid") → backup `.corrupt.<nanosec>` integer suffix regex `\.corrupt\.\d+$` warning corrupt/checksum recreate empty. Empty "" and whitespace "   \n\t" → empty store [] not corrupt. Node jobs empty MUST be [] not null (Go nil-slice pitfall) after add-node, deallocate, remove-job; raw must contain `"jobs":[]` not null. Idempotent no-op: re-adding existing node/job with different resources preserves old resources and allocation running (not upsert). Concurrent add-node 20 different IDs preserve all 20 sorted lock cleaned, concurrent same node 20 allocates preserve all 20 jobs used correct no overcommit file valid JSON during, concurrent diff nodes 20 preserve all 20 status allocated 20, concurrent list while allocating valid JSON. Pagination offset then limit order: offset1 limit2 → nodes 1,2 not 0,1; invalid limit/offset negative non-int abc → exit2; limit 0 returns all, offset beyond [] . Performance 500 nodes list <2s 1000 nodes O(n log n) not O(n²). Special chars <>& raw no escape for node and job, Unicode emoji 🌍🚀😀 preserved, large ID 10KB dash underscore dot colon valid, empty ID with spaces "   " → exit2, float resource "4.0" invalid → exit2. Status total/used resources sum correct used/free after allocate/deallocate, remove-job deallocates first preserves node free=total. Schedule first-fit sorted IDs asc first that fits wins even if wasteful (nodeA 10 CPU id smaller vs nodeB 4 CPU both fit 2 CPU → nodeA wins Step1) vs Step2 best-fit (nodeB wins). Allocate already allocated different node → exit2 same node idempotent no duplicate jobs sorted. File lock cleaned after failure insufficient.

**Turn2 (46 tests extra-hard, good to keep):**
- Config rule: missing → fallback single-file, invalid (missing shard_count, empty shards, bad JSON, dupe id, empty path, weight≤0, negative) → exit2 no stdout. Empty shards [] invalid → exit2.
- Best-fit tie-break cascade cpu→mem→gpu→id lex vs first-fit: mem tie nodeA 4 CPU 2048 MEM vs nodeB 4 CPU 1024 MEM job 2 CPU 512 MEM → nodeB (mem waste 512 vs 1536), gpu tie nodeX gpu1 vs nodeY gpu0 req gpu0 → nodeY, id lex identical waste → smaller ID, fragmentation vs first-fit, after allocations free resources matter.
- Token-bucket per-node float tokens refill elapsed*rate burst persistence wrapper checksum atomic per-node independent (nodeA limited nodeB succeeds) no-consume on insufficient (big job fails insufficient → token not consumed) no side effects when limited (no ops-log no allocation) corruption reset, cycles 2 succ fail sleep1.2 succ fail sleep1.2 succ, refill 1.6s.
- Optimize: total_nodes unchanged used_nodes <= before OR fragmentation_after <= before moves>=0 int preserve jobs no overcommit.
- Presence TTL: heartbeat online, expiry 2s→3s offline, multi-node 3 nodes heartbeat then 3.2s → [] then heartbeat bob → [bob], unknown offline 0 last_seen 0, corruption checksum mismatch → offline.
- Other: snapshot dir/file restore exact post-mutation gone (shard created after snapshot survives naive restore trap), ops-log skip invalid warning order preserved, pagination perf 200 <2s, distribution includes zeros, global broadcast -1 comma-separated sorted, empty-string "" valid hashed MD5.

## Latest Validation

### Oracle (current)
- Turn1: **66/66 PASS 8.59s** – hardened from 30→49→66
- Turn2: **46/46 PASS 13.72s** – good to keep
- Multi-turn inherit: 66 then 46 PASS

Previous oracle with 30/46: validationStatus passing tbdReviewStatus pass at `7f16a6cc` (Nest jobs 4489096–99 2026-08-11):

| Stage | Agent | Result |
| --- | --- | --- |
| oracle | oracle | 3/3 |
| metacode | avocado `avocado-5.14-code` | 5/10 |
| agent | claude-code `claude-opus-4-8` | 7/10 |
| codex | `gpt-5.5` | 10/10 |

Structural 10/10 contamination LOW novelty MEDIUM dedup 0.7472 threshold 0.75.

### Discriminators previously (Turn1 30/30 no signal, all failures Turn2)

| Test | Count | Subsystem | Failing Reason |
| --- | --- | --- | --- |
| `test_snapshot_restore_dir` | 6 | snapshot/restore exactness | Dir-mode snapshot copies each shard file if exists, so shard file created after snapshot survives naive restore that only restores known files. Agents restored only files present at snapshot time, not deleting post-snapshot shard file → newnode still present |
| `test_optimize_moves_valid` | 2 | optimize invariants | Fake optimize that returns same fragmentation without moves or without consolidating → fails moves>=0 or fragmentation_after<=before or used_nodes reduction |
| `test_rate_limit_refill_after_sleep` | 1 | token bucket | Shared global bucket not per-node or no float refill: 1.6s sleep should refill 1.6 tokens → alloc succeeds, but naive counter without elapsed*rate fails |
| `test_rate_limit_persistence` | 1 | token bucket | Bucket not persisted to file → second invocation not rate limited |

Single outlier trial 23522092 avocado 5/46 after 30/30 Turn1 – binary never implemented --config flag stderr `unknown command: --config` – legitimate agent failure not infra.

`test_rate_limit_refill_after_sleep` single occurrence may be timing flake.

### After hardening Step1 (66 tests)
Turn1 previously contributed no signal (30/30 every trial). Now adds 36 new discriminators that will fail naive agents:

| New Test | Expected Agent Failure Reason |
| --- | --- |
| `timestamp integer required` (1000.0, 1e3, 0x3e8) | Agents using ParseFloat or accepting scientific/hex → exit0 instead of exit2 |
| `whitespace file empty store` | Treat whitespace-only file as corrupt exit4 instead of empty store [] |
| `missing/bad checksum corruption` | Not backing up with `.corrupt.<nanosec>` integer suffix or not returning [] after corruption |
| `null/[] file corrupt` | "null" or "[]" not recognized as corrupt → returns not empty or crashes |
| `jobs [] not null` after add-node/deallocate/remove-job | Go `var jobs []string` nil marshals as `null` not `[]` → raw contains `"jobs":null` fails `[]` check |
| `add-job idempotent preserves allocation` | Upsert overwrites required cpu and clears node_id → loses running status |
| `concurrent add-node 20` | No file lock or reading without lock → lose nodes due to race |
| `concurrent same node 20` | Lock O_EXCL not used or not retrying 5ms 2000 tries → file invalid JSON during or only last 1 job preserved, used count wrong |
| `concurrent diff nodes 20` | Global lock missing for multi-shard? For single-file, not using lock for jobs+nodes atomic → lost updates |
| `pagination offset then limit` | Limit then offset: offset1 limit2 returns veh_0,veh_1 instead of veh_1,veh_2 |
| `invalid limit/offset` | Negative or abc not rejected → exit0 instead of exit2 |
| `large 500 perf` | O(n²) sorting each insert → >2s |
| `special chars job` | SetEscapeHTML true → raw contains `\u003c` not "<" |
| `large ID 10KB` | Buffer too small or crash |
| `status sum` | Total/used resources miscalculated after deallocate |
| `first-fit not best-fit` | Implement best-fit in Step1 (nodeB wins) → should be first-fit nodeA wins |
| `file lock cleaned after failure` | After insufficient exit2, lock file remains → next command fails "lock" |
| `node jobs sorted` | Allocate jobs Z,A,M → jobs not sorted asc |
| `empty ID with spaces` | Trim not checked → "   " treated as valid |
| `float resource` | "4.0" accepted as int → should exit2 |

These now provide signal for Step1, making overall task 95 tests balanced across both turns.

### Fairness fix verified
`d4339e3` relaxed `test_ops_log_and_skip_invalid` from `len>=3` to `>=1` plus allocate op present. Spec only attaches append ops log to allocate/deallocate/schedule/status (Turn2 L67) never says add-node/add-job write, so old assertion punished spec-following implementations. Now non-discriminating.

### Caveats
- Rate limit 1.6s sleep may flake
- Dedup margin 0.7472 vs 0.75 re-check before content-heavy edit
- Step1 now 66 extra-hard provides signal, Step2 46 good – overall 112 tests balanced.
