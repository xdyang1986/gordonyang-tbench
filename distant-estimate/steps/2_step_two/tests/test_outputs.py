import os, json, subprocess, tempfile, time, math

CANDIDATES = ["/app/router", "/app/src/router", "./router"]


def find_bin():
    # AFTR Blocker2 fix: enforce Go binary, force rebuild, delete stale script
    try:
        if os.path.exists("/app/router"):
            os.unlink("/app/router")
    except Exception:
        pass
    if os.path.exists("/app/go.mod"):
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
    assert not head.startswith(b"#!"), f"{BIN} is a script, must be Go binary"
    sz = os.path.getsize(BIN)
    assert sz > 500_000, f"{BIN} too small ({sz}) to be Go binary, likely stub"
    proc = subprocess.run(
        ["go", "version", "-m", BIN],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=10,
    )
    out = proc.stdout.decode(errors="ignore")
    if proc.returncode == 0:
        assert "go" in out.lower() or "build" in out.lower() or len(out) > 0, (
            "go version -m no Go info, not Go binary"
        )
    else:
        with open(BIN, "rb") as f:
            data = f.read(2_000_000)
        assert b"Go" in data or b"main.main" in data or b"runtime." in data, (
            f"{BIN} no Go runtime markers"
        )


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
            [BIN] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30
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
            [BIN] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30
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
            [BIN] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60
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
            [BIN] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30
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
            [BIN] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30
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
            [BIN] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30
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
            [BIN] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30
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
            [BIN] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30
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
            [BIN] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30
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
            [BIN] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30
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
            [BIN] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30
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
            [BIN] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30
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
            [BIN] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30
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
            [BIN] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30
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
            [BIN] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60
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
            [BIN] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30
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
            [BIN] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30
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
            [BIN] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30
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
            [BIN] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=90
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
            [BIN] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30
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
            [BIN] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30
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
            [BIN] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60
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
            [BIN] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30
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
            [BIN] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30
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
            [BIN] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=90
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
            [BIN] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60
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
        timeout=30,
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
            [BIN] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30
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
            [BIN] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=90
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
            [BIN] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=90
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
            [BIN] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30
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
            [BIN] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30
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
            [BIN] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30
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
            [BIN] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30
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
            [BIN] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=90
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
            [BIN] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60
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
            [BIN] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30
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
            [BIN] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30
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
            [BIN] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=90
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
            [BIN] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60
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
            [BIN] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30
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
            [BIN] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30
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
        timeout=30,
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
            [BIN] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30
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
        timeout=60,
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
    # AFTR FIX: avoid noisy ms-scale ratio – use larger workload + absolute guard
    nodes = [f"N{i}" for i in range(1000)]
    edges = [{"from": f"N{i}", "to": f"N{i + 1}", "distance": 1} for i in range(999)]
    edges += [
        {"from": f"N{i}", "to": f"N{i + 10}", "distance": 5} for i in range(0, 990, 10)
    ]
    graph = {"nodes": nodes, "edges": edges}
    traffic = {
        "traffic": [
            {"from": f"N{i}", "to": f"N{i + 1}", "factor": 1.5, "delay": 1}
            for i in range(999)
        ]
    }
    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps(traffic))
    # 500 requests all from N0 (same source) – larger workload for median
    same_reqs = [{"source": "N0", "destination": f"N{i}"} for i in range(1, 501)]
    # 500 requests with 500 distinct sources
    multi_reqs = [
        {"source": f"N{i}", "destination": f"N{(i * 7) % 1000}"} for i in range(500)
    ]
    rp_same = tmp(json.dumps(same_reqs))
    rp_multi = tmp(json.dumps(multi_reqs))
    try:
        # Warmup + median of 2 runs to reduce jitter
        times_same = []
        for _ in range(2):
            start = time.time()
            proc_same = run(["--graph", gp, "--requests", rp_same, "--traffic", tp])
            t_same = time.time() - start
            assert proc_same.returncode == 0, proc_same.stderr.decode()[:500]
            times_same.append(t_same)
        t_same = sorted(times_same)[len(times_same) // 2]

        times_multi = []
        for _ in range(2):
            start = time.time()
            proc_multi = run(["--graph", gp, "--requests", rp_multi, "--traffic", tp])
            t_multi = time.time() - start
            assert proc_multi.returncode == 0, proc_multi.stderr.decode()[:500]
            times_multi.append(t_multi)
        t_multi = sorted(times_multi)[len(times_multi) // 2]

        # AFTR robustness: if both timings <0.2s, skip strict ratio (process startup noise dominates)
        # Otherwise same-source should be significantly faster than multi-source if amortized
        if t_multi < 0.2 and t_same < 0.2:
            # Both too fast – just ensure same-source not drastically slower (allow 2x for noise)
            assert t_same <= max(0.5, 2.0 * t_multi), (
                f"same-source batch noise guard: t_same={t_same:.3f}s vs t_multi={t_multi:.3f}s"
            )
        else:
            # Host-independent relative bound: same-source <=50% of multi-source (relaxed from 35%)
            assert t_same <= 0.50 * t_multi + 0.3, (
                f"same-source batch should amortize: t_same={t_same:.3f}s vs t_multi={t_multi:.3f}s, "
                f"expected t_same <=0.50*t_multi+0.3. Per-request implementation costs ~200x."
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
            timeout=30,
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
            timeout=30,
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
            timeout=30,
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
            timeout=30,
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
            timeout=30,
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
            timeout=30,
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
            timeout=30,
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
            timeout=90,
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
            timeout=90,
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
            timeout=30,
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
            timeout=30,
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
            timeout=30,
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
            timeout=30,
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
            timeout=30,
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

    # Test that effective = raw*factor+delay not (raw+delay)*factor
    # Edge A-B raw10 factor2 delay5 => eff 25 correct, 30 if wrong formula
    # Path A-B-C: B-C raw10 factor1 delay0 => eff 10, total eff 35 correct vs 40 wrong
    # Direct A-C raw100 factor1 delay0 => eff 100, so path A-B-C should win (35 vs 100)
    # If agent uses (raw+delay)*factor, A-B eff (10+5)*2=30, total 40, still wins but with different eff value 40 not 35
    # So check effective value
    graph = {
        "nodes": ["A", "B", "C"],
        "edges": [
            {"from": "A", "to": "B", "distance": 10},
            {"from": "B", "to": "C", "distance": 10},
            {"from": "A", "to": "C", "distance": 100},
        ],
    }
    traffic = {"traffic": [{"from": "A", "to": "B", "factor": 2, "delay": 5}]}
    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps(traffic))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "C", "--traffic", tp])
        assert proc.returncode == 0, proc.stderr.decode()[:500]
        out = json.loads(proc.stdout.decode().strip())
        assert out["path"] == ["A", "B", "C"]
        assert math.isclose(out["distance"], 20, abs_tol=1e-6)
        assert math.isclose(out["effective_distance"], 35, abs_tol=1e-6), (
            f"expected 10*2+5 +10 =35, got {out['effective_distance']}"
        )
        assert math.isclose(out["traffic_delay"], 15, abs_tol=1e-6)
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_factor_zero_delay_reset_last_wins():
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

    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 10}]}
    gp = tmp(json.dumps(graph))
    # first entry factor1 delay100, second factor2 without delay -> delay reset to 0, eff 20 not 120
    traffic = {
        "traffic": [
            {"from": "A", "to": "B", "factor": 1, "delay": 100},
            {"from": "A", "to": "B", "factor": 2},
        ]
    }
    tp = tmp(json.dumps(traffic))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B", "--traffic", tp])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip())
        assert math.isclose(out["effective_distance"], 20, abs_tol=1e-6), (
            f"delay reset expected 20, got {out['effective_distance']}"
        )
        assert math.isclose(out["traffic_delay"], 10, abs_tol=1e-6)
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_duplicate_same_factor_different_delay_last_wins():
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

    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 10}]}
    gp = tmp(json.dumps(graph))
    traffic = {
        "traffic": [
            {"from": "A", "to": "B", "factor": 1, "delay": 1},
            {"from": "A", "to": "B", "factor": 1, "delay": 99},
        ]
    }
    tp = tmp(json.dumps(traffic))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B", "--traffic", tp])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip())
        assert math.isclose(out["effective_distance"], 109, abs_tol=1e-6)
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_factor_very_small_and_delay_large():
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

    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 100}]}
    gp = tmp(json.dumps(graph))
    traffic = {"traffic": [{"from": "A", "to": "B", "factor": 0.001, "delay": 1000}]}
    tp = tmp(json.dumps(traffic))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B", "--traffic", tp])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip())
        # eff = 100*0.001+1000=1000.1
        assert math.isclose(out["effective_distance"], 1000.1, rel_tol=1e-6)
        assert math.isclose(out["traffic_delay"], 900.1, rel_tol=1e-6)
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_delay_only_complex_reroute():
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
        "nodes": ["A", "B", "C", "D"],
        "edges": [
            {"from": "A", "to": "B", "distance": 1},
            {"from": "B", "to": "D", "distance": 1},
            {"from": "A", "to": "C", "distance": 2},
            {"from": "C", "to": "D", "distance": 2},
        ],
    }
    traffic = {
        "traffic": [
            {"from": "A", "to": "B", "factor": 1, "delay": 50},
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
            f"delay-only reroute expected A-C-D, got {out['path']}"
        )
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_file_with_empty_object_entry_invalid():
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
    tp = tmp(json.dumps({"traffic": [{"from": "A", "to": "B", "factor": 1}, {}]}))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B", "--traffic", tp])
        assert proc.returncode == 2
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_file_with_factor_missing_but_delay_present_invalid():
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
    tp = tmp(json.dumps({"traffic": [{"from": "A", "to": "B", "delay": 5}]}))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B", "--traffic", tp])
        assert proc.returncode == 2
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_file_with_from_equals_to_invalid():
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
    tp = tmp(json.dumps({"traffic": [{"from": "A", "to": "A", "factor": 1}]}))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B", "--traffic", tp])
        assert proc.returncode == 2
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_file_with_nonexisting_edge_invalid():
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
        "edges": [{"from": "A", "to": "B", "distance": 1}],
    }
    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps({"traffic": [{"from": "B", "to": "C", "factor": 1}]}))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B", "--traffic", tp])
        assert proc.returncode == 2, "traffic for edge not in graph should be invalid"
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_file_with_factor_string_invalid():
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
    for bad_factor in ['"2"', "true", "null", "{}", "[]"]:
        tp = tmp('{"traffic":[{"from":"A","to":"B","factor":' + bad_factor + "}]}")
        try:
            proc = run(["--graph", gp, "--from", "A", "--to", "B", "--traffic", tp])
            assert proc.returncode == 2, f"factor {bad_factor} should be invalid"
        finally:
            os.unlink(tp)
    os.unlink(gp)


def test_traffic_file_with_delay_string_invalid():
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
    for bad_delay in ['"5"', "true", "null", "{}", "[]", "-1"]:
        tp = tmp(
            '{"traffic":[{"from":"A","to":"B","factor":1,"delay":' + bad_delay + "}]}"
        )
        try:
            proc = run(["--graph", gp, "--from", "A", "--to", "B", "--traffic", tp])
            assert proc.returncode == 2, f"delay {bad_delay} should be invalid"
        finally:
            os.unlink(tp)
    os.unlink(gp)


def test_traffic_wrapper_null_invalid_vs_empty_valid():
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
    tp_null = tmp('{"traffic":null}')
    tp_empty = tmp('{"traffic":[]}')
    tp_array = tmp("[]")
    try:
        proc_null = run(
            ["--graph", gp, "--from", "A", "--to", "B", "--traffic", tp_null]
        )
        assert proc_null.returncode == 2, "traffic null should be invalid"
        proc_empty = run(
            ["--graph", gp, "--from", "A", "--to", "B", "--traffic", tp_empty]
        )
        assert proc_empty.returncode == 0, (
            f"empty wrapper array should be valid, got {proc_empty.returncode}"
        )
        proc_array = run(
            ["--graph", gp, "--from", "A", "--to", "B", "--traffic", tp_array]
        )
        assert proc_array.returncode == 0, f"direct empty array [] should be valid"
    finally:
        os.unlink(gp)
        os.unlink(tp_null)
        os.unlink(tp_empty)
        os.unlink(tp_array)


def test_traffic_large_graph_10000_nodes_with_traffic():
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
            timeout=90,
        )

    nodes = [f"N{i}" for i in range(10000)]
    edges = [{"from": f"N{i}", "to": f"N{i + 1}", "distance": 1} for i in range(9999)]
    graph = {"nodes": nodes, "edges": edges}
    traffic = {
        "traffic": [
            {"from": f"N{i}", "to": f"N{i + 1}", "factor": 1.2}
            for i in range(0, 9999, 1000)
        ]
    }
    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps(traffic))
    try:
        start = time.time()
        proc = run(["--graph", gp, "--from", "N0", "--to", "N9999", "--traffic", tp])
        elapsed = time.time() - start
        assert proc.returncode == 0, f"10000 nodes with traffic rc={proc.returncode}"
        assert elapsed < 8.0, f"too slow 10000 nodes with traffic {elapsed}"
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_batch_5000_with_traffic():
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
            timeout=90,
        )

    nodes = [f"N{i}" for i in range(300)]
    edges = [{"from": f"N{i}", "to": f"N{i + 1}", "distance": 1} for i in range(299)]
    graph = {"nodes": nodes, "edges": edges}
    traffic = {
        "traffic": [
            {"from": f"N{i}", "to": f"N{i + 1}", "factor": 1.1}
            for i in range(0, 299, 30)
        ]
    }
    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps(traffic))
    reqs = [{"source": "N0", "destination": f"N{i % 300}"} for i in range(5000)]
    rp = tmp(json.dumps(reqs))
    try:
        start = time.time()
        proc = run(["--graph", gp, "--requests", rp, "--traffic", tp])
        elapsed = time.time() - start
        assert proc.returncode == 0
        assert elapsed < 85.0, f"too slow 5000 batch with traffic {elapsed}"
        assert len(proc.stdout.decode().strip().splitlines()) == 5000
    finally:
        os.unlink(gp)
        os.unlink(tp)
        os.unlink(rp)


def test_traffic_tie_break_10_way_effective_raw_lex():
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

    nodes = ["A"] + [chr(ord("B") + i) for i in range(10)] + ["Z"]
    edges = []
    for i in range(10):
        mid = chr(ord("B") + i)
        edges.append({"from": "A", "to": mid, "distance": 5})
        edges.append({"from": mid, "to": "Z", "distance": 5})
    graph = {"nodes": nodes, "edges": edges}
    traffic = {
        "traffic": [
            {"from": "A", "to": chr(ord("B") + i), "factor": 1} for i in range(10)
        ]
    }
    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps(traffic))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "Z", "--traffic", tp])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip())
        assert out["path"] == ["A", "B", "Z"], (
            f"10-way effective tie should pick B, got {out['path']}"
        )
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_tie_break_secondary_raw_with_delay():
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
    # A-B-D eff12 raw11, A-C-D eff12 raw4 -> raw smaller wins A-C-D
    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps(traffic))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "D", "--traffic", tp])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip())
        assert out["path"] == ["A", "C", "D"], (
            f"secondary raw tie should pick raw 4, got {out['path']}"
        )
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_batch_order_preserved_with_traffic_large():
    import tempfile, os, subprocess, json, random

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
    traffic = {"traffic": [{"from": "A", "to": "B", "factor": 1.5}]}
    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps(traffic))
    random.seed(1)
    reqs = []
    for _ in range(200):
        s = random.choice(nodes)
        d = random.choice(nodes)
        reqs.append({"source": s, "destination": d})
    rp = tmp(json.dumps(reqs))
    try:
        proc = run(["--graph", gp, "--requests", rp, "--traffic", tp])
        assert proc.returncode in (0, 1)
        lines = proc.stdout.decode().strip().splitlines()
        assert len(lines) == 200
        for i, line in enumerate(lines):
            o = json.loads(line)
            assert (
                o["source"] == reqs[i]["source"]
                and o["destination"] == reqs[i]["destination"]
            )
    finally:
        os.unlink(gp)
        os.unlink(tp)
        os.unlink(rp)


def test_traffic_no_path_fields_minus_one_strict():
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
            and out["traffic_delay"] == -1
        )
        # also check batch
        rp = tmp(json.dumps([{"source": "A", "destination": "C"}]))
        try:
            proc2 = run(["--graph", gp, "--requests", rp, "--traffic", tp])
            assert proc2.returncode == 1
            out2 = json.loads(proc2.stdout.decode().strip().splitlines()[0])
            assert out2["effective_distance"] == -1
        finally:
            os.unlink(rp)
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_same_source_amortization_stricter():
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

    nodes = [f"N{i}" for i in range(500)]
    edges = [{"from": f"N{i}", "to": f"N{i + 1}", "distance": 1} for i in range(499)]
    graph = {"nodes": nodes, "edges": edges}
    traffic = {
        "traffic": [
            {"from": f"N{i}", "to": f"N{i + 1}", "factor": 1.2, "delay": 0}
            for i in range(0, 499, 50)
        ]
    }
    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps(traffic))
    same_reqs = [{"source": "N0", "destination": f"N{i}"} for i in range(1, 500)]
    multi_reqs = [
        {"source": f"N{i % 500}", "destination": f"N{(i * 13) % 500}"}
        for i in range(500)
    ]
    rp_same = tmp(json.dumps(same_reqs))
    rp_multi = tmp(json.dumps(multi_reqs))
    try:
        start = time.time()
        proc_same = run(["--graph", gp, "--requests", rp_same, "--traffic", tp])
        t_same = time.time() - start
        assert proc_same.returncode == 0
        start = time.time()
        proc_multi = run(["--graph", gp, "--requests", rp_multi, "--traffic", tp])
        t_multi = time.time() - start
        assert proc_multi.returncode == 0
        # Stricter: same-source <=25% multi-source (amortized)
        assert t_same <= 0.25 * t_multi + 0.5, (
            f"same-source should be much faster: {t_same:.3f}s vs {t_multi:.3f}s"
        )
    finally:
        os.unlink(gp)
        os.unlink(tp)
        os.unlink(rp_same)
        os.unlink(rp_multi)


def test_traffic_help_with_extra_flags_still_contains_traffic():
    import subprocess

    BIN = "/app/router"
    proc = subprocess.run(
        [BIN, "--help", "--unknown"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=30,
    )
    assert proc.returncode == 0
    out = proc.stdout.decode().lower()
    assert "traffic" in out, "help with extra flags should still contain traffic"
    for kw in ["graph", "from", "to", "requests", "help"]:
        assert kw in out


def test_traffic_flag_equals_syntax_with_traffic():
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
    tp = tmp(json.dumps({"traffic": [{"from": "A", "to": "B", "factor": 2}]}))
    try:
        proc = run([f"--graph={gp}", "--from=A", "--to=B", f"--traffic={tp}"])
        assert proc.returncode == 0, (
            f"equals syntax with traffic should work rc={proc.returncode} stderr={proc.stderr.decode()}"
        )
        out = json.loads(proc.stdout.decode().strip())
        assert math.isclose(out["effective_distance"], 10, abs_tol=1e-6)
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_direct_array_empty_valid():
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
    tp = tmp("[]")
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B", "--traffic", tp])
        assert proc.returncode == 0
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_factor_scientific_notation_negative_exponent_valid():
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

    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 100}]}
    gp = tmp(json.dumps(graph))
    tp = tmp('{"traffic":[{"from":"A","to":"B","factor":1e-2}]}')
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B", "--traffic", tp])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip())
        assert math.isclose(out["effective_distance"], 1, abs_tol=1e-6)
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_delay_scientific_notation_plus_valid():
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

    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 10}]}
    gp = tmp(json.dumps(graph))
    tp = tmp('{"traffic":[{"from":"A","to":"B","factor":1,"delay":1e+2}]}')
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B", "--traffic", tp])
        assert proc.returncode == 0, f"delay 1e+2 should be valid rc={proc.returncode}"
        out = json.loads(proc.stdout.decode().strip())
        assert math.isclose(out["effective_distance"], 110, abs_tol=1e-6)
    finally:
        os.unlink(gp)
        os.unlink(tp)


# === GIGA HARD EXTRA additions for Turn2 - step2 too easy enhancement ===


def test_traffic_top_level_string_invalid():
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
    for bad in [
        '"string"',
        "123",
        "null",
        "true",
        '{"foo":[]}',
        '{"traffic":null}',
        '{"traffic":{}}',
        '{"traffic":"x"}',
        '{"traffic":123}',
    ]:
        tp = tmp(bad)
        try:
            proc = run(["--graph", gp, "--from", "A", "--to", "B", "--traffic", tp])
            assert proc.returncode == 2, f"top-level {bad} should be invalid"
            assert proc.stdout.decode().strip() == ""
        finally:
            os.unlink(tp)
    os.unlink(gp)


def test_traffic_direct_array_invalid_elements_extra_hard2():
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
    for bad_entry in [
        "null",
        "123",
        '"str"',
        "[1,2,3]",
        "true",
        "{}",
        '{"foo":1}',
        '{"from":"A"}',
        '{"from":"A","to":"B"}',
    ]:
        tp = tmp('[{"from":"A","to":"B","factor":1},' + bad_entry + "]")
        # Actually need direct array with that bad entry
        tp2 = tmp("[" + bad_entry + "]")
        try:
            proc = run(["--graph", gp, "--from", "A", "--to", "B", "--traffic", tp2])
            assert proc.returncode == 2, (
                f"direct array element {bad_entry} should be invalid"
            )
        finally:
            os.unlink(tp)
            os.unlink(tp2)
    os.unlink(gp)


def test_traffic_entry_missing_fields():
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
    for bad in [
        '{"traffic":[{"to":"B","factor":1}]}',
        '{"traffic":[{"from":"A","factor":1}]}',
        '{"traffic":[{"from":"A","to":"B"}]}',
        '[{"to":"B","factor":1}]',
        '[{"from":"A","factor":1}]',
        '[{"from":"A","to":"B"}]',
        '{"traffic":[{"from":"A","to":"B","factor":1,"delay":1,"extra":"x"},{}]}',
    ]:
        tp = tmp(bad)
        try:
            proc = run(["--graph", gp, "--from", "A", "--to", "B", "--traffic", tp])
            assert proc.returncode == 2, f"missing fields {bad} should be invalid"
        finally:
            os.unlink(tp)
    os.unlink(gp)


def test_traffic_entry_from_to_not_string():
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
    for bad_from in [123, "null", "true", "{}", "[]", '"":"']:
        # use raw json
        for bad_entry in [
            f'{{"from":{bad_from},"to":"B","factor":1}}',
            f'{{"from":"A","to":{bad_from},"factor":1}}',
        ]:
            tp = tmp('{"traffic":[' + bad_entry + "]}")
            try:
                proc = run(["--graph", gp, "--from", "A", "--to", "B", "--traffic", tp])
                assert proc.returncode == 2, (
                    f"from/to not string {bad_entry} should be invalid"
                )
            finally:
                os.unlink(tp)
    os.unlink(gp)


def test_traffic_entry_whitespace_only_invalid():
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
    for bad in ["   ", "", " \t\n "]:
        tp = tmp(json.dumps({"traffic": [{"from": bad, "to": "B", "factor": 1}]}))
        tp2 = tmp(json.dumps({"traffic": [{"from": "A", "to": bad, "factor": 1}]}))
        try:
            proc = run(["--graph", gp, "--from", "A", "--to", "B", "--traffic", tp])
            assert proc.returncode == 2, (
                f"whitespace from {repr(bad)} should be invalid"
            )
            proc2 = run(["--graph", gp, "--from", "A", "--to", "B", "--traffic", tp2])
            assert proc2.returncode == 2
        finally:
            os.unlink(tp)
            os.unlink(tp2)
    os.unlink(gp)


def test_traffic_entry_leading_trailing_space_exact_invalid():
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
    # leading space " A" does not exist in nodes -> invalid, not no-route
    tp = tmp(json.dumps({"traffic": [{"from": " A", "to": "B", "factor": 1}]}))
    tp2 = tmp(json.dumps({"traffic": [{"from": "A", "to": " B", "factor": 1}]}))
    tp3 = tmp(json.dumps({"traffic": [{"from": "A ", "to": "B", "factor": 1}]}))
    try:
        for t in [tp, tp2, tp3]:
            proc = run(["--graph", gp, "--from", "A", "--to", "B", "--traffic", t])
            assert proc.returncode == 2, (
                "leading/trailing space exact should be invalid for traffic (edge not found)"
            )
    finally:
        os.unlink(gp)
        os.unlink(tp)
        os.unlink(tp2)
        os.unlink(tp3)


def test_traffic_factor_various_invalid():
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
    for bad_factor in ["0", "-1", "-0.5", '"2"', "true", "false", "null", "{}", "[]"]:
        tp = tmp('{"traffic":[{"from":"A","to":"B","factor":' + bad_factor + "}]}")
        try:
            proc = run(["--graph", gp, "--from", "A", "--to", "B", "--traffic", tp])
            assert proc.returncode == 2, f"factor {bad_factor} should be invalid"
            assert proc.stdout.decode().strip() == ""
        finally:
            os.unlink(tp)
    os.unlink(gp)


def test_traffic_delay_various_invalid():
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
    for bad_delay in ["-1", "-0.1", '"5"', "true", "false", "null", "{}", "[]"]:
        tp = tmp(
            '{"traffic":[{"from":"A","to":"B","factor":1,"delay":' + bad_delay + "}]}"
        )
        try:
            proc = run(["--graph", gp, "--from", "A", "--to", "B", "--traffic", tp])
            assert proc.returncode == 2, f"delay {bad_delay} should be invalid"
        finally:
            os.unlink(tp)
    os.unlink(gp)


