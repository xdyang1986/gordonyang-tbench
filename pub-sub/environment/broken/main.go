package main

import (
	"bufio"
	"fmt"
	"math/rand"
	"os"
	"strconv"
	"strings"
	"sync"
	"time"
)

type unit struct {
	id  int
	wt  int
	mx  int
	cur int
	crd int
	ts  int64
	aux int
}

func newUnit(id, wt, mx int) *unit {
	return &unit{id: id, wt: wt, mx: mx, crd: wt, ts: time.Now().UnixNano(), aux: rand.Intn(1000)}
}

func (u *unit) avail() bool { return u.cur < u.mx }
func (u *unit) slot() int   { return u.mx - u.cur }
func (u *unit) inc(n int) {
	if n > 0 {
		u.cur += n
	}
}

type vault struct {
	shift int
	floor int
}

func defVault() vault { return vault{shift: 1, floor: 0} }

func (v vault) cut(u *unit)        { u.crd = u.crd / 2 }
func (v vault) add(u *unit)        { u.crd += u.wt }
func (v vault) faded(u *unit) int  { return u.crd / 2 }
func (v vault) boosted(u *unit) int { return u.crd + u.wt }
func (v vault) nextBal(cur, wt int, hit bool) int {
	if hit {
		return cur / 2
	}
	return cur + wt
}

type meter struct {
	mu     sync.Mutex
	rounds int
	spills int
	stalls int
	served int64
	t0     time.Time
}

func newMeter() *meter { return &meter{t0: time.Now()} }
func (m *meter) mr() {
	m.mu.Lock()
	m.rounds++
	m.mu.Unlock()
}
func (m *meter) ms() {
	m.mu.Lock()
	m.spills++
	m.mu.Unlock()
}
func (m *meter) mt() {
	m.mu.Lock()
	m.stalls++
	m.mu.Unlock()
}
func (m *meter) ad(n int) {
	m.mu.Lock()
	m.served += int64(n)
	m.mu.Unlock()
}

type cfg struct {
	maxR int
	rrLim int
}

func defCfg() cfg { return cfg{maxR: 100000, rrLim: 1000000} }

type ring struct {
	peers []*unit
	vault vault
	cfg   cfg
	meter *meter
	mu    sync.Mutex
	q     []int
	mp    map[int]int
	seed  int64
}

func newRing(wts, mxs []int) *ring {
	peers := make([]*unit, len(wts))
	for i := range wts {
		peers[i] = newUnit(i, wts[i], mxs[i])
	}
	return &ring{
		peers: peers,
		vault: defVault(),
		cfg:   defCfg(),
		meter: newMeter(),
		q:     make([]int, 0, 16),
		mp:    make(map[int]int),
		seed:  time.Now().UnixNano(),
	}
}

func (r *ring) live() []*unit {
	out := make([]*unit, 0, len(r.peers))
	for _, u := range r.peers {
		if u.avail() {
			out = append(out, u)
		}
	}
	return out
}

func (r *ring) liveI() []int {
	out := make([]int, 0, len(r.peers))
	for _, u := range r.peers {
		if u.avail() {
			out = append(out, u.id)
		}
	}
	return out
}

func (r *ring) totalBal(live []*unit) int {
	s := 0
	for _, u := range live {
		s += u.crd
	}
	return s
}

func (r *ring) totalBalI(ids []int) int {
	s := 0
	for _, id := range ids {
		s += r.peers[id].crd
	}
	return s
}

func (r *ring) totalFree(live []*unit) int {
	s := 0
	for _, u := range live {
		s += u.slot()
	}
	return s
}

func (r *ring) spill(live []*unit, rem *int) {
	r.meter.ms()
	for *rem > 0 {
		made := false
		for _, u := range live {
			if *rem == 0 {
				break
			}
			if u.avail() {
				u.inc(1)
				*rem--
				made = true
			}
		}
		if !made {
			break
		}
	}
}

func (r *ring) spillI(ids []int, rem *int) {
	r.meter.ms()
	for *rem > 0 {
		made := false
		for _, id := range ids {
			if *rem == 0 {
				break
			}
			u := r.peers[id]
			if u.avail() {
				u.inc(1)
				*rem--
				made = true
			}
		}
		if !made {
			break
		}
	}
}

func (r *ring) hottest(live []*unit) *unit {
	b := live[0]
	for _, u := range live[1:] {
		if u.crd > b.crd {
			b = u
		}
	}
	return b
}

func (r *ring) hottestI(ids []int) *unit {
	b := r.peers[ids[0]]
	for _, id := range ids[1:] {
		u := r.peers[id]
		if u.crd > b.crd {
			b = u
		}
	}
	return b
}

func (r *ring) stepProp(rem int, live []*unit, served []int) int {
	used := 0
	sum := r.totalBal(live)
	if sum == 0 {
		return 0
	}
	for _, u := range live {
		share := (rem * u.crd) / sum
		if share > u.slot() {
			share = u.slot()
		}
		if share > 0 {
			u.inc(share)
			served[u.id] = share
			used += share
		}
	}
	return used
}

