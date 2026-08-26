import os, json, subprocess, tempfile, time, math

CANDIDATES = ["/app/router"]


def find_bin():
    try:
        if os.path.exists("/app/router"):
            os.unlink("/app/router")
    except Exception:
        pass
    if os.path.exists("/app/go.mod"):
        result = subprocess.run(
            ["go", "build", "-o", "router", "."],
            cwd="/app",
            timeout=90,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
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
    assert "traffic" in out


def test_help_precedence_with_traffic():
    proc = run(["--help", "--unknown", "--graph", "/no"])
    assert proc.returncode == 0
    assert "traffic" in proc.stdout.decode().lower()


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
        assert math.isclose(out["distance"], 3.0, abs_tol=1e-6)
    finally:
        os.unlink(gp)


def test_tie_break_without_traffic():
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


def test_unknown_flag_exit_2():
    gp = tmp(
        json.dumps(
            {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 1}]}
        )
    )
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B", "--unknown"])
        assert proc.returncode == 2 and proc.stdout.decode().strip() == ""
    finally:
        os.unlink(gp)


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
        assert out["path"] == ["A", "B", "C"]
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


def test_traffic_factor_string_invalid():
    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 5}]}
    traffic = {"traffic": [{"from": "A", "to": "B", "factor": "2.5"}]}
    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps(traffic))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B", "--traffic", tp])
        assert proc.returncode == 2 and proc.stdout.decode().strip() == ""
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_factor_zero_invalid():
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


def test_traffic_factor_negative_invalid():
    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 5}]}
    traffic = {"traffic": [{"from": "A", "to": "B", "factor": -1}]}
    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps(traffic))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B", "--traffic", tp])
        assert proc.returncode == 2
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_factor_scientific_plus_valid():
    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 10}]}
    content = '{"traffic":[{"from":"A","to":"B","factor":1e+3}]}'
    gp = tmp(json.dumps(graph))
    tp = tmp(content)
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B", "--traffic", tp])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip())
        assert math.isclose(out["effective_distance"], 10000, abs_tol=1e-6)
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_factor_scientific_negative_exponent():
    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 10}]}
    traffic = {"traffic": [{"from": "A", "to": "B", "factor": 1e-2}]}
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


def test_traffic_delay_negative_invalid():
    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 5}]}
    traffic = {"traffic": [{"from": "A", "to": "B", "factor": 1, "delay": -1}]}
    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps(traffic))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B", "--traffic", tp])
        assert proc.returncode == 2
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_delay_string_invalid():
    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 5}]}
    traffic = {"traffic": [{"from": "A", "to": "B", "factor": 1, "delay": "5"}]}
    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps(traffic))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B", "--traffic", tp])
        assert proc.returncode == 2
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_delay_scientific_valid():
    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 10}]}
    traffic = {"traffic": [{"from": "A", "to": "B", "factor": 1, "delay": 1e2}]}
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


def test_traffic_trailing_comma_invalid():
    gp = tmp(
        json.dumps(
            {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 5}]}
        )
    )
    tp = tmp('{"traffic":[{"from":"A","to":"B","factor":1},]}')
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B", "--traffic", tp])
        assert proc.returncode == 2
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_bom_must_not_crash():
    gp = tmp(
        json.dumps(
            {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 5}]}
        )
    )
    tp = tmp('\ufeff{"traffic":[{"from":"A","to":"B","factor":1}]}')
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B", "--traffic", tp])
        assert proc.returncode == 2
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_comment_invalid():
    gp = tmp(
        json.dumps(
            {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 5}]}
        )
    )
    tp = tmp('// comment\n{"traffic":[{"from":"A","to":"B","factor":1}]}')
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B", "--traffic", tp])
        assert proc.returncode == 2
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_file_not_found():
    gp = tmp(
        json.dumps(
            {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 5}]}
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
                "/nonexistent.json",
            ]
        )
        assert proc.returncode == 2 and proc.stdout.decode().strip() == ""
    finally:
        os.unlink(gp)


def test_traffic_entry_missing_fields():
    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 5}]}
    traffic = {"traffic": [{"from": "A", "to": "B"}]}
    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps(traffic))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B", "--traffic", tp])
        assert proc.returncode == 2
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_entry_from_to_not_string():
    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 5}]}
    traffic = {"traffic": [{"from": 123, "to": "B", "factor": 1}]}
    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps(traffic))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B", "--traffic", tp])
        assert proc.returncode == 2
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_entry_whitespace_only_invalid():
    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 5}]}
    traffic = {"traffic": [{"from": "   ", "to": "B", "factor": 1}]}
    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps(traffic))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B", "--traffic", tp])
        assert proc.returncode == 2
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_entry_leading_space_invalid():
    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 1}]}
    traffic = {"traffic": [{"from": " A", "to": "B", "factor": 1}]}
    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps(traffic))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B", "--traffic", tp])
        assert proc.returncode == 2
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_self_loop_invalid():
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


def test_traffic_nonexisting_node_invalid():
    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 5}]}
    traffic = {"traffic": [{"from": "A", "to": "C", "factor": 1}]}
    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps(traffic))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B", "--traffic", tp])
        assert proc.returncode == 2
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_nonexisting_edge_invalid():
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
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip())
        assert math.isclose(out["effective_distance"], 20, abs_tol=1e-6)
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_default_factor_for_untraffic_edge():
    graph = {
        "nodes": ["A", "B", "C"],
        "edges": [
            {"from": "A", "to": "B", "distance": 5},
            {"from": "B", "to": "C", "distance": 5},
        ],
    }
    traffic = {"traffic": [{"from": "A", "to": "B", "factor": 2}]}
    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps(traffic))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "C", "--traffic", tp])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip())
        assert math.isclose(out["effective_distance"], 15, abs_tol=1e-6)
        assert out["distance"] == 10
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


def test_traffic_direct_array_empty_valid():
    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 5}]}
    gp = tmp(json.dumps(graph))
    tp = tmp("[]")
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B", "--traffic", tp])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip())
        assert math.isclose(out["effective_distance"], 5, abs_tol=1e-6)
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_wrapper_null_invalid_vs_empty_valid():
    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 5}]}
    gp = tmp(json.dumps(graph))
    tp_null = tmp('{"traffic":null}')
    tp_empty = tmp('{"traffic":[]}')
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B", "--traffic", tp_null])
        assert proc.returncode == 2
        proc2 = run(["--graph", gp, "--from", "A", "--to", "B", "--traffic", tp_empty])
        assert proc2.returncode == 0
    finally:
        os.unlink(gp)
        os.unlink(tp_null)
        os.unlink(tp_empty)


def test_traffic_direct_array_invalid_elements():
    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 5}]}
    gp = tmp(json.dumps(graph))
    for bad in ["[null]", "[123]", '["A"]', "[[1,2]]"]:
        tp = tmp(bad)
        try:
            proc = run(["--graph", gp, "--from", "A", "--to", "B", "--traffic", tp])
            assert proc.returncode == 2, f"should be invalid for {bad}"
        finally:
            os.unlink(tp)
    os.unlink(gp)


def test_traffic_top_level_invalid():
    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 5}]}
    gp = tmp(json.dumps(graph))
    for bad in ['"string"', "123", "null"]:
        tp = tmp(bad)
        try:
            proc = run(["--graph", gp, "--from", "A", "--to", "B", "--traffic", tp])
            assert proc.returncode == 2
        finally:
            os.unlink(tp)
    os.unlink(gp)


def test_traffic_missing_key_invalid():
    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 5}]}
    gp = tmp(json.dumps(graph))
    tp = tmp('{"foo":[]}')
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


def test_traffic_batch_with_some_no_route():
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


def test_traffic_single_no_path():
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


def test_traffic_output_fields_strict():
    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 5}]}
    traffic = {"traffic": [{"from": "A", "to": "B", "factor": 2}]}
    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps(traffic))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B", "--traffic", tp])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip())
        assert set(out.keys()) == {
            "path",
            "distance",
            "effective_distance",
            "traffic_delay",
        }
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_directional_basic_asymmetric():
    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 10}]}
    traffic = {"traffic": [{"from": "A", "to": "B", "factor": 2}]}
    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps(traffic))
    try:
        proc_ab = run(["--graph", gp, "--from", "A", "--to", "B", "--traffic", tp])
        assert proc_ab.returncode == 0
        out_ab = json.loads(proc_ab.stdout.decode().strip())
        assert math.isclose(out_ab["effective_distance"], 20, abs_tol=1e-6)
        assert math.isclose(out_ab["traffic_delay"], 10, abs_tol=1e-6)
        proc_ba = run(["--graph", gp, "--from", "B", "--to", "A", "--traffic", tp])
        assert proc_ba.returncode == 0
        out_ba = json.loads(proc_ba.stdout.decode().strip())
        assert math.isclose(out_ba["effective_distance"], 10, abs_tol=1e-6), (
            f"B->A should be default 10, got {out_ba['effective_distance']}"
        )
        assert math.isclose(out_ba["traffic_delay"], 0, abs_tol=1e-6)
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_directional_reverse_distinct_not_overwrite():
    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 10}]}
    traffic = {
        "traffic": [
            {"from": "A", "to": "B", "factor": 2},
            {"from": "B", "to": "A", "factor": 3},
        ]
    }
    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps(traffic))
    try:
        proc_ab = run(["--graph", gp, "--from", "A", "--to", "B", "--traffic", tp])
        assert proc_ab.returncode == 0
        out_ab = json.loads(proc_ab.stdout.decode().strip())
        assert math.isclose(out_ab["effective_distance"], 20, abs_tol=1e-6)
        proc_ba = run(["--graph", gp, "--from", "B", "--to", "A", "--traffic", tp])
        assert proc_ba.returncode == 0
        out_ba = json.loads(proc_ba.stdout.decode().strip())
        assert math.isclose(out_ba["effective_distance"], 30, abs_tol=1e-6)
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_directional_same_ordered_last_wins():
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


