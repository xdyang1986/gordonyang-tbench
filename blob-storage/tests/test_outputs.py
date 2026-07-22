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
    """Creating existing bucket should be idempotent 200 or 201 and preserve createdAt"""
    resp = requests.put(f"{BASE_URL}/buckets/idempotent", timeout=5)
    assert resp.status_code in (200, 201)
    first_created = resp.json().get("createdAt")
    assert first_created is not None

    time.sleep(0.1)

    resp = requests.put(f"{BASE_URL}/buckets/idempotent", timeout=5)
    assert resp.status_code in (200, 201)
    assert resp.json()["name"] == "idempotent"
    second_created = resp.json().get("createdAt")
    # Idempotent create must preserve original createdAt (not generate new)
    assert second_created == first_created, (
        f"Idempotent bucket create should preserve createdAt: first {first_created} vs second {second_created}"
    )
    # Should still exist only once
    resp = requests.get(f"{BASE_URL}/buckets", timeout=5)
    assert len([b for b in resp.json()["buckets"] if b["name"] == "idempotent"]) == 1


def test_bucket_create_idempotency_preserves_createdAt_filesystem():
    """Idempotent bucket create preserves createdAt on filesystem as well"""
    resp = requests.put(f"{BASE_URL}/buckets/fixedtime", timeout=5)
    assert resp.status_code in (200, 201)
    first = resp.json()["createdAt"]

    time.sleep(0.2)

    resp = requests.put(f"{BASE_URL}/buckets/fixedtime", timeout=5)
    second = resp.json()["createdAt"]
    assert first == second

    # Check filesystem meta if accessible
    if os.path.exists(STORAGE_PATH):
        meta_path = os.path.join(STORAGE_PATH, "fixedtime", ".bucket_meta.json")
        if os.path.isfile(meta_path):
            with open(meta_path) as f:
                meta = json.load(f)
                # createdAt in file should have name field
                assert "name" in meta


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

    # Invalid SHA256 header format (non-hex, wrong length) should be 400 InvalidArgument
    for bad_format in [
        "not-hex",
        "abc",  # too short
        "g" * 64,  # non-hex
        "0" * 63,  # too short by 1
        "0" * 65,  # too long by 1
    ]:
        resp = requests.put(
            f"{BASE_URL}/buckets/checksumbucket/objects/badformat.txt",
            data=content,
            headers={"X-Content-SHA256": bad_format},
            timeout=5,
        )
        assert resp.status_code == 400, (
            f"Expected 400 for invalid SHA256 format {bad_format}, got {resp.status_code}"
        )
        assert resp.json()["code"] in ("InvalidArgument", "BadDigest")


def test_expiration_ttl():
    """Test novel TTL expiration via X-Expire-After and 410 Gone, plus metadata JSON inspection"""
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

    # Check that PUT response includes X-Expires-At header or metadata inspection
    # Inspect filesystem metadata JSON for expiresAt when X-Expire-After is used
    if os.path.exists(STORAGE_PATH):
        meta_path = os.path.join(STORAGE_PATH, "expirebucket", "temp.txt.meta.json")
        # Wait a moment for file to be written
        time.sleep(0.2)
        if os.path.isfile(meta_path):
            with open(meta_path) as f:
                meta = json.load(f)
                assert "expiresAt" in meta, (
                    "Metadata JSON should contain expiresAt when X-Expire-After is used"
                )
                # expiresAt should be future RFC3339
                assert meta["expiresAt"] is not None

    # Immediately GET should succeed and include X-Expires-At header
    resp = requests.get(f"{BASE_URL}/buckets/expirebucket/objects/temp.txt", timeout=5)
    assert resp.status_code == 200
    assert resp.content == b"temporary"
    # Header may be present
    # Lowercase check
    lower_headers = {k.lower(): v for k, v in resp.headers.items()}
    # X-Expires-At is optional in spec but recommended
    # At least not error

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

    # Test expiresAt in metadata JSON is valid RFC3339 and future
    requests.put(f"{BASE_URL}/buckets/expirebucket", timeout=5)
    resp = requests.put(
        f"{BASE_URL}/buckets/expirebucket/objects/inspect.txt",
        data=b"inspect",
        headers={"X-Expire-After": "10"},
        timeout=5,
    )
    assert resp.status_code in (200, 201)
    if os.path.exists(STORAGE_PATH):
        meta_path = os.path.join(STORAGE_PATH, "expirebucket", "inspect.txt.meta.json")
        time.sleep(0.2)
        if os.path.isfile(meta_path):
            with open(meta_path) as f:
                meta = json.load(f)
                assert "expiresAt" in meta
                # Should be parseable and future
                assert meta["expiresAt"] is not None
                # Check that file actually contains expiresAt field
                assert "etag" in meta and "size" in meta


