# Turn 1: Cluster Manager Core (Easy – 10 tests)

Build a Go CLI binary `cluster-manager` in `/app` (module `cluster-manager`, stdlib only, `go build -o cluster-manager .`).

## Persistence
- Default path `/app/data/cluster.json` override via `--data`.
- Missing file → empty store `{"nodes":{},"jobs":{}}`.
- Wrapper format `{"data":{...},"checksum":MD5(canonical)}` where canonical = `json.dumps(data, sort_keys=True, separators=(',',':'), ensure_ascii=False)`. Use `SetEscapeHTML(false)`.

## CLI
Global: `--data <path>`, help via bare, `--help`, `-h`, `help` must contain `add-node remove-node list-nodes get-node add-job remove-job list-jobs get-job allocate deallocate schedule status data checksum` exit 0. Unknown command, missing args, empty/whitespace ID, invalid resources (cpu<=0 mem<=0 gpu<0) → exit 2.

Commands (all idempotent, sorted asc for list):
- `add-node <id> <cpu> <mem> <gpu>` – preserve old if exists
- `remove-node <id>` – prints true/false lower, fails exit2 if has jobs
- `list-nodes [limit] [offset]` – sorted id asc
- `get-node <id>` – JSON with id, total, used, free, jobs sorted [] not null
- `add-job <id> <cpu> <mem> <gpu>`
- `remove-job <id>` – true/false, deallocates first
- `list-jobs / get-job / allocate / deallocate / schedule / status` – implement basic first-fit for schedule (sorted IDs), insufficient → exit2 stderr "insufficient", no fit → exit1 stderr "no fit"

Node: `{"id":...,"total":{"cpu":...,"memory":...,"gpu":...},"used":{...},"free":{...},"jobs":[sorted]}`
Job: `{"id":...,"required":{"cpu":...,"memory":...,"gpu":...},"node_id":...,"status":"pending"|"running"}`

Build and test via `/app/cluster-manager --data /app/data/cluster.json <cmd>`
