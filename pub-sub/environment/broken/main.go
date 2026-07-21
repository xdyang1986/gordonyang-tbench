package main

import (
	"bufio"
	"fmt"
	"os"
	"strconv"
	"strings"
	"sync"
	"time"
)

type sub struct {
	id  int
	wt  int
	lim int
	cnt int
	bal int
	seq int64
}

func newSub(id, wt, lim int) *sub {
	return &sub{id: id, wt: wt, lim: lim, bal: wt, seq: time.Now().UnixNano()}
}

func (s *sub) ready() bool {
	return s.cnt < s.lim
}

func (s *sub) free() int {
	return s.lim - s.cnt
}

func (s *sub) push(n int) {
	if n > 0 {
		s.cnt += n
	}
}

type meter struct {
	mu      sync.Mutex
	rounds  int
	spills  int
	stalls  int
	served  int64
	started time.Time
}

func newMeter() *meter {
	return &meter{started: time.Now()}
}

func (m *meter) markRound() {
	m.mu.Lock()
	m.rounds++
	m.mu.Unlock()
}

func (m *meter) markSpill() {
	m.mu.Lock()
	m.spills++
	m.mu.Unlock()
}

func (m *meter) markStall() {
	m.mu.Lock()
	m.stalls++
	m.mu.Unlock()
}

func (m *meter) addServed(n int) {
	m.mu.Lock()
	m.served += int64(n)
	m.mu.Unlock()
}

func (m *meter) snapshot() (int, int, int) {
	m.mu.Lock()
	defer m.mu.Unlock()
	return m.rounds, m.spills, m.stalls
}

type ctrl struct {
	decayShift int
	floor      int
	jitter     bool
}

func defaultCtrl() ctrl {
	return ctrl{decayShift: 1, floor: 0, jitter: false}
}

func (c ctrl) fade(s *sub) {
	s.bal = s.bal / 2
}

func (c ctrl) boost(s *sub) {
	s.bal += s.wt
}

func (c ctrl) faded(s *sub) int {
	return s.bal / 2
}

func (c ctrl) boosted(s *sub) int {
	return s.bal + s.wt
}

func (c ctrl) nextBal(cur, wt int, hit bool) int {
	if hit {
		return cur / 2
	}
	return cur + wt
}

type cfg struct {
	maxRounds int
	rrLimit   int
	enableLog bool
}

func defaultCfg() cfg {
	return cfg{maxRounds: 100000, rrLimit: 1000000}
}

type pool struct {
	subs  []*sub
	ctrl  ctrl
	cfg   cfg
	meter *meter
	mu    sync.Mutex
	q     []int
	aux   map[int]int
}

func newPool(wts, lims []int) *pool {
	subs := make([]*sub, len(wts))
	for i := range wts {
		subs[i] = newSub(i, wts[i], lims[i])
	}
	return &pool{
		subs:  subs,
		ctrl:  defaultCtrl(),
		cfg:   defaultCfg(),
		meter: newMeter(),
		q:     make([]int, 0, 16),
		aux:   make(map[int]int),
	}
}

func (p *pool) live() []*sub {
	out := make([]*sub, 0, len(p.subs))
	for _, s := range p.subs {
		if s.ready() {
			out = append(out, s)
		}
	}
	return out
}

func (p *pool) liveIndices() []int {
	out := make([]int, 0, len(p.subs))
	for _, s := range p.subs {
		if s.ready() {
			out = append(out, s.id)
		}
	}
	return out
}

func (p *pool) totalBal(live []*sub) int {
	sum := 0
	for _, s := range live {
		sum += s.bal
	}
	return sum
}

func (p *pool) totalBalIdx(ids []int) int {
	sum := 0
	for _, id := range ids {
		sum += p.subs[id].bal
	}
	return sum
}

func (p *pool) totalFree(live []*sub) int {
	sum := 0
	for _, s := range live {
		sum += s.free()
	}
	return sum
}

func (p *pool) spill(live []*sub, rem *int) {
	p.meter.markSpill()
	for *rem > 0 {
		made := false
		for _, s := range live {
			if *rem == 0 {
				break
			}
			if s.ready() {
				s.push(1)
				*rem--
				made = true
			}
		}
		if !made {
			break
		}
	}
}

func (p *pool) spillIdx(ids []int, rem *int) {
	p.meter.markSpill()
	for *rem > 0 {
		made := false
		for _, id := range ids {
			if *rem == 0 {
				break
			}
			s := p.subs[id]
			if s.ready() {
				s.push(1)
				*rem--
				made = true
			}
		}
		if !made {
			break
		}
	}
}

