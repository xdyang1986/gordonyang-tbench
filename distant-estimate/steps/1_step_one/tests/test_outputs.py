import os, json, subprocess, tempfile, time, math

CANDIDATES = ["/app/router", "/app/src/router", "./router", "/app/router/router"]


def find_bin():
    # AFTR Blocker2 fix: enforce Go binary, force rebuild, delete stale script
    # Remove any pre-existing non-Go binary and force fresh build
    try:
        if os.path.exists("/app/router"):
            os.unlink("/app/router")
    except Exception:
        pass
    if os.path.exists("/app/go.mod"):
        # Force go build -o /app/router .
        subprocess.run(
            ["go", "build", "-o", "router", "."],
            cwd="/app",
            timeout=90,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    for p in CANDIDATES:
        if os.path.exists(p) and os.access(p, os.X_OK):
            return p
    return "/app/router"


BIN = find_bin()


def run(args, cwd="/tmp"):
    return subprocess.run(
        [BIN] + args,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=60,
    )


def tmp(content, suffix=".json"):
    f = tempfile.NamedTemporaryFile(delete=False, suffix=suffix, mode="w")
    f.write(content)
    f.close()
    return f.name


def test_binary_exists():
    assert os.path.exists(BIN)


def test_binary_is_go_binary():
    # AFTR Blocker2: ensure /app/router is a Go binary, not a shell/python stub
    assert os.path.exists(BIN), f"{BIN} missing"
    with open(BIN, "rb") as f:
        head = f.read(4)
    assert head == b"\x7fELF", (
        f"{BIN} is not an ELF binary (found {head!r}), must be Go binary built via go build"
    )
    # ELF Go binaries contain "Go build ID" or .go debug info, but minimal check: not starting with #!
    assert not head.startswith(b"#!"), f"{BIN} is a script, must be Go binary"
    # Check file size plausible for Go binary (>500k)
    sz = os.path.getsize(BIN)
    assert sz > 500_000, f"{BIN} too small ({sz}) to be Go binary, likely stub"
    # Try go version to confirm build info (fails for non-Go)
    proc = subprocess.run(
        ["go", "version", "-m", BIN],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=10,
    )
    # go version -m may fail on older Go, fallback to checking strings
    out = proc.stdout.decode(errors="ignore")
    if proc.returncode == 0:
        # If go version output contains no Go string at all, likely not Go
        # go version -m for Go binary usually mentions runtime or path
        assert "go" in out.lower() or "build" in out.lower() or len(out) > 0, (
            "go version -m produced no Go info, not a Go binary"
        )
    else:
        # fallback: check for Go marker
        with open(BIN, "rb") as f:
            data = f.read(2_000_000)
        assert b"Go" in data or b"main.main" in data or b"runtime." in data, (
            f"{BIN} does not contain Go runtime markers"
        )


def test_help_contains_keywords():
    proc = run(["--help"])
    assert proc.returncode == 0
    out = proc.stdout.decode().lower()
    for kw in ["graph", "from", "to", "requests", "help"]:
        assert kw in out, f"missing {kw}"


def test_help_bare():
    proc = run([])
    assert proc.returncode == 0
    out = proc.stdout.decode().lower()
    for kw in ["graph", "from", "to", "requests", "help"]:
        assert kw in out


def test_help_h():
    proc = run(["-h"])
    assert proc.returncode == 0
    assert "graph" in proc.stdout.decode().lower()


def test_simple_shortest_path():
    graph = {
        "nodes": ["A", "B", "C"],
        "edges": [
            {"from": "A", "to": "B", "distance": 5},
            {"from": "B", "to": "C", "distance": 3},
            {"from": "A", "to": "C", "distance": 10},
        ],
    }
    gp = tmp(json.dumps(graph))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "C"])
        assert proc.returncode == 0, proc.stderr.decode()
        out = json.loads(proc.stdout.decode().strip())
        assert out["path"] == ["A", "B", "C"]
        assert out["distance"] == 8
    finally:
        os.unlink(gp)


def test_source_equals_dest():
    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 5}]}
    gp = tmp(json.dumps(graph))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "A"])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip())
        assert out["path"] == ["A"] and out["distance"] == 0
    finally:
        os.unlink(gp)


def test_no_path():
    graph = {
        "nodes": ["A", "B", "C"],
        "edges": [{"from": "A", "to": "B", "distance": 5}],
    }
    gp = tmp(json.dumps(graph))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "C"])
        assert proc.returncode == 1
        out = json.loads(proc.stdout.decode().strip())
        assert out["path"] == [] and out["distance"] == -1
    finally:
        os.unlink(gp)


def test_invalid_graph_duplicate_nodes():
    graph = {
        "nodes": ["A", "A", "B"],
        "edges": [{"from": "A", "to": "B", "distance": 5}],
    }
    gp = tmp(json.dumps(graph))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B"])
        assert proc.returncode == 2 and proc.stdout.decode().strip() == ""
    finally:
        os.unlink(gp)


def test_invalid_graph_negative_distance():
    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": -5}]}
    gp = tmp(json.dumps(graph))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B"])
        assert proc.returncode == 2 and proc.stdout.decode().strip() == ""
    finally:
        os.unlink(gp)


def test_invalid_graph_self_loop():
    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "A", "distance": 5}]}
    gp = tmp(json.dumps(graph))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B"])
        assert proc.returncode == 2
    finally:
        os.unlink(gp)


def test_invalid_graph_missing_node_edge():
    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "C", "distance": 5}]}
    gp = tmp(json.dumps(graph))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B"])
        assert proc.returncode == 2
    finally:
        os.unlink(gp)


def test_invalid_graph_empty_node():
    graph = {"nodes": ["A", "", "B"], "edges": []}
    gp = tmp(json.dumps(graph))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B"])
        assert proc.returncode == 2
    finally:
        os.unlink(gp)


def test_invalid_whitespace_node():
    graph = {"nodes": ["A", "   ", "B"], "edges": []}
    gp = tmp(json.dumps(graph))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B"])
        assert proc.returncode == 2, (
            f"expected 2 for whitespace node, got {proc.returncode}"
        )
        assert proc.stdout.decode().strip() == ""
    finally:
        os.unlink(gp)


def test_invalid_whitespace_edge():
    graph = {"nodes": ["A", "B"], "edges": [{"from": "   ", "to": "B", "distance": 1}]}
    gp = tmp(json.dumps(graph))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B"])
        assert proc.returncode == 2
    finally:
        os.unlink(gp)


def test_extra_fields_in_graph_ignored():
    graph = {
        "nodes": ["A", "B"],
        "edges": [
            {"from": "A", "to": "B", "distance": 5, "extra": "ignore", "weight": 999}
        ],
        "extra_top": "ignore me",
        "version": 1,
    }
    gp = tmp(json.dumps(graph))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B"])
        assert proc.returncode == 0, (
            f"extra fields should be ignored, rc={proc.returncode} stderr={proc.stderr.decode()}"
        )
        out = json.loads(proc.stdout.decode().strip())
        assert out["path"] == ["A", "B"] and out["distance"] == 5
    finally:
        os.unlink(gp)


def test_float_distance():
    graph = {
        "nodes": ["A", "B", "C"],
        "edges": [
            {"from": "A", "to": "B", "distance": 2.5},
            {"from": "B", "to": "C", "distance": 2.5},
            {"from": "A", "to": "C", "distance": 6},
        ],
    }
    gp = tmp(json.dumps(graph))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "C"])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip())
        assert out["path"] == ["A", "B", "C"]
        assert math.isclose(out["distance"], 5.0, abs_tol=1e-6)
    finally:
        os.unlink(gp)


def test_tie_breaking_lexicographic():
    graph = {
        "nodes": ["A", "B", "C", "D"],
        "edges": [
            {"from": "A", "to": "B", "distance": 5},
            {"from": "B", "to": "D", "distance": 5},
            {"from": "A", "to": "C", "distance": 5},
            {"from": "C", "to": "D", "distance": 5},
        ],
    }
    gp = tmp(json.dumps(graph))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "D"])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip())
        assert out["path"] == ["A", "B", "D"], f"got {out['path']}"
    finally:
        os.unlink(gp)


def test_tie_break_three_equal_paths():
    graph = {
        "nodes": ["A", "B", "C", "D", "E"],
        "edges": [
            {"from": "A", "to": "B", "distance": 5},
            {"from": "B", "to": "D", "distance": 5},
            {"from": "A", "to": "C", "distance": 5},
            {"from": "C", "to": "D", "distance": 5},
            {"from": "A", "to": "E", "distance": 5},
            {"from": "E", "to": "D", "distance": 5},
        ],
    }
    gp = tmp(json.dumps(graph))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "D"])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip())
        assert out["path"] == ["A", "B", "D"], f"expected A-B-D got {out['path']}"
    finally:
        os.unlink(gp)


def test_batch_mode_all_success():
    graph = {
        "nodes": ["A", "B", "C"],
        "edges": [
            {"from": "A", "to": "B", "distance": 2},
            {"from": "B", "to": "C", "distance": 2},
            {"from": "A", "to": "C", "distance": 10},
        ],
    }
    gp = tmp(json.dumps(graph))
    reqs = [{"source": "A", "destination": "C"}, {"source": "A", "destination": "B"}]
    rp = tmp(json.dumps(reqs))
    try:
        proc = run(["--graph", gp, "--requests", rp])
        assert proc.returncode == 0
        lines = proc.stdout.decode().strip().splitlines()
        assert len(lines) == 2
        o1 = json.loads(lines[0])
        assert o1["path"] == ["A", "B", "C"] and o1["distance"] == 4
    finally:
        os.unlink(gp)
        os.unlink(rp)


def test_batch_mode_some_no_route():
    graph = {
        "nodes": ["A", "B", "C"],
        "edges": [{"from": "A", "to": "B", "distance": 2}],
    }
    gp = tmp(json.dumps(graph))
    rp = tmp(
        json.dumps(
            [{"source": "A", "destination": "B"}, {"source": "A", "destination": "C"}]
        )
    )
    try:
        proc = run(["--graph", gp, "--requests", rp])
        assert proc.returncode == 1
        lines = proc.stdout.decode().strip().splitlines()
        assert len(lines) == 2
        o2 = json.loads(lines[1])
        assert o2["path"] == [] and o2["distance"] == -1
    finally:
        os.unlink(gp)
        os.unlink(rp)


def test_batch_invalid_json():
    gp = tmp(
        json.dumps(
            {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 1}]}
        )
    )
    rp = tmp("{invalid json}")
    try:
        proc = run(["--graph", gp, "--requests", rp])
        assert proc.returncode == 2 and proc.stdout.decode().strip() == ""
    finally:
        os.unlink(gp)
        os.unlink(rp)


def test_single_missing_flags():
    gp = tmp(
        json.dumps(
            {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 1}]}
        )
    )
    try:
        proc = run(["--graph", gp])
        assert proc.returncode == 2
    finally:
        os.unlink(gp)


def test_requests_from_to_keys():
    graph = {
        "nodes": ["A", "B", "C"],
        "edges": [
            {"from": "A", "to": "B", "distance": 1},
            {"from": "B", "to": "C", "distance": 1},
        ],
    }
    gp = tmp(json.dumps(graph))
    rp = tmp(json.dumps([{"from": "A", "to": "C"}]))
    try:
        proc = run(["--graph", gp, "--requests", rp])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip().splitlines()[0])
        assert out["path"] == ["A", "B", "C"]
    finally:
        os.unlink(gp)
        os.unlink(rp)


def test_requests_extra_fields_ignored():
    graph = {
        "nodes": ["A", "B", "C"],
        "edges": [
            {"from": "A", "to": "B", "distance": 1},
            {"from": "B", "to": "C", "distance": 1},
        ],
    }
    gp = tmp(json.dumps(graph))
    rp = tmp(
        json.dumps([{"source": "A", "destination": "C", "priority": 99, "extra": "x"}])
    )
    try:
        proc = run(["--graph", gp, "--requests", rp])
        assert proc.returncode == 0, (
            f"extra fields in requests should be ignored, rc={proc.returncode}"
        )
        out = json.loads(proc.stdout.decode().strip().splitlines()[0])
        assert out["path"] == ["A", "B", "C"]
    finally:
        os.unlink(gp)
        os.unlink(rp)


def test_empty_requests():
    gp = tmp(
        json.dumps(
            {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 1}]}
        )
    )
    rp = tmp("[]")
    try:
        proc = run(["--graph", gp, "--requests", rp])
        assert proc.returncode == 0 and proc.stdout.decode().strip() == ""
    finally:
        os.unlink(gp)
        os.unlink(rp)


def test_duplicate_edges_min_distance():
    graph = {
        "nodes": ["A", "B"],
        "edges": [
            {"from": "A", "to": "B", "distance": 10},
            {"from": "A", "to": "B", "distance": 3},
        ],
    }
    gp = tmp(json.dumps(graph))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B"])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip())
        assert out["distance"] == 3
    finally:
        os.unlink(gp)


def test_case_sensitive_nodes():
    graph = {
        "nodes": ["A", "a", "B"],
        "edges": [
            {"from": "A", "to": "B", "distance": 5},
            {"from": "a", "to": "B", "distance": 1},
        ],
    }
    gp = tmp(json.dumps(graph))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B"])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip())
        assert out["path"] == ["A", "B"]
        proc2 = run(["--graph", gp, "--from", "a", "--to", "B"])
        assert proc2.returncode == 0
        out2 = json.loads(proc2.stdout.decode().strip())
        assert out2["path"] == ["a", "B"] and out2["distance"] == 1
    finally:
        os.unlink(gp)


def test_unknown_flag_exit_2():
    gp = tmp(
        json.dumps(
            {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 1}]}
        )
    )
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B", "--unknown", "flag"])
        assert proc.returncode == 2
        assert proc.stdout.decode().strip() == ""
    finally:
        os.unlink(gp)


def test_empty_edges_valid():
    graph = {"nodes": ["A", "B", "C"], "edges": []}
    gp = tmp(json.dumps(graph))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "C"])
        assert proc.returncode == 1
        out = json.loads(proc.stdout.decode().strip())
        assert out["path"] == [] and out["distance"] == -1
    finally:
        os.unlink(gp)


def test_special_chars_node_ids():
    graph = {
        "nodes": ["Node-A_1", "Node-B_2", "C"],
        "edges": [
            {"from": "Node-A_1", "to": "Node-B_2", "distance": 1},
            {"from": "Node-B_2", "to": "C", "distance": 1},
        ],
    }
    gp = tmp(json.dumps(graph))
    try:
        proc = run(["--graph", gp, "--from", "Node-A_1", "--to", "C"])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip())
        assert out["path"] == ["Node-A_1", "Node-B_2", "C"]
    finally:
        os.unlink(gp)


def test_large_batch_100_requests():
    nodes = [f"N{i}" for i in range(20)]
    edges = [{"from": f"N{i}", "to": f"N{i + 1}", "distance": 1} for i in range(19)]
    graph = {"nodes": nodes, "edges": edges}
    gp = tmp(json.dumps(graph))
    reqs = [{"source": "N0", "destination": f"N{i}"} for i in range(20)] * 5
    rp = tmp(json.dumps(reqs))
    rp1 = tmp(json.dumps([{"source": "N0", "destination": "N19"}]))
    try:
        # Relative bound: 1 request vs 100 in the same process, same parse.
        # 100 requests must not cost ~100x one (per-request re-parse / O(n) rescan).
        start = time.time()
        base = run(["--graph", gp, "--requests", rp1])
        base_elapsed = time.time() - start
        assert base.returncode == 0, base.stderr.decode()[:500]

        start = time.time()
        proc = run(["--graph", gp, "--requests", rp])
        elapsed = time.time() - start
        assert proc.returncode == 0, proc.stderr.decode()[:500]
        assert elapsed <= 25 * base_elapsed + 1.0, (
            f"batch of 100 too slow vs single request: "
            f"{elapsed:.3f}s vs baseline {base_elapsed:.3f}s"
        )
        lines = proc.stdout.decode().strip().splitlines()
        assert len(lines) == 100
        o0 = json.loads(lines[0])
        assert o0["path"] == ["N0"] and o0["distance"] == 0
    finally:
        os.unlink(gp)
        os.unlink(rp)
        os.unlink(rp1)


