import os, json, subprocess, tempfile, time, math

CANDIDATES = ["/app/router", "/app/src/router", "./router"]


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


def test_help_contains_traffic_keyword():
    proc = run(["--help"])
    assert proc.returncode == 0
    out = proc.stdout.decode().lower()
    for kw in ["graph", "from", "to", "requests", "traffic", "help"]:
        assert kw in out, f"missing {kw}"


def test_help_bare():
    proc = run([])
    assert proc.returncode == 0
    out = proc.stdout.decode().lower()
    for kw in ["graph", "from", "to", "requests", "traffic", "help"]:
        assert kw in out


def test_simple_shortest_path_without_traffic():
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
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip())
        assert out["path"] == ["A", "B", "C"]
    finally:
        os.unlink(gp)


def test_extra_fields_graph_ignored_without_traffic():
    graph = {
        "nodes": ["A", "B"],
        "edges": [{"from": "A", "to": "B", "distance": 5, "extra": "x"}],
        "extra_top": 123,
    }
    gp = tmp(json.dumps(graph))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B"])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip())
        assert out["path"] == ["A", "B"]
    finally:
        os.unlink(gp)


def test_float_distance_without_traffic():
    graph = {
        "nodes": ["A", "B", "C"],
        "edges": [
            {"from": "A", "to": "B", "distance": 1.5},
            {"from": "B", "to": "C", "distance": 1.5},
            {"from": "A", "to": "C", "distance": 10},
        ],
    }
    gp = tmp(json.dumps(graph))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "C"])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip())
        assert out["path"] == ["A", "B", "C"]
        assert math.isclose(out["distance"], 3.0, abs_tol=1e-6)
    finally:
        os.unlink(gp)


def test_whitespace_node_invalid():
    graph = {"nodes": ["A", "   ", "B"], "edges": []}
    gp = tmp(json.dumps(graph))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B"])
        assert proc.returncode == 2
    finally:
        os.unlink(gp)


def test_unknown_flag_exit_2():
    gp = tmp(
        json.dumps(
            {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 1}]}
        )
    )
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B", "--unknown"])
        assert proc.returncode == 2
        assert proc.stdout.decode().strip() == ""
    finally:
        os.unlink(gp)


def test_tie_break_three_paths_without_traffic():
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
        assert out["path"] == ["A", "B", "D"], f"got {out['path']}"
    finally:
        os.unlink(gp)


# === Traffic specific ===


def test_traffic_changes_path():
    graph = {
        "nodes": ["A", "B", "C"],
        "edges": [
            {"from": "A", "to": "B", "distance": 6},
            {"from": "B", "to": "C", "distance": 6},
            {"from": "A", "to": "C", "distance": 5},
        ],
    }
    traffic = {
        "traffic": [
            {"from": "A", "to": "C", "factor": 10},
            {"from": "A", "to": "B", "factor": 1},
            {"from": "B", "to": "C", "factor": 1},
        ]
    }
    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps(traffic))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "C", "--traffic", tp])
        assert proc.returncode == 0, proc.stderr.decode()
        out = json.loads(proc.stdout.decode().strip())
        assert out["path"] == ["A", "B", "C"], f"got {out['path']}"
        assert out["distance"] == 12
        assert math.isclose(out["effective_distance"], 12, abs_tol=1e-6)
        assert math.isclose(out["traffic_delay"], 0, abs_tol=1e-6)
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_effective_distance_calculation():
    graph = {
        "nodes": ["A", "B", "C"],
        "edges": [
            {"from": "A", "to": "B", "distance": 10},
            {"from": "B", "to": "C", "distance": 10},
        ],
    }
    traffic = {
        "traffic": [
            {"from": "A", "to": "B", "factor": 2.5},
            {"from": "B", "to": "C", "factor": 0.5},
        ]
    }
    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps(traffic))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "C", "--traffic", tp])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip())
        assert out["distance"] == 20
        assert math.isclose(out["effective_distance"], 30, abs_tol=1e-6)
        assert math.isclose(out["traffic_delay"], 10, abs_tol=1e-6)
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_extra_fields_ignored():
    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 10}]}
    traffic = {
        "traffic": [
            {"from": "A", "to": "B", "factor": 2, "extra": "ignore", "delay": 0}
        ],
        "extra_top": "ignore",
    }
    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps(traffic))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B", "--traffic", tp])
        assert proc.returncode == 0, (
            f"extra fields should be ignored rc={proc.returncode} stderr={proc.stderr.decode()}"
        )
        out = json.loads(proc.stdout.decode().strip())
        assert math.isclose(out["effective_distance"], 20, abs_tol=1e-6)
    finally:
        import os

        os.unlink(gp)
        os.unlink(tp)


def test_traffic_factor_string_invalid():
    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 5}]}
    traffic = {"traffic": [{"from": "A", "to": "B", "factor": "2.5"}]}
    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps(traffic))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B", "--traffic", tp])
        assert proc.returncode == 2
        assert proc.stdout.decode().strip() == ""
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_factor_int_valid():
    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 5}]}
    traffic = {"traffic": [{"from": "A", "to": "B", "factor": 2}]}
    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps(traffic))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B", "--traffic", tp])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip())
        assert math.isclose(out["effective_distance"], 10, abs_tol=1e-6)
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_factor_less_than_one_negative_delay():
    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 10}]}
    traffic = {"traffic": [{"from": "A", "to": "B", "factor": 0.5}]}
    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps(traffic))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B", "--traffic", tp])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip())
        assert math.isclose(out["effective_distance"], 5, abs_tol=1e-6)
        assert math.isclose(out["traffic_delay"], -5, abs_tol=1e-6)
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_float_distance_times_factor():
    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 2.5}]}
    traffic = {"traffic": [{"from": "A", "to": "B", "factor": 2.5}]}
    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps(traffic))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B", "--traffic", tp])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip())
        assert math.isclose(out["distance"], 2.5, abs_tol=1e-6)
        assert math.isclose(out["effective_distance"], 6.25, abs_tol=1e-6)
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_invalid_negative_factor():
    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 5}]}
    traffic = {"traffic": [{"from": "A", "to": "B", "factor": -1}]}
    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps(traffic))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B", "--traffic", tp])
        assert proc.returncode == 2 and proc.stdout.decode().strip() == ""
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_invalid_zero_factor():
    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 5}]}
    traffic = {"traffic": [{"from": "A", "to": "B", "factor": 0}]}
    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps(traffic))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B", "--traffic", tp])
        assert proc.returncode == 2
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_invalid_nonexisting_edge():
    graph = {
        "nodes": ["A", "B", "C"],
        "edges": [{"from": "A", "to": "B", "distance": 5}],
    }
    traffic = {"traffic": [{"from": "B", "to": "C", "factor": 2}]}
    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps(traffic))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B", "--traffic", tp])
        assert proc.returncode == 2
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_invalid_self_loop():
    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 5}]}
    traffic = {"traffic": [{"from": "A", "to": "A", "factor": 2}]}
    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps(traffic))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B", "--traffic", tp])
        assert proc.returncode == 2
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_invalid_json():
    gp = tmp(
        json.dumps(
            {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 5}]}
        )
    )
    tp = tmp("{invalid}")
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B", "--traffic", tp])
        assert proc.returncode == 2
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_batch_mode():
    graph = {
        "nodes": ["A", "B", "C"],
        "edges": [
            {"from": "A", "to": "B", "distance": 5},
            {"from": "B", "to": "C", "distance": 5},
            {"from": "A", "to": "C", "distance": 15},
        ],
    }
    traffic = {
        "traffic": [
            {"from": "A", "to": "B", "factor": 1},
            {"from": "B", "to": "C", "factor": 2},
            {"from": "A", "to": "C", "factor": 1},
        ]
    }
    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps(traffic))
    rp = tmp(json.dumps([{"source": "A", "destination": "C"}]))
    try:
        proc = run(["--graph", gp, "--requests", rp, "--traffic", tp])
        assert proc.returncode == 0, proc.stderr.decode()
        out = json.loads(proc.stdout.decode().strip().splitlines()[0])
        assert "effective_distance" in out
        assert math.isclose(out["effective_distance"], 15, abs_tol=1e-6)
    finally:
        os.unlink(gp)
        os.unlink(tp)
        os.unlink(rp)


def test_traffic_batch_extra_fields_ignored():
    graph = {
        "nodes": ["A", "B", "C"],
        "edges": [
            {"from": "A", "to": "B", "distance": 1},
            {"from": "B", "to": "C", "distance": 1},
        ],
    }
    traffic = {
        "traffic": [
            {"from": "A", "to": "B", "factor": 1},
            {"from": "B", "to": "C", "factor": 1},
        ]
    }
    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps(traffic))
    rp = tmp(
        json.dumps(
            [{"source": "A", "destination": "C", "priority": 1, "extra": "ignore"}]
        )
    )
    try:
        proc = run(["--graph", gp, "--requests", rp, "--traffic", tp])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip().splitlines()[0])
        assert out["path"] == ["A", "B", "C"]
    finally:
        os.unlink(gp)
        os.unlink(tp)
        os.unlink(rp)


def test_traffic_source_equals_dest():
    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 5}]}
    traffic = {"traffic": [{"from": "A", "to": "B", "factor": 10}]}
    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps(traffic))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "A", "--traffic", tp])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip())
        assert (
            out["path"] == ["A"]
            and out["distance"] == 0
            and out["effective_distance"] == 0
            and out["traffic_delay"] == 0
        )
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_tie_break_lexicographic_effective():
    graph = {
        "nodes": ["A", "B", "C", "D"],
        "edges": [
            {"from": "A", "to": "B", "distance": 5},
            {"from": "B", "to": "D", "distance": 5},
            {"from": "A", "to": "C", "distance": 5},
            {"from": "C", "to": "D", "distance": 5},
        ],
    }
    traffic = {
        "traffic": [
            {"from": "A", "to": "B", "factor": 1},
            {"from": "B", "to": "D", "factor": 1},
            {"from": "A", "to": "C", "factor": 1},
            {"from": "C", "to": "D", "factor": 1},
        ]
    }
    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps(traffic))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "D", "--traffic", tp])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip())
        assert out["path"] == ["A", "B", "D"], f"got {out['path']}"
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_tie_break_three_paths_effective():
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
    traffic = {
        "traffic": [
            {"from": "A", "to": "B", "factor": 1},
            {"from": "B", "to": "D", "factor": 1},
            {"from": "A", "to": "C", "factor": 1},
            {"from": "C", "to": "D", "factor": 1},
            {"from": "A", "to": "E", "factor": 1},
            {"from": "E", "to": "D", "factor": 1},
        ]
    }
    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps(traffic))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "D", "--traffic", tp])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip())
        assert out["path"] == ["A", "B", "D"], f"expected B got {out['path']}"
        assert math.isclose(out["effective_distance"], 10, abs_tol=1e-6)
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_duplicate_last_wins():
    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 10}]}
    traffic = {
        "traffic": [
            {"from": "A", "to": "B", "factor": 2},
            {"from": "A", "to": "B", "factor": 0.5},
        ]
    }
    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps(traffic))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B", "--traffic", tp])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip())
        assert math.isclose(out["effective_distance"], 5, abs_tol=1e-6)
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_direct_array_format():
    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 10}]}
    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps([{"from": "A", "to": "B", "factor": 2}]))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B", "--traffic", tp])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip())
        assert math.isclose(out["effective_distance"], 20, abs_tol=1e-6)
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_single_with_traffic_no_path():
    graph = {
        "nodes": ["A", "B", "C"],
        "edges": [{"from": "A", "to": "B", "distance": 1}],
    }
    traffic = {"traffic": [{"from": "A", "to": "B", "factor": 1}]}
    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps(traffic))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "C", "--traffic", tp])
        assert proc.returncode == 1
        out = json.loads(proc.stdout.decode().strip())
        assert (
            out["path"] == []
            and out["distance"] == -1
            and out["effective_distance"] == -1
        )
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_batch_with_traffic_some_no_route():
    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 1}]}
    traffic = {"traffic": [{"from": "A", "to": "B", "factor": 2}]}
    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps(traffic))
    rp = tmp(
        json.dumps(
            [{"source": "A", "destination": "B"}, {"source": "A", "destination": "C"}]
        )
    )
    try:
        proc = run(["--graph", gp, "--requests", rp, "--traffic", tp])
        assert proc.returncode == 1
        lines = proc.stdout.decode().strip().splitlines()
        assert len(lines) == 2
        o2 = json.loads(lines[1])
        assert o2["distance"] == -1 and o2["effective_distance"] == -1
    finally:
        os.unlink(gp)
        os.unlink(tp)
        os.unlink(rp)


def test_large_batch_100_with_traffic():
    nodes = [f"N{i}" for i in range(20)]
    edges = [{"from": f"N{i}", "to": f"N{i + 1}", "distance": 1} for i in range(19)]
    graph = {"nodes": nodes, "edges": edges}
    traffic = {
        "traffic": [
            {"from": f"N{i}", "to": f"N{i + 1}", "factor": 1.5} for i in range(19)
        ]
    }
    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps(traffic))
    reqs = [{"source": "N0", "destination": f"N{i}"} for i in range(20)] * 5
    rp = tmp(json.dumps(reqs))
    rp1 = tmp(json.dumps([{"source": "N0", "destination": "N19"}]))
    try:
        # Relative bound: 1 request vs 100 in the same process, same parse.
        # 100 requests must not cost ~100x one (per-request re-parse / O(n) rescan).
        start = time.time()
        base = run(["--graph", gp, "--requests", rp1, "--traffic", tp])
        base_elapsed = time.time() - start
        assert base.returncode == 0, base.stderr.decode()[:500]

        start = time.time()
        proc = run(["--graph", gp, "--requests", rp, "--traffic", tp])
        elapsed = time.time() - start
        assert proc.returncode == 0, proc.stderr.decode()[:500]
        assert elapsed <= 25 * base_elapsed + 1.0, (
            f"batch of 100 too slow vs single request: "
            f"{elapsed:.3f}s vs baseline {base_elapsed:.3f}s"
        )
        lines = proc.stdout.decode().strip().splitlines()
        assert len(lines) == 100
        o0 = json.loads(lines[0])
        assert (
            o0["path"] == ["N0"]
            and o0["distance"] == 0
            and o0["effective_distance"] == 0
        )
    finally:
        os.unlink(gp)
        os.unlink(tp)
        os.unlink(rp)
        os.unlink(rp1)


def test_performance_with_traffic():
    nodes = [f"N{i}" for i in range(200)]
    edges = [{"from": f"N{i}", "to": f"N{i + 1}", "distance": 1} for i in range(199)]
    extra = [
        {"from": f"N{i}", "to": f"N{i + 2}", "distance": 2} for i in range(0, 198, 2)
    ]
    traffic = {
        "traffic": [
            {"from": f"N{i}", "to": f"N{i + 1}", "factor": 1.1} for i in range(199)
        ]
        + [
            {"from": f"N{i}", "to": f"N{i + 2}", "factor": 1.0}
            for i in range(0, 198, 2)
        ]
    }
    graph = {"nodes": nodes, "edges": edges + extra}
    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps(traffic))
    try:
        proc = run(["--graph", gp, "--from", "N0", "--to", "N199", "--traffic", tp])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip())
        assert len(out["path"]) > 0
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_invalid_graph_still_exit2_with_traffic():
    graph = {"nodes": ["A", "A"], "edges": []}
    traffic = {"traffic": []}
    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps(traffic))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B", "--traffic", tp])
        assert proc.returncode == 2 and proc.stdout.decode().strip() == ""
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_delay_and_factor_combined():
    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 10}]}
    traffic = {"traffic": [{"from": "A", "to": "B", "factor": 2, "delay": 5}]}
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
    tp = tmp(json.dumps(traffic))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B", "--traffic", tp])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip())
        assert math.isclose(out["effective_distance"], 25, abs_tol=1e-6)
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_factor_scientific_notation():
    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 10}]}
    traffic = {"traffic": [{"from": "A", "to": "B", "factor": 1e-2}]}
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
    tp = tmp(json.dumps(traffic))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B", "--traffic", tp])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip())
        assert math.isclose(out["effective_distance"], 0.1, abs_tol=1e-6)
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_heavy_perf_500_nodes_200_requests():
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
    reqs = [{"source": "N0", "destination": f"N{i}"} for i in range(0, 500, 5)] * 4
    reqs = reqs[:100]
    rp = tmp(json.dumps(reqs))
    try:
        start = time.time()
        proc = run(["--graph", gp, "--requests", rp])
        elapsed = time.time() - start
        assert proc.returncode == 0, (
            f"heavy perf should succeed rc={proc.returncode} stderr={proc.stderr.decode()[:200]}"
        )
        assert elapsed < 2.5, f"too slow {elapsed} for 500 nodes 100 requests"
        lines = proc.stdout.decode().strip().splitlines()
        assert len(lines) == 100
    finally:
        os.unlink(gp)
        os.unlink(rp)


def test_traffic_delay_scientific_notation():
    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 10}]}
    traffic = {"traffic": [{"from": "A", "to": "B", "factor": 1, "delay": 1e2}]}
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
    tp = tmp(json.dumps(traffic))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B", "--traffic", tp])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip())
        assert math.isclose(out["effective_distance"], 110, abs_tol=1e-6)
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_delay_and_factor_combined_detailed():
    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 10}]}
    traffic = {"traffic": [{"from": "A", "to": "B", "factor": 2, "delay": 5}]}
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
    tp = tmp(json.dumps(traffic))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B", "--traffic", tp])
        assert proc.returncode == 0, (
            f"delay+factor should be valid, rc={proc.returncode} stderr={proc.stderr.decode()}"
        )
        out = json.loads(proc.stdout.decode().strip())
        assert math.isclose(out["distance"], 10, abs_tol=1e-6)
        assert math.isclose(out["effective_distance"], 25, abs_tol=1e-6), (
            f"expected 10*2+5=25 got {out['effective_distance']}"
        )
        assert math.isclose(out["traffic_delay"], 15, abs_tol=1e-6)
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_secondary_raw_tie_break():
    # Effective equal (12), raw differs (11 vs 4) -> pick raw smaller A-C-D even though B<C
    graph = {
        "nodes": ["A", "B", "C", "D"],
        "edges": [
            {"from": "A", "to": "B", "distance": 10},
            {"from": "B", "to": "D", "distance": 1},
            {"from": "A", "to": "C", "distance": 2},
            {"from": "C", "to": "D", "distance": 2},
        ],
    }
    traffic = {
        "traffic": [
            {"from": "A", "to": "B", "factor": 1, "delay": 0},
            {"from": "B", "to": "D", "factor": 1, "delay": 1},
            {"from": "A", "to": "C", "factor": 2, "delay": 0},
            {"from": "C", "to": "D", "factor": 4, "delay": 0},
        ]
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
    tp = tmp(json.dumps(traffic))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "D", "--traffic", tp])
        assert proc.returncode == 0, proc.stderr.decode()
        out = json.loads(proc.stdout.decode().strip())
        assert out["path"] == ["A", "C", "D"], (
            f"secondary raw tie-break expects A-C-D raw 4 vs A-B-D raw 11, got {out['path']}"
        )
        import math

        assert math.isclose(out["effective_distance"], 12, abs_tol=1e-6)
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_very_small_and_large():
    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 1000}]}
    traffic = {"traffic": [{"from": "A", "to": "B", "factor": 1e-9}]}
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
    tp = tmp(json.dumps(traffic))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B", "--traffic", tp])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip())
        assert math.isclose(out["effective_distance"], 1000 * 1e-9, abs_tol=1e-9)
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_duplicate_different_direction_last_wins():
    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 10}]}
    traffic = {
        "traffic": [
            {"from": "A", "to": "B", "factor": 2},
            {"from": "B", "to": "A", "factor": 3},
        ]
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
    tp = tmp(json.dumps(traffic))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B", "--traffic", tp])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip())
        assert math.isclose(out["effective_distance"], 30, abs_tol=1e-6), (
            f"last wins factor 3 should give 30, got {out['effective_distance']}"
        )
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_missing_traffic_key_invalid():
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
    tp = tmp(json.dumps({"foo": []}))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B", "--traffic", tp])
        assert proc.returncode == 2
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_delay_negative_invalid():
    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 1}]}
    traffic = {"traffic": [{"from": "A", "to": "B", "factor": 1, "delay": -1}]}
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
    tp = tmp(json.dumps(traffic))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B", "--traffic", tp])
        assert proc.returncode == 2
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_factor_missing_invalid():
    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 1}]}
    traffic = {"traffic": [{"from": "A", "to": "B"}]}
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
    tp = tmp(json.dumps(traffic))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B", "--traffic", tp])
        assert proc.returncode == 2
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_batch_with_empty_source_no_route():
    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 5}]}
    traffic = {"traffic": [{"from": "A", "to": "B", "factor": 2}]}
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
    tp = tmp(json.dumps(traffic))
    rp = tmp(json.dumps([{"source": "", "destination": "B"}]))
    try:
        proc = run(["--graph", gp, "--requests", rp, "--traffic", tp])
        assert proc.returncode == 1
        out = json.loads(proc.stdout.decode().strip().splitlines()[0])
        assert out["path"] == [] and out["effective_distance"] == -1
    finally:
        os.unlink(gp)
        os.unlink(tp)
        os.unlink(rp)


