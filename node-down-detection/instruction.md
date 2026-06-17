# Requirement: Node Failure Probability Detection

## Goal

Implement a feature in Go that, for a cluster of nodes, estimates the **probability that any given node is down**. The cluster may contain many nodes
spread across the same region or across different regions. The result must be a probability, not a simple up/down boolean.

## Functional Requirements

1. The system must produce, for each target node, a probability in the range `[0, 1]` representing how likely it is that the node is genuinely down.
2. The estimate must be based on observations from multiple nodes, not a single observer.
3. The detection must be **region-aware**: all nodes in the same region may down at the same time.
4. The probability must increase smoothly as evidence of failure accumulates, rather than flipping abruptly at a fixed threshold.
5. A target node about which there is no information must be treated as fully suspect (probability `1`) until evidence of liveness is received.
6. If a node is still reachable from at least one region, its down-probability must stay low; A node unreachable from every region must trend toward 1.
7. Suspicion must adapt to each link's observed time variability.

## Inputs
- Liveness signals (heartbeats) observed by monitoring nodes, each identifying the observing node, the observed node, and the time observed.
- The region each monitoring node belongs to.

## Outputs
- For any target node, a down-probability in `[0, 1]`.
- A per-region breakdown of the evidence contributing to that probability.
- NodeStatus, Regions is ordered by region name (ascending)
- Observers is ordered by name in each region (ascending)
- StatusAll returns nodes ordered by target name (ascending)

## Non-Functional Requirements
- Written in Go.
- Safe for concurrent use (many observers reporting and many queries at once).
- Transport-agnostic: how heartbeats are delivered (gRPC, UDP, gossip, etc.) is
  out of scope; the feature consumes already-received liveness signals.
- Deterministic and testable: given the same inputs and query time, the output must be reproducible.
- The library reports probabilities only. Any action taken in response (alerting, eviction, quorum decisions) is the responsibility of the caller.

API should looks like this:
  - The package must be named failuredetector and live at module path github.com/example/nodedown (i.e. /app/failuredetector/) — the test is
  package failuredetector_test importing that path.
  - Standard library only.

  package failuredetector

  import "time"

  type Config struct {
        WindowSize             int           // recent inter-arrival samples retained. Default 200.
        MinStdDev              time.Duration // floor on the modelled std-dev. Default 100ms.
        FirstHeartbeatEstimate time.Duration // expected heartbeat period; seeds the window before any interval. Default 1s.
  }

  // Layer 1: one observer's view of one target.
  func NewPhiDetector(cfg Config) *PhiDetector
  func (d *PhiDetector) Heartbeat(now time.Time)
  func (d *PhiDetector) Phi(now time.Time) float64
  func (d *PhiDetector) Probability(now time.Time) float64
  func (d *PhiDetector) LastHeartbeat() (time.Time, bool)

  // Layer 2: region-aware aggregation over many observers.
  type ObserverReport struct {
        Observer    string
        Region      string
        Phi         float64
        Probability float64
  }

  type RegionReport struct {
        Region      string
        Observers   []ObserverReport
        Probability float64
  }

  type NodeStatus struct {
        Target          string
        DownProbability float64
        Regions         []RegionReport
  }

  func NewCluster(cfg Config) *Cluster
  func (c *Cluster) RegisterObserver(observer, region string)
  func (c *Cluster) Heartbeat(observer, target string, now time.Time)
  func (c *Cluster) Status(target string, now time.Time) NodeStatus
  func (c *Cluster) StatusAll(now time.Time) []NodeStatus
