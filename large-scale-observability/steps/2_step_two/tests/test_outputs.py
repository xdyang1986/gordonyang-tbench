"""
Step2 verifier — RatioSampler last-8-hex + error override, ParentAware AND, Batch evict-oldest + block-and-drain
- RatioSampler uses last-8-hex + error/critical override
- ParentAware requires parent AND root both Keep
- Batch evicts oldest on full + ForceFlush block-and-drain
- Propagation single-header x-ride-trace
"""

import os, subprocess, tempfile, re, textwrap, shutil, time
import pytest

APP_DIR = "/app"


def run(cmd, cwd=APP_DIR, timeout=30):
    env = os.environ.copy()
    # Use shared cache for normal runs (fast), isolated cache for -race runs to avoid
    # 'cannot reopen' corruption under heavy load with race detector
    if cwd != APP_DIR and "-race" in cmd:
        env["GOCACHE"] = os.path.join(cwd, "gocache")
    else:
        env["GOCACHE"] = "/tmp/codimango/gocache"
    env["GOPATH"] = "/tmp/codimango/gopath"
    env["GOFLAGS"] = "-mod=mod"
    env["GOTOOLCHAIN"] = "local"
    return subprocess.run(
        cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout, env=env
    )


def go_run_program(go_code: str, timeout=30):
    tmp = tempfile.mkdtemp(prefix="obs_test2_")
    try:
        mod = textwrap.dedent(f"""
        module testharness
        go 1.22
        require ride-observability v0.0.0
        replace ride-observability => {APP_DIR}
        """)
        open(os.path.join(tmp, "go.mod"), "w").write(mod)
        open(os.path.join(tmp, "main.go"), "w").write(go_code)
        proc = run(["go", "run", "."], cwd=tmp, timeout=timeout)
        return proc
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def go_run_with_race(go_code: str, timeout=30):
    tmp = tempfile.mkdtemp(prefix="obs_test2_")
    try:
        mod = textwrap.dedent(f"""
        module testharness
        go 1.22
        require ride-observability v0.0.0
        replace ride-observability => {APP_DIR}
        """)
        open(os.path.join(tmp, "go.mod"), "w").write(mod)
        open(os.path.join(tmp, "main.go"), "w").write(go_code)
        proc = run(["go", "run", "-race", "."], cwd=tmp, timeout=timeout)
        return proc
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_files_exist_step2():
    assert os.path.isdir(os.path.join(APP_DIR, "observability"))
    for fname in ["tracing.go", "metrics.go", "logger.go"]:
        assert os.path.isfile(os.path.join(APP_DIR, "observability", fname))


def test_go_build_vet_step2():
    p = run(["go", "vet", "./..."])
    assert p.returncode == 0, f"go vet failed: {p.stdout} {p.stderr}"
    p = run(["go", "build", "./..."])
    assert p.returncode == 0, f"go build failed: {p.stdout} {p.stderr}"


