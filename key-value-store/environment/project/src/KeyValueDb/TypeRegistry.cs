namespace KeyValueDb;

/// <summary>
/// Maps CLR types to stable string names used inside snapshot files, and back.
///
/// Snapshots store the type of every key and value by name. On load we must turn
/// those names back into <see cref="Type"/> instances. Resolving arbitrary type
/// names from a file is a well-known deserialization risk, so the registry acts as
/// an allow-list: only registered types can be written or read. Common primitives
/// are pre-registered; register your own key/value types before using them.
/// </summary>
public sealed class TypeRegistry
{
    private readonly Dictionary<string, Type> _byName = new(StringComparer.Ordinal);
    private readonly Dictionary<Type, string> _byType = new();

    /// <summary>Registers <typeparamref name="T"/> under <paramref name="name"/> (defaults to its full name).</summary>
    public TypeRegistry Register<T>(string? name = null) => Register(typeof(T), name);

    /// <summary>Registers <paramref name="type"/> under <paramref name="name"/> (defaults to its full name).</summary>
    public TypeRegistry Register(Type type, string? name = null)
    {
        ArgumentNullException.ThrowIfNull(type);
        name ??= type.FullName ?? type.Name;
        _byName[name] = type;
        _byType[type] = name;
        return this;
    }

    /// <summary>Returns the registered name for <paramref name="type"/>, or throws if it was never registered.</summary>
    public string GetName(Type type)
    {
        ArgumentNullException.ThrowIfNull(type);
        if (_byType.TryGetValue(type, out var name))
        {
            return name;
        }

        throw new InvalidOperationException(
            $"Type '{type.FullName}' is not registered. " +
            $"Call store.Types.Register<{type.Name}>() before storing it so it can be snapshotted.");
    }

    /// <summary>Resolves a snapshot type name back to a <see cref="Type"/>, or throws if not allow-listed.</summary>
    public Type Resolve(string name)
    {
        ArgumentException.ThrowIfNullOrEmpty(name);
        if (_byName.TryGetValue(name, out var type))
        {
            return type;
        }

        throw new InvalidDataException(
            $"Snapshot references type '{name}', which is not registered. " +
            "Register it before loading so the data can be deserialized safely.");
    }

    /// <summary>Creates a registry pre-populated with the common BCL value/primitive types.</summary>
    public static TypeRegistry CreateDefault()
    {
        var registry = new TypeRegistry();
        registry.Register<bool>();
        registry.Register<byte>();
        registry.Register<sbyte>();
        registry.Register<short>();
        registry.Register<ushort>();
        registry.Register<int>();
        registry.Register<uint>();
        registry.Register<long>();
        registry.Register<ulong>();
        registry.Register<float>();
        registry.Register<double>();
        registry.Register<decimal>();
        registry.Register<char>();
        registry.Register<string>();
        registry.Register<Guid>();
        registry.Register<DateTime>();
        registry.Register<DateTimeOffset>();
        registry.Register<TimeSpan>();
        return registry;
    }
}
