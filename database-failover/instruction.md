Build a database failover solution using Go language. The solution should be able to handle the failover process in a seamless manner, ensuring minimal downtime and data loss. It consumes a stream of health/position snapshots and must decide what action to take per tick.
Let's build the program runnable via go run . in /app.

The decision algorithm for deciding the right candidate must minimize data loss, and don't react to a single blip, any node that loses more than max-loss should not be promoted and if the previous primary recovered, you should let it rejoin the cluster.
your monitor receives a feed from a live cluster that only reports what changed each tick, a node's last observed state persists until a newer observation replaces it, there is no timeout/expiry. Every decision is made against the latest known state of every node seen so far, not just the nodes in the current tick.
1. debounce N = 3 consecutive down (with reset-on-up);
2. elect highest position; ties → priority → lowest id, higher priority win.

stdin input — time-series of snapshots
One observation per line; a leading tick column groups lines into per-tick snapshots, processed in ascending order:

# tick id    role     health  pos    prio
0    db1     primary  up      1000   10
0    db2     replica  up      998    5
0    db3     replica  up      1000   5
0    db4     replica  up      995    1
1    db1     primary  down    1000   10
1    db3     replica  up      1001   5
2    db1     primary  down    1000   10
2    db3     replica  up      1002   5
3    db1     primary  down    1000   10
3    db3     replica  up      1003   5

For output: the gradeable surface, exactly: PROMOTE <id> / ABORT / REJOIN <node> <primary>, one line per event in tick order, empty if nothing fires, and the exit codes (0/1/2).

REJOIN: A REJOIN <node> <primary> line is emitted only when a previously-fenced ex-primary returns up, once, targeting the current primary; surviving replicas never emit REJOIN (they reattach silently on stderr). Within a tick the PROMOTE/ABORT decision prints first, then any REJOIN lines sorted by node id, and across ticks everything is in ascending tick order.
Exit codes: 0 = the stream ended with a live primary (no failover needed, or all failovers succeeded); 1 = it ended with a dead, unreplaced primary (an ABORT with no later recovery); 2 = bad input or flags (unparseable line or invalid -threshold), with no decisions emitted.
max-loss is a fixed default of 0 (overridable via -max-loss), and the failover aborts with data_loss when the elected winner's missing count — measured as (highest position anywhere in the cluster, including the dead primary and lagging nodes) − winner's position — exceeds it, so a behind-candidate that trails the cluster max by more than max-loss triggers an abort. After an abort the consecutive-down counter resets to 0, so on each subsequent tick the still-down primary re-accumulates downs and re-attempts the election once it again hits N=3 consecutive downs — meaning if the lagging candidate has caught up past the max-loss bar by then, that later attempt emits PROMOTE
