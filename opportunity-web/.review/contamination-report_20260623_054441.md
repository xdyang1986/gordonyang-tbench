# Contamination Check Report: codimango/opportunity-web

## Task Identity (Check 1)
- **Path:** /data/repos/workspace/gordonyang-tbench/opportunity-web
- **Type:** terminal-bench (has `tests/test.sh`; no `tests/config.json`)
- **Task name:** codimango/opportunity-web
- **Domain:** web (React + TypeScript + Vite single-page app)
- **Repo (swe-bench-pro):** N/A
- **Instance ID (swe-bench-pro):** N/A

## Internal Decontamination Table (Check 2)
- **Result:** SKIPPED (no source reachable from this agent environment)
- **Snapshot:** unavailable (could not fetch)
- **Details:** All lookup transports failed:
  - **Source A — Manifold CLI** (`manifold get multimango_public/tree/aai_decontamination/decontaminated_delta.json`): `PERMISSION_DENIED error_code=ABAC_AGENT_ROLE_DENIED agent_role=AGENT:dev.oss … asset=asset://manifold.bucket/multimango_public`. The current agent role (`dev.oss` / `3p_ai_tools` on the devserver) is not authorized to read the snapshot via DevProxy. (`xxhash` was also not importable for the prompt-hash key, but Source A was blocked regardless.)
  - **Source A2 — Python `ManifoldClient`:** not attempted — same ABAC restriction applies; `manifold` module not importable in this env.
  - **Source B1 — precomputed block:** not present (this run is not driven by the multimango contamination runner, which would inject the server-side result).
  - **Source B2 — multimango HTTPS endpoint** (`/api/v1/aai-decontaminated-delta/lookup`): returned `{"error":"Authentication required"}` (HTTP 401) — no `MM_API_KEY` set in this environment.

## Overall Contamination Risk

**Risk Level:** NOT EVALUATED (treat as "not cleared")

**Summary:** The internal decontamination table could not be queried from this environment (Manifold ABAC denial + no `MM_API_KEY` + no precomputed block). Independently, this task is brand-new and **not yet submitted/pushed**, so it has almost certainly not been processed by the `aai_decontamination` pipeline yet — a live lookup would most likely return **NOT FOUND** until ~8 hours after a commit triggers a run. Contamination has therefore been neither confirmed nor cleared.

**Action Required:**
- This is a **no-signal** result, not a clearance. Re-run the contamination check after the task is committed/pushed to the `gordonyang-tbench` repo and the decontamination pipeline has had time to process it (wait up to ~8h). The multimango contamination runner performs this lookup server-side automatically once the task is submitted, so the cleanest path is: submit → wait → read the verdict in the multimango task page (or re-run this skill from an environment with `MM_API_KEY` / Manifold access).
- Until then, default the contamination status to **"not evaluated, not cleared."**

**Evidence:** N/A (no rows retrieved — lookup skipped). Transport errors quoted above.

> Report written to `/data/repos/workspace/gordonyang-tbench/opportunity-web/.review/contamination-report_20260623_054441.md`
