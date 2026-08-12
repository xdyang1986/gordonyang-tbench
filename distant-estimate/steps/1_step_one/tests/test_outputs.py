import os, json, subprocess, tempfile, time, math

CANDIDATES = ["/app/router", "/app/src/router", "./router", "/app/router/router"]


def find_bin():
    for p in CANDIDATES:
        if os.path.exists(p) and os.access(p, os.X_OK):
            return p
    if os.path.exists("/app/go.mod"):
        subprocess.run(
            ["go", "build", "-o", "router", "."],
            cwd="/app",
            timeout=30,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if os.path.exists("/app/router"):
            return "/app/router"
    return "/app/router"


BIN = find_bin()


def run(args, cwd="/tmp"):
    return subprocess.run(
        [BIN] + args,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=10,
    )


def tmp(content, suffix=".json"):
    f = tempfile.NamedTemporaryFile(delete=False, suffix=suffix, mode="w")
    f.write(content)
    f.close()
    return f.name


def test_binary_exists():
    assert os.path.exists(BIN)


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
            [BIN] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5
        )

    gp = tmp(json.dumps(graph))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B"])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip())
        assert math.isclose(out["distance"], 1000, abs_tol=1e-6)
    finally:
        os.unlink(gp)


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
            [BIN] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5
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
            [BIN] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5
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
            [BIN] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5
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
            [BIN] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5
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
            [BIN] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5
        )

    gp = tmp(json.dumps(graph))
    rp = tmp(json.dumps([{"source": 123, "destination": "B"}]))
    try:
        proc = run(["--graph", gp, "--requests", rp])
        assert proc.returncode == 2
    finally:
        os.unlink(gp)
        os.unlink(rp)


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
            [BIN] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5
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


