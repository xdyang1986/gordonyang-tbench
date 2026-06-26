using KeyValueDb;

namespace KeyValueDb.Grading;

// A custom value type used by the allow-list scenario.
public sealed record Note(string Text, int N);

// A plain .NET console grading harness (no test framework needed, so the grader
// requires no NuGet packages and runs fully offline). Each scenario runs in its own
// try/catch and prints "SCENARIO <name> PASS" or "SCENARIO <name> FAIL: <reason>".
// Exit code is 0 iff every scenario passed.
public static class GradingProgram
{
    private static IReplicatedKvCluster New(int nodes = 3, int leader = 0, TypeRegistry? registry = null)
        => DistributedKv.CreateCluster(nodes, leader, registry);

    public static int Main()
    {
        // 22-scenario behavioral suite (11 core consensus + 7 differentiator/stress
        // scenarios + 4 snapshot/restore scenarios).
        var scenarios = new (string Name, Action Body)[]
        {
            ("Replicate_and_converge", Replicate_and_converge),
            ("Follower_write_is_forwarded_to_leader", Follower_write_is_forwarded_to_leader),
            ("Quorum_commit_with_minority_partitioned", Quorum_commit_with_minority_partitioned),
            ("Quorum_rejected_when_majority_unreachable", Quorum_rejected_when_majority_unreachable),
            ("Rejected_write_leaves_leader_state_unchanged", Rejected_write_leaves_leader_state_unchanged),
            ("Deleted_key_is_not_resurrected_after_sync", Deleted_key_is_not_resurrected_after_sync),
            ("Anti_entropy_catches_up_lagging_follower", Anti_entropy_catches_up_lagging_follower),
            ("Failover_new_epoch_supersedes_stale_data", Failover_new_epoch_supersedes_stale_data),
            ("Higher_epoch_beats_higher_seq", Higher_epoch_beats_higher_seq),
            ("Conflicting_partition_resolved_by_epoch", Conflicting_partition_resolved_by_epoch),
            ("Even_cluster_split_in_half_rejects_write", Even_cluster_split_in_half_rejects_write),
            ("Even_cluster_three_of_four_commits_and_laggard_catches_up", Even_cluster_three_of_four_commits_and_laggard_catches_up),
            ("Settle_does_not_cross_active_partition", Settle_does_not_cross_active_partition),
            ("Higher_epoch_tombstone_beats_stale_live_value", Higher_epoch_tombstone_beats_stale_live_value),
            ("Multi_key_convergence_after_failover_partition_and_delete", Multi_key_convergence_after_failover_partition_and_delete),
            ("Sequential_failovers_discard_stale_minority_writes", Sequential_failovers_discard_stale_minority_writes),
            ("Higher_epoch_write_revives_key_over_older_tombstone", Higher_epoch_write_revives_key_over_older_tombstone),
            ("Snapshot_restore_round_trips_values_and_count", Snapshot_restore_round_trips_values_and_count),
            ("Restored_tombstone_and_version_survive_and_win_on_settle", Restored_tombstone_and_version_survive_and_win_on_settle),
            ("Restore_rejects_unregistered_type", Restore_rejects_unregistered_type),
            ("Writes_after_restore_supersede_restored_entries", Writes_after_restore_supersede_restored_entries),
            ("Unregistered_value_type_is_rejected_registered_round_trips", Unregistered_value_type_is_rejected_registered_round_trips),
        };

        var allPassed = true;
        foreach (var (name, body) in scenarios)
        {
            try
            {
                body();
                Console.WriteLine($"SCENARIO {name} PASS");
            }
            catch (Exception e)
            {
                allPassed = false;
                var msg = (e.Message ?? string.Empty).Replace("\n", " ").Replace("\r", " ");
                Console.WriteLine($"SCENARIO {name} FAIL: {e.GetType().Name}: {msg}");
            }
        }

        return allPassed ? 0 : 1;
    }

    // ---- assertion helpers (throw on failure) ----
    private static void Check(bool cond, string msg)
    {
        if (!cond) throw new Exception("assertion failed: " + msg);
    }

