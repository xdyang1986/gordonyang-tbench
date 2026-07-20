Broker Message Fan-Out Allocator — Bug Fix
A small Go program in /app (main.go) implements a message broker's fan-out allocator. Given a batch of pending messages and a list of subscribers (each with a weight and capacity), it determines how many messages to route to each subscriber — distributing proportionally by weight without exceeding any subscriber's capacity.

The program compiles and runs, but contains a bug: certain inputs produce incorrect allocations. Your goal is to locate and fix the defect so the program behaves correctly for all inputs. The underlying algorithm is correct aside from this one flaw — do not rewrite from scratch, and preserve the existing input/output format.

Input / Output Format
Read from stdin:
<load>
<weight> <cap>
<weight> <cap>
...
<load> — total number of messages to distribute (non-negative integer).

Each subsequent line defines a subscriber with an integer weight (≥ 1) and integer cap (≥ 0).

Write to stdout a single line: the allocation for each subscriber (in input order), comma-separated. No subscriber receives more than its cap, and the total allocated equals min(load, sum of all caps).

Build command: cd /app && go build -o /app/allocator .

Known Failing Cases
The program currently produces incorrect output for these inputs. Expected (correct) output is shown.

Case 1:
16
5 6
3 9
4 3
1 12
Expected: 6,5,3,2

Case 2:
9
5 10
6 10
6 1
Expected: 3,5,1

Case 3:
6
4 11
1 6
2 5
Expected: 4,0,2

Requirements
Fix the bug in /app so the allocator produces correct results for the cases above and in general.

The fix should address the same weighted-capacity-sharing logic already present — just without the defect.

Use only the Go standard library.
