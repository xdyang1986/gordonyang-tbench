"""
Elasticsearch-like search engine HARD tests.

Builds Go project at /app and runs HTTP server, black-box via requests.

Covers:
- CRUD, upsert, delete
- Simple OR/AND, boolean with NOT and parentheses, precedence
- TF / BM25 ranking
- Tag filtering (AND semantics)
- Pagination, empty query
- Persistence + recovery
- Concurrency
- Invalid inputs
- Phrase queries (positional)
- Field-specific (title:, body:, tags:)
- Prefix (sea*) and fuzzy (sarch~)
- Boost (^)
- BM25 exact scoring
- Highlight <em>
- Aggregations tags
- Bulk API NDJSON
- Stats endpoint
- Complex bool combining field/phrase/prefix/fuzzy
- Unknown field and invalid boost 400
"""

import os, json, time, shutil, socket, subprocess, threading, math
import pytest, requests

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
    p = s.getsockname()[1]
    s.close()
    return p


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
    if os.path.exists(DATA_DIR):
        shutil.rmtree(DATA_DIR, ignore_errors=True)
    if not os.path.exists(os.path.join(APP, "go.mod")):
        subprocess.run(
            ["go", "mod", "init", "searchengine"],
            cwd=APP,
            env=GO_ENV,
            capture_output=True,
        )
    subprocess.run(
        ["go", "mod", "tidy"], cwd=APP, env=GO_ENV, capture_output=True, timeout=60
    )
    res = subprocess.run(
        ["go", "build", "-o", BIN, "."],
        cwd=APP,
        env=GO_ENV,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert res.returncode == 0, (
        f"go build failed:\nSTDOUT:{res.stdout}\nSTDERR:{res.stderr}"
    )
    assert os.path.exists(BIN), "binary not produced"
    yield


@pytest.fixture()
def server():
    port = find_free_port()
    env = {**os.environ, "PORT": str(port)}
    if os.path.exists(DATA_DIR):
        shutil.rmtree(DATA_DIR, ignore_errors=True)
    proc = subprocess.Popen(
        [BIN], cwd=APP, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    assert wait_for_server(port, timeout=15), f"server failed to start on port {port}"
    base = f"http://127.0.0.1:{port}"
    try:
        yield base
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        if os.path.exists(DATA_DIR):
            shutil.rmtree(DATA_DIR, ignore_errors=True)
        time.sleep(0.2)


def post_doc(base, doc):
    return requests.post(f"{base}/documents", json=doc, timeout=5)


def get_doc(base, doc_id):
    return requests.get(f"{base}/documents/{doc_id}", timeout=5)


def delete_doc(base, doc_id):
    return requests.delete(f"{base}/documents/{doc_id}", timeout=5)


def search_get(
    base, q=None, tags=None, operator=None, limit=None, offset=None, highlight=None
):
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
    if highlight is not None:
        params["highlight"] = str(highlight).lower()
    return requests.get(f"{base}/search", params=params, timeout=5)


def search_post(base, body):
    return requests.post(f"{base}/search", json=body, timeout=5)


# ---------------------------------------------------------------------------
# Basic CRUD
# ---------------------------------------------------------------------------


def test_index_and_get(server):
    base = server
    doc = {
        "id": "doc1",
        "title": "Go Programming",
        "body": "Go is great for search engines",
        "tags": ["go", "programming"],
    }
    r = post_doc(base, doc)
    assert r.status_code in (200, 201), f"index failed {r.status_code} {r.text}"
    j = r.json()
    assert j.get("id") == "doc1" or j.get("ok") is True
    r = get_doc(base, "doc1")
    assert r.status_code == 200, r.text
    got = r.json()
    assert got["id"] == "doc1"
    assert "Go Programming" in got["title"]
    assert "go" in [t.lower() for t in got["tags"]]


def test_upsert_overwrites(server):
    base = server
    post_doc(base, {"id": "d1", "title": "old", "body": "old body", "tags": ["old"]})
    r2 = post_doc(
        base, {"id": "d1", "title": "new", "body": "new body", "tags": ["new"]}
    )
    assert r2.status_code in (200, 201)
    r = get_doc(base, "d1")
    assert r.status_code == 200
    assert r.json()["title"] == "new"
    s_old = search_get(base, q="old")
    assert s_old.status_code == 200
    assert s_old.json()["total"] == 0
    s_new = search_get(base, q="new")
    assert s_new.json()["total"] == 1


def test_delete(server):
    base = server
    post_doc(
        base, {"id": "del1", "title": "to delete", "body": "delete me", "tags": []}
    )
    assert get_doc(base, "del1").status_code == 200
    r = delete_doc(base, "del1")
    assert r.status_code == 200
    assert get_doc(base, "del1").status_code == 404
    s = search_get(base, q="delete")
    assert s.json()["total"] == 0
    r2 = delete_doc(base, "del1")
    assert r2.status_code == 404


# ---------------------------------------------------------------------------
# Simple search
# ---------------------------------------------------------------------------


def test_search_simple_or(server):
    base = server
    post_doc(
        base,
        {
            "id": "1",
            "title": "Go Search",
            "body": "build a search engine in Go",
            "tags": ["go", "search"],
        },
    )
    post_doc(
        base,
        {
            "id": "2",
            "title": "Java Search",
            "body": "Lucene is Java search library",
            "tags": ["java", "search"],
        },
    )
    post_doc(
        base,
        {
            "id": "3",
            "title": "Python",
            "body": "Python programming",
            "tags": ["python"],
        },
    )
    r = search_get(base, q="search")
    assert r.status_code == 200
    j = r.json()
    assert j["total"] == 2
    ids = {x["id"] for x in j["results"]}
    assert ids == {"1", "2"}
    r = search_get(base, q="go search", operator="OR")
    assert r.status_code == 200
    assert r.json()["total"] == 2
    r = search_post(
        base, {"query": "search", "operator": "OR", "limit": 10, "offset": 0}
    )
    assert r.status_code == 200
    assert r.json()["total"] == 2


def test_search_operator_and(server):
    base = server
    post_doc(
        base,
        {
            "id": "1",
            "title": "Go Search",
            "body": "build a search engine in Go",
            "tags": ["go"],
        },
    )
    post_doc(
        base,
        {
            "id": "2",
            "title": "Java Search",
            "body": "Lucene is Java search library",
            "tags": ["java"],
        },
    )
    post_doc(
        base,
        {"id": "3", "title": "Go Programming", "body": "Go is awesome", "tags": ["go"]},
    )
    r = search_get(base, q="go search", operator="AND")
    assert r.status_code == 200
    assert r.json()["total"] == 1
    assert r.json()["results"][0]["id"] == "1"
    r = search_post(base, {"query": "go search", "operator": "AND"})
    assert r.json()["total"] == 1


def test_search_boolean_with_explicit_operators(server):
    base = server
    docs = [
        {
            "id": "1",
            "title": "Go Search Engine",
            "body": "build search in Go",
            "tags": ["go"],
        },
        {"id": "2", "title": "Java Search", "body": "Lucene Java", "tags": ["java"]},
        {
            "id": "3",
            "title": "Go and Java",
            "body": "comparison of Go and Java",
            "tags": ["go", "java"],
        },
    ]
    for d in docs:
        post_doc(base, d)
    r = search_get(base, q="go AND search")
    assert r.status_code == 200
    ids = {x["id"] for x in r.json()["results"]}
    assert ids == {"1"}
    r = search_get(base, q="go OR java")
    assert r.json()["total"] == 3
    r = search_get(base, q="go NOT java")
    assert r.status_code == 200
    ids = {x["id"] for x in r.json()["results"]}
    assert ids == {"1"}
    r = search_get(base, q="go AND NOT java")
    ids = {x["id"] for x in r.json()["results"]}
    assert ids == {"1"}
    r = search_get(base, q="NOT go")
    ids = {x["id"] for x in r.json()["results"]}
    assert ids == {"2"}


def test_search_boolean_with_parentheses(server):
    base = server
    docs = [
        {
            "id": "1",
            "title": "Go Search Engine",
            "body": "Go search index",
            "tags": ["go"],
        },
        {
            "id": "2",
            "title": "Java Search Engine",
            "body": "Java search lucene",
            "tags": ["java"],
        },
        {"id": "3", "title": "Go Java Mix", "body": "Go and Java", "tags": []},
        {
            "id": "4",
            "title": "Python Search",
            "body": "Python search",
            "tags": ["python"],
        },
    ]
    for d in docs:
        post_doc(base, d)
    r = search_get(base, q="(go OR java) AND search")
    assert r.status_code == 200
    ids = {x["id"] for x in r.json()["results"]}
    assert ids == {"1", "2"}
    r = search_get(base, q="go AND (search OR index)")
    ids = {x["id"] for x in r.json()["results"]}
    assert "1" in ids
    r = search_get(base, q="go AND (search OR index) NOT java")
    ids = {x["id"] for x in r.json()["results"]}
    assert ids == {"1"}


def test_search_scoring_ranking(server):
    base = server
    post_doc(
        base,
        {"id": "high", "title": "go go go", "body": "go go go go search", "tags": []},
    )
    post_doc(base, {"id": "low", "title": "go", "body": "search", "tags": []})
    post_doc(base, {"id": "mid", "title": "go go", "body": "go search", "tags": []})
    r = search_get(base, q="go")
    assert r.status_code == 200
    j = r.json()
    assert j["total"] == 3
    results = j["results"]
    assert results[0]["id"] == "high"
    scores = [x["score"] for x in results]
    assert scores == sorted(scores, reverse=True)
    assert scores[0] > scores[-1]


def test_search_tag_filtering(server):
    base = server
    post_doc(
        base,
        {
            "id": "1",
            "title": "Go Search",
            "body": "Go search",
            "tags": ["go", "search"],
        },
    )
    post_doc(
        base, {"id": "2", "title": "Go Only", "body": "Go programming", "tags": ["go"]}
    )
    post_doc(
        base,
        {
            "id": "3",
            "title": "Search Only",
            "body": "Search engine",
            "tags": ["search"],
        },
    )
    r = search_get(base, q="", tags="go")
    assert r.status_code == 200
    assert r.json()["total"] == 2
    ids = {x["id"] for x in r.json()["results"]}
    assert ids == {"1", "2"}
    r = search_get(base, q="", tags="go,search")
    assert r.json()["total"] == 1
    assert r.json()["results"][0]["id"] == "1"
    r = search_get(base, q="search", tags="go")
    assert r.json()["total"] == 1
    r = search_post(base, {"query": "search", "tags": ["go"]})
    assert r.json()["total"] == 1
    r = search_get(base, q="", tags="Go")
    assert r.json()["total"] == 2


def test_search_pagination(server):
    base = server
    for i in range(5):
        post_doc(
            base,
            {
                "id": f"d{i}",
                "title": f"doc {i} search",
                "body": f"Search content {i}",
                "tags": [],
            },
        )
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
    ids_page1 = {x["id"] for x in r.json()["results"]}
    ids_page2 = {x["id"] for x in r2.json()["results"]}
    assert ids_page1.isdisjoint(ids_page2)


def test_search_empty_query_returns_all(server):
    base = server
    post_doc(base, {"id": "a", "title": "first", "body": "alpha", "tags": ["t1"]})
    post_doc(base, {"id": "b", "title": "second", "body": "beta", "tags": ["t2"]})
    r = search_get(base, q="")
    assert r.status_code == 200
    assert r.json()["total"] == 2
    r = search_get(base)
    assert r.status_code == 200
    assert r.json()["total"] == 2


def test_search_result_format(server):
    base = server
    post_doc(
        base,
        {
            "id": "fmt1",
            "title": "Format Test",
            "body": "Check format fields",
            "tags": ["fmt"],
        },
    )
    r = search_get(base, q="format")
    assert r.status_code == 200
    j = r.json()
    assert "total" in j
    assert "results" in j
    assert "aggregations" in j
    assert isinstance(j["results"], list)
    if len(j["results"]) > 0:
        first = j["results"][0]
        assert (
            "id" in first and "score" in first and "title" in first and "tags" in first
        )


def test_search_bool_or_precedence(server):
    base = server
    post_doc(base, {"id": "1", "title": "a b", "body": "a b", "tags": []})
    post_doc(base, {"id": "2", "title": "b c", "body": "b c", "tags": []})
    post_doc(base, {"id": "3", "title": "a c", "body": "a c", "tags": []})
    r = search_get(base, q="a OR b AND c")
    assert r.status_code == 200
    ids = {x["id"] for x in r.json()["results"]}
    assert "1" in ids and "2" in ids


# ---------------------------------------------------------------------------
# HARD tests — phrase, field, prefix, fuzzy, boost, BM25, highlight, agg, bulk, stats, recovery
# ---------------------------------------------------------------------------


def test_phrase_query(server):
    base = server
    post_doc(
        base,
        {
            "id": "p1",
            "title": "search engine",
            "body": "build a search engine",
            "tags": [],
        },
    )
    post_doc(
        base,
        {
            "id": "p2",
            "title": "search big engine",
            "body": "search and big engine separated",
            "tags": [],
        },
    )
    r = search_get(base, q='"search engine"')
    assert r.status_code == 200
    ids = {x["id"] for x in r.json()["results"]}
    assert ids == {"p1"}, f"phrase should only match p1, got {ids}"


def test_phrase_non_adjacent_should_not_match(server):
    base = server
    post_doc(base, {"id": "pa1", "title": "go search engine", "body": "", "tags": []})
    post_doc(base, {"id": "pa2", "title": "engine search go", "body": "", "tags": []})
    r = search_get(base, q='"search engine"')
    ids = {x["id"] for x in r.json()["results"]}
    assert "pa1" in ids
    assert "pa2" not in ids


def test_field_specific_title_only(server):
    base = server
    post_doc(
        base, {"id": "f1", "title": "go language", "body": "java language", "tags": []}
    )
    post_doc(
        base, {"id": "f2", "title": "java language", "body": "go language", "tags": []}
    )
    r = search_get(base, q="title:go")
    ids = {x["id"] for x in r.json()["results"]}
    assert ids == {"f1"}
    r = search_get(base, q="body:go")
    ids = {x["id"] for x in r.json()["results"]}
    assert ids == {"f2"}


def test_field_specific_tags_via_query(server):
    base = server
    post_doc(base, {"id": "t1", "title": "first", "body": "first", "tags": ["go"]})
    post_doc(base, {"id": "t2", "title": "second", "body": "second", "tags": ["java"]})
    r = search_get(base, q="tags:go")
    ids = {x["id"] for x in r.json()["results"]}
    assert ids == {"t1"}


def test_prefix_query(server):
    base = server
    post_doc(base, {"id": "pr1", "title": "search", "body": "", "tags": []})
    post_doc(base, {"id": "pr2", "title": "seal", "body": "", "tags": []})
    post_doc(base, {"id": "pr3", "title": "something", "body": "", "tags": []})
    r = search_get(base, q="sea*")
    ids = {x["id"] for x in r.json()["results"]}
    assert ids == {"pr1", "pr2"}, f"prefix sea* should match pr1 pr2, got {ids}"
    # field-specific prefix
    post_doc(base, {"id": "pr4", "title": "search", "body": "other", "tags": []})
    r = search_get(base, q="title:sea*")
    assert "pr1" in {x["id"] for x in r.json()["results"]}


def test_fuzzy_query(server):
    base = server
    post_doc(base, {"id": "fz1", "title": "search", "body": "", "tags": []})
    post_doc(base, {"id": "fz2", "title": "other", "body": "", "tags": []})
    r = search_get(base, q="sarch~")
    ids = {x["id"] for x in r.json()["results"]}
    assert "fz1" in ids, f"fuzzy sarch~ should match search, got {ids}"


def test_boost_scoring(server):
    base = server
    post_doc(base, {"id": "b1", "title": "go", "body": "", "tags": []})
    post_doc(base, {"id": "b2", "title": "search", "body": "", "tags": []})
    r = search_get(base, q="go^2 search")
    assert r.status_code == 200
    results = r.json()["results"]
    assert results[0]["id"] == "b1", (
        f"boost should rank b1 first, got {[x['id'] for x in results]}"
    )


def test_bm25_exact_scoring(server):
    base = server
    post_doc(base, {"id": "bm1", "title": "go go go", "body": "", "tags": []})
    post_doc(base, {"id": "bm2", "title": "go", "body": "", "tags": []})
    r = search_get(base, q="go")
    assert r.status_code == 200
    results = {x["id"]: x["score"] for x in r.json()["results"]}
    # Expected BM25 as in spec: idf=log(1+(N-df+0.5)/(df+0.5)), N=2 df=2
    idf = math.log(1 + 0.5 / 2.5)
    # avg title len = (3+1)/2=2
    score1 = idf * (3 * 2.2) / (3 + 1.2 * (0.25 + 0.75 * 3 / 2))
    score2 = idf * (1 * 2.2) / (1 + 1.2 * (0.25 + 0.75 * 1 / 2))
    assert abs(results["bm1"] - score1) < 0.05, (
        f"BM25 mismatch bm1 expected {score1} got {results['bm1']}"
    )
    assert abs(results["bm2"] - score2) < 0.05
    assert results["bm1"] > results["bm2"]


def test_highlight(server):
    base = server
    post_doc(
        base,
        {
            "id": "hl1",
            "title": "Go Search Engine",
            "body": "build search engine",
            "tags": [],
        },
    )
    r = search_get(base, q="search", highlight=True)
    assert r.status_code == 200
    assert len(r.json()["results"]) == 1
    res = r.json()["results"][0]
    assert "highlight" in res, "highlight field missing"
    # must contain <em>
    hl_str = json.dumps(res["highlight"])
    assert "<em>" in hl_str and "</em>" in hl_str, (
        f"highlight should contain <em>, got {hl_str}"
    )


def test_aggregations(server):
    base = server
    post_doc(base, {"id": "ag1", "title": "a", "body": "a", "tags": ["go", "search"]})
    post_doc(base, {"id": "ag2", "title": "b", "body": "b", "tags": ["go"]})
    post_doc(base, {"id": "ag3", "title": "c", "body": "c", "tags": ["python"]})
    r = search_get(base, q="", limit=10)
    assert r.status_code == 200
    agg = r.json().get("aggregations", {}).get("tags", {})
    # go appears 2, search 1, python 1
    assert agg.get("go") == 2
    assert agg.get("search") == 1
    assert agg.get("python") == 1
    # filtered agg
    r = search_get(base, q="a")
    agg = r.json().get("aggregations", {}).get("tags", {})
    # only ag1 matches "a"
    assert agg.get("go") == 1


def test_bulk_api(server):
    base = server
    ndjson = '{"index":{"_id":"bulk1"}}\n{"id":"bulk1","title":"bulk doc 1","body":"bulk","tags":["bulk"]}\n{"index":{"_id":"bulk2"}}\n{"id":"bulk2","title":"bulk doc 2","body":"bulk","tags":[]}\n'
    resp = requests.post(
        f"{base}/bulk",
        data=ndjson,
        headers={"Content-Type": "application/x-ndjson"},
        timeout=5,
    )
    assert resp.status_code == 200, resp.text
    j = resp.json()
    assert j["errors"] is False
    assert len(j["items"]) == 2
    # verify docs exist
    assert get_doc(base, "bulk1").status_code == 200
    r = search_get(base, q="bulk")
    assert r.json()["total"] == 2
    # simplified bulk without action lines
    ndjson2 = '{"id":"bulk3","title":"bulk3","body":"bulk","tags":[]}\n{"id":"bulk4","title":"bulk4","body":"bulk","tags":[]}\n'
    resp = requests.post(
        f"{base}/bulk",
        data=ndjson2,
        headers={"Content-Type": "application/x-ndjson"},
        timeout=5,
    )
    assert resp.status_code == 200
    r = search_get(base, q="bulk")
    assert r.json()["total"] == 4


def test_stats_endpoint(server):
    base = server
    post_doc(base, {"id": "s1", "title": "go search", "body": "engine", "tags": []})
    post_doc(base, {"id": "s2", "title": "java search", "body": "lucene", "tags": []})
    r = requests.get(f"{base}/stats", timeout=5)
    assert r.status_code == 200, r.text
    j = r.json()
    assert "docs" in j and "terms" in j and "avgdl" in j
    assert j["docs"] == 2
    assert j["terms"] > 0
    assert isinstance(j["avgdl"], (int, float))


def test_complex_bool_field_phrase_prefix(server):
    base = server
    post_doc(
        base,
        {
            "id": "c1",
            "title": "Go Search Engine",
            "body": "fast engine in Go",
            "tags": ["go", "search"],
        },
    )
    post_doc(
        base,
        {"id": "c2", "title": "Java Search", "body": "Lucene Java", "tags": ["java"]},
    )
    post_doc(
        base, {"id": "c3", "title": "Go Engine", "body": "Go engine", "tags": ["go"]}
    )
    # title phrase OR (body:engine AND tags:go)
    r = search_get(base, q='title:"go search" OR (body:engine AND tags:go)')
    assert r.status_code == 200
    ids = {x["id"] for x in r.json()["results"]}
    assert "c1" in ids, f"complex query should include c1, got {ids}"
    # prefix + fuzzy + field
    post_doc(base, {"id": "c4", "title": "searching", "body": "", "tags": []})
    r = search_get(base, q="search*")
    assert "c4" in {x["id"] for x in r.json()["results"]}


def test_invalid_field_should_400(server):
    base = server
    r = search_get(base, q="unknownfield:go")
    assert r.status_code == 400, (
        f"unknown field should 400, got {r.status_code} {r.text}"
    )


def test_invalid_boost_should_400(server):
    base = server
    r = search_get(base, q="go^abc")
    assert r.status_code == 400, f"invalid boost should 400, got {r.status_code}"


def test_persistence_and_recovery():
    port = find_free_port()
    env = {**os.environ, "PORT": str(port)}
    if os.path.exists(DATA_DIR):
        shutil.rmtree(DATA_DIR, ignore_errors=True)
    proc = subprocess.Popen(
        [BIN], cwd=APP, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    assert wait_for_server(port, timeout=15), "server first start failed"
    base = f"http://127.0.0.1:{port}"
    try:
        r = requests.post(
            f"{base}/documents",
            json={
                "id": "persist1",
                "title": "persistent",
                "body": "should persist",
                "tags": ["persist"],
            },
            timeout=5,
        )
        assert r.status_code in (200, 201)
        time.sleep(0.5)
        assert os.path.exists(os.path.join(DATA_DIR, "index.json")), (
            "persistence file not created"
        )
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        time.sleep(0.5)
        port2 = port
        env2 = {**os.environ, "PORT": str(port2)}
        proc2 = subprocess.Popen(
            [BIN], cwd=APP, env=env2, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        assert wait_for_server(port2, timeout=15), "server second start failed"
        base2 = f"http://127.0.0.1:{port2}"
        r = requests.get(f"{base2}/documents/persist1", timeout=5)
        assert r.status_code == 200
        r = requests.get(f"{base2}/search", params={"q": "persistent"}, timeout=5)
        assert r.status_code == 200 and r.json()["total"] == 1
        proc2.terminate()
        try:
            proc2.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc2.kill()
    finally:
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


def test_persistence_recovery_from_truncated():
    port = find_free_port()
    env = {**os.environ, "PORT": str(port)}
    if os.path.exists(DATA_DIR):
        shutil.rmtree(DATA_DIR, ignore_errors=True)
    proc = subprocess.Popen(
        [BIN], cwd=APP, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    assert wait_for_server(port, timeout=15)
    base = f"http://127.0.0.1:{port}"
    try:
        requests.post(
            f"{base}/documents",
            json={"id": "rec1", "title": "recover", "body": "recover me", "tags": []},
            timeout=5,
        )
        time.sleep(0.5)
        data_file = os.path.join(DATA_DIR, "index.json")
        # append garbage to simulate crash truncation
        with open(data_file, "ab") as f:
            f.write(b'{"truncated":')
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        time.sleep(0.5)
        port2 = find_free_port()
        env2 = {**os.environ, "PORT": str(port2)}
        proc2 = subprocess.Popen(
            [BIN], cwd=APP, env=env2, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        # must not crash, should start
        assert wait_for_server(port2, timeout=15), (
            "server should recover from truncated file and start"
        )
        base2 = f"http://127.0.0.1:{port2}"
        r = requests.get(f"{base2}/search", timeout=5)
        assert r.status_code == 200
        proc2.terminate()
        proc2.wait(timeout=5)
    finally:
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


def test_concurrency(server):
    base = server
    errors = []

    def index_docs(start, count):
        for i in range(count):
            doc_id = f"c{start}_{i}"
            try:
                r = requests.post(
                    f"{base}/documents",
                    json={
                        "id": doc_id,
                        "title": f"concurrent {doc_id}",
                        "body": "stress test Go search engine concurrent",
                        "tags": ["concurrent"],
                    },
                    timeout=5,
                )
                if r.status_code not in (200, 201):
                    errors.append(f"index {doc_id} failed {r.status_code}")
            except Exception as e:
                errors.append(str(e))

    def search_loop():
        for _ in range(20):
            try:
                r = requests.get(
                    f"{base}/search",
                    params={"q": "concurrent", "tags": "concurrent"},
                    timeout=5,
                )
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
    r = requests.get(
        f"{base}/search", params={"q": "concurrent", "limit": "100"}, timeout=5
    )
    assert r.status_code == 200 and r.json()["total"] >= 50


def test_invalid_inputs(server):
    base = server
    r = requests.post(f"{base}/documents", json={"title": "no id"}, timeout=5)
    assert r.status_code == 400
    r = requests.post(
        f"{base}/documents",
        data="not json",
        headers={"Content-Type": "application/json"},
        timeout=5,
    )
    assert r.status_code == 400
    r = requests.get(f"{base}/documents/doesnotexist", timeout=5)
    assert r.status_code == 404
    r = requests.get(f"{base}/search", params={"limit": "-1"}, timeout=5)
    assert r.status_code == 400
    r = requests.get(f"{base}/search", params={"offset": "-5"}, timeout=5)
    assert r.status_code == 400
    r = requests.post(
        f"{base}/search",
        data="{{{",
        headers={"Content-Type": "application/json"},
        timeout=5,
    )
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# Additional coverage for qualitative Medium gaps
# ---------------------------------------------------------------------------


def test_fuzzy_distance_2_should_not_match(server):
    base = server
    post_doc(base, {"id": "fz1", "title": "search", "body": "", "tags": []})
    # distance 2+ should NOT match (sarchhh vs search distance >1)
    r = search_get(base, q="sarchhh~")
    assert r.status_code == 200
    assert r.json()["total"] == 0, f"fuzzy distance 2+ should not match, got {r.json()}"
    # distance 2 also not match
    r = search_get(base, q="seaarch~")
    # seaarch vs search distance 1? Actually seaarch has extra a, distance 1? Let's use clearly distance 2: "sxxrch~"
    r = search_get(base, q="sxxrch~")
    assert r.json()["total"] == 0


def test_bulk_action_id_precedence(server):
    base = server
    # action _id should override doc's own id
    ndjson = '{"index":{"_id":"real_id"}}\n{"id":"fake_id","title":"bulk precedence","body":"test","tags":[]}\n'
    resp = requests.post(
        f"{base}/bulk",
        data=ndjson,
        headers={"Content-Type": "application/x-ndjson"},
        timeout=5,
    )
    assert resp.status_code == 200
    # fake_id should NOT exist, real_id should
    assert get_doc(base, "fake_id").status_code == 404
    assert get_doc(base, "real_id").status_code == 200


def test_data_file_env_override():
    # Tests DATA_FILE env var is respected (spec §4)
    custom_dir = "/tmp/custom_data_test"
    custom_file = os.path.join(custom_dir, "custom.json")
    if os.path.exists(custom_dir):
        shutil.rmtree(custom_dir, ignore_errors=True)
    os.makedirs(custom_dir, exist_ok=True)

    port = find_free_port()
    env = {**os.environ, "PORT": str(port), "DATA_FILE": custom_file}
    if os.path.exists(DATA_DIR):
        shutil.rmtree(DATA_DIR, ignore_errors=True)
    proc = subprocess.Popen(
        [BIN], cwd=APP, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    assert wait_for_server(port, timeout=15), (
        "server with custom DATA_FILE failed to start"
    )
    base = f"http://127.0.0.1:{port}"
    try:
        r = requests.post(
            f"{base}/documents",
            json={"id": "custom1", "title": "custom file", "body": "test", "tags": []},
            timeout=5,
        )
        assert r.status_code in (200, 201)
        time.sleep(0.5)
        assert os.path.exists(custom_file), (
            f"custom DATA_FILE not created at {custom_file}"
        )
        # default file should NOT exist
        assert not os.path.exists(os.path.join(DATA_DIR, "index.json"))
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        if os.path.exists(custom_dir):
            shutil.rmtree(custom_dir, ignore_errors=True)
        if os.path.exists(DATA_DIR):
            shutil.rmtree(DATA_DIR, ignore_errors=True)


def test_prefix_fuzzy_highlight(server):
    base = server
    post_doc(
        base, {"id": "hlp1", "title": "searching", "body": "search engine", "tags": []}
    )
    # prefix query with highlight should highlight actual indexed term, not query prefix
    r = search_get(base, q="sea*", highlight=True)
    assert r.status_code == 200 and r.json()["total"] >= 1
    found = False
    for res in r.json()["results"]:
        if "highlight" in res:
            hl = json.dumps(res["highlight"])
            if "<em>" in hl:
                found = True
                break
    assert found, f"prefix highlight should contain <em>, got {r.json()}"
    # fuzzy highlight
    r = search_get(base, q="sarch~", highlight=True)
    assert r.status_code == 200
    found = any(
        "<em>" in json.dumps(x.get("highlight", {})) for x in r.json()["results"]
    )
    assert found, "fuzzy highlight should contain <em>"
