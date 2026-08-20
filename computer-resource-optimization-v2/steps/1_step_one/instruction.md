# Turn 1: HPC Genomics Fleet Core – Durable Cluster Manager (Go, 400+ behavioral checks)

You are building the core of a genomics HPC fleet manager that schedules DNA sequencing pipeline jobs onto sequencer nodes. The initial single-file version must survive crashes, concurrent invocations, and hostile IDs while preserving exact JSON fidelity.

Sequencer IDs are 10KB instrument barcodes and pipeline IDs carry `<>&` lab symbols and 🌍🚀 emoji sample tags, so durability and canonicalization must hold for arbitrary UTF-8.

## Persistence contract (behavioral, implementation-agnostic)

- Default path `/app/data/cluster.json`, writable dir `/app/data/`, overridable via `--data`.
- Missing file, empty file, or whitespace-only file → empty store `{nodes:{},jobs:{}}` (not corruption, exit 0).
- Stored format is a wrapper:
```json
{"data":{"nodes":{...},"jobs":{...}},"checksum":"<md5>"}
```
where `checksum = MD5( json.dumps(data, sort_keys=True, separators=(',',':'), ensure_ascii=False) )`. You must produce byte-identical canonicalization: Python sorts keys, no spaces, raw UTF-8 for emoji, `<` stays `<` not `\u003c`. Go's JSON encoder escapes `U+2028`/`U+2029` even when HTML escaping is disabled – you must compensate so checksum matches Python.
- Atomic durability: concurrent CLI processes (20-way parallel) must never expose partial JSON, never overcommit resources, and must clean up after themselves. No `.tmp.*` or `.lock` files may remain after a successful command. Stale `<data>.tmp.<pid>` files must be ignored (not read as DB) and cleaned on next command. Stale `<data>.lock` files must be retried, not cause corruption.
- Corruption handling: if file is invalid JSON, or wrapper missing `checksum`, or checksum mismatches, or contains `null` or `[]` as top-level (not wrapper), or `data` is not an object or missing `nodes`/`jobs`, treat as corrupt: create backup `<original>.corrupt.<nanosec>` where suffix is integer nanoseconds, print warning containing `corrupt` or `checksum` to stderr, and recreate empty valid wrapper.

## CLI – Genomics Fleet (must match tests exactly)

Build binary via `go build -o <binary> .` in `/app`, module `cluster-manager`, stdlib only (`go list` shows no dotted imports).

Global flags: `--data <path>` default `/app/data/cluster.json`. Help: bare binary, `--help`, `-h`, `help` must print help containing `add-node`, `remove-node`, `list-nodes`, `get-node`, `add-job`, `remove-job`, `list-jobs`, `get-job`, `allocate`, `deallocate`, `schedule`, `status`, `data`, `checksum` and exit 0. Unknown command → exit 2, missing args → exit 2, extra args → exit 2, empty or whitespace-only ID → exit 2, non-int or invalid resources (cpu<=0 mem<=0 gpu<0 or float like `4.0` → exit 2; leading zeros and a leading + are valid (0004 == +4 == 4)) → exit 2.

**Numeric parsing contract (exact, Go `strconv.Atoi` semantics):**
- Numeric parsing (limit, offset, cpu, memory, gpu) uses plain decimal integer parsing — Go `strconv.Atoi` semantics, no extra validation:
  - ACCEPTED: leading zeros (`00`, `00002`, `0004` → 0, 2, 4); explicit sign (`+4`, `-0` → 4, 0).
  - REJECTED (exit 2): non-numeric (`abc`), hex (`0x10`), float (`2.0`, `4.0`), empty string, any surrounding whitespace (`" 2 "`), and out-of-range values (limit < 0, offset < 0, cpu <= 0, memory <= 0, gpu < 0).
  - `-0` parses to 0 and is therefore valid wherever 0 is valid (gpu, limit, offset). Do NOT special-case the sign character.

Commands:
```
add-node <nodeID> <cpu> <memory> <gpu>   idempotent no-op if exists (preserve old total/used/jobs), exit 0
remove-node <nodeID>                     prints true/false lower-case, fails exit 2 if node has allocated jobs, false if not exist (exit 0)
list-nodes [limit] [offset]              sorted id asc, limit 0 or omitted = all, offset beyond = [], invalid limit/offset → exit 2. Each element full node JSON.
get-node <nodeID>                        full node JSON, exit 2 if not exist
add-job <jobID> <cpu> <memory> <gpu>     idempotent no-op if exists (preserve required, node_id, status), exit 0
remove-job <jobID>                       true/false, deallocates first if needed (node jobs becomes [] not null, used decremented), false if not exist
list-jobs [limit] [offset]               same pagination contract as list-nodes
get-job <jobID>                          full job JSON, exit 2 if not exist
allocate <jobID> <nodeID>                allocate if free resources sufficient else exit 2 stderr "insufficient", job/node must exist else exit 2, already allocated to different node → exit 2, same node → idempotent exit 0. Prints {"job_id":...,"node_id":...,"allocated":true}
deallocate <jobID>                       true/false, false if not allocated, exit 2 if job not exist
schedule <jobID>                         first-fit by sorted node IDs asc – first node with enough free. If already allocated → exit 2, no fit → exit 1 stderr "no fit" no stdout. Prints {"job_id":...,"node_id":...,"scheduled":true}
status                                   {"total_nodes":..,"total_jobs":..,"allocated_jobs":..,"pending_jobs":..,"total_resources":{"cpu":..,"memory":..,"gpu":..},"used_resources":{"cpu":..,"memory":..,"gpu":..}}
```