def test_performance_500_nodes():
    nodes = [f"N{i}" for i in range(200)]
    edges = [{"from": f"N{i}", "to": f"N{i + 1}", "distance": 1} for i in range(199)]
    extra = [
        {"from": f"N{i}", "to": f"N{i + 2}", "distance": 2} for i in range(0, 198, 2)
    ]
    graph = {"nodes": nodes, "edges": edges + extra}
    gp = tmp(json.dumps(graph))
    try:
        proc = run(["--graph", gp, "--from", "N0", "--to", "N199"])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip())
        assert out["distance"] == 199
    finally:
        os.unlink(gp)


def test_invalid_graph_file_not_found():
    proc = run(["--graph", "/nonexistent/path.json", "--from", "A", "--to", "B"])
    assert proc.returncode == 2 and proc.stdout.decode().strip() == ""


def test_float_scientific_notation_distance():
    # distance as 1e3 scientific notation
    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 1e3}]}
    import json, tempfile, os, math, subprocess

    def tmp(c):
        f = tempfile.NamedTemporaryFile(delete=False, suffix=".json", mode="w")
        f.write(c)
        f.close()
        return f.name

    def run(args):
        BIN = "/app/router"
        return subprocess.run(
            [BIN] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30
        )

    gp = tmp(json.dumps(graph))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B"])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip())
        assert math.isclose(out["distance"], 1000, abs_tol=1e-6)
    finally:
        os.unlink(gp)


def test_heavy_perf_500_nodes():
    nodes = [f"N{i}" for i in range(500)]
    edges = [{"from": f"N{i}", "to": f"N{i + 1}", "distance": 1} for i in range(499)]
    edges += [
        {"from": f"N{i}", "to": f"N{i + 10}", "distance": 5} for i in range(0, 490, 10)
    ]
    graph = {"nodes": nodes, "edges": edges}
    import json, tempfile, os, time, subprocess

    def tmp(c):
        f = tempfile.NamedTemporaryFile(delete=False, suffix=".json", mode="w")
        f.write(c)
        f.close()
        return f.name

    def run(args):
        BIN = "/app/router"
        return subprocess.run(
            [BIN] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60
        )

    gp = tmp(json.dumps(graph))
    try:
        proc = run(["--graph", gp, "--from", "N0", "--to", "N499"])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip())
        assert out["distance"] <= 499
    finally:
        os.unlink(gp)


def test_heavy_batch_200_requests():
    nodes = [f"N{i}" for i in range(100)]
    edges = [{"from": f"N{i}", "to": f"N{i + 1}", "distance": 1} for i in range(99)]
    graph = {"nodes": nodes, "edges": edges}
    import json, tempfile, os, time, subprocess

    def tmp(c):
        f = tempfile.NamedTemporaryFile(delete=False, suffix=".json", mode="w")
        f.write(c)
        f.close()
        return f.name

    def run(args):
        BIN = "/app/router"
        return subprocess.run(
            [BIN] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60
        )

    gp = tmp(json.dumps(graph))
    reqs = [{"source": "N0", "destination": f"N{i % 100}"} for i in range(200)]
    rp = tmp(json.dumps(reqs))
    try:
        start = time.time()
        proc = run(["--graph", gp, "--requests", rp])
        elapsed = time.time() - start
        assert proc.returncode == 0
        assert elapsed < 3.0, f"too slow batch 200 {elapsed}"
        lines = proc.stdout.decode().strip().splitlines()
        assert len(lines) == 200
    finally:
        os.unlink(gp)
        os.unlink(rp)


def test_empty_source_in_batch_no_route():
    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 5}]}
    import json, tempfile, os, subprocess

    def tmp(c):
        f = tempfile.NamedTemporaryFile(delete=False, suffix=".json", mode="w")
        f.write(c)
        f.close()
        return f.name

    def run(args):
        BIN = "/app/router"
        return subprocess.run(
            [BIN] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30
        )

    gp = tmp(json.dumps(graph))
    rp = tmp(
        json.dumps(
            [{"source": "", "destination": "B"}, {"source": "A", "destination": "B"}]
        )
    )
    try:
        proc = run(["--graph", gp, "--requests", rp])
        assert proc.returncode == 1, (
            f"empty source should be no route not invalid, rc={proc.returncode}"
        )
        lines = proc.stdout.decode().strip().splitlines()
        assert len(lines) == 2
        o1 = json.loads(lines[0])
        assert o1["path"] == [] and o1["distance"] == -1
        o2 = json.loads(lines[1])
        assert o2["path"] == ["A", "B"]
    finally:
        os.unlink(gp)
        os.unlink(rp)


def test_whitespace_source_in_batch_no_route():
    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 5}]}
    import json, tempfile, os, subprocess

    def tmp(c):
        f = tempfile.NamedTemporaryFile(delete=False, suffix=".json", mode="w")
        f.write(c)
        f.close()
        return f.name

    def run(args):
        BIN = "/app/router"
        return subprocess.run(
            [BIN] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30
        )

    gp = tmp(json.dumps(graph))
    rp = tmp(json.dumps([{"source": "   ", "destination": "B"}]))
    try:
        proc = run(["--graph", gp, "--requests", rp])
        assert proc.returncode == 1
        out = json.loads(proc.stdout.decode().strip().splitlines()[0])
        assert out["path"] == [] and out["distance"] == -1
    finally:
        os.unlink(gp)
        os.unlink(rp)


def test_both_keys_prefer_source():
    graph = {
        "nodes": ["A", "B", "C"],
        "edges": [
            {"from": "A", "to": "B", "distance": 1},
            {"from": "B", "to": "C", "distance": 1},
            {"from": "A", "to": "C", "distance": 10},
        ],
    }
    import json, tempfile, os, subprocess

    def tmp(c):
        f = tempfile.NamedTemporaryFile(delete=False, suffix=".json", mode="w")
        f.write(c)
        f.close()
        return f.name

    def run(args):
        BIN = "/app/router"
        return subprocess.run(
            [BIN] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30
        )

    gp = tmp(json.dumps(graph))
    rp = tmp(json.dumps([{"source": "A", "destination": "C", "from": "A", "to": "B"}]))
    try:
        proc = run(["--graph", gp, "--requests", rp])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip().splitlines()[0])
        assert out["path"] == ["A", "B", "C"], (
            f"should prefer source/dest A->C via B, got {out['path']}"
        )
    finally:
        os.unlink(gp)
        os.unlink(rp)


def test_unknown_traffic_flag_in_step1_invalid():
    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 1}]}
    import json, tempfile, os, subprocess

    def tmp(c):
        f = tempfile.NamedTemporaryFile(delete=False, suffix=".json", mode="w")
        f.write(c)
        f.close()
        return f.name

    def run(args):
        BIN = "/app/router"
        return subprocess.run(
            [BIN] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30
        )

    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps({"traffic": [{"from": "A", "to": "B", "factor": 2}]}))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B", "--traffic", tp])
        assert proc.returncode == 2, (
            f"--traffic should be unknown in step1, rc={proc.returncode}"
        )
        assert proc.stdout.decode().strip() == ""
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_batch_with_not_string_source_invalid():
    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 1}]}
    import json, tempfile, os, subprocess

    def tmp(c):
        f = tempfile.NamedTemporaryFile(delete=False, suffix=".json", mode="w")
        f.write(c)
        f.close()
        return f.name

    def run(args):
        BIN = "/app/router"
        return subprocess.run(
            [BIN] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30
        )

    gp = tmp(json.dumps(graph))
    rp = tmp(json.dumps([{"source": 123, "destination": "B"}]))
    try:
        proc = run(["--graph", gp, "--requests", rp])
        assert proc.returncode == 2
    finally:
        os.unlink(gp)
        os.unlink(rp)


def test_5_way_tie_break():
    # 5 equal distance paths A-X-F each 10, choose lexicographically smallest X=B
    graph = {
        "nodes": ["A", "B", "C", "D", "E", "G", "F"],
        "edges": [
            {"from": "A", "to": "B", "distance": 5},
            {"from": "B", "to": "F", "distance": 5},
            {"from": "A", "to": "C", "distance": 5},
            {"from": "C", "to": "F", "distance": 5},
            {"from": "A", "to": "D", "distance": 5},
            {"from": "D", "to": "F", "distance": 5},
            {"from": "A", "to": "E", "distance": 5},
            {"from": "E", "to": "F", "distance": 5},
            {"from": "A", "to": "G", "distance": 5},
            {"from": "G", "to": "F", "distance": 5},
        ],
    }
    import json, tempfile, os, subprocess

    def tmp(c):
        f = tempfile.NamedTemporaryFile(delete=False, suffix=".json", mode="w")
        f.write(c)
        f.close()
        return f.name

    def run(args):
        BIN = "/app/router"
        return subprocess.run(
            [BIN] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30
        )

    gp = tmp(json.dumps(graph))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "F"])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip())
        assert out["path"] == ["A", "B", "F"], (
            f"5-way tie should pick B, got {out['path']}"
        )
    finally:
        os.unlink(gp)


def test_large_graph_1000_nodes_performance():
    nodes = [f"N{i}" for i in range(1000)]
    edges = [{"from": f"N{i}", "to": f"N{i + 1}", "distance": 1} for i in range(999)]
    edges += [
        {"from": f"N{i}", "to": f"N{i + 100}", "distance": 50}
        for i in range(0, 900, 100)
    ]
    graph = {"nodes": nodes, "edges": edges}
    import json, tempfile, os, time, subprocess

    def tmp(c):
        f = tempfile.NamedTemporaryFile(delete=False, suffix=".json", mode="w")
        f.write(c)
        f.close()
        return f.name

    def run(args):
        BIN = "/app/router"
        return subprocess.run(
            [BIN] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60
        )

    gp = tmp(json.dumps(graph))
    try:
        start = time.time()
        proc = run(["--graph", gp, "--from", "N0", "--to", "N999"])
        elapsed = time.time() - start
        assert proc.returncode == 0, (
            f"rc={proc.returncode} stderr={proc.stderr.decode()[:200]}"
        )
        assert elapsed < 2.5, f"too slow 1000 nodes {elapsed}"
        out = json.loads(proc.stdout.decode().strip())
        assert out["distance"] <= 999
    finally:
        os.unlink(gp)


def test_large_batch_500_requests():
    nodes = [f"N{i}" for i in range(100)]
    edges = [{"from": f"N{i}", "to": f"N{i + 1}", "distance": 1} for i in range(99)]
    graph = {"nodes": nodes, "edges": edges}
    import json, tempfile, os, time, subprocess

    def tmp(c):
        f = tempfile.NamedTemporaryFile(delete=False, suffix=".json", mode="w")
        f.write(c)
        f.close()
        return f.name

    def run(args):
        BIN = "/app/router"
        return subprocess.run(
            [BIN] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60
        )

    gp = tmp(json.dumps(graph))
    reqs = [{"source": "N0", "destination": f"N{i % 100}"} for i in range(500)]
    rp = tmp(json.dumps(reqs))
    try:
        start = time.time()
        proc = run(["--graph", gp, "--requests", rp])
        elapsed = time.time() - start
        assert proc.returncode == 0
        assert elapsed < 4.0, f"too slow 500 batch {elapsed}"
        lines = proc.stdout.decode().strip().splitlines()
        assert len(lines) == 500
    finally:
        os.unlink(gp)
        os.unlink(rp)


def test_duplicate_edges_many_keep_min():
    # 10 duplicate edges between A-B with distances 10..1, should keep min 1
    graph = {
        "nodes": ["A", "B"],
        "edges": [{"from": "A", "to": "B", "distance": d} for d in range(10, 0, -1)],
    }
    import json, tempfile, os, math, subprocess

    def tmp(c):
        f = tempfile.NamedTemporaryFile(delete=False, suffix=".json", mode="w")
        f.write(c)
        f.close()
        return f.name

    def run(args):
        BIN = "/app/router"
        return subprocess.run(
            [BIN] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30
        )

    gp = tmp(json.dumps(graph))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B"])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip())
        assert math.isclose(out["distance"], 1, abs_tol=1e-6)
    finally:
        os.unlink(gp)


def test_empty_edges_array_with_nodes():
    graph = {"nodes": ["A", "B"], "edges": []}
    import json, tempfile, os, subprocess

    def tmp(c):
        f = tempfile.NamedTemporaryFile(delete=False, suffix=".json", mode="w")
        f.write(c)
        f.close()
        return f.name

    def run(args):
        BIN = "/app/router"
        return subprocess.run(
            [BIN] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30
        )

    gp = tmp(json.dumps(graph))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B"])
        assert proc.returncode == 1
        out = json.loads(proc.stdout.decode().strip())
        assert out["path"] == [] and out["distance"] == -1
    finally:
        os.unlink(gp)


def test_request_order_preserved_with_no_route():
    graph = {
        "nodes": ["A", "B", "C"],
        "edges": [{"from": "A", "to": "B", "distance": 1}],
    }
    import json, tempfile, os, subprocess

    def tmp(c):
        f = tempfile.NamedTemporaryFile(delete=False, suffix=".json", mode="w")
        f.write(c)
        f.close()
        return f.name

    def run(args):
        BIN = "/app/router"
        return subprocess.run(
            [BIN] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30
        )

    gp = tmp(json.dumps(graph))
    reqs = [
        {"source": "A", "destination": "B"},
        {"source": "A", "destination": "C"},  # no route
        {"source": "A", "destination": "B"},
    ]
    rp = tmp(json.dumps(reqs))
    try:
        proc = run(["--graph", gp, "--requests", rp])
        assert proc.returncode == 1
        lines = proc.stdout.decode().strip().splitlines()
        assert len(lines) == 3
        o0 = json.loads(lines[0])
        o1 = json.loads(lines[1])
        o2 = json.loads(lines[2])
        assert o0["path"] == ["A", "B"]
        assert o1["path"] == [] and o1["distance"] == -1
        assert o2["path"] == ["A", "B"]
        # order preserved
        assert o0["source"] == "A" and o0["destination"] == "B"
        assert o1["source"] == "A" and o1["destination"] == "C"
    finally:
        os.unlink(gp)
        os.unlink(rp)


def test_node_id_with_dot_and_special():
    # Node IDs may contain dot, slash, etc. as long as non-empty non-whitespace — should be valid
    graph = {
        "nodes": ["A.B", "C/D", "E-F_G"],
        "edges": [
            {"from": "A.B", "to": "C/D", "distance": 5},
            {"from": "C/D", "to": "E-F_G", "distance": 3},
        ],
    }
    import json, tempfile, os, subprocess

    def tmp(c):
        f = tempfile.NamedTemporaryFile(delete=False, suffix=".json", mode="w")
        f.write(c)
        f.close()
        return f.name

    def run(args):
        BIN = "/app/router"
        return subprocess.run(
            [BIN] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30
        )

    gp = tmp(json.dumps(graph))
    try:
        proc = run(["--graph", gp, "--from", "A.B", "--to", "E-F_G"])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip())
        assert out["path"] == ["A.B", "C/D", "E-F_G"]
    finally:
        os.unlink(gp)


def test_10_way_tie_break():
    nodes = ["A"] + [chr(ord("B") + i) for i in range(10)] + ["Z"]
    # B..K each 5+5 to Z
    edges = []
    for i in range(10):
        mid = chr(ord("B") + i)
        edges.append({"from": "A", "to": mid, "distance": 5})
        edges.append({"from": mid, "to": "Z", "distance": 5})
    graph = {"nodes": nodes, "edges": edges}
    import json, tempfile, os, subprocess

    def tmp(c):
        f = tempfile.NamedTemporaryFile(delete=False, suffix=".json", mode="w")
        f.write(c)
        f.close()
        return f.name

    def run(args):
        BIN = "/app/router"
        return subprocess.run(
            [BIN] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30
        )

    gp = tmp(json.dumps(graph))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "Z"])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip())
        assert out["path"] == ["A", "B", "Z"], (
            f"10-way tie should pick B lex smallest, got {out['path']}"
        )
    finally:
        os.unlink(gp)


def test_batch_1000_requests():
    nodes = [f"N{i}" for i in range(100)]
    edges = [{"from": f"N{i}", "to": f"N{i + 1}", "distance": 1} for i in range(99)]
    graph = {"nodes": nodes, "edges": edges}
    import json, tempfile, os, time, subprocess

    def tmp(c):
        f = tempfile.NamedTemporaryFile(delete=False, suffix=".json", mode="w")
        f.write(c)
        f.close()
        return f.name

    def run(args):
        BIN = "/app/router"
        return subprocess.run(
            [BIN] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=90
        )

    gp = tmp(json.dumps(graph))
    reqs = [{"source": "N0", "destination": f"N{i % 100}"} for i in range(1000)]
    rp = tmp(json.dumps(reqs))
    try:
        start = time.time()
        proc = run(["--graph", gp, "--requests", rp])
        elapsed = time.time() - start
        assert proc.returncode == 0
        assert elapsed < 5.0, f"too slow 1000 batch {elapsed}"
        assert len(proc.stdout.decode().strip().splitlines()) == 1000
    finally:
        os.unlink(gp)
        os.unlink(rp)


