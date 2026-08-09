using Microsoft.Extensions.Configuration;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Hosting;
using Microsoft.Extensions.Logging;
using ReleaseLens.Core.Chunking;
using ReleaseLens.Core.Evidence;
using ReleaseLens.Embedding;
using ReleaseLens.Ingestion;
using ReleaseLens.Ingestion.GitHub;
using ReleaseLens.Storage;
using ReleaseLens.Storage.Repositories;

var builder = Host.CreateApplicationBuilder(args);
builder.Configuration.AddEnvironmentVariables("RELEASELENS_");

var connectionString = Environment.GetEnvironmentVariable("RELEASELENS_DB")
    ?? throw new InvalidOperationException(
        "RELEASELENS_DB is not set. Copy .env.example to .env and load it, or set the variable directly.");

var gitHubOptions = builder.Configuration.GetSection(GitHubOptions.SectionName).Get<GitHubOptions>()
    ?? new GitHubOptions();
gitHubOptions.Token = Environment.GetEnvironmentVariable("GITHUB_TOKEN") ?? string.Empty;

var embedderOptions = builder.Configuration.GetSection(EmbedderOptions.SectionName).Get<EmbedderOptions>()
    ?? new EmbedderOptions();

builder.Services.AddSingleton(gitHubOptions);
builder.Services.AddSingleton(embedderOptions);
builder.Services.AddSingleton(new TenantConnectionFactory(connectionString));
builder.Services.AddSingleton(new EvidenceChunker(ChunkOptions.Default));
builder.Services.AddSingleton<EvidenceRepository>();
builder.Services.AddSingleton<ChunkRepository>();
builder.Services.AddSingleton<CheckpointRepository>();
builder.Services.AddSingleton<TenantRepository>();
builder.Services.AddSingleton<IEmbedder>(sp => new OnnxEmbedder(sp.GetRequiredService<EmbedderOptions>()));
builder.Services.AddSingleton(sp => new RateLimitGate(
    sp.GetRequiredService<GitHubOptions>().RequestsPerHour, TimeProvider.System));

builder.Services.AddHttpClient<IEvidenceSource, GitHubEvidenceSource>(client =>
    client.BaseAddress = new Uri("https://api.github.com/"));

builder.Services.AddSingleton<IngestionPipeline>();

using var host = builder.Build();
var logger = host.Services.GetRequiredService<ILogger<Program>>();
var cancellation = new CancellationTokenSource();
Console.CancelKeyPress += (_, e) =>
{
    e.Cancel = true;
    logger.LogWarning("Cancellation requested — finishing the current batch, then stopping");
    cancellation.Cancel();
};

var command = args.FirstOrDefault() ?? "ingest";

switch (command)
{
    case "migrate":
    {
        var applied = await SchemaMigrator.MigrateAsync(connectionString, cancellation.Token);
        logger.LogInformation("Applied {Applied} migration(s)", applied);
        break;
    }

    case "create-tenant":
    {
        await SchemaMigrator.MigrateAsync(connectionString, cancellation.Token);

        var tenantSection = builder.Configuration.GetSection("Tenant");
        var tenants = host.Services.GetRequiredService<TenantRepository>();

        var tenantId = await tenants.CreateAsync(new TenantDefinition(
            tenantSection["Slug"] ?? "semantic-kernel",
            tenantSection["DisplayName"] ?? "microsoft/semantic-kernel",
            "github",
            gitHubOptions.Owner,
            gitHubOptions.Repository,
            long.Parse(tenantSection["DailyTokenBudget"] ?? "2000000")), cancellation.Token);

        logger.LogInformation("Tenant '{Slug}' is {TenantId}", tenantSection["Slug"], tenantId);
        break;
    }

    case "ingest":
    {
        if (string.IsNullOrWhiteSpace(gitHubOptions.Token))
        {
            throw new InvalidOperationException(
                "GITHUB_TOKEN is not set. Unauthenticated requests are limited to 60/hour and this ingest needs thousands.");
        }

        await SchemaMigrator.MigrateAsync(connectionString, cancellation.Token);

        var tenantSection = builder.Configuration.GetSection("Tenant");
        var tenants = host.Services.GetRequiredService<TenantRepository>();

        var tenantId = await tenants.CreateAsync(new TenantDefinition(
            tenantSection["Slug"] ?? "semantic-kernel",
            tenantSection["DisplayName"] ?? "microsoft/semantic-kernel",
            "github",
            gitHubOptions.Owner,
            gitHubOptions.Repository,
            long.Parse(tenantSection["DailyTokenBudget"] ?? "2000000")), cancellation.Token);

        logger.LogInformation("Ingesting {Owner}/{Repo} into tenant {TenantId}",
            gitHubOptions.Owner, gitHubOptions.Repository, tenantId);

        var report = await host.Services.GetRequiredService<IngestionPipeline>()
            .RunAsync(tenantId, cancellation.Token);

        logger.LogInformation("{Report}", report);
        break;
    }

    case "issue-key":
    {
        var slug = args.ElementAtOrDefault(1)
            ?? builder.Configuration["Tenant:Slug"]
            ?? "semantic-kernel";

        var tenants = host.Services.GetRequiredService<TenantRepository>();
        var tenant = await tenants.FindBySlugAsync(slug, cancellation.Token)
            ?? throw new InvalidOperationException($"No tenant with slug '{slug}'. Run 'ingest' first.");

        var factory = host.Services.GetRequiredService<TenantConnectionFactory>();
        var key = await new ApiKeyRepository(factory).CreateKeyAsync(
            tenant.TenantId, args.ElementAtOrDefault(2) ?? "local", cancellation.Token);

        // Printed once. Only the SHA-256 hash is stored, so this cannot be recovered later.
        Console.WriteLine(key);
        break;
    }

    default:
        logger.LogError("Unknown command '{Command}'. Use: migrate | create-tenant | ingest | issue-key", command);
        return 1;
}

return 0;
