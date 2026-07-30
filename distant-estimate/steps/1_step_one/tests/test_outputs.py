import os
import json
import subprocess
import tempfile
import shutil
import time

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
    # try build from /app if go.mod exists
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
    # fallback: try /app/src
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


def write_temp(content, suffix=".json", dir=None):
    tf = tempfile.NamedTemporaryFile(delete=False, suffix=suffix, mode="w", dir=dir)
    tf.write(content)
    tf.close()
    return tf.name


def test_binary_exists():
    assert os.path.exists(BIN), f"binary not found, tried {BINARY_CANDIDATES}"


def test_help_contains_keywords():
    proc = run_router(["--help"])
    assert proc.returncode == 0
    out = proc.stdout.decode().lower()
    for kw in ["graph", "from", "to", "requests", "help"]:
        assert kw in out, f"help missing keyword {kw}: {out[:200]}"


def test_help_bare_no_args():
    proc = run_router([])
    assert proc.returncode == 0
    out = proc.stdout.decode().lower()
    for kw in ["graph", "from", "to", "requests", "help"]:
        assert kw in out


def test_help_h_flag():
    proc = run_router(["-h"])
    assert proc.returncode == 0
    out = proc.stdout.decode().lower()
    assert "graph" in out


def test_simple_shortest_path():
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
        assert proc.returncode == 0, (
            f"rc={proc.returncode} stderr={proc.stderr.decode()}"
        )
        out = json.loads(proc.stdout.decode().strip())
        assert out["path"] == ["A", "B", "C"]
        assert out["distance"] == 8
    finally:
        os.unlink(gpath)


def test_source_equals_dest():
    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 5}]}
    gpath = write_temp(json.dumps(graph))
    try:
        proc = run_router(["--graph", gpath, "--from", "A", "--to", "A"])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip())
        assert out["path"] == ["A"]
        assert out["distance"] == 0
    finally:
        os.unlink(gpath)


def test_no_path():
    graph = {
        "nodes": ["A", "B", "C"],
        "edges": [{"from": "A", "to": "B", "distance": 5}],
    }
    gpath = write_temp(json.dumps(graph))
    try:
        proc = run_router(["--graph", gpath, "--from", "A", "--to", "C"])
        assert proc.returncode == 1, f"expected 1 got {proc.returncode}"
        out = json.loads(proc.stdout.decode().strip())
        assert out["path"] == []
        assert out["distance"] == -1
    finally:
        os.unlink(gpath)


def test_invalid_graph_duplicate_nodes():
    graph = {
        "nodes": ["A", "A", "B"],
        "edges": [{"from": "A", "to": "B", "distance": 5}],
    }
    gpath = write_temp(json.dumps(graph))
    try:
        proc = run_router(["--graph", gpath, "--from", "A", "--to", "B"])
        assert proc.returncode == 2, f"expected 2 got {proc.returncode}"
        assert proc.stdout.decode().strip() == ""
    finally:
        os.unlink(gpath)


def test_invalid_graph_negative_distance():
    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": -5}]}
    gpath = write_temp(json.dumps(graph))
    try:
        proc = run_router(["--graph", gpath, "--from", "A", "--to", "B"])
        assert proc.returncode == 2
        assert proc.stdout.decode().strip() == ""
    finally:
        os.unlink(gpath)


def test_invalid_graph_self_loop():
    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "A", "distance": 5}]}
    gpath = write_temp(json.dumps(graph))
    try:
        proc = run_router(["--graph", gpath, "--from", "A", "--to", "B"])
        assert proc.returncode == 2
        assert proc.stdout.decode().strip() == ""
    finally:
        os.unlink(gpath)


def test_invalid_graph_missing_node_edge():
    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "C", "distance": 5}]}
    gpath = write_temp(json.dumps(graph))
    try:
        proc = run_router(["--graph", gpath, "--from", "A", "--to", "B"])
        assert proc.returncode == 2
    finally:
        os.unlink(gpath)


def test_invalid_graph_empty_node():
    graph = {"nodes": ["A", "", "B"], "edges": []}
    gpath = write_temp(json.dumps(graph))
    try:
        proc = run_router(["--graph", gpath, "--from", "A", "--to", "B"])
        assert proc.returncode == 2
    finally:
        os.unlink(gpath)


def test_tie_breaking_lexicographic():
    # Two equal distance paths A-B-D and A-C-D both distance 10, B<C so A-B-D wins
    graph = {
        "nodes": ["A", "B", "C", "D"],
        "edges": [
            {"from": "A", "to": "B", "distance": 5},
            {"from": "B", "to": "D", "distance": 5},
            {"from": "A", "to": "C", "distance": 5},
            {"from": "C", "to": "D", "distance": 5},
        ],
    }
    gpath = write_temp(json.dumps(graph))
    try:
        proc = run_router(["--graph", gpath, "--from", "A", "--to", "D"])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip())
        assert out["path"] == ["A", "B", "D"], f"got {out['path']}"
        assert out["distance"] == 10
    finally:
        os.unlink(gpath)


