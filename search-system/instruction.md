# Multi-Tenant Code Search Engine in Go — HARD & NOVEL

Build a **novel** multi-tenant code search service in Go, not a textbook Elasticsearch clone. Server at `/app` must expose HTTP API with **code-aware positional index**, **recency-decayed BM25F with non-standard parameters**, **custom query language with NEAR/n**, **namespace isolation**, **WAL with checksums**, **top-terms aggregation**, and persistence recovery.

## Working Directory

`/app`. Entry `main.go` must support `go run .` and `go build -o /tmp/codimango/search-server .` listening on `0.0.0.0:$PORT` (default 8080, env `PORT`).

## 1. Server & Constraints

- Go stdlib only, no Bleve/elastic lib — implement index yourself. `go.mod` must not contain `bleve`, `elastic`, `elasticsearch`, `algolia`, `meilisearch`, `sonic`, or `tantivy` dependencies.
- Concurrent-safe (RWMutex). Scalability target is 10k docs and 1000 requests without OOM, but grader uses a bounded scale check (500 docs + 100 searches) to avoid wall-clock brittleness. Implementation should handle at least 1000 docs efficiently with linear scaling, no O(N^2) blowup per search, average search latency under 200ms for 500 docs.
- No crash on bad input, correct status codes: 400 for invalid inputs, 404 for missing doc, 201 for index, 200 for search/stats/bulk.

## 2. Document Model — Novel

```json
{
  "id": "doc1",
  "title": "GoSearchEngine",
  "body": "Implements SearchEngine in Go for team-a, created 1 hour ago",
  "tags": ["go","search"],
  "namespace": "team-a",
  "created_at": "2026-07-21T10:00:00Z"
}
```

- `id`: required non-empty unique, upsert.
- `title`, `body`: may be empty, code-aware tokenized.
- `tags`: array, case-insensitive filtering.
- `namespace`: optional string, default `"default"`. Used for multi-tenant isolation. Lowercase normalized for filtering, preserve original.
- `created_at`: optional RFC3339 timestamp. If present, used for recency decay in scoring. If missing or invalid, no decay (factor 1).

## 3. Code-Aware Tokenization — Novel

Standard Elasticsearch tutorials use `[^a-z0-9]+` lowercasing only. **You must also split camelCase and PascalCase** for code search:

- First split text on `[^A-Za-z0-9]+` into raw tokens preserving case.
- For each raw token, further split on camelCase boundaries:
  - Lowercase to uppercase transition: `searchEngine` → `search`, `Engine`
  - Acronym boundary: `IOError` → `IO`, `Error`; `HTTPRequest` → `HTTP`, `Request`
  - Implementation: split before uppercase when previous is lowercase, or before uppercase when previous is uppercase and next is lowercase.
- Lowercase all sub-tokens.
- Example: `GoSearchEngine` → `["go","search","engine"]` (positions 0,1,2). `searchEngine` → `["search","engine"]`. `IOError` → `["io","error"]`.
- Positions are assigned incrementally per sub-token across the whole field (title or body), starting 0. Required for phrase and NEAR.
- For default query `go`, doc with title `GoSearchEngine` must match because `go` token exists after camelCase split.

This code-aware analyzer is **not** in standard tutorials — it makes memorization harder.

## 4. Index — Positional + BM25F + Recency

Per-field (title, body) positional inverted index (black-box verified via phrase and NEAR queries, not via internal inspection — you must still implement positions):

- `term -> docID -> {tf, positions[]}` where positions are incremental after code-aware split.
- `titleDocFreq`, `bodyDocFreq`, `titleDocLengths` (token count after code-aware split), `bodyDocLengths`, totals for BM25F avg calculations.
- Store docs map with namespace and created_at for filtering and recency.
- Grader does NOT inspect internal index structure; positional correctness is enforced via phrase `"search engine"` matching only adjacent tokens and NEAR/n distance checks, and via tie-break and scoring tests.

BM25F with **non-standard parameters** (to reduce textbook memorization):

