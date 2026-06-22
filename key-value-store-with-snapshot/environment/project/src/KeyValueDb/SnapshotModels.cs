using System.Text.Json;

namespace KeyValueDb;

/// <summary>The on-disk representation of a single key/value pair.</summary>
public sealed class StoreEntry
{
    /// <summary>Registered name of the key's type.</summary>
    public string KeyType { get; set; } = string.Empty;

    /// <summary>The key, serialized as JSON.</summary>
    public JsonElement Key { get; set; }

    /// <summary>Registered name of the value's type, or <c>null</c> when the value is <c>null</c>.</summary>
    public string? ValueType { get; set; }

    /// <summary>The value, serialized as JSON, or <c>null</c> when the value is <c>null</c>.</summary>
    public JsonElement? Value { get; set; }
}

/// <summary>The root document written to a snapshot file.</summary>
public sealed class SnapshotData
{
    /// <summary>Current snapshot schema version. Bump when the format changes incompatibly.</summary>
    public const int CurrentVersion = 1;

    /// <summary>Schema version of this snapshot.</summary>
    public int Version { get; set; } = CurrentVersion;

    /// <summary>All stored entries.</summary>
    public List<StoreEntry> Entries { get; set; } = new();
}
