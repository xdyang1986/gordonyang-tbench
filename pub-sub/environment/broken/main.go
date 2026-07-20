// brokerd fanout allocator - internal messaging infra
// owner: messaging-infra / broker team
// oncall: broker-oncall , slack: #broker-fanout-support , dir: https://fburl.com/broker-fanout
// design: https://internalfb.com/docs/broker/fanout-allocator (v2 - may be stale)
//
// This file is a standalone extraction of the prod fanout path for local repro.
// Real brokerd has extra RPC / checkpointing / metrics around this. The core
// scheduling decision is in creditController + brokerLoop.
//
// history:
//
//	2022-06-10  initial cap-only allocator
//	2023-08-15  weighted fair share
//	2023-11-02  credit-decay: keep memory of who got served to avoid starvation
//	2024-01-10  total-credit == 0 -> RR fallback, saw deadlock on small weights - j.m.
//	2024-02-18  factored credit logic into creditController for reuse with push path
//	2024-03-22  stripped deps for offline repro / bench
package main

import (
	"bufio"
	"fmt"
	"os"
	"strconv"
	"strings"
)

// --- metrics / observability (no-op in repro, kept for shape) ---

type fanoutMetrics struct {
	rounds      int
	rrFallbacks int
	stalled     int
}

func (m *fanoutMetrics) incRounds()  { m.rounds++ }
func (m *fanoutMetrics) incRR()      { m.rrFallbacks++ }
func (m *fanoutMetrics) incStalled() { m.stalled++ }

// --- subscriber state ---

type subscriber struct {
	// immutable / config
	id     int
	weight int
	cap    int
	// mutable
	alloc  int
	credit int
}

func newSubscriber(id, w, c int) *subscriber {
	return &subscriber{
		id:     id,
		weight: w,
		cap:    c,
		credit: w, // credit starts at weight, decays on serve
	}
}

func (s *subscriber) hasRoom() bool {
	return s.alloc < s.cap
}

func (s *subscriber) room() int {
	// caller must have checked hasRoom()
	return s.cap - s.alloc
}

func (s *subscriber) add(n int) {
	if n <= 0 {
		return
	}
	s.alloc += n
}

func (s *subscriber) snapshotAlloc() int {
	return s.alloc
}

// --- credit controller (fair-share memory) ---

// creditController encapsulates the credit decay policy. Prod has two
// implementations: push and pull. This is the pull path used by fanout.
type creditController struct {
	// no fields in this stripped version - in prod holds config
	// e.g. decayFactor, floor, etc.
}

// onServed is called when a sub got `served` msgs in a round. We halve its
// credit so others get a chance. In prod this also emits a counter.
func (cc *creditController) onServed(s *subscriber, served int) {
	// BUG REVIEW NOTE (2024-01-12, j.m.): decay felt too aggressive in load
	// tests? We tried /2, /3, etc. /2 looked stable, keeping it for now.
	// Halving keeps some memory but prevents one heavy sub from hogging rounds.
	// NOTE: floor handling removed for repro simplicity.
	s.credit = s.credit / 2
}

// onIdle is called when a sub was active but got nothing this round.
// It accumulates weight so it won't starve.
func (cc *creditController) onIdle(s *subscriber) {
	s.credit += s.weight
}

// --- broker core ---

type broker struct {
	subs []*subscriber
	cc   creditController
	m    fanoutMetrics
}

func newBroker(weights, caps []int) *broker {
	n := len(weights)
	subs := make([]*subscriber, n)
	for i := 0; i < n; i++ {
		subs[i] = newSubscriber(i, weights[i], caps[i])
	}
	return &broker{subs: subs}
}

// active returns subs that still have capacity, in input order.
func (b *broker) active() []*subscriber {
	out := make([]*subscriber, 0, len(b.subs))
	for _, s := range b.subs {
		if s.hasRoom() {
			out = append(out, s)
		}
	}
	return out
}

