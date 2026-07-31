package main

import (
	"fmt"
	"os"
)

// Starter skeleton – implements help only.
// The ground-truth solution overwrites this file.
// Keep help keywords: graph, from, to, requests, traffic, help

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
	// TODO for agent:
	// 1. Parse flags --graph (required), --from/--to or --source/--destination, --requests (batch), --traffic (optional)
	// 2. Graph is UNDIRECTED (or DIRECTED per instruction – read instruction.md carefully). Nodes non-empty TrimSpace, unique, case-sensitive. Edges from/to must exist, distance >0 float, extra fields ignored. Invalid → exit 2 no stdout.
	// 3. Requests: JSON array of {source,destination} or {from,to}, extra fields ignored, empty TrimSpace → no route.
	// 4. Traffic: JSON may be {"traffic":[...]} or direct array [...]. Each entry from,to must be nodes, edge must exist undirected, factor required >0 int/float, delay optional >=0 default 0, extra fields ignored, duplicate last-wins, self-loop invalid. Missing entry → factor 1.0 delay 0. Effective = distance* factor + delay.
	// 5. Routing: Dijkstra minimizing effective distance (if no traffic, effective=distance). Tie-break 3-level: effective (eps 1e-9) → raw distance → lexicographically smallest path case-sensitive (sort neighbors).
	// 6. Output:
	//    Single no traffic: {"path":[...],"distance":N} exit0, no path {"path":[],"distance":-1} exit1, invalid exit2
	//    Single with traffic: {"path":[...],"distance":raw,"effective_distance":eff,"traffic_delay":eff-raw} + same no path -1 fields
	//    Batch: one JSON line per request in order with source,destination,path,distance (+ effective,traffic_delay if traffic)
	//    Batch exit 0 all routed, 1 some no route, 2 invalid
	// 7. Help must contain keywords graph, from, to, requests, traffic, help
	// 8. Performance: 500 nodes 2000 edges 100 requests <2s
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
