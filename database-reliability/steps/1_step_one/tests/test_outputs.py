"""
Step1 verifier - reactive monitoring
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
    assert os.path.isfile(os.path.join(APP_DIR, "go.mod")), "go.mod missing"
    assert os.path.isdir(os.path.join(APP_DIR, "reliability")), (
        "reliability dir missing"
    )
    assert os.path.isfile(os.path.join(APP_DIR, "reliability", "monitor.go")), (
        "monitor.go missing"
    )


def test_go_mod_module_name():
    with open(os.path.join(APP_DIR, "go.mod")) as f:
        content = f.read()
    assert "module db-reliability" in content, "module must be db-reliability"


def test_go_mod_no_external():
    with open(os.path.join(APP_DIR, "go.mod")) as f:
        content = f.read()
    for line in content.splitlines():
        line = line.strip()
        m = re.match(r"^(require\s+)?([^\s]+)\s+v[0-9]", line)
        if m:
            dep = m.group(2).split("/")[0]
            if "." in dep:
                assert False, f"external dependency {m.group(2)} not allowed"


def test_go_build_and_vet():
    p = run(["go", "vet", "./..."])
    assert p.returncode == 0, f"go vet failed: {p.stdout} {p.stderr}"
    p = run(["go", "build", "./..."])
    assert p.returncode == 0, f"go build failed: {p.stdout} {p.stderr}"


def test_monitor_creation_defaults():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "db-reliability/reliability"
    )
    func main(){
        m := reliability.NewMonitor(reliability.Config{})
        if m==nil { panic("nil monitor") }
        _, ok := m.GetNodeStatus("nonexistent")
        if ok { panic("nonexistent should return false") }
        healthy := m.IsHealthy("nonexistent")
        if healthy { panic("nonexistent IsHealthy should be false") }
        uptime := m.GetUptimePercentage("nonexistent")
        if uptime != 100.0 { panic(fmt.Sprintf("nonexistent uptime should be 100 got %f", uptime)) }
        alerts := m.GetAlerts()
        if len(alerts)!=0 { panic("new monitor should have 0 alerts") }
        nodes := m.GetAllNodes()
        if len(nodes)!=0 { panic("new monitor should have 0 nodes") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"defaults test failed: {proc.stdout} {proc.stderr}"


def test_record_check_basic():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "db-reliability/reliability"
    )
    func main(){
        cfg := reliability.Config{
            LatencyThresholdMs: 100,
            ErrorRateThreshold: 0.5,
            ReplicationLagThresholdMs: 500,
            ConnectionThreshold: 100,
            DownThreshold: 3,
            WindowSize: 10,
        }
        m := reliability.NewMonitor(cfg)
        alerts := m.RecordCheck(reliability.CheckResult{
            NodeID: "db1",
            LatencyMs: 50,
            Success: true,
            ReplicationLagMs: 100,
            Connections: 10,
        })
        if len(alerts)!=0 { panic(fmt.Sprintf("expected 0 alerts got %d %v", len(alerts), alerts)) }
        status, ok := m.GetNodeStatus("db1")
        if !ok { panic("db1 should exist") }
        if !status.IsHealthy { panic("should be healthy") }
        if status.TotalChecks!=1 { panic(fmt.Sprintf("total 1 got %d", status.TotalChecks)) }
        if status.FailedChecks!=0 { panic("failed should 0") }
        if status.ConsecutiveFailures!=0 { panic("consecutive 0") }
        if m.GetUptimePercentage("db1") != 100.0 { panic("uptime 100") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"basic test failed: {proc.stdout} {proc.stderr}"


def test_consecutive_failures_down():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "db-reliability/reliability"
    )
    func main(){
        cfg := reliability.Config{
            LatencyThresholdMs: 100,
            ErrorRateThreshold: 0.9,
            ReplicationLagThresholdMs: 500,
            ConnectionThreshold: 100,
            DownThreshold: 3,
            WindowSize: 10,
        }
        m := reliability.NewMonitor(cfg)
        for i:=0;i<2;i++{
            m.RecordCheck(reliability.CheckResult{NodeID:"db1", Success:false, LatencyMs:10})
        }
        alerts := m.RecordCheck(reliability.CheckResult{NodeID:"db1", Success:false, LatencyMs:10})
        found := false
        for _, a := range alerts {
            if a.Type==reliability.AlertNodeDown { found=true; if a.Severity!=reliability.SeverityCritical { panic("node_down should be critical") } }
        }
        if !found { panic(fmt.Sprintf("expected node_down alert, got %v", alerts)) }
        if m.IsHealthy("db1") { panic("should be unhealthy after 3 failures") }
        status,_ := m.GetNodeStatus("db1")
        if status.ConsecutiveFailures!=3 { panic(fmt.Sprintf("consecutive should 3 got %d", status.ConsecutiveFailures)) }
        alerts2 := m.RecordCheck(reliability.CheckResult{NodeID:"db1", Success:false, LatencyMs:10})
        for _, a := range alerts2 {
            if a.Type==reliability.AlertNodeDown { panic("should not emit node_down again after already down") }
        }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"down test failed: {proc.stdout} {proc.stderr}"


def test_recovery_after_failure():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "db-reliability/reliability"
    )
    func main(){
        m := reliability.NewMonitor(reliability.Config{DownThreshold:3, WindowSize:10, ErrorRateThreshold:0.9})
        for i:=0;i<3;i++{ m.RecordCheck(reliability.CheckResult{NodeID:"db1", Success:false}) }
        if m.IsHealthy("db1") { panic("should be down") }
        m.RecordCheck(reliability.CheckResult{NodeID:"db1", Success:true, LatencyMs:10})
        if !m.IsHealthy("db1") { panic("should recover after success") }
        status,_ := m.GetNodeStatus("db1")
        if status.ConsecutiveFailures!=0 { panic("consecutive should 0 after success") }
        if status.TotalChecks!=4 { panic(fmt.Sprintf("total 4 got %d", status.TotalChecks)) }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"recovery failed: {proc.stdout} {proc.stderr}"


def test_high_latency_alert():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "db-reliability/reliability"
    )
    func main(){
        cfg := reliability.Config{LatencyThresholdMs:100, ErrorRateThreshold:0.9, ReplicationLagThresholdMs:500, ConnectionThreshold:100, DownThreshold:5, WindowSize:10}
        m := reliability.NewMonitor(cfg)
        alerts := m.RecordCheck(reliability.CheckResult{NodeID:"db1", Success:true, LatencyMs:150})
        if len(alerts)!=1 { panic(fmt.Sprintf("expected 1 alert got %d", len(alerts))) }
        if alerts[0].Type!=reliability.AlertHighLatency { panic("type high_latency") }
        if alerts[0].Severity!=reliability.SeverityWarning { panic("severity warning") }
        if alerts[0].Value!=150 { panic(fmt.Sprintf("value 150 got %f", alerts[0].Value)) }
        if alerts[0].ID=="" { panic("ID empty") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"latency alert failed: {proc.stdout} {proc.stderr}"


def test_replication_lag_alert():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "db-reliability/reliability"
    )
    func main(){
        m := reliability.NewMonitor(reliability.Config{ReplicationLagThresholdMs:500, ErrorRateThreshold:0.9, DownThreshold:5, WindowSize:10})
        alerts := m.RecordCheck(reliability.CheckResult{NodeID:"db1", Success:true, ReplicationLagMs:600})
        if len(alerts)!=1 || alerts[0].Type!=reliability.AlertReplicationLag { panic(fmt.Sprintf("expected replication_lag got %v", alerts)) }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"replication lag failed: {proc.stdout} {proc.stderr}"


def test_connection_exhaustion_alert():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "db-reliability/reliability"
    )
    func main(){
        m := reliability.NewMonitor(reliability.Config{ConnectionThreshold:100, ErrorRateThreshold:0.9, DownThreshold:5, WindowSize:10})
        alerts := m.RecordCheck(reliability.CheckResult{NodeID:"db1", Success:true, Connections:150})
        if len(alerts)!=1 || alerts[0].Type!=reliability.AlertConnectionExhaustion { panic(fmt.Sprintf("expected connection_exhaustion got %v", alerts)) }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"connection alert failed: {proc.stdout} {proc.stderr}"


def test_error_rate_alert():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "db-reliability/reliability"
    )
    func main(){
        cfg := reliability.Config{ErrorRateThreshold:0.5, DownThreshold:10, WindowSize:10}
        m := reliability.NewMonitor(cfg)
        for i:=0;i<5;i++{ m.RecordCheck(reliability.CheckResult{NodeID:"db1", Success:true, LatencyMs:10}) }
        for i:=0;i<6;i++{ m.RecordCheck(reliability.CheckResult{NodeID:"db1", Success:false, LatencyMs:10}) }
        alerts := m.RecordCheck(reliability.CheckResult{NodeID:"db1", Success:false, LatencyMs:10})
        found:=false
        for _, a := range alerts { if a.Type==reliability.AlertHighErrorRate { found=true } }
        if !found { panic(fmt.Sprintf("expected high_error_rate alert, got %v", alerts)) }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"error rate alert failed: {proc.stdout} {proc.stderr}"


def test_multiple_alerts_same_check():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "db-reliability/reliability"
    )
    func main(){
        m := reliability.NewMonitor(reliability.Config{
            LatencyThresholdMs:100,
            ReplicationLagThresholdMs:500,
            ConnectionThreshold:100,
            ErrorRateThreshold:0.9,
            DownThreshold:5,
            WindowSize:10,
        })
        alerts := m.RecordCheck(reliability.CheckResult{
            NodeID:"db1",
            Success:true,
            LatencyMs:200,
            ReplicationLagMs:600,
            Connections:150,
        })
        if len(alerts)!=3 { panic(fmt.Sprintf("expected 3 alerts got %d %v", len(alerts), alerts)) }
        if alerts[0].Type!=reliability.AlertHighLatency { panic("first should be high_latency") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"multiple alerts failed: {proc.stdout} {proc.stderr}"


def test_get_alerts_and_by_node():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "db-reliability/reliability"
    )
    func main(){
        m := reliability.NewMonitor(reliability.Config{LatencyThresholdMs:10, ErrorRateThreshold:0.9, DownThreshold:10, WindowSize:10})
        m.RecordCheck(reliability.CheckResult{NodeID:"db1", LatencyMs:20, Success:true})
        m.RecordCheck(reliability.CheckResult{NodeID:"db2", LatencyMs:20, Success:true})
        all := m.GetAlerts()
        if len(all)!=2 { panic(fmt.Sprintf("expected 2 alerts got %d", len(all))) }
        byNode1 := m.GetAlertsByNode("db1")
        if len(byNode1)!=1 { panic("expected 1 for db1") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"get alerts failed: {proc.stdout} {proc.stderr}"


def test_uptime_percentage():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "db-reliability/reliability"
    )
    func main(){
        m := reliability.NewMonitor(reliability.Config{DownThreshold:10, WindowSize:10, ErrorRateThreshold:0.9})
        for i:=0;i<3;i++{ m.RecordCheck(reliability.CheckResult{NodeID:"db1", Success:true}) }
        m.RecordCheck(reliability.CheckResult{NodeID:"db1", Success:false})
        uptime := m.GetUptimePercentage("db1")
        if uptime < 74.9 || uptime > 75.1 { panic(fmt.Sprintf("expected 75 got %f", uptime)) }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"uptime failed: {proc.stdout} {proc.stderr}"


def test_reset():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "db-reliability/reliability"
    )
    func main(){
        m := reliability.NewMonitor(reliability.Config{DownThreshold:2, WindowSize:10, ErrorRateThreshold:0.9})
        m.RecordCheck(reliability.CheckResult{NodeID:"db1", Success:false})
        m.RecordCheck(reliability.CheckResult{NodeID:"db1", Success:false})
        m.Reset("db1")
        _, ok := m.GetNodeStatus("db1")
        if ok { panic("after reset should not exist") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"reset failed: {proc.stdout} {proc.stderr}"


def test_concurrency():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "sync"
        "db-reliability/reliability"
    )
    func main(){
        m := reliability.NewMonitor(reliability.Config{WindowSize:10, DownThreshold:100, ErrorRateThreshold:0.9})
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
        nodes := m.GetAllNodes()
        if len(nodes)!=5 { panic(fmt.Sprintf("expected 5 nodes got %d", len(nodes))) }
        fmt.Println("OK")
    }
    """)
    proc = go_run_race_program(code, timeout=60)
    assert proc.returncode == 0, f"concurrency failed: {proc.stdout} {proc.stderr}"