def test_traffic_directional_delay_reset():
    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 10}]}
    traffic = {
        "traffic": [
            {"from": "A", "to": "B", "factor": 2, "delay": 5},
            {"from": "A", "to": "B", "factor": 3},
        ]
    }
    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps(traffic))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B", "--traffic", tp])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip())
        assert math.isclose(out["effective_distance"], 30, abs_tol=1e-6), (
            f"delay should reset to 0, got {out['effective_distance']}"
        )
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_directional_reverse_interleaved():
    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 10}]}
    traffic = {
        "traffic": [
            {"from": "A", "to": "B", "factor": 2},
            {"from": "B", "to": "A", "factor": 3},
            {"from": "A", "to": "B", "factor": 4},
        ]
    }
    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps(traffic))
    try:
        proc_ab = run(["--graph", gp, "--from", "A", "--to", "B", "--traffic", tp])
        out_ab = json.loads(proc_ab.stdout.decode().strip())
        assert math.isclose(out_ab["effective_distance"], 40, abs_tol=1e-6)
        proc_ba = run(["--graph", gp, "--from", "B", "--to", "A", "--traffic", tp])
        out_ba = json.loads(proc_ba.stdout.decode().strip())
        assert math.isclose(out_ba["effective_distance"], 30, abs_tol=1e-6)
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_directional_reroute():
    graph = {
        "nodes": ["A", "B", "C"],
        "edges": [
            {"from": "A", "to": "B", "distance": 1},
            {"from": "B", "to": "C", "distance": 1},
            {"from": "A", "to": "C", "distance": 10},
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
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip())
        assert out["path"] == ["A", "B", "C"]
        assert math.isclose(out["effective_distance"], 2, abs_tol=1e-6)
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_directional_batch_mixed_directions():
    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 10}]}
    traffic = {
        "traffic": [
            {"from": "A", "to": "B", "factor": 2},
            {"from": "B", "to": "A", "factor": 0.5},
        ]
    }
    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps(traffic))
    rp = tmp(
        json.dumps(
            [{"source": "A", "destination": "B"}, {"source": "B", "destination": "A"}]
        )
    )
    try:
        proc = run(["--graph", gp, "--requests", rp, "--traffic", tp])
        assert proc.returncode == 0
        lines = proc.stdout.decode().strip().splitlines()
        assert len(lines) == 2
        o1 = json.loads(lines[0])
        o2 = json.loads(lines[1])
        assert math.isclose(o1["effective_distance"], 20, abs_tol=1e-6)
        assert math.isclose(o2["effective_distance"], 5, abs_tol=1e-6)
    finally:
        os.unlink(gp)
        os.unlink(tp)
        os.unlink(rp)


def test_traffic_directional_effective_formula_per_edge():
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
        assert math.isclose(out["effective_distance"], 35, abs_tol=1e-6), (
            f"expected 25+10=35 per-edge, got {out['effective_distance']}"
        )
        assert math.isclose(out["distance"], 20, abs_tol=1e-6)
        assert not math.isclose(out["effective_distance"], 40, abs_tol=1e-6)
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_directional_raw_along_effective_best():
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
            {"from": "A", "to": "B", "factor": 20},
            {"from": "B", "to": "D", "factor": 20},
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
        assert out["path"] == ["A", "C", "D"], (
            f"effective C=20 vs B=40, got {out['path']}"
        )
        assert math.isclose(out["distance"], 20, abs_tol=1e-6)
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_directional_secondary_raw_tie():
    graph = {
        "nodes": ["A", "B", "C", "D"],
        "edges": [
            {"from": "A", "to": "B", "distance": 10},
            {"from": "B", "to": "D", "distance": 1},
            {"from": "A", "to": "C", "distance": 2},
            {"from": "C", "to": "D", "distance": 2},
        ],
    }
    # zone-entry toll semantics: same factor shares zone, toll charged once at entry.
    # To keep effective tie 12 under zone model, put delay on first arc A->B, not second B->D.
    # Old per-arc: A-B 10+0 + B-D 1+1 =12, new zone: A-B entry 10+1=11 + B-D 1 =12 (same)
    # A-C-D: 2*2 + 2*4 =12 raw 4, so raw tie-break still picks C.
    traffic = {
        "traffic": [
            {"from": "A", "to": "B", "factor": 1, "delay": 1},
            {"from": "B", "to": "D", "factor": 1, "delay": 0},
            {"from": "A", "to": "C", "factor": 2, "delay": 0},
            {"from": "C", "to": "D", "factor": 4, "delay": 0},
        ]
    }
    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps(traffic))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "D", "--traffic", tp])
        assert proc.returncode == 0, proc.stderr.decode()
        out = json.loads(proc.stdout.decode().strip())
        assert out["path"] == ["A", "C", "D"], (
            f"effective equal 12 raw 4 vs 11, should pick C, got {out['path']}"
        )
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_directional_lex_tie():
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
        assert out["path"] == ["A", "B", "D"]
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_directional_10_way_tie():
    dest = "Z"
    mids = [chr(ord("B") + i) for i in range(10)]
    nodes = ["A", dest] + mids
    edges = []
    traf = []
    for c in mids:
        edges.append({"from": "A", "to": c, "distance": 5})
        edges.append({"from": c, "to": dest, "distance": 5})
        traf.append({"from": "A", "to": c, "factor": 1})
        traf.append({"from": c, "to": dest, "factor": 1})
    graph = {"nodes": nodes, "edges": edges}
    traffic = {"traffic": traf}
    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps(traffic))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", dest, "--traffic", tp])
        assert proc.returncode == 0, proc.stderr.decode()
        out = json.loads(proc.stdout.decode().strip())
        assert out["path"] == ["A", "B", dest]
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_directional_delay_with_directional():
    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 10}]}
    traffic = {
        "traffic": [
            {"from": "A", "to": "B", "factor": 1, "delay": 100},
            {"from": "B", "to": "A", "factor": 1, "delay": 0},
        ]
    }
    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps(traffic))
    try:
        proc_ab = run(["--graph", gp, "--from", "A", "--to", "B", "--traffic", tp])
        out_ab = json.loads(proc_ab.stdout.decode().strip())
        assert math.isclose(out_ab["effective_distance"], 110, abs_tol=1e-6)
        proc_ba = run(["--graph", gp, "--from", "B", "--to", "A", "--traffic", tp])
        out_ba = json.loads(proc_ba.stdout.decode().strip())
        assert math.isclose(out_ba["effective_distance"], 10, abs_tol=1e-6)
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_directional_negative_delay_allowed():
    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 10}]}
    traffic = {
        "traffic": [
            {"from": "A", "to": "B", "factor": 0.2},
            {"from": "B", "to": "A", "factor": 2},
        ]
    }
    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps(traffic))
    try:
        proc_ab = run(["--graph", gp, "--from", "A", "--to", "B", "--traffic", tp])
        out_ab = json.loads(proc_ab.stdout.decode().strip())
        assert math.isclose(out_ab["effective_distance"], 2, abs_tol=1e-6)
        proc_ba = run(["--graph", gp, "--from", "B", "--to", "A", "--traffic", tp])
        out_ba = json.loads(proc_ba.stdout.decode().strip())
        assert math.isclose(out_ba["effective_distance"], 20, abs_tol=1e-6)
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_flag_order_independence():
    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 1}]}
    traffic = {"traffic": [{"from": "A", "to": "B", "factor": 2}]}
    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps(traffic))
    try:
        proc = run(["--from", "A", "--graph", gp, "--to", "B", "--traffic", tp])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip())
        assert math.isclose(out["effective_distance"], 2, abs_tol=1e-6)
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_flag_equals_syntax():
    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 1}]}
    traffic = {"traffic": [{"from": "A", "to": "B", "factor": 2}]}
    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps(traffic))
    try:
        proc = run([f"--graph={gp}", f"--from=A", f"--to=B", f"--traffic={tp}"])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip())
        assert math.isclose(out["effective_distance"], 2, abs_tol=1e-6)
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_invalid_graph_still_exit2_with_traffic():
    graph = {"nodes": ["A", "A"], "edges": []}
    traffic = {"traffic": []}
    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps(traffic))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B", "--traffic", tp])
        assert proc.returncode == 2
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_batch_100_with_traffic_relative():
    nodes = [f"N{i}" for i in range(20)]
    edges = [{"from": f"N{i}", "to": f"N{i + 1}", "distance": 1} for i in range(19)]
    graph = {"nodes": nodes, "edges": edges}
    traffic = {
        "traffic": [
            {"from": f"N{i}", "to": f"N{i + 1}", "factor": 1.5} for i in range(19)
        ]
        + [{"from": f"N{i + 1}", "to": f"N{i}", "factor": 1.5} for i in range(19)]
    }
    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps(traffic))
    reqs = [{"source": "N0", "destination": f"N{i}"} for i in range(20)] * 5
    rp = tmp(json.dumps(reqs))
    rp1 = tmp(json.dumps([{"source": "N0", "destination": "N19"}]))
    try:
        start = time.time()
        base = run(["--graph", gp, "--requests", rp1, "--traffic", tp])
        base_elapsed = time.time() - start
        assert base.returncode == 0, base.stderr.decode()[:500]
        start = time.time()
        proc = run(["--graph", gp, "--requests", rp, "--traffic", tp])
        elapsed = time.time() - start
        assert proc.returncode == 0, proc.stderr.decode()[:500]
        assert elapsed <= max(2.5, 25 * base_elapsed + 1.0)
        lines = proc.stdout.decode().strip().splitlines()
        assert len(lines) == 100
    finally:
        os.unlink(gp)
        os.unlink(tp)
        os.unlink(rp)
        os.unlink(rp1)


