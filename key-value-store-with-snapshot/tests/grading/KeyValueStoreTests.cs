using KeyValueDb;
using Xunit;

namespace KeyValueDb.Tests;

// Sample custom types used as a composite key and a structured value.
public sealed record CityKey(string Country, string City);

public sealed record Person(string Name, int Age, DateTime JoinedUtc);

public class KeyValueStoreTests
{
    private static string TempPath() =>
        Path.Combine(Path.GetTempPath(), "kvdb-tests", Guid.NewGuid().ToString("N") + ".json");

    // The log directory is created here, so the log tests do not depend on OpenLog
    // creating it (directory creation is a stated Snapshot responsibility, not OpenLog).
    private static string LogPath()
    {
        var dir = Path.Combine(Path.GetTempPath(), "kvdb-tests");
        Directory.CreateDirectory(dir);
        return Path.Combine(dir, Guid.NewGuid().ToString("N") + ".log");
    }

    [Fact]
    public void Set_and_Get_round_trips_a_value()
    {
        using var store = new KeyValueStore();
        store.Set(1, "hello");

        Assert.Equal("hello", store.Get(1));
        Assert.Equal(1, store.Count);
    }

    [Fact]
    public void Get_missing_key_throws()
    {
        using var store = new KeyValueStore();
        // Behavior: a missing key must fail loudly, not be silently returned as null.
        Assert.ThrowsAny<Exception>(() => store.Get("nope"));
    }

    [Fact]
    public void Store_holds_mixed_key_types_simultaneously()
    {
        using var store = new KeyValueStore();
        var guid = Guid.NewGuid();

        store.Set(42, "int-key");
        store.Set("name", "string-key");
        store.Set(guid, "guid-key");

        Assert.Equal("int-key", store.Get(42));
        Assert.Equal("string-key", store.Get("name"));
        Assert.Equal("guid-key", store.Get(guid));
        Assert.Equal(3, store.Count);
    }

    [Fact]
    public void TryGet_generic_returns_typed_value()
    {
        using var store = new KeyValueStore();
        store.Set("answer", 42);

        Assert.True(store.TryGet<int>("answer", out var value));
        Assert.Equal(42, value);

        Assert.False(store.TryGet<string>("answer", out _));
        Assert.False(store.TryGet<int>("missing", out _));
    }

    [Fact]
    public void Remove_and_ContainsKey_and_Clear_behave()
    {
        using var store = new KeyValueStore();
        store.Set("a", 1);
        store.Set("b", 2);

        Assert.True(store.ContainsKey("a"));
        Assert.True(store.Remove("a"));
        Assert.False(store.Remove("a"));
        Assert.False(store.ContainsKey("a"));

        store.Clear();
        Assert.Equal(0, store.Count);
    }

    [Fact]
    public void Indexer_gets_and_sets()
    {
        using var store = new KeyValueStore();
        store["k"] = 99;
        Assert.Equal(99, store["k"]);
    }

    [Fact]
    public void Snapshot_then_Load_reproduces_primitive_data()
    {
        var path = TempPath();
        var when = new DateTime(2026, 6, 22, 10, 0, 0, DateTimeKind.Utc);

        using (var store = new KeyValueStore())
        {
            store.Set(1, "one");
            store.Set("pi", 3.14);
            store.Set(Guid.Empty, when);
            store.Snapshot(path);
        }

        using var loaded = new KeyValueStore();
        loaded.Load(path);

        Assert.Equal(3, loaded.Count);
        Assert.Equal("one", loaded.Get(1));
        Assert.Equal(3.14, loaded.Get("pi"));
        Assert.Equal(when, loaded.Get(Guid.Empty));
    }

