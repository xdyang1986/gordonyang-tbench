"""
Step2 verifier - proactive scoring + reactive regression
"""

import os, subprocess, tempfile, textwrap, shutil, re
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


def go_run_program(go_code, timeout=20):
    tmp = tempfile.mkdtemp(prefix="dbrel2_test_")
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


def go_run_race_program(go_code, timeout=60):
    tmp = tempfile.mkdtemp(prefix="dbrel2_test_")
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


def test_go_build_and_vet():
    p = run(["go", "vet", "./..."])
    assert p.returncode == 0, f"go vet failed: {p.stdout} {p.stderr}"
    p = run(["go", "build", "./..."])
    assert p.returncode == 0, f"go build failed: {p.stdout} {p.stderr}"


def test_step1_regression_basic():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "db-reliability/reliability"
    )
    func main(){
        m:=reliability.NewMonitor(reliability.Config{LatencyThresholdMs:100, DownThreshold:3, WindowSize:10, ErrorRateThreshold:0.9})
        alerts:=m.RecordCheck(reliability.CheckResult{NodeID:"db1", Success:true, LatencyMs:150})
        if len(alerts)!=1 || alerts[0].Type!=reliability.AlertHighLatency { panic("step1 regression latency") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"regression failed: {proc.stdout} {proc.stderr}"


def test_health_score_healthy():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "db-reliability/reliability"
    )
    func main(){
        m:=reliability.NewMonitor(reliability.Config{})
        for i:=0;i<5;i++{
            m.RecordCheck(reliability.CheckResult{NodeID:"db1", Success:true, LatencyMs:20, ReplicationLagMs:100, Connections:10})
        }
        score, ok := m.GetHealthScore("db1")
        if !ok { panic("score not found") }
        if score.Score < 90 { panic(fmt.Sprintf("healthy should >=90 got %f", score.Score)) }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"healthy score failed: {proc.stdout} {proc.stderr}"


def test_health_score_degraded():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "db-reliability/reliability"
    )
    func main(){
        m:=reliability.NewMonitor(reliability.Config{LatencyThresholdMs:100, ReplicationLagThresholdMs:500, ConnectionThreshold:100, ErrorRateThreshold:0.05, DownThreshold:10, WindowSize:10})
        for i:=0;i<5;i++{ m.RecordCheck(reliability.CheckResult{NodeID:"db1", Success:true, LatencyMs:200, ReplicationLagMs:600, Connections:150}) }
        for i:=0;i<5;i++{ m.RecordCheck(reliability.CheckResult{NodeID:"db1", Success:false, LatencyMs:200, ReplicationLagMs:600, Connections:150}) }
        score,_ := m.GetHealthScore("db1")
        if score.Score >= 90 { panic(fmt.Sprintf("degraded should <90 got %f", score.Score)) }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"degraded failed: {proc.stdout} {proc.stderr}"


def test_trend_degrading():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "db-reliability/reliability"
    )
    func main(){
        m:=reliability.NewMonitor(reliability.Config{WindowSize:10})
        for _, v := range []float64{10,20,30,40,50,60,70,80} {
            m.RecordCheck(reliability.CheckResult{NodeID:"db1", Success:true, LatencyMs:v})
        }
        trend := m.GetTrend("db1","latency")
        if trend!="degrading" { panic(fmt.Sprintf("expected degrading got %s", trend)) }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"trend degrading failed: {proc.stdout} {proc.stderr}"


def test_predict_failure_low_risk():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "db-reliability/reliability"
    )
    func main(){
        m:=reliability.NewMonitor(reliability.Config{})
        for i:=0;i<5;i++{ m.RecordCheck(reliability.CheckResult{NodeID:"db1", Success:true, LatencyMs:20}) }
        pred, _ := m.PredictFailure("db1")
        if pred.RiskLevel!=reliability.RiskLow { panic(fmt.Sprintf("expected low got %s", pred.RiskLevel)) }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"low risk failed: {proc.stdout} {proc.stderr}"


def test_predict_failure_critical_down():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "db-reliability/reliability"
    )
    func main(){
        m:=reliability.NewMonitor(reliability.Config{DownThreshold:3, WindowSize:10})
        for i:=0;i<3;i++{ m.RecordCheck(reliability.CheckResult{NodeID:"db1", Success:false}) }
        pred, _ := m.PredictFailure("db1")
        if pred.RiskLevel!=reliability.RiskCritical { panic(fmt.Sprintf("down should critical got %s", pred.RiskLevel)) }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"critical down failed: {proc.stdout} {proc.stderr}"


def test_reliability_report():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "db-reliability/reliability"
    )
    func main(){
        m:=reliability.NewMonitor(reliability.Config{LatencyThresholdMs:100})
        m.RecordCheck(reliability.CheckResult{NodeID:"db1", Success:true, LatencyMs:20})
        report := m.GetReliabilityReport()
        if len(report.Nodes)!=1 { panic("nodes 1") }
        if report.OverallScore <0 || report.OverallScore>100 { panic("overall out of range") }
        if len(report.Recommendations)==0 { panic("recommendations empty") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"report failed: {proc.stdout} {proc.stderr}"


def test_concurrency_scoring():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "sync"
        "db-reliability/reliability"
    )
    func main(){
        m:=reliability.NewMonitor(reliability.Config{WindowSize:20, DownThreshold:100, ErrorRateThreshold:0.9})
        var wg sync.WaitGroup
        n:=50
        wg.Add(n)
        for i:=0;i<n;i++{
            go func(idx int){
                defer wg.Done()
                for j:=0;j<100;j++{
                    m.RecordCheck(reliability.CheckResult{NodeID:fmt.Sprintf("db-%d", idx%5), Success:true, LatencyMs:float64(j%100)})
                    m.GetHealthScore(fmt.Sprintf("db-%d", idx%5))
                }
            }(i)
        }
        wg.Wait()
        fmt.Println("OK")
    }
    """)
    proc = go_run_race_program(code, timeout=60)
    assert proc.returncode == 0, (
        f"concurrency scoring failed: {proc.stdout} {proc.stderr}"
    )


def test_health_score_clamped():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "db-reliability/reliability"
    )
    func main(){
        m:=reliability.NewMonitor(reliability.Config{LatencyThresholdMs:10, ReplicationLagThresholdMs:10, ConnectionThreshold:10, ErrorRateThreshold:0.01, DownThreshold:2, WindowSize:10})
        for i:=0;i<20;i++{ m.RecordCheck(reliability.CheckResult{NodeID:"db1", Success:false, LatencyMs:1000, ReplicationLagMs:1000, Connections:1000}) }
        score,_:=m.GetHealthScore("db1")
        if score.Score<0 || score.Score>100 { panic(fmt.Sprintf("out of 0-100 got %f", score.Score)) }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"clamped failed: {proc.stdout} {proc.stderr}"


def test_trend_stable_insufficient():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "db-reliability/reliability"
    )
    func main(){
        m:=reliability.NewMonitor(reliability.Config{WindowSize:10})
        for i:=0;i<2;i++{ m.RecordCheck(reliability.CheckResult{NodeID:"db1", Success:true, LatencyMs:10}) }
        if m.GetTrend("db1","latency")!="stable" { panic("should stable") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"stable trend failed: {proc.stdout} {proc.stderr}"


def test_trend_improving():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "db-reliability/reliability"
    )
    func main(){
        m:=reliability.NewMonitor(reliability.Config{WindowSize:10})
        for _, v := range []float64{80,70,60,50,40,30,20,10} {
            m.RecordCheck(reliability.CheckResult{NodeID:"db1", Success:true, LatencyMs:v})
        }
        if m.GetTrend("db1","latency")!="improving" { panic("should improving") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"improving failed: {proc.stdout} {proc.stderr}"


def test_predict_failure_high_risk():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "db-reliability/reliability"
    )
    func main(){
        m:=reliability.NewMonitor(reliability.Config{LatencyThresholdMs:100, DownThreshold:10, WindowSize:10, ErrorRateThreshold:0.05})
        for i:=0;i<10;i++{ m.RecordCheck(reliability.CheckResult{NodeID:"db1", Success:false, LatencyMs:500}) }
        pred,_:=m.PredictFailure("db1")
        if pred.RiskLevel!=reliability.RiskHigh && pred.RiskLevel!=reliability.RiskCritical { panic(fmt.Sprintf("expected high/critical got %s", pred.RiskLevel)) }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"high risk failed: {proc.stdout} {proc.stderr}"


def test_reliability_report_empty():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "db-reliability/reliability"
    )
    func main(){
        m:=reliability.NewMonitor(reliability.Config{})
        report:=m.GetReliabilityReport()
        if report.OverallScore!=100 { panic(fmt.Sprintf("empty overall 100 got %f", report.OverallScore)) }
        if len(report.Recommendations)==0 { panic("recommendations empty") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"empty report failed: {proc.stdout} {proc.stderr}"


def test_cluster_health_levels():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "db-reliability/reliability"
    )
    func main(){
        m1:=reliability.NewMonitor(reliability.Config{})
        m1.RecordCheck(reliability.CheckResult{NodeID:"db1", Success:true, LatencyMs:10})
        r1:=m1.GetReliabilityReport()
        if r1.ClusterHealth!="healthy" { panic("healthy expected") }
        m2:=reliability.NewMonitor(reliability.Config{LatencyThresholdMs:10, DownThreshold:10, WindowSize:10, ErrorRateThreshold:0.01, ReplicationLagThresholdMs:10, ConnectionThreshold:10})
        for i:=0;i<10;i++{ m2.RecordCheck(reliability.CheckResult{NodeID:"db1", Success:false, LatencyMs:1000, ReplicationLagMs:1000, Connections:1000}) }
        r2:=m2.GetReliabilityReport()
        if r2.OverallScore>=60 { panic(fmt.Sprintf("should <60 got %f", r2.OverallScore)) }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"health levels failed: {proc.stdout} {proc.stderr}"


def test_recommendations_content():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "strings"
        "db-reliability/reliability"
    )
    func main(){
        m:=reliability.NewMonitor(reliability.Config{LatencyThresholdMs:100, ReplicationLagThresholdMs:500, ConnectionThreshold:100, DownThreshold:10, WindowSize:10, ErrorRateThreshold:0.9})
        m.RecordCheck(reliability.CheckResult{NodeID:"db1", Success:true, LatencyMs:200, ReplicationLagMs:600, Connections:150})
        report:=m.GetReliabilityReport()
        recStr:=strings.Join(report.Recommendations, " ")
        recLower:=strings.ToLower(recStr)
        if !strings.Contains(recLower, "scaling") && !strings.Contains(recLower, "latency") { panic("should mention latency") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"recommendations content failed: {proc.stdout} {proc.stderr}"
    )


def test_scoring_factors_sum():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "math"
        "db-reliability/reliability"
    )
    func main(){
        m:=reliability.NewMonitor(reliability.Config{LatencyThresholdMs:100, ReplicationLagThresholdMs:500, ConnectionThreshold:100, ErrorRateThreshold:0.05, DownThreshold:10, WindowSize:5})
        m.RecordCheck(reliability.CheckResult{NodeID:"db1", Success:true, LatencyMs:200, ReplicationLagMs:600, Connections:150})
        score,_:=m.GetHealthScore("db1")
        sum:=0.0
        for _, v := range score.Factors { sum+=v }
        expected:=100-sum
        if math.Abs(expected-score.Score)>0.001 { panic(fmt.Sprintf("score 100-sum expected %f got %f", expected, score.Score)) }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"factors sum failed: {proc.stdout} {proc.stderr}"


def test_scoring_examples_from_instruction():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "time"
        "db-reliability/reliability"
    )
    func main(){
        cfg:=reliability.Config{LatencyThresholdMs:100, ReplicationLagThresholdMs:500, ConnectionThreshold:100, DownThreshold:10, WindowSize:10, ErrorRateThreshold:0.05}
        m:=reliability.NewMonitor(cfg)
        base:=time.Date(2023,1,1,0,0,0,0,time.UTC)
        // Example: errorRate 0.2 -> Score 90 (1 fail out of 5 within 60s, last success so consec 0)
        m.RecordCheck(reliability.CheckResult{NodeID:"ex1", Timestamp:base.Add(-40*time.Second), Success:false, LatencyMs:50, ReplicationLagMs:100, Connections:10})
        m.RecordCheck(reliability.CheckResult{NodeID:"ex1", Timestamp:base.Add(-30*time.Second), Success:true, LatencyMs:50, ReplicationLagMs:100, Connections:10})
        m.RecordCheck(reliability.CheckResult{NodeID:"ex1", Timestamp:base.Add(-20*time.Second), Success:true, LatencyMs:50, ReplicationLagMs:100, Connections:10})
        m.RecordCheck(reliability.CheckResult{NodeID:"ex1", Timestamp:base.Add(-10*time.Second), Success:true, LatencyMs:50, ReplicationLagMs:100, Connections:10})
        m.RecordCheck(reliability.CheckResult{NodeID:"ex1", Timestamp:base, Success:true, LatencyMs:50, ReplicationLagMs:100, Connections:10})
        score,_:=m.GetHealthScore("ex1")
        if score.Score <89 || score.Score >91 { panic(fmt.Sprintf("ex errorRate 0.2 expected 90 got %f", score.Score)) }
        // latency 120 -> Score 96
        m2:=reliability.NewMonitor(cfg)
        m2.RecordCheck(reliability.CheckResult{NodeID:"ex2", Timestamp:base, Success:true, LatencyMs:120, ReplicationLagMs:100, Connections:10})
        s2,_:=m2.GetHealthScore("ex2")
        if s2.Score <95 || s2.Score >97 { panic(fmt.Sprintf("lat 120 expected 96 got %f", s2.Score)) }
        // latency 150 -> 90
        m3:=reliability.NewMonitor(cfg)
        m3.RecordCheck(reliability.CheckResult{NodeID:"ex3", Timestamp:base, Success:true, LatencyMs:150, ReplicationLagMs:100, Connections:10})
        s3,_:=m3.GetHealthScore("ex3")
        if s3.Score <89 || s3.Score >91 { panic(fmt.Sprintf("lat 150 expected 90 got %f", s3.Score)) }
        // latency 250 -> 70
        m4:=reliability.NewMonitor(cfg)
        m4.RecordCheck(reliability.CheckResult{NodeID:"ex4", Timestamp:base, Success:true, LatencyMs:250, ReplicationLagMs:100, Connections:10})
        s4,_:=m4.GetHealthScore("ex4")
        if s4.Score <69 || s4.Score >71 { panic(fmt.Sprintf("lat 250 expected 70 got %f", s4.Score)) }
        // combined example 68
        m5:=reliability.NewMonitor(cfg)
        // need errorRate 0.2 with 5 checks (we already have 1 fail in 5) plus latency 120 etc - we need to craft window with avgLatency 120, avgLag 600, consec 1, conn 90
        // Use 5 checks: 4 success latency 120 lag 600 conn 10, then 1 fail latency 120 lag 600 conn 90 with consec 1
        for i:=0;i<4;i++{
            m5.RecordCheck(reliability.CheckResult{NodeID:"ex5", Timestamp:base.Add(time.Duration(-40+i*10)*time.Second), Success:true, LatencyMs:120, ReplicationLagMs:600, Connections:10})
        }
        m5.RecordCheck(reliability.CheckResult{NodeID:"ex5", Timestamp:base, Success:false, LatencyMs:120, ReplicationLagMs:600, Connections:90})
        s5,_:=m5.GetHealthScore("ex5")
        if s5.Score <66 || s5.Score >70 { panic(fmt.Sprintf("combined expected ~68 got %f factors %v", s5.Score, s5.Factors)) }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"scoring examples failed: {proc.stdout} {proc.stderr}"


def test_trend_outage_exclusion():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "time"
        "db-reliability/reliability"
    )
    func main(){
        cfg:=reliability.Config{DownThreshold:3, WindowSize:20}
        m:=reliability.NewMonitor(cfg)
        base:=time.Date(2023,1,1,0,0,0,0,time.UTC)
        // Create pattern: first 4 checks low latency 10
        for i:=0;i<4;i++{
            m.RecordCheck(reliability.CheckResult{NodeID:"db1", Timestamp:base.Add(time.Duration(i)*time.Second), Success:true, LatencyMs:10})
        }
        // 3 fails to go down
        for i:=0;i<3;i++{
            m.RecordCheck(reliability.CheckResult{NodeID:"db1", Timestamp:base.Add(time.Duration(10+i)*time.Second), Success:false, LatencyMs:1000})
        }
        // Now 4 checks high latency 100 while node is down? Actually after 3 fails, node is down, but we continue recording checks with success false? The spec says trend must skip checks recorded while node was down.
        // After down, let's have 4 high latency checks with success true? No, success true would recover. So we need to have checks with success true but latency high while still down? Actually down state resets on success, so after 3 fails node is down. Next success would make it healthy again. So to have down period, we need consecutive fails.
        // For this test, we want trend to skip the down period: we will have 4 low latency, then 3 fails (down), then 4 high latency after recovery with success
        // If outage not excluded, trend would be degrading (10 vs 100). If outage excluded, trend would compute from low vs high but skipping down fails (which had 1000 latency) – still degrading? Need better scenario: low latency 10, down with latency 1000 (skew), then high latency 20 after recovery – if not excluded, avg newer includes 1000 skew → degrading, if excluded, still stable/improving? Let's craft simpler: 4 low 10, down with 1000s, then 4 low 12 (slightly higher). Without exclusion, newer avg includes 1000 → huge degrading; with exclusion, newer avg 12 vs older 10 → stable (since 12 < 10*1.1=11? Actually 12>11 so degrading, hmm). Let's use 11 for stable.
        // We'll test that trend ignores down: have 4 low 10, then 3 fails latency 1000 (down), then 4 low 11 – without exclusion avg newer would be (1000+1000+1000+11)/? Actually our split n/2 after filtering? Hard to craft.
        // Simpler: we just check that GetTrend does not panic and returns stable/improving/degrading and that it skipped down – we will just ensure it returns stable when we have 4 low, 3 down fails (1000), 4 low same as before – with exclusion should be stable, without exclusion would be degrading due to 1000s.
        m2:=reliability.NewMonitor(cfg)
        for i:=0;i<4;i++{ m2.RecordCheck(reliability.CheckResult{NodeID:"db1", Timestamp:base.Add(time.Duration(i)*time.Second), Success:true, LatencyMs:10}) }
        for i:=0;i<3;i++{ m2.RecordCheck(reliability.CheckResult{NodeID:"db1", Timestamp:base.Add(time.Duration(10+i)*time.Second), Success:false, LatencyMs:1000}) }
        for i:=0;i<4;i++{ m2.RecordCheck(reliability.CheckResult{NodeID:"db1", Timestamp:base.Add(time.Duration(20+i)*time.Second), Success:true, LatencyMs:10}) }
        trend:=m2.GetTrend("db1","latency")
        // With outage exclusion, history filtered removes the 3 down checks (1000), so remaining is 8 checks of 10 latency → stable
        if trend!="stable" { panic(fmt.Sprintf("trend with outage exclusion should be stable (down skipped), got %s", trend)) }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"outage exclusion failed: {proc.stdout} {proc.stderr}"


def test_randomized_differential_scoring():
    # Scaled differential: 3k checks across 10 nodes, verify scores in range and factors invariant
    import random, json

    random.seed(123)
    nodes = [f"db-{i}" for i in range(10)]
    checks = []
    ts = 0
    for _ in range(3000):
        node = random.choice(nodes)
        ts += random.randint(1, 10)
        latency = random.randint(10, 300)
        success = random.random() > 0.15
        lag = random.randint(50, 800)
        conns = random.randint(10, 150)
        checks.append((node, ts, latency, success, lag, conns))
    json_path = "/tmp/diff_score.json"
    with open(json_path, "w") as jf:
        json.dump(
            [
                {"node": n, "ts": ts, "lat": lat, "succ": s, "lag": lag, "conns": c}
                for n, ts, lat, s, lag, c in checks
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
    type In struct {{
        Node string `json:"node"`
        Ts int64 `json:"ts"`
        Lat float64 `json:"lat"`
        Succ bool `json:"succ"`
        Lag float64 `json:"lag"`
        Conns int `json:"conns"`
    }}
    func main(){{
        data,_:=os.ReadFile("/tmp/diff_score.json")
        var ins []In
        json.Unmarshal(data, &ins)
        cfg:=reliability.Config{{LatencyThresholdMs:100, ReplicationLagThresholdMs:500, ConnectionThreshold:100, DownThreshold:3, WindowSize:10, ErrorRateThreshold:0.05}}
        m:=reliability.NewMonitor(cfg)
        base:=time.Date(2023,1,1,0,0,0,0,time.UTC)
        for _,inpt:=range ins {{
            ts:=base.Add(time.Duration(inpt.Ts)*time.Second)
            m.RecordCheck(reliability.CheckResult{{NodeID:inpt.Node, Timestamp:ts, LatencyMs:inpt.Lat, Success:inpt.Succ, ReplicationLagMs:inpt.Lag, Connections:inpt.Conns}})
        }}
        scores:=m.GetAllHealthScores()
        report:=m.GetReliabilityReport()
        if len(scores)!=10 {{ panic(fmt.Sprintf("expected 10 scores got %d", len(scores))) }}
        for _,s:=range scores{{
            if s.Score<0 || s.Score>100 {{ panic(fmt.Sprintf("score out of range %f", s.Score)) }}
            sum:=0.0
            for _,v:=range s.Factors {{ sum+=v }}
            // check factors sum invariant within tolerance
            // score = 100 - sum (clamped)
            expected:=100-sum
            if expected<0 {{ expected=0 }}
            if expected>100 {{ expected=100 }}
            diff:=expected-s.Score
            if diff<0 {{ diff=-diff }}
            if diff>0.01 {{ panic(fmt.Sprintf("factors sum invariant failed for %s: score %f vs 100-sum %f", s.NodeID, s.Score, expected)) }}
            fmt.Printf("%s:%.2f:%s\\n", s.NodeID, s.Score, s.Trend)
        }}
        if report.OverallScore<0 || report.OverallScore>100 {{ panic("overall out of range") }}
        if len(report.Recommendations)==0 {{ panic("no recommendations") }}
        fmt.Println("OK")
    }}
    """)
    proc = go_run_program(go_code, timeout=30)
    assert proc.returncode == 0, f"diff scoring run failed: {proc.stdout} {proc.stderr}"
    assert "OK" in proc.stdout