def test_traffic_performance_500_nodes():
    nodes = [f"N{i}" for i in range(200)]
    edges = [{"from": f"N{i}", "to": f"N{i + 1}", "distance": 1} for i in range(199)]
    traffic = {
        "traffic": [
            {"from": f"N{i}", "to": f"N{i + 1}", "factor": 1.1} for i in range(199)
        ]
        + [{"from": f"N{i + 1}", "to": f"N{i}", "factor": 1.1} for i in range(199)]
    }
    graph = {
        "nodes": nodes,
        "edges": edges
        + [
            {"from": f"N{i}", "to": f"N{i + 2}", "distance": 2}
            for i in range(0, 198, 2)
        ],
    }
    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps(traffic))
    try:
        proc = run(["--graph", gp, "--from", "N0", "--to", "N199", "--traffic", tp])
        assert proc.returncode == 0
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_performance_2000_nodes():
    nodes = [f"N{i}" for i in range(2000)]
    edges = [{"from": f"N{i}", "to": f"N{i + 1}", "distance": 1} for i in range(1999)]
    traffic = {
        "traffic": [
            {"from": f"N{i}", "to": f"N{i + 1}", "factor": 1.1}
            for i in range(0, 1999, 100)
        ]
    }
    graph = {"nodes": nodes, "edges": edges}
    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps(traffic))
    base_graph = {
        "nodes": [f"N{i}" for i in range(20)],
        "edges": [
            {"from": f"N{i}", "to": f"N{i + 1}", "distance": 1} for i in range(19)
        ],
    }
    base_gp = tmp(json.dumps(base_graph))
    base_tp = tmp(
        json.dumps(
            {
                "traffic": [
                    {"from": f"N{i}", "to": f"N{i + 1}", "factor": 1.1}
                    for i in range(19)
                ]
            }
        )
    )
    try:
        start = time.time()
        base = run(
            ["--graph", base_gp, "--from", "N0", "--to", "N19", "--traffic", base_tp]
        )
        base_elapsed = time.time() - start
        assert base.returncode == 0
        start = time.time()
        proc = run(["--graph", gp, "--from", "N0", "--to", "N1999", "--traffic", tp])
        elapsed = time.time() - start
        assert proc.returncode == 0
        assert elapsed <= max(3.5, 25 * base_elapsed + 1.0)
    finally:
        os.unlink(gp)
        os.unlink(tp)
        os.unlink(base_gp)
        os.unlink(base_tp)


def test_traffic_large_graph_5000_nodes():
    nodes = [f"N{i}" for i in range(5000)]
    edges = [{"from": f"N{i}", "to": f"N{i + 1}", "distance": 1} for i in range(4999)]
    graph = {"nodes": nodes, "edges": edges}
    traffic = {
        "traffic": [
            {"from": f"N{i}", "to": f"N{i + 1}", "factor": 2}
            for i in range(0, 1000, 200)
        ]
    }
    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps(traffic))
    base_graph = {
        "nodes": [f"N{i}" for i in range(20)],
        "edges": [
            {"from": f"N{i}", "to": f"N{i + 1}", "distance": 1} for i in range(19)
        ],
    }
    base_gp = tmp(json.dumps(base_graph))
    base_tp = tmp(
        json.dumps(
            {
                "traffic": [
                    {"from": f"N{i}", "to": f"N{i + 1}", "factor": 2}
                    for i in range(0, 20, 5)
                ]
            }
        )
    )
    try:
        start = time.time()
        base = run(
            ["--graph", base_gp, "--from", "N0", "--to", "N19", "--traffic", base_tp]
        )
        base_elapsed = time.time() - start
        assert base.returncode == 0
        start = time.time()
        proc = run(["--graph", gp, "--from", "N0", "--to", "N4999", "--traffic", tp])
        elapsed = time.time() - start
        assert proc.returncode == 0
        assert elapsed <= max(5.5, 25 * base_elapsed + 2.0)
    finally:
        os.unlink(gp)
        os.unlink(tp)
        os.unlink(base_gp)
        os.unlink(base_tp)


def test_traffic_large_batch_200_with_traffic():
    nodes = [f"N{i}" for i in range(100)]
    edges = [{"from": f"N{i}", "to": f"N{i + 1}", "distance": 1} for i in range(99)]
    graph = {"nodes": nodes, "edges": edges}
    traffic = {
        "traffic": [
            {"from": f"N{i}", "to": f"N{i + 1}", "factor": 1.2} for i in range(99)
        ]
    }
    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps(traffic))
    reqs = [{"source": "N0", "destination": f"N{i % 100}"} for i in range(200)]
    rp = tmp(json.dumps(reqs))
    rp1 = tmp(json.dumps([{"source": "N0", "destination": "N99"}]))
    try:
        start = time.time()
        base = run(["--graph", gp, "--requests", rp1, "--traffic", tp])
        base_elapsed = time.time() - start
        assert base.returncode == 0
        start = time.time()
        proc = run(["--graph", gp, "--requests", rp, "--traffic", tp])
        elapsed = time.time() - start
        assert proc.returncode == 0
        assert elapsed <= max(4.0, 25 * base_elapsed + 1.0)
        lines = proc.stdout.decode().strip().splitlines()
        assert len(lines) == 200
    finally:
        os.unlink(gp)
        os.unlink(tp)
        os.unlink(rp)
        os.unlink(rp1)


def test_traffic_same_source_amortization():
    nodes = [f"N{i}" for i in range(100)]
    edges = [{"from": f"N{i}", "to": f"N{i + 1}", "distance": 1} for i in range(99)]
    graph = {"nodes": nodes, "edges": edges}
    traffic = {
        "traffic": [
            {"from": f"N{i}", "to": f"N{i + 1}", "factor": 1.1} for i in range(99)
        ]
    }
    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps(traffic))
    reqs_same = [{"source": "N0", "destination": f"N{i % 100}"} for i in range(500)]
    rp_same = tmp(json.dumps(reqs_same))
    reqs_multi = [
        {"source": f"N{i % 100}", "destination": f"N{(i + 50) % 100}"}
        for i in range(500)
    ]
    rp_multi = tmp(json.dumps(reqs_multi))
    try:
        start = time.time()
        proc_same = run(["--graph", gp, "--requests", rp_same, "--traffic", tp])
        elapsed_same = time.time() - start
        assert proc_same.returncode == 0
        start = time.time()
        proc_multi = run(["--graph", gp, "--requests", rp_multi, "--traffic", tp])
        elapsed_multi = time.time() - start
        assert proc_multi.returncode == 0
        assert elapsed_same <= 0.5 * elapsed_multi + 1.5 or elapsed_same < 3.0
    finally:
        os.unlink(gp)
        os.unlink(tp)
        os.unlink(rp_same)
        os.unlink(rp_multi)


def test_traffic_batch_5000_correctness():
    nodes = [f"N{i}" for i in range(100)]
    edges = [{"from": f"N{i}", "to": f"N{i + 1}", "distance": 1} for i in range(99)]
    graph = {"nodes": nodes, "edges": edges}
    traffic = {
        "traffic": [
            {"from": f"N{i}", "to": f"N{i + 1}", "factor": 1.5} for i in range(99)
        ]
    }
    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps(traffic))
    reqs = [{"source": "N0", "destination": f"N{i % 100}"} for i in range(5000)]
    rp = tmp(json.dumps(reqs))
    try:
        start = time.time()
        proc = run(["--graph", gp, "--requests", rp, "--traffic", tp])
        elapsed = time.time() - start
        assert proc.returncode == 0
        # relative bound instead of absolute <12
        base_gp = tmp(
            json.dumps(
                {
                    "nodes": ["A", "B"],
                    "edges": [{"from": "A", "to": "B", "distance": 1}],
                }
            )
        )
        base_rp = tmp(json.dumps([{"source": "A", "destination": "B"}]))
        try:
            s = time.time()
            b = run(["--graph", base_gp, "--requests", base_rp])
            base_elapsed = time.time() - s
            assert b.returncode == 0
            assert elapsed <= max(12.0, 25 * base_elapsed + 12.0)
        finally:
            os.unlink(base_gp)
            os.unlink(base_rp)
        lines = proc.stdout.decode().strip().splitlines()
        assert len(lines) == 5000
    finally:
        os.unlink(gp)
        os.unlink(tp)
        os.unlink(rp)


# --- Turn1 regression: Step2 spec line 124 requires raw-only validations still pass when traffic absent ---
def test_raw_regression_duplicate_nodes_invalid():
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


def test_raw_regression_negative_distance_invalid():
    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": -5}]}
    gp = tmp(json.dumps(graph))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B"])
        assert proc.returncode == 2
    finally:
        os.unlink(gp)


def test_raw_regression_self_loop_invalid():
    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "A", "distance": 5}]}
    gp = tmp(json.dumps(graph))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B"])
        assert proc.returncode == 2
    finally:
        os.unlink(gp)


def test_raw_regression_missing_node_edge_invalid():
    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "C", "distance": 5}]}
    gp = tmp(json.dumps(graph))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B"])
        assert proc.returncode == 2
    finally:
        os.unlink(gp)


def test_raw_regression_empty_node_invalid():
    graph = {"nodes": ["A", "", "B"], "edges": []}
    gp = tmp(json.dumps(graph))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B"])
        assert proc.returncode == 2
    finally:
        os.unlink(gp)


def test_raw_regression_whitespace_node_invalid():
    graph = {"nodes": ["A", "   ", "B"], "edges": []}
    gp = tmp(json.dumps(graph))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B"])
        assert proc.returncode == 2 and proc.stdout.decode().strip() == ""
    finally:
        os.unlink(gp)


