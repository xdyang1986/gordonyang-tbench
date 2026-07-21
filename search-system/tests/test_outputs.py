"""
Elasticsearch-like search engine tests.

Builds Go project at /app and runs HTTP server, then black-box tests via requests.
"""
import os
import sys
import json
import time
import shutil
import socket
import subprocess
import tempfile
import threading
from pathlib import Path

import pytest
import requests

APP = "/app"
BIN = "/tmp/search-server"
DATA_DIR = "/app/data"
GO_ENV = {
    **os.environ,
    "GOTOOLCHAIN": "local",
    "GOFLAGS": "-mod=mod",
    "GOCACHE": "/tmp/gocache",
    "GOPATH": "/tmp/gopath",
}

def find_free_port():
    s = socket.socket()
    s.bind(("", 0))
    port = s.getsockname()[1]
    s.close()
    return port

def wait_for_server(port, timeout=15):
    start = time.time()
    url = f"http://127.0.0.1:{port}/search"
    while time.time() - start < timeout:
        try:
            r = requests.get(url, timeout=1)
            if r.status_code in (200, 400):
                return True
        except Exception:
            pass
        time.sleep(0.2)
    return False

@pytest.fixture(scope="session", autouse=True)
def build_binary():
    # clean data dir for fresh start
    if os.path.exists(DATA_DIR):
        shutil.rmtree(DATA_DIR, ignore_errors=True)
    # init go.mod if missing
    if not os.path.exists(os.path.join(APP, "go.mod")):
        subprocess.run(["go", "mod", "init", "searchengine"], cwd=APP, env=GO_ENV, capture_output=True)
    # ensure go mod tidy
    subprocess.run(["go", "mod", "tidy"], cwd=APP, env=GO_ENV, capture_output=True, timeout=60)
    # build
    res = subprocess.run(["go", "build", "-o", BIN, "."], cwd=APP, env=GO_ENV, capture_output=True, text=True, timeout=120)
    assert res.returncode == 0, f"go build failed:\nSTDOUT:{res.stdout}\nSTDERR:{res.stderr}"
    assert os.path.exists(BIN), "binary not produced"
    yield

