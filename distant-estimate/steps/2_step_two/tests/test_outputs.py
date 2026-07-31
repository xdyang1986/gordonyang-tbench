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


def test_traffic_invalid_whitespace():
    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 5}]}
    traffic = {"traffic": [{"from": "   ", "to": "B", "factor": 2}]}
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
    try:
        start = time.time()
        proc = run(["--graph", gp, "--requests", rp, "--traffic", tp])
        elapsed = time.time() - start
        assert proc.returncode == 0, proc.stderr.decode()[:500]
        assert elapsed < 2.0, f"too slow {elapsed}"
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
        start = time.time()
        proc = run(["--graph", gp, "--from", "N0", "--to", "N199", "--traffic", tp])
        elapsed = time.time() - start
        assert proc.returncode == 0
        assert elapsed < 2.0
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
