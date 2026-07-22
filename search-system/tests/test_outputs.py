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
BIN = "/tmp/codimango/search-server"
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
    os.makedirs("/tmp/codimango", exist_ok=True)
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
    # NOVEL BM25F non-standard: k1=1.65, b=0.68, idf=log((N+1)/(df+0.5))+1, title weight 2.0
    N = 2
    df = 2
    idf = math.log((N + 1) / (df + 0.5)) + 1  # log(3/2.5)+1
    avg = 2.0  # (3+1)/2
    # title scores
    score_title_bm1 = idf * (3 * (1.65 + 1)) / (3 + 1.65 * (1 - 0.68 + 0.68 * 3 / avg))
    score_title_bm2 = idf * (1 * (1.65 + 1)) / (1 + 1.65 * (1 - 0.68 + 0.68 * 1 / avg))
    # default field = title*2 + body (body 0)
    score1 = score_title_bm1 * 2.0
    score2 = score_title_bm2 * 2.0
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
    # query only go -> only Go should be wrapped, not Search or Engine (vacuous check fix)
    r = search_get(base, q="go", highlight=True)
    assert r.status_code == 200 and len(r.json()["results"]) == 1
    res = r.json()["results"][0]
    assert "highlight" in res
    hl_title = res["highlight"].get("title", "")
    assert "<em>Go</em>" in hl_title, f"expected <em>Go</em> in title, got {hl_title}"
    assert "<em>Search</em>" not in hl_title and "<em>Engine</em>" not in hl_title, (
        f"only matched token should be wrapped, got {hl_title}"
    )

    r = search_get(base, q="search", highlight=True)
    res = r.json()["results"][0]
    hl_title = res["highlight"].get("title", "")
    assert "<em>Search</em>" in hl_title
    assert "<em>Go</em>" not in hl_title

    # phrase go search -> Go and Search wrapped, Engine not
    r = search_get(base, q='"go search"', highlight=True)
    res = r.json()["results"][0]
    hl_title = res["highlight"].get("title", "")
    assert "<em>Go</em>" in hl_title and "<em>Search</em>" in hl_title
    assert "<em>Engine</em>" not in hl_title
    assert hl_title.count("<em>") == 2, (
        f"phrase should have exactly 2 em spans, got {hl_title}"
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
    post_doc(
        base,
        {
            "id": "s1",
            "title": "go search",
            "body": "engine",
            "tags": [],
            "namespace": "ns-a",
        },
    )
    post_doc(
        base,
        {
            "id": "s2",
            "title": "java search",
            "body": "lucene",
            "tags": [],
            "namespace": "ns-b",
        },
    )
    r = requests.get(f"{base}/stats", timeout=5)
    assert r.status_code == 200, r.text
    j = r.json()
    assert "docs" in j and "terms" in j and "avgdl" in j and "namespaces" in j, (
        f"stats should include docs,terms,avgdl,namespaces, got {j}"
    )
    assert j["docs"] == 2
    assert j["terms"] > 0
    assert isinstance(j["avgdl"], (int, float))
    assert j["namespaces"] == 2, (
        f"expected 2 distinct namespaces, got {j['namespaces']}"
    )


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
    r = search_get(base, q="sea*", highlight=True)
    assert r.status_code == 200 and r.json()["total"] >= 1
    res = next((x for x in r.json()["results"] if x["id"] == "hlp1"), None)
    assert res is not None and "highlight" in res
    hl_title = res["highlight"].get("title", "")
    assert "<em>searching</em>" in hl_title.lower() or "<em>search" in hl_title.lower()
    hl_body = res["highlight"].get("body", "")
    if hl_body:
        assert "engine" in hl_body.lower()
        assert "<em>engine</em>" not in hl_body.lower(), (
            f"engine should NOT be wrapped for sea*, got {hl_body}"
        )

    r = search_get(base, q="sarch~", highlight=True)
    assert r.status_code == 200
    res = next((x for x in r.json()["results"] if x["id"] == "hlp1"), None)
    assert res is not None and "highlight" in res
    hl_body = res["highlight"].get("body", "")
    assert "<em>search</em>" in hl_body.lower()
    assert "<em>engine</em>" not in hl_body.lower()


# ---------------------------------------------------------------------------
# NOVEL tests — code-aware tokenization, namespace isolation, recency, NEAR, top_terms, WAL
# ---------------------------------------------------------------------------


def test_code_aware_camelcase_tokenization(server):
    base = server
    # GoSearchEngine should be split into go, search, engine
    post_doc(base, {"id": "cc1", "title": "GoSearchEngine", "body": "", "tags": []})
    post_doc(base, {"id": "cc2", "title": "Other", "body": "", "tags": []})
    r = search_get(base, q="go")
    assert r.status_code == 200
    ids = {x["id"] for x in r.json()["results"]}
    assert "cc1" in ids, f"code-aware: GoSearchEngine should match 'go', got {ids}"
    r = search_get(base, q="search")
    ids = {x["id"] for x in r.json()["results"]}
    assert "cc1" in ids
    r = search_get(base, q="engine")
    ids = {x["id"] for x in r.json()["results"]}
    assert "cc1" in ids
    # phrase after code-aware split
    r = search_get(base, q='"go search"')
    ids = {x["id"] for x in r.json()["results"]}
    assert "cc1" in ids, (
        f"phrase 'go search' should match GoSearchEngine via code-aware split, got {ids}"
    )


def test_namespace_isolation_via_param(server):
    base = server
    post_doc(
        base,
        {"id": "ns1", "title": "go doc", "body": "", "tags": [], "namespace": "team-a"},
    )
    post_doc(
        base,
        {"id": "ns2", "title": "go doc", "body": "", "tags": [], "namespace": "team-b"},
    )
    r = search_get(base, q="go", tags=None)
    # without namespace filter, both
    assert r.json()["total"] == 2
    # with namespace param
    r = requests.get(
        f"{base}/search", params={"q": "go", "namespace": "team-a"}, timeout=5
    )
    assert r.status_code == 200
    ids = {x["id"] for x in r.json()["results"]}
    assert ids == {"ns1"}
    # via field query namespace:team-b
    r = search_get(base, q="namespace:team-b")
    ids = {x["id"] for x in r.json()["results"]}
    assert ids == {"ns2"}


def test_namespace_isolation_via_header(server):
    base = server
    post_doc(
        base,
        {"id": "hns1", "title": "go", "body": "", "tags": [], "namespace": "team-a"},
    )
    post_doc(
        base,
        {"id": "hns2", "title": "go", "body": "", "tags": [], "namespace": "team-b"},
    )
    r = requests.get(
        f"{base}/search",
        params={"q": "go"},
        headers={"X-Namespace": "team-a"},
        timeout=5,
    )
    assert r.status_code == 200
    ids = {x["id"] for x in r.json()["results"]}
    assert ids == {"hns1"}
    # header overrides query param
    r = requests.get(
        f"{base}/search",
        params={"q": "go", "namespace": "team-b"},
        headers={"X-Namespace": "team-a"},
        timeout=5,
    )
    ids = {x["id"] for x in r.json()["results"]}
    assert ids == {"hns1"}


def test_recency_decay_ranking(server):
    base = server
    now = time.time()
    recent = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - 3600))  # 1 hour ago
    old = time.strftime(
        "%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - 7 * 24 * 3600)
    )  # 7 days ago
    post_doc(
        base,
        {
            "id": "recent",
            "title": "go search",
            "body": "",
            "tags": [],
            "created_at": recent,
        },
    )
    post_doc(
        base,
        {"id": "old", "title": "go search", "body": "", "tags": [], "created_at": old},
    )
    r = search_get(base, q="go")
    assert r.status_code == 200
    results = r.json()["results"]
    assert results[0]["id"] == "recent", (
        f"recency should rank recent first, got {[x['id'] for x in results]}"
    )
    assert results[0]["score"] > results[1]["score"]


