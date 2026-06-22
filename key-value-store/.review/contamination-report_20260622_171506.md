# Contamination Check Report: codimango/key-value-store

## Task Identity (Check 1)
- **Path:** /data/repos/workspace/gordonyang-tbench/key-value-store
- **Type:** terminal-bench
- **Task name:** codimango/key-value-store
- **Domain:** systems / software-engineering (data structures + serialization + concurrency)
- **Repo (swe-bench-pro):** N/A
- **Instance ID (swe-bench-pro):** N/A

## Internal Decontamination Table (Check 2)
- **Result:** SKIPPED (lookup transport unavailable from this environment)
- **Snapshot:** not retrieved
- **Details:** All three lookup sources failed:
  - **Source A — Manifold CLI:** `manifold get multimango_public/tree/aai_decontamination/decontaminated_delta.json` returned HTTP 500 `PERMISSION_DENIED` / `ABAC_AGENT_ROLE_DENIED` (agent role `AGENT:dev.oss` is not allowed to access `asset://manifold.bucket/multimango_public` through DevProxy). Downloaded file was 0 bytes.
  - **Source B1 — precomputed block:** not present in the invocation prompt (this skill was run directly, not via the multimango contamination runner).
  - **Source B2 — multimango v1 endpoint:** `MM_API_KEY` not set in the environment, so the authenticated lookup cannot be performed (would return HTTP 401).
  - (Note: `xxhash` is also not installed locally, so the `prompt_hash` content key could not be computed for an offline lookup — moot given no snapshot/endpoint access.)

## Overall Contamination Risk

**Risk Level:** Not evaluated, not cleared

**Summary:** The internal decontamination table could not be queried from this dev environment (Manifold access denied by agent role; no precomputed block; no `MM_API_KEY`). Independently, this is a brand-new task created today and not yet submitted/pushed, so it would almost certainly be **NOT FOUND** in the decontamination snapshot regardless — the pipeline only covers tasks that have already been run through it. No CONTAMINATED/POSSIBLY_CONTAMINATED signal was observed (because no lookup result was obtained), and none can be asserted either way.

**Action Required:**
- **Re-run this contamination check after the task is committed/pushed and submitted to multimango.** The `aai_decontamination` pipeline refreshes roughly every 3 hours; allow up to ~8 hours after submission for a verdict to appear, then re-check.
- To run the lookup from this devserver, it needs one of: (a) Manifold access for the agent role (request via https://fburl.com/agent_roles_feedback_group), or (b) `MM_API_KEY` exported for the Source B2 endpoint, or (c) run the check through the multimango contamination runner (which injects the precomputed Source B1 block).
- Treat as **no-signal until then** — do not infer LOW from the absence of a result.

**Evidence:** Source A error — `apache::thrift::TApplicationException: PERMISSION_DENIED error_code=ABAC_AGENT_ROLE_DENIED agent_role=AGENT:dev.oss asset=asset://manifold.bucket/multimango_public`. Sources B1/B2 unavailable as noted above. No `gemini_final_decision` / `max_cosine_similarity` / `num_knn_matches` could be retrieved.
