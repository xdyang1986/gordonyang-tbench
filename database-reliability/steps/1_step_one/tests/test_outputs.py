"""
Step1 hardened verifier - hysteresis + time-window errorRate + differential
"""

import os, subprocess, tempfile, textwrap, shutil, re, random, time
import pytest

APP_DIR = "/app"


def run(cmd, cwd=APP_DIR, timeout=30):
    env = os.environ.copy()
    env["GOCACHE"] = "/tmp/codimango/gocache"
    env["GOPATH"] = "/tmp/codimango/gopath"
    env["GOFLAGS"] = "-mod=mod"
    env["GOTOOLCHAIN"] = "local"
    return subprocess.run(
        cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout, env=env
    )


def go_run_program(go_code: str, timeout=20):
    tmp = tempfile.mkdtemp(prefix="dbrel_test_")
    try:
        mod = textwrap.dedent(f"""
        module testharness
        go 1.22
        require db-reliability v0.0.0
        replace db-reliability => {APP_DIR}
        """)
        open(os.path.join(tmp, "go.mod"), "w").write(mod)
        open(os.path.join(tmp, "main.go"), "w").write(go_code)
        proc = run(["go", "run", "."], cwd=tmp, timeout=timeout)
        return proc
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def go_run_race_program(go_code: str, timeout=60):
    tmp = tempfile.mkdtemp(prefix="dbrel_test_")
    try:
        mod = textwrap.dedent(f"""
        module testharness
        go 1.22
        require db-reliability v0.0.0
        replace db-reliability => {APP_DIR}
        """)
        open(os.path.join(tmp, "go.mod"), "w").write(mod)
        open(os.path.join(tmp, "main.go"), "w").write(go_code)
        proc = run(["go", "run", "-race", "."], cwd=tmp, timeout=timeout)
        return proc
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_files_exist():
    assert os.path.isfile(os.path.join(APP_DIR, "go.mod"))
    assert os.path.isdir(os.path.join(APP_DIR, "reliability"))
    assert os.path.isfile(os.path.join(APP_DIR, "reliability", "monitor.go"))


def test_go_mod_module_name():
    with open(os.path.join(APP_DIR, "go.mod")) as f:
        assert "module db-reliability" in f.read()


def test_go_build_and_vet():
    p = run(["go", "vet", "./..."])
    assert p.returncode == 0, f"vet {p.stdout} {p.stderr}"
    p = run(["go", "build", "./..."])
    assert p.returncode == 0, f"build {p.stdout} {p.stderr}"