    private static void CheckEqual(object? expected, object? actual, string msg)
    {
        if (!Equals(expected, actual))
            throw new Exception($"expected [{expected}] but got [{actual}]: {msg}");
    }

    private static void CheckThrows(Action action, string msg)
    {
        try { action(); }
        catch { return; }
        throw new Exception("expected an exception but none was thrown: " + msg);
    }

    // ---- scenarios ----
    private static void Replicate_and_converge()
    {
        var c = New();
        Check(c.Set(0, "k", "v"), "Set should commit");
        c.Settle();
        CheckEqual("v", c.Get(0, "k"), "node 0");
        CheckEqual("v", c.Get(1, "k"), "node 1");
        CheckEqual("v", c.Get(2, "k"), "node 2");
    }

    private static void Follower_write_is_forwarded_to_leader()
    {
        var c = New();                              // 3 nodes, leader 0
        Check(c.Set(1, "k", "v"), "follower write should forward and commit");
        c.Settle();
        CheckEqual("v", c.Get(0, "k"), "leader");
        CheckEqual("v", c.Get(2, "k"), "other follower");
        Check(c.Remove(2, "k"), "follower remove should forward and commit");
        c.Settle();
        Check(!c.ContainsKey(0, "k"), "key should be removed everywhere");
    }

    private static void Quorum_commit_with_minority_partitioned()
    {
        var c = New();                              // majority = 2
        c.Partition(2);                             // {2} | {0,1}
        Check(c.Set(0, "k", "v"), "2 of 3 reachable should commit");
        Check(c.ContainsKey(0, "k"), "leader has it");
        Check(!c.ContainsKey(2, "k"), "minority node lags");
        c.Heal();
        c.Settle();
        CheckEqual("v", c.Get(2, "k"), "minority catches up");
    }

    private static void Quorum_rejected_when_majority_unreachable()
    {
        var c = New();
        c.Partition(0);                             // leader {0} alone | {1,2}
        Check(!c.Set(0, "k", "v"), "no majority -> reject");
        Check(!c.ContainsKey(1, "k"), "node 1 unaffected");
        Check(!c.ContainsKey(2, "k"), "node 2 unaffected");
    }

    private static void Rejected_write_leaves_leader_state_unchanged()  // no-local-apply
    {
        var c = New();
        c.Partition(0);
        Check(!c.Set(0, "k", "v"), "rejected");
        CheckThrows(() => c.Get(0, "k"), "leader did not apply locally");
    }

    private static void Deleted_key_is_not_resurrected_after_sync()
    {
        var c = New();
        Check(c.Set(0, "k", "v"), "set");
        c.Settle();
        c.Partition(2);                             // node 2 misses the delete
        Check(c.Remove(0, "k"), "remove commits on {0,1}");
        c.Heal();
        c.Settle();
        Check(!c.ContainsKey(2, "k"), "tombstone wins, no resurrection");
        CheckThrows(() => c.Get(2, "k"), "deleted key throws");
    }

    private static void Anti_entropy_catches_up_lagging_follower()
    {
        var c = New();
        c.Partition(2);
        for (var i = 0; i < 5; i++) Check(c.Set(0, $"k{i}", i), $"set k{i}");
        CheckEqual(0, c.Count(2), "node 2 missed everything");
        c.Heal();
        c.Settle();
        CheckEqual(5, c.Count(2), "node 2 caught up");
        CheckEqual(3, c.Get(2, "k3"), "value present");
    }

    private static void Failover_new_epoch_supersedes_stale_data()
    {
        var c = New();
        Check(c.Set(0, "k", "old"), "initial set");
        c.Settle();
        c.PromoteLeader(1);
        Check(c.Set(1, "k", "new"), "new-leader set");
        c.Settle();
        CheckEqual("new", c.Get(0, "k"), "node 0");
        CheckEqual("new", c.Get(2, "k"), "node 2");
    }

