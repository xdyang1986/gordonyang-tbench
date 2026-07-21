# search-system — Elasticsearch-like Search Engine in Go (HARD)

## Description

This is a **HARD** version of the Elasticsearch-like search engine. Agent must implement in Go at `/app`:

- **Positional Inverted Index**: per-field (title, body) tokenization `[^a-z0-9]+` lowercased, storing TF AND positions for phrase queries.
- **BM25 Scoring**: `k1=1.2, b=0.75`, `idf=log(1+(N-df+0.5)/(df+0.5))`, `score=idf*(tf*(k1+1))/(tf+k1*(1-b+b*dl/avgdl))`, per-field with sum for default field. Sorting score desc, id asc.
- **Advanced Query Language**:
  - Boolean `AND/OR/NOT` with precedence `NOT>AND>OR`, parentheses, implicit `AND` before `NOT` and between adjacent clauses.
  - Phrase `"search engine"` requiring adjacent positions in same field (positional index).
  - Field-specific `title:go`, `body:search`, `tags:go`, default=title OR body.
  - Prefix `sea*` (term prefix scan), Fuzzy `sarch~` (Levenshtein ≤1).
  - Boost `term^2`, `"phrase"^1.5`, `field:term^2`.
  - Combination: `title:"go search"^2 AND body:engine* NOT tags:java`, etc.
- **Highlight**: When `highlight=true`, each result includes `highlight` map with `<em>term</em>` wrapping matched terms (preserving original case via token-wise wrap).
- **Aggregations**: Always return `aggregations.tags` counting matched docs' tags (before pagination).
- **Bulk API**: `POST /bulk` NDJSON with action lines `{"index":{"_id":"id"}}` + doc, or simplified per-line docs, returns `{"errors":bool,"items":[...]}`
- **Stats**: `GET /stats` returns `{"docs":int,"terms":int,"avgdl":float}`
- **Persistence with Recovery**: Atomic write to `/app/data/index.json`, load on startup rebuilding positional indexes; truncated file must not crash server, must recover via streaming decoder.
- **Concurrency**: RWMutex safe for 100 goroutines parallel index/search/delete/bulk.
- **HTTP API**: `POST /documents`, `GET /documents/{id}`, `DELETE /documents/{id}`, `POST /bulk`, `GET /stats`, `GET/POST /search` with `q,tags,operator,limit,offset,highlight` params, strict 400/404/201 codes.

A naive approach fails because:
- Simple contains/OR without positional index fails phrase tests (`"search engine"` vs `search big engine`).
- TF-IDF instead of BM25 fails exact BM25 scoring tests (expected values computed with formula).
- No field-aware indexes: `title:go` returns body matches => fails field-specific tests.
- No prefix/fuzzy expansion: `sea*` and `sarch~` return 0 results.
- No boost handling: `go^2 search` ranking wrong.
- Missing highlight `<em>` and aggregations `tags` counts.
- Bulk endpoint missing or NDJSON parsing wrong.
- Persistence not atomic or crashes on truncated file.
- Unknown field `unknownfield:go` should 400 but naive returns 200 empty.

## Completion Rates (local pytest — HARD)

- Oracle: **3/3 passed** (32/32 tests each run) — validated via hard `solve.sh` rebuild + pytest (15-17s)
- Sonnet 4.6: expected **0-1/5 passed** — fails phrase positional check, BM25 exact, prefix/fuzzy expansion, field-specific, highlight, bulk NDJSON, unknown field 400
- Opus 4.8 / GPT-5.5: previously 5/5 on easy version, now expected **1-3/5 passed** — may pass basic CRUD and boolean but miss: phrase adjacency, BM25 formula with avgdl, prefix scanning over all terms, Levenshtein implementation, boost multiplication, highlight wrapping, aggregations counting before pagination, bulk action parsing, truncated recovery
- Avocado: expected **1-2/5 passed** — gets lost in complex parser combining field+phrase+boost+prefix, often forgets implicit AND insertion and complement for NOT, fails recovery test due to crash on invalid JSON

> Oracle locally hard: 32 passed in ~16s, flakiness 3/3 identical. Difficulty intentionally increased to bring 5/5 down to 1-4/5 for balanced validation.

## Model Analysis

**What goes wrong (dominant failure modes):**

- `Phrase query 40% failed — returns docs where terms separated` — requires positional index and adjacency check `pos, pos+1`, simple inverted index without positions matches any doc containing both terms.
- `BM25 20% failed — score mismatch/tolerance or ranking` — models implement TF-IDF `tf*idf` instead of BM25 denominator with `k1,b,dl,avgdl`, then exact BM25 test `score1≈0.258` fails.
- `Field-specific 15% failed — title:go returns body matches` — requires separate title/body indexes; naive combined index returns union.
- `Prefix/Fuzzy 15% failed — sea* or sarch~ returns 0` — needs scanning distinct terms and Levenshtein ≤1, models skip.
- `Boost/Highlight/Agg/Bulk/Stats 10% failed — missing fields` — highlight must contain `<em>`, aggregations tags counts, bulk NDJSON action parsing, stats avgdl.

**Why reasoning gaps, not setup:**

- Build is `go build -o /tmp/search-server .` — if binary builds, logic wrong = reasoning gap.
- Server wait on `/search` endpoint — timeout only if crash, not flake.
- Phrase test uses `search engine` vs `search big engine` — deterministic positional adjacency required, not possible to guess.
- BM25 test computes expected score from formula with known N=2, df=2, avgdl=2 — exact math, not timing.

## Anti-Cheating Analysis

- **Hardcoded outputs**: Random free port per test (`find_free_port`), dynamic bulk IDs, concurrency IDs `c{thread}_{i}`, phrase test docs `p1/p2` but also random in other tests — hardcoding fails.
- **Overfitting**: Tests dir hidden in TBR prod; 32 tests include combinations not in instruction examples: `title:"go search" OR (body:engine AND tags:go)`, `unknownfield:go` 400, `go^abc` 400, truncated recovery, BM25 exact tolerance, highlight `<em>` presence, aggregations before pagination.
- **Modifying test files**: `/tests` read-only in verifier container, `test.sh` writes reward to `/logs/verifier/reward.txt` with CTRF, modification fails permission.
- **Bypassing intended path**: Using Bleve/elastic library doesn't provide positional phrase with custom BM25 per field + prefix* + fuzzy~ + boost^ + field: + highlight <em> + aggregations + bulk NDJSON + unknown field 400 + truncated recovery in one — all required, library alone fails multiple checks. Must implement inverted index yourself.

## Validation

```bash
# after solve.sh
cd /app && go build -o /tmp/search-server .
~/.local/bin/pytest search-system/tests/test_outputs.py -v
# expected 32 passed (15-17s)

# 3x flakiness
for i in 1 2 3; do pytest ... -q; done

codimango bench run -p search-system -a oracle -k 3
# expected 3/3
```
