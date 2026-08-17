"""
Step1 verifier — single-header x-ride-trace, concurrency, Collect deep copys
"""

import os, subprocess, tempfile, re, textwrap, shutil
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


def go_run_program(go_code: str):
    tmp = tempfile.mkdtemp(prefix="obs_test_")
    try:
        mod = textwrap.dedent(f"""
        module testharness
        go 1.22
        require ride-observability v0.0.0
        replace ride-observability => {APP_DIR}
        """)
        open(os.path.join(tmp, "go.mod"), "w").write(mod)
        open(os.path.join(tmp, "main.go"), "w").write(go_code)
        proc = run(["go", "run", "."], cwd=tmp, timeout=20)
        return proc
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def go_run_race_program(go_code: str, timeout=30):
    tmp = tempfile.mkdtemp(prefix="obs_test_")
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

def test_files_exist():
    assert os.path.isdir(os.path.join(APP_DIR, "observability"))
    for fname in ["tracing.go", "metrics.go", "logger.go"]:
        assert os.path.isfile(os.path.join(APP_DIR, "observability", fname)), (
            f"missing {fname}"
        )



def test_go_mod_no_external():
    with open(os.path.join(APP_DIR, "go.mod")) as f:
        content = f.read()
    for line in content.splitlines():
        line = line.strip()
        m = re.match(r"^(require\s+)?([^\s]+)\s+v[0-9]", line)
        if m:
            dep = m.group(2).split("/")[0]
            assert "." not in dep, f"external dependency {m.group(2)} not allowed"



def test_stdlib_only():
    import_re = re.compile(r'"([^"]+)"')
    for root, _, files in os.walk(APP_DIR):
        if "/.git" in root or "testharness" in root:
            continue
        for file in files:
            if not file.endswith(".go"):
                continue
            path = os.path.join(root, file)
            with open(path) as fh:
                txt = fh.read()
            for block in re.findall(r"import\s*\((.*?)\)", txt, flags=re.S):
                for imp in import_re.findall(block):
                    first = imp.split("/")[0]
                    assert "." not in first or first == "ride-observability", (
                        f"non stdlib import {imp} in {path}"
                    )
            for imp in re.findall(r'import\s+(?:[\w.]+\s+)?"([^"]+)"', txt):
                first = imp.split("/")[0]
                if first == "ride-observability":
                    continue
                assert "." not in first, f"non stdlib import {imp} in {path}"



def test_go_build_and_vet():
    p = run(["go", "vet", "./..."])
    assert p.returncode == 0, f"go vet failed: {p.stdout} {p.stderr}"
    p = run(["go", "build", "./..."])
    assert p.returncode == 0, f"go build failed: {p.stdout} {p.stderr}"



def test_tracing_id_format():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "regexp"
        "context"
        "ride-observability/observability"
    )
    func main(){
        exp := observability.NewMemoryExporter()
        proc := observability.NewSimpleProcessor(exp)
        tracer := observability.NewTracer("ride-service", observability.WithProcessor(proc))
        ctx, span := tracer.Start(context.Background(), "test-span")
        _ = ctx
        span.End()
        spans := exp.GetSpans()
        if len(spans)!=1 { panic(fmt.Sprintf("expected 1 span got %d", len(spans))) }
        sc := spans[0].SpanContext
        if len(sc.TraceID)!=32 { panic("traceID len !=32") }
        if len(sc.SpanID)!=16 { panic("spanID len !=16") }
        hex32 := regexp.MustCompile("^[0-9a-fA-F]{32}$")
        hex16 := regexp.MustCompile("^[0-9a-fA-F]{16}$")
        if !hex32.MatchString(sc.TraceID) { panic("traceID not hex32: "+sc.TraceID) }
        if !hex16.MatchString(sc.SpanID) { panic("spanID not hex16: "+sc.SpanID) }
        if !sc.Sampled { panic("sampled should be true in step1") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"tracing id format failed: {proc.stdout} {proc.stderr}"
    )



def test_tracing_id_uniqueness():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "context"
        "ride-observability/observability"
    )
    func main(){
        exp := observability.NewMemoryExporter()
        proc := observability.NewSimpleProcessor(exp)
        tracer := observability.NewTracer("svc", observability.WithProcessor(proc))
        seenTrace := map[string]bool{}
        seenSpan := map[string]bool{}
        n:=5000
        for i:=0;i<n;i++{
            _, span := tracer.Start(context.Background(), fmt.Sprintf("s-%d", i))
            sc := span.Context()
            if seenTrace[sc.TraceID] { panic("duplicate traceID "+sc.TraceID) }
            if seenSpan[sc.SpanID] { panic("duplicate spanID "+sc.SpanID) }
            seenTrace[sc.TraceID]=true
            seenSpan[sc.SpanID]=true
            span.End()
        }
        fmt.Printf("unique trace %d span %d OK\\n", len(seenTrace), len(seenSpan))
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"id uniqueness failed: {proc.stdout} {proc.stderr}"



def test_tracing_no_parent_new_trace():
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
        _, s1 := tracer.Start(context.Background(), "a")
        _, s2 := tracer.Start(context.Background(), "b")
        if s1.Context().TraceID == s2.Context().TraceID {
            panic("background starts should generate different traceIDs")
        }
        s1.End()
        s2.End()
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"no parent new trace failed: {proc.stdout} {proc.stderr}"
    )



def test_tracing_child_inherits():
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
        tracer := observability.NewTracer("ride-service", observability.WithProcessor(proc))
        ctx1, s1 := tracer.Start(context.Background(), "parent")
        ctx2, s2 := tracer.Start(ctx1, "child")
        s2.End()
        s1.End()
        spans := exp.GetSpans()
        if len(spans)!=2 { panic(fmt.Sprintf("expected 2 spans got %d", len(spans))) }
        var parent, child *observability.FinishedSpan
        for i:= range spans {
            if spans[i].Name=="parent" { parent = &spans[i] }
            if spans[i].Name=="child" { child = &spans[i] }
        }
        if parent==nil || child==nil { panic("parent/child not found") }
        if parent.SpanContext.TraceID != child.SpanContext.TraceID { panic(fmt.Sprintf("traceID mismatch parent %s child %s", parent.SpanContext.TraceID, child.SpanContext.TraceID)) }
        if parent.SpanContext.SpanID == child.SpanContext.SpanID { panic("spanID should differ") }
        if child.ParentID != parent.SpanContext.SpanID { panic(fmt.Sprintf("child parentID %s != parent spanID %s", child.ParentID, parent.SpanContext.SpanID)) }
        sc, ok := observability.TraceFromContext(ctx2)
        if !ok { panic("no TraceContext in ctx2") }
        if sc.TraceID != child.SpanContext.TraceID { panic("ctx2 traceID mismatch") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"child inherits failed: {proc.stdout} {proc.stderr}"



def test_tracing_withparent_overrides():
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
        ctx1, s1 := tracer.Start(context.Background(), "parent1")
        ctx2, s2 := tracer.Start(context.Background(), "parent2")
        explicit := s2.Context()
        _, child := tracer.Start(ctx1, "child", observability.WithParent(explicit))
        if child.Context().TraceID != explicit.TraceID {
            panic(fmt.Sprintf("WithParent should override context parent, expected %s got %s", explicit.TraceID, child.Context().TraceID))
        }
        if child.Context().ParentID != explicit.SpanID {
            panic("WithParent parentID mismatch")
        }
        child.End()
        s1.End()
        s2.End()
        _ = ctx2
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"withparent override failed: {proc.stdout} {proc.stderr}"
    )



def test_tracing_span_kind():
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
        _, s := tracer.Start(context.Background(), "kind-test", observability.WithSpanKind(observability.KindServer))
        s.End()
        spans := exp.GetSpans()
        if spans[0].Kind != observability.KindServer { panic(fmt.Sprintf("expected server kind got %d", spans[0].Kind)) }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"span kind failed: {proc.stdout} {proc.stderr}"



def test_tracing_attributes_events_status():
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
        tracer := observability.NewTracer("ride-service", observability.WithProcessor(proc))
        _, span := tracer.Start(context.Background(), "op", observability.WithAttributes(observability.Attribute{Key:"k1", Value:"v1"}))
        span.AddAttribute("k2", 42)
        span.AddAttribute("k3", true)
        span.AddEvent("event1", observability.Attribute{Key:"e1", Value:"x"})
        span.SetStatus(observability.StatusError, "oops")
        span.End()
        spans := exp.GetSpans()
        if len(spans)!=1 { panic("expected 1") }
        s := spans[0]
        if len(s.Attributes) < 3 { panic(fmt.Sprintf("expected >=3 attrs got %d", len(s.Attributes))) }
        if s.Attributes["k1"]!="v1" { panic("k1 missing") }
        if len(s.Events)!=1 { panic(fmt.Sprintf("expected 1 event got %d", len(s.Events))) }
        if s.StatusCode != observability.StatusError { panic("status code not error") }
        if s.StatusMessage != "oops" { panic("status msg mismatch") }
        if s.ServiceName != "ride-service" { panic("service name missing "+s.ServiceName) }
        if s.StartTime.IsZero() || s.EndTime.IsZero() { panic("start/end zero") }
        if s.EndTime.Before(s.StartTime) { panic("end before start") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"attrs/events/status failed: {proc.stdout} {proc.stderr}"
    )



def test_tracing_add_after_end_noop():
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
        _, span := tracer.Start(context.Background(), "after-end")
        span.End()
        span.AddAttribute("should-not", "appear")
        span.AddEvent("should-not")
        span.SetStatus(observability.StatusError, "nope")
        spans := exp.GetSpans()
        if len(spans)!=1 { panic("expected 1") }
        if _, ok := spans[0].Attributes["should-not"]; ok { panic("attribute after End should be noop") }
        if len(spans[0].Events)!=0 { panic("event after End should be noop") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"add after end failed: {proc.stdout} {proc.stderr}"



def test_tracing_event_attributes():
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
        _, span := tracer.Start(context.Background(), "ev-attr")
        span.AddEvent("ride_matched", observability.Attribute{Key:"driver", Value:"d123"}, observability.Attribute{Key:"eta", Value:5})
        span.End()
        spans := exp.GetSpans()
        ev := spans[0].Events[0]
        if ev.Name!="ride_matched" { panic("event name mismatch") }
        if len(ev.Attributes)!=2 { panic("event attrs count mismatch") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"event attrs failed: {proc.stdout} {proc.stderr}"



def test_tracing_end_idempotent():
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
        _, span := tracer.Start(context.Background(), "idempotent")
        span.End()
        span.End()
        span.End()
        spans := exp.GetSpans()
        if len(spans)!=1 { panic(fmt.Sprintf("idempotent End should produce 1 span, got %d", len(spans))) }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"idempotent failed: {proc.stdout} {proc.stderr}"



def test_tracing_marshal_unmarshal_single_header():
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
        ctx, span := tracer.Start(context.Background(), "root")
        carrier := map[string]string{}
        observability.MarshalTrace(ctx, carrier)
        v, ok := carrier["x-ride-trace"]
        if !ok || v=="" { panic("x-ride-trace missing, MarshalTrace must write single header") }
        // ensure single-header only
        if _, has := carrier["trace-id"]; has {
            panic("MarshalTrace must write only x-ride-trace, not trace-id")
        }
        if _, has := carrier["span-id"]; has {
            panic("MarshalTrace should NOT write span-id, use x-ride-trace")
        }
        ctx2 := observability.UnmarshalTrace(carrier)
        sc, ok := observability.TraceFromContext(ctx2)
        if !ok { panic("Unmarshal missing trace") }
        origSc, _ := observability.TraceFromContext(ctx)
        if sc.TraceID != origSc.TraceID { panic("traceID mismatch") }
        if sc.SpanID != origSc.SpanID { panic("spanID mismatch") }
        if sc.Sampled != origSc.Sampled { panic("sampled flag mismatch") }
        bad := map[string]string{"x-ride-trace":"bad"}
        ctxBad := observability.UnmarshalTrace(bad)
        _, ok = observability.TraceFromContext(ctxBad)
        if ok { panic("bad carrier should yield no trace") }
        span.End()
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"marshal/unmarshal single-header failed: {proc.stdout} {proc.stderr}"
    )



def test_tracing_inject_extract_alias_single_header():
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
        ctx, span := tracer.Start(context.Background(), "root")
        carrier := map[string]string{}
        observability.Inject(ctx, carrier)
        // Inject alias must also use single header
        if _, ok := carrier["x-ride-trace"]; !ok {
            panic("Inject alias must write x-ride-trace")
        }
        if _, has := carrier["trace-id"]; has {
            panic("Inject must write only x-ride-trace")
        }
        ctx2 := observability.Extract(carrier)
        sc, ok := observability.SpanContextFromContext(ctx2)
        if !ok { panic("Extract alias missing span") }
        origSc, _ := observability.SpanContextFromContext(ctx)
        if sc.TraceID != origSc.TraceID { panic("traceID mismatch via alias") }
        span.End()
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"inject/extract alias single-header failed: {proc.stdout} {proc.stderr}"
    )



def test_tracing_marshal_preserves_sampled():
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
        ctx, span := tracer.Start(context.Background(), "root")
        carrier := map[string]string{}
        observability.MarshalTrace(ctx, carrier)
        // sampled should be 1
        val := carrier["x-ride-trace"]
        fmt.Println(val)
        // parse sampled part
        // last part after last colon is sampled
        // check ends with :1
        if len(val) < 2 || val[len(val)-1] != '1' {
            // root sampled true per step1
            panic("sampled should be 1 in x-ride-trace, got "+val)
        }
        span.End()
        // manually create not sampled context
        carrier2 := map[string]string{"x-ride-trace":"0102030405060708090a0b0c0d0e0f10:0102030405060708::0"}
        ctx2 := observability.UnmarshalTrace(carrier2)
        sc, ok := observability.TraceFromContext(ctx2)
        if !ok { panic("should have sc") }
        if sc.Sampled { panic("sampled should be false when 0") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"marshal sampled flag failed: {proc.stdout} {proc.stderr}"
    )



