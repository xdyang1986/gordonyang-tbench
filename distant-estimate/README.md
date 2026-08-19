# codimango/distant-estimate – Multi-Turn Go Path Routing: Raw → Directed Traffic (GIGA HARD EXTRA v4)
## Description
- Turn1 (~81 tests) raw-distance Dijkstra undirected min duplicate unordered including reverse.
- Turn2 (~69 tests) extends with DIRECTED traffic breaking Step1 invariant: A->B factor != B->A factor. Raw graph remains undirected collapsed, but traffic adjacency must be split into directed arcs. Routing effective = raw*factor+delay per directed edge, raw along effective-best, traffic_delay negative allowed when factor<1. Worked example 35 vs 40 discriminates per-edge grouping. Duplicate same ordered last-wins including delay reset, reverse distinct. Forms wrapper or direct array, null vs [] distinct, BOM/trailing/comment must not crash, leading/trailing spaces exact, factor>0 delay>=0 default 1/0, tie-break effective 1e-9 -> raw 1e-9 -> lex ASCII '-'<'.'<'_' 'A'<'a'. Output strict 2/4 keys single, 4/6 batch, -1 no-route. Perf trimmed: 500<2s 2000<3.5s 5000<5.5s, batch relative 25*base+1, same-source amortization. OOM fix via prev-pointer cache. Timeout 1800 fixes fairness.
## Why v4
1. Breaks Step1 invariant undirected collapse -> directed arcs forces refactor.
2. Deleted giveaways: removed explicit wrong formula hints, replaced with worked example.
3. Cut tests 445->150 reduces wall-clock.
4. Raised timeout 1200->1800.