    [Fact]
    public void Snapshot_then_Load_round_trips_custom_key_and_value_types()
    {
        var path = TempPath();
        var registry = TypeRegistry.CreateDefault();
        registry.Register<CityKey>();
        registry.Register<Person>();

        var key = new CityKey("US", "Bellevue");
        var person = new Person("Gordon", 30, new DateTime(2026, 1, 1, 0, 0, 0, DateTimeKind.Utc));

        using (var store = new KeyValueStore(registry))
        {
            store.Set(key, person);
            store.Snapshot(path);
        }

        var loadRegistry = TypeRegistry.CreateDefault();
        loadRegistry.Register<CityKey>();
        loadRegistry.Register<Person>();

        using var loaded = new KeyValueStore(loadRegistry);
        loaded.Load(path);

        // Record value-equality means the composite key still resolves.
        Assert.True(loaded.TryGet<Person>(new CityKey("US", "Bellevue"), out var got));
        Assert.Equal(person, got);
    }

    [Fact]
    public void Null_values_round_trip()
    {
        var path = TempPath();
        using (var store = new KeyValueStore())
        {
            store.Set("nothing", null);
            store.Snapshot(path);
        }

        using var loaded = new KeyValueStore();
        loaded.Load(path);

        Assert.True(loaded.TryGet("nothing", out var value));
        Assert.Null(value);
    }

    [Fact]
    public void Snapshot_throws_for_unregistered_value_type()
    {
        using var store = new KeyValueStore();

        // Snapshot into a directory that already exists, so the ONLY possible reason
        // to throw is the unregistered type — not a missing parent directory. This
        // keeps the negative assertion type-agnostic yet behaviorally precise.
        var dir = Path.Combine(Path.GetTempPath(), "kvdb-tests");
        Directory.CreateDirectory(dir);
        var path = Path.Combine(dir, Guid.NewGuid().ToString("N") + ".json");

        // Behavior: persisting an unregistered type must fail loudly rather than
        // silently writing it. Implementations may enforce this eagerly (at Set) or
        // lazily (at Snapshot); both are accepted, so the whole sequence is wrapped.
        Assert.ThrowsAny<Exception>(() =>
        {
            store.Set("key", new Person("X", 1, DateTime.UnixEpoch)); // Person not registered
            store.Snapshot(path);
        });
    }

    [Fact]
    public void Load_throws_for_unregistered_type()
    {
        var path = TempPath();
        var registry = TypeRegistry.CreateDefault();
        registry.Register<Person>();

        using (var store = new KeyValueStore(registry))
        {
            store.Set("p", new Person("X", 1, DateTime.UnixEpoch));
            store.Snapshot(path);
        }

        // Default registry does not know about Person.
        // Behavior: loading a type that is not on the allow-list must fail loudly
        // rather than silently constructing an arbitrary type named in the file.
        using var loaded = new KeyValueStore();
        Assert.ThrowsAny<Exception>(() => loaded.Load(path));
    }

    [Fact]
    public void Snapshot_overwrites_previous_snapshot()
    {
        var path = TempPath();
        using var store = new KeyValueStore();

        store.Set("v", 1);
        store.Snapshot(path);

        store.Set("v", 2);
        store.Snapshot(path);

        using var loaded = new KeyValueStore();
        loaded.Load(path);
        Assert.Equal(2, loaded.Get("v"));
    }

    [Fact]
    public void Load_replaces_existing_contents()
    {
        var path = TempPath();
        using (var source = new KeyValueStore())
        {
            source.Set("fromfile", 1);
            source.Snapshot(path);
        }

        using var store = new KeyValueStore();
        store.Set("stale", 99);
        store.Load(path);

        Assert.False(store.ContainsKey("stale"));
        Assert.True(store.ContainsKey("fromfile"));
    }

    [Fact]
    public void Concurrent_writes_are_thread_safe()
    {
        using var store = new KeyValueStore();
        const int perThread = 1000;
        const int threads = 8;

        Parallel.For(0, threads, t =>
        {
            for (var i = 0; i < perThread; i++)
            {
                store.Set($"{t}:{i}", i);
            }
        });

        Assert.Equal(threads * perThread, store.Count);
    }

