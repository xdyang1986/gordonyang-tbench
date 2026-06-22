Create a key-value store that supports different type of keys and values. And also support to make a snapshot into the disk and restore from the snapshot.

Related APIs:
1. void Set(object key, object? value) - Insert or update the key/value.
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

Construction:
1. KeyValueStore() - Creates a new store.
2. KeyValueStore(registry) - registry parameter is optional with a default.

Notes:
1. Please be aware that the key-value store should be thread-safe.
2. Exceptions should be thrown for invalid operations.