func (b *broker) sumCredit(active []*subscriber) int {
	sum := 0
	for _, s := range active {
		sum += s.credit
	}
	return sum
}

// fallbackRR fills remaining load round-robin when total credit is 0.
// This happens after many decay rounds and would otherwise deadlock.
func (b *broker) fallbackRR(active []*subscriber, rem *int) {
	b.m.incRR()
	for *rem > 0 {
		made := false
		for _, s := range active {
			if *rem == 0 {
				break
			}
			if s.hasRoom() {
				s.add(1)
				*rem--
				made = true
			}
		}
		if !made {
			break
		}
	}
}

func (b *broker) pickHighestCredit(active []*subscriber) *subscriber {
	// tie-break: lowest id (input order), matches old prod behavior
	best := active[0]
	for _, s := range active[1:] {
		if s.credit > best.credit {
			best = s
		}
	}
	return best
}

// drain distributes `load` messages and returns per-sub allocations.
func (b *broker) drain(load int) []int {
	rem := load

	// reusable per-round served tracking, indexed by sub id
	served := make([]int, len(b.subs))

	for rem > 0 {
		b.m.incRounds()
		act := b.active()
		if len(act) == 0 {
			break
		}

		sum := b.sumCredit(act)
		if sum == 0 {
			b.fallbackRR(act, &rem)
			break
		}

		// clear served markers
		for i := range served {
			served[i] = 0
		}

		used := 0
		for _, s := range act {
			// proportional share, integer division truncates - progress
			// guaranteed separately below.
			share := (rem * s.credit) / sum
			if share > s.room() {
				share = s.room()
			}
			if share > 0 {
				s.add(share)
				served[s.id] = share
				used += share
			}
		}

		if used == 0 {
			b.m.incStalled()
			best := b.pickHighestCredit(act)
			best.add(1)
			served[best.id] = 1
			used = 1
		}

		rem -= used

		// update credits for next round
		for _, s := range act {
			if served[s.id] > 0 {
				b.cc.onServed(s, served[s.id])
			} else {
				b.cc.onIdle(s)
			}
		}
	}

	out := make([]int, len(b.subs))
	for i, s := range b.subs {
		out[i] = s.snapshotAlloc()
	}
	return out
}

// top-level allocate keeps old signature for main() / tests
func allocate(load int, weight []int, capLimit []int) []int {
	if len(weight) != len(capLimit) {
		// should not happen in repro, but keep parity with prod guard
		return nil
	}
	b := newBroker(weight, capLimit)
	return b.drain(load)
}

// --- input / output ---

func main() {
	sc := bufio.NewScanner(os.Stdin)
	sc.Buffer(make([]byte, 1<<20), 1<<20)
	var lines []string
	for sc.Scan() {
		t := strings.TrimSpace(sc.Text())
		lines = append(lines, t)
	}
	if len(lines) == 0 {
		fmt.Println("")
		return
	}
	load, err := strconv.Atoi(lines[0])
	if err != nil {
		fmt.Fprintln(os.Stderr, "invalid load line")
		os.Exit(1)
	}
	var weights []int
	var caps []int
	for _, ln := range lines[1:] {
		if ln == "" {
			continue
		}
		f := strings.Fields(ln)
		if len(f) != 2 {
			fmt.Fprintln(os.Stderr, "invalid subscriber line:", ln)
			os.Exit(1)
		}
		w, e1 := strconv.Atoi(f[0])
		c, e2 := strconv.Atoi(f[1])
		if e1 != nil || e2 != nil {
			fmt.Fprintln(os.Stderr, "invalid subscriber line:", ln)
			os.Exit(1)
		}
		weights = append(weights, w)
		caps = append(caps, c)
	}
	result := allocate(load, weights, caps)
	strs := make([]string, len(result))
	for i, v := range result {
		strs[i] = strconv.Itoa(v)
	}
	fmt.Println(strings.Join(strs, ","))
}