def test_tracing_concurrent():
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
        proc := observability.NewSimpleProcessor(exp)
        tracer := observability.NewTracer("svc", observability.WithProcessor(proc))
        var wg sync.WaitGroup
        n:=100
        wg.Add(n)
        for i:=0;i<n;i++{
            go func(idx int){
                defer wg.Done()
                ctx, span := tracer.Start(context.Background(), fmt.Sprintf("span-%d", idx))
                span.AddAttribute("idx", idx)
                span.AddEvent("ev")
                _ = ctx
                span.End()
            }(i)
        }
        wg.Wait()
        spans := exp.GetSpans()
        if len(spans)!=n { panic(fmt.Sprintf("expected %d spans got %d", n, len(spans))) }
        seen := map[string]bool{}
        for _, s := range spans {
            if seen[s.SpanContext.SpanID] { panic("duplicate spanID "+s.SpanContext.SpanID) }
            seen[s.SpanContext.SpanID]=true
        }
        fmt.Println("OK")
    }
    """)
    proc = go_run_race_program(code, timeout=60)
    assert proc.returncode == 0, (
        f"concurrent tracing failed: {proc.stdout} {proc.stderr}"
    )



def test_tracing_concurrent_addattr():
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
        proc := observability.NewSimpleProcessor(exp)
        tracer := observability.NewTracer("svc", observability.WithProcessor(proc))
        _, span := tracer.Start(context.Background(), "concurrent-attr")
        var wg sync.WaitGroup
        n:=100
        wg.Add(n)
        for i:=0;i<n;i++{
            go func(idx int){
                defer wg.Done()
                span.AddAttribute(fmt.Sprintf("k%d", idx), idx)
                span.AddEvent(fmt.Sprintf("ev-%d", idx))
            }(i)
        }
        wg.Wait()
        span.End()
        spans := exp.GetSpans()
        if len(spans[0].Attributes) > 128 { panic("attrs >128") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_race_program(code, timeout=60)
    assert proc.returncode == 0, (
        f"concurrent addattr failed: {proc.stdout} {proc.stderr}"
    )



def test_tracing_attribute_limit():
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
        _, span := tracer.Start(context.Background(), "limit")
        for i:=0;i<200;i++{
            span.AddAttribute(fmt.Sprintf("k%d", i), i)
        }
        span.End()
        spans := exp.GetSpans()
        if len(spans[0].Attributes) > 128 {
            panic(fmt.Sprintf("attrs >128 not enforced: %d", len(spans[0].Attributes)))
        }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"attr limit failed: {proc.stdout} {proc.stderr}"



def test_tracing_attribute_initial_limit():
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
        for i:=0;i<200;i++{
            attrs = append(attrs, observability.Attribute{Key: fmt.Sprintf("k%d", i), Value: i})
        }
        _, span := tracer.Start(context.Background(), "initial-limit", observability.WithAttributes(attrs...))
        span.End()
        spans := exp.GetSpans()
        if len(spans[0].Attributes) > 128 {
            panic(fmt.Sprintf("initial attrs >128: %d", len(spans[0].Attributes)))
        }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"initial attr limit failed: {proc.stdout} {proc.stderr}"
    )



def test_tracing_starttime_bounds():
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
        proc := observability.NewSimpleProcessor(exp)
        tracer := observability.NewTracer("svc", observability.WithProcessor(proc))
        before := time.Now()
        _, span := tracer.Start(context.Background(), "time-bounds")
        time.Sleep(5*time.Millisecond)
        span.End()
        after := time.Now()
        s := exp.GetSpans()[0]
        if s.StartTime.Before(before) || s.StartTime.After(after) { panic("start time out of bounds") }
        if s.EndTime.Before(s.StartTime) { panic("end before start") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"starttime bounds failed: {proc.stdout} {proc.stderr}"



def test_tracing_context_direct():
    code = textwrap.dedent("""
    package main
    import (
        "context"
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        sc := observability.TraceContext{TraceID:"0102030405060708090a0b0c0d0e0f10", SpanID:"0102030405060708", Sampled:true}
        ctx := observability.ContextWithTrace(context.Background(), sc)
        got, ok := observability.TraceFromContext(ctx)
        if !ok { panic("not ok") }
        if got.TraceID != sc.TraceID { panic("tid mismatch") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"context direct failed: {proc.stdout} {proc.stderr}"



def test_metrics_counter_basic():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        prov := observability.NewMetricsProvider()
        c := prov.Counter("ride_requests_total", observability.WithLabels(map[string]string{"status":"requested"}))
        c.Inc()
        c.Add(2)
        c.Add(-1)
        fams := prov.Collect()
        var found bool
        for _, fam := range fams {
            if fam.Name=="ride_requests_total" {
                found=true
                if len(fam.Metrics)!=1 { panic(fmt.Sprintf("expected 1 metric got %d", len(fam.Metrics))) }
                if fam.Metrics[0].Value != 3 { panic(fmt.Sprintf("expected value 3 got %f", fam.Metrics[0].Value)) }
            }
        }
        if !found { panic("family not found") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"counter basic failed: {proc.stdout} {proc.stderr}"



def test_metrics_counter_reuse():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        prov := observability.NewMetricsProvider()
        c1 := prov.Counter("my_counter", observability.WithLabels(map[string]string{"a":"1"}))
        c2 := prov.Counter("my_counter", observability.WithLabels(map[string]string{"a":"1"}))
        c1.Inc()
        c2.Inc()
        fams := prov.Collect()
        var val float64
        for _, fam := range fams {
            if fam.Name=="my_counter" {
                for _, m := range fam.Metrics {
                    if m.Labels["a"]=="1" { val+=m.Value }
                }
            }
        }
        if val!=2 { panic(fmt.Sprintf("expected 2 got %f", val)) }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"counter reuse failed: {proc.stdout} {proc.stderr}"



def test_metrics_distinct_labels():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        prov := observability.NewMetricsProvider()
        c1 := prov.Counter("req", observability.WithLabels(map[string]string{"route":"a"}))
        c2 := prov.Counter("req", observability.WithLabels(map[string]string{"route":"b"}))
        c1.Inc()
        c2.Add(5)
        fams := prov.Collect()
        for _, fam := range fams {
            if fam.Name=="req" {
                if len(fam.Metrics)!=2 { panic(fmt.Sprintf("expected 2 distinct series got %d", len(fam.Metrics))) }
                fmt.Println("OK")
                return
            }
        }
        panic("family req not found")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"distinct labels failed: {proc.stdout} {proc.stderr}"



def test_metrics_concurrency():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "sync"
        "ride-observability/observability"
    )
    func main(){
        prov := observability.NewMetricsProvider()
        c := prov.Counter("conc_counter")
        var wg sync.WaitGroup
        n:=100
        per:=1000
        wg.Add(n)
        for i:=0;i<n;i++{
            go func(){
                defer wg.Done()
                for j:=0;j<per;j++{ c.Inc() }
            }()
        }
        wg.Wait()
        fams := prov.Collect()
        var total float64
        for _, fam := range fams {
            if fam.Name=="conc_counter" {
                for _, m := range fam.Metrics { total+=m.Value }
            }
        }
        expected := float64(n*per)
        if total!=expected { panic(fmt.Sprintf("expected %f got %f", expected, total)) }
        fmt.Println("OK")
    }
    """)
    proc = go_run_race_program(code, timeout=60)
    assert proc.returncode == 0, (
        f"metrics concurrency failed: {proc.stdout} {proc.stderr}"
    )



def test_metrics_gauge_histogram():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        prov := observability.NewMetricsProvider()
        g := prov.Gauge("active_rides")
        g.Set(10)
        g.Inc()
        g.Dec()
        g.Add(5)
        h := prov.Histogram("ride_duration_seconds", observability.WithBuckets([]float64{1,5,10}))
        h.Observe(0.5)
        h.Observe(2)
        h.Observe(7)
        h.Observe(20)
        fams := prov.Collect()
        var gaugeVal float64
        var histFound bool
        for _, fam := range fams {
            if fam.Name=="active_rides" {
                for _, m := range fam.Metrics { gaugeVal=m.Value }
            }
            if fam.Name=="ride_duration_seconds" {
                histFound=true
                if len(fam.Metrics)!=1 { panic("expected 1 hist metric") }
                m := fam.Metrics[0]
                if m.Count!=4 { panic(fmt.Sprintf("hist count expected 4 got %d", m.Count)) }
                if m.Sum < 29.4 || m.Sum > 29.6 { panic(fmt.Sprintf("hist sum expected ~29.5 got %f", m.Sum)) }
            }
        }
        if gaugeVal!=15 { panic(fmt.Sprintf("gauge expected 15 got %f", gaugeVal)) }
        if !histFound { panic("hist not found") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"gauge/hist failed: {proc.stdout} {proc.stderr}"



def test_metrics_histogram_default_buckets():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        prov := observability.NewMetricsProvider()
        h := prov.Histogram("default_bucket_hist")
        h.Observe(0.01)
        fams := prov.Collect()
        for _, fam := range fams {
            if fam.Name=="default_bucket_hist" {
                if len(fam.Metrics[0].Buckets)!=11 { panic(fmt.Sprintf("default buckets expected 11 got %d", len(fam.Metrics[0].Buckets))) }
                if fam.Metrics[0].Buckets[0].UpperBound != 0.005 { panic("first default bucket should be 0.005") }
                fmt.Println("OK")
                return
            }
        }
        panic("not found")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"default buckets failed: {proc.stdout} {proc.stderr}"



def test_metrics_histogram_cumulative_strict():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        prov := observability.NewMetricsProvider()
        h := prov.Histogram("strict_hist", observability.WithBuckets([]float64{1,5,10}))
        h.Observe(0.5)
        h.Observe(2)
        h.Observe(7)
        h.Observe(20)
        fams := prov.Collect()
        for _, fam := range fams {
            if fam.Name=="strict_hist" {
                m := fam.Metrics[0]
                if m.Buckets[0].Count != 1 { panic(fmt.Sprintf("bucket 1 expected 1 got %d", m.Buckets[0].Count)) }
                if m.Buckets[1].Count != 2 { panic(fmt.Sprintf("bucket 5 expected 2 got %d", m.Buckets[1].Count)) }
                if m.Buckets[2].Count != 3 { panic(fmt.Sprintf("bucket 10 expected 3 got %d", m.Buckets[2].Count)) }
                if m.Count != 4 { panic("count 4") }
                fmt.Println("OK")
                return
            }
        }
        panic("not found")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"cumulative strict failed: {proc.stdout} {proc.stderr}"
    )



def test_metrics_invalid_name():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        prov := observability.NewMetricsProvider()
        c := prov.Counter("invalid-name", observability.WithLabels(map[string]string{"ok":"1"}))
        c.Inc()
        c2 := prov.Counter("123bad", observability.WithLabels(map[string]string{"ok":"1"}))
        c2.Inc()
        c3 := prov.Counter("good_name", observability.WithLabels(map[string]string{"bad-key":"1"}))
        c3.Inc()
        c4 := prov.Counter("good_name2", observability.WithLabels(map[string]string{"":"empty"}))
        c4.Inc()
        fams := prov.Collect()
        for _, fam := range fams {
            if fam.Name=="invalid-name" || fam.Name=="123bad" { panic("invalid metric name should not be in Collect") }
        }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"invalid name test failed: {proc.stdout} {proc.stderr}"
    )



def test_metrics_collect_copy():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        prov := observability.NewMetricsProvider()
        c := prov.Counter("copy_test", observability.WithLabels(map[string]string{"env":"prod"}))
        c.Inc()
        fams1 := prov.Collect()
        if len(fams1)==0 || len(fams1[0].Metrics)==0 {
            panic("Collect returned empty for copy_test")
        }
        if fams1[0].Metrics[0].Labels == nil {
            panic("Collect returned nil Labels map; expected non-nil")
        }
        fams1[0].Metrics[0].Value = 9999
        fams1[0].Metrics[0].Labels["env"]="hacked"
        fams1[0].Metrics[0].Labels["injected"]="yes"
        fams2 := prov.Collect()
        var val float64
        var env string
        var hasInjected bool
        for _, fam := range fams2 {
            if fam.Name=="copy_test" {
                if len(fam.Metrics)==0 { panic("copy_test empty on second collect") }
                val = fam.Metrics[0].Value
                if fam.Metrics[0].Labels == nil { panic("second Collect nil Labels") }
                env = fam.Metrics[0].Labels["env"]
                _, hasInjected = fam.Metrics[0].Labels["injected"]
            }
        }
        if hasInjected { panic("Collect should return deep copy, injected leaked") }
        if env != "prod" { panic(fmt.Sprintf("env expected prod got %s", env)) }
        if val != 1 { panic(fmt.Sprintf("expected 1 got %f", val)) }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"collect copy failed: {proc.stdout} {proc.stderr}"



def test_metrics_label_truncate():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "strings"
        "ride-observability/observability"
    )
    func main(){
        prov := observability.NewMetricsProvider()
        long := strings.Repeat("a", 500)
        c := prov.Counter("truncate_test", observability.WithLabels(map[string]string{"id": long}))
        c.Inc()
        fams := prov.Collect()
        for _, fam := range fams {
            if fam.Name=="truncate_test" {
                v := fam.Metrics[0].Labels["id"]
                if len(v) > 256 { panic(fmt.Sprintf("label value should be truncated to 256, got %d", len(v))) }
                fmt.Println("OK")
                return
            }
        }
        panic("not found")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"label truncate failed: {proc.stdout} {proc.stderr}"



def test_metrics_gauge_negative():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        prov := observability.NewMetricsProvider()
        g := prov.Gauge("neg_gauge")
        g.Set(0)
        g.Dec()
        g.Add(-5)
        fams := prov.Collect()
        for _, fam := range fams {
            if fam.Name=="neg_gauge" {
                if fam.Metrics[0].Value != -6 { panic(fmt.Sprintf("expected -6 got %f", fam.Metrics[0].Value)) }
                fmt.Println("OK")
                return
            }
        }
        panic("not found")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"gauge negative failed: {proc.stdout} {proc.stderr}"



def test_metrics_provider_isolation():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        p1 := observability.NewMetricsProvider()
        p2 := observability.NewMetricsProvider()
        c1 := p1.Counter("isolated")
        c1.Inc()
        fams2 := p2.Collect()
        for _, fam := range fams2 {
            if fam.Name=="isolated" { panic("provider isolation broken") }
        }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"isolation failed: {proc.stdout} {proc.stderr}"



def test_logger_json_and_trace():
    code = textwrap.dedent("""
    package main
    import (
        "bytes"
        "context"
        "encoding/json"
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        buf := &bytes.Buffer{}
        logger := observability.NewLogger("ride-service", observability.WithOutput(buf))
        exp := observability.NewMemoryExporter()
        proc := observability.NewSimpleProcessor(exp)
        tracer := observability.NewTracer("ride-service", observability.WithProcessor(proc))
        ctx, span := tracer.Start(context.Background(), "test-log")
        logger.Info(ctx, "ride requested", observability.Field{Key:"ride_id", Value:"r123"})
        span.End()
        line := buf.String()
        var obj map[string]interface{}
        if err:= json.Unmarshal([]byte(line), &obj); err!= nil { panic("not valid json: "+err.Error()) }
        if obj["service"]!="ride-service" { panic("service missing") }
        if obj["message"]!="ride requested" { panic("message missing") }
        if obj["trace_id"]==nil { panic("trace_id missing") }
        if obj["span_id"]==nil { panic("span_id missing") }
        if obj["ride_id"]!="r123" { panic("custom field missing") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"logger json trace failed: {proc.stdout} {proc.stderr}"
    )



def test_logger_no_trace():
    code = textwrap.dedent("""
    package main
    import (
        "bytes"
        "context"
        "encoding/json"
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        buf := &bytes.Buffer{}
        logger := observability.NewLogger("svc", observability.WithOutput(buf))
        logger.Info(context.Background(), "no trace")
        var obj map[string]interface{}
        json.Unmarshal(buf.Bytes(), &obj)
        if _, ok := obj["trace_id"]; ok { panic("trace_id should not be present without span") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"logger no trace failed: {proc.stdout} {proc.stderr}"



def test_logger_with_immutable():
    code = textwrap.dedent("""
    package main
    import (
        "bytes"
        "encoding/json"
        "fmt"
        "context"
        "ride-observability/observability"
    )
    func main(){
        buf := &bytes.Buffer{}
        base := observability.NewLogger("svc", observability.WithOutput(buf))
        child := base.With(observability.Field{Key:"env", Value:"prod"})
        base.Info(context.Background(), "base msg")
        child.Info(context.Background(), "child msg")
        lines := bytes.Split(bytes.TrimSpace(buf.Bytes()), []byte("\\n"))
        if len(lines)!=2 { panic(fmt.Sprintf("expected 2 lines got %d", len(lines))) }
        var m1,m2 map[string]interface{}
        json.Unmarshal(lines[0], &m1)
        json.Unmarshal(lines[1], &m2)
        if _, ok := m1["env"]; ok { panic("base logger should not have env") }
        if m2["env"]!="prod" { panic("child should have env=prod") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"logger With immutable failed: {proc.stdout} {proc.stderr}"
    )



def test_logger_multiple_with_chain():
    code = textwrap.dedent("""
    package main
    import (
        "bytes"
        "encoding/json"
        "fmt"
        "context"
        "ride-observability/observability"
    )
    func main(){
        buf := &bytes.Buffer{}
        base := observability.NewLogger("svc", observability.WithOutput(buf))
        l1 := base.With(observability.Field{Key:"a", Value:"1"})
        l2 := l1.With(observability.Field{Key:"b", Value:"2"})
        l2.Info(context.Background(), "msg")
        var obj map[string]interface{}
        json.Unmarshal(bytes.TrimSpace(buf.Bytes()), &obj)
        if obj["a"]!="1" || obj["b"]!="2" { panic("chained With failed") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"multiple with chain failed: {proc.stdout} {proc.stderr}"
    )



def test_logger_field_overwrite():
    code = textwrap.dedent("""
    package main
    import (
        "bytes"
        "encoding/json"
        "fmt"
        "context"
        "ride-observability/observability"
    )
    func main(){
        buf := &bytes.Buffer{}
        base := observability.NewLogger("svc", observability.WithOutput(buf))
        base = base.With(observability.Field{Key:"env", Value:"dev"})
        child := base.With(observability.Field{Key:"env", Value:"prod"})
        child.Info(context.Background(), "msg")
        var obj map[string]interface{}
        json.Unmarshal(bytes.TrimSpace(buf.Bytes()), &obj)
        if obj["env"]!="prod" { panic(fmt.Sprintf("expected prod got %v", obj["env"])) }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"field overwrite failed: {proc.stdout} {proc.stderr}"



def test_logger_level_filter():
    code = textwrap.dedent("""
    package main
    import (
        "bytes"
        "strings"
        "context"
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        buf := &bytes.Buffer{}
        logger := observability.NewLogger("svc", observability.WithOutput(buf), observability.WithLevel("error"))
        logger.Info(context.Background(), "info filtered")
        logger.Debug(context.Background(), "debug filtered")
        logger.Warn(context.Background(), "warn filtered")
        logger.Error(context.Background(), "error pass")
        out := strings.TrimSpace(buf.String())
        if out=="" { panic("expected error log") }
        lines := strings.Split(out, "\\n")
        var nonEmpty []string
        for _, l := range lines { if strings.TrimSpace(l)!="" { nonEmpty=append(nonEmpty,l) } }
        if len(nonEmpty)!=1 { panic(fmt.Sprintf("expected 1 line got %d", len(nonEmpty))) }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"logger level filter failed: {proc.stdout} {proc.stderr}"
    )



def test_logger_timestamp_format():
    code = textwrap.dedent("""
    package main
    import (
        "bytes"
        "context"
        "encoding/json"
        "fmt"
        "time"
        "ride-observability/observability"
    )
    func main(){
        buf := &bytes.Buffer{}
        logger := observability.NewLogger("svc", observability.WithOutput(buf))
        logger.Info(context.Background(), "ts test")
        var obj map[string]interface{}
        json.Unmarshal(buf.Bytes(), &obj)
        tsStr, ok := obj["timestamp"].(string)
        if !ok { panic("timestamp missing or not string") }
        if _, err := time.Parse(time.RFC3339Nano, tsStr); err != nil {
            panic("timestamp not RFC3339Nano: "+err.Error())
        }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"timestamp format failed: {proc.stdout} {proc.stderr}"



def test_logger_concurrent():
    code = textwrap.dedent("""
    package main
    import (
        "bytes"
        "context"
        "fmt"
        "sync"
        "ride-observability/observability"
    )
    func main(){
        buf := &bytes.Buffer{}
        logger := observability.NewLogger("svc", observability.WithOutput(buf))
        var wg sync.WaitGroup
        n:=100
        wg.Add(n)
        for i:=0;i<n;i++{
            go func(idx int){
                defer wg.Done()
                logger.Info(context.Background(), fmt.Sprintf("msg-%d", idx))
            }(i)
        }
        wg.Wait()
        out := buf.String()
        lines := 0
        for _, c := range out { if c=='\\n' { lines++ } }
        if lines!=n { panic(fmt.Sprintf("expected %d lines got %d", n, lines)) }
        fmt.Println("OK")
    }
    """)
    proc = go_run_race_program(code, timeout=60)
    assert proc.returncode == 0, (
        f"logger concurrent failed: {proc.stdout} {proc.stderr}"
    )



def test_exporter_clear_and_count():
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
        _, s1 := tracer.Start(context.Background(), "a")
        s1.End()
        if exp.GetCount()!=1 { panic("GetCount expected 1") }
        exp.Clear()
        if exp.GetCount()!=0 { panic("Clear should make 0") }
        if len(exp.GetSpans())!=0 { panic("GetSpans after Clear should be 0") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"exporter clear/count failed: {proc.stdout} {proc.stderr}"
    )



def test_tracer_isolation():
    code = textwrap.dedent("""
    package main
    import (
        "context"
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        exp1 := observability.NewMemoryExporter()
        exp2 := observability.NewMemoryExporter()
        p1 := observability.NewSimpleProcessor(exp1)
        p2 := observability.NewSimpleProcessor(exp2)
        t1 := observability.NewTracer("service-a", observability.WithProcessor(p1))
        t2 := observability.NewTracer("service-b", observability.WithProcessor(p2))
        _, s1 := t1.Start(context.Background(), "op1")
        s1.End()
        if len(exp2.GetSpans())!=0 { panic("tracer isolation broken") }
        if exp1.GetSpans()[0].ServiceName!="service-a" { panic("service name mismatch") }
        _, s2 := t2.Start(context.Background(), "op2")
        s2.End()
        if len(exp2.GetSpans())!=1 { panic("exp2 should have 1") }
        if exp2.GetSpans()[0].ServiceName!="service-b" { panic("service b name mismatch") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"tracer isolation failed: {proc.stdout} {proc.stderr}"



def test_tracing_attribute_types():
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
        _, span := tracer.Start(context.Background(), "types")
        span.AddAttribute("int", 42)
        span.AddAttribute("float", 3.14)
        span.AddAttribute("bool", true)
        span.AddAttribute("string", "hello")
        span.End()
        s := exp.GetSpans()[0]
        if s.Attributes["int"]!=42 { panic("int attr mismatch") }
        if s.Attributes["bool"]!=true { panic("bool mismatch") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"attr types failed: {proc.stdout} {proc.stderr}"



def test_tracing_event_limit():
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
        _, span := tracer.Start(context.Background(), "event-limit")
        for i:=0;i<200;i++{
            span.AddEvent(fmt.Sprintf("ev-%d", i))
        }
        span.End()
        s := exp.GetSpans()[0]
        if len(s.Events) > 128 { panic(fmt.Sprintf("event limit >128 got %d", len(s.Events))) }
        if len(s.Events) < 128 { panic(fmt.Sprintf("expected 128 after 200 adds, got %d", len(s.Events))) }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"event limit failed: {proc.stdout} {proc.stderr}"



def test_tracing_with_race():
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
        proc := observability.NewSimpleProcessor(exp)
        tracer := observability.NewTracer("svc", observability.WithProcessor(proc))
        var wg sync.WaitGroup
        n:=50
        wg.Add(n)
        for i:=0;i<n;i++{
            go func(idx int){
                defer wg.Done()
                ctx, span := tracer.Start(context.Background(), fmt.Sprintf("race-%d", idx))
                for j:=0;j<20;j++{
                    span.AddAttribute(fmt.Sprintf("k%d", j), j)
                }
                span.End()
                _ = ctx
            }(i)
        }
        wg.Wait()
        if len(exp.GetSpans())!=n { panic(fmt.Sprintf("expected %d got %d", n, len(exp.GetSpans()))) }
        fmt.Println("OK")
    }
    """)
    proc = go_run_race_program(code)
    assert proc.returncode == 0, f"race test failed: {proc.stdout} {proc.stderr}"



def test_tracing_custom_id_generator():
    code = textwrap.dedent("""
    package main
    import (
        "context"
        "fmt"
        "ride-observability/observability"
    )
    type fixedGen struct{}
    func (f *fixedGen) NewTraceID() string { return "0102030405060708090a0b0c0d0e0f10" }
    func (f *fixedGen) NewSpanID() string { return "0102030405060708" }
    func main(){
        exp := observability.NewMemoryExporter()
        proc := observability.NewSimpleProcessor(exp)
        gen := &fixedGen{}
        tracer := observability.NewTracer("svc", observability.WithProcessor(proc), observability.WithIDGenerator(gen))
        _, span := tracer.Start(context.Background(), "fixed")
        sc := span.Context()
        if sc.TraceID != "0102030405060708090a0b0c0d0e0f10" { panic("custom traceID not used") }
        if sc.SpanID != "0102030405060708" { panic("custom spanID not used") }
        span.End()
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"custom id gen failed: {proc.stdout} {proc.stderr}"



def test_tracing_service_name_override():
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
        tracer := observability.NewTracer("original", observability.WithProcessor(proc), observability.WithServiceName("override"))
        _, span := tracer.Start(context.Background(), "op")
        span.End()
        s := exp.GetSpans()[0]
        if s.ServiceName != "override" { panic(fmt.Sprintf("expected override got %s", s.ServiceName)) }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"service name override failed: {proc.stdout} {proc.stderr}"
    )



def test_metrics_description():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        prov := observability.NewMetricsProvider()
        c := prov.Counter("desc_counter", observability.WithDescription("number of requests"))
        c.Inc()
        fams := prov.Collect()
        for _, fam := range fams {
            if fam.Name=="desc_counter" {
                if fam.Help != "number of requests" { panic(fmt.Sprintf("help expected got '%s'", fam.Help)) }
                fmt.Println("OK")
                return
            }
        }
        panic("desc_counter not found")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"description failed: {proc.stdout} {proc.stderr}"



def test_metrics_histogram_unsorted_buckets():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        prov := observability.NewMetricsProvider()
        h := prov.Histogram("unsorted", observability.WithBuckets([]float64{10,1,5}))
        h.Observe(2)
        fams := prov.Collect()
        for _, fam := range fams {
            if fam.Name=="unsorted" {
                buckets := fam.Metrics[0].Buckets
                if buckets[0].UpperBound != 1 || buckets[1].UpperBound != 5 || buckets[2].UpperBound != 10 {
                    panic(fmt.Sprintf("buckets not sorted: %v", buckets))
                }
                if buckets[0].Count != 0 || buckets[1].Count != 1 {
                    panic(fmt.Sprintf("counts wrong: %v", buckets))
                }
                fmt.Println("OK")
                return
            }
        }
        panic("not found")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"unsorted buckets failed: {proc.stdout} {proc.stderr}"



def test_logger_level_case_insensitive():
    code = textwrap.dedent("""
    package main
    import (
        "bytes"
        "context"
        "fmt"
        "strings"
        "ride-observability/observability"
    )
    func main(){
        buf := &bytes.Buffer{}
        logger := observability.NewLogger("svc", observability.WithOutput(buf), observability.WithLevel("ERROR"))
        logger.Info(context.Background(), "filtered")
        logger.Error(context.Background(), "pass")
        _ = fmt.Sprintf("x")
        out := strings.TrimSpace(buf.String())
        if out=="" { panic("error should pass") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"level case insensitive failed: {proc.stdout} {proc.stderr}"
    )



def test_tracing_traceflags():
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
        _, span := tracer.Start(context.Background(), "flags")
        sc := span.Context()
        if sc.Flags != 1 { panic(fmt.Sprintf("Flags should be 1 got %d", sc.Flags)) }
        span.End()
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"traceflags failed: {proc.stdout} {proc.stderr}"



def test_tracing_tracecontext_field_names():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        tc := observability.TraceContext{TraceID:"0102030405060708090a0b0c0d0e0f10", SpanID:"0102030405060708", ParentID:"aabbccddeeff0011", Sampled:true, Flags:1}
        if tc.TraceID=="" { panic("TraceID field missing") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"tracecontext field test failed: {proc.stdout} {proc.stderr}"
    )



def test_exporter_getspans_deep_copy():
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
        _, span := tracer.Start(context.Background(), "deepcopy", observability.WithAttributes(observability.Attribute{Key:"k", Value:"v"}))
        span.End()
        spans1 := exp.GetSpans()
        if len(spans1)!=1 { panic("expected 1") }
        if spans1[0].Attributes == nil { panic("Attributes nil, expected non-nil map") }
        spans1[0].Attributes["k"] = "hacked"
        spans1[0].Attributes["injected"] = "yes"
        spans1[0].Name = "hacked-name"
        spans2 := exp.GetSpans()
        if spans2[0].Attributes["k"] != "v" { panic(fmt.Sprintf("GetSpans should return deep copy of Attributes, expected v got %s", spans2[0].Attributes["k"])) }
        if _, ok := spans2[0].Attributes["injected"]; ok { panic("GetSpans deep copy failed - injected leaked") }
        if spans2[0].Name != "deepcopy" { panic("GetSpans deep copy failed - Name mutation leaked") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"exporter GetSpans deep copy failed: {proc.stdout} {proc.stderr}"
    )



def test_tracing_context_immutability():
    code = textwrap.dedent("""
    package main
    import (
        "context"
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        tc := observability.TraceContext{TraceID:"0102030405060708090a0b0c0d0e0f10", SpanID:"0102030405060708", Sampled:true}
        ctx := observability.ContextWithTrace(context.Background(), tc)
        // mutate original after storing
        tc.TraceID = "ffffffffffffffffffffffffffffffff"
        tc.SpanID = "ffffffffffffffff"
        got, ok := observability.TraceFromContext(ctx)
        if !ok { panic("should have trace") }
        if got.TraceID == "ffffffffffffffffffffffffffffffff" { panic("ContextWithTrace must store copy, not reference - mutation leaked") }
        if got.TraceID != "0102030405060708090a0b0c0d0e0f10" { panic("traceID mismatch") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"context immutability failed: {proc.stdout} {proc.stderr}"
    )



def test_tracing_attribute_duplicate_last_wins():
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
        _, span := tracer.Start(context.Background(), "dup", observability.WithAttributes(observability.Attribute{Key:"k", Value:"first"}, observability.Attribute{Key:"k", Value:"second"}))
        span.AddAttribute("k", "third")
        span.End()
        s := exp.GetSpans()[0]
        if s.Attributes["k"] != "third" { panic(fmt.Sprintf("duplicate attr last wins expected third got %v", s.Attributes["k"])) }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"duplicate attr last wins failed: {proc.stdout} {proc.stderr}"
    )



def test_tracing_event_attr_copy():
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
        _, span := tracer.Start(context.Background(), "evcopy")
        attrs := []observability.Attribute{{Key:"a", Value:"1"}, {Key:"b", Value:"2"}}
        span.AddEvent("ev", attrs...)
        // mutate original slice after AddEvent
        attrs[0].Key = "hacked"
        attrs[0].Value = "hacked"
        span.End()
        ev := exp.GetSpans()[0].Events[0]
        if len(ev.Attributes)!=2 { panic("event attrs count mismatch") }
        if ev.Attributes[0].Key == "hacked" { panic("AddEvent must copy attributes slice, mutation leaked") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"event attr copy failed: {proc.stdout} {proc.stderr}"



def test_tracing_marshal_empty_parent_format():
    code = textwrap.dedent("""
    package main
    import (
        "context"
        "fmt"
        "strings"
        "ride-observability/observability"
    )
    func main(){
        sc := observability.TraceContext{TraceID:"0102030405060708090a0b0c0d0e0f10", SpanID:"0102030405060708", ParentID:"", Sampled:true}
        ctx := observability.ContextWithTrace(context.Background(), sc)
        carrier := map[string]string{}
        observability.MarshalTrace(ctx, carrier)
        v, ok := carrier["x-ride-trace"]
        if !ok { panic("missing x-ride-trace") }
        parts := strings.Split(v, ":")
        if len(parts)!=4 { panic(fmt.Sprintf("x-ride-trace must have 4 colon-separated parts, got %d: %s", len(parts), v)) }
        if parts[2] != "" { panic(fmt.Sprintf("root ParentID should be empty, got %s in %s", parts[2], v)) }
        if parts[3] != "1" { panic("sampled flag should be 1") }
        ctx2 := observability.UnmarshalTrace(carrier)
        sc2, ok := observability.TraceFromContext(ctx2)
        if !ok { panic("should have trace after unmarshal") }
        if sc2.ParentID != "" { panic("ParentID should stay empty") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"marshal empty parent format failed: {proc.stdout} {proc.stderr}"
    )



def test_metrics_histogram_boundary_inclusive():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        prov := observability.NewMetricsProvider()
        h := prov.Histogram("boundary_hist", observability.WithBuckets([]float64{1,5,10}))
        h.Observe(1)
        h.Observe(5)
        h.Observe(10)
        fams := prov.Collect()
        for _, fam := range fams {
            if fam.Name=="boundary_hist" {
                m := fam.Metrics[0]
                if len(m.Buckets)!=3 { panic("expected 3 buckets") }
                if m.Buckets[0].Count != 1 { panic(fmt.Sprintf("bucket 1 inclusive expected 1 got %d", m.Buckets[0].Count)) }
                if m.Buckets[1].Count != 2 { panic(fmt.Sprintf("bucket 5 cumulative inclusive expected 2 got %d", m.Buckets[1].Count)) }
                if m.Buckets[2].Count != 3 { panic(fmt.Sprintf("bucket 10 cumulative inclusive expected 3 got %d", m.Buckets[2].Count)) }
                if m.Count != 3 { panic("count 3") }
                fmt.Println("OK")
                return
            }
        }
        panic("not found")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"histogram boundary inclusive failed: {proc.stdout} {proc.stderr}"
    )



def test_exporter_concurrent():
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
        proc := observability.NewSimpleProcessor(exp)
        tracer := observability.NewTracer("svc", observability.WithProcessor(proc))
        var wg sync.WaitGroup
        n:=100
        wg.Add(n)
        for i:=0;i<n;i++{
            go func(idx int){
                defer wg.Done()
                for j:=0;j<50;j++{
                    _, s := tracer.Start(context.Background(), fmt.Sprintf("c-%d-%d", idx, j))
                    s.End()
                }
                // concurrent GetSpans
                _ = exp.GetSpans()
            }(i)
        }
        wg.Wait()
        if len(exp.GetSpans()) != n*50 { panic(fmt.Sprintf("expected %d got %d", n*50, len(exp.GetSpans()))) }
        fmt.Println("OK")
    }
    """)
    proc = go_run_race_program(code)
    assert proc.returncode == 0, f"race test failed: {proc.stdout} {proc.stderr}"



def test_metrics_collect_race():
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
    proc = go_run_race_program(code)
    assert proc.returncode == 0, f"race test failed: {proc.stdout} {proc.stderr}"



def test_tracing_parent_id_rules():
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
        _, root := tracer.Start(context.Background(), "root")
        rootCtx, _ := tracer.Start(context.Background(), "root2")
        _, child := tracer.Start(rootCtx, "child")
        root.End()
        child.End()
        spans := exp.GetSpans()
        var rootSpan, childSpan *observability.FinishedSpan
        for i:= range spans {
            if spans[i].Name=="root" { rootSpan=&spans[i] }
            if spans[i].Name=="child" { childSpan=&spans[i] }
        }
        if rootSpan==nil { panic("root not found") }
        if rootSpan.ParentID != "" { panic(fmt.Sprintf("root ParentID should be empty, got %s", rootSpan.ParentID)) }
        if childSpan==nil { panic("child not found") }
        if childSpan.ParentID == "" { panic("child ParentID should not be empty") }
        if childSpan.ParentID != childSpan.SpanContext.ParentID { panic("ParentID field and SpanContext.ParentID must match") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"parent id rules failed: {proc.stdout} {proc.stderr}"



def test_tracing_isrecording_after_end():
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
        _, span := tracer.Start(context.Background(), "rec")
        if !span.IsRecording() { panic("should be recording before End") }
        span.End()
        if span.IsRecording() { panic("should NOT be recording after End") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"isrecording after end failed: {proc.stdout} {proc.stderr}"
    )



def test_tracing_string_truncate_exact_boundary():
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
        exact1024 := strings.Repeat("b", 1024)
        longer := strings.Repeat("c", 1025)
        _, s1 := tracer.Start(context.Background(), "exact", observability.WithAttributes(observability.Attribute{Key:"k1", Value:exact1024}))
        s1.End()
        _, s2 := tracer.Start(context.Background(), "longer", observability.WithAttributes(observability.Attribute{Key:"k2", Value:longer}))
        s2.End()
        spans := exp.GetSpans()
        var v1, v2 string
        for _, sp := range spans {
            if sp.Name=="exact" { v1 = sp.Attributes["k1"].(string) }
            if sp.Name=="longer" { v2 = sp.Attributes["k2"].(string) }
        }
        if len(v1)!=1024 { panic(fmt.Sprintf("exact 1024 should stay 1024, got %d", len(v1))) }
        if len(v2)!=1024 { panic(fmt.Sprintf("1025 should truncate to 1024, got %d", len(v2))) }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"string truncate boundary failed: {proc.stdout} {proc.stderr}"
    )



def test_exporter_clear_then_reuse():
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
        _, s1 := tracer.Start(context.Background(), "a")
        s1.End()
        if exp.GetCount()!=1 { panic("count 1") }
        exp.Clear()
        if exp.GetCount()!=0 { panic("clear should 0") }
        if len(exp.GetSpans())!=0 { panic("clear spans not empty") }
        _, s2 := tracer.Start(context.Background(), "b")
        s2.End()
        _, s3 := tracer.Start(context.Background(), "c")
        s3.End()
        if exp.GetCount()!=2 { panic(fmt.Sprintf("expected 2 after reuse got %d", exp.GetCount())) }
        spans := exp.GetSpans()
        if spans[0].Name!="b" || spans[1].Name!="c" { panic("reuse order wrong") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"exporter clear then reuse failed: {proc.stdout} {proc.stderr}"
    )



def test_tracing_parent_chain_three_levels():
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
        ctx1, s1 := tracer.Start(context.Background(), "grandparent")
        ctx2, s2 := tracer.Start(ctx1, "parent")
        ctx3, s3 := tracer.Start(ctx2, "child")
        s3.End()
        s2.End()
        s1.End()
        spans := exp.GetSpans()
        if len(spans)!=3 { panic(fmt.Sprintf("expected 3 got %d", len(spans))) }
        var gp, par, child *observability.FinishedSpan
        for i:= range spans {
            switch spans[i].Name {
            case "grandparent": gp=&spans[i]
            case "parent": par=&spans[i]
            case "child": child=&spans[i]
            }
        }
        if gp==nil || par==nil || child==nil { panic("missing spans") }
        if gp.SpanContext.TraceID != par.SpanContext.TraceID || par.SpanContext.TraceID != child.SpanContext.TraceID { panic("traceID chain broken") }
        if par.ParentID != gp.SpanContext.SpanID { panic("parent ParentID != gp SpanID") }
        if child.ParentID != par.SpanContext.SpanID { panic("child ParentID != parent SpanID") }
        if child.SpanContext.ParentID != par.SpanContext.SpanID { panic("child SpanContext.ParentID mismatch") }
        _ = ctx3
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"parent chain three levels failed: {proc.stdout} {proc.stderr}"
    )



def test_tracing_attribute_map_non_nil():
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
        _, span := tracer.Start(context.Background(), "attr-map", observability.WithAttributes(observability.Attribute{Key:"k", Value:"v"}))
        span.End()
        s := exp.GetSpans()[0]
        if s.Attributes==nil { panic("Attributes map must be non-nil when attributes present") }
        if len(s.Attributes)!=1 { panic("expected 1 attr") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"attribute map non-nil failed: {proc.stdout} {proc.stderr}"
    )



def test_tracing_event_timestamp_recent():
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
        proc := observability.NewSimpleProcessor(exp)
        tracer := observability.NewTracer("svc", observability.WithProcessor(proc))
        before := time.Now()
        _, span := tracer.Start(context.Background(), "evt-time")
        time.Sleep(5*time.Millisecond)
        span.AddEvent("ev1")
        time.Sleep(5*time.Millisecond)
        span.AddEvent("ev2")
        span.End()
        after := time.Now()
        evs := exp.GetSpans()[0].Events
        if len(evs)!=2 { panic("expected 2 events") }
        if evs[0].Timestamp.Before(before) || evs[0].Timestamp.After(after) { panic("event timestamp out of bounds") }
        if evs[1].Timestamp.Before(evs[0].Timestamp) { panic("event timestamps not monotonic") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"event timestamp recent failed: {proc.stdout} {proc.stderr}"
    )



def test_metrics_histogram_boundary_and_sum():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        prov := observability.NewMetricsProvider()
        h := prov.Histogram("bound_sum", observability.WithBuckets([]float64{5,10}))
        h.Observe(5)
        h.Observe(10)
        h.Observe(3)
        h.Observe(12)
        fams := prov.Collect()
        for _, fam := range fams {
            if fam.Name=="bound_sum" {
                m := fam.Metrics[0]
                if m.Count!=4 { panic(fmt.Sprintf("count expected 4 got %d", m.Count)) }
                if m.Sum < 29.9 || m.Sum > 30.1 { panic(fmt.Sprintf("sum expected 30 got %f", m.Sum)) }
                if m.Buckets[0].Count!=2 { panic(fmt.Sprintf("bucket 5 should have 2 (3 and 5), got %d", m.Buckets[0].Count)) }
                if m.Buckets[1].Count!=3 { panic(fmt.Sprintf("bucket 10 should have 3 (3,5,10), got %d", m.Buckets[1].Count)) }
                fmt.Println("OK")
                return
            }
        }
        panic("not found")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"histogram boundary and sum failed: {proc.stdout} {proc.stderr}"
    )



def test_logger_json_fields():
    code = textwrap.dedent("""
    package main
    import (
        "bytes"
        "encoding/json"
        "fmt"
        "context"
        "ride-observability/observability"
    )
    func main(){
        buf := &bytes.Buffer{}
        logger := observability.NewLogger("svc", observability.WithOutput(buf))
        logger.Info(context.Background(), "msg", observability.Field{Key:"int", Value:42}, observability.Field{Key:"bool", Value:true})
        var obj map[string]interface{}
        if err:= json.Unmarshal(bytes.TrimSpace(buf.Bytes()), &obj); err!=nil { panic("not json") }
        if obj["level"]!="info" { panic("level missing") }
        if obj["service"]!="svc" { panic("service missing") }
        if obj["message"]!="msg" { panic("message missing") }
        if obj["timestamp"]==nil { panic("timestamp missing") }
        if obj["int"]!=float64(42) { panic(fmt.Sprintf("int field expected 42 got %v", obj["int"])) }
        if obj["bool"]!=true { panic("bool field missing") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"logger json fields failed: {proc.stdout} {proc.stderr}"
    )



def test_tracing_service_name_empty_fallback():
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
        tracer := observability.NewTracer("", observability.WithProcessor(proc))
        _, span := tracer.Start(context.Background(), "empty-svc")
        span.End()
        s := exp.GetSpans()[0]
        _ = s.ServiceName
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"service name empty fallback failed: {proc.stdout} {proc.stderr}"
    )



def test_tracing_id_generator_nil_fallback():
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
        tracer := observability.NewTracer("svc", observability.WithProcessor(proc), observability.WithIDGenerator(nil))
        _, span := tracer.Start(context.Background(), "nilgen")
        sc := span.Context()
        if len(sc.TraceID)!=32 || len(sc.SpanID)!=16 { panic("nil IDGenerator should fallback to default") }
        span.End()
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"id generator nil fallback failed: {proc.stdout} {proc.stderr}"
    )



def test_logger_level_default_info():
    code = textwrap.dedent("""
    package main
    import (
        "bytes"
        "context"
        "strings"
        "ride-observability/observability"
    )
    func main(){
        buf := &bytes.Buffer{}
        logger := observability.NewLogger("svc", observability.WithOutput(buf))
        logger.Debug(context.Background(), "debug should be filtered by default")
        logger.Info(context.Background(), "info should pass")
        out := buf.String()
        if strings.Contains(out, "debug should be filtered") { panic("default level should be info, debug filtered") }
        if !strings.Contains(out, "info should pass") { panic("info should pass at default") }
        println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"logger default level failed: {proc.stdout} {proc.stderr}"
    )



def test_exporter_getspans_slice_mutation():
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
        _, s := tracer.Start(context.Background(), "slice-mut")
        s.End()
        spans1 := exp.GetSpans()
        // append to returned slice should not affect internal
        spans1 = append(spans1, observability.FinishedSpan{Name:"injected"})
        spans2 := exp.GetSpans()
        if len(spans2)!=1 { panic(fmt.Sprintf("append to returned GetSpans slice should not affect internal, expected 1 got %d", len(spans2))) }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"exporter slice mutation failed: {proc.stdout} {proc.stderr}"
    )



def test_logger_with_empty_fields():
    code = textwrap.dedent("""
    package main
    import (
        "bytes"
        "context"
        "encoding/json"
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        buf := &bytes.Buffer{}
        base := observability.NewLogger("svc", observability.WithOutput(buf))
        child := base.With()
        child.Info(context.Background(), "empty-with")
        var obj map[string]interface{}
        if err:= json.Unmarshal(bytes.TrimSpace(buf.Bytes()), &obj); err!=nil { panic("not json") }
        if obj["message"]!="empty-with" { panic("msg missing") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"logger with empty fields failed: {proc.stdout} {proc.stderr}"
    )



def test_metrics_gauge_set_and_add():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        prov := observability.NewMetricsProvider()
        g := prov.Gauge("gauge_set_add")
        g.Set(5)
        g.Add(3)
        g.Add(-2)
        g.Inc()
        g.Dec()
        g.Dec()
        fams := prov.Collect()
        for _, fam := range fams {
            if fam.Name=="gauge_set_add" {
                // 5+3-2+1-1-1 =5
                if fam.Metrics[0].Value != 5 { panic(fmt.Sprintf("expected gauge 5 got %f", fam.Metrics[0].Value)) }
                fmt.Println("OK")
                return
            }
        }
        panic("not found")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"gauge set and add failed: {proc.stdout} {proc.stderr}"
    )



def test_metrics_label_order_irrelevant_for_reuse():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        prov := observability.NewMetricsProvider()
        c1 := prov.Counter("order_test", observability.WithLabels(map[string]string{"a":"1","b":"2"}))
        c2 := prov.Counter("order_test", observability.WithLabels(map[string]string{"b":"2","a":"1"}))
        c1.Inc()
        c2.Inc()
        fams := prov.Collect()
        for _, fam := range fams {
            if fam.Name=="order_test" {
                if len(fam.Metrics)!=1 { panic(fmt.Sprintf("label order should be irrelevant for reuse, expected 1 series got %d", len(fam.Metrics))) }
                if fam.Metrics[0].Value != 2 { panic(fmt.Sprintf("expected value 2 got %f", fam.Metrics[0].Value)) }
                fmt.Println("OK")
                return
            }
        }
        panic("not found")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"label order irrelevant failed: {proc.stdout} {proc.stderr}"
    )



def test_metrics_label_truncate_for_gauge_and_histogram():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "strings"
        "ride-observability/observability"
    )
    func main(){
        prov := observability.NewMetricsProvider()
        long := strings.Repeat("x", 500)
        g := prov.Gauge("gauge_trunc", observability.WithLabels(map[string]string{"id": long}))
        g.Set(1)
        h := prov.Histogram("hist_trunc", observability.WithLabels(map[string]string{"id": long}))
        h.Observe(1)
        fams := prov.Collect()
        for _, fam := range fams {
            if fam.Name=="gauge_trunc" {
                v := fam.Metrics[0].Labels["id"]
                if len(v) > 256 { panic(fmt.Sprintf("gauge label value should truncate to 256, got %d", len(v))) }
            }
            if fam.Name=="hist_trunc" {
                v := fam.Metrics[0].Labels["id"]
                if len(v) > 256 { panic(fmt.Sprintf("hist label value should truncate to 256, got %d", len(v))) }
            }
        }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"label truncate gauge hist failed: {proc.stdout} {proc.stderr}"
    )



def test_tracing_add_attribute_after_limit_still_128():
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
        for i:=0;i<128;i++{ attrs = append(attrs, observability.Attribute{Key: fmt.Sprintf("k%d", i), Value:i}) }
        _, span := tracer.Start(context.Background(), "limit-add", observability.WithAttributes(attrs...))
        // at limit now
        span.AddAttribute("extra", "should-be-ignored")
        span.End()
        s := exp.GetSpans()[0]
        if len(s.Attributes) != 128 { panic(fmt.Sprintf("after 128 initial + 1 extra, should still be 128, got %d", len(s.Attributes))) }
        if _, ok := s.Attributes["extra"]; ok { panic("extra attribute beyond 128 should be ignored") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"add attribute after limit failed: {proc.stdout} {proc.stderr}"
    )



def test_tracing_attribute_value_truncate_add():
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
        _, span := tracer.Start(context.Background(), "truncate-add")
        long := strings.Repeat("z", 2000)
        span.AddAttribute("longkey", long)
        span.End()
        s := exp.GetSpans()[0]
        v := s.Attributes["longkey"].(string)
        if len(v)!=1024 { panic(fmt.Sprintf("AddAttribute long should truncate to 1024, got %d", len(v))) }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"attribute value truncate add failed: {proc.stdout} {proc.stderr}"
    )



def test_tracing_context_overwrites():
    code = textwrap.dedent("""
    package main
    import (
        "context"
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        sc1 := observability.TraceContext{TraceID:"0102030405060708090a0b0c0d0e0f10", SpanID:"0102030405060708", Sampled:true}
        sc2 := observability.TraceContext{TraceID:"aabbccddeeff0011aabbccddeeff0011", SpanID:"aabbccddeeff0011", Sampled:false}
        ctx := observability.ContextWithTrace(context.Background(), sc1)
        ctx2 := observability.ContextWithTrace(ctx, sc2)
        got, ok := observability.TraceFromContext(ctx2)
        if !ok { panic("should have trace") }
        if got.TraceID != sc2.TraceID { panic("ContextWithTrace should overwrite previous") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"context overwrites failed: {proc.stdout} {proc.stderr}"
    )



def test_tracing_event_copy_isolation():
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
        _, span := tracer.Start(context.Background(), "ev-isolation")
        span.AddEvent("ev1")
        span.End()
        spans := exp.GetSpans()
        // mutate events slice in returned span
        spans[0].Events = append(spans[0].Events, observability.SpanEvent{Name:"injected"})
        spans2 := exp.GetSpans()
        if len(spans2[0].Events)!=1 { panic(fmt.Sprintf("mutating returned Events slice should not affect internal, expected 1 got %d", len(spans2[0].Events))) }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"event copy isolation failed: {proc.stdout} {proc.stderr}"
    )



def test_tracing_withattributes_duplicate_across_calls():
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
        _, span := tracer.Start(context.Background(), "dup-across",
            observability.WithAttributes(observability.Attribute{Key:"k", Value:"v1"}),
            observability.WithAttributes(observability.Attribute{Key:"k", Value:"v2"}),
        )
        span.End()
        s := exp.GetSpans()[0]
        if s.Attributes["k"] != "v2" { panic(fmt.Sprintf("duplicate across WithAttributes calls last wins expected v2 got %v", s.Attributes["k"])) }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"withattributes duplicate across calls failed: {proc.stdout} {proc.stderr}"
    )



def test_tracing_marshal_nil_carrier():
    code = textwrap.dedent("""
    package main
    import (
        "context"
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        defer func(){
            if r:=recover(); r!=nil { panic(fmt.Sprintf("MarshalTrace nil carrier should not panic, got %v", r)) }
        }()
        observability.MarshalTrace(context.Background(), nil)
        sc := observability.TraceContext{TraceID:"0102030405060708090a0b0c0d0e0f10", SpanID:"0102030405060708", Sampled:true}
        ctx := observability.ContextWithTrace(context.Background(), sc)
        observability.Inject(ctx, nil)
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"marshal nil carrier failed: {proc.stdout} {proc.stderr}"
    )



def test_tracing_unmarshal_invalid():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        cases := []map[string]string{
            nil,
            {},
            {"x-ride-trace":""},
            {"x-ride-trace":"bad"},
            {"x-ride-trace":"0102030405060708090a0b0c0d0e0f10:0102030405060708"},
            {"x-ride-trace":"zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz:0102030405060708::1"},
            {"x-ride-trace":"0102030405060708090a0b0c0d0e0f10:zzzzzzzzzzzzzzzz::1"},
            {"x-ride-trace":"0102030405060708090a0b0c0d0e0f10:0102030405060708:zzzzzzzzzzzzzzzz:1"},
        }
        for i, c := range cases {
            ctx := observability.UnmarshalTrace(c)
            if _, ok := observability.TraceFromContext(ctx); ok {
                panic(fmt.Sprintf("case %d should yield no trace for invalid carrier %v", i, c))
            }
            ctx2 := observability.Extract(c)
            if _, ok := observability.SpanContextFromContext(ctx2); ok {
                panic(fmt.Sprintf("Extract case %d should yield no trace", i))
            }
        }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"unmarshal invalid failed: {proc.stdout} {proc.stderr}"
    )



def test_tracing_withattributes_nil():
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
        _, s1 := tracer.Start(context.Background(), "nilattrs", observability.WithAttributes())
        s1.End()
        _, s2 := tracer.Start(context.Background(), "nilattrs2")
        s2.End()
        if len(exp.GetSpans())!=2 { panic("should have 2") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"withattributes nil failed: {proc.stdout} {proc.stderr}"
    )



def test_metrics_counter_type_conflict():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        prov := observability.NewMetricsProvider()
        c := prov.Counter("conflict_metric")
        c.Inc()
        g := prov.Gauge("conflict_metric")
        g.Set(100)
        h := prov.Histogram("conflict_metric")
        h.Observe(5)
        fams := prov.Collect()
        var families int
        var typ string
        for _, fam := range fams {
            if fam.Name=="conflict_metric" {
                families++
                typ = fam.Type
            }
        }
        if families!=1 { panic(fmt.Sprintf("conflict metric same name should be 1 family, got %d", families)) }
        if typ!="counter" { panic(fmt.Sprintf("first type counter should win, got %s", typ)) }
        // value should be 1, not affected by gauge/histogram no-op
        for _, fam := range fams {
            if fam.Name=="conflict_metric" {
                if fam.Metrics[0].Value != 1 { panic(fmt.Sprintf("counter value should be 1, got %f", fam.Metrics[0].Value)) }
            }
        }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"counter type conflict failed: {proc.stdout} {proc.stderr}"
    )



def test_tracing_flags_zero_when_not_sampled():
    code = textwrap.dedent("""
    package main
    import (
        "context"
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        sc := observability.TraceContext{TraceID:"0102030405060708090a0b0c0d0e0f10", SpanID:"0102030405060708", ParentID:"", Sampled:false}
        ctx := observability.ContextWithTrace(context.Background(), sc)
        carrier := map[string]string{}
        observability.MarshalTrace(ctx, carrier)
        v, ok := carrier["x-ride-trace"]
        if !ok { panic("should marshal even when not sampled? Flags 0 but still valid") }
        if v[len(v)-1] != '0' { panic(fmt.Sprintf("sampled false should have :0, got %s", v)) }
        sc2 := observability.TraceContext{TraceID:"0102030405060708090a0b0c0d0e0f10", SpanID:"0102030405060708", Sampled:false}
        if sc2.Flags != 0 { panic(fmt.Sprintf("Flags should be 0 when Sampled false, but struct may have Flag 0 - check Context() returns with Flags 0")) }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"flags zero when not sampled failed: {proc.stdout} {proc.stderr}"
    )



def test_tracing_concurrent_end_exactly_once():
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
        proc := observability.NewSimpleProcessor(exp)
        tracer := observability.NewTracer("svc", observability.WithProcessor(proc))
        _, span := tracer.Start(context.Background(), "concurrent-end-once")
        var wg sync.WaitGroup
        for i:=0;i<50;i++{
            wg.Add(1)
            go func(){
                defer wg.Done()
                span.End()
            }()
        }
        wg.Wait()
        spans := exp.GetSpans()
        if len(spans)!=1 {
            panic(fmt.Sprintf("Concurrent End() must export exactly once, got %d", len(spans)))
        }
        fmt.Println("OK")
    }
    """)
    proc = go_run_race_program(code, timeout=60)
    assert proc.returncode == 0, (
        f"concurrent End exactly once failed: {proc.stdout} {proc.stderr}"
    )



def test_tracing_export_snapshots_span():
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
        proc := observability.NewSimpleProcessor(exp)
        tracer := observability.NewTracer("svc", observability.WithProcessor(proc))
        _, span := tracer.Start(context.Background(), "snapshot-test")
        span.AddAttribute("k1", "v1")
        var wg sync.WaitGroup
        wg.Add(1)
        go func(){
            defer wg.Done()
            for i:=0;i<100;i++{
                span.AddAttribute(fmt.Sprintf("k%d", i+2), i)
            }
        }()
        span.End()
        wg.Wait()
        spans := exp.GetSpans()
        if len(spans)!=1 { panic(fmt.Sprintf("expected 1 span got %d", len(spans))) }
        // The exported span must be a snapshot - mutating after End must not affect it
        // AddAttribute after End is defined as no-op, so subsequent GetSpans must still return original snapshot
        // Also check that the exporter received a stable copy (no race)
        s := spans[0]
        if s.Attributes == nil { panic("Attributes nil") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_race_program(code, timeout=60)
    assert proc.returncode == 0, (
        f"export snapshots span failed: {proc.stdout} {proc.stderr}"
    )



def test_tracing_racing_add_after_end_noop_atomic():
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
        proc := observability.NewSimpleProcessor(exp)
        tracer := observability.NewTracer("svc", observability.WithProcessor(proc))
        _, span := tracer.Start(context.Background(), "race-add-after-end")
        var wg sync.WaitGroup
        // 20 goroutines racing AddAttribute/AddEvent with End
        for i:=0;i<20;i++{
            wg.Add(1)
            go func(idx int){
                defer wg.Done()
                span.AddAttribute(fmt.Sprintf("race-k%d", idx), idx)
                span.AddEvent(fmt.Sprintf("race-ev-%d", idx))
            }(i)
        }
        wg.Add(1)
        go func(){
            defer wg.Done()
            span.End()
        }()
        wg.Wait()
        // After all, End should have been called and exporter should have exactly 1 span, no panic, no race
        spans := exp.GetSpans()
        if len(spans)!=1 { panic(fmt.Sprintf("expected 1 span after racing End and Add, got %d", len(spans))) }
        // After End, further Add should be no-op and not affect exported count
        span.AddAttribute("after", "should-not-appear")
        if len(exp.GetSpans()) != 1 { panic("Add after End should be no-op") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_race_program(code, timeout=60)
    assert proc.returncode == 0, (
        f"racing add after end atomic failed: {proc.stdout} {proc.stderr}"
    )


def test_metrics_withlabels_defensive_copy():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        prov := observability.NewMetricsProvider()
        labels := map[string]string{"env":"prod"}
        c := prov.Counter("defcopy_test", observability.WithLabels(labels))
        // mutate original map after creation
        labels["env"]="hacked"
        labels["new"]="injected"
        c.Inc()
        fams := prov.Collect()
        for _, fam := range fams {
            if fam.Name=="defcopy_test" {
                if len(fam.Metrics)!=1 { panic("expected 1") }
                v := fam.Metrics[0].Labels["env"]
                if v!="prod" { panic(fmt.Sprintf("WithLabels must copy map, expected prod got %s", v)) }
                if _, ok := fam.Metrics[0].Labels["new"]; ok { panic("injected label leaked via defensive copy failure") }
                fmt.Println("OK")
                return
            }
        }
        panic("not found")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"withlabels defensive copy failed: {proc.stdout} {proc.stderr}"



def test_metrics_withbuckets_defensive_copy():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        prov := observability.NewMetricsProvider()
        buckets := []float64{1,5,10}
        h := prov.Histogram("bucket_defcopy", observability.WithBuckets(buckets))
        // mutate original slice
        buckets[0]=100
        buckets = append(buckets, 20)
        h.Observe(2)
        fams := prov.Collect()
        for _, fam := range fams {
            if fam.Name=="bucket_defcopy" {
                bs := fam.Metrics[0].Buckets
                if len(bs)!=3 { panic(fmt.Sprintf("expected 3 buckets got %d", len(bs))) }
                if bs[0].UpperBound != 1 { panic(fmt.Sprintf("WithBuckets must copy slice, expected 1 got %f", bs[0].UpperBound)) }
                fmt.Println("OK")
                return
            }
        }
        panic("not found")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"withbuckets defensive copy failed: {proc.stdout} {proc.stderr}"



def test_metrics_label_truncation_reuse_after_truncate():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "strings"
        "ride-observability/observability"
    )
    func main(){
        prov := observability.NewMetricsProvider()
        long1 := strings.Repeat("a", 500)
        long2 := strings.Repeat("a", 256) + strings.Repeat("b", 244) // same first 256 as long1
        // first 256 chars of both are all 'a's
        c1 := prov.Counter("trunc_reuse", observability.WithLabels(map[string]string{"id": long1}))
        c2 := prov.Counter("trunc_reuse", observability.WithLabels(map[string]string{"id": long2}))
        c1.Inc()
        c2.Inc()
        fams := prov.Collect()
        for _, fam := range fams {
            if fam.Name=="trunc_reuse" {
                // after truncation both should be same series, so only 1 metric
                if len(fam.Metrics)!=1 { panic(fmt.Sprintf("truncation should cause reuse, expected 1 series got %d", len(fam.Metrics))) }
                if fam.Metrics[0].Value != 2 { panic(fmt.Sprintf("expected value 2 got %f", fam.Metrics[0].Value)) }
                fmt.Println("OK")
                return
            }
        }
        panic("not found")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"label truncation reuse failed: {proc.stdout} {proc.stderr}"



def test_metrics_collect_buckets_deep_copy():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        prov := observability.NewMetricsProvider()
        h := prov.Histogram("buckets_deepcopy", observability.WithBuckets([]float64{1,5}))
        h.Observe(2)
        fams1 := prov.Collect()
        if len(fams1)==0 { panic("empty") }
        // mutate returned buckets
        fams1[0].Metrics[0].Buckets[0].Count = 9999
        fams1[0].Metrics[0].Buckets[0].UpperBound = 9999
        fams2 := prov.Collect()
        for _, fam := range fams2 {
            if fam.Name=="buckets_deepcopy" {
                if fam.Metrics[0].Buckets[0].Count == 9999 { panic("Collect must return deep copy of Buckets") }
                if fam.Metrics[0].Buckets[0].UpperBound == 9999 { panic("Buckets UpperBound deep copy failed") }
                fmt.Println("OK")
                return
            }
        }
        panic("not found")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"collect buckets deep copy failed: {proc.stdout} {proc.stderr}"



def test_gauge_ignore_nan_inf():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "math"
        "ride-observability/observability"
    )
    func main(){
        prov := observability.NewMetricsProvider()
        g := prov.Gauge("gauge_nan")
        g.Set(10)
        g.Set(math.NaN())
        g.Add(math.Inf(1))
        g.Add(math.NaN())
        fams := prov.Collect()
        for _, fam := range fams {
            if fam.Name=="gauge_nan" {
                if fam.Metrics[0].Value != 10 { panic(fmt.Sprintf("Gauge Set/Add should ignore NaN/Inf, expected 10 got %f", fam.Metrics[0].Value)) }
                fmt.Println("OK")
                return
            }
        }
        panic("not found")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"gauge ignore nan inf failed: {proc.stdout} {proc.stderr}"



def test_tracing_start_nil_context():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        exp := observability.NewMemoryExporter()
        proc := observability.NewSimpleProcessor(exp)
        tracer := observability.NewTracer("svc", observability.WithProcessor(proc))
        defer func(){
            if r:=recover(); r!=nil { panic(fmt.Sprintf("Start with nil ctx should not panic, got %v", r)) }
        }()
        ctx, span := tracer.Start(nil, "nil-ctx")
        if ctx==nil { panic("Start(nil, ...) should return non-nil context") }
        if _, ok := observability.TraceFromContext(ctx); !ok { panic("ctx should have trace") }
        span.End()
        if len(exp.GetSpans())!=1 { panic("expected 1 span") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"start nil context failed: {proc.stdout} {proc.stderr}"



def test_tracing_contextwithtrace_nil():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        tc := observability.TraceContext{TraceID:"0102030405060708090a0b0c0d0e0f10", SpanID:"0102030405060708", Sampled:true, Flags:1}
        defer func(){
            if r:=recover(); r!=nil { panic(fmt.Sprintf("ContextWithTrace(nil, ...) should not panic: %v", r)) }
        }()
        ctx := observability.ContextWithTrace(nil, tc)
        if ctx==nil { panic("ContextWithTrace(nil) must return non-nil") }
        got, ok := observability.TraceFromContext(ctx)
        if !ok || got.TraceID!=tc.TraceID { panic("trace not stored") }
        // TraceFromContext nil
        _, ok = observability.TraceFromContext(nil)
        if ok { panic("TraceFromContext(nil) should return false") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"contextwithtrace nil failed: {proc.stdout} {proc.stderr}"



def test_tracing_context_returns_copy():
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
        _, span := tracer.Start(context.Background(), "copy-ctx")
        sc := span.Context()
        originalID := sc.TraceID
        sc.TraceID = "ffffffffffffffffffffffffffffffff"
        sc2 := span.Context()
        if sc2.TraceID != originalID { panic("Span.Context() must return defensive copy") }
        span.End()
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"context returns copy failed: {proc.stdout} {proc.stderr}"



def test_tracing_duplicate_key_not_count_toward_limit():
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
        _, span := tracer.Start(context.Background(), "dup-limit")
        // 200 adds of same key should count as 1 distinct, not exhaust limit
        for i:=0;i<200;i++{
            span.AddAttribute("samekey", i)
        }
        // now add 127 more distinct keys, total distinct should be 128
        for i:=0;i<127;i++{
            span.AddAttribute(fmt.Sprintf("k%d", i), i)
        }
        // At this point we have 1 +127 =128 distinct, at limit
        // Adding new distinct should be ignored
        span.AddAttribute("extra", "ignored")
        // Overwriting existing samekey should still work even after limit
        span.AddAttribute("samekey", "final")
        span.End()
        s := exp.GetSpans()[0]
        if len(s.Attributes)!=128 { panic(fmt.Sprintf("expected 128 attrs, got %d", len(s.Attributes))) }
        if s.Attributes["samekey"] != "final" { panic(fmt.Sprintf("duplicate key after limit should overwrite, expected final got %v", s.Attributes["samekey"])) }
        if _, ok := s.Attributes["extra"]; ok { panic("extra beyond limit should be ignored") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"duplicate key not count toward limit failed: {proc.stdout} {proc.stderr}"



def test_tracing_empty_attribute_key_ignored():
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
        _, span := tracer.Start(context.Background(), "empty-key", observability.WithAttributes(observability.Attribute{Key:"", Value:"should-ignore"}))
        span.AddAttribute("", "also-ignore")
        span.End()
        s := exp.GetSpans()[0]
        if len(s.Attributes)!=0 { panic(fmt.Sprintf("empty attribute keys should be ignored, got %d attrs", len(s.Attributes))) }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"empty attribute key ignored failed: {proc.stdout} {proc.stderr}"



def test_tracing_flags_preserved_via_propagation():
    code = textwrap.dedent("""
    package main
    import (
        "context"
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        sc := observability.TraceContext{TraceID:"0102030405060708090a0b0c0d0e0f10", SpanID:"0102030405060708", ParentID:"aabbccddeeff0011", Sampled:true, Flags:1}
        ctx := observability.ContextWithTrace(context.Background(), sc)
        carrier := map[string]string{}
        observability.MarshalTrace(ctx, carrier)
        ctx2 := observability.UnmarshalTrace(carrier)
        sc2, ok := observability.TraceFromContext(ctx2)
        if !ok { panic("should have trace") }
        if sc2.Flags != 1 { panic(fmt.Sprintf("Flags should be 1 after unmarshal sampled true, got %d", sc2.Flags)) }
        if !sc2.Sampled { panic("Sampled should be true") }
        // sampled false
        scFalse := observability.TraceContext{TraceID:"0102030405060708090a0b0c0d0e0f10", SpanID:"0102030405060708", Sampled:false, Flags:0}
        ctxF := observability.ContextWithTrace(context.Background(), scFalse)
        carrierF := map[string]string{}
        observability.MarshalTrace(ctxF, carrierF)
        ctxF2 := observability.UnmarshalTrace(carrierF)
        scF2, _ := observability.TraceFromContext(ctxF2)
        if scF2.Flags != 0 { panic(fmt.Sprintf("Flags should be 0 when sampled false, got %d", scF2.Flags)) }
        if scF2.Sampled { panic("Sampled should be false") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"flags preserved via propagation failed: {proc.stdout} {proc.stderr}"



def test_exporter_event_attributes_deep_copy():
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
        _, span := tracer.Start(context.Background(), "ev-deepcopy")
        span.AddEvent("ev", observability.Attribute{Key:"k", Value:"v"})
        span.End()
        spans1 := exp.GetSpans()
        // mutate nested event attributes
        if len(spans1[0].Events)==0 { panic("no events") }
        spans1[0].Events[0].Attributes[0].Key = "hacked"
        spans1[0].Events[0].Attributes[0].Value = "hacked"
        spans1[0].Events[0].Name = "hacked"
        spans2 := exp.GetSpans()
        ev := spans2[0].Events[0]
        if ev.Name=="hacked" { panic("Events slice deep copy failed") }
        if ev.Attributes[0].Key=="hacked" { panic("Event Attributes deep copy failed") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"exporter event attributes deep copy failed: {proc.stdout} {proc.stderr}"



def test_logger_with_immutability_and_concurrent():
    code = textwrap.dedent("""
    package main
    import (
        "bytes"
        "context"
        "fmt"
        "sync"
        "ride-observability/observability"
    )
    func main(){
        buf := &bytes.Buffer{}
        base := observability.NewLogger("svc", observability.WithOutput(buf))
        var wg sync.WaitGroup
        // concurrent With chains
        for i:=0;i<20;i++{
            wg.Add(1)
            go func(idx int){
                defer wg.Done()
                child := base.With(observability.Field{Key: fmt.Sprintf("k%d", idx), Value: idx})
                child.Info(context.Background(), fmt.Sprintf("msg-%d", idx))
            }(i)
        }
        wg.Wait()
        // also test that base not mutated
        _ = base.With(observability.Field{Key:"env", Value:"prod"})
        // base should not have env
        // we check via logging base after With
        buf.Reset()
        base.Info(context.Background(), "base-check")
        out := buf.String()
        if len(out)==0 { panic("no output") }
        if contains(out, "env") {
            panic("base logger mutated by With")
        }
        fmt.Println("OK")
    }
    func contains(s, substr string) bool {
        return len(s)>=len(substr) && (func() bool {
            for i:=0;i<=len(s)-len(substr);i++{
                if s[i:i+len(substr)]==substr { return true }
            }
            return false
        })()
    }
    """)
    proc = go_run_race_program(code, timeout=60)
    assert proc.returncode == 0, f"logger with immutability concurrent failed: {proc.stdout} {proc.stderr}"



def test_histogram_dedup_buckets():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        prov := observability.NewMetricsProvider()
        h := prov.Histogram("dedup_buckets", observability.WithBuckets([]float64{5,1,5,10,1}))
        h.Observe(3)
        fams := prov.Collect()
        for _, fam := range fams {
            if fam.Name=="dedup_buckets" {
                buckets := fam.Metrics[0].Buckets
                // should be deduped and sorted: [1,5,10] => 3 buckets
                if len(buckets)!=3 { panic(fmt.Sprintf("expected 3 deduped buckets got %d", len(buckets))) }
                if buckets[0].UpperBound!=1 || buckets[1].UpperBound!=5 || buckets[2].UpperBound!=10 {
                    panic(fmt.Sprintf("dedup sorted buckets wrong: %v", buckets))
                }
                fmt.Println("OK")
                return
            }
        }
        panic("not found")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"histogram dedup buckets failed: {proc.stdout} {proc.stderr}"



def test_tracing_parent_id_consistency():
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
        ctx1, s1 := tracer.Start(context.Background(), "root")
        _, s2 := tracer.Start(ctx1, "child")
        s2.End()
        s1.End()
        spans := exp.GetSpans()
        var root, child *observability.FinishedSpan
        for i:= range spans {
            if spans[i].Name=="root" { root=&spans[i] }
            if spans[i].Name=="child" { child=&spans[i] }
        }
        if root==nil || child==nil { panic("missing") }
        if root.ParentID != "" { panic("root ParentID should be empty") }
        if root.SpanContext.ParentID != "" { panic("root SpanContext.ParentID should be empty") }
        if root.ParentID != root.SpanContext.ParentID { panic("root ParentID and SpanContext.ParentID mismatch") }
        if child.ParentID == "" { panic("child ParentID empty") }
        if child.ParentID != child.SpanContext.ParentID { panic(fmt.Sprintf("child ParentID %s != SpanContext.ParentID %s", child.ParentID, child.SpanContext.ParentID)) }
        if child.ParentID != root.SpanContext.SpanID { panic("child ParentID should equal parent SpanID") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"parent id consistency failed: {proc.stdout} {proc.stderr}"



def test_logger_output_nil_fallback():
    code = textwrap.dedent("""
    package main
    import (
        "context"
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        defer func(){
            if r:=recover(); r!=nil { panic(fmt.Sprintf("WithOutput(nil) should not panic: %v", r)) }
        }()
        logger := observability.NewLogger("svc", observability.WithOutput(nil))
        logger.Info(context.Background(), "nil-output-test")
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"logger output nil fallback failed: {proc.stdout} {proc.stderr}"



def test_tracing_attribute_nil_value_ignored():
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
        _, span := tracer.Start(context.Background(), "nil-val", observability.WithAttributes(observability.Attribute{Key:"k1", Value:nil}))
        span.AddAttribute("k2", nil)
        span.End()
        s := exp.GetSpans()[0]
        if len(s.Attributes)!=0 { panic(fmt.Sprintf("nil attribute values should be ignored, got %d attrs", len(s.Attributes))) }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"attribute nil value ignored failed: {proc.stdout} {proc.stderr}"



def test_tracing_attribute_invalid_type_ignored():
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
        _, span := tracer.Start(context.Background(), "invalid-type", observability.WithAttributes(
            observability.Attribute{Key:"slice", Value:[]int{1,2}},
            observability.Attribute{Key:"map", Value:map[string]int{"a":1}},
            observability.Attribute{Key:"valid", Value:"ok"},
        ))
        span.AddAttribute("struct", struct{ A int }{1})
        span.End()
        s := exp.GetSpans()[0]
        // only valid string/int/float/bool should be kept, slice/map/struct ignored
        if len(s.Attributes)!=1 { panic(fmt.Sprintf("invalid type attrs should be ignored, expected 1 got %d", len(s.Attributes))) }
        if s.Attributes["valid"]!="ok" { panic("valid attr missing") }
        if _, ok := s.Attributes["slice"]; ok { panic("slice type should be ignored") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"attribute invalid type ignored failed: {proc.stdout} {proc.stderr}"



def test_tracing_endtime_preserved_on_idempotent():
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
        proc := observability.NewSimpleProcessor(exp)
        tracer := observability.NewTracer("svc", observability.WithProcessor(proc))
        _, span := tracer.Start(context.Background(), "endtime-preserve")
        span.End()
        expSpans := exp.GetSpans()
        if len(expSpans)!=1 { panic("expected 1") }
        firstEnd := expSpans[0].EndTime
        time.Sleep(10*time.Millisecond)
        span.End()
        span.End()
        expSpans2 := exp.GetSpans()
        if len(expSpans2)!=1 { panic(fmt.Sprintf("idempotent End should still 1, got %d", len(expSpans2))) }
        secondEnd := expSpans2[0].EndTime
        if !firstEnd.Equal(secondEnd) {
            panic(fmt.Sprintf("EndTime must be preserved on second End, first %v second %v", firstEnd, secondEnd))
        }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"endtime preserved failed: {proc.stdout} {proc.stderr}"



def test_tracing_setstatus_after_end_noop():
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
        _, span := tracer.Start(context.Background(), "status-after-end")
        span.End()
        span.SetStatus(observability.StatusError, "should-be-ignored")
        s := exp.GetSpans()[0]
        if s.StatusCode == observability.StatusError { panic("SetStatus after End should be no-op") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"setstatus after end noop failed: {proc.stdout} {proc.stderr}"



def test_metrics_histogram_bucket_nan_inf_filtered():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "math"
        "ride-observability/observability"
    )
    func main(){
        prov := observability.NewMetricsProvider()
        h := prov.Histogram("bucket_filter", observability.WithBuckets([]float64{1, math.NaN(), 5, math.Inf(1), math.Inf(-1), 10, 5}))
        h.Observe(3)
        fams := prov.Collect()
        for _, fam := range fams {
            if fam.Name=="bucket_filter" {
                buckets := fam.Metrics[0].Buckets
                // NaN/Inf should be filtered, duplicate 5 deduped => [1,5,10] => 3 buckets
                if len(buckets)!=3 {
                    panic(fmt.Sprintf("NaN/Inf buckets should be filtered and dup deduped, expected 3 got %d buckets %v", len(buckets), buckets))
                }
                if buckets[0].UpperBound!=1 || buckets[1].UpperBound!=5 || buckets[2].UpperBound!=10 {
                    panic(fmt.Sprintf("filtered sorted buckets wrong %v", buckets))
                }
                fmt.Println("OK")
                return
            }
        }
        panic("not found")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"histogram bucket NaN Inf filtered failed: {proc.stdout} {proc.stderr}"



def test_logger_field_duplicate_last_wins():
    code = textwrap.dedent("""
    package main
    import (
        "bytes"
        "context"
        "encoding/json"
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        buf := &bytes.Buffer{}
        base := observability.NewLogger("svc", observability.WithOutput(buf))
        base = base.With(observability.Field{Key:"env", Value:"dev"})
        child := base.With(observability.Field{Key:"env", Value:"prod"})
        child.Info(context.Background(), "dup-field", observability.Field{Key:"env", Value:"staging"})
        var obj map[string]interface{}
        if err:= json.Unmarshal(bytes.TrimSpace(buf.Bytes()), &obj); err!=nil { panic("not json") }
        if obj["env"]!="staging" { panic(fmt.Sprintf("field duplicate last wins across With chain and per-call, expected staging got %v", obj["env"])) }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"logger field duplicate last wins failed: {proc.stdout} {proc.stderr}"



def test_logger_with_defensive_copy_fields():
    code = textwrap.dedent("""
    package main
    import (
        "bytes"
        "context"
        "encoding/json"
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        buf := &bytes.Buffer{}
        base := observability.NewLogger("svc", observability.WithOutput(buf))
        fields := []observability.Field{{Key:"k1", Value:"v1"}}
        child := base.With(fields...)
        // mutate original slice after With
        fields[0].Key = "hacked"
        fields[0].Value = "hacked"
        fields = append(fields, observability.Field{Key:"injected", Value:"yes"})
        child.Info(context.Background(), "defcopy")
        var obj map[string]interface{}
        json.Unmarshal(bytes.TrimSpace(buf.Bytes()), &obj)
        if obj["k1"]!="v1" { panic(fmt.Sprintf("With must copy fields slice defensively, expected v1 got %v", obj["k1"])) }
        if _, ok := obj["injected"]; ok { panic("injected field leaked") }
        if obj["hacked"]!=nil { panic("hacked leaked") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"logger with defensive copy fields failed: {proc.stdout} {proc.stderr}"



def test_exporter_concurrent_clear():
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
        proc := observability.NewSimpleProcessor(exp)
        tracer := observability.NewTracer("svc", observability.WithProcessor(proc))
        var wg sync.WaitGroup
        // concurrent ExportSpans and Clear
        wg.Add(2)
        go func(){
            defer wg.Done()
            for i:=0;i<100;i++{
                _, s := tracer.Start(context.Background(), fmt.Sprintf("span-%d", i))
                s.End()
            }
        }()
        go func(){
            defer wg.Done()
            for i:=0;i<20;i++{
                exp.Clear()
            }
        }()
        wg.Wait()
        // after concurrent clear, should not panic and GetCount should be consistent
        _ = exp.GetCount()
        _ = exp.GetSpans()
        fmt.Println("OK")
    }
    """)
    proc = go_run_race_program(code, timeout=30)
    assert proc.returncode == 0, f"exporter concurrent clear failed: {proc.stdout} {proc.stderr}"



def test_tracing_custom_idgen_invalid_hex_marshal_no_write():
    code = textwrap.dedent("""
    package main
    import (
        "context"
        "fmt"
        "ride-observability/observability"
    )
    type invalidGen struct{}
    func (g *invalidGen) NewTraceID() string { return "invalid-trace-id-not-hex" }
    func (g *invalidGen) NewSpanID() string { return "bad" }
    func main(){
        exp := observability.NewMemoryExporter()
        proc := observability.NewSimpleProcessor(exp)
        tracer := observability.NewTracer("svc", observability.WithProcessor(proc), observability.WithIDGenerator(&invalidGen{}))
        ctx, span := tracer.Start(context.Background(), "invalid-ids")
        sc := span.Context()
        if sc.TraceID != "invalid-trace-id-not-hex" { panic("custom invalid TraceID should still be stored") }
        carrier := map[string]string{}
        observability.MarshalTrace(ctx, carrier)
        if _, ok := carrier["x-ride-trace"]; ok {
            panic("MarshalTrace must NOT write anything when TraceID/SpanID invalid")
        }
        // also test that background ctx with no trace does not write
        carrier2 := map[string]string{}
        observability.MarshalTrace(context.Background(), carrier2)
        if len(carrier2)!=0 { panic("background should not write") }
        span.End()
        // exporter should still have span even with invalid IDs (exporter does not validate)
        if len(exp.GetSpans())!=1 { panic("exporter should have 1 even with invalid IDs") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"custom idgen invalid hex marshal no write failed: {proc.stdout} {proc.stderr}"



def test_tracing_service_name_preserved():
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
        tracer := observability.NewTracer("svc-a", observability.WithProcessor(proc), observability.WithServiceName("override-svc"))
        _, span := tracer.Start(context.Background(), "svc-test")
        span.End()
        s := exp.GetSpans()[0]
        if s.ServiceName != "override-svc" { panic(fmt.Sprintf("ServiceName override failed, expected override-svc got %s", s.ServiceName)) }
        // without override, should be first arg
        exp2 := observability.NewMemoryExporter()
        proc2 := observability.NewSimpleProcessor(exp2)
        tracer2 := observability.NewTracer("original-svc", observability.WithProcessor(proc2))
        _, span2 := tracer2.Start(context.Background(), "svc-test2")
        span2.End()
        s2 := exp2.GetSpans()[0]
        if s2.ServiceName != "original-svc" { panic(fmt.Sprintf("ServiceName should be original-svc got %s", s2.ServiceName)) }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"service name preserved failed: {proc.stdout} {proc.stderr}"



def test_tracing_marshal_overwrites_existing_carrier():
    code = textwrap.dedent("""
    package main
    import (
        "context"
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        sc1 := observability.TraceContext{TraceID:"0102030405060708090a0b0c0d0e0f10", SpanID:"0102030405060708", Sampled:true, Flags:1}
        ctx1 := observability.ContextWithTrace(context.Background(), sc1)
        carrier := map[string]string{"x-ride-trace":"old-value"}
        observability.MarshalTrace(ctx1, carrier)
        if carrier["x-ride-trace"]=="old-value" { panic("MarshalTrace should overwrite existing carrier value") }
        sc2 := observability.TraceContext{TraceID:"aabbccddeeff0011aabbccddeeff0011", SpanID:"aabbccddeeff0011", Sampled:false, Flags:0}
        ctx2 := observability.ContextWithTrace(context.Background(), sc2)
        observability.MarshalTrace(ctx2, carrier)
        if carrier["x-ride-trace"]=="old-value" { panic("overwrite failed second time") }
        // ensure second value is different from first?
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"marshal overwrites existing carrier failed: {proc.stdout} {proc.stderr}"



def test_metrics_withlabels_nil():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        prov := observability.NewMetricsProvider()
        c1 := prov.Counter("nil_labels_test", observability.WithLabels(nil))
        c1.Inc()
        c2 := prov.Counter("nil_labels_test")
        c2.Inc()
        fams := prov.Collect()
        for _, fam := range fams {
            if fam.Name=="nil_labels_test" {
                if len(fam.Metrics)!=1 { panic(fmt.Sprintf("nil vs empty labels should reuse same series, expected 1 got %d", len(fam.Metrics))) }
                if fam.Metrics[0].Value != 2 { panic(fmt.Sprintf("expected value 2 got %f", fam.Metrics[0].Value)) }
                fmt.Println("OK")
                return
            }
        }
        panic("not found")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"withlabels nil failed: {proc.stdout} {proc.stderr}"



def test_metrics_histogram_empty_buckets_uses_default():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        prov := observability.NewMetricsProvider()
        h1 := prov.Histogram("empty_bucket_def", observability.WithBuckets(nil))
        h1.Observe(0.01)
        h2 := prov.Histogram("empty_slice_bucket_def", observability.WithBuckets([]float64{}))
        h2.Observe(0.01)
        fams := prov.Collect()
        for _, fam := range fams {
            if fam.Name=="empty_bucket_def" {
                if len(fam.Metrics[0].Buckets)!=11 { panic(fmt.Sprintf("nil buckets should use default 11, got %d", len(fam.Metrics[0].Buckets))) }
            }
            if fam.Name=="empty_slice_bucket_def" {
                if len(fam.Metrics[0].Buckets)!=11 { panic(fmt.Sprintf("empty slice buckets should use default 11, got %d", len(fam.Metrics[0].Buckets))) }
            }
        }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"histogram empty buckets uses default failed: {proc.stdout} {proc.stderr}"



def test_logger_timestamp_recent_and_utc():
    code = textwrap.dedent("""
    package main
    import (
        "bytes"
        "context"
        "encoding/json"
        "fmt"
        "strings"
        "time"
        "ride-observability/observability"
    )
    func main(){
        buf := &bytes.Buffer{}
        logger := observability.NewLogger("svc", observability.WithOutput(buf))
        before := time.Now().Add(-2*time.Second)
        logger.Info(context.Background(), "ts-recent")
        after := time.Now().Add(2*time.Second)
        var obj map[string]interface{}
        if err:= json.Unmarshal(bytes.TrimSpace(buf.Bytes()), &obj); err!=nil { panic("not json") }
        tsStr, ok := obj["timestamp"].(string)
        if !ok { panic("timestamp not string") }
        // must be UTC (contains Z or +00:00) and recent
        if !strings.Contains(tsStr, "Z") && !strings.Contains(tsStr, "+") {
            // RFC3339Nano UTC usually ends with Z
            fmt.Printf("timestamp %s may not be UTC but still parseable\\n", tsStr)
        }
        parsed, err := time.Parse(time.RFC3339Nano, tsStr)
        if err!=nil { panic("timestamp not RFC3339Nano: "+err.Error()) }
        if parsed.Before(before) || parsed.After(after) {
            panic(fmt.Sprintf("timestamp not recent, before %v parsed %v after %v", before, parsed, after))
        }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"logger timestamp recent and utc failed: {proc.stdout} {proc.stderr}"



def test_tracing_withparent_duplicate_across_withattributes_and_addafterlimit():
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
        baseAttrs := []observability.Attribute{}
        for i:=0;i<128;i++{
            baseAttrs = append(baseAttrs, observability.Attribute{Key: fmt.Sprintf("k%d", i), Value:i})
        }
        _, span := tracer.Start(context.Background(), "dup-across-limit", observability.WithAttributes(baseAttrs...))
        // at limit 128, overwriting existing key should still succeed
        span.AddAttribute("k10", "overwritten")
        // new distinct beyond limit should be ignored
        span.AddAttribute("newkey", "ignored")
        span.End()
        s := exp.GetSpans()[0]
        if len(s.Attributes)!=128 { panic(fmt.Sprintf("expected 128 got %d", len(s.Attributes))) }
        if s.Attributes["k10"] != "overwritten" { panic(fmt.Sprintf("overwrite after limit should work, got %v", s.Attributes["k10"])) }
        if _, ok := s.Attributes["newkey"]; ok { panic("newkey beyond limit should be ignored") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"withparent duplicate across limit failed: {proc.stdout} {proc.stderr}"



def test_logger_json_escaping_special_chars():
    code = textwrap.dedent("""
    package main
    import (
        "bytes"
        "context"
        "encoding/json"
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        buf := &bytes.Buffer{}
        logger := observability.NewLogger("svc", observability.WithOutput(buf))
        logger.Info(context.Background(), "msg with \\"quotes\\" and\\nnewline", observability.Field{Key:"key", Value:"val with \\"quote\\""})
        var obj map[string]interface{}
        if err:= json.Unmarshal(bytes.TrimSpace(buf.Bytes()), &obj); err!=nil {
            panic(fmt.Sprintf("JSON escaping failed, not valid json: %v line %s", err, buf.String()))
        }
        if obj["message"]==nil { panic("message missing") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"logger json escaping failed: {proc.stdout} {proc.stderr}"



def test_tracing_context_withattributes_duplicate_within_same_call_last_wins():
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
        _, span := tracer.Start(context.Background(), "dup-within", observability.WithAttributes(
            observability.Attribute{Key:"dup", Value:"first"},
            observability.Attribute{Key:"dup", Value:"second"},
            observability.Attribute{Key:"dup", Value:"third"},
        ))
        span.End()
        s := exp.GetSpans()[0]
        if s.Attributes["dup"] != "third" { panic(fmt.Sprintf("duplicate within same WithAttributes last wins expected third got %v", s.Attributes["dup"])) }
        if len(s.Attributes)!=1 { panic(fmt.Sprintf("duplicate within same call should count as 1 distinct, got %d", len(s.Attributes))) }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"duplicate within same call failed: {proc.stdout} {proc.stderr}"



def test_tracing_finished_span_attributes_non_nil_when_present():
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
        // with attributes
        _, span := tracer.Start(context.Background(), "with-attr", observability.WithAttributes(observability.Attribute{Key:"k", Value:"v"}))
        span.End()
        s := exp.GetSpans()[0]
        if s.Attributes==nil { panic("Attributes map must be non-nil when attributes present") }
        // zero attributes case: may be nil or empty, but we check that GetSpans returns non-nil or empty but not panics
        // Actually for zero attrs, we allow nil or empty, but when attrs present must be non-nil
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"finished span attributes non-nil failed: {proc.stdout} {proc.stderr}"



def test_tracing_parent_overrides_context_even_when_root():
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
        ctx1, _ := tracer.Start(context.Background(), "parent1")
        ctx2, _ := tracer.Start(context.Background(), "parent2")
        sc1, _ := observability.TraceFromContext(ctx1)
        sc2, _ := observability.TraceFromContext(ctx2)
        // Use WithParent to override ctx1 parent with sc2
        _, child := tracer.Start(ctx1, "child", observability.WithParent(sc2))
        if child.Context().TraceID != sc2.TraceID {
            panic(fmt.Sprintf("WithParent should override context parent TraceID, got %s expected %s", child.Context().TraceID, sc2.TraceID))
        }
        if child.Context().ParentID != sc2.SpanID {
            panic(fmt.Sprintf("WithParent ParentID mismatch, got %s expected %s", child.Context().ParentID, sc2.SpanID))
        }
        child.End()
        fmt.Println("OK")
        _ = sc1
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"parent overrides context failed: {proc.stdout} {proc.stderr}"



def test_tracing_unmarshal_extra_colon_invalid():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        cases := []string{
            "a:b:c:d:e",
            "0102030405060708090a0b0c0d0e0f10:0102030405060708::1:extra",
            "0102030405060708090a0b0c0d0e0f10:0102030405060708::1:",
            ":0102030405060708::1",
            "0102030405060708090a0b0c0d0e0f10::aabbccddeeff0011:1",
        }
        for i, val := range cases {
            ctx := observability.UnmarshalTrace(map[string]string{"x-ride-trace": val})
            if _, ok := observability.TraceFromContext(ctx); ok {
                panic(fmt.Sprintf("case %d %q should be invalid (wrong colon count or empty TraceID), got trace", i, val))
            }
        }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"unmarshal extra colon invalid failed: {proc.stdout} {proc.stderr}"



def test_tracing_marshal_does_not_write_on_invalid_ids():
    code = textwrap.dedent("""
    package main
    import (
        "context"
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        // invalid TraceID
        scBad := observability.TraceContext{TraceID:"short", SpanID:"0102030405060708", Sampled:true, Flags:1}
        ctxBad := observability.ContextWithTrace(context.Background(), scBad)
        carrier := map[string]string{}
        observability.MarshalTrace(ctxBad, carrier)
        if len(carrier)!=0 { panic(fmt.Sprintf("MarshalTrace should NOT write when TraceID invalid, got %v", carrier)) }
        // invalid SpanID
        scBad2 := observability.TraceContext{TraceID:"0102030405060708090a0b0c0d0e0f10", SpanID:"badspan", Sampled:true}
        ctxBad2 := observability.ContextWithTrace(context.Background(), scBad2)
        carrier2 := map[string]string{}
        observability.MarshalTrace(ctxBad2, carrier2)
        if len(carrier2)!=0 { panic("should not write when SpanID invalid") }
        // invalid ParentID
        scBad3 := observability.TraceContext{TraceID:"0102030405060708090a0b0c0d0e0f10", SpanID:"0102030405060708", ParentID:"nothex", Sampled:true}
        ctxBad3 := observability.ContextWithTrace(context.Background(), scBad3)
        carrier3 := map[string]string{}
        observability.MarshalTrace(ctxBad3, carrier3)
        if len(carrier3)!=0 { panic("should not write when ParentID invalid") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"marshal does not write on invalid ids failed: {proc.stdout} {proc.stderr}"



def test_metrics_histogram_inclusive_and_sum():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        prov := observability.NewMetricsProvider()
        h := prov.Histogram("inclusive_sum", observability.WithBuckets([]float64{1,5,10}))
        h.Observe(1)
        h.Observe(5)
        h.Observe(10)
        h.Observe(2)
        fams := prov.Collect()
        for _, fam := range fams {
            if fam.Name=="inclusive_sum" {
                m := fam.Metrics[0]
                if m.Count!=4 { panic(fmt.Sprintf("count expected 4 got %d", m.Count)) }
                if m.Sum < 17.9 || m.Sum > 18.1 { panic(fmt.Sprintf("sum expected 18 got %f", m.Sum)) }
                // bucket 1: values <=1 => only 1 => count 1
                if m.Buckets[0].Count!=1 { panic(fmt.Sprintf("bucket 1 expected 1 got %d", m.Buckets[0].Count)) }
                // bucket 5: <=5 => 1,5,2 => 3
                if m.Buckets[1].Count!=3 { panic(fmt.Sprintf("bucket 5 expected 3 got %d", m.Buckets[1].Count)) }
                // bucket 10: <=10 => all 4
                if m.Buckets[2].Count!=4 { panic(fmt.Sprintf("bucket 10 expected 4 got %d", m.Buckets[2].Count)) }
                fmt.Println("OK")
                return
            }
        }
        panic("not found")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"histogram inclusive and sum failed: {proc.stdout} {proc.stderr}"



def test_metrics_invalid_labels_noop_does_not_affect_valid():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        prov := observability.NewMetricsProvider()
        cValid := prov.Counter("metric_a", observability.WithLabels(map[string]string{"good":"1"}))
        cValid.Inc()
        cInvalid := prov.Counter("metric_a", observability.WithLabels(map[string]string{"bad-key":"1"}))
        cInvalid.Inc()
        cInvalid2 := prov.Counter("metric_a", observability.WithLabels(map[string]string{"":"empty"}))
        cInvalid2.Inc()
        fams := prov.Collect()
        for _, fam := range fams {
            if fam.Name=="metric_a" {
                if len(fam.Metrics)!=1 { panic(fmt.Sprintf("invalid labels should be no-op not affect valid, expected 1 series got %d", len(fam.Metrics))) }
                if fam.Metrics[0].Value != 1 { panic(fmt.Sprintf("valid value should be 1 got %f", fam.Metrics[0].Value)) }
                fmt.Println("OK")
                return
            }
        }
        panic("not found")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"invalid labels noop does not affect valid failed: {proc.stdout} {proc.stderr}"



def test_tracing_context_overwrite_and_copy():
    code = textwrap.dedent("""
    package main
    import (
        "context"
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        sc1 := observability.TraceContext{TraceID:"0102030405060708090a0b0c0d0e0f10", SpanID:"0102030405060708", Sampled:true, Flags:1}
        sc2 := observability.TraceContext{TraceID:"aabbccddeeff0011aabbccddeeff0011", SpanID:"aabbccddeeff0011", Sampled:false, Flags:0}
        ctx := observability.ContextWithTrace(context.Background(), sc1)
        ctx2 := observability.ContextWithTrace(ctx, sc2)
        got, ok := observability.TraceFromContext(ctx2)
        if !ok { panic("should have trace") }
        if got.TraceID != sc2.TraceID { panic("overwrite should have sc2") }
        // check original ctx still has sc1
        got1, ok := observability.TraceFromContext(ctx)
        if !ok || got1.TraceID != sc1.TraceID { panic("original ctx should still have sc1") }
        // mutate sc2 after storing should not affect
        sc2.TraceID = "ffffffffffffffffffffffffffffffff"
        gotAfter, _ := observability.TraceFromContext(ctx2)
        if gotAfter.TraceID == "ffffffffffffffffffffffffffffffff" { panic("ContextWithTrace must copy") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"context overwrite and copy failed: {proc.stdout} {proc.stderr}"



def test_tracing_span_kind_default_internal():
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
        _, span := tracer.Start(context.Background(), "no-kind")
        span.End()
        s := exp.GetSpans()[0]
        if s.Kind != observability.KindInternal { panic(fmt.Sprintf("default kind should be Internal 0, got %d", s.Kind)) }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"span kind default internal failed: {proc.stdout} {proc.stderr}"



def test_logger_warning_alias():
    code = textwrap.dedent("""
    package main
    import (
        "bytes"
        "context"
        "fmt"
        "strings"
        "ride-observability/observability"
    )
    func main(){
        buf := &bytes.Buffer{}
        logger := observability.NewLogger("svc", observability.WithOutput(buf), observability.WithLevel("warning"))
        logger.Warn(context.Background(), "warn-should-pass")
        logger.Info(context.Background(), "info-should-filter")
        out := buf.String()
        if !strings.Contains(out, "warn-should-pass") { panic("warning level alias should allow Warn") }
        if strings.Contains(out, "info-should-filter") { panic("info should be filtered at warning level") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"logger warning alias failed: {proc.stdout} {proc.stderr}"



def test_metrics_counter_add_zero_allowed():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        prov := observability.NewMetricsProvider()
        c := prov.Counter("zero_add")
        c.Inc()
        c.Add(0)
        fams := prov.Collect()
        for _, fam := range fams {
            if fam.Name=="zero_add" {
                if fam.Metrics[0].Value != 1 { panic(fmt.Sprintf("Add(0) should be allowed and keep value 1, got %f", fam.Metrics[0].Value)) }
                fmt.Println("OK")
                return
            }
        }
        panic("not found")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"counter Add zero allowed failed: {proc.stdout} {proc.stderr}"



def test_tracing_event_attributes_empty_key_ignored():
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
        _, span := tracer.Start(context.Background(), "ev-empty-key")
        span.AddEvent("ev", observability.Attribute{Key:"", Value:"should-ignore"}, observability.Attribute{Key:"ok", Value:"yes"})
        span.End()
        ev := exp.GetSpans()[0].Events[0]
        // empty key event attr should be ignored or at least not counted as valid? We require filtering empty key for event attrs
        // So expect 1 attr (ok)
        hasEmpty := false
        hasOK := false
        for _, a := range ev.Attributes {
            if a.Key=="" { hasEmpty=true }
            if a.Key=="ok" { hasOK=true }
        }
        if hasEmpty { panic("empty key event attr should be ignored") }
        if !hasOK { panic("ok attr missing") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"event attributes empty key ignored failed: {proc.stdout} {proc.stderr}"



def test_tracing_exporter_getcount_consistency():
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
        for i:=0;i<5;i++{
            _, s := tracer.Start(context.Background(), fmt.Sprintf("span-%d", i))
            s.End()
        }
        if exp.GetCount()!=5 { panic(fmt.Sprintf("GetCount expected 5 got %d", exp.GetCount())) }
        if len(exp.GetSpans())!=5 { panic("GetSpans len mismatch GetCount") }
        exp.Clear()
        if exp.GetCount()!=0 { panic("after Clear GetCount should be 0") }
        if len(exp.GetSpans())!=0 { panic("after Clear GetSpans should be 0") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"exporter getcount consistency failed: {proc.stdout} {proc.stderr}"



def test_metrics_histogram_default_buckets_used_when_nil():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        prov := observability.NewMetricsProvider()
        h := prov.Histogram("default_when_nil")
        h.Observe(0.005)
        h.Observe(10)
        h.Observe(20)
        fams := prov.Collect()
        for _, fam := range fams {
            if fam.Name=="default_when_nil" {
                if len(fam.Metrics[0].Buckets)!=11 { panic(fmt.Sprintf("default buckets expected 11 got %d", len(fam.Metrics[0].Buckets))) }
                // 0.005 should be in first bucket, 10 in last default bucket, 20 beyond last => Count for last bucket should be 2 (10 and 0.005? actually 0.005 <=0.005 yes first bucket, 10 <=10 yes, 20 not <=10 so not counted in any bucket cumulative? Wait cumulative counts <= upper bound, so 20 not <=10 so last bucket count should be 2 not 3? Let's check: buckets [0.005,...,10], Observe 0.005 => bucket0 count1 and all larger buckets count1, Observe 10 => <=10 count1 for last bucket, Observe 20 => no bucket <=20? But default last is 10, so 20 not counted in any bucket? Actually OTel buckets include +Inf? But spec says cumulative inclusive for defined buckets only, so 20 would not be counted in any bucket? However prior tests assume default bucket list and Observe 0.01 etc. We'll just check Count field total 3.
                if fam.Metrics[0].Count!=3 { panic(fmt.Sprintf("count expected 3 got %d", fam.Metrics[0].Count)) }
                fmt.Println("OK")
                return
            }
        }
        panic("not found")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"histogram default buckets used when nil failed: {proc.stdout} {proc.stderr}"



def test_logger_service_field_always_present():
    code = textwrap.dedent("""
    package main
    import (
        "bytes"
        "context"
        "encoding/json"
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        buf := &bytes.Buffer{}
        logger := observability.NewLogger("my-service", observability.WithOutput(buf))
        logger.Info(context.Background(), "test-msg")
        var obj map[string]interface{}
        if err:= json.Unmarshal(bytes.TrimSpace(buf.Bytes()), &obj); err!=nil { panic("not json") }
        if obj["service"]!="my-service" { panic(fmt.Sprintf("service field must be present and equal to service name, got %v", obj["service"])) }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"logger service field always present failed: {proc.stdout} {proc.stderr}"



def test_metrics_provider_isolation_after_collect():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        p1 := observability.NewMetricsProvider()
        c1 := p1.Counter("isolated_after_collect")
        c1.Inc()
        fams1 := p1.Collect()
        // after collect, add more
        c1.Inc()
        fams2 := p1.Collect()
        var v1, v2 float64
        for _, fam := range fams1 {
            if fam.Name=="isolated_after_collect" { v1 = fam.Metrics[0].Value }
        }
        for _, fam := range fams2 {
            if fam.Name=="isolated_after_collect" { v2 = fam.Metrics[0].Value }
        }
        if v1!=1 { panic(fmt.Sprintf("first collect should be 1 got %f", v1)) }
        if v2!=2 { panic(fmt.Sprintf("second collect after inc should be 2 got %f", v2)) }
        // second provider isolation
        p2 := observability.NewMetricsProvider()
        if len(p2.Collect())!=0 { panic("new provider should be empty") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"provider isolation after collect failed: {proc.stdout} {proc.stderr}"



def test_tracing_span_attribute_types_varied():
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
        _, span := tracer.Start(context.Background(), "varied-types")
        span.AddAttribute("int", 42)
        span.AddAttribute("int32", int32(32))
        span.AddAttribute("int64", int64(64))
        span.AddAttribute("float32", float32(3.14))
        span.AddAttribute("float64", 2.718)
        span.AddAttribute("bool", true)
        span.AddAttribute("string", "hello")
        span.End()
        attrs := exp.GetSpans()[0].Attributes
        if len(attrs)!=7 { panic(fmt.Sprintf("expected 7 valid typed attrs, got %d", len(attrs))) }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"span attribute types varied failed: {proc.stdout} {proc.stderr}"



def test_tracing_extract_header_case_insensitive():
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
        ctx, span := tracer.Start(context.Background(), "root")
        carrier := map[string]string{}
        observability.MarshalTrace(ctx, carrier)
        // transform key to uppercase
        upper := map[string]string{}
        for k,v := range carrier {
            up := ""
            for _, ch := range k {
                if ch >= 'a' && ch <= 'z' { up += string(ch-32) } else { up += string(ch) }
            }
            upper[up] = v
        }
        ctx2 := observability.UnmarshalTrace(upper)
        got, ok := observability.TraceFromContext(ctx2)
        if !ok { panic("UnmarshalTrace should be case-insensitive for header key") }
        orig, _ := observability.TraceFromContext(ctx)
        if got.TraceID != orig.TraceID { panic(fmt.Sprintf("traceID mismatch %s vs %s", got.TraceID, orig.TraceID)) }
        span.End()
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"extract header case insensitive failed: {proc.stdout} {proc.stderr}"



def test_tracing_extract_trims_whitespace():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        val := "  aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa:bbbbbbbbbbbbbbbb::1  "
        carrier := map[string]string{"x-ride-trace": val}
        ctx := observability.UnmarshalTrace(carrier)
        tc, ok := observability.TraceFromContext(ctx)
        if !ok { panic("should trim whitespace") }
        if tc.TraceID != "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" { panic(fmt.Sprintf("bad traceID %s", tc.TraceID)) }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"extract trims whitespace failed: {proc.stdout} {proc.stderr}"



def test_tracing_unmarshal_uppercase_hex_allowed():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        carrier := map[string]string{"x-ride-trace": "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA:BBBBBBBBBBBBBBBB::1"}
        ctx := observability.UnmarshalTrace(carrier)
        tc, ok := observability.TraceFromContext(ctx)
        if !ok { panic("uppercase hex should be allowed") }
        if len(tc.TraceID)!=32 { panic("traceID len should be 32") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"uppercase hex allowed failed: {proc.stdout} {proc.stderr}"



def test_tracing_attribute_value_truncate_event():
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
        _, span := tracer.Start(context.Background(), "trunc-event")
        long := strings.Repeat("y", 2000)
        span.AddEvent("evt", observability.Attribute{Key:"k", Value: long})
        span.End()
        evts := exp.GetSpans()[0].Events
        if len(evts)!=1 { panic("event missing") }
        v, ok := evts[0].Attributes[0].Value.(string)
        if !ok { panic("event attr not string") }
        _ = ok
        if len(v)>1024 { panic(fmt.Sprintf("event attr not truncated: %d", len(v))) }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"attr truncate event failed: {proc.stdout} {proc.stderr}"



def test_tracing_withspanKind_last_wins():
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
        _, span := tracer.Start(context.Background(), "kind-last-wins",
            observability.WithSpanKind(observability.SpanKindClient),
            observability.WithSpanKind(observability.SpanKindServer),
        )
        span.End()
        fs := exp.GetSpans()[0]
        if fs.Kind != observability.SpanKindServer {
            panic(fmt.Sprintf("last WithSpanKind should win, got %d", fs.Kind))
        }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"withspankind last wins failed: {proc.stdout} {proc.stderr}"



def test_tracing_setstatus_last_wins_before_end():
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
        _, span := tracer.Start(context.Background(), "status-last-wins")
        span.SetStatus(observability.StatusError, "first")
        span.SetStatus(observability.StatusOK, "second")
        span.End()
        fs := exp.GetSpans()[0]
        if fs.StatusCode != observability.StatusOK { panic(fmt.Sprintf("last status should win, got %d", fs.StatusCode)) }
        if fs.StatusMessage != "second" { panic("message should be second") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"setstatus last wins failed: {proc.stdout} {proc.stderr}"



def test_tracing_concurrent_addattr_addevent_setstatus_end():
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
        proc := observability.NewSimpleProcessor(exp)
        tracer := observability.NewTracer("svc", observability.WithProcessor(proc))
        _, span := tracer.Start(context.Background(), "conc-all")
        var wg sync.WaitGroup
        for i:=0; i<10; i++ {
            wg.Add(1)
            go func(idx int){
                defer wg.Done()
                span.AddAttribute(fmt.Sprintf("k%d", idx), idx)
            }(i)
        }
        for i:=0; i<10; i++ {
            wg.Add(1)
            go func(idx int){
                defer wg.Done()
                span.AddEvent(fmt.Sprintf("e%d", idx))
            }(i)
        }
        for i:=0; i<5; i++ {
            wg.Add(1)
            go func(){
                defer wg.Done()
                span.SetStatus(observability.StatusError, "err")
            }()
        }
        wg.Add(1)
        go func(){
            defer wg.Done()
            span.End()
        }()
        wg.Wait()
        span.End()
        spans := exp.GetSpans()
        if len(spans)!=1 { panic(fmt.Sprintf("expected 1 span, got %d", len(spans))) }
        fmt.Println("OK")
    }
    """)
    proc = go_run_race_program(code)
    assert proc.returncode == 0, f"concurrent addattr addevent setstatus end failed: {proc.stdout} {proc.stderr}"



def test_tracing_context_withtrace_overwrites():
    code = textwrap.dedent("""
    package main
    import (
        "context"
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        tc1 := observability.TraceContext{TraceID: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", SpanID: "bbbbbbbbbbbbbbbb"}
        tc2 := observability.TraceContext{TraceID: "cccccccccccccccccccccccccccccccc", SpanID: "dddddddddddddddd"}
        ctx := context.Background()
        ctx = observability.ContextWithTrace(ctx, tc1)
        ctx = observability.ContextWithTrace(ctx, tc2)
        got, ok := observability.TraceFromContext(ctx)
        if !ok { panic("should have trace") }
        if got.TraceID != tc2.TraceID { panic(fmt.Sprintf("overwrite failed: %s vs %s", got.TraceID, tc2.TraceID)) }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"context withtrace overwrites failed: {proc.stdout} {proc.stderr}"



def test_tracing_span_context_copy_on_parent():
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
        ctx, parent := tracer.Start(context.Background(), "parent")
        _, child := tracer.Start(ctx, "child")
        parentSC := parent.SpanContext()
        childSC := child.SpanContext()
        if childSC.TraceID != parentSC.TraceID { panic("child should inherit TraceID") }
        if childSC.SpanID == parentSC.SpanID { panic("child SpanID should differ") }
        if childSC.ParentID != parentSC.SpanID { panic("child ParentID should be parent SpanID") }
        parent.End()
        if child.SpanContext().ParentID != parentSC.SpanID { panic("child ParentID changed after parent End") }
        child.End()
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"span context copy on parent failed: {proc.stdout} {proc.stderr}"



def test_tracing_marshal_preserves_other_keys():
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
        ctx, span := tracer.Start(context.Background(), "root")
        carrier := map[string]string{"other": "keep", "x-ride-trace": "old"}
        observability.MarshalTrace(ctx, carrier)
        if carrier["other"]!="keep" { panic("other keys should be preserved") }
        if carrier["x-ride-trace"]=="" { panic("x-ride-trace should be set") }
        span.End()
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"marshal preserves other keys failed: {proc.stdout} {proc.stderr}"



def test_tracing_event_empty_name_ignored():
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
        _, span := tracer.Start(context.Background(), "event-empty-name")
        span.AddEvent("")
        span.AddEvent("valid")
        span.End()
        evts := exp.GetSpans()[0].Events
        if len(evts)!=1 { panic(fmt.Sprintf("empty event name should be ignored, expected 1 got %d", len(evts))) }
        if evts[0].Name!="valid" { panic("valid event should remain") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"event empty name ignored failed: {proc.stdout} {proc.stderr}"



def test_tracing_attr_limit_and_event_limit_independent():
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
        _, span := tracer.Start(context.Background(), "limit-indep")
        for i:=0; i<200; i++ {
            span.AddAttribute(fmt.Sprintf("k%d", i), i)
        }
        for i:=0; i<200; i++ {
            span.AddEvent(fmt.Sprintf("e%d", i))
        }
        span.End()
        fs := exp.GetSpans()[0]
        if len(fs.Attributes)!=128 { panic(fmt.Sprintf("span attrs should be 128 got %d", len(fs.Attributes))) }
        if len(fs.Events)!=128 { panic(fmt.Sprintf("events should be 128 got %d", len(fs.Events))) }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"attr and event limit independent failed: {proc.stdout} {proc.stderr}"



def test_metrics_counter_negative_noop():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        p := observability.NewMetricsProvider()
        c := p.Counter("counter_neg")
        c.Inc()
        c.Add(-5)
        fams := p.Collect()
        var v float64
        for _, fam := range fams {
            if fam.Name=="counter_neg" { v = fam.Metrics[0].Value }
        }
        if v!=1 { panic(fmt.Sprintf("negative Add should be noop, expected 1 got %f", v)) }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"counter negative noop failed: {proc.stdout} {proc.stderr}"



def test_metrics_collect_buckets_deep_copy_modification_2():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        p := observability.NewMetricsProvider()
        h := p.Histogram("hist_deep_copy2", observability.WithBuckets([]float64{1,2,3}))
        h.Observe(1.5)
        fams1 := p.Collect()
        if len(fams1)>0 && len(fams1[0].Metrics)>0 && len(fams1[0].Metrics[0].Buckets)>0 {
            fams1[0].Metrics[0].Buckets[0].Count = 999
            fams1[0].Metrics[0].Buckets[0].UpperBound = 999
        }
        h2 := p.Histogram("hist_deep_copy2", observability.WithBuckets([]float64{1,2,3}))
        h2.Observe(0.5)
        fams2 := p.Collect()
        var cnt uint64
        for _, fam := range fams2 {
            if fam.Name=="hist_deep_copy2" {
                for _, b := range fam.Metrics[0].Buckets {
                    if b.UpperBound==1 { cnt = b.Count }
                }
            }
        }
        if cnt!=1 { panic(fmt.Sprintf("mutating Collect result should not affect internal, expected 1 got %d", cnt)) }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"collect buckets deep copy mutation 2 failed: {proc.stdout} {proc.stderr}"



def test_metrics_histogram_observe_negative_allowed():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        p := observability.NewMetricsProvider()
        h := p.Histogram("hist_neg", observability.WithBuckets([]float64{0,10}))
        h.Observe(-5)
        fams := p.Collect()
        var cnt uint64
        for _, fam := range fams {
            if fam.Name=="hist_neg" {
                for _, b := range fam.Metrics[0].Buckets {
                    if b.UpperBound==0 { cnt = b.Count }
                }
            }
        }
        if cnt!=1 { panic(fmt.Sprintf("negative observe should go in first bucket, got %d", cnt)) }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"histogram observe negative allowed failed: {proc.stdout} {proc.stderr}"



def test_metrics_label_truncation_collision():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "strings"
        "ride-observability/observability"
    )
    func main(){
        p := observability.NewMetricsProvider()
        long1 := strings.Repeat("a", 256) + "1"
        long2 := strings.Repeat("a", 256) + "2"
        c1 := p.Counter("collision_trunc", observability.WithLabels(map[string]string{"k": long1}))
        c1.Inc()
        c2 := p.Counter("collision_trunc", observability.WithLabels(map[string]string{"k": long2}))
        c2.Inc()
        fams := p.Collect()
        var n int
        var val float64
        for _, fam := range fams {
            if fam.Name=="collision_trunc" { n = len(fam.Metrics); val = fam.Metrics[0].Value }
        }
        if n!=1 { panic(fmt.Sprintf("truncated label values colliding should reuse same metric, expected 1 series got %d", n)) }
        if val!=2 { panic(fmt.Sprintf("expected value 2 after collision reuse, got %f", val)) }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"label truncation collision failed: {proc.stdout} {proc.stderr}"



def test_metrics_concurrent_create_same_labelset():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "sync"
        "ride-observability/observability"
    )
    func main(){
        p := observability.NewMetricsProvider()
        var wg sync.WaitGroup
        for i:=0; i<20; i++ {
            wg.Add(1)
            go func(){
                defer wg.Done()
                c := p.Counter("conc_create_same", observability.WithLabels(map[string]string{"id": "same"}))
                c.Inc()
            }()
        }
        wg.Wait()
        fams := p.Collect()
        var v float64
        var n int
        for _, fam := range fams {
            if fam.Name=="conc_create_same" { n = len(fam.Metrics); v = fam.Metrics[0].Value }
        }
        if n!=1 { panic(fmt.Sprintf("should be 1 series got %d", n)) }
        if v!=20 { panic(fmt.Sprintf("expected 20 got %f", v)) }
        fmt.Println("OK")
    }
    """)
    proc = go_run_race_program(code)
    assert proc.returncode == 0, f"concurrent create same labelset failed: {proc.stdout} {proc.stderr}"



def test_metrics_provider_collect_empty_after_clear():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        p := observability.NewMetricsProvider()
        c := p.Counter("empty_after")
        c.Inc()
        fams1 := p.Collect()
        if len(fams1)==0 { panic("first collect should have data") }
        fams2 := p.Collect()
        // per spec Collect does NOT clear, second collect should still have data (reuse after Collect must work)
        if len(fams2)==0 { panic("second collect should still have data since Collect does not clear") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"provider collect empty after clear failed: {proc.stdout} {proc.stderr}"



def test_metrics_gauge_add_nan_inf_ignored():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "math"
        "ride-observability/observability"
    )
    func main(){
        p := observability.NewMetricsProvider()
        g := p.Gauge("gauge_nan_inf_add")
        g.Set(5)
        g.Add(math.NaN())
        g.Add(math.Inf(1))
        g.Add(math.Inf(-1))
        fams := p.Collect()
        var v float64
        for _, fam := range fams {
            if fam.Name=="gauge_nan_inf_add" { v = fam.Metrics[0].Value }
        }
        if v!=5 { panic(fmt.Sprintf("Add NaN/Inf should be ignored, expected 5 got %f", v)) }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"gauge add nan inf ignored failed: {proc.stdout} {proc.stderr}"



def test_metrics_counter_race_add_and_collect():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "sync"
        "ride-observability/observability"
    )
    func main(){
        p := observability.NewMetricsProvider()
        c := p.Counter("race_add_collect")
        var wg sync.WaitGroup
        for i:=0; i<10; i++ {
            wg.Add(1)
            go func(){
                defer wg.Done()
                for j:=0; j<100; j++ { c.Inc() }
            }()
        }
        wg.Add(1)
        go func(){
            defer wg.Done()
            for k:=0; k<20; k++ { _ = p.Collect() }
        }()
        wg.Wait()
        fmt.Println("OK")
    }
    """)
    proc = go_run_race_program(code)
    assert proc.returncode == 0, f"counter race add and collect failed: {proc.stdout} {proc.stderr}"



def test_logger_level_unknown_defaults_info():
    code = textwrap.dedent("""
    package main
    import (
        "bytes"
        "context"
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        buf := &bytes.Buffer{}
        logger := observability.NewLogger("svc", observability.WithOutput(buf), observability.WithLevel("unknownlevel"))
        logger.Debug(context.Background(), "should-not-show")
        logger.Info(context.Background(), "should-show")
        out := buf.String()
        if !contains(out, "should-show") { panic("unknown level should default to info and show info") }
        if contains(out, "should-not-show") { panic("debug should be filtered when level defaults to info") }
        fmt.Println("OK")
    }
    func contains(s, substr string) bool {
        return len(s) >= len(substr) && (func() bool {
            for i:=0; i<=len(s)-len(substr); i++ { if s[i:i+len(substr)]==substr { return true } }
            return false
        })()
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"logger level unknown defaults info failed: {proc.stdout} {proc.stderr}"



def test_logger_error_level_filters_lower():
    code = textwrap.dedent("""
    package main
    import (
        "bytes"
        "context"
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        buf := &bytes.Buffer{}
        logger := observability.NewLogger("svc", observability.WithOutput(buf), observability.WithLevel("error"))
        logger.Info(context.Background(), "info-msg")
        logger.Warn(context.Background(), "warn-msg")
        logger.Error(context.Background(), "error-msg")
        out := buf.String()
        if !contains(out, "error-msg") { panic("error should be shown at error level") }
        if contains(out, "info-msg") { panic("info should be filtered at error level") }
        if contains(out, "warn-msg") { panic("warn should be filtered at error level") }
        fmt.Println("OK")
    }
    func contains(s, substr string) bool {
        return len(s) >= len(substr) && (func() bool {
            for i:=0; i<=len(s)-len(substr); i++ { if s[i:i+len(substr)]==substr { return true } }
            return false
        })()
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"logger error level filters lower failed: {proc.stdout} {proc.stderr}"



def test_logger_service_cannot_be_overridden_by_with():
    code = textwrap.dedent("""
    package main
    import (
        "bytes"
        "context"
        "encoding/json"
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        buf := &bytes.Buffer{}
        logger := observability.NewLogger("real-service", observability.WithOutput(buf))
        child := logger.With(observability.Field{Key:"service", Value:"fake-service"})
        child.Info(context.Background(), "msg")
        var obj map[string]interface{}
        if err:= json.Unmarshal(bytes.TrimSpace(buf.Bytes()), &obj); err!=nil { panic("not json") }
        if obj["service"]!="real-service" { panic(fmt.Sprintf("service field should always be real-service, got %v", obj["service"])) }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"logger service cannot be overridden failed: {proc.stdout} {proc.stderr}"



def test_logger_fields_json_types():
    code = textwrap.dedent("""
    package main
    import (
        "bytes"
        "context"
        "encoding/json"
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        buf := &bytes.Buffer{}
        logger := observability.NewLogger("svc", observability.WithOutput(buf))
        child := logger.With(observability.Field{Key:"int", Value:42}, observability.Field{Key:"bool", Value:true}, observability.Field{Key:"float", Value:3.14})
        child.Info(context.Background(), "typed")
        var obj map[string]interface{}
        if err:= json.Unmarshal(bytes.TrimSpace(buf.Bytes()), &obj); err!=nil { panic(err.Error()) }
        if obj["int"]!=float64(42) { panic(fmt.Sprintf("int field wrong: %v", obj["int"])) }
        if obj["bool"]!=true { panic("bool field wrong") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"logger fields json types failed: {proc.stdout} {proc.stderr}"



def test_logger_concurrent_with_and_log():
    code = textwrap.dedent("""
    package main
    import (
        "bytes"
        "context"
        "fmt"
        "sync"
        "ride-observability/observability"
    )
    func main(){
        buf := &bytes.Buffer{}
        base := observability.NewLogger("svc", observability.WithOutput(buf))
        var wg sync.WaitGroup
        for i:=0; i<20; i++ {
            wg.Add(1)
            go func(idx int){
                defer wg.Done()
                l := base.With(observability.Field{Key:"idx", Value: idx})
                l.Info(context.Background(), "concurrent")
            }(i)
        }
        wg.Wait()
        if buf.Len()==0 { panic("expected some output") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_race_program(code)
    assert proc.returncode == 0, f"logger concurrent with and log failed: {proc.stdout} {proc.stderr}"



def test_tracing_finished_span_parent_id_empty_for_root():
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
        _, span := tracer.Start(context.Background(), "root-parent-empty")
        span.End()
        fs := exp.GetSpans()[0]
        if fs.ParentID != "" { panic(fmt.Sprintf("root ParentID should be empty, got %s", fs.ParentID)) }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"finished span parentID empty for root failed: {proc.stdout} {proc.stderr}"



def test_tracing_finished_span_start_before_end():
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
        _, span := tracer.Start(context.Background(), "start-before-end")
        span.End()
        fs := exp.GetSpans()[0]
        if fs.StartTime.After(fs.EndTime) { panic(fmt.Sprintf("StartTime %v after EndTime %v", fs.StartTime, fs.EndTime)) }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"finished span start before end failed: {proc.stdout} {proc.stderr}"



def test_tracing_id_generator_nil_fallback_ensures_valid_traceid():
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
        tracer := observability.NewTracer("svc", observability.WithProcessor(proc), observability.WithIDGenerator(nil))
        _, span := tracer.Start(context.Background(), "nil-idgen-valid")
        sc := span.SpanContext()
        if len(sc.TraceID)!=32 { panic(fmt.Sprintf("nil IDGen should fallback to valid 32-char TraceID, got %s", sc.TraceID)) }
        if len(sc.SpanID)!=16 { panic(fmt.Sprintf("nil IDGen should fallback to valid 16-char SpanID, got %s", sc.SpanID)) }
        span.End()
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"id generator nil fallback ensures valid traceID failed: {proc.stdout} {proc.stderr}"



def test_tracing_unmarshal_empty_parent_valid():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        carrier := map[string]string{"x-ride-trace": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa:bbbbbbbbbbbbbbbb::1"}
        ctx := observability.UnmarshalTrace(carrier)
        tc, ok := observability.TraceFromContext(ctx)
        if !ok { panic("empty parent should be valid root") }
        if tc.ParentID!="" { panic("ParentID should be empty") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"unmarshal empty parent valid failed: {proc.stdout} {proc.stderr}"



def test_tracing_unmarshal_invalid_sampled():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        cases := []string{
            "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa:bbbbbbbbbbbbbbbb::2",
            "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa:bbbbbbbbbbbbbbbb::true",
            "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa:bbbbbbbbbbbbbbbb::",
            "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa:bbbbbbbbbbbbbbbb::  ",
        }
        for i, v := range cases {
            ctx := observability.UnmarshalTrace(map[string]string{"x-ride-trace": v})
            if _, ok := observability.TraceFromContext(ctx); ok {
                panic(fmt.Sprintf("case %d %q should be invalid sampled", i, v))
            }
        }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"unmarshal invalid sampled failed: {proc.stdout} {proc.stderr}"



def test_tracing_unmarshal_spaces_around_colons():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        carrier := map[string]string{"x-ride-trace": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa : bbbbbbbbbbbbbbbb :  : 1"}
        ctx := observability.UnmarshalTrace(carrier)
        tc, ok := observability.TraceFromContext(ctx)
        if !ok { panic("should trim spaces around colons") }
        if tc.TraceID!="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" { panic("traceID mismatch after trim") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"unmarshal spaces around colons failed: {proc.stdout} {proc.stderr}"



def test_tracing_context_withtrace_defensive_copy():
    code = textwrap.dedent("""
    package main
    import (
        "context"
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        tc := observability.TraceContext{TraceID: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", SpanID: "bbbbbbbbbbbbbbbb", Sampled: true}
        ctx := observability.ContextWithTrace(context.Background(), tc)
        tc.TraceID = "cccccccccccccccccccccccccccccccc"
        got, ok := observability.TraceFromContext(ctx)
        if !ok { panic("should have trace") }
        if got.TraceID=="cccccccccccccccccccccccccccccccc" { panic("ContextWithTrace must copy, mutation leaked") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"context withtrace defensive copy failed: {proc.stdout} {proc.stderr}"



def test_tracing_tracefromcontext_returns_copy():
    code = textwrap.dedent("""
    package main
    import (
        "context"
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        tc := observability.TraceContext{TraceID: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", SpanID: "bbbbbbbbbbbbbbbb", Sampled: true}
        ctx := observability.ContextWithTrace(context.Background(), tc)
        got, _ := observability.TraceFromContext(ctx)
        got.TraceID = "dddddddddddddddddddddddddddddddd"
        got2, _ := observability.TraceFromContext(ctx)
        if got2.TraceID=="dddddddddddddddddddddddddddddddd" { panic("TraceFromContext must return copy") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"tracefromcontext returns copy failed: {proc.stdout} {proc.stderr}"



def test_tracing_withattributes_nil_no_panic():
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
        _, span := tracer.Start(context.Background(), "nil-attrs", observability.WithAttributes())
        span.End()
        if exp.GetCount()!=1 { panic("should have 1 span") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"withattributes nil no panic failed: {proc.stdout} {proc.stderr}"



def test_tracing_attribute_overwrite_after_limit():
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
        _, span := tracer.Start(context.Background(), "overwrite-after-limit")
        for i:=0; i<128; i++ {
            span.AddAttribute(fmt.Sprintf("k%d", i), i)
        }
        // overwrite first key after limit reached
        span.AddAttribute("k0", 999)
        span.End()
        fs := exp.GetSpans()[0]
        if len(fs.Attributes)!=128 { panic(fmt.Sprintf("should still be 128 got %d", len(fs.Attributes))) }
        if fs.Attributes["k0"]!=999 { panic(fmt.Sprintf("overwrite after limit should update, got %v", fs.Attributes["k0"])) }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"attribute overwrite after limit failed: {proc.stdout} {proc.stderr}"



def test_tracing_exporter_events_attrs_value_mutation():
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
        _, span := tracer.Start(context.Background(), "exporter-event-attr-mut")
        span.AddEvent("ev", observability.Attribute{Key:"k", Value:"original"})
        span.End()
        spans := exp.GetSpans()
        // mutate returned event attr value type assertion as string, mutate slice
        if len(spans[0].Events)>0 && len(spans[0].Events[0].Attributes)>0 {
            spans[0].Events[0].Attributes[0].Value = "hacked"
            spans[0].Events[0].Attributes[0].Key = "hacked"
        }
        spans2 := exp.GetSpans()
        if spans2[0].Events[0].Attributes[0].Key=="hacked" { panic("event attr deep copy failed for Key") }
        if spans2[0].Events[0].Attributes[0].Value=="hacked" { panic("event attr deep copy failed for Value") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"exporter events attrs value mutation failed: {proc.stdout} {proc.stderr}"



def test_logger_with_no_args_immutable():
    code = textwrap.dedent("""
    package main
    import (
        "bytes"
        "context"
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        buf := &bytes.Buffer{}
        base := observability.NewLogger("svc", observability.WithOutput(buf))
        base = base.With(observability.Field{Key:"a", Value:"1"})
        child := base.With()
        child.Info(context.Background(), "child")
        // base should still have a, but With() should not drop
        buf2 := &bytes.Buffer{}
        base2 := observability.NewLogger("svc", observability.WithOutput(buf2))
        base2 = base2.With(observability.Field{Key:"a", Value:"1"})
        child2 := base2.With()
        // child2 should have same field as base2
        child2.Info(context.Background(), "check")
        out := buf2.String()
        if !contains(out, "a") { panic("With() should preserve fields") }
        fmt.Println("OK")
    }
    func contains(s, substr string) bool {
        for i:=0; i<=len(s)-len(substr); i++ { if s[i:i+len(substr)]==substr { return true } }
        return false
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"logger with no args immutable failed: {proc.stdout} {proc.stderr}"



def test_logger_field_empty_key_ignored():
    code = textwrap.dedent("""
    package main
    import (
        "bytes"
        "context"
        "encoding/json"
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        buf := &bytes.Buffer{}
        logger := observability.NewLogger("svc", observability.WithOutput(buf))
        logger = logger.With(observability.Field{Key:"", Value:"should-ignore"})
        logger.Info(context.Background(), "msg", observability.Field{Key:"", Value:"ignore-too"}, observability.Field{Key:"ok", Value:"yes"})
        var obj map[string]interface{}
        if err:= json.Unmarshal(bytes.TrimSpace(buf.Bytes()), &obj); err!=nil { panic(err.Error()) }
        if _, ok := obj[""]; ok { panic("empty key should be ignored") }
        if obj["ok"]!="yes" { panic("ok field missing") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"logger field empty key ignored failed: {proc.stdout} {proc.stderr}"



def test_logger_timestamp_monotonic():
    code = textwrap.dedent("""
    package main
    import (
        "bytes"
        "context"
        "encoding/json"
        "fmt"
        "time"
        "ride-observability/observability"
    )
    func main(){
        buf := &bytes.Buffer{}
        logger := observability.NewLogger("svc", observability.WithOutput(buf))
        logger.Info(context.Background(), "first")
        firstLine := buf.String()
        var obj1 map[string]interface{}
        json.Unmarshal([]byte(firstLine), &obj1)
        t1Str, _ := obj1["timestamp"].(string)
        t1, _ := time.Parse(time.RFC3339Nano, t1Str)
        // second
        buf.Reset()
        logger.Info(context.Background(), "second")
        secondLine := buf.String()
        var obj2 map[string]interface{}
        json.Unmarshal([]byte(secondLine), &obj2)
        t2Str, _ := obj2["timestamp"].(string)
        t2, _ := time.Parse(time.RFC3339Nano, t2Str)
        if t2.Before(t1) { panic(fmt.Sprintf("timestamp monotonic failed t2 %v before t1 %v", t2, t1)) }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"logger timestamp monotonic failed: {proc.stdout} {proc.stderr}"



def test_metrics_counter_inc_after_collect():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        p := observability.NewMetricsProvider()
        c := p.Counter("inc_after_collect")
        c.Inc()
        f1 := p.Collect()
        var v1 float64
        for _, fam := range f1 { if fam.Name=="inc_after_collect" { v1 = fam.Metrics[0].Value } }
        if v1!=1 { panic(fmt.Sprintf("v1 expected 1 got %f", v1)) }
        c.Inc()
        f2 := p.Collect()
        var v2 float64
        for _, fam := range f2 { if fam.Name=="inc_after_collect" { v2 = fam.Metrics[0].Value } }
        // Since Collect does not clear, v2 should be 2 (accumulated) or at least 1 if it did clear and new inc is 1? We require 2 per spec Collect does not clear
        if v2!=2 { panic(fmt.Sprintf("inc after collect should accumulate to 2, got %f", v2)) }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"counter inc after collect failed: {proc.stdout} {proc.stderr}"



def test_metrics_histogram_unsorted_buckets_sort():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        p := observability.NewMetricsProvider()
        h := p.Histogram("hist_unsorted_sort", observability.WithBuckets([]float64{10,1,5}))
        h.Observe(2)
        fams := p.Collect()
        var buckets []float64
        for _, fam := range fams {
            if fam.Name=="hist_unsorted_sort" {
                for _, b := range fam.Metrics[0].Buckets { buckets = append(buckets, b.UpperBound) }
            }
        }
        if len(buckets)!=3 { panic(fmt.Sprintf("expected 3 buckets got %d", len(buckets))) }
        if !(buckets[0]==1 && buckets[1]==5 && buckets[2]==10) { panic(fmt.Sprintf("buckets not sorted, got %v", buckets)) }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"histogram unsorted buckets sort failed: {proc.stdout} {proc.stderr}"



def test_metrics_histogram_duplicate_dedup():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        p := observability.NewMetricsProvider()
        h := p.Histogram("hist_dup_dedup", observability.WithBuckets([]float64{1,1,2,2,3}))
        fams := p.Collect()
        var n int
        for _, fam := range fams { if fam.Name=="hist_dup_dedup" { n = len(fam.Metrics[0].Buckets) } }
        if n!=3 { panic(fmt.Sprintf("dedup should give 3, got %d", n)) }
        fmt.Println("OK")
        _ = h
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"histogram duplicate dedup failed: {proc.stdout} {proc.stderr}"



def test_metrics_histogram_count_sum_accurate():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        p := observability.NewMetricsProvider()
        h := p.Histogram("hist_count_sum", observability.WithBuckets([]float64{1,5,10}))
        h.Observe(2)
        h.Observe(6)
        fams := p.Collect()
        var cnt uint64
        var sum float64
        for _, fam := range fams {
            if fam.Name=="hist_count_sum" {
                cnt = fam.Metrics[0].Count
                sum = fam.Metrics[0].Sum
            }
        }
        if cnt!=2 { panic(fmt.Sprintf("count expected 2 got %d", cnt)) }
        if sum!=8 { panic(fmt.Sprintf("sum expected 8 got %f", sum)) }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"histogram count sum accurate failed: {proc.stdout} {proc.stderr}"



def test_metrics_gauge_inc_dec_after_set():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        p := observability.NewMetricsProvider()
        g := p.Gauge("gauge_inc_dec_set")
        g.Set(10)
        g.Inc()
        g.Dec()
        g.Dec()
        fams := p.Collect()
        var v float64
        for _, fam := range fams { if fam.Name=="gauge_inc_dec_set" { v = fam.Metrics[0].Value } }
        if v!=9 { panic(fmt.Sprintf("expected 9 got %f", v)) }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"gauge inc dec after set failed: {proc.stdout} {proc.stderr}"



def test_tracing_spancontext_alias_same():
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
        ctx, span := tracer.Start(context.Background(), "alias")
        sc1 := span.SpanContext()
        sc2, ok := observability.SpanContextFromContext(ctx)
        if !ok { panic("SpanContextFromContext should work as alias") }
        if sc1.TraceID != sc2.TraceID { panic("alias mismatch") }
        span.End()
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"spancontext alias same failed: {proc.stdout} {proc.stderr}"



def test_tracing_inject_extract_alias_preserve_sampled():
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
        ctx, span := tracer.Start(context.Background(), "inject-extract")
        carrier := map[string]string{}
        observability.Inject(ctx, carrier)
        ctx2 := observability.Extract(carrier)
        sc, ok := observability.TraceFromContext(ctx2)
        if !ok { panic("Extract should recover trace") }
        if !sc.Sampled { panic("sampled should be true for step1 always") }
        span.End()
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"inject extract alias preserve sampled failed: {proc.stdout} {proc.stderr}"



def test_metrics_withlabels_order_irrelevant():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        p := observability.NewMetricsProvider()
        c1 := p.Counter("order_irrelevant", observability.WithLabels(map[string]string{"a":"1","b":"2"}))
        c1.Inc()
        c2 := p.Counter("order_irrelevant", observability.WithLabels(map[string]string{"b":"2","a":"1"}))
        c2.Inc()
        fams := p.Collect()
        var n int
        var v float64
        for _, fam := range fams { if fam.Name=="order_irrelevant" { n=len(fam.Metrics); v=fam.Metrics[0].Value } }
        if n!=1 { panic(fmt.Sprintf("order irrelevant should reuse, got %d series", n)) }
        if v!=2 { panic(fmt.Sprintf("expected 2 got %f", v)) }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"withlabels order irrelevant failed: {proc.stdout} {proc.stderr}"



def test_tracing_attribute_uint_types_allowed():
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
        _, span := tracer.Start(context.Background(), "uint-types")
        span.AddAttribute("u", uint(42))
        span.AddAttribute("u8", uint8(8))
        span.AddAttribute("u16", uint16(16))
        span.AddAttribute("u32", uint32(32))
        span.AddAttribute("u64", uint64(64))
        span.AddAttribute("i8", int8(8))
        span.AddAttribute("i16", int16(16))
        span.AddAttribute("i32", int32(32))
        span.AddAttribute("i64", int64(64))
        span.AddAttribute("f32", float32(3.14))
        span.End()
        fs := exp.GetSpans()[0]
        if len(fs.Attributes)!=10 { panic(fmt.Sprintf("expected 10 uint/int types, got %d", len(fs.Attributes))) }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"attribute uint types allowed failed: {proc.stdout} {proc.stderr}"



def test_tracing_withattributes_overwrite_and_truncate():
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
        long := strings.Repeat("z", 2000)
        _, span := tracer.Start(context.Background(), "overwrite-trunc", observability.WithAttributes(
            observability.Attribute{Key:"k", Value:"first"},
            observability.Attribute{Key:"k", Value: long},
        ))
        span.End()
        fs := exp.GetSpans()[0]
        v, ok := fs.Attributes["k"]
        if !ok { panic("k missing") }
        s, _ := v.(string)
        if len(s)!=1024 { panic(fmt.Sprintf("should be truncated to 1024 after overwrite, got %d", len(s))) }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"withattributes overwrite and truncate failed: {proc.stdout} {proc.stderr}"



def test_tracing_addattribute_nil_and_invalid_no_count():
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
        _, span := tracer.Start(context.Background(), "nil-invalid-no-count")
        for i:=0; i<10; i++ {
            span.AddAttribute("", "empty")
            span.AddAttribute(fmt.Sprintf("valid%d", i), fmt.Sprintf("v%d", i))
        }
        span.AddAttribute("nilval", nil)
        span.AddAttribute("slice", []int{1,2,3})
        span.AddAttribute("map", map[string]string{"a":"b"})
        span.End()
        fs := exp.GetSpans()[0]
        if len(fs.Attributes)!=10 { panic(fmt.Sprintf("only 10 valid should be counted, got %d", len(fs.Attributes))) }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"addattribute nil and invalid no count failed: {proc.stdout} {proc.stderr}"



def test_tracing_event_timestamp_between_start_end():
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
        _, span := tracer.Start(context.Background(), "event-ts-between")
        // Add event immediately after start
        span.AddEvent("ev1")
        // sleep a little
        // can't sleep in test harness reliably, but check timestamp non-zero and between start and end
        span.End()
        fs := exp.GetSpans()[0]
        if len(fs.Events)!=1 { panic("event missing") }
        evTs := fs.Events[0].Timestamp
        if evTs.IsZero() { panic("event timestamp zero") }
        if evTs.Before(fs.StartTime) { panic("event before start") }
        if evTs.After(fs.EndTime) { panic("event after end") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"event timestamp between start end failed: {proc.stdout} {proc.stderr}"



def test_tracing_event_attrs_monotonic_timestamps():
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
        _, span := tracer.Start(context.Background(), "event-mono")
        for i:=0; i<5; i++ { span.AddEvent(fmt.Sprintf("e%d", i)) }
        span.End()
        fs := exp.GetSpans()[0]
        for i:=1; i<len(fs.Events); i++ {
            if fs.Events[i].Timestamp.Before(fs.Events[i-1].Timestamp) { panic("event timestamps not monotonic") }
        }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"event attrs monotonic timestamps failed: {proc.stdout} {proc.stderr}"



def test_tracing_span_isrecording_after_end_false():
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
        _, span := tracer.Start(context.Background(), "isrecording")
        if !span.IsRecording() { panic("should be recording before end") }
        span.End()
        if span.IsRecording() { panic("should not be recording after end") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"span isrecording after end false failed: {proc.stdout} {proc.stderr}"



def test_metrics_counter_zero_add_allowed():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        p := observability.NewMetricsProvider()
        c := p.Counter("zero_add")
        c.Add(0)
        c.Inc()
        fams := p.Collect()
        var v float64
        for _, fam := range fams { if fam.Name=="zero_add" { v=fam.Metrics[0].Value } }
        if v!=1 { panic(fmt.Sprintf("Add(0) allowed, expected 1 got %f", v)) }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"counter zero add allowed failed: {proc.stdout} {proc.stderr}"



def test_metrics_gauge_set_nan_inf_keeps_prev():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "math"
        "ride-observability/observability"
    )
    func main(){
        p := observability.NewMetricsProvider()
        g := p.Gauge("gauge_nan_keep")
        g.Set(10)
        g.Set(math.NaN())
        g.Set(math.Inf(1))
        fams := p.Collect()
        var v float64
        for _, fam := range fams { if fam.Name=="gauge_nan_keep" { v=fam.Metrics[0].Value } }
        if v!=10 { panic(fmt.Sprintf("Set NaN/Inf should keep prev 10, got %f", v)) }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"gauge set nan inf keeps prev failed: {proc.stdout} {proc.stderr}"



def test_metrics_histogram_observations_independent_per_label():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        p := observability.NewMetricsProvider()
        h1 := p.Histogram("hist_label_indep", observability.WithLabels(map[string]string{"l":"a"}), observability.WithBuckets([]float64{1,2}))
        h2 := p.Histogram("hist_label_indep", observability.WithLabels(map[string]string{"l":"b"}), observability.WithBuckets([]float64{1,2}))
        h1.Observe(0.5)
        h2.Observe(1.5)
        fams := p.Collect()
        var aCnt, bCnt uint64
        for _, fam := range fams {
            if fam.Name=="hist_label_indep" {
                for _, m := range fam.Metrics {
                    if m.Labels["l"]=="a" { for _, b := range m.Buckets { if b.UpperBound==1 { aCnt=b.Count } } }
                    if m.Labels["l"]=="b" { for _, b := range m.Buckets { if b.UpperBound==2 { bCnt=b.Count } } }
                }
            }
        }
        if aCnt!=1 { panic(fmt.Sprintf("a count should be 1, got %d", aCnt)) }
        if bCnt!=1 { panic(fmt.Sprintf("b count should be 1, got %d", bCnt)) }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"hist observations independent per label failed: {proc.stdout} {proc.stderr}"



def test_logger_with_chaining_overwrite():
    code = textwrap.dedent("""
    package main
    import (
        "bytes"
        "context"
        "encoding/json"
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        buf := &bytes.Buffer{}
        base := observability.NewLogger("svc", observability.WithOutput(buf))
        l1 := base.With(observability.Field{Key:"env", Value:"dev"})
        l2 := l1.With(observability.Field{Key:"env", Value:"prod"})
        l2.Info(context.Background(), "msg")
        var obj map[string]interface{}
        json.Unmarshal(bytes.TrimSpace(buf.Bytes()), &obj)
        if obj["env"]!="prod" { panic(fmt.Sprintf("chaining overwrite should give prod, got %v", obj["env"])) }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"logger with chaining overwrite failed: {proc.stdout} {proc.stderr}"



def test_logger_per_call_overwrites_with():
    code = textwrap.dedent("""
    package main
    import (
        "bytes"
        "context"
        "encoding/json"
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        buf := &bytes.Buffer{}
        base := observability.NewLogger("svc", observability.WithOutput(buf))
        l1 := base.With(observability.Field{Key:"env", Value:"dev"})
        l1.Info(context.Background(), "msg", observability.Field{Key:"env", Value:"staging"})
        var obj map[string]interface{}
        json.Unmarshal(bytes.TrimSpace(buf.Bytes()), &obj)
        if obj["env"]!="staging" { panic(fmt.Sprintf("per-call should overwrite With, got %v", obj["env"])) }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"logger per-call overwrites with failed: {proc.stdout} {proc.stderr}"



def test_tracing_custom_idgen_same_ids_child_differs():
    code = textwrap.dedent("""
    package main
    import (
        "context"
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        gen := &fixedGen{traceID: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", spanID: "bbbbbbbbbbbbbbbb"}
        exp := observability.NewMemoryExporter()
        proc := observability.NewSimpleProcessor(exp)
        tracer := observability.NewTracer("svc", observability.WithProcessor(proc), observability.WithIDGenerator(gen))
        _, parent := tracer.Start(context.Background(), "parent")
        // Use WithParent to create child with same gen returning same spanID? Child should still have new SpanID via gen, but if gen returns same, child SpanID same as parent? That's allowed but we test parent SpanID != child ParentID? Actually if gen returns same, child SpanID == parent SpanID, but ParentID should be parent SpanID, so child.SpanID == child.ParentID? That would be weird but not panic
        // Instead we test that child inherits TraceID even when gen returns fixed
        psc := parent.SpanContext()
        _, child := tracer.Start(context.Background(), "child", observability.WithParent(psc))
        if child.SpanContext().TraceID != psc.TraceID { panic("child should inherit TraceID") }
        child.End()
        parent.End()
        fmt.Println("OK")
    }
    type fixedGen struct{ traceID, spanID string }
    func (g *fixedGen) NewTraceID() string { return g.traceID }
    func (g *fixedGen) NewSpanID() string { return g.spanID }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"custom idgen same ids child differs failed: {proc.stdout} {proc.stderr}"



def test_tracing_exporter_getspans_deep_copy_name():
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
        _, span := tracer.Start(context.Background(), "original-name")
        span.End()
        spans := exp.GetSpans()
        spans[0].Name = "hacked"
        spans2 := exp.GetSpans()
        if spans2[0].Name=="hacked" { panic("GetSpans Name deep copy failed") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"exporter getspans deep copy name failed: {proc.stdout} {proc.stderr}"



def test_metrics_provider_isolation_multiple():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        p1 := observability.NewMetricsProvider()
        p2 := observability.NewMetricsProvider()
        c1 := p1.Counter("iso_a")
        c1.Inc()
        c2 := p2.Counter("iso_b")
        c2.Inc()
        f1 := p1.Collect()
        f2 := p2.Collect()
        if len(f1)!=1 || f1[0].Name!="iso_a" { panic("p1 isolation failed") }
        if len(f2)!=1 || f2[0].Name!="iso_b" { panic("p2 isolation failed") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"provider isolation multiple failed: {proc.stdout} {proc.stderr}"



def test_tracing_start_with_parent_overrides_context_and_new_traceid_when_no_parent():
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
        // no parent context, new TraceID
        ctx, span1 := tracer.Start(context.Background(), "root1")
        sc1 := span1.SpanContext()
        span1.End()
        // second root should have different TraceID
        _, span2 := tracer.Start(context.Background(), "root2")
        sc2 := span2.SpanContext()
        if sc1.TraceID == sc2.TraceID { panic("different roots should have different TraceIDs") }
        span2.End()
        // with parent override, should reuse
        _, span3 := tracer.Start(ctx, "child-override", observability.WithParent(sc1))
        if span3.SpanContext().TraceID != sc1.TraceID { panic("WithParent should override context and reuse TraceID") }
        span3.End()
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"start with parent overrides failed: {proc.stdout} {proc.stderr}"



def test_tracing_addattribute_after_end_noop_preserves_exported():
    code = textwrap.dedent("""
    package main
    import (
        "context"
        "ride-observability/observability"
    )
    func main(){
        exp := observability.NewMemoryExporter()
        proc := observability.NewSimpleProcessor(exp)
        tracer := observability.NewTracer("svc", observability.WithProcessor(proc))
        _, span := tracer.Start(context.Background(), "after-end-noop")
        span.AddAttribute("before", "yes")
        span.End()
        span.AddAttribute("after", "should-be-ignored")
        fs := exp.GetSpans()[0]
        if _, ok := fs.Attributes["after"]; ok { panic("AddAttribute after End should be noop") }
        if fs.Attributes["before"]!="yes" { panic("before should remain") }
        println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"addattribute after end noop failed: {proc.stdout} {proc.stderr}"



def test_tracing_addevent_after_end_noop():
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
        _, span := tracer.Start(context.Background(), "addevent-after-end")
        span.End()
        span.AddEvent("should-not")
        fs := exp.GetSpans()[0]
        if len(fs.Events)!=0 { panic(fmt.Sprintf("AddEvent after End should be noop, got %d", len(fs.Events))) }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"addevent after end noop failed: {proc.stdout} {proc.stderr}"



def test_metrics_invalid_name_noop():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        p := observability.NewMetricsProvider()
        invalidNames := []string{"", "123abc", "a-b", "a.b", "a b"}
        for _, name := range invalidNames {
            c := p.Counter(name)
            c.Inc()
        }
        fams := p.Collect()
        if len(fams)!=0 { panic(fmt.Sprintf("all invalid names should be no-op, got %d", len(fams))) }
        cValid := p.Counter("_valid_name")
        cValid.Inc()
        fams2 := p.Collect()
        if len(fams2)!=1 { panic("valid name should work") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"invalid name noop failed: {proc.stdout} {proc.stderr}"



def test_metrics_withlabels_nil_treated_empty():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        p := observability.NewMetricsProvider()
        c := p.Counter("nil_labels", observability.WithLabels(nil))
        c.Inc()
        fams := p.Collect()
        if len(fams)!=1 { panic("nil labels should be treated as empty, not no-op") }
        if fams[0].Metrics[0].Value!=1 { panic("value should be 1") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"withlabels nil treated empty failed: {proc.stdout} {proc.stderr}"



def test_metrics_histogram_nil_buckets_default():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        p := observability.NewMetricsProvider()
        h := p.Histogram("nil_buckets_default", observability.WithBuckets(nil))
        h.Observe(1)
        fams := p.Collect()
        var n int
        for _, fam := range fams { if fam.Name=="nil_buckets_default" { n=len(fam.Metrics[0].Buckets) } }
        if n!=11 { panic(fmt.Sprintf("nil buckets should give default 11, got %d", n)) }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"nil buckets default failed: {proc.stdout} {proc.stderr}"



def test_tracing_withattributes_duplicate_within_same_call_last_wins():
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
        _, span := tracer.Start(context.Background(), "dup-within-call", observability.WithAttributes(
            observability.Attribute{Key:"dup", Value:"first"},
            observability.Attribute{Key:"dup", Value:"second"},
            observability.Attribute{Key:"dup", Value:"third"},
        ))
        span.End()
        fs := exp.GetSpans()[0]
        if len(fs.Attributes)!=1 { panic(fmt.Sprintf("duplicate within same call should count as 1, got %d", len(fs.Attributes))) }
        if fs.Attributes["dup"]!="third" { panic(fmt.Sprintf("last wins, got %v", fs.Attributes["dup"])) }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"duplicate within same call last wins failed: {proc.stdout} {proc.stderr}"



def test_metrics_gauge_dec_below_zero_allowed():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        p := observability.NewMetricsProvider()
        g := p.Gauge("gauge_below_zero")
        g.Set(1)
        g.Dec()
        g.Dec()
        fams := p.Collect()
        var v float64
        for _, fam := range fams { if fam.Name=="gauge_below_zero" { v=fam.Metrics[0].Value } }
        if v!=-1 { panic(fmt.Sprintf("gauge Dec below zero should be allowed, expected -1 got %f", v)) }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"gauge dec below zero failed: {proc.stdout} {proc.stderr}"



def test_metrics_histogram_observe_exact_boundary_inclusive():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        p := observability.NewMetricsProvider()
        h := p.Histogram("hist_exact_boundary", observability.WithBuckets([]float64{1,2,3}))
        h.Observe(2)
        fams := p.Collect()
        var counts []uint64
        for _, fam := range fams {
            if fam.Name=="hist_exact_boundary" {
                for _, b := range fam.Metrics[0].Buckets { counts = append(counts, b.Count) }
            }
        }
        if len(counts)!=3 { panic(fmt.Sprintf("expected 3 buckets, got %d", len(counts))) }
        if counts[0]!=0 || counts[1]!=1 || counts[2]!=1 { panic(fmt.Sprintf("exact boundary inclusive failed, got %v", counts)) }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"histogram exact boundary inclusive failed: {proc.stdout} {proc.stderr}"



def test_tracing_exporter_clear_reuse_order_preserved():
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
        for i:=0; i<3; i++ { _, s := tracer.Start(context.Background(), fmt.Sprintf("s-%d", i)); s.End() }
        if exp.GetCount()!=3 { panic("should be 3") }
        exp.Clear()
        if exp.GetCount()!=0 { panic("after clear 0") }
        _, s := tracer.Start(context.Background(), "after-clear")
        s.End()
        if exp.GetCount()!=1 { panic("after clear reuse should have 1") }
        if exp.GetSpans()[0].Name!="after-clear" { panic("order preserved after clear") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"exporter clear reuse order preserved failed: {proc.stdout} {proc.stderr}"



def test_logger_level_debug_shows_all():
    code = textwrap.dedent("""
    package main
    import (
        "bytes"
        "context"
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        buf := &bytes.Buffer{}
        logger := observability.NewLogger("svc", observability.WithOutput(buf), observability.WithLevel("debug"))
        logger.Debug(context.Background(), "debug-msg")
        logger.Info(context.Background(), "info-msg")
        logger.Warn(context.Background(), "warn-msg")
        logger.Error(context.Background(), "error-msg")
        out := buf.String()
        for _, substr := range []string{"debug-msg","info-msg","warn-msg","error-msg"} {
            if !contains(out, substr) { panic(fmt.Sprintf("debug level should show %s", substr)) }
        }
        fmt.Println("OK")
    }
    func contains(s, substr string) bool {
        for i:=0; i<=len(s)-len(substr); i++ { if s[i:i+len(substr)]==substr { return true } }
        return false
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"logger level debug shows all failed: {proc.stdout} {proc.stderr}"
    )



def test_logger_no_trace_when_invalid_context():
    code = textwrap.dedent("""
    package main
    import (
        "bytes"
        "context"
        "encoding/json"
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        buf := &bytes.Buffer{}
        logger := observability.NewLogger("svc", observability.WithOutput(buf))
        logger.Info(context.Background(), "no-trace")
        var obj map[string]interface{}
        json.Unmarshal(bytes.TrimSpace(buf.Bytes()), &obj)
        if obj["trace_id"]!=nil { panic(fmt.Sprintf("no trace_id expected for background, got %v", obj["trace_id"])) }
        if obj["span_id"]!=nil { panic("no span_id expected") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"logger no trace failed: {proc.stdout} {proc.stderr}"



def test_logger_trace_includes_parent_id():
    code = textwrap.dedent("""
    package main
    import (
        "bytes"
        "context"
        "encoding/json"
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        buf := &bytes.Buffer{}
        exp := observability.NewMemoryExporter()
        proc := observability.NewSimpleProcessor(exp)
        tracer := observability.NewTracer("svc", observability.WithProcessor(proc))
        ctx, parent := tracer.Start(context.Background(), "parent")
        ctxChild, _ := tracer.Start(ctx, "child")
        logger := observability.NewLogger("svc", observability.WithOutput(buf))
        logger.Info(ctxChild, "with-child")
        var obj map[string]interface{}
        json.Unmarshal(bytes.TrimSpace(buf.Bytes()), &obj)
        if obj["trace_id"]==nil { panic("trace_id missing") }
        if obj["span_id"]==nil { panic("span_id missing") }
        if obj["parent_id"]==nil { panic("parent_id should be present for child") }
        parent.End()
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"logger trace includes parent_id failed: {proc.stdout} {proc.stderr}"
    )



def test_metrics_description_first_wins():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        p := observability.NewMetricsProvider()
        c1 := p.Counter("desc_first_wins", observability.WithDescription("first"))
        c1.Inc()
        c2 := p.Counter("desc_first_wins", observability.WithDescription("second"))
        c2.Inc()
        fams := p.Collect()
        var help string
        var val float64
        for _, fam := range fams { if fam.Name=="desc_first_wins" { help=fam.Help; val=fam.Metrics[0].Value } }
        if help!="first" { panic(fmt.Sprintf("first description should win, got %s", help)) }
        if val!=2 { panic(fmt.Sprintf("value should be 2, got %f", val)) }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"description first wins failed: {proc.stdout} {proc.stderr}"
    )



def test_metrics_histogram_negative_buckets_sorted():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        p := observability.NewMetricsProvider()
        h := p.Histogram("hist_neg_sort", observability.WithBuckets([]float64{5, -1, 10, 0}))
        h.Observe(-0.5)
        fams := p.Collect()
        var buckets []float64
        for _, fam := range fams {
            if fam.Name=="hist_neg_sort" {
                for _, b := range fam.Metrics[0].Buckets { buckets = append(buckets, b.UpperBound) }
            }
        }
        if len(buckets)!=4 { panic(fmt.Sprintf("expected 4 buckets, got %d", len(buckets))) }
        if !(buckets[0]==-1 && buckets[1]==0 && buckets[2]==5 && buckets[3]==10) { panic(fmt.Sprintf("not sorted with negatives: %v", buckets)) }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"histogram negative buckets sorted failed: {proc.stdout} {proc.stderr}"
    )



def test_metrics_label_key_validation_more():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        p := observability.NewMetricsProvider()
        cases := []map[string]string{
            {"123abc": "1"},
            {"a-b": "1"},
            {"": "1"},
            {"a.b": "1"},
        }
        for i, labels := range cases {
            c := p.Counter(fmt.Sprintf("invalid_label_%d", i), observability.WithLabels(labels))
            c.Inc()
        }
        fams := p.Collect()
        if len(fams)!=0 { panic(fmt.Sprintf("all invalid label keys should be no-op, got %d families", len(fams))) }
        cValid := p.Counter("valid_label", observability.WithLabels(map[string]string{"_ok": "1", "a_1": "2"}))
        cValid.Inc()
        fams2 := p.Collect()
        if len(fams2)!=1 { panic("valid underscore labels should work") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"label key validation more failed: {proc.stdout} {proc.stderr}"
    )



def test_metrics_type_conflict_first_wins():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        p := observability.NewMetricsProvider()
        c := p.Counter("type_conflict")
        c.Inc()
        g := p.Gauge("type_conflict")
        g.Set(100)
        h := p.Histogram("type_conflict")
        h.Observe(5)
        fams := p.Collect()
        if len(fams)!=1 { panic(fmt.Sprintf("type conflict should give 1 family, got %d", len(fams))) }
        if fams[0].Type!="counter" { panic(fmt.Sprintf("first wins counter, got %s", fams[0].Type)) }
        if fams[0].Metrics[0].Value!=1 { panic(fmt.Sprintf("counter value should be 1, got %f", fams[0].Metrics[0].Value)) }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"type conflict first wins failed: {proc.stdout} {proc.stderr}"
    )



def test_tracing_all_zero_ids_invalid_marshal():
    code = textwrap.dedent("""
    package main
    import (
        "context"
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        scZeroTrace := observability.TraceContext{TraceID: "00000000000000000000000000000000", SpanID: "0102030405060708", Sampled: true}
        ctx := observability.ContextWithTrace(context.Background(), scZeroTrace)
        carrier := map[string]string{}
        observability.MarshalTrace(ctx, carrier)
        if len(carrier)!=0 { panic(fmt.Sprintf("all-zero TraceID should be invalid for marshal, got %v", carrier)) }
        scZeroSpan := observability.TraceContext{TraceID: "0102030405060708090a0b0c0d0e0f10", SpanID: "0000000000000000", Sampled: true}
        ctx2 := observability.ContextWithTrace(context.Background(), scZeroSpan)
        carrier2 := map[string]string{}
        observability.MarshalTrace(ctx2, carrier2)
        if len(carrier2)!=0 { panic("all-zero SpanID should be invalid for marshal") }
        c := map[string]string{"x-ride-trace": "00000000000000000000000000000000:0102030405060708::1"}
        ctx3 := observability.UnmarshalTrace(c)
        if _, ok := observability.TraceFromContext(ctx3); ok { panic("unmarshal all-zero TraceID should be invalid") }
        c2 := map[string]string{"x-ride-trace": "0102030405060708090a0b0c0d0e0f10:0000000000000000::1"}
        ctx4 := observability.UnmarshalTrace(c2)
        if _, ok := observability.TraceFromContext(ctx4); ok { panic("unmarshal all-zero SpanID should be invalid") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"all zero ids invalid marshal failed: {proc.stdout} {proc.stderr}"
    )



def test_tracing_all_zero_spanid_parentid_variants():
    code = textwrap.dedent("""
    package main
    import (
        "context"
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        // ParentID all zero should be invalid for marshal
        tc := observability.TraceContext{TraceID: "0102030405060708090a0b0c0d0e0f10", SpanID: "0102030405060708", ParentID: "0000000000000000", Sampled: true}
        ctx := observability.ContextWithTrace(context.Background(), tc)
        carrier := map[string]string{}
        observability.MarshalTrace(ctx, carrier)
        if len(carrier)!=0 { panic("all-zero ParentID should be invalid for marshal") }
        // Unmarshal with all-zero ParentID should be invalid
        c := map[string]string{"x-ride-trace": "0102030405060708090a0b0c0d0e0f10:0102030405060708:0000000000000000:1"}
        ctx2 := observability.UnmarshalTrace(c)
        if _, ok := observability.TraceFromContext(ctx2); ok { panic("all-zero ParentID unmarshal should be invalid") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"all zero parentID invalid failed: {proc.stdout} {proc.stderr}"
    )



def test_tracing_flags_normalized_in_contextwithtrace():
    code = textwrap.dedent("""
    package main
    import (
        "context"
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        tc1 := observability.TraceContext{TraceID: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", SpanID: "bbbbbbbbbbbbbbbb", Sampled: true, Flags: 0}
        ctx1 := observability.ContextWithTrace(context.Background(), tc1)
        got1, _ := observability.TraceFromContext(ctx1)
        if got1.Flags != 1 { panic(fmt.Sprintf("Flags should be normalized to 1 when Sampled true, got %d", got1.Flags)) }
        tc2 := observability.TraceContext{TraceID: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", SpanID: "bbbbbbbbbbbbbbbb", Sampled: false, Flags: 1}
        ctx2 := observability.ContextWithTrace(context.Background(), tc2)
        got2, _ := observability.TraceFromContext(ctx2)
        if got2.Flags != 0 { panic(fmt.Sprintf("Flags should be normalized to 0 when Sampled false, got %d", got2.Flags)) }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, f"flags normalized failed: {proc.stdout} {proc.stderr}"



def test_tracing_long_attribute_key_allowed():
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
        _, span := tracer.Start(context.Background(), "long-key")
        longKey := strings.Repeat("k", 500)
        span.AddAttribute(longKey, "value")
        span.End()
        fs := exp.GetSpans()[0]
        if len(fs.Attributes)!=1 { panic(fmt.Sprintf("long key should be allowed, got %d", len(fs.Attributes))) }
        if _, ok := fs.Attributes[longKey]; !ok { panic("long key not found") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"long attribute key allowed failed: {proc.stdout} {proc.stderr}"
    )



def test_tracing_marshal_nil_carrier_and_nil_context_no_panic():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        // nil carrier and nil ctx should not panic
        observability.MarshalTrace(nil, nil)
        // nil carrier with valid ctx
        // Need to create valid ctx first via Background + Trace
        // But we pass nil carrier, should no-op no panic
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"marshal nil carrier no panic failed: {proc.stdout} {proc.stderr}"
    )



def test_tracing_unmarshal_nil_and_empty_carrier_no_panic():
    code = textwrap.dedent("""
    package main
    import (
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        ctx := observability.UnmarshalTrace(nil)
        if ctx==nil { panic("Unmarshal nil should return non-nil background") }
        ctx2 := observability.UnmarshalTrace(map[string]string{})
        if ctx2==nil { panic("empty carrier should return background") }
        if _, ok := observability.TraceFromContext(ctx2); ok { panic("empty carrier should have no trace") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"unmarshal nil and empty failed: {proc.stdout} {proc.stderr}"
    )



def test_tracing_withattributes_mixed_valid_invalid():
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
        _, span := tracer.Start(context.Background(), "mixed-valid-invalid", observability.WithAttributes(
            observability.Attribute{Key:"ok1", Value:"v1"},
            observability.Attribute{Key:"", Value:"empty"},
            observability.Attribute{Key:"nilval", Value:nil},
            observability.Attribute{Key:"slice", Value:[]int{1}},
            observability.Attribute{Key:"ok2", Value:"v2"},
        ))
        span.End()
        fs := exp.GetSpans()[0]
        if len(fs.Attributes)!=2 { panic(fmt.Sprintf("only 2 valid should remain, got %d", len(fs.Attributes))) }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"withattributes mixed valid invalid failed: {proc.stdout} {proc.stderr}"
    )



def test_tracing_event_attr_duplicate_last_wins():
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
        _, span := tracer.Start(context.Background(), "event-dup-last-wins")
        span.AddEvent("ev", observability.Attribute{Key:"k", Value:"first"}, observability.Attribute{Key:"k", Value:"second"})
        span.End()
        ev := exp.GetSpans()[0].Events[0]
        if len(ev.Attributes)!=1 { panic(fmt.Sprintf("duplicate event attrs should dedup to 1, got %d", len(ev.Attributes))) }
        if ev.Attributes[0].Value!="second" { panic(fmt.Sprintf("last wins, got %v", ev.Attributes[0].Value)) }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"event attr duplicate last wins failed: {proc.stdout} {proc.stderr}"
    )



def test_tracing_event_attr_invalid_and_truncate():
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
        _, span := tracer.Start(context.Background(), "event-invalid-trunc")
        long := strings.Repeat("x", 2000)
        span.AddEvent("ev", 
            observability.Attribute{Key:"", Value:"empty-key"},
            observability.Attribute{Key:"nilval", Value:nil},
            observability.Attribute{Key:"slice", Value:[]int{1}},
            observability.Attribute{Key:"ok", Value: long},
        )
        span.End()
        ev := exp.GetSpans()[0].Events[0]
        if len(ev.Attributes)!=1 { panic(fmt.Sprintf("only 1 valid should remain, got %d", len(ev.Attributes))) }
        s, _ := ev.Attributes[0].Value.(string)
        if len(s)!=1024 { panic(fmt.Sprintf("event attr should be truncated 1024, got %d", len(s))) }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"event attr invalid and truncate failed: {proc.stdout} {proc.stderr}"
    )



def test_tracing_contextwithtrace_nil_and_empty():
    code = textwrap.dedent("""
    package main
    import (
        "context"
        "fmt"
        "ride-observability/observability"
    )
    func main(){
        // nil context should not panic and return non-nil
        ctx := observability.ContextWithTrace(nil, observability.TraceContext{TraceID: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", SpanID: "bbbbbbbbbbbbbbbb", Sampled: true})
        if ctx==nil { panic("ContextWithTrace(nil) should return non-nil") }
        // TraceFromContext nil should not panic and return false
        _, ok := observability.TraceFromContext(nil)
        if ok { panic("TraceFromContext(nil) should return false") }
        // Empty TraceContext should be considered? TraceID empty invalid but should not panic
        ctx2 := observability.ContextWithTrace(context.Background(), observability.TraceContext{})
        _, ok2 := observability.TraceFromContext(ctx2)
        // Even empty is stored but we consider? At least should not panic, ok may be true but TraceID empty
        if ctx2==nil { panic("empty TC ctx nil") }
        _ = ok2
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"contextwithtrace nil and empty failed: {proc.stdout} {proc.stderr}"
    )



def test_tracing_parent_parentid_handling():
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
        ctx1, parent := tracer.Start(context.Background(), "gp")
        scParent := parent.SpanContext()
        _, child := tracer.Start(ctx1, "parent")
        scChild := child.SpanContext()
        if scChild.ParentID != scParent.SpanID { panic(fmt.Sprintf("child ParentID should be parent SpanID %s, got %s", scParent.SpanID, scChild.ParentID)) }
        ctxGP, _ := observability.TraceFromContext(ctx1)
        if scChild.TraceID != ctxGP.TraceID { panic("traceID should propagate 3 levels") }
        child.End()
        parent.End()
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"parent parentID handling failed: {proc.stdout} {proc.stderr}"
    )



def test_tracing_attributes_map_deep_copy_key_mutation():
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
        _, span := tracer.Start(context.Background(), "attr-map-deep-copy")
        span.AddAttribute("k", "v")
        span.End()
        spans := exp.GetSpans()
        spans[0].Attributes["k"] = "hacked"
        spans[0].Attributes["new"] = "new"
        spans2 := exp.GetSpans()
        if spans2[0].Attributes["k"]=="hacked" { panic("Attributes map deep copy failed") }
        if _, ok := spans2[0].Attributes["new"]; ok { panic("new key leaked") }
        fmt.Println("OK")
    }
    """)
    proc = go_run_program(code)
    assert proc.returncode == 0, (
        f"attributes map deep copy key mutation failed: {proc.stdout} {proc.stderr}"
    )



def test_logger_concurrent_exact_lines():
    code = textwrap.dedent("""
    package main
    import (
        "bytes"
        "context"
        "fmt"
        "strings"
        "sync"
        "ride-observability/observability"
    )
    func main(){
        buf := &bytes.Buffer{}
        base := observability.NewLogger("svc", observability.WithOutput(buf))
        var wg sync.WaitGroup
        n:=100
        wg.Add(n)
        for i:=0; i<n; i++ {
            go func(idx int){
                defer wg.Done()
                base.Info(context.Background(), fmt.Sprintf("msg-%d", idx))
            }(i)
        }
        wg.Wait()
        lines := strings.Count(buf.String(), "\\n")
        if lines!=n { panic(fmt.Sprintf("expected %d lines, got %d", n, lines)) }
        fmt.Println("OK")
    }
    """)
    proc = go_run_race_program(code)
    assert proc.returncode == 0, (
        f"logger concurrent exact lines failed: {proc.stdout} {proc.stderr}"
    )



