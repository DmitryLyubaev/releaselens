namespace ReleaseLens.Llm.Providers;

public sealed class ChatOptions
{
    public const string SectionName = "Chat";

    /// <summary>Provider names, in the order the fallback chain tries them.</summary>
    /// <remarks>
    /// Configuration binding adds configured entries to this default instead of replacing it:
    /// binding <c>["azure-openai"]</c> into it yields anthropic, openai, azure-openai, and a
    /// configured empty array leaves the default as it is (checked against
    /// Microsoft.Extensions.Configuration.Binder 10.0.0).
    /// </remarks>
    public List<string> Providers { get; set; } = ["anthropic", "openai"];
}

public static class ChatProviderSelection
{
    /// <summary>
    /// Resolves the configured names, in order, to providers. Every name is checked before any
    /// factory runs, so a bad list builds nothing, and a provider that is not listed is never
    /// built at all.
    /// </summary>
    /// <remarks>
    /// Matching is exact and ordinal because the names are the lowercase identifiers recorded
    /// on spans and usage rows. A near-miss fails startup rather than being skipped: a chain
    /// that silently lost a provider looks healthy until the day the others are down.
    /// </remarks>
    /// <exception cref="InvalidOperationException">
    /// The list is empty, names an unknown provider (a misspelling or a different case), names
    /// one twice, or a factory builds a provider whose <see cref="IChatProvider.Name"/> is not
    /// the key it was registered under.
    /// </exception>
    public static IReadOnlyList<IChatProvider> Select(
        IReadOnlyList<string> names, IReadOnlyDictionary<string, Func<IChatProvider>> factories)
    {
        const string setting = ChatOptions.SectionName + ":Providers";

        // Rebuilt with an ordinal comparer so a case-insensitive dictionary handed in by a caller
        // cannot quietly accept "OpenAI".
        var byName = factories.ToDictionary(pair => pair.Key, pair => pair.Value, StringComparer.Ordinal);
        var valid = "Valid providers: " + string.Join(", ", byName.Keys.Order(StringComparer.Ordinal)) + ".";

        if (names.Count == 0)
        {
            throw new InvalidOperationException($"{setting} is empty; it must name at least one provider. {valid}");
        }

        var seen = new HashSet<string>(StringComparer.Ordinal);

        foreach (var name in names)
        {
            if (!byName.ContainsKey(name))
            {
                throw new InvalidOperationException(
                    $"{setting} names '{name}', which is not a provider. Names are exact and lowercase. {valid}");
            }

            if (!seen.Add(name))
            {
                throw new InvalidOperationException($"{setting} names '{name}' more than once. {valid}");
            }
        }

        var providers = new List<IChatProvider>(names.Count);

        foreach (var name in names)
        {
            var provider = byName[name]();

            if (!string.Equals(provider.Name, name, StringComparison.Ordinal))
            {
                throw new InvalidOperationException(
                    $"The provider registered as '{name}' reports its name as '{provider.Name}'.");
            }

            providers.Add(provider);
        }

        return providers;
    }
}