def test_recency_exact_constants(server):
    base = server
    # Pin exact constants 0.5 and 168 from spec: recencyFactor = 1 + 0.5*exp(-ageHours/168)
    # Two docs with same BM25 content but different ages: now (0h) and exactly 168h (1 week)
    now = time.time()
    now_str = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now))
    week_ago = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - 168 * 3600))
    post_doc(
        base,
        {"id": "nowDoc", "title": "go", "body": "", "tags": [], "created_at": now_str},
    )
    post_doc(
        base,
        {
            "id": "weekDoc",
            "title": "go",
            "body": "",
            "tags": [],
            "created_at": week_ago,
        },
    )
    r = search_get(base, q="go")
    assert r.status_code == 200 and r.json()["total"] == 2
    scores = {x["id"]: x["score"] for x in r.json()["results"]}
    # Both have same tf/len so BM25 equal, ratio should be recencyNow / recencyWeek
    # recencyNow = 1 + 0.5*exp(0)=1.5, recencyWeek=1+0.5*exp(-1)=1+0.1839=1.1839, ratio=1.5/1.1839≈1.267
    # Compute expected ratio
    expected_ratio = (1 + 0.5 * math.exp(0)) / (1 + 0.5 * math.exp(-1))
    actual_ratio = scores["nowDoc"] / scores["weekDoc"] if scores["weekDoc"] != 0 else 0
    assert abs(actual_ratio - expected_ratio) < 0.05, (
        f"recency exact constants 0.5 and 168 not pinned, expected ratio {expected_ratio} got {actual_ratio} scores {scores}"
    )
    # also test that future date is treated as age 0 => factor 1.5
    future = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now + 24 * 3600))
    post_doc(
        base,
        {
            "id": "futureDoc",
            "title": "go",
            "body": "",
            "tags": [],
            "created_at": future,
        },
    )
    r = search_get(base, q="go")
    scores = {x["id"]: x["score"] for x in r.json()["results"]}
    # future should have same score as nowDoc (age 0)
    assert abs(scores["futureDoc"] - scores["nowDoc"]) < 0.05, (
        f"future created_at should be age 0 factor 1.5, got {scores}"
    )


