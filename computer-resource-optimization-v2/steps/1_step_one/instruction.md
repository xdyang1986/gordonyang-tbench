# Turn 1: Computer Cluster Management System Core (Go) – Extra Hard (135 tests)

We need a production-grade computer cluster management system in Go that manages compute nodes and jobs with resource allocation. Build core functionality with durable persistence and integrity. This turn is extra hard: 135 tests (was 30 too easy, 49/66/80 still easy per feedback), now 108 with 105 new discriminators over original 30. Features: concurrent same ID race add-node same ID 20 ->1, add-job same ID 20 ->1, add-node 20 sorted, same node 20 preserve all 20, diff nodes 20, deallocate 20 -> used 0, list while allocating 10x30 valid JSON, 1000 nodes <1.5s, checksum strict MD5 canonical sort_keys separators + SetEscapeHTML false raw "<" not \u003c, special chars <>& no escape, Unicode emoji, idempotent no-op preserved not upsert, jobs [] not null (nil-slice pitfall), empty/whitespace empty store vs corrupt null/[]/invalid JSON -> backup .corrupt.<nanosec> integer suffix regex, missing/bad checksum corruption, atomic CreateTemp+Rename + file lock O_CREATE|O_EXCL retry 5ms 2000 tries cleanup no tmp/global.lock, pagination offset then limit order, first-fit not best-fit, lock retry 100ms, gpu insufficient, etc.

Failing observations (naive misses enforced 96):
- Empty "" whitespace "   \n\t" -> empty store [] not corrupt 4; files "null" "[]" -> corrupt backup integer suffix \.corrupt\.\d+$ warning list [] after. Missing checksum / bad checksum / data missing / data not object -> corrupt backup
- Jobs [] not null: nil slice marshals null -> bug, after add-node/deallocate/remove-job/remove-all must be [] not null raw '"jobs":[]', no \u003c raw "<" and emoji preserved
- Idempotent no-op: re-add node/job diff resources preserves old and allocation running not upsert; same ID concurrent 20 threads -> 1 node/job not 20 sorted lock cleaned checksum valid
- Concurrent add-node 20 diff IDs preserve all 20 sorted, add-job same ID race, same node 20 alloc preserve all 20 used cpu 20 correct no overcommit valid JSON during via O_EXCL, diff nodes 20 status allocated 20, deallocate 20 -> used 0 jobs [], list while allocating 10x30 valid JSON no crash, remove-node while allocating fails/ok not crash, list 100 times 10 threads no crash
- Pagination offset then limit: offset1 limit2 -> 1,2 not 0,1; invalid negative abc -> exit2; limit0 vs omit both all, offset beyond [] both nodes/jobs; list nodes/jobs sorted asc
- First-fit not best-fit: sorted IDs asc first that fits wins even if wasteful (nodeA 10 CPU id smaller vs nodeB 4 CPU both fit 2 CPU -> nodeA wins Step1, Step2 best-fit nodeB wins), fragmented A free2 B free1 C free4 job2 CPU2 -> first-fit picks A not C
- Timestamp integer required: cpu/mem/gpu must be int not float "4.0" -> exit2; empty ID with spaces "   " -> exit2
- Status total/used sum, used/free correct after allocate/deallocate, remove-job deallocates first preserves node free=total, remove false not exist, deallocate false when not allocated vs exit2 nonexist, allocate diff node exit2 same node idempotent no duplicate jobs sorted asc, node jobs sorted after many, insufficient memory/gpu/cpu all insufficient
- File lock cleaned after success and after failure insufficient, no .lock leftover, no .tmp leftover, checksum valid after each op contains CreateTemp Rename SetEscapeHTML stdlib only no dotted imports, lock retry: manually create lock file then thread removes after 100ms command should retry and succeed
- Large scale 800 nodes list <1.5s O(n log n) limit100 offset100 <1.5s 500 jobs sorted, large ID 10KB dash underscore dot colon valid, special chars <>& job and node, unicode job, etc.
- Schedule fragmented, empty jobs after remove all 5 jobs -> [] not null, status pending vs allocated, zero gpu valid neg invalid, etc.



Data directory `/app/data/` writable, default persistence `/app/data/cluster.json`.

## Task – Implement Go Cluster Manager at `/app/` (module `cluster-manager`), built via `go build -o <binary> .`

Stdlib only: `go list -f '{{join .Imports " "}}' .` must contain no dotted imports (only stdlib). `go.mod` must have no external require.

### CLI (MUST - Extra Hard)

Global: `--data` default `/app/data/cluster.json`

Help: bare binary no args must print help containing keywords `add-node`, `remove-node`, `list-nodes`, `get-node`, `add-job`, `remove-job`, `list-jobs`, `get-job`, `allocate`, `deallocate`, `schedule`, `status`, `data`, `checksum` exit0. `--help`, `-h`, `help` also help exit0. Unknown command → exit2, missing required args → exit2, surplus positional arguments → exit2, empty nodeID or jobID → exit2, invalid resources → exit2.

