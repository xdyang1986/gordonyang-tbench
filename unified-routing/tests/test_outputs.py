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


def test_exit_code_0_fully_routed():
    cfg = {
        "strategy": "latency",
        "max_replicas": 1,
        "providers": [
            {"id": "p", "region": "us", "latency_ms": 10, "cost_per_1k": 0.01,
             "error_rate": 0, "capacity_rps": 10, "status": "up", "health": 1},
        ],
    }
    code, out, _ = run_router(cfg, [{"id": "r", "user_region": "us"}])
    assert out == [["p"]]
    assert code == 0


def test_cost_strategy_picks_cheaper():
    cfg = {
        "strategy": "cost",
        "max_replicas": 1,
        "providers": [
            {"id": "fast-exp", "region": "us", "latency_ms": 10, "cost_per_1k": 0.1,
             "error_rate": 0, "capacity_rps": 10, "status": "up", "health": 1},
            {"id": "slow-cheap", "region": "us", "latency_ms": 100, "cost_per_1k": 0.01,
             "error_rate": 0, "capacity_rps": 10, "status": "up", "health": 1},
        ],
    }
    # cost strategy: w_cost dominates -> cheaper wins decisively (not a tie)
    code, out, _ = run_router(cfg, [{"id": "r", "user_region": "us"}])
    assert out == [["slow-cheap"]]
    assert code == 0


def test_status_down_filtered():
    cfg = {
        "strategy": "latency",
        "max_replicas": 1,
        "providers": [
            {"id": "down-best", "region": "us", "latency_ms": 5, "cost_per_1k": 0.01,
             "error_rate": 0, "capacity_rps": 10, "status": "down", "health": 1},
            {"id": "up-worse", "region": "us", "latency_ms": 50, "cost_per_1k": 0.01,
             "error_rate": 0, "capacity_rps": 10, "status": "up", "health": 1},
        ],
    }
    code, out, _ = run_router(cfg, [{"id": "r", "user_region": "us"}])
    assert out == [["up-worse"]]


def test_default_tenant_unlimited_budget():
    # A budget exists for "acme" but the request has no tenant -> uses "default",
    # which is unlimited, so an otherwise-unaffordable provider still routes.
    cfg = {
        "strategy": "cost",
        "max_replicas": 1,
        "tenant_budgets": {"acme": 0.0000001},
        "providers": [
            {"id": "p", "region": "us", "latency_ms": 10, "cost_per_1k": 0.05,
             "error_rate": 0, "capacity_rps": 10, "status": "up", "health": 1},
        ],
    }
    code, out, _ = run_router(cfg, [{"id": "r", "user_region": "us"}])
    assert out == [["p"]]
    assert code == 0


def test_payload_kb_ignored():
    cfg = {
        "strategy": "latency",
        "max_replicas": 1,
        "providers": [
            {"id": "p", "region": "us", "latency_ms": 10, "cost_per_1k": 0.01,
             "error_rate": 0, "capacity_rps": 10, "status": "up", "health": 1},
        ],
    }
    a = run_router(cfg, [{"id": "r", "user_region": "us"}])[1]
    b = run_router(cfg, [{"id": "r", "user_region": "us", "payload_kb": 999}])[1]
    assert a == b == [["p"]]


def test_capacity_spillover_across_providers():
    cfg = {
        "strategy": "latency",
        "max_replicas": 1,
        "providers": [
            {"id": "a", "region": "us", "latency_ms": 10, "cost_per_1k": 0.01,
             "error_rate": 0, "capacity_rps": 1, "status": "up", "health": 1},
            {"id": "b", "region": "us", "latency_ms": 50, "cost_per_1k": 0.01,
             "error_rate": 0, "capacity_rps": 10, "status": "up", "health": 1},
        ],
    }
    req = [
        {"id": "1", "user_region": "us"},
        {"id": "2", "user_region": "us"},
        {"id": "3", "user_region": "us"},
    ]
    code, out, _ = run_router(cfg, req)
    # a is best (lower latency) but only 1 capacity -> spills to b afterwards
    assert out == [["a"], ["b"], ["b"]]
    assert code == 0


