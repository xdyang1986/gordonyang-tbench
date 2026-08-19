# Computer Resource Optimization – Multi-Turn Go Task (Easy)

Two-turn Terminal-Bench task implementing cluster manager in Go.

## Turn 1: Core (10 tests, easy)
Build at `/app/` module `cluster-manager`, `go build -o cluster-manager .`, stdlib only.

Persistence: `/app/data/cluster.json` via `--data`, wrapper `{"data":{"nodes":{...},"jobs":{...}},"checksum":md5(canonical)}` where canonical = `json.dumps(data, sort_keys=True, separators=(',',':'), ensure_ascii=False)`, raw UTF-8, `<` not escaped via `SetEscapeHTML(false)`. Missing file → empty.

CLI: help via bare, --help, -h, help contains keywords. Unknown cmd, missing args, empty/whitespace ID, invalid resources (cpu<=0 mem<=0 gpu<0) → exit2.

Commands: add-node idempotent, remove-node true/false fails if has jobs, list-nodes [limit] [offset] sorted asc limit0 all, get-node, add-job, remove-job deallocates first jobs [] not null, list-jobs, get-job, allocate insufficient exit2, deallocate, schedule first-fit sorted IDs, no fit exit1, status sums.

See steps/1_step_one/instruction.md

## Turn 2: Sharded (10 tests, easy)
Extends Turn1 via inherit_prior_session. Adds --config /app/config.json.
Missing → fallback single-file, invalid (bad JSON, missing shard_count<=0, empty shards, dup id, empty path, weight<=0) → exit2 no stdout.
Weighted hash MD5: totalWeight sum weights, hashInt int(MD5(key).hexdigest,16), index hash%total, iterate sorted by id.
global: prefix → broadcast -1.

Schedule now BEST-FIT: minimal free_cpu-req, tie mem, tie gpu, tie ID lex.

New cmds: get-shard-id, get-shard-path, distribution, heartbeat, get-presence etc. Help must contain those plus data, checksum, shard, weight, global.

See steps/2_step_two/instruction.md

## Build
`go build -o ./cluster-manager .`

## Validation
- Step1: 10/10 PASS easy core
- Step2: 10/10 PASS easy sharded best-fit