def test_batch_mode_all_success():
    graph = {
        "nodes": ["A", "B", "C"],
        "edges": [
            {"from": "A", "to": "B", "distance": 2},
            {"from": "B", "to": "C", "distance": 2},
            {"from": "A", "to": "C", "distance": 10},
        ],
    }
    gpath = write_temp(json.dumps(graph))
    reqs = [{"source": "A", "destination": "C"}, {"source": "A", "destination": "B"}]
    rpath = write_temp(json.dumps(reqs))
    try:
        proc = run_router(["--graph", gpath, "--requests", rpath])
        assert proc.returncode == 0, (
            f"rc={proc.returncode} stderr={proc.stderr.decode()}"
        )
        lines = proc.stdout.decode().strip().splitlines()
        assert len(lines) == 2
        o1 = json.loads(lines[0])
        o2 = json.loads(lines[1])
        assert o1["source"] == "A" and o1["destination"] == "C"
        assert o1["path"] == ["A", "B", "C"]
        assert o1["distance"] == 4
        assert o2["path"] == ["A", "B"]
    finally:
        os.unlink(gpath)
        os.unlink(rpath)


def test_batch_mode_some_no_route():
    graph = {
        "nodes": ["A", "B", "C"],
        "edges": [{"from": "A", "to": "B", "distance": 2}],
    }
    gpath = write_temp(json.dumps(graph))
    reqs = [{"source": "A", "destination": "B"}, {"source": "A", "destination": "C"}]
    rpath = write_temp(json.dumps(reqs))
    try:
        proc = run_router(["--graph", gpath, "--requests", rpath])
        assert proc.returncode == 1
        lines = proc.stdout.decode().strip().splitlines()
        assert len(lines) == 2
        o1 = json.loads(lines[0])
        o2 = json.loads(lines[1])
        assert o1["distance"] == 2
        assert o2["path"] == [] and o2["distance"] == -1
    finally:
        os.unlink(gpath)
        os.unlink(rpath)


def test_batch_invalid_json():
    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 1}]}
    gpath = write_temp(json.dumps(graph))
    rpath = write_temp("{invalid json}")
    try:
        proc = run_router(["--graph", gpath, "--requests", rpath])
        assert proc.returncode == 2
        assert proc.stdout.decode().strip() == ""
    finally:
        os.unlink(gpath)
        os.unlink(rpath)


def test_single_missing_flags():
    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 1}]}
    gpath = write_temp(json.dumps(graph))
    try:
        proc = run_router(["--graph", gpath])
        assert proc.returncode == 2
    finally:
        os.unlink(gpath)


def test_requests_from_to_keys():
    graph = {
        "nodes": ["A", "B", "C"],
        "edges": [
            {"from": "A", "to": "B", "distance": 1},
            {"from": "B", "to": "C", "distance": 1},
        ],
    }
    gpath = write_temp(json.dumps(graph))
    reqs = [{"from": "A", "to": "C"}]
    rpath = write_temp(json.dumps(reqs))
    try:
        proc = run_router(["--graph", gpath, "--requests", rpath])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip().splitlines()[0])
        assert out["path"] == ["A", "B", "C"]
    finally:
        os.unlink(gpath)
        os.unlink(rpath)


def test_empty_requests():
    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 1}]}
    gpath = write_temp(json.dumps(graph))
    rpath = write_temp("[]")
    try:
        proc = run_router(["--graph", gpath, "--requests", rpath])
        assert proc.returncode == 0
        assert proc.stdout.decode().strip() == ""
    finally:
        os.unlink(gpath)
        os.unlink(rpath)


def test_duplicate_edges_min_distance():
    graph = {
        "nodes": ["A", "B"],
        "edges": [
            {"from": "A", "to": "B", "distance": 10},
            {"from": "A", "to": "B", "distance": 3},
        ],
    }
    gpath = write_temp(json.dumps(graph))
    try:
        proc = run_router(["--graph", gpath, "--from", "A", "--to", "B"])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip())
        assert out["distance"] == 3
    finally:
        os.unlink(gpath)


def test_stdlib_only():
    # check go.mod has no external requires and imports are stdlib only
    mod_paths = ["/app/go.mod", "/app/src/go.mod"]
    mod_content = None
    for mp in mod_paths:
        if os.path.exists(mp):
            with open(mp) as f:
                mod_content = f.read()
            break
    if mod_content:
        # ensure no require with github or external
        lines = mod_content.splitlines()
        for line in lines:
            if line.strip().startswith("require"):
                # allow if only standard? require should not exist for stdlib only
                # but if it exists, check if contains '.'
                # Actually go.mod may have require for stdlib? No.
                # We'll assert no external domain
                assert "github.com" not in line and "golang.org/x" not in line, (
                    f"external require found: {line}"
                )


def test_performance_small():
    # 100 nodes linear
    nodes = [f"N{i}" for i in range(100)]
    edges = [{"from": f"N{i}", "to": f"N{i + 1}", "distance": 1} for i in range(99)]
    graph = {"nodes": nodes, "edges": edges}
    gpath = write_temp(json.dumps(graph))
    try:
        start = time.time()
        proc = run_router(["--graph", gpath, "--from", "N0", "--to", "N99"])
        elapsed = time.time() - start
        assert proc.returncode == 0
        assert elapsed < 2.0, f"too slow {elapsed}"
        out = json.loads(proc.stdout.decode().strip())
        assert len(out["path"]) == 100
        assert out["distance"] == 99
    finally:
        os.unlink(gpath)


def test_invalid_graph_file_not_found():
    proc = run_router(["--graph", "/nonexistent/path.json", "--from", "A", "--to", "B"])
    assert proc.returncode == 2
    assert proc.stdout.decode().strip() == ""
