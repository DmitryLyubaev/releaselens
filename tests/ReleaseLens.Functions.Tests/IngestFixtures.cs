using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;
using ReleaseLens.Core.Chunking;
using ReleaseLens.Core.Evidence;
using ReleaseLens.Functions.Common;
using ReleaseLens.Functions.Ingest;

namespace ReleaseLens.Functions.Tests;

/// <summary>What the ingest tests share: a fixture artefact, the three services' replies, and the app wired over one stub.</summary>
internal static class IngestFixtures
{
    public static readonly Guid Tenant = Guid.Parse("00000000-0000-0000-0000-000000000001");
    public const string ArtefactId = "issue:42";
    public const string Title = "Crash when the importer meets an empty release";
    public const string BlobName = "issue-42.json";

    /// <summary>An issue whose body is long enough for several chunks under <see cref="ChunkOptions.Default"/>.</summary>
    public static IssueEvidence Issue(string title = Title, int sentences = 120) => new(
        Tenant, 42, title,
        string.Join(' ', Enumerable.Range(1, sentences).Select(i => $"Step {i} of the report says the importer stops after record {i * 7}.")),
        "open", ["bug"], "ada", new DateTimeOffset(2026, 10, 1, 9, 0, 0, TimeSpan.Zero), null, "https://example.com/issues/42");

    public static IReadOnlyList<Chunk> ChunksOf(IssueEvidence issue) =>
        new EvidenceChunker(ChunkOptions.Default).Chunk(issue);

    public static string EventFor(
        string blobName = BlobName,
        string eventType = ArtefactEvent.BlobCreated,
        string container = ArtefactEvent.Container) =>
        JsonSerializer.Serialize(new
        {
            id = "11111111-1111-1111-1111-111111111111",
            topic = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-example/providers/Microsoft.Storage/storageAccounts/example",
            subject = $"/blobServices/default/containers/{container}/blobs/{blobName}",
            eventType,
            eventTime = "2026-10-10T09:30:00.0000000Z",
            data = new { api = "PutBlob", url = $"https://example.blob.core.windows.net/{container}/{blobName}" },
            dataVersion = "",
            metadataVersion = "1",
        });

    /// <summary>One vector per chunk; each vector's first value is its position plus one, so a test can see which went where.</summary>
    public static string EmbeddingsReply(int count) =>
        JsonSerializer.Serialize(new
        {
            data = Enumerable.Range(0, count).Select(i =>
            {
                var vector = new float[EmbeddingsClient.Dimensions];
                vector[0] = i + 1;
                return new { index = i, embedding = vector };
            }),
        });

    public const string IndexReply = """{"value":[]}""";

    public static string KeysReply(params string[] keys) =>
        new JsonObject { ["value"] = new JsonArray([.. keys.Select(key => (JsonNode)new JsonObject { ["chunk_id"] = key })]) }
            .ToJsonString();

    /// <summary>The embeddings, the index and the keys lookup of one successful ingest, in the order they are called.</summary>
    public static StubHttpHandler StubFor(int chunks, params string[] existingKeys) =>
        new StubHttpHandler()
            .EnqueueJson(EmbeddingsReply(chunks))
            .EnqueueJson(IndexReply)
            .EnqueueJson(KeysReply(existingKeys));

    public static IngestArtefact Ingest(StubHttpHandler stub) =>
        new(
            new EmbeddingsClient(
                new HttpClient(stub) { BaseAddress = new Uri("https://example-account.openai.azure.com/openai/v1/") },
                "releaselens-embed-small"),
            new SearchIndexClient(
                new HttpClient(stub) { BaseAddress = new Uri("https://example-search.search.windows.net/") },
                "releaselens-chunks"));

    public static string Base64(string text) => Convert.ToBase64String(Encoding.UTF8.GetBytes(text));
}
