using KeyValueDb;

var snapshotPath = Path.Combine(AppContext.BaseDirectory, "demo-snapshot.json");

// Register the custom types so they can be persisted/loaded safely.
var registry = TypeRegistry.CreateDefault();
registry.Register<CityKey>();
registry.Register<Weather>();

Console.WriteLine("== Writing a store with mixed key/value types ==");
using (var store = new KeyValueStore(registry))
{
    store.Set(1, "an int key");                       // int -> string
    store.Set("greeting", "a string key");            // string -> string
    store.Set(Guid.NewGuid(), 42);                    // Guid -> int
    store.Set(new CityKey("US", "Bellevue"),          // composite key -> record value
        new Weather(21.5, "Sunny"));

    foreach (var key in store.Keys)
    {
        Console.WriteLine($"  [{key.GetType().Name}] {key} => {store.Get(key)}");
    }

    store.Snapshot(snapshotPath);
    Console.WriteLine($"\nSnapshot written to: {snapshotPath}");
}

Console.WriteLine("\n== Loading into a fresh store ==");
var loadRegistry = TypeRegistry.CreateDefault();
loadRegistry.Register<CityKey>();
loadRegistry.Register<Weather>();

using (var loaded = new KeyValueStore(loadRegistry))
{
    loaded.Load(snapshotPath);
    Console.WriteLine($"Loaded {loaded.Count} entries.");

    // Composite keys resolve by value equality after a round trip.
    if (loaded.TryGet<Weather>(new CityKey("US", "Bellevue"), out var weather))
    {
        Console.WriteLine($"Bellevue weather: {weather.TempC}C, {weather.Summary}");
    }
}

// A composite key and a structured value, to show heterogeneous types.
record CityKey(string Country, string City);
record Weather(double TempC, string Summary);
