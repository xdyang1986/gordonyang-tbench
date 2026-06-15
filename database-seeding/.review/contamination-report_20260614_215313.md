# Contamination Check Report: codimango/database-seeding

_Checked: 2026-06-14 21:53:13 UTC · skill: check-contamination-v2_

## Task Identity (Check 1)
- **Path:** /data/repos/workspace/gordonyang-tbench/database-seeding
- **Type:** terminal-bench (`tests/test.sh` present, no `tests/config.json`)
- **Task name:** codimango/database-seeding
- **Domain:** systems (streaming compression + framed TCP file transfer + atomic delivery)
- **Repo (swe-bench-pro):** N/A
- **Instance ID (swe-bench-pro):** N/A

## Internal Decontamination Table (Check 2)
- **Result:** NOT FOUND
- **Source:** A1 — Manifold CLI (`aai_eval_baselines/tree/quality_contamination/decontaminated_delta.json`)
- **Snapshot:** `exported_at=2026-06-14T19:23:26Z`, `age≈2.5h` (fresh; not stale)
- **Details:**
  - task_name index: `codimango/database-seeding` and `database-seeding` not found; 0 substring hits across 32,263 task entries.
  - content hash (`by_prompt_hash`, 35,098 entries): xxHash64 of current instruction.md = signed `-404350168283914075` (with trailing newline) / `7091945573209536507` (no trailing newline); 0 matches either variant.

## Overall Contamination Risk

**Risk Level:** LOW-with-caveat

**Summary:** The task does not appear in Meta's internal decontamination snapshot under either its task name or its prompt content hash, and the snapshot is current (2.5h old). NOT FOUND is not a clearance — the decontamination pipeline (KNN + N-gram + Gemini) has not evaluated this task, expected because it was authored locally and not yet pushed to codimango github.

**Action Required:**
- Treat as no-signal until evaluated, not a clean pass.
- Submit the task to the codimango github repo so the aai_decontamination pipeline runs and computes a real verdict (allow up to ~8h after submission).
- Re-run this check once processed to confirm NOT_CONTAMINATED.

**Evidence:** No matching rows (verdict is absence-of-row; no gemini_final_decision / max_cosine_similarity / num_knn_matches to cite). Snapshot exported_at=2026-06-14T19:23:26Z.