    private static void Higher_epoch_beats_higher_seq()
    {
        var c = New();
        for (var i = 0; i < 5; i++) Check(c.Set(0, "k", $"v{i}"), $"set v{i}"); // (epoch 1, seq up to 5)
        c.Partition(2);                             // node 2 keeps the high-seq value
        c.PromoteLeader(1);                         // epoch 2, seq restarts
        Check(c.Set(1, "k", "win"), "epoch-2 set commits on {0,1}");
        c.Heal();
        c.Settle();                                 // merge: epoch 2 beats higher seq of epoch 1
        CheckEqual("win", c.Get(0, "k"), "node 0");
        CheckEqual("win", c.Get(1, "k"), "node 1");
        CheckEqual("win", c.Get(2, "k"), "node 2");
    }

    private static void Conflicting_partition_resolved_by_epoch()
    {
        var c = New();
        Check(c.Set(0, "k", "v0"), "initial committed everywhere");
        c.Settle();
        c.Partition(0);                             // old leader {0} isolated | {1,2}
        Check(!c.Set(0, "k", "stale"), "old leader cannot commit (minority)");
        c.PromoteLeader(1);                         // promote on majority side (epoch 2)
        Check(c.Set(1, "k", "vNew"), "commits on {1,2}");
        c.Heal();
        c.Settle();
        CheckEqual("vNew", c.Get(0, "k"), "node 0");
        CheckEqual("vNew", c.Get(2, "k"), "node 2");
    }

    private static void Even_cluster_split_in_half_rejects_write()
    {
        var c = New(nodes: 4, leader: 0);           // N=4, strict majority = 3
        c.Partition(0, 1);                          // {0,1} | {2,3} -> leader side has only 2 of 4
        Check(!c.Set(0, "k", "v"), "2 of 4 is not a strict majority -> reject");
        Check(!c.ContainsKey(0, "k"), "leader did not apply locally");
        Check(!c.ContainsKey(1, "k"), "node 1 unaffected");
        Check(!c.ContainsKey(2, "k"), "node 2 unaffected");
        Check(!c.ContainsKey(3, "k"), "node 3 unaffected");
    }

    private static void Even_cluster_three_of_four_commits_and_laggard_catches_up()
    {
        var c = New(nodes: 4, leader: 0);           // N=4, strict majority = 3
        c.Partition(3);                             // {0,1,2} | {3} -> leader side has 3 of 4
        Check(c.Set(0, "k", "v"), "3 of 4 is a strict majority -> commit");
        Check(c.ContainsKey(0, "k"), "leader has it");
        Check(c.ContainsKey(1, "k"), "node 1 has it");
        Check(c.ContainsKey(2, "k"), "node 2 has it");
        Check(!c.ContainsKey(3, "k"), "isolated node lags");
        c.Heal();
        c.Settle();
        CheckEqual("v", c.Get(3, "k"), "isolated node catches up");
    }

    private static void Settle_does_not_cross_active_partition()
    {
        var c = New();                              // 3 nodes, leader 0
        c.Partition(2);                             // {0,1} | {2}
        Check(c.Set(0, "k", "v"), "commits on majority {0,1}");
        c.Settle();                                 // still partitioned: must NOT reach node 2
        Check(c.ContainsKey(0, "k"), "node 0 has it");
        Check(c.ContainsKey(1, "k"), "node 1 has it");
        Check(!c.ContainsKey(2, "k"), "Settle converges only connected nodes, not across the partition");
        c.Heal();
        c.Settle();                                 // now connected: node 2 catches up
        CheckEqual("v", c.Get(2, "k"), "node 2 catches up after heal");
    }

    private static void Higher_epoch_tombstone_beats_stale_live_value()
    {
        var c = New();                              // 3 nodes, leader 0
        for (var i = 0; i < 5; i++) Check(c.Set(0, "k", $"v{i}"), $"set v{i}"); // epoch 1, high seq
        c.Settle();
        c.Partition(2);                             // node 2 keeps the live, high-seq epoch-1 value
        c.PromoteLeader(1);                         // epoch 2, seq restarts
        Check(c.Remove(1, "k"), "epoch-2 delete commits on {0,1}");           // tombstone (epoch 2)
        c.Heal();
        c.Settle();                                 // higher epoch wins, even though it is a tombstone
        Check(!c.ContainsKey(2, "k"), "stale live value does not resurrect over a higher-epoch delete");
        CheckThrows(() => c.Get(2, "k"), "deleted key throws on node 2");
        Check(!c.ContainsKey(0, "k"), "node 0 deleted");
        Check(!c.ContainsKey(1, "k"), "node 1 deleted");
    }

