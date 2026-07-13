"""
Verify the outputs of the router tool.
"""

import json, subprocess, tempfile, os


def run_router(cfg, requests):
    with tempfile.TemporaryDirectory() as td:
        cfg_path = os.path.join(td, "cfg.json")
        req_path = os.path.join(td, "req.jsonl")
        with open(cfg_path, "w") as f:
            json.dump(cfg, f)
        with open(req_path, "w") as f:
            for r in requests:
                f.write(json.dumps(r) + "\n")
        proc = subprocess.run(
            ["router", "--config", cfg_path, "--requests", req_path],
            capture_output=True,
            text=True,
        )
        out = proc.stdout.strip().splitlines() if proc.stdout.strip() else []
        parsed = [json.loads(l) for l in out]
        return proc.returncode, parsed, proc.stderr


def test_region_affinity_exact_vs_continent_vs_none():
    cfg = {
        "strategy": "latency",
        "max_replicas": 1,
        "providers": [
            {"id": "exact", "region": "us-east", "latency_ms": 100, "cost_per_1k": 0.01,
             "error_rate": 0, "capacity_rps": 10, "status": "up", "health": 1},
            {"id": "continent", "region": "us-west", "latency_ms": 100, "cost_per_1k": 0.01,
             "error_rate": 0, "capacity_rps": 10, "status": "up", "health": 1},
            {"id": "far", "region": "eu-west", "latency_ms": 100, "cost_per_1k": 0.01,
             "error_rate": 0, "capacity_rps": 10, "status": "up", "health": 1},
        ],
    }
    req = [{"id": "r", "user_region": "us-east"}]
    code, out, _ = run_router(cfg, req)
    assert out == [["exact"]]
    cfg2 = {
        "strategy": "latency",
        "max_replicas": 1,
        "providers": [
            {"id": "continent", "region": "us-west", "latency_ms": 100, "cost_per_1k": 0.01,
             "error_rate": 0, "capacity_rps": 10, "status": "up", "health": 1},
            {"id": "far", "region": "eu-west", "latency_ms": 100, "cost_per_1k": 0.01,
             "error_rate": 0, "capacity_rps": 10, "status": "up", "health": 1},
        ],
    }
    code2, out2, _ = run_router(cfg2, [{"id": "r", "user_region": "us-east"}])
    assert out2 == [["continent"]]


def test_tenant_budget_enforcement():
    cfg = {
        "strategy": "cost",
        "max_replicas": 1,
        "tenant_budgets": {"acme": 0.00002},
        "providers": [
            {"id": "expensive", "region": "us", "latency_ms": 10, "cost_per_1k": 0.05,
             "error_rate": 0, "capacity_rps": 10, "status": "up", "health": 1},
            {"id": "cheap", "region": "us", "latency_ms": 10, "cost_per_1k": 0.01,
             "error_rate": 0, "capacity_rps": 10, "status": "up", "health": 1},
        ],
    }
    # per-request cost: expensive 0.00005, cheap 0.00001. Budget 0.00002 -> two cheap, then exhausted.
    req = [
        {"id": "1", "user_region": "us", "tenant": "acme"},
        {"id": "2", "user_region": "us", "tenant": "acme"},
        {"id": "3", "user_region": "us", "tenant": "acme"},
    ]
    code, out, _ = run_router(cfg, req)
    assert out[0] == ["cheap"]
    assert out[1] == ["cheap"]
    assert out[2] == []
    assert code == 1


def test_tie_breaking_health_cost_latency_id():
    cfg2 = {
        "strategy": "latency",
        "max_replicas": 1,
        "providers": [
            {"id": "lowhealth", "region": "us", "latency_ms": 45, "cost_per_1k": 0.01,
             "error_rate": 0, "capacity_rps": 10, "status": "up", "health": 0.9},
            {"id": "highhealth", "region": "us", "latency_ms": 50, "cost_per_1k": 0.01,
             "error_rate": 0, "capacity_rps": 10, "status": "up", "health": 1.0},
        ],
    }
    # scores: lowhealth 45*0.5/0.9 = 25 ; highhealth 50*0.5/1.0 = 25 -> tie -> higher health wins
    code, out, _ = run_router(cfg2, [{"id": "r", "user_region": "us"}])
    assert out == [["highhealth"]]


