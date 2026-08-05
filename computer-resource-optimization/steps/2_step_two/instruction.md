# Turn 2: Large-Scale Efficient Cluster Management (Go) – Extra Hard (62 tests)

Turn1 implemented core cluster management with single-file persistence, first-fit scheduling, integrity, atomic writes, corruption handling, concurrent safety.

Now the cluster has grown to 10k nodes and 100k jobs. The naive first-fit scheduler and single-file storage are inefficient (O(n²) and fragmentation). We need to make the system more efficient for large-scale production.

Turn1 code is present via `inherit_prior_session`.

## Task – Extend Go Cluster Manager at `/app/` (same module), built via `go build -o <binary> .`

Must keep Turn1 functionality working in both single-file fallback and new sharded mode.

Stdlib only, `go.mod` no external requires, `go list -f '{{join .Imports " "}}' .` must contain no dotted imports. Must contain `CreateTemp`, `Rename`, `SetEscapeHTML` for atomic writes.

### Flags
- `--data` default `/app/data/cluster.json` – single-file mode (Turn1 compat)
- `--config` default `/app/config.json` – sharded mode config path. If config file exists and valid, sharded mode active, else fallback to single-file.

### Config File Format (for sharding, MUST - Extra Hard)

`/app/config.json`:
```json
{
  "shard_count": 4,
  "shards": [
    {"id": 0, "path": "/app/data/shard_0.json", "weight": 1},
    {"id": 1, "path": "/app/data/shard_1.json", "weight": 2},
    {"id": 2, "path": "/app/data/shard_2.json", "weight": 1},
    {"id": 3, "path": "/app/data/shard_3.json", "weight": 1}
  ],
  "rate_limit": {"allocations_per_second": 1000, "burst": 10000},
  "node_heartbeat_ttl_seconds": 60,
  "ops_log": "/app/data/cluster_ops.log",
  "jobs_path": "/app/data/jobs.json",
  "presence_path": "/app/data/presence.json",
  "rate_limit_path": "/app/data/rate_limit.json",
  "counter_path": "/app/data/counter.json",
  "nodes_index_path": "/app/data/nodes_index.json"
}
```
- `shard_count` >0 else exit2 no stdout, shards unique id, path non-empty, weight>0 else exit2, negative id exit2, duplicate id exit2, empty path exit2.
- `rate_limit` optional default `{"allocations_per_second":5,"burst":10}`
- `node_heartbeat_ttl_seconds` optional default 60
- `ops_log`, `jobs_path`, `presence_path`, `rate_limit_path`, `counter_path`, `nodes_index_path` optional defaults as above.
- **Unknown fields must be ignored** – tolerant: extra fields top-level and inside shards must be ignored, still allow operations succeed. Tests verify `future_field`, `unknown_top_level`, `future_shard_field` ignored.

Validation: bad config (invalid JSON, shard_count≤0, duplicate id, empty path, weight≤0, negative id) → exit2 no stdout only stderr.

### Sharded Mode Semantics
- Nodes sharded via weighted hash of nodeID (same as database-sharding task)
- Jobs stored in `jobs_path` wrapper checksum file (map jobID -> job)
- `add-node <nodeID> <cpu> <mem> <gpu>`: idempotent, creates in designated shard via weighted hash, handles empty ID exit2, invalid resources exit2. If `global:` prefix, treat as broadcast? For cluster, global: returns -1 for get-shard-id but still stored in all shards? Simpler: global: nodes treated as broadcast to all shards for high availability (replicate). But for allocation, any copy can be used. To keep tests simple, implement global: broadcast: create in ALL shards, get-shard-id returns -1, get-shard-path returns comma-separated sorted list. If not global, single shard.
- `remove-node <nodeID>`: prints true/false, checks allocated jobs via jobs file, fails exit2 if node has jobs, else removes from all shards where it exists (for global). Exit0 even if not exist.
- `list-nodes <limit> <offset>` — sorted by id asc; limit=0 returns all; offset beyond the end returns []. Same for `list-jobs`. Now supports pagination for large scale. limit optional integer ≥0, 0/omit=all, offset optional integer ≥0 default 0. Returns sorted nodes array sliced by `sorted[offset:offset+limit]` if limit>0 else `[offset:]`. Invalid limit/offset (negative, non-int) → exit2. Performance: 1000 and 2000 nodes <2s O(n log n) not O(n²).
- `get-node <nodeID>`: works across shards, finds node in its shard (or any for global).
- `add-job`, `remove-job`, `list-jobs <limit> <offset>` — sorted by id asc; limit=0 returns all; offset beyond the end returns []. Same for list-nodes., `get-job` similar, jobs stored in jobs file with pagination. `list-jobs` pagination same semantics, performance 1000 and 500 <2s.

