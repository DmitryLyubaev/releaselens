using System.Diagnostics;
using System.Text.Json;
using Azure.Core;
using Microsoft.AspNetCore.Diagnostics;
using OpenTelemetry.Metrics;
using OpenTelemetry.Resources;
using OpenTelemetry.Trace;
using ReleaseLens.Api;
using ReleaseLens.Api.Auth;
using ReleaseLens.Core.Evidence;
using ReleaseLens.Core.Telemetry;
using ReleaseLens.Embedding;
using ReleaseLens.Llm.Agent;
using ReleaseLens.Llm.Providers;
using ReleaseLens.Llm.Providers.Azure;
using ReleaseLens.Llm.Tools;
using ReleaseLens.Storage;
using ReleaseLens.Storage.Repositories;
using ReleaseLens.Storage.Retrieval;

var builder = WebApplication.CreateBuilder(args);

var connectionString = Environment.GetEnvironmentVariable("RELEASELENS_DB")
    ?? throw new InvalidOperationException("RELEASELENS_DB is not set.");

var anthropicOptions = builder.Configuration.GetSection(AnthropicOptions.SectionName).Get<AnthropicOptions>()
    ?? new AnthropicOptions();
anthropicOptions.ApiKey = Environment.GetEnvironmentVariable("ANTHROPIC_API_KEY") ?? string.Empty;

var openAiOptions = builder.Configuration.GetSection(OpenAiOptions.SectionName).Get<OpenAiOptions>()
    ?? new OpenAiOptions();
openAiOptions.ApiKey = Environment.GetEnvironmentVariable("OPENAI_API_KEY") ?? string.Empty;

var azureOpenAiOptions = builder.Configuration.GetSection(AzureOpenAiOptions.SectionName).Get<AzureOpenAiOptions>()
    ?? new AzureOpenAiOptions();
azureOpenAiOptions.ClientId = Environment.GetEnvironmentVariable("AZURE_CLIENT_ID");

// Binding into ChatOptions would add the configured names to its default list rather than
// replace it, so ["anthropic", "openai"] would come out with both names twice. The list is
// read on its own, and the default applies only when nothing configures it.
var chatOptions = new ChatOptions();
chatOptions.Providers = builder.Configuration
    .GetSection($"{ChatOptions.SectionName}:{nameof(ChatOptions.Providers)}")
    .Get<List<string>>() ?? chatOptions.Providers;

var agentOptions = builder.Configuration.GetSection(AgentOptions.SectionName).Get<AgentOptions>() ?? new AgentOptions();
var embedderOptions = builder.Configuration.GetSection(EmbedderOptions.SectionName).Get<EmbedderOptions>()
    ?? new EmbedderOptions();

builder.Services.AddSingleton(anthropicOptions);
builder.Services.AddSingleton(openAiOptions);
builder.Services.AddSingleton(azureOpenAiOptions);
builder.Services.AddSingleton(chatOptions);
builder.Services.AddSingleton(TimeProvider.System);
builder.Services.AddSingleton(agentOptions);
builder.Services.AddSingleton(embedderOptions);

builder.Services.AddSingleton(new TenantConnectionFactory(connectionString));
builder.Services.AddSingleton<TenantRepository>();
builder.Services.AddSingleton<ApiKeyRepository>();
builder.Services.AddSingleton<ApiKeyAuthenticator>();
builder.Services.AddSingleton<TokenUsageRepository>();
builder.Services.AddSingleton<EvidenceRepository>();
builder.Services.AddSingleton<EvidenceQueries>();
builder.Services.AddSingleton<HybridRetriever>();
builder.Services.AddSingleton<IEmbedder>(sp => new OnnxEmbedder(sp.GetRequiredService<EmbedderOptions>()));

builder.Services.AddHttpClient<AnthropicChatProvider>()
    .ConfigureHttpClient(c => c.Timeout = TimeSpan.FromMinutes(2));
builder.Services.AddHttpClient<OpenAiChatProvider>()
    .ConfigureHttpClient(c => c.Timeout = TimeSpan.FromMinutes(2));

// Nothing resolves the credential or the token cache except the Azure client's handler, and
// nothing resolves that client unless Chat:Providers lists azure-openai, so a chain without
// Azure never builds a credential.
builder.Services.AddSingleton<TokenCredential>(_ => AzureCredentialFactory.Create(azureOpenAiOptions));
builder.Services.AddSingleton(sp => new EntraTokenCache(
    sp.GetRequiredService<TokenCredential>(), azureOpenAiOptions.TokenScope, sp.GetRequiredService<TimeProvider>()));
