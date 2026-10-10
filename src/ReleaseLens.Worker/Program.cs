using System.Diagnostics;
using System.Text;
using System.Text.Json;
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
using ReleaseLens.Functions.Common;
using ReleaseLens.Ingestion;
using ReleaseLens.Ingestion.GitHub;
using ReleaseLens.Llm.Providers;
using ReleaseLens.Storage;
using ReleaseLens.Storage.Repositories;
using ReleaseLens.Storage.Retrieval;

// Dispatched before RELEASELENS_DB is read, so the deploy smoke test can price a call on a
// runner with no database: pricing needs nothing beyond ModelPricing's own rates.
if (args.FirstOrDefault() == "price")
{
    return PriceCommand.Execute(args.Skip(1).ToList(), Console.Out, Console.Error);
}

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

    // Ingestion prefers a stored cursor over GitHub:SinceUtc, which is what makes a daily run
    // cheap and resumable — and also what makes widening the window a silent no-op: change
    // SinceUtc from 2026-06-01 to 2024-01-01, re-run, and the cursors still point at August
    // 2026, so a handful of items arrive and the run looks like a success. This is the
    // supported way to backfill; the alternative was hand-written SQL against
    // ingest_checkpoints.
    case "reset-checkpoints":
    {
        // Positional, like `issue-key`: slug first, then entity types. Naming a type
        // therefore means naming the tenant too. That is deliberate — the alternative of
        // guessing which one args[1] is would reset the wrong tenant's checkpoints whenever
        // a slug collided with an entity type name, whereas this fails loudly instead.
        var slug = args.ElementAtOrDefault(1)
            ?? builder.Configuration["Tenant:Slug"]
            ?? "semantic-kernel";

        var byWireName = Enum.GetValues<EntityType>().ToDictionary(type => type.ToWireName());
        var requested = args.Skip(2).ToArray();

        var unknown = requested.Where(name => !byWireName.ContainsKey(name)).ToArray();
        if (unknown.Length > 0)
        {
            logger.LogError(
                "Unknown entity type(s): {Unknown}. Use any of: {Valid}. " +
                "Usage: reset-checkpoints [tenant-slug] [entity-type...]",
                string.Join(", ", unknown), string.Join(" | ", byWireName.Keys));
            return 1;
        }

        EntityType[] entityTypes = requested.Length == 0
            ? Enum.GetValues<EntityType>()
            : [.. requested.Select(name => byWireName[name])];

        var tenants = host.Services.GetRequiredService<TenantRepository>();
        var tenant = await tenants.FindBySlugAsync(slug, cancellation.Token)
            ?? throw new InvalidOperationException($"No tenant with slug '{slug}'. Run 'ingest' first.");

        var factory = host.Services.GetRequiredService<TenantConnectionFactory>();
        var checkpoints = host.Services.GetRequiredService<CheckpointRepository>();

        IReadOnlyList<CheckpointReset> reset;
        await using (var scope = await factory.OpenAsync(tenant.TenantId, cancellation.Token))
        {
            reset = await checkpoints.ResetAsync(scope, entityTypes, cancellation.Token);
            await scope.CommitAsync(cancellation.Token);
        }

        // Said out loud rather than left to be inferred: an operator reaching for a command
        // called "reset" reasonably fears it empties the corpus. It does not touch evidence.
        logger.LogInformation(
            "Reset {Count} checkpoint(s) for tenant '{Slug}' ({TenantId}). No evidence was deleted — " +
            "ingestion upserts, so the next run re-walks the window from GitHub:SinceUtc " +
            "({Since:yyyy-MM-dd}) and refreshes what it finds.",
            reset.Count, slug, tenant.TenantId, gitHubOptions.SinceUtc);

        foreach (var entry in reset)
        {
            logger.LogInformation(
                "  {EntityType}: discarded cursor {Cursor}, etag {ETag}",
                entry.EntityType.ToWireName(),
                entry.DiscardedCursor ?? "(none)",
                entry.DiscardedETag ?? "(none)");
        }

        foreach (var untouched in entityTypes.Except(reset.Select(entry => entry.EntityType)))
        {
            logger.LogInformation("  {EntityType}: no checkpoint stored, nothing to reset", untouched.ToWireName());
        }

        break;
    }

    // The retrieval benchmark's corpus, for the Python side to embed and upload (plan 2).
    case "export-corpus":
    {
        if (args.ElementAtOrDefault(1) is not { } directory)
        {
            logger.LogError("Usage: export-corpus <dir>");
            return 1;
        }

        var tenantId = await ResolveTenantAsync();
        Directory.CreateDirectory(directory);

        var exporter = new CorpusExporter();
        var factory = host.Services.GetRequiredService<TenantConnectionFactory>();

        var chunkCount = 0;
        int linkCount;
        await using (var scope = await factory.OpenAsync(tenantId, cancellation.Token))
        {
            await using (var chunks = OpenJsonLines(Path.Combine(directory, "chunks.jsonl")))
            {
                await foreach (var chunk in exporter.ExportChunksAsync(scope, cancellation.Token))
                {
                    await chunks.WriteLineAsync(JsonSerializer.Serialize(chunk, BenchmarkRunner.Json));
                    chunkCount++;
                }
            }

            // Only after the chunk stream has ended: it holds the scope's connection until then.
            var links = await exporter.ExportLinksAsync(scope, cancellation.Token);
            await using (var linksFile = OpenJsonLines(Path.Combine(directory, "links.jsonl")))
            {
                foreach (var link in links)
                {
                    await linksFile.WriteLineAsync(JsonSerializer.Serialize(link, BenchmarkRunner.Json));
                }
            }

            linkCount = links.Count;
        }

        logger.LogInformation(
            "Exported {Chunks} chunk(s) and {Links} pull request link(s) to {Directory}",
            chunkCount, linkCount, Path.GetFullPath(directory));
        break;
    }

    // Real evidence as the ingest Function's input (plan 5): one file per key, in the artefact format,
    // named by the key's base64url, ready to upload to the artefacts-in container.
    case "export-artefacts":
    {
        if (args.ElementAtOrDefault(1) is not { } directory || args.Length < 3)
        {
            logger.LogError(
                "Usage: export-artefacts <dir> <key>...  (a key is issue:<number>, pull_request:<number>, " +
                "commit:<sha> or release:<tag>)");
            return 1;
        }

        // Every key is checked before the database is touched or a file is written.
        var keys = new List<EvidenceKey>();
        foreach (var text in args.Skip(2))
        {
            try
            {
                var key = ArtefactKeys.Parse(text);
                if (!keys.Contains(key))
                {
                    keys.Add(key);
                }
            }
            catch (FormatException ex)
            {
                logger.LogError("{Error}", ex.Message);
                return 1;
            }
        }

        var tenantId = await ResolveTenantAsync();
        var evidence = host.Services.GetRequiredService<EvidenceRepository>();
        var factory = host.Services.GetRequiredService<TenantConnectionFactory>();

        var found = new List<IEvidenceRecord>();
        var missing = new List<EvidenceKey>();
        await using (var scope = await factory.OpenAsync(tenantId, cancellation.Token))
        {
            foreach (var key in keys)
            {
                IEvidenceRecord? record = key.Type switch
                {
                    EntityType.Commit => await evidence.GetCommitAsync(scope, key.Value, cancellation.Token),
                    EntityType.Issue => await evidence.GetIssueAsync(scope, int.Parse(key.Value), cancellation.Token),
                    EntityType.PullRequest => await evidence.GetPullRequestAsync(scope, int.Parse(key.Value), cancellation.Token),
                    EntityType.Release => await evidence.GetReleaseAsync(scope, key.Value, cancellation.Token),
                    _ => throw new UnreachableException($"The key '{key}' was parsed above.")
                };

                if (record is null)
                {
                    missing.Add(key);
                }
                else
                {
                    found.Add(record);
                }
            }
        }

        // The others are written even when some keys are missing.
        Directory.CreateDirectory(directory);
        var utf8 = new UTF8Encoding(encoderShouldEmitUTF8Identifier: false);
        var written = new List<(string FileName, string Json)>();
        foreach (var record in found)
        {
            var fileName = ArtefactKeys.FileName(record.Key);
            var json = ArtefactJson.Serialize(record);
            await File.WriteAllTextAsync(Path.Combine(directory, fileName), json, utf8, cancellation.Token);
            written.Add((fileName, json));
        }

        // manifest.json: each file's expected chunk count, counted from the file's own JSON parsed back the
        // way the ingest app parses it, for the harness's checks. Earlier entries in the directory are kept.
        var manifestPath = Path.Combine(directory, ArtefactChunks.ManifestFileName);
        var earlier = File.Exists(manifestPath) ? await File.ReadAllTextAsync(manifestPath, cancellation.Token) : null;
        await File.WriteAllTextAsync(manifestPath, ArtefactChunks.Manifest(earlier, written), utf8, cancellation.Token);

        logger.LogInformation(
            "Exported {Count} artefact(s) to {Directory}", found.Count, Path.GetFullPath(directory));

        if (missing.Count > 0)
        {
            logger.LogError(
                "Not found, and not exported: {Missing}", string.Join(", ", missing.Select(key => key.ToString())));
            return 1;
        }

        break;
    }

    // The benchmark's two in-app arms: S1 (hybrid) and E1 (bge-exact).
    case "retrieve":
    {
        if (args.Length < 4)
        {
            logger.LogError(
                "Usage: retrieve <{Hybrid}|{BgeExact}> <questions.jsonl> <out.jsonl>",
                BenchmarkRunner.HybridMode, BenchmarkRunner.BgeExactMode);
            return 1;
        }

        var (mode, questionsPath, outputPath) = (args[1], args[2], args[3]);
        try
        {
            // Before the database or the output file is touched.
            BenchmarkRunner.ArmFor(mode);
        }
        catch (ArgumentException ex)
        {
            logger.LogError("{Error}", ex.Message);
            return 1;
        }

        var tenantId = await ResolveTenantAsync();
        var factory = host.Services.GetRequiredService<TenantConnectionFactory>();
        var embedder = host.Services.GetRequiredService<IEmbedder>();
        Func<CancellationToken, Task<TenantScope>> openScope = token => factory.OpenAsync(tenantId, token);

        // Each question opens its own scope (see PerQuestionScope), and the query is embedded
        // inside the delegate, so the runner's timing covers the embedding as well as the search.
        Func<string, CancellationToken, Task<IReadOnlyList<(long ChunkId, string Artefact, double Score)>>> search =
            mode switch
            {
                BenchmarkRunner.HybridMode => BenchmarkRunner.PerQuestionScope(openScope, async (scope, question, token) =>
                {
                    var vector = await embedder.EmbedQueryAsync(question, token);
                    var result = await new HybridRetriever().RetrieveAsync(
                        scope, new RetrievalRequest(question, vector, BenchmarkRunner.K), token);
                    return BenchmarkRunner.HybridHits(result);
                }),
                BenchmarkRunner.BgeExactMode => BenchmarkRunner.PerQuestionScope(openScope, async (scope, question, token) =>
                {
                    var vector = await embedder.EmbedQueryAsync(question, token);
                    return await new ExactVectorSearch().SearchAsync(scope, vector, BenchmarkRunner.K, token);
                }),
                _ => throw new UnreachableException($"The mode '{mode}' was checked above.")
            };

        (int Questions, int Errors) summary;
        using (var questions = new StreamReader(questionsPath))
        await using (var output = OpenJsonLines(outputPath))
        {
            summary = await BenchmarkRunner.RunRetrieveAsync(mode, questions, output, search, cancellation.Token);
        }

        logger.LogInformation(
            "Retrieved {Questions} question(s) with {Mode}, {Errors} of them with an error, into {Output}",
            summary.Questions, mode, summary.Errors, Path.GetFullPath(outputPath));
        break;
    }

    default:
        logger.LogError(
            "Unknown command '{Command}'. Use: migrate | create-tenant | ingest | issue-key | reset-checkpoints | " +
            "export-corpus | export-artefacts | retrieve | price",
            command);
        return 1;
}

return 0;

// The benchmark's tenant, resolved the way create-tenant names it.
async Task<Guid> ResolveTenantAsync()
{
    var slug = builder.Configuration["Tenant:Slug"] ?? "semantic-kernel";
    var tenant = await host.Services.GetRequiredService<TenantRepository>()
        .FindBySlugAsync(slug, cancellation.Token)
        ?? throw new InvalidOperationException($"No tenant with slug '{slug}'. Run 'ingest' first.");
    return tenant.TenantId;
}

// UTF-8 without a BOM and LF line ends, so the Python reader sees the same bytes on any OS.
static StreamWriter OpenJsonLines(string path) =>
    new(path, append: false, new UTF8Encoding(encoderShouldEmitUTF8Identifier: false)) { NewLine = "\n" };
