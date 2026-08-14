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

APP_DIR = "/app"
GO_ENV = os.environ.copy()
GO_ENV["GOCACHE"] = "/tmp/codimango/gocache"
GO_ENV["GOPATH"] = "/tmp/codimango/gopath"


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
        start = time.time()
        base = run(["--graph", gp, "--requests", rp1])
        base_elapsed = time.time() - start
        assert base.returncode == 0, base.stderr.decode()[:500]

        start = time.time()
        proc = run(["--graph", gp, "--requests", rp])
        elapsed = time.time() - start
        assert proc.returncode == 0, proc.stderr.decode()[:500]
        assert elapsed <= 25 * base_elapsed + 1.0, (
            f"batch of 100 too slow vs single request: {elapsed:.3f}s vs baseline {base_elapsed:.3f}s"
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
            [BIN] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10
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
            [BIN] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15
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


def test_batch_with_missing_field_invalid():
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
    rp = tmp(json.dumps([{"source": "A"}]))  # missing destination
    try:
        proc = run(["--graph", gp, "--requests", rp])
        assert proc.returncode == 2, (
            f"missing destination should be invalid, rc={proc.returncode}"
        )
    finally:
        os.unlink(gp)
        os.unlink(rp)


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
            [BIN] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5
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
            [BIN] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10
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
            [BIN] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15
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
            [BIN] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5
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
            [BIN] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5
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
            [BIN] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5
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
            [BIN] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5
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


def test_large_graph_2000_nodes():
    nodes = [f"N{i}" for i in range(2000)]
    edges = [{"from": f"N{i}", "to": f"N{i + 1}", "distance": 1} for i in range(1999)]
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
            [BIN] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10
        )

    gp = tmp(json.dumps(graph))
    try:
        start = time.time()
        proc = run(["--graph", gp, "--from", "N0", "--to", "N1999"])
        elapsed = time.time() - start
        assert proc.returncode == 0
        assert elapsed < 3.0, f"too slow 2000 nodes {elapsed}"
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
            [BIN] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20
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
        timeout=5,
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
            [BIN] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5
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
            [BIN] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5
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
            [BIN] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5
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
            [BIN] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5
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
            [BIN] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5
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


def test_graph_with_bom():
    # JSON with UTF-8 BOM should be invalid (Go json doesn't handle BOM) -> exit 2
    import tempfile, os, subprocess

    BIN = "/app/router"
    gf = tempfile.NamedTemporaryFile(delete=False, suffix=".json", mode="wb")
    gf.write(
        b'\xef\xbb\xbf{"nodes":["A","B"],"edges":[{"from":"A","to":"B","distance":1}]}'
    )
    gf.close()
    try:
        proc = subprocess.run(
            [BIN, "--graph", gf.name, "--from", "A", "--to", "B"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=5,
        )
        # Accept either invalid (2) or valid (0) depending on implementation stripping BOM — but must not crash and must be deterministic
        assert proc.returncode in (0, 2)
        if proc.returncode == 0:
            import json

            out = json.loads(proc.stdout.decode().strip())
            assert out["path"] == ["A", "B"]
    finally:
        os.unlink(gf.name)


def test_source_equals_dest_with_extra():
    graph = {"nodes": ["A"], "edges": []}
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
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "A"])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip())
        assert out["path"] == ["A"] and out["distance"] == 0
    finally:
        os.unlink(gp)


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
            [BIN] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5
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
            [BIN] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5
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
            [BIN] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5
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
            [BIN] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5
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
            [BIN] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5
        )

    gp = tmp(json.dumps(graph))
    rp = tmp(json.dumps({"source": "A", "destination": "B"}))
    try:
        proc = run(["--graph", gp, "--requests", rp])
        assert proc.returncode == 2
    finally:
        os.unlink(gp)
        os.unlink(rp)


def test_batch_2000_requests_perf():
    nodes = [f"N{i}" for i in range(200)]
    edges = [{"from": f"N{i}", "to": f"N{i + 1}", "distance": 1} for i in range(199)]
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
            [BIN] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30
        )

    gp = tmp(json.dumps(graph))
    reqs = [{"source": "N0", "destination": f"N{i % 200}"} for i in range(2000)]
    rp = tmp(json.dumps(reqs))
    try:
        start = time.time()
        proc = run(["--graph", gp, "--requests", rp])
        elapsed = time.time() - start
        assert proc.returncode == 0, (
            f"rc={proc.returncode} stderr={proc.stderr.decode()[:200]}"
        )
        assert elapsed < 6.0, f"too slow 2000 batch {elapsed}"
        assert len(proc.stdout.decode().strip().splitlines()) == 2000
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
            [BIN] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5
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


