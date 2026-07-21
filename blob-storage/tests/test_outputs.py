"""
Comprehensive tests for S3-like blob storage system.
Tests interact with the Go server via HTTP on localhost:8080
"""

import hashlib
import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
import requests

BASE_URL = "http://localhost:8080"
STORAGE_PATH = os.environ.get("STORAGE_PATH", "/tmp/blob-data")


def clear_storage():
    """Helper to clear via API - delete all buckets forcibly"""
    try:
        resp = requests.get(f"{BASE_URL}/buckets", timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            for b in data.get("buckets", []):
                name = b["name"]
                # list and delete objects
                try:
                    r = requests.get(f"{BASE_URL}/buckets/{name}/objects", timeout=5)
                    if r.status_code == 200:
                        objs = r.json().get("objects", [])
                        for obj in objs:
                            requests.delete(
                                f"{BASE_URL}/buckets/{name}/objects/{obj['key']}",
                                timeout=5,
                            )
                except Exception:
                    pass
                requests.delete(f"{BASE_URL}/buckets/{name}", timeout=5)
    except Exception as e:
        print(f"clear_storage warning: {e}")


@pytest.fixture(autouse=True)
def clean_between_tests():
    clear_storage()
    yield
    clear_storage()


def test_bucket_create_and_list():
    """Test bucket creation and listing"""
    # Initially empty
    resp = requests.get(f"{BASE_URL}/buckets", timeout=5)
    assert resp.status_code == 200
    assert resp.json()["buckets"] == []

    # Create bucket
    resp = requests.put(f"{BASE_URL}/buckets/testbucket1", timeout=5)
    assert resp.status_code in (200, 201)
    data = resp.json()
    assert data["name"] == "testbucket1"
    assert "createdAt" in data

    # List should contain it
    resp = requests.get(f"{BASE_URL}/buckets", timeout=5)
    assert resp.status_code == 200
    buckets = resp.json()["buckets"]
    assert len(buckets) == 1
    assert buckets[0]["name"] == "testbucket1"

    # Create second bucket
    resp = requests.put(f"{BASE_URL}/buckets/testbucket2", timeout=5)
    assert resp.status_code in (200, 201)

    resp = requests.get(f"{BASE_URL}/buckets", timeout=5)
    assert len(resp.json()["buckets"]) == 2


def test_bucket_create_idempotent():
    """Creating existing bucket should be idempotent 200 or 201"""
    resp = requests.put(f"{BASE_URL}/buckets/idempotent", timeout=5)
    assert resp.status_code in (200, 201)
    first_created = resp.json().get("createdAt")

    time.sleep(0.1)

    resp = requests.put(f"{BASE_URL}/buckets/idempotent", timeout=5)
    assert resp.status_code in (200, 201)
    assert resp.json()["name"] == "idempotent"
    # Should still exist only once
    resp = requests.get(f"{BASE_URL}/buckets", timeout=5)
    assert len([b for b in resp.json()["buckets"] if b["name"] == "idempotent"]) == 1


def test_bucket_invalid_names():
    """Test bucket name validation"""
    invalid_names = [
        "ab",  # too short
        "A",  # uppercase
        "MyBucket",  # uppercase
        "a" * 64,  # too long
        "-mybucket",  # starts with hyphen
        "mybucket-",  # ends with hyphen
        "my bucket",  # space
        "my_bucket",  # underscore
        "",  # empty - will be caught by routing but test
    ]
    for name in invalid_names:
        if not name:
            # Empty bucket name would be /buckets/ which is not matched as bucket create
            # PUT /buckets/ should 404 or 405
            resp = requests.put(f"{BASE_URL}/buckets/", timeout=5)
            # Accept 404, 405, 400
            assert resp.status_code in (400, 404, 405)
        else:
            resp = requests.put(f"{BASE_URL}/buckets/{name}", timeout=5)
            assert resp.status_code == 400, (
                f"Expected 400 for invalid bucket name '{name}', got {resp.status_code}"
            )
            body = resp.json()
            assert "error" in body
            assert "code" in body


def test_bucket_delete():
    """Test bucket deletion and 404, 409 cases"""
    # Delete non-existent bucket -> 404
    resp = requests.delete(f"{BASE_URL}/buckets/nonexistent", timeout=5)
    assert resp.status_code == 404

    # Create then delete empty bucket -> 204
    requests.put(f"{BASE_URL}/buckets/emptybucket", timeout=5)
    resp = requests.delete(f"{BASE_URL}/buckets/emptybucket", timeout=5)
    assert resp.status_code == 204

    # Verify gone
    resp = requests.get(f"{BASE_URL}/buckets", timeout=5)
    assert all(b["name"] != "emptybucket" for b in resp.json()["buckets"])


def test_bucket_delete_not_empty():
    """Deleting non-empty bucket should return 409"""
    requests.put(f"{BASE_URL}/buckets/notempty", timeout=5)
    requests.put(
        f"{BASE_URL}/buckets/notempty/objects/file.txt", data=b"content", timeout=5
    )

    resp = requests.delete(f"{BASE_URL}/buckets/notempty", timeout=5)
    assert resp.status_code == 409

    # After deleting objects, should succeed
    resp = requests.delete(f"{BASE_URL}/buckets/notempty/objects/file.txt", timeout=5)
    assert resp.status_code == 204
    resp = requests.delete(f"{BASE_URL}/buckets/notempty", timeout=5)
    assert resp.status_code == 204


def test_object_put_get_delete():
    """Basic object put, get, delete lifecycle"""
    requests.put(f"{BASE_URL}/buckets/mybucket", timeout=5)

    content = b"Hello World"
    resp = requests.put(
        f"{BASE_URL}/buckets/mybucket/objects/hello.txt", data=content, timeout=5
    )
    assert resp.status_code in (200, 201)
    body = resp.json()
    assert "etag" in body
    assert "size" in body
    assert body["size"] == len(content)
    # ETag should be MD5
    expected_etag = hashlib.md5(content).hexdigest()
    assert body["etag"] == expected_etag

    # Get object
    resp = requests.get(f"{BASE_URL}/buckets/mybucket/objects/hello.txt", timeout=5)
    assert resp.status_code == 200
    assert resp.content == content
    # ETag must be unquoted hex per task spec (preferred)
    assert resp.headers.get("ETag") == expected_etag
    assert "Content-Length" in resp.headers
    assert int(resp.headers["Content-Length"]) == len(content)

    # Delete
    resp = requests.delete(f"{BASE_URL}/buckets/mybucket/objects/hello.txt", timeout=5)
    assert resp.status_code == 204

    # Get after delete -> 404
    resp = requests.get(f"{BASE_URL}/buckets/mybucket/objects/hello.txt", timeout=5)
    assert resp.status_code == 404


def test_object_get_not_found():
    """Get non-existent object or bucket returns 404"""
    requests.put(f"{BASE_URL}/buckets/b1valid", timeout=5)

    resp = requests.get(f"{BASE_URL}/buckets/b1valid/objects/nope.txt", timeout=5)
    assert resp.status_code == 404

    resp = requests.get(f"{BASE_URL}/buckets/nobucket/objects/file.txt", timeout=5)
    assert resp.status_code == 404


def test_object_overwrite():
    """Putting same key twice should overwrite (last write wins)"""
    requests.put(f"{BASE_URL}/buckets/overwrite", timeout=5)

    requests.put(
        f"{BASE_URL}/buckets/overwrite/objects/file.txt", data=b"first", timeout=5
    )
    resp = requests.get(f"{BASE_URL}/buckets/overwrite/objects/file.txt", timeout=5)
    assert resp.content == b"first"

    requests.put(
        f"{BASE_URL}/buckets/overwrite/objects/file.txt",
        data=b"second version longer",
        timeout=5,
    )
    resp = requests.get(f"{BASE_URL}/buckets/overwrite/objects/file.txt", timeout=5)
    assert resp.content == b"second version longer"
    assert resp.headers.get("ETag") == hashlib.md5(b"second version longer").hexdigest()


def test_object_metadata():
    """Test Content-Type and custom X-Amz-Meta- preservation"""
    requests.put(f"{BASE_URL}/buckets/metabucket", timeout=5)

    headers = {
        "Content-Type": "text/plain",
        "X-Amz-Meta-author": "alice",
        "X-Amz-Meta-project": "test",
    }
    resp = requests.put(
        f"{BASE_URL}/buckets/metabucket/objects/meta.txt",
        data=b"metadata test",
        headers=headers,
        timeout=5,
    )
    assert resp.status_code in (200, 201)

    # GET should return preserved headers
    resp = requests.get(f"{BASE_URL}/buckets/metabucket/objects/meta.txt", timeout=5)
    assert resp.status_code == 200
    # Content-Type may be exactly what we sent or with charset - check contains
    ct = resp.headers.get("Content-Type", "")
    assert "text/plain" in ct
    # Custom metadata headers should be present (case-insensitive check)
    # Server should return X-Amz-Meta-* headers
    lower_headers = {k.lower(): v for k, v in resp.headers.items()}
    assert lower_headers.get("x-amz-meta-author") == "alice"
    assert lower_headers.get("x-amz-meta-project") == "test"


def test_object_head():
    """Test HEAD returns same headers as GET but no body"""
    requests.put(f"{BASE_URL}/buckets/headbucket", timeout=5)
    content = b"head test content"
    requests.put(
        f"{BASE_URL}/buckets/headbucket/objects/head.txt", data=content, timeout=5
    )

    resp_get = requests.get(
        f"{BASE_URL}/buckets/headbucket/objects/head.txt", timeout=5
    )
    resp_head = requests.head(
        f"{BASE_URL}/buckets/headbucket/objects/head.txt", timeout=5
    )

    assert resp_head.status_code == 200
    assert resp_head.content == b""  # HEAD should have no body
    assert resp_head.headers.get("ETag") == resp_get.headers.get("ETag")
    assert resp_head.headers.get("Content-Type") == resp_get.headers.get("Content-Type")
    assert resp_head.headers.get("Content-Length") == resp_get.headers.get(
        "Content-Length"
    )

    # HEAD non-existent -> 404
    resp = requests.head(
        f"{BASE_URL}/buckets/headbucket/objects/nonexistent.txt", timeout=5
    )
    assert resp.status_code == 404


def test_object_list():
    """Test listing objects"""
    requests.put(f"{BASE_URL}/buckets/listbucket", timeout=5)

    # Empty list
    resp = requests.get(f"{BASE_URL}/buckets/listbucket/objects", timeout=5)
    assert resp.status_code == 200
    data = resp.json()
    assert data["objects"] == []
    assert data["count"] == 0

    # Put several objects
    keys = ["a.txt", "b.txt", "folder/c.txt", "folder/d.txt", "z.txt"]
    for k in keys:
        requests.put(
            f"{BASE_URL}/buckets/listbucket/objects/{k}",
            data=f"content of {k}".encode(),
            timeout=5,
        )

    resp = requests.get(f"{BASE_URL}/buckets/listbucket/objects", timeout=5)
    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] == len(keys)
    returned_keys = [obj["key"] for obj in data["objects"]]
    # Should be sorted
    assert returned_keys == sorted(returned_keys)
    assert set(returned_keys) == set(keys)

    # Check object structure
    for obj in data["objects"]:
        assert "key" in obj
        assert "size" in obj
        assert "etag" in obj
        assert "lastModified" in obj
        assert "contentType" in obj


