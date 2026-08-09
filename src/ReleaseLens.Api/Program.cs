using System.Diagnostics;
using ReleaseLens.Api;
using ReleaseLens.Core.Evidence;
using ReleaseLens.Embedding;
using ReleaseLens.Llm.Agent;
using ReleaseLens.Llm.Providers;
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

var agentOptions = builder.Configuration.GetSection(AgentOptions.SectionName).Get<AgentOptions>() ?? new AgentOptions();
var embedderOptions = builder.Configuration.GetSection(EmbedderOptions.SectionName).Get<EmbedderOptions>()
    ?? new EmbedderOptions();

builder.Services.AddSingleton(anthropicOptions);
builder.Services.AddSingleton(openAiOptions);
builder.Services.AddSingleton(agentOptions);
builder.Services.AddSingleton(embedderOptions);

builder.Services.AddSingleton(new TenantConnectionFactory(connectionString));
builder.Services.AddSingleton<TenantRepository>();
builder.Services.AddSingleton<ApiKeyRepository>();
builder.Services.AddSingleton<TokenUsageRepository>();
builder.Services.AddSingleton<EvidenceRepository>();
builder.Services.AddSingleton<EvidenceQueries>();
builder.Services.AddSingleton<HybridRetriever>();
builder.Services.AddSingleton<IEmbedder>(sp => new OnnxEmbedder(sp.GetRequiredService<EmbedderOptions>()));

builder.Services.AddHttpClient<AnthropicChatProvider>()
    .ConfigureHttpClient(c => c.Timeout = TimeSpan.FromMinutes(2));
builder.Services.AddHttpClient<OpenAiChatProvider>()
    .ConfigureHttpClient(c => c.Timeout = TimeSpan.FromMinutes(2));

builder.Services.AddSingleton<IChatProvider>(sp => new FallbackChatProvider(
    [sp.GetRequiredService<AnthropicChatProvider>(), sp.GetRequiredService<OpenAiChatProvider>()],
    sp.GetRequiredService<ILogger<FallbackChatProvider>>()));

builder.Services.AddSingleton(sp => new ToolRegistry(
[
    new SearchCommitsTool(sp.GetRequiredService<HybridRetriever>(), sp.GetRequiredService<IEmbedder>()),
    new GetIssueTool(sp.GetRequiredService<EvidenceRepository>()),
    new DiffBetweenReleasesTool(sp.GetRequiredService<EvidenceQueries>()),
    new FindRegressionsTool(sp.GetRequiredService<EvidenceQueries>())
]));

builder.Services.AddSingleton<QueryAgent>();
builder.Services.AddOpenApi();

var app = builder.Build();

app.MapOpenApi();
app.UseSwaggerUI(options => options.SwaggerEndpoint("/openapi/v1.json", "ReleaseLens"));
app.UseDefaultFiles();
app.UseStaticFiles();

app.MapGet("/health", () => Results.Ok(new { status = "ok" }));

app.MapPost("/query", async (
    QueryRequest request,
    HttpContext context,
    ApiKeyRepository apiKeys,
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

    var presentedKey = context.Request.Headers["X-Api-Key"].ToString();
    var tenantId = await apiKeys.ResolveTenantAsync(presentedKey, cancellationToken);

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

    return Results.Ok(new QueryResponse(
        answer.Answer,
        [.. answer.Citations.Select(c => new CitationDto(
            c.Type.ToWireName(), c.EntityKey, c.Title, repositoryBaseUrl + c.Url))],
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
            answer.Metadata.RetrievalTruncated,
            answer.Metadata.RetrievalNote,
            answer.Metadata.UnresolvedCitationMarkers,
            (long)Stopwatch.GetElapsedTime(started).TotalMilliseconds)));
});

app.Run();

/// <summary>Exposed so WebApplicationFactory&lt;Program&gt; can host the app in tests.</summary>
public partial class Program;