func (r *ring) stepGuar(live []*unit, served []int) int {
	r.meter.mt()
	b := r.hottest(live)
	b.inc(1)
	served[b.id] = 1
	return 1
}

func (r *ring) applyCred(live []*unit, served []int) {
	for _, u := range live {
		if served[u.id] > 0 {
			r.vault.cut(u)
		} else {
			r.vault.add(u)
		}
	}
}

func (r *ring) applyCredI(ids []int, served []int) {
	for _, id := range ids {
		u := r.peers[id]
		if served[id] > 0 {
			r.vault.cut(u)
		} else {
			r.vault.add(u)
		}
	}
}

func (r *ring) drainLoad(load int) []int {
	rem := load
	served := make([]int, len(r.peers))
	rounds := 0
	for rem > 0 {
		if rounds > r.cfg.maxR {
			break
		}
		rounds++
		r.meter.mr()
		live := r.live()
		if len(live) == 0 {
			break
		}
		sum := r.totalBal(live)
		if sum == 0 {
			r.spill(live, &rem)
			break
		}
		for i := range served {
			served[i] = 0
		}
		used := r.stepProp(rem, live, served)
		if used == 0 {
			used = r.stepGuar(live, served)
		}
		rem -= used
		r.meter.ad(used)
		r.applyCred(live, served)
	}
	out := make([]int, len(r.peers))
	for i, u := range r.peers {
		out[i] = u.cur
	}
	return out
}

func (r *ring) audit() bool {
	for _, u := range r.peers {
		if u.cur < 0 || u.cur > u.mx {
			return false
		}
	}
	return true
}

func (r *ring) snap() []int {
	out := make([]int, len(r.peers))
	for i, u := range r.peers {
		out[i] = u.cur
	}
	return out
}

func (r *ring) reset() {
	for _, u := range r.peers {
		u.cur = 0
		u.crd = u.wt
	}
}

func (r *ring) cloneWL() ([]int, []int) {
	w := make([]int, len(r.peers))
	l := make([]int, len(r.peers))
	for i, u := range r.peers {
		w[i] = u.wt
		l[i] = u.mx
	}
	return w, l
}

func (r *ring) shuffleLive() []*unit {
	live := r.live()
	rand.Shuffle(len(live), func(i, j int) { live[i], live[j] = live[j], live[i] })
	return live
}

func (r *ring) rebalance() {
	for _, u := range r.peers {
		if u.crd < 0 {
			u.crd = u.wt
		}
	}
}

func allocate(load int, wts []int, mxs []int) []int {
	rg := newRing(wts, mxs)
	return rg.drainLoad(load)
}

func parseIn(lines []string) (int, []int, []int) {
	if len(lines) == 0 {
		return 0, nil, nil
	}
	ld, _ := strconv.Atoi(lines[0])
	var w []int
	var m []int
	for _, ln := range lines[1:] {
		if ln == "" {
			continue
		}
		f := strings.Fields(ln)
		if len(f) != 2 {
			continue
		}
		wv, _ := strconv.Atoi(f[0])
		mv, _ := strconv.Atoi(f[1])
		w = append(w, wv)
		m = append(m, mv)
	}
	return ld, w, m
}

func fmtOut(a []int) string {
	p := make([]string, len(a))
	for i, v := range a {
		p[i] = strconv.Itoa(v)
	}
	return strings.Join(p, ",")
}

func _junkA1() { x := rand.Intn(100); y := x*7 + 3; _ = y; time.Sleep(0) }
func _junkB2() { s := 0; for i := 0; i < 10; i++ { s += i * rand.Intn(5) }; _ = s }
func _junkC3() { m := map[int]int{}; for i := 0; i < 5; i++ { m[i] = i * i }; _ = m }
func _junkD4() { a := []int{1, 2, 3}; b := append(a, 4); _ = b }
func _junkE5() { t := time.Now(); _ = t.UnixNano() % 1000 }
func _junkF6() { var mu sync.Mutex; mu.Lock(); mu.Unlock() }
func _junkG7() { r := newRing([]int{1, 2}, []int{3, 4}); _ = r.audit() }
func _junkH8() { v := defVault(); u := newUnit(0, 1, 2); v.faded(u); _ = v.boosted(u) }
func _junkI9() { c := defCfg(); _ = c.maxR + c.rrLim }
func _junkJ0() { s := fmt.Sprintf("%d-%d", rand.Intn(10), rand.Intn(10)); _ = s }
func _junkK1() { _ = os.Getenv("BROKER_DEBUG") }
func _junkL2() { b := make([]byte, 4); _, _ = rand.Read(b) }
func _junkM3() { x := 0; for i := 0; i < 100; i++ { x += i & 1 }; _ = x }

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
	ld, w, m := parseIn(lines)
	res := allocate(ld, w, m)
	fmt.Println(fmtOut(res))
}