def test_object_list_prefix():
    """Test listing with prefix filter"""
    requests.put(f"{BASE_URL}/buckets/prefixbucket", timeout=5)

    keys = ["a.txt", "a/b.txt", "a/c.txt", "b.txt", "b/c.txt", "folder/file.txt"]
    for k in keys:
        requests.put(
            f"{BASE_URL}/buckets/prefixbucket/objects/{k}", data=b"x", timeout=5
        )

    # Prefix "a"
    resp = requests.get(f"{BASE_URL}/buckets/prefixbucket/objects?prefix=a", timeout=5)
    assert resp.status_code == 200
    returned_keys = [obj["key"] for obj in resp.json()["objects"]]
    assert all(k.startswith("a") for k in returned_keys)
    assert set(returned_keys) == {"a.txt", "a/b.txt", "a/c.txt"}

    # Prefix "folder/"
    resp = requests.get(
        f"{BASE_URL}/buckets/prefixbucket/objects?prefix=folder/", timeout=5
    )
    returned_keys = [obj["key"] for obj in resp.json()["objects"]]
    assert returned_keys == ["folder/file.txt"]

    # Prefix that matches nothing
    resp = requests.get(
        f"{BASE_URL}/buckets/prefixbucket/objects?prefix=nomatch", timeout=5
    )
    assert resp.json()["objects"] == []
    assert resp.json()["count"] == 0

    # Prefix "b"
    resp = requests.get(f"{BASE_URL}/buckets/prefixbucket/objects?prefix=b", timeout=5)
    returned_keys = set(obj["key"] for obj in resp.json()["objects"])
    assert returned_keys == {"b.txt", "b/c.txt"}