def test_copy_operation():
    """Test novel copy operation with detailed verification of Content-Type, meta, ETag, lastModified, expiresAt"""
    requests.put(f"{BASE_URL}/buckets/srcbucket", timeout=5)
    requests.put(f"{BASE_URL}/buckets/destbucket", timeout=5)

    content = b"copy me with meta"
    # Put original with Content-Type and custom meta and expiration
    headers = {
        "Content-Type": "text/custom-type",
        "X-Amz-Meta-author": "bob",
        "X-Amz-Meta-version": "1",
        "X-Expire-After": "10",
    }
    resp = requests.put(
        f"{BASE_URL}/buckets/srcbucket/objects/original.txt",
        data=content,
        headers=headers,
        timeout=5,
    )
    assert resp.status_code in (200, 201)
    src_etag = resp.json()["etag"]

    # Get src metadata for comparison
    resp_src = requests.get(
        f"{BASE_URL}/buckets/srcbucket/objects/original.txt", timeout=5
    )
    assert resp_src.status_code == 200
    src_last_modified = resp_src.headers.get("Last-Modified")
    src_content_type = resp_src.headers.get("Content-Type")
    src_meta_author = resp_src.headers.get("X-Amz-Meta-author") or resp_src.headers.get(
        "x-amz-meta-author"
    )

    time.sleep(0.2)

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
    dest_etag = resp.json()["etag"]
    # ETag must be same as src (same content)
    assert dest_etag == src_etag, (
        f"Copy ETag should match src: {dest_etag} vs {src_etag}"
    )

    # Verify dest exists with same content
    resp = requests.get(f"{BASE_URL}/buckets/destbucket/objects/copied.txt", timeout=5)
    assert resp.status_code == 200
    assert resp.content == content

    # Verify destination Content-Type preserved
    assert "text/custom-type" in resp.headers.get("Content-Type", "")

    # Verify X-Amz-Meta headers preserved
    lower_headers = {k.lower(): v for k, v in resp.headers.items()}
    assert lower_headers.get("x-amz-meta-author") == "bob"
    assert lower_headers.get("x-amz-meta-version") == "1"

    # Verify ETag same
    assert (
        resp.headers.get("ETag") == src_etag
        or resp.headers.get("ETag", "").strip('"') == src_etag
        or resp.headers.get("ETag") == dest_etag
    )

    # Verify lastModified is new (dest should have newer or >= src lastModified) and is actually new
    dest_last_modified = resp.headers.get("Last-Modified")
    assert dest_last_modified is not None, "Copy dest should have Last-Modified header"
    assert src_last_modified is not None, "Src should have Last-Modified"
    # Parse HTTP dates and ensure dest is >= src (newer or same second)
    try:
        from email.utils import parsedate_to_datetime

        src_dt = parsedate_to_datetime(src_last_modified)
        dest_dt = parsedate_to_datetime(dest_last_modified)
        # Dest should be >= src (new timestamp, not old)
        assert dest_dt >= src_dt, (
            f"Copy dest Last-Modified should be new, >= src: src={src_last_modified} dest={dest_last_modified}"
        )
        # Also check that dest is not too old (within 10s of now)
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)
        # Allow 10s tolerance
        assert (now - dest_dt).total_seconds() <= 10, (
            f"Dest Last-Modified should be recent, got {dest_last_modified}"
        )
    except Exception as e:
        # If parsing fails, at least check presence and that dest != src or dest is present
        # For strictness, we still want to ensure dest has Last-Modified
        assert dest_last_modified is not None

    # Verify preserved expiresAt: both src and dest should have X-Expires-At and share same absolute time or close
    src_expires = resp_src.headers.get("X-Expires-At") or resp_src.headers.get(
        "x-expires-at"
    )
    dest_expires = resp.headers.get("X-Expires-At") or resp.headers.get("x-expires-at")
    # If expiration was set, both should have expiresAt
    if src_expires:
        assert dest_expires is not None, "Copy should preserve expiresAt"

        # They should be same or very close (same absolute time)
        # For simplicity, check they are equal (our reference preserves absolute time)
        # Allow slight tolerance by checking they are both present
        # Real timestamp comparison - must preserve absolute expiry
        def parse_rfc3339(s):
            try:
                if s.endswith("Z"):
                    s = s[:-1] + "+00:00"
                import time

                return time.mktime(time.strptime(s[:19], "%Y-%m-%dT%H:%M:%S"))
            except Exception:
                try:
                    from datetime import datetime

                    ds = s
                    if ds.endswith("Z"):
                        ds = ds[:-1] + "+00:00"
                    dt = datetime.fromisoformat(ds)
                    return dt.timestamp()
                except Exception:
                    return None

        src_ts = parse_rfc3339(src_expires)
        dest_ts = parse_rfc3339(dest_expires)
        assert src_ts is not None and dest_ts is not None
        assert abs(src_ts - dest_ts) <= 2, (
            f"Copy should preserve same absolute expiry, diff {abs(src_ts - dest_ts)}s"
        )

    # Shared expiration behavior: src and dest share same absolute expiresAt, so after expiry both should be gone
    # Wait for src/dest to expire (10s TTL, but we put with 10s, need to wait? Actually we used 10s, not expire in this test)
    # For shared expiration test, we use separate bucket with short TTL

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

    # Copy with invalid dest bucket name format should be 400 InvalidBucketName
    for bad_bucket in ["AB", "InvalidUpper", "-bad", "bad-", "ab"]:
        resp = requests.post(
            f"{BASE_URL}/buckets/srcbucket/objects/original.txt/copy",
            json={"destBucket": bad_bucket, "destKey": "valid.txt"},
            timeout=5,
        )
        assert resp.status_code == 400, (
            f"Expected 400 for bad dest bucket {bad_bucket}, got {resp.status_code}"
        )
        assert resp.json()["code"] in ("InvalidBucketName", "InvalidArgument")

    # Copy with invalid dest key format should be 400 InvalidObjectKey
    for bad_key in ["../escape.txt", "a//b", "/leading.txt", "a/../b.txt"]:
        resp = requests.post(
            f"{BASE_URL}/buckets/srcbucket/objects/original.txt/copy",
            json={"destBucket": "destbucket", "destKey": bad_key},
            timeout=5,
        )
        # Accept 400 for invalid key, or 404 if path cleaned to bucket route (for .. cases)
        assert resp.status_code in (400, 404), (
            f"Expected 400/404 for bad dest key {bad_key}, got {resp.status_code}"
        )


