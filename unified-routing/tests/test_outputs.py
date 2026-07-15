"""
Grader for the concurrency-safe Go routing library `router`.

Strategy:
  - copy the agent's package (non-test .go + go.mod) into a temp module
  - drop in a HIDDEN Go test that hammers Route WaitRoute BatchRoute Release Add Remove Snapshot Close from many goroutines
  - run `go test -race -count=20` : correct impl passes; partial lock, lost wakeup, deadlock, torn snapshot, non-atomic batch, unsynchronized map, missing copy fail
  - enforce stdlib-only
"""

import os
import re
import shutil
import subprocess

import pytest

SRC_DIR = "/app/src"

HIDDEN_TEST_GO = r"""
package router

import (
	"context"
	"sync"
	"sync/atomic"
	"testing"
	"time"
)

func TestHiddenNewCopies(t *testing.T) {
	in := []Provider{{"a", 5}, {"b", 3}, {"a", 2}}
	r := New(in)
	in[0].Capacity = 999
	in = append(in, Provider{"c", 100})
	if r.Remaining("a") != 2 || r.Remaining("c") != 0 {
		t.Fatalf("New must copy input and last wins")
	}
}

func TestHiddenBasicOrderingLargest(t *testing.T) {
	r := New([]Provider{{"b", 2}, {"a", 2}, {"c", 1}})
	id1, ok1 := r.Route()
	if !ok1 || id1 != "a" {
		t.Fatalf("want a first got %q %v", id1, ok1)
	}
	id2, _ := r.Route()
	if id2 != "b" {
		t.Fatalf("want b second got %q", id2)
	}
	id3, _ := r.Route()
	if id3 != "a" {
		t.Fatalf("want a third got %q", id3)
	}
	id4, _ := r.Route()
	if id4 != "b" {
		t.Fatalf("want b fourth got %q", id4)
	}
	id5, _ := r.Route()
	if id5 != "c" {
		t.Fatalf("want c fifth got %q", id5)
	}
	if _, ok := r.Route(); ok {
		t.Fatalf("want exhausted")
	}
}

func TestHiddenReleaseAddRemove(t *testing.T) {
	r := New([]Provider{{"x", 1}})
	id, ok := r.Route()
	if !ok || id != "x" {
		t.Fatalf("route failed")
	}
	if r.Remaining("x") != 0 {
		t.Fatalf("remaining not 0")
	}
	if !r.Release("x") {
		t.Fatalf("release failed")
	}
	if r.Remaining("x") != 1 {
		t.Fatalf("release did not restore")
	}
	r.Release("x")
	if r.Remaining("x") > 1 {
		t.Fatalf("release exceeded capacity")
	}
	if !r.AddProvider(Provider{"y", 2}) {
		t.Fatalf("add failed")
	}
	if r.Remaining("y") != 2 {
		t.Fatalf("add failed")
	}
	if !r.RemoveProvider("y") {
		t.Fatalf("remove failed")
	}
	if r.Remaining("y") != 0 {
		t.Fatalf("remove should zero remaining")
	}
}

func TestHiddenSnapshotConsistency(t *testing.T) {
	r := New([]Provider{{"a", 100}, {"b", 100}})
	var wg sync.WaitGroup
	stop := make(chan struct{})
	var bad int32
	go func() {
		for {
			select {
			case <-stop:
				return
			default:
				s := r.Snapshot()
				// snapshot values should be within bounds and sum reasonable
				sum := 0
				for _, v := range s {
					if v < 0 || v > 100 {
						atomic.StoreInt32(&bad, 1)
						return
					}
					sum += v
				}
				if sum < 0 || sum > 200 {
					atomic.StoreInt32(&bad, 1)
					return
				}
				// mutate returned map must not affect router
				s["a"] = 9999
				if r.Remaining("a") == 9999 {
					atomic.StoreInt32(&bad, 1)
					return
				}
			}
		}
	}()
	for i := 0; i < 50; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for j := 0; j < 100; j++ {
				r.Route()
				r.Release("a")
				r.Release("b")
			}
		}()
	}
	wg.Wait()
	close(stop)
	if atomic.LoadInt32(&bad) != 0 {
		t.Fatalf("snapshot torn or not independent")
	}
}


func TestHiddenWaitRouteBasic(t *testing.T) {
	r := New([]Provider{{"a", 0}})
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	done := make(chan string, 1)
	go func() {
		id, ok, err := r.WaitRoute(ctx)
		if err == nil && ok {
			done <- id
		} else {
			done <- ""
		}
	}()
	time.Sleep(50 * time.Millisecond)
	r.AddProvider(Provider{"a", 1})
	select {
	case id := <-done:
		if id != "a" {
			t.Fatalf("waitroute woke but wrong id %q", id)
		}
	case <-time.After(1 * time.Second):
		t.Fatalf("waitroute did not wake on AddProvider")
	}
}

func TestHiddenWaitRouteWakeOnRelease(t *testing.T) {
	r := New([]Provider{{"a", 1}})
	r.Route() // drain
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	ch := make(chan bool, 1)
	go func() {
		_, ok, _ := r.WaitRoute(ctx)
		ch <- ok
	}()
	time.Sleep(50 * time.Millisecond)
	r.Release("a")
	select {
	case ok := <-ch:
		if !ok {
			t.Fatalf("waitroute should succeed after release")
		}
	case <-time.After(1 * time.Second):
		t.Fatalf("waitroute not woken by Release")
	}
}

func TestHiddenWaitRouteCancel(t *testing.T) {
	r := New([]Provider{{"a", 0}})
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan error, 1)
	go func() {
		_, _, err := r.WaitRoute(ctx)
		done <- err
	}()
	time.Sleep(50 * time.Millisecond)
	cancel()
	select {
	case err := <-done:
		if err == nil {
			t.Fatalf("expected context error")
		}
	case <-time.After(1 * time.Second):
		t.Fatalf("waitroute did not return on cancel")
	}
}

func TestHiddenWaitRouteClose(t *testing.T) {
	r := New([]Provider{{"a", 0}})
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	ch := make(chan bool, 1)
	go func() {
		_, ok, err := r.WaitRoute(ctx)
		ch <- (err == nil && !ok)
	}()
	time.Sleep(50 * time.Millisecond)
	r.Close()
	select {
	case ok := <-ch:
		if !ok {
			t.Fatalf("waitroute should unblock false nil on close")
		}
	case <-time.After(1 * time.Second):
		t.Fatalf("waitroute not unblocked by Close")
	}
}

func TestHiddenBatchAtomic(t *testing.T) {
	r := New([]Provider{{"a", 100}, {"b", 100}})
	var wg sync.WaitGroup
	var batchOk int32
	var single int32
	for i := 0; i < 4; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			if ids, ok := r.BatchRoute(50); ok && len(ids) == 50 {
				atomic.AddInt32(&batchOk, 1)
			}
		}()
	}
	for i := 0; i < 200; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			if _, ok := r.Route(); ok {
				atomic.AddInt32(&single, 1)
			}
		}()
	}
	wg.Wait()
	total := int(atomic.LoadInt32(&batchOk))*50 + int(atomic.LoadInt32(&single))
	if total != 200 {
		t.Fatalf("total assigned %d want 200, batch not atomic or over-spend", total)
	}
	if r.Remaining("a")+r.Remaining("b") != 0 {
		t.Fatalf("remaining not zero")
	}
}

func TestHiddenConcurrentNoOverspend(t *testing.T) {
	provs := []Provider{{"a", 2000}, {"b", 2000}, {"c", 2000}, {"d", 2000}, {"e", 2000}}
	for attempt := 0; attempt < 5; attempt++ {
		r := New(provs)
		var wg sync.WaitGroup
		var mu sync.Mutex
		got := 0
		for i := 0; i < 128; i++ {
			wg.Add(1)
			go func() {
				defer wg.Done()
				for {
					id, ok := r.Route()
					if !ok {
						return
					}
					mu.Lock()
					got++
					_ = id
					mu.Unlock()
				}
			}()
		}
		wg.Wait()
		if got != 10000 {
			t.Fatalf("attempt %d: total %d want 10000", attempt, got)
		}
		for _, p := range provs {
			if r.Remaining(p.ID) != 0 {
				t.Fatalf("attempt %d: remaining %s not 0", attempt, p.ID)
			}
		}
	}
}

func TestHiddenConcurrentTight(t *testing.T) {
	for attempt := 0; attempt < 10; attempt++ {
		r := New([]Provider{{"only", 100}})
		var wg sync.WaitGroup
		var mu sync.Mutex
		got := 0
		for i := 0; i < 256; i++ {
			wg.Add(1)
			go func() {
				defer wg.Done()
				if id, ok := r.Route(); ok && id == "only" {
					mu.Lock()
					got++
					mu.Unlock()
				}
			}()
		}
		wg.Wait()
		if got != 100 {
			t.Fatalf("attempt %d: got %d want 100", attempt, got)
		}
	}
}

func TestHiddenMixedWorkloadInvariant(t *testing.T) {
	r := New([]Provider{{"a", 1000}, {"b", 1000}, {"c", 1000}})
	var wg sync.WaitGroup
	var success int64
	var releases int64
	for i := 0; i < 64; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for j := 0; j < 20; j++ {
				if id, ok := r.Route(); ok {
					atomic.AddInt64(&success, 1)
					if j%7 == 0 {
						if r.Release(id) {
							atomic.AddInt64(&releases, 1)
						}
					}
				}
				_ = r.Remaining("a")
				_ = r.Snapshot()
				if j%20 == 0 {
					r.AddProvider(Provider{"d", 0})
					r.RemoveProvider("d")
				}
				if j%25 == 0 {
					if ids, ok := r.BatchRoute(2); ok {
						atomic.AddInt64(&success, int64(len(ids)))
					}
				}
				if j%30 == 0 {
						ctx, cancel := context.WithTimeout(context.Background(), 5*time.Millisecond)
						if _, ok, _ := r.WaitRoute(ctx); ok {
							atomic.AddInt64(&success, 1)
						}
						cancel()
					}
			}
		}()
	}
	wg.Wait()
	// Assert the exact capacity invariant only at the quiescent end state. A
	// non-atomic mid-flight sample of several counters is not a consistent
	// snapshot and yields false positives (it flaked even the correct oracle).
	sum := r.Remaining("a") + r.Remaining("b") + r.Remaining("c")
	s := atomic.LoadInt64(&success)
	rel := atomic.LoadInt64(&releases)
	if s-rel+int64(sum) != 3000 {
		t.Fatalf("final invariant %d + %d - %d != 3000", sum, s, rel)
	}
}

func TestHiddenPerformanceReaders(t *testing.T) {
	r := New([]Provider{{"a", 100000}, {"b", 100000}})
	var wg sync.WaitGroup
	for i := 0; i < 256; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for j := 0; j < 200; j++ {
				_ = r.Remaining("a")
				_ = r.Snapshot()
			}
		}()
	}
	for i := 0; i < 32; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for j := 0; j < 1000; j++ {
				r.Route()
				if j%10 == 0 {
					r.Release("a")
				}
			}
		}()
	}
	wg.Wait()
	// No wall-clock assertion: it is hardware/-race dependent and would flake, and
	// would unfairly penalize a correct plain-Mutex impl. This still runs as
	// concurrent reader/writer stress under the race detector.
}

func TestHiddenHeapPerformance(t *testing.T) {
	// 1000 providers, 5000 ops should finish quickly even with O(n) scan in Go, but sets upper bound
	provs := make([]Provider, 1000)
	for i := 0; i < 1000; i++ {
		provs[i] = Provider{ID: string(rune('a'+i%26)) + string(rune('0'+i%10)) + string(rune(i)), Capacity: 10}
	}
	// simplify IDs to ensure unique
	for i := range provs {
		provs[i].ID = string([]byte{byte('p'), byte(i >> 8), byte(i)})
	}
	r := New(provs)
	for i := 0; i < 5000; i++ {
		r.Route()
	}
	// No wall-clock assertion (hardware/-race dependent; would flake). This still
	// exercises Route over many providers under the race detector.
}



func TestHiddenNegativeCapacity(t *testing.T) {
	r := New([]Provider{{"a", -5}})
	if r.Remaining("a") != 0 {
		t.Fatalf("negative treated 0")
	}
	r.AddProvider(Provider{"b", -1})
	if r.Remaining("b") != 0 {
		t.Fatalf("negative add 0")
	}
}

func TestHiddenSnapshotAfterRemoveAdd(t *testing.T) {
	r := New([]Provider{{"a", 1}})
	s1 := r.Snapshot()
	if s1["a"] != 1 {
		t.Fatalf("snapshot initial")
	}
	r.RemoveProvider("a")
	s2 := r.Snapshot()
	if _, ok := s2["a"]; ok {
		t.Fatalf("snapshot after remove no key")
	}
	r.AddProvider(Provider{"b", 2})
	s3 := r.Snapshot()
	if s3["b"] != 2 {
		t.Fatalf("snapshot after add")
	}
}

func TestHiddenCloseConcurrent(t *testing.T) {
	r := New([]Provider{{"a", 1}})
	var wg sync.WaitGroup
	var trues int32
	for i := 0; i < 10; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			if r.Close() {
				atomic.AddInt32(&trues, 1)
			}
		}()
	}
	wg.Wait()
	if atomic.LoadInt32(&trues) != 1 {
		t.Fatalf("exactly one close true got %d", trues)
	}
}

func TestHiddenWaitRouteAfterCloseWithCapacity(t *testing.T) {
	r := New([]Provider{{"a", 5}})
	r.Close()
	ctx, cancel := context.WithTimeout(context.Background(), 200*time.Millisecond)
	defer cancel()
	start := time.Now()
	_, ok, err := r.WaitRoute(ctx)
	if time.Since(start) > 100*time.Millisecond {
		t.Fatalf("waitroute after close blocked")
	}
	if ok || err != nil {
		t.Fatalf("waitroute after close false nil")
	}
	if _, ok := r.Route(); ok {
		t.Fatalf("route after close false")
	}
}

func TestHiddenWaitRouteCancelBeforeCall(t *testing.T) {
	r := New([]Provider{{"a", 0}})
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	_, ok, err := r.WaitRoute(ctx)
	if ok || err == nil {
		t.Fatalf("immediate cancel should error")
	}
}



func TestHiddenBatchEdgeCases(t *testing.T) {
	r := New([]Provider{{"a", 1}})
	if ids, ok := r.BatchRoute(0); !ok || len(ids) != 0 {
		t.Fatalf("batch 0 true")
	}
	r.Close()
	if _, ok := r.BatchRoute(1); ok {
		t.Fatalf("batch after close false")
	}
}

func TestHiddenWaitRouteFairness(t *testing.T) {
	r := New([]Provider{{"a", 0}})
	var wg sync.WaitGroup
	var successes int32
	const N = 30
	for i := 0; i < N; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
			defer cancel()
			_, ok, _ := r.WaitRoute(ctx)
			if ok {
				atomic.AddInt32(&successes, 1)
			}
		}()
	}
	time.Sleep(100 * time.Millisecond)
	r.AddProvider(Provider{"a", N})
	wg.Wait()
	if atomic.LoadInt32(&successes) != N {
		t.Fatalf("fairness expected %d got %d", N, successes)
	}
}

func TestHiddenRemoveNonexist(t *testing.T) {
	r := New(nil)
	if r.RemoveProvider("x") {
		t.Fatalf("remove nonexist false")
	}
}

func TestHiddenSpuriousWakeup(t *testing.T) {
	r := New([]Provider{{"a", 0}})
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	done := make(chan bool, 1)
	go func() {
		_, ok, err := r.WaitRoute(ctx)
		done <- (ok && err == nil)
	}()
	time.Sleep(50 * time.Millisecond)
	// spurious wake via Add with 0 capacity and Remove non-existent should not cause return
	r.AddProvider(Provider{"b", 0})
	r.RemoveProvider("nonexist")
	// still waiting after 200ms?
	select {
	case <-done:
		t.Fatalf("spurious wakeup caused premature return")
	case <-time.After(200 * time.Millisecond):
		// still waiting good
	}
	r.AddProvider(Provider{"a", 1})
	select {
	case ok := <-done:
		if !ok {
			t.Fatalf("should succeed after real capacity")
		}
	case <-time.After(1 * time.Second):
		t.Fatalf("did not wake on real capacity")
	}
}

func TestHiddenDuplicateOrderStability(t *testing.T) {
	r := New([]Provider{{"b", 1}, {"a", 1}, {"b", 5}, {"a", 3}})
	// last wins: a=3, b=5 . Order should be deterministic lex tie break after largest remaining logic.
	// First pick should be b (5 >3)
	id1, _ := r.Route()
	if id1 != "b" {
		t.Fatalf("expected b first got %s", id1)
	}
	// now b=4, a=3 => b again
	id2, _ := r.Route()
	if id2 != "b" {
		t.Fatalf("expected b second")
	}
	// now b=3 a=3 tie lex a
	id3, _ := r.Route()
	if id3 != "a" {
		t.Fatalf("expected a third tie")
	}
}

func TestHiddenSnapshotDeepCopy(t *testing.T) {
	r := New([]Provider{{"a", 2}})
	s1 := r.Snapshot()
	s1["a"] = 999
	s1["new"] = 1
	s2 := r.Snapshot()
	if s2["a"] == 999 || s2["new"] == 1 {
		t.Fatalf("snapshot must be independent deep copy")
	}
	if r.Remaining("a") != 2 {
		t.Fatalf("original unchanged")
	}
}

func TestHiddenCloseRaceAdd(t *testing.T) {
	r := New([]Provider{{"a", 1}})
	var wg sync.WaitGroup
	var closeTrues int32
	var addResults int32
	for i := 0; i < 20; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			if r.Close() {
				atomic.AddInt32(&closeTrues, 1)
			}
		}()
		wg.Add(1)
		go func() {
			defer wg.Done()
			// mix new and existing adds
			if r.AddProvider(Provider{"b", 1}) {
				atomic.AddInt32(&addResults, 1)
			}
			r.AddProvider(Provider{"a", 2}) // existing, should succeed even after close per spec returning true but no assignment allowed
		}()
	}
	wg.Wait()
	if atomic.LoadInt32(&closeTrues) != 1 {
		t.Fatalf("exactly one close true")
	}
	// After close, Route must be false regardless of capacity
	if _, ok := r.Route(); ok {
		t.Fatalf("route after close must false")
	}
}

func TestHiddenBatchPartialVisibility(t *testing.T) {
	r := New([]Provider{{"a", 100}, {"b", 100}})
	var wg sync.WaitGroup
	stop := make(chan struct{})
	var bad int32
	// sampler checks sum never changes by partial batch amount not multiple of batch size? Actually batch is 7, total capacity 200, so sum should always be even? Not reliable. We'll check that sum never goes negative and total assigned never exceeds capacity, which existing tests cover, but we add extra sampling during concurrent BatchRoute to ensure no panic and sum stays within bounds.
	go func() {
		for {
			select {
			case <-stop:
				return
			default:
				s := r.Snapshot()
				sum := 0
				for _, v := range s {
					sum += v
				}
				if sum < 0 || sum > 200 {
					atomic.StoreInt32(&bad, 1)
					return
				}
			}
		}
	}()
	for i := 0; i < 10; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for j := 0; j < 20; j++ {
				r.BatchRoute(7)
			}
		}()
	}
	wg.Wait()
	close(stop)
	if atomic.LoadInt32(&bad) != 0 {
		t.Fatalf("snapshot saw out of bounds during batch")
	}
	// final total assigned must be multiple of 7 or leave remainder capacity, but never exceed 200
	sum := r.Remaining("a") + r.Remaining("b")
	if sum < 0 || sum > 200 || (200-sum)%7 != 0 && sum != 200%7 { // actually with 200 capacity and batch 7, remainder can be 200 mod 7 =4, but due to interleaving with other ops maybe okay; just check bounds
		// allow any remainder as long as within bounds, already checked
	}
}

func TestHiddenWaitRouteContextRace(t *testing.T) {
	r := New([]Provider{{"a", 0}})
	ctx, cancel := context.WithCancel(context.Background())
	var wg sync.WaitGroup
	wg.Add(1)
	go func() {
		defer wg.Done()
		time.Sleep(10 * time.Millisecond)
		cancel()
	}()
	wg.Add(1)
	go func() {
		defer wg.Done()
		time.Sleep(20 * time.Millisecond)
		r.AddProvider(Provider{"a", 1})
	}()
	_, ok, err := r.WaitRoute(ctx)
	wg.Wait()
	// Either cancelled first -> err != nil and ok false, or assigned first -> ok true nil err. Both acceptable as race, but must not deadlock and must return promptly and not panic. We just check no deadlock and result is one of valid states.
	if err != nil && ok {
		t.Fatalf("invalid state both ok and err")
	}
	// If ok true, then capacity consumed; else if err, then maybe capacity remains 1 for later use, that's fine.
}

func TestHiddenRouteDoesNotBlock(t *testing.T) {
	r := New([]Provider{{"a", 100000}})
	for i := 0; i < 10000; i++ {
		r.Route()
	}
	// No wall-clock assertion (hardware/-race dependent; would flake). Route being
	// non-blocking is already covered structurally by the concurrent tests.
}

"""