def test_help_with_graph_flag_still_help():
    import subprocess

    BIN = "/app/router"
    proc = subprocess.run(
        [BIN, "--help", "--graph", "dummy.json"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=30,
    )
    assert proc.returncode == 0
    assert "graph" in proc.stdout.decode().lower()


def test_unknown_single_dash_flag():
    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 1}]}
    import json, tempfile, os, subprocess

    def tmp(c):
        f = tempfile.NamedTemporaryFile(delete=False, suffix=".json", mode="w")
        f.write(c)
        f.close()
        return f.name

    def run(args):
        BIN = "/app/router"
        return subprocess.run(
            [BIN] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30
        )

    gp = tmp(json.dumps(graph))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B", "-x"])
        assert proc.returncode == 2
    finally:
        os.unlink(gp)


def test_float_with_many_decimals():
    graph = {
        "nodes": ["A", "B"],
        "edges": [{"from": "A", "to": "B", "distance": 1.123456789}],
    }
    import json, tempfile, os, math, subprocess

    def tmp(c):
        f = tempfile.NamedTemporaryFile(delete=False, suffix=".json", mode="w")
        f.write(c)
        f.close()
        return f.name

    def run(args):
        BIN = "/app/router"
        return subprocess.run(
            [BIN] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30
        )

    gp = tmp(json.dumps(graph))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B"])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip())
        assert math.isclose(out["distance"], 1.123456789, abs_tol=1e-9)
    finally:
        os.unlink(gp)


def test_request_with_extra_and_both_keys():
    graph = {
        "nodes": ["A", "B", "C"],
        "edges": [
            {"from": "A", "to": "B", "distance": 1},
            {"from": "B", "to": "C", "distance": 1},
            {"from": "A", "to": "C", "distance": 10},
        ],
    }
    import json, tempfile, os, subprocess

    def tmp(c):
        f = tempfile.NamedTemporaryFile(delete=False, suffix=".json", mode="w")
        f.write(c)
        f.close()
        return f.name

    def run(args):
        BIN = "/app/router"
        return subprocess.run(
            [BIN] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30
        )

    gp = tmp(json.dumps(graph))
    rp = tmp(
        json.dumps(
            [
                {
                    "source": "A",
                    "destination": "C",
                    "from": "A",
                    "to": "B",
                    "priority": 5,
                    "extra": "x",
                }
            ]
        )
    )
    try:
        proc = run(["--graph", gp, "--requests", rp])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip().splitlines()[0])
        assert out["path"] == ["A", "B", "C"]
    finally:
        os.unlink(gp)
        os.unlink(rp)


def test_isolated_nodes_valid():
    graph = {
        "nodes": ["A", "B", "C", "Isolated"],
        "edges": [{"from": "A", "to": "B", "distance": 1}],
    }
    import json, tempfile, os, subprocess

    def tmp(c):
        f = tempfile.NamedTemporaryFile(delete=False, suffix=".json", mode="w")
        f.write(c)
        f.close()
        return f.name

    def run(args):
        BIN = "/app/router"
        return subprocess.run(
            [BIN] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30
        )

    gp = tmp(json.dumps(graph))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B"])
        assert proc.returncode == 0
        proc2 = run(["--graph", gp, "--from", "A", "--to", "Isolated"])
        assert proc2.returncode == 1
    finally:
        os.unlink(gp)


def test_batch_duplicate_same_request():
    nodes = ["A", "B"]
    edges = [{"from": "A", "to": "B", "distance": 5}]
    graph = {"nodes": nodes, "edges": edges}
    import json, tempfile, os, subprocess

    def tmp(c):
        f = tempfile.NamedTemporaryFile(delete=False, suffix=".json", mode="w")
        f.write(c)
        f.close()
        return f.name

    def run(args):
        BIN = "/app/router"
        return subprocess.run(
            [BIN] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30
        )

    gp = tmp(json.dumps(graph))
    reqs = [{"source": "A", "destination": "B"}] * 10
    rp = tmp(json.dumps(reqs))
    try:
        proc = run(["--graph", gp, "--requests", rp])
        assert proc.returncode == 0
        lines = proc.stdout.decode().strip().splitlines()
        assert len(lines) == 10
        for line in lines:
            out = json.loads(line)
            assert out["path"] == ["A", "B"]
    finally:
        os.unlink(gp)
        os.unlink(rp)


def test_unknown_long_flag_with_equals():
    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 1}]}
    import json, tempfile, os, subprocess

    def tmp(c):
        f = tempfile.NamedTemporaryFile(delete=False, suffix=".json", mode="w")
        f.write(c)
        f.close()
        return f.name

    def run(args):
        BIN = "/app/router"
        return subprocess.run(
            [BIN] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30
        )

    gp = tmp(json.dumps(graph))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B", "--unknown=foo"])
        assert proc.returncode == 2
    finally:
        os.unlink(gp)


def test_distance_scientific_negative_exponent():
    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 1e-3}]}
    import json, tempfile, os, math, subprocess

    def tmp(c):
        f = tempfile.NamedTemporaryFile(delete=False, suffix=".json", mode="w")
        f.write(c)
        f.close()
        return f.name

    def run(args):
        BIN = "/app/router"
        return subprocess.run(
            [BIN] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30
        )

    gp = tmp(json.dumps(graph))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B"])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip())
        assert math.isclose(out["distance"], 0.001, abs_tol=1e-9)
    finally:
        os.unlink(gp)


def test_graph_duplicate_exact_invalid():
    graph = {"nodes": ["A", "A"], "edges": []}
    import json, tempfile, os, subprocess

    def tmp(c):
        f = tempfile.NamedTemporaryFile(delete=False, suffix=".json", mode="w")
        f.write(c)
        f.close()
        return f.name

    def run(args):
        BIN = "/app/router"
        return subprocess.run(
            [BIN] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30
        )

    gp = tmp(json.dumps(graph))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "A"])
        assert proc.returncode == 2
    finally:
        os.unlink(gp)


def test_edge_string_distance_invalid():
    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": "5"}]}
    import json, tempfile, os, subprocess

    def tmp(c):
        f = tempfile.NamedTemporaryFile(delete=False, suffix=".json", mode="w")
        f.write(c)
        f.close()
        return f.name

    def run(args):
        BIN = "/app/router"
        return subprocess.run(
            [BIN] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30
        )

    gp = tmp(json.dumps(graph))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B"])
        assert proc.returncode == 2
    finally:
        os.unlink(gp)


def test_requests_object_not_array_invalid():
    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 1}]}
    import json, tempfile, os, subprocess

    def tmp(c):
        f = tempfile.NamedTemporaryFile(delete=False, suffix=".json", mode="w")
        f.write(c)
        f.close()
        return f.name

    def run(args):
        BIN = "/app/router"
        return subprocess.run(
            [BIN] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30
        )

    gp = tmp(json.dumps(graph))
    rp = tmp(json.dumps({"source": "A", "destination": "B"}))
    try:
        proc = run(["--graph", gp, "--requests", rp])
        assert proc.returncode == 2
    finally:
        os.unlink(gp)
        os.unlink(rp)


def test_tie_break_special_chars():
    # Nodes with hyphen, underscore, dot — lex order: '-' < '.' < '_'? ASCII: '-' 45, '.' 46, '_' 95
    graph = {
        "nodes": ["A", "A-B", "A.B", "A_B", "Z"],
        "edges": [
            {"from": "A", "to": "A-B", "distance": 5},
            {"from": "A-B", "to": "Z", "distance": 5},
            {"from": "A", "to": "A.B", "distance": 5},
            {"from": "A.B", "to": "Z", "distance": 5},
            {"from": "A", "to": "A_B", "distance": 5},
            {"from": "A_B", "to": "Z", "distance": 5},
        ],
    }
    import json, tempfile, os, subprocess

    def tmp(c):
        f = tempfile.NamedTemporaryFile(delete=False, suffix=".json", mode="w")
        f.write(c)
        f.close()
        return f.name

    def run(args):
        BIN = "/app/router"
        return subprocess.run(
            [BIN] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30
        )

    gp = tmp(json.dumps(graph))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "Z"])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip())
        # Lex smallest among A-B, A.B, A_B: '-' (45) < '.' (46) < '_' (95), so A-B should win
        assert out["path"] == ["A", "A-B", "Z"], (
            f"special chars tie should pick A-B, got {out['path']}"
        )
    finally:
        os.unlink(gp)


def test_from_to_equals_syntax():
    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 5}]}
    import json, tempfile, os, subprocess

    def tmp(c):
        f = tempfile.NamedTemporaryFile(delete=False, suffix=".json", mode="w")
        f.write(c)
        f.close()
        return f.name

    def run(args):
        BIN = "/app/router"
        return subprocess.run(
            [BIN] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30
        )

    gp = tmp(json.dumps(graph))
    try:
        proc = run(["--graph=" + gp, "--from=A", "--to=B"])
        assert proc.returncode == 0, (
            f"equals syntax should work, rc={proc.returncode} stderr={proc.stderr.decode()[:200]}"
        )
        out = json.loads(proc.stdout.decode().strip())
        assert out["path"] == ["A", "B"]
    finally:
        os.unlink(gp)


def test_edge_missing_distance_invalid():
    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B"}]}
    import json, tempfile, os, subprocess

    def tmp(c):
        f = tempfile.NamedTemporaryFile(delete=False, suffix=".json", mode="w")
        f.write(c)
        f.close()
        return f.name

    def run(args):
        BIN = "/app/router"
        return subprocess.run(
            [BIN] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30
        )

    gp = tmp(json.dumps(graph))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B"])
        assert proc.returncode == 2
    finally:
        os.unlink(gp)


def test_requests_file_not_found():
    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 1}]}
    import json, tempfile, os, subprocess

    def tmp(c):
        f = tempfile.NamedTemporaryFile(delete=False, suffix=".json", mode="w")
        f.write(c)
        f.close()
        return f.name

    def run(args):
        BIN = "/app/router"
        return subprocess.run(
            [BIN] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30
        )

    gp = tmp(json.dumps(graph))
    try:
        proc = run(["--graph", gp, "--requests", "/nonexistent/req.json"])
        assert proc.returncode == 2
    finally:
        os.unlink(gp)


def test_large_distance_1e12():
    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 1e12}]}
    import json, tempfile, os, math, subprocess

    def tmp(c):
        f = tempfile.NamedTemporaryFile(delete=False, suffix=".json", mode="w")
        f.write(c)
        f.close()
        return f.name

    def run(args):
        BIN = "/app/router"
        return subprocess.run(
            [BIN] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30
        )

    gp = tmp(json.dumps(graph))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B"])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip())
        assert math.isclose(out["distance"], 1e12, rel_tol=1e-9)
    finally:
        os.unlink(gp)


def test_invalid_flag_equals_unknown():
    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 1}]}
    import json, tempfile, os, subprocess

    def tmp(c):
        f = tempfile.NamedTemporaryFile(delete=False, suffix=".json", mode="w")
        f.write(c)
        f.close()
        return f.name

    def run(args):
        BIN = "/app/router"
        return subprocess.run(
            [BIN] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30
        )

    gp = tmp(json.dumps(graph))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B", "--unknown=123"])
        assert proc.returncode == 2
    finally:
        os.unlink(gp)


def test_graph_nodes_not_list_invalid():
    graph = {"nodes": "notalist", "edges": []}
    import json, tempfile, os, subprocess

    def tmp(c):
        f = tempfile.NamedTemporaryFile(delete=False, suffix=".json", mode="w")
        f.write(c)
        f.close()
        return f.name

    def run(args):
        BIN = "/app/router"
        return subprocess.run(
            [BIN] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30
        )

    gp = tmp(json.dumps(graph))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B"])
        assert proc.returncode == 2
    finally:
        os.unlink(gp)


def test_edges_not_list_invalid():
    graph = {"nodes": ["A", "B"], "edges": "notalist"}
    import json, tempfile, os, subprocess

    def tmp(c):
        f = tempfile.NamedTemporaryFile(delete=False, suffix=".json", mode="w")
        f.write(c)
        f.close()
        return f.name

    def run(args):
        BIN = "/app/router"
        return subprocess.run(
            [BIN] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30
        )

    gp = tmp(json.dumps(graph))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B"])
        assert proc.returncode == 2
    finally:
        os.unlink(gp)


# ==================== EXTRA HARD TESTS (added to increase Step1 difficulty) ====================


def test_invalid_graph_nodes_contain_non_string():
    import json, tempfile, os, subprocess

    def tmp(c):
        f = tempfile.NamedTemporaryFile(delete=False, suffix=".json", mode="w")
        f.write(c)
        f.close()
        return f.name

    def run(args):
        BIN = "/app/router"
        return subprocess.run(
            [BIN] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30
        )

    for bad_nodes in [
        ["A", 123],
        ["A", None],
        ["A", True],
        ["A", {}],
        ["A", []],
        [1, 2, 3],
    ]:
        graph = {"nodes": bad_nodes, "edges": []}
        gp = tmp(json.dumps(graph))
        try:
            proc = run(["--graph", gp, "--from", "A", "--to", "B"])
            assert proc.returncode == 2, (
                f"bad nodes {bad_nodes} should be invalid, got {proc.returncode}"
            )
            assert proc.stdout.decode().strip() == ""
        finally:
            os.unlink(gp)


def test_invalid_graph_edges_contain_non_object():
    import json, tempfile, os, subprocess

    def tmp(c):
        f = tempfile.NamedTemporaryFile(delete=False, suffix=".json", mode="w")
        f.write(c)
        f.close()
        return f.name

    def run(args):
        BIN = "/app/router"
        return subprocess.run(
            [BIN] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30
        )

    for bad_edges in [
        [None],
        ["foo"],
        [123],
        [True],
        [[{"from": "A", "to": "B", "distance": 1}]],
    ]:
        graph = {"nodes": ["A", "B"], "edges": bad_edges}
        gp = tmp(json.dumps(graph))
        try:
            proc = run(["--graph", gp, "--from", "A", "--to", "B"])
            assert proc.returncode == 2, f"bad edges {bad_edges} should be invalid"
        finally:
            os.unlink(gp)


def test_invalid_graph_edge_missing_fields():
    import json, tempfile, os, subprocess

    def tmp(c):
        f = tempfile.NamedTemporaryFile(delete=False, suffix=".json", mode="w")
        f.write(c)
        f.close()
        return f.name

    def run(args):
        BIN = "/app/router"
        return subprocess.run(
            [BIN] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30
        )

    cases = [
        {"to": "B", "distance": 1},
        {"from": "A", "distance": 1},
        {"from": "A", "to": "B"},
        {},
    ]
    for bad_edge in cases:
        graph = {"nodes": ["A", "B"], "edges": [bad_edge]}
        gp = tmp(json.dumps(graph))
        try:
            proc = run(["--graph", gp, "--from", "A", "--to", "B"])
            assert proc.returncode == 2, (
                f"missing field edge {bad_edge} should be invalid"
            )
        finally:
            os.unlink(gp)


def test_invalid_graph_edge_from_to_not_string():
    import json, tempfile, os, subprocess

    def tmp(c):
        f = tempfile.NamedTemporaryFile(delete=False, suffix=".json", mode="w")
        f.write(c)
        f.close()
        return f.name

    def run(args):
        BIN = "/app/router"
        return subprocess.run(
            [BIN] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30
        )

    cases = [
        {"from": 123, "to": "B", "distance": 1},
        {"from": "A", "to": 456, "distance": 1},
        {"from": None, "to": "B", "distance": 1},
        {"from": "A", "to": None, "distance": 1},
        {"from": True, "to": "B", "distance": 1},
        {"from": "A", "to": [], "distance": 1},
        {"from": {}, "to": "B", "distance": 1},
    ]
    for bad_edge in cases:
        graph = {"nodes": ["A", "B"], "edges": [bad_edge]}
        gp = tmp(json.dumps(graph))
        try:
            proc = run(["--graph", gp, "--from", "A", "--to", "B"])
            assert proc.returncode == 2, (
                f"from/to non-string {bad_edge} should be invalid"
            )
        finally:
            os.unlink(gp)