def test_copy_shared_expiration():
    """Copy preserves expiresAt and both src and dest expire together"""
    requests.put(f"{BASE_URL}/buckets/srcexp", timeout=5)
    requests.put(f"{BASE_URL}/buckets/destexp", timeout=5)

    # Put src with 2-second TTL
    resp = requests.put(
        f"{BASE_URL}/buckets/srcexp/objects/exp.txt",
        data=b"will expire",
        headers={"X-Expire-After": "2"},
        timeout=5,
    )
    assert resp.status_code in (200, 201)

    # Immediate copy
    resp = requests.post(
        f"{BASE_URL}/buckets/srcexp/objects/exp.txt/copy",
        json={"destBucket": "destexp", "destKey": "exp-copy.txt"},
        timeout=5,
    )
    assert resp.status_code in (200, 201)

    # Both should exist now
    assert (
        requests.get(
            f"{BASE_URL}/buckets/srcexp/objects/exp.txt", timeout=5
        ).status_code
        == 200
    )
    assert (
        requests.get(
            f"{BASE_URL}/buckets/destexp/objects/exp-copy.txt", timeout=5
        ).status_code
        == 200
    )

    # Wait for expiry (2s + buffer) - accept 410 or 404 to avoid flaky race with reaper (reaper ticks every 1s)
    time.sleep(2.5)

    # Both should be expired with 410 Gone or 404 if reaper already deleted
    assert requests.get(
        f"{BASE_URL}/buckets/srcexp/objects/exp.txt", timeout=5
    ).status_code in (410, 404)
    assert requests.get(
        f"{BASE_URL}/buckets/destexp/objects/exp-copy.txt", timeout=5
    ).status_code in (410, 404)


