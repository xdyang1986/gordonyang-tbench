# Blob Storage System - S3-like Implementation in Go

## Overview

Implement a blob storage service similar to Amazon S3 in Go. The service must expose a RESTful HTTP API for bucket and object management, persist data to the local filesystem, and handle concurrent access safely.

This task tests your ability to build a backend storage service from scratch using Go's standard library, with proper HTTP handling, filesystem persistence, and concurrency control.

## Requirements

### 1. Project Setup

- Language: **Go 1.21+**
- Working directory: `/app`
- You must create a `go.mod` file (e.g., `module blob-storage`)
- The server binary must be buildable via `go build -o blob-server .` or `go build -o ./blob-server .` or `go build -o /app/blob-server .` from `/app` (legacy `/tmp/blob-server` also accepted if needed)
- Server must listen on `:8080` (0.0.0.0:8080)
- Data persistence: Use directory specified by env var `STORAGE_PATH` or `DATA_DIR`, fallback to `./data` if not set. Tests will set `STORAGE_PATH=/tmp/blob-data` (or `/tmp/codimango/blob-data`).
- Use **only Go standard library** (no external dependencies) for portability.

### 2. Storage Layout

```
STORAGE_PATH/
  bucket1/
    .bucket_meta.json          # bucket metadata: {"name": "...", "createdAt": "RFC3339"}
    object1.txt
    object1.txt.meta.json      # object metadata
    folder/
      file.txt
      file.txt.meta.json
  bucket2/
    ...
```

- Buckets are top-level directories under STORAGE_PATH.
- Object keys may contain slashes (e.g., `a/b/c.txt`) representing nested paths. Create intermediate directories as needed.
- Each object has a sidecar metadata JSON file: `<object_path>.meta.json` containing:
  ```json
  {
    "contentType": "text/plain",
    "size": 123,
    "etag": "md5hex",
    "lastModified": "2024-01-01T00:00:00Z",
    "custom": {"key1": "value1"}
  }
  ```
- ETag: hex-encoded MD5 of object content (e.g., `d41d8cd98f00b204e9800998ecf8427e`).
- Bucket metadata file `.bucket_meta.json` stores `name` and `createdAt`.

### 3. API Specification

Base URL: `http://localhost:8080`

All error responses must be JSON: `{"error": "message", "code": "ERROR_CODE"}` with appropriate HTTP status.

#### Bucket Operations

**Create Bucket**
- `PUT /buckets/{bucketName}`
- Path: bucket name must be validated (see validation).
- Responses:
  - `201 Created` if newly created, body `{"name": "...", "createdAt": "..."}`
  - `200 OK` if already exists (idempotent), body same
  - `400 Bad Request` if invalid name
- Behavior: idempotent, create directory and `.bucket_meta.json` if not exists.

**Delete Bucket**
- `DELETE /buckets/{bucketName}`
- Responses:
  - `204 No Content` if deleted
  - `404 Not Found` if bucket doesn't exist
  - `409 Conflict` if bucket not empty (contains objects)
- Must ensure bucket is empty before deletion.

**List Buckets**
- `GET /buckets`
- Response `200 OK` JSON:
  ```json
  {
    "buckets": [
      {"name": "bucket1", "createdAt": "2024-01-01T00:00:00Z"},
      {"name": "bucket2", "createdAt": "2024-01-02T00:00:00Z"}
    ]
  }
  ```
- Sorted by name ascending (required, lexicographically). Empty result must be `{"buckets": []}` not `{"buckets": null}` — use empty slice `make([]T,0)` in Go to avoid null.

**Object Operations**

**Put Object (Upload)**
- `PUT /buckets/{bucketName}/objects/{objectKey}`
- objectKey may contain slashes, URL-encoded. E.g., `folder/file.txt` => `/buckets/mybucket/objects/folder/file.txt` or URL-encoded `folder%2Ffile.txt` - server must support both raw slash path and handle PathUnescape.
- Headers:
  - `Content-Type`: optional, default `application/octet-stream`
  - `X-Amz-Meta-*`: custom metadata, preserve (case-insensitive prefix, store key lowercased after prefix)
- Body: raw bytes (may be empty, may be binary).
- Responses:
  - `200 OK` or `201 Created` with `{"etag": "...", "size": 123}`
  - `404 Not Found` if bucket not exists
  - `400 Bad Request` if invalid object key
- Must:
  - Compute MD5 ETag
  - Write atomically (temp file + rename)
  - Store metadata file
  - Set response headers `ETag`, `Content-Length` not required but JSON body mandatory.

**Get Object (Download)**
- `GET /buckets/{bucketName}/objects/{objectKey}`
- Responses:
  - `200 OK` with raw body, headers:
    - `Content-Type` from stored metadata
    - `Content-Length`
    - `ETag`
    - `X-Amz-Meta-*` custom headers
  - `404 Not Found` if bucket or object not found
- Body is exact bytes uploaded.

**Head Object**
- `HEAD /buckets/{bucketName}/objects/{objectKey}`
- Same as GET but no body, only headers.
- Responses: `200 OK` with headers, `404 Not Found`.

**Delete Object**
- `DELETE /buckets/{bucketName}/objects/{objectKey}`
- Responses:
  - `204 No Content` if deleted
  - `404 Not Found` if bucket or object not found
- Must delete both data file and meta file, and prune empty parent directories inside bucket (but not bucket itself).

**List Objects**
- `GET /buckets/{bucketName}/objects?prefix=&limit=`
- Query params:
  - `prefix` (optional): filter keys starting with prefix
  - `limit` (optional): max results (if provided, truncate)
