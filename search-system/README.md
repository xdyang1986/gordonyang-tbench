# search-system — Multi-Tenant Code Search in Go (HARD & NOVEL)

## Description — Novel to reduce memorization risk

This task is **NOT** the textbook "build your own Elasticsearch" template that triggers HIGH memorization risk. It is a **novel multi-tenant code search service** with custom traits not found together in tutorials:

- **Code-aware tokenization**: `[^A-Za-z0-9]+` split + camelCase/PascalCase splitting `GoSearchEngine` → `go, search, engine` with incremental positions. Standard tutorials only do lowercasing; code-aware makes recall harder.
- **Non-standard BM25F**: `k1=1.65, b=0.68` (NOT 1.2/0.75), `idf=log((N+1)/(df+0.5))+1`, title weight 2.0 + body weight 1.0. Changing constants and formula from textbook breaks direct memorization.
- **Recency-decayed scoring**: `recencyFactor = 1 + 0.5*exp(-ageHours/168)` where `ageHours` from optional `created_at` RFC3339. Fresh docs rank higher — custom, not in ES tutorials.
- **Namespace isolation**: docs have `namespace` field (default `"default"`). Search supports `?namespace=team-a` query param AND header `X-Namespace` (header overrides), plus field query `namespace:team-a`. Multi-tenant isolation is not part of standard min-search tutorial.
- **NEAR/n operator**: `go NEAR/2 search` — terms within distance n in same field (title/body). Requires positional proximity check, not just AND. Plus standard boolean `AND/OR/NOT` with precedence `NOT(3) > NEAR(2)=AND(2) > OR(1)`, implicit AND before NOT and between adjacent clauses.
- **Phrase queries** `"search engine"` with positional adjacency after code-aware split.
- **Field-specific** `title:go`, `body:search`, `tags:go`, `namespace:team-a`, default=title OR body with title boost 2x.
- **Prefix** `sea*` scanning distinct terms, **Fuzzy** `sarch~` and `sarch~2` with Levenshtein ≤ distance (0=exact, 1=default, 2 allowed) — distance param extends textbook.
- **Boost** `term^2`, `"phrase"^1.5`.
- **Highlight** token-based `<em>term</em>` preserving original case.
- **Aggregations** `tags` counts + `top_terms` top 5 frequent terms in matched docs + `namespaces` counts — top_terms not in standard tutorial.
- **WAL with checksums**: each index/delete appends to `/app/data/wal.log` JSON line with CRC32 checksum, verified on replay. On startup, load `index.json` with truncated recovery (streaming decoder) AND replay WAL if present, deleting index.json and keeping WAL must replay. Also recovers from corrupted WAL lines.
- **Bulk** `POST /bulk` NDJSON action `{"index":{"_id":"id"}}` + doc with action-id precedence.
- **Stats** `GET /stats` `{docs,terms,avgdl,namespaces}`.

Naive approach fails because:
- No camelCase → `GoSearchEngine` not matched by `go` or `search`.
- Standard BM25 1.2/0.75 exact score mismatch (now 1.65/0.68 + title weight 2x + recency).
- Missing namespace isolation → `?namespace=team-a` returns all.
- Missing recency → newer doc not ranked first.
- No NEAR → `go NEAR/1 search` returns docs with distance 3.
- No positional → phrase `"search engine"` matches `search big engine`.
- No prefix/fuzzy distance param → `sarch~0` should be exact only, `sarch~2` should match.
- Missing top_terms, WAL replay, highlight, bulk precedence.

## Completion Rates (local, novel hard — latest run 72 tests)

