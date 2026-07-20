// brokerd fanout allocator - internal messaging infra
// owner: messaging-infra / broker team
// oncall: broker-oncall , slack: #broker-fanout-support
// design doc: https://internal/docs/broker/fanout (may be outdated)
//
// history:
//
//	2023-08-15 - initial weighted version
//	2023-11-02 - switched to credit-decay weighted fair share to handle bursty subs
//	2024-01-10 - added fallback RR when credits drain to 0 (seen in prod) - j.m.
//	2024-03-22 - extracted to standalone repro for debugging
//
// This is pulled from prod logic for offline testing. Input format is simple
// for repros, not the real wire format.
//
// Usage: echo -e "16\n5 6\n3 9\n..." | go run main.go
package main

import (
	"bufio"
	"fmt"
	"os"
	"strconv"
	"strings"
)

// allocate decides how many messages each subscriber gets.
// load = total msgs to fan out
// weight = per-sub fair-share weight
// capLimit = per-sub max (capacity)
func allocate(load int, weight []int, capLimit []int) []int {
	n := len(weight)
	alloc := make([]int, n)
	credit := make([]int, n)
	for i := 0; i < n; i++ {
		credit[i] = weight[i]
	}
	rem := load

	for rem > 0 {
		// build list of subs that still have room
		active := make([]int, 0, n)
		for i := 0; i < n; i++ {
			if alloc[i] < capLimit[i] {
				active = append(active, i)
			}
		}
		if len(active) == 0 {
			break
		}

		sumCred := 0
		for _, idx := range active {
			sumCred += credit[idx]
		}

		// prod fix: when credits all decayed to 0 we would deadlock,
		// fall back to round-robin in input order. Not ideal but keeps
		// moving and matches old behavior for small n.
		if sumCred == 0 {
			for rem > 0 {
				made := false
				for _, idx := range active {
					if rem == 0 {
						break
					}
					if alloc[idx] < capLimit[idx] {
						alloc[idx]++
						rem--
						made = true
					}
				}
				if !made {
					break
				}
			}
			break
		}

		delta := make([]int, n)
		used := 0
		for _, idx := range active {
			// integer proportional split - truncation is ok because
			// we have the progress guarantee below
			share := (rem * credit[idx]) / sumCred
			room := capLimit[idx] - alloc[idx]
			if share > room {
				share = room
			}
			alloc[idx] += share
			delta[idx] = share
			used += share
		}

		// guarantee progress: if proportional pass gave nothing (rem < active or
		// credits skewed), give 1 to highest-credit active sub. tie -> lowest index.
		if used == 0 {
			best := active[0]
			for _, idx := range active[1:] {
				if credit[idx] > credit[best] {
					best = idx
				}
			}
			alloc[best]++
			delta[best]++
			used = 1
		}
		rem -= used

		// decay credits for next iteration
		// served subs lose credit, unserved accumulate
		for _, idx := range active {
			if delta[idx] > 0 {
				// halving gives some memory but avoids starvation too much
				credit[idx] = credit[idx] / 2
			} else {
				credit[idx] += weight[idx]
			}
		}
	}

	return alloc
}

func main() {
	sc := bufio.NewScanner(os.Stdin)
	sc.Buffer(make([]byte, 1<<20), 1<<20)
	var raw []string
	for sc.Scan() {
		raw = append(raw, strings.TrimSpace(sc.Text()))
	}
	if len(raw) == 0 {
		// no input -> nothing to allocate
		fmt.Println("")
		return
	}

	load, err := strconv.Atoi(raw[0])
	if err != nil {
		fmt.Fprintln(os.Stderr, "invalid load line")
		os.Exit(1)
	}

	var wts []int
	var caps []int
	for _, line := range raw[1:] {
		if line == "" {
			continue
		}
		parts := strings.Fields(line)
		if len(parts) != 2 {
			fmt.Fprintln(os.Stderr, "invalid sub line:", line)
			os.Exit(1)
		}
		w, e1 := strconv.Atoi(parts[0])
		c, e2 := strconv.Atoi(parts[1])
		if e1 != nil || e2 != nil {
			fmt.Fprintln(os.Stderr, "invalid sub line:", line)
			os.Exit(1)
		}
		wts = append(wts, w)
		caps = append(caps, c)
	}

	res := allocate(load, wts, caps)
	out := make([]string, len(res))
	for i, v := range res {
		out[i] = strconv.Itoa(v)
	}
	fmt.Println(strings.Join(out, ","))
}
