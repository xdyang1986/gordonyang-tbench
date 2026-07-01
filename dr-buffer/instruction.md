Scenario
A distributed service spans multiple geographic regions. When regions fail, their traffic redistributes to survivors proportional to capacity — but no region can exceed its own capacity, so overflow cascades to the remaining regions.

Build a tool that, given the current load profile and a simultaneous failure count k, reports the worst-case peak load each region would face and whether the system can survive every possible k-region failure, if it cannot survive, calculate the minimum capacity needed to make it so.

Input
JSON on stdin:

json

Copy
{
  "k": 1,
  "regions": [
    {"name": "us-east", "capacity": 100, "load": 60},
    {"name": "us-west", "capacity": 100, "load": 40}
  ]
}
k — number of regions that may fail at once.

regions — each with a name, positive capacity, and load (0 ≤ load ≤ capacity).

Redistribution Rules
When k regions fail, their combined load is spread across the survivors in proportion to their capacity up to its capacity. Other remaining load spills onto others until no headroom remains.

Each survivor receives a share proportional to its capacity.

If a survivor would exceed capacity, it fills to capacity (saturates) and the excess cascades to remaining unsaturated survivors.

Repeat until all load is placed or no headroom remains.

Output
JSON on stdout, regions in input order:

json

Copy
{
  "k": 1,
  "resilient": true,
  "capacityShortfall": 0,
  "regions": [
    {"name": "us-east", "capacity": 100, "peakLoad": 100, "peakBuffer": 40},
    {"name": "us-west", "capacity": 100, "peakLoad": 100, "peakBuffer": 60}
  ]
}
Field	Meaning
peakLoad	Highest load this region reaches across all size-k failures it survives. Equals load when k=0.
peakBuffer	peakLoad − load
resilient	true iff every failure scenario is fully absorbed; otherwise false (peak loads still reported — unabsorbable cases fill survivors to capacity).
Floating-point tolerance: 1e-6.
capacityShortfall — 0 if resilient; otherwise the minimum total extra capacity that must be added so the fleet can absorb every k-region failure (equivalently, the largest amount of load left unplaced in any single worst-case failure).

Invalid Input
Exit non-zero (no JSON output) for: malformed JSON, empty regions, k < 0, k ≥ N, non-positive capacity, negative load, load > capacity, duplicate names.

Worked Example
Three regions, each capacity 100, loads A=90, B=40, C=40, k=1:

B fails → 40 offered to A and C proportionally. A has 10 headroom → saturates; overflow cascades to C → C ends at 70.

A fails → 90 splits evenly to B and C → both end at 85.
The fleet survives every single-region failure, so resilient is true and capacityShortfall is 0.

Result: peakLoad = A:100, B:85, C:85 · peakBuffer = A:10, B:45, C:45 · resilient: true

Build Contract
Language: Go

Module root: /app

Build: cd /app && go build -o /app/drbuffer .

Binary reads stdin, writes stdout.
