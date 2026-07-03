  Implement a command-line tool, in Go, that computes each region's
  disaster-recovery (DR) buffer for a multi-region service.

  The rules are not given inline. The specification is split across two documents
  shipped in this environment:

  - `/app/docs/policy.md` — the reliability policy: how capacity and demand are
    reported (units and the accepted input encodings), the safety limit, how load
    is redistributed when a region fails, the exact output contract, and what
    makes a report invalid.
  - `/app/docs/incident.md` — a post-incident review whose worked numeric example
    confirms the redistribution rule and the overflow condition.

  Read both and reconcile them. The policy is authoritative; the incident review
  illustrates it.

  ## Runtime contract

  - The tool reads a single JSON report from standard input and writes a single
    JSON object to standard output, as defined by the policy's reporting contract.
    All output quantities use the canonical unit defined by the policy.
  - On an invalid report, exit non-zero and print no JSON.

  ## Build contract

  - Language: Go
  - Module root: /app
  - Build: `cd /app && go build -o /app/drbuffer .`