def test_graph_dense_5000_edges():
    nodes = [f"N{i}" for i in range(100)]
    edges = []
    for i in range(100):
        for j in range(i + 1, min(i + 10, 100)):
            edges.append({"from": f"N{i}", "to": f"N{j}", "distance": float(j - i)})
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
            [BIN] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10
        )

    gp = tmp(json.dumps(graph))
    try:
        start = time.time()
        proc = run(["--graph", gp, "--from", "N0", "--to", "N99"])
        elapsed = time.time() - start
        assert proc.returncode == 0
        assert elapsed < 1.5, (
            f"too slow dense 5000 edges {elapsed} (need <1.5s for giga hard)"
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
            [BIN] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5
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


def test_graph_with_unicode_emoji_nodes():
    graph = {
        "nodes": ["A😀", "B🚀", "C"],
        "edges": [
            {"from": "A😀", "to": "B🚀", "distance": 2},
            {"from": "B🚀", "to": "C", "distance": 3},
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
    try:
        proc = run(["--graph", gp, "--from", "A😀", "--to", "C"])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip())
        assert out["path"] == ["A😀", "B🚀", "C"]
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
            [BIN] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5
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
            [BIN] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5
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
            [BIN] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5
        )

    gp = tmp(json.dumps(graph))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B"])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip())
        assert math.isclose(out["distance"], 1e12, rel_tol=1e-9)
    finally:
        os.unlink(gp)


def test_batch_1500_requests():
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
            [BIN] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20
        )

    gp = tmp(json.dumps(graph))
    reqs = [{"source": "N0", "destination": f"N{i % 100}"} for i in range(1500)]
    rp = tmp(json.dumps(reqs))
    try:
        start = time.time()
        proc = run(["--graph", gp, "--requests", rp])
        elapsed = time.time() - start
        assert proc.returncode == 0
        assert elapsed < 5.0, f"too slow 1500 batch {elapsed}"
        assert len(proc.stdout.decode().strip().splitlines()) == 1500
    finally:
        os.unlink(gp)
        os.unlink(rp)


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
            [BIN] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5
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
            [BIN] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5
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
            [BIN] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5
        )

    gp = tmp(json.dumps(graph))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B"])
        assert proc.returncode == 2
    finally:
        os.unlink(gp)


def test_distance_nan_inf_invalid():
    import tempfile, os, subprocess

    BIN = "/app/router"
    gf = tempfile.NamedTemporaryFile(delete=False, suffix=".json", mode="w")
    gf.write('{"nodes":["A","B"],"edges":[{"from":"A","to":"B","distance":NaN}]}')
    gf.close()
    try:
        proc = subprocess.run(
            [BIN, "--graph", gf.name, "--from", "A", "--to", "B"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=5,
        )
        assert proc.returncode == 2
    finally:
        os.unlink(gf.name)


def test_help_case_insensitive():
    import subprocess

    BIN = "/app/router"
    for h in ["--help", "-h", "help"]:
        proc = subprocess.run(
            [BIN, h], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5
        )
        assert proc.returncode == 0, f"{h} should be help, rc={proc.returncode}"
        assert "graph" in proc.stdout.decode().lower()
    # Uppercase variants should be invalid (case-sensitive flags)
    for h in ["--HELP", "-H", "HELP"]:
        proc = subprocess.run(
            [BIN, h], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5
        )
        assert proc.returncode == 2, f"{h} should be invalid, rc={proc.returncode}"
    # Bare no args -> help
    proc = subprocess.run(
        [BIN], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5
    )
    assert proc.returncode == 0
    assert "graph" in proc.stdout.decode().lower()


def test_batch_with_comments_invalid():
    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 1}]}
    import tempfile, os, subprocess

    BIN = "/app/router"
    rf = tempfile.NamedTemporaryFile(delete=False, suffix=".json", mode="w")
    rf.write('[/*comment*/{"source":"A","destination":"B"}]')
    rf.close()
    gf = tempfile.NamedTemporaryFile(delete=False, suffix=".json", mode="w")
    import json

    gf.write(json.dumps(graph))
    gf.close()
    try:
        proc = subprocess.run(
            [BIN, "--graph", gf.name, "--requests", rf.name],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=5,
        )
        assert proc.returncode == 2
    finally:
        os.unlink(gf.name)
        os.unlink(rf.name)


