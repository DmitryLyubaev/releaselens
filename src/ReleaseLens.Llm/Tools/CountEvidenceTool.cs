using System.Globalization;
using System.Text;
using System.Text.Json;
using ReleaseLens.Core.Evidence;
using ReleaseLens.Storage;
using ReleaseLens.Storage.Retrieval;

namespace ReleaseLens.Llm.Tools;

/// <summary>
/// Counts artefacts with one bounded SQL aggregate.
/// </summary>
/// <remarks>
/// Every other tool in this registry returns evidence chunks, so an aggregation question
/// could previously only be answered by retrieving toward completeness — search, see a
/// partial list, search again, accumulate the corpus in context. That is what made one
/// counting question 78% of an evaluation run's cost. This tool computes instead of
/// retrieving: the answer is a single number and the context does not grow with the size
/// of the set being counted.
/// </remarks>
public sealed class CountEvidenceTool(EvidenceQueries queries) : IEvidenceTool
{
    public string Name => "count_evidence";

    public string Description =>
        "Count how many commits, issues, pull requests or releases fall in a date window. " +
        "Computes the count in the database over the whole corpus, so use it for any 'how many' " +
        "question instead of counting search results, which only ever see a sample. " +
        "You must say which date to count by: 'committed' for commits, 'created' for issues, " +
        "'created' or 'merged' for pull requests, 'published' for releases. " +
        "Issues can also be narrowed by label, which is how to answer 'how many bugs' - counting " +
        "the rows find_regressions returned would only ever count its page limit. Labels match " +
        "case-insensitively, so 'bug' also finds 'Bug', and an issue matches if it carries any " +
        "one of the labels given. Labels apply to issues only; asking for them with another " +
        "entity_type is an error rather than an unfiltered count. " +
        "Returns a number, the exact predicate applied, and the date range the corpus actually " +
        "covers — check that range before presenting the number as complete. " +
        "Returns no citation, because a computed count is not an artefact.";

    public JsonElement JsonSchema { get; } = JsonArgs.Schema("""
        {
          "type": "object",
          "properties": {
            "entity_type": { "type": "string", "enum": ["commit", "issue", "pull_request", "release"],
                             "description": "What to count." },
            "date_field":  { "type": "string", "enum": ["committed", "created", "merged", "published"],
                             "description": "Which date to count by. Valid values depend on entity_type: commit accepts 'committed'; issue accepts 'created'; pull_request accepts 'created' (opened) or 'merged'; release accepts 'published'. Opened and merged are different questions - pick the one asked." },
            "since":       { "type": "string", "description": "Optional ISO-8601 date. Inclusive lower bound, UTC." },
            "until":       { "type": "string", "description": "Optional ISO-8601 date. Upper bound, UTC. A plain date such as 2024-12-31 includes the whole of that day." },
            "labels":      { "type": "array", "items": { "type": "string" },
                             "description": "Optional, and only valid when entity_type is 'issue'. Count only issues carrying at least one of these labels. Matched case-insensitively but otherwise exactly, so 'bug' finds 'bug' and 'Bug' but not 'bugfix'. Supplying this with any other entity_type is an error." }
          },
          "required": ["entity_type", "date_field"]
        }
        """);

