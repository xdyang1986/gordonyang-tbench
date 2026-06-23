Create a key-value store that supports different type of keys and values. And also support to make a snapshot into the disk and restore from the snapshot.

Related APIs:
1. void Set(object key, object? value, TimeSpan? ttl = null) - Insert or update the key/value. The entry expires ttl (optional) after it is set.
2. object? Get(object key) - Returns the value for key.
3. bool TryGet(object key, out object? value) - Returns true and the raw value if present, else false.
4. bool TryGet<TValue>(object key, out TValue value) - Returns true only if present and assignable to TValue.
5. bool Remove(object key) - Removes key; returns true if it existed.
6. bool ContainsKey(object key) - Whether key exists.
7. void Clear() - Removes all entries.
8. void Snapshot(string path) - Writes a consistent JSON snapshot to path.
9. void Load(string path) - Replaces all contents with the snapshot at path.
10. void Dispose() - Releases the internal lock.
11. store[key] get/set -- Indexer for Get/Set.
12. Count property -- Number of entries.
13. void OpenLog(string path) - bind the store to an append-only log. replay a record into the store, auto append a record for every subsequent set/remove/clear.
14. void Compact() - rewrites the open log to one record per live key.

Construction:
1. KeyValueStore() - Creates a new store.
2. KeyValueStore(registry?, Func<DateTimeOffset>? clock) - registry parameter is optional with a default. clock is the time source for the TTL expiry, default to UTC now.

Notes:
1. Snapshot must be saved to a given path, if its parent directory doesn't exist, it should be created.
2. The log is one record per line, one record per mutation.
3. OpenLog must replay the log and recover from a truncated or corrupt tail, if the final record is not completed, discard it and keep all complete record, don't throw.
4. TTL is persisted as an absolute expiry in snapshot/load and the log;
5. Implement the files directly, don't stop to ask for plan approval.