builder.Services.AddHttpClient<AzureOpenAiChatProvider>()
    .ConfigureHttpClient(c => c.Timeout = TimeSpan.FromMinutes(2))
    .AddHttpMessageHandler(sp => new EntraTokenHandler(sp.GetRequiredService<EntraTokenCache>()));

builder.Services.AddSingleton(sp => new SelectedChatProviders(ChatProviderSelection.Select(
    chatOptions.Providers,
    new Dictionary<string, Func<IChatProvider>>
    {
        ["anthropic"] = () => sp.GetRequiredService<AnthropicChatProvider>(),
        ["openai"] = () => sp.GetRequiredService<OpenAiChatProvider>(),
        ["azure-openai"] = () =>
        {
            // Checked here rather than left to the provider: an empty BaseUrl would surface as
            // a UriFormatException that names no setting, and an empty Deployment only when
            // the first query reached Azure.
            var empty = new[]
                {
                    (Name: nameof(AzureOpenAiOptions.BaseUrl), Value: azureOpenAiOptions.BaseUrl),
                    (Name: nameof(AzureOpenAiOptions.Deployment), Value: azureOpenAiOptions.Deployment)
                }
                .Where(setting => string.IsNullOrWhiteSpace(setting.Value))
                .Select(setting => $"{AzureOpenAiOptions.SectionName}:{setting.Name}")
                .ToList();

            if (empty.Count > 0)
            {
                throw new InvalidOperationException(
                    $"Chat:Providers lists azure-openai, but these settings are empty: {string.Join(", ", empty)}.");
            }

            return sp.GetRequiredService<AzureOpenAiChatProvider>();
        }
    })));

builder.Services.AddSingleton<IChatProvider>(sp => new FallbackChatProvider(
    sp.GetRequiredService<SelectedChatProviders>().Providers,
    sp.GetRequiredService<ILogger<FallbackChatProvider>>()));

builder.Services.AddSingleton(sp => new ToolRegistry(
[
    new SearchCommitsTool(sp.GetRequiredService<HybridRetriever>(), sp.GetRequiredService<IEmbedder>()),
    new GetIssueTool(sp.GetRequiredService<EvidenceRepository>()),
    new DiffBetweenReleasesTool(sp.GetRequiredService<EvidenceQueries>()),
    new FindRegressionsTool(sp.GetRequiredService<EvidenceQueries>()),
    new CountEvidenceTool(sp.GetRequiredService<EvidenceQueries>()),
    new ListReleasesTool(sp.GetRequiredService<EvidenceQueries>())
],
sp.GetRequiredService<ILogger<ToolRegistry>>()));

builder.Services.AddSingleton<QueryAgent>();
builder.Services.AddOpenApi();

builder.Services.AddOpenTelemetry()
    .ConfigureResource(resource => resource.AddService("releaselens-api", serviceVersion: "1.0.0"))
    .WithTracing(tracing =>
    {
        tracing.AddAspNetCoreInstrumentation()
               .AddHttpClientInstrumentation();

        foreach (var source in ReleaseLensTelemetry.SourceNames)
        {
            tracing.AddSource(source);
        }

        // Reads OTEL_EXPORTER_OTLP_ENDPOINT. Locally that is the Aspire Dashboard on
        // http://localhost:4317; in Azure it is the Monitor OTLP ingestion endpoint.
        //
        // Skipped under the Testing environment. WebApplicationFactory boots the real host, so
        // an exporter with no collector to reach retries in the background and writes its
        // failures to the logger — which would make the API test output noisy, and the plan's
        // definition of done requires it pristine. The ActivitySources are still registered, so
        // anything asserting on spans still works; only the egress is off.
        if (!builder.Environment.IsEnvironment("Testing"))
        {
            tracing.AddOtlpExporter();
        }
    })
    .WithMetrics(metrics =>
    {
        metrics.AddAspNetCoreInstrumentation().AddHttpClientInstrumentation();

        if (!builder.Environment.IsEnvironment("Testing"))
        {
            metrics.AddOtlpExporter();
        }
    });

var app = builder.Build();

