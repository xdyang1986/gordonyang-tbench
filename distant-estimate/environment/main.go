package main

import (
	"fmt"
	"os"
)

// Starter skeleton – implements help only.
// The ground-truth solution overwrites this file.
// Turn1 help must contain: graph, from, to, requests, help (5 keywords)
// Turn2 help must also contain traffic (6 keywords total)
// Build via go build -o router . from /app, stdlib only

func main() {
	if len(os.Args) == 1 {
		printHelp()
		os.Exit(0)
	}
	if len(os.Args) >= 2 && os.Args[1] == "help" {
		printHelp()
		os.Exit(0)
	}
	for _, a := range os.Args[1:] {
		if a == "--help" || a == "-h" {
			printHelp()
			os.Exit(0)
		}
	}
	// TODO for agent (EXTRA HARD):
	// Turn1 (EXTRA HARD): distance-based Dijkstra, undirected, nodes non-empty TrimSpace unique case-sensitive (A vs a distinct), may contain dot/slash hyphen underscore, edges from/to must exist TrimSpace invalid, distance >0 int/float/scientific 1e3, extra top-level/edge fields ignored, duplicate edges keep min, invalid → exit2. Requests: JSON array, each {source,destination} or {from,to} prefer source/dest, extra ignored, empty/whitespace → no-route [] -1 not invalid, missing field / not-string → invalid exit2, order preserved. Unknown flag including --traffic in Turn1 → exit2. Tie-break 1e-9: distance → lexicographically smallest path (sort neighbors, compare element-wise case-sensitive, shorter wins if prefix, 5-way tie). Performance 1000 nodes 5000 edges 1 req <2.5s, 100 nodes 500 batch <4s, 500 nodes 1 req <2s.
	// Turn2 (EXTRA HARD): extends same binary, adds --traffic flag, help must contain traffic (6 keywords). Traffic JSON may be {"traffic":[...]} or direct array [...]. Each entry from,to non-empty whitespace invalid, nodes must exist, edge must exist undirected, factor required >0 int/float/scientific, delay optional >=0 default0 int/float/scientific, extra unknown fields (other than factor/delay) ignored, duplicate last-wins including different direction A-B,B-A last wins factor+delay, self-loop invalid, missing entry → factor1 delay0, missing traffic key in object invalid but direct array valid, invalid → exit2.
	// Routing Turn2: effective = distance*factor + delay, minimize sum effective, raw = sum distance, traffic_delay=eff-raw. Tie-break EXTRA HARD 3-level: effective (1e-9) → raw distance → lexicographic (sort neighbors). Example secondary raw: A-B-D eff12 raw11 vs A-C-D eff12 raw4 → pick A-C-D raw smaller. Tests include 3-way eff equal raw equal (B<C<E) and 5-way. Source==dest 0 values. No path -1 all fields. Output with traffic includes distance, effective_distance, traffic_delay. Batch with traffic order preserved, some no-route exit1. Performance: 1000 nodes 5000 edges 100 req with traffic <2s, 500 nodes 200 req <2.5s, 500 batch <4s. Stdlib only.
	fmt.Fprintln(os.Stderr, "not implemented – extend this file")
	os.Exit(2)
}

func printHelp() {
	fmt.Println(`Usage: router --graph <PATH> --from <NODE> --to <NODE> [--traffic <PATH>] [--requests <PATH>]
Flags:
  --graph <PATH>     path to graph JSON, contains nodes and edges
  --from <NODE>      source node
  --to <NODE>        destination node
  --requests <PATH>  batch requests JSON array
  --traffic <PATH>   traffic multipliers JSON (factor + delay)
  --help, -h, help   show this help

Graph JSON:
  {"nodes":["A","B"],"edges":[{"from":"A","to":"B","distance":5}]}

Traffic JSON:
  {"traffic":[{"from":"A","to":"B","factor":2.5,"delay":3}]}

Examples:
  router --graph graph.json --from A --to C
  router --graph graph.json --requests req.json
  router --graph graph.json --from A --to C --traffic traffic.json`)
}