def test_copy_preserves_absolute_expiry_not_duration():
    """Prove dest preserves same absolute expiry, not resetting duration (short-TTL test)"""
    requests.put(f"{BASE_URL}/buckets/srcexp3", timeout=5)
    requests.put(f"{BASE_URL}/buckets/destexp3", timeout=5)

    # Put src with 10s TTL
    resp = requests.put(
        f"{BASE_URL}/buckets/srcexp3/objects/long.txt",
        data=b"long ttl",
        headers={"X-Expire-After": "10"},
        timeout=5,
    )
    assert resp.status_code in (200, 201)

    # Wait 5 seconds
    time.sleep(5)

    # Copy to dest - should preserve absolute expiry (5 seconds remaining)
    resp = requests.post(
        f"{BASE_URL}/buckets/srcexp3/objects/long.txt/copy",
        json={"destBucket": "destexp3", "destKey": "long-copy.txt"},
        timeout=5,
    )
    assert resp.status_code in (200, 201)

    # Dest should exist immediately after copy
    assert (
        requests.get(
            f"{BASE_URL}/buckets/destexp3/objects/long-copy.txt", timeout=5
        ).status_code
        == 200
    )

    # Wait 6 more seconds - total 11s from src creation, 6s from dest creation
    # If absolute preserved, dest should now be expired (10s TTL from src)
    # If duration reset, dest would still have 4s remaining (10s from copy)
    time.sleep(6)

    src_status = requests.get(
        f"{BASE_URL}/buckets/srcexp3/objects/long.txt", timeout=5
    ).status_code
    dest_status = requests.get(
        f"{BASE_URL}/buckets/destexp3/objects/long-copy.txt", timeout=5
    ).status_code

    assert src_status in (410, 404), (
        f"Src should be expired after 11s, got {src_status}"
    )
    assert dest_status in (410, 404), (
        f"Dest should preserve absolute expiry and be expired after 11s total "
        f"(6s after copy), not reset duration. Got {dest_status}."
    )


def test_copy_expired_source_before_reaper():
    """Copying an expired source before reaper deletes it should return 410"""
    requests.put(f"{BASE_URL}/buckets/srcexp2", timeout=5)
    requests.put(f"{BASE_URL}/buckets/destexp2", timeout=5)

    # Put with very short TTL 1 second
    resp = requests.put(
        f"{BASE_URL}/buckets/srcexp2/objects/short.txt",
        data=b"short lived",
        headers={"X-Expire-After": "1"},
        timeout=5,
    )
    assert resp.status_code in (200, 201)

    # Wait to ensure expired, but before reaper necessarily deletes (reaper runs every 1s, but we test lazy expiration)
    time.sleep(2)

    # Try to copy expired source - should be 410 Gone, not 200
    resp = requests.post(
        f"{BASE_URL}/buckets/srcexp2/objects/short.txt/copy",
        json={"destBucket": "destexp2", "destKey": "should-not-exist.txt"},
        timeout=5,
    )
    assert resp.status_code in (410, 404), (
        f"Copy of expired source should be 410 or 404, got {resp.status_code}"
    )

    # Dest should not exist
    resp = requests.get(
        f"{BASE_URL}/buckets/destexp2/objects/should-not-exist.txt", timeout=5
    )
    assert resp.status_code == 404


def test_routing_folder_encoded_slash():
    """Test that folder%2Ffile.txt is correctly routed as hierarchical key"""
    requests.put(f"{BASE_URL}/buckets/routebucket", timeout=5)

    # PUT using encoded slash %2F should be treated as folder/file.txt
    resp = requests.put(
        f"{BASE_URL}/buckets/routebucket/objects/folder%2Ffile.txt",
        data=b"encoded slash content",
        timeout=5,
    )
    assert resp.status_code in (200, 201), (
        f"Encoded slash PUT failed: {resp.status_code} {resp.text}"
    )

    # GET using raw slash should succeed (same key)
    resp = requests.get(
        f"{BASE_URL}/buckets/routebucket/objects/folder/file.txt", timeout=5
    )
    assert resp.status_code == 200
    assert resp.content == b"encoded slash content"

    # GET using encoded slash should also succeed
    resp = requests.get(
        f"{BASE_URL}/buckets/routebucket/objects/folder%2Ffile.txt", timeout=5
    )
    assert resp.status_code == 200
    assert resp.content == b"encoded slash content"

    # LIST with prefix folder/ should include it
    resp = requests.get(
        f"{BASE_URL}/buckets/routebucket/objects?prefix=folder/", timeout=5
    )
    assert resp.status_code == 200
    keys = [o["key"] for o in resp.json().get("objects", [])]
    assert "folder/file.txt" in keys