def test_object_hierarchical_keys():
    """Test that object keys with slashes work as hierarchical paths"""
    requests.put(f"{BASE_URL}/buckets/hierbucket", timeout=5)

    key = "path/to/deeply/nested/file.txt"
    content = b"nested content"
    resp = requests.put(
        f"{BASE_URL}/buckets/hierbucket/objects/{key}", data=content, timeout=5
    )
    assert resp.status_code in (200, 201)

    resp = requests.get(f"{BASE_URL}/buckets/hierbucket/objects/{key}", timeout=5)
    assert resp.status_code == 200
    assert resp.content == content

    # List with prefix "path/to/"
    resp = requests.get(
        f"{BASE_URL}/buckets/hierbucket/objects?prefix=path/to/", timeout=5
    )
    assert resp.status_code == 200
    assert len(resp.json()["objects"]) == 1
    assert resp.json()["objects"][0]["key"] == key

    # Also test another nested file under same prefix
    requests.put(
        f"{BASE_URL}/buckets/hierbucket/objects/path/to/other.txt",
        data=b"other",
        timeout=5,
    )
    resp = requests.get(
        f"{BASE_URL}/buckets/hierbucket/objects?prefix=path/to/", timeout=5
    )
    assert resp.json()["count"] == 2


def test_object_etag_md5():
    """ETag must be MD5 hex of content"""
    requests.put(f"{BASE_URL}/buckets/etagbucket", timeout=5)

    test_cases = [
        b"",
        b"hello",
        b"\x00\x01\x02\xff\xfe",
        b"A" * 1024,
    ]
    for idx, content in enumerate(test_cases):
        key = f"file{idx}.bin"
        expected_md5 = hashlib.md5(content).hexdigest()
        resp = requests.put(
            f"{BASE_URL}/buckets/etagbucket/objects/{key}", data=content, timeout=5
        )
        assert resp.json()["etag"] == expected_md5

        resp = requests.get(f"{BASE_URL}/buckets/etagbucket/objects/{key}", timeout=5)
        assert resp.headers.get("ETag") == expected_md5
        assert resp.content == content