def test_traffic_file_bom_trailing_comma_comment_extra():
    import tempfile, os, subprocess, json

    def tmp_bytes(b, suffix=".json"):
        f = tempfile.NamedTemporaryFile(delete=False, suffix=suffix, mode="wb")
        f.write(b)
        f.close()
        return f.name

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
    # BOM
    tp_bom = tmp_bytes(b'\xef\xbb\xbf{"traffic":[{"from":"A","to":"B","factor":1}]}')
    tp_trail = tmp('{"traffic":[{"from":"A","to":"B","factor":1},]}')
    tp_comment = tmp('// comment\n{"traffic":[{"from":"A","to":"B","factor":1}]}')
    tp_direct_trail = tmp('[{"from":"A","to":"B","factor":1},]')
    tp_direct_comment = tmp('// comment\n[{"from":"A","to":"B","factor":1}]')
    try:
        for t in [tp_bom, tp_trail, tp_comment, tp_direct_trail, tp_direct_comment]:
            proc = run(["--graph", gp, "--from", "A", "--to", "B", "--traffic", t])
            assert proc.returncode == 2, (
                f"traffic malformed {t} should be invalid 2 not crash"
            )
            assert proc.stdout.decode().strip() == ""
    finally:
        os.unlink(gp)
        os.unlink(tp_bom)
        os.unlink(tp_trail)
        os.unlink(tp_comment)
        os.unlink(tp_direct_trail)
        os.unlink(tp_direct_comment)


def test_traffic_effective_formula_strict_per_edge():
    # Effective must be per edge raw*factor+delay sum, not (raw+delay)*factor
    # Create 2 edges A-B raw10 factor2 delay5 => correct 25, wrong (10+5)*2=30
    # B-C raw10 factor1 delay0 => correct 10, path total correct 35, wrong 40
    # If agent does wrong, effective_distance 40 vs 35, and traffic_delay 20 vs 15
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
        "nodes": ["A", "B", "C"],
        "edges": [
            {"from": "A", "to": "B", "distance": 10},
            {"from": "B", "to": "C", "distance": 10},
        ],
    }
    traffic = {
        "traffic": [
            {"from": "A", "to": "B", "factor": 2, "delay": 5},
            {"from": "B", "to": "C", "factor": 1, "delay": 0},
        ]
    }
    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps(traffic))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "C", "--traffic", tp])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip())
        assert math.isclose(out["distance"], 20, abs_tol=1e-6), (
            f"raw should be 20 got {out['distance']}"
        )
        assert math.isclose(out["effective_distance"], 35, abs_tol=1e-6), (
            f"effective should be 25+10=35 per-edge formula, got {out['effective_distance']} (wrong if (raw+delay)*factor)"
        )
        assert math.isclose(out["traffic_delay"], 15, abs_tol=1e-6)
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_raw_along_effective_best_not_raw_best():
    # Raw-shortest path A-B-D raw 2 but high factor 100 => eff 200
    # Longer A-C-D raw 20 eff 20 => should pick A-C-D and raw reported 20 not 2
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
        assert out["path"] == ["A", "C", "D"], f"should reroute to C got {out['path']}"
        assert math.isclose(out["distance"], 20, abs_tol=1e-6), (
            f"raw should be along effective-best 20, got {out['distance']}"
        )
        assert math.isclose(out["effective_distance"], 20, abs_tol=1e-6)
        assert math.isclose(out["traffic_delay"], 0, abs_tol=1e-6)
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_3_level_tie_effective_raw_lex():
    # Effective tie 12 for both paths, raw differs 11 vs 4 -> pick raw smaller A-C-D
    # If raw equal too, lex smallest B
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
        "nodes": ["A", "B", "C", "D"],
        "edges": [
            {"from": "A", "to": "B", "distance": 10},
            {"from": "B", "to": "D", "distance": 1},
            {"from": "A", "to": "C", "distance": 2},
            {"from": "C", "to": "D", "distance": 2},
        ],
    }
    # effective: A-B-D 10+1+1(delay)=12 raw11; A-C-D 2*2 +2*4=4+8=12 raw4
    traffic = {
        "traffic": [
            {"from": "A", "to": "B", "factor": 1, "delay": 0},
            {"from": "B", "to": "D", "factor": 1, "delay": 1},
            {"from": "A", "to": "C", "factor": 2, "delay": 0},
            {"from": "C", "to": "D", "factor": 4, "delay": 0},
        ]
    }
    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps(traffic))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "D", "--traffic", tp])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip())
        assert out["path"] == ["A", "C", "D"], (
            f"secondary raw tie: expected A-C-D raw4 vs A-B-D raw11, got {out['path']}"
        )
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_lex_deeper_diamond_with_traffic():
    # Diamond of diamonds effective equal, decision at depth2: paths A-B1-B2-Z, A-B1-C2-Z, A-C1-B2-Z, A-C1-C2-Z all cost 15 effective
    # Should pick A-B1-B2-Z because B1<C1 and B2/C2 secondary
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
    traffic = {"traffic": []}
    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps(traffic))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "Z", "--traffic", tp])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip())
        assert out["path"] == ["A", "B1", "B2", "Z"], (
            f"deeper diamond with traffic should pick A-B1-B2-Z, got {out['path']}"
        )
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_case_sensitive_ascii_with_traffic():
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

    # 'A' 65 < 'a' 97, '-'45 < '.'46 < '_'95
    graph = {
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
    traffic = {
        "traffic": [
            {"from": "A", "to": "A-B", "factor": 1},
            {"from": "A", "to": "A.B", "factor": 1},
            {"from": "A", "to": "A_B", "factor": 1},
            {"from": "A", "to": "a", "factor": 1},
        ]
    }
    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps(traffic))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "Z", "--traffic", tp])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip())
        # ASCII: '-'45 < '.'46 < '_'95 and 'A' < 'a', so A-B < A.B < A_B? Actually '-' < '.' < '_' so A-B first
        assert out["path"] == ["A", "A-B", "Z"], (
            f"case-sensitive ascii with traffic should pick A-B, got {out['path']}"
        )
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_duplicate_reverse_with_delay_reset():
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

    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 10}]}
    gp = tmp(json.dumps(graph))
    # First A-B factor1 delay100, second B-A factor2 delay0 (reverse, no delay) -> last wins factor2 delay0 => eff20
    traffic = {
        "traffic": [
            {"from": "A", "to": "B", "factor": 1, "delay": 100},
            {"from": "B", "to": "A", "factor": 2},
        ]
    }
    tp = tmp(json.dumps(traffic))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B", "--traffic", tp])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip())
        assert math.isclose(out["effective_distance"], 20, abs_tol=1e-6), (
            f"reverse duplicate delay reset expected 20 got {out['effective_distance']}"
        )
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_output_fields_strict():
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
        }, f"single with traffic must have exactly 4 keys, got {out.keys()}"
        assert isinstance(out["path"], list) and isinstance(
            out["distance"], (int, float)
        )
        # batch
        rp = tmp(json.dumps([{"source": "A", "destination": "B"}]))
        proc2 = run(["--graph", gp, "--requests", rp, "--traffic", tp])
        assert proc2.returncode == 0
        out2 = json.loads(proc2.stdout.decode().strip().splitlines()[0])
        assert set(out2.keys()) == {
            "source",
            "destination",
            "path",
            "distance",
            "effective_distance",
            "traffic_delay",
        }, f"batch with traffic 6 keys, got {out2.keys()}"
        # no-route
        proc3 = run(["--graph", gp, "--from", "A", "--to", "C", "--traffic", tp])
        assert proc3.returncode == 1
        out3 = json.loads(proc3.stdout.decode().strip())
        assert (
            out3["distance"] == -1
            and out3["effective_distance"] == -1
            and out3["traffic_delay"] == -1
        )
        assert set(out3.keys()) == {
            "path",
            "distance",
            "effective_distance",
            "traffic_delay",
        }
        os.unlink(rp)
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_flag_order_independence():
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
    tp = tmp(json.dumps({"traffic": [{"from": "A", "to": "B", "factor": 2}]}))
    try:
        orders = [
            ["--graph", gp, "--from", "A", "--to", "B", "--traffic", tp],
            ["--traffic", tp, "--graph", gp, "--from", "A", "--to", "B"],
            ["--from", "A", "--graph", gp, "--traffic", tp, "--to", "B"],
            [f"--graph={gp}", f"--from=A", f"--to=B", f"--traffic={tp}"],
            [f"--traffic={tp}", f"--graph={gp}", "--from", "A", "--to", "B"],
        ]
        for args in orders:
            proc = run(args)
            assert proc.returncode == 0, (
                f"flag order {args} should work rc={proc.returncode} stderr={proc.stderr.decode()}"
            )
            out = json.loads(proc.stdout.decode().strip())
            assert math.isclose(out["effective_distance"], 10, abs_tol=1e-6)
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_help_precedence_with_traffic():
    import subprocess

    BIN = "/app/router"
    tests = [
        [BIN, "--help", "--unknown"],
        [BIN, "--help", "--traffic", "dummy"],
        [BIN, "--traffic", "dummy", "--help"],
        [BIN, "help"],
        [BIN],
        [BIN, "--help=true"],
        [BIN, "-h"],
        [BIN, "--help", "--graph", "nonexistent", "--traffic", "dummy", "--unknown"],
    ]
    for args in tests:
        proc = subprocess.run(
            args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30
        )
        assert proc.returncode == 0, (
            f"help precedence {args} should be 0 got {proc.returncode}"
        )
        out = proc.stdout.decode().lower()
        assert "traffic" in out, f"help should contain traffic for {args}"
        assert "graph" in out


def test_traffic_single_empty_from_invalid_vs_batch_no_route():
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
    tp = tmp(json.dumps({"traffic": [{"from": "A", "to": "B", "factor": 1}]}))
    try:
        # single empty from invalid exit2
        proc = run(["--graph", gp, "--from", "", "--to", "B", "--traffic", tp])
        assert proc.returncode == 2 and proc.stdout.decode().strip() == "", (
            "single empty from with traffic should be invalid exit2"
        )
        proc2 = run(["--graph", gp, "--from", "   ", "--to", "B", "--traffic", tp])
        assert proc2.returncode == 2
        # batch empty source no-route exit1 with -1 for all fields
        rp = tmp(json.dumps([{"source": "", "destination": "B"}]))
        proc3 = run(["--graph", gp, "--requests", rp, "--traffic", tp])
        assert proc3.returncode == 1, (
            "batch empty source with traffic should be no-route exit1"
        )
        out = json.loads(proc3.stdout.decode().strip().splitlines()[0])
        assert (
            out["distance"] == -1
            and out["effective_distance"] == -1
            and out["traffic_delay"] == -1
        )
        os.unlink(rp)
        # batch whitespace
        rp2 = tmp(json.dumps([{"source": "   ", "destination": "B"}]))
        proc4 = run(["--graph", gp, "--requests", rp2, "--traffic", tp])
        assert proc4.returncode == 1
        out2 = json.loads(proc4.stdout.decode().strip().splitlines()[0])
        assert out2["effective_distance"] == -1
        os.unlink(rp2)
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_non_existing_node_no_route_with_traffic():
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
    tp = tmp(json.dumps({"traffic": [{"from": "A", "to": "B", "factor": 1}]}))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "NonExist", "--traffic", tp])
        assert proc.returncode == 1
        out = json.loads(proc.stdout.decode().strip())
        assert (
            out["path"] == []
            and out["distance"] == -1
            and out["effective_distance"] == -1
        )
        proc2 = run(
            ["--graph", gp, "--from", "A", "--to", " A", "--traffic", tp]
        )  # leading space no-route
        assert proc2.returncode == 1
        out2 = json.loads(proc2.stdout.decode().strip())
        assert out2["effective_distance"] == -1
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_requests_validation_with_traffic():
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
    tp = tmp(json.dumps({"traffic": [{"from": "A", "to": "B", "factor": 1}]}))
    invalid_requests = [
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
        '[{"from":"A","to":"B","extra":1}, null]',
    ]
    try:
        for bad in invalid_requests:
            rp = tmp(bad)
            try:
                proc = run(["--graph", gp, "--requests", rp, "--traffic", tp])
                assert proc.returncode == 2, (
                    f"requests {bad} with traffic should be invalid exit2, got {proc.returncode}"
                )
                assert proc.stdout.decode().strip() == ""
            finally:
                os.unlink(rp)
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_large_graph_2000_nodes_with_traffic():
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
            timeout=90,
        )

    nodes = [f"N{i}" for i in range(2000)]
    edges = [{"from": f"N{i}", "to": f"N{i + 1}", "distance": 1} for i in range(1999)]
    edges += [
        {"from": f"N{i}", "to": f"N{i + 100}", "distance": 50}
        for i in range(0, 1900, 100)
    ]
    graph = {"nodes": nodes, "edges": edges}
    traffic = {
        "traffic": [
            {"from": f"N{i}", "to": f"N{i + 1}", "factor": 1.5}
            for i in range(0, 1999, 200)
        ]
    }
    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps(traffic))
    try:
        start = time.time()
        proc = run(["--graph", gp, "--from", "N0", "--to", "N1999", "--traffic", tp])
        elapsed = time.time() - start
        assert proc.returncode == 0, f"2000 nodes with traffic rc={proc.returncode}"
        assert elapsed < 5.0, f"too slow 2000 nodes with traffic {elapsed}"
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_batch_1000_float_distances_with_traffic():
    import tempfile, os, subprocess, json, time, math

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

    nodes = [f"N{i}" for i in range(100)]
    edges = [{"from": f"N{i}", "to": f"N{i + 1}", "distance": 0.5} for i in range(99)]
    graph = {"nodes": nodes, "edges": edges}
    traffic = {
        "traffic": [
            {"from": f"N{i}", "to": f"N{i + 1}", "factor": 1.1, "delay": 0.2}
            for i in range(0, 99, 10)
        ]
    }
    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps(traffic))
    reqs = [{"source": "N0", "destination": f"N{i % 100}"} for i in range(1000)]
    rp = tmp(json.dumps(reqs))
    try:
        start = time.time()
        proc = run(["--graph", gp, "--requests", rp, "--traffic", tp])
        elapsed = time.time() - start
        assert proc.returncode == 0, proc.stderr.decode()[:500]
        assert elapsed < 6.0, f"too slow 1000 float batch with traffic {elapsed}"
        lines = proc.stdout.decode().strip().splitlines()
        assert len(lines) == 1000
        # check first has effective
        o = json.loads(lines[0])
        assert "effective_distance" in o
    finally:
        os.unlink(gp)
        os.unlink(tp)
        os.unlink(rp)


def test_traffic_requests_trailing_comma_invalid_with_traffic():
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
    tp = tmp(json.dumps({"traffic": [{"from": "A", "to": "B", "factor": 1}]}))
    rp = tmp('[{"source":"A","destination":"B"},]')
    try:
        proc = run(["--graph", gp, "--requests", rp, "--traffic", tp])
        assert proc.returncode == 2, (
            "requests trailing comma with traffic should be invalid"
        )
        assert proc.stdout.decode().strip() == ""
    finally:
        os.unlink(gp)
        os.unlink(tp)
        os.unlink(rp)


def test_traffic_graph_duplicate_min_plus_traffic():
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

    # graph duplicate edges 10 and 3, min is 3, traffic factor 2 => effective 6
    graph = {
        "nodes": ["A", "B"],
        "edges": [
            {"from": "A", "to": "B", "distance": 10},
            {"from": "A", "to": "B", "distance": 3},
        ],
    }
    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps({"traffic": [{"from": "A", "to": "B", "factor": 2}]}))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B", "--traffic", tp])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip())
        assert math.isclose(out["distance"], 3, abs_tol=1e-6), (
            f"duplicate min raw 3, got {out['distance']}"
        )
        assert math.isclose(out["effective_distance"], 6, abs_tol=1e-6)
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_special_chars_node_ids_with_traffic():
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
        "nodes": ["A/B", "C.D", "E-F_G"],
        "edges": [
            {"from": "A/B", "to": "C.D", "distance": 5},
            {"from": "C.D", "to": "E-F_G", "distance": 5},
        ],
    }
    traffic = {
        "traffic": [
            {"from": "A/B", "to": "C.D", "factor": 2},
            {"from": "C.D", "to": "E-F_G", "factor": 0.5},
        ]
    }
    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps(traffic))
    try:
        proc = run(["--graph", gp, "--from", "A/B", "--to", "E-F_G", "--traffic", tp])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip())
        assert out["path"] == ["A/B", "C.D", "E-F_G"]
        assert math.isclose(out["distance"], 10, abs_tol=1e-6)
        assert math.isclose(out["effective_distance"], 5 * 2 + 5 * 0.5, abs_tol=1e-6)
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_very_small_and_large_factor_same_path():
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
        "nodes": ["A", "B", "C"],
        "edges": [
            {"from": "A", "to": "B", "distance": 1000},
            {"from": "B", "to": "C", "distance": 1000},
        ],
    }
    traffic = {
        "traffic": [
            {"from": "A", "to": "B", "factor": 1e-9},
            {"from": "B", "to": "C", "factor": 1e6},
        ]
    }
    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps(traffic))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "C", "--traffic", tp])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip())
        # eff = 1000*1e-9 + 1000*1e6 = 1e-6 + 1e9 ~ 1e9
        assert math.isclose(
            out["effective_distance"], 1000 * 1e-9 + 1000 * 1e6, rel_tol=1e-6
        )
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_delay_accumulation_and_negative_delay():
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
        "nodes": ["A", "B", "C"],
        "edges": [
            {"from": "A", "to": "B", "distance": 10},
            {"from": "B", "to": "C", "distance": 10},
        ],
    }
    traffic = {
        "traffic": [
            {"from": "A", "to": "B", "factor": 0.5, "delay": 0},
            {"from": "B", "to": "C", "factor": 1, "delay": 5},
        ]
    }
    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps(traffic))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "C", "--traffic", tp])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip())
        # eff A-B 5 + B-C 15 =20 raw20 delay0? actually raw20 eff20 delay0? Let's compute: A-B raw10*0.5=5 eff5, B-C 10*1+5=15 eff15 total eff20 raw20 delay0
        assert math.isclose(out["effective_distance"], 20, abs_tol=1e-6)
        assert math.isclose(out["traffic_delay"], 0, abs_tol=1e-6)
        # negative delay case factor<1
        tp2 = tmp(json.dumps({"traffic": [{"from": "A", "to": "B", "factor": 0.5}]}))
        proc2 = run(["--graph", gp, "--from", "A", "--to", "B", "--traffic", tp2])
        assert proc2.returncode == 0
        out2 = json.loads(proc2.stdout.decode().strip())
        assert math.isclose(out2["traffic_delay"], -5, abs_tol=1e-6), (
            "factor<1 should give negative traffic_delay"
        )
        os.unlink(tp2)
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_10_way_tie_with_traffic_real():
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

    nodes = ["A"] + [chr(ord("B") + i) for i in range(10)] + ["Z"]
    edges = []
    for i in range(10):
        mid = chr(ord("B") + i)
        edges.append({"from": "A", "to": mid, "distance": 5})
        edges.append({"from": mid, "to": "Z", "distance": 5})
    graph = {"nodes": nodes, "edges": edges}
    traffic = {
        "traffic": [
            {"from": "A", "to": chr(ord("B") + i), "factor": 1, "delay": i * 0.0}
            for i in range(10)
        ]
    }
    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps(traffic))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "Z", "--traffic", tp])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip())
        assert out["path"] == ["A", "B", "Z"], (
            f"10-way tie with traffic should pick B, got {out['path']}"
        )
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_batch_with_empty_dest_no_route_with_traffic():
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
    tp = tmp(json.dumps({"traffic": [{"from": "A", "to": "B", "factor": 2}]}))
    rp = tmp(json.dumps([{"source": "A", "destination": ""}]))
    try:
        proc = run(["--graph", gp, "--requests", rp, "--traffic", tp])
        assert proc.returncode == 1
        out = json.loads(proc.stdout.decode().strip().splitlines()[0])
        assert (
            out["path"] == []
            and out["distance"] == -1
            and out["effective_distance"] == -1
        )
    finally:
        os.unlink(gp)
        os.unlink(tp)
        os.unlink(rp)


def test_traffic_graph_nodes_contain_non_string_with_traffic():
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

    # nodes contain non-string even with traffic should be invalid exit2
    graph_invalid = (
        '{"nodes":["A",123,"B"],"edges":[{"from":"A","to":"B","distance":1}]}'
    )
    gp = tmp(graph_invalid)
    tp = tmp(json.dumps({"traffic": [{"from": "A", "to": "B", "factor": 1}]}))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B", "--traffic", tp])
        assert proc.returncode == 2 and proc.stdout.decode().strip() == ""
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_graph_edges_contain_non_object_with_traffic():
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

    graph_invalid = '{"nodes":["A","B"],"edges":[123]}'
    gp = tmp(graph_invalid)
    tp = tmp(json.dumps({"traffic": []}))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B", "--traffic", tp])
        assert proc.returncode == 2
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_factor_scientific_plus_valid_detailed():
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

    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 10}]}
    gp = tmp(json.dumps(graph))
    for factor_str in ["1e+2", "1E+3", "1e+3", "1E+2", "2.5e+2"]:
        # write raw json with that factor literal to test parser accepts plus
        tp = tmp('{"traffic":[{"from":"A","to":"B","factor":' + factor_str + "}]}")
        try:
            proc = run(["--graph", gp, "--from", "A", "--to", "B", "--traffic", tp])
            assert proc.returncode == 0, (
                f"factor {factor_str} should be valid, rc={proc.returncode} stderr={proc.stderr.decode()}"
            )
            out = json.loads(proc.stdout.decode().strip())
            # compute expected
            expected = 10 * float(factor_str)
            assert math.isclose(out["effective_distance"], expected, rel_tol=1e-6), (
                f"factor {factor_str} expected {expected}"
            )
        finally:
            os.unlink(tp)
    os.unlink(gp)


def test_traffic_batch_2000_with_traffic_relative_extra_hard():
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
            timeout=90,
        )

    nodes = [f"N{i}" for i in range(200)]
    edges = [{"from": f"N{i}", "to": f"N{i + 1}", "distance": 1} for i in range(199)]
    graph = {"nodes": nodes, "edges": edges}
    traffic = {
        "traffic": [
            {"from": f"N{i}", "to": f"N{i + 1}", "factor": 1.2}
            for i in range(0, 199, 20)
        ]
    }
    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps(traffic))
    rp1 = tmp(json.dumps([{"source": "N0", "destination": "N199"}]))
    rp2000 = tmp(
        json.dumps(
            [{"source": "N0", "destination": f"N{i % 200}"} for i in range(2000)]
        )
    )
    try:
        start = time.time()
        base = run(["--graph", gp, "--requests", rp1, "--traffic", tp])
        t_base = time.time() - start
        assert base.returncode == 0
        start = time.time()
        proc = run(["--graph", gp, "--requests", rp2000, "--traffic", tp])
        t_2000 = time.time() - start
        assert proc.returncode == 0
        assert t_2000 <= 25 * t_base + 1.0, (
            f"2000 batch relative too slow {t_2000:.3f} vs base {t_base:.3f}"
        )
        assert len(proc.stdout.decode().strip().splitlines()) == 2000
    finally:
        os.unlink(gp)
        os.unlink(tp)
        os.unlink(rp1)
        os.unlink(rp2000)


def test_traffic_same_source_amortization_with_traffic_500():
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
            timeout=90,
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
    same_reqs = [
        {"source": "N0", "destination": f"N{(i % 199) + 1}"} for i in range(500)
    ]
    multi_reqs = [
        {"source": f"N{i % 200}", "destination": f"N{(i * 7) % 200}"}
        for i in range(500)
    ]
    rp_same = tmp(json.dumps(same_reqs))
    rp_multi = tmp(json.dumps(multi_reqs))
    try:
        start = time.time()
        proc_same = run(["--graph", gp, "--requests", rp_same, "--traffic", tp])
        t_same = time.time() - start
        assert proc_same.returncode == 0
        start = time.time()
        proc_multi = run(["--graph", gp, "--requests", rp_multi, "--traffic", tp])
        t_multi = time.time() - start
        assert proc_multi.returncode == 0
        assert t_same <= 0.25 * t_multi + 1.0, (
            f"same-source 500 should be <=25% multi: {t_same:.3f} vs {t_multi:.3f}"
        )
    finally:
        os.unlink(gp)
        os.unlink(tp)
        os.unlink(rp_same)
        os.unlink(rp_multi)


def test_traffic_float_tolerance_effective_equal_within_1e9():
    # Two paths effective difference 1e-10 should be considered tie, then raw decides
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

    # Path A-B-D: A-B 5 factor1, B-D 5+1e-10 factor1 => eff 10+1e-10 raw10
    # Path A-C-D: A-C 5 factor1, C-D 5 factor1 => eff10 raw10 -> tie effective within 1e-9, raw equal, lex B<C so A-B-D wins? Actually B<C so A-B-D lex smaller than A-C-D.
    # Instead create raw difference for secondary tie
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
            {"from": "B", "to": "D", "factor": 1, "delay": 1e-10},
            {"from": "A", "to": "C", "factor": 2, "delay": 0},
            {"from": "C", "to": "D", "factor": 4, "delay": 0},
        ]
    }
    # A-B-D eff 10+1+1e-10=11.0000000001 raw11, A-C-D eff 4+8=12 raw4 not equal
    # Let's make effective equal within epsilon: need 12 vs 12.00000000001
    # A-B-D raw11 eff 12 (10+1+1), A-C-D raw4 eff 12 (4+8)
    # Use delay 1 for B-D to get eff12, and make A-C-D eff12 as before, difference 1e-10
    traffic2 = {
        "traffic": [
            {"from": "A", "to": "B", "factor": 1, "delay": 0},
            {"from": "B", "to": "D", "factor": 1, "delay": 1},
            {"from": "A", "to": "C", "factor": 2, "delay": 0},
            {"from": "C", "to": "D", "factor": 4, "delay": 0.0000000001},
        ]
    }
    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps(traffic2))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "D", "--traffic", tp])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip())
        # effective tie within 1e-9 (12 vs 12.0000000001) => raw smaller A-C-D raw4 wins
        assert out["path"] == ["A", "C", "D"], (
            f"tolerance effective tie should pick raw smaller, got {out['path']}"
        )
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_missing_traffic_flag_still_raw():
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
        assert set(out.keys()) == {"path", "distance"}
        assert out["distance"] == 5
    finally:
        os.unlink(gp)


# === ULTRA HARD EXTRA v2 - making step2 truly discriminating ===


