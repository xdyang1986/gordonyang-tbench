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
        assert elapsed <= 25 * base_elapsed + 1.0
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
        assert elapsed <= 25 * base_elapsed + 1.0
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
        assert elapsed <= 25 * base_elapsed + 2.0
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
        assert elapsed <= 25 * base_elapsed + 1.0
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
            assert elapsed <= 25 * base_elapsed + 12.0
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