def test_empty_object():
    """Test handling of empty (0-byte) objects"""
    requests.put(f"{BASE_URL}/buckets/emptyobj", timeout=5)

    resp = requests.put(
        f"{BASE_URL}/buckets/emptyobj/objects/empty.txt", data=b"", timeout=5
    )
    assert resp.status_code in (200, 201)
    assert resp.json()["size"] == 0
    assert resp.json()["etag"] == hashlib.md5(b"").hexdigest()

    resp = requests.get(f"{BASE_URL}/buckets/emptyobj/objects/empty.txt", timeout=5)
    assert resp.status_code == 200
    assert resp.content == b""
    assert resp.headers.get("Content-Length") == "0"

    resp = requests.head(f"{BASE_URL}/buckets/emptyobj/objects/empty.txt", timeout=5)
    assert resp.status_code == 200
    assert resp.headers.get("Content-Length") == "0"


def test_large_object():
    """Test larger object (1MB)"""
    requests.put(f"{BASE_URL}/buckets/largebucket", timeout=5)

    content = os.urandom(1024 * 1024)  # 1MB random
    md5 = hashlib.md5(content).hexdigest()

    resp = requests.put(
        f"{BASE_URL}/buckets/largebucket/objects/large.bin", data=content, timeout=5
    )
    assert resp.status_code in (200, 201)
    assert resp.json()["etag"] == md5

    resp = requests.get(f"{BASE_URL}/buckets/largebucket/objects/large.bin", timeout=5)
    assert resp.status_code == 200
    assert len(resp.content) == len(content)
    assert hashlib.md5(resp.content).hexdigest() == md5


def test_binary_data():
    """Test binary data preservation"""
    requests.put(f"{BASE_URL}/buckets/binarybucket", timeout=5)

    # All byte values 0-255
    content = bytes(range(256)) * 4
    resp = requests.put(
        f"{BASE_URL}/buckets/binarybucket/objects/binary.dat", data=content, timeout=5
    )
    assert resp.status_code in (200, 201)

    resp = requests.get(
        f"{BASE_URL}/buckets/binarybucket/objects/binary.dat", timeout=5
    )
    assert resp.content == content


