using System;
using System.Threading.Tasks;
using ReleaseLens.Core.Evidence;
using ReleaseLens.Storage.Repositories;
using ReleaseLens.Storage.Retrieval;
using Xunit;

namespace ReleaseLens.Storage.Tests;

[Collection(nameof(PostgresCollection))]
public class EvidenceQueriesTests(PostgresFixture fixture)
{
    private readonly EvidenceQueries _queries = new();

    private static async Task<(TenantConnectionFactory Factory, Guid TenantId)> ArrangeTenantAsync(
        string connectionString, string slug)
    {
        var factory = new TenantConnectionFactory(connectionString);
        var tenantId = await new TenantRepository(factory).CreateAsync(
            new TenantDefinition(slug, slug, "github", "microsoft", "semantic-kernel", 1_000_000),
            TestContext.Current.CancellationToken);
        return (factory, tenantId);
    }

    [Fact]
    public async Task FindRegressionCandidates_IgnoresAPullRequestMergedBeforeTheIssueExisted()
    {
        // Issue 11222 is opened on 2026-03-26. Two merged pull requests mention "#11222":
        //
        //   PR 6138 merged 2025-05-07 - ten months BEFORE the issue was opened. It cannot
        //                               have fixed it; the number collides by accident
        //                               (a vendored changelog quoting some other tracker).
        //   PR 7400 merged 2026-04-02 - after the issue, and is the real fix.
        //
        // This mirrors a real false positive from the live corpus, where a dependabot bump
        // in the Python tree was reported as the fix for a .NET issue filed ten months later.
        // `order by p.merged_at limit 1` takes the *earliest* match, so an unconstrained
        // subquery actively prefers the impossible one.
        //
        // Both PRs are seeded deliberately: asserting merely that an impossible PR yields
        // null would also pass against a subquery that had stopped returning anything at
        // all. The assertion that matters is that the *later, possible* PR is chosen.
        var (factory, tenantId) = await ArrangeTenantAsync(fixture.ConnectionString, "regression-pr-temporal");

        var issueCreatedAt = new DateTimeOffset(2026, 3, 26, 0, 0, 0, TimeSpan.Zero);

        await using (var seed = await factory.OpenAsync(tenantId, TestContext.Current.CancellationToken))
        {
            var evidence = new EvidenceRepository();

            await evidence.UpsertIssuesAsync(seed,
            [
                new IssueEvidence(tenantId, 11222,
                    "Protected abstract RestoreChannelAsync() in Agent.cs prevents agent composition",
                    "Repro steps for the planner.", "open", ["bug"], "carol",
                    issueCreatedAt, null,
                    "https://github.com/microsoft/semantic-kernel/issues/11222")
            ], TestContext.Current.CancellationToken);

            await evidence.UpsertPullRequestsAsync(seed,
            [
                new PullRequestEvidence(tenantId, 6138,
                    "Bump ruff from 0.4.1 to 0.4.3 in /python",
                    "Bundled changelog: fixes #11222 upstream.",
                    "merged", new DateTimeOffset(2025, 5, 7, 0, 0, 0, TimeSpan.Zero), "sha_pr6138",
                    "main", "dependabot/ruff", "dependabot",
                    new DateTimeOffset(2025, 5, 1, 0, 0, 0, TimeSpan.Zero),
                    "https://github.com/microsoft/semantic-kernel/pull/6138"),
                new PullRequestEvidence(tenantId, 7400,
                    "Make RestoreChannelAsync public. Fixes #11222.",
                    "Fixes #11222 by relaxing the planner's channel visibility.",
                    "merged", new DateTimeOffset(2026, 4, 2, 0, 0, 0, TimeSpan.Zero), "sha_pr7400",
                    "main", "fix/agent-composition", "frank",
                    new DateTimeOffset(2026, 3, 30, 0, 0, 0, TimeSpan.Zero),
                    "https://github.com/microsoft/semantic-kernel/pull/7400")
            ], TestContext.Current.CancellationToken);

            await seed.CommitAsync(TestContext.Current.CancellationToken);
        }

        await using var scope = await factory.OpenAsync(tenantId, TestContext.Current.CancellationToken);

        var candidates = await _queries.FindRegressionCandidatesAsync(
            scope, "planner", null, 20, TestContext.Current.CancellationToken);

        var candidate = Assert.Single(candidates, c => c.Number == 11222);

        Assert.Equal(issueCreatedAt, candidate.CreatedAt);
        Assert.Equal(7400, candidate.FixedByPullRequest);
    }
}
