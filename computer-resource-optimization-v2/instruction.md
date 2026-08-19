# Computer Resource Optimization – Multi-Turn Go Task (Hard – 361+78 tests)

Two-turn Terminal-Bench task with full output contracts published (don't shrink tests).

## Turn 1: Core (361 tests, hard – difficulty in debug-in-place subtle allocator)

Build at `/app/` module `cluster-manager`, `go build -o cluster-manager .`, stdlib only.

**Persistence:** `/app/data/cluster.json` via `--data`, wrapper `{"data":{"nodes":{...},"jobs":{...}},"checksum":md5(canonical)}` where canonical = `json.dumps(data, sort_keys=True, separators=(',',':'), ensure_ascii=False)`, raw UTF-8, `<` not escaped via `SetEscapeHTML(false)`. Missing/empty/whitespace → empty store exit 0.

**Output contracts (exact):**
- `status` → JSON object with integer keys: `total_nodes`, `total_jobs`, `allocated_jobs`, `pending_jobs`, plus `total_resources` / `used_resources` as `{"cpu":N,"memory":N,"gpu":N}`.
- `schedule <job>` → JSON `{"job_id":...,"node_id":...,"scheduled":true}`.
- `allocate <job> <node>` → JSON `{"job_id":...,"node_id":...,"allocated":true}`. Re-allocating to SAME node is no-op exit 0. Re-allocating already allocated job to DIFFERENT node → exit 2.
- `deallocate <job>` → prints `true` (was allocated) or `false` (was not), exit 0. Nonexistent job → exit 2.
- Job JSON: `node_id` is `""` (empty string, never null) when unallocated.
- `get-node` / `get-job` on nonexistent id → exit 2, no stdout.
- `list-nodes [limit] [offset]`: **limit 0 means no limit (return all)**; negative limit/offset → exit 2.

**Corrupt data file:** On unparseable JSON or checksum mismatch: copy raw bytes to `<data-path>.corrupt.<unix-nanos>`, warn stderr containing `corrupt` or `checksum`, continue EMPTY store exit 0 so `list-nodes` prints `[]`.

**Numeric parsing (limit, offset, cpu, memory, gpu) uses plain decimal integer parsing — Go `strconv.Atoi` semantics, no extra validation:**
  - ACCEPTED: leading zeros (`00`, `00002`, `0004` → 0, 2, 4); explicit sign (`+4`, `-0` → 4, 0).
  - REJECTED (exit 2): non-numeric (`abc`), hex (`0x10`), float (`2.0`, `4.0`), empty string, any surrounding whitespace (`" 2 "`), and out-of-range values (limit < 0, offset < 0, cpu <= 0, memory <= 0, gpu < 0).
  - `-0` parses to 0 and is therefore valid wherever 0 is valid (gpu, limit, offset). Do NOT special-case the sign character.

**Core:** add-node idempotent preserve old, remove-node true/false fails exit2 if has jobs, list-nodes pagination sorted asc (limit 0 means all, leading zeros and + valid e.g. 0004==+4==4), get-node, add-job, remove-job deallocates first jobs [] not null, list-jobs, get-job, allocate insufficient exit2, deallocate, schedule first-fit sorted IDs asc (first that fits), no fit exit1, status sums. Node jobs sorted [] not null. Resources cpu<=0 mem<=0 gpu<0 or float like 4.0 → exit2; leading zeros and a leading + are valid.

Difficulty: After publishing exact contracts, task would swing too-easy. Put difficulty in prior-violating semantic: debug-in-place over subtly wrong allocator (e.g. schedule picks last-fit missing break, limit 0 returns 0 not all, corrupt exit2). Naive baseline with obvious implementations (null node_id, exit2 on corrupt, limit0=0) should give ~22/30.

See `steps/1_step_one/instruction.md` for full 400+ checks.

## Turn 2: Sharded (78 tests, hard – difficulty terse best-fit)

Extends Turn1 via `inherit_prior_session`, keep first-fit working when config missing.

**Flags:** --data default, --config default /app/config.json. Missing → fallback single-file, invalid → exit2 no stdout.

**Contracts for help test:** Must contain (lowercased): add-node, get-shard-id, get-shard-path, distribution, heartbeat, get-node-health, list-healthy, snapshot, restore, ops-log, optimize, data, checksum, shard, weight plus Turn1 core. Note get-presence/list-online are extra aliases not required by help test.

- Rate-limit rejection → exit 1 not 2, no stdout, stderr rate limit, per-node independent, no-consume on insufficient
- Snapshot/restore: snapshot <dir> dir mode (no .json suffix or existing dir) mkdir -p copy shards+jobs+presence+rate_limit+counter+ops_log+config; file mode .json combined JSON. restore <dir> restores exactly.
- Distribution → {"0":count,"1":count,...} shard_id string → count including global broadcast
- Ops-log → array of {"op": ...} entries, order preserved, skip invalid JSON lines warning stderr corrupt/skip/warning, needs big bufio.Scanner 10MB, after 50 triplets >=50 containing allocate

**Best-fit:** Spec says only "best-fit" terse – tie-break chain intentionally not fully specified (must infer waste cpu→mem→gpu→id lex from tests/calibration) to keep difficulty in inferred semantic, not unstated contracts.

See `steps/2_step_two/instruction.md` for full spec (78 tests).

## Build
`go build -o ./cluster-manager .`

## Validation
- Step1: 361 PASS with exact contracts, naive baseline ~22/30 with obvious bugs
- Step2: 78 PASS with terse best-fit