def test_concurrent_puts():
    """Test concurrent uploads to same bucket (different keys)"""
    requests.put(f"{BASE_URL}/buckets/concurrent", timeout=5)

    def put_object(i):
        key = f"concurrent/file{i}.txt"
        content = f"content {i}".encode()
        resp = requests.put(
            f"{BASE_URL}/buckets/concurrent/objects/{key}", data=content, timeout=5
        )
        return resp.status_code in (200, 201)

    with ThreadPoolExecutor(max_workers=10) as executor:
        results = list(executor.map(put_object, range(20)))

    assert all(results)

    resp = requests.get(f"{BASE_URL}/buckets/concurrent/objects", timeout=5)
    assert resp.json()["count"] == 20


def test_concurrent_same_key():
    """Concurrent puts to same key should not corrupt (last write wins)"""
    requests.put(f"{BASE_URL}/buckets/concurrent-same", timeout=5)

    def put_version(v):
        content = f"version {v}".encode()
        requests.put(
            f"{BASE_URL}/buckets/concurrent-same/objects/same.txt",
            data=content,
            timeout=5,
        )
        return content

    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(put_version, i) for i in range(10)]
        contents = [f.result() for f in futures]

    # Final object should be one of the versions, and readable
    resp = requests.get(
        f"{BASE_URL}/buckets/concurrent-same/objects/same.txt", timeout=5
    )
    assert resp.status_code == 200
    assert resp.content in contents
    # ETag should match content - must be unquoted hex per spec
    assert resp.headers.get("ETag") == hashlib.md5(resp.content).hexdigest()


def test_object_invalid_keys():
    """Test invalid object key handling - path traversal must be blocked"""
    requests.put(f"{BASE_URL}/buckets/validbucket", timeout=5)

    # These keys attempt directory traversal and must be rejected or sanitized
    # to not escape the bucket directory. For strict implementations, 400.
    # For implementations that normalize paths, we ensure no escape occurs.
    strict_invalid = [
        "../escape.txt",
        "a/../../b.txt",
    ]
    for key in strict_invalid:
        resp = requests.put(
            f"{BASE_URL}/buckets/validbucket/objects/{key}", data=b"x", timeout=5
        )
        # Must be blocked - 400 for invalid key or 404 if path cleaned to non-existent bucket/object
        # Both 400 and 404 indicate the traversal was not allowed to escape
        assert resp.status_code in (400, 404), (
            f"Expected 400 or 404 for key {key}, got {resp.status_code}"
        )

    # This key contains ".." but normalizes within bucket (a/../b.txt -> b.txt)
    # HTTP clients and Go's ServeMux may normalize it before reaching handler.
    # So we accept either 400 (strict) or 200 with safe normalization.
    key = "a/../b.txt"
    resp = requests.put(
        f"{BASE_URL}/buckets/validbucket/objects/{key}", data=b"x", timeout=5
    )
    assert resp.status_code in (200, 201, 400), (
        f"Expected 200/201 or 400 for normalized key {key}, got {resp.status_code}"
    )
    if resp.status_code in (200, 201):
        # If accepted, ensure it was stored as normalized key inside bucket, not outside
        # Check listing - should have at most one file, and key should not contain ".."
        r = requests.get(f"{BASE_URL}/buckets/validbucket/objects", timeout=5)
        assert r.status_code == 200
        objs = r.json().get("objects", []) or []
        for obj in objs:
            assert ".." not in obj["key"], "Stored key must not contain .."
        # Also ensure no file escaped to parent bucket dir outside storage (checked via API)
        # The bucket should still exist and be listable
        r = requests.get(f"{BASE_URL}/buckets", timeout=5)
        assert r.status_code == 200


def test_bucket_sorted_listing():
    """Buckets should be sorted (optional but good) or at least contain all"""
    names = ["zebra", "apple", "middle", "bucket-123", "test-bucket"]
    for name in names:
        requests.put(f"{BASE_URL}/buckets/{name}", timeout=5)

    resp = requests.get(f"{BASE_URL}/buckets", timeout=5)
    assert resp.status_code == 200
    returned = [b["name"] for b in resp.json()["buckets"]]
    assert set(returned) == set(names)
    # Check sorted if implementation does sorting
    assert returned == sorted(returned)


def test_object_content_type_default():
    """If no Content-Type provided, default to application/octet-stream"""
    requests.put(f"{BASE_URL}/buckets/ctbucket", timeout=5)

    resp = requests.put(
        f"{BASE_URL}/buckets/ctbucket/objects/noct.txt", data=b"data", timeout=5
    )
    assert resp.status_code in (200, 201)

    resp = requests.get(f"{BASE_URL}/buckets/ctbucket/objects/noct.txt", timeout=5)
    ct = resp.headers.get("Content-Type", "")
    assert "application/octet-stream" in ct


