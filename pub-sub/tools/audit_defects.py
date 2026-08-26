#!/usr/bin/env python3
"""Pre-push audit for the pub-sub debug-in-place task.

Three rounds of 0/15 were caused by the same thing: a planted defect that no
visible example exposes, so no agent can possibly find it. This checks that
every defect is discoverable before the task is pushed.

For each single-line difference between the reference (extracted from
solution/solve.sh) and the shipped environment/broken/main.go, it builds a
binary carrying only that defect and runs every example parsed out of
instruction.md. A defect that produces identical output on all examples is
invisible and the audit fails.

Also verifies each example is genuinely a failing case (shipped output differs
from reference) and that the documented "Correct output" matches the reference.

    python3 tools/audit_defects.py        # from the task directory

Exits nonzero if anything fails. Requires the `go` toolchain; no network.
"""

import os
import re
import shutil
import subprocess
import sys
import tempfile

TASK = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOLVE = os.path.join(TASK, "solution", "solve.sh")
BROKEN = os.path.join(TASK, "environment", "broken", "main.go")
INSTRUCTION = os.path.join(TASK, "instruction.md")


def reference_source():
    """Pull the corrected main.go out of the solve.sh heredoc."""
    out, keep = [], False
    for line in open(SOLVE):
        if line.startswith("cat > main.go"):
            keep = True
            continue
        if keep and line.rstrip("\n") == "EOF":
            break
        if keep:
            out.append(line)
    if not out:
        sys.exit("could not extract reference main.go from solve.sh")
    return "".join(out)


def examples():
    """Parse (input, documented_output) pairs from instruction.md."""
    text = open(INSTRUCTION).read()
    body = text.split("## Failing cases", 1)[-1]
    fences = re.findall(r"```\n(.*?)```", body, re.S)
    # Blocks alternate input / correct output; an input block starts with an int.
    pairs, i = [], 0
    while i + 1 < len(fences):
        inp, out = fences[i], fences[i + 1]
        if inp.strip().split("\n")[0].strip().lstrip("-").isdigit():
            pairs.append((inp, out.strip()))
            i += 2
        else:
            i += 1
    if not pairs:
        sys.exit("no examples parsed from instruction.md")
    return pairs


def defects(ref, broken):
    """Single-line divergences, as (label, reference_line, broken_line)."""
    r, b = ref.split("\n"), broken.split("\n")
    if len(r) != len(b):
        print(f"note: line counts differ (ref {len(r)}, broken {len(b)}); "
              "comparing by index anyway")
    found = []
    for n, (lr, lb) in enumerate(zip(r, b), 1):
        if lr.strip() != lb.strip():
            found.append((f"L{n}:{lr.strip()[:44]}", lr, lb))
    return found


def build(workdir, source, name):
    d = os.path.join(workdir, name)
    os.makedirs(d, exist_ok=True)
    open(os.path.join(d, "main.go"), "w").write(source)
    open(os.path.join(d, "go.mod"), "w").write("module allocator\n\ngo 1.21\n")
    binpath = os.path.join(d, "allocator")
    r = subprocess.run(["go", "build", "-o", binpath, "."], cwd=d,
                       capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit(f"build failed for {name}:\n{r.stderr}")
    return binpath


def run(binpath, stdin):
    p = subprocess.run([binpath], input=stdin, capture_output=True,
                       text=True, timeout=30)
    return p.stdout.strip()


def main():
    ref_src = reference_source()
    broken_src = open(BROKEN).read()
    ex = examples()
    dfs = defects(ref_src, broken_src)
    if not dfs:
        sys.exit("no defects found — shipped source matches the reference")

    work = tempfile.mkdtemp(prefix="pubsub-audit-")
    ok = True
    try:
        ref_bin = build(work, ref_src, "_ref")
        ship_bin = build(work, broken_src, "_shipped")

        print(f"{len(dfs)} defect(s), {len(ex)} example(s)\n")

        # 1. Every example must actually fail, and its documented output must be right.
        for i, (inp, documented) in enumerate(ex, 1):
            expected = run(ref_bin, inp)
            got = run(ship_bin, inp)
            if expected != documented.strip():
                print(f"  E{i}: documented 'Correct output' does not match the reference")
                print(f"       documented {documented.strip()!r}")
                print(f"       reference  {expected!r}")
                ok = False
            if expected == got:
                print(f"  E{i}: shipped output matches the reference — not a failing case")
                ok = False

        # 2. Every defect must be exposed by at least one example.
        width = max(len(d[0]) for d in dfs)
        print(f"\n{'defect':{width}}" + "".join(f"{'E%d' % i:>6}" for i in range(1, len(ex) + 1)))
        for label, ref_line, bad_line in dfs:
            src = ref_src.replace(ref_line, bad_line, 1)
            if src == ref_src:
                print(f"{label:{width}}  could not isolate — skipped")
                ok = False
                continue
            b = build(work, src, "d" + re.sub(r"\W", "", label)[:24])
            vis = [run(ref_bin, inp) != run(b, inp) for inp, _ in ex]
            print(f"{label:{width}}" + "".join(f"{'DIFF' if v else '.':>6}" +
                  ("" if v else "") for v in vis) +
                  ("" if any(vis) else "   <-- INVISIBLE"))
            if not any(vis):
                ok = False
    finally:
        shutil.rmtree(work, ignore_errors=True)

    print("\nPASS: every defect is exposed and every example is a real failing case"
          if ok else
          "\nFAIL: see above — do not push")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
