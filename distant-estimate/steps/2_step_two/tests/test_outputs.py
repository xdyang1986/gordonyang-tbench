import os
import json
import subprocess
import tempfile
import time
import math

BINARY_CANDIDATES = [
    "/app/router",
    "/app/src/router",
    "/app/bin/router",
    "./router",
    "/app/router/router",
]


def find_binary():
    for p in BINARY_CANDIDATES:
        if os.path.exists(p) and os.access(p, os.X_OK):
            return p
    if os.path.exists("/app/go.mod"):
        try:
            subprocess.run(
                ["go", "build", "-o", "router", "."],
                cwd="/app",
                check=False,
                timeout=30,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            if os.path.exists("/app/router"):
                return "/app/router"
        except Exception:
            pass
    if os.path.exists("/app/src/go.mod"):
        try:
            subprocess.run(
                ["go", "build", "-o", "router", "."],
                cwd="/app/src",
                check=False,
                timeout=30,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            if os.path.exists("/app/src/router"):
                return "/app/src/router"
        except Exception:
            pass
    return "/app/router"


BIN = find_binary()


def run_router(args, cwd="/tmp"):
    proc = subprocess.run(
        [BIN] + args,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=10,
    )
    return proc


def write_temp(content, suffix=".json"):
    tf = tempfile.NamedTemporaryFile(delete=False, suffix=suffix, mode="w")
    tf.write(content)
    tf.close()
    return tf.name


# === Turn1 backward compat ===
def test_binary_exists():
    assert os.path.exists(BIN)


def test_help_contains_traffic_keyword():
    proc = run_router(["--help"])
    assert proc.returncode == 0
    out = proc.stdout.decode().lower()
    for kw in ["graph", "from", "to", "requests", "traffic", "help"]:
        assert kw in out, f"missing {kw} in help: {out[:300]}"


def test_help_bare():
    proc = run_router([])
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
    gpath = write_temp(json.dumps(graph))
    try:
        proc = run_router(["--graph", gpath, "--from", "A", "--to", "C"])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip())
        assert out["path"] == ["A", "B", "C"]
        assert out["distance"] == 8
    finally:
        os.unlink(gpath)


# === Turn2 specific ===
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
    gpath = write_temp(json.dumps(graph))
    tpath = write_temp(json.dumps(traffic))
    try:
        proc = run_router(
            ["--graph", gpath, "--from", "A", "--to", "C", "--traffic", tpath]
        )
        assert proc.returncode == 0, (
            f"rc={proc.returncode} stderr={proc.stderr.decode()}"
        )
        out = json.loads(proc.stdout.decode().strip())
        assert out["path"] == ["A", "B", "C"], f"got {out['path']}"
        assert out["distance"] == 12
        assert math.isclose(out["effective_distance"], 12, abs_tol=1e-6)
        assert math.isclose(out["traffic_delay"], 0, abs_tol=1e-6)
    finally:
        os.unlink(gpath)
        os.unlink(tpath)


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
    gpath = write_temp(json.dumps(graph))
    tpath = write_temp(json.dumps(traffic))
    try:
        proc = run_router(
            ["--graph", gpath, "--from", "A", "--to", "C", "--traffic", tpath]
        )
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip())
        assert out["path"] == ["A", "B", "C"]
        assert out["distance"] == 20
        assert math.isclose(out["effective_distance"], 30, abs_tol=1e-6)
        assert math.isclose(out["traffic_delay"], 10, abs_tol=1e-6)
    finally:
        os.unlink(gpath)
        os.unlink(tpath)


def test_traffic_factor_less_than_one_negative_delay():
    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 10}]}
    traffic = {"traffic": [{"from": "A", "to": "B", "factor": 0.5}]}
    gpath = write_temp(json.dumps(graph))
    tpath = write_temp(json.dumps(traffic))
    try:
        proc = run_router(
            ["--graph", gpath, "--from", "A", "--to", "B", "--traffic", tpath]
        )
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip())
        assert out["distance"] == 10
        assert math.isclose(out["effective_distance"], 5, abs_tol=1e-6)
        assert math.isclose(out["traffic_delay"], -5, abs_tol=1e-6)
    finally:
        os.unlink(gpath)
        os.unlink(tpath)