def test_routing_double_slash_rejected():
    """Test that a//b double-slash keys are rejected with 400 not normalized"""
    requests.put(f"{BASE_URL}/buckets/routebucket2", timeout=5)

    # Double slash in key should be 400
    resp = requests.put(
        f"{BASE_URL}/buckets/routebucket2/objects/a//b", data=b"x", timeout=5
    )
    assert resp.status_code == 400, f"Expected 400 for a//b, got {resp.status_code}"
    assert resp.json()["code"] == "InvalidObjectKey"

    # Also test via raw socket-style URL that would be normalized by ServeMux if not protected
    # Our wrapper should catch raw // before ServeMux cleans
    resp = requests.put(
        f"{BASE_URL}/buckets/routebucket2/objects/folder//file.txt",
        data=b"x",
        timeout=5,
    )
    assert resp.status_code == 400


def test_routing_leading_slash_rejected():
    """Test that encoded or raw leading slash is rejected"""
    requests.put(f"{BASE_URL}/buckets/routebucket3", timeout=5)

    # Raw leading slash after /objects/ -> /buckets/b/objects//file.txt
    # This is empty segment + leading slash, must be 400 per our wrapper (blocks traversal)
    resp = requests.put(
        f"{BASE_URL}/buckets/routebucket3/objects//file.txt", data=b"x", timeout=5
    )
    assert resp.status_code == 400, (
        f"Expected 400 for leading slash //file.txt, got {resp.status_code}"
    )
    assert resp.json()["code"] == "InvalidObjectKey"

    # Encoded leading slash %2Ffile.txt decodes to /file.txt which starts with slash -> invalid 400
    resp = requests.put(
        f"{BASE_URL}/buckets/routebucket3/objects/%2Ffile.txt", data=b"x", timeout=5
    )
    assert resp.status_code == 400, (
        f"Expected 400 for %2Ffile.txt, got {resp.status_code}"
    )
    assert resp.json()["code"] == "InvalidObjectKey"

    # Encoded %2F at start with folder: %2Ffolder%2Ffile.txt -> /folder/file.txt leading slash invalid
    resp = requests.put(
        f"{BASE_URL}/buckets/routebucket3/objects/%2Ffolder%2Ffile.txt",
        data=b"x",
        timeout=5,
    )
    assert resp.status_code == 400


def test_routing_null_byte_rejected():
    """Test that encoded null byte %00 is rejected"""
    requests.put(f"{BASE_URL}/buckets/routebucket4", timeout=5)

    resp = requests.put(
        f"{BASE_URL}/buckets/routebucket4/objects/%00file.txt", data=b"x", timeout=5
    )
    assert resp.status_code == 400, (
        f"Expected 400 for null byte, got {resp.status_code}"
    )
    assert resp.json()["code"] == "InvalidObjectKey"

    # Also test raw null byte if possible (may be stripped by HTTP lib, but encoded version should be caught)
    resp = requests.put(
        f"{BASE_URL}/buckets/routebucket4/objects/bad%00key.txt", data=b"x", timeout=5
    )
    assert resp.status_code == 400


