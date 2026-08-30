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