- Oracle: **3/3 passed** (72/72 tests each run) — 32-39s, flakiness 3/3 (local 44s total, online oracle 3/3)
- Sonnet 4.6: **0-2/5** — fails code-aware camelCase highlight, BM25 non-standard k1=1.65 b=0.68 idf+1 with title weight 2x, namespace header override, recency exact constants 0.5/168, NEAR/1 vs NEAR/3, top_terms sorting, WAL checksum CRC32 enforcement
- Opus 4.8: **5/5** on easy, **0/5** on first hard, **5/5** on 58-test, **2/5 and 1/5** on 72-test final (balanced) — genuine reasoning: may get CRUD and simple boolean but misses camelCase sub-token highlight exact, BM25F per-field df vs union, recency exact ratio, namespace case-insensitive stats, top_terms top-5 sorted, id-asc tie break, body/default BM25F df, fixed scores, WAL delete replay, truncated recovery, invalid operator/fuzzy/NEAR 400, limit clamp 100
- Avocado (Metacode): **1/6, 2/7, 3/7** — real near-misses on positional NEAR wiring, recency factor, WAL checksum skip, code-aware highlight
- GPT-5.5 (Codex): **5/5** easy → **3/5** hard v2 → **2/5 and 1/5** final 72-test — difficulty increased via custom constants and novel composition, balanced 1-4/5

> Difficulty intentionally balanced: not 5/5 for all models (too easy), not 0/5 for all (too hard) — at least one model lands 1-4/5 (GPT 2-3/5, Avocado 1-3/5 qualifies; Opus 5/5 on some commits but 0-2/5 on others shows genuine reasoning attempts). Previous easy version was 5/5 for all → too easy, first hard 0/5 opus → too hard, novel 72-test tuned to 2-3/5 GPT and 1/6 Avocado.

## Model Analysis

- **Phrase + Code-aware 30%**: `GoSearchEngine` should match `go` and phrase `"go search"` after camelCase split into `go, search, engine` positions 0,1,2. Naive tokenizer without camelCase fails.
- **BM25 non-standard + Recency 25%**: k1=1.65,b=0.68,idf=log((N+1)/(df+0.5))+1,title*2. Students compute `score1≈3.607` for `go go go` vs `go` with avg2, not 0.258 from standard. Recency test expects newer `created_at` 1h ago ranks above 7d ago with same BM25.
- **Field + Namespace 20%**: `title:go` only title, `namespace:team-b`, `?namespace=team-a`, header `X-Namespace` override. Naive combined index fails.
- **NEAR + Prefix/Fuzzy distance 15%**: `go NEAR/1 search` must reject distance 3 (`go big big search`), prefix `sea*` must scan all terms, fuzzy `sarch~0` exact only (0 total) vs `sarch~1` matches, `sarch~2` also matches, `sxxrch~` (dist 2+ ) no match.
- **Highlight/Agg/Bulk/Stats/WAL 10%**: highlight `<em>` for prefix/fuzzy expanded terms, aggregations must include `top_terms` (top 5) and `namespaces`, bulk action `_id` precedence over doc `id`, WAL file `wal.log` with CRC32 `checksum` verified on replay, `DATA_FILE` env override, truncated recovery for both index.json and wal.log.

**Why reasoning gaps, not setup:**
- Build `go build -o /tmp/codimango/search-server .` — binary builds but logic wrong = reasoning.
- Random free ports, dynamic IDs `c{thread}_{i}`, `bulk` IDs, truncation tests — deterministic but not hardcodeable.
- BM25 exact with non-standard constants requires reading spec, not recalling standard 1.2/0.75.

## Anti-Cheating & Novelty

- **Memorization risk reduction HIGH→MEDIUM/LOW**: Changed from textbook template (positional index + BM25 1.2/0.75 + boolean + phrase + prefix + Levenshtein) to **novel multi-tenant code search** with:
  - Non-standard BM25F `k1=1.65,b=0.68` + title weight 2x + `idf=log((N+1)/(df+0.5))+1`
  - Code-aware camelCase splitting `GoSearchEngine` → `go,search,engine`
  - Recency decay `1+0.5*exp(-age/168)`
  - Namespace isolation via query param AND header `X-Namespace` AND field `namespace:`
  - NEAR/n operator `NEAR/2` positional proximity
  - Fuzzy distance param `~0,~1,~2` not just `~`
  - Top-terms aggregation `top_terms` + namespaces aggregation
  - WAL with CRC32 checksum `wal.log` replay after index.json deletion
  - These composed together are **not** in any single online tutorial — component composition with custom constants makes recall of working implementation per part insufficient; full integration is novel.