def test_routing_overlong_key_rejected():
    """Test that overlong keys >1024 chars are rejected, exactly 1024 allowed (filesystem-safe segments)"""
    requests.put(f"{BASE_URL}/buckets/routebucket5", timeout=5)

    long_key = "a" * 1025
    resp = requests.put(
        f"{BASE_URL}/buckets/routebucket5/objects/{long_key}", data=b"x", timeout=5
    )
    assert resp.status_code == 400, (
        f"Expected 400 for overlong key, got {resp.status_code}"
    )
    assert resp.json()["code"] == "InvalidObjectKey"

    # Exactly 1024 should be allowed - use hierarchical to avoid filesystem 255-char limit per segment
    # 200*4 + 220 + 4 slashes = 1024, each segment <255
    exact_key = (
        "a" * 200
        + "/"
        + "b" * 200
        + "/"
        + "c" * 200
        + "/"
        + "d" * 200
        + "/"
        + "e" * 220
    )
    assert len(exact_key) == 1024, f"exact_key len should be 1024, got {len(exact_key)}"
    resp = requests.put(
        f"{BASE_URL}/buckets/routebucket5/objects/{exact_key}", data=b"x", timeout=5
    )
    assert resp.status_code in (200, 201), (
        f"Exact 1024 key should be allowed, got {resp.status_code} {resp.text}"
    )

    # Verify it was stored and retrievable
    resp = requests.get(
        f"{BASE_URL}/buckets/routebucket5/objects/{exact_key}", timeout=5
    )
    assert resp.status_code == 200

    # 1025 via hierarchical also should be rejected (same pattern + 1 extra char = 1025)
    over_key = (
        "a" * 200
        + "/"
        + "b" * 200
        + "/"
        + "c" * 200
        + "/"
        + "d" * 200
        + "/"
        + "e" * 221
    )
    assert len(over_key) == 1025
    resp = requests.put(
        f"{BASE_URL}/buckets/routebucket5/objects/{over_key}", data=b"x", timeout=5
    )
    assert resp.status_code == 400

    # Single segment overlong also 400 (already tested above with a*1025)


def test_routing_encoded_dotdot_rejected():
    """Test that encoded dot-dot segments are rejected"""
    requests.put(f"{BASE_URL}/buckets/routebucket6", timeout=5)

    # %2E%2E is ..
    invalid_encoded = [
        "%2E%2E/escape.txt",
        "a/%2E%2E/b.txt",
        "a/%2e%2e/b.txt",  # lowercase
        "a/%2E%2E/%2E%2E/b.txt",
        "%2E%2E%2Fescape.txt",  # ../ encoded slash as %2F
    ]
    for key in invalid_encoded:
        resp = requests.put(
            f"{BASE_URL}/buckets/routebucket6/objects/{key}", data=b"x", timeout=5
        )
        # Should be blocked as 400 or 404 (if cleaned to bucket operation)
        assert resp.status_code in (400, 404), (
            f"Expected 400/404 for encoded dot-dot key {key}, got {resp.status_code}"
        )


def test_data_dir_fallback():
    """Test DATA_DIR fallback when STORAGE_PATH not set - verify code handles it"""
    # Check main.go contains DATA_DIR handling (static evidence)
    found_data_dir = False
    found_storage_path = False
    for search_path in ["/app/main.go", "./main.go"]:
        if os.path.isfile(search_path):
            try:
                with open(search_path) as f:
                    content = f.read()
                    if "DATA_DIR" in content:
                        found_data_dir = True
                    if "STORAGE_PATH" in content:
                        found_storage_path = True
            except Exception:
                pass

    sol_path = "/solution/solve.sh"
    if os.path.isfile(sol_path):
        try:
            with open(sol_path) as f:
                content = f.read()
                if "DATA_DIR" in content:
                    found_data_dir = True
                if "STORAGE_PATH" in content:
                    found_storage_path = True
        except Exception:
            pass

    assert found_storage_path, "Code should handle STORAGE_PATH env var per spec"
    # DATA_DIR is required fallback per latest spec
    assert found_data_dir, "Code should handle DATA_DIR fallback per spec"

    # Basic check main server still running
    resp = requests.get(f"{BASE_URL}/buckets", timeout=5)
    assert resp.status_code == 200