**Pagination contract (MUST):**
- `list-nodes <limit> <offset>` — sorted by id asc; limit=0 returns all; offset beyond the end returns []. Same for `list-jobs`.
- Both support optional args: `list-nodes`, `list-nodes <limit>`, `list-nodes <limit> <offset>` – same for list-jobs.
- Invalid limit/offset (negative, non-int) → exit2.
- `allocate`, `deallocate`, `schedule`, `status` work across shards: allocation needs to read nodes union and jobs, update appropriate shard file and jobs file atomically under global lock, append ops log.
- `schedule <jobID>`: **NOW BEST-FIT instead of first-fit for efficiency**. Among all nodes that fit, choose node with smallest sufficient free resources to reduce fragmentation. Scoring: minimal (free_cpu - req_cpu), tie-breaker minimal (free_mem - req_mem), then minimal (free_gpu - req_gpu), then smallest node ID lexicographically for determinism. Must be deterministic. If job already allocated exit2, if no fit exit1 stderr "no fit" no stdout. Prints JSON scheduled.

Best-fit vs first-fit difference tested: first-fit would pick nodeA (sorted ID) even if wasteful, best-fit must pick nodeB with smaller waste. This proves efficiency improvement.

### Rate Limiting (extra hard) – Per-Node Token Bucket

- Token-bucket per-node for allocation efficiency: each node has bucket {tokens=float, last_refill=nano}
- Config `rate_limit`: allocations_per_second = rate, burst = burst. Default 5,10.
- Bucket initialized tokens=burst, last_refill=now nano.
- Refill: elapsed=(now - last_refill)/1e9, tokens=min(burst, tokens+elapsed*rate), last_refill=now
- Consume: if tokens≥1, tokens-=1 allow persist, proceed allocate/schedule; else fail rate limited, persist refilled tokens, exit1 stderr contains "rate limit" case-insensitive no stdout, must NOT allocate and must NOT append to ops log.
- Per-node independent: allocating to nodeA rate limited, nodeB still succeeds.
- Persistence path `rate_limit_path` wrapper checksum, atomic via CreateTemp+Rename, corruption handling.
- Tests extra hard: burst2 rate1, 2 succeed, 3rd fails exit1 no side effects, per-node independent (nodeB succeeds when nodeA limited), **refill after 1.6s** succeeds, **multiple cycles** 2 succeed fail sleep 1.2s succeed fail sleep 1.2s succeed, persistence across invocations (file contains bucket), corruption handling for rate_limit.json (invalid JSON → bucket reset → allocate succeeds)

### Presence / Node Health (extra hard)

- `heartbeat <nodeID>`: updates last_seen nano in `presence.json` wrapper checksum atomic global lock, requires node exists else exit2
- `get-presence <nodeID>` and `get-node-health <nodeID>`: both aliases return `{"node_id":...,"online":bool,"last_seen":nano,"last_seen_seconds_ago":float}` where `online = now - last_seen <= TTL*1e9`, TTL from config default 60, if never heartbeat online false last_seen 0 last_seen_seconds_ago 0
- `list-healthy` and `list-online`: both aliases sorted healthy nodes within TTL
- Tests extra hard: heartbeat→online, **TTL expiry 2s→3s sleep** offline and list-online excludes, **unknown node** returns online false last_seen 0, **multiple nodes TTL** 3 nodes online, 3s sleep → [] empty, heartbeat bob → [bob], corruption handling for presence.json (checksum mismatch → offline), wrapper checksum strict