def test_persistence_filesystem():
    """Verify files are actually persisted on filesystem (if STORAGE_PATH accessible)"""
    requests.put(f"{BASE_URL}/buckets/persistbucket", timeout=5)
    requests.put(
        f"{BASE_URL}/buckets/persistbucket/objects/persist.txt",
        data=b"persist me",
        timeout=5,
    )

    # If we can access storage path
    if os.path.exists(STORAGE_PATH):
        bucket_path = os.path.join(STORAGE_PATH, "persistbucket")
        assert os.path.isdir(bucket_path)
        object_path = os.path.join(bucket_path, "persist.txt")
        assert os.path.isfile(object_path)
        with open(object_path, "rb") as f:
            assert f.read() == b"persist me"
        meta_path = object_path + ".meta.json"
        assert os.path.isfile(meta_path)
        with open(meta_path) as f:
            meta = json.load(f)
            assert "etag" in meta
            assert "size" in meta


def test_delete_object_idempotency():
    """Delete non-existent object should be 404 (not 204)"""
    requests.put(f"{BASE_URL}/buckets/delbucket", timeout=5)
    resp = requests.delete(
        f"{BASE_URL}/buckets/delbucket/objects/nonexistent.txt", timeout=5
    )
    assert resp.status_code == 404

    # Delete existing then again
    requests.put(
        f"{BASE_URL}/buckets/delbucket/objects/exist.txt", data=b"x", timeout=5
    )
    resp = requests.delete(f"{BASE_URL}/buckets/delbucket/objects/exist.txt", timeout=5)
    assert resp.status_code == 204
    resp = requests.delete(f"{BASE_URL}/buckets/delbucket/objects/exist.txt", timeout=5)
    assert resp.status_code == 404


def test_limit_query_param():
    """Test limit query param - if implemented should truncate, else return all"""
    requests.put(f"{BASE_URL}/buckets/limitbucket", timeout=5)
    for i in range(5):
        requests.put(
            f"{BASE_URL}/buckets/limitbucket/objects/file{i}.txt", data=b"x", timeout=5
        )

    # Without limit should return all 5
    resp = requests.get(f"{BASE_URL}/buckets/limitbucket/objects", timeout=5)
    assert resp.status_code == 200
    assert resp.json()["count"] == 5
    assert len(resp.json().get("objects", [])) == 5

    # With limit=2: must truncate to at most 2 after sorting
    resp = requests.get(f"{BASE_URL}/buckets/limitbucket/objects?limit=2", timeout=5)
    assert resp.status_code == 200
    count = resp.json()["count"]
    assert count == 2, f"limit=2 should yield count 2, got {count}"
    objs = resp.json().get("objects", [])
    assert len(objs) == 2
    # Ensure sorted order still holds and they are first 2 sorted keys
    returned_keys = [o["key"] for o in objs]
    assert returned_keys == sorted(returned_keys)
    assert returned_keys == sorted([f"file{i}.txt" for i in range(5)])[:2]


def test_checksum_sha256():
    """Test novel SHA256 checksum verification via X-Content-SHA256"""
    requests.put(f"{BASE_URL}/buckets/checksumbucket", timeout=5)

    content = b"checksum test content"
    sha256_hex = hashlib.sha256(content).hexdigest()

    # Valid checksum should succeed
    headers = {"X-Content-SHA256": sha256_hex}
    resp = requests.put(
        f"{BASE_URL}/buckets/checksumbucket/objects/valid.txt",
        data=content,
        headers=headers,
        timeout=5,
    )
    assert resp.status_code in (200, 201)
    assert resp.json()["etag"] == hashlib.md5(content).hexdigest()

    # Invalid checksum should be rejected with 400 BadDigest
    bad_headers = {"X-Content-SHA256": "0" * 64}
    resp = requests.put(
        f"{BASE_URL}/buckets/checksumbucket/objects/invalid.txt",
        data=content,
        headers=bad_headers,
        timeout=5,
    )
    assert resp.status_code == 400
    body = resp.json()
    assert body["code"] == "BadDigest"

    # Ensure invalid object was NOT stored
    resp = requests.get(
        f"{BASE_URL}/buckets/checksumbucket/objects/invalid.txt", timeout=5
    )
    assert resp.status_code == 404

    # Without checksum header should still work
    resp = requests.put(
        f"{BASE_URL}/buckets/checksumbucket/objects/nochecksum.txt",
        data=content,
        timeout=5,
    )
    assert resp.status_code in (200, 201)