def test_traffic_5_way_effective_tie():
    nodes = ["A", "B", "C", "D", "E", "G", "F"]
    edges = [
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
    ]
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
        proc = run(
            [
                "--graph",
                gp,
                "--from",
                "A",
                "--to",
                "F",
                "--traffic",
                tmp(json.dumps([])),
            ]
        )
        # Actually empty traffic array via direct format
        # Use explicit traffic file with empty array
        import os, json, tempfile

        tp = tempfile.NamedTemporaryFile(delete=False, suffix=".json", mode="w")
        tp.write(json.dumps([]))
        tp.close()
        proc = run(["--graph", gp, "--from", "A", "--to", "F", "--traffic", tp.name])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip())
        assert out["path"] == ["A", "B", "F"], (
            f"5-way effective tie should pick B, got {out['path']}"
        )
        os.unlink(tp.name)
    finally:
        os.unlink(gp)


def test_traffic_extra_fields_mixed_ignored():
    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 10}]}
    traffic = {
        "traffic": [
            {
                "from": "A",
                "to": "B",
                "factor": 2,
                "delay": 1,
                "extra": "ignore",
                "weight": 99,
                "unknown": True,
            }
        ],
        "top_extra": "ignore",
        "another": 123,
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
    tp = tmp(json.dumps(traffic))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B", "--traffic", tp])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip())
        assert math.isclose(out["effective_distance"], 21, abs_tol=1e-6)
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_large_batch_200_with_traffic():
    nodes = [f"N{i}" for i in range(100)]
    edges = [{"from": f"N{i}", "to": f"N{i + 1}", "distance": 1} for i in range(99)]
    graph = {"nodes": nodes, "edges": edges}
    traffic = {
        "traffic": [
            {"from": f"N{i}", "to": f"N{i + 1}", "factor": 2} for i in range(0, 99, 10)
        ]
    }
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
    tp = tmp(json.dumps(traffic))
    reqs = [{"source": "N0", "destination": f"N{i % 100}"} for i in range(200)]
    rp = tmp(json.dumps(reqs))
    try:
        start = time.time()
        proc = run(["--graph", gp, "--requests", rp, "--traffic", tp])
        elapsed = time.time() - start
        assert proc.returncode == 0, (
            f"rc={proc.returncode} stderr={proc.stderr.decode()[:200]}"
        )
        assert elapsed < 4.0, f"too slow 200 batch with traffic {elapsed}"
        assert len(proc.stdout.decode().strip().splitlines()) == 200
    finally:
        os.unlink(gp)
        os.unlink(tp)
        os.unlink(rp)


def test_traffic_10_way_effective_tie():
    nodes = ["A"] + [chr(ord("B") + i) for i in range(10)] + ["Z"]
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
        proc = run(
            [
                "--graph",
                gp,
                "--from",
                "A",
                "--to",
                "Z",
                "--traffic",
                tmp(json.dumps([])),
            ]
        )
        # second run with proper empty traffic file
        import tempfile, json

        tp = tempfile.NamedTemporaryFile(delete=False, suffix=".json", mode="w")
        tp.write(json.dumps([]))
        tp.close()
        proc = run(["--graph", gp, "--from", "A", "--to", "Z", "--traffic", tp.name])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip())
        assert out["path"] == ["A", "B", "Z"], (
            f"10-way effective tie should pick B, got {out['path']}"
        )
        import os

        os.unlink(tp.name)
    finally:
        import os

        os.unlink(gp)


def test_traffic_large_factor_and_delay():
    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 1e3}]}
    traffic = {"traffic": [{"from": "A", "to": "B", "factor": 1e6, "delay": 1e6}]}
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
    tp = tmp(json.dumps(traffic))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B", "--traffic", tp])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip())
        assert math.isclose(out["effective_distance"], 1e3 * 1e6 + 1e6, rel_tol=1e-6)
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_duplicate_many_last_wins():
    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 10}]}
    traffic_entries = [{"from": "A", "to": "B", "factor": i} for i in range(1, 11)]
    traffic = {"traffic": traffic_entries}
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
    tp = tmp(json.dumps(traffic))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B", "--traffic", tp])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip())
        assert math.isclose(out["effective_distance"], 100, abs_tol=1e-6), (
            f"last factor 10 should give 100, got {out['effective_distance']}"
        )
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_batch_500_with_traffic():
    nodes = [f"N{i}" for i in range(100)]
    edges = [{"from": f"N{i}", "to": f"N{i + 1}", "distance": 1} for i in range(99)]
    graph = {"nodes": nodes, "edges": edges}
    traffic = {
        "traffic": [
            {"from": f"N{i}", "to": f"N{i + 1}", "factor": 1.5} for i in range(0, 99, 5)
        ]
    }
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
    tp = tmp(json.dumps(traffic))
    reqs = [{"source": "N0", "destination": f"N{i % 100}"} for i in range(500)]
    rp = tmp(json.dumps(reqs))
    try:
        start = time.time()
        proc = run(["--graph", gp, "--requests", rp, "--traffic", tp])
        elapsed = time.time() - start
        assert proc.returncode == 0
        assert elapsed < 5.0, f"too slow 500 batch with traffic {elapsed}"
        assert len(proc.stdout.decode().strip().splitlines()) == 500
    finally:
        os.unlink(gp)
        os.unlink(tp)
        os.unlink(rp)


def test_traffic_request_both_keys_with_traffic():
    graph = {
        "nodes": ["A", "B", "C"],
        "edges": [
            {"from": "A", "to": "B", "distance": 1},
            {"from": "B", "to": "C", "distance": 1},
            {"from": "A", "to": "C", "distance": 10},
        ],
    }
    traffic = {"traffic": [{"from": "A", "to": "B", "factor": 10}]}
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
    tp = tmp(json.dumps(traffic))
    rp = tmp(
        json.dumps(
            [{"source": "A", "destination": "C", "from": "A", "to": "B", "extra": "x"}]
        )
    )
    try:
        proc = run(["--graph", gp, "--requests", rp, "--traffic", tp])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip().splitlines()[0])
        assert out["path"] == ["A", "C"]
    finally:
        os.unlink(gp)
        os.unlink(tp)
        os.unlink(rp)


def test_traffic_with_version_field_ignored():
    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 5}]}
    traffic = {"version": 1, "traffic": [{"from": "A", "to": "B", "factor": 2}]}
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
    tp = tmp(json.dumps(traffic))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B", "--traffic", tp])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip())
        assert math.isclose(out["effective_distance"], 10, abs_tol=1e-6)
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_performance_1000_nodes():
    nodes = [f"N{i}" for i in range(1000)]
    edges = [{"from": f"N{i}", "to": f"N{i + 1}", "distance": 1} for i in range(999)]
    graph = {"nodes": nodes, "edges": edges}
    traffic = {
        "traffic": [
            {"from": f"N{i}", "to": f"N{i + 1}", "factor": 1.5}
            for i in range(0, 999, 100)
        ]
    }
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
    tp = tmp(json.dumps(traffic))
    try:
        start = time.time()
        proc = run(["--graph", gp, "--from", "N0", "--to", "N999", "--traffic", tp])
        elapsed = time.time() - start
        assert proc.returncode == 0
        assert elapsed < 3.0, f"too slow 1000 nodes with traffic {elapsed}"
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_isolated_nodes_with_traffic():
    graph = {
        "nodes": ["A", "B", "Isolated"],
        "edges": [{"from": "A", "to": "B", "distance": 1}],
    }
    traffic = {"traffic": [{"from": "A", "to": "B", "factor": 2}]}
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
    tp = tmp(json.dumps(traffic))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "Isolated", "--traffic", tp])
        assert proc.returncode == 1
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_duplicate_many_with_delay_last_wins():
    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 10}]}
    entries = [{"from": "A", "to": "B", "factor": 1, "delay": i} for i in range(10)]
    traffic = {"traffic": entries}
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
    tp = tmp(json.dumps(traffic))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B", "--traffic", tp])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip())
        assert math.isclose(out["effective_distance"], 19, abs_tol=1e-6), (
            f"last delay 9 => eff 19, got {out['effective_distance']}"
        )
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_batch_1000_with_traffic():
    nodes = [f"N{i}" for i in range(200)]
    edges = [{"from": f"N{i}", "to": f"N{i + 1}", "distance": 1} for i in range(199)]
    graph = {"nodes": nodes, "edges": edges}
    traffic = {
        "traffic": [
            {"from": f"N{i}", "to": f"N{i + 1}", "factor": 1.2}
            for i in range(0, 199, 20)
        ]
    }
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
    tp = tmp(json.dumps(traffic))
    reqs = [{"source": "N0", "destination": f"N{i % 200}"} for i in range(1000)]
    rp = tmp(json.dumps(reqs))
    try:
        start = time.time()
        proc = run(["--graph", gp, "--requests", rp, "--traffic", tp])
        elapsed = time.time() - start
        assert proc.returncode == 0
        assert elapsed < 6.0, f"too slow 1000 batch with traffic {elapsed}"
        assert len(proc.stdout.decode().strip().splitlines()) == 1000
    finally:
        os.unlink(gp)
        os.unlink(tp)
        os.unlink(rp)


def test_traffic_performance_2000_nodes():
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
            [BIN] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15
        )

    gp = tmp(json.dumps(graph))
    try:
        start = time.time()
        proc = run(["--graph", gp, "--from", "N0", "--to", "N1999"])
        elapsed = time.time() - start
        assert proc.returncode == 0
        assert elapsed < 3.5, f"too slow 2000 nodes {elapsed}"
    finally:
        os.unlink(gp)


def test_traffic_help_with_extra_flags():
    import subprocess

    BIN = "/app/router"
    proc = subprocess.run(
        [BIN, "--help", "--traffic", "dummy"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=5,
    )
    assert proc.returncode == 0
    assert "traffic" in proc.stdout.decode().lower()


def test_traffic_duplicate_many_with_factor_and_delay_both():
    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 10}]}
    entries = [
        {"from": "A", "to": "B", "factor": i, "delay": i * 2} for i in range(1, 11)
    ]
    traffic = {"traffic": entries}
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
    tp = tmp(json.dumps(traffic))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B", "--traffic", tp])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip())
        # last factor 10 delay 20 => eff 120
        assert math.isclose(out["effective_distance"], 120, abs_tol=1e-6)
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_batch_1500_with_traffic():
    nodes = [f"N{i}" for i in range(200)]
    edges = [{"from": f"N{i}", "to": f"N{i + 1}", "distance": 1} for i in range(199)]
    graph = {"nodes": nodes, "edges": edges}
    traffic = {
        "traffic": [
            {"from": f"N{i}", "to": f"N{i + 1}", "factor": 1.1}
            for i in range(0, 199, 10)
        ]
    }
    import json, tempfile, os, time, subprocess

    def tmp(c):
        f = tempfile.NamedTemporaryFile(delete=False, suffix=".json", mode="w")
        f.write(c)
        f.close()
        return f.name

    def run(args):
        BIN = "/app/router"
        return subprocess.run(
            [BIN] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=25
        )

    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps(traffic))
    reqs = [{"source": "N0", "destination": f"N{i % 200}"} for i in range(1500)]
    rp = tmp(json.dumps(reqs))
    try:
        start = time.time()
        proc = run(["--graph", gp, "--requests", rp, "--traffic", tp])
        elapsed = time.time() - start
        assert proc.returncode == 0
        assert elapsed < 6.5, f"too slow 1500 batch with traffic {elapsed}"
        assert len(proc.stdout.decode().strip().splitlines()) == 1500
    finally:
        os.unlink(gp)
        os.unlink(tp)
        os.unlink(rp)


def test_traffic_performance_5000_nodes_with_traffic():
    nodes = [f"N{i}" for i in range(5000)]
    edges = [{"from": f"N{i}", "to": f"N{i + 1}", "distance": 1} for i in range(4999)]
    graph = {"nodes": nodes, "edges": edges}
    traffic = {
        "traffic": [
            {"from": f"N{i}", "to": f"N{i + 1}", "factor": 1.1}
            for i in range(0, 4999, 500)
        ]
    }
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
    tp = tmp(json.dumps(traffic))
    try:
        start = time.time()
        proc = run(["--graph", gp, "--from", "N0", "--to", "N4999", "--traffic", tp])
        elapsed = time.time() - start
        assert proc.returncode == 0
        assert elapsed < 5.0, f"too slow 5000 nodes with traffic {elapsed}"
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_tie_break_special_chars_secondary_raw():
    # Effective equal, raw equal, lex special chars: '-' < '.' < '_'  (same as step1 but with traffic)
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
    traffic = {
        "traffic": [
            {"from": "A", "to": "A-B", "factor": 1},
            {"from": "A", "to": "A.B", "factor": 1},
            {"from": "A", "to": "A_B", "factor": 1},
        ]
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
    tp = tmp(json.dumps(traffic))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "Z", "--traffic", tp])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip())
        assert out["path"] == ["A", "A-B", "Z"]
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_request_with_unicode():
    graph = {
        "nodes": ["A😀", "B🚀", "C"],
        "edges": [
            {"from": "A😀", "to": "B🚀", "distance": 2},
            {"from": "B🚀", "to": "C", "distance": 3},
        ],
    }
    traffic = {"traffic": [{"from": "A😀", "to": "B🚀", "factor": 1.5}]}
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
    tp = tmp(json.dumps(traffic))
    try:
        proc = run(
            [
                "--graph",
                gp,
                "--requests",
                tmp(json.dumps([{"source": "A😀", "destination": "C"}])),
                "--traffic",
                tp,
            ]
        )
        # Actually need to handle tmp inside
        import tempfile, json as js

        rp = tempfile.NamedTemporaryFile(delete=False, suffix=".json", mode="w")
        rp.write(js.dumps([{"source": "A😀", "destination": "C"}]))
        rp.close()
        proc = run(["--graph", gp, "--requests", rp.name, "--traffic", tp])
        assert proc.returncode == 0
        out = js.loads(proc.stdout.decode().strip().splitlines()[0])
        assert out["path"] == ["A😀", "B🚀", "C"]
        os.unlink(rp.name)
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_duplicate_with_delay_only_second_no_delay():
    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 10}]}
    traffic = {
        "traffic": [
            {"from": "A", "to": "B", "factor": 1, "delay": 10},
            {"from": "A", "to": "B", "factor": 2},
        ]
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
    tp = tmp(json.dumps(traffic))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B", "--traffic", tp])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip())
        assert math.isclose(out["effective_distance"], 20, abs_tol=1e-6), (
            f"delay reset expected 20 got {out['effective_distance']}"
        )
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_factor_float_many_decimals():
    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 1}]}
    traffic = {"traffic": [{"from": "A", "to": "B", "factor": 1.123456789}]}
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
    tp = tmp(json.dumps(traffic))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B", "--traffic", tp])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip())
        assert math.isclose(out["effective_distance"], 1.123456789, abs_tol=1e-9)
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_batch_2000_with_traffic():
    nodes = [f"N{i}" for i in range(200)]
    edges = [{"from": f"N{i}", "to": f"N{i + 1}", "distance": 1} for i in range(199)]
    graph = {"nodes": nodes, "edges": edges}
    traffic = {
        "traffic": [
            {"from": f"N{i}", "to": f"N{i + 1}", "factor": 1.1}
            for i in range(0, 199, 10)
        ]
    }
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
    tp = tmp(json.dumps(traffic))
    reqs = [{"source": "N0", "destination": f"N{i % 200}"} for i in range(2000)]
    rp = tmp(json.dumps(reqs))
    try:
        start = time.time()
        proc = run(["--graph", gp, "--requests", rp, "--traffic", tp])
        elapsed = time.time() - start
        assert proc.returncode == 0
        assert elapsed < 7.0, f"too slow 2000 batch with traffic {elapsed}"
        assert len(proc.stdout.decode().strip().splitlines()) == 2000
    finally:
        os.unlink(gp)
        os.unlink(tp)
        os.unlink(rp)


def test_traffic_performance_5000_edges_dense_with_traffic():
    nodes = [f"N{i}" for i in range(100)]
    edges = []
    for i in range(100):
        for j in range(i + 1, min(i + 10, 100)):
            edges.append({"from": f"N{i}", "to": f"N{j}", "distance": float(j - i)})
    graph = {"nodes": nodes, "edges": edges}
    traffic = {
        "traffic": [
            {"from": f"N{i}", "to": f"N{i + 1}", "factor": 2} for i in range(0, 99, 10)
        ]
    }
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
    tp = tmp(json.dumps(traffic))
    try:
        start = time.time()
        proc = run(["--graph", gp, "--from", "N0", "--to", "N99", "--traffic", tp])
        elapsed = time.time() - start
        assert proc.returncode == 0
        assert elapsed < 2.0, f"too slow dense 5000 edges with traffic {elapsed}"
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_tie_break_10_way_secondary_raw():
    # Effective equal, raw equal, lex smallest wins — 10-way
    nodes = ["A"] + [chr(ord("B") + i) for i in range(10)] + ["Z"]
    edges = []
    for i in range(10):
        mid = chr(ord("B") + i)
        edges.append({"from": "A", "to": mid, "distance": 5})
        edges.append({"from": mid, "to": "Z", "distance": 5})
    graph = {"nodes": nodes, "edges": edges}
    import json, tempfile, os, subprocess, tempfile as tf

    def tmp(c):
        f = tf.NamedTemporaryFile(delete=False, suffix=".json", mode="w")
        f.write(c)
        f.close()
        return f.name

    def run(args):
        BIN = "/app/router"
        return subprocess.run(
            [BIN] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5
        )

    gp = tmp(json.dumps(graph))
    tp = tf.NamedTemporaryFile(delete=False, suffix=".json", mode="w")
    tp.write(json.dumps([]))
    tp.close()
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "Z", "--traffic", tp.name])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip())
        assert out["path"] == ["A", "B", "Z"]
    finally:
        import os

        os.unlink(gp)
        os.unlink(tp.name)


def test_traffic_with_unicode_emoji():
    graph = {
        "nodes": ["A😀", "B🚀", "C"],
        "edges": [
            {"from": "A😀", "to": "B🚀", "distance": 2},
            {"from": "B🚀", "to": "C", "distance": 3},
        ],
    }
    traffic = {"traffic": [{"from": "A😀", "to": "B🚀", "factor": 2}]}
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
    tp = tmp(json.dumps(traffic))
    try:
        proc = run(["--graph", gp, "--from", "A😀", "--to", "C", "--traffic", tp])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip())
        assert out["path"] == ["A😀", "B🚀", "C"]
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_batch_2000_with_traffic_strict():
    nodes = [f"N{i}" for i in range(200)]
    edges = [{"from": f"N{i}", "to": f"N{i + 1}", "distance": 1} for i in range(199)]
    graph = {"nodes": nodes, "edges": edges}
    traffic = {
        "traffic": [
            {"from": f"N{i}", "to": f"N{i + 1}", "factor": 1.1}
            for i in range(0, 199, 10)
        ]
    }
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
    tp = tmp(json.dumps(traffic))
    reqs = [{"source": "N0", "destination": f"N{i % 200}"} for i in range(2000)]
    rp = tmp(json.dumps(reqs))
    try:
        start = time.time()
        proc = run(["--graph", gp, "--requests", rp, "--traffic", tp])
        elapsed = time.time() - start
        assert proc.returncode == 0
        assert elapsed < 4.5, (
            f"too slow 2000 batch with traffic {elapsed} need <4.5 for ultra"
        )
        assert len(proc.stdout.decode().strip().splitlines()) == 2000
    finally:
        os.unlink(gp)
        os.unlink(tp)
        os.unlink(rp)


def test_traffic_performance_5000_nodes_dense_strict():
    nodes = [f"N{i}" for i in range(100)]
    edges = []
    for i in range(100):
        for j in range(i + 1, min(i + 15, 100)):
            edges.append({"from": f"N{i}", "to": f"N{j}", "distance": float(j - i)})
    graph = {"nodes": nodes, "edges": edges}
    traffic = {
        "traffic": [
            {"from": f"N{i}", "to": f"N{i + 1}", "factor": 3} for i in range(0, 99, 10)
        ]
    }
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
    tp = tmp(json.dumps(traffic))
    try:
        start = time.time()
        proc = run(["--graph", gp, "--from", "N0", "--to", "N99", "--traffic", tp])
        elapsed = time.time() - start
        assert proc.returncode == 0
        assert elapsed < 1.0, (
            f"too slow dense 5000 edges with traffic {elapsed} need <1s ultra"
        )
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_duplicate_many_with_factor_and_delay_both_hard():
    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 10}]}
    entries = []
    for i in range(20):
        entries.append({"from": "A", "to": "B", "factor": i + 1, "delay": i * 3})
    traffic = {"traffic": entries}
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
    tp = tmp(json.dumps(traffic))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B", "--traffic", tp])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip())
        assert math.isclose(out["effective_distance"], 10 * 20 + 57, abs_tol=1e-6)
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_request_with_unicode_and_traffic_hard():
    graph = {
        "nodes": ["A😀", "B🚀", "C🌟", "D"],
        "edges": [
            {"from": "A😀", "to": "B🚀", "distance": 2},
            {"from": "B🚀", "to": "C🌟", "distance": 2},
            {"from": "C🌟", "to": "D", "distance": 10},
        ],
    }
    traffic = {"traffic": [{"from": "C🌟", "to": "D", "factor": 10}]}
    import json, tempfile, os, subprocess, tempfile as tf, json as js

    def tmp(c):
        f = tf.NamedTemporaryFile(delete=False, suffix=".json", mode="w")
        f.write(c)
        f.close()
        return f.name

    def run(args):
        BIN = "/app/router"
        return subprocess.run(
            [BIN] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5
        )

    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps(traffic))
    try:
        rp = tf.NamedTemporaryFile(delete=False, suffix=".json", mode="w")
        rp.write(js.dumps([{"source": "A😀", "destination": "D"}]))
        rp.close()
        proc = run(["--graph", gp, "--requests", rp.name, "--traffic", tp])
        assert proc.returncode == 0
        out = js.loads(proc.stdout.decode().strip().splitlines()[0])
        assert out["path"] == ["A😀", "B🚀", "C🌟", "D"]
        import os

        os.unlink(rp.name)
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_help_with_traffic_and_requests():
    import subprocess

    BIN = "/app/router"
    proc = subprocess.run(
        [BIN, "--help", "--graph", "a", "--requests", "b", "--traffic", "c"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=5,
    )
    assert proc.returncode == 0
    assert "traffic" in proc.stdout.decode().lower()


def test_traffic_batch_with_no_route_and_valid_mixed_traffic():
    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 1}]}
    traffic = {"traffic": [{"from": "A", "to": "B", "factor": 2}]}
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
    tp = tmp(json.dumps(traffic))
    reqs = [
        {"source": "A", "destination": "B"},
        {"source": "X", "destination": "Y"},
    ]
    rp = tmp(json.dumps(reqs))
    try:
        proc = run(["--graph", gp, "--requests", rp, "--traffic", tp])
        assert proc.returncode == 1
        lines = proc.stdout.decode().strip().splitlines()
        assert len(lines) == 2
    finally:
        os.unlink(gp)
        os.unlink(tp)
        os.unlink(rp)