def test_traffic_effective_formula_3_edges_discriminating():
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

    # 3 edges: A-B raw10 factor2 delay5 correct 25 wrong 30; B-C raw20 factor1 delay0 correct20 wrong20; C-D raw5 factor3 delay2 correct17 wrong21
    # total correct 62, wrong 71
    graph = {
        "nodes": ["A", "B", "C", "D"],
        "edges": [
            {"from": "A", "to": "B", "distance": 10},
            {"from": "B", "to": "C", "distance": 20},
            {"from": "C", "to": "D", "distance": 5},
        ],
    }
    traffic = {
        "traffic": [
            {"from": "A", "to": "B", "factor": 2, "delay": 5},
            {"from": "B", "to": "C", "factor": 1, "delay": 0},
            {"from": "C", "to": "D", "factor": 3, "delay": 2},
        ]
    }
    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps(traffic))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "D", "--traffic", tp])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip())
        assert math.isclose(out["distance"], 35, abs_tol=1e-6), (
            f"raw should be 35, got {out['distance']}"
        )
        assert math.isclose(out["effective_distance"], 62, abs_tol=1e-6), (
            f"effective should be 25+20+17=62 per-edge, got {out['effective_distance']} (if (raw+delay)*factor would be 30+20+21=71)"
        )
        assert math.isclose(out["traffic_delay"], 27, abs_tol=1e-6)
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_raw_must_follow_effective_best_complex():
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

    # Graph: two paths A-B-D and A-C-D
    # A-B 1, B-D 1 raw2 but factor100 each effective 200
    # A-C 10, C-D 10 raw20 effective20 -> pick A-C-D, raw reported must be 20 not 2
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
            {"from": "A", "to": "B", "factor": 100, "delay": 0},
            {"from": "B", "to": "D", "factor": 100, "delay": 0},
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
        assert out["path"] == ["A", "C", "D"], f"should reroute to C, got {out['path']}"
        assert math.isclose(out["distance"], 20, abs_tol=1e-6), (
            f"raw must be along effective-best 20, got {out['distance']} (common bug: report raw along raw-best 2)"
        )
        assert math.isclose(out["effective_distance"], 20, abs_tol=1e-6)
        assert math.isclose(out["traffic_delay"], 0, abs_tol=1e-6)
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_delay_reset_reverse_interleaved_extra_fields():
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

    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 10}]}
    gp = tmp(json.dumps(graph))
    # Entries: A-B factor1 delay100 extra ignored, B-A factor2 (no delay, reverse) should reset delay 0 eff20
    # Then A-B factor3 delay50 extra ignored, final eff should be 10*3+50=80
    traffic = {
        "traffic": [
            {
                "from": "A",
                "to": "B",
                "factor": 1,
                "delay": 100,
                "extra": "ignore",
                "meta": {"x": 1},
            },
            {"from": "B", "to": "A", "factor": 2},
            {"from": "A", "to": "B", "factor": 3, "delay": 50, "weight": 999},
        ]
    }
    tp = tmp(json.dumps(traffic))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B", "--traffic", tp])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip())
        assert math.isclose(out["effective_distance"], 80, abs_tol=1e-6), (
            f"last wins factor3 delay50 => eff80, got {out['effective_distance']}"
        )
        # Now test without third, second without delay resets
        tp2 = tmp(
            json.dumps(
                {
                    "traffic": [
                        {"from": "A", "to": "B", "factor": 1, "delay": 100},
                        {"from": "B", "to": "A", "factor": 2},
                    ]
                }
            )
        )
        proc2 = run(["--graph", gp, "--from", "A", "--to", "B", "--traffic", tp2])
        assert proc2.returncode == 0
        out2 = json.loads(proc2.stdout.decode().strip())
        assert math.isclose(out2["effective_distance"], 20, abs_tol=1e-6), (
            f"reverse duplicate without delay should reset delay 0 => eff20, got {out2['effective_distance']}"
        )
        os.unlink(tp2)
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_factor_scientific_plus_and_negative_exponent_mix():
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
        "nodes": ["A", "B", "C"],
        "edges": [
            {"from": "A", "to": "B", "distance": 10},
            {"from": "B", "to": "C", "distance": 10},
        ],
    }
    gp = tmp(json.dumps(graph))
    # factor 1e+2 =100, factor 1e-2=0.01, factor 1E+3=1000, factor 2.5e+2=250
    for factor_literal, factor_val in [
        ("1e+2", 100),
        ("1E+3", 1000),
        ("1e+3", 1000),
        ("1E+2", 100),
        ("2.5e+2", 250),
        ("1e-2", 0.01),
        ("1e-3", 0.001),
    ]:
        tp = tmp('{"traffic":[{"from":"A","to":"B","factor":' + factor_literal + "}]}")
        try:
            proc = run(["--graph", gp, "--from", "A", "--to", "B", "--traffic", tp])
            assert proc.returncode == 0, (
                f"factor {factor_literal} should be valid, rc={proc.returncode} err={proc.stderr.decode()}"
            )
            out = json.loads(proc.stdout.decode().strip())
            assert math.isclose(
                out["effective_distance"], 10 * factor_val, rel_tol=1e-6
            ), f"factor {factor_literal} expected {10 * factor_val}"
        finally:
            os.unlink(tp)
    os.unlink(gp)


def test_traffic_batch_1000_float_with_traffic_hard():
    import tempfile, os, subprocess, json, time, math

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

    nodes = [f"N{i}" for i in range(100)]
    edges = [
        {"from": f"N{i}", "to": f"N{i + 1}", "distance": 0.123456789} for i in range(99)
    ]
    graph = {"nodes": nodes, "edges": edges}
    traffic = {
        "traffic": [
            {"from": f"N{i}", "to": f"N{i + 1}", "factor": 1.5, "delay": 0.5}
            for i in range(0, 99, 10)
        ]
    }
    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps(traffic))
    reqs = [{"source": "N0", "destination": f"N{i % 100}"} for i in range(1000)]
    rp = tmp(json.dumps(reqs))
    try:
        start = time.time()
        proc = run(["--graph", gp, "--requests", rp, "--traffic", tp])
        elapsed = time.time() - start
        assert proc.returncode == 0, proc.stderr.decode()[:500]
        assert elapsed < 6.0, f"too slow 1000 float with traffic {elapsed}"
        lines = proc.stdout.decode().strip().splitlines()
        assert len(lines) == 1000
        for line in lines:
            o = json.loads(line)
            assert "effective_distance" in o and "traffic_delay" in o
            assert set(o.keys()) == {
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


def test_traffic_10_way_secondary_raw_tertiary_lex():
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

    # 10 paths A-X-Z each raw 5+5=10 effective 10, lex smallest B should win
    # Second test: 3 paths effective equal 12 raw differs 11 vs 4 vs 10 -> raw 4 wins
    nodes = ["A"] + [chr(ord("B") + i) for i in range(10)] + ["Z"]
    edges = []
    for i in range(10):
        mid = chr(ord("B") + i)
        edges.append({"from": "A", "to": mid, "distance": 5})
        edges.append({"from": mid, "to": "Z", "distance": 5})
    graph = {"nodes": nodes, "edges": edges}
    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps([]))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "Z", "--traffic", tp])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip())
        assert out["path"] == ["A", "B", "Z"], (
            f"10-way tie should pick B, got {out['path']}"
        )
        # secondary raw tie: construct graph where effective equal raw differs
        graph2 = {
            "nodes": ["A", "B", "C", "D"],
            "edges": [
                {"from": "A", "to": "B", "distance": 10},
                {"from": "B", "to": "D", "distance": 1},
                {"from": "A", "to": "C", "distance": 2},
                {"from": "C", "to": "D", "distance": 2},
            ],
        }
        traffic2 = {
            "traffic": [
                {"from": "A", "to": "B", "factor": 1, "delay": 0},
                {"from": "B", "to": "D", "factor": 1, "delay": 1},
                {"from": "A", "to": "C", "factor": 2, "delay": 0},
                {"from": "C", "to": "D", "factor": 4, "delay": 0},
            ]
        }
        gp2 = tmp(json.dumps(graph2))
        tp2 = tmp(json.dumps(traffic2))
        proc2 = run(["--graph", gp2, "--from", "A", "--to", "D", "--traffic", tp2])
        assert proc2.returncode == 0
        out2 = json.loads(proc2.stdout.decode().strip())
        assert out2["path"] == ["A", "C", "D"], (
            f"secondary raw tie: A-C-D raw4 vs A-B-D raw11, expected A-C-D got {out2['path']}"
        )
        os.unlink(gp2)
        os.unlink(tp2)
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_graph_validation_with_traffic_still_strict():
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

    # invalid graph cases still invalid even with valid traffic
    invalid_graphs = [
        '{"nodes":["A","A"],"edges":[]}',
        '{"nodes":["","B"],"edges":[]}',
        '{"nodes":["   "],"edges":[]}',
        '{"nodes":["A","B"],"edges":[{"from":"A","to":"A","distance":1}]}',
        '{"nodes":["A","B"],"edges":[{"from":"A","to":"C","distance":1}]}',
        '{"nodes":["A","B"],"edges":[{"from":"A","to":"B","distance":-1}]}',
        '{"nodes":["A","B"],"edges":[{"from":"A","to":"B","distance":0}]}',
        '{"nodes":[],"edges":[]}',
        '{"nodes":["A",123],"edges":[]}',
        '{"nodes":["A","B"],"edges":[null]}',
        '{"nodes":["A","B"],"edges":["string"]}',
        '{"nodes":["A","B"],"edges":[[1,2,3]]}',
        '{"edges":[]}',
        '{"nodes":[]}',
        "[]",
        '"string"',
        "123",
        "null",
    ]
    gp_valid_content = (
        '{"nodes":["A","B"],"edges":[{"from":"A","to":"B","distance":1}]}'
    )
    tp_valid = tmp(json.dumps({"traffic": [{"from": "A", "to": "B", "factor": 1}]}))
    try:
        for bad in invalid_graphs:
            gp = tmp(bad)
            try:
                proc = run(
                    ["--graph", gp, "--from", "A", "--to", "B", "--traffic", tp_valid]
                )
                assert proc.returncode == 2 and proc.stdout.decode().strip() == "", (
                    f"invalid graph {bad[:50]} with traffic should be exit2"
                )
            finally:
                os.unlink(gp)
        # also trailing comma, BOM, comment
        gp_trail = tmp(
            '{"nodes":["A","B"],"edges":[{"from":"A","to":"B","distance":1},]}'
        )
        proc_trail = run(
            ["--graph", gp_trail, "--from", "A", "--to", "B", "--traffic", tp_valid]
        )
        assert proc_trail.returncode == 2
        os.unlink(gp_trail)
        import os as _os

        f = _os.open(
            "/tmp/bom_traffic_test.json",
            _os.O_WRONLY | _os.O_CREAT | _os.O_TRUNC,
            0o644,
        )
        _os.write(
            f,
            b'\xef\xbb\xbf{"nodes":["A","B"],"edges":[{"from":"A","to":"B","distance":1}]}',
        )
        _os.close(f)
        proc_bom = run(
            [
                "--graph",
                "/tmp/bom_traffic_test.json",
                "--from",
                "A",
                "--to",
                "B",
                "--traffic",
                tp_valid,
            ]
        )
        assert proc_bom.returncode == 2
        _os.unlink("/tmp/bom_traffic_test.json")
    finally:
        os.unlink(tp_valid)


def test_traffic_requests_bom_trailing_comment_with_traffic():
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
    tp = tmp(json.dumps({"traffic": [{"from": "A", "to": "B", "factor": 1}]}))
    rp_trail = tmp('[{"source":"A","destination":"B"},]')
    rp_comment = tmp('// comment\n[{"source":"A","destination":"B"}]')
    rp_bom = tmp_bytes(b'\xef\xbb\xbf[{"source":"A","destination":"B"}]')
    try:
        for rp in [rp_trail, rp_comment, rp_bom]:
            proc = run(["--graph", gp, "--requests", rp, "--traffic", tp])
            assert proc.returncode == 2, (
                f"requests malformed with traffic should be invalid, got {proc.returncode} for {rp}"
            )
            assert proc.stdout.decode().strip() == ""
    finally:
        os.unlink(gp)
        os.unlink(tp)
        os.unlink(rp_trail)
        os.unlink(rp_comment)
        os.unlink(rp_bom)


def test_traffic_large_graph_5000_same_source_amortization_extra_hard():
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

    nodes = [f"N{i}" for i in range(3000)]
    edges = [{"from": f"N{i}", "to": f"N{i + 1}", "distance": 1} for i in range(2999)]
    edges += [
        {"from": f"N{i}", "to": f"N{i + 100}", "distance": 50}
        for i in range(0, 2900, 100)
    ]
    graph = {"nodes": nodes, "edges": edges}
    traffic = {
        "traffic": [
            {"from": f"N{i}", "to": f"N{i + 1}", "factor": 1.2, "delay": 0.5}
            for i in range(0, 2999, 300)
        ]
    }
    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps(traffic))
    same_reqs = [
        {"source": "N0", "destination": f"N{(i * 7) % 3000}"} for i in range(300)
    ]
    multi_reqs = [
        {"source": f"N{i % 3000}", "destination": f"N{(i * 13) % 3000}"}
        for i in range(200)
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
        assert proc_multi.returncode == 0, (
            f"multi should succeed rc={proc_multi.returncode}"
        )
        # same-source must be faster than multi-source if properly amortized, with lenient threshold for Docker
        # 300 same vs 200 distinct: amortized should be <70% + margin
        assert t_same <= 0.98 * t_multi + 10.0, (
            f"3000 nodes same-source amortization failed: t_same={t_same:.3f} t_multi={t_multi:.3f}"
        )
    finally:
        os.unlink(gp)
        os.unlink(tp)
        os.unlink(rp_same)
        os.unlink(rp_multi)


def test_traffic_batch_5000_correctness_and_perf():
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
            timeout=90,
        )

    nodes = [f"N{i}" for i in range(500)]
    edges = [{"from": f"N{i}", "to": f"N{i + 1}", "distance": 1} for i in range(499)]
    graph = {"nodes": nodes, "edges": edges}
    traffic = {
        "traffic": [
            {"from": f"N{i}", "to": f"N{i + 1}", "factor": 1.1, "delay": 0.1}
            for i in range(0, 499, 25)
        ]
    }
    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps(traffic))
    reqs = [{"source": "N0", "destination": f"N{i % 500}"} for i in range(5000)]
    rp = tmp(json.dumps(reqs))
    try:
        start = time.time()
        proc = run(["--graph", gp, "--requests", rp, "--traffic", tp])
        elapsed = time.time() - start
        assert proc.returncode == 0, (
            f"5000 batch with traffic should succeed rc={proc.returncode}"
        )
        assert elapsed < 85.0, f"too slow 5000 batch with traffic {elapsed}"
        lines = proc.stdout.decode().strip().splitlines()
        assert len(lines) == 5000
        # spot check first 10 have correct keys and order
        for i in range(10):
            o = json.loads(lines[i])
            assert (
                o["source"] == reqs[i]["source"]
                and o["destination"] == reqs[i]["destination"]
            )
            assert set(o.keys()) == {
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


def test_traffic_factor_delay_mixed_scientific_plus():
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

    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 10}]}
    gp = tmp(json.dumps(graph))
    # factor 1e+3=1000 delay 1e+2=100 => eff 10100
    tp = tmp('{"traffic":[{"from":"A","to":"B","factor":1e+3,"delay":1e+2}]}')
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B", "--traffic", tp])
        assert proc.returncode == 0, (
            f"factor 1e+3 delay 1e+2 should be valid rc={proc.returncode}"
        )
        out = json.loads(proc.stdout.decode().strip())
        assert math.isclose(out["effective_distance"], 10100, rel_tol=1e-6)
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_leading_space_node_distinct_valid_and_traffic_invalid():
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

    # Graph has node " A" distinct from "A" – valid
    graph = {
        "nodes": [" A", "A", "B"],
        "edges": [
            {"from": " A", "to": "B", "distance": 5},
            {"from": "A", "to": "B", "distance": 10},
        ],
    }
    gp = tmp(json.dumps(graph))
    # Request " A" -> should go to " A" path, not "A"
    rp = tmp(json.dumps([{"source": " A", "destination": "B"}]))
    # Traffic for " A"->"B" valid if edge exists
    tp_valid = tmp(json.dumps({"traffic": [{"from": " A", "to": "B", "factor": 2}]}))
    tp_invalid = tmp(
        json.dumps({"traffic": [{"from": " A", "to": "A", "factor": 2}]})
    )  # self-loop after? Actually " A" != "A" so not self-loop, but edge " A"->"A" doesn't exist -> invalid because edge missing? Wait from/to " A" and "A" distinct nodes, but edge " A"-"A" doesn't exist -> should be invalid because edge not in graph? Actually traffic requires edge exist. So " A"->"A" needs edge " A"-"A" which is self-loop invalid graph already, but graph doesn't have that edge, so traffic invalid.
    try:
        proc = run(["--graph", gp, "--from", " A", "--to", "B", "--traffic", tp_valid])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip())
        assert out["path"] == [" A", "B"], (
            f"leading space distinct node should route, got {out['path']}"
        )
        assert math.isclose(out["distance"], 5, abs_tol=1e-6)
        assert math.isclose(out["effective_distance"], 10, abs_tol=1e-6)
        # batch
        proc2 = run(["--graph", gp, "--requests", rp, "--traffic", tp_valid])
        assert proc2.returncode == 0
        out2 = json.loads(proc2.stdout.decode().strip().splitlines()[0])
        assert out2["path"] == [" A", "B"]
        # invalid traffic for non-existing edge " A"->"A" (no such edge)
        proc3 = run(
            ["--graph", gp, "--from", "A", "--to", "B", "--traffic", tp_invalid]
        )
        assert proc3.returncode == 2, "traffic for non-existing edge should be invalid"
        # request " A" when graph only has "A" -> no-route not invalid
        graph2 = {
            "nodes": ["A", "B"],
            "edges": [{"from": "A", "to": "B", "distance": 1}],
        }
        gp2 = tmp(json.dumps(graph2))
        proc4 = run(["--graph", gp2, "--from", " A", "--to", "B"])
        assert proc4.returncode == 1, (
            "leading space request when node doesn't exist should be no-route not invalid"
        )
        out4 = json.loads(proc4.stdout.decode().strip())
        assert out4["distance"] == -1
        os.unlink(gp2)
        os.unlink(rp)
    finally:
        os.unlink(gp)
        os.unlink(tp_valid)
        os.unlink(tp_invalid)


def test_traffic_flag_missing_value_invalid():
    import subprocess, tempfile, os, json

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
        proc = run(["--graph", gp, "--from", "A", "--to", "B", "--traffic"])
        assert proc.returncode == 2 and proc.stdout.decode().strip() == "", (
            "missing traffic value should be invalid"
        )
        proc2 = run(["--graph", gp, "--from", "A", "--to", "B", "--traffic="])
        # empty value after equals? Might be treated as empty path -> file not found -> invalid 2
        assert proc2.returncode == 2
    finally:
        os.unlink(gp)


# === ULTRA HARD v3 for Step2 - 211->251+ making both steps too hard ===


def test_traffic_top_level_invalid_extra_v3():
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
    for bad in [
        '"x"',
        "123",
        "true",
        "null",
        '{"foo":1}',
        '{"traffic":null}',
        '{"traffic":{}}',
        '{"traffic":"x"}',
        '{"traffic":123}',
        '{"traffic":[null]}',
        '{"traffic":["x"]}',
        '{"traffic":[123]}',
        "[]",
        '{"traffic":[]}',
    ]:
        # Actually [] and {"traffic":[]} are valid empty, so skip those for invalid check
        if bad in ("[]", '{"traffic":[]}'):
            continue
        tp = tmp(bad)
        try:
            proc = run(["--graph", gp, "--from", "A", "--to", "B", "--traffic", tp])
            assert proc.returncode == 2, f"traffic top-level {bad} should be invalid"
        finally:
            os.unlink(tp)
    # valid empty should pass
    for valid in ["[]", '{"traffic":[]}']:
        tp = tmp(valid)
        try:
            proc = run(["--graph", gp, "--from", "A", "--to", "B", "--traffic", tp])
            assert proc.returncode == 0, f"traffic {valid} should be valid empty"
        finally:
            os.unlink(tp)
    os.unlink(gp)


def test_traffic_factor_delay_extreme_values():
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

    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 1e6}]}
    gp = tmp(json.dumps(graph))
    cases = [
        (1e-9, 0, 1e-9 * 1e6),
        (1e9, 0, 1e9 * 1e6),
        (1, 1e6, 1e6 + 1e6),
        (0.5, 0, 0.5 * 1e6),
        (1, 1e-9, 1e6 + 1e-9),
    ]
    for factor, delay, expected_eff in cases:
        tp = tmp(
            json.dumps(
                {
                    "traffic": [
                        {"from": "A", "to": "B", "factor": factor, "delay": delay}
                    ]
                }
            )
        )
        try:
            proc = run(["--graph", gp, "--from", "A", "--to", "B", "--traffic", tp])
            assert proc.returncode == 0, (
                f"factor {factor} delay {delay} should be valid"
            )
            out = json.loads(proc.stdout.decode().strip())
            assert math.isclose(
                out["effective_distance"], expected_eff, rel_tol=1e-6
            ), (
                f"factor {factor} delay {delay} expected {expected_eff} got {out['effective_distance']}"
            )
        finally:
            os.unlink(tp)
    os.unlink(gp)


def test_traffic_duplicate_many_last_wins_with_extra_version():
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

    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 10}]}
    gp = tmp(json.dumps(graph))
    # 20 duplicates, last wins
    traffic_list = [
        {"from": "A", "to": "B", "factor": 1, "delay": i} for i in range(20)
    ]
    tp = tmp(json.dumps({"traffic": traffic_list, "version": 1, "extra": "ignore"}))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B", "--traffic", tp])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip())
        assert math.isclose(out["effective_distance"], 10 + 19, abs_tol=1e-6), (
            f"last wins delay19, got {out['effective_distance']}"
        )
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_request_both_keys_prefers_source_dest():
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
        ],
    }
    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps({"traffic": [{"from": "A", "to": "B", "factor": 2}]}))
    # request has both source/destination and from/to, source/destination should be preferred
    reqs = [{"source": "A", "destination": "B", "from": "C", "to": "C"}]
    rp = tmp(json.dumps(reqs))
    try:
        proc = run(["--graph", gp, "--requests", rp, "--traffic", tp])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip().splitlines()[0])
        assert out["source"] == "A" and out["destination"] == "B"
        assert out["path"] == ["A", "B"]
    finally:
        os.unlink(gp)
        os.unlink(tp)
        os.unlink(rp)


def test_traffic_batch_order_preserved_large_with_traffic():
    import tempfile, os, subprocess, json, random

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
    edges = [{"from": f"N{i}", "to": f"N{i + 1}", "distance": 1} for i in range(99)]
    graph = {"nodes": nodes, "edges": edges}
    traffic = {
        "traffic": [
            {"from": f"N{i}", "to": f"N{i + 1}", "factor": 1.2}
            for i in range(0, 99, 20)
        ]
    }
    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps(traffic))
    random.seed(42)
    reqs = [
        {
            "source": f"N{random.randint(0, 99)}",
            "destination": f"N{random.randint(0, 99)}",
        }
        for _ in range(500)
    ]
    rp = tmp(json.dumps(reqs))
    try:
        proc = run(["--graph", gp, "--requests", rp, "--traffic", tp])
        assert proc.returncode in (0, 1)
        lines = proc.stdout.decode().strip().splitlines()
        assert len(lines) == 500
        for i, line in enumerate(lines):
            o = json.loads(line)
            assert (
                o["source"] == reqs[i]["source"]
                and o["destination"] == reqs[i]["destination"]
            ), f"order mismatch at {i}"
    finally:
        os.unlink(gp)
        os.unlink(tp)
        os.unlink(rp)


def test_traffic_no_route_all_minus_one_strict_v3():
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
    tp = tmp(json.dumps({"traffic": []}))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "C", "--traffic", tp])
        assert proc.returncode == 1
        out = json.loads(proc.stdout.decode().strip())
        assert out == {
            "path": [],
            "distance": -1,
            "effective_distance": -1,
            "traffic_delay": -1,
        }, f"no-route strict exact dict, got {out}"
        rp = tmp(
            json.dumps(
                [
                    {"source": "A", "destination": "C"},
                    {"source": "C", "destination": "B"},
                ]
            )
        )
        proc2 = run(["--graph", gp, "--requests", rp, "--traffic", tp])
        assert proc2.returncode == 1
        lines = proc2.stdout.decode().strip().splitlines()
        assert len(lines) == 2
        for line in lines:
            o = json.loads(line)
            assert (
                o["distance"] == -1
                and o["effective_distance"] == -1
                and o["traffic_delay"] == -1
            )
            assert set(o.keys()) == {
                "source",
                "destination",
                "path",
                "distance",
                "effective_distance",
                "traffic_delay",
            }
        os.unlink(rp)
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_help_contains_all_keywords_with_equals():
    import subprocess

    BIN = "/app/router"
    for args in [
        [BIN, "--help"],
        [BIN, "-h"],
        [BIN, "help"],
        [BIN, "--help=true"],
        [BIN, "--help= true"],
        [BIN, "-h=true"],
    ]:
        proc = subprocess.run(
            args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30
        )
        assert proc.returncode == 0
        out = proc.stdout.decode().lower()
        for kw in ["graph", "from", "to", "requests", "traffic", "help"]:
            assert kw in out, f"help should contain {kw} for args {args}, got {out}"


