using System;
using System.Threading.Tasks;
using ReleaseLens.Core.Evidence;
using ReleaseLens.Storage.Repositories;
using Xunit;

namespace ReleaseLens.Storage.Tests;

[Collection(nameof(PostgresCollection))]
public class EvidenceRepositoryTests(PostgresFixture fixture)
{
    private readonly EvidenceRepository _repository = new();

    private async Task<(TenantConnectionFactory Factory, Guid TenantId)> ArrangeTenantAsync(string slug)
    {
        var factory = new TenantConnectionFactory(fixture.ConnectionString);
        var tenants = new TenantRepository(factory);
        var tenantId = await tenants.CreateAsync(
            new TenantDefinition(slug, $"Repo {slug}", "github", "microsoft", "semantic-kernel", 1_000_000),
            TestContext.Current.CancellationToken);
        return (factory, tenantId);
    }

    private static CommitEvidence Commit(Guid tenant, string sha, string message) => new(
        tenant, sha, message, "Alice", "alice@example.com",
        DateTimeOffset.UnixEpoch, DateTimeOffset.UnixEpoch,
        $"https://github.com/microsoft/semantic-kernel/commit/{sha}",
        [new FileChange("dotnet/src/Planner.cs", "modified", 5, 2)]);

    [Fact]
    public async Task UpsertCommits_InsertsCommitAndItsFiles()
    {
        var (factory, tenantId) = await ArrangeTenantAsync("upsert-commits");
        await using var scope = await factory.OpenAsync(tenantId, TestContext.Current.CancellationToken);

        var written = await _repository.UpsertCommitsAsync(
            scope, [Commit(tenantId, "sha0001", "fix: planner")], TestContext.Current.CancellationToken);
        await scope.CommitAsync(TestContext.Current.CancellationToken);

        Assert.Equal(1, written);

        await using var read = await factory.OpenAsync(tenantId, TestContext.Current.CancellationToken);
        var stored = await _repository.GetCommitAsync(read, "sha0001", TestContext.Current.CancellationToken);

        Assert.NotNull(stored);
        Assert.Equal("fix: planner", stored.Message);
        Assert.Single(stored.Files);
        Assert.Equal("dotnet/src/Planner.cs", stored.Files[0].Path);
    }

    [Fact]
    public async Task UpsertCommits_IsIdempotentAndUpdatesTheMessage()
    {
        var (factory, tenantId) = await ArrangeTenantAsync("upsert-idempotent");

        await using (var scope = await factory.OpenAsync(tenantId, TestContext.Current.CancellationToken))
        {
            await _repository.UpsertCommitsAsync(scope, [Commit(tenantId, "sha0002", "original")], TestContext.Current.CancellationToken);
            await scope.CommitAsync(TestContext.Current.CancellationToken);
        }

        await using (var scope = await factory.OpenAsync(tenantId, TestContext.Current.CancellationToken))
        {
            await _repository.UpsertCommitsAsync(scope, [Commit(tenantId, "sha0002", "amended")], TestContext.Current.CancellationToken);
            await scope.CommitAsync(TestContext.Current.CancellationToken);
        }

        await using var read = await factory.OpenAsync(tenantId, TestContext.Current.CancellationToken);
        var stored = await _repository.GetCommitAsync(read, "sha0002", TestContext.Current.CancellationToken);

        Assert.Equal("amended", stored!.Message);
    }

    [Fact]
    public async Task UpsertIssues_RoundTripsLabelsArray()
    {
        var (factory, tenantId) = await ArrangeTenantAsync("upsert-issues");
        var issue = new IssueEvidence(
            tenantId, 4211, "Planner drops tool results", "Repro steps", "closed",
            ["bug", "planner", "regression"], "bob",
            DateTimeOffset.UnixEpoch, DateTimeOffset.UnixEpoch,
            "https://github.com/microsoft/semantic-kernel/issues/4211");

        await using (var scope = await factory.OpenAsync(tenantId, TestContext.Current.CancellationToken))
        {
            await _repository.UpsertIssuesAsync(scope, [issue], TestContext.Current.CancellationToken);
            await scope.CommitAsync(TestContext.Current.CancellationToken);
        }

        await using var read = await factory.OpenAsync(tenantId, TestContext.Current.CancellationToken);
        var stored = await _repository.GetIssueAsync(read, 4211, TestContext.Current.CancellationToken);

        Assert.Equal(["bug", "planner", "regression"], stored!.Labels);
    }

    [Fact]
    public async Task GetIssue_ReturnsNullWhenAbsent()
    {
        var (factory, tenantId) = await ArrangeTenantAsync("issue-absent");
        await using var scope = await factory.OpenAsync(tenantId, TestContext.Current.CancellationToken);

        Assert.Null(await _repository.GetIssueAsync(scope, 999999, TestContext.Current.CancellationToken));
    }

    [Fact]
    public async Task UpsertReleases_StoresTagAndPublishedDate()
    {
        var (factory, tenantId) = await ArrangeTenantAsync("upsert-releases");
        var release = new ReleaseEvidence(
            tenantId, "dotnet-1.30.0", "1.30.0", "Release notes",
            new DateTimeOffset(2026, 3, 1, 0, 0, 0, TimeSpan.Zero), "main",
            "https://github.com/microsoft/semantic-kernel/releases/tag/dotnet-1.30.0");

        await using (var scope = await factory.OpenAsync(tenantId, TestContext.Current.CancellationToken))
        {
            await _repository.UpsertReleasesAsync(scope, [release], TestContext.Current.CancellationToken);
            await scope.CommitAsync(TestContext.Current.CancellationToken);
        }

        await using var read = await factory.OpenAsync(tenantId, TestContext.Current.CancellationToken);
        var stored = await _repository.GetReleaseAsync(read, "dotnet-1.30.0", TestContext.Current.CancellationToken);

        Assert.Equal(new DateTimeOffset(2026, 3, 1, 0, 0, 0, TimeSpan.Zero), stored!.PublishedAt);
    }

    [Fact]
    public async Task TenantRepository_FindBySlug_ReturnsTheTenant()
    {
        var (factory, tenantId) = await ArrangeTenantAsync("find-by-slug");
        var tenants = new TenantRepository(factory);

        var found = await tenants.FindBySlugAsync("find-by-slug", TestContext.Current.CancellationToken);

        Assert.Equal(tenantId, found!.TenantId);
        Assert.Equal("semantic-kernel", found.RepoName);
    }
}