def test_traffic_default_factor_one_for_untraffic_edge():
    # Spec L47/L52: missing traffic entry for an edge -> factor 1.0, delay 0.0.
    graph = {
        "nodes": ["A", "B", "C"],
        "edges": [
            {"from": "A", "to": "B", "distance": 10},
            {"from": "B", "to": "C", "distance": 20},
            {"from": "A", "to": "C", "distance": 45},
        ],
    }
    traffic = {"traffic": [{"from": "A", "to": "B", "factor": 3}]}
    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps(traffic))
    try:
        # A-B-C effective = 10*3 + 20*1.0 = 50; A-C direct = 45*1.0 = 45 -> direct wins.
        # Raw favours A-B-C (30 < 45), so ignoring traffic also fails here.
        proc = run(["--graph", gp, "--from", "A", "--to", "C", "--traffic", tp])
        assert proc.returncode == 0, proc.stderr.decode()[:300]
        out = json.loads(proc.stdout.decode().strip())
        assert out["path"] == ["A", "C"], (
            f"default factor 1.0 should pick direct A-C, got {out['path']}"
        )
        assert math.isclose(out["distance"], 45, abs_tol=1e-6)
        assert math.isclose(out["effective_distance"], 45, abs_tol=1e-6)
        assert math.isclose(out["traffic_delay"], 0, abs_tol=1e-6)

        # A single untraffic'd edge: effective == raw, delay 0.
        proc2 = run(["--graph", gp, "--from", "B", "--to", "C", "--traffic", tp])
        assert proc2.returncode == 0
        out2 = json.loads(proc2.stdout.decode().strip())
        assert out2["path"] == ["B", "C"]
        assert math.isclose(out2["effective_distance"], 20, abs_tol=1e-6)
        assert math.isclose(out2["traffic_delay"], 0, abs_tol=1e-6)
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_reverse_direction_entry_applies():
    # Spec L43/L46: graph edges are undirected, so a lone B->A traffic entry
    # must apply to an A->B query (and vice versa).
    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 10}]}
    traffic = {"traffic": [{"from": "B", "to": "A", "factor": 2, "delay": 5}]}
    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps(traffic))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B", "--traffic", tp])
        assert proc.returncode == 0, proc.stderr.decode()[:300]
        out = json.loads(proc.stdout.decode().strip())
        assert math.isclose(out["effective_distance"], 25, abs_tol=1e-6), (
            f"reverse-direction entry must apply: expected 25, got "
            f"{out['effective_distance']}"
        )
        assert math.isclose(out["distance"], 10, abs_tol=1e-6)
        assert math.isclose(out["traffic_delay"], 15, abs_tol=1e-6)

        proc2 = run(["--graph", gp, "--from", "B", "--to", "A", "--traffic", tp])
        assert proc2.returncode == 0
        out2 = json.loads(proc2.stdout.decode().strip())
        assert math.isclose(out2["effective_distance"], 25, abs_tol=1e-6)
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_batch_with_empty_destination_no_route():
    # Mirror of test_traffic_batch_with_empty_source_no_route for the dst side.
    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 5}]}
    traffic = {"traffic": [{"from": "A", "to": "B", "factor": 2}]}
    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps(traffic))
    rp = tmp(
        json.dumps(
            [
                {"source": "A", "destination": ""},
                {"source": "", "destination": ""},
            ]
        )
    )
    try:
        proc = run(["--graph", gp, "--requests", rp, "--traffic", tp])
        assert proc.returncode == 1
        lines = proc.stdout.decode().strip().splitlines()
        assert len(lines) == 2
        for ln in lines:
            out = json.loads(ln)
            assert out["path"] == [] and out["effective_distance"] == -1
    finally:
        os.unlink(gp)
        os.unlink(tp)
        os.unlink(rp)


# === HARDENING: make step 2 discriminating (previously 100% conditional pass) ===


def test_traffic_duplicate_edges_min_plus_traffic():
    # Graph has duplicate edges A-B 10 and 3, keep min 3. Traffic factor 2 => effective 6.
    graph = {
        "nodes": ["A", "B"],
        "edges": [
            {"from": "A", "to": "B", "distance": 10},
            {"from": "A", "to": "B", "distance": 3},
        ],
    }
    traffic = {"traffic": [{"from": "A", "to": "B", "factor": 2}]}
    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps(traffic))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B", "--traffic", tp])
        assert proc.returncode == 0, proc.stderr.decode()[:300]
        out = json.loads(proc.stdout.decode().strip())
        assert math.isclose(out["distance"], 3, abs_tol=1e-6), (
            f"should keep min 3, got {out['distance']}"
        )
        assert math.isclose(out["effective_distance"], 6, abs_tol=1e-6)
        assert math.isclose(out["traffic_delay"], 3, abs_tol=1e-6)
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_leading_trailing_space_invalid():
    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 5}]}
    cases = [
        {"from": " A", "to": "B", "factor": 2},
        {"from": "A ", "to": "B", "factor": 2},
        {"from": "A", "to": " B", "factor": 2},
        {"from": "A", "to": "B ", "factor": 2},
        {"from": " A ", "to": " B ", "factor": 2},
    ]
    gp = tmp(json.dumps(graph))
    try:
        for entry in cases:
            tp = tmp(json.dumps({"traffic": [entry]}))
            try:
                proc = run(["--graph", gp, "--from", "A", "--to", "B", "--traffic", tp])
                assert proc.returncode == 2, (
                    f"leading/trailing space should be invalid, got rc={proc.returncode} for {entry}"
                )
                assert proc.stdout.decode().strip() == ""
            finally:
                os.unlink(tp)
    finally:
        os.unlink(gp)


def test_traffic_file_not_found():
    gp = tmp(
        json.dumps(
            {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 1}]}
        )
    )
    try:
        proc = run(
            [
                "--graph",
                gp,
                "--from",
                "A",
                "--to",
                "B",
                "--traffic",
                "/nonexistent/traffic.json",
            ]
        )
        assert proc.returncode == 2, (
            f"traffic file not found should be exit 2, got {proc.returncode}"
        )
        assert proc.stdout.decode().strip() == ""
    finally:
        os.unlink(gp)


def test_traffic_equals_syntax():
    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 10}]}
    traffic = {"traffic": [{"from": "A", "to": "B", "factor": 2, "delay": 5}]}
    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps(traffic))
    try:
        proc = run([f"--graph={gp}", f"--from=A", f"--to=B", f"--traffic={tp}"])
        assert proc.returncode == 0, (
            f"equals syntax with traffic should work, rc={proc.returncode} stderr={proc.stderr.decode()[:200]}"
        )
        out = json.loads(proc.stdout.decode().strip())
        assert math.isclose(out["effective_distance"], 25, abs_tol=1e-6)

        proc2 = run([f"--graph={gp}", "--from", "A", "--to", "B", f"--traffic={tp}"])
        assert proc2.returncode == 0
        out2 = json.loads(proc2.stdout.decode().strip())
        assert math.isclose(out2["effective_distance"], 25, abs_tol=1e-6)
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_batch_equals_syntax():
    graph = {
        "nodes": ["A", "B", "C"],
        "edges": [
            {"from": "A", "to": "B", "distance": 1},
            {"from": "B", "to": "C", "distance": 1},
        ],
    }
    traffic = {"traffic": [{"from": "A", "to": "B", "factor": 2}]}
    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps(traffic))
    rp = tmp(json.dumps([{"source": "A", "destination": "C"}]))
    try:
        proc = run([f"--graph={gp}", f"--requests={rp}", f"--traffic={tp}"])
        assert proc.returncode == 0, (
            f"batch equals syntax should work rc={proc.returncode}"
        )
        out = json.loads(proc.stdout.decode().strip().splitlines()[0])
        assert out["path"] == ["A", "B", "C"]
        assert math.isclose(out["effective_distance"], 3, abs_tol=1e-6)
    finally:
        os.unlink(gp)
        os.unlink(tp)
        os.unlink(rp)


def test_traffic_formula_not_distance_plus_delay_times_factor():
    # Correct effective = distance*factor + delay = 10*2+10=30 per first edge.
    # A-B-C: 30 + 10*1 =40, A-C direct: 20*2=40 tie -> raw tie both 20, lex B<C => A-B-C wins.
    # Wrong formula (distance+delay)*factor => A-B (10+10)*2=40 +10=50 vs 40 => would pick A-C.
    # Wrong formula distance*(factor+delay) => 10*(2+10)=120+10=130 vs 40 => would pick A-C.
    graph = {
        "nodes": ["A", "B", "C"],
        "edges": [
            {"from": "A", "to": "B", "distance": 10},
            {"from": "B", "to": "C", "distance": 10},
            {"from": "A", "to": "C", "distance": 20},
        ],
    }
    traffic = {
        "traffic": [
            {"from": "A", "to": "B", "factor": 2, "delay": 10},
            {"from": "B", "to": "C", "factor": 1},
            {"from": "A", "to": "C", "factor": 2},
        ]
    }
    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps(traffic))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "C", "--traffic", tp])
        assert proc.returncode == 0, proc.stderr.decode()[:300]
        out = json.loads(proc.stdout.decode().strip())
        # Correct should be A-B-C due to 3-level tie (effective tie, raw tie, lex)
        assert out["path"] == ["A", "B", "C"], (
            f"formula must be distance*factor+delay, expected A-B-C got {out['path']} eff={out['effective_distance']}"
        )
        assert math.isclose(out["distance"], 20, abs_tol=1e-6)
        assert math.isclose(out["effective_distance"], 40, abs_tol=1e-6)
        assert math.isclose(out["traffic_delay"], 20, abs_tol=1e-6)
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_traffic_key_not_array_invalid():
    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 1}]}
    gp = tmp(json.dumps(graph))
    invalid_cases = [
        json.dumps({"traffic": {"from": "A", "to": "B", "factor": 1}}),
        json.dumps({"traffic": None}),
        json.dumps({"traffic": "invalid"}),
        json.dumps({"traffic": 123}),
    ]
    try:
        for content in invalid_cases:
            tp = tmp(content)
            try:
                proc = run(["--graph", gp, "--from", "A", "--to", "B", "--traffic", tp])
                assert proc.returncode == 2, (
                    f"traffic not array should be invalid, got rc={proc.returncode} for {content[:50]}"
                )
                assert proc.stdout.decode().strip() == ""
            finally:
                os.unlink(tp)
    finally:
        os.unlink(gp)


def test_traffic_direct_array_invalid_elements():
    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 1}]}
    gp = tmp(json.dumps(graph))
    invalid_cases = [
        json.dumps([1, 2, 3]),
        json.dumps(["A", "B"]),
        json.dumps([None]),
        json.dumps([{"from": "A", "to": "B"}]),  # missing factor
        json.dumps([{"from": "A", "to": "B", "factor": 1}, "invalid"]),
    ]
    try:
        for content in invalid_cases:
            tp = tmp(content)
            try:
                proc = run(["--graph", gp, "--from", "A", "--to", "B", "--traffic", tp])
                assert proc.returncode == 2, (
                    f"invalid direct array elements should be exit 2, got {proc.returncode} for {content[:80]}"
                )
            finally:
                os.unlink(tp)
    finally:
        os.unlink(gp)


def test_traffic_duplicate_delay_reset_reverse():
    # First factor only, second factor+delay, last wins should have delay
    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 10}]}
    traffic = {
        "traffic": [
            {"from": "A", "to": "B", "factor": 2},
            {"from": "A", "to": "B", "factor": 1, "delay": 10},
        ]
    }
    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps(traffic))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B", "--traffic", tp])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip())
        assert math.isclose(out["effective_distance"], 20, abs_tol=1e-6), (
            f"last wins with delay 10 should give 20, got {out['effective_distance']}"
        )
        assert math.isclose(out["traffic_delay"], 10, abs_tol=1e-6)
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_batch_2000_with_traffic_relative():
    nodes = [f"N{i}" for i in range(200)]
    edges = [{"from": f"N{i}", "to": f"N{i + 1}", "distance": 1} for i in range(199)]
    graph = {"nodes": nodes, "edges": edges}
    traffic = {
        "traffic": [
            {"from": f"N{i}", "to": f"N{i + 1}", "factor": 1.1}
            for i in range(0, 199, 10)
        ]
    }
    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps(traffic))
    rp1 = tmp(json.dumps([{"source": "N0", "destination": "N199"}]))
    rp = tmp(
        json.dumps(
            [{"source": "N0", "destination": f"N{i % 200}"} for i in range(2000)]
        )
    )
    try:
        start = time.time()
        base = run(["--graph", gp, "--requests", rp1, "--traffic", tp])
        base_elapsed = time.time() - start
        assert base.returncode == 0, base.stderr.decode()[:500]

        start = time.time()
        proc = run(["--graph", gp, "--requests", rp, "--traffic", tp])
        elapsed = time.time() - start
        assert proc.returncode == 0, proc.stderr.decode()[:500]
        # Relative bound: 2000 batch should not be astronomical vs single
        # Previous 40x+1.5 was tight in Docker, use 100x+3 for oracle safety
        assert elapsed <= 100 * base_elapsed + 3.0, (
            f"2000 batch too slow vs single: {elapsed:.3f}s vs baseline {base_elapsed:.3f}s"
        )
        assert len(proc.stdout.decode().strip().splitlines()) == 2000
    finally:
        os.unlink(gp)
        os.unlink(tp)
        os.unlink(rp1)
        os.unlink(rp)


def test_traffic_large_batch_5000_with_traffic_relative():
    nodes = [f"N{i}" for i in range(200)]
    edges = [{"from": f"N{i}", "to": f"N{i + 1}", "distance": 1} for i in range(199)]
    graph = {"nodes": nodes, "edges": edges}
    traffic = {
        "traffic": [
            {"from": f"N{i}", "to": f"N{i + 1}", "factor": 1.1}
            for i in range(0, 199, 20)
        ]
    }
    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps(traffic))
    rp1 = tmp(json.dumps([{"source": "N0", "destination": "N199"}]))
    rp = tmp(
        json.dumps(
            [{"source": "N0", "destination": f"N{i % 200}"} for i in range(5000)]
        )
    )
    try:
        start = time.time()
        base = run(["--graph", gp, "--requests", rp1, "--traffic", tp])
        base_elapsed = time.time() - start
        assert base.returncode == 0

        start = time.time()
        proc = run(["--graph", gp, "--requests", rp, "--traffic", tp])
        elapsed = time.time() - start
        assert proc.returncode == 0, proc.stderr.decode()[:500]
        # Generous relative bound for oracle: 5000 batch vs single
        # 60x+2 was too strict in Docker (3.5s vs 0.003s -> 2.18 fails), use 200x+5
        assert elapsed <= 200 * base_elapsed + 5.0, (
            f"5000 batch too slow vs single: {elapsed:.3f}s vs baseline {base_elapsed:.3f}s"
        )
        assert len(proc.stdout.decode().strip().splitlines()) == 5000
    finally:
        os.unlink(gp)
        os.unlink(tp)
        os.unlink(rp1)
        os.unlink(rp)