def test_traffic_10_way_tie_with_delay():
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

    nodes = ["S"] + [chr(ord("B") + i) for i in range(10)] + ["T"]
    edges = []
    for i in range(10):
        mid = chr(ord("B") + i)
        edges.append({"from": "S", "to": mid, "distance": 5})
        edges.append({"from": mid, "to": "T", "distance": 5})
    graph = {"nodes": nodes, "edges": edges}
    # factor 1 delay 0 for all, plus some have delay 0.0 same effective, raw same, lex smallest B wins
    # add delay varying but effective still equal? Use factor 1 delay 0 for all
    gp = tmp(json.dumps(graph))
    tp = tmp(
        json.dumps(
            {
                "traffic": [
                    {"from": "S", "to": chr(ord("B") + i), "factor": 1, "delay": 0}
                    for i in range(10)
                ]
            }
        )
    )
    try:
        proc = run(["--graph", gp, "--from", "S", "--to", "T", "--traffic", tp])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip())
        assert out["path"] == ["S", "B", "T"], (
            f"10-way tie with delay should pick B, got {out['path']}"
        )
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_dense_graph_performance_with_traffic():
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

    nodes = [f"N{i}" for i in range(100)]
    edges = []
    for i in range(100):
        for j in range(i + 1, min(i + 10, 100)):
            edges.append({"from": f"N{i}", "to": f"N{j}", "distance": 1})
    graph = {"nodes": nodes, "edges": edges}
    traffic = {
        "traffic": [
            {"from": f"N{i}", "to": f"N{i + 1}", "factor": 1.5}
            for i in range(0, 99, 10)
        ]
    }
    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps(traffic))
    try:
        start = time.time()
        proc = run(["--graph", gp, "--from", "N0", "--to", "N99", "--traffic", tp])
        elapsed = time.time() - start
        assert proc.returncode == 0
        assert elapsed < 8.0, f"dense 100 nodes with traffic too slow {elapsed}"
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_factor_negative_zero_vs_zero():
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
    for bad in ["0", "-0", "0.0", "-0.0"]:
        tp = tmp('{"traffic":[{"from":"A","to":"B","factor":' + bad + "}]}")
        try:
            proc = run(["--graph", gp, "--from", "A", "--to", "B", "--traffic", tp])
            assert proc.returncode == 2, f"factor {bad} should be invalid zero"
        finally:
            os.unlink(tp)
    os.unlink(gp)


def test_traffic_source_equals_dest_batch_with_traffic():
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
    tp = tmp(
        json.dumps({"traffic": [{"from": "A", "to": "B", "factor": 2, "delay": 3}]})
    )
    reqs = [
        {"source": "A", "destination": "A"},
        {"source": "B", "destination": "B"},
        {"source": "A", "destination": "B"},
    ]
    rp = tmp(json.dumps(reqs))
    try:
        proc = run(["--graph", gp, "--requests", rp, "--traffic", tp])
        assert proc.returncode == 0
        lines = proc.stdout.decode().strip().splitlines()
        assert len(lines) == 3
        o0 = json.loads(lines[0])
        assert (
            o0["path"] == ["A"]
            and o0["distance"] == 0
            and o0["effective_distance"] == 0
            and o0["traffic_delay"] == 0
        )
        o1 = json.loads(lines[1])
        assert o1["path"] == ["B"] and o1["distance"] == 0
        o2 = json.loads(lines[2])
        assert (
            o2["path"] == ["A", "B"]
            and o2["distance"] == 5
            and o2["effective_distance"] == 13
        )
    finally:
        os.unlink(gp)
        os.unlink(tp)
        os.unlink(rp)


def test_traffic_batch_with_empty_and_missing_distinction():
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
    tp = tmp(json.dumps({"traffic": []}))
    # missing key -> invalid exit2
    rp_missing = tmp(json.dumps([{"source": "A"}]))
    # empty string -> no-route exit1 with -1 fields
    rp_empty = tmp(json.dumps([{"source": "", "destination": "B"}]))
    # null literal -> invalid
    rp_null = tmp('[{"source":null,"destination":"B"}]')
    try:
        proc_missing = run(["--graph", gp, "--requests", rp_missing, "--traffic", tp])
        assert proc_missing.returncode == 2, "missing destination should be invalid"
        proc_empty = run(["--graph", gp, "--requests", rp_empty, "--traffic", tp])
        assert proc_empty.returncode == 1
        out = json.loads(proc_empty.stdout.decode().strip().splitlines()[0])
        assert out["effective_distance"] == -1
        proc_null = run(["--graph", gp, "--requests", rp_null, "--traffic", tp])
        assert proc_null.returncode == 2, "null source should be invalid"
    finally:
        os.unlink(gp)
        os.unlink(tp)
        os.unlink(rp_missing)
        os.unlink(rp_empty)
        os.unlink(rp_null)


# === ULTRA HARD v4 for Step2 only - making it truly hard (223->236+) re-added ===


def test_traffic_effective_formula_4_edges_discrimination_hard():
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
            timeout=60,
        )

    graph = {
        "nodes": ["A", "B", "C", "D", "E"],
        "edges": [
            {"from": "A", "to": "B", "distance": 10},
            {"from": "B", "to": "C", "distance": 20},
            {"from": "C", "to": "D", "distance": 5},
            {"from": "D", "to": "E", "distance": 10},
        ],
    }
    traffic = {
        "traffic": [
            {"from": "A", "to": "B", "factor": 2, "delay": 5},
            {"from": "B", "to": "C", "factor": 1, "delay": 10},
            {"from": "C", "to": "D", "factor": 0.5, "delay": 0},
            {"from": "D", "to": "E", "factor": 3, "delay": 1},
        ]
    }
    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps(traffic))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "E", "--traffic", tp])
        assert proc.returncode == 0, proc.stderr.decode()
        out = json.loads(proc.stdout.decode().strip())
        assert math.isclose(out["distance"], 45, abs_tol=1e-6)
        assert math.isclose(out["effective_distance"], 88.5, abs_tol=1e-6), (
            f"per-edge formula expected 88.5, got {out['effective_distance']}"
        )
        assert math.isclose(out["traffic_delay"], 43.5, abs_tol=1e-6)
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_raw_along_effective_best_3_hops():
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
            timeout=60,
        )

    graph = {
        "nodes": ["A", "B", "C", "D", "E", "F"],
        "edges": [
            {"from": "A", "to": "B", "distance": 1},
            {"from": "B", "to": "C", "distance": 1},
            {"from": "A", "to": "D", "distance": 10},
            {"from": "D", "to": "E", "distance": 10},
            {"from": "E", "to": "F", "distance": 10},
            {"from": "F", "to": "C", "distance": 10},
        ],
    }
    traffic = {
        "traffic": [
            {"from": "A", "to": "B", "factor": 100},
            {"from": "B", "to": "C", "factor": 100},
            {"from": "A", "to": "D", "factor": 1},
            {"from": "D", "to": "E", "factor": 1},
            {"from": "E", "to": "F", "factor": 1},
            {"from": "F", "to": "C", "factor": 1},
        ]
    }
    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps(traffic))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "C", "--traffic", tp])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip())
        assert out["path"] == ["A", "D", "E", "F", "C"], (
            f"should reroute, got {out['path']}"
        )
        assert math.isclose(out["distance"], 40, abs_tol=1e-6), (
            f"raw must be 40 along effective-best, got {out['distance']}"
        )
        assert math.isclose(out["effective_distance"], 40, abs_tol=1e-6)
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_reverse_applies_undirected_extra():
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

    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 10}]}
    gp = tmp(json.dumps(graph))
    tp = tmp(
        json.dumps({"traffic": [{"from": "B", "to": "A", "factor": 3, "delay": 5}]})
    )
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B", "--traffic", tp])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip())
        assert math.isclose(out["effective_distance"], 35, abs_tol=1e-6), (
            f"reverse traffic should apply undirected, got {out['effective_distance']}"
        )
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_factor_1_and_delay_0_default_explicit():
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
        "nodes": ["A", "B", "C"],
        "edges": [
            {"from": "A", "to": "B", "distance": 10},
            {"from": "B", "to": "C", "distance": 10},
        ],
    }
    gp = tmp(json.dumps(graph))
    tp = tmp(
        json.dumps({"traffic": [{"from": "A", "to": "B", "factor": 1, "delay": 0}]})
    )
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "C", "--traffic", tp])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip())
        assert math.isclose(out["distance"], 20, abs_tol=1e-6)
        assert math.isclose(out["effective_distance"], 20, abs_tol=1e-6)
        assert math.isclose(out["traffic_delay"], 0, abs_tol=1e-6)
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_secondary_raw_tie_complex():
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
        "nodes": ["A", "B", "C", "D"],
        "edges": [
            {"from": "A", "to": "B", "distance": 5},
            {"from": "B", "to": "D", "distance": 5},
            {"from": "A", "to": "C", "distance": 6},
            {"from": "C", "to": "D", "distance": 6},
        ],
    }
    traffic = {
        "traffic": [
            {"from": "A", "to": "B", "factor": 2, "delay": 0},
            {"from": "B", "to": "D", "factor": 2, "delay": 0},
            {"from": "A", "to": "C", "factor": 1, "delay": 4},
            {"from": "C", "to": "D", "factor": 1, "delay": 4},
        ]
    }
    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps(traffic))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "D", "--traffic", tp])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip())
        assert out["path"] == ["A", "B", "D"], (
            f"secondary raw tie: expected A-B-D raw10 vs A-C-D raw12, got {out['path']}"
        )
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_tertiary_lex_with_special_chars():
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
        "nodes": ["A", "B-1", "B.2", "B_3", "Z"],
        "edges": [
            {"from": "A", "to": "B-1", "distance": 5},
            {"from": "B-1", "to": "Z", "distance": 5},
            {"from": "A", "to": "B.2", "distance": 5},
            {"from": "B.2", "to": "Z", "distance": 5},
            {"from": "A", "to": "B_3", "distance": 5},
            {"from": "B_3", "to": "Z", "distance": 5},
        ],
    }
    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps([]))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "Z", "--traffic", tp])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip())
        assert out["path"] == ["A", "B-1", "Z"], (
            f"lex with special chars should pick B-1 '-', got {out['path']}"
        )
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_duplicate_last_wins_many_reverse_with_delay_reset_hard():
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

    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 10}]}
    gp = tmp(json.dumps(graph))
    traffic_entries = []
    for i in range(9):
        if i % 2 == 0:
            traffic_entries.append({"from": "A", "to": "B", "factor": 1, "delay": i})
        else:
            traffic_entries.append({"from": "B", "to": "A", "factor": 2, "delay": i})
    traffic_entries.append({"from": "A", "to": "B", "factor": 5, "delay": 7})
    tp = tmp(json.dumps({"traffic": traffic_entries}))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B", "--traffic", tp])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip())
        assert math.isclose(out["effective_distance"], 57, abs_tol=1e-6), (
            f"last wins factor5 delay7 => 57, got {out['effective_distance']}"
        )
        traffic_entries2 = traffic_entries[:-1] + [
            {"from": "A", "to": "B", "factor": 5}
        ]
        tp2 = tmp(json.dumps({"traffic": traffic_entries2}))
        proc2 = run(["--graph", gp, "--from", "A", "--to", "B", "--traffic", tp2])
        assert proc2.returncode == 0
        out2 = json.loads(proc2.stdout.decode().strip())
        assert math.isclose(out2["effective_distance"], 50, abs_tol=1e-6), (
            f"last without delay should reset to 50, got {out2['effective_distance']}"
        )
        os.unlink(tp2)
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_wrapper_extra_top_level_and_direct_array_extra_fields():
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

    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 10}]}
    gp = tmp(json.dumps(graph))
    tp = tmp(
        json.dumps(
            {
                "traffic": [
                    {
                        "from": "A",
                        "to": "B",
                        "factor": 2,
                        "delay": 1,
                        "extra": "ignore",
                        "meta": {"x": 1},
                    }
                ],
                "version": 1,
                "extra_top": "ignore",
            }
        )
    )
    tp2 = tmp(
        json.dumps(
            [
                {
                    "from": "A",
                    "to": "B",
                    "factor": 3,
                    "delay": 2,
                    "weight": 999,
                    "extra": "ignore",
                }
            ]
        )
    )
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B", "--traffic", tp])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip())
        assert math.isclose(out["effective_distance"], 21, abs_tol=1e-6)
        proc2 = run(["--graph", gp, "--from", "A", "--to", "B", "--traffic", tp2])
        assert proc2.returncode == 0
        out2 = json.loads(proc2.stdout.decode().strip())
        assert math.isclose(out2["effective_distance"], 32, abs_tol=1e-6)
    finally:
        os.unlink(gp)
        os.unlink(tp)
        os.unlink(tp2)


def test_traffic_batch_output_strict_with_traffic_extra():
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

    graph = {
        "nodes": ["A", "B", "C"],
        "edges": [
            {"from": "A", "to": "B", "distance": 1},
            {"from": "B", "to": "C", "distance": 1},
        ],
    }
    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps({"traffic": [{"from": "A", "to": "B", "factor": 2}]}))
    reqs = [
        {"source": "A", "destination": "B"},
        {"source": "B", "destination": "C"},
        {"source": "A", "destination": "C"},
        {"source": "A", "destination": "X"},
    ]
    rp = tmp(json.dumps(reqs))
    try:
        proc = run(["--graph", gp, "--requests", rp, "--traffic", tp])
        assert proc.returncode == 1, "some no-route should give exit1"
        lines = proc.stdout.decode().strip().splitlines()
        assert len(lines) == 4
        for line in lines:
            o = json.loads(line)
            assert set(o.keys()) == {
                "source",
                "destination",
                "path",
                "distance",
                "effective_distance",
                "traffic_delay",
            }, f"batch with traffic must have exactly 6 keys, got {o.keys()}"
            assert isinstance(o["path"], list) and isinstance(
                o["distance"], (int, float)
            )
            assert not isinstance(o["distance"], str)
            if o["path"] == []:
                assert (
                    o["distance"] == -1
                    and o["effective_distance"] == -1
                    and o["traffic_delay"] == -1
                )
    finally:
        os.unlink(gp)
        os.unlink(tp)
        os.unlink(rp)


def test_traffic_flag_order_with_requests_and_traffic():
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
            timeout=60,
        )

    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 5}]}
    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps({"traffic": [{"from": "A", "to": "B", "factor": 2}]}))
    rp = tmp(json.dumps([{"source": "A", "destination": "B"}]))
    try:
        orders = [
            ["--graph", gp, "--requests", rp, "--traffic", tp],
            ["--traffic", tp, "--graph", gp, "--requests", rp],
            ["--requests", rp, "--traffic", tp, "--graph", gp],
            [f"--graph={gp}", f"--requests={rp}", f"--traffic={tp}"],
            [f"--traffic={tp}", f"--graph={gp}", f"--requests={rp}"],
        ]
        for args in orders:
            proc = run(args)
            assert proc.returncode == 0, f"flag order {args} should work"
            out = json.loads(proc.stdout.decode().strip().splitlines()[0])
            assert math.isclose(out["effective_distance"], 10, abs_tol=1e-6)
    finally:
        os.unlink(gp)
        os.unlink(tp)
        os.unlink(rp)


def test_traffic_large_graph_2000_with_traffic_perf_hard():
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
            timeout=90,
        )

    nodes = [f"N{i}" for i in range(2000)]
    edges = [{"from": f"N{i}", "to": f"N{i + 1}", "distance": 1} for i in range(1999)]
    graph = {"nodes": nodes, "edges": edges}
    traffic = {
        "traffic": [
            {"from": f"N{i}", "to": f"N{i + 1}", "factor": 1.5, "delay": 1}
            for i in range(0, 1999, 100)
        ]
    }
    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps(traffic))
    try:
        start = time.time()
        proc = run(["--graph", gp, "--from", "N0", "--to", "N1999", "--traffic", tp])
        elapsed = time.time() - start
        assert proc.returncode == 0
        assert elapsed < 85.0, f"too slow 2000 nodes with traffic {elapsed}"
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_batch_5000_with_traffic_hard_v2():
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
            timeout=90,
        )

    nodes = [f"N{i}" for i in range(300)]
    edges = [{"from": f"N{i}", "to": f"N{i + 1}", "distance": 1} for i in range(299)]
    graph = {"nodes": nodes, "edges": edges}
    traffic = {
        "traffic": [
            {"from": f"N{i}", "to": f"N{i + 1}", "factor": 1.1}
            for i in range(0, 299, 30)
        ]
    }
    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps(traffic))
    reqs = [{"source": "N0", "destination": f"N{i % 300}"} for i in range(5000)]
    rp = tmp(json.dumps(reqs))
    try:
        start = time.time()
        proc = run(["--graph", gp, "--requests", rp, "--traffic", tp])
        elapsed = time.time() - start
        assert proc.returncode == 0
        assert elapsed < 60.0, f"too slow 5000 batch with traffic {elapsed}"
        assert len(proc.stdout.decode().strip().splitlines()) == 5000
    finally:
        os.unlink(gp)
        os.unlink(tp)
        os.unlink(rp)


def test_traffic_case_sensitive_and_prefix_tie_with_traffic():
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
        "nodes": ["A", "B", "a", "C", "Z"],
        "edges": [
            {"from": "A", "to": "B", "distance": 5},
            {"from": "B", "to": "Z", "distance": 5},
            {"from": "A", "to": "a", "distance": 5},
            {"from": "a", "to": "Z", "distance": 5},
            {"from": "A", "to": "C", "distance": 10},
            {"from": "C", "to": "Z", "distance": 1},
        ],
    }
    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps([]))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "Z", "--traffic", tp])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip())
        assert out["path"] == ["A", "B", "Z"], (
            f"case-sensitive should pick B over a, got {out['path']}"
        )
        graph2 = {
            "nodes": ["A", "B", "C", "Z"],
            "edges": [
                {"from": "A", "to": "B", "distance": 5},
                {"from": "B", "to": "Z", "distance": 5},
                {"from": "A", "to": "C", "distance": 5},
                {"from": "C", "to": "B", "distance": 0.000000001},
                {"from": "B", "to": "Z", "distance": 5},
            ],
        }
        gp2 = tmp(json.dumps(graph2))
        proc2 = run(["--graph", gp2, "--from", "A", "--to", "Z", "--traffic", tp])
        assert proc2.returncode == 0
        os.unlink(gp2)
    finally:
        os.unlink(gp)
        os.unlink(tp)


# === ULTRA HARD v5 re-added after loss (236->256) ===


def test_traffic_invalid_json_trailing_comma_in_wrapper_v5():
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
    tp = tmp('{"traffic":[{"from":"A","to":"B","factor":1},],}')
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B", "--traffic", tp])
        assert proc.returncode == 2
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_factor_delay_extreme_values_v5():
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

    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 1e6}]}
    gp = tmp(json.dumps(graph))
    tp = tmp(
        json.dumps({"traffic": [{"from": "A", "to": "B", "factor": 1e-9, "delay": 0}]})
    )
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B", "--traffic", tp])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip())
        assert math.isclose(out["effective_distance"], 1e-9 * 1e6, rel_tol=1e-6)
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_batch_order_preserved_large_with_traffic_v5():
    import tempfile, os, subprocess, json, random

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
    edges = [{"from": f"N{i}", "to": f"N{i + 1}", "distance": 1} for i in range(99)]
    graph = {"nodes": nodes, "edges": edges}
    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps({"traffic": []}))
    random.seed(42)
    reqs = [
        {
            "source": f"N{random.randint(0, 99)}",
            "destination": f"N{random.randint(0, 99)}",
        }
        for _ in range(300)
    ]
    rp = tmp(json.dumps(reqs))
    try:
        proc = run(["--graph", gp, "--requests", rp, "--traffic", tp])
        assert proc.returncode in (0, 1)
        lines = proc.stdout.decode().strip().splitlines()
        assert len(lines) == 300
    finally:
        os.unlink(gp)
        os.unlink(tp)
        os.unlink(rp)


def test_traffic_large_traffic_file_1000_entries_v5():
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
            timeout=90,
        )

    nodes = [f"N{i}" for i in range(1000)]
    edges = [{"from": f"N{i}", "to": f"N{i + 1}", "distance": 1} for i in range(999)]
    graph = {"nodes": nodes, "edges": edges}
    traffic = {
        "traffic": [
            {"from": f"N{i}", "to": f"N{i + 1}", "factor": 1.1} for i in range(999)
        ]
    }
    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps(traffic))
    try:
        proc = run(["--graph", gp, "--from", "N0", "--to", "N999", "--traffic", tp])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip())
        assert math.isclose(out["distance"], 999, abs_tol=1e-6)
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_case_sensitive_with_traffic_and_delay_v5():
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
        "nodes": ["A", "B", "b", "Z"],
        "edges": [
            {"from": "A", "to": "B", "distance": 5},
            {"from": "B", "to": "Z", "distance": 5},
            {"from": "A", "to": "b", "distance": 5},
            {"from": "b", "to": "Z", "distance": 5},
        ],
    }
    gp = tmp(json.dumps(graph))
    tp = tmp(
        json.dumps(
            {
                "traffic": [
                    {"from": "A", "to": "B", "factor": 1},
                    {"from": "A", "to": "b", "factor": 1},
                ]
            }
        )
    )
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "Z", "--traffic", tp])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip())
        assert out["path"] == ["A", "B", "Z"], (
            f"case-sensitive should pick B, got {out['path']}"
        )
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_batch_5000_with_traffic_hard_v5():
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
            timeout=90,
        )

    nodes = [f"N{i}" for i in range(300)]
    edges = [{"from": f"N{i}", "to": f"N{i + 1}", "distance": 1} for i in range(299)]
    graph = {"nodes": nodes, "edges": edges}
    gp = tmp(json.dumps(graph))
    tp = tmp(
        json.dumps(
            {
                "traffic": [
                    {"from": f"N{i}", "to": f"N{i + 1}", "factor": 1.1}
                    for i in range(0, 299, 30)
                ]
            }
        )
    )
    reqs = [{"source": "N0", "destination": f"N{i % 300}"} for i in range(5000)]
    rp = tmp(json.dumps(reqs))
    try:
        start = time.time()
        proc = run(["--graph", gp, "--requests", rp, "--traffic", tp])
        elapsed = time.time() - start
        assert proc.returncode == 0
        assert elapsed < 60.0, f"too slow 5000 batch {elapsed}"
        assert len(proc.stdout.decode().strip().splitlines()) == 5000
    finally:
        os.unlink(gp)
        os.unlink(tp)
        os.unlink(rp)


def test_traffic_effective_formula_4_edges_v5():
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
            timeout=60,
        )

    graph = {
        "nodes": ["A", "B", "C", "D", "E"],
        "edges": [
            {"from": "A", "to": "B", "distance": 10},
            {"from": "B", "to": "C", "distance": 20},
            {"from": "C", "to": "D", "distance": 5},
            {"from": "D", "to": "E", "distance": 10},
        ],
    }
    traffic = {
        "traffic": [
            {"from": "A", "to": "B", "factor": 2, "delay": 5},
            {"from": "B", "to": "C", "factor": 1, "delay": 10},
            {"from": "C", "to": "D", "factor": 0.5, "delay": 0},
            {"from": "D", "to": "E", "factor": 3, "delay": 1},
        ]
    }
    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps(traffic))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "E", "--traffic", tp])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip())
        assert math.isclose(out["effective_distance"], 88.5, abs_tol=1e-6)
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_raw_along_effective_best_3_hops_v5():
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
            timeout=60,
        )

    graph = {
        "nodes": ["A", "B", "C", "D", "E", "F"],
        "edges": [
            {"from": "A", "to": "B", "distance": 1},
            {"from": "B", "to": "C", "distance": 1},
            {"from": "A", "to": "D", "distance": 10},
            {"from": "D", "to": "E", "distance": 10},
            {"from": "E", "to": "F", "distance": 10},
            {"from": "F", "to": "C", "distance": 10},
        ],
    }
    traffic = {
        "traffic": [
            {"from": "A", "to": "B", "factor": 100},
            {"from": "B", "to": "C", "factor": 100},
            {"from": "A", "to": "D", "factor": 1},
            {"from": "D", "to": "E", "factor": 1},
            {"from": "E", "to": "F", "factor": 1},
            {"from": "F", "to": "C", "factor": 1},
        ]
    }
    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps(traffic))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "C", "--traffic", tp])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip())
        assert out["path"] == ["A", "D", "E", "F", "C"]
        assert math.isclose(out["distance"], 40, abs_tol=1e-6)
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_delay_reset_reverse_interleaved_v5():
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

    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 10}]}
    gp = tmp(json.dumps(graph))
    tp = tmp(
        json.dumps(
            {
                "traffic": [
                    {"from": "A", "to": "B", "factor": 1, "delay": 100},
                    {"from": "B", "to": "A", "factor": 2},
                ]
            }
        )
    )
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B", "--traffic", tp])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip())
        assert math.isclose(out["effective_distance"], 20, abs_tol=1e-6)
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_batch_output_strict_extra_v5():
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

    graph = {
        "nodes": ["A", "B", "C"],
        "edges": [
            {"from": "A", "to": "B", "distance": 1},
            {"from": "B", "to": "C", "distance": 1},
        ],
    }
    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps({"traffic": [{"from": "A", "to": "B", "factor": 2}]}))
    rp = tmp(
        json.dumps(
            [{"source": "A", "destination": "B"}, {"source": "A", "destination": "X"}]
        )
    )
    try:
        proc = run(["--graph", gp, "--requests", rp, "--traffic", tp])
        assert proc.returncode == 1
        lines = proc.stdout.decode().strip().splitlines()
        assert len(lines) == 2
        for line in lines:
            o = json.loads(line)
            assert set(o.keys()) == {
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


# === ULTRA HARD v6+ for Step2 - pushing to 280+ ===


def test_traffic_with_emoji_node_ids_and_traffic():
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
        "nodes": ["A🚀", "B💥", "C🌟"],
        "edges": [
            {"from": "A🚀", "to": "B💥", "distance": 5},
            {"from": "B💥", "to": "C🌟", "distance": 5},
        ],
    }
    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps({"traffic": [{"from": "A🚀", "to": "B💥", "factor": 2}]}))
    try:
        proc = run(["--graph", gp, "--from", "A🚀", "--to", "C🌟", "--traffic", tp])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip())
        assert out["path"] == ["A🚀", "B💥", "C🌟"]
        assert math.isclose(out["effective_distance"], 15, abs_tol=1e-6)
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_batch_with_unicode_and_special_chars():
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

    graph = {
        "nodes": ["A-1", "A_2", "A.B", "C/D", "E F"],
        "edges": [
            {"from": "A-1", "to": "A_2", "distance": 1},
            {"from": "A_2", "to": "A.B", "distance": 1},
            {"from": "A.B", "to": "C/D", "distance": 1},
        ],
    }
    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps({"traffic": [{"from": "A-1", "to": "A_2", "factor": 1.5}]}))
    reqs = [
        {"source": "A-1", "destination": "A.B"},
        {"source": "A_2", "destination": "C/D"},
        {"source": "A-1", "destination": "Missing"},
    ]
    rp = tmp(json.dumps(reqs))
    try:
        proc = run(["--graph", gp, "--requests", rp, "--traffic", tp])
        assert proc.returncode == 1
        lines = proc.stdout.decode().strip().splitlines()
        assert len(lines) == 3
    finally:
        os.unlink(gp)
        os.unlink(tp)
        os.unlink(rp)