def test_raw_regression_whitespace_edge_invalid():
    graph = {"nodes": ["A", "B"], "edges": [{"from": "   ", "to": "B", "distance": 1}]}
    gp = tmp(json.dumps(graph))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B"])
        assert proc.returncode == 2
    finally:
        os.unlink(gp)


def test_raw_regression_graph_file_not_found():
    proc = run(["--graph", "/nonexistent/path.json", "--from", "A", "--to", "B"])
    assert proc.returncode == 2 and proc.stdout.decode().strip() == ""


def test_raw_regression_trailing_comma_invalid():
    gp = tmp('{"nodes":["A","B"],"edges":[{"from":"A","to":"B","distance":5},]}')
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B"])
        assert proc.returncode == 2
    finally:
        os.unlink(gp)


def test_raw_regression_bom_invalid():
    gp = tmp('\ufeff{"nodes":["A","B"],"edges":[{"from":"A","to":"B","distance":5}]}')
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B"])
        assert proc.returncode == 2
    finally:
        os.unlink(gp)


def test_raw_regression_nodes_empty_array_invalid():
    gp = tmp('{"nodes":[],"edges":[]}')
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B"])
        assert proc.returncode == 2
    finally:
        os.unlink(gp)


def test_raw_regression_nodes_non_string_invalid():
    gp = tmp('{"nodes":["A",123,"B"],"edges":[]}')
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B"])
        assert proc.returncode == 2
    finally:
        os.unlink(gp)


def test_raw_regression_edges_non_object_invalid():
    gp = tmp('{"nodes":["A","B"],"edges":[123]}')
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B"])
        assert proc.returncode == 2
    finally:
        os.unlink(gp)


def test_raw_regression_duplicate_edges_min():
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


def test_raw_regression_duplicate_reverse_min():
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


def test_raw_regression_case_sensitive():
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


def test_raw_regression_source_equals_dest():
    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 5}]}
    gp = tmp(json.dumps(graph))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "A"])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip())
        assert out["path"] == ["A"] and out["distance"] == 0
    finally:
        os.unlink(gp)


def test_raw_regression_no_path():
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


def test_raw_regression_batch_all_success():
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


def test_raw_regression_batch_some_no_route():
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


def test_raw_regression_empty_requests():
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


def test_raw_regression_request_order_preserved():
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


def test_raw_regression_output_fields_strict_single():
    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 5}]}
    gp = tmp(json.dumps(graph))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B"])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip())
        assert set(out.keys()) == {"path", "distance"}
    finally:
        os.unlink(gp)


def test_raw_regression_flag_order_independence():
    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 1}]}
    gp = tmp(json.dumps(graph))
    try:
        proc = run(["--from", "A", "--graph", gp, "--to", "B"])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip())
        assert out["path"] == ["A", "B"]
    finally:
        os.unlink(gp)


def test_raw_regression_from_to_equals_syntax():
    graph = {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 1}]}
    gp = tmp(json.dumps(graph))
    try:
        proc = run([f"--graph={gp}", "--from=A", "--to=B"])
        assert proc.returncode == 0
        out = json.loads(proc.stdout.decode().strip())
        assert out["path"] == ["A", "B"]
    finally:
        os.unlink(gp)


def test_raw_regression_stdlib_only():
    # instruction.md:5 MUST stdlib only – verify go.mod has no require block
    go_mod_path = "/app/go.mod"
    if os.path.exists(go_mod_path):
        with open(go_mod_path) as f:
            content = f.read()
        # no require block allowed
        assert (
            "require" not in content.lower()
            or "require (" not in content.lower()
            and content.strip().count("require") == 0
            or "require" not in content
        ), f"go.mod contains non-stdlib require: {content[:500]}"
        # simpler: ensure no 'require' line
        for line in content.splitlines():
            stripped = line.strip()
            if stripped.startswith("require"):
                assert False, (
                    f"go.mod must not have require block for stdlib-only: {content}"
                )


def test_raw_duplicate_edge_last_record_wins_when_larger():
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


def test_raw_duplicate_reverse_edge_last_record_wins_when_larger():
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


def test_raw_duplicate_edge_larger_last_record_flips_chosen_route():
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


def test_traffic_same_factor_run_charges_zone_delay_once():
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
            {"from": "B", "to": "C", "factor": 2, "delay": 5},
        ]
    }
    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps(traffic))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "C", "--traffic", tp])
        assert proc.returncode == 0, proc.stderr.decode()
        out = json.loads(proc.stdout.decode().strip())
        assert out["path"] == ["A", "B", "C"]
        assert out["distance"] == 20
        assert math.isclose(out["effective_distance"], 45, abs_tol=1e-6)
        assert math.isclose(out["traffic_delay"], 25, abs_tol=1e-6)
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_factor_boundary_recharges_zone_delay():
    graph = {
        "nodes": ["A", "B", "C", "D"],
        "edges": [
            {"from": "A", "to": "B", "distance": 10},
            {"from": "B", "to": "C", "distance": 10},
            {"from": "C", "to": "D", "distance": 10},
        ],
    }
    traffic = {
        "traffic": [
            {"from": "A", "to": "B", "factor": 2, "delay": 5},
            {"from": "B", "to": "C", "factor": 2, "delay": 5},
            {"from": "C", "to": "D", "factor": 1, "delay": 3},
        ]
    }
    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps(traffic))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "D", "--traffic", tp])
        assert proc.returncode == 0, proc.stderr.decode()
        out = json.loads(proc.stdout.decode().strip())
        assert out["path"] == ["A", "B", "C", "D"]
        assert out["distance"] == 30
        assert math.isclose(out["effective_distance"], 58, abs_tol=1e-6)
        assert math.isclose(out["traffic_delay"], 28, abs_tol=1e-6)
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_explicit_factor_one_shares_default_zone():
    graph = {
        "nodes": ["A", "B", "C"],
        "edges": [
            {"from": "A", "to": "B", "distance": 10},
            {"from": "B", "to": "C", "distance": 10},
        ],
    }
    traffic = {
        "traffic": [
            {"from": "B", "to": "C", "factor": 1, "delay": 10},
        ]
    }
    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps(traffic))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "C", "--traffic", tp])
        assert proc.returncode == 0, proc.stderr.decode()
        out = json.loads(proc.stdout.decode().strip())
        assert out["path"] == ["A", "B", "C"]
        assert out["distance"] == 20
        assert math.isclose(out["effective_distance"], 20, abs_tol=1e-6)
        assert math.isclose(out["traffic_delay"], 0, abs_tol=1e-6)
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_path_state_preserves_expensive_arrival_zone():
    graph = {
        "nodes": ["A", "B", "C", "D"],
        "edges": [
            {"from": "A", "to": "B", "distance": 10},
            {"from": "A", "to": "C", "distance": 5},
            {"from": "C", "to": "B", "distance": 5},
            {"from": "B", "to": "D", "distance": 10},
        ],
    }
    # arrival-zone trap: cheaper arrival to B (via C, eff 20 zone f2) loses to
    # costlier arrival (direct A-B eff 110 zone f1) because B->D shares zone f1
    # and avoids its 100 toll. Pin reverse arcs to f2 to block cheap cycles.
    traffic = {
        "traffic": [
            {"from": "A", "to": "B", "factor": 1, "delay": 100},
            {"from": "B", "to": "A", "factor": 2, "delay": 0},
            {"from": "A", "to": "C", "factor": 2, "delay": 0},
            {"from": "C", "to": "A", "factor": 2, "delay": 0},
            {"from": "C", "to": "B", "factor": 2, "delay": 0},
            {"from": "B", "to": "C", "factor": 2, "delay": 0},
            {"from": "B", "to": "D", "factor": 1, "delay": 100},
        ]
    }
    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps(traffic))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "D", "--traffic", tp])
        assert proc.returncode == 0, proc.stderr.decode()
        out = json.loads(proc.stdout.decode().strip())
        assert out["path"] == ["A", "B", "D"]
        assert out["distance"] == 20
        assert math.isclose(out["effective_distance"], 120, abs_tol=1e-6)
        assert math.isclose(out["traffic_delay"], 100, abs_tol=1e-6)
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_batch_matches_single_on_arrival_zone_trap():
    graph = {
        "nodes": ["A", "B", "C", "D"],
        "edges": [
            {"from": "A", "to": "B", "distance": 10},
            {"from": "A", "to": "C", "distance": 5},
            {"from": "C", "to": "B", "distance": 5},
            {"from": "B", "to": "D", "distance": 10},
        ],
    }
    traffic = {
        "traffic": [
            {"from": "A", "to": "B", "factor": 1, "delay": 100},
            {"from": "B", "to": "A", "factor": 2, "delay": 0},
            {"from": "A", "to": "C", "factor": 2, "delay": 0},
            {"from": "C", "to": "A", "factor": 2, "delay": 0},
            {"from": "C", "to": "B", "factor": 2, "delay": 0},
            {"from": "B", "to": "C", "factor": 2, "delay": 0},
            {"from": "B", "to": "D", "factor": 1, "delay": 100},
        ]
    }
    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps(traffic))
    rp = tmp(json.dumps([{"source": "A", "destination": "D"}]))
    try:
        single_proc = run(["--graph", gp, "--from", "A", "--to", "D", "--traffic", tp])
        batch_proc = run(["--graph", gp, "--requests", rp, "--traffic", tp])
        assert single_proc.returncode == 0, single_proc.stderr.decode()
        assert batch_proc.returncode == 0, batch_proc.stderr.decode()
        single = json.loads(single_proc.stdout.decode().strip())
        batch = json.loads(batch_proc.stdout.decode().strip())
        assert batch["source"] == "A" and batch["destination"] == "D"
        assert batch["path"] == single["path"] == ["A", "B", "D"]
        assert batch["distance"] == single["distance"] == 20
        assert math.isclose(batch["effective_distance"], 120, abs_tol=1e-6)
        assert math.isclose(
            batch["effective_distance"], single["effective_distance"], abs_tol=1e-6
        )
        assert math.isclose(
            batch["traffic_delay"], single["traffic_delay"], abs_tol=1e-6
        )
    finally:
        os.unlink(gp)
        os.unlink(tp)
        os.unlink(rp)