def test_traffic_case_sensitive_with_traffic():
    # Node IDs case-sensitive: A vs a distinct, traffic must match exact case
    graph = {
        "nodes": ["A", "a", "B"],
        "edges": [
            {"from": "A", "to": "B", "distance": 5},
            {"from": "a", "to": "B", "distance": 5},
        ],
    }
    traffic = {"traffic": [{"from": "a", "to": "B", "factor": 10}]}
    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps(traffic))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B", "--traffic", tp])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip())
        # A-B has no traffic (factor 1), a-B has factor 10, so A-B should be direct with eff 5
        assert math.isclose(out["effective_distance"], 5, abs_tol=1e-6), (
            f"case sensitive: A-B eff should be 5, got {out['effective_distance']}"
        )
        assert out["path"] == ["A", "B"]

        # Now a->B should be penalized
        proc2 = run(["--graph", gp, "--from", "a", "--to", "B", "--traffic", tp])
        assert proc2.returncode == 0
        out2 = json.loads(proc2.stdout.decode().strip())
        assert math.isclose(out2["effective_distance"], 50, abs_tol=1e-6)
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_special_chars_secondary_raw_with_traffic():
    # Special chars node IDs with secondary raw tie-break under traffic
    graph = {
        "nodes": ["A", "B-1", "B_2", "C"],
        "edges": [
            {"from": "A", "to": "B-1", "distance": 10},
            {"from": "B-1", "to": "C", "distance": 1},
            {"from": "A", "to": "B_2", "distance": 2},
            {"from": "B_2", "to": "C", "distance": 2},
        ],
    }
    traffic = {
        "traffic": [
            {"from": "A", "to": "B-1", "factor": 1, "delay": 1},
            {"from": "B-1", "to": "C", "factor": 1},
            {"from": "A", "to": "B_2", "factor": 2},
            {"from": "B_2", "to": "C", "factor": 4},
        ]
    }
    # Effective both 12, raw 11 vs 4 => pick B_2 path raw smaller
    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps(traffic))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "C", "--traffic", tp])
        assert proc.returncode == 0, proc.stderr.decode()[:300]
        out = json.loads(proc.stdout.decode().strip())
        assert out["path"] == ["A", "B_2", "C"], (
            f"expected B_2 secondary raw, got {out['path']}"
        )
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_duplicate_interleaved_factor_delay():
    # Interleaved factor only and factor+delay, last wins
    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 10}]}
    entries = []
    for i in range(5):
        entries.append({"from": "A", "to": "B", "factor": 1, "delay": i})
        entries.append({"from": "A", "to": "B", "factor": i + 1})
    # Last entry factor 5 no delay => eff 50
    traffic = {"traffic": entries}
    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps(traffic))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B", "--traffic", tp])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip())
        assert math.isclose(out["effective_distance"], 50, abs_tol=1e-6), (
            f"expected 50, got {out['effective_distance']}"
        )
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_very_large_factor_and_small_in_same_path():
    # One edge 1e-12 factor, another 1e12 factor in same path, tests float handling
    graph = {
        "nodes": ["A", "B", "C"],
        "edges": [
            {"from": "A", "to": "B", "distance": 1e6},
            {"from": "B", "to": "C", "distance": 1e6},
        ],
    }
    traffic = {
        "traffic": [
            {"from": "A", "to": "B", "factor": 1e-12},
            {"from": "B", "to": "C", "factor": 1e12},
        ]
    }
    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps(traffic))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "C", "--traffic", tp])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip())
        # Effective = 1e6*1e-12 + 1e6*1e12 = 1e-6 + 1e18 ≈ 1e18
        assert math.isclose(out["effective_distance"], 1e-6 + 1e18, rel_tol=1e-9)
        assert math.isclose(out["distance"], 2e6, rel_tol=1e-9)
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_duplicate_reverse_with_delay():
    # A->B factor1 delay10, B->A factor2 delay0, last wins (reverse direction) => eff 20, delay 0? Actually factor2 delay0 => eff 20
    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 10}]}
    traffic = {
        "traffic": [
            {"from": "A", "to": "B", "factor": 1, "delay": 10},
            {"from": "B", "to": "A", "factor": 2, "delay": 0},
        ]
    }
    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps(traffic))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B", "--traffic", tp])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip())
        assert math.isclose(out["effective_distance"], 20, abs_tol=1e-6)
        assert math.isclose(
            out["traffic_delay"], 10, abs_tol=1e-6
        )  # eff 20 raw10 => delay 10
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_perf_dense_500_nodes_with_traffic():
    # 100 nodes, each to next 15, ~1350 edges, 10 traffic entries, must be <1.5s
    nodes = [f"N{i}" for i in range(100)]
    edges = []
    for i in range(100):
        for j in range(i + 1, min(i + 15, 100)):
            edges.append({"from": f"N{i}", "to": f"N{j}", "distance": float(j - i)})
    graph = {"nodes": nodes, "edges": edges}
    traffic = {
        "traffic": [
            {"from": f"N{i}", "to": f"N{i + 1}", "factor": 3} for i in range(0, 99, 10)
        ]
    }
    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps(traffic))
    try:
        start = time.time()
        proc = run(["--graph", gp, "--from", "N0", "--to", "N99", "--traffic", tp])
        elapsed = time.time() - start
        assert proc.returncode == 0, proc.stderr.decode()[:300]
        assert elapsed < 1.5, f"dense 100 nodes with traffic too slow {elapsed}"
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_secondary_raw_with_float_effective_tie():
    # Effective tie within 1e-9, raw differs due to float, secondary raw must win
    graph = {
        "nodes": ["A", "B", "C", "D"],
        "edges": [
            {"from": "A", "to": "B", "distance": 0.1},
            {"from": "B", "to": "D", "distance": 0.2},
            {"from": "A", "to": "C", "distance": 0.15},
            {"from": "C", "to": "D", "distance": 0.15},
        ],
    }
    traffic = {
        "traffic": [
            {"from": "A", "to": "B", "factor": 1},
            {"from": "B", "to": "D", "factor": 1},
            {"from": "A", "to": "C", "factor": 1},
            {"from": "C", "to": "D", "factor": 1},
        ]
    }
    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps(traffic))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "D", "--traffic", tp])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip())
        # Both effective 0.3, raw both 0.3 tie, lex B<C => A-B-D wins
        assert out["path"] == ["A", "B", "D"], f"got {out['path']}"
        assert math.isclose(out["effective_distance"], 0.3, abs_tol=1e-6)
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_stdlib_only():
    # TBR concern: stdlib-only / no-external-require MUST not tested
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
                if stripped.startswith("github.com") or stripped.startswith(
                    "golang.org"
                ):
                    assert False, f"External require in go.mod: {stripped}"

    proc = subprocess.run(
        ["go", "list", "-f", '{{join .Imports " "}}', "."],
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
        assert "." not in first, (
            f"Non-stdlib import {imp} found via go list - must be stdlib only"
        )


# === FURTHER HARDENING step2 too easy, step1 good: add 20 more hard discriminators (130->150) ===


def test_traffic_perf_dense_200_nodes_5000_edges_with_traffic():
    # 200 nodes, each to next 25, ~4500 edges, must be <2s with traffic map O(1)
    nodes = [f"N{i}" for i in range(200)]
    edges = []
    for i in range(200):
        for j in range(i + 1, min(i + 25, 200)):
            edges.append({"from": f"N{i}", "to": f"N{j}", "distance": float(j - i)})
    graph = {"nodes": nodes, "edges": edges}
    traffic = {
        "traffic": [
            {"from": f"N{i}", "to": f"N{i + 1}", "factor": 2} for i in range(0, 199, 5)
        ]
    }
    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps(traffic))
    try:
        start = time.time()
        proc = run(["--graph", gp, "--from", "N0", "--to", "N199", "--traffic", tp])
        elapsed = time.time() - start
        assert proc.returncode == 0, proc.stderr.decode()[:300]
        assert elapsed < 2.0, (
            f"dense 200 nodes 5000 edges with traffic too slow {elapsed}"
        )
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_factor_and_delay_float_many_decimals():
    graph = {
        "nodes": ["A", "B"],
        "edges": [{"from": "A", "to": "B", "distance": 1.123456789}],
    }
    traffic = {
        "traffic": [
            {"from": "A", "to": "B", "factor": 1.000000001, "delay": 0.000000001}
        ]
    }
    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps(traffic))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B", "--traffic", tp])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip())
        exp_eff = 1.123456789 * 1.000000001 + 0.000000001
        assert math.isclose(out["effective_distance"], exp_eff, abs_tol=1e-6)
        assert math.isclose(out["traffic_delay"], exp_eff - 1.123456789, abs_tol=1e-6)
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_secondary_raw_tie_with_factor_less_than_one():
    # Effective tie, raw differs where factor<1 makes shorter raw appear longer effective? Actually need effective equal
    # A-B distance 10 factor 0.5 => eff 5, B-D 10 factor1 => eff10 total15 raw20
    # A-C distance 5 factor 2 => eff10, C-D 5 factor1 => eff5 total15 raw10
    # Both eff 15, raw 20 vs 10 => pick raw smaller A-C-D
    graph = {
        "nodes": ["A", "B", "C", "D"],
        "edges": [
            {"from": "A", "to": "B", "distance": 10},
            {"from": "B", "to": "D", "distance": 10},
            {"from": "A", "to": "C", "distance": 5},
            {"from": "C", "to": "D", "distance": 5},
        ],
    }
    traffic = {
        "traffic": [
            {"from": "A", "to": "B", "factor": 0.5},
            {"from": "B", "to": "D", "factor": 1},
            {"from": "A", "to": "C", "factor": 2},
            {"from": "C", "to": "D", "factor": 1},
        ]
    }
    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps(traffic))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "D", "--traffic", tp])
        assert proc.returncode == 0, proc.stderr.decode()[:300]
        out = json.loads(proc.stdout.decode().strip())
        assert out["path"] == ["A", "C", "D"], (
            f"secondary raw tie should pick C (raw10 vs 20), got {out['path']}"
        )
        assert math.isclose(out["effective_distance"], 15, abs_tol=1e-6)
        assert math.isclose(out["distance"], 10, abs_tol=1e-6)
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_batch_with_traffic_and_unicode_special():
    graph = {
        "nodes": ["A", "B-1", "C_2", "D.e"],
        "edges": [
            {"from": "A", "to": "B-1", "distance": 1},
            {"from": "B-1", "to": "C_2", "distance": 1},
            {"from": "C_2", "to": "D.e", "distance": 1},
        ],
    }
    traffic = {"traffic": [{"from": "B-1", "to": "C_2", "factor": 2, "delay": 1}]}
    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps(traffic))
    rp = tmp(
        json.dumps(
            [
                {"source": "A", "destination": "D.e"},
                {"source": "B-1", "destination": "C_2"},
            ]
        )
    )
    try:
        proc = run(["--graph", gp, "--requests", rp, "--traffic", tp])
        assert proc.returncode == 0
        lines = proc.stdout.decode().strip().splitlines()
        assert len(lines) == 2
        out0 = json.loads(lines[0])
        assert out0["path"] == ["A", "B-1", "C_2", "D.e"]
        assert math.isclose(out0["effective_distance"], 5, abs_tol=1e-6)
        assert math.isclose(out0["distance"], 3, abs_tol=1e-6)
        out1 = json.loads(lines[1])
        assert math.isclose(out1["effective_distance"], 3, abs_tol=1e-6)
    finally:
        os.unlink(gp)
        os.unlink(tp)
        os.unlink(rp)


def test_traffic_graph_with_extra_nested_fields_and_traffic():
    graph = {
        "nodes": ["A", "B"],
        "edges": [
            {
                "from": "A",
                "to": "B",
                "distance": 5,
                "meta": {"nested": {"x": 1}},
                "extra": [1, 2, 3],
            }
        ],
        "version": 1,
        "extra_top": {"a": {"b": 2}},
    }
    traffic = {
        "traffic": [{"from": "A", "to": "B", "factor": 2, "extra": {"y": 2}}],
        "extra_top": [1, 2],
    }
    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps(traffic))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B", "--traffic", tp])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip())
        assert math.isclose(out["effective_distance"], 10, abs_tol=1e-6)
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_batch_no_route_with_traffic_delay_minus_one():
    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 5}]}
    traffic = {"traffic": [{"from": "A", "to": "B", "factor": 2, "delay": 5}]}
    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps(traffic))
    rp = tmp(
        json.dumps(
            [{"source": "A", "destination": "C"}, {"source": "C", "destination": "B"}]
        )
    )
    try:
        proc = run(["--graph", gp, "--requests", rp, "--traffic", tp])
        assert proc.returncode == 1
        lines = proc.stdout.decode().strip().splitlines()
        assert len(lines) == 2
        for ln in lines:
            out = json.loads(ln)
            assert out["path"] == []
            assert out["distance"] == -1
            assert out["effective_distance"] == -1
            assert out["traffic_delay"] == -1
    finally:
        os.unlink(gp)
        os.unlink(tp)
        os.unlink(rp)


def test_traffic_source_equals_dest_with_traffic():
    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 10}]}
    traffic = {"traffic": [{"from": "A", "to": "B", "factor": 5, "delay": 10}]}
    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps(traffic))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "A", "--traffic", tp])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip())
        assert out["path"] == ["A"]
        assert out["distance"] == 0
        assert out["effective_distance"] == 0
        assert out["traffic_delay"] == 0

        rp = tmp(json.dumps([{"source": "A", "destination": "A"}]))
        try:
            proc2 = run(["--graph", gp, "--requests", rp, "--traffic", tp])
            assert proc2.returncode == 0
            out2 = json.loads(proc2.stdout.decode().strip().splitlines()[0])
            assert out2["path"] == ["A"]
            assert out2["effective_distance"] == 0
        finally:
            os.unlink(rp)
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_perf_1000_nodes_line_with_traffic():
    nodes = [f"N{i}" for i in range(1000)]
    edges = [{"from": f"N{i}", "to": f"N{i + 1}", "distance": 1} for i in range(999)]
    graph = {"nodes": nodes, "edges": edges}
    traffic = {
        "traffic": [
            {"from": f"N{i}", "to": f"N{i + 1}", "factor": 1.5}
            for i in range(0, 999, 100)
        ]
    }
    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps(traffic))
    try:
        start = time.time()
        proc = run(["--graph", gp, "--from", "N0", "--to", "N999", "--traffic", tp])
        elapsed = time.time() - start
        assert proc.returncode == 0
        assert elapsed < 2.0, f"1000 line with traffic too slow {elapsed}"
        out = json.loads(proc.stdout.decode().strip())
        assert out["distance"] == 999
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_direct_array_with_extra_fields_valid():
    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 5}]}
    traffic = [
        {"from": "A", "to": "B", "factor": 2, "extra": "ignore", "meta": {"x": 1}}
    ]
    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps(traffic))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B", "--traffic", tp])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip())
        assert math.isclose(out["effective_distance"], 10, abs_tol=1e-6)
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_isolated_nodes_with_traffic_no_route():
    graph = {
        "nodes": ["A", "B", "C"],
        "edges": [{"from": "A", "to": "B", "distance": 1}],
    }
    traffic = {"traffic": [{"from": "A", "to": "B", "factor": 2}]}
    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps(traffic))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "C", "--traffic", tp])
        assert proc.returncode == 1
        out = json.loads(proc.stdout.decode().strip())
        assert out["path"] == []
        assert out["effective_distance"] == -1

        proc2 = run(["--graph", gp, "--from", "C", "--to", "A", "--traffic", tp])
        assert proc2.returncode == 1
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_help_with_traffic_equals_and_requests():
    proc = run(["--help", "--traffic=dummy", "--requests=dummy", "--graph=dummy"])
    assert proc.returncode == 0
    low = proc.stdout.decode().lower()
    for kw in ["graph", "from", "to", "requests", "traffic", "help"]:
        assert kw in low

    proc2 = run(["--help", "--traffic", "dummy"])
    assert proc2.returncode == 0
    assert "traffic" in proc2.stdout.decode().lower()


def test_traffic_unknown_flag_with_equals_in_step2():
    gp = tmp(
        json.dumps(
            {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 1}]}
        )
    )
    try:
        for flag in ["--unknown=val", "--foobar=xxx"]:
            proc = run([flag, "--graph", gp, "--from", "A", "--to", "B"])
            assert proc.returncode == 2, f"{flag} should be unknown ->2"
        # unknown with traffic present also invalid
        tp = tmp(json.dumps([{"from": "A", "to": "B", "factor": 1}]))
        try:
            proc2 = run(
                [
                    "--graph",
                    gp,
                    "--from",
                    "A",
                    "--to",
                    "B",
                    "--traffic",
                    tp,
                    "--unknown=1",
                ]
            )
            assert proc2.returncode == 2
        finally:
            os.unlink(tp)
    finally:
        os.unlink(gp)


# === FURTHER HARDENING step2 too easy again (146->170) ===


def test_traffic_duplicate_traffic_many_with_extra_and_version():
    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 10}]}
    entries = []
    for i in range(100):
        entries.append(
            {"from": "A", "to": "B", "factor": 1, "extra": f"e{i}", "version": i}
        )
    entries.append({"from": "A", "to": "B", "factor": 3, "delay": 2, "extra": "last"})
    traffic = {"traffic": entries, "version": 99, "extra_top": "ignore"}
    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps(traffic))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B", "--traffic", tp])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip())
        assert math.isclose(out["effective_distance"], 32, abs_tol=1e-6)
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_batch_with_traffic_all_no_route():
    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 1}]}
    traffic = {"traffic": [{"from": "A", "to": "B", "factor": 2}]}
    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps(traffic))
    rp = tmp(
        json.dumps(
            [{"source": "X", "destination": "Y"}, {"source": "A", "destination": "X"}]
        )
    )
    try:
        proc = run(["--graph", gp, "--requests", rp, "--traffic", tp])
        assert proc.returncode == 1
        lines = proc.stdout.decode().strip().splitlines()
        assert len(lines) == 2
        for ln in lines:
            out = json.loads(ln)
            assert (
                out["path"] == []
                and out["effective_distance"] == -1
                and out["traffic_delay"] == -1
            )
    finally:
        os.unlink(gp)
        os.unlink(tp)
        os.unlink(rp)


def test_traffic_batch_with_traffic_source_equals_dest_mixed():
    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 5}]}
    traffic = {"traffic": [{"from": "A", "to": "B", "factor": 2}]}
    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps(traffic))
    rp = tmp(
        json.dumps(
            [
                {"source": "A", "destination": "A"},
                {"source": "A", "destination": "B"},
                {"source": "B", "destination": "B"},
            ]
        )
    )
    try:
        proc = run(["--graph", gp, "--requests", rp, "--traffic", tp])
        assert proc.returncode == 0
        lines = proc.stdout.decode().strip().splitlines()
        assert len(lines) == 3
        out0 = json.loads(lines[0])
        assert out0["path"] == ["A"] and out0["effective_distance"] == 0
        out1 = json.loads(lines[1])
        assert out1["path"] == ["A", "B"] and math.isclose(
            out1["effective_distance"], 10, abs_tol=1e-6
        )
        out2 = json.loads(lines[2])
        assert out2["path"] == ["B"] and out2["effective_distance"] == 0
    finally:
        os.unlink(gp)
        os.unlink(tp)
        os.unlink(rp)


def test_traffic_help_with_equals_and_traffic_and_requests_mixed():
    proc = run(
        [
            "--help",
            "--graph=dummy",
            "--from=A",
            "--to=B",
            "--traffic=dummy",
            "--requests=dummy",
        ]
    )
    assert proc.returncode == 0
    low = proc.stdout.decode().lower()
    for kw in ["graph", "from", "to", "requests", "traffic", "help"]:
        assert kw in low


def test_traffic_traffic_file_with_extra_fields_and_version():
    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 5}]}
    traffic = {
        "traffic": [{"from": "A", "to": "B", "factor": 2}],
        "version": "1.0",
        "extra": {"x": 1},
        "meta": [1, 2, 3],
    }
    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps(traffic))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B", "--traffic", tp])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip())
        assert math.isclose(out["effective_distance"], 10, abs_tol=1e-6)
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_duplicate_many_with_factor_and_delay_both_hard_extra():
    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 10}]}
    entries = []
    for i in range(20):
        entries.append(
            {"from": "A", "to": "B", "factor": 1, "delay": i, "extra": f"e{i}"}
        )
        entries.append({"from": "B", "to": "A", "factor": 2, "delay": i * 2})
    entries.append({"from": "A", "to": "B", "factor": 1.5, "delay": 5})
    traffic = {"traffic": entries}
    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps(traffic))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B", "--traffic", tp])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip())
        assert math.isclose(out["effective_distance"], 20, abs_tol=1e-6)  # 10*1.5+5=20
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_lex_secondary_raw_with_special_chars_and_traffic():
    # A-B-1 vs A-B_2 with special chars, secondary raw tie under traffic
    graph = {
        "nodes": ["A", "B-1", "B_2", "D"],
        "edges": [
            {"from": "A", "to": "B-1", "distance": 2},
            {"from": "B-1", "to": "D", "distance": 10},
            {"from": "A", "to": "B_2", "distance": 10},
            {"from": "B_2", "to": "D", "distance": 2},
        ],
    }
    traffic = {
        "traffic": [
            {"from": "A", "to": "B-1", "factor": 1},
            {"from": "B-1", "to": "D", "factor": 1},
            {"from": "A", "to": "B_2", "factor": 1},
            {"from": "B_2", "to": "D", "factor": 1},
        ]
    }
    # Both effective 12, raw 12 tie, lex B-1 (45) vs B_2 (95) => B-1 wins because '-' < '_'
    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps(traffic))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "D", "--traffic", tp])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip())
        assert out["path"] == ["A", "B-1", "D"], (
            f"lex special chars tie, expected B-1, got {out['path']}"
        )
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_graph_duplicate_exact_min_plus_traffic_tie():
    # Duplicate edges with extra fields, keep min, then tie-breaking with traffic
    graph = {
        "nodes": ["A", "B", "C"],
        "edges": [
            {"from": "A", "to": "B", "distance": 10, "extra": "ignore"},
            {"from": "A", "to": "B", "distance": 3, "extra": "ignore2"},
            {"from": "B", "to": "C", "distance": 4},
            {"from": "A", "to": "C", "distance": 10},
        ],
    }
    traffic = {
        "traffic": [
            {"from": "A", "to": "B", "factor": 2},
            {"from": "B", "to": "C", "factor": 1},
            {"from": "A", "to": "C", "factor": 1},
        ]
    }
    # A-B-C: raw 3+4=7 eff 3*2+4=10, A-C: raw10 eff10 tie eff, raw 7 vs 10 -> A-B-C wins secondary raw
    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps(traffic))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "C", "--traffic", tp])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip())
        assert out["path"] == ["A", "B", "C"], (
            f"expected A-B-C secondary raw, got {out['path']}"
        )
        assert math.isclose(out["distance"], 7, abs_tol=1e-6)
        assert math.isclose(out["effective_distance"], 10, abs_tol=1e-6)
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_reroutes_away_from_raw_shortest():
    # Gap 1: effective-optimal must differ from raw-optimal
    # Raw-shortest A-B-D distance 2, but traffic makes it 100x -> effective 200
    # Effective-optimal A-C-D distance 20, effective 20 (default factor 1.0)
    # A model that runs Dijkstra on raw then computes effective will return A-B-D and fail
    graph = {
        "nodes": ["A", "B", "C", "D"],
        "edges": [
            {"from": "A", "to": "B", "distance": 1},
            {"from": "B", "to": "D", "distance": 1},
            {"from": "A", "to": "C", "distance": 10},
            {"from": "C", "to": "D", "distance": 10},
        ],
    }
    traffic = {
        "traffic": [
            {"from": "A", "to": "B", "factor": 100},
            {"from": "B", "to": "D", "factor": 100},
        ]
    }
    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps(traffic))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "D", "--traffic", tp])
        assert proc.returncode == 0, proc.stderr.decode()[:300]
        out = json.loads(proc.stdout.decode().strip())
        assert out["path"] == ["A", "C", "D"], (
            f"traffic must reroute away from raw-shortest, expected A-C-D got {out['path']}"
        )
        assert math.isclose(out["distance"], 20, abs_tol=1e-6), (
            f"raw should be 20 for A-C-D, got {out['distance']}"
        )
        assert math.isclose(out["effective_distance"], 20, abs_tol=1e-6), (
            f"effective should be 20 (default factor 1.0 on untraffic'd edges), got {out['effective_distance']}"
        )
        assert math.isclose(out["traffic_delay"], 0, abs_tol=1e-6)
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_delay_accumulates_per_edge():
    # Gap 2: delay is per-edge summed, not once per path
    graph = {
        "nodes": ["A", "B", "C", "D"],
        "edges": [
            {"from": "A", "to": "B", "distance": 1},
            {"from": "B", "to": "C", "distance": 1},
            {"from": "C", "to": "D", "distance": 1},
            {"from": "A", "to": "D", "distance": 5},
        ],
    }
    traffic = {
        "traffic": [
            {"from": f, "to": t, "factor": 1, "delay": 2}
            for f, t in [("A", "B"), ("B", "C"), ("C", "D"), ("A", "D")]
        ]
    }
    # 3-edge path: (1+2)*3 = 9 ; direct: 5+2 =7 -> direct wins
    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps(traffic))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "D", "--traffic", tp])
        assert proc.returncode == 0, proc.stderr.decode()[:300]
        out = json.loads(proc.stdout.decode().strip())
        assert out["path"] == ["A", "D"], (
            f"delay must accumulate per-edge, expected direct A-D (7 vs 9), got {out['path']}"
        )
        assert math.isclose(out["distance"], 5, abs_tol=1e-6)
        assert math.isclose(out["effective_distance"], 7, abs_tol=1e-6)
        assert math.isclose(out["traffic_delay"], 2, abs_tol=1e-6)
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_batch_same_source_amortized():
    # Step2 has no perf pressure, but runs same batch path as step1 with traffic layered (strictly more expensive)
    # Same-source batch can be amortized: one Dijkstra per distinct source answers all
    # Per-request implementation costs ~200x for same source
    # This targets untouched surface in step2, aims to pull opus off 9/10
    nodes = [f"N{i}" for i in range(500)]
    edges = [{"from": f"N{i}", "to": f"N{i + 1}", "distance": 1} for i in range(499)]
    edges += [
        {"from": f"N{i}", "to": f"N{i + 10}", "distance": 5} for i in range(0, 490, 10)
    ]
    graph = {"nodes": nodes, "edges": edges}
    traffic = {
        "traffic": [
            {"from": f"N{i}", "to": f"N{i + 1}", "factor": 1.5, "delay": 1}
            for i in range(499)
        ]
    }
    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps(traffic))
    # 200 requests all from N0 (same source)
    same_reqs = [{"source": "N0", "destination": f"N{i}"} for i in range(1, 201)]
    # 200 requests with 200 distinct sources
    multi_reqs = [
        {"source": f"N{i}", "destination": f"N{(i * 7) % 500}"} for i in range(200)
    ]
    rp_same = tmp(json.dumps(same_reqs))
    rp_multi = tmp(json.dumps(multi_reqs))
    try:
        start = time.time()
        proc_same = run(["--graph", gp, "--requests", rp_same, "--traffic", tp])
        t_same = time.time() - start
        assert proc_same.returncode == 0, proc_same.stderr.decode()[:500]

        start = time.time()
        proc_multi = run(["--graph", gp, "--requests", rp_multi, "--traffic", tp])
        t_multi = time.time() - start
        assert proc_multi.returncode == 0, proc_multi.stderr.decode()[:500]

        # Same source should be significantly faster than multi-source if amortized
        # Host-independent relative bound: same-source <=35% of multi-source
        assert t_same <= 0.35 * t_multi, (
            f"same-source batch should amortize: t_same={t_same:.3f}s vs t_multi={t_multi:.3f}s, "
            f"expected t_same <=0.35*t_multi. Per-request implementation costs ~200x."
        )
    finally:
        os.unlink(gp)
        os.unlink(tp)
        os.unlink(rp_same)
        os.unlink(rp_multi)