Node JSON: `{"id":...,"total":{"cpu":...,"memory":...,"gpu":...},"used":{...},"free":{...},"jobs":["sorted"]}` – jobs sorted asc, empty → `[]` not `null` in raw file and API. Job JSON: `{"id":...,"required":{"cpu":...,"memory":...,"gpu":...},"node_id":...,"status":"pending"|"running"}`.

## Hard discriminators (why previous 30 tests were too easy, now 400+)

- Empty ID (`""`) and whitespace IDs (`"   "`, `"\n\t"`) → exit 2, not treated as valid.
- Whitespace file (`"   \n\t  "`) → empty store.
- Files `null` and `[]` → corrupt path with backup.
- Jobs array must be `[]` not `null` after add-node, deallocate, remove-job (Go nil slice bug).
- Idempotent: re-adding existing node/job with different resources must keep old values and existing allocation.
- First-fit semantics: sorted ID asc first that fits wins even if wasteful (tested vs best-fit).
- Pagination: `list-nodes 2 1` → items 1,2 not 0,1. Limit/offset validation per numeric parsing contract: float (`2.0`), negative (`-1`), hex (`0x10`), whitespace (`" 2 "`), non-numeric → exit 2; leading zeros (`00`, `00002`) and explicit plus (`+4`, `+0`, `-0`) are ACCEPTED and parsed as decimal (see contract).
- Special chars: IDs containing `<>&`, `-_ . :`, `/`, `=;`, `[]`, `%&`, `$*+@`, `` ` `` must be preserved, raw `<` in file (no `\u003c`), and jobs sorted.
- Unicode: `node-🌍`, `job-🌍🚀😀` preserved raw UTF-8 in file and API.
- Large IDs: 10KB IDs with mixed special chars must work for add/get/allocate.
- Concurrent: 20-way add-node different IDs → 20 sorted, same ID 20-way → 1 node (first wins), add-job same ID 20-way → 1 job, allocate 20 jobs to same node (100 CPU) → 20 jobs preserved no overcommit, diff nodes 20-way → 20 allocated, deallocate 20-way, list while allocating, interleaved add-node+allocate overlapping IDs with exact used/free arithmetic, status many times.
- Lock cleanup after success and after failure (insufficient), no `.lock` leftover, no `.tmp.*` leftover after any op.
- Checksum after each op, raw file contains checksum key, canonical matches.
- Truncated file (prefix cut at 50 bytes) → corrupt backup integer nanosec suffix.
- Stale tmp `cluster.json.tmp.12345` ignored and cleaned, stale lock retry with 0.15s delayed removal → must succeed.
- Status sums correct after alloc/dealloc cycles.

## Examples
```bash
go build -o ./cluster-manager .
./cluster-manager --data /app/data/cluster.json add-node node1 4 1024 1
./cluster-manager --data /app/data/cluster.json add-job job1 1 256 0
./cluster-manager --data /app/data/cluster.json allocate job1 node1
./cluster-manager --data /app/data/cluster.json get-node node1
./cluster-manager --data /app/data/cluster.json status
```

Implement at `/app` – Turn1 core.

## Output contracts (exact)
- `status` → JSON object with integer keys: `total_nodes`, `total_jobs`, `allocated_jobs`, `pending_jobs`, plus `total_resources` / `used_resources` as `{"cpu":N,"memory":N,"gpu":N}`.
- `schedule <job>` → JSON `{"job_id":...,"node_id":...,"scheduled":true}`.
- `allocate <job> <node>` → JSON `{"job_id":...,"node_id":...,"allocated":true}`.
  Re-allocating to the SAME node is a no-op, exit 0. Re-allocating an already allocated job to a DIFFERENT node → exit 2.
- `deallocate <job>` → prints `true` (was allocated) or `false` (was not), exit 0. Nonexistent job → exit 2.
- Job JSON: `node_id` is `""` (empty string, never null) when unallocated.
- `get-node` / `get-job` on a nonexistent id → exit 2, no stdout.
- `list-nodes [limit] [offset]`: **limit 0 means no limit (return all)**; negative limit/offset → exit 2.

## Corrupt data file
On unparseable JSON or checksum mismatch: copy the raw bytes to `<data-path>.corrupt.<unix-nanos>`, warn on stderr containing `corrupt` or `checksum`, continue with an EMPTY store, and **exit 0** (so `list-nodes` prints `[]`).

## Difficulty note
After publishing exact contracts, difficulty is intentionally in prior-violating semantic the model must infer (e.g. best-fit tie-break terse in Turn2, or debug-in-place with subtly wrong allocator). Do not add more unstated contracts or scale as difficulty source. Naive baseline with obvious implementations (null node_id, exit2 on corrupt, limit0=0 items) should give ~22/30.