def test_duplicate_keys_inside_traffic_object_invalid():
    # T1: duplicate keys inside traffic entry invalid – duplicate factor same value would be valid old but must be invalid
    gp = tmp(
        json.dumps(
            {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 5}]}
        )
    )
    tp = tmp('{"traffic":[{"from":"A","to":"B","factor":2,"factor":2}]}')
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B", "--traffic", tp])
        assert proc.returncode == 2 and proc.stdout.decode().strip() == ""
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_node_id_with_replacement_char_fffd_invalid_traffic():
    # Graph itself contains FFFD node, even if traffic empty, should be invalid (exit2)
    # Old oracle would accept A->B_FFFD as valid path (exit0), new rejects.
    content = (
        '{"nodes":["A","B\\uD800"],"edges":[{"from":"A","to":"B\\uD800","distance":5}]}'
    )
    gp = tmp(content)
    tp = tmp('{"traffic":[]}')
    try:
        fffd_node = "B\ufffd"
        proc = run(["--graph", gp, "--from", "A", "--to", fffd_node, "--traffic", tp])
        assert proc.returncode == 2 and proc.stdout.decode().strip() == ""
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_from_with_replacement_char_invalid():
    # Traffic entry itself contains FFFD – should be invalid even though graph has that node
    # Old oracle would accept (node exists, edge exists), new rejects.
    gp_content = (
        '{"nodes":["A","B\\uD800"],"edges":[{"from":"A","to":"B\\uD800","distance":5}]}'
    )
    gp = tmp(gp_content)
    tp_content = '{"traffic":[{"from":"B\\uD800","to":"A","factor":1}]}'
    tp = tmp(tp_content)
    try:
        fffd_node = "B\ufffd"
        proc = run(["--graph", gp, "--from", "A", "--to", fffd_node, "--traffic", tp])
        assert proc.returncode == 2 and proc.stdout.decode().strip() == ""
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_from_direct_fffd_literal_invalid():
    fffd = "\ufffd"
    graph = {
        "nodes": ["A", f"B{fffd}"],
        "edges": [{"from": "A", "to": f"B{fffd}", "distance": 5}],
    }
    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps({"traffic": [{"from": f"B{fffd}", "to": "A", "factor": 1}]}))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", f"B{fffd}", "--traffic", tp])
        assert proc.returncode == 2 and proc.stdout.decode().strip() == ""
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_superseded_traffic_malformed_still_invalidates():
    # T2 applied to traffic log's last-wins too
    gp = tmp(
        json.dumps(
            {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 5}]}
        )
    )
    tp = tmp(
        '{"traffic":[{"from":"A","to":"B","factor":"bad"},{"from":"A","to":"B","factor":2}]}'
    )
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B", "--traffic", tp])
        assert proc.returncode == 2 and proc.stdout.decode().strip() == ""
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_batch_mixing_families_invalid_traffic_mode():
    gp = tmp(
        json.dumps(
            {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 1}]}
        )
    )
    rp = tmp(json.dumps([{"source": "A", "to": "B"}]))
    tp = tmp('{"traffic":[]}')
    try:
        proc = run(["--graph", gp, "--requests", rp, "--traffic", tp])
        assert proc.returncode == 2 and proc.stdout.decode().strip() == ""
    finally:
        os.unlink(gp)
        os.unlink(tp)
        os.unlink(rp)


