# Turn 1: Computer Cluster Management System Core (Go) – Extra Hard (58 tests)

We need a production-grade computer cluster management system in Go that manages compute nodes and jobs with resource allocation. Build core functionality with durable persistence and integrity. This turn is extra hard: 58 tests, 20 concurrent allocs all 20, 1000 nodes history, 200 nodes/200 jobs sorted, checksum strict, special chars <>& no HTML escape, spaces handling via Join, global IDs, edge validation, Unicode.

Data directory `/app/data/` writable, default persistence `/app/data/cluster.json`.

## Task – Implement Go Cluster Manager at `/app/` (module `cluster-manager`), built via `go build -o <binary> .`

Stdlib only: `go list -f '{{join .Imports " "}}' .` must contain no dotted imports (only stdlib). `go.mod` must have no external require.

### CLI (MUST - Extra Hard)

Global: `--data` default `/app/data/cluster.json`

Help: bare binary no args must print help containing keywords `add-node`, `remove-node`, `list-nodes`, `get-node`, `add-job`, `remove-job`, `list-jobs`, `get-job`, `allocate`, `deallocate`, `schedule`, `status`, `data`, `checksum` exit0. `--help`, `-h`, `help` also help exit0. Unknown command → exit2, missing required args → exit2, empty nodeID or jobID → exit2, invalid resources → exit2.

Commands:
```
add-node <nodeID> <cpu> <memory> <gpu>          -> idempotent exit0, fail exit2 if empty ID or cpu<=0 or memory<=0 or gpu<0 or not int, handles 200 nodes sorted, special chars <>&
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

Node JSON: `{"id":"node1","total":{"cpu":4,"memory":1024,"gpu":1},"used":{"cpu":1,"memory":256,"gpu":0},"free":{"cpu":3,"memory":768,"gpu":1},"jobs":["job1"]}` jobs sorted.

Job JSON: `{"id":"job1","required":{"cpu":1,"memory":256,"gpu":0},"node_id":"node1","status":"running"}` or pending when node_id empty.

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
- add-node idempotent, empty ID exit2, cpu>0 memory>0 gpu>=0 else exit2, handles 200 nodes sorted, special chars <>& no escape, Unicode
- remove-node true/false, fails exit2 if node has allocated jobs, does not clear jobs (must deallocate first), join after delete? Actually get-node after delete fails exit2
- list-nodes JSON array sorted, get-node exit2 if not exist
- add-job idempotent, empty ID exit2, invalid resources exit2, special chars no escape, Unicode emoji 🌍🚀😀 preserved, large 10KB ID? Actually job ID may be large? We test large message 10KB? For cluster, we test large node IDs? Keep 10KB handling for ID? We will test large ID 10KB maybe.
- remove-job true/false, if allocated deallocates and updates node used, then removes, exit0 even if not exist
- list-jobs sorted, get-job exit2 if not exist
- allocate: member resources check else exit2 insufficient, job must exist node must exist else exit2, already allocated to different node exit2, same node idempotent exit0, special chars, large
- deallocate true/false, exit2 if job not exist, false if not allocated
- schedule: first-fit sorted node IDs asc, first node that fits, if job already allocated exit2, if no fit exit1 stderr "no fit", prints JSON scheduled
- status: returns counts and total/used resources

### Integrity Coverage (58 tests extra hard)
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
