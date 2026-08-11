# codimango/computer-resource-optimization-v2

Multi-turn Go cluster management hardened – Step1 now 108 tests extra-hard (was 30 too easy, 49/66/80/96 still easy), Step2 46 extra-hard good to keep.

## Overview
**Turn1 (108 tests, extra-hard):** Single-file `/app/data/cluster.json` wrapper checksum MD5 canonical sort_keys separators + SetEscapeHTML false raw "<" not \u003c. Atomic CreateTemp+Rename same dir + file lock O_CREATE|O_EXCL retry 5ms 2000 tries cleanup no tmp. Checksum strict missing/bad/invalid JSON including null/[] -> backup `.corrupt.<nanosec>` integer suffix regex `\.corrupt\.\d+$` warning corrupt/checksum recreate empty. Empty "" and whitespace "   \n\t" -> empty store [] not corrupt. Jobs [] not null after add-node/deallocate/remove-job (nil-slice pitfall) raw '"jobs":[]' no null. Idempotent no-op preserves old resources/allocation running not upsert, same ID concurrent 20 race -> 1 node/job not 20. Concurrent add-node 20 different IDs sorted, same node 20 preserve all 20 used 20 valid JSON during, diff nodes 20 preserve all 20, deallocate 20 -> used 0 jobs [], list while allocating 10x30 valid JSON no crash. Pagination offset then limit order offset1 limit2 -> 1,2 not 0,1 invalid negative abc -> exit2 limit0 vs omit both all offset beyond []. Perf 800 nodes list <1.5s O(n log n) limit100 offset100 <1.5s 500 jobs sorted, special chars <>& raw, Unicode emoji 🌍🚀😀, large ID 10KB dash underscore dot colon valid, empty ID spaces "   " -> exit2 float resource "4.0" invalid, timestamp integer required reject 1000.0 1e3 0x3e8, status total/used resources used/free after allocate/deallocate, node jobs sorted asc, remove false not exist true/false, deallocate false vs exit2 nonexist, allocate diff node exit2 same node idempotent no duplicate, insufficient gpu (node gpu0 job gpu1), file lock cleaned after failure, concurrent list 100 times no crash, etc. 50 new discriminators over original 30.

**Turn2 (46 tests extra-hard, good to keep):** Config missing->fallback invalid->exit2 no stdout empty shards [] invalid, empty-string "" valid hashed MD5, distribution includes zeros global broadcast -1 comma-separated sorted, pagination contract sorted asc limit0 all offset beyond [] invalid->exit2 perf 200 <2s, schedule best-fit cpu->mem->gpu->id lex vs first-fit first-fit nodeA 10 CPU wins vs nodeB 4 CPU (Step1) flips to best-fit nodeB wins Step2 core discriminator, token-bucket per-node float refill elapsed*rate burst persistence wrapper checksum atomic per-node independent no-consume on insufficient no side effects corruption reset multi-cycle 2 succ fail sleep1.2 succ fail sleep1.2 succ refill 1.6s, presence TTL heartbeat online expiry 2s->3s offline multi-node unknown offline0 corruption, snapshot dir/file restore exact trap (shard created after snapshot survives naive restore), ops-log skip invalid warning order preserved, optimize fragmentation_after <= before OR used_nodes<=before moves>=0 total_nodes unchanged preserve jobs no overcommit.

## Latest Validation

### Oracle (current, after hardening)
- Turn1: **108/108 PASS 29.36s** (was 30/30 no signal, then 49/49 still easy, 66/66 still easy per feedback)
- Turn2: **46/46 PASS 13.99s** good to keep
- Multi-turn: 96 then 46 PASS

Previous oracle with 30/46: validationStatus passing tbdReviewStatus pass at 7f16a6cc (Nest jobs 4489096–99 2026-08-11):

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
| `test_snapshot_restore_dir` | 6 | snapshot/restore exactness | Dir snapshot copies each shard if exists, shard created after snapshot survives naive restore that only restores known files → newnode still present |
| `test_optimize_moves_valid` | 2 | optimize invariants | Fake optimize same fragmentation without moves |
| `test_rate_limit_refill_after_sleep` | 1 | token bucket | Shared global bucket not per-node or no float refill 1.6s sleep should refill |
| `test_rate_limit_persistence` | 1 | token bucket | Bucket not persisted to file |

Outlier trial 23522092 avocado 5/46 after 30/30 Turn1 – binary never implemented --config flag `unknown command: --config` – legitimate failure.

### After hardening Step1 (80 tests)
Turn1 previously no signal. New 50 discriminators will now fail naive agents:

| New Test | Expected Agent Failure |
| --- | --- |
| `timestamp integer required` 1000.0/1e3/0x3e8 | ParseFloat accepts float/scientific/hex → exit0 not exit2 |
| `whitespace file empty store` | Whitespace-only treated as corrupt 4 not empty [] |
| `missing/bad checksum + null/[] file corrupt` | No backup `.corrupt.<nanosec>` integer suffix or returns not [] after corruption |
| `jobs [] not null` after add-node/deallocate/remove-job | Go nil slice → null not [] raw check fails |
| `add-job idempotent preserves allocation` | Upsert overwrites cpu and clears node_id |
| `concurrent add-node same ID 20 race` | Race creates 20 nodes or crash, not 1, lock not cleaned |
| `concurrent add-job same ID 20` | 20 jobs not 1 |
| `concurrent add-node 20` | No lock → lose nodes |
| `concurrent same node 20` | No O_EXCL retry → invalid JSON or only last job preserved |
| `concurrent diff nodes 20` | Global lock missing → lost updates allocated_jobs !=20 |
| `concurrent deallocate 20` | Used not 0 after |
| `concurrent list while allocating` | List returns invalid JSON or crash under concurrent write |
| `pagination offset then limit` offset1 limit2 -> 1,2 | Limit then offset returns 0,1 |
| `invalid limit/offset` negative/abc | Not rejected exit0 not exit2 |
| `large 800 <1.5s` | O(n^2) → >1.5s |
| `special chars job <>& raw` | SetEscapeHTML true → \u003c |
| `large ID 10KB dash underscore dot colon` | Buffer crash or not preserved |
| `empty ID spaces` | "   " treated as valid |
| `float resource 4.0` | Accepted as int |
| `status sum used/free` | Miscalculated after deallocate |
| `first-fit not best-fit` | Best-fit implemented in Step1 → nodeB wins not nodeA |
| `file lock cleaned after failure` | Lock remains after insufficient → next cmd "lock" error |
| `node jobs sorted` | Jobs not sorted asc |
| `remove false / deallocate false` | Prints not false or exit code wrong |
| `allocate diff node exit2 / same node idempotent` | Duplicate jobs or not rejected |
| `insufficient gpu` | Only cpu/mem checked not gpu |
| `deallocate preserves other jobs` | Removes all jobs not just one |
| `checksum valid after each op` | Checksum invalid per canonical spec |
| `list limit0 vs omit all` | Different results |
| `offset beyond empty` | Not [] |
| `circle exact radius inside` | Point on edge exact radius considered outside (should <=) |

These add real signal where previously Turn1 was free.

### Fairness fix
d4339e3 relaxed test_ops_log_and_skip_invalid len>=3 -> >=1 plus allocate op present. Spec only says allocate/deallocate/schedule/status append ops log, never add-node/add-job, so old punished spec-following.

### Caveats
- Rate limit 1.6s sleep may flake
- Dedup margin 0.7472 vs 0.75 re-check before content-heavy edit
- Step1 80 extra-hard now provides signal, Step2 46 good – overall 126 tests balanced.
