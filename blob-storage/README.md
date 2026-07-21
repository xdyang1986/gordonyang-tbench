# codimango/blob-storage

## Description

This task requires implementing an S3-like blob storage system in Go with novel non-canonical extensions that break the standard S3 clone template. The agent must build a RESTful HTTP server that exposes bucket and object operations (create/list/delete buckets, put/get/head/delete/list objects), plus three novel extensions:

- **SHA256 checksum verification** via `X-Content-SHA256` header — server must compute SHA256 and reject with 400 BadDigest on mismatch (not in typical S3 tutorials which use MD5)
- **TTL expiration** via `X-Expire-After` header — objects expire after N seconds, GET/HEAD return 410 Gone with `ExpiredObject` code, LIST excludes expired, background reaper deletes expired every 1s (or lazy expiration)
- **Copy operation** via `POST /buckets/{src}/objects/{key}/copy` with JSON `{"destBucket","destKey"}` — atomic copy preserving metadata

Core requirements:
- Language Go 1.21+, working dir `/app`, `go.mod` module, binary buildable as `./blob-server` or `/app/blob-server` listening on `:8080`
- Filesystem persistence: buckets as directories, objects with hierarchical keys (`a/b/c.txt`), sidecar `.meta.json` containing contentType, size, etag (MD5 hex), lastModified, custom metadata, and expiresAt
- Validation: bucket regex `^[a-z0-9][a-z0-9-]{1,61}[a-z0-9]$`, object keys 1-1024 chars, no leading `/`, no `..` traversal, no `//`, no null bytes
- Concurrency safety via `RWMutex`, atomic writes via temp file + rename, sorted listings (required, not optional), empty lists as `[]` not `null`

Naive in-memory map without persistence, returning `null` for empty lists, or missing traversal checks will fail. The reference solution is ~750 lines using only stdlib, with reaper goroutine and SHA256 verification.

## Completion Rates

- **Oracle**: 3/3 passed (100%) — 28/28 pytest cases including 3 novel tests, reward 1 (Docker: blob-storage-test image)
- **Sonnet 4.6**: Previously 0/5, after leniency fixes for traversal (accept 400/404) and valid bucket name fix (`b1`→`b1valid`) expected 2-3/5 — typical failures: ETag quoted (`"etag"` vs `etag`), missing SHA256 checksum verification, missing expiration handling, missing copy endpoint, hierarchical MkdirAll
- **Opus (claude-opus-4-8)**: Before fix 0/5 (2 trials 24/25 only failing traversal, 3 trials 20/25 failing ETag quoted + traversal). After traversal leniency (400/404): 2 trials become 25/25, 3 trials become 21/25 (ETag) → 2/5 passes balanced. Latest run with novel extensions 031180b: 2/5 passes (40%), 3 fails — balanced, satisfies 1-4/5 requirement. GPT similarly 3-4/5 balanced.
- **Avocado (meta/avocado-5.14-code)**: Very slow (0/5 for 40+ mins provisioning, then 0/5 or 1/5), typically fails on get_not_found bucket validation and copy/expiration not implemented. Expected 1-2/5 after novel fixes.

Task calibrated to be hard but solvable: oracle 3/3, agents achieve 2-4/5 not 0/5 nor 5/5.

## Model Analysis

Dominant failure modes from Codimango trials (analysis of 15 trials for cba28e4 and 13b59c8):

1. **Path traversal (30%)** — Go's `ServeMux` cleans `..` before handler, so `../escape.txt` becomes `/buckets/validbucket/escape.txt` → bucket operation → 404 not 400. Strict test expecting 400 fails. Fix: accept 400 or 404 as blocking. Our reference checks raw `RequestURI` for `..` segments.

2. **ETag quoted (25%)** — HTTP spec allows `ETag: "abc"` but task requires unquoted hex. Many agents return quoted, causing `assert '"abc"' == 'abc'` failure. We now strip quotes in tests for leniency where appropriate, but keep JSON `etag` unquoted required.

3. **Invalid bucket name `b1` in test (20%)** — `b1` is 2 chars, invalid per 3-63 regex. PUT returns 400, so bucket doesn't exist, GET returns 400 vs expected 404 depending on validation order. Fixed to `b1valid`.

4. **Hierarchical keys (10%)** — forgetting MkdirAll for `folder/file.txt` → no such file.

5. **Novel extensions not implemented (15%)** — checksum SHA256, expiration 410, copy endpoint missing → fails new tests `test_checksum_sha256`, `test_expiration_ttl`, `test_copy_operation`. This is intentional to reduce memorization risk: standard S3 clone template doesn't include these.

These reflect real backend reasoning (HTTP cleaning semantics, JSON null vs [], filesystem security, checksum verification, TTL reaping) not setup issues.

## Anti-Cheating Analysis

- **Hardcoded outputs**: Tests use random data (`os.urandom(1MB)`), dynamic bucket names, SHA256 computed over random content, MD5 ETag verification, and expiration timing (2s TTL + 3s sleep). Hardcoded ETags/checksums would fail.

- **Overfitting to visible tests**: 28 tests cover CRUD, binary/empty/large, concurrent (20 parallel puts, same-key last-write-wins), prefix filtering, hierarchical, validation, persistence, plus novel checksum/expiration/copy. Hidden in TBR (verifier separate container, tests not in `/app`). No pre-staged answer fixtures; storage at `/tmp/blob-data` is runtime state.

- **Modifying test files**: In production, `/tests` is hidden and mounted read-only in separate verifier. `test.sh` builds Go binary from `/app` and runs live server on :8080; modifying `/app` tests doesn't affect `/tests/test_outputs.py`. Reward file from harness.

- **Bypassing intended solution path**: Requires real Go HTTP server on :8080 with filesystem persistence. Fake JSON without files fails `test_persistence_filesystem` (checks actual files under `STORAGE_PATH`), and novel tests (checksum mismatch must NOT store file, expiration must actually delete, copy must create dest file). Atomic temp+rename and reaper checks enforce real filesystem usage.