def test_large_graph_5000_nodes():
    nodes = [f"N{i}" for i in range(5000)]
    edges = [{"from": f"N{i}", "to": f"N{i + 1}", "distance": 1} for i in range(4999)]
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
            [BIN] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15
        )

    gp = tmp(json.dumps(graph))
    try:
        start = time.time()
        proc = run(["--graph", gp, "--from", "N0", "--to", "N4999"])
        elapsed = time.time() - start
        assert proc.returncode == 0
        assert elapsed < 4.0, f"too slow 5000 nodes {elapsed}"
    finally:
        os.unlink(gp)


# === HARDENING step1 too easy, step2 good: add 20 more hard discriminators ===


def test_large_batch_5000_requests_relative():
    nodes = [f"N{i}" for i in range(200)]
    edges = [{"from": f"N{i}", "to": f"N{i + 1}", "distance": 1} for i in range(199)]
    graph = {"nodes": nodes, "edges": edges}
    gp = tmp(json.dumps(graph))
    rp1 = tmp(json.dumps([{"source": "N0", "destination": "N199"}]))
    rp = tmp(
        json.dumps(
            [{"source": "N0", "destination": f"N{i % 200}"} for i in range(5000)]
        )
    )
    try:
        start = time.time()
        base = run(["--graph", gp, "--requests", rp1])
        base_elapsed = time.time() - start
        assert base.returncode == 0

        start = time.time()
        proc = run(["--graph", gp, "--requests", rp])
        elapsed = time.time() - start
        assert proc.returncode == 0, proc.stderr.decode()[:500]
        assert elapsed <= 200 * base_elapsed + 5.0, (
            f"5000 batch too slow vs single: {elapsed:.3f}s vs baseline {base_elapsed:.3f}s"
        )
        assert len(proc.stdout.decode().strip().splitlines()) == 5000
    finally:
        os.unlink(gp)
        os.unlink(rp1)
        os.unlink(rp)


def test_graph_with_comments_invalid():
    # JSON with // comments invalid, must be exit 2
    content = '{"nodes":["A","B"],"edges":[ // comment\n {"from":"A","to":"B","distance":1} ] }'
    gp = tmp(content)
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B"])
        assert proc.returncode == 2, (
            f"// comment should be invalid, got {proc.returncode}"
        )
        assert proc.stdout.decode().strip() == ""
    finally:
        os.unlink(gp)


def test_graph_with_trailing_comma_invalid():
    cases = [
        '{"nodes":["A","B"],"edges":[{"from":"A","to":"B","distance":1},]}',
        '{"nodes":["A","B",],"edges":[]}',
    ]
    for content in cases:
        gp = tmp(content)
        try:
            proc = run(["--graph", gp, "--from", "A", "--to", "B"])
            assert proc.returncode == 2, (
                f"trailing comma should be invalid, got {proc.returncode} for {content[:50]}"
            )
        finally:
            os.unlink(gp)


def test_flag_order_independence():
    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 5}]}
    gp = tmp(json.dumps(graph))
    try:
        # from before graph
        proc = run(["--from", "A", "--graph", gp, "--to", "B"])
        assert proc.returncode == 0, (
            f"flag order independence should work, rc={proc.returncode}"
        )
        out = json.loads(proc.stdout.decode().strip())
        assert out["path"] == ["A", "B"]

        # requests before graph
        rp = tmp(json.dumps([{"source": "A", "destination": "B"}]))
        try:
            proc2 = run(["--requests", rp, "--graph", gp])
            assert proc2.returncode == 0
        finally:
            os.unlink(rp)

        # equals and space mixed
        proc3 = run(["--from=A", "--graph", gp, "--to=B"])
        assert proc3.returncode == 0
    finally:
        os.unlink(gp)


def test_help_with_extra_and_requests_flag():
    # Help with extra flags should still be help exit 0
    proc = run(
        [
            "--help",
            "--graph",
            "dummy",
            "--from",
            "A",
            "--to",
            "B",
            "--requests",
            "dummy",
        ]
    )
    assert proc.returncode == 0
    out = proc.stdout.decode().lower()
    for kw in ["graph", "from", "to", "requests", "help"]:
        assert kw in out

    proc2 = run(["-h", "--graph", "dummy"])
    assert proc2.returncode == 0
    assert "graph" in proc2.stdout.decode().lower()