# ==================== NEW EXTRA HARD TESTS v2 – Step2 harder ====================


def test_traffic_file_bom_must_not_crash():
    import tempfile, os, subprocess, json

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
            timeout=5,
        )

    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 1}]}
    gp = tmp_bytes(json.dumps(graph).encode())
    # traffic with BOM
    tp = tmp_bytes(b"\xef\xbb\xbf" + json.dumps({"traffic": []}).encode())
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B", "--traffic", tp])
        assert proc.returncode == 2, (
            f"BOM traffic should be invalid not crash, got {proc.returncode}"
        )
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_file_trailing_comma_invalid():
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
            timeout=5,
        )

    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 1}]}
    gp = tmp(json.dumps(graph))
    bad = '{"traffic":[{"from":"A","to":"B","factor":1},]}'
    tp = tmp(bad)
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B", "--traffic", tp])
        assert proc.returncode == 2
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_file_comment_invalid():
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
            timeout=5,
        )

    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 1}]}
    gp = tmp(json.dumps(graph))
    bad = '{ // comment\n"traffic":[]}'
    tp = tmp(bad)
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B", "--traffic", tp])
        assert proc.returncode == 2
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_entries_with_leading_trailing_spaces_invalid():
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
            timeout=5,
        )

    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 1}]}
    gp = tmp(json.dumps(graph))
    cases = [
        {"from": " A", "to": "B", "factor": 1},
        {"from": "A ", "to": "B", "factor": 1},
        {"from": "A", "to": " B", "factor": 1},
    ]
    try:
        for bad in cases:
            tp = tmp(json.dumps({"traffic": [bad]}))
            try:
                proc = run(["--graph", gp, "--from", "A", "--to", "B", "--traffic", tp])
                assert proc.returncode == 2, (
                    f"leading/trailing space in traffic {bad} should be invalid"
                )
            finally:
                os.unlink(tp)
    finally:
        os.unlink(gp)


def test_traffic_factor_scientific_plus_valid():
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
            timeout=5,
        )

    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 10}]}
    gp = tmp(json.dumps(graph))
    tp = tmp('{"traffic":[{"from":"A","to":"B","factor":1e+2}]}')
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B", "--traffic", tp])
        assert proc.returncode == 0, (
            f"1e+2 factor should be valid, got {proc.returncode} stderr={proc.stderr.decode()}"
        )
        out = json.loads(proc.stdout.decode().strip())
        assert math.isclose(out["effective_distance"], 1000, rel_tol=1e-9)
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_output_fields_strict_with_traffic():
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
            timeout=5,
        )

    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 5}]}
    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps({"traffic": [{"from": "A", "to": "B", "factor": 2}]}))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B", "--traffic", tp])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip())
        assert set(out.keys()) == {
            "path",
            "distance",
            "effective_distance",
            "traffic_delay",
        }, f"strict 4 keys for traffic single, got {out.keys()}"
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_batch_output_fields_strict_with_traffic():
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
            timeout=5,
        )

    graph = {
        "nodes": ["A", "B", "C"],
        "edges": [
            {"from": "A", "to": "B", "distance": 1},
            {"from": "B", "to": "C", "distance": 1},
        ],
    }
    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps({"traffic": [{"from": "A", "to": "B", "factor": 1}]}))
    rp = tmp(json.dumps([{"source": "A", "destination": "C"}]))
    try:
        proc = run(["--graph", gp, "--requests", rp, "--traffic", tp])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip().splitlines()[0])
        assert set(out.keys()) == {
            "source",
            "destination",
            "path",
            "distance",
            "effective_distance",
            "traffic_delay",
        }
    finally:
        os.unlink(gp)
        os.unlink(tp)
        os.unlink(rp)


def test_traffic_reroute_due_to_delay_only():
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
            timeout=5,
        )

    graph = {
        "nodes": ["A", "B", "C", "D"],
        "edges": [
            {"from": "A", "to": "B", "distance": 5},
            {"from": "B", "to": "D", "distance": 5},
            {"from": "A", "to": "C", "distance": 5},
            {"from": "C", "to": "D", "distance": 5},
        ],
    }
    traffic = {
        "traffic": [
            {"from": "A", "to": "B", "factor": 1, "delay": 100},
            {"from": "B", "to": "D", "factor": 1, "delay": 0},
            {"from": "A", "to": "C", "factor": 1, "delay": 0},
            {"from": "C", "to": "D", "factor": 1, "delay": 0},
        ]
    }
    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps(traffic))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "D", "--traffic", tp])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip())
        assert out["path"] == ["A", "C", "D"], (
            f"delay-only should reroute to A-C-D, got {out['path']}"
        )
        assert math.isclose(out["effective_distance"], 10, abs_tol=1e-6)
        assert math.isclose(out["traffic_delay"], 0, abs_tol=1e-6)
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_large_graph_5000_nodes_with_traffic():
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
            timeout=20,
        )

    nodes = [f"N{i}" for i in range(5000)]
    edges = [{"from": f"N{i}", "to": f"N{i + 1}", "distance": 1} for i in range(4999)]
    graph = {"nodes": nodes, "edges": edges}
    traffic = {
        "traffic": [
            {"from": f"N{i}", "to": f"N{i + 1}", "factor": 1.5}
            for i in range(0, 4999, 500)
        ]
    }
    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps(traffic))
    try:
        start = time.time()
        proc = run(["--graph", gp, "--from", "N0", "--to", "N4999", "--traffic", tp])
        elapsed = time.time() - start
        assert proc.returncode == 0, (
            f"5000 nodes with traffic rc={proc.returncode} stderr={proc.stderr.decode()[:300]}"
        )
        assert elapsed < 5.5, f"too slow 5000 nodes with traffic {elapsed}"
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_batch_2000_with_traffic_performance():
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
            timeout=30,
        )

    nodes = [f"N{i}" for i in range(200)]
    edges = [{"from": f"N{i}", "to": f"N{i + 1}", "distance": 1} for i in range(199)]
    graph = {"nodes": nodes, "edges": edges}
    traffic = {
        "traffic": [
            {"from": f"N{i}", "to": f"N{i + 1}", "factor": 1.1}
            for i in range(0, 199, 10)
        ]
    }
    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps(traffic))
    reqs = [{"source": "N0", "destination": f"N{i % 200}"} for i in range(2000)]
    rp = tmp(json.dumps(reqs))
    try:
        start = time.time()
        proc = run(["--graph", gp, "--requests", rp, "--traffic", tp])
        elapsed = time.time() - start
        assert proc.returncode == 0
        assert elapsed < 7.5, f"too slow 2000 batch with traffic {elapsed}"
        assert len(proc.stdout.decode().strip().splitlines()) == 2000
    finally:
        os.unlink(gp)
        os.unlink(tp)
        os.unlink(rp)


def test_traffic_tie_break_deeper_with_traffic():
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
            timeout=5,
        )

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
    traffic = {
        "traffic": [
            {"from": "A", "to": "B1", "factor": 1},
            {"from": "A", "to": "C1", "factor": 1},
            {"from": "B1", "to": "B2", "factor": 1},
        ]
    }
    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps(traffic))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "Z", "--traffic", tp])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip())
        assert out["path"] == ["A", "B1", "B2", "Z"], (
            f"deeper tie with traffic should pick B1-B2-Z, got {out['path']}"
        )
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_direct_array_trailing_comma_invalid():
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
            timeout=5,
        )

    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 1}]}
    gp = tmp(json.dumps(graph))
    bad = '[{"from":"A","to":"B","factor":1},]'
    tp = tmp(bad)
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B", "--traffic", tp])
        assert proc.returncode == 2
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_request_with_leading_space_no_route_with_traffic():
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
            timeout=5,
        )

    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 1}]}
    traffic = {"traffic": [{"from": "A", "to": "B", "factor": 2}]}
    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps(traffic))
    rp = tmp(json.dumps([{"source": " A", "destination": "B"}]))
    try:
        proc = run(["--graph", gp, "--requests", rp, "--traffic", tp])
        assert proc.returncode == 1, (
            f"leading space source with traffic should be no-route"
        )
        out = json.loads(proc.stdout.decode().strip().splitlines()[0])
        assert out["path"] == [] and out["effective_distance"] == -1
    finally:
        os.unlink(gp)
        os.unlink(tp)
        os.unlink(rp)


def test_traffic_batch_with_no_route_and_valid_mixed_traffic_hard():
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
            timeout=5,
        )

    graph = {
        "nodes": ["A", "B", "Isolated"],
        "edges": [{"from": "A", "to": "B", "distance": 5}],
    }
    traffic = {"traffic": [{"from": "A", "to": "B", "factor": 2}]}
    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps(traffic))
    rp = tmp(
        json.dumps(
            [
                {"source": "A", "destination": "B"},
                {"source": "A", "destination": "Isolated"},
                {"source": "Isolated", "destination": "B"},
            ]
        )
    )
    try:
        proc = run(["--graph", gp, "--requests", rp, "--traffic", tp])
        assert proc.returncode == 1
        lines = proc.stdout.decode().strip().splitlines()
        assert len(lines) == 3
        o0 = json.loads(lines[0])
        import math

        assert math.isclose(o0["effective_distance"], 10, abs_tol=1e-6)
        o1 = json.loads(lines[1])
        assert o1["effective_distance"] == -1
        o2 = json.loads(lines[2])
        assert o2["effective_distance"] == -1
    finally:
        os.unlink(gp)
        os.unlink(tp)
        os.unlink(rp)


def test_traffic_default_factor_one_for_untraffic_edge_v2():
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
            timeout=5,
        )

    graph = {
        "nodes": ["A", "B", "C"],
        "edges": [
            {"from": "A", "to": "B", "distance": 5},
            {"from": "B", "to": "C", "distance": 5},
            {"from": "A", "to": "C", "distance": 20},
        ],
    }
    traffic = {"traffic": [{"from": "A", "to": "B", "factor": 10}]}
    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps(traffic))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "C", "--traffic", tp])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip())
        # A-B effective 50, B-C effective 5 (default factor 1) total 55 vs direct 20 → direct wins
        assert out["path"] == ["A", "C"], (
            f"untraffic edge should default factor 1, expected direct A-C, got {out['path']}"
        )
    finally:
        os.unlink(gp)
        os.unlink(tp)

# ==================== NEW GIGA HARD TESTS v3 – Step2 only harder ====================

def test_traffic_effective_formula_discrimination_multi_edge():
    import tempfile, os, subprocess, json, math
    def tmp(c):
        f=tempfile.NamedTemporaryFile(delete=False,suffix=".json",mode="w")
        f.write(c); f.close(); return f.name
    def run(args):
        return subprocess.run(["/app/router"]+args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5)
    # Test that effective = raw*factor+delay not (raw+delay)*factor
    # Edge A-B raw10 factor2 delay5 => eff 25 correct, 30 if wrong formula
    # Path A-B-C: B-C raw10 factor1 delay0 => eff 10, total eff 35 correct vs 40 wrong
    # Direct A-C raw100 factor1 delay0 => eff 100, so path A-B-C should win (35 vs 100)
    # If agent uses (raw+delay)*factor, A-B eff (10+5)*2=30, total 40, still wins but with different eff value 40 not 35
    # So check effective value
    graph={"nodes":["A","B","C"],"edges":[{"from":"A","to":"B","distance":10},{"from":"B","to":"C","distance":10},{"from":"A","to":"C","distance":100}]}
    traffic={"traffic":[{"from":"A","to":"B","factor":2,"delay":5}]}
    gp=tmp(json.dumps(graph)); tp=tmp(json.dumps(traffic))
    try:
        proc=run(["--graph",gp,"--from","A","--to","C","--traffic",tp])
        assert proc.returncode==0, proc.stderr.decode()[:500]
        out=json.loads(proc.stdout.decode().strip())
        assert out["path"]==["A","B","C"]
        assert math.isclose(out["distance"],20, abs_tol=1e-6)
        assert math.isclose(out["effective_distance"],35, abs_tol=1e-6), f"expected 10*2+5 +10 =35, got {out['effective_distance']}"
        assert math.isclose(out["traffic_delay"],15, abs_tol=1e-6)
    finally:
        os.unlink(gp); os.unlink(tp)


def test_traffic_factor_zero_delay_reset_last_wins():
    import tempfile, os, subprocess, json, math
    def tmp(c):
        f=tempfile.NamedTemporaryFile(delete=False,suffix=".json",mode="w")
        f.write(c); f.close(); return f.name
    def run(args):
        return subprocess.run(["/app/router"]+args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5)
    graph={"nodes":["A","B"],"edges":[{"from":"A","to":"B","distance":10}]}
    gp=tmp(json.dumps(graph))
    # first entry factor1 delay100, second factor2 without delay -> delay reset to 0, eff 20 not 120
    traffic={"traffic":[{"from":"A","to":"B","factor":1,"delay":100},{"from":"A","to":"B","factor":2}]}
    tp=tmp(json.dumps(traffic))
    try:
        proc=run(["--graph",gp,"--from","A","--to","B","--traffic",tp])
        assert proc.returncode==0
        out=json.loads(proc.stdout.decode().strip())
        assert math.isclose(out["effective_distance"],20, abs_tol=1e-6), f"delay reset expected 20, got {out['effective_distance']}"
        assert math.isclose(out["traffic_delay"],10, abs_tol=1e-6)
    finally:
        os.unlink(gp); os.unlink(tp)


def test_traffic_duplicate_same_factor_different_delay_last_wins():
    import tempfile, os, subprocess, json, math
    def tmp(c):
        f=tempfile.NamedTemporaryFile(delete=False,suffix=".json",mode="w")
        f.write(c); f.close(); return f.name
    def run(args):
        return subprocess.run(["/app/router"]+args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5)
    graph={"nodes":["A","B"],"edges":[{"from":"A","to":"B","distance":10}]}
    gp=tmp(json.dumps(graph))
    traffic={"traffic":[{"from":"A","to":"B","factor":1,"delay":1},{"from":"A","to":"B","factor":1,"delay":99}]}
    tp=tmp(json.dumps(traffic))
    try:
        proc=run(["--graph",gp,"--from","A","--to","B","--traffic",tp])
        assert proc.returncode==0
        out=json.loads(proc.stdout.decode().strip())
        assert math.isclose(out["effective_distance"],109, abs_tol=1e-6)
    finally:
        os.unlink(gp); os.unlink(tp)


def test_traffic_factor_very_small_and_delay_large():
    import tempfile, os, subprocess, json, math
    def tmp(c):
        f=tempfile.NamedTemporaryFile(delete=False,suffix=".json",mode="w")
        f.write(c); f.close(); return f.name
    def run(args):
        return subprocess.run(["/app/router"]+args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5)
    graph={"nodes":["A","B"],"edges":[{"from":"A","to":"B","distance":100}]}
    gp=tmp(json.dumps(graph))
    traffic={"traffic":[{"from":"A","to":"B","factor":0.001,"delay":1000}]}
    tp=tmp(json.dumps(traffic))
    try:
        proc=run(["--graph",gp,"--from","A","--to","B","--traffic",tp])
        assert proc.returncode==0
        out=json.loads(proc.stdout.decode().strip())
        # eff = 100*0.001+1000=1000.1
        assert math.isclose(out["effective_distance"],1000.1, rel_tol=1e-6)
        assert math.isclose(out["traffic_delay"],900.1, rel_tol=1e-6)
    finally:
        os.unlink(gp); os.unlink(tp)


def test_traffic_delay_only_complex_reroute():
    import tempfile, os, subprocess, json, math
    def tmp(c):
        f=tempfile.NamedTemporaryFile(delete=False,suffix=".json",mode="w")
        f.write(c); f.close(); return f.name
    def run(args):
        return subprocess.run(["/app/router"]+args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5)
    graph={"nodes":["A","B","C","D"],"edges":[
        {"from":"A","to":"B","distance":1},
        {"from":"B","to":"D","distance":1},
        {"from":"A","to":"C","distance":2},
        {"from":"C","to":"D","distance":2}]}
    traffic={"traffic":[
        {"from":"A","to":"B","factor":1,"delay":50},
        {"from":"B","to":"D","factor":1,"delay":0},
        {"from":"A","to":"C","factor":1,"delay":0},
        {"from":"C","to":"D","factor":1,"delay":0}]}
    gp=tmp(json.dumps(graph)); tp=tmp(json.dumps(traffic))
    try:
        proc=run(["--graph",gp,"--from","A","--to","D","--traffic",tp])
        assert proc.returncode==0
        out=json.loads(proc.stdout.decode().strip())
        assert out["path"]==["A","C","D"], f"delay-only reroute expected A-C-D, got {out['path']}"
    finally:
        os.unlink(gp); os.unlink(tp)


def test_traffic_file_with_empty_object_entry_invalid():
    import tempfile, os, subprocess, json
    def tmp(c):
        f=tempfile.NamedTemporaryFile(delete=False,suffix=".json",mode="w")
        f.write(c); f.close(); return f.name
    def run(args):
        return subprocess.run(["/app/router"]+args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5)
    graph={"nodes":["A","B"],"edges":[{"from":"A","to":"B","distance":1}]}
    gp=tmp(json.dumps(graph))
    tp=tmp(json.dumps({"traffic":[{"from":"A","to":"B","factor":1},{}]}))
    try:
        proc=run(["--graph",gp,"--from","A","--to","B","--traffic",tp])
        assert proc.returncode==2
    finally:
        os.unlink(gp); os.unlink(tp)


def test_traffic_file_with_factor_missing_but_delay_present_invalid():
    import tempfile, os, subprocess, json
    def tmp(c):
        f=tempfile.NamedTemporaryFile(delete=False,suffix=".json",mode="w")
        f.write(c); f.close(); return f.name
    def run(args):
        return subprocess.run(["/app/router"]+args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5)
    graph={"nodes":["A","B"],"edges":[{"from":"A","to":"B","distance":1}]}
    gp=tmp(json.dumps(graph))
    tp=tmp(json.dumps({"traffic":[{"from":"A","to":"B","delay":5}]}))
    try:
        proc=run(["--graph",gp,"--from","A","--to","B","--traffic",tp])
        assert proc.returncode==2
    finally:
        os.unlink(gp); os.unlink(tp)


def test_traffic_file_with_from_equals_to_invalid():
    import tempfile, os, subprocess, json
    def tmp(c):
        f=tempfile.NamedTemporaryFile(delete=False,suffix=".json",mode="w")
        f.write(c); f.close(); return f.name
    def run(args):
        return subprocess.run(["/app/router"]+args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5)
    graph={"nodes":["A","B"],"edges":[{"from":"A","to":"B","distance":1}]}
    gp=tmp(json.dumps(graph))
    tp=tmp(json.dumps({"traffic":[{"from":"A","to":"A","factor":1}]}))
    try:
        proc=run(["--graph",gp,"--from","A","--to","B","--traffic",tp])
        assert proc.returncode==2
    finally:
        os.unlink(gp); os.unlink(tp)


def test_traffic_file_with_nonexisting_edge_invalid():
    import tempfile, os, subprocess, json
    def tmp(c):
        f=tempfile.NamedTemporaryFile(delete=False,suffix=".json",mode="w")
        f.write(c); f.close(); return f.name
    def run(args):
        return subprocess.run(["/app/router"]+args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5)
    graph={"nodes":["A","B","C"],"edges":[{"from":"A","to":"B","distance":1}]}
    gp=tmp(json.dumps(graph))
    tp=tmp(json.dumps({"traffic":[{"from":"B","to":"C","factor":1}]}))
    try:
        proc=run(["--graph",gp,"--from","A","--to","B","--traffic",tp])
        assert proc.returncode==2, "traffic for edge not in graph should be invalid"
    finally:
        os.unlink(gp); os.unlink(tp)


