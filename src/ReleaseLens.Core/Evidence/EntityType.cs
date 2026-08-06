namespace ReleaseLens.Core.Evidence;

public enum EntityType
{
    Commit,
    Issue,
    PullRequest,
    Release
}

public static class EntityTypeExtensions
{
    /// <summary>
    /// The database-facing name. These four strings are duplicated in the
    /// evidence_chunks CHECK constraint (Task 4); changing one means changing both.
    /// </summary>
    public static string ToWireName(this EntityType type) => type switch
    {
        EntityType.Commit => "commit",
        EntityType.Issue => "issue",
        EntityType.PullRequest => "pull_request",
        EntityType.Release => "release",
        _ => throw new ArgumentOutOfRangeException(nameof(type), type, "Unknown entity type.")
    };

    public static EntityType FromWireName(string wireName) => wireName switch
    {
        "commit" => EntityType.Commit,
        "issue" => EntityType.Issue,
        "pull_request" => EntityType.PullRequest,
        "release" => EntityType.Release,
        _ => throw new ArgumentOutOfRangeException(nameof(wireName), wireName, "Unknown entity type.")
    };
}
