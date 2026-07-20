package main

import (
	"bufio"
	"fmt"
	"os"
	"strconv"
	"strings"
)

type subscriber struct {
	id     int
	weight int
	cap    int
	alloc  int
	credit int
}

func newSubscriber(id, w, c int) *subscriber {
	return &subscriber{id: id, weight: w, cap: c, credit: w}
}

func (s *subscriber) hasRoom() bool {
	return s.alloc < s.cap
}

func (s *subscriber) room() int {
	return s.cap - s.alloc
}

func (s *subscriber) add(n int) {
	if n > 0 {
		s.alloc += n
	}
}

type creditController struct{}

func (cc *creditController) onServed(s *subscriber, _ int) {
	s.credit = s.credit / 2
}

func (cc *creditController) onIdle(s *subscriber) {
	s.credit += s.weight
}

type broker struct {
	subs []*subscriber
	cc   creditController
}

func newBroker(weights, caps []int) *broker {
	subs := make([]*subscriber, len(weights))
	for i := range weights {
		subs[i] = newSubscriber(i, weights[i], caps[i])
	}
	return &broker{subs: subs}
}

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

func (b *broker) fallbackRR(active []*subscriber, rem *int) {
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
	best := active[0]
	for _, s := range active[1:] {
		if s.credit > best.credit {
			best = s
		}
	}
	return best
}

func (b *broker) drain(load int) []int {
	rem := load
	served := make([]int, len(b.subs))
	for rem > 0 {
		act := b.active()
		if len(act) == 0 {
			break
		}
		sum := b.sumCredit(act)
		if sum == 0 {
			b.fallbackRR(act, &rem)
			break
		}
		for i := range served {
			served[i] = 0
		}
		used := 0
		for _, s := range act {
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
			best := b.pickHighestCredit(act)
			best.add(1)
			served[best.id] = 1
			used = 1
		}
		rem -= used
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
		out[i] = s.alloc
	}
	return out
}

func allocate(load int, weight []int, capLimit []int) []int {
	b := newBroker(weight, capLimit)
	return b.drain(load)
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
	load, _ := strconv.Atoi(lines[0])
	var weights []int
	var caps []int
	for _, ln := range lines[1:] {
		if ln == "" {
			continue
		}
		f := strings.Fields(ln)
		if len(f) != 2 {
			continue
		}
		w, _ := strconv.Atoi(f[0])
		c, _ := strconv.Atoi(f[1])
		weights = append(weights, w)
		caps = append(caps, c)
	}
	res := allocate(load, weights, caps)
	out := make([]string, len(res))
	for i, v := range res {
		out[i] = strconv.Itoa(v)
	}
	fmt.Println(strings.Join(out, ","))
}