def test_traffic_file_with_factor_string_invalid():
    import tempfile, os, subprocess, json
    def tmp(c):
        f=tempfile.NamedTemporaryFile(delete=False,suffix=".json",mode="w")
        f.write(c); f.close(); return f.name
    def run(args):
        return subprocess.run(["/app/router"]+args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5)
    graph={"nodes":["A","B"],"edges":[{"from":"A","to":"B","distance":1}]}
    gp=tmp(json.dumps(graph))
    for bad_factor in ['"2"', 'true', 'null', '{}', '[]']:
        tp=tmp('{"traffic":[{"from":"A","to":"B","factor":'+bad_factor+'}]}')
        try:
            proc=run(["--graph",gp,"--from","A","--to","B","--traffic",tp])
            assert proc.returncode==2, f"factor {bad_factor} should be invalid"
        finally:
            os.unlink(tp)
    os.unlink(gp)


def test_traffic_file_with_delay_string_invalid():
    import tempfile, os, subprocess, json
    def tmp(c):
        f=tempfile.NamedTemporaryFile(delete=False,suffix=".json",mode="w")
        f.write(c); f.close(); return f.name
    def run(args):
        return subprocess.run(["/app/router"]+args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5)
    graph={"nodes":["A","B"],"edges":[{"from":"A","to":"B","distance":1}]}
    gp=tmp(json.dumps(graph))
    for bad_delay in ['"5"', 'true', 'null', '{}', '[]', '-1']:
        tp=tmp('{"traffic":[{"from":"A","to":"B","factor":1,"delay":'+bad_delay+'}]}')
        try:
            proc=run(["--graph",gp,"--from","A","--to","B","--traffic",tp])
            assert proc.returncode==2, f"delay {bad_delay} should be invalid"
        finally:
            os.unlink(tp)
    os.unlink(gp)


def test_traffic_wrapper_null_invalid_vs_empty_valid():
    import tempfile, os, subprocess, json
    def tmp(c):
        f=tempfile.NamedTemporaryFile(delete=False,suffix=".json",mode="w")
        f.write(c); f.close(); return f.name
    def run(args):
        return subprocess.run(["/app/router"]+args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5)
    graph={"nodes":["A","B"],"edges":[{"from":"A","to":"B","distance":1}]}
    gp=tmp(json.dumps(graph))
    tp_null=tmp('{"traffic":null}')
    tp_empty=tmp('{"traffic":[]}')
    tp_array=tmp('[]')
    try:
        proc_null=run(["--graph",gp,"--from","A","--to","B","--traffic",tp_null])
        assert proc_null.returncode==2, "traffic null should be invalid"
        proc_empty=run(["--graph",gp,"--from","A","--to","B","--traffic",tp_empty])
        assert proc_empty.returncode==0, f"empty wrapper array should be valid, got {proc_empty.returncode}"
        proc_array=run(["--graph",gp,"--from","A","--to","B","--traffic",tp_array])
        assert proc_array.returncode==0, f"direct empty array [] should be valid"
    finally:
        os.unlink(gp); os.unlink(tp_null); os.unlink(tp_empty); os.unlink(tp_array)


def test_traffic_large_graph_10000_nodes_with_traffic():
    import tempfile, os, subprocess, json, time
    def tmp(c):
        f=tempfile.NamedTemporaryFile(delete=False,suffix=".json",mode="w")
        f.write(c); f.close(); return f.name
    def run(args):
        return subprocess.run(["/app/router"]+args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=25)
    nodes=[f"N{i}" for i in range(10000)]
    edges=[{"from":f"N{i}","to":f"N{i+1}","distance":1} for i in range(9999)]
    graph={"nodes":nodes,"edges":edges}
    traffic={"traffic":[{"from":f"N{i}","to":f"N{i+1}","factor":1.2} for i in range(0,9999,1000)]}
    gp=tmp(json.dumps(graph))
    tp=tmp(json.dumps(traffic))
    try:
        start=time.time()
        proc=run(["--graph",gp,"--from","N0","--to","N9999","--traffic",tp])
        elapsed=time.time()-start
        assert proc.returncode==0, f"10000 nodes with traffic rc={proc.returncode}"
        assert elapsed < 8.0, f"too slow 10000 nodes with traffic {elapsed}"
    finally:
        os.unlink(gp); os.unlink(tp)


