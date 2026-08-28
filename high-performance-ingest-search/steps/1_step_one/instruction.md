# Step 1: Build Log Ingest and Search System in Go

Build a **log/observability ingest and full-text search service** in Go. This is the foundation that will be optimized for high performance in Step 2.

## Working Directory

`/app`. You must create `main.go` (and `go.mod`) supporting:

```
go run .            # listens on 0.0.0.0:$PORT (default 8080, env PORT)
go build -o /tmp/search-server .
/tmp/search-server  # same behavior
```

## Constraints

- **Go only**, stdlib only for core logic. No external search libraries: `go.mod` must NOT contain `bleve`, `elastic`, `elasticsearch`, `algolia`, `meilisearch`, `sonic`, `tantivy`, `lucene` (case-insensitive check). Standard library plus `golang.org/x/*` allowed but prefer stdlib.
- Must be concurrent-safe (use `sync.RWMutex` or equivalent). Must not crash on concurrent ingest+search.
- No crash on bad input; correct HTTP status codes.
- Must persist data to `/app/data/index.json` atomically (temp file + rename) and maintain WAL at `/app/data/wal.log` for recovery.

## Document Model

Log entry:

```json
{
  "id": "log-1",
  "timestamp": "2026-07-20T10:00:00Z",
  "service": "auth-service",
  "level": "info",
  "message": "User login successful for user_42",
  "tags": ["auth", "login"]
}
```

- `id`: required non-empty string, unique, **upsert semantics** (re-index on same id).
- `timestamp`: required RFC3339 string, e.g. `2026-07-20T10:00:00Z`, `2026-07-20T10:00:00.123Z`, with zone offset allowed. Must be parseable by `time.RFC3339` or `RFC3339Nano`. If invalid → 400.
- `service`: required non-empty string. Lowercase normalized for filtering but preserve original case in stored doc.
- `level`: required, must be one of `debug`, `info`, `warn`, `error` case-insensitive; store lowercased. Invalid → 400.
- `message`: string, may be empty. Full-text indexed.
- `tags`: optional array of strings, case-insensitive filtering, may be absent (treat as empty). Must be array if present else 400.

## Tokenization for Full-Text Search

Message field tokenization for inverted index:

- Split on `[^A-Za-z0-9]+` (non-alphanumeric), preserve case then lowercase tokens.
- Drop empty tokens.
- Example: `"User login: user_42 failed!"` → `["user","login","user","42","failed"]`

Positional index is **NOT required** in Step 1 (simple term → doc set with term frequencies), but you must maintain term frequency for scoring.

## Inverted Index

- `term -> map[docID]tf` plus per-doc token counts for length normalization.
- Document store `id -> document`.
- Also maintain service and level and tags indexes for fast filtering (or brute-force filter after full-text, but must be correct).
- Must be rebuilt on startup from persistence (see Persistence).

## HTTP API

### POST /ingest

Content-Type: application/json

Body:
```json
{
  "entries": [
    {"id":"log-1","timestamp":"2026-07-20T10:00:00Z","service":"auth","level":"info","message":"...","tags":[...]},
    ...
  ]
}
```

- `entries` must be array, required. Empty array → 201 with ingested 0.
- Each entry validated as per Document Model. If any entry invalid, return 400 with `{"error":"..."}` and **no side effects for that request** (atomic per-request: either all valid entries are ingested or none if any invalid? For simplicity: requirement is to reject entire request if any entry invalid → 400 and ingest 0).
- On success: upsert all entries, update indexes, persist, append WAL, return 201:

```json
{"ingested": 2, "failed": 0}
```

- Must support upsert: same id overwrites.

### POST /ingest/bulk (Optional in Step 1 but recommended)

NDJSON body (Content-Type: application/x-ndjson or application/json but body is NDJSON lines):

Each line is a JSON document (log entry). Empty lines ignored.

Same validation as /ingest, but per-line errors allowed: ingest valid lines, skip invalid with error list.

Response 201:
```json
{"ingested": 10, "failed": 2, "errors": [{"line": 3, "error": "invalid level"}]}
```

If you don't implement this in Step 1, the endpoint may return 404 — tests will skip bulk check. But Step 2 **requires** this endpoint, so implement now if possible.

### GET /documents/{id}