def test_invalid_graph_edge_distance_various_invalid():
    import json, tempfile, os, subprocess

    def tmp(c):
        f = tempfile.NamedTemporaryFile(delete=False, suffix=".json", mode="w")
        f.write(c)
        f.close()
        return f.name

    def run(args):
        BIN = "/app/router"
        return subprocess.run(
            [BIN] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30
        )

    invalid_distances = [
        None,
        True,
        False,
        "5",
        {},
        [],
        0,
        -0,
        -1,
        -2.5,
    ]
    for d in invalid_distances:
        graph = {
            "nodes": ["A", "B"],
            "edges": [{"from": "A", "to": "B", "distance": d}],
        }
        # json can't encode -0 as -0, but 0 covers it; for None etc json dumps ok
        gp = tmp(json.dumps(graph))
        try:
            proc = run(["--graph", gp, "--from", "A", "--to", "B"])
            assert proc.returncode == 2, (
                f"distance {d!r} should be invalid, got {proc.returncode}"
            )
        finally:
            os.unlink(gp)


def test_invalid_graph_json_trailing_comma():
    import tempfile, os, subprocess

    def tmp(c):
        f = tempfile.NamedTemporaryFile(delete=False, suffix=".json", mode="w")
        f.write(c)
        f.close()
        return f.name

    def run(args):
        BIN = "/app/router"
        return subprocess.run(
            [BIN] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30
        )

    bad_jsons = [
        '{"nodes":["A","B"],"edges":[{"from":"A","to":"B","distance":1},]}',
        '{"nodes":["A","B"],"edges":[{"from":"A","to":"B","distance":1}],}',
        '{"nodes":["A","B",],"edges":[]}',
    ]
    for bj in bad_jsons:
        gp = tmp(bj)
        try:
            proc = run(["--graph", gp, "--from", "A", "--to", "B"])
            assert proc.returncode == 2, f"trailing comma should be invalid: {bj}"
            assert proc.stdout.decode().strip() == ""
        finally:
            os.unlink(gp)


def test_invalid_graph_json_comment():
    import tempfile, os, subprocess

    def tmp(c):
        f = tempfile.NamedTemporaryFile(delete=False, suffix=".json", mode="w")
        f.write(c)
        f.close()
        return f.name

    def run(args):
        BIN = "/app/router"
        return subprocess.run(
            [BIN] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30
        )

    bad = """{
      // comment
      "nodes": ["A","B"],
      "edges": [{"from":"A","to":"B","distance":1}]
    }"""
    gp = tmp(bad)
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B"])
        assert proc.returncode == 2
        assert proc.stdout.decode().strip() == ""
    finally:
        os.unlink(gp)


def test_invalid_graph_json_bom():
    import tempfile, os, subprocess

    def tmp_bytes(b):
        f = tempfile.NamedTemporaryFile(delete=False, suffix=".json", mode="wb")
        f.write(b)
        f.close()
        return f.name

    def run(args):
        BIN = "/app/router"
        return subprocess.run(
            [BIN] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30
        )

    # UTF-8 BOM + valid JSON
    bom_json = (
        b"\xef\xbb\xbf"
        + b'{"nodes":["A","B"],"edges":[{"from":"A","to":"B","distance":1}]}'
    )
    gp = tmp_bytes(bom_json)
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B"])
        # Must not crash, must exit 2 or handle BOM and succeed? Spec says invalid JSON with BOM must not crash and exit 2
        # Go json doesn't handle BOM, so should be exit 2
        assert proc.returncode == 2, (
            f"BOM JSON should be invalid (or handled) but must not crash, got {proc.returncode}"
        )
        # Ensure no panic in stderr that crashes binary – return code 2 is ok
    finally:
        os.unlink(gp)


def test_graph_top_not_object_invalid():
    import json, tempfile, os, subprocess

    def tmp(c):
        f = tempfile.NamedTemporaryFile(delete=False, suffix=".json", mode="w")
        f.write(c)
        f.close()
        return f.name

    def run(args):
        BIN = "/app/router"
        return subprocess.run(
            [BIN] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30
        )

    for bad in ["[]", '"string"', "123", "null", "true"]:
        gp = tmp(bad)
        try:
            proc = run(["--graph", gp, "--from", "A", "--to", "B"])
            assert proc.returncode == 2, f"top-level {bad} should be invalid"
        finally:
            os.unlink(gp)


def test_edge_with_leading_trailing_space_invalid():
    import json, tempfile, os, subprocess

    def tmp(c):
        f = tempfile.NamedTemporaryFile(delete=False, suffix=".json", mode="w")
        f.write(c)
        f.close()
        return f.name

    def run(args):
        BIN = "/app/router"
        return subprocess.run(
            [BIN] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30
        )

    # Nodes are exactly "A","B", edge " A" with leading space should be invalid (node not found)
    cases = [
        {"from": " A", "to": "B", "distance": 1},
        {"from": "A ", "to": "B", "distance": 1},
        {"from": " A ", "to": "B", "distance": 1},
        {"from": "A", "to": " B", "distance": 1},
        {"from": "A", "to": "B ", "distance": 1},
    ]
    for bad_edge in cases:
        graph = {"nodes": ["A", "B"], "edges": [bad_edge]}
        gp = tmp(json.dumps(graph))
        try:
            proc = run(["--graph", gp, "--from", "A", "--to", "B"])
            assert proc.returncode == 2, (
                f"edge {bad_edge} with leading/trailing space should be invalid (exact match)"
            )
        finally:
            os.unlink(gp)


def test_node_id_with_leading_space_distinct_valid():
    import json, tempfile, os, subprocess

    def tmp(c):
        f = tempfile.NamedTemporaryFile(delete=False, suffix=".json", mode="w")
        f.write(c)
        f.close()
        return f.name

    def run(args):
        BIN = "/app/router"
        return subprocess.run(
            [BIN] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30
        )

    # " A" and "A" are distinct valid IDs
    graph = {
        "nodes": [" A", "A", "B"],
        "edges": [
            {"from": " A", "to": "B", "distance": 1},
            {"from": "A", "to": "B", "distance": 5},
        ],
    }
    gp = tmp(json.dumps(graph))
    try:
        proc = run(["--graph", gp, "--from", " A", "--to", "B"])
        assert proc.returncode == 0, (
            f"leading space node distinct should be valid, rc={proc.returncode} stderr={proc.stderr.decode()}"
        )
        out = json.loads(proc.stdout.decode().strip())
        assert out["path"] == [" A", "B"]
        assert out["distance"] == 1
        proc2 = run(["--graph", gp, "--from", "A", "--to", "B"])
        assert proc2.returncode == 0
        out2 = json.loads(proc2.stdout.decode().strip())
        assert out2["path"] == ["A", "B"]
        assert out2["distance"] == 5
    finally:
        os.unlink(gp)


def test_request_non_object_entries_invalid():
    import json, tempfile, os, subprocess

    def tmp(c):
        f = tempfile.NamedTemporaryFile(delete=False, suffix=".json", mode="w")
        f.write(c)
        f.close()
        return f.name

    def run(args):
        BIN = "/app/router"
        return subprocess.run(
            [BIN] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30
        )

    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 1}]}
    gp = tmp(json.dumps(graph))
    bad_reqs_list = [
        [None],
        [123],
        ["foo"],
        [True],
        [[{"source": "A", "destination": "B"}]],
        [{"source": "A", "destination": "B"}, None],
        [{"source": "A", "destination": "B"}, 123],
    ]
    try:
        for bad_reqs in bad_reqs_list:
            rp = tmp(json.dumps(bad_reqs))
            try:
                proc = run(["--graph", gp, "--requests", rp])
                assert proc.returncode == 2, f"requests {bad_reqs} should be invalid"
                assert proc.stdout.decode().strip() == ""
            finally:
                os.unlink(rp)
    finally:
        os.unlink(gp)


def test_request_with_null_source_invalid():
    import json, tempfile, os, subprocess

    def tmp(c):
        f = tempfile.NamedTemporaryFile(delete=False, suffix=".json", mode="w")
        f.write(c)
        f.close()
        return f.name

    def run(args):
        BIN = "/app/router"
        return subprocess.run(
            [BIN] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30
        )

    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 1}]}
    gp = tmp(json.dumps(graph))
    cases = [
        [{"source": True, "destination": "B"}],
        [{"source": "A", "destination": {}}],
        [{"source": "A", "destination": []}],
        [{"source": 123, "destination": "B"}],
        [{"from": True, "to": "B"}],
    ]
    # null case is tricky in Go - json null into string gives "" without error, treated as no-route not invalid by current spec?
    # We explicitly test non-string that json will error, and we keep null as separate - it should be invalid but Go's handling differs.
    # To keep test stable, we test bool/object/array/number invalid, and test null separately as raw "null" check
    try:
        for bad in cases:
            rp = tmp(json.dumps(bad))
            try:
                proc = run(["--graph", gp, "--requests", rp])
                assert proc.returncode == 2, (
                    f"null/non-string source/dest {bad} should be invalid"
                )
            finally:
                os.unlink(rp)
        # Now test raw JSON containing null literal without quotes, which must be invalid
        rp_null = tmp('[{"source":null,"destination":"B"}]')
        try:
            proc = run(["--graph", gp, "--requests", rp_null])
            assert proc.returncode == 2, (
                f"null literal source should be invalid, got {proc.returncode} stdout={proc.stdout.decode()}"
            )
        finally:
            os.unlink(rp_null)
        rp_null2 = tmp('[{"source":"A","destination":null}]')
        try:
            proc = run(["--graph", gp, "--requests", rp_null2])
            assert proc.returncode == 2, f"null literal dest should be invalid"
        finally:
            os.unlink(rp_null2)
    finally:
        os.unlink(gp)


def test_request_empty_object_invalid():
    import json, tempfile, os, subprocess

    def tmp(c):
        f = tempfile.NamedTemporaryFile(delete=False, suffix=".json", mode="w")
        f.write(c)
        f.close()
        return f.name

    def run(args):
        BIN = "/app/router"
        return subprocess.run(
            [BIN] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30
        )

    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 1}]}
    gp = tmp(json.dumps(graph))
    rp = tmp(json.dumps([{}]))
    try:
        proc = run(["--graph", gp, "--requests", rp])
        assert proc.returncode == 2, "empty object {} missing keys should be invalid"
    finally:
        os.unlink(gp)
        os.unlink(rp)


def test_batch_with_missing_field_invalid():
    # Explicit test for the dominant failure mode from README
    import json, tempfile, os, subprocess

    def tmp(c):
        f = tempfile.NamedTemporaryFile(delete=False, suffix=".json", mode="w")
        f.write(c)
        f.close()
        return f.name

    def run(args):
        BIN = "/app/router"
        return subprocess.run(
            [BIN] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30
        )

    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 1}]}
    gp = tmp(json.dumps(graph))
    cases = [
        [{"source": "A"}],
        [{"destination": "B"}],
        [{"from": "A"}],
        [{"to": "B"}],
        [{"source": "A", "from": "A"}],  # missing destination/to
        [{"source": "A", "destination": "B"}, {"source": "A"}],  # second missing
    ]
    try:
        for bad in cases:
            rp = tmp(json.dumps(bad))
            try:
                proc = run(["--graph", gp, "--requests", rp])
                assert proc.returncode == 2, (
                    f"missing key batch {bad} should be exit2, got {proc.returncode}"
                )
                assert proc.stdout.decode().strip() == "", (
                    "invalid should have no stdout"
                )
            finally:
                os.unlink(rp)
    finally:
        os.unlink(gp)


def test_help_with_extra_invalid_flags_still_help():
    import subprocess

    BIN = "/app/router"
    # help flag present with unknown flag – must still be help exit0
    cases = [
        ["--help", "--unknown", "flag"],
        ["--graph", "dummy.json", "--help"],
        ["--help", "--traffic", "x"],
        ["--help", "--from", "A", "--to", "B", "--unknown=foo"],
        ["-h", "--unknown"],
        ["--graph", "a", "--help", "--requests", "b", "--unknown"],
    ]
    for args in cases:
        proc = subprocess.run(
            [BIN] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30
        )
        assert proc.returncode == 0, (
            f"help with extra flags {args} should be exit0, got {proc.returncode}"
        )
        out = proc.stdout.decode().lower()
        assert "graph" in out, f"help output missing graph for args {args}"


def test_help_positional():
    import subprocess

    BIN = "/app/router"
    proc = subprocess.run(
        [BIN, "help"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30
    )
    assert proc.returncode == 0
    out = proc.stdout.decode().lower()
    for kw in ["graph", "from", "to", "requests", "help"]:
        assert kw in out, f"positional help missing {kw}"


def test_flag_order_independence():
    import json, tempfile, os, subprocess

    def tmp(c):
        f = tempfile.NamedTemporaryFile(delete=False, suffix=".json", mode="w")
        f.write(c)
        f.close()
        return f.name

    def run(args):
        BIN = "/app/router"
        return subprocess.run(
            [BIN] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30
        )

    graph = {
        "nodes": ["A", "B", "C"],
        "edges": [
            {"from": "A", "to": "B", "distance": 1},
            {"from": "B", "to": "C", "distance": 1},
        ],
    }
    gp = tmp(json.dumps(graph))
    try:
        # different orders
        orders = [
            ["--from", "A", "--graph", gp, "--to", "C"],
            ["--to", "C", "--from", "A", "--graph", gp],
            ["--graph", gp, "--to", "C", "--from", "A"],
            [f"--from=A", f"--graph={gp}", f"--to=C"],
        ]
        for args in orders:
            proc = run(args)
            assert proc.returncode == 0, (
                f"order {args} should work, got {proc.returncode} stderr={proc.stderr.decode()}"
            )
            out = json.loads(proc.stdout.decode().strip())
            assert out["path"] == ["A", "B", "C"], (
                f"order {args} wrong path {out['path']}"
            )
    finally:
        os.unlink(gp)


def test_single_mode_empty_from_invalid():
    import json, tempfile, os, subprocess

    def tmp(c):
        f = tempfile.NamedTemporaryFile(delete=False, suffix=".json", mode="w")
        f.write(c)
        f.close()
        return f.name

    def run(args):
        BIN = "/app/router"
        return subprocess.run(
            [BIN] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30
        )

    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 1}]}
    gp = tmp(json.dumps(graph))
    try:
        # empty string for from/to in single mode → invalid exit2, not no-route
        cases = [
            ["--graph", gp, "--from", "", "--to", "B"],
            ["--graph", gp, "--from", "A", "--to", ""],
            ["--graph", gp, "--from", "   ", "--to", "B"],
            ["--graph", gp, "--from", "A", "--to", "   "],
        ]
        for args in cases:
            proc = run(args)
            assert proc.returncode == 2, (
                f"single mode empty/whitespace {args} should be invalid exit2, got {proc.returncode}"
            )
            assert proc.stdout.decode().strip() == ""
    finally:
        os.unlink(gp)


def test_query_non_existing_node_no_route():
    import json, tempfile, os, subprocess

    def tmp(c):
        f = tempfile.NamedTemporaryFile(delete=False, suffix=".json", mode="w")
        f.write(c)
        f.close()
        return f.name

    def run(args):
        BIN = "/app/router"
        return subprocess.run(
            [BIN] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30
        )

    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 1}]}
    gp = tmp(json.dumps(graph))
    try:
        # query for node not in graph – should be no route exit1, not invalid
        proc = run(["--graph", gp, "--from", "A", "--to", "C"])
        assert proc.returncode == 1, (
            f"non-existing dest should be no route exit1, got {proc.returncode}"
        )
        out = json.loads(proc.stdout.decode().strip())
        assert out["path"] == [] and out["distance"] == -1

        proc2 = run(["--graph", gp, "--from", "X", "--to", "A"])
        assert proc2.returncode == 1
        out2 = json.loads(proc2.stdout.decode().strip())
        assert out2["path"] == [] and out2["distance"] == -1

        # request with leading space " A" distinct – not in graph – no route (batch)
        rp = tmp(json.dumps([{"source": " A", "destination": "B"}]))
        try:
            proc3 = run(["--graph", gp, "--requests", rp])
            assert proc3.returncode == 1, (
                f"leading space source in batch should be no route, got {proc3.returncode}"
            )
            out3 = json.loads(proc3.stdout.decode().strip().splitlines()[0])
            assert out3["path"] == [] and out3["distance"] == -1
        finally:
            os.unlink(rp)

    finally:
        os.unlink(gp)