def test_unknown_flag_equals_with_requests():
    gp = tmp(
        json.dumps(
            {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 1}]}
        )
    )
    try:
        for flag in ["--unknown=val", "--foobar=xxx", "--graph=nonexist --unknown=1"]:
            # single unknown with equals
            if " " in flag:
                parts = flag.split()
                proc = run(parts)
            else:
                proc = run([flag, "--graph", gp, "--from", "A", "--to", "B"])
            assert proc.returncode == 2, (
                f"{flag} should be unknown -> 2, got {proc.returncode}"
            )
    finally:
        os.unlink(gp)


def test_batch_with_both_empty_and_spaces_no_route():
    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 1}]}
    gp = tmp(json.dumps(graph))
    cases = [
        [{"source": "", "destination": ""}],
        [{"source": "   ", "destination": "   "}],
        [{"source": "", "destination": "B"}],
        [{"source": "A", "destination": ""}],
        [{"source": "   ", "destination": "B"}],
        [{"source": "A", "destination": "   "}],
    ]
    try:
        for req in cases:
            rp = tmp(json.dumps(req))
            try:
                proc = run(["--graph", gp, "--requests", rp])
                assert proc.returncode == 1, (
                    f"empty/whitespace should be no-route exit1, got {proc.returncode} for {req}"
                )
                out = json.loads(proc.stdout.decode().strip())
                assert out["path"] == [] and out["distance"] == -1
            finally:
                os.unlink(rp)
    finally:
        os.unlink(gp)


def test_graph_nodes_with_slash_and_dot():
    graph = {
        "nodes": ["a/b", "c.d", "e-f_g", "h"],
        "edges": [
            {"from": "a/b", "to": "c.d", "distance": 1},
            {"from": "c.d", "to": "e-f_g", "distance": 1},
            {"from": "e-f_g", "to": "h", "distance": 1},
        ],
    }
    gp = tmp(json.dumps(graph))
    try:
        proc = run(["--graph", gp, "--from", "a/b", "--to", "h"])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip())
        assert out["path"] == ["a/b", "c.d", "e-f_g", "h"]
        assert out["distance"] == 3
    finally:
        os.unlink(gp)


def test_empty_graph_file_whitespace_invalid():
    gp = tmp("   \n\t  ")
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B"])
        assert proc.returncode == 2, (
            f"whitespace graph file should be invalid, got {proc.returncode}"
        )
        assert proc.stdout.decode().strip() == ""
    finally:
        os.unlink(gp)


def test_large_distance_1e100_valid():
    graph = {
        "nodes": ["A", "B"],
        "edges": [{"from": "A", "to": "B", "distance": 1e100}],
    }
    gp = tmp(json.dumps(graph))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B"])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip())
        assert math.isclose(out["distance"], 1e100, rel_tol=1e-9)
    finally:
        os.unlink(gp)


def test_float_negative_zero_distance_invalid():
    # -0.0 is 0, invalid (distance must be >0)
    for content in [
        '{"nodes":["A","B"],"edges":[{"from":"A","to":"B","distance":-0.0}]}',
        '{"nodes":["A","B"],"edges":[{"from":"A","to":"B","distance":-0}]}',
        '{"nodes":["A","B"],"edges":[{"from":"A","to":"B","distance":0}]}',
    ]:
        gp = tmp(content)
        try:
            proc = run(["--graph", gp, "--from", "A", "--to", "B"])
            assert proc.returncode == 2, (
                f"-0 distance should be invalid, got {proc.returncode}"
            )
        finally:
            os.unlink(gp)


def test_case_sensitive_tie_break():
    # A vs a distinct, lex order B < C but case-sensitive: uppercase < lowercase? ASCII B=66, a=97, so B < a
    graph = {
        "nodes": ["A", "B", "a", "D"],
        "edges": [
            {"from": "A", "to": "B", "distance": 5},
            {"from": "B", "to": "D", "distance": 5},
            {"from": "A", "to": "a", "distance": 5},
            {"from": "a", "to": "D", "distance": 5},
        ],
    }
    gp = tmp(json.dumps(graph))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "D"])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip())
        # B (66) < a (97) so A-B-D wins
        assert out["path"] == ["A", "B", "D"], (
            f"case sensitive lex: expected B < a, got {out['path']}"
        )
    finally:
        os.unlink(gp)