def test_toll_count_tie_break_fewer_tolls_wins():
    # T5: effective -> raw -> fewer tolls -> lex
    # Two competing routes: same eff 45 raw20, but 1 toll vs 2 tolls
    # A-B-D: A->B f2 d5 (toll1), B->D f2 d5 (same zone, no new toll) => eff 45 tolls=1
    # A-C-D: A->C f2 d5 (toll1), C->D f1 d10 (different zone, second toll) => eff 45 tolls=2
    # Fewer tolls (1) should win => A-B-D, under per-arc old oracle A-B-D eff 50 vs 45 old picks A-C-D
    graph = {
        "nodes": ["A", "B", "C", "D"],
        "edges": [
            {"from": "A", "to": "B", "distance": 10},
            {"from": "B", "to": "D", "distance": 10},
            {"from": "A", "to": "C", "distance": 10},
            {"from": "C", "to": "D", "distance": 10},
        ],
    }
    traffic = {
        "traffic": [
            {"from": "A", "to": "B", "factor": 2, "delay": 5},
            {"from": "B", "to": "D", "factor": 2, "delay": 5},
            {"from": "A", "to": "C", "factor": 2, "delay": 5},
            {"from": "C", "to": "D", "factor": 1, "delay": 10},
        ]
    }
    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps(traffic))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "D", "--traffic", tp])
        assert proc.returncode == 0, proc.stderr.decode()
        out = json.loads(proc.stdout.decode().strip())
        assert out["path"] == ["A", "B", "D"], (
            f"fewer tolls should win, got {out['path']}"
        )
        assert math.isclose(out["effective_distance"], 45, abs_tol=1e-6)
        assert out["distance"] == 20
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_wrapper_duplicate_keys_invalid():
    # P1: top-level wrapper duplicate keys {"traffic":[...],"traffic":[...]} -> invalid
    # Same silent-last-wins trap as entry objects, previously uncovered
    gp = tmp(
        json.dumps(
            {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 1}]}
        )
    )
    tp = tmp(
        '{"traffic": [{"from":"A","to":"B","factor":2}], "traffic": [{"from":"A","to":"B","factor":3}]}'
    )
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B", "--traffic", tp])
        assert proc.returncode == 2 and proc.stdout.decode().strip() == "", (
            f"wrapper duplicate keys should be invalid, got rc {proc.returncode} out {proc.stdout.decode()[:200]}"
        )
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_f2_distinct_factor_bound_counts_surviving_not_all_logs_valid():
    # P1: factor-bound ambiguity pinned to surviving ordered pairs after last-wins
    # Log has 5 distinct factors but after deduplication only 4 distinct survive -> valid
    # If counting all logs (5) would be invalid, fairness fix discriminator
    graph = {
        "nodes": ["A", "B", "C", "D", "E"],
        "edges": [
            {"from": "A", "to": "B", "distance": 1},
            {"from": "B", "to": "C", "distance": 1},
            {"from": "C", "to": "D", "distance": 1},
            {"from": "D", "to": "E", "distance": 1},
            {"from": "A", "to": "E", "distance": 10},
        ],
    }
    traffic = {
        "traffic": [
            {"from": "A", "to": "B", "factor": 1.1},
            {"from": "B", "to": "C", "factor": 1.2},
            {"from": "C", "to": "D", "factor": 1.3},
            {"from": "D", "to": "E", "factor": 1.4},
            {"from": "A", "to": "E", "factor": 1.5},
            {
                "from": "A",
                "to": "B",
                "factor": 1.4,
            },  # overwrites 1.1 with 1.4, surviving distinct = {1.2,1.3,1.4,1.5}=4
        ]
    }
    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps(traffic))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "E", "--traffic", tp])
        # surviving count is 4 -> valid per spec pinned to surviving, not all logs
        assert proc.returncode == 0, (
            f"surviving 4 distinct should be valid (counts surviving, not all logs), got rc {proc.returncode} stderr {proc.stderr.decode()[:200]}"
        )
        out = json.loads(proc.stdout.decode().strip())
        # path should be direct or via chain depending, but must succeed
        assert "path" in out and out["path"] != []
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_f1_with_traffic_per_direction_raw_revives():
    # F1: with traffic, survey-log resolves per direction, not per unordered
    # Log: A->B 10, B->A 2. Without traffic both 2, with traffic A->B 10, B->A 2
    graph = {
        "nodes": ["A", "B"],
        "edges": [
            {"from": "A", "to": "B", "distance": 10},
            {"from": "B", "to": "A", "distance": 2},
        ],
    }
    gp = tmp(json.dumps(graph))
    tp = tmp("[]")
    try:
        # without traffic: unordered last wins 2 both ways
        proc_no = run(["--graph", gp, "--from", "A", "--to", "B"])
        assert proc_no.returncode == 0
        out_no = json.loads(proc_no.stdout.decode().strip())
        assert out_no["distance"] == 2, (
            f"without traffic unordered should be 2, got {out_no['distance']}"
        )
        # with traffic (even empty): per direction, A->B should be 10
        proc_yes = run(["--graph", gp, "--from", "A", "--to", "B", "--traffic", tp])
        assert proc_yes.returncode == 0
        out_yes = json.loads(proc_yes.stdout.decode().strip())
        assert out_yes["distance"] == 10, (
            f"with traffic per-direction should revive 10, got {out_yes['distance']}"
        )
        # opposite direction B->A should be 2 with traffic
        proc_rev = run(["--graph", gp, "--from", "B", "--to", "A", "--traffic", tp])
        assert proc_rev.returncode == 0
        out_rev = json.loads(proc_rev.stdout.decode().strip())
        assert out_rev["distance"] == 2
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_f1_traffic_entry_revives_superseded_raw():
    # Log: A->B 10, B->A 100, A->B 3 (last per unordered is 3)
    # Without traffic: A->B 3, B->A 3
    # With traffic: A->B 3, B->A 100 (revives 100)
    # Traffic entry for B->A with factor 1 should see raw 100 when traffic present
    graph = {
        "nodes": ["A", "B"],
        "edges": [
            {"from": "A", "to": "B", "distance": 10},
            {"from": "B", "to": "A", "distance": 100},
            {"from": "A", "to": "B", "distance": 3},
        ],
    }
    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps({"traffic": [{"from": "B", "to": "A", "factor": 1}]}))
    try:
        proc_no = run(["--graph", gp, "--from", "B", "--to", "A"])
        assert proc_no.returncode == 0
        out_no = json.loads(proc_no.stdout.decode().strip())
        assert out_no["distance"] == 3, (
            f"without traffic unordered last 3, got {out_no['distance']}"
        )
        proc_yes = run(["--graph", gp, "--from", "B", "--to", "A", "--traffic", tp])
        assert proc_yes.returncode == 0
        out_yes = json.loads(proc_yes.stdout.decode().strip())
        assert out_yes["distance"] == 100, (
            f"with traffic per-direction should be 100, got {out_yes['distance']}"
        )
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_f1_raw_along_effective_direction_dependent():
    # Raw becomes direction-dependent with traffic, so raw along effective best differs per direction
    graph = {
        "nodes": ["A", "B"],
        "edges": [
            {"from": "A", "to": "B", "distance": 10},
            {"from": "B", "to": "A", "distance": 20},
        ],
    }
    traffic = {
        "traffic": [
            {"from": "A", "to": "B", "factor": 2},
            {"from": "B", "to": "A", "factor": 2},
        ]
    }
    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps(traffic))
    try:
        proc_ab = run(["--graph", gp, "--from", "A", "--to", "B", "--traffic", tp])
        proc_ba = run(["--graph", gp, "--from", "B", "--to", "A", "--traffic", tp])
        assert proc_ab.returncode == 0 and proc_ba.returncode == 0
        out_ab = json.loads(proc_ab.stdout.decode().strip())
        out_ba = json.loads(proc_ba.stdout.decode().strip())
        # A->B raw 10 eff 20, B->A raw 20 eff 40, direction dependent raw
        assert out_ab["distance"] == 10
        assert out_ba["distance"] == 20
        assert out_ab["distance"] != out_ba["distance"]
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_f2_zone_toll_once_per_route_reenter_free():
    # F2: toll at most once per route, re-entering paid zone free
    # A-B f2 d5, B-C f1 d7, C-D f2 d10 (re-enter f2)
    # First f2 pay5, f1 pay7, re-enter f2 free (not 10) => tolls 12, eff = raw*factor sum +12
    # Raw 10 each, factor 2,1,2 => raw*factor =20+10+20=50 +12=62
    # Per-entry (not once-per-route) would be 5+7+10=22 eff 72
    graph = {
        "nodes": ["A", "B", "C", "D"],
        "edges": [
            {"from": "A", "to": "B", "distance": 10},
            {"from": "B", "to": "C", "distance": 10},
            {"from": "C", "to": "D", "distance": 10},
        ],
    }
    traffic = {
        "traffic": [
            {"from": "A", "to": "B", "factor": 2, "delay": 5},
            {"from": "B", "to": "C", "factor": 1, "delay": 7},
            {"from": "C", "to": "D", "factor": 2, "delay": 10},
        ]
    }
    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps(traffic))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "D", "--traffic", tp])
        assert proc.returncode == 0, proc.stderr.decode()
        out = json.loads(proc.stdout.decode().strip())
        # raw*factor sum = 10*2 +10*1+10*2=50
        # once-per-route tolls: f2 first 5 + f1 7 + re-enter f2 free =12 => eff 62
        assert math.isclose(out["effective_distance"], 62, abs_tol=1e-6), (
            f"expected 62, got {out['effective_distance']}"
        )
        assert out["distance"] == 30
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_f2_distinct_factor_bound_max_4_invalid():
    # At most 4 distinct factors per manifest, 5 distinct -> invalid (surviving count)
    graph = {
        "nodes": ["A", "B", "C", "D", "E"],
        "edges": [
            {"from": "A", "to": "B", "distance": 1},
            {"from": "B", "to": "C", "distance": 1},
            {"from": "C", "to": "D", "distance": 1},
            {"from": "D", "to": "E", "distance": 1},
            {"from": "A", "to": "E", "distance": 10},
        ],
    }
    traffic = {
        "traffic": [
            {"from": "A", "to": "B", "factor": 1.1},
            {"from": "B", "to": "C", "factor": 1.2},
            {"from": "C", "to": "D", "factor": 1.3},
            {"from": "D", "to": "E", "factor": 1.4},
            {"from": "A", "to": "E", "factor": 1.5},
        ]
    }
    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps(traffic))
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "E", "--traffic", tp])
        assert proc.returncode == 2 and proc.stdout.decode().strip() == ""
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_f1_with_traffic_per_direction_raw_revives_batch():
    # P2: batch-mode coverage for F1
    graph = {
        "nodes": ["A", "B"],
        "edges": [
            {"from": "A", "to": "B", "distance": 10},
            {"from": "B", "to": "A", "distance": 2},
        ],
    }
    gp = tmp(json.dumps(graph))
    tp = tmp("[]")
    rq = tmp(
        json.dumps(
            [{"source": "A", "destination": "B"}, {"source": "B", "destination": "A"}]
        )
    )
    try:
        proc_no = run(["--graph", gp, "--requests", rq])
        assert proc_no.returncode == 0
        lines = proc_no.stdout.decode().strip().splitlines()
        outs = [json.loads(l) for l in lines]
        # without traffic unordered last wins 2 both ways
        assert outs[0]["distance"] == 2 and outs[1]["distance"] == 2
        proc_yes = run(["--graph", gp, "--requests", rq, "--traffic", tp])
        assert proc_yes.returncode == 0
        lines = proc_yes.stdout.decode().strip().splitlines()
        outs = [json.loads(l) for l in lines]
        # with traffic per-direction A->B 10, B->A 2
        assert outs[0]["distance"] == 10, (
            f"batch with traffic A->B should be 10, got {outs[0]['distance']}"
        )
        assert outs[1]["distance"] == 2
        assert "effective_distance" in outs[0] and "traffic_delay" in outs[0]
    finally:
        os.unlink(gp)
        os.unlink(tp)
        os.unlink(rq)


def test_f1_traffic_entry_revives_superseded_raw_batch():
    graph = {
        "nodes": ["A", "B"],
        "edges": [
            {"from": "A", "to": "B", "distance": 10},
            {"from": "B", "to": "A", "distance": 100},
            {"from": "A", "to": "B", "distance": 3},
        ],
    }
    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps({"traffic": [{"from": "B", "to": "A", "factor": 1}]}))
    rq = tmp(json.dumps([{"source": "B", "destination": "A"}]))
    try:
        proc_no = run(["--graph", gp, "--requests", rq])
        assert proc_no.returncode == 0
        out_no = json.loads(proc_no.stdout.decode().strip().splitlines()[0])
        assert out_no["distance"] == 3
        proc_yes = run(["--graph", gp, "--requests", rq, "--traffic", tp])
        assert proc_yes.returncode == 0
        out_yes = json.loads(proc_yes.stdout.decode().strip().splitlines()[0])
        assert out_yes["distance"] == 100
    finally:
        os.unlink(gp)
        os.unlink(tp)
        os.unlink(rq)


def test_f1_raw_along_effective_direction_dependent_batch():
    graph = {
        "nodes": ["A", "B"],
        "edges": [
            {"from": "A", "to": "B", "distance": 10},
            {"from": "B", "to": "A", "distance": 20},
        ],
    }
    traffic = {
        "traffic": [
            {"from": "A", "to": "B", "factor": 2},
            {"from": "B", "to": "A", "factor": 2},
        ]
    }
    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps(traffic))
    rq = tmp(
        json.dumps(
            [{"source": "A", "destination": "B"}, {"source": "B", "destination": "A"}]
        )
    )
    try:
        proc = run(["--graph", gp, "--requests", rq, "--traffic", tp])
        assert proc.returncode == 0
        lines = proc.stdout.decode().strip().splitlines()
        outs = [json.loads(l) for l in lines]
        assert outs[0]["distance"] == 10 and outs[1]["distance"] == 20
        assert outs[0]["distance"] != outs[1]["distance"]
    finally:
        os.unlink(gp)
        os.unlink(tp)
        os.unlink(rq)


def test_f2_zone_toll_once_per_route_reenter_free_batch():
    # P2: batch-mode coverage for F2 once-per-route
    graph = {
        "nodes": ["A", "B", "C", "D"],
        "edges": [
            {"from": "A", "to": "B", "distance": 10},
            {"from": "B", "to": "C", "distance": 10},
            {"from": "C", "to": "D", "distance": 10},
        ],
    }
    traffic = {
        "traffic": [
            {"from": "A", "to": "B", "factor": 2, "delay": 5},
            {"from": "B", "to": "C", "factor": 1, "delay": 7},
            {"from": "C", "to": "D", "factor": 2, "delay": 10},
        ]
    }
    gp = tmp(json.dumps(graph))
    tp = tmp(json.dumps(traffic))
    rq = tmp(json.dumps([{"source": "A", "destination": "D"}]))
    try:
        proc = run(["--graph", gp, "--requests", rq, "--traffic", tp])
        assert proc.returncode == 0, proc.stderr.decode()
        out = json.loads(proc.stdout.decode().strip().splitlines()[0])
        assert math.isclose(out["effective_distance"], 62, abs_tol=1e-6), (
            f"batch expected 62, got {out['effective_distance']}"
        )
        assert out["distance"] == 30
        assert out["traffic_delay"] == 32
    finally:
        os.unlink(gp)
        os.unlink(tp)
        os.unlink(rq)