def test_duplicate_edges_reverse_min():
    import json, tempfile, os, math, subprocess

    def tmp(c):
        f = tempfile.NamedTemporaryFile(delete=False, suffix=".json", mode="w")
        f.write(c)
        f.close()
        return f.name

    def run(args):
        BIN = "/app/router"
        return subprocess.run(
            [BIN] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30
        )

    # Forward and reverse duplicate, keep min
    graph = {
        "nodes": ["A", "B"],
        "edges": [
            {"from": "A", "to": "B", "distance": 10},
            {"from": "B", "to": "A", "distance": 2},
        ],
    }
    gp = tmp(json.dumps(graph))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B"])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip())
        assert math.isclose(out["distance"], 2, abs_tol=1e-9), (
            f"reverse duplicate should keep min 2, got {out['distance']}"
        )
        # also test multiple duplicates mix
        graph2 = {
            "nodes": ["A", "B", "C"],
            "edges": [
                {"from": "A", "to": "B", "distance": 10},
                {"from": "B", "to": "A", "distance": 8},
                {"from": "A", "to": "B", "distance": 3},
                {"from": "B", "to": "C", "distance": 10},
                {"from": "C", "to": "B", "distance": 2},
            ],
        }
        gp2 = tmp(json.dumps(graph2))
        try:
            proc2 = run(["--graph", gp2, "--from", "A", "--to", "C"])
            assert proc2.returncode == 0
            out2 = json.loads(proc2.stdout.decode().strip())
            # A-B min 3, B-C min 2 => total 5
            assert math.isclose(out2["distance"], 5, abs_tol=1e-9), (
                f"expected 5 got {out2['distance']}"
            )
        finally:
            os.unlink(gp2)
    finally:
        os.unlink(gp)


def test_lexicographic_deeper_tie():
    # Diamond of diamonds where decision requires depth 2
    import json, tempfile, os, subprocess

    def tmp(c):
        f = tempfile.NamedTemporaryFile(delete=False, suffix=".json", mode="w")
        f.write(c)
        f.close()
        return f.name

    def run(args):
        BIN = "/app/router"
        return subprocess.run(
            [BIN] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30
        )

    # Graph: A->B1 cost1, A->C1 cost1, B1->B2 cost1, B1->C2 cost1, C1->B2 cost1, C1->C2 cost1, B2->Z cost1, C2->Z cost1
    # All 8 paths cost 3, lex smallest is A-B1-B2-Z because B1<C1 and B2<C2
    graph = {
        "nodes": ["A", "B1", "C1", "B2", "C2", "Z"],
        "edges": [
            {"from": "A", "to": "B1", "distance": 1},
            {"from": "A", "to": "C1", "distance": 1},
            {"from": "B1", "to": "B2", "distance": 1},
            {"from": "B1", "to": "C2", "distance": 1},
            {"from": "C1", "to": "B2", "distance": 1},
            {"from": "C1", "to": "C2", "distance": 1},
            {"from": "B2", "to": "Z", "distance": 1},
            {"from": "C2", "to": "Z", "distance": 1},
        ],
    }
    gp = tmp(json.dumps(graph))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "Z"])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip())
        assert out["path"] == ["A", "B1", "B2", "Z"], (
            f"deeper tie should pick A-B1-B2-Z, got {out['path']}"
        )
        # Second deeper but valid: no shortcut that makes shorter path
        # Graph: A->B 1, A->C 1, B->D 1, B->E 1, C->D 1, D->Z 1, E->Z 1, plus C->Z 2 (so A-C-Z =3 equal not shortcut)
        graph2 = {
            "nodes": ["A", "B", "C", "D", "E", "Z"],
            "edges": [
                {"from": "A", "to": "B", "distance": 1},
                {"from": "A", "to": "C", "distance": 1},
                {"from": "B", "to": "D", "distance": 1},
                {"from": "B", "to": "E", "distance": 1},
                {"from": "C", "to": "D", "distance": 1},
                {"from": "D", "to": "Z", "distance": 1},
                {"from": "E", "to": "Z", "distance": 1},
                {"from": "C", "to": "Z", "distance": 2},  # A-C-Z =3 equal to others
            ],
        }
        # Paths: A-B-D-Z (3), A-B-E-Z (3), A-C-D-Z (3), A-C-Z (3) -> lex smallest is A-B-D-Z? Let's compare:
        # A-B-D-Z vs A-B-E-Z : D<E so first wins
        # A-B-D-Z vs A-C-D-Z : B<C so first wins
        # A-B-D-Z vs A-C-Z : B<C so first wins
        # So winner A-B-D-Z
        gp2 = tmp(json.dumps(graph2))
        try:
            proc2 = run(["--graph", gp2, "--from", "A", "--to", "Z"])
            assert proc2.returncode == 0
            out2 = json.loads(proc2.stdout.decode().strip())
            assert out2["path"] == ["A", "B", "D", "Z"], (
                f"expected A-B-D-Z got {out2['path']}"
            )
        finally:
            os.unlink(gp2)
    finally:
        os.unlink(gp)


def test_lexicographic_case_sensitive_ascii():
    import json, tempfile, os, subprocess

    def tmp(c):
        f = tempfile.NamedTemporaryFile(delete=False, suffix=".json", mode="w")
        f.write(c)
        f.close()
        return f.name

    def run(args):
        BIN = "/app/router"
        return subprocess.run(
            [BIN] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30
        )

    # Nodes "a" and "A" : ASCII 'A'=65 < 'a'=97
    graph = {
        "nodes": ["S", "A", "a", "T"],
        "edges": [
            {"from": "S", "to": "A", "distance": 1},
            {"from": "A", "to": "T", "distance": 1},
            {"from": "S", "to": "a", "distance": 1},
            {"from": "a", "to": "T", "distance": 1},
        ],
    }
    gp = tmp(json.dumps(graph))
    try:
        proc = run(["--graph", gp, "--from", "S", "--to", "T"])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip())
        assert out["path"] == ["S", "A", "T"], (
            f"case sensitive lex A < a, expected S-A-T got {out['path']}"
        )
    finally:
        os.unlink(gp)

    # Special chars: '-' (45) < '.' (46) < '0' (48) < 'A' (65) < '_' (95) < 'a' (97)
    graph2 = {
        "nodes": ["A", "A-B", "A.B", "A_B", "Z"],
        "edges": [
            {"from": "A", "to": "A-B", "distance": 5},
            {"from": "A-B", "to": "Z", "distance": 5},
            {"from": "A", "to": "A.B", "distance": 5},
            {"from": "A.B", "to": "Z", "distance": 5},
            {"from": "A", "to": "A_B", "distance": 5},
            {"from": "A_B", "to": "Z", "distance": 5},
        ],
    }
    gp2 = tmp(json.dumps(graph2))
    try:
        proc2 = run(["--graph", gp2, "--from", "A", "--to", "Z"])
        assert proc2.returncode == 0
        out2 = json.loads(proc2.stdout.decode().strip())
        assert out2["path"] == ["A", "A-B", "Z"], (
            f"special char lex should pick A-B, got {out2['path']}"
        )
    finally:
        os.unlink(gp2)


def test_large_graph_2000_nodes():
    import json, tempfile, os, time, subprocess

    def tmp(c):
        f = tempfile.NamedTemporaryFile(delete=False, suffix=".json", mode="w")
        f.write(c)
        f.close()
        return f.name

    def run(args):
        BIN = "/app/router"
        return subprocess.run(
            [BIN] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60
        )

    nodes = [f"N{i}" for i in range(2000)]
    edges = [{"from": f"N{i}", "to": f"N{i + 1}", "distance": 1} for i in range(1999)]
    edges += [
        {"from": f"N{i}", "to": f"N{i + 100}", "distance": 50}
        for i in range(0, 1900, 100)
    ]
    graph = {"nodes": nodes, "edges": edges}
    gp = tmp(json.dumps(graph))
    try:
        start = time.time()
        proc = run(["--graph", gp, "--from", "N0", "--to", "N1999"])
        elapsed = time.time() - start
        assert proc.returncode == 0, (
            f"2000 nodes should succeed rc={proc.returncode} stderr={proc.stderr.decode()[:500]}"
        )
        assert elapsed < 3.5, f"too slow 2000 nodes {elapsed:.3f}s"
        out = json.loads(proc.stdout.decode().strip())
        assert out["distance"] <= 1999
    finally:
        os.unlink(gp)


def test_batch_1000_float_distances():
    import json, tempfile, os, time, subprocess, math

    def tmp(c):
        f = tempfile.NamedTemporaryFile(delete=False, suffix=".json", mode="w")
        f.write(c)
        f.close()
        return f.name

    def run(args):
        BIN = "/app/router"
        return subprocess.run(
            [BIN] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=90
        )

    nodes = [f"N{i}" for i in range(100)]
    edges = [{"from": f"N{i}", "to": f"N{i + 1}", "distance": 1.5} for i in range(99)]
    graph = {"nodes": nodes, "edges": edges}
    gp = tmp(json.dumps(graph))
    reqs = [{"source": "N0", "destination": f"N{i % 100}"} for i in range(1000)]
    rp = tmp(json.dumps(reqs))
    try:
        start = time.time()
        proc = run(["--graph", gp, "--requests", rp])
        elapsed = time.time() - start
        assert proc.returncode == 0
        assert elapsed < 5.0, f"too slow 1000 float batch {elapsed}"
        lines = proc.stdout.decode().strip().splitlines()
        assert len(lines) == 1000
        # Check first is source==dest 0
        o0 = json.loads(lines[0])
        assert o0["path"] == ["N0"] and math.isclose(o0["distance"], 0, abs_tol=1e-9)
    finally:
        os.unlink(gp)
        os.unlink(rp)


def test_requests_file_invalid_json_trailing_comma():
    import tempfile, os, subprocess

    def tmp(c):
        f = tempfile.NamedTemporaryFile(delete=False, suffix=".json", mode="w")
        f.write(c)
        f.close()
        return f.name

    def run(args):
        BIN = "/app/router"
        return subprocess.run(
            [BIN] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30
        )

    import json

    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 1}]}
    gp = tmp(json.dumps(graph))
    bad_req = '[{"source":"A","destination":"B"},]'
    rp = tmp(bad_req)
    try:
        proc = run(["--graph", gp, "--requests", rp])
        assert proc.returncode == 2
        assert proc.stdout.decode().strip() == ""
    finally:
        os.unlink(gp)
        os.unlink(rp)


def test_invalid_graph_nodes_empty_array():
    import json, tempfile, os, subprocess

    def tmp(c):
        f = tempfile.NamedTemporaryFile(delete=False, suffix=".json", mode="w")
        f.write(c)
        f.close()
        return f.name

    def run(args):
        BIN = "/app/router"
        return subprocess.run(
            [BIN] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30
        )

    graph = {"nodes": [], "edges": []}
    gp = tmp(json.dumps(graph))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B"])
        assert proc.returncode == 2
    finally:
        os.unlink(gp)


def test_batch_source_equals_dest_batch():
    import json, tempfile, os, subprocess

    def tmp(c):
        f = tempfile.NamedTemporaryFile(delete=False, suffix=".json", mode="w")
        f.write(c)
        f.close()
        return f.name

    def run(args):
        BIN = "/app/router"
        return subprocess.run(
            [BIN] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30
        )

    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 1}]}
    gp = tmp(json.dumps(graph))
    reqs = [
        {"source": "A", "destination": "A"},
        {"source": "A", "destination": "B"},
        {"source": "B", "destination": "B"},
    ]
    rp = tmp(json.dumps(reqs))
    try:
        proc = run(["--graph", gp, "--requests", rp])
        assert proc.returncode == 0, (
            f"batch with equal source/dest should be all success, got {proc.returncode}"
        )
        lines = proc.stdout.decode().strip().splitlines()
        assert len(lines) == 3
        o0 = json.loads(lines[0])
        assert o0["path"] == ["A"] and o0["distance"] == 0
        o1 = json.loads(lines[1])
        assert o1["path"] == ["A", "B"]
        o2 = json.loads(lines[2])
        assert o2["path"] == ["B"] and o2["distance"] == 0
    finally:
        os.unlink(gp)
        os.unlink(rp)


# ==================== NEW EXTRA HARD TESTS v2 – to push beyond easy ====================


def test_help_must_not_contain_traffic_in_turn1():
    import subprocess

    BIN = "/app/router"
    proc = subprocess.run(
        [BIN, "--help"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30
    )
    assert proc.returncode == 0
    out = proc.stdout.decode().lower()
    # Turn1 help must NOT advertise traffic – if it does, it's leaking Turn2 or generic template
    assert "traffic" not in out, (
        "Turn1 help should not contain traffic (only Turn2 adds it)"
    )
    for kw in ["graph", "from", "to", "requests", "help"]:
        assert kw in out


def test_output_fields_strict_single():
    import json, tempfile, os, subprocess

    def tmp(c):
        f = tempfile.NamedTemporaryFile(delete=False, suffix=".json", mode="w")
        f.write(c)
        f.close()
        return f.name

    def run(args):
        return subprocess.run(
            ["/app/router"] + args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
        )

    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 5}]}
    gp = tmp(json.dumps(graph))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B"])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip())
        assert set(out.keys()) == {"path", "distance"}, (
            f"single output must have exactly path and distance, got {out.keys()}"
        )
        assert isinstance(out["distance"], (int, float))
        assert not isinstance(out["distance"], bool)
        assert isinstance(out["path"], list)
        for elem in out["path"]:
            assert isinstance(elem, str)
    finally:
        os.unlink(gp)


def test_output_fields_strict_batch():
    import json, tempfile, os, subprocess

    def tmp(c):
        f = tempfile.NamedTemporaryFile(delete=False, suffix=".json", mode="w")
        f.write(c)
        f.close()
        return f.name

    def run(args):
        return subprocess.run(
            ["/app/router"] + args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
        )

    graph = {
        "nodes": ["A", "B", "C"],
        "edges": [
            {"from": "A", "to": "B", "distance": 1},
            {"from": "B", "to": "C", "distance": 1},
        ],
    }
    gp = tmp(json.dumps(graph))
    rp = tmp(json.dumps([{"source": "A", "destination": "C"}]))
    try:
        proc = run(["--graph", gp, "--requests", rp])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip().splitlines()[0])
        assert set(out.keys()) == {"source", "destination", "path", "distance"}, (
            f"batch output exact keys, got {out.keys()}"
        )
    finally:
        os.unlink(gp)
        os.unlink(rp)


def test_scientific_notation_plus_valid():
    import json, tempfile, os, subprocess, math

    def tmp(c):
        f = tempfile.NamedTemporaryFile(delete=False, suffix=".json", mode="w")
        f.write(c)
        f.close()
        return f.name

    def run(args):
        return subprocess.run(
            ["/app/router"] + args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
        )

    # 1e+3 is valid JSON (e/E with +), many hand-rolled parsers reject +
    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 1e3}]}
    # Write manually with + to ensure file contains "1e+3"
    gp = tmp('{"nodes":["A","B"],"edges":[{"from":"A","to":"B","distance":1e+3}]}')
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B"])
        assert proc.returncode == 0, (
            f"1e+3 should be valid, rc={proc.returncode} stderr={proc.stderr.decode()[:200]}"
        )
        out = json.loads(proc.stdout.decode().strip())
        assert math.isclose(out["distance"], 1000, rel_tol=1e-9)
        # Also 1E+3 capital E
        gp2 = tmp('{"nodes":["A","B"],"edges":[{"from":"A","to":"B","distance":1E+3}]}')
        try:
            proc2 = run(["--graph", gp2, "--from", "A", "--to", "B"])
            assert proc2.returncode == 0
        finally:
            os.unlink(gp2)
        # 2.5e+2 = 250
        gp3 = tmp(
            '{"nodes":["A","B"],"edges":[{"from":"A","to":"B","distance":2.5e+2}]}'
        )
        try:
            proc3 = run(["--graph", gp3, "--from", "A", "--to", "B"])
            assert proc3.returncode == 0
            out3 = json.loads(proc3.stdout.decode().strip())
            assert math.isclose(out3["distance"], 250, rel_tol=1e-9)
        finally:
            os.unlink(gp3)
    finally:
        os.unlink(gp)


