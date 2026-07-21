# Elasticsearch-like Search Engine in Go — HARD

Build a HARD mini search engine similar to Elasticsearch in Go. The server must run at `/app` and expose a full HTTP API with positional inverted index, BM25 scoring, advanced query language (boolean, phrase, field-specific, prefix, fuzzy, boost), highlights, aggregations, bulk API, and persistence with recovery.

## Working Directory

All code lives in `/app`. Create a Go module there. Entry must be `main.go` in `/app` so `go run .` and `go build -o /tmp/search-server .` starts server.

## 1. Server

- Listen on `0.0.0.0:$PORT` where `$PORT` env var default `8080`.
- Go only (`net/http` stdlib recommended), no external search library (Bleve, etc.) — implement inverted index yourself.
- Concurrent-safe (RWMutex), must pass `go test -race` style concurrency tests.
- Binary must build, log to stdout, never crash on bad input.

## 2. Document Model

```json
{
  "id": "doc1",
  "title": "Go Programming",
  "body": "Go is a statically typed language great for search engines",
  "tags": ["go", "programming", "search"]
}
```

- `id`: required non-empty unique, upsert on POST same id.
- `title`, `body`: strings, may be empty. Indexed separately but default search searches both.
- `tags`: array strings, preserve original case but filtering/search case-insensitive.

Indexing: Tokenize title and body separately with same analyzer: lowercased, split on `[^a-z0-9]+`, remove empty. Store per-field term frequencies AND positions (position = token index in that field, starting 0). Required for phrase queries.

## 3. HTTP API

### POST /documents
Index doc. Request JSON doc. Response 201 `{"ok":true,"id":"doc1"}`, 400 if id missing/empty or invalid JSON.

### GET /documents/{id}
200 doc JSON, 404 `{"error":"not found"}`.

### DELETE /documents/{id}
200 `{"ok":true}` or 404.

### POST /bulk (NEW - HARD)
Elasticsearch-like bulk API. Request body NDJSON (newline delimited JSON), Content-Type `application/x-ndjson` or `application/json` but parse as lines:

Each action pair:
```
{"index":{"_id":"doc1"}}
{"id":"doc1","title":"...","body":"...","tags":["go"]}
{"index":{"_id":"doc2"}}
{"id":"doc2","title":"..."}
...
```

- Action line must have `index` key with `_id` or `id`. Document line follows and may have its own id (action id takes precedence if present).
- Also support simplified bulk where each line IS a document JSON with id (no action lines) — if line does not contain `index`, treat line itself as doc to index.
- Empty lines ignored.
- Must index all valid docs atomically? Index sequentially, continue on error.
- Response 200:
```json
{
  "errors": false,
  "items": [
    {"index": {"_id":"doc1","status":201}},
    {"index": {"_id":"doc2","status":201}}
  ]
}
```
If a doc fails (missing id), status 400 and `errors:true` but still process rest.

### GET /stats (NEW)
Return index stats:
```json
{"docs":2,"terms":15,"avgdl":6.5}
```
- `docs`: number of docs
- `terms`: number of distinct terms across title+body
- `avgdl`: average document length (title tokens + body tokens) / docs, float.

### GET /search and POST /search
Search docs.

GET params:
- `q`: query string (may include advanced syntax) — optional, empty means match all.
- `tags`: comma-separated tag filter (AND semantics, case-insensitive) — optional.
- `operator`: `AND`/`OR` default `OR` for combining plain terms when no explicit boolean operators.
- `limit`: default 10, max 100, 400 if <0 or invalid.
- `offset`: default 0, 400 if <0.
- `highlight`: `true`/`false` default false — if true, include highlight snippets.

POST /search JSON body (overrides query params if present):
```json
{
  "query": "title:\"go search\"^2 AND body:engine",
  "tags": ["go"],
  "operator": "OR",
  "limit": 10,
  "offset": 0,
  "highlight": true
}
```

Support both `q` and `query` field in POST body.

#### Query Language (HARD — must implement all)

Your query parser must support:

**a) Terms:** `go` — lowercased, analyzed same as docs.

**b) Boolean:** `AND`, `OR`, `NOT` uppercase, with precedence `NOT (3) > AND (2) > OR (1)`, parentheses `( )`. Implicit `AND` before `NOT`: `go NOT java` → `go AND NOT java`. Example: `go AND (search OR index) NOT java`.

**c) Phrase Queries:** Double-quoted strings `"search engine"` — must match docs where terms appear consecutively in same field (title or body) in order, adjacent positions. Tokenize phrase content with same analyzer. Example: title="search engine" matches `"search engine"` but not if terms are separated. Must use positional index. Support multiple phrases.

