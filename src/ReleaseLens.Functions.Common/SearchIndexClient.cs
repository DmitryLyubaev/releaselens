using System.Globalization;
using System.Net;
using System.Net.Http.Json;
using System.Text.Json.Nodes;

namespace ReleaseLens.Functions.Common;

/// <summary>
/// The REST calls this project makes to the AI Search index that project 2 built
/// (<c>eval/app/retrieval/search_index.py</c>): fields <c>chunk_id</c> (the key), <c>artefact</c>
/// (filterable), <c>content</c> and <c>vector</c>, and a semantic configuration named
/// <c>default</c>. The <see cref="HttpClient"/>'s base address is the search endpoint and its handler
/// is a <see cref="BearerTokenHandler"/> for <see cref="BearerTokenHandler.SearchScope"/>: no key.
/// </summary>
public sealed class SearchIndexClient(HttpClient http, string indexName)
{
    public const string ApiVersion = "2026-04-01";
    public const string SemanticConfiguration = "default";

    private const string Service = "Azure AI Search";

    // A request to /docs/index takes at most 1,000 documents and 16 MB; a 1,536-dimension vector is
    // about 15 KB as JSON, so 100 a request stays well inside both.
    private const int BatchSize = 100;

    // The service's own page is 50, so this reads up to 5,000 keys of one artefact: a hundred times
    // more chunks than any artefact has. Past it, something is wrong, and it fails rather than loops.
    private const int MaxPages = 100;

    /// <summary>Writes each chunk with <c>mergeOrUpload</c>: a chunk that exists is replaced, one that does not is added.</summary>
    public async Task UpsertAsync(IReadOnlyList<IndexChunk> chunks, CancellationToken cancellationToken)
    {
        foreach (var batch in chunks.Chunk(BatchSize))
        {
            await IndexAsync(
                batch.Select(chunk => new JsonObject
                {
                    ["@search.action"] = "mergeOrUpload",
                    ["chunk_id"] = chunk.ChunkId,
                    ["artefact"] = chunk.Artefact,
                    ["content"] = chunk.Content,
                    ["vector"] = Vector(chunk.Vector),
                }),
                cancellationToken);
        }
    }

    /// <summary>Deletes each key. A key the index does not hold is not an error.</summary>
    public async Task DeleteAsync(IReadOnlyList<string> keys, CancellationToken cancellationToken)
    {
        foreach (var batch in keys.Chunk(BatchSize))
        {
            await IndexAsync(
                batch.Select(key => new JsonObject { ["@search.action"] = "delete", ["chunk_id"] = key }),
                cancellationToken);
        }
    }

    /// <summary>
    /// Every key of every chunk whose <c>artefact</c> is <paramref name="artefact"/>, whatever scheme
    /// wrote it, reading every page.
    /// </summary>
    /// <remarks>
    /// No <c>top</c> is sent: the service adds a next-page link only when it cannot return the
    /// <c>top</c> asked for, so a <c>top</c> of 1,000 with 1,500 matches would return 1,000 and no
    /// link. Without it the service pages by its own size and always says where the next page is:
    /// <c>@odata.nextLink</c> (the same URL on every page of a POST search) and
    /// <c>@search.nextPageParameters</c> (whose <c>skip</c> is the position; the rest of the request
    /// is sent again as it was first written, not as the reply echoes it).
    /// </remarks>
    public async Task<IReadOnlyList<string>> KeysForArtefactAsync(string artefact, CancellationToken cancellationToken)
    {
        var body = new JsonObject
        {
            ["search"] = "*",
            ["filter"] = $"artefact eq '{artefact.Replace("'", "''", StringComparison.Ordinal)}'",
            ["select"] = "chunk_id",
        };
        var url = DocsUrl("search");
        var keys = new List<string>();
        var skip = 0;

        for (var page = 1; ; page++)
        {
            var reply = await SendAsync(url, body, cancellationToken);
            foreach (var document in reply["value"]?.AsArray() ?? [])
            {
                keys.Add((string)document!["chunk_id"]!);
            }

            // The last page has no link.
            if (reply["@odata.nextLink"]?.GetValue<string>() is not { } next)
            {
                return keys;
            }

            if (page >= MaxPages)
            {
                throw new InvalidOperationException(
                    $"{Service} still had more keys after {MaxPages.ToString(CultureInfo.InvariantCulture)} pages; stopped.");
            }

            url = new Uri(next, UriKind.Absolute);
            if (url.Scheme != http.BaseAddress!.Scheme || url.Authority != http.BaseAddress.Authority)
            {
                // The bearer token goes to wherever this client sends a request.
                throw new InvalidOperationException($"{Service} paged to a different host; not followed.");
            }

            // Only the skip is taken from the reply. The filter and the select stay the ones sent
            // first, whatever the service echoes: this list feeds a delete, and a next page that
            // lost the filter would hand back other artefacts' keys.
            var nextPage = reply["@search.nextPageParameters"]?.AsObject()
                ?? throw new InvalidOperationException($"{Service} gave a next page and no way to ask for it.");

            // The link is the same on every page, so progress is the skip: it must move forward, or
            // the same page would be read for ever.
            if (nextPage["skip"]?.GetValue<int>() is not { } nextSkip || nextSkip <= skip)
            {
                throw new InvalidOperationException($"{Service} paged without moving forward; stopped.");
            }

            skip = nextSkip;
            body["skip"] = skip;
        }
    }