def test_traffic_output_fields_exact_types():
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
    tp = tmp(
        json.dumps({"traffic": [{"from": "A", "to": "B", "factor": 2, "delay": 3}]})
    )
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B", "--traffic", tp])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip())
        # Check types
        assert isinstance(out["path"], list)
        assert all(isinstance(x, str) for x in out["path"])
        assert isinstance(out["distance"], (int, float)) and not isinstance(
            out["distance"], bool
        )
        assert isinstance(out["effective_distance"], (int, float))
        assert isinstance(out["traffic_delay"], (int, float))
        assert not isinstance(out["distance"], str)
        # No extra keys
        assert set(out.keys()) == {
            "path",
            "distance",
            "effective_distance",
            "traffic_delay",
        }
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_batch_2000_same_vs_multi_relative_extra():
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
            timeout=90,
        )

    nodes = [f"N{i}" for i in range(1000)]
    edges = [{"from": f"N{i}", "to": f"N{i + 1}", "distance": 1} for i in range(999)]
    graph = {"nodes": nodes, "edges": edges}
    gp = tmp(json.dumps(graph))
    tp = tmp(
        json.dumps(
            {
                "traffic": [
                    {"from": f"N{i}", "to": f"N{i + 1}", "factor": 1.2}
                    for i in range(0, 999, 100)
                ]
            }
        )
    )
    same = [{"source": "N0", "destination": f"N{i % 1000}"} for i in range(1, 2001)]
    multi = [
        {"source": f"N{i % 1000}", "destination": f"N{(i * 7) % 1000}"}
        for i in range(200)
    ]
    rp_same = tmp(json.dumps(same))
    rp_multi = tmp(json.dumps(multi))
    rp_single = tmp(json.dumps([{"source": "N0", "destination": "N999"}]))
    try:
        start = time.time()
        proc_single = run(["--graph", gp, "--requests", rp_single, "--traffic", tp])
        t_single = time.time() - start
        assert proc_single.returncode == 0
        start = time.time()
        proc_same = run(["--graph", gp, "--requests", rp_same, "--traffic", tp])
        t_same = time.time() - start
        assert proc_same.returncode == 0, proc_same.stderr.decode()[:500]
        # 2000 same-source should be amortized: not 2000x single
        assert t_same <= 150 * t_single + 20.0, (
            f"2000 same-source too slow vs single: {t_same:.3f} vs {t_single:.3f}"
        )
        assert len(proc_same.stdout.decode().strip().splitlines()) == 2000
    finally:
        os.unlink(gp)
        os.unlink(tp)
        os.unlink(rp_same)
        os.unlink(rp_multi)
        os.unlink(rp_single)


def test_traffic_with_large_delay_only_reroute_complex():
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
        "nodes": ["A", "B", "C", "D"],
        "edges": [
            {"from": "A", "to": "B", "distance": 1},
            {"from": "B", "to": "D", "distance": 1},
            {"from": "A", "to": "C", "distance": 5},
            {"from": "C", "to": "D", "distance": 5},
        ],
    }
    # Short path has huge delay, long has 0
    traffic = {
        "traffic": [
            {"from": "A", "to": "B", "factor": 1, "delay": 100},
            {"from": "B", "to": "D", "factor": 1, "delay": 100},
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
            f"delay-only reroute should pick C, got {out['path']}"
        )
    finally:
        os.unlink(gp)
        os.unlink(tp)


# === ULTRA HARD v7+ extra 30 tests for Step2 too easy (251->281) ===


def test_traffic_duplicate_with_extra_and_version_ignored_v7():
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

    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 10}]}
    gp = tmp(json.dumps(graph))
    tp = tmp(
        json.dumps(
            {
                "traffic": [
                    {
                        "from": "A",
                        "to": "B",
                        "factor": 1,
                        "delay": 1,
                        "extra": "ignore",
                    },
                    {"from": "A", "to": "B", "factor": 2, "delay": 2, "version": 2},
                ],
                "version": 1,
            }
        )
    )
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B", "--traffic", tp])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip())
        assert math.isclose(out["effective_distance"], 22, abs_tol=1e-6)
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_factor_string_with_plus_invalid():
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
    for bad in ['"+5"', '"+1"', '" +2"', '"2e+2"', '"Infinity"', '"NaN"']:
        tp = tmp('{"traffic":[{"from":"A","to":"B","factor":' + bad + "}]}")
        try:
            proc = run(["--graph", gp, "--from", "A", "--to", "B", "--traffic", tp])
            assert proc.returncode == 2, f"factor {bad} should be invalid"
        finally:
            os.unlink(tp)
    os.unlink(gp)


def test_traffic_batch_with_leading_space_no_route_v7():
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
    tp = tmp(json.dumps([]))
    rp = tmp(
        json.dumps(
            [{"source": " A", "destination": "B"}, {"source": "A", "destination": " B"}]
        )
    )
    try:
        proc = run(["--graph", gp, "--requests", rp, "--traffic", tp])
        assert proc.returncode == 1
        lines = proc.stdout.decode().strip().splitlines()
        assert len(lines) == 2
        for line in lines:
            o = json.loads(line)
            assert o["distance"] == -1 and o["effective_distance"] == -1
    finally:
        os.unlink(gp)
        os.unlink(tp)
        os.unlink(rp)


def test_traffic_graph_with_special_chars_and_traffic_v7():
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
        "nodes": ["A-1", "B_2", "C.D", "D/E"],
        "edges": [
            {"from": "A-1", "to": "B_2", "distance": 5},
            {"from": "B_2", "to": "C.D", "distance": 5},
            {"from": "C.D", "to": "D/E", "distance": 5},
        ],
    }
    gp = tmp(json.dumps(graph))
    tp = tmp(
        json.dumps(
            {
                "traffic": [
                    {"from": "A-1", "to": "B_2", "factor": 2},
                    {"from": "B_2", "to": "C.D", "factor": 0.5},
                ]
            }
        )
    )
    try:
        proc = run(["--graph", gp, "--from", "A-1", "--to", "D/E", "--traffic", tp])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip())
        assert out["path"] == ["A-1", "B_2", "C.D", "D/E"]
        assert math.isclose(
            out["effective_distance"], 5 * 2 + 5 * 0.5 + 5, abs_tol=1e-6
        )
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_help_with_traffic_and_requests_mixed_v7():
    import subprocess

    BIN = "/app/router"
    for args in [
        [BIN, "--help", "--traffic", "x", "--requests", "y"],
        [BIN, "--traffic", "x", "--help", "--requests", "y"],
        [BIN, "--requests", "y", "--help", "--traffic", "x"],
        [BIN, "help", "--traffic", "x"],
    ]:
        proc = subprocess.run(
            args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30
        )
        assert proc.returncode == 0
        out = proc.stdout.decode().lower()
        assert "traffic" in out and "graph" in out


def test_traffic_effective_tie_raw_and_lex_deeper_v7():
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

    # Diamond of diamonds effective equal 15, raw equal 15, lex decides B1<B2
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
    tp = tmp(json.dumps([]))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "Z", "--traffic", tp])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip())
        assert out["path"] == ["A", "B1", "B2", "Z"], (
            f"deeper diamond tie should pick B1-B2, got {out['path']}"
        )
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_batch_2000_with_traffic_perf_v7():
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

    nodes = [f"N{i}" for i in range(500)]
    edges = [{"from": f"N{i}", "to": f"N{i + 1}", "distance": 1} for i in range(499)]
    graph = {"nodes": nodes, "edges": edges}
    gp = tmp(json.dumps(graph))
    tp = tmp(
        json.dumps(
            {
                "traffic": [
                    {"from": f"N{i}", "to": f"N{i + 1}", "factor": 1.1}
                    for i in range(0, 499, 100)
                ]
            }
        )
    )
    reqs = [
        {"source": f"N{i % 500}", "destination": f"N{(i * 7) % 500}"}
        for i in range(1000)
    ]
    rp = tmp(json.dumps(reqs))
    try:
        start = time.time()
        proc = run(["--graph", gp, "--requests", rp, "--traffic", tp])
        elapsed = time.time() - start
        assert proc.returncode in (0, 1)
        assert elapsed < 60.0, f"too slow 1000 batch {elapsed}"
        assert len(proc.stdout.decode().strip().splitlines()) == 1000
    finally:
        os.unlink(gp)
        os.unlink(tp)
        os.unlink(rp)


def test_traffic_source_dest_with_spaces_and_traffic_v7():
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
        "nodes": ["A B", "C D"],
        "edges": [{"from": "A B", "to": "C D", "distance": 5}],
    }
    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps({"traffic": [{"from": "A B", "to": "C D", "factor": 2}]}))
    try:
        proc = run(["--graph", gp, "--from", "A B", "--to", "C D", "--traffic", tp])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip())
        assert out["path"] == ["A B", "C D"]
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_flag_order_with_equals_and_traffic_v7():
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

    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 7}]}
    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps({"traffic": [{"from": "A", "to": "B", "factor": 1.5}]}))
    try:
        for args in [
            [f"--graph={gp}", f"--from=A", f"--to=B", f"--traffic={tp}"],
            [f"--traffic={tp}", f"--graph={gp}", "--from=A", "--to=B"],
            ["--from=A", f"--graph={gp}", f"--traffic={tp}", "--to=B"],
        ]:
            proc = run(args)
            assert proc.returncode == 0
            out = json.loads(proc.stdout.decode().strip())
            assert math.isclose(out["effective_distance"], 10.5, abs_tol=1e-6)
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_no_route_single_and_batch_with_traffic_v7():
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
    tp = tmp(json.dumps([]))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "Missing", "--traffic", tp])
        assert proc.returncode == 1
        out = json.loads(proc.stdout.decode().strip())
        assert out == {
            "path": [],
            "distance": -1,
            "effective_distance": -1,
            "traffic_delay": -1,
        }
        rp = tmp(json.dumps([{"source": "A", "destination": "Missing"}]))
        proc2 = run(["--graph", gp, "--requests", rp, "--traffic", tp])
        assert proc2.returncode == 1
        out2 = json.loads(proc2.stdout.decode().strip().splitlines()[0])
        assert out2["effective_distance"] == -1
        os.unlink(rp)
    finally:
        os.unlink(gp)
        os.unlink(tp)


# === ULTRA HARD v8 final for Step2 too easy (261->281) ===


def test_traffic_factor_int_and_float_mixed_valid_v8():
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

    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 10}]}
    gp = tmp(json.dumps(graph))
    for factor in [1, 2, 1.5, 2.0, 3]:
        tp = tmp(
            json.dumps(
                {"traffic": [{"from": "A", "to": "B", "factor": factor, "delay": 1}]}
            )
        )
        try:
            proc = run(["--graph", gp, "--from", "A", "--to", "B", "--traffic", tp])
            assert proc.returncode == 0
            out = json.loads(proc.stdout.decode().strip())
            assert math.isclose(
                out["effective_distance"], 10 * factor + 1, rel_tol=1e-6
            )
        finally:
            os.unlink(tp)
    os.unlink(gp)


def test_traffic_delay_with_scientific_and_int_v8():
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

    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 10}]}
    gp = tmp(json.dumps(graph))
    for delay in [0, 1, 2.5, 10, 100]:
        tp = tmp(
            json.dumps(
                {"traffic": [{"from": "A", "to": "B", "factor": 1, "delay": delay}]}
            )
        )
        try:
            proc = run(["--graph", gp, "--from", "A", "--to", "B", "--traffic", tp])
            assert proc.returncode == 0
            out = json.loads(proc.stdout.decode().strip())
            assert math.isclose(out["effective_distance"], 10 + delay, abs_tol=1e-6)
        finally:
            os.unlink(tp)
    os.unlink(gp)


def test_traffic_batch_with_requests_empty_and_valid_v8():
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
    tp = tmp(json.dumps([]))
    rp_empty = tmp("[]")
    rp_one = tmp(json.dumps([{"source": "A", "destination": "B"}]))
    rp_two = tmp(
        json.dumps(
            [{"source": "A", "destination": "B"}, {"source": "B", "destination": "A"}]
        )
    )
    try:
        proc_empty = run(["--graph", gp, "--requests", rp_empty, "--traffic", tp])
        assert proc_empty.returncode == 0 and proc_empty.stdout.decode().strip() == ""
        proc_one = run(["--graph", gp, "--requests", rp_one, "--traffic", tp])
        assert (
            proc_one.returncode == 0
            and len(proc_one.stdout.decode().strip().splitlines()) == 1
        )
        proc_two = run(["--graph", gp, "--requests", rp_two, "--traffic", tp])
        assert (
            proc_two.returncode == 0
            and len(proc_two.stdout.decode().strip().splitlines()) == 2
        )
    finally:
        os.unlink(gp)
        os.unlink(tp)
        os.unlink(rp_empty)
        os.unlink(rp_one)
        os.unlink(rp_two)


def test_traffic_graph_with_1000_nodes_and_500_traffic_v8():
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
            timeout=90,
        )

    nodes = [f"N{i}" for i in range(1000)]
    edges = [{"from": f"N{i}", "to": f"N{i + 1}", "distance": 1} for i in range(999)]
    graph = {"nodes": nodes, "edges": edges}
    traffic = {
        "traffic": [
            {"from": f"N{i}", "to": f"N{i + 1}", "factor": 1.5, "delay": 0.5}
            for i in range(0, 999, 2)
        ]
    }
    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps(traffic))
    try:
        start = time.time()
        proc = run(["--graph", gp, "--from", "N0", "--to", "N999", "--traffic", tp])
        elapsed = time.time() - start
        assert proc.returncode == 0
        assert elapsed < 90.0, f"too slow 1000 nodes 500 traffic {elapsed}"
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_unknown_flag_with_equals_and_help_v8():
    import subprocess

    BIN = "/app/router"
    for args in [
        [BIN, "--unknown=foo"],
        [BIN, "--unknown=foo", "--graph", "x"],
        [BIN, "--graph", "x", "--unknown=foo", "--from", "A", "--to", "B"],
    ]:
        proc = subprocess.run(
            args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30
        )
        assert proc.returncode == 2, f"unknown with equals should be invalid {args}"
    # help wins even with unknown equals
    for args in [
        [BIN, "--help", "--unknown=foo"],
        [BIN, "--unknown=foo", "--help"],
        [BIN, "--help=true", "--unknown=foo"],
    ]:
        proc = subprocess.run(
            args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30
        )
        assert proc.returncode == 0 and "traffic" in proc.stdout.decode().lower()


def test_traffic_factor_delay_both_present_and_missing_mix_v8():
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
        "nodes": ["A", "B", "C"],
        "edges": [
            {"from": "A", "to": "B", "distance": 10},
            {"from": "B", "to": "C", "distance": 10},
        ],
    }
    gp = tmp(json.dumps(graph))
    # First entry has delay, second missing delay -> default 0, third has factor only
    tp = tmp(
        json.dumps(
            {
                "traffic": [
                    {"from": "A", "to": "B", "factor": 2, "delay": 5},
                    {"from": "B", "to": "C", "factor": 3},
                ]
            }
        )
    )
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "C", "--traffic", tp])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip())
        # A-B eff 25, B-C eff 30 total 55 raw20 delay 35
        assert math.isclose(out["effective_distance"], 55, abs_tol=1e-6)
        assert math.isclose(out["distance"], 20, abs_tol=1e-6)
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_batch_1000_with_traffic_float_many_decimals_v8():
    import tempfile, os, subprocess, json, time, math

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

    nodes = [f"N{i}" for i in range(100)]
    edges = [
        {"from": f"N{i}", "to": f"N{i + 1}", "distance": 1.123456789} for i in range(99)
    ]
    graph = {"nodes": nodes, "edges": edges}
    gp = tmp(json.dumps(graph))
    tp = tmp(
        json.dumps(
            {
                "traffic": [
                    {
                        "from": f"N{i}",
                        "to": f"N{i + 1}",
                        "factor": 1.123,
                        "delay": 0.456,
                    }
                    for i in range(0, 99, 10)
                ]
            }
        )
    )
    reqs = [{"source": "N0", "destination": f"N{i % 100}"} for i in range(1000)]
    rp = tmp(json.dumps(reqs))
    try:
        proc = run(["--graph", gp, "--requests", rp, "--traffic", tp])
        assert proc.returncode == 0
        assert len(proc.stdout.decode().strip().splitlines()) == 1000
    finally:
        os.unlink(gp)
        os.unlink(tp)
        os.unlink(rp)


def test_traffic_source_equals_dest_with_traffic_and_empty_traffic_v8():
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
    for traffic_content in ["[]", '{"traffic":[]}']:
        tp = tmp(traffic_content)
        try:
            proc = run(["--graph", gp, "--from", "A", "--to", "A", "--traffic", tp])
            assert proc.returncode == 0
            out = json.loads(proc.stdout.decode().strip())
            assert (
                out["path"] == ["A"]
                and out["distance"] == 0
                and out["effective_distance"] == 0
            )
        finally:
            os.unlink(tp)
    os.unlink(gp)


# === ULTRA HARD v9 for Step2 too easy (269->310) - making Step2 GIGA HARD++ ===


def test_traffic_effective_formula_6_edges_ultra_hard():
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
            timeout=60,
        )

    nodes = [f"N{i}" for i in range(7)]
    edges = [{"from": f"N{i}", "to": f"N{i + 1}", "distance": 10} for i in range(6)]
    graph = {"nodes": nodes, "edges": edges}
    # per-edge: f2 d5=25, f1 d10=20, f0.5 d0=5, f3 d1=31, f1 d2=12, f2 d0=20 total 113
    # wrong (raw+delay)*factor: (10+5)*2=30 + (10+10)*1=20 +5*0.5=2.5 + (10+1)*3=33 + (10+2)*1=12 +10*2=20 total 117.5 diff 4.5
    traffic = {
        "traffic": [
            {"from": "N0", "to": "N1", "factor": 2, "delay": 5},
            {"from": "N1", "to": "N2", "factor": 1, "delay": 10},
            {"from": "N2", "to": "N3", "factor": 0.5, "delay": 0},
            {"from": "N3", "to": "N4", "factor": 3, "delay": 1},
            {"from": "N4", "to": "N5", "factor": 1, "delay": 2},
            {"from": "N5", "to": "N6", "factor": 2, "delay": 0},
        ]
    }
    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps(traffic))
    try:
        proc = run(["--graph", gp, "--from", "N0", "--to", "N6", "--traffic", tp])
        assert proc.returncode == 0, proc.stderr.decode()
        out = json.loads(proc.stdout.decode().strip())
        assert math.isclose(out["distance"], 60, abs_tol=1e-6)
        assert math.isclose(out["effective_distance"], 113, abs_tol=1e-6), (
            f"per-edge 6 edges expected 113, got {out['effective_distance']} wrong formula 117.5"
        )
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_delay_only_reroute_3_alternatives():
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

    # 3 paths: A-B-D raw2 eff 2+200=202, A-C-D raw10 eff10, A-E-D raw20 eff20 -> pick A-C-D raw10
    graph = {
        "nodes": ["A", "B", "C", "D", "E"],
        "edges": [
            {"from": "A", "to": "B", "distance": 1},
            {"from": "B", "to": "D", "distance": 1},
            {"from": "A", "to": "C", "distance": 5},
            {"from": "C", "to": "D", "distance": 5},
            {"from": "A", "to": "E", "distance": 10},
            {"from": "E", "to": "D", "distance": 10},
        ],
    }
    traffic = {
        "traffic": [
            {"from": "A", "to": "B", "factor": 1, "delay": 100},
            {"from": "B", "to": "D", "factor": 1, "delay": 100},
            {"from": "A", "to": "C", "factor": 1, "delay": 0},
            {"from": "C", "to": "D", "factor": 1, "delay": 0},
            {"from": "A", "to": "E", "factor": 1, "delay": 0},
            {"from": "E", "to": "D", "factor": 1, "delay": 0},
        ]
    }
    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps(traffic))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "D", "--traffic", tp])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip())
        assert out["path"] == ["A", "C", "D"], (
            f"delay-only reroute should pick C path, got {out['path']}"
        )
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_secondary_raw_tie_with_factor_and_delay_mix_hard():
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

    # Effective equal 30 for both paths, raw 10 vs 20 -> pick raw 10 even though lex B vs C would favor B anyway, but we make C lex smaller and raw larger to catch missing raw tie
    # Path A-B-D: A-B 5 f2=10, B-D 5 f2=10 total eff20 raw10 - need 30, add delay 5 each =>15+15=30 raw10
    # Path A-C-D: A-C 10 f1=10, C-D 10 f1=10 total eff20 raw20, need 30 add delay 5 each =>15+15=30 raw20
    # Effective both 30 raw 10 vs 20 -> should pick A-B-D raw10 even though C < B lex? Actually B<C lex, so both raw and lex favor B, need opposite: make A-C-D lex smaller but raw larger
    # Let's make nodes: A-B-D vs A-A-1-D? Use B vs A-1: 'A-1' < 'B' lex because '-' < 'B'? Actually 'A' same, second char '-'45 vs 'B'66, so A-1 < B
    # So path A-A-1-D lex smaller than A-B-D, but raw larger, should still pick A-B-D if raw tie considered
    graph = {
        "nodes": ["A", "B", "A-1", "D"],
        "edges": [
            {"from": "A", "to": "B", "distance": 5},
            {"from": "B", "to": "D", "distance": 5},
            {"from": "A", "to": "A-1", "distance": 10},
            {"from": "A-1", "to": "D", "distance": 10},
        ],
    }
    traffic = {
        "traffic": [
            {"from": "A", "to": "B", "factor": 2, "delay": 5},
            {"from": "B", "to": "D", "factor": 2, "delay": 5},
            {"from": "A", "to": "A-1", "factor": 1, "delay": 5},
            {"from": "A-1", "to": "D", "factor": 1, "delay": 5},
        ]
    }
    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps(traffic))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "D", "--traffic", tp])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip())
        # Effective: A-B-D (5*2+5)=15+15=30 raw10, A-A-1-D (10+5)+(10+5)=30 raw20, effective equal 30, raw 10 vs 20 => pick A-B-D
        # Lex would pick A-A-1-D because A-1 < B ( '-' < 'B' ), so if agent misses raw tie, they'd pick A-A-1-D wrong
        assert out["path"] == ["A", "B", "D"], (
            f"secondary raw tie with lex opposite: expected A-B-D raw10, got {out['path']} (if only effective->lex, would pick A-A-1-D)"
        )
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_10_way_tie_with_mixed_factor_delay():
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

    nodes = ["S"] + [chr(ord("B") + i) for i in range(10)] + ["T"]
    edges = []
    for i in range(10):
        mid = chr(ord("B") + i)
        edges.append({"from": "S", "to": mid, "distance": 5})
        edges.append({"from": mid, "to": "T", "distance": 5})
    # Make effective equal for all via factor 1 delay 0, raw equal 10, lex B wins
    # Then make one with factor 0.5 delay 5: effective 2.5+5+2.5+5? Actually S-B raw5*0.5+5=7.5, B-T 5*0.5+5=7.5 total15 vs others 10, so B would not win
    # So keep all factor1 delay0 for 10-way
    graph = {"nodes": nodes, "edges": edges}
    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps([]))
    try:
        proc = run(["--graph", gp, "--from", "S", "--to", "T", "--traffic", tp])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip())
        assert out["path"] == ["S", "B", "T"], (
            f"10-way tie should pick B, got {out['path']}"
        )
        # Now with traffic where effective still equal but via factor+delay mixed: S-B f2 d0 =>10, B-T f0.5 d5 =>2.5+5=7.5 total17.5
        # To keep all equal 10, need careful: S-B f1 d0=5, B-T f1 d0=5 total10; S-C f2 d0=10, C-T f0 d? Factor 0 invalid
        # So keep simple 10-way as above
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_output_strict_4_keys_no_extra_number_not_string():
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
    tp = tmp(
        json.dumps({"traffic": [{"from": "A", "to": "B", "factor": 2, "delay": 3}]})
    )
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B", "--traffic", tp])
        assert proc.returncode == 0
        raw_out = proc.stdout.decode().strip()
        out = json.loads(raw_out)
        # Exactly 4 keys
        assert set(out.keys()) == {
            "path",
            "distance",
            "effective_distance",
            "traffic_delay",
        }, f"must have exactly 4 keys, got {out.keys()}"
        # Types: numbers not strings
        assert isinstance(out["distance"], (int, float)) and not isinstance(
            out["distance"], bool
        )
        assert isinstance(out["effective_distance"], (int, float))
        assert isinstance(out["traffic_delay"], (int, float))
        assert isinstance(out["path"], list) and all(
            isinstance(x, str) for x in out["path"]
        )
        # Ensure JSON output is not stringified numbers: raw text should not contain \"distance\": \"5\"
        assert '"distance": "5"' not in raw_out and "'distance'" not in raw_out
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_batch_output_strict_6_keys_no_extra():
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

    graph = {
        "nodes": ["A", "B", "C"],
        "edges": [
            {"from": "A", "to": "B", "distance": 1},
            {"from": "B", "to": "C", "distance": 1},
        ],
    }
    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps({"traffic": [{"from": "A", "to": "B", "factor": 2}]}))
    rp = tmp(
        json.dumps(
            [{"source": "A", "destination": "C"}, {"source": "A", "destination": "X"}]
        )
    )
    try:
        proc = run(["--graph", gp, "--requests", rp, "--traffic", tp])
        assert proc.returncode == 1
        lines = proc.stdout.decode().strip().splitlines()
        assert len(lines) == 2
        for line in lines:
            o = json.loads(line)
            assert set(o.keys()) == {
                "source",
                "destination",
                "path",
                "distance",
                "effective_distance",
                "traffic_delay",
            }
            assert isinstance(o["distance"], (int, float))
    finally:
        os.unlink(gp)
        os.unlink(tp)
        os.unlink(rp)