**d) Field-Specific:** `field:term`, `field:"phrase"`, `field:prefix*`, `field:fuzzy~`, `field:term^2`. Fields allowed: `title`, `body`, `tags`, and default (no field) means search title OR body (union). For `tags:go`, match docs whose tags contain `go` case-insensitive (exact tag, not full-text). For `title:go`, search only title inverted index; `body:search` only body; default searches both.

**e) Prefix:** `sea*` — term ending with `*` matches any indexed term with that prefix. E.g., `sea*` matches `search`, `seal`, `sea`. Implement by scanning distinct terms. For field-specific: `title:sea*` only checks title field terms.

**f) Fuzzy:** `sarch~` — term ending with `~` matches any indexed term with Levenshtein distance ≤1. Implement Levenshtein yourself. Case-insensitive (terms already lowercased). E.g., `sarch~` matches `search` (1 deletion). For field-specific: `title:sarch~`.

**g) Boost:** `term^2`, `term^1.5`, `"phrase"^2`, `field:term^2`, `field:"phrase"^2`. Boost multiplies that clause's score. Default boost 1.0. Parse `^` followed by float. If boost missing or invalid, treat as 1.

**h) Combination:** All above can combine with boolean: `title:"go search"^2 AND body:engine* NOT tags:java`, `go^2 OR (search AND "engine")`, `(title:go OR body:go) AND tags:search`, `sarch~ AND sea*`, etc.

Tokenization for query parser:
- Must preserve quoted phrases as single tokens.
- Must preserve parentheses as tokens.
- Operators AND/OR/NOT case-insensitive recognition but test uses uppercase.
- Field detection: `field:` before term/phrase/prefix/fuzzy.
- Boost: `^number` after term/phrase.
- Prefix `*` at end of term, fuzzy `~` at end.
- Example: `title:"go search"^2 AND sea* NOT tags:java` → tokens: field:title phrase:"go search" boost2, AND, term:sea* (prefix), NOT, field:tags term:java.

If query contains ANY explicit boolean operator (AND/OR/NOT) or parentheses, treat entire query as boolean expression using precedence, ignoring `operator` param for logic (still use operator param for scoring? No, boolean logic overrides). If query has NO boolean operator, use `operator` param (AND/OR) to combine clauses: OR=union, AND=intersection. Phrase, prefix, fuzzy, field queries count as clauses.

#### Scoring — BM25 (HARD)

Must implement BM25, not TF-IDF:

- Parameters: `k1=1.2`, `b=0.75`
- For each doc d, field f (title, body), term t:
  - `tf = term frequency in field f for doc d`
  - `N = total number of docs`
  - `df = number of docs containing term t in field f (or in any field for default)`
  - `idf = log(1 + (N - df + 0.5)/(df + 0.5))` natural log
  - `fieldLen = number of tokens in field f for doc d`
  - `avgFieldLen = average field length across all docs for field f` (for default search, use avg of combined title+body lengths? Simpler: compute avg for title and body separately; for default search score = sum of title BM25 + body BM25)
  - `scoreField = idf * (tf*(k1+1)) / (tf + k1*(1-b + b*fieldLen/avgFieldLen))`
- For default (no field) query: score = scoreTitle + scoreBody (if term appears in both, sum).
- For field-specific: only that field's BM25.
- For `tags` field: if tag matches, score = 1.0 * boost (or 2.0 if you want) — simple, not BM25.
- For phrase query: phrase matches if positional adjacency holds. Score = sum of BM25 scores of each term in phrase (within that field) * boost. If phrase appears in both title and body, sum both.
- For prefix / fuzzy: clause expands to list of matching actual terms. For each doc, score = sum over matching expanded terms of BM25 for that expanded term * boost.
- For boost: final clause score multiplied by boost.
- Total doc score = sum over all positive clauses (NOT clauses excluded from scoring) of their scores.
- If query empty (match all), score=1.0 for all docs.
- Sort by score descending, then id ascending tie-breaker.

Tests will check:
- BM25 ranking: docs with higher tf and shorter field length rank higher than longer docs with same tf.
- Exact BM25 value within tolerance for a known dataset.

#### Highlight (NEW)

If `highlight=true` (GET param or POST body), each result must include `highlight` field: map field -> list of highlighted snippets? Simplified: `highlight` as object with `title` and/or `body` strings where matched query terms are wrapped in `<em>` tags.

For example, doc title="Go Search Engine", query "search", highlight: `{"title":"Go <em>Search</em> Engine"}`.

Requirements:
- Wrap each matched term (case-insensitive, original case preserved inside `<em>`? Or lowercased? Wrap lowercased version is ok) with `<em>` and `</em>`.
- For phrase, highlight whole phrase? Wrapping each term in phrase individually is okay, but must contain `<em>`.
- For prefix/fuzzy expanded terms, highlight the actual matched text in doc that corresponds to expanded term.
- Include highlight only for fields that have matches; if no match but doc matched via tag filter, highlight may be empty or absent — but if highlight requested and doc matched via text, must contain `<em>`.
- In result JSON, `highlight` field is optional but must be present when `highlight=true` and text matches exist.

