import os, json, subprocess, tempfile, time, math

CANDIDATES = ["/app/router"]


def find_bin():
    try:
        if os.path.exists("/app/router"):
            os.unlink("/app/router")
    except Exception:
        pass
    if os.path.exists("/app/go.mod"):
        result = subprocess.run(["go", "build", "-o", "router", "."], cwd="/app", timeout=90, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if result.returncode != 0:
            # build must succeed for valid task
            pass
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
    assert os.path.exists(BIN)
    with open(BIN, "rb") as f:
        head = f.read(4)
    assert head == b"\x7fELF"
    assert not head.startswith(b"#!")
    assert os.path.getsize(BIN) > 500_000
    with open(BIN, "rb") as f:
        data = f.read(2_000_000)
    assert b"Go" in data or b"main.main" in data or b"runtime." in data


def test_help_contains_keywords():
    proc = run(["--help"])
    assert proc.returncode == 0
    out = proc.stdout.decode().lower()
    for kw in ["graph", "from", "to", "requests", "help"]:
        assert kw in out
    assert "traffic" not in out


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


def test_help_positional():
    proc = run(["help"])
    assert proc.returncode == 0
    assert "graph" in proc.stdout.decode().lower()


def test_help_equals_syntax():
    for arg in ["--help=true", "--help=1", "-h=true"]:
        proc = run([arg])
        assert proc.returncode == 0, f"{arg} should be help"
        assert "graph" in proc.stdout.decode().lower()


def test_help_precedence_extra_hard():
    proc = run(["--help", "--unknown", "--graph", "/nonexistent"])
    assert proc.returncode == 0
    assert "graph" in proc.stdout.decode().lower()
    proc2 = run(["-h", "--unknown=foo"])
    assert proc2.returncode == 0


def test_help_with_graph_flag_still_help():
    gp = tmp(
        json.dumps(
            {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 1}]}
        )
    )
    try:
        proc = run(["--graph", gp, "--help"])
        assert proc.returncode == 0
    finally:
        os.unlink(gp)


def test_help_with_extra_invalid_flags_still_help():
    proc = run(["--help", "--unknown", "--bad"])
    assert proc.returncode == 0


def test_help_must_not_contain_traffic_in_turn1():
    proc = run(["--help"])
    assert proc.returncode == 0
    assert "traffic" not in proc.stdout.decode().lower()


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


def test_float_scientific_notation_distance():
    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 1e3}]}
    gp = tmp(json.dumps(graph))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B"])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip())
        assert math.isclose(out["distance"], 1000, abs_tol=1e-6)
    finally:
        os.unlink(gp)


def test_scientific_notation_plus_valid():
    content = '{"nodes":["A","B"],"edges":[{"from":"A","to":"B","distance":1e+3}]}'
    gp = tmp(content)
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B"])
        assert proc.returncode == 0, proc.stderr.decode()
        out = json.loads(proc.stdout.decode().strip())
        assert math.isclose(out["distance"], 1000, abs_tol=1e-6)
    finally:
        os.unlink(gp)


def test_distance_scientific_negative_exponent():
    content = '{"nodes":["A","B"],"edges":[{"from":"A","to":"B","distance":1e-3}]}'
    gp = tmp(content)
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B"])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip())
        assert math.isclose(out["distance"], 0.001, abs_tol=1e-9)
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
        assert proc.returncode == 2
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
        assert proc.returncode == 2 and proc.stdout.decode().strip() == ""
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
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip())
        assert out["path"] == ["A", "B"] and out["distance"] == 5
    finally:
        os.unlink(gp)


def test_invalid_graph_file_not_found():
    proc = run(["--graph", "/nonexistent/path.json", "--from", "A", "--to", "B"])
    assert proc.returncode == 2 and proc.stdout.decode().strip() == ""


def test_invalid_graph_json_trailing_comma():
    gp = tmp('{"nodes":["A","B"],"edges":[{"from":"A","to":"B","distance":5},]}')
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B"])
        assert proc.returncode == 2
    finally:
        os.unlink(gp)