    // ---- composed stress scenarios: many rules must all hold for the final converged
    // state to be correct; one slip (lost tombstone, regressed version, dropped key,
    // wrong epoch winner) produces a wrong answer somewhere. ----

    private static void Multi_key_convergence_after_failover_partition_and_delete()
    {
        var c = New();                              // 3 nodes, leader 0, epoch 1
        Check(c.Set(0, "a", 1), "a=1");             // seq1, all nodes
        Check(c.Set(0, "b", 2), "b=2");             // seq2
        Check(c.Set(0, "c", 3), "c=3");             // seq3
        c.Settle();
        c.Partition(2);                             // {0,1} | {2}; node 2 freezes at a=1,b=2,c=3
        Check(c.Set(0, "a", 10), "a=10 on {0,1}");  // seq4
        Check(c.Remove(0, "b"), "delete b on {0,1}"); // seq5 tombstone
        Check(c.Set(0, "d", 4), "d=4 on {0,1}");    // seq6
        c.PromoteLeader(1);                         // epoch 2, seq restarts; leader 1
        Check(c.Set(1, "c", 30), "c=30 epoch 2 on {0,1}"); // (epoch2, seq1)
        c.Heal();
        c.Settle();                                 // converge all three nodes by (epoch,seq)
        foreach (var node in new[] { 0, 1, 2 })
        {
            CheckEqual(10, c.Get(node, "a"), $"a on node {node}");          // higher seq wins
            Check(!c.ContainsKey(node, "b"), $"b deleted on node {node}");  // tombstone, no resurrection
            CheckEqual(30, c.Get(node, "c"), $"c on node {node}");          // higher epoch beats stale c=3
            CheckEqual(4, c.Get(node, "d"), $"d on node {node}");           // late write propagates
            CheckEqual(3, c.Count(node), $"count excludes tombstone on node {node}");
        }
    }

    private static void Sequential_failovers_discard_stale_minority_writes()
    {
        var c = New();                              // 3 nodes, leader 0, epoch 1
        Check(c.Set(0, "k", "v1"), "v1");           // (epoch1, seq1) everywhere
        c.Settle();
        c.Partition(0);                             // old leader {0} isolated | {1,2}
        Check(!c.Set(0, "k", "stale"), "isolated old leader cannot commit (minority)");
        c.PromoteLeader(1);                         // epoch 2, leader 1 (on majority side)
        Check(c.Set(1, "k", "v2"), "epoch-2 commit on {1,2}");
        c.PromoteLeader(2);                         // epoch 3, leader 2 (still on {1,2})
        Check(c.Set(2, "k", "v3"), "epoch-3 commit on {1,2}");
        Check(!c.ContainsKey(0, "k") || Equals("v1", c.Get(0, "k")), "node 0 still holds only stale v1");
        c.Heal();
        c.Settle();                                 // highest epoch wins
        CheckEqual("v3", c.Get(0, "k"), "node 0");
        CheckEqual("v3", c.Get(1, "k"), "node 1");
        CheckEqual("v3", c.Get(2, "k"), "node 2");
    }

    private static void Higher_epoch_write_revives_key_over_older_tombstone()
    {
        var c = New();                              // 3 nodes, leader 0, epoch 1
        Check(c.Set(0, "k", "v"), "set");           // (epoch1, seq1)
        c.Settle();
        Check(c.Remove(0, "k"), "delete");          // (epoch1, seq2) tombstone everywhere
        c.Settle();
        c.Partition(2);                             // node 2 keeps the epoch-1 tombstone
        c.PromoteLeader(1);                         // epoch 2, leader 1
        Check(c.Set(1, "k", "reborn"), "epoch-2 re-create on {0,1}"); // (epoch2, seq1) live
        c.Heal();
        c.Settle();                                 // higher epoch live write beats older tombstone
        CheckEqual("reborn", c.Get(0, "k"), "node 0");
        CheckEqual("reborn", c.Get(1, "k"), "node 1");
        CheckEqual("reborn", c.Get(2, "k"), "node 2 (tombstone does not stick across a newer epoch)");
    }