- Constants `k1=1.65`, `b=0.68` (NOT standard 1.2/0.75)
- IDF formula **non-standard**: `idf = log((N+1)/(df+0.5)) + 1` natural log with trailing +1, where `N=total docs`, `df=docs containing term in field (or union for default)`. This differs from standard `log(1+(N-df+0.5)/(df+0.5))` and includes +1.
- `fieldLen = tokens in field for doc after code-aware tokenization`, `avgFieldLen = total tokens in field across all docs / N` (minimum 1, per-field: avg title = total title tokens / N, avg body = total body tokens / N)
- `scoreField = idf * (tf*(k1+1)) / (tf + k1*(1-b + b*fieldLen/avgFieldLen))`
- For default (no field): `score = 2.0*scoreTitle + 1.0*scoreBody` — title weighted double (BM25F style, field boost). That is, title score multiplied by 2.0, body by 1.0, then sum.
- For `title:`, `body:`: only that field's BM25F contributes (title or body).
- For `tags:` and `namespace:`: fixed `1.0 * boost * recencyFactor`, no BM25.
- For phrase, prefix, fuzzy, NEAR: sum expanded term BM25F scores within matching field(s) multiplied by boost.

Recency decay — **novel**:

- If doc has `created_at`, compute `ageHours = (now - created_at).Hours()` (now = `time.Now()` UTC, if created_at in future, age=0).
- `recencyFactor = 1.0 + 0.5 * exp(-ageHours/168)`  — 168h = 1 week decay. So fresh doc (0h) factor=1.5, 1 week old factor≈1.18, old factor→1.0.
- If no `created_at`, factor=1.0.
- Final doc score = (sum BM25F scores) * recencyFactor. For empty query, score=1.0*recencyFactor.

Namespace isolation — **novel**:

- `namespace` query param is a single namespace string (e.g., `?namespace=team-a`) and header `X-Namespace` filters docs by exact namespace case-insensitive. Header takes precedence over query param; if both absent, search all namespaces.
- Also support field query `namespace:team-a` inside query language as clause with exact namespace match.
- For `tags` and `namespaces` aggregations, counts are per matched docs after namespace filtering.

## 5. HTTP API

### POST /documents
201 `{"ok":true,"id":...}` or 400.

### GET /documents/{id}
200 doc JSON including namespace, created_at, or 404.

### DELETE /documents/{id}
200 or 404.

### POST /bulk
NDJSON with action lines `{"index":{"_id":"id"}}` + doc, or per-line docs. Action `_id` overrides doc id. Returns `{"errors":bool,"items":[...]}`. Each bulk doc also writes WAL and persists.

### GET /stats
Returns precise index stats with JSON shape `{"docs":int,"terms":int,"avgdl":float,"namespaces":int}`:
- `docs`: number of documents indexed (len of docs map)
- `terms`: number of distinct terms across title and body fields after code-aware tokenization (union of title and body distinct terms)
- `avgdl`: average document length across all docs, defined as `(titleTotalTokens + bodyTotalTokens) / docs` as float. If docs=0, avgdl=0. Minimum 0. Title and body token counts use code-aware tokenization.
- `namespaces`: number of distinct namespaces across docs (lowercased? Count distinct case-insensitive, but return int)

Example: `{"docs":2,"terms":15,"avgdl":6.5,"namespaces":2}`

### GET /search and POST /search

GET params: `q`, `tags` (comma AND), `namespace` (single namespace filter), `operator` AND/OR default OR, `limit` default 10 max 100, `offset` default 0, `highlight` bool default false.

Header: `X-Namespace` overrides `namespace` query param.

POST body overrides GET params:
```json
{
  "query": "title:\"go search\"^2 AND body:engine NEAR/2 go",
  "tags": ["go"],
  "namespace": "team-a",
  "operator": "OR",
  "limit": 10,
  "offset": 0,
  "highlight": true
}
```

#### Query Language — Novel combination

Support:

- **Terms**: `go` analyzed with code-aware tokenizer.
- **Boolean**: `AND, OR, NOT` with precedence `NOT(3) > NEAR(2) = AND(2) > OR(1)`, parentheses, implicit AND before NOT and between adjacent clauses.
- **Phrase**: `"search engine"` — positional adjacent in same field (title or body) using code-aware positions. After code-aware split, `GoSearchEngine` contains `go` at pos0 and `search` at pos1, so `"go search"` matches `GoSearchEngine`.
- **NEAR/n**: `go NEAR/2 search` — terms within distance n in same field. Syntax `NEAR/<int>` where n is non-negative integer. Operator is case-insensitive, example `NEAR/2`. If used as `NEAR` without `/n`, default distance is 5. For this task, require integer n after slash when slash present. Matches if both terms exist in same field and `abs(pos1-pos2) <= n`. For default field (no field specified), match if NEAR holds in title OR body.
- **Field-specific**: `title:go`, `body:search`, `tags:go`, `namespace:team-a`, default=title OR body (title weighted 2x). Field names allowed: `title, body, tags, namespace`. Unknown field → 400.
- **Prefix**: `sea*` — term ending `*` matches indexed terms with that prefix (scan distinct terms). Field-specific: `title:sea*`.
- **Fuzzy**: `sarch~` or `sarch~2` — `~` optionally followed by max distance (e.g., `~2`). If no number after `~`, distance=1. If `~2`, distance=2. Levenshtein ≤ distance. Implement yourself. Field-specific supported.
- **Boost**: `term^2`, `"phrase"^1.5`, `field:term^2`, `go NEAR/2 search^2`. Boost float >0, default 1. Invalid boost → 400.
- **Combination**: `title:"go search"^2 AND body:engine NEAR/1 go NOT tags:java`, `(title:go OR body:go) AND tags:search`, `sarch~2 AND sea*`, `namespace:team-a AND go`, etc.

Tokenization for parser: preserve quoted phrases, parentheses, handle field:, boost ^, prefix *, fuzzy ~ and NEAR/n. Example: `title:"go search"^2 AND sea* NOT tags:java` tokens as before. `go NEAR/2 search` → term go, NEAR/2 operator, term search.

If query contains any explicit boolean operator (AND,OR,NOT,NEAR, parentheses), treat as boolean expression. If no boolean operator, combine clauses via `operator` param (AND=intersection, OR=union).

#### Scoring — Recency-decayed BM25F (Novel)

- Use non-standard k1=1.65, b=0.68, idf=log((N+1)/(df+0.5)) + 1 (natural log) with trailing +1, title weight 2.0, body 1.0.
- For each doc d, field f (title, body), term t:
  - tf = term frequency in field f for doc d
  - N = total docs
  - df = docs containing term t in field f (per-field document frequency). For default field (no field specified), scoring uses per-field df for each field separately, not union: title part uses title df, body part uses body df.
  - idf = log((N+1)/(df+0.5)) + 1 (natural log) with trailing +1
  - fieldLen = tokens in field f for doc d after code-aware tokenization
  - avgFieldLen = total tokens in field f across all docs / N (minimum 1), per-field: avgTitle = total title tokens / N, avgBody = total body tokens / N
  - scoreField = idf * (tf*(k1+1)) / (tf + k1*(1-b + b*fieldLen/avgFieldLen))
- Default field score = 2.0*scoreTitle + 1.0*scoreBody. That is, compute BM25F for title using title df and avgTitle, and for body using body df and avgBody, then combine with weights.
- Recency factor: if doc has created_at, ageHours = (now - created_at).Hours(), recency = 1 + 0.5*exp(-ageHours/168), else 1.0.
- For phrase: sum BM25F of terms in phrase * boost * recency.
- For prefix/fuzzy/NEAR: sum expanded/matching terms BM25F * boost * recency.
- For tags, namespace: fixed 1.0 * boost * recency (if matches).
- Empty query: score=1.0*recency.

Sort score desc, id asc.

#### Highlight

If highlight=true, each result includes `highlight` map with `<em>` wrapping. Token-based as in previous hard spec, using code-aware tokens and matched expanded terms. Must contain `<em>`.