def test_basic():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "db-reliability/reliability"
    )
    func main(){
        m:=reliability.NewMonitor(reliability.Config{LatencyThresholdMs:100, ErrorRateThreshold:0.9, DownThreshold:10, WindowSize:10})
        alerts:=m.RecordCheck(reliability.CheckResult{NodeID:"db1", Success:true, LatencyMs:50})
        if len(alerts)!=0 { panic("0 alerts") }
        status,_:=m.GetNodeStatus("db1")
        if status.TotalChecks!=1 { panic("total 1") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"{proc.stdout} {proc.stderr}"


def test_down_transition():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "db-reliability/reliability"
    )
    func main(){
        m:=reliability.NewMonitor(reliability.Config{DownThreshold:3, WindowSize:10, ErrorRateThreshold:0.9})
        for i:=0;i<2;i++{ m.RecordCheck(reliability.CheckResult{NodeID:"db1", Success:false}) }
        if !m.IsHealthy("db1") { panic("still healthy after 2") }
        alerts:=m.RecordCheck(reliability.CheckResult{NodeID:"db1", Success:false})
        found:=false
        for _,a:=range alerts{ if a.Type==reliability.AlertNodeDown {found=true} }
        if !found { panic("should down on 3rd") }
        alerts2:=m.RecordCheck(reliability.CheckResult{NodeID:"db1", Success:false})
        for _,a:=range alerts2{ if a.Type==reliability.AlertNodeDown{panic("no repeat")} }
        m.RecordCheck(reliability.CheckResult{NodeID:"db1", Success:true})
        if !m.IsHealthy("db1"){panic("should recover")}
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"{proc.stdout} {proc.stderr}"


def test_high_latency_basic():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "db-reliability/reliability"
    )
    func main(){
        m:=reliability.NewMonitor(reliability.Config{LatencyThresholdMs:100, ErrorRateThreshold:0.9, DownThreshold:10, WindowSize:10})
        alerts:=m.RecordCheck(reliability.CheckResult{NodeID:"db1", Success:true, LatencyMs:150})
        if len(alerts)!=1 || alerts[0].Type!=reliability.AlertHighLatency { panic("high_latency") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"{proc.stdout} {proc.stderr}"


# ---- hysteresis tests (prior-violating) ----


def test_hysteresis_suppress():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "db-reliability/reliability"
    )
    func main(){
        m:=reliability.NewMonitor(reliability.Config{LatencyThresholdMs:100, ErrorRateThreshold:0.9, DownThreshold:10, WindowSize:10})
        a1:=m.RecordCheck(reliability.CheckResult{NodeID:"db1", Success:true, LatencyMs:120})
        if len(a1)!=1 || a1[0].Type!=reliability.AlertHighLatency { panic(fmt.Sprintf("first should alert got %v", a1)) }
        a2:=m.RecordCheck(reliability.CheckResult{NodeID:"db1", Success:true, LatencyMs:110})
        if len(a2)!=0 { panic(fmt.Sprintf("second should be suppressed got %v", a2)) }
        a3:=m.RecordCheck(reliability.CheckResult{NodeID:"db1", Success:true, LatencyMs:90})
        if len(a3)!=0 { panic(fmt.Sprintf("90 should still be suppressed (needs <80) got %v", a3)) }
        a4:=m.RecordCheck(reliability.CheckResult{NodeID:"db1", Success:true, LatencyMs:70})
        if len(a4)!=0 { panic(fmt.Sprintf("70 should not alert but reset, got %v", a4)) }
        a5:=m.RecordCheck(reliability.CheckResult{NodeID:"db1", Success:true, LatencyMs:110})
        if len(a5)!=1 || a5[0].Type!=reliability.AlertHighLatency { panic(fmt.Sprintf("after reset should alert again got %v", a5)) }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"hysteresis suppress failed: {proc.stdout} {proc.stderr}"
    )


def test_hysteresis_reset_clears():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "db-reliability/reliability"
    )
    func main(){
        m:=reliability.NewMonitor(reliability.Config{LatencyThresholdMs:100, ErrorRateThreshold:0.9, DownThreshold:10, WindowSize:10})
        m.RecordCheck(reliability.CheckResult{NodeID:"db1", Success:true, LatencyMs:120})
        a2:=m.RecordCheck(reliability.CheckResult{NodeID:"db1", Success:true, LatencyMs:110})
        if len(a2)!=0 { panic("suppressed") }
        m.Reset("db1")
        a3:=m.RecordCheck(reliability.CheckResult{NodeID:"db1", Success:true, LatencyMs:110})
        if len(a3)!=1 { panic(fmt.Sprintf("after reset should alert, got %v", a3)) }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"hysteresis reset failed: {proc.stdout} {proc.stderr}"


def test_hysteresis_per_node():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "db-reliability/reliability"
    )
    func main(){
        m:=reliability.NewMonitor(reliability.Config{LatencyThresholdMs:100, ErrorRateThreshold:0.9, DownThreshold:10, WindowSize:10})
        m.RecordCheck(reliability.CheckResult{NodeID:"db1", Success:true, LatencyMs:120})
        m.RecordCheck(reliability.CheckResult{NodeID:"db1", Success:true, LatencyMs:110}) // suppressed
        a:=m.RecordCheck(reliability.CheckResult{NodeID:"db2", Success:true, LatencyMs:120})
        if len(a)!=1 { panic("db2 should alert independently") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"{proc.stdout} {proc.stderr}"


# ---- time window errorRate (mixed window) ----


def test_error_rate_time_window_exclusion():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "time"
        "db-reliability/reliability"
    )
    func main(){
        m:=reliability.NewMonitor(reliability.Config{ErrorRateThreshold:0.4, DownThreshold:10, WindowSize:10})
        base:=time.Date(2023,1,1,0,0,0,0,time.UTC)
        m.RecordCheck(reliability.CheckResult{NodeID:"db1", Timestamp:base.Add(-70*time.Second), Success:false})
        m.RecordCheck(reliability.CheckResult{NodeID:"db1", Timestamp:base.Add(-50*time.Second), Success:false})
        m.RecordCheck(reliability.CheckResult{NodeID:"db1", Timestamp:base.Add(-30*time.Second), Success:false})
        m.RecordCheck(reliability.CheckResult{NodeID:"db1", Timestamp:base.Add(-10*time.Second), Success:true})
        alerts:=m.RecordCheck(reliability.CheckResult{NodeID:"db1", Timestamp:base, Success:true, LatencyMs:10})
        found:=false
        for _,a:=range alerts{ if a.Type==reliability.AlertHighErrorRate{found=true} }
        if !found { panic(fmt.Sprintf("should alert error rate time window, got %v", alerts)) }
        m2:=reliability.NewMonitor(reliability.Config{ErrorRateThreshold:0.1, DownThreshold:10, WindowSize:10})
        m2.RecordCheck(reliability.CheckResult{NodeID:"db1", Timestamp:base.Add(-70*time.Second), Success:false})
        m2.RecordCheck(reliability.CheckResult{NodeID:"db1", Timestamp:base.Add(-65*time.Second), Success:false})
        alerts2:=m2.RecordCheck(reliability.CheckResult{NodeID:"db1", Timestamp:base, Success:true})
        for _,a:=range alerts2{ if a.Type==reliability.AlertHighErrorRate{panic(fmt.Sprintf("should NOT alert, outside window excluded, got %v", alerts2))} }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"time window exclusion failed: {proc.stdout} {proc.stderr}"
    )


def test_error_rate_time_window_min3():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "time"
        "db-reliability/reliability"
    )
    func main(){
        m:=reliability.NewMonitor(reliability.Config{ErrorRateThreshold:0.1, DownThreshold:10, WindowSize:10})
        base:=time.Date(2023,1,1,0,0,0,0,time.UTC)
        m.RecordCheck(reliability.CheckResult{NodeID:"db1", Timestamp:base.Add(-10*time.Second), Success:true})
        alerts:=m.RecordCheck(reliability.CheckResult{NodeID:"db1", Timestamp:base, Success:false})
        // window total 2 (<3) should NOT alert
        for _,a:=range alerts{ if a.Type==reliability.AlertHighErrorRate{panic("should not alert when window len <3")} }
        m.RecordCheck(reliability.CheckResult{NodeID:"db1", Timestamp:base.Add(5*time.Second), Success:false})
        alerts2:=m.RecordCheck(reliability.CheckResult{NodeID:"db1", Timestamp:base.Add(10*time.Second), Success:false})
        found:=false
        for _,a:=range alerts2{ if a.Type==reliability.AlertHighErrorRate{found=true} }
        if !found { panic("should alert after 3+ in window") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"{proc.stdout} {proc.stderr}"


def test_avg_latency_count_window():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "db-reliability/reliability"
    )
    func main(){
        m:=reliability.NewMonitor(reliability.Config{WindowSize:3, ErrorRateThreshold:0.9, DownThreshold:10})
        m.RecordCheck(reliability.CheckResult{NodeID:"db1", Success:true, LatencyMs:10})
        m.RecordCheck(reliability.CheckResult{NodeID:"db1", Success:true, LatencyMs:20})
        m.RecordCheck(reliability.CheckResult{NodeID:"db1", Success:true, LatencyMs:30})
        status,_:=m.GetNodeStatus("db1")
        if status.AvgLatencyMs <19.9 || status.AvgLatencyMs>20.1 { panic(fmt.Sprintf("avg 20 got %f", status.AvgLatencyMs)) }
        m.RecordCheck(reliability.CheckResult{NodeID:"db1", Success:true, LatencyMs:40})
        status2,_:=m.GetNodeStatus("db1")
        if status2.AvgLatencyMs <29.9 || status2.AvgLatencyMs>30.1 { panic(fmt.Sprintf("avg 30 got %f", status2.AvgLatencyMs)) }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"{proc.stdout} {proc.stderr}"


def test_mixed_window_semantics():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "time"
        "db-reliability/reliability"
    )
    func main(){
        // errorRate uses time window 60s, avgLatency uses count window WindowSize
        // With timestamps spaced >60s apart, errorRate window should only see current
        // but avgLatency should see last WindowSize regardless of time
        m:=reliability.NewMonitor(reliability.Config{WindowSize:3, ErrorRateThreshold:0.5, DownThreshold:10})
        base:=time.Date(2023,1,1,0,0,0,0,time.UTC)
        // 3 checks spaced 70s apart: each outside 60s of next, but count window keeps last 3
        m.RecordCheck(reliability.CheckResult{NodeID:"db1", Timestamp:base, Success:false, LatencyMs:10})
        m.RecordCheck(reliability.CheckResult{NodeID:"db1", Timestamp:base.Add(70*time.Second), Success:false, LatencyMs:20})
        m.RecordCheck(reliability.CheckResult{NodeID:"db1", Timestamp:base.Add(140*time.Second), Success:false, LatencyMs:30})
        // At ts=140s, time window last 60s only includes current (30) if previous were 70s apart, so errorRate = 1/1 =1 but window len 1 (<3) => no alert
        // However count window avg should be (10+20+30)/3=20 regardless of time gaps
        status,_:=m.GetNodeStatus("db1")
        if status.AvgLatencyMs <19.9 || status.AvgLatencyMs>20.1 { panic(fmt.Sprintf("mixed: avg should be count-based 20, got %f", status.AvgLatencyMs)) }
        // Now add 2 more fails within 60s to make time window len 3 with high error
        m.RecordCheck(reliability.CheckResult{NodeID:"db1", Timestamp:base.Add(145*time.Second), Success:false, LatencyMs:40})
        alerts:=m.RecordCheck(reliability.CheckResult{NodeID:"db1", Timestamp:base.Add(150*time.Second), Success:false, LatencyMs:10})
        found:=false
        for _,a:=range alerts{ if a.Type==reliability.AlertHighErrorRate{found=true} }
        if !found { panic(fmt.Sprintf("should alert with time window 3 fails inside 60s, got %v", alerts)) }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"mixed window failed: {proc.stdout} {proc.stderr}"


def test_multiple_alerts_order():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "db-reliability/reliability"
    )
    func main(){
        m:=reliability.NewMonitor(reliability.Config{LatencyThresholdMs:100, ReplicationLagThresholdMs:500, ConnectionThreshold:100, DownThreshold:1, WindowSize:10, ErrorRateThreshold:0.9})
        alerts:=m.RecordCheck(reliability.CheckResult{NodeID:"db1", Success:false, LatencyMs:200, ReplicationLagMs:600, Connections:150})
        if len(alerts)<4 { panic(fmt.Sprintf("expected >=4 got %d %v", len(alerts), alerts)) }
        if alerts[0].Type!=reliability.AlertNodeDown { panic("first node_down") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"{proc.stdout} {proc.stderr}"


def test_uptime_and_reset():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "db-reliability/reliability"
    )
    func main(){
        m:=reliability.NewMonitor(reliability.Config{DownThreshold:10, WindowSize:10, ErrorRateThreshold:0.9})
        for i:=0;i<3;i++{ m.RecordCheck(reliability.CheckResult{NodeID:"db1", Success:true}) }
        m.RecordCheck(reliability.CheckResult{NodeID:"db1", Success:false})
        up:=m.GetUptimePercentage("db1")
        if up<74.9 || up>75.1 { panic(fmt.Sprintf("uptime 75 got %f", up)) }
        m.Reset("db1")
        _, ok:=m.GetNodeStatus("db1")
        if ok { panic("after reset not exist") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"{proc.stdout} {proc.stderr}"


def test_concurrency():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "sync"
        "db-reliability/reliability"
    )
    func main(){
        m:=reliability.NewMonitor(reliability.Config{WindowSize:10, DownThreshold:100, ErrorRateThreshold:0.9})
        var wg sync.WaitGroup
        n:=100
        wg.Add(n)
        for i:=0;i<n;i++{
            go func(idx int){
                defer wg.Done()
                for j:=0;j<100;j++{
                    m.RecordCheck(reliability.CheckResult{NodeID:fmt.Sprintf("db-%d", idx%5), Success:true, LatencyMs:10})
                }
            }(i)
        }
        wg.Wait()
        if len(m.GetAllNodes())!=5 { panic("5 nodes") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_race_program(code, timeout=60)
    assert proc.returncode == 0, f"concurrency {proc.stdout} {proc.stderr}"


# ---- randomized differential test ----


def test_randomized_differential():
    # Python oracle that mirrors Go monitor with hysteresis + time window
    # We generate 5k checks across 20 nodes with random timestamps, latencies, success
    # Then we run same sequence through Go monitor and compare alerts (types per check, errorRate logic, hysteresis)
    # For simplicity we only check high_latency hysteresis and high_error_rate time window and node_down
    # The Go program will output per-check alerts as lines; Python oracle computes expected
    # Compare counts and types match for random seed

    random.seed(42)
    # generate checks
    checks = []
    base_time = 0  # seconds from base
    # We'll use explicit timestamps to make deterministic: start at 0, increment by random 5-20 sec
    nodes = [f"db-{i}" for i in range(20)]
    ts = 0
    for _ in range(5000):
        node = random.choice(nodes)
        # time increment 1-10 sec to keep many within 60s window but some outside
        ts += random.randint(1, 10)
        latency = random.choice(
            [random.randint(10, 90), random.randint(110, 200)]
        )  # sometimes high
        success = random.random() > 0.2  # 80% success
        repl_lag = random.randint(100, 700)
        conns = random.randint(50, 150)
        checks.append((node, ts, latency, success, repl_lag, conns))

    # Python oracle
    class NodeState:
        def __init__(self):
            self.consec = 0
            self.total = 0
            self.failed = 0
            self.history = []  # list of (ts, latency, success)
            self.suppressed = False
            self.alerts = []

    node_states = {}
    expected_alerts_per_check = []  # list of list of AlertType strings in order

    def compute_error_rate(history, cur_ts):
        # history includes current already? We'll include current before compute, like Go does
        # time window: ts >= cur_ts-60
        window = [h for h in history if h[0] >= cur_ts - 60]
        if len(window) == 0:
            return 0.0, 0
        failed = sum(1 for h in window if not h[2])
        return failed / len(window), len(window)

    for node, ts_cur, latency, success, repl_lag, conns in checks:
        st = node_states.get(node)
        if st is None:
            st = NodeState()
            node_states[node] = st
        # normalize latency etc like Go (negative->0) but our generated are positive
        # update counters
        st.total += 1
        if not success:
            st.failed += 1
            st.consec += 1
        else:
            st.consec = 0
        st.history.append((ts_cur, latency, success, repl_lag, conns))
        # keep maxHist 20 or window*2 (default window 10 => 20)
        max_hist = 20
        if len(st.history) > max_hist:
            st.history = st.history[-max_hist:]

        err_rate, win_len = compute_error_rate(st.history, ts_cur)
        alerts = []
        # node_down
        if st.consec == 3:  # default DownThreshold 3
            alerts.append("node_down")
        # high_latency with hysteresis
        if latency > 100:
            if not st.suppressed:
                alerts.append("high_latency")
                st.suppressed = True
        else:
            if latency < 80:
                st.suppressed = False
        # replication_lag
        if repl_lag > 500:
            alerts.append("replication_lag")
        # connection_exhaustion
        if conns > 100:
            alerts.append("connection_exhaustion")
        # high_error_rate time window
        if win_len >= 3 and err_rate > 0.05:
            alerts.append("high_error_rate")
        expected_alerts_per_check.append(alerts)

    # Now generate Go program that replays same checks and outputs alerts per check
    # We'll create Go code that uses same logic as expected to be implemented
    # For comparison we will use actual monitor's output vs expected oracle

    # Build Go code string that runs checks
    # We need to create a Go file that hardcodes checks from Python generation? That would be huge (5000). Instead we generate a file with checks data inline via Python generating Go code? We'll create a temporary Go program that reads checks from a JSON file we create.

    # Create JSON file with checks in /tmp
    import json, pathlib

    json_path = "/tmp/diff_checks.json"
    with open(json_path, "w") as jf:
        json.dump(
            [
                {"node": n, "ts": ts, "lat": lat, "succ": succ, "lag": lag, "conns": c}
                for n, ts, lat, succ, lag, c in checks
            ],
            jf,
        )

    go_code = textwrap.dedent(f"""
    package main

    import (
        "encoding/json"
        "fmt"
        "os"
        "time"
        "db-reliability/reliability"
    )

    type Input struct {{
        Node string `json:"node"`
        Ts int64 `json:"ts"`
        Lat float64 `json:"lat"`
        Succ bool `json:"succ"`
        Lag float64 `json:"lag"`
        Conns int `json:"conns"`
    }}

    func main(){{
        data, err:=os.ReadFile("/tmp/diff_checks.json")
        if err!=nil{{ panic(err) }}
        var inputs []Input
        if err:=json.Unmarshal(data, &inputs); err!=nil{{ panic(err) }}
        cfg:=reliability.Config{{LatencyThresholdMs:100, ErrorRateThreshold:0.05, ReplicationLagThresholdMs:500, ConnectionThreshold:100, DownThreshold:3, WindowSize:10}}
        m:=reliability.NewMonitor(cfg)
        base:=time.Date(2023,1,1,0,0,0,0,time.UTC)
        for idx, inpt:=range inputs {{
            ts:=base.Add(time.Duration(inpt.Ts)*time.Second)
            res:=reliability.CheckResult{{NodeID:inpt.Node, Timestamp:ts, LatencyMs:inpt.Lat, Success:inpt.Succ, ReplicationLagMs:inpt.Lag, Connections:inpt.Conns}}
            alerts:=m.RecordCheck(res)
            // encode alerts as comma-separated types in order
            types:=""
            for i,a:=range alerts {{
                if i>0 {{ types+="," }}
                types+=string(a.Type)
            }}
            fmt.Printf("%d:%s\\n", idx, types)
        }}
    }}
    """)
    proc = go_run_program(go_code, timeout=30)
    assert proc.returncode == 0, (
        f"Go differential runner failed: {proc.stdout} {proc.stderr}"
    )
    # Parse Go output
    go_lines = proc.stdout.strip().split("\n")
    assert len(go_lines) == len(expected_alerts_per_check), (
        f"lines mismatch {len(go_lines)} vs {len(expected_alerts_per_check)}"
    )
    for idx, line in enumerate(go_lines):
        parts = line.split(":", 1)
        assert len(parts) == 2
        go_idx = int(parts[0])
        go_types_str = parts[1]
        go_types = [t for t in go_types_str.split(",") if t] if go_types_str else []
        exp = expected_alerts_per_check[go_idx]
        if go_types != exp:
            # For debugging, find first mismatch
            if idx < 10 or go_types != exp:
                print(f"Mismatch at {idx}: expected {exp} got {go_types}")
            # Allow some flexibility? Strict comparison required for harness
            assert go_types == exp, (
                f"Diff at check {go_idx}: expected {exp} got {go_types}"
            )
    # If we reach here, differential passed


def test_negative_and_empty():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "db-reliability/reliability"
    )
    func main(){
        m:=reliability.NewMonitor(reliability.Config{})
        alerts:=m.RecordCheck(reliability.CheckResult{NodeID:"", Success:false})
        if len(alerts)!=0 { panic("empty no-op") }
        alerts2:=m.RecordCheck(reliability.CheckResult{NodeID:"db1", Success:true, LatencyMs:-10})
        if len(alerts2)!=0 { panic("negative latency no alert") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"{proc.stdout} {proc.stderr}"