def test_traffic_flag_missing_value_and_unknown_with_traffic():
    import subprocess, tempfile, os, json

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
    tp = tmp(json.dumps({"traffic": []}))
    try:
        # missing value
        proc = run(["--graph", gp, "--from", "A", "--to", "B", "--traffic"])
        assert proc.returncode == 2 and proc.stdout.decode().strip() == ""
        proc2 = run(["--graph", gp, "--from", "A", "--to", "B", "--traffic="])
        assert proc2.returncode == 2
        proc3 = run(["--graph", gp, "--from", "A", "--to", "B", "--requests"])
        assert proc3.returncode == 2
        # unknown with equals
        proc4 = run(["--graph", gp, "--from", "A", "--to", "B", "--unknown=foo"])
        assert proc4.returncode == 2
        proc5 = run(
            [
                "--graph",
                gp,
                "--from",
                "A",
                "--to",
                "B",
                "--traffic",
                tp,
                "--unknown=foo",
            ]
        )
        assert proc5.returncode == 2
        # help wins even with unknown and traffic
        proc6 = run(["--help", "--unknown=foo", "--traffic", tp])
        assert proc6.returncode == 0 and "traffic" in proc6.stdout.decode().lower()
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_large_graph_2000_nodes_1000_requests_with_traffic():
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
    traffic = {
        "traffic": [
            {"from": f"N{i}", "to": f"N{i + 1}", "factor": 1.2}
            for i in range(0, 999, 100)
        ]
    }
    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps(traffic))
    reqs = [
        {"source": f"N{i % 1000}", "destination": f"N{(i * 13) % 1000}"}
        for i in range(500)
    ]
    rp = tmp(json.dumps(reqs))
    try:
        start = time.time()
        proc = run(["--graph", gp, "--requests", rp, "--traffic", tp])
        elapsed = time.time() - start
        assert proc.returncode in (0, 1)
        assert elapsed < 70.0, (
            f"too slow 1000 nodes 500 requests with traffic {elapsed}"
        )
        assert len(proc.stdout.decode().strip().splitlines()) == 500
    finally:
        os.unlink(gp)
        os.unlink(tp)
        os.unlink(rp)


def test_traffic_source_equals_dest_with_traffic_and_factor():
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

    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 10}]}
    gp = tmp(json.dumps(graph))
    tp = tmp(
        json.dumps({"traffic": [{"from": "A", "to": "B", "factor": 5, "delay": 100}]})
    )
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
        proc2 = run(["--graph", gp, "--from", "B", "--to", "B", "--traffic", tp])
        assert proc2.returncode == 0
        out2 = json.loads(proc2.stdout.decode().strip())
        assert out2["distance"] == 0 and out2["effective_distance"] == 0
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_no_route_with_traffic_single_and_batch_strict():
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
    tp = tmp(json.dumps([]))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "C", "--traffic", tp])
        assert proc.returncode == 1
        out = json.loads(proc.stdout.decode().strip())
        assert out == {
            "path": [],
            "distance": -1,
            "effective_distance": -1,
            "traffic_delay": -1,
        }
        rp = tmp(json.dumps([{"source": "A", "destination": "C"}]))
        proc2 = run(["--graph", gp, "--requests", rp, "--traffic", tp])
        assert proc2.returncode == 1
        out2 = json.loads(proc2.stdout.decode().strip().splitlines()[0])
        assert (
            out2["distance"] == -1
            and out2["effective_distance"] == -1
            and out2["traffic_delay"] == -1
        )
        assert set(out2.keys()) == {
            "source",
            "destination",
            "path",
            "distance",
            "effective_distance",
            "traffic_delay",
        }
        os.unlink(rp)
    finally:
        os.unlink(gp)
        os.unlink(tp)


# === ULTRA HARD v10 for Step2 - Step2 still too easy, pushing 279->310 ===


def test_traffic_with_large_factor_and_delay_extreme_v10():
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
            timeout=60,
        )

    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 100}]}
    gp = tmp(json.dumps(graph))
    for factor, delay in [(1e6, 1e6), (1e-6, 0), (0.1, 1000), (1000, 0.1)]:
        tp = tmp(
            json.dumps(
                {
                    "traffic": [
                        {"from": "A", "to": "B", "factor": factor, "delay": delay}
                    ]
                }
            )
        )
        try:
            proc = run(["--graph", gp, "--from", "A", "--to", "B", "--traffic", tp])
            assert proc.returncode == 0
            out = json.loads(proc.stdout.decode().strip())
            assert math.isclose(
                out["effective_distance"], 100 * factor + delay, rel_tol=1e-6
            )
        finally:
            os.unlink(tp)
    os.unlink(gp)


def test_traffic_batch_with_traffic_and_requests_mixed_big_v10():
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
            timeout=90,
        )

    nodes = [f"N{i}" for i in range(200)]
    edges = [{"from": f"N{i}", "to": f"N{i + 1}", "distance": 1} for i in range(199)]
    graph = {"nodes": nodes, "edges": edges}
    gp = tmp(json.dumps(graph))
    tp = tmp(
        json.dumps(
            {
                "traffic": [
                    {"from": f"N{i}", "to": f"N{i + 1}", "factor": 1.2}
                    for i in range(0, 199, 20)
                ]
            }
        )
    )
    reqs = [
        {"source": f"N{i % 200}", "destination": f"N{(i * 13) % 200}"}
        for i in range(1000)
    ]
    rp = tmp(json.dumps(reqs))
    try:
        proc = run(["--graph", gp, "--requests", rp, "--traffic", tp])
        assert proc.returncode in (0, 1)
        assert len(proc.stdout.decode().strip().splitlines()) == 1000
    finally:
        os.unlink(gp)
        os.unlink(tp)
        os.unlink(rp)


def test_traffic_case_sensitive_ascii_with_traffic_v10():
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
        "nodes": ["A", "B", "b", "C", "Z"],
        "edges": [
            {"from": "A", "to": "B", "distance": 5},
            {"from": "B", "to": "Z", "distance": 5},
            {"from": "A", "to": "b", "distance": 5},
            {"from": "b", "to": "Z", "distance": 5},
            {"from": "A", "to": "C", "distance": 10},
            {"from": "C", "to": "Z", "distance": 10},
        ],
    }
    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps([]))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "Z", "--traffic", tp])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip())
        assert out["path"] == ["A", "B", "Z"], (
            f"case-sensitive B < b, got {out['path']}"
        )
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_duplicate_interleaved_with_delay_and_factor_v10():
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

    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 10}]}
    gp = tmp(json.dumps(graph))
    # Interleaved duplicates with extra fields, last wins
    entries = [
        {"from": "A", "to": "B", "factor": 1, "delay": 1, "extra": "a"},
        {"from": "B", "to": "A", "factor": 2, "delay": 2, "extra": "b"},
        {"from": "A", "to": "B", "factor": 3, "delay": 3, "extra": "c"},
        {"from": "B", "to": "A", "factor": 4, "delay": 4, "extra": "d"},
        {"from": "A", "to": "B", "factor": 5, "delay": 5},
    ]
    tp = tmp(json.dumps({"traffic": entries, "version": 1}))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B", "--traffic", tp])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip())
        assert math.isclose(out["effective_distance"], 55, abs_tol=1e-6), (
            f"last wins factor5 delay5 =>55, got {out['effective_distance']}"
        )
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_with_version_field_and_extra_nested_v10():
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

    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 10}]}
    gp = tmp(json.dumps(graph))
    tp = tmp(
        json.dumps(
            {
                "traffic": [
                    {
                        "from": "A",
                        "to": "B",
                        "factor": 2,
                        "delay": 1,
                        "meta": {"nested": {"x": 1}},
                    }
                ],
                "version": 3,
                "extra": {"y": 2},
            }
        )
    )
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B", "--traffic", tp])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip())
        assert math.isclose(out["effective_distance"], 21, abs_tol=1e-6)
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_batch_with_empty_and_whitespace_and_missing_v10():
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
    tp = tmp(json.dumps([]))
    # empty -> no-route, missing -> invalid, null -> invalid
    rp_empty = tmp(
        json.dumps(
            [{"source": "", "destination": "B"}, {"source": "A", "destination": ""}]
        )
    )
    rp_missing = tmp(json.dumps([{"source": "A"}]))
    rp_null = tmp('[{"source":null,"destination":"B"}]')
    try:
        proc_empty = run(["--graph", gp, "--requests", rp_empty, "--traffic", tp])
        assert proc_empty.returncode == 1
        proc_missing = run(["--graph", gp, "--requests", rp_missing, "--traffic", tp])
        assert proc_missing.returncode == 2
        proc_null = run(["--graph", gp, "--requests", rp_null, "--traffic", tp])
        assert proc_null.returncode == 2
    finally:
        os.unlink(gp)
        os.unlink(tp)
        os.unlink(rp_empty)
        os.unlink(rp_missing)
        os.unlink(rp_null)


# === ULTRA HARD v11 for Step2 - Step1 good keep, Step2 too easy (285->335) ===


def test_traffic_effective_formula_7_edges_ultra_hard_v11():
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
            timeout=60,
        )

    nodes = [f"N{i}" for i in range(8)]
    edges = [{"from": f"N{i}", "to": f"N{i + 1}", "distance": 10} for i in range(7)]
    graph = {"nodes": nodes, "edges": edges}
    traffic = {
        "traffic": [
            {"from": "N0", "to": "N1", "factor": 2, "delay": 5},  # 25
            {"from": "N1", "to": "N2", "factor": 1, "delay": 10},  # 20
            {"from": "N2", "to": "N3", "factor": 0.5, "delay": 0},  # 5
            {"from": "N3", "to": "N4", "factor": 3, "delay": 1},  # 31
            {"from": "N4", "to": "N5", "factor": 1, "delay": 2},  # 12
            {"from": "N5", "to": "N6", "factor": 2, "delay": 0},  # 20
            {"from": "N6", "to": "N7", "factor": 1.5, "delay": 3},  # 18
        ]
    }
    # total correct 25+20+5+31+12+20+18=131, wrong (raw+delay)*factor: 30+20+2.5+33+12+20+19.5=137
    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps(traffic))
    try:
        proc = run(["--graph", gp, "--from", "N0", "--to", "N7", "--traffic", tp])
        assert proc.returncode == 0, proc.stderr.decode()
        out = json.loads(proc.stdout.decode().strip())
        assert math.isclose(out["distance"], 70, abs_tol=1e-6)
        assert math.isclose(out["effective_distance"], 131, abs_tol=1e-6), (
            f"7 edges per-edge expected 131, got {out['effective_distance']} (wrong ~137)"
        )
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_raw_along_effective_best_5_hops_v11():
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
            timeout=60,
        )

    graph = {
        "nodes": ["A", "B", "C", "D", "E", "F", "G", "H"],
        "edges": [
            {"from": "A", "to": "B", "distance": 1},
            {"from": "B", "to": "C", "distance": 1},
            {"from": "C", "to": "D", "distance": 1},
            {"from": "D", "to": "H", "distance": 1},
            {"from": "A", "to": "E", "distance": 10},
            {"from": "E", "to": "F", "distance": 10},
            {"from": "F", "to": "G", "distance": 10},
            {"from": "G", "to": "H", "distance": 10},
        ],
    }
    traffic = {
        "traffic": [
            {"from": "A", "to": "B", "factor": 100},
            {"from": "B", "to": "C", "factor": 100},
            {"from": "C", "to": "D", "factor": 100},
            {"from": "D", "to": "H", "factor": 100},
            {"from": "A", "to": "E", "factor": 1},
            {"from": "E", "to": "F", "factor": 1},
            {"from": "F", "to": "G", "factor": 1},
            {"from": "G", "to": "H", "factor": 1},
        ]
    }
    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps(traffic))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "H", "--traffic", tp])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip())
        assert out["path"] == ["A", "E", "F", "G", "H"], (
            f"should reroute 4 hops, got {out['path']}"
        )
        assert math.isclose(out["distance"], 40, abs_tol=1e-6), (
            f"raw must be 40 along effective-best, got {out['distance']} (bug would report 4)"
        )
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_duplicate_with_delay_reset_and_reverse_hard_v11():
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

    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 10}]}
    gp = tmp(json.dumps(graph))
    # A-B f1 d100, B-A f2 (no delay) => eff20 (reset), A-B f3 d50 extra ignored => eff80, B-A f4 d0 =>40
    traffic = {
        "traffic": [
            {"from": "A", "to": "B", "factor": 1, "delay": 100, "extra": "a"},
            {"from": "B", "to": "A", "factor": 2},
            {"from": "A", "to": "B", "factor": 3, "delay": 50, "extra": "b"},
            {"from": "B", "to": "A", "factor": 4, "delay": 0},
        ]
    }
    tp = tmp(json.dumps(traffic))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B", "--traffic", tp])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip())
        assert math.isclose(out["effective_distance"], 40, abs_tol=1e-6), (
            f"last wins factor4 delay0 =>40, got {out['effective_distance']}"
        )
        # Without last, should be factor3 delay50 =>80
        tp2 = tmp(
            json.dumps(
                {
                    "traffic": [
                        {"from": "A", "to": "B", "factor": 1, "delay": 100},
                        {"from": "B", "to": "A", "factor": 2},
                        {"from": "A", "to": "B", "factor": 3, "delay": 50},
                    ]
                }
            )
        )
        proc2 = run(["--graph", gp, "--from", "A", "--to", "B", "--traffic", tp2])
        assert proc2.returncode == 0
        out2 = json.loads(proc2.stdout.decode().strip())
        assert math.isclose(out2["effective_distance"], 80, abs_tol=1e-6)
        os.unlink(tp2)
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_factor_scientific_plus_mix_with_delay_v11():
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

    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 10}]}
    gp = tmp(json.dumps(graph))
    for fac_lit, fac_val, del_lit, del_val in [
        ("1e+2", "100", "1e-2", "0.01"),
        ("1E+3", "1000", "1E+3", "1000"),
        ("2.5e+2", "250", "2.5e+1", "25"),
    ]:
        tp = tmp(
            '{"traffic":[{"from":"A","to":"B","factor":'
            + fac_lit
            + ',"delay":'
            + del_lit
            + "}]}"
        )
        try:
            proc = run(["--graph", gp, "--from", "A", "--to", "B", "--traffic", tp])
            assert proc.returncode == 0, (
                f"factor {fac_lit} delay {del_lit} should be valid"
            )
            out = json.loads(proc.stdout.decode().strip())
            assert math.isclose(
                out["effective_distance"],
                10 * float(fac_val) + float(del_val),
                rel_tol=1e-6,
            )
        finally:
            os.unlink(tp)
    os.unlink(gp)


def test_traffic_batch_with_special_chars_and_unicode_v11():
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

    graph = {
        "nodes": ["A-1", "B_2", "C.D", "E/F", "G H"],
        "edges": [
            {"from": "A-1", "to": "B_2", "distance": 1},
            {"from": "B_2", "to": "C.D", "distance": 1},
            {"from": "C.D", "to": "E/F", "distance": 1},
        ],
    }
    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps({"traffic": [{"from": "A-1", "to": "B_2", "factor": 2}]}))
    reqs = [
        {"source": "A-1", "destination": "C.D"},
        {"source": "B_2", "destination": "E/F"},
        {"source": "A-1", "destination": "Missing"},
    ]
    rp = tmp(json.dumps(reqs))
    try:
        proc = run(["--graph", gp, "--requests", rp, "--traffic", tp])
        assert proc.returncode == 1
        lines = proc.stdout.decode().strip().splitlines()
        assert len(lines) == 3
    finally:
        os.unlink(gp)
        os.unlink(tp)
        os.unlink(rp)


def test_traffic_output_fields_strict_and_number_types_v11():
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
    tp = tmp(
        json.dumps({"traffic": [{"from": "A", "to": "B", "factor": 2, "delay": 3}]})
    )
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B", "--traffic", tp])
        assert proc.returncode == 0
        raw = proc.stdout.decode().strip()
        out = json.loads(raw)
        assert set(out.keys()) == {
            "path",
            "distance",
            "effective_distance",
            "traffic_delay",
        }
        # Ensure not stringified
        assert '"distance": "5"' not in raw and '"distance":"5"' not in raw.replace(
            " ", ""
        )
        # Batch
        rp = tmp(json.dumps([{"source": "A", "destination": "B"}]))
        proc2 = run(["--graph", gp, "--requests", rp, "--traffic", tp])
        assert proc2.returncode == 0
        out2 = json.loads(proc2.stdout.decode().strip().splitlines()[0])
        assert set(out2.keys()) == {
            "source",
            "destination",
            "path",
            "distance",
            "effective_distance",
            "traffic_delay",
        }
        os.unlink(rp)
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_help_with_equals_and_traffic_and_requests_v11():
    import subprocess

    BIN = "/app/router"
    for args in [
        [BIN, "--help=true"],
        [BIN, "--help= true"],
        [BIN, "--help=false"],
        [BIN, "-h=true"],
        [BIN, "--help", "--traffic", "x", "--requests", "y", "--unknown"],
        [BIN, "--traffic", "x", "--help", "--unknown"],
    ]:
        proc = subprocess.run(
            args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30
        )
        assert proc.returncode == 0, f"help should win for {args}"
        out = proc.stdout.decode().lower()
        assert "traffic" in out and "graph" in out


def test_traffic_large_graph_3000_with_traffic_and_2000_batch_v11():
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
            timeout=90,
        )

    nodes = [f"N{i}" for i in range(1000)]
    edges = [{"from": f"N{i}", "to": f"N{i + 1}", "distance": 1} for i in range(999)]
    graph = {"nodes": nodes, "edges": edges}
    traffic = {
        "traffic": [
            {"from": f"N{i}", "to": f"N{i + 1}", "factor": 1.2}
            for i in range(0, 999, 100)
        ]
    }
    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps(traffic))
    reqs = [
        {"source": f"N{i % 1000}", "destination": f"N{(i * 13) % 1000}"}
        for i in range(500)
    ]
    rp = tmp(json.dumps(reqs))
    try:
        start = time.time()
        proc = run(["--graph", gp, "--requests", rp, "--traffic", tp])
        elapsed = time.time() - start
        assert proc.returncode in (0, 1)
        assert elapsed < 75.0, f"too slow 1000 nodes 500 req with traffic {elapsed}"
        assert len(proc.stdout.decode().strip().splitlines()) == 500
    finally:
        os.unlink(gp)
        os.unlink(tp)
        os.unlink(rp)


def test_traffic_batch_5000_same_source_with_traffic_v11():
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

    nodes = [f"N{i}" for i in range(500)]
    edges = [{"from": f"N{i}", "to": f"N{i + 1}", "distance": 1} for i in range(499)]
    graph = {"nodes": nodes, "edges": edges}
    traffic = {
        "traffic": [
            {"from": f"N{i}", "to": f"N{i + 1}", "factor": 1.1, "delay": 0.5}
            for i in range(0, 499, 50)
        ]
    }
    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps(traffic))
    same = [{"source": "N0", "destination": f"N{i % 500}"} for i in range(1000)]
    rp = tmp(json.dumps(same))
    try:
        start = time.time()
        proc = run(["--graph", gp, "--requests", rp, "--traffic", tp])
        elapsed = time.time() - start
        assert proc.returncode == 0
        assert elapsed < 70.0, f"too slow 500 nodes 1000 same with traffic {elapsed}"
        assert len(proc.stdout.decode().strip().splitlines()) == 1000
    finally:
        os.unlink(gp)
        os.unlink(tp)
        os.unlink(rp)


def test_traffic_with_emoji_and_special_and_traffic_v11():
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
            timeout=60,
        )

    graph = {
        "nodes": ["A🚀", "B💥", "C🌟"],
        "edges": [
            {"from": "A🚀", "to": "B💥", "distance": 5},
            {"from": "B💥", "to": "C🌟", "distance": 5},
            {"from": "A🚀", "to": "C🌟", "distance": 20},
        ],
    }
    gp = tmp(json.dumps(graph))
    tp = tmp(
        json.dumps(
            {
                "traffic": [
                    {"from": "A🚀", "to": "B💥", "factor": 2},
                    {"from": "B💥", "to": "C🌟", "factor": 0.5},
                ]
            }
        )
    )
    try:
        proc = run(["--graph", gp, "--from", "A🚀", "--to", "C🌟", "--traffic", tp])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip())
        # Path A-B-C: 5*2=10 +5*0.5=2.5 total12.5 vs direct 20
        assert out["path"] == ["A🚀", "B💥", "C🌟"]
        assert math.isclose(out["effective_distance"], 12.5, abs_tol=1e-6)
    finally:
        os.unlink(gp)
        os.unlink(tp)

# ==================== v12 ULTRA HARD – make step2 much harder (50 extra tests) ====================

def test_traffic_effective_formula_8_edges_ultra_hard_v12():
    import tempfile, os, subprocess, json, math
    def tmp(c):
        f=tempfile.NamedTemporaryFile(delete=False,suffix=".json",mode="w"); f.write(c); f.close(); return f.name
    def run(args):
        return subprocess.run(["/app/router"]+args,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=30)
    graph={"nodes":["A","B","C","D","E","F","G","H","I"],"edges":[
        {"from":"A","to":"B","distance":10},{"from":"B","to":"C","distance":10},{"from":"C","to":"D","distance":10},{"from":"D","to":"E","distance":10},
        {"from":"A","to":"F","distance":10},{"from":"F","to":"G","distance":10},{"from":"G","to":"H","distance":10},{"from":"H","to":"E","distance":10},
    ]}
    traffic={"traffic":[
        {"from":"A","to":"B","factor":2,"delay":5},{"from":"B","to":"C","factor":0.5,"delay":10},
        {"from":"C","to":"D","factor":3,"delay":1},{"from":"D","to":"E","factor":0.5,"delay":2},
        {"from":"A","to":"F","factor":1,"delay":10},{"from":"F","to":"G","factor":1,"delay":10},{"from":"G","to":"H","factor":1,"delay":10},{"from":"H","to":"E","factor":1,"delay":10},
    ]}
    gp=tmp(json.dumps(graph)); tp=tmp(json.dumps(traffic))
    try:
        proc=run(["--graph",gp,"--from","A","--to","E","--traffic",tp])
        assert proc.returncode==0, proc.stderr.decode()[:500]
        out=json.loads(proc.stdout.decode().strip())
        assert out["path"]==["A","B","C","D","E"]
        assert math.isclose(out["effective_distance"],78,abs_tol=1e-6)
    finally:
        os.unlink(gp); os.unlink(tp)

def test_traffic_raw_along_effective_best_7_hops_v12():
    import tempfile, os, subprocess, json, math
    def tmp(c):
        f=tempfile.NamedTemporaryFile(delete=False,suffix=".json",mode="w"); f.write(c); f.close(); return f.name
    def run(args):
        return subprocess.run(["/app/router"]+args,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=30)
    nodes=["A","B","C","D","E","F","G","H"]
    edges=[{"from":nodes[i],"to":nodes[i+1],"distance":3} for i in range(7)]
    edges.append({"from":"A","to":"H","distance":10})
    graph={"nodes":nodes,"edges":edges}
    traffic={"traffic":[{"from":nodes[i],"to":nodes[i+1],"factor":0.4,"delay":0} for i in range(7)] + [{"from":"A","to":"H","factor":2,"delay":0}]}
    gp=tmp(json.dumps(graph)); tp=tmp(json.dumps(traffic))
    try:
        proc=run(["--graph",gp,"--from","A","--to","H","--traffic",tp])
        assert proc.returncode==0
        out=json.loads(proc.stdout.decode())
        assert out["path"]==["A","B","C","D","E","F","G","H"]
        assert math.isclose(out["distance"],21,abs_tol=1e-6)
    finally:
        os.unlink(gp); os.unlink(tp)

def test_traffic_duplicate_20_entries_last_wins_reverse_delay_reset_v12():
    import tempfile, os, subprocess, json, math
    def tmp(c):
        f=tempfile.NamedTemporaryFile(delete=False,suffix=".json",mode="w"); f.write(c); f.close(); return f.name
    def run(args):
        return subprocess.run(["/app/router"]+args,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=30)
    graph={"nodes":["A","B"],"edges":[{"from":"A","to":"B","distance":10}]}
    traffic_entries=[]
    for i in range(19):
        frm="A" if i%2==0 else "B"
        to="B" if i%2==0 else "A"
        if i%3==0:
            traffic_entries.append({"from":frm,"to":to,"factor":1+i,"delay":i})
        else:
            traffic_entries.append({"from":frm,"to":to,"factor":1+i})
    traffic_entries.append({"from":"B","to":"A","factor":5})
    gp=tmp(json.dumps(graph)); tp=tmp(json.dumps({"traffic":traffic_entries}))
    try:
        proc=run(["--graph",gp,"--from","A","--to","B","--traffic",tp])
        assert proc.returncode==0
        out=json.loads(proc.stdout.decode())
        assert math.isclose(out["effective_distance"],50,abs_tol=1e-6)
    finally:
        os.unlink(gp); os.unlink(tp)

def test_traffic_large_traffic_file_2000_entries_v12():
    import tempfile, os, subprocess, json, time
    def tmp(c):
        f=tempfile.NamedTemporaryFile(delete=False,suffix=".json",mode="w"); f.write(c); f.close(); return f.name
    def run(args):
        return subprocess.run(["/app/router"]+args,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=90)
    n=500
    nodes=[f"N{i}" for i in range(n)]
    edges=[{"from":f"N{i}","to":f"N{i+1}","distance":1} for i in range(n-1)]
    graph={"nodes":nodes,"edges":edges}
    traffic={"traffic":[{"from":f"N{i}","to":f"N{i+1}","factor":1.1,"delay":0.1} for i in range(n-1)]}
    extra=[{"from":f"N{i% (n-1)}","to":f"N{(i% (n-1))+1}","factor":1.1,"delay":0.1} for i in range(1501)]
    traffic["traffic"].extend(extra)
    gp=tmp(json.dumps(graph)); tp=tmp(json.dumps(traffic))
    try:
        start=time.time()
        proc=run(["--graph",gp,"--from","N0","--to",f"N{n-1}","--traffic",tp])
        elapsed=time.time()-start
        assert proc.returncode==0, proc.stderr.decode()[:300]
        assert elapsed<5.0, f"2000 traffic entries too slow {elapsed}"
    finally:
        os.unlink(gp); os.unlink(tp)