    public async Task<ToolExecutionResult> ExecuteAsync(
        TenantScope scope, JsonElement arguments, CancellationToken cancellationToken)
    {
        var entityTypeText = JsonArgs.String(arguments, "entity_type");
        if (string.IsNullOrWhiteSpace(entityTypeText))
        {
            return ToolExecutionResult.Error(
                "The 'entity_type' argument is required. Use commit, issue, pull_request or release.");
        }

        EntityType entityType;
        try
        {
            entityType = EntityTypeExtensions.FromWireName(entityTypeText);
        }
        catch (ArgumentOutOfRangeException)
        {
            return ToolExecutionResult.Error(
                $"'{entityTypeText}' is not a valid entity_type. Use commit, issue, pull_request or release.");
        }

        var dateFieldText = JsonArgs.String(arguments, "date_field");
        if (string.IsNullOrWhiteSpace(dateFieldText))
        {
            return ToolExecutionResult.Error(
                $"The 'date_field' argument is required and is not inferred. For {entityTypeText} use " +
                $"{Alternatives(entityType)}.");
        }

        // The mismatch case is the one worth being strict about. Answering "how many pull
        // requests were merged in 2024" with the count of pull requests *opened* in 2024
        // is not a near miss, it is a different number for a different question, and
        // nothing downstream could tell it had happened.
        var field = EvidenceDateFields.Find(entityType, dateFieldText);
        if (field is null)
        {
            return ToolExecutionResult.Error(
                $"'{dateFieldText}' is not a valid date_field for entity_type '{entityTypeText}'. " +
                $"Valid: {Alternatives(entityType)}. " +
                "Choose the one the question actually asks about rather than retrying with another.");
        }

        if (!JsonArgs.TryStringArray(arguments, "labels", out var labels, out var labelsError))
        {
            return ToolExecutionResult.Error(labelsError!);
        }

        // Naming the restriction beats honouring the rest of the call. Only issues carry
        // labels, so "how many bug-labelled commits in 2024" has no answer here — and the
        // count it would otherwise return, every commit in 2024, is a precise figure for a
        // different question with nothing in the result to show the filter went missing.
        if (labels is not null && entityType != EntityType.Issue)
        {
            return ToolExecutionResult.Error(
                $"'labels' is only supported with entity_type='issue', not '{entityTypeText}', because " +
                "only issues carry labels. The filter was not applied and no count was computed. " +
                "Count issues instead, or drop the labels argument if you meant every " +
                $"{entityTypeText} in the window.");
        }

        if (!AggregateWindow.TryRead(arguments, "since", isUpperBound: false, out var since, out var sinceError))
        {
            return ToolExecutionResult.Error(sinceError!);
        }

        if (!AggregateWindow.TryRead(arguments, "until", isUpperBound: true, out var until, out var untilError))
        {
            return ToolExecutionResult.Error(untilError!);
        }

        if (since is not null && until is not null && until.Value <= since.Value)
        {
            return ToolExecutionResult.Error(
                $"The window is empty: until ({AggregateWindow.Format(until.Value)}) is not after " +
                $"since ({AggregateWindow.Format(since.Value)}). Check the order of the dates.");
        }

        var count = await queries.CountAsync(scope, field, since?.Value, until?.Value, labels, cancellationToken);
        var coverage = await queries.GetCoverageAsync(scope, field, cancellationToken);

        var label = $"{entityTypeText}.{field.FieldName}";

        var content = new StringBuilder()
            .Append("Count: ").AppendLine(count.ToString(CultureInfo.InvariantCulture))
            .Append("Predicate: entity_type=").Append(entityTypeText)
            .Append(", date_field=").Append(field.FieldName)
            .Append(" (").Append(field.Qualified).Append("), ")
            .Append(AggregateWindow.DescribeWindow(field.Column, since, until));

        if (labels is not null)
        {
            content.Append(", labels overlapping [")
                   .Append(string.Join(", ", labels.Select(l => $"'{l}'")))
                   .Append("] (case-insensitive; an issue matches if it carries any one of them)");
        }

        content.AppendLine(".");

        // Deliberately the unfiltered coverage of the date field. Coverage answers "what
        // span of history is held at all", which the label filter does not change, and a
        // filtered version would quietly redefine INCOMPLETE COVERAGE to mean "no issue
        // with this label falls outside the window" - a different and far weaker claim.
        AggregateWindow.AppendCoverage(content, label, coverage, since, until);

        // A count is computed, not retrieved, so there is no artefact to point at and no
        // citation is produced. Without this the model is caught between a system prompt
        // that requires a marker on every factual claim and a number that has none, and
        // the ways out of that bind are all bad: attach someone else's marker, drop the
        // number, or hedge it into uselessness.
        content.AppendLine()
               .AppendLine(
                   "This count was computed over the whole corpus, not retrieved, so it has no evidence " +
                   "marker and needs none. State the number with the predicate above as its justification, " +
                   "and do not attach an unrelated [E] marker to it.")
               .Append("Write the figure exactly as printed above, in digits, with no thousands separator ")
               .AppendLine("and no rounding or hedging.");

        return ToolExecutionResult.Ok(content.ToString(), []);
    }

    private static string Alternatives(EntityType type)
        => string.Join(" or ", EvidenceDateFields.NamesFor(type).Select(n => $"date_field='{n}'"));
}