    [Fact]
    public void Large_snapshot_round_trips_many_entries()
    {
        var path = TempPath();
        const int n = 50_000;

        using (var store = new KeyValueStore())
        {
            for (var i = 0; i < n; i++)
            {
                // Alternate the value's runtime type so the snapshot must serialize
                // each entry by its own type, not a single homogeneous element type.
                if (i % 2 == 0)
                {
                    store.Set(i, $"value-{i}");
                }
                else
                {
                    store.Set(i, i * 2);
                }
            }

            Assert.Equal(n, store.Count);
            store.Snapshot(path);
        }

        // A real on-disk file with all entries, not a truncated/streamed-short write.
        Assert.True(new FileInfo(path).Length > 100_000);

        using var loaded = new KeyValueStore();
        loaded.Load(path);

        Assert.Equal(n, loaded.Count);

        // Spot-check across the whole range and confirm runtime types survived.
        Assert.Equal("value-0", loaded.Get(0));
        Assert.Equal(2, loaded.Get(1));
        Assert.Equal("value-1000", loaded.Get(1000));
        Assert.Equal((n - 1) * 2, loaded.Get(n - 1));

        Assert.True(loaded.TryGet<string>(2500, out var s));
        Assert.Equal("value-2500", s);
        Assert.True(loaded.TryGet<int>(2501, out var v));
        Assert.Equal(2501 * 2, v);
    }

    [Fact]
    public void Log_replays_on_open()
    {
        var path = LogPath();
        using (var store = new KeyValueStore())
        {
            store.OpenLog(path);
            store.Set(1, "a");
            store.Set("k", 2);
            store.Set(1, "b");   // overwrite
            store.Remove("k");
        }

        using var reopened = new KeyValueStore();
        reopened.OpenLog(path);

        Assert.Equal(1, reopened.Count);
        Assert.Equal("b", reopened.Get(1));
        Assert.False(reopened.ContainsKey("k"));
    }

    [Fact]
    public void Log_appends_survive_across_reopen_and_continue()
    {
        var path = LogPath();
        using (var s1 = new KeyValueStore())
        {
            s1.OpenLog(path);
            s1.Set(1, "a");
        }

        using (var s2 = new KeyValueStore())
        {
            s2.OpenLog(path);
            Assert.Equal("a", s2.Get(1)); // replayed
            s2.Set(2, "b");               // appended after replay
        }

        using var s3 = new KeyValueStore();
        s3.OpenLog(path);
        Assert.Equal(2, s3.Count);
        Assert.Equal("a", s3.Get(1));
        Assert.Equal("b", s3.Get(2));
    }

    [Fact]
    public void Log_recovers_from_truncated_tail()
    {
        var path = LogPath();
        using (var store = new KeyValueStore())
        {
            store.OpenLog(path);
            store.Set(1, "one");
            store.Set(2, "two");
        }

        // Simulate a crash mid-append: a partial, non-newline-terminated final record.
        File.AppendAllText(path, "{\"Op\":\"set\",\"KeyType\":\"System.Int32\",\"Key\":3,\"Val");

        using var recovered = new KeyValueStore();
        recovered.OpenLog(path); // must NOT throw

        Assert.Equal(2, recovered.Count);
        Assert.Equal("one", recovered.Get(1));
        Assert.Equal("two", recovered.Get(2));
        Assert.False(recovered.ContainsKey(3));
    }

    [Fact]
    public void Compact_rewrites_log_to_live_state()
    {
        var path = LogPath();
        using (var store = new KeyValueStore())
        {
            store.OpenLog(path);
            store.Set("x", 1);
            store.Set("x", 2);
            store.Set("x", 3);
            store.Set("y", 9);
            store.Remove("y");

            var beforeLines = File.ReadAllLines(path).Length; // 5 records
            store.Compact();
            var afterLines = File.ReadAllLines(path).Length;  // 1 live key

            Assert.True(afterLines < beforeLines);
            Assert.Equal(1, afterLines);
        }

        using var reopened = new KeyValueStore();
        reopened.OpenLog(path);
        Assert.Equal(1, reopened.Count);
        Assert.Equal(3, reopened.Get("x"));
        Assert.False(reopened.ContainsKey("y"));
    }

