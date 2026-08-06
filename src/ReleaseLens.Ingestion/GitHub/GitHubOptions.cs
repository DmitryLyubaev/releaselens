namespace ReleaseLens.Ingestion.GitHub;

public sealed class GitHubOptions
{
    public const string SectionName = "GitHub";

    public string Owner { get; set; } = "microsoft";
    public string Repository { get; set; } = "semantic-kernel";

    /// <summary>Personal access token. Read from the GITHUB_TOKEN environment variable; never committed.</summary>
    public string Token { get; set; } = string.Empty;

    /// <summary>
    /// Ingestion window start. Defaults to 2024-01-01 because per-commit file lists cost
    /// one request each and the full history of a repository this size would exhaust the budget.
    /// </summary>
    public DateTimeOffset SinceUtc { get; set; } = new(2024, 1, 1, 0, 0, 0, TimeSpan.Zero);

    /// <summary>When true, issues an extra detail request per commit to collect changed files.</summary>
    public bool FetchCommitFiles { get; set; } = true;

    public int PageSize { get; set; } = 100;
    public int RequestsPerHour { get; set; } = 4500;
}