#### Aggregations

Always return aggregations for matched docs before pagination:

```json
{
  "total":2,
  "results":[...],
  "aggregations":{
    "tags":{"go":2,"search":1},
    "top_terms":[{"term":"go","count":5},{"term":"search","count":3}],
    "namespaces":{"team-a":2}
  }
}
```

- `tags`: map lowercased tag string → count int, counting matched docs containing that tag.
- `top_terms`: array of top 5 terms by frequency in matched docs' title+body after code-aware tokenization. Each entry `{"term":string,"count":int}` where count is total occurrences across matched docs. Sorted by count descending, then term ascending. If less than 5 distinct terms, return fewer. If no matches, empty array `[]`.
- `namespaces`: map lowercased namespace string → count int, counting matched docs per namespace.

#### Response Format — Precise

`GET /search` and `POST /search` always return 200 with JSON shape:

```json
{
  "total": 2,
  "results": [
    {"id":"doc1","score":1.23,"title":"GoSearchEngine","tags":["go"],"namespace":"team-a","highlight":{"title":"<em>Go</em><em>Search</em><em>Engine</em>"}},
    {"id":"doc2","score":0.89,"title":"Java Search","tags":["java"],"namespace":"team-b"}
  ],
  "aggregations": {
    "tags": {"go":1,"java":1},
    "top_terms": [{"term":"go","count":2},{"term":"search","count":2}],
    "namespaces": {"team-a":1,"team-b":1}
  }
}
```

- `total`: int, number of docs matching query before pagination.
- `results`: array, paginated. Each result object has **exact allowed keys** `id, score, title, tags, namespace` required, plus optional `highlight`. No other keys allowed. Specifically:
  - `id`: string, doc id
  - `score`: float64, BM25F with recency decay as defined, must be present and numeric (not string). For empty query, score = 1.0 * recencyFactor.
  - `title`: string, original doc title
  - `tags`: array of strings, original doc tags
  - `namespace`: string, doc namespace. If doc had no namespace on index, returned namespace must be `"default"` (default namespace rule).
  - `highlight`: optional map<string,string> present only when highlight=true and field has text match. Allowed keys inside highlight are `title` and/or `body` only. Values are strings where matched tokens (including camelCase sub-tokens) are wrapped with `<em>` and `</em>` preserving original sub-token case. Must contain literal `<em>` when present. When highlight=false or no text match, highlight must be absent (not null, not empty).

- Top-level search response must have **exact keys** `total, results, aggregations` only — no extra top-level keys.

- `aggregations`: object always present with **exact keys** `tags, top_terms, namespaces` only. If no matches, each aggregation empty: `tags:{}, top_terms:[], namespaces:{}`.
  - `tags` as defined, `top_terms` top 5 sorted count desc term asc, `namespaces` case-insensitive counts (lowercased keys).

- `GET /stats` must have **exact keys** `docs, terms, avgdl, namespaces` only, with precise definitions as in §5.
  - `terms` distinct count after code-aware tokenization (union title+body)
  - `avgdl` must be computed as `(titleTotalTokens + bodyTotalTokens)/docs` float, with code-aware token counts, 0 when docs=0
  - `docs` int, `namespaces` int distinct case-insensitive

- `POST /documents` returns `{ok:true, id:string}` with exact keys `ok,id`.
- `DELETE` returns `{ok:true}` exact.
- `POST /bulk` returns `{errors:bool, items:[{index:{_id,status}}]}` with exact top-level keys `errors,items`.

## 6. Persistence with WAL and Recovery — Novel

- On each successful POST /documents, DELETE, POST /bulk, persist docs to `/app/data/index.json` atomically (temp + rename).
- **WAL**: Also append to `/app/data/wal.log` one JSON line per operation with checksum:
  - Index: `{"op":"index","doc":{...},"checksum":"<crc32 of doc JSON>","ts":"RFC3339"}`
  - Delete: `{"op":"delete","id":"doc1","checksum":"<crc32 of id>","ts":...}`
  - Checksum is hex CRC32 (IEEE) of the doc JSON string (for index) or id string (for delete). On replay, verify checksum, skip invalid lines.
  - Create `/app/data` dir if needed.