@pytest.fixture()
def server():
    port = find_free_port()
    env = {**os.environ, "PORT": str(port)}
    # clean data dir if we want isolation per test? We want persistence test to manage own clean.
    # But for most tests, we clean before start to avoid cross-contamination.
    # Actually we clean data dir here to have empty index per test function.
    # Persistence test will handle its own restart inside test.
    if os.path.exists(DATA_DIR):
        shutil.rmtree(DATA_DIR, ignore_errors=True)
    proc = subprocess.Popen([BIN], cwd=APP, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert wait_for_server(port, timeout=15), f"server failed to start on port {port}. stderr: {proc.stderr.read().decode()[:500]}"
    base = f"http://127.0.0.1:{port}"
    try:
        yield base
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        # cleanup data after
        if os.path.exists(DATA_DIR):
            shutil.rmtree(DATA_DIR, ignore_errors=True)
        time.sleep(0.2)

def post_doc(base, doc):
    return requests.post(f"{base}/documents", json=doc, timeout=5)

def get_doc(base, doc_id):
    return requests.get(f"{base}/documents/{doc_id}", timeout=5)

def delete_doc(base, doc_id):
    return requests.delete(f"{base}/documents/{doc_id}", timeout=5)

def search_get(base, q=None, tags=None, operator=None, limit=None, offset=None):
    params = {}
    if q is not None:
        params["q"] = q
    if tags is not None:
        params["tags"] = tags
    if operator is not None:
        params["operator"] = operator
    if limit is not None:
        params["limit"] = limit
    if offset is not None:
        params["offset"] = offset
    return requests.get(f"{base}/search", params=params, timeout=5)

def search_post(base, body):
    return requests.post(f"{base}/search", json=body, timeout=5)

# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_index_and_get(server):
    base = server
    doc = {"id":"doc1","title":"Go Programming","body":"Go is great for search engines","tags":["go","programming"]}
    r = post_doc(base, doc)
    assert r.status_code in (200,201), f"index failed {r.status_code} {r.text}"
    j = r.json()
    assert j.get("id")== "doc1" or j.get("ok") is True

    r = get_doc(base, "doc1")
    assert r.status_code == 200, r.text
    got = r.json()
    assert got["id"] == "doc1"
    assert "Go Programming" in got["title"]
    assert "go" in [t.lower() for t in got["tags"]]

def test_upsert_overwrites(server):
    base = server
    post_doc(base, {"id":"d1","title":"old","body":"old body","tags":["old"]})
    r2 = post_doc(base, {"id":"d1","title":"new","body":"new body","tags":["new"]})
    assert r2.status_code in (200,201)
    r = get_doc(base, "d1")
    assert r.status_code == 200
    assert r.json()["title"] == "new"
    # old term should not be found
    s_old = search_get(base, q="old")
    assert s_old.status_code == 200
    assert s_old.json()["total"] == 0
    s_new = search_get(base, q="new")
    assert s_new.json()["total"] == 1

def test_delete(server):
    base = server
    post_doc(base, {"id":"del1","title":"to delete","body":"delete me","tags":[]})
    assert get_doc(base, "del1").status_code == 200
    r = delete_doc(base, "del1")
    assert r.status_code == 200
    assert get_doc(base, "del1").status_code == 404
    # search should not find it
    s = search_get(base, q="delete")
    assert s.json()["total"] == 0
    # delete again -> 404
    r2 = delete_doc(base, "del1")
    assert r2.status_code == 404

def test_search_simple_or(server):
    base = server
    post_doc(base, {"id":"1","title":"Go Search","body":"build a search engine in Go","tags":["go","search"]})
    post_doc(base, {"id":"2","title":"Java Search","body":"Lucene is Java search library","tags":["java","search"]})
    post_doc(base, {"id":"3","title":"Python","body":"Python programming","tags":["python"]})

    r = search_get(base, q="search")
    assert r.status_code == 200
    j = r.json()
    assert j["total"] == 2
    ids = {x["id"] for x in j["results"]}
    assert ids == {"1","2"}

    r = search_get(base, q="go search", operator="OR")
    assert r.status_code == 200
    j = r.json()
    # OR should match docs containing go or search => docs 1 and 2
    assert j["total"] == 2

    # check POST /search body
    r = search_post(base, {"query":"search","operator":"OR","limit":10,"offset":0})
    assert r.status_code == 200
    assert r.json()["total"] == 2

def test_search_operator_and(server):
    base = server
    post_doc(base, {"id":"1","title":"Go Search","body":"build a search engine in Go","tags":["go"]})
    post_doc(base, {"id":"2","title":"Java Search","body":"Lucene is Java search library","tags":["java"]})
    post_doc(base, {"id":"3","title":"Go Programming","body":"Go is awesome","tags":["go"]})

    r = search_get(base, q="go search", operator="AND")
    assert r.status_code == 200
    # only doc 1 has both go and search
    assert r.json()["total"] == 1
    assert r.json()["results"][0]["id"] == "1"

    # default operator is OR, but explicitly AND via POST
    r = search_post(base, {"query":"go search","operator":"AND"})
    assert r.json()["total"] == 1

def test_search_boolean_with_explicit_operators(server):
    base = server
    docs = [
        {"id":"1","title":"Go Search Engine","body":"build search in Go","tags":["go"]},
        {"id":"2","title":"Java Search","body":"Lucene Java","tags":["java"]},
        {"id":"3","title":"Go and Java","body":"comparison of Go and Java","tags":["go","java"]},
    ]
    for d in docs:
        post_doc(base, d)

    # go AND search
    r = search_get(base, q="go AND search")
    assert r.status_code == 200
    ids = {x["id"] for x in r.json()["results"]}
    assert ids == {"1"}, f"expected only doc1 for go AND search, got {ids}"

    # go OR java -> all 3 (since 1 has go, 2 java, 3 both)
    r = search_get(base, q="go OR java")
    assert r.json()["total"] == 3

    # go NOT java -> docs with go but not java -> only doc1
    # support both "go NOT java" and "go AND NOT java"
    r = search_get(base, q="go NOT java")
    assert r.status_code == 200
    ids = {x["id"] for x in r.json()["results"]}
    assert ids == {"1"}, f"go NOT java should be doc1 only, got {ids}"

    r = search_get(base, q="go AND NOT java")
    ids = {x["id"] for x in r.json()["results"]}
    assert ids == {"1"}

    # NOT go -> docs without go => only doc2
    r = search_get(base, q="NOT go")
    ids = {x["id"] for x in r.json()["results"]}
    assert ids == {"2"}

def test_search_boolean_with_parentheses(server):
    base = server
    docs = [
        {"id":"1","title":"Go Search Engine","body":"Go search index","tags":["go"]},
        {"id":"2","title":"Java Search Engine","body":"Java search lucene","tags":["java"]},
        {"id":"3","title":"Go Java Mix","body":"Go and Java","tags":[]},
        {"id":"4","title":"Python Search","body":"Python search","tags":["python"]},
    ]
    for d in docs:
        post_doc(base, d)

    # (go OR java) AND search -> doc1 and doc2 (both have search and go/java)
    r = search_get(base, q="(go OR java) AND search")
    assert r.status_code == 200
    ids = {x["id"] for x in r.json()["results"]}
    assert ids == {"1","2"}, f"expected 1,2 got {ids}"

    # go AND (search OR index) -> doc1 qualifies (search present, index present)
    r = search_get(base, q="go AND (search OR index)")
    ids = {x["id"] for x in r.json()["results"]}
    assert "1" in ids

    # go AND (search OR index) NOT java -> should exclude doc3 if java filtered
    r = search_get(base, q="go AND (search OR index) NOT java")
    ids = {x["id"] for x in r.json()["results"]}
    assert ids == {"1"}, f"complex bool expected doc1 only, got {ids}"

def test_search_scoring_ranking(server):
    base = server
    # doc with higher tf should rank higher for same term
    post_doc(base, {"id":"high","title":"go go go","body":"go go go go search","tags":[]})
    post_doc(base, {"id":"low","title":"go","body":"search","tags":[]})
    post_doc(base, {"id":"mid","title":"go go","body":"go search","tags":[]})

    r = search_get(base, q="go")
    assert r.status_code == 200
    j = r.json()
    assert j["total"] == 3
    # results sorted by score descending
    results = j["results"]
    assert results[0]["id"] == "high", f"expected high first, got {[x['id'] for x in results]}"
    # check scores exist and descending
    scores = [x["score"] for x in results]
    assert scores == sorted(scores, reverse=True), "scores not descending"
    # high should have highest score
    assert scores[0] > scores[-1]

def test_search_tag_filtering(server):
    base = server
    post_doc(base, {"id":"1","title":"Go Search","body":"Go search","tags":["go","search"]})
    post_doc(base, {"id":"2","title":"Go Only","body":"Go programming","tags":["go"]})
    post_doc(base, {"id":"3","title":"Search Only","body":"Search engine","tags":["search"]})

    # tags=go returns docs 1 and 2
    r = search_get(base, q="", tags="go")
    assert r.status_code == 200
    assert r.json()["total"] == 2
    ids = {x["id"] for x in r.json()["results"]}
    assert ids == {"1","2"}

    # tags=go,search returns only doc1 (must contain ALL)
    r = search_get(base, q="", tags="go,search")
    assert r.json()["total"] == 1
    assert r.json()["results"][0]["id"] == "1"

    # combine query + tags: q=search & tags=go => only doc1 (search AND go tag)
    r = search_get(base, q="search", tags="go")
    assert r.json()["total"] == 1
    assert r.json()["results"][0]["id"] == "1"

    # POST version with tags array
    r = search_post(base, {"query":"search","tags":["go"]})
    assert r.json()["total"] == 1

    # case insensitive tags
    r = search_get(base, q="", tags="Go")
    assert r.json()["total"] == 2

def test_search_pagination(server):
    base = server
    for i in range(5):
        post_doc(base, {"id":f"d{i}","title":f"doc {i} search","body":f"Search content {i}","tags":[]})

    r = search_get(base, q="search", limit=2, offset=0)
    assert r.status_code == 200
    j = r.json()
    assert j["total"] == 5
    assert len(j["results"]) == 2

    r2 = search_get(base, q="search", limit=2, offset=2)
    assert len(r2.json()["results"]) == 2

    r3 = search_get(base, q="search", limit=2, offset=4)
    assert len(r3.json()["results"]) == 1

    r4 = search_get(base, q="search", limit=10, offset=0)
    assert len(r4.json()["results"]) == 5

    # check no overlap between pages
    ids_page1 = {x["id"] for x in r.json()["results"]}
    ids_page2 = {x["id"] for x in r2.json()["results"]}
    assert ids_page1.isdisjoint(ids_page2)

def test_search_empty_query_returns_all(server):
    base = server
    post_doc(base, {"id":"a","title":"first","body":"alpha","tags":["t1"]})
    post_doc(base, {"id":"b","title":"second","body":"beta","tags":["t2"]})

    r = search_get(base, q="")
    assert r.status_code == 200
    assert r.json()["total"] == 2

    # no q param at all => match all
    r = search_get(base)
    # should be 200 and return all
    assert r.status_code == 200
    # might be 2 if no tags filter
    assert r.json()["total"] == 2

def test_persistence():
    """Special test that restarts server and checks data retained."""
    port = find_free_port()
    env = {**os.environ, "PORT": str(port)}
    # clean data dir
    if os.path.exists(DATA_DIR):
        shutil.rmtree(DATA_DIR, ignore_errors=True)

    proc = subprocess.Popen([BIN], cwd=APP, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert wait_for_server(port, timeout=15), "server first start failed"
    base = f"http://127.0.0.1:{port}"

    try:
        # index
        r = requests.post(f"{base}/documents", json={"id":"persist1","title":"persistent","body":"should persist across restart","tags":["persist"]}, timeout=5)
        assert r.status_code in (200,201)
        r = requests.get(f"{base}/documents/persist1", timeout=5)
        assert r.status_code == 200

        # check file exists
        time.sleep(0.5)  # give time for file write
        assert os.path.exists(os.path.join(DATA_DIR, "index.json")), "persistence file not created"

        # stop server
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        time.sleep(0.5)

        # restart server same port
        port2 = port # same port, to reuse data dir
        env2 = {**os.environ, "PORT": str(port2)}
        proc2 = subprocess.Popen([BIN], cwd=APP, env=env2, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        assert wait_for_server(port2, timeout=15), "server second start failed after persistence"
        base2 = f"http://127.0.0.1:{port2}"

        # doc should still be there
        r = requests.get(f"{base2}/documents/persist1", timeout=5)
        assert r.status_code == 200, f"doc not persisted after restart, got {r.status_code} {r.text}"
        assert r.json()["title"] == "persistent"

        # search should find it
        r = requests.get(f"{base2}/search", params={"q":"persistent"}, timeout=5)
        assert r.status_code == 200
        assert r.json()["total"] == 1

        proc2.terminate()
        try:
            proc2.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc2.kill()
    finally:
        # ensure any proc killed
        try:
            proc.terminate()
        except:
            pass
        try:
            proc2.terminate()
        except:
            pass
        if os.path.exists(DATA_DIR):
            shutil.rmtree(DATA_DIR, ignore_errors=True)

def test_invalid_inputs(server):
    base = server

    # missing id
    r = requests.post(f"{base}/documents", json={"title":"no id"}, timeout=5)
    assert r.status_code == 400, f"expected 400 for missing id, got {r.status_code}"

    # invalid json
    r = requests.post(f"{base}/documents", data="not json", headers={"Content-Type":"application/json"}, timeout=5)
    assert r.status_code == 400

    # get missing -> 404
    r = requests.get(f"{base}/documents/doesnotexist", timeout=5)
    assert r.status_code == 404

    # invalid limit
    r = requests.get(f"{base}/search", params={"limit":"-1"}, timeout=5)
    # spec says 400 for invalid limit/offset, but we allow leniency: if -1 returns 400 or empty? Require 400
    assert r.status_code == 400, f"expected 400 for negative limit, got {r.status_code}"

    r = requests.get(f"{base}/search", params={"offset":"-5"}, timeout=5)
    assert r.status_code == 400

    # POST /search invalid json
    r = requests.post(f"{base}/search", data="{{{", headers={"Content-Type":"application/json"}, timeout=5)
    assert r.status_code == 400

def test_concurrency(server):
    base = server
    errors = []

    def index_docs(start, count):
        for i in range(count):
            doc_id = f"c{start}_{i}"
            try:
                r = requests.post(f"{base}/documents", json={"id":doc_id,"title":f"concurrent {doc_id}","body":"stress test Go search engine concurrent","tags":["concurrent"]}, timeout=5)
                if r.status_code not in (200,201):
                    errors.append(f"index {doc_id} failed {r.status_code}")
            except Exception as e:
                errors.append(str(e))

    def search_loop():
        for _ in range(20):
            try:
                r = requests.get(f"{base}/search", params={"q":"concurrent","tags":"concurrent"}, timeout=5)
                if r.status_code != 200:
                    errors.append(f"search concurrent failed {r.status_code}")
            except Exception as e:
                errors.append(str(e))

    threads = []
    for t in range(5):
        th = threading.Thread(target=index_docs, args=(t, 10))
        threads.append(th)
        th.start()
    for _ in range(3):
        th = threading.Thread(target=search_loop)
        threads.append(th)
        th.start()

    for th in threads:
        th.join()

    assert not errors, f"concurrency errors: {errors}"
    # after concurrent indexing, we should have 50 docs, search should find them
    r = requests.get(f"{base}/search", params={"q":"concurrent","limit":"100"}, timeout=5)
    assert r.status_code == 200
    assert r.json()["total"] >= 50

def test_search_result_format(server):
    base = server
    post_doc(base, {"id":"fmt1","title":"Format Test","body":"Check format fields","tags":["fmt"]})
    r = search_get(base, q="format")
    assert r.status_code == 200
    j = r.json()
    assert "total" in j
    assert "results" in j
    assert isinstance(j["results"], list)
    if len(j["results"])>0:
        first = j["results"][0]
        assert "id" in first
        assert "score" in first
        assert isinstance(first["score"], (int,float))
        assert "title" in first
        assert "tags" in first

def test_search_bool_or_precedence(server):
    base = server
    post_doc(base, {"id":"1","title":"a b","body":"a b","tags":[]})
    post_doc(base, {"id":"2","title":"b c","body":"b c","tags":[]})
    post_doc(base, {"id":"3","title":"a c","body":"a c","tags":[]})

    # a OR b AND c -> AND higher precedence => a OR (b AND c)
    # doc1: a,b -> has a true => match, has b&c false, but a true => overall true
    # doc2: b,c -> b AND c true => true
    # doc3: a,c -> a true => true, also b&c false but a true => true
    # all 3 should match? Let's check spec: NOT highest, AND medium, OR lowest
    r = search_get(base, q="a OR b AND c")
    assert r.status_code == 200
    # doc1 should be included (a alone)
    ids = {x["id"] for x in r.json()["results"]}
    assert "1" in ids
    assert "2" in ids # b AND c
