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
	// TODO for agent (balanced difficulty):
	// Turn1 (HARD but solvable): distance-based Dijkstra, undirected graph, nodes non-empty TrimSpace unique case-sensitive, edges from/to must exist, distance >0 int/float/scientific notation (1e3), extra top-level/edge fields ignored, duplicate edges keep min distance, invalid → exit2.
	// Requests: JSON array of {source,destination} or {from,to}, prefer source/destination if both present, extra fields ignored, empty or whitespace-only source/dest → no route (not invalid) with [] and -1, output order preserved. Unknown flag (including --traffic in Turn1) → exit2.
	// Routing: Dijkstra minimizing sum distance, tie-break lexicographically smallest path within 1e-9 tolerance (sort neighbors, compare path element-wise case-sensitive, shorter wins if prefix). Performance 500 nodes 2000 edges 100 req <2s, 200 req <3s.
	// Output single: {"path":[...],"distance":N} exit0, no path {"path":[],"distance":-1} exit1, invalid exit2. Batch one JSON line per request in order with source,destination,path,distance. Exit 0 all routed, 1 some no route, 2 invalid.
	// Help Turn1: must contain graph, from, to, requests, help (5 keywords), bare no args → help exit0.
	// Turn2 (BALANCED): extends same binary, adds --traffic flag, help must now contain traffic keyword (6 total). Traffic JSON may be {"traffic":[...]} or direct array [...]. Each entry from,to must be nodes, edge must exist undirected, factor required >0 int/float/scientific, delay optional >=0 default 0 int/float/scientific, extra unknown fields (other than factor/delay) ignored, duplicate last-wins (A-B and B-A same undirected, last wins), self-loop invalid, missing entry → factor1 delay0, invalid → exit2.
	// Routing Turn2: effective = distance*factor + delay, minimize sum effective, raw = sum distance along chosen path, delay = effective-raw. Tie-break on effective (1e-9) → lexicographically smallest (2-level, NOT raw secondary). Source==dest 0 values. No path -1 for all distance fields. Output with traffic: includes distance, effective_distance, traffic_delay. Batch with traffic includes those extra fields.
	// Performance with traffic 500 nodes 2000 edges 100 req <2.5s. Stdlib only.
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