Commands:
```
add-node <nodeID> <cpu> <memory> <gpu>          -> idempotent exit0, fail exit2 if empty ID or cpu<=0 or memory<=0 or gpu<0 or not int, handles 200 nodes sorted, special chars <>&. Re-adding an existing nodeID is a no-op — exit 0, existing resources unchanged (not an upsert).
remove-node <nodeID>                            -> prints true/false, removes node only if no allocated jobs else fail exit2, exit0 even if not exist
list-nodes <limit> <offset>                     -> sorted by id asc; limit=0 returns all; offset beyond the end returns []. JSON array sorted by id asc, each element full node object. Same for list-jobs. Limit and offset optional, invalid → exit2. Performance 100 and 200 <2s
get-node <nodeID>                               -> JSON node, exit2 if not exist or empty ID
add-job <jobID> <cpu> <memory> <gpu>            -> idempotent exit0, fail exit2 if empty ID or invalid resources, handles 200 jobs sorted, special chars <>&, Unicode emoji
remove-job <jobID>                              -> prints true/false, if allocated deallocates first then removes, exit0 even if not exist
list-jobs <limit> <offset>                      -> sorted by id asc; limit=0 returns all; offset beyond the end returns []. JSON array sorted by id asc. Same pagination contract as list-nodes. Limit and offset optional, invalid → exit2
get-job <jobID>                                 -> JSON job, exit2 if not exist or empty ID
allocate <jobID> <nodeID>                       -> allocates job to node if enough free, else fail exit2 stderr "insufficient", job must exist else exit2, node must exist else exit2, if already allocated to different node exit2, same node idempotent exit0, prints JSON allocation
deallocate <jobID>                              -> prints true/false, true if deallocated, false if not allocated, exit2 if job not exist, idempotent
schedule <jobID>                                -> auto-schedules using first-fit sorted node IDs asc, first node that fits, if job already allocated exit2, if no fit exit1 stderr "no fit" no stdout, prints JSON {"job_id":...,"node_id":...,"scheduled":true}
status                                          -> JSON cluster status: {"total_nodes":int,"total_jobs":int,"allocated_jobs":int,"pending_jobs":int,"total_resources":{"cpu":int,"memory":int,"gpu":int},"used_resources":{"cpu":int,"memory":int,"gpu":int}}
```

**Pagination contract (MUST):**
- `list-nodes <limit> <offset>` — sorted by id asc; limit=0 returns all; offset beyond the end returns []. Same for `list-jobs`.
- Both support optional args: `list-nodes`, `list-nodes <limit>`, `list-nodes <limit> <offset>` – same for list-jobs.
- Invalid limit/offset (negative, non-int) → exit2.

Node JSON: `{"id":"node1","total":{"cpu":4,"memory":1024,"gpu":1},"used":{"cpu":1,"memory":256,"gpu":0},"free":{"cpu":3,"memory":768,"gpu":1},"jobs":["job1"]}` jobs sorted. Jobs is always a JSON array; when empty it MUST serialize as [] not null. Same for any empty array field (list-nodes/list-jobs return [] when empty).

Job JSON: `{"id":"job1","required":{"cpu":1,"memory":256,"gpu":0},"node_id":"node1","status":"running"}` or pending when node_id empty. Re-adding an existing job ID is a no-op: exit 0, existing resources and allocation unchanged.

Allocation JSON: `{"job_id":"job1","node_id":"node1","allocated":true}`

Schedule JSON: `{"job_id":"job1","node_id":"node1","scheduled":true}`

### Persistence with Integrity – Explicit File Format (MUST - Extra Hard)

File at `--data` must use wrapper `{"data":{"nodes":{...},"jobs":{...}}, "checksum": md5 canonical}` canonical = `json.dumps(data, sort_keys=True, separators=(',',':'))` no HTML escaping – Go must use `SetEscapeHTML(false)` for checksum and file write. Raw file must contain "<" for special chars and emoji.

Structure of data:
- nodes: map nodeID -> {id, total:{cpu,memory,gpu}, used:{cpu,memory,gpu}, jobs:[sorted]}
- jobs: map jobID -> {id, required:{cpu,memory,gpu}, node_id:"", status:"pending"/"running"}

On write: atomic via `os.CreateTemp` same dir + `os.Rename` plus file lock `<data>.lock` `O_CREATE|O_EXCL` retry loop 5ms 2000 tries + cleanup after each command (lock file must not remain)