def test_sampler_always():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        on := observability.NewAlwaysSampler()
        off := observability.NewNeverSampler()
        if on.Description()=="" { panic("on desc empty") }
        if off.Description()=="" { panic("off desc empty") }
        p := observability.SamplingRequest{TraceID:"0102030405060708090a0b0c0d0e0f10", SpanName:"test"}
        if on.ShouldSample(p)!=observability.DecisionKeep { panic("Always should keep") }
        if off.ShouldSample(p)!=observability.DecisionDrop { panic("Never should drop") }
        // aliases
        on2 := observability.NewAlwaysOnSampler()
        off2 := observability.NewAlwaysOffSampler()
        if on2.ShouldSample(p)!=observability.DecisionKeep { panic("AlwaysOn alias should keep") }
        if off2.ShouldSample(p)!=observability.DecisionDrop { panic("AlwaysOff alias should drop") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"always sampler failed: {proc.stdout} {proc.stderr}"


def test_sampler_ratio_boundaries():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        zero := observability.NewRatioSampler(0.0)
        one := observability.NewRatioSampler(1.0)
        p := observability.SamplingRequest{TraceID:"0102030405060708090a0b0c0d0e0f10", SpanName:"s"}
        if zero.ShouldSample(p)!=observability.DecisionDrop { panic("ratio 0 drop") }
        if one.ShouldSample(p)!=observability.DecisionKeep { panic("ratio 1 keep") }
        neg := observability.NewRatioSampler(-0.1)
        over := observability.NewRatioSampler(1.5)
        if neg.ShouldSample(p)!=observability.DecisionDrop { panic("neg drop") }
        if over.ShouldSample(p)!=observability.DecisionKeep { panic(">1 keep") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"ratio boundaries failed: {proc.stdout} {proc.stderr}"


def test_sampler_ratio_statistical():
    code = textwrap.dedent("""
    package main
    import (
        "crypto/rand"
        "encoding/hex"
        "fmt"
        "ride-observability/observability"
    )
    func randTraceID() string {
        b := make([]byte, 16)
        rand.Read(b)
        return hex.EncodeToString(b)
    }
    func main(){
        sampler := observability.NewRatioSampler(0.1)
        n:=10000
        sampled:=0
        for i:=0;i<n;i++{
            tid := randTraceID()
            p := observability.SamplingRequest{TraceID:tid, SpanName:"test"}
            if sampler.ShouldSample(p)==observability.DecisionKeep { sampled++ }
        }
        ratio := float64(sampled)/float64(n)
        fmt.Printf("sampled %d/%d ratio %f\\n", sampled, n, ratio)
        if ratio < 0.05 || ratio > 0.15 {
            panic(fmt.Sprintf("ratio 0.1 expected ~0.1, got %f", ratio))
        }
        tid := "0102030405060708090a0b0c0d0e0f10"
        p1 := observability.SamplingRequest{TraceID:tid, SpanName:"a"}
        d1 := sampler.ShouldSample(p1)
        d2 := sampler.ShouldSample(p1)
        if d1!=d2 { panic("not deterministic") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"ratio statistical failed: {proc.stdout} {proc.stderr}"
    )


def test_sampler_ratio_half():
    code = textwrap.dedent("""
    package main
    import (
        "crypto/rand"
        "encoding/hex"
        "fmt"
        "ride-observability/observability"
    )
    func randTraceID() string {
        b := make([]byte, 16)
        rand.Read(b)
        return hex.EncodeToString(b)
    }
    func main(){
        sampler := observability.NewRatioSampler(0.5)
        n:=10000
        sampled:=0
        for i:=0;i<n;i++{
            tid := randTraceID()
            p := observability.SamplingRequest{TraceID:tid, SpanName:"t"}
            if sampler.ShouldSample(p)==observability.DecisionKeep { sampled++ }
        }
        ratio := float64(sampled)/float64(n)
        if ratio < 0.4 || ratio > 0.6 { panic(fmt.Sprintf("expected ~0.5 got %f", ratio)) }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"ratio half failed: {proc.stdout} {proc.stderr}"


def test_sampler_determinism_prefix():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        sampler := observability.NewRatioSampler(0.5)
        tid := "01020304050607080102030405060708"
        p := observability.SamplingRequest{TraceID:tid, SpanName:"test"}
        d1 := sampler.ShouldSample(p)
        d2 := sampler.ShouldSample(p)
        d3 := sampler.ShouldSample(p)
        if d1!=d2 || d1!=d3 {
            panic(fmt.Sprintf("sampler should be deterministic on same TraceID, got %d %d %d", d1, d2, d3))
        }
        tid2 := "aabbccddeeff0011aabbccddeeff0011"
        p2 := observability.SamplingRequest{TraceID:tid2, SpanName:"test"}
        d4 := sampler.ShouldSample(p2)
        d5 := sampler.ShouldSample(p2)
        if d4!=d5 {
            panic("determinism failed for second id")
        }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"determinism failed: {proc.stdout} {proc.stderr}"


def test_sampler_invalid_traceid():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        sampler := observability.NewRatioSampler(0.5)
        invalids := []string{"", "short", "zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz", "0102030405060708090a0b0c0d0e0f0g", "010203040506070", "g102030405060708090a0b0c0d0e0f10"}
        for _, tid := range invalids {
            p := observability.SamplingRequest{TraceID:tid, SpanName:"test"}
            func(){
                defer func(){
                    if r:=recover(); r!=nil { panic(fmt.Sprintf("panic on invalid traceID %q: %v", tid, r)) }
                }()
                d := sampler.ShouldSample(p)
                if d!=observability.DecisionDrop {
                    panic(fmt.Sprintf("invalid traceID %q must return Drop per spec, got %d", tid, d))
                }
            }()
        }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"invalid traceid failed: {proc.stdout} {proc.stderr}"


def test_sampler_ratio_last8hex_vs_first16():
    # Determinism and algorithm test: uses last 8 hex as uint32
    # TraceID where last 8 is max (should Drop for 0.5)
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        sampler := observability.NewRatioSampler(0.5)
        // TraceID: first 16 chars 0000000000000001 tiny, last 8 ffffffff max
        // last 8 chars "ffffffff" = 4294967295 (max, ratio ~0.999, our spec should Drop for 0.5)
        tid := "0000000000000001ffffffffffffffff"
        // full length 32, last 8 = ffffffff
        p := observability.SamplingRequest{TraceID:tid, SpanName:"test"}
        d := sampler.ShouldSample(p)
        fmt.Printf("TraceID %s decision %d\\n", tid, d)
        // Our spec: last 8 hex ffffffff => 4294967295 / 2^32 ~0.999 >0.5 => Drop
        if d != observability.DecisionDrop {
            panic(fmt.Sprintf("Expected Drop for tid %s with last8 ffffffff at 0.5, got %d", tid, d))
        }
        // opposite: last 8 tiny => Keep for 0.5
        tid2 := "ffffffffffffffff0000000000000000"
        p2 := observability.SamplingRequest{TraceID:tid2, SpanName:"test"}
        d2 := sampler.ShouldSample(p2)
        fmt.Printf("TraceID %s decision %d\\n", tid2, d2)
        // last 8 = 00000000 => 0/2^32 =0 <0.5 => Keep
        if d2 != observability.DecisionKeep {
            panic(fmt.Sprintf("Expected Keep for tid %s with last8 00000000 at 0.5, got %d", tid2, d2))
        }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"ratio last8 vs first16 failed: {proc.stdout} {proc.stderr}"
    )


def test_sampler_ratio_error_override():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        sampler := observability.NewRatioSampler(0.0) // 0.0 would normally Drop all
        // Error status must override ratio 0.0 to Keep
        pErr := observability.SamplingRequest{TraceID:"0102030405060708090a0b0c0d0e0f10", SpanName:"test", Status:observability.StatusError}
        d := sampler.ShouldSample(pErr)
        if d != observability.DecisionKeep {
            panic(fmt.Sprintf("Error status should override ratio 0.0 to Keep, got %d", d))
        }
        pCrit := observability.SamplingRequest{TraceID:"0102030405060708090a0b0c0d0e0f10", SpanName:"test", Priority:"critical"}
        d2 := sampler.ShouldSample(pCrit)
        if d2 != observability.DecisionKeep {
            panic(fmt.Sprintf("Critical priority should override ratio 0.0 to Keep, got %d", d2))
        }
        pNormal := observability.SamplingRequest{TraceID:"0102030405060708090a0b0c0d0e0f10", SpanName:"test", Status:observability.StatusOK, Priority:"normal"}
        // normal at 0.0 should still Drop
        d3 := sampler.ShouldSample(pNormal)
        if d3 != observability.DecisionDrop {
            panic(fmt.Sprintf("Normal OK at 0.0 should Drop, got %d", d3))
        }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"ratio error override failed: {proc.stdout} {proc.stderr}"
    )


def test_sampler_parent_aware_and_logic():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        rootNever := observability.NewNeverSampler()
        rootAlways := observability.NewAlwaysSampler()
        paNever := observability.NewParentAwareSampler(rootNever)
        paAlways := observability.NewParentAwareSampler(rootAlways)

        // No parent => delegate to root
        pNoParent := observability.SamplingRequest{TraceID:"0102030405060708090a0b0c0d0e0f10", HasParent:false}
        if paAlways.ShouldSample(pNoParent)!=observability.DecisionKeep { panic("paAlways no-parent should keep") }
        if paNever.ShouldSample(pNoParent)!=observability.DecisionDrop { panic("paNever no-parent should drop") }

        // Parent not sampled => always Drop even if root Always
        parentNotSampled := observability.TraceContext{TraceID:"0102030405060708090a0b0c0d0e0f10", SpanID:"0102030405060708", Sampled:false}
        pNotSampled := observability.SamplingRequest{TraceID:parentNotSampled.TraceID, HasParent:true, Parent:parentNotSampled}
        if paAlways.ShouldSample(pNotSampled)!=observability.DecisionDrop { panic("not sampled parent should drop even if root Always") }

        // Parent sampled true + root Never => should Drop per AND logic
        parentSampled := observability.TraceContext{TraceID:"0102030405060708090a0b0c0d0e0f10", SpanID:"0102030405060708", Sampled:true}
        pSampledParentNeverRoot := observability.SamplingRequest{TraceID:parentSampled.TraceID, HasParent:true, Parent:parentSampled}
        // rootNever is 0.0 always drop
        if paNever.ShouldSample(pSampledParentNeverRoot)!=observability.DecisionDrop {
            panic("parent sampled true + root Never should Drop per AND logic")
        }

        // Parent sampled true + root Always => Keep
        pSampledParentAlwaysRoot := observability.SamplingRequest{TraceID:parentSampled.TraceID, HasParent:true, Parent:parentSampled}
        if paAlways.ShouldSample(pSampledParentAlwaysRoot)!=observability.DecisionKeep {
            panic("parent sampled true + root Always should Keep")
        }

        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"parent aware AND logic failed: {proc.stdout} {proc.stderr}"
    )


def test_sampler_parent_nested():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        root := observability.NewRatioSampler(0.0)
        pb := observability.NewParentAwareSampler(root)
        parentSampled := observability.TraceContext{TraceID:"0102030405060708090a0b0c0d0e0f10", SpanID:"0000000000000002", Sampled:true}
        pChild := observability.SamplingRequest{TraceID:parentSampled.TraceID, HasParent:true, Parent:parentSampled}
        // root 0.0 => Drop even though parent sampled true, because AND logic
        if pb.ShouldSample(pChild)!=observability.DecisionDrop { panic("nested sampled with root 0.0 should drop per AND logic") }

        parentNotSampled := observability.TraceContext{TraceID:"0102030405060708090a0b0c0d0e0f10", SpanID:"0000000000000002", Sampled:false}
        pChild2 := observability.SamplingRequest{TraceID:parentNotSampled.TraceID, HasParent:true, Parent:parentNotSampled}
        if pb.ShouldSample(pChild2)!=observability.DecisionDrop { panic("nested not sampled should drop") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"parent nested failed: {proc.stdout} {proc.stderr}"


def test_sampler_parent_description_contains_root():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "strings"
        "ride-observability/observability"
    )
    func main(){
        root := observability.NewRatioSampler(0.5)
        pb := observability.NewParentAwareSampler(root)
        desc := pb.Description()
        rootDesc := root.Description()
        if !strings.Contains(desc, rootDesc) && !strings.Contains(strings.ToLower(desc), "parent") {
            panic(fmt.Sprintf("ParentAware description should mention root or parent, got %s root %s", desc, rootDesc))
        }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"parent desc failed: {proc.stdout} {proc.stderr}"


def test_tracer_with_sampler_integration():
    code = textwrap.dedent("""
    package main
    import (
        "context"
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        exp := observability.NewMemoryExporter()
        proc := observability.NewSimpleProcessor(exp)
        sampler := observability.NewNeverSampler()
        tracer := observability.NewTracer("svc", observability.WithProcessor(proc), observability.WithSampler(sampler))
        _, span := tracer.Start(context.Background(), "should-drop")
        if span.IsRecording() { panic("should not be recording") }
        span.End()
        if len(exp.GetSpans())!=0 { panic(fmt.Sprintf("expected 0 got %d", len(exp.GetSpans()))) }

        exp2 := observability.NewMemoryExporter()
        proc2 := observability.NewSimpleProcessor(exp2)
        tracer2 := observability.NewTracer("svc", observability.WithProcessor(proc2), observability.WithSampler(observability.NewAlwaysSampler()))
        _, s2 := tracer2.Start(context.Background(), "should-sample")
        s2.End()
        if len(exp2.GetSpans())!=1 { panic("expected 1") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"tracer sampler integration failed: {proc.stdout} {proc.stderr}"
    )


def test_sampler_parent_propagation_via_tracer():
    code = textwrap.dedent("""
    package main
    import (
        "context"
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        exp := observability.NewMemoryExporter()
        proc := observability.NewSimpleProcessor(exp)
        root := observability.NewRatioSampler(0.5)
        pb := observability.NewParentAwareSampler(root)
        tracer := observability.NewTracer("svc", observability.WithProcessor(proc), observability.WithSampler(pb))

        parentSC := observability.TraceContext{TraceID:"0102030405060708090a0b0c0d0e0f10", SpanID:"0102030405060708", Sampled:true}
        ctx := observability.ContextWithTrace(context.Background(), parentSC)
        _, child := tracer.Start(ctx, "child")
        // child sampled true + root ratio 0.5: may be Keep or Drop depending on traceID; but using fixed tid that with last8 0d0e0f10 => small => Keep for 0.5
        // So we use a tid that definitely Keeps for 0.5 to test AND logic still works
        // Let's use tid where last8 = 00000000 => Keep
        parentSC2 := observability.TraceContext{TraceID:"01020304050607080000000000000000", SpanID:"0102030405060708", Sampled:true}
        ctx2 := observability.ContextWithTrace(context.Background(), parentSC2)
        _, child2 := tracer.Start(ctx2, "child2")
        // traceID last8 00000000 => ratio sampler 0.5 should Keep, and parent sampled true => Keep
        if !child2.IsRecording() { panic("child of sampled parent with root that Keeps should be recording") }
        child2.End()

        parentSC3 := observability.TraceContext{TraceID:"0202030405060708090a0b0c0d0e0f10", SpanID:"0202030405060708", Sampled:false}
        ctx3 := observability.ContextWithTrace(context.Background(), parentSC3)
        _, child3 := tracer.Start(ctx3, "child3")
        if child3.IsRecording() { panic("child of not-sampled parent should NOT be recording") }
        child3.End()

        fmt.Println("OK")
        _ = child
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"parent propagation via tracer failed: {proc.stdout} {proc.stderr}"
    )


def test_tracing_marshal_unmarshal_preserves_parent_id():
    code = textwrap.dedent("""
    package main
    import (
        "context"
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        sc := observability.TraceContext{TraceID:"0102030405060708090a0b0c0d0e0f10", SpanID:"0102030405060708", ParentID:"0a0b0c0d0e0f0a0b", Sampled:true}
        ctx := observability.ContextWithTrace(context.Background(), sc)
        carrier := map[string]string{}
        observability.MarshalTrace(ctx, carrier)
        v, ok := carrier["x-ride-trace"]
        if !ok { panic("x-ride-trace not present") }
        if len(v)==0 { panic("empty") }
        ctx2 := observability.UnmarshalTrace(carrier)
        sc2, _ := observability.TraceFromContext(ctx2)
        if sc2.ParentID != sc.ParentID { panic(fmt.Sprintf("parent-id mismatch %s vs %s", sc2.ParentID, sc.ParentID)) }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"marshal parent-id failed: {proc.stdout} {proc.stderr}"
    )


def test_batch_processor_basic():
    code = textwrap.dedent("""
    package main
    import (
        "context"
        "fmt"
        "time"
        "ride-observability/observability"
    )
    func main(){
        exp := observability.NewMemoryExporter()
        proc := observability.NewBatchProcessor(exp, observability.WithBatchSize(5), observability.WithQueueSize(100), observability.WithBatchTimeout(100*time.Millisecond))
        tracer := observability.NewTracer("svc", observability.WithProcessor(proc))
        for i:=0;i<10;i++{
            _, span := tracer.Start(context.Background(), fmt.Sprintf("span-%d", i))
            span.End()
        }
        proc.ForceFlush(context.Background())
        spans := exp.GetSpans()
        if len(spans)!=10 { panic(fmt.Sprintf("expected 10 got %d", len(spans))) }
        proc.Shutdown(context.Background())
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"batch basic failed: {proc.stdout} {proc.stderr}"


def test_batch_timeout_trigger():
    code = textwrap.dedent("""
    package main
    import (
        "context"
        "fmt"
        "time"
        "ride-observability/observability"
    )
    func main(){
        exp := observability.NewMemoryExporter()
        proc := observability.NewBatchProcessor(exp, observability.WithBatchSize(100), observability.WithQueueSize(1000), observability.WithBatchTimeout(100*time.Millisecond))
        tracer := observability.NewTracer("svc", observability.WithProcessor(proc))
        for i:=0;i<10;i++{
            _, s := tracer.Start(context.Background(), fmt.Sprintf("s-%d", i))
            s.End()
        }
        time.Sleep(400*time.Millisecond)
        count := len(exp.GetSpans())
        fmt.Printf("exported after timeout %d\\n", count)
        if count < 10 { panic(fmt.Sprintf("batch timeout should have exported 10, got %d", count)) }
        proc.Shutdown(context.Background())
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"batch timeout trigger failed: {proc.stdout} {proc.stderr}"
    )


def test_batch_batch_size_limit():
    code = textwrap.dedent("""
    package main
    import (
        "context"
        "fmt"
        "sync"
        "ride-observability/observability"
    )
    type countingExporter struct {
        mu sync.Mutex
        batches [][]observability.FinishedSpan
        maxBatch int
    }
    func (c *countingExporter) ExportSpans(ctx context.Context, spans []observability.FinishedSpan) error {
        c.mu.Lock()
        defer c.mu.Unlock()
        c.batches = append(c.batches, spans)
        if len(spans) > c.maxBatch { c.maxBatch = len(spans) }
        return nil
    }
    func main(){
        exporter := &countingExporter{}
        proc := observability.NewBatchProcessor(exporter, observability.WithBatchSize(5), observability.WithQueueSize(1000))
        tracer := observability.NewTracer("svc", observability.WithProcessor(proc))
        for i:=0;i<20;i++{
            _, s := tracer.Start(context.Background(), fmt.Sprintf("s-%d", i))
            s.End()
        }
        proc.ForceFlush(context.Background())
        proc.Shutdown(context.Background())
        fmt.Printf("maxBatch %d batches %d\\n", exporter.maxBatch, len(exporter.batches))
        if exporter.maxBatch > 5 { panic(fmt.Sprintf("batch size limit violated: max %d > 5", exporter.maxBatch)) }
        total := 0
        for _, b := range exporter.batches { total+=len(b) }
        if total!=20 { panic(fmt.Sprintf("total expected 20 got %d", total)) }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"batch size limit failed: {proc.stdout} {proc.stderr}"


def test_batch_evict_oldest():
    # Prior-violating: queue 2, enqueue 5, expect last 2 exported, not first 2
    code = textwrap.dedent("""
    package main
    import (
        "context"
        "fmt"
        "time"
        "ride-observability/observability"
    )
    func main(){
        exp := observability.NewMemoryExporter()
        proc := observability.NewBatchProcessor(exp, observability.WithQueueSize(2), observability.WithBatchSize(10), observability.WithBatchTimeout(5*time.Second))
        tracer := observability.NewTracer("svc", observability.WithProcessor(proc))
        // enqueue 5 quickly, queue size 2 => should evict oldest 3, keep newest 2
        for i:=0;i<5;i++{
            _, span := tracer.Start(context.Background(), fmt.Sprintf("span-%d", i))
            span.End()
        }
        // give a moment for background to not have flushed yet (timeout 5s)
        time.Sleep(100*time.Millisecond)
        proc.ForceFlush(context.Background())
        spans := exp.GetSpans()
        fmt.Printf("exported %d spans\\n", len(spans))
        if len(spans)!=2 {
            panic(fmt.Sprintf("evict-oldest: expected 2 exported (queue size 2 keeps newest 2), got %d.", len(spans)))
        }
        // check that exported are span-3 and span-4 (newest)
        has3, has4 := false, false
        has0 := false
        for _, s := range spans {
            if s.Name=="span-3" { has3=true }
            if s.Name=="span-4" { has4=true }
            if s.Name=="span-0" { has0=true }
        }
        if has0 {
            panic("evict-oldest: should have evicted span-0, but found it")
        }
        if !has3 || !has4 {
            panic(fmt.Sprintf("evict-oldest: expected span-3 and span-4 (newest), got has3=%v has4=%v", has3, has4))
        }
        proc.Shutdown(context.Background())
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"batch evict-oldest failed: {proc.stdout} {proc.stderr}"
    )


def test_batch_processor_drop_and_backpressure():
    code = textwrap.dedent("""
    package main
    import (
        "context"
        "fmt"
        "time"
        "ride-observability/observability"
    )
    func main(){
        exp := observability.NewMemoryExporter()
        proc := observability.NewBatchProcessor(exp, observability.WithQueueSize(10), observability.WithBatchSize(20), observability.WithBatchTimeout(5*time.Second))
        tracer := observability.NewTracer("svc", observability.WithProcessor(proc))
        for i:=0;i<100;i++{
            _, span := tracer.Start(context.Background(), fmt.Sprintf("s-%d", i))
            span.End()
        }
        time.Sleep(200*time.Millisecond)
        proc.ForceFlush(context.Background())
        exported := len(exp.GetSpans())
        fmt.Printf("exported %d out of 100\\n", exported)
        start := time.Now()
        for i:=0;i<50;i++{
            _, span := tracer.Start(context.Background(), "fast")
            span.End()
        }
        elapsed := time.Since(start)
        fmt.Printf("fast enqueue 50 took %v\\n", elapsed)
        if elapsed > 500*time.Millisecond {
            panic(fmt.Sprintf("enqueue blocking too long %v", elapsed))
        }
        proc.Shutdown(context.Background())
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"batch drop backpressure failed: {proc.stdout} {proc.stderr}"
    )


def test_batch_droppedcount_and_queuelen():
    code = textwrap.dedent("""
    package main
    import (
        "context"
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        exp := observability.NewMemoryExporter()
        proc := observability.NewBatchProcessor(exp, observability.WithQueueSize(5), observability.WithBatchSize(100))
        tracer := observability.NewTracer("svc", observability.WithProcessor(proc))
        for i:=0;i<20;i++{
            _, s := tracer.Start(context.Background(), fmt.Sprintf("s-%d", i))
            s.End()
        }
        var sp observability.Processor = proc
        if b, ok := sp.(interface{ DroppedCount() int }); ok {
            dc := b.DroppedCount()
            fmt.Printf("dropped %d\\n", dc)
            if dc < 0 { panic("negative dropped") }
            // with evict-oldest, dropped should be at least 15 (20-5)
            if dc < 10 { panic(fmt.Sprintf("expected dropped >=10 for evict-oldest, got %d", dc)) }
        } else {
            panic("BatchProcessor should have DroppedCount method")
        }
        if b, ok := sp.(interface{ QueueLen() int }); ok {
            ql := b.QueueLen()
            fmt.Printf("queueLen %d\\n", ql)
            if ql <0 || ql>5 { panic(fmt.Sprintf("queueLen out of expected 0-5 got %d", ql)) }
        } else {
            panic("BatchProcessor should have QueueLen method")
        }
        proc.ForceFlush(context.Background())
        proc.Shutdown(context.Background())
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"droppedcount queuelen failed: {proc.stdout} {proc.stderr}"
    )


def test_batch_shutdown_drops_new():
    code = textwrap.dedent("""
    package main
    import (
        "context"
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        exp := observability.NewMemoryExporter()
        proc := observability.NewBatchProcessor(exp, observability.WithQueueSize(100))
        tracer := observability.NewTracer("svc", observability.WithProcessor(proc))
        _, s := tracer.Start(context.Background(), "before-shutdown")
        s.End()
        proc.Shutdown(context.Background())
        countAfterShutdown := len(exp.GetSpans())
        _, s2 := tracer.Start(context.Background(), "after-shutdown")
        s2.End()
        if len(exp.GetSpans()) != countAfterShutdown {
            panic(fmt.Sprintf("spans after shutdown should be dropped, before %d after %d", countAfterShutdown, len(exp.GetSpans())))
        }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"shutdown drops new failed: {proc.stdout} {proc.stderr}"
    )


def test_batch_forceflush_timeout():
    code = textwrap.dedent("""
    package main
    import (
        "context"
        "fmt"
        "time"
        "ride-observability/observability"
    )
    type slowExporter struct {
        sleep time.Duration
    }
    func (s *slowExporter) ExportSpans(ctx context.Context, spans []observability.FinishedSpan) error {
        time.Sleep(s.sleep)
        return nil
    }
    func main(){
        slow := &slowExporter{sleep: 500*time.Millisecond}
        proc := observability.NewBatchProcessor(slow, observability.WithQueueSize(100), observability.WithBatchSize(10), observability.WithExportTimeout(100*time.Millisecond))
        tracer := observability.NewTracer("svc", observability.WithProcessor(proc))
        for i:=0;i<5;i++{
            _, s := tracer.Start(context.Background(), fmt.Sprintf("s-%d", i))
            s.End()
        }
        ctx, cancel := context.WithTimeout(context.Background(), 100*time.Millisecond)
        defer cancel()
        start := time.Now()
        err := proc.ForceFlush(ctx)
        elapsed := time.Since(start)
        fmt.Printf("ForceFlush elapsed %v err %v\\n", elapsed, err)
        if elapsed > 800*time.Millisecond {
            panic(fmt.Sprintf("ForceFlush took too long %v", elapsed))
        }
        proc.Shutdown(context.Background())
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"forceflush timeout failed: {proc.stdout} {proc.stderr}"
    )


def test_batch_export_timeout_respects():
    code = textwrap.dedent("""
    package main
    import (
        "context"
        "fmt"
        "time"
        "ride-observability/observability"
    )
    type slowExporter struct {
        sleep time.Duration
    }
    func (s *slowExporter) ExportSpans(ctx context.Context, spans []observability.FinishedSpan) error {
        select {
        case <-time.After(s.sleep):
            return nil
        case <-ctx.Done():
            return ctx.Err()
        }
    }
    func main(){
        slow := &slowExporter{sleep: 500*time.Millisecond}
        proc := observability.NewBatchProcessor(slow, observability.WithQueueSize(100), observability.WithBatchSize(5), observability.WithExportTimeout(100*time.Millisecond))
        tracer := observability.NewTracer("svc", observability.WithProcessor(proc))
        _, s := tracer.Start(context.Background(), "slow")
        s.End()
        start := time.Now()
        proc.ForceFlush(context.Background())
        elapsed := time.Since(start)
        fmt.Printf("ForceFlush with slow exporter respecting timeout elapsed %v\\n", elapsed)
        if elapsed > 600*time.Millisecond {
            panic(fmt.Sprintf("export timeout not respected, elapsed %v", elapsed))
        }
        proc.Shutdown(context.Background())
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"export timeout respects failed: {proc.stdout} {proc.stderr}"
    )


def test_batch_forceflush_block_and_drain():
    code = textwrap.dedent("""
    package main
    import (
        "context"
        "fmt"
        "time"
        "ride-observability/observability"
    )
    type slowExporter struct {
        dur time.Duration
    }
    func (s *slowExporter) ExportSpans(ctx context.Context, spans []observability.FinishedSpan) error {
        time.Sleep(s.dur)
        return nil
    }
    func main(){
        slow := &slowExporter{dur: 200*time.Millisecond}
        exp := slow
        proc := observability.NewBatchProcessor(exp, observability.WithQueueSize(2), observability.WithBatchSize(10), observability.WithBatchTimeout(5*time.Second))
        tracer := observability.NewTracer("svc", observability.WithProcessor(proc))
        // fill queue 2
        for i:=0;i<2;i++{
            _, s := tracer.Start(context.Background(), fmt.Sprintf("s-%d", i))
            s.End()
        }
        time.Sleep(50*time.Millisecond)
        // start ForceFlush in background (will take 200ms)
        done := make(chan error,1)
        go func(){
            done <- proc.ForceFlush(context.Background())
        }()
        time.Sleep(30*time.Millisecond) // let flush start
        // try to enqueue 1 more during flush — should block, not drop
        _, s := tracer.Start(context.Background(), "during-flush")
        s.End()
        err := <-done
        if err!=nil { fmt.Printf("ForceFlush err %v\\n", err) }
        // DroppedCount should be 0 — blocking drain means no drop during flush
        if bc, ok := proc.(interface{ DroppedCount() int }); ok {
            dc := bc.DroppedCount()
            fmt.Printf("DroppedCount after block-and-drain %d\\n", dc)
            if dc != 0 {
                panic(fmt.Sprintf("ForceFlush block-and-drain: DroppedCount should be 0 during flush, got %d", dc))
            }
        }
        proc.Shutdown(context.Background())
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"forceflush block-and-drain failed: {proc.stdout} {proc.stderr}"
    )


def test_batch_processor_concurrent():
    code = textwrap.dedent("""
    package main
    import (
        "context"
        "fmt"
        "sync"
        "time"
        "ride-observability/observability"
    )
    func main(){
        exp := observability.NewMemoryExporter()
        proc := observability.NewBatchProcessor(exp, observability.WithQueueSize(20000), observability.WithBatchSize(256), observability.WithBatchTimeout(50*time.Millisecond))
        tracer := observability.NewTracer("svc", observability.WithProcessor(proc))
        var wg sync.WaitGroup
        n:=100
        per:=100
        wg.Add(n)
        for i:=0;i<n;i++{
            go func(idx int){
                defer wg.Done()
                for j:=0;j<per;j++{
                    _, span := tracer.Start(context.Background(), fmt.Sprintf("span-%d-%d", idx, j))
                    span.End()
                }
            }(i)
        }
        wg.Wait()
        proc.ForceFlush(context.Background())
        time.Sleep(100*time.Millisecond)
        proc.Shutdown(context.Background())
        spans := exp.GetSpans()
        expected := n*per
        if len(spans)!=expected {
            panic(fmt.Sprintf("expected %d got %d", expected, len(spans)))
        }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"batch concurrent failed: {proc.stdout} {proc.stderr}"


def test_batch_processor_shutdown_flush():
    code = textwrap.dedent("""
    package main
    import (
        "context"
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        exp := observability.NewMemoryExporter()
        proc := observability.NewBatchProcessor(exp, observability.WithQueueSize(1000), observability.WithBatchSize(100))
        tracer := observability.NewTracer("svc", observability.WithProcessor(proc))
        for i:=0;i<250;i++{
            _, s := tracer.Start(context.Background(), fmt.Sprintf("s%d", i))
            s.End()
        }
        proc.Shutdown(context.Background())
        if len(exp.GetSpans())!=250 { panic(fmt.Sprintf("shutdown flush expected 250 got %d", len(exp.GetSpans()))) }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"shutdown flush failed: {proc.stdout} {proc.stderr}"


def test_batch_with_sampler():
    code = textwrap.dedent("""
    package main
    import (
        "context"
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        exp := observability.NewMemoryExporter()
        batch := observability.NewBatchProcessor(exp, observability.WithQueueSize(20000), observability.WithBatchSize(512))
        tracer := observability.NewTracer("svc", observability.WithProcessor(batch), observability.WithSampler(observability.NewRatioSampler(0.1)))
        n:=5000
        for i:=0;i<n;i++{
            _, s := tracer.Start(context.Background(), "op")
            s.End()
        }
        batch.ForceFlush(context.Background())
        batch.Shutdown(context.Background())
        exported := len(exp.GetSpans())
        fmt.Printf("n=%d exported=%d\\n", n, exported)
        if exported < 250 || exported > 750 {
            panic(fmt.Sprintf("expected ~500 got %d", exported))
        }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"batch with sampler failed: {proc.stdout} {proc.stderr}"
    )


def test_batch_concurrent_different_tracers():
    code = textwrap.dedent("""
    package main
    import (
        "context"
        "fmt"
        "sync"
        "ride-observability/observability"
    )
    func main(){
        exp := observability.NewMemoryExporter()
        proc := observability.NewBatchProcessor(exp, observability.WithQueueSize(20000), observability.WithBatchSize(128))
        var wg sync.WaitGroup
        n:=10
        wg.Add(n)
        for i:=0;i<n;i++{
            go func(idx int){
                defer wg.Done()
                tracer := observability.NewTracer(fmt.Sprintf("svc-%d", idx), observability.WithProcessor(proc))
                for j:=0;j<100;j++{
                    _, s := tracer.Start(context.Background(), fmt.Sprintf("op-%d-%d", idx, j))
                    s.End()
                }
            }(i)
        }
        wg.Wait()
        proc.ForceFlush(context.Background())
        proc.Shutdown(context.Background())
        if len(exp.GetSpans())!= n*100 {
            panic(fmt.Sprintf("expected %d got %d", n*100, len(exp.GetSpans())))
        }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"concurrent different tracers failed: {proc.stdout} {proc.stderr}"
    )


def test_metrics_cardinality_limit_drop():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        prov := observability.NewMetricsProvider(observability.WithMaxCardinality(100), observability.WithCardinalityOverflowHandling("drop"))
        for i:=0;i<200;i++{
            c := prov.Counter("limited_counter", observability.WithLabels(map[string]string{"id": fmt.Sprintf("%d", i)}))
            c.Inc()
        }
        fams := prov.Collect()
        var count int
        for _, fam := range fams {
            if fam.Name=="limited_counter" { count=len(fam.Metrics) }
        }
        fmt.Printf("count %d dropped %d\\n", count, prov.DroppedSeriesCount())
        if count>100 { panic(fmt.Sprintf("cardinality limit violated: got %d >100", count)) }
        if prov.DroppedSeriesCount() < 100 { panic(fmt.Sprintf("dropped count expected >=100 got %d", prov.DroppedSeriesCount())) }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"cardinality drop failed: {proc.stdout} {proc.stderr}"


def test_metrics_cardinality_per_name():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        prov := observability.NewMetricsProvider(observability.WithMaxCardinality(2))
        prov.Counter("a", observability.WithLabels(map[string]string{"l":"1"})).Inc()
        prov.Counter("a", observability.WithLabels(map[string]string{"l":"2"})).Inc()
        prov.Counter("a", observability.WithLabels(map[string]string{"l":"3"})).Inc()
        prov.Counter("b", observability.WithLabels(map[string]string{"l":"1"})).Inc()
        prov.Counter("b", observability.WithLabels(map[string]string{"l":"2"})).Inc()
        prov.Counter("b", observability.WithLabels(map[string]string{"l":"3"})).Inc()
        fams := prov.Collect()
        for _, fam := range fams {
            if fam.Name=="a" && len(fam.Metrics)>2 { panic("per-name limit a") }
            if fam.Name=="b" && len(fam.Metrics)>2 { panic("per-name limit b") }
        }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"per name cardinality failed: {proc.stdout} {proc.stderr}"
    )


def test_metrics_cardinality_aggregate():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        prov := observability.NewMetricsProvider(observability.WithMaxCardinality(2), observability.WithCardinalityOverflowHandling("aggregate"))
        prov.Counter("agg_counter", observability.WithLabels(map[string]string{"id":"1"})).Add(1)
        prov.Counter("agg_counter", observability.WithLabels(map[string]string{"id":"2"})).Add(2)
        prov.Counter("agg_counter", observability.WithLabels(map[string]string{"id":"3"})).Add(3)
        prov.Counter("agg_counter", observability.WithLabels(map[string]string{"id":"4"})).Add(4)
        fams := prov.Collect()
        var found bool
        var overflowVal float64
        var totalMetrics int
        for _, fam := range fams {
            if fam.Name=="agg_counter" {
                totalMetrics=len(fam.Metrics)
                for _, m := range fam.Metrics {
                    if m.Labels["__overflow__"]=="true" {
                        found=true
                        overflowVal+=m.Value
                    }
                }
            }
        }
        if !found { panic("overflow series not found") }
        if overflowVal < 7 { panic(fmt.Sprintf("overflow value expected >=7 got %f", overflowVal)) }
        if prov.DroppedSeriesCount()!=0 { panic("aggregate mode DroppedSeriesCount should be 0") }
        fmt.Printf("total %d overflow %f\\n", totalMetrics, overflowVal)
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"aggregate failed: {proc.stdout} {proc.stderr}"


def test_metrics_cardinality_unlimited():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        prov := observability.NewMetricsProvider()
        for i:=0;i<200;i++{
            prov.Counter("unlimited", observability.WithLabels(map[string]string{"id": fmt.Sprintf("%d", i)})).Inc()
        }
        fams := prov.Collect()
        for _, fam := range fams {
            if fam.Name=="unlimited" && len(fam.Metrics)!=200 {
                panic(fmt.Sprintf("unlimited should have 200 got %d", len(fam.Metrics)))
            }
        }
        if prov.DroppedSeriesCount()!=0 { panic("unlimited dropped should be 0") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"unlimited failed: {proc.stdout} {proc.stderr}"


def test_metrics_cardinality_concurrent():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "sync"
        "ride-observability/observability"
    )
    func main(){
        prov := observability.NewMetricsProvider(observability.WithMaxCardinality(10000))
        var wg sync.WaitGroup
        n:=100
        per:=100
        wg.Add(n)
        for i:=0;i<n;i++{
            go func(idx int){
                defer wg.Done()
                for j:=0;j<per;j++{
                    c := prov.Counter("conc_limit", observability.WithLabels(map[string]string{"id": fmt.Sprintf("%d-%d", idx, j)}))
                    c.Inc()
                }
            }(i)
        }
        wg.Wait()
        fams := prov.Collect()
        var total int
        for _, fam := range fams {
            if fam.Name=="conc_limit" { total=len(fam.Metrics) }
        }
        if total!= n*per { panic(fmt.Sprintf("expected %d got %d", n*per, total)) }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"cardinality concurrent failed: {proc.stdout} {proc.stderr}"
    )


def test_metrics_collect_immutability_under_concurrency():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "sync"
        "ride-observability/observability"
    )
    func main(){
        prov := observability.NewMetricsProvider()
        c := prov.Counter("race_collect", observability.WithLabels(map[string]string{"a":"1"}))
        var wg sync.WaitGroup
        wg.Add(2)
        go func(){
            defer wg.Done()
            for i:=0;i<10000;i++{ c.Inc() }
        }()
        go func(){
            defer wg.Done()
            for i:=0;i<1000;i++{ prov.Collect() }
        }()
        wg.Wait()
        fmt.Println("OK")
    }
    """)
    proc = go_run_with_race(code, timeout=30)
    assert proc.returncode == 0, (
        f"collect immutability race failed: {proc.stdout} {proc.stderr}"
    )


def test_span_resource_limits():
    code = textwrap.dedent("""
    package main
    import (
        "context"
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        exp := observability.NewMemoryExporter()
        proc := observability.NewSimpleProcessor(exp)
        tracer := observability.NewTracer("svc", observability.WithProcessor(proc))
        _, span := tracer.Start(context.Background(), "limits")
        for i:=0;i<200;i++{ span.AddAttribute(fmt.Sprintf("k%d", i), i) }
        for i:=0;i<200;i++{ span.AddEvent(fmt.Sprintf("ev%d", i)) }
        span.End()
        s := exp.GetSpans()[0]
        if len(s.Attributes)>128 { panic(fmt.Sprintf("attrs >128 %d", len(s.Attributes))) }
        if len(s.Events)>128 { panic(fmt.Sprintf("events >128 %d", len(s.Events))) }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"resource limits failed: {proc.stdout} {proc.stderr}"


def test_span_attribute_initial_limit_withattributes():
    code = textwrap.dedent("""
    package main
    import (
        "context"
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        exp := observability.NewMemoryExporter()
        proc := observability.NewSimpleProcessor(exp)
        tracer := observability.NewTracer("svc", observability.WithProcessor(proc))
        var attrs []observability.Attribute
        for i:=0;i<200;i++{ attrs=append(attrs, observability.Attribute{Key: fmt.Sprintf("k%d", i), Value:i}) }
        _, span := tracer.Start(context.Background(), "initial", observability.WithAttributes(attrs...))
        span.End()
        s := exp.GetSpans()[0]
        if len(s.Attributes)>128 { panic(fmt.Sprintf("initial attrs >128 %d", len(s.Attributes))) }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"initial limit failed: {proc.stdout} {proc.stderr}"


def test_span_attribute_truncate_exact_1024():
    code = textwrap.dedent("""
    package main
    import (
        "context"
        "fmt"
        "strings"
        "ride-observability/observability"
    )
    func main(){
        exp := observability.NewMemoryExporter()
        proc := observability.NewSimpleProcessor(exp)
        tracer := observability.NewTracer("svc", observability.WithProcessor(proc))
        long := strings.Repeat("a", 2000)
        _, span := tracer.Start(context.Background(), "truncate", observability.WithAttributes(observability.Attribute{Key:"long", Value:long}))
        span.End()
        s := exp.GetSpans()[0]
        v, ok := s.Attributes["long"]
        if !ok { panic("long attr missing") }
        str, ok := v.(string)
        if !ok { panic("not string") }
        if len(str)!=1024 { panic(fmt.Sprintf("expected exactly 1024 got %d", len(str))) }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"truncate 1024 failed: {proc.stdout} {proc.stderr}"


def test_batch_non_positive_options_fallback():
    code = textwrap.dedent("""
    package main
    import (
        "context"
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        exp := observability.NewMemoryExporter()
        proc := observability.NewBatchProcessor(exp, observability.WithBatchSize(0), observability.WithQueueSize(0), observability.WithBatchTimeout(0), observability.WithExportTimeout(0))
        tracer := observability.NewTracer("svc", observability.WithProcessor(proc))
        _, s := tracer.Start(context.Background(), "fallback")
        s.End()
        proc.ForceFlush(context.Background())
        if len(exp.GetSpans())!=1 { panic(fmt.Sprintf("non-positive options should fallback to defaults and export 1, got %d", len(exp.GetSpans()))) }
        proc.Shutdown(context.Background())
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"non-positive fallback failed: {proc.stdout} {proc.stderr}"
    )


def test_sampler_ratio_ignores_name_kind():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        sampler := observability.NewRatioSampler(0.5)
        tid := "0102030405060708090a0b0c0d0e0f10"
        p1 := observability.SamplingRequest{TraceID:tid, SpanName:"op-a", Kind:observability.KindServer}
        p2 := observability.SamplingRequest{TraceID:tid, SpanName:"op-b", Kind:observability.KindClient}
        p3 := observability.SamplingRequest{TraceID:tid, SpanName:"very-different-name", Kind:observability.KindInternal}
        d1 := sampler.ShouldSample(p1)
        d2 := sampler.ShouldSample(p2)
        d3 := sampler.ShouldSample(p3)
        if d1!=d2 || d1!=d3 { panic(fmt.Sprintf("RatioSampler must be based only on TraceID/Status/Priority, not name/kind, got %d %d %d", d1,d2,d3)) }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"ratio ignores name/kind failed: {proc.stdout} {proc.stderr}"
    )


def test_batch_order_preserved():
    code = textwrap.dedent("""
    package main
    import (
        "context"
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        exp := observability.NewMemoryExporter()
        proc := observability.NewBatchProcessor(exp, observability.WithQueueSize(100), observability.WithBatchSize(3), observability.WithBatchTimeout(5*1000000000))
        tracer := observability.NewTracer("svc", observability.WithProcessor(proc))
        names := []string{}
        for i:=0;i<10;i++{
            name := fmt.Sprintf("order-%d", i)
            names = append(names, name)
            _, s := tracer.Start(context.Background(), name)
            s.End()
        }
        proc.ForceFlush(context.Background())
        spans := exp.GetSpans()
        if len(spans)!=10 { panic(fmt.Sprintf("expected 10 got %d", len(spans))) }
        for i, n := range names {
            if spans[i].Name != n { panic(fmt.Sprintf("order preserved failed at %d expected %s got %s", i, n, spans[i].Name)) }
        }
        proc.Shutdown(context.Background())
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"batch order preserved failed: {proc.stdout} {proc.stderr}"
    )


def test_batch_droppedcount_not_for_non_recording():
    code = textwrap.dedent("""
    package main
    import (
        "context"
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        exp := observability.NewMemoryExporter()
        proc := observability.NewBatchProcessor(exp, observability.WithQueueSize(5), observability.WithBatchSize(10))
        tracer := observability.NewTracer("svc", observability.WithProcessor(proc), observability.WithSampler(observability.NewNeverSampler()))
        for i:=0;i<10;i++{
            _, s := tracer.Start(context.Background(), fmt.Sprintf("drop-%d", i))
            s.End()
        }
        proc.ForceFlush(context.Background())
        // non-recording spans should not be counted as dropped and not exported
        if len(exp.GetSpans())!=0 { panic(fmt.Sprintf("non-recording should not be exported, got %d", len(exp.GetSpans()))) }
        if b, ok := proc.(interface{ DroppedCount() int }); ok {
            if b.DroppedCount()!=0 { panic(fmt.Sprintf("non-recording spans must not increment DroppedCount, got %d", b.DroppedCount())) }
        }
        proc.Shutdown(context.Background())
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"droppedcount not for non-recording failed: {proc.stdout} {proc.stderr}"
    )


def test_metrics_cardinality_reuse_at_limit():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        prov := observability.NewMetricsProvider(observability.WithMaxCardinality(2), observability.WithCardinalityOverflowHandling("drop"))
        prov.Counter("reuse_limit", observability.WithLabels(map[string]string{"id":"1"})).Inc()
        prov.Counter("reuse_limit", observability.WithLabels(map[string]string{"id":"2"})).Inc()
        // at limit
        prov.Counter("reuse_limit", observability.WithLabels(map[string]string{"id":"3"})).Inc() // dropped
        droppedBefore := prov.DroppedSeriesCount()
        // reuse existing id 1 should NOT increase dropped
        prov.Counter("reuse_limit", observability.WithLabels(map[string]string{"id":"1"})).Inc()
        droppedAfter := prov.DroppedSeriesCount()
        if droppedAfter != droppedBefore { panic(fmt.Sprintf("reuse existing at limit should not increase Dropped, before %d after %d", droppedBefore, droppedAfter)) }
        fams := prov.Collect()
        for _, fam := range fams {
            if fam.Name=="reuse_limit" && len(fam.Metrics)!=2 { panic(fmt.Sprintf("expected 2 metrics at limit, got %d", len(fam.Metrics))) }
        }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"cardinality reuse at limit failed: {proc.stdout} {proc.stderr}"
    )


def test_tracing_sampler_priority_via_tracer():
    code = textwrap.dedent("""
    package main
    import (
        "context"
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        exp := observability.NewMemoryExporter()
        batch := observability.NewBatchProcessor(exp, observability.WithQueueSize(100), observability.WithBatchSize(10))
        sampler := observability.NewRatioSampler(0.0)
        tracer := observability.NewTracer("svc", observability.WithProcessor(batch), observability.WithSampler(sampler))
        _, s1 := tracer.Start(context.Background(), "normal", observability.WithAttributes(observability.Attribute{Key:"priority", Value:"normal"}))
        if s1.IsRecording() { panic("normal priority at 0.0 should not be recording") }
        s1.End()
        _, s2 := tracer.Start(context.Background(), "critical", observability.WithAttributes(observability.Attribute{Key:"priority", Value:"critical"}))
        if !s2.IsRecording() { panic("critical priority at 0.0 should be recording via override") }
        s2.End()
        batch.ForceFlush(context.Background())
        batch.Shutdown(context.Background())
        if len(exp.GetSpans())!=1 { panic(fmt.Sprintf("expected 1 critical span exported, got %d", len(exp.GetSpans()))) }
        if exp.GetSpans()[0].Name!="critical" { panic("expected critical span") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"sampler priority via tracer failed: {proc.stdout} {proc.stderr}"
    )


def test_batch_forceflush_respects_export_timeout():
    code = textwrap.dedent("""
    package main
    import (
        "context"
        "fmt"
        "time"
        "ride-observability/observability"
    )
    type slowExp struct{}
    func (s *slowExp) ExportSpans(ctx context.Context, spans []observability.FinishedSpan) error {
        time.Sleep(400*time.Millisecond)
        return nil
    }
    func main(){
        exp := &slowExp{}
        proc := observability.NewBatchProcessor(exp, observability.WithQueueSize(100), observability.WithBatchSize(5), observability.WithExportTimeout(100*time.Millisecond))
        tracer := observability.NewTracer("svc", observability.WithProcessor(proc))
        for i:=0;i<10;i++{
            _, span := tracer.Start(context.Background(), fmt.Sprintf("s%d", i))
            span.End()
        }
        ctx, cancel := context.WithTimeout(context.Background(), 150*time.Millisecond)
        defer cancel()
        start := time.Now()
        proc.ForceFlush(ctx)
        elapsed := time.Since(start)
        if elapsed > 500*time.Millisecond { panic(fmt.Sprintf("ForceFlush should respect export timeout, elapsed %v", elapsed)) }
        proc.Shutdown(context.Background())
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"forceflush respects export timeout failed: {proc.stdout} {proc.stderr}"
    )


def test_batch_shutdown_flushes_in_batches_respecting_size():
    code = textwrap.dedent("""
    package main
    import (
        "context"
        "fmt"
        "sync"
        "ride-observability/observability"
    )
    type countingExp struct{
        mu sync.Mutex
        maxBatch int
        batches int
        total int
    }
    func (c *countingExp) ExportSpans(ctx context.Context, spans []observability.FinishedSpan) error {
        c.mu.Lock()
        defer c.mu.Unlock()
        c.batches++
        c.total+=len(spans)
        if len(spans) > c.maxBatch { c.maxBatch = len(spans) }
        return nil
    }
    func main(){
        exp := &countingExp{}
        proc := observability.NewBatchProcessor(exp, observability.WithQueueSize(1000), observability.WithBatchSize(10))
        tracer := observability.NewTracer("svc", observability.WithProcessor(proc))
        for i:=0;i<25;i++{
            _, s := tracer.Start(context.Background(), fmt.Sprintf("s%d", i))
            s.End()
        }
        proc.Shutdown(context.Background())
        if exp.total != 25 { panic(fmt.Sprintf("expected total 25 got %d", exp.total)) }
        if exp.maxBatch > 10 { panic(fmt.Sprintf("Shutdown must respect BatchSize 10, got max %d", exp.maxBatch)) }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"shutdown batches respecting size failed: {proc.stdout} {proc.stderr}"
    )


def test_sampler_ratio_025_075():
    code = textwrap.dedent("""
    package main
    import (
        "crypto/rand"
        "encoding/hex"
        "fmt"
        "ride-observability/observability"
    )
    func randID() string {
        b:=make([]byte,16)
        rand.Read(b)
        return hex.EncodeToString(b)
    }
    func main(){
        s025 := observability.NewRatioSampler(0.25)
        s075 := observability.NewRatioSampler(0.75)
        n:=8000
        c025:=0
        c075:=0
        for i:=0;i<n;i++{
            tid := randID()
            if s025.ShouldSample(observability.SamplingRequest{TraceID:tid})==observability.DecisionKeep { c025++ }
            if s075.ShouldSample(observability.SamplingRequest{TraceID:tid})==observability.DecisionKeep { c075++ }
        }
        r025 := float64(c025)/float64(n)
        r075 := float64(c075)/float64(n)
        if r025 < 0.15 || r025 > 0.35 { panic(fmt.Sprintf("0.25 expected ~0.25 got %f", r025)) }
        if r075 < 0.65 || r075 > 0.85 { panic(fmt.Sprintf("0.75 expected ~0.75 got %f", r075)) }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"ratio 0.25 0.75 failed: {proc.stdout} {proc.stderr}"


def test_batch_concurrent_with_sampler():
    code = textwrap.dedent("""
    package main
    import (
        "context"
        "fmt"
        "sync"
        "ride-observability/observability"
    )
    func main(){
        exp := observability.NewMemoryExporter()
        proc := observability.NewBatchProcessor(exp, observability.WithQueueSize(20000), observability.WithBatchSize(128))
        sampler := observability.NewRatioSampler(0.5)
        var wg sync.WaitGroup
        n:=50
        per:=100
        wg.Add(n)
        for i:=0;i<n;i++{
            go func(){
                defer wg.Done()
                tracer := observability.NewTracer("svc", observability.WithProcessor(proc), observability.WithSampler(sampler))
                for j:=0;j<per;j++{
                    _, s := tracer.Start(context.Background(), "op")
                    s.End()
                }
            }()
        }
        wg.Wait()
        proc.ForceFlush(context.Background())
        proc.Shutdown(context.Background())
        total := len(exp.GetSpans())
        // 50*100=5000, ratio 0.5 => ~2500, allow 2000-3000
        if total < 2000 || total > 3000 { panic(fmt.Sprintf("concurrent with sampler 0.5 expected ~2500 got %d", total)) }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"concurrent with sampler failed: {proc.stdout} {proc.stderr}"
    )


def test_span_attribute_limit_with_add_after_initial():
    code = textwrap.dedent("""
    package main
    import (
        "context"
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        exp := observability.NewMemoryExporter()
        proc := observability.NewSimpleProcessor(exp)
        tracer := observability.NewTracer("svc", observability.WithProcessor(proc))
        var attrs []observability.Attribute
        for i:=0;i<100;i++{ attrs=append(attrs, observability.Attribute{Key: fmt.Sprintf("k%d", i), Value:i}) }
        _, span := tracer.Start(context.Background(), "limit-mix", observability.WithAttributes(attrs...))
        for i:=100;i<200;i++{ span.AddAttribute(fmt.Sprintf("k%d", i), i) }
        span.End()
        s := exp.GetSpans()[0]
        if len(s.Attributes) > 128 { panic(fmt.Sprintf("attrs limit 128 exceeded got %d", len(s.Attributes))) }
        if len(s.Attributes) < 128 { panic(fmt.Sprintf("should have 128 after 200 adds, got %d", len(s.Attributes))) }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"attr limit with add after initial failed: {proc.stdout} {proc.stderr}"
    )


def test_metrics_histogram_cumulative_with_zero():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        prov := observability.NewMetricsProvider()
        h := prov.Histogram("zero_hist", observability.WithBuckets([]float64{0,1,5}))
        h.Observe(0)
        h.Observe(0)
        h.Observe(1)
        h.Observe(6)
        fams := prov.Collect()
        for _, fam := range fams {
            if fam.Name=="zero_hist" {
                m := fam.Metrics[0]
                // 0 bucket should have 2
                if m.Buckets[0].Count != 2 { panic(fmt.Sprintf("bucket 0 expected 2 got %d", m.Buckets[0].Count)) }
                if m.Buckets[1].Count != 3 { panic(fmt.Sprintf("bucket 1 expected 3 got %d", m.Buckets[1].Count)) }
                if m.Buckets[2].Count != 3 { panic(fmt.Sprintf("bucket 5 expected 3 got %d", m.Buckets[2].Count)) }
                if m.Count != 4 { panic("count 4") }
                fmt.Println("OK")
                return
            }
        }
        panic("not found")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"histogram zero failed: {proc.stdout} {proc.stderr}"


def test_tracing_sampler_with_parent_and_child_ratio():
    code = textwrap.dedent("""
    package main
    import (
        "context"
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        exp := observability.NewMemoryExporter()
        proc := observability.NewBatchProcessor(exp, observability.WithQueueSize(1000), observability.WithBatchSize(10))
        root := observability.NewRatioSampler(0.0) // always drop
        pa := observability.NewParentAwareSampler(root)
        tracer := observability.NewTracer("svc", observability.WithProcessor(proc), observability.WithSampler(pa))
        // No parent => root 0.0 => drop
        _, s1 := tracer.Start(context.Background(), "no-parent")
        if s1.IsRecording() { panic("no-parent with root 0.0 should drop") }
        s1.End()
        // Parent sampled true but root 0.0 => drop per AND logic
        parentSampled := observability.TraceContext{TraceID:"0102030405060708090a0b0c0d0e0f10", SpanID:"0102030405060708", Sampled:true}
        ctx := observability.ContextWithTrace(context.Background(), parentSampled)
        _, s2 := tracer.Start(ctx, "parent-sampled-root-drop")
        if s2.IsRecording() { panic("parent sampled true + root 0.0 should drop per AND") }
        s2.End()
        proc.ForceFlush(context.Background())
        proc.Shutdown(context.Background())
        if len(exp.GetSpans())!=0 { panic(fmt.Sprintf("expected 0 exported, got %d", len(exp.GetSpans()))) }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"sampler parent and child ratio failed: {proc.stdout} {proc.stderr}"
    )


def test_batch_non_recording_not_queued_even_when_queue_full():
    code = textwrap.dedent("""
    package main
    import (
        "context"
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        exp := observability.NewMemoryExporter()
        proc := observability.NewBatchProcessor(exp, observability.WithQueueSize(2), observability.WithBatchSize(10))
        tracerNever := observability.NewTracer("svc", observability.WithProcessor(proc), observability.WithSampler(observability.NewNeverSampler()))
        tracerAlways := observability.NewTracer("svc", observability.WithProcessor(proc), observability.WithSampler(observability.NewAlwaysSampler()))
        // Fill queue with always spans
        for i:=0;i<2;i++{
            _, s := tracerAlways.Start(context.Background(), fmt.Sprintf("always-%d", i))
            s.End()
        }
        // Now queue full (2), try to add 10 never spans - they should not be queued and not increment DroppedCount
        for i:=0;i<10;i++{
            _, s := tracerNever.Start(context.Background(), fmt.Sprintf("never-%d", i))
            s.End()
        }
        if b, ok := proc.(interface{ DroppedCount() int }); ok {
            if b.DroppedCount()!=0 { panic(fmt.Sprintf("non-recording must not increment DroppedCount even when queue full, got %d", b.DroppedCount())) }
        }
        proc.ForceFlush(context.Background())
        proc.Shutdown(context.Background())
        // Only 2 always spans should be exported
        if len(exp.GetSpans())!=2 { panic(fmt.Sprintf("expected 2 always spans, got %d", len(exp.GetSpans()))) }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"non-recording not queued when full failed: {proc.stdout} {proc.stderr}"
    )