    /// <summary>
    /// Keyword plus vector search for the top <paramref name="top"/> chunks, reranked by the semantic
    /// ranker, best first. Each hit's score is its reranker score. A reply the ranker did not rank is
    /// an error rather than a result: its order is the hybrid fusion's and its scores are a different
    /// scale.
    /// </summary>
    public async Task<IReadOnlyList<SearchHit>> HybridSemanticAsync(
        string query, float[] vector, int top, CancellationToken cancellationToken)
    {
        var body = new JsonObject
        {
            ["search"] = query,
            ["vectorQueries"] = new JsonArray(new JsonObject
            {
                ["kind"] = "vector",
                ["vector"] = Vector(vector),
                ["fields"] = "vector",
                ["k"] = top,
            }),
            ["top"] = top,
            ["select"] = "chunk_id,artefact,content",
            ["queryType"] = "semantic",
            ["semanticConfiguration"] = SemanticConfiguration,
        };

        var reply = await SendAsync(DocsUrl("search"), body, cancellationToken);

        var documents = reply["value"]?.AsArray() ?? [];
        var hits = new List<SearchHit>(documents.Count);
        foreach (var document in documents)
        {
            if (document!["@search.rerankerScore"]?.GetValue<double>() is not { } score)
            {
                var reason = reply["@search.semanticPartialResponseReason"]?.GetValue<string>() ?? "no reason given";
                throw new InvalidOperationException(
                    $"{Service} did not rerank this query ({reason}); a hit has no reranker score.");
            }

            hits.Add(new SearchHit(
                (string)document["chunk_id"]!, (string)document["artefact"]!, (string)document["content"]!, score));
        }

        return hits;
    }

    private static JsonArray Vector(float[] vector) => new([.. vector.Select(value => JsonValue.Create(value))]);

    private async Task IndexAsync(IEnumerable<JsonObject> documents, CancellationToken cancellationToken)
    {
        var body = new JsonObject { ["value"] = new JsonArray([.. documents]) };
        var reply = await SendAsync(DocsUrl("index"), body, cancellationToken);

        // A 207 is a success with some documents refused, each with its own status.
        var refused = (reply["value"]?.AsArray() ?? [])
            .Where(item => item!["status"]?.GetValue<bool>() == false)
            .ToList();
        if (refused.Count > 0)
        {
            var first = refused[0]!;
            throw new HttpRequestException(
                $"{Service} refused {refused.Count.ToString(CultureInfo.InvariantCulture)} documents, " +
                $"the first {(string?)first["key"]} with HTTP {(int?)first["statusCode"]}.",
                inner: null,
                statusCode: (HttpStatusCode?)(int?)first["statusCode"]);
        }
    }

    private Uri DocsUrl(string operation) => new(
        $"indexes/{Uri.EscapeDataString(indexName)}/docs/{operation}?api-version={ApiVersion}", UriKind.Relative);

    private async Task<JsonNode> SendAsync(Uri url, JsonNode body, CancellationToken cancellationToken)
    {
        using var response = await http.PostAsJsonAsync(url, body, cancellationToken);
        HttpFailure.ThrowIfNotSuccess(response, Service);

        return await response.Content.ReadFromJsonAsync<JsonNode>(cancellationToken)
            ?? throw new InvalidOperationException($"{Service} replied with no body.");
    }
}