- **Hardcoded outputs**: Random ports via `find_free_port`, dynamic IDs `c{thread}_{i}`, `scale{i}`, `clamp{i}`, `perf{i}`, custom dirs `/tmp/custom_data_test`, `/tmp/wal_test`, `/tmp/wal_checksum_test`, `/tmp/wal_delete_test`, `/tmp/ns_ci_test` with random ports, recency timestamps `now-1h` vs `now-7d` computed at runtime, bulk action `_id` precedence `real_id` vs `fake_id`, exact response keys `total,results,aggregations` with allowed sets — hardcoding fails.

- **Overfitting**: Tests dir hidden in TBR prod; 72 tests include combos not in instruction examples: `title:"go search" OR (body:engine AND tags:go)`, `go NEAR/1 search` vs `NEAR/3`, `namespace:team-b` field vs `?namespace=team-a` param vs header `X-Namespace` override, `sarch~0` exact only vs `~1,~2` vs `sarch~abc` 400, `sxxrch~` distance 2+ no match, `team-b` hyphen handling, `GoSearchEngine` camelCase highlight `go` → `<em>Go</em>SearchEngine` and phrase `"go search"` → `<em>Go</em><em>Search</em>Engine` count 2, recency exact ratio with constants 0.5 and 168, WAL replay delete after index.json removal, truncated recovery preserves last valid doc, invalid operator `INVALID` 400, non-numeric limit/offset 400, empty phrase `""` 400, limit clamp 200→100, exact keys `total,results,aggregations` and result keys `id,score,title,tags,namespace` (+highlight), default namespace `default` in results, precise stats `terms=4 avgdl=3.0`, top_terms sorting count desc term asc and top-5, id-asc tie break `aaa,mmm,zzz`, body/default BM25F df per-field vs union, tag/namespace fixed scores `1.0*boost`, no external deps `bleve`, scale 500 docs + 100 searches functional (no strict timing, lenient <60s to avoid HIGH timing false-negative), POST body overrides GET params, invalid/future `created_at` handling, `DATA_FILE` env override, `X-Namespace` header override, invalid `NEAR/abc` 400, invalid boost `^abc` 400.

- **Modifying test files**: `/tests` read-only in verifier, reward written to `/logs/verifier/reward.txt` with CTRF, `set +e` around pytest ensures reward written even on failure (fixed High severity).

- **Bypassing path**: Bleve etc doesn't provide camelCase split + non-standard BM25F k1=1.65 b=0.68 idf+1 + title weight 2x + recency decay exp(-age/168) + namespace header + NEAR/n + fuzzy distance param ~0/~1/~2 + top_terms + namespaces aggregation + WAL CRC32 checksum skip + DATA_FILE override in one.

## Validation

```bash
cd /app && rm -rf data && mkdir -p /tmp/codimango && go build -o /tmp/codimango/search-server .
~/.local/bin/pytest search-system/tests/test_outputs.py -v
# expected 72 passed (~32-39s)

# after golden fixes (highlight camelCase + namespace stats case-insensitive)
# GoSearchEngine with go => <em>Go</em>SearchEngine, Team-A/team-a namespaces=1

# flakiness — oracle must be 3/3
for i in 1 2 3; do rm -rf /app/data && pytest -q; done
# 72 passed x3

# flakiness
for i in 1 2 3; do pytest -q; done

codimango bench run -p search-system -a oracle -k 3
# expected 3/3
```