    [Fact]
    public void Compact_keeps_log_open_for_further_appends()
    {
        var path = LogPath();
        using (var store = new KeyValueStore())
        {
            store.OpenLog(path);
            store.Set("x", 1);
            store.Set("x", 2);
            store.Set("y", 9);

            store.Compact(); // log now holds one record each for live keys x, y

            // Compact rewrites the *open* log (API 14): the store must stay bound to
            // it, so subsequent mutations keep appending (API 13) rather than being
            // dropped or throwing on a stale/closed handle.
            store.Set("z", 3);
            store.Remove("y");
        }

        using var reopened = new KeyValueStore();
        reopened.OpenLog(path);

        Assert.Equal(2, reopened.Count); // x and z live; y removed after compaction
        Assert.Equal(2, reopened.Get("x"));
        Assert.Equal(3, reopened.Get("z"));
        Assert.False(reopened.ContainsKey("y"));
    }

    // A fixed instant; tests advance the captured local to drive TTL deterministically.
    private static readonly DateTimeOffset T0 = new(2026, 1, 1, 0, 0, 0, TimeSpan.Zero);

    [Fact]
    public void Ttl_entry_expires_after_clock_advances()
    {
        var now = T0;
        using var store = new KeyValueStore(null, () => now);
        store.Set("k", 42, TimeSpan.FromSeconds(10));

        Assert.True(store.ContainsKey("k"));
        Assert.Equal(42, store.Get("k"));
        Assert.Equal(1, store.Count);

        now = T0.AddSeconds(11); // advance past expiry

        Assert.False(store.ContainsKey("k"));
        Assert.False(store.TryGet("k", out _));
        Assert.Equal(0, store.Count);
        // Behavior: an expired key reads as absent — Get fails loudly like any miss.
        Assert.ThrowsAny<Exception>(() => store.Get("k"));
    }

    [Fact]
    public void Set_without_ttl_never_expires()
    {
        var now = T0;
        using var store = new KeyValueStore(null, () => now);
        store.Set("k", 1);

        now = T0.AddYears(10);

        Assert.True(store.ContainsKey("k"));
        Assert.Equal(1, store.Get("k"));
        Assert.Equal(1, store.Count);
    }

    [Fact]
    public void Ttl_survives_snapshot_and_load()
    {
        var path = TempPath();
        var writeNow = T0;
        using (var store = new KeyValueStore(null, () => writeNow))
        {
            store.Set("k", 1, TimeSpan.FromSeconds(100));
            store.Snapshot(path);
        }

        var readNow = T0; // 0s elapsed since the absolute expiry was set
        using var loaded = new KeyValueStore(null, () => readNow);
        loaded.Load(path);
        Assert.True(loaded.ContainsKey("k"));

        readNow = T0.AddSeconds(101); // past the persisted absolute expiry
        Assert.False(loaded.ContainsKey("k"));
    }

    [Fact]
    public void Ttl_expired_entry_dropped_on_log_replay()
    {
        var path = LogPath();
        var now = T0;
        using (var store = new KeyValueStore(null, () => now))
        {
            store.OpenLog(path);
            store.Set("short", 1, TimeSpan.FromSeconds(10));
            store.Set("long", 2, TimeSpan.FromSeconds(1000));
        }

        var later = T0.AddSeconds(11); // "short" expired, "long" still alive
        using var reopened = new KeyValueStore(null, () => later);
        reopened.OpenLog(path);

        Assert.False(reopened.ContainsKey("short"));
        Assert.True(reopened.ContainsKey("long"));
        Assert.Equal(2, reopened.Get("long"));
        Assert.Equal(1, reopened.Count);
    }
}