// Building the chain here rather than on the first query is what turns a misnamed provider, an
// incomplete AzureOpenAi section or a model with no rate into a failed startup.
ModelPricing.EnsurePriced(
    app.Services.GetRequiredService<SelectedChatProviders>().Providers
        .OfType<IPricedChatProvider>()
        .Select(provider => provider.Pricing),
    DateOnly.FromDateTime(app.Services.GetRequiredService<TimeProvider>().GetUtcNow().UtcDateTime));

app.MapOpenApi();
app.UseSwaggerUI(options => options.SwaggerEndpoint("/openapi/v1.json", "ReleaseLens"));
app.UseDefaultFiles();
app.UseStaticFiles();

// Last resort. Every designed failure has its own path — 401, 429, and the degraded 200 —
// so reaching this means something genuinely unforeseen happened. It must still leave the
// caller with JSON rather than a stack trace, and must not disclose internals.
app.UseExceptionHandler(errorApp => errorApp.Run(async context =>
{
    var feature = context.Features.Get<IExceptionHandlerFeature>();

    context.RequestServices.GetRequiredService<ILoggerFactory>()
        .CreateLogger("ReleaseLens.Api")
        .LogError(feature?.Error, "Unhandled exception serving {Path}", context.Request.Path);

    context.Response.StatusCode = StatusCodes.Status500InternalServerError;
    context.Response.ContentType = "application/json";

    await context.Response.WriteAsJsonAsync(new { error = "An unexpected error occurred." });
}));

app.MapGet("/health", () => Results.Ok(new { status = "ok" }));

app.MapGet("/evidence/tools", async (
    HttpContext context,
    ApiKeyAuthenticator authenticator,
    ToolRegistry registry,
    CancellationToken cancellationToken) =>
{
    // Authenticated even though the list is not tenant-specific: an unauthenticated schema
    // dump tells an attacker exactly what the surface accepts, and costs nothing to refuse.
    var tenantId = await authenticator.ResolveTenantAsync(context, cancellationToken);

    if (tenantId is null)
    {
        return Results.Unauthorized();
    }

    return Results.Ok(new EvidenceToolListResponse(
        [.. registry.Definitions.Select(d => new EvidenceToolDto(d.Name, d.Description, d.JsonSchema))]));
});

app.MapPost("/evidence/tools/{name}", async (
    string name,
    JsonElement arguments,
    HttpContext context,
    ApiKeyAuthenticator authenticator,
    TenantConnectionFactory connections,
    TenantRepository tenants,
    ToolRegistry registry,
    CancellationToken cancellationToken) =>
{
    var tenantId = await authenticator.ResolveTenantAsync(context, cancellationToken);

    if (tenantId is null)
    {
        return Results.Unauthorized();
    }

    // Tenant comes from the key, never from the body. There is deliberately no route,
    // query or body parameter through which a caller could name a different one.
    await using var scope = await connections.OpenAsync(tenantId.Value, cancellationToken);

    var result = await registry.ExecuteAsync(name, scope, arguments, cancellationToken);

    // A citation's Url is a repo-relative fragment (e.g. "releases/tag/java-1.0"); /query
    // resolves the same fragment against the tenant's repo, and an external consumer needs
    // the same absolute link rather than a path with nowhere implied to resolve it from.
    var tenant = await tenants.FindByIdAsync(tenantId.Value, cancellationToken);
    var repositoryBaseUrl = tenant is null
        ? string.Empty
        : $"https://github.com/{tenant.RepoOwner}/{tenant.RepoName}/";

    // No token budget check: these tools make no billed model call. search_commits embeds
    // the query locally through OnnxEmbedder, which costs nothing to meter. The daily budget
    // meters model spend, and metering a database read (or a local embedding) against it
    // would refuse work that costs nothing. Rate limiting, if it is ever needed here, is a
    // different control.
    return Results.Ok(new EvidenceToolResultResponse(
        result.Kind == ResultKind.Computed ? "computed" : "evidence",
        result.Content,
        result.IsError,
        [.. result.Citations.Select(c => new EvidenceCitationDto(
            c.Type.ToWireName(), c.EntityKey, c.Title, repositoryBaseUrl + c.Url))],
        [.. result.Excerpts.Select(e => new EvidenceExcerptDto(
            e.Type.ToWireName(), e.EntityKey, e.Text))],
        result.Bounds is null ? null
            : new EvidenceBoundsDto(result.Bounds.Returned, result.Bounds.Matched, result.Bounds.Truncated),
        result.Coverage is null ? null
            : new EvidenceCoverageDto(
                result.Coverage.Earliest?.ToString("O"),
                result.Coverage.Latest?.ToString("O"),
                result.Coverage.CompleteForWindow)));
});