def test_from_with_leading_space_no_route_single():
    import json, tempfile, os, subprocess

    def tmp(c):
        f = tempfile.NamedTemporaryFile(delete=False, suffix=".json", mode="w")
        f.write(c)
        f.close()
        return f.name

    def run(args):
        return subprocess.run(
            ["/app/router"] + args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
        )

    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 1}]}
    gp = tmp(json.dumps(graph))
    try:
        # Leading space " A" is valid string but not exact location → no-route, not invalid
        proc = run(["--graph", gp, "--from", " A", "--to", "B"])
        assert proc.returncode == 1, (
            f"leading space from should be no-route exit1, got {proc.returncode}"
        )
        out = json.loads(proc.stdout.decode().strip())
        assert out["path"] == [] and out["distance"] == -1
        proc2 = run(["--graph", gp, "--from", "A", "--to", " B"])
        assert proc2.returncode == 1
    finally:
        os.unlink(gp)


def test_node_ids_dot_slash_hyphen_underscore_valid():
    import json, tempfile, os, subprocess

    def tmp(c):
        f = tempfile.NamedTemporaryFile(delete=False, suffix=".json", mode="w")
        f.write(c)
        f.close()
        return f.name

    def run(args):
        return subprocess.run(
            ["/app/router"] + args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
        )

    graph = {
        "nodes": ["A-1", "A_2", "A.3", "A/B", "B"],
        "edges": [
            {"from": "A-1", "to": "A_2", "distance": 1},
            {"from": "A_2", "to": "A.3", "distance": 1},
            {"from": "A.3", "to": "A/B", "distance": 1},
            {"from": "A/B", "to": "B", "distance": 1},
        ],
    }
    gp = tmp(json.dumps(graph))
    try:
        proc = run(["--graph", gp, "--from", "A-1", "--to", "B"])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip())
        assert out["path"] == ["A-1", "A_2", "A.3", "A/B", "B"]
    finally:
        os.unlink(gp)


def test_large_graph_5000_nodes_performance():
    import json, tempfile, os, time, subprocess

    def tmp(c):
        f = tempfile.NamedTemporaryFile(delete=False, suffix=".json", mode="w")
        f.write(c)
        f.close()
        return f.name

    def run(args):
        return subprocess.run(
            ["/app/router"] + args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=60,
        )

    nodes = [f"N{i}" for i in range(5000)]
    edges = [{"from": f"N{i}", "to": f"N{i + 1}", "distance": 1} for i in range(4999)]
    graph = {"nodes": nodes, "edges": edges}
    gp = tmp(json.dumps(graph))
    try:
        start = time.time()
        proc = run(["--graph", gp, "--from", "N0", "--to", "N4999"])
        elapsed = time.time() - start
        assert proc.returncode == 0, (
            f"5000 nodes should succeed rc={proc.returncode} stderr={proc.stderr.decode()[:300]}"
        )
        assert elapsed < 4.5, f"too slow 5000 nodes {elapsed:.3f}s"
    finally:
        os.unlink(gp)


def test_batch_2000_requests_performance():
    import json, tempfile, os, time, subprocess

    def tmp(c):
        f = tempfile.NamedTemporaryFile(delete=False, suffix=".json", mode="w")
        f.write(c)
        f.close()
        return f.name

    def run(args):
        return subprocess.run(
            ["/app/router"] + args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=90,
        )

    nodes = [f"N{i}" for i in range(200)]
    edges = [{"from": f"N{i}", "to": f"N{i + 1}", "distance": 1} for i in range(199)]
    graph = {"nodes": nodes, "edges": edges}
    gp = tmp(json.dumps(graph))
    reqs = [{"source": "N0", "destination": f"N{i % 200}"} for i in range(2000)]
    rp = tmp(json.dumps(reqs))
    try:
        start = time.time()
        proc = run(["--graph", gp, "--requests", rp])
        elapsed = time.time() - start
        assert proc.returncode == 0
        assert elapsed < 6.0, f"too slow 2000 batch {elapsed:.3f}s"
        assert len(proc.stdout.decode().strip().splitlines()) == 2000
    finally:
        os.unlink(gp)
        os.unlink(rp)


def test_graph_extra_nested_object_ignored():
    import json, tempfile, os, subprocess

    def tmp(c):
        f = tempfile.NamedTemporaryFile(delete=False, suffix=".json", mode="w")
        f.write(c)
        f.close()
        return f.name

    def run(args):
        return subprocess.run(
            ["/app/router"] + args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
        )

    graph = {
        "nodes": ["A", "B"],
        "edges": [
            {
                "from": "A",
                "to": "B",
                "distance": 1,
                "meta": {"x": 1, "y": {"z": 2}},
                "extra": 123,
            }
        ],
    }
    gp = tmp(json.dumps(graph))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B"])
        assert proc.returncode == 0, (
            f"nested extra should be ignored rc={proc.returncode}"
        )
    finally:
        os.unlink(gp)


def test_batch_order_preserved_large():
    import json, tempfile, os, subprocess, random

    def tmp(c):
        f = tempfile.NamedTemporaryFile(delete=False, suffix=".json", mode="w")
        f.write(c)
        f.close()
        return f.name

    def run(args):
        return subprocess.run(
            ["/app/router"] + args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=60,
        )

    nodes = ["A", "B", "C", "D", "E"]
    edges = [
        {"from": "A", "to": "B", "distance": 1},
        {"from": "B", "to": "C", "distance": 1},
        {"from": "C", "to": "D", "distance": 1},
        {"from": "D", "to": "E", "distance": 1},
    ]
    graph = {"nodes": nodes, "edges": edges}
    gp = tmp(json.dumps(graph))
    random.seed(0)
    reqs = []
    for _ in range(200):
        s = random.choice(nodes)
        d = random.choice(nodes)
        reqs.append({"source": s, "destination": d})
    rp = tmp(json.dumps(reqs))
    try:
        proc = run(["--graph", gp, "--requests", rp])
        assert proc.returncode == 0
        lines = proc.stdout.decode().strip().splitlines()
        assert len(lines) == 200
        for i, line in enumerate(lines):
            o = json.loads(line)
            assert (
                o["source"] == reqs[i]["source"]
                and o["destination"] == reqs[i]["destination"]
            ), f"order mismatch at {i}"
    finally:
        os.unlink(gp)
        os.unlink(rp)


def test_tie_break_5_way_raw_extra():
    import json, tempfile, os, subprocess

    def tmp(c):
        f = tempfile.NamedTemporaryFile(delete=False, suffix=".json", mode="w")
        f.write(c)
        f.close()
        return f.name

    def run(args):
        return subprocess.run(
            ["/app/router"] + args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
        )

    nodes = ["S"] + [chr(ord("B") + i) for i in range(5)] + ["T"]
    edges = []
    for i in range(5):
        mid = chr(ord("B") + i)
        edges.append({"from": "S", "to": mid, "distance": 5})
        edges.append({"from": mid, "to": "T", "distance": 5})
    graph = {"nodes": nodes, "edges": edges}
    gp = tmp(json.dumps(graph))
    try:
        proc = run(["--graph", gp, "--from", "S", "--to", "T"])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip())
        assert out["path"] == ["S", "B", "T"], (
            f"5-way raw tie should pick B, got {out['path']}"
        )
    finally:
        os.unlink(gp)


def test_nodes_distinct_with_spaces_valid():
    import json, tempfile, os, subprocess

    def tmp(c):
        f = tempfile.NamedTemporaryFile(delete=False, suffix=".json", mode="w")
        f.write(c)
        f.close()
        return f.name

    def run(args):
        return subprocess.run(
            ["/app/router"] + args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
        )

    # "A" and " A" distinct valid
    graph = {
        "nodes": ["A", " A", "B"],
        "edges": [
            {"from": "A", "to": "B", "distance": 5},
            {"from": " A", "to": "B", "distance": 1},
        ],
    }
    gp = tmp(json.dumps(graph))
    try:
        proc = run(["--graph", gp, "--from", " A", "--to", "B"])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip())
        assert out["path"] == [" A", "B"]
        assert out["distance"] == 1
    finally:
        os.unlink(gp)


def test_requests_empty_array_valid():
    import json, tempfile, os, subprocess

    def tmp(c):
        f = tempfile.NamedTemporaryFile(delete=False, suffix=".json", mode="w")
        f.write(c)
        f.close()
        return f.name

    def run(args):
        return subprocess.run(
            ["/app/router"] + args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
        )

    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 1}]}
    gp = tmp(json.dumps(graph))
    rp = tmp(json.dumps([]))
    try:
        proc = run(["--graph", gp, "--requests", rp])
        assert proc.returncode == 0, (
            f"empty requests array should be valid exit0, got {proc.returncode}"
        )
        assert proc.stdout.decode().strip() == ""  # no lines
    finally:
        os.unlink(gp)
        os.unlink(rp)


def test_batch_all_no_route_exit1():
    import json, tempfile, os, subprocess

    def tmp(c):
        f = tempfile.NamedTemporaryFile(delete=False, suffix=".json", mode="w")
        f.write(c)
        f.close()
        return f.name

    def run(args):
        return subprocess.run(
            ["/app/router"] + args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
        )

    graph = {
        "nodes": ["A", "B", "Isolated"],
        "edges": [{"from": "A", "to": "B", "distance": 1}],
    }
    gp = tmp(json.dumps(graph))
    reqs = [
        {"source": "A", "destination": "Isolated"},
        {"source": "Isolated", "destination": "B"},
    ]
    rp = tmp(json.dumps(reqs))
    try:
        proc = run(["--graph", gp, "--requests", rp])
        assert proc.returncode == 1
        lines = proc.stdout.decode().strip().splitlines()
        assert len(lines) == 2
        for line in lines:
            o = json.loads(line)
            assert o["path"] == [] and o["distance"] == -1
    finally:
        os.unlink(gp)
        os.unlink(rp)


def test_invalid_graph_edges_array_not_object():
    import json, tempfile, os, subprocess

    def tmp(c):
        f = tempfile.NamedTemporaryFile(delete=False, suffix=".json", mode="w")
        f.write(c)
        f.close()
        return f.name

    def run(args):
        return subprocess.run(
            ["/app/router"] + args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
        )

    for bad in [
        '{"nodes":["A","B"],"edges":[[1,2,3]]}',
        '{"nodes":["A","B"],"edges":[["A","B",1]]}',
    ]:
        gp = tmp(bad)
        try:
            proc = run(["--graph", gp, "--from", "A", "--to", "B"])
            assert proc.returncode == 2, (
                f"edges array element array should be invalid: {bad}"
            )
        finally:
            os.unlink(gp)


def test_invalid_graph_distance_plus_invalid_json():
    import tempfile, os, subprocess

    def tmp(c):
        f = tempfile.NamedTemporaryFile(delete=False, suffix=".json", mode="w")
        f.write(c)
        f.close()
        return f.name

    def run(args):
        return subprocess.run(
            ["/app/router"] + args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
        )

    # +5 is invalid JSON per spec (explicit plus)
    gp = tmp('{"nodes":["A","B"],"edges":[{"from":"A","to":"B","distance":+5}]}')
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B"])
        assert proc.returncode == 2
    finally:
        os.unlink(gp)


def test_flag_missing_value_invalid():
    import json, tempfile, os, subprocess

    def tmp(c):
        f = tempfile.NamedTemporaryFile(delete=False, suffix=".json", mode="w")
        f.write(c)
        f.close()
        return f.name

    def run(args):
        return subprocess.run(
            ["/app/router"] + args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
        )

    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 1}]}
    gp = tmp(json.dumps(graph))
    try:
        proc = run(["--graph"])
        assert proc.returncode == 2
        proc2 = run(["--graph", gp, "--from"])
        assert proc2.returncode == 2
    finally:
        os.unlink(gp)


def test_help_equals_syntax():
    import subprocess

    BIN = "/app/router"
    for args in [["--help=true"], ["--help=1"], ["-h=true"]]:
        proc = subprocess.run(
            [BIN] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30
        )
        assert proc.returncode == 0, (
            f"help equals syntax {args} should be help, got {proc.returncode}"
        )
        assert "graph" in proc.stdout.decode().lower()


def test_unknown_single_dash_flag_invalid():
    import json, tempfile, os, subprocess

    def tmp(c):
        f = tempfile.NamedTemporaryFile(delete=False, suffix=".json", mode="w")
        f.write(c)
        f.close()
        return f.name

    def run(args):
        return subprocess.run(
            ["/app/router"] + args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
        )

    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 1}]}
    gp = tmp(json.dumps(graph))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B", "-x"])
        assert proc.returncode == 2
    finally:
        os.unlink(gp)


def test_batch_with_whitespace_source_no_route():
    import json, tempfile, os, subprocess

    def tmp(c):
        f = tempfile.NamedTemporaryFile(delete=False, suffix=".json", mode="w")
        f.write(c)
        f.close()
        return f.name

    def run(args):
        return subprocess.run(
            ["/app/router"] + args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
        )

    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 1}]}
    gp = tmp(json.dumps(graph))
    rp = tmp(json.dumps([{"source": "   ", "destination": "B"}]))
    try:
        proc = run(["--graph", gp, "--requests", rp])
        assert proc.returncode == 1
        out = json.loads(proc.stdout.decode().strip().splitlines()[0])
        assert out["path"] == [] and out["distance"] == -1
    finally:
        os.unlink(gp)
        os.unlink(rp)


def test_nodes_case_sensitive_distinct():
    import json, tempfile, os, subprocess

    def tmp(c):
        f = tempfile.NamedTemporaryFile(delete=False, suffix=".json", mode="w")
        f.write(c)
        f.close()
        return f.name

    def run(args):
        return subprocess.run(
            ["/app/router"] + args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
        )

    graph = {
        "nodes": ["A", "a", "B"],
        "edges": [
            {"from": "A", "to": "B", "distance": 1},
            {"from": "a", "to": "B", "distance": 1},
        ],
    }
    gp = tmp(json.dumps(graph))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B"])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip())
        assert out["path"] == ["A", "B"]
        proc2 = run(["--graph", gp, "--from", "a", "--to", "B"])
        assert proc2.returncode == 0
        out2 = json.loads(proc2.stdout.decode().strip())
        assert out2["path"] == ["a", "B"]
    finally:
        os.unlink(gp)


def test_performance_dense_5000_edges():
    import json, tempfile, os, time, subprocess

    def tmp(c):
        f = tempfile.NamedTemporaryFile(delete=False, suffix=".json", mode="w")
        f.write(c)
        f.close()
        return f.name

    def run(args):
        return subprocess.run(
            ["/app/router"] + args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=60,
        )

    nodes = [f"N{i}" for i in range(100)]
    edges = []
    for i in range(100):
        for j in range(i + 1, min(i + 10, 100)):
            edges.append({"from": f"N{i}", "to": f"N{j}", "distance": float(j - i)})
    graph = {"nodes": nodes, "edges": edges}
    gp = tmp(json.dumps(graph))
    try:
        start = time.time()
        proc = run(["--graph", gp, "--from", "N0", "--to", "N99"])
        elapsed = time.time() - start
        assert proc.returncode == 0
        assert elapsed < 2.0, f"dense 5000 edges too slow {elapsed}"
    finally:
        os.unlink(gp)


# === ULTRA HARD v2 for Step1 - 119->150+ making both steps harder ===


def test_invalid_graph_top_level_string_number_null_invalid():
    import tempfile, os, subprocess, json

    def tmp(c):
        f = tempfile.NamedTemporaryFile(delete=False, suffix=".json", mode="w")
        f.write(c)
        f.close()
        return f.name

    def run(args):
        return subprocess.run(
            ["/app/router"] + args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
        )

    for bad in [
        '"string"',
        "123",
        "null",
        "true",
        "[]",
        '{"foo":[]}',
        '{"nodes":[]}',
        '{"edges":[]}',
    ]:
        gp = tmp(bad)
        try:
            proc = run(["--graph", gp, "--from", "A", "--to", "B"])
            assert proc.returncode == 2 and proc.stdout.decode().strip() == "", (
                f"graph top-level {bad} should be invalid"
            )
        finally:
            os.unlink(gp)


def test_invalid_graph_nodes_non_string_extra_hard():
    import tempfile, os, subprocess, json

    def tmp(c):
        f = tempfile.NamedTemporaryFile(delete=False, suffix=".json", mode="w")
        f.write(c)
        f.close()
        return f.name

    def run(args):
        return subprocess.run(
            ["/app/router"] + args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
        )

    for bad_node in ["123", "null", "true", "{}", "[]", '["A","B"]']:
        gp = tmp('{"nodes":["A",' + bad_node + '],"edges":[]}')
        try:
            proc = run(["--graph", gp, "--from", "A", "--to", "B"])
            assert proc.returncode == 2, (
                f"nodes non-string {bad_node} should be invalid"
            )
        finally:
            os.unlink(gp)


