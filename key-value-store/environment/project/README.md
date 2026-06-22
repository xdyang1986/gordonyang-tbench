# KeyValueDb

A thread-safe, in-memory key/value store for C# (.NET 8) that supports
**heterogeneous key and value types** in a single store and can **snapshot to
disk** as JSON and **load back** from disk.

## Features

- **Mixed key types** — `int`, `string`, `Guid`, and composite `record`/`struct`
  keys all live in the same store. Keys compare by their type's default equality,
  so records/structs with value equality work naturally.
- **Any value type** — values are stored as `object?`, with a typed `TryGet<T>`
  helper for convenience.
- **Thread-safe** — concurrent reads and writes are safe (`ConcurrentDictionary`),
  and a `ReaderWriterLockSlim` gives snapshot/load an exclusive window so a save
  is a consistent point-in-time view.
- **JSON snapshots** — human-readable `System.Text.Json` format. Writes are
  **atomic** (temp file + move), so a crash mid-write can't corrupt the file.
- **Safe loading** — a `TypeRegistry` allow-lists the types that may be persisted
  or read back. Loading an unregistered type fails loudly instead of constructing
  arbitrary types named in a file (avoids deserialization-gadget risks).

## Layout

```
src/KeyValueDb/          # the library
tests/KeyValueDb.Tests/  # xUnit tests
samples/KeyValueDb.Demo/ # runnable console demo
```

## Usage

```csharp
using KeyValueDb;

// Register any custom key/value types you intend to persist.
var registry = TypeRegistry.CreateDefault();   // primitives, string, Guid, DateTime, ...
registry.Register<CityKey>();
registry.Register<Weather>();

using var store = new KeyValueStore(registry);

store.Set(1, "an int key");                       // int    -> string
store.Set("greeting", "hello");                   // string -> string
store.Set(Guid.NewGuid(), 42);                    // Guid   -> int
store.Set(new CityKey("US", "Bellevue"),          // record key -> record value
          new Weather(21.5, "Sunny"));

// Persist to disk and reload elsewhere.
store.Snapshot("data.json");

using var reloaded = new KeyValueStore(registry);
reloaded.Load("data.json");

if (reloaded.TryGet<Weather>(new CityKey("US", "Bellevue"), out var w))
    Console.WriteLine($"{w.TempC}C, {w.Summary}");

record CityKey(string Country, string City);
record Weather(double TempC, string Summary);
```

## API

| Member | Description |
|--------|-------------|
| `Set(key, value)` / `this[key]` | Insert or update |
| `Get(key)` | Read (throws `KeyNotFoundException` if absent) |
| `TryGet(key, out value)` / `TryGet<T>(key, out value)` | Safe read |
| `Remove(key)` / `ContainsKey(key)` / `Clear()` | Mutate / query |
| `Count` / `Keys` | Inspect |
| `Snapshot(path)` | Atomic JSON save |
| `Load(path)` | Replace contents from JSON |
| `Types` | The `TypeRegistry` (allow-list of persistable types) |

## Build, test, run

```bash
dotnet build  KeyValueDb.sln -c Release
dotnet test   KeyValueDb.sln -c Release
dotnet run --project samples/KeyValueDb.Demo -c Release
```

## Design notes & trade-offs

- **Snapshot format** carries a `Version` field; `Load` rejects unknown versions
  so the format can evolve safely.
- **`object` keys** trade a little type-safety for the requested "different types
  of keys in one store." If you only need one key type, a generic
  `KeyValueStore<TKey, TValue>` would be more type-safe — this design favours the
  heterogeneous requirement.
- **Persistence is full-snapshot**, not an append-only log. It's simple and
  correct; for very large or high-churn datasets a write-ahead log + periodic
  compaction would scale better.
