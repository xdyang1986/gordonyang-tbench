// Command allocator distributes a batch of pending messages across a message
// broker's subscribers, giving each a share based on its weight while never
// exceeding its capacity. It reads a plan on stdin and prints the per-subscriber
// allocation on stdout.
//
// Input (stdin):
//
//	<load>
//	<weight> <cap>
//	<weight> <cap>
//	...
//
// Output (stdout): one line, the comma-separated allocation per subscriber, in
// input order.
package main

import (
	"bufio"
	"fmt"
	"os"
	"strconv"
	"strings"
)

func allocate(load int, weight, capacity []int) []int {
	n := len(weight)
	alloc := make([]int, n)
	credit := make([]int, n)
	copy(credit, weight)
	remaining := load

	for remaining > 0 {
		active := []int{}
		for i := 0; i < n; i++ {
			if alloc[i] < capacity[i] {
				active = append(active, i)
			}
		}
		if len(active) == 0 {
			break
		}

		total := 0
		for _, i := range active {
			total += credit[i]
		}

		// If every active subscriber's credit has decayed to zero, fill the
		// remaining load one unit at a time in subscriber order.
		if total == 0 {
			for remaining > 0 {
				progressed := false
				for _, i := range active {
					if remaining == 0 {
						break
					}
					if alloc[i] < capacity[i] {
						alloc[i]++
						remaining--
						progressed = true
					}
				}
				if !progressed {
					break
				}
			}
			break
		}

		delta := make([]int, n)
		used := 0
		for _, i := range active {
			give := (remaining * credit[i]) / total
			if give > capacity[i]-alloc[i] {
				give = capacity[i] - alloc[i]
			}
			alloc[i] += give
			delta[i] += give
			used += give
		}

		// Guarantee progress: if the proportional pass placed nothing, give one
		// unit to the highest-credit active subscriber (ties: lowest index).
		if used == 0 {
			best := active[0]
			for _, i := range active {
				if credit[i] > credit[best] {
					best = i
				}
			}
			alloc[best]++
			delta[best]++
			used = 1
		}
		remaining -= used

		// Decay credits between rounds: subscribers served this round have their
		// credit reduced; subscribers that got nothing accumulate more credit.
		for _, i := range active {
			if delta[i] > 0 {
				credit[i] = credit[i] / 2
			} else {
				credit[i] += weight[i]
			}
		}
	}
	return alloc
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
	load, err := strconv.Atoi(lines[0])
	if err != nil {
		fmt.Fprintln(os.Stderr, "bad load")
		os.Exit(1)
	}
	var weight, capacity []int
	for _, ln := range lines[1:] {
		if ln == "" {
			continue
		}
		f := strings.Fields(ln)
		if len(f) != 2 {
			fmt.Fprintln(os.Stderr, "bad subscriber line")
			os.Exit(1)
		}
		w, e1 := strconv.Atoi(f[0])
		c, e2 := strconv.Atoi(f[1])
		if e1 != nil || e2 != nil {
			fmt.Fprintln(os.Stderr, "bad subscriber line")
			os.Exit(1)
		}
		weight = append(weight, w)
		capacity = append(capacity, c)
	}
	a := allocate(load, weight, capacity)
	ss := make([]string, len(a))
	for i, x := range a {
		ss[i] = strconv.Itoa(x)
	}
	fmt.Println(strings.Join(ss, ","))
}