def test_region_diverse_max_replicas_with_capacity_consumption():
    cfg = {
        "strategy": "latency",
        "max_replicas": 2,
        "providers": [
            {"id": "us1", "region": "us-east", "latency_ms": 30, "cost_per_1k": 0.01,
             "error_rate": 0, "capacity_rps": 1, "status": "up", "health": 1},
            {"id": "us2", "region": "us-east", "latency_ms": 31, "cost_per_1k": 0.01,
             "error_rate": 0, "capacity_rps": 10, "status": "up", "health": 1},
            {"id": "eu1", "region": "eu-west", "latency_ms": 32, "cost_per_1k": 0.01,
             "error_rate": 0, "capacity_rps": 10, "status": "up", "health": 1},
        ],
    }
    req = [{"id": "1", "user_region": "us-east"}, {"id": "2", "user_region": "us-east"}]
    code, out, _ = run_router(cfg, req)
    # first request picks us1+eu1 (region-diverse), consumes us1 primary capacity to 0;
    # second request us1 gone -> us2+eu1
    assert out[0] == ["us1", "eu1"]
    assert out[1] == ["us2", "eu1"]


def test_priority_high_vs_low_cost_latency_tradeoff():
    cfg = {
        "strategy": "balanced",
        "max_replicas": 1,
        "providers": [
            {"id": "fast", "region": "us", "latency_ms": 10, "cost_per_1k": 0.1,
             "error_rate": 0, "capacity_rps": 10, "status": "up", "health": 1},
            {"id": "cheap", "region": "us", "latency_ms": 80, "cost_per_1k": 0.01,
             "error_rate": 0, "capacity_rps": 10, "status": "up", "health": 1},
        ],
    }
    _, out_low, _ = run_router(cfg, [{"id": "r", "user_region": "us", "priority": "low"}])
    _, out_high, _ = run_router(cfg, [{"id": "r", "user_region": "us", "priority": "high"}])
    assert out_low == [["cheap"]]
    assert out_high == [["fast"]]


def test_sla_and_invalid_priority():
    cfg = {
        "strategy": "latency",
        "max_replicas": 1,
        "providers": [
            {"id": "p", "region": "us", "latency_ms": 200, "cost_per_1k": 0.01,
             "error_rate": 0, "capacity_rps": 10, "status": "up", "health": 1},
        ],
    }
    code, out, _ = run_router(cfg, [{"id": "r", "user_region": "us", "sla_ms": 100}])
    assert out == [[]] and code == 1
    code2, _, _ = run_router(cfg, [{"id": "r", "user_region": "us", "priority": "urgent"}])
    assert code2 == 2


def test_blank_and_whitespace_lines_ignored():
    cfg = {
        "strategy": "latency",
        "max_replicas": 1,
        "providers": [
            {"id": "p", "region": "us", "latency_ms": 10, "cost_per_1k": 0.01,
             "error_rate": 0, "capacity_rps": 10, "status": "up", "health": 1},
        ],
    }
    with tempfile.TemporaryDirectory() as td:
        cp = os.path.join(td, "c.json")
        rp = os.path.join(td, "r.jsonl")
        with open(cp, "w") as f:
            json.dump(cfg, f)
        with open(rp, "w") as f:
            f.write('{"id":"1","user_region":"us"}\n')
            f.write("\n")
            f.write("   \n")
            f.write('{"id":"2","user_region":"us"}\n')
        proc = subprocess.run(
            ["router", "--config", cp, "--requests", rp], capture_output=True, text=True
        )
        out = proc.stdout.strip().splitlines()
        assert len(out) == 2


def test_invalid_config_missing_field():
    cfg = {
        "strategy": "latency",
        "providers": [
            {"id": "", "region": "us", "latency_ms": -1, "cost_per_1k": 0.01,
             "error_rate": 0, "capacity_rps": 1, "status": "up", "health": 1},
        ],
    }
    code, out, _ = run_router(cfg, [{"id": "r", "user_region": "us"}])
    assert code == 2


def test_degraded_partial_replicas():
    cfg = {
        "strategy": "latency",
        "max_replicas": 3,
        "providers": [
            {"id": "a", "region": "us-east", "latency_ms": 10, "cost_per_1k": 0.01,
             "error_rate": 0, "capacity_rps": 10, "status": "up", "health": 1},
            {"id": "b", "region": "eu-west", "latency_ms": 10, "cost_per_1k": 0.01,
             "error_rate": 0, "capacity_rps": 10, "status": "up", "health": 1},
        ],
    }
    code, out, _ = run_router(cfg, [{"id": "r", "user_region": "us"}])
    assert len(out[0]) == 2
    assert code == 1
