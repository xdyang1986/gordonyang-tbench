# Elasticsearch-like Search Engine in Go

Build a mini search engine similar to Elasticsearch in Go. The server must run at `/app` and expose an HTTP API with inverted index, TF-IDF scoring, Boolean query parsing, tag filtering, and persistence.

## Working Directory

All your code lives in `/app`. Create a Go module there if not exists.

## Requirements

### 1. Server

- Listen on port from env `PORT`, default `8080`. Must be `0.0.0.0:$PORT`.
- Implement in Go (any framework allowed, but `net/http` stdlib is enough).
- Must be concurrent-safe (use RWMutex or similar).
- Log to stdout, no crash on bad input.
- Entry point: `go run .` or compiled binary from `go build -o server .` must start the server. So `main` package must be in `/app` or `/app/cmd/server` is NOT allowed — keep `main.go` at `/app/main.go`.

### 2. Document Model

Each document:
```json
{
  "id": "doc1",
  "title": "Go Programming",
  "body": "Go is a statically typed language great for search engines",
  "tags": ["go", "programming", "search"]
}
```

- `id`: required, non-empty string, unique. Upsert semantics: POST same id overwrites.
- `title`: string (may be empty)
- `body`: string (may be empty)
- `tags`: array of strings (lowercase normalization expected, but preserve original case in storage; filtering should be case-insensitive)
- Internally, index `title + " " + body` as full-text.

### 3. HTTP API

#### POST /documents
Index a document.

Request:
```json
{"id":"doc1","title":"...","body":"...","tags":["go"]}
```

Response:
- 201: `{"ok": true, "id": "doc1"}`
- 400 if id missing/empty.

#### GET /documents/{id}
Retrieve document.

- 200: returns stored document JSON.
- 404: `{"error":"not found"}` if missing.

#### DELETE /documents/{id}
Delete document and remove from index.

- 200: `{"ok": true}`
- 404 if not found: still return 404 with `{"error":"not found"}`

#### GET /search  and POST /search
Search documents.

GET query params:
- `q`: query string (optional). If missing/empty, match all docs (before tag filtering).
- `tags`: comma-separated tag filter (e.g., `tags=go,search`). Doc must contain ALL specified tags (AND). Case-insensitive. Optional.
- `operator`: `AND` or `OR` (default `OR`) for how terms in `q` combine when boolean operators NOT explicitly in query. Optional.
- `limit`: int default 10, max 100.
- `offset`: int default 0.

POST /search JSON body (overrides GET params if both present, but support both):
```json
{
  "query": "go AND search",
  "tags": ["go"],
  "operator": "OR",
  "limit": 10,
  "offset": 0
}
```

Search behavior:

**Tokenizer**: Must normalize case (lowercase), split on any non-alphanumeric (regex `[^a-z0-9]+`), remove empty tokens. Apply to both documents and query.

**Inverted Index**: Maintain `term -> docID -> termFrequency`. And `docFreq` for IDF.

**Boolean Query Parsing** (required):

Support operators `AND`, `OR`, `NOT` (uppercase) and parentheses `(` `)` . Examples:

- `go` -> single term
- `go search` -> if operator=OR, docs with go OR search; if AND, go AND search.
- `go AND search`
- `go OR search`
- `go AND NOT java` , `go NOT java` (interpret `A NOT B` as `A AND NOT B`)
- `go AND (search OR index) NOT java`
- `NOT go` -> docs NOT containing go

Precedence: `NOT` highest (unary), `AND` medium, `OR` lowest. Parentheses override.

You must implement a tokenizer for query that preserves operators and parentheses as separate tokens, while terms are lowercased.

- Example tokenization: `"Go AND (Search OR Index) NOT Java"` => terms/operators: `go`, `AND`, `(`, `search`, `OR`, `index`, `)`, `NOT`, `java`

If query contains any explicit boolean operator (`AND`, `OR`, `NOT`) or parentheses, treat entire query as boolean expression using precedence above. Ignore `operator` param in that case (use explicit logic).

If query contains NO boolean operator, use `operator` param (default OR) to combine terms: OR = union, AND = intersection.

**Scoring**: TF-IDF simplified:

- `tf = term frequency in doc` (count of term in title+body, after tokenization)
- `df = number of docs containing term`
- `N = total number of docs`
- `idf = log((N+1)/(df+1)) + 1`  (natural log, use math.Log)
- Score per doc = sum over query terms (that participate positively) of `tf * idf`

Important: For boolean queries:
- For AND/OR, all query terms that are positively required contribute to scoring.
- For NOT clauses, terms under NOT should NOT contribute to scoring, and should exclude docs.
- Example: `go AND NOT java` -> only `go` contributes to score, but docs with java excluded.

If query empty, all docs score = 1.0 (or 0, but must be consistent, and sortable).

Sort by score descending, then by id ascending as tie-breaker.

**Tag Filtering**: After boolean matching, filter by tags: doc must contain ALL tags specified (case-insensitive). Tags filter applies even when q empty.

**Pagination**: Apply `offset` and `limit` after sorting.

Response format 200:
```json
{
  "total": 2,
  "results": [
    {"id":"doc1","score":1.23,"title":"...","tags":["go"]},
    {"id":"doc2","score":0.89,"title":"...","tags":["go","search"]}
  ]
}
```

- `total`: total number of matched docs BEFORE pagination.
- `results`: paginated slice. Each result must contain at least `id` and `score`. Including `title` and `tags` is required per tests.
- `score` float.

Return docs even if title/body empty but matches tags or empty query.

### 4. Persistence

- On each successful POST /documents or DELETE /documents/{id}, persist entire state to `/app/data/index.json`
- Format: you choose, but must contain all docs and be loadable on restart. JSON array of documents is sufficient, you can rebuild inverted index on load, OR persist full index.
- On startup, if `/app/data/index.json` exists, load and rebuild index.
- Create `/app/data` directory if not exists.
- No persistence required for search cache, just docs.
- Ensure persistence is atomic (write temp then rename).

### 5. Non-functional

- Thread-safe for concurrent requests.
- Must handle up to 10k docs, 1000 requests in tests without OOM.
- No external dependencies beyond Go stdlib highly recommended, but you may use go.mod with pure-Go deps. Do not require internet at runtime (deps fetched at build).
- Binary must build with `go build -o /tmp/search-server .` from `/app`.

### 6. Files to Create

`/app/main.go` (and additional Go files as needed) :

- Must compile and run.
- Suggest structure:
  - `engine.go`: inverted index logic, thread safety, persistence
  - `parser.go`: boolean query parser (shunting-yard or recursive descent)
  - `main.go`: HTTP server, handlers

But any structure inside `/app` is ok as long as `go run .` starts server.

### 7. Example Workflow

```
POST /documents {"id":"1","title":"Go Search","body":"build a search engine in Go","tags":["go","search"]}
POST /documents {"id":"2","title":"Java Search","body":"Lucene is Java search library","tags":["java","search"]}
GET /search?q=search  -> both docs, sorted by tfidf
GET /search?q=go AND search -> only doc 1
GET /search?q=search&tags=go -> only doc1
GET /search?q=NOT java -> only doc1
POST /search {"query":"go OR java","operator":"OR"} -> both
DELETE /documents/1
GET /search?q=search -> only doc2
# after restart, doc2 still exists due to persistence
```

### 8. Failure Modes to Handle

- 400 on invalid JSON for POST /documents and POST /search (if body invalid)
- 400 if limit <0 or not number, offset <0, etc. Return 400 with error JSON.
- GET /documents/nope -> 404
- Empty index: search returns total 0, empty results
- Operator param case-insensitive.

### 9. What You Should NOT Do

- Do NOT use an external Elasticsearch / Bleve / etc. library that provides search out-of-the-box. Implement inverted index yourself.
- Do NOT hardcode results.

### 10. Success Criteria

Tests will:

1. Build your Go project
2. Start server on random port (via PORT env)
3. Index documents, retrieve, delete
4. Search with simple terms, AND/OR operator, boolean parsing with parentheses and NOT
5. Check scoring (docs with higher term frequency rank higher)
6. Check tag filtering (AND semantics)
7. Check pagination
8. Check persistence (restart server must retain docs)
9. Check concurrency (parallel indexing/search)
10. Check edge cases (empty index, invalid inputs)

All must pass for reward=1.

Implement the spec precisely — tests are strict about status codes and JSON shapes.