Behavioral extra hard (reference gets 20/20):
- Same node: 20 concurrent allocate to same node (different jobs) file never invalid JSON during concurrent, must preserve all 20 jobs, used resources correct, no overcommit
- Different nodes: 20 parallel allocates to 20 different nodes must preserve all 20
- Concurrent add-node: 20 concurrent add-node different IDs must preserve all 20 sorted
- Persistence across restarts with many ops, node IDs with dash/underscore/dot/colon, special chars <>&
- Spaces via Join: if job or node ID contains spaces, must use `strings.Join(remainingArgs, " ")`? Actually IDs are single arg, but for consistency require handling of message with spaces? For cluster, we require that add-node and add-job handle IDs with spaces via Join of remaining? But to keep simple, require that nodeID and jobID are taken as first arg, but if there are spaces in ID, they would be separate args; we will test that implementation must use first arg as ID, not splitting. For special chars, we just test <>& preserved.
- Global ID? Not needed but large history 1000 nodes performance <2s, plus list-nodes sorted 200 nodes, list-jobs 1000
- Edge validation: empty IDs exit2, invalid resources (0, negative, non-int) exit2, missing args exit2, allocate insufficient exit2, remove-node with allocated jobs exit2, schedule no fit exit1, remove non-exist prints false, deallocate not allocated prints false, get non-exist exit2, list empty returns []

On read: missing file → empty store {nodes:{}, jobs:{}}, empty file → empty store, wrapper missing/empty checksum or mismatch or invalid JSON → corruption: backup `<original>.corrupt.<nanosec>` integer nanosec, stderr warning "corrupt" or "checksum", recreate empty valid wrapper.

### Business Rules
- add-node idempotent: re-adding an existing nodeID is a no-op — exit 0, existing resources unchanged (not an upsert). Empty ID exit2, cpu>0 memory>0 gpu>=0 else exit2, handles 200 nodes sorted, special chars <>& no escape, Unicode
- remove-node true/false, fails exit2 if node has allocated jobs, does not clear jobs (must deallocate first), get-node after delete fails exit2
- list-nodes JSON array sorted, get-node exit2 if not exist
- add-job idempotent: re-adding existing job ID is a no-op: exit 0, existing required resources and allocation unchanged. Empty ID exit2, invalid resources exit2, special chars no escape, Unicode emoji 🌍🚀😀 preserved, supports large IDs (10KB)
- remove-job true/false, if allocated deallocates first (node jobs field becomes [] not null, used resources decremented) then removes, exit0 even if not exist
- list-jobs sorted, get-job exit2 if not exist
- allocate: resources check else exit2 stderr "insufficient", job must exist node must exist else exit2, already allocated to different node exit2, same node idempotent exit0, special chars, large IDs
- deallocate true/false, exit2 if job not exist, false if not allocated, when deallocated node's jobs must become [] not null
- schedule: first-fit sorted node IDs asc, first node that fits, if job already allocated exit2, if no fit exit1 stderr "no fit", prints JSON scheduled
- status: returns counts and total/used resources

### Integrity Coverage (30 tests Turn 1, 20 tests Turn 2 – extra hard)
- checksum strict, mismatch/missing/invalid JSON backup integer nanosec, stderr warnings
- atomic all 20 same node, diff nodes all 20, concurrent add-node 20, file lock cleanup
- stdlib only, advisory CreateTemp/Rename
- special chars node+job no escape, Unicode emoji, large 10KB
- global? Not needed but IDs uniqueness, persistence across many ops 20 nodes+20 jobs allocations
- large history 1000 nodes perf latest N? Actually list-nodes 1000 perf <2s, 200 nodes sorted
- edge: empty ID exit2, invalid resources exit2, missing args exit2, insufficient exit2, remove-node with jobs exit2, schedule no fit exit1, etc.

### Exit Codes
0 success, 1 I/O error or no fit (schedule no fit), 2 invalid input. Remove non-exist exit0 prints false, deallocate not allocated exit0 prints false.

### Constraints
- Go stdlib only, build via `go build -o <binary> .`
- Handle `<>&` without escaping for both node and job, plus Unicode
- Atomic CreateTemp+Rename + file lock + cleanup, all 20 concurrent
- Use /tmp/codimango for temp
- Respect --data flag default

### Examples
```bash
go build -o ./cluster-manager .
./cluster-manager --data /app/data/cluster.json add-node node1 4 1024 1
./cluster-manager --data /app/data/cluster.json add-job job1 1 256 0
./cluster-manager --data /app/data/cluster.json allocate job1 node1
./cluster-manager --data /app/data/cluster.json get-node node1
./cluster-manager --data /app/data/cluster.json get-job job1
./cluster-manager --data /app/data/cluster.json schedule job2
./cluster-manager --data /app/data/cluster.json status
./cluster-manager --data /app/data/cluster.json list-nodes
./cluster-manager --data /app/data/cluster.json list-jobs
./cluster-manager --help
```

Implement at `/app` – Turn1.
