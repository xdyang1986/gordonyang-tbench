"""Grade the agent's distributed KeyValueDb implementation.

The agent implements the in-process replicated cluster (the `IReplicatedKvCluster`
interface + `DistributedKv.CreateCluster` factory) from scratch under
/app/src/KeyValueDb/. We grade it with a *fresh* .NET console program (under /tmp,
outside anything the agent could have modified) that project-references the agent's
library and runs the canonical scenario suite, printing one `SCENARIO <name>
PASS/FAIL` line per scenario.

The grader is a plain console app (SDK-only, no test-framework packages) so it needs
no NuGet restore at verification time and runs fully offline. Each `SCENARIO` line
surfaces as its own pytest case.
"""

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

AGENT_LIB_CSPROJ = "/app/src/KeyValueDb/KeyValueDb.csproj"
HIDDEN_GRADER = "/tests/grading/GradingProgram.cs"
GRADE_DIR = Path("/tmp/kvdb-grading")

# The canonical scenarios the agent's implementation must satisfy.
EXPECTED_TESTS = [
    "Replicate_and_converge",
    "Follower_write_is_forwarded_to_leader",
    "Quorum_commit_with_minority_partitioned",
    "Quorum_rejected_when_majority_unreachable",
    "Rejected_write_leaves_leader_state_unchanged",
    "Deleted_key_is_not_resurrected_after_sync",
    "Anti_entropy_catches_up_lagging_follower",
    "Failover_new_epoch_supersedes_stale_data",
    "Higher_epoch_beats_higher_seq",
    "Conflicting_partition_resolved_by_epoch",
    "Even_cluster_split_in_half_rejects_write",
    "Even_cluster_three_of_four_commits_and_laggard_catches_up",
    "Settle_does_not_cross_active_partition",
    "Higher_epoch_tombstone_beats_stale_live_value",
    "Multi_key_convergence_after_failover_partition_and_delete",
    "Sequential_failovers_discard_stale_minority_writes",
    "Higher_epoch_write_revives_key_over_older_tombstone",
    "Snapshot_restore_round_trips_values_and_count",
    "Restored_tombstone_and_version_survive_and_win_on_settle",
    "Restore_rejects_unregistered_type",
    "Snapshot_restore_round_trips_custom_registered_type",
    "Unregistered_value_type_is_rejected_registered_round_trips",
]

# Console grading project: SDK-only (Exe), references the agent's library, no
# external packages -> no NuGet needed, builds and runs offline.
GRADING_CSPROJ = """<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <OutputType>Exe</OutputType>
    <TargetFramework>net8.0</TargetFramework>
    <ImplicitUsings>enable</ImplicitUsings>
    <Nullable>enable</Nullable>
    <GenerateDocumentationFile>false</GenerateDocumentationFile>
  </PropertyGroup>
  <ItemGroup>
    <ProjectReference Include="{lib}" />
  </ItemGroup>
</Project>
""".format(
    lib=AGENT_LIB_CSPROJ
)

SCENARIO_RE = re.compile(r"^SCENARIO (\S+) (PASS|FAIL)(?::\s*(.*))?$")


def _run(cmd, cwd=None):
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=480)


@pytest.fixture(scope="session")
def grading_results():
    """Build & run the console grading harness against the agent's library once."""
    assert shutil.which("dotnet"), "dotnet SDK not found in the grading environment"
    assert os.path.exists(
        AGENT_LIB_CSPROJ
    ), f"Agent library project missing at {AGENT_LIB_CSPROJ}"
    assert os.path.exists(HIDDEN_GRADER), f"Hidden grader missing at {HIDDEN_GRADER}"

    if GRADE_DIR.exists():
        shutil.rmtree(GRADE_DIR)
    GRADE_DIR.mkdir(parents=True)

    (GRADE_DIR / "Grading.csproj").write_text(GRADING_CSPROJ)
    shutil.copy(HIDDEN_GRADER, GRADE_DIR / "GradingProgram.cs")

    proc = _run(
        ["dotnet", "run", "-c", "Release", "--project", "Grading.csproj"],
        cwd=str(GRADE_DIR),
    )

    diagnostics = (
        f"dotnet run exit={proc.returncode}\n"
        f"----- STDOUT -----\n{proc.stdout[-6000:]}\n"
        f"----- STDERR -----\n{proc.stderr[-3000:]}\n"
    )

    outcomes = {}
    for line in proc.stdout.splitlines():
        m = SCENARIO_RE.match(line.strip())
        if m:
            outcomes[m.group(1)] = "Passed" if m.group(2) == "PASS" else "Failed"

    return {
        "outcomes": outcomes,
        "diagnostics": diagnostics,
        "built": len(outcomes) > 0,
    }


def test_suite_built_and_ran(grading_results):
    """The agent's library must compile (interface + factory implemented) and run."""
    assert grading_results["built"], (
        "The grader produced no scenario results — the agent's implementation likely "
        "failed to compile (missing/mismatched IReplicatedKvCluster or "
        "DistributedKv.CreateCluster).\n\n" + grading_results["diagnostics"]
    )


@pytest.mark.parametrize("test_name", EXPECTED_TESTS)
def test_scenario_passes(grading_results, test_name):
    outcome = grading_results["outcomes"].get(test_name)
    assert outcome is not None, (
        f"Expected scenario '{test_name}' did not run.\n\n"
        + grading_results["diagnostics"]
    )
    assert outcome == "Passed", (
        f"'{test_name}' outcome was {outcome!r}, expected 'Passed'.\n\n"
        + grading_results["diagnostics"]
    )