- 200 returns document JSON as stored (with normalized service lowercasing? Store service lowercased for consistency, level lowercased, original message preserved, tags lowercased? For test simplicity: service stored lowercased, level lowercased, tags lowercased, message preserved, timestamp preserved as original RFC3339 string but normalized to UTC? To keep simple: store timestamp as original string provided, but also parse for sorting/filtering. Tests will check fields after lowercasing where noted. **Spec**: stored doc must preserve `id`, `message` as given, `timestamp` as given (but must be valid RFC3339), `service` lowercased, `level` lowercased, `tags` lowercased array if present.
- 404 `{"error":"not found"}` if missing.

### DELETE /documents/{id}

- 200 `{"ok":true}` if deleted.
- 404 if not found.

### GET /search

Query params:

- `q`: full-text query string on message field. Space-separated terms, **AND semantics**: all terms must be present in message (tokenized same as indexing). Example `q=login failed` matches docs whose message contains both `login` AND `failed`. If `q` missing or empty or whitespace only → match-all.
  - Support phrase query: `"user login"` in double quotes must match adjacent tokens in order. For Step 1, phrase is **required**: token sequence must appear consecutively in message token list. Example message `"User login successful"` tokenized `["user","login","successful"]`, phrase `"user login"` matches, `"login user"` does NOT (order matters), `"user successful"` does NOT (not adjacent).
  - Mixed: `q=error "login failed"` → must have term `error` AND phrase `login failed` adjacent.
  - Tokenization of q: need to parse quoted phrases preserved, then split remaining on whitespace, then each term tokenized with same analyzer (non-alnum split + lowercased). But for phrase terms, preserve order after tokenization.
  - If phrase contains only non-alphanumeric or empty after trimming inside quotes `""` or `"   "` → treat as empty query? Spec: **empty phrase `""` or whitespace-only phrase `"   "` → 400**.
- `service`: exact match filter, case-insensitive (lowercase compare). Optional.
- `level`: exact match filter, case-insensitive, must be valid level if provided else 400. Optional.
- `tags`: comma-separated AND filter: `tags=auth,login` means doc must contain **all** listed tags (case-insensitive). Optional. Example `?tags=auth` → docs containing auth tag.
- `from`: RFC3339 start timestamp inclusive, optional. If invalid → 400.
- `to`: RFC3339 end timestamp inclusive, optional. If invalid → 400.
- `limit`: int default 10, max 100, min 0. `limit>100` clamped to 100 not 400. Negative → 400. Non-numeric or float → 400.
- `offset`: int default 0, must be >=0 else 400. Non-numeric/float → 400.
- `sort`: optional `timestamp:asc` or `timestamp:desc` (default `timestamp:desc`). Case-insensitive. Invalid → 400. Tie-break by id ascending.

Response 200:

```json
{
  "total": 100,
  "results": [
    {"id":"log-1","timestamp":"...","service":"auth","level":"info","message":"...","tags":["auth"],"score":2.0},
    ...
  ],
  "took_ms": 5
}
```

- `total`: number of matching docs before pagination.
- `results`: paginated after sorting. Each result includes **at least** `id, timestamp, service, level, message, tags, score`. `score` is float: simple TF score = sum of term frequencies of query terms in message (for phrase, sum of frequencies of terms in phrase). If q empty, score=1.0. If no message match but filtered by service/level (empty q), score=1.0.
- `took_ms`: query time in ms (can be measured approximate, must be >=0).
- Sorted as per `sort`, tie-break id asc.

### GET /stats

200:
```json
{
  "docs": 100,
  "services": 5,
  "levels": {"debug":10,"info":50,"warn":20,"error":20},
  "terms": 1234
}
```

- `docs`: int number docs.
- `services`: int distinct services count (case-insensitive lowercased distinct).
- `levels`: map level→count.
- `terms`: int distinct terms in message index.

Exact keys required: `docs, services, levels, terms` only.

### GET /health

200 `{"status":"ok"}` (for readiness probe).

### Persistence & WAL

- On each successful ingest (POST /ingest), delete, upsert via GET's doc paths that modify state? Actually only ingest and delete modify.
- Persist docs to `/app/data/index.json`: JSON array of docs or object map `id->doc`. Either format acceptable as long as you can load it. Atomic write: write temp file then rename.
- WAL: append to `/app/data/wal.log` one JSON line per operation:

  - Ingest/upsert: `{"op":"index","doc":{...},"ts":"RFC3339","checksum":"<crc32 hex of doc JSON string>"}`
  - Delete: `{"op":"delete","id":"xxx","ts":"...","checksum":"<crc32 hex of id>"}`
  - Checksum: IEEE CRC32, hex encoded lower 8 chars.
  - Create `/app/data` dir if needed.

- On startup:
  - Try load `/app/data/index.json` if exists.
    - If corrupted/truncated, recover what you can (do not crash) — e.g., if JSON array truncated, use streaming decoder or best-effort parse up to last valid doc.
  - Then replay `/app/data/wal.log` if exists:
    - Read line by line, JSON parse, verify checksum (skip invalid checksum lines), apply operation in order (index upsert, delete).
    - If line corrupted/truncated, skip and continue (log to stderr, do not crash).
  - Rebuild indexes from final doc set.

- Respect `DATA_FILE` env var for custom index path; WAL path is `Dir(DATA_FILE)/wal.log` if DATA_FILE set, else `/app/data/wal.log`.

## Failure Handling

- 400 on invalid JSON, missing/invalid fields, invalid level, invalid timestamp, empty phrase `""`, invalid limit/offset, invalid from/to, invalid sort, invalid tags format (not array for ingest).
- 404 for missing doc.
- 201 for successful ingest.
- 200 for search/stats/health/get/delete.

## Example Workflow

```
POST /ingest {"entries":[{"id":"1","timestamp":"2026-07-20T10:00:00Z","service":"auth","level":"info","message":"User login successful","tags":["auth"]}]}
GET /search?q=login -> returns doc 1
GET /search?service=auth&level=info
GET /search?q="login successful" -> phrase match
GET /search?from=2026-07-20T00:00:00Z&to=2026-07-20T23:59:59Z
DELETE /documents/1
GET /stats
```

## Success Criteria for Step 1

Tests will build Go binary and start server on random PORT, then check:

- CRUD ingest, get, delete, upsert
- Full-text search AND semantics and phrase adjacency
- Filters service, level, tags AND, from/to time range, sort timestamp asc/desc, pagination limit/offset
- Stats endpoint
- Persistence: index + WAL replay after restart, truncated recovery
- Concurrency: 20 concurrent ingest + search without crash/data race (server must not crash; Go race detector not required but RWMutex expected)
- Invalid inputs 400 handling (empty phrase, bad level, bad timestamp, bad limit)

All must pass before Step 2.

## Notes

- Keep implementation simple but correct for Step 1; performance is evaluated in Step 2.
- Ensure `go run .` and built binary both work.
- Log to stdout/stderr, not to file.