Example result with highlight:
```json
{
  "total":1,
  "results":[{"id":"doc1","score":1.23,"title":"Go Search","tags":["go"],"highlight":{"title":"Go <em>Search</em>","body":"..."}}],
  "aggregations":{"tags":{"go":1}}
}
```

#### Aggregations (NEW)

Always return aggregations (even if highlight false) for tag counts among matched docs (before pagination):

```json
{
  "total":2,
  "results":[...],
  "aggregations":{"tags":{"go":2,"search":1}}
}
```

- `aggregations.tags`: map lowercased tag -> count of matched docs containing that tag.
- If no matches, `tags` empty object.

#### Response Format

200:
```json
{
  "total": 2,
  "results": [
    {"id":"doc1","score":1.23,"title":"...","tags":["go"],"highlight":{"title":"Go <em>Search</em>"}},
    {"id":"doc2","score":0.89,"title":"...","tags":["go","search"]}
  ],
  "aggregations": {"tags": {"go":2,"search":1}}
}
```

- `total`: matched before pagination.
- `results`: paginated, each at least `id`, `score` float, `title`, `tags`, plus `highlight` if requested.
- `aggregations`: always present.

### 4. Persistence with Recovery (HARD)

- On each successful POST /documents, DELETE /documents/{id}, POST /bulk (each doc), persist entire docs list to `/app/data/index.json` atomically (temp file + rename).
- On startup, if `/app/data/index.json` exists, load:
  - If file contains valid JSON array, load docs and rebuild all indexes (positional, BM25 stats).
  - If file is corrupted/truncated (e.g., last bytes missing due to crash), must recover: read file, try to parse; if JSON invalid, attempt to recover by truncating to last valid JSON array? Simplified: if unmarshal fails, try to read as is and if file ends mid-object, discard corrupted tail and recover up to last complete doc? Minimum requirement: if file is truncated (e.g., ends abruptly), server must still start and not crash, with either 0 docs or partially recovered docs, and must not return 500.
  - Implement robust load: if JSON array parse fails, try to decode stream of JSON objects ignoring errors, or truncate file and rebuild from valid prefix. Even simple handling that returns empty index on corrupt file but doesn't crash passes recovery test.
- Create `/app/data` dir if not exists.
- Atomic write required.

### 5. Non-functional

- Thread-safe for concurrent bulk, index, search, delete, stats.
- Must handle up to 10k docs, 1000 reqs without OOM.
- No external deps except Go stdlib; use go.mod.
- Binary builds with `go build -o /tmp/search-server .`
- `/app/data/index.json` path fixed; also respect `DATA_FILE` env for tests.

### 6. Example Workflows

```
POST /documents {"id":"1","title":"Go Search Engine","body":"build a search engine in Go","tags":["go","search"]}
POST /documents {"id":"2","title":"Java Search","body":"Lucene is Java search library","tags":["java","search"]}
GET /search?q=search -> both
GET /search?q="search engine" -> only doc1 (phrase adjacent)
GET /search?q=title:Go -> only doc1
GET /search?q=tags:go -> only doc1
GET /search?q=sea* -> both (search)
GET /search?q=sarch~ -> both (fuzzy to search)
GET /search?q=go^2 search -> doc1 higher score due to boost
GET /search?q=go&highlight=true -> results have <em>
GET /search -> aggregations tags counts
POST /bulk NDJSON -> bulk index
GET /stats -> docs, terms, avgdl
DELETE /documents/1
GET /search?q=search -> only doc2
# restart -> doc2 persists
```

### 7. Failure Handling

- 400 on invalid JSON, missing id, invalid limit/offset, invalid operator, invalid boost (ignore boost error? return 400 if boost not number).
- 404 for missing doc.
- Empty index: total 0.
- Operator case-insensitive.

### 8. Anti-Requirements

- No Bleve/Elastic client library.
- No hardcoding.
- Must implement positional index yourself.

### 9. Success Criteria

Tests will build Go project, start server on random $PORT, and test:

- CRUD
- Simple AND/OR, boolean with parentheses and NOT
- Phrase queries (adjacent only)
- Field-specific (title:, body:, tags:)
- Prefix (sea*) and fuzzy (sarch~)
- Boost (go^2)
- BM25 scoring exact and ranking
- Highlight <em>
- Aggregations tags
- Bulk API
- Stats
- Tag filter AND semantics, pagination, empty query, persistence with recovery, concurrency, invalid inputs

All must pass.

Implement precisely — tests strict about status codes, JSON shapes, BM25 tolerance, phrase adjacency.