def test_traffic_invalid_negative_factor():
    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 5}]}
    traffic = {"traffic": [{"from": "A", "to": "B", "factor": -1}]}
    gpath = write_temp(json.dumps(graph))
    tpath = write_temp(json.dumps(traffic))
    try:
        proc = run_router(
            ["--graph", gpath, "--from", "A", "--to", "B", "--traffic", tpath]
        )
        assert proc.returncode == 2
        assert proc.stdout.decode().strip() == ""
    finally:
        os.unlink(gpath)
        os.unlink(tpath)


def test_traffic_invalid_zero_factor():
    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 5}]}
    traffic = {"traffic": [{"from": "A", "to": "B", "factor": 0}]}
    gpath = write_temp(json.dumps(graph))
    tpath = write_temp(json.dumps(traffic))
    try:
        proc = run_router(
            ["--graph", gpath, "--from", "A", "--to", "B", "--traffic", tpath]
        )
        assert proc.returncode == 2
    finally:
        os.unlink(gpath)
        os.unlink(tpath)


def test_traffic_invalid_nonexisting_edge():
    graph = {
        "nodes": ["A", "B", "C"],
        "edges": [{"from": "A", "to": "B", "distance": 5}],
    }
    traffic = {"traffic": [{"from": "B", "to": "C", "factor": 2}]}
    gpath = write_temp(json.dumps(graph))
    tpath = write_temp(json.dumps(traffic))
    try:
        proc = run_router(
            ["--graph", gpath, "--from", "A", "--to", "B", "--traffic", tpath]
        )
        assert proc.returncode == 2
    finally:
        os.unlink(gpath)
        os.unlink(tpath)


def test_traffic_invalid_self_loop():
    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 5}]}
    traffic = {"traffic": [{"from": "A", "to": "A", "factor": 2}]}
    gpath = write_temp(json.dumps(graph))
    tpath = write_temp(json.dumps(traffic))
    try:
        proc = run_router(
            ["--graph", gpath, "--from", "A", "--to", "B", "--traffic", tpath]
        )
        assert proc.returncode == 2
    finally:
        os.unlink(gpath)
        os.unlink(tpath)


def test_traffic_invalid_json():
    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 5}]}
    gpath = write_temp(json.dumps(graph))
    tpath = write_temp("{invalid}")
    try:
        proc = run_router(
            ["--graph", gpath, "--from", "A", "--to", "B", "--traffic", tpath]
        )
        assert proc.returncode == 2
    finally:
        os.unlink(gpath)
        os.unlink(tpath)


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
    gpath = write_temp(json.dumps(graph))
    tpath = write_temp(json.dumps(traffic))
    reqs = [{"source": "A", "destination": "C"}]
    rpath = write_temp(json.dumps(reqs))
    try:
        proc = run_router(["--graph", gpath, "--requests", rpath, "--traffic", tpath])
        assert proc.returncode == 0, f"stderr={proc.stderr.decode()}"
        lines = proc.stdout.decode().strip().splitlines()
        assert len(lines) == 1
        out = json.loads(lines[0])
        assert out["source"] == "A"
        assert "effective_distance" in out and "traffic_delay" in out
        assert math.isclose(out["effective_distance"], 15, abs_tol=1e-6)
    finally:
        os.unlink(gpath)
        os.unlink(tpath)
        os.unlink(rpath)


def test_traffic_source_equals_dest():
    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 5}]}
    traffic = {"traffic": [{"from": "A", "to": "B", "factor": 10}]}
    gpath = write_temp(json.dumps(graph))
    tpath = write_temp(json.dumps(traffic))
    try:
        proc = run_router(
            ["--graph", gpath, "--from", "A", "--to", "A", "--traffic", tpath]
        )
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip())
        assert out["path"] == ["A"]
        assert out["distance"] == 0
        assert out["effective_distance"] == 0
        assert out["traffic_delay"] == 0
    finally:
        os.unlink(gpath)
        os.unlink(tpath)


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
    gpath = write_temp(json.dumps(graph))
    tpath = write_temp(json.dumps(traffic))
    try:
        proc = run_router(
            ["--graph", gpath, "--from", "A", "--to", "D", "--traffic", tpath]
        )
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip())
        assert out["path"] == ["A", "B", "D"], f"got {out['path']}"
    finally:
        os.unlink(gpath)
        os.unlink(tpath)


