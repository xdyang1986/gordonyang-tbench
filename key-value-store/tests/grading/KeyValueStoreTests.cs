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
        Assert.Throws<KeyNotFoundException>(() => store.Get("nope"));
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
        store.Set("key", new Person("X", 1, DateTime.UnixEpoch)); // Person not registered

        Assert.Throws<InvalidOperationException>(() => store.Snapshot(TempPath()));
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
        using var loaded = new KeyValueStore();
        Assert.Throws<InvalidDataException>(() => loaded.Load(path));
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
}
