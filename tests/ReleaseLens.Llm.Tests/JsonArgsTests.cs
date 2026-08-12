using System;
using System.Text.Json;
using ReleaseLens.Llm.Tools;
using Xunit;

namespace ReleaseLens.Llm.Tests;

/// <summary>
/// Argument parsing, tested away from the database because the defect it guards is
/// invisible in a query result. A window shifted by the server's UTC offset still
/// returns rows, still looks well formed, and answers a question nobody asked.
/// </summary>
public class JsonArgsTests
{
    private static JsonElement Args(string json) => JsonDocument.Parse(json).RootElement;

    /// <summary>
    /// A bare date is an instant in UTC, not an instant in whatever zone the process
    /// happens to run in.
    /// </summary>
    /// <remarks>
    /// <para>
    /// Asserted on <c>Offset</c> and on the resulting instant rather than by forcing the
    /// process into a known zone. Setting <c>TZ</c> is the usual trick and it does nothing
    /// here: on Windows <see cref="TimeZoneInfo.Local"/> reads the OS setting and ignores
    /// the variable entirely, so a TZ-based test would assert nothing on the machine this
    /// suite actually runs on, and .NET offers no supported way to override the local zone
    /// in-process.
    /// </para>
    /// <para>
    /// The two cases together pin the behaviour on any machine. The bare date catches
    /// <see cref="System.Globalization.DateTimeStyles.AssumeUniversal"/> going missing
    /// wherever the host is not already at UTC. The offset-bearing date catches
    /// <see cref="System.Globalization.DateTimeStyles.AdjustToUniversal"/> going missing
    /// everywhere, host zone included, since default parsing preserves the +05:00 the
    /// caller wrote.
    /// </para>
    /// </remarks>
    [Fact]
    public void Date_BareDate_IsReadAsUtcRegardlessOfTheProcessTimeZone()
    {
        var parsed = JsonArgs.Date(Args("""{"since":"2024-01-01"}"""), "since");

        Assert.NotNull(parsed);
        Assert.Equal(TimeSpan.Zero, parsed.Value.Offset);
        Assert.Equal(new DateTimeOffset(2024, 1, 1, 0, 0, 0, TimeSpan.Zero), parsed.Value);
    }

    [Fact]
    public void Date_OffsetBearingDate_IsNormalisedToUtcRatherThanKeptInItsOwnOffset()
    {
        var parsed = JsonArgs.Date(Args("""{"since":"2024-01-01T00:00:00+05:00"}"""), "since");

        Assert.NotNull(parsed);
        Assert.Equal(TimeSpan.Zero, parsed.Value.Offset);
        Assert.Equal(new DateTimeOffset(2023, 12, 31, 19, 0, 0, TimeSpan.Zero), parsed.Value);
    }

    /// <summary>
    /// The same string read by the retrieval tools and by the aggregate tools must mean the
    /// same instant. Two parsers that disagree by the host's UTC offset would make
    /// count_evidence and search_commits answer the same window differently.
    /// </summary>
    [Fact]
    public void Date_AgreesWithTheAggregateToolsOnTheSameText()
    {
        var arguments = Args("""{"since":"2024-06-30"}""");

        var viaJsonArgs = JsonArgs.Date(arguments, "since");
        Assert.True(AggregateWindow.TryRead(arguments, "since", isUpperBound: false, out var bound, out _));

        Assert.NotNull(bound);
        Assert.Equal(bound.Value, viaJsonArgs);
    }

    [Fact]
    public void Date_UnparseableText_IsStillNullForTheRetrievalTools()
    {
        // Unchanged on purpose. A dropped filter widens a search and the model sees the
        // results; AggregateWindow errors instead because a dropped filter on a count is
        // invisible. Only the zone the text is read in was wrong here, not this choice.
        Assert.Null(JsonArgs.Date(Args("""{"since":"last Tuesday"}"""), "since"));
        Assert.Null(JsonArgs.Date(Args("{}"), "since"));
    }
}