def test_traffic_duplicate_last_wins():
    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 10}]}
    traffic = {
        "traffic": [
            {"from": "A", "to": "B", "factor": 2},
            {"from": "A", "to": "B", "factor": 0.5},
        ]
    }
    gpath = write_temp(json.dumps(graph))
    tpath = write_temp(json.dumps(traffic))
    try:
        proc = run_router(
            ["--graph", gpath, "--from", "A", "--to", "B", "--traffic", tpath]
        )
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip())
        assert math.isclose(out["effective_distance"], 5, abs_tol=1e-6), (
            f"got {out['effective_distance']}"
        )
    finally:
        os.unlink(gpath)
        os.unlink(tpath)


def test_traffic_direct_array_format():
    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 10}]}
    traffic_arr = [{"from": "A", "to": "B", "factor": 2}]
    gpath = write_temp(json.dumps(graph))
    tpath = write_temp(json.dumps(traffic_arr))
    try:
        proc = run_router(
            ["--graph", gpath, "--from", "A", "--to", "B", "--traffic", tpath]
        )
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip())
        assert math.isclose(out["effective_distance"], 20, abs_tol=1e-6)
    finally:
        os.unlink(gpath)
        os.unlink(tpath)


def test_single_with_traffic_no_path():
    graph = {
        "nodes": ["A", "B", "C"],
        "edges": [{"from": "A", "to": "B", "distance": 1}],
    }
    traffic = {"traffic": [{"from": "A", "to": "B", "factor": 1}]}
    gpath = write_temp(json.dumps(graph))
    tpath = write_temp(json.dumps(traffic))
    try:
        proc = run_router(
            ["--graph", gpath, "--from", "A", "--to", "C", "--traffic", tpath]
        )
        assert proc.returncode == 1
        out = json.loads(proc.stdout.decode().strip())
        assert out["path"] == []
        assert out["distance"] == -1
        assert out["effective_distance"] == -1
        assert out["traffic_delay"] == -1
    finally:
        os.unlink(gpath)
        os.unlink(tpath)


def test_batch_with_traffic_some_no_route():
    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 1}]}
    traffic = {"traffic": [{"from": "A", "to": "B", "factor": 2}]}
    gpath = write_temp(json.dumps(graph))
    tpath = write_temp(json.dumps(traffic))
    reqs = [{"source": "A", "destination": "B"}, {"source": "A", "destination": "C"}]
    rpath = write_temp(json.dumps(reqs))
    try:
        proc = run_router(["--graph", gpath, "--requests", rpath, "--traffic", tpath])
        assert proc.returncode == 1
        lines = proc.stdout.decode().strip().splitlines()
        assert len(lines) == 2
        o2 = json.loads(lines[1])
        assert o2["distance"] == -1
        assert o2["effective_distance"] == -1
    finally:
        os.unlink(gpath)
        os.unlink(tpath)
        os.unlink(rpath)


def test_performance_with_traffic():
    nodes = [f"N{i}" for i in range(100)]
    edges = [{"from": f"N{i}", "to": f"N{i + 1}", "distance": 1} for i in range(99)]
    traffic = {
        "traffic": [
            {"from": f"N{i}", "to": f"N{i + 1}", "factor": 1.1} for i in range(99)
        ]
    }
    graph = {"nodes": nodes, "edges": edges}
    gpath = write_temp(json.dumps(graph))
    tpath = write_temp(json.dumps(traffic))
    try:
        start = time.time()
        proc = run_router(
            ["--graph", gpath, "--from", "N0", "--to", "N99", "--traffic", tpath]
        )
        elapsed = time.time() - start
        assert proc.returncode == 0
        assert elapsed < 2.0
        out = json.loads(proc.stdout.decode().strip())
        assert len(out["path"]) == 100
    finally:
        os.unlink(gpath)
        os.unlink(tpath)


def test_invalid_graph_still_exit2_with_traffic():
    graph = {"nodes": ["A", "A"], "edges": []}
    traffic = {"traffic": []}
    gpath = write_temp(json.dumps(graph))
    tpath = write_temp(json.dumps(traffic))
    try:
        proc = run_router(
            ["--graph", gpath, "--from", "A", "--to", "B", "--traffic", tpath]
        )
        assert proc.returncode == 2
        assert proc.stdout.decode().strip() == ""
    finally:
        os.unlink(gpath)
        os.unlink(tpath)