- On startup:
  - Try load `index.json` as before with recovery for truncated array (streaming decoder).
  - If `index.json` missing or empty, or after loading, also replay `wal.log` if exists: read line by line, verify checksum, apply operations in order, rebuilding indexes. WAL replay should happen after index.json load, so WAL can contain newer ops not yet in index.json (though our save writes both, but for test we will delete index.json and keep WAL).
  - If WAL corrupted/truncated, recover up to last valid line with correct checksum, skip invalid.
  - Must not crash on corrupted index.json or WAL.
- Respect `DATA_FILE` env var for custom index path; WAL path is `filepath.Dir(DATA_FILE)/wal.log` (if custom DATA_FILE, WAL in same dir).

## 7. Example Workflows (Novel)

```
POST /documents {"id":"1","title":"GoSearchEngine","body":"fast engine","tags":["go"],"namespace":"team-a","created_at":"2026-07-21T09:00:00Z"}
POST /documents {"id":"2","title":"Java Search","body":"Lucene","tags":["java"],"namespace":"team-b","created_at":"2026-07-14T10:00:00Z"}
GET /search?q=search -> both, but doc1 matches go via camelCase GoSearchEngine
GET /search?q="search engine" -> only doc1 (phrase adjacent after code-aware)
GET /search?q=title:go -> doc1 (GoSearchEngine split)
GET /search?q=tags:go -> doc1
GET /search?q=sea* -> both (search)
GET /search?q=sarch~ -> both fuzzy
GET /search?q=go NEAR/2 engine -> doc1 (go within 2 of engine in title)
GET /search?q=go^2 search -> doc1 higher due to boost + recency (newer)
GET /search?namespace=team-a -> only team-a docs
GET /search with header X-Namespace: team-b -> only team-b
GET /search?q=go&highlight=true -> <em>go</em> etc.
GET /search -> aggregations tags, top_terms, namespaces
POST /bulk NDJSON
GET /stats -> docs, terms, avgdl, namespaces
DELETE /documents/1
# restart with WAL: doc2 persists, doc1 deleted persists via WAL replay if index.json deleted
```

## 8. Failure Handling

- 400 on invalid JSON, missing id, invalid limit/offset (<0 or not number), invalid operator (must be AND/OR), invalid field (unknown field name), invalid boost (not number or <=0), invalid NEAR (e.g., NEAR/abc), invalid fuzzy distance (e.g., ~abc), phrase empty.
- 404 for missing doc.
- Empty index: total 0, aggregations empty.
- Operator case-insensitive, field names case-insensitive.

## 9. Anti-Requirements

- No external search lib.
- No hardcoding.

## 10. Success Criteria

Tests build Go binary from `/app` (`/tmp/codimango/search-server`), start server on random $PORT, test:

- CRUD with namespace and created_at
- Code-aware tokenization (GoSearchEngine → go, search, engine)
- Simple OR/AND, boolean with NOT, parentheses, NEAR/n
- Phrase queries adjacent after code-aware split
- Field-specific title, body, tags, namespace
- Prefix, fuzzy with distance ~ and ~2
- Boost ^ and recency decay ranking (newer doc higher when same BM25)
- Namespace isolation via param and header X-Namespace
- Highlight <em> for prefix/fuzzy/phrase
- Aggregations tags, top_terms (top 5), namespaces
- Bulk API with action-id precedence and WAL
- Stats with namespaces
- Tag filter AND, pagination, empty query, persistence with WAL replay and truncated recovery (both index.json and wal.log)
- Concurrency bulk+search+delete
- Invalid inputs 400 (unknown field, invalid boost, invalid NEAR, fuzzy distance)

All must pass. Implement precisely — tests strict about BM25 non-standard parameters, recency formula, camelCase split, NEAR distance, aggregations shapes.
