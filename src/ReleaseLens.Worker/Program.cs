using Microsoft.Extensions.Configuration;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Hosting;
using Microsoft.Extensions.Logging;
using OpenTelemetry.Metrics;
using OpenTelemetry.Resources;
using OpenTelemetry.Trace;
using ReleaseLens.Core.Chunking;
using ReleaseLens.Core.Evidence;
using ReleaseLens.Core.Telemetry;
using ReleaseLens.Embedding;
using ReleaseLens.Ingestion;
using ReleaseLens.Ingestion.GitHub;
using ReleaseLens.Storage;
using ReleaseLens.Storage.Repositories;

var builder = Host.CreateApplicationBuilder(new HostApplicationBuilderSettings
{
    Args = args,

    // Host.CreateApplicationBuilder defaults the content root to the process's current
    // directory, so `dotnet run --project src/ReleaseLens.Worker` from the repository root
    // never finds appsettings.json — and every Tenant:* and GitHub:* value silently falls
    // back to its C# default with nothing reported. That is invisible only while the defaults
    // happen to mirror the file; the moment someone edits SinceUtc or PageSize in
    // appsettings.json alone, the change would not apply and the ingest would quietly fetch a
    // different slice of history. appsettings.json is copied to the output directory, so
    // anchor the content root there instead of to wherever the process happens to be started.
    ContentRootPath = AppContext.BaseDirectory
});

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

builder.Services.AddOpenTelemetry()
    .ConfigureResource(resource => resource.AddService("releaselens-worker", serviceVersion: "1.0.0"))
    .WithTracing(tracing =>
    {
        tracing.AddHttpClientInstrumentation();

        foreach (var source in ReleaseLensTelemetry.SourceNames)
        {
            tracing.AddSource(source);
        }

        // Reads OTEL_EXPORTER_OTLP_ENDPOINT. Locally that is the Aspire Dashboard on
        // http://localhost:4317; in Azure it is the Monitor OTLP ingestion endpoint.
        //
        // Skipped under the Testing environment for the same reason as the API: no collector
        // to reach means an exporter that retries in the background and logs its failures. The
        // ActivitySources are still registered either way; only the egress is off.
        if (!builder.Environment.IsEnvironment("Testing"))
        {
            tracing.AddOtlpExporter();
        }
    })
    .WithMetrics(metrics =>
    {
        metrics.AddHttpClientInstrumentation();

        if (!builder.Environment.IsEnvironment("Testing"))
        {
            metrics.AddOtlpExporter();
        }
    });

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

        // Resolved once. Logging tenantSection["Slug"] directly reports (null) whenever the
        // value came from the default rather than configuration, which reads as a bug in the
        // command when it is only a bug in the log line.
        var slug = tenantSection["Slug"] ?? "semantic-kernel";

        var tenantId = await tenants.CreateAsync(new TenantDefinition(
            slug,
            tenantSection["DisplayName"] ?? "microsoft/semantic-kernel",
            "github",
            gitHubOptions.Owner,
            gitHubOptions.Repository,
            long.Parse(tenantSection["DailyTokenBudget"] ?? "2000000")), cancellation.Token);

        logger.LogInformation("Tenant '{Slug}' is {TenantId}", slug, tenantId);
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

        // Logged so the settings actually in effect are visible before a multi-hour run,
        // rather than assumed from a file that may not have been read.
        logger.LogInformation(
            "Ingesting {Owner}/{Repo} into tenant {TenantId} — since {Since:yyyy-MM-dd}, " +
            "commit files {FetchFiles}, page size {PageSize}, budget {RequestsPerHour}/hour",
            gitHubOptions.Owner, gitHubOptions.Repository, tenantId, gitHubOptions.SinceUtc,
            gitHubOptions.FetchCommitFiles, gitHubOptions.PageSize, gitHubOptions.RequestsPerHour);

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