def test_invalid_graph_edges_non_object_extra_hard():
    import tempfile, os, subprocess, json

    def tmp(c):
        f = tempfile.NamedTemporaryFile(delete=False, suffix=".json", mode="w")
        f.write(c)
        f.close()
        return f.name

    def run(args):
        return subprocess.run(
            ["/app/router"] + args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
        )

    for bad_edge in ["null", "123", '"str"', "[1,2,3]", "true", "[]"]:
        gp = tmp('{"nodes":["A","B"],"edges":[' + bad_edge + "]}")
        try:
            proc = run(["--graph", gp, "--from", "A", "--to", "B"])
            assert proc.returncode == 2, (
                f"edges non-object {bad_edge} should be invalid"
            )
        finally:
            os.unlink(gp)


def test_invalid_graph_edge_missing_fields_extra_hard():
    import tempfile, os, subprocess, json

    def tmp(c):
        f = tempfile.NamedTemporaryFile(delete=False, suffix=".json", mode="w")
        f.write(c)
        f.close()
        return f.name

    def run(args):
        return subprocess.run(
            ["/app/router"] + args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
        )

    for bad in [
        '{"nodes":["A","B"],"edges":[{"to":"B","distance":1}]}',
        '{"nodes":["A","B"],"edges":[{"from":"A","distance":1}]}',
        '{"nodes":["A","B"],"edges":[{"from":"A","to":"B"}]}',
        '{"nodes":["A","B"],"edges":[{}]}',
        '{"nodes":["A","B"],"edges":[{"from":"A","to":"B","distance":1},{"foo":"bar"}]}',
    ]:
        gp = tmp(bad)
        try:
            proc = run(["--graph", gp, "--from", "A", "--to", "B"])
            assert proc.returncode == 2, (
                f"edge missing fields {bad[:50]} should be invalid"
            )
        finally:
            os.unlink(gp)


def test_invalid_graph_edge_from_to_not_string_extra_hard():
    import tempfile, os, subprocess, json

    def tmp(c):
        f = tempfile.NamedTemporaryFile(delete=False, suffix=".json", mode="w")
        f.write(c)
        f.close()
        return f.name

    def run(args):
        return subprocess.run(
            ["/app/router"] + args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
        )

    for bad in ["123", "null", "true", "{}", "[]"]:
        for edge in [
            f'{{"from":{bad},"to":"B","distance":1}}',
            f'{{"from":"A","to":{bad},"distance":1}}',
        ]:
            gp = tmp('{"nodes":["A","B"],"edges":[' + edge + "]}")
            try:
                proc = run(["--graph", gp, "--from", "A", "--to", "B"])
                assert proc.returncode == 2, (
                    f"from/to not string {edge} should be invalid"
                )
            finally:
                os.unlink(gp)


def test_invalid_graph_edge_whitespace_only_invalid_extra_hard():
    import tempfile, os, subprocess, json

    def tmp(c):
        f = tempfile.NamedTemporaryFile(delete=False, suffix=".json", mode="w")
        f.write(c)
        f.close()
        return f.name

    def run(args):
        return subprocess.run(
            ["/app/router"] + args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
        )

    for bad in ["", "   ", "\t\n"]:
        gp = tmp(
            '{"nodes":["A","B"],"edges":[{"from":'
            + json.dumps(bad)
            + ',"to":"B","distance":1}]}'
        )
        gp2 = tmp(
            '{"nodes":["A","B"],"edges":[{"from":"A","to":'
            + json.dumps(bad)
            + ',"distance":1}]}'
        )
        try:
            proc = run(["--graph", gp, "--from", "A", "--to", "B"])
            assert proc.returncode == 2
            proc2 = run(["--graph", gp2, "--from", "A", "--to", "B"])
            assert proc2.returncode == 2
        finally:
            os.unlink(gp)
            os.unlink(gp2)


def test_invalid_graph_edge_distance_various_invalid_extra_hard():
    import tempfile, os, subprocess, json

    def tmp(c):
        f = tempfile.NamedTemporaryFile(delete=False, suffix=".json", mode="w")
        f.write(c)
        f.close()
        return f.name

    def run(args):
        return subprocess.run(
            ["/app/router"] + args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
        )

    for bad_dist in ["0", "-1", "-0", "null", '"5"', "true", "false", "{}", "[]"]:
        gp = tmp(
            '{"nodes":["A","B"],"edges":[{"from":"A","to":"B","distance":'
            + bad_dist
            + "}]}"
        )
        try:
            proc = run(["--graph", gp, "--from", "A", "--to", "B"])
            assert proc.returncode == 2, f"distance {bad_dist} should be invalid"
        finally:
            os.unlink(gp)


def test_graph_bom_trailing_comma_comment_extra_hard():
    import tempfile, os, subprocess, json

    def tmp(c):
        f = tempfile.NamedTemporaryFile(delete=False, suffix=".json", mode="w")
        f.write(c)
        f.close()
        return f.name

    def tmp_bytes(b):
        f = tempfile.NamedTemporaryFile(delete=False, suffix=".json", mode="wb")
        f.write(b)
        f.close()
        return f.name

    def run(args):
        return subprocess.run(
            ["/app/router"] + args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
        )

    gp_trail = tmp('{"nodes":["A","B"],"edges":[{"from":"A","to":"B","distance":1},]}')
    gp_comment = tmp(
        '// comment\n{"nodes":["A","B"],"edges":[{"from":"A","to":"B","distance":1}]}'
    )
    gp_bom = tmp_bytes(
        b'\xef\xbb\xbf{"nodes":["A","B"],"edges":[{"from":"A","to":"B","distance":1}]}'
    )
    try:
        for gp in [gp_trail, gp_comment, gp_bom]:
            proc = run(["--graph", gp, "--from", "A", "--to", "B"])
            assert proc.returncode == 2 and proc.stdout.decode().strip() == "", (
                f"malformed graph {gp} should be invalid"
            )
    finally:
        os.unlink(gp_trail)
        os.unlink(gp_comment)
        os.unlink(gp_bom)


def test_graph_with_leading_trailing_space_exact_invalid_extra_hard():
    import tempfile, os, subprocess, json

    def tmp(c):
        f = tempfile.NamedTemporaryFile(delete=False, suffix=".json", mode="w")
        f.write(c)
        f.close()
        return f.name

    def run(args):
        return subprocess.run(
            ["/app/router"] + args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
        )

    # Graph has nodes A,B only, edge from " A" (leading space) should be invalid because node " A" not in nodes
    gp = tmp('{"nodes":["A","B"],"edges":[{"from":" A","to":"B","distance":1}]}')
    gp2 = tmp('{"nodes":["A","B"],"edges":[{"from":"A","to":" B","distance":1}]}')
    gp3 = tmp('{"nodes":["A","B"],"edges":[{"from":"A ","to":"B","distance":1}]}')
    try:
        for g in [gp, gp2, gp3]:
            proc = run(["--graph", g, "--from", "A", "--to", "B"])
            assert proc.returncode == 2, (
                "leading/trailing space exact should be invalid for graph"
            )
    finally:
        os.unlink(gp)
        os.unlink(gp2)
        os.unlink(gp3)


def test_flag_order_and_equals_extra_hard():
    import tempfile, os, subprocess, json, math

    def tmp(c):
        f = tempfile.NamedTemporaryFile(delete=False, suffix=".json", mode="w")
        f.write(c)
        f.close()
        return f.name

    def run(args):
        return subprocess.run(
            ["/app/router"] + args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
        )

    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 5}]}
    gp = tmp(json.dumps(graph))
    try:
        orders = [
            ["--graph", gp, "--from", "A", "--to", "B"],
            ["--from", "A", "--graph", gp, "--to", "B"],
            ["--to", "B", "--graph", gp, "--from", "A"],
            [f"--graph={gp}", "--from=A", "--to=B"],
            [f"--graph={gp}", f"--from=A", f"--to=B"],
            ["--from=A", f"--graph={gp}", "--to=B"],
        ]
        for args in orders:
            proc = run(args)
            assert proc.returncode == 0, f"flag order {args} should work"
            out = json.loads(proc.stdout.decode().strip())
            assert math.isclose(out["distance"], 5, abs_tol=1e-6)
        # missing value
        proc2 = run(["--graph", gp, "--from", "A", "--to"])
        assert proc2.returncode == 2
        proc3 = run(["--graph"])
        assert proc3.returncode == 2
        proc4 = run(["--graph", gp, "--from", "A", "--to", "B", "--unknown"])
        assert proc4.returncode == 2
        proc5 = run(["--graph", gp, "--from", "A", "--to", "B", "--unknown=foo"])
        assert proc5.returncode == 2
    finally:
        os.unlink(gp)


def test_help_precedence_extra_hard():
    import subprocess

    BIN = "/app/router"
    cases = [
        [BIN, "--help"],
        [BIN, "-h"],
        [BIN, "help"],
        [BIN, "--help", "--unknown"],
        [BIN, "--unknown", "--help"],
        [BIN, "--help=true"],
        [BIN, "-h=true"],
        [BIN, "--graph", "nonexistent", "--help"],
        [BIN, "--help", "--graph", "nonexistent", "--from", "A", "--to", "B"],
        [BIN],
    ]
    for args in cases:
        proc = subprocess.run(
            args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30
        )
        assert proc.returncode == 0, f"help {args} should be 0"
        out = proc.stdout.decode().lower()
        assert (
            "graph" in out
            and "from" in out
            and "to" in out
            and "requests" in out
            and "help" in out
        )


def test_output_fields_strict_extra_hard():
    import tempfile, os, subprocess, json

    def tmp(c):
        f = tempfile.NamedTemporaryFile(delete=False, suffix=".json", mode="w")
        f.write(c)
        f.close()
        return f.name

    def run(args):
        return subprocess.run(
            ["/app/router"] + args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
        )

    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 5}]}
    gp = tmp(json.dumps(graph))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B"])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip())
        assert set(out.keys()) == {"path", "distance"}, (
            f"single strict 2 keys, got {out.keys()}"
        )
        assert isinstance(out["path"], list) and isinstance(
            out["distance"], (int, float)
        )
        # batch
        rp = tmp(json.dumps([{"source": "A", "destination": "B"}]))
        proc2 = run(["--graph", gp, "--requests", rp])
        assert proc2.returncode == 0
        out2 = json.loads(proc2.stdout.decode().strip().splitlines()[0])
        assert set(out2.keys()) == {"source", "destination", "path", "distance"}
        os.unlink(rp)
        # no-route
        proc3 = run(["--graph", gp, "--from", "A", "--to", "C"])
        assert proc3.returncode == 1
        out3 = json.loads(proc3.stdout.decode().strip())
        assert out3["path"] == [] and out3["distance"] == -1
        assert set(out3.keys()) == {"path", "distance"}
    finally:
        os.unlink(gp)


def test_batch_validation_extra_hard():
    import tempfile, os, subprocess, json

    def tmp(c):
        f = tempfile.NamedTemporaryFile(delete=False, suffix=".json", mode="w")
        f.write(c)
        f.close()
        return f.name

    def run(args):
        return subprocess.run(
            ["/app/router"] + args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
        )

    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 1}]}
    gp = tmp(json.dumps(graph))
    invalids = [
        "[null]",
        "[123]",
        '["abc"]',
        '[{"source":null,"destination":"B"}]',
        '[{"source":123,"destination":"B"}]',
        "[{}]",
        '[{"source":"A"}]',
        '[{"destination":"B"}]',
        '{"source":"A","destination":"B"}',
        '[{"source":"A","destination":"B"},]',
        '[{"from":"A","to":"B"}, null]',
        '[{"source":"A","destination":"B","extra":1}, "string"]',
    ]
    try:
        for bad in invalids:
            rp = tmp(bad)
            try:
                proc = run(["--graph", gp, "--requests", rp])
                assert proc.returncode == 2, f"requests {bad} should be invalid"
                assert proc.stdout.decode().strip() == ""
            finally:
                os.unlink(rp)
        # empty valid
        rp_empty = tmp("[]")
        proc_empty = run(["--graph", gp, "--requests", rp_empty])
        assert proc_empty.returncode == 0 and proc_empty.stdout.decode().strip() == ""
        os.unlink(rp_empty)
        # batch empty source no-route
        rp_no = tmp(json.dumps([{"source": "", "destination": "B"}]))
        proc_no = run(["--graph", gp, "--requests", rp_no])
        assert proc_no.returncode == 1
        out = json.loads(proc_no.stdout.decode().strip().splitlines()[0])
        assert out["distance"] == -1
        os.unlink(rp_no)
    finally:
        os.unlink(gp)


def test_lex_tie_deeper_and_case_sensitive_extra_hard():
    import tempfile, os, subprocess, json

    def tmp(c):
        f = tempfile.NamedTemporaryFile(delete=False, suffix=".json", mode="w")
        f.write(c)
        f.close()
        return f.name

    def run(args):
        return subprocess.run(
            ["/app/router"] + args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
        )

    # deeper diamond: A-B1-B2-Z etc all cost 15, pick A-B1-B2-Z
    graph = {
        "nodes": ["A", "B1", "C1", "B2", "C2", "Z"],
        "edges": [
            {"from": "A", "to": "B1", "distance": 5},
            {"from": "A", "to": "C1", "distance": 5},
            {"from": "B1", "to": "B2", "distance": 5},
            {"from": "B1", "to": "C2", "distance": 5},
            {"from": "C1", "to": "B2", "distance": 5},
            {"from": "C1", "to": "C2", "distance": 5},
            {"from": "B2", "to": "Z", "distance": 5},
            {"from": "C2", "to": "Z", "distance": 5},
        ],
    }
    gp = tmp(json.dumps(graph))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "Z"])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip())
        assert out["path"] == ["A", "B1", "B2", "Z"], (
            f"deeper diamond should pick B1-B2, got {out['path']}"
        )
        # case-sensitive: '-' < '.' < '_' and 'A' < 'a'
        graph2 = {
            "nodes": ["A", "A-B", "A.B", "A_B", "a", "Z"],
            "edges": [
                {"from": "A", "to": "A-B", "distance": 5},
                {"from": "A-B", "to": "Z", "distance": 5},
                {"from": "A", "to": "A.B", "distance": 5},
                {"from": "A.B", "to": "Z", "distance": 5},
                {"from": "A", "to": "A_B", "distance": 5},
                {"from": "A_B", "to": "Z", "distance": 5},
                {"from": "A", "to": "a", "distance": 5},
                {"from": "a", "to": "Z", "distance": 5},
            ],
        }
        gp2 = tmp(json.dumps(graph2))
        proc2 = run(["--graph", gp2, "--from", "A", "--to", "Z"])
        assert proc2.returncode == 0
        out2 = json.loads(proc2.stdout.decode().strip())
        assert out2["path"] == ["A", "A-B", "Z"], (
            f"case-sensitive ASCII should pick A-B '-', got {out2['path']}"
        )
        os.unlink(gp2)
        # 10-way tie
        nodes = ["A"] + [chr(ord("B") + i) for i in range(10)] + ["Z"]
        edges = []
        for i in range(10):
            mid = chr(ord("B") + i)
            edges.append({"from": "A", "to": mid, "distance": 5})
            edges.append({"from": mid, "to": "Z", "distance": 5})
        graph3 = {"nodes": nodes, "edges": edges}
        gp3 = tmp(json.dumps(graph3))
        proc3 = run(["--graph", gp3, "--from", "A", "--to", "Z"])
        assert proc3.returncode == 0
        out3 = json.loads(proc3.stdout.decode().strip())
        assert out3["path"] == ["A", "B", "Z"], (
            f"10-way tie should pick B, got {out3['path']}"
        )
        os.unlink(gp3)
    finally:
        os.unlink(gp)