def test_traffic_batch_5000_with_traffic():
    import tempfile, os, subprocess, json, time
    def tmp(c):
        f=tempfile.NamedTemporaryFile(delete=False,suffix=".json",mode="w")
        f.write(c); f.close(); return f.name
    def run(args):
        return subprocess.run(["/app/router"]+args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
    nodes=[f"N{i}" for i in range(300)]
    edges=[{"from":f"N{i}","to":f"N{i+1}","distance":1} for i in range(299)]
    graph={"nodes":nodes,"edges":edges}
    traffic={"traffic":[{"from":f"N{i}","to":f"N{i+1}","factor":1.1} for i in range(0,299,30)]}
    gp=tmp(json.dumps(graph))
    tp=tmp(json.dumps(traffic))
    reqs=[{"source":"N0","destination":f"N{i%300}"} for i in range(5000)]
    rp=tmp(json.dumps(reqs))
    try:
        start=time.time()
        proc=run(["--graph",gp,"--requests",rp,"--traffic",tp])
        elapsed=time.time()-start
        assert proc.returncode==0
        assert elapsed < 12.0, f"too slow 5000 batch with traffic {elapsed}"
        assert len(proc.stdout.decode().strip().splitlines())==5000
    finally:
        os.unlink(gp); os.unlink(tp); os.unlink(rp)


def test_traffic_tie_break_10_way_effective_raw_lex():
    import tempfile, os, subprocess, json
    def tmp(c):
        f=tempfile.NamedTemporaryFile(delete=False,suffix=".json",mode="w")
        f.write(c); f.close(); return f.name
    def run(args):
        return subprocess.run(["/app/router"]+args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5)
    nodes=["A"]+[chr(ord("B")+i) for i in range(10)]+["Z"]
    edges=[]
    for i in range(10):
        mid=chr(ord("B")+i)
        edges.append({"from":"A","to":mid,"distance":5})
        edges.append({"from":mid,"to":"Z","distance":5})
    graph={"nodes":nodes,"edges":edges}
    traffic={"traffic":[{"from":"A","to":chr(ord("B")+i),"factor":1} for i in range(10)]}
    gp=tmp(json.dumps(graph))
    tp=tmp(json.dumps(traffic))
    try:
        proc=run(["--graph",gp,"--from","A","--to","Z","--traffic",tp])
        assert proc.returncode==0
        out=json.loads(proc.stdout.decode().strip())
        assert out["path"]==["A","B","Z"], f"10-way effective tie should pick B, got {out['path']}"
    finally:
        os.unlink(gp); os.unlink(tp)


def test_traffic_tie_break_secondary_raw_with_delay():
    import tempfile, os, subprocess, json, math
    def tmp(c):
        f=tempfile.NamedTemporaryFile(delete=False,suffix=".json",mode="w")
        f.write(c); f.close(); return f.name
    def run(args):
        return subprocess.run(["/app/router"]+args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5)
    graph={"nodes":["A","B","C","D"],"edges":[
        {"from":"A","to":"B","distance":10},{"from":"B","to":"D","distance":1},
        {"from":"A","to":"C","distance":2},{"from":"C","to":"D","distance":2}]}
    traffic={"traffic":[
        {"from":"A","to":"B","factor":1,"delay":0},
        {"from":"B","to":"D","factor":1,"delay":1},
        {"from":"A","to":"C","factor":2,"delay":0},
        {"from":"C","to":"D","factor":4,"delay":0}]}
    # A-B-D eff12 raw11, A-C-D eff12 raw4 -> raw smaller wins A-C-D
    gp=tmp(json.dumps(graph)); tp=tmp(json.dumps(traffic))
    try:
        proc=run(["--graph",gp,"--from","A","--to","D","--traffic",tp])
        assert proc.returncode==0
        out=json.loads(proc.stdout.decode().strip())
        assert out["path"]==["A","C","D"], f"secondary raw tie should pick raw 4, got {out['path']}"
    finally:
        os.unlink(gp); os.unlink(tp)


def test_traffic_batch_order_preserved_with_traffic_large():
    import tempfile, os, subprocess, json, random
    def tmp(c):
        f=tempfile.NamedTemporaryFile(delete=False,suffix=".json",mode="w")
        f.write(c); f.close(); return f.name
    def run(args):
        return subprocess.run(["/app/router"]+args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)
    nodes=["A","B","C","D","E"]
    edges=[{"from":"A","to":"B","distance":1},{"from":"B","to":"C","distance":1},{"from":"C","to":"D","distance":1},{"from":"D","to":"E","distance":1}]
    graph={"nodes":nodes,"edges":edges}
    traffic={"traffic":[{"from":"A","to":"B","factor":1.5}]}
    gp=tmp(json.dumps(graph)); tp=tmp(json.dumps(traffic))
    random.seed(1)
    reqs=[]
    for _ in range(200):
        s=random.choice(nodes); d=random.choice(nodes)
        reqs.append({"source":s,"destination":d})
    rp=tmp(json.dumps(reqs))
    try:
        proc=run(["--graph",gp,"--requests",rp,"--traffic",tp])
        assert proc.returncode in (0,1)
        lines=proc.stdout.decode().strip().splitlines()
        assert len(lines)==200
        for i,line in enumerate(lines):
            o=json.loads(line)
            assert o["source"]==reqs[i]["source"] and o["destination"]==reqs[i]["destination"]
    finally:
        os.unlink(gp); os.unlink(tp); os.unlink(rp)


def test_traffic_no_path_fields_minus_one_strict():
    import tempfile, os, subprocess, json
    def tmp(c):
        f=tempfile.NamedTemporaryFile(delete=False,suffix=".json",mode="w")
        f.write(c); f.close(); return f.name
    def run(args):
        return subprocess.run(["/app/router"]+args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5)
    graph={"nodes":["A","B","C"],"edges":[{"from":"A","to":"B","distance":1}]}
    traffic={"traffic":[{"from":"A","to":"B","factor":1}]}
    gp=tmp(json.dumps(graph)); tp=tmp(json.dumps(traffic))
    try:
        proc=run(["--graph",gp,"--from","A","--to","C","--traffic",tp])
        assert proc.returncode==1
        out=json.loads(proc.stdout.decode().strip())
        assert out["path"]==[] and out["distance"]==-1 and out["effective_distance"]==-1 and out["traffic_delay"]==-1
        # also check batch
        rp=tmp(json.dumps([{"source":"A","destination":"C"}]))
        try:
            proc2=run(["--graph",gp,"--requests",rp,"--traffic",tp])
            assert proc2.returncode==1
            out2=json.loads(proc2.stdout.decode().strip().splitlines()[0])
            assert out2["effective_distance"]==-1
        finally:
            os.unlink(rp)
    finally:
        os.unlink(gp); os.unlink(tp)


def test_traffic_same_source_amortization_stricter():
    import tempfile, os, subprocess, json, time
    def tmp(c):
        f=tempfile.NamedTemporaryFile(delete=False,suffix=".json",mode="w")
        f.write(c); f.close(); return f.name
    def run(args):
        return subprocess.run(["/app/router"]+args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15)
    nodes=[f"N{i}" for i in range(500)]
    edges=[{"from":f"N{i}","to":f"N{i+1}","distance":1} for i in range(499)]
    graph={"nodes":nodes,"edges":edges}
    traffic={"traffic":[{"from":f"N{i}","to":f"N{i+1}","factor":1.2,"delay":0} for i in range(0,499,50)]}
    gp=tmp(json.dumps(graph)); tp=tmp(json.dumps(traffic))
    same_reqs=[{"source":"N0","destination":f"N{i}"} for i in range(1,500)]
    multi_reqs=[{"source":f"N{i%500}","destination":f"N{(i*13)%500}"} for i in range(500)]
    rp_same=tmp(json.dumps(same_reqs)); rp_multi=tmp(json.dumps(multi_reqs))
    try:
        start=time.time()
        proc_same=run(["--graph",gp,"--requests",rp_same,"--traffic",tp])
        t_same=time.time()-start
        assert proc_same.returncode==0
        start=time.time()
        proc_multi=run(["--graph",gp,"--requests",rp_multi,"--traffic",tp])
        t_multi=time.time()-start
        assert proc_multi.returncode==0
        # Stricter: same-source <=25% multi-source (amortized)
        assert t_same <= 0.25 * t_multi + 0.5, f"same-source should be much faster: {t_same:.3f}s vs {t_multi:.3f}s"
    finally:
        os.unlink(gp); os.unlink(tp); os.unlink(rp_same); os.unlink(rp_multi)


def test_traffic_help_with_extra_flags_still_contains_traffic():
    import subprocess
    BIN="/app/router"
    proc=subprocess.run([BIN,"--help","--unknown"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5)
    assert proc.returncode==0
    out=proc.stdout.decode().lower()
    assert "traffic" in out, "help with extra flags should still contain traffic"
    for kw in ["graph","from","to","requests","help"]:
        assert kw in out


def test_traffic_flag_equals_syntax_with_traffic():
    import tempfile, os, subprocess, json, math
    def tmp(c):
        f=tempfile.NamedTemporaryFile(delete=False,suffix=".json",mode="w")
        f.write(c); f.close(); return f.name
    def run(args):
        return subprocess.run(["/app/router"]+args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5)
    graph={"nodes":["A","B"],"edges":[{"from":"A","to":"B","distance":5}]}
    gp=tmp(json.dumps(graph))
    tp=tmp(json.dumps({"traffic":[{"from":"A","to":"B","factor":2}]}))
    try:
        proc=run([f"--graph={gp}", "--from=A", "--to=B", f"--traffic={tp}"])
        assert proc.returncode==0, f"equals syntax with traffic should work rc={proc.returncode} stderr={proc.stderr.decode()}"
        out=json.loads(proc.stdout.decode().strip())
        assert math.isclose(out["effective_distance"],10, abs_tol=1e-6)
    finally:
        os.unlink(gp); os.unlink(tp)


def test_traffic_direct_array_empty_valid():
    import tempfile, os, subprocess, json
    def tmp(c):
        f=tempfile.NamedTemporaryFile(delete=False,suffix=".json",mode="w")
        f.write(c); f.close(); return f.name
    def run(args):
        return subprocess.run(["/app/router"]+args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5)
    graph={"nodes":["A","B"],"edges":[{"from":"A","to":"B","distance":1}]}
    gp=tmp(json.dumps(graph))
    tp=tmp('[]')
    try:
        proc=run(["--graph",gp,"--from","A","--to","B","--traffic",tp])
        assert proc.returncode==0
    finally:
        os.unlink(gp); os.unlink(tp)


def test_traffic_factor_scientific_notation_negative_exponent_valid():
    import tempfile, os, subprocess, json, math
    def tmp(c):
        f=tempfile.NamedTemporaryFile(delete=False,suffix=".json",mode="w")
        f.write(c); f.close(); return f.name
    def run(args):
        return subprocess.run(["/app/router"]+args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5)
    graph={"nodes":["A","B"],"edges":[{"from":"A","to":"B","distance":100}]}
    gp=tmp(json.dumps(graph))
    tp=tmp('{"traffic":[{"from":"A","to":"B","factor":1e-2}]}')
    try:
        proc=run(["--graph",gp,"--from","A","--to","B","--traffic",tp])
        assert proc.returncode==0
        out=json.loads(proc.stdout.decode().strip())
        assert math.isclose(out["effective_distance"],1, abs_tol=1e-6)
    finally:
        os.unlink(gp); os.unlink(tp)


def test_traffic_delay_scientific_notation_plus_valid():
    import tempfile, os, subprocess, json, math
    def tmp(c):
        f=tempfile.NamedTemporaryFile(delete=False,suffix=".json",mode="w")
        f.write(c); f.close(); return f.name
    def run(args):
        return subprocess.run(["/app/router"]+args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5)
    graph={"nodes":["A","B"],"edges":[{"from":"A","to":"B","distance":10}]}
    gp=tmp(json.dumps(graph))
    tp=tmp('{"traffic":[{"from":"A","to":"B","factor":1,"delay":1e+2}]}')
    try:
        proc=run(["--graph",gp,"--from","A","--to","B","--traffic",tp])
        assert proc.returncode==0, f"delay 1e+2 should be valid rc={proc.returncode}"
        out=json.loads(proc.stdout.decode().strip())
        assert math.isclose(out["effective_distance"],110, abs_tol=1e-6)
    finally:
        os.unlink(gp); os.unlink(tp)

# === GIGA HARD EXTRA additions for Turn2 - step2 too easy enhancement ===

def test_traffic_top_level_string_invalid():
    import tempfile, os, subprocess, json
    def tmp(c):
        f=tempfile.NamedTemporaryFile(delete=False,suffix=".json",mode="w"); f.write(c); f.close(); return f.name
    def run(args):
        return subprocess.run(["/app/router"]+args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5)
    graph={"nodes":["A","B"],"edges":[{"from":"A","to":"B","distance":1}]}
    gp=tmp(json.dumps(graph))
    for bad in ['"string"', '123', 'null', 'true', '{"foo":[]}', '{"traffic":null}', '{"traffic":{}}', '{"traffic":"x"}', '{"traffic":123}']:
        tp=tmp(bad)
        try:
            proc=run(["--graph",gp,"--from","A","--to","B","--traffic",tp])
            assert proc.returncode==2, f"top-level {bad} should be invalid"
            assert proc.stdout.decode().strip()==""
        finally:
            os.unlink(tp)
    os.unlink(gp)

def test_traffic_direct_array_invalid_elements_extra_hard2():
    import tempfile, os, subprocess, json
    def tmp(c):
        f=tempfile.NamedTemporaryFile(delete=False,suffix=".json",mode="w"); f.write(c); f.close(); return f.name
    def run(args):
        return subprocess.run(["/app/router"]+args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5)
    graph={"nodes":["A","B"],"edges":[{"from":"A","to":"B","distance":1}]}
    gp=tmp(json.dumps(graph))
    for bad_entry in ['null', '123', '"str"', '[1,2,3]', 'true', '{}', '{"foo":1}', '{"from":"A"}', '{"from":"A","to":"B"}']:
        tp=tmp('[{"from":"A","to":"B","factor":1},'+bad_entry+']')
        # Actually need direct array with that bad entry
        tp2=tmp('['+bad_entry+']')
        try:
            proc=run(["--graph",gp,"--from","A","--to","B","--traffic",tp2])
            assert proc.returncode==2, f"direct array element {bad_entry} should be invalid"
        finally:
            os.unlink(tp)
            os.unlink(tp2)
    os.unlink(gp)

def test_traffic_entry_missing_fields():
    import tempfile, os, subprocess, json
    def tmp(c):
        f=tempfile.NamedTemporaryFile(delete=False,suffix=".json",mode="w"); f.write(c); f.close(); return f.name
    def run(args):
        return subprocess.run(["/app/router"]+args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5)
    graph={"nodes":["A","B"],"edges":[{"from":"A","to":"B","distance":1}]}
    gp=tmp(json.dumps(graph))
    for bad in [
        '{"traffic":[{"to":"B","factor":1}]}',
        '{"traffic":[{"from":"A","factor":1}]}',
        '{"traffic":[{"from":"A","to":"B"}]}',
        '[{"to":"B","factor":1}]',
        '[{"from":"A","factor":1}]',
        '[{"from":"A","to":"B"}]',
        '{"traffic":[{"from":"A","to":"B","factor":1,"delay":1,"extra":"x"},{}]}'
    ]:
        tp=tmp(bad)
        try:
            proc=run(["--graph",gp,"--from","A","--to","B","--traffic",tp])
            assert proc.returncode==2, f"missing fields {bad} should be invalid"
        finally:
            os.unlink(tp)
    os.unlink(gp)

def test_traffic_entry_from_to_not_string():
    import tempfile, os, subprocess, json
    def tmp(c):
        f=tempfile.NamedTemporaryFile(delete=False,suffix=".json",mode="w"); f.write(c); f.close(); return f.name
    def run(args):
        return subprocess.run(["/app/router"]+args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5)
    graph={"nodes":["A","B"],"edges":[{"from":"A","to":"B","distance":1}]}
    gp=tmp(json.dumps(graph))
    for bad_from in [123, 'null', 'true', '{}', '[]', '"":"']:
        # use raw json
        for bad_entry in [f'{{"from":{bad_from},"to":"B","factor":1}}', f'{{"from":"A","to":{bad_from},"factor":1}}']:
            tp=tmp('{"traffic":['+bad_entry+']}')
            try:
                proc=run(["--graph",gp,"--from","A","--to","B","--traffic",tp])
                assert proc.returncode==2, f"from/to not string {bad_entry} should be invalid"
            finally:
                os.unlink(tp)
    os.unlink(gp)

def test_traffic_entry_whitespace_only_invalid():
    import tempfile, os, subprocess, json
    def tmp(c):
        f=tempfile.NamedTemporaryFile(delete=False,suffix=".json",mode="w"); f.write(c); f.close(); return f.name
    def run(args):
        return subprocess.run(["/app/router"]+args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5)
    graph={"nodes":["A","B"],"edges":[{"from":"A","to":"B","distance":1}]}
    gp=tmp(json.dumps(graph))
    for bad in ['   ', '', ' \t\n ']:
        tp=tmp(json.dumps({"traffic":[{"from":bad,"to":"B","factor":1}]}))
        tp2=tmp(json.dumps({"traffic":[{"from":"A","to":bad,"factor":1}]}))
        try:
            proc=run(["--graph",gp,"--from","A","--to","B","--traffic",tp])
            assert proc.returncode==2, f"whitespace from {repr(bad)} should be invalid"
            proc2=run(["--graph",gp,"--from","A","--to","B","--traffic",tp2])
            assert proc2.returncode==2
        finally:
            os.unlink(tp); os.unlink(tp2)
    os.unlink(gp)

def test_traffic_entry_leading_trailing_space_exact_invalid():
    import tempfile, os, subprocess, json
    def tmp(c):
        f=tempfile.NamedTemporaryFile(delete=False,suffix=".json",mode="w"); f.write(c); f.close(); return f.name
    def run(args):
        return subprocess.run(["/app/router"]+args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5)
    graph={"nodes":["A","B"],"edges":[{"from":"A","to":"B","distance":1}]}
    gp=tmp(json.dumps(graph))
    # leading space " A" does not exist in nodes -> invalid, not no-route
    tp=tmp(json.dumps({"traffic":[{"from":" A","to":"B","factor":1}]}))
    tp2=tmp(json.dumps({"traffic":[{"from":"A","to":" B","factor":1}]}))
    tp3=tmp(json.dumps({"traffic":[{"from":"A ","to":"B","factor":1}]}))
    try:
        for t in [tp, tp2, tp3]:
            proc=run(["--graph",gp,"--from","A","--to","B","--traffic",t])
            assert proc.returncode==2, "leading/trailing space exact should be invalid for traffic (edge not found)"
    finally:
        os.unlink(gp); os.unlink(tp); os.unlink(tp2); os.unlink(tp3)

def test_traffic_factor_various_invalid():
    import tempfile, os, subprocess, json
    def tmp(c):
        f=tempfile.NamedTemporaryFile(delete=False,suffix=".json",mode="w"); f.write(c); f.close(); return f.name
    def run(args):
        return subprocess.run(["/app/router"]+args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5)
    graph={"nodes":["A","B"],"edges":[{"from":"A","to":"B","distance":1}]}
    gp=tmp(json.dumps(graph))
    for bad_factor in ['0', '-1', '-0.5', '"2"', 'true', 'false', 'null', '{}', '[]']:
        tp=tmp('{"traffic":[{"from":"A","to":"B","factor":'+bad_factor+'}]}')
        try:
            proc=run(["--graph",gp,"--from","A","--to","B","--traffic",tp])
            assert proc.returncode==2, f"factor {bad_factor} should be invalid"
            assert proc.stdout.decode().strip()==""
        finally:
            os.unlink(tp)
    os.unlink(gp)

def test_traffic_delay_various_invalid():
    import tempfile, os, subprocess, json
    def tmp(c):
        f=tempfile.NamedTemporaryFile(delete=False,suffix=".json",mode="w"); f.write(c); f.close(); return f.name
    def run(args):
        return subprocess.run(["/app/router"]+args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5)
    graph={"nodes":["A","B"],"edges":[{"from":"A","to":"B","distance":1}]}
    gp=tmp(json.dumps(graph))
    for bad_delay in ['-1', '-0.1', '"5"', 'true', 'false', 'null', '{}', '[]']:
        tp=tmp('{"traffic":[{"from":"A","to":"B","factor":1,"delay":'+bad_delay+'}]}')
        try:
            proc=run(["--graph",gp,"--from","A","--to","B","--traffic",tp])
            assert proc.returncode==2, f"delay {bad_delay} should be invalid"
        finally:
            os.unlink(tp)
    os.unlink(gp)

def test_traffic_file_bom_trailing_comma_comment_extra():
    import tempfile, os, subprocess, json
    def tmp_bytes(b, suffix=".json"):
        f=tempfile.NamedTemporaryFile(delete=False, suffix=suffix, mode="wb")
        f.write(b)
        f.close()
        return f.name
    def tmp(c):
        f=tempfile.NamedTemporaryFile(delete=False,suffix=".json",mode="w"); f.write(c); f.close(); return f.name
    def run(args):
        return subprocess.run(["/app/router"]+args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5)
    graph={"nodes":["A","B"],"edges":[{"from":"A","to":"B","distance":1}]}
    gp=tmp(json.dumps(graph))
    # BOM
    tp_bom=tmp_bytes(b'\xef\xbb\xbf{"traffic":[{"from":"A","to":"B","factor":1}]}')
    tp_trail=tmp('{"traffic":[{"from":"A","to":"B","factor":1},]}')
    tp_comment=tmp('// comment\n{"traffic":[{"from":"A","to":"B","factor":1}]}')
    tp_direct_trail=tmp('[{"from":"A","to":"B","factor":1},]')
    tp_direct_comment=tmp('// comment\n[{"from":"A","to":"B","factor":1}]')
    try:
        for t in [tp_bom, tp_trail, tp_comment, tp_direct_trail, tp_direct_comment]:
            proc=run(["--graph",gp,"--from","A","--to","B","--traffic",t])
            assert proc.returncode==2, f"traffic malformed {t} should be invalid 2 not crash"
            assert proc.stdout.decode().strip()==""
    finally:
        os.unlink(gp); os.unlink(tp_bom); os.unlink(tp_trail); os.unlink(tp_comment); os.unlink(tp_direct_trail); os.unlink(tp_direct_comment)

def test_traffic_effective_formula_strict_per_edge():
    # Effective must be per edge raw*factor+delay sum, not (raw+delay)*factor
    # Create 2 edges A-B raw10 factor2 delay5 => correct 25, wrong (10+5)*2=30
    # B-C raw10 factor1 delay0 => correct 10, path total correct 35, wrong 40
    # If agent does wrong, effective_distance 40 vs 35, and traffic_delay 20 vs 15
    import tempfile, os, subprocess, json, math
    def tmp(c):
        f=tempfile.NamedTemporaryFile(delete=False,suffix=".json",mode="w"); f.write(c); f.close(); return f.name
    def run(args):
        return subprocess.run(["/app/router"]+args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5)
    graph={"nodes":["A","B","C"],"edges":[{"from":"A","to":"B","distance":10},{"from":"B","to":"C","distance":10}]}
    traffic={"traffic":[{"from":"A","to":"B","factor":2,"delay":5},{"from":"B","to":"C","factor":1,"delay":0}]}
    gp=tmp(json.dumps(graph)); tp=tmp(json.dumps(traffic))
    try:
        proc=run(["--graph",gp,"--from","A","--to","C","--traffic",tp])
        assert proc.returncode==0
        out=json.loads(proc.stdout.decode().strip())
        assert math.isclose(out["distance"],20, abs_tol=1e-6), f"raw should be 20 got {out['distance']}"
        assert math.isclose(out["effective_distance"],35, abs_tol=1e-6), f"effective should be 25+10=35 per-edge formula, got {out['effective_distance']} (wrong if (raw+delay)*factor)"
        assert math.isclose(out["traffic_delay"],15, abs_tol=1e-6)
    finally:
        os.unlink(gp); os.unlink(tp)

def test_traffic_raw_along_effective_best_not_raw_best():
    # Raw-shortest path A-B-D raw 2 but high factor 100 => eff 200
    # Longer A-C-D raw 20 eff 20 => should pick A-C-D and raw reported 20 not 2
    import tempfile, os, subprocess, json, math
    def tmp(c):
        f=tempfile.NamedTemporaryFile(delete=False,suffix=".json",mode="w"); f.write(c); f.close(); return f.name
    def run(args):
        return subprocess.run(["/app/router"]+args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5)
    graph={"nodes":["A","B","C","D"],"edges":[
        {"from":"A","to":"B","distance":1},{"from":"B","to":"D","distance":1},
        {"from":"A","to":"C","distance":10},{"from":"C","to":"D","distance":10}]}
    traffic={"traffic":[
        {"from":"A","to":"B","factor":100},{"from":"B","to":"D","factor":100},
        {"from":"A","to":"C","factor":1},{"from":"C","to":"D","factor":1}]}
    gp=tmp(json.dumps(graph)); tp=tmp(json.dumps(traffic))
    try:
        proc=run(["--graph",gp,"--from","A","--to","D","--traffic",tp])
        assert proc.returncode==0
        out=json.loads(proc.stdout.decode().strip())
        assert out["path"]==["A","C","D"], f"should reroute to C got {out['path']}"
        assert math.isclose(out["distance"],20, abs_tol=1e-6), f"raw should be along effective-best 20, got {out['distance']}"
        assert math.isclose(out["effective_distance"],20, abs_tol=1e-6)
        assert math.isclose(out["traffic_delay"],0, abs_tol=1e-6)
    finally:
        os.unlink(gp); os.unlink(tp)

def test_traffic_3_level_tie_effective_raw_lex():
    # Effective tie 12 for both paths, raw differs 11 vs 4 -> pick raw smaller A-C-D
    # If raw equal too, lex smallest B
    import tempfile, os, subprocess, json
    def tmp(c):
        f=tempfile.NamedTemporaryFile(delete=False,suffix=".json",mode="w"); f.write(c); f.close(); return f.name
    def run(args):
        return subprocess.run(["/app/router"]+args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5)
    graph={"nodes":["A","B","C","D"],"edges":[
        {"from":"A","to":"B","distance":10},{"from":"B","to":"D","distance":1},
        {"from":"A","to":"C","distance":2},{"from":"C","to":"D","distance":2}]}
    # effective: A-B-D 10+1+1(delay)=12 raw11; A-C-D 2*2 +2*4=4+8=12 raw4
    traffic={"traffic":[
        {"from":"A","to":"B","factor":1,"delay":0},
        {"from":"B","to":"D","factor":1,"delay":1},
        {"from":"A","to":"C","factor":2,"delay":0},
        {"from":"C","to":"D","factor":4,"delay":0}]}
    gp=tmp(json.dumps(graph)); tp=tmp(json.dumps(traffic))
    try:
        proc=run(["--graph",gp,"--from","A","--to","D","--traffic",tp])
        assert proc.returncode==0
        out=json.loads(proc.stdout.decode().strip())
        assert out["path"]==["A","C","D"], f"secondary raw tie: expected A-C-D raw4 vs A-B-D raw11, got {out['path']}"
    finally:
        os.unlink(gp); os.unlink(tp)

def test_traffic_lex_deeper_diamond_with_traffic():
    # Diamond of diamonds effective equal, decision at depth2: paths A-B1-B2-Z, A-B1-C2-Z, A-C1-B2-Z, A-C1-C2-Z all cost 15 effective
    # Should pick A-B1-B2-Z because B1<C1 and B2/C2 secondary
    import tempfile, os, subprocess, json
    def tmp(c):
        f=tempfile.NamedTemporaryFile(delete=False,suffix=".json",mode="w"); f.write(c); f.close(); return f.name
    def run(args):
        return subprocess.run(["/app/router"]+args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5)
    graph={"nodes":["A","B1","C1","B2","C2","Z"],"edges":[
        {"from":"A","to":"B1","distance":5},{"from":"A","to":"C1","distance":5},
        {"from":"B1","to":"B2","distance":5},{"from":"B1","to":"C2","distance":5},
        {"from":"C1","to":"B2","distance":5},{"from":"C1","to":"C2","distance":5},
        {"from":"B2","to":"Z","distance":5},{"from":"C2","to":"Z","distance":5}]}
    traffic={"traffic":[]}
    gp=tmp(json.dumps(graph)); tp=tmp(json.dumps(traffic))
    try:
        proc=run(["--graph",gp,"--from","A","--to","Z","--traffic",tp])
        assert proc.returncode==0
        out=json.loads(proc.stdout.decode().strip())
        assert out["path"]==["A","B1","B2","Z"], f"deeper diamond with traffic should pick A-B1-B2-Z, got {out['path']}"
    finally:
        os.unlink(gp); os.unlink(tp)

def test_traffic_case_sensitive_ascii_with_traffic():
    import tempfile, os, subprocess, json
    def tmp(c):
        f=tempfile.NamedTemporaryFile(delete=False,suffix=".json",mode="w"); f.write(c); f.close(); return f.name
    def run(args):
        return subprocess.run(["/app/router"]+args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5)
    # 'A' 65 < 'a' 97, '-'45 < '.'46 < '_'95
    graph={"nodes":["A","A-B","A.B","A_B","a","Z"],"edges":[
        {"from":"A","to":"A-B","distance":5},{"from":"A-B","to":"Z","distance":5},
        {"from":"A","to":"A.B","distance":5},{"from":"A.B","to":"Z","distance":5},
        {"from":"A","to":"A_B","distance":5},{"from":"A_B","to":"Z","distance":5},
        {"from":"A","to":"a","distance":5},{"from":"a","to":"Z","distance":5}]}
    traffic={"traffic":[{"from":"A","to":"A-B","factor":1},{"from":"A","to":"A.B","factor":1},{"from":"A","to":"A_B","factor":1},{"from":"A","to":"a","factor":1}]}
    gp=tmp(json.dumps(graph)); tp=tmp(json.dumps(traffic))
    try:
        proc=run(["--graph",gp,"--from","A","--to","Z","--traffic",tp])
        assert proc.returncode==0
        out=json.loads(proc.stdout.decode().strip())
        # ASCII: '-'45 < '.'46 < '_'95 and 'A' < 'a', so A-B < A.B < A_B? Actually '-' < '.' < '_' so A-B first
        assert out["path"]==["A","A-B","Z"], f"case-sensitive ascii with traffic should pick A-B, got {out['path']}"
    finally:
        os.unlink(gp); os.unlink(tp)

def test_traffic_duplicate_reverse_with_delay_reset():
    import tempfile, os, subprocess, json, math
    def tmp(c):
        f=tempfile.NamedTemporaryFile(delete=False,suffix=".json",mode="w"); f.write(c); f.close(); return f.name
    def run(args):
        return subprocess.run(["/app/router"]+args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5)
    graph={"nodes":["A","B"],"edges":[{"from":"A","to":"B","distance":10}]}
    gp=tmp(json.dumps(graph))
    # First A-B factor1 delay100, second B-A factor2 delay0 (reverse, no delay) -> last wins factor2 delay0 => eff20
    traffic={"traffic":[{"from":"A","to":"B","factor":1,"delay":100},{"from":"B","to":"A","factor":2}]}
    tp=tmp(json.dumps(traffic))
    try:
        proc=run(["--graph",gp,"--from","A","--to","B","--traffic",tp])
        assert proc.returncode==0
        out=json.loads(proc.stdout.decode().strip())
        assert math.isclose(out["effective_distance"],20, abs_tol=1e-6), f"reverse duplicate delay reset expected 20 got {out['effective_distance']}"
    finally:
        os.unlink(gp); os.unlink(tp)

def test_traffic_output_fields_strict():
    import tempfile, os, subprocess, json
    def tmp(c):
        f=tempfile.NamedTemporaryFile(delete=False,suffix=".json",mode="w"); f.write(c); f.close(); return f.name
    def run(args):
        return subprocess.run(["/app/router"]+args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5)
    graph={"nodes":["A","B"],"edges":[{"from":"A","to":"B","distance":5}]}
    gp=tmp(json.dumps(graph))
    tp=tmp(json.dumps({"traffic":[{"from":"A","to":"B","factor":2}]}))
    try:
        proc=run(["--graph",gp,"--from","A","--to","B","--traffic",tp])
        assert proc.returncode==0
        out=json.loads(proc.stdout.decode().strip())
        assert set(out.keys())=={"path","distance","effective_distance","traffic_delay"}, f"single with traffic must have exactly 4 keys, got {out.keys()}"
        assert isinstance(out["path"], list) and isinstance(out["distance"], (int,float))
        # batch
        rp=tmp(json.dumps([{"source":"A","destination":"B"}]))
        proc2=run(["--graph",gp,"--requests",rp,"--traffic",tp])
        assert proc2.returncode==0
        out2=json.loads(proc2.stdout.decode().strip().splitlines()[0])
        assert set(out2.keys())=={"source","destination","path","distance","effective_distance","traffic_delay"}, f"batch with traffic 6 keys, got {out2.keys()}"
        # no-route
        proc3=run(["--graph",gp,"--from","A","--to","C","--traffic",tp])
        assert proc3.returncode==1
        out3=json.loads(proc3.stdout.decode().strip())
        assert out3["distance"]==-1 and out3["effective_distance"]==-1 and out3["traffic_delay"]==-1
        assert set(out3.keys())=={"path","distance","effective_distance","traffic_delay"}
        os.unlink(rp)
    finally:
        os.unlink(gp); os.unlink(tp)

def test_traffic_flag_order_independence():
    import tempfile, os, subprocess, json, math
    def tmp(c):
        f=tempfile.NamedTemporaryFile(delete=False,suffix=".json",mode="w"); f.write(c); f.close(); return f.name
    def run(args):
        return subprocess.run(["/app/router"]+args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5)
    graph={"nodes":["A","B"],"edges":[{"from":"A","to":"B","distance":5}]}
    gp=tmp(json.dumps(graph))
    tp=tmp(json.dumps({"traffic":[{"from":"A","to":"B","factor":2}]}))
    try:
        orders=[
            ["--graph",gp,"--from","A","--to","B","--traffic",tp],
            ["--traffic",tp,"--graph",gp,"--from","A","--to","B"],
            ["--from","A","--graph",gp,"--traffic",tp,"--to","B"],
            [f"--graph={gp}",f"--from=A",f"--to=B",f"--traffic={tp}"],
            [f"--traffic={tp}",f"--graph={gp}","--from","A","--to","B"]
        ]
        for args in orders:
            proc=run(args)
            assert proc.returncode==0, f"flag order {args} should work rc={proc.returncode} stderr={proc.stderr.decode()}"
            out=json.loads(proc.stdout.decode().strip())
            assert math.isclose(out["effective_distance"],10, abs_tol=1e-6)
    finally:
        os.unlink(gp); os.unlink(tp)

def test_traffic_help_precedence_with_traffic():
    import subprocess
    BIN="/app/router"
    tests=[
        [BIN,"--help","--unknown"],
        [BIN,"--help","--traffic","dummy"],
        [BIN,"--traffic","dummy","--help"],
        [BIN,"help"],
        [BIN],
        [BIN,"--help=true"],
        [BIN,"-h"],
        [BIN,"--help","--graph","nonexistent","--traffic","dummy","--unknown"]
    ]
    for args in tests:
        proc=subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5)
        assert proc.returncode==0, f"help precedence {args} should be 0 got {proc.returncode}"
        out=proc.stdout.decode().lower()
        assert "traffic" in out, f"help should contain traffic for {args}"
        assert "graph" in out

def test_traffic_single_empty_from_invalid_vs_batch_no_route():
    import tempfile, os, subprocess, json
    def tmp(c):
        f=tempfile.NamedTemporaryFile(delete=False,suffix=".json",mode="w"); f.write(c); f.close(); return f.name
    def run(args):
        return subprocess.run(["/app/router"]+args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5)
    graph={"nodes":["A","B"],"edges":[{"from":"A","to":"B","distance":1}]}
    gp=tmp(json.dumps(graph))
    tp=tmp(json.dumps({"traffic":[{"from":"A","to":"B","factor":1}]}))
    try:
        # single empty from invalid exit2
        proc=run(["--graph",gp,"--from","","--to","B","--traffic",tp])
        assert proc.returncode==2 and proc.stdout.decode().strip()=="", "single empty from with traffic should be invalid exit2"
        proc2=run(["--graph",gp,"--from","   ","--to","B","--traffic",tp])
        assert proc2.returncode==2
        # batch empty source no-route exit1 with -1 for all fields
        rp=tmp(json.dumps([{"source":"","destination":"B"}]))
        proc3=run(["--graph",gp,"--requests",rp,"--traffic",tp])
        assert proc3.returncode==1, "batch empty source with traffic should be no-route exit1"
        out=json.loads(proc3.stdout.decode().strip().splitlines()[0])
        assert out["distance"]==-1 and out["effective_distance"]==-1 and out["traffic_delay"]==-1
        os.unlink(rp)
        # batch whitespace
        rp2=tmp(json.dumps([{"source":"   ","destination":"B"}]))
        proc4=run(["--graph",gp,"--requests",rp2,"--traffic",tp])
        assert proc4.returncode==1
        out2=json.loads(proc4.stdout.decode().strip().splitlines()[0])
        assert out2["effective_distance"]==-1
        os.unlink(rp2)
    finally:
        os.unlink(gp); os.unlink(tp)

def test_traffic_non_existing_node_no_route_with_traffic():
    import tempfile, os, subprocess, json
    def tmp(c):
        f=tempfile.NamedTemporaryFile(delete=False,suffix=".json",mode="w"); f.write(c); f.close(); return f.name
    def run(args):
        return subprocess.run(["/app/router"]+args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5)
    graph={"nodes":["A","B"],"edges":[{"from":"A","to":"B","distance":1}]}
    gp=tmp(json.dumps(graph))
    tp=tmp(json.dumps({"traffic":[{"from":"A","to":"B","factor":1}]}))
    try:
        proc=run(["--graph",gp,"--from","A","--to","NonExist","--traffic",tp])
        assert proc.returncode==1
        out=json.loads(proc.stdout.decode().strip())
        assert out["path"]==[] and out["distance"]==-1 and out["effective_distance"]==-1
        proc2=run(["--graph",gp,"--from","A","--to"," A","--traffic",tp])  # leading space no-route
        assert proc2.returncode==1
        out2=json.loads(proc2.stdout.decode().strip())
        assert out2["effective_distance"]==-1
    finally:
        os.unlink(gp); os.unlink(tp)

def test_traffic_requests_validation_with_traffic():
    import tempfile, os, subprocess, json
    def tmp(c):
        f=tempfile.NamedTemporaryFile(delete=False,suffix=".json",mode="w"); f.write(c); f.close(); return f.name
    def run(args):
        return subprocess.run(["/app/router"]+args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5)
    graph={"nodes":["A","B"],"edges":[{"from":"A","to":"B","distance":1}]}
    gp=tmp(json.dumps(graph))
    tp=tmp(json.dumps({"traffic":[{"from":"A","to":"B","factor":1}]}))
    invalid_requests=[
        '[null]',
        '[123]',
        '["abc"]',
        '[{"source":null,"destination":"B"}]',
        '[{"source":123,"destination":"B"}]',
        '[{}]',
        '[{"source":"A"}]',
        '[{"destination":"B"}]',
        '{"source":"A","destination":"B"}',
        '[{"source":"A","destination":"B"},]',
        '[{"from":"A","to":"B","extra":1}, null]'
    ]
    try:
        for bad in invalid_requests:
            rp=tmp(bad)
            try:
                proc=run(["--graph",gp,"--requests",rp,"--traffic",tp])
                assert proc.returncode==2, f"requests {bad} with traffic should be invalid exit2, got {proc.returncode}"
                assert proc.stdout.decode().strip()==""
            finally:
                os.unlink(rp)
    finally:
        os.unlink(gp); os.unlink(tp)

def test_traffic_large_graph_2000_nodes_with_traffic():
    import tempfile, os, subprocess, json, time
    def tmp(c):
        f=tempfile.NamedTemporaryFile(delete=False,suffix=".json",mode="w"); f.write(c); f.close(); return f.name
    def run(args):
        return subprocess.run(["/app/router"]+args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20)
    nodes=[f"N{i}" for i in range(2000)]
    edges=[{"from":f"N{i}","to":f"N{i+1}","distance":1} for i in range(1999)]
    edges+= [{"from":f"N{i}","to":f"N{i+100}","distance":50} for i in range(0,1900,100)]
    graph={"nodes":nodes,"edges":edges}
    traffic={"traffic":[{"from":f"N{i}","to":f"N{i+1}","factor":1.5} for i in range(0,1999,200)]}
    gp=tmp(json.dumps(graph)); tp=tmp(json.dumps(traffic))
    try:
        start=time.time()
        proc=run(["--graph",gp,"--from","N0","--to","N1999","--traffic",tp])
        elapsed=time.time()-start
        assert proc.returncode==0, f"2000 nodes with traffic rc={proc.returncode}"
        assert elapsed<5.0, f"too slow 2000 nodes with traffic {elapsed}"
    finally:
        os.unlink(gp); os.unlink(tp)

def test_traffic_batch_1000_float_distances_with_traffic():
    import tempfile, os, subprocess, json, time, math
    def tmp(c):
        f=tempfile.NamedTemporaryFile(delete=False,suffix=".json",mode="w"); f.write(c); f.close(); return f.name
    def run(args):
        return subprocess.run(["/app/router"]+args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20)
    nodes=[f"N{i}" for i in range(100)]
    edges=[{"from":f"N{i}","to":f"N{i+1}","distance":0.5} for i in range(99)]
    graph={"nodes":nodes,"edges":edges}
    traffic={"traffic":[{"from":f"N{i}","to":f"N{i+1}","factor":1.1,"delay":0.2} for i in range(0,99,10)]}
    gp=tmp(json.dumps(graph)); tp=tmp(json.dumps(traffic))
    reqs=[{"source":"N0","destination":f"N{i%100}"} for i in range(1000)]
    rp=tmp(json.dumps(reqs))
    try:
        start=time.time()
        proc=run(["--graph",gp,"--requests",rp,"--traffic",tp])
        elapsed=time.time()-start
        assert proc.returncode==0, proc.stderr.decode()[:500]
        assert elapsed<6.0, f"too slow 1000 float batch with traffic {elapsed}"
        lines=proc.stdout.decode().strip().splitlines()
        assert len(lines)==1000
        # check first has effective
        o=json.loads(lines[0])
        assert "effective_distance" in o
    finally:
        os.unlink(gp); os.unlink(tp); os.unlink(rp)

def test_traffic_requests_trailing_comma_invalid_with_traffic():
    import tempfile, os, subprocess, json
    def tmp(c):
        f=tempfile.NamedTemporaryFile(delete=False,suffix=".json",mode="w"); f.write(c); f.close(); return f.name
    def run(args):
        return subprocess.run(["/app/router"]+args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5)
    graph={"nodes":["A","B"],"edges":[{"from":"A","to":"B","distance":1}]}
    gp=tmp(json.dumps(graph))
    tp=tmp(json.dumps({"traffic":[{"from":"A","to":"B","factor":1}]}))
    rp=tmp('[{"source":"A","destination":"B"},]')
    try:
        proc=run(["--graph",gp,"--requests",rp,"--traffic",tp])
        assert proc.returncode==2, "requests trailing comma with traffic should be invalid"
        assert proc.stdout.decode().strip()==""
    finally:
        os.unlink(gp); os.unlink(tp); os.unlink(rp)

def test_traffic_graph_duplicate_min_plus_traffic():
    import tempfile, os, subprocess, json, math
    def tmp(c):
        f=tempfile.NamedTemporaryFile(delete=False,suffix=".json",mode="w"); f.write(c); f.close(); return f.name
    def run(args):
        return subprocess.run(["/app/router"]+args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5)
    # graph duplicate edges 10 and 3, min is 3, traffic factor 2 => effective 6
    graph={"nodes":["A","B"],"edges":[{"from":"A","to":"B","distance":10},{"from":"A","to":"B","distance":3}]}
    gp=tmp(json.dumps(graph))
    tp=tmp(json.dumps({"traffic":[{"from":"A","to":"B","factor":2}]}))
    try:
        proc=run(["--graph",gp,"--from","A","--to","B","--traffic",tp])
        assert proc.returncode==0
        out=json.loads(proc.stdout.decode().strip())
        assert math.isclose(out["distance"],3, abs_tol=1e-6), f"duplicate min raw 3, got {out['distance']}"
        assert math.isclose(out["effective_distance"],6, abs_tol=1e-6)
    finally:
        os.unlink(gp); os.unlink(tp)

def test_traffic_special_chars_node_ids_with_traffic():
    import tempfile, os, subprocess, json, math
    def tmp(c):
        f=tempfile.NamedTemporaryFile(delete=False,suffix=".json",mode="w"); f.write(c); f.close(); return f.name
    def run(args):
        return subprocess.run(["/app/router"]+args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5)
    graph={"nodes":["A/B","C.D","E-F_G"],"edges":[
        {"from":"A/B","to":"C.D","distance":5},{"from":"C.D","to":"E-F_G","distance":5}]}
    traffic={"traffic":[{"from":"A/B","to":"C.D","factor":2},{"from":"C.D","to":"E-F_G","factor":0.5}]}
    gp=tmp(json.dumps(graph)); tp=tmp(json.dumps(traffic))
    try:
        proc=run(["--graph",gp,"--from","A/B","--to","E-F_G","--traffic",tp])
        assert proc.returncode==0
        out=json.loads(proc.stdout.decode().strip())
        assert out["path"]==["A/B","C.D","E-F_G"]
        assert math.isclose(out["distance"],10, abs_tol=1e-6)
        assert math.isclose(out["effective_distance"],5*2+5*0.5, abs_tol=1e-6)
    finally:
        os.unlink(gp); os.unlink(tp)

def test_traffic_very_small_and_large_factor_same_path():
    import tempfile, os, subprocess, json, math
    def tmp(c):
        f=tempfile.NamedTemporaryFile(delete=False,suffix=".json",mode="w"); f.write(c); f.close(); return f.name
    def run(args):
        return subprocess.run(["/app/router"]+args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5)
    graph={"nodes":["A","B","C"],"edges":[{"from":"A","to":"B","distance":1000},{"from":"B","to":"C","distance":1000}]}
    traffic={"traffic":[{"from":"A","to":"B","factor":1e-9},{"from":"B","to":"C","factor":1e6}]}
    gp=tmp(json.dumps(graph)); tp=tmp(json.dumps(traffic))
    try:
        proc=run(["--graph",gp,"--from","A","--to","C","--traffic",tp])
        assert proc.returncode==0
        out=json.loads(proc.stdout.decode().strip())
        # eff = 1000*1e-9 + 1000*1e6 = 1e-6 + 1e9 ~ 1e9
        assert math.isclose(out["effective_distance"],1000*1e-9+1000*1e6, rel_tol=1e-6)
    finally:
        os.unlink(gp); os.unlink(tp)

def test_traffic_delay_accumulation_and_negative_delay():
    import tempfile, os, subprocess, json, math
    def tmp(c):
        f=tempfile.NamedTemporaryFile(delete=False,suffix=".json",mode="w"); f.write(c); f.close(); return f.name
    def run(args):
        return subprocess.run(["/app/router"]+args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5)
    graph={"nodes":["A","B","C"],"edges":[{"from":"A","to":"B","distance":10},{"from":"B","to":"C","distance":10}]}
    traffic={"traffic":[{"from":"A","to":"B","factor":0.5,"delay":0},{"from":"B","to":"C","factor":1,"delay":5}]}
    gp=tmp(json.dumps(graph)); tp=tmp(json.dumps(traffic))
    try:
        proc=run(["--graph",gp,"--from","A","--to","C","--traffic",tp])
        assert proc.returncode==0
        out=json.loads(proc.stdout.decode().strip())
        # eff A-B 5 + B-C 15 =20 raw20 delay0? actually raw20 eff20 delay0? Let's compute: A-B raw10*0.5=5 eff5, B-C 10*1+5=15 eff15 total eff20 raw20 delay0
        assert math.isclose(out["effective_distance"],20, abs_tol=1e-6)
        assert math.isclose(out["traffic_delay"],0, abs_tol=1e-6)
        # negative delay case factor<1
        tp2=tmp(json.dumps({"traffic":[{"from":"A","to":"B","factor":0.5}]}))
        proc2=run(["--graph",gp,"--from","A","--to","B","--traffic",tp2])
        assert proc2.returncode==0
        out2=json.loads(proc2.stdout.decode().strip())
        assert math.isclose(out2["traffic_delay"],-5, abs_tol=1e-6), "factor<1 should give negative traffic_delay"
        os.unlink(tp2)
    finally:
        os.unlink(gp); os.unlink(tp)

def test_traffic_10_way_tie_with_traffic_real():
    import tempfile, os, subprocess, json
    def tmp(c):
        f=tempfile.NamedTemporaryFile(delete=False,suffix=".json",mode="w"); f.write(c); f.close(); return f.name
    def run(args):
        return subprocess.run(["/app/router"]+args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5)
    nodes=["A"]+[chr(ord("B")+i) for i in range(10)]+["Z"]
    edges=[]
    for i in range(10):
        mid=chr(ord("B")+i)
        edges.append({"from":"A","to":mid,"distance":5})
        edges.append({"from":mid,"to":"Z","distance":5})
    graph={"nodes":nodes,"edges":edges}
    traffic={"traffic":[{"from":"A","to":chr(ord("B")+i),"factor":1,"delay":i*0.0} for i in range(10)]}
    gp=tmp(json.dumps(graph)); tp=tmp(json.dumps(traffic))
    try:
        proc=run(["--graph",gp,"--from","A","--to","Z","--traffic",tp])
        assert proc.returncode==0
        out=json.loads(proc.stdout.decode().strip())
        assert out["path"]==["A","B","Z"], f"10-way tie with traffic should pick B, got {out['path']}"
    finally:
        os.unlink(gp); os.unlink(tp)

def test_traffic_batch_with_empty_dest_no_route_with_traffic():
    import tempfile, os, subprocess, json
    def tmp(c):
        f=tempfile.NamedTemporaryFile(delete=False,suffix=".json",mode="w"); f.write(c); f.close(); return f.name
    def run(args):
        return subprocess.run(["/app/router"]+args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5)
    graph={"nodes":["A","B"],"edges":[{"from":"A","to":"B","distance":1}]}
    gp=tmp(json.dumps(graph))
    tp=tmp(json.dumps({"traffic":[{"from":"A","to":"B","factor":2}]}))
    rp=tmp(json.dumps([{"source":"A","destination":""}]))
    try:
        proc=run(["--graph",gp,"--requests",rp,"--traffic",tp])
        assert proc.returncode==1
        out=json.loads(proc.stdout.decode().strip().splitlines()[0])
        assert out["path"]==[] and out["distance"]==-1 and out["effective_distance"]==-1
    finally:
        os.unlink(gp); os.unlink(tp); os.unlink(rp)

def test_traffic_graph_nodes_contain_non_string_with_traffic():
    import tempfile, os, subprocess, json
    def tmp(c):
        f=tempfile.NamedTemporaryFile(delete=False,suffix=".json",mode="w"); f.write(c); f.close(); return f.name
    def run(args):
        return subprocess.run(["/app/router"]+args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5)
    # nodes contain non-string even with traffic should be invalid exit2
    graph_invalid='{"nodes":["A",123,"B"],"edges":[{"from":"A","to":"B","distance":1}]}'
    gp=tmp(graph_invalid)
    tp=tmp(json.dumps({"traffic":[{"from":"A","to":"B","factor":1}]}))
    try:
        proc=run(["--graph",gp,"--from","A","--to","B","--traffic",tp])
        assert proc.returncode==2 and proc.stdout.decode().strip()==""
    finally:
        os.unlink(gp); os.unlink(tp)

def test_traffic_graph_edges_contain_non_object_with_traffic():
    import tempfile, os, subprocess, json
    def tmp(c):
        f=tempfile.NamedTemporaryFile(delete=False,suffix=".json",mode="w"); f.write(c); f.close(); return f.name
    def run(args):
        return subprocess.run(["/app/router"]+args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5)
    graph_invalid='{"nodes":["A","B"],"edges":[123]}'
    gp=tmp(graph_invalid)
    tp=tmp(json.dumps({"traffic":[]}))
    try:
        proc=run(["--graph",gp,"--from","A","--to","B","--traffic",tp])
        assert proc.returncode==2
    finally:
        os.unlink(gp); os.unlink(tp)

def test_traffic_factor_scientific_plus_valid_detailed():
    import tempfile, os, subprocess, json, math
    def tmp(c):
        f=tempfile.NamedTemporaryFile(delete=False,suffix=".json",mode="w"); f.write(c); f.close(); return f.name
    def run(args):
        return subprocess.run(["/app/router"]+args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5)
    graph={"nodes":["A","B"],"edges":[{"from":"A","to":"B","distance":10}]}
    gp=tmp(json.dumps(graph))
    for factor_str in ["1e+2", "1E+3", "1e+3", "1E+2", "2.5e+2"]:
        # write raw json with that factor literal to test parser accepts plus
        tp=tmp('{"traffic":[{"from":"A","to":"B","factor":'+factor_str+'}]}')
        try:
            proc=run(["--graph",gp,"--from","A","--to","B","--traffic",tp])
            assert proc.returncode==0, f"factor {factor_str} should be valid, rc={proc.returncode} stderr={proc.stderr.decode()}"
            out=json.loads(proc.stdout.decode().strip())
            # compute expected
            expected=10*float(factor_str)
            assert math.isclose(out["effective_distance"],expected, rel_tol=1e-6), f"factor {factor_str} expected {expected}"
        finally:
            os.unlink(tp)
    os.unlink(gp)

def test_traffic_batch_2000_with_traffic_relative_extra_hard():
    import tempfile, os, subprocess, json, time
    def tmp(c):
        f=tempfile.NamedTemporaryFile(delete=False,suffix=".json",mode="w"); f.write(c); f.close(); return f.name
    def run(args):
        return subprocess.run(["/app/router"]+args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20)
    nodes=[f"N{i}" for i in range(200)]
    edges=[{"from":f"N{i}","to":f"N{i+1}","distance":1} for i in range(199)]
    graph={"nodes":nodes,"edges":edges}
    traffic={"traffic":[{"from":f"N{i}","to":f"N{i+1}","factor":1.2} for i in range(0,199,20)]}
    gp=tmp(json.dumps(graph)); tp=tmp(json.dumps(traffic))
    rp1=tmp(json.dumps([{"source":"N0","destination":"N199"}]))
    rp2000=tmp(json.dumps([{"source":"N0","destination":f"N{i%200}"} for i in range(2000)]))
    try:
        start=time.time()
        base=run(["--graph",gp,"--requests",rp1,"--traffic",tp])
        t_base=time.time()-start
        assert base.returncode==0
        start=time.time()
        proc=run(["--graph",gp,"--requests",rp2000,"--traffic",tp])
        t_2000=time.time()-start
        assert proc.returncode==0
        assert t_2000 <= 25*t_base+1.0, f"2000 batch relative too slow {t_2000:.3f} vs base {t_base:.3f}"
        assert len(proc.stdout.decode().strip().splitlines())==2000
    finally:
        os.unlink(gp); os.unlink(tp); os.unlink(rp1); os.unlink(rp2000)

def test_traffic_same_source_amortization_with_traffic_500():
    import tempfile, os, subprocess, json, time
    def tmp(c):
        f=tempfile.NamedTemporaryFile(delete=False,suffix=".json",mode="w"); f.write(c); f.close(); return f.name
    def run(args):
        return subprocess.run(["/app/router"]+args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20)
    nodes=[f"N{i}" for i in range(200)]
    edges=[{"from":f"N{i}","to":f"N{i+1}","distance":1} for i in range(199)]
    graph={"nodes":nodes,"edges":edges}
    traffic={"traffic":[{"from":f"N{i}","to":f"N{i+1}","factor":1.1} for i in range(0,199,10)]}
    gp=tmp(json.dumps(graph)); tp=tmp(json.dumps(traffic))
    same_reqs=[{"source":"N0","destination":f"N{(i%199)+1}"} for i in range(500)]
    multi_reqs=[{"source":f"N{i%200}","destination":f"N{(i*7)%200}"} for i in range(500)]
    rp_same=tmp(json.dumps(same_reqs)); rp_multi=tmp(json.dumps(multi_reqs))
    try:
        start=time.time()
        proc_same=run(["--graph",gp,"--requests",rp_same,"--traffic",tp])
        t_same=time.time()-start
        assert proc_same.returncode==0
        start=time.time()
        proc_multi=run(["--graph",gp,"--requests",rp_multi,"--traffic",tp])
        t_multi=time.time()-start
        assert proc_multi.returncode==0
        assert t_same <= 0.25*t_multi+1.0, f"same-source 500 should be <=25% multi: {t_same:.3f} vs {t_multi:.3f}"
    finally:
        os.unlink(gp); os.unlink(tp); os.unlink(rp_same); os.unlink(rp_multi)

def test_traffic_float_tolerance_effective_equal_within_1e9():
    # Two paths effective difference 1e-10 should be considered tie, then raw decides
    import tempfile, os, subprocess, json
    def tmp(c):
        f=tempfile.NamedTemporaryFile(delete=False,suffix=".json",mode="w"); f.write(c); f.close(); return f.name
    def run(args):
        return subprocess.run(["/app/router"]+args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5)
    # Path A-B-D: A-B 5 factor1, B-D 5+1e-10 factor1 => eff 10+1e-10 raw10
    # Path A-C-D: A-C 5 factor1, C-D 5 factor1 => eff10 raw10 -> tie effective within 1e-9, raw equal, lex B<C so A-B-D wins? Actually B<C so A-B-D lex smaller than A-C-D.
    # Instead create raw difference for secondary tie
    graph={"nodes":["A","B","C","D"],"edges":[
        {"from":"A","to":"B","distance":10},{"from":"B","to":"D","distance":1},
        {"from":"A","to":"C","distance":2},{"from":"C","to":"D","distance":2}]}
    traffic={"traffic":[
        {"from":"A","to":"B","factor":1,"delay":0},
        {"from":"B","to":"D","factor":1,"delay":1e-10},
        {"from":"A","to":"C","factor":2,"delay":0},
        {"from":"C","to":"D","factor":4,"delay":0}]}
    # A-B-D eff 10+1+1e-10=11.0000000001 raw11, A-C-D eff 4+8=12 raw4 not equal
    # Let's make effective equal within epsilon: need 12 vs 12.00000000001
    # A-B-D raw11 eff 12 (10+1+1), A-C-D raw4 eff 12 (4+8)
    # Use delay 1 for B-D to get eff12, and make A-C-D eff12 as before, difference 1e-10
    traffic2={"traffic":[
        {"from":"A","to":"B","factor":1,"delay":0},
        {"from":"B","to":"D","factor":1,"delay":1},
        {"from":"A","to":"C","factor":2,"delay":0},
        {"from":"C","to":"D","factor":4,"delay":0.0000000001}]}
    gp=tmp(json.dumps(graph)); tp=tmp(json.dumps(traffic2))
    try:
        proc=run(["--graph",gp,"--from","A","--to","D","--traffic",tp])
        assert proc.returncode==0
        out=json.loads(proc.stdout.decode().strip())
        # effective tie within 1e-9 (12 vs 12.0000000001) => raw smaller A-C-D raw4 wins
        assert out["path"]==["A","C","D"], f"tolerance effective tie should pick raw smaller, got {out['path']}"
    finally:
        os.unlink(gp); os.unlink(tp)

def test_traffic_missing_traffic_flag_still_raw():
    import tempfile, os, subprocess, json
    def tmp(c):
        f=tempfile.NamedTemporaryFile(delete=False,suffix=".json",mode="w"); f.write(c); f.close(); return f.name
    def run(args):
        return subprocess.run(["/app/router"]+args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5)
    graph={"nodes":["A","B"],"edges":[{"from":"A","to":"B","distance":5}]}
    gp=tmp(json.dumps(graph))
    try:
        proc=run(["--graph",gp,"--from","A","--to","B"])
        assert proc.returncode==0
        out=json.loads(proc.stdout.decode().strip())
        assert set(out.keys())=={"path","distance"}
        assert out["distance"]==5
    finally:
        os.unlink(gp)