### New Commands (MUST)

```
get-shard-id <nodeID>            -> int, weighted hash, -1 for global:. Empty-string key "" is valid, hashed via MD5 (d41d8cd98f00b204e9800998ecf8427e), returns exit 0
get-shard-path <nodeID>          -> path single for normal, comma-separated sorted list for global:. Empty-string key "" is valid, hashed, returns exit 0
distribution                     -> JSON map shard_id (string) -> count nodes including global in each shard (if global broadcast, counts in each), handles 200 nodes, includes zeros
list-nodes <limit> <offset>      -> pagination contract: sorted by id asc; limit=0 returns all; offset beyond the end returns []. Same for list-jobs. Performance 1000 and 2000 <2s
list-jobs <limit> <offset>       -> pagination contract: sorted by id asc; limit=0 returns all; offset beyond the end returns []. Same for list-nodes.
heartbeat <nodeID>               -> updates presence
get-presence <nodeID>            -> alias for health
get-node-health <nodeID>         -> health JSON
list-healthy                     -> alias list-online sorted online within TTL
list-online                      -> sorted online within TTL
snapshot <backup_path>           -> dir mode: mkdir -p and copy shard files+jobs+presence+rate_limit+counter+ops_log+config; file mode: combined JSON file with shards map, jobs, presence, rate_limit, counter, ops_log
restore <backup_path>            -> dir and file modes restore all files via atomic writes, must restore exactly, post-snapshot mutations gone, list-nodes no newnode, list-jobs no newjob, and next allocate still works
ops-log                          -> prints ops.log as JSON array, skips invalid JSON lines with warning stderr "corrupt"/"skip"/"warning", preserves order, content checks op types order, large 100 ops
optimize                         -> efficient defragmentation: tries to consolidate jobs onto fewer nodes using best-fit, prints JSON {"fragmentation_before":float,"fragmentation_after":float,"moves":int,"total_nodes":int,"used_nodes":int}, must not overcommit, must preserve all jobs, reduces fragmentation or keeps same, moves >=0
```

Help must contain: `add-node`, `remove-node`, `list-nodes`, `get-node`, `add-job`, `remove-job`, `list-jobs`, `get-job`, `allocate`, `deallocate`, `schedule`, `status`, `get-shard-id`, `get-shard-path`, `distribution`, `heartbeat`, `get-presence`, `get-node-health`, `list-healthy`, `list-online`, `snapshot`, `restore`, `ops-log`, `optimize`, plus `data`, `checksum`, `shard`, `weight`, `global`

Bare no args → help exit0.

### Weighted Sharding Algorithm (MUST)

- Weight default 1 if missing, must be >0 else invalid config exit2
- Total weight = sum weights
- Hash: MD5 bytes, big-endian int: Python `int(md5(key.encode()).hexdigest(),16)`
- `weighted_index = hashInt % totalWeight`
- Iterate shards sorted by id asc subtracting weight: if weighted_index < shard.weight → pick that shard id else subtract
- Example: 0:w1,1:w2,2:w1,3:w1 total5 → 0→0,1→1,2→1,3→2,4→3
- `global:` prefix → -1 broadcast, `get-shard-path` returns comma-separated sorted list of all shard paths

### Pagination – Extra Hard

- `list-nodes <limit> <offset>`: limit0/all, offset0 default, `sorted[offset:offset+limit]` if limit>0 else `[offset:]`
- Similarly `list-jobs`
- Must handle 100, 500, 1000, 2000 quickly <2s

### Snapshot/Restore – Extra Hard