def test_expiration_ttl():
    """Test novel TTL expiration via X-Expire-After and 410 Gone"""
    requests.put(f"{BASE_URL}/buckets/expirebucket", timeout=5)

    # Put object with 2-second TTL
    headers = {"X-Expire-After": "2"}
    resp = requests.put(
        f"{BASE_URL}/buckets/expirebucket/objects/temp.txt",
        data=b"temporary",
        headers=headers,
        timeout=5,
    )
    assert resp.status_code in (200, 201)

    # Immediately GET should succeed
    resp = requests.get(f"{BASE_URL}/buckets/expirebucket/objects/temp.txt", timeout=5)
    assert resp.status_code == 200
    assert resp.content == b"temporary"

    # Wait for expiration (2s + buffer)
    time.sleep(3)

    # GET after expiry should be 410 Gone (or 404 if reaper deleted)
    resp = requests.get(f"{BASE_URL}/buckets/expirebucket/objects/temp.txt", timeout=5)
    assert resp.status_code in (410, 404), (
        f"Expected 410 Gone or 404 after expiry, got {resp.status_code}"
    )
    if resp.status_code == 410:
        body = resp.json()
        assert body["code"] == "ExpiredObject"

    # HEAD after expiry also 410 or 404
    resp = requests.head(f"{BASE_URL}/buckets/expirebucket/objects/temp.txt", timeout=5)
    assert resp.status_code in (410, 404)

    # LIST should exclude expired object
    resp = requests.get(f"{BASE_URL}/buckets/expirebucket/objects", timeout=5)
    assert resp.status_code == 200
    keys = [o["key"] for o in resp.json().get("objects", [])]
    assert "temp.txt" not in keys

    # Invalid expire header should be 400
    resp = requests.put(
        f"{BASE_URL}/buckets/expirebucket/objects/badexpire.txt",
        data=b"x",
        headers={"X-Expire-After": "invalid"},
        timeout=5,
    )
    assert resp.status_code == 400
    assert resp.json()["code"] == "InvalidArgument"


def test_copy_operation():
    """Test novel copy operation POST .../objects/{key}/copy"""
    requests.put(f"{BASE_URL}/buckets/srcbucket", timeout=5)
    requests.put(f"{BASE_URL}/buckets/destbucket", timeout=5)

    content = b"copy me"
    resp = requests.put(
        f"{BASE_URL}/buckets/srcbucket/objects/original.txt", data=content, timeout=5
    )
    assert resp.status_code in (200, 201)

    # Copy to dest bucket
    copy_body = {"destBucket": "destbucket", "destKey": "copied.txt"}
    resp = requests.post(
        f"{BASE_URL}/buckets/srcbucket/objects/original.txt/copy",
        json=copy_body,
        timeout=5,
    )
    assert resp.status_code in (200, 201), (
        f"Copy failed: {resp.status_code} {resp.text}"
    )
    assert "etag" in resp.json()

    # Verify dest exists with same content
    resp = requests.get(f"{BASE_URL}/buckets/destbucket/objects/copied.txt", timeout=5)
    assert resp.status_code == 200
    assert resp.content == content

    # Original should still exist
    resp = requests.get(f"{BASE_URL}/buckets/srcbucket/objects/original.txt", timeout=5)
    assert resp.status_code == 200
    assert resp.content == content

    # Copy non-existent src should be 404
    resp = requests.post(
        f"{BASE_URL}/buckets/srcbucket/objects/nonexistent.txt/copy",
        json=copy_body,
        timeout=5,
    )
    assert resp.status_code == 404

    # Copy to non-existent dest bucket should be 404
    resp = requests.post(
        f"{BASE_URL}/buckets/srcbucket/objects/original.txt/copy",
        json={"destBucket": "nobucket", "destKey": "x.txt"},
        timeout=5,
    )
    assert resp.status_code == 404

    # Copy with missing fields should be 400
    resp = requests.post(
        f"{BASE_URL}/buckets/srcbucket/objects/original.txt/copy",
        json={"destBucket": "destbucket"},
        timeout=5,
    )
    assert resp.status_code == 400