def test_near_operator(server):
    base = server
    post_doc(base, {"id": "near1", "title": "go search engine", "body": "", "tags": []})
    post_doc(
        base, {"id": "near2", "title": "go big big search", "body": "", "tags": []}
    )
    # go within 1 of search should only match near1 (adjacent? go at pos0, search pos1 => distance 1)
    r = search_get(base, q="go NEAR/1 search")
    assert r.status_code == 200
    ids = {x["id"] for x in r.json()["results"]}
    assert "near1" in ids
    assert "near2" not in ids, (
        f"NEAR/1 should not match near2 with distance 3, got {ids}"
    )
    # NEAR/3 should match both
    r = search_get(base, q="go NEAR/3 search")
    ids = {x["id"] for x in r.json()["results"]}
    assert "near1" in ids and "near2" in ids


def test_top_terms_aggregation(server):
    base = server
    post_doc(base, {"id": "tt1", "title": "go go search", "body": "engine", "tags": []})
    post_doc(base, {"id": "tt2", "title": "go search", "body": "go", "tags": []})
    r = search_get(base, q="go")
    assert r.status_code == 200
    agg = r.json().get("aggregations", {})
    assert "top_terms" in agg, f"top_terms missing, got {agg}"
    top = agg["top_terms"]
    assert isinstance(top, list) and len(top) > 0
    # go should be top term
    assert top[0]["term"] == "go"
    assert "count" in top[0]