def test_error_rate_window_length_min():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "db-reliability/reliability"
    )
    func main(){
        m:=reliability.NewMonitor(reliability.Config{ErrorRateThreshold:0.1, DownThreshold:10, WindowSize:10})
        m.RecordCheck(reliability.CheckResult{NodeID:"db1", Success:true})
        alerts:=m.RecordCheck(reliability.CheckResult{NodeID:"db1", Success:false})
        for _, a := range alerts { if a.Type==reliability.AlertHighErrorRate { panic("should not alert when window <3") } }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"window min failed: {proc.stdout} {proc.stderr}"


def test_alert_id_uniqueness():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "db-reliability/reliability"
    )
    func main(){
        m:=reliability.NewMonitor(reliability.Config{LatencyThresholdMs:10, ErrorRateThreshold:0.9, DownThreshold:10, WindowSize:10})
        seen:=map[string]bool{}
        for i:=0;i<20;i++{
            alerts:=m.RecordCheck(reliability.CheckResult{NodeID:"db1", Success:true, LatencyMs:20})
            for _, a := range alerts {
                if seen[a.ID] { panic("duplicate ID") }
                seen[a.ID]=true
            }
        }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"id uniqueness failed: {proc.stdout} {proc.stderr}"


def test_get_all_nodes():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "db-reliability/reliability"
    )
    func main(){
        m:=reliability.NewMonitor(reliability.Config{ErrorRateThreshold:0.9, DownThreshold:10, WindowSize:10})
        m.RecordCheck(reliability.CheckResult{NodeID:"db1", Success:true})
        m.RecordCheck(reliability.CheckResult{NodeID:"db2", Success:true})
        nodes:=m.GetAllNodes()
        if len(nodes)!=2 { panic(fmt.Sprintf("expected 2 got %d", len(nodes))) }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"get all nodes failed: {proc.stdout} {proc.stderr}"


def test_empty_nodeid_noop():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "db-reliability/reliability"
    )
    func main(){
        m:=reliability.NewMonitor(reliability.Config{})
        alerts:=m.RecordCheck(reliability.CheckResult{NodeID:"", Success:false, LatencyMs:1000})
        if len(alerts)!=0 { panic("empty nodeID should 0 alerts") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"empty nodeid failed: {proc.stdout} {proc.stderr}"


def test_negative_values_handling():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "db-reliability/reliability"
    )
    func main(){
        m:=reliability.NewMonitor(reliability.Config{LatencyThresholdMs:100, ReplicationLagThresholdMs:500, ConnectionThreshold:100, DownThreshold:10, ErrorRateThreshold:0.9, WindowSize:10})
        alerts:=m.RecordCheck(reliability.CheckResult{NodeID:"db1", Success:true, LatencyMs:-10, ReplicationLagMs:-5, Connections:-1})
        if len(alerts)!=0 { panic(fmt.Sprintf("negative should not trigger got %v", alerts)) }
        status,_:=m.GetNodeStatus("db1")
        if status.AvgLatencyMs!=0 { panic("negative latency 0") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"negative handling failed: {proc.stdout} {proc.stderr}"
    )


def test_timestamp_zero_handling():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "time"
        "db-reliability/reliability"
    )
    func main(){
        m:=reliability.NewMonitor(reliability.Config{LatencyThresholdMs:10, DownThreshold:10, WindowSize:10, ErrorRateThreshold:0.9})
        alerts:=m.RecordCheck(reliability.CheckResult{NodeID:"db1", Success:true, LatencyMs:20})
        if alerts[0].Timestamp.IsZero() { panic("timestamp zero") }
        ts:=time.Date(2023,1,2,3,4,5,0,time.UTC)
        alerts2:=m.RecordCheck(reliability.CheckResult{NodeID:"db1", Timestamp:ts, Success:true, LatencyMs:20})
        if !alerts2[0].Timestamp.Equal(ts) { panic(fmt.Sprintf("should use provided ts got %v expected %v", alerts2[0].Timestamp, ts)) }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"timestamp handling failed: {proc.stdout} {proc.stderr}"
    )


def test_custom_config():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "db-reliability/reliability"
    )
    func main(){
        cfg:=reliability.Config{LatencyThresholdMs:200, DownThreshold:2, WindowSize:5, ErrorRateThreshold:0.8}
        m:=reliability.NewMonitor(cfg)
        alerts:=m.RecordCheck(reliability.CheckResult{NodeID:"db1", Success:true, LatencyMs:150})
        if len(alerts)!=0 { panic("150 <200 no alert") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"custom config failed: {proc.stdout} {proc.stderr}"


def test_defaults_zero_config():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "db-reliability/reliability"
    )
    func main(){
        m:=reliability.NewMonitor(reliability.Config{})
        alerts:=m.RecordCheck(reliability.CheckResult{NodeID:"db1", Success:true, LatencyMs:150})
        if len(alerts)==0 { panic("default 100 should trigger 150") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"defaults zero config failed: {proc.stdout} {proc.stderr}"
    )


def test_avg_latency_window():
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
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"avg latency window failed: {proc.stdout} {proc.stderr}"
    )


def test_alert_ordering():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "db-reliability/reliability"
    )
    func main(){
        m:=reliability.NewMonitor(reliability.Config{
            LatencyThresholdMs:100,
            ReplicationLagThresholdMs:500,
            ConnectionThreshold:100,
            DownThreshold:1,
            WindowSize:10,
            ErrorRateThreshold:0.9,
        })
        alerts:=m.RecordCheck(reliability.CheckResult{
            NodeID:"db1",
            Success:false,
            LatencyMs:200,
            ReplicationLagMs:600,
            Connections:150,
        })
        if alerts[0].Type!=reliability.AlertNodeDown { panic(fmt.Sprintf("first should node_down got %s", alerts[0].Type)) }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"ordering failed: {proc.stdout} {proc.stderr}"