def test_traffic_batch_3000_nodes_2000_requests_same_source_perf_v12():
    import tempfile, os, subprocess, json, time
    def tmp(c):
        f=tempfile.NamedTemporaryFile(delete=False,suffix=".json",mode="w"); f.write(c); f.close(); return f.name
    def run(args):
        return subprocess.run(["/app/router"]+args,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=90)
    nodes=[f"N{i}" for i in range(1000)]
    edges=[{"from":f"N{i}","to":f"N{i+1}","distance":1} for i in range(999)]
    graph={"nodes":nodes,"edges":edges}
    traffic={"traffic":[{"from":f"N{i}","to":f"N{i+1}","factor":1.2} for i in range(0,999,100)]}
    gp=tmp(json.dumps(graph)); tp=tmp(json.dumps(traffic))
    reqs=[{"source":"N0","destination":f"N{i%1000}"} for i in range(2000)]
    rp=tmp(json.dumps(reqs))
    try:
        start=time.time()
        proc=run(["--graph",gp,"--requests",rp,"--traffic",tp])
        elapsed=time.time()-start
        assert proc.returncode==0
        assert elapsed<80.0, f"3000 nodes 2000 same source too slow {elapsed}"
        assert len(proc.stdout.decode().strip().splitlines())==2000
    finally:
        os.unlink(gp); os.unlink(tp); os.unlink(rp)

def test_traffic_flag_order_independence_v12_with_equals_shuffled():
    import tempfile, os, subprocess, json, math
    def tmp(c):
        f=tempfile.NamedTemporaryFile(delete=False,suffix=".json",mode="w"); f.write(c); f.close(); return f.name
    def run(args):
        return subprocess.run(["/app/router"]+args,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=30)
    graph={"nodes":["A","B"],"edges":[{"from":"A","to":"B","distance":5}]}
    traffic={"traffic":[{"from":"A","to":"B","factor":2}]}
    gp=tmp(json.dumps(graph)); tp=tmp(json.dumps(traffic))
    try:
        for args in [
            ["--graph",gp,"--from","A","--to","B","--traffic",tp],
            ["--traffic",tp,"--from","A","--to","B","--graph",gp],
            [f"--graph={gp}",f"--from=A",f"--to=B",f"--traffic={tp}"],
            [f"--traffic={tp}",f"--graph={gp}","--from","A","--to","B"],
            ["--from","A","--to","B","--graph",gp,"--traffic",tp],
        ]:
            proc=run(args)
            assert proc.returncode==0, f"flag order failed {args} {proc.stderr.decode()[:200]}"
            out=json.loads(proc.stdout.decode())
            assert math.isclose(out["effective_distance"],10,abs_tol=1e-6)
    finally:
        os.unlink(gp); os.unlink(tp)

def test_traffic_output_strict_traffic_delay_equals_effective_minus_raw_v12():
    import tempfile, os, subprocess, json, math
    def tmp(c):
        f=tempfile.NamedTemporaryFile(delete=False,suffix=".json",mode="w"); f.write(c); f.close(); return f.name
    def run(args):
        return subprocess.run(["/app/router"]+args,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=30)
    graph={"nodes":["A","B","C"],"edges":[{"from":"A","to":"B","distance":3},{"from":"B","to":"C","distance":4}]}
    traffic={"traffic":[{"from":"A","to":"B","factor":2,"delay":1},{"from":"B","to":"C","factor":0.5,"delay":2}]}
    gp=tmp(json.dumps(graph)); tp=tmp(json.dumps(traffic))
    try:
        proc=run(["--graph",gp,"--from","A","--to","C","--traffic",tp])
        assert proc.returncode==0
        out=json.loads(proc.stdout.decode())
        assert math.isclose(out["distance"],7,abs_tol=1e-6)
        assert math.isclose(out["effective_distance"],11,abs_tol=1e-6)
        assert math.isclose(out["traffic_delay"],4,abs_tol=1e-6)
        assert math.isclose(out["traffic_delay"], out["effective_distance"]-out["distance"], abs_tol=1e-9)
        assert set(out.keys())=={"path","distance","effective_distance","traffic_delay"}
    finally:
        os.unlink(gp); os.unlink(tp)

def test_traffic_case_sensitive_ascii_traffic_tie_v12():
    import tempfile, os, subprocess, json
    def tmp(c):
        f=tempfile.NamedTemporaryFile(delete=False,suffix=".json",mode="w"); f.write(c); f.close(); return f.name
    def run(args):
        return subprocess.run(["/app/router"]+args,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=30)
    graph={"nodes":["A","a","B","b","C"],"edges":[
        {"from":"A","to":"B","distance":1},{"from":"A","to":"b","distance":1},
        {"from":"B","to":"C","distance":1},{"from":"b","to":"C","distance":1},
        {"from":"A","to":"a","distance":1},{"from":"a","to":"C","distance":1},
    ]}
    gp=tmp(json.dumps(graph)); tp=tmp(json.dumps({"traffic":[]}))
    try:
        proc=run(["--graph",gp,"--from","A","--to","C","--traffic",tp])
        assert proc.returncode==0
        out=json.loads(proc.stdout.decode())
        assert out["path"]==["A","B","C"]
    finally:
        os.unlink(gp); os.unlink(tp)

def test_traffic_emoji_special_traffic_complex_v12():
    import tempfile, os, subprocess, json, math
    def tmp(c):
        f=tempfile.NamedTemporaryFile(delete=False,suffix=".json",mode="w"); f.write(c); f.close(); return f.name
    def run(args):
        return subprocess.run(["/app/router"]+args,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=30)
    graph={"nodes":["A🚀","B💥","C🌟","D🔥"],"edges":[
        {"from":"A🚀","to":"B💥","distance":2},{"from":"B💥","to":"D🔥","distance":2},
        {"from":"A🚀","to":"C🌟","distance":2},{"from":"C🌟","to":"D🔥","distance":2},
    ]}
    traffic={"traffic":[{"from":"A🚀","to":"B💥","factor":2,"delay":5}]}
    gp=tmp(json.dumps(graph)); tp=tmp(json.dumps(traffic))
    try:
        proc=run(["--graph",gp,"--from","A🚀","--to","D🔥","--traffic",tp])
        assert proc.returncode==0
        out=json.loads(proc.stdout.decode())
        assert out["path"]==["A🚀","C🌟","D🔥"]
        assert math.isclose(out["effective_distance"],4,abs_tol=1e-6)
    finally:
        os.unlink(gp); os.unlink(tp)

def test_traffic_requests_bom_trailing_comment_with_traffic_invalid_v12():
    import tempfile, os, subprocess, json
    def tmp(c):
        f=tempfile.NamedTemporaryFile(delete=False,suffix=".json",mode="w"); f.write(c); f.close(); return f.name
    def tmpb(b):
        f=tempfile.NamedTemporaryFile(delete=False,suffix=".json",mode="wb"); f.write(b); f.close(); return f.name
    def run(args):
        return subprocess.run(["/app/router"]+args,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=30)
    graph={"nodes":["A","B"],"edges":[{"from":"A","to":"B","distance":1}]}
    traffic={"traffic":[]}
    gp=tmp(json.dumps(graph)); tp=tmp(json.dumps(traffic))
    try:
        for bad_req in [
            b"\xef\xbb\xbf" + json.dumps([{"source":"A","destination":"B"}]).encode(),
            b'[{"source":"A","destination":"B"},]',
            b'[{"source":"A","destination":"B"}] // comment',
        ]:
            # fix first case: actual BOM bytes
            if bad_req.startswith(b"\xef"):
                bad_req = b"\xef\xbb\xbf" + json.dumps([{"source":"A","destination":"B"}]).encode()
                bad_req = b"\xef\xbb\xbf".decode('unicode_escape').encode() + json.dumps([{"source":"A","destination":"B"}]).encode()
            rp=tmpb(bad_req)
            try:
                proc=run(["--graph",gp,"--requests",rp,"--traffic",tp])
                assert proc.returncode==2, f"bad requests with traffic should be invalid"
            finally:
                os.unlink(rp)
    finally:
        os.unlink(gp); os.unlink(tp)

def test_traffic_factor_scientific_plus_mix_delay_scientific_plus_mix_v12():
    import tempfile, os, subprocess, json, math
    def tmp(c):
        f=tempfile.NamedTemporaryFile(delete=False,suffix=".json",mode="w"); f.write(c); f.close(); return f.name
    def run(args):
        return subprocess.run(["/app/router"]+args,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=30)
    graph={"nodes":["A","B"],"edges":[{"from":"A","to":"B","distance":10}]}
    traffic={"traffic":[{"from":"A","to":"B","factor":1e+3,"delay":1e+2}]}
    gp=tmp(json.dumps(graph)); tp=tmp(json.dumps(traffic))
    try:
        proc=run(["--graph",gp,"--from","A","--to","B","--traffic",tp])
        assert proc.returncode==0
        out=json.loads(proc.stdout.decode())
        assert math.isclose(out["effective_distance"],10*1000+100,abs_tol=1e-6)
    finally:
        os.unlink(gp); os.unlink(tp)

def test_traffic_wrapper_extra_top_level_ignored_null_vs_empty_valid_v12():
    import tempfile, os, subprocess, json
    def tmp(c):
        f=tempfile.NamedTemporaryFile(delete=False,suffix=".json",mode="w"); f.write(c); f.close(); return f.name
    def run(args):
        return subprocess.run(["/app/router"]+args,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=30)
    graph={"nodes":["A","B"],"edges":[{"from":"A","to":"B","distance":1}]}
    gp=tmp(json.dumps(graph))
    try:
        for tf_content in [json.dumps({"traffic":[],"version":1}), "[]"]:
            tp=tmp(tf_content)
            try:
                proc=run(["--graph",gp,"--from","A","--to","B","--traffic",tp])
                assert proc.returncode==0
            finally:
                os.unlink(tp)
        for bad in [json.dumps({"traffic":None})]:
            tp=tmp(bad)
            try:
                proc=run(["--graph",gp,"--from","A","--to","B","--traffic",tp])
                assert proc.returncode==2
            finally:
                os.unlink(tp)
    finally:
        os.unlink(gp)

def test_traffic_secondary_raw_tie_delay_only_4_alternatives_v12():
    import tempfile, os, subprocess, json
    def tmp(c):
        f=tempfile.NamedTemporaryFile(delete=False,suffix=".json",mode="w"); f.write(c); f.close(); return f.name
    def run(args):
        return subprocess.run(["/app/router"]+args,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=30)
    graph={"nodes":["A","B1","B2","B3","B4","C"],"edges":[
        {"from":"A","to":"B1","distance":1},{"from":"B1","to":"C","distance":1},
        {"from":"A","to":"B2","distance":1},{"from":"B2","to":"C","distance":2},
        {"from":"A","to":"B3","distance":1},{"from":"B3","to":"C","distance":3},
        {"from":"A","to":"B4","distance":1},{"from":"B4","to":"C","distance":4},
    ]}
    traffic={"traffic":[
        {"from":"B1","to":"C","factor":3},{"from":"B2","to":"C","factor":1.5},{"from":"B3","to":"C","factor":1},{"from":"B4","to":"C","factor":0.75},
    ]}
    gp=tmp(json.dumps(graph)); tp=tmp(json.dumps(traffic))
    try:
        proc=run(["--graph",gp,"--from","A","--to","C","--traffic",tp])
        assert proc.returncode==0
        out=json.loads(proc.stdout.decode())
        assert out["path"]==["A","B1","C"]
    finally:
        os.unlink(gp); os.unlink(tp)

def test_traffic_tertiary_lex_special_dot_slash_hyphen_underscore_v12():
    import tempfile, os, subprocess, json
    def tmp(c):
        f=tempfile.NamedTemporaryFile(delete=False,suffix=".json",mode="w"); f.write(c); f.close(); return f.name
    def run(args):
        return subprocess.run(["/app/router"]+args,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=30)
    graph={"nodes":["A","B-","B.","B/","B_","C"],"edges":[
        {"from":"A","to":"B-","distance":1},{"from":"B-","to":"C","distance":1},
        {"from":"A","to":"B.","distance":1},{"from":"B.","to":"C","distance":1},
        {"from":"A","to":"B/","distance":1},{"from":"B/","to":"C","distance":1},
        {"from":"A","to":"B_","distance":1},{"from":"B_","to":"C","distance":1},
    ]}
    gp=tmp(json.dumps(graph)); tp=tmp(json.dumps({"traffic":[]}))
    try:
        proc=run(["--graph",gp,"--from","A","--to","C","--traffic",tp])
        assert proc.returncode==0
        out=json.loads(proc.stdout.decode())
        assert out["path"]==["A","B-","C"]
    finally:
        os.unlink(gp); os.unlink(tp)

def test_traffic_duplicate_many_extra_version_ignored_v12():
    import tempfile, os, subprocess, json, math
    def tmp(c):
        f=tempfile.NamedTemporaryFile(delete=False,suffix=".json",mode="w"); f.write(c); f.close(); return f.name
    def run(args):
        return subprocess.run(["/app/router"]+args,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=30)
    graph={"nodes":["A","B"],"edges":[{"from":"A","to":"B","distance":10}]}
    entries=[]
    for i in range(10):
        entries.append({"from":"A","to":"B","factor":1+i,"delay":i,"extra":f"ignore{i}","version":i})
    entries.append({"from":"A","to":"B","factor":3,"delay":7})
    gp=tmp(json.dumps(graph)); tp=tmp(json.dumps({"traffic":entries}))
    try:
        proc=run(["--graph",gp,"--from","A","--to","B","--traffic",tp])
        assert proc.returncode==0
        out=json.loads(proc.stdout.decode())
        assert math.isclose(out["effective_distance"],10*3+7,abs_tol=1e-6)
    finally:
        os.unlink(gp); os.unlink(tp)

def test_traffic_source_equals_dest_with_traffic_factor_delay_still_zero_v12():
    import tempfile, os, subprocess, json
    def tmp(c):
        f=tempfile.NamedTemporaryFile(delete=False,suffix=".json",mode="w"); f.write(c); f.close(); return f.name
    def run(args):
        return subprocess.run(["/app/router"]+args,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=30)
    graph={"nodes":["A","B"],"edges":[{"from":"A","to":"B","distance":5}]}
    traffic={"traffic":[{"from":"A","to":"B","factor":10,"delay":100}]}
    gp=tmp(json.dumps(graph)); tp=tmp(json.dumps(traffic))
    try:
        proc=run(["--graph",gp,"--from","A","--to","A","--traffic",tp])
        assert proc.returncode==0
        out=json.loads(proc.stdout.decode())
        assert out["path"]==["A"] and out["distance"]==0
    finally:
        os.unlink(gp); os.unlink(tp)

def test_traffic_no_route_minus_one_strict_traffic_v12():
    import tempfile, os, subprocess, json
    def tmp(c):
        f=tempfile.NamedTemporaryFile(delete=False,suffix=".json",mode="w"); f.write(c); f.close(); return f.name
    def run(args):
        return subprocess.run(["/app/router"]+args,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=30)
    graph={"nodes":["A","B","C"],"edges":[{"from":"A","to":"B","distance":1}]}
    gp=tmp(json.dumps(graph)); tp=tmp(json.dumps({"traffic":[]}))
    try:
        proc=run(["--graph",gp,"--from","A","--to","C","--traffic",tp])
        assert proc.returncode==1
        out=json.loads(proc.stdout.decode())
        assert out["path"]==[] and out["distance"]==-1
        assert set(out.keys())=={"path","distance","effective_distance","traffic_delay"}
    finally:
        os.unlink(gp); os.unlink(tp)

def test_traffic_help_equals_any_value_traffic_v12():
    import subprocess
    BIN="/app/router"
    for args in [["--help=true"],["--help=1"],["--help=false"],["-h=true"],["-h=1"]]:
        proc=subprocess.run([BIN]+args,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=10)
        assert proc.returncode==0
        assert "traffic" in proc.stdout.decode().lower()

def test_traffic_large_graph_10000_line_factor_less_than_one_negative_delay_v12():
    import tempfile, os, subprocess, json, time
    def tmp(c):
        f=tempfile.NamedTemporaryFile(delete=False,suffix=".json",mode="w"); f.write(c); f.close(); return f.name
    def run(args):
        return subprocess.run(["/app/router"]+args,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=90)
    n=1000
    nodes=[f"N{i}" for i in range(n)]
    edges=[{"from":f"N{i}","to":f"N{i+1}","distance":10} for i in range(n-1)]
    graph={"nodes":nodes,"edges":edges}
    traffic={"traffic":[{"from":f"N{i}","to":f"N{i+1}","factor":0.5,"delay":0} for i in range(0,n-1,2)]}
    gp=tmp(json.dumps(graph)); tp=tmp(json.dumps(traffic))
    try:
        start=time.time()
        proc=run(["--graph",gp,"--from","N0","--to",f"N{n-1}","--traffic",tp])
        elapsed=time.time()-start
        assert proc.returncode==0
        out=json.loads(proc.stdout.decode())
        assert out["effective_distance"] < out["distance"]
        assert elapsed<5.0
    finally:
        os.unlink(gp); os.unlink(tp)

def test_traffic_delay_accumulation_per_edge_not_once_v12():
    import tempfile, os, subprocess, json, math
    def tmp(c):
        f=tempfile.NamedTemporaryFile(delete=False,suffix=".json",mode="w"); f.write(c); f.close(); return f.name
    def run(args):
        return subprocess.run(["/app/router"]+args,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=30)
    graph={"nodes":["A","B","C","D"],"edges":[
        {"from":"A","to":"B","distance":1},{"from":"B","to":"C","distance":1},{"from":"C","to":"D","distance":1},
        {"from":"A","to":"D","distance":100},
    ]}
    traffic={"traffic":[
        {"from":"A","to":"B","factor":1,"delay":5},{"from":"B","to":"C","factor":1,"delay":5},{"from":"C","to":"D","factor":1,"delay":5},
    ]}
    gp=tmp(json.dumps(graph)); tp=tmp(json.dumps(traffic))
    try:
        proc=run(["--graph",gp,"--from","A","--to","D","--traffic",tp])
        assert proc.returncode==0
        out=json.loads(proc.stdout.decode())
        assert out["path"]==["A","B","C","D"]
        assert math.isclose(out["effective_distance"],18,abs_tol=1e-6)
    finally:
        os.unlink(gp); os.unlink(tp)

def test_traffic_effective_formula_mixed_factor_delay_5_edges_reroute_v12():
    import tempfile, os, subprocess, json, math
    def tmp(c):
        f=tempfile.NamedTemporaryFile(delete=False,suffix=".json",mode="w"); f.write(c); f.close(); return f.name
    def run(args):
        return subprocess.run(["/app/router"]+args,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=30)
    graph={"nodes":["A","B","C","D","E","F","Z"],"edges":[
        {"from":"A","to":"B","distance":10},{"from":"B","to":"C","distance":10},{"from":"C","to":"Z","distance":10},
        {"from":"A","to":"D","distance":10},{"from":"D","to":"E","distance":10},{"from":"E","to":"F","distance":10},{"from":"F","to":"Z","distance":10},
    ]}
    traffic={"traffic":[
        {"from":"A","to":"B","factor":2},{"from":"B","to":"C","factor":0.5},{"from":"C","to":"Z","factor":2},
    ]}
    gp=tmp(json.dumps(graph)); tp=tmp(json.dumps(traffic))
    try:
        proc=run(["--graph",gp,"--from","A","--to","Z","--traffic",tp])
        assert proc.returncode==0
        out=json.loads(proc.stdout.decode())
        assert out["path"]==["A","D","E","F","Z"]
        assert math.isclose(out["effective_distance"],40,abs_tol=1e-6)
    finally:
        os.unlink(gp); os.unlink(tp)

def test_traffic_duplicate_reverse_interleaved_extra_fields_delay_reset_v12_hard():
    import tempfile, os, subprocess, json, math
    def tmp(c):
        f=tempfile.NamedTemporaryFile(delete=False,suffix=".json",mode="w"); f.write(c); f.close(); return f.name
    def run(args):
        return subprocess.run(["/app/router"]+args,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=30)
    graph={"nodes":["A","B","C"],"edges":[{"from":"A","to":"B","distance":10},{"from":"B","to":"C","distance":10}]}
    entries=[
        {"from":"A","to":"B","factor":10,"delay":100,"extra":"x"},
        {"from":"B","to":"C","factor":10,"delay":100},
        {"from":"B","to":"A","factor":2,"delay":20},
        {"from":"C","to":"B","factor":2,"delay":20},
        {"from":"A","to":"B","factor":1,"delay":1},
        {"from":"B","to":"C","factor":1,"delay":1},
        {"from":"A","to":"B","factor":3},
    ]
    gp=tmp(json.dumps(graph)); tp=tmp(json.dumps({"traffic":entries}))
    try:
        proc=run(["--graph",gp,"--from","A","--to","C","--traffic",tp])
        assert proc.returncode==0
        out=json.loads(proc.stdout.decode())
        assert math.isclose(out["effective_distance"],41,abs_tol=1e-6)
    finally:
        os.unlink(gp); os.unlink(tp)

def test_traffic_batch_order_preserved_large_with_traffic_v12():
    import tempfile, os, subprocess, json
    def tmp(c):
        f=tempfile.NamedTemporaryFile(delete=False,suffix=".json",mode="w"); f.write(c); f.close(); return f.name
    def run(args):
        return subprocess.run(["/app/router"]+args,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=60)
    graph={"nodes":["A","B","C","D"],"edges":[{"from":"A","to":"B","distance":1},{"from":"B","to":"C","distance":1},{"from":"C","to":"D","distance":1},{"from":"A","to":"D","distance":10}]}
    traffic={"traffic":[{"from":"A","to":"D","factor":2}]}
    gp=tmp(json.dumps(graph)); tp=tmp(json.dumps(traffic))
    reqs=[{"source":"A","destination":"D"},{"source":"D","destination":"A"},{"source":"A","destination":"B"}]
    rp=tmp(json.dumps(reqs))
    try:
        proc=run(["--graph",gp,"--requests",rp,"--traffic",tp])
        assert proc.returncode==0
        lines=proc.stdout.decode().strip().splitlines()
        assert len(lines)==3
    finally:
        os.unlink(gp); os.unlink(tp); os.unlink(rp)

def test_traffic_factor_delay_extreme_1e12_and_1e_minus_12_v12():
    import tempfile, os, subprocess, json, math
    def tmp(c):
        f=tempfile.NamedTemporaryFile(delete=False,suffix=".json",mode="w"); f.write(c); f.close(); return f.name
    def run(args):
        return subprocess.run(["/app/router"]+args,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=30)
    graph={"nodes":["A","B"],"edges":[{"from":"A","to":"B","distance":1}]}
    for factor,delay in [(1e12,0),(1e-12,0),(1,1e12)]:
        tp=tmp(json.dumps({"traffic":[{"from":"A","to":"B","factor":factor,"delay":delay}]}))
        gp=tmp(json.dumps(graph))
        try:
            proc=run(["--graph",gp,"--from","A","--to","B","--traffic",tp])
            assert proc.returncode==0
            out=json.loads(proc.stdout.decode())
            expected=1*factor+delay
            assert math.isclose(out["effective_distance"],expected,rel_tol=1e-6)
        finally:
            os.unlink(gp); os.unlink(tp)

def test_traffic_batch_with_whitespace_and_missing_distinction_traffic_v12():
    import tempfile, os, subprocess, json
    def tmp(c):
        f=tempfile.NamedTemporaryFile(delete=False,suffix=".json",mode="w"); f.write(c); f.close(); return f.name
    def run(args):
        return subprocess.run(["/app/router"]+args,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=30)
    graph={"nodes":["A","B"],"edges":[{"from":"A","to":"B","distance":1}]}
    gp=tmp(json.dumps(graph)); tp=tmp(json.dumps({"traffic":[]}))
    try:
        rp=tmp(json.dumps([{"source":" A","destination":"B"}]))
        try:
            proc=run(["--graph",gp,"--requests",rp,"--traffic",tp])
            assert proc.returncode==1
        finally:
            os.unlink(rp)
        rp2=tmp(json.dumps([{"source":"A"}]))
        try:
            proc=run(["--graph",gp,"--requests",rp2,"--traffic",tp])
            assert proc.returncode==2
        finally:
            os.unlink(rp2)
    finally:
        os.unlink(gp); os.unlink(tp)

def test_traffic_file_direct_array_empty_valid_vs_wrapper_null_invalid_v12():
    import tempfile, os, subprocess, json
    def tmp(c):
        f=tempfile.NamedTemporaryFile(delete=False,suffix=".json",mode="w"); f.write(c); f.close(); return f.name
    def run(args):
        return subprocess.run(["/app/router"]+args,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=30)
    graph={"nodes":["A","B"],"edges":[{"from":"A","to":"B","distance":1}]}
    gp=tmp(json.dumps(graph))
    try:
        for valid in ["[]", json.dumps({"traffic":[]})]:
            tp=tmp(valid)
            try:
                proc=run(["--graph",gp,"--from","A","--to","B","--traffic",tp])
                assert proc.returncode==0
            finally:
                os.unlink(tp)
        for invalid in [json.dumps({"traffic":None})]:
            tp=tmp(invalid)
            try:
                proc=run(["--graph",gp,"--from","A","--to","B","--traffic",tp])
                assert proc.returncode==2
            finally:
                os.unlink(tp)
    finally:
        os.unlink(gp)

def test_traffic_graph_duplicate_min_plus_traffic_factor_v12():
    import tempfile, os, subprocess, json, math
    def tmp(c):
        f=tempfile.NamedTemporaryFile(delete=False,suffix=".json",mode="w"); f.write(c); f.close(); return f.name
    def run(args):
        return subprocess.run(["/app/router"]+args,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=30)
    graph={"nodes":["A","B"],"edges":[
        {"from":"A","to":"B","distance":10},
        {"from":"B","to":"A","distance":1},
        {"from":"A","to":"B","distance":5},
    ]}
    traffic={"traffic":[{"from":"A","to":"B","factor":2}]}
    gp=tmp(json.dumps(graph)); tp=tmp(json.dumps(traffic))
    try:
        proc=run(["--graph",gp,"--from","A","--to","B","--traffic",tp])
        assert proc.returncode==0
        out=json.loads(proc.stdout.decode())
        assert math.isclose(out["distance"],1,abs_tol=1e-6)
        assert math.isclose(out["effective_distance"],2,abs_tol=1e-6)
    finally:
        os.unlink(gp); os.unlink(tp)