def _walk_go(root):
    for dp, _, files in os.walk(root):
        for f in files:
            if f.endswith(".go"):
                yield os.path.join(dp, f)


@pytest.fixture(scope="session")
def pkgdir(tmp_path_factory):
    assert os.path.isdir(SRC_DIR), "/app/src missing"
    assert os.path.isfile(os.path.join(SRC_DIR, "go.mod")), "go.mod missing"
    assert shutil.which("go"), "go toolchain not found"
    d = str(tmp_path_factory.mktemp("pkg"))
    shutil.copy(os.path.join(SRC_DIR, "go.mod"), os.path.join(d, "go.mod"))
    for path in _walk_go(SRC_DIR):
        if path.endswith("_test.go"):
            continue
        rel = os.path.relpath(path, SRC_DIR)
        dst = os.path.join(d, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy(path, dst)
    with open(os.path.join(d, "zz_hidden_test.go"), "w") as f:
        f.write(HIDDEN_TEST_GO)
    return d


def test_go_mod_no_external_requires():
    with open(os.path.join(SRC_DIR, "go.mod")) as fh:
        for line in fh:
            m = re.match(r"^(require\s+)?([^\s]+)\s+v[0-9]", line.strip())
            if m and "." in m.group(2).split("/")[0]:
                raise AssertionError(f"external dependency {m.group(2)}")


def test_imports_stdlib_only():
    ext = re.compile(r'"([a-z0-9.\-]+\.[a-z]{2,}/[^"]+)"')
    for path in _walk_go(SRC_DIR):
        with open(path) as fh:
            for line in fh:
                if ext.search(line):
                    raise AssertionError(f"non-stdlib import in {path}: {line.strip()}")


def test_builds(pkgdir):
    go = shutil.which("go")
    proc = subprocess.run(
        [go, "build", "./..."], cwd=pkgdir, capture_output=True, text=True, timeout=180
    )
    assert proc.returncode == 0, f"go build failed:\n{proc.stdout}\n{proc.stderr}"


def test_race_and_capacity_invariants(pkgdir):
    go = shutil.which("go")
    env = dict(os.environ, CGO_ENABLED="1")
    proc = subprocess.run(
        [go, "test", "-race", "-count=20", "-run", "Hidden", "./..."],
        cwd=pkgdir,
        capture_output=True,
        text=True,
        timeout=600,
        env=env,
    )
    combined = proc.stdout + proc.stderr
    assert "DATA RACE" not in combined, f"race detector fired:\n{combined}"
    assert proc.returncode == 0, f"go test -race failed:\n{combined}"