def test_invalid_graph_json_comment():
    gp = tmp(
        '// comment\n{"nodes":["A","B"],"edges":[{"from":"A","to":"B","distance":5}]}'
    )
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B"])
        assert proc.returncode == 2
    finally:
        os.unlink(gp)


def test_invalid_graph_json_bom():
    gp = tmp('\ufeff{"nodes":["A","B"],"edges":[{"from":"A","to":"B","distance":5}]}')
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B"])
        assert proc.returncode == 2
    finally:
        os.unlink(gp)


def test_graph_top_not_object_invalid():
    gp = tmp('["A","B"]')
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B"])
        assert proc.returncode == 2
    finally:
        os.unlink(gp)


def test_graph_nodes_not_list_invalid():
    gp = tmp('{"nodes":"A","edges":[]}')
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B"])
        assert proc.returncode == 2
    finally:
        os.unlink(gp)


def test_edges_not_list_invalid():
    gp = tmp('{"nodes":["A","B"],"edges":"no"}')
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B"])
        assert proc.returncode == 2
    finally:
        os.unlink(gp)


def test_graph_nodes_empty_array():
    gp = tmp('{"nodes":[],"edges":[]}')
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B"])
        assert proc.returncode == 2
    finally:
        os.unlink(gp)


def test_invalid_graph_nodes_contain_non_string():
    gp = tmp('{"nodes":["A",123,"B"],"edges":[]}')
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B"])
        assert proc.returncode == 2
    finally:
        os.unlink(gp)


def test_invalid_graph_edges_contain_non_object():
    gp = tmp('{"nodes":["A","B"],"edges":[123]}')
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B"])
        assert proc.returncode == 2
    finally:
        os.unlink(gp)


def test_invalid_graph_edge_missing_fields():
    gp = tmp('{"nodes":["A","B"],"edges":[{"from":"A"}]}')
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B"])
        assert proc.returncode == 2
    finally:
        os.unlink(gp)


def test_invalid_graph_edge_from_to_not_string():
    gp = tmp('{"nodes":["A","B"],"edges":[{"from":123,"to":"B","distance":5}]}')
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B"])
        assert proc.returncode == 2
    finally:
        os.unlink(gp)


def test_edge_missing_distance_invalid():
    gp = tmp('{"nodes":["A","B"],"edges":[{"from":"A","to":"B"}]}')
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B"])
        assert proc.returncode == 2
    finally:
        os.unlink(gp)


def test_edge_string_distance_invalid():
    gp = tmp('{"nodes":["A","B"],"edges":[{"from":"A","to":"B","distance":"5"}]}')
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B"])
        assert proc.returncode == 2
    finally:
        os.unlink(gp)


def test_edge_with_leading_trailing_space_invalid():
    graph = {"nodes": ["A", "B"], "edges": [{"from": " A", "to": "B", "distance": 1}]}
    gp = tmp(json.dumps(graph))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B"])
        assert proc.returncode == 2
    finally:
        os.unlink(gp)


def test_node_id_with_leading_space_distinct_valid():
    graph = {
        "nodes": ["A", " A", "B"],
        "edges": [
            {"from": " A", "to": "B", "distance": 1},
            {"from": "A", "to": "B", "distance": 2},
        ],
    }
    gp = tmp(json.dumps(graph))
    try:
        proc = run(["--graph", gp, "--from", " A", "--to", "B"])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip())
        assert out["path"] == [" A", "B"]
    finally:
        os.unlink(gp)


def test_from_with_leading_space_no_route_single():
    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 1}]}
    gp = tmp(json.dumps(graph))
    try:
        proc = run(["--graph", gp, "--from", " A", "--to", "B"])
        assert proc.returncode == 1
        out = json.loads(proc.stdout.decode().strip())
        assert out["path"] == [] and out["distance"] == -1
    finally:
        os.unlink(gp)