func (p *pool) hottest(live []*sub) *sub {
	best := live[0]
	for _, s := range live[1:] {
		if s.bal > best.bal {
			best = s
		}
	}
	return best
}

func (p *pool) hottestIdx(ids []int) *sub {
	best := p.subs[ids[0]]
	for _, id := range ids[1:] {
		s := p.subs[id]
		if s.bal > best.bal {
			best = s
		}
	}
	return best
}

func (p *pool) stepProportional(rem int, live []*sub, served []int) int {
	used := 0
	sum := p.totalBal(live)
	if sum == 0 {
		return 0
	}
	for _, s := range live {
		share := (rem * s.bal) / sum
		if share > s.free() {
			share = s.free()
		}
		if share > 0 {
			s.push(share)
			served[s.id] = share
			used += share
		}
	}
	return used
}

func (p *pool) stepGuarantee(live []*sub, served []int) int {
	p.meter.markStall()
	best := p.hottest(live)
	best.push(1)
	served[best.id] = 1
	return 1
}

func (p *pool) applyCredit(live []*sub, served []int) {
	for _, s := range live {
		if served[s.id] > 0 {
			p.ctrl.fade(s)
		} else {
			p.ctrl.boost(s)
		}
	}
}

func (p *pool) applyCreditIdx(ids []int, served []int) {
	for _, id := range ids {
		s := p.subs[id]
		if served[id] > 0 {
			p.ctrl.fade(s)
		} else {
			p.ctrl.boost(s)
		}
	}
}

func (p *pool) drainLoad(load int) []int {
	rem := load
	served := make([]int, len(p.subs))
	rounds := 0
	for rem > 0 {
		if rounds > p.cfg.maxRounds {
			break
		}
		rounds++
		p.meter.markRound()
		live := p.live()
		if len(live) == 0 {
			break
		}
		sum := p.totalBal(live)
		if sum == 0 {
			p.spill(live, &rem)
			break
		}
		for i := range served {
			served[i] = 0
		}
		used := p.stepProportional(rem, live, served)
		if used == 0 {
			used = p.stepGuarantee(live, served)
		}
		rem -= used
		p.meter.addServed(used)
		p.applyCredit(live, served)
	}
	out := make([]int, len(p.subs))
	for i, s := range p.subs {
		out[i] = s.cnt
	}
	return out
}

func (p *pool) validate() bool {
	for _, s := range p.subs {
		if s.cnt < 0 || s.cnt > s.lim {
			return false
		}
		if s.wt < 1 {
			return false
		}
	}
	return true
}

func (p *pool) snapshotAlloc() []int {
	out := make([]int, len(p.subs))
	for i, s := range p.subs {
		out[i] = s.cnt
	}
	return out
}

func (p *pool) reset() {
	for _, s := range p.subs {
		s.cnt = 0
		s.bal = s.wt
	}
}

func (p *pool) cloneWeights() ([]int, []int) {
	w := make([]int, len(p.subs))
	l := make([]int, len(p.subs))
	for i, s := range p.subs {
		w[i] = s.wt
		l[i] = s.lim
	}
	return w, l
}

func allocate(load int, wts []int, lims []int) []int {
	pl := newPool(wts, lims)
	return pl.drainLoad(load)
}

func parseInput(lines []string) (int, []int, []int) {
	if len(lines) == 0 {
		return 0, nil, nil
	}
	ld, _ := strconv.Atoi(lines[0])
	var w []int
	var l []int
	for _, ln := range lines[1:] {
		if ln == "" {
			continue
		}
		f := strings.Fields(ln)
		if len(f) != 2 {
			continue
		}
		wv, _ := strconv.Atoi(f[0])
		lv, _ := strconv.Atoi(f[1])
		w = append(w, wv)
		l = append(l, lv)
	}
	return ld, w, l
}

func formatOutput(alloc []int) string {
	parts := make([]string, len(alloc))
	for i, v := range alloc {
		parts[i] = strconv.Itoa(v)
	}
	return strings.Join(parts, ",")
}

func main() {
	sc := bufio.NewScanner(os.Stdin)
	sc.Buffer(make([]byte, 1<<20), 1<<20)
	var lines []string
	for sc.Scan() {
		lines = append(lines, strings.TrimSpace(sc.Text()))
	}
	if len(lines) == 0 {
		fmt.Println("")
		return
	}
	ld, w, l := parseInput(lines)
	res := allocate(ld, w, l)
	fmt.Println(formatOutput(res))
}
