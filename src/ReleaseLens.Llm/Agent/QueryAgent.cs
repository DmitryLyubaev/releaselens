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
        var ledger = new EvidenceLedger();
        var evidenceBlock = FormatEvidence(seed, citations, ledger);

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

        // The agent has no model of its own; until a provider answers there is none to report.
        var modelName = "none";

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

                    // After the citations, never before: an excerpt is filed under the marker
                    // its artefact already holds, and that marker only exists once the citation
                    // has been resolved.
                    RecordExcerpts(execution.Excerpts, citations, ledger);

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
                        messages, [], options.MaxTokens), cancellationToken);

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

            var degradedAnswer = BuildDegradedAnswer(question, evidenceBlock);

            return new AgentAnswer(
                degradedAnswer,
                // The same filter as the synthesised path, deliberately: the degraded answer is
                // the evidence block verbatim, and every entry in it is labelled "[E<n>]", so
                // every seed artefact resolves and the caller can attribute all of the prose
                // they were handed. What it drops is anything a tool returned before the
                // provider died - those artefacts appear nowhere in this response's text, so
                // shipping them would be the same "everything we looked at" bug in miniature.
                //
                // The evidence text rides along on this path too, and the API surfaces it
                // when asked exactly as it does on the synthesised path. It is redundant
                // here - the degraded answer IS the evidence block, so the caller already
                // holds the prose - but withholding it would hand any consumer that scores
                // groundedness an empty evidence list for a response that is nothing but
                // evidence, and score it zero for a harness reason.
                SelectCitedEvidence(degradedAnswer, citations, ledger),
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
                    seed.Chunks.Count, k, seed.FewerThanRequested, seed.Note, citations.Count, []));
        }

        answerText ??= "No answer was produced.";

        // Order matters and is load-bearing. Validation runs against the FULL accumulated pool,
        // because a marker is hallucinated only if it indexes nothing the agent ever saw.
        // Filtering first would shrink the pool to the cited subset and then report every marker
        // in it as in-range while reporting nothing else at all - or, worse under a different
        // ordering, brand every unused marker a hallucination.
        var unresolved = FindUnresolvedMarkers(answerText, citations.Count);
        var cited = SelectCitedEvidence(answerText, citations, ledger);
        var cost = ModelPricing.CostUsd(modelName, usage, DateOnly.FromDateTime(DateTime.UtcNow));

        activity?.SetTag("tokens_in", usage.InputTokens);
        activity?.SetTag("tokens_out", usage.OutputTokens);
        activity?.SetTag("cost_usd", (double)cost);
        activity?.SetTag("iterations", iterations);

        return new AgentAnswer(answerText, cited, new AgentMetadata(
            iterations, toolsCalled, usage, cost, providerName, modelName,
            Degraded: false, DegradedReason: null,
            seed.Chunks.Count, k, seed.FewerThanRequested, seed.Note, citations.Count, unresolved));
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

    private static string FormatEvidence(
        RetrievalResult result, List<EvidenceCitation> citations, EvidenceLedger ledger)
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
            // One entity split across several chunks gets ONE citation and therefore one
            // marker, shared by all of its chunks. Every chunk's text is still listed below;
            // only the citation list collapses.
            var marker = ResolveCitationMarker(new EvidenceCitation(
                chunk.Type, chunk.EntityKey,
                SearchCommitsTool.FirstLine(chunk.Content),
                SearchCommitsTool.BuildUrl(chunk.Type, chunk.EntityKey)), citations);

            // Every chunk, under the one marker its artefact shares. The citation list
            // collapses here and the evidence must not collapse with it - the model is shown
            // all of this text, so anything asked to check the model's claims has to be too.
            ledger.Record(marker, chunk.Content);

            // The chunk's own header carries a truncated identifier (a 7-char sha, say) for
            // readability in the embedding text. The full entity key is repeated here so the
            // model can cite it precisely rather than the truncated form baked into the chunk.
            builder.Append('[').Append('E').Append(marker).Append("] (")
                   .Append(chunk.EntityKey).Append(") ")
                   .AppendLine(chunk.Content)
                   .AppendLine();
        }

        return builder.ToString();
    }

    /// <summary>
    /// Returns the marker number of every incoming tool citation, adding the ones not
    /// already accumulated, so the model can be told what to cite.
    /// </summary>
    private static List<int> AppendCitations(
        IReadOnlyList<EvidenceCitation> incoming, List<EvidenceCitation> accumulated)
    {
        var markers = new List<int>(incoming.Count);

        foreach (var citation in incoming)
        {
            markers.Add(ResolveCitationMarker(citation, accumulated));
        }

        return markers;
    }

    /// <summary>
    /// Files each excerpt under the marker its artefact already holds.
    /// </summary>
    /// <remarks>
    /// Lookup only, deliberately never find-or-add. An excerpt is text ABOUT an artefact,
    /// not a new artefact; letting one append to the pool would inflate
    /// <c>AccumulatedCitationCount</c> and could make an otherwise out-of-range marker
    /// resolve, silently moving the groundedness signal the eval service scores — from a
    /// field whose entire job is to be inert. An excerpt whose artefact is not in the pool
    /// is dropped rather than added; in practice there are none, because tools list the
    /// citation for every artefact they excerpt.
    /// </remarks>
    private static void RecordExcerpts(
        IReadOnlyList<EvidenceExcerpt> excerpts, List<EvidenceCitation> accumulated, EvidenceLedger ledger)
    {
        foreach (var excerpt in excerpts)
        {
            var index = accumulated.FindIndex(
                c => c.Type == excerpt.Type && c.EntityKey == excerpt.EntityKey);

            if (index >= 0)
            {
                ledger.Record(index + 1, excerpt.Text);
            }
        }
    }

    /// <summary>
    /// Find-or-add on (Type, EntityKey), returning the 1-based marker number — the citation's
    /// position in <paramref name="accumulated"/>, which is what <c>[E&lt;n&gt;]</c> indexes and
    /// what <see cref="FindUnresolvedMarkers"/> range-checks.
    ///
    /// A citation identifies an artefact, not a chunk, so a second sighting of an artefact
    /// reuses the marker it already has. This is the ONLY place that rule is implemented:
    /// seed retrieval and tool results both come through here, because when they each had
    /// their own copy of it the seed path's copy was missing and the citation list grew one
    /// entry per chunk — inflating exactly the count the eval harness scores precision over.
    /// </summary>
    private static int ResolveCitationMarker(EvidenceCitation citation, List<EvidenceCitation> accumulated)
    {
        var existing = accumulated.FindIndex(c => c.Type == citation.Type && c.EntityKey == citation.EntityKey);

        if (existing >= 0)
        {
            return existing + 1;
        }

        accumulated.Add(citation);
        return accumulated.Count;
    }

    /// <summary>
    /// Narrows the accumulated evidence pool to the artefacts the answer actually cites, each
    /// paired with the marker the answer used. Without this the response carried every artefact
    /// any tool returned during the loop — "everything we looked at", not "what this rests on" —
    /// which one <c>list_releases</c> call can inflate to 300 entries.
    ///
    /// Three properties this must have, all of them exercised by tests:
    /// a marker outside <c>1..accumulated.Count</c> yields no citation rather than a phantom one;
    /// a marker repeated across several sentences yields one citation, not one per mention;
    /// and an answer with no markers at all — a decline — yields none.
    ///
    /// The result is ordered by marker ascending. That is the least surprising order for a
    /// consumer that has not yet read the marker field, but it is NOT a licence to keep
    /// inferring markers from position: <c>[E3]</c> alone lands at index 0.
    /// </summary>
    private static IReadOnlyList<CitedEvidence> SelectCitedEvidence(
        string answer, List<EvidenceCitation> accumulated, EvidenceLedger ledger)
    {
        // Keyed by marker, so a repeat collapses; sorted, so the order is deterministic
        // rather than a function of which sentence happened to mention what first.
        var cited = new SortedDictionary<int, EvidenceCitation>();

        foreach (Match match in CitationMarker().Matches(answer))
        {
            if (int.TryParse(match.Groups[1].Value, CultureInfo.InvariantCulture, out var marker)
                && marker >= 1 && marker <= accumulated.Count)
            {
                cited[marker] = accumulated[marker - 1];
            }
        }

        return [.. cited.Select(entry =>
            new CitedEvidence(entry.Key, entry.Value, ledger.For(entry.Key)))];
    }

    /// <summary>
    /// The evidence text behind each marker: every fragment the agent put in front of the
    /// model for that artefact, in the order it saw them.
    /// </summary>
    /// <remarks>
    /// <para>
    /// Nothing here is capped, and that is a decision rather than an oversight. The point of
    /// the text is that a reader can check a claim against it, and a cap would recreate the
    /// starvation this exists to fix one level down — a reader shown a cut fragment cannot
    /// tell "the evidence does not support this" from "the part that supported it was
    /// removed", and would score the first while looking at the second. What bounds it
    /// instead is construction: every fragment here was already in the prompt the agent sent,
    /// so the whole ledger is smaller than one model context, and the API only serialises it
    /// when a request asks for it. If a cap is ever genuinely needed it must announce itself
    /// in the payload, the way the list tools announce theirs.
    /// </para>
    /// <para>
    /// Exact duplicates are dropped. Seed retrieval and a later <c>search_commits</c> for a
    /// similar query routinely return the identical chunk, and repeating it costs whoever
    /// reads this real tokens while making one artefact look like it says a thing twice.
    /// Only exact matches: two overlapping-but-different chunks are two different fragments
    /// and both are kept, because guessing which one subsumes the other is how text goes
    /// missing.
    /// </para>
    /// </remarks>
    private sealed class EvidenceLedger
    {
        private readonly Dictionary<int, List<string>> _byMarker = [];

        public void Record(int marker, string? text)
        {
            if (string.IsNullOrWhiteSpace(text))
            {
                return;
            }

            if (!_byMarker.TryGetValue(marker, out var fragments))
            {
                fragments = [];
                _byMarker[marker] = fragments;
            }

            if (!fragments.Contains(text, StringComparer.Ordinal))
            {
                fragments.Add(text);
            }
        }

        public IReadOnlyList<string> For(int marker)
            => _byMarker.TryGetValue(marker, out var fragments) ? fragments : [];
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
