using System.Diagnostics;
using System.Globalization;
using System.Text;
using System.Text.RegularExpressions;
using Dapper;
using Microsoft.Extensions.Logging;
using ReleaseLens.Embedding;
using ReleaseLens.Llm.Providers;
using ReleaseLens.Llm.Tools;
using ReleaseLens.Storage;
using ReleaseLens.Storage.Retrieval;

namespace ReleaseLens.Llm.Agent;

public sealed partial class QueryAgent(
    IChatProvider provider,
    ToolRegistry tools,
    HybridRetriever retriever,
    IEmbedder embedder,
    AgentOptions options,
    ILogger<QueryAgent> logger)
{
    public static readonly ActivitySource ActivitySource = new("ReleaseLens.Agent");

    [GeneratedRegex(@"\[E(\d+)\]")]
    private static partial Regex CitationMarker();

    public async Task<AgentAnswer> AnswerAsync(
        TenantScope scope, string question, int k, CancellationToken cancellationToken)
    {
        using var activity = ActivitySource.StartActivity("agent.answer");
        activity?.SetTag("tenant_id", scope.TenantId);
        activity?.SetTag("retrieval_k", k);

        // Seed with a first-pass retrieval so the model starts grounded rather than
        // spending an iteration discovering what the repository even contains.
        var queryVector = await embedder.EmbedQueryAsync(question, cancellationToken);
        var seed = await retriever.RetrieveAsync(scope, new RetrievalRequest(question, queryVector, k), cancellationToken);

        var citations = new List<EvidenceCitation>();
        var evidenceBlock = FormatEvidence(seed, citations);

        var messages = new List<ChatMessage>
        {
            ChatMessage.User($"""
                Question: {question}

                Evidence:
                {evidenceBlock}
                """)
        };

        // Built once, before the loop: the string must be byte-identical across iterations for
        // the provider's prompt cache to hit, and rebuilding it per iteration invites drift.
        var systemPrompt = SystemPrompt.Build(await DescribeRepositoryAsync(scope, cancellationToken));

        var usage = TokenUsage.Zero;
        var toolsCalled = new List<string>();
        var iterations = 0;
        string? answerText = null;
        var providerName = provider.Name;
        var modelName = options.Model;

        try
        {
            while (iterations < options.MaxIterations)
            {
                iterations++;

                using var iterationActivity = ActivitySource.StartActivity("agent.iteration");
                iterationActivity?.SetTag("iteration", iterations);

                var response = await provider.CompleteAsync(new ChatRequest(
                    systemPrompt,
                    messages,
                    tools.Definitions,
                    options.Model,
                    options.MaxTokens), cancellationToken);

                usage += response.Usage;
                providerName = response.Provider;
                modelName = response.Model;

                iterationActivity?.SetTag("tokens_in", response.Usage.InputTokens);
                iterationActivity?.SetTag("tokens_out", response.Usage.OutputTokens);
                iterationActivity?.SetTag("cache_read_input_tokens", response.Usage.CacheReadInputTokens);
                iterationActivity?.SetTag("provider", response.Provider);
                iterationActivity?.SetTag("model", response.Model);

                if (response.ToolCalls.Count == 0)
                {
                    answerText = response.Text ?? string.Empty;
                    break;
                }

                messages.Add(ChatMessage.AssistantToolCalls(response.ToolCalls));

                var results = new List<ToolResult>(response.ToolCalls.Count);

                foreach (var call in response.ToolCalls)
                {
                    toolsCalled.Add(call.Name);

                    using var toolActivity = ActivitySource.StartActivity("agent.tool");
                    toolActivity?.SetTag("tool_name", call.Name);

                    var execution = await tools.ExecuteAsync(call.Name, scope, call.Arguments, cancellationToken);
                    toolActivity?.SetTag("tool_error", execution.IsError);

                    var appended = AppendCitations(execution.Citations, citations);

                    results.Add(new ToolResult(
                        call.Id,
                        appended.Count > 0
                            ? execution.Content + "\n\nEvidence markers for these results: " +
                              string.Join(", ", appended.Select(i => $"[E{i}]"))
                            : execution.Content,
                        execution.IsError));
                }

                messages.Add(ChatMessage.UserToolResults(results));

                // The ceiling was reached with the model still calling tools. Ask once for a
                // final answer rather than returning nothing.
                if (iterations == options.MaxIterations)
                {
                    messages.Add(ChatMessage.User(
                        "You have reached the tool-call limit. Answer the question now from the evidence you have, " +
                        "citing markers, and say explicitly what remains unresolved."));

                    var final = await provider.CompleteAsync(new ChatRequest(
                        systemPrompt,
                        messages, [], options.Model, options.MaxTokens), cancellationToken);

                    if (final.ToolCalls.Count > 0)
                    {
                        // Should be unreachable: this request offered no tools. A real provider
                        // given `tools: []` cannot emit a tool call, so seeing one here would mean
                        // either a provider bug or a scripted test not honouring the contract.
                        logger.LogWarning(
                            "Final no-tools call still returned {Count} tool call(s); ignoring them.",
                            final.ToolCalls.Count);
                    }

                    usage += final.Usage;
                    answerText = final.Text ?? string.Empty;
                }
            }
        }
        catch (AllProvidersUnavailableException unavailable)
        {
            logger.LogError(unavailable, "All providers unavailable; degrading to unsynthesised evidence");
            activity?.SetTag("degraded", true);

            return new AgentAnswer(
                BuildDegradedAnswer(question, evidenceBlock),
                citations,
                new AgentMetadata(
                    iterations, toolsCalled, usage,
                    // Providers can fail on iteration 2+, after earlier iterations already spent
                    // real, billable tokens. Hardcoding zero here discards that spend and its
                    // attribution — and the only test for this path fails on the first call, so
                    // usage is zero there and the loss is invisible.
                    ModelPricing.CostUsd(modelName, usage, DateOnly.FromDateTime(DateTime.UtcNow)),
                    usage.Total > 0 ? providerName : "none",
                    modelName,
                    Degraded: true,
                    DegradedReason: $"All providers unavailable: {string.Join(", ", unavailable.AttemptedProviders)}. " +
                                    "Returning retrieved evidence without synthesis.",
                    seed.Chunks.Count, k, seed.Truncated, seed.Note, []));
        }

        answerText ??= "No answer was produced.";

        var unresolved = FindUnresolvedMarkers(answerText, citations.Count);
        var cost = ModelPricing.CostUsd(modelName, usage, DateOnly.FromDateTime(DateTime.UtcNow));

        activity?.SetTag("tokens_in", usage.InputTokens);
        activity?.SetTag("tokens_out", usage.OutputTokens);
        activity?.SetTag("cost_usd", (double)cost);
        activity?.SetTag("iterations", iterations);

        return new AgentAnswer(answerText, citations, new AgentMetadata(
            iterations, toolsCalled, usage, cost, providerName, modelName,
            Degraded: false, DegradedReason: null,
            seed.Chunks.Count, k, seed.Truncated, seed.Note, unresolved));
    }

    /// <summary>
    /// Resolves "owner/repo" for the system prompt. The prompt names the repository the model
    /// is answering about, and a tenant GUID there tells it nothing — it would read as
    /// "evidence of the 3f2b1a4c-… repository", discarding the single most useful piece of
    /// context with nothing failing to signal it. `tenants` is deliberately outside RLS and
    /// the app role holds select on it, so this reads correctly inside the tenant scope.
    /// </summary>
    private static async Task<string> DescribeRepositoryAsync(TenantScope scope, CancellationToken cancellationToken)
    {
        var name = await scope.Connection.ExecuteScalarAsync<string>(new CommandDefinition(
            "select repo_owner || '/' || repo_name from tenants where tenant_id = @tenantId",
            new { tenantId = scope.TenantId }, scope.Transaction, cancellationToken: cancellationToken));

        return string.IsNullOrWhiteSpace(name) ? "the indexed" : name;
    }

    private static string FormatEvidence(RetrievalResult result, List<EvidenceCitation> citations)
    {
        if (result.Chunks.Count == 0)
        {
            return "(no evidence was retrieved for this question)";
        }

        var builder = new StringBuilder();

        if (result.Note is { } note)
        {
            builder.AppendLine(note).AppendLine();
        }

        foreach (var chunk in result.Chunks)
        {
            citations.Add(new EvidenceCitation(
                chunk.Type, chunk.EntityKey,
                SearchCommitsTool.FirstLine(chunk.Content),
                SearchCommitsTool.BuildUrl(chunk.Type, chunk.EntityKey)));

            // The chunk's own header carries a truncated identifier (a 7-char sha, say) for
            // readability in the embedding text. The full entity key is repeated here so the
            // model can cite it precisely rather than the truncated form baked into the chunk.
            builder.Append('[').Append('E').Append(citations.Count).Append("] (")
                   .Append(chunk.EntityKey).Append(") ")
                   .AppendLine(chunk.Content)
                   .AppendLine();
        }

        return builder.ToString();
    }

    /// <summary>
    /// Adds tool citations that are not already present and returns the marker numbers
    /// for the ones added, so the model can be told what to cite.
    /// </summary>
    private static List<int> AppendCitations(
        IReadOnlyList<EvidenceCitation> incoming, List<EvidenceCitation> accumulated)
    {
        var markers = new List<int>();

        foreach (var citation in incoming)
        {
            var existing = accumulated.FindIndex(c => c.Type == citation.Type && c.EntityKey == citation.EntityKey);

            if (existing >= 0)
            {
                markers.Add(existing + 1);
                continue;
            }

            accumulated.Add(citation);
            markers.Add(accumulated.Count);
        }

        return markers;
    }

    private static IReadOnlyList<string> FindUnresolvedMarkers(string answer, int citationCount)
    {
        var unresolved = new List<string>();

        foreach (Match match in CitationMarker().Matches(answer))
        {
            var raw = match.Groups[1].Value;

            // A marker that will not parse is unresolved BY DEFINITION - an overflowing digit
            // run indexes nothing. Requiring a successful parse before the range check would
            // short-circuit and silently drop it, and this list is the groundedness signal the
            // eval service scores directly, so a false negative here is worse than a noisy one.
            if (!int.TryParse(raw, CultureInfo.InvariantCulture, out var index)
                || index < 1 || index > citationCount)
            {
                unresolved.Add($"E{raw}");
            }
        }

        return unresolved.Distinct().ToArray();
    }

    private static string BuildDegradedAnswer(string question, string evidenceBlock) => $"""
        No language-model provider was available, so this response is the retrieved evidence
        without synthesis. It is unranked prose from the index, not an answer.

        Question: {question}

        {evidenceBlock}
        """;
}