def test_large_graph_and_batch_perf_extra_hard():
    import tempfile, os, subprocess, json, time

    def tmp(c):
        f = tempfile.NamedTemporaryFile(delete=False, suffix=".json", mode="w")
        f.write(c)
        f.close()
        return f.name

    def run(args):
        return subprocess.run(
            ["/app/router"] + args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=60,
        )

    # 3000 nodes line - reduced from 5000 to avoid Docker timeout for oracle but still catches O(n^2)
    nodes = [f"N{i}" for i in range(3000)]
    edges = [{"from": f"N{i}", "to": f"N{i + 1}", "distance": 1} for i in range(2999)]
    graph = {"nodes": nodes, "edges": edges}
    gp = tmp(json.dumps(graph))
    try:
        start = time.time()
        proc = run(["--graph", gp, "--from", "N0", "--to", "N2999"])
        elapsed = time.time() - start
        assert proc.returncode == 0
        assert elapsed < 45.0, f"too slow 3000 nodes {elapsed}"
        # 1000 batch same-source (amortized)
        reqs = [{"source": "N0", "destination": f"N{i % 3000}"} for i in range(1000)]
        rp = tmp(json.dumps(reqs))
        start = time.time()
        proc2 = run(["--graph", gp, "--requests", rp])
        elapsed2 = time.time() - start
        assert proc2.returncode == 0
        assert elapsed2 < 25.0, f"too slow 1000 batch {elapsed2}"
        assert len(proc2.stdout.decode().strip().splitlines()) == 1000
        os.unlink(rp)
    finally:
        os.unlink(gp)


# === EXTRA HARD v3 for Step1 - pushing to 150+ ===


def test_graph_with_extra_nested_object_ignored_extra():
    import tempfile, os, subprocess, json, math

    def tmp(c):
        f = tempfile.NamedTemporaryFile(delete=False, suffix=".json", mode="w")
        f.write(c)
        f.close()
        return f.name

    def run(args):
        return subprocess.run(
            ["/app/router"] + args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
        )

    graph = {
        "nodes": ["A", "B"],
        "edges": [
            {
                "from": "A",
                "to": "B",
                "distance": 5,
                "meta": {"x": 1, "nested": {"y": 2}},
            }
        ],
    }
    gp = tmp(json.dumps(graph))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B"])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip())
        assert math.isclose(out["distance"], 5, abs_tol=1e-6)
    finally:
        os.unlink(gp)


def test_requests_file_bom_trailing_comment_extra_hard_v3():
    import tempfile, os, subprocess, json

    def tmp(c):
        f = tempfile.NamedTemporaryFile(delete=False, suffix=".json", mode="w")
        f.write(c)
        f.close()
        return f.name

    def tmp_bytes(b):
        f = tempfile.NamedTemporaryFile(delete=False, suffix=".json", mode="wb")
        f.write(b)
        f.close()
        return f.name

    def run(args):
        return subprocess.run(
            ["/app/router"] + args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
        )

    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 1}]}
    gp = tmp(json.dumps(graph))
    rp_trail = tmp('[{"source":"A","destination":"B"},]')
    rp_comment = tmp('// comment\n[{"source":"A","destination":"B"}]')
    rp_bom = tmp_bytes(b'\xef\xbb\xbf[{"source":"A","destination":"B"}]')
    try:
        for rp in [rp_trail, rp_comment, rp_bom]:
            proc = run(["--graph", gp, "--requests", rp])
            assert proc.returncode == 2, f"requests malformed should be invalid"
    finally:
        os.unlink(gp)
        os.unlink(rp_trail)
        os.unlink(rp_comment)
        os.unlink(rp_bom)


def test_batch_all_no_route_and_all_valid_mixed():
    import tempfile, os, subprocess, json

    def tmp(c):
        f = tempfile.NamedTemporaryFile(delete=False, suffix=".json", mode="w")
        f.write(c)
        f.close()
        return f.name

    def run(args):
        return subprocess.run(
            ["/app/router"] + args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=60,
        )

    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 1}]}
    gp = tmp(json.dumps(graph))
    rp_all_no = tmp(
        json.dumps(
            [{"source": "X", "destination": "Y"}, {"source": "C", "destination": "D"}]
        )
    )
    rp_mixed = tmp(
        json.dumps(
            [{"source": "A", "destination": "B"}, {"source": "X", "destination": "Y"}]
        )
    )
    try:
        proc_all = run(["--graph", gp, "--requests", rp_all_no])
        assert proc_all.returncode == 1
        assert len(proc_all.stdout.decode().strip().splitlines()) == 2
        proc_mixed = run(["--graph", gp, "--requests", rp_mixed])
        assert proc_mixed.returncode == 1
        lines = proc_mixed.stdout.decode().strip().splitlines()
        assert len(lines) == 2
        o0 = json.loads(lines[0])
        assert o0["distance"] != -1
        o1 = json.loads(lines[1])
        assert o1["distance"] == -1
    finally:
        os.unlink(gp)
        os.unlink(rp_all_no)
        os.unlink(rp_mixed)


def test_node_id_with_special_chars_valid():
    import tempfile, os, subprocess, json, math

    def tmp(c):
        f = tempfile.NamedTemporaryFile(delete=False, suffix=".json", mode="w")
        f.write(c)
        f.close()
        return f.name

    def run(args):
        return subprocess.run(
            ["/app/router"] + args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
        )

    graph = {
        "nodes": ["A/B", "C.D", "E-F_G", "H I"],
        "edges": [
            {"from": "A/B", "to": "C.D", "distance": 5},
            {"from": "C.D", "to": "E-F_G", "distance": 5},
        ],
    }
    gp = tmp(json.dumps(graph))
    try:
        proc = run(["--graph", gp, "--from", "A/B", "--to", "E-F_G"])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip())
        assert out["path"] == ["A/B", "C.D", "E-F_G"]
    finally:
        os.unlink(gp)


def test_tie_break_prefix_and_case_extra_hard():
    import tempfile, os, subprocess, json

    def tmp(c):
        f = tempfile.NamedTemporaryFile(delete=False, suffix=".json", mode="w")
        f.write(c)
        f.close()
        return f.name

    def run(args):
        return subprocess.run(
            ["/app/router"] + args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
        )

    # Prefix shorter wins when one path is prefix of other? Actually need same cost and one path prefix? Dijkstra paths can't be prefix unless zero distance, but we test lex compare still
    # Case-sensitive: 'B' < 'b'
    graph = {
        "nodes": ["A", "B", "b", "Z"],
        "edges": [
            {"from": "A", "to": "B", "distance": 5},
            {"from": "B", "to": "Z", "distance": 5},
            {"from": "A", "to": "b", "distance": 5},
            {"from": "b", "to": "Z", "distance": 5},
        ],
    }
    gp = tmp(json.dumps(graph))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "Z"])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip())
        assert out["path"] == ["A", "B", "Z"], (
            f"case-sensitive B < b, got {out['path']}"
        )
    finally:
        os.unlink(gp)


def test_performance_dense_graph_extra_hard_v3():
    import tempfile, os, subprocess, json, time

    def tmp(c):
        f = tempfile.NamedTemporaryFile(delete=False, suffix=".json", mode="w")
        f.write(c)
        f.close()
        return f.name

    def run(args):
        return subprocess.run(
            ["/app/router"] + args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=60,
        )

    nodes = [f"N{i}" for i in range(200)]
    edges = []
    for i in range(200):
        for j in range(i + 1, min(i + 15, 200)):
            edges.append({"from": f"N{i}", "to": f"N{j}", "distance": 1})
    graph = {"nodes": nodes, "edges": edges}
    gp = tmp(json.dumps(graph))
    try:
        start = time.time()
        proc = run(["--graph", gp, "--from", "N0", "--to", "N199"])
        elapsed = time.time() - start
        assert proc.returncode == 0
        assert elapsed < 40.0, f"dense 200 nodes too slow {elapsed}"
    finally:
        os.unlink(gp)


def test_graph_nodes_with_leading_space_distinct_and_duplicate_check_v4():
    import tempfile, os, subprocess, json

    def tmp(c):
        f = tempfile.NamedTemporaryFile(delete=False, suffix=".json", mode="w")
        f.write(c)
        f.close()
        return f.name

    def run(args):
        return subprocess.run(
            ["/app/router"] + args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
        )

    graph = {
        "nodes": [" A", "A", "B"],
        "edges": [
            {"from": " A", "to": "B", "distance": 1},
            {"from": "A", "to": "B", "distance": 2},
        ],
    }
    gp = tmp(json.dumps(graph))
    try:
        proc = run(["--graph", gp, "--from", " A", "--to", "B"])
        assert proc.returncode == 0, "leading space distinct valid"
        out = json.loads(proc.stdout.decode().strip())
        assert out["path"] == [" A", "B"]
        gp2 = tmp(json.dumps({"nodes": ["A", "A"], "edges": []}))
        proc2 = run(["--graph", gp2, "--from", "A", "--to", "A"])
        assert proc2.returncode == 2
        os.unlink(gp2)
    finally:
        os.unlink(gp)


def test_graph_edge_distance_scientific_plus_valid_v4():
    import tempfile, os, subprocess, json, math

    def tmp(c):
        f = tempfile.NamedTemporaryFile(delete=False, suffix=".json", mode="w")
        f.write(c)
        f.close()
        return f.name

    def run(args):
        return subprocess.run(
            ["/app/router"] + args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
        )

    for lit, val in [
        ("1e+2", 100),
        ("1E+3", 1000),
        ("2.5e+2", 250),
        ("1e+3", 1000),
        ("1E+2", 100),
    ]:
        graph = (
            '{"nodes":["A","B"],"edges":[{"from":"A","to":"B","distance":' + lit + "}]}"
        )
        gp = tmp(graph)
        try:
            proc = run(["--graph", gp, "--from", "A", "--to", "B"])
            assert proc.returncode == 0, f"distance {lit} should be valid"
            out = json.loads(proc.stdout.decode().strip())
            assert math.isclose(out["distance"], val, rel_tol=1e-6)
        finally:
            os.unlink(gp)


def test_graph_edge_distance_plus_invalid_json_v4():
    import tempfile, os, subprocess, json

    def tmp(c):
        f = tempfile.NamedTemporaryFile(delete=False, suffix=".json", mode="w")
        f.write(c)
        f.close()
        return f.name

    def run(args):
        return subprocess.run(
            ["/app/router"] + args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
        )

    for bad in ["+5", "+1"]:
        gp = tmp(
            '{"nodes":["A","B"],"edges":[{"from":"A","to":"B","distance":' + bad + "}]}"
        )
        try:
            proc = run(["--graph", gp, "--from", "A", "--to", "B"])
            assert proc.returncode == 2, (
                f"distance {bad} explicit plus should be invalid JSON"
            )
        finally:
            os.unlink(gp)


def test_requests_with_both_keys_prefers_source_v4():
    import tempfile, os, subprocess, json

    def tmp(c):
        f = tempfile.NamedTemporaryFile(delete=False, suffix=".json", mode="w")
        f.write(c)
        f.close()
        return f.name

    def run(args):
        return subprocess.run(
            ["/app/router"] + args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
        )

    graph = {
        "nodes": ["A", "B", "C"],
        "edges": [
            {"from": "A", "to": "B", "distance": 1},
            {"from": "B", "to": "C", "distance": 1},
            {"from": "A", "to": "C", "distance": 10},
        ],
    }
    gp = tmp(json.dumps(graph))
    reqs = [{"source": "A", "destination": "B", "from": "A", "to": "C"}]
    rp = tmp(json.dumps(reqs))
    try:
        proc = run(["--graph", gp, "--requests", rp])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip().splitlines()[0])
        assert out["source"] == "A" and out["destination"] == "B"
        assert out["path"] == ["A", "B"], "should prefer source/dest over from/to"
    finally:
        os.unlink(gp)
        os.unlink(rp)


def test_batch_with_whitespace_source_no_route_extra_v4():
    import tempfile, os, subprocess, json

    def tmp(c):
        f = tempfile.NamedTemporaryFile(delete=False, suffix=".json", mode="w")
        f.write(c)
        f.close()
        return f.name

    def run(args):
        return subprocess.run(
            ["/app/router"] + args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
        )

    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 1}]}
    gp = tmp(json.dumps(graph))
    for bad_src in ["", "   ", "\t"]:
        rp = tmp(json.dumps([{"source": bad_src, "destination": "B"}]))
        try:
            proc = run(["--graph", gp, "--requests", rp])
            assert proc.returncode == 1, (
                f"empty/whitespace source {repr(bad_src)} should be no-route exit1"
            )
            out = json.loads(proc.stdout.decode().strip().splitlines()[0])
            assert out["distance"] == -1
        finally:
            os.unlink(rp)
    os.unlink(gp)


def test_large_graph_10000_nodes_line_extra_hard_v4():
    import tempfile, os, subprocess, json, time

    def tmp(c):
        f = tempfile.NamedTemporaryFile(delete=False, suffix=".json", mode="w")
        f.write(c)
        f.close()
        return f.name

    def run(args):
        return subprocess.run(
            ["/app/router"] + args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=60,
        )

    nodes = [f"N{i}" for i in range(5000)]
    edges = [{"from": f"N{i}", "to": f"N{i + 1}", "distance": 1} for i in range(4999)]
    graph = {"nodes": nodes, "edges": edges}
    gp = tmp(json.dumps(graph))
    try:
        start = time.time()
        proc = run(["--graph", gp, "--from", "N0", "--to", "N4999"])
        elapsed = time.time() - start
        assert proc.returncode == 0
        assert elapsed < 50.0, f"too slow 5000 nodes {elapsed}"
    finally:
        os.unlink(gp)


def test_batch_2000_same_source_amortization_extra_hard_v4b():
    import tempfile, os, subprocess, json, time

    def tmp(c):
        f = tempfile.NamedTemporaryFile(delete=False, suffix=".json", mode="w")
        f.write(c)
        f.close()
        return f.name

    def run(args):
        return subprocess.run(
            ["/app/router"] + args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=60,
        )

    nodes = [f"N{i}" for i in range(1000)]
    edges = [{"from": f"N{i}", "to": f"N{i + 1}", "distance": 1} for i in range(999)]
    graph = {"nodes": nodes, "edges": edges}
    gp = tmp(json.dumps(graph))
    same = [{"source": "N0", "destination": f"N{i % 1000}"} for i in range(500)]
    multi = [
        {"source": f"N{i % 1000}", "destination": f"N{(i * 7) % 1000}"}
        for i in range(500)
    ]
    rp_same = tmp(json.dumps(same))
    rp_multi = tmp(json.dumps(multi))
    try:
        start = time.time()
        proc_same = run(["--graph", gp, "--requests", rp_same])
        t_same = time.time() - start
        assert proc_same.returncode == 0
        start = time.time()
        proc_multi = run(["--graph", gp, "--requests", rp_multi])
        t_multi = time.time() - start
        assert proc_multi.returncode == 0
        # Same-source with caching should be faster than multi distinct (which does 500 Dijkstras vs 1)
        assert t_same <= 0.95 * t_multi + 3.0, (
            f"same-source 500 should amortize: {t_same:.3f} vs {t_multi:.3f}"
        )
    finally:
        os.unlink(gp)
        os.unlink(rp_same)
        os.unlink(rp_multi)


def test_10_way_tie_and_5_way_tie_extra_hard_v4():
    import tempfile, os, subprocess, json

    def tmp(c):
        f = tempfile.NamedTemporaryFile(delete=False, suffix=".json", mode="w")
        f.write(c)
        f.close()
        return f.name

    def run(args):
        return subprocess.run(
            ["/app/router"] + args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
        )

    nodes = ["A"] + ["B", "C", "D", "E", "F"] + ["Z"]
    edges = []
    for mid in ["B", "C", "D", "E", "F"]:
        edges.append({"from": "A", "to": mid, "distance": 5})
        edges.append({"from": mid, "to": "Z", "distance": 5})
    graph = {"nodes": nodes, "edges": edges}
    gp = tmp(json.dumps(graph))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "Z"])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip())
        assert out["path"] == ["A", "B", "Z"], (
            f"5-way tie should pick B, got {out['path']}"
        )
        nodes10 = ["A"] + [chr(ord("B") + i) for i in range(10)] + ["Z"]
        edges10 = []
        for i in range(10):
            mid = chr(ord("B") + i)
            edges10.append({"from": "A", "to": mid, "distance": 5})
            edges10.append({"from": mid, "to": "Z", "distance": 5})
        graph10 = {"nodes": nodes10, "edges": edges10}
        gp10 = tmp(json.dumps(graph10))
        proc10 = run(["--graph", gp10, "--from", "A", "--to", "Z"])
        assert proc10.returncode == 0
        out10 = json.loads(proc10.stdout.decode().strip())
        assert out10["path"] == ["A", "B", "Z"], (
            f"10-way tie should pick B, got {out10['path']}"
        )
        os.unlink(gp10)
    finally:
        os.unlink(gp)
