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
        // 15-scenario behavioral suite (11 core consensus + 4 differentiator scenarios).
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