def test_query_non_existing_node_no_route():
    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 1}]}
    gp = tmp(json.dumps(graph))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "Z"])
        assert proc.returncode == 1
        out = json.loads(proc.stdout.decode().strip())
        assert out["path"] == [] and out["distance"] == -1
    finally:
        os.unlink(gp)


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


def test_duplicate_edges_reverse_min():
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
        assert out["distance"] == 2
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


def test_node_ids_dot_slash_hyphen_underscore_valid():
    graph = {
        "nodes": ["A-1", "A_2", "A.3", "A/B"],
        "edges": [
            {"from": "A-1", "to": "A_2", "distance": 1},
            {"from": "A_2", "to": "A.3", "distance": 1},
            {"from": "A.3", "to": "A/B", "distance": 1},
        ],
    }
    gp = tmp(json.dumps(graph))
    try:
        proc = run(["--graph", gp, "--from", "A-1", "--to", "A/B"])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip())
        assert out["distance"] == 3
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
        assert out["path"] == ["A", "B", "D"]
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
        assert out["path"] == ["A", "B", "D"]
    finally:
        os.unlink(gp)


def test_5_way_tie_break():
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
    gp = tmp(json.dumps(graph))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "F"])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip())
        assert out["path"] == ["A", "B", "F"]
    finally:
        os.unlink(gp)


def test_10_way_tie_break():
    dest = "Z"
    mids = [chr(ord("B") + i) for i in range(10)]
    nodes = ["A", dest] + mids
    edges = []
    for c in mids:
        edges.append({"from": "A", "to": c, "distance": 5})
        edges.append({"from": c, "to": dest, "distance": 5})
    graph = {"nodes": nodes, "edges": edges}
    gp = tmp(json.dumps(graph))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", dest])
        assert proc.returncode == 0, proc.stderr.decode()
        out = json.loads(proc.stdout.decode().strip())
        assert out["path"] == ["A", "B", dest]
    finally:
        os.unlink(gp)


def test_lexicographic_deeper_tie():
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
        assert out["path"] == ["A", "B1", "B2", "Z"]
    finally:
        os.unlink(gp)


def test_lexicographic_case_sensitive_ascii():
    graph = {
        "nodes": ["A", "A-1", "A.B", "A_2", "Z"],
        "edges": [
            {"from": "A", "to": "A-1", "distance": 1},
            {"from": "A", "to": "A.B", "distance": 1},
            {"from": "A", "to": "A_2", "distance": 1},
            {"from": "A-1", "to": "Z", "distance": 1},
            {"from": "A.B", "to": "Z", "distance": 1},
            {"from": "A_2", "to": "Z", "distance": 1},
        ],
    }
    gp = tmp(json.dumps(graph))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "Z"])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip())
        assert out["path"] == ["A", "A-1", "Z"]
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
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip().splitlines()[0])
        assert out["path"] == ["A", "B", "C"]
    finally:
        os.unlink(gp)
        os.unlink(rp)


def test_empty_source_in_batch_no_route():
    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 5}]}
    gp = tmp(json.dumps(graph))
    rp = tmp(
        json.dumps(
            [{"source": "", "destination": "B"}, {"source": "A", "destination": "B"}]
        )
    )
    try:
        proc = run(["--graph", gp, "--requests", rp])
        assert proc.returncode == 1
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
    gp = tmp(json.dumps(graph))
    rp = tmp(json.dumps([{"source": "A", "destination": "C", "from": "A", "to": "B"}]))
    try:
        proc = run(["--graph", gp, "--requests", rp])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip().splitlines()[0])
        assert out["path"] == ["A", "B", "C"]
    finally:
        os.unlink(gp)
        os.unlink(rp)


