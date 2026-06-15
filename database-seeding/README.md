# codimango/database-seeding

## Description

The agent must implement a **streaming database-copy CLI** that copies a file
locally and over the network. The tool is graded entirely through its
command-line interface:

```
python copy_data.py local SRC DST [--chunk-size N]
python copy_data.py send HOST PORT SRC [--codec NAME] [--chunk-size N]
python copy_data.py recv HOST PORT DST [--chunk-size N]
```

Required behavior:

- **Local copy** — byte-exact, chunked (handles files larger than memory),
  creates the destination directory on demand, overwrites an existing
  destination, and exits non-zero with a stderr message (leaving no destination
  file) when the source is missing.
- **Network transfer** — a `recv` process binds `HOST:PORT` and a `send` process
  connects, with the payload **compressed in transit** and reconstructed
  byte-for-byte on the receiver. Works on loopback. Errors exit non-zero with a
  stderr message.

**Why a naive approach won't work.** A `shutil.copyfile` one-liner doesn't cover
the chunked/bounded-memory requirement, and it does nothing for the network
half — which is the real filter. The sender must compress a stream and the
receiver must decompress it across arbitrary `recv()` boundaries (a received
chunk is **not** a compression message boundary), the end-of-stream has to be
signaled so the receiver's read loop terminates, and the receiver must
reconstruct the exact original bytes. Each is a place a plausible implementation
silently corrupts data or hangs.

## Completion Rates

> Calibration target: Opus or Avocado must pass **at least once and fail at
> least once** out of 5. Sonnet is informational only.

_Measured on the final 15-test suite (k=5)._

| Model | Pass rate (k=5) |
|-------|-----------------|
| Oracle | 3/3 (mean 1.000, Docker) |
| Sonnet 4.6 | 2/5 passed (mean 0.400, informational) |
| Opus 4.6 | 1/5 passed (mean 0.200) — **calibration target met** |
| Avocado | 5/5 passed (mean 1.000) |

## Model Analysis

Per-model results over k=5 (15 tests per trial):

- **Sonnet 4.6 — 2/5 passed, 3/5 failed** (informational). All 3 failing trials
  failed on the same `test_transfer_interrupted_midstream_leaves_no_file`
  (14/15 each).
- **Opus 4.6 — 1/5 passed, 4/5 failed.** All 4 failing trials failed on the
  *same single test*, `test_transfer_interrupted_midstream_leaves_no_file`
  (14/15 each; all other tests green). The one passing trial was 15/15. In the
  failing trials the receiver wrote decompressed bytes straight to the
  destination and did not remove it when the connection was severed mid-stream —
  leaving a partial, corrupt file at DST.
- **Avocado — 5/5 passed** (15/15 each). Avocado implemented clean-on-failure
  delivery, compression, and the disk pre-check in every trial.

**Dominant failure mode (all models): atomic / clean-on-failure delivery.**
7 of 7 total model failures across all three models (100%) are the receiver
leaving a partial file at the destination after an interrupted transfer, because
it streams directly to the final path instead of writing to a temp file and
renaming on success (or deleting on error). No other failure mode appeared in
any trial. The disk-space pre-check and the compression-in-transit test each
passed in all 15 model trials (Sonnet + Opus + Avocado) — models implement those
required behaviors reliably, so those tests harden spec⇄test coverage without
adding to the difficulty signal, which isolates entirely on the clean-on-failure
decision. Run-to-run pass counts vary around the boundary (e.g. Opus 1–3 / 5
across runs), the expected signature of a well-calibrated task.

**Why this is a reasoning gap, not a task-setup issue.** The other 12 tests
passed in 100% of trials across both models, the oracle is a deterministic 3/3,
and the required behavior is solvable in-environment — two Opus trials and all
five Avocado trials do it correctly. The instruction explicitly asks for "no
corrupt file on failure"; the failing trials simply did not reason about the
partial-failure path (commit-on-success / cleanup-on-error). The difficulty
isolates on that single design decision rather than on any environmental
flakiness or under-specification.

## Anti-Cheating Analysis

- **Hardcoded outputs** — tests use randomized payloads (`os.urandom`) and
  multi-megabyte inputs verified byte-for-byte; there is no fixed expected blob
  to memorize.
- **Overfitting to visible tests** — in production the `tests/` directory is
  hidden from the agent during the trajectory; the CLI contract in the
  instruction is the only thing to build against. (Run local model checks from
  inside the task subfolder, never the repo root.)
- **Modifying test files** — the reward is computed from a fresh
  `tests/test_outputs.py` copied in by the verifier at grade time; any agent
  edits under `/app` are ignored.
- **Bypassing the intended solution path** — grading drives the real CLI via
  subprocess: it runs an actual `send`↔`recv` transfer over loopback as two
  processes and diffs the received file against the source, and pushes 4–5 MiB
  random and highly-compressible payloads through a small `--chunk-size` to force
  genuine multi-chunk streaming. A copy that buffers everything, skips
  compression, or mishandles stream boundaries fails the byte-for-byte checks;
  there is no shortcut that produces correct received bytes without implementing
  the streaming transfer.

## Validation

```bash
# from the repo root (gordonyang-tbench/)
codimango bench run -p database-seeding -a oracle            # verified: 1.0
codimango bench run -p database-seeding -a oracle -k 3       # expect 3/3
codimango bench run -p database-seeding -a claude-code -m claude-sonnet-4-6 -k 5
codimango bench run -p database-seeding -a claude-code -m claude-opus-4-6 -k 5
codimango bench run -p database-seeding -a metacode -m meta/avocado_dvsc_tester -k 5
```