- Responses:
  - `200 OK` JSON:
    ```json
    {
      "objects": [
        {"key": "a.txt", "size": 123, "etag": "abc", "lastModified": "RFC3339", "contentType": "text/plain"},
        {"key": "b/c.txt", "size": 456, "etag": "def", "lastModified": "...", "contentType": "text/plain"}
      ],
      "prefix": "a",
      "count": 2
    }
    ```
  - Sorted by key lexicographically ascending (required). Empty result must be `{"objects": [], "prefix": "...", "count": 0}` not null — use empty slice.
  - `404 Not Found` if bucket doesn't exist.
- Must exclude `*.meta.json` and `.bucket_meta.json` from listing.

#### Optional but Recommended
- `GET /health` => `200 OK` `{"status": "ok"}` for liveness. Not required but helps testing.

### 4. Validation Rules

**Bucket Names:**
- Length 3-63 characters
- Only lowercase letters (a-z), numbers (0-9), and hyphens (-)
- Must start and end with alphanumeric (a-z, 0-9)
- Regex: `^[a-z0-9][a-z0-9-]{1,61}[a-z0-9]$` — this covers exactly 3 chars as `^[a-z0-9][a-z0-9-][a-z0-9]$` is subset. Consecutive hyphens are allowed per this regex (simple rule).
- Invalid => `400` with `{"error": "invalid bucket name", "code": "InvalidBucketName"}`
- Empty bucket list must be `[]` not `null`.

**Object Keys:**
- Length 1-1024 characters after URL decoding
- Must not be empty
- Must not start with `/`
- Must not contain `..` path traversal as segment (e.g., `a/../b` invalid). Implementations must also prevent escaping bucket root via `filepath.Rel` check — if cleaned path escapes bucket (Rel starts with `..`), reject as 400. Go's `net/http` cleans `..` before handler, so check raw `r.RequestURI` for `..` segments and reject as 400 (or 404 if cleaned path becomes bucket operation, both block escape).
- Must not contain `//` double-slash (reject as 400) and must not contain null bytes.
- Invalid => `400` `{"error": "invalid object key", "code": "InvalidObjectKey"}`. For cases where `..` causes path to be cleaned to a bucket-level path, returning 404 is also acceptable as it blocks traversal.
- ETag: header should be unquoted hex MD5, JSON `etag` field must be unquoted hex. Implementations returning quoted ETag `"abc"` should still have matching unquoted value inside quotes — tests will strip quotes leniently, but prefer unquoted.

### 5. Concurrency & Safety

- Must be safe for concurrent requests. Use `sync.RWMutex` or per-bucket locks.
- Atomic writes: write to temp file then rename.
- No data corruption on concurrent put to same key (last write wins is acceptable).

### 6. Error Handling

Use consistent JSON error format and HTTP codes:
- `400` invalid input
- `404` bucket/object not found
- `409` bucket not empty
- `405` method not allowed (optional)
- `500` internal error

Example error body:
```json
{"error": "bucket not found", "code": "NoSuchBucket"}
```

Codes to use (suggested):
- `InvalidBucketName`
- `InvalidObjectKey`
- `NoSuchBucket`
- `NoSuchKey`
- `BucketNotEmpty`
- `InternalError`

### 7. Expected Files

After completion, `/app` should contain:
- `go.mod`
- `main.go` (and any other .go files you need)
- Binary should build and run: `STORAGE_PATH=/tmp/blob-data ./blob-server` or built to `/app/blob-server` or `./blob-server`. Avoid hardcoding `/tmp/blob-server` in solution files; use `./blob-server` or `/app/blob-server` to satisfy structural checks (logs should go to `/tmp/codimango/` if using /tmp).

Your server logs to stdout/stderr is okay. Binary path `./blob-server` or `/app/blob-server` is preferred over `/tmp/blob-server`.

### 8. Constraints

- Only Go standard library.
- No external DB, only filesystem.
- Must run on `:8080`.
- Must handle binary data (not only text).
- Must handle empty objects (0 bytes) correctly.

### 9. Example Interaction

```bash
# Build and run
go build -o ./blob-server .
STORAGE_PATH=/tmp/blob-data ./blob-server &
sleep 1

# Create bucket
curl -X PUT http://localhost:8080/buckets/mybucket

# Upload object
curl -X PUT http://localhost:8080/buckets/mybucket/objects/hello.txt \
  -H "Content-Type: text/plain" \
  -H "X-Amz-Meta-author: alice" \
  --data-binary "Hello World"

# List objects
curl http://localhost:8080/buckets/mybucket/objects

# Download
curl http://localhost:8080/buckets/mybucket/objects/hello.txt

# Head
curl -I http://localhost:8080/buckets/mybucket/objects/hello.txt

# Delete
curl -X DELETE http://localhost:8080/buckets/mybucket/objects/hello.txt
curl -X DELETE http://localhost:8080/buckets/mybucket
```

### 10. Success Criteria

- Server builds without errors
- All bucket CRUD operations work with correct status codes
- All object CRUD operations work, including binary and empty objects
- Listing with prefix filter works and sorted
- Metadata preservation (Content-Type, custom X-Amz-Meta-)
- ETag MD5 correctness
- Validation of bucket names and object keys
- 409 on deleting non-empty bucket
- Persistence on filesystem with atomic writes
- Concurrency safety (tests will do parallel uploads)
- Handles hierarchical keys (slashes)

Implement the full service now. Ensure `go.mod` is initialized and server starts on :8080.
