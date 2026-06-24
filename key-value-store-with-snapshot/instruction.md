Build a distributed key value store that support read and write in multiple nodes.

1. There are N nodes, and one of them is the configured leader. Write should go to the leader (forward to leader if from follower) and read from local commit even if  it's stale.
2. Deletes are durable across sync.
3. Conflict rule: higher (epoch, seq) wins, higher epoch beats higher seq.
4. epoch bumps after manual failover(leader promotion)
5. replicated values must be on the registry allow-list.
6. If the Set/Remove false, let's return bool instead of throw exception.
7. If the Get on a miss key, throw exception.
8. Write commits only when it gets strict majority acknowledge, otherwise return fails, no local apply on failure.
9. Partition(ids…) splits cluster and drops cross-cut messages; Heal() restores connectivity (does not catch up stale nodes); PromoteLeader(id) forces failover with epoch bump; Settle() converges connected nodes by (epoch,seq) (tombstones included).

Interface:

IReplicatedKvCluster DistributedKv.CreateCluster(int nodes, int leaderId, TypeRegistry? registry = null);

interface IReplicatedKvCluster {
    bool Set(int nodeId, object key, object? value);
    object? Get(int nodeId, object key);
    bool Remove(int nodeId, object key);
    bool ContainsKey(int nodeId, object key);
    int  Count(int nodeId);
    void Partition(params int[] ids);
    void Heal();
    void PromoteLeader(int nodeId);
    void Settle();
    int  LeaderId { get; }
}
