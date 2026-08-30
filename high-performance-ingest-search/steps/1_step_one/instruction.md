# Step 1: Log Ingest and Search Service

Build a log ingest and full-text search HTTP service in Go. This is the correctness baseline for Step 2.

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
  Phrase: `"user login"` must be adjacent tokens in order. Mixed `q=error "login failed"` → term AND phrase. Tokenization same as indexing. Empty phrase `""` or whitespace-only `"   "` → 400. **Unclosed quote** e.g. `"login` or `"` or `"user` without closing `"` → 400.
- `service`: exact match filter case-insensitive.
- `level`: exact match filter case-insensitive, must be valid else 400.
- `tags`: comma-separated AND filter, case-insensitive.
- `from`, `to`: RFC3339 inclusive, invalid → 400.
- `limit`: int default 10, max 100 capped not 400, min 0. Negative or non-numeric/float → 400.
- `offset`: int default 0, >=0 else 400, non-numeric/float → 400.
- `sort`: one of `timestamp:asc`, `timestamp:desc`, `relevance` (case-insensitive). Default `timestamp:desc`. Invalid → 400.
  - `timestamp:asc` / `timestamp:desc`: order by timestamp, tie-break id ascending.
  - `relevance`: order by score descending, then timestamp descending, then id ascending. Note the directions differ per level.

Response 200:
```json
{"total": 100, "results": [{"id":"...","timestamp":"...","service":"...","level":"...","message":"...","tags":[...],"score":2.0}], "took_ms": 5}
```
`score`: sum TF of query terms (for phrase sum of terms in phrase). Empty q score=1.0.

### GET /stats
200 `{"docs":100,"services":5,"levels":{"debug":10,"info":50,"warn":20,"error":20},"terms":1234}` exact keys.

### GET /health
200 `{"status":"ok"}`

### POST /ingest/bulk
- NDJSON body, must accept both `Content-Type: application/x-ndjson` and `application/json` (treat body as NDJSON lines regardless of header).
- Each line JSON doc, empty lines ignored, `{"truncated":` or non-JSON lines are invalid.
- Success 201: `{"ingested":10,"failed":2,"errors":[{"line":3,"error":"..."}]}`. Per-line errors allowed: ingest valid lines, skip invalid ones. Empty body → 201 with ingested 0.
- Must be implemented in Step 1 (scoring better when implemented) and is required in Step 2. Do not return 404.

## Persistence & WAL — Core discriminator for Step 1

This is the main correctness lever where many naive implementations fail.

- On ingest/delete, persist docs to data file (array) atomically: write temp file then rename.
- WAL line format (one JSON per line):
  - Index: `{"op":"index","doc":{"id":"...","timestamp":"...","service":"...","level":"...","message":"...","tags":[...]},"ts":"RFC3339","checksum":"<crc32 hex>"}`
    Checksum = IEEE CRC32 (lower 8 hex chars) of the **raw bytes** of the `doc` JSON object as it appears in WAL line (compact, no spaces if you use `json.Marshal`).
  - Delete: `{"op":"delete","id":"xxx","ts":"...","checksum":"<crc32 hex of id string bytes>"}`
- `DATA_FILE` env var overrides index path; WAL is `Dir(DATA_FILE)/wal.log` else `/app/data/wal.log`. Must create dir if needed.
- Startup recovery (critical):
  1. Try load `index.json` if exists. If corrupted/truncated mid-array or mid-object (e.g. file ends with `{"truncated":`), **recover best-effort** using streaming `json.Decoder` to parse up to last valid doc, skip truncated tail, do not crash.
  2. Then replay `wal.log` line-by-line **in order**:
     - Skip empty lines.
     - If line invalid JSON → skip (log to stderr, continue).
     - If line truncated (e.g. `{"truncated":` ) → skip.
     - Verify checksum: for `index` op, compute CRC32 of raw `doc` JSON bytes (`json.RawMessage`) and compare to `checksum` field; if mismatch → skip (reject). For `delete` op, CRC32 of id string bytes.
     - If checksum OK, apply op (upsert or delete).
     - Valid entries after corrupt/bad-checksum lines must still be replayed.
  3. Rebuild indexes from final doc set.
  - Must NOT crash on any corrupt file. Log errors to stderr.
- Durability: WAL must be written reliably per batch (fsync not required but file must contain entry after ack). After `POST /ingest` returns 201, killing server **immediately with SIGKILL (-9)** (no graceful shutdown, no sleep) must still recover doc via WAL replay. Tests cover this.

Tests inject: bad checksum line, non-JSON line, truncated JSON line, mid-record truncation in index.json, and SIGKILL-immediately-after-ack.

## Failure Handling

400 invalid JSON/fields, level, timestamp, empty phrase, unclosed quote, limit/offset, from/to, sort, tags not array. 404 missing doc. 201 ingest success. 200 others.

## Success Criteria

Tests build binary and start on random PORT, check CRUD, upsert, AND semantics, phrase adjacency incl unclosed quote 400, filters, time range, sort, pagination, stats, bulk (required) accepts both content-types, persistence (index+WAL replay, truncated recovery mid-record, checksum rejection, corrupt-line skip, SIGKILL durability), DATA_FILE handling, go.mod forbidden libs, concurrency, invalid inputs.
