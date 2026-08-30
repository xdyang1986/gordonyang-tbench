# Step 1: Log Ingest and Search Service

Build a log ingest and full-text search HTTP service in Go. This will be the correctness baseline for Step 2.

## Working Directory

`/app`. You must create `main.go` (and `go.mod`) supporting:

```
go run .            # listens on 0.0.0.0:$PORT (default 8080, env PORT)
go build -o /tmp/search-server .
/tmp/search-server  # same behavior
```

## Constraints

- **Go only**, stdlib preferred. `go.mod` must NOT contain forbidden search libs: `bleve`, `elastic`, `elasticsearch`, `algolia`, `meilisearch`, `sonic`, `tantivy`, `lucene` (case-insensitive).
- Concurrent-safe: must not crash or corrupt under concurrent ingest + search.
- No crash on bad input; proper HTTP codes.
- Persist to `/app/data/index.json` atomically (temp + rename) and maintain WAL at `/app/data/wal.log`.

## Document Model

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

- `id`: required non-empty string, unique, upsert semantics.
- `timestamp`: required RFC3339, e.g. `2026-07-20T10:00:00Z` or nano + zone offset. Must parse with `time.RFC3339` or `RFC3339Nano`. Invalid → 400.
- `service`: required non-empty string, stored lowercased, filter case-insensitive.
- `level`: required, one of `debug`, `info`, `warn`, `error` case-insensitive; stored lowercased. Invalid → 400.
- `message`: string, may be empty, full-text indexed. Preserve original.
- `tags`: optional array of strings. Lowercased. If present must be array else 400.

## Tokenization

Message tokenization: split on `[^A-Za-z0-9]+`, lowercase tokens, drop empty. Example `"User login: user_42 failed!"` → `["user","login","user","42","failed"]`.

## HTTP API

### POST /ingest
`{"entries":[{...},...]}`

- `entries` required array. Empty → 201 with ingested 0.
- If any entry invalid → 400 `{"error":"..."}` and no side effects for that request (atomic).
- Success 201: `{"ingested": 2, "failed": 0}`. Upsert semantics.
- Persist and WAL after success.

### GET /documents/{id}
200 doc JSON as stored (`id`, `message` preserved as given, `timestamp` preserved string, `service` lowercased, `level` lowercased, `tags` lowercased).
404 `{"error":"not found"}`.

### DELETE /documents/{id}
200 `{"ok":true}` or 404.

### GET /search
Query params:
- `q`: full-text on message. Space-separated terms, AND semantics. Empty/missing → match-all.
  Phrase: `"user login"` must be adjacent tokens in order. Mixed `q=error "login failed"` → term AND phrase. Tokenization same as indexing. Empty phrase `""` or whitespace-only `"   "` → 400. Support unclosed quote handling as 400.
- `service`: exact match filter case-insensitive.
- `level`: exact match filter case-insensitive, must be valid else 400.
- `tags`: comma-separated AND filter, case-insensitive.
- `from`, `to`: RFC3339 inclusive, invalid → 400.
- `limit`: int default 10, max 100 capped not 400, min 0. Negative or non-numeric/float → 400.
- `offset`: int default 0, >=0 else 400, non-numeric/float → 400.
- `sort`: `timestamp:asc` or `timestamp:desc` default desc, case-insensitive, invalid → 400. Tie-break id asc.

Response 200:
```json
{"total": 100, "results": [{"id":"...","timestamp":"...","service":"...","level":"...","message":"...","tags":[...],"score":2.0}], "took_ms": 5}
```
`score`: sum TF of query terms (for phrase sum of terms in phrase). Empty q score=1.0.

### GET /stats
200 `{"docs":100,"services":5,"levels":{"debug":10,"info":50,"warn":20,"error":20},"terms":1234}` exact keys.

### GET /health
200 `{"status":"ok"}`

### POST /ingest/bulk (Optional in Step 1)
NDJSON body. Each line JSON doc, empty lines ignored. Per-line errors allowed: ingest valid, skip invalid with error list. Response 201 `{"ingested":10,"failed":2,"errors":[{"line":3,"error":"..."}]}`. If not implemented, may return 404 — tests will skip, but Step 2 requires it.

## Persistence & WAL

- On ingest/delete, persist docs to data file (array or map) atomically and append WAL.
- WAL line: `{"op":"index","doc":{...},"ts":"RFC3339","checksum":"<crc32 hex of doc JSON>"}`, delete: `{"op":"delete","id":"xxx","ts":"...","checksum":"<crc32 hex of id>"}`. IEEE CRC32 hex 8 chars lower.
- `DATA_FILE` env var overrides index path; WAL is `Dir(DATA_FILE)/wal.log` else `/app/data/wal.log`.
- Startup: load index.json if exists. If corrupted/truncated, recover best-effort (streaming decoder). Replay WAL line-by-line: skip invalid JSON, invalid checksum, corrupt/truncated lines, log to stderr, do not crash. Rebuild indexes.

## Failure Handling

400 invalid JSON/fields, level, timestamp, empty phrase, limit/offset, from/to, sort, tags not array. 404 missing doc. 201 ingest success. 200 others.

## Success Criteria

Tests build binary and start on random PORT, check CRUD, upsert, AND semantics, phrase adjacency, filters, time range, sort, pagination, stats, persistence (index+WAL replay, truncated recovery, checksum rejection, corrupt-line skip), DATA_FILE handling, go.mod forbidden libs, concurrency (multiple ingest+search, no crash/race), invalid inputs 400.