def test_wal_replay():
    # WAL: delete index.json but keep wal.log, restart should replay
    port = find_free_port()
    custom_dir = "/tmp/wal_test"
    custom_file = os.path.join(custom_dir, "index.json")
    if os.path.exists(custom_dir):
        shutil.rmtree(custom_dir, ignore_errors=True)
    os.makedirs(custom_dir, exist_ok=True)
    env = {**os.environ, "PORT": str(port), "DATA_FILE": custom_file}
    if os.path.exists(DATA_DIR):
        shutil.rmtree(DATA_DIR, ignore_errors=True)
    proc = subprocess.Popen(
        [BIN], cwd=APP, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    assert wait_for_server(port, timeout=15)
    base = f"http://127.0.0.1:{port}"
    try:
        r = requests.post(
            f"{base}/documents",
            json={"id": "wal1", "title": "wal replay", "body": "test", "tags": []},
            timeout=5,
        )
        assert r.status_code in (200, 201)
        time.sleep(0.5)
        wal_file = os.path.join(custom_dir, "wal.log")
        assert os.path.exists(wal_file), "wal.log not created"
        # delete index.json, keep WAL, restart
        os.remove(custom_file)
        assert not os.path.exists(custom_file)
        proc.terminate()
        proc.wait(timeout=5)
        time.sleep(0.5)
        port2 = find_free_port()
        env2 = {**os.environ, "PORT": str(port2), "DATA_FILE": custom_file}
        proc2 = subprocess.Popen(
            [BIN], cwd=APP, env=env2, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        assert wait_for_server(port2, timeout=15), "server should start and replay WAL"
        base2 = f"http://127.0.0.1:{port2}"
        r = requests.get(f"{base2}/documents/wal1", timeout=5)
        assert r.status_code == 200, (
            f"WAL replay failed, expected wal1 to be recovered, got {r.status_code} {r.text}"
        )
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
        if os.path.exists(custom_dir):
            shutil.rmtree(custom_dir, ignore_errors=True)
        if os.path.exists(DATA_DIR):
            shutil.rmtree(DATA_DIR, ignore_errors=True)


def test_fuzzy_with_distance(server):
    base = server
    post_doc(base, {"id": "fzd1", "title": "search", "body": "", "tags": []})
    r = search_get(base, q="sarch~1")
    assert r.json()["total"] == 1
    r = search_get(base, q="sarch~2")
    assert r.json()["total"] == 1
    r = search_get(base, q="sarch~0")
    assert r.json()["total"] == 0


def test_get_doc_includes_namespace_created_at(server):
    base = server
    ts = "2026-07-21T10:00:00Z"
    post_doc(
        base,
        {
            "id": "nsca1",
            "title": "test",
            "body": "test",
            "tags": [],
            "namespace": "team-x",
            "created_at": ts,
        },
    )
    r = get_doc(base, "nsca1")
    assert r.status_code == 200, r.text
    j = r.json()
    assert "namespace" in j and j["namespace"] == "team-x", (
        f"GET doc should include namespace, got {j}"
    )
    assert "created_at" in j and j["created_at"] == ts, (
        f"GET doc should include created_at, got {j}"
    )


def test_invalid_near_should_400(server):
    base = server
    r = search_get(base, q="go NEAR/abc search")
    assert r.status_code == 400, (
        f"invalid NEAR/abc should 400, got {r.status_code} {r.text}"
    )
    r = search_get(base, q="go NEAR/-1 search")
    assert r.status_code == 400


def test_wal_checksum_skip():
    # WAL checksum verification: must enforce CRC32-IEEE algorithm, not any self-consistent hash
    import binascii

    port = find_free_port()
    custom_dir = "/tmp/wal_checksum_test"
    custom_file = os.path.join(custom_dir, "index.json")
    if os.path.exists(custom_dir):
        shutil.rmtree(custom_dir, ignore_errors=True)
    os.makedirs(custom_dir, exist_ok=True)
    env = {**os.environ, "PORT": str(port), "DATA_FILE": custom_file}
    if os.path.exists(DATA_DIR):
        shutil.rmtree(DATA_DIR, ignore_errors=True)
    proc = subprocess.Popen(
        [BIN], cwd=APP, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    assert wait_for_server(port, timeout=15)
    base = f"http://127.0.0.1:{port}"
    try:
        r = requests.post(
            f"{base}/documents",
            json={"id": "good1", "title": "good", "body": "", "tags": []},
            timeout=5,
        )
        assert r.status_code in (200, 201)
        time.sleep(0.5)
        wal_file = os.path.join(custom_dir, "wal.log")
        assert os.path.exists(wal_file)

        # Craft a valid doc with correct CRC32-IEEE checksum via Python (should replay)
        doc_valid = {
            "id": "crc_valid",
            "title": "crc valid doc",
            "body": "",
            "tags": [],
        }
        doc_json = json.dumps(doc_valid, separators=(",", ":")).encode()
        crc_val = binascii.crc32(doc_json) & 0xFFFFFFFF
        crc_hex = format(crc_val, "08x")
        with open(wal_file, "a") as f:
            entry = {
                "op": "index",
                "doc": doc_valid,
                "checksum": crc_hex,
                "ts": "2026-07-21T00:00:00Z",
            }
            f.write(json.dumps(entry) + "\n")

        # Craft doc with checksum of id only (wrong algorithm) — should be skipped (enforces CRC32 of doc, not id)
        doc_bad_algo = {"id": "bad_algo", "title": "bad algo", "body": "", "tags": []}
        bad_crc = format(
            binascii.crc32(doc_bad_algo["id"].encode()) & 0xFFFFFFFF, "08x"
        )  # CRC32 of id, not doc JSON
        with open(wal_file, "a") as f:
            entry = {
                "op": "index",
                "doc": doc_bad_algo,
                "checksum": bad_crc,
                "ts": "2026-07-21T00:00:00Z",
            }
            f.write(json.dumps(entry) + "\n")

        # Append corrupted entry with obviously wrong checksum and invalid json
        with open(wal_file, "a") as f:
            f.write(
                '{"op":"index","doc":{"id":"bad","title":"bad"},"checksum":"00000000","ts":"2026-07-21T00:00:00Z"}\n'
            )
            f.write('{"invalid json line\n')

        proc.terminate()
        proc.wait(timeout=5)
        time.sleep(0.5)
        port2 = find_free_port()
        env2 = {**os.environ, "PORT": str(port2), "DATA_FILE": custom_file}
        proc2 = subprocess.Popen(
            [BIN], cwd=APP, env=env2, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        assert wait_for_server(port2, timeout=15), (
            "server should start and skip corrupted WAL lines"
        )
        base2 = f"http://127.0.0.1:{port2}"
        r = requests.get(f"{base2}/documents/good1", timeout=5)
        assert r.status_code == 200
        r = requests.get(f"{base2}/documents/crc_valid", timeout=5)
        assert r.status_code == 200, (
            f"doc with correct CRC32 should be replayed, got {r.status_code}"
        )
        r = requests.get(f"{base2}/documents/bad", timeout=5)
        assert r.status_code == 404
        r = requests.get(f"{base2}/documents/bad_algo", timeout=5)
        assert r.status_code == 404, (
            f"doc with wrong algo (CRC32 of id not doc) should be skipped, got {r.status_code}"
        )
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
        if os.path.exists(custom_dir):
            shutil.rmtree(custom_dir, ignore_errors=True)
        if os.path.exists(DATA_DIR):
            shutil.rmtree(DATA_DIR, ignore_errors=True)


def test_camelcase_acronym_highlighting(server):
    base = server
    post_doc(
        base,
        {
            "id": "cc_hl1",
            "title": "GoSearchEngine",
            "body": "Implements SearchEngine",
            "tags": [],
        },
    )
    # go should wrap only Go sub-token, preserving case Go, not Search/Engine
    r = search_get(base, q="go", highlight=True)
    assert r.status_code == 200 and r.json()["total"] == 1
    hl_title = r.json()["results"][0].get("highlight", {}).get("title", "")
    assert "<em>Go</em>" in hl_title, (
        f"expected <em>Go</em> preserving case, got {hl_title}"
    )
    assert "<em>Search</em>" not in hl_title and "<em>Engine</em>" not in hl_title, (
        f"only Go should be wrapped for query go, got {hl_title}"
    )

    # search query wraps Search only
    r = search_get(base, q="search", highlight=True)
    hl_title = r.json()["results"][0].get("highlight", {}).get("title", "")
    assert "<em>Search</em>" in hl_title
    assert "<em>Go</em>" not in hl_title

    # phrase go search wraps Go and Search, not Engine, preserves case and count 2
    r = search_get(base, q='"go search"', highlight=True)
    hl_title = r.json()["results"][0].get("highlight", {}).get("title", "")
    assert "<em>Go</em>" in hl_title and "<em>Search</em>" in hl_title
    assert "<em>Engine</em>" not in hl_title
    assert hl_title.count("<em>") == 2, (
        f"phrase should wrap exactly 2 sub-tokens, got {hl_title}"
    )

    # acronym IOError
    post_doc(base, {"id": "ac1", "title": "IOError occurred", "body": "", "tags": []})
    r = search_get(base, q="io", highlight=True)
    assert r.json()["total"] >= 1
    res_ac1 = next((x for x in r.json()["results"] if x["id"] == "ac1"), None)
    assert res_ac1 is not None and "highlight" in res_ac1
    assert "<em>IO</em>" in res_ac1["highlight"].get("title", ""), (
        f"IOError should highlight IO preserving case, got {res_ac1['highlight']}"
    )

    post_doc(
        base, {"id": "ac2", "title": "HTTPRequest handler", "body": "", "tags": []}
    )
    r = search_get(base, q="http", highlight=True)
    assert "ac2" in {x["id"] for x in r.json()["results"]}
    res_ac2 = next((x for x in r.json()["results"] if x["id"] == "ac2"), None)
    assert "<em>HTTP</em>" in res_ac2.get("highlight", {}).get("title", "")


def test_namespace_stats_case_insensitive(server):
    base = server
    post_doc(
        base,
        {"id": "nsci1", "title": "a", "body": "", "tags": [], "namespace": "Team-A"},
    )
    post_doc(
        base,
        {"id": "nsci2", "title": "b", "body": "", "tags": [], "namespace": "team-a"},
    )
    post_doc(
        base,
        {"id": "nsci3", "title": "c", "body": "", "tags": [], "namespace": "TEAM-B"},
    )
    r = requests.get(f"{base}/stats", timeout=5)
    assert r.status_code == 200
    j = r.json()
    assert j["docs"] == 3
    assert j["namespaces"] == 2, (
        f"Team-A and team-a should be one namespace, got {j['namespaces']}"
    )


def test_namespace_filtered_aggregations(server):
    base = server
    post_doc(
        base,
        {
            "id": "nfa1",
            "title": "go",
            "body": "",
            "tags": ["go"],
            "namespace": "team-a",
        },
    )
    post_doc(
        base,
        {
            "id": "nfa2",
            "title": "go",
            "body": "",
            "tags": ["java"],
            "namespace": "team-b",
        },
    )
    post_doc(
        base,
        {
            "id": "nfa3",
            "title": "go",
            "body": "",
            "tags": ["go", "java"],
            "namespace": "team-a",
        },
    )
    r = requests.get(
        f"{base}/search", params={"q": "go", "namespace": "team-a"}, timeout=5
    )
    assert r.status_code == 200
    j = r.json()
    assert j["total"] == 2
    assert j.get("aggregations", {}).get("tags", {}).get("go") == 2
    assert j.get("aggregations", {}).get("tags", {}).get("java") == 1
    assert j.get("aggregations", {}).get("namespaces", {}).get("team-a") == 2
    assert "team-b" not in j.get("aggregations", {}).get("namespaces", {})
    r = requests.get(
        f"{base}/search",
        params={"q": "go"},
        headers={"X-Namespace": "team-b"},
        timeout=5,
    )
    assert r.json()["total"] == 1


def test_wal_replay_delete_after_index_removal():
    port = find_free_port()
    custom_dir = "/tmp/wal_delete_test"
    custom_file = os.path.join(custom_dir, "index.json")
    if os.path.exists(custom_dir):
        shutil.rmtree(custom_dir, ignore_errors=True)
    os.makedirs(custom_dir, exist_ok=True)
    env = {**os.environ, "PORT": str(port), "DATA_FILE": custom_file}
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
            json={"id": "todel", "title": "to delete", "body": "", "tags": []},
            timeout=5,
        )
        requests.delete(f"{base}/documents/todel", timeout=5)
        time.sleep(0.5)
        wal_file = os.path.join(custom_dir, "wal.log")
        assert os.path.exists(wal_file)
        os.remove(custom_file)
        proc.terminate()
        proc.wait(timeout=5)
        time.sleep(0.5)
        port2 = find_free_port()
        env2 = {**os.environ, "PORT": str(port2), "DATA_FILE": custom_file}
        proc2 = subprocess.Popen(
            [BIN], cwd=APP, env=env2, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        assert wait_for_server(port2, timeout=15)
        base2 = f"http://127.0.0.1:{port2}"
        r = requests.get(f"{base2}/documents/todel", timeout=5)
        assert r.status_code == 404, (
            f"deleted doc should stay deleted after WAL replay, got {r.status_code}"
        )
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
        if os.path.exists(custom_dir):
            shutil.rmtree(custom_dir, ignore_errors=True)
        if os.path.exists(DATA_DIR):
            shutil.rmtree(DATA_DIR, ignore_errors=True)


def test_truncated_index_preserves_last_valid():
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
        for i in range(3):
            requests.post(
                f"{base}/documents",
                json={
                    "id": f"trunc{i}",
                    "title": f"valid doc {i}",
                    "body": "test",
                    "tags": [],
                },
                timeout=5,
            )
        time.sleep(0.5)
        data_file = os.path.join(DATA_DIR, "index.json")
        assert os.path.exists(data_file)
        with open(data_file, "r") as f:
            content = f.read()
        truncated = content[:-30]
        with open(data_file, "w") as f:
            f.write(truncated)
        proc.terminate()
        proc.wait(timeout=5)
        time.sleep(0.5)
        port2 = find_free_port()
        env2 = {**os.environ, "PORT": str(port2)}
        proc2 = subprocess.Popen(
            [BIN], cwd=APP, env=env2, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        assert wait_for_server(port2, timeout=15)
        base2 = f"http://127.0.0.1:{port2}"
        r = requests.get(f"{base2}/search", timeout=5)
        assert r.status_code == 200
        total = r.json().get("total", 0)
        assert total >= 1, (
            f"truncated recovery should preserve at least 1 doc, got {total}"
        )
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


def test_invalid_and_future_created_at(server):
    base = server
    post_doc(
        base,
        {
            "id": "inv1",
            "title": "go",
            "body": "",
            "tags": [],
            "created_at": "not-a-date",
        },
    )
    post_doc(
        base,
        {
            "id": "future1",
            "title": "go",
            "body": "",
            "tags": [],
            "created_at": "2099-12-31T23:59:59Z",
        },
    )
    post_doc(
        base,
        {
            "id": "valid1",
            "title": "go",
            "body": "",
            "tags": [],
            "created_at": "2026-07-21T10:00:00Z",
        },
    )
    r = search_get(base, q="go")
    assert r.status_code == 200
    assert r.json()["total"] == 3
    results = r.json()["results"]
    scores = {x["id"]: x["score"] for x in results}
    assert "inv1" in scores and "future1" in scores


def test_post_body_overrides_get_params(server):
    base = server
    post_doc(
        base,
        {
            "id": "over1",
            "title": "go search",
            "body": "",
            "tags": [],
            "namespace": "team-a",
        },
    )
    post_doc(
        base,
        {
            "id": "over2",
            "title": "java search",
            "body": "",
            "tags": [],
            "namespace": "team-b",
        },
    )
    r = requests.get(
        f"{base}/search", params={"q": "java", "namespace": "team-b"}, timeout=5
    )
    assert r.json()["total"] == 1
    resp = requests.post(
        f"{base}/search?q=java&namespace=team-b",
        json={"query": "go", "namespace": "team-a"},
        timeout=5,
    )
    assert resp.status_code == 200
    j = resp.json()
    assert j["total"] == 1 and j["results"][0]["id"] == "over1"


def test_exact_response_keys(server):
    base = server
    post_doc(
        base,
        {
            "id": "key1",
            "title": "go",
            "body": "search",
            "tags": ["go"],
            "namespace": "default",
        },
    )
    r = search_get(base, q="go")
    assert r.status_code == 200
    j = r.json()
    assert "total" in j and "results" in j and "aggregations" in j
    res = j["results"][0]
    for k in ["id", "score", "title", "tags"]:
        assert k in res
    agg = j["aggregations"]
    for k in ["tags", "top_terms", "namespaces"]:
        assert k in agg
    assert isinstance(res["score"], (int, float))


def test_performance_scale(server):
    base = server
    # Bounded scale test without wall-clock gate to avoid timing false-negatives on 2 CPUs
    # Previously had elapsed <15s which is hardware-dependent and caused HIGH severity failure
    # Now only checks functional correctness under moderate load, no strict timing
    n_docs = 300
    for i in range(n_docs):
        post_doc(
            base,
            {
                "id": f"scale{i}",
                "title": f"doc {i} search engine go java",
                "body": f"body content {i} with search term go",
                "tags": ["go", "search"] if i % 2 == 0 else ["java"],
            },
        )
    # 100 searches should complete without crash/OOM, no timing assertion to avoid hardware-dependent flake
    for _ in range(100):
        r = search_get(base, q="go search", limit=10)
        assert r.status_code == 200
    r = search_get(base, q="go", limit=100)
    assert r.json()["total"] >= n_docs // 2
    # also test that stats still works under load
    r = requests.get(f"{base}/stats", timeout=5)
    assert r.status_code == 200
    assert r.json()["docs"] == n_docs


def test_regression_camelcase_highlight_subtokens(server):
    base = server
    post_doc(base, {"id": "reg_cc1", "title": "GoSearchEngine", "body": "", "tags": []})
    r = search_get(base, q="go", highlight=True)
    assert r.json()["total"] == 1
    hl = r.json()["results"][0].get("highlight", {})
    assert "<em>" in json.dumps(hl)

    r = search_get(base, q='"go search"', highlight=True)
    hl = r.json()["results"][0].get("highlight", {})
    assert json.dumps(hl).count("<em>") >= 2


def test_regression_namespace_stats_case_insensitive():
    port = find_free_port()
    custom_dir = "/tmp/ns_ci_test"
    custom_file = os.path.join(custom_dir, "index.json")
    if os.path.exists(custom_dir):
        shutil.rmtree(custom_dir, ignore_errors=True)
    os.makedirs(custom_dir, exist_ok=True)
    env = {**os.environ, "PORT": str(port), "DATA_FILE": custom_file}
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
            json={
                "id": "ns1",
                "title": "a",
                "body": "",
                "tags": [],
                "namespace": "Team-A",
            },
            timeout=5,
        )
        requests.post(
            f"{base}/documents",
            json={
                "id": "ns2",
                "title": "b",
                "body": "",
                "tags": [],
                "namespace": "team-a",
            },
            timeout=5,
        )
        time.sleep(0.5)
        r = requests.get(f"{base}/stats", timeout=5)
        assert r.status_code == 200
        assert r.json()["namespaces"] == 1
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except:
            pass
        if os.path.exists(custom_dir):
            shutil.rmtree(custom_dir, ignore_errors=True)
        if os.path.exists(DATA_DIR):
            shutil.rmtree(DATA_DIR, ignore_errors=True)


def test_invalid_operator_should_400(server):
    base = server
    r = requests.get(f"{base}/search", params={"operator": "INVALID"}, timeout=5)
    assert r.status_code == 400
    r = requests.post(
        f"{base}/search", json={"query": "go", "operator": "WRONG"}, timeout=5
    )
    assert r.status_code == 400


def test_nonnumeric_limit_offset_should_400(server):
    base = server
    r = requests.get(f"{base}/search", params={"limit": "abc"}, timeout=5)
    assert r.status_code == 400
    r = requests.get(f"{base}/search", params={"offset": "xyz"}, timeout=5)
    assert r.status_code == 400
    r = requests.get(f"{base}/search", params={"limit": "10.5"}, timeout=5)
    assert r.status_code == 400
    r = requests.post(
        f"{base}/search", json={"query": "go", "limit": "notint"}, timeout=5
    )
    assert r.status_code == 400


def test_invalid_fuzzy_distance_should_400(server):
    base = server
    r = search_get(base, q="sarch~abc")
    assert r.status_code == 400, f"sarch~abc should 400, got {r.status_code}"
    r = search_get(base, q="go~xyz")
    assert r.status_code == 400


def test_empty_phrase_should_400(server):
    base = server
    r = search_get(base, q='""')
    assert r.status_code == 400, f"empty phrase should 400, got {r.status_code}"
    r = search_get(base, q='title:""')
    assert r.status_code == 400
    r = search_get(base, q='body:"   "')
    assert r.status_code == 400


def test_limit_clamping_to_100(server):
    base = server
    for i in range(150):
        post_doc(
            base,
            {
                "id": f"clamp{i}",
                "title": f"doc clamp {i}",
                "body": "clamp test",
                "tags": [],
            },
        )
    r = search_get(base, q="clamp", limit=200)
    assert r.status_code == 200, (
        f"limit 200 should be clamped to 100, not 400, got {r.status_code}"
    )
    j = r.json()
    assert j["total"] == 150
    assert len(j["results"]) == 100, f"expected clamped to 100, got {len(j['results'])}"


def test_exact_result_keys_highlight_absent_present(server):
    base = server
    post_doc(
        base,
        {
            "id": "keys1",
            "title": "go search",
            "body": "test",
            "tags": ["go"],
            "namespace": "default",
        },
    )
    r = search_get(base, q="go", highlight=False)
    assert r.status_code == 200
    j = r.json()
    assert set(j.keys()) == {"total", "results", "aggregations"}, (
        f"top-level exact keys mismatch, got {set(j.keys())}"
    )
    res = j["results"][0]
    assert "highlight" not in res
    allowed_without_hl = {"id", "score", "title", "tags", "namespace"}
    assert set(res.keys()) == allowed_without_hl, (
        f"expected {allowed_without_hl}, got {set(res.keys())}"
    )
    r = search_get(base, q="go", highlight=True)
    res = r.json()["results"][0]
    allowed_with_hl = {"id", "score", "title", "tags", "namespace", "highlight"}
    assert set(res.keys()) == allowed_with_hl, (
        f"expected {allowed_with_hl}, got {set(res.keys())}"
    )
    for k in res["highlight"].keys():
        assert k in ("title", "body")


def test_default_namespace_in_results(server):
    base = server
    post_doc(base, {"id": "defns1", "title": "go", "body": "", "tags": []})
    r = search_get(base, q="go")
    res = next((x for x in r.json()["results"] if x["id"] == "defns1"), None)
    assert res is not None
    assert res.get("namespace") == "default"


def test_precise_stats_terms_avgdl(server):
    base = server
    post_doc(
        base,
        {
            "id": "st1",
            "title": "go search",
            "body": "engine",
            "tags": [],
            "namespace": "ns1",
        },
    )
    post_doc(
        base,
        {
            "id": "st2",
            "title": "java",
            "body": "search engine",
            "tags": [],
            "namespace": "ns2",
        },
    )
    r = requests.get(f"{base}/stats", timeout=5)
    j = r.json()
    assert j["docs"] == 2
    assert j["terms"] == 4, f"expected 4 distinct terms, got {j['terms']}"
    assert abs(j["avgdl"] - 3.0) < 0.01, f"expected avgdl 3.0, got {j['avgdl']}"
    assert set(j.keys()) == {"docs", "terms", "avgdl", "namespaces"}


def test_top_terms_sorting_and_counts(server):
    base = server
    post_doc(
        base,
        {"id": "ttc1", "title": "go go go search", "body": "engine engine", "tags": []},
    )
    post_doc(base, {"id": "ttc2", "title": "go search", "body": "go", "tags": []})
    r = search_get(base, q="go")
    top = r.json().get("aggregations", {}).get("top_terms", [])
    assert len(top) <= 5 and len(top) >= 1
    assert top[0]["term"] == "go" and top[0]["count"] == 5
    counts = [x["count"] for x in top]
    assert counts == sorted(counts, reverse=True)
    cnt2_terms = [x["term"] for x in top if x["count"] == 2]
    assert cnt2_terms == sorted(cnt2_terms)


def test_id_ascending_tie_break(server):
    base = server
    post_doc(base, {"id": "aaa", "title": "go", "body": "", "tags": []})
    post_doc(base, {"id": "zzz", "title": "go", "body": "", "tags": []})
    post_doc(base, {"id": "mmm", "title": "go", "body": "", "tags": []})
    r = search_get(base, q="go")
    ids = [x["id"] for x in r.json()["results"]]
    assert ids == sorted(ids)
    assert ids == ["aaa", "mmm", "zzz"]


def test_body_default_bm25_df_behavior(server):
    base = server
    post_doc(
        base, {"id": "bd1", "title": "uniqueTitleTerm", "body": "common", "tags": []}
    )
    post_doc(
        base, {"id": "bd2", "title": "common", "body": "uniqueBodyTerm", "tags": []}
    )
    post_doc(base, {"id": "bd3", "title": "common", "body": "common", "tags": []})
    r = search_get(base, q="title:uniqueTitleTerm")
    assert r.json()["total"] == 1 and r.json()["results"][0]["id"] == "bd1"
    r = search_get(base, q="body:uniqueBodyTerm")
    assert r.json()["total"] == 1 and r.json()["results"][0]["id"] == "bd2"
    r = search_get(base, q="common")
    assert r.json()["total"] == 3
    ids_order = [x["id"] for x in r.json()["results"]]
    assert ids_order[0] == "bd3", (
        f"bd3 with both fields should rank highest, got {ids_order}"
    )


def test_tag_namespace_fixed_scores(server):
    base = server
    post_doc(
        base,
        {
            "id": "fixed1",
            "title": "no match",
            "body": "no match",
            "tags": ["go"],
            "namespace": "team-a",
        },
    )
    r = search_get(base, q="tags:go")
    assert abs(r.json()["results"][0]["score"] - 1.0) < 0.01
    r = search_get(base, q="tags:go^2")
    assert abs(r.json()["results"][0]["score"] - 2.0) < 0.01
    r = search_get(base, q="namespace:team-a")
    assert abs(r.json()["results"][0]["score"] - 1.0) < 0.01
    r = search_get(base, q="namespace:team-a^3")
    assert abs(r.json()["results"][0]["score"] - 3.0) < 0.01


def test_no_external_search_dependencies():
    mod_path = os.path.join(APP, "go.mod")
    if os.path.exists(mod_path):
        content = open(mod_path).read().lower()
        for f in [
            "bleve",
            "elastic",
            "algolia",
            "meilisearch",
            "sonic",
            "tantivy",
            "elasticsearch",
        ]:
            assert f not in content, f"go.mod should not contain {f}"


def test_performance_scale_less_brittle(server):
    base = server
    # Previously had avg<0.2 p95<0.5 wall-clock gates inside all-must-pass reward — HIGH timing false-negative risk
    # Now checks functional correctness under scale without timing assertion, plus very lenient total bound to avoid hardware flake
    n_docs = 300
    for i in range(n_docs):
        post_doc(
            base,
            {
                "id": f"perf{i}",
                "title": f"doc {i} search engine go java",
                "body": f"body {i} search",
                "tags": ["go"],
            },
        )
    # 50 searches should complete without OOM/crash; we record latency but only assert extremely lenient total <60s to avoid brittleness
    start = time.time()
    for _ in range(50):
        r = search_get(base, q="go search", limit=10)
        assert r.status_code == 200
    elapsed = time.time() - start
    assert elapsed < 60, (
        f"50 searches over 300 docs took {elapsed}s, unexpectedly slow (possible O(N^2))"
    )
    # also ensure search still returns correct totals under load
    r = search_get(base, q="go", limit=10)
    assert r.json()["total"] == n_docs
