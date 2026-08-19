package main
import (
	"fmt"
	"os"
)
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
  --traffic <PATH>   directed traffic multipliers JSON (factor + delay per directed arc)
  --help, -h, help   show this help
Graph JSON:
  {"nodes":["A","B"],"edges":[{"from":"A","to":"B","distance":5}]}
Traffic JSON (directed):
  {"traffic":[{"from":"A","to":"B","factor":2.5,"delay":3}]}
  Direct array also: [{"from":"A","to":"B","factor":2.5}]
Examples:
  router --graph graph.json --from A --to C
  router --graph graph.json --requests req.json
  router --graph graph.json --from A --to C --traffic traffic.json`)
}