def test_request_order_preserved_with_no_route():
    graph = {
        "nodes": ["A", "B", "C"],
        "edges": [{"from": "A", "to": "B", "distance": 1}],
    }
    gp = tmp(json.dumps(graph))
    reqs = [
        {"source": "A", "destination": "B"},
        {"source": "A", "destination": "C"},
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
        assert o1["path"] == []
        assert o2["path"] == ["A", "B"]
    finally:
        os.unlink(gp)
        os.unlink(rp)


def test_single_mode_empty_from_invalid():
    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 1}]}
    gp = tmp(json.dumps(graph))
    try:
        proc = run(["--graph", gp, "--from", "", "--to", "B"])
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
        proc = run(["--graph", gp, "--from", "A", "--to", "B", "--unknown", "flag"])
        assert proc.returncode == 2
        assert proc.stdout.decode().strip() == ""
    finally:
        os.unlink(gp)


def test_flag_missing_value_invalid():
    proc = run(["--graph"])
    assert proc.returncode == 2
    assert proc.stdout.decode().strip() == ""


def test_flag_order_independence():
    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 1}]}
    gp = tmp(json.dumps(graph))
    try:
        proc = run(["--from", "A", "--graph", gp, "--to", "B"])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip())
        assert out["path"] == ["A", "B"]
    finally:
        os.unlink(gp)


def test_from_to_equals_syntax():
    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 1}]}
    gp = tmp(json.dumps(graph))
    try:
        proc = run([f"--graph={gp}", "--from=A", "--to=B"])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip())
        assert out["path"] == ["A", "B"]
    finally:
        os.unlink(gp)


def test_unknown_traffic_flag_in_step1_invalid():
    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 1}]}
    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps({"traffic": [{"from": "A", "to": "B", "factor": 2}]}))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B", "--traffic", tp])
        assert proc.returncode == 2
        assert proc.stdout.decode().strip() == ""
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_batch_with_not_string_source_invalid():
    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 1}]}
    gp = tmp(json.dumps(graph))
    rp = tmp(json.dumps([{"source": 123, "destination": "B"}]))
    try:
        proc = run(["--graph", gp, "--requests", rp])
        assert proc.returncode == 2
    finally:
        os.unlink(gp)
        os.unlink(rp)


def test_output_fields_strict_single():
    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 5}]}
    gp = tmp(json.dumps(graph))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B"])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip())
        assert set(out.keys()) == {"path", "distance"}
        assert isinstance(out["path"], list)
        assert isinstance(out["distance"], (int, float))
    finally:
        os.unlink(gp)


def test_output_fields_strict_batch():
    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 5}]}
    gp = tmp(json.dumps(graph))
    rp = tmp(json.dumps([{"source": "A", "destination": "B"}]))
    try:
        proc = run(["--graph", gp, "--requests", rp])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip().splitlines()[0])
        assert set(out.keys()) == {"source", "destination", "path", "distance"}
    finally:
        os.unlink(gp)
        os.unlink(rp)


def test_large_batch_100_requests():
    nodes = [f"N{i}" for i in range(20)]
    edges = [{"from": f"N{i}", "to": f"N{i + 1}", "distance": 1} for i in range(19)]
    graph = {"nodes": nodes, "edges": edges}
    gp = tmp(json.dumps(graph))
    reqs = [{"source": "N0", "destination": f"N{i}"} for i in range(20)] * 5
    rp = tmp(json.dumps(reqs))
    rp1 = tmp(json.dumps([{"source": "N0", "destination": "N19"}]))
    try:
        start = time.time()
        base = run(["--graph", gp, "--requests", rp1])
        base_elapsed = time.time() - start
        assert base.returncode == 0
        start = time.time()
        proc = run(["--graph", gp, "--requests", rp])
        elapsed = time.time() - start
        assert proc.returncode == 0
        assert elapsed <= 25 * base_elapsed + 1.0
        lines = proc.stdout.decode().strip().splitlines()
        assert len(lines) == 100
    finally:
        os.unlink(gp)
        os.unlink(rp)
        os.unlink(rp1)


