"""Grade the agent's KeyValueStore implementation.

The agent implements /app/src/KeyValueDb/KeyValueStore.cs. We grade it by building
a *fresh* xUnit grading project (under /tmp, outside anything the agent could have
modified) that project-references the agent's library and runs the canonical test
suite. Parsing the .trx report lets each xUnit fact surface as its own pytest case.
"""

import os
import shutil
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

AGENT_LIB_CSPROJ = "/app/src/KeyValueDb/KeyValueDb.csproj"
HIDDEN_TESTS = "/tests/grading/KeyValueStoreTests.cs"
GRADE_DIR = Path("/tmp/kvdb-grading")

TRX_NS = {"t": "http://microsoft.com/schemas/VisualStudio/TeamTest/2010"}

# The canonical xUnit facts the agent's implementation must satisfy.
EXPECTED_TESTS = [
    "Set_and_Get_round_trips_a_value",
    "Get_missing_key_throws",
    "Store_holds_mixed_key_types_simultaneously",
    "TryGet_generic_returns_typed_value",
    "Remove_and_ContainsKey_and_Clear_behave",
    "Indexer_gets_and_sets",
    "Snapshot_then_Load_reproduces_primitive_data",
    "Snapshot_then_Load_round_trips_custom_key_and_value_types",
    "Null_values_round_trip",
    "Snapshot_throws_for_unregistered_value_type",
    "Load_throws_for_unregistered_type",
    "Snapshot_overwrites_previous_snapshot",
    "Load_replaces_existing_contents",
    "Concurrent_writes_are_thread_safe",
    "Large_snapshot_round_trips_many_entries",
    "Log_replays_on_open",
    "Log_appends_survive_across_reopen_and_continue",
    "Log_recovers_from_truncated_tail",
    "Compact_rewrites_log_to_live_state",
    "Compact_keeps_log_open_for_further_appends",
    "Ttl_entry_expires_after_clock_advances",
    "Set_without_ttl_never_expires",
    "Ttl_survives_snapshot_and_load",
    "Ttl_expired_entry_dropped_on_log_replay",
]

GRADING_CSPROJ = """<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <TargetFramework>net8.0</TargetFramework>
    <ImplicitUsings>enable</ImplicitUsings>
    <Nullable>enable</Nullable>
    <IsPackable>false</IsPackable>
    <IsTestProject>true</IsTestProject>
  </PropertyGroup>
  <ItemGroup>
    <PackageReference Include="Microsoft.NET.Test.Sdk" Version="17.11.1" />
    <PackageReference Include="xunit" Version="2.9.2" />
    <PackageReference Include="xunit.runner.visualstudio" Version="2.8.2" />
  </ItemGroup>
  <ItemGroup>
    <ProjectReference Include="{lib}" />
  </ItemGroup>
</Project>
""".format(
    lib=AGENT_LIB_CSPROJ
)


def _run(cmd, cwd=None):
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=480)


@pytest.fixture(scope="session")
def grading_results():
    """Build & run the canonical suite against the agent's library once."""
    assert shutil.which("dotnet"), "dotnet SDK not found in the grading environment"
    assert os.path.exists(
        AGENT_LIB_CSPROJ
    ), f"Agent library project missing at {AGENT_LIB_CSPROJ}"
    assert os.path.exists(
        "/app/src/KeyValueDb/KeyValueStore.cs"
    ), "KeyValueStore.cs was not implemented by the agent"
    assert os.path.exists(HIDDEN_TESTS), f"Hidden test file missing at {HIDDEN_TESTS}"

    if GRADE_DIR.exists():
        shutil.rmtree(GRADE_DIR)
    GRADE_DIR.mkdir(parents=True)

    (GRADE_DIR / "Grading.csproj").write_text(GRADING_CSPROJ)
    shutil.copy(HIDDEN_TESTS, GRADE_DIR / "KeyValueStoreTests.cs")

    trx = GRADE_DIR / "results.trx"
    proc = _run(
        [
            "dotnet",
            "test",
            "Grading.csproj",
            "-c",
            "Release",
            "--logger",
            f"trx;LogFileName={trx}",
            "--results-directory",
            str(GRADE_DIR),
        ],
        cwd=str(GRADE_DIR),
    )

    diagnostics = (
        f"dotnet test exit={proc.returncode}\n"
        f"----- STDOUT -----\n{proc.stdout[-6000:]}\n"
        f"----- STDERR -----\n{proc.stderr[-3000:]}\n"
    )

    outcomes = {}
    if trx.exists():
        root = ET.parse(trx).getroot()
        for r in root.findall(".//t:UnitTestResult", TRX_NS):
            name = r.get("testName", "")
            short = name.split(".")[-1]
            outcomes[short] = r.get("outcome", "Unknown")

    return {"outcomes": outcomes, "diagnostics": diagnostics, "built": trx.exists()}


def test_suite_built_and_ran(grading_results):
    """The agent's library must compile and the test suite must execute."""
    assert grading_results["built"], (
        "The grading suite did not produce a result file — the agent's "
        "implementation likely failed to compile.\n\n" + grading_results["diagnostics"]
    )


@pytest.mark.parametrize("test_name", EXPECTED_TESTS)
def test_xunit_fact_passes(grading_results, test_name):
    outcome = grading_results["outcomes"].get(test_name)
    assert outcome is not None, (
        f"Expected test '{test_name}' did not run.\n\n" + grading_results["diagnostics"]
    )
    assert outcome == "Passed", (
        f"'{test_name}' outcome was {outcome!r}, expected 'Passed'.\n\n"
        + grading_results["diagnostics"]
    )