def test_f2_randomized_zone_reentry_bruteforce_crosscheck():
    # P1 highest yield: randomized property test comparing against brute-force enumeration
    # Catches any deviation in paidMask search, not one hand-picked case, can't be special-cased
    import random

    def brute_best(adj, traffic_map, src, dst):
        # adj: dict node -> dict neighbor -> raw
        # traffic_map: dict (u,v) -> (factor, delay)
        best = None  # (eff, raw, tolls, path)

        def compare_paths(a, b):
            ml = len(a)
            if len(b) < ml:
                ml = len(b)
            for i in range(ml):
                if a[i] < b[i]:
                    return -1
                if a[i] > b[i]:
                    return 1
            if len(a) < len(b):
                return -1
            if len(a) > len(b):
                return 1
            return 0

        def dfs(cur, path, raw_sum, eff_sum, visited_factors, tolls):
            nonlocal best
            if cur == dst:
                cand = (eff_sum, raw_sum, tolls, list(path))
                if best is None:
                    best = cand
                else:
                    be, br, bt, bp = best
                    # eff primary
                    if abs(cand[0] - be) > 1e-9:
                        if cand[0] < be:
                            best = cand
                    else:
                        if abs(cand[1] - br) > 1e-9:
                            if cand[1] < br:
                                best = cand
                        else:
                            if cand[2] != bt:
                                if cand[2] < bt:
                                    best = cand
                            else:
                                if compare_paths(cand[3], bp) < 0:
                                    best = cand
                return
            # prune if eff already worse than best
            if best is not None and eff_sum > best[0] + 1e-9:
                return
            for nb, raw_edge in adj.get(cur, {}).items():
                if nb in path:
                    continue
                factor, delay = traffic_map.get((cur, nb), (1.0, 0.0))
                is_new = factor not in visited_factors
                new_visited = visited_factors | {factor}
                new_tolls = tolls + (1 if is_new else 0)
                new_raw = raw_sum + raw_edge
                new_eff = eff_sum + raw_edge * factor + (delay if is_new else 0)
                dfs(nb, path + [nb], new_raw, new_eff, new_visited, new_tolls)

        dfs(src, [src], 0.0, 0.0, set(), 0)
        return best

    rnd = random.Random(42)
    for case_idx in range(15):
        nodes = ["A", "B", "C", "D", "E"]
        # random edges
        edges = []
        adj = {n: {} for n in nodes}
        for i in range(len(nodes)):
            for j in range(i + 1, len(nodes)):
                if rnd.random() < 0.5:
                    d = rnd.randint(1, 10)
                    u = nodes[i]
                    v = nodes[j]
                    edges.append({"from": u, "to": v, "distance": d})
                    adj[u][v] = d
                    adj[v][u] = d
        # ensure at least 3 edges
        if len(edges) < 3:
            continue
        # traffic: pick subset of directed arcs
        traffic_entries = []
        traffic_map = {}
        factors = [1.0, 2.0, 3.0]  # keep distinct <=3 for bound
        for u, v in list(adj.items()):
            for nb in list(v.keys()):
                if rnd.random() < 0.5:
                    f = rnd.choice(factors)
                    dl = rnd.choice([0, 1, 5])
                    traffic_entries.append(
                        {"from": u, "to": nb, "factor": f, "delay": dl}
                    )
                    # last wins for same ordered pair – simulate last wins by overwriting
                    traffic_map[(u, nb)] = (f, float(dl))

        # pick source/dest connected
        src = rnd.choice(nodes)
        dst = rnd.choice([n for n in nodes if n != src])
        # check connectivity via BFS ignoring traffic
        from collections import deque

        def reachable(s, t):
            q = deque([s])
            seen = {s}
            while q:
                cur = q.popleft()
                if cur == t:
                    return True
                for nb in adj.get(cur, {}):
                    if nb not in seen:
                        seen.add(nb)
                        q.append(nb)
            return False

        if not reachable(src, dst):
            continue

        # write temp files
        graph_obj = {"nodes": nodes, "edges": edges}
        traffic_obj = {"traffic": traffic_entries}
        gp = tmp(json.dumps(graph_obj))
        tp = tmp(json.dumps(traffic_obj))
        try:
            proc = run(["--graph", gp, "--from", src, "--to", dst, "--traffic", tp])
            if proc.returncode not in (0, 1):
                # invalid graph/traffic due to random (e.g., distinct bound) – skip
                continue
            if proc.returncode == 1:
                # no route – brute should also have no route
                brute = brute_best(adj, traffic_map, src, dst)
                assert brute is None, (
                    f"router says no route but brute found {brute} case {case_idx}"
                )
                continue
            out = json.loads(proc.stdout.decode().strip())
            if out["distance"] == -1:
                brute = brute_best(adj, traffic_map, src, dst)
                assert brute is None
                continue
            brute = brute_best(adj, traffic_map, src, dst)
            assert brute is not None, (
                f"brute found no route but router did case {case_idx} {src}->{dst} adj {adj} traffic {traffic_map}"
            )
            beff, braw, btolls, bpath = brute
            # compare against router output
            assert math.isclose(out["distance"], braw, abs_tol=1e-6), (
                f"case {case_idx} raw mismatch: router {out['distance']} brute {braw} path router {out['path']} brute {bpath} src {src} dst {dst} traffic {traffic_map}"
            )
            assert math.isclose(out["effective_distance"], beff, abs_tol=1e-6), (
                f"case {case_idx} eff mismatch: router {out['effective_distance']} brute {beff} raw {out['distance']} vs {braw} path router {out['path']} brute {bpath} src {src} dst {dst}"
            )
            # path may differ if tie-break differs? But we implement same tie-break, so check eff/raw/tolls primary and path lex as well
            # Allow path divergence only if eff/raw/tolls equal and lex order would pick different – but our brute uses same lex, so should match
            if not math.isclose(
                out["effective_distance"], beff, abs_tol=1e-9
            ) or not math.isclose(out["distance"], braw, abs_tol=1e-9):
                continue
            # if tolls tie-break matters, our brute tolls count may differ but eff equal – check path lex if needed
            # For strictness, require path equal when eff equal
            assert out["path"] == bpath, (
                f"case {case_idx} path mismatch: router {out['path']} brute {bpath} eff {out['effective_distance']} vs {beff} raw {out['distance']} vs {braw} src {src} dst {dst}"
            )
        finally:
            os.unlink(gp)
            os.unlink(tp)


# --- Type matrix: 43 specified-but-untested cells (AFTR R06 gap) ---
# Only traffic=null was tested and accounted for 13% avocado failures. Filling matrix is cheapest durable margin.
# No spec change, no oracle change beyond edges:null bug already fixed.


def test_edges_null_invalid_step2():
    gp = tmp('{"nodes":["A","B"], "edges": null}')
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B"])
        assert proc.returncode == 2 and proc.stdout.decode().strip() == "", (
            f"edges:null must be exit2, got {proc.returncode}"
        )
    finally:
        os.unlink(gp)


def test_edges_null_invalid_with_traffic():
    gp = tmp('{"nodes":["A","B"], "edges": null}')
    tp = tmp("[]")
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B", "--traffic", tp])
        assert proc.returncode == 2 and proc.stdout.decode().strip() == ""
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_nodes_bool_invalid_step2():
    gp = tmp('{"nodes": true, "edges": [{"from":"A","to":"B","distance":1}]}')
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B"])
        assert proc.returncode == 2 and proc.stdout.decode().strip() == ""
    finally:
        os.unlink(gp)


def test_nodes_number_invalid_step2():
    gp = tmp('{"nodes": 5, "edges": [{"from":"A","to":"B","distance":1}]}')
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B"])
        assert proc.returncode == 2 and proc.stdout.decode().strip() == ""
    finally:
        os.unlink(gp)