app.MapPost("/query", async (
    QueryRequest request,
    HttpContext context,
    ApiKeyAuthenticator authenticator,
    TenantConnectionFactory connections,
    TenantRepository tenants,
    TokenUsageRepository usageRepository,
    QueryAgent agent,
    CancellationToken cancellationToken) =>
{
    if (string.IsNullOrWhiteSpace(request.Question))
    {
        return Results.BadRequest(new { error = "The 'question' field is required and must not be empty." });
    }

    var tenantId = await authenticator.ResolveTenantAsync(context, cancellationToken);

    if (tenantId is null)
    {
        return Results.Unauthorized();
    }

    var today = DateOnly.FromDateTime(DateTime.UtcNow);
    var k = Math.Clamp(request.K ?? agentOptions.SeedRetrievalK, 1, 50);
    var started = Stopwatch.GetTimestamp();

    await using var scope = await connections.OpenAsync(tenantId.Value, cancellationToken);

    // Budget is checked before the call, not after. Spec section 3.6: hard stop, HTTP 429.
    var tenant = await tenants.FindByIdAsync(tenantId.Value, cancellationToken);
    var usedToday = await usageRepository.GetTodayAsync(scope, today, cancellationToken);

    if (tenant is not null && usedToday.Total >= tenant.DailyTokenBudget)
    {
        return Results.Json(new
        {
            error = "Daily token budget exhausted for this tenant.",
            usedToday = usedToday.Total,
            dailyTokenBudget = tenant.DailyTokenBudget
        }, statusCode: StatusCodes.Status429TooManyRequests);
    }

    var answer = await agent.AnswerAsync(scope, request.Question, k, cancellationToken);

    await usageRepository.RecordAsync(scope, today,
        answer.Metadata.Usage.InputTokens, answer.Metadata.Usage.OutputTokens,
        answer.Metadata.CostUsd, cancellationToken);

    await scope.CommitAsync(cancellationToken);

    var repositoryBaseUrl = tenant is null
        ? string.Empty
        : $"https://github.com/{tenant.RepoOwner}/{tenant.RepoName}/";

    // Opt-in, and it changes nothing but this one field: the citation list itself - which
    // artefacts, which markers, in which order - is identical either way, so a consumer
    // measuring citation recall or precision reads the same numbers whether it asks for the
    // text or not.
    var includeEvidence = request.IncludeEvidence ?? false;

    return Results.Ok(new QueryResponse(
        answer.Answer,
        [.. answer.Citations.Select(c => new CitationDto(
            c.Marker, c.Citation.Type.ToWireName(), c.Citation.EntityKey, c.Citation.Title,
            repositoryBaseUrl + c.Citation.Url,
            includeEvidence ? c.Excerpts : null))],
        new QueryMetadataDto(
            answer.Metadata.Iterations,
            answer.Metadata.ToolsCalled,
            answer.Metadata.Usage.InputTokens,
            answer.Metadata.Usage.OutputTokens,
            answer.Metadata.Usage.CacheReadInputTokens,
            answer.Metadata.CostUsd,
            answer.Metadata.Provider,
            answer.Metadata.Model,
            answer.Metadata.Degraded,
            answer.Metadata.DegradedReason,
            answer.Metadata.RetrievedCount,
            answer.Metadata.RequestedK,
            answer.Metadata.RetrievalFewerThanRequested,
            answer.Metadata.RetrievalNote,
            answer.Metadata.AccumulatedCitationCount,
            answer.Metadata.UnresolvedCitationMarkers,
            (long)Stopwatch.GetElapsedTime(started).TotalMilliseconds,
            answer.Metadata.Providers ?? [],
            answer.Metadata.Filtered is { } filtered
                ? new FilteredOutcomeDto(filtered.Stage, filtered.Provider)
                : null)));
});

app.Run();

/// <summary>Exposed so WebApplicationFactory&lt;Program&gt; can host the app in tests.</summary>
public partial class Program;

/// <summary>
/// The providers Chat:Providers names, in order: the fallback chain, and the set startup
/// checks for a rate. One instance, so the chain that answers is the one that was checked.
/// </summary>
internal sealed record SelectedChatProviders(IReadOnlyList<IChatProvider> Providers);