def _cfg_with(provider_over=None, top_over=None):
    p = {"id": "p", "region": "us", "latency_ms": 10, "cost_per_1k": 0.01,
         "error_rate": 0, "capacity_rps": 10, "status": "up", "health": 1}
    if provider_over:
        p.update(provider_over)
    cfg = {"strategy": "latency", "max_replicas": 1, "providers": [p]}
    if top_over:
        cfg.update(top_over)
    return cfg


def test_config_validation_exit2_variants():
    req = [{"id": "r", "user_region": "us"}]
    bad_cfgs = [
        {"strategy": "latency", "max_replicas": 1, "providers": [
            {"id": "dup", "region": "us", "latency_ms": 1, "cost_per_1k": 0.01, "error_rate": 0, "capacity_rps": 1, "status": "up", "health": 1},
            {"id": "dup", "region": "us", "latency_ms": 1, "cost_per_1k": 0.01, "error_rate": 0, "capacity_rps": 1, "status": "up", "health": 1},
        ]},                                                   # duplicate id
        _cfg_with({"status": "maybe"}),                       # unrecognized status
        _cfg_with({"error_rate": 1.5}),                       # error_rate > 1
        _cfg_with({"capacity_rps": -1}),                      # negative capacity
        _cfg_with({"health": 0}),                             # health not in (0,1]
        _cfg_with({"health": 1.5}),                           # health > 1
        _cfg_with(top_over={"max_replicas": 0}),             # max_replicas < 1
    ]
    for cfg in bad_cfgs:
        code, _, _ = run_router(cfg, req)
        assert code == 2, f"expected exit 2 for {cfg}"


def test_invalid_requests_line_exit2():
    # A malformed JSON line in the requests file must produce exit 2 (no output).
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
            f.write("this is not json\n")
        proc = subprocess.run(
            ["router", "--config", cp, "--requests", rp], capture_output=True, text=True
        )
        assert proc.returncode == 2
        assert proc.stdout.strip() == ""


def test_error_rate_weight_decisive():
    # Providers identical except error_rate; w_err (10000) makes the lower-error
    # provider win decisively (no tie), exercising the error_rate scoring term.
    cfg = {
        "strategy": "latency",
        "max_replicas": 1,
        "providers": [
            {"id": "err-high", "region": "us", "latency_ms": 10, "cost_per_1k": 0.01,
             "error_rate": 0.1, "capacity_rps": 10, "status": "up", "health": 1},
            {"id": "err-low", "region": "us", "latency_ms": 10, "cost_per_1k": 0.01,
             "error_rate": 0.001, "capacity_rps": 10, "status": "up", "health": 1},
        ],
    }
    code, out, _ = run_router(cfg, [{"id": "r", "user_region": "us"}])
    assert out == [["err-low"]]
    assert code == 0


def test_multiple_tenants_independent_budgets():
    # Each tenant has its own budget; one exhausting its budget must not affect
    # the other. Per-request cost = 0.01/1000 = 0.00001; each budget affords one.
    cfg = {
        "strategy": "cost",
        "max_replicas": 1,
        "tenant_budgets": {"A": 0.00001, "B": 0.00001},
        "providers": [
            {"id": "p", "region": "us", "latency_ms": 10, "cost_per_1k": 0.01,
             "error_rate": 0, "capacity_rps": 100, "status": "up", "health": 1},
        ],
    }
    req = [
        {"id": "1", "user_region": "us", "tenant": "A"},
        {"id": "2", "user_region": "us", "tenant": "B"},
        {"id": "3", "user_region": "us", "tenant": "A"},
        {"id": "4", "user_region": "us", "tenant": "B"},
    ]
    code, out, _ = run_router(cfg, req)
    assert out == [["p"], ["p"], [], []]
    assert code == 1