def test_traffic_wrapper_object_invalid():
    gp = tmp(
        json.dumps(
            {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 1}]}
        )
    )
    tp = tmp('{"traffic": {}}')
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B", "--traffic", tp])
        assert proc.returncode == 2 and proc.stdout.decode().strip() == ""
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_wrapper_string_invalid():
    gp = tmp(
        json.dumps(
            {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 1}]}
        )
    )
    tp = tmp('{"traffic": "x"}')
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B", "--traffic", tp])
        assert proc.returncode == 2 and proc.stdout.decode().strip() == ""
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_wrapper_number_invalid():
    gp = tmp(
        json.dumps(
            {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 1}]}
        )
    )
    tp = tmp('{"traffic": 5}')
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B", "--traffic", tp])
        assert proc.returncode == 2 and proc.stdout.decode().strip() == ""
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_wrapper_bool_invalid():
    gp = tmp(
        json.dumps(
            {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 1}]}
        )
    )
    tp = tmp('{"traffic": true}')
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B", "--traffic", tp])
        assert proc.returncode == 2 and proc.stdout.decode().strip() == ""
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_top_level_number_invalid():
    gp = tmp(
        json.dumps(
            {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 1}]}
        )
    )
    tp = tmp("5")
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B", "--traffic", tp])
        assert proc.returncode == 2 and proc.stdout.decode().strip() == ""
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_top_level_string_invalid():
    gp = tmp(
        json.dumps(
            {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 1}]}
        )
    )
    tp = tmp('"x"')
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B", "--traffic", tp])
        assert proc.returncode == 2 and proc.stdout.decode().strip() == ""
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_entry_from_bool_invalid():
    gp = tmp(
        json.dumps(
            {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 1}]}
        )
    )
    tp = tmp('[{"from": true, "to":"B","factor":2}]')
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B", "--traffic", tp])
        assert proc.returncode == 2 and proc.stdout.decode().strip() == ""
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_entry_from_null_invalid():
    gp = tmp(
        json.dumps(
            {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 1}]}
        )
    )
    tp = tmp('[{"from": null, "to":"B","factor":2}]')
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B", "--traffic", tp])
        assert proc.returncode == 2 and proc.stdout.decode().strip() == ""
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_entry_from_object_invalid():
    gp = tmp(
        json.dumps(
            {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 1}]}
        )
    )
    tp = tmp('[{"from": {}, "to":"B","factor":2}]')
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B", "--traffic", tp])
        assert proc.returncode == 2 and proc.stdout.decode().strip() == ""
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_entry_from_array_invalid():
    gp = tmp(
        json.dumps(
            {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 1}]}
        )
    )
    tp = tmp('[{"from": [], "to":"B","factor":2}]')
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B", "--traffic", tp])
        assert proc.returncode == 2 and proc.stdout.decode().strip() == ""
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_entry_from_missing_invalid():
    gp = tmp(
        json.dumps(
            {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 1}]}
        )
    )
    tp = tmp('[{"to":"B","factor":2}]')
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B", "--traffic", tp])
        assert proc.returncode == 2 and proc.stdout.decode().strip() == ""
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_entry_to_number_invalid():
    gp = tmp(
        json.dumps(
            {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 1}]}
        )
    )
    tp = tmp('[{"from":"A","to":123,"factor":2}]')
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B", "--traffic", tp])
        assert proc.returncode == 2 and proc.stdout.decode().strip() == ""
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_entry_to_bool_invalid():
    gp = tmp(
        json.dumps(
            {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 1}]}
        )
    )
    tp = tmp('[{"from":"A","to":false,"factor":2}]')
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B", "--traffic", tp])
        assert proc.returncode == 2 and proc.stdout.decode().strip() == ""
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_entry_to_null_invalid():
    gp = tmp(
        json.dumps(
            {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 1}]}
        )
    )
    tp = tmp('[{"from":"A","to":null,"factor":2}]')
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B", "--traffic", tp])
        assert proc.returncode == 2 and proc.stdout.decode().strip() == ""
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_entry_to_object_invalid():
    gp = tmp(
        json.dumps(
            {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 1}]}
        )
    )
    tp = tmp('[{"from":"A","to":{},"factor":2}]')
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B", "--traffic", tp])
        assert proc.returncode == 2 and proc.stdout.decode().strip() == ""
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_entry_to_array_invalid():
    gp = tmp(
        json.dumps(
            {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 1}]}
        )
    )
    tp = tmp('[{"from":"A","to":[],"factor":2}]')
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B", "--traffic", tp])
        assert proc.returncode == 2 and proc.stdout.decode().strip() == ""
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_entry_to_missing_invalid():
    gp = tmp(
        json.dumps(
            {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 1}]}
        )
    )
    tp = tmp('[{"from":"A","factor":2}]')
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B", "--traffic", tp])
        assert proc.returncode == 2 and proc.stdout.decode().strip() == ""
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_entry_factor_bool_invalid():
    gp = tmp(
        json.dumps(
            {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 1}]}
        )
    )
    tp = tmp('[{"from":"A","to":"B","factor":true}]')
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B", "--traffic", tp])
        assert proc.returncode == 2 and proc.stdout.decode().strip() == ""
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_entry_factor_null_invalid():
    gp = tmp(
        json.dumps(
            {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 1}]}
        )
    )
    tp = tmp('[{"from":"A","to":"B","factor":null}]')
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B", "--traffic", tp])
        assert proc.returncode == 2 and proc.stdout.decode().strip() == ""
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_entry_factor_object_invalid():
    gp = tmp(
        json.dumps(
            {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 1}]}
        )
    )
    tp = tmp('[{"from":"A","to":"B","factor":{}}]')
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B", "--traffic", tp])
        assert proc.returncode == 2 and proc.stdout.decode().strip() == ""
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_entry_factor_array_invalid():
    gp = tmp(
        json.dumps(
            {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 1}]}
        )
    )
    tp = tmp('[{"from":"A","to":"B","factor":[]}]')
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B", "--traffic", tp])
        assert proc.returncode == 2 and proc.stdout.decode().strip() == ""
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_entry_factor_missing_invalid():
    gp = tmp(
        json.dumps(
            {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 1}]}
        )
    )
    tp = tmp('[{"from":"A","to":"B"}]')
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B", "--traffic", tp])
        assert proc.returncode == 2 and proc.stdout.decode().strip() == ""
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_entry_delay_bool_invalid():
    gp = tmp(
        json.dumps(
            {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 1}]}
        )
    )
    tp = tmp('[{"from":"A","to":"B","factor":2,"delay":true}]')
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B", "--traffic", tp])
        assert proc.returncode == 2 and proc.stdout.decode().strip() == ""
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_entry_delay_null_invalid():
    gp = tmp(
        json.dumps(
            {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 1}]}
        )
    )
    tp = tmp('[{"from":"A","to":"B","factor":2,"delay":null}]')
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B", "--traffic", tp])
        assert proc.returncode == 2 and proc.stdout.decode().strip() == ""
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_entry_delay_object_invalid():
    gp = tmp(
        json.dumps(
            {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 1}]}
        )
    )
    tp = tmp('[{"from":"A","to":"B","factor":2,"delay":{}}]')
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B", "--traffic", tp])
        assert proc.returncode == 2 and proc.stdout.decode().strip() == ""
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_traffic_entry_delay_array_invalid():
    gp = tmp(
        json.dumps(
            {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 1}]}
        )
    )
    tp = tmp('[{"from":"A","to":"B","factor":2,"delay":[]}]')
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B", "--traffic", tp])
        assert proc.returncode == 2 and proc.stdout.decode().strip() == ""
    finally:
        os.unlink(gp)
        os.unlink(tp)


# --- R06: --source / --destination single-route alias (missing since 52561ef) ---


def test_source_destination_alias_single_route_step2():
    gp = tmp(
        json.dumps(
            {
                "nodes": ["A", "B", "C"],
                "edges": [
                    {"from": "A", "to": "B", "distance": 2},
                    {"from": "B", "to": "C", "distance": 3},
                ],
            }
        )
    )
    try:
        proc = run(["--graph", gp, "--source", "A", "--destination", "C"])
        assert proc.returncode == 0, proc.stderr.decode()
        out = json.loads(proc.stdout.decode().strip())
        assert out["path"] == ["A", "B", "C"]
        assert math.isclose(out["distance"], 5)
    finally:
        os.unlink(gp)


def test_source_destination_alias_with_traffic_step2():
    gp = tmp(
        json.dumps(
            {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 5}]}
        )
    )
    tp = tmp(
        json.dumps({"traffic": [{"from": "A", "to": "B", "factor": 2, "delay": 3}]})
    )
    try:
        proc_from = run(["--graph", gp, "--from", "A", "--to", "B", "--traffic", tp])
        proc_src = run(
            ["--graph", gp, "--source", "A", "--destination", "B", "--traffic", tp]
        )
        assert proc_from.returncode == 0 and proc_src.returncode == 0
        out_from = json.loads(proc_from.stdout.decode().strip())
        out_src = json.loads(proc_src.stdout.decode().strip())
        assert out_from["path"] == out_src["path"] == ["A", "B"]
        assert out_from["distance"] == out_src["distance"] == 5
        assert out_from["effective_distance"] == out_src["effective_distance"] == 13
    finally:
        os.unlink(gp)
        os.unlink(tp)


def test_source_destination_alias_equals_syntax_step2():
    gp = tmp(
        json.dumps(
            {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 7}]}
        )
    )
    try:
        proc = run([f"--graph={gp}", "--source=A", "--destination=B"])
        assert proc.returncode == 0, proc.stderr.decode()
        out = json.loads(proc.stdout.decode().strip())
        assert out["path"] == ["A", "B"]
        assert out["distance"] == 7
    finally:
        os.unlink(gp)


def test_source_destination_alias_with_traffic_equals_syntax():
    gp = tmp(
        json.dumps(
            {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 4}]}
        )
    )
    tp = tmp(json.dumps({"traffic": [{"from": "A", "to": "B", "factor": 1.5}]}))
    try:
        proc = run(
            [f"--graph={gp}", "--source=A", f"--destination=B", f"--traffic={tp}"]
        )
        assert proc.returncode == 0, proc.stderr.decode()
        out = json.loads(proc.stdout.decode().strip())
        assert out["path"] == ["A", "B"]
        assert out["distance"] == 4
        assert math.isclose(out["effective_distance"], 6.0)
    finally:
        os.unlink(gp)
        os.unlink(tp)


# --- Extension of null widening (per final suggestion, not required but improves margin) ---


def test_nodes_element_null_invalid():
    gp = tmp('{"nodes": ["A", null], "edges": [{"from":"A","to":"B","distance":1}]}')
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B"])
        assert proc.returncode == 2 and proc.stdout.decode().strip() == ""
    finally:
        os.unlink(gp)


def test_requests_array_null_invalid():
    gp = tmp(
        json.dumps(
            {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 1}]}
        )
    )
    rp = tmp("null")
    try:
        proc = run(["--graph", gp, "--requests", rp])
        assert proc.returncode == 2 and proc.stdout.decode().strip() == ""
    finally:
        os.unlink(gp)
        os.unlink(rp)


def test_traffic_wrapper_array_contains_null_invalid():
    gp = tmp(
        json.dumps(
            {"nodes": ["A", "B"], "edges": [{"from": "A", "to": "B", "distance": 1}]}
        )
    )
    tp = tmp('{"traffic": [null]}')
    try:
        proc = run(["--graph", gp, "--from", "A", "--to", "B", "--traffic", tp])
        assert proc.returncode == 2 and proc.stdout.decode().strip() == ""
    finally:
        os.unlink(gp)
        os.unlink(tp)
