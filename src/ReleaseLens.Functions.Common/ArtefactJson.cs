using System.Text.Json;
using System.Text.Json.Nodes;
using ReleaseLens.Core.Evidence;

namespace ReleaseLens.Functions.Common;

/// <summary>An artefact file that is not one: bad JSON, an unknown or missing <c>entityType</c>, or a missing or mistyped field.</summary>
/// <remarks>The message names the problem and where, never the artefact's content: these reach logs.</remarks>
public sealed class InvalidArtefactException(string message, Exception? inner = null)
    : Exception(message, inner);

/// <summary>
/// The artefact format: one evidence record as JSON, in the shape <c>ReleaseLens.Core</c> defines,
/// camelCase, with an <c>entityType</c> discriminator holding the record's wire name (<c>commit</c>,
/// <c>issue</c>, <c>pull_request</c> or <c>release</c>). Every field must be present; a nullable one
/// may be null.
/// </summary>
public static class ArtefactJson
{
    private const string Discriminator = "entityType";

    private static readonly JsonSerializerOptions Options = new()
    {
        PropertyNamingPolicy = JsonNamingPolicy.CamelCase,
        NumberHandling = System.Text.Json.Serialization.JsonNumberHandling.Strict,
        RespectRequiredConstructorParameters = true,
        RespectNullableAnnotations = true,
    };

    /// <exception cref="InvalidArtefactException">The JSON is not a valid artefact.</exception>
    public static IEvidenceRecord Parse(string json)
    {
        ArgumentNullException.ThrowIfNull(json);

        try
        {
            if (JsonNode.Parse(json) is not JsonObject artefact)
            {
                throw new InvalidArtefactException("The artefact is not a JSON object.");
            }

            if (artefact[Discriminator] is not JsonValue kind || !kind.TryGetValue<string>(out var wireName))
            {
                throw new InvalidArtefactException($"The artefact has no string '{Discriminator}'.");
            }

            EntityType type;
            try
            {
                type = EntityTypeExtensions.FromWireName(wireName);
            }
            catch (ArgumentOutOfRangeException)
            {
                throw new InvalidArtefactException($"The artefact's '{Discriminator}' is not an entity type.");
            }

            IEvidenceRecord? record = type switch
            {
                EntityType.Commit => artefact.Deserialize<CommitEvidence>(Options),
                EntityType.Issue => artefact.Deserialize<IssueEvidence>(Options),
                EntityType.PullRequest => artefact.Deserialize<PullRequestEvidence>(Options),
                EntityType.Release => artefact.Deserialize<ReleaseEvidence>(Options),
                _ => throw new InvalidArtefactException($"The artefact's '{Discriminator}' is not an entity type."),
            };
            return record ?? throw new InvalidArtefactException("The artefact is empty.");
        }
        catch (JsonException failure)
        {
            throw new InvalidArtefactException($"The artefact is not valid: {failure.Message}", failure);
        }
    }

    /// <summary>
    /// The record as artefact JSON. A commit's <c>authorEmail</c> is always written as null: chunking
    /// never uses it, and the export should not copy contributors' emails into Storage.
    /// </summary>
    public static string Serialize(IEvidenceRecord record)
    {
        ArgumentNullException.ThrowIfNull(record);

        var written = record switch
        {
            CommitEvidence commit => JsonSerializer.SerializeToNode(commit with { AuthorEmail = null }, Options),
            IssueEvidence issue => JsonSerializer.SerializeToNode(issue, Options),
            PullRequestEvidence pullRequest => JsonSerializer.SerializeToNode(pullRequest, Options),
            ReleaseEvidence release => JsonSerializer.SerializeToNode(release, Options),
            _ => throw new ArgumentException($"{record.GetType().Name} is not an evidence record.", nameof(record)),
        };

        var fields = written!.AsObject();
        // Key is computed from the record's own fields, and Parse rebuilds it.
        fields.Remove("key");
        var artefact = new JsonObject { [Discriminator] = record.Key.Type.ToWireName() };
        foreach (var (name, value) in fields.ToList())
        {
            fields.Remove(name);
            artefact[name] = value;
        }

        return artefact.ToJsonString(Options);
    }
}