def test_batch_with_unicode_and_special():
    graph = {
        "nodes": ["A", "B-1", "C_2", "D.e"],
        "edges": [
            {"from": "A", "to": "B-1", "distance": 1},
            {"from": "B-1", "to": "C_2", "distance": 1},
            {"from": "C_2", "to": "D.e", "distance": 1},
        ],
    }
    gp = tmp(json.dumps(graph))
    rp = tmp(
        json.dumps(
            [
                {"source": "A", "destination": "D.e"},
                {"source": "B-1", "destination": "C_2"},
            ]
        )
    )
    try:
        proc = run(["--graph", gp, "--requests", rp])
        assert proc.returncode == 0
        lines = proc.stdout.decode().strip().splitlines()
        assert len(lines) == 2
        out = json.loads(lines[0])
        assert out["path"] == ["A", "B-1", "C_2", "D.e"]
    finally:
        os.unlink(gp)
        os.unlink(rp)


def test_perf_dense_500_nodes():
    nodes = [f"N{i}" for i in range(200)]
    edges = []
    for i in range(200):
        for j in range(i + 1, min(i + 10, 200)):
            edges.append({"from": f"N{i}", "to": f"N{j}", "distance": float(j - i)})
    graph = {"nodes": nodes, "edges": edges}
    gp = tmp(json.dumps(graph))
    try:
        start = time.time()
        proc = run(["--graph", gp, "--from", "N0", "--to", "N199"])
        elapsed = time.time() - start
        assert proc.returncode == 0
        assert elapsed < 2.0, f"dense 200 nodes too slow {elapsed}"
    finally:
        os.unlink(gp)


def test_batch_5000_requests_relative_step1():
    nodes = [f"N{i}" for i in range(200)]
    edges = [{"from": f"N{i}", "to": f"N{i + 1}", "distance": 1} for i in range(199)]
    graph = {"nodes": nodes, "edges": edges}
    gp = tmp(json.dumps(graph))
    rp1 = tmp(json.dumps([{"source": "N0", "destination": "N199"}]))
    rp = tmp(
        json.dumps(
            [{"source": "N0", "destination": f"N{i % 200}"} for i in range(5000)]
        )
    )
    try:
        start = time.time()
        base = run(["--graph", gp, "--requests", rp1])
        base_elapsed = time.time() - start
        assert base.returncode == 0

        start = time.time()
        proc = run(["--graph", gp, "--requests", rp])
        elapsed = time.time() - start
        assert proc.returncode == 0
        assert elapsed <= 200 * base_elapsed + 5.0, (
            f"5000 batch too slow vs single: {elapsed:.3f}s vs baseline {base_elapsed:.3f}s"
        )
        assert len(proc.stdout.decode().strip().splitlines()) == 5000
    finally:
        os.unlink(gp)
        os.unlink(rp1)
        os.unlink(rp)


def test_stdlib_only():
    # TBR concern: stdlib-only / no-external-require MUST not tested - now tested
    go_mod_path = os.path.join(APP_DIR, "go.mod")
    assert os.path.exists(go_mod_path), "go.mod must exist in /app"
    with open(go_mod_path) as f:
        content = f.read()
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("require "):
            parts = stripped.split()
            if len(parts) >= 2:
                mod = parts[1]
                first = mod.split("/")[0]
                if "." in first:
                    assert False, f"External dep in go.mod: {mod} - must be stdlib only"
        if "github.com" in stripped or "golang.org/x" in stripped:
            if not stripped.startswith("//") and "module " not in stripped:
                if stripped.startswith("github.com") or stripped.startswith("golang.org"):
                    assert False, f"External require in go.mod: {stripped}"

    proc = subprocess.run(
        ["go", "list", "-f", "{{join .Imports \" \"}}", "."],
        cwd=APP_DIR,
        env=GO_ENV,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=15,
    )
    assert proc.returncode == 0, f"go list failed: {proc.stderr.decode()[:300]}"
    imports = proc.stdout.decode().strip().split()
    for imp in imports:
        first = imp.split("/")[0]
        assert "." not in first, f"Non-stdlib import {imp} found via go list - must be stdlib only"
