using KeyValueDb;
using Xunit;

namespace KeyValueDb.Tests;

// A custom value type used by the allow-list scenario.
public sealed record Note(string Text, int N);

public class DistributedKvTests
{
    private static IReplicatedKvCluster New(int nodes = 3, int leader = 0, TypeRegistry? registry = null)
        => DistributedKv.CreateCluster(nodes, leader, registry);

    [Fact]
    public void Replicate_and_converge()
    {
        var c = New();
        Assert.True(c.Set(0, "k", "v"));
        c.Settle();
        Assert.Equal("v", c.Get(0, "k"));
        Assert.Equal("v", c.Get(1, "k"));
        Assert.Equal("v", c.Get(2, "k"));
    }

    [Fact]
    public void Quorum_commit_with_minority_partitioned()
    {
        var c = New();                          // 3 nodes, majority = 2
        c.Partition(2);                         // {2} | {0,1}
        Assert.True(c.Set(0, "k", "v"));        // 2 of 3 reachable -> commits
        Assert.True(c.ContainsKey(0, "k"));
        Assert.False(c.ContainsKey(2, "k"));    // the minority node lags
        c.Heal();
        c.Settle();
        Assert.Equal("v", c.Get(2, "k"));       // and then catches up
    }

    [Fact]
    public void Quorum_rejected_when_majority_unreachable()
    {
        var c = New();
        c.Partition(0);                         // leader {0} alone | {1,2}
        Assert.False(c.Set(0, "k", "v"));       // no majority -> rejected
        Assert.False(c.ContainsKey(1, "k"));
        Assert.False(c.ContainsKey(2, "k"));
    }

    [Fact]
    public void Rejected_write_leaves_leader_state_unchanged()  // no-local-apply
    {
        var c = New();
        c.Partition(0);
        Assert.False(c.Set(0, "k", "v"));
        Assert.ThrowsAny<System.Exception>(() => c.Get(0, "k"));  // leader did not apply locally
    }

    [Fact]
    public void Deleted_key_is_not_resurrected_after_sync()
    {
        var c = New();
        Assert.True(c.Set(0, "k", "v"));
        c.Settle();
        c.Partition(2);                         // node 2 will miss the delete
        Assert.True(c.Remove(0, "k"));          // commits on {0,1}
        c.Heal();
        c.Settle();
        Assert.False(c.ContainsKey(2, "k"));    // tombstone wins; no resurrection
        Assert.ThrowsAny<System.Exception>(() => c.Get(2, "k"));
    }

    [Fact]
    public void Anti_entropy_catches_up_lagging_follower()
    {
        var c = New();
        c.Partition(2);
        for (var i = 0; i < 5; i++) Assert.True(c.Set(0, $"k{i}", i));
        Assert.Equal(0, c.Count(2));            // node 2 missed everything
        c.Heal();
        c.Settle();
        Assert.Equal(5, c.Count(2));
        Assert.Equal(3, c.Get(2, "k3"));
    }

    [Fact]
    public void Failover_new_epoch_supersedes_stale_data()
    {
        var c = New();
        Assert.True(c.Set(0, "k", "old"));
        c.Settle();
        c.PromoteLeader(1);
        Assert.True(c.Set(1, "k", "new"));
        c.Settle();
        Assert.Equal("new", c.Get(0, "k"));
        Assert.Equal("new", c.Get(2, "k"));
    }

    [Fact]
    public void Higher_epoch_beats_higher_seq()
    {
        var c = New();
        for (var i = 0; i < 5; i++) Assert.True(c.Set(0, "k", $"v{i}")); // (epoch 1, seq up to 5)
        c.Partition(2);                         // node 2 keeps the high-seq value
        c.PromoteLeader(1);                     // epoch 2, seq restarts
        Assert.True(c.Set(1, "k", "win"));      // (epoch 2, seq 1) commits on {0,1}
        c.Heal();
        c.Settle();                             // merge: epoch 2 beats higher seq of epoch 1
        Assert.Equal("win", c.Get(0, "k"));
        Assert.Equal("win", c.Get(1, "k"));
        Assert.Equal("win", c.Get(2, "k"));
    }

    [Fact]
    public void Conflicting_partition_resolved_by_epoch()
    {
        var c = New();
        Assert.True(c.Set(0, "k", "v0"));       // committed everywhere (epoch 1)
        c.Settle();
        c.Partition(0);                         // old leader {0} isolated | {1,2}
        Assert.False(c.Set(0, "k", "stale"));   // old leader cannot commit (minority)
        c.PromoteLeader(1);                     // promote on the majority side (epoch 2)
        Assert.True(c.Set(1, "k", "vNew"));     // commits on {1,2}
        c.Heal();
        c.Settle();
        Assert.Equal("vNew", c.Get(0, "k"));
        Assert.Equal("vNew", c.Get(2, "k"));
    }

    [Fact]
    public void Unregistered_value_type_is_rejected_registered_round_trips()
    {
        var c1 = New();                                          // default registry: no Note
        Assert.False(c1.Set(0, "k", new Note("hi", 1)));        // not on allow-list -> rejected
        Assert.False(c1.ContainsKey(0, "k"));

        var reg = TypeRegistry.CreateDefault();
        reg.Register<Note>();
        var c2 = New(registry: reg);
        Assert.True(c2.Set(0, "k", new Note("hi", 1)));        // registered -> commits
        c2.Settle();
        Assert.Equal(new Note("hi", 1), c2.Get(2, "k"));
    }
}
