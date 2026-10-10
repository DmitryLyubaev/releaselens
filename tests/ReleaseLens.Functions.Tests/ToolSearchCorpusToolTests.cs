using System.Net;
using System.Text.Json.Nodes;
using Microsoft.Extensions.Logging;
using ReleaseLens.Functions.Tool;

namespace ReleaseLens.Functions.Tests;

public class ToolSearchCorpusToolTests
{
    private static CancellationToken Ct => TestContext.Current.CancellationToken;

    private const string Query = "my-private-query-text";

    /// <summary>Keeps everything a log call carries: the rendered message, each structured value, and the exception.</summary>
    private sealed class CapturingLogger : ILogger<SearchCorpusTool>
    {
        public List<string> Entries { get; } = [];

        public IDisposable? BeginScope<TState>(TState state) where TState : notnull => null;

        public bool IsEnabled(LogLevel logLevel) => true;

        public void Log<TState>(
            LogLevel logLevel, EventId eventId, TState state, Exception? exception, Func<TState, Exception?, string> formatter)
        {
            Entries.Add(formatter(state, exception));
            if (state is IEnumerable<KeyValuePair<string, object?>> values)
            {
                Entries.AddRange(values.Select(pair => $"{pair.Key}={pair.Value}"));
            }

            if (exception is not null)
            {
                Entries.Add(exception.ToString());
            }
        }
    }

    private static SearchCorpusTool Tool(StubHttpHandler stub, CapturingLogger? log = null) =>
        new(ToolFixtures.Search(stub), log ?? new CapturingLogger());

    [Fact]
    public async Task Tool_ReturnsTheHitsAsJson_WithTheFourNamedProperties()
    {
        var stub = ToolFixtures.StubFor(("issue:42", "the importer stops"), ("release:v1", "notes"));

        var json = await Tool(stub).SearchCorpus(null!, "importer", null, Ct);

        var hits = JsonNode.Parse(json)!.AsArray();
        Assert.Equal(2, hits.Count);
        var first = hits[0]!.AsObject();
        Assert.Equal(["artefact", "type", "excerpt", "score"], first.Select(pair => pair.Key));
        Assert.Equal("issue:42", (string?)first["artefact"]);
        Assert.Equal("issue", (string?)first["type"]);
        Assert.Equal("the importer stops", (string?)first["excerpt"]);
        Assert.Equal(3.0, (double?)first["score"]);
    }

    [Fact]
    public async Task Tool_NoHits_IsAnEmptyList()
    {
        var stub = ToolFixtures.StubFor();

        Assert.Equal("[]", await Tool(stub).SearchCorpus(null!, "importer", null, Ct));
    }

    [Fact]
    public async Task Tool_AnEmptyQuery_IsTheFixedInputError()
    {
        var error = await Assert.ThrowsAsync<ToolInputException>(
            () => Tool(new StubHttpHandler()).SearchCorpus(null!, "  ", null, Ct));

        Assert.Equal("query must not be empty", error.Message);
    }

    [Theory]
    [InlineData(HttpStatusCode.InternalServerError, """{"error":{"message":"backend-detail index releaselens-chunks"}}""")]
    [InlineData(HttpStatusCode.TooManyRequests, "backend-detail throttled")]
    public async Task Tool_ASearchFailure_IsTheFixedToolError_WithNoBackendText(HttpStatusCode status, string body)
    {
        var log = new CapturingLogger();
        var stub = new StubHttpHandler().EnqueueJson(ToolFixtures.EmbeddingsReply()).Enqueue(status, body);

        var error = await Assert.ThrowsAnyAsync<Exception>(() => Tool(stub, log).SearchCorpus(null!, Query, null, Ct));

        Assert.Equal("search failed", error.Message);
        Assert.Null(error.InnerException);
        Assert.DoesNotContain("backend-detail", error.ToString());
        // The log says what failed, never what was asked.
        Assert.NotEmpty(log.Entries);
        Assert.All(log.Entries, entry => Assert.DoesNotContain(Query, entry));
    }

    [Fact]
    public async Task Tool_AnEmbeddingFailure_IsTheFixedToolError_WithNoBackendText()
    {
        var stub = new StubHttpHandler().Enqueue(HttpStatusCode.BadGateway, "backend-detail embeddings down");

        var error = await Assert.ThrowsAnyAsync<Exception>(() => Tool(stub).SearchCorpus(null!, Query, null, Ct));

        Assert.Equal("search failed", error.Message);
        Assert.DoesNotContain("backend-detail", error.ToString());
    }

    [Fact]
    public async Task Tool_ACancellation_IsNotTurnedIntoASearchFailure()
    {
        using var cancelled = new CancellationTokenSource();
        await cancelled.CancelAsync();
        var stub = new StubHttpHandler().EnqueueJson(ToolFixtures.EmbeddingsReply());

        await Assert.ThrowsAnyAsync<OperationCanceledException>(
            () => Tool(stub).SearchCorpus(null!, Query, null, cancelled.Token));
    }

    [Fact]
    public async Task Tool_ASuccess_LogsNoQueryText()
    {
        var log = new CapturingLogger();
        var stub = ToolFixtures.StubFor(("issue:1", "text"));

        await Tool(stub, log).SearchCorpus(null!, Query, null, Ct);

        Assert.All(log.Entries, entry => Assert.DoesNotContain(Query, entry));
    }
}