    // ---- snapshot / restore: serialize a node's committed state and rebuild it
    // elsewhere. The snapshot must preserve values, tombstones, and (epoch,seq) versions,
    // encode types by registry name (allow-list enforced on Restore), and advance the
    // write counter so post-Restore writes supersede restored entries. ----

    private static void Snapshot_restore_round_trips_values_and_count()
    {
        var c = New();
        Check(c.Set(0, "a", 1), "a=1");
        Check(c.Set(0, "b", 2), "b=2");
        Check(c.Set(0, "c", 3), "c=3");
        c.Settle();
        var snap = c.Snapshot(0);

        var c2 = New();                              // fresh, empty cluster
        c2.Restore(0, snap);
        CheckEqual(1, c2.Get(0, "a"), "a round-trips");
        CheckEqual(2, c2.Get(0, "b"), "b round-trips");
        CheckEqual(3, c2.Get(0, "c"), "c round-trips");
        CheckEqual(3, c2.Count(0), "count round-trips");
    }

    private static void Restored_tombstone_and_version_survive_and_win_on_settle()
    {
        var c = New();
        Check(c.Set(0, "k", "v"), "set");            // (epoch1, seq1)
        c.Settle();
        Check(c.Remove(0, "k"), "delete");           // (epoch1, seq2) tombstone
        c.Settle();
        var snap = c.Snapshot(0);                     // snapshot carries the tombstone at seq2

        var c2 = New();
        Check(c2.Set(0, "k", "old"), "older live value");  // (epoch1, seq1) on all nodes
        c2.Settle();
        c2.Restore(0, snap);                          // node 0 now holds the tombstone (seq2)
        c2.Settle();                                  // tombstone (seq2) must beat the live value (seq1)
        Check(!c2.ContainsKey(0, "k"), "node 0 deleted");
        Check(!c2.ContainsKey(1, "k"), "node 1 deleted — restored tombstone + version survived and won");
        Check(!c2.ContainsKey(2, "k"), "node 2 deleted");
    }

    private static void Restore_rejects_unregistered_type()
    {
        var reg = TypeRegistry.CreateDefault();
        reg.Register<Note>();
        var c = New(registry: reg);
        Check(c.Set(0, "k", new Note("hi", 1)), "registered type commits");
        var snap = c.Snapshot(0);

        var c2 = New();                              // default registry: no Note
        CheckThrows(() => c2.Restore(0, snap), "restoring a type not on the allow-list must be rejected");
    }

    private static void Writes_after_restore_supersede_restored_entries()
    {
        var c = New();
        for (var i = 0; i < 5; i++) Check(c.Set(0, "k", $"v{i}"), $"set v{i}"); // (epoch1, seq up to 5)
        c.Settle();
        var snap = c.Snapshot(0);                     // k = "v4" at (epoch1, seq5)

        var c2 = New();
        c2.Partition(2);                             // {0,1} | {2}
        c2.Restore(2, snap);                         // isolated node 2 holds "v4" at seq5; counters must advance
        Check(c2.Set(0, "k", "newer"), "post-restore write commits on {0,1}");
        c2.Heal();
        c2.Settle();                                 // "newer" must outrank the restored seq-5 entry
        CheckEqual("newer", c2.Get(0, "k"), "node 0");
        CheckEqual("newer", c2.Get(1, "k"), "node 1");
        CheckEqual("newer", c2.Get(2, "k"), "node 2 — restored high-seq value did not win");
    }

    private static void Unregistered_value_type_is_rejected_registered_round_trips()
    {
        var c1 = New();                                         // default registry: no Note
        Check(!c1.Set(0, "k", new Note("hi", 1)), "unregistered type rejected");
        Check(!c1.ContainsKey(0, "k"), "nothing committed");

        var reg = TypeRegistry.CreateDefault();
        reg.Register<Note>();
        var c2 = New(registry: reg);
        Check(c2.Set(0, "k", new Note("hi", 1)), "registered type commits");
        c2.Settle();
        CheckEqual(new Note("hi", 1), c2.Get(2, "k"), "round-trips");
    }
}