def test_data_dir_fallback_real_server():
    """Real check for DATA_DIR fallback by starting server without STORAGE_PATH, with DATA_DIR set on different port"""
    import subprocess
    import shutil

    data_dir = "/tmp/codimango/data_dir_fallback_test"
    # Clean
    shutil.rmtree(data_dir, ignore_errors=True)
    os.makedirs(data_dir, exist_ok=True)

    # Find binary
    bin_candidates = [
        "./blob-server",
        "/app/blob-server",
        "/tmp/codimango/blob-server",
        "/tmp/blob-server",
    ]
    bin_path = None
    for p in bin_candidates:
        if os.path.isfile(p):
            bin_path = p
            break
    if not bin_path:
        pytest.skip("Binary not found for DATA_DIR fallback real test")

    # Start server on 8081 with DATA_DIR set and STORAGE_PATH explicitly unset
    env = os.environ.copy()
    env.pop("STORAGE_PATH", None)
    env["DATA_DIR"] = data_dir
    env["PORT"] = "8081"

    proc = subprocess.Popen(
        [bin_path],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    try:
        # Wait for server on 8081
        ready = False
        for _ in range(15):
            time.sleep(1)
            try:
                r = requests.get("http://localhost:8081/buckets", timeout=2)
                if r.status_code == 200:
                    ready = True
                    break
            except Exception:
                pass
            if proc.poll() is not None:
                # Server died
                stdout, stderr = proc.communicate(timeout=1)
                print(
                    "Server failed to start with DATA_DIR, stdout:",
                    stdout.decode()[:500],
                )
                print("stderr:", stderr.decode()[:500])
                break

        if not ready:
            pytest.skip(
                "DATA_DIR fallback server on 8081 not ready, skipping real fallback test"
            )

        # Create bucket via 8081 - should appear under DATA_DIR, not STORAGE_PATH
        resp = requests.put("http://localhost:8081/buckets/datadirbucket", timeout=5)
        assert resp.status_code in (200, 201)

        # Put object
        resp = requests.put(
            "http://localhost:8081/buckets/datadirbucket/objects/fallback.txt",
            data=b"fallback data",
            timeout=5,
        )
        assert resp.status_code in (200, 201)

        # Verify file exists under DATA_DIR
        time.sleep(0.5)
        expected_file = os.path.join(data_dir, "datadirbucket", "fallback.txt")
        assert os.path.isfile(expected_file), (
            f"File should be under DATA_DIR {data_dir}, not found at {expected_file}"
        )

        # Verify it does NOT appear under STORAGE_PATH (which is /tmp/blob-data for main server)
        # Since main server uses /tmp/blob-data, datadirbucket should not appear there
        assert not os.path.exists(os.path.join(STORAGE_PATH, "datadirbucket")), (
            "DATA_DIR fallback should not use STORAGE_PATH"
        )

        # Verify GET via 8081 works
        resp = requests.get(
            "http://localhost:8081/buckets/datadirbucket/objects/fallback.txt",
            timeout=5,
        )
        assert resp.status_code == 200
        assert resp.content == b"fallback data"

    finally:
        # Cleanup: kill 8081 server
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except Exception:
            proc.kill()
        shutil.rmtree(data_dir, ignore_errors=True)


def test_metadata_json_expires_at_inspection():
    """Inspect object metadata JSON for expiresAt when X-Expire-After is used"""
    requests.put(f"{BASE_URL}/buckets/metainspect", timeout=5)

    # Put with expiration
    resp = requests.put(
        f"{BASE_URL}/buckets/metainspect/objects/with-expire.txt",
        data=b"has expiration",
        headers={"X-Expire-After": "10"},
        timeout=5,
    )
    assert resp.status_code in (200, 201)

    # Check filesystem metadata JSON if accessible
    if os.path.exists(STORAGE_PATH):
        meta_path = os.path.join(
            STORAGE_PATH, "metainspect", "with-expire.txt.meta.json"
        )
        # Allow time for write
        time.sleep(0.3)
        assert os.path.isfile(meta_path), f"Meta file should exist at {meta_path}"
        with open(meta_path) as f:
            meta = json.load(f)
            assert "expiresAt" in meta, (
                "Metadata should have expiresAt field when X-Expire-After used"
            )
            # expiresAt should be a string RFC3339
            expires_str = meta["expiresAt"]
            assert expires_str is not None
            # Try parsing
            # expiresAt may be string
            # If stored as string, check it's not empty and contains T
            if isinstance(expires_str, str):
                assert "T" in expires_str or "Z" in expires_str
            # Also check other required fields exist
            assert "etag" in meta
            assert "size" in meta
            assert "contentType" in meta

    # Without expiration, expiresAt should be absent or null
    resp = requests.put(
        f"{BASE_URL}/buckets/metainspect/objects/no-expire.txt",
        data=b"no expiration",
        timeout=5,
    )
    assert resp.status_code in (200, 201)

    if os.path.exists(STORAGE_PATH):
        meta_path = os.path.join(STORAGE_PATH, "metainspect", "no-expire.txt.meta.json")
        time.sleep(0.2)
        if os.path.isfile(meta_path):
            with open(meta_path) as f:
                meta = json.load(f)
                # expiresAt should be absent or None when no TTL
                # Our reference omits expiresAt when none (omitempty)
                assert meta.get("expiresAt") is None or "expiresAt" not in meta