- `snapshot <backup_path>`: dir mode (no .json suffix or existing dir): mkdir -p, copy each shard file (if exists), jobs_path, presence_path, rate_limit_path, counter_path, ops_log into backup dir basename preserved plus config; file mode (path ends with .json): writes combined JSON file with keys shards map (shard_id->file data), jobs (data), presence, rate_limit, counter, ops_log (array)
- `restore <backup_path>`: dir mode copy files back overwrite, file mode reads combined JSON and restores each component via atomic writes; after restore jobs, nodes, presence, rate_limit, counter must be exactly as snapshot time (tested via that post-snapshot mutated data gone, list-nodes no newnode, and jobs preserved)
- Exit0, must handle global lock

### Integrity & Concurrency – Extra Hard (62 tests)

- Persistence files must use wrapper `{"data":..., "checksum":...}` checksum MD5 canonical `json.dumps(data, sort_keys=True, separators=(',',':'))` `SetEscapeHTML(false)`, atomic via CreateTemp+Rename, tests verify checksum for all sharded files strict
- Corruption handling for all files: invalid JSON → backup `<path>.corrupt.<nanosec>` integer, stderr warning "corrupt" or "checksum", recreate empty valid file. Tests for shard files, jobs.json, presence.json, rate_limit.json, etc.
- Missing checksum and mismatch → corruption handling
- Atomic behavior extra hard:
  - Same node: 20 concurrent allocate, file must remain valid JSON, no overcommit, preserve **all 20 jobs** (extra hard), global.lock cleaned
  - Different nodes: 20 concurrent allocate to 20 different nodes must preserve **all 20** with correct used counts (multi-shard atomic via global lock)
- Stdlib-only imports, advisory CreateTemp+Rename
- Ops-log invalid line skipping with warning, order preserved, content order and large 100 ops, must use bufio.Scanner with big buffer 10*1024*1024 to handle 100KB+ lines
- Config validation and unknown-field tolerance: malformed configs exit2 no stdout, unknown fields ignored, defaults for missing optional, shard_count mismatch lenient not crash
- Weighted distribution 20 exact, 50 tolerance, 100 tolerance (40% weight)
- Global broadcast: if implemented, create in all shards, allocation from any copy works, get returns first found, distribution counts global in each shard
- Spaces handling: nodeID and jobID with spaces? Use first arg as ID, but ensure special chars
- Edge: empty IDs exit2, missing args exit2, invalid limit/offset exit2, nonexist returns [], etc.

### Efficiency Requirements

- Scheduling must be best-fit now (more efficient than Turn1 first-fit) to reduce fragmentation
- Best-fit must pick minimal waste, verified by tests
- Pagination must be O(n) slicing not O(n²), large history tests 1000 bulk500 and 2000 bulk1000 <2s
- Concurrent allocations 20 must preserve all and be atomic via global lock
- Optimize command must not overcommit and should improve or keep fragmentation

### Exit Codes
0 success, 1 I/O or rate-limited or no fit (rate limit → exit1 stderr "rate limit", no fit → exit1 stderr "no fit"), 2 invalid input (bad config, node/job not exist, empty IDs, invalid limit, missing args, unknown command). Remove non-exist exit0 prints false.

### Examples
```bash
go build -o ./cluster-manager .
./cluster-manager --config /app/config.json add-node node1 4 1024 1
./cluster-manager --config /app/config.json get-shard-id node1
./cluster-manager --config /app/config.json get-shard-path node1
./cluster-manager --config /app/config.json distribution
./cluster-manager --config /app/config.json add-job job1 1 256 0
./cluster-manager --config /app/config.json schedule job1
./cluster-manager --config /app/config.json heartbeat node1
./cluster-manager --config /app/config.json get-node-health node1
./cluster-manager --config /app/config.json list-healthy
./cluster-manager --config /app/config.json snapshot /tmp/backup
./cluster-manager --config /app/config.json restore /tmp/backup
./cluster-manager --config /app/config.json optimize
```

Implement at `/app` – Turn2 efficient large-scale.
