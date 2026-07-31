#!/bin/bash
set -euo pipefail
mkdir -p /app
cat > /app/go.mod <<'EOF'
module router
go 1.22
EOF
cat > /app/main.go <<'GOEOF'
package main

import (
    "container/heap"
    "encoding/json"
    "flag"
    "fmt"
    "math"
    "os"
    "sort"
    "strings"
)

type GraphFile struct {
    Nodes []string `json:"nodes"`
    Edges []Edge   `json:"edges"`
}
type Edge struct {
    From     string  `json:"from"`
    To       string  `json:"to"`
    Distance float64 `json:"distance"`
}
type TrafficEntryRaw struct {
    From   string   `json:"from"`
    To     string   `json:"to"`
    Factor *float64 `json:"factor"`
    Delay  *float64 `json:"delay"`
}
type TrafficFileObj struct {
    Traffic []TrafficEntryRaw `json:"traffic"`
}
type SingleOut struct {
    Path     []string `json:"path"`
    Distance float64  `json:"distance"`
}
type SingleOutTraffic struct {
    Path              []string `json:"path"`
    Distance          float64  `json:"distance"`
    EffectiveDistance float64  `json:"effective_distance"`
    TrafficDelay      float64  `json:"traffic_delay"`
}
type BatchOut struct {
    Source      string   `json:"source"`
    Destination string   `json:"destination"`
    Path        []string `json:"path"`
    Distance    float64  `json:"distance"`
}
type BatchOutTraffic struct {
    Source            string   `json:"source"`
    Destination       string   `json:"destination"`
    Path              []string `json:"path"`
    Distance          float64  `json:"distance"`
    EffectiveDistance float64  `json:"effective_distance"`
    TrafficDelay      float64  `json:"traffic_delay"`
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

func comparePaths(a, b []string) int {
    ml := len(a)
    if len(b) < ml { ml = len(b) }
    for i:=0;i<ml;i++ { if a[i] < b[i] { return -1 }; if a[i] > b[i] { return 1 } }
    if len(a) < len(b) { return -1 }
    if len(a) > len(b) { return 1 }
    return 0
}

type Item struct {
    node string
    eff  float64
    raw  float64
    path []string
}
type PriorityQueue []*Item
func (pq PriorityQueue) Len() int { return len(pq) }
func (pq PriorityQueue) Less(i,j int) bool {
    const eps=1e-9
    if math.Abs(pq[i].eff-pq[j].eff) > eps { return pq[i].eff < pq[j].eff }
    if math.Abs(pq[i].raw-pq[j].raw) > eps { return pq[i].raw < pq[j].raw }
    return comparePaths(pq[i].path, pq[j].path) < 0
}
func (pq PriorityQueue) Swap(i,j int) { pq[i], pq[j] = pq[j], pq[i] }
func (pq *PriorityQueue) Push(x interface{}) { *pq = append(*pq, x.(*Item)) }
func (pq *PriorityQueue) Pop() interface{} {
    old := *pq
    n := len(old)
    it := old[n-1]
    *pq = old[0:n-1]
    return it
}

func parseGraph(path string) (map[string]map[string]float64, map[string]bool, bool) {
    data, err := os.ReadFile(path)
    if err != nil { return nil,nil,false }
    var gf GraphFile
    if err := json.Unmarshal(data, &gf); err != nil { return nil,nil,false }
    if gf.Nodes == nil || len(gf.Nodes)==0 { return nil,nil,false }
    nodeSet := make(map[string]bool)
    for _, n := range gf.Nodes {
        if strings.TrimSpace(n) == "" { return nil,nil,false }
        if nodeSet[n] { return nil,nil,false }
        nodeSet[n]=true
    }
    if gf.Edges == nil { gf.Edges = []Edge{} }
    adj := make(map[string]map[string]float64)
    for _, nd := range gf.Nodes { adj[nd]=make(map[string]float64) }
    for _, e := range gf.Edges {
        if strings.TrimSpace(e.From)==""||strings.TrimSpace(e.To)=="" { return nil,nil,false }
        if e.From==e.To { return nil,nil,false }
        if !nodeSet[e.From]||!nodeSet[e.To] { return nil,nil,false }
        if e.Distance<=0 || math.IsNaN(e.Distance) || math.IsInf(e.Distance,0) { return nil,nil,false }
        // UNDIRECTED balanced
        if existing, ok := adj[e.From][e.To]; ok {
            if e.Distance < existing {
                adj[e.From][e.To]=e.Distance
                adj[e.To][e.From]=e.Distance
            }
        } else {
            adj[e.From][e.To]=e.Distance
            adj[e.To][e.From]=e.Distance
        }
    }
    return adj, nodeSet, true
}

type TrafficParsed struct {
    Factor float64
    Delay  float64
}

func parseTraffic(path string, nodeSet map[string]bool, adj map[string]map[string]float64) (map[string]TrafficParsed, bool) {
    if path == "" { return make(map[string]TrafficParsed), true }
    data, err := os.ReadFile(path)
    if err != nil { return nil,false }
    var rawEntries []TrafficEntryRaw
    var generic map[string]json.RawMessage
    if err := json.Unmarshal(data, &generic); err == nil {
        if _, ok := generic["traffic"]; ok {
            var obj TrafficFileObj
            if err := json.Unmarshal(data, &obj); err != nil { return nil,false }
            rawEntries = obj.Traffic
        } else {
            return nil,false
        }
    } else {
        if err := json.Unmarshal(data, &rawEntries); err != nil { return nil,false }
    }
    if rawEntries == nil { rawEntries = []TrafficEntryRaw{} }
    factorMap := make(map[string]TrafficParsed)
    for _, te := range rawEntries {
        if strings.TrimSpace(te.From)==""||strings.TrimSpace(te.To)=="" { return nil,false }
        if te.From==te.To { return nil,false }
        if te.Factor==nil { return nil,false }
        if *te.Factor<=0 || math.IsNaN(*te.Factor) || math.IsInf(*te.Factor,0) { return nil,false }
        delay:=0.0
        if te.Delay!=nil {
            if *te.Delay<0 || math.IsNaN(*te.Delay) || math.IsInf(*te.Delay,0) { return nil,false }
            delay=*te.Delay
        }
        if !nodeSet[te.From]||!nodeSet[te.To] { return nil,false }
        if _, ok := adj[te.From][te.To]; !ok { return nil,false }
        a,b := te.From, te.To
        if a > b { a,b = b,a }
        key := a+"|"+b // UNDIRECTED key for balanced difficulty
        factorMap[key]=TrafficParsed{Factor:*te.Factor, Delay:delay}
    }
    return factorMap, true
}

func getEffective(from,to string, raw float64, factorMap map[string]TrafficParsed) (float64, float64) {
    if factorMap==nil { return raw, raw }
    a,b := from,to
    if a>b { a,b=b,a }
    key := a+"|"+b
    if tp, ok := factorMap[key]; ok {
        eff := raw*tp.Factor + tp.Delay
        return raw, eff
    }
    return raw, raw
}

func dijkstra(adj map[string]map[string]float64, factorMap map[string]TrafficParsed, source, dest string, nodeSet map[string]bool) ([]string, float64, float64, bool) {
    const eps=1e-9
    if !nodeSet[source]||!nodeSet[dest] { return nil,-1,-1,false }
    if source==dest { return []string{source},0,0,true }
    bestEff := make(map[string]float64)
    bestRaw := make(map[string]float64)
    bestPath := make(map[string][]string)
    for n:=range nodeSet { bestEff[n]=math.Inf(1); bestRaw[n]=math.Inf(1) }
    bestEff[source]=0; bestRaw[source]=0; bestPath[source]=[]string{source}
    pq:=&PriorityQueue{}
    heap.Init(pq)
    heap.Push(pq, &Item{node:source, eff:0, raw:0, path:[]string{source}})
    for pq.Len()>0 {
        it:=heap.Pop(pq).(*Item)
        u:=it.node
        if it.eff > bestEff[u]+eps { continue }
        if math.Abs(it.eff-bestEff[u])<=eps {
            if math.Abs(it.raw-bestRaw[u])>eps {
                if it.raw > bestRaw[u]+eps { continue }
            }
            if comparePaths(it.path, bestPath[u])>0 { continue }
        }
        if u==dest { break }
        neigh:=make([]string,0,len(adj[u]))
        for v:=range adj[u] { neigh=append(neigh,v) }
        sort.Strings(neigh)
        for _, v:=range neigh {
            rawEdge := adj[u][v]
            _, effEdge := getEffective(u,v,rawEdge,factorMap)
            newEff := it.eff+effEdge
            newRaw := it.raw+rawEdge
            newPath := append(append([]string{}, it.path...), v)
            if newEff+eps < bestEff[v] {
                bestEff[v]=newEff; bestRaw[v]=newRaw; bestPath[v]=newPath
                heap.Push(pq, &Item{node:v, eff:newEff, raw:newRaw, path:newPath})
            } else if math.Abs(newEff-bestEff[v])<=eps {
                if newRaw+eps < bestRaw[v] {
                    bestEff[v]=newEff; bestRaw[v]=newRaw; bestPath[v]=newPath
                    heap.Push(pq, &Item{node:v, eff:newEff, raw:newRaw, path:newPath})
                } else if math.Abs(newRaw-bestRaw[v])<=eps {
                    if comparePaths(newPath, bestPath[v])<0 {
                        bestEff[v]=newEff; bestRaw[v]=newRaw; bestPath[v]=newPath
                        heap.Push(pq, &Item{node:v, eff:newEff, raw:newRaw, path:newPath})
                    }
                }
            }
        }
    }
    if math.IsInf(bestEff[dest],1) { return nil,-1,-1,false }
    return bestPath[dest], bestRaw[dest], bestEff[dest], true
}

func main() {
    if len(os.Args)==1 { printHelp(); os.Exit(0) }
    if len(os.Args)>=2 && os.Args[1]=="help" { printHelp(); os.Exit(0) }
    fs:=flag.NewFlagSet("router", flag.ContinueOnError)
    fs.SetOutput(os.Stderr)
    var graphPath, fromNode, toNode, requestsPath, trafficPath, sourceAlt, destAlt string
    var helpFlag bool
    fs.StringVar(&graphPath, "graph", "", "graph")
    fs.StringVar(&fromNode, "from", "", "from")
    fs.StringVar(&toNode, "to", "", "to")
    fs.StringVar(&sourceAlt, "source", "", "source")
    fs.StringVar(&destAlt, "destination", "", "dest")
    fs.StringVar(&requestsPath, "requests", "", "requests")
    fs.StringVar(&trafficPath, "traffic", "", "traffic")
    fs.BoolVar(&helpFlag, "help", false, "help")
    fs.BoolVar(&helpFlag, "h", false, "help")
    if err:=fs.Parse(os.Args[1:]); err!=nil { os.Exit(2) }
    if helpFlag { printHelp(); os.Exit(0) }
    if graphPath=="" { os.Exit(2) }
    if fromNode=="" { fromNode=sourceAlt }
    if toNode=="" { toNode=destAlt }
    adj, nodeSet, ok := parseGraph(graphPath)
    if !ok { os.Exit(2) }
    factorMap, ok := parseTraffic(trafficPath, nodeSet, adj)
    if !ok { os.Exit(2) }
    usingTraffic := trafficPath != ""
    if requestsPath!="" {
        data, err:=os.ReadFile(requestsPath)
        if err!=nil { os.Exit(2) }
        var rawArr []json.RawMessage
        if err:=json.Unmarshal(data, &rawArr); err!=nil { os.Exit(2) }
        type pair struct{src,dst string}
        var pairs []pair
        valid:=true
        for _, raw:=range rawArr {
            var m map[string]interface{}
            if err:=json.Unmarshal(raw, &m); err!=nil { valid=false; break }
            var srcStr,dstStr string
            var srcFound,dstFound bool
            if v,ok:=m["source"]; ok {
                if s, ok2:=v.(string); ok2 { srcStr=s; srcFound=true } else { valid=false; break }
            } else if v,ok:=m["from"]; ok {
                if s, ok2:=v.(string); ok2 { srcStr=s; srcFound=true } else { valid=false; break }
            }
            if v,ok:=m["destination"]; ok {
                if s, ok2:=v.(string); ok2 { dstStr=s; dstFound=true } else { valid=false; break }
            } else if v,ok:=m["to"]; ok {
                if s, ok2:=v.(string); ok2 { dstStr=s; dstFound=true } else { valid=false; break }
            }
            if !srcFound||!dstFound { valid=false; break }
            pairs=append(pairs, pair{srcStr,dstStr})
        }
        if !valid { os.Exit(2) }
        anyNoRoute:=false
        for _, p:=range pairs {
            if strings.TrimSpace(p.src)==""||strings.TrimSpace(p.dst)=="" {
                anyNoRoute=true
                if usingTraffic {
                    out:=BatchOutTraffic{Source:p.src, Destination:p.dst, Path:[]string{}, Distance:-1, EffectiveDistance:-1, TrafficDelay:-1}
                    b,_:=json.Marshal(out)
                    fmt.Println(string(b))
                } else {
                    out:=BatchOut{Source:p.src, Destination:p.dst, Path:[]string{}, Distance:-1}
                    b,_:=json.Marshal(out)
                    fmt.Println(string(b))
                }
                continue
            }
            path,rawD,effD,found:=dijkstra(adj,factorMap,p.src,p.dst,nodeSet)
            if !found {
                anyNoRoute=true
                if usingTraffic {
                    out:=BatchOutTraffic{Source:p.src, Destination:p.dst, Path:[]string{}, Distance:-1, EffectiveDistance:-1, TrafficDelay:-1}
                    b,_:=json.Marshal(out)
                    fmt.Println(string(b))
                } else {
                    out:=BatchOut{Source:p.src, Destination:p.dst, Path:[]string{}, Distance:-1}
                    b,_:=json.Marshal(out)
                    fmt.Println(string(b))
                }
            } else {
                if usingTraffic {
                    out:=BatchOutTraffic{Source:p.src, Destination:p.dst, Path:path, Distance:rawD, EffectiveDistance:effD, TrafficDelay:effD-rawD}
                    b,_:=json.Marshal(out)
                    fmt.Println(string(b))
                } else {
                    out:=BatchOut{Source:p.src, Destination:p.dst, Path:path, Distance:rawD}
                    b,_:=json.Marshal(out)
                    fmt.Println(string(b))
                }
            }
        }
        if anyNoRoute { os.Exit(1) }
        os.Exit(0)
    } else {
        if strings.TrimSpace(fromNode)==""||strings.TrimSpace(toNode)=="" { os.Exit(2) }
        path,rawD,effD,found:=dijkstra(adj,factorMap,fromNode,toNode,nodeSet)
        if !found {
            if usingTraffic {
                out:=SingleOutTraffic{Path:[]string{}, Distance:-1, EffectiveDistance:-1, TrafficDelay:-1}
                b,_:=json.Marshal(out)
                fmt.Println(string(b))
            } else {
                out:=SingleOut{Path:[]string{}, Distance:-1}
                b,_:=json.Marshal(out)
                fmt.Println(string(b))
            }
            os.Exit(1)
        } else {
            if usingTraffic {
                out:=SingleOutTraffic{Path:path, Distance:rawD, EffectiveDistance:effD, TrafficDelay:effD-rawD}
                b,_:=json.Marshal(out)
                fmt.Println(string(b))
            } else {
                out:=SingleOut{Path:path, Distance:rawD}
                b,_:=json.Marshal(out)
                fmt.Println(string(b))
            }
            os.Exit(0)
        }
    }
}
GOEOF
cd /app && go build -o router . && echo "Build ok step2 undirected balanced factor+delay + raw tie"
