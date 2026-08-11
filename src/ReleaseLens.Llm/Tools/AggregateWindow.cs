using System.Globalization;
using System.Text;
using System.Text.Json;
using ReleaseLens.Storage.Retrieval;

namespace ReleaseLens.Llm.Tools;

/// <summary>
/// One end of a requested date window, as parsed from a tool argument.
/// </summary>
/// <param name="Value">The instant used in the SQL predicate, always UTC.</param>
/// <param name="Text">Exactly what the caller wrote, for echoing back in the predicate.</param>
/// <param name="ExpandedToEndOfDay">
/// True when a plain date given as <c>until</c> was widened to the end of that day.
/// </param>
internal sealed record WindowBound(DateTimeOffset Value, string Text, bool ExpandedToEndOfDay);

/// <summary>
/// Date-window handling shared by the aggregate tools. Kept apart from
/// <see cref="JsonArgs"/> because the aggregate tools need three things the retrieval
/// tools do not: UTC-anchored parsing, a hard error on an unparseable date, and a
/// record of what was applied so the result can state its own predicate.
/// </summary>
internal static class AggregateWindow
{
    /// <summary>
    /// Reads one window bound, distinguishing "absent" from "present but unparseable".
    /// </summary>
    /// <remarks>
    /// <para>
    /// <see cref="JsonArgs.Date"/> returns null for both cases, which is right for a
    /// search tool — a dropped filter widens the results and the model sees them. It is
    /// wrong here. A dropped filter on a count silently changes the window and returns a
    /// number that is precise, checkable-looking and about a different question than the
    /// one asked. So an unparseable date is an error, not a shrug.
    /// </para>
    /// <para>
    /// Parsing assumes UTC when the text carries no offset. The default
    /// <see cref="DateTimeOffset.TryParse(string, out DateTimeOffset)"/> would read
    /// "2024-01-01" in the server's local zone, so the same question would return
    /// different counts on a machine in Sydney and a machine in London.
    /// </para>
    /// </remarks>
    public static bool TryRead(
        JsonElement arguments, string name, bool isUpperBound, out WindowBound? bound, out string? error)
    {
        bound = null;
        error = null;

        if (!arguments.TryGetProperty(name, out var element) || element.ValueKind == JsonValueKind.Null)
        {
            return true;
        }

        if (element.ValueKind != JsonValueKind.String)
        {
            error = $"'{name}' must be an ISO-8601 date string, for example \"2024-01-01\".";
            return false;
        }

        var text = element.GetString() ?? string.Empty;
        if (text.Length == 0)
        {
            return true;
        }

        if (!DateTimeOffset.TryParse(text, CultureInfo.InvariantCulture,
                DateTimeStyles.AssumeUniversal | DateTimeStyles.AdjustToUniversal, out var parsed))
        {
            error = $"'{name}' value \"{text}\" is not an ISO-8601 date. Use a form like \"2024-01-01\".";
            return false;
        }

        // A plain date as the upper bound means the whole of that day. Read literally,
        // until="2024-12-31" would cut the window at midnight and drop that day's
        // records — an off-by-one-day undercount that looks exactly like a correct
        // answer. Widening here, and saying so in the predicate, makes both readings
        // of "during 2024" (until=2024-12-31 and until=2025-01-01) agree.
        var dateOnly = DateOnly.TryParseExact(
            text, "yyyy-MM-dd", CultureInfo.InvariantCulture, DateTimeStyles.None, out _);

        var expand = isUpperBound && dateOnly;
        bound = new WindowBound(expand ? parsed.AddDays(1) : parsed, text, expand);
        return true;
    }

    public static string Format(DateTimeOffset value)
        => value.ToUniversalTime().ToString("yyyy-MM-dd'T'HH:mm:ss'Z'", CultureInfo.InvariantCulture);

    private static string FormatDay(DateTimeOffset value)
        => value.ToUniversalTime().ToString("yyyy-MM-dd", CultureInfo.InvariantCulture);

    /// <summary>Describes the window in the terms the SQL actually applied.</summary>
    public static string DescribeWindow(string column, WindowBound? since, WindowBound? until)
    {
        if (since is null && until is null)
        {
            return $"no date window - every record with a {column} value";
        }

        var parts = new List<string>(2);
        if (since is not null)
        {
            parts.Add($"{column} >= {Format(since.Value)}");
        }

        if (until is not null)
        {
            parts.Add($"{column} < {Format(until.Value)}");
        }

        var description = string.Join(" and ", parts);

        if (until is { ExpandedToEndOfDay: true })
        {
            description += $" (until=\"{until.Text}\" was read as the whole of that day)";
        }

        return description;
    }

    /// <summary>
    /// Appends the coverage block. Every aggregate result carries one, and a requested
    /// window that runs past either end of the corpus is called out explicitly rather
    /// than left for the reader to work out from two dates.
    /// </summary>
    public static void AppendCoverage(
        StringBuilder content, string label, EvidenceCoverage coverage, WindowBound? since, WindowBound? until)
    {
        content.AppendLine();

        if (coverage.Earliest is not { } earliest || coverage.Latest is not { } latest)
        {
            content.Append("Corpus coverage: no ").Append(label)
                   .AppendLine(" records are held at all, so this result reflects an empty corpus, not the repository.");
            return;
        }

        content.Append("Corpus coverage for ").Append(label).Append(": ")
               .Append(FormatDay(earliest)).Append(" to ").Append(FormatDay(latest))
               .Append(" (").Append(coverage.Total.ToString(CultureInfo.InvariantCulture))
               .Append(coverage.Total == 1 ? " record" : " records")
               .AppendLine(" held in total).");

        var gaps = new List<string>(2);
        if (since is not null && since.Value < earliest)
        {
            gaps.Add($"the window starts {FormatDay(since.Value)}, before the earliest {label} record held ({FormatDay(earliest)})");
        }

        if (until is not null && until.Value > latest)
        {
            gaps.Add($"the window ends {FormatDay(until.Value)}, after the latest {label} record held ({FormatDay(latest)})");
        }

        // An unbounded request necessarily spans the whole corpus and no further, so it
        // gets no warning - only an explicit window can overshoot.
        if (gaps.Count > 0)
        {
            content.Append("INCOMPLETE COVERAGE: ").Append(string.Join("; and ", gaps))
                   .AppendLine(".")
                   .AppendLine(
                       "This result is therefore true of the indexed corpus but not of the repository's full history. " +
                       "Say so in the answer, or decline, rather than presenting it as the complete figure.");
        }
    }
}