def test_performance_500_nodes():
    nodes = [f"N{i}" for i in range(200)]
    edges = [{"from": f"N{i}", "to": f"N{i + 1}", "distance": 1} for i in range(199)]
    graph = {"nodes": nodes, "edges": edges}
    gp = tmp(json.dumps(graph))
    try:
        proc = run(["--graph", gp, "--from", "N0", "--to", "N199"])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip())
        assert out["distance"] == 199
    finally:
        os.unlink(gp)


def test_large_graph_1000_nodes_performance():
    nodes = [f"N{i}" for i in range(1000)]
    edges = [{"from": f"N{i}", "to": f"N{i + 1}", "distance": 1} for i in range(999)]
    graph = {"nodes": nodes, "edges": edges}
    gp = tmp(json.dumps(graph))
    base_graph = {
        "nodes": [f"N{i}" for i in range(20)],
        "edges": [
            {"from": f"N{i}", "to": f"N{i + 1}", "distance": 1} for i in range(19)
        ],
    }
    base_gp = tmp(json.dumps(base_graph))
    try:
        start = time.time()
        base = run(["--graph", base_gp, "--from", "N0", "--to", "N19"])
        base_elapsed = time.time() - start
        assert base.returncode == 0
        start = time.time()
        proc = run(["--graph", gp, "--from", "N0", "--to", "N999"])
        elapsed = time.time() - start
        assert proc.returncode == 0
        assert elapsed <= 25 * base_elapsed + 1.0
        out = json.loads(proc.stdout.decode().strip())
        assert out["distance"] <= 999
    finally:
        os.unlink(gp)
        os.unlink(base_gp)


def test_large_graph_2000_nodes():
    nodes = [f"N{i}" for i in range(2000)]
    edges = [{"from": f"N{i}", "to": f"N{i + 1}", "distance": 1} for i in range(1999)]
    graph = {"nodes": nodes, "edges": edges}
    gp = tmp(json.dumps(graph))
    base_graph = {
        "nodes": [f"N{i}" for i in range(20)],
        "edges": [
            {"from": f"N{i}", "to": f"N{i + 1}", "distance": 1} for i in range(19)
        ],
    }
    base_gp = tmp(json.dumps(base_graph))
    try:
        start = time.time()
        base = run(["--graph", base_gp, "--from", "N0", "--to", "N19"])
        base_elapsed = time.time() - start
        assert base.returncode == 0
        start = time.time()
        proc = run(["--graph", gp, "--from", "N0", "--to", "N1999"])
        elapsed = time.time() - start
        assert proc.returncode == 0
        assert elapsed <= 25 * base_elapsed + 1.0
    finally:
        os.unlink(gp)
        os.unlink(base_gp)


def test_large_graph_5000_nodes_performance():
    nodes = [f"N{i}" for i in range(5000)]
    edges = [{"from": f"N{i}", "to": f"N{i + 1}", "distance": 1} for i in range(4999)]
    graph = {"nodes": nodes, "edges": edges}
    gp = tmp(json.dumps(graph))
    base_graph = {
        "nodes": [f"N{i}" for i in range(20)],
        "edges": [
            {"from": f"N{i}", "to": f"N{i + 1}", "distance": 1} for i in range(19)
        ],
    }
    base_gp = tmp(json.dumps(base_graph))
    try:
        start = time.time()
        base = run(["--graph", base_gp, "--from", "N0", "--to", "N19"])
        base_elapsed = time.time() - start
        assert base.returncode == 0
        start = time.time()
        proc = run(["--graph", gp, "--from", "N0", "--to", "N4999"])
        elapsed = time.time() - start
        assert proc.returncode == 0
        assert elapsed <= 25 * base_elapsed + 2.0
    finally:
        os.unlink(gp)
        os.unlink(base_gp)


