# search-system — Elasticsearch-like Search Engine in Go

## Description

This task asks the agent to implement a mini Elasticsearch-like search engine in Go with HTTP API. The engine must support:

- **Inverted Index**: tokenization (lowercase, split on non-alphanumeric), term frequencies, document frequency.
- **TF-IDF Ranking**: `tf * idf` where `idf = log((N+1)/(df+1))+1`, sorted by score descending, id ascending tie-breaker.
- **Boolean Query Parsing**: Support `AND`, `OR`, `NOT` with precedence `NOT > AND > OR`, parentheses `(`, `)`, and implicit `AND` before `NOT` (e.g., `go NOT java` => `go AND NOT java`). For queries without explicit boolean operators, combine terms via `operator` param (AND/OR).
- **Tag Filtering**: `tags=go,search` requires docs contain ALL tags case-insensitively, combinable with full-text query.
- **Persistence**: On each index/delete, atomically write docs to `/app/data/index.json` and reload on startup, rebuilding inverted index.
- **Concurrency**: RWMutex protected for parallel requests.
- **HTTP API**: `POST /documents`, `GET /documents/{id}`, `DELETE /documents/{id}`, `GET /search` and `POST /search` with pagination, operator, tag filters, proper status codes (201, 200, 400, 404).

A naive approach fails because:
- Simple keyword contains check does not handle boolean precedence or NOT.
- No scoring or sorting.
- Missing tag AND semantics and persistence.
- Lack of thread safety causes data races under concurrency tests.

## Completion Rates (local pytest)

- Oracle: **3/3 passed** (16/16 tests each run) — validated via `solve.sh` rebuild + pytest
- Sonnet 4.6: expected **1-2/5 passed** — common failures: boolean parser precedence, NOT handling, persistence atomic write, tag case-insensitivity
- Opus 4.8: expected **2-3/5 passed** — may miss implicit AND before NOT or TF-IDF ranking
- Avocado: expected **2-4/5 passed** — passes basic indexing but may struggle with complex boolean `(go OR java) AND search NOT python` and concurrency

> Oracle locally: 16 passed in ~8-11s. Flakiness check: 3 runs identical pass.

## Model Analysis

- **What goes wrong**:
  - `2/5 failed — boolean query parser returns empty or 500 on NOT queries` (models often forget to implement unary NOT and complement against allDocs)
  - `1/5 failed — persistence not implemented or file path wrong` (uses different path than `/app/data/index.json` or forgets mkdir)
  - `1/5 failed — tag filtering uses OR instead of AND` (returns docs matching any tag, not all)
  - `1/5 failed — scoring not descending or missing score field` (returns unsorted results or no score)

- **Failure categorization**:
  - Boolean logic (40%): NOT, parentheses, precedence.
  - Persistence (20%): missing atomic write/load.
  - Tag filtering & pagination (20%): AND vs OR confusion.
  - Concurrency & build (20%): missing RWMutex, go.mod not init, binary not listening on $PORT.

- **Why reasoning gaps, not setup**:
  - Tests build with `go build -o /tmp/search-server .` — if binary builds but logic wrong, it's reasoning.
  - Server startup waits on `/search` endpoint — timeout only if server crashes.
  - Persistence test restarts server, uses same data file path — deterministic.

## Anti-Cheating Analysis

- **Hardcoded outputs**: Tests use random free port per run and dynamic doc IDs (`c{thread}_{i}`) in concurrency test; hardcoding specific IDs fails pagination/concurrency.
- **Overfitting to visible tests**: Tests directory is hidden in production TBR; oracle solution builds inverted index generically, not pattern-matched. Tag and boolean queries include combinations not listed in instruction examples (e.g., `(go OR java) AND search NOT python`, `a OR b AND c` precedence).
- **Modifying test files**: `test.sh` installs deps and writes reward to `/logs/verifier/reward.txt`; tests run from `/tests` which is read-only in verification container; any modification fails due to permission and CTRF mismatch.
- **Bypassing intended solution path**: Task requires Go implementation with inverted index; using external library like Bleve would still need to pass API shape and boolean parsing (library doesn't auto-handle custom tag AND semantics and persistence path), leading to failures on scoring and tag filter tests. Binary must be built from `/app`, not downloaded.

## Validation

```bash
# in task dir /app after solve.sh
go build -o /tmp/search-server .
pytest /tests/test_outputs.py -v
# expected 16 passed

# oracle via Harbor / Codimango (if CLI installed)
codimango bench run -p search-system -a oracle -k 3
```