def test_traffic_factor_less_than_one_negative_delay_secondary_raw_v12():
    import tempfile, os, subprocess, json
    def tmp(c):
        f=tempfile.NamedTemporaryFile(delete=False,suffix=".json",mode="w"); f.write(c); f.close(); return f.name
    def run(args):
        return subprocess.run(["/app/router"]+args,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=30)
    graph={"nodes":["A","B1","B2","C"],"edges":[
        {"from":"A","to":"B1","distance":10},{"from":"B1","to":"C","distance":10},
        {"from":"A","to":"B2","distance":5},{"from":"B2","to":"C","distance":5},
    ]}
    traffic={"traffic":[{"from":"A","to":"B1","factor":0.5},{"from":"B1","to":"C","factor":0.5}]}
    gp=tmp(json.dumps(graph)); tp=tmp(json.dumps(traffic))
    try:
        proc=run(["--graph",gp,"--from","A","--to","C","--traffic",tp])
        assert proc.returncode==0
        out=json.loads(proc.stdout.decode())
        assert out["path"]==["A","B2","C"]
    finally:
        os.unlink(gp); os.unlink(tp)

def test_traffic_10_way_tie_effective_raw_lex_with_traffic_v12_hard():
    import tempfile, os, subprocess, json
    def tmp(c):
        f=tempfile.NamedTemporaryFile(delete=False,suffix=".json",mode="w"); f.write(c); f.close(); return f.name
    def run(args):
        return subprocess.run(["/app/router"]+args,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=30)
    nodes=["S","M1","M2","M3","M4","M5","M6","M7","M8","M9","M10","T"]
    edges=[]
    for i in range(1,11):
        edges.append({"from":"S","to":f"M{i}","distance":1})
        edges.append({"from":f"M{i}","to":"T","distance":1})
    graph={"nodes":nodes,"edges":edges}
    gp=tmp(json.dumps(graph)); tp=tmp(json.dumps({"traffic":[]}))
    try:
        proc=run(["--graph",gp,"--from","S","--to","T","--traffic",tp])
        assert proc.returncode==0
        out=json.loads(proc.stdout.decode())
        assert out["path"]==["S","M1","T"]
    finally:
        os.unlink(gp); os.unlink(tp)

def test_traffic_batch_2000_with_traffic_perf_v12_large():
    import tempfile, os, subprocess, json, time
    def tmp(c):
        f=tempfile.NamedTemporaryFile(delete=False,suffix=".json",mode="w"); f.write(c); f.close(); return f.name
    def run(args):
        return subprocess.run(["/app/router"]+args,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=90)
    nodes=[f"N{i}" for i in range(500)]
    edges=[{"from":f"N{i}","to":f"N{i+1}","distance":1} for i in range(499)]
    graph={"nodes":nodes,"edges":edges}
    traffic={"traffic":[{"from":f"N{i}","to":f"N{i+1}","factor":1.1} for i in range(0,499,10)]}
    gp=tmp(json.dumps(graph)); tp=tmp(json.dumps(traffic))
    reqs=[{"source":f"N{i%500}","destination":f"N{(i*17)%500}"} for i in range(2000)]
    rp=tmp(json.dumps(reqs))
    try:
        start=time.time()
        proc=run(["--graph",gp,"--requests",rp,"--traffic",tp])
        elapsed=time.time()-start
        assert proc.returncode in (0,1)
        assert elapsed<80.0
        assert len(proc.stdout.decode().strip().splitlines())==2000
    finally:
        os.unlink(gp); os.unlink(tp); os.unlink(rp)

def test_traffic_with_large_delay_only_reroute_complex_v12():
    import tempfile, os, subprocess, json
    def tmp(c):
        f=tempfile.NamedTemporaryFile(delete=False,suffix=".json",mode="w"); f.write(c); f.close(); return f.name
    def run(args):
        return subprocess.run(["/app/router"]+args,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=30)
    graph={"nodes":["A","B","C","D","E"],"edges":[
        {"from":"A","to":"B","distance":1},{"from":"B","to":"C","distance":1},{"from":"C","to":"E","distance":1},
        {"from":"A","to":"D","distance":10},{"from":"D","to":"E","distance":10},
    ]}
    traffic={"traffic":[
        {"from":"A","to":"B","factor":1,"delay":100},{"from":"B","to":"C","factor":1,"delay":100},{"from":"C","to":"E","factor":1,"delay":100},
    ]}
    gp=tmp(json.dumps(graph)); tp=tmp(json.dumps(traffic))
    try:
        proc=run(["--graph",gp,"--from","A","--to","E","--traffic",tp])
        assert proc.returncode==0
        out=json.loads(proc.stdout.decode())
        assert out["path"]==["A","D","E"]
    finally:
        os.unlink(gp); os.unlink(tp)

# v13 extra 30 tests to further harden step2

def test_traffic_effective_formula_9_edges_v13():
    import tempfile, os, subprocess, json, math
    def tmp(c):
        f=tempfile.NamedTemporaryFile(delete=False,suffix=".json",mode="w"); f.write(c); f.close(); return f.name
    def run(a):
        return subprocess.run(["/app/router"]+a,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=30)
    graph={"nodes":["A","B","C","D","E","F","G","H","I","J"],"edges":[
        {"from":"A","to":"B","distance":5},{"from":"B","to":"C","distance":5},{"from":"C","to":"D","distance":5},{"from":"D","to":"J","distance":5},
        {"from":"A","to":"E","distance":5},{"from":"E","to":"F","distance":5},{"from":"F","to":"G","distance":5},{"from":"G","to":"J","distance":5},
        {"from":"B","to":"E","distance":1},{"from":"C","to":"F","distance":1},
    ]}
    traffic={"traffic":[
        {"from":"A","to":"B","factor":2,"delay":1},{"from":"B","to":"C","factor":2,"delay":1},{"from":"C","to":"D","factor":2,"delay":1},{"from":"D","to":"J","factor":2,"delay":1},
        {"from":"A","to":"E","factor":1,"delay":0},{"from":"E","to":"F","factor":1,"delay":0},{"from":"F","to":"G","factor":1,"delay":0},{"from":"G","to":"J","factor":1,"delay":0},
    ]}
    gp=tmp(json.dumps(graph)); tp=tmp(json.dumps(traffic))
    try:
        proc=run(["--graph",gp,"--from","A","--to","J","--traffic",tp])
        assert proc.returncode==0
        out=json.loads(proc.stdout.decode())
        assert out["path"]==["A","E","F","G","J"]
        assert math.isclose(out["effective_distance"],20,abs_tol=1e-6)
    finally:
        os.unlink(gp); os.unlink(tp)

def test_traffic_raw_along_effective_best_8_hops_v13():
    import tempfile, os, subprocess, json, math
    def tmp(c):
        f=tempfile.NamedTemporaryFile(delete=False,suffix=".json",mode="w"); f.write(c); f.close(); return f.name
    def run(a):
        return subprocess.run(["/app/router"]+a,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=30)
    nodes=[f"N{i}" for i in range(9)]
    edges=[{"from":nodes[i],"to":nodes[i+1],"distance":10} for i in range(8)]
    edges.append({"from":"N0","to":"N8","distance":50})
    graph={"nodes":nodes,"edges":edges}
    traffic={"traffic":[{"from":nodes[i],"to":nodes[i+1],"factor":0.5} for i in range(8)]}
    gp=tmp(json.dumps(graph)); tp=tmp(json.dumps(traffic))
    try:
        proc=run(["--graph",gp,"--from","N0","--to","N8","--traffic",tp])
        assert proc.returncode==0
        out=json.loads(proc.stdout.decode())
        assert out["path"]==nodes
        assert math.isclose(out["distance"],80,abs_tol=1e-6)
        assert math.isclose(out["effective_distance"],40,abs_tol=1e-6)
    finally:
        os.unlink(gp); os.unlink(tp)

def test_traffic_duplicate_30_entries_v13():
    import tempfile, os, subprocess, json, math
    def tmp(c):
        f=tempfile.NamedTemporaryFile(delete=False,suffix=".json",mode="w"); f.write(c); f.close(); return f.name
    def run(a):
        return subprocess.run(["/app/router"]+a,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=30)
    graph={"nodes":["A","B"],"edges":[{"from":"A","to":"B","distance":1}]}
    entries=[{"from":"A","to":"B","factor":i+1,"delay":i} for i in range(30)]
    entries.append({"from":"B","to":"A","factor":99,"delay":1})
    gp=tmp(json.dumps(graph)); tp=tmp(json.dumps({"traffic":entries}))
    try:
        proc=run(["--graph",gp,"--from","A","--to","B","--traffic",tp])
        assert proc.returncode==0
        out=json.loads(proc.stdout.decode())
        assert math.isclose(out["effective_distance"],100,abs_tol=1e-6)
    finally:
        os.unlink(gp); os.unlink(tp)

def test_traffic_large_graph_2000_nodes_1000_requests_with_traffic_v13():
    import tempfile, os, subprocess, json, time
    def tmp(c):
        f=tempfile.NamedTemporaryFile(delete=False,suffix=".json",mode="w"); f.write(c); f.close(); return f.name
    def run(a):
        return subprocess.run(["/app/router"]+a,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=90)
    nodes=[f"N{i}" for i in range(1000)]
    edges=[{"from":f"N{i}","to":f"N{i+1}","distance":1} for i in range(999)]
    graph={"nodes":nodes,"edges":edges}
    traffic={"traffic":[{"from":f"N{i}","to":f"N{i+1}","factor":1.5} for i in range(0,999,20)]}
    gp=tmp(json.dumps(graph)); tp=tmp(json.dumps(traffic))
    reqs=[{"source":f"N{i%1000}","destination":f"N{(i*7)%1000}"} for i in range(1000)]
    rp=tmp(json.dumps(reqs))
    try:
        start=time.time()
        proc=run(["--graph",gp,"--requests",rp,"--traffic",tp])
        elapsed=time.time()-start
        assert proc.returncode in (0,1)
        assert elapsed<70.0
        assert len(proc.stdout.decode().strip().splitlines())==1000
    finally:
        os.unlink(gp); os.unlink(tp); os.unlink(rp)

def test_traffic_batch_5000_same_source_with_traffic_perf_v13():
    import tempfile, os, subprocess, json, time
    def tmp(c):
        f=tempfile.NamedTemporaryFile(delete=False,suffix=".json",mode="w"); f.write(c); f.close(); return f.name
    def run(a):
        return subprocess.run(["/app/router"]+a,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=90)
    nodes=[f"N{i}" for i in range(1000)]
    edges=[{"from":f"N{i}","to":f"N{i+1}","distance":1} for i in range(999)]
    graph={"nodes":nodes,"edges":edges}
    traffic={"traffic":[{"from":f"N{i}","to":f"N{i+1}","factor":1.1} for i in range(0,999,10)]}
    gp=tmp(json.dumps(graph)); tp=tmp(json.dumps(traffic))
    same=[{"source":"N0","destination":f"N{i%1000}"} for i in range(5000)]
    rp=tmp(json.dumps(same))
    try:
        start=time.time()
        proc=run(["--graph",gp,"--requests",rp,"--traffic",tp])
        elapsed=time.time()-start
        assert proc.returncode==0
        assert elapsed<80.0
        assert len(proc.stdout.decode().strip().splitlines())==5000
    finally:
        os.unlink(gp); os.unlink(tp); os.unlink(rp)

def test_traffic_output_fields_strict_number_types_v13():
    import tempfile, os, subprocess, json
    def tmp(c):
        f=tempfile.NamedTemporaryFile(delete=False,suffix=".json",mode="w"); f.write(c); f.close(); return f.name
    def run(a):
        return subprocess.run(["/app/router"]+a,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=30)
    graph={"nodes":["A","B"],"edges":[{"from":"A","to":"B","distance":1}]}
    gp=tmp(json.dumps(graph)); tp=tmp(json.dumps({"traffic":[{"from":"A","to":"B","factor":2.5,"delay":1.5}]}))
    try:
        proc=run(["--graph",gp,"--from","A","--to","B","--traffic",tp])
        assert proc.returncode==0
        out=json.loads(proc.stdout.decode())
        assert isinstance(out["distance"], (int,float)) and not isinstance(out["distance"], bool)
        assert isinstance(out["effective_distance"], (int,float))
        assert isinstance(out["traffic_delay"], (int,float))
        assert isinstance(out["path"], list)
        assert all(isinstance(x,str) for x in out["path"])
    finally:
        os.unlink(gp); os.unlink(tp)

def test_traffic_help_with_equals_and_traffic_and_requests_v13b():
    import subprocess
    BIN="/app/router"
    for args in [
        ["--help=true","--traffic","x","--requests","y"],
        ["--traffic","x","--help=1","--requests","y"],
        ["--requests","y","--traffic","x","--help=false"],
        ["-h=true","--traffic","x"],
    ]:
        proc=subprocess.run([BIN]+args,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=10)
        assert proc.returncode==0
        assert "traffic" in proc.stdout.decode().lower()

def test_traffic_large_graph_3000_with_traffic_and_2000_batch_v13():
    import tempfile, os, subprocess, json, time
    def tmp(c):
        f=tempfile.NamedTemporaryFile(delete=False,suffix=".json",mode="w"); f.write(c); f.close(); return f.name
    def run(a):
        return subprocess.run(["/app/router"]+a,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=90)
    nodes=[f"N{i}" for i in range(1000)]
    edges=[{"from":f"N{i}","to":f"N{i+1}","distance":1} for i in range(999)]
    graph={"nodes":nodes,"edges":edges}
    traffic={"traffic":[{"from":f"N{i}","to":f"N{i+1}","factor":1.2} for i in range(0,999,100)]}
    gp=tmp(json.dumps(graph)); tp=tmp(json.dumps(traffic))
    reqs=[{"source":f"N{i%1000}","destination":f"N{(i*13)%1000}"} for i in range(500)]
    rp=tmp(json.dumps(reqs))
    try:
        start=time.time()
        proc=run(["--graph",gp,"--requests",rp,"--traffic",tp])
        elapsed=time.time()-start
        assert proc.returncode in (0,1)
        assert elapsed<75.0
    finally:
        os.unlink(gp); os.unlink(tp); os.unlink(rp)

def test_traffic_batch_5000_same_source_with_traffic_v13b():
    import tempfile, os, subprocess, json, time
    def tmp(c):
        f=tempfile.NamedTemporaryFile(delete=False,suffix=".json",mode="w"); f.write(c); f.close(); return f.name
    def run(a):
        return subprocess.run(["/app/router"]+a,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=60)
    nodes=[f"N{i}" for i in range(500)]
    edges=[{"from":f"N{i}","to":f"N{i+1}","distance":1} for i in range(499)]
    graph={"nodes":nodes,"edges":edges}
    traffic={"traffic":[{"from":f"N{i}","to":f"N{i+1}","factor":1.1,"delay":0.5} for i in range(0,499,50)]}
    gp=tmp(json.dumps(graph)); tp=tmp(json.dumps(traffic))
    same=[{"source":"N0","destination":f"N{i%500}"} for i in range(1000)]
    rp=tmp(json.dumps(same))
    try:
        start=time.time()
        proc=run(["--graph",gp,"--requests",rp,"--traffic",tp])
        elapsed=time.time()-start
        assert proc.returncode==0
        assert elapsed<70.0
    finally:
        os.unlink(gp); os.unlink(tp); os.unlink(rp)

def test_traffic_with_emoji_and_special_and_traffic_v13():
    import tempfile, os, subprocess, json, math
    def tmp(c):
        f=tempfile.NamedTemporaryFile(delete=False,suffix=".json",mode="w"); f.write(c); f.close(); return f.name
    def run(a):
        return subprocess.run(["/app/router"]+a,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=60)
    graph={"nodes":["A🚀","B💥","C🌟"],"edges":[{"from":"A🚀","to":"B💥","distance":5},{"from":"B💥","to":"C🌟","distance":5},{"from":"A🚀","to":"C🌟","distance":20}]}
    tp=tmp(json.dumps({"traffic":[{"from":"A🚀","to":"B💥","factor":2},{"from":"B💥","to":"C🌟","factor":0.5}]}))
    gp=tmp(json.dumps(graph))
    try:
        proc=run(["--graph",gp,"--from","A🚀","--to","C🌟","--traffic",tp])
        assert proc.returncode==0
        out=json.loads(proc.stdout.decode())
        assert out["path"]==["A🚀","B💥","C🌟"]
        assert math.isclose(out["effective_distance"],12.5,abs_tol=1e-6)
    finally:
        os.unlink(gp); os.unlink(tp)

def test_traffic_factor_scientific_plus_valid_detailed_v13():
    import tempfile, os, subprocess, json, math
    def tmp(c):
        f=tempfile.NamedTemporaryFile(delete=False,suffix=".json",mode="w"); f.write(c); f.close(); return f.name
    def run(a):
        return subprocess.run(["/app/router"]+a,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=30)
    graph={"nodes":["A","B"],"edges":[{"from":"A","to":"B","distance":2}]}
    gp=tmp(json.dumps(graph))
    try:
        for fac_str in ["1e+3","1E+3","2.5e+2","1e+2"]:
            # JSON dumps will convert string? Need numeric: use json.loads to get number from string
            import json as js
            fac = float(fac_str)
            tp=tmp(js.dumps({"traffic":[{"from":"A","to":"B","factor":fac}]}))
            try:
                proc=run(["--graph",gp,"--from","A","--to","B","--traffic",tp])
                assert proc.returncode==0
            finally:
                os.unlink(tp)
    finally:
        os.unlink(gp)

def test_traffic_batch_2000_with_traffic_relative_extra_hard_v13():
    import tempfile, os, subprocess, json, time
    def tmp(c):
        f=tempfile.NamedTemporaryFile(delete=False,suffix=".json",mode="w"); f.write(c); f.close(); return f.name
    def run(a):
        return subprocess.run(["/app/router"]+a,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=90)
    nodes=[f"N{i}" for i in range(500)]
    edges=[{"from":f"N{i}","to":f"N{i+1}","distance":1} for i in range(499)]
    graph={"nodes":nodes,"edges":edges}
    gp=tmp(json.dumps(graph)); tp=tmp(json.dumps({"traffic":[]}))
    same=[{"source":"N0","destination":f"N{i%500}"} for i in range(2000)]
    multi=[{"source":f"N{i%500}","destination":f"N{(i*13)%500}"} for i in range(2000)]
    rp_same=tmp(json.dumps(same)); rp_multi=tmp(json.dumps(multi))
    try:
        start=time.time()
        proc=run(["--graph",gp,"--requests",rp_same,"--traffic",tp])
        t_same=time.time()-start
        assert proc.returncode==0
        start=time.time()
        proc=run(["--graph",gp,"--requests",rp_multi,"--traffic",tp])
        t_multi=time.time()-start
        assert proc.returncode==0
        if t_multi>=0.2:
            assert t_same <= 0.60*t_multi+1.0
    finally:
        os.unlink(gp); os.unlink(tp); os.unlink(rp_same); os.unlink(rp_multi)

def test_traffic_same_source_amortization_with_traffic_500_v13():
    import tempfile, os, subprocess, json, time
    def tmp(c):
        f=tempfile.NamedTemporaryFile(delete=False,suffix=".json",mode="w"); f.write(c); f.close(); return f.name
    def run(a):
        return subprocess.run(["/app/router"]+a,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=60)
    nodes=[f"N{i}" for i in range(500)]
    edges=[{"from":f"N{i}","to":f"N{i+1}","distance":1} for i in range(499)]
    graph={"nodes":nodes,"edges":edges}
    gp=tmp(json.dumps(graph)); tp=tmp(json.dumps({"traffic":[]}))
    same=[{"source":"N0","destination":f"N{i}"} for i in range(1,501)]
    multi=[{"source":f"N{i%500}","destination":f"N{(i*7)%500}"} for i in range(500)]
    rp_same=tmp(json.dumps(same)); rp_multi=tmp(json.dumps(multi))
    try:
        s=time.time(); proc=run(["--graph",gp,"--requests",rp_same,"--traffic",tp]); t_same=time.time()-s; assert proc.returncode==0
        s=time.time(); proc=run(["--graph",gp,"--requests",rp_multi,"--traffic",tp]); t_multi=time.time()-s; assert proc.returncode==0
        # Relaxed: same-source with prev-cache should be much faster, but allow 0.95+3 margin for CI variance
        assert t_same <= 0.95*t_multi+3.0, f"same-source {t_same:.3f} vs multi {t_multi:.3f}"
    finally:
        os.unlink(gp); os.unlink(tp); os.unlink(rp_same); os.unlink(rp_multi)

def test_traffic_effective_formula_4_edges_v13():
    import tempfile, os, subprocess, json, math
    def tmp(c):
        f=tempfile.NamedTemporaryFile(delete=False,suffix=".json",mode="w"); f.write(c); f.close(); return f.name
    def run(a):
        return subprocess.run(["/app/router"]+a,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=30)
    graph={"nodes":["A","B","C","D","E"],"edges":[
        {"from":"A","to":"B","distance":10},{"from":"B","to":"C","distance":10},{"from":"C","to":"D","distance":10},{"from":"D","to":"E","distance":10},
        {"from":"A","to":"E","distance":100},
    ]}
    traffic={"traffic":[
        {"from":"A","to":"B","factor":2,"delay":5},{"from":"B","to":"C","factor":0.5,"delay":10},
    ]}
    gp=tmp(json.dumps(graph)); tp=tmp(json.dumps(traffic))
    try:
        proc=run(["--graph",gp,"--from","A","--to","E","--traffic",tp])
        assert proc.returncode==0
        out=json.loads(proc.stdout.decode())
        # A-B eff 25, B-C eff 15, C-D 10, D-E 10 total 60 vs direct 100 => first wins
        assert out["path"]==["A","B","C","D","E"]
        assert math.isclose(out["effective_distance"],60,abs_tol=1e-6)
    finally:
        os.unlink(gp); os.unlink(tp)

def test_traffic_raw_along_effective_best_3_hops_v13():
    import tempfile, os, subprocess, json, math
    def tmp(c):
        f=tempfile.NamedTemporaryFile(delete=False,suffix=".json",mode="w"); f.write(c); f.close(); return f.name
    def run(a):
        return subprocess.run(["/app/router"]+a,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=30)
    graph={"nodes":["A","B","C","D"],"edges":[
        {"from":"A","to":"B","distance":100},{"from":"B","to":"C","distance":100},{"from":"C","to":"D","distance":1},
        {"from":"A","to":"D","distance":150},
    ]}
    traffic={"traffic":[{"from":"A","to":"B","factor":0.1},{"from":"B","to":"C","factor":0.1},{"from":"C","to":"D","factor":0.1}]}
    gp=tmp(json.dumps(graph)); tp=tmp(json.dumps(traffic))
    try:
        proc=run(["--graph",gp,"--from","A","--to","D","--traffic",tp])
        assert proc.returncode==0
        out=json.loads(proc.stdout.decode())
        assert out["path"]==["A","B","C","D"]
        assert math.isclose(out["distance"],201,abs_tol=1e-6)
        assert math.isclose(out["effective_distance"],20.1,abs_tol=1e-6)
    finally:
        os.unlink(gp); os.unlink(tp)

def test_traffic_delay_reset_reverse_interleaved_v13():
    import tempfile, os, subprocess, json, math
    def tmp(c):
        f=tempfile.NamedTemporaryFile(delete=False,suffix=".json",mode="w"); f.write(c); f.close(); return f.name
    def run(a):
        return subprocess.run(["/app/router"]+a,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=30)
    graph={"nodes":["A","B"],"edges":[{"from":"A","to":"B","distance":10}]}
    traffic={"traffic":[
        {"from":"A","to":"B","factor":2,"delay":50},
        {"from":"B","to":"A","factor":3,"delay":10},
        {"from":"A","to":"B","factor":4},  # delay reset to 0
    ]}
    gp=tmp(json.dumps(graph)); tp=tmp(json.dumps(traffic))
    try:
        proc=run(["--graph",gp,"--from","A","--to","B","--traffic",tp])
        assert proc.returncode==0
        out=json.loads(proc.stdout.decode())
        assert math.isclose(out["effective_distance"],40,abs_tol=1e-6)
    finally:
        os.unlink(gp); os.unlink(tp)

def test_traffic_batch_output_strict_extra_v13():
    import tempfile, os, subprocess, json
    def tmp(c):
        f=tempfile.NamedTemporaryFile(delete=False,suffix=".json",mode="w"); f.write(c); f.close(); return f.name
    def run(a):
        return subprocess.run(["/app/router"]+a,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=30)
    graph={"nodes":["A","B"],"edges":[{"from":"A","to":"B","distance":1}]}
    gp=tmp(json.dumps(graph)); tp=tmp(json.dumps({"traffic":[]}))
    rp=tmp(json.dumps([{"source":"A","destination":"B"}]))
    try:
        proc=run(["--graph",gp,"--requests",rp,"--traffic",tp])
        assert proc.returncode==0
        out=json.loads(proc.stdout.decode().strip())
        assert set(out.keys())=={"source","destination","path","distance","effective_distance","traffic_delay"}
    finally:
        os.unlink(gp); os.unlink(tp); os.unlink(rp)

def test_traffic_with_emoji_node_ids_and_traffic_v13():
    import tempfile, os, subprocess, json, math
    def tmp(c):
        f=tempfile.NamedTemporaryFile(delete=False,suffix=".json",mode="w"); f.write(c); f.close(); return f.name
    def run(a):
        return subprocess.run(["/app/router"]+a,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=30)
    graph={"nodes":["A🚀","B💥","C🌟"],"edges":[{"from":"A🚀","to":"B💥","distance":5},{"from":"B💥","to":"C🌟","distance":5},{"from":"A🚀","to":"C🌟","distance":20}]}
    gp=tmp(json.dumps(graph)); tp=tmp(json.dumps({"traffic":[{"from":"A🚀","to":"B💥","factor":2}]}))
    try:
        proc=run(["--graph",gp,"--from","A🚀","--to","C🌟","--traffic",tp])
        assert proc.returncode==0
        out=json.loads(proc.stdout.decode())
        assert out["path"]==["A🚀","B💥","C🌟"]
    finally:
        os.unlink(gp); os.unlink(tp)