def test_large_batch_500_requests():
    nodes = [f"N{i}" for i in range(100)]
    edges = [{"from": f"N{i}", "to": f"N{i + 1}", "distance": 1} for i in range(99)]
    graph = {"nodes": nodes, "edges": edges}
    gp = tmp(json.dumps(graph))
    reqs = [{"source": "N0", "destination": f"N{i % 100}"} for i in range(500)]
    rp = tmp(json.dumps(reqs))
    rp1 = tmp(json.dumps([{"source": "N0", "destination": "N99"}]))
    try:
        start = time.time()
        base = run(["--graph", gp, "--requests", rp1])
        base_elapsed = time.time() - start
        assert base.returncode == 0
        start = time.time()
        proc = run(["--graph", gp, "--requests", rp])
        elapsed = time.time() - start
        assert proc.returncode == 0
        assert elapsed <= 25 * base_elapsed + 1.0
        lines = proc.stdout.decode().strip().splitlines()
        assert len(lines) == 500
    finally:
        os.unlink(gp)
        os.unlink(rp)
        os.unlink(rp1)


def test_performance_dense_5000_edges():
    nodes = [f"N{i}" for i in range(100)]
    edges = []
    for i in range(100):
        for j in range(i + 1, min(i + 6, 100)):
            edges.append({"from": f"N{i}", "to": f"N{j}", "distance": 1})
    graph = {"nodes": nodes, "edges": edges}
    gp = tmp(json.dumps(graph))
    base_graph = {
        "nodes": [f"N{i}" for i in range(20)],
        "edges": [
            {"from": f"N{i}", "to": f"N{i + 1}", "distance": 1} for i in range(19)
        ],
    }
    base_gp = tmp(json.dumps(base_graph))
    try:
        start = time.time()
        base = run(["--graph", base_gp, "--from", "N0", "--to", "N19"])
        base_elapsed = time.time() - start
        assert base.returncode == 0
        start = time.time()
        proc = run(["--graph", gp, "--from", "N0", "--to", "N99"])
        elapsed = time.time() - start
        assert proc.returncode == 0
        assert elapsed <= 25 * base_elapsed + 1.0
    finally:
        os.unlink(gp)
        os.unlink(base_gp)


def test_stdlib_only_go_mod_no_require():
    go_mod_path = "/app/go.mod"
    if os.path.exists(go_mod_path):
        with open(go_mod_path) as f:
            content = f.read()
        for line in content.splitlines():
            stripped = line.strip()
            if stripped.startswith("require"):
                assert False, (
                    f"go.mod must not have require block for stdlib-only: {content}"
                )


def test_duplicate_edge_last_record_wins_when_larger():
    graph = {
        "nodes": ["A", "B"],
        "edges": [
            {"from": "A", "to": "B", "distance": 3},
            {"from": "A", "to": "B", "distance": 10},
        ],
    }
    gp = tmp(json.dumps(graph))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B"])
        assert proc.returncode == 0, proc.stderr.decode()
        out = json.loads(proc.stdout.decode().strip())
        assert out["path"] == ["A", "B"]
        assert out["distance"] == 10
    finally:
        os.unlink(gp)


def test_duplicate_reverse_edge_last_record_wins_when_larger():
    graph = {
        "nodes": ["A", "B"],
        "edges": [
            {"from": "A", "to": "B", "distance": 3},
            {"from": "B", "to": "A", "distance": 10},
        ],
    }
    gp = tmp(json.dumps(graph))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B"])
        assert proc.returncode == 0, proc.stderr.decode()
        out = json.loads(proc.stdout.decode().strip())
        assert out["path"] == ["A", "B"]
        assert out["distance"] == 10
    finally:
        os.unlink(gp)


def test_duplicate_edge_larger_last_record_flips_chosen_route():
    graph = {
        "nodes": ["A", "B", "C"],
        "edges": [
            {"from": "A", "to": "B", "distance": 1},
            {"from": "B", "to": "C", "distance": 1},
            {"from": "A", "to": "C", "distance": 10},
            {"from": "A", "to": "B", "distance": 100},
        ],
    }
    gp = tmp(json.dumps(graph))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "C"])
        assert proc.returncode == 0, proc.stderr.decode()
        out = json.loads(proc.stdout.decode().strip())
        assert out["path"] == ["A", "C"]
        assert out["distance"] == 10
    finally:
        os.unlink(gp)
